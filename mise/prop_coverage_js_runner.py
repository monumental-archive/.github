#!/usr/bin/env python3
"""coverage_js_runner.py's readers, on lockfiles and scripts nobody wrote.

The properties of `test_coverage_js_runner.py`'s fixed rows (#1548),
over generated lockfile entries and task run scripts:

- Invariance (V): `providers` names exactly the vitest coverage
  providers a lockfile's entries carry, in any of the four lockfile
  spellings and in any order, and nothing for a package whose name
  only begins like one.
- Invariance (V): a command split across backslash continuations is
  one logical line, the same as written on one line; blanks and
  comments are dropped.
- Invariance (V): `read_line` reads the arguments after vitest behind
  any launcher the leg knows, and refuses vitest behind any other.
- Refusal (R): any tasks `mise tasks info --json` can give (a run
  script, or a list of scripts and `{ task = … }` / `{ tasks = […] }`
  references) are walked by task_walk.py and answered by
  `--vitest-command` with 0 and the command or 1 and its refusal, or
  refused by the walk by name; never a traceback (#1555: a task
  reference raised AttributeError until this found it).

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "coverage_js_runner",
    Path(__file__).with_name("coverage_js_runner.py"),
)
cj = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cj)
# The walk the runner reads (#1608), loaded beside it by the runner.
tw = cj.task_walk

VERSION = st.from_regex(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True)
# One entry per lockfile dialect, for a package at a version.
DIALECTS = (
    "  {name}@{version}:",
    "  '{name}@{version}':",
    "{name}@^{version}:",
    '"{name}@npm:{version}":',
    '    "{name}@{version}",',
    '    "node_modules/{name}": {{',
)
PACKAGE = st.sampled_from([
    "@vitest/coverage-v8",
    "@vitest/coverage-istanbul",
    "@vitest/coverage-v8x",
    "vitest",
    "left-pad",
])
WORD = st.from_regex(r"[a-z][a-z0-9.=-]{0,8}", fullmatch=True)


@st.composite
def lockfile(draw: st.DrawFn) -> tuple[str, set[str]]:
    """Draw lockfile lines naming packages.

    Returns:
        The text and the providers it carries.

    """
    lines, expected = [], set()
    for _ in range(draw(st.integers(0, 6))):
        name = draw(PACKAGE)
        lines.append(
            draw(st.sampled_from(DIALECTS)).format(name=name, version=draw(VERSION))
        )
        if name in {"@vitest/coverage-v8", "@vitest/coverage-istanbul"}:
            expected.add(name.rsplit("-", 1)[-1])
    return "\n".join(lines) + "\n", expected


class Providers(unittest.TestCase):
    """Every dialect, every order."""

    @given(lockfile())
    @example(('"node_modules/@vitest/coverage-v8x": {\n', set()))
    def test_exactly_the_providers_named(self, drawn: tuple[str, set[str]]) -> None:
        """v8 and istanbul as the entries carry them, nothing else."""
        text, expected = drawn
        self.assertEqual(cj.providers(text), expected)


class LogicalLines(unittest.TestCase):
    """A continuation is one line."""

    @given(
        st.lists(st.lists(WORD, min_size=1, max_size=5), min_size=1, max_size=4),
        st.data(),
    )
    def test_continuations_join(
        self, commands: list[list[str]], data: st.DataObject
    ) -> None:
        """Split at any word boundary, read as written on one line."""
        script = []
        for words in commands:
            script.append(data.draw(st.sampled_from(["", "# a comment", "   "])))
            pieces = [words[0]]
            for word in words[1:]:
                if data.draw(st.booleans()):
                    pieces[-1] += " \\"
                    pieces.append("  " + word)
                else:
                    pieces[-1] += " " + word
            script.extend(pieces)
        self.assertEqual(
            [" ".join(line.split()) for line in cj.logical_lines("\n".join(script))],
            [" ".join(words) for words in commands],
        )


class ReadLine(unittest.TestCase):
    """Known launchers read; any other command refused."""

    @given(
        st.sampled_from(cj.LAUNCHERS),
        st.sampled_from(["vitest", "node_modules/.bin/vitest", "./x/vitest"]),
        st.lists(WORD, max_size=4),
    )
    def test_a_known_launcher_reads_its_arguments(
        self, launcher: tuple[str, ...], binary: str, args: list[str]
    ) -> None:
        """The arguments after vitest, verbatim."""
        line = " ".join([*launcher, binary, *args])
        self.assertEqual(cj.read_line(line), (args, None))

    @given(
        st.sampled_from(["sudo", "env", "time", "bunx", "nice"]),
        st.lists(WORD, max_size=3),
    )
    def test_an_unknown_launcher_is_refused(self, prefix: str, args: list[str]) -> None:
        """Refused by name, never read."""
        line = " ".join([prefix, "vitest", *args])
        got, reason = cj.read_line(line)
        self.assertIsNone(got)
        self.assertIn("does not read", reason)


SCRIPT = st.one_of(
    st.text(max_size=40),
    st.sampled_from(["vitest run", "pnpm exec vitest --run x", "npx vitest watch"]),
)
REFERENCE = st.one_of(
    st.fixed_dictionaries({"task": WORD}),
    st.fixed_dictionaries({"tasks": st.lists(WORD, max_size=2)}),
)
TASK = st.fixed_dictionaries(
    {"name": WORD},
    optional={
        "run": st.one_of(SCRIPT, st.lists(st.one_of(SCRIPT, REFERENCE), max_size=3)),
        "dir": st.sampled_from(["", ".", "web"]),
    },
)


class VitestCommand(unittest.TestCase):
    """Any task stream: the command, or the refusal."""

    @given(st.lists(TASK, max_size=3))
    @example([{"name": "test", "run": [{"task": "build"}, "vitest run"]}])
    def test_any_stream_is_answered(self, tasks: list[dict]) -> None:
        """0 with JSON on stdout, or 1 with the refusal on stderr."""
        answers = {t["name"]: t for t in tasks}
        try:
            walked = tw.walk(tasks[0]["name"] if tasks else "test", answers.get, list)
        except tw.RefusedError as exc:
            self.assertIn("task '", str(exc))
            return
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cj.vitest_command("coverage:check", walked)
        self.assertIn(status, (0, 1))
        if status == 0:
            self.assertEqual(json.loads(out.getvalue())["args"][0], "run")
        else:
            self.assertIn("coverage:check: typescript:", err.getvalue())


if __name__ == "__main__":
    unittest.main()

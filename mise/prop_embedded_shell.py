#!/usr/bin/env python3
"""embedded_shell.py's `run:` reader and writer, on files nobody wrote.

The properties of `test_embedded_shell.py`'s fixed rows (#1555, #1846),
over generated workflow steps and debt lines:

- Round trip (T): every `run:` value a workflow holds, however it is
  written — a literal block, anchored or not, a plain, single- or
  double-quoted single-line value, a flow mapping, a quoted key — reads
  back through `runs()` as the value generated, with its step's name
  (quoted or not), its `shell:` (bash when none) and its line, whatever
  indentation the file uses and whatever blank lines follow a block.
- Round trip (T): `rewrite` with a formatter that returns each script as
  it is leaves the file byte for byte; with one that changes each
  script, every literal block reads back formatted, every other value
  as it was, and every line outside the blocks as it was.
- Invariance (V): a folded block of plain lines reads as its lines
  joined by single spaces, and weighs as many lines as the file holds.
- Refusal (R): `runs` reads any text into values or refuses it with a
  ValueError naming the file, and `parse_entry` reads any debt line;
  neither raises anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import embedded_shell as es
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

TEXT = st.from_regex(r"[a-z][a-z0-9 =$\"'_./-]{0,20}", fullmatch=True).map(str.rstrip)
# A single-line command every scalar style holds as written: no quote,
# no `#`, no `:`, no flow indicator, and a leading word YAML resolves to
# no boolean or number.
LINE = st.from_regex(r"echo [a-z0-9 =$_./-]{0,15}", fullmatch=True).map(str.rstrip)


def well_ended(lines: list[str]) -> bool:
    """Tell whether a script starts unindented and ends on a non-blank line.

    Returns:
        True when it does.

    """
    return lines[0][:1] not in {"", " "} and bool(lines[-1].strip())


SCRIPT = st.lists(
    st.one_of(TEXT, st.just(""), TEXT.map("  {}".format)), min_size=1, max_size=5
).filter(well_ended)
# A name YAML reads as a string in every style: never a word it resolves.
RESOLVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}


def unresolved(name: str) -> bool:
    """Tell whether YAML reads a plain name as a string.

    Returns:
        True when it does.

    """
    return name.lower() not in RESOLVED


NAME = (
    st
    .from_regex(r"[A-Za-z][A-Za-z0-9 ]{0,12}", fullmatch=True)
    .map(str.strip)
    .filter(unresolved)
)
# A block whose body is a blank line, then text; and a value YAML reads
# as a boolean, which the parser refuses by name.
BLANK_THEN_TEXT: list[str] = ["run: |", "   ", "x"]
RUN_TRUE: list[str] = ["runs:", "  steps:", "    - run: true"]
# Every way a step can write its `run:`.
FORMS = ("literal", "anchored", "plain", "single", "double", "flow", "quoted-key")
BLOCKS = {"literal", "anchored"}
# A value's line, text, step name and shell.
Value = tuple[int, str, str | None, str]
Drawn = tuple[list[str], list[Value]]


def draw_step(draw: st.DrawFn, i: int, base: int, lines: list[str]) -> Value:
    """Write one step, its `run:` in a drawn form, onto the file's lines.

    Returns:
        The value's line (1-based), text, step name and shell.

    """
    form = draw(st.sampled_from(FORMS))
    name = draw(st.one_of(st.none(), NAME.filter(bool)))
    quote = draw(st.sampled_from(["", "'", '"']))
    shell = draw(st.sampled_from([None, "bash", "sh", "python"]))
    lead = " " * base + "- "
    keys = " " * (base + 2)
    if form == "flow":
        text = draw(LINE)
        pairs = [f"name: {quote}{name}{quote}"] if name is not None else []
        pairs += [f"shell: {shell}"] if shell is not None else []
        lines.append(lead + "{" + ", ".join([*pairs, f"run: {text}"]) + "}")
        return len(lines), text, name, shell or "bash"
    if name is not None:
        lines.append(f"{lead}name: {quote}{name}{quote}")
        lead = keys
    if shell is not None:
        lines.append(f"{lead}shell: {shell}")
        lead = keys
    if form in BLOCKS:
        script = draw(SCRIPT)
        anchor = f"&a{i} " if form == "anchored" else ""
        lines.append(f"{lead}run: {anchor}|")
        header = len(lines)
        pad = " " * (base + 2 + draw(st.sampled_from([2, 4])))
        lines += [(pad + s) if s else "" for s in script]
        lines += [""] * draw(st.integers(0, 2))
        return header, "\n".join(script) + "\n", name, shell or "bash"
    text = draw(LINE)
    key = '"run"' if form == "quoted-key" else "run"
    held = {"single": f"'{text}'", "double": f'"{text}"'}.get(form, text)
    lines.append(f"{lead}{key}: {held}")
    return len(lines), text, name, shell or "bash"


def draw_workflow(draw: st.DrawFn) -> Drawn:
    """Write a job's steps, each a `run:` value in a drawn form.

    Returns:
        The file's lines, and per value its line, text, name and shell.

    """
    base = draw(st.sampled_from([4, 6]))
    lines = ["on: push", "jobs:", "  build:", "    runs-on: ubuntu-latest"]
    lines.append(" " * (base - 2) + "steps:")
    expected = [draw_step(draw, i, base, lines) for i in range(draw(st.integers(1, 4)))]
    return lines, expected


workflow = st.composite(draw_workflow)


class Runs(unittest.TestCase):
    """A workflow's values, as written."""

    @given(workflow())
    def test_values_read_back(self, drawn: Drawn) -> None:
        """Line, text, name and shell of every value, in every form."""
        lines, expected = drawn
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "ci.yml"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            got = [(r.line, r.text, r.name, r.shell) for r in es.runs(path)]
        self.assertEqual(got, expected, "\n".join(lines))

    @given(st.lists(st.text(max_size=30), max_size=12))
    @example(BLANK_THEN_TEXT)
    @example(RUN_TRUE)
    def test_any_text_is_read_or_refused_by_name(self, lines: list[str]) -> None:
        """Values or a refusal naming the file, never a traceback."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "ci.yml"
            path.write_text("\n".join(lines), encoding="utf-8")
            try:
                found = list(es.runs(path))
            except ValueError as err:
                self.assertTrue(str(err).startswith(f"{path}:"), err)
                return
        for run in found:
            self.assertTrue(run.source.endswith("\n"))


def formatter(*, change: bool) -> object:
    """Stand in for shfmt: echo the script, or append a marker line.

    Returns:
        A stub for es.shfmt.

    """

    def run(source: str) -> str:
        return source + "echo formatted\n" if change else source

    return run


class Rewrite(unittest.TestCase):
    """The writer puts back exactly what the formatter gives, where it was."""

    @given(workflow(), st.sampled_from(["same", "changed"]))
    def test_rewrite_round_trip(self, drawn: Drawn, how: str) -> None:
        """Unchanged scripts, an unchanged file; changed ones, read back."""
        lines, expected = drawn
        text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "ci.yml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(es, "shfmt", formatter(change=how == "changed")):
                count = es.rewrite(path)
            after = path.read_text(encoding="utf-8")
            reread = [r.text for r in es.runs(path)]
        # A block's text ends in a line break; a single-line value's never.
        blocks = [v[3] in {"bash", "sh"} and v[1].endswith("\n") for v in expected]
        if how == "same":
            self.assertEqual((count, after), (0, text))
            return
        self.assertEqual(count, sum(blocks))
        want = [
            v[1] + "echo formatted\n" if block else v[1]
            for v, block in zip(expected, blocks, strict=True)
        ]
        self.assertEqual(reread, want)
        kept = [line for line in after.splitlines() if "echo formatted" not in line]
        self.assertEqual(kept, lines)


class Folded(unittest.TestCase):
    """Plain lines fold to one line, and weigh as many as the file holds."""

    @given(st.lists(TEXT.filter(bool), min_size=1, max_size=6))
    def test_plain_lines_join_and_are_counted(self, body: list[str]) -> None:
        """Single breaks become single spaces; the ceiling counts each line."""
        lines = ["runs:", "  steps:", "    - run: >-"]
        lines += [f"        {x}" for x in body]
        lines.append("    - run: echo after")
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "action.yml"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            [run, after] = list(es.runs(path))
        self.assertEqual(run.text, " ".join(body))
        self.assertEqual(es.weight(run.source), len(body))
        self.assertEqual(after.text, "echo after")


class DebtLine(unittest.TestCase):
    """A debt line is an entry or a sentence, never a traceback."""

    @given(
        st.one_of(
            st.text(max_size=40),
            st.lists(st.text(max_size=10), min_size=4, max_size=4).map("\t".join),
        )
    )
    @example("ci.yml\tbuild\t²\t# #1")
    def test_any_line_is_read(self, raw: str) -> None:
        """An Entry over the ceiling, or a message."""
        got = es.parse_entry(raw)
        if isinstance(got, str):
            self.assertTrue(got)
        else:
            self.assertGreater(got.lines, es.CEILING)

    @given(
        st.from_regex(r"\.github/workflows/[a-z]{1,6}\.yml", fullmatch=True),
        NAME.filter(bool),
        st.integers(1, 200),
        st.integers(1, 9999),
    )
    @example(".github/workflows/a.yml", "A", 16, 1352)
    def test_an_entry_reads_back(
        self, file: str, step: str, count: int, issue: int
    ) -> None:
        """Written as the file spells it, read back as written."""
        note = f"# #{issue}"
        got = es.parse_entry(f"{file}\t{step}\t{count}\t{note}")
        # The ceiling's own issue is no remover: a note naming it must say
        # `unassigned` (the rule's own refusal, not a parse failure).
        umbrella = es.WORKFLOW.umbrella.search(note) is not None
        if count <= es.CEILING or umbrella:
            self.assertIsInstance(got, str)
        else:
            self.assertEqual(got, es.Entry(file, step, count, 0))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""embedded_shell.py's `run:` reader and writer, on files nobody wrote.

The properties of `test_embedded_shell.py`'s fixed rows (#1555), over
generated workflow steps and debt lines:

- Round trip (T): every `run: |` block a workflow holds reads back as
  the script it was written from, dedented, with its step's name
  (quoted or not), its `shell:` (bash when none) and the line its body
  starts on, whatever indentation the file uses and whatever blank
  lines follow the block.
- Round trip (T): `rewrite` with a formatter that returns each body as
  it is leaves the file byte for byte; with one that changes each
  body, the file reads back with exactly the formatted scripts and
  every line outside the blocks as it was.
- Invariance (V): folding a block with no blank and no indented lines
  is its lines joined by single spaces.
- Refusal (R): `runs_in` reads any text, and `parse_entry` any debt
  line, and never raise.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import] - only stubbed
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import embedded_shell as es
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

TEXT = st.from_regex(r"[a-z][a-z0-9 =$\"'_./-]{0,20}", fullmatch=True).map(str.rstrip)


def well_ended(lines: list[str]) -> bool:
    """Tell whether a script starts unindented and ends on a non-blank line.

    Returns:
        True when it does.

    """
    return lines[0][:1] not in {"", " "} and bool(lines[-1].strip())


SCRIPT = st.lists(
    st.one_of(TEXT, st.just(""), TEXT.map("  {}".format)), min_size=1, max_size=5
).filter(well_ended)
# A block whose body is a blank line, then text.
BLANK_THEN_TEXT: list[str] = ["run: |", "   ", "x"]
NAME = st.from_regex(r"[A-Za-z][A-Za-z0-9 ]{0,12}", fullmatch=True).map(str.strip)
Block = tuple[int, str, str | None, str]


def draw_workflow(draw: st.DrawFn) -> tuple[list[str], list[Block]]:
    """Write a job's steps, each a `run:` block.

    Returns:
        The file's lines, and per block: its first body line (1-based),
        its script, its name and its shell.

    """
    base = draw(st.sampled_from([4, 6]))
    pad = draw(st.sampled_from([2, 4]))
    lines = [
        "on: push",
        "jobs:",
        "  build:",
        "    runs-on: ubuntu-latest",
        "    steps:",
    ]
    lines[-1] = " " * (base - 2) + "steps:"
    expected: list[Block] = []
    for _ in range(draw(st.integers(1, 4))):
        name = draw(st.one_of(st.none(), NAME.filter(bool)))
        shell = draw(st.sampled_from([None, "bash", "sh", "python"]))
        lead = " " * base + "- "
        keys = " " * (base + 2)
        if name is not None:
            quote = draw(st.sampled_from(["", "'", '"']))
            lines.append(f"{lead}name: {quote}{name}{quote}")
            lead = keys
        if shell is not None:
            lines.append(f"{lead}shell: {shell}")
            lead = keys
        script = draw(SCRIPT)
        lines.append(f"{lead}run: |")
        first = len(lines) + 1
        lines += [(" " * (base + 2 + pad) + s) if s else "" for s in script]
        lines += [""] * draw(st.integers(0, 2))
        expected.append((first, "\n".join(script) + "\n", name, shell or "bash"))
    return lines, expected


workflow = st.composite(draw_workflow)


class Runs(unittest.TestCase):
    """A workflow's blocks, as written."""

    @given(workflow())
    def test_blocks_read_back(self, drawn: tuple[list[str], list[Block]]) -> None:
        """Line, script, name and shell of every block."""
        lines, expected = drawn
        got = [(r.line, r.source, r.name, r.shell) for r in es.runs_in(lines)]
        self.assertEqual(got, expected, "\n".join(lines))

    @given(st.lists(st.text(max_size=30), max_size=12))
    @example(BLANK_THEN_TEXT)
    def test_any_text_is_read(self, lines: list[str]) -> None:
        """Blocks or none, never a traceback."""
        for run in es.runs_in(lines):
            self.assertTrue(run.source.endswith("\n"))


def formatter(*, change: bool) -> object:
    """Stand in for shfmt: echo the script, or append a marker line.

    Returns:
        A stub for subprocess.run.

    """

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        text = str(kwargs["input"])
        if change:
            text += "echo formatted\n"
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    return run


class Rewrite(unittest.TestCase):
    """The writer puts back exactly what the formatter gives."""

    @given(workflow(), st.sampled_from(["same", "changed"]))
    def test_rewrite_round_trip(
        self, drawn: tuple[list[str], list[Block]], how: str
    ) -> None:
        """Unchanged bodies, an unchanged file; changed ones, read back."""
        lines, expected = drawn
        text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "ci.yml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(
                subprocess, "run", formatter(change=how == "changed")
            ):
                count = es.rewrite(path)
            after = path.read_text(encoding="utf-8")
        literal = [b for b in expected if b[3] in {"bash", "sh"}]
        if how == "same":
            self.assertEqual((count, after), (0, text))
            return
        self.assertEqual(count, len(literal))
        got = {
            r.source
            for r in es.runs_in(after.splitlines())
            if r.shell in {"bash", "sh"}
        }
        formatted = {b[1] + "echo formatted\n" for b in literal}
        self.assertEqual(got, formatted)
        kept = [line for line in after.splitlines() if "echo formatted" not in line]
        self.assertEqual(kept, lines)


class Fold(unittest.TestCase):
    """Plain lines fold to one line."""

    @given(st.lists(TEXT.filter(bool), min_size=1, max_size=6))
    def test_plain_lines_join(self, lines: list[str]) -> None:
        """Single breaks become single spaces."""
        self.assertEqual(es.fold("\n".join(lines) + "\n"), " ".join(lines) + "\n")


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

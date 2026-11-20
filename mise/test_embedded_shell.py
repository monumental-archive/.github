#!/usr/bin/env python3
"""Table tests for the `run:` block ceiling (#1352) and the task one (#1544).

One row per guard branch of lint:workflow-shell, each driven through the
same reader the lint uses and each asserting the MESSAGE, not just the
outcome: a red that fires for the wrong reason looks exactly like a red
that fires for the right one. The fix rows read the debt file back from
disk after `--write`, never the verdict's own bookkeeping.

The last rows run the task bodies themselves, decoded from the belt's
config, in a scratch repository: the skip on a tree with no workflows
is a branch of the TASK, not the helper, and a guard that skips when it
should run looks like success.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import io
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tomllib
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import embedded_shell as es
import hermetic

_HERE = Path(__file__).resolve().parent

ISSUE = "\t# canon helper: #1386"
GIT = hermetic.tool("git")
BASH = hermetic.tool("bash")
# A scratch repository must not inherit the caller's git: under a hook,
# GIT_DIR and GIT_INDEX_FILE point every `git` at the repository being
# pushed, and `git ls-files` would list the canon's own workflows.
CLEAN_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def workflow(*steps: str) -> str:
    """Wrap step texts (each starting `- `) in a one-job workflow.

    Returns:
        The workflow's text.

    """
    body = "".join(
        "".join(f"      {line}\n" if line else "\n" for line in s.splitlines())
        for s in steps
    )
    return f"on: push\njobs:\n  j:\n    runs-on: x\n    steps:\n{body}"


def step(name: str | None, lines: int, *, extra: str = "") -> str:
    """Build a step whose `run:` block holds `lines` counted lines.

    Returns:
        The step's text at zero indent.

    """
    head = f"- name: {name}\n  run: |\n" if name is not None else "- run: |\n"
    body = "".join(f"    echo {i}\n" for i in range(lines))
    return head + body + extra


class Tree:
    """A scratch directory holding one workflow and a debt file."""

    def __init__(self, root: Path, text: str, debt: str | None) -> None:
        """Write the workflow, and the debt file when one is given."""
        self.wf = root / "w.yml"
        self.wf.write_text(text, encoding="utf-8")
        self.debt = root / "debt.txt"
        if debt is not None:
            self.debt.write_text(debt, encoding="utf-8")

    def entry(self, name: str, lines: int) -> str:
        """Return one debt line naming this tree's workflow.

        Returns:
            The tab-separated entry, newline-terminated.

        """
        return f"{self.wf}\t{name}\t{lines}{ISSUE}\n"

    def run(self, *, write: bool = False) -> tuple[int, str, str]:
        """Run the ceiling over the workflow.

        Returns:
            (exit status, stdout, stderr).

        """
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = es.ceiling([str(self.wf)], self.debt, write=write)
        return rc, out.getvalue(), err.getvalue()


class Ceiling(unittest.TestCase):
    """The Done-when rows of #1352, one guard branch each."""

    def tree(self, text: str, debt: str | None = None) -> Tree:
        """Build a scratch tree that lives as long as the test.

        Returns:
            The tree.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Tree(Path(tmp.name), text, debt)

    def test_at_the_ceiling_passes(self) -> None:
        """Fifteen counted lines is allowed with no entry."""
        rc, out, err = self.tree(workflow(step("Fits", 15))).run()
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("every run: block fits 15 lines or its debt entry", out)

    def test_over_and_unlisted_is_red_by_file_and_step(self) -> None:
        """Sixteen lines with no entry names the file, the step and the count."""
        t = self.tree(workflow(step("Big", 16)))
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{t.wf}:8: step 'Big' holds a run: block of 16 lines, over the "
            f"15-line ceiling, and {t.debt} does not list it",
            err,
        )

    def test_listed_and_equal_passes(self) -> None:
        """An entry naming the block at its count covers it."""
        t = self.tree(workflow(step("Big", 16)))
        t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
        rc, out, err = t.run()
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("lowers the entry", out)

    def test_listed_and_grown_is_red(self) -> None:
        """A block past its entry's number is red, naming both numbers."""
        t = self.tree(workflow(step("Big", 17)))
        t.debt.write_text("# header\n" + t.entry("Big", 16), encoding="utf-8")
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"step 'Big' holds 17 lines, more than its debt entry's 16 "
            f"({t.debt}:2) — the debt only shrinks",
            err,
        )

    def test_shrunk_but_over_passes_and_fix_lowers_it(self) -> None:
        """A shrunk block passes; fix writes the new number, nothing else."""
        t = self.tree(workflow(step("Big", 17), step("Other", 20)))
        before = "# keep me\n" + t.entry("Big", 30) + t.entry("Other", 20)
        t.debt.write_text(before, encoding="utf-8")
        rc, out, err = t.run()
        self.assertEqual((rc, err), (0, ""))
        self.assertIn(
            f"{t.debt}:2: the block now holds 17 lines — "
            "mise run fix:workflow-shell lowers the entry",
            out,
        )
        rc, out, _ = t.run(write=True)
        self.assertEqual(rc, 0)
        self.assertIn("1 entry(ies) lowered, 0 removed", out)
        self.assertEqual(
            t.debt.read_text(encoding="utf-8"),
            "# keep me\n" + t.entry("Big", 17) + t.entry("Other", 20),
        )

    def test_entry_whose_block_fits_is_stale_and_fix_removes_it(self) -> None:
        """An entry for a block now at the ceiling is red; fix drops it."""
        t = self.tree(workflow(step("Big", 15)))
        t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{t.debt}:1: step 'Big' in {t.wf} now fits the ceiling (15 lines) "
            "— remove the entry (mise run fix:workflow-shell)",
            err,
        )
        _, out, _ = t.run(write=True)
        self.assertIn("0 entry(ies) lowered, 1 removed", out)
        self.assertEqual(t.debt.read_text(encoding="utf-8"), "\n")
        self.assertEqual(t.run()[0], 0)

    def test_entry_naming_nothing_is_stale_and_fix_removes_it(self) -> None:
        """An entry for a step that does not exist is red; fix drops it."""
        t = self.tree(workflow(step("Big", 16)))
        t.debt.write_text(t.entry("Big", 16) + t.entry("Gone", 40), encoding="utf-8")
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{t.debt}:2: step 'Gone' in {t.wf} names no run: block "
            "— remove the entry (mise run fix:workflow-shell)",
            err,
        )
        t.run(write=True)
        self.assertEqual(t.debt.read_text(encoding="utf-8"), t.entry("Big", 16))

    def test_unnamed_oversized_step_is_red(self) -> None:
        """An oversized block in a step with no name cannot be keyed."""
        t = self.tree(workflow(step(None, 16)))
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{t.wf}:7: a run: block of 16 lines, over the 15-line ceiling, in a "
            "step with no name — the debt file keys on the step name",
            err,
        )

    def test_duplicated_step_name_is_red_even_when_listed(self) -> None:
        """Two blocks sharing a name make the key ambiguous."""
        t = self.tree(workflow(step("Twin", 16), step("Twin", 3)))
        t.debt.write_text(t.entry("Twin", 16), encoding="utf-8")
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            "step 'Twin' holds a run: block of 16 lines, over the 15-line "
            "ceiling, and 2 run: blocks here share that name "
            "— the debt file keys on the step name",
            err,
        )

    def test_fix_never_adds_an_entry(self) -> None:
        """An unlisted block stays red after fix, the file untouched."""
        t = self.tree(workflow(step("Big", 16)))
        t.debt.write_text("# only a header\n", encoding="utf-8")
        rc, out, _ = t.run(write=True)
        self.assertEqual(rc, 0)
        self.assertIn("0 entry(ies) lowered, 0 removed; it never adds one", out)
        self.assertEqual(t.debt.read_text(encoding="utf-8"), "# only a header\n")
        self.assertEqual(t.run()[0], 1)

    def test_no_debt_file_is_an_empty_debt(self) -> None:
        """A repository without the file is held to the bare ceiling."""
        t = self.tree(workflow(step("Big", 16)))
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn("does not list it", err)
        self.assertFalse(t.debt.exists())
        self.assertEqual(t.run(write=True)[0], 0)
        self.assertFalse(t.debt.exists())


def folded(name: str, lines: int, header: str = ">-") -> str:
    """Build a step whose `run:` is a folded block of `lines` lines.

    Returns:
        The step's text at zero indent.

    """
    body = "".join(f"    word{i}\n" for i in range(lines))
    return f"- name: {name}\n  run: {header}\n{body}"


class Headers(unittest.TestCase):
    """Every block-scalar header is counted; shellcheck reads `|` and `|-`."""

    def tree(self, text: str) -> Tree:
        """Build a scratch tree that lives as long as the test.

        Returns:
            The tree.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Tree(Path(tmp.name), text, None)

    def test_folded_over_the_ceiling_and_unlisted_is_red(self) -> None:
        """A `>-` block is no opt-out: sixteen lines is red by step."""
        t = self.tree(workflow(folded("Fold", 16)))
        rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{t.wf}:8: step 'Fold' holds a run: block of 16 lines, over the "
            f"15-line ceiling, and {t.debt} does not list it",
            err,
        )

    def test_folded_within_the_ceiling_passes(self) -> None:
        """A folded block at fifteen lines passes like a literal one."""
        rc, out, err = self.tree(workflow(folded("Fold", 15))).run()
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("every run: block fits 15 lines or its debt entry", out)

    def test_every_header_is_counted(self) -> None:
        """Each indicator form, and a trailing comment, reaches the ceiling."""
        for header in ["|", "|-", "|+", "|2", "|2-", "|-2", ">", ">-", ">+", ">2"]:
            for comment in ["", " # zizmor: ignore[github-env]"]:
                with self.subTest(header=header, comment=comment):
                    [run] = read_runs(workflow(folded("H", 16, header + comment)))
                    self.assertEqual((run.header, es.weight(run.source)), (header, 16))
                    self.assertEqual(run.commented, bool(comment))

    def test_what_shellcheck_reads_and_how(self) -> None:
        """Every literal header is read as written, every folded one folded.

        A comment after the indicator changes nothing about either.
        """
        rows = [
            ("|", [False]),
            ("|-", [False]),
            ("|+", [False]),
            ("|2", [False]),
            ("|2-", [False]),
            (">", [True]),
            (">-", [True]),
            (">+", [True]),
            (">2", [True]),
            ("| # zizmor: ignore[github-env]", [False]),
            (">- # a note", [True]),
        ]
        for header, folds in rows:
            with self.subTest(header=header), TemporaryDirectory() as tmp:
                p = Path(tmp) / "w.yml"
                p.write_text(workflow(folded("H", 3, header)), encoding="utf-8")
                self.assertEqual([f for _, _, f in es.blocks(p)], folds)
                self.assertEqual(es.weight(next(es.runs(p)).source), 3)

    def test_mint_doi_folded_block_is_seen(self) -> None:
        """The canon's one `>-` block is read and counted (3 lines)."""
        path = _HERE.parent / ".github" / "workflows" / "mint-doi.yml"
        found = [r for r in es.runs(path) if r.header == ">-"]
        self.assertEqual(
            [(r.name, es.weight(r.source)) for r in found],
            [("Deposit, then publish — the DOI names the released bytes", 3)],
        )


def action(header: str, body: str) -> str:
    """Build a composite action with one bash step under `header`.

    Returns:
        The action's text; the step's body starts on line 8.

    """
    lines = "".join(f"        {x}\n" if x else "\n" for x in body.split("\n"))
    return (
        "name: a\nruns:\n  using: composite\n  steps:\n"
        f"    - name: Act\n      shell: bash\n      run: {header}\n{lines}"
    )


class Folded(unittest.TestCase):
    """#1396: shellcheck reads a folded block as bash receives it."""

    def check(self, text: str, *, fmt: bool = True) -> tuple[Path, list[str]]:
        """Run shfmt and shellcheck over one action text.

        Returns:
            The file's path and the findings.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "action.yml"
        path.write_text(text, encoding="utf-8")
        return path, es.check(path, fmt=fmt, lint=True)

    def test_planted_finding_in_a_folded_composite_block(self) -> None:
        """A finding inside `run: >-` in an action is reported on the block's line.

        The test that fails before #1396: the reader skipped folded blocks
        and this returned no finding at all.
        """
        path, found = self.check(action(">-", "rm -rf\n${UNQUOTED}"))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(f"{path}:8:"), found[0])
        self.assertIn("SC2086", found[0])
        self.assertIn(f"[{es.FOLDED_NOTE}]", found[0])

    def test_the_note_is_said_once_per_block(self) -> None:
        """Two findings in one folded block: the note rides on the first."""
        _, found = self.check(action(">", "echo ${A}\n\necho ${B}"))
        self.assertEqual(len(found), 2, found)
        self.assertEqual([es.FOLDED_NOTE in f for f in found], [True, False])
        self.assertTrue(all(":8:" in f for f in found), found)

    def test_folding_is_what_shellcheck_judges(self) -> None:
        """A quoted string split across lines is one word once folded."""
        _, found = self.check(action(">-", 'printf "%s\\n" "a\nb"'))
        self.assertEqual(found, [])

    def test_a_clean_folded_block_passes_and_shfmt_does_not_read_it(self) -> None:
        """Text shfmt would reformat is not a finding in a folded block."""
        _, found = self.check(action(">", "if true;then echo x;fi"))
        self.assertEqual(found, [])
        _, literal = self.check(action("|", "if true;then echo x;fi"))
        self.assertTrue(any("not shfmt-formatted" in f for f in literal), literal)

    def test_literal_headers_get_both_passes(self) -> None:
        """`|+`, an indicator and a commented header get shfmt and shellcheck."""
        for header in ["|+", "|2", "|2-", "| # zizmor: ignore[github-env]"]:
            with self.subTest(header=header):
                path, found = self.check(action(header, "if true;then echo ${X};fi"))
                self.assertIn(f"{path}:8: run: block is not shfmt-formatted", found)
                self.assertTrue(any("SC2086" in f for f in found), found)

    def test_commented_header_finding_lands_on_its_line(self) -> None:
        """A literal block under `run: | # ...` reports on the finding's own line."""
        path, found = self.check(
            action("| # zizmor: ignore[github-env]", "echo ok\nrm -rf ${GONE}"),
        )
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(f"{path}:9:"), found[0])
        self.assertIn("SC2086", found[0])
        self.assertNotIn(es.FOLDED_NOTE, found[0])

    def test_rewrite_keeps_a_header_comment(self) -> None:
        """The header line, comment included, survives the shfmt rewrite."""
        n, out = rewrite_text(action("| # keep", "if true;then echo x;fi"))
        self.assertEqual(n, 1)
        self.assertEqual(out, action("| # keep", "if true; then echo x; fi"))

    def test_fold(self) -> None:
        """The fold, against YAML's rule, row by row."""
        rows = [
            ("a\nb\n", "a b\n"),
            ("a\n\nb\n", "a\nb\n"),
            ("a\n\n\nb\n", "a\n\nb\n"),
            ("a\n  b\nc\n", "a\n  b\nc\n"),
            ("a\n\n  b\n", "a\n\n  b\n"),
            ("\na\nb\n", "\na b\n"),
        ]
        for source, folded_text in rows:
            with self.subTest(source=source):
                self.assertEqual(es.fold(source), folded_text)


class OrgConfig(unittest.TestCase):
    """The org's shellcheck settings are the ones applied (#696, #733)."""

    def test_a_home_shellcheckrc_cannot_lower_the_org_checks(self) -> None:
        """A machine's own rc file does not switch a belt check off.

        Without `--rcfile`, shellcheck reads `~/.shellcheckrc`, so a
        `disable=` there silently dropped a check the belt enables; the
        delivered `mise/shellcheckrc` is passed instead and nothing else
        is read.
        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / "home"
        home.mkdir()
        (home / ".shellcheckrc").write_text("disable=SC2250\n", encoding="utf-8")
        path = Path(tmp.name) / "wf.yml"
        path.write_text(
            workflow('- name: s\n  run: |\n    x=1\n    echo "$x"'),
            encoding="utf-8",
        )
        with unittest.mock.patch.dict(os.environ, hermetic.env(home), clear=True):
            found = es.check(path, fmt=False, lint=True)
        self.assertTrue(any("[SC2250]" in f for f in found), found)


def rewrite_text(text: str) -> tuple[int, str]:
    """Rewrite one action text in place.

    Returns:
        The number of blocks rewritten, and the file afterwards.

    """
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "action.yml"
        path.write_text(text, encoding="utf-8")
        n = es.rewrite(path)
        return n, path.read_text(encoding="utf-8")


class Rewrite(unittest.TestCase):
    """`--write` splices through the one reader and leaves folded text alone."""

    def test_literal_block_is_formatted_at_its_indent(self) -> None:
        """A `|` block comes back shfmt-formatted under the same key."""
        n, out = rewrite_text(action("|", "if true;then echo x;fi"))
        self.assertEqual(n, 1)
        self.assertEqual(out, action("|", "if true; then echo x; fi"))

    def test_folded_block_is_never_rewritten(self) -> None:
        """A folded block is never handed to shfmt's rewrite."""
        text = action(">", "if true;then echo x;fi")
        self.assertEqual(rewrite_text(text), (0, text))

    def test_indentation_indicator_keeps_its_content_column(self) -> None:
        """`|2` content sits two past the key, and the rewrite writes it there.

        The leading spaces beyond the indicator are script text, which
        shfmt drops; reading the column from the deepest line instead
        would have written the result back two columns too far right.
        """
        text = action("|2", "  if true;then echo x;fi")
        n, out = rewrite_text(text)
        self.assertEqual(n, 1)
        self.assertEqual(out, action("|2", "if true; then echo x; fi"))

    def test_keep_chomping_keeps_trailing_blank_lines(self) -> None:
        """`|+` trailing blank lines are value, and survive the rewrite."""
        text = action("|+", "if true;then echo x;fi\n\n")
        n, out = rewrite_text(text)
        self.assertEqual(n, 1)
        self.assertEqual(out, action("|+", "if true; then echo x; fi\n\n"))


def blank_after(first: str) -> str:
    """Build #1431's five-step workflow: blank lines after two blocks.

    The first block's body is `first`, on line 6, with a blank line and
    a comment after it; the third block's body is on line 14, with a
    blank line before the next step. The other blocks have none.

    Returns:
        The workflow's text.

    """
    return (
        "on: push\n"
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - run: |\n"
        f"          {first}\n"
        "\n"
        "      # a comment\n"
        "      - name: two\n"
        "        run: |\n"
        "          echo two\n"
        "      - name: three\n"
        "        run: |\n"
        "          echo three\n"
        "\n"
        "      - run: |\n"
        "          echo four\n"
        "      - run: echo five\n"
    )


class TrailingBlank(unittest.TestCase):
    """#1431: blank lines after a block are YAML layout, not shell."""

    def write(self, text: str) -> Path:
        """Write one workflow text to a scratch file.

        Returns:
            The file's path.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "w.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_blank_line_after_a_literal_block_is_not_a_finding(self) -> None:
        """A formatted block followed by a blank line is clean.

        The test that fails before #1431: the reader took the blank line
        as body, shfmt reported it, and lines 6 and 14 were red.
        """
        path = self.write(blank_after("echo one"))
        self.assertEqual(es.check(path, fmt=True, lint=True), [])

    def test_rewrite_keeps_blank_lines_after_a_block(self) -> None:
        """`--write` on clean blocks rewrites none and changes no byte.

        The test that fails before #1431: the fixer reformatted two
        blocks by deleting the blank line after each.
        """
        text = blank_after("echo one")
        path = self.write(text)
        self.assertEqual(es.rewrite(path), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_unformatted_shell_before_a_blank_line_stays_red(self) -> None:
        """The shell itself still reds, and the fix keeps the blank line."""
        path = self.write(blank_after("if true;then echo x;fi"))
        found = es.check(path, fmt=True, lint=True)
        self.assertEqual(found, [f"{path}:6: run: block is not shfmt-formatted"])
        self.assertEqual(es.rewrite(path), 1)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            blank_after("if true; then echo x; fi"),
        )


class DebtFile(unittest.TestCase):
    """Lines of the debt file that are not entries are red, by line."""

    def test_malformed_lines(self) -> None:
        """Field count, count and note are each refused with their reason."""
        rows = [
            ("w.yml\tBig\t16\n", "this line has 3 field(s)"),
            ("w.yml\t\t16\t# #1\n", "this line has 4 field(s)"),
            ("w.yml\tBig\t15\t# #1\n", "'15' is not a line count over the 15-line"),
            ("w.yml\tBig\tmany\t# #1\n", "'many' is not a line count"),
            # A non-ASCII digit: isdigit took it and int() raised (#1555).
            ("w.yml\tBig\t\u00b2\t# #1\n", "'\u00b2' is not a line count"),
            ("w.yml\tBig\t16\tsoon #1\n", "the note is a `#` comment naming the issue"),
            ("w.yml\tBig\t16\t# later\n", "the note is a `#` comment naming the issue"),
            (
                "w.yml\tBig\t16\t# canon helper: #1352\n",
                "a note naming #1352 says `unassigned`: name the issue that removes",
            ),
        ]
        for line, message in rows:
            with self.subTest(line=line), TemporaryDirectory() as tmp:
                debt = Path(tmp) / "debt.txt"
                debt.write_text("# header\n\n" + line, encoding="utf-8")
                entries, errors = es.read_debt(debt)
                self.assertEqual(entries, [])
                self.assertEqual(len(errors), 1)
                self.assertTrue(errors[0].startswith(f"{debt}:3: "), errors[0])
                self.assertIn(message, errors[0])

    def test_umbrella_with_unassigned_is_an_entry(self) -> None:
        """#1352 with `unassigned` reads; a longer number is another issue."""
        with TemporaryDirectory() as tmp:
            debt = Path(tmp) / "debt.txt"
            debt.write_text(
                "w.yml\tA\t16\t# stele: #1352 unassigned\n"
                "w.yml\tB\t16\t# canon helper: #13520\n",
                encoding="utf-8",
            )
            entries, errors = es.read_debt(debt)
        self.assertEqual(errors, [])
        self.assertEqual([e.step for e in entries], ["A", "B"])

    def test_listed_twice(self) -> None:
        """The second copy of an entry is named."""
        with TemporaryDirectory() as tmp:
            debt = Path(tmp) / "debt.txt"
            debt.write_text("w.yml\tBig\t16\t# #1\n" * 2, encoding="utf-8")
            entries, errors = es.read_debt(debt)
        self.assertEqual(len(entries), 1)
        self.assertEqual(errors, [f"{debt}:2: step 'Big' in w.yml is listed twice"])

    def test_malformed_line_reds_the_lint(self) -> None:
        """A bad line reaches the verdict, not just the parser."""
        with TemporaryDirectory() as tmp:
            t = Tree(Path(tmp), workflow(step("Small", 2)), "w.yml\tBig\n")
            rc, _, err = t.run()
        self.assertEqual(rc, 1)
        self.assertIn("this line has 2 field(s)", err)


def read_runs(text: str) -> list:
    """Read a workflow text through the shared reader.

    Returns:
        Its Run records.

    """
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.yml"
        p.write_text(text, encoding="utf-8")
        return list(es.runs(p))


class Reader(unittest.TestCase):
    """The weight and the step name, as the one reader sees them."""

    def test_blank_and_comment_lines_do_not_count_heredocs_do(self) -> None:
        """Comments and blanks are free; a heredoc body is lines like any."""
        text = workflow(
            "- name: S\n  run: |\n    # why\n\n    cat > f <<'EOF'\n"
            "    a: 1\n    b: 2\n    EOF\n      # indented comment\n    echo done\n",
        )
        [run] = read_runs(text)
        self.assertEqual(es.weight(run.source), 5)

    def test_step_names(self) -> None:
        """Each place a step can carry its name, and places that are not one."""
        rows = [
            ("- name: Plain\n  run: |\n    x\n", "Plain"),
            ("- name: 'Single quoted'\n  run: |\n    x\n", "Single quoted"),
            ('- name: "Double: quoted"\n  run: |\n    x\n', "Double: quoted"),
            ("- name: Commented # note\n  run: |\n    x\n", "Commented"),
            ("- run: |\n    x\n  name: After\n", "After"),
            ("- id: a\n  name: Middle\n  run: |-\n    x\n", "Middle"),
            ("- run: |\n    name: inside\n", None),
            ("- uses: a/b@c\n  with:\n    name: input\n  run: |\n    x\n", None),
            ("- name: First\n  run: |\n    x\n- run: |\n    y\n", "First"),
        ]
        for text, name in rows:
            with self.subTest(text=text):
                self.assertEqual(read_runs(workflow(text))[0].name, name)
        second = read_runs(workflow(rows[-1][0]))[1]
        self.assertIsNone(second.name)

    def test_composite_action_steps(self) -> None:
        """A composite action's steps are read at their own indent."""
        text = (
            "runs:\n  using: composite\n  steps:\n"
            "    - name: Act\n      shell: bash\n      run: |\n        x\n"
        )
        [run] = read_runs(text)
        self.assertEqual((run.name, run.line), ("Act", 7))

    def test_the_shell_key_anywhere_in_the_step(self) -> None:
        """First on the `- ` line, or after the block: still the step's (#1555).

        Both were read as bash, so a python block went to shfmt and
        shellcheck.
        """
        rows = [
            ("- shell: python\n  run: |\n    print(1)\n", "python"),
            ("- run: |\n    print(1)\n  shell: python\n", "python"),
            ("- name: S\n  run: |\n    x\n  shell: sh\n", "sh"),
            ("- run: |\n    shell: python\n", "bash"),
            ("- shell: python\n  run: |\n    x\n- run: |\n    y\n", "python"),
        ]
        for text, shell in rows:
            with self.subTest(text=text):
                self.assertEqual(read_runs(workflow(text))[0].shell, shell)
        second = read_runs(workflow(rows[-1][0]))[1]
        self.assertEqual(second.shell, "bash")

    def test_every_interpreter_is_held_but_shellcheck_reads_bash(self) -> None:
        """The ceiling counts a python block; shellcheck's view skips it."""
        text = workflow("- name: Py\n  shell: python\n  run: |\n    print(1)\n")
        [run] = read_runs(text)
        self.assertEqual(run.shell, "python")
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "w.yml"
            p.write_text(text, encoding="utf-8")
            self.assertEqual(list(es.blocks(p)), [])


def task_body(name: str) -> str:
    """Decode a task's body from the belt's own config.

    Returns:
        The bash source mise runs.

    """
    config = tomllib.loads((_HERE / "config.toml").read_text(encoding="utf-8"))
    return config["tasks"][name]["run"]


def run_task(root: Path, name: str) -> subprocess.CompletedProcess[str]:
    """Run one task body under the interpreter mise uses.

    Returns:
        The finished process.

    """
    env = {**CLEAN_ENV, "ORG_BELT_DIR": str(_HERE)}
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-euo", "pipefail", "-c", task_body(name)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class Task(unittest.TestCase):
    """The task bodies, run as mise runs them, in a scratch repository."""

    def repo(self) -> Path:
        """Initialise an empty git repository.

        Returns:
            Its root.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        subprocess.run([GIT, "init", "-q", str(root)], check=True, env=CLEAN_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]
        return root

    def test_no_workflows_skips_clean(self) -> None:
        """A tree tracking no workflow skips both tasks by name."""
        root = self.repo()
        for name, message in [
            ("lint:workflow-shell", "no workflows, actions or templates, skipped"),
            (
                "fix:workflow-shell",
                "no workflows, actions or templates, nothing to fix",
            ),
        ]:
            with self.subTest(task=name):
                r = run_task(root, name)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"{name}: {message}", r.stdout)

    def test_tracked_workflow_is_held(self) -> None:
        """The task reaches the helper: an oversized tracked block is red."""
        root = self.repo()
        wf = root / ".github" / "workflows" / "w.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(workflow(step("Big", 16)), encoding="utf-8")
        subprocess.run([GIT, "-C", str(root), "add", "."], check=True, env=CLEAN_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]
        r = run_task(root, "lint:workflow-shell")
        self.assertEqual(r.returncode, 1)
        self.assertIn(
            ".github/workflows/w.yml:8: step 'Big' holds a run: block of 16 lines, "
            "over the 15-line ceiling, and security/workflow-shell-debt.txt does "
            "not list it",
            r.stderr,
        )

    def test_untracked_workflow_is_not_read(self) -> None:
        """Tracked files only: an untracked workflow leaves the tree skipped."""
        root = self.repo()
        wf = root / ".github" / "workflows" / "w.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(workflow(step("Big", 16)), encoding="utf-8")
        r = run_task(root, "lint:workflow-shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped", r.stdout)


def config(**tasks: int | list[int]) -> str:
    """Build a mise config whose tasks carry bodies of the given weights.

    A list weight is a `run` given as a list of commands, one per entry.

    Returns:
        The TOML text.

    """
    out = ""
    for name, lines in tasks.items():
        out += f'[tasks."{name.replace("_", ":")}"]\n'
        if isinstance(lines, list):
            cmds = ", ".join(
                '"' + "\\n".join(f"echo {i}" for i in range(n)) + '"' for n in lines
            )
            out += f"run = [{cmds}]\n"
        else:
            body = "# why\n\n" + "".join(f"echo {i}\n" for i in range(lines))
            out += f"run = '''\n{body}'''\n"
    return out


class Tasks(unittest.TestCase):
    """The Done-when rows of #1544: task bodies under the same ceiling."""

    def setUp(self) -> None:
        """Give each row a scratch directory for the config and the debt."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = self.root / "mise.toml"
        self.debt = self.root / "debt.txt"

    def run_ceiling(self, text: str, *, write: bool = False) -> tuple[int, str, str]:
        """Write the config and run the task ceiling over it.

        Returns:
            (exit status, stdout, stderr).

        """
        self.cfg.write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = es.task_ceiling([str(self.cfg)], self.debt, write=write)
        return rc, out.getvalue(), err.getvalue()

    def entry(self, name: str, lines: int, note: str = "# #1544 unassigned") -> str:
        """Return one debt line naming this row's config.

        Returns:
            The tab-separated entry, newline-terminated.

        """
        return f"{self.cfg}\t{name}\t{lines}\t{note}\n"

    def test_at_the_ceiling_passes(self) -> None:
        """Fifteen counted lines, comments and blanks free, needs no entry."""
        rc, out, err = self.run_ceiling(config(lint_a=15))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("every task body fits 15 lines or its debt entry (0 over", out)

    def test_a_config_that_is_not_toml_is_refused_by_name(self) -> None:
        """An undecodable config is a refusal naming it, in each mode (#1681)."""
        try:
            tomllib.loads("lint")
        except tomllib.TOMLDecodeError as exc:
            why = str(exc)
        for write, label in ((False, "task-shell"), (True, "fix:task-shell")):
            rc, out, err = self.run_ceiling("lint", write=write)
            self.assertEqual((rc, out), (1, ""))
            self.assertEqual(err, f"{label}: {self.cfg}: not TOML: {why}\n")

    def test_tasks_that_is_not_a_table_is_refused_by_name(self) -> None:
        """`tasks = 1` has no bodies to weigh; it is named, not a crash."""
        rc, out, err = self.run_ceiling("tasks = 1\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(err, f"task-shell: {self.cfg}: tasks is not a table\n")

    def test_no_debt_file_and_no_long_body_passes(self) -> None:
        """A repository with nothing over the ceiling carries no file."""
        rc, out, _ = self.run_ceiling(config(lint_a=3, fix_a=[2, 2]))
        self.assertEqual(rc, 0)
        self.assertIn("task-shell: every task body fits", out)
        self.assertFalse(self.debt.exists())

    def test_over_the_ceiling_and_unlisted_is_red_by_name(self) -> None:
        """Sixteen lines with no entry is red, naming the task."""
        rc, _, err = self.run_ceiling(config(lint_a=16))
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{self.cfg}: task 'lint:a' holds a body of 16 lines, over the 15-line "
            f"ceiling, and {self.debt} does not list it — move the logic out",
            err,
        )
        self.assertIn("task-shell: 1 finding(s)", err)

    def test_list_run_is_one_body(self) -> None:
        """A `run` list of 8 + 8 commands is a 16-line body, not two."""
        self.cfg.write_text(config(lint_a=[8, 8]), encoding="utf-8")
        self.assertEqual(list(es.task_bodies(self.cfg)), [("lint:a", 16)])
        rc, _, err = self.run_ceiling(config(lint_a=[8, 8]))
        self.assertEqual(rc, 1)
        self.assertIn("task 'lint:a' holds a body of 16 lines", err)

    def test_listed_passes(self) -> None:
        """A body at or under its entry is green."""
        self.debt.write_text(self.entry("lint:a", 20), encoding="utf-8")
        rc, out, err = self.run_ceiling(config(lint_a=20))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("(1 over the ceiling, all listed)", out)

    def test_listed_body_that_grew_is_red(self) -> None:
        """A body past its entry's number is red; the debt only shrinks."""
        self.debt.write_text(self.entry("lint:a", 20), encoding="utf-8")
        rc, _, err = self.run_ceiling(config(lint_a=21))
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{self.cfg}: task 'lint:a' holds 21 lines, more than its debt "
            f"entry's 20 ({self.debt}:1) — the debt only shrinks",
            err,
        )

    def test_stale_entries_are_red(self) -> None:
        """An entry whose body fits, or is gone, is red by its line."""
        self.debt.write_text(
            self.entry("lint:a", 20) + self.entry("lint:gone", 30), encoding="utf-8"
        )
        rc, _, err = self.run_ceiling(config(lint_a=15))
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{self.debt}:1: task 'lint:a' in {self.cfg} now fits the ceiling "
            "(15 lines) — remove the entry (mise run fix:task-shell)",
            err,
        )
        self.assertIn(
            f"{self.debt}:2: task 'lint:gone' in {self.cfg} names no task body "
            "— remove the entry (mise run fix:task-shell)",
            err,
        )

    def test_shrunk_entry_is_noted_and_lowered(self) -> None:
        """A body that shrank is green; fix writes its new number."""
        self.debt.write_text("# head\n" + self.entry("lint:a", 20), encoding="utf-8")
        rc, out, _ = self.run_ceiling(config(lint_a=18))
        self.assertEqual(rc, 0)
        self.assertIn(f"{self.debt}:2: the body now holds 18 lines", out)
        rc, out, _ = self.run_ceiling(config(lint_a=18), write=True)
        self.assertEqual(rc, 0)
        self.assertIn("1 entry(ies) lowered, 0 removed", out)
        self.assertEqual(
            self.debt.read_text(encoding="utf-8"),
            "# head\n" + self.entry("lint:a", 18),
        )

    def test_fix_removes_stale_and_never_adds_to_a_file(self) -> None:
        """With a debt file, fix settles it down and leaves new bodies red."""
        self.debt.write_text(
            self.entry("lint:a", 20) + self.entry("lint:gone", 30), encoding="utf-8"
        )
        rc, out, _ = self.run_ceiling(config(lint_a=20, lint_b=17), write=True)
        self.assertEqual(rc, 0)
        self.assertIn("0 entry(ies) lowered, 1 removed", out)
        self.assertEqual(
            self.debt.read_text(encoding="utf-8"), self.entry("lint:a", 20)
        )
        rc, _, err = self.run_ceiling(config(lint_a=20, lint_b=17))
        self.assertEqual(rc, 1)
        self.assertIn("task 'lint:b' holds a body of 17 lines", err)

    def test_fix_generates_an_absent_file(self) -> None:
        """With no debt file, fix writes every body over the ceiling."""
        rc, out, _ = self.run_ceiling(config(lint_a=16, lint_b=3, fix_c=40), write=True)
        self.assertEqual(rc, 0)
        self.assertIn(f"generated {self.debt} with 2 entry(ies)", out)
        text = self.debt.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(es.TASK_HEADER))
        self.assertEqual(
            text.removeprefix(es.TASK_HEADER),
            self.entry("lint:a", 16) + self.entry("fix:c", 40),
        )
        rc, out, err = self.run_ceiling(config(lint_a=16, lint_b=3, fix_c=40))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("(2 over the ceiling, all listed)", out)

    def test_fix_with_nothing_over_writes_no_file(self) -> None:
        """A repository with nothing over the ceiling gets no debt file."""
        rc, out, _ = self.run_ceiling(config(lint_a=3), write=True)
        self.assertEqual(rc, 0)
        self.assertIn("every task body fits; no debt file needed", out)
        self.assertFalse(self.debt.exists())

    def test_umbrella_is_1544(self) -> None:
        """A task note naming #1544 must say `unassigned`; #1352 is any issue."""
        self.debt.write_text(
            self.entry("lint:a", 20, "# canon helper: #1544")
            + self.entry("lint:b", 20, "# #1352"),
            encoding="utf-8",
        )
        rc, _, err = self.run_ceiling(config(lint_a=20, lint_b=20))
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{self.debt}:1: a note naming #1544 says `unassigned`: name the issue "
            "that removes the body, or mark it unassigned",
            err,
        )
        self.assertIn("task 'lint:a' holds a body of 20 lines", err)
        self.assertNotIn("lint:b", err)

    def test_listed_twice_names_the_task(self) -> None:
        """A duplicate entry is named as a task, not a step."""
        self.debt.write_text(self.entry("lint:a", 20) * 2, encoding="utf-8")
        _, errors = es.read_debt(self.debt, es.TASKS)
        self.assertEqual(
            errors, [f"{self.debt}:2: task 'lint:a' in {self.cfg} is listed twice"]
        )

    def test_non_bodies_are_not_weighed(self) -> None:
        """A `file =` task, a non-table task and a blank body carry nothing."""
        self.cfg.write_text(
            '[tasks]\nalias = "x"\n[tasks.f]\nfile = "a.sh"\n'
            '[tasks.blank]\nrun = "  "\n[tasks.n]\nrun = [1, "echo"]\n',
            encoding="utf-8",
        )
        self.assertEqual(list(es.task_bodies(self.cfg)), [("n", 1)])


class TaskShellTask(unittest.TestCase):
    """lint:task-shell and fix:task-shell, run as mise runs them.

    The first row fails before #1544: on main the belt defines neither
    task, so decoding the body raises KeyError.
    """

    def repo(self) -> Path:
        """Initialise an empty git repository.

        Returns:
            Its root.

        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        subprocess.run([GIT, "init", "-q", str(root)], check=True, env=CLEAN_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]
        return root

    @staticmethod
    def track(root: Path, text: str) -> None:
        """Write and stage a root mise.toml."""
        (root / "mise.toml").write_text(text, encoding="utf-8")
        subprocess.run([GIT, "-C", str(root), "add", "."], check=True, env=CLEAN_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]

    def test_tracked_long_body_is_red_then_generated(self) -> None:
        """The lint reaches the helper; fix generates; the lint goes green."""
        root = self.repo()
        self.track(root, config(lint_a=16))
        r = run_task(root, "lint:task-shell")
        self.assertEqual(r.returncode, 1)
        self.assertIn(
            "mise.toml: task 'lint:a' holds a body of 16 lines, over the 15-line "
            "ceiling, and security/task-shell-debt.txt does not list it",
            r.stderr,
        )
        r = run_task(root, "fix:task-shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(
            "generated security/task-shell-debt.txt with 1 entry(ies)", r.stdout
        )
        r = run_task(root, "lint:task-shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(1 over the ceiling, all listed)", r.stdout)

    def test_no_mise_config_skips_clean(self) -> None:
        """A tree tracking no mise config skips both tasks by name."""
        root = self.repo()
        for name, message in [
            ("lint:task-shell", "no mise config tracked, skipped"),
            ("fix:task-shell", "no mise config tracked, nothing to fix"),
        ]:
            with self.subTest(task=name):
                r = run_task(root, name)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"{name}: {message}", r.stdout)

    def test_untracked_config_is_not_read(self) -> None:
        """Tracked files only: an untracked long body leaves the tree skipped."""
        root = self.repo()
        (root / "mise.toml").write_text(config(lint_a=16), encoding="utf-8")
        r = run_task(root, "lint:task-shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped", r.stdout)


class Cli(unittest.TestCase):
    """`--ceiling` and `--task-ceiling` on the command line exit by the verdict.

    `main(argv)` is called in the test process (#1697); one row starts
    the helper as a process.
    """

    def test_exit_status(self) -> None:
        """Red is exit 1 with the finding on stderr; green is exit 0."""
        with TemporaryDirectory() as tmp:
            t = Tree(Path(tmp), workflow(step("Big", 16)), None)
            argv = ["--ceiling", str(t.debt), str(t.wf)]
            red = hermetic.call_main(lambda: es.main(argv))
            t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
            green = hermetic.call_main(lambda: es.main(argv))
        self.assertEqual(red.returncode, 1)
        self.assertIn("step 'Big' holds a run: block of 16 lines", red.stderr)
        self.assertEqual(green.returncode, 0, green.stderr)

    def test_task_ceiling_exit_status(self) -> None:
        """Started as a process: `--task-ceiling` red, then green when listed.

        The script contract (#1697): the one row that starts the helper.
        """
        with TemporaryDirectory() as tmp:
            cfg, debt = Path(tmp) / "mise.toml", Path(tmp) / "debt.txt"
            cfg.write_text(config(lint_a=16), encoding="utf-8")
            argv = [
                sys.executable,
                str(_HERE / "embedded_shell.py"),
                "--task-ceiling",
                str(debt),
                str(cfg),
            ]
            red = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                argv, capture_output=True, text=True, check=False
            )
            debt.write_text(
                f"{cfg}\tlint:a\t16\t# #1544 unassigned\n", encoding="utf-8"
            )
            green = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                argv, capture_output=True, text=True, check=False
            )
        self.assertEqual(red.returncode, 1)
        self.assertIn("task 'lint:a' holds a body of 16 lines", red.stderr)
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()

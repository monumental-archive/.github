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
"""

import io
import os
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import embedded_shell as es
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:shell-embedded"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


_HERE = support.MISE
# An empty result, named so the expected list carries its type.
NOTHING: list[object] = []

ISSUE = "\t# canon helper: #1386"
GIT = support.tool("git")
BASH = support.tool("bash")
# A scratch repository must not inherit the caller's git: under a hook,
# GIT_DIR and GIT_INDEX_FILE point every `git` at the repository being
# pushed, and `git ls-files` would list the canon's own workflows.
CLEAN_ENV = {
    **support.env(),
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


class Ceiling:
    """The Done-when rows of #1352, one guard branch each."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def tree(self, text: str, debt: str | None = None) -> Tree:
        """Build a scratch tree that lives as long as the test.

        Returns:
            The tree.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        return Tree(Path(tmp), text, debt)


@fixture
def ceiling_state(tmp_path: Path) -> Ceiling:
    """Give each row its own Ceiling."""
    return Ceiling(tmp_path)


def test_at_the_ceiling_passes(ceiling_state: Ceiling) -> None:
    """Fifteen counted lines is allowed with no entry."""
    rc, out, err = ceiling_state.tree(workflow(step("Fits", 15))).run()
    assert (rc, err) == (0, "")
    assert "every run: block fits 15 lines or its debt entry" in out


def test_over_and_unlisted_is_red_by_file_and_step(ceiling_state: Ceiling) -> None:
    """Sixteen lines with no entry names the file, the step and the count."""
    t = ceiling_state.tree(workflow(step("Big", 16)))
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"{t.wf}:8: step 'Big' holds a run: block of 16 lines, over the "
        f"15-line ceiling, and {t.debt} does not list it"
    ) in err


def test_listed_and_equal_passes(ceiling_state: Ceiling) -> None:
    """An entry naming the block at its count covers it."""
    t = ceiling_state.tree(workflow(step("Big", 16)))
    t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
    rc, out, err = t.run()
    assert (rc, err) == (0, "")
    assert "lowers the entry" not in out


def test_listed_and_grown_is_red(ceiling_state: Ceiling) -> None:
    """A block past its entry's number is red, naming both numbers."""
    t = ceiling_state.tree(workflow(step("Big", 17)))
    t.debt.write_text("# header\n" + t.entry("Big", 16), encoding="utf-8")
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"step 'Big' holds 17 lines, more than its debt entry's 16 "
        f"({t.debt}:2) — the debt only shrinks"
    ) in err


def test_shrunk_but_over_passes_and_fix_lowers_it(ceiling_state: Ceiling) -> None:
    """A shrunk block passes; fix writes the new number, nothing else."""
    t = ceiling_state.tree(workflow(step("Big", 17), step("Other", 20)))
    before = "# keep me\n" + t.entry("Big", 30) + t.entry("Other", 20)
    t.debt.write_text(before, encoding="utf-8")
    rc, out, err = t.run()
    assert (rc, err) == (0, "")
    assert (
        f"{t.debt}:2: the block now holds 17 lines — "
        "mise run fix:workflow-shell lowers the entry"
    ) in out
    rc, out, _ = t.run(write=True)
    assert rc == 0
    assert "1 entry(ies) lowered, 0 removed" in out
    assert t.debt.read_text(encoding="utf-8") == "# keep me\n" + t.entry(
        "Big", 17
    ) + t.entry("Other", 20)


def test_entry_whose_block_fits_is_stale_and_fix_removes_it(
    ceiling_state: Ceiling,
) -> None:
    """An entry for a block now at the ceiling is red; fix drops it."""
    t = ceiling_state.tree(workflow(step("Big", 15)))
    t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"{t.debt}:1: step 'Big' in {t.wf} now fits the ceiling (15 lines) "
        "— remove the entry (mise run fix:workflow-shell)"
    ) in err
    _, out, _ = t.run(write=True)
    assert "0 entry(ies) lowered, 1 removed" in out
    assert t.debt.read_text(encoding="utf-8") == "\n"
    assert t.run()[0] == 0


def test_entry_naming_nothing_is_stale_and_fix_removes_it(
    ceiling_state: Ceiling,
) -> None:
    """An entry for a step that does not exist is red; fix drops it."""
    t = ceiling_state.tree(workflow(step("Big", 16)))
    t.debt.write_text(t.entry("Big", 16) + t.entry("Gone", 40), encoding="utf-8")
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"{t.debt}:2: step 'Gone' in {t.wf} names no run: block "
        "— remove the entry (mise run fix:workflow-shell)"
    ) in err
    t.run(write=True)
    assert t.debt.read_text(encoding="utf-8") == t.entry("Big", 16)


def test_unnamed_oversized_step_is_red(ceiling_state: Ceiling) -> None:
    """An oversized block in a step with no name cannot be keyed."""
    t = ceiling_state.tree(workflow(step(None, 16)))
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"{t.wf}:7: a run: block of 16 lines, over the 15-line ceiling, in a "
        "step with no name — the debt file keys on the step name"
    ) in err


def test_duplicated_step_name_is_red_even_when_listed(ceiling_state: Ceiling) -> None:
    """Two blocks sharing a name make the key ambiguous."""
    t = ceiling_state.tree(workflow(step("Twin", 16), step("Twin", 3)))
    t.debt.write_text(t.entry("Twin", 16), encoding="utf-8")
    rc, _, err = t.run()
    assert rc == 1
    assert (
        "step 'Twin' holds a run: block of 16 lines, over the 15-line "
        "ceiling, and 2 run: blocks here share that name "
        "— the debt file keys on the step name"
    ) in err


def test_fix_never_adds_an_entry(ceiling_state: Ceiling) -> None:
    """An unlisted block stays red after fix, the file untouched."""
    t = ceiling_state.tree(workflow(step("Big", 16)))
    t.debt.write_text("# only a header\n", encoding="utf-8")
    rc, out, _ = t.run(write=True)
    assert rc == 0
    assert "0 entry(ies) lowered, 0 removed; it never adds one" in out
    assert t.debt.read_text(encoding="utf-8") == "# only a header\n"
    assert t.run()[0] == 1


def test_no_debt_file_is_an_empty_debt(ceiling_state: Ceiling) -> None:
    """A repository without the file is held to the bare ceiling."""
    t = ceiling_state.tree(workflow(step("Big", 16)))
    rc, _, err = t.run()
    assert rc == 1
    assert "does not list it" in err
    assert not t.debt.exists()
    assert t.run(write=True)[0] == 0
    assert not t.debt.exists()


def folded(name: str, lines: int, header: str = ">-") -> str:
    """Build a step whose `run:` is a folded block of `lines` lines.

    Returns:
        The step's text at zero indent.

    """
    body = "".join(f"    word{i}\n" for i in range(lines))
    return f"- name: {name}\n  run: {header}\n{body}"


WHAT_SHELLCHECK_READS_AND_HOW_ROWS = [
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


class Headers:
    """Every block-scalar header is counted; shellcheck reads `|` and `|-`."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def tree(self, text: str) -> Tree:
        """Build a scratch tree that lives as long as the test.

        Returns:
            The tree.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        return Tree(Path(tmp), text, None)


@fixture
def headers(tmp_path: Path) -> Headers:
    """Give each row its own Headers."""
    return Headers(tmp_path)


def test_folded_over_the_ceiling_and_unlisted_is_red(headers: Headers) -> None:
    """A `>-` block is no opt-out: sixteen lines is red by step."""
    t = headers.tree(workflow(folded("Fold", 16)))
    rc, _, err = t.run()
    assert rc == 1
    assert (
        f"{t.wf}:8: step 'Fold' holds a run: block of 16 lines, over the "
        f"15-line ceiling, and {t.debt} does not list it"
    ) in err


def test_folded_within_the_ceiling_passes(headers: Headers) -> None:
    """A folded block at fifteen lines passes like a literal one."""
    rc, out, err = headers.tree(workflow(folded("Fold", 15))).run()
    assert (rc, err) == (0, "")
    assert "every run: block fits 15 lines or its debt entry" in out


@pytest.mark.usefixtures("headers")
def test_every_header_is_counted() -> None:
    """Each indicator form, and a trailing comment, reaches the ceiling."""
    for header in ["|", "|-", "|+", "|2", "|2-", "|-2", ">", ">-", ">+", ">2"]:
        style = es.LITERAL if header.startswith("|") else es.FOLDED
        for comment in ["", " # zizmor: ignore[github-env]"]:
            [run] = read_runs(workflow(folded("H", 16, header + comment)))
            assert (run.style, es.weight(run.source)) == (style, 16)


@pytest.mark.usefixtures("headers")
@pytest.mark.parametrize(("header", "folds"), WHAT_SHELLCHECK_READS_AND_HOW_ROWS)
def test_what_shellcheck_reads_and_how(header: str, folds: list[object]) -> None:
    """Every literal header is read as written, every folded one folded.

    A comment after the indicator changes nothing about either.
    """
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.yml"
        p.write_text(workflow(folded("H", 3, header)), encoding="utf-8")
        read = [r.style == es.FOLDED for r in es.linted(p)]
        assert read == folds
        assert es.weight(next(es.runs(p)).source) == 3


@pytest.mark.usefixtures("headers")
def test_mint_doi_folded_block_is_seen() -> None:
    """The canon's one `>-` block is read and counted (3 lines)."""
    path = _HERE.parent / ".github" / "workflows" / "mint-doi.yml"
    found = [r for r in es.runs(path) if r.style == es.FOLDED]
    weighed = [(r.name, es.weight(r.source)) for r in found]
    want: list[tuple[str | None, int]] = [
        ("Deposit, then publish — the DOI names the released bytes", 3)
    ]
    assert weighed == want


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


A_FOLDED_BLOCK_IS_WEIGHED_BY_ITS_LINES_ROWS = [
    ("a\nb", 2),
    ("a\n\nb", 2),
    ("a\n  b\nc", 3),
    ("# c\na\nb", 2),
]


class Folded:
    """#1396: shellcheck reads a folded block as bash receives it."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def check(self, text: str, *, fmt: bool = True) -> tuple[Path, list[str]]:
        """Run shfmt and shellcheck over one action text.

        Returns:
            The file's path and the findings.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        path = Path(tmp) / "action.yml"
        path.write_text(text, encoding="utf-8")
        return path, es.check(path, fmt=fmt, lint=True)


@fixture
def folded_state(tmp_path: Path) -> Folded:
    """Give each row its own Folded."""
    return Folded(tmp_path)


def test_planted_finding_in_a_folded_composite_block(folded_state: Folded) -> None:
    """A finding inside `run: >-` in an action is reported on the block's line.

    The test that fails before #1396: the reader skipped folded blocks
    and this returned no finding at all.
    """
    path, found = folded_state.check(action(">-", "rm -rf\n${UNQUOTED}"))
    assert len(found) == 1, found
    assert found[0].startswith(f"{path}:8:"), found[0]
    assert "SC2086" in found[0]
    assert f"[{es.FOLDED_NOTE}]" in found[0]


def test_the_note_is_said_once_per_block(folded_state: Folded) -> None:
    """Two findings in one folded block: the note rides on the first."""
    _, found = folded_state.check(action(">", "echo ${A}\n\necho ${B}"))
    assert len(found) == 2, found
    noted = [es.FOLDED_NOTE in f for f in found]
    first_only: list[bool] = [True, False]
    assert noted == first_only
    assert all(":8:" in f for f in found), found


def test_folding_is_what_shellcheck_judges(folded_state: Folded) -> None:
    """A quoted string split across lines is one word once folded."""
    _, found = folded_state.check(action(">-", 'printf "%s\\n" "a\nb"'))
    assert found == NOTHING


def test_a_clean_folded_block_passes_and_shfmt_does_not_read_it(
    folded_state: Folded,
) -> None:
    """Text shfmt would reformat is not a finding in a folded block."""
    _, found = folded_state.check(action(">", "if true;then echo x;fi"))
    assert found == NOTHING
    _, literal = folded_state.check(action("|", "if true;then echo x;fi"))
    assert any("not shfmt-formatted" in f for f in literal), literal


@pytest.mark.parametrize(
    "header", ["|+", "|2", "|2-", "| # zizmor: ignore[github-env]"]
)
def test_literal_headers_get_both_passes(folded_state: Folded, header: object) -> None:
    """`|+`, an indicator and a commented header get shfmt and shellcheck."""
    path, found = folded_state.check(action(header, "if true;then echo ${X};fi"))
    assert f"{path}:8: run: block is not shfmt-formatted" in found
    assert any("SC2086" in f for f in found), found


def test_commented_header_finding_lands_on_its_line(folded_state: Folded) -> None:
    """A literal block under `run: | # ...` reports on the finding's own line."""
    path, found = folded_state.check(
        action("| # zizmor: ignore[github-env]", "echo ok\nrm -rf ${GONE}"),
    )
    assert len(found) == 1, found
    assert found[0].startswith(f"{path}:9:"), found[0]
    assert "SC2086" in found[0]
    assert es.FOLDED_NOTE not in found[0]


@pytest.mark.usefixtures("folded_state")
def test_rewrite_keeps_a_header_comment() -> None:
    """The header line, comment included, survives the shfmt rewrite."""
    n, out = rewrite_text(action("| # keep", "if true;then echo x;fi"))
    assert n == 1
    assert out == action("| # keep", "if true; then echo x; fi")


@pytest.mark.usefixtures("folded_state")
@pytest.mark.parametrize(("body", "lines"), A_FOLDED_BLOCK_IS_WEIGHED_BY_ITS_LINES_ROWS)
def test_a_folded_block_is_weighed_by_its_lines(body: str, lines: int) -> None:
    """Folding joins lines; the ceiling counts the ones the file holds."""
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "action.yml"
        p.write_text(action(">", body) + "    - run: echo b\n", "utf-8")
        found = list(es.runs(p))
        assert es.weight(found[0].source) == lines
        assert found[1].source == "echo b\n"


class Parser:
    """#1846: every `run:` value is read, however the YAML spells it.

    The first four rows fail before #1846: the hand reader matched block
    headers with a line regex, so an anchored header, a single-line
    value, a flow mapping and a quoted key each gave no block at all.
    """

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def check(self, name: str, text: str) -> tuple[Path, list[str]]:
        """Run shfmt and shellcheck over one file's text.

        Returns:
            The file's path and the findings.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        path = Path(tmp) / name
        path.write_text(text, encoding="utf-8")
        return path, es.check(path, fmt=True, lint=True)


@fixture
def parser(tmp_path: Path) -> Parser:
    """Give each row its own Parser."""
    return Parser(tmp_path)


def test_an_anchored_literal_block_is_linted_at_its_line(parser: Parser) -> None:
    """`run: &x |` is a block like any other: SC2086 on its body line."""
    path, found = parser.check("action.yml", action("&x |", "rm -rf ${GONE}"))
    assert len(found) == 1, found
    assert found[0].startswith(f"{path}:8:"), found[0]
    assert "SC2086" in found[0]


def test_a_single_line_value_in_an_action_is_linted_at_its_line(parser: Parser) -> None:
    """A composite action's `run: rm -rf ${GONE}` is a finding on line 7.

    actionlint does not read an action, so nothing else checks it.
    """
    path, found = parser.check(
        "action.yml",
        "name: a\nruns:\n  using: composite\n  steps:\n"
        "    - name: Act\n      shell: bash\n      run: rm -rf ${GONE}\n",
    )
    assert len(found) == 1, found
    assert found[0].startswith(f"{path}:7:"), found[0]
    assert "SC2086" in found[0]


def test_a_flow_mapping_and_a_quoted_key_are_read(parser: Parser) -> None:
    """`- {run: ...}` and `- "run": ...` are each a value, at its line."""
    path, found = parser.check(
        "w.yml",
        workflow('- {name: F, run: "rm -rf ${FLOW}"}\n- "run": rm -rf ${QUOTED}\n'),
    )
    at = [(f.split(":")[1], "SC2086" in f) for f in found]
    want: list[tuple[str, bool]] = [("6", True), ("7", True)]
    assert at == want, found
    assert all(f.startswith(f"{path}:") for f in found), found


def test_an_alias_adds_no_second_finding(parser: Parser) -> None:
    """`run: *x` is its anchor's text, linted once where the anchor is."""
    path, found = parser.check(
        "w.yml",
        workflow("- name: A\n  run: &x |\n    rm -rf ${GONE}\n- name: B\n  run: *x\n"),
    )
    assert len(found) == 1, found
    assert found[0].startswith(f"{path}:8:"), found[0]
    [anchor, alias] = list(es.runs(path))
    assert (anchor.alias, alias.alias) == (False, True)
    assert alias.text == anchor.text


def test_an_alias_is_not_weighed_again(parser: Parser) -> None:
    """A 16-line anchor, listed, aliased in an unlisted step: one entry."""
    tmp = tempfile.mkdtemp(dir=parser.tmp)
    t = Tree(Path(tmp), workflow(step("Big", 16)), None)
    text = t.wf.read_text(encoding="utf-8").replace("run: |", "run: &x |")
    t.wf.write_text(text + "      - name: Again\n        run: *x\n", "utf-8")
    t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
    rc, _, err = t.run()
    assert (rc, err) == (0, "")


def test_a_single_line_format_finding_carries_shfmt_text(parser: Parser) -> None:
    """Fixed by hand: the file, the line and what shfmt would write."""
    text = workflow("- run: if true;then echo x;fi\n")
    path, found = parser.check("w.yml", text)
    want: list[str] = [
        (
            f"{path}:6: run: value is not shfmt-formatted, and only a literal "
            "block is rewritten — shfmt writes it as: if true; then echo x; fi"
        )
    ]
    assert found == want
    assert es.rewrite(path) == 0
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.usefixtures("parser")
def test_a_file_yq_cannot_parse_is_refused_by_name(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Not YAML: exit 1 naming the file, never an empty clean run."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "w.yml"
        path.write_text("on: [push\n", encoding="utf-8")
        plain = es.main([str(path)])
        _out, said = capfd.readouterr()
        debt = Path(tmp) / "debt.txt"
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv("MISE_TASK_NAME", "lint:workflow-shell")
            ceiling = es.main(["--ceiling", str(debt), str(path)])
        _out, err = capfd.readouterr()
    assert plain == 1
    assert said.startswith(f"{TASK}: {path}: ")
    assert ceiling == 1
    assert err.startswith(f"lint:workflow-shell: {path}: ")


@pytest.mark.usefixtures("parser")
def test_fix_mode_rewrites_a_literal_block_byte_for_byte() -> None:
    """A commented `|2+` header with trailing blanks: only the body moves.

    The parser locates the body: from the line after the header, as
    many lines as the value has before its trailing line breaks, at
    the indent its first non-blank line carries ahead of its text.
    """
    before = (
        "name: a\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - name: Act\n"
        "      shell: bash\n"
        "      run: |2+ # keep me\n"
        "          if true;then\n"
        "            echo x;fi\n"
        "\n"
        "\n"
        "    # a comment between steps\n"
        "    - run: echo  next\n"
    )
    after = (
        "name: a\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - name: Act\n"
        "      shell: bash\n"
        "      run: |2+ # keep me\n"
        "        if true; then\n"
        "          echo x\n"
        "        fi\n"
        "\n"
        "\n"
        "    # a comment between steps\n"
        "    - run: echo  next\n"
    )
    n, out = rewrite_text(before)
    assert (n, out.encode()) == (1, after.encode())
    assert rewrite_text(after) == (0, after)


class OrgConfig:
    """The org's shellcheck settings are the ones applied (#696, #733)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path


@fixture
def org_config(tmp_path: Path) -> OrgConfig:
    """Give each row its own OrgConfig."""
    return OrgConfig(tmp_path)


def test_a_home_shellcheckrc_cannot_lower_the_org_checks(org_config: OrgConfig) -> None:
    """A machine's own rc file does not switch a belt check off.

    Without `--rcfile`, shellcheck reads `~/.shellcheckrc`, so a
    `disable=` there silently dropped a check the belt enables; the
    delivered `mise/shellcheckrc` is passed instead and nothing else
    is read.
    """
    tmp = tempfile.mkdtemp(dir=org_config.tmp)
    home = Path(tmp) / "home"
    home.mkdir()
    (home / ".shellcheckrc").write_text("disable=SC2250\n", encoding="utf-8")
    path = Path(tmp) / "wf.yml"
    path.write_text(
        workflow('- name: s\n  run: |\n    x=1\n    echo "$x"'),
        encoding="utf-8",
    )
    # The environment is built before the old one is cleared: it
    # reads the variables it keeps from this one.
    planted = support.env(home)
    with pytest.MonkeyPatch.context() as patched:
        for name in list(os.environ):
            patched.delenv(name)
        for name, value in planted.items():
            patched.setenv(name, value)
        found = es.check(path, fmt=False, lint=True)
    assert any("[SC2250]" in f for f in found), found


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


# `--write` splices through the one reader and leaves folded text alone.
def test_literal_block_is_formatted_at_its_indent() -> None:
    """A `|` block comes back shfmt-formatted under the same key."""
    n, out = rewrite_text(action("|", "if true;then echo x;fi"))
    assert n == 1
    assert out == action("|", "if true; then echo x; fi")


def test_folded_block_is_never_rewritten() -> None:
    """A folded block is never handed to shfmt's rewrite."""
    text = action(">", "if true;then echo x;fi")
    assert rewrite_text(text) == (0, text)


def test_indentation_indicator_keeps_its_content_column() -> None:
    """`|2` content sits two past the key, and the rewrite writes it there.

    The leading spaces beyond the indicator are script text, which
    shfmt drops; reading the column from the deepest line instead
    would have written the result back two columns too far right.
    """
    text = action("|2", "  if true;then echo x;fi")
    n, out = rewrite_text(text)
    assert n == 1
    assert out == action("|2", "if true; then echo x; fi")


def test_keep_chomping_keeps_trailing_blank_lines() -> None:
    """`|+` trailing blank lines are value, and survive the rewrite."""
    text = action("|+", "if true;then echo x;fi\n\n")
    n, out = rewrite_text(text)
    assert n == 1
    assert out == action("|+", "if true; then echo x; fi\n\n")


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


class TrailingBlank:
    """#1431: blank lines after a block are YAML layout, not shell."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def write(self, text: str) -> Path:
        """Write one workflow text to a scratch file.

        Returns:
            The file's path.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        path = Path(tmp) / "w.yml"
        path.write_text(text, encoding="utf-8")
        return path


@fixture
def trailing_blank(tmp_path: Path) -> TrailingBlank:
    """Give each row its own TrailingBlank."""
    return TrailingBlank(tmp_path)


def test_blank_line_after_a_literal_block_is_not_a_finding(
    trailing_blank: TrailingBlank,
) -> None:
    """A formatted block followed by a blank line is clean.

    The test that fails before #1431: the reader took the blank line
    as body, shfmt reported it, and lines 6 and 14 were red.
    """
    path = trailing_blank.write(blank_after("echo one"))
    assert es.check(path, fmt=True, lint=True) == NOTHING


def test_rewrite_keeps_blank_lines_after_a_block(trailing_blank: TrailingBlank) -> None:
    """`--write` on clean blocks rewrites none and changes no byte.

    The test that fails before #1431: the fixer reformatted two
    blocks by deleting the blank line after each.
    """
    text = blank_after("echo one")
    path = trailing_blank.write(text)
    assert es.rewrite(path) == 0
    assert path.read_text(encoding="utf-8") == text


def test_unformatted_shell_before_a_blank_line_stays_red(
    trailing_blank: TrailingBlank,
) -> None:
    """The shell itself still reds, and the fix keeps the blank line."""
    path = trailing_blank.write(blank_after("if true;then echo x;fi"))
    found = es.check(path, fmt=True, lint=True)
    want: list[str] = [f"{path}:6: run: block is not shfmt-formatted"]
    assert found == want
    assert es.rewrite(path) == 1
    assert path.read_text(encoding="utf-8") == blank_after("if true; then echo x; fi")


MALFORMED_LINES_ROWS = [
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


# Lines of the debt file that are not entries are red, by line.
@pytest.mark.parametrize(("line", "message"), MALFORMED_LINES_ROWS)
def test_malformed_lines(line: str, message: str) -> None:
    """Field count, count and note are each refused with their reason."""
    with TemporaryDirectory() as tmp:
        debt = Path(tmp) / "debt.txt"
        debt.write_text("# header\n\n" + line, encoding="utf-8")
        entries, errors = es.read_debt(debt)
        assert entries == NOTHING
        assert len(errors) == 1
        assert errors[0].startswith(f"{debt}:3: "), errors[0]
        assert message in errors[0]


def test_umbrella_with_unassigned_is_an_entry() -> None:
    """#1352 with `unassigned` reads; a longer number is another issue."""
    with TemporaryDirectory() as tmp:
        debt = Path(tmp) / "debt.txt"
        debt.write_text(
            "w.yml\tA\t16\t# stele: #1352 unassigned\n"
            "w.yml\tB\t16\t# canon helper: #13520\n",
            encoding="utf-8",
        )
        entries, errors = es.read_debt(debt)
    assert errors == NOTHING
    steps = [e.step for e in entries]
    both: list[str] = ["A", "B"]
    assert steps == both


def test_listed_twice() -> None:
    """The second copy of an entry is named."""
    with TemporaryDirectory() as tmp:
        debt = Path(tmp) / "debt.txt"
        debt.write_text("w.yml\tBig\t16\t# #1\n" * 2, encoding="utf-8")
        entries, errors = es.read_debt(debt)
    assert len(entries) == 1
    want: list[str] = [f"{debt}:2: step 'Big' in w.yml is listed twice"]
    assert errors == want


def test_malformed_line_reds_the_lint() -> None:
    """A bad line reaches the verdict, not just the parser."""
    with TemporaryDirectory() as tmp:
        t = Tree(Path(tmp), workflow(step("Small", 2)), "w.yml\tBig\n")
        rc, _, err = t.run()
    assert rc == 1
    assert "this line has 2 field(s)" in err


def read_runs(text: str) -> list[es.Run]:
    """Read a workflow text through the shared reader.

    Returns:
        Its Run records.

    """
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.yml"
        p.write_text(text, encoding="utf-8")
        return list(es.runs(p))


# The weight and the step name, as the one reader sees them.
def test_blank_and_comment_lines_do_not_count_heredocs_do() -> None:
    """Comments and blanks are free; a heredoc body is lines like any."""
    text = workflow(
        "- name: S\n  run: |\n    # why\n\n    cat > f <<'EOF'\n"
        "    a: 1\n    b: 2\n    EOF\n      # indented comment\n    echo done\n",
    )
    [run] = read_runs(text)
    assert es.weight(run.source) == 5


def test_step_names() -> None:
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
        assert read_runs(workflow(text))[0].name == name
    second = read_runs(workflow(rows[-1][0]))[1]
    assert second.name is None


def test_composite_action_steps() -> None:
    """A composite action's steps are read at their own indent."""
    text = (
        "runs:\n  using: composite\n  steps:\n"
        "    - name: Act\n      shell: bash\n      run: |\n        x\n"
    )
    [run] = read_runs(text)
    assert (run.name, run.line, es.place(run, 0)) == ("Act", 6, 7)


def test_the_shell_key_anywhere_in_the_step() -> None:
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
        assert read_runs(workflow(text))[0].shell == shell
    second = read_runs(workflow(rows[-1][0]))[1]
    assert second.shell == "bash"


def test_every_interpreter_is_held_but_shellcheck_reads_bash() -> None:
    """The ceiling counts a python block; shellcheck's view skips it."""
    text = workflow("- name: Py\n  shell: python\n  run: |\n    print(1)\n")
    [run] = read_runs(text)
    assert run.shell == "python"
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.yml"
        p.write_text(text, encoding="utf-8")
        read = list(es.linted(p))
        assert read == NOTHING


def task_body(name: str) -> str:
    """Decode a task's body from the belt's own config.

    Returns:
        The bash source mise runs.

    """
    config = decoded.load_toml((_HERE / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    return decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")


def run_task(root: Path, name: str) -> subprocess.CompletedProcess[str]:
    """Run one task body under the interpreter mise uses.

    Returns:
        The finished process.

    """
    env = {**CLEAN_ENV, "ORG_BELT_DIR": str(_HERE)}
    as_mise = support.task_run(name, task_body(name))
    return subprocess.run(
        as_mise.argv,
        cwd=root,
        env=env | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


class Task:
    """The task bodies, run as mise runs them, in a scratch repository."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def repo(self) -> Path:
        """Initialise an empty git repository.

        Returns:
            Its root.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        root = Path(tmp)
        subprocess.run([GIT, "init", "-q", str(root)], check=True, env=CLEAN_ENV)
        return root


@fixture
def task_state(tmp_path: Path) -> Task:
    """Give each row its own Task."""
    return Task(tmp_path)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("lint:workflow-shell", "no workflows, actions or templates, skipped"),
        (
            "fix:workflow-shell",
            "no workflows, actions or templates, nothing to fix",
        ),
    ],
)
def test_no_workflows_skips_clean(
    task_state: Task, name: object, message: object
) -> None:
    """A tree tracking no workflow skips both tasks by name."""
    root = task_state.repo()
    r = run_task(root, name)
    assert r.returncode == 0, r.stderr
    assert f"{name}: {message}" in r.stdout


def test_tracked_workflow_is_held(task_state: Task) -> None:
    """The task reaches the helper: an oversized tracked block is red."""
    root = task_state.repo()
    wf = root / ".github" / "workflows" / "w.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(workflow(step("Big", 16)), encoding="utf-8")
    subprocess.run([GIT, "-C", str(root), "add", "."], check=True, env=CLEAN_ENV)
    r = run_task(root, "lint:workflow-shell")
    assert r.returncode == 1
    assert (
        ".github/workflows/w.yml:8: step 'Big' holds a run: block of 16 lines, "
        "over the 15-line ceiling, and security/workflow-shell-debt.txt does "
        "not list it"
    ) in r.stderr


def test_untracked_workflow_is_not_read(task_state: Task) -> None:
    """Tracked files only: an untracked workflow leaves the tree skipped."""
    root = task_state.repo()
    wf = root / ".github" / "workflows" / "w.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(workflow(step("Big", 16)), encoding="utf-8")
    r = run_task(root, "lint:workflow-shell")
    assert r.returncode == 0, r.stderr
    assert "skipped" in r.stdout


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


class Tasks:
    """The Done-when rows of #1544: task bodies under the same ceiling."""

    def __init__(self, tmp_path: Path) -> None:
        """Give each row a scratch directory for the config and the debt."""
        self.root = Path(str(tmp_path))
        self.cfg = self.root / "mise.toml"
        self.debt = self.root / "debt.txt"

    def run_ceiling(self, text: str, *, write: bool = False) -> tuple[int, str, str]:
        """Write the config and run the task ceiling over it.

        Returns:
            (exit status, stdout, stderr).

        """
        self.cfg.write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        task = "fix:task-shell" if write else "lint:task-shell"
        with (
            pytest.MonkeyPatch.context() as patched,
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            patched.setenv("MISE_TASK_NAME", task)
            patched.setenv("ORG_FINDINGS", "")
            rc = es.task_ceiling([str(self.cfg)], self.debt, write=write)
        return rc, out.getvalue(), err.getvalue()

    def entry(self, name: str, lines: int, note: str = "# #1544 unassigned") -> str:
        """Return one debt line naming this row's config.

        Returns:
            The tab-separated entry, newline-terminated.

        """
        return f"{self.cfg}\t{name}\t{lines}\t{note}\n"


@fixture
def tasks_state(tmp_path: Path) -> Tasks:
    """Give each row its own Tasks."""
    return Tasks(tmp_path)


def test_tasks_at_the_ceiling_passes(tasks_state: Tasks) -> None:
    """Fifteen counted lines, comments and blanks free, needs no entry."""
    rc, out, err = tasks_state.run_ceiling(config(lint_a=15))
    assert (rc, err) == (0, "")
    assert "every task body fits 15 lines or its debt entry (0 over" in out


def test_a_config_that_is_not_toml_is_refused_by_name(tasks_state: Tasks) -> None:
    """An undecodable config is a refusal naming it, in each mode (#1681)."""
    with pytest.raises(decoded.UndecodableError) as refused:
        decoded.load_toml("lint")
    why = str(refused.value)
    for write, label in ((False, "lint:task-shell"), (True, "fix:task-shell")):
        rc, out, err = tasks_state.run_ceiling("lint", write=write)
        assert (rc, out) == (1, "")
        assert err == f"{label}: {tasks_state.cfg}: not TOML: {why}\n"


def test_tasks_that_is_not_a_table_is_refused_by_name(tasks_state: Tasks) -> None:
    """`tasks = 1` has no bodies to weigh; it is named, not a crash."""
    rc, out, err = tasks_state.run_ceiling("tasks = 1\n")
    assert (rc, out) == (1, "")
    assert err == f"lint:task-shell: {tasks_state.cfg}: tasks is not a table\n"


def test_no_debt_file_and_no_long_body_passes(tasks_state: Tasks) -> None:
    """A repository with nothing over the ceiling carries no file."""
    rc, out, _ = tasks_state.run_ceiling(config(lint_a=3, fix_a=[2, 2]))
    assert rc == 0
    assert "task-shell: every task body fits" in out
    assert not tasks_state.debt.exists()


def test_over_the_ceiling_and_unlisted_is_red_by_name(tasks_state: Tasks) -> None:
    """Sixteen lines with no entry is red, naming the task."""
    rc, _, err = tasks_state.run_ceiling(config(lint_a=16))
    assert rc == 1
    assert (
        f"{tasks_state.cfg}: task 'lint:a' holds a body of 16 lines, over the 15-line "
        f"ceiling, and {tasks_state.debt} does not list it — move the logic out"
    ) in err
    assert "lint:task-shell: 1 finding(s)" in err


def test_list_run_is_one_body(tasks_state: Tasks) -> None:
    """A `run` list of 8 + 8 commands is a 16-line body, not two."""
    tasks_state.cfg.write_text(config(lint_a=[8, 8]), encoding="utf-8")
    bodies = list(es.task_bodies(tasks_state.cfg))
    one_body: list[tuple[str, int]] = [("lint:a", 16)]
    assert bodies == one_body
    rc, _, err = tasks_state.run_ceiling(config(lint_a=[8, 8]))
    assert rc == 1
    assert "task 'lint:a' holds a body of 16 lines" in err


def test_listed_passes(tasks_state: Tasks) -> None:
    """A body at or under its entry is green."""
    tasks_state.debt.write_text(tasks_state.entry("lint:a", 20), encoding="utf-8")
    rc, out, err = tasks_state.run_ceiling(config(lint_a=20))
    assert (rc, err) == (0, "")
    assert "(1 over the ceiling, all listed)" in out


def test_listed_body_that_grew_is_red(tasks_state: Tasks) -> None:
    """A body past its entry's number is red; the debt only shrinks."""
    tasks_state.debt.write_text(tasks_state.entry("lint:a", 20), encoding="utf-8")
    rc, _, err = tasks_state.run_ceiling(config(lint_a=21))
    assert rc == 1
    assert (
        f"{tasks_state.cfg}: task 'lint:a' holds 21 lines, more than its debt "
        f"entry's 20 ({tasks_state.debt}:1) — the debt only shrinks"
    ) in err


def test_stale_entries_are_red(tasks_state: Tasks) -> None:
    """An entry whose body fits, or is gone, is red by its line."""
    tasks_state.debt.write_text(
        tasks_state.entry("lint:a", 20) + tasks_state.entry("lint:gone", 30),
        encoding="utf-8",
    )
    rc, _, err = tasks_state.run_ceiling(config(lint_a=15))
    assert rc == 1
    assert (
        f"{tasks_state.debt}:1: task 'lint:a' in {tasks_state.cfg} now fits the ceiling "
        "(15 lines) — remove the entry (mise run fix:task-shell)"
    ) in err
    assert (
        f"{tasks_state.debt}:2: task 'lint:gone' in {tasks_state.cfg} names no task body "
        "— remove the entry (mise run fix:task-shell)"
    ) in err


def test_shrunk_entry_is_noted_and_lowered(tasks_state: Tasks) -> None:
    """A body that shrank is green; fix writes its new number."""
    tasks_state.debt.write_text(
        "# head\n" + tasks_state.entry("lint:a", 20), encoding="utf-8"
    )
    rc, out, _ = tasks_state.run_ceiling(config(lint_a=18))
    assert rc == 0
    assert f"{tasks_state.debt}:2: the body now holds 18 lines" in out
    rc, out, _ = tasks_state.run_ceiling(config(lint_a=18), write=True)
    assert rc == 0
    assert "1 entry(ies) lowered, 0 removed" in out
    assert tasks_state.debt.read_text(
        encoding="utf-8"
    ) == "# head\n" + tasks_state.entry("lint:a", 18)


def test_fix_removes_stale_and_never_adds_to_a_file(tasks_state: Tasks) -> None:
    """With a debt file, fix settles it down and leaves new bodies red."""
    tasks_state.debt.write_text(
        tasks_state.entry("lint:a", 20) + tasks_state.entry("lint:gone", 30),
        encoding="utf-8",
    )
    rc, out, _ = tasks_state.run_ceiling(config(lint_a=20, lint_b=17), write=True)
    assert rc == 0
    assert "0 entry(ies) lowered, 1 removed" in out
    assert tasks_state.debt.read_text(encoding="utf-8") == tasks_state.entry(
        "lint:a", 20
    )
    rc, _, err = tasks_state.run_ceiling(config(lint_a=20, lint_b=17))
    assert rc == 1
    assert "task 'lint:b' holds a body of 17 lines" in err


def test_fix_generates_an_absent_file(tasks_state: Tasks) -> None:
    """With no debt file, fix writes every body over the ceiling."""
    rc, out, _ = tasks_state.run_ceiling(
        config(lint_a=16, lint_b=3, fix_c=40), write=True
    )
    assert rc == 0
    assert f"generated {tasks_state.debt} with 2 entry(ies)" in out
    text = tasks_state.debt.read_text(encoding="utf-8")
    assert text.startswith(es.TASK_HEADER)
    assert text.removeprefix(es.TASK_HEADER) == tasks_state.entry(
        "lint:a", 16
    ) + tasks_state.entry("fix:c", 40)
    rc, out, err = tasks_state.run_ceiling(config(lint_a=16, lint_b=3, fix_c=40))
    assert (rc, err) == (0, "")
    assert "(2 over the ceiling, all listed)" in out


def test_fix_with_nothing_over_writes_no_file(tasks_state: Tasks) -> None:
    """A repository with nothing over the ceiling gets no debt file."""
    rc, out, _ = tasks_state.run_ceiling(config(lint_a=3), write=True)
    assert rc == 0
    assert "every task body fits; no debt file needed" in out
    assert not tasks_state.debt.exists()


def test_umbrella_is_1544(tasks_state: Tasks) -> None:
    """A task note naming #1544 must say `unassigned`; #1352 is any issue."""
    tasks_state.debt.write_text(
        tasks_state.entry("lint:a", 20, "# canon helper: #1544")
        + tasks_state.entry("lint:b", 20, "# #1352"),
        encoding="utf-8",
    )
    rc, _, err = tasks_state.run_ceiling(config(lint_a=20, lint_b=20))
    assert rc == 1
    assert (
        f"{tasks_state.debt}:1: a note naming #1544 says `unassigned`: name the issue "
        "that removes the body, or mark it unassigned"
    ) in err
    assert "task 'lint:a' holds a body of 20 lines" in err
    assert "lint:b" not in err


def test_listed_twice_names_the_task(tasks_state: Tasks) -> None:
    """A duplicate entry is named as a task, not a step."""
    tasks_state.debt.write_text(tasks_state.entry("lint:a", 20) * 2, encoding="utf-8")
    _, errors = es.read_debt(tasks_state.debt, es.TASKS)
    want: list[str] = [
        f"{tasks_state.debt}:2: task 'lint:a' in {tasks_state.cfg} is listed twice"
    ]
    assert errors == want


def test_non_bodies_are_not_weighed(tasks_state: Tasks) -> None:
    """A `file =` task, a non-table task and a blank body carry nothing."""
    tasks_state.cfg.write_text(
        '[tasks]\nalias = "x"\n[tasks.f]\nfile = "a.sh"\n'
        '[tasks.blank]\nrun = "  "\n[tasks.n]\nrun = [1, "echo"]\n',
        encoding="utf-8",
    )
    bodies = list(es.task_bodies(tasks_state.cfg))
    one_body: list[tuple[str, int]] = [("n", 1)]
    assert bodies == one_body


class TaskShellTask:
    """lint:task-shell and fix:task-shell, run as mise runs them.

    The first row fails before #1544: on main the belt defines neither
    task, so decoding the body raises KeyError.
    """

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def repo(self) -> Path:
        """Initialise an empty git repository.

        Returns:
            Its root.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        root = Path(tmp)
        subprocess.run([GIT, "init", "-q", str(root)], check=True, env=CLEAN_ENV)
        return root


@fixture
def task_shell_task(tmp_path: Path) -> TaskShellTask:
    """Give each row its own TaskShellTask."""
    return TaskShellTask(tmp_path)


def track(root: Path, text: str) -> None:
    """Write and stage a root mise.toml."""
    (root / "mise.toml").write_text(text, encoding="utf-8")
    subprocess.run([GIT, "-C", str(root), "add", "."], check=True, env=CLEAN_ENV)


def test_tracked_long_body_is_red_then_generated(
    task_shell_task: TaskShellTask,
) -> None:
    """The lint reaches the helper; fix generates; the lint goes green."""
    root = task_shell_task.repo()
    track(root, config(lint_a=16))
    r = run_task(root, "lint:task-shell")
    assert r.returncode == 1
    assert (
        "mise.toml: task 'lint:a' holds a body of 16 lines, over the 15-line "
        "ceiling, and security/task-shell-debt.txt does not list it"
    ) in r.stderr
    r = run_task(root, "fix:task-shell")
    assert r.returncode == 0, r.stderr
    assert "generated security/task-shell-debt.txt with 1 entry(ies)" in r.stdout
    r = run_task(root, "lint:task-shell")
    assert r.returncode == 0, r.stderr
    assert "(1 over the ceiling, all listed)" in r.stdout


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("lint:task-shell", "no mise config tracked, skipped"),
        ("fix:task-shell", "no mise config tracked, nothing to fix"),
    ],
)
def test_no_mise_config_skips_clean(
    task_shell_task: TaskShellTask, name: object, message: object
) -> None:
    """A tree tracking no mise config skips both tasks by name."""
    root = task_shell_task.repo()
    r = run_task(root, name)
    assert r.returncode == 0, r.stderr
    assert f"{name}: {message}" in r.stdout


def test_untracked_config_is_not_read(task_shell_task: TaskShellTask) -> None:
    """Tracked files only: an untracked long body leaves the tree skipped."""
    root = task_shell_task.repo()
    (root / "mise.toml").write_text(config(lint_a=16), encoding="utf-8")
    r = run_task(root, "lint:task-shell")
    assert r.returncode == 0, r.stderr
    assert "skipped" in r.stdout


# `--ceiling` and `--task-ceiling` on the command line exit by the verdict.
#
# `main(argv)` is called in the test process (#1697); one row starts
# the helper as a process.
def test_exit_status(capfd: pytest.CaptureFixture[str]) -> None:
    """Red is exit 1 with the finding on stderr; green is exit 0."""
    with TemporaryDirectory() as tmp:
        t = Tree(Path(tmp), workflow(step("Big", 16)), None)
        argv = ["--ceiling", str(t.debt), str(t.wf)]
        red = es.main(argv)
        _out, said = capfd.readouterr()
        t.debt.write_text(t.entry("Big", 16), encoding="utf-8")
        green = es.main(argv)
        _out, err = capfd.readouterr()
    assert red == 1
    assert "step 'Big' holds a run: block of 16 lines" in said
    assert green == 0, err


def test_task_ceiling_exit_status(capfd: pytest.CaptureFixture[str]) -> None:
    """`--task-ceiling` red, then green when listed."""
    with TemporaryDirectory() as tmp:
        cfg, debt = Path(tmp) / "mise.toml", Path(tmp) / "debt.txt"
        cfg.write_text(config(lint_a=16), encoding="utf-8")
        argv = ["--task-ceiling", str(debt), str(cfg)]
        red = es.main(argv)
        _out, said = capfd.readouterr()
        debt.write_text(f"{cfg}\tlint:a\t16\t# #1544 unassigned\n", encoding="utf-8")
        green = es.main(argv)
        _out, err = capfd.readouterr()
    assert red == 1
    assert "task 'lint:a' holds a body of 16 lines" in said
    assert green == 0, err

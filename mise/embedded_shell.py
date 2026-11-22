#!/usr/bin/env python3
"""Lint and format-check the shell embedded in `run:` blocks (#82).

The gap this closes, measured rather than assumed:

  - shfmt cannot see inside YAML. `org_tracked | xargs -0 shfmt --find`
    returns zero workflow files, so 2584 lines of shell in `run:` blocks
    were never formatted by anything. That is where the 268-character
    lines came from.
  - actionlint delegates `run:` blocks to shellcheck, but ONLY for
    workflows. Pointed at a composite action it tries to parse it as a
    workflow and fails on a missing `jobs:` key, so 152 lines of shell in
    .github/actions/*/action.yml were checked by nothing at all.

The technique is actionlint's own (rule_shellcheck.go): extract each
block to a temporary file and run the real tool on it. Nothing moves —
which matters, because moving shell OUT of YAML would hide it from
zizmor, whose github-env and template-injection audits read `run:`
content and cannot follow a script call.

The values are read by the parser (#1846): `workflow_yaml.runs()`, on
the belt's pinned yq, so every `run:` value is read however the YAML
spells it, and this file holds no YAML grammar.

Deterministic and offline: shfmt and shellcheck both read only the bytes
handed to them.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import decoded
import findings as writer
import workflow_yaml
from typed_std import group

# How each value is linted (#1396, #1846), by the style yq reports. A
# literal block (`|`, any chomping or indentation indicator, any header
# comment) and a single-line value (plain, single- or double-quoted) are
# the script bash runs, line for line, so shfmt and shellcheck both read
# them. A folded block (`>`) has its lines joined before bash sees it:
# shellcheck reads the FOLDED text, and shfmt does not apply, because
# the file's lines are not the script's lines. Where the value sits —
# anchored, in a flow mapping, under a quoted key — is the parser's
# business, never this file's: it holds no YAML grammar.
LITERAL = "literal"
FOLDED = "folded"
FOLDED_NOTE = (
    "folded block: shellcheck read the text bash receives; shfmt does not apply"
)

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

SHFMT = ["shfmt", "-i", "2", "-bn", "-ci", "-sr", "-s"]

# The org's shellcheck settings, read from the belt directory this file is
# itself delivered in (#696) — one definition for every surface that can
# reach it, rather than a second copy of `--enable=all` living here and
# drifting from the one lint:shell uses. It also closes a smaller hole:
# these blocks are linted as temporary files, and until this file was
# passed, shellcheck was free to discover $HOME/.shellcheckrc and lower
# the level on a developer's machine while CI stayed strict.
ORG_RCFILE = Path(__file__).resolve().parent / "shellcheckrc"

# The exclusion list is actionlint's, verbatim from its rule_shellcheck.go,
# because this runs the same code through the same tool and must agree with
# it. Its reasons, preserved rather than re-derived:
#   SC1091  file not found — scripts are for the CI environment
#   SC2153  same as SC2154
#   SC2154  referenced but not assigned — a run: block legitimately reads
#           variables the step's env: supplies
#   SC2194 / SC2050 / SC2157 / SC2043
#           the word/expression is constant — an artefact of ${{ }} being
#           substituted before the shell ever sees it
# Diverging here would mean the belt reporting findings on `run:` blocks
# that lint:actions calls clean, on the same lines.
# gcc format is <file>:<line>:<col>: <level>: <message> — five fields, so a
# usable record has at least four after one split-limited partition.
GCC_FIELDS = 4

SHELLCHECK_EXCLUDE = "SC1091,SC2194,SC2050,SC2153,SC2154,SC2157,SC2043"
# A step that sources the canon tree (`. .org-canon/mise/…`, #1660). The
# runner has `.org-canon` (the canon action links it); a laptop does not,
# so shellcheck is told where it is (#1715). And SC1091, the one finding
# that would say a source was not followed, is excluded above as
# actionlint excludes it, so a source the canon does not hold is
# refused here by name.
CANON_SOURCE_RE = re.compile(r"""^\s*(?:\.|source)\s+["']?\.org-canon/([^"'\s;]+)""")


def canon_dir() -> Path:
    """Name the canon tree: ORG_CANON_DIR, or the tree this belt is in.

    Returns:
        The directory `.org-canon` stands for.

    """
    named = os.environ.get("ORG_CANON_DIR")
    return Path(named) if named else Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def canon_source_path() -> Generator[Path]:
    """Make a directory of this run's own holding `.org-canon`.

    Shellcheck resolves `. .org-canon/<path>` against `--source-path`,
    so a laptop, which has no `.org-canon`, follows the same file the
    runner does. Nothing is written in the working tree, and the
    directory goes when the run does.

    Yields:
        The directory to pass as `--source-path`.

    """
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".org-canon").symlink_to(canon_dir())
        yield Path(tmp)


def unfollowed(path: Path, run: Run, script: str) -> list[str]:
    """Refuse a step that sources a canon file the canon does not hold.

    Returns:
        One line per such source: the file, its line and the path.

    """
    canon = canon_dir()
    out: list[str] = []
    for offset, line in enumerate(script.splitlines()):
        found = CANON_SOURCE_RE.match(line)
        sourced = group(found, 1) if found else None
        if sourced and not (canon / sourced).is_file():
            out.append(
                f"{path}:{place(run, offset)}: sources .org-canon/{sourced}, which"
                f" the canon tree ({canon}) does not hold: shellcheck cannot"
                " follow it, and SC1091 is not shown (#1715)"
            )
    return out


class Run(NamedTuple):
    """One `run:` value, as the parser reads it (#1846)."""

    line: int  # 1-based line of the value: a block's header, or the value
    style: str  # yq's: literal, folded, single, double, or '' for plain
    text: str  # what bash receives, folded and chomped
    source: str  # the lines the ceiling counts: the file's, for a folded block
    shell: str  # the step's `shell:`, lowercased, or 'bash'
    name: str | None  # the step's `name:`, or None when it has none
    alias: bool  # the value is `*name`: its anchor's, read there


def depth(line: str) -> int | None:
    """Return a YAML line's indent, or None for a blank or comment line.

    Returns:
        The number of leading spaces on a line that carries content.

    """
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    return len(line) - len(line.lstrip())


def script(text: str) -> str:
    """Return a value's text as the script shfmt and shellcheck read.

    Line breaks after the text are YAML layout under every chomping
    indicator, never script to format (#1431): one ends the script.

    Returns:
        The text, ending in exactly one line break.

    """
    return text.rstrip("\n") + "\n"


def folded_lines(lines: list[str], header: int, text: str) -> list[str]:
    """Return a folded block's lines as the file holds them, for the ceiling.

    Folding joins the block's lines, so the text bash receives is no
    count of them, and a ceiling that counted it would be an opt-out by
    punctuation (#1352). The lines are found from the parser's text, not
    from indentation: from the line after the header, each line's content
    is the next piece of that text, until the text is spent.

    Returns:
        The block's lines, stripped, blank lines as ''.

    """
    rest = text.strip()
    out: list[str] = []
    for raw in lines[header:]:
        if not rest:
            break
        content = raw.strip()
        if not content:
            out.append("")
            continue
        if not rest.startswith(content):
            break
        rest = rest[len(content) :].lstrip()
        out.append(content)
    return out


def runs(path: Path) -> Iterator[Run]:
    """Yield every `run:` value in a workflow or composite action.

    This is the one reader of `run:` values: shfmt, shellcheck and the
    line ceiling all see the values it finds, and no others. It is
    `workflow_yaml.runs()` (#1846), one record per value, whatever its
    style and wherever it sits; a file yq cannot parse is a ValueError
    naming it.

    Yields:
        One Run per non-empty value, whatever its interpreter.

    Raises:
        ValueError: yq cannot read the file; the text names it.

    """
    lines = path.read_text(encoding="utf-8").split("\n")
    try:
        values = workflow_yaml.runs(path)
    except ValueError as err:
        if str(err).startswith(f"{path}:"):
            raise
        msg = f"{path}: {err}"
        raise ValueError(msg) from None
    for found in values:
        if not found.text.strip():
            continue
        if found.style == FOLDED and not found.alias:
            source = "\n".join(folded_lines(lines, found.line, found.text)) + "\n"
        else:
            source = script(found.text)
        yield Run(
            line=found.line,
            style=found.style,
            text=found.text,
            source=source,
            shell=(found.shell or "bash").strip().lower(),
            name=found.name,
            alias=found.alias,
        )


def shell_family(run: Run) -> bool:
    """Say whether a value runs under a bash-family interpreter.

    Returns:
        True for bash and sh values, the ones shfmt and shellcheck read.

    """
    return any(s in run.shell for s in ("bash", "sh"))


def linted(path: Path) -> Iterator[Run]:
    """Yield every value shellcheck reads: bash-family, and not an alias.

    An alias's text is its anchor's, linted where the anchor is, so it
    adds no second finding.

    Yields:
        One Run per value linted.

    """
    for run in runs(path):
        if shell_family(run) and not run.alias:
            yield run


def place(run: Run, offset: int) -> int:
    """Put a line of a value's script back on the file's line (#1846).

    A literal block's script line `offset` (0-based) is that many lines
    after its first body line; a folded block's lines are not the
    script's, so every line lands on its first body line; a single-line
    value has one line, the header's.

    Returns:
        The 1-based line in the file.

    """
    if run.style == LITERAL:
        return run.line + 1 + offset
    if run.style == FOLDED:
        return run.line + 1
    return run.line


def shfmt(source: str) -> str | None:
    """Format one script with the belt's shfmt flags.

    Returns:
        The formatted script, or None when shfmt cannot parse it.

    """
    r = subprocess.run(
        [*SHFMT, "-"],
        input="#!/usr/bin/env bash\n" + source,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    return r.stdout.split("\n", 1)[1]  # drop the shebang we added


def rewrite(path: Path) -> int:
    """Format every literal `run:` block in place, preserving its indentation.

    The parser locates each block (#1846): its body starts on the line
    after the header yq reports and runs for as many lines as the value
    has before its trailing line breaks, and the indent kept is the one
    the body's first non-blank line carries ahead of its text. Every
    other byte of the file is left as it was. A single-line value is
    fixed by hand, and a folded one is not formatted.

    Returns:
        The number of blocks rewritten.

    """
    lines = path.read_text(encoding="utf-8").split("\n")
    changed = 0
    # From the last block up, so a rewrite never moves a block not yet read.
    for run in reversed(list(linted(path))):
        if run.style != LITERAL:
            continue
        formatted = shfmt(run.source)
        if formatted is None or formatted == run.source:
            continue
        text = run.source.split("\n")[:-1]
        start, stop = run.line, run.line + len(text)
        first = next(i for i, x in enumerate(text) if x.strip())
        held = lines[start + first]
        if not held.endswith(text[first]):
            continue  # not where the parser says: leave it to the lint
        pad = len(held) - len(text[first])
        lines[start:stop] = [
            (" " * pad + x) if x.strip() else "" for x in formatted.split("\n")[:-1]
        ]
        changed += 1
    if changed:
        path.write_text("\n".join(lines), encoding="utf-8")
    return changed


def findings(gcc: str, path: Path, run: Run) -> list[str]:
    """Map shellcheck's gcc-format output back onto the YAML file.

    A literal block's findings land on their own lines, a single-line
    value's on its line. A folded block's lines are not the script's
    lines, so its findings land on the block's first line, and the first
    of them says why, once.

    Returns:
        One line per finding.

    """
    out: list[str] = []
    for line in gcc.splitlines():
        # gcc format is <file>:<line>:<col>: <level>: <msg>
        parts = line.split(":", 3)
        if len(parts) < GCC_FIELDS or not parts[1].isdigit():
            continue
        note = f" [{FOLDED_NOTE}]" if run.style == FOLDED and not out else ""
        # -2: the shebang this wrapper added, and line 1 is offset 0.
        at = place(run, int(parts[1]) - 2)
        out.append(f"{path}:{at}:{parts[3].strip()}{note}")
    return out


def unformatted(path: Path, run: Run) -> str | None:
    """Say how a value shfmt would change is wrong, and where.

    A literal block is rewritten by fix:workflow-shell; a single-line
    value cannot be, so its finding carries shfmt's text to copy by hand.
    A folded block is not format-checked.

    Returns:
        The finding, or None.

    """
    if run.style == FOLDED:
        return None
    formatted = shfmt(run.source)
    if formatted is None or formatted == run.source:
        return None
    if run.style == LITERAL:
        return f"{path}:{run.line + 1}: run: block is not shfmt-formatted"
    return (
        f"{path}:{run.line}: run: value is not shfmt-formatted, and only a "
        f"literal block is rewritten — shfmt writes it as: {formatted.rstrip()}"
    )


def check(
    path: Path, *, fmt: bool, lint: bool, source_path: Path | None = None
) -> list[str]:
    """Run shfmt and shellcheck over one file's `run:` values.

    `source_path` is where shellcheck finds `.org-canon` (#1715).

    Returns:
        One human-readable line per finding.

    """
    out: list[str] = []
    for run in linted(path):
        src = script(run.text)
        if lint:
            out += unfollowed(path, run, src)
        if fmt:
            finding = unformatted(path, run)
            if finding:
                out.append(finding)
        if not lint:
            continue
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".sh",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            # The shebang is what makes shellcheck read the dialect GitHub
            # runs `run:` values under by default: bash.
            tmp.write("#!/usr/bin/env bash\n" + src)
            name = tmp.name
        try:
            # ruff: ignore[start-process-with-partial-path]
            r = subprocess.run(
                [
                    "shellcheck",
                    f"--rcfile={ORG_RCFILE}",
                    *([f"--source-path={source_path}"] if source_path else []),
                    "--exclude",
                    SHELLCHECK_EXCLUDE,
                    "--format=gcc",
                    name,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            out += findings(r.stdout, path, run)
        finally:
            Path(name).unlink(missing_ok=True)
    return out


# The ceiling (#1352). A `run:` block holds at most this many lines that
# are neither blank nor a comment. Heredoc bodies count: a config file
# written inline is a file that should be tracked.
CEILING = 15
# One entry per block over the ceiling: file, step name, lines, and a
# `#` note naming the issue that will remove it — tab-separated, read
# with str.split so an empty field is an error rather than a shift.
DEBT_FIELDS = 4
ISSUE_RE = re.compile(r"#\d+")
# The ceiling's own issue. An entry no capability issue names carries it
# with the word `unassigned`; without that word a note naming the
# umbrella would look assigned while nothing is due to remove the block.
UMBRELLA_RE = re.compile(r"(?<!\d)#1352(?!\d)")
SETTLE = "mise run fix:workflow-shell"


class Kind(NamedTuple):
    """Which shell surface a debt file lists: its key, its thing, its umbrella."""

    noun: str  # what an entry's second field names: `step` or `task`
    thing: str  # what the shell is: `block` or `body`
    umbrella: re.Pattern[str]  # the ceiling's own issue, in a note
    umbrella_ref: str  # the same, as a message names it


WORKFLOW = Kind("step", "block", UMBRELLA_RE, "#1352")


def weight(source: str) -> int:
    """Count a block's lines that are neither blank nor a comment.

    Returns:
        The number the ceiling is held against.

    """
    return sum(1 for x in source.splitlines() if depth(x) is not None)


class Entry(NamedTuple):
    """One debt-file line naming a block allowed over the ceiling."""

    file: str
    step: str
    lines: int
    lineno: int


def parse_entry(raw: str, kind: Kind | None = None) -> Entry | str:
    """Read one debt line.

    Returns:
        The entry (line number 0, filled by the caller), or what is wrong.

    """
    kind = kind or WORKFLOW
    fields = raw.split("\t")
    if len(fields) != DEBT_FIELDS or not all(fields):
        return (
            f"an entry is <file>, <{kind.noun} name>, <lines>, `# <issue>`, "
            f"tab-separated — this line has {len(fields)} field(s)"
        )
    file, step, count, note = fields
    # ASCII only: str.isdigit takes `²`, which int() refuses (#1555).
    if not (count.isascii() and count.isdigit()) or int(count) <= CEILING:
        return f"'{count}' is not a line count over the {CEILING}-line ceiling"
    if not note.startswith("#") or not ISSUE_RE.search(note):
        return (
            "the note is a `#` comment naming the issue that will remove the "
            f"{kind.thing}"
        )
    if kind.umbrella.search(note) and "unassigned" not in note:
        return (
            f"a note naming {kind.umbrella_ref} says `unassigned`: name the issue "
            f"that removes the {kind.thing}, or mark it unassigned"
        )
    return Entry(file, step, int(count), 0)


def read_debt(path: Path, kind: Kind | None = None) -> tuple[list[Entry], list[str]]:
    """Parse the debt file; a missing file is an empty debt.

    Returns:
        The entries, and one finding per line that is not a valid entry.

    """
    kind = kind or WORKFLOW
    if not path.is_file():
        return [], []
    entries: list[Entry] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        entry = parse_entry(raw, kind)
        if isinstance(entry, str):
            errors.append(f"{path}:{n}: {entry}")
        elif (entry.file, entry.step) in seen:
            errors.append(
                f"{path}:{n}: {kind.noun} '{entry.step}' in {entry.file} "
                "is listed twice"
            )
        else:
            seen.add((entry.file, entry.step))
            entries.append(entry._replace(lineno=n))
    return entries, errors


class Verdict(NamedTuple):
    """What the ceiling found, and what fix:workflow-shell may change."""

    findings: list[str]  # red
    lowered: dict[int, int]  # debt line number -> the block's new count
    stale: set[int]  # debt line numbers naming no block over the ceiling


def measure(files: list[str]) -> list[tuple[str, Run, int]]:
    """Weigh every `run:` value in files, whatever its style (#1846).

    An alias is not weighed again: its text is its anchor's, held to
    the ceiling where the anchor is.

    Returns:
        (file, value, weight) for every value, in file order.

    """
    return [
        (f, run, weight(run.source))
        for f in files
        for run in runs(Path(f))
        if not run.alias
    ]


def over_block(
    block: tuple[str, Run, int],
    sharing: int,
    entry: Entry | None,
    debt: Path,
) -> str | None:
    """Judge one block over the ceiling against its debt entry.

    Returns:
        The finding, or None when the entry covers the block.

    """
    f, run, n = block
    at = f"{f}:{place(run, 0)}"
    over = f"a run: block of {n} lines, over the {CEILING}-line ceiling"
    keyed = "— the debt file keys on the step name"
    if run.name is None:
        return f"{at}: {over}, in a step with no name {keyed}"
    step = f"{at}: step '{run.name}'"
    if sharing > 1:
        return (
            f"{step} holds {over}, and {sharing} run: blocks here share that name "
            f"{keyed}"
        )
    if entry is None:
        return (
            f"{step} holds {over}, and {debt} does not list it "
            "— move the logic out (CONTRIBUTING.md)"
        )
    if n > entry.lines:
        return (
            f"{step} holds {n} lines, more than its debt entry's {entry.lines} "
            f"({debt}:{entry.lineno}) — the debt only shrinks"
        )
    return None


def judge(files: list[str], debt: Path) -> Verdict:
    """Hold every `run:` block in files to the ceiling, net of the debt.

    Returns:
        The findings, plus the entries that shrank or went stale.

    """
    entries, findings = read_debt(debt)
    listed = {(e.file, e.step): e for e in entries}
    weighed = measure(files)
    named: dict[tuple[str, str], list[int]] = {}
    for f, run, n in weighed:
        if run.name is not None:
            named.setdefault((f, run.name), []).append(n)
    for block in weighed:
        f, run, n = block
        if n <= CEILING:
            continue
        key = (f, run.name or "")
        finding = over_block(block, len(named.get(key, [])), listed.get(key), debt)
        if finding:
            findings.append(finding)
    lowered: dict[int, int] = {}
    stale: set[int] = set()
    for e in entries:
        at = f"{debt}:{e.lineno}: step '{e.step}' in {e.file}"
        top = max(named.get((e.file, e.step), [0]))
        if top > CEILING:
            if top < e.lines:
                lowered[e.lineno] = top
            continue
        stale.add(e.lineno)
        if (e.file, e.step) in named:
            findings.append(
                f"{at} now fits the ceiling ({top} lines) — remove the entry ({SETTLE})"
            )
        else:
            findings.append(f"{at} names no run: block — remove the entry ({SETTLE})")
    return Verdict(findings, lowered, stale)


def settled(text: str, verdict: Verdict) -> str:
    """Lower shrunk entries and drop stale ones. Never adds an entry.

    Returns:
        The debt file's new text.

    """
    out: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        if n in verdict.stale:
            continue
        fields = raw.split("\t")
        if n in verdict.lowered:
            fields[2] = str(verdict.lowered[n])
        out.append("\t".join(fields))
    return "\n".join(out) + "\n"


def ceiling(files: list[str], debt: Path, *, write: bool) -> int:
    """Run the ceiling in lint or fix mode.

    Returns:
        The process exit status.

    """
    try:
        verdict = judge(files, debt)
    except ValueError as err:
        writer.finding("", str(err))
        return 1
    if write:
        if verdict.lowered or verdict.stale:
            debt.write_text(
                settled(debt.read_text(encoding="utf-8"), verdict), encoding="utf-8"
            )
        print(
            f"fix:workflow-shell: {len(verdict.lowered)} entry(ies) lowered, "
            f"{len(verdict.stale)} removed; it never adds one",
        )
        return 0
    for n, count in sorted(verdict.lowered.items()):
        print(
            f"{debt}:{n}: the block now holds {count} lines — {SETTLE} lowers the entry"
        )
    for line in verdict.findings:
        writer.finding("", line)
    if verdict.findings:
        writer.finding(
            "",
            f"{len(verdict.findings)} finding(s) — a run: block holds "
            f"at most {CEILING} lines, and the debt file only shrinks",
        )
        return 1
    print(f"workflow-shell: every run: block fits {CEILING} lines or its debt entry")
    return 0


# The same ceiling on the second place shell lives (#1544): a mise task's
# `run` body. One number for shell, wherever it lives, counted by the same
# `weight`. The debt file keys on the task's name, which TOML makes unique
# within a config, so no body is ever unkeyed.
TASKS = Kind("task", "body", re.compile(r"(?<!\d)#1544(?!\d)"), "#1544")
TASK_SETTLE = "mise run fix:task-shell"
TASK_HEADER = """\
# Task shell debt (#1544): every mise task `run` body over the 15-line
# ceiling that lint:task-shell holds, as the tree had it when the ceiling
# landed. The list only shrinks. lint:task-shell is red on a body over
# the ceiling that is not listed, on a listed body that grew past its
# number, and on an entry whose body is gone or now fits;
# fix:task-shell lowers a number that shrank and removes a stale entry.
# It writes entries only when this file does not exist: new logic goes
# to a helper (CONTRIBUTING.md, "Workflow shell").
#
# One entry per line, four tab-separated fields: the config file, the
# task's name, the body's non-blank non-comment lines, and a `#` note
# naming the issue that removes it. The issue that extracts a body
# deletes its entry in the same pull request. A body no issue names yet
# carries `#1544 unassigned`.
#
# Generated from the tree by the lint's own counter
# (mise/embedded_shell.py), never typed.
"""


def task_bodies(path: Path) -> Iterator[tuple[str, int]]:
    """Weigh every task's `run` body in one mise config.

    A `run` given as a list of commands is one body: mise runs the
    commands in turn, as one task, so its lines are counted together.

    Yields:
        (task name, weight) per task carrying a body, in file order.

    Raises:
        UndecodableError: the file is not TOML, or its `tasks` is not a
            table; the text names the file.

    """
    try:
        data = decoded.load_toml(path.read_bytes())
    except decoded.UndecodableError as err:
        msg = f"{path}: not TOML: {err}"
        raise decoded.UndecodableError(msg) from None
    tasks = data.get("tasks") or {}
    if not decoded.is_object(tasks):
        msg = f"{path}: tasks is not a table"
        raise decoded.UndecodableError(msg)
    for name, task in tasks.items():
        if not decoded.is_object(task):
            continue
        run = task.get("run")
        commands = run if decoded.is_array(run) else [run]
        source = "\n".join(c for c in commands if isinstance(c, str))
        if source.strip():
            yield name, weight(source)


def judge_tasks(files: list[str], debt: Path) -> tuple[Verdict, list[Entry]]:
    """Hold every task body in files to the ceiling, net of the debt.

    Returns:
        The verdict, and the bodies over the ceiling as entries.

    """
    entries, findings = read_debt(debt, TASKS)
    listed = {(e.file, e.step): e for e in entries}
    weighed = {(f, name): n for f in files for name, n in task_bodies(Path(f))}
    over = [Entry(f, name, n, 0) for (f, name), n in weighed.items() if n > CEILING]
    for body in over:
        at = f"{body.file}: task '{body.step}'"
        e = listed.get((body.file, body.step))
        if e is None:
            findings.append(
                f"{at} holds a body of {body.lines} lines, over the {CEILING}-line "
                f"ceiling, and {debt} does not list it — move the logic out "
                "(CONTRIBUTING.md)"
            )
        elif body.lines > e.lines:
            findings.append(
                f"{at} holds {body.lines} lines, more than its debt entry's "
                f"{e.lines} ({debt}:{e.lineno}) — the debt only shrinks"
            )
    lowered: dict[int, int] = {}
    stale: set[int] = set()
    for e in entries:
        at = f"{debt}:{e.lineno}: task '{e.step}' in {e.file}"
        n = weighed.get((e.file, e.step))
        if n is not None and n > CEILING:
            if n < e.lines:
                lowered[e.lineno] = n
            continue
        stale.add(e.lineno)
        if n is None:
            findings.append(
                f"{at} names no task body — remove the entry ({TASK_SETTLE})"
            )
        else:
            findings.append(
                f"{at} now fits the ceiling ({n} lines) — remove the entry "
                f"({TASK_SETTLE})"
            )
    return Verdict(findings, lowered, stale), over


def generated(over: list[Entry]) -> str:
    """Write a new debt file listing every body over the ceiling.

    Returns:
        The file's text: the header, then one entry per body.

    """
    rows = "".join(f"{e.file}\t{e.step}\t{e.lines}\t# #1544 unassigned\n" for e in over)
    return TASK_HEADER + rows


def task_ceiling(files: list[str], debt: Path, *, write: bool) -> int:
    """Run the task-body ceiling in lint or fix mode.

    Fix mode settles an existing debt file down and never adds to it. A
    repository with no debt file gets one generated, listing every body
    over the ceiling: that is how the file is first written (the canon's
    when the ceiling landed, a consumer's in the pull request that takes
    the pin), and the lint holds it from then on.

    Returns:
        The process exit status.

    """
    try:
        verdict, over = judge_tasks(files, debt)
    except decoded.UndecodableError as err:
        writer.finding("", str(err))
        return 1
    if write:
        if not debt.is_file():
            if over:
                debt.parent.mkdir(parents=True, exist_ok=True)
                debt.write_text(generated(over), encoding="utf-8")
            print(
                f"fix:task-shell: no debt file; generated {debt} with "
                f"{len(over)} entry(ies)"
                if over
                else "fix:task-shell: every task body fits; no debt file needed"
            )
            return 0
        if verdict.lowered or verdict.stale:
            debt.write_text(
                settled(debt.read_text(encoding="utf-8"), verdict), encoding="utf-8"
            )
        print(
            f"fix:task-shell: {len(verdict.lowered)} entry(ies) lowered, "
            f"{len(verdict.stale)} removed; it never adds one to an existing file",
        )
        return 0
    for n, count in sorted(verdict.lowered.items()):
        print(
            f"{debt}:{n}: the body now holds {count} lines — {TASK_SETTLE} "
            "lowers the entry"
        )
    for line in verdict.findings:
        writer.finding("", line)
    if verdict.findings:
        writer.finding(
            "",
            f"{len(verdict.findings)} finding(s) — a task body holds "
            f"at most {CEILING} lines, and the debt file only shrinks",
        )
        return 1
    print(
        f"task-shell: every task body fits {CEILING} lines or its debt entry "
        f"({len(over)} over the ceiling, all listed)"
    )
    return 0


class Args(argparse.Namespace):
    """The arguments, typed as the parser declares them."""

    files: list[str]
    no_format: bool
    no_lint: bool
    write: bool
    ceiling: str | None
    task_ceiling: str | None


def main(argv: list[str]) -> int:
    """Entry point: check every file named on the command line.

    Returns:
        The process exit status.

    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--no-format", action="store_true")
    ap.add_argument("--no-lint", action="store_true")
    ap.add_argument("--write", action="store_true", help="format blocks in place")
    ap.add_argument(
        "--ceiling",
        metavar="DEBT",
        help="hold blocks to the line ceiling, net of this debt file (#1352)",
    )
    ap.add_argument(
        "--task-ceiling",
        metavar="DEBT",
        help="hold mise task bodies to the line ceiling, net of this debt file",
    )
    args = ap.parse_args(argv, namespace=Args())

    if args.ceiling:
        return ceiling(args.files, Path(args.ceiling), write=args.write)
    if args.task_ceiling:
        return task_ceiling(args.files, Path(args.task_ceiling), write=args.write)

    # shellcheck only WARNS on an unreadable --rcfile and then lints at its
    # own defaults, exit 0 — so a belt that did not arrive would read as a
    # clean run. Checked before anything is linted.
    if not args.write and not args.no_lint and not ORG_RCFILE.is_file():
        writer.finding("", f"the org shellcheck config is missing at {ORG_RCFILE}")
        return 1

    try:
        return rewrite_all(args.files) if args.write else lint_all(args)
    except ValueError as err:
        # A file yq cannot parse is refused by name, never read as empty.
        writer.finding("", str(err))
        return 1


def rewrite_all(files: list[str]) -> int:
    """Format every literal `run:` block in files, in place.

    Returns:
        The process exit status.

    """
    total = sum(rewrite(Path(f)) for f in files)
    print(f"embedded-shell: reformatted {total} run: block(s)")
    return 0


def lint_all(args: Args) -> int:
    """Run shfmt and shellcheck over every `run:` value in the files.

    Returns:
        The process exit status.

    """
    findings: list[str] = []
    with canon_source_path() as source_path:
        for f in args.files:
            findings += check(
                Path(f),
                fmt=not args.no_format,
                lint=not args.no_lint,
                source_path=source_path,
            )
    for line in findings:
        writer.finding("", line)
    if findings:
        writer.finding(
            "",
            f"{len(findings)} finding(s) in `run:` values — "
            "the same bar the belt applies to standalone scripts",
        )
        return 1
    print("embedded-shell: every run: value is shfmt-clean and shellcheck-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Lint and format-check the shell embedded in `run:` blocks (#82).

The gap this closes, measured rather than assumed:

  - shfmt cannot see inside YAML. `git ls-files | xargs shfmt --find`
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

Deterministic and offline: shfmt and shellcheck both read only the bytes
handed to them.
"""

# Running shfmt and shellcheck IS this file's job, so the subprocess rules
# have nothing to warn about here: every argv is a fixed tool name plus a
# temporary path this file just wrote, nothing from the repository reaches
# a command line, and the tools resolve through the belt's PATH, which
# activate_aggressive puts ahead of anything a machine happens to ship.
from __future__ import annotations

import argparse
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

# Every block-scalar header of `run:`, at any indent, list item or not:
# literal (`|`) or folded (`>`), any chomping or indentation indicator,
# and an optional trailing comment (`run: | # zizmor: ...`). The line
# ceiling counts all of them (#1352) — a header it could not read would
# be an opt-out by punctuation.
RUN_RE = re.compile(r"^(\s*)(- )?run: ([|>](?:[+-]?[1-9]?|[1-9][+-]))(\s+#.*)?\s*$")
# How each header is linted (#1396). A literal block (`|`, any chomping
# or indentation indicator) is the script bash runs, so shfmt and
# shellcheck both read it. A folded block (`>`) has its lines joined
# before bash sees it: shellcheck reads the FOLDED text, and shfmt does
# not apply, because the file's lines are not the script's lines. A
# comment after the indicator (`run: | # zizmor: ...`) changes nothing:
# the block is read by its indicator like any other.
LITERAL = "|"
FOLDED = ">"
FOLDED_NOTE = (
    "folded block: shellcheck read the text bash receives; shfmt does not apply"
)
# A `shell:` key on the same step selects the interpreter. Only bash-family
# blocks are shell at all — `shell: python` is a different language.
SHELL_RE = re.compile(r"^\s*shell:\s*([a-z0-9 {}$.-]+)\s*$")
# A step's `name:`, on its own line or as the list item's first key.
NAME_RE = re.compile(r"^(\s*)(- )?name:\s*(.*?)\s*$")

if TYPE_CHECKING:
    from collections.abc import Iterator

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


def step_shell(lines: list[str], run_idx: int, indent: int) -> str:
    """Return the `shell:` this block runs under, or 'bash' by default.

    Returns:
        The interpreter name, lowercased, with expressions left intact.

    """
    for j in range(run_idx, max(-1, run_idx - 40), -1):
        line = lines[j]
        if not line.strip():
            continue
        cur = len(line) - len(line.lstrip())
        if cur < indent and line.lstrip().startswith("- "):
            break
        m = SHELL_RE.match(line)
        if m and cur == indent:
            return m.group(1).strip().lower()
    return "bash"


class Run(NamedTuple):
    """One `run:` block scalar, literal or folded, as the file holds it."""

    line: int  # 1-based number of the block's first body line
    source: str  # the body, dedented, newline-terminated, as the file holds it
    shell: str  # the step's `shell:`, or 'bash'
    header: str  # the block indicator: `|`, `|-`, `>-`, `|2`, ...
    commented: bool  # the indicator line carries a trailing comment
    name: str | None  # the step's `name:`, or None when it has none
    start: int  # 0-based index of the header line
    end: int  # 0-based index of the first line after the body
    pad: int  # the body's content indentation in the file


def depth(line: str) -> int | None:
    """Return a YAML line's indent, or None for a blank or comment line.

    Returns:
        The number of leading spaces on a line that carries content.

    """
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    return len(line) - len(line.lstrip())


def step_start(lines: list[str], run_idx: int, key_indent: int) -> int | None:
    """Return the index of the `- ` line opening the step that holds run_idx.

    Returns:
        The line index, or None when the key sits in no list item.

    """
    for j in range(run_idx, -1, -1):
        cur = depth(lines[j])
        if cur is None:
            continue
        if cur == key_indent - 2 and lines[j].lstrip().startswith("- "):
            return j
        if cur < key_indent:
            return None
    return None


def step_name(lines: list[str], run_idx: int, key_indent: int) -> str | None:
    """Return the `name:` of the step whose `run:` key sits at run_idx.

    The step is the list item that holds the key: its `- ` sits two
    columns left of its keys, and it ends at the first line indented less
    than them. A `name:` inside the body or a `with:` map is deeper and is
    never read.

    Returns:
        The name with YAML quoting removed, or None for an unnamed step.

    """
    start = step_start(lines, run_idx, key_indent)
    if start is None:
        return None
    for j in range(start, len(lines)):
        cur = depth(lines[j])
        if j > start and cur is not None and cur < key_indent:
            break
        m = NAME_RE.match(lines[j])
        if m and len(m.group(1)) + (2 if m.group(2) else 0) == key_indent:
            return unquote(m.group(3)) or None
    return None


def unquote(value: str) -> str:
    """Strip one layer of YAML quoting, or a trailing comment if unquoted.

    Returns:
        The scalar as YAML reads it, for the names steps actually carry.

    """
    if value[:1] in {"'", '"'} and value.endswith(value[0]) and len(value) > 1:
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def content_pad(body: list[str], header: str, key_indent: int) -> int:
    """Return the column a block's content starts at.

    An indentation indicator (`|2`) fixes it relative to the key, and
    spaces beyond it are content; without one it is the least-indented
    line, as YAML's auto-detection finds it.

    Returns:
        The number of leading spaces that belong to the YAML, not the text.

    """
    least = min(len(x) - len(x.lstrip()) for x in body if x.strip())
    digits = "".join(c for c in header if c.isdigit())
    if digits and key_indent + int(digits) <= least:
        return key_indent + int(digits)
    return least


def runs_in(lines: list[str]) -> Iterator[Run]:
    """Yield every `run:` block scalar in a file's lines.

    Yields:
        One Run per non-empty block, whatever its interpreter.

    """
    i = 0
    while i < len(lines):
        m = RUN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        key_indent = indent + (2 if m.group(2) else 0)
        start = i
        i += 1
        body: list[str] = []
        while i < len(lines) and (
            not lines[i].strip() or (len(lines[i]) - len(lines[i].lstrip())) > indent
        ):
            body.append(lines[i])
            i += 1
        if not any(x.strip() for x in body):
            continue
        pad = content_pad(body, m.group(3), key_indent)
        yield Run(
            line=start + 2,
            source="\n".join(x[pad:] if x.strip() else "" for x in body) + "\n",
            shell=step_shell(lines, start, indent),
            header=m.group(3),
            commented=m.group(4) is not None,
            name=step_name(lines, start, key_indent),
            start=start,
            end=i,
            pad=pad,
        )


def runs(path: Path) -> Iterator[Run]:
    """Yield every `run:` block scalar in a workflow or composite action.

    This is the one reader of `run:` blocks: shfmt, shellcheck and the
    line ceiling all see the blocks it finds, and no others.

    Yields:
        One Run per non-empty block, whatever its interpreter.

    """
    yield from runs_in(path.read_text(encoding="utf-8").splitlines())


def fold(source: str) -> str:
    """Fold a block's dedented text the way YAML hands it to bash.

    A single line break between two text lines becomes a space; a run of
    blank lines between them becomes that many line breaks; a break next
    to a more-indented line is kept as written, blank lines included.

    Returns:
        The folded text, newline-terminated.

    """
    out: list[str] = []
    prev: str | None = None
    blanks = 0
    for line in source.split("\n")[:-1]:
        if not line:
            if prev is None:
                out.append("\n")
            else:
                blanks += 1
            continue
        if prev is None:
            out.append(line)
        elif line[0] in " \t" or prev[0] in " \t":
            out.append("\n" * (blanks + 1) + line)
        elif blanks:
            out.append("\n" * blanks + line)
        else:
            out.append(" " + line)
        prev, blanks = line, 0
    return "".join(out) + "\n"


def shell_family(run: Run) -> bool:
    """Say whether a block runs under a bash-family interpreter.

    Returns:
        True for bash and sh blocks, the ones shfmt and shellcheck read.

    """
    return any(s in run.shell for s in ("bash", "sh"))


def blocks(path: Path) -> Iterator[tuple[int, str, bool]]:
    """Yield (first_body_line_number, script, folded) per linted block.

    A literal block's script is its text; a folded block's is the text
    after folding, which is what bash runs.

    Yields:
        One tuple per bash-family block shellcheck reads.

    """
    for run in runs(path):
        if not shell_family(run):
            continue
        if run.header.startswith(FOLDED):
            yield run.line, fold(run.source), True
        else:
            yield run.line, run.source, False


def shfmt_body(run: Run, lines: list[str]) -> list[str] | None:
    """Return a literal block's body as shfmt formats it, at file indent.

    Returns:
        The new body lines, or None when shfmt fails or changes nothing.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    r = subprocess.run(
        [*SHFMT, "-"],
        input="#!/usr/bin/env bash\n" + run.source,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    body = lines[run.start + 1 : run.end]
    formatted = r.stdout.splitlines()[1:]  # drop the shebang we added
    if formatted == [x[run.pad :] if x.strip() else "" for x in body]:
        return None
    new = [(" " * run.pad + x) if x.strip() else "" for x in formatted]
    if "+" in run.header:
        # Keep chomping: trailing blank lines are part of the value.
        trailing = 0
        while trailing < len(body) and not body[-1 - trailing].strip():
            trailing += 1
        new += [""] * trailing
    return new


def rewrite(path: Path) -> int:
    """Format every literal `run:` block in place, preserving YAML indentation.

    Returns:
        The number of blocks rewritten.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    pos = 0
    changed = 0
    for run in runs_in(lines):
        if not shell_family(run) or not run.header.startswith(LITERAL):
            continue
        new = shfmt_body(run, lines)
        if new is None:
            continue
        out.extend(lines[pos : run.start + 1])
        out.extend(new)
        pos = run.end
        changed += 1
    if changed:
        out.extend(lines[pos:])
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def findings(gcc: str, path: Path, lineno: int, *, folded: bool) -> list[str]:
    """Map shellcheck's gcc-format output back onto the YAML file.

    A literal block's findings land on their own lines. A folded block's
    lines are not the script's lines, so its findings land on the block's
    first line, and the first of them says why, once.

    Returns:
        One line per finding.

    """
    out: list[str] = []
    for line in gcc.splitlines():
        # gcc format is <file>:<line>:<col>: <level>: <msg>
        parts = line.split(":", 3)
        if len(parts) < GCC_FIELDS or not parts[1].isdigit():
            continue
        if folded:
            note = "" if out else f" [{FOLDED_NOTE}]"
            out.append(f"{path}:{lineno}:{parts[3].strip()}{note}")
        else:
            # -1 for the shebang this wrapper added.
            real = lineno + int(parts[1]) - 2
            out.append(f"{path}:{real}:{parts[3].strip()}")
    return out


def check(path: Path, *, fmt: bool, lint: bool) -> list[str]:
    """Run shfmt and shellcheck over one file's embedded blocks.

    Returns:
        One human-readable line per finding.

    """
    out: list[str] = []
    for lineno, src, folded in blocks(path):
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".sh",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            # The shebang is what makes shfmt and shellcheck agree on the
            # dialect; GitHub runs `run:` blocks under bash by default.
            tmp.write("#!/usr/bin/env bash\n" + src)
            name = tmp.name
        try:
            if fmt and not folded:
                # ruff: ignore[subprocess-without-shell-equals-true]
                r = subprocess.run(
                    [*SHFMT, "-d", name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if r.stdout.strip():
                    out.append(f"{path}:{lineno}: run: block is not shfmt-formatted")
            if lint:
                # ruff: ignore[subprocess-without-shell-equals-true, start-process-with-partial-path]
                r = subprocess.run(
                    [
                        "shellcheck",
                        f"--rcfile={ORG_RCFILE}",
                        "--exclude",
                        SHELLCHECK_EXCLUDE,
                        "--format=gcc",
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                out += findings(r.stdout, path, lineno, folded=folded)
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


def parse_entry(raw: str) -> Entry | str:
    """Read one debt line.

    Returns:
        The entry (line number 0, filled by the caller), or what is wrong.

    """
    fields = raw.split("\t")
    if len(fields) != DEBT_FIELDS or not all(fields):
        return (
            "an entry is <file>, <step name>, <lines>, `# <issue>`, tab-separated "
            f"— this line has {len(fields)} field(s)"
        )
    file, step, count, note = fields
    if not count.isdigit() or int(count) <= CEILING:
        return f"'{count}' is not a line count over the {CEILING}-line ceiling"
    if not note.startswith("#") or not ISSUE_RE.search(note):
        return "the note is a `#` comment naming the issue that will remove the block"
    if UMBRELLA_RE.search(note) and "unassigned" not in note:
        return (
            "a note naming #1352 says `unassigned`: name the issue that removes "
            "the block, or mark it unassigned"
        )
    return Entry(file, step, int(count), 0)


def read_debt(path: Path) -> tuple[list[Entry], list[str]]:
    """Parse the debt file; a missing file is an empty debt.

    Returns:
        The entries, and one finding per line that is not a valid entry.

    """
    if not path.is_file():
        return [], []
    entries: list[Entry] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        entry = parse_entry(raw)
        if isinstance(entry, str):
            errors.append(f"{path}:{n}: {entry}")
        elif (entry.file, entry.step) in seen:
            errors.append(
                f"{path}:{n}: step '{entry.step}' in {entry.file} is listed twice"
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
    """Weigh every `run:` block in files.

    Returns:
        (file, block, weight) for every block, in file order.

    """
    return [(f, run, weight(run.source)) for f in files for run in runs(Path(f))]


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
    at = f"{f}:{run.line}"
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
    verdict = judge(files, debt)
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
        print(line, file=sys.stderr)
    if verdict.findings:
        print(
            f"workflow-shell: {len(verdict.findings)} finding(s) — a run: block holds "
            f"at most {CEILING} lines, and the debt file only shrinks",
            file=sys.stderr,
        )
        return 1
    print(f"workflow-shell: every run: block fits {CEILING} lines or its debt entry")
    return 0


def main() -> None:
    """Entry point: check every file named on the command line."""
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
    args = ap.parse_args()

    if args.ceiling:
        sys.exit(ceiling(args.files, Path(args.ceiling), write=args.write))

    # shellcheck only WARNS on an unreadable --rcfile and then lints at its
    # own defaults, exit 0 — so a belt that did not arrive would read as a
    # clean run. Checked before anything is linted.
    if not args.write and not args.no_lint and not ORG_RCFILE.is_file():
        print(
            f"embedded-shell: the org shellcheck config is missing at {ORG_RCFILE}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.write:
        total = sum(rewrite(Path(f)) for f in args.files)
        print(f"embedded-shell: reformatted {total} run: block(s)")
        return

    findings: list[str] = []
    for f in args.files:
        findings += check(Path(f), fmt=not args.no_format, lint=not args.no_lint)
    for line in findings:
        print(line, file=sys.stderr)
    if findings:
        print(
            f"embedded-shell: {len(findings)} finding(s) in `run:` blocks — "
            "the same bar the belt applies to standalone scripts",
            file=sys.stderr,
        )
        sys.exit(1)
    print("embedded-shell: every run: block is shfmt-clean and shellcheck-clean")


if __name__ == "__main__":
    main()

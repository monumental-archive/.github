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
from typing import TYPE_CHECKING

# `run: |` and `run: |-`, at any indent, list item or not.
RUN_RE = re.compile(r"^(\s*)(- )?run: \|-?\s*$")
# A `shell:` key on the same step selects the interpreter. Only bash-family
# blocks are shell at all — `shell: python` is a different language.
SHELL_RE = re.compile(r"^\s*shell:\s*([a-z0-9 {}$.-]+)\s*$")

if TYPE_CHECKING:
    from collections.abc import Iterator

SHFMT = ["shfmt", "-i", "2", "-bn", "-ci", "-sr", "-s"]

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


def blocks(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (first_body_line_number, dedented_source) per `run:` block.

    Yields:
        One tuple per block whose interpreter is bash-family.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = RUN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
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
        shell = step_shell(lines, start, indent)
        if not any(s in shell for s in ("bash", "sh")):
            continue
        pad = min(len(x) - len(x.lstrip()) for x in body if x.strip())
        yield start + 2, "\n".join(x[pad:] if x.strip() else "" for x in body) + "\n"


def rewrite(path: Path) -> int:
    """Format every `run:` block in place, preserving YAML indentation.

    Returns:
        The number of blocks rewritten.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    changed = 0
    while i < len(lines):
        m = RUN_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent = len(m.group(1))
        start = i
        out.append(lines[i])
        i += 1
        body: list[str] = []
        while i < len(lines) and (
            not lines[i].strip() or (len(lines[i]) - len(lines[i].lstrip())) > indent
        ):
            body.append(lines[i])
            i += 1
        if not any(x.strip() for x in body) or not any(
            s in step_shell(lines, start, indent) for s in ("bash", "sh")
        ):
            out.extend(body)
            continue
        pad = min(len(x) - len(x.lstrip()) for x in body if x.strip())
        src = "\n".join(x[pad:] if x.strip() else "" for x in body) + "\n"
        # ruff: ignore[subprocess-without-shell-equals-true]
        r = subprocess.run(
            [*SHFMT, "-"],
            input="#!/usr/bin/env bash\n" + src,
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0 or not r.stdout:
            out.extend(body)
            continue
        formatted = r.stdout.splitlines()[1:]  # drop the shebang we added
        if formatted == [x[pad:] if x.strip() else "" for x in body]:
            out.extend(body)
            continue
        out.extend((" " * pad + x) if x.strip() else "" for x in formatted)
        changed += 1
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def check(path: Path, *, fmt: bool, lint: bool) -> list[str]:
    """Run shfmt and shellcheck over one file's embedded blocks.

    Returns:
        One human-readable line per finding.

    """
    out: list[str] = []
    for lineno, src in blocks(path):
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
            if fmt:
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
                        "--enable=all",
                        "--external-sources",
                        "--exclude",
                        SHELLCHECK_EXCLUDE,
                        "--format=gcc",
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in r.stdout.splitlines():
                    # gcc format is <file>:<line>:<col>: <level>: <msg>
                    parts = line.split(":", 3)
                    if len(parts) < GCC_FIELDS or not parts[1].isdigit():
                        continue
                    # -1 for the shebang this wrapper added.
                    real = lineno + int(parts[1]) - 2
                    out.append(f"{path}:{real}:{parts[3].strip()}")
        finally:
            Path(name).unlink(missing_ok=True)
    return out


def main() -> None:
    """Entry point: check every file named on the command line."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--no-format", action="store_true")
    ap.add_argument("--no-lint", action="store_true")
    ap.add_argument("--write", action="store_true", help="format blocks in place")
    args = ap.parse_args()

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

#!/usr/bin/env python3
"""Lint and format-check the shell inside mise task `run` bodies (#619).

The gap this closes, measured rather than assumed:

  - `lint:shell` finds shell files through `shfmt --find`, and a
    `run = '''…'''` string in TOML is not a file. ~2900 lines of bash
    across the belt's own task bodies were linted by nothing.
  - `lint:shell-embedded` reaches YAML `run:` blocks only.
  - `lint:toml` checks TOML syntax and is blind to what the strings say.

Measured 2026-08-20 on the canon: a planted
`PROBE=$(echo probe); echo $PROBE; cd /tmp; rm -f $PROBE` in a task body
sails through `mise run ci`; the same line in a `.sh` file is ten
findings. Six `# shellcheck disable=SC…` directives already sat inside
task bodies, inert — a directive suppressing a finding no tool produces
is positive evidence a maintainer expected coverage.

The technique is `lint:shell-embedded`'s, one file format across:
extract each body to a temporary file and run the real tools on it.
Deterministic and offline — shfmt and shellcheck read only the bytes
handed to them.

Three things make the extracted body faithful to what mise executes,
and each of them changes the verdict:

  - **The interpreter's own flags.** mise runs a body as
    `bash -euo pipefail -c <body>` (`[task_config] shell`), so the
    prelude carries `set -euo pipefail`, taken from that declaration
    rather than restated here. It is not cosmetic: under `set -e` an
    assignment from a command substitution propagates failure, so
    modelling the wrong interpreter reports SC2312 on 88 sites that do
    not have it.
  - **The environment mise exports.** A body reads `ORG_BELT_DIR` the
    way a workflow step reads its `env:`. The names come from the
    `[env]` tables of the configs in play, so a variable NOBODY
    declares still reports — the check stays live instead of being
    excluded away.
  - **Tera.** mise renders a body as a template before running it, and
    a `{% raw %}` line is template syntax, not shell. Left in place it
    is a parse error (SC1073/SC1054, the two bodies #624 measured as
    unparsable); those lines are commented out for the tools and
    restored on write.

Nothing is excluded. The standalone corpus and the `run:` corpus are
both clean at `--enable=all` with per-site `# shellcheck disable`
directives where an idiom is deliberate, so the same code is not held
to a lower bar for living in a TOML string.
"""

# Running shfmt and shellcheck IS this file's job, so the subprocess
# rules have nothing to warn about here: every argv is a fixed tool name
# plus a temporary path this file just wrote, nothing from the
# repository reaches a command line, and the tools resolve through the
# belt's PATH, which activate_aggressive puts ahead of anything a
# machine happens to ship.
from __future__ import annotations

import argparse
import re
import shlex
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

SHFMT = ["shfmt", "-i", "2", "-bn", "-ci", "-sr", "-s"]

# mise's own default when no `[task_config] shell` is declared anywhere.
# The belt declares one, so this is the adopter's floor, not the canon's
# path.
DEFAULT_SHELL = "bash -euo pipefail -c"

# `[tasks.name]` and `[tasks."name:with:colons"]`.
HEADER_RE = re.compile(r'^\[tasks\.(?:"([^"]+)"|([^\]"]+))\]\s*$')
RUN_RE = re.compile(r"^run\s*=\s*(.*)$")
# A whole line that is nothing but a Tera statement — `{% raw %}` and its
# closer. Expressions (`{{ … }}`) are deliberately NOT touched: no body
# carries one, and neutralising a construct nothing uses would be
# untested machinery.
TERA_RE = re.compile(r"^(\s*)(\{%.*%\}\s*)$")
UNTERA_RE = re.compile(r"^(\s*)#(\{%.*%\}\s*)$")

# gcc format is <file>:<line>:<col>: <level>: <message> — a usable record
# has at least four fields after one split-limited partition.
GCC_FIELDS = 4


class Body(NamedTuple):
    """One task's `run` string, located in the file it was read from."""

    name: str
    source: str
    # 1-based line of the `run =` key.
    run_line: int
    # The multi-line delimiter (`'''` / `\"\"\"`), or "" for a one-liner.
    delim: str
    # False for one command of a list `run`: N bodies share one `run =`
    # line, so there is no region a rewrite could own.
    rewritable: bool


class Config(NamedTuple):
    """What one mise config contributes: bodies, env names, task shell."""

    bodies: list[Body]
    env: set[str]
    shell: str | None


def locate(text: str) -> dict[str, tuple[int, str]]:
    """Map each task name to its `run =` line and quoting delimiter.

    tomllib gives the decoded value and no position, so the raw text is
    scanned for the one thing splicing and line numbers need: where each
    body starts and how it is quoted.

    Returns:
        name -> (1-based line of `run =`, "'''" / '\"\"\"' / "").

    """
    found: dict[str, tuple[int, str]] = {}
    current: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        header = HEADER_RE.match(line)
        if header:
            current = header.group(1) or header.group(2)
            continue
        if line.startswith("["):
            # Any other table ends the task's own keys. A subtable of the
            # task (`[tasks.x.env]`) does too, which is correct: `run`
            # cannot live inside one.
            current = None
            continue
        if current is None:
            continue
        run = RUN_RE.match(line)
        if run and current not in found:
            rest = run.group(1).strip()
            delim = rest if rest in {"'''", '"""'} else ""
            found[current] = (lineno, delim)
    return found


def read(path: Path) -> Config:
    """Read one mise config: its task bodies, `[env]` names and shell.

    Returns:
        The config's contribution, empty when it declares no tasks.

    """
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    at = locate(text)
    env = {k for k in (data.get("env") or {}) if not k.startswith("_")}
    bodies: list[Body] = []
    for name, task in (data.get("tasks") or {}).items():
        if not isinstance(task, dict):
            continue
        env |= {k for k in (task.get("env") or {}) if not k.startswith("_")}
        run = task.get("run")
        # mise also takes a list of commands, and `file =` instead of a
        # body. Every command of a list is CHECKED; none is rewritten,
        # because they share one `run =` line and splicing a block over
        # it would eat the array.
        listed = isinstance(run, list)
        for source in run if listed else [run]:
            if not isinstance(source, str) or not source.strip():
                continue
            line, delim = at.get(name, (0, ""))
            bodies.append(
                Body(name, source, line, "" if listed else delim, not listed),
            )
    shell = (data.get("task_config") or {}).get("shell")
    return Config(bodies, env, shell if isinstance(shell, str) else None)


def prelude(shell: str, env: Sequence[str]) -> str:
    """Build the header that makes an extracted body faithful to mise.

    `bash -euo pipefail -c` becomes a bash shebang plus
    `set -euo pipefail`; the exported names are what `[env]` supplies,
    exported rather than assigned so an unused one is not itself a
    finding.

    Returns:
        The prelude text, ending in a newline.

    """
    parts = shlex.split(shell)
    interp = Path(parts[0]).name if parts else ""
    flags = [p for p in parts[1:] if p != "-c"]
    head = f"#!/usr/bin/env {interp}\n"
    if flags:
        head += f"set {' '.join(flags)}\n"
    for name in sorted(env):
        head += f'export {name}=""\n'
    return head


def shellify(source: str) -> tuple[str, int]:
    """Turn a `run` body into shell the tools can parse.

    Tera statement lines are commented out and the body is newline-
    terminated — a one-line TOML string carries no trailing newline, and
    reporting its absence would be the extractor's own artefact.

    Returns:
        The shell text and the number of Tera lines commented out.

    """
    out: list[str] = []
    tera = 0
    for line in source.splitlines():
        match = TERA_RE.match(line)
        if match:
            tera += 1
            out.append(f"{match.group(1)}#{match.group(2)}")
        else:
            out.append(line)
    return "\n".join(out) + "\n", tera


def unshellify(text: str, tera: int) -> str | None:
    """Restore the Tera statement lines `shellify` commented out.

    Returns:
        The body text, or None when the count does not match — a body
        whose own comments look like Tera is not one to rewrite blind.

    """
    out: list[str] = []
    seen = 0
    for line in text.splitlines():
        match = UNTERA_RE.match(line)
        if match:
            seen += 1
            out.append(f"{match.group(1)}{match.group(2)}")
        else:
            out.append(line)
    if seen != tera:
        return None
    return "\n".join(out) + "\n"


def formatted(source: str) -> str | None:
    """Run one body through shfmt.

    Returns:
        The canonical text, or None when shfmt could not parse it.

    """
    shell, tera = shellify(source)
    # ruff: ignore[subprocess-without-shell-equals-true]
    result = subprocess.run(
        [*SHFMT, "-"],
        input=shell,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return unshellify(result.stdout, tera)


def findings(
    body: Body,
    path: Path,
    head: str,
    source_paths: Sequence[str],
) -> Iterator[str]:
    """Run shfmt and shellcheck over one extracted body.

    Yields:
        One human-readable line per finding.

    """
    shell, _ = shellify(body.source)
    offset = head.count("\n")
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(head + shell)
        name = tmp.name
    try:
        # ruff: ignore[subprocess-without-shell-equals-true]
        fmt = subprocess.run(
            [*SHFMT, "-d", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if fmt.stdout.strip():
            yield (
                f"{path}:{body.run_line}: {body.name}: `run` body is not "
                f"shfmt-formatted (mise run fix:belt-shell)"
            )
        args = ["shellcheck", "--enable=all", "--external-sources"]
        for source_path in source_paths:
            args += ["--source-path", source_path]
        # ruff: ignore[subprocess-without-shell-equals-true]
        lint = subprocess.run(
            [*args, "--format=gcc", name],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in lint.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) < GCC_FIELDS or not parts[1].isdigit():
                continue
            # A one-liner has one line to point at; a block's body starts
            # on the line after `run = '''`.
            real = (
                body.run_line + int(parts[1]) - offset if body.delim else body.run_line
            )
            yield f"{path}:{real}: {body.name}: {parts[3].strip()}"
    finally:
        Path(name).unlink(missing_ok=True)


def splice(lines: list[str], body: Body, text: str) -> list[str] | None:
    """Replace one body's raw region with `text`, always as a `'''` block.

    Every rewritten body lands as a multi-line literal string: it is the
    form most of the corpus already uses, it needs no escaping, and it is
    the only one that survives a formatter adding lines.

    Returns:
        The new lines, or None when the region could not be bounded.

    """
    if "'''" in text:
        return None
    start = body.run_line - 1
    if start < 0 or start >= len(lines) or not RUN_RE.match(lines[start]):
        return None
    end = start
    if body.delim:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip() == body.delim),
            -1,
        )
        if end < 0:
            return None
    block = ["run = '''", *text.rstrip("\n").split("\n"), "'''"]
    return lines[:start] + block + lines[end + 1 :]


def rewrite(path: Path, bodies: Sequence[Body]) -> tuple[int, list[str]]:
    """Format every rewritable body in one file, verifying by re-reading.

    The splice is never trusted on its own bookkeeping: the file is
    re-decoded through tomllib afterwards and every body compared to the
    text that was meant to land.

    Returns:
        The number of bodies rewritten, and one line per refusal.

    """
    lines = path.read_text(encoding="utf-8").splitlines()
    refused: list[str] = []
    landed: dict[str, str] = {}
    # Last body first: splicing shifts every line below it.
    for body in sorted(bodies, key=lambda b: b.run_line, reverse=True):
        text = formatted(body.source)
        if text is None:
            refused.append(f"{path}:{body.run_line}: {body.name}: cannot format")
            continue
        if text == (body.source if body.source.endswith("\n") else body.source + "\n"):
            continue
        if not body.rewritable:
            refused.append(
                f"{path}:{body.run_line}: {body.name}: a list `run` is checked "
                f"but never rewritten — make it one body to format it",
            )
            continue
        spliced = splice(lines, body, text)
        if spliced is None:
            refused.append(f"{path}:{body.run_line}: {body.name}: cannot rewrite")
            continue
        lines = spliced
        landed[body.name] = text
    if not landed:
        return 0, refused
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Never the splicer's own bookkeeping: what the file says now is read
    # back through the same decoder the lint half uses.
    for body in read(path).bodies:
        expected = landed.get(body.name)
        if expected is not None and body.source != expected:
            refused.append(f"{path}:{body.run_line}: {body.name}: rewrite did not land")
    return len(landed), refused


def collect(
    paths: Sequence[Path],
    env_from: Sequence[Path],
) -> tuple[dict[Path, Config], str, list[str]]:
    """Read every config, and settle the env and shell they run under.

    Returns:
        The per-file configs, the task shell, and the exported names.

    """
    configs = {path: read(path) for path in paths}
    extra = [read(path) for path in env_from]
    env: set[str] = set()
    for config in [*configs.values(), *extra]:
        env |= config.env
    shell = next(
        (c.shell for c in [*configs.values(), *extra] if c.shell),
        DEFAULT_SHELL,
    )
    return configs, shell, sorted(env)


def main(argv: Sequence[str] | None = None) -> int:
    """Check, or rewrite, every task body in the files named.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="lint mise task run bodies")
    parser.add_argument("files", nargs="*")
    parser.add_argument(
        "--env-from",
        action="append",
        default=[],
        help="a config whose [env] the bodies read but whose tasks are not checked",
    )
    parser.add_argument(
        "--source-path",
        action="append",
        default=[],
        help="a directory shellcheck may resolve `source` targets in",
    )
    parser.add_argument("--write", action="store_true", help="format bodies in place")
    args = parser.parse_args(argv)

    paths = [Path(f) for f in args.files]
    if not paths:
        print("belt-shell: no mise config named, nothing to check")
        return 0
    configs, shell, env = collect(paths, [Path(f) for f in args.env_from])

    if Path(shlex.split(shell)[0]).name not in {"bash", "sh"}:
        print(f"belt-shell: task shell is {shell!r}, not shell — skipped")
        return 0

    if args.write:
        total = 0
        refused: list[str] = []
        for path, config in configs.items():
            written, problems = rewrite(path, config.bodies)
            total += written
            refused += problems
        for line in refused:
            print(line, file=sys.stderr)
        print(f"belt-shell: reformatted {total} `run` body(ies)")
        return 1 if refused else 0

    head = prelude(shell, env)
    # The linted tree resolves a body's own `source` targets; the belt
    # directory the caller adds resolves the ones it delivers.
    source_paths = [str(Path(p).resolve()) for p in [".", *args.source_path]]
    found: list[str] = []
    bodies = 0
    for path, config in configs.items():
        for body in config.bodies:
            bodies += 1
            found += findings(body, path, head, source_paths)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(
            f"belt-shell: {len(found)} finding(s) in mise task bodies — the same "
            "bar the belt applies to standalone scripts",
            file=sys.stderr,
        )
        return 1
    print(f"belt-shell: {bodies} task body(ies) shfmt-clean and shellcheck-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

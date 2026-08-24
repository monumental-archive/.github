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

The file also carries the one rule about a body that no shell linter can
hold an opinion about: a body may not execute `mise run` (#764). A nested
mise races the parallel lint fan-out and fails to resolve its own
lockfile — #82, which made `mise run ci` unrunnable locally — and the
belt had been stating that in a comment while three of its own tasks did
it. It lives here because this is already the file that decodes a body
out of any tracked mise config; a second reader would be a second thing
to keep true.
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

# The org's shellcheck settings, read from the belt directory this file is
# itself delivered in (#696). One definition, shared with lint:shell and
# embedded-shell.py, instead of a third copy of `--enable=all` here; and
# these bodies are linted as temporary files, so without it shellcheck
# was free to discover $HOME/.shellcheckrc and quietly lower the level.
ORG_RCFILE = Path(__file__).resolve().parent / "shellcheckrc"

# What the belt sets as mise's default inline shell
# (`[settings] unix_default_inline_shell_args`, #700), and therefore what a
# body runs under when its own file pins nothing.
#
# NOT mise's own default, which is `sh -c -o errexit` — dash on an Ubuntu
# runner. This constant used to claim otherwise, and the claim was load
# bearing: an unpinned repo's bodies were modelled with pipefail and
# nounset they did not get, so the linter judged them against a stricter
# shell than the runner gave them. The belt setting is what makes the
# optimistic model true; `unpinned()` below is what keeps it true when the
# belt is not the layer in play.
DEFAULT_SHELL = "bash -euo pipefail -c"

# The remedy `unpinned()` names, verbatim, so the message hands over the
# lines to paste rather than a description of them.
SHELL_PIN = '[task_config]\nshell = "bash -euo pipefail -c"'

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

# `mise run` as a command, and what may legally stand between it and the
# start of a command. The operators are what ends one command and begins
# the next — `$(` reaches command position through its `(` — and the
# words are the ones a command may follow while still being the command:
# an assignment prefix, and the shell keywords.
MISE_RUN_RE = re.compile(r"\bmise\s+run\b")
COMMAND_BREAK_RE = re.compile(r"[;&|(){}`\n]")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
COMMAND_PREFIXES = frozenset(
    {
        "!",
        "if",
        "elif",
        "then",
        "else",
        "do",
        "while",
        "until",
        "exec",
        "command",
        "env",
        "time",
        "nohup",
    },
)
# The marker that answers for an invocation, `capability-boundary:`-shaped:
# absolute by default, and every exception a named, lintable reason. An
# empty marker is not a marker — the reason is the whole point.
NESTED_MARKER_RE = re.compile(r"#.*\bnested-mise:\s*\S")
# What may precede a `#` for it to open a comment: nothing, whitespace, or
# an operator. Inside a word (`${x#y}`, `url#frag`) it does not.
COMMENT_OPENERS = frozenset(" \t\n;&|(")


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


class Asserted(NamedTuple):
    """The findings that are facts about the FILES, not about their shell.

    Both are settled before a body is modelled and survive a run in which
    the tools decline to read one, so they travel together rather than as
    two more parameters of the body sweep.
    """

    pins: list[str]
    nests: list[str]


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
        args = ["shellcheck", f"--rcfile={ORG_RCFILE}"]
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


def unpinned(configs: dict[Path, Config]) -> list[str]:
    """Name every checked config that defines tasks and pins no shell.

    The assertion that makes the strict shell true rather than advisory
    (#700). `[task_config]` governs the file it is written in and layers
    nowhere, so a repo that defines a task and restates nothing is relying
    on the belt's default being the layer in play — true in the gate and on
    a belt-wearing machine, false in a clone that has neither. The pin costs
    two lines and removes the dependency.

    Only files being CHECKED are asserted on: an `--env-from` config is read
    for its `[env]`, and a config that declares no tasks has nothing to run
    under a shell, so both stay silent. That is the same skip-clean rule
    every other belt linter follows.

    Returns:
        One finding per offending file, each carrying the remedy.

    """
    found: list[str] = []
    for path, config in sorted(configs.items()):
        if not config.bodies or config.shell:
            continue
        remedy = SHELL_PIN.replace("\n", "; ")
        found.append(
            f"{path}:1: defines {len(config.bodies)} task body(ies) and pins no "
            f"task shell — mise's default is `sh`, which is dash on an Ubuntu "
            f"runner: no pipefail, no nounset, and `[[ ]]`/`<<<` are syntax "
            f"errors. Add to {path}: {remedy}",
        )
    return found


def scrub(source: str) -> str:
    """Blank every quoted span and comment tail, preserving offsets.

    Quote state is carried ACROSS lines, because a body's quoted spans
    routinely are: the awk programs in this belt run to fifty lines
    inside one `'…'`. Scrubbing line by line would end each span at the
    newline and read the program text as shell.

    The result is the same length as `source` with its newlines in place,
    so an offset into it is an offset into the original.

    Returns:
        The source with quoted and commented characters replaced by
        spaces.

    """
    out: list[str] = []
    quote = ""
    comment = False
    prev = ""
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\n":
            comment = False
            out.append(char)
        elif comment:
            out.append(" ")
        elif quote:
            # A backslash escapes inside "…" and is literal inside '…'.
            if quote == '"' and char == "\\" and index + 1 < len(source):
                out.append(" ")
                index += 1
            elif char == quote:
                quote = ""
            out.append(" ")
        elif char in {"'", '"'}:
            quote = char
            out.append(" ")
        elif char == "\\" and index + 1 < len(source):
            out.append("  ")
            index += 1
        elif char == "#" and (not prev or prev in COMMENT_OPENERS):
            comment = True
            out.append(" ")
        else:
            out.append(char)
        prev = source[index]
        index += 1
    return "".join(out)


def nested_runs(source: str) -> Iterator[int]:
    """Find every `mise run` one body EXECUTES, as a 0-based line index.

    Executed, not merely written: a remedy a task echoes to a human MAY
    say "run mise run fix:x", and lint:audit-scheduled greps the workflows
    for that very string. Both are quoted, so `scrub` has already removed
    them; what is left is judged by position, so `echo mise run fix:x`
    is text too and only a command is a command.

    Yields:
        The line index, within `source`, of each executed invocation.

    """
    scrubbed = scrub(source)
    for match in MISE_RUN_RE.finditer(scrubbed):
        head = COMMAND_BREAK_RE.split(scrubbed[: match.start()])[-1]
        if all(
            word in COMMAND_PREFIXES or ASSIGNMENT_RE.match(word)
            for word in head.split()
        ):
            yield scrubbed.count("\n", 0, match.start())


def excused(lines: Sequence[str], index: int) -> bool:
    """Say whether a marker answers for the invocation on `lines[index]`.

    On the line itself, or anywhere in the run of comment lines directly
    above it — which is where a reason long enough to BE a reason fits.
    A blank line or any code between the two ends the block: a marker
    must sit against the thing it excuses, or it becomes a comment about
    something else.

    Returns:
        True when a `# nested-mise: <reason>` marker covers the line.

    """
    if NESTED_MARKER_RE.search(lines[index]):
        return True
    for above in range(index - 1, -1, -1):
        line = lines[above].strip()
        if not line.startswith("#"):
            return False
        if NESTED_MARKER_RE.search(line):
            return True
    return False


def nested(configs: dict[Path, Config]) -> list[str]:
    """Name every task body that executes `mise run` without a marker.

    The structural form of #82's lesson, which the belt had been carrying
    as a comment while three of its own tasks broke it (#764). A nested
    mise races the parallel `lint:*` fan-out `ci` runs and fails to
    resolve its own lockfile; the task that does it is usually a lint
    reaching for its own `fix:` half, and the red it produces names no
    finding, which is the least debuggable failure a gate can produce.

    The remedy is a derivation both halves invoke — the `derive-badges.sh`
    pattern — or mise's own `depends`, which composes tasks without a
    second mise. Where invoking a task BY NAME is genuinely the job, the
    marker says so and stays lintable.

    Returns:
        One finding per unexcused invocation, each carrying the remedy.

    """
    found: list[str] = []
    for path, config in sorted(configs.items()):
        for body in config.bodies:
            lines = body.source.splitlines()
            for index in nested_runs(body.source):
                if excused(lines, index):
                    continue
                # A one-liner has one line to point at; a block's body
                # starts on the line after `run = '''`.
                real = body.run_line + index + 1 if body.delim else body.run_line
                found.append(
                    f"{path}:{real}: {body.name}: the body executes `mise run` — a "
                    f"nested mise races the parallel lint fan-out and cannot "
                    f"resolve its own lockfile, which made `mise run ci` unrunnable "
                    f"locally (#82). Share the step as a script both tasks invoke, "
                    f"or compose with `depends = [...]`; if dispatching by name is "
                    f"the job, answer for it with a `# nested-mise: <why>` comment "
                    f"on or above the line.",
                )
    return found


def write_mode(configs: dict[Path, Config]) -> int:
    """Rewrite every body in place, and name any splice that did not land.

    Returns:
        A process exit status.

    """
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


def check_mode(
    configs: dict[Path, Config],
    shell: str,
    env: Sequence[str],
    source_path: Sequence[str],
    asserted: Asserted,
) -> int:
    """Lint every body, and settle the exit with the file findings.

    `asserted` is already reported by the caller; it is passed in because
    a clean body sweep does not make an unpinned config, or one that
    nests a `mise run`, green.

    Returns:
        A process exit status.

    """
    head = prelude(shell, env)
    # The linted tree resolves a body's own `source` targets; the belt
    # directory the caller adds resolves the ones it delivers.
    source_paths = [str(Path(p).resolve()) for p in [".", *source_path]]
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
    if asserted.pins:
        print(
            f"belt-shell: {len(asserted.pins)} config(s) define tasks without "
            "pinning the task shell — the belt supplies the strict shell as a "
            "default, and the pin is what makes the file true without it (#700)",
            file=sys.stderr,
        )
    if asserted.nests:
        print(
            f"belt-shell: {len(asserted.nests)} task body(ies) execute `mise run` "
            "— the race #82 measured, and the reason every shared derivation in "
            "the belt is a script both halves invoke (#764)",
            file=sys.stderr,
        )
    if found or asserted.pins or asserted.nests:
        return 1
    print(f"belt-shell: {bodies} task body(ies) shfmt-clean and shellcheck-clean")
    return 0


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

    # shellcheck only WARNS on an unreadable --rcfile and then lints at its
    # own defaults, exit 0 — so a belt that did not arrive would read as a
    # clean run. Checked before any body is linted.
    if not args.write and not ORG_RCFILE.is_file():
        print(
            f"belt-shell: the org shellcheck config is missing at {ORG_RCFILE}",
            file=sys.stderr,
        )
        return 1
    configs, shell, env = collect(paths, [Path(f) for f in args.env_from])

    # Asserted before the interpreter skip below and before any body is
    # read: whether a file pins the shell, and whether a body runs a
    # nested mise, are facts about the file rather than about what the
    # tools can make of its bodies — so a config the linter then declines
    # to model must still answer for both. Neither is asserted in write
    # mode: fix:belt-shell formats bodies, and can no more add a pin than
    # it can extract a shared derivation for you.
    asserted = Asserted(
        [] if args.write else unpinned(configs),
        [] if args.write else nested(configs),
    )
    for line in [*asserted.pins, *asserted.nests]:
        print(line, file=sys.stderr)

    if Path(shlex.split(shell)[0]).name not in {"bash", "sh"}:
        print(f"belt-shell: task shell is {shell!r}, not shell — skipped")
        return 1 if asserted.pins or asserted.nests else 0

    if args.write:
        return write_mode(configs)
    return check_mode(configs, shell, env, args.source_path, asserted)


if __name__ == "__main__":
    sys.exit(main())

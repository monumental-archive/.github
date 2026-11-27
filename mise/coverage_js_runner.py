#!/usr/bin/env python3
"""Name a JavaScript tree's test runner and its coverage provider.

Org canon (#1357). The `typescript` leg of `coverage-measure.sh` runs the
repository's own runner under that runner's own coverage provider, and
both are READ, never chosen: the runner and the provider are whatever
the repository's lockfile pins, and the belt pins neither. A second
instrumentation of the suite (c8 or nyc beside the runner) is rejected
in the issue, and so is a provider version the belt would own where the
repository's lockfile should.

Run from the repository root, with the tracked files among
`package.json` and the lockfiles as arguments (the caller asks git, so
an untracked lockfile is never read). Prints one line, or nothing:

    vitest <provider>     the leg applies; <provider> is `v8`,
                          `istanbul`, `none` (the lockfile carries no
                          provider) or `both` (it carries two, and the leg
                          does not choose)
    unsupported <runner>  the tree has a test runner the leg does not
                          support; the caller names it and skips
    (nothing)             no tracked root package.json, or one that
                          declares no test runner at all

The leg supports vitest. Any other runner is named, from the root
package.json's dependencies or its `test` script, so a skip says what
it skipped rather than reading as a tree with nothing to measure.

`--measured-run <directory> <tracked…> -- <command…>` is the second
question, asked by `measured` (#2105): whether the command it was
handed is the vitest run the leg measures. vitest's coverage has no
environment switch, so the measurement rides the test command's own
line, as Go's `-cover` would were GOFLAGS not there. Where the tracked
files above answer `vitest v8` or `vitest istanbul`, and the command
runs vitest behind nothing but a package manager's launcher, the run
is printed back, NUL-separated, with `run` inserted where vitest was
given no subcommand and the leg's five coverage flags appended, the
reports going to <directory>. Anything else prints nothing, and
`measured` execs the command unchanged: a tree that is not vitest's,
a provider the leg reports on (none, or both), a command with no
vitest in it, or a vitest subcommand other than `run`. A coverage flag
of the command's own is refused by name before anything runs, because
the leg sets them. Running the command with a coverage-adding `vitest`
first on PATH was measured and refused (#1357): `pnpm exec` and `npx`
put `node_modules/.bin` at the head of PATH, so the repository's own
binary runs and the shim never does.

`locked()` is the same reader asked for a version (#2145): the
versions a lockfile resolves one package to, for the four lockfiles
above and for Cargo.lock beside them. lint:subject-budget measures a
lock-file update's subject with it, so the leg and the budget read a
lockfile one way.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# The belt's helpers, modules beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import decoded
import findings
import typed_std

if TYPE_CHECKING:
    from collections.abc import Sequence

# Lockfiles the leg reads, in the order a package manager would prefer.
LOCKFILES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock")

# A package's entry in any of the four: pnpm's `  vitest@4.1.12:` and
# `'@vitest/coverage-v8@4.1.12':`, yarn's `vitest@^4.1.12:` and berry's
# `"vitest@npm:4.1.12":`, bun's `"vitest@4.1.12"`, and npm's
# `"node_modules/vitest":`.
ENTRY = r'(?:(?:^|[\s"\'])({name})@(?:npm:)?[\^~]?\d|"node_modules/({name})")'
RUNNER = re.compile(ENTRY.format(name="vitest"), re.MULTILINE)
PROVIDER = re.compile(
    ENTRY.format(name=r"@vitest/coverage-(?:v8|istanbul)"),
    re.MULTILINE,
)

# Where each lockfile writes the version an ENTRY resolves to, matched
# from the end of the entry's package name (#2145): pnpm and bun write
# it after the `@` (`vitest@4.1.12:`, `vitest@4.1.12(@types/node@…)`,
# `"vitest@4.1.12"`); npm as the entry's `"version"` key; yarn, classic
# and berry alike, on the line after the entry's header (`  version
# "4.1.12"`, `  version: 4.1.12`), because the header names the RANGE.
VERSION_AT = {
    "pnpm-lock.yaml": re.compile(r"@(\d[^\s:('\",]*)"),
    "package-lock.json": re.compile(r'"\s*:\s*\{[^{}]*?"version"\s*:\s*"([^"]+)"'),
    "yarn.lock": re.compile(r'[^\n]*\n[ \t]+version:?[ \t]+"?([^"\s]+)'),
    "bun.lock": re.compile(r'@(\d[^\s"]*)"'),
}

# Cargo's lockfile, read beside the four: its `[[package]]` tables name
# every crate the workspace resolves, each at its exact version.
CARGO_LOCK = "Cargo.lock"


def locked(lockfile: str, lock: str, name: str) -> set[str]:
    """Read the versions a lockfile resolves for one package.

    The entries are ENTRY's for the four npm lockfiles, so this answers
    the question RUNNER and PROVIDER ask, with the version: a package
    the leg would see as pinned is one this reads a version for. A yarn
    entry counts only at its header, the one line at the left margin;
    berry's indented `resolution: "vitest@npm:4.1.12"` is the same entry
    again. Cargo.lock is read as the TOML it is.

    Returns:
        Every version the lockfile resolves the package to, empty when
        it carries no entry for it.

    A Cargo.lock that is not TOML, or not the shape Cargo writes, raises
    decoded's UndecodableError from its decoders.

    """
    base = Path(lockfile).name
    if base == CARGO_LOCK:
        doc = decoded.load_toml(lock)
        packages = decoded.as_array(doc.get("package", []), "package")
        found: set[str] = set()
        for i, value in enumerate(packages):
            package = decoded.as_object(value, f"package[{i}]")
            if package.get("name") == name:
                where = f"package[{i}].version"
                found.add(decoded.as_str(package.get("version"), where))
        return found
    entry = re.compile(ENTRY.format(name=re.escape(name)), re.MULTILINE)
    at = VERSION_AT[base]
    versions: set[str] = set()
    for match in entry.finditer(lock):
        group = 1 if typed_std.group(match, 1) is not None else 2
        if base == "yarn.lock":
            line = lock.rfind("\n", 0, match.start(group)) + 1
            if lock[line] in " \t":
                continue
        version = at.match(lock, match.end(group))
        if version is not None:
            versions.add(typed_std.group(version, 1) or "")
    return versions - {""}


# Runners the leg does not support, by the package that provides them.
OTHER_RUNNERS = (
    "jest",
    "mocha",
    "ava",
    "jasmine",
    "tap",
    "uvu",
    "karma",
    "@playwright/test",
    "cypress",
)

# The `test` script npm writes into a new package.json: no runner at all.
NPM_PLACEHOLDER = "no test specified"


def providers(lock: str) -> set[str]:
    """Read the vitest coverage providers a lockfile carries.

    Returns:
        `v8` and/or `istanbul`.

    """
    found: set[str] = set()
    for match in PROVIDER.finditer(lock):
        # PROVIDER takes one of its two groups in every match.
        package = typed_std.group(match, 1) or typed_std.group(match, 2) or ""
        found.add(package.rsplit("-", 1)[-1])
    return found


@dataclass(frozen=True)
class Manifest:
    """What the leg reads of a root package.json."""

    # Every package named in `dependencies` and `devDependencies`.
    declared: frozenset[str]
    # The `scripts.test` string, if there is one.
    test_script: str | None
    # Whether `vitest` appears anywhere in the manifest.
    mentions_vitest: bool


def read_manifest(value: object) -> Manifest:
    """Decode a package.json for the fields the leg reads.

    npm's own reading is the leg's: a manifest that is not an object, or
    a section or script of another shape, declares nothing.

    Returns:
        The manifest.

    """
    manifest = value if decoded.is_object(value) else {}
    declared: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        section = manifest.get(field)
        if decoded.is_object(section):
            declared |= section.keys()
    scripts = manifest.get("scripts")
    script = scripts.get("test") if decoded.is_object(scripts) else None
    return Manifest(
        declared=frozenset(declared),
        test_script=script if isinstance(script, str) else None,
        mentions_vitest="vitest" in json.dumps(manifest),
    )


def other_runner(manifest: Manifest) -> str | None:
    """Name a test runner the leg does not support.

    Returns:
        The runner's package, the first word of the `test` script, or
        None when the manifest declares no test runner.

    """
    for runner in OTHER_RUNNERS:
        if runner in manifest.declared:
            return runner
    script = manifest.test_script
    if script is not None and script.strip() and NPM_PLACEHOLDER not in script:
        words = script.split()
        if words[:2] == ["node", "--test"]:
            return "node --test"
        return words[0]
    return None


# What may stand before `vitest` on `measured`'s command line: the
# package managers' ways of running a dependency's binary, or nothing.
# A path ending in `/vitest` (`node_modules/.bin/vitest`) is the binary
# itself.
LAUNCHERS = (
    (),
    ("pnpm",),
    ("pnpm", "exec"),
    ("npx",),
    ("npx", "--no-install"),
    ("npx", "--no"),
    ("yarn",),
    ("yarn", "exec"),
    ("npm", "exec"),
    ("npm", "exec", "--"),
)

# vitest subcommands other than `run`; a run given one of them is not
# the suite's run, and `measured` leaves it as it is. An argument that
# is none of them is a file filter or an option, which `vitest run`
# takes as it is.
SUBCOMMANDS = frozenset(
    {"watch", "dev", "bench", "init", "list", "related", "typecheck"},
)


class RefusedError(Exception):
    """The command sets what the measurement sets: refused by name."""


def measured_run(
    provider: str, directory: str, command: Sequence[str]
) -> list[str] | None:
    """Read a test command for the vitest run the leg measures.

    The first argument whose basename is `vitest`, with only one of the
    LAUNCHERS before it, is the run; a next argument that is one of the
    SUBCOMMANDS is not a run; no subcommand is `run`.

    Returns:
        The command with `run` where vitest was given no subcommand and
        the leg's coverage flags appended, or None when the command is
        not a vitest run and goes unchanged.

    Raises:
        RefusedError: the run carries a `--coverage` argument of its own.

    """
    at = next(
        (i for i, word in enumerate(command) if Path(word).name == "vitest"), None
    )
    if at is None or tuple(command[:at]) not in LAUNCHERS:
        return None
    rest = list(command[at + 1 :])
    if rest and rest[0] in SUBCOMMANDS:
        return None
    if not rest or rest[0] != "run":
        rest = ["run", *rest]
    own = next((word for word in rest if word.startswith("--coverage")), None)
    if own is not None:
        msg = (
            f"{own} is on the vitest run, which would measure TypeScript a"
            " second way or replace the measurement's own (#2105); drop it:"
            " the typescript leg sets vitest's coverage flags on the measured run"
        )
        raise RefusedError(msg)
    # The repository's own vitest config decides what is in the suite and
    # in the report (`coverage.include`: vitest counts only files a test
    # loaded unless it is set). The leg sets only what makes the number
    # readable: the provider, the two reporters, and the run's directory,
    # so nothing lands in the tree unasked.
    return [
        *command[: at + 1],
        *rest,
        "--coverage.enabled=true",
        f"--coverage.provider={provider}",
        "--coverage.reporter=json-summary",
        "--coverage.reporter=lcov",
        f"--coverage.reportsDirectory={directory}",
    ]


def answer_measured(argv: list[str]) -> int:
    """Answer `--measured-run <directory> <tracked…> -- <command…>`.

    Returns:
        0 with the run on stdout, NUL-separated, or nothing when the
        command goes unchanged; 1 with the refusal on stderr.

    """
    if "--" not in argv[1:]:
        sys.stderr.write(
            "measured: usage: coverage_js_runner.py --measured-run"
            " <directory> <tracked…> -- <command…>\n"
        )
        return 2
    split = argv.index("--", 1)
    directory, tracked, command = argv[0], argv[1:split], argv[split + 1 :]
    try:
        line = runner(tracked)
    except (OSError, decoded.UndecodableError) as exc:
        findings.finding("package.json", str(exc))
        return 1
    if line not in {"vitest v8", "vitest istanbul"}:
        return 0
    try:
        run = measured_run(line.removeprefix("vitest "), directory, command)
    except RefusedError as exc:
        sys.stderr.write(f"measured: {exc}\n")
        return 1
    if run is not None:
        sys.stdout.write("".join(f"{word}\0" for word in run))
    return 0


def runner(tracked: Sequence[str]) -> str | None:
    """Name the tree's runner and provider from its tracked files.

    Returns:
        The runner line, or None.

    """
    if "package.json" not in tracked:
        return None
    manifest = read_manifest(decoded.load_json(Path("package.json").read_bytes()))
    lockfile = next((name for name in LOCKFILES if name in tracked), None)
    lock = Path(lockfile).read_text(encoding="utf-8") if lockfile else ""
    if RUNNER.search(lock):
        found = providers(lock)
        provider = found.pop() if len(found) == 1 else ("both" if found else "none")
        return f"vitest {provider}"
    other = other_runner(manifest)
    if other == "vitest" or (other is None and manifest.mentions_vitest):
        # Declared, but the lockfile pins no vitest: nothing the leg may run.
        return "unsupported vitest-without-a-lockfile-entry"
    if other is not None:
        return f"unsupported {other}"
    return None


def main(argv: list[str]) -> int:
    """Print the runner line for the tree, or answer `--measured-run`.

    `--measured-run` reads `measured`'s command (answer_measured); any
    other arguments are the tracked files among package.json and the
    lockfiles.

    Returns:
        0, or 1 when the root package.json is not readable JSON;
        answer_measured's status for `--measured-run`.

    """
    if argv[:1] == ["--measured-run"]:
        return answer_measured(argv[1:])
    try:
        line = runner(argv)
    except (OSError, decoded.UndecodableError) as exc:
        findings.finding("package.json", str(exc))
        return 1
    if line is not None:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

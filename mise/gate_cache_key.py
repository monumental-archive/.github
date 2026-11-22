#!/usr/bin/env python3
"""Name the gate's caches: the tool cache, and the compiler in the cargo caches.

The gate's tool cache used to be restored and saved by mise-action itself
(`cache: true`). That action restores by EXACT key only — its
`restoreMiseCache` calls `cache.restoreCache([cachePath], primaryKey)` with
no restore keys — so a pull request could not fall back to the default
branch's last cache, and a canon or tool bump installed everything from
nothing on every ref. #1406 moves the restore and the save out of the
action into `actions/cache/restore` and a default-branch-only
`actions/cache/save`, around an install with `cache: false`.

THE KEY IS THE ACTION'S, CHARACTER FOR CHARACTER. Every repository's
default branch holds the cache mise-action saved under its own key; a key
of ours that differed by one byte would make the first run after this
change cold everywhere, for nothing. So this reproduces the action's
default template as v5.2.7 renders it with the inputs the gate gives it
(no `install_args`, no `plugins`, no `bootstrap`, no `cache_key` override):

    {prefix}-{target}-{image}-{version}[-{MISE_ENV}]-p{period}-{hash}

`target` is `getTarget()` (`linux-x64`), `image` the runner's `ImageOS`
(`self-hosted` when unset), `version` the mise version the install step
pins, `period` whole 30-day periods since the epoch (`cache_max_age_days`
at its default), and `hash` the action's `glob.hashFiles` over its config
globs — computed by the workflow's own `hashFiles` over the same list,
since both are @actions/glob's walk and digest (`no-config` when nothing
matched). The test pins the result to a key mise-action logged on a real
run.

The path is the action's too: GitHub includes the path list in a cache's
version, so a restore naming `~/.local/share/mise` would never meet an
entry saved under the absolute path the action resolved.

THE BELT'S LOCKFILE IS THE ONE PART ADDED (#1414). The action's globs stop
at the working directory and its parents, so in a calling repository the
belt, delivered under `.org-canon/`, was never in the key: a belt tool
bump kept the key, the default branch hit a cache without the new tool,
installed it on every run and never saved it, because it hit. So the key
ends in `-b{sha256 of .org-canon/mise/mise.lock}`: the one file that
changes exactly when a belt tool, its version, its checksums or its
install options do. Not the belt's `config.toml`, which carries every
task body and changes with almost every canon release — that would make
every canon bump a cold install of an unchanged toolset. The one belt
tool option the lockfile does not record, `bin_path`, is resolved by mise
at run time and changes nothing installed. The first run after #1414 on
each default branch fills the new key once; every other ref falls back
by prefix in the meantime.

The fallback prefix stops before the mise version: a mise bump restores
the previous tools and the action installs the one binary that changed.
`name` is the hand-moved part of the key (#1165) and stays the
invalidation lever: a different name shares no prefix.

A JOB THAT INSTALLS A LIST (#1408) passes it with `--install`, the words
its install step gives mise-action as `install_args`, and the KEY carries
their digest as `-i{digest}` before the belt suffix: the tools sorted and
joined, flags dropped, as mise-action digests `install_args`. Only the
key: the list carries versions (`rust@1.97.1 …`), and a digest in the
prefix would leave a job no fallback the moment any pin moved — a Rust
or cargo-pgrx bump would install every leg cold, the case the fallback
exists for. What must never cross, one pgrx leg's Postgres into another
major's leg, is kept apart by the NAME (`mise-pgrx-v1-pg18`), which is
the prefix. Without `--install` the key is the one above, to the byte.

THE BELT TOOLS LEFT OUT (#1410). The gate disables the belt tools a tree
does not need (mise/gate_tools.py), and what it installs then changes
with the tree's files, which neither hash sees: a first Dockerfile needs
hadolint under the same key. An exact hit is never re-saved, so that
restore would lack hadolint and fetch it on every run until the key
moved. So a tree that leaves tools out ends the key in
`-off{digest of the left-out set}` (ORG_GATE_BELT_DIGEST) — after the
prefix, so a pull request whose set differs still falls back to the
default branch's entry and installs only what it adds. A tree that
leaves nothing out (the canon) sets no digest and no suffix.

THE CARGO CACHES NAME THEIR COMPILER (#1864). The gate's cargo caches
hold `target`, whose meaning depends on the compiler that built it, and
were keyed on Cargo.lock alone. A toolchain bump that left Cargo.lock
unchanged restored the old compiler's objects, cargo built the new ones
beside them, and llvm-cov read both: edtf's Rust 1.100 bump measured
70.1% over a target 1.97.1 had filled, 99.3% over a clean one. So
`cargo` prints the sha256 of the full `rustc -vV` output of the `rustc`
the job's PATH resolves after its install step (mise-action exports the
mise environment, so that is the toolchain the tasks build with). The
release, commit hash, LLVM version and host are all in it. The workflow
places the digest before the Cargo.lock hash, in the key and in the
fallback prefix alike: a toolchain change misses, and a fallback only
ever restores an entry the same compiler filled. No `rustc` is red,
never an empty digest: the step runs only where a cargo cache does.

EVERY COVERAGE DIRECTORY NAMES ITS COMPILER (#1877). The cache key keeps
a restore from mixing compilers, but not a directory that persists: a
developer's `target/`, a reused worktree. Rust 1.100 moved test
executables from `deps/` to `build/*/out/`, so a bump left 1.97.1's
beside 1.100.0's and `cargo llvm-cov report` read both (99.37% fresh,
95.77% over the mixed directory, on edtf). So `digest` prints the first
16 hex characters of the same digest, for the `rustc` on PATH (under
RUSTUP_TOOLCHAIN when the caller sets it), and every coverage
CARGO_TARGET_DIR ends in it: a compiler change is a new, empty
directory. Same red branches as `cargo`.
"""

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import findings
import typed_std

NAME = "gate-cache-key"

# mise-action's `cachePeriod`: whole periods of `cache_max_age_days`
# (default 30) since the Unix epoch.
PERIOD_SECONDS = 30 * 24 * 60 * 60

# Node's `process.arch` for the machines a runner reports.
ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}

# The install step's own pin, as lint:installer-pinned reads it.
VERSION = re.compile(r"^\s+version:\s*(\d{4}\.\d+\.\d+)\s*$", re.MULTILINE)


def mise_version(workflow: str) -> str:
    """Read the mise version the gate's install steps pin.

    Returns:
        The one version every `version:` pin in the workflow names.

    Raises:
        ValueError: no pin, or pins that disagree — a key built from either
            would name a cache the install does not make.

    """
    found = sorted({typed_std.group(m, 1) or "" for m in VERSION.finditer(workflow)})
    if len(found) != 1:
        msg = (
            f"expected one mise version pinned in the workflow, found {found or 'none'}"
        )
        raise ValueError(msg)
    return found[0]


def target(system: str, machine: str) -> str:
    """Render mise-action's `getTarget()` for a runner.

    Returns:
        `<os>-<arch>`, as the action names it.

    Raises:
        ValueError: a platform the gate does not run on.

    """
    oses = {"Linux": "linux", "Darwin": "macos"}
    if system not in oses or machine not in ARCH:
        msg = f"no mise-action target for {system}/{machine}"
        raise ValueError(msg)
    return f"{oses[system]}-{ARCH[machine]}"


def data_dir(env: dict[str, str]) -> str:
    """Resolve the directory mise-action caches.

    Returns:
        MISE_DATA_DIR, else XDG_DATA_HOME/mise, else ~/.local/share/mise.

    """
    if env.get("MISE_DATA_DIR"):
        return env["MISE_DATA_DIR"]
    if env.get("XDG_DATA_HOME"):
        return str(Path(env["XDG_DATA_HOME"]) / "mise")
    return str(Path(env["HOME"]) / ".local" / "share" / "mise")


def belt_hash(lock: bytes) -> str:
    """Digest the belt's lockfile.

    Returns:
        Its sha256, in hex.

    """
    return hashlib.sha256(lock).hexdigest()


class Facts(NamedTuple):
    """What the key is built from, besides the runner's environment."""

    version: str
    plat: str
    now: float
    belt: str


def install_digest(install: str) -> str:
    """Digest a job's install list as mise-action digests `install_args`.

    Returns:
        The sha256 of the tools, sorted and space-joined with flags
        dropped, or "" when the list names no tool.

    """
    tools = " ".join(sorted(w for w in install.split() if not w.startswith("-")))
    return hashlib.sha256(tools.encode()).hexdigest() if tools else ""


def names(
    name: str, env: dict[str, str], facts: Facts, install: str = ""
) -> dict[str, str]:
    """Compose the cache's key, fallback prefix and path.

    Returns:
        `key`, `prefix` and `dir`, as the workflow reads them.

    """
    image = env.get("ImageOS") or "self-hosted"
    prefix = f"{name}-{facts.plat}-{image}-"
    mise_env = env.get("MISE_ENV", "").replace(",", "-")
    period = int(facts.now // PERIOD_SECONDS)
    file_hash = env.get("ORG_MISE_CONFIG_HASH") or "no-config"
    menv = f"-{mise_env}" if mise_env else ""
    digest = install_digest(install)
    listed = f"-i{digest}" if digest else ""
    key = f"{prefix}{facts.version}{menv}-p{period}-{file_hash}{listed}-b{facts.belt}"
    left_out = env.get("ORG_GATE_BELT_DIGEST")
    if left_out:
        key += f"-off{left_out}"
    return {"key": key, "prefix": prefix, "dir": data_dir(env)}


RELEASE = re.compile(r"^release: \S+$", re.MULTILINE)

# The compiler the cargo caches hold, run by name from PATH (#1864).
RUSTC = "rustc"


def rustc_digest(verbose: str) -> str:
    """Digest a compiler's `rustc -vV` output, whole (#1864).

    Returns:
        Its sha256, in hex.

    Raises:
        ValueError: the text has no `release:` line, so it does not name
            a compiler and a key built from it would name nothing.

    """
    if not RELEASE.search(verbose):
        msg = f"`rustc -vV` printed no release line: {verbose.strip()!r}"
        raise ValueError(msg)
    return hashlib.sha256(verbose.encode()).hexdigest()


# How much of the digest a directory name carries (#1877): 64 bits, so
# two compilers colliding is not a case anyone meets.
DIGEST_HEX = 16


def directory_digest(verbose: str) -> str:
    """Shorten a compiler's digest for a directory name (#1877).

    Returns:
        The first DIGEST_HEX hex characters of `rustc_digest(verbose)`.

    """
    return rustc_digest(verbose)[:DIGEST_HEX]


def rustc_verbose() -> str:
    """Ask the `rustc` on PATH for its full version.

    Returns:
        What `rustc -vV` printed.

    Raises:
        ValueError: no `rustc` on PATH, or one that fails — a rustup
            proxy whose toolchain is not installed, say.

    """
    if shutil.which(RUSTC) is None:
        msg = (
            "no rustc on PATH: a cargo cache or a measurement directory"
            " must name the compiler that fills it"
        )
        raise ValueError(msg)
    done = subprocess.run([RUSTC, "-vV"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        msg = f"{RUSTC} -vV exited {done.returncode}: {done.stderr.strip()}"
        raise ValueError(msg)
    return done.stdout


class Args(argparse.Namespace):
    """The command line, typed where argparse writes it."""

    cache: str
    name: str
    canon: Path
    install: str


def parse(argv: list[str] | None) -> Args:
    """Read the command line.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(prog=NAME, description=__doc__.splitlines()[0])
    caches = parser.add_subparsers(dest="cache", required=True)
    tool = caches.add_parser("tool", help="name the tool cache (#1406)")
    tool.add_argument("name", help="the cache's hand-moved name (#1165)")
    tool.add_argument(
        "canon",
        type=Path,
        help="the canon tree: its ci.yml pins mise, its mise/mise.lock the belt",
    )
    tool.add_argument(
        "--install",
        default="",
        help="the job's install_args, for a job that installs a list (#1408)",
    )
    caches.add_parser("cargo", help="digest the compiler the cargo caches hold")
    caches.add_parser(
        "digest", help="print the compiler's short digest, for a directory (#1877)"
    )
    return parser.parse_args(argv, namespace=Args())


def write_outputs(env: dict[str, str], out: dict[str, str]) -> None:
    """Append a step's outputs to GITHUB_OUTPUT."""
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as fh:
        fh.write(lines)


def cargo(env: dict[str, str]) -> int:
    """Write the compiler's digest, for the cargo caches' keys (#1864).

    Returns:
        0, or 1 with the reason on stderr.

    """
    try:
        verbose = rustc_verbose()
        digest = rustc_digest(verbose)
    except (OSError, ValueError) as err:
        findings.finding("", str(err))
        return 1
    write_outputs(env, {"digest": digest})
    print(f"{NAME}: {digest} for {verbose.splitlines()[0]}")
    return 0


def digest() -> int:
    """Print the compiler's short digest, for a coverage directory (#1877).

    Returns:
        0, or 1 with the reason on stderr and nothing on stdout.

    """
    try:
        short = directory_digest(rustc_verbose())
    except (OSError, ValueError) as err:
        findings.finding("", str(err))
        return 1
    print(short)
    return 0


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    """Write a cache's names to GITHUB_OUTPUT.

    Returns:
        0, or 1 with the reason on stderr.

    """
    args = parse(argv)
    env = dict(os.environ)
    if args.cache == "cargo":
        return cargo(env)
    if args.cache == "digest":
        return digest()
    try:
        workflow = args.canon / ".github" / "workflows" / "ci.yml"
        version = mise_version(workflow.read_text("utf-8"))
        belt = belt_hash((args.canon / "mise" / "mise.lock").read_bytes())
        plat = target(platform.system(), platform.machine())
    except (OSError, ValueError) as err:
        findings.finding("", str(err))
        return 1
    clock = time.time() if now is None else now
    out = names(args.name, env, Facts(version, plat, clock, belt), args.install)
    write_outputs(env, out)
    print(f"{NAME}: {out['key']} (fallback {out['prefix']}, path {out['dir']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

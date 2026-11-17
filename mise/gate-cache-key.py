#!/usr/bin/env python3
"""Name the gate's tool cache: mise-action's key, plus the belt it installs.

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
"""

import argparse
import hashlib
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

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
    found = sorted(set(VERSION.findall(workflow)))
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
    return {"key": key, "prefix": prefix, "dir": data_dir(env)}


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    """Write the tool cache's names to GITHUB_OUTPUT.

    Returns:
        0, or 1 with the reason on stderr.

    """
    parser = argparse.ArgumentParser(prog=NAME, description=__doc__.splitlines()[0])
    parser.add_argument("name", help="the cache's hand-moved name (#1165)")
    parser.add_argument(
        "canon",
        type=Path,
        help="the canon tree: its ci.yml pins mise, its mise/mise.lock the belt",
    )
    parser.add_argument(
        "--install",
        default="",
        help="the job's install_args, for a job that installs a list (#1408)",
    )
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        workflow = args.canon / ".github" / "workflows" / "ci.yml"
        version = mise_version(workflow.read_text("utf-8"))
        belt = belt_hash((args.canon / "mise" / "mise.lock").read_bytes())
        plat = target(platform.system(), platform.machine())
    except (OSError, ValueError) as err:
        print(f"{NAME}: {err}", file=sys.stderr)
        return 1
    clock = time.time() if now is None else now
    out = names(args.name, env, Facts(version, plat, clock, belt), args.install)
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as fh:
        fh.write(lines)
    print(f"{NAME}: {out['key']} (fallback {out['prefix']}, path {out['dir']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

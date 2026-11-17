#!/usr/bin/env python3
"""Name the gate's tool cache the way jdx/mise-action v5.2.7 names it (#1406).

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

The fallback prefix stops before the mise version: a mise bump restores
the previous tools and the action installs the one binary that changed.
`name` is the hand-moved part of the key (#1165) and stays the
invalidation lever: a different name shares no prefix.
"""

import argparse
import os
import platform
import re
import sys
import time
from pathlib import Path

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


def names(
    name: str, version: str, plat: str, env: dict[str, str], now: float
) -> dict[str, str]:
    """Compose the cache's key, fallback prefix and path.

    Returns:
        `key`, `prefix` and `dir`, as the workflow reads them.

    """
    image = env.get("ImageOS") or "self-hosted"
    prefix = f"{name}-{plat}-{image}-"
    mise_env = env.get("MISE_ENV", "").replace(",", "-")
    period = int(now // PERIOD_SECONDS)
    file_hash = env.get("ORG_MISE_CONFIG_HASH") or "no-config"
    key = f"{prefix}{version}{'-' + mise_env if mise_env else ''}-p{period}-{file_hash}"
    return {"key": key, "prefix": prefix, "dir": data_dir(env)}


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    """Write the tool cache's names to GITHUB_OUTPUT.

    Returns:
        0, or 1 with the reason on stderr.

    """
    parser = argparse.ArgumentParser(prog=NAME, description=__doc__.splitlines()[0])
    parser.add_argument("name", help="the cache's hand-moved name (#1165)")
    parser.add_argument(
        "workflow", type=Path, help="the workflow whose install pins mise"
    )
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        version = mise_version(args.workflow.read_text("utf-8"))
        plat = target(platform.system(), platform.machine())
    except (OSError, ValueError) as err:
        print(f"{NAME}: {err}", file=sys.stderr)
        return 1
    out = names(args.name, version, plat, env, time.time() if now is None else now)
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as fh:
        fh.write(lines)
    print(f"{NAME}: {out['key']} (fallback {out['prefix']}, path {out['dir']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

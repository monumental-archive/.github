#!/usr/bin/env python3
"""What the helpers of the belt's Rust tasks share (#1911).

lint:msrv's body (mise/msrv_lint.py) and lint:rust's and fix:rust's
(mise/rust_lint.py) run the same steps around their own: the belt's
Python for its other helpers, a command with this process's streams or
read, a function of a belt shell script called through bash, the gate's
features, and the pgrx derivation and provisioning. Written once here,
so the helpers cannot drift apart on any of them.

The pgrx steps stay in mise/pgrx-env.sh, written once for every task
that needs them, shell and Python alike; this module calls its
functions through bash rather than restating them.

Imported, never run; stdlib only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import tracked

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BASH = shutil.which("bash") or "bash"
# The task shell's flags ([task_config] shell), for the belt functions
# this module calls: they ran under them in the bodies.
SHELL = [BASH, "--norc", "-euo", "pipefail", "-c"]
# Source a belt script, then call one of its functions with the rest.
CALL = '. "$1" && shift && "$@"'
# The same, then write every exported variable NUL-separated to a file:
# what a function exported for the calls after it (org_pgrx_darwin_prepare).
EXPORTS = (
    '. "$1" && out=$2 && shift 2 && "$@" && '
    '{ for name in $(compgen -e); do printf "%s=%s\\0" "${name}" "${!name}"; done; }'
    ' > "${out}"'
)
# What bash itself exports or rewrites in every shell, which no function
# exported for its callers.
BASH_OWN = frozenset({"SHLVL", "_", "PWD", "OLDPWD"})
POSTGRES = "aqua:theseus-rs/postgresql-binaries"


def belt() -> Path:
    """Name the belt the task runs from.

    ORG_BELT_DIR, which the belt's [env] sets for every task and the
    bodies read; an imported module's own path is the belt's real
    directory, never a planted one.

    Returns:
        The belt directory; this module's own where ORG_BELT_DIR is
        unset or empty.

    """
    return Path(os.environ.get("ORG_BELT_DIR") or Path(__file__).parent)


def python() -> str:
    """Name the belt's Python, which runs the belt's other helpers.

    Returns:
        ORG_PYTHON, as the bodies read it; this interpreter where it is
        unset.

    """
    return os.environ.get("ORG_PYTHON") or sys.executable


def name_of(spec: str) -> str:
    """Take a package spec's name, as bash's `${spec%@*}` does.

    Returns:
        The text before the last `@`; the whole spec when it has none.

    """
    head, at, _ = spec.rpartition("@")
    return head if at else spec


def run(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run a command with this process's streams.

    Returns:
        Its exit status.

    """
    return subprocess.run(argv, env=env, check=False).returncode


def output(argv: Sequence[str], stdin: str = "") -> tuple[int, str]:
    """Run a command, reading what it prints; its stderr is this process's.

    Returns:
        Its exit status and its standard output.

    """
    done = subprocess.run(
        argv, input=stdin, stdout=subprocess.PIPE, text=True, check=False
    )
    return done.returncode, done.stdout


def belt_call(script: str, *call: str, stdin: str = "") -> tuple[int, str]:
    """Call a function of a belt shell script.

    Returns:
        Its exit status and its standard output.

    """
    return output([*SHELL, CALL, "belt", str(belt() / script), *call], stdin)


def belt_exports(script: str, *call: str) -> tuple[int, dict[str, str]]:
    """Call a belt function that exports, and take what it exported.

    Returns:
        Its exit status, and every variable it set or changed in the
        environment, for the commands that run after it.

    """
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "exports"
        status = run([*SHELL, EXPORTS, "belt", str(belt() / script), str(dump), *call])
        if status != 0:
            return status, {}
        listing = dump.read_bytes()
    exported: dict[str, str] = {}
    for entry in tracked.split(listing):
        key, _, value = entry.partition("=")
        if key not in BASH_OWN and os.environ.get(key) != value:
            exported[key] = value
    return 0, exported


def features() -> list[str]:
    """Take the features every crate is compiled with.

    CLIPPY_FEATURES lets a repo whose features are mutually exclusive
    say so; everything else gets --all-features, because an unlinted
    feature is unlinted code. The compile surface is the gate's, stated
    once: lint:msrv reads the same variable rather than a twin.

    Returns:
        The flag words, split as bash splits an unquoted value.

    """
    return (os.environ.get("CLIPPY_FEATURES") or "--all-features").split()


def pgrx_pairs(found: str) -> tuple[int, list[list[str]], str]:
    """Take each extension's major, and the majors to register (#1795).

    From the belt's one derivation, mise/pgrx-env.sh: every task builds
    each extension at its highest major. One step per assignment, so a
    failed derivation is never an empty list.

    Returns:
        The status, `[spec, major]` per extension, and the majors.

    """
    status, highest = belt_call("pgrx-env.sh", "org_pgrx_highest", stdin=found)
    if status != 0:
        return status, [], ""
    status, majors = belt_call("pgrx-env.sh", "org_pgrx_union", stdin=highest)
    pairs = [line.split("\t") for line in highest.splitlines()]
    return status, [pair for pair in pairs if len(pair) > 1 and pair[0]], majors.strip()


def register(majors: str) -> tuple[int, dict[str, str]]:
    """Ready this machine to build the extensions, before anything compiles.

    The SDK, and the ICU headers and Postgres pin of every major the
    pass builds, checked first (#1774, #1940).

    Returns:
        The status, and what org_pgrx_darwin_prepare exported for the
        compiles after it.

    """
    status, prepared = belt_exports("pgrx-env.sh", "org_pgrx_darwin_prepare", majors)
    if status != 0:
        return status, {}
    listed, installed = output([
        shutil.which("mise") or "mise",
        "ls",
        POSTGRES,
        "--json",
    ])
    checked, _ = output(
        [python(), str(belt() / "pgrx_postgres.py"), "--majors", majors], installed
    )
    return checked or listed, prepared


def at_major(major: str, argv: Sequence[str], prepared: Mapping[str, str]) -> int:
    """Run one compile in a shell holding its own major's headers and Postgres.

    org_pgrx_major's subshell ends with the command, so the next major
    starts from the caller's environment (#1774, #1940). An empty major
    runs the command as it is.

    Returns:
        The command's status.

    """
    pgrx_env = str(belt() / "pgrx-env.sh")
    shell = [*SHELL, CALL, "belt", pgrx_env, "org_pgrx_major", major]
    return run([*shell, *argv], {**os.environ, **prepared})

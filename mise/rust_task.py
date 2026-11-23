#!/usr/bin/env python3
"""What the helpers of the belt's Rust tasks share (#1911).

lint:msrv's body (mise/msrv_lint.py) and lint:rust's and fix:rust's
(mise/rust_lint.py) run the same steps around their own: the belt and
its Python for its other helpers, a command with this process's streams
or read, a function of a belt shell script called through bash, the
gate's features and compile surface, the installed components, the
clippy ladder, the pgrx derivation and provisioning, and the tree's
workspaces from mise/rust_workspaces.py (#2004). Written once
here, so the helpers cannot drift apart on any of them.

The ladder stays in mise/clippy-ladder.sh and the pgrx steps in
mise/pgrx-env.sh, each written once for every task that needs it, shell
and Python alike; this module calls their functions through bash rather
than restating them.

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

import findings
import rust_workspaces
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
PIN = 'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }'
NO_CARGO = "Cargo.toml present but cargo missing — pin rust in mise.toml"


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


def installed_components() -> list[str]:
    """List the rustup components the repository's own pin installed.

    Returns:
        One line per component; none when rustup is absent or fails.

    """
    rustup = shutil.which("rustup")
    if rustup is None:
        return []
    done = subprocess.run(
        [rustup, "component", "list", "--installed"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return done.stdout.splitlines() if done.returncode == 0 else []


def has(components: list[str], name: str) -> bool:
    """Say whether a component is installed, as `grep -q "^<name>"` does.

    Returns:
        True when a line starts with the name.

    """
    return any(line.startswith(name) for line in components)


def ladder() -> tuple[int, list[str], str]:
    """Read the org's one clippy ladder, from mise/clippy-ladder.sh.

    THE LADDER LIVES IN THE BELT'S clippy-ladder.sh (#813). It moved the
    day a SECOND task acquired the same obligation: `lint:pg-clippy`
    sets this exact level on the pgrx crate the gate never compiled, and
    a second copy of a ten-name list is the drift #752 recorded — the
    list said ten and the prose above it said nine.

    Returns:
        The status, the flags that ride clippy's `--`, and the count of
        named contradictions.

    """
    status, flags = belt_call("clippy-ladder.sh", "org_clippy_ladder")
    if status != 0:
        return status, [], ""
    status, count = belt_call("clippy-ladder.sh", "org_clippy_contradiction_count")
    return status, flags.split(), count.strip()


def surface(cargo: str, where: Sequence[str] = ()) -> tuple[int, str]:
    """Derive the workspace's compile surface, the belt's one derivation (#813).

    gate_surface.py reads cargo's own account — `workspace_members`, not
    `workspace_default_members` — and classifies each member, so
    lint:rust, fix:rust, lint:pg-clippy, test:pgrx and lint:msrv cannot
    disagree about which crates exist or what kind each one is. `where`
    is cargo's `--manifest-path` naming the workspace; empty, cargo
    finds the one at the working directory.

    Returns:
        The status (the helper's when it failed, else cargo's, as
        pipefail reads the pipe) and the records.

    """
    meta_status, meta = output([
        cargo,
        "metadata",
        "--no-deps",
        "--format-version",
        "1",
        "--locked",
        *where,
    ])
    exclude = os.environ.get("CLIPPY_EXCLUDE", "")
    status, records = output(
        [python(), str(belt() / "gate_surface.py"), "--exclude", exclude], meta
    )
    return status or meta_status, records


def workspace_roots() -> list[str] | None:
    """Name the tree's workspaces, from the belt's one derivation (#2004).

    Returns:
        Each root manifest, the top one first; None after a finding.

    """
    try:
        return rust_workspaces.roots()
    except rust_workspaces.UnresolvedError:
        return None
    except subprocess.CalledProcessError as exc:
        # git could not name the tree's top: the derivation's own words.
        said: object = exc.stderr
        text = os.fsdecode(said) if isinstance(said, bytes) else str(said)
        findings.finding("", "git could not name the tree", text.strip().splitlines())
        return None

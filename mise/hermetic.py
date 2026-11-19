#!/usr/bin/env python3
"""The one environment a test fixture is given (#1508, #1514).

A fixture's process inherits from the suite's only what INHERITED names,
plus what a named tool extension in TOOLS adds, then HOME, a git
identity and the row's own overrides. Nothing else reaches it, so no
script input arrives from the job the suite happens to run in: the
release job exports `VERSION`, and a row asserting "VERSION must be set"
inherited it and the canon could not tag (#1508). git's own variables
are never inherited either (#857): under a hook git exports `GIT_DIR`,
and a fixture that inherits it writes into the real repository.

    env = hermetic.env(home)                       # a fixture HOME
    env = hermetic.env(tools=("mise",), FOO="1")   # the caller's HOME

An ALLOW-list on purpose. The deny-lists it replaced (drop `GIT_*`,
drop the forge's and the runner's) kept everything else, and missed
the next variable a job exported. A row that needs a variable states
it in its overrides; a tool that needs one gets it in its extension,
with why. stdlib only; imported by every test file in the four
discovery roots, which `test_hermetic.py` holds them to.

The files a tree tracks are read here too, through the same
environment (#1618):

    names = hermetic.tracked(CANON, "*.py")        # relative to CANON

A test that lists the canon's own tree calls `tracked`, never `git
ls-files` in the tree: mutmut runs the suite in `mutants/`, a gitignored
copy of the tree, where git lists nothing under the copy and every such
test failed the statistics pass (self-mutants run 44597898554).
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# What every fixture inherits, by name, and why.
INHERITED: dict[str, str] = {
    # Finding bash, git, jq and the belt's tools at all.
    "PATH": "the commands a script runs",
    # mktemp and every tool that writes scratch.
    "TMPDIR": "where scratch files go",
    # The scripts print and match UTF-8 (em-dashes in their messages);
    # under the C locale bash and grep treat them byte-wise.
    "LANG": "the character set messages are read in",
    "LC_ALL": "the character set messages are read in",
    "LC_CTYPE": "the character set messages are read in",
    # The Rust toolchain's own homes: cargo and rustup find their
    # installed toolchains here, and a fixture HOME would hide them.
    "CARGO_HOME": "where cargo's toolchain lives",
    "RUSTUP_HOME": "where rustup's toolchains live",
    "RUSTUP_TOOLCHAIN": "the toolchain mise selected for this run",
    # The coverage measurement reaches the helpers a fixture runs as
    # `python3 helper.py` through these two (#1331): a sitecustomize on
    # PYTHONPATH starts coverage in every python3 that names its rc file.
    # Without them every helper a suite execs runs unmeasured and the
    # branch floor falls (measured: 86.75% against 89.1%).
    "COVERAGE_PROCESS_START": "where coverage's child-process rc file is",
    "PYTHONPATH": "the coverage start-up hook, when the suite is measured",
    # The belt's Python, which every script that starts a helper runs it
    # under and refuses to run without (#1543): the canon action exports
    # it in CI, the belt's [env] locally.
    "ORG_PYTHON": "the interpreter a script starts its helpers under",
}

# What a tool needs beyond INHERITED, per tool, each with why. A row
# names the tools its fixture runs; nothing here reaches a row that
# does not.
TOOLS: dict[str, dict[str, str]] = {}

IDENTITY_NAME = "hermetic harness"
IDENTITY_EMAIL = "harness@example.invalid"


def env(
    home: Path | str | None = None,
    *,
    tools: Iterable[str] = (),
    **overrides: str,
) -> dict[str, str]:
    """Build a fixture's environment from the allow-list.

    `home` is the fixture's HOME. Without one the fixture keeps the
    suite's HOME, which is where mise, cargo and go keep what they have
    installed; a row that needs a home of its own passes one.

    Returns:
        INHERITED's variables and each named tool's, HOME, a git
        identity, and the overrides applied last.

    A tool no extension names is a KeyError: an unmeasured tool has
    nothing to borrow.

    """
    names = dict(INHERITED)
    for tool in tools:
        names |= TOOLS[tool]
    out = {name: os.environ[name] for name in names if name in os.environ}
    if home is not None:
        out["HOME"] = str(home)
    elif "HOME" in os.environ:
        out["HOME"] = os.environ["HOME"]
    out |= {
        "GIT_EDITOR": "true",
        "GIT_AUTHOR_NAME": IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
    }
    out |= overrides
    return out


def top(pathspec: str) -> str:
    """Anchor one pathspec at the repository's top, keeping its magic.

    Returns:
        `:(top)` added to the pathspec: `*.py` is `:(top)*.py`, the
        short exclude `:!x` is `:(top,exclude)x`, and a long-form
        `:(glob)x` is `:(top,glob)x`.

    """
    if pathspec.startswith((":!", ":^")):
        return f":(top,exclude){pathspec[2:]}"
    if pathspec.startswith(":("):
        return f":(top,{pathspec[2:]}"
    return f":(top){pathspec}"


def tracked(root: Path | str, *pathspecs: str, **overrides: str) -> list[str]:
    """Name the files a tree tracks, relative to the tree's root (#1618).

    `root` is a checkout's top, or a copy of the whole tree inside it
    (mutmut's `mutants/`). git is asked from the repository's top, with
    every pathspec anchored there and the names in full, so a copy gets
    the tracked names; a name is kept when the file exists under `root`,
    so a copy is judged on what was copied into it. In a checkout that
    is exactly `git ls-files` at its top. The listing runs under `env`,
    with the row's `overrides`.

    Returns:
        The names, in git's order.

    Raises:
        RuntimeError: git is not on PATH, or lists no file at all: an
            empty listing where files were asked for is never a pass.

    """
    git = shutil.which("git")
    if git is None:
        msg = "hermetic.tracked: git is not on PATH"
        raise RuntimeError(msg)
    # ruff: ignore[subprocess-without-shell-equals-true]
    listed = subprocess.run(
        [
            git,
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--full-name",
            "--",
            *(top(spec) for spec in pathspecs),
        ],
        env=env(**overrides),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = [n for n in listed.split("\0") if n and os.path.lexists(Path(root, n))]
    if not names:
        msg = (
            f"hermetic.tracked: git lists no tracked file under {root}"
            f" for {list(pathspecs) or ['(every file)']}"
        )
        raise RuntimeError(msg)
    return names

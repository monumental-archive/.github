"""The suite's utilities, one module (#1508, #1514, #1684, #1714, #2177).

The rule that holds every test, its process environment cleared and its
directory held, is tests/conftest.py's hooks. What is here is what a
test asks for: the environment a process it starts is given, the tools
it runs, the trees it plants, and a task body run as mise runs it.

THE ENVIRONMENT A STARTED PROCESS IS GIVEN. A fixture's process inherits from the suite's only what INHERITED names,
plus what a named tool extension in TOOLS adds, then HOME, a git
identity and the row's own overrides. Nothing else reaches it, so no
script input arrives from the job the suite happens to run in: the
release job exports `VERSION`, and a row asserting "VERSION must be set"
inherited it and the canon could not tag (#1508). git's own variables
are never inherited either (#857): under a hook git exports `GIT_DIR`,
and a fixture that inherits it writes into the real repository.

    def test_x(env: support.Env) -> None:
        environ = env(home)                       # a fixture HOME
        environ = env(tools=("mise",), FOO="1")   # the caller's HOME

An ALLOW-list on purpose. The deny-lists it replaced (drop `GIT_*`,
drop the forge's and the runner's) kept everything else, and missed
the next variable a job exported. A row that needs a variable states
it in its overrides; a tool that needs one gets it in its extension,
with why. tests/conftest.py's hooks clear every test's own process to
this same environment, unasked; the `env` fixture (conftest.py, built
by `environment` here) is for the processes a test starts: git, stele,
a shell body, a tool.

The files a tree tracks are read here too, through the same
environment (#1618):

    names = support.tracked(CANON, "*.py")        # relative to CANON
    support.track(tree)                           # plant, then track it all

A test that lists the canon's own tree calls `tracked`, never `git
ls-files` itself, so every listing runs under the same environment and
an empty one is red by name.

A row that runs a belt task's body runs it as mise does (#1934):

    run = support.task_run("lint:dco", body)
    subprocess.run(run.argv, env=env(home) | run.env, ...)

A test finds the tools it runs here too (#1684):

    BASH = support.tool("bash")                  # absolute, or raises
    DASH = support.optional_tool("dash")         # None when absent

`tool` is for a tool the row needs: a missing one is red by name at the
lookup, never a `TypeError` in `subprocess` three frames later, and
never a bare name the child's own PATH resolves to something else.
`optional_tool` is for a lookup whose absence is a decision the row
makes: a skip, or a planted "tool missing". No test calls
`shutil.which` itself; `test_support.py` holds them to that.

A test that needs a belt or a canon tree of its own gets the whole one
(#1714) from the conftest fixtures `belt` and `canon`, every tracked file
a link, with only what it stubs replaced (`track` makes a planted tree a
repository, `plant` links any tracked names, `rustc` writes the stand-in
compiler; each typed here):

    belt_dir = belt(tmp / "tree", stubs={"pgrx-env.sh": STUB})
    canon(work / ".org-canon")

A belt is planted as the tree it is delivered in: `mise/` and the
`belt/` package beside it (#2177), so a script that finds the package
beside its own directory finds the planted one. `belt` returns the
`mise/` directory, ORG_BELT_DIR; its parent is ORG_CANON_DIR. A stub
named `belt/<module>.py` replaces a module of the planted package.

A hand-kept list of the files a helper reads goes stale the day it
gains an import or starts a sibling. No test links or copies a path
from the belt or the canon itself; `test_support.py` holds them to it.

A row that runs a coverage script gets a stand-in compiler (#1877):

    rustc(stubs)                                  # stubs/rustc
    support.rustc_verbose("nightly-2026-07-20")   # what it prints

Every coverage directory ends in a digest of `rustc -vV`, so the name is
the row's own fact only if the compiler is: the suite's own, or none on
a machine without Rust, would make it the machine's. The stand-in
answers for RUSTUP_TOOLCHAIN, `stable` when unset, as the rustup proxy
selects, so a leg's per-command toolchain reaches it.
"""

import ast
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from belt import decoded, launcher
from belt.belt_scripts import script

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


# The canon this module is part of: its checkout, and the directories
# a test reads the delivered files from (#2177): the belt's own (task
# bodies, tool configs, tables), the release scripts, and the package.
CANON = Path(__file__).resolve().parent.parent
MISE = CANON / "mise"
RELEASE = CANON / "release"
PACKAGE = CANON / "belt"

# A belt command as a call site names it (#2177): the launcher's
# directory, then the command, `"${ORG_PYTHON}" -I "${ORG_CANON_DIR}/belt"
# subject-budget`. The directory may be spelled any way that ends `/belt`.
LAUNCHED = re.compile(r'/belt"?\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\b')


def launched(text: str) -> list[str]:
    """Name every belt command a text runs through the launcher.

    Returns:
        The commands, in text order.

    """
    return [m.group(1) for m in LAUNCHED.finditer(text)]


def command_module(command: str, package: Path = PACKAGE) -> Path | None:
    """Find the module `belt <command>` runs, as the launcher searches.

    Returns:
        Its file under `package`, or None when no module answers.

    """
    stem = command.replace("-", "_")
    for name in launcher.PACKAGES:
        path = package.joinpath(*name.split(".")[1:], f"{stem}.py")
        if path.is_file():
            return path
    return None


def belt_modules(source: str, package: Path = PACKAGE) -> list[Path]:
    """Name the belt modules a module's text imports, as files.

    `from belt import x`, `from belt.release import y`, `from belt.x import
    name` and `import belt.x` each name the module's file under `package`.

    Returns:
        The files, each once, in import order.

    """
    out: list[Path] = []
    for node in ast.walk(ast.parse(source)):
        dotted: list[str] = []
        if isinstance(node, ast.Import):
            dotted = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            dotted = [f"{node.module}.{alias.name}" for alias in node.names]
            dotted.append(node.module)
        for name in dotted:
            parts = name.split(".")
            if parts[0] != "belt" or len(parts) == 1:
                continue
            path = package.joinpath(*parts[1:]).with_suffix(".py")
            if path.is_file() and path not in out:
                out.append(path)
    return out


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
    # The belt's Python, which every script that starts a helper runs it
    # under and refuses to run without (#1543): the canon action exports
    # it in CI, the belt's [env] locally.
    "ORG_PYTHON": "the interpreter a script starts its helpers under",
}

# What a tool needs beyond INHERITED, per tool, each with why. A row
# names the tools its fixture runs; nothing here reaches a row that
# does not.
TOOLS: dict[str, dict[str, str]] = {
    # The PL/pgSQL leg's real database (#2163) runs in a container: the
    # client finds its daemon through these, where they are set.
    "docker": {
        "DOCKER_HOST": "the daemon the client talks to",
        "DOCKER_CONTEXT": "the context that names the daemon",
        "DOCKER_CONFIG": "where the client's configuration lives",
    },
}

IDENTITY_NAME = "hermetic harness"
IDENTITY_EMAIL = "harness@example.invalid"


class MissingToolError(RuntimeError):
    """A tool a test needs is not on the search path (#1684)."""


def optional_tool(name: str, *, path: str | None = None) -> str | None:
    """Find a tool whose absence the row decides on.

    `path` is the search path; without one, the suite's PATH.

    Returns:
        The tool's absolute path, or None when it is not found.

    """
    found = shutil.which(name, path=path)
    return None if found is None else os.path.normpath(Path(found).absolute())


def tool(name: str, *, path: str | None = None) -> str:
    """Find a tool the row needs, or say which one is missing.

    `path` is the search path; without one, the suite's PATH. The path
    is absolute, not resolved: a mise shim is a link that dispatches on
    its own name.

    Returns:
        The tool's absolute path.

    Raises:
        MissingToolError: the tool is not on the search path.

    """
    found = optional_tool(name, path=path)
    if found is None:
        where = "PATH" if path is None else f"the search path {path!r}"
        msg = (
            f"support.tool: {name} is not on {where};"
            " the belt provides it: run mise install"
        )
        raise MissingToolError(msg)
    return found


def environment(
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
        identity, and the overrides applied last; an ORG_BELT_DIR among
        them brings its ORG_CANON_DIR, unless they name one.

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
    # The package sits beside the belt directory, as the belt's [env]
    # derives it (#2177): the canon's own, unless the row names a belt,
    # which brings its root with it.
    out["ORG_CANON_DIR"] = str(CANON)
    out |= overrides
    belt_dir = overrides.get("ORG_BELT_DIR")
    if belt_dir and "ORG_CANON_DIR" not in overrides:
        out["ORG_CANON_DIR"] = os.path.dirname(belt_dir.rstrip("/"))
    return out


@dataclass(frozen=True)
class TaskRun:
    """How mise runs one task body: the command line and what it adds."""

    argv: list[str]
    env: dict[str, str]


def task_run(name: str, body: str) -> TaskRun:
    """Give a harness what mise gives a belt task body (#1934).

    `argv` is the belt's `[task_config]` shell, read from the belt's
    config and split as mise splits it, its interpreter found by `tool`,
    then the body. `env` is what a row merges into its environment:
    MISE_TASK_NAME, the task's name, which the finding writer reads
    (#1890), and BASH_ENV, the belt's findings.sh, which the belt's
    `[env]` names so every body has `org_finding`. No SHLVL: the shell's
    `--norc` is what lets BASH_ENV through without one.

    Returns:
        The command line and the variables.

    """
    config = decoded.load_toml((CANON / "mise" / "config.toml").read_bytes())
    table = decoded.as_object(config["task_config"], "task_config")
    words = shlex.split(decoded.as_str(table["shell"], "task_config.shell"))
    return TaskRun(
        [tool(words[0]), *words[1:], body],
        {
            "MISE_TASK_NAME": name,
            "BASH_ENV": str(script(CANON / "mise", "findings.sh")),
        },
    )


def tracked(root: Path | str, *pathspecs: str, **overrides: str) -> list[str]:
    """Name the files a tree tracks, relative to the tree's root (#1618).

    `root` is a checkout's top: the listing is git's NUL-separated one
    there, as mise/tracked.py reads it, for the pathspecs given, under
    `env` with the row's `overrides`.

    Returns:
        The names, in git's order.

    Raises:
        RuntimeError: git is not on PATH, or lists no file at all: an
            empty listing where files were asked for is never a pass.

    """
    git = shutil.which("git")
    if git is None:
        msg = "support.tracked: git is not on PATH"
        raise RuntimeError(msg)
    listed = subprocess.run(
        [
            git,
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--",
            *pathspecs,
        ],
        env=environment(**overrides),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = [n for n in listed.split("\0") if n]
    if not names:
        msg = (
            f"support.tracked: git lists no tracked file under {root}"
            f" for {list(pathspecs) or ['(every file)']}"
        )
        raise RuntimeError(msg)
    return names


def rustc_verbose(toolchain: str = "stable") -> str:
    """Render what the stand-in `rustc -vV` prints for a toolchain (#1877).

    Returns:
        The text: only its release line names the toolchain.

    """
    return (
        "rustc (hermetic stand-in)\nbinary: rustc\nhost: x86_64-unknown-linux-gnu\n"
        f"release: {toolchain}\nLLVM version: 21.1.2\n"
    )


# The stand-in's script: rustc_verbose's text, for RUSTUP_TOOLCHAIN.
RUSTC_STUB = """#!/bin/sh
t=${RUSTUP_TOOLCHAIN:-stable}
printf 'rustc (hermetic stand-in)\\nbinary: rustc\\nhost: x86_64-unknown-linux-gnu\\n'
printf 'release: %s\\nLLVM version: 21.1.2\\n' "$t"
"""


class Env(Protocol):
    """The `env` fixture: `environment`, the one a started process gets."""

    def __call__(
        self,
        home: Path | str | None = None,
        *,
        tools: Iterable[str] = (),
        **overrides: str,
    ) -> dict[str, str]:
        """Build an environment; see `environment`."""
        ...


class Belt(Protocol):
    """The `belt` fixture: plant a whole belt tree, return its `mise/`."""

    def __call__(
        self, dest: Path | str, *, stubs: Mapping[str, str] | None = None
    ) -> Path:
        """Plant the belt at `dest`."""
        ...


class Canon(Protocol):
    """The `canon` fixture: plant a canon tree and what `also` names."""

    def __call__(
        self, dest: Path | str, *also: str, stubs: Mapping[str, str] | None = None
    ) -> Path:
        """Plant the canon at `dest`."""
        ...


class Track(Protocol):
    """The `track` fixture: make a tree a repository tracking its files."""

    def __call__(self, tree: Path | str) -> None:
        """Track every file under `tree`."""
        ...


class Rustc(Protocol):
    """The `rustc` fixture: write the stand-in compiler into a directory."""

    def __call__(self, bin_dir: Path) -> Path:
        """Write `bin_dir/rustc`."""
        ...


class Plant(Protocol):
    """The `plant` fixture: link tracked names into a tree, then the stubs."""

    def __call__(self, dest: Path, names: list[str], stubs: Mapping[str, str]) -> Path:
        """Plant `names` and `stubs` at `dest`."""
        ...

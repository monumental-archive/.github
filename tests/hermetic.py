"""The one environment a test fixture is given (#1508, #1514, #2177).

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
with why. tests/conftest.py clears every test's own process to this
same environment, unasked; `env()` is for the processes a test starts.

The files a tree tracks are read here too, through the same
environment (#1618):

    names = hermetic.tracked(CANON, "*.py")        # relative to CANON
    hermetic.track(tree)                           # plant, then track it all

A test that lists the canon's own tree calls `tracked`, never `git
ls-files` itself, so every listing runs under the same environment and
an empty one is red by name.

A row that runs a belt task's body runs it as mise does (#1934):

    run = hermetic.task_run("lint:dco", body)
    subprocess.run(run.argv, env=hermetic.env(home) | run.env, ...)

A test finds the tools it runs here too (#1684):

    BASH = hermetic.tool("bash")                  # absolute, or raises
    DASH = hermetic.optional_tool("dash")         # None when absent

`tool` is for a tool the row needs: a missing one is red by name at the
lookup, never a `TypeError` in `subprocess` three frames later, and
never a bare name the child's own PATH resolves to something else.
`optional_tool` is for a lookup whose absence is a decision the row
makes: a skip, or a planted "tool missing". No test calls
`shutil.which` itself; `test_hermetic.py` holds them to that.

A test calls a helper's `main` in its own process here too (#1673,
#2177), as a script row once ran it as a child:

    done = hermetic.call(lambda: helper.main(["--flag"]), env=env, cwd=tree)

The environment, working directory, standard input and `sys.argv` a
child would have had are this process's for the call, then restored, so
coverage and mutation see the helper run. `done` carries the status and
both streams, as a finished process does; a `SystemExit` is the status.
The `call_main` fixture is the same caller, and `as_task` names the task
a helper runs under (tests/conftest.py, #1934).

A test that needs a belt or a canon tree of its own gets the whole one
(#1714), every tracked file a link, with only what it stubs replaced:

    belt = hermetic.belt(tmp / "tree", stubs={"pgrx-env.sh": STUB})
    hermetic.canon(work / ".org-canon")

A belt is planted as the tree it is delivered in: `mise/` and the
`belt/` package beside it (#2177), so a script that finds the package
beside its own directory finds the planted one. `belt` returns the
`mise/` directory, ORG_BELT_DIR; its parent is ORG_BELT_ROOT. A stub
named `belt/<module>.py` replaces a module of the planted package.

A hand-kept list of the files a helper reads goes stale the day it
gains an import or starts a sibling. No test links or copies a path
from the belt or the canon itself; `test_hermetic.py` holds them to it.

A row that runs a coverage script gets a stand-in compiler (#1877):

    hermetic.rustc(stubs)                          # stubs/rustc
    hermetic.rustc_verbose("nightly-2026-07-20")   # what it prints

Every coverage directory ends in a digest of `rustc -vV`, so the name is
the row's own fact only if the compiler is: the suite's own, or none on
a machine without Rust, would make it the machine's. The stand-in
answers for RUSTUP_TOOLCHAIN, `stable` when unset, as the rustup proxy
selects, so a leg's per-command toolchain reaches it.
"""

import io
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from belt import decoded
from belt.belt_scripts import script

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, MutableMapping

    import pytest

# The canon this module is part of: its checkout, and the directories
# a test reads the delivered files from (#2177): the belt's own (task
# bodies, tool configs, tables), the release scripts, and the package.
CANON = Path(__file__).resolve().parent.parent
MISE = CANON / "mise"
RELEASE = CANON / "release"
PACKAGE = CANON / "belt"

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
            f"hermetic.tool: {name} is not on {where};"
            " the belt provides it: run mise install"
        )
        raise MissingToolError(msg)
    return found


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
        identity, and the overrides applied last; an ORG_BELT_DIR among
        them brings its ORG_BELT_ROOT, unless they name one.

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
    # The package sits beside the belt directory, as the belt's [env]
    # derives it (#2177): a row that names a belt names its root too.
    belt_dir = overrides.get("ORG_BELT_DIR")
    if belt_dir and "ORG_BELT_ROOT" not in overrides:
        out["ORG_BELT_ROOT"] = os.path.dirname(belt_dir.rstrip("/"))
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
        msg = "hermetic.tracked: git is not on PATH"
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
        env=env(**overrides),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = [n for n in listed.split("\0") if n]
    if not names:
        msg = (
            f"hermetic.tracked: git lists no tracked file under {root}"
            f" for {list(pathspecs) or ['(every file)']}"
        )
        raise RuntimeError(msg)
    return names


def track(tree: Path | str) -> None:
    """Make `tree` a git repository tracking every file in it (#2007).

    A belt task reads the files git tracks, never a directory's walk, so
    a row that plants files for a task body to read tracks them, under
    `env`. Idempotent: a tree already a repository is added to.

    Raises:
        RuntimeError: git is not on PATH.

    """
    git = shutil.which("git")
    if git is None:
        msg = "hermetic.track: git is not on PATH"
        raise RuntimeError(msg)
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(
            [git, "-C", str(tree), *args], env=env(), capture_output=True, check=True
        )


@dataclass(frozen=True)
class Called:
    """What an in-process call to a command's `main` returned and wrote."""

    returncode: int
    stdout: str
    stderr: str


def _stream(raw: io.BytesIO) -> io.TextIOWrapper:
    """Make a text stream over bytes, as a process's standard streams are.

    Returns:
        A UTF-8 stream with a `.buffer`, writing through, newlines as given.

    """
    return io.TextIOWrapper(
        raw,
        encoding="utf-8",
        errors="backslashreplace",
        newline="",
        write_through=True,
    )


def call(
    run: Callable[[], int],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    stdin: str | bytes = "",
    command: str | None = None,
) -> Called:
    """Call a command's `main` in this process, as a child once ran it.

    `run` makes the call, written out in the test (`lambda:
    helper.main([...])`). `env`, when given, IS the process environment
    for the call; `cwd` the working directory; `stdin` what it reads;
    `command` is `sys.argv[0]`, the name a finding falls back to. Each
    is restored afterwards.

    Returns:
        The status and what was written to each stream.

    """
    out_raw, err_raw = io.BytesIO(), io.BytesIO()
    out, err = _stream(out_raw), _stream(err_raw)
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    saved_env = dict(os.environ)
    saved = (sys.stdin, sys.stdout, sys.stderr, list(sys.argv), os.getcwd())
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        if cwd is not None:
            os.chdir(cwd)
        sys.stdin, sys.stdout, sys.stderr = _stream(io.BytesIO(data)), out, err
        sys.argv = [command or "belt"]
        try:
            status = run()
        except SystemExit as exc:
            code = exc.code
            status = code if isinstance(code, int) else (0 if code is None else 1)
            if isinstance(code, str):
                print(code, file=sys.stderr)
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv, here = saved
        os.chdir(here)
        os.environ.clear()
        os.environ.update(saved_env)
    out.flush()
    err.flush()
    return Called(
        status,
        out_raw.getvalue().decode("utf-8", "backslashreplace"),
        err_raw.getvalue().decode("utf-8", "backslashreplace"),
    )


def patch_env(
    patch: pytest.MonkeyPatch, values: Mapping[str, str], *, clear: bool = False
) -> None:
    """Set the process environment for a patch's life, as a row needs it.

    `clear` empties it first, so `values` is the whole environment.
    """
    if clear:
        for name in list(os.environ):
            patch.delenv(name)
    for name, value in values.items():
        patch.setenv(name, value)


def patch_dict(
    patch: pytest.MonkeyPatch,
    target: MutableMapping[str, object],
    values: Mapping[str, object] | None = None,
    *,
    clear: bool = False,
) -> None:
    """Set a dictionary's items for a patch's life; `clear` empties it first."""
    if clear:
        for key in list(target):
            patch.delitem(target, key)
    for key, value in (values or {}).items():
        patch.setitem(target, key, value)


def name_task(patch: pytest.MonkeyPatch, task: str) -> None:
    """Name the task a helper runs under, recording nothing (#1934).

    The finding writer reads MISE_TASK_NAME and ORG_FINDINGS. An empty
    name is a workflow step's: the writer falls back to the command's
    name. An empty ORG_FINDINGS records nothing.
    """
    patch.setenv("MISE_TASK_NAME", task)
    patch.setenv("ORG_FINDINGS", "")


def raising(error: BaseException | type[BaseException]) -> Callable[..., object]:
    """Make a stand-in that raises, whatever it is called with.

    Returns:
        The stand-in.

    """

    def stand_in(*_args: object, **_kwargs: object) -> object:
        raise error

    return stand_in


def returning(value: object) -> Callable[..., object]:
    """Make a stand-in that returns one value, whatever it is called with.

    Returns:
        The stand-in.

    """

    def stand_in(*_args: object, **_kwargs: object) -> object:
        return value

    return stand_in


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


def rustc(bin_dir: Path) -> Path:
    """Write the stand-in `rustc` into a row's stub directory (#1877).

    Returns:
        Its path.

    """
    stub = bin_dir / "rustc"
    stub.write_text(RUSTC_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def plant(dest: Path, names: list[str], stubs: Mapping[str, str]) -> Path:
    """Link each tracked name into `dest`, then write each stub over its link.

    Names are paths from the canon's top, and so are the stubs'. A stub
    replaces its link with a file of its own, so a row that edits it
    never writes through to the canon; it keeps the real file's mode,
    and a stub with no real file has the default mode.

    Returns:
        `dest`.

    """
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        link = dest / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(CANON / name)
    for name, text in stubs.items():
        path = dest / name
        real = CANON / name
        path.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if real.exists():
            path.chmod(real.stat().st_mode & 0o7777)
    return dest


def belt(dest: Path | str, *, stubs: Mapping[str, str] | None = None) -> Path:
    """Make `dest` a whole belt tree: the canon's `mise/` and `belt/` (#1714, #2177).

    Each file is a symbolic link to the real one, so a helper that gains
    an import, or a script that starts a new sibling, finds it with no
    change to the row. `stubs` (a name under `mise/`, or `belt/<path>`
    for the package, to its text) are what the row means to replace; a
    row whose point is a file MISSING removes it after planting.

        belt = hermetic.belt(tmp / "tree", stubs={"pgrx-env.sh": STUB})

    Returns:
        `dest/mise`, the planted belt directory (ORG_BELT_DIR); `dest`
        is its ORG_BELT_ROOT.

    """
    names = tracked(CANON, "mise/", "belt/")
    named = {
        (name if name.startswith("belt/") else f"mise/{name}"): text
        for name, text in (stubs or {}).items()
    }
    return plant(Path(dest), names, named) / "mise"


def canon(dest: Path | str, *also: str, stubs: Mapping[str, str] | None = None) -> Path:
    """Make `dest` a canon tree: `mise/`, `belt/`, `release/`, and what `also` names (#1714).

    `also` are pathspecs from the canon's top (an action directory,
    `security/identity.toml`), for a row that plants `.org-canon` or a
    canon checkout. Every file is a link, and `stubs` (a path from the
    canon's top to its text) replace theirs, as `belt` does.

    Returns:
        `dest`, as a Path.

    """
    names = tracked(CANON, "mise/", "belt/", "release/", *also)
    return plant(Path(dest), names, stubs or {})

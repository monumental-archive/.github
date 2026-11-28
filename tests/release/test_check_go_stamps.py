"""Run belt/release/check_go_stamps.py, the go-binary stamp check, on a stub go.

#1371 moved the step "Refuse a binary whose stamp is not the released
commit" out of build-go-binary.yml into the helper, refusals unchanged.
A build leg carries no belt, so the step runs the helper from the canon
tree the pins step recorded, like the build step beside it.

WHAT RUNS. The helper's `main`, called in this process (#1698), in a
real git repository (its HEAD is the released commit), with a stub
`go` first on PATH. The stub answers `version -m FILE` with FILE's own
contents, so a row plants the build info each binary reports by
writing it as the binary; STUB_FAIL makes go fail instead.
"""

import itertools
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

from belt import workflow_yaml
from belt.release import check_go_stamps
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "build-go-binary.yml"
COMMAND = "check-go-stamps"

GO_STUB = """#!/usr/bin/env bash
# Stub `go`: `version -m FILE` prints FILE; STUB_FAIL fails it.
set -euo pipefail
if [[ -n ${STUB_FAIL:-} ]]; then echo "go: not a Go binary" >&2; exit 1; fi
case $1 in
  version) cat "$3" ;;
  *) echo "go stub: refusing $*" >&2; exit 2 ;;
esac
"""


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["check-go-stamps"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def info(
    rev: str | None, modified: str | None = "false", *, module: bool = True
) -> str:
    """Render what `go version -m` prints for one binary.

    Returns:
        The build info, each absent setting left out.

    """
    lines = ["staged/tool: go1.26.6", "\tpath\texample.com/m/cmd/tool"]
    if module:
        lines.append("\tmod\texample.com/m\tv1.0.0\t")
    lines.append("\tdep\tgolang.org/x/mod\tv0.38.0\th1:x=")
    if rev is not None:
        lines.append(f"\tbuild\tvcs.revision={rev}")
    if modified is not None:
        lines.append(f"\tbuild\tvcs.modified={modified}")
    return "\n".join(lines) + "\n"


def clean_env(*, env: support.Env) -> dict[str, str]:
    """Strip every inherited `GIT_*` from the environment.

    The suite runs under a decoy GIT_DIR (#1463); a scratch repository
    here must be the one git answers for, never the decoy.

    Returns:
        os.environ less its GIT_* variables, with no user or system
        config read.

    """
    environ = env()
    return {**environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    head: str


def git(cwd: Path, *args: str, env: support.Env) -> str:
    """Run git in a scratch repository.

    Returns:
        Its standard output.

    """
    return subprocess.run(
        [support.tool("git"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env(env=env),
    ).stdout


@dataclass
class Stamping:
    """One release laid out: the commit its binaries must name."""

    head: str

    def ran(self, status: int, said: tuple[str, str]) -> Run:
        """Read what the helper's run did.

        Returns:
            Its status, output and the released commit.

        """
        out, err = said
        return Run(status, out, err, self.head)


class Stamp(Protocol):
    """What the `stamp` fixture hands a row."""

    def __call__(
        self, binaries: dict[str, str] | None, *, fail: bool = ..., repo: bool = ...
    ) -> Stamping:
        """Run stamp."""
        ...


@fixture
def stamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env) -> Stamp:
    """Lay out staged/ holding a row's binaries (name -> build info).

    `{head}` in a build info is replaced by the repository's HEAD.

    Returns:
        A function that lays out one release and returns it.

    """
    made = itertools.count()

    def lay_out(
        binaries: dict[str, str] | None, *, fail: bool = False, repo: bool = True
    ) -> Stamping:
        tmp = tmp_path / f"stamp{next(made)}"
        stubs, work = tmp / "bin", tmp / "work"
        for p in (stubs, work):
            p.mkdir(parents=True)
        (stubs / "go").write_text(GO_STUB)
        (stubs / "go").chmod(0o755)
        head = ""
        if repo:
            git(work, "init", "-q", env=env)
            who = ("-c", "user.name=t", "-c", "user.email=t@t")
            git(work, *who, "commit", "-q", "--allow-empty", "-m", "c", env=env)
            head = git(work, "rev-parse", "HEAD", env=env).strip()
        if binaries is not None:
            (work / "staged").mkdir()
            for name, text in binaries.items():
                (work / "staged" / name).write_text(text.replace("{head}", head))
        environ = {
            **clean_env(env=env),
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "STUB_FAIL": "1" if fail else "",
            "GIT_CEILING_DIRECTORIES": str(tmp),
        }
        for name, value in environ.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(work)
        monkeypatch.setattr(sys, "argv", [COMMAND])
        return Stamping(head)

    return lay_out


def step(name: str) -> dict[str, object]:
    """Read one step of the shipped workflow by its name.

    Returns:
        The step, as workflow_yaml reads it.

    """
    document = workflow_yaml.document(WORKFLOW)
    (found,) = [
        s
        for job in workflow_yaml.jobs(document).values()
        for s in workflow_yaml.steps(job)
        if s.get("name") == name
    ]
    return found


# The job runs the helper these rows run, through the one link.
def test_the_shipped_step_calls_the_helper() -> None:
    """The run: is the call alone, through `.org-canon`."""
    body = step("Refuse a binary whose stamp is not the released commit")["run"]
    assert body == '"${ORG_PYTHON}" -I .org-canon/belt check-go-stamps\n'


# A clean build of the released commit passes, every binary.
def test_every_binary_stamped_with_the_released_commit(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Two binaries, both clean, both carrying their modules."""
    stamping = stamp({"tool": info("{head}"), "probe": info("{head}")})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout + run.stderr
    assert not run.stdout


# One row per refusal, each message asserted.
def assert_refused(run: Run, want: str) -> None:
    """Hold one refusal to its exit status and its exact line."""
    assert run.returncode == 1, run.stderr
    assert f"::error::check-go-stamps: {want}\n" == run.stderr.replace(run.head, "HEAD")


def test_another_revision_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Bytes built from some other commit."""
    rev = "f" * 40
    stamping = stamp({"tool": info(rev)})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        f"tool: stamped revision {rev} is not the released commit HEAD",
    )


def test_no_revision_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Bytes built outside a checkout or with -buildvcs=false."""
    stamping = stamp({"tool": info(None)})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "tool: stamped revision <none> is not the released commit HEAD",
    )


def test_a_modified_tree_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Bytes that match no commit."""
    stamping = stamp({"tool": info("{head}", "true")})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "tool: vcs.modified=true — a release builds a clean tree",
    )


def test_no_modified_stamp_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """A tree whose cleanliness the stamp does not state."""
    stamping = stamp({"tool": info("{head}", None)})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "tool: vcs.modified=<none> — a release builds a clean tree",
    )


def test_no_module_stamp_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Bytes that carry no inventory at all."""
    stamping = stamp({"tool": info("{head}", module=False)})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "tool: no module stamp — the shipped bytes must carry their inventory",
    )


def test_a_setting_value_that_spells_the_stamp_is_not_the_stamp(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """An -ldflags value naming HEAD is not a stamp (#1556's shrunk example).

    The setting read was the first `vcs.revision=` anywhere in the
    text, so this binary, with no VCS stamp at all, passed.
    """
    spelled = (
        "staged/tool: go1.26.6\n"
        "\tpath\texample.com/m/cmd/tool\n"
        "\tmod\texample.com/m\tv1.0.0\t\n"
        "\tbuild\t-ldflags=-X=main.v=vcs.revision={head}\n"
        "\tbuild\t-ldflags=-X=main.m=vcs.modified=false\n"
    )
    stamping = stamp({"tool": spelled})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "tool: stamped revision <none> is not the released commit HEAD",
    )


def test_the_first_bad_binary_in_name_order_is_named(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """staged/ is read in name order, as the shell's glob read it."""
    stamping = stamp({"b": info("{head}", "true"), "a": info("f" * 40)})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert "::error::check-go-stamps: a: stamped revision" in run.stderr


def test_an_empty_staged_directory_is_refused(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Nothing built is nothing checked, and never a pass."""
    stamping = stamp({})
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "staged/ holds no binary — nothing was built to check",
    )


# go's or git's own failure fails the step with its status.
def test_a_file_go_cannot_read_fails_the_step(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """Not a Go binary: go's run.returncode and message."""
    stamping = stamp({"tool": info("{head}")}, fail=True)
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == "go: not a Go binary\n"
    assert not run.stdout


def test_outside_a_repository_git_fails_the_step(
    capfd: pytest.CaptureFixture[str], stamp: Stamp
) -> None:
    """No released commit to hold the stamp to."""
    stamping = stamp({"tool": info("f" * 40)}, repo=False)
    status = check_go_stamps.main([])
    run = stamping.ran(status, capfd.readouterr())
    assert run.returncode == support.GIT_FATAL
    assert not run.stdout

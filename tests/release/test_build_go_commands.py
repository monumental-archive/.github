"""Run belt/release/build_go_commands.py, the go-binary build, on a stub go.

#1384 moved the step "Build every command" out of build-go-binary.yml
into the helper, refusals unchanged. The job names the canon tree one
way, `.org-canon`, from its first step to its last: the canon action
lists the link in the clone's info/exclude, so go never stamps it as a
change, and no step unlinks it (#1944, where a step that did left two
later steps sourcing a path that was gone).

WHAT RUNS. The helper's `main`, called in this process (#1698) from a
module directory with no `.org-canon` link (go list skips dot paths), with a
stub `go` first on PATH. The stub answers `list` with the row's main
packages, one per line, and `build -o OUT PKG` by writing OUT.
"""

import itertools
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

from belt import workflow_yaml
from belt.release import build_go_commands
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "build-go-binary.yml"
TEMPLATE = '{{if eq .Name "main"}}{{.ImportPath}}{{end}}'
# A run that staged no binary.
NOTHING: list[str] = []

GO_STUB = """#!/usr/bin/env bash
# Stub `go`: `list` prints STUB_MAINS; `build -o OUT PKG` writes OUT,
# or fails when PKG is STUB_BROKEN.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
case $1 in
  list) printf '%s' "${STUB_MAINS}" ;;
  build)
    if [[ $4 == "${STUB_BROKEN}" ]]; then echo "$4: undefined: x" >&2; exit 1; fi
    echo "binary of $4" > "$3"
    ;;
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
    monkeypatch.setattr(sys, "argv", ["build-go-commands"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    go: list[str]
    staged: list[str]


@dataclass
class Building:
    """One module laid out: where the stub logs and the helper stages."""

    go_log: Path
    staged: Path

    def ran(self, status: int, said: tuple[str, str]) -> Run:
        """Read what the helper's run did.

        Returns:
            Its status, output, the go log and what staged/ holds.

        """
        out, err = said
        log, staged = self.go_log, self.staged
        return Run(
            status,
            out,
            err,
            log.read_text().splitlines() if log.exists() else [],
            sorted(p.name for p in staged.iterdir()) if staged.is_dir() else [],
        )


class Build(Protocol):
    """What the `build` fixture hands a row."""

    def __call__(self, mains: str, broken: str = ...) -> Building:
        """Run build."""
        ...


@fixture
def build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env) -> Build:
    """Lay out a module whose `go list` prints a row's mains.

    Returns:
        A function that lays out one module and returns it.

    """
    made = itertools.count()

    def lay_out(mains: str, broken: str = "") -> Building:
        tmp = tmp_path / f"build{next(made)}"
        stubs, work = tmp / "bin", tmp / "work"
        for p in (stubs, work):
            p.mkdir(parents=True)
        (stubs / "go").write_text(GO_STUB)
        (stubs / "go").chmod(0o755)
        log = tmp / "go.log"
        environ = env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            STUB_LOG=str(log),
            STUB_MAINS=mains,
            STUB_BROKEN=broken,
        )
        for name, value in environ.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(work)
        return Building(log, work / "staged")

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
    body = step("Build every command")["run"]
    assert body == '"${ORG_PYTHON}" -I .org-canon/belt build-go-commands\n'


# Every main package becomes one staged binary.
def test_two_commands_build_two_binaries(
    capfd: pytest.CaptureFixture[str], build: Build
) -> None:
    """Each named by its import path's last element."""
    building = build("example.com/m/cmd/stele\nexample.com/m/cmd/probe\n")
    status = build_go_commands.main([])
    run = building.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout + run.stderr
    staged: list[str] = ["probe", "stele"]
    assert staged == run.staged
    go: list[str] = [
        f"list -f {TEMPLATE} ./...",
        "build -o staged/stele example.com/m/cmd/stele",
        "build -o staged/probe example.com/m/cmd/probe",
    ]
    assert go == run.go
    assert not run.stdout


def test_the_blank_lines_of_go_list_are_no_package(
    capfd: pytest.CaptureFixture[str], build: Build
) -> None:
    """Non-main packages print an empty line each."""
    building = build("\n\nexample.com/m\n\n")
    status = build_go_commands.main([])
    run = building.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout + run.stderr
    staged: list[str] = ["m"]
    assert staged == run.staged


# One row per refusal, each message asserted.
def test_a_module_with_no_main_package_is_refused(
    capfd: pytest.CaptureFixture[str], build: Build
) -> None:
    """A library is the wrong class."""
    building = build("\n\n")
    status = build_go_commands.main([])
    run = building.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == (
        "::error::build-go-commands: no main packages in this module —"
        " go-binary is the wrong class\n"
    )
    assert run.staged == NOTHING


def test_two_commands_with_one_binary_name_are_refused(
    capfd: pytest.CaptureFixture[str], build: Build
) -> None:
    """The second would overwrite the first; it is never built."""
    building = build("example.com/m/cmd/tool\nexample.com/m/internal/tool\n")
    status = build_go_commands.main([])
    run = building.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == (
        "::error::build-go-commands: two main packages both build a binary named tool\n"
    )
    staged: list[str] = ["tool"]
    assert staged == run.staged
    assert "build -o staged/tool example.com/m/internal/tool" not in run.go


def test_a_failing_build_fails_the_step_with_its_status(
    capfd: pytest.CaptureFixture[str], build: Build
) -> None:
    """The failing build's run.returncode and message; none after it is built.

    The script row: go's message is on the child's own stderr.
    """
    building = build(
        "example.com/m/cmd/a\nexample.com/m/cmd/b\n", broken="example.com/m/cmd/a"
    )
    status = build_go_commands.main([])
    run = building.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == "example.com/m/cmd/a: undefined: x\n"
    assert not run.stdout
    assert run.staged == NOTHING

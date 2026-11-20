#!/usr/bin/env python3
"""Run release/check_go_stamps.py, the go-binary stamp check, on a stub go.

#1371 moved the step "Refuse a binary whose stamp is not the released
commit" out of build-go-binary.yml into the helper, refusals unchanged.
A build leg carries no belt, so the step runs the helper from the canon
tree the pins step recorded, like the build step beside it.

WHAT RUNS. The helper's `main`, called in this process (#1698), in a
real git repository (its HEAD is the released commit), with a stub
`go` first on PATH. The stub answers `version -m FILE` with FILE's own
contents, so a row plants the build info each binary reports by
writing it as the binary; STUB_FAIL makes go fail instead. One row
starts the helper by its path in this tree instead: the script
contract (it starts, and exits with go's own status and message).

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import os

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import check_go_stamps
import hermetic
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "build-go-binary.yml"
HELPER = CANON / "release" / "check_go_stamps.py"

GO_STUB = """#!/usr/bin/env bash
# Stub `go`: `version -m FILE` prints FILE; STUB_FAIL fails it.
set -euo pipefail
if [[ -n ${STUB_FAIL:-} ]]; then echo "go: not a Go binary" >&2; exit 1; fi
case $1 in
  version) cat "$3" ;;
  *) echo "go stub: refusing $*" >&2; exit 2 ;;
esac
"""


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


def clean_env() -> dict[str, str]:
    """Strip every inherited `GIT_*` from the environment.

    The suite runs under a decoy GIT_DIR (#1463); a scratch repository
    here must be the one git answers for, never the decoy.

    Returns:
        os.environ less its GIT_* variables, with no user or system
        config read.

    """
    env = hermetic.env()
    return {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    head: str


def git(cwd: Path, *args: str) -> str:
    """Run git in a scratch repository.

    Returns:
        Its standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        # ruff: ignore[start-process-with-partial-path] - the runner's git
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=clean_env(),
    ).stdout


def run_helper(
    binaries: dict[str, str] | None,
    *,
    fail: bool = False,
    repo: bool = True,
    script: bool = False,
) -> Run:
    """Run the helper over staged/ holding `binaries` (name → build info).

    `{head}` in a build info is replaced by the repository's HEAD. In
    this process unless `script`, which starts it by its path.

    Returns:
        Its status, output and the released commit.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, work = tmp / "bin", tmp / "work"
        for p in (stubs, work):
            p.mkdir()
        (stubs / "go").write_text(GO_STUB)
        (stubs / "go").chmod(0o755)
        head = ""
        if repo:
            git(work, "init", "-q")
            who = ("-c", "user.name=t", "-c", "user.email=t@t")
            git(work, *who, "commit", "-q", "--allow-empty", "-m", "c")
            head = git(work, "rev-parse", "HEAD").strip()
        if binaries is not None:
            (work / "staged").mkdir()
            for name, text in binaries.items():
                (work / "staged" / name).write_text(text.replace("{head}", head))
        env = {
            **clean_env(),
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "STUB_FAIL": "1" if fail else "",
            "GIT_CEILING_DIRECTORIES": str(tmp),
        }
        result: subprocess.CompletedProcess[str] | hermetic.Called
        if script:
            # ruff: ignore[subprocess-without-shell-equals-true]
            result = subprocess.run(
                [str(HELPER)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=work,
            )
        else:

            def call() -> int:
                return check_go_stamps.main()

            result = hermetic.call_main(call, env=env, cwd=work)
        return Run(result.returncode, result.stdout, result.stderr, head)


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


class StampStepIsTheHelper(unittest.TestCase):
    """The job runs the helper these rows run, from the recorded tree."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """The run: is the call alone, by the recorded tree."""
        body = step("Refuse a binary whose stamp is not the released commit")["run"]
        self.assertEqual(
            '"${ORG_PYTHON}" "${ORG_CANON_TREE}/release/check_go_stamps.py"\n', body
        )


class Passes(unittest.TestCase):
    """A clean build of the released commit passes, every binary."""

    def test_every_binary_stamped_with_the_released_commit(self) -> None:
        """Two binaries, both clean, both carrying their modules."""
        run = run_helper({"tool": info("{head}"), "probe": info("{head}")})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual("", run.stdout)


class Refuses(unittest.TestCase):
    """One row per refusal, each message asserted."""

    def assert_refused(self, run: Run, want: str) -> None:
        """Hold one refusal to its exit status and its exact line."""
        self.assertEqual(1, run.returncode, run.stderr)
        self.assertEqual(f"::error::{want}\n", run.stdout.replace(run.head, "HEAD"))

    def test_another_revision_is_refused(self) -> None:
        """Bytes built from some other commit."""
        rev = "f" * 40
        self.assert_refused(
            run_helper({"tool": info(rev)}),
            f"tool: stamped revision {rev} is not the released commit HEAD",
        )

    def test_no_revision_is_refused(self) -> None:
        """Bytes built outside a checkout or with -buildvcs=false."""
        self.assert_refused(
            run_helper({"tool": info(None)}),
            "tool: stamped revision <none> is not the released commit HEAD",
        )

    def test_a_modified_tree_is_refused(self) -> None:
        """Bytes that match no commit."""
        self.assert_refused(
            run_helper({"tool": info("{head}", "true")}),
            "tool: vcs.modified=true — a release builds a clean tree",
        )

    def test_no_modified_stamp_is_refused(self) -> None:
        """A tree whose cleanliness the stamp does not state."""
        self.assert_refused(
            run_helper({"tool": info("{head}", None)}),
            "tool: vcs.modified=<none> — a release builds a clean tree",
        )

    def test_no_module_stamp_is_refused(self) -> None:
        """Bytes that carry no inventory at all."""
        self.assert_refused(
            run_helper({"tool": info("{head}", module=False)}),
            "tool: no module stamp — the shipped bytes must carry their inventory",
        )

    def test_a_setting_value_that_spells_the_stamp_is_not_the_stamp(self) -> None:
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
        self.assert_refused(
            run_helper({"tool": spelled}),
            "tool: stamped revision <none> is not the released commit HEAD",
        )

    def test_the_first_bad_binary_in_name_order_is_named(self) -> None:
        """staged/ is read in name order, as the shell's glob read it."""
        run = run_helper({"b": info("{head}", "true"), "a": info("f" * 40)})
        self.assertIn("::error::a: stamped revision", run.stdout)

    def test_an_empty_staged_directory_is_refused(self) -> None:
        """Nothing built is nothing checked, and never a pass."""
        self.assert_refused(
            run_helper({}), "staged/ holds no binary — nothing was built to check"
        )


class ToolFailures(unittest.TestCase):
    """go's or git's own failure fails the step with its status."""

    def test_a_file_go_cannot_read_fails_the_step(self) -> None:
        """Not a Go binary: go's status and message.

        The script row: go's message is on the child's own stderr.
        """
        run = run_helper({"tool": info("{head}")}, fail=True, script=True)
        self.assertEqual(1, run.returncode)
        self.assertEqual("go: not a Go binary\n", run.stderr)
        self.assertEqual("", run.stdout)

    def test_outside_a_repository_git_fails_the_step(self) -> None:
        """No released commit to hold the stamp to."""
        run = run_helper({"tool": info("f" * 40)}, repo=False)
        self.assertEqual(128, run.returncode)
        self.assertEqual("", run.stdout)


if __name__ == "__main__":
    unittest.main()

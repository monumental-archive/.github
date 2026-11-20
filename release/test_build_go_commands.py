#!/usr/bin/env python3
"""Run release/build_go_commands.py, the go-binary build, on a stub go.

#1384 moved the step "Build every command" out of build-go-binary.yml
into the helper, refusals unchanged. The pins step unlinks .org-canon
before go runs (an untracked path stamps vcs.modified=true), so it
records the tree's real path in ORG_CANON_TREE and the step runs the
helper from there; one row holds both halves of that wiring.

WHAT RUNS. The helper's `main`, called in this process (#1698) from a
module directory with no `.org-canon` link (as on the runner), with a
stub `go` first on PATH. The stub answers `list` with the row's main
packages, one per line, and `build -o OUT PKG` by writing OUT. One row
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

import build_go_commands
import hermetic

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "build-go-binary.yml"
HELPER = CANON / "release" / "build_go_commands.py"
TEMPLATE = '{{if eq .Name "main"}}{{.ImportPath}}{{end}}'

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


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    go: list[str]
    staged: list[str]


def run_helper(mains: str, broken: str = "", *, script: bool = False) -> Run:
    """Run the helper on a module whose `go list` prints `mains`.

    In this process unless `script`, which starts it by its path.

    Returns:
        Its status, output, the go log and what staged/ holds.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, work = tmp / "bin", tmp / "work"
        for p in (stubs, work):
            p.mkdir()
        (stubs / "go").write_text(GO_STUB)
        (stubs / "go").chmod(0o755)
        log = tmp / "go.log"
        env = hermetic.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            STUB_LOG=str(log),
            STUB_MAINS=mains,
            STUB_BROKEN=broken,
        )
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
                return build_go_commands.main()

            result = hermetic.call_main(call, env=env, cwd=work)
        staged = work / "staged"
        return Run(
            result.returncode,
            result.stdout,
            result.stderr,
            log.read_text().splitlines() if log.exists() else [],
            sorted(p.name for p in staged.iterdir()) if staged.is_dir() else [],
        )


def step(name: str) -> str:
    """Cut one step's text out of the shipped workflow.

    Returns:
        From its `- name:` line to the next step's.

    """
    text = WORKFLOW.read_text("utf-8")
    return text.split(f"      - name: {name}\n")[1].split("      - name:")[0]


class BuildStepIsTheHelper(unittest.TestCase):
    """The job runs the helper these rows run, from the recorded tree."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """The run: is the call alone, by the recorded tree."""
        body = step("Build every command")
        self.assertIn(
            '        run: |\n          "${ORG_PYTHON}" "${ORG_CANON_TREE}/release/'
            'build_go_commands.py"\n',
            body,
        )
        self.assertNotIn("go build", body)

    def test_the_pins_step_records_the_tree_before_unlinking(self) -> None:
        """ORG_CANON_TREE is written, then the link goes."""
        body = step("Read the caller's pinned build inputs")
        record = body.index('echo "ORG_CANON_TREE=${tree}" >> "${GITHUB_ENV}"')
        self.assertLess(body.index("tree=$(realpath .org-canon)"), record)
        self.assertLess(record, body.index("rm -- .org-canon"))


class Builds(unittest.TestCase):
    """Every main package becomes one staged binary."""

    def test_two_commands_build_two_binaries(self) -> None:
        """Each named by its import path's last element."""
        run = run_helper("example.com/m/cmd/stele\nexample.com/m/cmd/probe\n")
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(["probe", "stele"], run.staged)
        self.assertEqual(
            [
                f"list -f {TEMPLATE} ./...",
                "build -o staged/stele example.com/m/cmd/stele",
                "build -o staged/probe example.com/m/cmd/probe",
            ],
            run.go,
        )
        self.assertEqual("", run.stdout)

    def test_the_blank_lines_of_go_list_are_no_package(self) -> None:
        """Non-main packages print an empty line each."""
        run = run_helper("\n\nexample.com/m\n\n")
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(["m"], run.staged)


class Refuses(unittest.TestCase):
    """One row per refusal, each message asserted."""

    def test_a_module_with_no_main_package_is_refused(self) -> None:
        """A library is the wrong class."""
        run = run_helper("\n\n")
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::no main packages in this module — go-binary is the wrong class\n",
            run.stdout,
        )
        self.assertEqual([], run.staged)

    def test_two_commands_with_one_binary_name_are_refused(self) -> None:
        """The second would overwrite the first; it is never built."""
        run = run_helper("example.com/m/cmd/tool\nexample.com/m/internal/tool\n")
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::two main packages both build a binary named tool\n",
            run.stdout,
        )
        self.assertEqual(["tool"], run.staged)
        self.assertNotIn("build -o staged/tool example.com/m/internal/tool", run.go)

    def test_a_failing_build_fails_the_step_with_its_status(self) -> None:
        """The failing build's status and message; none after it is built.

        The script row: go's message is on the child's own stderr.
        """
        run = run_helper(
            "example.com/m/cmd/a\nexample.com/m/cmd/b\n",
            broken="example.com/m/cmd/a",
            script=True,
        )
        self.assertEqual(1, run.returncode)
        self.assertEqual("example.com/m/cmd/a: undefined: x\n", run.stderr)
        self.assertEqual("", run.stdout)
        self.assertEqual([], run.staged)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Table tests for lint:gate-stub's concurrency requirement (#1195, #1617).

An event on a pull request that has already merged has no merge ref, so
its `github.ref` is the base branch. A stub grouping on the ref alone put
that run in the same group as the push gate on main, and
`cancel-in-progress: true` let it cancel the gate: main went red with
nothing wrong in it (edtf, run 43620265494). The stub then grouped by the
pull request's number and cancelled only `pull_request` runs — but every
push to the default branch still shared the ref's group, and the forge
holds one pending run per group, a newer one replacing it as `cancelled`:
27 of 28 push gates on main, 2026-11-19 19:00Z-19:53Z (#1617). Outside a
pull request the group now names the ref and the commit, and the lint
holds every gate caller to both lines.

The body under test is the task's own `run` string, read out of
mise/config.toml with decoded.load_toml and run by bash in a planted
tree — not a copy of it — so a change to the lint has to face these
rows. The Tera raw markers are dropped the way mise's render drops
them.

Each red row is red-when-planted and asserts the message, not just the
exit status: a planted row scored on exit alone can pass for the wrong
reason (one of the other two requirements tripping instead). The green
rows plant the canon's real stub and its real gate.yml, so the template
and the lint cannot drift apart.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

import decoded
import gate_stub_fix
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")

STUB = (CANON / "workflow-templates" / "ci.yml").read_text(encoding="utf-8")
GATE = (CANON / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8")

WANT = (
    "${{ github.event.pull_request.number"
    " || format('{0}-{1}', github.ref, github.sha) }}"
)
GROUP = "  group: ${{ github.workflow }}-" + WANT + "\n"
PRE_1617_GROUP = (
    "  group: ${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.ref }}\n"
)
CANCEL = "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
OLD_GROUP = "  group: ${{ github.workflow }}-${{ github.ref }}\n"
OLD_CANCEL = "  cancel-in-progress: true\n"

# The message, line for line: the defect, then both wanted lines verbatim.
MESSAGE = (
    "lint:gate-stub: gate caller's concurrency group is shared: a merged pull\n"
    "  request's event cancels the push gate (#1195), a push replaces the one\n"
    "  pending (#1617). Run mise run fix:gate-stub, or set under concurrency:\n"
    "    group: ${{ github.workflow }}-" + WANT + "\n"
    "    cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
    "  in: .github/workflows/gate.yml\n"
)


def body(task: str = "lint:gate-stub") -> str:
    """Read a task's run string as mise would render it.

    Returns:
        The task body with the `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    run = decoded.as_str(decoded.as_object(tasks[task], task)["run"], f"{task}'s run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()
FIX_BODY = body("fix:gate-stub")


def run(script: str, tree: Path) -> subprocess.CompletedProcess[str]:
    """Run one task body in a planted tree.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    env = hermetic.env()
    env |= {
        "ORG_BELT_DIR": str(BELT),
        "ORG_CANON_DIR": str(CANON),
        "ORG_PYTHON": sys.executable,
        "ORG_PYTHON_UNSET": "unset",
    }
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-euo", "pipefail", "-c", script],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def lint(workflow: str) -> subprocess.CompletedProcess[str]:
    """Run the lint against one planted gate caller.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        flows = Path(tmp) / ".github" / "workflows"
        flows.mkdir(parents=True)
        (flows / "gate.yml").write_text(workflow, encoding="utf-8")
        return run(BODY, Path(tmp))


def canon_name() -> str:
    """Ask identity.sh, the identity file's one reader, for owner/repo.

    Returns:
        The canon's name, as fix:gate-stub's body passes it.

    """
    script = (
        f'. "{BELT}/identity.sh"; echo "$(org_identity owner)/$(org_identity repo)"'
    )
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-euo", "pipefail", "-c", script],
        env=hermetic.env(ORG_CANON_DIR=str(CANON)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


CANON_NAME = canon_name()


def fixed(workflow: str) -> tuple[hermetic.Called, str]:
    """Call the fixer in-process on one planted gate caller (#1697).

    fix:gate-stub's body hands the helper the canon's name and the
    tree's workflows; the row hands it the same, from the same tree.

    Returns:
        The status and both streams, and the caller's text after it ran.

    """
    with tempfile.TemporaryDirectory() as tmp:
        flows = Path(tmp) / ".github" / "workflows"
        flows.mkdir(parents=True)
        caller = flows / "gate.yml"
        caller.write_text(workflow, encoding="utf-8")
        done = hermetic.call_main(
            lambda: gate_stub_fix.main([CANON_NAME, ".github/workflows/gate.yml"]),
            cwd=tmp,
        )
        return done, caller.read_text(encoding="utf-8")


def started(workflow: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Start the helper as a process on one caller: the script contract.

    Returns:
        The finished process and the caller's text after it ran.

    """
    with tempfile.TemporaryDirectory() as tmp:
        flows = Path(tmp) / ".github" / "workflows"
        flows.mkdir(parents=True)
        caller = flows / "gate.yml"
        caller.write_text(workflow, encoding="utf-8")
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                *(sys.executable, str(BELT / "gate_stub_fix.py")),
                *(CANON_NAME, ".github/workflows/gate.yml"),
            ],
            cwd=tmp,
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        return done, caller.read_text(encoding="utf-8")


def planted(old: str, new: str) -> str:
    """Return the canon's stub with one line swapped.

    Returns:
        The stub text with the swap made.

    Raises:
        ValueError: the line to swap is not in the stub exactly once, so
            the row would plant nothing.

    """
    if STUB.count(old) != 1:
        message = f"the stub no longer carries exactly one {old!r}"
        raise ValueError(message)
    return STUB.replace(old, new)


class Green(unittest.TestCase):
    """The shapes the lint must pass."""

    def assert_ok(self, workflow: str) -> None:
        """Assert a clean pass with the ok line and nothing on stderr."""
        done = lint(workflow)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "lint:gate-stub: ok\n")
        self.assertEqual(done.stderr, "")

    def test_the_canons_stub_passes(self) -> None:
        """The template every repository copies is clean."""
        self.assert_ok(STUB)

    def test_the_canons_own_gate_passes(self) -> None:
        """The canon's self-applied gate is clean."""
        self.assert_ok(GATE)

    def test_the_canons_own_gate_groups_a_push_by_its_commit(self) -> None:
        """A push to main cannot replace another push's pending gate (#1617).

        Read on the file itself, not through the lint, so the row holds
        even if the lint's wanted line were weakened with it.
        """
        self.assertIn(GROUP, GATE)
        self.assertIn("github.sha", GROUP)

    def test_a_trailing_comment_on_cancel_passes(self) -> None:
        """A comment after the wanted line does not change it."""
        self.assert_ok(planted(CANCEL, CANCEL.rstrip("\n") + " # see #1195\n"))

    def test_a_workflow_that_is_not_a_gate_caller_is_not_held(self) -> None:
        """Only a caller of ci.yml owes the group."""
        other = planted(
            "uses: monumental-archive/.github/.github/workflows/ci.yml@",
            "uses: monumental-archive/.github/.github/workflows/audit.yml@",
        )
        self.assert_ok(other.replace(GROUP, OLD_GROUP).replace(CANCEL, OLD_CANCEL))


class Red(unittest.TestCase):
    """Each planted defect reds with the stated message."""

    def assert_red(self, workflow: str) -> None:
        """Assert exit 1 and exactly the concurrency message on stderr."""
        done = lint(workflow)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, MESSAGE)
        self.assertEqual(done.stdout, "")

    def test_the_pre_1195_stub_is_red(self) -> None:
        """The stub every repository carried before #1195."""
        self.assert_red(planted(GROUP, OLD_GROUP).replace(CANCEL, OLD_CANCEL))

    def test_the_pre_1617_group_is_red(self) -> None:
        """Every push to the default branch shares the ref's one group."""
        self.assert_red(planted(GROUP, PRE_1617_GROUP))

    def test_a_group_on_the_ref_alone_is_red(self) -> None:
        """The shared group alone, cancel already right."""
        self.assert_red(planted(GROUP, OLD_GROUP))

    def test_cancel_true_is_red(self) -> None:
        """A push to main may still be cancelled."""
        self.assert_red(planted(CANCEL, OLD_CANCEL))

    def test_cancel_false_is_red(self) -> None:
        """Five pushes to a pull request would run five gates."""
        self.assert_red(planted(CANCEL, "  cancel-in-progress: false\n"))

    def test_a_missing_cancel_line_is_red(self) -> None:
        """Absent is not the expression."""
        self.assert_red(planted(CANCEL, ""))

    def test_a_missing_concurrency_block_is_red(self) -> None:
        """No group at all is not the group."""
        self.assert_red(
            planted("concurrency:\n" + GROUP + CANCEL, ""),
        )

    def test_a_second_cancel_line_that_disagrees_is_red(self) -> None:
        """One right line does not excuse a wrong one."""
        self.assert_red(
            STUB.replace(
                "    uses: monumental-archive",
                "    concurrency:\n"
                "      group: x\n"
                "      cancel-in-progress: true\n"
                "    uses: monumental-archive",
            ),
        )


class Fix(unittest.TestCase):
    """fix:gate-stub, the lint's write-mode sibling (#1617)."""

    def assert_rewritten(self, workflow: str) -> None:
        """Assert the fixer makes the stub the canon's, which the lint passes."""
        done, after = fixed(workflow)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "fix:gate-stub: rewrote the concurrency lines in"
            " .github/workflows/gate.yml\nfix:gate-stub: ok\n",
        )
        self.assertEqual(after, STUB)
        self.assertEqual(lint(after).returncode, 0)

    def test_the_script_contract(self) -> None:
        """Started as a process, it rewrites the caller and exits 0."""
        done, after = started(planted(GROUP, PRE_1617_GROUP))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "fix:gate-stub: rewrote the concurrency lines in"
            " .github/workflows/gate.yml\nfix:gate-stub: ok\n",
        )
        self.assertEqual(after, STUB)

    def test_the_task_body_hands_the_helper_the_canon(self) -> None:
        """fix:gate-stub's body names the canon from identity.sh and fixes."""
        with tempfile.TemporaryDirectory() as tmp:
            flows = Path(tmp) / ".github" / "workflows"
            flows.mkdir(parents=True)
            caller = flows / "gate.yml"
            caller.write_text(planted(GROUP, PRE_1617_GROUP), encoding="utf-8")
            done = run(FIX_BODY, Path(tmp))
            after = caller.read_text(encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(after, STUB)

    def test_the_pre_1617_stub_is_rewritten_and_then_passes(self) -> None:
        """The stub every repository carries today becomes the canon's."""
        self.assert_rewritten(planted(GROUP, PRE_1617_GROUP))

    def test_the_pre_1195_stub_is_rewritten_and_then_passes(self) -> None:
        """Both lines wrong: both rewritten, nothing else touched."""
        self.assert_rewritten(planted(GROUP, OLD_GROUP).replace(CANCEL, OLD_CANCEL))

    def test_a_missing_cancel_line_is_written(self) -> None:
        """A block without the cancel line gets it at the block's end."""
        self.assert_rewritten(planted(CANCEL, ""))

    def test_a_trailing_comment_is_kept(self) -> None:
        """The rewrite keeps a comment on the line it rewrites."""
        done, after = fixed(planted(GROUP, PRE_1617_GROUP.rstrip("\n") + " # x\n"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(after, STUB.replace(GROUP, GROUP.rstrip("\n") + " # x\n"))

    def test_a_conforming_stub_is_left_byte_for_byte(self) -> None:
        """The canon's stub and gate are no-ops."""
        for workflow in (STUB, GATE):
            done, after = fixed(workflow)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout, "fix:gate-stub: ok\n")
            self.assertEqual(after, workflow)

    def test_a_stub_with_no_concurrency_block_is_refused_by_name(self) -> None:
        """No block is never given one silently."""
        workflow = planted("concurrency:\n" + GROUP + CANCEL, "")
        done, after = fixed(workflow)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(after, workflow)
        self.assertEqual(done.stdout, "")
        self.assertTrue(
            done.stderr.startswith(
                "fix:gate-stub: no top-level concurrency block to rewrite",
            ),
            done.stderr,
        )
        self.assertTrue(
            done.stderr.endswith("  in: .github/workflows/gate.yml\n"),
            done.stderr,
        )

    def test_a_workflow_that_is_not_a_gate_caller_is_not_touched(self) -> None:
        """Only a caller of ci.yml is the fixer's."""
        other = planted(
            "uses: monumental-archive/.github/.github/workflows/ci.yml@",
            "uses: monumental-archive/.github/.github/workflows/audit.yml@",
        ).replace(GROUP, OLD_GROUP)
        done, after = fixed(other)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(after, other)


if __name__ == "__main__":
    unittest.main()

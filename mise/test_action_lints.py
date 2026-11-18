#!/usr/bin/env python3
"""Planted rows for the belt's action lints and their one selection (#1448).

lint:actions-allowed, lint:action-pins and lint:zizmor assumed every
action lives under `.github/actions/`. Two defects followed. The
allow-list was one unquoted string, so `actions/*` expanded against the
working directory, and in a repository tracking `actions/<name>/` every
GitHub-owned action was reported forbidden (reproduced by the stele#386
lane). And an action published from `actions/<name>/action.yml` was read
by none of the three. The three now share mise/action-files.sh.

Each task's own body, read from mise/config.toml, runs in a planted git
tree with the belt directory as ORG_BELT_DIR, so the selection, the
identity reader and pinact's config are the real ones. zizmor is
replaced by a stub that records what it was given: the row is about
which files reach it, and zizmor's verdict on an action is zizmor's to
prove. pinact runs for real (`--no-api`, offline).

Mutation-checked: run against `main`'s mise/config.toml, the first row
fails with the forbidden-action message naming actions/checkout, and the
pins, owner and zizmor rows fail because actions/x/action.yml is never
read, and the gate-install row fails because mise/gate-tools.toml never
installed pinact or zizmor for it.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib `unittest`,
run through the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

BELT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("gate_tools", BELT / "gate-tools.py")
gate_tools = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate_tools)
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
SHA = "0123456789abcdef0123456789abcdef01234567"
CHECKOUT = f"actions/checkout@{SHA} # v6.0.0"


def body(task: str) -> str:
    """Read a task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"][task]["run"]


def owner() -> str:
    """Read the canon owner the allow-list names.

    Returns:
        The owner from security/identity.toml.

    """
    with (BELT.parent / "security" / "identity.toml").open("rb") as handle:
        return tomllib.load(handle)["owner"]


def workflow(*uses: str) -> str:
    """Render a workflow whose one job's steps use each reference.

    Returns:
        The YAML text.

    """
    steps = "".join(f"      - uses: {u}\n" for u in uses)
    return f"on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n{steps}"


def action(*uses: str) -> str:
    """Render a composite action whose steps use each reference.

    Returns:
        The YAML text.

    """
    steps = "".join(f"    - uses: {u}\n" for u in uses)
    return f"name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n{steps}"


def run_step(name: str, lines: list[str]) -> str:
    """Render a composite action with one named bash step and its block.

    Returns:
        The YAML text; the block's first line is the file's line 9.

    """
    block = "".join(f"        {line}\n" for line in lines)
    return (
        "name: x\ndescription: x\nruns:\n  using: composite\n  steps:\n"
        f"    - name: {name}\n      shell: bash\n      run: |\n{block}"
    )


def template(name: str, lines: list[str]) -> str:
    """Render a workflow template with one named step and its block.

    Returns:
        The YAML text; the block's first line is the file's line 8.

    """
    block = "".join(f"          {line}\n" for line in lines)
    return (
        "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
        f"      - name: {name}\n        run: |\n{block}"
    )


SIXTEEN = [f"echo {i}" for i in range(1, 17)]
CEILING = (
    "workflow-shell: 1 finding(s) — a run: block holds at most 15 lines,"
    " and the debt file only shrinks"
)


class Planted(unittest.TestCase):
    """A planted, tracked tree; a lint's verdict on it."""

    def setUp(self) -> None:
        """Make an empty git repository and a scrubbed environment."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = tmp / "tree"
        self.tree.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_CANON_DIR": str(BELT.parent),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=self.env, check=True
        )

    def plant(self, files: dict[str, str]) -> None:
        """Write and track files in the planted tree."""
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")

    def run_task(self, task: str) -> subprocess.CompletedProcess:
        """Run a task's body in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body(task)],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def selected(self) -> list[str]:
        """Run the one selection in the planted tree.

        Returns:
            The paths it prints.

        """
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                BASH,
                "-euo",
                "pipefail",
                "-c",
                '. "${ORG_BELT_DIR}/action-files.sh"; action_files',
            ],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.splitlines()


class ActionsAllowed(Planted):
    """lint:actions-allowed."""

    def test_a_top_level_actions_directory_does_not_forbid_github_actions(self) -> None:
        """`actions/*` is a pattern for references, never a path to expand."""
        self.plant({
            "actions/x/action.yml": action(),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:actions-allowed")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "lint:actions-allowed: 1 action reference(s), all permitted\n",
        )

    def test_a_disallowed_owner_in_a_top_level_action_is_red(self) -> None:
        """An action outside .github/actions/ is read, and its owner judged."""
        ref = f"evil/thing@{SHA} # v1.0.0"
        self.plant({
            "actions/x/action.yml": action(ref),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:actions-allowed")
        self.assertEqual(done.returncode, 1)
        forbids = (
            "lint:actions-allowed: uses: an action the org allowlist forbids:"
            f" actions/x/action.yml(evil/thing@{SHA})"
        )
        allowed = (
            f"  allowed: GitHub-owned plus actions/* github/* {owner()}/*"
            " jdx/mise-action@* codecov/codecov-action@* ossf/scorecard-action@*"
        )
        self.assertEqual(done.stderr.splitlines()[:2], [forbids, allowed])

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged: nothing to read is a clean skip."""
        self.plant({"README.md": "x\n", "my-action.yml": action("evil/thing@v1")})
        done = self.run_task("lint:actions-allowed")
        self.assertEqual(
            (done.returncode, done.stdout),
            (0, "lint:actions-allowed: no workflows or actions, skipped\n"),
        )


class ActionPins(Planted):
    """lint:action-pins."""

    def test_an_unpinned_uses_in_a_top_level_action_is_red(self) -> None:
        """Pinact is handed actions/x/action.yml and refuses its tag pin."""
        self.plant({
            "actions/x/action.yml": action("actions/setup-go@v5"),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:action-pins")
        self.assertNotEqual(done.returncode, 0)
        output = done.stdout + done.stderr
        self.assertIn("action can't be pinned", output)
        self.assertIn("actions/x/action.yml:6", output)
        self.assertIn("- uses: actions/setup-go@v5", output)

    def test_a_pinned_top_level_action_passes(self) -> None:
        """The control row: the same tree pinned is green."""
        self.plant({
            "actions/x/action.yml": action(f"actions/setup-go@{SHA} # v5.0.0"),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:action-pins")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.endswith("lint:action-pins: ok\n"))

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged."""
        self.plant({"README.md": "x\n"})
        done = self.run_task("lint:action-pins")
        self.assertEqual(
            (done.returncode, done.stdout),
            (0, "lint:action-pins: no workflows or actions, skipped\n"),
        )


class Zizmor(Planted):
    """lint:zizmor, with zizmor stubbed to record its arguments."""

    def setUp(self) -> None:
        """Put a recording zizmor first on PATH."""
        super().setUp()
        self.args = self.bin / "args"
        stub = self.bin / "zizmor"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{self.args}"\n', "utf-8")
        stub.chmod(0o755)
        self.env["PATH"] = f"{self.bin}{os.pathsep}{self.env['PATH']}"

    def test_zizmor_is_given_every_action_file(self) -> None:
        """Workflows and action definitions wherever they live, offline."""
        self.plant({
            "actions/x/action.yml": action(),
            ".github/actions/y/action.yaml": action(),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
            "action.yml": action(),
        })
        done = self.run_task("lint:zizmor")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self.args.read_text("utf-8").splitlines(),
            [
                "--offline",
                "--persona=pedantic",
                ".github/actions/y/action.yaml",
                ".github/workflows/ci.yml",
                "action.yml",
                "actions/x/action.yml",
            ],
        )

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged, and zizmor is never run."""
        self.plant({"README.md": "x\n"})
        done = self.run_task("lint:zizmor")
        self.assertEqual(
            (done.returncode, done.stdout),
            (0, "lint:zizmor: no workflows or actions, skipped\n"),
        )
        self.assertFalse(self.args.exists())


class ShellEmbedded(Planted):
    """lint:shell-embedded reads a top-level action's run: blocks."""

    def test_an_unquoted_expansion_in_a_top_level_action_is_red(self) -> None:
        """Shellcheck refuses the block, at its line in actions/x/action.yml."""
        self.plant({"actions/x/action.yml": run_step("Remove", ["rm $UNQUOTED"])})
        done = self.run_task("lint:shell-embedded")
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "actions/x/action.yml:9:note: Double quote to prevent globbing and"
            " word splitting. [SC2086]",
            done.stderr.splitlines(),
        )

    def test_a_clean_top_level_action_passes(self) -> None:
        """The control row: the same file, quoted, is green."""
        self.plant({"actions/x/action.yml": run_step("Remove", ['rm "${QUOTED}"'])})
        done = self.run_task("lint:shell-embedded")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class WorkflowShell(Planted):
    """lint:workflow-shell and fix:workflow-shell read a top-level action."""

    DEBT = "security/workflow-shell-debt.txt"

    def test_an_oversized_block_in_a_top_level_action_is_red(self) -> None:
        """Sixteen lines in actions/x/action.yml, unlisted, are over the ceiling."""
        self.plant({"actions/x/action.yml": run_step("Big", SIXTEEN)})
        done = self.run_task("lint:workflow-shell")
        self.assertEqual(done.returncode, 1)
        finding = (
            "actions/x/action.yml:9: step 'Big' holds a run: block of 16"
            " lines, over the 15-line ceiling, and"
            " security/workflow-shell-debt.txt does not list it — move the"
            " logic out (CONTRIBUTING.md)"
        )
        self.assertEqual(done.stderr.splitlines(), [finding, CEILING])

    def test_the_workflow_templates_are_still_read(self) -> None:
        """The selection is added to, never narrowed: templates stay."""
        self.plant({"workflow-templates/t.yml": template("Big", SIXTEEN)})
        done = self.run_task("lint:workflow-shell")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines()[0],
            "workflow-templates/t.yml:8: step 'Big' holds a run: block of 16"
            " lines, over the 15-line ceiling, and"
            " security/workflow-shell-debt.txt does not list it — move the"
            " logic out (CONTRIBUTING.md)",
        )

    def test_the_fixer_lowers_a_top_level_action_entry(self) -> None:
        """Read, the entry is lowered; unread, it would be removed as gone."""
        self.plant({
            "actions/x/action.yml": run_step("Big", SIXTEEN),
            self.DEBT: "actions/x/action.yml\tBig\t30\t# canon helper: #1\n",
        })
        done = self.run_task("fix:workflow-shell")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            "fix:workflow-shell: 1 entry(ies) lowered, 0 removed; it never adds one\n",
        )
        self.assertEqual(
            (self.tree / self.DEBT).read_text("utf-8"),
            "actions/x/action.yml\tBig\t16\t# canon helper: #1\n",
        )


class Selection(Planted):
    """mise/action-files.sh: what an action file is."""

    def test_action_files_at_any_depth_and_nothing_else(self) -> None:
        """By file name, tracked, outside vendor/."""
        self.plant({
            "action.yml": "",
            "actions/x/action.yml": "",
            "a/b/c/action.yaml": "",
            ".github/actions/y/action.yml": "",
            ".github/workflows/ci.yml": "",
            ".github/workflows/release.yaml": "",
            "vendor/v/action.yml": "",
            "my-action.yml": "",
            "actions/x/action.yml.bak": "",
            ".github/dependabot.yml": "",
        })
        (self.tree / "untracked").mkdir()
        (self.tree / "untracked" / "action.yml").write_text("", "utf-8")
        self.assertEqual(
            self.selected(),
            [
                ".github/actions/y/action.yml",
                ".github/workflows/ci.yml",
                ".github/workflows/release.yaml",
                "a/b/c/action.yaml",
                "action.yml",
                "actions/x/action.yml",
            ],
        )


class GateInstalls(unittest.TestCase):
    """mise/gate-tools.toml installs the tools wherever the selection reads."""

    def test_a_top_level_action_alone_installs_pinact_and_zizmor(self) -> None:
        """A tree whose only action file is actions/x/action.yml."""
        reasons = gate_tools.needed(gate_tools.load_table(), ["actions/x/action.yml"])
        for key in ("aqua:suzuki-shunsuke/pinact", "zizmor"):
            with self.subTest(key=key):
                self.assertEqual(
                    reasons[key], "needed: actions/x/action.yml is tracked"
                )


if __name__ == "__main__":
    unittest.main()

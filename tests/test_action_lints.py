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

import json
import os
import subprocess
from pathlib import Path

import pytest

from belt import decoded, gate_tools
from tests import hermetic

BELT = hermetic.MISE
GIT = hermetic.tool("git")
SHA = "0123456789abcdef0123456789abcdef01234567"
CHECKOUT = f"actions/checkout@{SHA} # v6.0.0"


def body(task: str) -> str:
    """Read a task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    return decoded.as_str(task_table(task)["run"], f"{task}.run")


def task_table(task: str) -> dict[str, object]:
    """Read a task's table from the belt's config.

    Returns:
        The task's keys as the config writes them.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    return decoded.as_object(tasks[task], task)


def owner() -> str:
    """Read the canon owner the allow-list names.

    Returns:
        The owner from security/identity.toml.

    """
    identity = (BELT.parent / "security" / "identity.toml").read_bytes()
    return decoded.as_str(decoded.load_toml(identity)["owner"], "owner")


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
    "lint:workflow-shell: 1 finding(s) — a run: block holds at most 15 lines,"
    " and the debt file only shrinks"
)


class TestPlanted:
    """A planted, tracked tree; a lint's verdict on it."""

    @pytest.fixture(autouse=True)
    def _set_up_test_planted(self, tmp_path: Path) -> None:
        """Make an empty git repository and a scrubbed environment."""
        tmp = tmp_path
        self.tree = tmp / "tree"
        self.tree.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.env = hermetic.env() | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_CANON_DIR": str(BELT.parent),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run([GIT, *args], cwd=self.tree, env=self.env, check=True)

    def plant(self, files: dict[str, str]) -> None:
        """Write and track files in the planted tree."""
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")

    def run_task(self, task: str) -> subprocess.CompletedProcess[str]:
        """Run a task's body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(task, body(task))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def selected(self) -> list[str]:
        """Run the one selection in the planted tree, as a task body does.

        Returns:
            The paths it prints, NUL-separated (#1986).

        """
        as_mise = hermetic.task_run(
            "lint:zizmor",
            '. "${ORG_BELT_DIR}/action-files.sh"; files=() && action_files files;'
            ' printf "%s\\0" "${files[@]}"',
        )
        done = subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return [path for path in done.stdout.split("\0") if path]


class TestActionsAllowed(TestPlanted):
    """lint:actions-allowed."""

    def test_a_top_level_actions_directory_does_not_forbid_github_actions(self) -> None:
        """`actions/*` is a pattern for references, never a path to expand."""
        self.plant({
            "actions/x/action.yml": action(),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:actions-allowed")
        assert done.returncode == 0, done.stderr
        assert (
            done.stdout
            == "lint:actions-allowed: 1 action reference(s), all permitted\n"
        )

    def test_a_disallowed_owner_in_a_top_level_action_is_red(self) -> None:
        """An action outside .github/actions/ is read, and its owner judged."""
        ref = f"evil/thing@{SHA} # v1.0.0"
        self.plant({
            "actions/x/action.yml": action(ref),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:actions-allowed")
        assert done.returncode == 1
        forbids = (
            "lint:actions-allowed: uses: an action the org allowlist forbids:"
            f" actions/x/action.yml(evil/thing@{SHA})"
        )
        allowed = (
            f"  allowed: GitHub-owned plus actions/* github/* {owner()}/*"
            " jdx/mise-action@* codecov/codecov-action@* ossf/scorecard-action@*"
        )
        expected = [forbids, allowed]
        assert done.stderr.splitlines()[:2] == expected

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged: nothing to read is a clean skip."""
        self.plant({"README.md": "x\n", "my-action.yml": action("evil/thing@v1")})
        done = self.run_task("lint:actions-allowed")
        assert (done.returncode, done.stdout) == (
            0,
            "lint:actions-allowed: no workflows or actions, skipped\n",
        )


class TestActionPins(TestPlanted):
    """lint:action-pins."""

    def test_an_unpinned_uses_in_a_top_level_action_is_red(self) -> None:
        """Pinact is handed actions/x/action.yml and refuses its tag pin."""
        self.plant({
            "actions/x/action.yml": action("actions/setup-go@v5"),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:action-pins")
        assert done.returncode != 0
        output = done.stdout + done.stderr
        assert "action can't be pinned" in output
        assert "actions/x/action.yml:6" in output
        assert "- uses: actions/setup-go@v5" in output

    def test_a_pinned_top_level_action_passes(self) -> None:
        """The control row: the same tree pinned is green."""
        self.plant({
            "actions/x/action.yml": action(f"actions/setup-go@{SHA} # v5.0.0"),
            ".github/workflows/ci.yml": workflow(CHECKOUT),
        })
        done = self.run_task("lint:action-pins")
        assert done.returncode == 0, done.stderr
        assert done.stdout.endswith("lint:action-pins: ok\n")

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged."""
        self.plant({"README.md": "x\n"})
        done = self.run_task("lint:action-pins")
        assert (done.returncode, done.stdout) == (
            0,
            "lint:action-pins: no workflows or actions, skipped\n",
        )


class TestZizmor(TestPlanted):
    """lint:zizmor, with zizmor stubbed to record its arguments."""

    @pytest.fixture(autouse=True)
    def _set_up_test_zizmor(self) -> None:
        """Put a recording zizmor first on PATH."""
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
        assert done.returncode == 0, done.stderr
        expected = [
            "--offline",
            "--persona=auditor",
            ".github/actions/y/action.yaml",
            ".github/workflows/ci.yml",
            "action.yml",
            "actions/x/action.yml",
        ]
        assert self.args.read_text("utf-8").splitlines() == expected

    def test_no_workflow_and_no_action_skips_clean(self) -> None:
        """Applicability unchanged, and zizmor is never run."""
        self.plant({"README.md": "x\n"})
        done = self.run_task("lint:zizmor")
        assert (done.returncode, done.stdout) == (
            0,
            "lint:zizmor: no workflows or actions, skipped\n",
        )
        assert not self.args.exists()


def deploy(environment: str) -> str:
    """Render a workflow whose one job reads a secret.

    `environment` is the job's `environment:` line, or "" for none.

    Returns:
        The YAML text; the secret is read on line 14 without one.

    """
    return (
        "name: deploy\non: push\npermissions: {}\n"
        "concurrency:\n  group: deploy\n  cancel-in-progress: false\n"
        "jobs:\n  deploy:\n    name: deploy\n"
        f"{environment}"
        "    runs-on: ubuntu-24.04\n    steps:\n      - name: use the key\n"
        "        env:\n          KEY: ${{ secrets.DEPLOY_KEY }}\n"
        '        run: test -n "${KEY}"\n'
    )


class TestZizmorPersona(TestPlanted):
    """lint:zizmor's verdict, from the real zizmor at the belt's pin (#1719).

    Pedantic suppresses secrets-outside-env, so before #1719 the first
    row was green: a job handed an environment's secret with no
    `environment:` gets an empty string, and only Monday's audit said so.
    """

    def test_a_secret_read_outside_an_environment_is_red(self) -> None:
        """The auditor persona names the audit and the line."""
        self.plant({".github/workflows/deploy.yml": deploy("")})
        done = self.run_task("lint:zizmor")
        assert done.returncode != 0
        assert (
            "warning[secrets-outside-env]: secrets referenced without a"
            " dedicated environment"
        ) in done.stdout
        assert ".github/workflows/deploy.yml:14:" in done.stdout

    def test_the_same_job_in_an_environment_is_green(self) -> None:
        """The control row: one `environment:` line, nothing else changed."""
        self.plant({
            ".github/workflows/deploy.yml": deploy("    environment: production\n")
        })
        done = self.run_task("lint:zizmor")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "No findings to report." in done.stdout


class TestShellEmbedded(TestPlanted):
    """lint:shell-embedded reads a top-level action's run: blocks."""

    def test_an_unquoted_expansion_in_a_top_level_action_is_red(self) -> None:
        """Shellcheck refuses the block, at its line in actions/x/action.yml."""
        self.plant({"actions/x/action.yml": run_step("Remove", ["rm $UNQUOTED"])})
        done = self.run_task("lint:shell-embedded")
        assert done.returncode == 1
        assert (
            "lint:shell-embedded: actions/x/action.yml:9:note: Double quote to"
            " prevent globbing and"
            " word splitting. [SC2086]"
        ) in done.stderr.splitlines()

    def test_a_clean_top_level_action_passes(self) -> None:
        """The control row: the same file, quoted, is green."""
        self.plant({"actions/x/action.yml": run_step("Remove", ['rm "${QUOTED}"'])})
        done = self.run_task("lint:shell-embedded")
        assert done.returncode == 0, done.stdout + done.stderr


# A step sourcing the canon's writer and calling org_error with no
# arguments, which it reads: SC2119, a finding shellcheck can make only
# once it has followed the source into the canon. Unfollowed, the same
# line gives SC1091 alone (#1715, #1997). The runner red #1710 met was
# such a source, which a laptop with no `.org-canon` never saw.
SOURCED = [
    "set -euo pipefail",
    ". .org-canon/mise/workflow-command.sh",
    "[[ -n ${X:-} ]] || { org_error; exit 1; }",
]


def task_env(task: str) -> dict[str, str]:
    """Read a task's own env table from the belt's config.

    Returns:
        The variables `mise run` would set for it.

    """
    env = decoded.as_object(task_table(task).get("env", {}), f"{task}.env")
    return {name: decoded.as_str(value, name) for name, value in env.items()}


class TestCanonSources(TestPlanted):
    """Both shell lints follow `.org-canon` where there is none (#1715)."""

    def porcelain(self) -> str:
        """Read the planted tree's status.

        Returns:
            `git status --porcelain --untracked-files=all`.

        """
        return subprocess.run(
            [GIT, "status", "--porcelain", "--untracked-files=all"],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def test_lint_actions_follows_the_canon_source(self) -> None:
        """SC2119 is named, with no `.org-canon` in the tree."""
        self.plant({".github/workflows/w.yml": template("Refuse", SOURCED)})
        before = self.porcelain()
        assert not (self.tree / ".org-canon").exists()
        self.env |= task_env("lint:actions")
        done = self.run_task("lint:actions")
        assert done.returncode == 1, done.stdout + done.stderr
        assert "SC2119" in done.stdout + done.stderr
        assert self.porcelain() == before

    def test_lint_shell_embedded_follows_the_canon_source(self) -> None:
        """The same step, the same finding, at its line in the workflow."""
        self.plant({".github/workflows/w.yml": template("Refuse", SOURCED)})
        before = self.porcelain()
        done = self.run_task("lint:shell-embedded")
        assert done.returncode == 1, done.stdout + done.stderr
        assert ".github/workflows/w.yml:10:" in done.stderr
        assert "[SC2119]" in done.stderr
        assert self.porcelain() == before

    def test_a_source_the_canon_does_not_hold_is_red_by_name(self) -> None:
        """The workflow, the line and the path; SC1091 alone says nothing."""
        lines = ["set -euo pipefail", ". .org-canon/mise/no-such-file.sh"]
        self.plant({".github/workflows/w.yml": template("Missing", lines)})
        before = self.porcelain()
        done = self.run_task("lint:shell-embedded")
        assert done.returncode == 1, done.stdout + done.stderr
        assert (
            ".github/workflows/w.yml:9: sources .org-canon/mise/no-such-file.sh,"
            " which the canon tree"
        ) in done.stderr
        assert self.porcelain() == before


class TestWorkflowShell(TestPlanted):
    """lint:workflow-shell and fix:workflow-shell read a top-level action."""

    DEBT = "security/workflow-shell-debt.txt"

    def test_an_oversized_block_in_a_top_level_action_is_red(self) -> None:
        """Sixteen lines in actions/x/action.yml, unlisted, are over the ceiling."""
        self.plant({"actions/x/action.yml": run_step("Big", SIXTEEN)})
        done = self.run_task("lint:workflow-shell")
        assert done.returncode == 1
        finding = (
            "lint:workflow-shell: actions/x/action.yml:9: step 'Big' holds a run:"
            " block of 16 lines, over the 15-line ceiling, and"
            " security/workflow-shell-debt.txt does not list it — move the"
            " logic out (CONTRIBUTING.md)"
        )
        expected = [finding, CEILING]
        assert done.stderr.splitlines() == expected

    def test_the_workflow_templates_are_still_read(self) -> None:
        """The selection is added to, never narrowed: templates stay."""
        self.plant({"workflow-templates/t.yml": template("Big", SIXTEEN)})
        done = self.run_task("lint:workflow-shell")
        assert done.returncode == 1
        assert done.stderr.splitlines()[0] == (
            "lint:workflow-shell: workflow-templates/t.yml:8: step 'Big' holds"
            " a run: block of 16"
            " lines, over the 15-line ceiling, and"
            " security/workflow-shell-debt.txt does not list it — move the"
            " logic out (CONTRIBUTING.md)"
        )

    def test_the_fixer_lowers_a_top_level_action_entry(self) -> None:
        """Read, the entry is lowered; unread, it would be removed as gone."""
        self.plant({
            "actions/x/action.yml": run_step("Big", SIXTEEN),
            self.DEBT: "actions/x/action.yml\tBig\t30\t# canon helper: #1\n",
        })
        done = self.run_task("fix:workflow-shell")
        assert done.returncode == 0, done.stdout + done.stderr
        assert (
            done.stdout
            == "fix:workflow-shell: 1 entry(ies) lowered, 0 removed; it never adds one\n"
        )
        assert (self.tree / self.DEBT).read_text(
            "utf-8"
        ) == "actions/x/action.yml\tBig\t16\t# canon helper: #1\n"


class TestSelection(TestPlanted):
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
        expected = [
            ".github/actions/y/action.yml",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yaml",
            "a/b/c/action.yaml",
            "action.yml",
            "actions/x/action.yml",
        ]
        assert self.selected() == expected


def belt_patterns() -> list[str]:
    """List the belt's patterns beyond the two GitHub owners.

    Returns:
        The patterns the org setting must carry.

    """
    return [
        f"{owner()}/*",
        "jdx/mise-action@*",
        "codecov/codecov-action@*",
        "ossf/scorecard-action@*",
    ]


class TestAllowlistAudit(TestPlanted):
    """audit:actions-allowlist against a stub `gh` answering the live setting.

    The audit and the lint read one list, mise/actions-allowed.sh. The
    audit used to scrape the lint's `allowed=` line; when that line
    became an array the scrape matched nothing and the audit died with
    no message (the canon's audit run 44209677660). On that body the
    first row here fails with exit 1 and empty output.
    """

    def audit(self, patterns: list[str]) -> subprocess.CompletedProcess[str]:
        """Run the audit with the org answering `patterns`.

        Returns:
            The finished process.

        """
        answer: dict[str, object] = {
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": patterns,
        }
        live = json.dumps(answer)
        stub = self.bin / "gh"
        stub.write_text("#!/usr/bin/env bash\nprintf '%s' \"${STUB_LIVE}\"\n", "utf-8")
        stub.chmod(0o755)
        self.env |= {
            "STUB_LIVE": live,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
        }
        self.plant({"security/identity.toml": ""})
        return self.run_task("audit:actions-allowlist")

    def test_the_live_setting_matching_the_belt_is_green(self) -> None:
        """The org carrying the belt's list, in any order."""
        done = self.audit(list(reversed(belt_patterns())))
        assert (done.returncode, done.stdout, done.stderr) == (
            0,
            "audit:actions-allowlist: live org allowlist matches the belt\n",
            "",
        )

    def test_a_widened_live_setting_is_drift_by_name(self) -> None:
        """One pattern more in the org than in the belt."""
        done = self.audit([*belt_patterns(), "docker/login-action@*"])
        assert done.returncode == 1
        assert done.stdout == ""
        assert "audit:actions-allowlist: patterns differ" in done.stderr
        assert "docker/login-action@*" in done.stderr
        assert "reconcile mise/actions-allowed.sh and security/README.md" in done.stderr

    @pytest.mark.parametrize(
        "task", ("lint:actions-allowed", "audit:actions-allowlist")
    )
    def test_the_lint_and_the_audit_read_one_list(self, task: object) -> None:
        """Neither task body spells a pattern of its own."""
        text = body(task)
        assert '. "${ORG_BELT_DIR}/actions-allowed.sh"' in text
        assert "mise-action@" not in text


class TestGateInstalls:
    """mise/gate-tools.toml installs the tools wherever the selection reads."""

    @pytest.mark.parametrize("key", ("aqua:suzuki-shunsuke/pinact", "zizmor"))
    def test_a_top_level_action_alone_installs_pinact_and_zizmor(
        self, key: object
    ) -> None:
        """A tree whose only action file is actions/x/action.yml."""
        reasons = gate_tools.needed(gate_tools.load_table(), ["actions/x/action.yml"])
        assert reasons[key] == "needed: actions/x/action.yml is tracked"

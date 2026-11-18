#!/usr/bin/env python3
"""Table tests for lint:hook-jobs (#1426).

lefthook runs a `script:` job from its remote cache,
.git/info/lefthook-remotes/, which every worktree of a repository
shares and which holds one canon ref at a time. The pre-push gate was
`script: ci.sh`, and a hook in a worktree pinned to another canon ref
deleted it mid-gate: the push died with exit 127 after a green gate.
The lint holds the fix: no job in the canon's lefthook/org-hooks.yml
names a script.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted git tree, not
a copy of it. Every red row plants into the canon's REAL hook file and
asserts the exact message, line number included (#650): the delivered
config is green, and the job as it shipped before #1426 is red.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = shutil.which("bash")
GIT = shutil.which("git")

NAME = "lint:hook-jobs"
HOOKS_PATH = "lefthook/org-hooks.yml"
IDENTITY_PATH = "security/identity.toml"
HOOKS = (CANON / HOOKS_PATH).read_text(encoding="utf-8")
IDENTITY = (CANON / IDENTITY_PATH).read_text(encoding="utf-8")

# The pre-push job as it shipped before #1426, at the same place.
BEFORE_JOB = "    - name: ci\n      script: ci.sh\n      runner: bash\n"


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
    run = config["tasks"][NAME]["run"]
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


def lint(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the lint in a git tree holding `files` tracked.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)  # ruff: ignore[subprocess-without-shell-equals-true]
        if files:
            subprocess.run([GIT, "add", "--", *files], cwd=root, env=env, check=True)  # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", BODY],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


def ci_job() -> tuple[int, int]:
    """Locate the delivered pre-push job in the real hook file.

    Returns:
        The job's first character offset and the offset just past it.

    """
    start = HOOKS.index("    - name: ci\n")
    end = HOOKS.index("...\n", start)
    return start, end


def with_job(job: str) -> tuple[str, int]:
    """Replace the real pre-push job with `job`.

    Returns:
        The planted hook file and the 1-based line `job`'s second line
        lands on (its first is the `- name:` line).

    """
    start, end = ci_job()
    return HOOKS[:start] + job + HOOKS[end:], HOOKS[:start].count("\n") + 2


def red(lines: list[str]) -> str:
    """Build the exact red message for `lines` (grep -n form).

    Returns:
        The stderr text.

    """
    listed = "".join(f"  line {line}\n" for line in lines)
    return (
        f"{NAME}: {HOOKS_PATH} runs a script file from lefthook's remote cache:\n"
        f"{listed}"
        "  every worktree shares that cache and a hook on another canon ref\n"
        "  deletes it mid-gate (#1426); write the job as an inline run:\n"
    )


def canon(hooks: str = HOOKS) -> dict[str, str]:
    """Build a canon tree: the identity tracked, `hooks` as the hook file.

    Returns:
        path -> text.

    """
    return {IDENTITY_PATH: IDENTITY, HOOKS_PATH: hooks}


class HookJobs(unittest.TestCase):
    """The delivered config is green; every script form is red by line."""

    def assert_red(self, files: dict[str, str], message: str) -> None:
        """Assert exit 1 with exactly `message` on stderr."""
        done = lint(files)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, message)
        self.assertEqual(done.stdout, "")

    def assert_out(self, files: dict[str, str], line: str) -> None:
        """Assert exit 0 with exactly one stdout line and no stderr."""
        done = lint(files)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{NAME}: {line}\n")
        self.assertEqual(done.stderr, "")

    def test_the_delivered_config_is_green(self) -> None:
        """The canon's own hook file, as tracked, passes."""
        self.assert_out(canon(), f"every job in {HOOKS_PATH} is an inline run:")

    def test_the_job_as_it_shipped_before_is_red(self) -> None:
        """`script: ci.sh`, the pre-push job before #1426, reds by line."""
        hooks, line = with_job(BEFORE_JOB)
        self.assert_red(canon(hooks), red([f"{line}:      script: ci.sh"]))

    def test_a_scripts_map_is_red(self) -> None:
        """The older `scripts:` map runs from the same cache."""
        job = (
            '    - name: ci\n      run: "true"\n'
            '  scripts:\n    "ci.sh":\n      runner: bash\n'
        )
        hooks, line = with_job(job)
        self.assert_red(canon(hooks), red([f"{line + 1}:  scripts:"]))

    def test_a_flow_mapping_job_is_red(self) -> None:
        """A job written as a flow mapping is read the same way."""
        flow = "    - {name: ci, script: ci.sh, runner: bash}"
        hooks, line = with_job(f"{flow}\n")
        self.assert_red(canon(hooks), red([f"{line - 1}:{flow}"]))

    def test_a_script_in_a_comment_is_green(self) -> None:
        """A comment may name the rule; only YAML keys are read."""
        hooks = HOOKS.replace(
            "    - name: ci\n", "    # script: ci.sh was the old form\n    - name: ci\n"
        )
        self.assertNotEqual(hooks, HOOKS)
        self.assert_out(canon(hooks), f"every job in {HOOKS_PATH} is an inline run:")

    def test_outside_the_canon_it_skips(self) -> None:
        """No tracked identity: not the canon, whatever the hook file says."""
        hooks, _ = with_job(BEFORE_JOB)
        self.assert_out(
            {HOOKS_PATH: hooks},
            "no tracked security/identity.toml, not the canon, skipped",
        )

    def test_the_canon_without_its_hook_file_is_red_by_name(self) -> None:
        """In the canon the hook file is a requirement, never a skip."""
        self.assert_red(
            {IDENTITY_PATH: IDENTITY},
            f"{NAME}: {HOOKS_PATH} is not tracked;"
            " the canon delivers its hooks there\n",
        )


if __name__ == "__main__":
    unittest.main()

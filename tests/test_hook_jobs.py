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
"""

import subprocess
import tempfile
from pathlib import Path

from belt import decoded
from tests import support

CANON = support.CANON
BELT = CANON / "mise"
BASH = support.tool("bash")
GIT = support.tool("git")

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
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    run = decoded.as_str(decoded.as_object(tasks[NAME], NAME)["run"], "the run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


def lint(
    files: dict[str, str], *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the lint in a git tree holding `files` tracked.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        environ = env()
        environ |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=environ, check=True)
        if files:
            subprocess.run(
                [GIT, "add", "--", *files], cwd=root, env=environ, check=True
            )
        as_mise = support.task_run("lint:hook-jobs", BODY)
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
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


# The delivered config is green; every script form is red by line.
def assert_red(files: dict[str, str], message: str, *, env: support.Env) -> None:
    """Assert exit 1 with exactly `message` on stderr."""
    done = lint(files, env=env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr == message
    assert done.stdout == ""


def assert_out(files: dict[str, str], line: str, *, env: support.Env) -> None:
    """Assert exit 0 with exactly one stdout line and no stderr."""
    done = lint(files, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{NAME}: {line}\n"
    assert done.stderr == ""


def test_the_delivered_config_is_green(env: support.Env) -> None:
    """The canon's own hook file, as tracked, passes."""
    assert_out(canon(), f"every job in {HOOKS_PATH} is an inline run:", env=env)


def test_the_job_as_it_shipped_before_is_red(env: support.Env) -> None:
    """`script: ci.sh`, the pre-push job before #1426, reds by line."""
    hooks, line = with_job(BEFORE_JOB)
    assert_red(canon(hooks), red([f"{line}:      script: ci.sh"]), env=env)


def test_a_scripts_map_is_red(env: support.Env) -> None:
    """The older `scripts:` map runs from the same cache."""
    job = (
        '    - name: ci\n      run: "true"\n'
        '  scripts:\n    "ci.sh":\n      runner: bash\n'
    )
    hooks, line = with_job(job)
    assert_red(canon(hooks), red([f"{line + 1}:  scripts:"]), env=env)


def test_a_flow_mapping_job_is_red(env: support.Env) -> None:
    """A job written as a flow mapping is read the same way."""
    flow = "    - {name: ci, script: ci.sh, runner: bash}"
    hooks, line = with_job(f"{flow}\n")
    assert_red(canon(hooks), red([f"{line - 1}:{flow}"]), env=env)


def test_a_script_in_a_comment_is_green(env: support.Env) -> None:
    """A comment may name the rule; only YAML keys are read."""
    hooks = HOOKS.replace(
        "    - name: ci\n", "    # script: ci.sh was the old form\n    - name: ci\n"
    )
    assert hooks != HOOKS
    assert_out(canon(hooks), f"every job in {HOOKS_PATH} is an inline run:", env=env)


def test_outside_the_canon_it_skips(env: support.Env) -> None:
    """No tracked identity: not the canon, whatever the hook file says."""
    hooks, _ = with_job(BEFORE_JOB)
    assert_out(
        {HOOKS_PATH: hooks},
        "no tracked security/identity.toml, not the canon, skipped",
        env=env,
    )


def test_the_canon_without_its_hook_file_is_red_by_name(env: support.Env) -> None:
    """In the canon the hook file is a requirement, never a skip."""
    assert_red(
        {IDENTITY_PATH: IDENTITY},
        f"{NAME}: {HOOKS_PATH} is not tracked; the canon delivers its hooks there\n",
        env=env,
    )

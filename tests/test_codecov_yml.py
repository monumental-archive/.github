"""Table tests for lint:codecov-yml (#1532).

Codecov is the badge feed and nothing else: the gate's coverage:check
is the one coverage threshold. The org turns Codecov's statuses off
once, in Codecov's organisation Global YAML, and a repository file
replaces that default. The lint is red by name on any tracked file at a
location Codecov reads: four names, at the root or under dev/ or
.github/ (codecov/umbrella libs/shared/shared/yaml/fetcher.py).

The body under test is the task's own `run` string, read out of
mise/config.toml with decoded.load_toml and run by bash in a planted git
tree, not a copy of it. Every red row asserts the exact message (#650).
"""

import subprocess
import tempfile
from pathlib import Path

from belt import decoded
from tests import support

BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")

NAME = "lint:codecov-yml"
GREEN = f"{NAME}: no Codecov config tracked; the org's Global YAML applies\n"
NAMES = ("codecov.yml", ".codecov.yml", "codecov.yaml", ".codecov.yaml")
DIRS = ("", "dev/", ".github/")
# edtf's file as it stood on main, 2026-11-18: quoted 'off' strings,
# which Codecov's validator rejects, so the statuses stayed on.
EDTF = "coverage:\n  status:\n    project: 'off'\n    patch: 'off'\n"


def body() -> str:
    """Read the lint's run string out of the belt config.

    Returns:
        The task body.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[NAME], NAME)
    return decoded.as_str(task["run"], f"{NAME}'s run")


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
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=environ, check=True)
        if files:
            subprocess.run(
                [GIT, "add", "--", *files], cwd=root, env=environ, check=True
            )
        as_mise = support.task_run(NAME, BODY)
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


def red(paths: list[str]) -> str:
    """Build the exact red message for `paths`, in the lint's order.

    Returns:
        The stderr text.

    """
    listed = "".join(f"  {path}\n" for path in paths)
    return (
        f"{NAME}: tracked Codecov config overrides the org's Global YAML:\n"
        f"{listed}"
        "  the org turns Codecov's statuses off once, org-wide (#1532);\n"
        "  remove the file; docs/runbook.md's Codecov entry holds the rule\n"
    )


# No tracked Codecov config is green; every location Codecov reads is red.
def assert_red(files: dict[str, str], paths: list[str], *, env: support.Env) -> None:
    """Assert exit 1 with exactly the message naming `paths`."""
    done = lint(files, env=env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr == red(paths)
    assert done.stdout == ""


def assert_green(files: dict[str, str], *, env: support.Env) -> None:
    """Assert exit 0 with exactly the green line and no stderr."""
    done = lint(files, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == GREEN
    assert done.stderr == ""


def test_a_tree_tracking_none_is_green(env: support.Env) -> None:
    """No Codecov config anywhere: the org default applies."""
    assert_green({"README.md": "x\n"}, env=env)


def test_edtfs_file_is_red_by_name(env: support.Env) -> None:
    """The file edtf tracked, .github/codecov.yml, reds by name."""
    assert_red({".github/codecov.yml": EDTF}, [".github/codecov.yml"], env=env)


def test_every_location_codecov_reads_is_red(env: support.Env) -> None:
    """Each of the twelve locations reds alone, named."""
    for folder in DIRS:
        for name in NAMES:
            path = f"{folder}{name}"
            assert_red({path: EDTF}, [path], env=env)


def test_every_hit_is_named(env: support.Env) -> None:
    """Several files are all listed, root first."""
    assert_red(
        {"dev/.codecov.yaml": EDTF, "codecov.yml": EDTF},
        ["codecov.yml", "dev/.codecov.yaml"],
        env=env,
    )


def test_an_untracked_file_is_green(env: support.Env) -> None:
    """Tracked files only: an untracked config is not the tree's."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        environ = env()
        subprocess.run([GIT, "init", "-q"], cwd=root, env=environ, check=True)
        (root / "codecov.yml").write_text(EDTF, encoding="utf-8")
        as_mise = support.task_run(NAME, BODY)
        done = subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
    assert done.returncode == 0, done.stderr
    assert done.stdout == GREEN


def test_other_locations_are_green(env: support.Env) -> None:
    """Codecov reads no deeper folder and no other name."""
    assert_green(
        {
            "docs/codecov.yml": EDTF,
            ".github/workflows/codecov.yml": EDTF,
            "codecov.json": EDTF,
        },
        env=env,
    )

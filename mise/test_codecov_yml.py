#!/usr/bin/env python3
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

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

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


def lint(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the lint in a git tree holding `files` tracked.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = hermetic.env()
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)
        if files:
            subprocess.run([GIT, "add", "--", *files], cwd=root, env=env, check=True)
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", BODY],
            cwd=root,
            env=env,
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


class CodecovYml(unittest.TestCase):
    """No tracked Codecov config is green; every location Codecov reads is red."""

    def assert_red(self, files: dict[str, str], paths: list[str]) -> None:
        """Assert exit 1 with exactly the message naming `paths`."""
        done = lint(files)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, red(paths))
        self.assertEqual(done.stdout, "")

    def assert_green(self, files: dict[str, str]) -> None:
        """Assert exit 0 with exactly the green line and no stderr."""
        done = lint(files)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, GREEN)
        self.assertEqual(done.stderr, "")

    def test_a_tree_tracking_none_is_green(self) -> None:
        """No Codecov config anywhere: the org default applies."""
        self.assert_green({"README.md": "x\n"})

    def test_edtfs_file_is_red_by_name(self) -> None:
        """The file edtf tracked, .github/codecov.yml, reds by name."""
        self.assert_red({".github/codecov.yml": EDTF}, [".github/codecov.yml"])

    def test_every_location_codecov_reads_is_red(self) -> None:
        """Each of the twelve locations reds alone, named."""
        for folder in DIRS:
            for name in NAMES:
                path = f"{folder}{name}"
                with self.subTest(path=path):
                    self.assert_red({path: EDTF}, [path])

    def test_every_hit_is_named(self) -> None:
        """Several files are all listed, root first."""
        self.assert_red(
            {"dev/.codecov.yaml": EDTF, "codecov.yml": EDTF},
            ["codecov.yml", "dev/.codecov.yaml"],
        )

    def test_an_untracked_file_is_green(self) -> None:
        """Tracked files only: an untracked config is not the tree's."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = hermetic.env()
            subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)
            (root / "codecov.yml").write_text(EDTF, encoding="utf-8")
            done = subprocess.run(
                [BASH, "-euo", "pipefail", "-c", BODY],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, GREEN)

    def test_other_locations_are_green(self) -> None:
        """Codecov reads no deeper folder and no other name."""
        self.assert_green({
            "docs/codecov.yml": EDTF,
            ".github/workflows/codecov.yml": EDTF,
            "codecov.json": EDTF,
        })


if __name__ == "__main__":
    unittest.main()

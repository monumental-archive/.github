#!/usr/bin/env python3
"""lint:python-module-names refuses a hyphenated Python file (#1634).

Each row runs the task's own `run` body, read from mise/config.toml,
under bash in a planted git tree, and asserts its message: a planted
hyphenated file is red by name, a tree of module names is green, a
tree with no Python skips. The canon's own tree is the last row.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`. Every git call passes a scrubbed environment.
"""

import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
TASK = "lint:python-module-names"
hermetic = SimpleNamespace(**runpy.run_path(str(BELT / "hermetic.py")))


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"][TASK]["run"]


def lint(files: list[str]) -> subprocess.CompletedProcess[str]:
    """Plant a tree tracking files and run the lint in it.

    Returns:
        The finished run.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "repo"
        tree.mkdir()
        env = hermetic.env(root / "home")
        (tree / "README.md").write_text("x\n", encoding="utf-8")
        for name in files:
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n", encoding="utf-8")
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class PythonModuleNames(unittest.TestCase):
    """A Python file is named as a module, or the lint names it."""

    def test_a_planted_hyphenated_file_is_red_by_name(self) -> None:
        """Each hyphenated file is named; a hyphenated directory is not."""
        done = lint([
            "release/caller-pins.py",
            "stubs/old-name.pyi",
            "my-tools/fine_name.py",
            "mise/module_name.py",
        ])
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            f"{TASK}: not a module name, so nothing can import it (#1634):\n"
            "  release/caller-pins.py\n"
            "  stubs/old-name.pyi\n"
            "  name it with underscores in place of the hyphens\n",
            done.stderr,
        )

    def test_module_names_are_green(self) -> None:
        """A tree of module names passes."""
        done = lint(["mise/module_name.py", "my-tools/fine_name.py"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: ok\n", done.stdout)

    def test_a_vendored_file_is_not_ours(self) -> None:
        """vendor/ is someone else's naming, as in every belt lint."""
        done = lint(["vendor/their-tool.py"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: no Python tracked, skipped\n", done.stdout)

    def test_no_python_skips(self) -> None:
        """A tree with no Python skips clean."""
        done = lint([])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: no Python tracked, skipped\n", done.stdout)


if __name__ == "__main__":
    unittest.main()

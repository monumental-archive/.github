#!/usr/bin/env python3
"""lint:python-module-names refuses a file nothing can import by name.

A hyphenated file is not a module name (#1634); two files that would be
one module under the derived import roots reach only one of them on an
import (#1737), refused by python_roots.py's own check. Each row runs
the task's own `run` body, read from mise/config.toml, under bash in a
planted git tree with the belt as ORG_BELT_DIR, and asserts its
message: each planted defect is red by name, a tree of module names is
green, a tree with no Python skips. The canon's own tree is the last
row.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`. Every git call passes a scrubbed environment.
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
TASK = "lint:python-module-names"


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], "the task's run")


def run(tree: Path, home: Path) -> subprocess.CompletedProcess[str]:
    """Run the lint's body in a tree, with the belt as ORG_BELT_DIR.

    Returns:
        The finished run.

    """
    as_mise = hermetic.task_run(TASK, body())
    return subprocess.run(
        as_mise.argv,
        cwd=tree,
        env=hermetic.env(
            home,
            ORG_BELT_DIR=str(BELT),
        )
        | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


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
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
        return run(tree, root / "home")


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

    def test_two_files_that_are_one_module_are_red_by_name(self) -> None:
        """One basename in two roots is one module: both are named (#1737)."""
        done = lint(["mise/test_steps.py", "release/test_steps.py"])
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            "python_roots: these tracked files would be one module,"
            " so an import of that name reaches only one of them:\n"
            "  test_steps: mise/test_steps, release/test_steps\n"
            f"{TASK}: rename one of each pair above so every module"
            " name is one file's\n",
            done.stderr,
        )

    def test_a_stub_beside_its_module_is_one_module(self) -> None:
        """A `.pyi` beside its own `.py` is that module's stub."""
        done = lint(["mise/helper.py", "mise/helper.pyi"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: ok\n", done.stdout)

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

    def test_the_canon_tree_is_green(self) -> None:
        """The canon's own tracked Python passes both halves."""
        with tempfile.TemporaryDirectory() as tmp:
            done = run(BELT.parent, Path(tmp) / "home")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: ok\n", done.stdout)


if __name__ == "__main__":
    unittest.main()

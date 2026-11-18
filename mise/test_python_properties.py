#!/usr/bin/env python3
"""Table tests for `test:python-properties` and its audit half (#1332).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/python-properties.sh` and
`mise/python-properties.py`. The rows that run a property use the
belt's real Hypothesis tool venv, found on PATH as the gate finds it:
a stub could not print Hypothesis's own falsifying example, which is
what a failing row exists to assert. The rows about the shell's own
refusals (nothing tracked, Hypothesis missing, the wrong interpreter,
an ordinary test importing Hypothesis) need no Hypothesis at all.

This file never imports Hypothesis: it runs the tool venv's
interpreter as a subprocess, so the ordinary suite stays runnable on
an interpreter with nothing installed.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import os
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
PYTHON = shutil.which("python3")
HYPOTHESIS = shutil.which("hypothesis")

CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
GATE = CONFIG["tasks"]["test:python-properties"]["run"]
AUDIT = CONFIG["tasks"]["audit:python-properties"]["run"]

HOLDS = """\
import unittest
from hypothesis import given, strategies as st


class Holds(unittest.TestCase):
    @given(st.integers())
    def test_double_is_even(self, n):
        self.assertEqual((2 * n) % 2, 0)
"""

FAILS = """\
import unittest
from hypothesis import given, strategies as st


class Fails(unittest.TestCase):
    @given(st.integers(min_value=0))
    def test_small(self, n):
        self.assertLess(n, 10)
"""

NO_PROPERTY = """\
import unittest


class Plain(unittest.TestCase):
    def test_plain(self):
        self.assertTrue(True)
"""

OWN_BOUNDS = """\
import unittest
from hypothesis import given, settings, strategies as st


class Bounded(unittest.TestCase):
    @settings(max_examples=3)
    @given(st.integers())
    def test_few(self, n):
        self.assertIsInstance(n, int)
"""

# A Hypothesis venv built on some other interpreter: its python answers
# the version probe with a version that is not the belt's.
STUB_PYTHON = """\
#!/usr/bin/env bash
echo "3.0.0 (stub interpreter)"
"""


# Every inherited GIT_* goes (#857): under the pre-push hook git exports
# GIT_DIR and friends, and `git init` / `git add` would then write the
# REAL repository's index instead of the planted one. Measured here:
# the first push of this file staged the deletion of every tracked file.
_GIT_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def plant(root: Path, files: dict[str, str]) -> None:
    """Make `root` a git tree tracking `files`."""
    subprocess.run([GIT, "init", "-q", str(root)], check=True, env=_GIT_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "-C", str(root), "add", "-A"], check=True, env=_GIT_ENV
        )


def run(
    body: str,
    files: dict[str, str],
    *,
    tool_dir: str | None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a task body in a planted tree.

    Returns:
        The finished process, text mode.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        plant(root, files)
        path = [str(Path(PYTHON).parent), str(Path(GIT).parent), "/usr/bin", "/bin"]
        if tool_dir:
            path.insert(0, tool_dir)
        environ = {
            "PATH": os.pathsep.join(path),
            "HOME": scratch,
            "ORG_BELT_DIR": str(BELT),
            **(env or {}),
        }
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body],
            cwd=root,
            env=environ,
            capture_output=True,
            text=True,
            check=False,
        )


def real_tool_dir(test: unittest.TestCase) -> str:
    """Name the directory holding the belt's `hypothesis` entry point.

    Returns:
        The directory, which the row puts on PATH.

    """
    test.assertIsNotNone(
        HYPOTHESIS,
        "hypothesis is not on PATH: the belt pins pipx:hypothesis and"
        " `mise run test` puts it there (run: mise install)",
    )
    return str(Path(HYPOTHESIS).parent)


class Refusals(unittest.TestCase):
    """What the shell decides before any property runs."""

    def test_no_prop_file_skips_clean_by_name(self) -> None:
        """Nothing tracked: exit 0, skipped, named."""
        done = run(GATE, {"test_x.py": NO_PROPERTY}, tool_dir=None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, "test:python-properties: no prop_*.py tracked, skipped\n"
        )

    def test_audit_skips_clean_by_name(self) -> None:
        """The audit half skips the same way, under its own name."""
        done = run(AUDIT, {}, tool_dir=None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout, "audit:python-properties: no prop_*.py tracked, skipped\n"
        )

    def test_a_prop_file_without_hypothesis_is_red(self) -> None:
        """A property file and no tool: red, with the remedy."""
        done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=None)
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:python-properties: prop_*.py tracked but hypothesis is missing",
            done.stderr,
        )

    def test_an_ordinary_test_importing_hypothesis_is_red(self) -> None:
        """A test_*.py that imports Hypothesis: red, the file named."""
        done = run(
            GATE,
            {"prop_x.py": HOLDS, "pkg/test_leak.py": HOLDS},
            tool_dir=None,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "import Hypothesis (or cannot be read) but are not prop_*.py files:",
            done.stderr,
        )
        self.assertIn("  pkg/test_leak.py\n", done.stderr)

    def test_hypothesis_source_in_a_string_is_not_an_import(self) -> None:
        """A fixture carrying Hypothesis source as text: not a leak."""
        fixture = f"HOLDS = {HOLDS!r}\n"
        done = run(GATE, {"prop_x.py": HOLDS, "test_x.py": fixture}, tool_dir=None)
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("import Hypothesis", done.stderr)
        self.assertIn("prop_*.py tracked but hypothesis is missing", done.stderr)

    def test_a_venv_on_another_interpreter_is_red(self) -> None:
        """The tool venv's python is not the belt's: red, both named."""
        with tempfile.TemporaryDirectory() as venv:
            bin_dir = Path(venv) / "bin"
            bin_dir.mkdir()
            for name in ("hypothesis", "python"):
                (bin_dir / name).write_text(STUB_PYTHON, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
            done = run(GATE, {"prop_x.py": HOLDS}, tool_dir=str(bin_dir))
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:python-properties: hypothesis runs on '3.0.0 (stub interpreter)'",
            done.stderr,
        )
        self.assertIn("mise install -f pipx:hypothesis", done.stderr)


class Properties(unittest.TestCase):
    """Rows that run a property on the belt's Hypothesis."""

    def test_a_passing_file_holds(self) -> None:
        """One property that holds: exit 0, the test and the count."""
        done = run(GATE, {"pkg/prop_ok.py": HOLDS}, tool_dir=real_tool_dir(self))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "pkg/prop_ok.py: prop_0_prop_ok.Holds.test_double_is_even ... ok\n"
            "test:python-properties: 1 test(s) in 1 file(s) held\n",
        )

    def test_a_failing_property_is_red_with_its_example(self) -> None:
        """A property that fails: red, the shrunk example printed."""
        done = run(GATE, {"prop_bad.py": FAILS}, tool_dir=real_tool_dir(self))
        self.assertEqual(done.returncode, 1)
        self.assertIn("prop_0_prop_bad.Fails.test_small ... FAIL\n", done.stdout)
        self.assertIn("AssertionError: 10 not less than 10", done.stderr)
        self.assertIn("Failing test case: test_small(", done.stderr)
        self.assertIn("    n=10,\n", done.stderr)

    def test_a_file_with_no_property_is_refused(self) -> None:
        """A prop_*.py holding no @given test: red, named."""
        done = run(GATE, {"prop_none.py": NO_PROPERTY}, tool_dir=real_tool_dir(self))
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:python-properties: prop_none.py:"
            " holds no Hypothesis test (no @given)",
            done.stderr,
        )

    def test_a_test_restating_a_bound_is_refused(self) -> None:
        """@settings(max_examples=…) on a test: red, the bound named."""
        done = run(GATE, {"prop_own.py": OWN_BOUNDS}, tool_dir=real_tool_dir(self))
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "prop_own.py: prop_0_prop_own.Bounded.test_few sets its own bounds"
            " (max_examples=3); the belt's profile owns them",
            done.stderr,
        )

    def test_the_audit_prints_its_seed_and_honours_a_given_one(self) -> None:
        """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
        done = run(
            AUDIT,
            {"prop_bad.py": FAILS},
            tool_dir=real_tool_dir(self),
            env={"ORG_PROPERTY_SEED": "7"},
        )
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stdout.startswith(
                "audit:python-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
            ),
            done.stdout,
        )
        self.assertIn("    n=10,\n", done.stderr)
        self.assertIn(
            "audit:python-properties: seed 7 — commit each falsifying example above"
            " as an @example row with its fix",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()

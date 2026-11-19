#!/usr/bin/env python3
"""audit:python-types runs mypy on the belt's terms (#1604).

Each row runs the task's own `run` body, read from mise/config.toml, in
a planted git tree, against a belt directory holding this tree's
python-types.sh, python-types-seams.py, mypy.ini and config.toml (the
pin). The STUB rows put a `mypy` on PATH that records its arguments and
prints and exits as the row says: they hold the task's wiring, which
mypy cannot see. The REAL rows run the belt's pinned mypy over planted
files, for what the delivered config makes mypy refuse; they run where
that mypy is installed and skip where the gate disables it, which is
every tree until the task is renamed `lint:python-types` and its
gate-tools row names it (#1604's last pull request).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:python-types"
BELT_FILES = (
    "python-types.sh",
    "python-types-seams.py",
    "mypy.ini",
    "config.toml",
    "org_hypothesis.py",
)

STUB = """\
#!/usr/bin/env bash
if [[ ${1:-} == --version ]]; then
  echo "mypy ${STUB_VERSION} (compiled: yes)"
  exit 0
fi
printf '%s\\n' "$@" > "${STUB_LOG}"
out=${STUB_OUT:-}
[[ -z ${out} ]] || printf '%s\\n' "${out//@CFG@/$2}"
exit "${STUB_RC:-0}"
"""


def belt() -> dict[str, object]:
    """Read the belt's config.

    Returns:
        mise/config.toml, decoded.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    Raises:
        TypeError: the config defines no such task.

    """
    tasks = belt()["tasks"]
    task = tasks.get(LABEL) if isinstance(tasks, dict) else None
    if not isinstance(task, dict):
        msg = f"mise/config.toml defines no {LABEL}"
        raise TypeError(msg)
    return str(task["run"])


def pinned() -> str:
    """Read the belt's mypy pin.

    Returns:
        The version `pipx:mypy` is pinned at.

    Raises:
        TypeError: the config has no tools table.

    """
    tools = belt()["tools"]
    if not isinstance(tools, dict):
        msg = "mise/config.toml has no [tools]"
        raise TypeError(msg)
    return str(tools["pipx:mypy"])


def real_mypy() -> str | None:
    """Find the belt's pinned mypy, if this machine has it.

    Returns:
        Its path when `mypy --version` names the pin, else None.

    """
    found = shutil.which("mypy")
    if found is None:
        return None
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [found, "--version"], capture_output=True, text=True, check=False
    )
    return found if done.stdout.split()[1:2] == [pinned()] else None


@dataclass(frozen=True)
class Run:
    """What one run of the task left: exit status, output, stub args."""

    returncode: int
    stdout: str
    stderr: str
    args: list[str]


def run(
    files: dict[str, str],
    *,
    belt_files: tuple[str, ...] = BELT_FILES,
    stub: dict[str, str] | None = None,
) -> Run:
    """Run the task in a planted tree.

    `files` are written and tracked. With `stub`, a stub mypy leads
    PATH and `stub` is its environment; without, PATH is the suite's.

    Returns:
        The run's exit status, output and the stub's arguments.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "repo"
        tree.mkdir()
        for name, text in files.items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text, encoding="utf-8")
        belt = root / "belt"
        belt.mkdir()
        for name in belt_files:
            shutil.copy(BELT / name, belt / name)
        env = hermetic.env(ORG_BELT_DIR=str(belt), ORG_PYTHON=sys.executable)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "add", "-A"], cwd=tree, env=env, check=True)
        log = root / "stub.log"
        if stub is not None:
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "mypy").write_text(STUB, encoding="utf-8")
            (bindir / "mypy").chmod(0o755)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env |= {"STUB_LOG": str(log), "STUB_VERSION": pinned(), **stub}
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        args = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return Run(
            returncode=done.returncode,
            stdout=done.stdout.replace(str(belt), "<belt>"),
            stderr=done.stderr.replace(str(belt), "<belt>"),
            args=[a.replace(str(belt), "<belt>") for a in args],
        )


CLEAN = {"a.py": "X: int = 1\n"}


class TheWiring(unittest.TestCase):
    """The task's own decisions, with a stub mypy."""

    def test_no_python_is_skipped_clean(self) -> None:
        """A tree with no .py or .pyi skips, and mypy is never asked."""
        done = run({"README.md": "x\n"}, stub={})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{LABEL}: no Python tracked, skipped\n", done.stdout)
        self.assertEqual([], done.args)

    def test_the_delivered_config_missing_is_red(self) -> None:
        """No mypy.ini in the belt: red by name, mypy never asked."""
        files = tuple(f for f in BELT_FILES if f != "mypy.ini")
        done = run(CLEAN, belt_files=files, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{LABEL}: <belt>/mypy.ini is missing — the belt did not arrive\n",
            done.stderr,
        )
        self.assertEqual([], done.args)

    def test_a_mypy_that_is_not_the_pin_is_red(self) -> None:
        """A stale install is named with the pin, before it checks anything."""
        done = run(CLEAN, stub={"STUB_VERSION": "0.0.1"})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{LABEL}: mypy 0.0.1 is running but the belt pins '{pinned()}'\n",
            done.stderr,
        )
        self.assertEqual([], done.args)

    def test_the_belt_config_is_the_one_passed(self) -> None:
        """The stub gets --config-file <belt>/mypy.ini and every tracked file."""
        done = run({**CLEAN, "mypy.ini": "[mypy]\nignore_errors = True\n"}, stub={})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(["--config-file", "<belt>/mypy.ini"], done.args[:2])
        self.assertEqual(["a.py"], done.args[4:])
        self.assertIn(f"{LABEL}: 1 file(s) clean under mypy {pinned()}\n", done.stdout)

    def test_a_finding_is_red(self) -> None:
        """The task exits as mypy did, with its output kept."""
        out = 'a.py:1: error: Expression has type "Any"  [misc]'
        done = run(CLEAN, stub={"STUB_OUT": out, "STUB_RC": "1"})
        self.assertEqual(1, done.returncode)
        self.assertIn(out + "\n", done.stdout)
        self.assertIn(f"{LABEL}: mypy exited 1;", done.stderr)

    def test_a_config_mypy_could_not_load_is_red(self) -> None:
        """A warning about the config is red, though mypy exits 0."""
        out = "@CFG@: [mypy]: Unrecognized option: stirct = True"
        done = run(CLEAN, stub={"STUB_OUT": out})
        self.assertEqual(1, done.returncode)
        self.assertIn("<belt>/mypy.ini: [mypy]: Unrecognized option", done.stdout)
        self.assertIn(
            f"{LABEL}: mypy did not load <belt>/mypy.ini cleanly (above)", done.stderr
        )

    def test_a_bare_ignore_is_red(self) -> None:
        """`# type: ignore` with no code is named, even at the module's top."""
        done = run({"a.py": "# type: ignore\nX: int = 1\n"}, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn("  a.py:1: `# type: ignore` names no error code\n", done.stderr)

    def test_an_inline_config_is_red(self) -> None:
        """A `# mypy:` comment lowering its file's level is named."""
        done = run({"a.py": "# mypy: ignore-errors\nX: int = 1\n"}, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "  a.py:1: an inline `# mypy:` comment sets options for its file\n",
            done.stderr,
        )

    def test_no_type_check_is_red(self) -> None:
        """`typing.no_type_check`, imported or by attribute, is named."""
        text = "import typing\n\n\n@typing.no_type_check\ndef f(x):\n    return x\n"
        done = run({"a.py": text}, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn("  a.py:4: `no_type_check` skips type checking\n", done.stderr)

    def test_markers_in_strings_are_not_seams(self) -> None:
        """The same text inside a string is not a finding."""
        text = 'S = "# type: ignore  # mypy: ignore-errors  no_type_check"\n'
        done = run({"a.py": text}, stub={})
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_coded_ignore_is_not_a_seam(self) -> None:
        """`# type: ignore[<code>]` names its rule and passes the helper."""
        done = run({"a.py": "X: int = 1  # type: ignore[misc]\n"}, stub={})
        self.assertEqual(0, done.returncode, done.stderr)


@unittest.skipUnless(real_mypy(), "the belt's pinned mypy is not installed here")
class TheConfig(unittest.TestCase):
    """What the delivered config makes the pinned mypy refuse."""

    def test_a_finding_is_red(self) -> None:
        """An unvalidated json value is refused where it is read."""
        text = "import json\n\n\ndef f(s: str) -> int:\n    return len(json.loads(s))\n"
        done = run({"a.py": text})
        self.assertEqual(1, done.returncode)
        self.assertIn('a.py:5: error: Expression has type "Any"  [misc]', done.stdout)

    def test_an_unused_suppression_is_red(self) -> None:
        """A coded ignore that suppresses nothing is mypy's unused-ignore."""
        done = run({"a.py": "X: int = 1  # type: ignore[misc]\n"})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            'a.py:1: error: Unused "type: ignore" comment  [unused-ignore]', done.stdout
        )

    def test_a_used_coded_suppression_passes(self) -> None:
        """The one escape: a coded ignore that suppresses its finding."""
        done = run({"a.py": 'X: int = "s"  # type: ignore[assignment]\n'})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_a_repository_config_does_not_outrank_the_belt(self) -> None:
        """mypy.ini and pyproject.toml saying ignore_errors change nothing."""
        files = {
            "a.py": 'X: int = "s"\n',
            "mypy.ini": "[mypy]\nignore_errors = True\n",
            "pyproject.toml": "[tool.mypy]\nignore_errors = true\n",
        }
        done = run(files)
        self.assertEqual(1, done.returncode)
        self.assertIn("a.py:1: error: Incompatible types in assignment", done.stdout)


PROPERTY = """\
\"\"\"A property.\"\"\"

import unittest

from hypothesis import strategies as st
from org_hypothesis import given


class Holds(unittest.TestCase):
    \"\"\"Doubling.\"\"\"

    @given(st.integers(), st.data())
    def test_double(self, n: int, data: st.DataObject) -> None:
        \"\"\"Even.\"\"\"
        self.assertEqual((2 * n + 2 * data.draw(st.integers())) % 2, 0)
"""


@unittest.skipUnless(real_mypy(), "the belt's pinned mypy is not installed here")
@unittest.skipUnless(
    shutil.which("hypothesis"), "the belt's Hypothesis is not installed here"
)
class TheTypedGiven(unittest.TestCase):
    """The belt's `given` under the unmodified config (#1604)."""

    def test_a_property_is_clean(self) -> None:
        """No finding on the decorator or the decorated test."""
        done = run({"prop_a.py": PROPERTY})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn(f"{LABEL}: 1 file(s) clean under mypy", done.stdout)

    def test_the_body_is_still_checked(self) -> None:
        """An `int` parameter used as a `str` is red."""
        text = PROPERTY.replace("% 2, 0)", "% 2, 0)\n        n.upper()")
        done = run({"prop_a.py": text})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            'error: "int" has no attribute "upper"  [attr-defined]', done.stdout
        )

    def test_a_strategy_that_does_not_match_its_parameter_is_red(self) -> None:
        """An `int` strategy feeding a `str` parameter names the argument."""
        text = PROPERTY.replace("n: int,", "n: str,")
        done = run({"prop_a.py": text})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "error: Argument 1 has incompatible type"
            ' "Callable[[Holds, str, DataObject], None]";'
            ' expected "Callable[[Holds, int, DataObject], None]"  [arg-type]',
            done.stdout,
        )


if __name__ == "__main__":
    unittest.main()

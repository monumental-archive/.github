#!/usr/bin/env python3
"""lint:python-types runs mypy on the belt's terms (#1604).

Each row runs the task's own `run` body, read from mise/config.toml, in
a planted git tree, against a belt directory holding this tree's
python-types.sh, python_types_seams.py, mypy.ini and config.toml (the
pin). The STUB rows put a `mypy` on PATH that records its arguments and
prints and exits as the row says: they hold the task's wiring, which
mypy cannot see. The REAL rows run the belt's pinned mypy over planted
files, for what the delivered config makes mypy refuse; they run where
that mypy is installed and skip where the gate disables it: a tree
with no tracked `.py` or `.pyi`, by its gate-tools row.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import override

import decoded
import hermetic
import python_roots
import python_types_seams

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:python-types"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
LABEL = "lint:python-types"
# What the seam guard says of every type-ignore comment, coded or bare.
IGNORED = "`# type: ignore` hides a finding; type the code instead"

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
    return decoded.load_toml((BELT / "config.toml").read_bytes())


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
    found = hermetic.optional_tool("mypy")
    if found is None:
        return None
    done = subprocess.run(
        [found, "--version"], capture_output=True, text=True, check=False
    )
    return found if done.stdout.split()[1:2] == [pinned()] else None


# Each looked up once, at import, as the skip decorators they replace did.
MYPY = real_mypy()
HYPOTHESIS = hermetic.optional_tool("hypothesis")


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
    missing: tuple[str, ...] = (),
    stub: dict[str, str] | None = None,
    stale_venv: bool = False,
) -> Run:
    """Run the task in a planted tree.

    `files` are written and tracked. The belt is the whole one, less
    the files `missing` names. With `stub`, a stub mypy leads PATH and
    `stub` is its environment; without, PATH is the suite's. With
    `stale_venv` (and `stub`), a `hypothesis` entry point with no
    interpreter beside it leads PATH too: a venv left on a removed
    Python.

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
        belt = hermetic.belt(root / "belt")
        for name in missing:
            (belt / name).unlink()
        env = hermetic.env(ORG_BELT_DIR=str(belt), ORG_PYTHON=sys.executable)
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        subprocess.run([GIT, "add", "-A"], cwd=tree, env=env, check=True)
        log = root / "stub.log"
        if stub is not None:
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "mypy").write_text(STUB, encoding="utf-8")
            (bindir / "mypy").chmod(0o755)
            if stale_venv:
                (bindir / "hypothesis").write_text("#!/bin/sh\n", encoding="utf-8")
                (bindir / "hypothesis").chmod(0o755)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env |= {"STUB_LOG": str(log), "STUB_VERSION": pinned(), **stub}
        as_mise = hermetic.task_run("lint:python-types", body())
        done = subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=env | as_mise.env,
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
        self.assertEqual(list[str](), done.args)

    def test_a_hypothesis_venv_with_no_interpreter_is_red(self) -> None:
        """The Hypothesis venv mypy would read has no python: refused (#1788)."""
        done = run(CLEAN, stub={}, stale_venv=True)
        self.assertEqual(1, done.returncode)
        self.assertRegex(
            done.stderr,
            rf"\A{LABEL}: hypothesis runs on 'no interpreter' but the suite's"
            r" python3 is '3\.[^']+' — the tool venv must be built on the"
            r" belt's python pin \(reinstall: mise install -f pipx:hypothesis\)\n\Z",
        )
        self.assertEqual(list[str](), done.args)

    def test_the_delivered_config_missing_is_red(self) -> None:
        """No mypy.ini in the belt: red by name, mypy never asked."""
        done = run(CLEAN, missing=("mypy.ini",), stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{LABEL}: <belt>/mypy.ini is missing — the belt did not arrive\n",
            done.stderr,
        )
        self.assertEqual(list[str](), done.args)

    def test_a_mypy_that_is_not_the_pin_is_red(self) -> None:
        """A stale install is named with the pin, before it checks anything."""
        done = run(CLEAN, stub={"STUB_VERSION": "0.0.1"})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{LABEL}: mypy 0.0.1 is running but the belt pins '{pinned()}'\n",
            done.stderr,
        )
        self.assertEqual(list[str](), done.args)

    def test_the_belt_config_is_the_one_passed(self) -> None:
        """The stub gets --config-file <belt>/mypy.ini and every tracked file."""
        done = run({**CLEAN, "mypy.ini": "[mypy]\nignore_errors = True\n"}, stub={})
        self.assertEqual(0, done.returncode, done.stderr)
        config = ["--config-file", "<belt>/mypy.ini"]
        files = ["a.py"]
        self.assertEqual(config, done.args[:2])
        self.assertEqual(files, done.args[4:])
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
        self.assertIn(f"  a.py:1: {IGNORED}\n", done.stderr)

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

    def test_two_files_that_are_one_module_are_red_by_name(self) -> None:
        """Two roots each holding helper.py: named, and mypy never asked."""
        files = {"a/helper.py": "X = 1\n", "b/helper.py": "X = 2\n"}
        done = run(files, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn("  helper: a/helper, b/helper\n", done.stderr)
        self.assertIn(f"{LABEL}: rename one of each pair above", done.stderr)
        self.assertEqual(list[str](), done.args)

    def test_a_coded_ignore_is_red(self) -> None:
        """`# type: ignore[<code>]` hides its finding too (owner, #1604)."""
        done = run({"a.py": "f(1)  # type: ignore[arg-type]\n"}, stub={})
        self.assertEqual(1, done.returncode)
        self.assertIn(f"  a.py:1: {IGNORED}\n", done.stderr)


def seams(text: str) -> hermetic.Called:
    """Call python_types_seams in-process on one planted `a.py` (#1697).

    Returns:
        The status and both streams.

    """
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text(text, encoding="utf-8")
        return hermetic.call_main(lambda: python_types_seams.main(["a.py"]), cwd=tmp)


def roots(*files: str) -> hermetic.Called:
    """Call python_roots in-process over planted files, as the script does.

    Returns:
        The status and both streams.

    """
    with tempfile.TemporaryDirectory() as tmp:
        for name in files:
            (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / name).write_text("X = 1\n", encoding="utf-8")
        return hermetic.call_main(
            lambda: python_roots.main([str(BELT), *files]), cwd=tmp
        )


class TheHelpers(unittest.TestCase):
    """The seams and the roots, called in the test process (#1697)."""

    def test_a_bare_ignore_is_a_seam(self) -> None:
        """`# type: ignore` with no code is named, even at the module's top."""
        done = seams("# type: ignore\nX: int = 1\n")
        self.assertEqual((1, f"a.py:1: {IGNORED}\n"), (done.returncode, done.stdout))

    def test_an_inline_config_is_a_seam(self) -> None:
        """A `# mypy:` comment lowering its file's level is named."""
        done = seams("# mypy: ignore-errors\nX: int = 1\n")
        self.assertEqual(
            (1, "a.py:1: an inline `# mypy:` comment sets options for its file\n"),
            (done.returncode, done.stdout),
        )

    def test_no_type_check_is_a_seam(self) -> None:
        """`typing.no_type_check`, by attribute, is named."""
        text = "import typing\n\n\n@typing.no_type_check\ndef f(x):\n    return x\n"
        done = seams(text)
        self.assertEqual(
            (1, "a.py:4: `no_type_check` skips type checking\n"),
            (done.returncode, done.stdout),
        )

    def test_markers_in_strings_are_not_seams(self) -> None:
        """The same text inside a string is not a finding."""
        done = seams('S = "# type: ignore  # mypy: ignore-errors  no_type_check"\n')
        self.assertEqual((0, ""), (done.returncode, done.stdout))

    def test_a_coded_ignore_is_a_seam(self) -> None:
        """`# type: ignore[arg-type]` is named: it hides its finding."""
        done = seams("f(1)  # type: ignore[arg-type]\n")
        self.assertEqual((1, f"a.py:1: {IGNORED}\n"), (done.returncode, done.stdout))

    def test_the_roots_are_printed(self) -> None:
        """Each file's root, then the belt's, joined for MYPYPATH."""
        done = roots("top.py", "a/helper.py")
        self.assertEqual(
            (0, f"a:{BELT}\n", ""), (done.returncode, done.stdout, done.stderr)
        )

    def test_two_files_that_are_one_module_are_named(self) -> None:
        """Two roots each holding helper.py: a clash, by module name."""
        done = roots("a/helper.py", "b/helper.py")
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            f"{TASK}: these tracked files would be one module, so an import"
            " of that name reaches only one of them:\n  helper: a/helper, b/helper\n",
            done.stderr,
        )

    def test_too_few_arguments_is_a_usage_error(self) -> None:
        """No belt or no file: exit 2 with the usage."""
        done = hermetic.call_main(lambda: python_roots.main([str(BELT)]))
        self.assertEqual(
            (2, f"{TASK}: usage: python_roots.py <belt-dir> <file>...\n"),
            (done.returncode, done.stderr),
        )

    def test_the_seams_script_contract(self) -> None:
        """python_types_seams started as a process: arguments in, exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("# type: ignore\n", encoding="utf-8")
            done = subprocess.run(
                [sys.executable, str(BELT / "python_types_seams.py"), "a.py"],
                cwd=tmp,
                env=hermetic.env(),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual((1, f"a.py:1: {IGNORED}\n"), (done.returncode, done.stdout))

    def test_the_roots_script_contract(self) -> None:
        """python_roots started as a process: no file is exit 2 and the usage."""
        done = subprocess.run(
            [sys.executable, str(BELT / "python_roots.py"), str(BELT)],
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            (2, "python_roots.py: usage: python_roots.py <belt-dir> <file>...\n"),
            (done.returncode, done.stderr),
        )


class TheConfig(unittest.TestCase):
    """What the delivered config makes the pinned mypy refuse."""

    @override
    def setUp(self) -> None:
        """Skip the row where the belt's pinned mypy is not installed."""
        if not MYPY:
            self.skipTest("the belt's pinned mypy is not installed here")

    def test_a_finding_is_red(self) -> None:
        """An unvalidated json value is refused where it is read."""
        text = "import json\n\n\ndef f(s: str) -> int:\n    return len(json.loads(s))\n"
        done = run({"a.py": text})
        self.assertEqual(1, done.returncode)
        self.assertIn('a.py:5: error: Expression has type "Any"  [misc]', done.stdout)

    def test_an_untyped_fixture_builder_is_red(self) -> None:
        """A test's data builder with no signature reds the gate (#1604).

        The shape #1604 ran down: a function that builds test data with
        no signature. It fails before the rename, with no such task.
        """
        text = "def build(name):\n    return {'name': name}\n"
        done = run({"test_a.py": text})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "test_a.py:1: error: Function is missing a type annotation"
            "  [no-untyped-def]",
            done.stdout,
        )
        self.assertIn(f"{LABEL}: mypy exited 1", done.stderr)

    def test_an_unused_suppression_is_red(self) -> None:
        """A coded ignore that suppresses nothing is mypy's unused-ignore."""
        done = run({"a.py": "X: int = 1  # type: ignore[misc]\n"})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            'a.py:1: error: Unused "type: ignore" comment  [unused-ignore]', done.stdout
        )

    def test_a_used_coded_suppression_is_red(self) -> None:
        """No escape is left: mypy is satisfied, and the seam guard reds it."""
        done = run({"a.py": 'X: int = "s"  # type: ignore[assignment]\n'})
        self.assertEqual(1, done.returncode)
        self.assertNotIn("a.py:1: error", done.stdout)
        self.assertIn(f"  a.py:1: {IGNORED}\n", done.stderr)

    def test_a_neighbour_and_a_sibling_root_are_imported_typed(self) -> None:
        """Both imports are read from the derived roots (#1634).

        `test_x.py` imports a helper beside it, and one in a sibling root
        named from its own `__file__`; each is typed, so a wrong use of
        either is a finding, not an untyped `Any`.
        """
        files = {
            "lib/helper.py": "NAME: str = 'x'\n",
            "tools/shared.py": "COUNT: int = 1\n",
            "lib/test_x.py": (
                "import sys\n"
                "from pathlib import Path\n\n"
                "import helper\n\n"
                "sys.path.append(str(Path(__file__).resolve().parents[1]"
                ' / "tools"))\n\n'
                "import shared\n\n"
                "A: int = helper.NAME\n"
                "B: str = shared.COUNT\n"
            ),
        }
        done = run(files)
        self.assertEqual(1, done.returncode)
        self.assertNotIn("import-not-found", done.stdout)
        wrong = "error: Incompatible types in assignment (expression has type"
        self.assertIn(
            f'lib/test_x.py:10: {wrong} "str", variable has type "int")',
            done.stdout,
        )
        self.assertIn(
            f'lib/test_x.py:11: {wrong} "int", variable has type "str")',
            done.stdout,
        )

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
from typed_hypothesis import given


class Holds(unittest.TestCase):
    \"\"\"Doubling.\"\"\"

    @given(st.integers(), st.data())
    def test_double(self, n: int, data: st.DataObject) -> None:
        \"\"\"Even.\"\"\"
        self.assertEqual((2 * n + 2 * data.draw(st.integers())) % 2, 0)
"""


class TheTypedGiven(unittest.TestCase):
    """The belt's `given` under the unmodified config (#1604)."""

    @override
    def setUp(self) -> None:
        """Skip the row where the pinned mypy or the belt's Hypothesis is absent."""
        if not MYPY:
            self.skipTest("the belt's pinned mypy is not installed here")
        if not HYPOTHESIS:
            self.skipTest("the belt's Hypothesis is not installed here")

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

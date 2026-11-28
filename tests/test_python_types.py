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
"""

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, python_roots, python_types_seams
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:python-types"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")
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
    found = support.optional_tool("mypy")
    if found is None:
        return None
    done = subprocess.run(
        [found, "--version"], capture_output=True, text=True, check=False
    )
    return found if done.stdout.split()[1:2] == [pinned()] else None


# Each looked up once, at import, as the skip decorators they replace did.
MYPY = real_mypy()
HYPOTHESIS = support.optional_tool("pytest")


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
    belt: support.Belt,
    env: support.Env,
) -> Run:
    """Run the task in a planted tree.

    `files` are written and tracked. The belt is the whole one, less
    the files `missing` names. With `stub`, a stub mypy leads PATH and
    `stub` is its environment; without, PATH is the suite's. With
    `stale_venv` (and `stub`), a `pytest` entry point with no
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
        belt_dir = belt(root / "belt")
        for name in missing:
            (belt_dir / name).unlink()
        environ = env(ORG_BELT_DIR=str(belt_dir), ORG_PYTHON=sys.executable)
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=environ, check=True)
        subprocess.run([GIT, "add", "-A"], cwd=tree, env=environ, check=True)
        log = root / "stub.log"
        if stub is not None:
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "mypy").write_text(STUB, encoding="utf-8")
            (bindir / "mypy").chmod(0o755)
            if stale_venv:
                (bindir / "pytest").write_text("#!/bin/sh\n", encoding="utf-8")
                (bindir / "pytest").chmod(0o755)
            environ["PATH"] = f"{bindir}{os.pathsep}{environ['PATH']}"
            environ |= {"STUB_LOG": str(log), "STUB_VERSION": pinned(), **stub}
        as_mise = support.task_run("lint:python-types", body())
        done = subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        args = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return Run(
            returncode=done.returncode,
            stdout=done.stdout.replace(str(belt_dir), "<belt>"),
            stderr=done.stderr.replace(str(belt_dir), "<belt>"),
            args=[a.replace(str(belt_dir), "<belt>") for a in args],
        )


CLEAN = {"a.py": "X: int = 1\n"}


# The task's own decisions, with a stub mypy.
def test_no_python_is_skipped_clean(belt: support.Belt, env: support.Env) -> None:
    """A tree with no .py or .pyi skips, and mypy is never asked."""
    done = run({"README.md": "x\n"}, stub={}, belt=belt, env=env)
    assert done.returncode == 0, done.stderr
    assert f"{LABEL}: no Python tracked, skipped\n" == done.stdout
    assert list[str]() == done.args


def test_a_pytest_venv_with_no_interpreter_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """The tool venv mypy would read has no python: refused (#1788)."""
    done = run(CLEAN, stub={}, stale_venv=True, belt=belt, env=env)
    assert done.returncode == 1
    assert re.search(
        rf"\A{LABEL}: pytest runs on 'no interpreter' but the suite's"
        r" python3 is '3\.[^']+' — the tool venv must be built on the"
        r" belt's python pin \(reinstall: mise install -f pipx:pytest\)\n\Z",
        done.stderr,
    )
    assert list[str]() == done.args


def test_the_delivered_config_missing_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """No mypy.ini in the belt: red by name, mypy never asked."""
    done = run(CLEAN, missing=("mypy.ini",), stub={}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        f"{LABEL}: <belt>/mypy.ini is missing — the belt did not arrive\n"
        in done.stderr
    )
    assert list[str]() == done.args


def test_a_mypy_that_is_not_the_pin_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """A stale install is named with the pin, before it checks anything."""
    done = run(CLEAN, stub={"STUB_VERSION": "0.0.1"}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        f"{LABEL}: mypy 0.0.1 is running but the belt pins '{pinned()}'\n"
        in done.stderr
    )
    assert list[str]() == done.args


def test_the_belt_config_is_the_one_passed(
    belt: support.Belt, env: support.Env
) -> None:
    """The stub gets --config-file <belt>/mypy.ini and every tracked file."""
    done = run(
        {**CLEAN, "mypy.ini": "[mypy]\nignore_errors = True\n"},
        stub={},
        belt=belt,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    config = ["--config-file", "<belt>/mypy.ini"]
    files = ["a.py"]
    assert config == done.args[:2]
    assert files == done.args[4:]
    assert f"{LABEL}: 1 file(s) clean under mypy {pinned()}\n" in done.stdout


def test_a_finding_is_red(belt: support.Belt, env: support.Env) -> None:
    """The task exits as mypy did, with its output kept."""
    out = 'a.py:1: error: Expression has type "Any"  [misc]'
    done = run(CLEAN, stub={"STUB_OUT": out, "STUB_RC": "1"}, belt=belt, env=env)
    assert done.returncode == 1
    assert out + "\n" in done.stdout
    assert f"{LABEL}: mypy exited 1;" in done.stderr


def test_a_config_mypy_could_not_load_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """A warning about the config is red, though mypy exits 0."""
    out = "@CFG@: [mypy]: Unrecognized option: stirct = True"
    done = run(CLEAN, stub={"STUB_OUT": out}, belt=belt, env=env)
    assert done.returncode == 1
    assert "<belt>/mypy.ini: [mypy]: Unrecognized option" in done.stdout
    assert f"{LABEL}: mypy did not load <belt>/mypy.ini cleanly (above)" in done.stderr


def test_a_bare_ignore_is_red(belt: support.Belt, env: support.Env) -> None:
    """`# type: ignore` with no code is named, even at the module's top."""
    done = run({"a.py": "# type: ignore\nX: int = 1\n"}, stub={}, belt=belt, env=env)
    assert done.returncode == 1
    assert f"  a.py:1: {IGNORED}\n" in done.stderr


def test_an_inline_config_is_red(belt: support.Belt, env: support.Env) -> None:
    """A `# mypy:` comment lowering its file's level is named."""
    done = run(
        {"a.py": "# mypy: ignore-errors\nX: int = 1\n"}, stub={}, belt=belt, env=env
    )
    assert done.returncode == 1
    assert (
        "  a.py:1: an inline `# mypy:` comment sets options for its file\n"
        in done.stderr
    )


def test_no_type_check_is_red(belt: support.Belt, env: support.Env) -> None:
    """`typing.no_type_check`, imported or by attribute, is named."""
    text = "import typing\n\n\n@typing.no_type_check\ndef f(x):\n    return x\n"
    done = run({"a.py": text}, stub={}, belt=belt, env=env)
    assert done.returncode == 1
    assert "  a.py:4: `no_type_check` skips type checking\n" in done.stderr


def test_markers_in_strings_are_not_seams(belt: support.Belt, env: support.Env) -> None:
    """The same text inside a string is not a finding."""
    text = 'S = "# type: ignore  # mypy: ignore-errors  no_type_check"\n'
    done = run({"a.py": text}, stub={}, belt=belt, env=env)
    assert done.returncode == 0, done.stderr


def test_two_files_that_are_one_module_are_red_by_name(
    belt: support.Belt, env: support.Env
) -> None:
    """Two roots each holding helper.py: named, and mypy never asked."""
    files = {"a/helper.py": "X = 1\n", "b/helper.py": "X = 2\n"}
    done = run(files, stub={}, belt=belt, env=env)
    assert done.returncode == 1
    assert "  helper: a/helper, b/helper\n" in done.stderr
    assert f"{LABEL}: rename one of each pair above" in done.stderr
    assert list[str]() == done.args


def test_a_coded_ignore_is_red(belt: support.Belt, env: support.Env) -> None:
    """`# type: ignore[<code>]` hides its finding too (owner, #1604)."""
    done = run(
        {"a.py": "f(1)  # type: ignore[arg-type]\n"}, stub={}, belt=belt, env=env
    )
    assert done.returncode == 1
    assert f"  a.py:1: {IGNORED}\n" in done.stderr


def seams(
    text: str,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    """Call python_types_seams in-process on one planted `a.py` (#1697).

    Returns:
        The status and both streams.

    """
    (tmp_path / "a.py").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    status = python_types_seams.main(["a.py"])
    out, err = capfd.readouterr()
    return status, out, err


def roots(
    *files: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    """Call python_roots in-process over planted files, as the script does.

    Returns:
        The status and both streams.

    """
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("X = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    status = python_roots.main([str(BELT), *files])
    out, err = capfd.readouterr()
    return status, out, err


# The seams and the roots, called in the test process (#1697).
def test_a_bare_ignore_is_a_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`# type: ignore` with no code is named, even at the module's top."""
    status, out, _err = seams(
        "# type: ignore\nX: int = 1\n",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert (1, f"a.py:1: {IGNORED}\n") == (status, out)


def test_an_inline_config_is_a_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A `# mypy:` comment lowering its file's level is named."""
    status, out, _err = seams(
        "# mypy: ignore-errors\nX: int = 1\n",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert (status, out) == (
        1,
        "a.py:1: an inline `# mypy:` comment sets options for its file\n",
    )


def test_no_type_check_is_a_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`typing.no_type_check`, by attribute, is named."""
    text = "import typing\n\n\n@typing.no_type_check\ndef f(x):\n    return x\n"
    status, out, _err = seams(
        text, tmp_path=tmp_path, monkeypatch=monkeypatch, capfd=capfd
    )
    assert (status, out) == (
        1,
        "a.py:4: `no_type_check` skips type checking\n",
    )


def test_the_helpers_markers_in_strings_are_not_seams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The same text inside a string is not a finding."""
    status, out, _err = seams(
        'S = "# type: ignore  # mypy: ignore-errors  no_type_check"\n',
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert (status, out) == (0, "")


def test_a_coded_ignore_is_a_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`# type: ignore[arg-type]` is named: it hides its finding."""
    status, out, _err = seams(
        "f(1)  # type: ignore[arg-type]\n",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert (1, f"a.py:1: {IGNORED}\n") == (status, out)


def test_the_roots_are_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Each file's root, then the belt's, joined for MYPYPATH."""
    status, out, err = roots(
        "top.py",
        "a/helper.py",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert (0, f"a:{BELT}\n", "") == (status, out, err)


def test_two_files_that_are_one_module_are_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Two roots each holding helper.py: a clash, by module name."""
    status, _out, err = roots(
        "a/helper.py",
        "b/helper.py",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capfd=capfd,
    )
    assert status == 1
    assert (
        f"{TASK}: these tracked files would be one module, so an import"
        " of that name reaches only one of them:\n  helper: a/helper, b/helper\n"
    ) == err


def test_too_few_arguments_is_a_usage_error(capfd: pytest.CaptureFixture[str]) -> None:
    """No belt or no file: exit 2 with the usage."""
    assert python_roots.main([str(BELT)]) == 2
    assert capfd.readouterr().err == (
        f"{TASK}: usage: belt python-roots <belt-root> <file>...\n"
    )


# What the delivered config makes the pinned mypy refuse.
@fixture
def _set_up_test_the_config() -> None:
    """Skip the row where the belt's pinned mypy is not installed."""
    if not MYPY:
        pytest.skip("the belt's pinned mypy is not installed here")


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_the_config_a_finding_is_red(belt: support.Belt, env: support.Env) -> None:
    """An unvalidated json value is refused where it is read."""
    text = "import json\n\n\ndef f(s: str) -> int:\n    return len(json.loads(s))\n"
    done = run({"a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert 'a.py:5: error: Expression has type "Any"  [misc]' in done.stdout


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_an_untyped_fixture_builder_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """A test's data builder with no signature reds the gate (#1604).

    The shape #1604 ran down: a function that builds test data with
    no signature. It fails before the rename, with no such task.
    """
    text = "def build(name):\n    return {'name': name}\n"
    done = run({"test_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        "test_a.py:1: error: Function is missing a type annotation  [no-untyped-def]"
    ) in done.stdout
    assert f"{LABEL}: mypy exited 1" in done.stderr


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_an_unused_suppression_is_red(belt: support.Belt, env: support.Env) -> None:
    """A coded ignore that suppresses nothing is mypy's unused-ignore."""
    done = run({"a.py": "X: int = 1  # type: ignore[misc]\n"}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        'a.py:1: error: Unused "type: ignore" comment  [unused-ignore]' in done.stdout
    )


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_a_used_coded_suppression_is_red(belt: support.Belt, env: support.Env) -> None:
    """No escape is left: mypy is satisfied, and the seam guard reds it."""
    done = run(
        {"a.py": 'X: int = "s"  # type: ignore[assignment]\n'}, belt=belt, env=env
    )
    assert done.returncode == 1
    assert "a.py:1: error" not in done.stdout
    assert f"  a.py:1: {IGNORED}\n" in done.stderr


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_a_neighbour_and_a_sibling_root_are_imported_typed(
    belt: support.Belt, env: support.Env
) -> None:
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
            "sys.path.append(str(support.CANON"
            ' / "tools"))\n\n'
            "import shared\n\n"
            "A: int = helper.NAME\n"
            "B: str = shared.COUNT\n"
        ),
    }
    done = run(files, belt=belt, env=env)
    assert done.returncode == 1
    assert "import-not-found" not in done.stdout
    wrong = "error: Incompatible types in assignment (expression has type"
    assert f'lib/test_x.py:10: {wrong} "str", variable has type "int")' in done.stdout
    assert f'lib/test_x.py:11: {wrong} "int", variable has type "str")' in done.stdout


@pytest.mark.usefixtures("_set_up_test_the_config")
def test_a_repository_config_does_not_outrank_the_belt(
    belt: support.Belt, env: support.Env
) -> None:
    """mypy.ini and pyproject.toml saying ignore_errors change nothing."""
    files = {
        "a.py": 'X: int = "s"\n',
        "mypy.ini": "[mypy]\nignore_errors = True\n",
        "pyproject.toml": "[tool.mypy]\nignore_errors = true\n",
    }
    done = run(files, belt=belt, env=env)
    assert done.returncode == 1
    assert "a.py:1: error: Incompatible types in assignment" in done.stdout


PROPERTY = """\
\"\"\"A property.\"\"\"

from hypothesis import strategies as st
from belt.typed_hypothesis import given


class Holds:
    \"\"\"Doubling.\"\"\"

    @given(st.integers(), st.data())
    def test_double(self, n: int, data: st.DataObject) -> None:
        \"\"\"Even.\"\"\"
        assert (2 * n + 2 * data.draw(st.integers())) % 2 == 0
"""


# The belt's `given` under the unmodified config (#1604).
@fixture
def _set_up_test_the_typed_given() -> None:
    """Skip the row where the pinned mypy or the belt's Hypothesis is absent."""
    if not MYPY:
        pytest.skip("the belt's pinned mypy is not installed here")
    if not HYPOTHESIS:
        pytest.skip("the belt's pytest venv is not installed here")


@pytest.mark.usefixtures("_set_up_test_the_typed_given")
def test_a_property_is_clean(belt: support.Belt, env: support.Env) -> None:
    """No finding on the decorator or the decorated test."""
    done = run({"prop_a.py": PROPERTY}, belt=belt, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{LABEL}: 1 file(s) clean under mypy" in done.stdout


@pytest.mark.usefixtures("_set_up_test_the_typed_given")
def test_the_body_is_still_checked(belt: support.Belt, env: support.Env) -> None:
    """An `int` parameter used as a `str` is red."""
    text = PROPERTY.replace("% 2 == 0\n", "% 2 == 0\n        n.upper()\n")
    assert text != PROPERTY
    done = run({"prop_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert 'error: "int" has no attribute "upper"  [attr-defined]' in done.stdout


@pytest.mark.usefixtures("_set_up_test_the_typed_given")
def test_a_strategy_that_does_not_match_its_parameter_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """An `int` strategy feeding a `str` parameter names the argument."""
    text = PROPERTY.replace("n: int,", "n: str,")
    done = run({"prop_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        'error: Argument 1 to "__call__" of "Given2" has incompatible type'
        ' "Callable[[Holds, str, DataObject], None]";'
        ' expected "Callable[[Holds, int, DataObject], None]"  [arg-type]'
    ) in done.stdout


@fixture
def _real_mypy() -> None:
    """Skip the row where the pinned mypy or the belt's pytest venv is absent."""
    if not MYPY:
        pytest.skip("the belt's pinned mypy is not installed here")
    if not HYPOTHESIS:
        pytest.skip("the belt's pytest venv is not installed here")


MODULE_PROPERTY = """\
\"\"\"A property on a module function.\"\"\"

from hypothesis import strategies as st
from belt.typed_hypothesis import given


@given(st.integers(), st.text())
def test_pair(n: int, s: str) -> None:
    \"\"\"Both kept.\"\"\"
    assert (n, s) == (n, s)
"""


@pytest.mark.usefixtures("_real_mypy")
def test_given_on_a_module_function_is_clean(
    belt: support.Belt, env: support.Env
) -> None:
    """`given` types a module function's strategies too, no `self` (#2177)."""
    done = run({"prop_a.py": MODULE_PROPERTY}, belt=belt, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{LABEL}: 1 file(s) clean under mypy" in done.stdout


@pytest.mark.usefixtures("_real_mypy")
def test_a_module_strategy_that_does_not_match_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """An `int` strategy feeding a module function's `str` is red."""
    text = MODULE_PROPERTY.replace("n: int,", "n: str,")
    assert text != MODULE_PROPERTY
    done = run({"prop_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert (
        'prop_a.py:7: error: Argument 1 to "__call__" of "Given2" has incompatible type'
    ) in done.stdout


FIXTURES = """\
\"\"\"Fixtures.\"\"\"

from collections.abc import Iterator

from belt.typed_pytest import fixture


@fixture
def word() -> str:
    \"\"\"A word.\"\"\"
    return "a"


@fixture
def words() -> Iterator[list[str]]:
    \"\"\"Words, torn down after.\"\"\"
    yield ["a"]


class TestShared:
    \"\"\"A class's rows.\"\"\"

    @fixture(scope="class")
    def shared(self) -> int:
        \"\"\"Shared by the class.\"\"\"
        return 1

    def test_word(self, word: str) -> None:
        \"\"\"Holds.\"\"\"
        assert word
"""


@pytest.mark.usefixtures("_real_mypy")
def test_the_typed_fixture_is_clean(belt: support.Belt, env: support.Env) -> None:
    """Bare, generator and class-scoped fixtures, no finding (#2177)."""
    done = run({"test_a.py": FIXTURES}, belt=belt, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{LABEL}: 1 file(s) clean under mypy" in done.stdout


@pytest.mark.usefixtures("_real_mypy")
def test_pytests_own_fixture_is_red(belt: support.Belt, env: support.Env) -> None:
    """`pytest.fixture` carries `Any` under the belt's config: the reason."""
    text = FIXTURES.replace(
        "from belt.typed_pytest import fixture", "from pytest import fixture"
    )
    done = run({"test_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert 'test_a.py:8: error: Expression type contains "Any"' in done.stdout


@pytest.mark.usefixtures("_real_mypy")
def test_a_fixture_called_directly_is_red(belt: support.Belt, env: support.Env) -> None:
    """A fixture called directly is red by type, as pytest fails it at run time."""
    text = FIXTURES + '\n\ndef test_call() -> None:\n    """Called."""\n    word()\n'
    done = run({"test_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert 'error: "object" not callable  [operator]' in done.stdout


@pytest.mark.usefixtures("_real_mypy")
def test_a_form_the_belt_does_not_type_is_red(
    belt: support.Belt, env: support.Env
) -> None:
    """A scope no overload names is no overload variant, not `Any`."""
    text = FIXTURES.replace('scope="class"', 'scope="session"')
    done = run({"test_a.py": text}, belt=belt, env=env)
    assert done.returncode == 1
    assert 'error: No overload variant of "fixture" matches' in done.stdout

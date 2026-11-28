"""`audit:mutants`' Python leg: mutmut on a fixture package (#2177).

The leg is called in process (`belt mutants-python`); it runs the real
mutmut, the belt's pin, on a planted repository: a package, its tests
and mutmut's own `[mutmut]` section. Each row asserts the leg's exact
words: a survivor red by its module and function, a mutant no test
reaches red by name, a timeout red, a pragma with no reason or a region
form red, and a `[mutmut]` key outside the allow-list red by key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from belt import mutants_python
from tests import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.hermetic import Called

TASK = "audit:mutants"
RED, USAGE = 1, 2
SETUP = "[mutmut]\nsource_paths = pkg/\npytest_add_cli_args_test_selection = tests/\n"
CALC = '''"""A fixture module."""


def add(a: int, b: int) -> int:
    """Add."""
    return a + b


def untested(a: int) -> int:
    """Nothing calls this in process."""
    return a * 2


def weak(a: int) -> bool:
    """Called, and its answer never checked."""
    return a > 0
'''
CALC_TESTS = '''from pkg import calc


def test_add() -> None:
    assert calc.add(2, 3) == 5


def test_weak() -> None:
    calc.weak(5)
'''
SPIN = '''"""A loop whose mutant never ends."""


def spin(n: int) -> int:
    """Count to n."""
    i = 0
    while i < n:
        i += 1
    return i
'''
SPIN_TESTS = '''from pkg import spin


def test_spin() -> None:
    assert spin.spin(3) == 3
'''


def plant(
    root: Path, module: str, source: str, tests: str, setup: str = SETUP
) -> Path:
    """Plant a repository: a package, its tests and its mutmut section.

    Returns:
        The repository's root.

    """
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text('"""A package."""\n', encoding="utf-8")
    (root / "pkg" / f"{module}.py").write_text(source, encoding="utf-8")
    (root / "tests" / f"test_{module}.py").write_text(tests, encoding="utf-8")
    if setup:
        (root / "setup.cfg").write_text(setup, encoding="utf-8")
    hermetic.track(root)
    return root


@pytest.fixture
def leg(
    call_main: Callable[..., Called], as_task: Callable[[str], None]
) -> Callable[..., Called]:
    """Call the leg in a planted repository, as audit:mutants runs it.

    Returns:
        The caller: the root, then the leg's arguments.

    """
    as_task(TASK)
    hermetic.tool("pytest")

    def call(root: Path, *argv: str) -> Called:
        return call_main(lambda: mutants_python.main(list(argv)), cwd=root)

    return call


def test_a_survivor_is_red_by_module_and_function_and_no_tests_by_name(
    tmp_path: Path, leg: Callable[..., Called]
) -> None:
    """The real mutmut: `weak`'s survivors and `untested`'s mutants, named."""
    done = leg(plant(tmp_path, "calc", CALC, CALC_TESTS))
    assert done.returncode == RED
    assert (
        "audit:mutants: pkg.calc.weak: 2 mutant(s) survived\n"
        "  pkg.calc.x_weak__mutmut_1\n"
        "  pkg.calc.x_weak__mutmut_2\n"
        "audit:mutants: 2 mutant(s) no tests: no test reaches them in process\n"
        "  pkg.calc.x_untested__mutmut_1\n"
        "  pkg.calc.x_untested__mutmut_2\n"
    ) in done.stderr
    assert done.stdout.endswith(
        "audit:mutants: python: 5 mutant(s), 1 killed, shard 1/1\n"
    )


def test_a_timeout_is_red(tmp_path: Path, leg: Callable[..., Called]) -> None:
    """`i += 1` made `i -= 1` or `i = 1` never ends: red, as a timeout, by name."""
    done = leg(plant(tmp_path, "spin", SPIN, SPIN_TESTS))
    assert done.returncode == RED
    assert (
        "audit:mutants: 2 mutant(s) timeout: their tests ran past the time limit\n"
        "  pkg.spin.x_spin__mutmut_4\n"
        "  pkg.spin.x_spin__mutmut_5\n"
    ) in done.stderr


@pytest.mark.parametrize(
    ("comment", "message"),
    [
        pytest.param(
            "# pragma: no mutate",
            "# pragma: no mutate with no reason — write it after ' -- '",
            id="no reason",
        ),
        pytest.param(
            "# pragma: no mutate block -- a reason",
            "# pragma: no mutate block — only the line form exists;"
            " mark each line, with its reason",
            id="block",
        ),
        pytest.param(
            "# pragma: no mutate start",
            "# pragma: no mutate start — only the line form exists;"
            " mark each line, with its reason",
            id="start",
        ),
        pytest.param(
            "# pragma: no mutate end",
            "# pragma: no mutate end — only the line form exists;"
            " mark each line, with its reason",
            id="end",
        ),
        pytest.param(
            "# pragma: no mutate: start, a context",
            "# pragma: no mutate start — only the line form exists;"
            " mark each line, with its reason",
            id="a context",
        ),
    ],
)
def test_a_pragma_that_is_not_the_reasoned_line_form_is_red(
    tmp_path: Path, leg: Callable[..., Called], comment: str, message: str
) -> None:
    """Before anything runs: the site, and why."""
    source = CALC.replace("    return a + b\n", f"    return a + b  {comment}\n")
    done = leg(plant(tmp_path, "calc", source, CALC_TESTS))
    assert done.returncode == RED
    assert done.stderr == (
        f"audit:mutants: pkg/calc.py:6: {message}\n"
        "  an exclusion records a mutant the suite cannot kill; say why at the site\n"
    )


def test_a_key_outside_the_allow_list_is_red_by_key(
    tmp_path: Path, leg: Callable[..., Called]
) -> None:
    """Policy in the repository's section: each key named, nothing run."""
    setup = SETUP + "timeout_multiplier = 1.0\ndo_not_mutate = pkg/*\n"
    done = leg(plant(tmp_path, "calc", CALC, CALC_TESTS, setup))
    assert done.returncode == RED
    assert done.stderr == (
        "audit:mutants: python: setup.cfg [mutmut] holds keys that are the belt's"
        " policy, not the repository's facts:\n"
        "  do_not_mutate\n"
        "  timeout_multiplier\n"
        "  a repository names only source_paths, pytest_add_cli_args_test_selection\n"
    )


def test_no_configuration_skips_clean_by_name(
    tmp_path: Path, leg: Callable[..., Called]
) -> None:
    """No [mutmut] tracked: 0, the skip named, and a skipped row for the totals."""
    summary = tmp_path / "summary"
    root = plant(tmp_path / "repo", "calc", CALC, CALC_TESTS, setup="")
    done = leg(root)
    assert done.returncode == 0
    assert done.stdout == (
        "audit:mutants: python: no mutmut configuration tracked"
        " (setup.cfg [mutmut]), skipped\n"
    )
    assert not summary.exists()


def test_both_configurations_are_red(tmp_path: Path, leg: Callable[..., Called]) -> None:
    """pyproject.toml [tool.mutmut] beside setup.cfg [mutmut]: mutmut reads one."""
    root = tmp_path
    (root / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["pkg/"]\n', encoding="utf-8"
    )
    plant(root, "calc", CALC, CALC_TESTS)
    done = leg(root)
    assert done.returncode == RED
    assert done.stderr == (
        "audit:mutants: python: both pyproject.toml [tool.mutmut] and setup.cfg"
        " [mutmut] are tracked; mutmut reads only the first, so keep one\n"
    )


@pytest.mark.parametrize(
    ("setup", "said"),
    [
        pytest.param(SETUP, "adopted=true\n", id="a mutmut section"),
        pytest.param("[metadata]\nname = x\n", "adopted=false\n", id="none"),
    ],
)
def test_adopted_says_whether_the_section_is_tracked(
    tmp_path: Path, leg: Callable[..., Called], setup: str, said: str
) -> None:
    """The adoption job's output: true only for a tracked [mutmut]."""
    done = leg(plant(tmp_path, "calc", CALC, CALC_TESTS, setup), "adopted")
    assert done.returncode == 0
    assert done.stdout == said


def test_an_unknown_argument_is_a_usage_error(
    tmp_path: Path, leg: Callable[..., Called]
) -> None:
    """Only `adopted` is a verb."""
    done = leg(tmp_path, "plan")
    assert done.returncode == USAGE
    assert done.stderr == "audit:mutants: usage: belt mutants-python [adopted]\n"


@pytest.mark.parametrize(
    ("name", "where"),
    [
        ("belt.decoded.x_load_json__mutmut_3", ("belt.decoded", "load_json")),
        ("belt.findings.xǁReportǁadd__mutmut_1", ("belt.findings", "Report.add")),
        ("not a mutant", ("not a mutant", "")),
    ],
)
def test_a_mutants_name_reads_as_its_module_and_function(
    name: str, where: tuple[str, str]
) -> None:
    """mutmut's mangled names, read back."""
    assert mutants_python.function_of(name) == where


def test_a_modules_pattern_reaches_none_of_a_submodules_mutants() -> None:
    """`belt.release.x*` is release's own; belt.release.matrix's are not in it."""
    assert mutants_python.patterns(["belt.release", "belt.release.matrix"]) == [
        "belt.release.x*",
        "belt.release.matrix.x*",
    ]

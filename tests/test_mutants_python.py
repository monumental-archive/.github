"""`audit:mutants`' Python leg: mutmut on a fixture package (#2177).

The leg is called in process (`belt mutants-python`); it runs the real
mutmut, the belt's pin, on a planted repository: a package, its tests
and mutmut's own `[mutmut]` section. Each row asserts the leg's exact
words: a survivor red by its module and function, a mutant no test
reaches red by name, a timeout red, a pragma with no reason or a region
form red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from belt import mutants_python
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
CALC_TESTS = """from pkg import calc


def test_add() -> None:
    assert calc.add(2, 3) == 5


def test_weak() -> None:
    calc.weak(5)
"""
SPIN = '''"""A loop whose mutant never ends."""


def spin(n: int) -> int:
    """Count to n."""
    i = 0
    while i < n:
        i += 1
    return i
'''
SPIN_TESTS = """from pkg import spin


def test_spin() -> None:
    assert spin.spin(3) == 3
"""


class Planting(TypedDict, total=False):
    """What a row plants beside its module, each with its default."""

    setup: str


def plant(
    root: Path,
    module: str,
    source: str,
    tests: str,
    *,
    track: support.Track,
    **options: Unpack[Planting],
) -> Path:
    """Plant a repository: a package, its tests and its mutmut section.

    Returns:
        The repository's root.

    """
    setup = options.get("setup", SETUP)
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "__init__.py").write_text('"""A package."""\n', encoding="utf-8")
    (root / "pkg" / f"{module}.py").write_text(source, encoding="utf-8")
    (root / "tests" / f"test_{module}.py").write_text(tests, encoding="utf-8")
    if setup:
        (root / "setup.cfg").write_text(setup, encoding="utf-8")
    track(root)
    return root


@fixture
def leg(
    as_task: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> Callable[[Path], None]:
    """Step into a planted repository, as audit:mutants runs the leg.

    Returns:
        A function that steps into the root.

    """
    as_task(TASK)
    support.tool("pytest")

    def enter(root: Path) -> None:
        monkeypatch.chdir(root)

    return enter


def test_a_survivor_is_red_by_module_and_function_and_no_tests_by_name(
    tmp_path: Path,
    leg: Callable[[Path], None],
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The real mutmut: `weak`'s survivors and `untested`'s mutants, named."""
    leg(plant(tmp_path, "calc", CALC, CALC_TESTS, track=track))
    status = mutants_python.main([])
    out, err = capfd.readouterr()
    assert status == RED
    assert (
        "audit:mutants: pkg.calc.weak: 2 mutant(s) survived\n"
        "  pkg.calc.x_weak__mutmut_1\n"
        "  pkg.calc.x_weak__mutmut_2\n"
        "audit:mutants: 2 mutant(s) no tests: no test reaches them in process\n"
        "  pkg.calc.x_untested__mutmut_1\n"
        "  pkg.calc.x_untested__mutmut_2\n"
    ) in err
    assert out.endswith("audit:mutants: python: 5 mutant(s), 1 killed, shard 1/1\n")


def test_a_timeout_is_red(
    tmp_path: Path,
    leg: Callable[[Path], None],
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`i += 1` made `i -= 1` or `i = 1` never ends: red, as a timeout, by name."""
    leg(plant(tmp_path, "spin", SPIN, SPIN_TESTS, track=track))
    status = mutants_python.main([])
    err = capfd.readouterr().err
    assert status == RED
    assert (
        "audit:mutants: 2 mutant(s) timeout: their tests ran past the time limit\n"
        "  pkg.spin.x_spin__mutmut_4\n"
        "  pkg.spin.x_spin__mutmut_5\n"
    ) in err


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(
            (
                "# pragma: no mutate",
                "# pragma: no mutate with no reason — write it after ' -- '",
            ),
            id="no reason",
        ),
        pytest.param(
            (
                "# pragma: no mutate block -- a reason",
                (
                    "# pragma: no mutate block — only the line form exists;"
                    " mark each line, with its reason"
                ),
            ),
            id="block",
        ),
        pytest.param(
            (
                "# pragma: no mutate start",
                (
                    "# pragma: no mutate start — only the line form exists;"
                    " mark each line, with its reason"
                ),
            ),
            id="start",
        ),
        pytest.param(
            (
                "# pragma: no mutate end",
                (
                    "# pragma: no mutate end — only the line form exists;"
                    " mark each line, with its reason"
                ),
            ),
            id="end",
        ),
        pytest.param(
            (
                "# pragma: no mutate: start, a context",
                (
                    "# pragma: no mutate start — only the line form exists;"
                    " mark each line, with its reason"
                ),
            ),
            id="a context",
        ),
    ],
)
def test_a_pragma_that_is_not_the_reasoned_line_form_is_red(
    row: tuple[str, str],
    tmp_path: Path,
    leg: Callable[[Path], None],
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Before anything runs: the site, and why."""
    comment, message = row
    source = CALC.replace("    return a + b\n", f"    return a + b  {comment}\n")
    leg(plant(tmp_path, "calc", source, CALC_TESTS, track=track))
    status = mutants_python.main([])
    err = capfd.readouterr().err
    assert status == RED
    assert err == (
        f"audit:mutants: pkg/calc.py:6: {message}\n"
        "  an exclusion records a mutant the suite cannot kill; say why at the site\n"
        "audit:mutants: python: a pragma above is refused; nothing was mutated\n"
    )


def test_no_configuration_skips_clean_by_name(
    tmp_path: Path,
    leg: Callable[[Path], None],
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No [mutmut] tracked: 0, the skip named, and a skipped row for the totals."""
    summary = tmp_path / "summary"
    root = plant(tmp_path / "repo", "calc", CALC, CALC_TESTS, setup="", track=track)
    leg(root)
    status = mutants_python.main([])
    out = capfd.readouterr().out
    assert status == 0
    assert out == (
        "audit:mutants: python: no mutmut configuration tracked"
        " (setup.cfg [mutmut]), skipped\n"
    )
    assert not summary.exists()


@pytest.mark.parametrize(
    "row",
    [
        pytest.param((SETUP, "adopted=true\n"), id="a mutmut section"),
        pytest.param(("[metadata]\nname = x\n", "adopted=false\n"), id="none"),
    ],
)
def test_adopted_says_whether_the_section_is_tracked(
    row: tuple[str, str],
    tmp_path: Path,
    leg: Callable[[Path], None],
    track: support.Track,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The adoption job's output: true only for a tracked [mutmut]."""
    setup, said = row
    leg(plant(tmp_path, "calc", CALC, CALC_TESTS, setup=setup, track=track))
    status = mutants_python.main(["adopted"])
    out = capfd.readouterr().out
    assert status == 0
    assert out == said


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["plan"],
            (
                "belt mutants-python: error: argument mode: invalid choice: 'plan' "
                "(choose from 'adopted')"
            ),
            id='["plan"]',
        ),
    ],
)
def test_an_unknown_argument_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Only `adopted` is a verb: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        mutants_python.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt mutants-python "), err
    assert err.splitlines()[-1] == error


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
    """Mutmut's mangled names, read back."""
    assert mutants_python.function_of(name) == where


def test_a_modules_pattern_reaches_none_of_a_submodules_mutants() -> None:
    """`belt.release.x*` is release's own; belt.release.matrix's are not in it."""
    assert mutants_python.patterns(["belt.release", "belt.release.matrix"]) == [
        "belt.release.x*",
        "belt.release.matrix.x*",
    ]

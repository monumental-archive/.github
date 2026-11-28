"""The hooks every test runs under, each run on a planted test (#2177).

Each row plants a test with pytester and runs it in this process, with
a planted conftest that takes the two hooks from tests/conftest.py,
so the row sees each do its work: the environment cleared, the working
directory held.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

# What a hook in a linked worktree inherits, and any other job variable.
PLANTED = ("GIT_DIR", "GIT_INDEX_FILE", "ORG_PLANTED_BY_THE_JOB")
CONFTEST = "from tests.conftest import pytest_runtest_setup, pytest_runtest_teardown\n"


def test_a_variable_the_job_sets_never_reaches_a_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job's variables outside INHERITED: the planted test sees none of them.

    `GIT_DIR` and `GIT_INDEX_FILE` are what git hands a hook standing in
    a linked worktree (#857): a test that saw them would point its own
    git at the caller's index. `ORG_PLANTED_BY_THE_JOB` stands for any
    other variable a job sets.
    """
    pytester.makepyfile(
        test_reads="""
        import os


        def test_reads():
            planted = {"GIT_DIR", "GIT_INDEX_FILE", "ORG_PLANTED_BY_THE_JOB"}
            assert not planted & set(os.environ)
        """
    )
    pytester.makeconftest(CONFTEST)
    for name in PLANTED:
        monkeypatch.setenv(name, str(pytester.path / "leaked"))
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)


def test_without_the_hooks_the_variable_would_reach_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the same test, with no conftest, sees the variable."""
    pytester.makepyfile(
        test_reads="""
        import os


        def test_reads():
            planted = {"GIT_DIR", "GIT_INDEX_FILE", "ORG_PLANTED_BY_THE_JOB"}
            assert not planted & set(os.environ)
        """
    )
    for name in PLANTED:
        monkeypatch.setenv(name, str(pytester.path / "leaked"))
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)


def test_a_test_that_leaves_the_directory_moved_fails_by_name(
    pytester: pytest.Pytester,
) -> None:
    """A planted test chdirs away: it errors at teardown, named."""
    pytester.makepyfile(
        test_moves="""
        import os
        import tempfile


        def test_moves():
            os.chdir(tempfile.gettempdir())
        """
    )
    pytester.makeconftest(CONFTEST)
    start = pytester.path
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines([
        "*test_moves.py::test_moves left the working directory at *"
        f" (the suite runs from {start}); take monkeypatch.chdir*",
    ])

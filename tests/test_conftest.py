"""The fixtures every test runs under, each run on a planted test (#2177).

Each row plants a test with pytester and runs it in this process, with
a planted conftest that takes the two fixtures from tests/conftest.py,
so the row sees each do its work: the environment cleared, the working
directory held.
"""

from __future__ import annotations

import pytest

CONFTEST = "from tests.conftest import _hermetic_environ, _directory_held\n"


def test_a_variable_the_job_sets_never_reaches_a_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job's variable outside INHERITED: the planted test does not see it."""
    pytester.makepyfile(
        test_reads="""
        import os


        def test_reads():
            assert "ORG_PLANTED_BY_THE_JOB" not in os.environ
        """
    )
    pytester.makeconftest(CONFTEST)
    monkeypatch.setenv("ORG_PLANTED_BY_THE_JOB", "leaked")
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(passed=1)


def test_without_the_fixture_the_variable_would_reach_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the same test, with no conftest, sees the variable."""
    pytester.makepyfile(
        test_reads="""
        import os


        def test_reads():
            assert "ORG_PLANTED_BY_THE_JOB" not in os.environ
        """
    )
    monkeypatch.setenv("ORG_PLANTED_BY_THE_JOB", "leaked")
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

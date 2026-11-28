"""The typed `fixture` is pytest's at run time (#2177).

Each row plants a test file that takes `fixture` from
belt/typed_pytest.py and runs it with pytester in this process. The
type each form gets is test_python_types.py's to hold; these rows hold
that the decorator still makes the fixture pytest would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_a_bare_fixture_is_set_up_for_each_row(pytester: pytest.Pytester) -> None:
    """`@fixture`: a function fixture, made again for every row."""
    pytester.makepyfile(
        test_bare="""
        from belt.typed_pytest import fixture

        MADE = []


        @fixture
        def word():
            MADE.append("a")
            return "a"


        def test_one(word):
            assert word == "a"


        def test_two(word):
            assert MADE == ["a", "a"]
        """
    )
    pytester.runpytest_inprocess("-p", "no:randomly").assert_outcomes(passed=2)


def test_a_class_fixture_is_set_up_once_for_its_class(
    pytester: pytest.Pytester,
) -> None:
    """`@fixture(scope="class")`: one value, shared by the class's rows."""
    pytester.makepyfile(
        test_shared="""
        from belt.typed_pytest import fixture

        MADE = []


        class TestShared:
            @fixture(scope="class")
            def shared(self):
                MADE.append(1)
                return 1

            def test_one(self, shared):
                assert shared == 1

            def test_two(self, shared):
                assert MADE == [1]
        """
    )
    pytester.runpytest_inprocess("-p", "no:randomly").assert_outcomes(passed=2)


def test_a_fixture_called_directly_fails(pytester: pytest.Pytester) -> None:
    """The definition pytest returns refuses a direct call, as its type does."""
    pytester.makepyfile(
        test_direct="""
        from belt.typed_pytest import fixture


        @fixture
        def word():
            return "a"


        def test_call():
            word()
        """
    )
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(['*Fixture "word" called directly*'])

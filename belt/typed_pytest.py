"""`fixture`, typed: what every test file imports instead of pytest's (#2177).

pytest types `fixture`'s `ids` as `Callable[[Any], object | None]`, so
under the belt's mypy (`disallow_any_expr`, mise/mypy.ini) that `Any`
reds the decorator on every fixture, whatever the fixture says. `Any`
is pytest's honest type for a callable over arbitrary parameter values,
and no flag comes off for it: as `given` is (#1604, belt/typed_hypothesis
.py), `fixture` is given an exact type here instead, one overload per
form the org's tests use:

- bare, `@fixture`, over a function or a method;
- `@fixture(scope="class")`, a fixture a test class's rows share;
- `@fixture(scope="module")`, one value for a module's rows, for a
  fixture as costly as a real run of the thing under test;
- `@fixture(scope="session")`, a factory with nothing to set up or tear
  down, which a Hypothesis property may take as well;
- `@fixture(params=[...])`, one fixture per value, every row that takes
  it run once for each. Its first parameter is `request`, typed
  `Requested[T]` for the values' own `T`: pytest's `FixtureRequest`
  declares no `param`, so the value it carries would be untyped, and
  the overload ties it to the list it came from.

Each overload takes the fixture function by its own parameters, so its
body is checked as written, and the decorated name is an `object`: pytest
replaces it with a definition that fails when called, so a row calling a
fixture directly is a type error here, as it is a failure at run time.
A form no overload names (`autouse=`, `params=` with a scope) is "no
overload variant", red until an overload is added here.

The tree the belt package sits in is on pytest's path for a run and on
mypy's (`mise/python-types.sh`), so a repository's test file needs one
line, `from belt.typed_pytest import fixture`.
"""

from typing import TYPE_CHECKING, Concatenate, Literal, Protocol, cast, overload

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class Requested[T](Protocol):
    """The `request` a parametrized fixture is given: its value, typed."""

    @property
    def param(self) -> T:
        """The value this instance of the fixture is for."""
        ...


@overload
def fixture[**P](function: Callable[P, object], /) -> object: ...


@overload
def fixture[**P](
    *, scope: Literal["class", "module", "session"]
) -> Callable[[Callable[P, object]], object]: ...


@overload
def fixture[T, **P](
    *, params: Sequence[T]
) -> Callable[[Callable[Concatenate[Requested[T], P], object]], object]: ...


def fixture[**P](
    function: Callable[P, object] | None = None,
    /,
    *,
    scope: Literal["function", "class", "module", "session"] = "function",
    params: Sequence[object] | None = None,
) -> object:
    """Apply pytest's `fixture`; the overloads above are its type.

    Returns:
        pytest's fixture definition, or its marker when a scope or
        params are named. Callers see the overloads only.

    """
    # THE ONE CAST, and the one place it can be: pytest's own type for
    # this value carries the `Any` of `ids`, which the overloads exist
    # to replace. `object` is what the implementation promises (every
    # overload's return is one); the overloads, checked against each
    # call, are the type callers get.
    if function is None:
        return cast("object", pytest.fixture(scope=scope, params=params))
    return cast("object", pytest.fixture(function))

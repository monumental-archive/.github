"""`given`, typed: what every property file imports instead of Hypothesis's (#1604).

Hypothesis types `given(...)` as returning `Callable[..., None]`. Under
the belt's mypy (`disallow_any_expr`, `disallow_any_decorated`, mise/
mypy.ini) that `Any` reds the decorator and the decorated test on every
property, whatever the property says. The org's properties take
positional strategies, on a module function or a test class's method
(#2177). For those two forms an exact type exists, written here as one
overload of `given` per arity, each returning a decorator whose own two
overloads are the two forms: each strategy's value type must match its
parameter (after `self`, on a method), and the decorated test takes
nothing, or `self` alone. A strategy that does not match its parameter
is a type error naming the argument, which Hypothesis's own typing
would let through. The arities are the ones the canon's property files
use (1 to 6); a seventh strategy is "no overload variant", red until an
overload is added here.

`belt python-properties` puts the canon's tree on pytest's path for
the run and `mise/python-types.sh` hands mypy the same, so a
repository's property file needs one line,
`from belt.typed_hypothesis import given`; `test:python-properties`
refuses `given` taken from `hypothesis`.
"""

from typing import TYPE_CHECKING, Protocol, cast, overload

import hypothesis

if TYPE_CHECKING:
    from collections.abc import Callable

    from hypothesis.strategies import SearchStrategy


class Given1[A](Protocol):
    """The decorator `given` returns for one strategy."""

    @overload
    def __call__[S](self, test: Callable[[S, A], None], /) -> Callable[[S], None]: ...

    @overload
    def __call__(self, test: Callable[[A], None], /) -> Callable[[], None]: ...


class Given2[A, B](Protocol):
    """The decorator `given` returns for two strategies."""

    @overload
    def __call__[S](
        self, test: Callable[[S, A, B], None], /
    ) -> Callable[[S], None]: ...

    @overload
    def __call__(self, test: Callable[[A, B], None], /) -> Callable[[], None]: ...


class Given3[A, B, C](Protocol):
    """The decorator `given` returns for three strategies."""

    @overload
    def __call__[S](
        self, test: Callable[[S, A, B, C], None], /
    ) -> Callable[[S], None]: ...

    @overload
    def __call__(self, test: Callable[[A, B, C], None], /) -> Callable[[], None]: ...


class Given4[A, B, C, D](Protocol):
    """The decorator `given` returns for four strategies."""

    @overload
    def __call__[S](
        self, test: Callable[[S, A, B, C, D], None], /
    ) -> Callable[[S], None]: ...

    @overload
    def __call__(self, test: Callable[[A, B, C, D], None], /) -> Callable[[], None]: ...


class Given5[A, B, C, D, E](Protocol):
    """The decorator `given` returns for five strategies."""

    @overload
    def __call__[S](
        self, test: Callable[[S, A, B, C, D, E], None], /
    ) -> Callable[[S], None]: ...

    @overload
    def __call__(
        self, test: Callable[[A, B, C, D, E], None], /
    ) -> Callable[[], None]: ...


class Given6[A, B, C, D, E, F](Protocol):
    """The decorator `given` returns for six strategies."""

    @overload
    def __call__[S](
        self, test: Callable[[S, A, B, C, D, E, F], None], /
    ) -> Callable[[S], None]: ...

    @overload
    def __call__(
        self, test: Callable[[A, B, C, D, E, F], None], /
    ) -> Callable[[], None]: ...


@overload
def given[A](a: SearchStrategy[A], /) -> Given1[A]: ...


@overload
def given[A, B](a: SearchStrategy[A], b: SearchStrategy[B], /) -> Given2[A, B]: ...


@overload
def given[A, B, C](
    a: SearchStrategy[A], b: SearchStrategy[B], c: SearchStrategy[C], /
) -> Given3[A, B, C]: ...


@overload
def given[A, B, C, D](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    /,
) -> Given4[A, B, C, D]: ...


@overload
def given[A, B, C, D, E](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    e: SearchStrategy[E],
    /,
) -> Given5[A, B, C, D, E]: ...


@overload
def given[A, B, C, D, E, F](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    e: SearchStrategy[E],
    f: SearchStrategy[F],
    /,
) -> Given6[A, B, C, D, E, F]: ...


def given(*strategies: SearchStrategy[object]) -> object:
    """Apply Hypothesis's `given`; the overloads above are its type.

    Returns:
        Hypothesis's decorator. Callers see the overloads only.

    """
    # THE ONE CAST, and the one place it can be: Hypothesis's own type
    # for this value is `Callable[..., None]`, which carries the `Any`
    # the overloads exist to replace. `object` is what the
    # implementation promises (every overload's return is one); the
    # overloads, checked against each call, are the type callers get.
    return cast("object", hypothesis.given(*strategies))

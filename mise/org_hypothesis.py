#!/usr/bin/env python3
"""`given`, typed: what every property file imports instead of Hypothesis's (#1604).

Hypothesis types `given(...)` as returning `Callable[..., None]`. Under
the belt's mypy (`disallow_any_expr`, `disallow_any_decorated`, mise/
mypy.ini) that `Any` reds the decorator and the decorated test on every
property, whatever the property says. The org's properties take one
form: positional strategies on a `unittest.TestCase` method. For that
form an exact type exists, written here as one overload per arity:
each strategy's value type must match its parameter after `self`, and
the decorated test takes `self` alone. A strategy that does not match
its parameter is a type error naming the argument, which Hypothesis's
own typing would let through. The arities are the ones the canon's 54
property files use (1 to 6); a seventh strategy is "no overload
variant", red until an overload is added here.

`mise/python-properties.py` puts this directory on the path for the
run and `mise/python-types.sh` hands mypy the same, so a repository's
property file needs one line, `from org_hypothesis import given`;
`test:python-properties` refuses `given` taken from `hypothesis`.
"""

from typing import TYPE_CHECKING, cast, overload

import hypothesis

if TYPE_CHECKING:
    from collections.abc import Callable

    from hypothesis.strategies import SearchStrategy


@overload
def given[S, A](
    a: SearchStrategy[A], /
) -> Callable[[Callable[[S, A], None]], Callable[[S], None]]: ...


@overload
def given[S, A, B](
    a: SearchStrategy[A], b: SearchStrategy[B], /
) -> Callable[[Callable[[S, A, B], None]], Callable[[S], None]]: ...


@overload
def given[S, A, B, C](
    a: SearchStrategy[A], b: SearchStrategy[B], c: SearchStrategy[C], /
) -> Callable[[Callable[[S, A, B, C], None]], Callable[[S], None]]: ...


@overload
def given[S, A, B, C, D](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    /,
) -> Callable[[Callable[[S, A, B, C, D], None]], Callable[[S], None]]: ...


@overload
def given[S, A, B, C, D, E](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    e: SearchStrategy[E],
    /,
) -> Callable[[Callable[[S, A, B, C, D, E], None]], Callable[[S], None]]: ...


@overload
def given[S, A, B, C, D, E, F](
    a: SearchStrategy[A],
    b: SearchStrategy[B],
    c: SearchStrategy[C],
    d: SearchStrategy[D],
    e: SearchStrategy[E],
    f: SearchStrategy[F],
    /,
) -> Callable[[Callable[[S, A, B, C, D, E, F], None]], Callable[[S], None]]: ...


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

"""The belt's two Hypothesis profiles and its collection refusals (#1332, #2177).

The pytest plugin `belt python-properties` runs every property file
under (`-p belt.property_profiles`): each process pytest starts, every
pytest-xdist worker included, imports it before any file, so the two
profiles are registered wherever a property runs. They live here and
nowhere else, so no repository sets its own bounds:

- `gate`: `derandomize=True`, so each test's examples are derived from
  the test itself and two runs execute the same examples; a bounded
  `max_examples`, no example database, no deadline.
- `audit`: a random seed (python_properties.py chooses and prints it);
  a larger `max_examples`; no example database, no deadline.

Neither profile has a deadline or the `too_slow` health check: both
measure wall time, and wall time on a shared runner is not a property of
the code.

At collection the plugin records, by name, a file that holds no
Hypothesis test, and a test that restates any of the profile's four
bounds with its own `@settings`, in REFUSED. The runner collects once
in its own process before anything runs and reads them there.

Only pytest imports this module: one imported before pytest starts
would be a module pytest's assertion rewriting has already missed,
which the delivered configuration's `filterwarnings = error` reds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import HealthCheck, settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

GATE_EXAMPLES = 200
AUDIT_EXAMPLES = 10_000
BOUNDS = ("max_examples", "derandomize", "database", "deadline")
# Typed here: register_profile takes its settings as `**kwargs: Any`.
SUPPRESSED: list[HealthCheck] = [HealthCheck.too_slow]

settings.register_profile(
    "gate",
    derandomize=True,
    max_examples=GATE_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=SUPPRESSED,
)
settings.register_profile(
    "audit",
    derandomize=False,
    max_examples=AUDIT_EXAMPLES,
    database=None,
    deadline=None,
    suppress_health_check=SUPPRESSED,
)


def is_property(func: object) -> bool:
    """Say whether a test function is a Hypothesis test.

    Returns:
        True for a function `@given` wrapped.

    """
    flag: object = getattr(func, "is_hypothesis_test", False)
    return bool(flag)


def bounds(chosen: settings) -> dict[str, object]:
    """Read the bounds the belt's profile owns from one settings object.

    Returns:
        Each bound in BOUNDS, by name.

    """
    return {
        "max_examples": chosen.max_examples,
        "derandomize": chosen.derandomize,
        "database": chosen.database,
        "deadline": chosen.deadline,
    }


def own_bounds(func: object, profile: settings) -> list[str]:
    """Name the profile bounds a test restates with its own `@settings`.

    Returns:
        `field=value` for each bound that differs from the profile.

    """
    mine: object = getattr(func, "_hypothesis_internal_use_settings", None)
    if not isinstance(mine, settings):
        return []
    have, want = bounds(mine), bounds(profile)
    return [
        f"{field}={have[field]!r}" for field in BOUNDS if have[field] != want[field]
    ]


def refusals(items: Sequence[tuple[str, str, object]], profile: settings) -> list[str]:
    """Judge what was collected: one line per refused file or test.

    `items` are (file, test id, the test's function) in collection order.

    Returns:
        A line per file with no Hypothesis test, then one per test that
        sets its own bounds.

    """
    out: list[str] = []
    seen: dict[str, bool] = {}
    for path, _, func in items:
        seen[path] = seen.get(path, False) or is_property(func)
    out += [
        f"{path}: holds no Hypothesis test (no @given)"
        for path, has in seen.items()
        if not has
    ]
    for _, nodeid, func in items:
        own = own_bounds(func, profile)
        if own:
            out.append(
                f"{nodeid} sets its own bounds ({', '.join(own)});"
                " the belt's profile owns them"
            )
    return out


# What the last collection in this process refused, read by `run`.
REFUSED: list[str] = []


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Record each property file with no property, and each test's own bounds."""
    del session
    name: object = config.getoption("hypothesis_profile", None)
    profile = settings.get_profile(name if isinstance(name, str) else "default")
    found: list[tuple[str, str, object]] = []
    for item in items:
        func: object = getattr(item, "obj", None)
        found.append((item.nodeid.partition("::")[0], item.nodeid, func))
    REFUSED[:] = refusals(found, profile)

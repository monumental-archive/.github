"""belt.python_properties and its plugin, as properties (#1696, #2177).

The example rows over the runner, its stand-in pytest and the real
plugin are `test_python_properties.py`'s; these are the properties.

- Refusal (R): any argument list that names no profile and a file is
  a usage error, exit 2, never a traceback.
- Invariance (V): for any n, the shards 1/n to n/n partition the files:
  every file in exactly one, whatever order the files were listed in
  (#1631); a named shard runs in one process.
- Invariance (V): the plugin refuses exactly the files that hold no
  Hypothesis test, and exactly the tests that restate a bound.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import settings
from hypothesis import strategies as st

from belt import findings, property_profiles, python_properties
from belt.typed_hypothesis import given
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

GATE = "test:python-properties"
AUDIT = "audit:python-properties"


# The three tests a property file can hold, as the plugin is handed them.
@given(st.integers())
def holds(n: int) -> None:
    """Hold for any integer, as a property file's Hypothesis test does."""
    assert 2 * n % 2 == 0


def plain() -> None:
    """Draw nothing: the test that is no Hypothesis test."""


@settings(max_examples=3)
@given(st.integers())
def bounded(n: int) -> None:
    """Restate one of the profile's bounds, as a file may not."""
    assert isinstance(n, int)


def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record what the runner's finding writer is handed, writing nothing.

    Returns:
        The messages, in order, as the run gives them.

    """
    lines: list[str] = []

    def record(subject: str, message: str, *detail: str) -> None:
        del subject, detail
        lines.append(message)

    monkeypatch.setattr(findings, "finding", record)
    return lines


def recorder(
    seen: list[tuple[list[Path], dict[str, str]]],
) -> Callable[[str, list[Path], dict[str, str]], int]:
    """Stand in for the run itself, recording what each call was handed.

    Returns:
        The stand-in: it appends its files and environment to `seen`.

    """

    def record(profile: str, files: list[Path], environ: dict[str, str]) -> int:
        del profile
        seen.append((files, environ))
        return 0

    return record


def nothing_run(profile: str, files: list[Path], environ: dict[str, str]) -> int:
    """Stand in for the run itself, running nothing.

    Returns:
        0, as a run that held.

    """
    del profile, files, environ
    return 0


def sharded(spec: str) -> bool:
    """Tell whether a value names a shard, or tries to.

    Returns:
        False for empty and 1/1, which leave the run unsharded.

    """
    return spec not in {"", "1/1"}


# The command line every run takes.
@given(st.integers(1, 16), st.integers(1, 6))
def test_every_run_takes_the_belt_config_and_its_plugin(count: int, files: int) -> None:
    """`-c` the delivered config, the plugin, the profile; workers capped."""
    names = [Path(f"prop_{i}.py") for i in range(files)]
    argv = python_properties.arguments("gate", names, None, count)
    assert argv[:4] == [
        "-c",
        str(python_properties.CONFIG),
        "--rootdir",
        str(Path.cwd()),
    ]
    assert argv[4:6] == ["-p", python_properties.PLUGIN]
    assert "--hypothesis-profile=gate" in argv
    assert argv[-files:] == [str(n) for n in names]
    if count > 1:
        assert argv[argv.index("-n") + 1] == str(min(count, files))
    else:
        assert "-n" not in argv


@given(st.text(max_size=4))
def test_workers_are_a_positive_number_or_the_cores(named: str) -> None:
    """ORG_TEST_WORKERS when positive digits, else the cores, at most eight."""
    got = python_properties.workers({python_properties.WORKERS: named})
    if named.isdigit() and int(named) > 0:
        assert got == int(named)
    else:
        assert got == min(os.cpu_count() or 1, python_properties.MAX_WORKERS)


def unlabelled(word: str) -> bool:
    """Tell whether a word is not a profile's name.

    Returns:
        True when it is none.

    """
    return word not in python_properties.LABELS


# Arguments and shards, the run itself a stand-in.
@given(
    st.one_of(
        st.lists(st.text(max_size=6).filter(support.not_help), max_size=1),
        st.lists(
            st.text(max_size=6).filter(unlabelled).filter(support.not_help),
            min_size=2,
            max_size=4,
        ),
    )
)
def test_a_usage_error_is_two(argv: list[str]) -> None:
    """R: argparse's exit 2 with its usage, and nothing run."""
    err = io.StringIO()
    with pytest.MonkeyPatch.context() as patched, contextlib.redirect_stderr(err):
        patched.setattr(python_properties, "run", nothing_run)
        with pytest.raises(SystemExit) as ended:
            python_properties.main(argv)
    assert ended.value.code == support.USAGE
    assert err.getvalue().startswith("usage: belt python-properties ")


@given(
    st.lists(
        st.from_regex(r"[a-z]{1,4}/prop_[a-z]{1,6}\.py", fullmatch=True),
        unique=True,
        min_size=1,
        max_size=12,
    ),
    st.integers(1, 6),
)
def test_the_shards_partition_the_files(files: list[str], n: int) -> None:
    """V: 1/n to n/n run every file once, listed in any order, one process each."""
    chosen: list[str] = []
    for k in range(1, n + 1):
        seen: list[tuple[list[Path], dict[str, str]]] = []
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(python_properties, "run", recorder(seen))
            patched.setenv("ORG_SHARD", f"{k}/{n}")
            patched.setenv("ORG_SHARD_REPORTS", "")
            patched.setenv("ORG_FINDINGS", "")
            status = python_properties.main(["gate", *reversed(files)])
        assert status == 0
        if seen:
            mine, environ = seen[0]
            chosen += [str(f) for f in mine]
            if n > 1:
                assert environ[python_properties.WORKERS] == "1"
    assert sorted(chosen) == sorted(files)


@given(
    st.text(
        st.characters(exclude_characters="\x00", exclude_categories=["Cs"]),
        max_size=6,
    ).filter(sharded)
)
def test_a_malformed_shard_is_refused_by_name(spec: str) -> None:
    """R: a value that is not k/n is exit 2, the value named; k/n is read."""
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(python_properties, "run", nothing_run)
        patched.setenv("ORG_SHARD", spec)
        patched.setenv("ORG_SHARD_REPORTS", "")
        patched.setenv("ORG_FINDINGS", "")
        lines = said(patched)
        status = python_properties.main(["gate", "prop_x.py"])
    if status == python_properties.EXIT_USAGE:
        assert lines == [f"ORG_SHARD={spec!r} is not a shard (k/n, 1 <= k <= n)"]
    else:
        assert (status, lines) == (0, [])


SHAPES = {"holds": holds, "plain": plain, "bounded": bounded}


# The plugin's judgment of what was collected.
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["a.py", "b.py", "c.py"]),
            st.sampled_from(sorted(SHAPES)),
        ),
        max_size=8,
    )
)
def test_exactly_the_files_and_tests_that_break_the_rules(
    collected: list[tuple[str, str]],
) -> None:
    """V: each file with no property, then each test restating a bound."""
    items = [
        (path, f"{path}::{shape}{i}", SHAPES[shape])
        for i, (path, shape) in enumerate(collected)
    ]
    # The profile the shapes were decorated under, as a property
    # file's tests are under the profile its run loaded first.
    got = property_profiles.refusals(items, settings())
    bare = []
    for path, _, _ in items:
        if path not in bare and not any(
            p == path and s != "plain" for p, s in collected
        ):
            bare.append(path)
    want = [f"{path}: holds no Hypothesis test (no @given)" for path in bare] + [
        f"{nodeid} sets its own bounds (max_examples=3); the belt's profile owns them"
        for nodeid, (_, shape) in zip((i[1] for i in items), collected, strict=True)
        if shape == "bounded"
    ]
    assert got == want


def test_the_profiles_hold_the_four_bounds() -> None:
    """Gate derandomised and bounded, audit larger; no database or deadline."""
    gate, audit = settings.get_profile("gate"), settings.get_profile("audit")
    assert property_profiles.bounds(gate) == {
        "max_examples": property_profiles.GATE_EXAMPLES,
        "derandomize": True,
        "database": None,
        "deadline": None,
    }
    assert property_profiles.bounds(audit) == {
        "max_examples": property_profiles.AUDIT_EXAMPLES,
        "derandomize": False,
        "database": None,
        "deadline": None,
    }

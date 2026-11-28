"""python_properties.py and its plugin, called in the process that runs them (#1696, #2177).

The plugin imports Hypothesis, which the ordinary suite's interpreter
does not carry, so these rows live here and run under
`test:python-properties`, on the belt's Hypothesis. The runner's pytest
is a stub that records what it was asked and answers as told: a real
run of planted files is `test_python_properties.py`'s, which starts the
task's own body.

- Refusal (R): any argument list that names no profile and a file is
  a usage error, exit 2, never a traceback.
- Refusal (R): a collection that refused a file or a test ends the run
  before anything runs, each refusal named; a collection pytest could
  not finish is red by its status.
- Invariance (V): for any n, the shards 1/n to n/n partition the files:
  every file in exactly one, whatever order the files were listed in
  (#1631); a named shard runs in one process.
- Invariance (V): the plugin refuses exactly the files that hold no
  Hypothesis test, and exactly the tests that restate a bound.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol

import pytest
from hypothesis import settings
from hypothesis import strategies as st

from belt import findings, property_profiles, python_properties
from belt.typed_hypothesis import given
from belt.typed_pytest import fixture

GATE = "test:python-properties"
AUDIT = "audit:python-properties"
USAGE = "usage: belt python-properties gate|audit FILE..."


class Shapes:
    """The three tests a property file can hold, as the plugin is handed them."""

    @given(st.integers())
    def holds(self, n: int) -> None:
        """A Hypothesis test, as a property file holds one."""
        assert 2 * n % 2 == 0

    def plain(self) -> None:
        """A test that is no Hypothesis test."""

    @settings(max_examples=3)
    @given(st.integers())
    def bounded(self, n: int) -> None:
        """A Hypothesis test restating one of the profile's bounds."""
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


class Pytest:
    """A stand-in for `pytest.main`: each call recorded, each answer told."""

    def __init__(self, *answers: tuple[int, list[str]]) -> None:
        """Answer the calls in turn: a status, and what the plugin refused."""
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        """Record one call and give its answer.

        Returns:
            The status told for this call.

        """
        self.calls.append(argv)
        status, refused = self.answers.pop(0)
        property_profiles.REFUSED[:] = refused
        return status


class Ran(Protocol):
    """The `ran` fixture's caller."""

    def __call__(
        self, profile: str, files: list[str], stub: Pytest, **environ: str
    ) -> tuple[int, str, str]:
        """Run `files` under `profile` against `stub`, with `environ`."""
        ...


@fixture
def ran(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> Ran:
    """Call `run` with a stand-in pytest, restoring what a run sets.

    Returns:
        The caller: `ran(profile, files, stub, **environ)`, giving the
        status and what was written to each stream.

    """
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setitem(sys.modules, python_properties.PLUGIN, property_profiles)
    monkeypatch.setattr(property_profiles, "REFUSED", [])
    for name in (python_properties.STORAGE, "PYTHONDONTWRITEBYTECODE"):
        monkeypatch.delenv(name, raising=False)

    def call(
        profile: str, files: list[str], stub: Pytest, **environ: str
    ) -> tuple[int, str, str]:
        monkeypatch.setenv("MISE_TASK_NAME", python_properties.LABELS[profile])
        status = python_properties.run(
            profile, [Path(f) for f in files], environ, pytest_main=stub
        )
        out, err = capfd.readouterr()
        return status, out, err

    return call


# The runner over a stand-in pytest: collect, then run.
def test_a_held_run_collects_then_runs(ran: Ran) -> None:
    """Collected with no refusal, run, held: exit 0 and the count."""
    stub = Pytest((0, []), (0, []))
    status, out, err = ran("gate", ["a/prop_x.py"], stub, ORG_TEST_WORKERS="1")
    assert (status, out, err) == (0, f"{GATE}: 1 file(s) held\n", "")
    collect, run = stub.calls
    assert collect[-3:] == ["a/prop_x.py", "--collect-only", "-q"]
    assert run[-1] == "a/prop_x.py"
    assert "--collect-only" not in run
    assert os.environ[python_properties.STORAGE]
    assert sys.dont_write_bytecode
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"


def test_each_refusal_is_named_and_nothing_runs(ran: Ran) -> None:
    """The plugin refused two: both named, exit 1, no run after."""
    refused = ["prop_x.py: holds no Hypothesis test (no @given)", "prop_y.py::t"]
    stub = Pytest((0, refused))
    status, out, err = ran("gate", ["prop_x.py", "prop_y.py"], stub)
    assert (status, out) == (1, "")
    assert err == "".join(f"{GATE}: {line}\n" for line in refused)
    assert len(stub.calls) == 1


def test_a_collection_that_failed_is_red_by_its_status(ran: Ran) -> None:
    """pytest could not collect (an import error): exit 1, its status named."""
    stub = Pytest((2, []))
    status, out, err = ran("gate", ["prop_x.py"], stub)
    assert (status, out) == (1, "")
    assert err == f"{GATE}: pytest could not collect the files (exit 2)\n"


def test_a_failed_gate_run_is_red(ran: Ran) -> None:
    """A property failed: exit 1, pytest's status named."""
    status, out, err = ran("gate", ["prop_x.py"], Pytest((0, []), (1, [])))
    assert (status, out) == (1, "")
    assert err == f"{GATE}: pytest exited 1; failures above\n"


def test_the_audit_prints_its_seed_and_honours_a_given_one(ran: Ran) -> None:
    """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
    stub = Pytest((0, []), (1, []))
    status, out, err = ran(
        "audit", ["prop_x.py"], stub, ORG_PROPERTY_SEED="7", ORG_TEST_WORKERS="1"
    )
    assert status == 1
    assert out == f"{AUDIT}: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
    assert err == (
        f"{AUDIT}: seed 7 — commit each falsifying example above as an"
        " @example row with its fix\n"
    )
    assert "--hypothesis-seed=7" not in stub.calls[0]
    assert ["--hypothesis-seed=7", "--durations=10"] == [
        word for word in stub.calls[1] if word.startswith("--d") or "seed" in word
    ]


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
        st.lists(st.text(max_size=6), max_size=1),
        st.lists(st.text(max_size=6).filter(unlabelled), min_size=2, max_size=4),
    )
)
def test_a_usage_error_is_two(argv: list[str]) -> None:
    """R: exit 2 with the usage line, and nothing run."""
    with pytest.MonkeyPatch.context() as patched:
        lines = said(patched)
        patched.setattr(python_properties, "run", nothing_run)
        status = python_properties.main(argv)
    assert (status, lines) == (2, [USAGE])


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

        def record(profile: str, files: list[Path], environ: dict[str, str]) -> int:
            del profile
            seen.append((files, environ))
            return 0

        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(python_properties, "run", record)
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
    if status == 2:
        assert lines == [f"ORG_SHARD={spec!r} is not a shard (k/n, 1 <= k <= n)"]
    else:
        assert (status, lines) == (0, [])


SHAPES = {"holds": Shapes.holds, "plain": Shapes.plain, "bounded": Shapes.bounded}


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

"""Every shard's share is its task's whole population, each item once (#2160).

mise/shard.py names a shard (ORG_SHARD=k/n), writes what each shard
ran, and holds the union of every shard's report to the population it
chose from. These rows plant the union's two failures (a shard that
drops an item, one that repeats one) and each of its other refusals, by
message, then hand a pytest run over a fixture tree its share at 1/2 and 2/2
(shard_runner.py, in process), run each share for real, and hold what
the two shards reported to the whole suite.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from belt import decoded, shard, shard_runner
from belt.typed_pytest import fixture
from tests import support


def doc(
    k: int, n: int, ran: list[str], population: list[str] | None = None
) -> dict[str, object]:
    """Make one shard's report of task `t`, runner `r`.

    Returns:
        The report, as shard.report writes it.

    """
    return {
        "task": "t",
        "runner": "r",
        "scope": "",
        "shard": k,
        "of": n,
        "population": population or ["a", "b", "c", "d"],
        "ran": ran,
    }


def lines(*text: str) -> list[str]:
    """Spell the defects a row expects.

    Returns:
        The lines, as the union returns them.

    """
    return list(text)


# ORG_SHARD is k/n with 1 <= k <= n, or nothing.
def test_good() -> None:
    """1/2, 2/2 and 4/4 name shards; empty names none."""
    assert shard.parse("1/2") == (1, 2)
    assert shard.parse(" 2/2 ") == (2, 2)
    assert shard.parse("4/4") == (4, 4)
    assert shard.parse("") is None


@pytest.mark.parametrize("raw", ["0/2", "3/2", "1/0", "x/2", "1", "1/2/3", "-1/2"])
def test_bad(raw: object) -> None:
    """Zero, k above n, and anything not two numbers: refused by name."""
    with pytest.raises(shard.ShardError) as caught:
        shard.parse(raw)
    assert f"ORG_SHARD={raw!r} is not a shard (k/n, 1 <= k <= n)" == str(caught.value)


# The union check, planted: each defect red by name.
def test_whole() -> None:
    """Two shards that split the population exactly: green."""
    none: list[str] = []
    got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b", "d"])], {"t": 2})
    assert none == got


def test_a_shard_drops_an_item() -> None:
    """`d` ran nowhere: red, naming it."""
    got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b"])], {"t": 2})
    assert lines("t: r: ran on no shard: d") == got


def test_a_shard_repeats_an_item() -> None:
    """`c` ran on both: red, naming it."""
    got = shard.union([doc(1, 2, ["a", "c"]), doc(2, 2, ["b", "c", "d"])], {"t": 2})
    assert lines("t: r: ran on more than one shard: c") == got


def test_a_shard_reported_nothing() -> None:
    """Shard 2 of 2 is missing: red, naming it, and what it would have run."""
    got = shard.union([doc(1, 2, ["a", "c"])], {"t": 2})
    want = lines(
        "t: r: shard(s) 2 of 2 reported nothing", "t: r: ran on no shard: b, d"
    )
    assert want == got


def test_shards_disagree_on_the_population() -> None:
    """Two shards that chose from different lists: red."""
    got = shard.union(
        [doc(1, 2, ["a"], ["a", "b"]), doc(2, 2, ["b"], ["a", "b", "c"])], {"t": 2}
    )
    assert lines("t: r: the shards chose from 2 different populations") == got


def test_an_item_outside_the_population() -> None:
    """A shard ran what it did not choose from: red."""
    got = shard.union([doc(1, 2, ["a", "c", "z"]), doc(2, 2, ["b", "d"])], {"t": 2})
    assert lines("t: r: ran but is not in the population: z") == got


def test_a_sharded_task_with_no_runner_that_takes_a_share() -> None:
    """A task planned on 3 shards that nothing reported for: red."""
    got = shard.union([], {"t": 3, "ci:lint": 1})
    want = lines(
        "t: planned on 3 shards, and no shard reported what it ran:"
        " its command runs no runner that takes a share"
    )
    assert want == got


def test_a_task_the_plan_did_not_shard() -> None:
    """A report from a task the plan gave one runner: red."""
    got = shard.union([doc(1, 2, ["a", "b", "c", "d"])], {})
    assert lines("t: r: reported by a task the plan did not shard") == got


def test_the_command_line() -> None:
    """`union` reads the directory; a malformed count is the usage exit."""
    with tempfile.TemporaryDirectory() as scratch:
        assert shard.main(["union", scratch, "t=1"]) == 0
        assert shard.main(["union", scratch, "t=2"]) == 1
        assert shard.main(["union", scratch, "t=0"]) == 2
        assert shard.main(["union"]) == 2


SUITE = {f"test_{name}.py": "def test_it():\n    pass\n" for name in "abcde"}
PYTEST = support.tool("pytest")
# The suite's pytest run, as the task hands it to the shard runner.
PYTEST_SHARE = ["--", PYTEST, "-q", "-p", "no:cacheprovider", "suite"]


class Guarded:
    """A pytest run takes a share, run for real at 1/2 and 2/2."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant five test files and a reports directory."""
        self.make_env = env
        self.root = Path(str(tmp_path))
        (self.root / "suite").mkdir()
        for name, body in SUITE.items():
            (self.root / "suite" / name).write_text(body, encoding="utf-8")
        self.reports = self.root / "reports"

    def enter(self, monkeypatch: pytest.MonkeyPatch, spec: str) -> None:
        """Hand the suite's pytest run the shard `spec`, in the planted tree."""
        for name, value in {
            "ORG_SHARD": spec,
            "ORG_SHARD_TASK": "test:py",
            "ORG_SHARD_REPORTS": str(self.reports),
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.root)

    def share(self, out: str) -> subprocess.CompletedProcess[str]:
        """Run the share the runner named, as the task's shell does.

        Returns:
            The share's run.

        """
        return subprocess.run(
            [word for word in out.split("\0") if word],
            cwd=self.root,
            env=self.make_env(),
            capture_output=True,
            text=True,
            check=False,
        )


@fixture
def guarded(tmp_path: Path, env: support.Env) -> Guarded:
    """Give each row its own Guarded."""
    return Guarded(tmp_path, env=env)


def test_two_shards_run_the_whole_suite_once(
    guarded: Guarded, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """1/2 and 2/2 between them run every file, none twice.

    The run's own `-q` makes pytest's listing `-qq`, a count per
    file: before the share read that form, every shard held nothing
    and ran `true`.
    """
    for spec in ("1/2", "2/2"):
        guarded.enter(monkeypatch, spec)
        status = shard_runner.main(PYTEST_SHARE)
        out, err = capfd.readouterr()
        assert status == 0, err
        done = guarded.share(out)
        assert done.returncode == 0, done.stderr
    reports = shard.load(guarded.reports)
    assert len(reports) == 2
    ran = sorted(
        decoded.as_str(r, "ran")
        for d in reports
        for r in decoded.as_array(d["ran"], "ran")
    )
    want = [f"suite/test_{n}.py" for n in "abcde"]
    assert want == ran
    none: list[str] = []
    assert none == shard.union(reports, {"test:py": 2})


def test_one_shard_alone_is_red(
    guarded: Guarded, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Only 1/2 ran: the union names shard 2 and every file it held."""
    guarded.enter(monkeypatch, "1/2")
    status = shard_runner.main(PYTEST_SHARE)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert guarded.share(out).returncode == 0
    got = shard.union(shard.load(guarded.reports), {"test:py": 2})
    assert len(got) == 2, got
    assert "shard(s) 2 of 2 reported nothing" in got[0]
    assert "ran on no shard: " in got[1]


def test_a_malformed_shard_is_refused(
    guarded: Guarded, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """ORG_SHARD=0/2: exit 1, the value named, nothing run."""
    guarded.enter(monkeypatch, "0/2")
    with pytest.raises(SystemExit) as ended:
        shard_runner.main(PYTEST_SHARE)
    err = capfd.readouterr().err
    assert ended.value.code == 1
    assert "ORG_SHARD='0/2' is not a shard (k/n, 1 <= k <= n)" in err

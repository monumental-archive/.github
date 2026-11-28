"""Each runner `measured` shards is handed its share in its own terms (#2160).

belt/shard_runner.py turns ORG_SHARD=k/n into vitest's `--shard`,
nextest's `--partition slice:`, a share of pytest's test files (#2177)
and a share of `go test`'s packages. These rows call the adapter in
process against stand-ins on PATH that keep each runner's measured
split contract (the owner's ruling on #2160, the measurements recorded
there):

    vitest   k from 1; files sorted by sha1 of "/<root-relative path>",
             cut in contiguous slices, the first N mod n one longer;
             k < 1, k > n and n above the file count exit 1.
    nextest  `slice:k/n`, k from 1: every matching test, by binary then
             name, dealt round-robin.
    pytest   no shard of its own; `--collect-only -q` prints one
             `file::test` line per test, and a run runs the files it
             is given.
    go       no shard of its own; `go list` prints import paths sorted,
             and `go test` runs the packages it is given.

Each stand-in's `run` runs its own share by that contract and records
it, so a row holds the adapter's report to what the runner ran, and the
shards' union to the whole suite with nothing twice. The real runners
are proven in the first consumer's own gate (monumental-archive#523).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, shard, shard_runner
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# The stand-ins, one Python script per runner. Each reads its fixture
# from the environment and writes what its `run` ran to STUB_RAN.
VITEST = r"""
import hashlib, os, sys
files = os.environ["STUB_FILES"].split()
args = sys.argv[1:]
spec = next((a.split("=", 1)[1] for a in args if a.startswith("--shard=")), None)
def share():
    if spec is None:
        return files
    k, n = (int(x) for x in spec.split("/"))
    if k < 1 or k > n:
        sys.exit("Error: --shard <index> must be a positive number less then <count>")
    if n > len(files):
        sys.exit("Error: --shard <count> must be a smaller than count of test files.")
    order = sorted(files, key=lambda f: hashlib.sha1(("/" + f).encode()).hexdigest())
    base, extra = divmod(len(order), n)
    if extra >= k:
        start, end = (base + 1) * (k - 1), (base + 1) * k
    else:
        start = extra * (base + 1) + (k - extra - 1) * base
        end = start + base
    return order[start:end]
if args[0] == "list":
    print("\n".join(sorted(share())))
elif args[0] == "run":
    with open(os.environ["STUB_RAN"], "a") as out:
        out.write("".join(f"{f}\n" for f in share()))
"""

NEXTEST = r"""
import json, os, sys
tests = [line.split(" ", 1) for line in os.environ["STUB_TESTS"].split("\n") if line]
args = sys.argv[1:]
assert args[0] == "nextest", args
order = sorted((b, t) for b, t in tests)
if args[1] == "list":
    suites = {}
    for b, t in tests:
        cases = suites.setdefault(b, {"testcases": {}})["testcases"]
        cases[t] = {"filter-match": {"status": "matches"}}
    print(json.dumps({"rust-suites": suites}))
elif args[1] == "run":
    at = args.index("--partition")
    kind, spec = args[at + 1].split(":")
    assert kind == "slice", kind
    k, n = (int(x) for x in spec.split("/"))
    with open(os.environ["STUB_RAN"], "a") as out:
        out.write("".join(f"{b} {t}\n" for b, t in order[k - 1 :: n]))
"""

PYTEST = r"""
import os, sys
files = os.environ["STUB_FILES"].split()
args = sys.argv[1:]
given = [a for a in args if not a.startswith("-") and (a in files or a == "tests")]
if "--collect-only" in args:
    chosen = [f for f in files if any(f == g or f.startswith(g + "/") for g in given)]
    print("".join(f"{f}::test_one\n{f}::test_two\n" for f in chosen), end="")
    print(f"\n{2 * len(chosen)} tests collected in 0.01s")
else:
    with open(os.environ["STUB_RAN"], "a") as out:
        out.write("".join(f"{f}\n" for f in given))
        out.write("argv " + " ".join(args) + "\n")
"""

# `python3 -m pytest`, handed to the pytest stand-in beside it.
PYTHON3 = r"""
import os, sys
assert sys.argv[1:3] == ["-m", "pytest"], sys.argv
here = os.path.dirname(os.path.abspath(__file__))
os.execv(os.path.join(here, "pytest"), ["pytest", *sys.argv[3:]])
"""

GO = r"""
import os, sys
packages = os.environ["STUB_PACKAGES"].split()
args = sys.argv[1:]
if args[0] == "list":
    print("\n".join(sorted(packages)))
elif args[0] == "test":
    ran = [a for a in args[1:] if a in packages]
    with open(os.environ["STUB_RAN"], "a") as out:
        out.write("".join(f"{p}\n" for p in ran))
        out.write("argv " + " ".join(args) + "\n")
"""


TRUE: list[str] = ["true"]
USAGE = 2


def words(out: str) -> list[str]:
    """Read the adapter's command, NUL-separated on its stdout.

    Returns:
        Its words.

    """
    return [w for w in out.split("\0") if w]


def argv_of(*text: str) -> list[str]:
    """Spell a command a row expects.

    Returns:
        Its words.

    """
    return list(text)


def vitest_key(path: str) -> str:
    """Sort a file as vitest's sequencer does: sha1 of its root-relative path.

    Returns:
        The hex digest.

    """
    return hashlib.sha1(f"/{path}".encode(), usedforsecurity=False).hexdigest()


class Runner:
    """A stand-in on PATH, the adapter run against it shard by shard."""

    def __init__(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str], *, env: support.Env
    ) -> None:
        """Make the stand-ins' directory and the reports directory."""
        self.make_env = env
        self.capfd = capfd
        self.base = tmp_path
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.reports = self.base / "reports"
        self.ran = self.base / "ran"
        stubs = (
            ("vitest", VITEST),
            ("cargo", NEXTEST),
            ("pytest", PYTEST),
            ("python3", PYTHON3),
            ("go", GO),
        )
        for name, body in stubs:
            stub = self.bin / name
            stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            stub.chmod(0o755)

    def adapt(
        self, spec: str, command: list[str], fixture: dict[str, str]
    ) -> tuple[int, str, str]:
        """Call the adapter for one shard of a command.

        Returns:
            Its status and both streams: stdout is the command,
            NUL-separated.

        """
        env = self.make_env(
            PATH=f"{self.bin}:/usr/bin:/bin",
            ORG_SHARD=spec,
            ORG_SHARD_TASK="test:suite",
            ORG_SHARD_REPORTS=str(self.reports),
            STUB_RAN=str(self.ran),
            **fixture,
        )
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.chdir(self.base)
            status = shard_runner.main(["--", *command])
        out, err = self.capfd.readouterr()
        return status, out, err

    def shards(
        self, n: int, command: list[str], fixture: dict[str, str]
    ) -> list[list[str]]:
        """Adapt and run every shard of a command, as the gate's rows do.

        Returns:
            What the runner ran on each shard, shard 1 first.

        """
        shares: list[list[str]] = []
        for k in range(1, n + 1):
            status, out, err = self.adapt(f"{k}/{n}", command, fixture)
            assert status == 0, err
            argv = words(out)
            self.ran.write_text("", encoding="utf-8")
            if argv != ["true"]:
                ran = subprocess.run(
                    argv,
                    cwd=self.base,
                    env=self.make_env(
                        PATH=f"{self.bin}:/usr/bin:/bin",
                        STUB_RAN=str(self.ran),
                        **fixture,
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert ran.returncode == 0, ran.stderr
            lines = self.ran.read_text("utf-8").splitlines()
            shares.append([line for line in lines if not line.startswith("argv ")])
        return shares

    def whole(self, n: int, shares: list[list[str]], population: list[str]) -> None:
        """Hold the shards to the population and the reports to the shares."""
        ran = sorted(item for share in shares for item in share)
        want: list[str] = sorted(population)
        assert want == ran
        none: list[str] = []
        assert none == shard.union(shard.load(self.reports), {"test:suite": n})
        reported: dict[int, list[str]] = {}
        for doc in shard.load(self.reports):
            k = decoded.as_int(doc.get("shard"), "shard")
            items = decoded.as_array(doc.get("ran"), "ran")
            reported[k] = sorted(decoded.as_str(i, "ran") for i in items)
        mine = {k: sorted(share) for k, share in enumerate(shares, 1) if share}
        kept: dict[int, list[str]] = {k: v for k, v in reported.items() if v}
        assert mine == kept


@fixture
def runner(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], env: support.Env
) -> Runner:
    """Give each row its own Runner."""
    return Runner(tmp_path, capfd, env=env)


FILES = [
    "src/a.test.ts",
    "src/b.test.ts",
    "src/c.test.ts",
    "lib/d.test.ts",
    "e.test.ts",
]


@pytest.mark.parametrize("n", [2, 3])
def test_two_and_three_shards_run_the_suite_once(runner: Runner, n: object) -> None:
    """1/2 and 2/2, then 1/3 to 3/3: every file once, as reported."""
    for old in runner.reports.glob("*") if runner.reports.is_dir() else []:
        old.unlink()
    fixture = {"STUB_FILES": " ".join(FILES)}
    shares = runner.shards(n, ["vitest", "run", "src"], fixture)
    runner.whole(n, shares, FILES)


def test_the_contract_the_stand_in_keeps(runner: Runner) -> None:
    """The first N mod n slices are one longer, in sha1 order."""
    order = sorted(FILES, key=vitest_key)
    shares = runner.shards(2, ["vitest", "run"], {"STUB_FILES": " ".join(FILES)})
    want: list[list[str]] = [sorted(order[:3]), sorted(order[3:])]
    got: list[list[str]] = [sorted(s) for s in shares]
    assert want == got


def test_the_command_gains_the_flag(runner: Runner) -> None:
    """`run` and the repository's own arguments kept, `--shard` appended."""
    status, out, err = runner.adapt(
        "2/2", ["vitest", "--project", "unit"], {"STUB_FILES": " ".join(FILES)}
    )
    assert status == 0, err
    want: list[str] = ["vitest", "run", "--project", "unit", "--shard=2/2"]
    assert want == words(out)


def test_more_shards_than_files_is_red(runner: Runner) -> None:
    """More shards than files: vitest refuses, so the adapter is red, by name."""
    status, _out, err = runner.adapt(
        "1/9", ["vitest", "run"], {"STUB_FILES": " ".join(FILES)}
    )
    assert status == 1
    assert "--shard <count> must be a smaller than count of test files" in err
    assert "so the share cannot be listed" in err


TESTS = [
    "crate::a t_one",
    "crate::a t_two",
    "crate::b t_one",
    "crate$bin t_three",
    "crate::a t_four",
]


@pytest.mark.parametrize("n", [2, 3])
def test_nextest_two_and_three_shards_run_the_suite_once(
    runner: Runner, n: object
) -> None:
    """Every test once, across binaries, as reported."""
    for old in runner.reports.glob("*") if runner.reports.is_dir() else []:
        old.unlink()
    fixture = {"STUB_TESTS": "\n".join(TESTS)}
    shares = runner.shards(n, ["cargo", "nextest", "run", "--workspace"], fixture)
    runner.whole(n, shares, TESTS)


def test_the_command_gains_the_partition(runner: Runner) -> None:
    """`--partition slice:k/n`, after the repository's own arguments."""
    status, out, err = runner.adapt(
        "1/2",
        ["cargo", "nextest", "run", "-p", "x"],
        {"STUB_TESTS": "\n".join(TESTS)},
    )
    assert status == 0, err
    want: list[str] = [
        "cargo",
        "nextest",
        "run",
        "-p",
        "x",
        "--partition",
        "slice:1/2",
    ]
    assert want == words(out)


def test_a_shard_dealt_nothing_runs_true(runner: Runner) -> None:
    """Two tests on three shards: the third runs `true`, never exit 4."""
    status, out, err = runner.adapt(
        "3/3", ["cargo", "nextest", "run"], {"STUB_TESTS": "\n".join(TESTS[:2])}
    )
    assert status == 0, err
    assert words(out) == TRUE


PACKAGES = [
    "example.com/m/c",
    "example.com/m/a",
    "example.com/m/sub/d",
    "example.com/m/b",
]


TEST_FILES = [
    "tests/test_a.py",
    "tests/test_b.py",
    "tests/sub/test_c.py",
    "tests/test_d.py",
]


@pytest.mark.parametrize("n", [2, 3])
def test_the_share_is_this_shards_files_and_the_union_the_population(
    runner: Runner, n: int
) -> None:
    """Each shard runs its own files; together, every file once."""
    fixture = {"STUB_FILES": " ".join(TEST_FILES)}
    command = ["pytest", "-c", "mise/pytest.toml", "-n", "auto", "tests"]
    shares = runner.shards(n, command, fixture)
    runner.whole(n, shares, TEST_FILES)
    assert shares[0] == sorted(TEST_FILES)[::n]


def test_options_stay_and_paths_become_files(runner: Runner) -> None:
    """A valued option's value is not a path; the paths become the share."""
    status, out, err = runner.adapt(
        "1/2",
        ["python3", "-m", "pytest", "-c", "cfg.toml", "--durations=10", "tests"],
        {"STUB_FILES": " ".join(TEST_FILES)},
    )
    assert status == 0, err
    want: list[str] = [
        "python3", "-m", "pytest", "-c", "cfg.toml", "--durations=10",
        "tests/sub/test_c.py", "tests/test_b.py",
    ]  # fmt: skip
    assert want == words(out)


def test_pytest_a_shard_dealt_nothing_runs_true(runner: Runner) -> None:
    """One file on two shards: the second runs `true`, not every test."""
    status, out, err = runner.adapt(
        "2/2", ["pytest", "tests"], {"STUB_FILES": TEST_FILES[0]}
    )
    assert status == 0, err
    assert words(out) == TRUE


@pytest.mark.parametrize("n", [2, 3])
def test_go_test_two_and_three_shards_run_the_suite_once(
    runner: Runner, n: object
) -> None:
    """Every package once, as reported."""
    for old in runner.reports.glob("*") if runner.reports.is_dir() else []:
        old.unlink()
    fixture = {"STUB_PACKAGES": " ".join(PACKAGES)}
    shares = runner.shards(n, ["go", "test", "-run", "X", "./..."], fixture)
    runner.whole(n, shares, PACKAGES)


def test_flags_stay_and_patterns_become_packages(runner: Runner) -> None:
    """A valued flag's value is not a package; `-args` and after stay."""
    status, out, err = runner.adapt(
        "1/2",
        ["go", "test", "-run", "X", "-v", "./...", "-args", "-x"],
        {"STUB_PACKAGES": " ".join(PACKAGES)},
    )
    assert status == 0, err
    want: list[str] = [
        "go", "test", "-run", "X", "-v",
        "example.com/m/a", "example.com/m/c", "-args", "-x",
    ]  # fmt: skip
    assert want == words(out)


def test_go_test_a_shard_dealt_nothing_runs_true(runner: Runner) -> None:
    """One package on two shards: the second runs `true`, not `go test .`."""
    status, out, err = runner.adapt(
        "2/2", ["go", "test", "./..."], {"STUB_PACKAGES": "example.com/m/a"}
    )
    assert status == 0, err
    assert words(out) == TRUE


def test_no_adapter_passes_through(runner: Runner) -> None:
    """A setup line under a shard: unchanged, no report."""
    status, out, err = runner.adapt("1/2", ["python3", "setup.py"], {})
    assert status == 0, err
    assert argv_of("python3", "setup.py") == words(out)
    assert not runner.reports.exists()


def test_no_shard_passes_through(runner: Runner) -> None:
    """ORG_SHARD empty: the vitest run as given."""
    _status, out, _err = runner.adapt("", ["vitest", "run"], {"STUB_FILES": "a"})
    assert argv_of("vitest", "run") == words(out)


def test_usage(runner: Runner, as_task: Callable[[str], None]) -> None:
    """No `--`: the usage exit."""
    as_task("test")
    status = shard_runner.main(["vitest"])
    assert status == USAGE
    assert runner.capfd.readouterr().err == (
        "test: usage: belt shard-runner -- <command> [<argument>…]\n"
    )


@pytest.mark.parametrize(
    ("line", "file"),
    [
        pytest.param("suite/test_a.py::test_it", "suite/test_a.py", id="per-test"),
        pytest.param("suite/test_b.py: 2", "suite/test_b.py", id="per-file"),
        pytest.param("  <Function test_it>", "", id="indented"),
        pytest.param("3 tests collected in 0.12s", "", id="summary"),
        pytest.param("", "", id="blank"),
    ],
)
def test_each_listing_line_names_its_file(line: str, file: str) -> None:
    """pytest's listing names a file per test, or per file under `-qq`."""
    assert shard_runner.listed(line) == file

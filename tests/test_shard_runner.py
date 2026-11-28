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
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.support import Called

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


def words(done: Called) -> list[str]:
    """Read the adapter's command, NUL-separated on its stdout.

    Returns:
        Its words.

    """
    return [w for w in done.stdout.split("\0") if w]


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


@pytest.mark.usefixtures("_set_up_test_runner")
class TestRunner:
    """A stand-in on PATH, the adapter run against it shard by shard."""

    @pytest.fixture
    def _set_up_test_runner(
        self, tmp_path: Path, call_main: Callable[..., Called]
    ) -> None:
        """Make the stand-ins' directory and the reports directory."""
        self.call = call_main
        self.base = tmp_path
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.reports = self.base / "reports"
        self.ran = self.base / "ran"
        status, out, err = (
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

    def adapt(self, spec: str, command: list[str], fixture: dict[str, str]) -> Called:
        """Call the adapter for one shard of a command.

        Returns:
            What it returned and wrote: its stdout is the command,
            NUL-separated.

        """
        env = support.env(
            PATH=f"{self.bin}:/usr/bin:/bin",
            ORG_SHARD=spec,
            ORG_SHARD_TASK="test:suite",
            ORG_SHARD_REPORTS=str(self.reports),
            STUB_RAN=str(self.ran),
            **fixture,
        )
        return self.call(
            lambda: shard_runner.main(["--", *command]), env=env, cwd=self.base
        )

    def shards(
        self, n: int, command: list[str], fixture: dict[str, str]
    ) -> list[list[str]]:
        """Adapt and run every shard of a command, as the gate's rows do.

        Returns:
            What the runner ran on each shard, shard 1 first.

        """
        out: list[list[str]] = []
        for k in range(1, n + 1):
            done = self.adapt(f"{k}/{n}", command, fixture)
            assert done.returncode == 0, done.stderr
            argv = words(done)
            self.ran.write_text("", encoding="utf-8")
            if argv != ["true"]:
                ran = subprocess.run(
                    argv,
                    cwd=self.base,
                    env=support.env(
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
            out.append([line for line in lines if not line.startswith("argv ")])
        return out

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


FILES = [
    "src/a.test.ts",
    "src/b.test.ts",
    "src/c.test.ts",
    "lib/d.test.ts",
    "e.test.ts",
]


class TestVitest(TestRunner):
    """The vitest `--shard=k/n`: contiguous slices of the hash order."""

    @pytest.mark.parametrize("n", [2, 3])
    def test_two_and_three_shards_run_the_suite_once(self, n: object) -> None:
        """1/2 and 2/2, then 1/3 to 3/3: every file once, as reported."""
        for old in self.reports.glob("*") if self.reports.is_dir() else []:
            old.unlink()
        fixture = {"STUB_FILES": " ".join(FILES)}
        shares = self.shards(n, ["vitest", "run", "src"], fixture)
        self.whole(n, shares, FILES)

    def test_the_contract_the_stand_in_keeps(self) -> None:
        """The first N mod n slices are one longer, in sha1 order."""
        order = sorted(FILES, key=vitest_key)
        shares = self.shards(2, ["vitest", "run"], {"STUB_FILES": " ".join(FILES)})
        want: list[list[str]] = [sorted(order[:3]), sorted(order[3:])]
        got: list[list[str]] = [sorted(s) for s in shares]
        assert want == got

    def test_the_command_gains_the_flag(self) -> None:
        """`run` and the repository's own arguments kept, `--shard` appended."""
        done = self.adapt(
            "2/2", ["vitest", "--project", "unit"], {"STUB_FILES": " ".join(FILES)}
        )
        assert done.returncode == 0, done.stderr
        want: list[str] = ["vitest", "run", "--project", "unit", "--shard=2/2"]
        assert want == words(done)

    def test_more_shards_than_files_is_red(self) -> None:
        """More shards than files: vitest refuses, so the adapter is red, by name."""
        done = self.adapt("1/9", ["vitest", "run"], {"STUB_FILES": " ".join(FILES)})
        assert done.returncode == 1
        assert (
            "--shard <count> must be a smaller than count of test files" in done.stderr
        )
        assert "so the share cannot be listed" in done.stderr


TESTS = [
    "crate::a t_one",
    "crate::a t_two",
    "crate::b t_one",
    "crate$bin t_three",
    "crate::a t_four",
]


class TestNextest(TestRunner):
    """nextest `--partition slice:k/n`: round-robin over every binary."""

    @pytest.mark.parametrize("n", [2, 3])
    def test_two_and_three_shards_run_the_suite_once(self, n: object) -> None:
        """Every test once, across binaries, as reported."""
        for old in self.reports.glob("*") if self.reports.is_dir() else []:
            old.unlink()
        fixture = {"STUB_TESTS": "\n".join(TESTS)}
        shares = self.shards(n, ["cargo", "nextest", "run", "--workspace"], fixture)
        self.whole(n, shares, TESTS)

    def test_the_command_gains_the_partition(self) -> None:
        """`--partition slice:k/n`, after the repository's own arguments."""
        done = self.adapt(
            "1/2",
            ["cargo", "nextest", "run", "-p", "x"],
            {"STUB_TESTS": "\n".join(TESTS)},
        )
        assert done.returncode == 0, done.stderr
        want: list[str] = [
            "cargo",
            "nextest",
            "run",
            "-p",
            "x",
            "--partition",
            "slice:1/2",
        ]
        assert want == words(done)

    def test_a_shard_dealt_nothing_runs_true(self) -> None:
        """Two tests on three shards: the third runs `true`, never exit 4."""
        done = self.adapt(
            "3/3", ["cargo", "nextest", "run"], {"STUB_TESTS": "\n".join(TESTS[:2])}
        )
        assert done.returncode == 0, done.stderr
        assert words(done) == TRUE


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


class TestPytest(TestRunner):
    """pytest: every n-th of the sorted test files, from the k-th (#2177)."""

    @pytest.mark.parametrize("n", [2, 3])
    def test_the_share_is_this_shards_files_and_the_union_the_population(
        self, n: int
    ) -> None:
        """Each shard runs its own files; together, every file once."""
        fixture = {"STUB_FILES": " ".join(TEST_FILES)}
        command = ["pytest", "-c", "mise/pytest.toml", "-n", "auto", "tests"]
        shares = self.shards(n, command, fixture)
        self.whole(n, shares, TEST_FILES)
        assert shares[0] == sorted(TEST_FILES)[::n]

    def test_options_stay_and_paths_become_files(self) -> None:
        """A valued option's value is not a path; the paths become the share."""
        done = self.adapt(
            "1/2",
            ["python3", "-m", "pytest", "-c", "cfg.toml", "--durations=10", "tests"],
            {"STUB_FILES": " ".join(TEST_FILES)},
        )
        assert done.returncode == 0, done.stderr
        want: list[str] = [
            "python3", "-m", "pytest", "-c", "cfg.toml", "--durations=10",
            "tests/sub/test_c.py", "tests/test_b.py",
        ]  # fmt: skip
        assert want == words(done)

    def test_a_shard_dealt_nothing_runs_true(self) -> None:
        """One file on two shards: the second runs `true`, not every test."""
        done = self.adapt("2/2", ["pytest", "tests"], {"STUB_FILES": TEST_FILES[0]})
        assert done.returncode == 0, done.stderr
        assert words(done) == TRUE


class TestGoTest(TestRunner):
    """`go test`: every n-th of the sorted packages, from the k-th."""

    @pytest.mark.parametrize("n", [2, 3])
    def test_two_and_three_shards_run_the_suite_once(self, n: object) -> None:
        """Every package once, as reported."""
        for old in self.reports.glob("*") if self.reports.is_dir() else []:
            old.unlink()
        fixture = {"STUB_PACKAGES": " ".join(PACKAGES)}
        shares = self.shards(n, ["go", "test", "-run", "X", "./..."], fixture)
        self.whole(n, shares, PACKAGES)

    def test_flags_stay_and_patterns_become_packages(self) -> None:
        """A valued flag's value is not a package; `-args` and after stay."""
        done = self.adapt(
            "1/2",
            ["go", "test", "-run", "X", "-v", "./...", "-args", "-x"],
            {"STUB_PACKAGES": " ".join(PACKAGES)},
        )
        assert done.returncode == 0, done.stderr
        want: list[str] = [
            "go", "test", "-run", "X", "-v",
            "example.com/m/a", "example.com/m/c", "-args", "-x",
        ]  # fmt: skip
        assert want == words(done)

    def test_a_shard_dealt_nothing_runs_true(self) -> None:
        """One package on two shards: the second runs `true`, not `go test .`."""
        done = self.adapt(
            "2/2", ["go", "test", "./..."], {"STUB_PACKAGES": "example.com/m/a"}
        )
        assert done.returncode == 0, done.stderr
        assert words(done) == TRUE


class TestUnchanged(TestRunner):
    """Any other command, or no shard, goes as it is."""

    def test_no_adapter_passes_through(self) -> None:
        """A setup line under a shard: unchanged, no report."""
        done = self.adapt("1/2", ["python3", "setup.py"], {})
        assert done.returncode == 0, done.stderr
        assert argv_of("python3", "setup.py") == words(done)
        assert not self.reports.exists()

    def test_no_shard_passes_through(self) -> None:
        """ORG_SHARD empty: the vitest run as given."""
        done = self.adapt("", ["vitest", "run"], {"STUB_FILES": "a"})
        assert argv_of("vitest", "run") == words(done)

    def test_usage(self, as_task: Callable[[str], None]) -> None:
        """No `--`: the usage exit."""
        as_task("test")
        done = self.call(lambda: shard_runner.main(["vitest"]))
        assert done.returncode == USAGE
        assert done.stderr == (
            "test: usage: belt shard-runner -- <command> [<argument>…]\n"
        )

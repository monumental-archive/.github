#!/usr/bin/env python3
"""Each runner `measured` shards is handed its share in its own terms (#2160).

mise/shard_runner.py turns ORG_SHARD=k/n into vitest's `--shard`,
nextest's `--partition slice:` and a share of `go test`'s packages. The
belt pins none of the three (the repository does), so these rows run
the adapter against stand-ins that keep each runner's measured split
contract (the owner's ruling on #2160, the measurements recorded there):

    vitest   k from 1; files sorted by sha1 of "/<root-relative path>",
             cut in contiguous slices, the first N mod n one longer;
             k < 1, k > n and n above the file count exit 1.
    nextest  `slice:k/n`, k from 1: every matching test, by binary then
             name, dealt round-robin.
    go       no shard of its own; `go list` prints import paths sorted,
             and `go test` runs the packages it is given.

Each stand-in's `run` runs its own share by that contract and records
it, so a row holds the adapter's report to what the runner ran, and the
shards' union to the whole suite with nothing twice. The real runners
are proven in the first consumer's own gate (monumental-archive#523).

stdlib `unittest`, run by `mise run test`.
"""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import shard

HERE = Path(__file__).resolve().parent
ADAPTER = HERE / "shard_runner.py"

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


def words(done: subprocess.CompletedProcess[str]) -> list[str]:
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


class Runner(unittest.TestCase):
    """A stand-in on PATH, the adapter run against it shard by shard."""

    @override
    def setUp(self) -> None:
        """Make the stand-ins' directory and the reports directory."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.reports = self.base / "reports"
        self.ran = self.base / "ran"
        for name, body in (("vitest", VITEST), ("cargo", NEXTEST), ("go", GO)):
            stub = self.bin / name
            stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            stub.chmod(0o755)

    def adapt(
        self, spec: str, command: list[str], fixture: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter for one shard of a command.

        Returns:
            The finished adapter: its stdout is the command, NUL-separated.

        """
        env = hermetic.env(
            PATH=f"{self.bin}:/usr/bin:/bin",
            ORG_SHARD=spec,
            ORG_SHARD_TASK="test:suite",
            ORG_SHARD_REPORTS=str(self.reports),
            STUB_RAN=str(self.ran),
            **fixture,
        )
        return subprocess.run(
            [sys.executable, str(ADAPTER), "--", *command],
            cwd=self.base,
            env=env,
            capture_output=True,
            text=True,
            check=False,
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
            self.assertEqual(0, done.returncode, done.stderr)
            argv = words(done)
            self.ran.write_text("", encoding="utf-8")
            if argv != ["true"]:
                ran = subprocess.run(
                    argv,
                    cwd=self.base,
                    env=hermetic.env(
                        PATH=f"{self.bin}:/usr/bin:/bin",
                        STUB_RAN=str(self.ran),
                        **fixture,
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, ran.returncode, ran.stderr)
            lines = self.ran.read_text("utf-8").splitlines()
            out.append([line for line in lines if not line.startswith("argv ")])
        return out

    def whole(self, n: int, shares: list[list[str]], population: list[str]) -> None:
        """Hold the shards to the population and the reports to the shares."""
        ran = sorted(item for share in shares for item in share)
        want: list[str] = sorted(population)
        self.assertEqual(want, ran)
        none: list[str] = []
        self.assertEqual(none, shard.union(shard.load(self.reports), {"test:suite": n}))
        reported: dict[int, list[str]] = {}
        for doc in shard.load(self.reports):
            k = decoded.as_int(doc.get("shard"), "shard")
            items = decoded.as_array(doc.get("ran"), "ran")
            reported[k] = sorted(decoded.as_str(i, "ran") for i in items)
        mine = {k: sorted(share) for k, share in enumerate(shares, 1) if share}
        kept: dict[int, list[str]] = {k: v for k, v in reported.items() if v}
        self.assertEqual(mine, kept)


FILES = [
    "src/a.test.ts",
    "src/b.test.ts",
    "src/c.test.ts",
    "lib/d.test.ts",
    "e.test.ts",
]


class Vitest(Runner):
    """The vitest `--shard=k/n`: contiguous slices of the hash order."""

    def test_two_and_three_shards_run_the_suite_once(self) -> None:
        """1/2 and 2/2, then 1/3 to 3/3: every file once, as reported."""
        for n in (2, 3):
            with self.subTest(n=n):
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
        self.assertEqual(want, got)

    def test_the_command_gains_the_flag(self) -> None:
        """`run` and the repository's own arguments kept, `--shard` appended."""
        done = self.adapt(
            "2/2", ["vitest", "--project", "unit"], {"STUB_FILES": " ".join(FILES)}
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want: list[str] = ["vitest", "run", "--project", "unit", "--shard=2/2"]
        self.assertEqual(want, words(done))

    def test_more_shards_than_files_is_red(self) -> None:
        """More shards than files: vitest refuses, so the adapter is red, by name."""
        done = self.adapt("1/9", ["vitest", "run"], {"STUB_FILES": " ".join(FILES)})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "--shard <count> must be a smaller than count of test files", done.stderr
        )
        self.assertIn("so the share cannot be listed", done.stderr)


TESTS = [
    "crate::a t_one",
    "crate::a t_two",
    "crate::b t_one",
    "crate$bin t_three",
    "crate::a t_four",
]


class Nextest(Runner):
    """nextest `--partition slice:k/n`: round-robin over every binary."""

    def test_two_and_three_shards_run_the_suite_once(self) -> None:
        """Every test once, across binaries, as reported."""
        for n in (2, 3):
            with self.subTest(n=n):
                for old in self.reports.glob("*") if self.reports.is_dir() else []:
                    old.unlink()
                fixture = {"STUB_TESTS": "\n".join(TESTS)}
                shares = self.shards(
                    n, ["cargo", "nextest", "run", "--workspace"], fixture
                )
                self.whole(n, shares, TESTS)

    def test_the_command_gains_the_partition(self) -> None:
        """`--partition slice:k/n`, after the repository's own arguments."""
        done = self.adapt(
            "1/2",
            ["cargo", "nextest", "run", "-p", "x"],
            {"STUB_TESTS": "\n".join(TESTS)},
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want: list[str] = [
            "cargo",
            "nextest",
            "run",
            "-p",
            "x",
            "--partition",
            "slice:1/2",
        ]
        self.assertEqual(want, words(done))

    def test_a_shard_dealt_nothing_runs_true(self) -> None:
        """Two tests on three shards: the third runs `true`, never exit 4."""
        done = self.adapt(
            "3/3", ["cargo", "nextest", "run"], {"STUB_TESTS": "\n".join(TESTS[:2])}
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(TRUE, words(done))


PACKAGES = [
    "example.com/m/c",
    "example.com/m/a",
    "example.com/m/sub/d",
    "example.com/m/b",
]


class GoTest(Runner):
    """`go test`: every n-th of the sorted packages, from the k-th."""

    def test_two_and_three_shards_run_the_suite_once(self) -> None:
        """Every package once, as reported."""
        for n in (2, 3):
            with self.subTest(n=n):
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
        self.assertEqual(0, done.returncode, done.stderr)
        want: list[str] = [
            "go", "test", "-run", "X", "-v",
            "example.com/m/a", "example.com/m/c", "-args", "-x",
        ]  # fmt: skip
        self.assertEqual(want, words(done))

    def test_a_shard_dealt_nothing_runs_true(self) -> None:
        """One package on two shards: the second runs `true`, not `go test .`."""
        done = self.adapt(
            "2/2", ["go", "test", "./..."], {"STUB_PACKAGES": "example.com/m/a"}
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(TRUE, words(done))


class Unchanged(Runner):
    """Any other command, or no shard, goes as it is."""

    def test_no_adapter_passes_through(self) -> None:
        """A setup line under a shard: unchanged, no report."""
        done = self.adapt("1/2", ["python3", "setup.py"], {})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(argv_of("python3", "setup.py"), words(done))
        self.assertFalse(self.reports.exists())

    def test_no_shard_passes_through(self) -> None:
        """ORG_SHARD empty: the vitest run as given."""
        done = self.adapt("", ["vitest", "run"], {"STUB_FILES": "a"})
        self.assertEqual(argv_of("vitest", "run"), words(done))

    def test_usage(self) -> None:
        """No `--`: the usage exit."""
        done = subprocess.run(
            [sys.executable, str(ADAPTER), "vitest"],
            env=hermetic.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("usage: shard_runner.py -- <command>", done.stderr)


if __name__ == "__main__":
    unittest.main()

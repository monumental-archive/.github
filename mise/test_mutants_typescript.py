#!/usr/bin/env python3
"""Table tests for audit:mutants' TypeScript leg (#1358).

mise/mutants-typescript.sh runs for real in a planted git tree, with
StrykerJS as a stub at `node_modules/.bin/stryker`, where the leg runs
the repository's own copy. No row runs StrykerJS: the first real run is
the audit job's. The leg was proven against StrykerJS 10.1.1's real
output once, on one file with one worker (#1358's standup comment); the
report the stub writes has that output's shape
(mutation-testing-report-schema 1.0: per mutant `mutatorName`,
`replacement`, `status`, `statusReason`, `location`).

The stub logs its arguments, may change a tracked file (as an
interrupted in-place run would leave it), copies the planted report to
`reports/mutation/mutation.json` and exits with the planted status.
Every row asserts the exact output, so a row cannot pass by stopping
somewhere else.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import hermetic
import mutants_typescript

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:mutants"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-typescript.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
LABEL = "audit:mutants: typescript"
MARK = "// Stryker disable next-line <Mutator>: <reason>"
RED_TAIL = (
    f"{LABEL}: red — delete code a survivor proves redundant, kill each"
    " survivor with an assertion, add a test for each mutant no test"
    " reaches, or record an equivalent mutant at its"
    f" site: {MARK}"
)


STRYKER_STUB = """#!/usr/bin/env bash
printf 'stryker %s\\n' "$*" >> "${PLANT_LOG}"
[[ -z ${PLANT_DIRTY:-} ]] || printf 'mutated\\n' >> "${PLANT_DIRTY}"
[[ -z ${PLANT_OUTPUT:-} ]] || printf '%s\\n' "${PLANT_OUTPUT}"
if [[ -s ${PLANT_REPORT} ]]; then
  mkdir -p reports/mutation
  cp "${PLANT_REPORT}" reports/mutation/mutation.json
fi
exit "${PLANT_STATUS:-0}"
"""

CONFIG: dict[str, object] = {
    "testRunner": "vitest",
    "plugins": ["@stryker-mutator/vitest-runner"],
    "mutate": ["src/**/*.ts"],
}
SOURCE = "export const add = (a: number, b: number): number => a + b;\n"
CALL = (
    "stryker run stryker.config.json --inPlace --mutate src/add.ts"
    " --timeoutMS 5000 --timeoutFactor 1.5 --reporters json,progress-append-only"
)
# The share line every run of the planted one-file tree prints first.
SHARED = f"{LABEL}: shard 0/1: 1 of 1 files"


def mutant(
    status: str,
    line: int,
    *,
    mutator: str = "ArithmeticOperator",
    replacement: str = "a - b",
    reason: str | None = None,
) -> dict[str, object]:
    """Spell one mutant as StrykerJS 10.1.1's JSON report does.

    Returns:
        The mutant object.

    """
    found: dict[str, object] = {
        "id": str(line),
        "mutatorName": mutator,
        "replacement": replacement,
        "status": status,
        "static": False,
        "coveredBy": [],
        "location": {
            "start": {"line": line, "column": 54},
            "end": {"line": line, "column": 59},
        },
    }
    if reason is not None:
        found["statusReason"] = reason
    return found


def report(*mutants: dict[str, object]) -> dict[str, object]:
    """Wrap mutants in a report of one file, `src/add.ts`.

    Returns:
        The report object.

    """
    return {
        "schemaVersion": "1.0",
        "thresholds": {"high": 80, "low": 60, "break": None},
        "files": {
            "src/add.ts": {
                "language": "typescript",
                "source": SOURCE,
                "mutants": list(mutants),
            }
        },
    }


KILLED = (mutant("Killed", 1), mutant("Timeout", 2, mutator="BlockStatement"))


class Planted(unittest.TestCase):
    """A planted tree and a stub StrykerJS; the rows are in the classes below."""

    @override
    def setUp(self) -> None:
        """Plant a git tree, the stub and its answers."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.planted = self.tmp / "report.json"
        self.planted.touch()
        self.summary = self.tmp / "summary"
        self.env = hermetic.env(
            MISE_TASK_NAME=TASK,
            PLANT_LOG=str(self.log),
            PLANT_REPORT=str(self.planted),
            MUTANTS_SUMMARY_DIR=str(self.summary),
            ORG_BELT_DIR=str(BELT),
            CI="true",
        )
        self.git("init", "-q")
        self.track(".gitignore", "node_modules/\nreports/\n")
        self.track("src/add.ts", SOURCE)

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run([GIT, *args], cwd=self.tree, env=hermetic.env(), check=True)

    def track(self, path: str, text: str) -> None:
        """Write a file into the planted tree and track it."""
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "-f", path)

    def install(self) -> None:
        """Put the stub where the repository's StrykerJS would be."""
        stub = self.tree / "node_modules" / ".bin" / "stryker"
        stub.parent.mkdir(parents=True)
        stub.write_text(STRYKER_STUB, encoding="utf-8")
        stub.chmod(0o755)

    def plant(self, *mutants: dict[str, object], config: object = None) -> None:
        """Adopt, install the stub, commit, and plant the report."""
        self.track("stryker.config.json", json.dumps(config or CONFIG))
        self.install()
        self.git("commit", "-q", "-m", "plant")
        self.planted.write_text(json.dumps(report(*mutants)), encoding="utf-8")

    def run_leg(self, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the leg in the planted tree.

        Returns:
            The finished process and the stub's call log.

        """
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def counts(self) -> str:
        """Read the counts the leg wrote for the totals job.

        Returns:
            typescript.tsv's text.

        """
        return (self.summary / "typescript.tsv").read_text(encoding="utf-8")


class Adoption(Planted):
    """Adoption is the tool's own config file, tracked, in JSON."""

    def test_no_config_skips_clean_by_name(self) -> None:
        """No stryker.config.json: exit 0, named, nothing called, skipped counted."""
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [f"{LABEL}: no stryker.config.json tracked, skipped"]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(done.stderr, "")
        self.assertEqual(log, list[str]())
        self.assertEqual(self.counts(), "0/1\tskipped\n")

    def test_an_untracked_config_is_not_adoption(self) -> None:
        """A config on disk that git does not track adopts nothing."""
        (self.tree / "stryker.config.json").write_text(
            json.dumps(CONFIG), encoding="utf-8"
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("skipped", done.stdout)
        self.assertEqual(log, list[str]())

    def test_a_config_in_another_form_is_red_never_skipped(self) -> None:
        """A tracked stryker.config.mjs is adoption the leg cannot read: red."""
        self.track("stryker.config.mjs", "export default {};\n")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: a StrykerJS config the leg cannot read: stryker.config.mjs",
            "  the leg reads stryker.config.json only; move the config there",
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_stryker_missing_is_red_by_name(self) -> None:
        """Adopted with no StrykerJS installed: red, naming what to add."""
        self.track("stryker.config.json", json.dumps(CONFIG))
        done, _log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: stryker.config.json is tracked but"
                " node_modules/.bin/stryker"
                " is missing — add"
            ),
            (
                "  @stryker-mutator/core and its runner plugin to devDependencies,"
                " and install"
            ),
        ]
        self.assertEqual(done.stderr.splitlines(), want)


class Shards(Planted):
    """MUTANTS_SHARD=k/n shares the files out; each shard reports its own."""

    def test_a_shard_mutates_its_share_and_reports_its_shard(self) -> None:
        """Shard 1/2 of two files names the second to StrykerJS, and its row."""
        self.track("src/sub.ts", SOURCE)
        self.plant(*KILLED)
        done, log = self.run_leg(MUTANTS_SHARD="1/2")
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: shard 1/2: 1 of 2 files",
            (
                f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
                " 0 not viable, 0 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        want = [
            (
                "stryker run stryker.config.json --inPlace --mutate src/sub.ts"
                " --timeoutMS 5000 --timeoutFactor 1.5"
                " --reporters json,progress-append-only"
            )
        ]
        self.assertEqual(log, want)
        self.assertEqual(self.counts(), "1/2\t2\t2\t0\t0\tshard\n")

    def test_an_empty_share_runs_nothing_and_counts_zero(self) -> None:
        """More shards than files: the spare shard is a zero row, green."""
        self.plant(*KILLED)
        done, log = self.run_leg(MUTANTS_SHARD="2/3")
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: shard 2/3: 0 of 1 files",
            f"{LABEL}: shard 2/3: no file to mutate, nothing run",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(log, list[str]())
        self.assertEqual(self.counts(), "2/3\t0\t0\t0\t0\tshard\n")

    def test_a_shard_of_several_may_hold_no_mutant(self) -> None:
        """Files with nothing to mutate, in one shard of several: zero, green."""
        self.plant()
        done, _ = self.run_leg(MUTANTS_SHARD="0/2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.counts(), "0/2\t0\t0\t0\t0\tshard\n")

    def test_progress_reaches_the_job_log_as_it_runs(self) -> None:
        """StrykerJS's output is on stdout, not only in the scratch file."""
        self.plant(*KILLED)
        done, _ = self.run_leg(PLANT_OUTPUT="Mutation testing 50% (elapsed: ~1m)")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Mutation testing 50% (elapsed: ~1m)", done.stdout.splitlines())

    def test_a_ranged_entry_is_red_before_the_run(self) -> None:
        """A mutation range cannot be shared out by file: red, by name."""
        self.plant(*KILLED, config=CONFIG | {"mutate": ["src/add.ts:1-5"]})
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: `mutate` entry 'src/add.ts:1-5' carries a mutation"
                " range; a range cannot be shared out by file"
            )
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_a_malformed_shard_is_red(self) -> None:
        """K must be below n: 2/2 is red before anything runs."""
        self.plant(*KILLED)
        done, log = self.run_leg(MUTANTS_SHARD="2/2")
        self.assertEqual(done.returncode, 1)
        want = [f"{LABEL}: MUTANTS_SHARD='2/2' is not k/n with 0 <= k < n"]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())


class ShareOut(unittest.TestCase):
    """`share`: the config's `mutate` list, expanded and cut round-robin."""

    FILES = (
        "src/a.ts",
        "src/b.ts",
        "src/c.tsx",
        "src/deep/d.ts",
        "src/deep/d.test.ts",
        "src/.hidden/h.ts",
        "src/[id]/page.ts",
        "lib/l.ts",
        "README.md",
    )

    @override
    def setUp(self) -> None:
        """Plant a git tree of tracked files, and work in it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = tmp / "tree"
        self.tree.mkdir()
        self.out = tmp / "mutate"
        git = [GIT, "-C", str(self.tree)]
        subprocess.run([*git, "init", "-q"], env=hermetic.env(), check=True)
        for path in self.FILES:
            (self.tree / path).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / path).write_text(SOURCE, encoding="utf-8")
        (self.tree / "untracked.ts").write_text(SOURCE, encoding="utf-8")
        subprocess.run([*git, "add", "--", *self.FILES], env=hermetic.env(), check=True)
        self.enterContext(contextlib.chdir(self.tree))

    def share(self, mutate: list[str], shard: str) -> hermetic.Called:
        """Write a config naming `mutate` and share it out.

        Returns:
            What `main` returned and wrote.

        """
        config = self.tree / "stryker.config.json"
        config.write_text(json.dumps(CONFIG | {"mutate": mutate}), encoding="utf-8")
        return call(["share", str(config), "--shard", shard, "--out", str(self.out)])

    def shared(self, mutate: list[str], shard: str) -> list[str]:
        """Share out and read the files back from the argument.

        Returns:
            The shard's files, unescaped, in order.

        """
        done = self.share(mutate, shard)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.out.read_text(encoding="utf-8")
        return [re.sub(r"\\(.)", r"\1", f) for f in text.split(",") if f]

    def test_three_shards_hold_every_file_exactly_once(self) -> None:
        """A `!` entry subtracts; 0/3, 1/3 and 2/3 partition what is left."""
        mutate = ["src/**/*.{ts,tsx}", "lib/*.ts", "!src/**/*.test.ts"]
        every = [
            "lib/l.ts",
            "src/[id]/page.ts",
            "src/a.ts",
            "src/b.ts",
            "src/c.tsx",
            "src/deep/d.ts",
        ]
        shards = [self.shared(mutate, f"{k}/3") for k in range(3)]
        want = [every[0::3], every[1::3], every[2::3]]
        self.assertEqual(shards, want)
        held = sorted(f for s in shards for f in s)
        self.assertEqual(held, every)

    def test_the_whole_share_prints_its_count(self) -> None:
        """0/1 takes every file, and says how many of how many."""
        done = self.share(["src/*.ts"], "0/1")
        self.assertEqual(
            done, hermetic.Called(0, f"{LABEL}: shard 0/1: 2 of 2 files\n", "")
        )
        self.assertEqual(self.out.read_text(encoding="utf-8"), "src/a.ts,src/b.ts")

    def test_entries_apply_in_order(self) -> None:
        """An entry after a `!` adds back what the `!` took, as StrykerJS reads it."""
        mutate = ["src/*.ts", "!src/*.ts", "src/a.ts"]
        want = ["src/a.ts"]
        self.assertEqual(self.shared(mutate, "0/1"), want)

    def test_a_dotted_name_is_matched_only_by_a_dotted_part(self) -> None:
        """Minimatch's `dot: false`: `**` never reaches `.hidden`; `.hidden` does."""
        self.assertNotIn("src/.hidden/h.ts", self.shared(["src/**/*.ts"], "0/1"))
        want = ["src/.hidden/h.ts"]
        self.assertEqual(self.shared(["src/.hidden/*.ts"], "0/1"), want)

    def test_a_file_name_with_glob_syntax_is_escaped(self) -> None:
        """`[id]` is named literally, so minimatch matches that file alone."""
        self.share(["src/\\[id\\]/*.ts"], "0/1")
        self.assertEqual(self.out.read_text(encoding="utf-8"), r"src/\[id\]/page.ts")

    def test_a_ranged_entry_is_red_by_name(self) -> None:
        """A range, even under `!`, cannot be shared out by file."""
        done = self.share(["src/*.ts", "!src/a.ts:1:2-3:4"], "0/1")
        want = (
            f"{LABEL}: `mutate` entry '!src/a.ts:1:2-3:4' carries a mutation"
            " range; a range cannot be shared out by file\n"
        )
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_an_extglob_is_red_by_name(self) -> None:
        """`full_match` cannot spell an extglob, so it is refused, not misread."""
        done = self.share(["src/+(a|b).ts"], "0/1")
        want = (
            f"{LABEL}: `mutate` entry 'src/+(a|b).ts' is an extglob, which the"
            " leg cannot expand\n"
        )
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_a_list_matching_nothing_is_red(self) -> None:
        """No tracked file matched: red, never an empty run."""
        done = self.share(["untracked.ts"], "0/1")
        want = f"{LABEL}: `mutate` matches no tracked file\n"
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_a_name_with_a_comma_is_red(self) -> None:
        """--mutate splits on commas, so such a file cannot be named."""
        (self.tree / "src/x,y.ts").write_text(SOURCE, encoding="utf-8")
        subprocess.run([GIT, "add", "--", "src/x,y.ts"], env=hermetic.env(), check=True)
        done = self.share(["src/x*.ts"], "0/1")
        want = (
            f"{LABEL}: src/x,y.ts: StrykerJS's --mutate cannot name this file"
            " (a comma, or a trailing range)\n"
        )
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_a_class_negated_with_a_caret_is_read_as_minimatch_reads_it(self) -> None:
        """`[^a]` is every one character but `a`, as `[!a]` is."""
        want = ["src/b.ts"]
        self.assertEqual(self.shared(["src/[^a].ts"], "0/1"), want)

    def test_entries_resolve_against_the_tree(self) -> None:
        """`./`, a path back into the tree and an absolute one; outside is none."""
        mutate = [
            "./src/a.ts",
            "../tree/src/b.ts",
            f"{self.tree.resolve().as_posix()}/lib/*.ts",
            "../elsewhere/*.ts",
        ]
        want = ["lib/l.ts", "src/a.ts", "src/b.ts"]
        self.assertEqual(self.shared(mutate, "0/1"), want)

    def test_a_shard_with_files_writes_no_counts(self) -> None:
        """Only an empty share writes its row; a run's row is the verdict's."""
        summary = self.out.parent / "summary"
        config = self.tree / "stryker.config.json"
        config.write_text(json.dumps(CONFIG), encoding="utf-8")
        argv = ["share", str(config), "--out", str(self.out)]
        done = call([*argv, "--summary-dir", str(summary)])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(summary.exists())

    def test_a_name_ending_in_a_range_is_red(self) -> None:
        """--mutate would read the name's tail as a range: red, by name."""
        (self.tree / "src/x:1-2").write_text(SOURCE, encoding="utf-8")
        subprocess.run([GIT, "add", "--", "src/x:1-2"], env=hermetic.env(), check=True)
        done = self.share(["src/x*"], "0/1")
        want = (
            f"{LABEL}: src/x:1-2: StrykerJS's --mutate cannot name this file"
            " (a comma, or a trailing range)\n"
        )
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_each_malformed_shard_is_red(self) -> None:
        """Not two whole numbers, or k not below n: red, by name."""
        for shard in ("x", "1/2/3", "2/2", "3/2", "-1/2"):
            with self.subTest(shard=shard):
                done = self.share(["src/*.ts"], shard)
                want = f"{LABEL}: MUTANTS_SHARD='{shard}' is not k/n with 0 <= k < n\n"
                self.assertEqual(done, hermetic.Called(1, "", want))

    def test_a_config_with_no_mutate_list_is_red(self) -> None:
        """`share` reads the list itself; a missing one is named."""
        config = self.tree / "stryker.config.json"
        bare: dict[str, object] = {"testRunner": "vitest"}
        config.write_text(json.dumps(bare), encoding="utf-8")
        done = call(["share", str(config), "--out", str(self.out)])
        want = f"{LABEL}: {config} names no `mutate` list\n"
        self.assertEqual(done, hermetic.Called(1, "", want))

    def test_an_unreadable_config_is_red(self) -> None:
        """No file: red, never an empty share."""
        done = call(["share", str(self.tree / "none.json"), "--out", str(self.out)])
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stderr.startswith(f"{LABEL}: "), done.stderr)


class Braces(unittest.TestCase):
    """minimatch's braces, expanded before `full_match` reads a pattern."""

    ROWS: tuple[tuple[str, list[str]], ...] = (
        ("a.{ts,tsx}", ["a.ts", "a.tsx"]),
        ("{a,b{1,2}}.ts", ["a.ts", "b1.ts", "b2.ts"]),
        ("{a{1,2},b}.ts", ["a1.ts", "a2.ts", "b.ts"]),
        ("f{1..3}.ts", ["f1.ts", "f2.ts", "f3.ts"]),
        ("f{3..1}.ts", ["f3.ts", "f2.ts", "f1.ts"]),
        ("f{2..2}.ts", ["f2.ts"]),
        ("{c..a}.ts", ["c.ts", "b.ts", "a.ts"]),
        ("{x}.ts", ["{x}.ts"]),
        ("{x}{a,b}", ["{x}a", "{x}b"]),
        ("{x{a,b}}", ["{xa}", "{xb}"]),
        ("\\{a,b}.ts", ["\\{a,b}.ts"]),
        ("\\{{a,b}.ts", ["\\{a.ts", "\\{b.ts"]),
        ("{a\\,,c}.ts", ["a\\,.ts", "c.ts"]),
        ("{a,b.ts", ["{a,b.ts"]),
        ("a,b}.ts", ["a,b}.ts"]),
    )

    def test_each_form(self) -> None:
        """Lists, nested lists, ranges, literal braces and escaped ones."""
        for pattern, want in self.ROWS:
            with self.subTest(pattern=pattern):
                self.assertEqual(mutants_typescript.braces(pattern), want)


class Spelling(unittest.TestCase):
    """minimatch's glob, spelled for `PurePosixPath.full_match`."""

    ROWS: tuple[tuple[str, str], ...] = (
        ("src/\\[id\\]/*.ts", "src/[[]id]/*.ts"),
        ("a\\*b\\?c", "a[*]b[?]c"),
        ("a\\!b", "a!b"),
        ("[^a].ts", "[!a].ts"),
        ("[a^].ts", "[a^].ts"),
        ("a\\", "a\\"),
    )

    def test_each_form(self) -> None:
        """An escape names its character; a `[^` class negates."""
        for pattern, want in self.ROWS:
            with self.subTest(pattern=pattern):
                self.assertEqual(mutants_typescript.full_match_spelling(pattern), want)


class Refusals(Planted):
    """A config that excludes mutants with no reason at a site is red."""

    ROWS: tuple[tuple[dict[str, object], str], ...] = (
        (
            {"mutator": {"excludedMutations": ["StringLiteral"]}},
            (
                "sets `mutator.excludedMutations` — it drops a mutator everywhere,"
                " with no reason at any site"
            ),
        ),
        (
            {"ignoreStatic": True},
            (
                "sets `ignoreStatic` — it drops every static mutant, with no reason at"
                " any site"
            ),
        ),
        (
            {"ignorers": ["angular"]},
            "sets `ignorers` — they drop mutants by rule, with no reason at any site",
        ),
        (
            {"thresholds": {"break": 80}},
            (
                "sets `thresholds.break` — the measure is no unexplained survivor,"
                " never a score"
            ),
        ),
        (
            {"mutate": []},
            (
                "names no `mutate` list — the paths to mutate are the repository's to"
                " state"
            ),
        ),
    )

    def test_each_refused_setting_is_red_by_name(self) -> None:
        """Each row's setting is named, and StrykerJS never runs."""
        for setting, message in self.ROWS:
            with self.subTest(setting=setting):
                self.setUp()
                self.plant(*KILLED, config=CONFIG | setting)
                done, log = self.run_leg()
                self.assertEqual(done.returncode, 1)
                want = [
                    f"{LABEL}: stryker.config.json {message}",
                    (
                        f"{LABEL}: record each equivalent mutant at its site"
                        f" instead: {MARK}"
                    ),
                ]
                self.assertEqual(done.stderr.splitlines(), want)
                self.assertEqual(log, list[str]())

    def test_incremental_is_refused_as_deferred(self) -> None:
        """`incremental: true` is red by name, with the reason it is deferred."""
        self.plant(*KILLED, config=CONFIG | {"incremental": True})
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: stryker.config.json sets `incremental` — a runner has no"
                " earlier results, so it would read as on and do nothing; the leg"
                " runs every mutant every week (deferred, #1358)"
            )
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_a_config_that_is_not_json_is_red(self) -> None:
        """An unreadable config is red, never a skip."""
        self.track("stryker.config.json", "{ mutate: [")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stderr.startswith(
                f"{LABEL}: stryker.config.json is not readable JSON: "
            ),
            done.stderr,
        )
        self.assertEqual(log, list[str]())

    def test_a_config_that_is_not_an_object_is_red(self) -> None:
        """A JSON list is no config."""
        want = ["is not a JSON object"]
        self.assertEqual(mutants_typescript.refusals([]), want)


class Verdict(Planted):
    """Statuses, survivors, exclusions and the summary line."""

    def test_clean_run_is_green_with_the_summary(self) -> None:
        """Killed and timed out: exit 0, the summary line, the counts, the call."""
        self.plant(*KILLED)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            SHARED,
            (
                f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
                " 0 not viable, 0 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(done.stderr, "")
        want = [CALL]
        self.assertEqual(log, want)
        self.assertEqual(self.counts(), "0/1\t2\t2\t0\t0\tshard\n")
        self.assertFalse((self.tree / "reports/mutation/mutation.json").exists())

    def test_one_survivor_is_red_by_file_and_line(self) -> None:
        """A survivor is named file:line:column, mutator, change."""
        self.plant(*KILLED, mutant("Survived", 7))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            SHARED,
            f"{LABEL}: survived: src/add.ts:7:54: ArithmeticOperator: a - b",
            (
                f"{LABEL}: 3 mutants, 2 killed, 1 survived, 0 not covered,"
                " 0 not viable, 0 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        want = [RED_TAIL]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(self.counts(), "0/1\t3\t2\t1\t0\tshard\n")

    def test_a_mutant_no_test_reaches_is_not_covered_and_red(self) -> None:
        """NoCoverage is a survivor of its own name, counted as survived."""
        self.plant(
            *KILLED, mutant("NoCoverage", 9, mutator="BlockStatement", replacement="{}")
        )
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            SHARED,
            f"{LABEL}: not covered: src/add.ts:9:54: BlockStatement: {{}}",
            (
                f"{LABEL}: 3 mutants, 2 killed, 0 survived, 1 not covered,"
                " 0 not viable, 0 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(self.counts(), "0/1\t3\t2\t1\t0\tshard\n")

    def test_an_excluded_survivor_with_a_reason_is_green_and_counted(self) -> None:
        """A site comment with its reason: excluded, counted, green."""
        self.plant(*KILLED, mutant("Ignored", 4, reason="zero is either sign here"))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            SHARED,
            (
                f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
                " 0 not viable, 1 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(self.counts(), "0/1\t3\t2\t0\t1\tshard\n")

    def test_an_exclusion_without_its_reason_is_red(self) -> None:
        """StrykerJS's default reason is no reason: red, by site."""
        self.plant(*KILLED, mutant("Ignored", 4, reason="Ignored using a comment"))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: excluded without a reason: src/add.ts:4:54:"
                " ArithmeticOperator: a - b"
            ),
            RED_TAIL,
        ]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_a_compile_error_is_not_viable_never_killed(self) -> None:
        """CompileError is counted on its own; green, killed unchanged."""
        self.plant(*KILLED, mutant("CompileError", 5))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            SHARED,
            (
                f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
                " 1 not viable, 0 excluded"
            ),
        ]
        self.assertEqual(done.stdout.splitlines(), want)

    def test_a_runtime_error_is_not_judged_and_writes_no_counts(self) -> None:
        """RuntimeError was not judged: red, and the totals get nothing."""
        self.plant(*KILLED, mutant("RuntimeError", 6))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: not judged: RuntimeError: src/add.ts:6:54:"
                " ArithmeticOperator: a - b"
            ),
            RED_TAIL,
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertFalse((self.summary / "typescript.tsv").exists())

    def test_an_empty_report_is_not_judged(self) -> None:
        """A report of no mutant judged nothing."""
        self.plant()
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            f"{LABEL}: not judged: the report holds no mutant — nothing was mutated",
            done.stderr.splitlines(),
        )


# OFF A RUNNER (#1432, the owner's ruling): the leg runs itself in a
# clean copy under `env -i`. Every tool it asks is a stand-in on the
# copy's PATH, read from `mise bin-paths`, because nothing outside the
# fixed list reaches the copy: `mise` names the stand-ins' directory,
# `pnpm` answers the store and cache and, installing, puts a StrykerJS
# stand-in in the copy (and a `.env`, where the row plants one),
# `docker` names a daemon. The StrykerJS stand-in writes what it saw
# (its directory, its environment, whether the real HOME's file reads,
# its DOCKER_CONFIG) to files whose paths are baked into it.
COPY_STRYKER = """#!/bin/bash
printf 'stryker %s\\n' "$*" >> {log}
pwd -P > {seen}/cwd
env > {seen}/env
cat "$HOME/secret" > {seen}/secret 2>&1 || echo unreadable > {seen}/secret
ls -A "$DOCKER_CONFIG" > {seen}/docker-config
mkdir -p reports/mutation
cp {report} reports/mutation/mutation.json
"""
COPY_TOOLS = {
    "mise": """#!/bin/bash
[[ $1 == bin-paths ]] && echo {tools}
""",
    "pnpm": """#!/bin/bash
case "$1 $2" in
  "store path") echo {tmp}/store ;;
  "config get") echo undefined ;;
  install*)
    printf 'pnpm %s\\n' "$*" >> {log}
    mkdir -p node_modules/.bin
    cp {tools}/stryker-stub node_modules/.bin/stryker
    [[ ! -e {tools}/plant-env ]] || echo SECRET=1 > .env
    ;;
esac
""",
    "docker": """#!/bin/bash
[[ $1 == context ]] && echo unix:///planted/docker.sock
""",
}


class OffRunner(Planted):
    """CI unset: a clean copy, a cleared environment, a scratch HOME."""

    @override
    def setUp(self) -> None:
        """Plant the stand-ins, a real HOME with secrets, and the tree."""
        super().setUp()
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        self.seen = self.tmp / "seen"
        self.seen.mkdir()
        fill = {
            "log": str(self.log),
            "seen": str(self.seen),
            "report": str(self.planted),
            "tools": str(self.tools),
            "tmp": str(self.tmp),
        }
        for name, text in {**COPY_TOOLS, "stryker-stub": COPY_STRYKER}.items():
            path = self.tools / name
            path.write_text(text.format(**fill), encoding="utf-8")
            path.chmod(0o755)
        self.home = self.tmp / "realhome"
        (self.home / ".docker" / "cli-plugins").mkdir(parents=True)
        (self.home / "secret").write_text("the real home\n", encoding="utf-8")
        (self.home / ".docker" / "config.json").write_text("{}", encoding="utf-8")
        plugin = self.tmp / "compose-binary"
        plugin.write_text("", encoding="utf-8")
        (self.home / ".docker" / "cli-plugins" / "docker-compose").symlink_to(plugin)
        del self.env["CI"]
        self.env |= {
            "HOME": str(self.home),
            "PATH": f"{self.tools}:{Path(GIT).parent}:/usr/bin:/bin",
            "ORG_PYTHON": sys.executable,
            "OUTSIDE_SECRET": "never in the copy",
        }
        self.track("pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        self.track("stryker.config.json", json.dumps(CONFIG))
        self.git("commit", "-q", "-m", "plant")
        self.planted.write_text(json.dumps(report(*KILLED)), encoding="utf-8")

    def seen_file(self, name: str) -> str:
        """Read what the StrykerJS stand-in saw.

        Returns:
            The file's text.

        """
        return (self.seen / name).read_text(encoding="utf-8")

    def seen_env(self) -> dict[str, str]:
        """Read the environment the StrykerJS stand-in saw.

        Returns:
            Each variable's value.

        """
        lines = self.seen_file("env").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def test_runs_in_a_clean_copy_of_the_committed_tree(self) -> None:
        """Green, in a copy, never in the tree; installed offline."""
        done, log = self.run_leg()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(f"{LABEL}: off a runner — in a clean copy of", done.stdout)
        self.assertNotEqual(str(self.tree.resolve()), self.seen_file("cwd").strip())
        self.assertFalse((self.tree / "node_modules").exists())
        store, cache = self.tmp / "store", self.home / "Library/Caches/pnpm"
        linux_cache = self.home / ".cache/pnpm"
        install = log[0].replace(str(linux_cache), str(cache))
        self.assertEqual(
            f"pnpm install --offline --frozen-lockfile --store-dir {store}"
            f" --cache-dir {cache}",
            install,
        )
        runs = [x for x in log if x.startswith("stryker run")]
        self.assertEqual(1, len(runs))

    def test_the_environment_is_the_fixed_list(self) -> None:
        """A variable set outside never reaches the run; CI is set there."""
        done, _ = self.run_leg()
        self.assertEqual(0, done.returncode, done.stderr)
        env = self.seen_env()
        self.assertNotIn("OUTSIDE_SECRET", env)
        self.assertEqual("1", env["CI"])
        self.assertEqual("unix:///planted/docker.sock", env["DOCKER_HOST"])
        self.assertTrue(env["PATH"].startswith(str(Path(sys.executable).parent)))
        self.assertIn(str(self.tools), env["PATH"].split(":"))

    def test_a_file_in_the_real_home_is_not_readable(self) -> None:
        """HOME is an empty scratch directory: ~/secret does not read."""
        done, _ = self.run_leg()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("unreadable\n", self.seen_file("secret"))
        env = self.seen_env()
        self.assertNotEqual(str(self.home), env["HOME"])

    def test_the_docker_config_holds_no_config_json(self) -> None:
        """DOCKER_CONFIG holds only the CLI plugins, linked to their targets."""
        done, _ = self.run_leg()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("cli-plugins\n", self.seen_file("docker-config"))

    def test_an_env_file_in_the_copy_is_refused(self) -> None:
        """The install writes a `.env`: red by name, StrykerJS never runs."""
        (self.tools / "plant-env").touch()
        done, log = self.run_leg()
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{LABEL}: .env is in the copy after the install, untracked — refused",
            done.stderr,
        )
        self.assertFalse(any(x.startswith("stryker") for x in log))

    def test_a_dirty_tree_is_refused_before_the_copy(self) -> None:
        """An uncommitted tracked edit would go unjudged: red, nothing run."""
        (self.tree / "src/add.ts").write_text("// edited\n", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(1, done.returncode)
        self.assertIn(f"{LABEL}: uncommitted:  M src/add.ts", done.stderr)
        self.assertEqual(list[str](), log)


class Run(Planted):
    """In place, on a clean tree, from the repository's own StrykerJS."""

    def test_a_failing_stryker_is_red_with_its_output(self) -> None:
        """A non-zero exit judged nothing: red, with StrykerJS's own words."""
        self.plant(*KILLED)
        done, _ = self.run_leg(PLANT_STATUS="3", PLANT_OUTPUT="no TestRunner plugins")
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: StrykerJS exited 3 — nothing judged; its output:",
            "    no TestRunner plugins",
        ]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_no_report_is_red(self) -> None:
        """A zero exit with no report is red; a stale report is not read."""
        self.plant(*KILLED)
        stale = self.tree / "reports/mutation/mutation.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps(report(*KILLED)), encoding="utf-8")
        self.planted.write_text("", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [f"{LABEL}: StrykerJS wrote no reports/mutation/mutation.json"]
        self.assertEqual(done.stderr.splitlines(), want)
        want = [CALL]
        self.assertEqual(log, want)

    def test_a_dirty_tree_is_refused_before_the_run(self) -> None:
        """Uncommitted tracked changes: red, and StrykerJS never runs."""
        self.plant(*KILLED)
        (self.tree / "src/add.ts").write_text("// edited\n", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: uncommitted:  M src/add.ts",
            ("  the leg mutates tracked files in place; it runs on a clean tree only"),
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_a_tree_left_changed_is_red(self) -> None:
        """A run that leaves a tracked file changed is red, whatever it judged."""
        self.plant(*KILLED)
        done, log = self.run_leg(PLANT_DIRTY=str(self.tree / "src/add.ts"))
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: left changed after the run:  M src/add.ts",
            (
                "  StrykerJS did not restore the tree it mutated in place; restore"
                " it with git"
            ),
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        want = [CALL]
        self.assertEqual(log, want)

    def test_untracked_files_do_not_block_the_run(self) -> None:
        """An untracked .env is not the tree's: the run goes ahead, in place."""
        self.plant(*KILLED)
        (self.tree / ".env").write_text("SECRET=1\n", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [CALL]
        self.assertEqual(log, want)

    def test_a_tracked_report_is_refused(self) -> None:
        """The leg replaces StrykerJS's report; it never deletes a tracked file."""
        self.track("reports/mutation/mutation.json", "{}")
        self.plant(*KILLED)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: reports/mutation/mutation.json is tracked; it is"
                " StrykerJS's"
                " output, and the leg replaces it"
            )
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())


class Shapes(unittest.TestCase):
    """A report field of another shape is refused by name, never a crash."""

    def test_each_field_the_verdict_reads(self) -> None:
        """The refusal names the field, where it is, in the report."""
        file = 'files["a.ts"]'
        rows: tuple[tuple[object, str], ...] = (
            ({"files": {"a.ts": 3}}, f"{file} is not an object"),
            ({"files": {"a.ts": {"mutants": {}}}}, f"{file}.mutants is not a list"),
            (
                {"files": {"a.ts": {"mutants": [1]}}},
                f"{file}.mutants[0] is not an object",
            ),
            (
                {"files": {"a.ts": {"mutants": [{}]}}},
                f"{file}.mutants[0].status is not a string",
            ),
            (
                {"files": {"a.ts": {"mutants": [{"status": "Killed", "location": 1}]}}},
                f"{file}.mutants[0].location is not an object",
            ),
            (
                {
                    "files": {
                        "a.ts": {"mutants": [{"location": {"start": {"line": "1"}}}]}
                    }
                },
                f"{file}.mutants[0].line is not an integer",
            ),
            (
                {"files": {"a.ts": {"mutants": [{"replacement": 1}]}}},
                f"{file}.mutants[0].replacement is not a string",
            ),
            (
                {"files": {"a.ts": {"mutants": [{"mutatorName": 1}]}}},
                f"{file}.mutants[0].mutatorName is not a string",
            ),
            (
                {
                    "files": {
                        "a.ts": {"mutants": [{"status": "Ignored", "statusReason": 1}]}
                    }
                },
                f"{file}.mutants[0].statusReason is not a string",
            ),
        )
        for value, why in rows:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp, "mutation.json")
                path.write_text(json.dumps(value), encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    status = mutants_typescript.judge(str(path), LABEL, "")
                self.assertEqual(status, 1)
                self.assertEqual(
                    err.getvalue(), f"{LABEL}: no readable report at {path}: {why}\n"
                )


def call(argv: list[str]) -> hermetic.Called:
    """Call the helper's `main` in this process (#1696).

    Returns:
        The status `main` returned and what it wrote to each stream.

    """
    return hermetic.call_main(
        lambda: mutants_typescript.main(["--label", LABEL, *argv])
    )


class Main(unittest.TestCase):
    """`config` and `verdict` through main, in this process (#1696)."""

    def write(self, name: str, value: object) -> Path:
        """Write one JSON document into a scratch directory.

        Returns:
            Its path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name, name)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_a_sound_config_passes_silently(self) -> None:
        """No refused setting: exit 0, nothing printed."""
        done = call(["config", str(self.write("stryker.config.json", CONFIG))])
        self.assertEqual(done, hermetic.Called(0, "", ""))

    def test_each_refused_setting_is_red_by_name(self) -> None:
        """Each Refusals row's setting, named with the site mark."""
        for setting, message in Refusals.ROWS:
            with self.subTest(setting=setting):
                path = self.write("stryker.config.json", CONFIG | setting)
                done = call(["config", str(path)])
                self.assertEqual(done.returncode, 1)
                want = [
                    f"{LABEL}: {path} {message}",
                    (
                        f"{LABEL}: record each equivalent mutant at its site"
                        f" instead: {MARK}"
                    ),
                ]
                self.assertEqual(done.stderr.splitlines(), want)

    def test_incremental_is_refused_as_deferred(self) -> None:
        """`incremental: true`, with the reason it is deferred."""
        path = self.write("stryker.config.json", CONFIG | {"incremental": True})
        done = call(["config", str(path)])
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: {path} sets `incremental` — a runner has no earlier results,"
            " so it would read as on and do nothing; the leg runs every mutant every"
            " week (deferred, #1358)\n",
        )

    def test_an_unreadable_config_is_red(self) -> None:
        """Not JSON: named, exit 1."""
        path = self.write("stryker.config.json", 0)
        path.write_text("{ mutate: [", encoding="utf-8")
        done = call(["config", str(path)])
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stderr.startswith(f"{LABEL}: {path} is not readable JSON: "),
            done.stderr,
        )

    def test_a_clean_verdict_writes_its_counts(self) -> None:
        """Killed and timed out: exit 0, the summary line, typescript.tsv."""
        path = self.write("mutation.json", report(*KILLED))
        summary = path.parent / "summary"
        done = call(["verdict", str(path), "--summary-dir", str(summary)])
        self.assertEqual(
            done,
            hermetic.Called(
                0,
                f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
                " 0 not viable, 0 excluded\n",
                "",
            ),
        )
        self.assertEqual(
            (summary / "typescript.tsv").read_text(encoding="utf-8"),
            "0/1\t2\t2\t0\t0\tshard\n",
        )

    def test_a_verdict_writes_the_shard_it_was_given(self) -> None:
        """The row names `--shard`, never a fixed 0/1 (#2153)."""
        path = self.write("mutation.json", report(*KILLED))
        summary = path.parent / "summary"
        done = call([
            "verdict",
            str(path),
            "--shard",
            "3/8",
            "--summary-dir",
            str(summary),
        ])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            (summary / "typescript.tsv").read_text(encoding="utf-8"),
            "3/8\t2\t2\t0\t0\tshard\n",
        )

    def test_a_survivor_is_red_and_writes_its_counts(self) -> None:
        """A survivor is printed by site, and the red tail follows."""
        path = self.write("mutation.json", report(mutant("Survived", 3)))
        done = call(["verdict", str(path)])
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines()[0],
            f"{LABEL}: survived: src/add.ts:3:54: ArithmeticOperator: a - b",
        )
        self.assertEqual(done.stderr, RED_TAIL + "\n")


class Script(unittest.TestCase):
    """The one row that starts the helper as a process: the script contract."""

    def test_the_helper_runs_as_a_script(self) -> None:
        """It reads its arguments, judges a sound config and exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "stryker.config.json")
            path.write_text(json.dumps(CONFIG), encoding="utf-8")
            done = subprocess.run(
                [
                    sys.executable,
                    str(BELT / "mutants_typescript.py"),
                    "config",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))


if __name__ == "__main__":
    unittest.main()

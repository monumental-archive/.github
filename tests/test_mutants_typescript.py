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
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import mutants_typescript
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:mutants"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
SCRIPT = BELT / "mutants-typescript.sh"
BASH = support.tool("bash")
GIT = support.tool("git")
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
    " --reporters json,progress-append-only"
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


class Planted:
    """A planted tree and a stub StrykerJS; the rows are in the classes below."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a git tree, the stub and its answers."""
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.planted = self.tmp / "report.json"
        self.planted.touch()
        self.summary = self.tmp / "summary"
        self.env = self.make_env(
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
        subprocess.run([GIT, *args], cwd=self.tree, env=self.make_env(), check=True)

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


@fixture
def planted_state(tmp_path: Path, env: support.Env) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, env=env)


def test_no_config_skips_clean_by_name(planted_state: Planted) -> None:
    """No stryker.config.json: exit 0, named, nothing called, skipped counted."""
    done, log = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    want = [f"{LABEL}: no stryker.config.json tracked, skipped"]
    assert done.stdout.splitlines() == want
    assert done.stderr == ""
    assert log == list[str]()
    assert planted_state.counts() == "0/1\tskipped\n"


def test_an_untracked_config_is_not_adoption(planted_state: Planted) -> None:
    """A config on disk that git does not track adopts nothing."""
    (planted_state.tree / "stryker.config.json").write_text(
        json.dumps(CONFIG), encoding="utf-8"
    )
    done, log = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    assert "skipped" in done.stdout
    assert log == list[str]()


def test_a_config_in_another_form_is_red_never_skipped(planted_state: Planted) -> None:
    """A tracked stryker.config.mjs is adoption the leg cannot read: red."""
    planted_state.track("stryker.config.mjs", "export default {};\n")
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        f"{LABEL}: a StrykerJS config the leg cannot read: stryker.config.mjs",
        "  the leg reads stryker.config.json only; move the config there",
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_a_workstation_is_refused_by_name(planted_state: Planted) -> None:
    """No CI: adopted, the leg refuses before anything runs."""
    planted_state.plant(*KILLED)
    env = dict(planted_state.env)
    del env["CI"]
    done = subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=planted_state.tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: CI is unset — mutants run in place with the tree's own"
            " environment,"
        ),
        ("  so this leg runs on a runner only (repo-mutants.yml), never a workstation"),
    ]
    assert done.stderr.splitlines() == want
    assert planted_state.log.read_text(encoding="utf-8") == ""


def test_stryker_missing_is_red_by_name(planted_state: Planted) -> None:
    """Adopted with no StrykerJS installed: red, naming what to add."""
    planted_state.track("stryker.config.json", json.dumps(CONFIG))
    done, _log = planted_state.run_leg()
    assert done.returncode == 1
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
    assert done.stderr.splitlines() == want


def test_a_shard_mutates_its_share_and_reports_its_shard(
    planted_state: Planted,
) -> None:
    """Shard 1/2 of two files names the second to StrykerJS, and its row."""
    planted_state.track("src/sub.ts", SOURCE)
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg(MUTANTS_SHARD="1/2")
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: shard 1/2: 1 of 2 files",
        (
            f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
            " 0 not viable, 0 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want
    want = [
        (
            "stryker run stryker.config.json --inPlace --mutate src/sub.ts"
            " --reporters json,progress-append-only"
        )
    ]
    assert log == want
    assert planted_state.counts() == "1/2\t2\t2\t0\t0\tshard\n"


def test_an_empty_share_runs_nothing_and_counts_zero(planted_state: Planted) -> None:
    """More shards than files: the spare shard is a zero row, green."""
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg(MUTANTS_SHARD="2/3")
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: shard 2/3: 0 of 1 files",
        f"{LABEL}: shard 2/3: no file to mutate, nothing run",
    ]
    assert done.stdout.splitlines() == want
    assert log == list[str]()
    assert planted_state.counts() == "2/3\t0\t0\t0\t0\tshard\n"


def test_a_shard_of_several_may_hold_no_mutant(planted_state: Planted) -> None:
    """Files with nothing to mutate, in one shard of several: zero, green."""
    planted_state.plant()
    done, _ = planted_state.run_leg(MUTANTS_SHARD="0/2")
    assert done.returncode == 0, done.stderr
    assert planted_state.counts() == "0/2\t0\t0\t0\t0\tshard\n"


def test_progress_reaches_the_job_log_as_it_runs(planted_state: Planted) -> None:
    """StrykerJS's output is on stdout, not only in the scratch file."""
    planted_state.plant(*KILLED)
    done, _ = planted_state.run_leg(PLANT_OUTPUT="Mutation testing 50% (elapsed: ~1m)")
    assert done.returncode == 0, done.stderr
    assert "Mutation testing 50% (elapsed: ~1m)" in done.stdout.splitlines()


def test_a_ranged_entry_is_red_before_the_run(planted_state: Planted) -> None:
    """A mutation range cannot be shared out by file: red, by name."""
    planted_state.plant(*KILLED, config=CONFIG | {"mutate": ["src/add.ts:1-5"]})
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: `mutate` entry 'src/add.ts:1-5' carries a mutation"
            " range; a range cannot be shared out by file"
        )
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_a_malformed_shard_is_red(planted_state: Planted) -> None:
    """K must be below n: 2/2 is red before anything runs."""
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg(MUTANTS_SHARD="2/2")
    assert done.returncode == 1
    want = [f"{LABEL}: MUTANTS_SHARD='2/2' is not k/n with 0 <= k < n"]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


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


class ShareOut:
    """`share`: the config's `mutate` list, expanded and cut round-robin."""

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, env: support.Env
    ) -> None:
        """Plant a git tree of tracked files, and work in it."""
        self.make_env = env
        tmp = tmp_path
        self.tree = tmp / "tree"
        self.tree.mkdir()
        self.out = tmp / "mutate"
        git = [GIT, "-C", str(self.tree)]
        subprocess.run([*git, "init", "-q"], env=self.make_env(), check=True)
        for path in FILES:
            (self.tree / path).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / path).write_text(SOURCE, encoding="utf-8")
        (self.tree / "untracked.ts").write_text(SOURCE, encoding="utf-8")
        subprocess.run([*git, "add", "--", *FILES], env=self.make_env(), check=True)
        monkeypatch.chdir(self.tree)

    def share(
        self, mutate: list[str], shard: str, *, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Write a config naming `mutate` and share it out.

        Returns:
            What `main` returned and wrote.

        """
        config = self.tree / "stryker.config.json"
        config.write_text(json.dumps(CONFIG | {"mutate": mutate}), encoding="utf-8")
        return call(
            ["share", str(config), "--shard", shard, "--out", str(self.out)],
            capfd=capfd,
        )

    def shared(
        self, mutate: list[str], shard: str, *, capfd: pytest.CaptureFixture[str]
    ) -> list[str]:
        """Share out and read the files back from the argument.

        Returns:
            The shard's files, unescaped, in order.

        """
        status, _out, err = self.share(mutate, shard, capfd=capfd)
        assert status == 0, err
        text = self.out.read_text(encoding="utf-8")
        return [re.sub(r"\\(.)", r"\1", f) for f in text.split(",") if f]


@fixture
def share_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: support.Env
) -> ShareOut:
    """Give each row its own ShareOut."""
    return ShareOut(tmp_path, monkeypatch, env=env)


def test_three_shards_hold_every_file_exactly_once(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
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
    shards = [share_out.shared(mutate, f"{k}/3", capfd=capfd) for k in range(3)]
    want = [every[0::3], every[1::3], every[2::3]]
    assert shards == want
    held = sorted(f for s in shards for f in s)
    assert held == every


def test_the_whole_share_prints_its_count(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """0/1 takes every file, and says how many of how many."""
    status, out, err = share_out.share(["src/*.ts"], "0/1", capfd=capfd)
    assert (status, out, err) == (0, f"{LABEL}: shard 0/1: 2 of 2 files\n", "")
    assert share_out.out.read_text(encoding="utf-8") == "src/a.ts,src/b.ts"


def test_entries_apply_in_order(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """An entry after a `!` adds back what the `!` took, as StrykerJS reads it."""
    mutate = ["src/*.ts", "!src/*.ts", "src/a.ts"]
    want = ["src/a.ts"]
    assert share_out.shared(mutate, "0/1", capfd=capfd) == want


def test_a_dotted_name_is_matched_only_by_a_dotted_part(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """Minimatch's `dot: false`: `**` never reaches `.hidden`; `.hidden` does."""
    assert "src/.hidden/h.ts" not in share_out.shared(
        ["src/**/*.ts"], "0/1", capfd=capfd
    )
    want = ["src/.hidden/h.ts"]
    assert share_out.shared(["src/.hidden/*.ts"], "0/1", capfd=capfd) == want


def test_a_file_name_with_glob_syntax_is_escaped(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """`[id]` is named literally, so minimatch matches that file alone."""
    share_out.share(["src/\\[id\\]/*.ts"], "0/1", capfd=capfd)
    assert share_out.out.read_text(encoding="utf-8") == r"src/\[id\]/page.ts"


def test_a_ranged_entry_is_red_by_name(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """A range, even under `!`, cannot be shared out by file."""
    status, out, err = share_out.share(
        ["src/*.ts", "!src/a.ts:1:2-3:4"], "0/1", capfd=capfd
    )
    want = (
        f"{LABEL}: `mutate` entry '!src/a.ts:1:2-3:4' carries a mutation"
        " range; a range cannot be shared out by file\n"
    )
    assert (status, out, err) == (1, "", want)


def test_an_extglob_is_red_by_name(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """`full_match` cannot spell an extglob, so it is refused, not misread."""
    status, out, err = share_out.share(["src/+(a|b).ts"], "0/1", capfd=capfd)
    want = (
        f"{LABEL}: `mutate` entry 'src/+(a|b).ts' is an extglob, which the"
        " leg cannot expand\n"
    )
    assert (status, out, err) == (1, "", want)


def test_a_list_matching_nothing_is_red(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """No tracked file matched: red, never an empty run."""
    status, out, err = share_out.share(["untracked.ts"], "0/1", capfd=capfd)
    want = f"{LABEL}: `mutate` matches no tracked file\n"
    assert (status, out, err) == (1, "", want)


def test_a_name_with_a_comma_is_red(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str], env: support.Env
) -> None:
    """--mutate splits on commas, so such a file cannot be named."""
    (share_out.tree / "src/x,y.ts").write_text(SOURCE, encoding="utf-8")
    subprocess.run([GIT, "add", "--", "src/x,y.ts"], env=env(), check=True)
    status, out, err = share_out.share(["src/x*.ts"], "0/1", capfd=capfd)
    want = (
        f"{LABEL}: src/x,y.ts: StrykerJS's --mutate cannot name this file"
        " (a comma, or a trailing range)\n"
    )
    assert (status, out, err) == (1, "", want)


def test_a_class_negated_with_a_caret_is_read_as_minimatch_reads_it(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """`[^a]` is every one character but `a`, as `[!a]` is."""
    want = ["src/b.ts"]
    assert share_out.shared(["src/[^a].ts"], "0/1", capfd=capfd) == want


def test_entries_resolve_against_the_tree(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """`./`, a path back into the tree and an absolute one; outside is none."""
    mutate = [
        "./src/a.ts",
        "../tree/src/b.ts",
        f"{share_out.tree.resolve().as_posix()}/lib/*.ts",
        "../elsewhere/*.ts",
    ]
    want = ["lib/l.ts", "src/a.ts", "src/b.ts"]
    assert share_out.shared(mutate, "0/1", capfd=capfd) == want


def test_a_shard_with_files_writes_no_counts(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """Only an empty share writes its row; a run's row is the verdict's."""
    summary = share_out.out.parent / "summary"
    config = share_out.tree / "stryker.config.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    argv = ["share", str(config), "--out", str(share_out.out)]
    status, _out, err = call([*argv, "--summary-dir", str(summary)], capfd=capfd)
    assert status == 0, err
    assert not summary.exists()


def test_a_name_ending_in_a_range_is_red(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str], env: support.Env
) -> None:
    """--mutate would read the name's tail as a range: red, by name."""
    (share_out.tree / "src/x:1-2").write_text(SOURCE, encoding="utf-8")
    subprocess.run([GIT, "add", "--", "src/x:1-2"], env=env(), check=True)
    status, out, err = share_out.share(["src/x*"], "0/1", capfd=capfd)
    want = (
        f"{LABEL}: src/x:1-2: StrykerJS's --mutate cannot name this file"
        " (a comma, or a trailing range)\n"
    )
    assert (status, out, err) == (1, "", want)


@pytest.mark.parametrize("shard", ["x", "1/2/3", "2/2", "3/2", "-1/2"])
def test_each_malformed_shard_is_red(
    share_out: ShareOut, shard: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """Not two whole numbers, or k not below n: red, by name."""
    status, out, err = share_out.share(["src/*.ts"], shard, capfd=capfd)
    want = f"{LABEL}: MUTANTS_SHARD='{shard}' is not k/n with 0 <= k < n\n"
    assert (status, out, err) == (1, "", want)


def test_a_config_with_no_mutate_list_is_red(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """`share` reads the list itself; a missing one is named."""
    config = share_out.tree / "stryker.config.json"
    bare: dict[str, object] = {"testRunner": "vitest"}
    config.write_text(json.dumps(bare), encoding="utf-8")
    status, out, err = call(
        ["share", str(config), "--out", str(share_out.out)], capfd=capfd
    )
    want = f"{LABEL}: {config} names no `mutate` list\n"
    assert (status, out, err) == (1, "", want)


def test_an_unreadable_config_is_red(
    share_out: ShareOut, capfd: pytest.CaptureFixture[str]
) -> None:
    """No file: red, never an empty share."""
    status, _out, err = call(
        ["share", str(share_out.tree / "none.json"), "--out", str(share_out.out)],
        capfd=capfd,
    )
    assert status == 1
    assert err.startswith(f"{LABEL}: "), err


# minimatch's braces, expanded before `full_match` reads a pattern.
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


@pytest.mark.parametrize(("pattern", "want"), ROWS)
def test_each_form(pattern: object, want: object) -> None:
    """Lists, nested lists, ranges, literal braces and escaped ones."""
    assert mutants_typescript.braces(pattern) == want


# minimatch's glob, spelled for `PurePosixPath.full_match`.
SPELLING_ROWS: tuple[tuple[str, str], ...] = (
    ("src/\\[id\\]/*.ts", "src/[[]id]/*.ts"),
    ("a\\*b\\?c", "a[*]b[?]c"),
    ("a\\!b", "a!b"),
    ("[^a].ts", "[!a].ts"),
    ("[a^].ts", "[a^].ts"),
    ("a\\", "a\\"),
)


@pytest.mark.parametrize(("pattern", "want"), SPELLING_ROWS)
def test_spelling_each_form(pattern: object, want: object) -> None:
    """An escape names its character; a `[^` class negates."""
    assert mutants_typescript.full_match_spelling(pattern) == want


REFUSALS_ROWS: tuple[tuple[dict[str, object], str], ...] = (
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
        ("names no `mutate` list — the paths to mutate are the repository's to state"),
    ),
)


@pytest.mark.parametrize(("setting", "message"), REFUSALS_ROWS)
def test_each_refused_setting_is_red_by_name(
    planted_state: Planted, setting: object, message: object
) -> None:
    """Each row's setting is named, and StrykerJS never runs."""
    planted_state.plant(*KILLED, config=CONFIG | setting)
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        f"{LABEL}: stryker.config.json {message}",
        (f"{LABEL}: record each equivalent mutant at its site instead: {MARK}"),
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_incremental_is_refused_as_deferred(planted_state: Planted) -> None:
    """`incremental: true` is red by name, with the reason it is deferred."""
    planted_state.plant(*KILLED, config=CONFIG | {"incremental": True})
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: stryker.config.json sets `incremental` — a runner has no"
            " earlier results, so it would read as on and do nothing; the leg"
            " runs every mutant every week (deferred, #1358)"
        )
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_a_config_that_is_not_json_is_red(planted_state: Planted) -> None:
    """An unreadable config is red, never a skip."""
    planted_state.track("stryker.config.json", "{ mutate: [")
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    assert done.stderr.startswith(
        f"{LABEL}: stryker.config.json is not readable JSON: "
    ), done.stderr
    assert log == list[str]()


@pytest.mark.usefixtures("planted_state")
def test_a_config_that_is_not_an_object_is_red() -> None:
    """A JSON list is no config."""
    want = ["is not a JSON object"]
    assert mutants_typescript.refusals([]) == want


def test_clean_run_is_green_with_the_summary(planted_state: Planted) -> None:
    """Killed and timed out: exit 0, the summary line, the counts, the call."""
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    want = [
        SHARED,
        (
            f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
            " 0 not viable, 0 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want
    assert done.stderr == ""
    want = [CALL]
    assert log == want
    assert planted_state.counts() == "0/1\t2\t2\t0\t0\tshard\n"
    assert not (planted_state.tree / "reports/mutation/mutation.json").exists()


def test_one_survivor_is_red_by_file_and_line(planted_state: Planted) -> None:
    """A survivor is named file:line:column, mutator, change."""
    planted_state.plant(*KILLED, mutant("Survived", 7))
    done, _ = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        SHARED,
        f"{LABEL}: survived: src/add.ts:7:54: ArithmeticOperator: a - b",
        (
            f"{LABEL}: 3 mutants, 2 killed, 1 survived, 0 not covered,"
            " 0 not viable, 0 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want
    want = [RED_TAIL]
    assert done.stderr.splitlines() == want
    assert planted_state.counts() == "0/1\t3\t2\t1\t0\tshard\n"


def test_a_mutant_no_test_reaches_is_not_covered_and_red(
    planted_state: Planted,
) -> None:
    """NoCoverage is a survivor of its own name, counted as survived."""
    planted_state.plant(
        *KILLED, mutant("NoCoverage", 9, mutator="BlockStatement", replacement="{}")
    )
    done, _ = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        SHARED,
        f"{LABEL}: not covered: src/add.ts:9:54: BlockStatement: {{}}",
        (
            f"{LABEL}: 3 mutants, 2 killed, 0 survived, 1 not covered,"
            " 0 not viable, 0 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want
    assert planted_state.counts() == "0/1\t3\t2\t1\t0\tshard\n"


def test_an_excluded_survivor_with_a_reason_is_green_and_counted(
    planted_state: Planted,
) -> None:
    """A site comment with its reason: excluded, counted, green."""
    planted_state.plant(
        *KILLED, mutant("Ignored", 4, reason="zero is either sign here")
    )
    done, _ = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    want = [
        SHARED,
        (
            f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
            " 0 not viable, 1 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want
    assert planted_state.counts() == "0/1\t3\t2\t0\t1\tshard\n"


def test_an_exclusion_without_its_reason_is_red(planted_state: Planted) -> None:
    """StrykerJS's default reason is no reason: red, by site."""
    planted_state.plant(*KILLED, mutant("Ignored", 4, reason="Ignored using a comment"))
    done, _ = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: excluded without a reason: src/add.ts:4:54:"
            " ArithmeticOperator: a - b"
        ),
        RED_TAIL,
    ]
    assert done.stderr.splitlines() == want


def test_a_compile_error_is_not_viable_never_killed(planted_state: Planted) -> None:
    """CompileError is counted on its own; green, killed unchanged."""
    planted_state.plant(*KILLED, mutant("CompileError", 5))
    done, _ = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    want = [
        SHARED,
        (
            f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
            " 1 not viable, 0 excluded"
        ),
    ]
    assert done.stdout.splitlines() == want


def test_a_runtime_error_is_not_judged_and_writes_no_counts(
    planted_state: Planted,
) -> None:
    """RuntimeError was not judged: red, and the totals get nothing."""
    planted_state.plant(*KILLED, mutant("RuntimeError", 6))
    done, _ = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: not judged: RuntimeError: src/add.ts:6:54:"
            " ArithmeticOperator: a - b"
        ),
        RED_TAIL,
    ]
    assert done.stderr.splitlines() == want
    assert not (planted_state.summary / "typescript.tsv").exists()


def test_an_empty_report_is_not_judged(planted_state: Planted) -> None:
    """A report of no mutant judged nothing."""
    planted_state.plant()
    done, _ = planted_state.run_leg()
    assert done.returncode == 1
    assert (
        f"{LABEL}: not judged: the report holds no mutant — nothing was mutated"
        in done.stderr.splitlines()
    )


def test_a_failing_stryker_is_red_with_its_output(planted_state: Planted) -> None:
    """A non-zero exit judged nothing: red, with StrykerJS's own words."""
    planted_state.plant(*KILLED)
    done, _ = planted_state.run_leg(
        PLANT_STATUS="3", PLANT_OUTPUT="no TestRunner plugins"
    )
    assert done.returncode == 1
    want = [
        f"{LABEL}: StrykerJS exited 3 — nothing judged; its output:",
        "    no TestRunner plugins",
    ]
    assert done.stderr.splitlines() == want


def test_no_report_is_red(planted_state: Planted) -> None:
    """A zero exit with no report is red; a stale report is not read."""
    planted_state.plant(*KILLED)
    stale = planted_state.tree / "reports/mutation/mutation.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps(report(*KILLED)), encoding="utf-8")
    planted_state.planted.write_text("", encoding="utf-8")
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [f"{LABEL}: StrykerJS wrote no reports/mutation/mutation.json"]
    assert done.stderr.splitlines() == want
    want = [CALL]
    assert log == want


def test_a_dirty_tree_is_refused_before_the_run(planted_state: Planted) -> None:
    """Uncommitted tracked changes: red, and StrykerJS never runs."""
    planted_state.plant(*KILLED)
    (planted_state.tree / "src/add.ts").write_text("// edited\n", encoding="utf-8")
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        f"{LABEL}: uncommitted:  M src/add.ts",
        ("  the leg mutates tracked files in place; it runs on a clean tree only"),
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_a_tree_left_changed_is_red(planted_state: Planted) -> None:
    """A run that leaves a tracked file changed is red, whatever it judged."""
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg(
        PLANT_DIRTY=str(planted_state.tree / "src/add.ts")
    )
    assert done.returncode == 1
    want = [
        f"{LABEL}: left changed after the run:  M src/add.ts",
        (
            "  StrykerJS did not restore the tree it mutated in place; restore"
            " it with git"
        ),
    ]
    assert done.stderr.splitlines() == want
    want = [CALL]
    assert log == want


def test_untracked_files_do_not_block_the_run(planted_state: Planted) -> None:
    """An untracked .env is not the tree's: the run goes ahead, in place."""
    planted_state.plant(*KILLED)
    (planted_state.tree / ".env").write_text("SECRET=1\n", encoding="utf-8")
    done, log = planted_state.run_leg()
    assert done.returncode == 0, done.stderr
    want = [CALL]
    assert log == want


def test_a_tracked_report_is_refused(planted_state: Planted) -> None:
    """The leg replaces StrykerJS's report; it never deletes a tracked file."""
    planted_state.track("reports/mutation/mutation.json", "{}")
    planted_state.plant(*KILLED)
    done, log = planted_state.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: reports/mutation/mutation.json is tracked; it is"
            " StrykerJS's"
            " output, and the leg replaces it"
        )
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


FILE = 'files["a.ts"]'
EACH_FIELD_THE_VERDICT_READS_ROWS: tuple[tuple[object, str], ...] = (
    ({"files": {"a.ts": 3}}, f"{FILE} is not an object"),
    ({"files": {"a.ts": {"mutants": {}}}}, f"{FILE}.mutants is not a list"),
    (
        {"files": {"a.ts": {"mutants": [1]}}},
        f"{FILE}.mutants[0] is not an object",
    ),
    (
        {"files": {"a.ts": {"mutants": [{}]}}},
        f"{FILE}.mutants[0].status is not a string",
    ),
    (
        {"files": {"a.ts": {"mutants": [{"status": "Killed", "location": 1}]}}},
        f"{FILE}.mutants[0].location is not an object",
    ),
    (
        {"files": {"a.ts": {"mutants": [{"location": {"start": {"line": "1"}}}]}}},
        f"{FILE}.mutants[0].line is not an integer",
    ),
    (
        {"files": {"a.ts": {"mutants": [{"replacement": 1}]}}},
        f"{FILE}.mutants[0].replacement is not a string",
    ),
    (
        {"files": {"a.ts": {"mutants": [{"mutatorName": 1}]}}},
        f"{FILE}.mutants[0].mutatorName is not a string",
    ),
    (
        {"files": {"a.ts": {"mutants": [{"status": "Ignored", "statusReason": 1}]}}},
        f"{FILE}.mutants[0].statusReason is not a string",
    ),
)


# A report field of another shape is refused by name, never a crash.
@pytest.mark.parametrize(("value", "why"), EACH_FIELD_THE_VERDICT_READS_ROWS)
def test_each_field_the_verdict_reads(
    value: object, why: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """The refusal names the field, where it is, in the report."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "mutation.json")
        path.write_text(json.dumps(value), encoding="utf-8")
        status = mutants_typescript.judge(str(path), LABEL, "")
        err = capfd.readouterr().err
        assert status == 1
        assert err == f"{LABEL}: no readable report at {path}: {why}\n"


def call(argv: list[str], *, capfd: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Call the helper's `main` in this process (#1696).

    Returns:
        The status `main` returned and what it wrote to each stream.

    """
    status = mutants_typescript.main(["--label", LABEL, *argv])
    out, err = capfd.readouterr()
    return status, out, err


# `config` and `verdict` through main, in this process (#1696).
def write(name: str, value: object, *, tmp_path: Path) -> Path:
    """Write one JSON document into the row's scratch directory.

    Returns:
        Its path.

    """
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_a_sound_config_passes_silently(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """No refused setting: exit 0, nothing printed."""
    status, out, err = call(
        [
            "config",
            str(write("stryker.config.json", CONFIG, tmp_path=tmp_path)),
        ],
        capfd=capfd,
    )
    assert (status, out, err) == (0, "", "")


@pytest.mark.parametrize(("setting", "message"), REFUSALS_ROWS)
def test_main_each_refused_setting_is_red_by_name(
    setting: object,
    message: object,
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Each Refusals row's setting, named with the site mark."""
    path = write("stryker.config.json", CONFIG | setting, tmp_path=tmp_path)
    status, _out, err = call(["config", str(path)], capfd=capfd)
    assert status == 1
    want = [
        f"{LABEL}: {path} {message}",
        (f"{LABEL}: record each equivalent mutant at its site instead: {MARK}"),
    ]
    assert err.splitlines() == want


def test_main_incremental_is_refused_as_deferred(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`incremental: true`, with the reason it is deferred."""
    path = write(
        "stryker.config.json", CONFIG | {"incremental": True}, tmp_path=tmp_path
    )
    status, _out, err = call(["config", str(path)], capfd=capfd)
    assert status == 1
    assert err == (
        f"{LABEL}: {path} sets `incremental` — a runner has no earlier results,"
        " so it would read as on and do nothing; the leg runs every mutant every"
        " week (deferred, #1358)\n"
    )


def test_main_an_unreadable_config_is_red(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Not JSON: named, exit 1."""
    path = write("stryker.config.json", 0, tmp_path=tmp_path)
    path.write_text("{ mutate: [", encoding="utf-8")
    status, _out, err = call(["config", str(path)], capfd=capfd)
    assert status == 1
    assert err.startswith(f"{LABEL}: {path} is not readable JSON: "), err


def test_a_clean_verdict_writes_its_counts(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Killed and timed out: exit 0, the summary line, typescript.tsv."""
    path = write("mutation.json", report(*KILLED), tmp_path=tmp_path)
    summary = path.parent / "summary"
    status, out, err = call(
        ["verdict", str(path), "--summary-dir", str(summary)], capfd=capfd
    )
    assert (status, out, err) == (
        0,
        f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
        " 0 not viable, 0 excluded\n",
        "",
    )
    assert (summary / "typescript.tsv").read_text(
        encoding="utf-8"
    ) == "0/1\t2\t2\t0\t0\tshard\n"


def test_a_verdict_writes_the_shard_it_was_given(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The row names `--shard`, never a fixed 0/1 (#2153)."""
    path = write("mutation.json", report(*KILLED), tmp_path=tmp_path)
    summary = path.parent / "summary"
    status, _out, err = call(
        [
            "verdict",
            str(path),
            "--shard",
            "3/8",
            "--summary-dir",
            str(summary),
        ],
        capfd=capfd,
    )
    assert status == 0, err
    assert (summary / "typescript.tsv").read_text(
        encoding="utf-8"
    ) == "3/8\t2\t2\t0\t0\tshard\n"


def test_a_survivor_is_red_and_writes_its_counts(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A survivor is printed by site, and the red tail follows."""
    path = write("mutation.json", report(mutant("Survived", 3)), tmp_path=tmp_path)
    status, out, err = call(["verdict", str(path)], capfd=capfd)
    assert status == 1
    assert (
        out.splitlines()[0]
        == f"{LABEL}: survived: src/add.ts:3:54: ArithmeticOperator: a - b"
    )
    assert err == RED_TAIL + "\n"

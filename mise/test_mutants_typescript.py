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

import importlib.util
import json
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-typescript.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:mutants: typescript"
MARK = "// Stryker disable next-line <Mutator>: <reason>"
RED_TAIL = (
    f"{LABEL}: red — kill each survivor with an assertion, add a test for"
    f" each mutant no test reaches, or record an equivalent mutant at its"
    f" site: {MARK}"
)

_SPEC = importlib.util.spec_from_file_location(
    "mutants_typescript", BELT / "mutants-typescript.py"
)
mutants_typescript = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutants_typescript)

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

CONFIG = {
    "testRunner": "vitest",
    "plugins": ["@stryker-mutator/vitest-runner"],
    "mutate": ["src/**/*.ts"],
}
SOURCE = "export const add = (a: number, b: number): number => a + b;\n"
CALL = "stryker run stryker.config.json --inPlace --reporters json"


def mutant(
    status: str,
    line: int,
    *,
    mutator: str = "ArithmeticOperator",
    replacement: str = "a - b",
    reason: str | None = None,
) -> dict:
    """Spell one mutant as StrykerJS 10.1.1's JSON report does.

    Returns:
        The mutant object.

    """
    found = {
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


def report(*mutants: dict) -> dict:
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
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=hermetic.env(), check=True
        )

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

    def plant(self, *mutants: dict, config: object = None) -> None:
        """Adopt, install the stub, commit, and plant the report."""
        self.track("stryker.config.json", json.dumps(config or CONFIG))
        self.install()
        self.git("commit", "-q", "-m", "plant")
        self.planted.write_text(json.dumps(report(*mutants)), encoding="utf-8")

    def run_leg(self, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        """Run the leg in the planted tree.

        Returns:
            The finished process and the stub's call log.

        """
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
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
        self.assertEqual(
            done.stdout.splitlines(),
            [f"{LABEL}: no stryker.config.json tracked, skipped"],
        )
        self.assertEqual(done.stderr, "")
        self.assertEqual(log, [])
        self.assertEqual(self.counts(), "0/1\tskipped\n")

    def test_an_untracked_config_is_not_adoption(self) -> None:
        """A config on disk that git does not track adopts nothing."""
        (self.tree / "stryker.config.json").write_text(
            json.dumps(CONFIG), encoding="utf-8"
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("skipped", done.stdout)
        self.assertEqual(log, [])

    def test_a_config_in_another_form_is_red_never_skipped(self) -> None:
        """A tracked stryker.config.mjs is adoption the leg cannot read: red."""
        self.track("stryker.config.mjs", "export default {};\n")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                f"{LABEL}: a StrykerJS config the leg cannot read: stryker.config.mjs",
                "  the leg reads stryker.config.json only; move the config there",
            ],
        )
        self.assertEqual(log, [])

    def test_a_workstation_is_refused_by_name(self) -> None:
        """No CI: adopted, the leg refuses before anything runs."""
        self.plant(*KILLED)
        env = dict(self.env)
        del env["CI"]
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: CI is unset — mutants run in place with the tree's own"
                    " environment,"
                ),
                (
                    "  so this leg runs on a runner only (repo-mutants.yml), never a"
                    " workstation"
                ),
            ],
        )
        self.assertEqual(self.log.read_text(encoding="utf-8"), "")

    def test_stryker_missing_is_red_by_name(self) -> None:
        """Adopted with no StrykerJS installed: red, naming what to add."""
        self.track("stryker.config.json", json.dumps(CONFIG))
        done, _log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: stryker.config.json is tracked but"
                    " node_modules/.bin/stryker"
                    " is missing — add"
                ),
                (
                    "  @stryker-mutator/core and its runner plugin to devDependencies,"
                    " and install"
                ),
            ],
        )

    def test_a_shard_other_than_whole_is_red(self) -> None:
        """StrykerJS cannot shard: 1/2 is red before anything runs."""
        done, log = self.run_leg(MUTANTS_SHARD="1/2")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: MUTANTS_SHARD='1/2' — StrykerJS cannot shard; the"
                    " typescript"
                    " job runs one shard, 0/1"
                )
            ],
        )
        self.assertEqual(log, [])


class Refusals(Planted):
    """A config that excludes mutants with no reason at a site is red."""

    ROWS = (
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
                self.assertEqual(
                    done.stderr.splitlines(),
                    [
                        f"{LABEL}: stryker.config.json {message}",
                        (
                            f"{LABEL}: record each equivalent mutant at its site"
                            f" instead: {MARK}"
                        ),
                    ],
                )
                self.assertEqual(log, [])

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
        self.assertEqual(log, [])

    def test_a_config_that_is_not_an_object_is_red(self) -> None:
        """A JSON list is no config."""
        self.assertEqual(mutants_typescript.refusals([]), ["is not a JSON object"])


class Verdict(Planted):
    """Statuses, survivors, exclusions and the summary line."""

    def test_clean_run_is_green_with_the_summary(self) -> None:
        """Killed and timed out: exit 0, the summary line, the counts, the call."""
        self.plant(*KILLED)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                (
                    f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 not covered,"
                    " 0 not viable, 0 excluded"
                )
            ],
        )
        self.assertEqual(done.stderr, "")
        self.assertEqual(log, [CALL])
        self.assertEqual(self.counts(), "0/1\t2\t2\t0\t0\n")
        self.assertFalse((self.tree / "reports/mutation/mutation.json").exists())

    def test_one_survivor_is_red_by_file_and_line(self) -> None:
        """A survivor is named file:line:column, mutator, change."""
        self.plant(*KILLED, mutant("Survived", 7))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                f"{LABEL}: survived: src/add.ts:7:54: ArithmeticOperator: a - b",
                (
                    f"{LABEL}: 3 mutants, 2 killed, 1 survived, 0 not covered,"
                    " 0 not viable, 0 excluded"
                ),
            ],
        )
        self.assertEqual(done.stderr.splitlines(), [RED_TAIL])
        self.assertEqual(self.counts(), "0/1\t3\t2\t1\t0\n")

    def test_a_mutant_no_test_reaches_is_not_covered_and_red(self) -> None:
        """NoCoverage is a survivor of its own name, counted as survived."""
        self.plant(
            *KILLED, mutant("NoCoverage", 9, mutator="BlockStatement", replacement="{}")
        )
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                f"{LABEL}: not covered: src/add.ts:9:54: BlockStatement: {{}}",
                (
                    f"{LABEL}: 3 mutants, 2 killed, 0 survived, 1 not covered,"
                    " 0 not viable, 0 excluded"
                ),
            ],
        )
        self.assertEqual(self.counts(), "0/1\t3\t2\t1\t0\n")

    def test_an_excluded_survivor_with_a_reason_is_green_and_counted(self) -> None:
        """A site comment with its reason: excluded, counted, green."""
        self.plant(*KILLED, mutant("Ignored", 4, reason="zero is either sign here"))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                (
                    f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
                    " 0 not viable, 1 excluded"
                )
            ],
        )
        self.assertEqual(self.counts(), "0/1\t3\t2\t0\t1\n")

    def test_an_exclusion_without_its_reason_is_red(self) -> None:
        """StrykerJS's default reason is no reason: red, by site."""
        self.plant(*KILLED, mutant("Ignored", 4, reason="Ignored using a comment"))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: excluded without a reason: src/add.ts:4:54:"
                    " ArithmeticOperator: a - b"
                ),
                RED_TAIL,
            ],
        )

    def test_a_compile_error_is_not_viable_never_killed(self) -> None:
        """CompileError is counted on its own; green, killed unchanged."""
        self.plant(*KILLED, mutant("CompileError", 5))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                (
                    f"{LABEL}: 3 mutants, 2 killed, 0 survived, 0 not covered,"
                    " 1 not viable, 0 excluded"
                )
            ],
        )

    def test_a_runtime_error_is_not_judged_and_writes_no_counts(self) -> None:
        """RuntimeError was not judged: red, and the totals get nothing."""
        self.plant(*KILLED, mutant("RuntimeError", 6))
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: not judged: RuntimeError: src/add.ts:6:54:"
                    " ArithmeticOperator: a - b"
                ),
                RED_TAIL,
            ],
        )
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


class Run(Planted):
    """In place, on a clean tree, from the repository's own StrykerJS."""

    def test_a_failing_stryker_is_red_with_its_output(self) -> None:
        """A non-zero exit judged nothing: red, with StrykerJS's own words."""
        self.plant(*KILLED)
        done, _ = self.run_leg(PLANT_STATUS="3", PLANT_OUTPUT="no TestRunner plugins")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                f"{LABEL}: StrykerJS exited 3 — nothing judged; its output:",
                "    no TestRunner plugins",
            ],
        )

    def test_no_report_is_red(self) -> None:
        """A zero exit with no report is red; a stale report is not read."""
        self.plant(*KILLED)
        stale = self.tree / "reports/mutation/mutation.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps(report(*KILLED)), encoding="utf-8")
        self.planted.write_text("", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [f"{LABEL}: StrykerJS wrote no reports/mutation/mutation.json"],
        )
        self.assertEqual(log, [CALL])

    def test_a_dirty_tree_is_refused_before_the_run(self) -> None:
        """Uncommitted tracked changes: red, and StrykerJS never runs."""
        self.plant(*KILLED)
        (self.tree / "src/add.ts").write_text("// edited\n", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                f"{LABEL}: uncommitted:  M src/add.ts",
                (
                    "  the leg mutates tracked files in place; it runs on a clean tree"
                    " only"
                ),
            ],
        )
        self.assertEqual(log, [])

    def test_a_tree_left_changed_is_red(self) -> None:
        """A run that leaves a tracked file changed is red, whatever it judged."""
        self.plant(*KILLED)
        done, log = self.run_leg(PLANT_DIRTY=str(self.tree / "src/add.ts"))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                f"{LABEL}: left changed after the run:  M src/add.ts",
                (
                    "  StrykerJS did not restore the tree it mutated in place; restore"
                    " it with git"
                ),
            ],
        )
        self.assertEqual(log, [CALL])

    def test_untracked_files_do_not_block_the_run(self) -> None:
        """An untracked .env is not the tree's: the run goes ahead, in place."""
        self.plant(*KILLED)
        (self.tree / ".env").write_text("SECRET=1\n", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, [CALL])

    def test_a_tracked_report_is_refused(self) -> None:
        """The leg replaces StrykerJS's report; it never deletes a tracked file."""
        self.track("reports/mutation/mutation.json", "{}")
        self.plant(*KILLED)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: reports/mutation/mutation.json is tracked; it is"
                    " StrykerJS's"
                    " output, and the leg replaces it"
                )
            ],
        )
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The task contract, read from the belt's own config (#1355).

`mise run ci` is the gate, and the gate is deterministic and fast by
rule: nothing network-bound, nothing that repeats the suite once per
mutant. The contract in mise/config.toml's header says so in prose;
this holds the file to it. The closure of `ci` is computed the way mise
collects it — each `depends` entry, a `*` matching within one colon
group — over the tasks the belt defines, and no task it reaches may be
an `audit:*` task or run a mutation leg.

The `audit:mutants` half: every delivered leg, `mise/mutants-*.sh`, is
named by exactly one `leg <language>` line of the task's body, so a leg
file nobody runs cannot exist and a language is one added line (#1336's
shared shape); MUTANTS_LEG selects one leg for that language's own jobs
in repo-mutants.yml, and unset runs them all.

stdlib `unittest` and `tomllib`, the belt's one test idiom. Run through
the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
CONFIG = BELT / "config.toml"


def tasks() -> dict[str, dict]:
    """Read every task the belt defines.

    Returns:
        Task name to its table.

    """
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)["tasks"]


def named(entry: str | dict) -> str:
    """Spell one `depends` entry as a task name or pattern.

    Returns:
        The name, from the bare string or the table's `task` key.

    """
    return entry if isinstance(entry, str) else entry["task"]


def matches(pattern: str, defined: dict[str, dict]) -> list[str]:
    """Resolve a `depends` entry the way mise does.

    `*` matches within ONE colon group (the config header's caveat for
    `fix:*`), so `lint:*` collects `lint:rust` and never `lint:a:b`.

    Returns:
        The defined tasks the entry names.

    """
    if "*" not in pattern:
        return [pattern] if pattern in defined else []
    groups = pattern.count(":")
    return sorted(
        name
        for name in defined
        if name.count(":") == groups and fnmatchcase(name, pattern)
    )


def closure(root: str, defined: dict[str, dict]) -> set[str]:
    """Walk every task `root` reaches through `depends`.

    Returns:
        The reached task names, `root` included.

    """
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for entry in defined.get(name, {}).get("depends") or []:
            todo.extend(matches(named(entry), defined))
    return seen


class Gate(unittest.TestCase):
    """`ci` reaches no audit task and no mutation leg."""

    def test_ci_reaches_no_audit_task(self) -> None:
        """Network-bound and slow checks are the cron's, never the gate's."""
        defined = tasks()
        reached = closure("ci", defined)
        self.assertIn("lint:typos", reached, "the closure walk found no lint")
        self.assertEqual(sorted(n for n in reached if n.startswith("audit")), [])

    def test_ci_runs_no_mutation_leg(self) -> None:
        """No task the gate reaches names a leg or cargo-mutants."""
        defined = tasks()
        leg = re.compile(r"mutants-[a-z]+\.sh|cargo[ -]mutants")
        hits = sorted(
            name
            for name in closure("ci", defined)
            if leg.search(str(defined[name].get("run", "")))
        )
        self.assertEqual(hits, [])

    def test_the_walk_sees_a_planted_audit(self) -> None:
        """The walk is not vacuous: a planted depends on audit is found."""
        defined = tasks()
        defined["ci"] = {**defined["ci"], "depends": ["lint:*", "audit:mutants"]}
        self.assertIn("audit:mutants", closure("ci", defined))


LEG_LINE = re.compile(r"^leg ([a-z]+)$", re.MULTILINE)


def leg_files() -> list[str]:
    """Name the delivered legs, mise/mutants-<language>.sh.

    Returns:
        The languages, sorted.

    """
    return sorted(
        path.stem.removeprefix("mutants-") for path in BELT.glob("mutants-*.sh")
    )


class Mutants(unittest.TestCase):
    """Every delivered leg is one line of `audit:mutants`, run on its own."""

    def test_mutants_run_in_their_own_workflow(self) -> None:
        """repo-mutants.yml runs the task; repo-audit.yml never does.

        A survivor reds the mutation run and never the weekly audit, so
        the audit's result keeps meaning what it means (owner, #1355).
        """
        workflows = BELT.parent / ".github" / "workflows"
        run = re.compile(r"mise run audit:mutants\b")
        own = (workflows / "repo-mutants.yml").read_text(encoding="utf-8")
        audit = (workflows / "repo-audit.yml").read_text(encoding="utf-8")
        self.assertNotEqual(run.findall(own), [])
        self.assertEqual(run.findall(audit), [])
        stub = BELT.parent / "workflow-templates" / "mutants.yml"
        self.assertIn(
            "/.github/workflows/repo-mutants.yml@", stub.read_text(encoding="utf-8")
        )

    def test_each_language_has_its_own_jobs(self) -> None:
        """One `mutants-<language>` job per leg, selecting only that leg.

        Each language's jobs are its own tick, shard count and timeout,
        so a red names its language at the job level (owner, #1355).
        """
        own = (BELT.parent / ".github" / "workflows" / "repo-mutants.yml").read_text(
            encoding="utf-8"
        )
        for lang in leg_files():
            with self.subTest(lang=lang):
                self.assertIn(f"\n  mutants-{lang}:\n", own)
                self.assertIn(f"\n  mutants-{lang}-totals:\n", own)
                self.assertIn(f"MUTANTS_LEG: {lang}\n", own)
                self.assertIn(f'mutation-totals.sh "${{SUMMARIES}}" {lang}\n', own)

    def test_every_leg_is_one_line_of_the_task(self) -> None:
        """Each mise/mutants-<language>.sh is exactly one `leg` line.

        A leg file nobody runs cannot exist, nor a line naming a missing leg.
        """
        body = tasks()["audit:mutants"]["run"]
        named = LEG_LINE.findall(body)
        self.assertNotEqual(named, [])
        self.assertEqual(sorted(named), leg_files())
        self.assertEqual(len(named), len(set(named)))


class Selection(unittest.TestCase):
    """MUTANTS_LEG selects one leg; unset, every leg runs."""

    def setUp(self) -> None:
        """Plant a belt directory of stub legs that only say they ran."""
        self.belt = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.belt)
        for lang in leg_files():
            (self.belt / f"mutants-{lang}.sh").write_text(
                f'echo "ran {lang}"\n[[ -z ${{PLANT_RED:-}} ]]\n', encoding="utf-8"
            )

    def run_task(self, **env: str) -> subprocess.CompletedProcess:
        """Run audit:mutants' body against the stub legs.

        Returns:
            The finished process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", tasks()["audit:mutants"]["run"]],
            cwd=self.belt,
            env=hermetic.env(ORG_BELT_DIR=str(self.belt), **env),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_unset_runs_every_leg(self) -> None:
        """Locally, `mise run audit:mutants` runs every adopted leg."""
        done = self.run_task()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [f"ran {g}" for g in leg_files()])

    def test_named_leg_runs_alone(self) -> None:
        """A language's job runs only its own leg."""
        done = self.run_task(MUTANTS_LEG="rust")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["ran rust"])

    def test_red_leg_is_named(self) -> None:
        """A red leg reds the task, by language."""
        done = self.run_task(MUTANTS_LEG="rust", PLANT_RED="1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr.splitlines(), ["audit:mutants: red leg(s): rust"])

    def test_unknown_leg_is_red_by_name(self) -> None:
        """A typo never runs nothing and passes."""
        done = self.run_task(MUTANTS_LEG="cobol")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    "audit:mutants: MUTANTS_LEG='cobol' names no leg"
                    " (mise/mutants-<language>.sh)"
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()

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
named by exactly one `leg <language>` line of the task's delivered
body, `mise/mutants.sh` (extracted under #1544's ceiling), so a leg
file nobody runs cannot exist and a language is one added line (#1336's
shared shape); MUTANTS_LEG selects one leg for that language's own jobs
in repo-mutants.yml, and unset runs them all.

`test` once per gate (#1623 item 3): `ci` collects the repository's
`test` unless the coverage measurement already runs the whole task,
which `coverage-measure.sh runs-test` answers from the tree. The rows
run the selector, then the belt's real `ci` under a real mise in
planted trees (`mise run --dry-run`, which plans every task and runs
none): a python tree with a floor plans no `test`; a Go-shaped and a
Rust-shaped tree with a floor, and a python tree without one, plan
it; and an unreadable script or an absent belt keeps it, never an
error (#569).

stdlib `unittest` and `tomllib`, the belt's one test idiom. Run through
the gate as `mise run test`, which `ci` collects.
"""

import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)


BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
CONFIG = BELT / "config.toml"
MEASURE = BELT / "coverage-measure.sh"

# The PL/pgSQL leg applies wherever any tracked file carries the
# LANGUAGE clause, the canon included: the planted clause is built from
# two halves so this file never switches the leg on for the canon (the
# trap test_plpgsql_coverage.py's header names).
PLPGSQL = "plpg" + "sql"

# The step line mise prints for a planned `test` in a dry run.
TEST_STEP = re.compile(r"^\[test\] \$ ", re.MULTILINE)


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

    def test_ci_names_each_property_leg(self) -> None:
        """A belt test leg is named in `ci`, never collected (#1332, #1549).

        `test` is the repository's, so a belt-defined test reaches the
        gate only by a line of its own in `ci`'s depends.
        """
        direct = [named(entry) for entry in tasks()["ci"]["depends"]]
        for leg in ("test:python-properties", "test:typescript-properties"):
            with self.subTest(leg=leg):
                self.assertIn(leg, direct)

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
        self.assertEqual(
            tasks()["audit:mutants"]["run"], '"${ORG_BELT_DIR}/mutants.sh"'
        )
        body = (BELT / "mutants.sh").read_text(encoding="utf-8")
        named = LEG_LINE.findall(body)
        self.assertNotEqual(named, [])
        self.assertEqual(sorted(named), leg_files())
        self.assertEqual(len(named), len(set(named)))


class Selection(unittest.TestCase):
    """MUTANTS_LEG selects one leg; unset, every leg runs."""

    def setUp(self) -> None:
        """Plant a belt of the real body and stub legs that only say they ran."""
        self.belt = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.belt)
        shutil.copy2(BELT / "mutants.sh", self.belt / "mutants.sh")
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


def plant(base: Path, name: str, *files: str, floor: bool = True) -> Path:
    """Plant a git tree holding `files`, a `test` task and maybe a floor.

    Returns:
        The tree.

    """
    tree = base / name
    tree.mkdir(parents=True)
    (tree / "mise.toml").write_text(
        '[tasks.test]\nrun = "echo planted test ran"\n', encoding="utf-8"
    )
    for rel in files:
        (tree / rel).write_text("", encoding="utf-8")
    if floor:
        (tree / ".coverage-floor").write_text("80.0\n", encoding="utf-8")
    env = hermetic.env()
    for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(argv, cwd=tree, env=env, check=True)
    return tree


class RunsTest(unittest.TestCase):
    """The selector: which legs run the whole `test` task (#1623 item 3)."""

    @staticmethod
    def answer(tree: Path) -> subprocess.CompletedProcess[str]:
        """Ask the selector in `tree`, with no belt Python and no mise.

        Returns:
            The finished selector.

        """
        # A `mise` on PATH that fails loudly: the selector must never ask
        # it (it runs while mise loads its config, and would recurse).
        stubs = tree.parent / f"{tree.name}-stubs"
        stubs.mkdir()
        (stubs / "mise").write_text("#!/bin/sh\necho asked mise >&2\nexit 9\n")
        (stubs / "mise").chmod(0o755)
        env = hermetic.env(ORG_PYTHON="")
        env.pop("ORG_PYTHON")
        env["PATH"] = f"{stubs}:{env['PATH']}"
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(MEASURE), "ci", "runs-test"],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_each_tree_gets_its_answer(self) -> None:
        """Python and plpgsql with a floor; nothing for the rest."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pg = plant(base, "pg", "schema.sql")
            (pg / "schema.sql").write_text(
                "CREATE FUNCTION f() RETURNS int LANGUAGE "
                + PLPGSQL
                + " AS $$ BEGIN RETURN 1; END $$;\n",
                encoding="utf-8",
            )
            for tree, want in (
                (plant(base, "py", "a.py"), "python\n"),
                (pg, "plpgsql\n"),
                (plant(base, "go", "go.mod"), ""),
                (plant(base, "rust", "Cargo.toml"), ""),
                (plant(base, "ts", "package.json"), ""),
                (plant(base, "py-no-floor", "a.py", floor=False), ""),
            ):
                with self.subTest(tree=tree.name):
                    done = self.answer(tree)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(done.stdout, want)
                    self.assertNotIn("asked mise", done.stderr)


class CiCollectsTest(unittest.TestCase):
    """The belt's real `ci`, planned by a real mise in planted trees."""

    def plan(self, tree: Path, belt: Path | None) -> subprocess.CompletedProcess[str]:
        """Dry-run `ci` in `tree`, the belt delivered from `belt`.

        `belt` None loads the belt's config as the tree's own and leaves
        no carrier, so ORG_BELT_DIR is empty: mise in a container without
        the belt (#569).

        Returns:
            The finished plan.

        """
        found = shutil.which("mise")
        self.assertIsNotNone(found, "mise is not on PATH")
        config = tree.parent / f"{tree.name}-config"
        config.mkdir()
        env = hermetic.env(
            MISE_CONFIG_DIR=str(config),
            XDG_CONFIG_HOME=str(config),
            MISE_TRUSTED_CONFIG_PATHS=f"{tree.parent}:{CONFIG.parent.parent}",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
        )
        if belt is None:
            text = CONFIG.read_text(encoding="utf-8")
            own = (tree / "mise.toml").read_text(encoding="utf-8")
            (tree / "mise.toml").write_text(text + "\n" + own, encoding="utf-8")
        else:
            env["MISE_GLOBAL_CONFIG_FILE"] = str(belt / "config.toml")
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [str(found), "run", "--dry-run", "ci"],
            cwd=tree,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )

    def planned(self, tree: Path, belt: Path | None = BELT) -> int:
        """Count the `test` steps `ci` plans in `tree`, asserting no error.

        Returns:
            How many times `ci` would run the planted `test`.

        """
        done = self.plan(tree, belt)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("mise ERROR", done.stdout + done.stderr)
        return len(TEST_STEP.findall(done.stdout + done.stderr))

    def test_a_python_tree_with_a_floor_runs_test_once(self) -> None:
        """`ci` plans no plain `test`: coverage:check's run is the one."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(self.planned(plant(base, "py", "a.py")), 0)
            # A path with a quote in it is interpolated into the exec's
            # shell: still answered, never a broken command.
            self.assertEqual(self.planned(plant(base, "it's py", "a.py")), 0)

    def test_go_rust_and_floorless_trees_keep_test(self) -> None:
        """Their measurement is not the repository's `test`, or there is none."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for tree in (
                plant(base, "go", "go.mod"),
                plant(base, "rust", "Cargo.toml"),
                plant(base, "py-no-floor", "a.py", floor=False),
            ):
                with self.subTest(tree=tree.name):
                    self.assertEqual(self.planned(tree), 1)

    def test_an_unreadable_or_absent_belt_keeps_test(self) -> None:
        """The selector unreadable, or no belt at all: `test`, never an error."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            belt = base / "belt"
            shutil.copytree(BELT, belt)
            (belt / "coverage-measure.sh").chmod(0)
            self.assertEqual(self.planned(plant(base, "py", "a.py"), belt), 1)
            self.assertEqual(self.planned(plant(base, "beltless", "a.py"), None), 1)


if __name__ == "__main__":
    unittest.main()

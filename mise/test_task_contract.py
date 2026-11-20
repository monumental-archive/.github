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

`test` once per gate (#1667): `ci` depends on the repository's `test`
plainly, and coverage:check depends on the same `test` and reads what
its run measured. The suite says it is measured by running its test
command through the belt's `measured`. The rows run the belt's real
`ci` and `coverage:*` tasks under a real mise in a planted python
repository whose suite runs from a `depends` with a `PYTHONPATH` of its
own and starts a bare `python3` child: one suite run, the task's path
kept, the child measured, the figures printed; the same tree with no
floor measures nothing; coverage:check alone runs `test` once; a suite
not run through `measured` is red by the floor's own record (#1666);
and `measured` reaches a file task.

stdlib `unittest` and `tomllib`, the belt's one test idiom. Run through
the gate as `mise run test`, which `ci` collects.
"""

import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
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

    def test_ci_names_each_property_leg(self) -> None:
        """A belt test leg is named in the gate, never collected (#1332, #1549).

        `test` is the repository's, so a belt-defined test reaches the
        gate only by a line of its own in a leg's depends: the
        properties leg's since #1740.
        """
        direct = [named(entry) for entry in tasks()["ci:properties"]["depends"]]
        for leg in ("test:python-properties", "test:typescript-properties"):
            with self.subTest(leg=leg):
                self.assertIn(leg, direct)

    def test_the_walk_sees_a_planted_audit(self) -> None:
        """The walk is not vacuous: a planted depends on audit is found."""
        defined = tasks()
        defined["ci"] = {**defined["ci"], "depends": ["lint:*", "audit:mutants"]}
        self.assertIn("audit:mutants", closure("ci", defined))


# The gate's three legs (#1740): ci.yml's `gate` job runs one per runner,
# and `ci` is their sum.
LEGS = ("ci:lint", "ci:test", "ci:properties")

# Everything `ci` named before it was split into legs, each entry spelt
# as `depends` spells it. The legs together must name exactly these.
GATE: tuple[str | dict, ...] = (
    "lint:*",
    {"task": "test", "optional": True},
    {"task": "build", "optional": True},
    "coverage:check",
    "test:pgrx",
    "test:python-properties",
    "test:typescript-properties",
)


def spelt(entry: str | dict) -> str:
    """Spell one `depends` entry whole, its optional flag included.

    Returns:
        The name, with ` (optional)` when the entry is optional.

    """
    if isinstance(entry, str):
        return entry
    return entry["task"] + (" (optional)" if entry.get("optional") else "")


def leg_findings(defined: dict[str, dict]) -> list[str]:
    """Hold `ci` to the sum of its legs, and the legs to the gate's list.

    Returns:
        One line per breach: `ci` naming anything but the three legs, an
        entry of the gate's list no leg names, an entry two legs name or
        one leg names twice, and an entry no gate ever ran.

    """
    out = []
    direct = [named(entry) for entry in defined["ci"].get("depends") or []]
    if direct != list(LEGS):
        out.append(f"ci depends on {direct}, not the three legs {list(LEGS)}")
    where: dict[str, list[str]] = {}
    for leg in LEGS:
        for entry in defined.get(leg, {}).get("depends") or []:
            where.setdefault(spelt(entry), []).append(leg)
    wanted = [spelt(entry) for entry in GATE]
    out.extend(f"no leg runs {name}" for name in wanted if name not in where)
    out.extend(
        f"{name} is run {len(legs)} times, by {legs}"
        for name, legs in where.items()
        if len(legs) > 1
    )
    out.extend(
        f"{name} is in {legs} and was never in the gate"
        for name, legs in where.items()
        if name not in wanted
    )
    return out


class Legs(unittest.TestCase):
    """`ci` is the sum of three legs, and they run what it ran (#1740)."""

    def test_the_belt_conforms(self) -> None:
        """The real config: three legs, the gate's list split among them."""
        self.assertEqual([], leg_findings(tasks()))

    def test_each_leg_holds_its_share(self) -> None:
        """Coverage is judged on the leg that measures it."""
        legs = {leg: [spelt(e) for e in tasks()[leg]["depends"]] for leg in LEGS}
        self.assertEqual(["lint:*"], legs["ci:lint"])
        self.assertEqual(
            [
                "test (optional)",
                "build (optional)",
                "coverage:check",
                "test:pgrx",
            ],
            legs["ci:test"],
        )
        self.assertEqual(
            ["test:python-properties", "test:typescript-properties"],
            legs["ci:properties"],
        )

    @staticmethod
    def plant(leg: str, depends: list[str | dict]) -> list[str]:
        """Replace one task's depends in the real config.

        Returns:
            The findings for the planted config.

        """
        defined = tasks()
        defined[leg] = {**defined[leg], "depends": depends}
        return leg_findings(defined)

    def test_a_fourth_line_in_ci_is_named(self) -> None:
        """Planted: `ci` names a task beside its legs."""
        self.assertEqual(
            [
                (
                    "ci depends on"
                    " ['ci:lint', 'ci:test', 'ci:properties', 'test:pgrx'],"
                    " not the three legs ['ci:lint', 'ci:test', 'ci:properties']"
                ),
            ],
            self.plant("ci", [*LEGS, "test:pgrx"]),
        )

    def test_a_dropped_entry_is_named(self) -> None:
        """Planted: the test leg loses the coverage ratchet."""
        self.assertEqual(
            ["no leg runs coverage:check"],
            self.plant(
                "ci:test",
                [
                    {"task": "test", "optional": True},
                    {"task": "build", "optional": True},
                    "test:pgrx",
                ],
            ),
        )

    def test_an_entry_in_two_legs_is_named(self) -> None:
        """Planted: the lint leg runs the properties too."""
        self.assertEqual(
            ["test:python-properties is run 2 times, by ['ci:lint', 'ci:properties']"],
            self.plant("ci:lint", ["lint:*", "test:python-properties"]),
        )

    def test_test_made_required_is_named(self) -> None:
        """Planted: `test` loses its optional flag, which every repo needs."""
        self.assertEqual(
            [
                "no leg runs test (optional)",
                "test is in ['ci:test'] and was never in the gate",
            ],
            self.plant(
                "ci:test",
                [
                    "test",
                    {"task": "build", "optional": True},
                    "coverage:check",
                    "test:pgrx",
                ],
            ),
        )

    def test_a_missing_leg_is_named(self) -> None:
        """Before #1740 there was no `ci:lint`: every lint is unrun."""
        defined = tasks()
        del defined["ci:lint"]
        self.assertEqual(["no leg runs lint:*"], leg_findings(defined))


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
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.belt = hermetic.belt(
            tmp / "belt",
            stubs={
                f"mutants-{lang}.sh": f'echo "ran {lang}"\n[[ -z ${{PLANT_RED:-}} ]]\n'
                for lang in leg_files()
            },
        )

    def run_task(self, **env: str) -> subprocess.CompletedProcess[str]:
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


# The planted repository's code: `half` takes one branch in the unittest
# process and the other in a bare `python3` child, which alone calls
# `child_only`, so 100% of statements and branches means the child was
# measured. The child imports `pkg` through the task's own
# PYTHONPATH=src, so it runs at all only where that entry was kept.
LIB = """\
def half(x):
    if x > 0:
        return x / 2
    return 0


def child_only():
    return "child"
"""
HELPER = """\
import sys

from pkg.lib import child_only, half

print(half(int(sys.argv[1])), child_only())
"""
SUITE = """\
import subprocess
import unittest

from pkg.lib import half


class Lib(unittest.TestCase):
    def test_in_process(self):
        self.assertEqual(2, half(4))

    def test_bare_child(self):
        out = subprocess.run(
            ["python3", "src/pkg/helper.py", "-1"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual("0 child\\n", out.stdout)
"""
# A floor the derived-state law accepts, recording the python legs.
FLOOR = (
    "# band: 2\n# measured: 52.0\n# derived: v1.0.0\n"
    "# legs: python python-branches\n50.0\n"
)
SUITE_COMMAND = "python3 -m unittest discover -s tests -t ."


def plant(base: Path, name: str, *, floor: bool = True, measured: bool = True) -> Path:
    """Plant a python repository whose suite runs from a `depends`.

    `test` depends on `test:unit`, which sets PYTHONPATH=src of its own,
    counts its runs beside the tree, and runs the suite, through
    `measured` unless `measured` is False. Every belt `lint:*` and
    `test:*` task is overridden by a no-op of the repository's own, so
    the belt's `ci` runs here with nothing but `test` and coverage:check
    doing work.

    Returns:
        The tree.

    """
    tree = base / name
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "src" / "pkg" / "lib.py").write_text(LIB, encoding="utf-8")
    (tree / "src" / "pkg" / "helper.py").write_text(HELPER, encoding="utf-8")
    (tree / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "tests" / "test_lib.py").write_text(SUITE, encoding="utf-8")
    command = (
        f'"${{ORG_BELT_DIR}}/measured" {SUITE_COMMAND}' if measured else SUITE_COMMAND
    )
    stubs = "".join(
        f'[tasks."{n}"]\nrun = "true"\n'
        for n in sorted(tasks())
        if n.startswith(("lint:", "test:"))
    )
    (tree / "mise.toml").write_text(
        '[task_config]\nshell = "bash -euo pipefail -c"\n'
        '[tasks.test]\ndepends = ["test:unit"]\nrun = "echo suite done"\n'
        '[tasks."test:unit"]\nenv = { PYTHONPATH = "src" }\n'
        f"run = '''\necho ran >> ../{name}.counter\n{command}\n'''\n" + stubs,
        encoding="utf-8",
    )
    if floor:
        (tree / ".coverage-floor").write_text(FLOOR, encoding="utf-8")
    env = hermetic.env()
    for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(argv, cwd=tree, env=env, check=True)
    return tree


def runs(tree: Path) -> int:
    """Count the planted suite's runs.

    Returns:
        How many times `test:unit` ran.

    """
    counter = tree.parent / f"{tree.name}.counter"
    return len(counter.read_text("utf-8").splitlines()) if counter.exists() else 0


class OneRun(unittest.TestCase):
    """The belt's real tasks, run by a real mise in planted trees (#1667)."""

    @staticmethod
    def run_mise(tree: Path, task: str) -> subprocess.CompletedProcess[str]:
        """Run `task` in `tree` with the belt as the global config.

        Returns:
            The finished run, its output in `stdout` and `stderr`.

        """
        found = hermetic.tool("mise")
        config = tree.parent / f"{tree.name}-config"
        config.mkdir()
        env = hermetic.env(
            MISE_CONFIG_DIR=str(config),
            XDG_CONFIG_HOME=str(config),
            MISE_GLOBAL_CONFIG_FILE=str(CONFIG),
            MISE_TRUSTED_CONFIG_PATHS=f"{tree.parent}:{CONFIG.parent.parent}",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [str(found), "run", task],
            cwd=tree,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )

    HELD = (
        "coverage:check: python leg holds the floor 50.0 (100.0%)",
        "coverage:check: python-branches leg holds the floor 50.0 (100.0%)",
    )

    def test_ci_runs_the_suite_once_and_measures_the_bare_child(self) -> None:
        """One run: the depend's own path kept, the child measured, the figures."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), "py")
            done = self.run_mise(tree, "ci")
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertEqual(1, runs(tree), out)
            for line in self.HELD:
                self.assertIn(line, out)
            self.assertIn("ci: all gates green", out)

    def test_no_floor_runs_test_once_and_measures_nothing(self) -> None:
        """Without a floor `measured` is plain exec and coverage:check skips."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), "py", floor=False)
            done = self.run_mise(tree, "ci")
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertEqual(1, runs(tree), out)
            self.assertIn("coverage:check: no .coverage-floor, skipped", out)
            self.assertNotIn("leg holds the floor", out)

    def test_coverage_check_alone_runs_test_once(self) -> None:
        """Run on its own, coverage:check runs `test` and holds the floor."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), "py")
            done = self.run_mise(tree, "coverage:check")
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertEqual(1, runs(tree), out)
            for line in self.HELD:
                self.assertIn(line, out)

    def test_a_suite_outside_measured_is_red_by_the_record(self) -> None:
        """Not through `measured`: the recorded legs are named, with the remedy."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), "py", measured=False)
            done = self.run_mise(tree, "coverage:check")
            out = done.stdout + done.stderr
            self.assertEqual(1, done.returncode, out)
            self.assertEqual(1, runs(tree), out)
            for leg in ("python", "python-branches"):
                self.assertIn(
                    f"coverage:check: the floor was derived with {leg} and this"
                    " run measured none\n"
                    "  the python leg needs tracked *.py and a `test` task whose"
                    ' test command runs through "${ORG_BELT_DIR}/measured"; or'
                    " record the leg's removal on purpose:"
                    f" `# dropped: {leg} <reason>` in .coverage-floor\n",
                    done.stderr,
                )

    def test_measured_reaches_a_file_task(self) -> None:
        """A file task with a `#!/bin/sh` shebang runs through `measured` too."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), "py")
            tasks_dir = tree / "mise-tasks"
            tasks_dir.mkdir()
            probe = tasks_dir / "probe"
            probe.write_text(
                '#!/bin/sh\nexec "$ORG_BELT_DIR/measured" sh -c'
                ' \'printf "%s\\n" "$COVERAGE_PROCESS_START"\'\n',
                encoding="utf-8",
            )
            probe.chmod(0o755)
            done = self.run_mise(tree, "probe")
            out = done.stdout + done.stderr
            self.assertEqual(0, done.returncode, out)
            self.assertRegex(done.stdout, r"/org-coverage-[0-9]+/coverage\.rc\n")


if __name__ == "__main__":
    unittest.main()

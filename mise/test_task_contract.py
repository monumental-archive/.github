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
named by exactly one line of the task's body, so a leg file nobody runs
cannot exist and a language is one added line (#1336's shared shape).

stdlib `unittest` and `tomllib`, the belt's one test idiom. Run through
the gate as `mise run test`, which `ci` collects.
"""

import re
import tomllib
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

BELT = Path(__file__).resolve().parent
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
        self.assertEqual(len(run.findall(own)), 1)
        self.assertEqual(run.findall(audit), [])
        stub = BELT.parent / "workflow-templates" / "mutants.yml"
        self.assertIn("/.github/workflows/repo-mutants.yml@", stub.read_text())

    def test_every_leg_is_one_line_of_the_task(self) -> None:
        """A mise/mutants-<language>.sh nobody runs cannot exist."""
        body = tasks()["audit:mutants"]["run"]
        legs = sorted(path.name for path in BELT.glob("mutants-*.sh"))
        self.assertNotEqual(legs, [])
        for leg in legs:
            lines = [line for line in body.splitlines() if leg in line]
            self.assertEqual(len(lines), 1, f"{leg} is named {len(lines)} times")

    def test_task_names_no_leg_that_does_not_exist(self) -> None:
        """A line naming a missing leg would red every adopter for nothing."""
        body = tasks()["audit:mutants"]["run"]
        for leg in re.findall(r"mutants-[a-z]+\.sh", body):
            self.assertTrue((BELT / leg).is_file(), leg)


if __name__ == "__main__":
    unittest.main()

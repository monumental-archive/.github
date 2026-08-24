#!/usr/bin/env python3
"""Table tests for lint:pg-upgrade-path's guard branches, both directions.

#825: the guard inferred installability from the derivation's past
behaviour. A version that is only ever an upgrade TARGET was called
"burned" — the Release PR committed its script and the publish then
failed, so nobody could install it — when the same filenames are equally
consistent with the version having published fine and the next release
simply not being derived from it. In that second case every installation
on it is stranded and the guard said the opposite, in green.

The graph planted below is the one the issue was measured on:

    1.2.2--1.2.3, 1.2.3--1.3.1, 1.2.3--1.3.2, current 1.3.2

so 1.3.1 is the target nothing was derived from — the open question. It
resolves from `.pgrx-installable`, the derived record the release path
writes (release/generate-pgrx-upgrade.sh), and BOTH directions are
planted here: 1.3.1 present in the record must red, absent must pass,
and neither message may assert a fact the record does not hold. The
no-record interim path is a branch in its own right and gets its own
rows, because a tree that has not been through a Release PR since #825
is the normal state of every repository the day this lands.

THE RECORD MAY ACCUSE, NEVER EXCUSE. A `from` version owes a path
whatever the record says about it — it is a `from` because a later
release was derived from it, which is the forge's own answer at that
time — so a row plants a `from` the record omits and requires the guard
to still red. A deleted asset must not be able to retire a real
obligation.

The trees are real: a control file, a crate `cargo pkgid` can resolve,
a tracked upgrade graph, and `git ls-files` answering for all of it,
because that is what the script reads. The lockfile is generated
`--offline` (the crates have no dependencies, so nothing is fetched).

GIT ENVIRONMENT IS SCRUBBED, deliberately — see #857. `git` reads
`GIT_DIR` and `GIT_INDEX_FILE` from the environment and applies them
regardless of `cwd`, and git EXPORTS `GIT_DIR` to hooks run in a linked
worktree. A harness that inherits the caller's environment therefore
writes its fixture into the caller's real index when the suite runs from
a pre-push hook, which is how three lanes lost their indexes on
2026-08-24. Every subprocess below is handed an environment with the
`GIT_*` family removed.

stdlib `unittest`, matching the other mise/test_*.py — #364 refused a
test framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "mise" / "pg-upgrade-path.sh"
RECORD = ".pgrx-installable"

# Absolute, resolved once: a partial path lets PATH decide which binary a
# test ran.
BASH = shutil.which("bash")
GIT = shutil.which("git")
CARGO = shutil.which("cargo")

# The graph the defect was measured on. 1.3.1 is the target nothing was
# derived from; 1.2.2 and 1.2.3 are `from` halves and always owe a path.
PLANTED = [("1.2.2", "1.2.3"), ("1.2.3", "1.3.1"), ("1.2.3", "1.3.2")]
CURRENT = "1.3.2"


def clean_env() -> dict[str, str]:
    """Build the caller's environment with git's own variables removed.

    Returns:
        A copy of os.environ carrying no GIT_* variable, so a `git` call
        below cannot be redirected at the caller's repository (#857).

    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def record_file(
    *lines: str,
    observed: str = "2026-08-24",
    derived: str = "v1.3.2",
) -> str:
    """Render a `.pgrx-installable` with the given extension lines.

    Returns:
        The whole file, provenance comments and all.

    """
    head = [
        "# pgrx installable versions -- DERIVED STATE (#825).",
        f"# observed: {observed}",
        f"# derived: {derived}",
    ]
    return "\n".join([*head, *lines]) + "\n"


class Tree:
    """A throwaway pgrx repository with a chosen graph and record."""

    def __init__(
        self,
        root: str,
        version: str = CURRENT,
        edges: list[tuple[str, str]] | None = None,
        record: str | None = None,
        name: str = "demo_pg",
    ) -> None:
        """Lay out the control file, the crate, the graph and the record."""
        self.root = Path(root)
        self.name = name
        # pgrx extensions are named with underscores and their crates with
        # hyphens; one argument, not two that can disagree.
        self.crate_dir = self.root / "crates" / name.replace("_", "-")
        (self.crate_dir / "sql").mkdir(parents=True)
        (self.crate_dir / "src").mkdir()
        (self.crate_dir / "src" / "lib.rs").write_text("pub fn f() {}\n")
        (self.crate_dir / f"{name}.control").write_text(
            "default_version = '@CARGO_VERSION@'\n"
        )
        (self.crate_dir / "Cargo.toml").write_text(
            textwrap.dedent(f"""\
                [package]
                name = "{name.replace("_", "-")}"
                version = "{version}"
                edition = "2021"
                """)
        )
        for frm, to in edges or []:
            (self.crate_dir / "sql" / f"{name}--{frm}--{to}.sql").write_text("")
        if record is not None:
            (self.root / RECORD).write_text(record)

        self.git("init", "-q", ".")
        self.git("add", "-A")

    def git(self, *args: str) -> None:
        """Run one git command against this tree, and nothing else (#857)."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args],
            cwd=self.root,
            env=clean_env(),
            check=True,
            capture_output=True,
        )

    def drop_control(self) -> None:
        """Make this an ordinary Rust repository, not a pgrx one."""
        (self.crate_dir / f"{self.name}.control").unlink()
        self.git("add", "-A")

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the real guard over this tree.

        Returns:
            The completed process; rows assert on stdout and the status.

        """
        env = clean_env()
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [
                CARGO,
                "generate-lockfile",
                "--offline",
                "--manifest-path",
                str(self.crate_dir / "Cargo.toml"),
            ],
            cwd=self.root,
            env=env,
            check=True,
            capture_output=True,
        )
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


@unittest.skipUnless(CARGO, "cargo resolves the crate version the guard reads")
class TheOpenQuestion(unittest.TestCase):
    """A target-only version: burned dead end, or stranded installation."""

    def test_a_recorded_installable_target_only_version_reds(self) -> None:
        """1.3.1 published: installations exist on it and nothing reaches."""
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.3.1 1.2.3 1.2.2"),
            ).lint()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "demo_pg 1.3.1 is a target nothing was derived from,", result.stdout
        )
        self.assertIn(
            "it published, so installations on it are stranded", result.stdout
        )
        self.assertIn(
            "no ALTER EXTENSION UPDATE path to 1.3.2 from: 1.3.1", result.stdout
        )
        self.assertIn("--<from>--<to>.sql to connect them", result.stdout)

    def test_a_target_only_version_absent_from_the_record_passes(self) -> None:
        """1.3.1 burned: no release carries it, so nothing can be on it."""
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            ).lint()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("nothing can be installed on it", result.stdout)
        self.assertIn("every installable demo_pg version reaches 1.3.2", result.stdout)

    def test_neither_outcome_asserts_what_the_record_does_not_hold(self) -> None:
        """The word the defect was filed about is gone from both branches."""
        with TemporaryDirectory() as d:
            passing = Tree(
                d,
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            ).lint()
        with TemporaryDirectory() as d:
            failing = Tree(
                d,
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.3.1 1.2.3 1.2.2"),
            ).lint()
        for result in (passing, failing):
            self.assertNotIn("burned", result.stdout)
        # The pass cites the record it relied on rather than asserting a
        # bare fact about the forge.
        self.assertIn("v1.3.2, observed 2026-08-24", passing.stdout)

    def test_the_record_may_accuse_but_never_excuse(self) -> None:
        """A `from` the record omits still owes a path to the current one."""
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                edges=[("1.0.0", "1.1.0"), *PLANTED],
                # 1.0.0 is a `from`, and deliberately absent here.
                record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            ).lint()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("1.0.0", result.stdout)


@unittest.skipUnless(CARGO, "cargo resolves the crate version the guard reads")
class TheInterimPath(unittest.TestCase):
    """No record: the question is open and the guard has to say so."""

    def test_no_record_states_the_question_instead_of_closing_it(self) -> None:
        """Green, but it does not pretend to know that 1.3.1 burned."""
        with TemporaryDirectory() as d:
            result = Tree(d, edges=PLANTED).lint()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("if it did, installations on it are stranded", result.stdout)
        self.assertNotIn("burned", result.stdout)

    def test_no_record_does_not_claim_every_published_version_reaches(
        self,
    ) -> None:
        """The success line is the one #825 was filed about. Bound it."""
        with TemporaryDirectory() as d:
            result = Tree(d, edges=PLANTED).lint()
        self.assertNotIn("every published demo_pg version", result.stdout)
        self.assertIn("was not checked", result.stdout)

    def test_a_record_naming_another_extension_is_the_interim_path(self) -> None:
        """A crate added since the last release was never asked about."""
        with TemporaryDirectory() as d:
            result = Tree(
                d, edges=PLANTED, record=record_file("other_ext 9.9.9")
            ).lint()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("if it did, installations on it are stranded", result.stdout)


@unittest.skipUnless(CARGO, "cargo resolves the crate version the guard reads")
class DerivedStateIsHeldToItsRecord(unittest.TestCase):
    """A record that cannot be trusted reds; it is never quietly repaired."""

    def test_a_record_without_provenance_is_refused(self) -> None:
        """Same law as .coverage-floor: derived state carries its derivation."""
        with TemporaryDirectory() as d:
            result = Tree(d, edges=PLANTED, record="demo_pg 1.3.2 1.2.3 1.2.2\n").lint()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("carries no", result.stdout)

    def test_a_record_naming_an_extension_twice_is_refused(self) -> None:
        """Two answers to one question is drift, not a merge to guess at."""
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                edges=PLANTED,
                record=record_file("demo_pg 1.2.3", "demo_pg 1.2.2"),
            ).lint()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("is named twice", result.stdout)


@unittest.skipUnless(CARGO, "cargo resolves the crate version the guard reads")
class StillSkipsAndStillCatches(unittest.TestCase):
    """The behaviour #825 must not have cost, in both directions."""

    def test_a_repo_with_no_control_file_skips_clean(self) -> None:
        """A linter that cannot skip cannot be universal."""
        with TemporaryDirectory() as d:
            tree = Tree(d, edges=PLANTED)
            tree.drop_control()
            result = tree.lint()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("no extension control files tracked", result.stdout)

    def test_an_empty_graph_is_a_first_publish_and_skips_clean(self) -> None:
        """No upgrade scripts means no published predecessor to strand."""
        with TemporaryDirectory() as d:
            result = Tree(d, version="1.0.0", edges=[]).lint()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("first publish has no predecessor", result.stdout)

    def test_a_release_that_derived_no_script_still_reds(self) -> None:
        """#816's own catch, kept: a bump with no new script strands all.

        The graph stops at 1.3.2 while the crate says 1.4.0, which is
        exactly what a burned predecessor produced in #816. Reachability
        fails for every version before the endpoint guard downstream of
        it is consulted, so that guard stays the backstop it has always
        been and this asserts what actually fires.
        """
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                version="1.4.0",
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            ).lint()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("no ALTER EXTENSION UPDATE path to 1.4.0", result.stdout)

    def test_only_the_record_sourced_accusation_is_explained(self) -> None:
        """A `from` is on the hook by the graph, and needs no record line.

        The explanation exists to carry information the reader cannot get
        from the filenames. Printing it for every stranded version buried
        the one line that did.
        """
        with TemporaryDirectory() as d:
            result = Tree(
                d,
                version="1.4.0",
                edges=PLANTED,
                record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            ).lint()
        # 1.3.2 is a target-only version the record accuses; 1.2.2 and
        # 1.2.3 are `from` halves and get no such line.
        self.assertIn(
            "demo_pg 1.3.2 is a target nothing was derived from,", result.stdout
        )
        self.assertNotIn("demo_pg 1.2.2 is a target", result.stdout)
        self.assertNotIn("demo_pg 1.2.3 is a target", result.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Table tests for `release/prepare-release.sh`'s tree decisions (#772).

Phase 1 step 1 decides nothing itself — every release decision is
`stele derive release-plan`'s (stele#155) — so what is testable here is
the INPUT CONTRACT the script hands the engine, and the two refusals the
script still owns: a plan that refuses, and a leftover `--next.sql` stub.

WHY THE CHANGELOG ROWS EXIST. The edtf import (#669) died twice on this
contract, both times on `main` after a merge:

  #742, first data point   `derive notes: reading CHANGELOG.md: open
                           CHANGELOG.md: no such file or directory` —
                           the repository arrived with six per-crate
                           changelogs and no root one
  #742, second data point  `derive notes: CHANGELOG.md already carries a
                           section for 1.3.0` — from a hand-invented
                           preamble whose h2 PROSE contained a version
                           string

NEITHER IS A CANON DEFECT, and the rows below are contract tests rather
than regression tests because of it: the deriver is right both times. A
version inside any `h2` IS that version's section, which is Keep a
Changelog's own convention, so the second message is correct and only
reads as a contradiction to someone who did not intend the heading as a
section. There is therefore no canon revision at which these two go red
— what was missing was a guard, not a fix, and the guard is
`lint:release-stub` plus a `scaffold/CHANGELOG.md`, which #847 landed
while this suite was being written.

So the positive control reads THE SCAFFOLD ITSELF rather than a
changelog invented here. That is #742's own "done when", measured
instead of argued: feeding the file the org tells every new repository
to copy, verbatim, to the deriver that killed edtf's first release, and
watching it report no existing section for any version. A synthetic
preamble would only prove the deriver agrees with my idea of one.

The third failure of that morning, the imported-tag-scheme misread
(#762/#766), IS a canon defect and is proven red on its pre-fix commit
in `test_generate_pgrx_upgrade.py`.

WHAT IS ASSERTED. The real script, run in a throwaway repository, with
the real `stele` from the belt. Nothing is stubbed: every input is a
tree, and the outputs asserted are the plan document, the step outputs
and the refusal text — the three things a caller reads.

WHAT IT MAY NOT TOUCH. Every subprocess here takes `hermetic_env()`:
this harness shells out to `git`, and the script it drives shells out to
`git` and `stele` again under whatever it is handed. #857 records what
inheriting the caller's git environment cost three lanes in one batch.

stdlib `unittest`, matching mise/test_*.py — #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "release" / "prepare-release.sh"
# Absolute, resolved once: a partial path lets PATH decide which binary a
# test ran, which is not a thing a test should leave open.
BASH = shutil.which("bash")
GIT = shutil.which("git")
CARGO = shutil.which("cargo")
STELE = shutil.which("stele")

# The real stub every new repository is told to copy (#742, shipped by
# #847). Read from the tree rather than transcribed: a copy here would
# drift from the file under test the first time the scaffold changed,
# and drift silently, since both would still parse as no section.
SCAFFOLD = (CANON / "scaffold" / "CHANGELOG.md").read_text()

# The hand-invented preamble that produced the second failure. The h2 is
# prose, not a section heading — but it carries a version, and a version
# inside an h2 is that version's section.
PROSE_H2_CARRYING_A_VERSION = """\
# Changelog

## Everything before 1.3.0 lived in the per-crate changelogs

See the crate directories for the history of each published component.
"""

# The same rule stated the way a reader expects it, so the row above
# cannot be mistaken for a quirk of prose parsing.
REAL_SECTION = """\
# Changelog

## [1.3.0] - 2026-08-21

### Added

- something that already shipped
"""


# The one place every subprocess in this file gets its environment, and
# the same contract `test_generate_pgrx_upgrade.py` states for #857: drop
# every `GIT_*` but the editor, redirect HOME, and supply an identity.
#
# `cwd=` is no defence — `GIT_DIR` is an absolute override git obeys
# wherever it is standing — and git EXPORTS it to hooks in a linked
# worktree, so a `git push` from a session worktree ran a fixture's
# `git add -A` against the real index. This file needs it twice over: it
# shells out to git, and `prepare-release.sh` shells out to git and to
# `stele` for itself, under whatever environment it is handed.
#
# CARGO_HOME and RUSTUP_HOME are carried across the HOME redirect
# deliberately. The hazard is git config, not cargo's registry, and a
# fixture that had to re-fetch a toolchain under a fresh HOME would be
# neither hermetic nor offline.
GIT_ENV_KEPT = frozenset({"GIT_EDITOR"})
IDENTITY_NAME = "prepare-release harness"
IDENTITY_EMAIL = "harness@example.invalid"


# A row that asserts an input is REQUIRED only means something in an
# environment where the input is genuinely absent, and a GitHub runner
# supplies several of them ambiently — `GITHUB_REPOSITORY`, `GITHUB_SHA`,
# `RUNNER_TEMP`. Locally they are unset, so three rows here passed on a
# laptop and failed on the first CI run: the scripts read the runner's
# values and sailed past the guard being tested.
#
# So the forge ambient is dropped with git's, and every input a row wants
# is supplied explicitly through `overrides`. `GH_TOKEN` is named because
# it is the one required input whose name does not start with a scrubbed
# prefix.
FORGE_ENV_PREFIXES = ("GITHUB_", "RUNNER_")
FORGE_ENV_NAMES = frozenset({"GH_TOKEN"})


def _ambient(name: str) -> bool:
    """Report whether a variable is the caller's rather than a row's.

    Returns:
        True when the name belongs to git, the forge or the runner, and
        must therefore not reach a fixture by inheritance.

    """
    if name.startswith("GIT_"):
        return name not in GIT_ENV_KEPT
    return name.startswith(FORGE_ENV_PREFIXES) or name in FORGE_ENV_NAMES


def hermetic_env(home: Path, **overrides: str) -> dict[str, str]:
    """Build an environment the caller's git state cannot reach into.

    Returns:
        The caller's environment with git's, the forge's and the
        runner's variables dropped, HOME and a git identity set, cargo's
        own homes preserved, and any overrides applied last.

    """
    env = {name: value for name, value in os.environ.items() if not _ambient(name)}
    real_home = Path(os.environ.get("HOME", "~")).expanduser()
    env.update(
        HOME=str(home),
        CARGO_HOME=os.environ.get("CARGO_HOME", str(real_home / ".cargo")),
        RUSTUP_HOME=os.environ.get("RUSTUP_HOME", str(real_home / ".rustup")),
        GIT_EDITOR=env.get("GIT_EDITOR", "true"),
        GIT_AUTHOR_NAME=IDENTITY_NAME,
        GIT_AUTHOR_EMAIL=IDENTITY_EMAIL,
        GIT_COMMITTER_NAME=IDENTITY_NAME,
        GIT_COMMITTER_EMAIL=IDENTITY_EMAIL,
    )
    env.update(overrides)
    return env


class Tree:
    """A throwaway releasable repository with a chosen shape."""

    def __init__(self, root: str, changelog: str | None = SCAFFOLD) -> None:
        """Lay out a crate at 1.2.3, tag it, and add one releasable commit."""
        # The repository and the HOME its git runs under are siblings,
        # never nested: anything git writes for this fixture would
        # otherwise land inside the very tree it is about to stage.
        self.home = Path(root) / "home"
        self.home.mkdir()
        self.root = Path(root) / "repo"
        self.root.mkdir()
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "main.rs").write_text("fn main() {}\n")
        (self.root / "Cargo.toml").write_text(
            textwrap.dedent("""\
                [package]
                name = "demo"
                version = "1.2.3"
                edition = "2021"
                """)
        )
        if changelog is not None:
            (self.root / "CHANGELOG.md").write_text(changelog)
        self.git("init", "-q", ".")
        # A fixture answers for itself, not for whatever identity, signing
        # policy or hook path the machine running the suite happens to
        # carry: a global `core.hooksPath` would run the org's own
        # lefthook against these commits.
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("config", "commit.gpgSign", "false")
        self.git("config", "tag.gpgSign", "false")
        self.git("config", "core.hooksPath", str(self.root / ".githooks-none"))
        self.cargo("generate-lockfile", "--offline")
        self.commit("feat: initial")
        self.git("tag", "-a", "v1.2.3", "-m", "v1.2.3")
        (self.root / "src" / "lib.rs").write_text("pub fn helper() {}\n")
        self.commit("feat: add a helper")

    def git(self, *args: str) -> None:
        """Run one git command in the fixture, failing the test if it does."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [GIT, *args],
            cwd=self.root,
            env=hermetic_env(self.home),
            check=True,
            capture_output=True,
        )

    def cargo(self, *args: str) -> None:
        """Run one cargo command in the fixture."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [CARGO, *args],
            cwd=self.root,
            env=hermetic_env(self.home),
            check=True,
            capture_output=True,
        )

    def write(self, path: str, text: str) -> None:
        """Write one tracked file, creating its parents."""
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, subject: str) -> None:
        """Stage everything and commit it under the fixture's identity."""
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def add_fuzz_workspace(self) -> None:
        """Add the second cargo workspace `cargo-fuzz` convention creates."""
        self.write("fuzz/fuzz_targets/t.rs", "fn main() {}\n")
        self.write(
            "fuzz/Cargo.toml",
            textwrap.dedent("""\
                [package]
                name = "demo-fuzz"
                version = "0.0.0"
                edition = "2021"

                [dependencies]
                demo = { path = ".." }

                [[bin]]
                name = "t"
                path = "fuzz_targets/t.rs"
                """),
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [CARGO, "generate-lockfile", "--offline"],
            cwd=self.root / "fuzz",
            env=hermetic_env(self.home),
            check=True,
            capture_output=True,
        )
        self.commit("feat: add the fuzz workspace")

    def prepare(self) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        """Run the real phase-1 step and read back what a caller would.

        Returns:
            The completed process and the parsed step outputs. The plan
            document is left on disk at `.rt/release-plan.json` for rows
            that assert the contract handed to the executor.

        """
        runner_temp = self.root / ".rt"
        runner_temp.mkdir(exist_ok=True)
        outputs = self.root / ".outputs"
        outputs.write_text("")
        # ruff: ignore[subprocess-without-shell-equals-true]
        result = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=hermetic_env(
                self.home,
                GITHUB_REPOSITORY="monumental-archive/under-test",
                RUNNER_TEMP=str(runner_temp),
                GITHUB_OUTPUT=str(outputs),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        emitted = {}
        for line in outputs.read_text().splitlines():
            key, _, value = line.partition("=")
            emitted[key] = value
        return result, emitted

    def plan(self) -> dict:
        """Read the plan document the step handed its executor.

        Returns:
            The parsed plan, whose `commit.additions` is the file list
            the release commit will carry.

        """
        return json.loads((self.root / ".rt" / "release-plan.json").read_text())


@unittest.skipUnless(STELE, "stele is the belt's release engine and derives the plan")
@unittest.skipUnless(CARGO, "cargo refreshes the lockfiles the plan declares")
class TheChangelogContract(unittest.TestCase):
    """Decide, from a changelog's text, whether a version has a section.

    The four rows are the whole decision: the file is absent, the file
    names the version in an h2 two different ways, or it names no
    version at all.
    """

    def test_a_missing_changelog_refuses_and_names_the_file(self) -> None:
        """#742's first data point: edtf carried no root CHANGELOG.md."""
        with TemporaryDirectory() as d:
            result, emitted = Tree(d, changelog=None).prepare()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "reading CHANGELOG.md: open CHANGELOG.md: no such file or directory",
            result.stderr,
        )
        self.assertNotIn("release", emitted)

    def test_a_version_inside_any_h2_is_that_versions_section(self) -> None:
        """#742's second data point, and the deriver is RIGHT.

        The heading was meant as prose. It carries 1.3.0, the version
        being released, so it is 1.3.0's section — Keep a Changelog's own
        convention. The message reads as the opposite of the truth only
        because the author did not intend a section.
        """
        with TemporaryDirectory() as d:
            result, _ = Tree(d, changelog=PROSE_H2_CARRYING_A_VERSION).prepare()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("CHANGELOG.md already carries a section for 1.3.0", result.stderr)

    def test_a_conventional_section_for_the_version_refuses_identically(self) -> None:
        """One rule, not a quirk: `## [1.3.0]` gets the same refusal."""
        with TemporaryDirectory() as d:
            result, _ = Tree(d, changelog=REAL_SECTION).prepare()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("CHANGELOG.md already carries a section for 1.3.0", result.stderr)

    def test_the_scaffold_reports_no_section_for_any_version(self) -> None:
        """Measure #742's own "done when" on the real scaffold file.

        The file the org hands every new repository must feed the
        deriver cleanly: no heading in it may parse as a version's
        section, or the first release of every repository that copies it
        dies the way edtf's did. This is the row that makes the three
        refusals above mean something, and it reds if the scaffold ever
        grows a version-shaped heading.
        """
        with TemporaryDirectory() as d:
            tree = Tree(d, changelog=SCAFFOLD)
            result, emitted = tree.prepare()
            written = (tree.root / "CHANGELOG.md").read_text()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("true", emitted["release"])
        self.assertEqual("1.3.0", emitted["version"])
        self.assertEqual("1.2.3", emitted["current"])
        self.assertIn("1.3.0", written)
        self.assertIn("add a helper", written)


@unittest.skipUnless(STELE, "stele is the belt's release engine and derives the plan")
@unittest.skipUnless(CARGO, "cargo refreshes the lockfiles the plan declares")
class TheTreeDecisions(unittest.TestCase):
    """The decisions phase 1 step 1 still owns after stele took the rest."""

    def test_the_lockfiles_are_declared_to_the_plan_not_appended_after(self) -> None:
        """`--also` carries both cargo workspaces into the plan's file list.

        The commit's contents are one list with one author. A lockfile
        refreshed after the plan was made would be a second author, which
        is the shape #374 found four releases of drift in.
        """
        with TemporaryDirectory() as d:
            tree = Tree(d)
            tree.add_fuzz_workspace()
            result, _ = tree.prepare()
            additions = tree.plan()["commit"]["additions"]
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["CHANGELOG.md", "Cargo.lock", "Cargo.toml", "fuzz/Cargo.lock"],
            sorted(additions),
        )

    def test_a_silent_range_releases_nothing_and_says_so(self) -> None:
        """chore/ci/docs/style/test release nothing; the step is not a failure."""
        with TemporaryDirectory() as d:
            tree = Tree(d)
            tree.git("tag", "-a", "v1.3.0", "-m", "v1.3.0")
            tree.write("docs/notes.md", "notes\n")
            tree.commit("docs: write some notes")
            result, emitted = tree.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("nothing to release", result.stdout)
        self.assertEqual("false", emitted["release"])
        self.assertNotIn("version", emitted)

    def test_a_leftover_next_sql_stub_refuses_with_the_remedy(self) -> None:
        """Derived state written by hand is a refusal, never a rename (#132).

        The stub is checked AFTER the plan is derived, so this row also
        pins that a refusal at this point spends no version: the step
        fails before anything is emitted.
        """
        with TemporaryDirectory() as d:
            tree = Tree(d)
            tree.write("sql/demo--next.sql", "-- hand-authored\n")
            tree.commit("feat: leave a stub behind")
            result, emitted = tree.prepare()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("--next.sql stubs are retired", result.stderr)
        self.assertIn("sql/demo--next.sql", result.stderr)
        self.assertIn("sql/next-data.sql", result.stderr)
        self.assertNotIn("release", emitted)

    def test_an_untracked_next_sql_stub_is_not_a_refusal(self) -> None:
        """`git ls-files`, never a walker: an untracked file is not the tree.

        The belt's own convention, and the difference matters here — a
        scratch file in a maintainer's working copy must not be able to
        refuse a release.
        """
        with TemporaryDirectory() as d:
            tree = Tree(d)
            tree.write("sql/demo--next.sql", "-- never staged\n")
            result, emitted = tree.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("--next.sql stubs are retired", result.stderr)
        self.assertEqual("true", emitted["release"])


if __name__ == "__main__":
    unittest.main()

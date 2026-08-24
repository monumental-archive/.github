#!/usr/bin/env python3
"""Table tests for the pgrx upgrade derivation's predecessor selection.

#816: after edtf v1.3.0 was tagged and failed to publish, the next
release asked only whether the graph HEAD (1.3.0) had published tarballs,
found none, and announced "first publish of this extension" for an
extension with eleven published versions. It derived nothing and exited
0. Only edtf's repo-local upgrade-path lint noticed.

The three cases below are distinct and were being conflated:

  burned predecessor   the graph head is not installable, but an older
                       version is - derive from the older one
  mid-life adoption    a repo with real releases and no extension
                       tarballs at all - genuinely no predecessor
  true first publish   no upgrade graph - decided before any release
                       call is made

FIXTURES ARE REAL. `testdata/edtf-releases.json` and
`testdata/stele-releases.json` are `gh api repos/<r>/releases` output,
captured 2026-08-21, reduced to the fields the script reads
(tag_name, draft, assets[].name) and pretty-printed by the belt's own
formatter. No value is edited. A hand-written
listing would only prove the script agrees with my idea of one - the
edtf fixture carries the exact shape that caused the bug, a DRAFT v1.3.0
with zero assets beside a non-draft `edtf-postgres-v1.2.3` carrying
twenty. See [[fixture-is-not-evidence]]: canon v1.24.0 was burned by a
hand-written fixture that no real artifact matched.

WHAT IS ASSERTED. The script is driven for real, with a stub `gh` on
PATH that answers the release listing from a fixture and refuses
everything else. Selection happens before the first download, so these
tests assert the script's own stdout up to that point and deliberately
do NOT assert the exit status: past selection the script wants a
tarball, a container and a live Postgres, which is the publish path's
job to prove and not something a unit test should pretend to do.

WHAT IT MAY NOT TOUCH. Every subprocess here takes `hermetic_env()`,
because this harness shells out to `git` and the thing it drives shells
out to `git` again. #857 records what inheriting the caller's git
environment cost.

stdlib `unittest`, matching mise/test_*.py - #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "release" / "generate-pgrx-upgrade.sh"
TESTDATA = CANON / "release" / "testdata"
# Absolute, resolved once: a partial path lets PATH decide which binary a
# test ran, and the stub `gh` this file installs is proof PATH is in play.
BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")

GH_STUB = """#!/usr/bin/env bash
# Stub `gh` for the derivation tests: answers the release listing from a
# fixture and refuses everything else, so a test stops at the first step
# that wants real bytes.
set -uo pipefail
if [[ ${1:-} == api && ${2:-} == *release* ]]; then
  expr=""
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --jq ]]; then expr="$2"; fi
    shift
  done
  jq -r "${expr}" "${GH_FIXTURE}"
  exit 0
fi
echo "gh stub: refusing '$*' (the test stops here by design)" >&2
exit 1
"""

# The one place every subprocess in this file gets its environment (#857).
#
# git EXPORTS `GIT_DIR` to the hooks it runs in a LINKED worktree - it is
# absent in the main working tree, which is why instrumenting a hook in an
# ordinary clone looks exculpatory - and `pre-commit` exports
# `GIT_INDEX_FILE` there too. So a `git push` from a session worktree ran
# this harness pointed at the real repository: `git init` + `git add -A`
# staged the demo fixture into the shared index and marked every real file
# deleted, while the suite reported OK. Three lanes of the 2026-08-24
# batch were hit before anyone read a `git status`.
#
# `cwd=` is no defence: `GIT_DIR` is an absolute override git obeys
# wherever it is standing. Only an explicit `env=` is, and it belongs
# HERE rather than at the call sites, because one call site is not in
# this file at all - generate-pgrx-upgrade.sh runs `git ls-files
# '*.control'` for itself, under whatever `derive()` hands it, and no
# author editing this file would think to go looking for it.
#
# HOME is redirected with it so no global git config reaches a fixture,
# and an identity is set because a redirected HOME leaves git without one
# the moment a row commits - which the planted row below does.
GIT_ENV_KEPT = frozenset({"GIT_EDITOR"})
IDENTITY_NAME = "pgrx upgrade harness"
IDENTITY_EMAIL = "harness@example.invalid"


def hermetic_env(home: Path, **overrides: str) -> dict[str, str]:
    """Build an environment the caller's git state cannot reach into.

    Returns:
        The caller's environment with every `GIT_*` variable dropped but
        `GIT_EDITOR`, with HOME and a git identity set, and with any
        overrides applied last.

    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_") or name in GIT_ENV_KEPT
    }
    env.update(
        HOME=str(home),
        GIT_EDITOR=env.get("GIT_EDITOR", "true"),
        GIT_AUTHOR_NAME=IDENTITY_NAME,
        GIT_AUTHOR_EMAIL=IDENTITY_EMAIL,
        GIT_COMMITTER_NAME=IDENTITY_NAME,
        GIT_COMMITTER_EMAIL=IDENTITY_EMAIL,
    )
    env.update(overrides)
    return env


def git(args: list[str], cwd: Path, home: Path) -> str:
    """Run one `git`, blind to the caller's git environment.

    Returns:
        Its stdout. A wrapper rather than an `env=` argument repeated per
        call site, so that a call site added later cannot omit the scrub
        by not knowing it exists.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [GIT, *args],
        cwd=cwd,
        env=hermetic_env(home),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class Repo:
    """A throwaway pgrx repository with a chosen upgrade graph."""

    def __init__(
        self,
        root: str,
        name: str,
        crate: str,
        version: str,
        edges: list[tuple[str, str]],
    ) -> None:
        """Lay out a control file, an upgrade graph and the tool pins."""
        # The repository and the HOME its git runs under are siblings in
        # the caller's temp directory, never nested: anything git writes
        # for this fixture would otherwise land inside the very tree it is
        # about to stage.
        self.home = Path(root) / "home"
        self.home.mkdir()
        self.root = Path(root) / "repo"
        self.root.mkdir()
        self.name = name
        crate_dir = self.root / "crates" / crate
        (crate_dir / "sql").mkdir(parents=True)
        (crate_dir / f"{name}.control").write_text(
            "default_version = '@CARGO_VERSION@'\n"
        )
        (crate_dir / "Cargo.toml").write_text(
            textwrap.dedent(f"""\
                [package]
                name = "{crate}"
                version = "{version}"
                edition = "2021"
                """)
        )
        for frm, to in edges:
            (crate_dir / "sql" / f"{name}--{frm}--{to}.sql").write_text("")
        (self.root / "mise.toml").write_text(
            textwrap.dedent("""\
                [tools]
                rust = "1.97.1"
                "cargo:cargo-pgrx" = "0.19.2"
                """)
        )
        git(["init", "-q", "."], self.root, self.home)
        git(["add", "-A"], self.root, self.home)

    def derive(
        self, version: str, prev_manifest: str, fixture: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the real derivation with a stub forge.

        Returns:
            The completed process; callers assert on its stdout, since the
            script wants real bytes past the point these tests cover.

        """
        bindir = self.root / "stubbin"
        bindir.mkdir(exist_ok=True)
        gh = bindir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        # The script runs `git ls-files '*.control'` for itself, so the
        # scrub has to reach it too - a fixture built hermetically and then
        # inspected through the caller's `GIT_DIR` is the same defect one
        # process further down.
        env = hermetic_env(
            self.home,
            PATH=f"{bindir}:{os.environ['PATH']}",
            VERSION=version,
            PREV_MANIFEST=prev_manifest,
            # Not a credential: the script only asserts GH_TOKEN is set, and
            # every call that would use one is answered by the stub.
            GH_TOKEN="stub",  # ruff: ignore[hardcoded-password-func-arg]
            GITHUB_REPOSITORY="monumental-archive/under-test",
            GH_FIXTURE=str(TESTDATA / fixture),
        )
        env.pop("GITHUB_OUTPUT", None)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


# The eleven published edtf versions, plus the burned 1.3.0 the failed
# release left behind. Endpoints only; the bodies are irrelevant here.
EDTF_EDGES = [
    ("0.2.0", "1.0.2"),
    ("1.0.0", "1.0.2"),
    ("1.0.1", "1.0.2"),
    ("1.0.2", "1.1.0"),
    ("1.1.0", "1.1.1"),
    ("1.1.1", "1.1.2"),
    ("1.1.2", "1.2.0"),
    ("1.2.0", "1.2.1"),
    ("1.2.1", "1.2.2"),
    ("1.2.2", "1.2.3"),
    ("1.2.3", "1.3.0"),
]


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
class SelectsThePredecessor(unittest.TestCase):
    """The three cases the old code collapsed into one."""

    def test_burned_head_falls_back_to_the_newest_installable(self) -> None:
        """Select 1.2.3: #816 itself, where only the graph head is burned."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            out = repo.derive("1.3.1", "1.3.0", "edtf-releases.json").stdout
        self.assertIn("1.3.0 is in the upgrade graph but no non-draft release", out)
        self.assertIn("deriving edtf_postgres from 1.2.3", out)
        self.assertNotIn("first publish", out)

    def test_the_fixture_really_carries_the_shape_that_caused_it(self) -> None:
        """Guard the guard: the capture still says what the bug needed.

        A refreshed listing that lost the draft v1.3.0, or the tarballs on
        edtf-postgres-v1.2.3, would turn every test above into a tautology
        without failing. So assert the fixture's shape, not just its use.
        """
        data = json.loads((TESTDATA / "edtf-releases.json").read_text())
        by_tag = {r["tag_name"]: r for r in data}
        burned = by_tag["v1.3.0"]
        self.assertTrue(burned["draft"], "v1.3.0 must still be the burn draft")
        self.assertEqual([], burned["assets"], "v1.3.0 must carry nothing")
        carried = by_tag["edtf-postgres-v1.2.3"]["assets"]
        self.assertTrue(
            any(a["name"].startswith("edtf_postgres-1.2.3-pg") for a in carried),
            "the real predecessor must still carry its tarballs",
        )

    def test_mid_life_adoption_has_no_predecessor(self) -> None:
        """Real releases, no extension tarballs: first publish is correct."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.1.0", [("1.0.0", "1.0.1")])
            result = repo.derive("1.1.0", "1.0.1", "stele-releases.json")
        self.assertIn("no non-draft release carries any demo_pg tarball", result.stdout)
        self.assertIn("first publish", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_true_first_publish_never_asks_the_forge(self) -> None:
        """An empty graph is decided before any release call is made."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.0.0", [])
            result = repo.derive("1.0.0", "", "stele-releases.json")
        self.assertIn("no upgrade graph", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("gh stub", result.stderr)

    def test_a_graph_that_already_reaches_the_version_derives_nothing(self) -> None:
        """A re-run over an unchanged graph is a no-op, not a re-derivation."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.0.1", [("1.0.0", "1.0.1")])
            result = repo.derive("1.0.1", "1.0.0", "stele-releases.json")
        self.assertIn("already reaches 1.0.1", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)


# Set on the suite this row spawns, so the child declines to spawn one of
# its own. A marker rather than running one class by name: the row is
# about what a WHOLE suite run does to an index, and naming a class would
# quietly stop covering rows added to any other one.
INNER_RUN = "PGRX_HARNESS_INNER_RUN"


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
@unittest.skipIf(os.environ.get(INNER_RUN), "the inner run: this row spawned it")
class LeavesTheCallersGitAlone(unittest.TestCase):
    """#857, planted: the hazard itself, run for real on every gate."""

    def test_a_linked_worktrees_index_is_byte_identical_afterwards(self) -> None:
        """Run the whole suite the way a `pre-push` hook in a worktree does.

        A scratch repository, a linked worktree of it, and exactly the
        environment git hands a hook standing there: `GIT_DIR`, plus the
        `GIT_INDEX_FILE` that `pre-commit` adds and that on its own leaves
        an index referencing objects the repository does not hold
        (`fatal: unable to read <oid>`). Both are planted on the child
        only; this row's own git calls take the scrub, which is what keeps
        the plant from reaching the tree the batch is working in.

        Green has to mean two things, because a green suite WAS the defect
        (#857): the index is untouched, and the suite that left it
        untouched actually ran.
        """
        expected = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__]
        ).countTestCases()
        with TemporaryDirectory() as d:
            scratch = Path(d)
            home = scratch / "home"
            home.mkdir()
            main = scratch / "main"
            main.mkdir()
            (main / "tracked.txt").write_text("a real file, in a real repo\n")
            git(["init", "-q", "."], main, home)
            git(["add", "-A"], main, home)
            git(["commit", "-q", "-m", "seed"], main, home)
            linked = scratch / "linked"
            git(["worktree", "add", "-q", str(linked), "-b", "side"], main, home)
            gitdir = Path(
                git(["rev-parse", "--absolute-git-dir"], linked, home).strip()
            )
            index = gitdir / "index"

            before_digest = hashlib.sha256(index.read_bytes()).hexdigest()
            before_files = git(["ls-files"], linked, home)

            planted = dict(os.environ)
            planted.update({
                INNER_RUN: "1",
                "GIT_DIR": str(gitdir),
                "GIT_INDEX_FILE": str(index),
            })
            # ruff: ignore[subprocess-without-shell-equals-true]
            run = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "-v"],
                cwd=linked,
                env=planted,
                capture_output=True,
                text=True,
                check=False,
            )

            after_digest = hashlib.sha256(index.read_bytes()).hexdigest()
            after_files = git(["ls-files"], linked, home)
            status = git(["status", "--porcelain"], linked, home)

        self.assertEqual(0, run.returncode, run.stderr)
        self.assertIn(f"Ran {expected} tests", run.stderr)
        # Exactly one skip, this row declining to recurse. Any other skip
        # means the rows that shell out to git did not run, and an index
        # nothing touched would then prove nothing at all.
        self.assertIn("OK (skipped=1)", run.stderr)
        self.assertEqual(before_files, after_files, "the shared index lost files")
        self.assertEqual("", status, f"the linked worktree is dirty:\n{status}")
        self.assertEqual(before_digest, after_digest, "the shared index was rewritten")


RECORD = ".pgrx-installable"
LINT = CANON / "mise" / "pg-upgrade-path.sh"
CARGO = shutil.which("cargo")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def written_record(root: Path) -> str:
    """Read the record the derivation wrote into a fixture repository.

    Returns:
        The whole file, comments and all.

    """
    return (root / RECORD).read_text()


def entries(written: str) -> list[str]:
    """Take the record's data lines, dropping its comment header.

    Returns:
        One line per extension the forge was asked about.

    """
    return [line for line in written.splitlines() if not line.startswith("#")]


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
class WritesTheInstallableSet(unittest.TestCase):
    """#825: the forge walk is recorded, because the gate cannot repeat it.

    Selection asks which versions a non-draft release CARRIES. That is
    the same question `lint:pg-upgrade-path` needs to tell a burned dead
    end from a stranded installation and cannot ask, being deterministic
    and offline. So the answer for EVERY version in the graph is written
    down here, as derived state, and rides the release commit.
    """

    def test_it_names_the_versions_the_forge_carries_newest_first(self) -> None:
        """The real edtf listing carries three of its eleven graph versions."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            repo.derive("1.3.1", "1.3.0", "edtf-releases.json")
            written = written_record(repo.root)
        self.assertEqual(["edtf_postgres 1.2.3 1.1.2 1.1.0"], entries(written))

    def test_the_burned_head_is_absent_and_that_is_the_whole_point(self) -> None:
        """1.3.0 is in the graph, carried by nothing, and must not appear.

        This is the fact the lint could not otherwise have. Assert it
        directly rather than trusting the line above to imply it.
        """
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            repo.derive("1.3.1", "1.3.0", "edtf-releases.json")
            written = written_record(repo.root)
        self.assertNotIn("1.3.0", written)

    def test_it_carries_its_own_derivation(self) -> None:
        """Derived state states how it was derived, like `.coverage-floor`."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            repo.derive("1.3.1", "1.3.0", "edtf-releases.json")
            written = written_record(repo.root)
        self.assertIn("# derived: v1.3.1", written)
        observed = next(
            ln.split(":", 1)[1].strip()
            for ln in written.splitlines()
            if ln.startswith("# observed:")
        )
        self.assertRegex(observed, ISO_DATE)

    def test_an_extension_the_forge_carries_nothing_for_is_recorded_empty(
        self,
    ) -> None:
        """Mid-life adoption: asked, and the answer was none. Not silence."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.1.0", [("1.0.0", "1.0.1")])
            repo.derive("1.1.0", "1.0.1", "stele-releases.json")
            written = written_record(repo.root)
        self.assertEqual(["demo_pg"], entries(written))

    def test_it_rides_the_release_commit(self) -> None:
        """A record the Release PR does not carry never reaches the gate.

        `files=` feeds EXTRA_FILES in release.yml, so this is the whole
        delivery path. Run separately from `Repo.derive`, which drops
        GITHUB_OUTPUT on purpose; the `already reaches` branch settles
        the record and exits without wanting a container.
        """
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.0.1", [("1.0.0", "1.0.1")])
            repo.derive("1.0.1", "1.0.0", "stele-releases.json")
            out = repo.root / "gh-output"
            env = hermetic_env(
                repo.home,
                PATH=f"{repo.root / 'stubbin'}:{os.environ['PATH']}",
                VERSION="1.0.1",
                PREV_MANIFEST="1.0.0",
                GH_TOKEN="stub",  # ruff: ignore[hardcoded-password-func-arg]
                GITHUB_REPOSITORY="monumental-archive/under-test",
                GH_FIXTURE=str(TESTDATA / "stele-releases.json"),
                GITHUB_OUTPUT=str(out),
            )
            result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(SCRIPT)],
                cwd=repo.root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            emitted = out.read_text()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"files={RECORD}", emitted)

    @unittest.skipUnless(CARGO, "the lint resolves the crate version with cargo")
    def test_the_lint_reads_exactly_what_this_wrote(self) -> None:
        """End to end, across the two scripts that must agree on the name.

        The record's filename is written in `generate-pgrx-upgrade.sh`
        and read in `pg-upgrade-path.sh`. Nothing but this row would
        notice a rename on one side: the lint would simply find no
        record and take its interim path, in green, forever.
        """
        edges = [("1.1.0", "1.1.2"), ("1.1.2", "1.2.3")]
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.2.3", edges)
            written = repo.derive("1.2.3", "1.1.2", "edtf-releases.json")
            self.assertIn("already reaches 1.2.3", written.stdout)

            crate = repo.root / "crates" / "edtf-postgres"
            (crate / "src").mkdir()
            (crate / "src" / "lib.rs").write_text("pub fn f() {}\n")
            # Rust's two homes, threaded explicitly. `hermetic_env`
            # redirects HOME to keep the caller's git config out of a
            # fixture, and that hides `~/.cargo` and `~/.rustup` with
            # it — on a rustup-managed machine the `cargo` on PATH is a
            # shim, and it exits 1 with "could not choose a version of
            # cargo to run" rather than anything about HOME.
            env = hermetic_env(
                repo.home,
                CARGO_HOME=os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
                RUSTUP_HOME=os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
            )
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [
                    CARGO,
                    "generate-lockfile",
                    "--offline",
                    "--manifest-path",
                    str(crate / "Cargo.toml"),
                ],
                cwd=repo.root,
                env=env,
                check=True,
                capture_output=True,
            )
            linted = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(LINT)],
                cwd=repo.root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, linted.returncode, linted.stdout + linted.stderr)
        self.assertIn(
            "every installable edtf_postgres version reaches 1.2.3", linted.stdout
        )
        self.assertIn("installable set recorded v1.2.3", linted.stdout)


if __name__ == "__main__":
    unittest.main()

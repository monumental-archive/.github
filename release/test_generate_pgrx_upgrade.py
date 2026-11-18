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
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().parents[1] / "mise" / "hermetic.py"))
)


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
#
# `gh api ... --paginate --jq` applies the filter to the COMBINED pages,
# not per page — measured against release-lab's 59 releases, where
# `--jq length` answers 59 rather than 30 then 29. So one jq over the
# whole fixture array is what the real call does, and a fixture may span
# more than one page without the stub having to pretend to paginate.
#
# `gh release view --json tagName` is answered too, because that is what
# the derivation asked BEFORE #766: the latest release, which for a real
# `gh` excludes drafts. Without it the pre-fix script could only fail by
# having its forge call refused, which proves nothing about the tag test
# that was the actual defect.
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
if [[ ${1:-} == release && ${2:-} == view ]]; then
  expr=""
  want=""
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jq) expr="$2"; shift ;;
      --json | --repo) shift ;;
      -*) ;;
      *) want="$1" ;;
    esac
    shift
  done
  # `gh release view` with no tag answers the LATEST release, which
  # excludes drafts; with a tag it answers that one. The fields come back
  # camelCased, unlike the REST listing the fixture is, so the stub
  # renames them rather than letting a test pass against a shape the real
  # command never returns.
  if [[ -n ${want} ]]; then
    pick=$(jq --arg t "${want}" '[.[] | select(.tag_name == $t)]' "${GH_FIXTURE}")
  else
    pick=$(jq '[.[] | select(.draft | not)]' "${GH_FIXTURE}")
  fi
  one=$(jq 'first | {tagName: .tag_name, isDraft: .draft, assets}' <<< "${pick}")
  [[ ${one} != null ]] || exit 1
  jq -r "${expr:-.}" <<< "${one}"
  exit 0
fi
# `gh release download` is answered only when a row hands the stub a
# previous archive to serve (GH_PREV_TARBALL), and then only by writing
# that archive to --output: the rows that read the archive's layout need
# the script to get past the download, and no further than the next
# container, which the stub `docker` refuses.
if [[ ${1:-} == release && ${2:-} == download && -n ${GH_PREV_TARBALL:-} ]]; then
  out=""
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --output ]]; then out="$2"; fi
    shift
  done
  cp "${GH_PREV_TARBALL}" "${out}"
  exit 0
fi
echo "gh stub: refusing '$*' (the test stops here by design)" >&2
exit 1
"""

DOCKER_STUB = """#!/usr/bin/env bash
echo "docker stub: refusing '$*' (the test stops here by design)" >&2
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
IDENTITY_NAME = "pgrx upgrade harness"
IDENTITY_EMAIL = "harness@example.invalid"


def hermetic_env(home: Path, **overrides: str) -> dict[str, str]:
    """Build a fixture's environment through mise/hermetic.py (#1508, #1514).

    Returns:
        hermetic.env for `home`, with this harness's git identity, and
        any overrides applied last.

    """
    stated = {
        "GIT_AUTHOR_NAME": IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
    }
    return hermetic.env(home, **(stated | overrides))


def mise_data_dir() -> str:
    """Name the caller's mise data directory, resolved as mise does.

    Returns:
        `MISE_DATA_DIR` if set, else `$XDG_DATA_HOME/mise`, else
        `~/.local/share/mise` - read from the caller's environment, so
        that a fixture whose HOME is redirected still finds the tools
        the caller's belt installed.

    """
    if data_dir := os.environ.get("MISE_DATA_DIR"):
        return data_dir
    if xdg := os.environ.get("XDG_DATA_HOME"):
        return str(Path(xdg) / "mise")
    return str(Path.home() / ".local" / "share" / "mise")


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
        self,
        version: str,
        prev_manifest: str,
        fixture: str,
        prev_tarball: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the real derivation with a stub forge.

        `prev_tarball`, when given, is the archive the stub forge serves
        for the predecessor's download, and a stub `docker` refusing by
        name ends the run at the candidate build that follows.

        Returns:
            The completed process; callers assert on its stdout, since the
            script wants real bytes past the point these tests cover.

        """
        bindir = self.root / "stubbin"
        bindir.mkdir(exist_ok=True)
        stubs = [("gh", GH_STUB)]
        if prev_tarball is not None:
            stubs.append(("docker", DOCKER_STUB))
        for stub, body in stubs:
            (bindir / stub).write_text(body)
            (bindir / stub).chmod(0o755)
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
            GH_PREV_TARBALL=str(prev_tarball or ""),
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


class ReadsThePinsThroughTheOneReader(unittest.TestCase):
    """The pins come from release/caller-pins.py, as the build leg's do (#1333)."""

    def test_an_unpinned_cargo_pgrx_is_refused_with_its_remedy(self) -> None:
        """The reader names the tool, the script keeps its own FAIL line."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            (repo.root / "mise.toml").write_text('[tools]\nrust = "1.97.1"\n')
            result = repo.derive("1.3.1", "1.3.0", "edtf-releases.json")
        self.assertEqual(1, result.returncode)
        self.assertIn(
            "::error::cargo-pgrx missing — pin cargo-pgrx in this repository's"
            " mise config; it is a build input of the pgrx-extension class\n"
            "FAIL: rust and cargo:cargo-pgrx must be pinned in this"
            " repository's mise config\n",
            result.stderr,
        )


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

            planted = hermetic.env()
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
        # Exactly one skip, this row declining to recurse, plus the floor
        # rows where this machine has no Postgres: they start a server
        # and never touch git. Any other skip means the rows that shell
        # out to git did not run, and an index nothing touched would then
        # prove nothing at all.
        self.assertIn(f"OK (skipped={expected_inner_skips()})", run.stderr)
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
            #
            # mise's data directory is under HOME too, and on a machine
            # where the `cargo` on PATH is a mise shim it is threaded the
            # same way. Without it, a caller that names a belt through
            # MISE_GLOBAL_CONFIG_FILE (a linked worktree must) sends the
            # shim looking for the belt's Rust pin in an empty data
            # directory: it starts an install and exits 1 (#1201).
            env = hermetic_env(
                repo.home,
                CARGO_HOME=os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
                RUSTUP_HOME=os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
                MISE_DATA_DIR=mise_data_dir(),
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


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
class TagHistoryShapes(unittest.TestCase):
    """The three tag histories the predecessor search must tell apart (#762).

    Selection succeeds silently — the script prints no tag on the happy
    path — so the assertion is the FETCH it goes on to attempt, which the
    stub refuses by name on stderr. That is the decision itself rather
    than a proxy for it: a wrong tag would be a wrong download.
    """

    def test_always_released_as_v_star(self) -> None:
        """release-lab: predecessor tarballs live on a plain `v*` tag."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "lab_pg", "lab-pg", "0.29.0", [("0.27.0", "0.28.1")])
            result = repo.derive("0.29.0", "0.28.1", "release-lab-releases.json")
        self.assertIn("release download v0.28.1", result.stderr)
        self.assertIn("deriving lab_pg: 0.28.1 -> 0.29.0", result.stdout)
        self.assertNotIn("first publish", result.stdout)

    def test_imported_previous_release_under_another_scheme(self) -> None:
        """edtf: the same bytes sit on `edtf-postgres-v1.2.3` (#762).

        `v1.2.3` exists in edtf as a tag with no release object, so the
        tag test this replaced saw "not a v* release" and derived nothing
        for an extension with eleven published versions.
        """
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            result = repo.derive("1.3.1", "1.3.0", "edtf-releases.json")
        self.assertIn("release download edtf-postgres-v1.2.3", result.stderr)
        self.assertIn("deriving edtf_postgres: 1.2.3 -> 1.3.1", result.stdout)
        self.assertNotIn("first publish", result.stdout)

    def test_no_releases_at_all(self) -> None:
        """signer: the listing is empty, so nothing is installable.

        A non-empty graph with an empty listing is NOT the same input as
        an empty graph, and this is the row that says so: the script must
        reach the forge, be told there is nothing, and only then conclude
        first publish. It must not attempt a download.
        """
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.1.0", [("1.0.0", "1.0.1")])
            result = repo.derive("1.1.0", "1.0.1", "signer-releases.json")
        self.assertIn("no non-draft release carries any demo_pg tarball", result.stdout)
        self.assertIn("first publish", result.stdout)
        self.assertNotIn("release download", result.stderr)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_the_new_fixtures_carry_the_shapes_they_are_named_for(self) -> None:
        """Guard the guards: a refreshed capture must not go tautological.

        Same reasoning as the edtf fixture's own shape check — a listing
        that lost its `v*` tarballs, or a `signer` that acquired a
        release, would leave the rows above asserting nothing while
        staying green.
        """
        lab = json.loads((TESTDATA / "release-lab-releases.json").read_text())
        carriers = [
            r["tag_name"]
            for r in lab
            if not r["draft"]
            and any(a["name"].startswith("lab_pg-0.28.1-pg") for a in r["assets"])
        ]
        self.assertEqual(
            ["v0.28.1"], carriers, "the v*-scheme predecessor must still be v0.28.1"
        )
        self.assertEqual(
            [], json.loads((TESTDATA / "signer-releases.json").read_text())
        )
        edtf = json.loads((TESTDATA / "edtf-releases.json").read_text())
        self.assertNotIn(
            "v1.2.3",
            [r["tag_name"] for r in edtf],
            "edtf's imported scheme is the point: no v1.2.3 release object",
        )


def previous_archive(dest: Path, top: str, files: list[str]) -> Path:
    """Pack a predecessor tarball with these files under `top`.

    Returns:
        The archive's path. Packed the way the release packs (a staging
        directory archived by its top-level name), so the layout is the
        only thing a row varies.

    """
    stage = dest / "stage"
    for rel in files:
        path = stage / top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    archive = dest / "prev.tar.gz"
    # ruff: ignore[subprocess-without-shell-equals-true]
    subprocess.run(
        [shutil.which("tar"), "-czf", str(archive), "-C", str(stage), top],
        check=True,
    )
    return archive


PREV_FILES = [
    "usr/lib/postgresql/18/lib/lab_pg.so",
    "usr/share/postgresql/18/extension/lab_pg.control",
    "usr/share/postgresql/18/extension/lab_pg--0.28.1.sql",
]
NO_ROOT = (
    "FAIL: v0.28.1 tarball has no usr/lib/postgresql/18/lib under its package root"
)


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
class ReadsThePreviousArchive(unittest.TestCase):
    """Every top level the predecessor's archive may have (#780, #1274).

    #1274 packs the tarball from `pkgroot/` alone: the CloudNativePG
    mirror beside it is gone. The derivation must still find the
    package root and the previous schema, and reach the candidate
    build, which the stub `docker` refuses by name. A layout it cannot
    read stops before that with its own message.
    """

    @staticmethod
    def run_with(top: str, files: list[str]) -> subprocess.CompletedProcess:
        """Derive lab_pg 0.29.0 from a 0.28.1 archive of this shape.

        Returns:
            The completed process.

        """
        with TemporaryDirectory() as d:
            archive = previous_archive(Path(d), top, files)
            repo = Repo(d, "lab_pg", "lab-pg", "0.29.0", [("0.27.0", "0.28.1")])
            return repo.derive("0.29.0", "0.28.1", "release-lab-releases.json", archive)

    def test_pkgroot_and_nothing_else(self) -> None:
        """The #1274 tarball: `pkgroot/` is the archive's only top level."""
        result = self.run_with("pkgroot", PREV_FILES)
        self.assertIn("deriving lab_pg: 0.28.1 -> 0.29.0 (pg18)", result.stdout)
        self.assertIn("docker stub: refusing 'run --rm", result.stderr)
        self.assertNotIn("FAIL:", result.stderr)

    def test_an_imported_archive_packed_from_root(self) -> None:
        """An imported repository's tarball carries `./usr/...` directly."""
        result = self.run_with(".", PREV_FILES)
        self.assertIn("docker stub: refusing 'run --rm", result.stderr)
        self.assertNotIn("FAIL:", result.stderr)

    def test_neither_layout_is_refused_by_name(self) -> None:
        """A root holding neither shape stops before any container."""
        result = self.run_with("cnpgroot", ["lib/lab_pg.so"])
        self.assertIn(NO_ROOT, result.stderr)
        self.assertNotIn("docker stub", result.stderr)
        self.assertEqual(1, result.returncode)

    def test_no_previous_schema_is_refused_by_name(self) -> None:
        """A package root without the previous schema stops before a build."""
        result = self.run_with("pkgroot", PREV_FILES[:2])
        self.assertIn(
            "FAIL: v0.28.1 tarball carries no generated schema lab_pg--0.28.1.sql",
            result.stderr,
        )
        self.assertNotIn("docker stub", result.stderr)
        self.assertEqual(1, result.returncode)


WORKFLOW = CANON / ".github" / "workflows" / "build-pgrx-extension.yml"
# The build's root half and its floor template, out of the workflow
# since #1380: the tracked scripts the container runs.
BUILD_ROOT = CANON / "release" / "pgrx" / "build-root.sh"
FLOOR_SQL = CANON / "release" / "pgrx" / "floor.sql.in"
HEADER_PIN = '"postgresql-server-dev-${PG}=${server}"'
SERVER_UNCHANGED = '[[ ${after} == "${server}" ]] || {'
DPKG_STUB = """#!/usr/bin/env bash
# dpkg-query -W -f=<fmt> <pkg>: answer the server version on record.
cat "${STUB_STATE}"
"""
APT_CACHE_STUB = """#!/usr/bin/env bash
# apt-cache show <pkg>=<version>: served iff it is in the served list.
grep -qxF -- "$2" "${STUB_SERVED}"
"""
APT_GET_STUB = """#!/usr/bin/env bash
# apt-get install ...: record the arguments; an unpinned dependency
# drags the server to STUB_MOVE_TO when that is set.
printf '%s\\n' "$@" >> "${STUB_ARGS}"
if [[ -n ${STUB_MOVE_TO:-} ]]; then
  printf '%s' "${STUB_MOVE_TO}" > "${STUB_STATE}"
fi
"""


def header_install(text: str) -> list[str]:
    """Cut the header install out of a site, indentation and comments off.

    Returns:
        The lines from the server read to the close of the
        server-unchanged check. Extracted from the shipped bytes, so a
        site that drifts from the other, or loses the check, fails here
        rather than in a release.

    """
    lines = [line.strip() for line in text.splitlines()]
    start = next(
        i for i, line in enumerate(lines) if line.startswith("server=$(dpkg-query")
    )
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return [line for line in lines[start : end + 1] if not line.startswith("#")]


def run_header_install(
    served: list[str], move_to: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the shipped block against stub dpkg-query, apt-cache, apt-get.

    Returns:
        The completed process and the arguments apt-get received.

    """
    block = "\n".join(header_install(SCRIPT.read_text()))
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs = tmp / "bin"
        stubs.mkdir()
        for name, body in (
            ("dpkg-query", DPKG_STUB),
            ("apt-cache", APT_CACHE_STUB),
            ("apt-get", APT_GET_STUB),
        ):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        (tmp / "state").write_text("18.6-1.pgdg120+1")
        (tmp / "served").write_text("".join(f"{s}\n" for s in served))
        (tmp / "args").write_text("")
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "PG": "18",
            "BUILD_IMAGE": "postgres:18-bookworm@sha256:planted",
            "STUB_STATE": str(tmp / "state"),
            "STUB_SERVED": str(tmp / "served"),
            "STUB_ARGS": str(tmp / "args"),
            "STUB_MOVE_TO": move_to,
        }
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", block],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, (tmp / "args").read_text().splitlines()


class InstallsHeadersAtTheImagesServer(unittest.TestCase):
    """Headers at exactly the server the pinned image carries (#1157).

    November's minors: apt served 18.7 headers into an 18.6 image, and
    the library linked against `heap_datum_check`, which the image's
    server does not export. Both build sites install the same block.
    """

    def test_both_sites_pin_the_headers_and_check_the_server(self) -> None:
        """Each site names the pinned headers and the unchanged check."""
        for site in (SCRIPT, BUILD_ROOT):
            text = site.read_text()
            with self.subTest(site=site.name):
                block = "\n".join(header_install(text))
                self.assertIn(HEADER_PIN, block)
                self.assertIn(SERVER_UNCHANGED, block)
                self.assertNotIn('"postgresql-server-dev-${PG}" ', text)
        # The image the remedy names reaches each container.
        for site in (SCRIPT, WORKFLOW):
            with self.subTest(env=site.name):
                self.assertIn("--env BUILD_IMAGE", site.read_text())

    def test_the_two_sites_are_textually_identical(self) -> None:
        """They cannot share a script; they must not drift apart."""
        self.assertEqual(
            header_install(SCRIPT.read_text()),
            header_install(BUILD_ROOT.read_text()),
        )

    def test_installs_the_headers_at_the_server_version(self) -> None:
        """Every minor served, the image's own is the one installed."""
        result, args = run_header_install([
            "postgresql-server-dev-18=18.4-1.pgdg120+1",
            "postgresql-server-dev-18=18.6-1.pgdg120+1",
            "postgresql-server-dev-18=18.7-1.pgdg120+1",
        ])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("postgresql-server-dev-18=18.6-1.pgdg120+1", args)
        self.assertNotIn("postgresql-server-dev-18", args)

    def test_refuses_with_the_remedy_when_apt_dropped_the_headers(self) -> None:
        """The skew is a red with its cause and its fix in it."""
        result, args = run_header_install(["postgresql-server-dev-18=18.7-1.pgdg120+1"])
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::image postgres:18-bookworm@sha256:planted carries server"
            " 18.6-1.pgdg120+1; the apt repository no longer serves its"
            " headers — move the image pin in docker/pgrx-base-images.toml\n",
            result.stdout,
        )
        self.assertEqual([], args)

    def test_refuses_a_build_that_moved_the_server(self) -> None:
        """An install that upgrades the server is caught after the fact."""
        result, _ = run_header_install(
            ["postgresql-server-dev-18=18.6-1.pgdg120+1"],
            move_to="18.7-1.pgdg120+1",
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual(
            "::error::the build moved the server from 18.6-1.pgdg120+1"
            " to 18.7-1.pgdg120+1\n",
            result.stdout,
        )


def floor_template() -> str:
    """Read the floor block's template the build fills (#1274, #1380).

    Returns:
        release/pgrx/floor.sql.in as floor.sh reads it, `$(cat ...)`,
        trailing newline off: the shipped bytes, never a copy of them.

    """
    return FLOOR_SQL.read_text().rstrip("\n")


def fill(template: str, num: int, xy: str) -> str:
    """Fill the template the way the build does, field by field.

    Returns:
        The DO block a build at server `xy` (`num`) would prepend.

    """
    for field, value in (
        ("@NUM@", str(num)),
        ("@XY@", xy),
        ("@EXT@", FLOOR_EXT),
        ("@VERSION@", FLOOR_VERSION),
    ):
        template = template.replace(field, value)
    return template


def postgres_bindir() -> Path | None:
    """Find the gate's own Postgres: `PG_BINDIR`, else the Debian layout.

    The runner image carries PostgreSQL under /usr/lib/postgresql/<N>/bin
    (installed, service disabled), which is all these rows need: a server
    they start themselves on a private socket.

    Returns:
        The newest major's bin directory, or None where there is none.

    """
    if override := os.environ.get("PG_BINDIR"):
        return Path(override)
    found = sorted(
        Path("/usr/lib/postgresql").glob("*/bin/initdb"),
        key=lambda p: int(p.parent.parent.name),
    )
    return found[-1].parent if found else None


FLOOR_EXT = "demo_ext"
FLOOR_VERSION = "9.8.7"
PG_BIN = postgres_bindir()
# The gate's runner carries a Postgres, so there a missing one is a
# defect, never a skip that reads like a pass (#364). A machine without
# one skips by name.
ON_RUNNER = os.environ.get("GITHUB_ACTIONS") == "true"


class FloorTemplateShape(unittest.TestCase):
    """The template carries every field the build fills, and its tag."""

    def test_every_field_and_the_tag(self) -> None:
        """Each placeholder is there, and none survives the fill."""
        template = floor_template()
        for field in ("@NUM@", "@XY@", "@EXT@", "@VERSION@"):
            with self.subTest(field=field):
                self.assertIn(field, template)
        self.assertTrue(template.startswith("DO $pgrx_floor$\n"))
        self.assertTrue(template.endswith("\n$pgrx_floor$;"))
        self.assertNotIn("@", fill(template, 180007, "18.7"))


class LiveServer(unittest.TestCase):
    """A throwaway server from the gate's own Postgres, shared by the rows.

    Holds no rows itself, so the loader never starts it on its own.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Start a throwaway server on a private socket, no TCP.

        Raises:
            AssertionError: on the gate's runner with no Postgres found,
                where a skip would read like a pass.

        """
        if PG_BIN is None:
            msg = "the gate's runner carries no /usr/lib/postgresql/*/bin"
            raise AssertionError(msg)
        cls.tmp = TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.data = tmp / "data"
        cls.sock = tmp
        run = [str(PG_BIN / "initdb"), "-D", str(cls.data), "-A", "trust"]
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([*run, "-U", "postgres", "-N"], check=True, capture_output=True)
        opts = f"-c listen_addresses= -k {cls.sock}"
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [
                str(PG_BIN / "pg_ctl"),
                "-D",
                str(cls.data),
                "-o",
                opts,
                "-w",
                "-l",
                str(tmp / "log"),
                "start",
            ],
            check=True,
            capture_output=True,
        )
        cls.num = int(cls.psql("SHOW server_version_num").stdout.strip())
        cls.version = cls.psql("SHOW server_version").stdout.strip()

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop the server and drop its directory."""
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [str(PG_BIN / "pg_ctl"), "-D", str(cls.data), "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
        )
        cls.tmp.cleanup()

    @classmethod
    def psql(cls, sql: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one command string against the throwaway server.

        `args` replace the default `-c sql`: the snapshot row passes
        `-v ext=… -f <file>` as the proof does, since psql interpolates
        `:'ext'` only in a script it reads, never in `-c`.

        Returns:
            The completed process; stderr carries any WARNING.

        """
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [
                str(PG_BIN / "psql"),
                "-X",
                "-q",
                "-tA",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                str(cls.sock),
                "-U",
                "postgres",
                "-d",
                "postgres",
                *(args or ("-c", sql)),
            ],
            capture_output=True,
            text=True,
            check=False,
        )


@unittest.skipUnless(PG_BIN or ON_RUNNER, "no Postgres on this machine")
class WarnsBelowTheFloor(LiveServer):
    """The floor block alone, against a live server (#1274).

    The gate's own server stands in for the one a person installs on:
    a floor set above its version is the older-server case and must
    warn with the exact sentence; a floor at its version is the build's
    own server and must say nothing.
    """

    @staticmethod
    def block(num: int) -> str:
        """Fill the shipped template with a floor of `num`.

        Returns:
            The DO block, its X.Y derived from `num` as the build does.

        """
        xy = f"{num // 10000}.{num % 10000}"
        return fill(floor_template(), num, xy)

    def test_below_the_floor_warns_with_the_sentence(self) -> None:
        """A server one minor older than the build's warns, exactly."""
        floor = self.num + 1
        xy = f"{floor // 10000}.{floor % 10000}"
        result = self.psql(self.block(floor))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"WARNING:  {FLOOR_EXT} {FLOOR_VERSION} was built against PostgreSQL"
            f" {xy}; this server is {self.version}. If loading fails below,"
            f" update PostgreSQL to {xy} or newer.\n",
            result.stderr,
        )

    def test_at_the_floor_is_silent(self) -> None:
        """The build's own server prints nothing."""
        result = self.psql(self.block(self.num))
        self.assertEqual((0, ""), (result.returncode, result.stderr))

    def test_above_the_floor_is_silent(self) -> None:
        """A newer server than the build's prints nothing."""
        result = self.psql(self.block(self.num - 1))
        self.assertEqual((0, ""), (result.returncode, result.stderr))


def snapshot_sql(text: str) -> str:
    """Pull the shipped snapshot query out of the derivation script.

    Returns:
        The quoted heredoc's body: the exact text the proof feeds psql.

    Raises:
        AssertionError: when the script no longer writes that heredoc.

    """
    match = re.search(
        r"cat > \"\$\{work\}/snapshot\.sql\" << 'EOSQL'\n(.*?\n)EOSQL\n",
        text,
        re.DOTALL,
    )
    if match is None:
        msg = "no snapshot.sql heredoc in generate-pgrx-upgrade.sh"
        raise AssertionError(msg)
    return match.group(1)


@unittest.skipUnless(PG_BIN or ON_RUNNER, "no Postgres on this machine")
class SnapshotReadsTheCatalog(LiveServer):
    """The shipped snapshot query, against a live server (#1324).

    `plpgsql` is an extension every server carries, so its member rows
    are there to read without building anything. Until #1324 the query
    died at parse time on `"char" || unknown`, and the proof compared
    two empty files.
    """

    def test_plpgsql_members_are_read(self) -> None:
        """The query exits 0 and returns the extension's member rows."""
        with TemporaryDirectory() as tmp:
            sql = Path(tmp) / "snapshot.sql"
            sql.write_text(snapshot_sql(SCRIPT.read_text()), encoding="utf-8")
            result = self.psql("", "-v", "ext=plpgsql", "-f", str(sql))
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        rows = [r for r in result.stdout.splitlines() if r.startswith("(")]
        self.assertGreaterEqual(len(rows), 1, result.stdout)
        self.assertIn("(language,,plpgsql,plpgsql) | ", rows)


PSQL_ERROR = (
    'psql:/work/snapshot.sql:19: ERROR:  operator is not unique: "char" || unknown\n'
)

# Answers the proof's psql calls the way the real one does: a snapshot
# reads the row file for its database, `error` prints psql's own
# message and exits 3 only under ON_ERROR_STOP (0 without it, as the
# real psql does), and the count query on stdin answers STUB_COUNT.
PSQL_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${STUB_LOG}"
db='' file='' cmd='' stop=0
while (($#)); do
  case $1 in
    -d) db=$2; shift ;;
    -f) file=$2; shift ;;
    -c) cmd=$2; shift ;;
    -v) [[ $2 == ON_ERROR_STOP=1 ]] && stop=1; shift ;;
  esac
  shift
done
if [[ -n ${file} ]]; then
  rows="${STUB_ROWS}/${db}"
  if [[ $(< "${rows}") == error ]]; then
    printf '%s' "${STUB_ERROR}" >&2
    ((stop)) && exit 3
    exit 0
  fi
  cat "${rows}"
elif [[ ${cmd} == *extversion* ]]; then
  echo "${NEW}"
elif [[ -z ${cmd} ]]; then
  cat > /dev/null
  echo "${STUB_COUNT}"
fi
"""

# Runs the inner script the proof hands it, as the postgres user would.
SU_STUB = """#!/usr/bin/env bash
while (($#)) && [[ $1 != -c ]]; do shift; done
exec bash -c "$2"
"""

TRUE_STUB = "#!/usr/bin/env bash\nexit 0\n"

ROW_A = (
    '(function,public,,"public.a()") | CREATE OR REPLACE FUNCTION public.a()\n'
    " RETURNS void\n"
)
ROW_B = "(type,public,b,public.b) | b:-1:U:b_in:b_out\n"


def proof_sh(text: str) -> str:
    """Pull proof.sh out of the derivation script.

    Returns:
        The quoted heredoc's body, the script the build image runs.

    Raises:
        AssertionError: when the script no longer writes that heredoc.

    """
    match = re.search(
        r"cat > \"\$\{work\}/proof\.sh\" << 'EOSH'\n(.*?\n)EOSH\n",
        text,
        re.DOTALL,
    )
    if match is None:
        msg = "no proof.sh heredoc in generate-pgrx-upgrade.sh"
        raise AssertionError(msg)
    return match.group(1)


class ProofReadsBeforeItCompares(unittest.TestCase):
    """proof.sh with a stand-in psql, one row per outcome (#1324).

    The shipped script runs as written but for one rewrite: its
    container paths (/usr/lib/postgresql, /usr/share/postgresql, /tmp,
    /work) are moved under a scratch root, since the rows run on the
    host. `su`, `cp`, `createdb` and the server binaries stand in as
    no-ops; psql answers from the row's files.
    """

    @staticmethod
    def run_proof(
        upgraded: str, fresh: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the shipped proof.sh against the given snapshot answers.

        Returns:
            The completed run, and the argument line of every psql call.

        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stubs = root / "stubs"
            server = root / "usr" / "lib" / "postgresql" / "18" / "bin"
            rows = root / "rows"
            for d in (stubs, server, rows, root / "tmp", root / "work"):
                d.mkdir(parents=True)
            for path, body in (
                (stubs / "psql", PSQL_STUB),
                (stubs / "su", SU_STUB),
                (stubs / "cp", TRUE_STUB),
                (stubs / "createdb", TRUE_STUB),
                (server / "initdb", TRUE_STUB),
                (server / "pg_ctl", TRUE_STUB),
            ):
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            (rows / "upgraded").write_text(upgraded, encoding="utf-8")
            (rows / "fresh").write_text(fresh, encoding="utf-8")
            (root / "work" / "snapshot.sql").write_text(
                snapshot_sql(SCRIPT.read_text()), encoding="utf-8"
            )
            script = proof_sh(SCRIPT.read_text())
            # One pass, never a replace() per prefix: on Linux the scratch
            # root is itself under /tmp/, so a later replace() rewrote the
            # paths an earlier one produced (/tmp/x/tmp/x/usr/...) and no
            # stand-in was found. The Mac's /var/folders root hid it.
            container = re.compile(
                "|".join(
                    re.escape(prefix)
                    for prefix in (
                        "/usr/lib/postgresql/",
                        "/usr/share/postgresql/",
                        # The proof's own container path, moved OFF the
                        # host's /tmp: the opposite of the rule's concern.
                        # ruff: ignore[hardcoded-temp-file]
                        "/tmp/",
                        "/work/",
                    )
                )
            )
            script = container.sub(lambda m: f"{root}{m.group(0)}", script)
            log = root / "psql.log"
            env = hermetic_env(
                root,
                PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                PG="18",
                NAME="demo_ext",
                PREV="1.0.0",
                NEW="1.1.0",
                STUB_LOG=str(log),
                STUB_ROWS=str(rows),
                STUB_ERROR=PSQL_ERROR,
                STUB_COUNT="2",
            )
            # ruff: ignore[subprocess-without-shell-equals-true]
            result = subprocess.run(
                [BASH, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    def test_an_sql_error_stops_the_step_with_psqls_message(self) -> None:
        """The step stops with psql's own status and message."""
        result, _ = self.run_proof("error", ROW_A + ROW_B)
        self.assertEqual(3, result.returncode)
        self.assertEqual(PSQL_ERROR, result.stderr)
        self.assertNotIn("proof:", result.stdout)
        self.assertNotIn("::error::", result.stdout)

    def test_an_empty_snapshot_is_refused_by_name(self) -> None:
        """Either database's empty read fails with the exact message."""
        for empty, upgraded, fresh in (
            ("upgraded", "", ROW_A + ROW_B),
            ("fresh", ROW_A + ROW_B, ""),
        ):
            with self.subTest(empty=empty):
                result, _ = self.run_proof(upgraded, fresh)
                self.assertEqual(1, result.returncode)
                self.assertEqual(
                    f"::error::{empty} catalog snapshot of demo_ext is empty"
                    " — the proof read nothing\n",
                    result.stdout,
                )

    def test_equal_snapshots_pass_with_the_object_count(self) -> None:
        """The success line says how many objects it compared."""
        result, _ = self.run_proof(ROW_A + ROW_B, ROW_A + ROW_B)
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertEqual(
            "proof: demo_ext 1.0.0 -> 1.1.0 upgraded catalog identical"
            " to fresh install (2 objects)\n",
            result.stdout,
        )

    def test_different_snapshots_fail_with_the_diff(self) -> None:
        """A differing catalog keeps the existing message."""
        result, _ = self.run_proof(ROW_A, ROW_A + ROW_B)
        self.assertEqual(1, result.returncode)
        self.assertIn("+" + ROW_B, result.stdout)
        self.assertTrue(
            result.stdout.endswith(
                "::error::upgraded demo_ext catalog differs from a fresh 1.1.0"
                " install — the derived script is unsound; see the diff above\n"
            ),
            result.stdout,
        )

    def test_every_psql_call_stops_on_error(self) -> None:
        """No psql in the proof can fail and exit 0."""
        _, calls = self.run_proof(ROW_A + ROW_B, ROW_A + ROW_B)
        self.assertEqual(7, len(calls), calls)
        for call in calls:
            with self.subTest(call=call):
                self.assertTrue(call.startswith("-v ON_ERROR_STOP=1 "), call)


def expected_inner_skips() -> int:
    """Count the skips a whole-suite run of this file owes (#857 row).

    Returns:
        One for the row declining to recurse, plus every floor row
        where this machine has no Postgres to start.

    """
    skips = 1
    if not (PG_BIN or ON_RUNNER):
        for live in (WarnsBelowTheFloor, SnapshotReadsTheCatalog):
            skips += len(unittest.TestLoader().getTestCaseNames(live))
    return skips


if __name__ == "__main__":
    unittest.main()

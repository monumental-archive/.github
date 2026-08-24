#!/usr/bin/env python3
"""Table tests for the release scripts' guard branches (#772).

The scripts in `release/` are mostly capability — build a container,
call an API, sign a tag — and capability is release-lab's to prove at
full width. What is testable here without any of that is every branch
that maps an input to a DECISION before the capability starts: a missing
variable, a tree that is not applicable, a forge answer that means
"nothing to do", a refusal.

Those branches are the least exercised code in the org, and #364 named
the failure mode precisely: a guard that skips when it should run looks
exactly like success. Each row below therefore states which branch it
is, and the negative rows matter as much as the positive ones — a skip
that cannot be told from a pass is the defect.

WHAT IS NOT HERE, deliberately. `derive-coverage-floor.sh`'s measuring
branch shells into the belt's `coverage-measure.sh`; `rust-build.sh`
past its input validation runs a real `cargo auditable build`;
`tag-release.sh` past its refusals signs with gitsign and pushes;
`open-release-pr.sh` past its validation calls the GraphQL API. None of
those is a pure input-to-decision mapping, and #772 kept the container
integration legs with release-lab on exactly that line.

WHAT IT MAY NOT TOUCH. Every subprocess here takes `hermetic_env()`,
the contract `test_generate_pgrx_upgrade.py` states for #857: every
`GIT_*` dropped but the editor, HOME redirected, an identity supplied.
`cwd=` is no defence, because `GIT_DIR` is an absolute override git
obeys wherever it is standing.

stdlib `unittest`, matching mise/test_*.py — #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
RELEASE = CANON / "release"
BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")
STELE = shutil.which("stele")

# `gh` for the record-draft rows. It answers the two reads the script
# makes from a fixture the test writes, and captures the notes body the
# script would have posted so the row can assert what was written rather
# than that something was.
GH_STUB = """#!/usr/bin/env bash
set -uo pipefail
if [[ ${1:-} == release && ${2:-} == view ]]; then
  [[ -f ${STATE}/exists ]] || exit 1
  for arg in "$@"; do
    case "${arg}" in
      isDraft) cat "${STATE}/isDraft"; exit 0 ;;
      body) cat "${STATE}/body"; exit 0 ;;
    esac
  done
  exit 0
fi
if [[ ${1:-} == release && ${2:-} == edit ]]; then
  prev=""
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --notes-file ]]; then prev="$2"; fi
    shift
  done
  cp "${prev}" "${STATE}/posted"
  exit 0
fi
echo "gh stub: refusing '$*'" >&2
exit 1
"""


GIT_ENV_KEPT = frozenset({"GIT_EDITOR"})
IDENTITY_NAME = "release guard harness"
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
        runner's variables dropped, HOME and a git identity set, and any
        overrides applied last.

    """
    env = {name: value for name, value in os.environ.items() if not _ambient(name)}
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


def run_script(name: str, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run one release script in a fixture directory.

    Returns:
        The completed process, never raising on a non-zero status: a
        refusal is the thing under test.

    """
    # Inside the fixture, never beside it: a sibling of a
    # `TemporaryDirectory()` outlives the cleanup and leaks a directory
    # per row. None of these scripts stages anything, so a dot-directory
    # in the tree is invisible to the `git ls-files` reads they do make.
    home = root / ".home"
    home.mkdir(exist_ok=True)
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [BASH, str(RELEASE / name)],
        cwd=root,
        env=hermetic_env(home, **env),
        capture_output=True,
        text=True,
        check=False,
    )


def emitted(path: Path) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file into its key/value pairs.

    Returns:
        The emitted step outputs; an absent file reads as none emitted.

    """
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        pairs[key] = value
    return pairs


class DraftRecorder:
    """`record-draft.sh` driven against a stubbed forge."""

    def __init__(self, root: str) -> None:
        """Install the stub and default the forge to an unannotated draft."""
        self.root = Path(root)
        self.state = self.root / "state"
        self.state.mkdir()
        bindir = self.root / "stubbin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.bindir = bindir
        self.forge(exists=True, is_draft=True, body="the release notes\n")

    def forge(self, *, exists: bool, is_draft: bool = True, body: str = "") -> None:
        """Set what the stubbed forge will say about the release."""
        marker = self.state / "exists"
        if exists:
            marker.write_text("")
        elif marker.exists():
            marker.unlink()
        (self.state / "isDraft").write_text("true" if is_draft else "false")
        (self.state / "body").write_text(body)

    def record(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Run the recorder with the stub first on PATH.

        Returns:
            The completed process.

        """
        return run_script(
            "record-draft.sh",
            self.root,
            PATH=f"{self.bindir}:{os.environ['PATH']}",
            STATE=str(self.state),
            **env,
        )

    def posted(self) -> str:
        """Read back what the script would have published.

        Returns:
            The notes body the script posted, or the empty string when
            it posted nothing — which several rows assert.

        """
        posted = self.state / "posted"
        return posted.read_text(encoding="utf-8") if posted.exists() else ""


class RecordDraftRefusals(unittest.TestCase):
    """The inputs `record-draft.sh` will not act on."""

    def test_a_missing_repo_is_refused(self) -> None:
        """REPO defaults to GITHUB_REPOSITORY, and neither being set is fatal."""
        with TemporaryDirectory() as d:
            result = DraftRecorder(d).record(TAG="v1.0.0", KIND="burn")
        self.assertEqual(1, result.returncode)
        self.assertIn("REPO is unset", result.stderr)

    def test_a_missing_tag_is_refused(self) -> None:
        """There is no default tag: a record is about one version number."""
        with TemporaryDirectory() as d:
            result = DraftRecorder(d).record(REPO="o/r", KIND="burn")
        self.assertEqual(1, result.returncode)
        self.assertIn("TAG is unset", result.stderr)

    def test_a_kind_outside_the_two_is_refused_by_name(self) -> None:
        """Burn and rehearsal are different claims; a third is neither."""
        with TemporaryDirectory() as d:
            result = DraftRecorder(d).record(REPO="o/r", TAG="v1.0.0", KIND="cleanup")
        self.assertEqual(1, result.returncode)
        self.assertIn("KIND must be burn or rehearsal, got 'cleanup'", result.stderr)

    def test_a_read_that_fails_is_never_read_as_no_record_yet(self) -> None:
        """Degraded-forge discipline: an unreadable release stops the script.

        Treating a failed read as "nothing there" would annotate a
        release that has already published, or write a second record
        onto an annotated draft.
        """
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.forge(exists=False)
            result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
            posted = recorder.posted()
        self.assertEqual(1, result.returncode)
        self.assertIn(
            "no release at o/r@v1.0.0, or it could not be read", result.stderr
        )
        self.assertEqual("", posted)


class RecordDraftNoOps(unittest.TestCase):
    """The two states that are already correct, and must not be rewritten."""

    def test_a_published_release_is_its_own_record(self) -> None:
        """Not a failure: exit 0, and nothing posted."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.forge(exists=True, is_draft=False)
            result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
            posted = recorder.posted()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("a published release is its own record", result.stdout)
        self.assertEqual("", posted)

    def test_an_existing_record_is_left_alone(self) -> None:
        """Idempotent: the marker is the check, so re-running is safe."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.forge(
                exists=True, body="<!-- draft-record: burn -->\nalready written\n"
            )
            result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
            posted = recorder.posted()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("already carries a record, leaving it", result.stdout)
        self.assertEqual("", posted)


class RecordDraftProse(unittest.TestCase):
    """What the record SAYS, which is the whole point of writing one."""

    def test_a_burn_with_a_run_cites_it(self) -> None:
        """The evidence outlives the logs, which expire at 90 days."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            result = recorder.record(
                REPO="o/r", TAG="v1.0.0", KIND="burn", RUN_URL="https://example/run/1"
            )
            posted = recorder.posted()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("<!-- draft-record: burn -->", posted)
        self.assertIn("**Burned version — never released.**", posted)
        self.assertIn("its publish run failed before anything", posted)
        self.assertIn("- Burned by: https://example/run/1", posted)
        self.assertIn("the release notes", posted)

    def test_a_burn_with_no_run_does_not_fabricate_one(self) -> None:
        """A citation for a run that never existed is the shape audits catch.

        The sentence follows the evidence rather than the other way
        round, so the two burns are not the same claim.
        """
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
            posted = recorder.posted()
        self.assertIn("no publish run for it", posted)
        self.assertIn("- Burned by: no run recorded", posted)
        self.assertNotIn("its publish run failed", posted)

    def test_a_burn_leaves_the_fix_pending_until_one_is_cut(self) -> None:
        """The fix does not exist at burn time, and the record says so."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
            pending = recorder.posted()
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.record(
                REPO="o/r", TAG="v1.0.0", KIND="burn", FIXED_FORWARD="v1.0.1"
            )
            completed = recorder.posted()
        self.assertIn("- Fixed forward in: pending", pending)
        self.assertIn("- Fixed forward in: v1.0.1", completed)

    def test_a_rehearsal_is_not_described_as_a_burn(self) -> None:
        """A dry run leaves a draft ON PURPOSE and is otherwise identical."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            recorder.record(
                REPO="o/r", TAG="v1.0.0", KIND="rehearsal", RUN_URL="https://e/r/2"
            )
            posted = recorder.posted()
        self.assertIn("<!-- draft-record: rehearsal -->", posted)
        self.assertIn("**Rehearsal — deliberately left unpublished.**", posted)
        self.assertIn("- Rehearsed by: https://e/r/2", posted)
        self.assertNotIn("Burned", posted)
        self.assertNotIn("Fixed forward", posted)


class CoverageFloorApplicability(unittest.TestCase):
    """`derive-coverage-floor.sh` decides whether it applies at all (#652)."""

    def test_the_version_is_required(self) -> None:
        """The floor records the release it was measured for."""
        with TemporaryDirectory() as d:
            result = run_script("derive-coverage-floor.sh", Path(d))
        self.assertEqual(1, result.returncode)
        self.assertIn("VERSION must be set", result.stderr)

    def test_no_floor_file_is_an_adoption_decision_not_a_failure(self) -> None:
        """The belt offers the mechanism; the committed file is the adoption."""
        with TemporaryDirectory() as d:
            out = Path(d) / "outputs"
            result = run_script(
                "derive-coverage-floor.sh",
                Path(d),
                VERSION="1.0.0",
                GITHUB_OUTPUT=str(out),
            )
            files = emitted(out)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no .coverage-floor, skipped", result.stdout)
        self.assertEqual("", files["files"])

    def test_a_floor_with_no_measurable_language_skips_clean(self) -> None:
        """Mid-migration is not a release to fail; `coverage:check` agrees.

        This is the row that distinguishes a skip from a pass: the file
        IS present, so the previous row's guard has been passed, and the
        script still declines — for a different, stated reason.
        """
        with TemporaryDirectory() as d:
            (Path(d) / ".coverage-floor").write_text("floor = 80.0\n")
            out = Path(d) / "outputs"
            result = run_script(
                "derive-coverage-floor.sh",
                Path(d),
                VERSION="1.0.0",
                GITHUB_OUTPUT=str(out),
            )
            files = emitted(out)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no Cargo.toml or go.mod, skipped", result.stdout)
        self.assertEqual("", files["files"])


class RustBuildInputs(unittest.TestCase):
    """`rust-build.sh` validates its scope before it compiles anything."""

    def test_the_target_is_required(self) -> None:
        """There is no default triple: the artifact's platform is the caller's."""
        with TemporaryDirectory() as d:
            result = run_script("rust-build.sh", Path(d), STAGE_DIR="stage")
        self.assertEqual(1, result.returncode)
        self.assertIn("rust-build: TARGET is unset", result.stdout)

    def test_the_stage_directory_is_required(self) -> None:
        """Two callers stage differently; neither inherits a default."""
        with TemporaryDirectory() as d:
            result = run_script(
                "rust-build.sh", Path(d), TARGET="x86_64-unknown-linux-musl"
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("rust-build: STAGE_DIR is unset", result.stdout)

    def test_a_plan_class_without_a_document_prefix_is_refused(self) -> None:
        """A plan names a document; the prefix says what the artifact IS.

        The prefix and the params disagreeing silently is #544, so the
        pair is required together rather than defaulted apart.
        """
        with TemporaryDirectory() as d:
            result = run_script(
                "rust-build.sh",
                Path(d),
                TARGET="x86_64-unknown-linux-musl",
                STAGE_DIR="stage",
                PLAN_CLASS="oci-image",
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("PLAN_CLASS is set but PLAN_DOC_PREFIX is not", result.stdout)


@unittest.skipUnless(JQ, "jq reads the plan document")
class OpenReleasePrInputs(unittest.TestCase):
    """`open-release-pr.sh` is plumbing, and refuses to plumb blind."""

    REQUIRED = ("PLAN", "GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA", "APP_SLUG")

    @staticmethod
    def _full_env(plan: Path) -> dict[str, str]:
        """Build a complete input set for the executor.

        Returns:
            Every variable the script requires, all satisfied.

        """
        return {
            "PLAN": str(plan),
            # Not a credential: no call that would use one is reachable
            # in these rows. ruff: ignore[hardcoded-password-func-arg]
            "GH_TOKEN": "stub",
            "GITHUB_REPOSITORY": "monumental-archive/under-test",
            "GITHUB_SHA": "0" * 40,
            "APP_SLUG": "org-tag-mint",
        }

    def test_each_required_input_is_named_when_it_is_missing(self) -> None:
        """Five inputs, five rows: a default here would plumb the wrong repo."""
        with TemporaryDirectory() as d:
            plan = Path(d) / "plan.json"
            plan.write_text('{"version": "1.0.0"}')
            for missing in self.REQUIRED:
                env = OpenReleasePrInputs._full_env(plan)
                del env[missing]
                with self.subTest(missing=missing):
                    result = run_script("open-release-pr.sh", Path(d), **env)
                    self.assertEqual(1, result.returncode)
                    self.assertIn(missing, result.stderr)

    def test_a_plan_that_states_no_branch_is_refused_by_field(self) -> None:
        """The branch, staging ref, version and subject are all the plan's.

        A plan missing any of them is not a plan this script may
        improvise around — it names the field and stops.
        """
        with TemporaryDirectory() as d:
            plan = Path(d) / "plan.json"
            plan.write_text(
                '{"version": "1.0.0", "commit": {"subject": "chore: release v1.0.0"}}'
            )
            result = run_script("open-release-pr.sh", Path(d), **self._full_env(plan))
        self.assertEqual(1, result.returncode)
        self.assertIn("FAIL: the plan states no branch", result.stderr)


@unittest.skipUnless(STELE, "stele derives the plan tag-release.sh reads")
@unittest.skipUnless(GIT, "the fixture is a git repository")
class TagReleaseRefusals(unittest.TestCase):
    """`tag-release.sh` decides whether a commit may be tagged at all.

    Every row here stops before the signing step, which is the point:
    a tag is immutable, so each of these is a version number NOT spent.
    """

    @staticmethod
    def _repo(root: str, *, subject: str) -> Path:
        """Lay out a repository whose HEAD carries `subject`.

        Returns:
            The repository root, a sibling of the HOME its git runs
            under so that nothing git writes for the fixture lands
            inside the tree being staged.

        """
        home = Path(root) / "home"
        home.mkdir()
        repo = Path(root) / "repo"
        repo.mkdir()
        (repo / "a.txt").write_text("x\n")

        def git(*args: str) -> None:
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run(
                [GIT, *args],
                cwd=repo,
                env=hermetic_env(home),
                check=True,
                capture_output=True,
            )

        git("init", "-q", ".")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Fixture")
        git("config", "commit.gpgSign", "false")
        git("config", "tag.gpgSign", "false")
        git("config", "core.hooksPath", str(repo / ".githooks-none"))
        git("add", "-A")
        git("commit", "-q", "-m", "feat: initial")
        git("tag", "-a", "v1.0.0", "-m", "v1.0.0")
        (repo / "b.txt").write_text("y\n")
        git("add", "-A")
        git("commit", "-q", "-m", subject)
        return repo

    @staticmethod
    def _run(repo: Path) -> subprocess.CompletedProcess[str]:
        """Run the tag step against a fixture repository.

        Returns:
            The completed process.

        """
        return run_script(
            "tag-release.sh",
            repo,
            GITHUB_REPOSITORY="monumental-archive/under-test",
            RUNNER_TEMP=str(repo / ".rt"),
        )

    def setUp(self) -> None:
        """Nothing shared; each row builds the history it is about."""

    def test_an_ordinary_commit_is_not_a_release_commit(self) -> None:
        """A workflow_dispatch on main must not mint a tag for an unprepared tree.

        The manifests and changelog were never bumped, so the version
        the plan derives has nothing behind it.
        """
        with TemporaryDirectory() as d:
            repo = self._repo(d, subject="feat: an ordinary change")
            (repo / ".rt").mkdir()
            result = self._run(repo)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("HEAD is not the release commit", result.stderr)
        self.assertIn("expected: chore: release v", result.stderr)

    def test_a_commit_already_tagged_is_a_resume_not_a_failure(self) -> None:
        """A re-dispatch onto a tagged commit is a resume, not a failure.

        The work is already done — the tag is minted and the version
        spent — so the run has nothing to do and says so. #864 made
        this reachable: the resume is asked of HEAD before the plan is
        derived, because once the tag is on the commit the range is
        empty and the plan correctly names no tag at all.

        Written as the contract and marked expected-failure while the
        defect stood, so that the fix would force the decorator off
        (#772). It did exactly that: the fix landed, this row reported
        an unexpected success, and the decorator went with it.
        """
        with TemporaryDirectory() as d:
            repo = self._repo(d, subject="feat: an ordinary change")
            (repo / ".rt").mkdir()
            first = self._run(repo)
            tag = ""
            for line in first.stderr.splitlines():
                if "expected: chore: release v" in line:
                    tag = "v" + line.rsplit("release v", 1)[1].strip()
            self.assertTrue(tag, first.stderr)
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run(
                [GIT, "tag", "-a", tag, "-m", tag],
                cwd=repo,
                env=hermetic_env(repo.parent / "home"),
                check=True,
                capture_output=True,
            )
            result = self._run(repo)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"{tag} already exists; nothing to do", result.stdout)


if __name__ == "__main__":
    unittest.main()

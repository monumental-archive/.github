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
`tag-release.sh` past its refusals signs with gitsign and pushes.
None of those is a pure input-to-decision mapping, and #772 kept the
container integration legs with release-lab on exactly that line.
`open-release-pr.sh` is the exception that proves it: its one decision
past validation — is this plan still for the commit the default branch
is at (#1260) — is read from the forge, so its rows drive the real
script end to end against a stubbed `gh` and assert the forge calls it
made, in order, not only how it exited.

WHAT IT MAY NOT TOUCH. Every subprocess here takes `hermetic_env()`,
the contract `test_generate_pgrx_upgrade.py` states for #857: every
`GIT_*` dropped but the editor, HOME redirected, an identity supplied.
`cwd=` is no defence, because `GIT_DIR` is an absolute override git
obeys wherever it is standing.

stdlib `unittest`, matching mise/test_*.py — #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import hermetic

CANON = Path(__file__).resolve().parent.parent
RELEASE = CANON / "release"
TESTDATA = RELEASE / "testdata"
BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")
STELE = shutil.which("stele")


def _parses(script: str) -> bool:
    """Report whether this machine's bash can parse a release script.

    macOS ships bash 3.2, which cannot parse a heredoc carrying an
    apostrophe inside `$(...)` — open-release-pr.sh's PR body is one.
    The runner's bash can. A row that drives the script past that line
    therefore runs where the release path runs and skips, by name,
    where it never will; the rows that stop before it run everywhere.

    Returns:
        True when `bash -n` accepts the script.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return (
        subprocess.run(
            [BASH, "-n", str(RELEASE / script)], capture_output=True, check=False
        ).returncode
        == 0
    )


OPEN_PR_PARSES = _parses("open-release-pr.sh")

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


IDENTITY_NAME = "release guard harness"
IDENTITY_EMAIL = "harness@example.invalid"


# A row that asserts an input is REQUIRED only means something in an
# environment where the input is genuinely absent, and a GitHub runner
# supplies several of them ambiently — `GITHUB_REPOSITORY`, `GITHUB_SHA`,
# `RUNNER_TEMP` — as the release job supplies its step inputs
# (`VERSION`, `PLAN`, `APP_SLUG`). So nothing reaches a fixture by
# inheritance except what mise/hermetic.py names; every input a row wants is
# supplied explicitly through `overrides`.


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


RUN = "https://example/run/1"
NOTHING_PUBLIC = (
    "`v1.0.0` was tagged, and its publish run failed before anything"
    " became public. Tags are immutable, so this version number is spent:"
    " nothing shipped under it and nothing can. This draft is the record"
    " of that, not a release in flight."
)
SOME_PUBLIC_HEAD = (
    "`v1.0.0` was tagged, and its publish run failed after these became"
    " public, and they remain:\n\n"
)
SOME_PUBLIC_TAIL = (
    "\nTags are immutable, so this version number is spent: the release"
    " never published, and nothing more can ship under it. This draft is"
    " the record of that, not a release in flight."
)


class RecordDraftRegistries(unittest.TestCase):
    """What a burn left on the registries, read from its job results (#1280).

    The uploads run beside each other after the commit point, so a run
    that dies at one may already have shipped another, and "nothing
    became public" is then false.
    """

    def burn(self, registries: str) -> str:
        """Record a burn with these job results and return the body.

        Returns:
            The notes body the script posted.

        """
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            result = recorder.record(
                REPO="o/r",
                TAG="v1.0.0",
                KIND="burn",
                RUN_URL=RUN,
                REGISTRIES=registries,
            )
            posted = recorder.posted()
        self.assertEqual(0, result.returncode, result.stderr)
        return posted

    def test_nothing_public_when_every_registry_job_skipped(self) -> None:
        """A run that died before its commit point keeps the old sentence."""
        posted = self.burn(
            "crates.io=skipped\nnpm=skipped\n"
            "ghcr.io (extension images)=skipped\nghcr.io (image)=\n"
        )
        self.assertIn(NOTHING_PUBLIC, posted)
        self.assertNotIn("remain", posted)

    def test_nothing_public_when_no_registry_list_is_given(self) -> None:
        """The backfill and older callers pass no list: the sentence is unchanged."""
        posted = self.burn("")
        self.assertIn(NOTHING_PUBLIC, posted)

    def test_some_public_names_each_registry_that_succeeded(self) -> None:
        """The shape edtf v1.3.3 burned in: npm and image tags out, crates.io red."""
        posted = self.burn(
            "crates.io=failure\nnpm=success\n"
            "ghcr.io (extension images)=success\nghcr.io (image)=skipped\n"
        )
        self.assertIn(
            SOME_PUBLIC_HEAD
            + "- npm\n- ghcr.io (extension images)\n"
            + "- crates.io: may be partly published\n"
            + SOME_PUBLIC_TAIL,
            posted,
        )
        self.assertNotIn("before anything", posted)
        self.assertNotIn("nothing shipped under it", posted)
        self.assertNotIn("ghcr.io (image)", posted)

    def test_one_partly_published_is_listed_alone(self) -> None:
        """A failed upload is neither public nor nothing: it may be partial."""
        posted = self.burn("crates.io=failure\nnpm=skipped\n")
        self.assertIn(
            SOME_PUBLIC_HEAD
            + "- crates.io: may be partly published\n"
            + SOME_PUBLIC_TAIL,
            posted,
        )
        self.assertNotIn("before anything", posted)

    def test_a_cancelled_upload_may_be_partly_published(self) -> None:
        """Cancelled mid-upload is the same claim as failed mid-upload."""
        posted = self.burn("npm=cancelled\n")
        self.assertIn("- npm: may be partly published\n", posted)

    def refused(self, message: str, **env: str) -> None:
        """Assert the run refuses with this message and posts nothing."""
        with TemporaryDirectory() as d:
            recorder = DraftRecorder(d)
            result = recorder.record(REPO="o/r", TAG="v1.0.0", **env)
            posted = recorder.posted()
        self.assertEqual(1, result.returncode)
        self.assertEqual(f"record-draft: {message}\n", result.stderr)
        self.assertEqual("", posted)

    def test_a_registry_list_without_a_run_is_refused(self) -> None:
        """Job results are evidence of a run; with no run they cite nothing."""
        self.refused(
            "REGISTRIES is evidence of a publish run: it needs KIND=burn and RUN_URL",
            KIND="burn",
            REGISTRIES="npm=success",
        )

    def test_a_registry_list_on_a_rehearsal_is_refused(self) -> None:
        """A dry run publishes to no registry, so a list there is a wrong call."""
        self.refused(
            "REGISTRIES is evidence of a publish run: it needs KIND=burn and RUN_URL",
            KIND="rehearsal",
            RUN_URL=RUN,
            REGISTRIES="npm=success",
        )

    def test_a_line_without_a_result_is_refused(self) -> None:
        """A line is <registry>=<job result>, never a bare name."""
        self.refused(
            "REGISTRIES line 'npm' is not <registry>=<job result>",
            KIND="burn",
            RUN_URL=RUN,
            REGISTRIES="npm",
        )

    def test_a_line_without_a_registry_is_refused(self) -> None:
        """An unnamed result could not say what became public."""
        self.refused(
            "REGISTRIES line '=success' is not <registry>=<job result>",
            KIND="burn",
            RUN_URL=RUN,
            REGISTRIES="=success",
        )

    def test_an_unknown_result_is_refused_by_name(self) -> None:
        """A result outside GitHub's set is never guessed public or not."""
        self.refused(
            "REGISTRIES: npm has job result 'neutral', not success, failure,"
            " cancelled, skipped or empty",
            KIND="burn",
            RUN_URL=RUN,
            REGISTRIES="npm=neutral",
        )


class HermeticEnvironment(unittest.TestCase):
    """A fixture's environment is what the row gives it (#1508)."""

    def test_the_release_job_inputs_do_not_reach_a_fixture(self) -> None:
        """The release job exports its step inputs; no fixture sees them."""
        planted = {"VERSION": "1.69.4", "PLAN": "{}", "APP_SLUG": "canon-release"}
        with patch.dict(os.environ, planted), TemporaryDirectory() as d:
            env = hermetic_env(Path(d))
        self.assertEqual([], sorted(set(planted) & set(env)))

    def test_a_row_still_states_its_own_input(self) -> None:
        """Overrides are how a row gives a fixture an input."""
        with patch.dict(os.environ, {"VERSION": "1.69.4"}), TemporaryDirectory() as d:
            env = hermetic_env(Path(d), VERSION="2.0.0")
        self.assertEqual("2.0.0", env["VERSION"])


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
        self.assertIn(".coverage-floor with no measurable language", result.stdout)
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

    @unittest.skipUnless(JQ, "jq reads cargo's metadata")
    def test_the_build_is_handed_fat_lto(self) -> None:
        """Release builds link with fat LTO, the fourth determinism input (#778).

        Cargo's `lto = false` default still runs thin LOCAL LTO, whose
        `.llvm.<hash>` symbols differed between two clean builds of the
        same tree, so the repro gate failed on iiif-server. A stand-in
        cargo records what the build step was handed and stops there.
        """
        with TemporaryDirectory() as d:
            root = Path(d)
            stubs = root / "stubs"
            stubs.mkdir()
            seen = root / "build-env"
            stub = (
                "#!/usr/bin/env bash\n"
                "if [[ $1 == metadata ]]; then\n"
                '  echo \'{"packages": [{"name": "demo", "targets": []}]}\'\n'
                "  exit 0\n"
                "fi\n"
                f'printf "%s" "${{CARGO_PROFILE_RELEASE_LTO-unset}}" > "{seen}"\n'
                "exit 1\n"
            )
            for name, body in (
                ("cargo", stub),
                ("rustup", "#!/usr/bin/env bash\nexit 0\n"),
                ("cargo-auditable", "#!/usr/bin/env bash\nexit 0\n"),
            ):
                (stubs / name).write_text(body)
                (stubs / name).chmod(0o755)
            result = run_script(
                "rust-build.sh",
                root,
                TARGET="x86_64-unknown-linux-gnu",
                STAGE_DIR="stage",
                SOURCE_DATE_EPOCH="1700000000",
                PACKAGE="demo",
                PATH=f"{stubs}:{os.environ['PATH']}",
            )
            self.assertTrue(seen.exists(), result.stdout + result.stderr)
            self.assertEqual("fat", seen.read_text())


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


# `gh` for the Release-PR rows: a forge that answers every call
# open-release-pr.sh and close-release-pr.sh make and records each one,
# one normalised line per call, so a row can assert exactly what was
# written and in what order. A ref read answers "absent" unless its path
# is listed in ${STATE}/exists, so a run that proceeds creates both refs;
# the open pull requests are a state file, a close keeps the comment it
# was given, and a create or edit keeps the body. A call listed in
# ${STATE}/queued is refused the way the forge refuses to move the branch
# of a pull request in the merge queue (#1472).
RELEASE_FORGE_STUB = """#!/usr/bin/env bash
set -uo pipefail
log() { printf '%s\\n' "$*" >> "${STATE}/calls"; }
# A call made with --include gets what the real gh prints first: the
# status line and headers, CRLF-terminated, then a blank line. A call
# named in ${STATE}/refuse is refused the way the forge refused #633.
headers() {
  [[ " $* " == *" --include "* ]] || return 0
  printf 'HTTP/2.0 %s\r\nX-Github-Request-Id: STUB:633\r\n' "${status:-200 OK}"
  printf 'X-Accepted-Github-Permissions: contents=write\r\n\r\n'
}
refused() {
  [[ -f ${STATE}/refuse ]] && grep -qxF "$1" "${STATE}/refuse" || return 1
  status="403 Forbidden" headers "${@:2}"
  echo '{"message":"Resource not accessible by integration","status":"403"}'
  echo "gh: Resource not accessible by integration (HTTP 403)" >&2
  exit 1
}
queued() {
  [[ -f ${STATE}/queued ]] && grep -qxF "$1" "${STATE}/queued" || return 1
  status="422 Unprocessable Entity" headers "${@:2}"
  printf '%s' '{"message":"A pull request for this branch has been added to'
  printf '%s' ' a merge queue. Branches that are queued for merging cannot be'
  printf '%s' ' updated. To modify this branch, dequeue the associated'
  printf '%s\n' ' pull request.","status":"422"}'
  printf '%s' "gh: A pull request for this branch has been added" >&2
  printf '%s\n' " to a merge queue. (HTTP 422)" >&2
  exit 1
}
keep_body() {
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --body ]]; then printf '%s' "$2" > "${STATE}/body"; fi
    shift
  done
}
if [[ ${1:-} == api && ${2:-} == graphql ]]; then
  # The dequeue (#1504): accepted, it takes the branch out of the queue so
  # the next move succeeds. ${STATE}/dequeue says how the forge answers
  # otherwise: "forbidden" as GraphQL refuses an integration (HTTP 200,
  # errors[].type FORBIDDEN), "fails" as a 502.
  if [[ "$*" == *dequeuePullRequest* ]]; then
    log graphql dequeuePullRequest
    printf '%s' "$*" > "${STATE}/dequeue-request"
    case "$(cat "${STATE}/dequeue" 2> /dev/null)" in
      forbidden)
        headers "$@"
        printf '%s' '{"data":{"dequeuePullRequest":null},"errors":[{"type":'
        printf '%s' '"FORBIDDEN","message":"Resource not accessible'
        printf '%s\n' ' by integration"}]}'
        echo "gh: Resource not accessible by integration" >&2
        exit 1
        ;;
      fails)
        status="502 Bad Gateway" headers "$@"
        echo "gh: HTTP 502" >&2
        exit 1
        ;;
    esac
    rm -f "${STATE}/queued"
    headers "$@"
    echo '{"data":{"dequeuePullRequest":{"mergeQueueEntry":{"id":"MQE_1"}}}}'
    exit 0
  fi
  if [[ "$*" == *defaultBranchRef* ]]; then
    log graphql head
    [[ -f ${STATE}/head ]] || { echo "gh: HTTP 502" >&2; exit 1; }
    cat "${STATE}/head"
    exit 0
  fi
  request=$(cat)
  if grep -q createCommitOnBranch <<< "${request}"; then
    log graphql createCommitOnBranch
    printf '%s' "${request}" > "${STATE}/commit-request"
    refused "graphql createCommitOnBranch" "$@"
    headers "$@"
    echo "${OID}"
    exit 0
  fi
fi
if [[ ${1:-} == api ]]; then
  path=$2 method=GET
  args=("$@")
  shift 2
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --method ]]; then method=$2; fi
    shift
  done
  log "${method} ${path}"
  refused "${method} ${path}" "${args[@]}"
  queued "${method} ${path}" "${args[@]}"
  # An App installation token has no user context: the forge answers
  # `user` with this 403 (#58, measured in the release lab).
  if [[ ${path} == user ]]; then
    echo "gh: Resource not accessible by integration (HTTP 403)" >&2
    exit 1
  fi
  [[ ${method} == GET && ${path} == */git/ref/heads/* ]] || headers "${args[@]}"
  case "${method} ${path}" in
    "GET users/"*) echo 4242 ;;
    "GET repos/"*"/commits/"*) echo true ;;
    "GET repos/"*"/compare/"*) cat "${STATE}/ahead" 2> /dev/null || echo 2 ;;
    "GET repos/"*"/git/ref/heads/"*)
      [[ -f ${STATE}/exists ]] && grep -qxF "${path}" "${STATE}/exists" || exit 1
      ;;
  esac
  exit 0
fi
if [[ ${1:-} == pr ]]; then
  case "${2:-}" in
    list)
      log pr list
      [[ ! -f ${STATE}/list-fails ]] || { echo "gh: HTTP 502" >&2; exit 1; }
      [[ ! -f ${STATE}/open ]] || cat "${STATE}/open"
      ;;
    create)
      log pr create
      keep_body "$@"
      ;;
    edit)
      log pr edit "$3"
      keep_body "$@"
      ;;
    close)
      log pr close "$3"
      number=$3
      while [[ $# -gt 0 ]]; do
        if [[ $1 == --comment ]]; then
          printf '%s' "$2" > "${STATE}/comment-${number}"
        fi
        shift
      done
      ;;
    view)
      log pr view "$3"
      cat "${STATE}/view"
      ;;
    comment)
      log pr comment "$3"
      while [[ $# -gt 0 ]]; do
        if [[ $1 == --body ]]; then printf '%s' "$2" > "${STATE}/pr-comment"; fi
        shift
      done
      ;;
    *) log pr "$2" "$3" ;;
  esac
  exit 0
fi
echo "gh stub: refusing '$*'" >&2
exit 1
"""


FORGE_REPO = "monumental-archive/under-test"


class ReleaseForge:
    """A fixture directory with the stubbed forge first on PATH."""

    REPO = FORGE_REPO
    PLANNED = "a" * 40
    MOVED = "b" * 40

    def setUp(self) -> None:
        """Lay out a fixture with a stub on PATH and an empty call log."""
        self._dir = TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.state = self.root / "state"
        self.state.mkdir()
        bindir = self.root / "stubbin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(RELEASE_FORGE_STUB)
        gh.chmod(0o755)
        self.bindir = bindir

    def tearDown(self) -> None:
        """Remove the fixture."""
        self._dir.cleanup()

    def forge(self, head: str | None) -> None:
        """Set the default branch head the forge reports; None fails the read."""
        marker = self.state / "head"
        if head is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(head + "\n")

    def calls(self) -> list[str]:
        """Read back the forge calls the script made, in order.

        Returns:
            One normalised line per call; empty when it made none.

        """
        log = self.state / "calls"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def stubbed(self) -> dict[str, str]:
        """Build the inputs every row shares: the stub, its state, the repo.

        Returns:
            The environment a row extends with the script's own inputs.

        """
        return {
            "PATH": f"{self.bindir}:{os.environ['PATH']}",
            "STATE": str(self.state),
            # Not a credential: the stub reads none.
            "GH_TOKEN": "stub",
            "GITHUB_REPOSITORY": self.REPO,
            "GITHUB_SHA": self.PLANNED,
            "GITHUB_EVENT_NAME": "push",
        }


@unittest.skipUnless(JQ, "jq reads the plan document")
class OpenReleasePrHead(ReleaseForge, unittest.TestCase):
    """A run acts only for the commit the default branch is at (#1260).

    Measured on edtf: a maintain job planned at one commit, the Release
    PR merged ten minutes into its run, and the job then opened a second
    Release PR for the version that had just shipped. Every row here
    runs the real script against a stubbed forge.
    """

    OID = "c" * 40
    PROCEEDED = (
        "graphql head",
        f"GET repos/{FORGE_REPO}/git/ref/heads/release-staging/next",
        f"POST repos/{FORGE_REPO}/git/refs",
        "GET users/org-tag-mint%5Bbot%5D",
        "graphql createCommitOnBranch",
        f"GET repos/{FORGE_REPO}/commits/{OID}",
        f"GET repos/{FORGE_REPO}/git/ref/heads/release/next",
        f"POST repos/{FORGE_REPO}/git/refs",
        "pr list",
        "pr create",
        f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next",
    )

    def setUp(self) -> None:
        """Lay out the stubbed forge and a plan with something to release."""
        super().setUp()
        self.plan = self.root / "plan.json"
        self.plan.write_text(
            '{"schema": 7, "release": true, "version": "1.0.0",'
            ' "commit": {"subject": "chore: release v1.0.0",'
            ' "additions": [], "deletions": []},'
            ' "branch": {"name": "release/next",'
            ' "staging": "release-staging/next"}}'
        )

    def run_executor(
        self, cwd: Path | None = None, **env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run open-release-pr.sh for a push of PLANNED.

        Returns:
            The completed process.

        """
        inputs = self.stubbed()
        inputs.update(OID=self.OID, PLAN=str(self.plan), APP_SLUG="org-tag-mint")
        inputs.update(env)
        return run_script("open-release-pr.sh", cwd or self.root, **inputs)

    @unittest.skipUnless(OPEN_PR_PARSES, "this bash cannot parse the PR body")
    def test_head_at_the_planned_commit_proceeds_as_today(self) -> None:
        """The branch has not moved: the PR is opened exactly as before."""
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("opened the release PR for v1.0.0", result.stdout)
        self.assertNotIn("moved", result.stdout)
        self.assertEqual(list(self.PROCEEDED), self.calls())

    def test_a_refused_call_names_itself_and_what_the_forge_said(self) -> None:
        """A 403 says which call was refused and carries the headers (#633).

        Four runs died on the forge's one-line refusal with nothing to
        say which of the script's calls it answered. Each row refuses
        one call and asserts the run still fails, names that call, and
        stops exactly there.
        """
        staging = f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next"
        rows = (
            (
                f"POST repos/{FORGE_REPO}/git/refs",
                "create the staging ref release-staging/next at " + self.PLANNED,
                list(self.PROCEEDED[:3]),
            ),
            (
                "GET users/org-tag-mint%5Bbot%5D",
                "read the bot user of org-tag-mint",
                [*self.PROCEEDED[:4], staging],
            ),
            (
                "graphql createCommitOnBranch",
                "create the release commit on release-staging/next",
                [*self.PROCEEDED[:5], staging],
            ),
            (
                f"GET repos/{FORGE_REPO}/commits/{self.OID}",
                "read back the release commit " + self.OID,
                [*self.PROCEEDED[:6], staging],
            ),
        )
        for refuse, what, calls in rows:
            with self.subTest(refused=refuse):
                (self.state / "calls").unlink(missing_ok=True)
                (self.state / "refuse").write_text(refuse + "\n")
                self.forge(self.PLANNED)
                result = self.run_executor()
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertEqual(
                    "gh: Resource not accessible by integration (HTTP 403)\n"
                    f"FAIL: could not {what}\n"
                    "HTTP/2.0 403 Forbidden\n"
                    "X-Github-Request-Id: STUB:633\n"
                    "X-Accepted-Github-Permissions: contents=write\n",
                    result.stderr,
                )
                self.assertEqual("", result.stdout)
                self.assertEqual(calls, self.calls())

    def test_the_sign_off_names_the_app_bot_without_the_user_endpoint(
        self,
    ) -> None:
        """The release commit signs off as the App's bot user (#58).

        An installation token has no user context, so `gh api user`
        answers 403 and the Release PR was never opened. The identity
        comes from APP_SLUG and the public users endpoint instead, and
        `lint:dco` matches it against the commit author verbatim.
        """
        self.forge(self.PLANNED)
        result = self.run_executor()
        request = self.state / "commit-request"
        self.assertTrue(
            request.exists(), "no release commit was requested:\n" + result.stderr
        )
        message = json.loads(request.read_text())["variables"]["input"]["message"]
        self.assertEqual(
            "Signed-off-by: org-tag-mint[bot]"
            " <4242+org-tag-mint[bot]@users.noreply.github.com>",
            message["body"],
        )
        self.assertNotIn("GET user", self.calls())

    def test_the_old_half_of_a_rename_rides_the_commit_as_a_deletion(
        self,
    ) -> None:
        """A planned path absent from disk is deleted, not read (#101).

        The bump renames an upgrade script, and the plan names both
        halves. The release lab's first run of the rename (run
        31408916140) died with FileNotFoundError reading the old path
        as an addition.
        """
        old = "sql/demo--1.0.0--next.sql"
        new = "sql/demo--1.0.0--1.1.0.sql"
        plan = json.loads(self.plan.read_text())
        plan["commit"]["additions"] = [old, new]
        self.plan.write_text(json.dumps(plan))
        (self.root / "sql").mkdir()
        (self.root / new).write_text("-- upgrade\n")
        self.forge(self.PLANNED)
        result = self.run_executor()
        request = self.state / "commit-request"
        self.assertTrue(
            request.exists(), "no release commit was requested:\n" + result.stderr
        )
        changes = json.loads(request.read_text())["variables"]["input"]["fileChanges"]
        self.assertEqual([new], [a["path"] for a in changes["additions"]])
        self.assertEqual([{"path": old}], changes["deletions"])

    def test_head_moved_writes_nothing_and_says_why(self) -> None:
        """The measured race: the branch moved while the run was planning."""
        self.forge(self.MOVED)
        result = self.run_executor()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"main moved to {self.MOVED} while this run was planning;"
            " the run for that commit maintains the Release PR\n",
            result.stdout,
        )
        self.assertEqual(["graphql head"], self.calls())

    @unittest.skipUnless(OPEN_PR_PARSES, "this bash cannot parse the PR body")
    def test_the_body_says_where_the_release_was_planned(self) -> None:
        """The PR body ends in `Planned-At: <planned commit>` (#1472).

        The squash takes the body, so the release commit carries the
        commit it was planned from, and lint:release-planned reads it.
        """
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(0, result.returncode, result.stderr)
        body = (self.state / "body").read_text(encoding="utf-8")
        self.assertEqual(f"Planned-At: {self.PLANNED}", body.splitlines()[-1])
        self.assertEqual(1, body.count("Planned-At:"))

    def queue_release_branch(self) -> str:
        """Make release/next exist, so the run moves it rather than creating it.

        Returns:
            The forge call that moves it.

        """
        ref = f"repos/{FORGE_REPO}/git/ref/heads/release/next"
        (self.state / "exists").write_text(ref + "\n")
        return f"PATCH repos/{FORGE_REPO}/git/refs/heads/release/next"

    OLD_PLAN = "d" * 40

    def queue_release_pr(self) -> tuple[str, list[str]]:
        """Queue the open Release PR #77, planned at OLD_PLAN.

        Returns:
            The forge call that moves the branch, and the calls a run
            makes up to and including the move the queue refuses.

        """
        move = self.queue_release_branch()
        (self.state / "queued").write_text(move + "\n")
        (self.state / "open").write_text("77\n")
        (self.state / "view").write_text(
            json.dumps({
                "number": 77,
                "id": "PR_kwDOqueued",
                "body": f"Notes.\n\nPlanned-At: {self.OLD_PLAN}\n",
            })
        )
        return move, [*self.PROCEEDED[:7], move]

    @unittest.skipUnless(OPEN_PR_PARSES, "this bash cannot parse the PR body")
    def test_a_queued_release_pr_is_dequeued_and_regenerated(self) -> None:
        """The 422 for a queued branch: dequeue, move, say so (#1504).

        Measured on the canon: Release PR #1476 was queued and removed four
        times in an hour, planned at a commit main had passed, because the
        run that met this 422 exited without moving it and no later push
        came. Main's script stops at the 422.
        """
        move, before = self.queue_release_pr()
        (self.state / "ahead").write_text("3\n")
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(0, result.returncode, result.stderr)
        said = (
            "taken out of the merge queue: 3 commits landed after this release"
            f" was planned; regenerated at {self.PLANNED}; queue it again"
        )
        self.assertIn(f"#77 {said}\n", result.stdout)
        self.assertEqual(said, (self.state / "pr-comment").read_text())
        self.assertIn(
            'dequeuePullRequest(input: {id: "PR_kwDOqueued"})',
            (self.state / "dequeue-request").read_text(),
        )
        self.assertEqual(
            [
                *before,
                "pr view release/next",
                f"GET repos/{FORGE_REPO}/compare/{self.OLD_PLAN}...{self.PLANNED}",
                "graphql dequeuePullRequest",
                move,
                "pr list",
                "pr edit 77",
                "pr comment 77",
                f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next",
            ],
            self.calls(),
        )
        self.assertFalse(any("enqueue" in c.lower() for c in self.calls()))
        body = (self.state / "body").read_text(encoding="utf-8")
        self.assertEqual(f"Planned-At: {self.PLANNED}", body.splitlines()[-1])

    def test_a_refused_dequeue_takes_the_permitted_response(self) -> None:
        """The forge refusing the App the dequeue: exit 0, by name (#1504)."""
        _, before = self.queue_release_pr()
        (self.state / "dequeue").write_text("forbidden")
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "the forge refused to take #77 out of the merge queue; it stays"
            f" planned at {self.OLD_PLAN}; the next push to the default branch"
            " regenerates it\n",
            result.stdout,
        )
        self.assertEqual(
            [
                *before,
                "pr view release/next",
                f"GET repos/{FORGE_REPO}/compare/{self.OLD_PLAN}...{self.PLANNED}",
                "graphql dequeuePullRequest",
                f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next",
            ],
            self.calls(),
        )

    def test_a_dequeue_that_fails_otherwise_is_red(self) -> None:
        """A 502 on the dequeue is a failure by name, not a permission."""
        self.queue_release_pr()
        (self.state / "dequeue").write_text("fails")
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual("", result.stdout)
        self.assertIn(
            "FAIL: could not take #77 out of the merge queue\n"
            "HTTP/2.0 502 Bad Gateway\n",
            result.stderr,
        )

    def test_any_other_refusal_to_move_the_branch_still_fails(self) -> None:
        """Only the queue's 422 is let through; a 403 on the move is red."""
        move = self.queue_release_branch()
        (self.state / "refuse").write_text(move + "\n")
        self.forge(self.PLANNED)
        result = self.run_executor()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual("", result.stdout)
        self.assertIn(
            f"FAIL: could not move release/next to the release commit {self.OID}\n"
            "HTTP/2.0 403 Forbidden\n",
            result.stderr,
        )

    def test_a_head_read_that_fails_is_never_read_as_unchanged(self) -> None:
        """No answer, or an answer that is not a commit, fails by name."""
        for label, head in (("refused", None), ("null", "null"), ("empty", "")):
            with self.subTest(read=label):
                (self.state / "calls").unlink(missing_ok=True)
                self.forge(head)
                result = self.run_executor()
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(
                    f"FAIL: could not read the default branch head of {self.REPO}",
                    result.stderr,
                )
                self.assertEqual("", result.stdout)
                self.assertEqual(["graphql head"], self.calls())

    @unittest.skipUnless(OPEN_PR_PARSES, "this bash cannot parse the PR body")
    @unittest.skipUnless(GIT, "the refresh compares against a checkout")
    def test_a_release_refresh_compares_the_checked_out_commit(self) -> None:
        """On release: published, GITHUB_SHA is the tag; HEAD is the plan's."""
        repo = self.root / "repo"
        repo.mkdir()
        home = self.root / ".home"
        home.mkdir()

        def git(*args: str) -> str:
            # ruff: ignore[subprocess-without-shell-equals-true]
            return subprocess.run(
                [GIT, *args],
                cwd=repo,
                env=hermetic_env(home),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q", ".")
        git("config", "commit.gpgSign", "false")
        git("config", "core.hooksPath", str(repo / ".githooks-none"))
        git("commit", "-q", "--allow-empty", "-m", "fix: checked out")
        checked_out = git("rev-parse", "HEAD")
        tagged = self.PLANNED
        self.assertNotEqual(tagged, checked_out)
        cases = (
            ("head is the checkout", checked_out, 0, "opened the release PR"),
            ("head is the tag", tagged, 0, f"main moved to {tagged}"),
        )
        for label, head, status, said in cases:
            with self.subTest(case=label):
                (self.state / "calls").unlink(missing_ok=True)
                self.forge(head)
                result = self.run_executor(
                    cwd=repo, GITHUB_EVENT_NAME="release", GITHUB_SHA=tagged
                )
                self.assertEqual(status, result.returncode, result.stderr)
                self.assertIn(said, result.stdout)
                expected = (
                    list(self.PROCEEDED) if head == checked_out else ["graphql head"]
                )
                self.assertEqual(expected, self.calls())


class CloseReleasePr(ReleaseForge, unittest.TestCase):
    """Nothing to release means no Release PR (#1260).

    The second half of the edtf measurement: the later run that found
    nothing to release left the stale v1.3.4 PR standing. Each row runs
    the real close-release-pr.sh against the stubbed forge and asserts
    the calls it made and what it said, not only how it exited.
    """

    BRANCH = "release/next"

    def close(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Run close-release-pr.sh for a push of PLANNED.

        Returns:
            The completed process.

        """
        inputs = self.stubbed()
        inputs.update(RELEASE_BRANCH=self.BRANCH)
        inputs.update(env)
        return run_script("close-release-pr.sh", self.root, **inputs)

    def comment(self, number: int) -> str:
        """Read back the comment a close carried.

        Returns:
            The comment text, or the empty string when none was closed.

        """
        path = self.state / f"comment-{number}"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_an_open_release_pr_is_closed_with_the_comment(self) -> None:
        """The stale PR is closed, once, saying why."""
        self.forge(self.PLANNED)
        (self.state / "open").write_text("245\n")
        result = self.close()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["graphql head", "pr list", "pr close 245"], self.calls())
        self.assertEqual(
            f"main holds nothing to release at {self.PLANNED};"
            " closed by the release workflow",
            self.comment(245),
        )
        self.assertIn("closed PR #245", result.stdout)

    def test_no_open_release_pr_writes_nothing(self) -> None:
        """The quiet case says so and makes no write."""
        self.forge(self.PLANNED)
        result = self.close()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"no open Release PR on {self.BRANCH}; nothing to close\n", result.stdout
        )
        self.assertEqual(["graphql head", "pr list"], self.calls())

    def test_head_moved_closes_nothing(self) -> None:
        """A stale run must not close a PR a newer run just opened."""
        self.forge(self.MOVED)
        (self.state / "open").write_text("246\n")
        result = self.close()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            f"main moved to {self.MOVED} while this run was planning;"
            " the run for that commit maintains the Release PR\n",
            result.stdout,
        )
        self.assertEqual(["graphql head"], self.calls())
        self.assertEqual("", self.comment(246))

    def test_a_head_read_that_fails_closes_nothing_and_fails(self) -> None:
        """The same guard as the open path: an unread head is not a match."""
        self.forge(None)
        (self.state / "open").write_text("247\n")
        result = self.close()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            f"FAIL: could not read the default branch head of {self.REPO}",
            result.stderr,
        )
        self.assertEqual(["graphql head"], self.calls())

    def test_a_list_that_fails_is_never_read_as_none_open(self) -> None:
        """An unread list would leave the stale PR looking like the quiet case."""
        self.forge(self.PLANNED)
        (self.state / "list-fails").write_text("")
        result = self.close()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            f"FAIL: could not list the open pull requests on {self.BRANCH}",
            result.stderr,
        )
        self.assertNotIn("nothing to close", result.stdout)
        self.assertEqual(["graphql head", "pr list"], self.calls())

    def test_each_required_input_is_named_when_it_is_missing(self) -> None:
        """No default branch name, repository or commit: each is named."""
        self.forge(self.PLANNED)
        for missing in (
            "RELEASE_BRANCH",
            "GH_TOKEN",
            "GITHUB_REPOSITORY",
            "GITHUB_SHA",
        ):
            with self.subTest(missing=missing):
                (self.state / "calls").unlink(missing_ok=True)
                inputs = self.stubbed()
                inputs.update(RELEASE_BRANCH=self.BRANCH)
                del inputs[missing]
                result = run_script("close-release-pr.sh", self.root, **inputs)
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(missing, result.stderr)
                self.assertEqual([], self.calls())


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


# A stand-in for gitsign: git's x509 signing contract is the signature on
# stdout and a SIG_CREATED line on the status fd. Nothing here verifies
# the signature; the rows are about what happens after it is made.
GITSIGN_STUB = """#!/usr/bin/env bash
cat > /dev/null
printf '\\n[GNUPG:] SIG_CREATED ' >&2
printf -- '-----BEGIN SIGNED MESSAGE-----\\nAA==\\n-----END SIGNED MESSAGE-----\\n'
"""

# GitHub's refusal, as edtf's release run 43786670848 printed it, sent by
# a pre-receive hook so git relays it with the same `remote:` prefix.
WORKFLOW_REFUSAL = (
    "error: GH013: Repository rule violations found for refs/tags/v1.1.0.\n"
    "- refusing to allow a GitHub App to create or update workflow"
    " `.github/workflows/audit.yml` without `workflows` permission\n"
)

TAG_REFUSED = (
    "::error::the push of v1.1.0 was refused: a workflow-changing commit"
    " reached the default branch after the release commit, so its workflow"
    " files differ from the release commit's, and GitHub will not let the"
    " tag-mint App create a ref that would change them. v1.1.0 has not been"
    " minted and 1.1.0 is not spent. Land one change on the default branch"
    " that makes its workflow files equal to the release commit's again (the"
    " commit that moved them, reverted), then re-run this job"
    " (docs/runbook.md).\n"
)


@unittest.skipUnless(STELE, "stele derives the plan tag-release.sh reads")
class TagReleasePush(unittest.TestCase):
    """What `tag-release.sh` says when the tag push itself fails (#1322).

    The rows reach the push for real: a release commit the plan accepts,
    a stand-in signer, and an `origin` whose pre-receive hook answers
    the way GitHub answered edtf's v1.3.5 push.
    """

    def _run(self, hook: str) -> tuple[subprocess.CompletedProcess[str], str, str]:
        """Run the tag step against a remote whose hook prints `hook`.

        Returns:
            The completed process, the fixture's local tags after it,
            and the remote's tags after it.

        """
        root = Path(self.enterContext(TemporaryDirectory()))
        home = root / "home"
        home.mkdir()
        env = hermetic_env(home)
        remote = root / "remote.git"
        repo = root / "repo"
        repo.mkdir()

        def git(cwd: Path, *args: str) -> str:
            # ruff: ignore[subprocess-without-shell-equals-true]
            return subprocess.run(
                [GIT, *args],
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        git(root, "init", "-q", "--bare", str(remote))
        if hook:
            pre = remote / "hooks" / "pre-receive"
            pre.write_text(f"#!/usr/bin/env bash\nprintf %s {hook!r} >&2\nexit 1\n")
            pre.chmod(0o755)
        git(repo, "init", "-q", ".")
        git(repo, "config", "commit.gpgSign", "false")
        git(repo, "config", "core.hooksPath", str(repo / ".githooks-none"))
        (repo / "a.txt").write_text("x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feat: initial")
        git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
        (repo / "b.txt").write_text("y\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feat: a change")
        git(repo, "commit", "-q", "--allow-empty", "-m", "chore: release v1.1.0")
        git(repo, "remote", "add", "origin", str(remote))
        stubs = root / "stubs"
        stubs.mkdir()
        (stubs / "gitsign").write_text(GITSIGN_STUB)
        (stubs / "gitsign").chmod(0o755)
        # The draft release after an accepted push needs the forge; the
        # stand-in refuses, so no row can reach the network.
        (stubs / "gh").write_text("#!/usr/bin/env bash\nexit 1\n")
        (stubs / "gh").chmod(0o755)
        (repo / ".rt").mkdir()
        result = run_script(
            "tag-release.sh",
            repo,
            GITHUB_REPOSITORY="monumental-archive/under-test",
            RUNNER_TEMP=str(repo / ".rt"),
            PATH=f"{stubs}:{os.environ['PATH']}",
        )
        local = git(repo, "tag", "--list", "v1.1.0")
        pushed = git(remote, "tag", "--list", "v1.1.0")
        self.pushed_tag = git(remote, "cat-file", "tag", "v1.1.0") if pushed else ""
        return result, local, pushed

    def test_the_workflow_refusal_is_named_and_mints_nothing(self) -> None:
        """GitHub's workflows-permission refusal becomes one ::error and a remedy."""
        result, local, pushed = self._run(WORKFLOW_REFUSAL)
        self.assertEqual(1, result.returncode, result.stderr)
        errors = [ln for ln in result.stdout.splitlines() if ln.startswith("::")]
        self.assertEqual([TAG_REFUSED.rstrip("\n")], errors)
        self.assertTrue(result.stdout.endswith(TAG_REFUSED), result.stdout)
        self.assertNotIn("pushed v1.1.0", result.stdout)
        self.assertNotIn("GH013", result.stderr)
        self.assertEqual("", local)
        self.assertEqual("", pushed)

    def test_an_unrelated_push_failure_keeps_gits_own_output(self) -> None:
        """Any other refusal passes through as before: git's text, git's status."""
        result, local, pushed = self._run("error: some other rule said no\n")
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("remote: error: some other rule said no", result.stderr)
        self.assertIn("[remote rejected] v1.1.0 -> v1.1.0", result.stderr)
        self.assertNotIn("::error::", result.stdout)
        self.assertNotIn("pushed v1.1.0", result.stdout)
        self.assertEqual("v1.1.0\n", local)
        self.assertEqual("", pushed)

    def test_an_accepted_push_says_so(self) -> None:
        """The baseline: with no refusal the tag lands and the script says so.

        It stops at the draft release, which needs the forge; the row is
        about the push.
        """
        result, _, pushed = self._run("")
        self.assertIn("pushed v1.1.0 (signed)", result.stdout)
        self.assertEqual("v1.1.0\n", pushed)

    def test_the_pushed_tag_carries_the_signers_signature(self) -> None:
        """The tag is signed through gitsign, not merely annotated (#349 S4).

        An unsigned annotated tag minted by the App and one minted by
        hand at break-glass were indistinguishable by inspection; the
        x509 signature in the tag object is what tells them apart.
        """
        result, _, pushed = self._run("")
        self.assertEqual("v1.1.0\n", pushed, result.stderr)
        self.assertIn(
            "-----BEGIN SIGNED MESSAGE-----", self.pushed_tag, self.pushed_tag
        )


# The forge for the mint guard's rows: the release listing from a
# captured fixture, and `gh release view` answered as GitHub would, so a
# row run against the inline guard this replaced fails on its decision
# rather than on a refused call. Everything else (the draft release after
# a push) is refused.
MINT_FORGE_STUB = """#!/usr/bin/env bash
expr="."
tag=""
verb="$1 $2"
shift 2
while (($#)); do
  case "$1" in
    --jq) expr="$2"; shift ;;
    --json | --repo) shift ;;
    -*) ;;
    *) tag="$1" ;;
  esac
  shift
done
[[ -n ${STUB_LISTING} ]] || { echo "HTTP 502" >&2; exit 1; }
case "${verb}" in
  api\\ *) jq -r "${expr}" "${STUB_LISTING}" ;;
  "release view")
    if [[ -z ${tag} ]]; then printf '%s\\n' "${STUB_LATEST}"; exit 0; fi
    jq --arg t "${tag}" 'first(.[] | select(.tag_name == $t))' "${STUB_LISTING}" \\
      | jq -r "${expr}"
    ;;
  *) exit 1 ;;
esac
"""


@unittest.skipUnless(STELE, "stele derives the plan tag-release.sh reads")
@unittest.skipUnless(JQ, "jq drives the forge stub")
class TagReleaseUpgradePath(unittest.TestCase):
    """The mint guard acts on previous_release.py's predecessor (#1592).

    edtf's captures: its release listing and its extension's `sql/`
    directory at a tag (testdata/edtf-*). The guard used GitHub's latest
    release and a `v` on its tag, so an imported scheme passed unread and
    a maintenance release marked latest was checked in place of the
    newest. Each row reaches the push or stops before signing.
    """

    def _run(
        self, listing: str, sql_at: str, latest: str, subject: str, *extra: str
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        """Mint over edtf's `sql/` at `sql_at`: a feature, then `subject`.

        Returns:
            The completed process, and the remote's tags after it.

        """
        root = Path(self.enterContext(TemporaryDirectory()))
        home = root / "home"
        home.mkdir()
        env = hermetic_env(home)
        remote = root / "remote.git"
        repo = root / "repo"
        sql = repo / "crates" / "edtf-postgres" / "sql"
        sql.mkdir(parents=True)

        def git(cwd: Path, *args: str) -> str:
            # ruff: ignore[subprocess-without-shell-equals-true]
            return subprocess.run(
                [GIT, *args],
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        git(root, "init", "-q", "--bare", str(remote))
        (sql.parent / "edtf_postgres.control").write_text("default_version = ''\n")
        for name in (TESTDATA / f"edtf-sql-{sql_at}.txt").read_text().split():
            (sql / name).write_text("")
        git(repo, "init", "-q", ".")
        git(repo, "config", "commit.gpgSign", "false")
        git(repo, "config", "core.hooksPath", str(repo / ".githooks-none"))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feat: initial")
        git(repo, "tag", "-a", sql_at, "-m", sql_at)
        (repo / "b.txt").write_text("y\n")
        for name in extra:
            (sql / name).write_text("")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feat: a change")
        git(repo, "commit", "-q", "--allow-empty", "-m", subject)
        git(repo, "remote", "add", "origin", str(remote))
        stubs = root / "stubs"
        stubs.mkdir()
        for name, body in (("gitsign", GITSIGN_STUB), ("gh", MINT_FORGE_STUB)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        (repo / ".rt").mkdir()
        result = run_script(
            "tag-release.sh",
            repo,
            GITHUB_REPOSITORY="monumental-archive/edtf",
            RUNNER_TEMP=str(repo / ".rt"),
            PATH=f"{stubs}:{os.environ['PATH']}",
            STUB_LISTING=str(TESTDATA / listing) if listing else "",
            STUB_LATEST=latest,
        )
        return result, git(remote, "tag", "--list")

    def test_an_imported_predecessor_without_a_path_refuses_the_mint(self) -> None:
        """No 1.2.3--1.3.0 path: refused, naming `edtf-postgres-v1.2.3`."""
        result, pushed = self._run(
            "edtf-releases.json",
            "v1.2.3",
            "edtf-postgres-v1.2.3",
            "chore: release v1.3.0",
        )
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "FAIL: refusing to mint v1.3.0 — no upgrade path"
            " crates/edtf-postgres/sql/edtf_postgres--1.2.3--1.3.0.sql from"
            " published 1.2.3 (edtf-postgres-v1.2.3)\n"
            "  The Release PR derived its path before edtf-postgres-v1.2.3"
            " finished publishing\n",
            result.stderr,
        )
        self.assertEqual("", pushed)

    def test_the_path_from_the_newest_mints_past_an_older_latest(self) -> None:
        """Latest is v1.3.4; the path from 1.3.5 is the one owed, and is there."""
        result, pushed = self._run(
            "edtf-releases-v1.3.5.json",
            "v1.3.5",
            "v1.3.4",
            "chore: release v1.4.0",
            "edtf_postgres--1.3.5--1.4.0.sql",
        )
        self.assertIn("pushed v1.4.0 (signed)", result.stdout, result.stderr)
        self.assertIn("v1.4.0\n", pushed)

    def test_an_unreadable_listing_refuses_the_mint(self) -> None:
        """A failed listing read is a refusal by name, never a pass."""
        result, pushed = self._run("", "v1.2.3", "", "chore: release v1.3.0")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "HTTP 502\n"
            "FAIL: refusing to mint v1.3.0 — the release listing could not be"
            " read\n  to find edtf_postgres's predecessor; nothing was minted,"
            " re-run this job.\n",
            result.stderr,
        )
        self.assertEqual("", pushed)


class InventoryStepWiring(unittest.TestCase):
    """publish.yml's inventory step: what it hands its one stele call.

    The class rules this step used to hold in shell (.github#784's
    binaries-beside-a-plan refusal, the binaries and plans branches, the
    per-major coverage) are stele's now, tabled there (stele#396,
    TestDeriveInventoriesRefusals), so the rows that ran them here went
    with them (#1371). What stays canon-side is the call's wiring: the
    rows run the step's own `run:` block, extracted from the workflow,
    with `stele`, `git` and `date` stubbed — stele records its argv and
    prints a list, and GNU `date -d` is not the Mac's.
    """

    STEP = "Derive the per-artifact inventories and the release view"

    @classmethod
    def _block(cls) -> str:
        workflow = CANON / ".github/workflows/publish.yml"
        lines = workflow.read_text(encoding="utf-8").splitlines()
        name = f"- name: {cls.STEP}"
        start = next(i for i, line in enumerate(lines) if line.strip() == name)
        run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
        body = []
        indent = None
        for line in lines[run + 1 :]:
            if not line.strip():
                body.append("")
                continue
            width = len(line) - len(line.lstrip())
            if indent is None:
                indent = width
            if width < indent:
                break
            body.append(line[indent:])
        return "\n".join(body) + "\n"

    def _run(
        self, *, binaries: bool, plans: int, variants: str, refuse: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
        root = Path(self.enterContext(TemporaryDirectory()))
        stubs = root / ".stubs"
        stubs.mkdir()
        calls = root / ".stele-argv"
        (stubs / "stele").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$@\" > {calls}\n"
            + ("echo refused >&2; exit 1\n" if refuse else "echo '[\"\"]'\n"),
            encoding="utf-8",
        )
        (stubs / "git").write_text(
            "#!/usr/bin/env bash\n[[ $1 == log ]] && echo 1700000000\nexit 0\n",
            encoding="utf-8",
        )
        (stubs / "date").write_text(
            "#!/usr/bin/env bash\necho 2023-11-14T22:13:20Z\n", encoding="utf-8"
        )
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        (root / ".org-canon").mkdir()
        (root / ".org-canon" / "CITATION.cff").write_text("version: 1.69.1\n")
        for n in range(plans):
            leg = root / "plans" / f"sbom-plan-{n}"
            leg.mkdir(parents=True)
            (leg / "plan.json").write_text("[]\n", encoding="utf-8")
        bins = root / ".bins"
        bins.mkdir()
        script = root / ".step.sh"
        script.write_text(self._block(), encoding="utf-8")
        home = root / ".home"
        home.mkdir()
        extra = {"BINARIES_DIR": str(bins)} if binaries else {}
        env = hermetic_env(
            home,
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            CLASSES="go-binary",
            EVIDENCE_CLASSES="go-binary",
            VERSION="1.0.0",
            SBOM_BASENAME="",
            VARIANTS=variants,
            GITHUB_REPOSITORY="monumental-archive/tool",
            GITHUB_ENV=str(root / ".env"),
            GITHUB_OUTPUT=str(root / ".output"),
            **extra,
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        result = subprocess.run(
            [BASH, str(script)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        argv = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
        return result, argv, root

    def test_a_binaries_release_hands_stele_the_binaries(self) -> None:
        """The binaries directory goes in; the list comes out unchanged."""
        result, argv, root = self._run(binaries=True, plans=0, variants="")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["derive", "inventories"], argv[:2])
        self.assertEqual(str(root / ".bins"), argv[argv.index("--binaries-dir") + 1])
        self.assertNotIn("--variants", argv)
        self.assertEqual("1.69.1", argv[argv.index("--machinery-version") + 1])
        self.assertEqual("2023-11-14T22:13:20Z", argv[argv.index("--created") + 1])
        output = (root / ".output").read_text(encoding="utf-8")
        self.assertEqual('list=[""]\n', output)
        self.assertIn("SBOM_CREATED=2023-11-14T22:13:20Z", (root / ".env").read_text())

    def test_plans_follow_every_flag(self) -> None:
        """Go's flag parser stops at the first operand: plans go last."""
        result, argv, _ = self._run(binaries=False, plans=2, variants="16,17")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            ["plans/sbom-plan-0/plan.json", "plans/sbom-plan-1/plan.json"], argv[-2:]
        )
        self.assertEqual("16,17", argv[argv.index("--variants") + 1])
        self.assertNotIn("--binaries-dir", argv)
        self.assertTrue(all(a.startswith("plans/") for a in argv[-2:]))
        flags = [a for a in argv[:-2] if a.startswith("--")]
        self.assertTrue(flags, argv)

    def test_no_plans_hands_stele_no_operand(self) -> None:
        """An unmatched glob is no plan, never a literal path."""
        result, argv, _ = self._run(binaries=False, plans=0, variants="")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(any("sbom-plan-" in a for a in argv), argv)
        self.assertEqual(".", argv[argv.index("--tree") + 1])
        self.assertEqual("dist", argv[argv.index("--out-dir") + 1])

    def test_a_stele_refusal_fails_the_step(self) -> None:
        """Nothing is written for a refused derivation."""
        result, _, root = self._run(binaries=True, plans=0, variants="", refuse=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("refused", result.stderr)
        output = root / ".output"
        self.assertFalse(output.exists() and output.read_text(), "a list was written")


if __name__ == "__main__":
    unittest.main()

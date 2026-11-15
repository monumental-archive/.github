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


# `gh` for the Release-PR rows: a forge that answers every call
# open-release-pr.sh and close-release-pr.sh make and records each one,
# one normalised line per call, so a row can assert exactly what was
# written and in what order. A ref read answers "absent", so a run that
# proceeds creates both refs; the open pull requests are a state file,
# and a close keeps the comment it was given.
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
if [[ ${1:-} == api && ${2:-} == graphql ]]; then
  if [[ "$*" == *defaultBranchRef* ]]; then
    log graphql head
    [[ -f ${STATE}/head ]] || { echo "gh: HTTP 502" >&2; exit 1; }
    cat "${STATE}/head"
    exit 0
  fi
  if grep -q createCommitOnBranch; then
    log graphql createCommitOnBranch
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
  [[ ${method} == GET && ${path} == */git/ref/heads/* ]] || headers "${args[@]}"
  case "${method} ${path}" in
    "GET users/"*) echo 4242 ;;
    "GET repos/"*"/commits/"*) echo true ;;
    "GET repos/"*"/git/ref/heads/"*) exit 1 ;;
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
    create) log pr create ;;
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


class InventoryClassRefusal(unittest.TestCase):
    """publish.yml's inventory step, run for real over stubbed tools (#784).

    The step chooses one way to build the inventory for the whole
    release, and go-binary beside a plan-emitting class used to take the
    plan branch and publish green without the Go binaries' packages. The
    rows run the step's own `run:` block, extracted from the workflow,
    so the refusal under test is the one that ships. `stele`, `git` and
    `date` are stubs: the passing rows need only reach the end of the
    binaries branch, and GNU `date -d` is not the Mac's.
    """

    STEP = "Derive the per-artifact inventories and the release view"
    MESSAGE = (
        "::error::go-binary cannot yet ship beside rust-binary: the inventory"
        " would omit the Go binaries' packages (.github#784)"
    )

    DERIVED = "derive sbom --out dist/tool-1.0.0.spdx.json"

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

    def _run(self, classes: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        root = Path(self.enterContext(TemporaryDirectory()))
        stubs = root / ".stubs"
        stubs.mkdir()
        calls = root / ".stele-calls"
        (stubs / "stele").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$*" >> {calls}\n'
            "out=\n"
            "while [[ $# -gt 0 ]]; do [[ $1 == --out ]] && out=$2; shift; done\n"
            'echo \'{"packages":[{"name":"stub"}]}\' > "${out}"\n',
            encoding="utf-8",
        )
        (stubs / "git").write_text(
            "#!/usr/bin/env bash\n[[ $1 == log ]] && echo 1700000000\nexit 0\n",
            encoding="utf-8",
        )
        (stubs / "date").write_text(
            "#!/usr/bin/env bash\necho 2023-11-14T22:13:20Z\n",
            encoding="utf-8",
        )
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        bins = root / ".bins"
        bins.mkdir()
        (bins / "tool_linux_amd64").write_text("binary\n", encoding="utf-8")
        script = root / ".step.sh"
        script.write_text(self._block(), encoding="utf-8")
        home = root / ".home"
        home.mkdir()
        env = hermetic_env(
            home,
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            CLASSES=classes,
            EVIDENCE_CLASSES=classes,
            VERSION="1.0.0",
            SBOM_BASENAME="tool",
            PG_MAJORS="",
            BINARIES_DIR=str(bins),
            GITHUB_REPOSITORY="monumental-archive/tool",
            GITHUB_ENV=str(root / ".env"),
            GITHUB_OUTPUT=str(root / ".output"),
            RUNNER_TEMP=str(root / ".rt"),
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
        return result, calls

    def test_go_binary_beside_a_plan_class_is_refused_by_name(self) -> None:
        """go-binary,rust-binary stops by name before anything is derived."""
        result, calls = self._run("go-binary,rust-binary")
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn(self.MESSAGE, result.stdout.splitlines())
        self.assertFalse(calls.exists(), "refused after an inventory was derived")

    def test_go_binary_beside_an_image_still_derives_from_the_binaries(self) -> None:
        """go-binary,oci-image takes the binaries branch as before."""
        result, calls = self._run("go-binary,oci-image")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("release inventory: 1 packages", result.stdout)
        self.assertIn(self.DERIVED, calls.read_text(encoding="utf-8"))

    def test_go_binary_alone_still_derives_from_the_binaries(self) -> None:
        """go-binary alone takes the binaries branch as before."""
        result, calls = self._run("go-binary")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("release inventory: 1 packages", result.stdout)
        self.assertNotIn("go-binary cannot yet ship", result.stdout)
        self.assertIn(self.DERIVED, calls.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

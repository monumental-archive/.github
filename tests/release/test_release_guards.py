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

Run through the gate as `mise run test`, which `ci` collects.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
RELEASE = CANON / "release"
TESTDATA = Path(__file__).resolve().parent / "testdata"
BASH = support.tool("bash")


def dumps(value: object) -> str:
    """Encode a planted document as JSON.

    Returns:
        The JSON text.

    """
    return json.dumps(value)


def commit_input(request: Path) -> dict[str, object]:
    """Read the createCommitOnBranch input a run sent the forge.

    Returns:
        The mutation's `variables.input` object.

    """
    text = request.read_text(encoding="utf-8")
    sent = decoded.as_object(decoded.load_json(text), "the request")
    variables = decoded.as_object(sent["variables"], "variables")
    return decoded.as_object(variables["input"], "variables.input")


GIT = support.optional_tool("git")
JQ = support.optional_tool("jq")
STELE = support.optional_tool("stele")


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
# inheritance except what mise/support.py names; every input a row wants is
# supplied explicitly through `overrides`.


def hermetic_env(home: Path, *, env: support.Env, **overrides: str) -> dict[str, str]:
    """Build a fixture's environment through mise/support.py (#1508, #1514).

    Returns:
        support.env for `home`, with this harness's git identity, and
        any overrides applied last.

    """
    stated = {
        "GIT_AUTHOR_NAME": IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": IDENTITY_EMAIL,
        "GIT_COMMITTER_NAME": IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": IDENTITY_EMAIL,
    }
    return env(home, **(stated | overrides))


def run_script(
    name: str, root: Path, *, make_env: support.Env, **env: str
) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        [BASH, str(RELEASE / name)],
        cwd=root,
        env=hermetic_env(home, **env, env=make_env),
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

    def __init__(self, root: str, *, env: support.Env) -> None:
        """Install the stub and default the forge to an unannotated draft."""
        self.make_env = env
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
            make_env=self.make_env,
        )

    def posted(self) -> str:
        """Read back what the script would have published.

        Returns:
            The notes body the script posted, or the empty string when
            it posted nothing — which several rows assert.

        """
        posted = self.state / "posted"
        return posted.read_text(encoding="utf-8") if posted.exists() else ""


# The inputs `record-draft.sh` will not act on.
def test_a_missing_repo_is_refused(env: support.Env) -> None:
    """REPO defaults to GITHUB_REPOSITORY, and neither being set is fatal."""
    with TemporaryDirectory() as d:
        result = DraftRecorder(d, env=env).record(TAG="v1.0.0", KIND="burn")
    assert result.returncode == 1
    assert "REPO is unset" in result.stderr


def test_a_missing_tag_is_refused(env: support.Env) -> None:
    """There is no default tag: a record is about one version number."""
    with TemporaryDirectory() as d:
        result = DraftRecorder(d, env=env).record(REPO="o/r", KIND="burn")
    assert result.returncode == 1
    assert "TAG is unset" in result.stderr


def test_a_kind_outside_the_two_is_refused_by_name(env: support.Env) -> None:
    """Burn and rehearsal are different claims; a third is neither."""
    with TemporaryDirectory() as d:
        result = DraftRecorder(d, env=env).record(
            REPO="o/r", TAG="v1.0.0", KIND="cleanup"
        )
    assert result.returncode == 1
    assert "KIND must be burn or rehearsal, got 'cleanup'" in result.stderr


def test_a_read_that_fails_is_never_read_as_no_record_yet(env: support.Env) -> None:
    """Degraded-forge discipline: an unreadable release stops the script.

    Treating a failed read as "nothing there" would annotate a
    release that has already published, or write a second record
    onto an annotated draft.
    """
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.forge(exists=False)
        result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
        posted = recorder.posted()
    assert result.returncode == 1
    assert "no release at o/r@v1.0.0, or it could not be read" in result.stderr
    assert posted == ""


# The two states that are already correct, and must not be rewritten.
def test_a_published_release_is_its_own_record(env: support.Env) -> None:
    """Not a failure: exit 0, and nothing posted."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.forge(exists=True, is_draft=False)
        result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
        posted = recorder.posted()
    assert result.returncode == 0, result.stderr
    assert "a published release is its own record" in result.stdout
    assert posted == ""


def test_an_existing_record_is_left_alone(env: support.Env) -> None:
    """Idempotent: the marker is the check, so re-running is safe."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.forge(
            exists=True, body="<!-- draft-record: burn -->\nalready written\n"
        )
        result = recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
        posted = recorder.posted()
    assert result.returncode == 0, result.stderr
    assert "already carries a record, leaving it" in result.stdout
    assert posted == ""


# What the record SAYS, which is the whole point of writing one.
def test_a_burn_with_a_run_cites_it(env: support.Env) -> None:
    """The evidence outlives the logs, which expire at 90 days."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        result = recorder.record(
            REPO="o/r", TAG="v1.0.0", KIND="burn", RUN_URL="https://example/run/1"
        )
        posted = recorder.posted()
    assert result.returncode == 0, result.stderr
    assert "<!-- draft-record: burn -->" in posted
    assert "**Burned version — never released.**" in posted
    assert "its publish run failed before anything" in posted
    assert "- Burned by: https://example/run/1" in posted
    assert "the release notes" in posted


def test_a_burn_with_no_run_does_not_fabricate_one(env: support.Env) -> None:
    """A citation for a run that never existed is the shape audits catch.

    The sentence follows the evidence rather than the other way
    round, so the two burns are not the same claim.
    """
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
        posted = recorder.posted()
    assert "no publish run for it" in posted
    assert "- Burned by: no run recorded" in posted
    assert "its publish run failed" not in posted


def test_a_burn_leaves_the_fix_pending_until_one_is_cut(env: support.Env) -> None:
    """The fix does not exist at burn time, and the record says so."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn")
        pending = recorder.posted()
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.record(REPO="o/r", TAG="v1.0.0", KIND="burn", FIXED_FORWARD="v1.0.1")
        completed = recorder.posted()
    assert "- Fixed forward in: pending" in pending
    assert "- Fixed forward in: v1.0.1" in completed


def test_a_rehearsal_is_not_described_as_a_burn(env: support.Env) -> None:
    """A dry run leaves a draft ON PURPOSE and is otherwise identical."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        recorder.record(
            REPO="o/r", TAG="v1.0.0", KIND="rehearsal", RUN_URL="https://e/r/2"
        )
        posted = recorder.posted()
    assert "<!-- draft-record: rehearsal -->" in posted
    assert "**Rehearsal — deliberately left unpublished.**" in posted
    assert "- Rehearsed by: https://e/r/2" in posted
    assert "Burned" not in posted
    assert "Fixed forward" not in posted


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


# What a burn left on the registries, read from its job results (#1280).
#
# The uploads run beside each other after the commit point, so a run
# that dies at one may already have shipped another, and "nothing
# became public" is then false.
def burn(registries: str, *, env: support.Env) -> str:
    """Record a burn with these job results and return the body.

    Returns:
        The notes body the script posted.

    """
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=env)
        result = recorder.record(
            REPO="o/r",
            TAG="v1.0.0",
            KIND="burn",
            RUN_URL=RUN,
            REGISTRIES=registries,
        )
        posted = recorder.posted()
    assert result.returncode == 0, result.stderr
    return posted


def test_nothing_public_when_every_registry_job_skipped(env: support.Env) -> None:
    """A run that died before its commit point keeps the old sentence."""
    posted = burn(
        "crates.io=skipped\nnpm=skipped\n"
        "ghcr.io (extension images)=skipped\nghcr.io (image)=\n",
        env=env,
    )
    assert NOTHING_PUBLIC in posted
    assert "remain" not in posted


def test_nothing_public_when_no_registry_list_is_given(env: support.Env) -> None:
    """The backfill and older callers pass no list: the sentence is unchanged."""
    posted = burn("", env=env)
    assert NOTHING_PUBLIC in posted


def test_some_public_names_each_registry_that_succeeded(env: support.Env) -> None:
    """The shape edtf v1.3.3 burned in: npm and image tags out, crates.io red."""
    posted = burn(
        "crates.io=failure\nnpm=success\n"
        "ghcr.io (extension images)=success\nghcr.io (image)=skipped\n",
        env=env,
    )
    assert (
        SOME_PUBLIC_HEAD
        + "- npm\n- ghcr.io (extension images)\n"
        + "- crates.io: may be partly published\n"
        + SOME_PUBLIC_TAIL
    ) in posted
    assert "before anything" not in posted
    assert "nothing shipped under it" not in posted
    assert "ghcr.io (image)" not in posted


def test_one_partly_published_is_listed_alone(env: support.Env) -> None:
    """A failed upload is neither public nor nothing: it may be partial."""
    posted = burn("crates.io=failure\nnpm=skipped\n", env=env)
    assert (
        SOME_PUBLIC_HEAD + "- crates.io: may be partly published\n" + SOME_PUBLIC_TAIL
    ) in posted
    assert "before anything" not in posted


def test_a_cancelled_upload_may_be_partly_published(env: support.Env) -> None:
    """Cancelled mid-upload is the same claim as failed mid-upload."""
    posted = burn("npm=cancelled\n", env=env)
    assert "- npm: may be partly published\n" in posted


def refused(message: str, *, make_env: support.Env, **env: str) -> None:
    """Assert the run refuses with this message and posts nothing."""
    with TemporaryDirectory() as d:
        recorder = DraftRecorder(d, env=make_env)
        result = recorder.record(REPO="o/r", TAG="v1.0.0", **env)
        posted = recorder.posted()
    assert result.returncode == 1
    assert f"record-draft: {message}\n" == result.stderr
    assert posted == ""


def test_a_registry_list_without_a_run_is_refused(env: support.Env) -> None:
    """Job results are evidence of a run; with no run they cite nothing."""
    refused(
        "REGISTRIES is evidence of a publish run: it needs KIND=burn and RUN_URL",
        KIND="burn",
        REGISTRIES="npm=success",
        make_env=env,
    )


def test_a_registry_list_on_a_rehearsal_is_refused(env: support.Env) -> None:
    """A dry run publishes to no registry, so a list there is a wrong call."""
    refused(
        "REGISTRIES is evidence of a publish run: it needs KIND=burn and RUN_URL",
        KIND="rehearsal",
        RUN_URL=RUN,
        REGISTRIES="npm=success",
        make_env=env,
    )


def test_a_line_without_a_result_is_refused(env: support.Env) -> None:
    """A line is <registry>=<job result>, never a bare name."""
    refused(
        "REGISTRIES line 'npm' is not <registry>=<job result>",
        KIND="burn",
        RUN_URL=RUN,
        REGISTRIES="npm",
        make_env=env,
    )


def test_a_line_without_a_registry_is_refused(env: support.Env) -> None:
    """An unnamed result could not say what became public."""
    refused(
        "REGISTRIES line '=success' is not <registry>=<job result>",
        KIND="burn",
        RUN_URL=RUN,
        REGISTRIES="=success",
        make_env=env,
    )


def test_an_unknown_result_is_refused_by_name(env: support.Env) -> None:
    """A result outside GitHub's set is never guessed public or not."""
    refused(
        "REGISTRIES: npm has job result 'neutral', not success, failure,"
        " cancelled, skipped or empty",
        KIND="burn",
        RUN_URL=RUN,
        REGISTRIES="npm=neutral",
        make_env=env,
    )


# A fixture's environment is what the row gives it (#1508).
def test_the_release_job_inputs_do_not_reach_a_fixture(env: support.Env) -> None:
    """The release job exports its step inputs; no fixture sees them."""
    planted = {"VERSION": "1.69.4", "PLAN": "{}", "APP_SLUG": "canon-release"}
    with pytest.MonkeyPatch.context() as patched, TemporaryDirectory() as d:
        for name, value in planted.items():
            patched.setenv(name, value)
        environ = hermetic_env(Path(d), env=env)
    leaked = sorted(set(planted) & set(environ))
    assert list[str]() == leaked


def test_a_row_still_states_its_own_input(env: support.Env) -> None:
    """Overrides are how a row gives a fixture an input."""
    planted = {"VERSION": "1.69.4"}
    with pytest.MonkeyPatch.context() as patched, TemporaryDirectory() as d:
        for name, value in planted.items():
            patched.setenv(name, value)
        environ = hermetic_env(Path(d), VERSION="2.0.0", env=env)
    assert environ["VERSION"] == "2.0.0"


# `derive-coverage-floor.sh` decides whether it applies at all (#652).
def test_the_version_is_required(env: support.Env) -> None:
    """The floor records the release it was measured for."""
    with TemporaryDirectory() as d:
        result = run_script("derive-coverage-floor.sh", Path(d), make_env=env)
    assert result.returncode == 1
    assert "VERSION must be set" in result.stderr


def test_no_floor_file_is_an_adoption_decision_not_a_failure(env: support.Env) -> None:
    """The belt offers the mechanism; the committed file is the adoption."""
    with TemporaryDirectory() as d:
        out = Path(d) / "outputs"
        result = run_script(
            "derive-coverage-floor.sh",
            Path(d),
            VERSION="1.0.0",
            GITHUB_OUTPUT=str(out),
            make_env=env,
        )
        files = emitted(out)
    assert result.returncode == 0, result.stderr
    assert "no .coverage-floor, skipped" in result.stdout
    assert files["files"] == ""


# `rust-build.sh` validates its scope before it compiles anything.
def test_the_target_is_required(env: support.Env) -> None:
    """There is no default triple: the artifact's platform is the caller's."""
    with TemporaryDirectory() as d:
        result = run_script("rust-build.sh", Path(d), STAGE_DIR="stage", make_env=env)
    assert result.returncode == 1
    assert "rust-build: TARGET is unset" in result.stderr


def test_the_stage_directory_is_required(env: support.Env) -> None:
    """Two callers stage differently; neither inherits a default."""
    with TemporaryDirectory() as d:
        result = run_script(
            "rust-build.sh", Path(d), TARGET="x86_64-unknown-linux-musl", make_env=env
        )
    assert result.returncode == 1
    assert "rust-build: STAGE_DIR is unset" in result.stderr


def test_a_plan_class_without_a_document_prefix_is_refused(env: support.Env) -> None:
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
            make_env=env,
        )
    assert result.returncode == 1
    assert "PLAN_CLASS is set but PLAN_DOC_PREFIX is not" in result.stderr


def test_the_build_is_handed_fat_lto(env: support.Env) -> None:
    """Release builds link with fat LTO, the fourth determinism input (#778).

    Cargo's `lto = false` default still runs thin LOCAL LTO, whose
    `.llvm.<hash>` symbols differed between two clean builds of the
    same tree, so the repro gate failed on iiif-server. A stand-in
    cargo records what the build step was handed and stops there.
    """
    if not JQ:
        pytest.skip("jq reads cargo's metadata")
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
            make_env=env,
        )
        assert seen.exists(), result.stdout + result.stderr
        assert seen.read_text() == "fat"


# `open-release-pr.sh` is plumbing, and refuses to plumb blind.
REQUIRED = ("PLAN", "GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA", "APP_SLUG")


@fixture
def _set_up_test_open_release_pr_inputs() -> None:
    """Skip unless jq is installed."""
    if not JQ:
        pytest.skip("jq reads the plan document")


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


@pytest.mark.usefixtures("_set_up_test_open_release_pr_inputs")
def test_each_required_input_is_named_when_it_is_missing(env: support.Env) -> None:
    """Five inputs, five rows: a default here would plumb the wrong repo."""
    with TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text('{"version": "1.0.0"}')
        for missing in REQUIRED:
            environ = _full_env(plan)
            del environ[missing]
            result = run_script("open-release-pr.sh", Path(d), **environ, make_env=env)
            assert result.returncode == 1
            assert missing in result.stderr


@pytest.mark.usefixtures("_set_up_test_open_release_pr_inputs")
def test_a_plan_that_states_no_branch_is_refused_by_field(env: support.Env) -> None:
    """The branch, staging ref, version and subject are all the plan's.

    A plan missing any of them is not a plan this script may
    improvise around — it names the field and stops.
    """
    with TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text(
            '{"version": "1.0.0", "commit": {"subject": "chore: release v1.0.0"}}'
        )
        result = run_script(
            "open-release-pr.sh", Path(d), **_full_env(plan), make_env=env
        )
    assert result.returncode == 1
    assert "open-release-pr.sh: the plan states no branch" in result.stderr


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

REPO = FORGE_REPO
PLANNED = "a" * 40
MOVED = "b" * 40


class ReleaseForge:
    """A fixture directory with the stubbed forge first on PATH."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a fixture with a stub on PATH and an empty call log."""
        self.make_env = env
        self.root = tmp_path
        self.state = self.root / "state"
        self.state.mkdir()
        bindir = self.root / "stubbin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(RELEASE_FORGE_STUB)
        gh.chmod(0o755)
        self.bindir = bindir

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
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SHA": PLANNED,
            "GITHUB_EVENT_NAME": "push",
        }


@fixture
def release_forge(tmp_path: Path, env: support.Env) -> ReleaseForge:
    """Give each row its own ReleaseForge."""
    return ReleaseForge(tmp_path, env=env)


OID = "c" * 40
PROCEEDED: tuple[str, ...] = (
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

OLD_PLAN = "d" * 40


class OpenReleasePrHead(ReleaseForge):
    """A run acts only for the commit the default branch is at (#1260).

    Measured on edtf: a maintain job planned at one commit, the Release
    PR merged ten minutes into its run, and the job then opened a second
    Release PR for the version that had just shipped. Every row here
    runs the real script against a stubbed forge.
    """

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out the stubbed forge and a plan with something to release."""
        super().__init__(tmp_path, env=env)
        if not JQ:
            pytest.skip("jq reads the plan document")
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
        inputs.update(OID=OID, PLAN=str(self.plan), APP_SLUG="org-tag-mint")
        inputs.update(env)
        return run_script(
            "open-release-pr.sh", cwd or self.root, **inputs, make_env=self.make_env
        )

    def queue_release_branch(self) -> str:
        """Make release/next exist, so the run moves it rather than creating it.

        Returns:
            The forge call that moves it.

        """
        ref = f"repos/{FORGE_REPO}/git/ref/heads/release/next"
        (self.state / "exists").write_text(ref + "\n")
        return f"PATCH repos/{FORGE_REPO}/git/refs/heads/release/next"

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
            dumps({
                "number": 77,
                "id": "PR_kwDOqueued",
                "body": f"Notes.\n\nPlanned-At: {OLD_PLAN}\n",
            })
        )
        return move, [*PROCEEDED[:7], move]


@fixture
def open_release_pr_head(tmp_path: Path, env: support.Env) -> OpenReleasePrHead:
    """Give each row its own OpenReleasePrHead."""
    return OpenReleasePrHead(tmp_path, env=env)


@pytest.mark.usefixtures("_forge")
def test_head_at_the_planned_commit_proceeds_as_today(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The branch has not moved: the PR is opened exactly as before."""
    if not OPEN_PR_PARSES:
        pytest.skip("this bash cannot parse the PR body")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 0, result.stderr
    assert "opened the release PR for v1.0.0" in result.stdout
    assert "moved" not in result.stdout
    want = list(PROCEEDED)
    assert want == open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_a_refused_call_names_itself_and_what_the_forge_said(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
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
            "create the staging ref release-staging/next at " + PLANNED,
            list(PROCEEDED[:3]),
        ),
        (
            "GET users/org-tag-mint%5Bbot%5D",
            "read the bot user of org-tag-mint",
            [*PROCEEDED[:4], staging],
        ),
        (
            "graphql createCommitOnBranch",
            "create the release commit on release-staging/next",
            [*PROCEEDED[:5], staging],
        ),
        (
            f"GET repos/{FORGE_REPO}/commits/{OID}",
            "read back the release commit " + OID,
            [*PROCEEDED[:6], staging],
        ),
    )
    for refuse, what, calls in rows:
        (open_release_pr_head.state / "calls").unlink(missing_ok=True)
        (open_release_pr_head.state / "refuse").write_text(refuse + "\n")
        open_release_pr_head.forge(PLANNED)
        result = open_release_pr_head.run_executor()
        assert result.returncode == 1, result.stdout
        assert (
            "gh: Resource not accessible by integration (HTTP 403)\n"
            f"open-release-pr.sh: could not {what}\n"
            "  HTTP/2.0 403 Forbidden\n"
            "  X-Github-Request-Id: STUB:633\n"
            "  X-Accepted-Github-Permissions: contents=write\n"
        ) == result.stderr
        assert result.stdout == ""
        assert calls == open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_the_sign_off_names_the_app_bot_without_the_user_endpoint(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The release commit signs off as the App's bot user (#58).

    An installation token has no user context, so `gh api user`
    answers 403 and the Release PR was never opened. The identity
    comes from APP_SLUG and the public users endpoint instead, and
    `lint:dco` matches it against the commit author verbatim.
    """
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    request = open_release_pr_head.state / "commit-request"
    assert request.exists(), "no release commit was requested:\n" + result.stderr
    message = decoded.as_object(commit_input(request)["message"], "message")
    assert message["body"] == (
        "Signed-off-by: org-tag-mint[bot]"
        " <4242+org-tag-mint[bot]@users.noreply.github.com>"
    )
    assert "GET user" not in open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_the_old_half_of_a_rename_rides_the_commit_as_a_deletion(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """A planned path absent from disk is deleted, not read (#101).

    The bump renames an upgrade script, and the plan names both
    halves. The release lab's first run of the rename (run
    31408916140) died with FileNotFoundError reading the old path
    as an addition.
    """
    old = "sql/demo--1.0.0--next.sql"
    new = "sql/demo--1.0.0--1.1.0.sql"
    plan = decoded.as_object(
        decoded.load_json(open_release_pr_head.plan.read_text()), "plan"
    )
    decoded.as_object(plan["commit"], "commit")["additions"] = [old, new]
    open_release_pr_head.plan.write_text(json.dumps(plan))
    (open_release_pr_head.root / "sql").mkdir()
    (open_release_pr_head.root / new).write_text("-- upgrade\n")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    request = open_release_pr_head.state / "commit-request"
    assert request.exists(), "no release commit was requested:\n" + result.stderr
    changes = decoded.as_object(commit_input(request)["fileChanges"], "fileChanges")
    additions = decoded.as_array(changes["additions"], "additions")
    paths = [decoded.as_object(a, "an addition")["path"] for a in additions]
    added: list[object] = [new]
    assert added == paths
    deleted: list[object] = [{"path": old}]
    assert deleted == changes["deletions"]


@pytest.mark.usefixtures("_forge")
def test_head_moved_writes_nothing_and_says_why(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The measured race: the branch moved while the run was planning."""
    open_release_pr_head.forge(MOVED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 0, result.stderr
    assert (
        f"main moved to {MOVED} while this run was planning;"
        " the run for that commit maintains the Release PR\n"
    ) == result.stdout
    want: list[str] = ["graphql head"]
    assert want == open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_the_body_says_where_the_release_was_planned(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The PR body ends in `Planned-At: <planned commit>` (#1472).

    The squash takes the body, so the release commit carries the
    commit it was planned from, and lint:release-planned reads it.
    """
    if not OPEN_PR_PARSES:
        pytest.skip("this bash cannot parse the PR body")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 0, result.stderr
    body = (open_release_pr_head.state / "body").read_text(encoding="utf-8")
    assert f"Planned-At: {PLANNED}" == body.splitlines()[-1]
    assert body.count("Planned-At:") == 1


@pytest.mark.usefixtures("_forge")
def test_a_queued_release_pr_is_dequeued_and_regenerated(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The 422 for a queued branch: dequeue, move, say so (#1504).

    Measured on the canon: Release PR #1476 was queued and removed four
    times in an hour, planned at a commit main had passed, because the
    run that met this 422 exited without moving it and no later push
    came. Main's script stops at the 422.
    """
    if not OPEN_PR_PARSES:
        pytest.skip("this bash cannot parse the PR body")
    move, before = open_release_pr_head.queue_release_pr()
    (open_release_pr_head.state / "ahead").write_text("3\n")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 0, result.stderr
    said = (
        "taken out of the merge queue: 3 commits landed after this release"
        f" was planned; regenerated at {PLANNED}; queue it again"
    )
    assert f"#77 {said}\n" in result.stdout
    assert said == (open_release_pr_head.state / "pr-comment").read_text()
    assert (
        'dequeuePullRequest(input: {id: "PR_kwDOqueued"})'
        in (open_release_pr_head.state / "dequeue-request").read_text()
    )
    want: list[str] = [
        *before,
        "pr view release/next",
        f"GET repos/{FORGE_REPO}/compare/{OLD_PLAN}...{PLANNED}",
        "graphql dequeuePullRequest",
        move,
        "pr list",
        "pr edit 77",
        "pr comment 77",
        f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next",
    ]
    assert want == open_release_pr_head.calls()
    assert not any("enqueue" in c.lower() for c in open_release_pr_head.calls())
    body = (open_release_pr_head.state / "body").read_text(encoding="utf-8")
    assert f"Planned-At: {PLANNED}" == body.splitlines()[-1]


@pytest.mark.usefixtures("_forge")
def test_a_refused_dequeue_takes_the_permitted_response(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """The forge refusing the App the dequeue: exit 0, by name (#1504)."""
    _, before = open_release_pr_head.queue_release_pr()
    (open_release_pr_head.state / "dequeue").write_text("forbidden")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 0, result.stderr
    assert (
        "the forge refused to take #77 out of the merge queue; it stays"
        f" planned at {OLD_PLAN}; the next push to the default branch"
        " regenerates it\n"
    ) == result.stdout
    want: list[str] = [
        *before,
        "pr view release/next",
        f"GET repos/{FORGE_REPO}/compare/{OLD_PLAN}...{PLANNED}",
        "graphql dequeuePullRequest",
        f"DELETE repos/{FORGE_REPO}/git/refs/heads/release-staging/next",
    ]
    assert want == open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_a_dequeue_that_fails_otherwise_is_red(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """A 502 on the dequeue is a failure by name, not a permission."""
    open_release_pr_head.queue_release_pr()
    (open_release_pr_head.state / "dequeue").write_text("fails")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 1, result.stdout
    assert result.stdout == ""
    assert (
        "open-release-pr.sh: could not take #77 out of the merge queue\n"
        "  HTTP/2.0 502 Bad Gateway\n"
    ) in result.stderr


@pytest.mark.usefixtures("_forge")
def test_any_other_refusal_to_move_the_branch_still_fails(
    open_release_pr_head: OpenReleasePrHead,
) -> None:
    """Only the queue's 422 is let through; a 403 on the move is red."""
    move = open_release_pr_head.queue_release_branch()
    (open_release_pr_head.state / "refuse").write_text(move + "\n")
    open_release_pr_head.forge(PLANNED)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 1, result.stdout
    assert result.stdout == ""
    assert (
        f"open-release-pr.sh: could not move release/next to the release commit"
        f" {OID}\n"
        "  HTTP/2.0 403 Forbidden\n"
    ) in result.stderr


@pytest.mark.usefixtures("_forge")
@pytest.mark.parametrize(
    ("label", "head"), [("refused", None), ("null", "null"), ("empty", "")]
)
def test_a_head_read_that_fails_is_never_read_as_unchanged(
    open_release_pr_head: OpenReleasePrHead, label: object, head: object
) -> None:
    """No answer, or an answer that is not a commit, fails by name."""
    (open_release_pr_head.state / "calls").unlink(missing_ok=True)
    open_release_pr_head.forge(head)
    result = open_release_pr_head.run_executor()
    assert result.returncode == 1, result.stdout
    assert (
        f"open-release-pr.sh: could not read the default branch head of {REPO}"
    ) in result.stderr
    assert result.stdout == ""
    want: list[str] = ["graphql head"]
    assert want == open_release_pr_head.calls()


@pytest.mark.usefixtures("_forge")
def test_a_release_refresh_compares_the_checked_out_commit(
    open_release_pr_head: OpenReleasePrHead, env: support.Env
) -> None:
    """On release: published, GITHUB_SHA is the tag; HEAD is the plan's."""
    if not OPEN_PR_PARSES:
        pytest.skip("this bash cannot parse the PR body")
    if not GIT:
        pytest.skip("the refresh compares against a checkout")
    repo = open_release_pr_head.root / "repo"
    repo.mkdir()
    home = open_release_pr_head.root / ".home"
    home.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            [support.tool("git"), *args],
            cwd=repo,
            env=hermetic_env(home, env=env),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q", ".")
    git("config", "commit.gpgSign", "false")
    git("config", "core.hooksPath", str(repo / ".githooks-none"))
    git("commit", "-q", "--allow-empty", "-m", "fix: checked out")
    checked_out = git("rev-parse", "HEAD")
    tagged = PLANNED
    assert tagged != checked_out
    cases = (
        ("head is the checkout", checked_out, 0, "opened the release PR"),
        ("head is the tag", tagged, 0, f"main moved to {tagged}"),
    )
    for _label, head, status, said in cases:
        (open_release_pr_head.state / "calls").unlink(missing_ok=True)
        open_release_pr_head.forge(head)
        result = open_release_pr_head.run_executor(
            cwd=repo, GITHUB_EVENT_NAME="release", GITHUB_SHA=tagged
        )
        assert status == result.returncode, result.stderr
        assert said in result.stdout
        expected = list(PROCEEDED) if head == checked_out else ["graphql head"]
        assert expected == open_release_pr_head.calls()


BRANCH = "release/next"


class CloseReleasePr(ReleaseForge):
    """Nothing to release means no Release PR (#1260).

    The second half of the edtf measurement: the later run that found
    nothing to release left the stale v1.3.4 PR standing. Each row runs
    the real close-release-pr.sh against the stubbed forge and asserts
    the calls it made and what it said, not only how it exited.
    """

    def close(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Run close-release-pr.sh for a push of PLANNED.

        Returns:
            The completed process.

        """
        inputs = self.stubbed()
        inputs.update(RELEASE_BRANCH=BRANCH)
        inputs.update(env)
        return run_script(
            "close-release-pr.sh", self.root, **inputs, make_env=self.make_env
        )

    def comment(self, number: int) -> str:
        """Read back the comment a close carried.

        Returns:
            The comment text, or the empty string when none was closed.

        """
        path = self.state / f"comment-{number}"
        return path.read_text(encoding="utf-8") if path.exists() else ""


@fixture
def close_release_pr(tmp_path: Path, env: support.Env) -> CloseReleasePr:
    """Give each row its own CloseReleasePr."""
    return CloseReleasePr(tmp_path, env=env)


@pytest.mark.usefixtures("_forge")
def test_an_open_release_pr_is_closed_with_the_comment(
    close_release_pr: CloseReleasePr,
) -> None:
    """The stale PR is closed, once, saying why."""
    close_release_pr.forge(PLANNED)
    (close_release_pr.state / "open").write_text("245\n")
    result = close_release_pr.close()
    assert result.returncode == 0, result.stderr
    want: list[str] = ["graphql head", "pr list", "pr close 245"]
    assert want == close_release_pr.calls()
    assert (
        f"main holds nothing to release at {PLANNED}; closed by the release workflow"
    ) == close_release_pr.comment(245)
    assert "closed PR #245" in result.stdout


@pytest.mark.usefixtures("_forge")
def test_no_open_release_pr_writes_nothing(close_release_pr: CloseReleasePr) -> None:
    """The quiet case says so and makes no write."""
    close_release_pr.forge(PLANNED)
    result = close_release_pr.close()
    assert result.returncode == 0, result.stderr
    assert f"no open Release PR on {BRANCH}; nothing to close\n" == result.stdout
    want: list[str] = ["graphql head", "pr list"]
    assert want == close_release_pr.calls()


@pytest.mark.usefixtures("_forge")
def test_head_moved_closes_nothing(close_release_pr: CloseReleasePr) -> None:
    """A stale run must not close a PR a newer run just opened."""
    close_release_pr.forge(MOVED)
    (close_release_pr.state / "open").write_text("246\n")
    result = close_release_pr.close()
    assert result.returncode == 0, result.stderr
    assert (
        f"main moved to {MOVED} while this run was planning;"
        " the run for that commit maintains the Release PR\n"
    ) == result.stdout
    want: list[str] = ["graphql head"]
    assert want == close_release_pr.calls()
    assert close_release_pr.comment(246) == ""


@pytest.mark.usefixtures("_forge")
def test_a_head_read_that_fails_closes_nothing_and_fails(
    close_release_pr: CloseReleasePr,
) -> None:
    """The same guard as the open path: an unread head is not a match."""
    close_release_pr.forge(None)
    (close_release_pr.state / "open").write_text("247\n")
    result = close_release_pr.close()
    assert result.returncode == 1, result.stdout
    assert (
        f"close-release-pr.sh: could not read the default branch head of {REPO}"
    ) in result.stderr
    want: list[str] = ["graphql head"]
    assert want == close_release_pr.calls()


@pytest.mark.usefixtures("_forge")
def test_a_list_that_fails_is_never_read_as_none_open(
    close_release_pr: CloseReleasePr,
) -> None:
    """An unread list would leave the stale PR looking like the quiet case."""
    close_release_pr.forge(PLANNED)
    (close_release_pr.state / "list-fails").write_text("")
    result = close_release_pr.close()
    assert result.returncode == 1, result.stdout
    assert (
        f"close-release-pr.sh: could not list the open pull requests on {BRANCH}"
    ) in result.stderr
    assert "nothing to close" not in result.stdout
    want: list[str] = ["graphql head", "pr list"]
    assert want == close_release_pr.calls()


@pytest.mark.usefixtures("_forge")
@pytest.mark.parametrize(
    "missing",
    [
        "RELEASE_BRANCH",
        "GH_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_SHA",
    ],
)
def test_close_release_pr_each_required_input_is_named_when_it_is_missing(
    close_release_pr: CloseReleasePr, missing: object, env: support.Env
) -> None:
    """No default branch name, repository or commit: each is named."""
    close_release_pr.forge(PLANNED)
    (close_release_pr.state / "calls").unlink(missing_ok=True)
    inputs = close_release_pr.stubbed()
    inputs.update(RELEASE_BRANCH=BRANCH)
    del inputs[missing]
    result = run_script(
        "close-release-pr.sh", close_release_pr.root, **inputs, make_env=env
    )
    assert result.returncode == 1, result.stdout
    assert missing in result.stderr
    assert list[str]() == close_release_pr.calls()


# `tag-release.sh` decides whether a commit may be tagged at all.
#
# Every row here stops before the signing step, which is the point:
# a tag is immutable, so each of these is a version number NOT spent.
@fixture
def _set_up_test_tag_release_refusals() -> None:
    """Nothing shared; each row builds the history it is about."""
    if not STELE:
        pytest.skip("stele derives the plan tag-release.sh reads")
    if not GIT:
        pytest.skip("the fixture is a git repository")


def _repo(root: str, *, subject: str, env: support.Env) -> Path:
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
        subprocess.run(
            [support.tool("git"), *args],
            cwd=repo,
            env=hermetic_env(home, env=env),
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


def _run(repo: Path, *, env: support.Env) -> subprocess.CompletedProcess[str]:
    """Run the tag step against a fixture repository.

    Returns:
        The completed process.

    """
    return run_script(
        "tag-release.sh",
        repo,
        GITHUB_REPOSITORY="monumental-archive/under-test",
        RUNNER_TEMP=str(repo / ".rt"),
        make_env=env,
    )


@pytest.mark.usefixtures("_set_up_test_tag_release_refusals")
def test_an_ordinary_commit_is_not_a_release_commit(env: support.Env) -> None:
    """A workflow_dispatch on main must not mint a tag for an unprepared tree.

    The manifests and changelog were never bumped, so the version
    the plan derives has nothing behind it.
    """
    with TemporaryDirectory() as d:
        repo = _repo(d, subject="feat: an ordinary change", env=env)
        (repo / ".rt").mkdir()
        result = _run(repo, env=env)
    assert result.returncode == 1, result.stdout
    assert "HEAD is not the release commit" in result.stderr
    assert "expected: chore: release v" in result.stderr


@pytest.mark.usefixtures("_set_up_test_tag_release_refusals")
def test_a_commit_already_tagged_is_a_resume_not_a_failure(env: support.Env) -> None:
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
        repo = _repo(d, subject="feat: an ordinary change", env=env)
        (repo / ".rt").mkdir()
        first = _run(repo, env=env)
        tag = ""
        for line in first.stderr.splitlines():
            if "expected: chore: release v" in line:
                tag = "v" + line.rsplit("release v", 1)[1].strip()
        assert tag, first.stderr
        subprocess.run(
            [support.tool("git"), "tag", "-a", tag, "-m", tag],
            cwd=repo,
            env=hermetic_env(repo.parent / "home", env=env),
            check=True,
            capture_output=True,
        )
        result = _run(repo, env=env)
    assert result.returncode == 0, result.stderr
    assert f"already minted, nothing to do: {tag}" in result.stdout


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
    "::error::tag-release.sh: the push of v1.1.0 was refused: a workflow-changing"
    " commit"
    " reached the default branch after the release commit, so its workflow"
    " files differ from the release commit's, and GitHub will not let the"
    " tag-mint App create a ref that would change them. v1.1.0 has not been"
    " minted and 1.1.0 is not spent. Land one change on the default branch"
    " that makes its workflow files equal to the release commit's again (the"
    " commit that moved them, reverted), then re-run this job"
    " (docs/runbook.md).\n"
)


class TagReleasePush:
    """What `tag-release.sh` says when the tag push itself fails (#1322).

    The rows reach the push for real: a release commit the plan accepts,
    a stand-in signer, and an `origin` whose pre-receive hook answers
    the way GitHub answered edtf's v1.3.5 push.
    """

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Set up the rows' state."""
        self.make_env = env
        self.set_up_tmp(tmp_path)
        self.set_up_test_tag_release_push()

    def set_up_tmp(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def set_up_test_tag_release_push(self) -> None:
        """Skip unless stele is installed."""
        if not STELE:
            pytest.skip("stele derives the plan tag-release.sh reads")

    def _run(self, hook: str) -> tuple[subprocess.CompletedProcess[str], str, str]:
        """Run the tag step against a remote whose hook prints `hook`.

        Returns:
            The completed process, the fixture's local tags after it,
            and the remote's tags after it.

        """
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        home = root / "home"
        home.mkdir()
        env = hermetic_env(home, env=self.make_env)
        remote = root / "remote.git"
        repo = root / "repo"
        repo.mkdir()

        def git(cwd: Path, *args: str) -> str:
            return subprocess.run(
                [support.tool("git"), *args],
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
            make_env=self.make_env,
        )
        local = git(repo, "tag", "--list", "v1.1.0")
        pushed = git(remote, "tag", "--list", "v1.1.0")
        self.pushed_tag = git(remote, "cat-file", "tag", "v1.1.0") if pushed else ""
        return result, local, pushed


@fixture
def tag_release_push(tmp_path: Path, env: support.Env) -> TagReleasePush:
    """Give each row its own TagReleasePush."""
    return TagReleasePush(tmp_path, env=env)


def test_the_workflow_refusal_is_named_and_mints_nothing(
    tag_release_push: TagReleasePush,
) -> None:
    """GitHub's workflows-permission refusal becomes one ::error and a remedy."""
    result, local, pushed = tag_release_push._run(WORKFLOW_REFUSAL)
    assert result.returncode == 1, result.stderr
    errors = [ln for ln in result.stderr.splitlines() if ln.startswith("::")]
    want: list[str] = [TAG_REFUSED.rstrip("\n")]
    assert want == errors
    assert result.stderr.endswith(TAG_REFUSED), result.stderr
    assert "pushed v1.1.0" not in result.stdout
    assert "GH013" not in result.stderr
    assert local == ""
    assert pushed == ""


def test_an_unrelated_push_failure_keeps_gits_own_output(
    tag_release_push: TagReleasePush,
) -> None:
    """Any other refusal passes through as before: git's text, git's status."""
    result, local, pushed = tag_release_push._run("error: some other rule said no\n")
    assert result.returncode == 1, result.stderr
    assert "remote: error: some other rule said no" in result.stderr
    assert "[remote rejected] v1.1.0 -> v1.1.0" in result.stderr
    assert "::error::" not in result.stderr
    assert "pushed v1.1.0" not in result.stdout
    assert local == "v1.1.0\n"
    assert pushed == ""


def test_an_accepted_push_says_so(tag_release_push: TagReleasePush) -> None:
    """The baseline: with no refusal the tag lands and the script says so.

    It stops at the draft release, which needs the forge; the row is
    about the push.
    """
    result, _, pushed = tag_release_push._run("")
    assert "pushed v1.1.0 (signed)" in result.stdout
    assert pushed == "v1.1.0\n"


def test_the_pushed_tag_carries_the_signers_signature(
    tag_release_push: TagReleasePush,
) -> None:
    """The tag is signed through gitsign, not merely annotated (#349 S4).

    An unsigned annotated tag minted by the App and one minted by
    hand at break-glass were indistinguishable by inspection; the
    x509 signature in the tag object is what tells them apart.
    """
    result, _, pushed = tag_release_push._run("")
    assert pushed == "v1.1.0\n", result.stderr
    assert "-----BEGIN SIGNED MESSAGE-----" in tag_release_push.pushed_tag, (
        tag_release_push.pushed_tag
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


class TagReleaseUpgradePath:
    """The mint guard acts on previous_release.py's predecessor (#1592).

    edtf's captures: its release listing and its extension's `sql/`
    directory at a tag (testdata/edtf-*). The guard used GitHub's latest
    release and a `v` on its tag, so an imported scheme passed unread and
    a maintenance release marked latest was checked in place of the
    newest. Each row reaches the push or stops before signing.
    """

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Set up the rows' state."""
        self.make_env = env
        self.set_up_tmp(tmp_path)
        self.set_up_test_tag_release_upgrade_path()

    def set_up_tmp(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def set_up_test_tag_release_upgrade_path(self) -> None:
        """Skip unless stele and jq is installed."""
        if not STELE:
            pytest.skip("stele derives the plan tag-release.sh reads")
        if not JQ:
            pytest.skip("jq drives the forge stub")

    def _run(
        self, listing: str, sql_at: str, latest: str, subject: str, *extra: str
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        """Mint over edtf's `sql/` at `sql_at`: a feature, then `subject`.

        Returns:
            The completed process, and the remote's tags after it.

        """
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        home = root / "home"
        home.mkdir()
        env = hermetic_env(home, env=self.make_env)
        remote = root / "remote.git"
        repo = root / "repo"
        sql = repo / "crates" / "edtf-postgres" / "sql"
        sql.mkdir(parents=True)

        def git(cwd: Path, *args: str) -> str:
            return subprocess.run(
                [support.tool("git"), *args],
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
            make_env=self.make_env,
        )
        return result, git(remote, "tag", "--list")


@fixture
def tag_release_upgrade_path(tmp_path: Path, env: support.Env) -> TagReleaseUpgradePath:
    """Give each row its own TagReleaseUpgradePath."""
    return TagReleaseUpgradePath(tmp_path, env=env)


def test_an_imported_predecessor_without_a_path_refuses_the_mint(
    tag_release_upgrade_path: TagReleaseUpgradePath,
) -> None:
    """No 1.2.3--1.3.0 path: refused, naming `edtf-postgres-v1.2.3`."""
    result, pushed = tag_release_upgrade_path._run(
        "edtf-releases.json",
        "v1.2.3",
        "edtf-postgres-v1.2.3",
        "chore: release v1.3.0",
    )
    assert result.returncode == 1, result.stdout
    assert (
        "tag-release.sh: refusing to mint v1.3.0 — no upgrade path"
        " crates/edtf-postgres/sql/edtf_postgres--1.2.3--1.3.0.sql from"
        " published 1.2.3 (edtf-postgres-v1.2.3)\n"
        "  The Release PR derived its path before edtf-postgres-v1.2.3"
        " finished publishing\n"
    ) in result.stderr
    assert pushed == ""


def test_the_path_from_the_newest_mints_past_an_older_latest(
    tag_release_upgrade_path: TagReleaseUpgradePath,
) -> None:
    """Latest is v1.3.4; the path from 1.3.5 is the one owed, and is there."""
    result, pushed = tag_release_upgrade_path._run(
        "edtf-releases-v1.3.5.json",
        "v1.3.5",
        "v1.3.4",
        "chore: release v1.4.0",
        "edtf_postgres--1.3.5--1.4.0.sql",
    )
    assert "pushed v1.4.0 (signed)" in result.stdout, result.stderr
    assert "v1.4.0\n" in pushed


def test_an_unreadable_listing_refuses_the_mint(
    tag_release_upgrade_path: TagReleaseUpgradePath,
) -> None:
    """A failed listing read is a refusal by name, never a pass."""
    result, pushed = tag_release_upgrade_path._run(
        "", "v1.2.3", "", "chore: release v1.3.0"
    )
    assert result.returncode == 1, result.stdout
    assert (
        "HTTP 502\n"
        "tag-release.sh: refusing to mint v1.3.0 — the release listing could not be"
        " read\n  to find edtf_postgres's predecessor; nothing was minted,"
        " re-run this job.\n"
    ) in result.stderr
    assert pushed == ""


STEP = "Derive the per-artifact inventories and the release view"


class InventoryStepWiring:
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

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.make_env = env
        self.tmp = tmp_path

    def _run(
        self, *, binaries: bool, plans: int, variants: str, refuse: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
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
        script.write_text(_block(), encoding="utf-8")
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
            env=self.make_env,
        )
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


@fixture
def inventory_step_wiring(tmp_path: Path, env: support.Env) -> InventoryStepWiring:
    """Give each row its own InventoryStepWiring."""
    return InventoryStepWiring(tmp_path, env=env)


def _block() -> str:
    workflow = CANON / ".github/workflows/publish.yml"
    lines = workflow.read_text(encoding="utf-8").splitlines()
    name = f"- name: {STEP}"
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


def test_a_binaries_release_hands_stele_the_binaries(
    inventory_step_wiring: InventoryStepWiring,
) -> None:
    """The binaries directory goes in; the list comes out unchanged."""
    result, argv, root = inventory_step_wiring._run(binaries=True, plans=0, variants="")
    assert result.returncode == 0, result.stdout + result.stderr
    want: list[str] = ["derive", "inventories"]
    assert want == argv[:2]
    assert str(root / ".bins") == argv[argv.index("--binaries-dir") + 1]
    assert "--variants" not in argv
    assert argv[argv.index("--machinery-version") + 1] == "1.69.1"
    assert argv[argv.index("--created") + 1] == "2023-11-14T22:13:20Z"
    output = (root / ".output").read_text(encoding="utf-8")
    assert output == 'list=[""]\n'
    assert "SBOM_CREATED=2023-11-14T22:13:20Z" in (root / ".env").read_text()


def test_plans_follow_every_flag(inventory_step_wiring: InventoryStepWiring) -> None:
    """Go's flag parser stops at the first operand: plans go last."""
    result, argv, _ = inventory_step_wiring._run(
        binaries=False, plans=2, variants="16,17"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    want: list[str] = ["plans/sbom-plan-0/plan.json", "plans/sbom-plan-1/plan.json"]
    assert want == argv[-2:]
    assert argv[argv.index("--variants") + 1] == "16,17"
    assert "--binaries-dir" not in argv
    assert all(a.startswith("plans/") for a in argv[-2:])
    flags = [a for a in argv[:-2] if a.startswith("--")]
    assert flags, argv


def test_no_plans_hands_stele_no_operand(
    inventory_step_wiring: InventoryStepWiring,
) -> None:
    """An unmatched glob is no plan, never a literal path."""
    result, argv, _ = inventory_step_wiring._run(binaries=False, plans=0, variants="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("sbom-plan-" in a for a in argv), argv
    assert argv[argv.index("--tree") + 1] == "."
    assert argv[argv.index("--out-dir") + 1] == "dist"


def test_a_stele_refusal_fails_the_step(
    inventory_step_wiring: InventoryStepWiring,
) -> None:
    """Nothing is written for a refused derivation."""
    result, _, root = inventory_step_wiring._run(
        binaries=True, plans=0, variants="", refuse=True
    )
    assert result.returncode != 0
    assert "refused" in result.stderr
    output = root / ".output"
    assert not (output.exists() and output.read_text()), "a list was written"

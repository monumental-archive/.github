"""The release job derives the floor from the gate's figure (#1622).

Two halves. `coverage_figures.py`'s decisions, row by row, each refusal
asserted by its message. Then `derive-coverage-floor.sh` end to end, in
a planted tree with a stubbed forge (`gh`) and a stubbed measurement
that FAILS if it is called: an artifact for the commit is read and
nothing is measured; one for another commit, or none, is refused by
name and the step measures; and the floor written is the same either
way on one recorded figure. On main before #1622 the first row fails:
the step measures with an artifact present.

The forge's answers are shaped on real ones: the artifacts listing and
a merge-group run read from monumental-archive/.github on 2026-11-20
(`gh api .../actions/artifacts?name=coverage-reports`, `.../runs`), with
the ids, commits and outcomes each row needs.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from belt import coverage_floor
from belt.release import coverage_figures as figures
from belt.typed_pytest import fixture
from tests import support

HERE = support.RELEASE
BASH = support.tool("bash")
GIT = support.tool("git")

SHA = "977d99f0ee27ca9d8a3c9c4cf5911105c88241bf"
OTHER = "2980f36dba6e88b75ecd97536850263c140a4103"
LEGS = "python 95.23\npython-branches 91.45\n"
# The launcher's command, which a finding names when no task does.
COMMAND = "coverage-figures"


def artifact(
    sha: str,
    head: str,
    *,
    ident: int = 14582536594,
    run: int = 44631546473,
    expired: bool = False,
) -> dict[str, object]:
    """Build one artifacts-listing entry, in GitHub's shape.

    Returns:
        The entry.

    """
    return {
        "expired": expired,
        "id": ident,
        "name": figures.name(sha),
        "workflow_run": {
            "head_branch": "main",
            "head_repository_id": 1328349227,
            "head_sha": head,
            "id": run,
            "repository_id": 1328349227,
        },
    }


def listing(*entries: dict[str, object]) -> dict[str, object]:
    """Build an artifacts listing.

    Returns:
        The listing.

    """
    return {"total_count": len(entries), "artifacts": list(entries)}


def run_record(
    head: str,
    *,
    event: str = "merge_group",
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, object]:
    """Build one workflow run, in GitHub's shape.

    Returns:
        The run.

    """
    return {
        "conclusion": conclusion,
        "event": event,
        "head_sha": head,
        "html_url": "https://github.com/monumental-archive/.github/actions/runs/44631546473",
        "id": 44631546473,
        "name": "gate",
        "status": status,
    }


def zipped(text: str | None, member: str = figures.FILE) -> bytes:
    """Zip a figures file as the artifact download does.

    Returns:
        The archive's bytes.

    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if text is not None:
            archive.writestr(member, text)
    return buffer.getvalue()


# Which artifact is this commit's.
def test_the_commits_own_artifact_is_chosen() -> None:
    """Named for the commit, kept by a run of the commit."""
    got = figures.choose(listing(artifact(SHA, SHA)), SHA)
    assert figures.Choice(14582536594, 44631546473) == got


def test_the_newest_of_two_is_chosen() -> None:
    """A re-run kept a second one: the newest artifact wins."""
    got = figures.choose(
        listing(
            artifact(SHA, SHA, ident=1, run=10), artifact(SHA, SHA, ident=2, run=20)
        ),
        SHA,
    )
    assert figures.Choice(2, 20) == got


@pytest.mark.parametrize(
    ("case", "given", "said"),
    [
        ("none", listing(), f"no unexpired gate artifact coverage-figures-{SHA}"),
        ("expired", listing(artifact(SHA, SHA, expired=True)), "no unexpired"),
        (
            "another commit",
            listing(artifact(SHA, OTHER)),
            f"was kept by a run of {OTHER}, not {SHA}",
        ),
        (
            "not a listing",
            ["artifacts"],
            "the artifacts listing is not one GitHub writes",
        ),
    ],
)
def test_refusals_say_why(case: object, given: object, said: object) -> None:
    """No artifact, an expired one, another commit's, a strange listing."""
    with pytest.raises(figures.RefusedError) as raised:
        figures.choose(given, SHA)
    assert said in str(raised.value)


# The run that kept the artifact judged the tree that lands.
@pytest.mark.parametrize("event", ["merge_group", "push"])
def test_a_green_merge_group_or_push_run_is_named(event: object) -> None:
    """Its address is what the step's log names."""
    assert (
        figures.judge(run_record(SHA, event=event), SHA)
        == "https://github.com/monumental-archive/.github/actions/runs/44631546473"
    )


@pytest.mark.parametrize(
    ("case", "given", "said"),
    [
        ("another commit", run_record(OTHER), f"is for {OTHER}, not {SHA}"),
        (
            "pull request",
            run_record(SHA, event="pull_request"),
            "is a pull_request run, not merge_group or push",
        ),
        (
            "in progress",
            run_record(SHA, status="in_progress", conclusion=None),
            "is in_progress/None, not completed/success",
        ),
        ("failed", run_record(SHA, conclusion="failure"), "completed/failure"),
    ],
)
def test_judge_refusals_say_why(case: object, given: object, said: object) -> None:
    """Another commit, a pull request's run, a run not green."""
    with pytest.raises(figures.RefusedError) as raised:
        figures.judge(given, SHA)
    assert said in str(raised.value)


# The file holds coverage-measure.sh's leg lines and nothing else.
def test_the_lines_come_back_as_written() -> None:
    """Read out of the zip, newline-terminated."""
    assert figures.figures(figures.unpack(zipped(LEGS))) == LEGS


def test_a_leg_not_held_is_a_leg_line() -> None:
    """`<leg> not-held` (#2163) reaches the derivation, which keeps it."""
    text = "plpgsql 95.00\nplpgsql-branches not-held\n"
    assert text == figures.figures(figures.unpack(zipped(text)))
    with pytest.raises(figures.RefusedError):
        figures.figures("plpgsql-branches held\n")


@pytest.mark.parametrize(
    ("case", "data", "said"),
    [
        ("empty", zipped(""), "coverage-figures.txt holds no leg"),
        (
            "stray line",
            zipped("python 95.23\nupload lcov.info\n"),
            "holds a line that is not a leg: 'upload lcov.info'",
        ),
        (
            "no member",
            zipped(None),
            "the artifact holds no readable coverage-figures.txt",
        ),
        (
            "not a zip",
            b"not a zip",
            "the artifact holds no readable coverage-figures.txt",
        ),
    ],
)
def test_figures_refusals_say_why(case: object, data: object, said: object) -> None:
    """Empty, a stray line, no file in the zip, not a zip."""
    with pytest.raises(figures.RefusedError) as raised:
        figures.figures(figures.unpack(data))
    assert said in str(raised.value)


# A pgrx job's figure: its own name, and exactly its leg (#1692).
def test_the_name_carries_the_leg() -> None:
    """`coverage-figures-pgrx-pg17-<sha>` beside the gate's own."""
    assert f"coverage-figures-{SHA}" == figures.name(SHA)
    assert f"coverage-figures-pgrx-pg17-{SHA}" == figures.name(SHA, "pgrx-pg17")


def test_choose_reads_the_legs_name_only() -> None:
    """The gate's artifact is not pg17's, and pg17's is."""
    gate = artifact(SHA, SHA, ident=1, run=10)
    pg17 = {
        **artifact(SHA, SHA, ident=2, run=20),
        "name": figures.name(SHA, "pgrx-pg17"),
    }
    got = figures.choose(listing(gate, pg17), SHA, "pgrx-pg17")
    assert figures.Choice(2, 20) == got
    with pytest.raises(figures.RefusedError) as raised:
        figures.choose(listing(gate), SHA, "pgrx-pg17")
    assert f"no unexpired gate artifact coverage-figures-pgrx-pg17-{SHA}" in str(
        raised.value
    )


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("pgrx-pg18 96.5\n", "holds pgrx-pg18, not exactly pgrx-pg17"),
        (
            "pgrx-pg17 96.5\npython 90.0\n",
            "holds pgrx-pg17 python, not exactly pgrx-pg17",
        ),
    ],
)
def test_exactly_the_leg(text: object, said: object) -> None:
    """Its line alone; another major's, or a second line, is refused."""
    assert figures.figures("pgrx-pg17 96.5\n", "pgrx-pg17") == "pgrx-pg17 96.5\n"
    with pytest.raises(figures.RefusedError) as raised:
        figures.figures(text, "pgrx-pg17")
    assert said in str(raised.value)


def test_the_command_line_takes_only_a_pgrx_leg() -> None:
    """A third argument is `pgrx-pg<major>` or a usage error."""
    assert figures.main(["figures", SHA, "python"]) == 2


# main(), in process: each subcommand over what is piped to it.
def main(argv: list[str], data: bytes) -> tuple[int, str, str]:
    """Run the command line with `data` as its standard input.

    Returns:
        The exit status, stdout and stderr.

    """
    with tempfile.TemporaryFile() as piped:
        piped.write(data)
        piped.seek(0)
        out, err = io.StringIO(), io.StringIO()
        with (
            pytest.MonkeyPatch.context() as patched,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            patched.setattr(figures, "STDIN", piped.fileno())
            patched.setattr(sys, "argv", [COMMAND])
            status = figures.main(argv)
    return status, out.getvalue(), err.getvalue()


def test_each_subcommand_answers() -> None:
    """choose, judge and figures print what the script reads."""
    listed = json.dumps(listing(artifact(SHA, SHA))).encode()
    ran = json.dumps(run_record(SHA)).encode()
    for argv, data, said in (
        (["choose", SHA], listed, "14582536594 44631546473\n"),
        (
            ["judge", SHA],
            ran,
            "https://github.com/monumental-archive/.github/actions/runs/44631546473\n",
        ),
        (["figures", SHA], zipped(LEGS), LEGS),
        (
            ["figures", SHA, "pgrx-pg17"],
            zipped("pgrx-pg17 96.5\n"),
            "pgrx-pg17 96.5\n",
        ),
    ):
        assert (0, said, "") == main(argv, data)


def test_a_refusal_is_one_line_and_status_one() -> None:
    """The reason, labelled, on stderr; nothing on stdout."""
    status, out, err = main(["judge", SHA], json.dumps(run_record(OTHER)).encode())
    assert (status, out) == (1, "")
    assert (
        f"release:coverage-floor: not reading the gate's figures:"
        f" run 44631546473 is for {OTHER}, not {SHA}\n"
    ) == err


def test_a_forge_answer_that_is_not_json_is_refused() -> None:
    """decoded.py's refusal becomes the step's."""
    status, _, err = main(["choose", SHA], b"<html>rate limited</html>")
    assert status == 1
    assert "the forge answered with something that is not JSON" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["choose"],
        ["choose", "abc"],
        ["measure", SHA],
        ["figures", SHA, "python"],
    ],
)
def test_usage_errors_are_status_two(argv: object) -> None:
    """No sha, a short sha, an unknown command, a leg that is not pgrx."""
    status, out, err = main(argv, b"")
    assert (status, out) == (2, "")
    assert f"{COMMAND}: usage: belt {COMMAND} " in err


# The forge: answers from files the row planted beside it, keyed by the
# artifact's name, the run's id and the artifact's id, and records every
# call. A path with no planted answer fails as gh does.
GH = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/gh.log"
case "$2" in
  */artifacts\\?name=*)
    n=${2#*name=}
    f="listing-${n%%&*}.json"
    ;;
  */runs/*) f="run-${2##*/}.json" ;;
  */artifacts/*/zip)
    a=${2%/zip}
    f="zip-${a##*/}.zip"
    ;;
  *) f=none ;;
esac
if [[ -f ${STUB_DIR}/${f} ]]; then
  cat "${STUB_DIR}/${f}"
  exit 0
fi
echo "gh: Not Found (HTTP 404)" >&2
exit 1
"""

# The measurement script, as the step calls it directly: `probe` names
# the python leg, `pgrx-majors` the planted majors, and `pgrx` prints
# the extension's planted legs, or fails loudly where the row says
# nothing may be measured. The gate's own legs are never asked of it
# outside mise (#1667): outside a run the python leg reads nothing, so
# the step measures them through `mise run coverage:check`, and a direct
# call for them here is red by name.
MEASURE = """\
#!/usr/bin/env bash
case "$2" in
  probe)
    echo python
    exit 0
    ;;
  pgrx-majors)
    [[ -z ${STUB_MAJORS:-} ]] || printf '%s\\n' ${STUB_MAJORS}
    exit 0
    ;;
  pgrx) ;;
  *)
    echo "coverage-measure.sh measured outside mise: no run to read" >&2
    exit 98
    ;;
esac
echo pgrx >> "${STUB_DIR}/measured.log"
if [[ -n ${STUB_NO_MEASURE:-} ]]; then
  echo "the stub measurement (pgrx) was called" >&2
  exit 97
fi
printf '%s' "${STUB_PGRX_LEGS}"
"""

# `mise run coverage:check`, the step's own measurement of the gate's
# legs (#1667): logged as `all`, they land in coverage-figures.txt
# under ORG_COVERAGE_REPORTS, where coverage-measure.sh's `reports`
# path writes them; or it fails loudly where the row says nothing may
# be measured.
MISE = """\
#!/usr/bin/env bash
[[ "$1 $2" == "run coverage:check" ]] || exit 2
echo all >> "${STUB_DIR}/measured.log"
if [[ -n ${STUB_NO_MEASURE:-} ]]; then
  echo "the stub measurement (all) was called" >&2
  exit 97
fi
printf '%s' "${STUB_LEGS}" > "${ORG_COVERAGE_REPORTS}/coverage-figures.txt"
"""

PGRX_LEGS = "pgrx-pg17 96.5\npgrx-pg18 96.25\n"
# What the stub measurement was asked for: the gate's legs, or the
# extension's.
MEASURED_ALL: list[str] = ["all"]
MEASURED_PGRX: list[str] = ["pgrx"]


class Derive:
    """derive-coverage-floor.sh, read or measured, in a planted tree."""

    def __init__(self, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
        """Plant the canon's two scripts, the stubs, and a tree with a floor."""
        self.base = Path(str(tmp_path))
        # The canon whole, as the release job has it: a helper that comes
        # to import another belt module finds it, and only the
        # measurement is a stub.
        canon = support.canon(
            self.base / "canon", stubs={"mise/coverage-measure.sh": MEASURE}
        )
        (canon / "mise" / "coverage-measure.sh").chmod(0o755)
        self.stubs = self.base / "stubs"
        self.stubs.mkdir()
        for path, text in ((self.stubs / "gh", GH), (self.stubs / "mise", MISE)):
            path.write_text(text, encoding="utf-8")
            path.chmod(0o755)
        self.script = canon / "release" / "derive-coverage-floor.sh"
        self.tree = self.base / "tree"
        self.tree.mkdir()
        (self.tree / "a.py").write_text("", encoding="utf-8")
        env = support.env(self.base / "home")
        with pytest.MonkeyPatch.context() as patched:
            patched.chdir(self.tree)
            patched.setattr(sys, "stdin", io.StringIO(LEGS))
            assert not coverage_floor.main([
                "write",
                "--adopt",
                "--provenance",
                "v1.0.0",
            ])
        capfd.readouterr()
        for argv in (
            [GIT, "init", "-q"],
            [GIT, "add", "-A"],
            [GIT, "commit", "-qm", "plant"],
        ):
            subprocess.run(argv, cwd=self.tree, env=env, check=True)
        self.sha = subprocess.run(
            [GIT, "rev-parse", "HEAD"],
            cwd=self.tree,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.next_id = 100

    def offer(self, leg: str, text: str, *, kept_by: str = "") -> int:
        """Plant one artifact: its listing, the run that kept it, its zip.

        `leg` is "" for the gate job's figures, else `pgrx-pg<major>`;
        `kept_by` is the commit the keeping run was for (this one unless
        named).

        Returns:
            The run's id, which the step's log names.

        """
        self.next_id += 1
        ident = self.next_id
        name = figures.name(self.sha, leg)
        entry = artifact(self.sha, kept_by or self.sha, ident=ident, run=ident)
        entry["name"] = name
        record = run_record(kept_by or self.sha)
        record["id"] = ident
        record["html_url"] = f"https://example.invalid/runs/{ident}"
        (self.base / f"listing-{name}.json").write_text(
            json.dumps(listing(entry)), encoding="utf-8"
        )
        (self.base / f"run-{ident}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        (self.base / f"zip-{ident}.zip").write_bytes(zipped(text))
        return ident

    def none_for(self, leg: str = "") -> None:
        """Plant an empty listing for one artifact name: none was kept."""
        (self.base / f"listing-{figures.name(self.sha, leg)}.json").write_text(
            json.dumps(listing()), encoding="utf-8"
        )

    def derive(
        self, *, measure: bool, majors: str = ""
    ) -> subprocess.CompletedProcess[str]:
        """Run the step as the release job does.

        Returns:
            The finished step.

        """
        env = support.env(
            self.base / "home",
            VERSION="1.1.0",
            GITHUB_REPOSITORY="monumental-archive/.github",
            STUB_DIR=str(self.base),
            STUB_LEGS=LEGS,
            STUB_PGRX_LEGS=PGRX_LEGS,
            STUB_MAJORS=majors,
        )
        # Any token: the stub forge reads none. Not a literal, so no
        # credential is written in the source.
        env["GH_TOKEN"] = self.base.name
        env["PATH"] = f"{self.stubs}:{env['PATH']}"
        if not measure:
            env["STUB_NO_MEASURE"] = "1"
        return subprocess.run(
            [BASH, str(self.script)],
            cwd=self.tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def measured(self) -> list[str]:
        """Read which measurements the stub was asked for.

        Returns:
            The selectors, in order; empty when nothing was measured.

        """
        log = self.base / "measured.log"
        return log.read_text(encoding="utf-8").split() if log.exists() else []


@fixture
def derive_state(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> Derive:
    """Give each row its own Derive."""
    return Derive(tmp_path, capfd)


def test_the_commits_artifact_is_read_and_nothing_measured(
    derive_state: Derive,
) -> None:
    """The figure the gate kept becomes the floor; the step measures nothing."""
    run = derive_state.offer("", LEGS)
    done = derive_state.derive(measure=False)
    assert done.returncode == 0, done.stderr
    assert not derive_state.measured(), done.stderr
    assert (
        f"release:coverage-floor: read coverage-figures-{derive_state.sha} from"
        f" https://example.invalid/runs/{run}"
    ) in done.stderr
    assert "measuring" not in done.stderr


def test_another_commits_artifact_is_refused_and_the_step_measures(
    derive_state: Derive,
) -> None:
    """Never a floor from another commit's figure."""
    derive_state.offer("", LEGS, kept_by=OTHER)
    done = derive_state.derive(measure=True)
    assert done.returncode == 0, done.stderr
    assert derive_state.measured() == MEASURED_ALL
    assert f"was kept by a run of {OTHER}, not {derive_state.sha}" in done.stderr
    assert f"release:coverage-floor: measuring {derive_state.sha}" in done.stderr


def test_no_artifact_measures_saying_so(derive_state: Derive) -> None:
    """A repository with no queue, or a run that kept nothing."""
    derive_state.none_for()
    done = derive_state.derive(measure=True)
    assert done.returncode == 0, done.stderr
    assert derive_state.measured() == MEASURED_ALL
    assert (
        f"not reading the gate's figures: no unexpired gate artifact"
        f" coverage-figures-{derive_state.sha}"
    ) in done.stderr
    assert f"release:coverage-floor: measuring {derive_state.sha}" in done.stderr


def test_the_floor_is_the_same_either_way(derive_state: Derive) -> None:
    """One recorded figure, read or measured: the same file is written."""
    before = (derive_state.tree / ".coverage-floor").read_text(encoding="utf-8")
    written = {}
    for read in (True, False):
        (derive_state.tree / ".coverage-floor").write_text(before, encoding="utf-8")
        if read:
            derive_state.offer("", LEGS)
        else:
            derive_state.none_for()
        done = derive_state.derive(measure=not read)
        assert done.returncode == 0, done.stderr
        written[read] = (derive_state.tree / ".coverage-floor").read_text(
            encoding="utf-8"
        )
    assert written[True] == written[False]
    assert "v1.1.0" in written[True]


def test_every_majors_figure_is_read_and_nothing_measured(derive_state: Derive) -> None:
    """Each pgrx job's figure read, each run named: no measurement (#1692)."""
    derive_state.offer("", LEGS)
    runs = {
        major: derive_state.offer(f"pgrx-pg{major}", line + "\n")
        for major, line in (("17", "pgrx-pg17 96.5"), ("18", "pgrx-pg18 96.25"))
    }
    done = derive_state.derive(measure=False, majors="17 18")
    assert done.returncode == 0, done.stderr
    assert not derive_state.measured(), done.stderr
    for major, run in runs.items():
        assert (
            "release:coverage-floor: read"
            f" coverage-figures-pgrx-pg{major}-{derive_state.sha}"
            f" from https://example.invalid/runs/{run}"
        ) in done.stderr
    floor = (derive_state.tree / ".coverage-floor").read_text(encoding="utf-8")
    assert "v1.1.0" in floor


def test_a_missing_major_measures_the_extension_naming_it(derive_state: Derive) -> None:
    """pg18's job kept nothing: every major is measured, pg18 named."""
    derive_state.offer("", LEGS)
    derive_state.offer("pgrx-pg17", "pgrx-pg17 96.5\n")
    derive_state.none_for("pgrx-pg18")
    done = derive_state.derive(measure=True, majors="17 18")
    assert done.returncode == 0, done.stderr
    assert derive_state.measured() == MEASURED_PGRX
    assert (
        "release:coverage-floor: no figure read for the extension at pg18;"
        " measuring every major"
    ) in done.stderr


def test_a_figure_for_another_major_is_refused(derive_state: Derive) -> None:
    """pg18's artifact holding pg17's line is not pg18's figure."""
    derive_state.offer("", LEGS)
    derive_state.offer("pgrx-pg17", "pgrx-pg17 96.5\n")
    derive_state.offer("pgrx-pg18", "pgrx-pg17 96.5\n")
    done = derive_state.derive(measure=True, majors="17 18")
    assert done.returncode == 0, done.stderr
    assert derive_state.measured() == MEASURED_PGRX
    assert "coverage-figures.txt holds pgrx-pg17, not exactly pgrx-pg18" in done.stderr
    assert "no figure read for the extension at pg18" in done.stderr


def test_another_commits_major_is_refused(derive_state: Derive) -> None:
    """A pgrx job of another commit kept pg17's figure: refused, measured."""
    derive_state.offer("", LEGS)
    derive_state.offer("pgrx-pg17", "pgrx-pg17 96.5\n", kept_by=OTHER)
    derive_state.offer("pgrx-pg18", "pgrx-pg18 96.25\n")
    done = derive_state.derive(measure=True, majors="17 18")
    assert done.returncode == 0, done.stderr
    assert derive_state.measured() == MEASURED_PGRX
    assert (
        f"coverage-figures-pgrx-pg17-{derive_state.sha} was kept by a run of {OTHER}"
        in done.stderr
    )
    assert "no figure read for the extension at pg17" in done.stderr

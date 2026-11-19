#!/usr/bin/env python3
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

stdlib `unittest`, matching the other release/test_*.py; run through
the gate as `mise run test`.
"""

import io
import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import override

import coverage_figures as figures

sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

SHA = "977d99f0ee27ca9d8a3c9c4cf5911105c88241bf"
OTHER = "2980f36dba6e88b75ecd97536850263c140a4103"
LEGS = "python 95.23\npython-branches 91.45\n"


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


class Choose(unittest.TestCase):
    """Which artifact is this commit's."""

    def test_the_commits_own_artifact_is_chosen(self) -> None:
        """Named for the commit, kept by a run of the commit."""
        got = figures.choose(listing(artifact(SHA, SHA)), SHA)
        self.assertEqual(figures.Choice(14582536594, 44631546473), got)

    def test_the_newest_of_two_is_chosen(self) -> None:
        """A re-run kept a second one: the newest artifact wins."""
        got = figures.choose(
            listing(
                artifact(SHA, SHA, ident=1, run=10), artifact(SHA, SHA, ident=2, run=20)
            ),
            SHA,
        )
        self.assertEqual(figures.Choice(2, 20), got)

    def test_refusals_say_why(self) -> None:
        """No artifact, an expired one, another commit's, a strange listing."""
        for case, given, said in (
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
        ):
            with (
                self.subTest(case=case),
                self.assertRaises(figures.RefusedError) as raised,
            ):
                figures.choose(given, SHA)
            self.assertIn(said, str(raised.exception))


class Judge(unittest.TestCase):
    """The run that kept the artifact judged the tree that lands."""

    def test_a_green_merge_group_or_push_run_is_named(self) -> None:
        """Its address is what the step's log names."""
        for event in ("merge_group", "push"):
            with self.subTest(event=event):
                self.assertEqual(
                    "https://github.com/monumental-archive/.github/actions/runs/44631546473",
                    figures.judge(run_record(SHA, event=event), SHA),
                )

    def test_refusals_say_why(self) -> None:
        """Another commit, a pull request's run, a run not green."""
        for case, given, said in (
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
        ):
            with (
                self.subTest(case=case),
                self.assertRaises(figures.RefusedError) as raised,
            ):
                figures.judge(given, SHA)
            self.assertIn(said, str(raised.exception))


class Figures(unittest.TestCase):
    """The file holds coverage-measure.sh's leg lines and nothing else."""

    def test_the_lines_come_back_as_written(self) -> None:
        """Read out of the zip, newline-terminated."""
        self.assertEqual(LEGS, figures.figures(figures.unpack(zipped(LEGS))))

    def test_refusals_say_why(self) -> None:
        """Empty, a stray line, no file in the zip, not a zip."""
        for case, data, said in (
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
        ):
            with (
                self.subTest(case=case),
                self.assertRaises(figures.RefusedError) as raised,
            ):
                figures.figures(figures.unpack(data))
            self.assertIn(said, str(raised.exception))


class OneLeg(unittest.TestCase):
    """A pgrx job's figure: its own name, and exactly its leg (#1692)."""

    def test_the_name_carries_the_leg(self) -> None:
        """`coverage-figures-pgrx-pg17-<sha>` beside the gate's own."""
        self.assertEqual(f"coverage-figures-{SHA}", figures.name(SHA))
        self.assertEqual(
            f"coverage-figures-pgrx-pg17-{SHA}", figures.name(SHA, "pgrx-pg17")
        )

    def test_choose_reads_the_legs_name_only(self) -> None:
        """The gate's artifact is not pg17's, and pg17's is."""
        gate = artifact(SHA, SHA, ident=1, run=10)
        pg17 = {
            **artifact(SHA, SHA, ident=2, run=20),
            "name": figures.name(SHA, "pgrx-pg17"),
        }
        got = figures.choose(listing(gate, pg17), SHA, "pgrx-pg17")
        self.assertEqual(figures.Choice(2, 20), got)
        with self.assertRaises(figures.RefusedError) as raised:
            figures.choose(listing(gate), SHA, "pgrx-pg17")
        self.assertIn(
            f"no unexpired gate artifact coverage-figures-pgrx-pg17-{SHA}",
            str(raised.exception),
        )

    def test_exactly_the_leg(self) -> None:
        """Its line alone; another major's, or a second line, is refused."""
        self.assertEqual(
            "pgrx-pg17 96.5\n", figures.figures("pgrx-pg17 96.5\n", "pgrx-pg17")
        )
        for text, said in (
            ("pgrx-pg18 96.5\n", "holds pgrx-pg18, not exactly pgrx-pg17"),
            (
                "pgrx-pg17 96.5\npython 90.0\n",
                "holds pgrx-pg17 python, not exactly pgrx-pg17",
            ),
        ):
            with (
                self.subTest(text=text),
                self.assertRaises(figures.RefusedError) as raised,
            ):
                figures.figures(text, "pgrx-pg17")
            self.assertIn(said, str(raised.exception))

    def test_the_command_line_takes_only_a_pgrx_leg(self) -> None:
        """A third argument is `pgrx-pg<major>` or a usage error."""
        self.assertEqual(2, figures.main(["figures", SHA, "python"]))


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

# The measurement: `probe` names the python leg and `pgrx-majors` the
# planted majors; a measurement (`all` or `pgrx`) prints the planted
# legs, or fails loudly where the row says nothing may be measured.
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
  *) ;;
esac
echo "${2:-all}" >> "${STUB_DIR}/measured.log"
if [[ -n ${STUB_NO_MEASURE:-} ]]; then
  echo "the stub measurement (${2:-all}) was called" >&2
  exit 97
fi
if [[ $2 == pgrx ]]; then
  printf '%s' "${STUB_PGRX_LEGS}"
else
  printf '%s' "${STUB_LEGS}"
fi
"""

PGRX_LEGS = "pgrx-pg17 96.5\npgrx-pg18 96.25\n"
# What the stub measurement was asked for: the gate's legs, or the
# extension's.
MEASURED_ALL: list[str] = ["all"]
MEASURED_PGRX: list[str] = ["pgrx"]


class Derive(unittest.TestCase):
    """derive-coverage-floor.sh, read or measured, in a planted tree."""

    @override
    def setUp(self) -> None:
        """Plant the canon's two scripts, the stubs, and a tree with a floor."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        canon = self.base / "canon"
        (canon / "release").mkdir(parents=True)
        for rel in ("release/derive-coverage-floor.sh", "release/coverage_figures.py"):
            shutil.copy(CANON / rel, canon / rel)
        # The belt whole, as the release job has it: a helper that comes
        # to import another belt module finds it, and only the
        # measurement below is a stub.
        shutil.copytree(
            CANON / "mise", canon / "mise", ignore=shutil.ignore_patterns("__pycache__")
        )
        self.stubs = self.base / "stubs"
        self.stubs.mkdir()
        for path, text in (
            (self.stubs / "gh", GH),
            (canon / "mise" / "coverage-measure.sh", MEASURE),
        ):
            path.write_text(text, encoding="utf-8")
            path.chmod(0o755)
        self.script = canon / "release" / "derive-coverage-floor.sh"
        self.tree = self.base / "tree"
        self.tree.mkdir()
        (self.tree / "a.py").write_text("", encoding="utf-8")
        env = hermetic.env(self.base / "home")
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [
                sys.executable,
                str(canon / "mise" / "coverage_floor.py"),
                "write",
                "--adopt",
                "--provenance",
                "v1.0.0",
            ],
            cwd=self.tree,
            input=LEGS,
            text=True,
            env=env,
            check=True,
            capture_output=True,
        )
        for argv in (
            [GIT, "init", "-q"],
            [GIT, "add", "-A"],
            [GIT, "commit", "-qm", "plant"],
        ):
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run(argv, cwd=self.tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
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
        env = hermetic.env(
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
        # ruff: ignore[subprocess-without-shell-equals-true]
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

    def test_the_commits_artifact_is_read_and_nothing_measured(self) -> None:
        """The figure the gate kept becomes the floor; the step measures nothing."""
        run = self.offer("", LEGS)
        done = self.derive(measure=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(self.measured(), done.stderr)
        self.assertIn(
            f"release:coverage-floor: read coverage-figures-{self.sha} from"
            f" https://example.invalid/runs/{run}",
            done.stderr,
        )
        self.assertNotIn("measuring", done.stderr)

    def test_another_commits_artifact_is_refused_and_the_step_measures(self) -> None:
        """Never a floor from another commit's figure."""
        self.offer("", LEGS, kept_by=OTHER)
        done = self.derive(measure=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(MEASURED_ALL, self.measured())
        self.assertIn(f"was kept by a run of {OTHER}, not {self.sha}", done.stderr)
        self.assertIn(f"release:coverage-floor: measuring {self.sha}", done.stderr)

    def test_no_artifact_measures_saying_so(self) -> None:
        """A repository with no queue, or a run that kept nothing."""
        self.none_for()
        done = self.derive(measure=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(MEASURED_ALL, self.measured())
        self.assertIn(
            f"not reading the gate's figures: no unexpired gate artifact"
            f" coverage-figures-{self.sha}",
            done.stderr,
        )
        self.assertIn(f"release:coverage-floor: measuring {self.sha}", done.stderr)

    def test_the_floor_is_the_same_either_way(self) -> None:
        """One recorded figure, read or measured: the same file is written."""
        before = (self.tree / ".coverage-floor").read_text(encoding="utf-8")
        written = {}
        for read in (True, False):
            with self.subTest(read=read):
                (self.tree / ".coverage-floor").write_text(before, encoding="utf-8")
                if read:
                    self.offer("", LEGS)
                else:
                    self.none_for()
                done = self.derive(measure=not read)
                self.assertEqual(0, done.returncode, done.stderr)
                written[read] = (self.tree / ".coverage-floor").read_text(
                    encoding="utf-8"
                )
        self.assertEqual(written[True], written[False])
        self.assertIn("v1.1.0", written[True])

    def test_every_majors_figure_is_read_and_nothing_measured(self) -> None:
        """Each pgrx job's figure read, each run named: no measurement (#1692)."""
        self.offer("", LEGS)
        runs = {
            major: self.offer(f"pgrx-pg{major}", line + "\n")
            for major, line in (("17", "pgrx-pg17 96.5"), ("18", "pgrx-pg18 96.25"))
        }
        done = self.derive(measure=False, majors="17 18")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(self.measured(), done.stderr)
        for major, run in runs.items():
            self.assertIn(
                "release:coverage-floor: read"
                f" coverage-figures-pgrx-pg{major}-{self.sha}"
                f" from https://example.invalid/runs/{run}",
                done.stderr,
            )
        floor = (self.tree / ".coverage-floor").read_text(encoding="utf-8")
        self.assertIn("v1.1.0", floor)

    def test_a_missing_major_measures_the_extension_naming_it(self) -> None:
        """pg18's job kept nothing: every major is measured, pg18 named."""
        self.offer("", LEGS)
        self.offer("pgrx-pg17", "pgrx-pg17 96.5\n")
        self.none_for("pgrx-pg18")
        done = self.derive(measure=True, majors="17 18")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(MEASURED_PGRX, self.measured())
        self.assertIn(
            "release:coverage-floor: no figure read for the extension at pg18;"
            " measuring every major",
            done.stderr,
        )

    def test_a_figure_for_another_major_is_refused(self) -> None:
        """pg18's artifact holding pg17's line is not pg18's figure."""
        self.offer("", LEGS)
        self.offer("pgrx-pg17", "pgrx-pg17 96.5\n")
        self.offer("pgrx-pg18", "pgrx-pg17 96.5\n")
        done = self.derive(measure=True, majors="17 18")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(MEASURED_PGRX, self.measured())
        self.assertIn(
            "coverage-figures.txt holds pgrx-pg17, not exactly pgrx-pg18", done.stderr
        )
        self.assertIn("no figure read for the extension at pg18", done.stderr)

    def test_another_commits_major_is_refused(self) -> None:
        """A pgrx job of another commit kept pg17's figure: refused, measured."""
        self.offer("", LEGS)
        self.offer("pgrx-pg17", "pgrx-pg17 96.5\n", kept_by=OTHER)
        self.offer("pgrx-pg18", "pgrx-pg18 96.25\n")
        done = self.derive(measure=True, majors="17 18")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(MEASURED_PGRX, self.measured())
        self.assertIn(
            f"coverage-figures-pgrx-pg17-{self.sha} was kept by a run of {OTHER}",
            done.stderr,
        )
        self.assertIn("no figure read for the extension at pg17", done.stderr)


if __name__ == "__main__":
    unittest.main()

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
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"

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


# The forge: answers from files the row planted beside it, and records
# every call. A path with no planted answer fails as gh does.
GH = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/gh.log"
case "$2" in
  */artifacts\\?name=*) f=listing.json ;;
  */runs/*) f=run.json ;;
  */artifacts/*/zip) f=figures.zip ;;
  *) f=none ;;
esac
if [[ -f ${STUB_DIR}/${f} ]]; then
  cat "${STUB_DIR}/${f}"
  exit 0
fi
echo "gh: Not Found (HTTP 404)" >&2
exit 1
"""

# The measurement: `probe` names the python leg, `pgrx` prints no leg
# (no extension); a measurement prints the planted legs, or fails loudly
# where the row says nothing may be measured.
MEASURE = """\
#!/usr/bin/env bash
if [[ $2 == probe ]]; then
  echo python
  exit 0
fi
# The pgrx legs (#1656): this tree has no extension, so none.
if [[ $2 == pgrx ]]; then
  exit 0
fi
echo measured >> "${STUB_DIR}/measured.log"
if [[ -n ${STUB_NO_MEASURE:-} ]]; then
  echo "the stub measurement was called" >&2
  exit 97
fi
printf '%s' "${STUB_LEGS}"
"""


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
        (canon / "mise").mkdir()
        for rel in (
            "release/derive-coverage-floor.sh",
            "release/coverage_figures.py",
            "mise/coverage_floor.py",
            "mise/decoded.py",
        ):
            shutil.copy(CANON / rel, canon / rel)
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

    def plant(self, **answers: object) -> None:
        """Write the forge's answers: listing, run, and the figures zip."""
        for name, answer in answers.items():
            path = self.base / ("figures.zip" if name == "zip" else f"{name}.json")
            if isinstance(answer, bytes):
                path.write_bytes(answer)
            else:
                path.write_text(json.dumps(answer), encoding="utf-8")

    def derive(self, *, measure: bool) -> subprocess.CompletedProcess[str]:
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

    def measured(self) -> bool:
        """Say whether the stub measurement was called.

        Returns:
            True when it was.

        """
        return (self.base / "measured.log").exists()

    def test_the_commits_artifact_is_read_and_nothing_measured(self) -> None:
        """The figure the gate kept becomes the floor; the step measures nothing."""
        self.plant(
            listing=listing(artifact(self.sha, self.sha)),
            run=run_record(self.sha),
            zip=zipped(LEGS),
        )
        done = self.derive(measure=False)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(self.measured(), done.stderr)
        self.assertIn(
            f"release:coverage-floor: read the gate's figures for {self.sha} from"
            " https://github.com/monumental-archive/.github/actions/runs/44631546473",
            done.stderr,
        )
        self.assertNotIn("measuring", done.stderr)

    def test_another_commits_artifact_is_refused_and_the_step_measures(self) -> None:
        """Never a floor from another commit's figure."""
        self.plant(listing=listing(artifact(self.sha, OTHER)))
        done = self.derive(measure=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(self.measured())
        self.assertIn(f"was kept by a run of {OTHER}, not {self.sha}", done.stderr)
        self.assertIn(f"release:coverage-floor: measuring {self.sha}", done.stderr)

    def test_no_artifact_measures_saying_so(self) -> None:
        """A repository with no queue, or a run that kept nothing."""
        self.plant(listing=listing())
        done = self.derive(measure=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(self.measured())
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
                self.plant(
                    listing=listing(artifact(self.sha, self.sha))
                    if read
                    else listing(),
                    run=run_record(self.sha),
                    zip=zipped(LEGS),
                )
                done = self.derive(measure=not read)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual(not read, self.measured(), done.stderr)
                written[read] = (self.tree / ".coverage-floor").read_text(
                    encoding="utf-8"
                )
        self.assertEqual(written[True], written[False])
        self.assertIn("v1.1.0", written[True])


if __name__ == "__main__":
    unittest.main()

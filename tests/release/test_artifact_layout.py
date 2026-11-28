"""Every row artifact carries its own name, whatever the match count (#2178).

`actions/download-artifact` with a `pattern` gives each artifact a
directory of its own only when the pattern matches two or more. With
exactly one match it unpacks that artifact straight into `path`, so a
consumer that reads one directory per artifact reads one artifact's
subdirectories as many: monumental-archive's one measuring test row was
read as four coverage rows, and a release whose legs emit one inventory
plan would glob no plans at all.

So the layout is the artifact's, never the download's: each uploader
stages its file or directory as `<artifact name>/` under a parent and
uploads the parent, and each consumer downloads with
`merge-multiple: true`. These rows hold every `coverage-run-*` and
`sbom-plan-*` upload and download in the canon's workflows to that, run
each staging step's own body to see the layout it makes, and plant the
two defects to see each named.

pytest, run by `mise run test`."""

import copy
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from belt import workflow_yaml
from tests import hermetic
import contextlib

if TYPE_CHECKING:
    from collections.abc import Iterator

# A workflow and a step, as workflow_yaml reads them.
Doc = dict[str, object]
Step = dict[str, object]

CANON = hermetic.CANON
WORKFLOWS = CANON / ".github" / "workflows"
BASH = hermetic.tool("bash")

UPLOAD = "actions/upload-artifact@"
DOWNLOAD = "actions/download-artifact@"
VERB = {UPLOAD: "upload", DOWNLOAD: "download"}
# The artifact families whose consumers read one directory per artifact.
NAMED = ("coverage-run-", "sbom-plan-")


def _with(step: Step) -> dict[str, object]:
    """Read a step's `with`, or an empty mapping.

    Returns:
        The mapping.

    """
    found = step.get("with", {})
    return found if isinstance(found, dict) else {}


def _env(step: Step) -> dict[str, object]:
    """Read a step's `env`, or an empty mapping.

    Returns:
        The mapping.

    """
    found = step.get("env", {})
    return found if isinstance(found, dict) else {}


def _family(value: object) -> str | None:
    """Name the artifact family a name or a pattern belongs to.

    Returns:
        The family's prefix, or None for any other artifact.

    """
    if not isinstance(value, str):
        return None
    return next((prefix for prefix in NAMED if value.startswith(prefix)), None)


class Named(NamedTuple):
    """One upload or download of a named family, with the step before it."""

    where: str
    action: str
    family: str
    step: Step
    before: Step


def named(docs: dict[str, Doc]) -> Iterator[Named]:
    """Walk every upload and download of a named family.

    Yields:
        Each, in file order; `action` is UPLOAD or DOWNLOAD.

    """
    for name, doc in docs.items():
        for job_id, job in workflow_yaml.jobs(doc).items():
            steps = workflow_yaml.steps(job)
            for index, step in enumerate(steps):
                uses = str(step.get("uses", ""))
                given = _with(step)
                action, key = (
                    (DOWNLOAD, given.get("pattern"))
                    if uses.startswith(DOWNLOAD)
                    else (UPLOAD, given.get("name"))
                )
                family = _family(key)
                if family and uses.startswith(action):
                    before = steps[index - 1] if index else {}
                    yield Named(f"{name} {job_id}", action, family, step, before)


def stagings(docs: dict[str, Doc]) -> list[Step]:
    """Find the step staging each named upload.

    Returns:
        Each named upload's preceding step, in file order.

    """
    return [n.before for n in named(docs) if n.action == UPLOAD]


def defect(found: Named) -> str | None:
    """Judge one named upload or download.

    Returns:
        The defect's line, or None when it is laid out by name.

    """
    given = _with(found.step)
    if found.action == DOWNLOAD:
        if given.get("merge-multiple") is True:
            return None
        return (
            f"{found.where}: the {given.get('pattern')} download sets no "
            "merge-multiple: true, so a lone match unpacks flat"
        )
    artifact = given.get("name")
    env = _env(found.before)
    if env.get("ARTIFACT") == artifact and env.get("STAGE") == given.get("path"):
        return None
    return (
        f"{found.where}: the {artifact} upload's root does not hold {artifact}/: "
        "stage it under STAGE as ARTIFACT, and upload STAGE"
    )


def findings(docs: dict[str, Doc]) -> list[str]:
    """Hold every named upload and download to the artifact's own layout.

    Returns:
        One line per defect; none when every family is laid out by name.

    """
    every = list(named(docs))
    red = [line for line in map(defect, every) if line]
    seen = {(n.family, n.action) for n in every}
    red.extend(
        f"no {family}* {VERB[action]} found: this row checks nothing for it"
        for family in NAMED
        for action in (UPLOAD, DOWNLOAD)
        if (family, action) not in seen
    )
    return red


def canon() -> dict[str, Doc]:
    """Read every workflow in the canon.

    Returns:
        File name to its document.

    """
    return {
        path.name: workflow_yaml.document(path)
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }


def first(docs: dict[str, Doc], family: str, action: str) -> Step:
    """Find the first step of a family using an action, to plant on.

    Returns:
        The step, inside `docs`.

    """
    return next(
        n.step for n in named(docs) if n.family == family and n.action == action
    )


class TestLayout:
    """Each family is laid out by the artifact, never by the match count."""

    def test_the_canon_lays_every_family_out_by_name(self) -> None:
        """Every named upload is staged and every named download merged."""
        none: list[str] = []
        assert none == findings(canon())

    @pytest.mark.parametrize("family", NAMED)
    def test_a_download_without_merge_multiple_is_red(self, family: object) -> None:
        """A planted pattern download with no merge-multiple is named."""
        docs = copy.deepcopy(canon())
        step = first(docs, family, DOWNLOAD)
        given = _with(step)
        del given["merge-multiple"]
        red = findings(docs)
        assert len(red) == 1, red
        assert re.search(
            rf"the {family}\* download sets no merge-multiple: true, "
            "so a lone match unpacks flat$",
            red[0],
        )

    @pytest.mark.parametrize("family", NAMED)
    def test_an_upload_of_the_bare_path_is_red(self, family: object) -> None:
        """A planted upload of the row's own path, not its stage, is named."""
        docs = copy.deepcopy(canon())
        step = first(docs, family, UPLOAD)
        given = _with(step)
        given["path"] = "plan/plan.json"
        artifact = given["name"]
        red = findings(docs)
        assert len(red) == 1, red
        assert red[0].endswith(
            f": the {artifact} upload's root does not hold {artifact}/: "
            "stage it under STAGE as ARTIFACT, and upload STAGE"
        ), red[0]


def plant(base: Path, *, directory: bool) -> tuple[Path, list[str]]:
    """Plant what a row stages: a coverage run directory, or a plan.

    Returns:
        The source path and the files the stage must hold, relative.

    """
    if directory:
        source = base / "coverage-run"
        (source / "python").mkdir(parents=True)
        (source / "python" / ".coverage.host").write_text("d", encoding="utf-8")
        (source / "coverage.rc").write_text("rc", encoding="utf-8")
        return source, ["coverage.rc", "python/.coverage.host"]
    source = base / "plan.json"
    source.write_text("{}", encoding="utf-8")
    return source, ["plan.json"]


@pytest.mark.usefixtures("_stack")
class TestStaged:
    """Each staging step's own body makes the layout the consumer reads."""
    
    @pytest.fixture
    def _stack(self, request: pytest.FixtureRequest) -> None:
        """Hold the row's cleanups, undone last first after it ends."""
        self.stack = contextlib.ExitStack()
        request.addfinalizer(self.stack.close)

    def stage(self, step: Step, source: Path, stage: Path, artifact: str) -> None:
        """Run one staging step's body against planted paths."""
        body = step.get("run")
        assert isinstance(body, str), step
        done = subprocess.run(
            [BASH, "-e", "-c", str(body)],
            env=hermetic.env(SOURCE=str(source), STAGE=str(stage), ARTIFACT=artifact),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr

    def test_each_stage_holds_the_artifacts_own_directory(self) -> None:
        """The staged root holds `<artifact>/` and the row's files inside it."""
        steps = stagings(canon())
        assert steps
        for step in steps:
            env = _env(step)
            family = _family(env.get("ARTIFACT"))
            scratch = tempfile.TemporaryDirectory()
            self.stack.callback(scratch.cleanup)
            base = Path(scratch.name)
            stage = base / "up"
            artifact = f"{family}7"
            source, inside = plant(base, directory=family == "coverage-run-")
            self.stage(step, source, stage, artifact)
            want: list[str] = [artifact]
            names = [p.name for p in stage.iterdir()]
            assert want == names
            found = sorted(
                str(p.relative_to(stage / artifact))
                for p in (stage / artifact).rglob("*")
                if p.is_file()
            )
            assert inside == found

    def test_a_row_that_measured_nothing_stages_nothing(self) -> None:
        """No run directory, no stage: the upload then finds no files."""
        steps = [
            s
            for s in stagings(canon())
            if _family(_env(s).get("ARTIFACT")) == "coverage-run-"
        ]
        assert steps
        for step in steps:
            scratch = tempfile.TemporaryDirectory()
            self.stack.callback(scratch.cleanup)
            base = Path(scratch.name)
            self.stage(step, base / "coverage-run", base / "up", "coverage-run-1")
            assert not (base / "up").exists()

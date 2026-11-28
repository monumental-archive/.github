"""Table tests for audit:actions' signer filter (#1152, #1340).

Two fixtures, both zizmor 1.32.0's own `--format=json
--persona=auditor` output, run online on the canon's
`.github/workflows/`, unedited:

- `zizmor-1.32.0-canon.json`, 2026-11-15: the ten `stale-action-refs`
  findings on the signer.
- `zizmor-1.32.0-ref-version-mismatch.json`, 2026-11-17: the first of
  the ten `ref-version-mismatch` findings on the canon at `af30901`
  (`4564ce7^`, before #1345 moved the signer pin), which pins the
  signer at `0d6c6e2` while the signer's `main` was `b746956`.

Every other row is derived from one of those real findings by changing
one field, so each kept or dropped case is planted alone, not invented.
Those rows run `main` with `run_zizmor` answering the array (#1955).

`Records` asserts what remains is recorded, read back from
ORG_FINDINGS: subject the first location's file, message `<ident>:
<the first line of the first Primary location's feature>` (the
owner's ruling on #1955; Hidden and Related are context), detail
`desc`, the severity and each location with its line; the token and
offline-degradation findings; zizmor's status as the exit.
`PinnedZizmor` runs the belt's zizmor `--offline` on a planted
workflow, and two unpinned `uses:` in one file are two records with
two messages. `Task` runs `audit:actions`' own body over a stub zizmor:
one run, the record, zizmor's 11; a clean run, 0. Against
`origin/main` the `Records` and `Task` record rows fail.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import zizmor_signer as signer
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The findings of the canon's own run, all the signer's.
SIGNER_FINDINGS = 10

# zizmor's exit statuses: 10-14 by the highest remaining finding's
# severity, 14 for a high one; one of them a stub answers; and one
# outside them, zizmor failing.
HIGH = 14
PLANTED_SEVERITY = 11
NOT_A_SEVERITY = 2

HERE = support.MISE


def recorded(name: str) -> list[dict[str, object]]:
    """Read a recorded zizmor run: its finding array, each an object.

    Returns:
        The findings, as zizmor wrote them.

    """
    text = (Path(__file__).resolve().parent / "testdata" / name).read_text()
    found = decoded.as_array(decoded.load_json(text), name)
    return [decoded.as_object(f, f"{name}[{i}]") for i, f in enumerate(found)]


def locations(finding: dict[str, object]) -> list[dict[str, object]]:
    """Read a finding's locations, each an object.

    Returns:
        The locations themselves, so a change to one changes the finding.

    """
    found = decoded.as_array(finding["locations"], "locations")
    return [decoded.as_object(loc, f"locations[{i}]") for i, loc in enumerate(found)]


def symbolic(location: dict[str, object]) -> dict[str, object]:
    """Read a location's `symbolic` half.

    Returns:
        The object itself.

    """
    return decoded.as_object(location["symbolic"], "symbolic")


def concrete(location: dict[str, object]) -> dict[str, object]:
    """Read a location's `concrete` half.

    Returns:
        The object itself.

    """
    return decoded.as_object(location["concrete"], "concrete")


REAL = recorded("zizmor-1.32.0-canon.json")
MISMATCH = recorded("zizmor-1.32.0-ref-version-mismatch.json")
SIGNER_WORKFLOW = "monumental-archive/signer/.github/workflows/sign.yml"
SIGNER_REF = SIGNER_WORKFLOW + "@cffec9441210d46f237099d8785098aacf5b8273"
MISMATCH_REF = SIGNER_WORKFLOW + "@0d6c6e297684c8ba401fb9c212532628fe552148"
TASK = "audit:actions"
ARGS = ["--persona=auditor", ".github/workflows/a.yml"]
# A GH_TOKEN value: the rows never reach the API.
STANDIN = "a token"


SIGNED = ["--signer-workflow", SIGNER_WORKFLOW, *ARGS]


@fixture
def answer(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Have zizmor answer a finding array, as the task runs the CLI.

    zizmor's status is 14 for any answer but an empty array, unless the
    row names one.

    Returns:
        A function taking the findings and, optionally, the status.

    """

    def answering(findings: object, *, status: int | None = None) -> None:
        code = status if status is not None else (14 if findings != [] else 0)
        ran = signer.Ran(code, json.dumps(findings).encode(), b"")
        monkeypatch.setattr(signer, "run_zizmor", lambda *_args, **_kwargs: ran)
        for name, value in {
            "GH_TOKEN": STANDIN,
            "MISE_TASK_NAME": TASK,
            "ORG_FINDINGS": "",
        }.items():
            monkeypatch.setenv(name, value)

    return answering


def repointed(finding: dict[str, object], reference: str) -> dict[str, object]:
    """Return a copy of a real finding whose Primary reference is changed.

    Returns:
        The finding with every Primary location's feature replaced.

    """
    changed = copy.deepcopy(finding)
    for location in locations(changed):
        if symbolic(location)["kind"] == "Primary":
            concrete(location)["feature"] = reference
    return changed


def rewritten(
    finding: dict[str, object],
    kind: str,
    old: str,
    new: str,
) -> dict[str, object]:
    """Return a copy of a real finding with text replaced in one location kind.

    Returns:
        The finding with `old` replaced by `new` in the feature of every
        location of that kind.

    """
    changed = copy.deepcopy(finding)
    for location in locations(changed):
        if symbolic(location)["kind"] == kind:
            feature = decoded.as_str(concrete(location)["feature"], "feature")
            concrete(location)["feature"] = feature.replace(old, new)
    return changed


# The canon's own zizmor run, as it came.
def test_the_fixture_is_the_ten_signer_findings() -> None:
    """The fixture is what the issue measured: ten, one rule, one ref."""
    assert len(REAL) == SIGNER_FINDINGS
    idents: set[object] = {f["ident"] for f in REAL}
    rules: set[object] = {"stale-action-refs"}
    assert rules == idents
    refs = {r for f in REAL for r in signer.references(f)}
    want: set[str] = {SIGNER_REF}
    assert want == refs


def test_all_ten_are_dropped_and_counted(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The canon goes green, and says what it dropped."""
    answer(REAL)
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "10 stale-action-refs finding(s) on the signer dropped" in out
    assert not err


# The signer's `# main` pin behind `main` (#1340), as zizmor wrote it.
def test_the_fixture_is_one_signer_mismatch() -> None:
    """The fixture is the measured shape: the reference rides the Hidden job."""
    assert len(MISMATCH) == 1
    assert MISMATCH[0]["ident"] == "ref-version-mismatch"
    primary = [
        concrete(loc)["feature"]
        for loc in locations(MISMATCH[0])
        if symbolic(loc)["kind"] == "Primary"
    ]
    feature: list[object] = ["main"]
    assert feature == primary
    want: list[str] = [MISMATCH_REF]
    assert want == signer.references(MISMATCH[0])


def test_the_signer_mismatch_is_dropped_and_counted(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The signer's `# main` behind its branch head goes green, said aloud."""
    answer(MISMATCH)
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert (
        "1 ref-version-mismatch finding(s) on the signer dropped"
        " (monumental-archive/signer is pinned on `# main`"
    ) in out
    assert not err


def test_both_audits_together_are_dropped(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The scheduled run carries both; each is counted on its own line."""
    answer([*REAL, *MISMATCH])
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "10 stale-action-refs finding(s) on the signer dropped" in out
    assert "1 ref-version-mismatch finding(s) on the signer dropped" in out


def test_another_owner_mismatch_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The same finding on another owner's repository still reds."""
    other = rewritten(
        MISMATCH[0],
        "Hidden",
        "monumental-archive/signer/",
        "someone-else/signer/",
    )
    answer([other])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "0 ref-version-mismatch finding(s) on the signer dropped" in out
    assert "1 finding(s) remain: ref-version-mismatch" in out


def test_a_mismatch_with_no_readable_reference_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """A Hidden location with no `uses:` line names nothing; it is kept."""
    blind = rewritten(MISMATCH[0], "Hidden", "uses:", "calls:")
    none: list[str] = []
    assert none == signer.references(blind)
    answer([blind])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "1 finding(s) remain: ref-version-mismatch" in out


def test_the_comment_alone_is_not_read_as_a_reference(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """Primary carries only `main`; dropping the Hidden location blinds it."""
    blind = copy.deepcopy(MISMATCH[0])
    blind["locations"] = [
        loc for loc in locations(blind) if symbolic(loc)["kind"] == "Primary"
    ]
    answer([blind])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "1 finding(s) remain: ref-version-mismatch" in out


def test_a_uses_in_prose_is_not_a_reference(answer: Callable[..., None]) -> None:
    """A commented `uses:` beside the signer's is not read as a second ref."""
    prose = rewritten(
        MISMATCH[0],
        "Hidden",
        "    with:\n",
        "    # relative `uses: actions/checkout@v4` in prose\n    with:\n",
    )
    want: list[str] = [MISMATCH_REF]
    assert want == signer.references(prose)
    answer([prose])
    assert signer.main(SIGNED) == 0


def test_a_second_reference_elsewhere_keeps_it(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """Every named reference must be the signer's, not just one of them."""
    two = rewritten(
        MISMATCH[0],
        "Hidden",
        "    with:\n",
        "    with:\n      - uses: actions/checkout@v4\n",
    )
    answer([two])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "1 finding(s) remain: ref-version-mismatch" in out


def test_another_audit_on_the_signer_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """An unrelated audit carrying the same locations still reds."""
    other_rule = copy.deepcopy(MISMATCH[0])
    other_rule["ident"] = "unpinned-uses"
    answer([other_rule])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "1 finding(s) remain: unpinned-uses" in out


# Every finding the exemption does not name still reds.
def test_another_repository_reusable_workflow_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The same audit on someone else's reusable workflow is reported."""
    other = repointed(
        REAL[0],
        "monumental-archive/other/.github/workflows/sign.yml@" + "a" * 40,
    )
    answer([other])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "0 stale-action-refs finding(s) on the signer dropped" in out
    assert "1 finding(s) remain: stale-action-refs" in out


def test_a_third_party_action_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """A stale third-party action pin is exactly what the audit is for."""
    third = repointed(REAL[0], "actions/checkout@" + "b" * 40)
    answer([*REAL, third])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "1 finding(s) remain" in out


def test_a_repository_that_only_shares_the_prefix_is_kept(
    answer: Callable[..., None],
) -> None:
    """`signer-fork` is not `signer`: repositories match whole."""
    near = repointed(
        REAL[0],
        "monumental-archive/signer-fork/.github/workflows/sign.yml@" + "c" * 40,
    )
    answer([near])
    assert signer.main(SIGNED) == HIGH


def test_kept_another_audit_on_the_signer_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The exemption is one rule wide, not the signer wide."""
    other_rule = copy.deepcopy(REAL[0])
    other_rule["ident"] = "impostor-commit"
    answer([other_rule])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == HIGH
    assert "remain: impostor-commit" in out


def test_a_finding_with_no_readable_reference_is_kept(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """When zizmor's shape gives no reference, nothing is assumed."""
    blind = copy.deepcopy(REAL[0])
    for location in locations(blind):
        symbolic(location)["kind"] = "Related"
    answer([blind])
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == 1
    assert "findings[0]: no location is Primary" in err
    assert not out


def test_a_reference_without_a_ref_is_kept(answer: Callable[..., None]) -> None:
    """A Primary feature with no `@` is not a reference."""
    answer([repointed(REAL[0], "monumental-archive/signer")])
    assert signer.main(SIGNED) == HIGH


# How the signer's repository is read and compared.
def test_case_differences_do_not_matter(answer: Callable[..., None]) -> None:
    """GitHub owner and repository names are case-insensitive."""
    upper = repointed(REAL[0], SIGNER_REF.replace("signer", "Signer", 1))
    answer([upper])
    assert signer.main(SIGNED) == 0


def test_an_ignored_finding_is_not_reported(answer: Callable[..., None]) -> None:
    """Per-site `zizmor: ignore` comments are zizmor's, not this filter's."""
    ignored = repointed(REAL[0], "actions/checkout@" + "d" * 40)
    ignored["ignored"] = True
    answer([*REAL, ignored])
    assert signer.main(SIGNED) == 0


def test_no_findings_is_green_and_counts_zero(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """An empty run drops nothing and passes."""
    answer([])
    status = signer.main(SIGNED)
    out = capfd.readouterr().out
    assert status == 0
    assert "0 stale-action-refs finding(s) on the signer dropped" in out


# Reports the filter will not guess about: exit 1, by name.
def test_a_signer_declaration_with_no_repository_is_refused(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """A declaration that names no owner/repo cannot exempt anything."""
    answer(REAL)
    status = signer.main(["--signer-workflow", "signer", *ARGS])
    err = capfd.readouterr().err
    assert status == 1
    assert "names no owner/repository" in err


def test_a_finding_of_the_wrong_shape_is_refused_not_raised(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """The shrunk examples (#1555): refused by name, never a traceback."""
    shapes: tuple[tuple[dict[str, object], str], ...] = (
        ({"ident": []}, "findings[0]: ident is not a string"),
        (
            {"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]},
            "findings[0]: desc is not a string",
        ),
        (
            {"ident": "stale-action-refs", "desc": "d", "locations": 5},
            "findings[0]: determinations is not an object",
        ),
        (
            {
                "ident": "stale-action-refs",
                "desc": "d",
                "determinations": {"severity": "Low"},
                "locations": [{"symbolic": {"kind": "Primary"}, "concrete": 1}],
            },
            "findings[0]: locations[0]: symbolic.key is not an object",
        ),
    )
    for finding, said in shapes:
        answer([finding])
        status = signer.main(SIGNED)
        err = capfd.readouterr().err
        assert status == 1
        assert (
            f"{TASK}: zizmor exited 14: the output is not zizmor's report: {said}"
        ) in err


def test_a_finding_with_no_primary_location_is_refused(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """A remaining finding with no location at fault: refused by name."""
    third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
    for location in locations(third):
        symbolic(location)["kind"] = "Hidden"
    answer([third])
    status = signer.main(SIGNED)
    err = capfd.readouterr().err
    assert status == 1
    assert (
        f"{TASK}: zizmor exited 14: the output is not zizmor's report:"
        " findings[0]: no location is Primary"
    ) in err


def test_input_that_is_not_the_finding_array_is_refused(
    capfd: pytest.CaptureFixture[str], answer: Callable[..., None]
) -> None:
    """A SARIF document or an error object is not silently empty."""
    answer({"runs": []})
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == 1
    assert "it is not zizmor's finding array" in err
    assert not out


class Records:
    """What remains is recorded; zizmor runs once, its status the exit."""

    def __init__(self, tmp_path: Path) -> None:
        """Lay out the findings file and the environment a task gives."""
        self.file = Path(str(tmp_path)) / "findings" / "job.jsonl"
        self.env = {
            "GH_TOKEN": "a token",
            "MISE_TASK_NAME": TASK,
            "ORG_FINDINGS": str(self.file),
        }

    def enter(
        self, monkeypatch: pytest.MonkeyPatch, ran: signer.Ran
    ) -> list[list[str]]:
        """Have zizmor answer `ran`, under the task's environment.

        Returns:
            Each argument list zizmor is given, as the CLI runs.

        """
        asked: list[list[str]] = []

        def zizmor(argv: list[str]) -> signer.Ran:
            asked.append(list(argv))
            return ran

        monkeypatch.setattr(signer, "run_zizmor", zizmor)
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        return asked

    def records(self) -> list[object]:
        """Read every record appended.

        Returns:
            The records, in order; none when the file was not written.

        """
        if not self.file.exists():
            return []
        lines = self.file.read_text(encoding="utf-8").splitlines()
        return [decoded.load_json(line) for line in lines]


@fixture
def records_state(tmp_path: Path) -> Records:
    """Give each row its own Records.

    Returns:
        The row's Records.

    """
    return Records(tmp_path)


def test_a_remaining_finding_is_one_record(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A third-party stale ref: one record, its lines, zizmor's 11.

    Its Hidden location (the job) comes first: the subject is that
    location's file, the message the Primary location's source.
    """
    third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
    both: list[dict[str, object]] = [*REAL, third]
    doc = json.dumps(both).encode()
    asked = records_state.enter(
        monkeypatch, signer.Ran(PLANTED_SEVERITY, doc, b" INFO zizmor\n")
    )
    status = signer.main(SIGNED)
    out, err = capfd.readouterr()
    assert status == PLANTED_SEVERITY, err
    assert asked == [list(ARGS)]
    path = ".github/workflows/continuous.yml"
    want: list[object] = [
        {
            "v": 1,
            "task": TASK,
            "subject": path,
            "message": "stale-action-refs: actions/checkout@" + "d" * 40,
            "detail": [
                "commit hash does not point to a Git tag",
                "severity: Low",
                f"{path}:640: this job",
                f"{path}:643: this job",
            ],
        }
    ]
    assert want == records_state.records()
    assert err.startswith(" INFO zizmor\n"), err
    assert f"{TASK}: 1 finding(s) remain: stale-action-refs" in out
    assert "10 stale-action-refs finding(s) on the signer dropped" in out


def test_all_dropped_is_no_record_and_green(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Every finding the signer's: no record, 0, though zizmor said 11."""
    records_state.enter(
        monkeypatch, signer.Ran(PLANTED_SEVERITY, json.dumps(REAL).encode(), b"")
    )
    status = signer.main(SIGNED)
    err = capfd.readouterr().err
    assert status == 0, err
    assert not records_state.records()


def test_a_clean_run_is_green(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No findings: no record, zizmor's 0."""
    records_state.enter(monkeypatch, signer.Ran(0, b"[]", b""))
    status = signer.main(SIGNED)
    err = capfd.readouterr().err
    assert status == 0, err
    assert not records_state.records()


def test_zizmor_failing_is_its_status_and_its_stderr(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A status outside 0 and 10-14: zizmor's, its stderr, no record."""
    records_state.enter(
        monkeypatch, signer.Ran(NOT_A_SEVERITY, b"", b"error: bad input\n")
    )
    status = signer.main(SIGNED)
    err = capfd.readouterr().err
    assert status == NOT_A_SEVERITY
    assert err == "error: bad input\n"
    assert not records_state.records()


def test_no_token_is_a_finding_before_zizmor_runs(
    records_state: Records, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GH_TOKEN unset: the blindness finding, 1, zizmor never run."""
    records_state.env["GH_TOKEN"] = ""
    asked = records_state.enter(monkeypatch, signer.Ran(0, b"[]", b""))
    status = signer.main(SIGNED)
    assert status == 1
    assert asked == []
    want: list[object] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "",
            "message": "GH_TOKEN is unset — the offline run skips every",
            "detail": ["API-backed audit this task exists for; refusing to pass blind"],
        }
    ]
    assert want == records_state.records()


def test_an_offline_degradation_is_a_finding(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Zizmor dropped to offline: its stderr, the finding, 1."""
    said = b" WARN zizmor: running in offline mode\n"
    records_state.enter(monkeypatch, signer.Ran(0, b"[]", said))
    status = signer.main(SIGNED)
    err = capfd.readouterr().err
    assert status == 1
    assert err.startswith(said.decode()), err
    want: list[object] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "",
            "message": "zizmor degraded to offline despite GH_TOKEN —",
            "detail": ["the online audits did not run; a green result would be blind"],
        }
    ]
    assert want == records_state.records()


def test_no_zizmor_is_refused_by_name(
    records_state: Records,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No zizmor on PATH: refused by name, 1, its one record (#1934)."""
    monkeypatch.setattr(signer, "ZIZMOR", "/nonexistent/zizmor")
    for name, value in records_state.env.items():
        monkeypatch.setenv(name, value)
    status = signer.main(["--signer-workflow", SIGNER_WORKFLOW])
    _out, err = capfd.readouterr()
    assert status == 1
    assert f"{TASK}: zizmor is not on PATH" in err
    want: list[object] = [
        {
            "v": 1,
            "task": TASK,
            "subject": "",
            "message": "zizmor is not on PATH",
            "detail": [],
        }
    ]
    assert want == records_state.records()


WORKFLOW = """\
name: a
on: push
permissions: {}
concurrency:
  group: a
  cancel-in-progress: true
jobs:
  a:
    name: a
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/setup-go@v5
"""


class PinnedZizmor:
    """The belt's pinned zizmor, `--offline`, on a planted workflow."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.make_env = env
        self.tmp = tmp_path

    def plant(self, monkeypatch: pytest.MonkeyPatch, workflow: str) -> Path:
        """Stand in a tree holding one planted workflow, the belt's zizmor to judge it.

        Returns:
            The findings file the CLI writes.

        """
        if support.optional_tool("zizmor") is None:
            pytest.skip("the belt's zizmor is not on PATH")
        tmp = Path(tempfile.mkdtemp(dir=self.tmp)).resolve()
        (tmp / ".github" / "workflows").mkdir(parents=True)
        (tmp / ".github" / "workflows" / "a.yml").write_text(workflow, encoding="utf-8")
        file = tmp / "findings.jsonl"
        for name, value in self.make_env(
            GH_TOKEN=STANDIN,
            MISE_TASK_NAME=TASK,
            ORG_FINDINGS=str(file),
            PYTHONUTF8="1",
        ).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(tmp)
        return file


def records(file: Path) -> list[object]:
    """Read back what the CLI recorded.

    Returns:
        Each record, in order; none when the file was not written.

    """
    lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
    return [decoded.load_json(line) for line in lines]


@fixture
def pinned_zizmor(tmp_path: Path, env: support.Env) -> PinnedZizmor:
    """Give each row its own PinnedZizmor.

    Returns:
        The row's PinnedZizmor.

    """
    return PinnedZizmor(tmp_path, env=env)


def test_an_unpinned_action_is_the_record(
    pinned_zizmor: PinnedZizmor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unpinned `uses:`: the record read back, zizmor's 14."""
    file = pinned_zizmor.plant(monkeypatch, WORKFLOW)
    status = signer.main(["--signer-workflow", SIGNER_WORKFLOW, "--offline", *ARGS])
    err = capfd.readouterr().err
    got = records(file)
    assert status == HIGH, err
    path = ".github/workflows/a.yml"
    want: list[object] = [
        {
            "v": 1,
            "task": TASK,
            "subject": path,
            "message": "unpinned-uses: actions/setup-go@v5",
            "detail": [
                "unpinned action reference",
                "severity: High",
                f"{path}:12: this step",
                (
                    f"{path}:12: action is not pinned to a hash"
                    " (required by blanket policy)"
                ),
            ],
        }
    ]
    assert want == got


def test_two_of_one_audit_in_one_file_are_two_messages(
    pinned_zizmor: PinnedZizmor,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two unpinned `uses:` in one file: two records, two messages."""
    two = WORKFLOW + "      - uses: actions/setup-node@v4\n"
    file = pinned_zizmor.plant(monkeypatch, two)
    status = signer.main(["--signer-workflow", SIGNER_WORKFLOW, "--offline", *ARGS])
    err = capfd.readouterr().err
    got = records(file)
    assert status == HIGH, err
    messages = [decoded.as_object(r, "a record")["message"] for r in got]
    want: list[object] = [
        "unpinned-uses: actions/setup-go@v5",
        "unpinned-uses: actions/setup-node@v4",
    ]
    assert want == messages


STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""


class Task:
    """audit:actions' own body over a stub zizmor, in a planted tree."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        canon: support.Canon,
        env: support.Env,
        track: support.Track,
    ) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.plant_canon = canon
        self.make_env = env
        self.track_tree = track
        self.tmp = tmp_path

    def run_task(
        self, document: object, status: int
    ) -> subprocess.CompletedProcess[str]:
        """Plant a workflow and the canon's identity, then run the body.

        Returns:
            The finished process.

        """
        tmp = Path(tempfile.mkdtemp(dir=self.tmp)).resolve()
        root = tmp / "tree"
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "a.yml").write_text("on: push\n")
        # The task runs the command from the tree it names the canon.
        self.plant_canon(root)
        (root / "security").mkdir()
        (root / "security" / "identity.toml").write_text(
            f'signer-workflow = "{SIGNER_WORKFLOW}"\n', encoding="utf-8"
        )
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "zizmor"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        doc = tmp / "doc.json"
        doc.write_text(json.dumps(document), encoding="utf-8")
        self.log = tmp / "zizmor.log"
        self.file = tmp / "findings.jsonl"
        config = decoded.load_toml((HERE / "config.toml").read_bytes())
        tasks = decoded.as_object(config["tasks"], "tasks")
        body = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], "run")
        self.track_tree(root)
        run = support.task_run(TASK, body)
        return subprocess.run(
            run.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.make_env(
                **run.env,
                PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                ORG_BELT_DIR=str(HERE),
                ORG_CANON_DIR=str(root),
                ORG_PYTHON=sys.executable,
                ORG_FINDINGS=str(self.file),
                GH_TOKEN=STANDIN,
                STUB_LOG=str(self.log),
                STUB_DOC=str(doc),
                STUB_RC=str(status),
                PYTHONUTF8="1",
            ),
            cwd=root,
            check=False,
        )


@fixture
def task(
    tmp_path: Path, canon: support.Canon, env: support.Env, track: support.Track
) -> Task:
    """Give each row its own Task.

    Returns:
        The row's Task.

    """
    return Task(tmp_path, canon=canon, env=env, track=track)


def test_a_remaining_finding_is_recorded_and_zizmor_s_red(task: Task) -> None:
    """A third-party stale ref: one record, zizmor's 11, one run."""
    third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
    done = task.run_task([third], PLANTED_SEVERITY)
    assert done.returncode == PLANTED_SEVERITY, done.stderr
    logged: list[str] = task.log.read_text(encoding="utf-8").splitlines()
    once: list[str] = ["--format=json\t--persona=auditor\t.github/workflows/a.yml"]
    assert logged == once
    lines = task.file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = decoded.as_object(decoded.load_json(lines[0]), "the record")
    assert record["subject"] == ".github/workflows/continuous.yml"


def test_a_clean_run_is_no_record_and_green(task: Task) -> None:
    """No findings: no record, 0."""
    done = task.run_task([], 0)
    assert done.returncode == 0, done.stderr
    assert not task.file.exists()

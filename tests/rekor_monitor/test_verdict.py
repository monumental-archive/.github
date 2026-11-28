"""`belt verdict`, one row per outcome of a leg (#1339).

Each row calls the command's `main` in this process with the leg's last
step's arguments (#1673, #1699): mutation, coverage and the type checker
see the call. Each asserts the colour
and the message. The reports are stele v0.24.0's own
(testdata/SOURCE.md): a run that judged to the head (PASS, exit 0) and a
run whose budget ended before its first chunk was done (CANNOT_JUDGE,
exit 4, nothing judged). A row that needs a shape no live run produced
edits a copy in code and says which field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from belt import decoded
from belt.rekor_monitor import verdict
from belt.typed_pytest import fixture

if TYPE_CHECKING:
    import pytest

    from tests import support

COMMAND = "verdict"

DATA = Path(__file__).resolve().parent / "testdata"

# The budget run's stopped fact, verbatim.
STOPPED = (
    "budget: ctlog: the run's budget ended waiting 5s on ctlog: get-entries"
    " 448196522..448206521: the log or the transport failed, which says"
    " nothing about its entries: context deadline exceeded"
)


def load(name: str) -> dict[str, object]:
    """Read one captured report.

    Returns:
        The report.

    """
    text = (DATA / name).read_text(encoding="utf-8")
    return decoded.as_object(decoded.load_json(text), name)


def advanced() -> dict[str, object]:
    """Make the budget run's report one whose leg advanced one chunk.

    Edits `judged` of the real CANNOT_JUDGE report: no live run advanced
    and then stopped.

    Returns:
        The edited report.

    """
    doc = load("report-budget.json")
    for listed in decoded.as_array(doc["facts"], "facts"):
        fact = decoded.as_object(listed, "a fact")
        if fact["name"] == "judged":
            fact["value"] = "448196522..448206521"
    return doc


def finding(index: int, condition: str) -> dict[str, object]:
    """Write one finding as stele records it (internal/assert/reconcile.go).

    Returns:
        The finding.

    """
    return {"subject": f"CT index {index}", "assertion": condition, "detail": "x"}


def as_the_leg(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], env: support.Env
) -> None:
    """Set the world the leg starts the helper's main in.

    The leg runs `belt verdict` with no mise task around it, so the
    finding writer names the command (#1934): argv[0] is its name, and
    the environment is the allow-list's, never the running suite's
    MISE_TASK_NAME or ORG_FINDINGS.
    """
    monkeypatch.setattr(sys, "argv", [COMMAND, *argv])
    for name, value in env().items():
        monkeypatch.setenv(name, value)


class Verdict:
    """One row per branch of verdict.judge()."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Lay out a report file."""
        self.make_env = env
        self.report = Path(str(tmp_path)) / "report.json"

    def enter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        code: str,
        doc: dict[str, object] | None,
        saved: str = "true",
    ) -> list[str]:
        """Write the report (None: stele wrote none) and set the leg's world.

        Returns:
            The argv the run is given.

        """
        text = json.dumps(doc) if doc is not None else ""
        self.report.write_text(text, encoding="utf-8")
        argv = ["--exit-code", code, "--report", str(self.report), "--saved", saved]
        as_the_leg(monkeypatch, argv, self.make_env)
        return argv


@fixture
def verdict_state(tmp_path: Path, env: support.Env) -> Verdict:
    """Give each row its own Verdict.

    Returns:
        The row's Verdict.

    """
    return Verdict(tmp_path, env=env)


def test_judged_to_the_head(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0, saved: green."""
    argv = verdict_state.enter(monkeypatch, "0", load("report-pass.json"))
    status = verdict.main(argv)
    out = capfd.readouterr().out
    assert status == 0, out
    assert out.strip() == "verdict: green: judged to the head of the log"


def test_fail_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 1, saved: red by the findings."""
    doc = load("report-pass.json")
    doc["verdict"] = "FAIL"
    doc["findings"] = [finding(448221200, "foreign"), finding(448221200, "chain")]
    argv = verdict_state.enter(monkeypatch, "1", doc)
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: 2 finding(s): a certificate did not reconcile;"
        " each has its issue"
    )


def test_advanced_is_green(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 4, advanced, no finding: green, the outstanding range named."""
    argv = verdict_state.enter(monkeypatch, "4", advanced())
    status = verdict.main(argv)
    out = capfd.readouterr().out
    assert status == 0, out
    assert (
        out.strip()
        == f"verdict: green: advanced; outstanding 448196522..448222426 ({STOPPED})"
    )


def test_finding_under_cannot_judge_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 4, advanced, a finding: red whatever the exit code."""
    doc = advanced()
    doc["findings"] = [finding(448200000, "run")]
    argv = verdict_state.enter(monkeypatch, "4", doc)
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: 1 finding(s): a certificate did not reconcile;"
        " each has its issue"
    )


def test_judged_nothing_is_green(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 4, saved, no finding, nothing judged: green, stele's reason printed.

    A leg is red only for its own failure (#1967): whether the run
    gained is judged once, after the last leg (gain.py).
    """
    argv = verdict_state.enter(monkeypatch, "4", load("report-budget.json"))
    status = verdict.main(argv)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert not err
    assert out.strip() == f"verdict: green: stele judged nothing ({STOPPED})"


def test_killed_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No exit status (cancelled or timed out): red."""
    argv = verdict_state.enter(monkeypatch, "", None, saved="false")
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: stele did not finish: no exit status was"
        " recorded (a step before it failed, or the leg was cancelled or"
        " timed out)"
    )


def test_no_report_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stele refused (exit 2, empty stdout), nothing saved: red, named."""
    argv = verdict_state.enter(monkeypatch, "2", None, saved="false")
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: stele exited 2 and the checkpoint was not saved:"
        " stele wrote no report"
    )


def test_filing_failed_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report, but the checkpoint not saved: a finding was not filed."""
    argv = verdict_state.enter(
        monkeypatch, "0", load("report-pass.json"), saved="false"
    )
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: stele exited 0 and the checkpoint was not saved:"
        " a finding could not be filed"
    )


def test_other_exit_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saved leg that exited 3: red, named."""
    argv = verdict_state.enter(monkeypatch, "3", load("report-budget.json"))
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == "::error::verdict: stele exited 3"


def test_facts_not_stele_shape_is_red(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saved, a finding, facts that are not stele's: red, not green.

    prop_verdict.py's example (#1557): the facts could not be read,
    so the finding was not counted and exit 0 judged the leg green.
    """
    doc = load("report-pass.json")
    doc["findings"] = [finding(448221200, "chain")]
    doc["facts"] = [1]
    argv = verdict_state.enter(monkeypatch, "0", doc)
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: stele exited 0 and its report could not be judged:"
        " the report could not be read"
    )


def test_report_nested_past_the_parser(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON nested past the parser's depth: red by name, no traceback."""
    verdict_state.report.write_bytes(b"[" * 100_000)
    argv = [
        "--exit-code",
        "0",
        "--report",
        str(verdict_state.report),
        "--saved",
        "true",
    ]
    as_the_leg(monkeypatch, argv, env)
    status = verdict.main(argv)
    out, err = capfd.readouterr()
    assert status == 1, err
    assert not out
    assert err.strip() == (
        "::error::verdict: stele exited 0 and its report could not be judged:"
        " the report could not be read"
    )


def test_a_report_not_of_stele_s_shape_is_unreadable(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each wrong shape, read where the report is decoded: one refusal (#1683)."""
    target = json.dumps("assert signer-certificates")
    rows = {
        "a fact's value is not a string": (
            f'{{"target": {target}, "facts": [{{"name": "judged", "value": 3}}]}}'
        ),
        "a fact has no value": (
            f'{{"target": {target}, "facts": [{{"name": "judged"}}]}}'
        ),
        "the facts are not a list": f'{{"target": {target}, "facts": {{}}}}',
        "the findings are not a list": f'{{"target": {target}, "findings": "x"}}',
        "the report is not an object": "[]",
        "a key appears twice": f'{{"target": {target}, "target": {target}}}',
        "a number JSON does not have": f'{{"target": {target}, "findings": [NaN]}}',
    }
    argv = [
        "--exit-code",
        "0",
        "--report",
        str(verdict_state.report),
        "--saved",
        "true",
    ]
    for text in rows.values():
        verdict_state.report.write_text(text, encoding="utf-8")
        as_the_leg(monkeypatch, argv, env)
        status = verdict.main(argv)
        out, err = capfd.readouterr()
        assert status == 1, err
        assert not out
        assert err.strip() == (
            "::error::verdict: stele exited 0 and its report could not"
            " be judged: the report could not be read"
        )


def test_report_from_another_target(
    verdict_state: Verdict,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another verb's report: named, never read on."""
    doc = load("report-budget.json")
    doc["target"] = "assert tags"
    argv = verdict_state.enter(monkeypatch, "4", doc, saved="false")
    status = verdict.main(argv)
    err = capfd.readouterr().err
    assert status == 1
    assert err.strip() == (
        "::error::verdict: stele exited 4 and the checkpoint was not saved:"
        " the report is not stele's assert signer-certificates report"
    )

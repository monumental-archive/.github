#!/usr/bin/env python3
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

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import decoded
import zizmor_signer as signer

HERE = Path(__file__).resolve().parent


def fixture(name: str) -> list[dict[str, object]]:
    """Read a recorded zizmor run: its finding array, each an object.

    Returns:
        The findings, as zizmor wrote them.

    """
    text = (HERE / "testdata" / name).read_text()
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


REAL = fixture("zizmor-1.32.0-canon.json")
MISMATCH = fixture("zizmor-1.32.0-ref-version-mismatch.json")
SIGNER_WORKFLOW = "monumental-archive/signer/.github/workflows/sign.yml"
SIGNER_REF = SIGNER_WORKFLOW + "@cffec9441210d46f237099d8785098aacf5b8273"
MISMATCH_REF = SIGNER_WORKFLOW + "@0d6c6e297684c8ba401fb9c212532628fe552148"


def run(findings: object, workflow: str = SIGNER_WORKFLOW) -> tuple[int, str, str]:
    """Run the CLI on a finding array.

    Returns:
        The exit status, what it printed, and what it printed to stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO(json.dumps(findings))
    with (
        mock.patch.object(sys, "stdin", stdin),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        status = signer.main(["--signer-workflow", workflow])
    return status, out.getvalue(), err.getvalue()


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


class RealCanonOutput(unittest.TestCase):
    """The canon's own zizmor run, as it came."""

    def test_the_fixture_is_the_ten_signer_findings(self) -> None:
        """The fixture is what the issue measured: ten, one rule, one ref."""
        self.assertEqual(10, len(REAL))
        idents: set[object] = {f["ident"] for f in REAL}
        rules: set[object] = {"stale-action-refs"}
        self.assertEqual(rules, idents)
        refs = {r for f in REAL for r in signer.references(f)}
        want: set[str] = {SIGNER_REF}
        self.assertEqual(want, refs)

    def test_all_ten_are_dropped_and_counted(self) -> None:
        """The canon goes green, and says what it dropped."""
        status, out, err = run(REAL)
        self.assertEqual(0, status, err)
        self.assertIn("10 stale-action-refs finding(s) on the signer dropped", out)
        self.assertEqual("", err)


class RefVersionMismatch(unittest.TestCase):
    """The signer's `# main` pin behind `main` (#1340), as zizmor wrote it."""

    def test_the_fixture_is_one_signer_mismatch(self) -> None:
        """The fixture is the measured shape: the reference rides the Hidden job."""
        self.assertEqual(1, len(MISMATCH))
        self.assertEqual("ref-version-mismatch", MISMATCH[0]["ident"])
        primary = [
            concrete(loc)["feature"]
            for loc in locations(MISMATCH[0])
            if symbolic(loc)["kind"] == "Primary"
        ]
        feature: list[object] = ["main"]
        self.assertEqual(feature, primary)
        want: list[str] = [MISMATCH_REF]
        self.assertEqual(want, signer.references(MISMATCH[0]))

    def test_the_signer_mismatch_is_dropped_and_counted(self) -> None:
        """The signer's `# main` behind its branch head goes green, said aloud."""
        status, out, err = run(MISMATCH)
        self.assertEqual(0, status, err)
        self.assertIn(
            "1 ref-version-mismatch finding(s) on the signer dropped"
            " (monumental-archive/signer is pinned on `# main`",
            out,
        )
        self.assertEqual("", err)

    def test_both_audits_together_are_dropped(self) -> None:
        """The scheduled run carries both; each is counted on its own line."""
        status, out, err = run([*REAL, *MISMATCH])
        self.assertEqual(0, status, err)
        self.assertIn("10 stale-action-refs finding(s) on the signer dropped", out)
        self.assertIn("1 ref-version-mismatch finding(s) on the signer dropped", out)

    def test_another_owner_mismatch_is_kept(self) -> None:
        """The same finding on another owner's repository still reds."""
        other = rewritten(
            MISMATCH[0],
            "Hidden",
            "monumental-archive/signer/",
            "someone-else/signer/",
        )
        status, out, err = run([other])
        self.assertEqual(1, status)
        self.assertIn("0 ref-version-mismatch finding(s) on the signer dropped", out)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", err)

    def test_a_mismatch_with_no_readable_reference_is_kept(self) -> None:
        """A Hidden location with no `uses:` line names nothing; it is kept."""
        blind = rewritten(MISMATCH[0], "Hidden", "uses:", "calls:")
        none: list[str] = []
        self.assertEqual(none, signer.references(blind))
        status, _, err = run([blind])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", err)

    def test_the_comment_alone_is_not_read_as_a_reference(self) -> None:
        """Primary carries only `main`; dropping the Hidden location blinds it."""
        blind = copy.deepcopy(MISMATCH[0])
        blind["locations"] = [
            loc for loc in locations(blind) if symbolic(loc)["kind"] == "Primary"
        ]
        status, _, err = run([blind])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", err)

    def test_a_uses_in_prose_is_not_a_reference(self) -> None:
        """A commented `uses:` beside the signer's is not read as a second ref."""
        prose = rewritten(
            MISMATCH[0],
            "Hidden",
            "    with:\n",
            "    # relative `uses: actions/checkout@v4` in prose\n    with:\n",
        )
        want: list[str] = [MISMATCH_REF]
        self.assertEqual(want, signer.references(prose))
        self.assertEqual(0, run([prose])[0])

    def test_a_second_reference_elsewhere_keeps_it(self) -> None:
        """Every named reference must be the signer's, not just one of them."""
        two = rewritten(
            MISMATCH[0],
            "Hidden",
            "    with:\n",
            "    with:\n      - uses: actions/checkout@v4\n",
        )
        status, _, err = run([two])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", err)

    def test_another_audit_on_the_signer_is_kept(self) -> None:
        """An unrelated audit carrying the same locations still reds."""
        other_rule = copy.deepcopy(MISMATCH[0])
        other_rule["ident"] = "unpinned-uses"
        status, _, err = run([other_rule])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain: unpinned-uses", err)


class Kept(unittest.TestCase):
    """Every finding the exemption does not name still reds."""

    def test_another_repository_reusable_workflow_is_kept(self) -> None:
        """The same audit on someone else's reusable workflow is reported."""
        other = repointed(
            REAL[0],
            "monumental-archive/other/.github/workflows/sign.yml@" + "a" * 40,
        )
        status, out, err = run([other])
        self.assertEqual(1, status)
        self.assertIn("0 stale-action-refs finding(s) on the signer dropped", out)
        self.assertIn("1 finding(s) remain: stale-action-refs", err)

    def test_a_third_party_action_is_kept(self) -> None:
        """A stale third-party action pin is exactly what the audit is for."""
        third = repointed(REAL[0], "actions/checkout@" + "b" * 40)
        status, _, err = run([*REAL, third])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain", err)

    def test_a_repository_that_only_shares_the_prefix_is_kept(self) -> None:
        """`signer-fork` is not `signer`: repositories match whole."""
        near = repointed(
            REAL[0],
            "monumental-archive/signer-fork/.github/workflows/sign.yml@" + "c" * 40,
        )
        self.assertEqual(1, run([near])[0])

    def test_another_audit_on_the_signer_is_kept(self) -> None:
        """The exemption is one rule wide, not the signer wide."""
        other_rule = copy.deepcopy(REAL[0])
        other_rule["ident"] = "impostor-commit"
        status, _, err = run([other_rule])
        self.assertEqual(1, status)
        self.assertIn("remain: impostor-commit", err)

    def test_a_finding_with_no_readable_reference_is_kept(self) -> None:
        """When zizmor's shape gives no reference, nothing is assumed."""
        blind = copy.deepcopy(REAL[0])
        for location in locations(blind):
            symbolic(location)["kind"] = "Related"
        self.assertEqual(1, run([blind])[0])

    def test_a_reference_without_a_ref_is_kept(self) -> None:
        """A Primary feature with no `@` is not a reference."""
        self.assertEqual(1, run([repointed(REAL[0], "monumental-archive/signer")])[0])


class Matching(unittest.TestCase):
    """How the signer's repository is read and compared."""

    def test_case_differences_do_not_matter(self) -> None:
        """GitHub owner and repository names are case-insensitive."""
        upper = repointed(REAL[0], SIGNER_REF.replace("signer", "Signer", 1))
        self.assertEqual(0, run([upper])[0])

    def test_an_ignored_finding_is_not_reported(self) -> None:
        """Per-site `zizmor: ignore` comments are zizmor's, not this filter's."""
        ignored = repointed(REAL[0], "actions/checkout@" + "d" * 40)
        ignored["ignored"] = True
        self.assertEqual(0, run([*REAL, ignored])[0])

    def test_no_findings_is_green_and_counts_zero(self) -> None:
        """An empty run drops nothing and passes."""
        status, out, _ = run([])
        self.assertEqual(0, status)
        self.assertIn("0 stale-action-refs finding(s) on the signer dropped", out)


class Refusals(unittest.TestCase):
    """Inputs the filter will not guess about."""

    def test_a_signer_declaration_with_no_repository_is_refused(self) -> None:
        """A declaration that names no owner/repo cannot exempt anything."""
        status, _, err = run(REAL, workflow="signer")
        self.assertEqual(2, status)
        self.assertIn("names no owner/repository", err)

    def test_a_finding_of_the_wrong_shape_is_kept_not_raised(self) -> None:
        """The shrunk examples: kept and reported, never a traceback (#1555)."""
        shapes: tuple[dict[str, object], ...] = (
            {"ident": []},
            {"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]},
            {"ident": "stale-action-refs", "locations": 5},
            {
                "ident": "stale-action-refs",
                "locations": [{"symbolic": {"kind": "Primary"}, "concrete": 1}],
            },
        )
        for finding in shapes:
            with self.subTest(finding=finding):
                status, _, err = run([finding])
                self.assertEqual(1, status)
                self.assertIn("1 finding(s) remain", err)

    def test_a_process_with_no_stdin_is_refused_by_name(self) -> None:
        """No standard input is a refusal, never a traceback (#1681)."""
        err = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", None),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            status = signer.main(["--signer-workflow", SIGNER_WORKFLOW])
        self.assertEqual(2, status)
        self.assertEqual(
            err.getvalue(),
            "zizmor-signer: no standard input: the process was started without one\n",
        )

    def test_input_that_is_not_the_finding_array_is_refused(self) -> None:
        """A SARIF document or an error object is not silently empty."""
        self.assertEqual(2, run({"runs": []})[0])


if __name__ == "__main__":
    unittest.main()

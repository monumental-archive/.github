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
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "zizmor_signer",
    HERE / "zizmor_signer.py",
)
signer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(signer)

REAL = json.loads((HERE / "testdata" / "zizmor-1.32.0-canon.json").read_text())
MISMATCH = json.loads(
    (HERE / "testdata" / "zizmor-1.32.0-ref-version-mismatch.json").read_text(),
)
SIGNER_WORKFLOW = "monumental-archive/signer/.github/workflows/sign.yml"
SIGNER_REF = SIGNER_WORKFLOW + "@cffec9441210d46f237099d8785098aacf5b8273"
MISMATCH_REF = SIGNER_WORKFLOW + "@0d6c6e297684c8ba401fb9c212532628fe552148"


def run(findings: object, workflow: str = SIGNER_WORKFLOW) -> tuple[int, str, str]:
    """Run the CLI on a finding array.

    Returns:
        The exit status, what it printed, and what it printed to stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    real = signer.sys.stdin
    signer.sys.stdin = io.StringIO(json.dumps(findings))
    try:
        with redirect_stdout(out), redirect_stderr(err):
            status = signer.main(["--signer-workflow", workflow])
    finally:
        signer.sys.stdin = real
    return status, out.getvalue(), err.getvalue()


def repointed(finding: dict, reference: str) -> dict:
    """Return a copy of a real finding whose Primary reference is changed.

    Returns:
        The finding with every Primary location's feature replaced.

    """
    changed = copy.deepcopy(finding)
    for location in changed["locations"]:
        if location["symbolic"]["kind"] == "Primary":
            location["concrete"]["feature"] = reference
    return changed


def rewritten(finding: dict, kind: str, old: str, new: str) -> dict:
    """Return a copy of a real finding with text replaced in one location kind.

    Returns:
        The finding with `old` replaced by `new` in the feature of every
        location of that kind.

    """
    changed = copy.deepcopy(finding)
    for location in changed["locations"]:
        if location["symbolic"]["kind"] == kind:
            feature = location["concrete"]["feature"]
            location["concrete"]["feature"] = feature.replace(old, new)
    return changed


class RealCanonOutput(unittest.TestCase):
    """The canon's own zizmor run, as it came."""

    def test_the_fixture_is_the_ten_signer_findings(self) -> None:
        """The fixture is what the issue measured: ten, one rule, one ref."""
        self.assertEqual(10, len(REAL))
        self.assertEqual({"stale-action-refs"}, {f["ident"] for f in REAL})
        refs = {r for f in REAL for r in signer.references(f)}
        self.assertEqual({SIGNER_REF}, refs)

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
            loc["concrete"]["feature"]
            for loc in MISMATCH[0]["locations"]
            if loc["symbolic"]["kind"] == "Primary"
        ]
        self.assertEqual(["main"], primary)
        self.assertEqual([MISMATCH_REF], signer.references(MISMATCH[0]))

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
        self.assertEqual([], signer.references(blind))
        status, _, err = run([blind])
        self.assertEqual(1, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", err)

    def test_the_comment_alone_is_not_read_as_a_reference(self) -> None:
        """Primary carries only `main`; dropping the Hidden location blinds it."""
        blind = copy.deepcopy(MISMATCH[0])
        blind["locations"] = [
            loc for loc in blind["locations"] if loc["symbolic"]["kind"] == "Primary"
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
        self.assertEqual([MISMATCH_REF], signer.references(prose))
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
        for location in blind["locations"]:
            location["symbolic"]["kind"] = "Related"
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
        for finding in (
            {"ident": []},
            {"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]},
            {"ident": "stale-action-refs", "locations": 5},
            {
                "ident": "stale-action-refs",
                "locations": [{"symbolic": {"kind": "Primary"}, "concrete": 1}],
            },
        ):
            with self.subTest(finding=finding):
                status, _, err = run([finding])
                self.assertEqual(1, status)
                self.assertIn("1 finding(s) remain", err)

    def test_input_that_is_not_the_finding_array_is_refused(self) -> None:
        """A SARIF document or an error object is not silently empty."""
        self.assertEqual(2, run({"runs": []})[0])


if __name__ == "__main__":
    unittest.main()

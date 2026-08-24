#!/usr/bin/env python3
"""Table tests for the declaration/producer join (#833).

#650's law: one row per guard branch, both directions where there are two,
and a planted failure measured rather than reasoned about. The guards here
ARE the mechanism — a join that passes when a class has no producer is
exactly the silence that let `rust-crate` owe an SBOM document nothing
emitted for forty releases.

The first row is the delivered tree itself: the canon's own policy and its
own workflows must join clean as they stand, so a class added to
`slsa/assert-policy.json` without a producer reds this file too, not only
the gate.

Mutation-checked: each planted row was run against a helper with the
corresponding check removed and observed to pass, which is the failure it
is written to catch.

stdlib `unittest`, run through the gate as `mise run test`.
"""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "plan_producers",
    Path(__file__).with_name("plan-producers.py"),
)
pp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pp)

REPO = Path(__file__).resolve().parent.parent
POLICY = REPO / "slsa" / "assert-policy.json"
WORKFLOWS = REPO / ".github" / "workflows"

# The smallest policy carrying the shape under test: one class owing one
# planned document, one owing an unplanned asset, one owing nothing.
POLICY_UNDER_TEST = {
    "evidence": {
        "classes": {
            "rust-crate": {
                "bundles": ["attestations-crates.intoto.jsonl"],
                "assetPrefixes": [
                    {"prefix": "sbom-cargo-", "owedFrom": "1.42.0", "planned": True},
                ],
            },
            "pgrx-extension": {
                "bundles": ["attestations-extensions.intoto.jsonl"],
                "assetPrefixes": [
                    {"prefix": "attestations-extimg-pg"},
                    {"prefix": "sbom-pgrx-", "owedFrom": "1.42.0", "planned": True},
                ],
            },
            "source-archive": {"bundles": ["attestations-source.intoto.jsonl"]},
        },
    },
}

CRATE_LEG = """\
name: build-rust-crate
# plan-producer: rust-crate sbom-cargo-
jobs:
  build:
    steps:
      - run: |
          jq -n '[{class: "rust-crate", doc: ("sbom-cargo-" + . + "-crate")}]'
      - uses: actions/upload-artifact@0000000000000000000000000000000000000000
        with:
          name: sbom-plan-crate
"""

PGRX_LEG = """\
name: build-pgrx-extension
# plan-producer: pgrx-extension sbom-pgrx-
jobs:
  build:
    steps:
      - run: |
          jq -n '[{class: "pgrx-extension", doc: ("sbom-pgrx-" + $pkg)}]'
      - uses: actions/upload-artifact@0000000000000000000000000000000000000000
        with:
          name: sbom-plan-extension
"""


def tree(**files: str) -> dict[str, str]:
    """Name a planted set of workflow files.

    Args:
        **files: file stem to contents.

    Returns:
        The mapping `judge` reads, with workflow-shaped paths.

    """
    return {f".github/workflows/{k}.yml": v for k, v in files.items()}


def crate(leg: str) -> dict[str, str]:
    """Plant a mutated crate leg beside an untouched pgrx one.

    Args:
        leg: the `build-rust-crate.yml` under test.

    Returns:
        The two-file tree every mutation row judges.

    """
    return tree(**{"build-rust-crate": leg, "build-pgrx-extension": PGRX_LEG})


def delivered() -> tuple[dict, dict[str, str]]:
    """Read the canon's real policy and its real producer surface.

    Returns:
        The decoded policy and the workflow files the check reads.

    """
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    paths = [*sorted(WORKFLOWS.glob("build-*.yml")), WORKFLOWS / "publish.yml"]
    return policy, {
        str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in paths
    }


WHOLE = crate(CRATE_LEG)


class DeliveredTreeTest(unittest.TestCase):
    """The canon's own policy and workflows, exactly as they ship."""

    def setUp(self) -> None:
        """Skip where the canon's documents are not in the tree."""
        if not POLICY.exists() or not WORKFLOWS.is_dir():
            self.skipTest("not the canon's tree")

    def test_the_delivered_tree_joins_clean(self) -> None:
        """Every planned obligation the org declares has its producer."""
        policy, files = delivered()
        self.assertEqual(pp.judge(policy, files), [])

    def test_the_policy_declares_planned_obligations(self) -> None:
        """An empty obligation set would make every row above vacuous."""
        policy, _ = delivered()
        self.assertTrue(pp.obligations(policy))

    def test_the_crate_class_is_among_them(self) -> None:
        """The defect's own class, joined rather than merely present."""
        policy, files = delivered()
        self.assertIn(("rust-crate", "sbom-cargo-"), pp.obligations(policy))
        crate = files[".github/workflows/build-rust-crate.yml"]
        self.assertIn(("rust-crate", "sbom-cargo-"), pp.markers(crate)[0])
        self.assertIn("sbom-cargo-", pp.emitted(crate))


class ObligationsTest(unittest.TestCase):
    """Which rows of the policy are this check's population."""

    def test_planned_entries_are_read(self) -> None:
        """The judged set is the planned obligations, both classes."""
        self.assertEqual(
            pp.obligations(POLICY_UNDER_TEST),
            {("rust-crate", "sbom-cargo-"), ("pgrx-extension", "sbom-pgrx-")},
        )

    def test_an_unplanned_prefix_is_not_an_obligation(self) -> None:
        """`attestations-extimg-pg` is attached by a leg, not planned."""
        prefixes = {p for _, p in pp.obligations(POLICY_UNDER_TEST)}
        self.assertNotIn("attestations-extimg-pg", prefixes)

    def test_a_class_owing_only_bundles_is_absent(self) -> None:
        """A class with no planned prefix demands no producer."""
        classes = {c for c, _ in pp.obligations(POLICY_UNDER_TEST)}
        self.assertNotIn("source-archive", classes)


class JoinTest(unittest.TestCase):
    """The three directions the defect reads, planted both ways."""

    def test_the_whole_tree_passes(self) -> None:
        """The green direction, so every red row below means something."""
        self.assertEqual(pp.judge(POLICY_UNDER_TEST, WHOLE), [])

    def test_a_class_with_no_producer_is_named(self) -> None:
        """#833 itself: the declaration stands and the leg is gone."""
        gone = tree(**{"build-pgrx-extension": PGRX_LEG})
        findings = pp.judge(POLICY_UNDER_TEST, gone)
        self.assertEqual(len(findings), 1)
        self.assertIn("rust-crate", findings[0])
        self.assertIn("sbom-cargo-", findings[0])

    def test_restoring_the_producer_makes_it_green(self) -> None:
        """The other direction of the same row."""
        self.assertEqual(pp.judge(POLICY_UNDER_TEST, WHOLE), [])

    def test_the_emission_alone_does_not_satisfy_the_class(self) -> None:
        """A leg that emits the prefix but claims it for nobody."""
        leg = CRATE_LEG.replace("# plan-producer: rust-crate sbom-cargo-\n", "")
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("declares itself its producer" in f for f in findings))
        self.assertTrue(any("marker claims it for a class" in f for f in findings))

    def test_a_sibling_producer_does_not_satisfy_the_obligation(self) -> None:
        """The prefix is shared; the join is the class, never the prefix."""
        leg = CRATE_LEG.replace("rust-crate", "rust-binary")
        findings = pp.judge(
            POLICY_UNDER_TEST,
            tree(**{"build-rust-binary": leg, "build-pgrx-extension": PGRX_LEG}),
        )
        self.assertTrue(any("rust-crate" in f and "sbom-cargo-" in f for f in findings))

    def test_a_marker_for_an_undeclared_class_is_refused(self) -> None:
        """A producer for a class the policy never heard of."""
        leg = CRATE_LEG.replace(
            "# plan-producer: rust-crate sbom-cargo-",
            "# plan-producer: rust-crate-next sbom-cargo-",
        )
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertTrue(any("does not declare" in f for f in findings))

    def test_a_prefix_the_class_never_declared_is_refused(self) -> None:
        """Drift on the producer side."""
        leg = CRATE_LEG.replace(
            "# plan-producer: rust-crate sbom-cargo-",
            "# plan-producer: rust-crate sbom-crates-",
        )
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertTrue(any("no such planned obligation" in f for f in findings))

    def test_a_rename_on_the_policy_side_only_is_refused(self) -> None:
        """The same drift, arrived at from the other file."""
        policy = json.loads(json.dumps(POLICY_UNDER_TEST))
        entry = policy["evidence"]["classes"]["rust-crate"]["assetPrefixes"][0]
        entry["prefix"] = "sbom-crates-"
        findings = pp.judge(policy, WHOLE)
        self.assertTrue(any("no such planned obligation" in f for f in findings))
        self.assertTrue(any("sbom-crates-" in f for f in findings))

    def test_a_marker_no_leg_emits_is_refused(self) -> None:
        """A producer removed under a marker nobody deleted."""
        leg = "\n".join(
            line
            for line in CRATE_LEG.splitlines()
            if "sbom-cargo-" not in line or "plan-producer" in line
        )
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertTrue(any("renamed" in f or "removed" in f for f in findings))

    def test_a_plan_upload_with_no_readable_prefix_is_refused(self) -> None:
        """Fail-closed: a new producer cannot arrive invisible."""
        leg = "\n".join(
            line for line in CRATE_LEG.splitlines() if "sbom-cargo-" not in line
        )
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertTrue(any("states no document prefix" in f for f in findings))

    def test_a_marker_away_from_its_class_is_refused(self) -> None:
        """The marker belongs where the class name is written."""
        leg = PGRX_LEG + "# plan-producer: rust-crate sbom-cargo-\n"
        findings = pp.judge(POLICY_UNDER_TEST, tree(**{"build-pgrx-extension": leg}))
        self.assertTrue(any("never states" in f for f in findings))

    def test_a_malformed_marker_is_refused(self) -> None:
        """A marker nobody can read is a join nobody made."""
        leg = CRATE_LEG.replace(
            "# plan-producer: rust-crate sbom-cargo-",
            "# plan-producer: rust-crate",
        )
        findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
        self.assertTrue(any("is not `# plan-producer:" in f for f in findings))


class ReaderTest(unittest.TestCase):
    """The two emission idioms and the marker, read off real shapes."""

    def test_the_env_idiom_is_read(self) -> None:
        """`release/rust-build.sh`'s caller states the prefix as env."""
        env = "          PLAN_DOC_PREFIX: sbom-image-\n"
        self.assertEqual(pp.emitted(env), {"sbom-image-"})

    def test_a_quoted_env_value_is_read(self) -> None:
        """YAML quoting is not a second prefix."""
        self.assertEqual(pp.emitted('  PLAN_DOC_PREFIX: "sbom-npm-"\n'), {"sbom-npm-"})

    def test_the_jq_idiom_is_read(self) -> None:
        """The inline emitters build the document name in jq."""
        self.assertEqual(
            pp.emitted('  | map({class: "wasm-npm", doc: ("sbom-npm-" + .),\n'),
            {"sbom-npm-"},
        )

    def test_prose_about_a_prefix_is_not_an_emission(self) -> None:
        """A comment naming a document must not count as producing one."""
        prose = "          # `sbom-image-*` document, the second nothing\n"
        self.assertEqual(pp.emitted(prose), set())

    def test_a_download_pattern_is_not_an_upload(self) -> None:
        """publish.yml fetches the plans and emits none."""
        self.assertFalse(pp.uploads_plan("          pattern: sbom-plan-*\n"))

    def test_an_upload_is_an_upload(self) -> None:
        """The producing half of the same string."""
        up = "          name: sbom-plan-binary-${{ matrix.target }}\n"
        self.assertTrue(pp.uploads_plan(up))

    def test_a_marker_is_not_its_own_evidence(self) -> None:
        """The placement tie reads the file without its markers."""
        parked = "name: build-oci-image\n# plan-producer: rust-crate sbom-cargo-\n"
        self.assertNotIn("rust-crate", pp.strip_markers(parked))


class MainTest(unittest.TestCase):
    """The exits, which are what the gate actually reads."""

    def test_a_policy_with_no_planned_obligations_is_nothing_to_join(self) -> None:
        """An adopter's policy that plans nothing is not a defect."""
        empty = {"evidence": {"classes": {"source-archive": {"bundles": ["a"]}}}}
        self.assertEqual(pp.obligations(empty), set())

    def test_an_unreadable_policy_is_this_checks_own_input(self) -> None:
        """A policy that will not decode stops the check, loudly."""
        missing = REPO / "slsa" / "no-such-policy.json"
        err = io.StringIO()
        with (
            redirect_stderr(err),
            mock.patch("sys.argv", ["plan-producers.py", "--policy", str(missing)]),
        ):
            self.assertEqual(pp.main(), 1)
        self.assertIn("does not read as JSON", err.getvalue())


if __name__ == "__main__":
    unittest.main()

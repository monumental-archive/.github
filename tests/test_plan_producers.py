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
"""

import io
import json
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import plan_producers as pp
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:plan-producers"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


REPO = support.CANON
POLICY = REPO / "slsa" / "assert-policy.json"
WORKFLOWS = REPO / ".github" / "workflows"

# The smallest policy carrying the shape under test: one class owing one
# planned document, one owing an unplanned asset, one owing nothing.
POLICY_DOC = {
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

POLICY_UNDER_TEST = pp.read_policy(POLICY_DOC)

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


def delivered() -> tuple[pp.Policy, dict[str, str]]:
    """Read the canon's real policy and its real producer surface.

    Returns:
        The decoded policy and the workflow files the check reads.

    """
    root = REPO
    policy_file = root / POLICY.relative_to(REPO)
    policy = pp.read_policy(decoded.load_json(policy_file.read_text(encoding="utf-8")))
    workflows = root / WORKFLOWS.relative_to(REPO)
    paths = [*sorted(workflows.glob("build-*.yml")), workflows / "publish.yml"]
    return policy, {
        str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in paths
    }


WHOLE = crate(CRATE_LEG)


# The canon's own policy and workflows, exactly as they ship.
@fixture
def _set_up_test_delivered_tree_test() -> None:
    """Skip where the canon's documents are not in the tree."""
    if not POLICY.exists() or not WORKFLOWS.is_dir():
        pytest.skip("not the canon's tree")


@pytest.mark.usefixtures("_set_up_test_delivered_tree_test")
def test_the_delivered_tree_joins_clean() -> None:
    """Every planned obligation the org declares has its producer."""
    policy, files = delivered()
    assert pp.judge(policy, files) == list[str]()


@pytest.mark.usefixtures("_set_up_test_delivered_tree_test")
def test_the_policy_declares_planned_obligations() -> None:
    """An empty obligation set would make every row above vacuous."""
    policy, _ = delivered()
    assert pp.obligations(policy)


@pytest.mark.usefixtures("_set_up_test_delivered_tree_test")
def test_the_crate_class_is_among_them() -> None:
    """The defect's own class, joined rather than merely present.

    stele writes the crate plan (stele#396, #1371): the leg names its
    class, and the prefix is the policy's own, read back here.
    """
    policy, files = delivered()
    assert ("rust-crate", "sbom-cargo-") in pp.obligations(policy)
    crate = files[".github/workflows/build-rust-crate.yml"]
    assert ("rust-crate", "sbom-cargo-") in pp.markers(crate)[0]
    assert "sbom-cargo-" in pp.emitted(crate, policy)
    assert pp.emitted(crate) == set[str]()


# Which rows of the policy are this check's population.
def test_planned_entries_are_read() -> None:
    """The judged set is the planned obligations, both classes."""
    want = {("rust-crate", "sbom-cargo-"), ("pgrx-extension", "sbom-pgrx-")}
    assert pp.obligations(POLICY_UNDER_TEST) == want


def test_an_unplanned_prefix_is_not_an_obligation() -> None:
    """`attestations-extimg-pg` is attached by a leg, not planned."""
    prefixes = {p for _, p in pp.obligations(POLICY_UNDER_TEST)}
    assert "attestations-extimg-pg" not in prefixes


def test_a_class_owing_only_bundles_is_absent() -> None:
    """A class with no planned prefix demands no producer."""
    classes = {c for c, _ in pp.obligations(POLICY_UNDER_TEST)}
    assert "source-archive" not in classes


# The three directions the defect reads, planted both ways.
def test_the_whole_tree_passes() -> None:
    """The green direction, so every red row below means something."""
    assert pp.judge(POLICY_UNDER_TEST, WHOLE) == list[str]()


def test_a_class_with_no_producer_is_named() -> None:
    """#833 itself: the declaration stands and the leg is gone."""
    gone = tree(**{"build-pgrx-extension": PGRX_LEG})
    findings = pp.judge(POLICY_UNDER_TEST, gone)
    assert len(findings) == 1
    assert "rust-crate" in findings[0]
    assert "sbom-cargo-" in findings[0]


def test_restoring_the_producer_makes_it_green() -> None:
    """The other direction of the same row."""
    assert pp.judge(POLICY_UNDER_TEST, WHOLE) == list[str]()


def test_the_emission_alone_does_not_satisfy_the_class() -> None:
    """A leg that emits the prefix but claims it for nobody."""
    leg = CRATE_LEG.replace("# plan-producer: rust-crate sbom-cargo-\n", "")
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert len(findings) == 2
    assert any("declares itself its producer" in f for f in findings)
    assert any("marker claims it for a class" in f for f in findings)


def test_a_sibling_producer_does_not_satisfy_the_obligation() -> None:
    """The prefix is shared; the join is the class, never the prefix."""
    leg = CRATE_LEG.replace("rust-crate", "rust-binary")
    findings = pp.judge(
        POLICY_UNDER_TEST,
        tree(**{"build-rust-binary": leg, "build-pgrx-extension": PGRX_LEG}),
    )
    assert any("rust-crate" in f and "sbom-cargo-" in f for f in findings)


def test_a_marker_for_an_undeclared_class_is_refused() -> None:
    """A producer for a class the policy never heard of."""
    leg = CRATE_LEG.replace(
        "# plan-producer: rust-crate sbom-cargo-",
        "# plan-producer: rust-crate-next sbom-cargo-",
    )
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert any("does not declare" in f for f in findings)


def test_a_prefix_the_class_never_declared_is_refused() -> None:
    """Drift on the producer side."""
    leg = CRATE_LEG.replace(
        "# plan-producer: rust-crate sbom-cargo-",
        "# plan-producer: rust-crate sbom-crates-",
    )
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert any("no such planned obligation" in f for f in findings)


def test_a_rename_on_the_policy_side_only_is_refused() -> None:
    """The same drift, arrived at from the other file."""
    policy = decoded.load_json(json.dumps(POLICY_DOC))
    evidence = decoded.as_object(
        decoded.as_object(policy, "policy")["evidence"], "evidence"
    )
    classes = decoded.as_object(evidence["classes"], "classes")
    crate_class = decoded.as_object(classes["rust-crate"], "rust-crate")
    prefixes = decoded.as_array(crate_class["assetPrefixes"], "assetPrefixes")
    entry = decoded.as_object(prefixes[0], "assetPrefixes[0]")
    entry["prefix"] = "sbom-crates-"
    findings = pp.judge(pp.read_policy(policy), WHOLE)
    assert any("no such planned obligation" in f for f in findings)
    assert any("sbom-crates-" in f for f in findings)


def test_a_marker_no_leg_emits_is_refused() -> None:
    """A producer removed under a marker nobody deleted."""
    leg = "\n".join(
        line
        for line in CRATE_LEG.splitlines()
        if "sbom-cargo-" not in line or "plan-producer" in line
    )
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert any("renamed" in f or "removed" in f for f in findings)


def test_a_plan_upload_with_no_readable_prefix_is_refused() -> None:
    """Fail-closed: a new producer cannot arrive invisible."""
    leg = "\n".join(
        line for line in CRATE_LEG.splitlines() if "sbom-cargo-" not in line
    )
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert any("states no document prefix" in f for f in findings)


def test_a_marker_away_from_its_class_is_refused() -> None:
    """The marker belongs where the class name is written."""
    leg = PGRX_LEG + "# plan-producer: rust-crate sbom-cargo-\n"
    findings = pp.judge(POLICY_UNDER_TEST, tree(**{"build-pgrx-extension": leg}))
    assert any("never states" in f for f in findings)


def test_a_malformed_marker_is_refused() -> None:
    """A marker nobody can read is a join nobody made."""
    leg = CRATE_LEG.replace(
        "# plan-producer: rust-crate sbom-cargo-",
        "# plan-producer: rust-crate",
    )
    findings = pp.judge(POLICY_UNDER_TEST, crate(leg))
    assert any("is not `# plan-producer:" in f for f in findings)


# The two emission idioms and the marker, read off real shapes.
def test_the_env_idiom_is_read() -> None:
    """`release/rust-build.sh`'s caller states the prefix as env."""
    env = "          PLAN_DOC_PREFIX: sbom-image-\n"
    want = {"sbom-image-"}
    assert pp.emitted(env) == want


def test_a_quoted_env_value_is_read() -> None:
    """YAML quoting is not a second prefix."""
    want = {"sbom-npm-"}
    assert pp.emitted('  PLAN_DOC_PREFIX: "sbom-npm-"\n') == want


def test_the_jq_idiom_is_read() -> None:
    """The inline emitters build the document name in jq."""
    want = {"sbom-npm-"}
    assert pp.emitted('  | map({class: "wasm-npm", doc: ("sbom-npm-" + .),\n') == want


def test_the_stele_idiom_is_read_from_the_policy() -> None:
    """A stele-written plan emits under its class's planned prefix."""
    call = "  stele derive inventory-plan --class rust-crate --policy p.json \\\n"
    want = {"sbom-cargo-"}
    assert pp.emitted(call, POLICY_UNDER_TEST) == want


def test_the_stele_idiom_names_nothing_without_the_policy() -> None:
    """The prefix lives in the policy, never in the leg."""
    call = "  stele derive inventory-plan --class rust-crate --policy p.json\n"
    assert pp.emitted(call) == set[str]()


def test_a_stele_plan_for_a_class_planning_nothing_is_refused() -> None:
    """Fail-closed: a class with no planned prefix names no document."""
    leg = (
        "name: build-source\n"
        "# plan-producer: source-archive sbom-src-\n"
        "      - run: stele derive inventory-plan --class source-archive\n"
        "          name: sbom-plan-src\n"
    )
    planted = {**WHOLE, **tree(**{"build-source": leg})}
    findings = pp.judge(POLICY_UNDER_TEST, planted)
    assert any("states no document prefix" in f for f in findings)


def test_prose_about_a_prefix_is_not_an_emission() -> None:
    """A comment naming a document must not count as producing one."""
    prose = "          # `sbom-image-*` document, the second nothing\n"
    assert pp.emitted(prose) == set[str]()


def test_a_download_pattern_is_not_an_upload() -> None:
    """publish.yml fetches the plans and emits none."""
    assert not pp.uploads_plan("          pattern: sbom-plan-*\n")


def test_an_upload_is_an_upload() -> None:
    """The producing half of the same string."""
    up = "          name: sbom-plan-binary-${{ matrix.target }}\n"
    assert pp.uploads_plan(up)


def test_a_marker_is_not_its_own_evidence() -> None:
    """The placement tie reads the file without its markers."""
    parked = "name: build-oci-image\n# plan-producer: rust-crate sbom-cargo-\n"
    assert "rust-crate" not in pp.strip_markers(parked)


# The exits, which are what the gate actually reads.
def test_a_policy_with_no_planned_obligations_is_nothing_to_join() -> None:
    """An adopter's policy that plans nothing is not a defect."""
    empty = {"evidence": {"classes": {"source-archive": {"bundles": ["a"]}}}}
    assert pp.obligations(pp.read_policy(empty)) == set[tuple[str, str]]()


def test_an_unreadable_policy_is_this_checks_own_input() -> None:
    """A policy that will not decode stops the check, loudly."""
    missing = REPO / "slsa" / "no-such-policy.json"
    err = io.StringIO()
    with redirect_stderr(err):
        assert pp.main(["--policy", str(missing)]) == 1
    assert "does not read as JSON" in err.getvalue()


# Every field the check reads, wrongly shaped, and its refusal.
WRONG_SHAPES: list[tuple[object, str]] = [
    ([], "the policy is not an object"),
    ({"evidence": []}, "evidence is not an object"),
    ({"evidence": {"classes": 1}}, "evidence.classes is not an object"),
    (
        {"evidence": {"classes": {"x": []}}},
        "evidence.classes.x is not an object",
    ),
    (
        {"evidence": {"classes": {"x": {"assetPrefixes": {}}}}},
        "evidence.classes.x.assetPrefixes is not a list",
    ),
    (
        {"evidence": {"classes": {"x": {"assetPrefixes": ["p"]}}}},
        "evidence.classes.x.assetPrefixes[0] is not an object",
    ),
    (
        {"evidence": {"classes": {"x": {"assetPrefixes": [{"prefix": 1}]}}}},
        "evidence.classes.x.assetPrefixes[0].prefix is not a string",
    ),
    (
        {
            "evidence": {
                "classes": {"x": {"assetPrefixes": [{"prefix": "p", "planned": 1}]}}
            }
        },
        "evidence.classes.x.assetPrefixes[0].planned is not a boolean",
    ),
]


# A policy field of the wrong shape is refused by its path.
@pytest.mark.parametrize(("doc", "message"), WRONG_SHAPES)
def test_each_wrong_shape_names_its_field(doc: object, message: str) -> None:
    """Every field the check reads, wrongly shaped in turn."""
    with pytest.raises(decoded.UndecodableError) as cm:
        pp.read_policy(doc)
    assert str(cm.value) == message


def test_the_fixture_decodes() -> None:
    """Classes are every declared class; obligations only the planned."""
    assert (
        pp.Policy(
            frozenset({"rust-crate", "pgrx-extension", "source-archive"}),
            frozenset({
                ("rust-crate", "sbom-cargo-"),
                ("pgrx-extension", "sbom-pgrx-"),
            }),
        )
        == POLICY_UNDER_TEST
    )


def test_main_refuses_a_wrongly_shaped_policy() -> None:
    """The refusal reaches the exit, by file and field."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "policy.json"
        bad.write_text('{"evidence": {"classes": []}}', encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            assert pp.main(["--policy", str(bad)]) == 1
    assert err.getvalue() == f"{TASK}: {bad}: evidence.classes is not an object\n"


def test_main_refuses_a_duplicate_key_as_json() -> None:
    """The strict loader's refusal keeps the check's own words."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "policy.json"
        bad.write_text('{"evidence": {}, "evidence": {}}', encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            assert pp.main(["--policy", str(bad)]) == 1
    assert (
        err.getvalue()
        == f'{TASK}: {bad} does not read as JSON\n  duplicate key "evidence"\n'
    )

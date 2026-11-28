"""Table tests for the subject budget, guard branches first.

The defect class #364 named survives its closure: skip-clean guards are
the least exercised code in the org, and a guard that skips when it
should run looks exactly like success. Proving a branch once by hand is
not a table test and does not catch the next regression, so every guard
in `subject_budget.py` is driven here — both directions where there are
two, and the hard-error paths asserted as errors rather than fallbacks.
grounds that adopting one is a fourth thing to port; that reasoning
bites a new dependency, not the batteries already inside the pinned
interpreter. Nothing is added to the belt to run this.

Run through the gate as `mise run test`, which `ci` collects.
"""

import io
import json
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self

import pytest

from belt import decoded, typed_std
from belt import subject_budget as sb
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

# What Renovate appends to an advisory subject, and what default.json
# writes out explicitly since #667. Eleven columns.
# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:subject-budget"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


SUFFIX = "[SECURITY]"

# A minimal preset in the shape default.json now has: all five message
# fields explicit plus the advisory suffix, so nothing here depends on
# a Renovate default.
MESSAGE: dict[str, str] = {
    "commitMessageAction": "update",
    "commitMessageTopic": "{{depName}}",
    "commitMessageExtra": "to {{newValue}}",
    "semanticCommitType": "chore",
    "semanticCommitScope": "deps",
}
PRESET: dict[str, object] = {
    **MESSAGE,
    "packageRules": [],
    "vulnerabilityAlerts": {"commitMessageSuffix": SUFFIX},
}

# What a row expects when nothing overruns or nothing is read, typed so
# the comparison states the element type it is checking.
NO_FINDINGS: list[sb.Finding] = []
NO_DEPS: list[sb.Dep] = []
NO_LINES: list[str] = []


class Stops:
    """Expect the task to stop: exit 1, its finding said on stderr (#1934).

    The refusal is a finding, written through the writer; `said` is what
    it printed, without the final newline.
    """

    def __init__(self) -> None:
        """Start with nothing said."""
        self.said = ""
        self._err = io.StringIO()
        self._redirect = redirect_stderr(self._err)

    def __enter__(self) -> Self:
        """Start capturing stderr.

        Returns:
            This expectation, to read `said` from afterwards.

        """
        self._redirect.__enter__()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Stop capturing, and hold the block to a stop with status 1.

        Returns:
            True: the expected stop is swallowed.

        """
        self._redirect.__exit__(kind, exc, tb)
        self.said = self._err.getvalue().rstrip("\n")
        if not isinstance(exc, SystemExit):
            pytest.fail(f"the task did not stop: {exc!r}")
        assert exc.code == 1, self.said
        return True


def dep(name: str, current: str = "v1.0.0", manager: str = "mise") -> sb.Dep:
    """Build a dependency for a table row.

    Returns:
        A Dep with a stable origin, so tests compare on what they set.

    """
    return sb.Dep(name, current, manager, "fixture")


def preset(**overrides: object) -> dict[str, object]:
    """Copy the baseline preset with fields or rules replaced.

    Returns:
        A preset dict safe to mutate.

    """
    return {**PRESET, **overrides}


def owned(
    source: dict[str, object],
    repo_rules: Sequence[object] | None = None,
    suffix: str = "",
    repo_top: dict[str, object] | None = None,
) -> sb.Template:
    """Gather a Template the way main() does, from a test's three parts.

    The repo arrives as its whole document (#725), so a row states its
    two layers separately — the packageRules it declares and the message
    fields it writes beside `extends` — and this assembles them the way
    a renovate.json holds them.

    Returns:
        The three sources as one value.

    """
    repo: dict[str, object] = {**(repo_top or {}), "packageRules": repo_rules or []}
    return sb.Template(sb.decode_config(source), sb.decode_config(repo), suffix)


def rule_of(doc: object) -> sb.Rule:
    """Decode one packageRule the way a config's rules are decoded.

    Returns:
        The rule `selects()` reads.

    """
    return sb.decode_rule(doc, "packageRules[0]")


def template(**overrides: str) -> dict[str, str]:
    """Build a resolved template, ordinary shape unless told otherwise.

    `render()` composes six fields and reads every one of them, so a
    caller must state the suffix rather than let it default — the same
    law the five composed fields answer to (#576, #686).

    Returns:
        The six rendered fields, safe to mutate.

    """
    return {
        **{k: MESSAGE[k] for k in sb.MESSAGE_CONFIG},
        "commitMessageSuffix": "",
        **overrides,
    }


def width_of(name: str, current: str) -> int:
    """Render one dependency under the baseline preset and measure it.

    Returns:
        The subject's column count, growth allowance included.

    """
    subject, _ = sb.render(template(), name, len(current) + sb.VERSION_GROWTH)
    return len(subject)


# Base for rows whose rules leave exactly one resolution.
#
# `effective()` returns EVERY resolution Renovate could reach, because
# a rule carrying an unmodellable matcher forks the set (#724). A row
# that plants no such rule must therefore produce exactly one, so this
# says so rather than indexing past a second and calling it the
# answer — the assertion is part of what each row proves.
def sole(source: sb.Template, row: sb.Dep) -> dict[str, str]:
    """Resolve a dependency, asserting the resolution is unambiguous.

    Returns:
        The single reachable resolution.

    """
    configs = sb.effective(source, row)
    assert len(configs) == 1
    return configs[0]


# matchPackageNames, including the measured packageName behaviour.
def test_exact_name() -> None:
    """An exact depName selects that dependency and no other."""
    assert sb.matches("ruff", "ruff")
    assert not sb.matches("ruff", "rumdl")


def test_star_selects_everything() -> None:
    """The catch-all rule reaches every dependency."""
    assert sb.matches("*", "anything/at-all")


def test_glob_matches_backend_stripped_name() -> None:
    """`monumental-archive/**` selects a mise dep carrying a backend.

    Measured on #574: Renovate renders depName WITH the backend
    prefix but selects on the stripped packageName, which is how
    that pull request came out scoped `chore(canon)`.
    """
    assert sb.matches("monumental-archive/**", "github:monumental-archive/stele")
    assert sb.matches("monumental-archive/**", "monumental-archive/.github")
    assert not sb.matches("monumental-archive/**", "actions/checkout")


def test_single_star_does_not_cross_a_slash() -> None:
    """A single star is one path segment, as in Renovate's globs."""
    assert sb.matches("actions/*", "actions/checkout")
    assert not sb.matches("actions/*", "actions/some/nested")


def test_a_globstar_segment_matches_zero_directories() -> None:
    """`**/x` selects a root `x`, as minimatch does.

    It matters for matchFileNames, where `**/Cargo.toml` is the
    ordinary way to name every manifest in a workspace: reading it as
    "at least one directory deep" would drop the rule for the root
    manifest and model that dependency at the value the rule
    overrides — the fail-open #724 exists to close.
    """
    assert sb.glob_matches("**/Cargo.toml", "Cargo.toml")
    assert sb.glob_matches("**/Cargo.toml", "crates/one/Cargo.toml")
    assert not sb.glob_matches("**/Cargo.toml", "Cargo.lock")


# Rule matchers, including the AND across matcher kinds.
def test_manager_only() -> None:
    """A manager-scoped rule takes that manager and no other."""
    rule = {"matchManagers": ["gomod"]}
    assert sb.selects(rule_of(rule), dep("x", manager="gomod"))
    assert not sb.selects(rule_of(rule), dep("x", manager="mise"))


def test_names_only() -> None:
    """A name-scoped rule ignores which manager declared it."""
    rule = {"matchPackageNames": ["ruff"]}
    assert sb.selects(rule_of(rule), dep("ruff", manager="mise"))
    assert not sb.selects(rule_of(rule), dep("rumdl", manager="mise"))


def test_both_matchers_must_hold() -> None:
    """Renovate ANDs across matcher kinds; so does this."""
    rule = {"matchManagers": ["gomod"], "matchPackageNames": ["ruff"]}
    assert sb.selects(rule_of(rule), dep("ruff", manager="gomod"))
    assert not sb.selects(rule_of(rule), dep("ruff", manager="mise"))
    assert not sb.selects(rule_of(rule), dep("rumdl", manager="gomod"))


def test_rule_with_no_matcher_selects_nothing() -> None:
    """A rule setting something this task does not render is inert."""
    assert not sb.selects(rule_of({"automerge": True}), dep("ruff"))


def test_dep_names_does_not_fall_back_to_the_package_name() -> None:
    """The measured difference between the two name matchers (#574).

    matchPackageNames reaches a mise dependency through its
    backend-stripped packageName; matchDepNames sees only the name
    the subject renders. Modelling the second as the first would
    apply a rule Renovate does not.
    """
    stele = dep("github:monumental-archive/stele")
    rules = [
        {"matchPackageNames": ["monumental-archive/**"]},
        {"matchDepNames": ["monumental-archive/**"]},
        {"matchDepNames": ["github:monumental-archive/**"]},
    ]
    got: list[bool | None] = [sb.selects(rule_of(rule), stele) for rule in rules]
    want: list[bool | None] = [True, False, True]
    assert got == want


def test_file_names_selects_on_the_declaring_file() -> None:
    """Selection by path reads the origin every walk already records."""
    row = sb.Dep("postgres", "18", "custom.regex", "docker/images.toml")
    rules = [
        {"matchFileNames": ["docker/images.toml"]},
        {"matchFileNames": ["docker/**"]},
        {"matchFileNames": ["mise/config.toml"]},
    ]
    got: list[bool | None] = [sb.selects(rule_of(rule), row) for rule in rules]
    want: list[bool | None] = [True, True, False]
    assert got == want


def test_an_unmodellable_matcher_is_unresolved_not_dropped() -> None:
    """None is the third answer, and it is not False (#724).

    A rule carrying only `matchUpdateTypes` was dropped entirely
    before, which models a widening rule at the value it overrides.
    """
    assert sb.selects(rule_of({"matchUpdateTypes": ["major"]}), dep("ruff")) is None


def test_a_modelled_matcher_that_refuses_settles_the_whole_rule() -> None:
    """Renovate ANDs, so one refusal is a decision, not a maybe."""
    rule = {"matchManagers": ["gomod"], "matchUpdateTypes": ["major"]}
    assert sb.selects(rule_of(rule), dep("x", manager="mise")) == False
    assert sb.selects(rule_of(rule), dep("x", manager="gomod")) is None


def test_every_modelled_matcher_must_hold_together() -> None:
    """Four kinds now, and the AND runs across all of them."""
    rule = {
        "matchManagers": ["mise"],
        "matchDepNames": ["rumdl"],
        "matchFileNames": ["mise/config.toml"],
    }
    rows = [
        sb.Dep("rumdl", "0.2.53", "mise", "mise/config.toml"),
        sb.Dep("ruff", "0.2.53", "mise", "mise/config.toml"),
        sb.Dep("rumdl", "0.2.53", "mise", "mise.toml"),
        sb.Dep("rumdl", "0.2.53", "aqua", "mise/config.toml"),
    ]
    got: list[bool | None] = [sb.selects(rule_of(rule), row) for row in rows]
    want: list[bool | None] = [True, False, False, False]
    assert got == want


# Field resolution: read from the preset, later-wins, never defaulted.
def test_reads_all_five_from_the_top_level() -> None:
    """With no rules, every field comes from the preset as written."""
    config = sole(owned(preset()), dep("ruff"))
    want: dict[str, object] = {k: PRESET[k] for k in sb.MESSAGE_CONFIG}
    assert config == want


def test_later_rule_wins_per_field() -> None:
    """Two rules touching one dependency resolve in declared order."""
    rules = [
        {"matchPackageNames": ["*"], "commitMessageTopic": "first"},
        {"matchPackageNames": ["ruff"], "commitMessageTopic": "second"},
    ]
    config = sole(owned(preset(packageRules=rules)), dep("ruff"))
    assert config["commitMessageTopic"] == "second"


def test_a_rule_leaves_fields_it_does_not_set() -> None:
    """Overriding the topic must not disturb the semantic prefix."""
    rules = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "short"}]
    config = sole(owned(preset(packageRules=rules)), dep("ruff"))
    assert config["semanticCommitType"] == "chore"
    assert config["commitMessageExtra"] == "to {{newValue}}"


def test_manager_scoped_rule_reaches_only_that_manager() -> None:
    """The gomod rule keeps `fix` without touching anything else."""
    rules = [{"matchManagers": ["gomod"], "semanticCommitType": "fix"}]
    config = owned(preset(packageRules=rules))
    assert sole(config, dep("x", manager="gomod"))["semanticCommitType"] == "fix"
    assert sole(config, dep("x", manager="mise"))["semanticCommitType"] == "chore"


def test_absent_field_is_a_hard_error_not_a_fallback() -> None:
    """A field the preset omits stops the task and names itself.

    The review decision on #576: a simulation that fills in a value
    the org did not write is agreeing with Renovate from memory, and
    an upstream default changing underneath it is invisible.
    """
    incomplete = preset()
    del incomplete["commitMessageExtra"]
    with Stops() as caught:
        sb.effective(owned(incomplete), dep("ruff"))
    assert "commitMessageExtra" in caught.said


def test_every_missing_field_is_named_at_once() -> None:
    """The error lists all absent fields, not just the first."""
    with Stops() as caught:
        sb.effective(owned({"packageRules": []}), dep("ruff"))
    for field in sb.MESSAGE_CONFIG:
        assert field in caught.said


# The repo's own packageRules, folded in after the preset's (#677).
#
# Both directions per #650. The narrowing direction was already true
# by accident before this existed — `fix(deps):` is two columns under
# `chore(deps):` — so it is the WIDENING rows that carry the proof:
# the preset-only model went green on a subject Renovate cannot mint
# inside the ceiling, and that is the state #576 exists to forbid.
def test_no_repo_rules_is_the_preset_only_model() -> None:
    """The default argument leaves the resolution exactly as it was.

    Fail-SAFE by construction: a caller that supplies nothing gets
    the over-estimating model rather than a silently narrower one.
    """
    rules = [{"matchPackageNames": ["*"], "commitMessageTopic": "kept"}]
    config = preset(packageRules=rules)
    assert sb.effective(owned(config), dep("ruff")) == sb.effective(
        owned(config, []), dep("ruff")
    )


def test_a_repo_rule_narrows_a_preset_field() -> None:
    """#668's live case, followed down.

    The canon takes `fix` where the preset says `chore`, and the
    model must resolve to the narrower prefix rather than the
    preset's.
    """
    repo = [{"matchManagers": ["mise"], "semanticCommitType": "fix"}]
    config = sole(owned(preset(), repo), dep("ruff"))
    assert config["semanticCommitType"] == "fix"


def test_a_repo_rule_widens_a_preset_field() -> None:
    """THE direction that matters.

    A widening repo rule is modelled wide, not at the preset's
    narrower value — the row the preset-only model got wrong.
    """
    repo = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "a" * 40}]
    config = sole(owned(preset(), repo), dep("ruff"))
    assert config["commitMessageTopic"] == "a" * 40


def test_a_repo_rule_resolves_after_every_preset_rule() -> None:
    """Ordering is not a choice.

    Repo config resolves after an extended preset, so the repo's
    rule appends to the preset's list and wins the field they
    both set.
    """
    rules = [{"matchPackageNames": ["*"], "commitMessageTopic": "preset-last"}]
    repo = [{"matchPackageNames": ["*"], "commitMessageTopic": "repo"}]
    config = sole(owned(preset(packageRules=rules), repo), dep("ruff"))
    assert config["commitMessageTopic"] == "repo"


def test_later_wins_within_the_repo_list_too() -> None:
    """The canon's loop guard is last in its own list and must win."""
    repo = [
        {"matchManagers": ["mise"], "semanticCommitType": "fix"},
        {
            "matchPackageNames": ["monumental-archive/.github"],
            "semanticCommitType": "chore",
            "semanticCommitScope": "canon",
        },
    ]
    config = sole(owned(preset(), repo), dep("monumental-archive/.github"))
    assert config["semanticCommitType"] == "chore"
    assert config["semanticCommitScope"] == "canon"


def test_a_repo_rule_that_does_not_select_changes_nothing() -> None:
    """Selection is unchanged; only the list it walks grew."""
    repo = [{"matchPackageNames": ["rumdl"], "commitMessageTopic": "other"}]
    config = sole(owned(preset(), repo), dep("ruff"))
    assert config["commitMessageTopic"] == "{{depName}}"


def test_a_repo_rule_may_not_be_the_only_place_a_field_is_written() -> None:
    """The absent-field error sits BETWEEN the two lists.

    That placement is the rule: a repo may override a field the
    preset sets, never supply one it does not. Otherwise a consumer
    that does not extend the canon inherits nothing and the hard
    error stops meaning what it says.
    """
    thin = preset()
    del thin["commitMessageAction"]
    repo = [{"matchPackageNames": ["*"], "commitMessageAction": "bump"}]
    with Stops() as caught:
        sb.effective(owned(thin, repo), dep("ruff"))
    assert "commitMessageAction" in caught.said


def test_judge_goes_red_on_a_widening_repo_rule_and_green_without() -> None:
    """Plant and measure, both directions, through the real judge."""
    row = [dep("ruff", "v1.0.0")]
    limit = width_of("ruff", "v1.0.0")
    assert sb.judge(row, owned(PRESET), limit, []) == NO_FINDINGS
    widen = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "ruff-x"}]
    findings = sb.judge(row, owned(PRESET, widen), limit, [])
    assert len(findings) == 1
    assert findings[0].width == limit + 2
    narrow = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "rf"}]
    assert sb.judge(row, owned(PRESET, narrow), limit, []) == NO_FINDINGS


# The repo's own top-level fields, the middle layer (#725).
#
# Renovate resolves four layers, not two: the preset's top level, the
# repo's top level, the preset's packageRules, the repo's. #677 taught
# the resolver the fourth and left the second unread, so a
# `semanticCommitType` written beside `extends` was resolved at the
# preset's value.
def test_a_repo_top_level_field_beats_the_preset_top_level() -> None:
    """The layer that was skipped, in one row."""
    config = sole(
        owned(preset(), repo_top={"semanticCommitType": "refactor"}),
        dep("ruff"),
    )
    assert config["semanticCommitType"] == "refactor"


def test_it_leaves_the_fields_it_does_not_set() -> None:
    """An override is per field, as everywhere else in the model."""
    config = sole(
        owned(preset(), repo_top={"semanticCommitScope": "belt"}),
        dep("ruff"),
    )
    assert config["semanticCommitScope"] == "belt"
    assert config["semanticCommitType"] == "chore"


def test_a_preset_package_rule_beats_a_repo_top_level_field() -> None:
    """THE precedence row, and the live blast radius with it.

    A rule from either file beats a top-level value from either
    file. The preset overrides `semanticCommitType` per manager for
    gomod, cargo and npm only — so the preset's rule wins there,
    while for mise (and github-actions, and custom.regex) nothing
    stands between the repo's top-level value and the subject. That
    asymmetry is the reason this layer is worth reading: those three
    managers carry every dependency this repo declares.
    """
    rules = [{"matchManagers": ["gomod"], "semanticCommitType": "fix"}]
    source = owned(
        preset(packageRules=rules),
        repo_top={"semanticCommitType": "refactor"},
    )
    assert sole(source, dep("x", manager="gomod"))["semanticCommitType"] == "fix"
    assert sole(source, dep("x", manager="mise"))["semanticCommitType"] == "refactor"


def test_a_repo_package_rule_beats_the_repo_top_level() -> None:
    """Within one file too: a rule is the narrower statement."""
    config = sole(
        owned(
            preset(),
            [{"matchPackageNames": ["ruff"], "semanticCommitType": "fix"}],
            repo_top={"semanticCommitType": "refactor"},
        ),
        dep("ruff"),
    )
    assert config["semanticCommitType"] == "fix"


def test_it_reaches_every_forked_resolution() -> None:
    """The middle layer sits under #724's forks, not beside them."""
    repo = [{"matchUpdateTypes": ["major"], "commitMessageTopic": "wide"}]
    configs = sb.effective(
        owned(preset(), repo, repo_top={"semanticCommitType": "refactor"}),
        dep("ruff"),
    )
    assert len(configs) == 2
    types: set[str] = {c["semanticCommitType"] for c in configs}
    want: set[str] = {"refactor"}
    assert types == want


def test_a_repo_top_level_field_cannot_satisfy_the_absent_field_error() -> None:
    """A repo may override any of the five; it may write none of them.

    Same law the repo's packageRules answer to (#677), one layer up:
    the preset must remain the place every field is written, or a
    consumer that does not extend the canon inherits nothing and the
    hard error stops meaning what it says.
    """
    thin = preset()
    del thin["semanticCommitType"]
    with Stops() as caught:
        sb.effective(
            owned(thin, repo_top={"semanticCommitType": "chore"}),
            dep("ruff"),
        )
    assert "semanticCommitType" in caught.said


def test_judge_goes_red_on_a_widening_top_level_field_and_green_without() -> None:
    """Plant and measure, both directions, per #650.

    `refactor` is nine columns against `chore`'s five: four columns
    of fail-OPEN before this layer was read.
    """
    row = [dep("ruff", "v1.0.0")]
    limit = width_of("ruff", "v1.0.0")
    assert sb.judge(row, owned(PRESET), limit, []) == NO_FINDINGS
    widen = owned(PRESET, repo_top={"semanticCommitType": "refactor"})
    findings = sb.judge(row, widen, limit, [])
    assert len(findings) == 1
    assert findings[0].width == limit + len("refactor") - len("chore")
    narrow = owned(PRESET, repo_top={"semanticCommitType": "fix"})
    assert sb.judge(row, narrow, limit, []) == NO_FINDINGS


# The two matcher kinds #724 taught the resolver to read.
#
# Both were live in this repo's renovate.json when the defect was
# written, and both were guessed at rather than read: a rule carrying
# matchDepNames beside matchManagers was applied WHOLESALE to the
# manager, and a rule carrying only matchFileNames was dropped.
def test_a_dep_names_rule_reaches_the_dependency_it_names() -> None:
    """The narrowing plant, and it is the one that failed before.

    `{matchManagers: [mise], matchDepNames: [rumdl]}` is this repo's
    own shape. Read as the manager alone it narrows every mise
    dependency in the model; it names one.
    """
    repo = [
        {
            "matchManagers": ["mise"],
            "matchDepNames": ["rumdl"],
            "commitMessageTopic": "r",
        },
    ]
    source = owned(preset(), repo)
    assert sole(source, dep("rumdl"))["commitMessageTopic"] == "r"
    assert sole(source, dep("ruff"))["commitMessageTopic"] == "{{depName}}"
    assert (
        sole(source, dep("rumdl", manager="cargo"))["commitMessageTopic"]
        == "{{depName}}"
    )


def test_a_file_names_rule_is_read_rather_than_dropped() -> None:
    """The rule the model never saw, now resolved against the origin."""
    repo = [
        {
            "matchFileNames": ["docker/images.toml"],
            "semanticCommitType": "fix",
        },
    ]
    source = owned(preset(), repo)
    matched = sb.Dep("postgres", "18", "custom.regex", "docker/images.toml")
    other = sb.Dep("postgres", "18", "custom.regex", "lefthook.yml")
    assert sole(source, matched)["semanticCommitType"] == "fix"
    assert sole(source, other)["semanticCommitType"] == "chore"


def test_judge_goes_red_on_a_widening_file_names_rule() -> None:
    """Plant and measure, both directions, per #650."""
    row = [sb.Dep("postgres", "18.1", "custom.regex", "docker/images.toml")]
    limit = width_of("postgres", "18.1")
    assert sb.judge(row, owned(PRESET), limit, []) == NO_FINDINGS
    widen = [
        {
            "matchFileNames": ["docker/images.toml"],
            "commitMessageTopic": "postgres-x",
        },
    ]
    findings = sb.judge(row, owned(PRESET, widen), limit, [])
    assert len(findings) == 1
    assert findings[0].width == limit + 2
    elsewhere = [
        {
            "matchFileNames": ["docker/other.toml"],
            "commitMessageTopic": "postgres-x",
        },
    ]
    assert sb.judge(row, owned(PRESET, elsewhere), limit, []) == NO_FINDINGS


# A matcher no simulation can settle costs margin, never correctness.
#
# `matchUpdateTypes` and its kin do not exist until Renovate has looked
# a version up, which is after this task runs. So the rule is resolved
# BOTH ways and the widest subject is charged — the same construction
# as the two-subject advisory rendering (#686).
def test_a_forked_rule_yields_both_resolutions_in_order() -> None:
    """Two resolutions, the unapplied one first."""
    repo = [{"matchUpdateTypes": ["major"], "commitMessageTopic": "wide-topic"}]
    configs = sb.effective(owned(preset(), repo), dep("ruff"))
    topics: list[str] = [c["commitMessageTopic"] for c in configs]
    want: list[str] = ["{{depName}}", "wide-topic"]
    assert topics == want


def test_a_rule_setting_no_message_field_never_forks() -> None:
    """default.json's live case, and why the org forks nothing today.

    The one unmodellable matcher in force org-wide carries
    `automerge`, which composes no part of a subject: both branches
    would be the same resolution.
    """
    rules = [{"matchUpdateTypes": ["patch", "minor"], "automerge": True}]
    configs = sb.effective(owned(preset(packageRules=rules)), dep("ruff"))
    assert len(configs) == 1


def test_a_refused_modelled_matcher_forks_nothing() -> None:
    """A rule Renovate cannot apply is not a maybe."""
    repo = [
        {
            "matchManagers": ["gomod"],
            "matchUpdateTypes": ["major"],
            "commitMessageTopic": "wide",
        },
    ]
    configs = sb.effective(owned(preset(), repo), dep("ruff", manager="mise"))
    topics: list[str] = [c["commitMessageTopic"] for c in configs]
    want: list[str] = ["{{depName}}"]
    assert topics == want


def test_forks_onto_the_same_value_reconverge() -> None:
    """A fork that lands where another did is one resolution."""
    repo = [
        {"matchUpdateTypes": ["major"], "commitMessageTopic": "same"},
        {"matchUpdateTypes": ["minor"], "commitMessageTopic": "same"},
    ]
    configs = sb.effective(owned(preset(), repo), dep("ruff"))
    topics: list[str] = [c["commitMessageTopic"] for c in configs]
    want: list[str] = ["{{depName}}", "same"]
    assert topics == want


def test_the_widening_resolution_is_the_one_charged() -> None:
    """A rule that MIGHT widen is measured as though it does."""
    row = [dep("ruff", "v1.0.0")]
    limit = width_of("ruff", "v1.0.0")
    widen = [{"matchUpdateTypes": ["major"], "commitMessageTopic": "ruff-x"}]
    findings = sb.judge(row, owned(PRESET, widen), limit, [])
    assert len(findings) == 1
    assert findings[0].width == limit + 2


def test_a_narrowing_fork_buys_no_margin() -> None:
    """The other direction, which is the same rule read the other way.

    A rule that might NOT apply cannot spend the columns it might not
    save: the resolution charged is the unnarrowed one, so the
    dependency stays exactly as wide as it was.
    """
    row = [dep("ruff", "v1.0.0")]
    limit = width_of("ruff", "v1.0.0")
    narrow = [{"matchUpdateTypes": ["major"], "commitMessageTopic": "rf"}]
    assert sb.judge(row, owned(PRESET, narrow), limit, []) == NO_FINDINGS
    findings = sb.judge(row, owned(PRESET, narrow), limit - 1, [])
    assert len(findings) == 1
    assert findings[0].width == limit


def test_a_forked_rule_may_not_be_the_only_place_a_field_is_written() -> None:
    """The absent-field law holds across every resolution.

    A field only the applied branch carries is absent on the other,
    so the preset has not written it in the sense the hard error
    means (#576).
    """
    thin = preset(
        packageRules=[
            {"matchUpdateTypes": ["major"], "semanticCommitScope": "deps"},
        ],
    )
    del thin["semanticCommitScope"]
    with Stops() as caught:
        sb.effective(owned(thin), dep("ruff"))
    assert "semanticCommitScope" in caught.said


# Subject composition, substitution, and the unmodelled report.
def test_composes_the_whole_subject() -> None:
    """Prefix, action, topic, extra and the pinned suffix, in order."""
    subject, unmodelled = sb.render(template(), "ruff", 6)
    assert subject == "chore(deps): update ruff to 999999 (#999999)"
    assert unmodelled is None


def test_substitutes_triple_and_double_braces() -> None:
    """Renovate writes both spellings; both must resolve."""
    config = template(
        commitMessageTopic="{{{depName}}}",
        commitMessageExtra="to {{{newValue}}}",
    )
    subject, unmodelled = sb.render(config, "ruff", 3)
    assert subject == "chore(deps): update ruff to 999 (#999999)"
    assert unmodelled is None


def test_an_empty_field_is_skipped_not_double_spaced() -> None:
    """A preset may empty a field; the subject must not gain a gap."""
    config = template(commitMessageExtra="")
    subject, _ = sb.render(config, "ruff", 6)
    assert subject == "chore(deps): update ruff (#999999)"


def test_unresolved_placeholder_is_reported() -> None:
    """A template this task cannot model is named, never measured raw."""
    config = template(commitMessageTopic="{{depName}} {{newMajor}}")
    _, unmodelled = sb.render(config, "ruff", 6)
    assert unmodelled is not None
    assert "newMajor" in (unmodelled or "")


def test_suffix_uses_the_pinned_worst_case() -> None:
    """The rendered suffix is as wide as the pinned digit count."""
    subject, _ = sb.render(template(), "x", 1)
    assert subject.endswith(f" (#{'9' * sb.PR_NUMBER_DIGITS})")


# Budget arithmetic, exercised at the boundary in both directions.
def test_exactly_at_the_ceiling_passes() -> None:
    """A subject of exactly `limit` columns is not over budget."""
    name = "n" * 20
    limit = width_of(name, "v1.0.0")
    assert sb.judge([dep(name, "v1.0.0")], owned(PRESET), limit, []) == NO_FINDINGS


def test_one_column_over_fails() -> None:
    """One column past the ceiling is a finding, reported with it."""
    name = "n" * 20
    limit = width_of(name, "v1.0.0") - 1
    findings = sb.judge([dep(name, "v1.0.0")], owned(PRESET), limit, [])
    assert len(findings) == 1
    assert findings[0].width == limit + 1
    assert findings[0].dep.name == name


def test_version_growth_is_added_to_the_declared_width() -> None:
    """The budget measures the NEXT version, not the one in the tree."""
    bare = len(sb.render(template(), "x", len("v1.0.0"))[0])
    assert width_of("x", "v1.0.0") == bare + sb.VERSION_GROWTH


def test_a_go_pseudo_version_gets_no_growth() -> None:
    """Pseudo-versions are fixed-width, so the allowance is zero."""
    pseudo = "v0.0.0-20241213102144-19d51d7fe467"
    assert re.search(sb.PSEUDO_VERSION, pseudo)
    limit = len(sb.render(template(), "x", len(pseudo))[0])
    assert sb.judge([dep("x", pseudo)], owned(PRESET), limit, []) == NO_FINDINGS


def test_a_name_is_judged_once() -> None:
    """Two managers declaring one name produce one finding, not two."""
    rows = [dep("n" * 60, "v1.0.0"), dep("n" * 60, "v1.0.0", manager="npm")]
    assert len(sb.judge(rows, owned(PRESET), 40, [])) == 1


def test_unresolved_template_reaches_the_report() -> None:
    """judge() records what render() could not model."""
    config = preset(commitMessageTopic="{{depName}} {{newMajor}}")
    report: list[str] = []
    sb.judge([dep("ruff")], owned(config), 200, report)
    assert any("newMajor" in line for line in report)


# The delivered extra since #2145: a lock-file update names the version it
# installs, every other update the declared value.
LOCK_EXTRA = "to {{#if isLockfileUpdate}}{{newVersion}}{{else}}{{newValue}}{{/if}}"
LOCK_PRESET: dict[str, object] = {**PRESET, "commitMessageExtra": LOCK_EXTRA}

# One lock file of each format the belt reads, each resolving one
# package to a version wider than the range its manifest declares.
PNPM_LOCK = (
    "lockfileVersion: '9.0'\n\npackages:\n\n  vitest@4.1.12:\n"
    "    resolution: {integrity: sha512-x}\n\nsnapshots:\n\n"
    "  vitest@4.1.12(@types/node@22.0.0):\n    dependencies: {}\n"
)
NPM_LOCK = (
    '{"lockfileVersion": 3, "packages": {"": {"devDependencies":'
    ' {"vitest": "^4"}}, "node_modules/vitest": {"version": "4.1.12",'
    ' "dev": true}}}'
)
YARN_LOCK = (
    '# yarn lockfile v1\n\n\nvitest@^4, vitest@^4.1.0:\n  version "4.1.12"\n'
    '  resolved "https://registry.yarnpkg.com/vitest/-/vitest-4.1.12.tgz"\n'
)
BERRY_LOCK = (
    '__metadata:\n  version: 8\n\n"vitest@npm:^4":\n  version: 4.1.12\n'
    '  resolution: "vitest@npm:4.1.12"\n'
)
BUN_LOCK = (
    '{\n  "lockfileVersion": 1,\n  "packages": {\n'
    '    "vitest": ["vitest@4.1.12", "", {}, "sha512-x"],\n  }\n}\n'
)
CARGO_LOCK = 'version = 4\n\n[[package]]\nname = "serde"\nversion = "1.0.154"\n'
LOCKS: dict[str, tuple[str, str, str]] = {
    "pnpm-lock.yaml": ("vitest", PNPM_LOCK, "4.1.12"),
    "package-lock.json": ("vitest", NPM_LOCK, "4.1.12"),
    "yarn.lock": ("vitest", YARN_LOCK, "4.1.12"),
    "berry/yarn.lock": ("vitest", BERRY_LOCK, "4.1.12"),
    "bun.lock": ("vitest", BUN_LOCK, "4.1.12"),
    "Cargo.lock": ("serde", CARGO_LOCK, "1.0.154"),
}
# The two manifests the end-to-end rows declare a range in.
HONO_MANIFEST = '{"dependencies": {"hono": "^4.1.0"}}'
VITEST_MANIFEST = '{"devDependencies": {"vitest": "^4"}}'
LOCK_PRESET_JSON = json.dumps(LOCK_PRESET)


def locked_dep(lock: str, name: str, tmp: str) -> sb.Dep:
    """Declare one package as a range beside a lock file in `tmp`.

    Returns:
        The dependency, its manifest in the lock file's directory.

    """
    manager = "cargo" if lock.endswith("Cargo.lock") else "npm"
    manifest = "Cargo.toml" if manager == "cargo" else "package.json"
    current = "1" if manager == "cargo" else "^4"
    origin = Path(tmp) / Path(lock).parent / manifest
    return sb.Dep(name, current, manager, str(origin))


# A lock-file update names the version it installs (#2145).
def test_both_branches_render_and_the_wider_one_binds() -> None:
    """`newVersion` on the lock-file branch, `newValue` on the other."""
    config = template(commitMessageExtra=LOCK_EXTRA)
    lock, unmodelled = sb.render(config, "tokio", 4, version=9, lockfile=True)
    other, _ = sb.render(config, "tokio", 4, version=9)
    assert lock == "chore(deps): update tokio to 999999999 (#999999)"
    assert other == "chore(deps): update tokio to 9999 (#999999)"
    assert unmodelled is None
    with TemporaryDirectory() as tmp:
        row = [sb.Dep("tokio", "1", "cargo", f"{tmp}/Cargo.toml")]
        Path(tmp, "Cargo.lock").write_text(
            '[[package]]\nname = "tokio"\nversion = "1.48.0"\n', encoding="utf-8"
        )
        locks = sb.Locks([Path(tmp, "Cargo.lock")])
        # `1.48.0` plus the growth allowance is the binding width.
        wide, _ = sb.render(
            config, "tokio", 4, version=6 + sb.VERSION_GROWTH, lockfile=True
        )
        limit = len(wide)
        fits = sb.judge(row, owned(LOCK_PRESET), limit, [], locks)
        over = sb.judge(row, owned(LOCK_PRESET), limit - 1, [], locks)
    assert fits == NO_FINDINGS
    subjects: list[str] = [f.subject for f in over]
    want: list[str] = [wide]
    assert subjects == want
    assert locks.missing == NO_LINES


def test_a_locked_version_wider_than_its_range_is_charged() -> None:
    """`"1"` locks `1.0.154`: the subject is charged ten columns, not four."""
    with TemporaryDirectory() as tmp:
        dep_row = locked_dep("Cargo.lock", "serde", tmp)
        Path(tmp, "Cargo.lock").write_text(CARGO_LOCK, encoding="utf-8")
        locks = sb.Locks([Path(tmp, "Cargo.lock")])
        (finding,) = sb.judge([dep_row], owned(LOCK_PRESET), 0, [], locks)
    version = "9" * (len("1.0.154") + sb.VERSION_GROWTH)
    assert finding.subject == f"chore(deps): update serde to {version} (#999999)"
    assert finding.width == len(finding.subject)


@pytest.mark.parametrize(("lock", "entry"), LOCKS.items())
def test_each_lock_format_reads_the_locked_version(
    lock: str, entry: tuple[str, str, str], tmp_path: Path
) -> None:
    """pnpm, npm, yarn classic and berry, bun and Cargo, one reader."""
    name, body, version = entry
    path = tmp_path / lock
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    locks = sb.Locks([path])
    assert locks.widest(locked_dep(lock, name, str(tmp_path))) == version
    assert locks.missing == NO_LINES


def test_a_workspace_member_reads_the_root_lock_file() -> None:
    """The nearest tracked lock file from the manifest up answers."""
    with TemporaryDirectory() as tmp:
        Path(tmp, "Cargo.lock").write_text(CARGO_LOCK, encoding="utf-8")
        member = sb.Dep("serde", "1", "cargo", f"{tmp}/crates/a/Cargo.toml")
        locks = sb.Locks([Path(tmp, "Cargo.lock")])
        assert locks.widest(member) == "1.0.154"


def test_an_unreadable_lock_file_is_a_finding() -> None:
    """A lock file that cannot be read or decoded answers no version."""
    with TemporaryDirectory() as tmp:
        gone = sb.Dep("serde", "1", "cargo", f"{tmp}/Cargo.toml")
        broken = sb.Dep("hono", "^4", "npm", f"{tmp}/web/package.json")
        Path(tmp, "web").mkdir()
        Path(tmp, "web", "pnpm-lock.yaml").write_bytes(b"\xff\xfe")
        locks = sb.Locks([Path(tmp, "Cargo.lock"), Path(tmp, "web/pnpm-lock.yaml")])
        assert locks.widest(gone) is None
        assert locks.widest(broken) is None
        Path(tmp, "Cargo.lock").write_text("[[package]\n", encoding="utf-8")
        assert sb.Locks([Path(tmp, "Cargo.lock")]).widest(gone) is None
    said: list[str] = [line.split(", and ")[1] for line in locks.missing]
    want: list[str] = [
        f"{tmp}/Cargo.lock is unreadable",
        f"{tmp}/web/pnpm-lock.yaml is unreadable",
    ]
    assert said == want
    assert len(locks.report) == 2


def test_a_missing_lock_file_for_a_range_is_a_finding() -> None:
    """No tracked lock file: red by name, never the declared width."""
    status, _, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": LOCK_PRESET_JSON,
            "package.json": HONO_MANIFEST,
        },
    )
    assert status == 1
    assert (
        "1 range-declared dependency has no locked version for the"
        " lock-file branch's newVersion"
    ) in err
    assert (
        "hono (npm, package.json) declares '^4.1.0', and no tracked"
        " pnpm-lock.yaml or package-lock.json or yarn.lock or bun.lock"
        " resolves it"
    ) in err


def test_a_lock_file_without_the_entry_is_a_finding() -> None:
    """A tracked lock file that resolves no version is no fallback either."""
    status, _, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": LOCK_PRESET_JSON,
            "Cargo.toml": '[dependencies]\nserde = "1"\ntokio = "1"\n',
            "Cargo.lock": CARGO_LOCK,
        },
    )
    assert status == 1
    assert (
        "tokio (cargo, Cargo.toml) declares '1', and Cargo.lock resolves no"
        " version of it"
    ) in err
    assert "serde (cargo" not in err


def test_a_locked_tree_passes_end_to_end() -> None:
    """Every range resolved and every subject fitting is green."""
    status, out, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": LOCK_PRESET_JSON,
            "package.json": VITEST_MANIFEST,
            "pnpm-lock.yaml": PNPM_LOCK,
        },
    )
    assert status == 0, err
    assert "every subject the owned template can mint fits" in out


def test_an_exact_pin_or_a_template_without_it_reads_no_lock() -> None:
    """Only a range whose subject names `newVersion` needs a lock file."""
    cases = (
        (dep("hono", "4.1.0", "npm"), LOCK_PRESET),
        (dep("hono", "workspace:^", "npm"), LOCK_PRESET),
        (dep("serde", "=1.0.154", "cargo"), LOCK_PRESET),
        (dep("ruff", "0.16.2", "mise"), LOCK_PRESET),
        (dep("hono", "^4.1.0", "npm"), PRESET),
    )
    for row, source in cases:
        locks = sb.Locks([])
        found = sb.judge([row], owned(source), 200, [], locks)
        assert found == NO_FINDINGS
        assert locks.missing == NO_LINES


@pytest.mark.parametrize(
    "extra",
    [
        "to {{#if isMajor}}{{newMajor}}{{else}}{{newValue}}{{/if}}",
        "to {{#unless isLockfileUpdate}}{{newValue}}{{/unless}}",
        "to {{#if isLockfileUpdate}}{{#if x}}y{{/if}}{{else}}{{newValue}}{{/if}}",
    ],
)
def test_an_unrecognised_helper_is_still_unmodelled(extra: object) -> None:
    """Only the one conditional is rendered; any other block is reported."""
    config = template(commitMessageExtra=extra)
    for lockfile in (False, True):
        _, unmodelled = sb.render(config, "x", 3, lockfile=lockfile)
        assert unmodelled == f"commitMessageExtra: {extra}"


# The sixth field, and the allowance the advisory subject spends.
#
# Renovate composes a commit subject from six fields, not five: it
# appends `commitMessageSuffix`, and the one in force org-wide is
# `[SECURITY]` from the `vulnerabilityAlerts` block (#686, #667). Every
# dependency is therefore rendered twice and both subjects are held to
# the one ceiling.
def test_the_suffix_is_read_from_the_vulnerability_block() -> None:
    """It arrives from a nested block, not from a packageRule."""
    assert sb.advisory_suffix(sb.decode_config(PRESET), sb.decode_config({})) == SUFFIX


def test_a_repo_block_wins_the_field_it_sets() -> None:
    """The repo's own value is the one minted.

    Renovate merges that block child over parent, so a repo that
    writes its own suffix is not modelled at the preset's.
    """
    repo: dict[str, object] = {
        "vulnerabilityAlerts": {"commitMessageSuffix": "[CVE]"},
    }
    assert (
        sb.advisory_suffix(sb.decode_config(PRESET), sb.decode_config(repo)) == "[CVE]"
    )


def test_a_repo_block_without_the_field_falls_to_the_preset() -> None:
    """A partly declared block does not erase the preset's value."""
    repo: dict[str, object] = {"vulnerabilityAlerts": {"enabled": True}}
    assert (
        sb.advisory_suffix(sb.decode_config(PRESET), sb.decode_config(repo)) == SUFFIX
    )


def test_an_absent_suffix_is_a_hard_error_not_an_empty_string() -> None:
    """Renovate appends one whether or not the org writes it.

    Reading no suffix would not model "no suffix"; it would model an
    upstream default from memory, eleven columns wide, which is the
    failure #576 exists to kill.
    """
    with Stops() as caught:
        sb.advisory_suffix(sb.decode_config({}), sb.decode_config({}))
    assert "commitMessageSuffix" in caught.said


def test_the_suffix_renders_last_before_the_pull_request_tail() -> None:
    """Renovate's own composition order, and the whole subject."""
    subject, _ = sb.render(template(commitMessageSuffix=SUFFIX), "ruff", 6)
    assert subject == "chore(deps): update ruff to 999999 [SECURITY] (#999999)"


def test_an_empty_suffix_leaves_the_subject_untouched() -> None:
    """The ordinary subject gains no gap where the field is empty."""
    subject, _ = sb.render(template(), "ruff", 6)
    assert subject == "chore(deps): update ruff to 999999 (#999999)"


def test_the_ordinary_subject_is_charged_its_whole_width() -> None:
    """Including the pinned pull request tail, as #576 decided."""
    row = [dep("ruff", "v1.0.0")]
    width = len("v1.0.0") + sb.VERSION_GROWTH
    ordinary, _ = sb.render(template(), "ruff", width)
    findings = sb.judge(row, owned(PRESET), len(ordinary) - 1, [])
    assert findings[0].width == len(ordinary)


def test_the_advisory_subject_is_not_charged_that_tail() -> None:
    """It spends the allowance on the suffix rather than beside it.

    The measured consequence is in the next test; this one pins the
    arithmetic: the charge is the subject less the pinned tail, and
    the boundary is exact in both directions.
    """
    row = [dep("ruff", "v1.0.0")]
    width = len("v1.0.0") + sb.VERSION_GROWTH
    advisory, _ = sb.render(template(commitMessageSuffix=SUFFIX), "ruff", width)
    charged = len(advisory) - len(sb.PR_TAIL)
    findings = sb.judge(row, owned(PRESET, suffix=SUFFIX), charged - 1, [])
    assert findings[0].width == charged
    assert findings[0].subject == advisory
    assert sb.judge(row, owned(PRESET, suffix=SUFFIX), charged, []) == NO_FINDINGS


def test_the_advisory_charge_is_one_column_over_the_ordinary() -> None:
    """The whole tightening this change is, in one number.

    Suffix plus its space is eleven columns against the tail's ten,
    so the advisory rendering binds everywhere and the ordinary
    check rides free underneath it: 61 columns of content rather
    than 62.
    """
    width = len("v1.0.0") + sb.VERSION_GROWTH
    ordinary, _ = sb.render(template(), "ruff", width)
    advisory, _ = sb.render(template(commitMessageSuffix=SUFFIX), "ruff", width)
    assert len(advisory) - len(sb.PR_TAIL) - len(ordinary) == len(SUFFIX) + 1 - len(
        sb.PR_TAIL
    )


def test_a_short_suffix_leaves_the_ordinary_subject_binding() -> None:
    """Both subjects are measured, and either can be the wider one.

    `[SECURITY]` plus its space is eleven columns against the pinned
    tail's ten, so it binds — but that is a property of the org's
    suffix, not of the model. A suffix under ten columns puts the
    ordinary subject back in front, and the budget must report that
    one rather than assume the advisory is always widest.
    """
    row = [dep("ruff", "v1.0.0")]
    width = len("v1.0.0") + sb.VERSION_GROWTH
    ordinary, _ = sb.render(template(), "ruff", width)
    findings = sb.judge(row, owned(PRESET, suffix="[X]"), len(ordinary) - 1, [])
    assert findings[0].width == len(ordinary)
    assert findings[0].subject == ordinary


def test_a_pseudo_versioned_module_fits_and_could_not_otherwise() -> None:
    """THE row that pins the allowance decision (#686).

    A Go pseudo-version is fixed-width at 34 columns and takes no
    growth allowance, so charging the tail as well as the suffix puts
    it past the ceiling at ANY dependency name — measured against a
    real stele checkout, a floor of 78 columns with a ONE-character
    topic, against `jcs` which is already the shortest name in the
    org. A delivered gate a repo cannot pass is not enforcement, so
    this must stay green.
    """
    pseudo = "v0.0.0-20241213102144-19d51d7fe467"
    assert len(pseudo) == 34
    name = "github.com/cyberphone/json-canonicalization"
    config = preset(
        packageRules=[
            {"matchManagers": ["gomod"], "semanticCommitType": "fix"},
            {"matchPackageNames": [name], "commitMessageTopic": "jcs"},
        ],
    )
    row = [dep(name, pseudo, manager="gomod")]
    assert sb.judge(row, owned(config, suffix=SUFFIX), 72, []) == NO_FINDINGS
    charged = sb.judge(row, owned(config, suffix=SUFFIX), 0, [])[0]
    assert charged.width + len(sb.PR_TAIL) > 72


def test_a_dependency_that_fits_ordinarily_and_not_with_the_suffix() -> None:
    """Plant and measure, both directions, per #650."""
    name = "n" * 30
    row = [dep(name, "1.0.0")]
    ordinary, _ = sb.render(template(), name, len("1.0.0") + sb.VERSION_GROWTH)
    assert len(ordinary) <= 72
    assert sb.judge(row, owned(PRESET), 72, []) == NO_FINDINGS
    findings = sb.judge(row, owned(PRESET, suffix=SUFFIX), 72, [])
    assert len(findings) == 1
    assert SUFFIX in findings[0].subject
    shorter = preset(
        packageRules=[
            {"matchPackageNames": [name], "commitMessageTopic": "n" * 29},
        ],
    )
    assert sb.judge(row, owned(shorter, suffix=SUFFIX), 72, []) == NO_FINDINGS


# A Renovate config of the wrong shape is refused where it is read.
#
# Each value the template is resolved from is decoded once, so a
# wrong one is named by where it sits rather than failing later as a
# traceback (#1677).
ROWS: tuple[tuple[dict[str, object], str], ...] = (
    ({"packageRules": {}}, "packageRules is not a list"),
    ({"packageRules": ["x"]}, "packageRules[0] is not an object"),
    (
        {"packageRules": [{"matchManagers": "gomod"}]},
        "packageRules[0].matchManagers is not a list",
    ),
    (
        {"packageRules": [{"matchFileNames": [1]}]},
        "packageRules[0].matchFileNames[0] is not a string",
    ),
    (
        {"packageRules": [{"matchManagers": [], "commitMessageTopic": 1}]},
        "packageRules[0].commitMessageTopic is not a string",
    ),
    ({"semanticCommitType": None}, "semanticCommitType is not a string"),
    ({"vulnerabilityAlerts": []}, "vulnerabilityAlerts is not an object"),
    (
        {"vulnerabilityAlerts": {"commitMessageSuffix": False}},
        "vulnerabilityAlerts.commitMessageSuffix is not a string",
    ),
    ({"customManagers": "x"}, "customManagers is not a list"),
    ({"customManagers": [[]]}, "customManagers[0] is not an object"),
    (
        {"customManagers": [{"managerFilePatterns": "x"}]},
        "customManagers[0].managerFilePatterns is not a list",
    ),
    (
        {"customManagers": [{"matchStrings": [{}]}]},
        "customManagers[0].matchStrings[0] is not a string",
    ),
    (
        {"customManagers": [{"depNameTemplate": 7}]},
        "customManagers[0].depNameTemplate is not a string",
    ),
)


@pytest.mark.parametrize(("doc", "message"), ROWS)
def test_each_wrong_shape_is_refused_by_where_it_sits(
    doc: dict[str, object], message: str
) -> None:
    """One row per value decoded, each refused by its own message."""
    with pytest.raises(decoded.UndecodableError) as caught:
        sb.decode_config(doc)
    assert str(caught.value) == message


def test_a_wrong_shaped_file_stops_the_task_by_name() -> None:
    """The refusal carries the task's label and the file's path."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "renovate.json"
        path.write_text('{"packageRules": {}}', encoding="utf-8")
        with Stops() as caught:
            sb.read_config(path, [])
    assert caught.said == f"lint:subject-budget: {path}: packageRules is not a list"


def test_an_unreadable_file_decodes_as_an_empty_config() -> None:
    """It is recorded as unmodelled, the way every tracked file is."""
    report: list[str] = []
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "renovate.json"
        path.write_text("[]", encoding="utf-8")
        config = sb.read_config(path, report)
    assert config == sb.Renovate({}, (), None, ())
    want: list[str] = [f"{path}: not a JSON object"]
    assert report == want


# The ceiling comes from the delivered config or the task stops.
def test_reads_subject_length_from_the_delivered_config() -> None:
    """The one definition of 72 is the delivered committed.toml."""
    with TemporaryDirectory() as tmp:
        (Path(tmp) / "committed.toml").write_text(
            "subject_length = 61\n",
            encoding="utf-8",
        )
        env: dict[str, str] = {"ORG_BELT_DIR": tmp}
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            assert sb.ceiling() == 61


def test_unset_belt_dir_is_a_hard_error() -> None:
    """No belt means no ceiling; the task must not invent one."""
    env: dict[str, str] = {"ORG_BELT_DIR": ""}
    with (
        pytest.MonkeyPatch.context() as patched,
        pytest.raises(
            SystemExit,
        ),
    ):
        for name, value in env.items():
            patched.setenv(name, value)
        sb.ceiling()


def test_absent_committed_toml_is_a_hard_error() -> None:
    """A belt without the delivered config fails rather than defaults."""
    with TemporaryDirectory() as tmp:
        env: dict[str, str] = {"ORG_BELT_DIR": tmp}
        with pytest.MonkeyPatch.context() as patched, pytest.raises(SystemExit):
            for name, value in env.items():
                patched.setenv(name, value)
            sb.ceiling()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("subject_length = \n", "Invalid value (at line 1, column 18)"),
        ("x = 1\n", "subject_length is not an integer"),
        ('subject_length = "72"\n', "subject_length is not an integer"),
    ],
)
def test_a_ceiling_of_the_wrong_shape_is_refused_by_name(
    text: str, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unparsable, absent or not an integer: the task stops, named."""
    config = tmp_path / "committed.toml"
    config.write_text(text, encoding="utf-8")
    monkeypatch.setenv("ORG_BELT_DIR", str(tmp_path))
    with Stops() as caught:
        sb.ceiling()
    assert caught.said == f"lint:subject-budget: {config}: {message}"


def test_the_canon_ceiling_is_what_the_org_ships() -> None:
    """The delivered file parses and carries a subject_length."""
    shipped = support.MISE / "committed.toml"
    parsed = decoded.load_toml(shipped.read_text(encoding="utf-8"))
    assert isinstance(parsed["subject_length"], int)


# The per-manager walks, on the cases that were once wrong.
def test_mise_takes_the_widest_of_an_array_pin() -> None:
    """A repo that fuzzes pins two toolchains; the wider one governs."""
    assert sb.widest_version("1.2.3") == "1.2.3"
    assert sb.widest_version({"version": "1.2.3"}) == "1.2.3"
    assert (
        sb.widest_version([{"version": "1.97.1"}, "nightly-2026-07-20"])
        == "nightly-2026-07-20"
    )


def test_gomod_skips_indirect_requires() -> None:
    """Renovate raises no pull request of its own for an indirect."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "go.mod"
        path.write_text(
            "require (\n"
            "\tgithub.com/direct/one v1.2.3\n"
            "\tgithub.com/indirect/two v4.5.6 // indirect\n"
            ")\n",
            encoding="utf-8",
        )
        found = sb.from_gomod([path], [])
    names: list[str] = [d.name for d in found]
    want: list[str] = ["github.com/direct/one"]
    assert names == want
    assert found[0].manager == "gomod"


def test_a_sha_pinned_action_is_measured_at_the_short_digest() -> None:
    """Renovate renders newDigestShort, never the 40-character pin."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "action.yml"
        path.write_text(f"    uses: owner/repo@{'a' * 40}\n", encoding="utf-8")
        found = sb.from_actions([path], [])
    assert len(found[0].current) == sb.DIGEST_SHORT


def test_a_version_comment_beats_the_ref() -> None:
    """A `# vX.Y.Z` comment is what reaches the subject."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "action.yml"
        path.write_text(
            f"    uses: owner/repo@{'a' * 40} # v4.37.7\n",
            encoding="utf-8",
        )
        found = sb.from_actions([path], [])
    assert found[0].current == "v4.37.7"


def test_local_and_self_references_name_no_dependency() -> None:
    """`./`, `$/` and container steps have no upstream to bump."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "action.yml"
        path.write_text(
            "    uses: ./.github/actions/thing\n"
            "    uses: $/.github/actions/canon\n"
            "    uses: docker://alpine:3\n",
            encoding="utf-8",
        )
        assert sb.from_actions([path], []) == NO_DEPS


def test_cargo_skips_path_and_workspace_entries() -> None:
    """Neither resolves against a registry, so neither is bumped."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "Cargo.toml"
        path.write_text(
            "[dependencies]\n"
            'serde = "1.0.0"\n'
            'local = { path = "../local" }\n'
            "shared = { workspace = true }\n",
            encoding="utf-8",
        )
        found = sb.from_cargo([path], [])
    names: list[str] = [d.name for d in found]
    want: list[str] = ["serde"]
    assert names == want


def test_a_custom_manager_runs_its_javascript_regex() -> None:
    """`(?<name>)` must be translated before Python will compile it."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "lefthook.yml"
        path.write_text("    ref: v1.46.0\n", encoding="utf-8")
        manager = {
            "managerFilePatterns": ["/(^|/)lefthook\\.yml$/"],
            "matchStrings": ["ref: (?<currentValue>v[0-9][\\w.+-]*)"],
            "depNameTemplate": "monumental-archive/.github",
        }
        found = sb.from_custom_managers(
            [("default.json", sb.decode_config({"customManagers": [manager]}))],
            [path],
            [],
        )
    assert len(found) == 1
    assert found[0].name == "monumental-archive/.github"
    assert found[0].current == "v1.46.0"
    assert found[0].manager == "custom.regex"


def test_a_custom_manager_names_the_file_it_matched() -> None:
    """The origin is the FILE, not the config declaring the manager.

    Without it `matchFileNames` cannot be resolved for a
    regex-managed dependency (#724), and `custom.regex` is the
    manager id of every regex manager alike — so the file is the only
    thing that tells two of them apart. The declaring config still
    names the manager in the unresolved report.
    """
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "images.toml"
        path.write_text('"18" = "postgres:18.1"\n', encoding="utf-8")
        manager = {
            "managerFilePatterns": ["/(^|/)images\\.toml$/"],
            "matchStrings": ['"[0-9]+" = "postgres:(?<currentValue>[0-9.]+)"'],
            "depNameTemplate": "postgres",
        }
        found = sb.from_custom_managers(
            [("default.json", sb.decode_config({"customManagers": [manager]}))],
            [path],
            [],
        )
    assert len(found) == 1
    assert found[0].origin == str(path)


def test_an_unresolvable_match_still_names_its_declaring_config() -> None:
    """Which of two regex managers could not be read is the report's job."""
    report: list[str] = []
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "images.toml"
        path.write_text('"18" = "postgres:18.1"\n', encoding="utf-8")
        manager = {
            "managerFilePatterns": ["/(^|/)images\\.toml$/"],
            "matchStrings": ['"[0-9]+" = "postgres:(?<currentValue>[0-9.]+)"'],
        }
        found = sb.from_custom_managers(
            [("renovate.json", sb.decode_config({"customManagers": [manager]}))],
            [path],
            report,
        )
    assert found == NO_DEPS
    assert any("renovate.json" in line for line in report)


def test_an_unreadable_file_is_reported_not_raised() -> None:
    """A walk records what it could not read and carries on."""
    report: list[str] = []
    assert sb.from_gomod([Path("/nonexistent/go.mod")], report) == NO_DEPS
    assert report


# The applicability guards, driven end to end in a real tree.
def run_tree(tree: dict[str, str]) -> tuple[int, str, str]:
    """Lay out a tree, run main() against it, capture everything.

    Returns:
        The exit status, stdout and stderr.

    """
    # The real directory, read before anything is patched (#1491):
    # under the patch below, Path.cwd() answers the temporary root.
    cwd = Path.cwd()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        belt = root / "belt"
        belt.mkdir()
        (belt / "committed.toml").write_text(
            "subject_length = 72\n",
            encoding="utf-8",
        )
        for name, body in tree.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        listing = "\0".join(tree)
        out, err = io.StringIO(), io.StringIO()
        env = {"ORG_BELT_DIR": str(belt), "ORG_CANON_DIR": str(root)}
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.setattr(sys, "stdin", io.StringIO(listing))
            patched.setattr(Path, "cwd", lambda *_args, **_kwargs: root)
            os.chdir(root)
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    status = sb.main([])
            except SystemExit as stop:
                # A refusal the task stops on: said as a finding, exit 1.
                status = stop.code if isinstance(stop.code, int) else 1
            finally:
                os.chdir(cwd)
        return status, out.getvalue(), err.getvalue()


def test_no_renovate_json_skips_clean() -> None:
    """Nothing mints subjects here, so there is nothing to bound."""
    status, out, _ = run_tree({"README.md": "# repo\n"})
    assert status == 0
    assert "no renovate.json tracked, skipped" in out


def test_a_declared_dependency_that_fits_passes() -> None:
    """The green path reports the count and the ceiling it met."""
    status, out, _ = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": json.dumps(PRESET),
            "mise.toml": '[tools]\nruff = "0.16.2"\n',
        },
    )
    assert status == 0
    assert "1 dependency" in out
    assert "fits 72 columns" in out


def test_an_over_budget_dependency_fails_with_the_remedy() -> None:
    """The red path names the dependency, the width, and the fix."""
    status, _, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": json.dumps(PRESET),
            "mise.toml": f'[tools]\n"{"n" * 60}" = "1.0.0"\n',
        },
    )
    assert status == 1
    assert "would mint a subject past 72 columns" in err
    assert "commitMessageTopic" in err


def test_the_preset_reference_is_itself_a_dependency() -> None:
    """A repo with only a renovate.json still counts one, not zero.

    The zero-dependency branch is therefore nearly unreachable, and
    this is the test that says so rather than leaving it looking
    like dead code.
    """
    manager = {
        "managerFilePatterns": ["/(^|/)renovate\\.json$/"],
        "matchStrings": [
            "github>monumental-archive/\\.github#(?<currentValue>v[0-9][\\w.+-]*)",
        ],
        "depNameTemplate": "monumental-archive/.github",
    }
    # The canon's own name is 26 columns and overruns once the
    # advisory suffix is on it, which is this repo's live case and
    # not what this test is about — so the fixture preset shortens
    # it the way default.json does.
    short = {
        "matchPackageNames": ["monumental-archive/.github"],
        "commitMessageTopic": "canon",
    }
    doc: dict[str, object] = {
        **PRESET,
        "customManagers": [manager],
        "packageRules": [short],
    }
    status, out, _ = run_tree(
        {
            "renovate.json": (
                '{"extends":["github>monumental-archive/.github#v1.46.0"]}\n'
            ),
            "default.json": json.dumps(doc),
        },
    )
    assert status == 0
    assert "1 dependency" in out


def test_a_repo_rule_reaches_the_end_to_end_verdict() -> None:
    """The whole point, driven through main().

    The same dependency goes red under a widening repo rule and
    green when it is removed, with no change to the preset either
    side.
    """
    name = "n" * 15
    tree = {
        "renovate.json": "{}\n",
        "default.json": json.dumps(PRESET),
        "mise.toml": f'[tools]\n"{name}" = "1.0.0"\n',
    }
    status, out, _ = run_tree(tree)
    assert status == 0
    assert "fits 72 columns" in out

    widen = {
        "packageRules": [
            {
                "matchPackageNames": [name],
                "commitMessageTopic": "{{depName}}-and-then-some-more",
            },
        ],
    }
    status, _, err = run_tree({**tree, "renovate.json": json.dumps(widen)})
    assert status == 1
    assert "would mint a subject past 72 columns" in err


def test_the_advisory_subject_reaches_the_end_to_end_verdict() -> None:
    """A dependency that fits ordinarily and not with the suffix.

    The whole of #686 driven through main(): the tree is green under
    the ordinary subject alone and red once the sixth field is
    modelled, and one topic rule in the preset turns it back.
    """
    name = "n" * 30
    tree = {
        "renovate.json": "{}\n",
        "default.json": json.dumps(PRESET),
        "mise.toml": f'[tools]\n"{name}" = "1.0.0"\n',
    }
    status, _, err = run_tree(tree)
    assert status == 1
    assert SUFFIX in err
    assert "would mint a subject past 72 columns" in err

    short = {
        "matchPackageNames": [name],
        "commitMessageTopic": "n" * 29,
    }
    doc: dict[str, object] = {**PRESET, "packageRules": [short]}
    status, out, _ = run_tree({**tree, "default.json": json.dumps(doc)})
    assert status == 0
    assert "fits 72 columns" in out


def test_a_file_names_rule_reaches_a_regex_managed_dependency() -> None:
    """#724 end to end: the origin fix and the matcher, together.

    A repo rule selected by FILE reaches the dependency a custom
    manager matched in that file. Before, the dependency carried the
    config that declared the manager as its origin, so no such rule
    could ever select it — and this repo's live `matchFileNames` rule
    over docker/pgrx-base-images.toml was invisible to the budget.
    """
    manager = {
        "managerFilePatterns": ["/^docker/images\\.toml$/"],
        "matchStrings": ['"[0-9]+" = "postgres:(?<currentValue>[a-z0-9.-]+)"'],
        "depNameTemplate": "postgres",
    }
    doc: dict[str, object] = {**PRESET, "customManagers": [manager]}
    tree = {
        "renovate.json": "{}\n",
        "default.json": json.dumps(doc),
        "docker/images.toml": '"18" = "postgres:18.1-bookworm"\n',
    }
    status, out, _ = run_tree(tree)
    assert status == 0
    assert "fits 72 columns" in out

    widen = {
        "packageRules": [
            {
                "matchFileNames": ["docker/images.toml"],
                "commitMessageTopic": "p" * 60,
            },
        ],
    }
    status, _, err = run_tree({**tree, "renovate.json": json.dumps(widen)})
    assert status == 1
    assert "would mint a subject past 72 columns" in err


def test_a_repo_top_level_field_reaches_the_end_to_end_verdict() -> None:
    """#725 driven through main(), both directions.

    The same dependency is green under the preset's `chore` and red
    under a `semanticCommitType` the repo writes beside its
    `extends` — the four columns the model could not see. 27
    characters is the width that tells them apart: 70 columns as
    `chore`, 74 as `refactor`.
    """
    name = "n" * 27
    tree = {
        "renovate.json": "{}\n",
        "default.json": json.dumps(PRESET),
        "mise.toml": f'[tools]\n"{name}" = "1.0.0"\n',
    }
    status, out, _ = run_tree(tree)
    assert status == 0
    assert "fits 72 columns" in out

    widen = {
        "extends": ["github>monumental-archive/.github"],
        "semanticCommitType": "refactor",
    }
    status, _, err = run_tree({**tree, "renovate.json": json.dumps(widen)})
    assert status == 1
    assert "would mint a subject past 72 columns" in err
    assert "refactor(deps)" in err


def test_a_preset_with_no_advisory_suffix_stops_the_task() -> None:
    """The sixth field answers the same law as the other five."""
    thin = {k: v for k, v in PRESET.items() if k != "vulnerabilityAlerts"}
    status, _, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": json.dumps(thin),
            "mise.toml": '[tools]\nruff = "0.16.2"\n',
        },
    )
    assert status == 1
    assert "commitMessageSuffix" in err


def test_an_incomplete_template_stops_the_task() -> None:
    """A preset missing a field errors; it does not quietly assume."""
    thin = {k: v for k, v in PRESET.items() if k != "commitMessageAction"}
    status, _, err = run_tree(
        {
            "renovate.json": "{}\n",
            "default.json": json.dumps(thin),
            "mise.toml": '[tools]\nruff = "0.16.2"\n',
        },
    )
    assert status == 1
    assert "commitMessageAction" in err


CANON = support.CANON

# A `[tools]` entry in the scaffold, live or commented out as an example:
# the key, then `=` (#1393).
SCAFFOLD_PIN = re.compile(r'^(?:#\s*)?("[^"]+"|[A-Za-z][\w:/.-]*)\s*=\s*(.*)$')


def scaffold_pins(text: str) -> dict[str, object]:
    """Read every tool pin the scaffold's `[tools]` names.

    The scaffold recommends most of its pins as comments, which Renovate
    and `lint:subject-budget` never see, so a key the canon tells every
    repository to adopt would reach them unbudgeted (#1393). Each live or
    commented `key = value` line in the `[tools]` section is decoded as
    TOML, a commented multi-line array included; prose lines are not
    pins and are skipped.

    Returns:
        Each key to its decoded value, in file order.

    """
    pins: dict[str, object] = {}
    lines = text.splitlines()
    start = lines.index("[tools]") + 1
    i = start
    while i < len(lines):
        line = lines[i]
        if re.match(r"^#?\s*\[", line):
            break
        match = SCAFFOLD_PIN.match(line)
        i += 1
        if not match:
            continue
        body = [line.lstrip("# ")]
        if (typed_std.group(match, 2) or "").strip() == "[":
            while i < len(lines) and lines[i].lstrip("# ").strip() != "]":
                body.append(lines[i].lstrip("#").strip())
                i += 1
            body.append("]")
            i += 1
        pins.update(decoded.load_toml("\n".join(body)))
    return pins


# Every key the scaffold recommends mints a subject that fits (#1393).
#
# The canon pins none of these itself, so its own gate never budgeted
# them: #1334 changed the recommended cargo-auditable key and the first
# repository to adopt it went red on an 82-column subject.
PINS = scaffold_pins(
    (CANON / "scaffold" / "mise.toml").read_text(encoding="utf-8"),
)


def test_the_reader_finds_the_recommended_keys() -> None:
    """No pins read would pass the budget row below vacuously."""
    want: list[str] = [
        "rust",
        "github:rust-secure-code/cargo-auditable",
        "cargo:cargo-fuzz",
        "go",
        "go:golang.org/x/vuln/cmd/govulncheck",
    ]
    keys: list[str] = list(PINS)
    assert want == keys


def test_every_key_fits_under_the_delivered_preset() -> None:
    """The real default.json and the real ceiling, advisory included."""
    preset = decoded.as_object(
        decoded.load_json((CANON / "default.json").read_text(encoding="utf-8")),
        "default.json",
    )
    limit = decoded.as_int(
        decoded.load_toml(
            (CANON / "mise" / "committed.toml").read_text(encoding="utf-8"),
        )["subject_length"],
        "subject_length",
    )
    deps = [
        sb.Dep(key, sb.widest_version(spec), "mise", "scaffold/mise.toml")
        for key, spec in PINS.items()
    ]
    delivered, repo = sb.decode_config(preset), sb.decode_config({})
    template = sb.Template(delivered, repo, sb.advisory_suffix(delivered, repo))
    findings = sb.judge(deps, template, limit, [])
    over = [f"{f.dep.name}: {f.width} cols, {f.subject}" for f in findings]
    none: list[str] = []
    assert none == over


# A manifest of the wrong shape is unmodelled, never a traceback (#1555).
#
# The regression rows of what `prop_subject_budget.py`'s refusal
# property found: each reader raised on a table of the wrong type, or
# a file that is not UTF-8, before any tool had refused the manifest.
WRONG_SHAPED_MANIFESTS_ROWS = (
    ("package.json", b"[]", "not a JSON object"),
    ("package.json", b'{"dependencies": []}', "`dependencies` is not a table"),
    ("Cargo.toml", b"dependencies = 1\n", "`dependencies` is not a table"),
    ("Cargo.toml", b"target = 1\n", "`target` is not a table"),
    ("Cargo.toml", b"[target]\nx = 1\n", "`target.x` is not a table"),
    ("Cargo.toml", b"workspace = 1\n", "`workspace` is not a table"),
    ("Cargo.toml", b"\xff\n", "unreadable"),
    ("go.mod", b"\xff\n", "unreadable"),
    ("mise.toml", b"tools = 1\n", "`tools` is not a table"),
    ("mise.toml", b'tools = { "" = false }\n', "declares no readable version"),
)


@pytest.mark.parametrize(("name", "data", "said"), WRONG_SHAPED_MANIFESTS_ROWS)
def test_each_is_recorded(name: str, data: bytes, said: str, tmp_path: Path) -> None:
    """No dependency, and one report line naming the file."""
    readers = {
        "package.json": sb.from_npm,
        "Cargo.toml": sb.from_cargo,
        "go.mod": sb.from_gomod,
        "mise.toml": sb.from_mise,
    }
    path = tmp_path / name
    path.write_bytes(data)
    report: list[str] = []
    assert readers[name]([path], report) == NO_DEPS
    assert len(report) == 1, report
    assert said in report[0]
    assert report[0].startswith(str(path))

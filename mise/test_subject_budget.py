#!/usr/bin/env python3
"""Table tests for the subject budget, guard branches first.

The defect class #364 named survives its closure: skip-clean guards are
the least exercised code in the org, and a guard that skips when it
should run looks exactly like success. Proving a branch once by hand is
not a table test and does not catch the next regression, so every guard
in `subject-budget.py` is driven here — both directions where there are
two, and the hard-error paths asserted as errors rather than fallbacks.

stdlib `unittest`, deliberately. #364 refused a test framework on the
grounds that adopting one is a fourth thing to port; that reasoning bites
a new dependency, not the batteries already inside the pinned
interpreter. Nothing is added to the belt to run this.

Run through the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import io
import json
import sys
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "subject_budget",
    Path(__file__).with_name("subject-budget.py"),
)
sb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sb)

# A minimal preset in the shape default.json now has: all five message
# fields explicit, so nothing here depends on a Renovate default.
PRESET = {
    "commitMessageAction": "update",
    "commitMessageTopic": "{{depName}}",
    "commitMessageExtra": "to {{newValue}}",
    "semanticCommitType": "chore",
    "semanticCommitScope": "deps",
    "packageRules": [],
}


def dep(name: str, current: str = "v1.0.0", manager: str = "mise") -> sb.Dep:
    """Build a dependency for a table row.

    Returns:
        A Dep with a stable origin, so tests compare on what they set.

    """
    return sb.Dep(name, current, manager, "fixture")


def preset(**overrides: object) -> dict:
    """Copy the baseline preset with fields or rules replaced.

    Returns:
        A preset dict safe to mutate.

    """
    return {**PRESET, **overrides}


def width_of(name: str, current: str) -> int:
    """Render one dependency under the baseline preset and measure it.

    Returns:
        The subject's column count, growth allowance included.

    """
    subject, _ = sb.render(dict(PRESET), name, len(current) + sb.VERSION_GROWTH)
    return len(subject)


class TestMatches(unittest.TestCase):
    """matchPackageNames, including the measured packageName behaviour."""

    def test_exact_name(self) -> None:
        """An exact depName selects that dependency and no other."""
        self.assertTrue(sb.matches("ruff", "ruff"))
        self.assertFalse(sb.matches("ruff", "rumdl"))

    def test_star_selects_everything(self) -> None:
        """The catch-all rule reaches every dependency."""
        self.assertTrue(sb.matches("*", "anything/at-all"))

    def test_glob_matches_backend_stripped_name(self) -> None:
        """`monumental-archive/**` selects a mise dep carrying a backend.

        Measured on #574: Renovate renders depName WITH the backend
        prefix but selects on the stripped packageName, which is how
        that pull request came out scoped `chore(canon)`.
        """
        self.assertTrue(
            sb.matches("monumental-archive/**", "github:monumental-archive/stele"),
        )
        self.assertTrue(
            sb.matches("monumental-archive/**", "monumental-archive/.github"),
        )
        self.assertFalse(sb.matches("monumental-archive/**", "actions/checkout"))

    def test_single_star_does_not_cross_a_slash(self) -> None:
        """A single star is one path segment, as in Renovate's globs."""
        self.assertTrue(sb.matches("actions/*", "actions/checkout"))
        self.assertFalse(sb.matches("actions/*", "actions/some/nested"))


class TestSelects(unittest.TestCase):
    """Rule matchers, including the AND across matcher kinds."""

    def test_manager_only(self) -> None:
        """A manager-scoped rule takes that manager and no other."""
        rule = {"matchManagers": ["gomod"]}
        self.assertTrue(sb.selects(rule, dep("x", manager="gomod")))
        self.assertFalse(sb.selects(rule, dep("x", manager="mise")))

    def test_names_only(self) -> None:
        """A name-scoped rule ignores which manager declared it."""
        rule = {"matchPackageNames": ["ruff"]}
        self.assertTrue(sb.selects(rule, dep("ruff", manager="mise")))
        self.assertFalse(sb.selects(rule, dep("rumdl", manager="mise")))

    def test_both_matchers_must_hold(self) -> None:
        """Renovate ANDs across matcher kinds; so does this."""
        rule = {"matchManagers": ["gomod"], "matchPackageNames": ["ruff"]}
        self.assertTrue(sb.selects(rule, dep("ruff", manager="gomod")))
        self.assertFalse(sb.selects(rule, dep("ruff", manager="mise")))
        self.assertFalse(sb.selects(rule, dep("rumdl", manager="gomod")))

    def test_rule_with_no_matcher_selects_nothing(self) -> None:
        """A rule setting something this task does not render is inert."""
        self.assertFalse(sb.selects({"automerge": True}, dep("ruff")))


class TestEffective(unittest.TestCase):
    """Field resolution: read from the preset, later-wins, never defaulted."""

    def test_reads_all_five_from_the_top_level(self) -> None:
        """With no rules, every field comes from the preset as written."""
        config = sb.effective(preset(), dep("ruff"))
        self.assertEqual(
            config,
            {k: PRESET[k] for k in sb.MESSAGE_CONFIG},
        )

    def test_later_rule_wins_per_field(self) -> None:
        """Two rules touching one dependency resolve in declared order."""
        rules = [
            {"matchPackageNames": ["*"], "commitMessageTopic": "first"},
            {"matchPackageNames": ["ruff"], "commitMessageTopic": "second"},
        ]
        config = sb.effective(preset(packageRules=rules), dep("ruff"))
        self.assertEqual(config["commitMessageTopic"], "second")

    def test_a_rule_leaves_fields_it_does_not_set(self) -> None:
        """Overriding the topic must not disturb the semantic prefix."""
        rules = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "short"}]
        config = sb.effective(preset(packageRules=rules), dep("ruff"))
        self.assertEqual(config["semanticCommitType"], "chore")
        self.assertEqual(config["commitMessageExtra"], "to {{newValue}}")

    def test_manager_scoped_rule_reaches_only_that_manager(self) -> None:
        """The gomod rule keeps `fix` without touching anything else."""
        rules = [{"matchManagers": ["gomod"], "semanticCommitType": "fix"}]
        config = preset(packageRules=rules)
        self.assertEqual(
            sb.effective(config, dep("x", manager="gomod"))["semanticCommitType"],
            "fix",
        )
        self.assertEqual(
            sb.effective(config, dep("x", manager="mise"))["semanticCommitType"],
            "chore",
        )

    def test_absent_field_is_a_hard_error_not_a_fallback(self) -> None:
        """A field the preset omits stops the task and names itself.

        The review decision on #576: a simulation that fills in a value
        the org did not write is agreeing with Renovate from memory, and
        an upstream default changing underneath it is invisible.
        """
        incomplete = preset()
        del incomplete["commitMessageExtra"]
        with self.assertRaises(SystemExit) as caught:
            sb.effective(incomplete, dep("ruff"))
        self.assertIn("commitMessageExtra", str(caught.exception))

    def test_every_missing_field_is_named_at_once(self) -> None:
        """The error lists all absent fields, not just the first."""
        with self.assertRaises(SystemExit) as caught:
            sb.effective({"packageRules": []}, dep("ruff"))
        for field in sb.MESSAGE_CONFIG:
            self.assertIn(field, str(caught.exception))


class TestRepoRules(unittest.TestCase):
    """The repo's own packageRules, folded in after the preset's (#677).

    Both directions per #650. The narrowing direction was already true
    by accident before this existed — `fix(deps):` is two columns under
    `chore(deps):` — so it is the WIDENING rows that carry the proof:
    the preset-only model went green on a subject Renovate cannot mint
    inside the ceiling, and that is the state #576 exists to forbid.
    """

    def test_no_repo_rules_is_the_preset_only_model(self) -> None:
        """The default argument leaves the resolution exactly as it was.

        Fail-SAFE by construction: a caller that supplies nothing gets
        the over-estimating model rather than a silently narrower one.
        """
        rules = [{"matchPackageNames": ["*"], "commitMessageTopic": "kept"}]
        config = preset(packageRules=rules)
        self.assertEqual(
            sb.effective(config, dep("ruff")),
            sb.effective(config, dep("ruff"), []),
        )

    def test_a_repo_rule_narrows_a_preset_field(self) -> None:
        """#668's live case, followed down.

        The canon takes `fix` where the preset says `chore`, and the
        model must resolve to the narrower prefix rather than the
        preset's.
        """
        repo = [{"matchManagers": ["mise"], "semanticCommitType": "fix"}]
        config = sb.effective(preset(), dep("ruff"), repo)
        self.assertEqual(config["semanticCommitType"], "fix")

    def test_a_repo_rule_widens_a_preset_field(self) -> None:
        """THE direction that matters.

        A widening repo rule is modelled wide, not at the preset's
        narrower value — the row the preset-only model got wrong.
        """
        repo = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "a" * 40}]
        config = sb.effective(preset(), dep("ruff"), repo)
        self.assertEqual(config["commitMessageTopic"], "a" * 40)

    def test_a_repo_rule_resolves_after_every_preset_rule(self) -> None:
        """Ordering is not a choice.

        Repo config resolves after an extended preset, so the repo's
        rule appends to the preset's list and wins the field they
        both set.
        """
        rules = [{"matchPackageNames": ["*"], "commitMessageTopic": "preset-last"}]
        repo = [{"matchPackageNames": ["*"], "commitMessageTopic": "repo"}]
        config = sb.effective(preset(packageRules=rules), dep("ruff"), repo)
        self.assertEqual(config["commitMessageTopic"], "repo")

    def test_later_wins_within_the_repo_list_too(self) -> None:
        """The canon's loop guard is last in its own list and must win."""
        repo = [
            {"matchManagers": ["mise"], "semanticCommitType": "fix"},
            {
                "matchPackageNames": ["monumental-archive/.github"],
                "semanticCommitType": "chore",
                "semanticCommitScope": "canon",
            },
        ]
        config = sb.effective(preset(), dep("monumental-archive/.github"), repo)
        self.assertEqual(config["semanticCommitType"], "chore")
        self.assertEqual(config["semanticCommitScope"], "canon")

    def test_a_repo_rule_that_does_not_select_changes_nothing(self) -> None:
        """Selection is unchanged; only the list it walks grew."""
        repo = [{"matchPackageNames": ["rumdl"], "commitMessageTopic": "other"}]
        config = sb.effective(preset(), dep("ruff"), repo)
        self.assertEqual(config["commitMessageTopic"], "{{depName}}")

    def test_a_repo_rule_may_not_be_the_only_place_a_field_is_written(
        self,
    ) -> None:
        """The absent-field error sits BETWEEN the two lists.

        That placement is the rule: a repo may override a field the
        preset sets, never supply one it does not. Otherwise a consumer
        that does not extend the canon inherits nothing and the hard
        error stops meaning what it says.
        """
        thin = preset()
        del thin["commitMessageAction"]
        repo = [{"matchPackageNames": ["*"], "commitMessageAction": "bump"}]
        with self.assertRaises(SystemExit) as caught:
            sb.effective(thin, dep("ruff"), repo)
        self.assertIn("commitMessageAction", str(caught.exception))

    def test_judge_goes_red_on_a_widening_repo_rule_and_green_without(
        self,
    ) -> None:
        """Plant and measure, both directions, through the real judge."""
        row = [dep("ruff", "v1.0.0")]
        limit = width_of("ruff", "v1.0.0")
        self.assertEqual(sb.judge(row, PRESET, limit, []), [])
        widen = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "ruff-x"}]
        findings = sb.judge(row, PRESET, limit, [], widen)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].width, limit + 2)
        narrow = [{"matchPackageNames": ["ruff"], "commitMessageTopic": "rf"}]
        self.assertEqual(sb.judge(row, PRESET, limit, [], narrow), [])


class TestRender(unittest.TestCase):
    """Subject composition, substitution, and the unmodelled report."""

    def test_composes_the_whole_subject(self) -> None:
        """Prefix, action, topic, extra and the pinned suffix, in order."""
        subject, unmodelled = sb.render(dict(PRESET), "ruff", 6)
        self.assertEqual(subject, "chore(deps): update ruff to 999999 (#999999)")
        self.assertIsNone(unmodelled)

    def test_substitutes_triple_and_double_braces(self) -> None:
        """Renovate writes both spellings; both must resolve."""
        config = dict(PRESET)
        config["commitMessageTopic"] = "{{{depName}}}"
        config["commitMessageExtra"] = "to {{{newValue}}}"
        subject, unmodelled = sb.render(config, "ruff", 3)
        self.assertEqual(subject, "chore(deps): update ruff to 999 (#999999)")
        self.assertIsNone(unmodelled)

    def test_an_empty_field_is_skipped_not_double_spaced(self) -> None:
        """A preset may empty a field; the subject must not gain a gap."""
        config = dict(PRESET)
        config["commitMessageExtra"] = ""
        subject, _ = sb.render(config, "ruff", 6)
        self.assertEqual(subject, "chore(deps): update ruff (#999999)")

    def test_unresolved_placeholder_is_reported(self) -> None:
        """A template this task cannot model is named, never measured raw."""
        config = dict(PRESET)
        config["commitMessageTopic"] = "{{depName}} {{newMajor}}"
        _, unmodelled = sb.render(config, "ruff", 6)
        self.assertIsNotNone(unmodelled)
        self.assertIn("newMajor", unmodelled)

    def test_suffix_uses_the_pinned_worst_case(self) -> None:
        """The rendered suffix is as wide as the pinned digit count."""
        subject, _ = sb.render(dict(PRESET), "x", 1)
        self.assertTrue(subject.endswith(f" (#{'9' * sb.PR_NUMBER_DIGITS})"))


class TestJudge(unittest.TestCase):
    """Budget arithmetic, exercised at the boundary in both directions."""

    def test_exactly_at_the_ceiling_passes(self) -> None:
        """A subject of exactly `limit` columns is not over budget."""
        name = "n" * 20
        limit = width_of(name, "v1.0.0")
        self.assertEqual(sb.judge([dep(name, "v1.0.0")], PRESET, limit, []), [])

    def test_one_column_over_fails(self) -> None:
        """One column past the ceiling is a finding, reported with it."""
        name = "n" * 20
        limit = width_of(name, "v1.0.0") - 1
        findings = sb.judge([dep(name, "v1.0.0")], PRESET, limit, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].width, limit + 1)
        self.assertEqual(findings[0].dep.name, name)

    def test_version_growth_is_added_to_the_declared_width(self) -> None:
        """The budget measures the NEXT version, not the one in the tree."""
        bare = len(sb.render(dict(PRESET), "x", len("v1.0.0"))[0])
        self.assertEqual(width_of("x", "v1.0.0"), bare + sb.VERSION_GROWTH)

    def test_a_go_pseudo_version_gets_no_growth(self) -> None:
        """Pseudo-versions are fixed-width, so the allowance is zero."""
        pseudo = "v0.0.0-20241213102144-19d51d7fe467"
        self.assertRegex(pseudo, sb.PSEUDO_VERSION)
        limit = len(sb.render(dict(PRESET), "x", len(pseudo))[0])
        self.assertEqual(sb.judge([dep("x", pseudo)], PRESET, limit, []), [])

    def test_a_name_is_judged_once(self) -> None:
        """Two managers declaring one name produce one finding, not two."""
        rows = [dep("n" * 60, "v1.0.0"), dep("n" * 60, "v1.0.0", manager="npm")]
        self.assertEqual(len(sb.judge(rows, PRESET, 40, [])), 1)

    def test_unresolved_template_reaches_the_report(self) -> None:
        """judge() records what render() could not model."""
        config = preset(commitMessageTopic="{{depName}} {{newMajor}}")
        report: list[str] = []
        sb.judge([dep("ruff")], config, 200, report)
        self.assertTrue(any("newMajor" in line for line in report))


class TestCeiling(unittest.TestCase):
    """The ceiling comes from the delivered config or the task stops."""

    def test_reads_subject_length_from_the_delivered_config(self) -> None:
        """The one definition of 72 is the delivered committed.toml."""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "committed.toml").write_text(
                "subject_length = 61\n",
                encoding="utf-8",
            )
            with mock.patch.dict(sb.os.environ, {"ORG_BELT_DIR": tmp}):
                self.assertEqual(sb.ceiling(), 61)

    def test_unset_belt_dir_is_a_hard_error(self) -> None:
        """No belt means no ceiling; the task must not invent one."""
        with (
            mock.patch.dict(sb.os.environ, {"ORG_BELT_DIR": ""}),
            self.assertRaises(
                SystemExit,
            ),
        ):
            sb.ceiling()

    def test_absent_committed_toml_is_a_hard_error(self) -> None:
        """A belt without the delivered config fails rather than defaults."""
        with (
            TemporaryDirectory() as tmp,
            mock.patch.dict(
                sb.os.environ,
                {"ORG_BELT_DIR": tmp},
            ),
            self.assertRaises(SystemExit),
        ):
            sb.ceiling()

    def test_the_canon_ceiling_is_what_the_org_ships(self) -> None:
        """The delivered file parses and carries a subject_length."""
        shipped = Path(__file__).with_name("committed.toml")
        parsed = tomllib.loads(shipped.read_text(encoding="utf-8"))
        self.assertIsInstance(parsed["subject_length"], int)


class TestWalks(unittest.TestCase):
    """The per-manager walks, on the cases that were once wrong."""

    def test_mise_takes_the_widest_of_an_array_pin(self) -> None:
        """A repo that fuzzes pins two toolchains; the wider one governs."""
        self.assertEqual(sb.widest_version("1.2.3"), "1.2.3")
        self.assertEqual(sb.widest_version({"version": "1.2.3"}), "1.2.3")
        self.assertEqual(
            sb.widest_version([{"version": "1.97.1"}, "nightly-2026-07-20"]),
            "nightly-2026-07-20",
        )

    def test_gomod_skips_indirect_requires(self) -> None:
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
        self.assertEqual([d.name for d in found], ["github.com/direct/one"])
        self.assertEqual(found[0].manager, "gomod")

    def test_a_sha_pinned_action_is_measured_at_the_short_digest(self) -> None:
        """Renovate renders newDigestShort, never the 40-character pin."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "action.yml"
            path.write_text(f"    uses: owner/repo@{'a' * 40}\n", encoding="utf-8")
            found = sb.from_actions([path], [])
        self.assertEqual(len(found[0].current), sb.DIGEST_SHORT)

    def test_a_version_comment_beats_the_ref(self) -> None:
        """A `# vX.Y.Z` comment is what reaches the subject."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "action.yml"
            path.write_text(
                f"    uses: owner/repo@{'a' * 40} # v4.37.7\n",
                encoding="utf-8",
            )
            found = sb.from_actions([path], [])
        self.assertEqual(found[0].current, "v4.37.7")

    def test_local_and_self_references_name_no_dependency(self) -> None:
        """`./`, `$/` and container steps have no upstream to bump."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "action.yml"
            path.write_text(
                "    uses: ./.github/actions/thing\n"
                "    uses: $/.github/actions/canon\n"
                "    uses: docker://alpine:3\n",
                encoding="utf-8",
            )
            self.assertEqual(sb.from_actions([path], []), [])

    def test_cargo_skips_path_and_workspace_entries(self) -> None:
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
        self.assertEqual([d.name for d in found], ["serde"])

    def test_a_custom_manager_runs_its_javascript_regex(self) -> None:
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
                [("default.json", {"customManagers": [manager]})],
                [path],
                [],
            )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "monumental-archive/.github")
        self.assertEqual(found[0].current, "v1.46.0")
        self.assertEqual(found[0].manager, "custom.regex")

    def test_an_unreadable_file_is_reported_not_raised(self) -> None:
        """A walk records what it could not read and carries on."""
        report: list[str] = []
        self.assertEqual(sb.from_gomod([Path("/nonexistent/go.mod")], report), [])
        self.assertTrue(report)


class TestMainGuards(unittest.TestCase):
    """The applicability guards, driven end to end in a real tree."""

    @staticmethod
    def _run(tree: dict[str, str]) -> tuple[int, str, str]:
        """Lay out a tree, run main() against it, capture everything.

        Returns:
            The exit status, stdout and stderr.

        """
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
            listing = "\n".join(tree)
            out, err = io.StringIO(), io.StringIO()
            env = {"ORG_BELT_DIR": str(belt), "ORG_CANON_DIR": str(root)}
            with (
                mock.patch.dict(sb.os.environ, env),
                mock.patch.object(
                    sb.sys,
                    "stdin",
                    io.StringIO(listing),
                ),
                mock.patch.object(sb.Path, "cwd", return_value=root),
            ):
                cwd = Path.cwd()
                sb.os.chdir(root)
                try:
                    with redirect_stdout(out), redirect_stderr(err):
                        status = sb.main()
                finally:
                    sb.os.chdir(cwd)
            return status, out.getvalue(), err.getvalue()

    def test_no_renovate_json_skips_clean(self) -> None:
        """Nothing mints subjects here, so there is nothing to bound."""
        status, out, _ = self._run({"README.md": "# repo\n"})
        self.assertEqual(status, 0)
        self.assertIn("no renovate.json tracked, skipped", out)

    def test_a_declared_dependency_that_fits_passes(self) -> None:
        """The green path reports the count and the ceiling it met."""
        status, out, _ = self._run(
            {
                "renovate.json": "{}\n",
                "default.json": json.dumps(PRESET),
                "mise.toml": '[tools]\nruff = "0.16.2"\n',
            },
        )
        self.assertEqual(status, 0)
        self.assertIn("1 dependency", out)
        self.assertIn("fits 72 columns", out)

    def test_an_over_budget_dependency_fails_with_the_remedy(self) -> None:
        """The red path names the dependency, the width, and the fix."""
        status, _, err = self._run(
            {
                "renovate.json": "{}\n",
                "default.json": json.dumps(PRESET),
                "mise.toml": f'[tools]\n"{"n" * 60}" = "1.0.0"\n',
            },
        )
        self.assertEqual(status, 1)
        self.assertIn("would mint a subject past 72 columns", err)
        self.assertIn("commitMessageTopic", err)

    def test_the_preset_reference_is_itself_a_dependency(self) -> None:
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
        status, out, _ = self._run(
            {
                "renovate.json": (
                    '{"extends":["github>monumental-archive/.github#v1.46.0"]}\n'
                ),
                "default.json": json.dumps({**PRESET, "customManagers": [manager]}),
            },
        )
        self.assertEqual(status, 0)
        self.assertIn("1 dependency", out)

    def test_a_repo_rule_reaches_the_end_to_end_verdict(self) -> None:
        """The whole point, driven through main().

        The same dependency goes red under a widening repo rule and
        green when it is removed, with no change to the preset either
        side.
        """
        name = "n" * 30
        tree = {
            "renovate.json": "{}\n",
            "default.json": json.dumps(PRESET),
            "mise.toml": f'[tools]\n"{name}" = "1.0.0"\n',
        }
        status, out, _ = self._run(tree)
        self.assertEqual(status, 0)
        self.assertIn("fits 72 columns", out)

        widen = {
            "packageRules": [
                {
                    "matchPackageNames": [name],
                    "commitMessageTopic": "{{depName}}-and-then-some-more",
                },
            ],
        }
        status, _, err = self._run({**tree, "renovate.json": json.dumps(widen)})
        self.assertEqual(status, 1)
        self.assertIn("would mint a subject past 72 columns", err)

    def test_an_incomplete_template_stops_the_task(self) -> None:
        """A preset missing a field errors; it does not quietly assume."""
        thin = {k: v for k, v in PRESET.items() if k != "commitMessageAction"}
        with self.assertRaises(SystemExit) as caught:
            self._run(
                {
                    "renovate.json": "{}\n",
                    "default.json": json.dumps(thin),
                    "mise.toml": '[tools]\nruff = "0.16.2"\n',
                },
            )
        self.assertIn("commitMessageAction", str(caught.exception))


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())

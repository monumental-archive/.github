#!/usr/bin/env python3
"""Table tests for the generated biome config (#695).

Every branch of the declaration guard gets a row, both directions:
something a repository may say and the nearest thing it may not. The
generator's own arithmetic — claimed domains "all", unclaimed identity
domains "none", org domains always "all" — is asserted against the
DELIVERED table rather than a copy of it, so a row added to
`mise/biome-domains.tsv` is covered the moment it lands.

These tests are mutation-checked: each was run against a deliberately
broken helper and observed to fail. A test that passes both ways is not
a test, and the guards here are exactly the class #364 named — the least
exercised code in the org, where a check that admits what it should
refuse looks precisely like success.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import ClassVar

_SPEC = importlib.util.spec_from_file_location(
    "biome_config",
    Path(__file__).with_name("biome-config.py"),
)
biome_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(biome_config)

BELT = Path(__file__).parent
DOMAINS_FILE = BELT / "biome-domains.tsv"
NURSERY_FILE = BELT / "biome-nursery-domains.tsv"
ORG_FILE = BELT / "biome-org.json"
TSC_FLAGS_FILE = BELT / "tsc-flags.txt"

DELIVERED = biome_config.read_domains(DOMAINS_FILE)
NURSERY = biome_config.read_nursery(NURSERY_FILE)


def declaration(body: dict) -> str:
    """Render a repository declaration.

    Returns:
        The file's text.

    """
    return json.dumps(body)


class ReadDomainsTest(unittest.TestCase):
    """The delivered table parses into the split the belt relies on."""

    def test_identity_and_org_are_both_populated(self) -> None:
        """Neither half may be empty, or the mechanism is vacuous."""
        self.assertTrue(DELIVERED.identity)
        self.assertTrue(DELIVERED.org)

    def test_org_domains_are_the_three_with_no_dependency_trigger(self) -> None:
        """project, types and test are org strictness, never repo identity."""
        self.assertEqual(sorted(DELIVERED.org), ["project", "test", "types"])

    def test_every_identity_domain_names_at_least_one_trigger(self) -> None:
        """An identity domain with no trigger could never be checked for."""
        for domain, triggers in DELIVERED.identity.items():
            with self.subTest(domain=domain):
                self.assertTrue(triggers)

    def test_react_carries_its_measured_trigger(self) -> None:
        """The fixture domain, spot-checked against `biome explain`."""
        self.assertEqual(DELIVERED.identity["react"], ["react"])

    def test_comments_and_blank_lines_are_not_rows(self) -> None:
        """The file is commented heavily; none of it may become a domain."""
        self.assertNotIn("#", "".join(DELIVERED.identity))


class DeclarationProblemsTest(unittest.TestCase):
    """What a repository may and may not put in its own biome.json."""

    def check(self, document: object, *, admissible: bool) -> list[str]:
        """Judge one declaration.

        Returns:
            The refusals.

        """
        problems = biome_config.declaration_problems(document, DELIVERED.identity)
        self.assertEqual(not problems, admissible, msg=f"{document} -> {problems}")
        return problems

    def test_admissible_declarations(self) -> None:
        """Everything a conforming repository is allowed to write."""
        for document in (
            {},
            {"linter": {}},
            {"linter": {"domains": {}}},
            {"linter": {"domains": {"react": "all"}}},
            {"$schema": "./x.json", "linter": {"domains": {"react": "all"}}},
            {"linter": {"domains": {"react": "all", "next": "all"}}},
            # Over-claiming is legal on purpose: it turns rules ON.
            {"linter": {"domains": {"vue": "all"}}},
        ):
            with self.subTest(document=document):
                self.check(document, admissible=True)

    def test_a_repository_may_not_write_none(self) -> None:
        """The word "none" is the belt's — writing it here switches a domain off."""
        problems = self.check(
            {"linter": {"domains": {"react": "none"}}},
            admissible=False,
        )
        self.assertIn("none", problems[0])

    def test_a_repository_may_not_lower_a_domain_it_claims(self) -> None:
        """A "recommended" value is below the org's level, so it is refused."""
        self.check({"linter": {"domains": {"react": "recommended"}}}, admissible=False)

    def test_org_domains_may_not_be_named_at_all(self) -> None:
        """project, types and test are strictness, not identity."""
        for domain in DELIVERED.org:
            with self.subTest(domain=domain):
                problems = self.check(
                    {"linter": {"domains": {domain: "all"}}},
                    admissible=False,
                )
                self.assertIn(domain, problems[0])

    def test_an_unknown_domain_is_refused(self) -> None:
        """A typo must red rather than silently claim nothing."""
        self.check({"linter": {"domains": {"raect": "all"}}}, admissible=False)

    def test_rules_may_not_travel_with_the_declaration(self) -> None:
        """The whole point: no repo-side surface on which to lower a rule."""
        problems = self.check(
            {"linter": {"rules": {"correctness": {"noNodejsModules": "off"}}}},
            admissible=False,
        )
        self.assertIn("linter.rules", problems[0])

    def test_other_biome_sections_are_refused(self) -> None:
        """An overrides block is a per-path weakening surface; so is formatter."""
        for key in ("overrides", "formatter", "assist", "files", "extends"):
            with self.subTest(key=key):
                self.check({key: {}}, admissible=False)

    def test_wrong_shapes_are_refused_rather_than_crashing(self) -> None:
        """A malformed file must produce a refusal, never a traceback."""
        for document in ([], "react", {"linter": []}, {"linter": {"domains": []}}):
            with self.subTest(document=document):
                self.check(document, admissible=False)


class OmissionsTest(unittest.TestCase):
    """Silence is the other way to lower a rule, so silence is checked."""

    def test_a_declared_dependency_must_be_claimed(self) -> None:
        """The #695 case: a react repo that says nothing gets react turned off."""
        missing = biome_config.omissions(
            set(),
            DELIVERED.identity,
            {"react": ["apps/web/package.json"]},
        )
        self.assertEqual(len(missing), 1)
        self.assertIn("react", missing[0])
        self.assertIn("apps/web/package.json", missing[0])

    def test_claiming_it_satisfies_the_check(self) -> None:
        """The remedy the message names actually works."""
        self.assertEqual(
            biome_config.omissions(
                {"react"},
                DELIVERED.identity,
                {"react": ["apps/web/package.json"]},
            ),
            [],
        )

    def test_an_unrelated_dependency_demands_nothing(self) -> None:
        """Only trigger packages carry the obligation."""
        self.assertEqual(
            biome_config.omissions(set(), DELIVERED.identity, {"hono": ["p.json"]}),
            [],
        )

    def test_either_trigger_of_a_two_package_domain_counts(self) -> None:
        """Qwik ships under two names; both oblige."""
        for package in DELIVERED.identity["qwik"]:
            with self.subTest(package=package):
                missing = biome_config.omissions(
                    set(),
                    DELIVERED.identity,
                    {package: ["package.json"]},
                )
                self.assertEqual(len(missing), 1)
                self.assertIn("qwik", missing[0])


class DeclaredPackagesTest(unittest.TestCase):
    """Where a dependency may be declared, and what is not a manifest."""

    def manifest(self, body: object) -> Path:
        """Write one manifest into a temporary tree.

        Returns:
            Its path.

        """
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        path = directory / "package.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_every_dependency_section_is_read(self) -> None:
        """A framework declared as a peer dependency is still a framework."""
        for section in biome_config.MANIFEST_SECTIONS:
            with self.subTest(section=section):
                path = self.manifest({section: {"react": "19"}})
                self.assertIn("react", biome_config.declared_packages([path]))

    def test_a_non_dependency_section_is_not_read(self) -> None:
        """Scripts named after a framework declare nothing."""
        path = self.manifest({"scripts": {"react": "echo"}})
        self.assertNotIn("react", biome_config.declared_packages([path]))

    def test_unreadable_and_malformed_manifests_are_skipped(self) -> None:
        """A broken manifest is another task's finding, not a crash here."""
        broken = Path(tempfile.mkdtemp()) / "package.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(
            biome_config.declared_packages([broken, Path("/nonexistent/package.json")]),
            {},
        )


class GenerateTest(unittest.TestCase):
    """The arithmetic the repository is not allowed to do for itself."""

    def setUp(self) -> None:
        """Generate against the real org config and the real tables."""
        self.org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        self.config, self.silenced = biome_config.generate(
            self.org,
            {"react"},
            DELIVERED,
            NURSERY,
        )
        self.written = self.config["linter"]["domains"]

    def test_every_domain_is_written_explicitly(self) -> None:
        """Nothing is left to biome's auto-detection or to preset: all."""
        self.assertEqual(
            sorted(self.written),
            sorted([*DELIVERED.identity, *DELIVERED.org]),
        )

    def test_the_claimed_domain_is_all(self) -> None:
        """What the repository said it is."""
        self.assertEqual(self.written["react"], "all")

    def test_every_unclaimed_identity_domain_is_none(self) -> None:
        """The measured half: only "none" changes anything under preset all."""
        for domain in DELIVERED.identity:
            if domain != "react":
                with self.subTest(domain=domain):
                    self.assertEqual(self.written[domain], "none")

    def test_org_domains_are_all_whatever_the_repository_said(self) -> None:
        """project, types and test never move."""
        for domain in DELIVERED.org:
            with self.subTest(domain=domain):
                self.assertEqual(self.written[domain], "all")

    def test_the_org_rules_survive_untouched(self) -> None:
        """The merge adds identity and silences nursery; nothing else moves."""
        generated = self.config["linter"]["rules"]
        original = self.org["linter"]["rules"]
        self.assertEqual(generated["preset"], original["preset"])
        self.assertEqual(
            {k: v for k, v in generated.items() if k != "nursery"},
            {k: v for k, v in original.items() if k != "nursery"},
        )
        self.assertEqual(self.config["assist"], self.org["assist"])

    def test_the_org_config_is_not_mutated_in_place(self) -> None:
        """Two runs in one process must not accumulate."""
        self.assertNotIn("domains", self.org.get("linter", {}))

    def test_a_repository_claiming_nothing_gets_every_identity_off(self) -> None:
        """The canon's own case: no framework, no framework rules."""
        config, _silenced = biome_config.generate(self.org, set(), DELIVERED, NURSERY)
        written = config["linter"]["domains"]
        self.assertEqual(
            {d for d, v in written.items() if v == "all"},
            set(DELIVERED.org),
        )


class ContradictionTest(unittest.TestCase):
    """Rules the org delivers that another org control forbids (#759).

    This is a CROSS-FILE invariant and it lives here because nothing else
    reads both files. `lint:biome` reads biome-org.json, `lint:types`
    reads tsc-flags.txt, and each is green in isolation while the pair is
    unsatisfiable on a real tree — which is exactly how it survived.
    """

    @staticmethod
    def tsc_dials() -> set[str]:
        """Read the dial names the org forces on the compiler.

        Returns:
            Every flag named in tsc-flags.txt, without its leading dashes.

        """
        dials = set()
        for raw in TSC_FLAGS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("--"):
                dials.add(line.split()[0].removeprefix("--"))
        return dials

    @staticmethod
    def rule_setting(group: str, rule: str) -> object:
        """Read one rule's setting out of the delivered config.

        Returns:
            The setting, or None when the group does not name the rule.

        """
        org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        return org["linter"]["rules"].get(group, {}).get(rule)

    def test_useliteralkeys_is_off_while_the_tsc_dial_is_forced(self) -> None:
        """The #759 pair, pinned so it cannot silently re-open.

        `useLiteralKeys` wants `row.id`; `noPropertyAccessFromIndexSignature`
        makes that TS4111 on an index-signature type. Measured on
        monumental-archive at 590 sites. If the dial is ever dropped from
        tsc-flags.txt the rule may come back — but not before.
        """
        if "noPropertyAccessFromIndexSignature" not in self.tsc_dials():
            self.skipTest("the org no longer forces the dial that contradicts it")
        self.assertEqual(self.rule_setting("complexity", "useLiteralKeys"), "off")

    def test_naming_that_rule_does_not_disable_its_whole_group(self) -> None:
        """Only the one rule is named; the preset still carries the rest."""
        org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(org["linter"]["rules"]["preset"], "all")
        self.assertEqual(
            set(org["linter"]["rules"]["complexity"]),
            {"useLiteralKeys"},
        )


class TestFileOverrideTest(unittest.TestCase):
    """The one `overrides` entry the org delivers (#783).

    In a test the literal — a number or a regex — IS the specification,
    so `style/noMagicNumbers` and `performance/useTopLevelRegex` are off
    for test files and nowhere else. Everything here is asserted against
    the literals biome understands rather than against a constant in
    this module: a test that compares a value to the thing that produced
    it passes whatever that thing says.
    """

    # The closed list, measured across every JS/TS file in the org
    # (monumental-archive is the whole population; the other repos have
    # none). Repeated here as literals ON PURPOSE — this is the mutation
    # check, and reading the list out of the file under test would make
    # the assertion vacuous.
    GLOBS: ClassVar[list[str]] = ["**/*.test.ts", "**/test/**", "**/tests/**"]
    SILENCED: ClassVar[dict[str, str]] = {
        "performance": "useTopLevelRegex",
        "style": "noMagicNumbers",
    }

    def setUp(self) -> None:
        """Read the delivered config, not a copy of it."""
        self.org = json.loads(ORG_FILE.read_text(encoding="utf-8"))

    def entry(self) -> dict:
        """Return the single override entry.

        Returns:
            The delivered override pattern.

        """
        overrides = self.org["overrides"]
        self.assertEqual(len(overrides), 1, msg="biome uses only the FIRST match")
        return overrides[0]

    def test_the_glob_list_is_exactly_the_closed_list(self) -> None:
        """Widening the list is an edit to this test as well as the config."""
        self.assertEqual(self.entry()["includes"], self.GLOBS)

    def test_the_shapes_the_org_does_not_have_are_absent(self) -> None:
        """`*.spec.*` and `__tests__/**` exist in no org repo, so are unnamed."""
        joined = " ".join(self.entry()["includes"])
        for absent in ("spec", "__tests__", "tsx", ".js"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, joined)

    def test_both_rules_are_off_against_the_literal(self) -> None:
        """Asserted against "off" itself; a mutant value must not survive."""
        rules = self.entry()["linter"]["rules"]
        for group, rule in self.SILENCED.items():
            with self.subTest(rule=rule):
                self.assertEqual(rules[group][rule], "off")

    def test_the_override_silences_those_two_rules_and_no_others(self) -> None:
        """A third rule arriving here is a decision, not a drive-by."""
        rules = self.entry()["linter"]["rules"]
        self.assertEqual(
            {(g, r) for g, block in rules.items() for r in block},
            set(self.SILENCED.items()),
        )

    def test_the_override_carries_nothing_but_those_rules(self) -> None:
        """No `linter.enabled: false`, no formatter, no files — rules only."""
        self.assertEqual(set(self.entry()), {"includes", "linter"})
        self.assertEqual(set(self.entry()["linter"]), {"rules"})

    def test_neither_rule_is_switched_off_at_the_top_level(self) -> None:
        """The other direction: a magic number in production still reds.

        Both rules are on by `preset: "all"` not naming them, so the
        proof that production is untouched is that neither appears in the
        top-level rule block at all.
        """
        top = self.org["linter"]["rules"]
        self.assertEqual(top["preset"], "all")
        for group, rule in self.SILENCED.items():
            with self.subTest(rule=rule):
                self.assertNotIn(rule, top.get(group, {}))

    def test_nomisplacedassertion_is_not_in_the_override(self) -> None:
        """#783's ruling: its 8 false positives are not a file-type question.

        Six are the assertion-helper pattern and two are the rule not
        knowing `test.prop` (biomejs/biome#11454). A test-glob override
        would remove the class it guards — an `expect` stranded outside
        `it`/`test`, which reports nothing and passes green.
        """
        rules = self.entry()["linter"]["rules"]
        self.assertNotIn("noMisplacedAssertion", rules.get("suspicious", {}))
        self.assertNotIn("suspicious", self.org["linter"]["rules"])

    def test_the_override_survives_generation_untouched(self) -> None:
        """A repository's identity moves domains and nursery, never this."""
        for claimed in (set(), {"react"}):
            with self.subTest(claimed=claimed):
                config, _silenced = biome_config.generate(
                    self.org,
                    claimed,
                    DELIVERED,
                    NURSERY,
                )
                self.assertEqual(config["overrides"], self.org["overrides"])

    def test_a_repository_may_not_declare_an_override_of_its_own(self) -> None:
        """The widening a repo would reach for is refused by name (#695)."""
        problems = biome_config.declaration_problems(
            {"overrides": [{"includes": ["src/**"], "linter": {"rules": {}}}]},
            DELIVERED.identity,
        )
        self.assertTrue(problems)
        self.assertIn("overrides", problems[0])


class ConditionalExpectTest(unittest.TestCase):
    """The one nursery rule the org switches off by name (#788).

    Eight findings on the first real tree, eight false positives, one of
    them in production code — the door's `assert` VERB read as a test
    assertion, which no test-glob override could reach. Off with an
    expiry at biomejs/biome#11455.
    """

    RULE = "noConditionalExpect"

    def setUp(self) -> None:
        """Read the delivered config, not a copy of it."""
        self.org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        self.nursery = self.org["linter"]["rules"]["nursery"]

    def test_the_rule_is_off_against_the_literal(self) -> None:
        """Asserted against "off" itself, never against a constant."""
        self.assertEqual(self.nursery[self.RULE], "off")

    def test_the_rule_is_still_named(self) -> None:
        """`preset: "all"` never reaches nursery, so the 87 names are the list.

        Deleting the name would leave the rule off by accident rather
        than by decision, and indistinguishable from one nobody ruled on.
        """
        self.assertIn(self.RULE, self.nursery)
        self.assertIn(self.RULE, NURSERY)

    def test_its_domain_is_org_fixed_so_no_repo_can_move_it(self) -> None:
        """`test` is org strictness; a repository cannot claim or lower it."""
        self.assertEqual(NURSERY[self.RULE], "test")
        self.assertIn(NURSERY[self.RULE], DELIVERED.org)
        self.assertNotIn(NURSERY[self.RULE], DELIVERED.identity)

    def test_no_repository_identity_turns_it_back_on(self) -> None:
        """The generator only ever writes "off"; assert it never writes "on"."""
        for claimed in (set(), {"react"}, set(DELIVERED.identity)):
            with self.subTest(claimed=sorted(claimed)):
                config, _silenced = biome_config.generate(
                    self.org,
                    claimed,
                    DELIVERED,
                    NURSERY,
                )
                rule = config["linter"]["rules"]["nursery"][self.RULE]
                self.assertEqual(rule, "off")

    def test_it_is_not_in_the_test_file_override(self) -> None:
        """The reason it is off org-wide: one of the eight is production code.

        `apps/door/src/submit.ts:176` — a local `assert` verb, not a test
        assertion. #783's globs would never have reached it.
        """
        for entry in self.org["overrides"]:
            with self.subTest(includes=entry["includes"]):
                rules = entry.get("linter", {}).get("rules", {})
                self.assertNotIn(self.RULE, rules.get("nursery", {}))


class NurseryTableTest(unittest.TestCase):
    """The delivered rule-to-domain table, and what the generator does with it."""

    def test_every_enforced_nursery_rule_is_mapped(self) -> None:
        """An unmapped rule escapes its domain — the whole #720 defect."""
        org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        enforced = set(org["linter"]["rules"]["nursery"])
        self.assertEqual(enforced - set(NURSERY), set())

    def test_the_table_maps_nothing_the_org_does_not_enforce(self) -> None:
        """A mapped rule nobody enables is a row that means nothing."""
        org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(set(NURSERY) - set(org["linter"]["rules"]["nursery"]), set())

    def test_no_domain_is_stated_rather_than_left_blank(self) -> None:
        """`-` is a statement; an empty column is a field someone forgot."""
        self.assertEqual(biome_config.NO_DOMAIN, "-")
        self.assertNotIn("", NURSERY.values())
        self.assertIn("-", NURSERY.values())

    def test_every_stated_domain_is_a_domain_the_org_knows(self) -> None:
        """A row naming a domain outside the table could never be claimed."""
        known = {*DELIVERED.identity, *DELIVERED.org, biome_config.NO_DOMAIN}
        for rule, domain in NURSERY.items():
            with self.subTest(rule=rule):
                self.assertIn(domain, known)

    def test_the_measured_fixture_rule_is_mapped_to_reactnative(self) -> None:
        """The rule that proved the defect, spot-checked."""
        self.assertEqual(NURSERY["noReactNativeRawText"], "reactNative")

    def test_comments_are_not_rows(self) -> None:
        """The file is commented heavily; none of it may become a rule."""
        self.assertNotIn("#", "".join(NURSERY))


class SilenceNurseryTest(unittest.TestCase):
    """Which nursery rules a repository's identity switches off."""

    @staticmethod
    def build(claimed: set[str], table: dict[str, str]) -> tuple:
        """Silence one small nursery block.

        Returns:
            The resulting block and the count reported.

        """
        config = {"linter": {"rules": {"nursery": dict.fromkeys(table, "on")}}}
        silenced = biome_config.silence_nursery(config, claimed, DELIVERED, table)
        return config["linter"]["rules"]["nursery"], silenced

    def test_the_silencing_value_is_the_literal_biome_understands(self) -> None:
        """Asserted against "off" itself, never against the constant.

        Comparing a result to the constant that produced it passes whatever
        the constant says — proven: a mutant setting SILENCED to "on"
        survived a whole suite that did exactly that.
        """
        self.assertEqual(biome_config.SILENCED, "off")

    def test_an_unclaimed_framework_rule_is_switched_off(self) -> None:
        """The #720 case, in miniature."""
        block, silenced = self.build(set(), {"noReactNativeRawText": "reactNative"})
        self.assertEqual(block["noReactNativeRawText"], "off")
        self.assertEqual(silenced, 1)

    def test_a_claimed_framework_rule_stays_on(self) -> None:
        """A React repo keeps react's nursery rules."""
        block, silenced = self.build({"react"}, {"noJsxLeakedDollar": "react"})
        self.assertEqual(block["noJsxLeakedDollar"], "on")
        self.assertEqual(silenced, 0)

    def test_a_rule_with_no_domain_stays_on_everywhere(self) -> None:
        """31 of the 87 are not statements about what a repo is."""
        block, silenced = self.build(set(), {"useExplicitType": "-"})
        self.assertEqual(block["useExplicitType"], "on")
        self.assertEqual(silenced, 0)

    def test_org_fixed_domains_are_never_silenced(self) -> None:
        """project, types and test are the org's level, not repo identity."""
        for domain in DELIVERED.org:
            with self.subTest(domain=domain):
                block, silenced = self.build(set(), {"aRule": domain})
                self.assertEqual(block["aRule"], "on")
                self.assertEqual(silenced, 0)

    def test_a_rule_missing_from_the_table_stays_on(self) -> None:
        """Fail OPEN here: the gate's own guard is what catches an omission."""
        block, silenced = self.build(set(), {})
        self.assertEqual(block, {})
        self.assertEqual(silenced, 0)

    def test_the_delivered_tables_silence_the_measured_counts(self) -> None:
        """34 for a repo claiming nothing, 28 for one claiming react."""
        org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        _config, none = biome_config.generate(org, set(), DELIVERED, NURSERY)
        _config, react = biome_config.generate(org, {"react"}, DELIVERED, NURSERY)
        self.assertEqual(none, 34)
        self.assertEqual(react, 28)
        # The difference is exactly react's own nursery rules, which is
        # what claiming a domain buys back.
        react_rules = sum(1 for d in NURSERY.values() if d == "react")
        self.assertEqual(none - react, react_rules)


class RunTest(unittest.TestCase):
    """End to end, through the same entry point the belt task calls."""

    @staticmethod
    def invoke(body: object | None, manifests: dict[str, object]) -> tuple:
        """Run the generator in a temporary tree.

        Returns:
            Exit status, stdout, stderr and the generated config path.

        """
        tree = Path(tempfile.mkdtemp())
        declared = tree / "biome.json"
        if body is not None:
            declared.write_text(declaration(body), encoding="utf-8")
        paths = []
        for name, content in manifests.items():
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content), encoding="utf-8")
            paths.append(path)
        out = tree / "generated.json"

        class Args:
            org = ORG_FILE
            domains = DOMAINS_FILE
            nursery = NURSERY_FILE

        args = Args()
        args.out = out
        args.declaration = declared
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = biome_config.run(args, paths)
        return status, stdout.getvalue(), stderr.getvalue(), out

    def test_a_conforming_react_repository_generates(self) -> None:
        """The MA shape: one claim, one matching dependency."""
        status, out, _err, path = self.invoke(
            {"linter": {"domains": {"react": "all"}}},
            {"apps/web/package.json": {"dependencies": {"react": "19.2.8"}}},
        )
        self.assertEqual(status, 0)
        self.assertIn("react", out)
        written = json.loads(path.read_text(encoding="utf-8"))["linter"]["domains"]
        self.assertEqual(written["react"], "all")
        self.assertEqual(written["qwik"], "none")

    def test_a_repository_with_no_declaration_and_no_framework_generates(self) -> None:
        """The canon itself must keep working with no biome.json at all."""
        status, _out, _err, path = self.invoke(None, {})
        self.assertEqual(status, 0)
        self.assertTrue(path.is_file())

    def test_an_undeclared_framework_fails_with_the_manifest_named(self) -> None:
        """Lowering by silence, refused, with the evidence in the message."""
        status, _out, err, path = self.invoke(
            None,
            {"apps/web/package.json": {"dependencies": {"react": "19.2.8"}}},
        )
        self.assertEqual(status, 1)
        self.assertIn("react", err)
        self.assertIn("apps/web/package.json", err)
        self.assertFalse(path.exists())

    def test_a_declaration_carrying_rules_fails(self) -> None:
        """No config is written when the declaration is refused."""
        status, _out, err, path = self.invoke(
            {"linter": {"rules": {"correctness": {"noNodejsModules": "off"}}}},
            {},
        )
        self.assertEqual(status, 1)
        self.assertIn("linter.rules", err)
        self.assertFalse(path.exists())

    def test_invalid_json_fails_without_a_traceback(self) -> None:
        """A hand-edited file that does not parse gets a sentence, not a stack."""
        tree = Path(tempfile.mkdtemp())
        declared = tree / "biome.json"
        declared.write_text("{", encoding="utf-8")

        class Args:
            org = ORG_FILE
            domains = DOMAINS_FILE
            nursery = NURSERY_FILE

        args = Args()
        args.out = tree / "generated.json"
        args.declaration = declared
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = biome_config.run(args, [])
        self.assertEqual(status, 1)
        self.assertIn("not valid JSON", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

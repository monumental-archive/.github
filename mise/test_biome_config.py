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

_SPEC = importlib.util.spec_from_file_location(
    "biome_config",
    Path(__file__).with_name("biome-config.py"),
)
biome_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(biome_config)

BELT = Path(__file__).parent
DOMAINS_FILE = BELT / "biome-domains.tsv"
ORG_FILE = BELT / "biome-org.json"

DELIVERED = biome_config.read_domains(DOMAINS_FILE)


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
        """Generate against the real org config and the real table."""
        self.org = json.loads(ORG_FILE.read_text(encoding="utf-8"))
        self.config = biome_config.generate(self.org, {"react"}, DELIVERED)
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
        """The merge adds identity; it may not edit a single rule."""
        self.assertEqual(self.config["linter"]["rules"], self.org["linter"]["rules"])
        self.assertEqual(self.config["assist"], self.org["assist"])

    def test_the_org_config_is_not_mutated_in_place(self) -> None:
        """Two runs in one process must not accumulate."""
        self.assertNotIn("domains", self.org.get("linter", {}))

    def test_a_repository_claiming_nothing_gets_every_identity_off(self) -> None:
        """The canon's own case: no framework, no framework rules."""
        written = biome_config.generate(self.org, set(), DELIVERED)["linter"]["domains"]
        self.assertEqual(
            {d for d, v in written.items() if v == "all"},
            set(DELIVERED.org),
        )


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

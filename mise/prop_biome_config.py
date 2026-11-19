#!/usr/bin/env python3
"""biome-config.py's identity merge, on declarations nobody wrote down.

The properties of `test_biome_config.py`'s fixed rows (#1548), over
generated repository declarations, manifests and nursery tables:

- Refusal (R): any JSON document as a repository's `biome.json` is
  judged with a list of problems, never a traceback; it is admissible
  exactly when it claims identity domains at "all" and says nothing
  else, in any key order.
- Invariance (V): a domain is reported missing exactly when it is not
  claimed and one of its trigger packages is declared, in whichever
  manifest and section.
- Idempotence (I): generating over a generated config changes nothing,
  and the org's own rules are never mutated.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from org_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "biome_config",
    Path(__file__).with_name("biome-config.py"),
)
bc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bc)

DOMAINS = bc.Domains(
    identity={"react": ["react", "react-dom"], "vue": ["vue"], "qwik": []},
    org=["project", "types", "test"],
)
DOMAIN = st.sampled_from([*DOMAINS.identity, *DOMAINS.org, "nope"])
JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=6),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=6), inner, max_size=3)
    ),
    max_leaves=10,
)
CLAIMED = st.sets(st.sampled_from(sorted(DOMAINS.identity)))
RULE = st.from_regex(r"use[A-Z][a-z]{1,6}", fullmatch=True)
NURSERY = st.dictionaries(RULE, st.sampled_from([*DOMAINS.identity, *DOMAINS.org, "-"]))


@st.composite
def admissible(draw: st.DrawFn) -> dict[str, object]:
    """Draw a declaration a repository may write, keys in any order.

    Returns:
        The document.

    """
    domains = dict.fromkeys(draw(st.permutations(sorted(draw(CLAIMED)))), "all")
    items = [("linter", {"domains": domains})]
    if draw(st.booleans()):
        items.append(("$schema", "https://biomejs.dev/schemas/x/schema.json"))
    return dict(draw(st.permutations(items)))


class Declaration(unittest.TestCase):
    """A repository states its domains and nothing else."""

    @given(admissible())
    def test_admissible_has_no_problem(self, document: dict) -> None:
        """Identity domains at "all", any order: nothing to say."""
        self.assertEqual(bc.declaration_problems(document, DOMAINS.identity), [])

    @given(admissible(), DOMAIN, st.one_of(st.just("all"), JSON))
    def test_anything_more_is_a_problem(
        self, document: dict, domain: str, value: object
    ) -> None:
        """A non-identity domain, or a value other than "all", is named."""
        document["linter"]["domains"][domain] = value
        problems = bc.declaration_problems(document, DOMAINS.identity)
        allowed = domain in DOMAINS.identity and value == "all"
        self.assertEqual(problems == [], allowed, problems)

    @given(JSON)
    @example({"linter": []})
    @example({"linter": {"domains": "x"}})
    def test_any_json_is_judged(self, document: object) -> None:
        """A list of problems, never a traceback."""
        self.assertIsInstance(bc.declaration_problems(document, DOMAINS.identity), list)


class Omissions(unittest.TestCase):
    """A domain the repository has must be claimed."""

    @given(
        CLAIMED,
        st.dictionaries(
            st.sampled_from(["react", "react-dom", "vue", "left-pad"]),
            st.lists(st.sampled_from(["a/package.json", "package.json"]), min_size=1),
        ),
    )
    def test_missing_exactly_when_declared_and_unclaimed(
        self, claimed: set[str], packages: dict[str, list[str]]
    ) -> None:
        """Each unclaimed domain with a declared trigger, once."""
        found = bc.omissions(claimed, DOMAINS.identity, packages)
        expected = {
            d
            for d, triggers in DOMAINS.identity.items()
            if d not in claimed and any(t in packages for t in triggers)
        }
        self.assertEqual({line.split('"')[1] for line in found}, expected)


class Generate(unittest.TestCase):
    """The generated config is a fixed point, and the org's rules stay."""

    @given(CLAIMED, NURSERY)
    def test_twice_is_once(self, claimed: set[str], nursery: dict[str, str]) -> None:
        """Generating over the output changes nothing; org untouched."""
        org = {"linter": {"rules": {"nursery": dict.fromkeys(nursery, "on")}}}
        before = copy.deepcopy(org)
        once, _ = bc.generate(org, claimed, DOMAINS, nursery)
        twice, _ = bc.generate(once, claimed, DOMAINS, nursery)
        self.assertEqual(org, before)
        self.assertEqual(
            json.dumps(twice, sort_keys=True), json.dumps(once, sort_keys=True)
        )
        for rule, domain in nursery.items():
            off = domain in DOMAINS.identity and domain not in claimed
            self.assertEqual(
                once["linter"]["rules"]["nursery"][rule], "off" if off else "on"
            )
        written = once["linter"]["domains"]
        for domain in DOMAINS.identity:
            self.assertEqual(written[domain], "all" if domain in claimed else "none")
        for domain in DOMAINS.org:
            self.assertEqual(written[domain], "all")


if __name__ == "__main__":
    unittest.main()

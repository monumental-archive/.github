#!/usr/bin/env python3
"""biome_config.py's identity merge, on declarations nobody wrote down.

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
import json
import unittest

import biome_config as bc
import decoded
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

DOMAINS = bc.Domains(
    identity={"react": ["react", "react-dom"], "vue": ["vue"], "qwik": []},
    org=["project", "types", "test"],
)
DOMAIN = st.sampled_from([*DOMAINS.identity, *DOMAINS.org, "nope"])
SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=6)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=6), inner, max_size=3
    )
    return arrays | objects


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=10)
VALUE: st.SearchStrategy[object] = st.one_of(st.just("all"), JSON)
# A linter that is a list, and domains that are a string.
LINTER_LIST: dict[str, object] = {"linter": []}
DOMAINS_STRING: dict[str, object] = {"linter": {"domains": "x"}}
CLAIMED = st.sets(st.sampled_from(sorted(DOMAINS.identity)))
RULE = st.from_regex(r"use[A-Z][a-z]{1,6}", fullmatch=True)
NURSERY = st.dictionaries(RULE, st.sampled_from([*DOMAINS.identity, *DOMAINS.org, "-"]))


def table(document: dict[str, object], *path: str) -> dict[str, object]:
    """Walk a document down a path of keys, each an object.

    Returns:
        The object at the end of the path.

    """
    node = document
    for key in path:
        node = decoded.as_object(node[key], key)
    return node


def draw_admissible(draw: st.DrawFn) -> dict[str, object]:
    """Draw a declaration a repository may write, keys in any order.

    Returns:
        The document.

    """
    domains = dict.fromkeys(draw(st.permutations(sorted(draw(CLAIMED)))), "all")
    items: list[tuple[str, object]] = [("linter", {"domains": domains})]
    if draw(st.booleans()):
        items.append(("$schema", "https://biomejs.dev/schemas/x/schema.json"))
    return dict(draw(st.permutations(items)))


admissible = st.composite(draw_admissible)


class Declaration(unittest.TestCase):
    """A repository states its domains and nothing else."""

    @given(admissible())
    def test_admissible_has_no_problem(self, document: dict[str, object]) -> None:
        """Identity domains at "all", any order: nothing to say."""
        problems = bc.declaration_problems(document, DOMAINS.identity)
        self.assertEqual(problems, list[str]())

    @given(admissible(), DOMAIN, VALUE)
    def test_anything_more_is_a_problem(
        self, document: dict[str, object], domain: str, value: object
    ) -> None:
        """A non-identity domain, or a value other than "all", is named."""
        table(document, "linter", "domains")[domain] = value
        problems = bc.declaration_problems(document, DOMAINS.identity)
        allowed = domain in DOMAINS.identity and value == "all"
        self.assertEqual(problems == [], allowed, problems)

    @given(JSON)
    @example(LINTER_LIST)
    @example(DOMAINS_STRING)
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
        named = {line.split('"')[1] for line in found}
        self.assertEqual(named, expected)


class Generate(unittest.TestCase):
    """The generated config is a fixed point, and the org's rules stay."""

    @given(CLAIMED, NURSERY)
    def test_twice_is_once(self, claimed: set[str], nursery: dict[str, str]) -> None:
        """Generating over the output changes nothing; org untouched."""
        org: dict[str, object] = {
            "linter": {"rules": {"nursery": dict.fromkeys(nursery, "on")}}
        }
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
                table(once, "linter", "rules", "nursery")[rule], "off" if off else "on"
            )
        written = table(once, "linter", "domains")
        for domain in DOMAINS.identity:
            self.assertEqual(written[domain], "all" if domain in claimed else "none")
        for domain in DOMAINS.org:
            self.assertEqual(written[domain], "all")


if __name__ == "__main__":
    unittest.main()

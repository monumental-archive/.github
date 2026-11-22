#!/usr/bin/env python3
"""zizmor_signer.py's exemption, on findings nobody wrote down.

The properties of `test_zizmor_signer.py`'s fixed rows (#1548), over
generated zizmor findings:

- Invariance (V): a finding is dropped exactly when its audit is
  exempt and every reference it carries, in the location kind that
  audit uses, is in the signer's repository, however the owner and
  repository are cased; the order of the findings never changes how
  many are dropped or kept.
- Refusal (R): whatever zizmor writes to stdout, `main` answers 0,
  zizmor's status, or its own refusal 1, and never raises (#1555: a
  finding whose ident, locations or location fields were the wrong
  type raised). A kept finding drawn here carries no record's fields,
  so it is refused: 1.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import json
import os
import unittest
from unittest import mock

import zizmor_signer as zs
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

SIGNER = "Org/Signer"
WORKFLOW = f"{SIGNER}/.github/workflows/sign.yml"
REPO = st.sampled_from([
    "Org/Signer",
    "org/signer",
    "ORG/SIGNER",
    "org/other",
    "else/signer",
])
SHA = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
IDENT = st.sampled_from([*zs.AUDITS, "unpinned-uses", "excessive-permissions"])


Drawn = tuple[dict[str, object], bool]


def draw_finding(draw: st.DrawFn) -> Drawn:
    """Draw a finding and whether it is on the signer.

    Returns:
        The finding and the verdict its construction implies.

    """
    ident = draw(IDENT)
    kind = zs.AUDITS[ident][0] if ident in zs.AUDITS else "Primary"
    repos = draw(st.lists(REPO, min_size=1, max_size=3))
    locations: list[dict[str, object]] = []
    for repo in repos:
        ref = f"{repo}/.github/workflows/x.yml@{draw(SHA)}"
        feature = draw(
            st.sampled_from([ref, f"- uses: {ref} # main", f"uses: '{ref}'"])
        )
        locations.append({"symbolic": {"kind": kind}, "concrete": {"feature": feature}})
    if draw(st.booleans()):
        other = "Hidden" if kind == "Primary" else "Primary"
        locations.append({
            "symbolic": {"kind": other},
            "concrete": {"feature": "else/where@main"},
        })
    on = ident in zs.AUDITS and all(r.lower() == SIGNER.lower() for r in repos)
    return {"ident": ident, "locations": locations}, on


finding = st.composite(draw_finding)


class Exemption(unittest.TestCase):
    """Dropped exactly when every reference is the signer's."""

    @given(finding())
    def test_on_signer_exactly_when_every_reference_is(self, drawn: Drawn) -> None:
        """Case of owner and repository does not matter."""
        found, on = drawn
        signer = zs.signer_repository(WORKFLOW)
        self.assertEqual(zs.on_signer(zs.Finding.of(found), signer), on, found)

    @given(st.data(), st.lists(finding(), max_size=6))
    def test_order_does_not_matter(
        self, data: st.DataObject, drawn: list[Drawn]
    ) -> None:
        """The same findings in any order: same status, same counts."""
        findings = [f for f, _ in drawn]
        shuffled = data.draw(st.permutations(findings))
        self.assertEqual(run(json.dumps(findings)), run(json.dumps(shuffled)))
        status, _ = run(json.dumps(findings))
        self.assertEqual(status, 0 if all(on for _, on in drawn) else 1)


def run(stdout: str) -> tuple[int, str]:
    """Run main with zizmor answering `stdout` and its findings status, 14.

    Returns:
        The status and stdout.

    """
    out = io.StringIO()
    ran = zs.Ran(14, stdout.encode(), b"")
    env = {"GH_TOKEN": "a token", "MISE_TASK_NAME": "audit:actions", "ORG_FINDINGS": ""}
    with (
        mock.patch.object(zs, "run_zizmor", return_value=ran),
        mock.patch.dict(os.environ, env),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        status = zs.main(["--signer-workflow", WORKFLOW])
    return status, out.getvalue()


SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=10)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=4)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=10), inner, max_size=4
    )
    return arrays | objects


def dumped(value: object) -> str:
    """Serialise a value as JSON.

    Returns:
        The document.

    """
    return json.dumps(value)


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=15)
SYMBOLIC: st.SearchStrategy[object] = st.one_of(
    JSON, st.fixed_dictionaries({"kind": JSON})
)
CONCRETE: st.SearchStrategy[object] = st.one_of(
    JSON, st.fixed_dictionaries({"feature": JSON})
)
LOCATION: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries(
    {}, optional={"symbolic": SYMBOLIC, "concrete": CONCRETE}
)
LOCATIONS: st.SearchStrategy[object] = st.one_of(JSON, st.lists(LOCATION, max_size=3))
ANY_IDENT: st.SearchStrategy[object] = st.one_of(IDENT, JSON)
SHAPED: st.SearchStrategy[list[dict[str, object]]] = st.lists(
    st.fixed_dictionaries(
        {"ident": ANY_IDENT}, optional={"locations": LOCATIONS, "ignored": JSON}
    ),
    max_size=3,
)


class Refusal(unittest.TestCase):
    """Any output of zizmor is answered, never a traceback."""

    @given(st.one_of(JSON.map(dumped), SHAPED.map(dumped), st.text(max_size=40)))
    @example('[{"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]}]')
    @example('[{"ident": []}]')
    def test_main_answers_any_output(self, stdout: str) -> None:
        """0, 1 or 14."""
        status, _ = run(stdout)
        self.assertIn(status, (0, 1, 14))


if __name__ == "__main__":
    unittest.main()

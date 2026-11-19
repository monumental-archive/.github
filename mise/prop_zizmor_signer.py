#!/usr/bin/env python3
"""zizmor_signer.py's exemption, on findings nobody wrote down.

The properties of `test_zizmor_signer.py`'s fixed rows (#1548), over
generated zizmor findings:

- Invariance (V): a finding is dropped exactly when its audit is
  exempt and every reference it carries, in the location kind that
  audit uses, is in the signer's repository, however the owner and
  repository are cased; the order of the findings never changes how
  many are dropped or kept.
- Refusal (R): whatever JSON arrives on stdin, `main` answers 0, 1 or
  its own refusal 2, and never raises (#1555: a finding whose ident,
  locations or location fields were the wrong type raised).

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import json
import sys
import unittest

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


@st.composite
def finding(draw: st.DrawFn) -> tuple[dict[str, object], bool]:
    """Draw a finding and whether it is on the signer.

    Returns:
        The finding and the verdict its construction implies.

    """
    ident = draw(IDENT)
    kind = zs.AUDITS[ident][0] if ident in zs.AUDITS else "Primary"
    repos = draw(st.lists(REPO, min_size=1, max_size=3))
    locations = []
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


class Exemption(unittest.TestCase):
    """Dropped exactly when every reference is the signer's."""

    @given(finding())
    def test_on_signer_exactly_when_every_reference_is(
        self, drawn: tuple[dict, bool]
    ) -> None:
        """Case of owner and repository does not matter."""
        found, on = drawn
        self.assertEqual(zs.on_signer(found, zs.signer_repository(WORKFLOW)), on, found)

    @given(st.data(), st.lists(finding(), max_size=6))
    def test_order_does_not_matter(
        self, data: st.DataObject, drawn: list[tuple[dict, bool]]
    ) -> None:
        """The same findings in any order: same status, same counts."""
        findings = [f for f, _ in drawn]
        shuffled = data.draw(st.permutations(findings))
        self.assertEqual(run(json.dumps(findings)), run(json.dumps(shuffled)))
        status, _ = run(json.dumps(findings))
        self.assertEqual(status, 0 if all(on for _, on in drawn) else 1)


def run(stdin: str) -> tuple[int, str]:
    """Run main on a stdin.

    Returns:
        The status and stdout.

    """
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            status = zs.main(["--signer-workflow", WORKFLOW])
    finally:
        sys.stdin = old
    return status, out.getvalue()


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=10),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(max_size=10), inner, max_size=4)
    ),
    max_leaves=15,
)
SHAPED = st.lists(
    st.fixed_dictionaries(
        {"ident": st.one_of(IDENT, JSON)},
        optional={
            "locations": st.one_of(
                JSON,
                st.lists(
                    st.fixed_dictionaries(
                        {},
                        optional={
                            "symbolic": st.one_of(
                                JSON, st.fixed_dictionaries({"kind": JSON})
                            ),
                            "concrete": st.one_of(
                                JSON, st.fixed_dictionaries({"feature": JSON})
                            ),
                        },
                    ),
                    max_size=3,
                ),
            ),
            "ignored": JSON,
        },
    ),
    max_size=3,
)


class Refusal(unittest.TestCase):
    """Any stdin is answered, never a traceback."""

    @given(
        st.one_of(JSON.map(json.dumps), SHAPED.map(json.dumps), st.text(max_size=40))
    )
    @example('[{"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]}]')
    @example('[{"ident": []}]')
    def test_main_answers_any_stdin(self, stdin: str) -> None:
        """0, 1 or 2."""
        status, _ = run(stdin)
        self.assertIn(status, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()

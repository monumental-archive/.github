#!/usr/bin/env python3
"""plan-producers.py's join, on policies and workflows nobody wrote down.

The properties of `test_plan_producers.py`'s fixed rows (#1548), over
generated planned obligations and the workflows that produce them:

- Invariance (V): stripping the markers keeps every other non-blank
  line, in order, and leaves no marker.
- Invariance (V): with every obligation produced (a marker beside the
  class name, and a prefix the file emits under) the join is clean;
  taking one producer away is a finding naming exactly that
  obligation, in whichever file and order the workflows come.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "plan_producers",
    Path(__file__).with_name("plan-producers.py"),
)
pp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pp)

CLASS = st.from_regex(r"[a-z]{3,6}-[a-z]{3,6}", fullmatch=True)
PREFIX = st.from_regex(r"sbom-[a-z]{2,6}-", fullmatch=True)


@st.composite
def obligations(draw: st.DrawFn) -> list[tuple[str, str]]:
    """Draw planned (class, prefix) pairs, one prefix each.

    Returns:
        The pairs.

    """
    classes = draw(st.lists(CLASS, unique=True, min_size=1, max_size=4))
    prefixes = draw(
        st.lists(PREFIX, unique=True, min_size=len(classes), max_size=len(classes))
    )
    return list(zip(classes, prefixes, strict=True))


def policy(pairs: list[tuple[str, str]]) -> dict:
    """Write the assert policy declaring the pairs as planned.

    Returns:
        The decoded policy.

    """
    return {
        "evidence": {
            "classes": {
                klass: {"assetPrefixes": [{"prefix": prefix, "planned": True}]}
                for klass, prefix in pairs
            }
        }
    }


def producer(draw: st.DrawFn, klass: str, prefix: str) -> str:
    """Write one workflow that produces an obligation.

    Returns:
        The workflow text.

    """
    emit = draw(
        st.sampled_from([
            f"      PLAN_DOC_PREFIX: {prefix}",
            f"      run: jq -n '{{doc: (\"{prefix}\" + $v)}}'",
        ])
    )
    return (
        "\n".join([
            draw(st.sampled_from(["", "# a workflow"])),
            f"# plan-producer: {klass} {prefix}",
            f"name: build {klass}",
            "    env:",
            emit,
            f"      - name: sbom-plan-{klass}",
        ])
        + "\n"
    )


class Join(unittest.TestCase):
    """Every obligation produced, or each missing one named."""

    @given(st.data(), obligations())
    def test_clean_then_one_missing(
        self, data: st.DataObject, pairs: list[tuple[str, str]]
    ) -> None:
        """Clean with all producers; one finding per producer removed."""
        files = {
            f"w{i}.yml": producer(data.draw, k, p) for i, (k, p) in enumerate(pairs)
        }
        order = data.draw(st.permutations(sorted(files)))
        files = {name: files[name] for name in order}
        self.assertEqual(pp.judge(policy(pairs), files), [])
        gone = data.draw(st.integers(0, len(pairs) - 1))
        del files[f"w{gone}.yml"]
        found = pp.judge(policy(pairs), files)
        klass, prefix = pairs[gone]
        self.assertEqual(len(found), 1, found)
        self.assertIn(
            f"class {klass!r} declares the planned obligation {prefix!r}", found[0]
        )


class StripMarkers(unittest.TestCase):
    """A marker cannot be its own evidence."""

    @given(
        st.lists(
            st.one_of(
                st.text(
                    st.characters(blacklist_categories=("Cc", "Zl", "Zp", "Cs")),
                    max_size=20,
                ),
                st.sampled_from([
                    "# plan-producer: a b",
                    "  #plan-producer:x",
                    "#plan-producer",
                ]),
            ),
            max_size=8,
        )
    )
    def test_every_other_line_kept(self, lines: list[str]) -> None:
        """The non-marker lines, in order, and no marker."""
        text = "\n".join(lines)
        kept = [line for line in text.splitlines() if not pp.MARKER.match(line)]
        got = [line for line in pp.strip_markers(text).splitlines() if line]
        self.assertEqual(got, [line for line in kept if line])


if __name__ == "__main__":
    unittest.main()

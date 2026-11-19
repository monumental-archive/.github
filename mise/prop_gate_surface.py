#!/usr/bin/env python3
"""gate-surface.py's derivation, on workspaces and stubs nobody wrote down.

The properties of `test_gate_surface.py`'s fixed rows (#1548), over
generated cargo packages and publish stubs:

- Invariance (V): a package's majors are its `pgNN` features, sorted,
  exactly when it depends on pgrx, whatever order its features and
  dependencies come in.
- Invariance (V): a stub's inputs read the same whatever comments,
  blank lines and quoting surround them; the crates the publish path
  builds do not depend on the order or spacing of `classes` and
  `exclude`, and an excluded crate is never built by a workspace class.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "gate_surface",
    Path(__file__).with_name("gate-surface.py"),
)
gs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gs)

NAME = st.from_regex(r"[a-z][a-z0-9_-]{0,8}", fullmatch=True)
FEATURE = st.one_of(
    st.integers(10, 30).map("pg{}".format),
    st.sampled_from(["default", "pg_test", "pgx", "pg", "cshim"]),
)
CLASS = st.sampled_from([
    "rust-crate",
    "rust-binary",
    "pgrx-extension",
    "wasm-npm",
    "oci-image",
])


class Majors(unittest.TestCase):
    """pgNN features of a pgrx dependent, and nothing else."""

    @given(
        st.data(),
        st.lists(FEATURE, unique=True, max_size=6),
        st.lists(NAME, max_size=3),
        st.sampled_from(["pgrx", "plain"]),
    )
    def test_majors_are_the_pg_features(
        self, data: st.DataObject, features: list[str], deps: list[str], kind: str
    ) -> None:
        """Sorted majors, in any feature or dependency order."""
        pgrx = kind == "pgrx"
        names = [*deps, "pgrx"] if pgrx else [d for d in deps if d != "pgrx"]
        pkg = {
            "features": {f: [] for f in data.draw(st.permutations(features))},
            "dependencies": [{"name": n} for n in data.draw(st.permutations(names))],
        }
        expected = sorted(
            int(f[2:]) for f in features if f[2:].isdigit() and f.startswith("pg")
        )
        self.assertEqual(gs.majors(pkg), expected if pgrx else [])


@st.composite
def stub(draw: st.DrawFn) -> tuple[str, dict[str, str]]:
    """Write a publish stub's `with:` block among other text.

    Returns:
        The stub and the inputs it states.

    """
    keys = draw(
        st.dictionaries(
            st.sampled_from(["classes", "exclude", "pg-majors", "crate-dir"]),
            NAME,
            min_size=1,
        )
    )
    lines = ["jobs:", "  publish:", "    uses: org/.github/x.yml@abc # v1"]
    lines.append("    with:" + draw(st.sampled_from(["", "  # inputs"])))
    for key, value in keys.items():
        lines.append(draw(st.sampled_from(["", "      # a note"])))
        quote = draw(st.sampled_from(["", "'", '"']))
        lines.append(
            f"      {key}: {quote}{value}{quote}"
            + draw(st.sampled_from(["", "  # why"]))
        )
    lines.append("    secrets: inherit")
    return "\n".join(lines) + "\n", keys


class Stub(unittest.TestCase):
    """The stub's inputs, as written."""

    @given(stub())
    def test_inputs_read_back(self, drawn: tuple[str, dict[str, str]]) -> None:
        """Comments, blanks and quotes change nothing."""
        text, keys = drawn
        self.assertEqual(gs.stub_inputs(text), (keys, []))

    @given(
        st.data(),
        st.lists(CLASS, unique=True, min_size=1),
        st.lists(
            st.fixed_dictionaries({
                "name": NAME,
                "dir": st.sampled_from(["", "a", "b/c"]),
            }),
            max_size=5,
        ),
        st.lists(NAME, max_size=3),
    )
    def test_published_does_not_depend_on_spelling(
        self,
        data: st.DataObject,
        classes: list[str],
        surface: list[dict],
        excluded: list[str],
    ) -> None:
        """Order and spaces of the lists change nothing; exclusions hold."""

        def spell(items: list[str]) -> str:
            return ",".join(f" {i} " for i in data.draw(st.permutations(items)))

        one = gs.published(
            {
                "classes": ",".join(classes),
                "exclude": ",".join(excluded),
                "crate-dir": "a",
            },
            surface,
        )
        two = gs.published(
            {"classes": spell(classes), "exclude": spell(excluded), "crate-dir": "a/"},
            surface,
        )
        self.assertEqual(one, two)
        if not set(classes) & {"pgrx-extension", "wasm-npm"}:
            self.assertFalse(one & set(excluded))


if __name__ == "__main__":
    unittest.main()

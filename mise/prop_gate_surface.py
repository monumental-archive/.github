#!/usr/bin/env python3
"""gate_surface.py's derivation, on workspaces and stubs nobody wrote down.

The properties of `test_gate_surface.py`'s fixed rows (#1548), over
generated cargo packages and publish stubs:

- Invariance (V): a package's majors are its `pgNN` features, sorted,
  exactly when it depends on pgrx, whatever order its features and
  dependencies come in.
- Invariance (V): a stub's inputs read the same whatever comments,
  blank lines and quoting surround them; the crates the publish path
  builds do not depend on the order or spacing of `classes` and
  `exclude`, and an excluded crate is never built by a workspace class.
- Refusal (R): whatever JSON cargo metadata hands over, `main` prints
  the surface (0) or refuses by name (1), never a traceback (#1555: a
  non-object document, or a package or field of the wrong type,
  raised).

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import gate_surface as gs
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:gate-surface"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    patch = mock.patch.dict(os.environ, {"MISE_TASK_NAME": TASK, "ORG_FINDINGS": ""})
    patch.start()
    unittest.addModuleCleanup(patch.stop)


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
        declared: dict[str, list[str]] = {
            f: [] for f in data.draw(st.permutations(features))
        }
        pkg: dict[str, object] = {
            "features": declared,
            "dependencies": [{"name": n} for n in data.draw(st.permutations(names))],
        }
        expected = sorted(
            int(f[2:]) for f in features if f[2:].isdigit() and f.startswith("pg")
        )
        self.assertEqual(gs.majors(gs.package(pkg)), expected if pgrx else list[int]())


def draw_stub(draw: st.DrawFn) -> tuple[str, dict[str, str]]:
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


stub = st.composite(draw_stub)


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
        records: list[dict[str, str]],
        excluded: list[str],
    ) -> None:
        """Order and spaces of the lists change nothing; exclusions hold."""
        surface = [gs.Crate(r["name"], r["name"], (), r["dir"]) for r in records]

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


SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=8)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=8), inner, max_size=3
    )
    return arrays | objects


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=10)
PACKAGE: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries(
    {},
    optional={
        "id": st.one_of(NAME, JSON),
        "name": st.one_of(NAME, JSON),
        "version": st.one_of(st.just("0.1.0"), JSON),
        "features": st.one_of(st.dictionaries(FEATURE, st.just([])), JSON),
        "dependencies": st.one_of(
            st.lists(st.fixed_dictionaries({"name": NAME})), JSON
        ),
        "manifest_path": st.one_of(st.just("/ws/a/Cargo.toml"), JSON),
    },
)
METADATA: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries(
    {},
    optional={
        "workspace_root": st.one_of(st.just("/ws"), JSON),
        "workspace_members": st.one_of(st.lists(st.one_of(NAME, JSON)), JSON),
        "packages": st.one_of(st.lists(st.one_of(PACKAGE, JSON), max_size=3), JSON),
    },
)


DOCUMENT: st.SearchStrategy[object] = st.one_of(JSON, METADATA)
# An array for a document, and a package that is not an object.
NO_DOCUMENT: list[object] = []
NUMBER_PACKAGE: dict[str, object] = {"packages": [1], "workspace_members": []}


class Refusal(unittest.TestCase):
    """Any metadata document: the surface, or a refusal by name."""

    @given(DOCUMENT)
    @example(NO_DOCUMENT)
    @example(NUMBER_PACKAGE)
    def test_main_answers_any_document(self, doc: object) -> None:
        """0, or 1 with the reason on stderr."""
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(doc))
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = gs.main([])
        finally:
            sys.stdin = old
        self.assertIn(status, (0, 1))
        if status:
            self.assertIn(f"{TASK}: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()

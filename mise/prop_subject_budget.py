#!/usr/bin/env python3
"""subject-budget.py's globs and templates, on inputs nobody wrote down.

The properties of `test_subject_budget.py`'s fixed rows (#1332), over
generated dependency names, paths and commit templates:

- `glob_matches`: a path matches every pattern made from it by turning
  slash-free stretches into `*`, a leading run of directories into
  `**/`, or nothing at all (a literal), whatever regex metacharacters
  the path carries; and with no `**` a pattern never matches a path
  deeper than itself, because a single `*` does not cross a slash.
- `render`: the subject grows by exactly one column per `newValue`
  placeholder for each column of version width, carries the dependency
  name wherever the template asks for it, and reports a template as
  unmodelled exactly when a placeholder it cannot resolve is left.
- Refusal (R): the dependency readers (`from_cargo`, `from_npm`,
  `from_gomod`, `from_actions`, `from_mise`) read any bytes, and any
  well-formed document of any shape, and answer with dependencies and
  unmodelled lines, never a traceback (#1555). A table of the wrong
  type, a non-object package.json and a file that is not UTF-8 all
  raised until this found them.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "subject_budget",
    Path(__file__).with_name("subject-budget.py"),
)
sb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sb)

# A path segment: never empty, never a slash or a glob character, and
# rich in what a regex would misread if the pattern were not escaped.
SEGMENT = st.text(alphabet="abz09.-_@+()[]{}^$|\\", min_size=1, max_size=8)
PATH = st.lists(SEGMENT, min_size=1, max_size=5)


@st.composite
def globbed(draw: st.DrawFn) -> tuple[str, str, bool]:
    """Draw a path and a pattern made from it.

    Returns:
        The path, the pattern, and whether the pattern uses `**`.

    """
    segments = draw(PATH)
    parts = []
    for segment in segments:
        how = draw(st.sampled_from(["keep", "star", "inner"]))
        if how == "star":
            parts.append("*")
        elif how == "inner":
            start = draw(st.integers(0, len(segment)))
            end = draw(st.integers(start, len(segment)))
            parts.append(f"{segment[:start]}*{segment[end:]}")
        else:
            parts.append(segment)
    deep = draw(st.integers(0, len(parts) - 1))
    double = draw(st.booleans())
    if double:
        parts = ["**", *parts[deep:]]
    return "/".join(segments), "/".join(parts), double


# A dependency name as the managers read them: backend prefixes,
# scopes and paths, but no template braces.
DEP = st.from_regex(r"[a-z][a-z0-9@/._:-]{0,30}", fullmatch=True)
LITERAL = st.text(alphabet="abc -_()[]:.", max_size=12)
TOKEN = st.sampled_from([
    "{{depName}}",
    "{{{depName}}}",
    "{{newValue}}",
    "{{{newValue}}}",
    "{{unknownField}}",
])
FIELD = st.lists(st.one_of(LITERAL, TOKEN), max_size=5).map("".join)


class GlobMatches(unittest.TestCase):
    """Renovate's globs, as `glob_matches` models them."""

    @given(globbed())
    @example(("a.b/c", "a.b/*", False))
    @example(("Cargo.toml", "**/Cargo.toml", True))
    def test_a_path_matches_its_own_patterns(
        self, drawn: tuple[str, str, bool]
    ) -> None:
        """Every pattern made from a path selects it."""
        path, pattern, _ = drawn
        self.assertTrue(sb.glob_matches(pattern, path), f"{pattern!r} vs {path!r}")

    @given(globbed(), SEGMENT)
    def test_a_star_never_crosses_a_slash(
        self, drawn: tuple[str, str, bool], extra: str
    ) -> None:
        """Without `**`, a pattern does not select a deeper path."""
        path, pattern, double = drawn
        if double or pattern == "*":
            return
        self.assertFalse(sb.glob_matches(pattern, f"{path}/{extra}"))
        self.assertFalse(sb.glob_matches(pattern, f"{extra}/{path}"))


class Render(unittest.TestCase):
    """The widest subject a commit template can mint."""

    @given(
        st.fixed_dictionaries({
            "semanticCommitType": LITERAL,
            "semanticCommitScope": LITERAL,
            **dict.fromkeys(sb.MESSAGE_FIELDS, FIELD),
        }),
        DEP,
        st.integers(1, 40),
    )
    def test_width_name_and_unmodelled(
        self, config: dict, dep: str, width: int
    ) -> None:
        """Growth per column, the name where asked, unmodelled when left."""
        fields = [config[k] for k in sb.MESSAGE_FIELDS]
        versions = sum(f.count("{{newValue}}") for f in fields)
        names = sum(f.count("{{depName}}") for f in fields)
        narrow, unmodelled = sb.render(config, dep, width)
        wide, _ = sb.render(config, dep, width + 1)
        self.assertEqual(len(wide) - len(narrow), versions)
        if names:
            self.assertIn(dep, narrow)
        left = any("{{unknownField}}" in f for f in fields)
        self.assertEqual(unmodelled is not None, left)
        self.assertTrue(narrow.endswith(sb.PR_TAIL))


SCALAR = st.one_of(st.booleans(), st.integers(-9, 9), st.text(max_size=8))
VALUE = st.recursive(
    SCALAR,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=8), inner, max_size=3)
    ),
    max_leaves=10,
)
MANIFEST_KEY = st.sampled_from([
    "dependencies",
    "dev-dependencies",
    "devDependencies",
    "workspace",
    "target",
    "tools",
    "x",
])


def toml(value: object) -> str:
    """Write a value as a TOML inline value.

    Returns:
        The TOML text.

    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        inner = ", ".join(f"{json.dumps(k)} = {toml(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    return json.dumps(value)


DOCUMENT = st.dictionaries(MANIFEST_KEY, VALUE, max_size=4)
READERS = {
    "Cargo.toml": "from_cargo",
    "package.json": "from_npm",
    "go.mod": "from_gomod",
    ".github/workflows/ci.yml": "from_actions",
    "mise.toml": "from_mise",
}


class Readers(unittest.TestCase):
    """Any manifest is read or recorded as unmodelled."""

    @given(
        st.sampled_from(sorted(READERS)),
        st.one_of(
            st.binary(max_size=80),
            DOCUMENT.map(lambda d: json.dumps(d).encode()),
            DOCUMENT.map(
                lambda d: "".join(
                    f"{json.dumps(k)} = {toml(v)}\n" for k, v in d.items()
                )
            ).map(str.encode),
            VALUE.map(lambda v: json.dumps(v).encode()),
        ),
    )
    @example("mise.toml", b'"tools"={""=false}')
    def test_any_manifest_is_answered(self, name: str, data: bytes) -> None:
        """Dependencies and report lines, never a traceback."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            report: list[str] = []
            deps = getattr(sb, READERS[name])([path], report)
        self.assertIsInstance(deps, list)
        for line in report:
            self.assertTrue(line.startswith(str(path)), line)


if __name__ == "__main__":
    unittest.main()

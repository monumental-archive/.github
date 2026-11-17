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

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

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


if __name__ == "__main__":
    unittest.main()

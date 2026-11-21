#!/usr/bin/env python3
"""rust_suppressions.py's findings, on sources and manifests nobody wrote down.

The properties of `test_rust_suppressions.py`'s fixed rows (#1857):

- Invariance (V): over a source drawn line by line from real inner
  attributes and from decoys (the text `#![allow(` inside a line
  comment, a doc comment, a block comment, a string, a raw string, a
  reason), `inner_allows` finds exactly the attributes that allow,
  plain or under `cfg_attr`, at their lines, and never an expect or a
  decoy.
- Invariance (V): over a manifest drawn from `[lints.*]` and
  `[workspace.lints.*]` tables with every level in both spellings,
  `manifest_allows` lists exactly the `allow` levels.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import decoded
import rust_suppressions as rsup
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

LINT = st.sampled_from(["dead_code", "clippy::unwrap_used", "unused", "x"])
LEVEL = st.sampled_from(["allow", "warn", "deny", "forbid"])
NAME = st.from_regex(r"[a-z][a-z_]{0,8}", fullmatch=True)
DECOYS = (
    "// #![allow({})]",
    "//! #![allow({})]",
    "/* #![allow({}) /* nested */ #![allow({})] */",
    'const S: &str = "#![allow({})] \\" #![allow({})]";',
    'const R: &str = r#"#![allow({})]"#;',
    '#![expect({}, reason = "never allow({}) here")]',
    "#![cfg_attr(test, expect({}))]",
    "#[allow({})]",
    "fn f<'a>(x: &'a str) -> char {{ '#' }}",
)
REAL = ("#![allow({})]", "#![cfg_attr(test, allow({}))]")
Line = tuple[str, bool]
Manifest = tuple[str, list[str]]


def draw_line(draw: st.DrawFn) -> Line:
    """Draw one source line: a real allow, or a decoy that is none.

    Returns:
        The line and whether it is an inner allow.

    """
    lint = draw(LINT)
    if draw(st.booleans()):
        return draw(st.sampled_from(REAL)).format(lint), True
    return draw(st.sampled_from(DECOYS)).format(lint, lint), False


def draw_manifest(draw: st.DrawFn) -> Manifest:
    """Draw a manifest's lint tables with every level in both forms.

    Returns:
        The TOML text and the findings expected, `[lints.*]` first.

    """
    text: list[str] = []
    expected: list[str] = []
    prefixes = draw(
        st.lists(st.sampled_from(["lints", "workspace.lints"]), unique=True)
    )
    # The helper reads `[lints.*]` before `[workspace.lints.*]`, whatever
    # order the document writes them in.
    for prefix in sorted(prefixes):
        for tool in draw(
            st.lists(st.sampled_from(["rust", "clippy", "rustdoc"]), unique=True)
        ):
            text.append(f"[{prefix}.{tool}]")
            for lint in draw(st.lists(NAME, max_size=4, unique=True)):
                level = draw(LEVEL)
                if draw(st.booleans()):
                    text.append(f'{lint} = "{level}"')
                else:
                    text.append(f'{lint} = {{ level = "{level}", priority = 1 }}')
                if level == "allow":
                    expected.append(f'[{prefix}.{tool}] {lint} = "allow"')
    return "\n".join(text) + "\n", expected


DECOY_THEN_REAL: list[Line] = [("// #![allow(x)]", False), ("#![allow(x)]", True)]
source = st.lists(st.composite(draw_line)(), max_size=12)
manifest = st.composite(draw_manifest)


class Findings(unittest.TestCase):
    """Exactly the suppressions clippy cannot see, nothing else."""

    @given(source)
    @example(DECOY_THEN_REAL)
    def test_every_inner_allow_and_no_decoy(self, lines: list[Line]) -> None:
        """Each real allow at its line; every decoy passed over."""
        text = "".join(f"{line}\n" for line, _ in lines)
        expected = [(n, line) for n, (line, real) in enumerate(lines, 1) if real]
        self.assertEqual(expected, rsup.inner_allows(text))

    @given(manifest())
    @example((
        '[lints.rust]\ndead_code = "allow"\n',
        ['[lints.rust] dead_code = "allow"'],
    ))
    def test_every_manifest_allow(self, drawn: Manifest) -> None:
        """Each `allow` level, in both forms, in every table."""
        text, expected = drawn
        self.assertEqual(expected, rsup.manifest_allows(decoded.load_toml(text)))


if __name__ == "__main__":
    unittest.main()

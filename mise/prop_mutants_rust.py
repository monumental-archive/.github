#!/usr/bin/env python3
"""mutants_rust.py's reason rule and default major, on inputs nobody wrote.

The properties of `test_mutants_rust.py`'s fixed rows (#1555), over
generated `.cargo/mutants.toml` files and cargo metadata:

- Invariance (V): an exclusion entry is unexplained exactly when no
  `#` reason line stands above it (blank lines between allowed), named
  at its own line under its own key, however many entries and keys the
  file holds; adding the reason clears it, removing it reds.
- Refusal (R): `unexplained` reads any text and never raises.
- Invariance (V): a crate's default major is its one `pgNN` default
  feature, in any order among the others, and nothing when it names
  none or several.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import unittest

import mutants_rust as mr
from hypothesis import strategies as st
from typed_hypothesis import given

ENTRY = st.from_regex(r"[a-z_:]{1,12}", fullmatch=True)


Drawn = tuple[str, list[tuple[int, str, str]]]


def draw_config(draw: st.DrawFn) -> Drawn:
    """Write exclusion arrays, some entries with a reason above them.

    Returns:
        The text and the (line, key, entry) of each entry with none.

    """
    lines = ["# cargo-mutants configuration", ""]
    expected: list[tuple[int, str, str]] = []
    for key in draw(
        st.lists(st.sampled_from(["exclude_re", "exclude_globs"]), unique=True)
    ):
        lines.append(f"{key} = [")
        for entry in draw(st.lists(ENTRY, max_size=4)):
            explained = draw(st.booleans())
            if explained:
                lines.append(f"  # {draw(ENTRY)} is equivalent")
                lines += [""] * draw(st.integers(0, 2))
            quote = draw(st.sampled_from(['"', "'"]))
            spelt = f"{quote}{entry}{quote},"
            lines.append(f"  {spelt}")
            if not explained:
                expected.append((len(lines), key, spelt.rstrip(",")))
        lines.append("]")
        lines.append(draw(st.sampled_from(["", "timeout_multiplier = 2.0"])))
    return "\n".join(lines) + "\n", expected


config = st.composite(draw_config)


class Reasons(unittest.TestCase):
    """Each entry without a reason, by line and key."""

    @given(config())
    def test_exactly_the_unexplained_entries(self, drawn: Drawn) -> None:
        """Named at their lines; explained ones never."""
        text, expected = drawn
        self.assertEqual(mr.unexplained(text), expected, text)

    @given(st.text(max_size=200))
    def test_any_text_is_read(self, text: str) -> None:
        """A list of findings, never a traceback."""
        self.assertIsInstance(mr.unexplained(text), list)


class DefaultMajor(unittest.TestCase):
    """One pgNN in default, or nothing."""

    @given(
        st.data(),
        st.lists(st.integers(10, 30).map("pg{}".format), unique=True, max_size=3),
        st.lists(
            st.sampled_from(["cshim", "pg_test", "serde"]), unique=True, max_size=3
        ),
    )
    def test_the_one_major(
        self, data: st.DataObject, majors: list[str], others: list[str]
    ) -> None:
        """The feature when there is exactly one, else empty."""
        default = data.draw(st.permutations(majors + others))
        metadata = {
            "packages": [
                {"name": "other", "features": {"default": ["pg99"]}},
                {"name": "ext", "features": {"default": default}},
            ]
        }
        self.assertEqual(
            mr.default_major(mr.read_metadata(metadata), "ext"),
            majors[0] if len(majors) == 1 else "",
        )


if __name__ == "__main__":
    unittest.main()

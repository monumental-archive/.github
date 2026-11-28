"""regression_count.py's path and pin rules, on inputs nobody wrote down.

The properties of `test_regression_count.py`'s fixed rows (#1548), over
generated paths, diff lines and patches:

- Idempotence (I): blanking the pins of a line twice is blanking them
  once.
- Invariance (V): a patch whose added lines are its removed lines with
  only versions, hashes and dates changed is pin-only, in whatever
  order its lines come; one that changes any other word is not.
- Invariance (V): a test is a test by its basename wherever it sits,
  and anything under a `tests/` or `testdata/` directory is a test.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

from hypothesis import strategies as st

from belt import regression_count as rc
from belt.typed_hypothesis import given

WORD = st.from_regex(r"[g-z_]{1,8}", fullmatch=True)  # never a hex run
PINS = {
    "ver": st.from_regex(r"v?[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True),
    "hash": st.from_regex(r"[0-9a-f]{40}", fullmatch=True),
    "date": st.from_regex(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]", fullmatch=True),
}
SEGMENT = st.from_regex(r"[a-z][a-z0-9_-]{0,6}", fullmatch=True)


def draw_pinned_line(draw: st.DrawFn) -> list[tuple[str | None, str]]:
    """Draw a line of words with pins among them.

    Returns:
        (pin kind or None, text) per word.

    """
    kinds = draw(st.lists(st.sampled_from([None, *PINS]), min_size=1, max_size=6))
    return [(k, draw(PINS[k]) if k else draw(WORD)) for k in kinds]


pinned_line = st.composite(draw_pinned_line)


# Pins are blanked; nothing else is.
@given(st.text(max_size=80))
def test_twice_is_once(line: str) -> None:
    """A normalised line is its own normal form."""
    once = rc.normalise_pin_line(line)
    assert rc.normalise_pin_line(once) == once


@given(st.data(), st.lists(pinned_line(), min_size=1, max_size=5))
def test_a_pin_bump_is_pin_only(
    data: st.DataObject, lines: list[list[tuple[str | None, str]]]
) -> None:
    """Every pin moved, nothing else: pin-only, in any line order."""
    removed = [" ".join(w for _, w in line) for line in lines]
    added = [
        " ".join(data.draw(PINS[k]) if k else w for k, w in line) for line in lines
    ]
    patch = "\n".join(
        data.draw(
            st.permutations([f"-{r}" for r in removed] + [f"+{a}" for a in added])
        )
    )
    assert rc.is_pin_only(f"--- a/x\n+++ b/x\n{patch}\n")


@given(st.lists(pinned_line(), min_size=1, max_size=5), WORD)
def test_another_word_is_not(
    lines: list[list[tuple[str | None, str]]], extra: str
) -> None:
    """A word added to one line: not pin-only."""
    removed = [" ".join(w for _, w in line) for line in lines]
    added = [*removed[:-1], removed[-1] + " " + extra]
    patch = "\n".join([*(f"-{r}" for r in removed), *(f"+{a}" for a in added)])
    assert not rc.is_pin_only(patch)


# Tests by name, anywhere; anything under a test directory.
@given(
    st.lists(SEGMENT, max_size=3),
    st.sampled_from(["test_x.py", "x_test.go", "x.test.ts", "x.spec.js"]),
)
def test_named_tests_anywhere(dirs: list[str], name: str) -> None:
    """The basename decides."""
    assert rc.is_test_path("/".join([*dirs, name]))


@given(
    st.lists(SEGMENT, max_size=2),
    st.sampled_from(sorted(rc.TEST_DIRS)),
    st.lists(SEGMENT, max_size=2),
    SEGMENT,
)
def test_under_a_test_dir(
    above: list[str], tests: str, below: list[str], name: str
) -> None:
    """Any file below a test directory."""
    assert rc.is_test_path("/".join([*above, tests, *below, name]))

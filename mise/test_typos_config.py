#!/usr/bin/env python3
"""Table tests for the delivered typos config (#1249, #2079).

Two rulings live in `mise/typos.toml` and are driven here: the
commit-SHA skip (#1249) and the fuzz-corpus exclusion (#2079).

The v1.67.1 release PR went red on its own generated changelog: typos
read the letters inside the abbreviated SHA `3ede177` as a word.
The belt's `mise/typos.toml` now skips a standalone lowercase hex token
of seven or more characters that contains a digit. The rows here run the
PINNED typos against the DELIVERED file, with `--isolated` so no
implicit config can stand in for it.

Every green row has a control: the same input under typos' defaults is
red, so a row cannot pass because the input was never a finding. Red
rows assert the exact message, not just the exit status (#650). The
misspellings are assembled at runtime — written out here, they would
red this repository's own `lint:typos`.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
CONFIG = BELT / "typos.toml"
CHANGELOG_LINE = BELT / "testdata" / "typos" / "changelog-v1.67.1.txt"
TYPOS = hermetic.tool("typos")

# The run typos found inside the SHA, and what it asked for.
RUN = "e" + "de"
RUN_MESSAGE = f"`{RUN}` should be `edge`"

# A real misspelling with no hex in it.
PROSE = "t" + "eh"
PROSE_MESSAGE = f"`{PROSE}` should be `the`"

# Seven letters, all a-f, no digit, and a misspelling typos knows: found
# by running the pinned typos over every seven-letter a-f string.
HEX_WORD = "ebe" + "dded"
HEX_WORD_MESSAGE = f"`{HEX_WORD}` should be `embedded`"

# The first digit at every position the config enumerates, each with
# the run beside it: positions 0 to 6 have an entry each, 7 rides on
# the open-ended last one.
BY_POSITION = [
    f"1{RUN}111",
    f"e1{RUN}11",
    f"ed1{RUN}1",
    f"{RUN}1{RUN}",
    f"c{RUN}1{RUN}",
    f"cc{RUN}1{RUN}",
    f"ccc{RUN}1{RUN}",
    f"cccc{RUN}1{RUN}",
]


# The kept input that reddened edtf's corpus PR (#2079), byte for byte,
# with the misspelling typos found in it.
CORPUS_RUN = "i" + "z"
CORPUS_INPUT = f"Y6776E----{CORPUS_RUN}-x-&---z"
CORPUS_MESSAGE = f"`{CORPUS_RUN}` should be `is`"
CORPUS_FILE = "fuzz/corpus/normalize/07eca93f432491730957db8dd770ce35073a27ad"


def typos_at(
    path: str, text: str, *, belt: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the pinned typos as `lint:typos` does, over one planted path.

    The task hands typos each tracked path, relative to the tree's root,
    with `--force-exclude` so the config's `extend-exclude` still holds
    for a path named explicitly. This plants `text` at `path` in a fresh
    tree and names it the same way.

    Returns:
        The completed process, stdout and stderr captured.

    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / path
        target.parent.mkdir(parents=True)
        target.write_text(text + "\n", encoding="utf-8")
        args = [TYPOS, "--isolated", "--force-exclude"]
        if belt:
            args += ["-c", str(CONFIG)]
        return subprocess.run(
            [*args, path],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )


def typos(text: str, *, belt: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the pinned typos over one file holding `text`.

    Returns:
        The completed process, stdout and stderr captured.

    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "input.md"
        target.write_text(text + "\n", encoding="utf-8")
        args = [TYPOS, "--isolated"]
        if belt:
            args += ["-c", str(CONFIG)]
        return subprocess.run(
            [*args, str(target)],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )


class TyposShaTest(unittest.TestCase):
    """A short SHA is skipped; words around and like it are not."""

    def assert_green(self, text: str) -> None:
        """Red under typos' defaults, green under the belt config."""
        control = typos(text, belt=False)
        self.assertEqual(control.returncode, 2, control.stdout)
        result = typos(text)
        self.assertEqual(result.returncode, 0, result.stdout)

    def assert_red(self, text: str, message: str) -> str:
        """Red under the belt config with exactly `message`.

        Returns:
            typos' stdout, for further assertions.

        """
        result = typos(text)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(message, result.stdout)
        return result.stdout

    def test_the_release_sha_passes(self) -> None:
        """`3ede177`, the token that reddened the v1.67.1 release PR."""
        self.assert_green(f"3{RUN}177")

    def test_the_release_changelog_line_passes(self) -> None:
        """The release PR's CHANGELOG line, verbatim from the fixture."""
        self.assert_green(CHANGELOG_LINE.read_text(encoding="utf-8").rstrip("\n"))

    def test_every_digit_position_passes(self) -> None:
        """Each enumerated entry, by where the first digit sits."""
        for token in BY_POSITION:
            with self.subTest(token=token):
                self.assert_green(token)

    def test_a_misspelling_beside_the_sha_still_reds(self) -> None:
        """The skip covers the SHA, not the line it is on."""
        out = self.assert_red(f"3{RUN}177 {PROSE}", PROSE_MESSAGE)
        self.assertNotIn(RUN_MESSAGE, out)

    def test_an_all_letter_hex_misspelling_is_still_checked(self) -> None:
        """No digit, no skip: a-f-only words stay words."""
        self.assert_red(HEX_WORD, HEX_WORD_MESSAGE)

    def test_a_short_hex_token_is_still_checked(self) -> None:
        """Below seven characters the token is not taken for a SHA."""
        self.assert_red(f"{RUN}12", RUN_MESSAGE)


class TyposFuzzCorpusTest(unittest.TestCase):
    """The root `fuzz/corpus/` is data; the same bytes elsewhere are not."""

    def assert_red_at(self, path: str) -> None:
        """Red under the belt config at `path`, with the exact message."""
        result = typos_at(path, CORPUS_INPUT)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(CORPUS_MESSAGE, result.stdout)
        self.assertIn(path, result.stdout)

    def test_a_corpus_input_passes(self) -> None:
        """The edtf input, at its own path: green, and red without the belt."""
        control = typos_at(CORPUS_FILE, CORPUS_INPUT, belt=False)
        self.assertEqual(control.returncode, 2, control.stdout)
        self.assertIn(CORPUS_MESSAGE, control.stdout)
        result = typos_at(CORPUS_FILE, CORPUS_INPUT)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_same_bytes_outside_the_corpus_still_red(self) -> None:
        """The exclusion is the path, not the content."""
        self.assert_red_at("fuzz/normalize.txt")

    def test_a_nested_corpus_is_still_checked(self) -> None:
        """Anchored at the root: cargo-fuzz writes nowhere deeper."""
        self.assert_red_at("vendor/fuzz/corpus/normalize/input")


if __name__ == "__main__":
    unittest.main()

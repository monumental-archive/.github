#!/usr/bin/env python3
"""watch_issue.py's checkpoints and verdict, on inputs nobody wrote down.

The properties of `test_watch_issue.py`'s fixed rows (#1557):

- R: reading a checkpoint never raises, whatever bytes the file holds,
  and what it read is judged and written into a body without raising.
  A checkpoint it cannot read is a `Checkpoint` naming why, and the run
  goes red behind. A `next` of Infinity, a timestamp past the platform's
  range and JSON nested past the parser's depth each raised a traceback
  until this file found them.
- V: with both checkpoints read and the newest short of the log's head,
  the run is red exactly when `next` did not advance, and gaining
  (nothing to keep) exactly when it advanced and the outstanding count
  fell. At the head it is caught up whatever the start; a founding run
  is green. The head's timestamp never changes the verdict.
- T: what the body writes as "Behind since" and "Runs behind since it
  opened", the next run reads back to the same value. Both are read
  from one line of the previous body, so the values written are lines:
  a run URL and the forge's job results, never text that spans lines.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import override

import watch_issue as standing
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# Past the JSON parser's depth: a RecursionError, not a ValueError.
DEEP = b"[" * 100_000


def numeral(number: int) -> str:
    """Write a number as the string a hand-edited checkpoint might hold.

    Returns:
        Its decimal text.

    """
    return str(number)


NUMERAL = st.integers().map(numeral)
NUMBER: st.SearchStrategy[object] = st.one_of(
    st.integers(),
    st.floats(),
    NUMERAL,
    st.booleans(),
    st.none(),
    st.text(max_size=6),
)
SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=12)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=4)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=8), inner, max_size=4
    )
    return arrays | objects


def encoded(value: object) -> bytes:
    """Write a drawn document as a checkpoint file's bytes.

    Returns:
        The JSON text, encoded.

    """
    return json.dumps(value).encode()


def one_line(text: str) -> bool:
    """Say whether a drawn text holds no newline.

    Returns:
        True for a single line.

    """
    return "\n" not in text


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=12)
HEAD_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "treeSize": NUMBER,
    "timestamp": NUMBER,
}
HEAD: st.SearchStrategy[object] = st.fixed_dictionaries(HEAD_FIELDS) | JSON
CHECKPOINT_FIELDS: dict[str, st.SearchStrategy[object]] = {
    "next": NUMBER,
    "treeHead": HEAD,
}
CHECKPOINT: st.SearchStrategy[object] = st.one_of(
    st.fixed_dictionaries(CHECKPOINT_FIELDS),
    JSON,
)
CONTENT = st.one_of(
    st.binary(max_size=64),
    CHECKPOINT.map(encoded),
)
KEPT = st.sampled_from(["true", "false", ""]) | st.text(max_size=5)
# A line: what the workflow passes as a run URL or the legs' results.
LINE = st.text(min_size=1, max_size=40).filter(one_line)
POSITION = st.integers(0, 10**12)
MILLIS = st.integers(0, 4 * 10**12)
# A checkpoint stele could write: next index, tree size, head time (ms).
POINT = st.tuples(POSITION, POSITION, MILLIS)


def doc(point: tuple[int, int, int]) -> bytes:
    """Write a checkpoint in stele's shape.

    Returns:
        The file's bytes.

    """
    nxt, size, millis = point
    head: dict[str, object] = {"treeSize": size, "timestamp": millis}
    checkpoint: dict[str, object] = {"next": nxt, "treeHead": head}
    return json.dumps(checkpoint).encode()


class Scratch(unittest.TestCase):
    """Two scratch checkpoint files for a test's examples."""

    @override
    def setUp(self) -> None:
        """Hold the scratch files."""
        self.scratch = tempfile.TemporaryDirectory()
        self.start = Path(self.scratch.name) / "run-start.json"
        self.end = Path(self.scratch.name) / "checkpoint.json"

    @override
    def tearDown(self) -> None:
        """Remove the scratch files."""
        self.scratch.cleanup()

    @staticmethod
    def read(path: Path, content: bytes) -> standing.Checkpoint:
        """Write a checkpoint file and read it back.

        Returns:
            The Checkpoint the helper read.

        """
        path.write_bytes(content)
        return standing.read_checkpoint(path)


class Reading(Scratch):
    """R: whatever the checkpoint files hold, a verdict and a body."""

    @given(CONTENT, CONTENT, KEPT)
    @example(DEEP, DEEP, "true")
    @example(
        b'{"next": Infinity, "treeHead": {"treeSize": 1, "timestamp": 0}}',
        b"",
        "true",
    )
    @example(
        b"",
        b'{"next": 1, "treeHead": {"treeSize": 2, "timestamp": 1e300}}',
        "true",
    )
    def test_never_raises(self, start: bytes, end: bytes, kept: str) -> None:
        """Read, judged and written; unreadable names why."""
        first = self.read(self.start, start)
        last = self.read(self.end, end)
        for point in (first, last):
            self.assertIsInstance(point, standing.Checkpoint)
            read = (point.next, point.size, point.when)
            if point.missing is None:
                self.assertNotIn(None, read)
            else:
                self.assertEqual(read, (None, None, None))
        outcome = standing.judge(first, kept, last)
        if last.missing is not None:
            self.assertTrue(outcome.red)
            self.assertEqual(outcome.standing, "behind")
        run = standing.Run(
            standing.start_count(first, kept), outcome.why, "success", "u"
        )
        standing.body(last, run, "u", 1)


class Gained(Scratch):
    """V: advanced and gaining decide the verdict, nothing else."""

    @given(POINT, POINT)
    @example((5, 10, 0), (5, 10, 0))
    @example((5, 10, 0), (6, 10, 0))
    @example((5, 10, 0), (6, 11, 0))
    def test_gained_exactly_when_advanced(
        self, start: tuple[int, int, int], end: tuple[int, int, int]
    ) -> None:
        """Red when not advanced; gaining when advanced and the count fell."""
        (start_next, start_size, _), (end_next, end_size, end_ms) = start, end
        first = self.read(self.start, doc(start))
        last = self.read(self.end, doc(end))
        outcome = standing.judge(first, "true", last)
        if end_size == end_next:
            self.assertEqual((outcome.red, outcome.standing), (False, "caught-up"))
            return
        advanced = end_next > start_next
        self.assertEqual(outcome.red, not advanced, outcome.why)
        fell = (end_size - end_next) < (start_size - start_next)
        gaining = outcome.standing is None
        self.assertEqual(gaining, advanced and fell, outcome.why)
        if not gaining:
            self.assertEqual(outcome.standing, "behind")
        # The head's time is not a position: moving it changes nothing.
        moved = self.read(self.end, doc((end_next, end_size, end_ms // 2)))
        self.assertEqual(standing.judge(first, "true", moved), outcome)

    @given(CONTENT, KEPT, POSITION, MILLIS)
    def test_head_is_caught_up(
        self, start: bytes, kept: str, size: int, millis: int
    ) -> None:
        """At the head: caught up and green, whatever the start."""
        first = self.read(self.start, start)
        last = self.read(self.end, doc((size, size, millis)))
        outcome = standing.judge(first, kept, last)
        self.assertEqual((outcome.red, outcome.standing), (False, "caught-up"))

    @given(CONTENT, POINT)
    def test_founding_run_is_green(
        self, start: bytes, end: tuple[int, int, int]
    ) -> None:
        """No checkpoint kept at the start: green, nothing to keep, unless at head."""
        nxt, size, _ = end
        first = self.read(self.start, start)
        last = self.read(self.end, doc(end))
        outcome = standing.judge(first, "false", last)
        self.assertFalse(outcome.red)
        if size != nxt:
            self.assertIsNone(outcome.standing)


class BodyRoundTrip(unittest.TestCase):
    """T: the two values an edit carries forward read back unchanged."""

    @given(POINT, st.tuples(LINE, LINE, LINE), st.integers(1, 10**9), KEPT)
    @example((5, 10, 0), ("u", "success", "u"), 1, "true")
    def test_since_and_runs_round_trip(
        self,
        point: tuple[int, int, int],
        lines: tuple[str, str, str],
        runs: int,
        kept: str,
    ) -> None:
        """Written into a body, read back by the next run's edit."""
        url, legs, since = lines
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "checkpoint.json"
            path.write_bytes(doc(point))
            end = standing.read_checkpoint(path)
        outcome = standing.judge(end, kept, end)
        run = standing.Run(standing.start_count(end, kept), outcome.why, legs, url)
        text = standing.body(end, run, since, runs)
        since_match = standing.BEHIND_SINCE.search(text)
        runs_match = standing.RUNS_BEHIND.search(text)
        if since_match is None:
            self.fail("the body carries no behind-since line")
        if runs_match is None:
            self.fail("the body carries no runs-behind line")
        self.assertEqual(since_match.group(1), since)
        self.assertEqual(int(runs_match.group(1)), runs)


if __name__ == "__main__":
    unittest.main()

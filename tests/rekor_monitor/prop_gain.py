"""gain.py's checkpoints, verdict and reasons, on inputs nobody wrote down.

The properties of `test_gain.py`'s fixed rows (#1557, #1967):

- R: reading a checkpoint never raises, whatever bytes the file holds,
  and what it read is judged and written into the finding's detail
  without raising. A checkpoint it cannot read is a `Checkpoint` naming
  why, and the run goes red behind. A `next` of Infinity, a timestamp
  past the platform's range and JSON nested past the parser's depth
  each raised a traceback until this file found them.
- V: with both checkpoints read and the newest short of the log's head,
  the run is green exactly when it advanced and the outstanding count
  fell. At the head it is green whatever the start; a founding run is
  green. The head's timestamp never changes the verdict.
- C: whatever the legs' reports hold, the reason lines count every leg
  once: their counts sum to the number of legs, a leg with no report
  counted as one.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import tempfile
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st

from belt.rekor_monitor import gain, verdict
from belt.typed_hypothesis import given

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


def stopped_report(reason: str) -> bytes:
    """Write a report in stele's shape that stopped for a reason.

    Returns:
        The file's bytes.

    """
    fact: dict[str, object] = {"name": "stopped", "value": reason}
    report: dict[str, object] = {"target": verdict.TARGET, "facts": [fact]}
    return json.dumps(report).encode()


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
# A leg's report: any bytes, or stele's shape stopped for one of a few
# reasons, so reasons repeat across legs.
REPORT = st.one_of(
    CONTENT,
    st.sampled_from(["throttled", "budget", ""]).map(stopped_report),
)
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
def test_never_raises(start: bytes, end: bytes, kept: str) -> None:
    """Read, judged and written; unreadable names why."""
    first = gain.checkpoint_of(start)
    last = gain.checkpoint_of(end)
    for point in (first, last):
        assert isinstance(point, gain.Checkpoint)
        fields = (point.next, point.size, point.when)
        if point.missing is None:
            assert None not in fields
        else:
            assert fields == (None, None, None)
    outcome = gain.judge(first, kept, last)
    if last.missing is not None:
        assert outcome.red
    lines = gain.detail(gain.start_count(first, kept), last, outcome.why, [])
    assert len(lines) == 5


@given(POINT, POINT)
@example((5, 10, 0), (5, 10, 0))
@example((5, 10, 0), (6, 10, 0))
@example((5, 10, 0), (6, 11, 0))
def test_gained_exactly_when_advanced(
    start: tuple[int, int, int], end: tuple[int, int, int]
) -> None:
    """Green exactly when advanced and the count fell."""
    (start_next, start_size, _), (end_next, end_size, end_ms) = start, end
    first = gain.checkpoint_of(doc(start))
    last = gain.checkpoint_of(doc(end))
    outcome = gain.judge(first, "true", last)
    if end_size == end_next:
        assert outcome == gain.Outcome(red=False, why=gain.AT_HEAD)
        return
    advanced = end_next > start_next
    fell = (end_size - end_next) < (start_size - start_next)
    assert outcome.red == (not (advanced and fell)), outcome.why
    # The head's time is not a position: moving it changes nothing.
    moved = gain.checkpoint_of(doc((end_next, end_size, end_ms // 2)))
    assert gain.judge(first, "true", moved) == outcome


@given(CONTENT, KEPT, POSITION, MILLIS)
def test_head_is_caught_up(start: bytes, kept: str, size: int, millis: int) -> None:
    """At the head: green, whatever the start."""
    first = gain.checkpoint_of(start)
    last = gain.checkpoint_of(doc((size, size, millis)))
    outcome = gain.judge(first, kept, last)
    assert outcome == gain.Outcome(red=False, why=gain.AT_HEAD)


@given(CONTENT, POINT)
def test_founding_run_is_green(start: bytes, end: tuple[int, int, int]) -> None:
    """No checkpoint kept at the start: green."""
    first = gain.checkpoint_of(start)
    last = gain.checkpoint_of(doc(end))
    assert not gain.judge(first, "false", last).red


# An empty report, one nested past the parser, and one of another shape.
MIXED: list[bytes] = [b"", DEEP, b"[]"]


# C: every leg is counted once, whatever its report holds.
@given(st.lists(REPORT, max_size=6), st.integers(0, 4))
@example(MIXED, 0)
def test_counts_sum_to_the_legs(contents: list[bytes], extra: int) -> None:
    """The reason lines' counts sum to the legs; no line is empty."""
    legs = len(contents) + extra
    with tempfile.TemporaryDirectory() as scratch:
        reports = Path(scratch)
        for leg, content in enumerate(contents, start=1):
            path = reports / f"watch-report-{leg}" / "report.json"
            path.parent.mkdir()
            path.write_bytes(content)
        lines = gain.reasons(reports, legs)
    total = 0
    for line in lines:
        n = int(line.split(" ", 1)[0])
        assert n > 0, line
        total += n
    assert total == legs
    assert len(set(lines)) == len(lines)

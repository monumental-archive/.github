"""Every mutation pragma is the reasoned line form (#1354, #1555, #2177).

`refusal` judges one comment; `main` judges every pragma comment in the
files it is given, and names a file it cannot read (the regression row
of the defect prop_mutation_pragmas.py found: a file whose bytes are not
UTF-8 past its encoding line raised UnicodeDecodeError out of `main`,
and left every later file unread).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from belt import mutation_pragmas as mp

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:mutants"
LINE_ONLY = "only the line form exists; mark each line, with its reason"
NO_REASON = "# pragma: no mutate with no reason — write it after ' -- '"
WHY = "  an exclusion records a mutant the suite cannot kill; say why at the site\n"


@pytest.mark.parametrize(
    ("comment", "word"),
    [
        ("# pragma: no mutate -- the width", "--"),
        ("# pragma: no mutate", ""),
        ("# pragma: no mutate block", "block"),
        ("# pragma: no mutate: start, why", "start"),
        ("# pragma: no mutate end", "end"),
    ],
)
def test_the_word_is_read_as_mutmut_reads_it(comment: str, word: str) -> None:
    """The first word after `no mutate`, past a colon, before a comma."""
    assert mp.word(comment) == word


@pytest.mark.parametrize(
    ("comment", "refused"),
    [
        pytest.param("# pragma: no mutate -- the fixture's width", None, id="reasoned"),
        pytest.param("# pragma: no mutate", NO_REASON, id="no reason"),
        pytest.param("# pragma: no mutate --   ", NO_REASON, id="an empty reason"),
        pytest.param(
            "# pragma: no mutate block -- why",
            f"# pragma: no mutate block — {LINE_ONLY}",
            id="block",
        ),
        pytest.param(
            "# pragma: no mutate start",
            f"# pragma: no mutate start — {LINE_ONLY}",
            id="start",
        ),
        pytest.param(
            "# pragma: no mutate end",
            f"# pragma: no mutate end — {LINE_ONLY}",
            id="end",
        ),
    ],
)
def test_each_pragma_is_judged(comment: str, refused: str | None) -> None:
    """The line form with its reason passes; every other form is named."""
    assert mp.refusal(comment) == refused


def test_a_pragma_in_a_string_is_not_a_site(
    tmp_path: Path,
    as_task: Callable[[str], None],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A fixture's planted pragma text is string, not comment."""
    as_task(TASK)
    path = tmp_path / "f.py"
    path.write_text('X = "# pragma: no mutate block"\n', encoding="utf-8")
    status = mp.main([str(path)])
    _out, err = capfd.readouterr()
    assert (status, err) == (0, "")


def test_invalid_utf8_is_named_and_the_rest_read(
    tmp_path: Path,
    as_task: Callable[[str], None],
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The shrunk example: a CR, then a truncated UTF-8 sequence."""
    as_task(TASK)
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\r\xc2\x80")
    good = tmp_path / "good.py"
    good.write_text("x = 1  # pragma: no mutate\n", encoding="utf-8")
    status = mp.main([str(bad), str(good)])
    _out, err = capfd.readouterr()
    assert status == 1
    assert err.startswith(f"{TASK}: {bad}: could not tokenize:")
    assert err.endswith(f"{TASK}: {good}:1: {NO_REASON}\n{WHY}")

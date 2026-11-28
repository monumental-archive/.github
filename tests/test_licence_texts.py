"""Tests for licence_texts.py, lint:licence's licence-text check (#1589).

The canon's REUSE.toml labelled two fixtures copied from edtf as the
canon's own 0BSD work. The relabel names `MIT OR Apache-2.0`, and a
REUSE.toml naming a licence whose text is not tracked is the half of
that defect a machine can hold. Every red row asserts its message, so
a row cannot pass on a different failure.
"""

import io
import sys
from typing import TYPE_CHECKING

import pytest

from belt import licence_texts as lt
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:licence"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


HERE = support.MISE
ROOT = HERE.parent

FIXTURE_ENTRY = """\
version = 1

[[annotations]]
path = ["**"]
precedence = "aggregate"
SPDX-FileCopyrightText = "2026 Carl Allen"
SPDX-License-Identifier = "0BSD"

[[annotations]]
path = ["release/testdata/edtf-core-1.3.3.crate"]
precedence = "override"
SPDX-FileCopyrightText = "Copyright (c) the edtf contributors"
SPDX-License-Identifier = "MIT OR Apache-2.0"
"""


@fixture
def listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, str]], None]:
    """Stand each row in a tree of its own, its files listed on stdin.

    Returns:
        A function that writes the files and steps into the tree.

    """

    def tree(files: dict[str, str]) -> None:
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO("\0".join(files) + "\0"))

    return tree


# An expression splits into the identifiers whose texts it needs.
def test_operators_and_parentheses_are_not_identifiers() -> None:
    """OR, AND and WITH in either case, and brackets, name nothing."""
    named: list[str] = [
        "MIT",
        "Apache-2.0",
        "GPL-2.0-only",
        "Classpath-exception-2.0",
    ]
    assert named == lt.identifiers(
        "(MIT or Apache-2.0) AND GPL-2.0-only WITH Classpath-exception-2.0"
    )


def test_or_later_needs_the_base_text() -> None:
    """`GPL-2.0+` is GPL-2.0's text with the or-later operator."""
    base: list[str] = ["GPL-2.0"]
    assert base == lt.identifiers("GPL-2.0+")


def test_a_list_of_expressions_is_read_whole() -> None:
    """SPDX-License-Identifier may be a list of strings."""
    config: dict[str, object] = {
        "annotations": [
            {"SPDX-License-Identifier": ["MIT", "CC0-1.0"]},
            {"SPDX-License-Identifier": "0BSD"},
            {"path": "no-licence"},
        ]
    }
    every: list[str] = ["MIT", "CC0-1.0", "0BSD"]
    assert every == lt.expressions(config)


# Every identifier a tracked REUSE.toml names has its tracked text.
def test_an_identifier_with_no_text_is_red_by_name(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """A missing text names the identifier and the file naming it."""
    listed({
        "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
        "LICENSES/0BSD.txt": "0BSD\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert (
        err
        == "lint:licence: REUSE.toml names MIT, and LICENSES/MIT.txt is not tracked\n"
    )


def test_an_or_expression_needs_both_texts(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """One text of a dual licence is not enough: the other is named."""
    listed({
        "REUSE.toml": FIXTURE_ENTRY,
        "LICENSES/0BSD.txt": "0BSD\n",
        "LICENSES/MIT.txt": "MIT\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert err == (
        "lint:licence: REUSE.toml names Apache-2.0,"
        " and LICENSES/Apache-2.0.txt is not tracked\n"
    )


def test_main_with_the_relabel_and_no_texts_is_red(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """Main's tree after item 1 alone: both new texts named (fails before)."""
    listed({
        "REUSE.toml": FIXTURE_ENTRY,
        "LICENSES/0BSD.txt": "0BSD\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert err == (
        "lint:licence: REUSE.toml names MIT,"
        " and LICENSES/MIT.txt is not tracked\n"
        "lint:licence: REUSE.toml names Apache-2.0,"
        " and LICENSES/Apache-2.0.txt is not tracked\n"
    )


def test_every_text_present_is_green(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """The dual-licensed fixture entry with all three texts passes."""
    listed({
        "REUSE.toml": FIXTURE_ENTRY,
        "LICENSES/0BSD.txt": "0BSD\n",
        "LICENSES/MIT.txt": "MIT\n",
        "LICENSES/Apache-2.0.txt": "Apache\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert (status, err) == (0, "")


def test_a_nested_reuse_toml_is_held_too(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """Any tracked REUSE.toml, against the root LICENSES/."""
    listed({
        "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "0BSD"\n',
        "sub/REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
        "LICENSES/0BSD.txt": "0BSD\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert err == (
        "lint:licence: sub/REUSE.toml names MIT, and LICENSES/MIT.txt is not tracked\n"
    )


def test_a_text_elsewhere_does_not_count(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """A text outside LICENSES/, or not `.txt`, is not REUSE's place."""
    listed({
        "REUSE.toml": '[[annotations]]\nSPDX-License-Identifier = "MIT"\n',
        "LICENSE-MIT": "MIT\n",
        "LICENSES/MIT.md": "MIT\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert (
        err
        == "lint:licence: REUSE.toml names MIT, and LICENSES/MIT.txt is not tracked\n"
    )


def test_the_placeholder_is_still_refused(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """The scaffold's placeholder keeps its message from before (#214)."""
    listed({
        "REUSE.toml": (
            '[[annotations]]\nSPDX-License-Identifier = "<SPDX expression>"\n'
        ),
        "LICENSES/0BSD.txt": "0BSD\n",
    })
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert err == (
        "lint:licence: REUSE.toml still carries the scaffold's"
        " <SPDX expression> placeholder — pick the licence (#214)\n"
    )


def test_a_malformed_file_is_red_by_name(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """A REUSE.toml that is not TOML is named, never read as empty."""
    listed({"REUSE.toml": "[[annotations]\n"})
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert err.startswith("lint:licence: REUSE.toml does not parse: "), err


@pytest.mark.parametrize(
    ("reuse", "where"),
    [
        ("annotations = 1\n", "annotations is not a list"),
        ("annotations = [1]\n", "annotations[0] is not an object"),
        (
            "[[annotations]]\nSPDX-License-Identifier = 1\n",
            (
                "annotations[0].SPDX-License-Identifier is not a string or"
                " a list of strings"
            ),
        ),
    ],
)
def test_a_wrong_shape_is_red_by_where(
    reuse: str,
    where: str,
    capfd: pytest.CaptureFixture[str],
    listed: Callable[[dict[str, str]], None],
) -> None:
    """An annotation of the wrong shape is refused, naming where."""
    listed({"REUSE.toml": reuse})
    status = lt.main([])
    err = capfd.readouterr().err
    assert status == 1
    assert f"lint:licence: REUSE.toml: {where}\n" == err


def test_no_reuse_toml_is_green(
    capfd: pytest.CaptureFixture[str], listed: Callable[[dict[str, str]], None]
) -> None:
    """A repository that has not declared yet has nothing to check."""
    listed({"LICENSE": "0BSD\n"})
    status = lt.main([])
    _out, err = capfd.readouterr()
    assert (status, err) == (0, "")


def test_the_canons_own_tree_is_green() -> None:
    """The canon's REUSE.toml against the canon's LICENSES/."""
    tracked = ["REUSE.toml"] + [
        f"LICENSES/{path.name}" for path in (ROOT / "LICENSES").iterdir()
    ]
    clean: list[str] = []
    assert clean == lt.findings(ROOT, tracked)
    # The relabel is what makes this row mean something: the canon
    # names licences other than its own, and drops either text red.
    for name in ("MIT", "Apache-2.0"):
        without = [p for p in tracked if p != f"LICENSES/{name}.txt"]
        want: list[str] = [
            f"REUSE.toml names {name}, and LICENSES/{name}.txt is not tracked"
        ]
        assert want == lt.findings(ROOT, without)


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            ["extra"],
            "belt licence-texts: error: unrecognized arguments: extra",
            id='["extra"]',
        ),
    ],
)
def test_usage(argv: list[str], error: str, capfd: pytest.CaptureFixture[str]) -> None:
    """An argument is a usage error, not a pass: argparse's exit 2 and its error."""
    with pytest.raises(SystemExit) as ended:
        lt.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt licence-texts "), err
    assert err.splitlines()[-1] == error

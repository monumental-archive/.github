"""Tests for project_licence.py, the reader both derivations call (#1589).

The script rows live beside their scripts (`test_citation.py`,
`test_derive_badges.py`), which run each derivation whole. These run
the reader in process for the branches neither script row reaches.
"""

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import project_licence as pl
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "fix:test"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


HERE = support.MISE
GIT = support.tool("git")


@fixture
def repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: support.Env
) -> Callable[[dict[str, str]], None]:
    """Stand each row in a repository of its own, its files tracked.

    The reader asks git whether `.badge-states` is tracked (#2013).

    Returns:
        A function that writes and tracks the files and steps in.

    """

    def tree(files: dict[str, str]) -> None:
        for name, text in files.items():
            (tmp_path / name).write_text(text, encoding="utf-8")
        environ = env(tmp_path / ".home")
        for step in (["init", "-q"], ["add", "-A"]):
            subprocess.run([GIT, *step], cwd=tmp_path, env=environ, check=True)
        monkeypatch.chdir(tmp_path)

    return tree


# The reader's own branches.
def test_one_expression_is_printed(
    capfd: pytest.CaptureFixture[str], repo: Callable[[dict[str, str]], None]
) -> None:
    """No declaration needed where the tree says one thing."""
    reuse = '[[annotations]]\nSPDX-License-Identifier = "0BSD"\n'
    repo({"REUSE.toml": reuse})
    status = pl.main(["fix:test"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "0BSD\n", "")


def test_a_list_value_is_read_whole(
    capfd: pytest.CaptureFixture[str], repo: Callable[[dict[str, str]], None]
) -> None:
    """SPDX-License-Identifier may be a list; each entry counts."""
    reuse = '[[annotations]]\nSPDX-License-Identifier = ["0BSD", "MIT"]\n'
    repo({"REUSE.toml": reuse})
    status = pl.main(["fix:test"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == (
        "fix:test: REUSE.toml names several licence expressions:\n"
        "  0BSD\n  MIT\n" + pl.DECLARE + "\n"
    )


def test_no_expression_prints_nothing(
    capfd: pytest.CaptureFixture[str], repo: Callable[[dict[str, str]], None]
) -> None:
    """The caller words its own refusal; the reader stays silent."""
    repo({"REUSE.toml": "version = 1\n"})
    status = pl.main(["fix:test"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", "")


def test_comments_and_other_keys_are_not_the_licence_line() -> None:
    """Only a line whose first field is `licence` declares."""
    states = "# licence MIT\nlicences MIT\n\nlicence  0BSD \n"
    assert pl.declared(states) == "0BSD"
    assert pl.declared("reuse registered\n") == ""


def test_an_unparsable_file_is_red_by_name(
    capfd: pytest.CaptureFixture[str], repo: Callable[[dict[str, str]], None]
) -> None:
    """A REUSE.toml that is not TOML is named, never read as empty."""
    repo({"REUSE.toml": "[[annotations]\n"})
    status = pl.main(["fix:test"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err.startswith("fix:test: REUSE.toml does not parse: "), err


@pytest.mark.parametrize(
    ("reuse", "where"),
    [
        ("annotations = 1\n", "annotations is not a list"),
        ("annotations = [1]\n", "annotations[0] is not an object"),
        (
            "[[annotations]]\nSPDX-License-Identifier = [1]\n",
            (
                "annotations[0].SPDX-License-Identifier is not a string or"
                " a list of strings"
            ),
        ),
    ],
)
def test_a_wrong_shape_is_red_by_where(
    reuse: object,
    where: object,
    capfd: pytest.CaptureFixture[str],
    repo: Callable[[dict[str, str]], None],
) -> None:
    """An annotation of the wrong shape is refused, naming where."""
    repo({"REUSE.toml": reuse})
    status = pl.main(["fix:test"])
    out, err = capfd.readouterr()
    assert (1, "", f"fix:test: REUSE.toml: {where}\n") == (status, out, err)


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            "belt project-licence: error: the following arguments are required: caller",
            id="nothing",
        ),
    ],
)
def test_usage(argv: list[str], error: str, capfd: pytest.CaptureFixture[str]) -> None:
    """No caller is a usage error, not a pass: argparse's exit 2, its usage and its error."""
    with pytest.raises(SystemExit) as ended:
        pl.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt project-licence "), err
    assert err.splitlines()[-1] == error

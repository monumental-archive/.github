"""Tests for deny_skips.py's one-entry variants (#1555).

The regression rows of the defect `prop_deny_skips.py` found: a
variant dropped every line naming the crate, not the one skip entry.
Two skips of one crate at two versions each lost both, so a needless
one of the pair was never caught; and a `deny` entry for the crate was
dropped beside its skip.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import deny_skips as ds
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:deny"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


HERE = support.MISE

TWO_VERSIONS = """\
[bans]
skip = [
  { crate = "windows-sys@0.48", reason = "a" },
  { crate = "windows-sys@0.52", reason = "b" },
]
"""
DENIED_TOO = """\
[bans]
deny = [
  { crate = "openssl", wrappers = ["native-tls"] },
]
skip = [
  "openssl@0.10",
]
"""


@fixture
def deny(tmp_path: Path) -> Callable[[str], Path]:
    """Write a row's deny.toml for the helper.

    Returns:
        A function that writes `text` and returns the file's path.

    """

    def write(text: str) -> Path:
        path = tmp_path / "deny.toml"
        path.write_text(text, encoding="utf-8")
        return path

    return write


def variants(out: str) -> list[tuple[str, dict[str, object]]]:
    """Read the helper's stdout: one spec and variant file per line.

    Returns:
        Each printed spec with its variant's `[bans]` table.

    """
    found: list[tuple[str, dict[str, object]]] = []
    for line in out.splitlines():
        spec, variant = line.split("\t")
        config = decoded.load_toml(Path(variant).read_text("utf-8"))
        found.append((spec, decoded.as_object(config["bans"], "[bans]")))
    return found


def crates(bans: dict[str, object], key: str) -> list[str]:
    """Name the crate of each entry in one `[bans]` list.

    Returns:
        The `crate` of every entry under `key`, in order.

    """
    return [
        decoded.as_str(decoded.as_object(entry, key)["crate"], f"{key} crate")
        for entry in decoded.as_array(bans[key], key)
    ]


# Each variant is missing its own entry and nothing else.
def test_two_versions_of_one_crate_are_two_variants(
    capfd: pytest.CaptureFixture[str], deny: Callable[[str], Path]
) -> None:
    """Each version's variant keeps the other version's skip."""
    status = ds.main([str(deny(TWO_VERSIONS))])
    out = capfd.readouterr().out
    assert status == 0, out
    got = variants(out)
    skipped = [(spec, crates(bans, "skip")) for spec, bans in got]
    want: list[tuple[str, list[str]]] = [
        ("windows-sys@0.48", ["windows-sys@0.52"]),
        ("windows-sys@0.52", ["windows-sys@0.48"]),
    ]
    assert skipped == want


def test_a_deny_entry_for_the_crate_is_kept(
    capfd: pytest.CaptureFixture[str], deny: Callable[[str], Path]
) -> None:
    """Only the skip goes; the deny entry stays."""
    status = ds.main([str(deny(DENIED_TOO))])
    out = capfd.readouterr().out
    assert status == 0, out
    [(spec, bans)] = variants(out)
    assert spec == "openssl@0.10"
    none: list[object] = []
    assert bans["skip"] == none
    denied: list[str] = ["openssl"]
    assert crates(bans, "deny") == denied


# A deny.toml the helper cannot read is refused by name.
@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("[bans\n", "lint:deny: PATH: Expected ']' at the end of a table"),
        ("bans = 1\n", "lint:deny: PATH: bans is not an object\n"),
        ("[bans]\nskip = 1\n", "lint:deny: PATH: bans.skip is not a list\n"),
        (
            "[bans]\nskip-tree = {}\n",
            "lint:deny: PATH: bans.skip-tree is not a list\n",
        ),
    ],
)
def test_each_wrong_document_is_named(
    text: str, said: str, deny: Callable[[str], Path], capfd: pytest.CaptureFixture[str]
) -> None:
    """Not TOML, `bans` not a table, a skip key not an array."""
    path = deny(text)
    status = ds.main([str(path)])
    out, err = capfd.readouterr()
    err = err.replace(str(path), "PATH")
    assert (status, out) == (1, "")
    assert err.startswith(said), err

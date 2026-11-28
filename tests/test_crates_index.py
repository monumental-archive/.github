"""The crates.io sparse-index path rule, once (#1383).

Every bucket of cargo's layout, read against paths the index host
serves: `ed/tf/edtf-core` is release/testdata's captured record, and
`3/s/syn`, `2/cc`, `1/a` are the host's own short-name buckets.
"""

from typing import TYPE_CHECKING

import pytest

from belt import crates_index
from belt.typed_pytest import fixture

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:semver"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


# cargo's index layout, bucket by bucket.
@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("a", "1/a"),
        ("cc", "2/cc"),
        ("syn", "3/s/syn"),
        ("serde", "se/rd/serde"),
        ("edtf-core", "ed/tf/edtf-core"),
        ("lab-core", "la/b-/lab-core"),
    ],
)
def test_each_bucket(name: object, want: object) -> None:
    """One, two, three, and four or more characters."""
    assert want == crates_index.index_path(name)


def test_the_name_is_lowercased() -> None:
    """The index files a crate under its lowercased name."""
    assert crates_index.index_path("EDTF-Core") == "ed/tf/edtf-core"
    assert crates_index.index_path("Syn") == "3/s/syn"


def test_an_empty_name_has_no_record() -> None:
    """An empty name is refused, never placed at `0/`."""
    with pytest.raises(ValueError, match="a crate with no name has no index record"):
        crates_index.index_path("")


def test_the_url_is_the_host_and_the_path() -> None:
    """The record's URL, as both readers ask for it."""
    assert (
        crates_index.index_url("edtf-core") == "https://index.crates.io/ed/tf/edtf-core"
    )


# `crates_index.py url <crate>`, for the shell reader.
def test_url(capfd: pytest.CaptureFixture[str]) -> None:
    """One line, the record's URL."""
    status = crates_index.main(["url", "alpha"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "https://index.crates.io/al/ph/alpha\n",
        "",
    )


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ([], "belt crates-index: error: the following arguments are required: mode"),
        (
            ["url"],
            "belt crates-index url: error: the following arguments are required: crate",
        ),
        (
            ["path", "alpha"],
            "belt crates-index: error: argument mode: invalid choice: 'path'"
            " (choose from 'url')",
        ),
        (["url", "a", "b"], "belt crates-index: error: unrecognized arguments: b"),
    ],
)
def test_usage(argv: list[str], error: str, capfd: pytest.CaptureFixture[str]) -> None:
    """Anything but `url <crate>` is argparse's usage error."""
    with pytest.raises(SystemExit) as ended:
        crates_index.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt crates-index"), err
    assert err.splitlines()[-1] == error


def test_an_empty_name(capfd: pytest.CaptureFixture[str]) -> None:
    """The refusal reaches the caller's stderr by name."""
    status = crates_index.main(["url", ""])
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        2,
        "",
        f"{TASK}: a crate with no name has no index record\n",
    )

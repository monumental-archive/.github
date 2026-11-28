"""The crates.io sparse-index path rule, once (#1383).

Every bucket of cargo's layout, read against paths the index host
serves: `ed/tf/edtf-core` is release/testdata's captured record, and
`3/s/syn`, `2/cc`, `1/a` are the host's own short-name buckets.
"""

import io
from contextlib import redirect_stderr, redirect_stdout
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


class TestIndexPath:
    """cargo's index layout, bucket by bucket."""

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
    def test_each_bucket(self, name: object, want: object) -> None:
        """One, two, three, and four or more characters."""
        assert want == crates_index.index_path(name)

    def test_the_name_is_lowercased(self) -> None:
        """The index files a crate under its lowercased name."""
        assert crates_index.index_path("EDTF-Core") == "ed/tf/edtf-core"
        assert crates_index.index_path("Syn") == "3/s/syn"

    def test_an_empty_name_has_no_record(self) -> None:
        """An empty name is refused, never placed at `0/`."""
        with pytest.raises(
            ValueError, match="a crate with no name has no index record"
        ):
            crates_index.index_path("")

    def test_the_url_is_the_host_and_the_path(self) -> None:
        """The record's URL, as both readers ask for it."""
        assert (
            crates_index.index_url("edtf-core")
            == "https://index.crates.io/ed/tf/edtf-core"
        )


class TestCommand:
    """`crates_index.py url <crate>`, for the shell reader."""

    @staticmethod
    def run_main(*argv: str) -> tuple[int, str, str]:
        """Run main and capture its streams.

        Returns:
            The exit code, stdout and stderr.

        """
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = crates_index.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_url(self) -> None:
        """One line, the record's URL."""
        assert self.run_main("url", "alpha") == (
            0,
            "https://index.crates.io/al/ph/alpha\n",
            "",
        )

    @pytest.mark.parametrize(
        "argv", [(), ("url",), ("path", "alpha"), ("url", "a", "b")]
    )
    def test_usage(self, argv: object) -> None:
        """Anything but `url <crate>` is a usage error."""
        assert (
            2,
            "",
            f"{TASK}: usage: crates_index.py url <crate>\n",
        ) == self.run_main(*argv)

    def test_an_empty_name(self) -> None:
        """The refusal reaches the caller's stderr by name."""
        assert (
            2,
            "",
            f"{TASK}: a crate with no name has no index record\n",
        ) == self.run_main("url", "")

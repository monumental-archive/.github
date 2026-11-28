"""Table tests for the Best Practices ownership check (#1197).

The two fixtures are bestpractices.dev's own answers, fetched on
2026-11-15 from `https://www.bestpractices.dev/projects/<id>.json` and
reduced with jq to the fields `audit:badges` reads; no value in them was
typed by hand. 13998 is the entry monumental-archive-db wears, whose
`repo_url` names another owner; 14058 is the canon's own. A hand-written
fixture would only prove the check agrees with an idea of the issuer's
answer, which is how canon v1.24.0 burned.

The normalisation rows build on 14058's real URL, varying one thing at a
time, so each ignored difference and each kept one is planted alone.
"""

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import bestpractices_owner as owner
from belt import decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

HERE = support.MISE

DB = decoded.as_object(
    decoded.load_json(
        (
            Path(__file__).resolve().parent / "testdata" / "bestpractices-13998.json"
        ).read_text()
    ),
    "project 13998's entry",
)
CANON = decoded.as_object(
    decoded.load_json(
        (
            Path(__file__).resolve().parent / "testdata" / "bestpractices-14058.json"
        ).read_text()
    ),
    "project 14058's entry",
)

DB_WEARER = "https://github.com/monumental-archive/monumental-archive-db"
CANON_WEARER = "https://github.com/monumental-archive/.github"


@fixture
def fed(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
    """Feed the helper one project's entry on stdin, as the leg pipes it.

    Returns:
        A function that puts an entry, as JSON, on stdin.

    """

    def feed(doc: object) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(doc)))

    return feed


def entry(repo_url: str) -> dict[str, object]:
    """Return the canon's real entry with repo_url replaced.

    Returns:
        A copy of the fixture.

    """
    return {**CANON, "repo_url": repo_url}


# The two entries as the issuer serves them.
def test_13998_is_not_monumental_archive_dbs(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The defect as measured: red, naming both URLs."""
    fed(DB)
    status = owner.main([DB_WEARER])
    said = capfd.readouterr().out
    assert status == 1
    assert said == (
        "project 13998's repo_url is "
        "https://github.com/CarlAllenn/monumental-archive-db, not "
        "https://github.com/monumental-archive/monumental-archive-db, "
        "the repository wearing it\n"
    )


def test_14058_is_the_canons(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The canon's own entry passes, silently."""
    fed(CANON)
    status = owner.main([CANON_WEARER])
    assert (status, capfd.readouterr().out) == (0, "")


# Case, one trailing slash and one trailing .git; nothing else.
def test_case_is_ignored(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """GitHub owners are case-insensitive."""
    shouted = entry("https://github.com/Monumental-Archive/.GitHub")
    fed(shouted)
    assert owner.main([CANON_WEARER]) == 0
    capfd.readouterr()


def test_a_trailing_slash_is_ignored(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """A slash on either side."""
    fed(entry(CANON_WEARER + "/"))
    assert owner.main([CANON_WEARER]) == 0
    capfd.readouterr()
    fed(CANON)
    assert owner.main([CANON_WEARER + "/"]) == 0
    capfd.readouterr()


def test_a_trailing_git_is_ignored(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The clone URL form."""
    fed(entry(CANON_WEARER + ".git"))
    assert owner.main([CANON_WEARER]) == 0
    capfd.readouterr()
    fed(entry(CANON_WEARER + ".git/"))
    assert owner.main([CANON_WEARER]) == 0
    capfd.readouterr()


def test_an_old_owner_is_still_a_mismatch(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """A redirect from a renamed owner is not followed."""
    moved = entry("https://github.com/CarlAllenn/.github")
    fed(moved)
    status = owner.main([CANON_WEARER])
    said = capfd.readouterr().out
    assert status == 1
    assert "https://github.com/CarlAllenn/.github" in said
    assert CANON_WEARER in said


def test_another_host_is_a_mismatch(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """Only the three named differences fold."""
    elsewhere = entry("https://gitlab.com/monumental-archive/.github")
    fed(elsewhere)
    assert owner.main([CANON_WEARER]) == 1
    capfd.readouterr()


def test_a_missing_repo_url_is_red(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """An entry naming no repository binds to none."""
    entry = {k: v for k, v in CANON.items() if k != "repo_url"}
    fed(entry)
    status = owner.main([CANON_WEARER])
    said = capfd.readouterr().out
    assert status == 1
    assert said == (
        "project 14058 names no repo_url, so nothing binds it to "
        "https://github.com/monumental-archive/.github\n"
    )


def test_a_process_with_no_stdin_is_red_by_name(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """No standard input is named, never a traceback (#1681)."""
    monkeypatch.setattr("sys.stdin", None)
    status = owner.main([CANON_WEARER])
    out = capfd.readouterr().out
    assert status == 1
    assert out == (
        "the issuer's answer cannot be read: no standard input: the"
        " process was started without one\n"
    )


def test_a_non_object_answer_is_red(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """A list is not a project."""
    fed([CANON])
    assert owner.main([CANON_WEARER]) == 1
    capfd.readouterr()

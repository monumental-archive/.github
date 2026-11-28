"""Run `release/repro_resolve.py` against a stub `gh` (#1386).

`repro-check.yml`'s "Read the latest release" step is now one call to
the helper. These rows call its `main` in this process (#1698), with a
stub `gh` first on PATH answering from the row's fixtures, and read
back what it wrote to `GITHUB_OUTPUT`. Every refusal row asserts the
step's message. One row starts the helper as the step does instead:
the script contract (it starts, and exits with gh's message).
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from belt.release import repro_resolve
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["repro-resolve"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def dumps(value: object) -> str:
    """Encode a planted document as JSON.

    Returns:
        The JSON text.

    """
    return json.dumps(value)


COMMAND = "repro-resolve"
STUB_GH = """#!/usr/bin/env bash
case "$1 $2" in
  "release view") echo "${STUB_TAG}" ;;
  "release download")
    case " $* " in
      *" checksums.txt "*)
        [[ -f ${STUB_FIXTURES}/checksums.txt ]] || {
          echo "release asset not found" >&2
          exit 1
        }
        cp "${STUB_FIXTURES}/checksums.txt" "${@: -1}"
        ;;
      *" evidence-manifest.json "*)
        [[ -f ${STUB_FIXTURES}/evidence-manifest.json ]] || {
          echo "release asset not found" >&2
          exit 1
        }
        cp "${STUB_FIXTURES}/evidence-manifest.json" "${@: -1}"
        ;;
    esac
    ;;
esac
"""
CHECKSUMS = "aaaa  tool-1.2.3-linux.tar.gz\n"
TYPED = {"schema": 5, "entries": [{"name": "tool-1.2.3-linux.tar.gz"}]}


class ReadTheLatestRelease:
    """Each row: the latest release, and what the step handed on."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Give each row a working directory, fixtures and a stub `gh`."""
        self.make_env = env
        self.tmp = tmp_path
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        gh = self.bindir / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.fixtures = self.tmp / "published"
        self.fixtures.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()

    def enter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tag: str = "v1.2.3",
        *,
        checksums: str | None = CHECKSUMS,
        manifest: str | None = None,
    ) -> None:
        """Set the step's world against a latest release in the given state."""
        if checksums is not None:
            (self.fixtures / "checksums.txt").write_text(checksums, encoding="utf-8")
        if manifest is not None:
            (self.fixtures / "evidence-manifest.json").write_text(
                manifest, encoding="utf-8"
            )
        output = self.tmp / "github-output"
        output.write_text("", encoding="utf-8")
        env = self.make_env(
            PATH=f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.tmp),
            STUB_TAG=tag,
            STUB_FIXTURES=str(self.fixtures),
            GITHUB_REPOSITORY="example/project",
            GITHUB_OUTPUT=str(output),
        )
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.work)
        monkeypatch.setattr(sys, "argv", [COMMAND])

    def written(self) -> str:
        """Read what the run appended to `GITHUB_OUTPUT`.

        Returns:
            The appended lines, as one text.

        """
        return (self.tmp / "github-output").read_text(encoding="utf-8")


@fixture
def read_the_latest_release(tmp_path: Path, env: support.Env) -> ReadTheLatestRelease:
    """Give each row its own ReadTheLatestRelease."""
    return ReadTheLatestRelease(tmp_path, env=env)


def test_typed_release_hands_its_manifest_on(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stele#185: a typed manifest scopes the population."""
    manifest = json.dumps(TYPED, indent=2) + "\n\n"
    read_the_latest_release.enter(monkeypatch, manifest=manifest)
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 0, out + err
    assert written == (
        f"checksums<<CHECKSUMS_EOF\n{CHECKSUMS}CHECKSUMS_EOF\n"
        f"manifest<<MANIFEST_EOF\n{json.dumps(TYPED, indent=2)}\nMANIFEST_EOF\n"
        "tag=v1.2.3\nversion=1.2.3\n"
    )
    assert out == (
        "::notice::v1.2.3 types its entries — the rebuilt class scopes the"
        " population\n::notice::proving v1.2.3 reproduces\n"
    )


def test_schema_one_manifest_is_not_handed_on(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An untyped manifest would seal CANNOT_JUDGE (exit 4): withheld."""
    read_the_latest_release.enter(monkeypatch, manifest=dumps({"schema": 1}))
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 0, out + err
    assert "manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n" in written
    assert (
        "::notice::v1.2.3 carries no per-class typing — judging every build"
        " subject it published\n"
    ) in out


def test_entries_with_no_name_are_not_typing(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first entry with no name answers nothing either."""
    read_the_latest_release.enter(
        monkeypatch, manifest=dumps({"entries": [{"type": "x"}]})
    )
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 0, out + err
    assert "manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n" in written


def test_no_manifest_judges_the_whole_release(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release older than the manifest keeps the whole-set comparison."""
    read_the_latest_release.enter(monkeypatch)
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 0, out + err
    assert "manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n" in written
    assert "carries no per-class typing" in out


def test_latest_release_that_is_not_a_v_release(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused by name, nothing written."""
    read_the_latest_release.enter(monkeypatch, "nightly")
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 1, out + err
    assert err == (
        "::error::repro-resolve: latest release tag nightly is not a v* release\n"
    )
    assert written == ""


def test_empty_checksums(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to prove against is refused."""
    read_the_latest_release.enter(monkeypatch, checksums="")
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 1, out + err
    assert err == (
        "::error::repro-resolve: release v1.2.3 has no checksums.txt — nothing"
        " to prove against\n"
    )
    assert written == ""


def test_absent_checksums_fails_with_gh(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release with no checksums asset fails the download, as gh does."""
    read_the_latest_release.enter(monkeypatch, checksums=None)
    with pytest.raises(SystemExit) as stopped:
        repro_resolve.main([])
    assert stopped.value.code == 1
    assert "release asset not found" in capfd.readouterr().err
    assert read_the_latest_release.written() == ""


def test_manifest_that_is_not_json(
    read_the_latest_release: ReadTheLatestRelease,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest that cannot be read refuses rather than guessing."""
    read_the_latest_release.enter(monkeypatch, manifest="{")
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 1, out + err
    assert err.startswith(
        "::error::repro-resolve: evidence-manifest.json is not JSON:"
    ), err
    assert written == ""


@pytest.mark.parametrize("entries", [True, {"": None}, "abc"])
def test_manifest_whose_entries_are_not_a_list(
    read_the_latest_release: ReadTheLatestRelease,
    entries: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused by name (#1556's shrunk examples), nothing written.

    `true` died with a TypeError and `{"": null}` with a KeyError.
    """
    read_the_latest_release.enter(monkeypatch, manifest=dumps({"entries": entries}))
    status = repro_resolve.main([])
    out, err = capfd.readouterr()
    written = read_the_latest_release.written()
    assert status == 1, out + err
    assert err == (
        "::error::repro-resolve: evidence-manifest.json is not an"
        " evidence manifest:"
        " its entries are not a list\n"
    )
    assert written == ""

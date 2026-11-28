"""Run `belt/release/attach_publish.py` against a stub `gh` (#1386).

`attach-release.yml`'s "Attach and publish" step is now one call to the
helper. These rows call its `main` in this process (#1698), from a
working directory holding `dist/`, with a stub `gh` first on PATH that
records every call and answers from the row's fixtures: the release's
draft state, the published `checksums.txt` and, when the release has
one, its evidence manifest. Every refusal row asserts the step's
message. One row starts the helper as the step does instead: the
script contract, and the one row whose assertions read `diff`'s own
output, which only a child's stdout carries.

The rehearsal row runs the real `release/record-draft.sh`, so the
record it writes is the one a kept draft carries.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from belt.release import attach_publish
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

HERE = support.RELEASE
COMMAND = "attach-publish"
TAG = "v1.2.3"
REPO = "example/project"
# Answers by subcommand; every call is logged one per line, its
# arguments NUL-separated.
STUB_GH = """#!/usr/bin/env bash
printf '%s\\0' "$@" >> "${GH_LOG}"
echo >> "${GH_LOG}"
case "$1 $2" in
  "release view")
    case " $* " in
      *" isDraft "*) echo "${STUB_DRAFT}" ;;
      *" body "*) echo "" ;;
    esac
    ;;
  "release download")
    case " $* " in
      *" checksums.txt "*) cp "${STUB_FIXTURES}/checksums.txt" "${@: -1}" ;;
      *" evidence-manifest.json "*)
        [[ -f ${STUB_FIXTURES}/evidence-manifest.json ]] || {
          echo "release asset not found" >&2
          exit 1
        }
        cp "${STUB_FIXTURES}/evidence-manifest.json" "${@: -1}"
        ;;
    esac
    ;;
  "release edit")
    for ((i = 1; i <= $#; i++)); do
      [[ ${!i} != --notes-file ]] || { j=$((i + 1)); cp "${!j}" "${GH_LOG}.notes"; }
    done
    ;;
esac
"""
REBUILT = (
    "aaaa  tool-1.2.3-linux.tar.gz\n"
    "bbbb  sbom.spdx.json\n"
    "cccc  attestations.intoto.jsonl\n"
)
TYPED = {
    "schema": 5,
    "entries": [
        {"name": "tool-1.2.3-linux.tar.gz", "type": "build-subject"},
        {"name": "sbom.spdx.json", "type": "sbom"},
        {"name": "attestations.intoto.jsonl", "type": "attestation-bundle"},
    ],
}


def subcommands(calls: list[list[str]]) -> list[str]:
    """Name each call by its first two arguments.

    Returns:
        `release view`, `release upload`, and so on, in order.

    """
    return [" ".join(call[:2]) for call in calls]


@pytest.mark.usefixtures("_set_up_test_attach_and_publish")
class TestAttachAndPublish:
    """Each row: the release as published, and what the step did."""

    @fixture
    def _set_up_test_attach_and_publish(self, tmp_path: Path) -> None:
        """Give each row a working directory, fixtures and a stub `gh`."""
        self.tmp = tmp_path
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        gh = self.bindir / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.fixtures = self.tmp / "published"
        self.fixtures.mkdir()
        self.work = self.tmp / "work"
        dist = self.work / "dist"
        dist.mkdir(parents=True)
        (dist / "checksums.txt").write_text(REBUILT, encoding="utf-8")
        for line in REBUILT.splitlines():
            (dist / line.split()[1]).write_text(line, encoding="utf-8")

    def publish(
        self,
        draft: str,
        *,
        published: str = REBUILT,
        manifest: dict[str, object] | None = None,
        dry_run: str = "false",
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str, list[list[str]]]:
        """Run the helper against a release in the given state.

        In this process, as the step's command.

        Returns:
            The status, both streams, and every `gh` call's arguments.

        """
        (self.fixtures / "checksums.txt").write_text(published, encoding="utf-8")
        if manifest is not None:
            (self.fixtures / "evidence-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
        log = self.tmp / "gh-log"
        env = support.env(
            PATH=f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.tmp),
            GH_LOG=str(log),
            STUB_DRAFT=draft,
            STUB_FIXTURES=str(self.fixtures),
            TAG=TAG,
            DRY_RUN=dry_run,
            GITHUB_REPOSITORY=REPO,
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_RUN_ID="42",
            # A workflow step's: the writer names the command (#1934).
            MISE_TASK_NAME="",
        )
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.chdir(self.work)
            patched.setattr(sys, "argv", [COMMAND])
            status = attach_publish.main([])
        out, err = capfd.readouterr()
        lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
        return status, out, err, [line.split("\0")[:-1] for line in lines]

    def test_identical_published_release_converges(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """#316, #586: a resume converges on the build subjects alone."""
        resigned = REBUILT.replace("cccc", "dddd")  # a fresh signature
        status, out, err, calls = self.publish(
            "false", published=resigned, manifest=TYPED, capfd=capfd
        )
        assert status == 0, out + err
        assert out == (
            f"::notice::{TAG} already published; build subjects verified"
            " identical, converging\n"
        )
        assert "release upload" not in subcommands(calls)
        assert "release edit" not in subcommands(calls)

    def test_untyped_release_with_identical_checksums_converges(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A schema-1 release converges on its whole set, uploading nothing (#330)."""
        status, out, err, calls = self.publish(
            "false", manifest={"schema": 1}, capfd=capfd
        )
        assert status == 0, out + err
        assert "converging" in out
        assert "release upload" not in subcommands(calls)

    def test_checksums_differ_without_typing(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No manifest: any differing asset refuses the resume."""
        resigned = REBUILT.replace("cccc", "dddd")
        status, out, err, calls = self.publish("false", published=resigned, capfd=capfd)
        assert status == 1, out + err
        assert "-dddd  attestations.intoto.jsonl" in out
        assert "+cccc  attestations.intoto.jsonl" in out
        assert err.endswith(
            f"::error::attach-publish: {TAG} is published and immutable but"
            f" the rebuilt assets"
            " differ from the published set — this is not a resume\n"
        ), err
        assert "release upload" not in subcommands(calls)

    def test_typed_build_subjects_differ(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """stele#156: a differing build subject refuses, by name."""
        rebuilt_other = REBUILT.replace("aaaa", "ffff")
        status, out, err, _ = self.publish(
            "false", published=rebuilt_other, manifest=TYPED, capfd=capfd
        )
        assert status == 1, out + err
        assert err == (
            f"::error::attach-publish: {TAG} is published and immutable but these"
            f" build subjects"
            " differ from the published bytes: tool-1.2.3-linux.tar.gz — this is"
            " not a resume\n"
        )

    def test_typed_build_subject_missing_from_the_release(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A build subject the published list lacks is a difference."""
        partial = "bbbb  sbom.spdx.json\ncccc  attestations.intoto.jsonl\n"
        status, out, err, _ = self.publish(
            "false", published=partial, manifest=TYPED, capfd=capfd
        )
        assert status == 1, out + err
        assert "differ from the published bytes: tool-1.2.3" in err

    def test_manifest_that_is_not_json(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A manifest that cannot be read refuses rather than guessing."""
        (self.fixtures / "evidence-manifest.json").write_text("{", encoding="utf-8")
        status, out, err, _ = self.publish("false", capfd=capfd)
        assert status == 1, out + err
        assert err.startswith(
            "::error::attach-publish: published-manifest.json is not JSON:"
        ), err

    def test_manifest_whose_entries_are_not_a_list(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """`{"entries": true}` refuses by name (#1556's shrunk example).

        It died with a TypeError before: the entries were iterated
        whatever they were.
        """
        (self.fixtures / "evidence-manifest.json").write_text(
            '{"entries": true}', encoding="utf-8"
        )
        status, out, err, _ = self.publish("false", capfd=capfd)
        assert status == 1, out + err
        assert err == (
            "::error::attach-publish: published-manifest.json is not an evidence"
            " manifest:"
            " its entries are not a list\n"
        )

    def test_draft_uploads_and_publishes(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A draft takes every asset, then is published."""
        status, out, err, calls = self.publish("true", capfd=capfd)
        assert status == 0, out + err
        assert out == f"::notice::published {TAG}\n"
        want: list[str] = ["release view", "release upload", "release edit"]
        assert subcommands(calls) == want
        upload: list[str] = [
            "release", "upload", TAG,
            "dist/attestations.intoto.jsonl", "dist/checksums.txt",
            "dist/sbom.spdx.json", "dist/tool-1.2.3-linux.tar.gz",
            "--clobber", "--repo", REPO,
        ]  # fmt: skip
        assert calls[1] == upload
        edit: list[str] = ["release", "edit", TAG, "--draft=false", "--repo", REPO]
        assert calls[2] == edit

    def test_rehearsal_stays_a_draft_and_records_it(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """#597: a rehearsal uploads, keeps the draft, and says why."""
        status, out, err, calls = self.publish("true", dry_run="true", capfd=capfd)
        assert status == 0, out + err
        assert out.endswith(
            f"::warning::rehearsal: {TAG} left as a draft, nothing published\n"
        ), out
        edits = [call for call in calls if call[:2] == ["release", "edit"]]
        assert len(edits) == 1, calls
        assert "--draft=false" not in edits[0]
        assert "--notes-file" in edits[0]
        notes = (self.tmp / "gh-log.notes").read_text(encoding="utf-8")
        assert "rehearsal" in notes
        assert "https://github.com/example/project/actions/runs/42" in notes

    def test_a_failing_gh_fails_the_step(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A release that cannot be read stops the step with gh's status."""
        (self.bindir / "gh").write_text("#!/usr/bin/env bash\nexit 4\n", "utf-8")
        with pytest.raises(SystemExit) as stopped:
            self.publish("true", capfd=capfd)
        assert stopped.value.code == 4
        assert capfd.readouterr().out == ""

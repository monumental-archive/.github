#!/usr/bin/env python3
"""Run `release/attach-publish.py` against a stub `gh` (#1386).

`attach-release.yml`'s "Attach and publish" step is now one call to the
helper. These rows run it as the step does, from a working directory
holding `dist/`, with a stub `gh` first on PATH that records every call
and answers from the row's fixtures: the release's draft state, the
published `checksums.txt` and, when the release has one, its evidence
manifest. Every refusal row asserts the step's message.

The rehearsal row runs the real `release/record-draft.sh`, so the
record it writes is the one a kept draft carries.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HELPER = HERE / "attach-publish.py"
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


class AttachAndPublish(unittest.TestCase):
    """Each row: the release as published, and what the step did."""

    def setUp(self) -> None:
        """Give each row a working directory, fixtures and a stub `gh`."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
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
        manifest: dict | None = None,
        dry_run: str = "false",
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        """Run the helper against a release in the given state.

        Returns:
            The run, and every `gh` call's arguments.

        """
        (self.fixtures / "checksums.txt").write_text(published, encoding="utf-8")
        if manifest is not None:
            (self.fixtures / "evidence-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
        log = self.tmp / "gh-log"
        env = {
            "PATH": f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "GH_LOG": str(log),
            "STUB_DRAFT": draft,
            "STUB_FIXTURES": str(self.fixtures),
            "TAG": TAG,
            "DRY_RUN": dry_run,
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "42",
        }
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [sys.executable, str(HELPER)],
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
        return done, [line.split("\0")[:-1] for line in lines]

    def test_identical_published_release_converges(self) -> None:
        """#316: a resume into a published release passes through."""
        resigned = REBUILT.replace("cccc", "dddd")  # a fresh signature
        done, calls = self.publish("false", published=resigned, manifest=TYPED)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"::notice::{TAG} already published; build subjects verified"
            " identical, converging\n",
        )
        self.assertNotIn("release upload", subcommands(calls))
        self.assertNotIn("release edit", subcommands(calls))

    def test_untyped_release_with_identical_checksums_converges(self) -> None:
        """A schema-1 release keeps the whole-set comparison, and passes."""
        done, calls = self.publish("false", manifest={"schema": 1})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("converging", done.stdout)
        self.assertNotIn("release upload", subcommands(calls))

    def test_checksums_differ_without_typing(self) -> None:
        """No manifest: any differing asset refuses the resume."""
        resigned = REBUILT.replace("cccc", "dddd")
        done, calls = self.publish("false", published=resigned)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("-dddd  attestations.intoto.jsonl", done.stdout)
        self.assertIn("+cccc  attestations.intoto.jsonl", done.stdout)
        self.assertTrue(
            done.stdout.endswith(
                f"::error::{TAG} is published and immutable but the rebuilt assets"
                " differ from the published set — this is not a resume\n"
            ),
            done.stdout,
        )
        self.assertNotIn("release upload", subcommands(calls))

    def test_typed_build_subjects_differ(self) -> None:
        """stele#156: a differing build subject refuses, by name."""
        rebuilt_other = REBUILT.replace("aaaa", "ffff")
        done, _ = self.publish("false", published=rebuilt_other, manifest=TYPED)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"::error::{TAG} is published and immutable but these build subjects"
            " differ from the published bytes: tool-1.2.3-linux.tar.gz — this is"
            " not a resume\n",
        )

    def test_typed_build_subject_missing_from_the_release(self) -> None:
        """A build subject the published list lacks is a difference."""
        partial = "bbbb  sbom.spdx.json\ncccc  attestations.intoto.jsonl\n"
        done, _ = self.publish("false", published=partial, manifest=TYPED)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("differ from the published bytes: tool-1.2.3", done.stdout)

    def test_manifest_that_is_not_json(self) -> None:
        """A manifest that cannot be read refuses rather than guessing."""
        (self.fixtures / "evidence-manifest.json").write_text("{", encoding="utf-8")
        done, _ = self.publish("false")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue(
            done.stdout.startswith("::error::published-manifest.json is not JSON:"),
            done.stdout,
        )

    def test_manifest_whose_entries_are_not_a_list(self) -> None:
        """`{"entries": true}` refuses by name (#1556's shrunk example).

        It died with a TypeError before: the entries were iterated
        whatever they were.
        """
        (self.fixtures / "evidence-manifest.json").write_text(
            '{"entries": true}', encoding="utf-8"
        )
        done, _ = self.publish("false")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            "::error::published-manifest.json is not an evidence manifest:"
            " its entries are not a list\n",
        )

    def test_draft_uploads_and_publishes(self) -> None:
        """A draft takes every asset, then is published."""
        done, calls = self.publish("true")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"::notice::published {TAG}\n")
        self.assertEqual(
            subcommands(calls), ["release view", "release upload", "release edit"]
        )
        self.assertEqual(
            calls[1],
            [
                "release", "upload", TAG,
                "dist/attestations.intoto.jsonl", "dist/checksums.txt",
                "dist/sbom.spdx.json", "dist/tool-1.2.3-linux.tar.gz",
                "--clobber", "--repo", REPO,
            ],
        )  # fmt: skip
        self.assertEqual(
            calls[2], ["release", "edit", TAG, "--draft=false", "--repo", REPO]
        )

    def test_rehearsal_stays_a_draft_and_records_it(self) -> None:
        """#597: a rehearsal uploads, keeps the draft, and says why."""
        done, calls = self.publish("true", dry_run="true")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                f"::warning::rehearsal: {TAG} left as a draft, nothing published\n"
            ),
            done.stdout,
        )
        edits = [call for call in calls if call[:2] == ["release", "edit"]]
        self.assertEqual(len(edits), 1, calls)
        self.assertNotIn("--draft=false", edits[0])
        self.assertIn("--notes-file", edits[0])
        notes = (self.tmp / "gh-log.notes").read_text(encoding="utf-8")
        self.assertIn("rehearsal", notes)
        self.assertIn("https://github.com/example/project/actions/runs/42", notes)

    def test_a_failing_gh_fails_the_step(self) -> None:
        """A release that cannot be read stops the step with gh's status."""
        (self.bindir / "gh").write_text("#!/usr/bin/env bash\nexit 4\n", "utf-8")
        done, _ = self.publish("true")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Run `release/repro_resolve.py` against a stub `gh` (#1386).

`repro-check.yml`'s "Read the latest release" step is now one call to
the helper. These rows call its `main` in this process (#1698), with a
stub `gh` first on PATH answering from the row's fixtures, and read
back what it wrote to `GITHUB_OUTPUT`. Every refusal row asserts the
step's message. One row starts the helper as the step does instead:
the script contract (it starts, and exits with gh's message).

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import hermetic
import repro_resolve

_NAMED = contextlib.ExitStack()


def setUpModule() -> None:
    """Name the helper by its file, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite.
    """
    _NAMED.enter_context(mock.patch.object(sys, "argv", ["repro_resolve.py"]))
    for key in ("MISE_TASK_NAME", "ORG_FINDINGS"):
        if key in os.environ:
            _NAMED.callback(os.environ.__setitem__, key, os.environ.pop(key))
    # Empty, not unset: unset on a runner records to the job's file (#1976).
    os.environ["ORG_FINDINGS"] = ""
    _NAMED.callback(os.environ.pop, "ORG_FINDINGS", None)
    unittest.addModuleCleanup(_NAMED.close)


def dumps(value: object) -> str:
    """Encode a planted document as JSON.

    Returns:
        The JSON text.

    """
    return json.dumps(value)


HELPER = pathlib.Path(__file__).resolve().parent / "repro_resolve.py"
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


class ReadTheLatestRelease(unittest.TestCase):
    """Each row: the latest release, and what the step handed on."""

    @override
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
        self.work.mkdir()

    def resolve(
        self,
        tag: str = "v1.2.3",
        *,
        checksums: str | None = CHECKSUMS,
        manifest: str | None = None,
        script: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str] | hermetic.Called, str]:
        """Run the helper against a latest release in the given state.

        In this process unless `script`, which starts it as the step does.

        Returns:
            The run, and what it appended to `GITHUB_OUTPUT`.

        """
        if checksums is not None:
            (self.fixtures / "checksums.txt").write_text(checksums, encoding="utf-8")
        if manifest is not None:
            (self.fixtures / "evidence-manifest.json").write_text(
                manifest, encoding="utf-8"
            )
        output = self.tmp / "github-output"
        output.write_text("", encoding="utf-8")
        env = hermetic.env(
            PATH=f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            HOME=str(self.tmp),
            STUB_TAG=tag,
            STUB_FIXTURES=str(self.fixtures),
            GITHUB_REPOSITORY="example/project",
            GITHUB_OUTPUT=str(output),
        )
        done: subprocess.CompletedProcess[str] | hermetic.Called
        if script:
            done = subprocess.run(
                [sys.executable, str(HELPER)],
                cwd=self.work,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        else:

            def call() -> int:
                return repro_resolve.main()

            done = hermetic.call_main(call, env=env, cwd=self.work)
        return done, output.read_text(encoding="utf-8")

    def test_typed_release_hands_its_manifest_on(self) -> None:
        """stele#185: a typed manifest scopes the population."""
        manifest = json.dumps(TYPED, indent=2) + "\n\n"
        done, out = self.resolve(manifest=manifest)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            out,
            f"checksums<<CHECKSUMS_EOF\n{CHECKSUMS}CHECKSUMS_EOF\n"
            f"manifest<<MANIFEST_EOF\n{json.dumps(TYPED, indent=2)}\nMANIFEST_EOF\n"
            "tag=v1.2.3\nversion=1.2.3\n",
        )
        self.assertEqual(
            done.stdout,
            "::notice::v1.2.3 types its entries — the rebuilt class scopes the"
            " population\n::notice::proving v1.2.3 reproduces\n",
        )

    def test_schema_one_manifest_is_not_handed_on(self) -> None:
        """An untyped manifest would seal CANNOT_JUDGE (exit 4): withheld."""
        done, out = self.resolve(manifest=dumps({"schema": 1}))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n", out)
        self.assertIn(
            "::notice::v1.2.3 carries no per-class typing — judging every build"
            " subject it published\n",
            done.stdout,
        )

    def test_entries_with_no_name_are_not_typing(self) -> None:
        """A first entry with no name answers nothing either."""
        done, out = self.resolve(manifest=dumps({"entries": [{"type": "x"}]}))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n", out)

    def test_no_manifest_judges_the_whole_release(self) -> None:
        """A release older than the manifest keeps the whole-set comparison."""
        done, out = self.resolve()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("manifest<<MANIFEST_EOF\n\nMANIFEST_EOF\n", out)
        self.assertIn("carries no per-class typing", done.stdout)

    def test_latest_release_that_is_not_a_v_release(self) -> None:
        """Refused by name, nothing written."""
        done, out = self.resolve("nightly")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            "::error::repro_resolve.py: latest release tag nightly is not a v*"
            " release\n",
        )
        self.assertEqual(out, "")

    def test_empty_checksums(self) -> None:
        """Nothing to prove against is refused."""
        done, out = self.resolve(checksums="")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            "::error::repro_resolve.py: release v1.2.3 has no checksums.txt — nothing"
            " to prove against\n",
        )
        self.assertEqual(out, "")

    def test_absent_checksums_fails_with_gh(self) -> None:
        """A release with no checksums asset fails the download, as gh does.

        The script row: gh's message is on the child's own stderr.
        """
        done, out = self.resolve(checksums=None, script=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("release asset not found", done.stderr)
        self.assertEqual(out, "")

    def test_manifest_that_is_not_json(self) -> None:
        """A manifest that cannot be read refuses rather than guessing."""
        done, out = self.resolve(manifest="{")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertTrue(
            done.stderr.startswith(
                "::error::repro_resolve.py: evidence-manifest.json is not JSON:"
            ),
            done.stderr,
        )
        self.assertEqual(out, "")

    def test_manifest_whose_entries_are_not_a_list(self) -> None:
        """Refused by name (#1556's shrunk examples), nothing written.

        `true` died with a TypeError and `{"": null}` with a KeyError.
        """
        for entries in (True, {"": None}, "abc"):
            with self.subTest(entries=entries):
                done, out = self.resolve(manifest=dumps({"entries": entries}))
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(
                    done.stderr,
                    "::error::repro_resolve.py: evidence-manifest.json is not an"
                    " evidence manifest:"
                    " its entries are not a list\n",
                )
                self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()

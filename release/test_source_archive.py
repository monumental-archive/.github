#!/usr/bin/env python3
"""Run release/source_archive.py, the source-archive build, on a stub git.

#1384 moved the step "Build the archive twice and prove determinism"
out of build-source-archive.yml into the helper, refusals unchanged.

WHAT RUNS. The helper's `main`, called in this process (#1698), with a
stub `git` first on PATH. One row starts the helper as the job runs it
instead, through a `.org-canon` link to this tree: the script contract
(it starts, and exits with git's own status and message). The
stub answers `archive` by copying the row's planted .tar.gz for each
call in turn (the first build, then the check build) to the `-o` path,
and `ls-files` with the row's count of tracked paths. The archives are
real gzip tarballs, so the helper counts real entries.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import contextlib
import gzip
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import hermetic
import source_archive
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "build-source-archive.yml"
HELPER = ".org-canon/release/source_archive.py"
VERSION = "1.3.4"

GIT_STUB = """#!/usr/bin/env bash
# Stub `git`: `archive ... -o OUT REF` copies the next planted archive;
# `ls-files` prints STUB_TRACKED lines; STUB_FAIL fails `archive`.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
case $1 in
  ls-files) for ((i = 0; i < STUB_TRACKED; i++)); do echo "file${i}"; done ;;
  archive)
    if [[ -n ${STUB_FAIL} ]]; then echo "fatal: not a valid object" >&2; exit 128; fi
    n=$(cat "${STUB_COUNTER}" 2> /dev/null || echo 0)
    echo $((n + 1)) > "${STUB_COUNTER}"
    out=""
    while [[ $# -gt 0 ]]; do
      if [[ $1 == -o ]]; then out=$2; fi
      shift
    done
    cp "${STUB_PLANTED}/${n}.tar.gz" "${out}"
    ;;
  *) echo "git stub: refusing $*" >&2; exit 2 ;;
esac
"""


_NAMED = contextlib.ExitStack()


def setUpModule() -> None:
    """Name the helper by its file, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite.
    """
    _NAMED.enter_context(mock.patch.object(sys, "argv", ["source_archive.py"]))
    for key in ("MISE_TASK_NAME", "ORG_FINDINGS"):
        if key in os.environ:
            _NAMED.callback(os.environ.__setitem__, key, os.environ.pop(key))
    unittest.addModuleCleanup(_NAMED.close)


def tarball(entries: int, salt: str = "") -> bytes:
    """Make a gzip tarball with a number of file entries.

    Returns:
        Its bytes, identical for identical arguments.

    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for i in range(entries):
            data = f"{salt}{i}\n".encode()
            info = tarfile.TarInfo(f"p/file{i}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue(), mtime=0)


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    git: list[str]
    shipped: list[str]


def run_helper(
    archives: tuple[bytes, bytes],
    tracked: int,
    repo: str = "monumental-archive/edtf",
    *,
    fail: bool = False,
    script: bool = False,
) -> Run:
    """Run the helper with two planted builds and a tracked count.

    In this process unless `script`, which starts it as the job does.

    Returns:
        Its status, output, the git log and what dist/ holds.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, planted, work = tmp / "bin", tmp / "planted", tmp / "work"
        for p in (stubs, planted, work):
            p.mkdir()
        (stubs / "git").write_text(GIT_STUB)
        (stubs / "git").chmod(0o755)
        hermetic.canon(work / ".org-canon")
        for i, data in enumerate(archives):
            (planted / f"{i}.tar.gz").write_bytes(data)
        log = tmp / "git.log"
        env = hermetic.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            VERSION=VERSION,
            GITHUB_REPOSITORY=repo,
            STUB_LOG=str(log),
            STUB_COUNTER=str(tmp / "counter"),
            STUB_PLANTED=str(planted),
            STUB_TRACKED=str(tracked),
            STUB_FAIL="1" if fail else "",
        )
        result: subprocess.CompletedProcess[str] | hermetic.Called
        if script:
            result = subprocess.run(
                [HELPER],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=work,
            )
        else:

            def call() -> int:
                return source_archive.main()

            result = hermetic.call_main(call, env=env, cwd=work)
        dist = work / "dist"
        return Run(
            result.returncode,
            result.stdout,
            result.stderr,
            log.read_text().splitlines() if log.exists() else [],
            sorted(p.name for p in dist.iterdir()) if dist.is_dir() else [],
        )


def sha(data: bytes) -> str:
    """Hash planted bytes.

    Returns:
        The hex sha256.

    """
    return hashlib.sha256(data).hexdigest()


class BuildStepIsTheHelper(unittest.TestCase):
    """The job runs the helper these rows run."""

    def test_the_shipped_step_calls_the_helper(self) -> None:
        """The canon tree is linked first, and the run: is the call alone."""
        document = workflow_yaml.document(WORKFLOW)
        ((before, step),) = [
            (job[:i], s)
            for job in map(workflow_yaml.steps, workflow_yaml.jobs(document).values())
            for i, s in enumerate(job)
            if s.get("name") == "Build the archive twice and prove determinism"
        ]
        # Under the belt's Python (#1543), on one line.
        self.assertEqual(f'"${{ORG_PYTHON}}" {HELPER}', step["run"])
        used: list[object] = [s.get("uses") for s in before]
        self.assertIn("$/.github/actions/canon", used)


class Builds(unittest.TestCase):
    """Two equal builds of the tagged tree pass."""

    def test_two_equal_builds_ship_one_archive(self) -> None:
        """Both builds archive the tag under the release's prefix."""
        data = tarball(5)
        run = run_helper((data, data), tracked=5)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        base = f"edtf-{VERSION}"
        self.assertEqual(
            f"::notice::{base}.tar.gz: 5 entries, digest {sha(data)}\n", run.stdout
        )
        shipped: list[str] = [f"{base}.tar.gz"]
        self.assertEqual(shipped, run.shipped)
        archives = [x for x in run.git if x.startswith("archive")]
        self.assertEqual(2, len(archives))
        self.assertEqual(
            f"archive --format=tar.gz --prefix={base}/ -o dist/{base}.tar.gz"
            f" v{VERSION}",
            archives[0],
        )
        self.assertTrue(archives[1].endswith(f"/{base}.check.tar.gz v{VERSION}"))

    def test_a_dot_repository_gets_a_visible_name(self) -> None:
        """The .github repository's asset is github-<version>.tar.gz."""
        data = tarball(2)
        run = run_helper((data, data), tracked=2, repo="monumental-archive/.github")
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        shipped: list[str] = [f"github-{VERSION}.tar.gz"]
        self.assertEqual(shipped, run.shipped)

    def test_more_than_half_the_tracked_files_is_enough(self) -> None:
        """Four entries for seven tracked files: 4 > 7 // 2."""
        data = tarball(4)
        run = run_helper((data, data), tracked=7)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)


class Refuses(unittest.TestCase):
    """One row per refusal, each message asserted."""

    def test_two_builds_that_differ_are_refused(self) -> None:
        """The archive is not reproducible, so nothing can check it."""
        a, b = tarball(5), tarball(5, salt="x")
        run = run_helper((a, b), tracked=5)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            f"::error::source_archive.py: source archive is not deterministic"
            f" ({sha(a)} vs {sha(b)})\n",
            run.stderr,
        )
        self.assertNotIn("ls-files", run.git)

    def test_half_the_tracked_files_is_refused(self) -> None:
        """Three entries for seven tracked files: 3 <= 7 // 2."""
        data = tarball(3)
        run = run_helper((data, data), tracked=7)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::source_archive.py: archive lists 3 entries for 7 tracked files\n",
            run.stderr,
        )

    def test_an_empty_archive_is_refused(self) -> None:
        """No entries at all is a truncated stream."""
        data = tarball(0)
        run = run_helper((data, data), tracked=4)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::source_archive.py: archive lists 0 entries for 4 tracked files\n",
            run.stderr,
        )

    def test_a_failing_git_fails_the_step_with_its_status(self) -> None:
        """A tag git cannot archive: git's status and message.

        The script row: git's message is on the child's own stderr.
        """
        data = tarball(1)
        run = run_helper((data, data), tracked=1, fail=True, script=True)
        self.assertEqual(128, run.returncode)
        self.assertEqual("fatal: not a valid object\n", run.stderr)
        self.assertEqual("", run.stdout)


if __name__ == "__main__":
    unittest.main()

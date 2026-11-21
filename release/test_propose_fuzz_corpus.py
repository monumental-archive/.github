#!/usr/bin/env python3
"""Table tests for the fuzz-corpus proposal and the signed commit (#1897).

`propose-fuzz-corpus.sh` turns the scheduled search's artifact into a
pull request. The artifact was written by the job that ran the
repository's code, so the rows that matter most are its refusals: a
path outside the two corpus shapes, a `..` segment and a symbolic link
each refuse the whole artifact, by name, with nothing committed. The
script runs against a stubbed `gh` that answers every call and records
each one, so a row asserts exactly what was written.

`signed-commit.sh` is the one signed-commit call, shared with
open-release-pr.sh (whose own rows are test_release_guards.py's): one
row each for a signed and an unsigned answer.

Each row asserts the message, not just the exit status.

stdlib `unittest`, the belt's one test idiom.
"""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic

RELEASE = Path(__file__).resolve().parent
PROPOSE = RELEASE / "propose-fuzz-corpus.sh"
SIGNED = RELEASE / "signed-commit.sh"
BASH = hermetic.tool("bash")
REPO = "monumental-archive/under-test"
OID = "c" * 40
MAIN = "a" * 40
BRANCH_HEAD = "b" * 40

# gh: every call logged, one line each. A ref read answers from
# ${STATE}/refs (`<ref> <sha>` lines) or fails; the commit read answers
# ${STATE}/verified (default true); the commit mutation keeps its
# request and answers ${STATE}/oid (default OID).
GH = """\
#!/usr/bin/env bash
set -uo pipefail
log() { printf '%s\\n' "$*" >> "${STATE}/calls"; }
headers() {
  [[ " $* " == *" --include "* ]] || return 0
  printf 'HTTP/2.0 200 OK\\r\\n\\r\\n'
}
if [[ $1 == api && $2 == graphql ]]; then
  cat > "${STATE}/commit-request"
  log graphql createCommitOnBranch
  headers "$@"
  cat "${STATE}/oid" 2> /dev/null || echo "${OID}"
  exit 0
fi
if [[ $1 == api ]]; then
  path=$2 method=GET
  for ((i = 1; i <= $#; i++)); do
    [[ ${!i} != --method ]] || { j=$((i + 1)); method=${!j}; }
  done
  log "${method} ${path}"
  case "${method} ${path}" in
    "GET repos/"*"/git/ref/heads/"*)
      ref=${path#*/git/ref/heads/}
      sha=$(awk -v r="${ref}" '$1 == r { print $2 }' "${STATE}/refs" 2> /dev/null)
      [[ -n ${sha} ]] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
      headers "$@"
      echo "${sha}"
      ;;
    "GET users/"*) headers "$@"; echo 4242 ;;
    "GET repos/"*"/commits/"*)
      headers "$@"
      cat "${STATE}/verified" 2> /dev/null || echo true
      ;;
    *) headers "$@" ;;
  esac
  exit 0
fi
if [[ $1 == pr ]]; then
  log pr "$2"
  if [[ $2 == list ]]; then cat "${STATE}/open" 2> /dev/null; fi
  while [[ $# -gt 0 ]]; do
    [[ $1 != --body ]] || printf '%s' "$2" > "${STATE}/body"
    shift
  done
  exit 0
fi
echo "gh stub: refusing '$*'" >&2
exit 1
"""


def same(case: unittest.TestCase, want: list[str], got: list[str]) -> None:
    """Assert two lists of strings are equal, typed so a literal is too."""
    case.assertEqual(want, got)


class Forge(unittest.TestCase):
    """A scratch directory with the stubbed gh first on PATH."""

    @override
    def setUp(self) -> None:
        """Lay out the stub, its state and an empty artifact."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.artifact = self.tmp / "artifact"
        self.artifact.mkdir()
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "gh").write_text(GH, encoding="utf-8")
        (stubs / "gh").chmod(0o755)
        (self.state / "refs").write_text(f"main {MAIN}\n", encoding="utf-8")
        env = hermetic.env(self.tmp / "home")
        self.env = env | {
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STATE": str(self.state),
            "OID": OID,
            # Not a credential: the stub reads none.
            "GH_TOKEN": "stub",
            "GITHUB_REPOSITORY": REPO,
            "DEFAULT_BRANCH": "main",
            "APP_SLUG": "org-tag-mint",
            "CANDIDATES": str(self.artifact),
            "SUMMARY": "- `parse_marker`: corpus 10% to 11%",
        }

    def plant(self, path: str, text: str = "x") -> Path:
        """Write a file into the artifact at a repository path.

        Returns:
            Its path.

        """
        where = self.artifact / path
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(text, encoding="utf-8")
        return where

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run propose-fuzz-corpus.sh.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, str(PROPOSE), *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[str]:
        """Read back the forge calls, in order.

        Returns:
            One line per call; none when it made none.

        """
        log = self.state / "calls"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def commit(self) -> dict[str, object]:
        """Read the createCommitOnBranch input the run sent.

        Returns:
            The mutation's input.

        """
        text = (self.state / "commit-request").read_text(encoding="utf-8")
        sent = decoded.as_object(decoded.load_json(text), "request")
        variables = decoded.as_object(sent["variables"], "variables")
        return decoded.as_object(variables["input"], "input")


class Refusals(Forge):
    """The artifact's paths are input: anything else refuses all of it."""

    def test_a_path_outside_the_corpus_shapes_is_refused_by_name(self) -> None:
        """A workflow file beside a good seed: the whole artifact is refused."""
        self.plant("fuzz/corpus/parse_marker/abc")
        self.plant(".github/workflows/x.yml")
        result = self.run_script("propose")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual(
            "FAIL: refused .github/workflows/x.yml: outside"
            " fuzz/corpus/<target>/<file> and <dirs>/testdata/fuzz/Fuzz<Name>/<file>\n"
            "FAIL: the artifact is refused whole; nothing is committed\n",
            result.stderr,
        )
        same(self, [], self.calls())

    def test_a_symbolic_link_is_refused_by_name(self) -> None:
        """A link at a corpus path is refused, and nothing is committed."""
        (self.artifact / "fuzz/corpus/t").mkdir(parents=True)
        (self.artifact / "fuzz/corpus/t/abc").symlink_to("/etc/passwd")
        result = self.run_script("propose")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(
            "FAIL: refused fuzz/corpus/t/abc: a symbolic link\n", result.stderr
        )
        self.assertIn("nothing is committed", result.stderr)
        same(self, [], self.calls())

    def test_a_dot_dot_segment_is_refused_by_name(self) -> None:
        """No filesystem entry is named `..`, so the reader is held directly."""
        rows = (
            ("fuzz/corpus/../x", "a '..' segment"),
            ("fuzz/corpus/t/..", "a '..' segment"),
            ("a/./testdata/fuzz/FuzzX/f", "a '.' segment"),
            ("fuzz/corpus/t/a b", "a segment outside [A-Za-z0-9._-]"),
            (
                "fuzz/corpus/t/x/y",
                (
                    "outside fuzz/corpus/<target>/<file> and"
                    " <dirs>/testdata/fuzz/Fuzz<Name>/<file>"
                ),
            ),
            ("fuzz/corpus/t/abc", ""),
            ("internal/policy/testdata/fuzz/FuzzLoad/crash-abc", ""),
            ("testdata/fuzz/FuzzLoad/abc", ""),
        )
        for path, why in rows:
            with self.subTest(path=path):
                said = subprocess.run(
                    [BASH, "-c", f'. "{PROPOSE}"; corpus_path_refusal "$1"', "t", path],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                self.assertEqual(why, said.strip())

    def test_an_empty_artifact_proposes_nothing(self) -> None:
        """No file: refused before any call."""
        result = self.run_script("check")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual(
            "FAIL: the artifact holds no file; nothing to propose\n", result.stderr
        )
        same(self, [], self.calls())


class Proposal(Forge):
    """One signed commit on fuzz-corpus, then the pull request."""

    def test_a_new_branch_is_cut_from_the_default_head(self) -> None:
        """No fuzz-corpus: created at main's head, committed, PR opened."""
        self.plant("fuzz/corpus/parse_marker/abc", "raise")
        self.plant(
            "internal/policy/testdata/fuzz/FuzzLoad/crash-f00d", "go test fuzz v1\n"
        )
        result = self.run_script("propose")
        self.assertEqual(0, result.returncode, result.stderr)
        same(
            self,
            [
                f"GET repos/{REPO}/git/ref/heads/fuzz-corpus",
                f"GET repos/{REPO}/git/ref/heads/main",
                f"POST repos/{REPO}/git/refs",
                "GET users/org-tag-mint%5Bbot%5D",
                "graphql createCommitOnBranch",
                f"GET repos/{REPO}/commits/{OID}",
                "pr list",
                "pr create",
            ],
            self.calls(),
        )
        sent = self.commit()
        self.assertEqual(MAIN, sent["expectedHeadOid"])
        branch = decoded.as_object(sent["branch"], "branch")
        self.assertEqual(REPO, branch["repositoryNameWithOwner"])
        self.assertEqual("fuzz-corpus", branch["branchName"])
        message = decoded.as_object(sent["message"], "message")
        self.assertEqual(
            "test(fuzz): add inputs that reach new code", message["headline"]
        )
        self.assertEqual(
            "Signed-off-by: org-tag-mint[bot]"
            " <4242+org-tag-mint[bot]@users.noreply.github.com>",
            message["body"],
        )
        changes = decoded.as_object(sent["fileChanges"], "fileChanges")
        added = [
            decoded.as_object(entry, "addition")
            for entry in decoded.as_array(changes["additions"], "additions")
        ]
        same(
            self,
            [
                "fuzz/corpus/parse_marker/abc",
                "internal/policy/testdata/fuzz/FuzzLoad/crash-f00d",
            ],
            [decoded.as_str(entry["path"], "path") for entry in added],
        )
        same(
            self,
            [
                base64.b64encode(b"raise").decode(),
                base64.b64encode(b"go test fuzz v1\n").decode(),
            ],
            [decoded.as_str(entry["contents"], "contents") for entry in added],
        )
        self.assertFalse(decoded.as_array(changes["deletions"], "deletions"))
        body = (self.state / "body").read_text(encoding="utf-8")
        self.assertIn("- `parse_marker`: corpus 10% to 11%", body)
        self.assertIn(
            f"committed {OID} to fuzz-corpus, signature verified", result.stdout
        )

    def test_an_unmerged_proposal_is_built_on_and_its_pr_updated(self) -> None:
        """fuzz-corpus exists: committed on its head, the open PR edited."""
        self.plant("fuzz/corpus/parse_marker/abc")
        (self.state / "refs").write_text(
            f"main {MAIN}\nfuzz-corpus {BRANCH_HEAD}\n", encoding="utf-8"
        )
        (self.state / "open").write_text("17\n", encoding="utf-8")
        result = self.run_script("propose")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(BRANCH_HEAD, self.commit()["expectedHeadOid"])
        self.assertNotIn(f"POST repos/{REPO}/git/refs", self.calls())
        same(self, ["pr list", "pr edit"], self.calls()[-2:])
        self.assertIn("updated PR #17", result.stdout)


class SignedCommit(Forge):
    """The shared helper: a commit GitHub signed, or a named refusal."""

    def helper(self) -> subprocess.CompletedProcess[str]:
        """Write one commit through org_signed_commit.

        Returns:
            The finished process.

        """
        changes = self.tmp / "changes.json"
        changes.write_text('{"additions": [], "deletions": []}', encoding="utf-8")
        return subprocess.run(
            [
                BASH,
                "-c",
                (
                    f'. "{SIGNED}"; '
                    'org_signed_commit "fuzz corpus commit" b "$1" h "" "$2"'
                ),
                "t",
                MAIN,
                str(changes),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_signed_answer_prints_the_commit(self) -> None:
        """GitHub reports the commit verified: its oid is printed."""
        result = self.helper()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"{OID}\n", result.stdout)
        self.assertEqual(MAIN, self.commit()["expectedHeadOid"])

    def test_an_unsigned_answer_is_refused_by_name(self) -> None:
        """GitHub reports it unverified: red, naming the commit."""
        (self.state / "verified").write_text("false\n", encoding="utf-8")
        result = self.helper()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual("", result.stdout)
        self.assertEqual(
            f"FAIL: fuzz corpus commit {OID} is unsigned;"
            " the PR would be unmergeable\n",
            result.stderr,
        )

    def test_no_commit_in_the_answer_is_refused(self) -> None:
        """An empty oid is never read back as a commit."""
        (self.state / "oid").write_text("", encoding="utf-8")
        result = self.helper()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual("FAIL: the API returned no commit\n", result.stderr)


if __name__ == "__main__":
    unittest.main()

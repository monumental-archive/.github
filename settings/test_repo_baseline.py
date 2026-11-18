#!/usr/bin/env python3
"""Tests for repo-baseline.sh's drift check, against a stubbed forge (#1360).

The script reads every repository in the org and compares it with the
baseline. Each row here runs the real script in `check` mode with a
stand-in `gh` first on PATH: it answers each endpoint from a fixture
the row lays out, answers anything else the way the forge answers a
missing object, and records every call with the token it carried. No
row makes a live settings read or write.

The clean org every row starts from is built from the canon's own
files: the roster in `slsa/assert-policy.json`, the keys and labels in
`settings/repo-baseline.json`, and the ruleset in
`settings/merge-queue.json`. A row then changes one thing and asserts
the script's own words about it.

Each row carries the issue or pull request of the fix it holds, and
fails on its assertion with that fix's code reverted (#1360).

stdlib `unittest`, matching mise/test_*.py. Run through the gate as
`mise run test`, which `ci` collects.
"""

import base64
import json
import os
import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().parents[1] / "mise" / "hermetic.py"))
)


HERE = Path(__file__).resolve().parent
CANON = HERE.parent
SCRIPT = HERE / "repo-baseline.sh"
BASH = shutil.which("bash") or "bash"
ORG = "monumental-archive"

BASELINE = json.loads((HERE / "repo-baseline.json").read_text(encoding="utf-8"))
QUEUE = json.loads((HERE / "merge-queue.json").read_text(encoding="utf-8"))
ROSTER = [
    r["repo"]
    for r in json.loads((CANON / "slsa" / "assert-policy.json").read_text())[
        "population"
    ]["repositories"]
]
APPS = ("renovate", "monumental-archive-tag-mint", "codecov")
LISTING = f"orgs/{ORG}/repos?per_page=100"
PACKAGES = f"orgs/{ORG}/packages?package_type=container&per_page=100"

# The stand-in forge. A fixture file named after the call answers it
# (`--jq` applied as gh applies it); `<key>.err` is printed to stderr
# and fails the call; anything else is the forge's 404.
GH_STUB = r"""#!/usr/bin/env bash
set -uo pipefail
printf '%s\t%s\n' "${GH_TOKEN-}" "$*" >> "${STUB}/calls"
if [[ ${1:-} != api ]]; then
  echo "gh: GraphQL is refused to this token (HTTP 401)" >&2
  exit 1
fi
endpoint=$2
shift 2
filter=""
method=GET
while [[ $# -gt 0 ]]; do
  case $1 in
    --jq) filter=$2; shift ;;
    -X | --method) method=$2; shift ;;
  esac
  shift
done
key=$(printf '%s %s' "${method}" "${endpoint}" | tr '/ ?=&' '_____')
file="${STUB}/r/${key}"
if [[ -f ${file}.err ]]; then
  cat "${file}.err" >&2
  exit 1
fi
if [[ ! -f ${file} ]]; then
  echo "gh: Not Found (HTTP 404)" >&2
  exit 1
fi
if [[ -n ${filter} ]]; then
  jq -r "${filter}" "${file}"
else
  cat "${file}"
fi
"""


def key(endpoint: str, method: str = "GET") -> str:
    """Name the fixture file the stub reads for one call.

    Returns:
        The call's method and endpoint with path punctuation flattened.

    """
    return re.sub(r"[/ ?=&]", "_", f"{method} {endpoint}")


class Forge:
    """A clean org on disk, and the stub that serves it."""

    def __init__(self, root: Path) -> None:
        """Lay out the stub and a fixture for every read a clean check makes."""
        self.root = root
        self.answers = root / "r"
        self.answers.mkdir(parents=True)
        bindir = root / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(GH_STUB, encoding="utf-8")
        (bindir / "gh").chmod(0o755)
        self.bindir = bindir
        self.answer(LISTING, [{"name": r} for r in ROSTER])
        for repo in ROSTER:
            settings = {k: v for k, v in BASELINE.items() if k != "labels"}
            settings |= {
                "web_commit_signoff_required": True,
                "default_branch": "main",
                "visibility": "public",
            }
            self.answer(f"repos/{ORG}/{repo}", settings)
            for label in BASELINE["labels"]:
                self.answer(f"repos/{ORG}/{repo}/labels/{label['name']}", label)
            self.answer(
                f"repos/{ORG}/{repo}/actions/oidc/customization/sub",
                {"use_immutable_subject": True},
            )
            self.refuse(
                f"repos/{ORG}/{repo}/branches/main/protection",
                "gh: Branch not protected (HTTP 404)",
            )
            self.answer(
                f"repos/{ORG}/{repo}/rulesets?includes_parents=false",
                [
                    {
                        "id": 7,
                        "name": QUEUE["name"],
                        "target": QUEUE["target"],
                        "enforcement": QUEUE["enforcement"],
                        "source_type": "Repository",
                    }
                ],
            )
            self.answer(f"repos/{ORG}/{repo}/rulesets/7", QUEUE)
        self.answer(
            f"orgs/{ORG}/installations?per_page=100",
            {
                "installations": [
                    {"app_slug": a, "repository_selection": "all"} for a in APPS
                ]
            },
        )
        self.answer(PACKAGES, [])

    def answer(self, endpoint: str, body: object) -> None:
        """Serve `body` as JSON for a GET of `endpoint`."""
        (self.answers / key(endpoint)).write_text(json.dumps(body), encoding="utf-8")

    def refuse(self, endpoint: str, said: str) -> None:
        """Fail a GET of `endpoint` with gh's one line `said`."""
        (self.answers / key(endpoint)).unlink(missing_ok=True)
        (self.answers / (key(endpoint) + ".err")).write_text(
            said + "\n", encoding="utf-8"
        )

    def publish_yml(self, repo: str, text: str) -> None:
        """Serve a publish.yml for `repo` the way the contents API does."""
        encoded = base64.b64encode(text.encode()).decode()
        self.answer(
            f"repos/{ORG}/{repo}/contents/.github/workflows/publish.yml",
            {"content": encoded},
        )

    def check(self) -> subprocess.CompletedProcess[str]:
        """Run `repo-baseline.sh check` against this forge.

        Returns:
            The completed process; drift is the thing under test.

        """
        env = hermetic.env()
        env |= {
            "PATH": f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            "STUB": str(self.root),
            "GH_TOKEN": "audit-token",
            "PACKAGES_TOKEN": "packages-token",
        }
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(SCRIPT), "check"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[tuple[str, str]]:
        """Read back every call the script made, with the token it carried.

        Returns:
            (token, arguments) per call, in order.

        """
        log = self.root / "calls"
        if not log.exists():
            return []
        return [
            tuple(line.split("\t", 1))  # type: ignore[misc]
            for line in log.read_text(encoding="utf-8").splitlines()
        ]


class ForgeCase(unittest.TestCase):
    """A row with a fresh clean org."""

    def setUp(self) -> None:
        """Build the clean org in a temporary directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.forge = Forge(Path(tmp.name))


class CleanOrg(unittest.TestCase):
    """What a check of a clean org reads and says: one run, three rows."""

    @classmethod
    def setUpClass(cls) -> None:
        """Check the clean org once; every row reads the same run."""
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.forge = Forge(Path(tmp.name))
        cls.done = cls.forge.check()

    def test_a_clean_check_says_what_it_checked(self) -> None:
        """A clean run names its coverage; silence is no proof (#311).

        A check that exited 0 printing nothing could not be told apart
        from one that checked nothing.
        """
        done = self.done
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertRegex(
            done.stdout,
            rf"\Arepo-baseline: {len(ROSTER)} repos checked \(.*\), no drift\n\Z",
        )

    def test_the_listing_is_read_over_rest(self) -> None:
        """The population is read from the REST API, never `gh repo list` (#30).

        `gh repo list` is GraphQL, and the audit's fine-grained token
        supports only REST: the listing 401'd and the check ran on
        nothing.
        """
        done = self.done
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        args = [a for _, a in self.forge.calls()]
        self.assertIn(f"api {LISTING} --paginate --jq .[].name", args)
        self.assertFalse(any(a.startswith("repo ") for a in args), args)

    def test_packages_are_read_with_the_packages_token(self) -> None:
        """The container listing carries PACKAGES_TOKEN, not the audit token (#194).

        The org's packages API needs a classic token with
        read:packages; the audit token is a fine-grained one scoped to
        an environment, and cannot read them.
        """
        tokens = [t for t, a in self.forge.calls() if PACKAGES in a]
        self.assertEqual(["packages-token"], tokens)


class PublishEnvironment(ForgeCase):
    """Only an ENTRY publish.yml needs a publish environment (#104)."""

    def test_a_reusable_publish_yml_needs_no_environment(self) -> None:
        """A `workflow_call` publish.yml is the canon's shared one, not an entry.

        The probe matched the canon's reusable publish.yml and demanded
        an environment of the repository that publishes nothing.
        """
        repo = ROSTER[0]
        self.forge.publish_yml(repo, "on:\n  workflow_call:\n    inputs: {}\n")
        done = self.forge.check()
        self.assertNotIn(
            f"drift: {repo} has publish.yml but no publish environment", done.stdout
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_an_entry_publish_yml_without_an_environment_drifts(self) -> None:
        """The other half: a tag-triggered caller stub does need one."""
        repo = ROSTER[0]
        self.forge.publish_yml(repo, "on:\n  push:\n    tags: ['v*']\n")
        done = self.forge.check()
        self.assertIn(
            f"drift: {repo} has publish.yml but no publish environment\n", done.stdout
        )
        self.assertEqual(1, done.returncode)


class Signoff(ForgeCase):
    """The org-enforced commit signoff is read back, not assumed (#104)."""

    def test_a_repo_without_signoff_drifts(self) -> None:
        """Enforced at the org, so the baseline stopped PATCHing it; check reads it."""
        repo = ROSTER[0]
        settings = {k: v for k, v in BASELINE.items() if k != "labels"}
        settings |= {
            "web_commit_signoff_required": False,
            "default_branch": "main",
            "visibility": "public",
        }
        self.forge.answer(f"repos/{ORG}/{repo}", settings)
        done = self.forge.check()
        self.assertIn(
            f"drift: {repo} web commit signoff is false (org enforcement regressed?)\n",
            done.stdout,
        )


class RefusedRead(ForgeCase):
    """A refused read names its endpoint and grant (#862, #895)."""

    def test_a_refused_listing_says_which_call_and_what_it_needs(self) -> None:
        """One line of gh's was the whole output of a dead audit leg.

        Under `set -e` the first refused read killed the run with
        `gh: Resource not accessible by personal access token (HTTP
        403)` and nothing to say which call it answered.
        """
        said = "gh: Resource not accessible by personal access token (HTTP 403)"
        self.forge.refuse(LISTING, said)
        done = self.forge.check()
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "drift: cannot read the org's repository listing\n"
            f"       endpoint: {LISTING}\n"
            "       needs:    an org owner's token — a fine-grained PAT scoped"
            " to SELECTED repositories is refused here\n"
            f"       gh said:  {said}\n",
            done.stdout,
        )
        self.assertEqual(
            "repo-baseline: nothing was checked — the population is unreadable.\n",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()

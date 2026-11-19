#!/usr/bin/env python3
"""audit:badges asks the Scorecard API as it asks every issuer (#1621).

The task's own body, read from mise/config.toml, runs in a planted canon
tree against stub `gh` and `curl`. The stub org holds two repositories,
`alpha` then `beta`, each wearing one Scorecard shield. The stub `curl`
answers each project with a planted status and body, keeps curl's own
`-f` (a non-2xx answer is exit 22 and no output) and `-w`, and logs the
User-Agent of every request.

The defect: the scorecard read was `curl -sf … | jq`, so a 404 (the API
holds nothing for the repository) left the pipeline non-zero and
errexit ended the task one line above the finding written for exactly
that answer. Measured on canon audit run 44595395692: the last line was
stele's workflow shield, then `ERROR task failed`, no finding, and every
repository after stele unchecked.

The rows: a 404 is the "shield over nothing" finding and the walk goes
on to `beta`; a 503 is a question unanswered; a 200 below its floor and
a 200 above it read as before, asked by the identified client. And the
class row: no command substitution pipes a bare `curl` into a reader.
Since #1633 the walk is the delivered `audit-badges.sh` and the task is
its guard and call, so the behaviour rows run the task body as before
and the class row reads the script, through belt-shell's scrub.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib `unittest`,
run through the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import json
import os
import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)

_SPEC = importlib.util.spec_from_file_location(
    "belt_shell",
    Path(__file__).with_name("belt-shell.py"),
)
bs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bs)

BELT = Path(__file__).resolve().parent
CANON = BELT.parent
SCRIPT = BELT / "audit-badges.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
OWNER = "monumental-archive"
PROJECT = f"https://api.scorecard.dev/projects/github.com/{OWNER}"
UA = f"User-Agent: {OWNER}/.github audit:badges"

# The org's repository list and each README: the two markers around one
# shield, as fix:badges renders it. The shield is the row's SHIELD with
# @REPO@ standing for the repository asked about. Anything else
# (.badge-states) is absent.
GH_STUB = """#!/usr/bin/env bash
case "$2" in
  orgs/*/repos) printf 'alpha\\nbeta\\n' ;;
  repos/*/readme)
    r="${2%/readme}"
    printf '<!-- badges:begin -->\\n[![s](%s)](x)\\n<!-- badges:end -->\\n' \\
      "${SHIELD//@REPO@/${r##*/}}" | base64 | tr -d '\\n'
    ;;
  *) exit 1 ;;
esac
"""

# curl as the task calls it: the URL is the last argument, `-f` fails a
# non-2xx answer with no output, `-w '\\n%{http_code}'` appends the code.
CURL_STUB = """#!/usr/bin/env bash
url="" fail="" write="" ua=""
while (($#)); do
  case "$1" in
    -f | -sf) fail=1 ;;
    -w) write=1 && shift ;;
    -H) ua="$2" && shift ;;
    --retry | --retry-delay | --max-time) shift ;;
    -*) ;;
    *) url="$1" ;;
  esac
  shift
done
echo "${url} ${ua}" >> "${STUBS}/curl.log"
name="${url##*/}"
code=$(cat "${STUBS}/${name}.code")
if [[ -n ${fail} && ${code} -ge 400 ]]; then
  exit 22
fi
cat "${STUBS}/${name}.body"
[[ -z ${write} ]] || printf '\\n%s' "${code}"
"""


def body() -> str:
    """Read audit:badges' run body the way lint:belt-shell reads it.

    Returns:
        The bash the task runs.

    Raises:
        LookupError: the belt defines no audit:badges body.

    """
    for found in bs.read(BELT / "config.toml").bodies:
        if found.name == "audit:badges":
            return found.source
    msg = "mise/config.toml defines no audit:badges body"
    raise LookupError(msg)


def bare_curls(source: str) -> list[str]:
    """Name each `curl` outside the body of `ask`.

    Read through belt-shell's scrub, so a `curl` in a comment or a
    quoted string does not count; `ask` is the function from its
    `ask() {` line to the first line that is a lone `}`.

    Returns:
        `<line>: <text>` for each such `curl`.

    """
    clean = bs.scrub(source).splitlines()
    lines = source.splitlines()
    found, inside = [], False
    for number, line in enumerate(clean, start=1):
        if line.startswith("ask() {"):
            inside = True
        elif inside and line.rstrip() == "}":
            inside = False
        elif not inside and re.search(r"\bcurl\b", line):
            found.append(f"{number}: {lines[number - 1].strip()}")
    return found


class Planted(unittest.TestCase):
    """A planted canon and org, alpha then beta, each wearing one shield."""

    def setUp(self) -> None:
        """Plant the canon tree, the floors and the stub tools."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = tmp / "tree"
        (self.tree / "security").mkdir(parents=True)
        shutil.copy(CANON / "security" / "identity.toml", self.tree / "security")
        (self.tree / "security" / "scorecard-floors.txt").write_text(
            "alpha 5.0\nbeta 5.0\n", encoding="utf-8"
        )
        self.env = hermetic.env(
            tmp,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(self.tree),
            GITHUB_REPOSITORY_OWNER=OWNER,
            STUBS=str(tmp / "stubs"),
            SHIELD=f"{PROJECT}/@REPO@/badge",
            # The bestpractices legs start the belt's owner helper.
            ORG_PYTHON=os.environ.get("ORG_PYTHON") or sys.executable,
        )
        for argv in (["init", "-q"], ["add", "security/identity.toml"]):
            subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [GIT, *argv], cwd=self.tree, env=self.env, check=True
            )
        self.stubs = tmp / "stubs"
        bindir = tmp / "bin"
        for where in (self.stubs, bindir):
            where.mkdir()
        for name, text in (("gh", GH_STUB), ("curl", CURL_STUB)):
            (bindir / name).write_text(text, encoding="utf-8")
            (bindir / name).chmod(0o755)
        self.env["PATH"] = f"{bindir}:{self.env['PATH']}"

    def org(
        self, alpha: tuple[int, str], beta: tuple[int, str], suffix: str = ""
    ) -> None:
        """Plant what the issuer answers about each repository."""
        for name, (code, text) in (("alpha", alpha), ("beta", beta)):
            stub = self.stubs / f"{name}{suffix}"
            stub.with_name(f"{stub.name}.code").write_text(str(code), encoding="utf-8")
            stub.with_name(f"{stub.name}.body").write_text(text, encoding="utf-8")

    def run_audit(self) -> subprocess.CompletedProcess:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def verdict(self, code: int, stdout: list[str], first_red: str = "") -> None:
        """Run the audit; assert its exit, its notes and its first red line."""
        done = self.run_audit()
        self.assertEqual(done.returncode, code, done.stderr)
        for line in stdout:
            self.assertIn(line, done.stdout.splitlines())
        if first_red:
            self.assertEqual(done.stderr.splitlines()[0], first_red)
        # Asked through `ask`: each issuer was asked, and by the
        # identified client, never a bare curl.
        log = (self.stubs / "curl.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 2)
        for line in log:
            self.assertIn(UA, line)


class ScorecardRead(Planted):
    """The Scorecard leg (#1621)."""

    def asked_identified(self) -> None:
        """Assert every Scorecard request carried the identifying client."""
        log = (self.stubs / "curl.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [line.split(" ", 1)[0] for line in log],
            [f"{PROJECT}/alpha", f"{PROJECT}/beta"],
        )
        for line in log:
            self.assertIn(UA, line)

    def test_404_is_a_shield_over_nothing_and_the_walk_goes_on(self) -> None:
        """No result for alpha: its finding is named and beta still read."""
        self.org((404, ""), (200, '{"score": 5.1}'))
        done = self.run_audit()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("audit:badges: beta: scorecard 5.1 (floor 5.0)", done.stdout)
        self.assertEqual(
            done.stderr.splitlines()[0],
            "audit:badges: shield defects: alpha(scorecard: issuer returned"
            " no score — a shield over nothing)",
        )

    def test_503_is_a_question_unanswered(self) -> None:
        """An outage is never reported as a missing score."""
        self.org((503, "down"), (200, '{"score": 5.1}'))
        done = self.run_audit()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("audit:badges: beta: scorecard 5.1 (floor 5.0)", done.stdout)
        self.assertEqual(
            done.stderr.splitlines()[0],
            "audit:badges: shield defects: alpha(scorecard: api.scorecard.dev,"
            " about alpha, did not answer — HTTP 503, a question unanswered,"
            " not an absence)",
        )

    def test_200_below_the_floor_is_a_regression(self) -> None:
        """A score under its floor reds as before, asked identified."""
        self.org((200, '{"score": 4.2}'), (200, '{"score": 5.1}'))
        done = self.run_audit()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(
            done.stderr.splitlines()[0],
            "audit:badges: shield defects: alpha(scorecard: 4.2 below floor"
            " 5.0 — regression)",
        )
        self.asked_identified()

    def test_200_above_the_floor_reads_clean(self) -> None:
        """Scores at or above their floors: green, asked identified."""
        self.org((200, '{"score": 5.0}'), (200, '{"score": 5.1}'))
        done = self.run_audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                "audit:badges: alpha: scorecard 5.0 (floor 5.0)",
                "audit:badges: beta: scorecard 5.1 (floor 5.0)",
                "audit:badges: 2 shield(s) checked, all agree with their issuers",
            ],
        )
        self.asked_identified()


DEFECTS = "audit:badges: shield defects: "
UNANSWERED = "did not answer — HTTP 503, a question unanswered, not an absence)"
PAGES = "https://pages.example"


def entry(repo: str, **fields: object) -> str:
    """Write the Best Practices entry for one repository, as the API serves it.

    Returns:
        The project JSON, its repo_url naming the repository.

    """
    bound = {"id": 1, "repo_url": f"https://github.com/{OWNER}/{repo}"}
    return json.dumps(bound | fields)


class BestPracticesRead(Planted):
    """The Best Practices badge leg, asked through `ask` (#1633)."""

    def setUp(self) -> None:
        """Wear the passing badge, whose id the stub reads as the repo."""
        super().setUp()
        self.env["SHIELD"] = "https://www.bestpractices.dev/projects/@REPO@/badge"
        self.ok = (200, entry("beta", badge_percentage_0=100))

    def test_404_names_the_missing_project(self) -> None:
        """No entry: the finding the leg already words; beta still read."""
        self.org((404, ""), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: bestpractices beta passing"],
            f"{DEFECTS}alpha(bestpractices: issuer returned nothing for project alpha)",
        )

    def test_503_is_a_question_unanswered(self) -> None:
        """An outage is never reported as a missing project."""
        self.org((503, "down"), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: bestpractices beta passing"],
            f"{DEFECTS}alpha(bestpractices: bestpractices.dev, about project alpha,"
            f" {UNANSWERED}",
        )

    def test_200_is_read_for_its_verdict(self) -> None:
        """Passing entries bound to their wearers read clean."""
        self.org((200, entry("alpha", badge_percentage_0=100)), self.ok, ".json")
        self.verdict(
            0,
            [
                "audit:badges: alpha: bestpractices alpha passing",
                "audit:badges: beta: bestpractices beta passing",
            ],
        )


class BaselineRead(Planted):
    """The OSPS Baseline leg, asked through `ask` (#1633)."""

    def setUp(self) -> None:
        """Wear the Baseline shield on the same entry."""
        super().setUp()
        self.env["SHIELD"] = "https://www.bestpractices.dev/projects/@REPO@/baseline"
        self.ok = (200, entry("beta", achieved_baseline_1_at="2026-01-01"))

    def test_404_names_the_missing_project(self) -> None:
        """No entry: the finding the leg already words; beta still read."""
        self.org((404, ""), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: OSPS Baseline level 1 achieved"],
            f"{DEFECTS}alpha(baseline: issuer returned nothing for project alpha)",
        )

    def test_503_is_a_question_unanswered(self) -> None:
        """An outage is never reported as a missing project."""
        self.org((503, "down"), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: OSPS Baseline level 1 achieved"],
            f"{DEFECTS}alpha(baseline: bestpractices.dev, about project alpha,"
            f" {UNANSWERED}",
        )

    def test_200_is_read_for_its_level(self) -> None:
        """Achieved levels read clean, each by its own number."""
        alpha = entry(
            "alpha", achieved_baseline_1_at="2026-01-01", achieved_baseline_2_at="x"
        )
        self.org((200, alpha), self.ok, ".json")
        self.verdict(
            0,
            [
                "audit:badges: alpha: OSPS Baseline level 2 achieved",
                "audit:badges: beta: OSPS Baseline level 1 achieved",
            ],
        )


class LevelShieldRead(Planted):
    """The level shield's published document, asked through `ask` (#1633)."""

    def setUp(self) -> None:
        """Wear a shields endpoint over a document named for the repo."""
        super().setUp()
        self.env["SHIELD"] = (
            "https://img.shields.io/endpoint?url=https%3A%2F%2Fpages.example%2F@REPO@.json"
        )
        self.ok = (200, '{"schemaVersion": 1, "label": "slsa", "message": "L3"}')

    def test_404_is_a_shield_over_nothing(self) -> None:
        """No document: the finding the leg already words; beta still read."""
        self.org((404, ""), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: level shield slsa L3 published and live"],
            f"{DEFECTS}alpha(level-shield: published judgment {PAGES}/alpha.json"
            " unreadable — the badge renders nothing)",
        )

    def test_503_is_a_question_unanswered(self) -> None:
        """An outage is never reported as an unpublished judgment."""
        self.org((503, "down"), self.ok, ".json")
        self.verdict(
            1,
            ["audit:badges: beta: level shield slsa L3 published and live"],
            f"{DEFECTS}alpha(level-shield: {PAGES}/alpha.json {UNANSWERED}",
        )

    def test_200_is_read_for_its_shield(self) -> None:
        """A live endpoint document reads clean, by its label and message."""
        self.org(self.ok, self.ok, ".json")
        self.verdict(
            0,
            [
                "audit:badges: alpha: level shield slsa L3 published and live",
                "audit:badges: beta: level shield slsa L3 published and live",
            ],
        )


class BareCurl(unittest.TestCase):
    """`ask` is the walk's one curl (#1633)."""

    def test_script_curls_only_inside_ask(self) -> None:
        """Every issuer read in the script goes through `ask`."""
        self.assertEqual(bare_curls(SCRIPT.read_text(encoding="utf-8")), [])

    def test_a_bare_read_is_found(self) -> None:
        """A planted bare read is named; ask's own curl and comments are not."""
        planted = (
            "# a bare `curl -sf` in prose\n"
            "ask() {\n"
            '  out=$(curl -s -w "%{http_code}" "$@" || true)\n'
            "}\n"
            'bp=$(curl -sf "https://y/${id}.json" || true)\n'
        )
        self.assertEqual(
            bare_curls(planted),
            ['5: bp=$(curl -sf "https://y/${id}.json" || true)'],
        )


if __name__ == "__main__":
    unittest.main()

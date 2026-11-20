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

import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import belt_shell as bs
import hermetic

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "audit-badges.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
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
  repos/*/commits/*) printf 'abc1234\\t%s\\n' "${HEAD_AT:-$(date +%s)}" ;;
  repos/*/contents/*) exit 1 ;;
  repos/*) echo main ;;
  *) exit 1 ;;
esac
"""

# curl as the task calls it: the URL is the last argument, `-f` fails a
# non-2xx answer with no output, `-w '\\n%{http_code}'` appends the code,
# and `-D <file>` writes the status line and the planted `<name>.headers`.
CURL_STUB = """#!/usr/bin/env bash
url="" fail="" write="" ua="" dump=""
while (($#)); do
  case "$1" in
    -f | -sf) fail=1 ;;
    -w) write=1 && shift ;;
    -H) ua="$2" && shift ;;
    -D) dump="$2" && shift ;;
    --retry | --retry-delay | --max-time) shift ;;
    -*) ;;
    *) url="$1" ;;
  esac
  shift
done
echo "${url} ${ua}" >> "${STUBS}/curl.log"
name="${url##*/}"
code=$(cat "${STUBS}/${name}.code")
if [[ -n ${dump} ]]; then
  printf 'HTTP/2 %s\\r\\n' "${code}" > "${dump}"
  [[ ! -f ${STUBS}/${name}.headers ]] || cat "${STUBS}/${name}.headers" >> "${dump}"
  printf '\\r\\n' >> "${dump}"
fi
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
        self.tree = hermetic.canon(
            tmp / "tree",
            "security/identity.toml",
            stubs={"security/scorecard-floors.txt": "alpha 5.0\nbeta 5.0\n"},
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

    def run_audit(self) -> subprocess.CompletedProcess[str]:
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

    def headers(self, name: str, *lines: str) -> None:
        """Plant the response headers the issuer sends about one name."""
        text = "".join(f"{line}\r\n" for line in lines)
        (self.stubs / f"{name}.headers").write_text(text, encoding="utf-8")

    def verdict(
        self, code: int, stdout: list[str], first_red: str = "", asks: int = 2
    ) -> None:
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
        self.assertEqual(len(log), asks)
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


STALE = ("age: 43302", "cache-control: public, max-age=600")
PAST = "answered from a cache 42702 seconds past its lifetime"
# release-lab's Scorecard answer in the first audit after #1649: a 200
# with a dated result, 45495 s past `max-age=600`.
AGED = ("age: 45495", "cache-control: max-age=600")
OLD = ("age: 700000", "cache-control: max-age=600")
WEEK = "answered from a cache 700000 seconds old, older than the audit's week"
NOT_ANSWERED = ", a question unanswered, not an absence)"
# Codecov's branch route, as it answered for .github's main (#1668).
COVERED = (
    '{"name": "main",'
    ' "head_commit": {"commitid": "abc1234", "totals": {"coverage": 90.5}}}'
)
CODECOV = f"https://codecov.io/gh/{OWNER}/@REPO@/graph/badge.svg"

# Each leg that asks an issuer: its shield, the suffix its question's
# last path component carries, how `unanswered` names the issuer for
# alpha, a 200 body it judges clean for a repository, and its note.
LEGS = (
    (
        "scorecard",
        f"{PROJECT}/@REPO@/badge",
        "",
        "api.scorecard.dev, about alpha,",
        lambda _r: '{"score": 5.0}',
        lambda r: f"audit:badges: {r}: scorecard 5.0 (floor 5.0)",
    ),
    (
        "bestpractices",
        "https://www.bestpractices.dev/projects/@REPO@/badge",
        ".json",
        "bestpractices.dev, about project alpha,",
        lambda r: entry(r, badge_percentage_0=100),
        lambda r: f"audit:badges: {r}: bestpractices {r} passing",
    ),
    (
        "baseline",
        "https://www.bestpractices.dev/projects/@REPO@/baseline",
        ".json",
        "bestpractices.dev, about project alpha,",
        lambda r: entry(r, achieved_baseline_1_at="2026-01-01"),
        lambda r: f"audit:badges: {r}: OSPS Baseline level 1 achieved",
    ),
    (
        "level-shield",
        "https://img.shields.io/endpoint?url=https%3A%2F%2Fpages.example%2F@REPO@.json",
        ".json",
        f"{PAGES}/alpha.json",
        lambda _r: '{"schemaVersion": 1, "label": "slsa", "message": "L3"}',
        lambda r: f"audit:badges: {r}: level shield slsa L3 published and live",
    ),
    (
        "reuse",
        "https://api.reuse.software/badge/@REPO@",
        "",
        "api.reuse.software",
        lambda _r: '{"status": "compliant"}',
        lambda r: f"audit:badges: {r}: reuse compliant",
    ),
    (
        "doi",
        "https://zenodo.org/badge/DOI/10.5281/@REPO@.svg",
        "",
        "doi.org, about 10.5281/alpha,",
        lambda _r: "",
        lambda r: f"audit:badges: {r}: DOI resolves",
    ),
    (
        "crates",
        "https://img.shields.io/crates/v/@REPO@",
        "",
        "crates.io, about alpha,",
        lambda _r: "{}",
        lambda r: f"audit:badges: {r}: crate {r} exists",
    ),
    (
        "npm",
        "https://img.shields.io/npm/v/@REPO@",
        "",
        "the npm registry",
        lambda _r: "{}",
        lambda r: f"audit:badges: {r}: npm package exists",
    ),
    (
        "codecov",
        CODECOV,
        "",
        "Codecov, about alpha's main,",
        lambda _r: '{"branch": "main"}',
        lambda r: f"audit:badges: {r}: codecov holds 90.5% for {r} at abc1234",
    ),
)


class StaleAnswer(Planted):
    """What an answer's cache age costs depends on what it says (#1649).

    An absence has to be the issuer's current word, so a stale one is a
    question unanswered (Scorecard served stele a 404 with `age: 43302`
    against `max-age=600`). A presence is judged on its content whatever
    its cache age, up to the audit's own week.
    """

    def plant(
        self, suffix: str, body: Callable[[str], str], headers: tuple[str, ...]
    ) -> None:
        """Plant both repositories' answers, and Codecov's branch route."""
        self.org((200, body("alpha")), (200, body("beta")), suffix)
        for name in ("alpha", "beta"):
            self.headers(f"{name}{suffix}", *headers)
        (self.stubs / "main.code").write_text("200", encoding="utf-8")
        (self.stubs / "main.body").write_text(COVERED, encoding="utf-8")
        self.headers("main", *headers)

    def test_a_stale_404_is_a_question_unanswered(self) -> None:
        """The measured stale 404 names the cache, never an absence."""
        self.org((404, ""), (200, '{"score": 5.1}'))
        self.headers("alpha", *STALE)
        self.verdict(
            1,
            ["audit:badges: beta: scorecard 5.1 (floor 5.0)"],
            f"{DEFECTS}alpha(scorecard: api.scorecard.dev, about alpha, did not"
            f" answer — HTTP 404, {PAST}{NOT_ANSWERED}",
        )

    def test_a_stale_200_is_judged_on_its_score(self) -> None:
        """A dated result past `max-age` passes or fails on its score."""
        for alpha, code, said in (
            ("5.0", 0, ""),
            ("4.2", 1, f"{DEFECTS}alpha(scorecard: 4.2 below floor 5.0 — regression)"),
        ):
            with self.subTest(score=alpha):
                (self.stubs / "curl.log").unlink(missing_ok=True)
                self.org((200, f'{{"score": {alpha}}}'), (200, '{"score": 5.1}'))
                self.headers("alpha", *AGED)
                self.headers("beta", *AGED)
                beta = "audit:badges: beta: scorecard 5.1 (floor 5.0)"
                self.verdict(code, [beta], said)

    def test_an_answer_older_than_the_week_is_unanswered(self) -> None:
        """Past the audit's own period, whatever `max-age` says."""
        for lines in (OLD, ("age: 700000", "cache-control: max-age=31536000")):
            with self.subTest(headers=lines):
                (self.stubs / "curl.log").unlink(missing_ok=True)
                self.org((200, '{"score": 5.0}'), (200, '{"score": 5.1}'))
                self.headers("alpha", *lines)
                self.verdict(
                    1,
                    ["audit:badges: beta: scorecard 5.1 (floor 5.0)"],
                    f"{DEFECTS}alpha(scorecard: api.scorecard.dev, about alpha, did"
                    f" not answer — HTTP 200, {WEEK}{NOT_ANSWERED}",
                )

    def test_the_scorecard_note_names_its_result_date(self) -> None:
        """The result's own date is printed beside the score, not judged."""
        dated = '{"score": 5.0, "date": "2026-11-18T18:00:22Z"}'
        self.org((200, dated), (200, '{"score": 5.1}'))
        self.verdict(
            0,
            [
                "audit:badges: alpha: scorecard 5.0 (floor 5.0, result of 2026-11-18)",
                "audit:badges: beta: scorecard 5.1 (floor 5.0)",
            ],
        )

    def test_fresh_or_headerless_answers_read_as_today(self) -> None:
        """Within its lifetime, or with no claim either way: read as today."""
        absent = (
            f"{DEFECTS}alpha(scorecard: issuer returned no score"
            " — a shield over nothing)"
        )
        beta = "audit:badges: beta: scorecard 5.1 (floor 5.0)"
        for lines in (
            ("age: 100", "cache-control: max-age=600"),
            # s-maxage is the shared cache's lifetime and wins over max-age.
            ("age: 700", "cache-control: max-age=600, s-maxage=1000"),
            ("age: 43302",),
            ("cache-control: max-age=600",),
            (),
        ):
            with self.subTest(headers=lines):
                (self.stubs / "curl.log").unlink(missing_ok=True)
                self.org((404, ""), (200, '{"score": 5.1}'))
                self.headers("alpha", *lines)
                self.headers("beta", *lines)
                self.verdict(1, [beta], absent)

    def test_every_leg_judges_a_stale_presence(self) -> None:
        """Each leg reads a 200 past `max-age` on its content."""
        for leg, shield, suffix, _who, body, note in LEGS:
            with self.subTest(leg=leg):
                (self.stubs / "curl.log").unlink(missing_ok=True)
                self.env["SHIELD"] = shield
                self.plant(suffix, body, AGED)
                asks = 2
                self.verdict(0, [note("alpha"), note("beta")], asks=asks)

    def test_every_leg_reads_the_week_bound(self) -> None:
        """Each leg sends an answer older than the week to `unanswered`."""
        for leg, shield, suffix, who, body, _note in LEGS:
            with self.subTest(leg=leg):
                (self.stubs / "curl.log").unlink(missing_ok=True)
                self.env["SHIELD"] = shield
                self.plant(suffix, body, OLD)
                said = f"({leg}: {who} did not answer — HTTP 200, {WEEK}{NOT_ANSWERED}"
                beta = said.replace("alpha", "beta")
                self.verdict(1, [], f"{DEFECTS}alpha{said} beta{beta}")

    def test_codecov_stale_empty_branch_is_unanswered(self) -> None:
        """No coverage in a stale branch answer is an absence: unanswered."""
        self.env["SHIELD"] = CODECOV
        self.plant("", lambda _r: "", ())
        (self.stubs / "main.body").write_text('{"name": "main"}', encoding="utf-8")
        self.headers("main", *STALE)
        said = (
            "(codecov: Codecov, about alpha's main, did not answer"
            f" — HTTP 200, {PAST}{NOT_ANSWERED}"
        )
        beta = said.replace("alpha", "beta")
        self.verdict(1, [], f"{DEFECTS}alpha{said} beta{beta}")


def both(said: str) -> str:
    """Build the defects line naming alpha and then beta.

    Returns:
        The line, beta's entry the same as alpha's by name.

    """
    return f"{DEFECTS}alpha{said} beta{said.replace('alpha', 'beta')}"


class CodecovRead(Planted):
    """One question to Codecov, its branch route (#1668).

    The stub's GitHub says each repository's default branch is `main`
    at `abc1234`, committed at HEAD_AT (now, unless a row says).
    """

    def setUp(self) -> None:
        """Wear the Codecov shield."""
        super().setUp()
        self.env["SHIELD"] = CODECOV

    def branch(self, code: int, text: str) -> None:
        """Plant what Codecov's branch route answers for `main`."""
        (self.stubs / "main.code").write_text(str(code), encoding="utf-8")
        (self.stubs / "main.body").write_text(text, encoding="utf-8")

    def asked(self) -> list[str]:
        """Read the URLs the audit asked, in order.

        Returns:
            One URL per request.

        """
        log = (self.stubs / "curl.log").read_text(encoding="utf-8").splitlines()
        return [line.split(" ", 1)[0] for line in log]

    def test_the_detail_route_is_never_asked(self) -> None:
        """A 404 on /repos/<name> is not asked about; the branch is judged."""
        self.org((404, '{"detail": "Not found."}'), (404, '{"detail": "Not found."}'))
        self.branch(200, COVERED)
        self.verdict(
            0,
            [
                "audit:badges: alpha: codecov holds 90.5% for alpha at abc1234",
                "audit:badges: beta: codecov holds 90.5% for beta at abc1234",
            ],
        )
        api = f"https://api.codecov.io/api/v2/github/{OWNER}/repos"
        self.assertEqual(
            self.asked(), [f"{api}/alpha/branches/main", f"{api}/beta/branches/main"]
        )

    def test_branch_route_404_is_no_record(self) -> None:
        """Codecov knows neither the repository nor the branch."""
        self.branch(404, '{"detail": "Not found."}')
        self.verdict(1, [], both("(codecov: issuer has no record of alpha)"))

    def test_no_coverage_is_a_shield_reading_unknown(self) -> None:
        """The branch is known and its head has no report."""
        self.branch(200, '{"name": "main", "head_commit": {"commitid": "abc1234"}}')
        self.verdict(
            1,
            [],
            both(
                "(codecov: no coverage report for alpha's main — the shield reads"
                " unknown; the gate stub must pass codecov-token)"
            ),
        )

    def test_a_head_behind_the_forge_after_an_hour_is_stale(self) -> None:
        """Codecov's head is not GitHub's, and GitHub's is over an hour old."""
        self.branch(200, COVERED.replace("abc1234", "0ld0000"))
        self.env["HEAD_AT"] = "1000000000"
        self.verdict(
            1,
            [],
            both(
                "(codecov: stale — Codecov last heard about 0ld0000, main is at"
                " abc1234; the upload job is not running)"
            ),
        )

    def test_a_head_behind_the_forge_within_the_hour_is_in_flight(self) -> None:
        """An upload still in flight: the figure Codecov holds is noted."""
        self.branch(200, COVERED.replace("abc1234", "0ld0000"))
        self.verdict(
            0,
            [
                "audit:badges: alpha: codecov holds 90.5% for alpha at 0ld0000",
                "audit:badges: beta: codecov holds 90.5% for beta at 0ld0000",
            ],
        )

    def test_503_is_a_question_unanswered(self) -> None:
        """An outage is never reported as a missing record."""
        self.branch(503, "down")
        self.verdict(
            1,
            [],
            both(
                "(codecov: Codecov, about alpha's main, did not answer — HTTP 503,"
                " a question unanswered, not an absence)"
            ),
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

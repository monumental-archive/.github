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

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import json
import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING

from belt.typed_pytest import fixture

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

import pytest

from belt import belt_shell as bs
from tests import support

# The two legs that ask: the project's JSON, then its shield.
ASKS = 2

BELT = support.MISE
SCRIPT = BELT / "audit-badges.sh"
BASH = support.tool("bash")
GIT = support.tool("git")
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
name="${url%\\?fresh=*}"
name="${name##*/}"
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


class Planted:
    """A planted canon and org, alpha then beta, each wearing one shield."""

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Plant the canon tree, the floors and the stub tools."""
        self.plant_canon = canon
        self.make_env = env
        tmp = tmp_path
        self.tree = self.plant_canon(
            tmp / "tree",
            "security/identity.toml",
            stubs={"security/scorecard-floors.txt": "alpha 5.0\nbeta 5.0\n"},
        )
        self.env = self.make_env(
            tmp,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(self.tree),
            GITHUB_REPOSITORY_OWNER=OWNER,
            STUBS=str(tmp / "stubs"),
            SHIELD=f"{PROJECT}/@REPO@/badge",
            # The bestpractices legs start the belt's owner helper.
            ORG_PYTHON=os.environ.get("ORG_PYTHON") or sys.executable,
        )
        tracked = ["security/identity.toml", "security/scorecard-floors.txt"]
        for argv in (["init", "-q"], ["add", *tracked]):
            subprocess.run([GIT, *argv], cwd=self.tree, env=self.env, check=True)
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
        as_mise = support.task_run("audit:badges", body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
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
        assert done.returncode == code, done.stderr
        for line in stdout:
            assert line in done.stdout.splitlines()
        if first_red:
            assert done.stderr.splitlines()[0] == first_red
        # Asked through `ask`: each issuer was asked, and by the
        # identified client, never a bare curl.
        log = (self.stubs / "curl.log").read_text(encoding="utf-8").splitlines()
        assert len(log) == asks
        for line in log:
            assert UA in line


@fixture
def planted_state(tmp_path: Path, canon: support.Canon, env: support.Env) -> Planted:
    """Give each row its own Planted.

    Returns:
        The row's Planted.

    """
    return Planted(tmp_path, canon=canon, env=env)


class ScorecardRead(Planted):
    """The Scorecard leg (#1621)."""

    def asked_identified(self) -> None:
        """Assert every Scorecard request carried the identifying client."""
        log = (self.stubs / "curl.log").read_text(encoding="utf-8").splitlines()
        got = [line.split(" ", 1)[0] for line in log]
        expected = [f"{PROJECT}/alpha", f"{PROJECT}/beta"]
        assert got == expected
        for line in log:
            assert UA in line


@fixture
def scorecard_read(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> ScorecardRead:
    """Give each row its own ScorecardRead.

    Returns:
        The row's ScorecardRead.

    """
    return ScorecardRead(tmp_path, canon=canon, env=env)


def test_404_is_a_shield_over_nothing_and_the_walk_goes_on(
    scorecard_read: ScorecardRead,
) -> None:
    """No result for alpha: its finding is named and beta still read."""
    scorecard_read.org((404, ""), (200, '{"score": 5.1}'))
    done = scorecard_read.run_audit()
    assert done.returncode == 1, done.stderr
    assert "audit:badges: beta: scorecard 5.1 (floor 5.0)" in done.stdout
    assert done.stderr.splitlines()[0] == (
        "audit:badges: shield defects: alpha(scorecard: issuer returned"
        " no score — a shield over nothing)"
    )


def test_503_is_a_question_unanswered(scorecard_read: ScorecardRead) -> None:
    """An outage is never reported as a missing score."""
    scorecard_read.org((503, "down"), (200, '{"score": 5.1}'))
    done = scorecard_read.run_audit()
    assert done.returncode == 1, done.stderr
    assert "audit:badges: beta: scorecard 5.1 (floor 5.0)" in done.stdout
    assert done.stderr.splitlines()[0] == (
        "audit:badges: shield defects: alpha(scorecard: api.scorecard.dev,"
        " about alpha, did not answer — HTTP 503, a question unanswered,"
        " not an absence)"
    )


def test_200_below_the_floor_is_a_regression(scorecard_read: ScorecardRead) -> None:
    """A score under its floor reds as before, asked identified."""
    scorecard_read.org((200, '{"score": 4.2}'), (200, '{"score": 5.1}'))
    done = scorecard_read.run_audit()
    assert done.returncode == 1, done.stderr
    assert done.stderr.splitlines()[0] == (
        "audit:badges: shield defects: alpha(scorecard: 4.2 below floor"
        " 5.0 — regression)"
    )
    scorecard_read.asked_identified()


def test_200_above_the_floor_reads_clean(scorecard_read: ScorecardRead) -> None:
    """Scores at or above their floors: green, asked identified."""
    scorecard_read.org((200, '{"score": 5.0}'), (200, '{"score": 5.1}'))
    done = scorecard_read.run_audit()
    assert done.returncode == 0, done.stderr
    expected = [
        "audit:badges: alpha: scorecard 5.0 (floor 5.0)",
        "audit:badges: beta: scorecard 5.1 (floor 5.0)",
        "audit:badges: 2 shield(s) checked, all agree with their issuers",
    ]
    assert done.stdout.splitlines() == expected
    scorecard_read.asked_identified()


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

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Wear the passing badge, whose id the stub reads as the repo."""
        super().__init__(tmp_path, canon=canon, env=env)
        self.env["SHIELD"] = "https://www.bestpractices.dev/projects/@REPO@/badge"
        self.ok = (200, entry("beta", badge_percentage_0=100))


@fixture
def best_practices_read(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> BestPracticesRead:
    """Give each row its own BestPracticesRead.

    Returns:
        The row's BestPracticesRead.

    """
    return BestPracticesRead(tmp_path, canon=canon, env=env)


def test_404_names_the_missing_project(best_practices_read: BestPracticesRead) -> None:
    """No entry: the finding the leg already words; beta still read."""
    best_practices_read.org((404, ""), best_practices_read.ok, ".json")
    best_practices_read.verdict(
        1,
        ["audit:badges: beta: bestpractices beta passing"],
        f"{DEFECTS}alpha(bestpractices: issuer returned nothing for project alpha)",
    )


def test_best_practices_read_503_is_a_question_unanswered(
    best_practices_read: BestPracticesRead,
) -> None:
    """An outage is never reported as a missing project."""
    best_practices_read.org((503, "down"), best_practices_read.ok, ".json")
    best_practices_read.verdict(
        1,
        ["audit:badges: beta: bestpractices beta passing"],
        f"{DEFECTS}alpha(bestpractices: bestpractices.dev, about project alpha,"
        f" {UNANSWERED}",
    )


def test_200_is_read_for_its_verdict(best_practices_read: BestPracticesRead) -> None:
    """Passing entries bound to their wearers read clean."""
    best_practices_read.org(
        (200, entry("alpha", badge_percentage_0=100)), best_practices_read.ok, ".json"
    )
    best_practices_read.verdict(
        0,
        [
            "audit:badges: alpha: bestpractices alpha passing",
            "audit:badges: beta: bestpractices beta passing",
        ],
    )


@pytest.mark.parametrize("shield", ["badge", "baseline"])
def test_the_entry_is_asked_past_the_shared_cache(
    best_practices_read: BestPracticesRead, shield: object
) -> None:
    """Each entry is asked at a key the cache never held (#2084).

    Red when planted: dropping `?${fresh}` from either leg's ask
    leaves the planted URL bare.
    """
    best_practices_read.env["SHIELD"] = (
        f"https://www.bestpractices.dev/projects/@REPO@/{shield}"
    )
    (best_practices_read.stubs / "curl.log").unlink(missing_ok=True)
    best_practices_read.org(best_practices_read.ok, best_practices_read.ok, ".json")
    best_practices_read.run_audit()
    log = (best_practices_read.stubs / "curl.log").read_text(encoding="utf-8")
    asked = [line.split(" ", 1)[0] for line in log.splitlines()]
    assert len(asked) == ASKS
    for url in asked:
        assert re.search(
            r"^https://www\.bestpractices\.dev/projects/(alpha|beta)"
            r"\.json\?fresh=[0-9]+$",
            url,
        )


class BaselineRead(Planted):
    """The OSPS Baseline leg, asked through `ask` (#1633)."""

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Wear the Baseline shield on the same entry."""
        super().__init__(tmp_path, canon=canon, env=env)
        self.env["SHIELD"] = "https://www.bestpractices.dev/projects/@REPO@/baseline"
        self.ok = (200, entry("beta", achieved_baseline_1_at="2026-01-01"))


@fixture
def baseline_read(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> BaselineRead:
    """Give each row its own BaselineRead.

    Returns:
        The row's BaselineRead.

    """
    return BaselineRead(tmp_path, canon=canon, env=env)


def test_baseline_read_404_names_the_missing_project(
    baseline_read: BaselineRead,
) -> None:
    """No entry: the finding the leg already words; beta still read."""
    baseline_read.org((404, ""), baseline_read.ok, ".json")
    baseline_read.verdict(
        1,
        ["audit:badges: beta: OSPS Baseline level 1 achieved"],
        f"{DEFECTS}alpha(baseline: issuer returned nothing for project alpha)",
    )


def test_baseline_read_503_is_a_question_unanswered(
    baseline_read: BaselineRead,
) -> None:
    """An outage is never reported as a missing project."""
    baseline_read.org((503, "down"), baseline_read.ok, ".json")
    baseline_read.verdict(
        1,
        ["audit:badges: beta: OSPS Baseline level 1 achieved"],
        f"{DEFECTS}alpha(baseline: bestpractices.dev, about project alpha,"
        f" {UNANSWERED}",
    )


def test_200_is_read_for_its_level(baseline_read: BaselineRead) -> None:
    """Achieved levels read clean, each by its own number."""
    alpha = entry(
        "alpha", achieved_baseline_1_at="2026-01-01", achieved_baseline_2_at="x"
    )
    baseline_read.org((200, alpha), baseline_read.ok, ".json")
    baseline_read.verdict(
        0,
        [
            "audit:badges: alpha: OSPS Baseline level 2 achieved",
            "audit:badges: beta: OSPS Baseline level 1 achieved",
        ],
    )


class LevelShieldRead(Planted):
    """The level shield's published document, asked through `ask` (#1633)."""

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Wear a shields endpoint over a document named for the repo."""
        super().__init__(tmp_path, canon=canon, env=env)
        self.env["SHIELD"] = (
            "https://img.shields.io/endpoint?url=https%3A%2F%2Fpages.example%2F@REPO@.json"
        )
        self.ok = (200, '{"schemaVersion": 1, "label": "slsa", "message": "L3"}')


@fixture
def level_shield_read(
    tmp_path: Path, canon: support.Canon, env: support.Env
) -> LevelShieldRead:
    """Give each row its own LevelShieldRead.

    Returns:
        The row's LevelShieldRead.

    """
    return LevelShieldRead(tmp_path, canon=canon, env=env)


def test_404_is_a_shield_over_nothing(level_shield_read: LevelShieldRead) -> None:
    """No document: the finding the leg already words; beta still read."""
    level_shield_read.org((404, ""), level_shield_read.ok, ".json")
    level_shield_read.verdict(
        1,
        ["audit:badges: beta: level shield slsa L3 published and live"],
        f"{DEFECTS}alpha(level-shield: published judgment {PAGES}/alpha.json"
        " unreadable — the badge renders nothing)",
    )


def test_level_shield_read_503_is_a_question_unanswered(
    level_shield_read: LevelShieldRead,
) -> None:
    """An outage is never reported as an unpublished judgment."""
    level_shield_read.org((503, "down"), level_shield_read.ok, ".json")
    level_shield_read.verdict(
        1,
        ["audit:badges: beta: level shield slsa L3 published and live"],
        f"{DEFECTS}alpha(level-shield: {PAGES}/alpha.json {UNANSWERED}",
    )


def test_200_is_read_for_its_shield(level_shield_read: LevelShieldRead) -> None:
    """A live endpoint document reads clean, by its label and message."""
    level_shield_read.org(level_shield_read.ok, level_shield_read.ok, ".json")
    level_shield_read.verdict(
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
type Leg = tuple[str, str, str, str, Callable[[str], str], Callable[[str], str]]
LEGS: tuple[Leg, ...] = (
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


@fixture
def stale_answer(tmp_path: Path, canon: support.Canon, env: support.Env) -> StaleAnswer:
    """Give each row its own StaleAnswer.

    Returns:
        The row's StaleAnswer.

    """
    return StaleAnswer(tmp_path, canon=canon, env=env)


def test_a_stale_404_is_a_question_unanswered(stale_answer: StaleAnswer) -> None:
    """The measured stale 404 names the cache, never an absence."""
    stale_answer.org((404, ""), (200, '{"score": 5.1}'))
    stale_answer.headers("alpha", *STALE)
    stale_answer.verdict(
        1,
        ["audit:badges: beta: scorecard 5.1 (floor 5.0)"],
        f"{DEFECTS}alpha(scorecard: api.scorecard.dev, about alpha, did not"
        f" answer — HTTP 404, {PAST}{NOT_ANSWERED}",
    )


@pytest.mark.parametrize(
    ("alpha", "code", "said"),
    [
        ("5.0", 0, ""),
        ("4.2", 1, f"{DEFECTS}alpha(scorecard: 4.2 below floor 5.0 — regression)"),
    ],
)
def test_a_stale_200_is_judged_on_its_score(
    stale_answer: StaleAnswer, alpha: object, code: object, said: object
) -> None:
    """A dated result past `max-age` passes or fails on its score."""
    (stale_answer.stubs / "curl.log").unlink(missing_ok=True)
    stale_answer.org((200, f'{{"score": {alpha}}}'), (200, '{"score": 5.1}'))
    stale_answer.headers("alpha", *AGED)
    stale_answer.headers("beta", *AGED)
    beta = "audit:badges: beta: scorecard 5.1 (floor 5.0)"
    stale_answer.verdict(code, [beta], said)


@pytest.mark.parametrize(
    "lines", [OLD, ("age: 700000", "cache-control: max-age=31536000")]
)
def test_an_answer_older_than_the_week_is_unanswered(
    stale_answer: StaleAnswer, lines: object
) -> None:
    """Past the audit's own period, whatever `max-age` says."""
    (stale_answer.stubs / "curl.log").unlink(missing_ok=True)
    stale_answer.org((200, '{"score": 5.0}'), (200, '{"score": 5.1}'))
    stale_answer.headers("alpha", *lines)
    stale_answer.verdict(
        1,
        ["audit:badges: beta: scorecard 5.1 (floor 5.0)"],
        f"{DEFECTS}alpha(scorecard: api.scorecard.dev, about alpha, did"
        f" not answer — HTTP 200, {WEEK}{NOT_ANSWERED}",
    )


def test_the_scorecard_note_names_its_result_date(stale_answer: StaleAnswer) -> None:
    """The result's own date is printed beside the score, not judged."""
    dated = '{"score": 5.0, "date": "2026-11-18T18:00:22Z"}'
    stale_answer.org((200, dated), (200, '{"score": 5.1}'))
    stale_answer.verdict(
        0,
        [
            "audit:badges: alpha: scorecard 5.0 (floor 5.0, result of 2026-11-18)",
            "audit:badges: beta: scorecard 5.1 (floor 5.0)",
        ],
    )


@pytest.mark.parametrize(
    "lines",
    [
        ("age: 100", "cache-control: max-age=600"),
        # s-maxage is the shared cache's lifetime and wins over max-age.
        ("age: 700", "cache-control: max-age=600, s-maxage=1000"),
        ("age: 43302",),
        ("cache-control: max-age=600",),
        (),
    ],
)
def test_fresh_or_headerless_answers_read_as_today(
    stale_answer: StaleAnswer, lines: object
) -> None:
    """Within its lifetime, or with no claim either way: read as today."""
    absent = (
        f"{DEFECTS}alpha(scorecard: issuer returned no score — a shield over nothing)"
    )
    beta = "audit:badges: beta: scorecard 5.1 (floor 5.0)"
    (stale_answer.stubs / "curl.log").unlink(missing_ok=True)
    stale_answer.org((404, ""), (200, '{"score": 5.1}'))
    stale_answer.headers("alpha", *lines)
    stale_answer.headers("beta", *lines)
    stale_answer.verdict(1, [beta], absent)


@pytest.mark.parametrize(
    ("shield", "suffix", "body", "note"),
    [
        pytest.param(shield, suffix, body, note, id=leg)
        for leg, shield, suffix, _who, body, note in LEGS
    ],
)
def test_every_leg_judges_a_stale_presence(
    stale_answer: StaleAnswer,
    shield: object,
    suffix: object,
    body: object,
    note: object,
) -> None:
    """Each leg reads a 200 past `max-age` on its content."""
    (stale_answer.stubs / "curl.log").unlink(missing_ok=True)
    stale_answer.env["SHIELD"] = shield
    stale_answer.plant(suffix, body, AGED)
    asks = 2
    stale_answer.verdict(0, [note("alpha"), note("beta")], asks=asks)


@pytest.mark.parametrize("row", [pytest.param(row, id=str(row[0])) for row in LEGS])
def test_every_leg_reads_the_week_bound(
    row: tuple[object, object, object, object, object, object],
    stale_answer: StaleAnswer,
) -> None:
    """Each leg sends an answer older than the week to `unanswered`."""
    leg, shield, suffix, who, body, _note = row
    (stale_answer.stubs / "curl.log").unlink(missing_ok=True)
    stale_answer.env["SHIELD"] = shield
    stale_answer.plant(suffix, body, OLD)
    said = f"({leg}: {who} did not answer — HTTP 200, {WEEK}{NOT_ANSWERED}"
    beta = said.replace("alpha", "beta")
    stale_answer.verdict(1, [], f"{DEFECTS}alpha{said} beta{beta}")


def test_codecov_stale_empty_branch_is_unanswered(stale_answer: StaleAnswer) -> None:
    """No coverage in a stale branch answer is an absence: unanswered."""
    stale_answer.env["SHIELD"] = CODECOV
    stale_answer.plant("", lambda _r: "", ())
    (stale_answer.stubs / "main.body").write_text('{"name": "main"}', encoding="utf-8")
    stale_answer.headers("main", *STALE)
    said = (
        "(codecov: Codecov, about alpha's main, did not answer"
        f" — HTTP 200, {PAST}{NOT_ANSWERED}"
    )
    beta = said.replace("alpha", "beta")
    stale_answer.verdict(1, [], f"{DEFECTS}alpha{said} beta{beta}")


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

    def __init__(
        self, tmp_path: Path, *, canon: support.Canon, env: support.Env
    ) -> None:
        """Wear the Codecov shield."""
        super().__init__(tmp_path, canon=canon, env=env)
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


@fixture
def codecov_read(tmp_path: Path, canon: support.Canon, env: support.Env) -> CodecovRead:
    """Give each row its own CodecovRead.

    Returns:
        The row's CodecovRead.

    """
    return CodecovRead(tmp_path, canon=canon, env=env)


def test_the_detail_route_is_never_asked(codecov_read: CodecovRead) -> None:
    """A 404 on /repos/<name> is not asked about; the branch is judged."""
    codecov_read.org(
        (404, '{"detail": "Not found."}'), (404, '{"detail": "Not found."}')
    )
    codecov_read.branch(200, COVERED)
    codecov_read.verdict(
        0,
        [
            "audit:badges: alpha: codecov holds 90.5% for alpha at abc1234",
            "audit:badges: beta: codecov holds 90.5% for beta at abc1234",
        ],
    )
    api = f"https://api.codecov.io/api/v2/github/{OWNER}/repos"
    expected = [f"{api}/alpha/branches/main", f"{api}/beta/branches/main"]
    assert codecov_read.asked() == expected


def test_branch_route_404_is_no_record(codecov_read: CodecovRead) -> None:
    """Codecov knows neither the repository nor the branch."""
    codecov_read.branch(404, '{"detail": "Not found."}')
    codecov_read.verdict(1, [], both("(codecov: issuer has no record of alpha)"))


def test_no_coverage_is_a_shield_reading_unknown(codecov_read: CodecovRead) -> None:
    """The branch is known and its head has no report."""
    codecov_read.branch(200, '{"name": "main", "head_commit": {"commitid": "abc1234"}}')
    codecov_read.verdict(
        1,
        [],
        both(
            "(codecov: no coverage report for alpha's main — the shield reads"
            " unknown; the gate stub must pass codecov-token)"
        ),
    )


def test_a_head_behind_the_forge_after_an_hour_is_stale(
    codecov_read: CodecovRead,
) -> None:
    """Codecov's head is not GitHub's, and GitHub's is over an hour old."""
    codecov_read.branch(200, COVERED.replace("abc1234", "0ld0000"))
    codecov_read.env["HEAD_AT"] = "1000000000"
    codecov_read.verdict(
        1,
        [],
        both(
            "(codecov: stale — Codecov last heard about 0ld0000, main is at"
            " abc1234; the upload job is not running)"
        ),
    )


def test_a_head_behind_the_forge_within_the_hour_is_in_flight(
    codecov_read: CodecovRead,
) -> None:
    """An upload still in flight: the figure Codecov holds is noted."""
    codecov_read.branch(200, COVERED.replace("abc1234", "0ld0000"))
    codecov_read.verdict(
        0,
        [
            "audit:badges: alpha: codecov holds 90.5% for alpha at 0ld0000",
            "audit:badges: beta: codecov holds 90.5% for beta at 0ld0000",
        ],
    )


def test_codecov_read_503_is_a_question_unanswered(codecov_read: CodecovRead) -> None:
    """An outage is never reported as a missing record."""
    codecov_read.branch(503, "down")
    codecov_read.verdict(
        1,
        [],
        both(
            "(codecov: Codecov, about alpha's main, did not answer — HTTP 503,"
            " a question unanswered, not an absence)"
        ),
    )


# `ask` is the walk's one curl (#1633).
def test_script_curls_only_inside_ask() -> None:
    """Every issuer read in the script goes through `ask`."""
    assert bare_curls(SCRIPT.read_text(encoding="utf-8")) == list[str]()


def test_a_bare_read_is_found() -> None:
    """A planted bare read is named; ask's own curl and comments are not."""
    planted = (
        "# a bare `curl -sf` in prose\n"
        "ask() {\n"
        '  out=$(curl -s -w "%{http_code}" "$@" || true)\n'
        "}\n"
        'bp=$(curl -sf "https://y/${id}.json" || true)\n'
    )
    expected = ['5: bp=$(curl -sf "https://y/${id}.json" || true)']
    assert bare_curls(planted) == expected

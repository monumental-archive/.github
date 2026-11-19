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
class row: over the body, as `lint:belt-shell` reads it, no command
substitution pipes a bare `curl` into a reader.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib `unittest`,
run through the gate as `mise run test`, which `ci` collects.
"""

import importlib.util
import re
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
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
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
OWNER = "monumental-archive"
PROJECT = f"https://api.scorecard.dev/projects/github.com/{OWNER}"
UA = f"User-Agent: {OWNER}/.github audit:badges"

# The org's repository list and each README: the two markers around one
# Scorecard shield naming the repository asked for, as fix:badges renders
# it. Anything else (.badge-states) is absent.
GH_STUB = """#!/usr/bin/env bash
case "$2" in
  orgs/*/repos) printf 'alpha\\nbeta\\n' ;;
  repos/*/readme)
    r="${2%/readme}"
    printf '<!-- badges:begin -->\\n[![s](%s/%s/badge)](x)\\n<!-- badges:end -->\\n' \\
      "${PROJECT}" "${r##*/}" | base64 | tr -d '\\n'
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


def piped_curls(source: str) -> list[str]:
    """Name each command substitution that pipes a bare `curl` onward.

    Read through belt-shell's scrub, so quoted spans and comments are
    blank and a `(` or `|` inside a string or a jq program cannot count.
    `||` is an or-list, not a pipe.

    Returns:
        The source text of each such substitution.

    """
    clean = bs.scrub(source)
    found = []
    for start in (m.start() for m in re.finditer(r"\$\(", clean)):
        depth, end = 0, start + 1
        while end < len(clean):
            depth += {"(": 1, ")": -1}.get(clean[end], 0)
            if depth == 0:
                break
            end += 1
        inner = clean[start + 2 : end].replace("||", "  ")
        if re.search(r"\bcurl\b[^|]*\|", inner):
            found.append(" ".join(source[start : end + 1].split()))
    return found


class ScorecardRead(unittest.TestCase):
    """A planted canon and org; the task's verdict on the Scorecard leg."""

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
            PROJECT=PROJECT,
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

    def org(self, alpha: tuple[int, str], beta: tuple[int, str]) -> None:
        """Plant what the Scorecard API answers for each repository."""
        for name, (code, text) in (("alpha", alpha), ("beta", beta)):
            (self.stubs / f"{name}.code").write_text(str(code), encoding="utf-8")
            (self.stubs / f"{name}.body").write_text(text, encoding="utf-8")

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


class BareCurl(unittest.TestCase):
    """No command substitution in the body pipes a bare curl onward."""

    def test_body_pipes_no_bare_curl(self) -> None:
        """Every issuer read the body pipes goes through `ask` instead."""
        self.assertEqual(piped_curls(body()), [])

    def test_the_read_this_replaced_is_found(self) -> None:
        """The planted #1621 line is named; `|| true` and ask's own are not."""
        planted = (
            'out=$(curl -s -w "%{http_code}" "$@" || true)\n'
            "score=$(curl -sf \"https://x/${proj}\" | jq -r '.score // empty')\n"
            'bp=$(curl -sf "https://y/${id}.json" || true)\n'
        )
        self.assertEqual(
            piped_curls(planted),
            ["$(curl -sf \"https://x/${proj}\" | jq -r '.score // empty')"],
        )


if __name__ == "__main__":
    unittest.main()

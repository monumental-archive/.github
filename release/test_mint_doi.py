#!/usr/bin/env python3
"""Run the shipped `mint-doi` step's licence handling against stub Zenodo.

#1246: edtf v1.3.4 published everything and then failed its DOI job,

    CITATION.cff license 'MIT OR Apache-2.0' is not a single SPDX id

because the legacy deposit carries one `license` string. The step now
parses the SPDX expression into its ids, asks Zenodo's licence
vocabulary for each before any deposit exists, sends the first through
the legacy deposit and the full list as the InvenioRDM draft's `rights`,
and quotes the expression in the description when there is more than
one id.

WHAT RUNS. The `run:` body of the step, cut out of
`.github/workflows/mint-doi.yml` as shipped, under `bash -c`, with stub
`gh` and `curl` first on PATH. `jq`, `base64`, `sed`, `awk` and `tr`
are the real ones. Every row takes the first-mint path (no concept
identifier in CITATION.cff), which reaches the deposit, the metadata
and the publish with no concept to resolve. The stub `curl` refuses any
request it does not know, so a request the step should not make reads
as a failure, not a pass.

FIXTURES ARE REAL. `CITATION.cff` is this repository's own, the render
fix:citation writes, with its identifiers block removed (a first mint)
and only the `license:` line varied per row.
`testdata/zenodo-rdm-draft.json` is the body production Zenodo answered
to `GET /api/records/24249135/draft` with
`Accept: application/vnd.inveniordm.v1+json`, captured 2026-11-15 in
run 43729983384 (#1246's measurement: a draft, read back and deleted,
never published). It is the read-back AFTER a legacy metadata PUT, so
its `rights` holds one id - the state the step must repair.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import base64
import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "mint-doi.yml"
DRAFT = CANON / "release" / "testdata" / "zenodo-rdm-draft.json"
BASH = shutil.which("bash")
STEP = "      - name: Deposit, then publish — the DOI names the released bytes"
RDM = "application/vnd.inveniordm.v1+json"
VOCAB = "mit apache-2.0 0bsd gpl-2.0-only"

CURL_STUB = """#!/usr/bin/env bash
# Stub `curl` for zenodo.org. Logs "METHOD<TAB>URL<TAB>ACCEPT" per
# request and each request body as body.<n>. Answers the routes the
# first-mint path takes; refuses everything else.
set -euo pipefail
out=/dev/stdout
fmt=""
method=GET
accept=""
data=""
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -w) fmt="$2"; shift ;;
    -X) method="$2"; shift ;;
    -H) if [[ $2 == "Accept: "* ]]; then accept="${2#Accept: }"; fi; shift ;;
    --data) data="$2"; shift ;;
    --data-binary | --retry | --retry-max-time | -A) shift ;;
    https://*) url="$1" ;;
    *) ;;
  esac
  shift
done
n=$(wc -l < "${STUB_LOG}/curl" 2> /dev/null || echo 0)
n=$((n + 0))
printf '%s\\t%s\\t%s\\n' "${method}" "${url}" "${accept}" >> "${STUB_LOG}/curl"
printf '%s' "${data}" > "${STUB_LOG}/body.${n}"
path="${url#https://zenodo.org}"
code=200
body=""
case "${method} ${path}" in
  "GET /api/vocabularies/licenses/"*)
    id="${path##*/}"
    if [[ " ${STUB_VOCAB} " == *" ${id} "* ]]; then
      body="{\\"id\\":\\"${id}\\"}"
    else
      code=404
      body='{}'
    fi
    ;;
  "GET /api/deposit/depositions?"*) body='[]' ;;
  "POST /api/deposit/depositions")
    body='{"id":101,"links":{"bucket":"https://zenodo.org/api/files/b",
      "self":"https://zenodo.org/api/deposit/depositions/101",
      "publish":"https://zenodo.org/api/deposit/depositions/101/actions/publish"}}'
    ;;
  "PUT /api/files/b/"*) body='{}' ;;
  "PUT /api/deposit/depositions/101") body='{}' ;;
  "GET /api/records/101/draft") body=$(cat "${STUB_DRAFT}") ;;
  "GET /api/records/100/versions/latest")
    body='{"doi":"10.5281/zenodo.101","conceptdoi":"10.5281/zenodo.100",
      "id":101,"metadata":{"version":"1.3.5"}}'
    ;;
  "GET /api/records/101")
    # The published record: the captured draft's shape, with the rights
    # STUB_PUBLISHED names - or a read that fails as curl -f would.
    case ${STUB_PUBLISHED} in
      both) ids='[{"id": "mit"}, {"id": "apache-2.0"}]' ;;
      one) ids='[{"id": "mit"}]' ;;
      *) echo "curl: (22) The requested URL returned error: 503" >&2; exit 22 ;;
    esac
    body=$(jq -c --argjson r "${ids}" '.metadata.rights = $r' "${STUB_DRAFT}")
    ;;
  "PUT /api/records/101/draft")
    if [[ -n ${STUB_COLLAPSE:-} ]]; then
      body=$(jq -c '.metadata.rights |= .[:1]' <<< "${data}")
    else
      body="${data}"
    fi
    ;;
  "POST /api/deposit/depositions/101/actions/publish")
    body='{"doi":"10.5281/zenodo.101","conceptdoi":"10.5281/zenodo.100"}'
    ;;
  *) echo "curl stub: refusing ${method} ${url}" >&2; exit 7 ;;
esac
printf '%s' "${body}" > "${out}"
if [[ ${fmt} == '%{http_code}' ]]; then printf '%s' "${code}"; fi
"""

GH_STUB = """#!/usr/bin/env bash
# Stub `gh`: the tag's tarball, and CITATION.cff base64-encoded the way
# the contents API's .content carries it.
set -euo pipefail
case "$2" in
  */tarball/*) printf 'tarball' ;;
  */contents/CITATION.cff*) cat "${STUB_CFF_B64}" ;;
  *) echo "gh stub: refusing $*" >&2; exit 1 ;;
esac
"""


def step_body() -> str:
    """Cut the deposit step's `run:` block out of the shipped workflow.

    Returns:
        The block, dedented, exactly as the runner would hand it to bash.

    """
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index(STEP)
    run = next(i for i in range(step, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


def citation(licence: str, concept: str | None = None) -> str:
    """Render this repository's CITATION.cff with `licence`.

    Args:
        licence: the `license:` line's value.
        concept: the concept DOI to record; None renders a first mint.

    Returns:
        The file's text: `license:` set, the identifiers block dropped
        or its value replaced.

    """
    out = []
    skipping = False
    for raw in (CANON / "CITATION.cff").read_text().splitlines():
        line = raw
        if line.startswith("identifiers:") and concept is None:
            skipping = True
            continue
        if skipping and line.startswith(" "):
            continue
        skipping = False
        if line.startswith("license:"):
            line = f"license: {licence}"
        elif concept is not None and line.strip().startswith("value:"):
            line = f"{line.split('value:')[0]}value: {concept}"
        out.append(line)
    return "\n".join(out) + "\n"


@dataclass
class Run:
    """What one run of the step did."""

    returncode: int
    stdout: str
    requests: list[tuple[str, str, str]]
    bodies: list[str]

    def made(self, method: str, path: str) -> list[int]:
        """Find the requests with this method and path prefix.

        Returns:
            Their positions in the order the step made them.

        """
        url = "https://zenodo.org" + path
        return [
            i
            for i, (m, u, _) in enumerate(self.requests)
            if m == method and u.startswith(url)
        ]

    def published_reads(self) -> list[tuple[str, str, str]]:
        """Pick out the reads of the published record itself.

        Returns:
            Each GET of /api/records/101 - not its draft - as logged.

        """
        url = "https://zenodo.org/api/records/101"
        return [r for r in self.requests if r[0] == "GET" and r[1] == url]

    def legacy_metadata(self) -> dict[str, object]:
        """Decode the legacy deposit's metadata PUT.

        Returns:
            The `metadata` object the step sent.

        """
        (i,) = self.made("PUT", "/api/deposit/depositions/101")
        return json.loads(self.bodies[i])["metadata"]


def run_step(
    licence: str,
    *,
    collapse: bool = False,
    published: str = "both",
    concept: str | None = None,
) -> Run:
    """Run the shipped body with CITATION.cff stating `licence`.

    Args:
        licence: the `license:` line's value.
        collapse: make the stub's RDM PUT answer one licence, as if the
            write had not taken.
        published: what a read of the published record answers - "both"
            ids, "one", or "fail".
        concept: a concept DOI for CITATION.cff to record; its latest
            version is this release, so the run is a rerun after a
            publish (#484).

    Returns:
        The run's status, output and the stub's request log.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, log = tmp / "bin", tmp / "log"
        stubs.mkdir()
        log.mkdir()
        for name, body in (("curl", CURL_STUB), ("gh", GH_STUB)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        cff = tmp / "cff.b64"
        cff.write_text(base64.b64encode(citation(licence, concept).encode()).decode())
        summary = tmp / "summary"
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "ZENODO_TOKEN": "stub-token",
            "BASE_URL": "https://zenodo.org",
            "UNTRUSTED_VERSION": "1.3.5",
            "GH_TOKEN": "stub",
            "TAG": "v1.3.5",
            "ORG_USER_AGENT": "monumental-archive-publish (stub)",
            "GITHUB_REPOSITORY": "monumental-archive/edtf",
            "GITHUB_STEP_SUMMARY": str(summary),
            "STUB_LOG": str(log),
            "STUB_CFF_B64": str(cff),
            "STUB_DRAFT": str(DRAFT),
            "STUB_VOCAB": VOCAB,
            "STUB_PUBLISHED": published,
        }
        if collapse:
            env["STUB_COLLAPSE"] = "1"
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-c", step_body()],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )
        reqs = []
        if (log / "curl").exists():
            reqs = [
                tuple(x.split("\t")) for x in (log / "curl").read_text().splitlines()
            ]
        bodies = [(log / f"body.{i}").read_text() for i in range(len(reqs))]
        return Run(result.returncode, result.stdout + result.stderr, reqs, bodies)


LEAD = "Licensed under the SPDX expression"
SENTENCE = f"{LEAD} MIT OR Apache-2.0, as CITATION.cff states it."


class TheFixturesAreTheRealShapes(unittest.TestCase):
    """The captured draft carries what the rows lean on."""

    def test_the_draft_is_the_collapsed_read_back(self) -> None:
        """The fixture is a draft whose rights a legacy PUT collapsed to one."""
        draft = json.loads(DRAFT.read_text())
        self.assertTrue(draft["is_draft"])
        self.assertFalse(draft["is_published"])
        self.assertEqual([r["id"] for r in draft["metadata"]["rights"]], ["mit"])

    def test_the_citation_is_a_first_mint(self) -> None:
        """The rendered citation has no concept identifier and the row's licence."""
        text = citation("MIT OR Apache-2.0")
        self.assertNotIn("identifiers:", text)
        self.assertIn("\nlicense: MIT OR Apache-2.0\n", text)
        self.assertIn("family-names:", text)


class OneId(unittest.TestCase):
    """`0BSD` -> one id, through the legacy deposit alone, as before."""

    run_: Run

    @classmethod
    def setUpClass(cls) -> None:
        """Run the step once for every row of the class."""
        cls.run_ = run_step("0BSD")

    def test_it_publishes(self) -> None:
        """The step exits green with the stub's DOI."""
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout)
        self.assertIn("DOI 10.5281/zenodo.101", self.run_.stdout)

    def test_the_vocabulary_is_asked_for_the_one_id(self) -> None:
        """The vocabulary is asked for exactly the one id."""
        asked = [u for m, u, _ in self.run_.requests if "/vocabularies/" in u]
        self.assertEqual(asked, ["https://zenodo.org/api/vocabularies/licenses/0bsd"])

    def test_the_legacy_licence_is_the_id(self) -> None:
        """The legacy deposit carries the id, lowercased."""
        self.assertEqual(self.run_.legacy_metadata()["license"], "0bsd")

    def test_no_rdm_request_and_no_sentence(self) -> None:
        """One id makes no InvenioRDM request and adds no sentence."""
        self.assertEqual(self.run_.made("GET", "/api/records/"), [])
        self.assertEqual(self.run_.made("PUT", "/api/records/"), [])
        self.assertNotIn("Licensed under", self.run_.legacy_metadata()["description"])


class TwoIds(unittest.TestCase):
    """`MIT OR Apache-2.0` -> both ids on the record, the sentence quoted."""

    run_: Run

    @classmethod
    def setUpClass(cls) -> None:
        """Run the step once for every row of the class."""
        cls.run_ = run_step("MIT OR Apache-2.0")

    def test_it_publishes(self) -> None:
        """The step exits green with the stub's DOI."""
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout)

    def test_the_vocabulary_is_asked_for_each_before_any_deposit(self) -> None:
        """Each id is checked, in order, before the deposit is created."""
        asked = self.run_.made("GET", "/api/vocabularies/licenses/")
        self.assertEqual(
            [self.run_.requests[i][1].rsplit("/", 1)[1] for i in asked],
            ["mit", "apache-2.0"],
        )
        create = min(self.run_.made("POST", "/api/deposit/depositions"))
        self.assertLess(max(asked), create)

    def test_the_description_quotes_the_expression_exactly(self) -> None:
        """The description ends quoting the expression as written."""
        self.assertTrue(
            self.run_.legacy_metadata()["description"].endswith(" " + SENTENCE)
        )

    def test_the_rights_are_both_ids_and_land_before_the_publish(self) -> None:
        """Both ids go on the draft after the legacy PUT, before the publish."""
        (get,) = self.run_.made("GET", "/api/records/101/draft")
        (put,) = self.run_.made("PUT", "/api/records/101/draft")
        (legacy,) = self.run_.made("PUT", "/api/deposit/depositions/101")
        (publish,) = self.run_.made(
            "POST", "/api/deposit/depositions/101/actions/publish"
        )
        self.assertLess(legacy, get)
        self.assertLess(put, publish)
        self.assertEqual(self.run_.requests[get][2], RDM)
        self.assertEqual(self.run_.requests[put][2], RDM)
        sent = json.loads(self.run_.bodies[put])
        both = [{"id": "mit"}, {"id": "apache-2.0"}]
        self.assertEqual(sent["metadata"]["rights"], both)
        self.assertEqual(set(sent), {"metadata", "files"})

    def test_the_rest_of_the_draft_is_sent_back_unchanged(self) -> None:
        """Only rights changes in what is sent back."""
        (put,) = self.run_.made("PUT", "/api/records/101/draft")
        sent = json.loads(self.run_.bodies[put])
        draft = json.loads(DRAFT.read_text())
        sent["metadata"].pop("rights")
        draft["metadata"].pop("rights")
        self.assertEqual(sent["metadata"], draft["metadata"])
        self.assertEqual(sent["files"], draft["files"])

    def test_the_published_record_is_read_back_after_the_publish(self) -> None:
        """The published record's licences are read, with the RDM shape."""
        (publish,) = self.run_.made(
            "POST", "/api/deposit/depositions/101/actions/publish"
        )
        reads = self.run_.published_reads()
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0][2], RDM)
        self.assertGreater(self.run_.requests.index(reads[0]), publish)

    def test_and_takes_the_same_path(self) -> None:
        """AND is accepted and quoted the same way as OR."""
        run = run_step("MIT AND Apache-2.0")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(
            "Licensed under the SPDX expression MIT AND Apache-2.0,",
            run.legacy_metadata()["description"],
        )


TWO = '["mit","apache-2.0"]'
SHORT = (
    "::error::the published record's licences read back as"
    f' ["mit"], not {TWO} — the DOI 10.5281/zenodo.101 states less than'
    " CITATION.cff"
)


class ThePublishedRecord(unittest.TestCase):
    """The published record is verified; it cannot be unpublished."""

    def test_a_published_record_with_one_id_is_red_by_name(self) -> None:
        """Publish kept one of two ids: red, naming the DOI."""
        run = run_step("MIT OR Apache-2.0", published="one")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(SHORT, run.stdout)
        self.assertEqual(
            len(run.made("POST", "/api/deposit/depositions/101/actions/publish")),
            1,
        )

    def test_a_read_that_fails_is_unverified_never_a_pass(self) -> None:
        """A published record that cannot be read is red as unverified."""
        run = run_step("MIT OR Apache-2.0", published="fail")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::the published record 101 could not be read back — the"
            " licences of DOI 10.5281/zenodo.101 are unverified",
            run.stdout,
        )
        self.assertNotIn("::notice::DOI", run.stdout)

    def test_a_rerun_over_a_published_record_with_one_id_is_red(self) -> None:
        """The #484 convergence path reaches the same check."""
        run = run_step(
            "MIT OR Apache-2.0", published="one", concept="10.5281/zenodo.100"
        )
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(SHORT, run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), [])
        self.assertEqual(len(run.published_reads()), 1)

    def test_a_rerun_over_a_published_record_with_both_is_green(self) -> None:
        """A rerun over a complete record converges green."""
        run = run_step("MIT OR Apache-2.0", concept="10.5281/zenodo.100")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("::notice::DOI 10.5281/zenodo.101", run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), [])
        self.assertEqual(len(run.published_reads()), 1)

    def test_one_id_never_reads_the_published_record(self) -> None:
        """0BSD, fresh or rerun: no published read, whatever it would say."""
        for concept in (None, "10.5281/zenodo.100"):
            run = run_step("0BSD", published="fail", concept=concept)
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertEqual(run.published_reads(), [])


class Refusals(unittest.TestCase):
    """Each refusal names its cause and makes no deposit."""

    def assert_refused(self, run: Run, message: str) -> None:
        """Assert a red run, its message, and that no deposit was created."""
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(message, run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), [])

    def test_with_is_refused_by_name(self) -> None:
        """WITH refuses, naming the operator."""
        self.assert_refused(
            run_step("GPL-2.0-only WITH Classpath-exception-2.0"),
            "::error::CITATION.cff license 'GPL-2.0-only WITH Classpath-exception-2.0'"
            " joins ids with 'WITH'",
        )

    def test_parentheses_are_refused_by_name(self) -> None:
        """Parentheses refuse by name."""
        self.assert_refused(
            run_step("(MIT OR Apache-2.0)"),
            "license '(MIT OR Apache-2.0)' uses parentheses",
        )

    def test_a_dangling_operator_is_refused(self) -> None:
        """An expression ending in an operator refuses."""
        self.assert_refused(
            run_step("MIT OR"), "license 'MIT OR' is not a complete expression"
        )

    def test_an_id_absent_from_the_vocabulary_is_refused_naming_it(self) -> None:
        """An id the vocabulary 404s refuses, naming it."""
        run = run_step("MIT OR Not-A-Licence")
        self.assert_refused(
            run,
            "::error::licence id 'not-a-licence' (from CITATION.cff"
            " 'MIT OR Not-A-Licence') is not in Zenodo's licence vocabulary",
        )
        self.assertEqual(len(run.made("GET", "/api/vocabularies/licenses/")), 2)

    def test_a_read_back_missing_an_id_never_publishes(self) -> None:
        """A read-back short of an id refuses before the publish."""
        run = run_step("MIT OR Apache-2.0", collapse=True)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('the draft\'s licences read back as ["mit"]', run.stdout)
        self.assertEqual(
            run.made("POST", "/api/deposit/depositions/101/actions/publish"), []
        )


if __name__ == "__main__":
    unittest.main()

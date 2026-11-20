#!/usr/bin/env python3
"""Run `release/mint_doi.py` against a local fake Zenodo.

#1365 moved the `mint-doi` step's Zenodo client out of the workflow
into `release/mint_doi.py`; the step is now one call to it. These rows
run that helper as the step does, in-process, against an
`http.server` on 127.0.0.1 standing in for zenodo.org, with a stub `gh`
first on PATH for the tag's tarball and CITATION.cff. The fake refuses
any route it does not know (HTTP 418), so a request the helper should
not make reads as a failure, not a pass. Requests are logged with the
production origin, `https://zenodo.org`, in place of the fake's.
One row (#1698) starts the helper as the step does instead, against the
same fake: the script contract.

THE FIRST 23 ROWS are #1246's (`OneId`, `TwoIds`, `ThePublishedRecord`,
`Refusals`, `TheFixturesAreTheRealShapes`), kept row for row from when
they cut the `run:` body out of the workflow and ran it under bash with
a stub `curl`: same fixtures, same assertions, the stub's routes served
by the fake instead. Every one takes the first-mint path, or the #484
rerun over concept 100, and exercises the licence handling. edtf #1246:
v1.3.4 published everything and then failed its DOI job because the
legacy deposit carries one `license` string.

THE LATER-MINT ROWS (#1365) run on responses recorded from production
Zenodo, public reads with no token, captured 2026-11-17:

- `testdata/zenodo-canon-latest.json`: `GET /api/records/21913604/
  versions/latest`, this repository's concept (CITATION.cff). Answered
  `301` to `/api/records/24269508`, whose body this is: v1.68.4, 0bsd.
- `testdata/zenodo-canon-record-rdm.json`: `GET /api/records/24269508`
  with `Accept: application/vnd.inveniordm.v1+json`.
- `testdata/zenodo-edtf-latest.json`: `GET /api/records/24268583/
  versions/latest`, edtf's concept. `301` to `/api/records/24268584`:
  v1.3.5, `MIT OR Apache-2.0`.
- `testdata/zenodo-edtf-record-rdm.json`: `GET /api/records/24268584`
  in the InvenioRDM shape: published, rights `[mit, apache-2.0]`.

The fake answers the concept's `versions/latest` with that measured
301.

THE WRITE SIDE is captured too, `testdata/zenodo-capture-*.json`: run
44121466994 on this repository, 2026-11-17, a throwaway branch workflow
holding the org's ZENODO_TOKEN that opened a new-version draft of the
canon's concept and deleted it, never publishing. Bodies are as logged,
re-indented by jq, otherwise unedited:

- `titles` (C1): `GET /api/deposit/depositions?q=title:%22.github%22
  &size=10`, answered `[]`.
- `drafts-before` (C2, and C10 after the delete, byte-identical):
  `GET ?q=conceptrecid:21913604&status=draft&size=50`, listing the
  PUBLISHED v1.68.4 (`submitted: true`) despite `status=draft`.
- `newversion` (C3, 201; C4, the `links.latest_draft` read, was
  byte-identical): draft 24318079, unsubmitted, carrying v1.68.4's
  tarball as its one file.
- `drafts-during` (C5): the same listing with the draft open.
- `draft-after-file-delete` (C7, after C6's `DELETE .../files/<id>`
  answered 204): the draft with no files.
- `draft-gone` (C9, after C8's `DELETE` of the draft answered 204): 404.

The fake serves captured bodies with `https://zenodo.org` rewritten to
its own origin, so the helper follows their links to it. ONE ANSWER IS
NOT CAPTURED: the publish response, which cannot be taken without
minting. It keeps the one shape the helper reads (`doi`, `conceptdoi`)
and is proven only by the canon's next release. The first-mint create
POST was not called either; it answers the captured draft deposition.

THE TITLE GUARD'S QUESTION is measured too (#1389), in
`testdata/zenodo-measure-*.json`: run 44129109573 on this repository,
2026-11-17, GET only, nothing created. These are authenticated
`GET /api/deposit/depositions` answers:

- `present-github` (M1), `?q=title:%22.github%22&size=10`, and
  `present-edtf` (M2), `?q=title:%22edtf%22&size=10`: both answered
  `[]`. The guard's question before #1389 found nothing, dotted name
  or not.
- `full-github` (M3), `?q=title:%22monumental-archive%2F.github%22`,
  and `full-edtf` (M4), `?q=title:%22monumental-archive%2Fedtf%22`:
  each answered exactly its submitted record. With the slash literal
  (M5, M6) the answers were byte-identical.
- `listing-summary.txt` (M7, the default unfiltered listing): the
  summary lines as the run printed them, not a body. Six depositions:
  five submitted org records, and one unsubmitted draft whose title the
  run did not print. `size=100` answered the same six (M9);
  `size=1000` answered 400 (M8). No Link or total header was returned.

`testdata/zenodo-rdm-draft.json` is the body production Zenodo answered
to `GET /api/records/24249135/draft` with the InvenioRDM media type,
captured 2026-11-15 in run 43729983384 (#1246: a draft, read back and
deleted, never published). It is the read-back AFTER a legacy metadata
PUT, so its `rights` holds one id - the state the helper must repair.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import functools
import http.client
import io
import json
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import threading
import unittest
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self, override
from unittest import mock

import mint_doi

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import hermetic

if TYPE_CHECKING:
    from collections.abc import Callable

CANON = Path(__file__).resolve().parent.parent
# A JSON object as the fake and the helper exchange it.
Doc = dict[str, object]
# An empty list, whatever it would hold: requests, writes, entries.
NOTHING: list[object] = []
HELPER = CANON / "release" / "mint_doi.py"
TESTDATA = CANON / "release" / "testdata"
DRAFT = TESTDATA / "zenodo-rdm-draft.json"
CANON_LATEST = TESTDATA / "zenodo-canon-latest.json"
CANON_RDM = TESTDATA / "zenodo-canon-record-rdm.json"
EDTF_LATEST = TESTDATA / "zenodo-edtf-latest.json"
EDTF_RDM = TESTDATA / "zenodo-edtf-record-rdm.json"
CAPTURE = TESTDATA / "zenodo-capture-{}.json"
RDM = "application/vnd.inveniordm.v1+json"
VOCAB = frozenset({"mit", "apache-2.0", "0bsd", "gpl-2.0-only"})
ORIGIN = "https://zenodo.org"
PIPELINE = (
    "Built, proven and signed by the monumental-archive release pipeline;"
    " verification instructions ship with the GitHub release."
)
WRITES = frozenset({"POST", "PUT", "DELETE"})

GH_STUB = """#!/usr/bin/env bash
# Stub `gh`: the tag's tarball, and CITATION.cff base64-encoded the way
# the contents API's .content carries it; an empty one is a 404, which
# gh reports by printing the error body to stdout and exiting 1.
# STUB_CFF_STATUS makes gh fail AFTER printing a decodable body (#103).
set -euo pipefail
case "$2" in
  */tarball/*) printf 'tarball' ;;
  */contents/CITATION.cff*)
    if [[ ! -s ${STUB_CFF_B64} ]]; then
      echo '{"message":"Not Found","status":"404"}'
      exit 1
    fi
    cat "${STUB_CFF_B64}"
    exit "${STUB_CFF_STATUS:-0}"
    ;;
  *) echo "gh stub: refusing $*" >&2; exit 1 ;;
esac
"""


def strings(*items: str) -> list[str]:
    """List the given strings, typed as strings for the assertions.

    Returns:
        The strings, in order.

    """
    return list(items)


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


def recorded(path: Path) -> object:
    """Read a recorded response.

    Returns:
        The parsed body.

    """
    return decoded.load_json(path.read_text(encoding="utf-8"))


def recorded_object(path: Path) -> Doc:
    """Read a recorded response that is one object.

    Returns:
        The parsed body.

    """
    return decoded.as_object(recorded(path), str(path))


def objects(value: object, where: str) -> list[Doc]:
    """Narrow a decoded value to an array of objects.

    Returns:
        The objects, in order.

    """
    return [decoded.as_object(v, where) for v in decoded.as_array(value, where)]


def at(doc: object, *keys: str) -> object:
    """Walk nested objects by key.

    Returns:
        The value at the end of the walk.

    """
    for key in keys:
        doc = decoded.as_object(doc, key)[key]
    return doc


def body_of(sent: str | bytes) -> Doc:
    """Read a request body the helper sent as one JSON object.

    Returns:
        The object.

    """
    return decoded.as_object(decoded.load_json(sent), "the request body")


@dataclass
class Zenodo:
    """What the fake answers. Defaults are #1246's stub, route for route.

    `latest` maps a concept record id to the record its
    `versions/latest` resolves to; `redirect` makes it answer the
    measured 301 to `/api/records/<id>` instead of the body.
    `records` maps a record id to its InvenioRDM read-back; a value of
    None answers 503. `publish` is "ok", "drop" (the connection closes
    with no answer, the record published - a lost response) or "fail"
    (500, nothing published). `appear_after` is how many oracle reads
    after a dropped publish still answer the old version.
    """

    vocab: frozenset[str] = VOCAB
    vocab_status: int | None = None
    titles: object = field(default_factory=list)
    title_pages: list[object] | None = None
    titles_by_query: dict[str, object] | None = None
    drafts: list[Doc] = field(default_factory=list)
    latest: dict[str, Doc] = field(default_factory=dict)
    redirect: bool = False
    records: dict[str, Doc | None] = field(default_factory=dict)
    collapse: bool = False
    publish: str = "ok"
    deposition: str = "101"
    published_record: Doc | None = None
    appear_after: int = 0
    create_body: object = None
    newversion_body: object = None
    draft_body: object = None
    publish_body: Doc | None = None
    stale: Doc = field(default_factory=dict)
    log: list[tuple[str, str, str]] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)
    base: str = ""

    def deposit(self, rid: str) -> Doc:
        """Answer a deposition the way #1246's stub did.

        Returns:
            The deposition: id, links, no files.

        """
        dep = f"{self.base}/api/deposit/depositions/{rid}"
        return {
            "id": int(rid),
            "files": [],
            "links": {
                "bucket": f"{self.base}/api/files/b",
                "self": dep,
                "publish": f"{dep}/actions/publish",
            },
        }

    def title_page(self, query: str) -> tuple[int, object, dict[str, str]]:
        """Answer one page of the title listing.

        Without `title_pages`, every page answers `titles`, as the stub
        always did. With it, page N answers entry N-1 (a body, or an int
        status), and pages past the list answer `[]`.

        Returns:
            (status, JSON body, headers).

        """
        params: dict[str, str] = {}
        for kv in query.split("&"):
            key, _, value = kv.partition("=")
            params[key] = value
        if self.titles_by_query is not None:
            got = self.titles_by_query.get(params["q"])
            if got is None:
                return 418, {"refused": params["q"]}, {}
            return 200, self.live(got), {}
        if self.title_pages is None:
            return 200, self.live(self.titles), {}
        page = int(params["page"])
        if page > len(self.title_pages):
            return 200, [], {}
        got = self.title_pages[page - 1]
        if isinstance(got, int):
            return got, {"status": got}, {}
        return 200, self.live(got), {}

    def live(self, body: object) -> object:
        """Point a recorded body's zenodo.org links at the fake.

        Returns:
            The body, every production origin replaced by the fake's.

        """
        text = json.dumps(body).replace("@BASE@", self.base)
        return decoded.load_json(text.replace(ORIGIN, self.base))

    def route(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches] - one table of routes
        self, method: str, path: str, accept: str, body: bytes
    ) -> tuple[int, object, dict[str, str]] | None:
        """Answer one request.

        Returns:
            (status, JSON body, headers), or None to drop the connection.

        """
        dep = f"/api/deposit/depositions/{self.deposition}"
        base, _, query = path.partition("?")
        if method == "GET" and base.startswith("/api/vocabularies/licenses/"):
            if self.vocab_status is not None:
                return self.vocab_status, {}, {}
            ok = base.rsplit("/", 1)[1] in self.vocab
            return (200, {"id": base.rsplit("/", 1)[1]}, {}) if ok else (404, {}, {})
        if method == "GET" and base == "/api/deposit/depositions":
            if "status=draft" not in query:
                return self.title_page(query)
            return 200, self.live(self.drafts), {}
        if method == "POST" and path == "/api/deposit/depositions":
            if self.create_body is not None:
                return 201, self.live(self.create_body), {}
            return 200, self.deposit(self.deposition), {}
        if method == "DELETE" and base.startswith("/api/deposit/depositions/"):
            return 204, None, {}
        if method == "POST" and base.endswith("/actions/newversion"):
            if self.newversion_body is not None:
                return 201, self.live(self.newversion_body), {}
            draft = f"{self.base}{dep}"
            return 200, {"links": {"latest_draft": draft}}, {}
        if method == "GET" and path == dep:
            if self.draft_body is not None:
                return 200, self.live(self.draft_body), {}
            return 200, self.deposit(self.deposition), {}
        if method == "PUT" and (base.startswith("/api/files/") or path == dep):
            return 200, {}, {}
        if path == f"/api/records/{self.deposition}/draft":
            if method == "GET" and accept == RDM:
                return 200, recorded_object(DRAFT), {}
            if method == "PUT" and accept == RDM:
                sent = body_of(body)
                if self.collapse:
                    meta = decoded.as_object(sent["metadata"], "metadata")
                    meta["rights"] = decoded.as_array(meta["rights"], "rights")[:1]
                return 200, sent, {}
        if method == "POST" and path == f"{dep}/actions/publish":
            if self.publish == "fail":
                return 500, {"message": "publish failed"}, {}
            self.land()
            if self.publish == "drop":
                return None
            if self.publish_body is not None:
                # NOT CAPTURED: a publish cannot be taken without minting.
                # The one shape the helper reads; the canon's next release
                # is its proof (#1365).
                return 202, self.publish_body, {}
            return (
                200,
                {
                    "doi": f"10.5281/zenodo.{self.deposition}",
                    "conceptdoi": "10.5281/zenodo.100",
                },
                {},
            )
        if method == "GET" and base.endswith("/versions/latest"):
            concept = base.split("/")[3]
            if concept not in self.latest:
                return 404, {"status": 404}, {}
            if self.appear_after and self.stale:
                self.appear_after -= 1
                rec = self.stale
            else:
                rec = self.latest[concept]
            if self.redirect:
                loc = f"{self.base}/api/records/{rec['id']}"
                return 301, {}, {"Location": loc}
            self.records.setdefault(f"plain/{rec['id']}", rec)
            return 200, rec, {}
        if method == "GET" and base.startswith("/api/records/"):
            rid = base.rsplit("/", 1)[1]
            if accept != RDM:
                plain = self.records.get(f"plain/{rid}")
                return (200, plain, {}) if plain else (404, {}, {})
            if rid not in self.records:
                return 404, {"status": 404}, {}
            rdm = self.records[rid]
            return (200, rdm, {}) if rdm is not None else (503, {}, {})
        return 418, {"refused": f"{method} {path}"}, {}

    def land(self) -> None:
        """Make the publish true in the archive: the new record is latest."""
        if self.published_record is None:
            return
        for concept, rec in list(self.latest.items()):
            self.stale = rec
            self.latest[concept] = self.published_record


class Fake:
    """An `http.server` serving one `Zenodo` on 127.0.0.1."""

    def __init__(self, zenodo: Zenodo) -> None:
        """Start serving `zenodo` on a free port."""
        z = zenodo
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            @override
            def log_message(self, *_: object) -> None:
                pass

            def answer(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                accept = self.headers.get("Accept", "")
                if accept == "*/*":
                    accept = ""
                with lock:
                    z.log.append((self.command, ORIGIN + self.path, accept))
                    z.bodies.append(body)
                    out = z.route(self.command, self.path, accept, body)
                if out is None:
                    self.close_connection = True
                    return
                status, payload, headers = out
                data = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_PUT = do_POST = do_DELETE = answer  # ruff: ignore[mixed-case-variable-in-class-scope] - http.server's names

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        z.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        # A short poll, so shutdown does not wait out the default 0.5 s.
        self.thread = threading.Thread(
            target=self.server.serve_forever, args=(0.01,), daemon=True
        )

    def __enter__(self) -> Self:
        """Serve.

        Returns:
            The fake.

        """
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()


@dataclass
class Run:
    """What one run of the helper did."""

    returncode: int
    stdout: str
    requests: list[tuple[str, str, str]]
    bodies: list[str]
    pauses: list[float]
    summary: str

    def made(self, method: str, path: str) -> list[int]:
        """Find the requests with this method and path prefix.

        Returns:
            Their positions in the order the helper made them.

        """
        url = ORIGIN + path
        return [
            i
            for i, (m, u, _) in enumerate(self.requests)
            if m == method and u.startswith(url)
        ]

    def writes(self) -> list[tuple[str, str, str]]:
        """Pick out every request that changes state.

        Returns:
            Each POST, PUT and DELETE, as logged.

        """
        return [r for r in self.requests if r[0] in WRITES]

    def published_reads(self, rid: str = "101") -> list[tuple[str, str, str]]:
        """Pick out the reads of the published record itself.

        Returns:
            Each GET of /api/records/<rid> - not its draft - as logged.

        """
        url = f"{ORIGIN}/api/records/{rid}"
        return [r for r in self.requests if r[0] == "GET" and r[1] == url]

    def legacy_metadata(self, rid: str = "101") -> dict[str, object]:
        """Decode the legacy deposit's metadata PUT.

        Returns:
            The `metadata` object the helper sent.

        """
        (i,) = self.made("PUT", f"/api/deposit/depositions/{rid}")
        return decoded.as_object(at(body_of(self.bodies[i]), "metadata"), "metadata")


def stub_zenodo(published: str, concept: str | None) -> Zenodo:
    """Build #1246's stub as a fake.

    Returns:
        The fake's answers: concept 100's latest is record 101 at
        1.3.5, and record 101 reads back as `published` says.

    """
    rights = {
        "both": [{"id": "mit"}, {"id": "apache-2.0"}],
        "one": [{"id": "mit"}],
    }
    record: Doc | None = None
    if published in rights:
        record = recorded_object(DRAFT)
        decoded.as_object(record["metadata"], "metadata")["rights"] = rights[published]
    z = Zenodo(records={"101": record})
    if concept is not None:
        z.latest["100"] = {
            "doi": "10.5281/zenodo.101",
            "conceptdoi": "10.5281/zenodo.100",
            "id": 101,
            "metadata": {"version": "1.3.5"},
        }
    return z


def run_helper(  # ruff: ignore[too-many-arguments] - one knob per row family
    licence: str,
    *,
    collapse: bool = False,
    published: str = "both",
    concept: str | None = None,
    zenodo: Zenodo | None = None,
    version: str = "1.3.5",
    repo: str = "monumental-archive/edtf",
    cff: str | None = None,
    cff_status: int = 0,
    script: bool = False,
) -> Run:
    """Run the helper with CITATION.cff stating `licence`.

    Args:
        licence: the `license:` line's value.
        collapse: make the fake's RDM PUT answer one licence, as if the
            write had not taken.
        published: what a read of the published record answers - "both"
            ids, "one", or "fail".
        concept: a concept DOI for CITATION.cff to record.
        zenodo: the fake's answers; None builds #1246's stub.
        version: the version being released.
        repo: the repository releasing.
        cff: the whole CITATION.cff, overriding `licence` and `concept`.
        cff_status: gh's exit status on the CITATION.cff read.
        script: start the helper as the step does, rather than call its
            `main` in this process; its two streams are read as one.

    Returns:
        The run's status, output and the fake's request log.

    """
    z = zenodo or stub_zenodo(published, concept)
    z.collapse = z.collapse or collapse
    pauses: list[float] = []
    with TemporaryDirectory() as d, Fake(z):
        tmp = Path(d)
        stubs = tmp / "bin"
        stubs.mkdir()
        (stubs / "gh").write_text(GH_STUB)
        (stubs / "gh").chmod(0o755)
        b64 = tmp / "cff.b64"
        text = cff if cff is not None else citation(licence, concept)
        b64.write_text(base64.b64encode(text.encode()).decode())
        summary = tmp / "summary"
        env = hermetic.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            ZENODO_TOKEN="stub-token",  # ruff: ignore[hardcoded-password-func-arg]
            BASE_URL=z.base,
            UNTRUSTED_VERSION=version,
            GH_TOKEN="stub",  # ruff: ignore[hardcoded-password-func-arg]
            TAG=f"v{version}",
            ORG_USER_AGENT="monumental-archive-publish (stub)",
            GITHUB_REPOSITORY=repo,
            GITHUB_STEP_SUMMARY=str(summary),
            STUB_CFF_B64=str(b64),
            STUB_CFF_STATUS=str(cff_status),
        )
        out = io.StringIO()
        if script:
            done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [sys.executable, str(HELPER), "--pipeline", PIPELINE],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=60,
            )
            code = done.returncode
            out.write(done.stdout)
        else:
            with (
                mock.patch.object(mint_doi, "pause", pauses.append),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(out),
            ):
                code = mint_doi.main(["--pipeline", PIPELINE], env)
        text = summary.read_text() if summary.exists() else ""
    return Run(
        code,
        out.getvalue(),
        list(z.log),
        [b.decode(errors="replace") for b in z.bodies],
        pauses,
        text,
    )


LEAD = "Licensed under the SPDX expression"
SENTENCE = f"{LEAD} MIT OR Apache-2.0, as CITATION.cff states it."


class TheFixturesAreTheRealShapes(unittest.TestCase):
    """The captured draft carries what the rows lean on."""

    def test_the_draft_is_the_collapsed_read_back(self) -> None:
        """The fixture is a draft whose rights a legacy PUT collapsed to one."""
        draft = recorded_object(DRAFT)
        self.assertTrue(draft["is_draft"])
        self.assertFalse(draft["is_published"])
        ids = [r["id"] for r in objects(at(draft, "metadata", "rights"), "rights")]
        self.assertEqual(ids, strings("mit"))

    def test_the_citation_is_a_first_mint(self) -> None:
        """The rendered citation has no concept identifier and the row's licence."""
        text = citation("MIT OR Apache-2.0")
        self.assertNotIn("identifiers:", text)
        self.assertIn("\nlicense: MIT OR Apache-2.0\n", text)
        self.assertIn("family-names:", text)


class OneId(unittest.TestCase):
    """`0BSD` -> one id, through the legacy deposit alone, as before."""

    run_: Run

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Run the helper once for every row of the class."""
        cls.run_ = run_helper("0BSD")

    def test_it_publishes(self) -> None:
        """The helper exits green with the stub's DOI."""
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout)
        self.assertIn("DOI 10.5281/zenodo.101", self.run_.stdout)

    def test_the_script_mints_as_the_step_runs_it(self) -> None:
        """The script row (#1698): started as the step starts it, same answer."""
        run = run_helper("0BSD", script=True)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("DOI 10.5281/zenodo.101", run.stdout)
        self.assertEqual(run.requests, self.run_.requests)

    def test_the_vocabulary_is_asked_for_the_one_id(self) -> None:
        """The vocabulary is asked for exactly the one id."""
        asked = [u for m, u, _ in self.run_.requests if "/vocabularies/" in u]
        self.assertEqual(
            asked, strings("https://zenodo.org/api/vocabularies/licenses/0bsd")
        )

    def test_the_legacy_licence_is_the_id(self) -> None:
        """The legacy deposit carries the id, lowercased."""
        self.assertEqual(self.run_.legacy_metadata()["license"], "0bsd")

    def test_no_rdm_request_and_no_sentence(self) -> None:
        """One id makes no InvenioRDM request and adds no sentence."""
        self.assertEqual(self.run_.made("GET", "/api/records/"), NOTHING)
        self.assertEqual(self.run_.made("PUT", "/api/records/"), NOTHING)
        description = decoded.as_str(
            self.run_.legacy_metadata()["description"], "description"
        )
        self.assertNotIn("Licensed under", description)


class TwoIds(unittest.TestCase):
    """`MIT OR Apache-2.0` -> both ids on the record, the sentence quoted."""

    run_: Run

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Run the helper once for every row of the class."""
        cls.run_ = run_helper("MIT OR Apache-2.0")

    def test_it_publishes(self) -> None:
        """The helper exits green with the stub's DOI."""
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout)

    def test_the_vocabulary_is_asked_for_each_before_any_deposit(self) -> None:
        """Each id is checked, in order, before the deposit is created."""
        asked = self.run_.made("GET", "/api/vocabularies/licenses/")
        ids: list[str] = [self.run_.requests[i][1].rsplit("/", 1)[1] for i in asked]
        self.assertEqual(ids, strings("mit", "apache-2.0"))
        create = min(self.run_.made("POST", "/api/deposit/depositions"))
        self.assertLess(max(asked), create)

    def test_the_description_quotes_the_expression_exactly(self) -> None:
        """The description ends quoting the expression as written."""
        description = decoded.as_str(
            self.run_.legacy_metadata()["description"], "description"
        )
        self.assertTrue(description.endswith(" " + SENTENCE))

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
        sent = body_of(self.run_.bodies[put])
        both: list[object] = [{"id": "mit"}, {"id": "apache-2.0"}]
        self.assertEqual(at(sent, "metadata", "rights"), both)
        keys: set[str] = {"metadata", "files"}
        sent_keys: set[str] = set(sent)
        self.assertEqual(sent_keys, keys)

    def test_the_rest_of_the_draft_is_sent_back_unchanged(self) -> None:
        """Only rights changes in what is sent back."""
        (put,) = self.run_.made("PUT", "/api/records/101/draft")
        sent = body_of(self.run_.bodies[put])
        draft = recorded_object(DRAFT)
        decoded.as_object(sent["metadata"], "metadata").pop("rights")
        decoded.as_object(draft["metadata"], "metadata").pop("rights")
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
        run = run_helper("MIT AND Apache-2.0")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(
            "Licensed under the SPDX expression MIT AND Apache-2.0,",
            decoded.as_str(run.legacy_metadata()["description"], "description"),
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
        run = run_helper("MIT OR Apache-2.0", published="one")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(SHORT, run.stdout)
        self.assertEqual(
            len(run.made("POST", "/api/deposit/depositions/101/actions/publish")),
            1,
        )

    def test_a_read_that_fails_is_unverified_never_a_pass(self) -> None:
        """A published record that cannot be read is red as unverified."""
        run = run_helper("MIT OR Apache-2.0", published="fail")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::the published record 101 could not be read back — the"
            " licences of DOI 10.5281/zenodo.101 are unverified",
            run.stdout,
        )
        self.assertNotIn("::notice::DOI", run.stdout)

    def test_a_rerun_over_a_published_record_with_one_id_is_red(self) -> None:
        """The #484 convergence path reaches the same check."""
        run = run_helper(
            "MIT OR Apache-2.0", published="one", concept="10.5281/zenodo.100"
        )
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(SHORT, run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), NOTHING)
        self.assertEqual(len(run.published_reads()), 1)

    def test_a_rerun_over_a_published_record_with_both_is_green(self) -> None:
        """A rerun over a complete record converges green."""
        run = run_helper("MIT OR Apache-2.0", concept="10.5281/zenodo.100")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("::notice::DOI 10.5281/zenodo.101", run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), NOTHING)
        self.assertEqual(len(run.published_reads()), 1)

    def test_one_id_never_reads_the_published_record(self) -> None:
        """0BSD, fresh or rerun: no published read, whatever it would say."""
        for concept in (None, "10.5281/zenodo.100"):
            run = run_helper("0BSD", published="fail", concept=concept)
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertEqual(run.published_reads(), NOTHING)


class Refusals(unittest.TestCase):
    """Each refusal names its cause and makes no deposit."""

    def assert_refused(self, run: Run, message: str) -> None:
        """Assert a red run, its message, and that no deposit was created."""
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(message, run.stdout)
        self.assertEqual(run.made("POST", "/api/deposit/depositions"), NOTHING)

    def test_with_is_refused_by_name(self) -> None:
        """WITH refuses, naming the operator."""
        self.assert_refused(
            run_helper("GPL-2.0-only WITH Classpath-exception-2.0"),
            "::error::CITATION.cff license 'GPL-2.0-only WITH Classpath-exception-2.0'"
            " joins ids with 'WITH'",
        )

    def test_parentheses_are_refused_by_name(self) -> None:
        """Parentheses refuse by name."""
        self.assert_refused(
            run_helper("(MIT OR Apache-2.0)"),
            "license '(MIT OR Apache-2.0)' uses parentheses",
        )

    def test_a_dangling_operator_is_refused(self) -> None:
        """An expression ending in an operator refuses."""
        self.assert_refused(
            run_helper("MIT OR"), "license 'MIT OR' is not a complete expression"
        )

    def test_an_id_absent_from_the_vocabulary_is_refused_naming_it(self) -> None:
        """An id the vocabulary 404s refuses, naming it."""
        run = run_helper("MIT OR Not-A-Licence")
        self.assert_refused(
            run,
            "::error::licence id 'not-a-licence' (from CITATION.cff"
            " 'MIT OR Not-A-Licence') is not in Zenodo's licence vocabulary",
        )
        self.assertEqual(len(run.made("GET", "/api/vocabularies/licenses/")), 2)

    def test_a_read_back_missing_an_id_never_publishes(self) -> None:
        """A read-back short of an id refuses before the publish."""
        run = run_helper("MIT OR Apache-2.0", collapse=True)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('the draft\'s licences read back as ["mit"]', run.stdout)
        self.assertEqual(
            run.made("POST", "/api/deposit/depositions/101/actions/publish"), NOTHING
        )


# ---------------------------------------------------------------------
# #1365: the later-mint paths, on the recorded responses.
# ---------------------------------------------------------------------

EDTF_CONCEPT = "24268583"
EDTF_PREV = "24268584"
CANON_CONCEPT = "21913604"
CANON_PREV = "24269508"
CANON_DRAFT = "24318079"
INHERITED = "4f7761b5-0cc0-4d68-98ad-f38fd53f0084"
BUCKET = "/api/files/2df85c4a-73c4-4dd0-8201-406824a6284b"
CANON_REPO = "monumental-archive/.github"
DEP = "/api/deposit/depositions"


def captured(name: str) -> object:
    """Read one body run 44121466994 captured.

    Returns:
        The parsed body.

    """
    return recorded(Path(str(CAPTURE).format(name)))


def captured_object(name: str) -> Doc:
    """Read one captured body that is one object.

    Returns:
        The parsed body.

    """
    return decoded.as_object(captured(name), name)


def captured_list(name: str) -> list[Doc]:
    """Read one captured body that is an array of objects.

    Returns:
        The objects, in order.

    """
    return objects(captured(name), name)


def canon_zenodo(**kw: object) -> Zenodo:
    """Serve the canon's concept from the recorded and captured answers.

    Every answer is production's except the publish response, which no
    capture can take without minting: it keeps the one shape the helper
    reads (`doi`, `conceptdoi`), and is proven only by the canon's next
    release. Once published, the new record reads as the recorded
    v1.68.4 record re-keyed to the draft's id and version - the only
    fields the oracle compares.

    Returns:
        The fake's answers.

    """
    prev = recorded_object(CANON_LATEST)
    nxt = copy.deepcopy(prev)
    nxt.update(id=int(CANON_DRAFT), recid=CANON_DRAFT)
    nxt["doi"] = f"10.5281/zenodo.{CANON_DRAFT}"
    decoded.as_object(nxt["metadata"], "metadata")["version"] = "1.68.5"
    z = Zenodo(
        latest={CANON_CONCEPT: prev},
        redirect=True,
        records={f"plain/{CANON_PREV}": prev, f"plain/{CANON_DRAFT}": nxt},
        drafts=captured_list("drafts-before"),
        newversion_body=captured("newversion"),
        draft_body=captured("newversion"),
        deposition=CANON_DRAFT,
        published_record=nxt,
        publish_body={
            "doi": f"10.5281/zenodo.{CANON_DRAFT}",
            "conceptdoi": f"10.5281/zenodo.{CANON_CONCEPT}",
        },
    )
    for k, v in kw.items():
        setattr(z, k, v)
    return z


def canon_run(z: Zenodo, licence: str = "0BSD", version: str = "1.68.5") -> Run:
    """Run the canon's release of `version` against `z`.

    Returns:
        The run.

    """
    return run_helper(
        licence,
        concept=f"10.5281/zenodo.{CANON_CONCEPT}",
        zenodo=z,
        version=version,
        repo=CANON_REPO,
    )


def edtf_zenodo() -> Zenodo:
    """Serve edtf's concept as production answered it, redirect and all.

    Returns:
        The fake's answers: the recorded v1.3.5 record and its
        InvenioRDM read-back.

    """
    prev = recorded_object(EDTF_LATEST)
    return Zenodo(
        latest={EDTF_CONCEPT: prev},
        redirect=True,
        records={EDTF_PREV: recorded_object(EDTF_RDM), f"plain/{EDTF_PREV}": prev},
    )


def no_redirect_handler() -> type[urllib.request.HTTPRedirectHandler]:
    """Find the helper's own redirect handler, the authenticated opener's.

    Returns:
        The one `HTTPRedirectHandler` subclass the helper defines.

    """
    (handler,) = (
        c
        for c in urllib.request.HTTPRedirectHandler.__subclasses__()
        if c.__module__ == mint_doi.__name__
    )
    return handler


class TheTokenNeverFollowsARedirect(unittest.TestCase):
    """An authenticated call answered with a 3xx stops there (#1698)."""

    def test_the_handler_makes_no_new_request(self) -> None:
        """Asked for the redirected request, as urllib asks, it makes none.

        urllib then raises the 3xx as an `HTTPError`, so the token is
        never sent on to the redirect's target.
        """
        old = urllib.request.Request(
            "https://zenodo.org/api/records/1/versions/latest",
            headers={"Authorization": "Bearer stub-token"},
        )
        new = no_redirect_handler()().redirect_request(
            old,
            io.BytesIO(),
            301,
            "Moved Permanently",
            http.client.HTTPMessage(),
            "https://zenodo.org/api/records/2",
        )
        self.assertIsNone(new)

    def test_an_authenticated_read_is_not_followed(self) -> None:
        """The 301 is the answer, though its target would answer 200."""
        z = edtf_zenodo()
        with Fake(z):
            client = mint_doi.Zenodo(z.base, "stub-token", "stub-agent")
            url = f"{z.base}/api/records/{EDTF_CONCEPT}/versions/latest"
            with self.assertRaises(mint_doi.HttpError) as caught:
                client.idem("GET", url)
        self.assertEqual(f"GET {url}: HTTP 301", str(caught.exception))


class TheRecordedResponses(unittest.TestCase):
    """The production captures carry what the later-mint rows lean on."""

    def test_the_canon_latest_is_its_concepts_published_version(self) -> None:
        """The canon's concept resolves to a published 0bsd v1.68.4."""
        rec = recorded_object(CANON_LATEST)
        self.assertEqual(rec["conceptrecid"], CANON_CONCEPT)
        self.assertEqual(rec["conceptdoi"], f"10.5281/zenodo.{CANON_CONCEPT}")
        self.assertEqual(at(rec, "metadata", "version"), "1.68.4")
        self.assertTrue(rec["submitted"])
        rdm = recorded_object(CANON_RDM)
        ids = [r["id"] for r in objects(at(rdm, "metadata", "rights"), "rights")]
        self.assertEqual(ids, strings("0bsd"))

    def test_edtf_latest_is_published_with_both_licences(self) -> None:
        """Edtf's concept resolves to v1.3.5, whose record states both ids."""
        rec = recorded_object(EDTF_LATEST)
        self.assertEqual(rec["conceptrecid"], EDTF_CONCEPT)
        self.assertEqual(str(rec["id"]), EDTF_PREV)
        self.assertEqual(at(rec, "metadata", "version"), "1.3.5")
        rdm = recorded_object(EDTF_RDM)
        self.assertTrue(rdm["is_published"])
        ids = [r["id"] for r in objects(at(rdm, "metadata", "rights"), "rights")]
        self.assertEqual(ids, strings("mit", "apache-2.0"))

    def test_newversion_answers_an_unsubmitted_draft_with_the_old_file(
        self,
    ) -> None:
        """C3: a draft of the concept, carrying v1.68.4's tarball."""
        self.assertIsInstance(captured("newversion"), dict)
        draft = captured_object("newversion")
        self.assertEqual(str(draft["id"]), CANON_DRAFT)
        self.assertEqual(draft["conceptrecid"], CANON_CONCEPT)
        self.assertFalse(draft["submitted"])
        self.assertEqual(
            at(draft, "links", "latest_draft"), f"{ORIGIN}{DEP}/{CANON_DRAFT}"
        )
        files: list[tuple[object, object]] = [
            (f["id"], f["filename"]) for f in objects(draft["files"], "files")
        ]
        inherited: list[tuple[object, object]] = [(INHERITED, ".github-1.68.4.tar.gz")]
        self.assertEqual(files, inherited)

    def test_the_draft_listing_answers_the_published_record_too(self) -> None:
        """C2: `status=draft` also lists the published v1.68.4."""
        (rec,) = captured_list("drafts-before")
        self.assertTrue(rec["submitted"])
        self.assertEqual(str(rec["id"]), CANON_PREV)
        (draft,) = captured_list("drafts-during")
        self.assertFalse(draft["submitted"])
        self.assertEqual(str(draft["id"]), CANON_DRAFT)

    def test_the_deleted_draft_is_gone(self) -> None:
        """C9: after the DELETE, the draft reads as a missing identifier."""
        gone: Doc = {
            "status": 404,
            "message": "The persistent identifier does not exist.",
        }
        self.assertEqual(captured("draft-gone"), gone)
        self.assertEqual(captured_object("draft-after-file-delete")["files"], NOTHING)

    def test_only_the_full_title_finds_the_record(self) -> None:
        """M1-M4 (#1389): the repo name finds nothing, the full title finds it.

        C1, the same repo-name query in the capture run, answered `[]` too.
        """
        self.assertEqual(captured("titles"), NOTHING)
        self.assertEqual(measured("present-github"), NOTHING)
        self.assertEqual(measured("present-edtf"), NOTHING)
        for name, rid, title in (
            ("full-github", CANON_PREV, CANON_REPO),
            ("full-edtf", EDTF_PREV, "monumental-archive/edtf"),
        ):
            with self.subTest(name):
                (rec,) = objects(measured(name), name)
                self.assertEqual((str(rec["id"]), rec["title"]), (rid, title))
                self.assertTrue(rec["submitted"])


class AlreadyPublished(unittest.TestCase):
    """#484: a rerun over a published version converges, writing nothing."""

    def test_the_canon_converges_on_its_recorded_record(self) -> None:
        """The canon's own concept at v1.68.4: green, no write, no read-back."""
        run = canon_run(canon_zenodo(), version="1.68.4")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(
            f"::notice::DOI 10.5281/zenodo.{CANON_PREV} (concept"
            f" 10.5281/zenodo.{CANON_CONCEPT})",
            run.stdout,
        )
        self.assertIn("**Already published**", run.summary)
        self.assertEqual(run.writes(), NOTHING)
        last: list[tuple[str, str, str]] = [run.requests[-1]]
        self.assertEqual(run.published_reads(CANON_PREV), last)
        self.assertEqual(run.requests[-1][2], "")

    def test_edtf_converges_after_verifying_both_licences(self) -> None:
        """Edtf at v1.3.5: green, its record's licences read, no write."""
        run = run_helper(
            "MIT OR Apache-2.0",
            concept=f"10.5281/zenodo.{EDTF_CONCEPT}",
            zenodo=edtf_zenodo(),
            version="1.3.5",
        )
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(f"::notice::DOI 10.5281/zenodo.{EDTF_PREV}", run.stdout)
        self.assertIn("this run converged on it (#484)", run.summary)
        self.assertEqual(run.writes(), NOTHING)
        self.assertIn(("GET", f"{ORIGIN}/api/records/{EDTF_PREV}", RDM), run.requests)


class ALaterMint(unittest.TestCase):
    """The canon's v1.68.5 grows from v1.68.4 on the captured answers."""

    run_: Run

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Run the helper once for every row of the class."""
        cls.run_ = canon_run(canon_zenodo())

    def test_it_publishes_the_new_version(self) -> None:
        """Green through the concept's redirect, with the new DOI (#337)."""
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout)
        self.assertIn(f"::notice::DOI 10.5281/zenodo.{CANON_DRAFT}", self.run_.stdout)
        self.assertNotIn("First mint", self.run_.summary)

    def test_the_concept_is_resolved_through_the_redirect_without_a_token(
        self,
    ) -> None:
        """The oracle follows the measured 301 as a public read."""
        latest = f"{ORIGIN}/api/records/{CANON_CONCEPT}/versions/latest"
        first = [r[1] for r in self.run_.requests].index(latest)
        self.assertEqual(
            self.run_.requests[first + 1],
            ("GET", f"{ORIGIN}/api/records/{CANON_PREV}", ""),
        )

    def test_newversion_grows_from_the_latest_and_no_title_search(self) -> None:
        """Newversion is asked of the resolved record; no title guard runs."""
        path = f"{DEP}/{CANON_PREV}/actions/newversion"
        self.assertEqual(len(self.run_.made("POST", path)), 1)
        self.assertEqual(self.run_.made("GET", f"{DEP}?q=title"), NOTHING)
        self.assertNotIn(("POST", f"{ORIGIN}{DEP}", ""), self.run_.requests)

    def test_the_draft_is_read_from_latest_draft(self) -> None:
        """The deposition read is the URL newversion answered."""
        (newv,) = self.run_.made("POST", f"{DEP}/{CANON_PREV}/actions/newversion")
        self.assertEqual(
            self.run_.requests[newv + 1], ("GET", f"{ORIGIN}{DEP}/{CANON_DRAFT}", "")
        )

    def test_the_inherited_file_is_deleted_before_the_upload(self) -> None:
        """v1.68.4's tarball goes, by its captured id, before the new one."""
        (gone,) = self.run_.made("DELETE", f"{DEP}/{CANON_DRAFT}/files/{INHERITED}")
        (put,) = self.run_.made("PUT", f"{BUCKET}/.github-1.68.5.tar.gz")
        self.assertLess(gone, put)
        deletes: list[tuple[str, str, str]] = [
            r for r in self.run_.requests if r[0] == "DELETE"
        ]
        only: list[tuple[str, str, str]] = [self.run_.requests[gone]]
        self.assertEqual(deletes, only)

    def test_the_new_draft_carries_this_release(self) -> None:
        """The tarball and metadata go to the captured draft, then publish."""
        (put,) = self.run_.made("PUT", f"{BUCKET}/.github-1.68.5.tar.gz")
        self.assertEqual(self.run_.bodies[put], "tarball")
        meta = self.run_.legacy_metadata(CANON_DRAFT)
        self.assertEqual(meta["version"], "1.68.5")
        self.assertEqual(meta["license"], "0bsd")
        self.assertEqual(
            meta["description"], f"Release v1.68.5 of {CANON_REPO}. {PIPELINE}"
        )
        (publish,) = self.run_.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
        self.assertGreater(publish, put)

    def test_the_creator_is_family_comma_given(self) -> None:
        """The one deliberate difference from the bash (#1365).

        The bash's sed kept the whitespace before `given-names`, sending
        `Allen<TAB>    , Carl`; Zenodo normalised it to `Allen, Carl` on
        the record. The helper sends what the record states.
        """
        meta = self.run_.legacy_metadata(CANON_DRAFT)
        creators: list[object] = [{"name": "Allen, Carl"}]
        self.assertEqual(meta["creators"], creators)

    def test_two_licences_go_on_the_new_draft(self) -> None:
        """A later mint with two ids sets the rights on the captured draft."""
        z = canon_zenodo(
            records={
                f"plain/{CANON_PREV}": recorded(CANON_LATEST),
                CANON_DRAFT: recorded(EDTF_RDM),
            }
        )
        z.records[f"plain/{CANON_DRAFT}"] = z.published_record
        run = canon_run(z, licence="MIT OR Apache-2.0")
        self.assertEqual(run.returncode, 0, run.stdout)
        (put,) = run.made("PUT", f"/api/records/{CANON_DRAFT}/draft")
        (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
        self.assertLess(put, publish)
        sent = body_of(run.bodies[put])
        both: list[object] = [{"id": "mit"}, {"id": "apache-2.0"}]
        self.assertEqual(at(sent, "metadata", "rights"), both)


class StaleDrafts(unittest.TestCase):
    """Only an unsubmitted draft under the concept is deleted (C2, C5)."""

    def test_the_published_record_in_the_draft_listing_is_kept(self) -> None:
        """C2 as captured lists v1.68.4 itself: nothing but the old file goes."""
        run = canon_run(canon_zenodo())
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertNotIn("deleting stale", run.stdout)
        deleted = [r[1] for r in run.requests if r[0] == "DELETE"]
        self.assertEqual(
            deleted, strings(f"{ORIGIN}{DEP}/{CANON_DRAFT}/files/{INHERITED}")
        )

    def test_a_stale_draft_is_deleted_before_newversion(self) -> None:
        """C5 as captured holds an interrupted run's draft: it goes first.

        The captured newversion then answers the same id again; the fake
        does not model Zenodo minting a fresh one.
        """
        z = canon_zenodo(drafts=captured("drafts-during"))
        run = canon_run(z)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(
            f"::notice::deleting stale unpublished draft {z.base}{DEP}/{CANON_DRAFT}"
            " left by an interrupted run",
            run.stdout,
        )
        target: tuple[str, str] = ("DELETE", f"{ORIGIN}{DEP}/{CANON_DRAFT}")
        (stale,) = [
            i for i, r in enumerate(run.requests)
            if (r[0], r[1]) == target
        ]  # fmt: skip
        (newv,) = run.made("POST", f"{DEP}/{CANON_PREV}/actions/newversion")
        self.assertLess(stale, newv)

    def test_both_together_delete_only_the_draft(self) -> None:
        """C2 and C5 in one listing: the published record is never touched."""
        z = canon_zenodo(
            drafts=[*captured_list("drafts-before"), *captured_list("drafts-during")]
        )
        run = canon_run(z)
        self.assertEqual(run.returncode, 0, run.stdout)
        deleted = [r[1] for r in run.requests if r[0] == "DELETE"]
        self.assertEqual(
            deleted,
            strings(
                f"{ORIGIN}{DEP}/{CANON_DRAFT}",
                f"{ORIGIN}{DEP}/{CANON_DRAFT}/files/{INHERITED}",
            ),
        )
        touched: list[str] = [u for u in deleted if CANON_PREV in u]
        self.assertFalse(touched)


class LostPublishResponse(unittest.TestCase):
    """#484: the truth after a failed publish is the archive's state."""

    def test_a_lost_response_over_a_landed_publish_is_recovered(self) -> None:
        """The connection drops, the record landed: green, 20 s spacing."""
        run = canon_run(canon_zenodo(publish="drop", appear_after=1))
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(f"::notice::DOI 10.5281/zenodo.{CANON_DRAFT}", run.stdout)
        self.assertIn("**Published despite a lost response**", run.summary)
        one_pause: list[float] = [20]
        self.assertEqual(run.pauses, one_pause)
        (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
        latest = run.made("GET", f"/api/records/{CANON_CONCEPT}/versions/latest")
        self.assertEqual(len([i for i in latest if i > publish]), 2)

    def test_a_failed_publish_that_never_lands_is_red(self) -> None:
        """Nothing published: three reads, 20 s apart, then red by name."""
        run = canon_run(canon_zenodo(publish="fail"))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::publish failed and the concept's latest published version"
            " is not 1.68.5",
            run.stdout,
        )
        self.assertNotIn("::notice::DOI", run.stdout)
        two_pauses: list[float] = [20, 20]
        self.assertEqual(run.pauses, two_pauses)
        (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
        latest = run.made("GET", f"/api/records/{CANON_CONCEPT}/versions/latest")
        self.assertEqual(len([i for i in latest if i > publish]), 3)


def measured(name: str) -> object:
    """Read one answer run 44129109573 measured (#1389).

    Returns:
        The parsed body.

    """
    return recorded(TESTDATA / f"zenodo-measure-{name}.json")


def measured_titles() -> dict[str, object]:
    """Map each measured title query to production's answer (#1389).

    Run 44129109573, 2026-11-17, GET only. The repo-name query (the
    guard before #1389) found nothing for either title; the full title
    quoted found exactly the record, for the dotted name and the plain
    one. The literal-slash forms answered byte-identically to these.

    Returns:
        The `q` value, as sent, to its answer.

    """
    return {
        "title:%22.github%22": measured("present-github"),
        "title:%22edtf%22": measured("present-edtf"),
        "title:%22monumental-archive%2F.github%22": measured("full-github"),
        "title:%22monumental-archive%2Fedtf%22": measured("full-edtf"),
    }


def first_mint_zenodo(titles: object) -> Zenodo:
    """Serve a first mint of the canon, the title query answering `titles`.

    The create POST was not part of the capture; it answers the captured
    draft deposition (C3), a real unsubmitted deposition of this title.

    Returns:
        The fake's answers.

    """
    return canon_zenodo(
        latest={},
        titles=titles,
        create_body=captured("newversion"),
        publish_body={"doi": f"10.5281/zenodo.{CANON_DRAFT}"},
    )


class FirstMintGuards(unittest.TestCase):
    """The refusals before any deposit exists."""

    def test_the_canons_first_mint_is_refused_on_the_measured_answers(
        self,
    ) -> None:
        """#1389's fail-before test: the canon's title, as production answers.

        The fake answers each title query as run 44129109573 measured. The
        guard before #1389 asked `title:".github"`, got M1 (`[]`) and let a
        second first mint through; the full title gets M3, the submitted
        `monumental-archive/.github`, and refuses.
        """
        z = first_mint_zenodo([])
        z.titles_by_query = measured_titles()
        run = run_helper("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::a submitted deposition already bears this title but"
            " CITATION.cff records no concept DOI — record it under identifiers:"
            " before minting again",
            run.stdout,
        )
        self.assertEqual(run.writes(), NOTHING)
        (listing,) = run.made("GET", f"{DEP}?")
        self.assertEqual(
            run.requests[listing][1],
            f"{ORIGIN}{DEP}?q=title:%22monumental-archive%2F.github%22&size=100&page=1",
        )

    def test_a_title_with_no_dot_is_refused_on_the_measured_answers(self) -> None:
        """Edtf's title: M4 holds its submitted record, so a first mint refuses."""
        z = first_mint_zenodo([])
        z.titles_by_query = measured_titles()
        run = run_helper(
            "0BSD", zenodo=z, version="1.3.6", repo="monumental-archive/edtf"
        )
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::a submitted deposition already bears this title", run.stdout
        )
        self.assertEqual(run.writes(), NOTHING)

    def test_the_old_query_answer_could_never_refuse(self) -> None:
        """M1, what `title:".github"` answered: nothing, so a mint went on."""
        run = run_helper("0BSD", zenodo=first_mint_zenodo(measured("present-github")),
                         version="1.68.5", repo=CANON_REPO)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("**First mint**", run.summary)
        self.assertEqual(len(run.made("POST", DEP)), 2)

    def test_an_unsubmitted_title_alone_does_not_refuse(self) -> None:
        """C5, the captured unsubmitted draft of the title: the mint goes on."""
        run = run_helper("0BSD", zenodo=first_mint_zenodo(captured("drafts-during")),
                         version="1.68.5", repo=CANON_REPO)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("**First mint**", run.summary)

    def test_a_title_on_a_later_page_refuses(self) -> None:
        """Page 1 full of unsubmitted drafts, the minted title on page 2."""
        (draft,) = captured_list("drafts-during")
        z = first_mint_zenodo([])
        z.title_pages = [[draft] * mint_doi.LISTING_PAGE, captured("drafts-before")]
        run = run_helper("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::a submitted deposition already bears this title", run.stdout
        )
        self.assertEqual(len(run.made("GET", f"{DEP}?q=title")), 2)
        self.assertEqual(run.writes(), NOTHING)

    def test_a_listing_page_that_cannot_be_read_refuses(self) -> None:
        """Page 2 answers 503 on every try: refused, nothing created."""
        (draft,) = captured_list("drafts-during")
        z = first_mint_zenodo([])
        z.title_pages = [[draft] * mint_doi.LISTING_PAGE, 503]
        run = run_helper("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            # The query's own `%` is escaped as the runner reads it (#1660).
            "::error::the deposition listing"
            " (q=title:%2522monumental-archive%252F.github%2522) could not be"
            f" read to its end ({mint_doi.LISTING_PAGE} entries read) — a first mint"
            " is never checked against part of it",
            run.stdout,
        )
        self.assertEqual(run.writes(), NOTHING)

    def test_a_listing_still_full_at_the_page_bound_refuses(self) -> None:
        """Full pages to the bound: the bound refuses, never passes."""
        (draft,) = captured_list("drafts-during")
        z = first_mint_zenodo([])
        z.title_pages = [[draft] * mint_doi.LISTING_PAGE] * 3
        with mock.patch.object(mint_doi, "LISTING_MAX_PAGES", 3):
            run = run_helper("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            f"could not be read to its end ({3 * mint_doi.LISTING_PAGE} entries read)",
            run.stdout,
        )
        self.assertEqual(len(run.made("GET", f"{DEP}?q=title")), 3)
        self.assertEqual(run.writes(), NOTHING)

    def test_a_vocabulary_answering_500_refuses_as_unchecked(self) -> None:
        """A 500 is retried as transient, then refused; no deposit."""
        z = stub_zenodo("both", None)
        z.vocab_status = 500
        run = run_helper("0BSD", zenodo=z)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::Zenodo's licence vocabulary answered HTTP 500 for '0bsd'"
            " — not minting on an unchecked licence",
            run.stdout,
        )
        self.assertEqual(len(run.made("GET", "/api/vocabularies/licenses/0bsd")), 6)
        self.assertEqual(run.writes(), NOTHING)

    def test_no_authors_refuses_before_any_request(self) -> None:
        """A citation with no authors is red by name; Zenodo is never asked."""
        cff = "\n".join(
            line
            for line in citation("0BSD").splitlines()
            if "family-names" not in line and "given-names" not in line
        )
        run = run_helper("0BSD", cff=cff)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::CITATION.cff carries no authors — the record credits people,"
            " never an account name",
            run.stdout,
        )
        self.assertEqual(run.requests, NOTHING)

    def test_no_citation_refuses_by_name(self) -> None:
        """Gh failing to fetch CITATION.cff is red, naming the tag."""
        run = run_helper("0BSD", cff="")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::no CITATION.cff at v1.3.5 — a DOI-minting repo derives one"
            " from REUSE.toml (fix:citation; lint:citation gates this upstream)",
            run.stdout,
        )
        self.assertEqual(run.requests, NOTHING)

    def test_a_failed_read_refuses_though_its_body_decodes(self) -> None:
        """Gh's exit status decides, never the decoder (#103).

        A 404 reached the fallback only because its error body happened
        not to be valid base64. Here gh fails after printing a body that
        decodes to a whole CITATION.cff: the read failed, so it refuses.
        """
        run = run_helper("0BSD", cff_status=1)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::no CITATION.cff at v1.3.5 — a DOI-minting repo derives one"
            " from REUSE.toml (fix:citation; lint:citation gates this upstream)",
            run.stdout,
        )
        self.assertEqual(run.requests, NOTHING)

    def test_a_concept_doi_without_a_record_id_refuses(self) -> None:
        """A recorded concept DOI must end in a Zenodo record id."""
        run = run_helper("0BSD", concept="10.5281/zenodo.abc")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::CITATION.cff concept DOI '10.5281/zenodo.abc' does not end"
            " in a Zenodo record id",
            run.stdout,
        )
        self.assertEqual(run.requests[-1][1].rsplit("/", 1)[1], "0bsd")

    def test_an_unresolvable_concept_refuses(self) -> None:
        """A concept with no published version is red by name, nothing created."""
        run = run_helper("0BSD", concept="10.5281/zenodo.555")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::the concept record 555 did not resolve to a latest"
            " published version",
            run.stdout,
        )
        self.assertEqual(run.writes(), NOTHING)


# ---------------------------------------------------------------------
# The CITATION.cff reader and the licence expression, as tables.
# ---------------------------------------------------------------------

AUTHORS = """authors:
  - family-names: Allen
    given-names: Carl
  - family-names: "van Rossum"
    given-names: "Guido"
"""


class ReadCitation(unittest.TestCase):
    """`read_citation`: the three fields, one row per shape."""

    def test_the_canons_own_citation(self) -> None:
        """The canon's own CITATION.cff reads as itself."""
        got = mint_doi.read_citation((CANON / "CITATION.cff").read_text())
        self.assertEqual(got.creators, strings("Allen, Carl"))
        self.assertEqual(got.licence, "0BSD")
        self.assertEqual(got.concept_doi, "10.5281/zenodo.21913604")

    def test_rows(self) -> None:
        """Each row: text in, (creators, licence, concept) out."""
        rows = [
            ("quoted names unquote", AUTHORS,
             (["Allen, Carl", "van Rossum, Guido"], "", "")),
            ("no authors", "license: MIT\n", ([], "MIT", "")),
            ("family without given is no author",
             "  - family-names: Allen\n  orcid: x\n", ([], "", "")),
            ("first license line wins, verbatim",
             "license: MIT OR 0BSD \nlicense: Apache-2.0\n",
             ([], "MIT OR 0BSD ", "")),
            ("an indented license is not the top-level key",
             "  license: MIT\n", ([], "", "")),
            ("value before identifiers is ignored",
             "  value: 10.1/a\nidentifiers:\n  - type: doi\n    value: 10.1/b\n",
             ([], "", "10.1/b")),
            ("first value after identifiers wins",
             "identifiers:\n  - value: 10.1/c\n  - value: 10.1/d\n",
             ([], "", "10.1/c")),
            ("no identifiers is a first mint", "title: x\n", ([], "", "")),
            # #1556's shrunk example: zip(strict=True) raised on it.
            ("an empty file reads as nothing", "", ([], "", "")),
        ]  # fmt: skip
        for name, text, want in rows:
            with self.subTest(name):
                got = mint_doi.read_citation(text)
                self.assertEqual((got.creators, got.licence, got.concept_doi), want)


class LicenceIds(unittest.TestCase):
    """`licence_ids`: one row per guard branch, the message asserted."""

    def test_accepted(self) -> None:
        """Plain ids joined by OR or AND, lowercased in order."""
        rows = [
            ("0BSD", ["0bsd"]),
            ("MIT OR Apache-2.0", ["mit", "apache-2.0"]),
            ("MIT AND Apache-2.0 OR GPL-2.0+", ["mit", "apache-2.0", "gpl-2.0+"]),
        ]
        for licence, want in rows:
            with self.subTest(licence):
                self.assertEqual(mint_doi.licence_ids(licence), want)

    def test_refused(self) -> None:
        """Each refusal names its cause."""
        rows = [
            ("", "is empty"),
            ("  ", "is empty"),
            ("(MIT)", "uses parentheses"),
            ("MIT XOR 0BSD", "joins ids with 'XOR'"),
            ("OR MIT", "has 'OR' where an id belongs"),
            ("MIT OR WITH", "has 'WITH' where an id belongs"),
            ('"MIT"', "has '\"MIT\"', which is not an SPDX id"),
            ("MIT OR", "is not a complete expression"),
        ]
        for licence, why in rows:
            with self.subTest(licence):
                with self.assertRaises(mint_doi.RefusedError) as caught:
                    mint_doi.licence_ids(licence)
                self.assertEqual(
                    str(caught.exception),
                    f"CITATION.cff license '{licence}' {why} — the record carries"
                    " plain SPDX ids joined by OR or AND",
                )


class AnswerShapes(unittest.TestCase):
    """Each answer Zenodo gives is read once, or refused by name (#1682)."""

    def refused(self, call: Callable[[], object], message: str) -> None:
        """Assert calling `call` refuses with exactly `message`."""
        with self.assertRaises(mint_doi.RefusedError) as caught:
            call()
        self.assertEqual(message, str(caught.exception))

    def test_an_answer_that_is_not_a_response_is_refused(self) -> None:
        """What the opener answered is not an HTTP response: refused by name."""
        url = "https://zenodo.invalid/api/records/1"
        z = mint_doi.Zenodo("https://zenodo.invalid", "t", "a")
        with (
            mock.patch.object(z._public, "open", return_value=None),  # ruff: ignore[private-member-access] - the opener the row stubs
            self.assertRaises(mint_doi.RefusedError) as caught,
        ):
            z.public(url)
        self.assertEqual(
            f"{url} answered NoneType, not an HTTP response", str(caught.exception)
        )

    def test_an_answer_that_is_not_json_is_refused(self) -> None:
        """Named with the decoder's reason."""
        with self.assertRaises(mint_doi.RefusedError) as caught:
            mint_doi.loaded(b"<html>", "the new deposition")
        self.assertTrue(
            str(caught.exception).startswith("the new deposition is not JSON: ")
        )

    def test_each_wrong_deposition_shape_is_refused(self) -> None:
        """Not an object, no links, a link or file that is not readable, no id."""
        links = {"self": "s", "bucket": "b", "publish": "p"}
        what = "the new deposition"
        for value, message in (
            ([], f"{what} is not an object"),
            ({"id": 1}, f"{what}'s links is not an object"),
            (
                {"id": 1, "links": {**links, "self": 1}},
                f"{what}'s links.self is not a string",
            ),
            (
                {"id": 1, "links": {**links, "bucket": None}},
                f"{what}'s links.bucket is not a string",
            ),
            (
                {"id": 1, "links": {"self": "s", "bucket": "b"}},
                f"{what}'s links.publish is not a string",
            ),
            (
                {"id": 1, "links": links, "files": {"a": 1}},
                f"{what}'s files is not a list",
            ),
            (
                {"id": 1, "links": links, "files": [1]},
                f"a file of {what} is not an object",
            ),
            ({"id": 1, "links": links, "files": [{}]}, f"a file of {what} has no id"),
            ({"links": links}, f"{what} has no id"),
        ):
            with self.subTest(value=value):
                self.refused(
                    functools.partial(mint_doi.deposition, value, what), message
                )

    def test_a_deposition_reads_its_id_links_and_files(self) -> None:
        """The shape the step reads, an id that is not a string compact."""
        got = mint_doi.deposition(
            {
                "id": 7,
                "links": {"self": "s", "bucket": "b", "publish": "p"},
                "files": [{"id": "f1"}],
            },
            "it",
        )
        self.assertEqual(got, mint_doi.Deposition("7", "s", "b", "p", ["f1"]))

    def test_rights_that_are_not_a_list_of_ids_are_unreadable(self) -> None:
        """Each refusal of the rights reader names the record."""
        where = "the published record 1"
        for value, message in (
            ({"metadata": {}}, f"{where}'s rights is not a list"),
            ({"metadata": {"rights": [1]}}, f"a right of {where} is not an object"),
            ({"metadata": {"rights": [{}]}}, f"a right of {where} has no id"),
            ({"metadata": []}, f"{where} is not an object"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(decoded.UndecodableError) as caught:
                    mint_doi.rights_ids(value, where)
                self.assertEqual(message, str(caught.exception))

    def test_a_newversion_answer_with_no_draft_link_is_refused(self) -> None:
        """No deposit is grown from an answer that names no draft."""
        run = canon_run(canon_zenodo(newversion_body={"links": {}}))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::the newversion answer's links.latest_draft is not a string",
            run.stdout,
        )

    def test_a_draft_listing_entry_that_is_not_an_object_is_refused(self) -> None:
        """The stale-draft sweep reads depositions, nothing else."""
        run = canon_run(canon_zenodo(drafts=[1]))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::an entry of the draft listing is not an object", run.stdout
        )

    def test_a_title_listing_entry_that_is_not_an_object_is_refused(self) -> None:
        """The first-mint guard reads depositions, nothing else."""
        run = run_helper(
            "0BSD", zenodo=first_mint_zenodo([1]), version="1.68.5", repo=CANON_REPO
        )
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(
            "::error::an entry of the deposition listing (q=title:", run.stdout
        )
        self.assertIn(") is not an object", run.stdout)
        self.assertEqual(run.writes(), NOTHING)


if __name__ == "__main__":
    unittest.main()

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
"""

from __future__ import annotations

import base64
import contextlib
import copy
import functools
import http.client
import io
import itertools
import json
import os
import sys
import threading
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Self, TypedDict, Unpack, override

import pytest

from belt import decoded
from belt.release import mint_doi
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# The requests a row counts: the licence lookups of a two-licence
# expression; the latest-version reads after one pause and after two;
# a first mint's two POSTs (the concept, then its draft); the title
# search's pages, read to the end and at the cap; and the tries at one
# licence that answers 500 every time.
LICENCE_LOOKUPS = 2
READS_AFTER_ONE_PAUSE = 2
READS_AFTER_TWO_PAUSES = 3
FIRST_MINT_POSTS = 2
TITLE_PAGES = 2
TITLE_PAGES_CAPPED = 3
LICENCE_TRIES = 6

CANON = support.CANON
# A JSON object as the fake and the helper exchange it.
Doc = dict[str, object]
# An empty list, whatever it would hold: requests, writes, entries.
NOTHING: list[object] = []
COMMAND = "mint-doi"
TESTDATA = Path(__file__).resolve().parent / "testdata"
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


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["mint-doi"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


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


# One answer of the fake: status, JSON body and headers; None drops it.
Answer = tuple[int, object, dict[str, str]] | None


class Asked(NamedTuple):
    """One request the fake was asked, read once."""

    method: str
    path: str
    base: str
    query: str
    accept: str
    body: bytes


class Route(NamedTuple):
    """One route: its method, what it matches, and how it answers."""

    method: str
    matches: Callable[[Asked], bool]
    answer: Callable[[Asked], Answer]


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

    def routes(self) -> list[Route]:
        """Name each route the fake answers, in the order it is tried.

        Returns:
            Each route's method, its match, and its answer.

        """
        dep = f"/api/deposit/depositions/{self.deposition}"
        draft = f"/api/records/{self.deposition}/draft"
        return [
            Route(
                "GET",
                lambda a: a.base.startswith("/api/vocabularies/licenses/"),
                self.licence,
            ),
            Route("GET", lambda a: a.base == "/api/deposit/depositions", self.listing),
            Route("POST", lambda a: a.path == "/api/deposit/depositions", self.created),
            Route(
                "DELETE",
                lambda a: a.base.startswith("/api/deposit/depositions/"),
                lambda _a: (204, None, {}),
            ),
            Route("POST", lambda a: a.base.endswith("/actions/newversion"), self.grown),
            Route("GET", lambda a: a.path == dep, self.draft_read),
            Route(
                "PUT",
                lambda a: a.base.startswith("/api/files/") or a.path == dep,
                lambda _a: (200, {}, {}),
            ),
            Route("GET", lambda a: a.path == draft and a.accept == RDM, self.rdm_read),
            Route("PUT", lambda a: a.path == draft and a.accept == RDM, self.rdm_put),
            Route(
                "POST", lambda a: a.path == f"{dep}/actions/publish", self.publishing
            ),
            Route(
                "GET", lambda a: a.base.endswith("/versions/latest"), self.latest_read
            ),
            Route(
                "GET", lambda a: a.base.startswith("/api/records/"), self.record_read
            ),
        ]

    def route(self, method: str, path: str, accept: str, body: bytes) -> Answer:
        """Answer one request by the first route that matches it.

        Returns:
            (status, JSON body, headers), or None to drop the connection.

        """
        base, _, query = path.partition("?")
        asked = Asked(method, path, base, query, accept, body)
        for route in self.routes():
            if route.method == method and route.matches(asked):
                return route.answer(asked)
        return 418, {"refused": f"{method} {path}"}, {}

    def licence(self, asked: Asked) -> Answer:
        """Answer the licence vocabulary: the id, or its 404.

        Returns:
            The answer.

        """
        if self.vocab_status is not None:
            return self.vocab_status, {}, {}
        key = asked.base.rsplit("/", 1)[1]
        return (200, {"id": key}, {}) if key in self.vocab else (404, {}, {})

    def listing(self, asked: Asked) -> Answer:
        """List depositions: a title search's page, or the drafts.

        Returns:
            The answer.

        """
        if "status=draft" not in asked.query:
            return self.title_page(asked.query)
        return 200, self.live(self.drafts), {}

    def created(self, _asked: Asked) -> Answer:
        """Create a deposition: the row's body, or the planted one.

        Returns:
            The answer.

        """
        if self.create_body is not None:
            return 201, self.live(self.create_body), {}
        return 200, self.deposit(self.deposition), {}

    def grown(self, _asked: Asked) -> Answer:
        """Grow a new version: the row's body, or the latest draft's link.

        Returns:
            The answer.

        """
        if self.newversion_body is not None:
            return 201, self.live(self.newversion_body), {}
        draft = f"{self.base}/api/deposit/depositions/{self.deposition}"
        return 200, {"links": {"latest_draft": draft}}, {}

    def draft_read(self, _asked: Asked) -> Answer:
        """Read the draft deposition: the row's body, or the planted one.

        Returns:
            The answer.

        """
        if self.draft_body is not None:
            return 200, self.live(self.draft_body), {}
        return 200, self.deposit(self.deposition), {}

    @staticmethod
    def rdm_read(_asked: Asked) -> Answer:
        """Read the draft record, in its RDM form.

        Returns:
            The answer.

        """
        return 200, recorded_object(DRAFT), {}

    def rdm_put(self, asked: Asked) -> Answer:
        """Take the draft record's rights, collapsed to one when the row says.

        Returns:
            The answer: what was sent, as stored.

        """
        sent = body_of(asked.body)
        if self.collapse:
            meta = decoded.as_object(sent["metadata"], "metadata")
            meta["rights"] = decoded.as_array(meta["rights"], "rights")[:1]
        return 200, sent, {}

    def publishing(self, _asked: Asked) -> Answer:
        """Publish: fail, drop the response, or answer the DOIs.

        Returns:
            The answer, or None to drop the connection.

        """
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

    def latest_read(self, asked: Asked) -> Answer:
        """Read a concept's latest version, stale or redirected as planted.

        Returns:
            The answer.

        """
        concept = asked.base.split("/")[3]
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

    def record_read(self, asked: Asked) -> Answer:
        """Read a record, plain or in its RDM form.

        Returns:
            The answer.

        """
        rid = asked.base.rsplit("/", 1)[1]
        if asked.accept != RDM:
            plain = self.records.get(f"plain/{rid}")
            return (200, plain, {}) if plain else (404, {}, {})
        if rid not in self.records:
            return 404, {"status": 404}, {}
        rdm = self.records[rid]
        return (200, rdm, {}) if rdm is not None else (503, {}, {})

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

        # http.server answers a verb through the handler's do_<VERB>.
        for verb in ("GET", "PUT", "POST", "DELETE"):
            setattr(Handler, f"do_{verb}", Handler.answer)

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


@dataclass
class Minting:
    """One mint laid out: the fake it talks to and the step's environment."""

    zenodo: Zenodo
    environ: dict[str, str]
    pauses: list[float]
    summary: Path

    def ran(self, status: int, said: tuple[str, str]) -> Run:
        """Read what the helper's run did, both streams as one.

        Returns:
            The run's status, output and the fake's request log.

        """
        out, err = said
        return Run(
            status,
            out + err,
            list(self.zenodo.log),
            [b.decode(errors="replace") for b in self.zenodo.bodies],
            self.pauses,
            self.summary.read_text() if self.summary.exists() else "",
        )


class Layout(TypedDict, total=False):
    """What a minting row lays out beside its licence, each with its default."""

    collapse: bool
    published: str
    concept: str | None
    zenodo: Zenodo | None
    version: str
    repo: str
    cff: str | None
    cff_status: int


# What a row that names none of them plants.
LAYOUT_DEFAULTS: Layout = {
    "collapse": False,
    "published": "both",
    "concept": None,
    "zenodo": None,
    "version": "1.3.5",
    "repo": "monumental-archive/edtf",
    "cff": None,
    "cff_status": 0,
}


@fixture
def mint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env
) -> Iterator[Callable[..., Minting]]:
    """Lay out the step's world: the fake Zenodo, a stub `gh`, its env.

    Yields:
        A function that lays out one mint and returns it.

    """
    served = contextlib.ExitStack()
    made = itertools.count()

    def lay_out(
        licence: str,
        **options: Unpack[Layout],
    ) -> Minting:
        """Lay out one mint with CITATION.cff stating `licence`.

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

        Returns:
            The mint, its fake serving.

        """
        facts: Layout = {**LAYOUT_DEFAULTS, **options}
        z = facts["zenodo"] or stub_zenodo(facts["published"], facts["concept"])
        z.collapse = z.collapse or facts["collapse"]
        served.enter_context(Fake(z))
        tmp = tmp_path / f"mint{next(made)}"
        stubs = tmp / "bin"
        stubs.mkdir(parents=True)
        (stubs / "gh").write_text(GH_STUB)
        (stubs / "gh").chmod(0o755)
        b64 = tmp / "cff.b64"
        text = (
            facts["cff"]
            if facts["cff"] is not None
            else citation(licence, facts["concept"])
        )
        b64.write_text(base64.b64encode(text.encode()).decode())
        summary = tmp / "summary"
        environ = env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            ZENODO_TOKEN=support.FAKE_CREDENTIAL,
            BASE_URL=z.base,
            UNTRUSTED_VERSION=facts["version"],
            GH_TOKEN=support.FAKE_CREDENTIAL,
            TAG=f"v{facts['version']}",
            ORG_USER_AGENT="monumental-archive-publish (stub)",
            GITHUB_REPOSITORY=facts["repo"],
            GITHUB_STEP_SUMMARY=str(summary),
            STUB_CFF_B64=str(b64),
            STUB_CFF_STATUS=str(facts["cff_status"]),
        )
        pauses: list[float] = []
        monkeypatch.setattr(sys, "argv", [COMMAND])
        monkeypatch.setattr(mint_doi, "pause", pauses.append)
        return Minting(z, environ, pauses, summary)

    with served:
        yield lay_out


LEAD = "Licensed under the SPDX expression"
SENTENCE = f"{LEAD} MIT OR Apache-2.0, as CITATION.cff states it."


# The captured draft carries what the rows lean on.
def test_the_draft_is_the_collapsed_read_back() -> None:
    """The fixture is a draft whose rights a legacy PUT collapsed to one."""
    draft = recorded_object(DRAFT)
    assert draft["is_draft"]
    assert not draft["is_published"]
    ids = [r["id"] for r in objects(at(draft, "metadata", "rights"), "rights")]
    assert ids == strings("mit")


def test_the_citation_is_a_first_mint() -> None:
    """The rendered citation has no concept identifier and the row's licence."""
    text = citation("MIT OR Apache-2.0")
    assert "identifiers:" not in text
    assert "\nlicense: MIT OR Apache-2.0\n" in text
    assert "family-names:" in text


class OneId:
    """`0BSD` -> one id, through the legacy deposit alone, as before."""

    minting: Minting

    def __init__(self, mint: Callable[..., Minting]) -> None:
        """Lay out the row's mint."""
        self.minting = mint("0BSD")


@fixture
def one_id(mint: Callable[..., Minting]) -> OneId:
    """Give each row its own OneId.

    Returns:
        The row's OneId.

    """
    return OneId(mint)


def test_it_publishes(one_id: OneId, capfd: pytest.CaptureFixture[str]) -> None:
    """The helper exits green with the stub's DOI."""
    status = mint_doi.main(["--pipeline", PIPELINE], one_id.minting.environ)
    run = one_id.minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "DOI 10.5281/zenodo.101" in run.stdout


def test_the_vocabulary_is_asked_for_the_one_id(
    one_id: OneId, capfd: pytest.CaptureFixture[str]
) -> None:
    """The vocabulary is asked for exactly the one id."""
    status = mint_doi.main(["--pipeline", PIPELINE], one_id.minting.environ)
    run = one_id.minting.ran(status, capfd.readouterr())
    asked = [u for m, u, _ in run.requests if "/vocabularies/" in u]
    assert asked == strings("https://zenodo.org/api/vocabularies/licenses/0bsd")


def test_the_legacy_licence_is_the_id(
    one_id: OneId, capfd: pytest.CaptureFixture[str]
) -> None:
    """The legacy deposit carries the id, lowercased."""
    status = mint_doi.main(["--pipeline", PIPELINE], one_id.minting.environ)
    run = one_id.minting.ran(status, capfd.readouterr())
    assert run.legacy_metadata()["license"] == "0bsd"


def test_no_rdm_request_and_no_sentence(
    one_id: OneId, capfd: pytest.CaptureFixture[str]
) -> None:
    """One id makes no InvenioRDM request and adds no sentence."""
    status = mint_doi.main(["--pipeline", PIPELINE], one_id.minting.environ)
    run = one_id.minting.ran(status, capfd.readouterr())
    assert run.made("GET", "/api/records/") == NOTHING
    assert run.made("PUT", "/api/records/") == NOTHING
    description = decoded.as_str(run.legacy_metadata()["description"], "description")
    assert "Licensed under" not in description


class TwoIds:
    """`MIT OR Apache-2.0` -> both ids on the record, the sentence quoted."""

    minting: Minting

    def __init__(self, mint: Callable[..., Minting]) -> None:
        """Lay out the row's mint."""
        self.minting = mint("MIT OR Apache-2.0")


@fixture
def two_ids(mint: Callable[..., Minting]) -> TwoIds:
    """Give each row its own TwoIds.

    Returns:
        The row's TwoIds.

    """
    return TwoIds(mint)


def test_two_ids_it_publishes(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """The helper exits green with the stub's DOI."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout


def test_the_vocabulary_is_asked_for_each_before_any_deposit(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each id is checked, in order, before the deposit is created."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    asked = run.made("GET", "/api/vocabularies/licenses/")
    ids: list[str] = [run.requests[i][1].rsplit("/", 1)[1] for i in asked]
    assert ids == strings("mit", "apache-2.0")
    create = min(run.made("POST", "/api/deposit/depositions"))
    assert max(asked) < create


def test_the_description_quotes_the_expression_exactly(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """The description ends quoting the expression as written."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    description = decoded.as_str(run.legacy_metadata()["description"], "description")
    assert description.endswith(" " + SENTENCE)


def test_the_rights_are_both_ids_and_land_before_the_publish(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """Both ids go on the draft after the legacy PUT, before the publish."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    (get,) = run.made("GET", "/api/records/101/draft")
    (put,) = run.made("PUT", "/api/records/101/draft")
    (legacy,) = run.made("PUT", "/api/deposit/depositions/101")
    (publish,) = run.made("POST", "/api/deposit/depositions/101/actions/publish")
    assert legacy < get
    assert put < publish
    assert run.requests[get][2] == RDM
    assert run.requests[put][2] == RDM
    sent = body_of(run.bodies[put])
    both: list[object] = [{"id": "mit"}, {"id": "apache-2.0"}]
    assert at(sent, "metadata", "rights") == both
    keys: set[str] = {"metadata", "files"}
    sent_keys: set[str] = set(sent)
    assert sent_keys == keys


def test_the_rest_of_the_draft_is_sent_back_unchanged(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """Only rights changes in what is sent back."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    (put,) = run.made("PUT", "/api/records/101/draft")
    sent = body_of(run.bodies[put])
    draft = recorded_object(DRAFT)
    decoded.as_object(sent["metadata"], "metadata").pop("rights")
    decoded.as_object(draft["metadata"], "metadata").pop("rights")
    assert sent["metadata"] == draft["metadata"]
    assert sent["files"] == draft["files"]


def test_the_published_record_is_read_back_after_the_publish(
    two_ids: TwoIds, capfd: pytest.CaptureFixture[str]
) -> None:
    """The published record's licences are read, with the RDM shape."""
    status = mint_doi.main(["--pipeline", PIPELINE], two_ids.minting.environ)
    run = two_ids.minting.ran(status, capfd.readouterr())
    (publish,) = run.made("POST", "/api/deposit/depositions/101/actions/publish")
    reads = run.published_reads()
    assert len(reads) == 1
    assert reads[0][2] == RDM
    assert run.requests.index(reads[0]) > publish


@pytest.mark.usefixtures("two_ids")
def test_and_takes_the_same_path(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """AND is accepted and quoted the same way as OR."""
    minting = mint("MIT AND Apache-2.0")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "Licensed under the SPDX expression MIT AND Apache-2.0," in decoded.as_str(
        run.legacy_metadata()["description"], "description"
    )


TWO = '["mit","apache-2.0"]'
SHORT = (
    "::error::mint-doi: the published record's licences read back as"
    f' ["mit"], not {TWO} — the DOI 10.5281/zenodo.101 states less than'
    " CITATION.cff"
)


# The published record is verified; it cannot be unpublished.
def test_a_published_record_with_one_id_is_red_by_name(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Publish kept one of two ids: red, naming the DOI."""
    minting = mint("MIT OR Apache-2.0", published="one")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert SHORT in run.stdout
    assert len(run.made("POST", "/api/deposit/depositions/101/actions/publish")) == 1


def test_a_read_that_fails_is_unverified_never_a_pass(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A published record that cannot be read is red as unverified."""
    minting = mint("MIT OR Apache-2.0", published="fail")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: the published record 101 could not be read back —"
        " the"
        " licences of DOI 10.5281/zenodo.101 are unverified"
    ) in run.stdout
    assert "::notice::DOI" not in run.stdout


def test_a_rerun_over_a_published_record_with_one_id_is_red(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """The #484 convergence path reaches the same check."""
    minting = mint("MIT OR Apache-2.0", published="one", concept="10.5281/zenodo.100")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert SHORT in run.stdout
    assert run.made("POST", "/api/deposit/depositions") == NOTHING
    assert len(run.published_reads()) == 1


def test_a_rerun_over_a_published_record_with_both_is_green(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A rerun over a complete record converges green."""
    minting = mint("MIT OR Apache-2.0", concept="10.5281/zenodo.100")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "::notice::DOI 10.5281/zenodo.101" in run.stdout
    assert run.made("POST", "/api/deposit/depositions") == NOTHING
    assert len(run.published_reads()) == 1


def test_one_id_never_reads_the_published_record(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """0BSD, fresh or rerun: no published read, whatever it would say."""
    for concept in (None, "10.5281/zenodo.100"):
        minting = mint("0BSD", published="fail", concept=concept)
        status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
        run = minting.ran(status, capfd.readouterr())
        assert run.returncode == 0, run.stdout
        assert run.published_reads() == NOTHING


# Each refusal names its cause and makes no deposit.
def assert_refused(run: Run, message: str) -> None:
    """Assert a red run, its message, and that no deposit was created."""
    assert run.returncode == 1, run.stdout
    assert message in run.stdout
    assert run.made("POST", "/api/deposit/depositions") == NOTHING


def test_with_is_refused_by_name(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """WITH refuses, naming the operator."""
    minting = mint("GPL-2.0-only WITH Classpath-exception-2.0")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "::error::mint-doi: CITATION.cff license 'GPL-2.0-only WITH"
        " Classpath-exception-2.0'"
        " joins ids with 'WITH'",
    )


def test_parentheses_are_refused_by_name(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Parentheses refuse by name."""
    minting = mint("(MIT OR Apache-2.0)")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "license '(MIT OR Apache-2.0)' uses parentheses",
    )


def test_a_dangling_operator_is_refused(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """An expression ending in an operator refuses."""
    minting = mint("MIT OR")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "license 'MIT OR' is not a complete expression",
    )


def test_an_id_absent_from_the_vocabulary_is_refused_naming_it(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """An id the vocabulary 404s refuses, naming it."""
    minting = mint("MIT OR Not-A-Licence")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert_refused(
        run,
        "::error::mint-doi: licence id 'not-a-licence' (from CITATION.cff"
        " 'MIT OR Not-A-Licence') is not in Zenodo's licence vocabulary",
    )
    assert len(run.made("GET", "/api/vocabularies/licenses/")) == LICENCE_LOOKUPS


def test_a_read_back_missing_an_id_never_publishes(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A read-back short of an id refuses before the publish."""
    minting = mint("MIT OR Apache-2.0", collapse=True)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert 'the draft\'s licences read back as ["mit"]' in run.stdout
    assert run.made("POST", "/api/deposit/depositions/101/actions/publish") == NOTHING


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


def canon_mint(
    mint: Callable[..., Minting],
    z: Zenodo,
    licence: str = "0BSD",
    version: str = "1.68.5",
) -> Minting:
    """Lay out the canon's release of `version` against `z`.

    Returns:
        The mint.

    """
    return mint(
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


# An authenticated call answered with a 3xx stops there (#1698).
def test_the_handler_makes_no_new_request() -> None:
    """Asked for the redirected request, as urllib asks, it makes none.

    urllib then raises the 3xx as an `HTTPError`, so the token is
    never sent on to the redirect's target.
    """
    old = urllib.request.Request(
        "https://zenodo.org/api/records/1/versions/latest",
        headers={"Authorization": f"Bearer {support.FAKE_CREDENTIAL}"},
    )
    new = no_redirect_handler()().redirect_request(
        old,
        io.BytesIO(),
        301,
        "Moved Permanently",
        http.client.HTTPMessage(),
        "https://zenodo.org/api/records/2",
    )
    assert new is None


def test_an_authenticated_read_is_not_followed() -> None:
    """The 301 is the answer, though its target would answer 200."""
    z = edtf_zenodo()
    with Fake(z):
        client = mint_doi.Zenodo(z.base, support.FAKE_CREDENTIAL, "stub-agent")
        url = f"{z.base}/api/records/{EDTF_CONCEPT}/versions/latest"
        with pytest.raises(mint_doi.HttpError) as caught:
            client.idem("GET", url)
    assert f"GET {url}: HTTP 301" == str(caught.value)


# The production captures carry what the later-mint rows lean on.
def test_the_canon_latest_is_its_concepts_published_version() -> None:
    """The canon's concept resolves to a published 0bsd v1.68.4."""
    rec = recorded_object(CANON_LATEST)
    assert rec["conceptrecid"] == CANON_CONCEPT
    assert rec["conceptdoi"] == f"10.5281/zenodo.{CANON_CONCEPT}"
    assert at(rec, "metadata", "version") == "1.68.4"
    assert rec["submitted"]
    rdm = recorded_object(CANON_RDM)
    ids = [r["id"] for r in objects(at(rdm, "metadata", "rights"), "rights")]
    assert ids == strings("0bsd")


def test_edtf_latest_is_published_with_both_licences() -> None:
    """Edtf's concept resolves to v1.3.5, whose record states both ids."""
    rec = recorded_object(EDTF_LATEST)
    assert rec["conceptrecid"] == EDTF_CONCEPT
    assert str(rec["id"]) == EDTF_PREV
    assert at(rec, "metadata", "version") == "1.3.5"
    rdm = recorded_object(EDTF_RDM)
    assert rdm["is_published"]
    ids = [r["id"] for r in objects(at(rdm, "metadata", "rights"), "rights")]
    assert ids == strings("mit", "apache-2.0")


def test_newversion_answers_an_unsubmitted_draft_with_the_old_file() -> None:
    """C3: a draft of the concept, carrying v1.68.4's tarball."""
    assert isinstance(captured("newversion"), dict)
    draft = captured_object("newversion")
    assert str(draft["id"]) == CANON_DRAFT
    assert draft["conceptrecid"] == CANON_CONCEPT
    assert not draft["submitted"]
    assert at(draft, "links", "latest_draft") == f"{ORIGIN}{DEP}/{CANON_DRAFT}"
    files: list[tuple[object, object]] = [
        (f["id"], f["filename"]) for f in objects(draft["files"], "files")
    ]
    inherited: list[tuple[object, object]] = [(INHERITED, ".github-1.68.4.tar.gz")]
    assert files == inherited


def test_the_draft_listing_answers_the_published_record_too() -> None:
    """C2: `status=draft` also lists the published v1.68.4."""
    (rec,) = captured_list("drafts-before")
    assert rec["submitted"]
    assert str(rec["id"]) == CANON_PREV
    (draft,) = captured_list("drafts-during")
    assert not draft["submitted"]
    assert str(draft["id"]) == CANON_DRAFT


def test_the_deleted_draft_is_gone() -> None:
    """C9: after the DELETE, the draft reads as a missing identifier."""
    gone: Doc = {
        "status": 404,
        "message": "The persistent identifier does not exist.",
    }
    assert captured("draft-gone") == gone
    assert captured_object("draft-after-file-delete")["files"] == NOTHING


@pytest.mark.parametrize(
    ("name", "rid", "title"),
    [
        ("full-github", CANON_PREV, CANON_REPO),
        ("full-edtf", EDTF_PREV, "monumental-archive/edtf"),
    ],
)
def test_only_the_full_title_finds_the_record(
    name: object, rid: object, title: object
) -> None:
    """M1-M4 (#1389): the repo name finds nothing, the full title finds it.

    C1, the same repo-name query in the capture run, answered `[]` too.
    """
    assert captured("titles") == NOTHING
    assert measured("present-github") == NOTHING
    assert measured("present-edtf") == NOTHING
    (rec,) = objects(measured(name), name)
    assert (str(rec["id"]), rec["title"]) == (rid, title)
    assert rec["submitted"]


# #484: a rerun over a published version converges, writing nothing.
def test_the_canon_converges_on_its_recorded_record(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """The canon's own concept at v1.68.4: green, no write, no read-back."""
    minting = canon_mint(mint, canon_zenodo(), version="1.68.4")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert (
        f"::notice::DOI 10.5281/zenodo.{CANON_PREV} (concept"
        f" 10.5281/zenodo.{CANON_CONCEPT})"
    ) in run.stdout
    assert "**Already published**" in run.summary
    assert run.writes() == NOTHING
    last: list[tuple[str, str, str]] = [run.requests[-1]]
    assert run.published_reads(CANON_PREV) == last
    assert not run.requests[-1][2]


def test_edtf_converges_after_verifying_both_licences(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Edtf at v1.3.5: green, its record's licences read, no write."""
    minting = mint(
        "MIT OR Apache-2.0",
        concept=f"10.5281/zenodo.{EDTF_CONCEPT}",
        zenodo=edtf_zenodo(),
        version="1.3.5",
    )
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert f"::notice::DOI 10.5281/zenodo.{EDTF_PREV}" in run.stdout
    assert "this run converged on it (#484)" in run.summary
    assert run.writes() == NOTHING
    assert ("GET", f"{ORIGIN}/api/records/{EDTF_PREV}", RDM) in run.requests


class ALaterMint:
    """The canon's v1.68.5 grows from v1.68.4 on the captured answers."""

    minting: Minting

    def __init__(self, mint: Callable[..., Minting]) -> None:
        """Lay out the row's mint."""
        self.minting = canon_mint(mint, canon_zenodo())


@fixture
def a_later_mint(mint: Callable[..., Minting]) -> ALaterMint:
    """Give each row its own ALaterMint.

    Returns:
        The row's ALaterMint.

    """
    return ALaterMint(mint)


def test_it_publishes_the_new_version(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """Green through the concept's redirect, with the new DOI (#337)."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert f"::notice::DOI 10.5281/zenodo.{CANON_DRAFT}" in run.stdout
    assert "First mint" not in run.summary


def test_the_concept_is_resolved_through_the_redirect_without_a_token(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """The oracle follows the measured 301 as a public read."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    latest = f"{ORIGIN}/api/records/{CANON_CONCEPT}/versions/latest"
    first = [r[1] for r in run.requests].index(latest)
    assert run.requests[first + 1] == (
        "GET",
        f"{ORIGIN}/api/records/{CANON_PREV}",
        "",
    )


def test_newversion_grows_from_the_latest_and_no_title_search(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """Newversion is asked of the resolved record; no title guard runs."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    path = f"{DEP}/{CANON_PREV}/actions/newversion"
    assert len(run.made("POST", path)) == 1
    assert run.made("GET", f"{DEP}?q=title") == NOTHING
    assert ("POST", f"{ORIGIN}{DEP}", "") not in run.requests


def test_the_draft_is_read_from_latest_draft(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """The deposition read is the URL newversion answered."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    (newv,) = run.made("POST", f"{DEP}/{CANON_PREV}/actions/newversion")
    assert run.requests[newv + 1] == (
        "GET",
        f"{ORIGIN}{DEP}/{CANON_DRAFT}",
        "",
    )


def test_the_inherited_file_is_deleted_before_the_upload(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """v1.68.4's tarball goes, by its captured id, before the new one."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    (gone,) = run.made("DELETE", f"{DEP}/{CANON_DRAFT}/files/{INHERITED}")
    (put,) = run.made("PUT", f"{BUCKET}/.github-1.68.5.tar.gz")
    assert gone < put
    deletes: list[tuple[str, str, str]] = [r for r in run.requests if r[0] == "DELETE"]
    only: list[tuple[str, str, str]] = [run.requests[gone]]
    assert deletes == only


def test_the_new_draft_carries_this_release(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """The tarball and metadata go to the captured draft, then publish."""
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    (put,) = run.made("PUT", f"{BUCKET}/.github-1.68.5.tar.gz")
    assert run.bodies[put] == "tarball"
    meta = run.legacy_metadata(CANON_DRAFT)
    assert meta["version"] == "1.68.5"
    assert meta["license"] == "0bsd"
    assert meta["description"] == f"Release v1.68.5 of {CANON_REPO}. {PIPELINE}"
    (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
    assert publish > put


def test_the_creator_is_family_comma_given(
    a_later_mint: ALaterMint, capfd: pytest.CaptureFixture[str]
) -> None:
    """The one deliberate difference from the bash (#1365).

    The bash's sed kept the whitespace before `given-names`, sending
    `Allen<TAB>    , Carl`; Zenodo normalised it to `Allen, Carl` on
    the record. The helper sends what the record states.
    """
    status = mint_doi.main(["--pipeline", PIPELINE], a_later_mint.minting.environ)
    run = a_later_mint.minting.ran(status, capfd.readouterr())
    meta = run.legacy_metadata(CANON_DRAFT)
    creators: list[object] = [{"name": "Allen, Carl"}]
    assert meta["creators"] == creators


@pytest.mark.usefixtures("a_later_mint")
def test_two_licences_go_on_the_new_draft(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A later mint with two ids sets the rights on the captured draft."""
    z = canon_zenodo(
        records={
            f"plain/{CANON_PREV}": recorded(CANON_LATEST),
            CANON_DRAFT: recorded(EDTF_RDM),
        }
    )
    z.records[f"plain/{CANON_DRAFT}"] = z.published_record
    minting = canon_mint(mint, z, licence="MIT OR Apache-2.0")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    (put,) = run.made("PUT", f"/api/records/{CANON_DRAFT}/draft")
    (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
    assert put < publish
    sent = body_of(run.bodies[put])
    both: list[object] = [{"id": "mit"}, {"id": "apache-2.0"}]
    assert at(sent, "metadata", "rights") == both


# Only an unsubmitted draft under the concept is deleted (C2, C5).
def test_the_published_record_in_the_draft_listing_is_kept(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """C2 as captured lists v1.68.4 itself: nothing but the old file goes."""
    minting = canon_mint(mint, canon_zenodo())
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "deleting stale" not in run.stdout
    deleted = [r[1] for r in run.requests if r[0] == "DELETE"]
    assert deleted == strings(f"{ORIGIN}{DEP}/{CANON_DRAFT}/files/{INHERITED}")


def test_a_stale_draft_is_deleted_before_newversion(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """C5 as captured holds an interrupted run's draft: it goes first.

    The captured newversion then answers the same id again; the fake
    does not model Zenodo minting a fresh one.
    """
    z = canon_zenodo(drafts=captured("drafts-during"))
    minting = canon_mint(mint, z)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert (
        f"::notice::deleting stale unpublished draft {z.base}{DEP}/{CANON_DRAFT}"
        " left by an interrupted run"
    ) in run.stdout
    target: tuple[str, str] = ("DELETE", f"{ORIGIN}{DEP}/{CANON_DRAFT}")
    (stale,) = [i for i, r in enumerate(run.requests) if (r[0], r[1]) == target]
    (newv,) = run.made("POST", f"{DEP}/{CANON_PREV}/actions/newversion")
    assert stale < newv


def test_both_together_delete_only_the_draft(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """C2 and C5 in one listing: the published record is never touched."""
    z = canon_zenodo(
        drafts=[*captured_list("drafts-before"), *captured_list("drafts-during")]
    )
    minting = canon_mint(mint, z)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    deleted = [r[1] for r in run.requests if r[0] == "DELETE"]
    assert deleted == strings(
        f"{ORIGIN}{DEP}/{CANON_DRAFT}",
        f"{ORIGIN}{DEP}/{CANON_DRAFT}/files/{INHERITED}",
    )
    touched: list[str] = [u for u in deleted if CANON_PREV in u]
    assert not touched


# #484: the truth after a failed publish is the archive's state.
def test_a_lost_response_over_a_landed_publish_is_recovered(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """The connection drops, the record landed: green, 20 s spacing."""
    minting = canon_mint(mint, canon_zenodo(publish="drop", appear_after=1))
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert f"::notice::DOI 10.5281/zenodo.{CANON_DRAFT}" in run.stdout
    assert "**Published despite a lost response**" in run.summary
    one_pause: list[float] = [20]
    assert run.pauses == one_pause
    (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
    latest = run.made("GET", f"/api/records/{CANON_CONCEPT}/versions/latest")
    assert len([i for i in latest if i > publish]) == READS_AFTER_ONE_PAUSE


def test_a_failed_publish_that_never_lands_is_red(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Nothing published: three reads, 20 s apart, then red by name."""
    minting = canon_mint(mint, canon_zenodo(publish="fail"))
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: publish failed and the concept's latest published"
        " version"
        " is not 1.68.5"
    ) in run.stdout
    assert "::notice::DOI" not in run.stdout
    two_pauses: list[float] = [20, 20]
    assert run.pauses == two_pauses
    (publish,) = run.made("POST", f"{DEP}/{CANON_DRAFT}/actions/publish")
    latest = run.made("GET", f"/api/records/{CANON_CONCEPT}/versions/latest")
    assert len([i for i in latest if i > publish]) == READS_AFTER_TWO_PAUSES


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


# The refusals before any deposit exists.
def test_the_canons_first_mint_is_refused_on_the_measured_answers(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """#1389's fail-before test: the canon's title, as production answers.

    The fake answers each title query as run 44129109573 measured. The
    guard before #1389 asked `title:".github"`, got M1 (`[]`) and let a
    second first mint through; the full title gets M3, the submitted
    `monumental-archive/.github`, and refuses.
    """
    z = first_mint_zenodo([])
    z.titles_by_query = measured_titles()
    minting = mint("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: a submitted deposition already bears this title but"
        " CITATION.cff records no concept DOI — record it under identifiers:"
        " before minting again"
    ) in run.stdout
    assert run.writes() == NOTHING
    (listing,) = run.made("GET", f"{DEP}?")
    assert (
        run.requests[listing][1]
        == f"{ORIGIN}{DEP}?q=title:%22monumental-archive%2F.github%22&size=100&page=1"
    )


def test_a_title_with_no_dot_is_refused_on_the_measured_answers(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Edtf's title: M4 holds its submitted record, so a first mint refuses."""
    z = first_mint_zenodo([])
    z.titles_by_query = measured_titles()
    minting = mint("0BSD", zenodo=z, version="1.3.6", repo="monumental-archive/edtf")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: a submitted deposition already bears this title"
        in run.stdout
    )
    assert run.writes() == NOTHING


def test_the_old_query_answer_could_never_refuse(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """M1, what `title:".github"` answered: nothing, so a mint went on."""
    minting = mint(
        "0BSD",
        zenodo=first_mint_zenodo(measured("present-github")),
        version="1.68.5",
        repo=CANON_REPO,
    )
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "**First mint**" in run.summary
    assert len(run.made("POST", DEP)) == FIRST_MINT_POSTS


def test_an_unsubmitted_title_alone_does_not_refuse(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """C5, the captured unsubmitted draft of the title: the mint goes on."""
    minting = mint(
        "0BSD",
        zenodo=first_mint_zenodo(captured("drafts-during")),
        version="1.68.5",
        repo=CANON_REPO,
    )
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert "**First mint**" in run.summary


def test_a_title_on_a_later_page_refuses(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Page 1 full of unsubmitted drafts, the minted title on page 2."""
    (draft,) = captured_list("drafts-during")
    z = first_mint_zenodo([])
    z.title_pages = [[draft] * mint_doi.LISTING_PAGE, captured("drafts-before")]
    minting = mint("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: a submitted deposition already bears this title"
        in run.stdout
    )
    assert len(run.made("GET", f"{DEP}?q=title")) == TITLE_PAGES
    assert run.writes() == NOTHING


def test_a_listing_page_that_cannot_be_read_refuses(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Page 2 answers 503 on every try: refused, nothing created."""
    (draft,) = captured_list("drafts-during")
    z = first_mint_zenodo([])
    z.title_pages = [[draft] * mint_doi.LISTING_PAGE, 503]
    minting = mint("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: the deposition listing"
        " (q=title:%2522monumental-archive%252F.github%2522) could not be"
        f" read to its end ({mint_doi.LISTING_PAGE} entries read) — a first mint"
        " is never checked against part of it"
    ) in run.stdout
    assert run.writes() == NOTHING


def test_a_listing_still_full_at_the_page_bound_refuses(
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    mint: Callable[..., Minting],
) -> None:
    """Full pages to the bound: the bound refuses, never passes."""
    (draft,) = captured_list("drafts-during")
    z = first_mint_zenodo([])
    z.title_pages = [[draft] * mint_doi.LISTING_PAGE] * 3
    monkeypatch.setattr(mint_doi, "LISTING_MAX_PAGES", 3)
    minting = mint("0BSD", zenodo=z, version="1.68.5", repo=CANON_REPO)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        f"could not be read to its end ({3 * mint_doi.LISTING_PAGE} entries read)"
        in run.stdout
    )
    assert len(run.made("GET", f"{DEP}?q=title")) == TITLE_PAGES_CAPPED
    assert run.writes() == NOTHING


def test_a_vocabulary_answering_500_refuses_as_unchecked(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A 500 is retried as transient, then refused; no deposit."""
    z = stub_zenodo("both", None)
    z.vocab_status = 500
    minting = mint("0BSD", zenodo=z)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: Zenodo's licence vocabulary answered HTTP 500 for"
        " '0bsd'"
        " — not minting on an unchecked licence"
    ) in run.stdout
    assert len(run.made("GET", "/api/vocabularies/licenses/0bsd")) == LICENCE_TRIES
    assert run.writes() == NOTHING


def test_no_authors_refuses_before_any_request(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A citation with no authors is red by name; Zenodo is never asked."""
    cff = "\n".join(
        line
        for line in citation("0BSD").splitlines()
        if "family-names" not in line and "given-names" not in line
    )
    minting = mint("0BSD", cff=cff)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: CITATION.cff carries no authors — the record"
        " credits people,"
        " never an account name"
    ) in run.stdout
    assert run.requests == NOTHING


def test_no_citation_refuses_by_name(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Gh failing to fetch CITATION.cff is red, naming the tag."""
    minting = mint("0BSD", cff="")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: no CITATION.cff at v1.3.5 — a DOI-minting repo"
        " derives one"
        " from REUSE.toml (fix:citation; lint:citation gates this upstream)"
    ) in run.stdout
    assert run.requests == NOTHING


def test_a_failed_read_refuses_though_its_body_decodes(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """Gh's exit status decides, never the decoder (#103).

    A 404 reached the fallback only because its error body happened
    not to be valid base64. Here gh fails after printing a body that
    decodes to a whole CITATION.cff: the read failed, so it refuses.
    """
    minting = mint("0BSD", cff_status=1)
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: no CITATION.cff at v1.3.5 — a DOI-minting repo"
        " derives one"
        " from REUSE.toml (fix:citation; lint:citation gates this upstream)"
    ) in run.stdout
    assert run.requests == NOTHING


def test_a_concept_doi_without_a_record_id_refuses(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A recorded concept DOI must end in a Zenodo record id."""
    minting = mint("0BSD", concept="10.5281/zenodo.abc")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: CITATION.cff concept DOI '10.5281/zenodo.abc' does"
        " not end"
        " in a Zenodo record id"
    ) in run.stdout
    assert run.requests[-1][1].rsplit("/", 1)[1] == "0bsd"


def test_an_unresolvable_concept_refuses(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """A concept with no published version is red by name, nothing created."""
    minting = mint("0BSD", concept="10.5281/zenodo.555")
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: the concept record 555 did not resolve to a latest"
        " published version"
    ) in run.stdout
    assert run.writes() == NOTHING


# ---------------------------------------------------------------------
# The CITATION.cff reader and the licence expression, as tables.
# ---------------------------------------------------------------------

AUTHORS = """authors:
  - family-names: Allen
    given-names: Carl
  - family-names: "van Rossum"
    given-names: "Guido"
"""


# `read_citation`: the three fields, one row per shape.
def test_the_canons_own_citation() -> None:
    """The canon's own CITATION.cff reads as itself."""
    got = mint_doi.read_citation((CANON / "CITATION.cff").read_text())
    assert got.creators == strings("Allen, Carl")
    assert got.licence == "0BSD"
    assert got.concept_doi == "10.5281/zenodo.21913604"


def test_rows() -> None:
    """Each row: text in, (creators, licence, concept) out."""
    rows = [
        (
            "quoted names unquote",
            AUTHORS,
            (["Allen, Carl", "van Rossum, Guido"], "", ""),
        ),
        ("no authors", "license: MIT\n", ([], "MIT", "")),
        (
            "family without given is no author",
            "  - family-names: Allen\n  orcid: x\n",
            ([], "", ""),
        ),
        (
            "first license line wins, verbatim",
            "license: MIT OR 0BSD \nlicense: Apache-2.0\n",
            ([], "MIT OR 0BSD ", ""),
        ),
        (
            "an indented license is not the top-level key",
            "  license: MIT\n",
            ([], "", ""),
        ),
        (
            "value before identifiers is ignored",
            "  value: 10.1/a\nidentifiers:\n  - type: doi\n    value: 10.1/b\n",
            ([], "", "10.1/b"),
        ),
        (
            "first value after identifiers wins",
            "identifiers:\n  - value: 10.1/c\n  - value: 10.1/d\n",
            ([], "", "10.1/c"),
        ),
        ("no identifiers is a first mint", "title: x\n", ([], "", "")),
        # #1556's shrunk example: zip(strict=True) raised on it.
        ("an empty file reads as nothing", "", ([], "", "")),
    ]
    for _name, text, want in rows:
        got = mint_doi.read_citation(text)
        assert (got.creators, got.licence, got.concept_doi) == want


# `licence_ids`: one row per guard branch, the message asserted.
def test_accepted() -> None:
    """Plain ids joined by OR or AND, lowercased in order."""
    rows = [
        ("0BSD", ["0bsd"]),
        ("MIT OR Apache-2.0", ["mit", "apache-2.0"]),
        ("MIT AND Apache-2.0 OR GPL-2.0+", ["mit", "apache-2.0", "gpl-2.0+"]),
    ]
    for licence, want in rows:
        assert mint_doi.licence_ids(licence) == want


def test_refused() -> None:
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
        with pytest.raises(mint_doi.RefusedError) as caught:
            mint_doi.licence_ids(licence)
        assert str(caught.value) == (
            f"CITATION.cff license '{licence}' {why} — the record carries"
            " plain SPDX ids joined by OR or AND"
        )


# Each answer Zenodo gives is read once, or refused by name (#1682).
def refused(call: Callable[[], object], message: str) -> None:
    """Assert calling `call` refuses with exactly `message`."""
    with pytest.raises(mint_doi.RefusedError) as caught:
        call()
    assert message == str(caught.value)


def test_an_answer_that_is_not_a_response_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the opener answered is not an HTTP response: refused by name."""
    url = "https://zenodo.invalid/api/records/1"
    z = mint_doi.Zenodo("https://zenodo.invalid", "t", "a")
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda *_args, **_kwargs: None
    )
    with pytest.raises(mint_doi.RefusedError) as caught:
        z.public(url)
    assert f"{url} answered NoneType, not an HTTP response" == str(caught.value)


def test_an_answer_that_is_not_json_is_refused() -> None:
    """Named with the decoder's reason."""
    with pytest.raises(mint_doi.RefusedError) as caught:
        mint_doi.loaded(b"<html>", "the new deposition")
    assert str(caught.value).startswith("the new deposition is not JSON: ")


def test_each_wrong_deposition_shape_is_refused() -> None:
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
        refused(functools.partial(mint_doi.deposition, value, what), message)


def test_a_deposition_reads_its_id_links_and_files() -> None:
    """The shape the step reads, an id that is not a string compact."""
    got = mint_doi.deposition(
        {
            "id": 7,
            "links": {"self": "s", "bucket": "b", "publish": "p"},
            "files": [{"id": "f1"}],
        },
        "it",
    )
    assert got == mint_doi.Deposition("7", "s", "b", "p", ["f1"])


def test_rights_that_are_not_a_list_of_ids_are_unreadable() -> None:
    """Each refusal of the rights reader names the record."""
    where = "the published record 1"
    for value, message in (
        ({"metadata": {}}, f"{where}'s rights is not a list"),
        ({"metadata": {"rights": [1]}}, f"a right of {where} is not an object"),
        ({"metadata": {"rights": [{}]}}, f"a right of {where} has no id"),
        ({"metadata": []}, f"{where} is not an object"),
    ):
        with pytest.raises(decoded.UndecodableError) as caught:
            mint_doi.rights_ids(value, where)
        assert message == str(caught.value)


def test_a_newversion_answer_with_no_draft_link_is_refused(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """No deposit is grown from an answer that names no draft."""
    minting = canon_mint(mint, canon_zenodo(newversion_body={"links": {}}))
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: the newversion answer's links.latest_draft is not a string"
    ) in run.stdout


def test_a_draft_listing_entry_that_is_not_an_object_is_refused(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """The stale-draft sweep reads depositions, nothing else."""
    minting = canon_mint(mint, canon_zenodo(drafts=[1]))
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: an entry of the draft listing is not an object"
        in run.stdout
    )


def test_a_title_listing_entry_that_is_not_an_object_is_refused(
    capfd: pytest.CaptureFixture[str], mint: Callable[..., Minting]
) -> None:
    """The first-mint guard reads depositions, nothing else."""
    minting = mint(
        "0BSD", zenodo=first_mint_zenodo([1]), version="1.68.5", repo=CANON_REPO
    )
    status = mint_doi.main(["--pipeline", PIPELINE], minting.environ)
    run = minting.ran(status, capfd.readouterr())
    assert run.returncode == 1, run.stdout
    assert (
        "::error::mint-doi: an entry of the deposition listing (q=title:" in run.stdout
    )
    assert ") is not an object" in run.stdout
    assert run.writes() == NOTHING

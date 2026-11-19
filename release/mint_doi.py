#!/usr/bin/env python3
"""Deposit the released source on Zenodo and publish its version DOI.

`mint-doi.yml` calls this as its one step (#1365); before that it was a
211-line `run:` block of bash and `jq`. The behaviour is the block's,
ported: the module docstring of `mint-doi.yml` states the design, and
each function below names the issue that fixed its part.

INPUTS. From the environment, as the step sets them: `ZENODO_TOKEN`
(sent only in the Authorization header of authenticated calls, never in
a URL, never printed), `BASE_URL`, `UNTRUSTED_VERSION`, `TAG`,
`GITHUB_REPOSITORY`, `GITHUB_STEP_SUMMARY`, `ORG_USER_AGENT`, and the
`GH_TOKEN` the `gh` CLI reads. The pipeline sentence of the description
is an argument, so no organisation name lives here.

IDEMPOTENCY (#484). Reads and PUTs converge on the same state however
often they run, so they retry. POSTs create and a DELETE removes: one
shot, never retried; their safety is the state oracle (the concept's
latest PUBLISHED version), consulted before acting and again after a
failed publish. The truth is the archive's state, never an HTTP status.

stdlib only (`urllib`, `json`): no YAML library enters the canon to read
three fields out of CITATION.cff (`read_citation`).
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override
from urllib.parse import quote

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "mise"))

import decoded
import workflow_command

if TYPE_CHECKING:
    from collections.abc import Mapping

RDM = "application/vnd.inveniordm.v1+json"
# curl's transient set, which `--retry` without `--retry-all-errors`
# retries: a read whose 404 is an answer (the licence vocabulary).
TRANSIENT = frozenset({408, 429, 500, 502, 503, 504, 522, 524})
# curl `--retry 5 --retry-max-time 120`: five retries, the first after a
# second, doubling, none started past two minutes.
RETRIES = 5
RETRY_MAX_TIME = 120
TIMEOUT = 300
# Three oracle reads after a lost publish response, 20 s apart (#484).
RECOVERY_READS = 3
RECOVERY_SPACING = 20
# The first-mint title guard's listing (#1389): page size, and a bound
# on pages that REFUSES when reached - a page cap never decides a pass
# (#316 addendum A). Measured in run 44129109573: `size=100` answers,
# `size=1000` is a 400, and no Link or total header comes back, so a
# short page is the only end there is.
LISTING_PAGE = 100
LISTING_MAX_PAGES = 1000
SPDX_ID = re.compile(r"[A-Za-z0-9.+-]+")
FAMILY = re.compile(r'^\s*- family-names:\s*"?([^"]*?)"?\s*$')
GIVEN = re.compile(r'^\s*given-names:\s*"?([^"]*?)"?\s*$')
ALREADY = (
    "**Already published** — a previous attempt minted this version's DOI;"
    " this run converged on it (#484)."
)
RECOVERED = (
    "**Published despite a lost response** — the publish landed; this run"
    " recovered the DOI from the record (#484)."
)
FIRST = (
    "**First mint** — record the concept DOI in CITATION.cff\n"
    "(`identifiers:`, via fix:citation's preserved field) before\n"
    "the next release; later mints resolve the concept from it."
)
FIRST_CONCEPT = "first release — concept DOI equals this version"


class RefusedError(Exception):
    """A refusal: printed as one error workflow command, exit 1."""


class HttpError(Exception):
    """A request that did not answer 2xx, or did not answer at all."""

    def __init__(self, method: str, url: str, detail: str) -> None:
        """Name the request and what went wrong."""
        super().__init__(f"{method} {url}: {detail}")


def loaded(raw: bytes, what: str) -> object:
    """Decode an answer Zenodo gave, or refuse it by name.

    Returns:
        The decoded value.

    Raises:
        RefusedError: it is not JSON, naming `what`.

    """
    try:
        return decoded.load_json(raw)
    except decoded.UndecodableError as e:
        msg = f"{what} is not JSON: {e}"
        raise RefusedError(msg) from e


def an_object(value: object, where: str) -> dict[str, object]:
    """Narrow an answer's value to an object, or refuse it by name.

    Returns:
        The object.

    Raises:
        RefusedError: it is not one, naming `where`.

    """
    try:
        return decoded.as_object(value, where)
    except decoded.UndecodableError as e:
        raise RefusedError(str(e)) from None


def a_string(value: object, where: str) -> str:
    """Narrow an answer's value to a string, or refuse it by name.

    Returns:
        The string.

    Raises:
        RefusedError: it is not one, naming `where`.

    """
    try:
        return decoded.as_str(value, where)
    except decoded.UndecodableError as e:
        raise RefusedError(str(e)) from None


def a_list(value: object, where: str) -> list[object]:
    """Narrow an answer's value to a list, or refuse it by name.

    Returns:
        The list.

    Raises:
        RefusedError: it is not one, naming `where`.

    """
    try:
        return decoded.as_array(value, where)
    except decoded.UndecodableError as e:
        raise RefusedError(str(e)) from None


def rendered(value: object) -> str:
    """Render an id as the step always has: a string as is, else compact.

    Returns:
        The id's text.

    """
    return value if isinstance(value, str) else compact(value)


@dataclass(frozen=True)
class Deposition:
    """A deposition, as the step reads one: its id, links and files."""

    id: str
    self_url: str
    bucket: str
    publish: str
    # Each file's id, as its delete URL names it.
    files: list[str]


def deposition(value: object, what: str) -> Deposition:
    """Read a deposition Zenodo answered.

    Returns:
        Its id, links and file ids.

    Raises:
        RefusedError: not that shape, naming `what` and the field.

    """
    dep = an_object(value, what)
    links = an_object(dep.get("links"), f"{what}'s links")
    files: list[str] = []
    for item in a_list(dep.get("files") or [], f"{what}'s files"):
        entry = an_object(item, f"a file of {what}")
        if "id" not in entry:
            msg = f"a file of {what} has no id"
            raise RefusedError(msg)
        files.append(f"{entry['id']}")
    if "id" not in dep:
        msg = f"{what} has no id"
        raise RefusedError(msg)
    return Deposition(
        id=rendered(dep["id"]),
        self_url=a_string(links.get("self"), f"{what}'s links.self"),
        bucket=a_string(links.get("bucket"), f"{what}'s links.bucket"),
        publish=a_string(links.get("publish"), f"{what}'s links.publish"),
        files=files,
    )


def rights_ids(value: object, where: str) -> str:
    """Read a record's licence ids, as `jq -c '[.metadata.rights[].id]'`.

    Returns:
        The ids, compact.

    Raises:
        decoded.UndecodableError: the record holds no list of rights.

    """
    meta = decoded.as_object(decoded.as_object(value, where).get("metadata"), where)
    ids: list[object] = []
    for right in decoded.as_array(meta.get("rights"), f"{where}'s rights"):
        entry = decoded.as_object(right, f"a right of {where}")
        if "id" not in entry:
            msg = f"a right of {where} has no id"
            raise decoded.UndecodableError(msg)
        ids.append(entry["id"])
    return compact(ids)


@dataclass(frozen=True)
class Listed:
    """A deposition listing's entry, as the title guard reads it."""

    # The title; None where it is not a string, which no repo's name equals.
    title: str | None
    submitted: bool


def pause(seconds: float) -> None:
    """Wait between retries; one seam, so the tests run without waiting."""
    time.sleep(seconds)


def compact(value: object) -> str:
    """Render JSON the way `jq -c` does.

    Returns:
        The value with no insignificant whitespace.

    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Citation:
    """The three things CITATION.cff is read for."""

    creators: list[str]
    licence: str
    concept_doi: str


def read_citation(text: str) -> Citation:
    """Read authors, the licence line and the concept DOI.

    The file is fix:citation's render, so its format is the generator's:
    these reads parse what the generator writes, and nothing more.

    - An author is a `- family-names:` line followed by a
      `given-names:` line; it is credited as "family, given".
    - The licence is the rest of the first `license: ` line, verbatim.
    - The concept DOI is the last word of the first line containing
      `value:` at or after the `identifiers:` line; empty when absent.

    Returns:
        The three fields, unvalidated.

    """
    lines = text.splitlines()
    creators = []
    # Each line beside the next, the last beside nothing; an empty file
    # has no pairs (zip's strict length check refused it, #1556).
    for here, after in itertools.pairwise([*lines, ""]):
        family, given = FAMILY.match(here), GIVEN.match(after)
        if family and given:
            creators.append(f"{family.group(1)}, {given.group(1)}")
    licence = next(
        (
            line.removeprefix("license: ")
            for line in lines
            if line.startswith("license: ")
        ),
        "",
    )
    concept = ""
    seen = False
    for line in lines:
        seen = seen or line.startswith("identifiers:")
        if seen and "value:" in line:
            concept = line.split()[-1]
            break
    return Citation(creators, licence, concept)


def licence_ids(licence: str) -> list[str]:
    """Split a CITATION.cff licence expression into lowercase SPDX ids.

    The record states every licence the tree states (#1246). The
    expression is plain SPDX ids joined by OR or AND; anything richer
    (WITH, parentheses) has no faithful place in a list of licence ids
    and refuses by name. A single id is the degenerate case.

    Returns:
        The ids, lowercased, in order.

    Raises:
        RefusedError: the expression is not plain ids joined by OR/AND.

    """
    ids: list[str] = []
    why = _licence_fault(licence, ids)
    if why:
        msg = (
            f"CITATION.cff license '{licence}' {why} — the record carries"
            " plain SPDX ids joined by OR or AND"
        )
        raise RefusedError(msg)
    return ids


def _licence_fault(licence: str, ids: list[str]) -> str:  # ruff: ignore[too-many-return-statements] - one per guard
    """Walk the expression, collecting ids, in the order the guards run.

    Returns:
        Why the expression refuses, or "" when `ids` holds every id.

    """
    if not licence.strip():
        return "is empty"
    if "(" in licence or ")" in licence:
        return "uses parentheses"
    words = licence.split()
    for i, word in enumerate(words):
        if i % 2 == 1:
            if word not in {"OR", "AND"}:
                return f"joins ids with '{word}'"
        elif word in {"WITH", "OR", "AND"}:
            return f"has '{word}' where an id belongs"
        elif not SPDX_ID.fullmatch(word):
            return f"has '{word}', which is not an SPDX id"
        else:
            ids.append(word.lower())
    if not ids or len(words) % 2 == 0:
        return "is not a complete expression"
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Authenticated calls never follow a redirect: the token stays put."""

    @override
    @staticmethod
    def redirect_request(*_: object, **__: object) -> None:
        """Refuse: urllib then raises the 3xx as an HTTPError."""


@dataclass
class Zenodo:
    """The Zenodo client, split by idempotency (#484)."""

    base: str
    token: str
    agent: str
    _authed: urllib.request.OpenerDirector = field(init=False, repr=False)
    _public: urllib.request.OpenerDirector = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Build one opener that never redirects, one that follows."""
        self._authed = urllib.request.build_opener(_NoRedirect())
        self._public = urllib.request.build_opener()

    def _once(
        self,
        method: str,
        url: str,
        *,
        auth: bool,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Make one request.

        Returns:
            The status and body, whatever the status.

        Raises:
            HttpError: no answer at all.

        """
        h = {"User-Agent": self.agent, **(headers or {})}
        if auth:
            h["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=body, headers=h, method=method)  # ruff: ignore[suspicious-url-open-usage] - BASE_URL is the step's input
        opener = self._authed if auth else self._public
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            reason = e.reason if isinstance(e, urllib.error.URLError) else e
            raise HttpError(method, url, str(reason)) from e

    def _retrying(  # ruff: ignore[too-many-arguments] - curl's two retry modes
        self,
        method: str,
        url: str,
        *,
        auth: bool,
        all_errors: bool,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Make a request, retrying as curl `--retry 5` does.

        Returns:
            The last status and body.

        Raises:
            HttpError: no answer at all on the last try.

        """
        start = time.monotonic()
        delay = 1.0
        attempt = 0
        while True:
            last = attempt == RETRIES or time.monotonic() - start >= RETRY_MAX_TIME
            attempt += 1
            try:
                status, data = self._once(
                    method, url, auth=auth, body=body, headers=headers
                )
            except HttpError as e:
                if last:
                    raise
                print(f"retrying after {e}", file=sys.stderr)
            else:
                retry = status >= 400 if all_errors else status in TRANSIENT  # ruff: ignore[magic-value-comparison] - an HTTP status class
                if last or not retry:
                    return status, data
                print(f"retrying {method} {url}: HTTP {status}", file=sys.stderr)
            pause(delay)
            delay *= 2

    @staticmethod
    def _ok(method: str, url: str, status: int, data: bytes) -> bytes:
        if not 200 <= status < 300:  # ruff: ignore[magic-value-comparison] - the 2xx class
            raise HttpError(method, url, f"HTTP {status}")
        return data

    def call(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Make an authenticated non-idempotent call (POST/DELETE), once.

        Returns:
            The body of a 2xx answer.

        """
        status, data = self._once(method, url, auth=True, body=body, headers=headers)
        return self._ok(method, url, status, data)

    def idem(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Make an authenticated idempotent call (GET/PUT), retrying.

        Returns:
            The body of a 2xx answer.

        """
        status, data = self._retrying(
            method, url, auth=True, all_errors=True, body=body, headers=headers
        )
        return self._ok(method, url, status, data)

    def public(self, url: str, *, accept: str | None = None) -> bytes:
        """PUBLIC read that may REDIRECT, with no token (#316).

        The concept record redirects to its latest version, so this
        follows — and deliberately sends no credentials through a
        redirect for a read that needs none. Measured on canon v1.22.0:
        the token helper returned the HTML redirect body and the DOI was
        lost after the commitment point.

        Returns:
            The body of a 2xx answer.

        """
        headers = {"Accept": accept} if accept else None
        status, data = self._retrying(
            "GET", url, auth=False, all_errors=True, headers=headers
        )
        return self._ok("GET", url, status, data)

    def status(self, url: str) -> int:
        """Public read whose 404 is an answer: transient retries only.

        Returns:
            The final HTTP status.

        """
        status, _ = self._retrying("GET", url, auth=False, all_errors=False)
        return status


def gh(env: Mapping[str, str], *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run the `gh` CLI with the step's environment.

    Returns:
        The finished process, stdout and stderr captured.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        ["gh", *args],  # ruff: ignore[start-process-with-partial-path] - gh is the runner's, resolved on PATH
        env=dict(env),
        capture_output=True,
        check=False,
    )


@dataclass
class Mint:
    """One run of the step."""

    env: Mapping[str, str]
    zenodo: Zenodo
    version: str
    tag: str
    repo: str
    summary: str
    ids: list[str] = field(default_factory=list)

    @property
    def rights(self) -> list[dict[str, str]]:
        """The licence ids as InvenioRDM `rights`."""
        return [{"id": i} for i in self.ids]

    def oracle(self, concept_recid: str) -> tuple[str, str, str, str] | None:
        """Read the state oracle (#484).

        The concept's latest PUBLISHED version, read directly — no
        search, so no page cap to resolve it wrongly (#316 addendum A).

        Returns:
            (doi, concept DOI, record id, version), or None when the
            read fails or does not answer a record.

        """
        url = f"{self.zenodo.base}/api/records/{concept_recid}/versions/latest"
        try:
            rec = decoded.load_json(self.zenodo.public(url))
        except (HttpError, decoded.UndecodableError) as e:
            print(e, file=sys.stderr)
            return None
        if not decoded.is_object(rec):
            return None
        meta = rec.get("metadata") or {}
        version = meta.get("version") if decoded.is_object(meta) else None
        doi, concept = rec.get("doi"), rec.get("conceptdoi")
        return (
            (doi if isinstance(doi, str) else "") or "",
            (concept if isinstance(concept, str) else "") or "unrecorded",
            rendered(rec.get("id")),
            (version if isinstance(version, str) else "") or "unversioned",
        )

    def done(self, doi: str, concept: str, extra: str = "") -> int:
        """Write the summary and the notice.

        Returns:
            0, the green exit.

        """
        lines = ["### DOI minted", "", f"- version: `{doi}`", f"- concept: `{concept}`"]
        if extra:
            lines += ["", extra]
        with open(self.summary, "a", encoding="utf-8") as f:  # ruff: ignore[builtin-open] - a path from the runner
            f.write("\n".join(lines) + "\n")
        workflow_command.notice(f"DOI {doi} (concept {concept})")
        return 0

    def verify_published(self, recid: str, doi: str) -> None:
        """Check the PUBLISHED record states every licence (#1246).

        The draft's read-back is checked before the publish; that the
        legacy publish keeps the list could not be measured without
        minting, and a record over one of two licences is the false
        claim #316 removed. It cannot be unpublished — the red is the
        signal. Every green exit after a publish passes here, the #484
        convergence paths included; a read that cannot be completed is
        unverified, never a pass. One id: no read.

        Raises:
            RefusedError: unreadable, or states other licences.

        """
        if len(self.ids) < 2:  # ruff: ignore[magic-value-comparison] - one id needs no list check
            return
        want = compact(self.ids)
        try:
            got = rights_ids(
                decoded.load_json(
                    self.zenodo.public(
                        f"{self.zenodo.base}/api/records/{recid}", accept=RDM
                    )
                ),
                f"the published record {recid}",
            )
        except (HttpError, decoded.UndecodableError) as e:
            print(e, file=sys.stderr)
            msg = (
                f"the published record {recid} could not be read back — the"
                f" licences of DOI {doi} are unverified"
            )
            raise RefusedError(msg) from e
        if got != want:
            msg = (
                f"the published record's licences read back as {got}, not {want}"
                f" — the DOI {doi} states less than CITATION.cff"
            )
            raise RefusedError(msg)

    def fetch(self) -> tuple[bytes, Citation]:
        """Fetch the tag's source tarball and its CITATION.cff.

        The tarball is GitHub's own of the tag, so the DOI names the
        released bytes. CITATION.cff is read at the tag — the attested
        revision — and is REQUIRED: a permanent record never publishes a
        fallback (#316 findings 1+6). gh's exit status decides, not a
        decoder: on a 404 gh prints the JSON error body to stdout.

        Returns:
            The tarball's bytes and the parsed citation.

        Raises:
            RefusedError: either fetch fails.

        """
        tar = gh(self.env, "api", f"repos/{self.repo}/tarball/{self.tag}")
        if tar.returncode != 0:
            sys.stderr.write(tar.stderr.decode(errors="replace"))
            msg = f"the source tarball of {self.tag} could not be fetched"
            raise RefusedError(msg)
        cff = gh(
            self.env,
            "api",
            f"repos/{self.repo}/contents/CITATION.cff?ref={self.tag}",
            "--jq",
            ".content",
        )
        text = None
        if cff.returncode == 0:
            # binascii.Error and UnicodeDecodeError both subclass ValueError.
            try:
                text = base64.b64decode(cff.stdout).decode()
            except ValueError:
                text = None
        if text is None:
            msg = (
                f"no CITATION.cff at {self.tag} — a DOI-minting repo derives one"
                " from REUSE.toml (fix:citation; lint:citation gates this upstream)"
            )
            raise RefusedError(msg)
        return tar.stdout, read_citation(text)

    def check_vocabulary(self, licence: str) -> None:
        """Ask Zenodo's licence vocabulary for every id, before any deposit.

        A public read (no token); transient answers retry, and a 404 is
        the vocabulary's no.

        Raises:
            RefusedError: an id is absent, or the answer is not 200.

        """
        for i in self.ids:
            code = self.zenodo.status(
                f"{self.zenodo.base}/api/vocabularies/licenses/{i}"
            )
            if code == 404:  # ruff: ignore[magic-value-comparison] - the vocabulary's no
                msg = (
                    f"licence id '{i}' (from CITATION.cff '{licence}') is not in"
                    " Zenodo's licence vocabulary"
                )
                raise RefusedError(msg)
            if code != 200:  # ruff: ignore[magic-value-comparison] - the vocabulary's yes
                msg = (
                    f"Zenodo's licence vocabulary answered HTTP {code} for '{i}'"
                    " — not minting on an unchecked licence"
                )
                raise RefusedError(msg)

    def read_listing(self, query: str) -> list[Listed]:
        """Read a deposition listing to its end, page by page (#1389).

        The end is a page shorter than the page size. A page that cannot
        be read, is not a list, or a listing still full at the page
        bound refuses: a guard that saw part of the listing has not
        checked it.

        Returns:
            Every entry, in the order the pages answered.

        Raises:
            RefusedError: the listing could not be read to its end, or
                holds an entry that is not a deposition.

        """
        z = self.zenodo
        entries: list[Listed] = []
        for page in range(1, LISTING_MAX_PAGES + 1):
            url = (
                f"{z.base}/api/deposit/depositions?{query}"
                f"&size={LISTING_PAGE}&page={page}"
            )
            try:
                got = decoded.load_json(z.idem("GET", url))
            except (HttpError, decoded.UndecodableError) as e:
                print(e, file=sys.stderr)
                got = None
            if not decoded.is_array(got):
                break
            for item in got:
                entry = an_object(item, f"an entry of the deposition listing ({query})")
                said = entry.get("title")
                title = said if isinstance(said, str) else None
                entries.append(
                    Listed(title=title, submitted=entry.get("submitted") is True)
                )
            if len(got) < LISTING_PAGE:
                return entries
        msg = (
            f"the deposition listing ({query}) could not be read to its end"
            f" ({len(entries)} entries read) — a first mint is never checked"
            " against part of it"
        )
        raise RefusedError(msg)

    def first_deposit(self) -> Deposition:
        """Create a deposition for a first mint, behind the title guard.

        A submitted deposition already bearing this title is a concept
        the tree failed to record; minting again would split the
        citation record permanently.

        Returns:
            The new deposition.

        Raises:
            RefusedError: a submitted deposition bears the title.

        """
        # The FULL title, quoted, slash encoded (#1389). Measured in run
        # 44129109573 on production: `title:"<repo name>"` answered []
        # for both `.github` and `edtf`, so the guard had never matched
        # anything; `title:"<owner>/<repo>"` answered exactly the record
        # for both, the slash encoded or literal.
        listing = self.read_listing(f"q=title:%22{quote(self.repo, safe='')}%22")
        if any(d.title == self.repo and d.submitted for d in listing):
            msg = (
                "a submitted deposition already bears this title but CITATION.cff"
                " records no concept DOI — record it under identifiers: before"
                " minting again"
            )
            raise RefusedError(msg)
        created = self.zenodo.call(
            "POST",
            f"{self.zenodo.base}/api/deposit/depositions",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        return deposition(loaded(created, "the new deposition"), "the new deposition")

    def new_version(self, concept_recid: str, latest: str) -> Deposition:
        """Open a new version of the concept, emptied of inherited files.

        An unpublished draft under the concept is the wreckage of an
        interrupted run: it blocks newversion and its contents are
        unknown, so it is deleted and the deposit built fresh. Published
        records are never touched: a draft is deleted only when it is
        unsubmitted AND under this concept, whatever the search matched.

        Returns:
            The new version's draft deposition.

        """
        z = self.zenodo
        what = "the draft listing"
        raw = z.idem(
            "GET",
            f"{z.base}/api/deposit/depositions?q=conceptrecid:{concept_recid}"
            "&status=draft&size=50",
        )
        for item in a_list(loaded(raw, what), what):
            d = an_object(item, f"an entry of {what}")
            rid = d.get("conceptrecid") or ""
            if d.get("submitted") is False and rendered(rid) == concept_recid:
                links = an_object(d.get("links"), f"a stale draft's links in {what}")
                url = a_string(
                    links.get("self"), f"a stale draft's links.self in {what}"
                )
                workflow_command.notice(
                    f"deleting stale unpublished draft {url} left by an interrupted run"
                )
                z.call("DELETE", url)
        what = "the newversion answer"
        newv = an_object(
            loaded(
                z.call(
                    "POST",
                    f"{z.base}/api/deposit/depositions/{latest}/actions/newversion",
                ),
                what,
            ),
            what,
        )
        links = an_object(newv.get("links"), f"{what}'s links")
        draft = a_string(links.get("latest_draft"), f"{what}'s links.latest_draft")
        dep = deposition(loaded(z.idem("GET", draft), "the new draft"), "the new draft")
        for fid in dep.files:
            z.call("DELETE", f"{dep.self_url}/files/{fid}")
        return dep

    def set_rights(self, recid: str) -> None:
        """Put every licence on the draft through the InvenioRDM API.

        The legacy metadata PUT sets ONE licence and collapses any list
        already on the draft, so with more than one id the full list goes
        on through the InvenioRDM draft of the same record — the
        deposition id IS the record id. Measured on production Zenodo,
        drafts only (#1246): without the InvenioRDM media type
        /api/records answers in the legacy shape and drops `rights`;
        with it, a read-modify-write of {metadata, files} sets every id
        and changes nothing else. A PUT, so it retries.

        Raises:
            RefusedError: the read-back names other ids.

        """
        z = self.zenodo
        url = f"{z.base}/api/records/{recid}/draft"
        what = f"the InvenioRDM draft {recid}"
        draft = an_object(
            loaded(z.idem("GET", url, headers={"Accept": RDM}), what), what
        )
        meta = an_object(draft.get("metadata"), f"{what}'s metadata")
        if "files" not in draft:
            msg = f"{what} has no files"
            raise RefusedError(msg)
        meta["rights"] = self.rights
        sent: dict[str, object] = {"metadata": meta, "files": draft["files"]}
        body = compact(sent).encode()
        back = loaded(
            z.idem(
                "PUT",
                url,
                body=body,
                headers={"Accept": RDM, "Content-Type": "application/json"},
            ),
            f"{what}'s read-back",
        )
        try:
            got = rights_ids(back, f"{what}'s read-back")
        except decoded.UndecodableError as e:
            raise RefusedError(str(e)) from None
        if got != compact(self.ids):
            msg = (
                f"the draft's licences read back as {got}, not the"
                f" {compact(self.rights)} CITATION.cff states — not publishing"
            )
            raise RefusedError(msg)

    def run(self, pipeline: str) -> int:  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements] - the step, in order
        """Deposit, then publish — the DOI names the released bytes.

        Returns:
            The exit status.

        Raises:
            RefusedError: any refusal.

        """
        tarball, cff = self.fetch()
        if not cff.creators:
            msg = (
                "CITATION.cff carries no authors — the record credits people,"
                " never an account name"
            )
            raise RefusedError(msg)
        self.ids = licence_ids(cff.licence)
        self.check_vocabulary(cff.licence)
        desc = f"Release {self.tag} of {self.repo}. {pipeline}"
        # A list of licences cannot say OR from AND, so the expression
        # itself is quoted, exactly as CITATION.cff has it.
        if len(self.ids) > 1:
            desc += (
                f" Licensed under the SPDX expression {cff.licence},"
                " as CITATION.cff states it."
            )
        # The legacy deposit's `license` is ONE string (measured, #1246:
        # a list answers 400). It carries the first id.
        metadata = compact({
            "metadata": {
                "title": self.repo,
                "upload_type": "software",
                "description": desc,
                "version": self.version,
                "creators": [{"name": n} for n in cff.creators],
                "access_right": "open",
                "license": self.ids[0],
            }
        })
        z = self.zenodo
        concept_recid = ""
        if not cff.concept_doi:
            dep = self.first_deposit()
        else:
            concept_recid = cff.concept_doi.rsplit(".", 1)[-1]
            if not concept_recid.isascii() or not concept_recid.isdigit():
                msg = (
                    f"CITATION.cff concept DOI '{cff.concept_doi}' does not end"
                    " in a Zenodo record id"
                )
                raise RefusedError(msg)
            # One read answers two questions: is the goal state already
            # true (a rerun after a lost publish response), and if not,
            # which record does newversion grow from?
            latest = self.oracle(concept_recid)
            if latest is None:
                msg = (
                    f"the concept record {concept_recid} did not resolve to a"
                    " latest published version"
                )
                raise RefusedError(msg)
            if latest[3] == self.version:
                self.verify_published(latest[2], latest[0])
                return self.done(latest[0], latest[1], ALREADY)
            dep = self.new_version(concept_recid, latest[2])
        name = self.repo.rsplit("/", 1)[-1]
        z.idem(
            "PUT",
            f"{dep.bucket}/{name}-{self.version}.tar.gz",
            body=tarball,
            headers={"Content-Type": "application/octet-stream"},
        )
        z.idem(
            "PUT",
            dep.self_url,
            body=metadata.encode(),
            headers={"Content-Type": "application/json"},
        )
        recid = dep.id
        if len(self.ids) > 1:
            self.set_rights(recid)
        # The commitment point. One POST — then the truth is the
        # archive's state (#484, measured on v1.34.1: the publish landed
        # and the response was lost). On any failure to get a DOI out of
        # the response, re-read the oracle: present means this run's
        # publish landed — green; absent means it failed — red. First
        # mints have no concept to re-read; there a lost response stays
        # red, and the rerun fails closed on the title guard.
        doi, concept = "", ""
        try:
            published = an_object(
                loaded(z.call("POST", dep.publish), "the publish answer"),
                "the publish answer",
            )
            said_doi, said_concept = published.get("doi"), published.get("conceptdoi")
            doi = (said_doi if isinstance(said_doi, str) else "") or ""
            concept = (
                said_concept if isinstance(said_concept, str) else ""
            ) or FIRST_CONCEPT
        except (HttpError, RefusedError) as e:
            print(e, file=sys.stderr)
        if not doi and concept_recid:
            # Spaced reads: a publish that landed as the response was
            # lost may take a moment to appear as the latest version.
            for attempt in range(1, RECOVERY_READS + 1):
                now = self.oracle(concept_recid)
                if now is not None and now[3] == self.version:
                    self.verify_published(now[2], now[0])
                    return self.done(now[0], now[1], RECOVERED)
                if attempt < RECOVERY_READS:
                    pause(RECOVERY_SPACING)
        if not doi:
            msg = (
                "publish failed and the concept's latest published version is"
                f" not {self.version}"
            )
            raise RefusedError(msg)
        self.verify_published(recid, doi)
        return self.done(doi, concept, "" if concept_recid else FIRST)


class Args(argparse.Namespace):
    """The step's one flag, typed as parsed."""

    pipeline: str


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Run the step from its environment.

    Returns:
        The exit status: 0 published (or converged), 1 refused.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pipeline",
        required=True,
        help="the description's sentence on how the release was built and proven",
    )
    args = parser.parse_args(argv, namespace=Args())
    env = os.environ if env is None else env
    mint = Mint(
        env=env,
        zenodo=Zenodo(env["BASE_URL"], env["ZENODO_TOKEN"], env["ORG_USER_AGENT"]),
        version=env["UNTRUSTED_VERSION"],
        tag=env["TAG"],
        repo=env["GITHUB_REPOSITORY"],
        summary=env["GITHUB_STEP_SUMMARY"],
    )
    try:
        return mint.run(args.pipeline)
    except RefusedError as e:
        workflow_command.error(str(e))
    except (HttpError, ValueError, KeyError, TypeError) as e:
        workflow_command.error(f"{type(e).__name__}: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

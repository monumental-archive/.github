#!/usr/bin/env python3
"""Reconcile each signer-identity match against a signing the org performed.

rekor-monitor.yml's CT monitor prints `Found <subject> <issuer> <index>`
for every certificate Fulcio issued to the signer identity, and every
release the org signs produces one (#1250). A forged issuance would look
the same, so each match is reconciled against the forge, and only a
match for which ALL of these hold is accepted. Only the run id and the
Build Signer Digest D are taken from the certificate; the rest is read
from the forge.

  run            the run exists, in a repository of the org's roster
                 (slsa/assert-policy.json);
  foreign        every reusable workflow the run called is the canon's
                 or the signer's sign.yml;
  signer-in-run  the signer's sign.yml was called at exactly D;
  canon-branch   every candidate canon file is at a commit on the
                 canon's default branch;
  chain          at least one candidate, read at that commit, has a
                 `uses:` of sign.yml pinned to D. The candidates are the
                 canon workflows the run called, and, only when the run
                 is in the canon itself, its top-level workflow at
                 head_sha (vex-attest.yml calls the signer directly);
  direct         outside the canon, the run's top-level workflow at
                 head_sha does not call the signer: a repository calls
                 the signer only through the canon;
  signer-branch  D is on the signer repository's default branch.

"The canon" is the repository this runs in (GITHUB_REPOSITORY). No list
of signer callers is kept: the chain link is read at the commit the run
used.

The claims are read from the CT log entry itself, never from the
monitor's output line beyond its index. A certificate that fails a
check is a finding (an issue, deduplicated by index). A run the forge
answers 404 for is a finding on `run`: deleted cannot be told from
never existed, so it is never reconciled. Any other read that cannot be
completed (403, throttling, 5xx, the log unreachable) is an error that
fails the job by name, and nothing after it is counted as reconciled.

Usage:
  reconcile.py MONITOR_LOG          reconcile every `Found` line
  reconcile.py --index N            reconcile one CT index

Stdlib only: the runner's python3, no packages, like the belt helpers.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

CT_LOG = os.environ.get("CT_LOG", "https://ctfe.sigstore.dev/2022")
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
ORG = "monumental-archive"
SIGNER_REPO = f"{ORG}/signer"
SIGNER_SIGN = f"{SIGNER_REPO}/.github/workflows/sign.yml"
SIGNER_WORKFLOW = f"https://github.com/{SIGNER_SIGN}@"
USER_AGENT = "monumental-archive/.github/rekor-monitor"

HTTP_OK = 200
HTTP_NOT_FOUND = 404
DER_EXTENSIONS = 0xA3  # [3] EXPLICIT Extensions in a TBSCertificate
DER_URI = 0x86  # [6] IMPLICIT uniformResourceIdentifier in GeneralNames
PRECERT_ENTRY = 1  # RFC 6962 LogEntryType precert_entry
EXIT_READ_ERROR = 2

# Fulcio's certificate extensions (docs/oid-info.md in sigstore/fulcio).
# The v2 extensions (.8 onwards) are DER UTF8Strings.
OID_SAN = "2.5.29.17"
CLAIM_NAMES = {
    "1.3.6.1.4.1.57264.1.8": "issuer",
    "1.3.6.1.4.1.57264.1.9": "build_signer_uri",
    "1.3.6.1.4.1.57264.1.10": "build_signer_digest",
    "1.3.6.1.4.1.57264.1.12": "source_repository_uri",
    "1.3.6.1.4.1.57264.1.13": "source_repository_digest",
    "1.3.6.1.4.1.57264.1.14": "source_repository_ref",
    "1.3.6.1.4.1.57264.1.18": "build_config_uri",
    "1.3.6.1.4.1.57264.1.21": "run_invocation_uri",
}

# The conditions a match must meet, named as the issue it files names them.
CONDITIONS = {
    "run": "the run in the certificate exists in a repository of the roster",
    "foreign": (
        "every reusable workflow the run called is the canon's or the signer's sign.yml"
    ),
    "signer-in-run": (
        "the run called the signer's sign.yml at exactly the certificate's"
        " signer digest"
    ),
    "canon-branch": (
        "every canon file in the chain is at a commit on the canon's default branch"
    ),
    "chain": (
        "a canon file, read at the commit the run used, calls sign.yml pinned"
        " to the certificate's digest"
    ),
    "direct": "outside the canon, the run's own workflow does not call the signer",
    "signer-branch": "the signer commit is on the signer's default branch",
}

RUN_URI = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/actions/runs/(\d+)(?:/attempts/\d+)?$"
)
FOUND = re.compile(r"^Found \S+ \S+ (\d+)\s*$")
DIGEST = re.compile(r"[0-9a-f]{40}")


class ReadError(Exception):
    """A read that could not be completed: fails the job, reconciles nothing."""


# --- DER ---------------------------------------------------------------------


def _der(buf: bytes, pos: int) -> tuple[int, int, int]:
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(buf[pos : pos + n], "big")
        pos += n
    return tag, pos, pos + length


def _children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out = []
    while start < end:
        element = _der(buf, start)
        out.append(element)
        start = element[2]
    return out


def _oid(raw: bytes) -> str:
    parts = [raw[0] // 40, raw[0] % 40]
    value = 0
    for b in raw[1:]:
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(p) for p in parts)


def _uri_from_san(value: bytes) -> str | None:
    _, start, stop = _der(value, 0)
    for tag, name_start, name_stop in _children(value, start, stop):
        if tag == DER_URI:
            return value[name_start:name_stop].decode()
    return None


def tbs_claims(tbs: bytes) -> dict[str, str]:
    """Read the signer claims from a DER TBSCertificate.

    Returns:
        The subject alternative name (`san`) and each Fulcio extension
        named in CLAIM_NAMES that the certificate carries.

    """
    _, start, stop = _der(tbs, 0)
    claims: dict[str, str] = {}
    for tag, field_start, _ in _children(tbs, start, stop):
        if tag != DER_EXTENSIONS:
            continue
        _, ext_start, ext_stop = _der(tbs, field_start)
        for _, one_start, one_stop in _children(tbs, ext_start, ext_stop):
            parts = _children(tbs, one_start, one_stop)
            oid = _oid(tbs[parts[0][1] : parts[0][2]])
            value = tbs[parts[-1][1] : parts[-1][2]]  # extnValue OCTET STRING
            if oid == OID_SAN:
                uri = _uri_from_san(value)
                if uri is not None:
                    claims["san"] = uri
            elif oid in CLAIM_NAMES:
                _, utf8_start, utf8_stop = _der(value, 0)
                claims[CLAIM_NAMES[oid]] = value[utf8_start:utf8_stop].decode()
    return claims


def leaf_tbs(leaf_input: bytes) -> bytes:
    """Extract the TBSCertificate from an RFC 6962 MerkleTreeLeaf.

    Returns:
        The DER TBSCertificate: the precertificate's own for a precert
        entry, the first child of the certificate for an x509 entry.

    """
    entry_type = int.from_bytes(leaf_input[10:12], "big")
    pos = 12
    if entry_type == PRECERT_ENTRY:  # issuer_key_hash[32], TBS<1..2^24-1>
        pos += 32
    length = int.from_bytes(leaf_input[pos : pos + 3], "big")
    body = leaf_input[pos + 3 : pos + 3 + length]
    if entry_type == PRECERT_ENTRY:
        return body
    _, cert_start, _ = _der(body, 0)
    _, _, tbs_stop = _der(body, cert_start)
    return body[cert_start:tbs_stop]


# --- the forge and the log ---------------------------------------------------


def http_json(url: str, token: str | None = None) -> tuple[int, object]:
    """GET an https URL and decode its JSON body.

    Returns:
        The HTTP status, and the decoded body (None unless 200).

    Raises:
        ReadError: the URL is not https, or the request did not complete.

    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        msg = f"refusing a non-https URL: {url}"
        raise ReadError(msg)
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=30)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except (OSError, http.client.HTTPException) as err:
        msg = f"{url}: {err}"
        raise ReadError(msg) from err
    finally:
        connection.close()
    if response.status != HTTP_OK:
        return response.status, None
    return response.status, json.loads(raw)


def fetch_entry(index: int) -> dict[str, str]:
    """Read one CT log entry's signer claims.

    Returns:
        The claims tbs_claims reads from the entry's certificate.

    Raises:
        ReadError: the log did not answer with exactly that entry.

    """
    status, body = http_json(f"{CT_LOG}/ct/v1/get-entries?start={index}&end={index}")
    if status != HTTP_OK or not isinstance(body, dict) or len(body["entries"]) != 1:
        msg = f"CT log get-entries {index}: HTTP {status}"
        raise ReadError(msg)
    leaf = base64.b64decode(body["entries"][0]["leaf_input"])
    return tbs_claims(leaf_tbs(leaf))


@dataclass
class Forge:
    """GitHub reads; every non-200 except a 404 on a run is a ReadError."""

    token: str | None
    get: Callable[[str, str | None], tuple[int, object]] | None = None

    def _get(self, path: str) -> tuple[int, object]:
        return (self.get or http_json)(f"{API}{path}", self.token)

    def run(self, repo: str, run_id: str) -> dict | None:
        """Read a workflow run.

        Returns:
            The run, or None when the forge answers 404.

        Raises:
            ReadError: any other answer than 200 or 404.

        """
        status, body = self._get(f"/repos/{repo}/actions/runs/{run_id}")
        if status == HTTP_NOT_FOUND:
            return None
        if status != HTTP_OK or not isinstance(body, dict):
            msg = f"GET runs/{run_id} in {repo}: HTTP {status}"
            raise ReadError(msg)
        return body

    def contents(self, repo: str, path: str, ref: str) -> str:
        """Read a file at a commit.

        Returns:
            The file's text.

        Raises:
            ReadError: the forge did not return the file.

        """
        status, body = self._get(f"/repos/{repo}/contents/{path}?ref={ref}")
        if status != HTTP_OK or not isinstance(body, dict):
            msg = f"GET {repo}/{path}@{ref}: HTTP {status}"
            raise ReadError(msg)
        return base64.b64decode(body["content"]).decode()

    def on_default_branch(self, repo: str, sha: str) -> bool:
        """Ask whether a commit is reachable from a repository's default branch.

        Returns:
            True when the commit is the default branch's head or behind it.

        Raises:
            ReadError: the forge did not answer either read.

        """
        status, body = self._get(f"/repos/{repo}")
        if status != HTTP_OK or not isinstance(body, dict):
            msg = f"GET {repo}: HTTP {status}"
            raise ReadError(msg)
        branch = body["default_branch"]
        status, body = self._get(f"/repos/{repo}/compare/{branch}...{sha}")
        if status == HTTP_NOT_FOUND:
            return False
        if status != HTTP_OK or not isinstance(body, dict):
            msg = f"GET {repo} compare {branch}...{sha}: HTTP {status}"
            raise ReadError(msg)
        return body["status"] in {"identical", "behind"}


# --- reconciliation ----------------------------------------------------------


def _pins_signer(text: str, digest: str) -> bool:
    return re.search(rf"uses:\s*{re.escape(SIGNER_SIGN)}@{digest}\b", text) is not None


def _calls_signer(text: str) -> bool:
    return re.search(rf"uses:\s*{re.escape(SIGNER_SIGN)}@", text) is not None


@dataclass
class Verdict:
    """One certificate's reconciliation: the conditions it failed, and why."""

    index: int
    claims: dict[str, str]
    failed: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)

    @property
    def reconciled(self) -> bool:
        """Whether every condition held."""
        return not self.failed

    def fail(self, condition: str, why: str) -> None:
        """Record a failed condition and the reason."""
        if condition not in self.failed:
            self.failed.append(condition)
        self.detail.append(f"{condition}: {why}")

    def issue(self) -> dict[str, str]:
        """Render the issue a finding files: one per certificate, by CT index.

        Returns:
            The issue's title, body, and the key it is deduplicated by.

        """
        lines = [
            (
                f"The signer-identity monitor found a certificate at CT index"
                f" {self.index} that does not reconcile against a signing the"
                " org performed (#1250)."
            ),
            "",
            f"- CT index: {self.index} ({CT_LOG})",
            f"- subject: {self.claims.get('san', '(none)')}",
            "",
            "Failed:",
            *[f"- **{c}** ({CONDITIONS[c]})" for c in self.failed],
            "",
            "Detail:",
            *[f"- {d}" for d in self.detail],
            "",
            "Claims read from the CT entry:",
            "",
            "```json",
            json.dumps(self.claims, indent=2),
            "```",
            "",
            (
                'Runbook: docs/runbook.md, "rekor-monitor: a certificate did not'
                ' reconcile".'
            ),
        ]
        failed = ", ".join(self.failed)
        return {
            "title": (
                f"rekor-monitor: certificate at CT index {self.index}"
                f" did not reconcile ({failed})"
            ),
            "key": f"CT index {self.index}",
            "body": "\n".join(lines),
        }


def roster(root: Path, owner: str) -> set[str]:
    """Read the org's repositories from the roster.

    Returns:
        Every `owner/repo` named in slsa/assert-policy.json's population.

    """
    policy = json.loads((root / "slsa/assert-policy.json").read_text(encoding="utf-8"))
    return {f"{owner}/{r['repo']}" for r in policy["population"]["repositories"]}


def _read_run(verdict: Verdict, forge: Forge, members: set[str]) -> dict | None:
    uri = verdict.claims.get("run_invocation_uri", "")
    match = RUN_URI.match(uri)
    if not match:
        verdict.fail("run", f"no Run Invocation URI a run can be read from: {uri!r}")
        return None
    owner, name, run_id = match.groups()
    repo = f"{owner}/{name}"
    if repo not in members:
        verdict.fail(
            "run", f"{repo} is not a repository of the roster (slsa/assert-policy.json)"
        )
        return None
    run = forge.run(repo, run_id)
    if run is None:
        verdict.fail(
            "run", f"run {run_id} does not exist in {repo} (never existed, or deleted)"
        )
        return None
    verdict.detail.append(f"run {run['html_url']} ({run['path']}, {run['event']})")
    return run


def _candidates(
    verdict: Verdict, run: dict, digest: str, canon: str
) -> list[tuple[str, str]]:
    signer_shas = set()
    candidates = []
    for workflow in run.get("referenced_workflows", []):
        path, _, ref = workflow["path"].partition("@")
        if path == SIGNER_SIGN:
            signer_shas.add(workflow["sha"])
        elif path.startswith(f"{canon}/"):
            candidates.append((path.removeprefix(f"{canon}/"), workflow["sha"]))
        else:
            verdict.fail(
                "foreign",
                f"the run called {path}@{ref}, which is neither the canon's"
                " nor the signer's",
            )
    if digest not in signer_shas:
        verdict.fail(
            "signer-in-run",
            f"the run called sign.yml at {sorted(signer_shas) or 'no commit'},"
            f" the certificate says {digest}",
        )
    if run["repository"]["full_name"] == canon:
        candidates.append((run["path"], run["head_sha"]))
    return sorted(set(candidates))


def _check_direct(verdict: Verdict, run: dict, forge: Forge, canon: str) -> None:
    repo = run["repository"]["full_name"]
    if repo == canon:
        return
    if _calls_signer(forge.contents(repo, run["path"], run["head_sha"])):
        verdict.fail(
            "direct",
            f"{repo}'s {run['path']} at {run['head_sha']} calls the signer itself",
        )


def _check_chain(
    verdict: Verdict,
    candidates: list[tuple[str, str]],
    forge: Forge,
    digest: str,
    canon: str,
) -> None:
    on_branch: dict[str, bool] = {}
    for path, sha in candidates:
        if sha not in on_branch:
            on_branch[sha] = forge.on_default_branch(canon, sha)
        if not on_branch[sha]:
            verdict.fail(
                "canon-branch",
                f"{canon}/{path} at {sha} is not on {canon}'s default branch",
            )
    for path, sha in candidates:
        if _pins_signer(forge.contents(canon, path, sha), digest):
            verdict.detail.append(
                f"chain {canon}/{path}@{sha[:12]} calls sign.yml@{digest[:12]}"
            )
            return
    verdict.fail("chain", f"no canon file the run used calls sign.yml@{digest}")


def reconcile(
    index: int, claims: dict[str, str], forge: Forge, members: set[str], canon: str
) -> Verdict:
    """Reconcile one certificate against the forge.

    Only the run id and the Build Signer Digest are taken from the claims.

    Returns:
        The verdict: reconciled, or the conditions that failed and why.

    """
    verdict = Verdict(index, claims)
    digest = claims.get("build_signer_digest", "")
    san = claims.get("san", "")
    if not DIGEST.fullmatch(digest) or not san.startswith(SIGNER_WORKFLOW):
        verdict.fail(
            "signer-in-run", f"signer claim unreadable: SAN {san!r}, digest {digest!r}"
        )
        return verdict
    run = _read_run(verdict, forge, members)
    if run is not None:
        candidates = _candidates(verdict, run, digest, canon)
        _check_direct(verdict, run, forge, canon)
        _check_chain(verdict, candidates, forge, digest, canon)
    if forge.on_default_branch(SIGNER_REPO, digest):
        verdict.detail.append(f"signer {digest[:12]} on default branch")
    else:
        verdict.fail(
            "signer-branch",
            f"signer commit {digest} is not on {SIGNER_REPO}'s default branch",
        )
    return verdict


def found_indexes(log: str) -> list[int]:
    """Collect the CT indexes of the monitor's `Found` lines.

    Returns:
        Each index once, ascending.

    """
    return sorted({
        int(m.group(1)) for line in log.splitlines() if (m := FOUND.match(line))
    })


def _reconcile_all(
    indexes: list[int], forge: Forge, members: set[str], canon: str
) -> tuple[list[str], list[dict[str, str]]]:
    reconciled, findings = [], []
    for index in indexes:
        verdict = reconcile(index, fetch_entry(index), forge, members, canon)
        detail = "; ".join(verdict.detail)
        if verdict.reconciled:
            line = f"reconciled CT index {index}: {detail}"
            reconciled.append(line)
        else:
            failed = ", ".join(verdict.failed)
            line = f"NOT reconciled CT index {index} ({failed}): {detail}"
            findings.append(verdict.issue())
        print(line)
    return reconciled, findings


def main(argv: list[str]) -> int:
    """Reconcile the matches and write the findings and the summary.

    Returns:
        0 when every read completed (findings or not), 2 when one did not.

    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("monitor_log", nargs="?")
    parser.add_argument("--index", type=int, action="append", default=[])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--findings", type=Path, help="issues to file (JSON)")
    parser.add_argument("--summary", type=Path, help="append the result (Markdown)")
    args = parser.parse_args(argv)

    indexes = list(args.index)
    if args.monitor_log:
        indexes += found_indexes(Path(args.monitor_log).read_text(encoding="utf-8"))
    indexes = sorted(set(indexes))
    canon = os.environ.get("GITHUB_REPOSITORY") or f"{ORG}/.github"
    forge = Forge(os.environ.get("FORGE_TOKEN"))
    members = roster(args.root, canon.split("/", maxsplit=1)[0])

    try:
        reconciled, findings = _reconcile_all(indexes, forge, members, canon)
    except ReadError as err:
        print(
            "::error::reconcile could not complete a read;"
            f" nothing from here on is reconciled: {err}"
        )
        return EXIT_READ_ERROR
    if args.findings:
        args.findings.write_text(
            json.dumps(findings, indent=2) + "\n", encoding="utf-8"
        )
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(
                f"### Signer certificates: {len(indexes)} found,"
                f" {len(reconciled)} reconciled\n\n"
            )
            out.writelines(f"- {line}\n" for line in reconciled)
            out.writelines(f"- **{f['title']}**\n" for f in findings)
    print(
        f"{len(indexes)} match(es), {len(reconciled)} reconciled, {len(findings)} not"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

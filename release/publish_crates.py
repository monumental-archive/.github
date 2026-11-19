#!/usr/bin/env python3
"""Publish a workspace to crates.io, resumably (#1383; the step's history below).

The body of publish-rust-crate.yml's Publish step, moved out of the YAML
so it is tested where it lives. Run from the caller's checkout, with the
canon tree beside this file; `cargo` is the caller's pinned toolchain.

RESUMABLE. A crate already at this version is a completed step, not a
failure. crates.io is yank-only, so re-running a partly finished release
must converge rather than abort. `cargo publish --workspace` does not do
that by itself: it refuses the whole workspace when any member already
exists — edtf v1.3.3's crates leg died that way on every re-run (#1218).
So the index is asked first, and what is there is excluded.

1. The members this run would publish: workspace members with crates.io
   among their registries, with their versions. Nothing else narrows it
   (#1273): a member the build could not compile still publishes unless
   its own manifest says `publish = false`. The set is the canon helper's
   (crates-io-members.sh), the same one the build read to decide its
   release subjects (#1310) — one definition.
2. The sparse index, the static host, is asked for each exact name and
   version, at the path the belt's one index rule names (#1383). A 404 or
   a record without the version is "not published"; any other answer
   fails the run by status and is never read as "not published". The
   org's User-Agent rides anyway — the API host's policy is the reason it
   exists (#907).
3. A member on the index at this version is skipped only if it is the
   same bytes: it is packaged here (`--no-verify`, so no build runs, as
   for the publish) and the .crate's sha256 compared with the index's
   `cksum`. A resumed release must be resuming the release that was cut.
4. Nothing left is a finished leg, not a failure — and it mints no token
   it would not use.
5. Trusted publishing, by hand rather than through an action. This is the
   one job in the organisation holding `id-token: write` beside a checkout
   of caller source, and third-party code belongs there least of all.
   Contract read from crates.io's own exchange handler:
       POST /api/v1/trusted_publishing/tokens  {"jwt": "<oidc>"}
       -> {"token": "<short-lived crates.io token>"}
   The audience is `crates.io`. Neither token is ever printed, and the
   exchanged one is masked before it can reach a log. It is single-use —
   crates.io records the JWT's jti and rejects a replay — so each run
   mints its own.

THE USER-AGENT IS LOAD-BEARING (#907). crates.io's data access policy
requires a request to identify who is making it, and its protection layer
refuses an unidentified one BEFORE any trusted-publishing validation
happens. Without the header the exchange dies as HTTP 403 carrying only a
boilerplate "unable to process your request" naming neither the header
nor the endpoint, which reads like a trust-configuration mismatch — three
of edtf v1.3.2's release runs were spent looking at its publisher config,
which was correct the whole time. Measured 2026-08-24 with a deliberately
invalid JWT: UA-less answers 403, and with it the same request reaches
real validation and answers 400 "Failed to decode JWT". If that 403 ever
returns, the policy has tightened again — look at the header before
looking at the publisher config.

The environment carries ORG_USER_AGENT and, from the runner,
ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN.
"""

import hashlib
import json
import os

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

# The belt's crates.io index reader, beside this tree in mise/. A
# missing sibling is named, never a bare traceback (#1522).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))
try:
    import crates_index
    import decoded
    import workflow_command
except ModuleNotFoundError as missing:
    if missing.name != "crates_index":
        raise
    msg = "publish-crates: mise/crates_index.py did not arrive in the canon tree"
    raise SystemExit(msg) from None

if TYPE_CHECKING:
    from collections.abc import Mapping

HERE = Path(__file__).resolve().parent
EXCHANGE = "https://crates.io/api/v1/trusted_publishing/tokens"
HTTP_OK = 200
HTTP_NOT_FOUND = 404
TIMEOUT = 60


@dataclass
class Response:
    """One HTTP answer: the status (None when nothing answered) and body."""

    status: int | None
    body: bytes


def http(
    method: str, url: str, headers: Mapping[str, str], data: bytes | None = None
) -> Response:
    """Make one request, answering every status rather than raising.

    Returns:
        The status and body, or a None status when the host never answered.

    """
    # ruff: ignore[suspicious-url-open-usage] - https URLs this file names
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        # ruff: ignore[suspicious-url-open-usage] - https URLs this file names
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read())
    except urllib.error.URLError, TimeoutError:
        return Response(None, b"")


class Fetch(Protocol):
    """Make one HTTP request: `http`, or a test's stand-in for it."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: bytes | None = None,
    ) -> Response:
        """Answer one request."""
        ...


@dataclass(frozen=True)
class IndexRecord:
    """What the publish step reads of one index record."""

    # The record's version, None when it names none as a string.
    vers: str | None
    # Its checksum, None when absent, not a string, or empty.
    cksum: str | None


def say(line: object) -> None:
    """Print one job-log line at once, in order with cargo's own."""
    print(line, flush=True)


class RefusalError(Exception):
    """A stop with a message for the job log; the run exits 1."""


class CommandError(Exception):
    """A command this step runs exited non-zero; the run exits with it."""

    def __init__(self, argv: list[str], returncode: int) -> None:
        """Keep the command and its status, which the job log names."""
        super().__init__(argv, returncode)
        self.argv = argv
        self.returncode = returncode


def checked(
    argv: list[str], env: Mapping[str, str], cwd: Path, *, capture: bool
) -> str:
    """Run one command, which must succeed.

    Returns:
        Its standard output when captured, else "".

    Raises:
        CommandError: it exited non-zero.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        argv, env=dict(env), cwd=cwd, capture_output=capture, text=True, check=False
    )
    if done.returncode != 0:
        raise CommandError(argv, done.returncode)
    return done.stdout if capture else ""


def members(env: Mapping[str, str], cwd: Path) -> list[tuple[str, str]]:
    """Ask the shared member helper which crates publish to crates.io.

    Returns:
        Each member's name and version.

    """
    out = checked([str(HERE / "crates-io-members.sh")], env, cwd, capture=True)
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if line:
            name, version = line.split("\t", 1)
            pairs.append((name, version))
    return pairs


def index_records(body: bytes) -> list[IndexRecord] | None:
    """Read an index file: one JSON object per line.

    One that does not read so is unread, never "not published": bytes
    that are not UTF-8, or a line that is not an object, died in the
    reader as a traceback (#1556).

    Returns:
        Each record, or None when the file is not so.

    """
    try:
        lines = body.decode().split("\n")
    except UnicodeDecodeError:
        return None
    records: list[IndexRecord] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = decoded.load_json(line)
        except decoded.UndecodableError:
            return None
        if not decoded.is_object(record):
            return None
        vers, cksum = record.get("vers"), record.get("cksum")
        records.append(
            IndexRecord(
                vers=vers if isinstance(vers, str) else None,
                cksum=cksum if isinstance(cksum, str) and cksum else None,
            )
        )
    return records


def answer_field(body: str | bytes, key: str, where: str) -> str:
    """Read one string field of a JSON object something answered.

    Returns:
        The field's value.

    Raises:
        RefusalError: the answer is not JSON, not an object, or its field
            is not a string; naming `where`.

    """
    try:
        doc = decoded.load_json(body)
    except decoded.UndecodableError as err:
        msg = f"{where} is not JSON: {err}"
        raise RefusalError(msg) from err
    try:
        return decoded.as_str(
            decoded.as_object(doc, where).get(key), f"{where}'s {key}"
        )
    except decoded.UndecodableError as err:
        raise RefusalError(str(err)) from err


def published_checksum(
    name: str, version: str, user_agent: str, fetch: Fetch
) -> str | None:
    """Ask the sparse index whether name@version is published.

    Returns:
        The index's checksum for that version, or None when it is not published.

    Raises:
        RefusalError: when the index answered anything but 200 or 404,
            or a 200 that is not one JSON record per line with a cksum.

    """
    url = crates_index.index_url(name)
    answer = fetch("GET", url, {"User-Agent": user_agent})
    if answer.status == HTTP_NOT_FOUND:
        return None
    if answer.status != HTTP_OK:
        said = answer.status if answer.status is not None else "nothing"
        msg = (
            f"the crates.io index answered {said} for {name} ({url});"
            ' an unread index is not "not published"'
        )
        raise RefusalError(msg)
    records = index_records(answer.body)
    for record in records or []:
        if record.vers == version:
            if record.cksum is not None:
                return record.cksum
            records = None
            break
    if records is None:
        msg = (
            f"the crates.io index for {name} ({url}) is not one JSON"
            ' record per line with a cksum; an unread index is not "not published"'
        )
        raise RefusalError(msg)
    return None


def exchange_token(env: Mapping[str, str], fetch: Fetch) -> str:
    """Mint the runner's OIDC token for crates.io and exchange it.

    Returns:
        The short-lived crates.io token.

    Raises:
        RefusalError: when either request is answered with anything but 200,
            or with anything but a JSON object holding its token string.

    """
    oidc = fetch(
        "GET",
        env["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=crates.io",
        {"Authorization": "bearer " + env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
    )
    if oidc.status != HTTP_OK:
        said = oidc.status or "nothing"
        msg = f"the runner's OIDC token endpoint answered {said}"
        raise RefusalError(msg)
    jwt = answer_field(oidc.body, "value", "the runner's OIDC token answer")
    payload: dict[str, str] = {"jwt": jwt}
    exchanged = fetch(
        "POST",
        EXCHANGE,
        {"content-type": "application/json", "User-Agent": env["ORG_USER_AGENT"]},
        json.dumps(payload).encode(),
    )
    if exchanged.status != HTTP_OK:
        said = exchanged.status or "nothing"
        msg = (
            f"the crates.io token exchange answered {said}"
            " — if 403, check the User-Agent before the publisher config (#907)"
        )
        raise RefusalError(msg)
    return answer_field(
        exchanged.body, "token", "the crates.io token exchange's answer"
    )


def run(env: Mapping[str, str], cwd: Path, fetch: Fetch = http) -> int:
    """Publish what is not yet on crates.io.

    An unread index and a failed exchange raise RefusalError from the
    helpers above; a checksum mismatch is printed per crate and exits 1.

    Returns:
        The exit code: cargo publish's own, or 0 when nothing was left.

    """
    # cargo is the caller's pinned toolchain, resolved on PATH.
    metadata_cmd = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--no-deps",
        "--locked",
    ]
    metadata = checked(metadata_cmd, env, cwd, capture=True)
    target_dir = Path(answer_field(metadata, "target_directory", "cargo metadata"))

    held: list[tuple[str, str, str]] = []
    pending = 0
    for name, version in members(env, cwd):
        cksum = published_checksum(name, version, env["ORG_USER_AGENT"], fetch)
        if cksum:
            held.append((name, version, cksum))
        else:
            pending += 1

    excludes: list[str] = []
    if held:
        package = ["cargo", "package", "--locked", "--no-verify"]
        for name, _, _ in held:
            package += ["-p", name]
        checked(package, env, cwd, capture=False)
        mismatched = False
        for name, version, cksum in held:
            crate = target_dir / "package" / f"{name}-{version}.crate"
            local = hashlib.sha256(crate.read_bytes()).hexdigest()
            if local != cksum:
                workflow_command.error(
                    f"{name} {version} is on crates.io with checksum"
                    f" {cksum}, but this tree packages {local}"
                )
                mismatched = True
                continue
            say(f"{name}: already on crates.io at {version}, skipped")
            excludes += ["--exclude", name]
        if mismatched:
            return 1

    if pending == 0:
        say(
            "every crate this release publishes is already on crates.io"
            " at its version; nothing to publish"
        )
        return 0

    token = exchange_token(env, fetch)
    workflow_command.mask(token)
    publish = ["cargo", "publish", "--workspace", "--locked", "--no-verify", *excludes]
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        publish, env={**env, "CARGO_REGISTRY_TOKEN": token}, cwd=cwd, check=False
    ).returncode


def main() -> int:
    """Run against the job's environment and working directory.

    Returns:
        The exit code.

    """
    try:
        return run(os.environ, Path.cwd())
    except RefusalError as e:
        workflow_command.error(str(e))
        return 1
    except CommandError as e:
        workflow_command.error(f"{' '.join(e.argv)} exited {e.returncode}")
        return e.returncode


if __name__ == "__main__":
    sys.exit(main())

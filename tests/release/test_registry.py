"""Run `release/registry.py` against a stub `docker` and a fake registry.

#1382 moved four workflows' registry plumbing into `release/registry.py`
(the buildx floor, flags from the facts, push by digest, the index's
assembly and upload, tagging a gated digest). None of the four blocks
had a test. These rows run the helper in-process, as the steps call it,
with a stub `docker` first on PATH and an `http.server` on 127.0.0.1
standing in for ghcr.io (the `origin` the tests pass to `main`; the
production path never reads one from the environment). The fake refuses a route it
does not know (HTTP 418) and a manifest request without the bearer
token its token endpoint issued. Every refusal row asserts the message.

The stub's `buildx imagetools inspect` asks the fake for the reference
and answers the sha256 of the bytes it holds, in the shape buildx
prints, so "the registry answered another digest" is a registry that
stored other bytes, not a canned answer.

CAPTURED, not written (2026-11-17, buildx v0.36.1-desktop.1, reads of
the public `docker.io/library/alpine` only, nothing pushed):

- `testdata/registry-buildx-version.txt`: `docker buildx version`.
- `testdata/registry-index-dry-run.out`: `docker buildx imagetools
  create --dry-run` with two `index:` annotations over alpine 3.20's
  amd64 and arm64 manifests, byte for byte: the index ends in one
  newline.
- `testdata/registry-inspect-manifest.out`: `imagetools inspect
  <ref> --format '{{json .Manifest}}'` on the amd64 manifest. buildx
  printed no final newline; one was added for the tree's
  editorconfig, the only edit.

NOT CAPTURED: the `--metadata-file` a push writes. The daemon was not
running and a push needs a registry; the rows use the one key the
helper reads, `containerimage.digest`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, NotRequired, TypedDict, override

import pytest

from belt import decoded
from belt.release import registry
from belt.typed_pytest import fixture
from tests import support


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["registry"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def dumps(value: object) -> str:
    """Encode a planted document as JSON.

    Returns:
        The JSON text.

    """
    return json.dumps(value)


HERE = support.RELEASE
TESTDATA = Path(__file__).resolve().parent / "testdata"
VERSION = TESTDATA / "registry-buildx-version.txt"
DRY_RUN = TESTDATA / "registry-index-dry-run.out"
INSPECT = TESTDATA / "registry-inspect-manifest.out"

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
TOKEN = "registry-token"  # ruff: ignore[hardcoded-password-string] - the fake's own bearer token
GH_TOKEN = "actions-token"  # ruff: ignore[hardcoded-password-string] - a stand-in, never sent anywhere real
ACTOR = "release-bot"
AMD = "sha256:" + "a" * 64
ARM = "sha256:" + "b" * 64
FACTS = dumps(
    {
        "org.opencontainers.image.version": "1.2.3",
        "org.opencontainers.image.revision": "0123456789abcdef",
    }
)

DOCKER_STUB = r'''#!{python} -IS
"""Stub `docker`: logs its argv, answers each subcommand the helper runs."""
import hashlib, json, os, sys, urllib.request
args = sys.argv[1:]
env = os.environ
with open(env["STUB_LOG"], "a") as f:
    stdin = sys.stdin.read() if args[:1] == ["login"] else None
    f.write(json.dumps({{"argv": args, "stdin": stdin}}) + "\n")
if args[:2] == ["buildx", "version"]:
    sys.stdout.write(open(env["STUB_VERSION"]).read())
elif args[:1] == ["login"]:
    sys.exit(int(env.get("STUB_LOGIN_RC", "0")))
elif args[:2] == ["buildx", "build"]:
    meta = args[args.index("--metadata-file") + 1]
    if "STUB_META" in env:
        open(meta, "w").write(env["STUB_META"])
    sys.exit(int(env.get("STUB_BUILD_RC", "0")))
elif args[:4] == ["buildx", "imagetools", "create", "--dry-run"]:
    sys.stdout.buffer.write(open(env["STUB_DRY_RUN"], "rb").read())
elif args[:3] == ["buildx", "imagetools", "inspect"]:
    ref = args[3].removeprefix("ghcr.io/")
    name, sep, reference = ref.rpartition("@") if "@" in ref else ref.rpartition(":")
    url = env["STUB_REGISTRY_URL"] + "/v2/" + name + "/manifests/" + reference
    req = urllib.request.Request(url, headers={{"X-Stub": "inspect"}})
    body = urllib.request.urlopen(req).read()
    shape = json.loads(open(env["STUB_INSPECT"]).read())
    if "STUB_NO_DIGEST" not in env:
        shape["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
    else:
        shape.pop("digest")
    sys.stdout.write(json.dumps(shape, indent=2))
else:
    sys.stderr.write("stub docker: unexpected " + " ".join(args) + "\n")
    sys.exit(97)
'''


class Logged(TypedDict):
    """One request the fake was sent, as it logged it."""

    method: str
    path: str
    headers: dict[str, str]
    body: NotRequired[bytes]


class Call(TypedDict):
    """One docker call, as the stub logged it."""

    argv: list[str]
    stdin: str | None


class Fake(BaseHTTPRequestHandler):
    """ghcr.io's token endpoint and manifest routes, nothing else."""

    manifests: ClassVar[dict[tuple[str, str], tuple[bytes, str]]] = {}
    log: ClassVar[list[Logged]] = []
    token_body: ClassVar[bytes] = dumps({"token": TOKEN}).encode()
    rewrite: ClassVar[bool] = False

    @override
    def log_message(self, *_: object) -> None:
        """Keep the test output clean."""

    def answer(self, code: int, body: bytes = b"", ctype: str = "") -> None:
        """Send one response."""
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self) -> tuple[str, str] | None:
        """Split a manifest path.

        Returns:
            (name, reference), or None for any other path.

        """
        # Plain splitting, never a pattern: the path is the request's.
        name, sep, reference = self.path.removeprefix("/v2/").rpartition("/manifests/")
        if not self.path.startswith("/v2/") or not sep or not name:
            return None
        if not reference or "/" in reference:
            return None
        return name, reference

    def authorised(self) -> bool:
        """Say whether the request carries the issued bearer token.

        Returns:
            True for the token, or for the stub docker's own read.

        """
        return (
            self.headers.get("Authorization") == f"Bearer {TOKEN}"
            or self.headers.get("X-Stub") == "inspect"
        )

    def do_GET(self) -> None:
        """Serve a token or a manifest."""
        if self.headers.get("X-Stub") != "inspect":
            Fake.log.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                }
            )
        if self.path.startswith("/token?"):
            self.answer(200, Fake.token_body, "application/json")
            return
        route = self.route()
        if route is None:
            self.answer(418)
            return
        if not self.authorised():
            self.answer(401)
            return
        if route not in Fake.manifests:
            self.answer(404)
            return
        body, ctype = Fake.manifests[route]
        self.answer(200, body, ctype)

    def do_PUT(self) -> None:
        """Store a manifest under its reference and its own digest."""
        body = self.rfile.read(int(self.headers["Content-Length"]))
        ctype = self.headers.get("Content-Type", "")
        Fake.log.append(
            {
                "method": "PUT",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )
        route = self.route()
        if route is None:
            self.answer(418)
            return
        if not self.authorised():
            self.answer(401)
            return
        if Fake.rewrite:
            body = body.replace(b'"schemaVersion": 2', b'"schemaVersion":2')
        name, reference = route
        Fake.manifests[name, reference] = (body, ctype)
        Fake.manifests[name, "sha256:" + hashlib.sha256(body).hexdigest()] = (
            body,
            ctype,
        )
        self.answer(201)


class Harness:
    """A fresh fake, a stub docker on PATH, and the step's environment."""

    def __init__(self, request: pytest.FixtureRequest, tmp_path: Path) -> None:
        """Start the fake and lay out the stub."""
        Fake.manifests = {}
        Fake.log = []
        Fake.token_body = dumps({"token": TOKEN}).encode()
        Fake.rewrite = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            daemon=True,
        ).start()
        request.addfinalizer(self.server.server_close)
        request.addfinalizer(self.server.shutdown)
        self.tmp = Path(str(tmp_path))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "docker"
        stub.write_text(DOCKER_STUB.format(python=sys.executable), "utf-8")
        stub.chmod(0o755)
        self.output = self.tmp / "github-output"
        self.output.write_text("", "utf-8")
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            # The stub docker's own variable: the test writes the stub.
            "STUB_REGISTRY_URL": self.origin,
            "GITHUB_ACTOR": ACTOR,
            "GITHUB_REPOSITORY": "Some-Owner/Some-Image",
            "GITHUB_OUTPUT": str(self.output),
            "GH_TOKEN": GH_TOKEN,
            "FACTS": FACTS,
            "STUB_LOG": str(self.tmp / "docker.log"),
            "STUB_VERSION": str(VERSION),
            "STUB_DRY_RUN": str(DRY_RUN),
            "STUB_INSPECT": str(INSPECT),
        }

    def docker_calls(self) -> list[Call]:
        """Read the stub's log.

        Returns:
            One {argv, stdin} per docker call, in order.

        """
        log = Path(self.env["STUB_LOG"])
        if not log.exists():
            return []
        calls: list[Call] = []
        for ln in log.read_text("utf-8").splitlines():
            call = decoded.as_object(decoded.load_json(ln), "a docker call")
            argv = decoded.as_array(call["argv"], "argv")
            stdin = call["stdin"]
            calls.append(
                {
                    "argv": [decoded.as_str(a, "an argument") for a in argv],
                    "stdin": None if stdin is None else decoded.as_str(stdin, "stdin"),
                }
            )
        return calls

    def argvs(self) -> list[list[str]]:
        """Read each docker call's argv from the stub's log.

        Returns:
            One argv per docker call, in order.

        """
        return [c["argv"] for c in self.docker_calls()]

    def version(self, text: str) -> None:
        """Make the stub's `buildx version` print `text`."""
        path = self.tmp / "version.txt"
        path.write_text(text, "utf-8")
        self.env["STUB_VERSION"] = str(path)

    def dry_run(self, data: bytes) -> None:
        """Make the stub's `imagetools create --dry-run` print `data`."""
        path = self.tmp / "dry-run.out"
        path.write_bytes(data)
        self.env["STUB_DRY_RUN"] = str(path)


@fixture
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    """Give each row its own Harness."""
    return Harness(request, tmp_path)


@pytest.mark.usefixtures("harness")
def test_the_captured_version_passes() -> None:
    """Buildx v0.36.1-desktop.1, as captured, is above the floor."""
    try:
        registry.buildx_floor(VERSION.read_text("utf-8"))
    except registry.RefusedError as e:
        pytest.fail(f"refused: {e}")


@pytest.mark.usefixtures("harness")
def test_the_floor_itself_passes() -> None:
    """0.12.0 is the floor, not below it."""
    try:
        registry.buildx_floor("github.com/docker/buildx v0.12.0 abc\n")
    except registry.RefusedError as e:
        pytest.fail(f"refused: {e}")


@pytest.mark.usefixtures("harness")
def test_refusals() -> None:
    """Below the floor, or no version: the old step's exact words."""
    rows = [
        ("github.com/docker/buildx v0.11.2 abc\n", "buildx 0.11"),
        ("github.com/docker/buildx v0.9.1 abc\n", "buildx 0.9"),
        ("github.com/docker/buildx dev abc\n", "buildx unknown"),
        ("", "buildx unknown"),
    ]
    for text, shown in rows:
        with pytest.raises(registry.RefusedError) as cm:
            registry.buildx_floor(text)
        assert (
            str(cm.value)
            == f"{shown} < 0.12: index annotations would be dropped silently"
        )


def test_assemble_refuses_below_the_floor_before_any_push(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """The assemble step asserts the floor first."""
    harness.version("github.com/docker/buildx v0.11.2 abc\n")
    code = registry.main(
        ["assemble", "--digests", str(harness.tmp), "--tag", "1.2.3"],
        harness.env,
        origin=harness.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        f"::error::registry: {'buildx 0.11 < 0.12: index annotations would be dropped silently'}\n"
        in out
    )
    only: list[list[str]] = [["buildx", "version"]]
    assert harness.argvs() == only
    assert Fake.log == list[Logged]()


def test_a_pgrx_push_refuses_below_the_floor(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """The pgrx index carries annotations, so its push asserts the floor."""
    harness.version("github.com/docker/buildx v0.11.2 abc\n")
    code = registry.main([*pgrx_push(harness.tmp)], harness.env, origin=harness.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        f"::error::registry: {'buildx 0.11 < 0.12: index annotations would be dropped silently'}\n"
        in out
    )
    only: list[list[str]] = [["buildx", "version"]]
    assert harness.argvs() == only


@pytest.mark.usefixtures("harness")
def test_pairs_keep_the_map_order() -> None:
    """Labels and annotations follow the map, one flag per fact."""
    want: list[str] = [
        "--label",
        "org.opencontainers.image.version=1.2.3",
        "--label",
        "org.opencontainers.image.revision=0123456789abcdef",
        "--annotation",
        "index:org.opencontainers.image.version=1.2.3",
        "--annotation",
        "index:org.opencontainers.image.revision=0123456789abcdef",
    ]
    assert registry.fact_flags(FACTS, labels=True, index=True) == want


@pytest.mark.usefixtures("harness")
def test_non_string_values_render_as_compact_json() -> None:
    r"""A number or an object reads as jq's `\(.value)` wrote it."""
    want: list[str] = ["a=1", 'b={"c":[1,"x"]}', "d=e f"]
    assert registry.facts_pairs('{"a": 1, "b": {"c": [1, "x"]}, "d": "e f"}') == want


@pytest.mark.usefixtures("harness")
@pytest.mark.parametrize("facts", ["[]", "not json", '"x"', "[" * 100_000])
def test_facts_that_are_not_an_object_refuse(facts: object) -> None:
    """Jq died on these; the helper names what it was given."""
    with pytest.raises(registry.RefusedError) as cm:
        registry.facts_pairs(facts)
    assert str(cm.value) == "facts is not a JSON object"


def oci_push(tmp: Path) -> list[str]:
    """Argv of build-oci-image.yml's push step.

    Returns:
        The helper's arguments.

    """
    return [
        "push",
        "--image",
        "ghcr.io/some-owner/some-image",
        "--file",
        "ctx/Dockerfile",
        "--context",
        "ctx",
        "--digests",
        str(tmp / "digests"),
        "--metadata-file",
        str(tmp / "meta.json"),
        "--arch",
        "amd64",
    ]


def pgrx_push(tmp: Path) -> list[str]:
    """Argv of build-pgrx-images.yml's push step.

    Returns:
        The helper's arguments.

    """
    return [
        "push",
        "--image",
        "ghcr.io/some-owner/lab_pg",
        "--file",
        ".org-canon/docker/pgrx-artifact.Dockerfile",
        "--context",
        ".",
        "--digests",
        str(tmp / "digests"),
        "--metadata-file",
        str(tmp / "meta.json"),
        "--pg",
        "17",
        "--platform",
        "linux/amd64,linux/arm64",
        "--tag",
        "1.2.3-pg17-artifact",
    ]


OUTSPEC = (
    "type=image,name={},push-by-digest=true,name-canonical=true,"
    "push=true,oci-mediatypes=true,rewrite-timestamp=true"
)


def test_a_per_architecture_push(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Labels only, no floor, the raw digest in <digests>/<arch>."""
    harness.env["STUB_META"] = dumps({"containerimage.digest": AMD})
    code = registry.main([*oci_push(harness.tmp)], harness.env, origin=harness.origin)
    out = "".join(capfd.readouterr())
    assert code == 0, out
    calls = harness.docker_calls()
    login: Call = {
        "argv": ["login", "ghcr.io", "-u", ACTOR, "--password-stdin"],
        "stdin": GH_TOKEN,
    }
    assert calls[0] == login
    build: list[str] = [
        "buildx",
        "build",
        "--file",
        "ctx/Dockerfile",
        "--label",
        "org.opencontainers.image.version=1.2.3",
        "--label",
        "org.opencontainers.image.revision=0123456789abcdef",
        "--provenance=false",
        "--sbom=false",
        "--output",
        OUTSPEC.format("ghcr.io/some-owner/some-image"),
        "--metadata-file",
        str(harness.tmp / "meta.json"),
        "ctx",
    ]
    assert calls[1]["argv"] == build
    assert len(calls) == 2
    assert (harness.tmp / "digests" / "amd64").read_text("utf-8") == AMD
    assert out == f"::notice::amd64 pushed as {AMD}\n"


def test_a_pgrx_index_push(harness: Harness, capfd: pytest.CaptureFixture[str]) -> None:
    """Both platforms, labels and index annotations, a JSON record."""
    harness.env["STUB_META"] = dumps({"containerimage.digest": AMD})
    code = registry.main([*pgrx_push(harness.tmp)], harness.env, origin=harness.origin)
    out = "".join(capfd.readouterr())
    assert code == 0, out
    argv = harness.argvs()
    version: list[str] = ["buildx", "version"]
    assert argv[0] == version
    assert argv[1][0] == "login"
    build: list[str] = [
        "buildx",
        "build",
        "--file",
        ".org-canon/docker/pgrx-artifact.Dockerfile",
        "--platform",
        "linux/amd64,linux/arm64",
        "--label",
        "org.opencontainers.image.version=1.2.3",
        "--label",
        "org.opencontainers.image.revision=0123456789abcdef",
        "--annotation",
        "index:org.opencontainers.image.version=1.2.3",
        "--annotation",
        "index:org.opencontainers.image.revision=0123456789abcdef",
        "--provenance=false",
        "--sbom=false",
        "--output",
        OUTSPEC.format("ghcr.io/some-owner/lab_pg"),
        "--metadata-file",
        str(harness.tmp / "meta.json"),
        ".",
    ]
    assert argv[2] == build
    written = (harness.tmp / "digests" / "pg17.json").read_text("utf-8")
    record = decoded.as_object(decoded.load_json(written), "the record")
    want: dict[str, object] = {
        "pg": "17",
        "image": "ghcr.io/some-owner/lab_pg",
        "tag": "1.2.3-pg17-artifact",
        "digest": AMD,
    }
    assert record == want
    assert out == f"::notice::pg17 index {AMD}\n"


def test_no_digest_from_the_push(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """Absent, malformed or missing metadata: each message, no record."""
    rows = [
        dumps({"containerimage.digest": "sha256:abc"}),
        dumps({"containerimage.digest": AMD.upper()}),
        dumps({"image.name": "x"}),
        "not json",
        None,
    ]
    for meta in rows:
        for argv, message in [
            (oci_push(harness.tmp), "no digest from the push"),
            (pgrx_push(harness.tmp), "no index digest from the push"),
        ]:
            (harness.tmp / "meta.json").unlink(missing_ok=True)
            harness.env.pop("STUB_META", None)
            if meta is not None:
                harness.env["STUB_META"] = meta
            code = registry.main([*argv], harness.env, origin=harness.origin)
            out = "".join(capfd.readouterr())
            assert code == 1, out
            assert f"::error::registry: {message}\n" in out
            assert not (harness.tmp / "digests" / "amd64").exists()
            assert not (harness.tmp / "digests" / "pg17.json").exists()


def test_a_failed_build_refuses_on_its_status(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """The build's exit status is not masked by the metadata read."""
    harness.env["STUB_META"] = dumps({"containerimage.digest": AMD})
    harness.env["STUB_BUILD_RC"] = "1"
    code = registry.main([*oci_push(harness.tmp)], harness.env, origin=harness.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert f"::error::registry: {'docker buildx build --file exited 1'}\n" in out
    assert not (harness.tmp / "digests" / "amd64").exists()


class Assemble(Harness):
    """assemble-oci-index.yml: the index, by digest, proven by the registry."""

    def __init__(self, request: pytest.FixtureRequest, tmp_path: Path) -> None:
        """Lay out both per-architecture digests, as the download leaves them."""
        super().__init__(request, tmp_path)
        self.digests = self.tmp / "digests"
        self.digests.mkdir()
        (self.digests / "amd64").write_text(AMD, "utf-8")
        (self.digests / "arm64").write_text(ARM, "utf-8")


@fixture
def assemble_state(request: pytest.FixtureRequest, tmp_path: Path) -> Assemble:
    """Give each row its own Assemble."""
    return Assemble(request, tmp_path)


def test_the_captured_index_goes_up_under_its_own_digest(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """The exact dry-run bytes less the newline, PUT by digest."""
    body = DRY_RUN.read_bytes()[:-1]
    index = "sha256:" + hashlib.sha256(body).hexdigest()
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 0, out
    argv = assemble_state.argvs()
    version: list[str] = ["buildx", "version"]
    assert argv[0] == version
    assert argv[1][0] == "login"
    create: list[str] = [
        "buildx",
        "imagetools",
        "create",
        "--dry-run",
        "--annotation",
        "index:org.opencontainers.image.version=1.2.3",
        "--annotation",
        "index:org.opencontainers.image.revision=0123456789abcdef",
        f"ghcr.io/some-owner/some-image@{AMD}",
        f"ghcr.io/some-owner/some-image@{ARM}",
    ]
    assert argv[2] == create
    token, put = Fake.log
    assert (
        token["path"]
        == "/token?scope=repository:some-owner/some-image:pull,push&service=ghcr.io"
    )
    basic = base64.b64encode(f"{ACTOR}:{GH_TOKEN}".encode()).decode()
    assert token["headers"]["Authorization"] == f"Basic {basic}"
    assert put["path"] == f"/v2/some-owner/some-image/manifests/{index}"
    assert put["body"] == body
    assert put["headers"]["Content-Type"] == OCI_INDEX
    image = "ghcr.io/some-owner/some-image"
    images = [{"image": image, "tag": "1.2.3", "digest": index}]
    assert assemble_state.output.read_text("utf-8") == (
        f"image={image}\n"
        f"digest={index}\n"
        f"registry-subjects=image {index.removeprefix('sha256:')}  {image}\n"
        f"images={json.dumps(images, separators=(',', ':'))}\n"
    )
    assert out == f"::notice::index {index}\n"


def test_a_missing_per_architecture_digest(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """Absent or malformed: refused before login, nothing pushed."""
    message = "a per-architecture digest is missing; refusing to tag a partial image"
    rows = [
        ("arm64", None),
        ("amd64", None),
        ("arm64", ""),
        ("arm64", "sha256:" + "b" * 63),
        ("arm64", "sha256:" + "B" * 64),
        ("arm64", ARM + "\nextra"),
    ]
    for arch, content in rows:
        (assemble_state.digests / "amd64").write_text(AMD, "utf-8")
        (assemble_state.digests / "arm64").write_text(ARM, "utf-8")
        if content is None:
            (assemble_state.digests / arch).unlink()
        else:
            (assemble_state.digests / arch).write_text(content, "utf-8")
        Path(assemble_state.env["STUB_LOG"]).unlink(missing_ok=True)
        code = registry.main(
            ["assemble", "--digests", str(assemble_state.digests), "--tag", "1"],
            assemble_state.env,
            origin=assemble_state.origin,
        )
        out = "".join(capfd.readouterr())
        assert code == 1, out
        assert f"::error::registry: {message}\n" in out
        only: list[list[str]] = [["buildx", "version"]]
        assert assemble_state.argvs() == only
        assert Fake.log == list[Logged]()


def test_a_trailing_newline_on_the_digest_file_is_read_as_cat_read_it(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """`$(cat f)` dropped trailing newlines; so does the helper."""
    (assemble_state.digests / "arm64").write_text(ARM + "\n", "utf-8")
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 0, out


@pytest.mark.parametrize("data", [DRY_RUN.read_bytes()[:-1], b""])
def test_a_dry_run_not_ending_in_a_newline(
    assemble_state: Assemble, data: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """No guessing at the manifest bytes; nothing PUT."""
    assemble_state.dry_run(data)
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        "::error::registry: imagetools --dry-run output does not end"
        " in a newline;"
        " refusing to guess the manifest bytes\n"
    ) in out
    assert Fake.log == list[Logged]()


def test_a_media_type_that_is_not_an_oci_index(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """A Docker list has no annotations field: refused, nothing PUT."""
    captured = DRY_RUN.read_bytes()
    rows = [
        (captured.replace(OCI_INDEX.encode(), DOCKER_LIST.encode()), DOCKER_LIST),
        (b'{"schemaVersion": 2}\n', "no media type"),
        (b"not json\n", "no media type"),
    ]
    for data, shown in rows:
        assemble_state.dry_run(data)
        code = registry.main(
            ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
            assemble_state.env,
            origin=assemble_state.origin,
        )
        out = "".join(capfd.readouterr())
        assert code == 1, out
        assert (
            f"::error::registry: the assembled index is '{shown}',"
            " not an OCI image index\n"
        ) in out
        assert Fake.log == list[Logged]()


def test_a_registry_answering_another_digest_for_the_index(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """The registry stored other bytes: refused, no outputs written."""
    Fake.rewrite = True
    body = DRY_RUN.read_bytes()[:-1]
    index = "sha256:" + hashlib.sha256(body).hexdigest()
    stored = body.replace(b'"schemaVersion": 2', b'"schemaVersion":2')
    answered = "sha256:" + hashlib.sha256(stored).hexdigest()
    assert answered != index
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        f"::error::registry: the registry answered {answered}"
        f" for the index pushed as {index}\n"
    ) in out
    assert assemble_state.output.read_text("utf-8") == ""


def test_a_registry_answering_no_digest(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """An inspect that names no digest reads as `no digest`."""
    assemble_state.env["STUB_NO_DIGEST"] = "1"
    index = "sha256:" + hashlib.sha256(DRY_RUN.read_bytes()[:-1]).hexdigest()
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        "::error::registry: the registry answered no digest"
        f" for the index pushed as {index}\n"
    ) in out
    assert assemble_state.output.read_text("utf-8") == ""


def test_a_failed_put_names_the_request(
    assemble_state: Assemble, capfd: pytest.CaptureFixture[str]
) -> None:
    """No token issued: the PUT answers 401, refused with the URL."""
    Fake.token_body = b'{"token": "wrong"}'
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert re.search(
        r"::error::registry: PUT http://127\.0\.0\.1:\d+/v2/some-owner/some-image"
        r"/manifests/sha256:[0-9a-f]{64} answered 401\n",
        out,
    )


@pytest.mark.parametrize("body", [b"{}", b'{"token": ""}', b"[]", b"html"])
def test_no_token_from_the_endpoint(
    assemble_state: Assemble, body: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """A token endpoint answering no token refuses before the PUT."""
    Fake.log = []
    Fake.token_body = body
    code = registry.main(
        ["assemble", "--digests", str(assemble_state.digests), "--tag", "1.2.3"],
        assemble_state.env,
        origin=assemble_state.origin,
    )
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        f"::error::registry: {'the token endpoint answered no token for some-owner/some-image'}\n"
        in out
    )
    methods = [e["method"] for e in Fake.log]
    want: list[str] = ["GET"]
    assert methods == want


def gated(tag: str = "1.2.3", digest: str | None = None) -> str:
    """Put one manifest in the fake and name it the way attach hands it on.

    Returns:
        tag-images.yml's `images` input for it.

    """
    body = DRY_RUN.read_bytes()[:-1]
    real = "sha256:" + hashlib.sha256(body).hexdigest()
    Fake.manifests["some-owner/some-image", real] = (body, OCI_INDEX)
    return dumps(
        [
            {
                "image": "ghcr.io/some-owner/some-image",
                "tag": tag,
                "digest": digest or real,
                "extra": "ignored",
            }
        ]
    )


def gated_entry(images: str) -> dict[str, object]:
    """Read the first entry of an `images` input.

    Returns:
        Its object, as tag-images.yml's input carries it.

    """
    entries = decoded.as_array(decoded.load_json(images), "images")
    return decoded.as_object(entries[0], "an image")


class Tag(Harness):
    """tag-images.yml: the gated bytes under each tag, proven by a read."""


@fixture
def tag_state(request: pytest.FixtureRequest, tmp_path: Path) -> Tag:
    """Give each row its own Tag."""
    return Tag(request, tmp_path)


def test_the_gated_bytes_go_up_under_the_tag(
    tag_state: Tag, capfd: pytest.CaptureFixture[str]
) -> None:
    """GET by digest, PUT the same bytes and type to the tag."""
    images = gated()
    digest = decoded.as_str(gated_entry(images)["digest"], "digest")
    tag_state.env["UNTRUSTED_IMAGES"] = images
    code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
    out = "".join(capfd.readouterr())
    assert code == 0, out
    token, get, put = Fake.log
    assert token["path"].startswith("/token?")
    assert get["path"] == f"/v2/some-owner/some-image/manifests/{digest}"
    assert get["headers"]["Accept"] == f"{OCI_INDEX}, {DOCKER_LIST}"
    assert put["path"] == "/v2/some-owner/some-image/manifests/1.2.3"
    assert put["body"] == DRY_RUN.read_bytes()[:-1]
    assert put["headers"]["Content-Type"] == OCI_INDEX
    assert out == f"::notice::ghcr.io/some-owner/some-image:1.2.3 -> {digest}\n"


def test_input_that_is_not_an_array_of_triples(
    tag_state: Tag, capfd: pytest.CaptureFixture[str]
) -> None:
    """The whole input is refused before any request."""
    message = "images is not a JSON array of {image, tag, digest}"
    entry = {"image": "ghcr.io/a/b", "tag": "1", "digest": AMD}
    rows = [
        "not json",
        "{}",
        "[]",
        dumps(entry),
        dumps([entry, 1]),
        dumps([{**entry, "digest": None}]),
        dumps([{"image": "ghcr.io/a/b", "tag": "1"}]),
        dumps([{**entry, "tag": 1}]),
        # #1556: nested past the parser's depth, a RecursionError before.
        "[" * 100_000,
    ]
    for images in rows:
        tag_state.env["UNTRUSTED_IMAGES"] = images
        code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
        out = "".join(capfd.readouterr())
        assert code == 1, out
        assert f"::error::registry: {message}\n" in out
        assert Fake.log == list[Logged]()


def test_field_whitelists(tag_state: Tag, capfd: pytest.CaptureFixture[str]) -> None:
    """Each field's whitelist, each message; nothing is requested."""
    ok = {"image": "ghcr.io/a/b", "tag": "1.2.3", "digest": AMD}
    rows = [
        ({"image": "docker.io/a/b"}, "not a lowercase ghcr.io reference"),
        ({"image": "ghcr.io/A/b"}, "not a lowercase ghcr.io reference"),
        ({"image": "ghcr.io/"}, "not a lowercase ghcr.io reference"),
        ({"image": "ghcr.io/a/b:1"}, "not a lowercase ghcr.io reference"),
        ({"image": "ghcr.io/a/b\n"}, "not a lowercase ghcr.io reference"),
        ({"tag": ""}, "implausible tag"),
        ({"tag": "1 2"}, "implausible tag"),
        ({"tag": "a/b"}, "implausible tag"),
        ({"tag": "a:b"}, "implausible tag"),
        (
            {"digest": "sha256:" + "A" * 64},
            "digest is not sha256:<64 lowercase hex>",
        ),
        (
            {"digest": "sha512:" + "a" * 64},
            "digest is not sha256:<64 lowercase hex>",
        ),
        ({"digest": AMD + "a"}, "digest is not sha256:<64 lowercase hex>"),
    ]
    for change, message in rows:
        tag_state.env["UNTRUSTED_IMAGES"] = dumps([{**ok, **change}])
        code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
        out = "".join(capfd.readouterr())
        assert code == 1, out
        assert f"::error::registry: {message}\n" in out
        assert Fake.log == list[Logged]()


def test_a_bad_later_entry_refuses_before_the_first_is_tagged(
    tag_state: Tag, capfd: pytest.CaptureFixture[str]
) -> None:
    """Validation is whole-input: no tag lands from a refused input."""
    first = gated_entry(gated())
    bad = {**first, "tag": "1 2"}
    tag_state.env["UNTRUSTED_IMAGES"] = dumps([first, bad])
    code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert f"::error::registry: {'implausible tag'}\n" in out
    assert Fake.log == list[Logged]()


def test_a_tag_that_resolves_to_another_digest(
    tag_state: Tag, capfd: pytest.CaptureFixture[str]
) -> None:
    """The registry stored other bytes under the tag: refused."""
    images = gated()
    digest = decoded.as_str(gated_entry(images)["digest"], "digest")
    Fake.rewrite = True
    stored = DRY_RUN.read_bytes()[:-1].replace(
        b'"schemaVersion": 2', b'"schemaVersion":2'
    )
    resolved = "sha256:" + hashlib.sha256(stored).hexdigest()
    tag_state.env["UNTRUSTED_IMAGES"] = images
    code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert (
        f"::error::registry: ghcr.io/some-owner/some-image:1.2.3 resolves to"
        f" {resolved},"
        f" gated {digest}\n"
    ) in out
    assert "::notice::" not in out


def test_a_digest_the_registry_does_not_hold(
    tag_state: Tag, capfd: pytest.CaptureFixture[str]
) -> None:
    """A 404 on the GET refuses with the request, nothing PUT."""
    tag_state.env["UNTRUSTED_IMAGES"] = gated(digest=ARM)
    code = registry.main(["tag"], tag_state.env, origin=tag_state.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert re.search(
        rf"::error::registry: GET http://\S+/manifests/{ARM} answered 404\n", out
    )
    methods = [e["method"] for e in Fake.log]
    want: list[str] = ["GET", "GET"]
    assert methods == want


def test_registry_url_in_the_environment_changes_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A GITHUB_ENV write cannot send the token elsewhere (#1382 review).

    The production entry (no `origin`) is run with `REGISTRY_URL` set
    to another host; `urlopen` is replaced, so nothing leaves the
    machine, and the one request it is asked for goes to ghcr.io.
    """
    harness.env["REGISTRY_URL"] = "https://attacker.example"
    harness.env["UNTRUSTED_IMAGES"] = dumps(
        [{"image": "ghcr.io/a/b", "tag": "1.2.3", "digest": AMD}]
    )
    opened: list[str] = []

    def refuse(req: urllib.request.Request, **_: object) -> None:
        opened.append(req.full_url)
        reason = "no network in this row"
        raise urllib.error.URLError(reason)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    code = registry.main(["tag"], harness.env)
    out, out = capfd.readouterr()
    assert code == 1
    want: list[str] = [
        "https://ghcr.io/token?scope=repository:a/b:pull,push&service=ghcr.io"
    ]
    assert opened == want
    assert "attacker" not in out


@pytest.mark.usefixtures("harness")
def test_an_answer_that_is_not_a_response_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What answered is not an HTTP response: refused by name (#1682)."""
    url = "https://ghcr.io/v2/a/b/manifests/1"
    with pytest.raises(registry.RefusedError) as caught:
        monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: None)
        registry.Registry.request("GET", url, {})
    assert f"{url} answered NoneType, not an HTTP response" == str(caught.value)


def test_an_unset_input_is_named(
    harness: Harness, capfd: pytest.CaptureFixture[str]
) -> None:
    """No UNTRUSTED_IMAGES: the variable is named, exit 1."""
    code = registry.main(["tag"], harness.env, origin=harness.origin)
    out = "".join(capfd.readouterr())
    assert code == 1, out
    assert f"::error::registry: {'UNTRUSTED_IMAGES is not set'}\n" in out

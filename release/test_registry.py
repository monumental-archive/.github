#!/usr/bin/env python3
"""Run `release/registry.py` against a stub `docker` and a fake registry.

#1382 moved four workflows' registry plumbing into `release/registry.py`
(the buildx floor, flags from the facts, push by digest, the index's
assembly and upload, tagging a gated digest). None of the four blocks
had a test. These rows run the helper in-process, as the steps call it,
with a stub `docker` first on PATH and an `http.server` on 127.0.0.1
standing in for ghcr.io (`REGISTRY_URL`). The fake refuses a route it
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

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from types import ModuleType

HERE = Path(__file__).resolve().parent
TESTDATA = HERE / "testdata"
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
FACTS = json.dumps({
    "org.opencontainers.image.version": "1.2.3",
    "org.opencontainers.image.revision": "0123456789abcdef",
})

DOCKER_STUB = r'''#!{python} -I -S
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
    url = env["REGISTRY_URL"] + "/v2/" + name + "/manifests/" + reference
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


def load_helper() -> ModuleType:
    """Import release/registry.py by path (its name is not a module's).

    Returns:
        The helper module.

    """
    spec = importlib.util.spec_from_file_location("registry", HERE / "registry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = load_helper()


class Fake(BaseHTTPRequestHandler):
    """ghcr.io's token endpoint and manifest routes, nothing else."""

    manifests: ClassVar[dict[tuple[str, str], tuple[bytes, str]]] = {}
    log: ClassVar[list[dict]] = []
    token_body: ClassVar[bytes] = json.dumps({"token": TOKEN}).encode()
    rewrite: ClassVar[bool] = False

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
        m = re.fullmatch(r"/v2/(.+)/manifests/([^/]+)", self.path)
        return (m[1], m[2]) if m else None

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
            Fake.log.append({
                "method": "GET",
                "path": self.path,
                "headers": dict(self.headers),
            })
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
        Fake.log.append({
            "method": "PUT",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
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


class Harness(unittest.TestCase):
    """A fresh fake, a stub docker on PATH, and the step's environment."""

    def setUp(self) -> None:
        """Start the fake and lay out the stub."""
        Fake.manifests = {}
        Fake.log = []
        Fake.token_body = json.dumps({"token": TOKEN}).encode()
        Fake.rewrite = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        ).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "docker"
        stub.write_text(DOCKER_STUB.format(python=sys.executable), "utf-8")
        stub.chmod(0o755)
        self.output = self.tmp / "github-output"
        self.output.write_text("", "utf-8")
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "REGISTRY_URL": f"http://127.0.0.1:{self.server.server_port}",
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

    def run_helper(self, *argv: str) -> tuple[int, str]:
        """Run the helper as a step does.

        Returns:
            Its exit status and what it printed.

        """
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = registry.main(list(argv), self.env)
        return code, out.getvalue()

    def docker_calls(self) -> list[dict]:
        """Read the stub's log.

        Returns:
            One {argv, stdin} per docker call, in order.

        """
        log = Path(self.env["STUB_LOG"])
        if not log.exists():
            return []
        return [json.loads(ln) for ln in log.read_text("utf-8").splitlines()]

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

    def refused(self, message: str, *argv: str) -> None:
        """Assert the helper refuses with exactly `message`."""
        code, out = self.run_helper(*argv)
        self.assertEqual(code, 1, out)
        self.assertIn(f"::error::{message}\n", out)


class BuildxFloor(Harness):
    """The floor (docker/buildx#1965): below 0.12 annotations vanish."""

    def test_the_captured_version_passes(self) -> None:
        """Buildx v0.36.1-desktop.1, as captured, is above the floor."""
        self.assertIsNone(registry.buildx_floor(VERSION.read_text("utf-8")))

    def test_the_floor_itself_passes(self) -> None:
        """0.12.0 is the floor, not below it."""
        self.assertIsNone(
            registry.buildx_floor("github.com/docker/buildx v0.12.0 abc\n")
        )

    def test_refusals(self) -> None:
        """Below the floor, or no version: the old step's exact words."""
        rows = [
            ("github.com/docker/buildx v0.11.2 abc\n", "buildx 0.11"),
            ("github.com/docker/buildx v0.9.1 abc\n", "buildx 0.9"),
            ("github.com/docker/buildx dev abc\n", "buildx unknown"),
            ("", "buildx unknown"),
        ]
        for text, shown in rows:
            with self.subTest(text=text):
                with self.assertRaises(registry.RefusedError) as cm:
                    registry.buildx_floor(text)
                self.assertEqual(
                    str(cm.exception),
                    f"{shown} < 0.12: index annotations would be dropped silently",
                )

    def test_assemble_refuses_below_the_floor_before_any_push(self) -> None:
        """The assemble step asserts the floor first."""
        self.version("github.com/docker/buildx v0.11.2 abc\n")
        self.refused(
            "buildx 0.11 < 0.12: index annotations would be dropped silently",
            "assemble",
            "--digests",
            str(self.tmp),
            "--tag",
            "1.2.3",
        )
        self.assertEqual(
            [c["argv"] for c in self.docker_calls()], [["buildx", "version"]]
        )
        self.assertEqual(Fake.log, [])

    def test_a_pgrx_push_refuses_below_the_floor(self) -> None:
        """The pgrx index carries annotations, so its push asserts the floor."""
        self.version("github.com/docker/buildx v0.11.2 abc\n")
        self.refused(
            "buildx 0.11 < 0.12: index annotations would be dropped silently",
            *pgrx_push(self.tmp),
        )
        self.assertEqual(
            [c["argv"] for c in self.docker_calls()], [["buildx", "version"]]
        )


class Facts(Harness):
    """One map, rendered the way jq's interpolation rendered it."""

    def test_pairs_keep_the_map_order(self) -> None:
        """Labels and annotations follow the map, one flag per fact."""
        self.assertEqual(
            registry.fact_flags(FACTS, labels=True, index=True),
            [
                "--label",
                "org.opencontainers.image.version=1.2.3",
                "--label",
                "org.opencontainers.image.revision=0123456789abcdef",
                "--annotation",
                "index:org.opencontainers.image.version=1.2.3",
                "--annotation",
                "index:org.opencontainers.image.revision=0123456789abcdef",
            ],
        )

    def test_non_string_values_render_as_compact_json(self) -> None:
        r"""A number or an object reads as jq's `\(.value)` wrote it."""
        self.assertEqual(
            registry.facts_pairs('{"a": 1, "b": {"c": [1, "x"]}, "d": "e f"}'),
            ["a=1", 'b={"c":[1,"x"]}', "d=e f"],
        )

    def test_facts_that_are_not_an_object_refuse(self) -> None:
        """Jq died on these; the helper names what it was given."""
        for facts in ["[]", "not json", '"x"']:
            with self.subTest(facts=facts):
                with self.assertRaises(registry.RefusedError) as cm:
                    registry.facts_pairs(facts)
                self.assertEqual(str(cm.exception), "facts is not a JSON object")


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


class Push(Harness):
    """Push by digest: the build's argv, the digest check, the record."""

    def test_a_per_architecture_push(self) -> None:
        """Labels only, no floor, the raw digest in <digests>/<arch>."""
        self.env["STUB_META"] = json.dumps({"containerimage.digest": AMD})
        code, out = self.run_helper(*oci_push(self.tmp))
        self.assertEqual(code, 0, out)
        calls = self.docker_calls()
        self.assertEqual(
            calls[0],
            {
                "argv": ["login", "ghcr.io", "-u", ACTOR, "--password-stdin"],
                "stdin": GH_TOKEN,
            },
        )
        self.assertEqual(
            calls[1]["argv"],
            [
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
                str(self.tmp / "meta.json"),
                "ctx",
            ],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.tmp / "digests" / "amd64").read_text("utf-8"), AMD)
        self.assertEqual(out, f"::notice::amd64 pushed as {AMD}\n")

    def test_a_pgrx_index_push(self) -> None:
        """Both platforms, labels and index annotations, a JSON record."""
        self.env["STUB_META"] = json.dumps({"containerimage.digest": AMD})
        code, out = self.run_helper(*pgrx_push(self.tmp))
        self.assertEqual(code, 0, out)
        argv = [c["argv"] for c in self.docker_calls()]
        self.assertEqual(argv[0], ["buildx", "version"])
        self.assertEqual(argv[1][0], "login")
        self.assertEqual(
            argv[2],
            [
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
                str(self.tmp / "meta.json"),
                ".",
            ],
        )
        record = json.loads((self.tmp / "digests" / "pg17.json").read_text("utf-8"))
        self.assertEqual(
            record,
            {
                "pg": "17",
                "image": "ghcr.io/some-owner/lab_pg",
                "tag": "1.2.3-pg17-artifact",
                "digest": AMD,
            },
        )
        self.assertEqual(out, f"::notice::pg17 index {AMD}\n")

    def test_no_digest_from_the_push(self) -> None:
        """Absent, malformed or missing metadata: each message, no record."""
        rows = [
            json.dumps({"containerimage.digest": "sha256:abc"}),
            json.dumps({"containerimage.digest": AMD.upper()}),
            json.dumps({"image.name": "x"}),
            "not json",
            None,
        ]
        for meta in rows:
            for argv, message in [
                (oci_push(self.tmp), "no digest from the push"),
                (pgrx_push(self.tmp), "no index digest from the push"),
            ]:
                with self.subTest(meta=meta, message=message):
                    (self.tmp / "meta.json").unlink(missing_ok=True)
                    self.env.pop("STUB_META", None)
                    if meta is not None:
                        self.env["STUB_META"] = meta
                    self.refused(message, *argv)
                    self.assertFalse((self.tmp / "digests" / "amd64").exists())
                    self.assertFalse((self.tmp / "digests" / "pg17.json").exists())

    def test_a_failed_build_refuses_on_its_status(self) -> None:
        """The build's exit status is not masked by the metadata read."""
        self.env["STUB_META"] = json.dumps({"containerimage.digest": AMD})
        self.env["STUB_BUILD_RC"] = "1"
        self.refused("docker buildx build --file exited 1", *oci_push(self.tmp))
        self.assertFalse((self.tmp / "digests" / "amd64").exists())


class Assemble(Harness):
    """assemble-oci-index.yml: the index, by digest, proven by the registry."""

    def setUp(self) -> None:
        """Lay out both per-architecture digests, as the download leaves them."""
        super().setUp()
        self.digests = self.tmp / "digests"
        self.digests.mkdir()
        (self.digests / "amd64").write_text(AMD, "utf-8")
        (self.digests / "arm64").write_text(ARM, "utf-8")

    def assemble(self) -> tuple[int, str]:
        """Run the assemble step.

        Returns:
            Its exit status and output.

        """
        return self.run_helper(
            "assemble", "--digests", str(self.digests), "--tag", "1.2.3"
        )

    def test_the_captured_index_goes_up_under_its_own_digest(self) -> None:
        """The exact dry-run bytes less the newline, PUT by digest."""
        body = DRY_RUN.read_bytes()[:-1]
        index = "sha256:" + hashlib.sha256(body).hexdigest()
        code, out = self.assemble()
        self.assertEqual(code, 0, out)
        argv = [c["argv"] for c in self.docker_calls()]
        self.assertEqual(argv[0], ["buildx", "version"])
        self.assertEqual(argv[1][0], "login")
        self.assertEqual(
            argv[2],
            [
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
            ],
        )
        token, put = Fake.log
        self.assertEqual(
            token["path"],
            "/token?scope=repository:some-owner/some-image:pull,push&service=ghcr.io",
        )
        basic = base64.b64encode(f"{ACTOR}:{GH_TOKEN}".encode()).decode()
        self.assertEqual(token["headers"]["Authorization"], f"Basic {basic}")
        self.assertEqual(put["path"], f"/v2/some-owner/some-image/manifests/{index}")
        self.assertEqual(put["body"], body)
        self.assertEqual(put["headers"]["Content-Type"], OCI_INDEX)
        image = "ghcr.io/some-owner/some-image"
        images = [{"image": image, "tag": "1.2.3", "digest": index}]
        self.assertEqual(
            self.output.read_text("utf-8"),
            f"image={image}\n"
            f"digest={index}\n"
            f"registry-subjects=image {index.removeprefix('sha256:')}  {image}\n"
            f"images={json.dumps(images, separators=(',', ':'))}\n",
        )
        self.assertEqual(out, f"::notice::index {index}\n")

    def test_a_missing_per_architecture_digest(self) -> None:
        """Absent or malformed: refused before login, nothing pushed."""
        message = (
            "a per-architecture digest is missing; refusing to tag a partial image"
        )
        rows = [
            ("arm64", None),
            ("amd64", None),
            ("arm64", ""),
            ("arm64", "sha256:" + "b" * 63),
            ("arm64", "sha256:" + "B" * 64),
            ("arm64", ARM + "\nextra"),
        ]
        for arch, content in rows:
            with self.subTest(arch=arch, content=content):
                (self.digests / "amd64").write_text(AMD, "utf-8")
                (self.digests / "arm64").write_text(ARM, "utf-8")
                if content is None:
                    (self.digests / arch).unlink()
                else:
                    (self.digests / arch).write_text(content, "utf-8")
                Path(self.env["STUB_LOG"]).unlink(missing_ok=True)
                self.refused(
                    message, "assemble", "--digests", str(self.digests), "--tag", "1"
                )
                self.assertEqual(
                    [c["argv"] for c in self.docker_calls()], [["buildx", "version"]]
                )
                self.assertEqual(Fake.log, [])

    def test_a_trailing_newline_on_the_digest_file_is_read_as_cat_read_it(self) -> None:
        """`$(cat f)` dropped trailing newlines; so does the helper."""
        (self.digests / "arm64").write_text(ARM + "\n", "utf-8")
        code, out = self.assemble()
        self.assertEqual(code, 0, out)

    def test_a_dry_run_not_ending_in_a_newline(self) -> None:
        """No guessing at the manifest bytes; nothing PUT."""
        for data in [DRY_RUN.read_bytes()[:-1], b""]:
            with self.subTest(size=len(data)):
                self.dry_run(data)
                code, out = self.assemble()
                self.assertEqual(code, 1, out)
                self.assertIn(
                    "::error::imagetools --dry-run output does not end in a newline;"
                    " refusing to guess the manifest bytes\n",
                    out,
                )
                self.assertEqual(Fake.log, [])

    def test_a_media_type_that_is_not_an_oci_index(self) -> None:
        """A Docker list has no annotations field: refused, nothing PUT."""
        captured = DRY_RUN.read_bytes()
        rows = [
            (captured.replace(OCI_INDEX.encode(), DOCKER_LIST.encode()), DOCKER_LIST),
            (b'{"schemaVersion": 2}\n', "no media type"),
            (b"not json\n", "no media type"),
        ]
        for data, shown in rows:
            with self.subTest(shown=shown):
                self.dry_run(data)
                code, out = self.assemble()
                self.assertEqual(code, 1, out)
                self.assertIn(
                    f"::error::the assembled index is '{shown}',"
                    " not an OCI image index\n",
                    out,
                )
                self.assertEqual(Fake.log, [])

    def test_a_registry_answering_another_digest_for_the_index(self) -> None:
        """The registry stored other bytes: refused, no outputs written."""
        Fake.rewrite = True
        body = DRY_RUN.read_bytes()[:-1]
        index = "sha256:" + hashlib.sha256(body).hexdigest()
        stored = body.replace(b'"schemaVersion": 2', b'"schemaVersion":2')
        answered = "sha256:" + hashlib.sha256(stored).hexdigest()
        self.assertNotEqual(answered, index)
        code, out = self.assemble()
        self.assertEqual(code, 1, out)
        self.assertIn(
            f"::error::the registry answered {answered}"
            f" for the index pushed as {index}\n",
            out,
        )
        self.assertEqual(self.output.read_text("utf-8"), "")

    def test_a_registry_answering_no_digest(self) -> None:
        """An inspect that names no digest reads as `no digest`."""
        self.env["STUB_NO_DIGEST"] = "1"
        index = "sha256:" + hashlib.sha256(DRY_RUN.read_bytes()[:-1]).hexdigest()
        code, out = self.assemble()
        self.assertEqual(code, 1, out)
        self.assertIn(
            "::error::the registry answered no digest"
            f" for the index pushed as {index}\n",
            out,
        )
        self.assertEqual(self.output.read_text("utf-8"), "")

    def test_a_failed_put_names_the_request(self) -> None:
        """No token issued: the PUT answers 401, refused with the URL."""
        Fake.token_body = b'{"token": "wrong"}'
        code, out = self.assemble()
        self.assertEqual(code, 1, out)
        self.assertRegex(
            out,
            r"::error::PUT http://127\.0\.0\.1:\d+/v2/some-owner/some-image"
            r"/manifests/sha256:[0-9a-f]{64} answered 401\n",
        )

    def test_no_token_from_the_endpoint(self) -> None:
        """A token endpoint answering no token refuses before the PUT."""
        for body in [b"{}", b'{"token": ""}', b"[]", b"html"]:
            with self.subTest(body=body):
                Fake.log = []
                Fake.token_body = body
                self.refused(
                    "the token endpoint answered no token for some-owner/some-image",
                    "assemble",
                    "--digests",
                    str(self.digests),
                    "--tag",
                    "1.2.3",
                )
                self.assertEqual([e["method"] for e in Fake.log], ["GET"])


def gated(tag: str = "1.2.3", digest: str | None = None) -> str:
    """Put one manifest in the fake and name it the way attach hands it on.

    Returns:
        tag-images.yml's `images` input for it.

    """
    body = DRY_RUN.read_bytes()[:-1]
    real = "sha256:" + hashlib.sha256(body).hexdigest()
    Fake.manifests["some-owner/some-image", real] = (body, OCI_INDEX)
    return json.dumps([
        {
            "image": "ghcr.io/some-owner/some-image",
            "tag": tag,
            "digest": digest or real,
            "extra": "ignored",
        }
    ])


class Tag(Harness):
    """tag-images.yml: the gated bytes under each tag, proven by a read."""

    def tag(self, images: str) -> tuple[int, str]:
        """Run the tag step on `images`.

        Returns:
            Its exit status and output.

        """
        self.env["UNTRUSTED_IMAGES"] = images
        return self.run_helper("tag")

    def test_the_gated_bytes_go_up_under_the_tag(self) -> None:
        """GET by digest, PUT the same bytes and type to the tag."""
        images = gated()
        digest = json.loads(images)[0]["digest"]
        code, out = self.tag(images)
        self.assertEqual(code, 0, out)
        token, get, put = Fake.log
        self.assertTrue(token["path"].startswith("/token?"))
        self.assertEqual(get["path"], f"/v2/some-owner/some-image/manifests/{digest}")
        self.assertEqual(get["headers"]["Accept"], f"{OCI_INDEX}, {DOCKER_LIST}")
        self.assertEqual(put["path"], "/v2/some-owner/some-image/manifests/1.2.3")
        self.assertEqual(put["body"], DRY_RUN.read_bytes()[:-1])
        self.assertEqual(put["headers"]["Content-Type"], OCI_INDEX)
        self.assertEqual(
            out, f"::notice::ghcr.io/some-owner/some-image:1.2.3 -> {digest}\n"
        )

    def test_input_that_is_not_an_array_of_triples(self) -> None:
        """The whole input is refused before any request."""
        message = "images is not a JSON array of {image, tag, digest}"
        entry = {"image": "ghcr.io/a/b", "tag": "1", "digest": AMD}
        rows = [
            "not json",
            "{}",
            "[]",
            json.dumps(entry),
            json.dumps([entry, 1]),
            json.dumps([{**entry, "digest": None}]),
            json.dumps([{"image": "ghcr.io/a/b", "tag": "1"}]),
            json.dumps([{**entry, "tag": 1}]),
        ]
        for images in rows:
            with self.subTest(images=images):
                self.env["UNTRUSTED_IMAGES"] = images
                self.refused(message, "tag")
                self.assertEqual(Fake.log, [])

    def test_field_whitelists(self) -> None:
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
            with self.subTest(change=change):
                self.env["UNTRUSTED_IMAGES"] = json.dumps([{**ok, **change}])
                self.refused(message, "tag")
                self.assertEqual(Fake.log, [])

    def test_a_bad_later_entry_refuses_before_the_first_is_tagged(self) -> None:
        """Validation is whole-input: no tag lands from a refused input."""
        first = json.loads(gated())[0]
        bad = {**first, "tag": "1 2"}
        self.env["UNTRUSTED_IMAGES"] = json.dumps([first, bad])
        self.refused("implausible tag", "tag")
        self.assertEqual(Fake.log, [])

    def test_a_tag_that_resolves_to_another_digest(self) -> None:
        """The registry stored other bytes under the tag: refused."""
        images = gated()
        digest = json.loads(images)[0]["digest"]
        Fake.rewrite = True
        stored = DRY_RUN.read_bytes()[:-1].replace(
            b'"schemaVersion": 2', b'"schemaVersion":2'
        )
        resolved = "sha256:" + hashlib.sha256(stored).hexdigest()
        code, out = self.tag(images)
        self.assertEqual(code, 1, out)
        self.assertIn(
            f"::error::ghcr.io/some-owner/some-image:1.2.3 resolves to {resolved},"
            f" gated {digest}\n",
            out,
        )
        self.assertNotIn("::notice::", out)

    def test_a_digest_the_registry_does_not_hold(self) -> None:
        """A 404 on the GET refuses with the request, nothing PUT."""
        code, out = self.tag(gated(digest=ARM))
        self.assertEqual(code, 1, out)
        self.assertRegex(
            out, rf"::error::GET http://\S+/manifests/{ARM} answered 404\n"
        )
        self.assertEqual([e["method"] for e in Fake.log], ["GET", "GET"])


class Environment(Harness):
    """A step that forgot an input names it."""

    def test_an_unset_input_is_named(self) -> None:
        """No UNTRUSTED_IMAGES: the variable is named, exit 1."""
        self.refused("UNTRUSTED_IMAGES is not set", "tag")


if __name__ == "__main__":
    unittest.main()

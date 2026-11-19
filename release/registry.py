#!/usr/bin/env python3
"""Push, assemble and tag release images on the registry, by digest.

One helper for the registry plumbing four workflows wrote out in bash
(#1382): the buildx floor, labels and index annotations from the image
facts, a push by digest, the index's assembly and upload, and tagging a
gated digest. Each operation is a function here and a subcommand below;
`release/test_registry.py` runs them against a stub `docker` and a
local fake registry.

- `push` (build-oci-image.yml, one leg per architecture; and
  build-pgrx-images.yml, one leg per Postgres major, both platforms in
  one build) builds and pushes untagged, then records the digest.
- `assemble` (assemble-oci-index.yml) folds the per-architecture
  digests into one OCI index and PUTs it under its own digest.
- `tag` (tag-images.yml) puts the gated manifest bytes under each tag.

INPUTS. From the environment, as the steps set them: `GH_TOKEN` (sent
to `docker login` on stdin and to the token endpoint in a Basic
Authorization header, never in a URL, never printed), `GITHUB_ACTOR`,
`GITHUB_REPOSITORY`, `GITHUB_OUTPUT`, `FACTS` (the resolved facts map),
`UNTRUSTED_IMAGES` (tag's input). Image names come from the workflow:
no organisation or repository is named here.

The registry's HTTP origin is never read from the environment: an
earlier step that can write GITHUB_ENV would otherwise send the
`packages: write` token to a host of its choosing. `main` takes it as
a keyword argument, fixed to `ORIGIN` on the production path; only the
tests pass their fake's.

stdlib only (`urllib`, `json`, `hashlib`); `docker buildx` is called as
a subprocess.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re

# ruff: ignore[suspicious-subprocess-import] - docker is the one command run
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import decoded
import typed_std
import workflow_command

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# The one registry the org publishes images to. A host, not an owner:
# every name under it comes from the calling workflow.
REGISTRY = "ghcr.io"
ORIGIN = f"https://{REGISTRY}"

# imagetools create silently ignored --annotation before buildx 0.12
# (docker/buildx#1965), and so did a build's index: annotations.
BUILDX_FLOOR = (0, 12)

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
INDEX_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    " application/vnd.docker.distribution.manifest.list.v2+json"
)

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_REF = re.compile(r"ghcr\.io/[a-z0-9._/-]+")
TAG = re.compile(r"[A-Za-z0-9._-]+")
# The fields tag-images.yml's input holds per image.
KEYS = ("image", "tag", "digest")

# The determinism set (#118), measured in the lab's first full-width gate
# run. --provenance=false and --sbom=false: buildx otherwise embeds a
# per-build attestation manifest carrying wall-clock metadata, so two
# identical builds NEVER share a digest; the org's provenance is its own
# signed evidence, never the embedded blob. rewrite-timestamp=true clamps
# layer mtimes to SOURCE_DATE_EPOCH (inherited from the step's env).
#
# push-by-digest: no tag. A tag would make an unproven image pullable by
# name before the repro gate has compared it (tag-images.yml applies the
# tags afterwards, #1280).
#
# oci-mediatypes=true is load-bearing: the exporter default is false, a
# Docker-mediatype manifest makes imagetools create assemble a Docker
# manifest list, and that format has no annotations field, so buildx
# drops index annotations SILENTLY (docker/buildx#1965). The downstream
# assert proves the published index is OCI because this failure looks
# exactly like success.
DETERMINISM = ["--provenance=false", "--sbom=false"]
OUTPUT = (
    "type=image,name={image},push-by-digest=true,name-canonical=true,"
    "push=true,oci-mediatypes=true,rewrite-timestamp=true"
)


class RefusedError(Exception):
    """A refusal: printed as one error workflow command, exit 1."""


class Args(argparse.Namespace):
    """The flags every subcommand's parser may set, typed as parsed."""

    command: str
    image: str
    file: str
    context: str
    digests: str
    metadata_file: str
    arch: str | None
    pg: str | None
    platform: str | None
    tag: str | None
    run: Callable[[Registry, Args], int]


class Registry:
    """The registry's HTTP API and the `docker` that talks to it."""

    def __init__(self, env: Mapping[str, str], origin: str = ORIGIN) -> None:
        """Take the step's env for credentials; the origin never comes from it."""
        self.env = env
        self.origin = origin

    def need(self, name: str) -> str:
        """Read one variable of the step's env.

        Returns:
            Its value.

        Raises:
            RefusedError: it is not set.

        """
        value = self.env.get(name)
        if value is None:
            msg = f"{name} is not set"
            raise RefusedError(msg)
        return value

    def docker(
        self, *args: str, stdin: bytes | None = None, capture: bool = True
    ) -> bytes:
        """Run `docker` with the step's env; its log goes to the step's.

        `capture=False` leaves stdout on the step's log too (a build).

        Returns:
            What the command printed on stdout, b"" when not captured.

        Raises:
            RefusedError: the command exited non-zero.

        """
        # ruff: ignore[subprocess-without-shell-equals-true] - argv, no shell
        done = subprocess.run(
            # ruff: ignore[start-process-with-partial-path] - docker from the runner's PATH
            ["docker", *args],
            input=stdin,
            stdout=subprocess.PIPE if capture else None,
            env=dict(self.env),
            check=False,
        )
        if done.returncode != 0:
            msg = f"docker {' '.join(args[:3])} exited {done.returncode}"
            raise RefusedError(msg)
        return done.stdout or b""

    def login(self) -> None:
        """Log `docker` in to the registry with the Actions token."""
        self.docker(
            "login",
            REGISTRY,
            "-u",
            self.need("GITHUB_ACTOR"),
            "--password-stdin",
            stdin=self.need("GH_TOKEN").encode(),
        )

    @staticmethod
    def request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[bytes, str]:
        """Make one request; anything but 2xx refuses, as `curl -f` did.

        Returns:
            The body and the response's Content-Type.

        Raises:
            RefusedError: no answer, or an answer that is not 2xx.

        """
        # ruff: ignore[suspicious-url-open-usage] - https, or the tests' fake
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            # ruff: ignore[suspicious-url-open-usage]
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            e.close()
            msg = f"{method} {url} answered {e.code}"
            raise RefusedError(msg) from None
        except urllib.error.URLError as e:
            msg = f"{method} {url}: {e.reason}"
            raise RefusedError(msg) from None

    def token(self, name: str) -> str:
        """Exchange the Actions token for a pull+push token on one package.

        ghcr's token endpoint accepts the Actions token as the password
        for a pull+push scope on a package the job may write.

        Returns:
            The bearer token.

        Raises:
            RefusedError: the endpoint answered no token.

        """
        basic = f"{self.need('GITHUB_ACTOR')}:{self.need('GH_TOKEN')}"
        url = (
            f"{self.origin}/token?scope=repository:{name}:pull,push&service={REGISTRY}"
        )
        body, _ = self.request(
            "GET",
            url,
            {"Authorization": "Basic " + base64.b64encode(basic.encode()).decode()},
        )
        token = field(body, "token")
        if not isinstance(token, str) or not token:
            msg = f"the token endpoint answered no token for {name}"
            raise RefusedError(msg)
        return token

    def manifest_url(self, name: str, reference: str) -> str:
        """Name a manifest by tag or digest.

        Returns:
            The manifest's URL.

        """
        return f"{self.origin}/v2/{name}/manifests/{reference}"

    def resolve(self, ref: str) -> str:
        """Ask the registry which digest a reference resolves to.

        Returns:
            The digest buildx reads back, "" when it names none.

        """
        out = self.docker(
            "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"
        )
        digest = field(out, "digest")
        return digest if isinstance(digest, str) else ""


def field(source: str | bytes, key: str) -> object:
    """Read one field of a JSON object, or None where there is none.

    Returns:
        The field's value; None when the text is not a JSON object or
        holds no such field.

    """
    try:
        doc = decoded.load_json(source)
    except decoded.UndecodableError:
        return None
    return doc.get(key) if decoded.is_object(doc) else None


def name_of(image: str) -> str:
    """Strip the registry host off a fully-qualified image.

    Returns:
        The repository path the registry's API names.

    """
    return image.removeprefix(f"{REGISTRY}/")


def buildx_floor(version_output: str) -> None:
    """Refuse a buildx that would drop index annotations silently.

    `version_output` is `docker buildx version`, whose first line names
    the release (`github.com/docker/buildx v0.36.0 <commit>`).

    Raises:
        RefusedError: buildx below the floor, or no version to read.

    """
    line = version_output.splitlines()[0] if version_output else ""
    found = re.search(r"(\d+)\.(\d+)\.\d+", line)
    have = (
        (int(typed_std.group(found, 1) or "0"), int(typed_std.group(found, 2) or "0"))
        if found
        else None
    )
    if have is None or have < BUILDX_FLOOR:
        shown = f"{have[0]}.{have[1]}" if have else "unknown"
        msg = f"buildx {shown} < 0.12: index annotations would be dropped silently"
        raise RefusedError(msg)


def facts_pairs(facts: str) -> list[str]:
    """Render the facts map as `key=value` pairs, in the map's order.

    The key set exists once, in the resolver: nothing here names a key.
    A value that is not a string is written as compact JSON, as jq's
    string interpolation wrote it.

    Returns:
        One `key=value` per fact.

    Raises:
        RefusedError: the facts are not a JSON object.

    """
    try:
        parsed = decoded.load_json(facts)
    except decoded.UndecodableError:
        parsed = None
    if not decoded.is_object(parsed):
        msg = "facts is not a JSON object"
        raise RefusedError(msg)
    return [
        f"{k}={v if isinstance(v, str) else json.dumps(v, separators=(',', ':'))}"
        for k, v in parsed.items()
    ]


def fact_flags(facts: str, *, labels: bool, index: bool) -> list[str]:
    """Turn the facts into buildx flags: config labels, index annotations.

    One map, generated the same way at every surface: a label changes the
    config digest, so the per-architecture images and the index carry the
    same set.

    Returns:
        `--label k=v` and/or `--annotation index:k=v`, per fact.

    """
    out: list[str] = []
    pairs = facts_pairs(facts)
    if labels:
        for kv in pairs:
            out += ["--label", kv]
    if index:
        for kv in pairs:
            out += ["--annotation", f"index:{kv}"]
    return out


def read_metadata_digest(path: Path, refusal: str) -> str:
    """Read the pushed digest from buildx's metadata file.

    Returns:
        The digest.

    Raises:
        RefusedError: no well-formed digest, with `refusal` as the message.

    """
    try:
        digest = field(path.read_bytes(), "containerimage.digest")
    except OSError:
        digest = None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise RefusedError(refusal)
    return digest


def push(reg: Registry, args: Args) -> int:
    """Build and push by digest, untagged, then record the digest.

    `--arch`: one native per-architecture image (build-oci-image.yml),
    labels only; the index is assembled downstream. The digest is
    written to `<digests>/<arch>` — a file, not a job output, because
    the outputs of a MATRIX job overwrite one another.

    `--pg`: both platforms in one build (build-pgrx-images.yml): FROM
    scratch runs nothing, so no emulation is involved. The index comes
    from this build, so its annotations enter here and the buildx floor
    is asserted first. The record is `<digests>/pg<N>.json` with the tag
    NAME riding along, applied only post-gate by tag-images.yml.

    Returns:
        0.

    """
    index = args.pg is not None
    if index:
        buildx_floor(reg.docker("buildx", "version").decode())
    reg.login()
    meta = Path(args.metadata_file)
    platform = ["--platform", args.platform] if args.platform else []
    # Build as its own statement, then read the metadata: a failed build
    # refuses on its exit status, with its log already in the step's.
    reg.docker(
        "buildx",
        "build",
        "--file",
        args.file,
        *platform,
        *fact_flags(reg.need("FACTS"), labels=True, index=index),
        *DETERMINISM,
        "--output",
        OUTPUT.format(image=args.image),
        "--metadata-file",
        str(meta),
        args.context,
        capture=False,
    )
    digests = Path(args.digests)
    digests.mkdir(parents=True, exist_ok=True)
    if index:
        digest = read_metadata_digest(meta, "no index digest from the push")
        record = {"pg": args.pg, "image": args.image, "tag": args.tag, "digest": digest}
        (digests / f"pg{args.pg}.json").write_text(
            json.dumps(record, indent=2) + "\n", "utf-8"
        )
        workflow_command.notice(f"pg{args.pg} index {digest}")
    elif args.arch is not None:  # the group requires --arch or --pg
        digest = read_metadata_digest(meta, "no digest from the push")
        (digests / args.arch).write_text(digest, "utf-8")
        workflow_command.notice(f"{args.arch} pushed as {digest}")
    return 0


def read_arch_digests(digests: Path, arches: list[str]) -> list[str]:
    """Read one digest per architecture, refusing a partial set.

    Returns:
        The digests, in `arches` order.

    Raises:
        RefusedError: a digest is missing or malformed.

    """
    out = []
    for arch in arches:
        try:
            d = (digests / arch).read_text("utf-8").rstrip("\n")
        except OSError:
            d = ""
        if not DIGEST.fullmatch(d):
            msg = (
                "a per-architecture digest is missing; refusing to tag a partial image"
            )
            raise RefusedError(msg)
        out.append(d)
    return out


def index_bytes(dry_run: bytes) -> tuple[bytes, str]:
    """Take the index manifest out of `imagetools create --dry-run`.

    --dry-run prints the exact bytes a tagged create would push,
    annotations included, plus one trailing newline (measured, buildx
    0.36: byte-identical to the manifest a tagged create stored once that
    newline is dropped).

    Returns:
        The manifest bytes and their media type.

    Raises:
        RefusedError: no trailing newline, or not an OCI image index.

    """
    if not dry_run.endswith(b"\n"):
        msg = (
            "imagetools --dry-run output does not end in a newline;"
            " refusing to guess the manifest bytes"
        )
        raise RefusedError(msg)
    body = dry_run[:-1]
    ctype = field(body, "mediaType") or ""
    if ctype != OCI_INDEX:
        msg = (
            f"the assembled index is '{ctype or 'no media type'}',"
            " not an OCI image index"
        )
        raise RefusedError(msg)
    return body, OCI_INDEX


def write_outputs(path: str, outputs: dict[str, str]) -> None:
    """Append `key=value` lines to the step's GITHUB_OUTPUT."""
    with Path(path).open("a", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in outputs.items())


def assemble(reg: Registry, args: Args) -> int:
    """Assemble the per-architecture digests into one index, by digest.

    The index is pushed BY DIGEST, with no tag (#1280): a manifest PUT
    under the content address of its own bytes, and the registry must
    answer that digest. The sources are already in this repository, so
    there is nothing to copy across.

    Returns:
        0.

    Raises:
        RefusedError: the registry answered another digest, or none.

    """
    buildx_floor(reg.docker("buildx", "version").decode())
    image = f"{REGISTRY}/{reg.need('GITHUB_REPOSITORY').lower()}"
    digests = read_arch_digests(Path(args.digests), ["amd64", "arm64"])
    reg.login()
    dry = reg.docker(
        "buildx",
        "imagetools",
        "create",
        "--dry-run",
        *fact_flags(reg.need("FACTS"), labels=False, index=True),
        *(f"{image}@{d}" for d in digests),
    )
    body, ctype = index_bytes(dry)
    index = "sha256:" + hashlib.sha256(body).hexdigest()
    name = name_of(image)
    token = reg.token(name)
    reg.request(
        "PUT",
        reg.manifest_url(name, index),
        {"Authorization": f"Bearer {token}", "Content-Type": ctype},
        body,
    )
    answered = reg.resolve(f"{image}@{index}")
    if answered != index:
        msg = (
            f"the registry answered {answered or 'no digest'}"
            f" for the index pushed as {index}"
        )
        raise RefusedError(msg)
    images = [{"image": image, "tag": args.tag, "digest": index}]
    # registry-subjects: one index, one leg. The class varies nothing
    # above the per-architecture builds folded here, so the target is the
    # publisher's name for the whole of it.
    write_outputs(
        reg.need("GITHUB_OUTPUT"),
        {
            "image": image,
            "digest": index,
            "registry-subjects": f"image {index.removeprefix('sha256:')}  {image}",
            "images": json.dumps(images, separators=(",", ":"), ensure_ascii=False),
        },
    )
    workflow_command.notice(f"index {index}")
    return 0


def parse_images(raw: str) -> list[tuple[str, str, str]]:
    """Validate tag-images.yml's input whole, before anything is tagged.

    Extra keys are ignored, so a build's signing-matrix output passes
    through unchanged.

    Returns:
        (image, tag, digest) per entry.

    Raises:
        RefusedError: not an array of string triples, or a field that
            fails its whitelist.

    """
    # RecursionError too: JSON nested past the parser's depth is a shape
    # untrusted input can take, and it died here as a traceback (#1556).
    try:
        parsed = decoded.load_json(raw)
    except decoded.UndecodableError:
        parsed = None
    triples: list[tuple[str, str, str]] = []
    for e in parsed if decoded.is_array(parsed) else []:
        image, tag, digest = (e.get(k) if decoded.is_object(e) else None for k in KEYS)
        if not (
            isinstance(image, str) and isinstance(tag, str) and isinstance(digest, str)
        ):
            triples = []
            break
        triples.append((image, tag, digest))
    if not triples:
        msg = "images is not a JSON array of {image, tag, digest}"
        raise RefusedError(msg)
    out: list[tuple[str, str, str]] = []
    for image, tag, digest in triples:
        if not IMAGE_REF.fullmatch(image):
            msg = "not a lowercase ghcr.io reference"
            raise RefusedError(msg)
        if not TAG.fullmatch(tag):
            msg = "implausible tag"
            raise RefusedError(msg)
        if not DIGEST.fullmatch(digest):
            msg = "digest is not sha256:<64 lowercase hex>"
            raise RefusedError(msg)
        out.append((image, tag, digest))
    return out


def tag(reg: Registry, _args: Args) -> int:
    """Put each gated digest's exact manifest bytes under its tag (#118).

    Fetch the manifest by digest, PUT the same bytes to the tag name, and
    require the tag to resolve to the digest. No re-assembly, so the
    digest cannot change under the tag: the crane-tag pattern. The
    Content-Type must round-trip or the registry would compute a
    different digest.

    Returns:
        0.

    Raises:
        RefusedError: a tag that resolves to another digest.

    """
    for image, name_tag, digest in parse_images(reg.need("UNTRUSTED_IMAGES")):
        name = name_of(image)
        token = reg.token(name)
        body, ctype = reg.request(
            "GET",
            reg.manifest_url(name, digest),
            {"Authorization": f"Bearer {token}", "Accept": INDEX_ACCEPT},
        )
        reg.request(
            "PUT",
            reg.manifest_url(name, name_tag),
            {"Authorization": f"Bearer {token}", "Content-Type": ctype},
            body,
        )
        # The proof: the tag resolves to the gated digest, byte for byte
        # the index the repro gate compared.
        resolved = reg.resolve(f"{image}:{name_tag}")
        if resolved != digest:
            msg = f"{image}:{name_tag} resolves to {resolved}, gated {digest}"
            raise RefusedError(msg)
        workflow_command.notice(f"{image}:{name_tag} -> {digest}")
    return 0


def parser() -> argparse.ArgumentParser:
    """Build the command line.

    Returns:
        The parser, one subcommand per operation.

    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("push", help="build and push by digest, untagged")
    s.add_argument("--image", required=True, help="fully-qualified, lowercase")
    s.add_argument("--file", required=True, help="the Dockerfile")
    s.add_argument("--context", required=True, help="the build context")
    s.add_argument("--digests", required=True, help="directory for the record")
    # ruff: ignore[hardcoded-temp-file]
    s.add_argument("--metadata-file", default="/tmp/meta.json")
    mode = s.add_mutually_exclusive_group(required=True)
    mode.add_argument("--arch", help="one native per-architecture image")
    mode.add_argument("--pg", help="a pgrx artifact index for this major")
    s.add_argument("--platform", help="buildx platforms (with --pg)")
    s.add_argument("--tag", help="the tag name the record carries (with --pg)")
    s.set_defaults(run=push)

    a = sub.add_parser("assemble", help="assemble and push the index by digest")
    a.add_argument("--digests", required=True, help="per-architecture digests")
    a.add_argument("--tag", required=True, help="the version the index is for")
    a.set_defaults(run=assemble)

    t = sub.add_parser("tag", help="tag each gated digest (UNTRUSTED_IMAGES)")
    t.set_defaults(run=tag)
    return p


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    origin: str = ORIGIN,
) -> int:
    """Run one operation from the step's environment.

    `origin` is the registry's HTTP origin: fixed on the production path,
    passed only by the tests.

    Returns:
        The exit status: 0 done, 1 refused.

    """
    p = parser()
    args = p.parse_args(argv, namespace=Args())
    if args.command == "push" and args.pg is not None and not args.tag:
        p.error("push --pg needs --tag")
    reg = Registry(os.environ if env is None else env, origin)
    try:
        return args.run(reg, args)
    except RefusedError as e:
        workflow_command.error(str(e))
    return 1


if __name__ == "__main__":
    sys.exit(main())

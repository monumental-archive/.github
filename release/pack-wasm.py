#!/usr/bin/env python3
"""Pack and hash the exact bytes a wasm-npm release publishes.

The wasm-npm class's pack step (#1384 moved it here from the workflow's
shell, refusals unchanged). `npm pack` here, `npm publish <tarball>` in
the publisher: the bytes hashed, signed and verified are the bytes that
ship, with no re-pack in between to drift.

Run from the workspace root after `wasm-pack build`, with
UNTRUSTED_CRATE_DIR (the crate whose pkg/ wasm-pack wrote),
UNTRUSTED_VERSION (the tag's version), GITHUB_REPOSITORY and
GITHUB_OUTPUT. Writes dist/<tarball> and the step's outputs: `subjects`
(the sha256 manifest), `package` (name@version) and `registry-subjects`
(one tarball, one leg, `package`).

THE REPOSITORY IS THE CALLER'S. npm's provenance validation (E422)
requires package.json's repository to match the caller repository, and
wasm-pack copies it from Cargo.toml's `repository`, so its absence fails
here, with the remedy, not at the registry after everything else
succeeded (found by the v0.14.1 run).

Exit status: 0 packed, 1 refused, else npm's own failing status.
"""

import hashlib
import json
import os

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


class RefusedError(Exception):
    """The pack is refused; the message says why."""


class NpmFailedError(Exception):
    """npm failed; its own output already said why."""

    def __init__(self, status: int) -> None:
        """Keep npm's exit status, which the step exits with."""
        super().__init__(status)
        self.status = status


def npm(cwd: Path, *args: str) -> str:
    """Run npm in a directory.

    Returns:
        Its standard output, trailing newlines removed.

    Raises:
        NpmFailedError: npm exited non-zero.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        # ruff: ignore[start-process-with-partial-path] - the caller's pinned node, on PATH
        ["npm", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise NpmFailedError(done.returncode)
    return done.stdout.rstrip("\n")


def raw(value: object) -> str:
    """Render a JSON value as `jq -r` prints it.

    Returns:
        A string as itself, null as `null`, anything else as JSON.

    """
    if isinstance(value, str):
        return value
    return json.dumps(value)


def repository(manifest: Mapping[str, object]) -> str:
    """Read package.json's repository, URL form or string form.

    Returns:
        `.repository.url`, else `.repository`, else "".

    """
    repo = manifest.get("repository")
    if isinstance(repo, Mapping):
        url = repo.get("url")
        if url is not None and url is not False:
            return raw(url)
    if repo is None or repo is False:
        return ""
    return raw(repo)


def check(manifest: Mapping[str, object], version: str, caller: str) -> None:
    """Refuse a package that names another repository or another version.

    Raises:
        RefusedError: the repository or the version is not the release's.

    """
    repo_url = repository(manifest)
    want = f"https://github.com/{caller}"
    norm = repo_url.removeprefix("git+").removesuffix(".git")
    if norm != want:
        msg = (
            f"package.json repository is '{repo_url}', npm provenance requires"
            f" '{want}' — set repository = \"{want}\" in the crate's Cargo.toml"
            " (canon: [workspace.package] repository with repository.workspace ="
            " true)"
        )
        raise RefusedError(msg)
    said = raw(manifest.get("version"))
    if said != version:
        msg = f"package.json says {said}, tag says {version}"
        raise RefusedError(msg)


def read_manifest(path: Path) -> Mapping[str, object]:
    """Read package.json, which must be a JSON object.

    `0`, `[]` or text that is not JSON died here with a traceback
    (#1556); each is refused by name.

    Returns:
        The parsed object.

    Raises:
        RefusedError: it is not JSON, or not a JSON object.

    """
    try:
        manifest = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as err:
        msg = f"{path} is not JSON: {err}"
        raise RefusedError(msg) from err
    if not isinstance(manifest, dict):
        msg = f"{path} is not a JSON object"
        raise RefusedError(msg)
    return manifest


def pack(env: Mapping[str, str]) -> list[str]:
    """Check the package, pack it and hash the tarball.

    Returns:
        The lines for $GITHUB_OUTPUT.

    Raises:
        RefusedError: package.json is not a JSON object, a check failed,
            or npm packed nothing.

    """
    dist = Path("dist")
    dist.mkdir(parents=True, exist_ok=True)
    pkg = Path(env["UNTRUSTED_CRATE_DIR"]) / "pkg"
    manifest = read_manifest(pkg / "package.json")
    version = env["UNTRUSTED_VERSION"]
    check(manifest, version, env["GITHUB_REPOSITORY"])
    tarball = npm(dist, "pack", str(pkg.resolve()), "--silent")
    packed = dist / tarball
    if not tarball or not packed.is_file() or packed.stat().st_size == 0:
        msg = "npm pack produced nothing"
        raise RefusedError(msg)
    line = f"{hashlib.sha256(packed.read_bytes()).hexdigest()}  {tarball}"
    print(line, flush=True)
    # registry-subjects: one tarball, one leg, the same digest the signer
    # is handed, carrying the publisher's name for a class that varies
    # nothing.
    return [
        "subjects<<SUBJECTS_EOF",
        line,
        "SUBJECTS_EOF",
        f"package={raw(manifest.get('name'))}@{raw(manifest.get('version'))}",
        f"registry-subjects=package {line}",
    ]


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the pack step.

    Returns:
        The exit status: 0 packed, 1 refused, else npm's.

    """
    env = os.environ if env is None else env
    try:
        outputs = pack(env)
    except RefusedError as e:
        print(f"::error::{e}", flush=True)
        return 1
    except NpmFailedError as e:
        return e.status
    with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        out.write("".join(f"{x}\n" for x in outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

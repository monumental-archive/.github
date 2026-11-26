#!/usr/bin/env python3
"""Derive the tree's Rust workspaces, one root per workspace (#2004).

The belt decides a repository has Rust from its tracked `Cargo.toml`
files, and took the crates to check from the root workspace alone, so a
tracked crate that is its own workspace (a cargo-fuzz crate) was never
formatted or linted. This is the one answer to "which workspaces are in
this tree", and every task that runs cargo loops over it.

- `roots()`: each tracked manifest outside `vendor/` (`tracked.tracked`,
  #1986), resolved with `cargo locate-project --workspace`, as the
  distinct workspace roots relative to `git rev-parse --show-toplevel`.
  Both sides are resolved physically, so `/tmp` and `/private/tmp`
  agree. The root `Cargo.toml` comes first when there is one, the rest
  sorted.
- `resolved()`: the same list, or None having said why, for a Python
  helper that loops over the roots (lint:msrv, #2011).
- `rust_workspaces.py roots`: the same list, NUL-separated on stdout,
  for `org_rust_workspace_roots` in mise/rust-workspaces.sh.
- `built_as(root)`: the environment one root's cargo calls run in, held
  for the calls inside it (#2093). A cargo-fuzz workspace (its root
  manifest carries `[package.metadata] cargo-fuzz = true`, the marker
  `cargo fuzz init` writes and cargo-fuzz requires) is built as
  cargo-fuzz builds it, with `--cfg fuzzing`; every other root is
  unchanged.

Two findings, both red, each reported for every manifest that has it:

- `cargo locate-project` fails for a manifest: cargo's own text;
- a manifest resolves to a root that is not one of the tracked manifests
  (a crate with no `[workspace]` table, inside a tree nested in
  another's workspace, #1290): `<manifest> belongs to a workspace
  outside this tree: <root>`, with the remedy.

stdlib only.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import decoded
import findings as writer
import tracked

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

CARGO = shutil.which("cargo") or "cargo"
GIT = shutil.which("git") or "git"

# The tracked manifests a workspace can be rooted at: every Cargo.toml
# outside a vendored tree, which is upstream's code (config.toml's
# enumerating-walk rule).
PATHSPECS = (":!vendor/**", "Cargo.toml", "*/Cargo.toml")

TOP_MANIFEST = "Cargo.toml"

REMEDY = "give it its own [workspace] table, or make it a member of a tracked workspace"

# What cargo-fuzz adds to every build of the crate it runs (#2093), and
# the two variables cargo reads rustc's flags from: the encoded one,
# whose arguments are split by SEPARATOR, wins whenever it is set.
FUZZ_CFG = ("--cfg", "fuzzing")
PLAIN = "RUSTFLAGS"
ENCODED = "CARGO_ENCODED_RUSTFLAGS"
SEPARATOR = "\x1f"


class UnresolvedError(Exception):
    """A manifest's workspace could not be named; the findings say why."""


def toplevel() -> Path:
    """Name the working tree's top, resolved physically.

    Returns:
        The top directory.

    """
    out = subprocess.run(
        [GIT, "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return Path(out.strip()).resolve()


def locate(manifest: str) -> Path | None:
    """Ask cargo for the workspace root manifest of one manifest.

    Returns:
        The root manifest, resolved physically; None when cargo refused,
        after the finding that carries cargo's text.

    """
    done = subprocess.run(
        [
            CARGO,
            "locate-project",
            "--workspace",
            "--manifest-path",
            manifest,
            "--message-format",
            "plain",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if done.returncode != 0:
        text = (done.stderr + done.stdout).strip().splitlines()
        writer.finding("", f"cargo locate-project failed for {manifest}", text)
        return None
    return Path(done.stdout.strip()).resolve()


def ordered(found: set[str]) -> list[str]:
    """Put the top manifest first and the rest sorted.

    Returns:
        The roots in the order every caller runs them.

    """
    rest = sorted(found - {TOP_MANIFEST})
    return [TOP_MANIFEST, *rest] if TOP_MANIFEST in found else rest


def roots() -> list[str]:
    """Derive the distinct workspace roots of the tracked manifests.

    Returns:
        The roots, relative to the tree's top.

    Raises:
        UnresolvedError: a manifest's root could not be named in the
            tree; every such manifest has had its finding.

    """
    top = toplevel()
    manifests = tracked.tracked(*PATHSPECS)
    known = {Path(m).resolve(): m for m in manifests}
    found: set[str] = set()
    unresolved = False
    for manifest in manifests:
        root = locate(manifest)
        if root is None:
            unresolved = True
            continue
        if root not in known:
            writer.finding(
                "",
                f"{manifest} belongs to a workspace outside this tree: {root}",
                [REMEDY],
            )
            unresolved = True
            continue
        found.add(root.relative_to(top).as_posix())
    if unresolved:
        msg = "a manifest's workspace is not in this tree"
        raise UnresolvedError(msg)
    return ordered(found)


def resolved() -> list[str] | None:
    """Derive the roots, or say why not, for a caller that loops over them.

    Returns:
        The roots, as `roots` gives them; None when a manifest's
        workspace is not in the tree (its findings already said) or git
        could not name the tree (said here, with git's text).

    """
    try:
        return roots()
    except UnresolvedError:
        return None
    except subprocess.CalledProcessError as exc:
        said: object = exc.stderr
        text = os.fsdecode(said) if isinstance(said, bytes) else str(said)
        writer.finding("", "git could not name the tree", text.strip().splitlines())
        return None


def fuzzing(root: str) -> bool:
    """Say whether a workspace is a cargo-fuzz crate, by its own marker.

    `root` is one of `roots()`'s answers, so a manifest the tree tracks
    (#1986). The marker is the tool's own statement, never the
    directory's name: cargo-fuzz takes `--fuzz-dir`.

    Returns:
        True when the root manifest's `[package.metadata]` sets
        `cargo-fuzz = true`.

    """
    manifest = decoded.load_toml(Path(root).read_bytes())
    package = manifest.get("package")
    metadata = package.get("metadata") if decoded.is_object(package) else None
    return decoded.is_object(metadata) and metadata.get("cargo-fuzz") is True


def rustflags(root: str, environ: Mapping[str, str]) -> dict[str, str]:
    """Name the flags one root's cargo calls add, as cargo-fuzz adds them.

    cargo-fuzz builds its crate with `--cfg fuzzing` appended to the
    flags the call already has, so a target that calls fuzzing-only
    code is linted as the build that runs it (#2093). Appended to
    whichever of the two variables cargo will read: the encoded one
    when it is set at all, else RUSTFLAGS.

    Returns:
        The variable to set and its value; nothing for a root that is
        not a cargo-fuzz crate.

    """
    if not fuzzing(root):
        return {}
    if ENCODED in environ:
        had = environ[ENCODED]
        return {ENCODED: SEPARATOR.join([*([had] if had else []), *FUZZ_CFG])}
    had = environ.get(PLAIN, "")
    return {PLAIN: " ".join([*([had] if had else []), *FUZZ_CFG])}


@contextlib.contextmanager
def built_as(root: str) -> Iterator[None]:
    """Hold one root's flags in this process's environment, then put it back.

    Every cargo call inside starts from `os.environ`, so the flags reach
    each of the root's calls and no other root's.

    Yields:
        Nothing; the variable is restored, or removed, on the way out.

    """
    added = rustflags(root, os.environ)
    saved = {key: os.environ.get(key) for key in added}
    os.environ.update(added)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                del os.environ[key]
            else:
                os.environ[key] = value


def main(argv: list[str]) -> int:
    """Print the roots NUL-separated, for the shell reader.

    Returns:
        0 with the roots printed, 1 on a finding, 2 on a usage error.

    """
    if argv != ["roots"]:
        print("usage: rust_workspaces.py roots", file=sys.stderr)
        return 2
    listed = resolved()
    if listed is None:
        return 1
    stream: object = sys.stdout
    if isinstance(stream, io.TextIOWrapper):
        # The bytes, so a name need not be UTF-8: os.fsencode undoes the
        # decoding tracked.split did.
        stream.flush()
        stream.buffer.write(b"".join(os.fsencode(r) + b"\0" for r in listed))
        stream.buffer.flush()
    else:
        print("".join(f"{r}\0" for r in listed), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

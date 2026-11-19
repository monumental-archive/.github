#!/usr/bin/env python3
"""Derive a release job's matrix from its request: one tested derivation (#1385).

Five plan steps each derived a matrix in their own shell. This is the one
derivation, a mode per workflow, and the target-to-runner tables live
here, once: a new target or runner is one table row with a test.

    matrix.py targets CLASS REQUEST     build-go-binary.yml (go-binary)
                                        build-rust-binary.yml (rust-binary)
    matrix.py pgrx-extension MAPPING REQUEST
                                        build-pgrx-extension.yml
    matrix.py pgrx-images REQUEST       build-pgrx-images.yml
    matrix.py base-images MAPPING       base-attest.yml

REQUEST is the caller's comma-separated input, verbatim; MAPPING is the
path of `docker/pgrx-base-images.toml` in the canon tree the job holds.

STDOUT IS THE STEP'S OUTPUTS: `matrix=`, `expected=` and (pgrx-extension)
`floor=` lines, appended by the step to $GITHUB_OUTPUT. `expected` is the
planned legs as a JSON array, the contract `stele derive subjects
--expected` reads, so its name and shape do not move. Notices and
refusals go to stderr, where the runner reads workflow commands as it
does on stdout. Nothing reaches stdout before every check has passed, so
a refusal never leaves half a set of outputs behind.

THE REQUEST IS SPLIT AS THE SHELL SPLIT IT (`IFS=',' read -ra` over the
input with its spaces removed): the first line only, a trailing comma
dropped, an inner or leading empty field kept and refused as unknown.
"""

import json
import sys
import tomllib
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import workflow_command

# The canon's target-to-runner tables, one per class that builds per
# target, with the hint the refusal of an unknown target carries.
TARGETS = {
    "go-binary": (
        {
            "linux-amd64": "ubuntu-24.04",
            "linux-arm64": "ubuntu-24.04-arm",
            "darwin-amd64": "macos-15-intel",
            "darwin-arm64": "macos-15",
        },
        "canon targets only (<goos>-<goarch>)",
    ),
    "rust-binary": (
        {
            "x86_64-unknown-linux-musl": "ubuntu-24.04",
            "aarch64-unknown-linux-musl": "ubuntu-24.04-arm",
            "x86_64-apple-darwin": "macos-15-intel",
            "aarch64-apple-darwin": "macos-15",
        },
        "canon targets only",
    ),
}

# Every pgrx extension major builds on both arches, amd64 first.
PGRX_ARCHES = (("amd64", "ubuntu-24.04"), ("arm64", "ubuntu-24.04-arm"))

MAPPING_NAME = "docker/pgrx-base-images.toml"


class RefusalError(Exception):
    """A request this derivation will not plan; the message is the remedy."""


class BareRefusalError(RefusalError):
    """A refusal printed bare, as base-attest's heredoc printed it."""


def split(request: str) -> list[str]:
    """Split a request the way `IFS=',' read -ra` split it, spaces removed.

    Returns:
        The requested fields, in order.

    """
    line = request.replace(" ", "").split("\n", 1)[0]
    if not line:
        return []
    fields = line.split(",")
    if not fields[-1]:
        fields.pop()
    return fields


def compact(value: object) -> str:
    """Render JSON the way `jq -c` did: no spaces, UTF-8 left as is.

    Returns:
        The compact JSON text.

    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def targets(cls: str, request: str) -> tuple[list[str], list[str]]:
    """Plan a per-target build: each requested target on its runner.

    Returns:
        The output lines and the notice lines.

    Raises:
        RefusalError: nothing requested, an unknown target, a duplicate.

    """
    runners, hint = TARGETS[cls]
    requested = split(request)
    if not requested:
        msg = "no targets requested — a release that builds nothing is not a release"
        raise RefusalError(msg)
    include = []
    for target in requested:
        if target not in runners:
            msg = f"unknown target {target}; {hint}"
            raise RefusalError(msg)
        include.append({"target": target, "runner": runners[target]})
    planned = [cell["target"] for cell in include]
    if len(planned) != len(set(planned)):
        msg = "duplicate target requested"
        raise RefusalError(msg)
    outputs = [
        f"matrix={compact({'include': include})}",
        f"expected={compact(planned)}",
    ]
    notices = [f"will build {c['target']} on {c['runner']}" for c in include]
    return outputs, notices


def load(mapping: Path) -> dict:
    """Read the base-image mapping.

    Returns:
        The parsed TOML.

    """
    with mapping.open("rb") as f:
        return tomllib.load(f)


def pinned(table: object, major: str) -> str | None:
    """Return the image a role pins for a major, or None where it pins none.

    Returns:
        The digest-pinned reference, or None.

    """
    ref = table.get(major) if isinstance(table, dict) else None
    return ref if isinstance(ref, str) and ref else None


def requested_majors(request: str) -> list[str]:
    """Split a pgrx request into its majors: every rule about the request itself.

    Both pgrx planners call this first and keep only what is theirs
    (#1531): one rule, one place.

    Returns:
        The requested majors, in order.

    Raises:
        RefusalError: nothing requested, a major that is not a number, a
            duplicate.

    """
    majors = split(request)
    if not majors:
        msg = "no PG majors requested — a release that builds nothing is not a release"
        raise RefusalError(msg)
    for pg in majors:
        if not (pg.isascii() and pg.isdigit()):
            msg = f"{pg} is not a PG major"
            raise RefusalError(msg)
    if len(majors) != len(set(majors)):
        msg = "duplicate PG major requested"
        raise RefusalError(msg)
    return majors


def pgrx_extension(mapping: Path, request: str) -> tuple[list[str], list[str]]:
    """Plan a pgrx extension build: each major on both arches, both images.

    Returns:
        The output lines and the notice lines.

    Raises:
        RefusalError: a major the mapping does not pin a build and a smoke
            image for, checked after `requested_majors`'s own rules.

    """
    t = load(mapping)
    majors = requested_majors(request)
    include = []
    for pg in majors:
        build, smoke = pinned(t.get("build"), pg), pinned(t.get("smoke"), pg)
        if build is None or smoke is None:
            msg = f"pg{pg} has no digest-pinned base image in {MAPPING_NAME}"
            raise RefusalError(msg)
        include += [
            {"pg": pg, "arch": arch, "runner": runner, "build": build, "smoke": smoke}
            for arch, runner in PGRX_ARCHES
        ]
    planned = [f"{cell['pg']}-{cell['arch']}" for cell in include]
    floor = t.get("floor", {}).get("glibc")
    outputs = [
        f"matrix={compact({'include': include})}",
        f"expected={compact(planned)}",
        f"floor={'null' if floor is None else floor}",
    ]
    notices = [f"will build pg{c['pg']} on {c['runner']}" for c in include]
    return outputs, notices


def pgrx_images(request: str) -> tuple[list[str], list[str]]:
    """Plan the extension images: one cell per requested major.

    The request's own rules are `requested_majors`'s; the images planner
    adds none.

    Returns:
        The output lines and (none) notice lines.

    """
    majors = requested_majors(request)
    outputs = [
        f"matrix={compact({'include': [{'pg': pg} for pg in majors]})}",
        f"expected={compact(majors)}",
    ]
    return outputs, []


def base_images(mapping: Path) -> tuple[list[str], list[str]]:
    """Plan the approval set: every pinned base image, build then smoke.

    Returns:
        The output lines and the notice lines.

    Raises:
        BareRefusalError: a reference with no digest, in the wording
            base-attest has always printed, bare.
        RefusalError: a mapping that pins nothing.

    """
    t = load(mapping)
    cells = []
    for role in ("build", "smoke"):
        for pg, ref in sorted(t.get(role, {}).items()):
            name, _, digest = ref.partition("@")
            if not digest.startswith("sha256:"):
                msg = f"{role}.{pg}: no digest in {ref!r}"
                raise BareRefusalError(msg)
            repo = name.split(":", 1)[0]
            if "/" not in repo:
                repo = f"docker.io/library/{repo}"
            cells.append({
                "role": role,
                "pg": pg,
                "ref": ref,
                "name": repo,
                "digest": digest,
            })
    if not cells:
        msg = "no pinned base images found — an empty approval set approves nothing"
        raise RefusalError(msg)
    notices = [f"will attest {c['role']} pg{c['pg']}: {c['ref']}" for c in cells]
    return [f"matrix={compact({'include': cells})}"], notices


USAGE = """usage: matrix.py targets {go-binary,rust-binary} REQUEST
       matrix.py pgrx-extension MAPPING REQUEST
       matrix.py pgrx-images REQUEST
       matrix.py base-images MAPPING"""


def plan(argv: list[str]) -> tuple[list[str], list[str]] | None:
    """Dispatch one mode.

    Returns:
        The output and notice lines, or None for a malformed command line.

    """
    match argv:
        case ["targets", cls, request] if cls in TARGETS:
            return targets(cls, request)
        case ["pgrx-extension", mapping, request]:
            return pgrx_extension(Path(mapping), request)
        case ["pgrx-images", request]:
            return pgrx_images(request)
        case ["base-images", mapping]:
            return base_images(Path(mapping))
    return None


def main(argv: list[str] | None = None) -> int:
    """Print a mode's outputs to stdout and its notices to stderr.

    Returns:
        0 when planned, 1 on a refusal, 2 on a malformed command line.

    """
    try:
        planned = plan(sys.argv[1:] if argv is None else argv)
    except BareRefusalError as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except RefusalError as refusal:
        workflow_command.error(str(refusal), stream=sys.stderr)
        return 1
    if planned is None:
        print(USAGE, file=sys.stderr)
        return 2
    outputs, notices = planned
    for line in notices:
        workflow_command.notice(line, stream=sys.stderr)
    for line in outputs:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

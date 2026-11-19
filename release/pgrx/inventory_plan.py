#!/usr/bin/env python3
"""Emit one pgrx-extension cell's inventory plan.

The pgrx-extension class's plan step (#1371 moved it here from the
workflow's shell). The artifact-to-package mapping exists with certainty
exactly once — in this cell: it built exactly this crate with exactly
`--features pg<major>` and no default features into exactly this
tarball, because those are its own build inputs (#537). The plan is that
mapping as data, written as plan/plan.json in the shape `stele assert
plans` judges and the sbom job derives the closure from on the prove leg.
The artifact name keeps the document apart from the package; the purl
keeps the crate's real identity, because advisory matching runs on what
the artifact IS (stele#126).

Usage: inventory_plan.py CRATE_DIR MAJOR, with PLAN_DOC_PREFIX the
class's planned prefix — stated on the step, where the producer join
(mise/plan_producers.py) reads it. A build leg carries no belt, so this
is stdlib Python run from the canon tree. The bytes are the ones the
step's jq wrote: two-space indentation and a trailing newline.

Exit status: 0 written, 1 refused, 2 usage.
"""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

MAJOR = re.compile(r"^[0-9]+$")


class RefusedError(Exception):
    """The plan is refused; the message says why."""


def package_name(crate_dir: Path) -> str:
    """Read the crate's own package name from its manifest.

    Returns:
        `package.name`.

    Raises:
        RefusedError: the manifest is unreadable or names no package.

    """
    manifest = crate_dir / "Cargo.toml"
    try:
        with manifest.open("rb") as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        msg = f"{manifest}: {e}"
        raise RefusedError(msg) from e
    package = parsed.get("package", {})
    # `package = false` died here as an AttributeError (#1556).
    name = package.get("name") if isinstance(package, dict) else None
    if not isinstance(name, str) or not name:
        msg = f"{manifest} names no package — a cell plans the crate it built"
        raise RefusedError(msg)
    return name


def plan(crate_dir: Path, major: str, prefix: str) -> list[dict]:
    """Build the cell's one-entry plan.

    Returns:
        The plan document.

    Raises:
        RefusedError: no prefix, or a major that is not a number.

    """
    if not prefix:
        msg = "PLAN_DOC_PREFIX is unset — the class's planned prefix names the document"
        raise RefusedError(msg)
    if not MAJOR.match(major):
        msg = f"major {major!r} is not a Postgres major"
        raise RefusedError(msg)
    pkg = package_name(crate_dir)
    return [
        {
            "class": "pgrx-extension",
            "doc": f"{prefix}{pkg}-pg{major}",
            "artifact": f"{pkg}-pg{major}",
            "params": {
                "cargoPackage": pkg,
                "features": [f"pg{major}"],
                "noDefaultFeatures": True,
            },
        }
    ]


def main(argv: list[str]) -> int:
    """Run the step.

    Returns:
        The exit status: 0 written, 1 refused, 2 usage.

    """
    if len(argv) != 2:  # ruff: ignore[magic-value-comparison] - the two positional arguments
        print("usage: inventory_plan.py CRATE_DIR MAJOR", file=sys.stderr)
        return 2
    try:
        entries = plan(Path(argv[0]), argv[1], os.environ.get("PLAN_DOC_PREFIX", ""))
    except RefusedError as e:
        print(f"::error::{e}", flush=True)
        return 1
    out = Path("plan")
    out.mkdir(exist_ok=True)
    rendered = json.dumps(entries, indent=2) + "\n"
    (out / "plan.json").write_text(rendered, encoding="utf-8")
    for e in entries:
        doc, pkg = e["doc"], e["params"]["cargoPackage"]
        print(f"::notice::plans inventory {doc} for package {pkg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

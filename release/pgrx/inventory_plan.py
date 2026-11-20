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
from dataclasses import dataclass
from pathlib import Path

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import decoded
import workflow_command

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
        parsed = decoded.load_toml(manifest.read_bytes())
    except (OSError, decoded.UndecodableError) as e:
        msg = f"{manifest}: {e}"
        raise RefusedError(msg) from e
    package = parsed.get("package", {})
    # `package = false` died here as an AttributeError (#1556).
    name = package.get("name") if decoded.is_object(package) else None
    if not isinstance(name, str) or not name:
        msg = f"{manifest} names no package — a cell plans the crate it built"
        raise RefusedError(msg)
    return name


@dataclass(frozen=True)
class Cell:
    """What one cell built: the crate, the major, the document's name."""

    package: str
    major: str
    doc: str

    def entry(self) -> dict[str, object]:
        """Write the cell as its plan entry.

        Returns:
            The entry, in the shape `stele assert plans` judges.

        """
        return {
            "class": "pgrx-extension",
            "doc": self.doc,
            "artifact": f"{self.package}-pg{self.major}",
            "params": {
                "cargoPackage": self.package,
                "features": [f"pg{self.major}"],
                "noDefaultFeatures": True,
            },
        }


def cell(crate_dir: Path, major: str, prefix: str) -> Cell:
    """Name the cell this step plans.

    Returns:
        The cell.

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
    return Cell(package=pkg, major=major, doc=f"{prefix}{pkg}-pg{major}")


def plan(crate_dir: Path, major: str, prefix: str) -> list[dict[str, object]]:
    """Build the cell's one-entry plan; `cell` refuses what it cannot name.

    Returns:
        The plan document.

    """
    return [cell(crate_dir, major, prefix).entry()]


def main(argv: list[str]) -> int:
    """Run the step.

    Returns:
        The exit status: 0 written, 1 refused, 2 usage.

    """
    if len(argv) != 2:  # ruff: ignore[magic-value-comparison] - the two positional arguments
        print("usage: inventory_plan.py CRATE_DIR MAJOR", file=sys.stderr)
        return 2
    try:
        one = cell(Path(argv[0]), argv[1], os.environ.get("PLAN_DOC_PREFIX", ""))
    except RefusedError as e:
        workflow_command.error(str(e))
        return 1
    out = Path("plan")
    out.mkdir(exist_ok=True)
    entries: list[dict[str, object]] = [one.entry()]
    rendered = json.dumps(entries, indent=2) + "\n"
    (out / "plan.json").write_text(rendered, encoding="utf-8")
    workflow_command.notice(f"plans inventory {one.doc} for package {one.package}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

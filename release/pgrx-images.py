#!/usr/bin/env python3
"""Stage and name the pgrx artifact image's inputs (#1381).

Two verbs for build-pgrx-images.yml, which kept both inline until #1381:
`stage` builds the per-architecture trees the image copies, and
`base-image` names the stock postgres image the consumer proof
(release/pgrx-image-proof.sh) installs the extension into.

STAGE.

The artifact image (docker/pgrx-artifact.Dockerfile) copies one tree per
architecture, `staged/<arch>/image/`, holding the image's one layout on
every major (#1298): `/lib` and `/share/extension`. This builds those
trees from the verified release tarballs the extension build produced,
the same bytes the release attests, so the image has one byte path.

For each of amd64 and arm64 it extracts
`<tarballs>/<name>-<version>-pg<major>-linux-<arch>.tar.gz` into
`staged/<arch>/` and copies the Debian tree the tarball installs,
`pkgroot/usr/lib/postgresql/<major>/lib` and
`pkgroot/usr/share/postgresql/<major>/extension`, into
`staged/<arch>/image/lib` and `staged/<arch>/image/share/extension`.
`<name>` is the crate directory's last segment with `-` as `_`, as
cargo-pgrx names the extension. It reads `pkgroot/` only: the tarball's
second copy is going away (#1274).

Refusals, each on stderr as a workflow `::error::` and exit 1:

- a missing tarball: the extension build did not produce that cell;
- a tarball without its `lib` or `extension` directory.

CONSUMER-DOCKERFILE. Prints the Dockerfile for one of the consumer
proof's two recipes (docs/release.md), the text the workflow used to
write through a heredoc: `copy` COPYs the image's one layout into the
stock postgres's own directories, `path` puts the image whole under
/extensions/ext. Both references must carry `@sha256:`; an unpinned one
is refused by name and renders nothing, so no unpinned base can reach a
build (#1381: a base named by a build argument is decided where the
build runs, out of lint:from-digests' sight, so the text is rendered
here instead of tracked).

EXTENSION-NAME. Prints the name of the one extension the artifact image
ships, the stem of the one `*.control` file in the staged
`share/extension/` tree it was built from. The consumer proof creates
that name and no other (#1439): the stock image's own extension
directory holds 45-47 contrib control files, and picking the first of
them created adminpack or amcheck on every major. A tree with no
control file, or more than one, is refused by name: the proof cannot
tell what it is proving.

BASE-IMAGE. Prints the `[build]` row for the major from
docker/pgrx-base-images.toml: the digest-pinned image the extension was
built against, so the proof installs it where it was built. A major
with no row is refused by name.

    pgrx-images.py stage --version V --crate-dir DIR --pg MAJOR
                         [--tarballs DIR] [--out DIR]
    pgrx-images.py consumer-dockerfile --recipe copy|path --image REF
                                       --base REF --pg MAJOR
    pgrx-images.py extension-name --tree DIR
    pgrx-images.py base-image --pg MAJOR --table FILE
"""

import argparse
import shutil
import sys
import tarfile
import tomllib
from pathlib import Path
from typing import NamedTuple

ARCHES = ("amd64", "arm64")


class RefusalError(Exception):
    """An input this helper will not build or prove an image from."""


def tarball_name(crate_dir: str, version: str, pg: str, arch: str) -> str:
    """Name the release tarball for one cell.

    Returns:
        `<name>-<version>-pg<major>-linux-<arch>.tar.gz`, the name from
        the crate directory's last segment with `-` as `_`.

    """
    name = Path(crate_dir).name.replace("-", "_")
    return f"{name}-{version}-pg{pg}-linux-{arch}.tar.gz"


def stage_arch(tarball: Path, pg: str, arch_dir: Path, shown: str) -> None:
    """Extract one tarball and copy its two directories into the image tree.

    Raises:
        RefusalError: the tarball is missing, or lacks `lib` or `extension`.

    """
    if not tarball.is_file():
        msg = f"missing {shown} — the extension build did not produce this cell"
        raise RefusalError(msg)
    arch_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(arch_dir, filter="tar")
    lib = Path("pkgroot/usr/lib/postgresql") / pg / "lib"
    share = Path("pkgroot/usr/share/postgresql") / pg / "extension"
    for rel in (lib, share):
        if not (arch_dir / rel).is_dir():
            msg = f"{shown} has no {rel}"
            raise RefusalError(msg)
    image = arch_dir / "image"
    shutil.copytree(arch_dir / lib, image / "lib", symlinks=True, dirs_exist_ok=True)
    shutil.copytree(
        arch_dir / share,
        image / "share" / "extension",
        symlinks=True,
        dirs_exist_ok=True,
    )


class Cell(NamedTuple):
    """What names one major's tarballs: the crate, the version, the major."""

    crate_dir: str
    version: str
    pg: str


def stage(cell: Cell, tarballs: Path, out: Path, shown: str) -> None:
    """Stage both architectures, in order, stopping at the first refusal.

    `shown` is the tarball directory as the messages name it. A refusal
    from either architecture propagates as stage_arch raised it.

    """
    for arch in ARCHES:
        name = tarball_name(cell.crate_dir, cell.version, cell.pg, arch)
        stage_arch(tarballs / name, cell.pg, out / arch, f"{shown}/{name}")


RECIPES = {
    "copy": (
        "COPY --from=ext /lib/ /usr/lib/postgresql/{pg}/lib/\n"
        "COPY --from=ext /share/extension/ /usr/share/postgresql/{pg}/extension/\n"
    ),
    "path": "COPY --from=ext / /extensions/ext/\n",
}


def consumer_dockerfile(recipe: str, image: str, base: str, pg: str) -> str:
    """Render one consumer proof recipe's Dockerfile.

    Returns:
        `FROM <image> AS ext`, `FROM <base>`, then the recipe's COPY lines.

    Raises:
        RefusalError: a reference that is not pinned by digest.

    """
    for role, ref in (("extension image", image), ("base image", base)):
        if "@sha256:" not in ref:
            msg = (
                f"the {role} {ref} is not pinned by digest (@sha256:); the"
                " consumer proof builds only from pinned references"
            )
            raise RefusalError(msg)
    return f"FROM {image} AS ext\nFROM {base}\n" + RECIPES[recipe].format(pg=pg)


def extension_name(tree: Path) -> str:
    """Name the one extension a staged `share/extension/` tree ships.

    Returns:
        The stem of its one `*.control` file.

    Raises:
        RefusalError: no control file, or more than one.

    """
    controls = sorted(p.stem for p in tree.glob("*.control"))
    if not controls:
        msg = (
            f"{tree} holds no extension control file; the artifact image"
            " ships no extension for the consumer proof to create"
        )
        raise RefusalError(msg)
    if len(controls) > 1:
        msg = (
            f"{tree} holds {len(controls)} extension control files"
            f" ({', '.join(controls)}); the consumer proof creates the one"
            " extension the artifact image ships and cannot tell which"
        )
        raise RefusalError(msg)
    return controls[0]


def base_image(table: Path, pg: str) -> str:
    """Name the image the extension was built against, for one major.

    Returns:
        The `[build]` row's digest-pinned reference.

    Raises:
        RefusalError: the table has no row for the major.

    """
    rows = tomllib.loads(table.read_text("utf-8")).get("build", {})
    if pg not in rows:
        msg = f"pg{pg} has no build image in {table}"
        raise RefusalError(msg)
    return rows[pg]


def main(argv: list[str] | None = None) -> int:
    """Run one verb, or refuse with the workflow's error line.

    Returns:
        0 on success, 1 on a refusal.

    """
    parser = argparse.ArgumentParser(prog="pgrx-images.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    staging = verbs.add_parser("stage")
    staging.add_argument("--version", required=True)
    staging.add_argument("--crate-dir", required=True)
    staging.add_argument("--pg", required=True)
    staging.add_argument("--tarballs", default="tarballs")
    staging.add_argument("--out", default="staged")
    render = verbs.add_parser("consumer-dockerfile")
    render.add_argument("--recipe", choices=sorted(RECIPES), required=True)
    render.add_argument("--image", required=True)
    render.add_argument("--base", required=True)
    render.add_argument("--pg", required=True)
    named = verbs.add_parser("extension-name")
    named.add_argument("--tree", type=Path, required=True)
    base = verbs.add_parser("base-image")
    base.add_argument("--pg", required=True)
    base.add_argument("--table", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(run(args))
    except RefusalError as refusal:
        print(f"::error::{refusal}", file=sys.stderr)
        return 1
    return 0


def run(args: argparse.Namespace) -> str:
    """Do the verb the arguments name.

    Returns:
        What the verb prints: a reference, a Dockerfile, or nothing.

    """
    if args.verb == "base-image":
        return base_image(args.table, args.pg) + "\n"
    if args.verb == "extension-name":
        return extension_name(args.tree) + "\n"
    if args.verb == "consumer-dockerfile":
        return consumer_dockerfile(args.recipe, args.image, args.base, args.pg)
    stage(
        Cell(args.crate_dir, args.version, args.pg),
        Path(args.tarballs),
        Path(args.out),
        args.tarballs.rstrip("/"),
    )
    return ""


if __name__ == "__main__":
    sys.exit(main())

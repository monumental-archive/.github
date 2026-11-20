#!/usr/bin/env python3
"""The pgrx release path's one answer to "which release came before?" (#1592).

Every site that needs the previous release asks it here: the Release
PR's derivation (release/generate-pgrx-upgrade.sh), the mint guard
(release/tag-release.sh) and the build's backstop and upgrade execution
(build-pgrx-extension.yml). They used to answer it two ways, the
derivation from the extension's own upgrade graph and the guards from
GitHub's "latest" release with a `v` on its tag, and so could name
different predecessors on the same release, or the guards none at all:
an imported tag scheme (#762), a burned head (#816) and a maintenance
release published after a newer one each split them.

THE PREDECESSOR. The candidates are the versions an `<ext>--<from>--<to>.sql`
in the extension's `sql/` directory upgrades TO that are LOWER than the
version under release, newest first: an upgrade graph is the record of
what was published, where a tag scheme is only a fact about a
repository's past. Lower, not merely other: releasing 1.2.5 on an older
line while 1.3.0 is published, the predecessor is 1.2.4, and a path from
1.3.0 would be a downgrade.
The predecessor is the first candidate a non-draft release carries this
extension's tarballs for, with that release's tag, whatever it is named.
A candidate no release carries burned, or lost its release, and is passed
over. No installable candidate at all is the one meaning of first publish.

Stdlib only, so the logic sits under the coverage and mutation legs
(#1352).

    previous_release.py guard --repo R --version V --crate-dir D
        Refuse a control file that strands existing installations. The
        upgrade path from the predecessor is derived and proven on the
        Release PR by release/generate-pgrx-upgrade.sh, so by the time a
        tag exists the file exists. This guard is the BACKSTOP for a tag
        minted around that path: a missing script ships green while
        stranding every existing installation at ALTER EXTENSION UPDATE,
        so its absence refuses the release. Exit 1 on the refusal, 0 on
        every other path, each one named.

    previous_release.py fetch --repo R --version V --crate-dir D
            --pg PG --arch ARCH --tarball FILE --dest DIR
        Download and extract the predecessor's tarball of this cell, for
        the upgrade execution, and print the predecessor's version on
        stdout. Prints nothing, and says why on stderr, when there is no
        predecessor or it shipped no tarball of this cell: the
        fresh-install smokes are then the whole story.

    previous_release.py predecessor --repo R --version V --control FILE
        Print the predecessor of the extension FILE declares as
        `VERSION<TAB>TAG`, or nothing on a first publish.

    previous_release.py graph [--below V] CONTROL...
        Print the versions any of these extensions' graphs upgrade TO,
        newest first, one per line: nothing is the empty graph. With
        --below, only those lower than V: the predecessor's candidates.

    previous_release.py record --repo R --version V --out FILE CONTROL...
        The derivation's one forge walk: write the installability record
        (#825) for every version each extension's graph names, and print
        `NAME<TAB>VERSION<TAB>TAG` per extension, the two last fields
        empty where it has no predecessor.

The repository is always the workflow's own `GITHUB_REPOSITORY`, passed
in; nothing here names one.
"""

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# The canon's one writer of workflow commands, in mise/ (#1660).
# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mise"))

import workflow_command

# The runner's own, resolved on PATH once.
GH = shutil.which("gh") or "gh"
TAR = shutil.which("tar") or "tar"

# One listing of every non-draft release's assets, a line per asset. The
# filter only maps, never aggregates: `--paginate` runs it over each page
# in turn, and an aggregate would answer once per page (#825).
LISTING_JQ = (
    ".[] | select(.draft | not)"
    " | {tag: .tag_name, asset: .assets[]?.name}"
    ' | "\\(.tag)\\t\\(.asset)"'
)

RECORD_HEADER = """\
# pgrx installable versions -- DERIVED STATE, not a list to edit (#825).
#
# Which extension versions a non-draft release still CARRIES tarballs
# for, and therefore which versions a database can be sitting on.
# Written by release/generate-pgrx-upgrade.sh on every Release PR, from
# the same forge walk that picks the upgrade derivation's predecessor,
# so the two cannot answer differently.
#
# lint:pg-upgrade-path reads this instead of asking the forge, because
# the gate is deterministic. Without it that check cannot tell a burned
# dead end -- a version whose Release PR committed an upgrade script and
# whose publish then failed -- from a published version nothing was
# derived from, whose every installation is stranded. It used to call
# both "burned", which is green and untrue for the second.
#
# The universe here is the upgrade graph: every version named by a
# tracked <ext>--<from>--<to>.sql was asked about, and nothing else was.
# A line names an extension and the versions the forge carried, newest
# first; an extension whose graph is empty is omitted rather than
# recorded as carrying nothing, since it was never asked.
#
# observed: {observed}
# derived: {provenance}
"""


class Predecessor(NamedTuple):
    """The release an installation can be sitting on before this one."""

    version: str
    tag: str


class Graph(NamedTuple):
    """An extension's upgrade graph, each list newest first."""

    targets: list[str]
    versions: list[str]


def version_key(version: str) -> tuple[int, ...]:
    """Order `X.Y.Z` numerically, by its core where it carries a suffix.

    Returns:
        The core's parts as integers: `1.3.0-rc.1` keys as 1.3.0, so
        1.3.0 is not lower than it and 1.2.x is.

    """
    core = version.split("+", 1)[0].split("-", 1)[0]
    return tuple(int(part) for part in core.split("."))


def upgrade_graph(sql_dir: Path, name: str) -> Graph:
    """Read the extension's upgrade graph from its `sql/` directory.

    Returns:
        The versions something upgrades TO, and every version named
        (both halves), newest first; empty where the directory is absent.

    """
    edge = re.compile(
        r"^" + re.escape(name) + r"--(\d+\.\d+\.\d+)--(\d+\.\d+\.\d+)\.sql$"
    )
    targets: set[str] = set()
    versions: set[str] = set()
    entries = sql_dir.iterdir() if sql_dir.is_dir() else ()
    for entry in entries:
        match = edge.match(entry.name)
        if match:
            versions.update(match.groups())
            targets.add(match.group(2))
    return Graph(
        sorted(targets, key=version_key, reverse=True),
        sorted(versions, key=version_key, reverse=True),
    )


def carrier(rows: list[tuple[str, str]], name: str, version: str) -> str | None:
    """Name the non-draft release carrying this version's tarballs.

    Returns:
        The first such release's tag in listing order, or None.

    """
    prefix = f"{name}-{version}-pg"
    return next((tag for tag, asset in rows if asset.startswith(prefix)), None)


def predecessor(
    rows: list[tuple[str, str]], sql_dir: Path, name: str, version: str
) -> Predecessor | None:
    """Answer which release came before `version` of this extension.

    Returns:
        The newest graph target lower than `version` that a non-draft
        release carries, with that release's tag; None when no candidate
        is installable, the one meaning of first publish.

    """
    ceiling = version_key(version)
    for candidate in upgrade_graph(sql_dir, name).targets:
        if version_key(candidate) >= ceiling:
            continue
        tag = carrier(rows, name, candidate)
        if tag is not None:
            return Predecessor(candidate, tag)
    return None


def listing(repo: str) -> tuple[int, list[tuple[str, str]]]:
    """List every non-draft release's assets, one call for all of them.

    Returns:
        `gh`'s status, and `(tag, asset)` per asset. A repository with no
        release answers an empty listing, not an error, so a non-zero
        status is a failed read and never a first publish.

    """
    result = subprocess.run(
        [GH, "api", f"repos/{repo}/releases", "--paginate", "--jq", LISTING_JQ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    rows = []
    for line in result.stdout.splitlines():
        tag, sep, asset = line.partition("\t")
        if sep:
            rows.append((tag, asset))
    return result.returncode, rows


def extension_name(crate_dir: str) -> str:
    """Name the extension: the crate directory's basename, `-` as `_`.

    Returns:
        What `basename DIR | tr - _` printed.

    """
    return crate_dir.rstrip("/").rsplit("/", 1)[-1].replace("-", "_")


def guard(repo: str, version: str, crate_dir: str) -> int:
    """Refuse a release whose predecessor has no upgrade path to it.

    Returns:
        1 on the refusal, 0 on every path that needs no upgrade script,
        or `gh`'s own status where reading the listing failed.

    """
    status, rows = listing(repo)
    if status != 0:
        return status
    name = extension_name(crate_dir)
    if carrier(rows, name, version) is not None:
        workflow_command.notice(f"re-run of {version}; upgrade path not applicable")
        return 0
    prev = predecessor(rows, Path(crate_dir) / "sql", name, version)
    if prev is None:
        workflow_command.notice(
            "no previous release; first publish needs no upgrade path"
        )
        return 0
    upgrade = f"{crate_dir}/sql/{name}--{prev.version}--{version}.sql"
    if not Path(upgrade).is_file():
        workflow_command.error(
            f"no upgrade path {upgrade} from published {prev.version}"
            f" ({prev.tag}) — existing installations would be stranded"
        )
        return 1
    workflow_command.notice(f"upgrade path {prev.version} -> {version} present")
    return 0


class Args(argparse.Namespace):
    """The flags every mode's parser may set, typed as parsed."""

    mode: str
    repo: str
    version: str
    crate_dir: str
    pg: str
    arch: str
    tarball: str
    dest: str
    control: str
    out: str
    below: str | None
    controls: list[str]


def fetch(args: Args) -> int:
    """Fetch and extract the predecessor's tarball of this cell.

    Returns:
        0, having printed the predecessor's version where it was fetched;
        `gh`'s status where the listing failed, tar's where extracting
        failed.

    """
    status, rows = listing(args.repo)
    if status != 0:
        return status
    name = extension_name(args.crate_dir)
    prev = None
    if carrier(rows, name, args.version) is None:
        prev = predecessor(rows, Path(args.crate_dir) / "sql", name, args.version)
    if prev is None:
        workflow_command.notice(
            "no previous release; upgrade execution skipped", stream=sys.stderr
        )
        return 0
    asset = f"{name}-{prev.version}-pg{args.pg}-linux-{args.arch}.tar.gz"
    download = subprocess.run(
        [
            GH,
            "release",
            "download",
            prev.tag,
            "--repo",
            args.repo,
            "--pattern",
            asset,
            "--output",
            args.tarball,
        ],
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if download.returncode != 0:
        workflow_command.notice(
            f"{prev.tag} shipped no {asset}; upgrade execution skipped",
            stream=sys.stderr,
        )
        return 0
    Path(args.dest).mkdir(parents=True, exist_ok=True)
    extract = subprocess.run([TAR, "-xzf", args.tarball, "-C", args.dest], check=False)
    if extract.returncode != 0:
        return extract.returncode
    print(prev.version)
    return 0


def control_parts(control: str) -> tuple[Path, str]:
    """Split a control file's path into its `sql/` directory and name.

    Returns:
        The sibling `sql/` directory, and the extension's name.

    """
    path = Path(control)
    return path.parent / "sql", path.name.removesuffix(".control")


def answer(args: Args) -> int:
    """Print the predecessor of the extension a control file declares.

    Returns:
        0, or `gh`'s status where the listing failed.

    """
    status, rows = listing(args.repo)
    if status != 0:
        return status
    prev = predecessor(rows, *control_parts(args.control), args.version)
    if prev is not None:
        print(f"{prev.version}\t{prev.tag}")
    return 0


def record(args: Args) -> int:
    """Write the installability record and print each predecessor.

    Returns:
        0, or `gh`'s status where the listing failed.

    """
    status, rows = listing(args.repo)
    if status != 0:
        return status
    lines: dict[str, str] = {}
    answers = []
    for control in args.controls:
        sql_dir, name = control_parts(control)
        graph = upgrade_graph(sql_dir, name)
        if graph.versions:
            carried = [v for v in graph.versions if carrier(rows, name, v)]
            lines[name] = " ".join([name, *carried])
        prev = predecessor(rows, sql_dir, name, args.version)
        answers.append("\t".join([name, *(prev or ("", ""))]))
    header = RECORD_HEADER.format(
        observed=dt.datetime.now(dt.UTC).date().isoformat(),
        provenance=f"v{args.version}",
    )
    body = "\n".join(lines[name] for name in sorted(lines)) + "\n"
    Path(args.out).write_text(header + body, encoding="utf-8")
    print("\n".join(answers))
    return 0


def targets(args: Args) -> int:
    """Print the union of the extensions' graph targets, newest first.

    Returns:
        0.

    """
    union: set[str] = set()
    for control in args.controls:
        union.update(upgrade_graph(*control_parts(control)).targets)
    ceiling = version_key(args.below) if args.below else None
    for version in sorted(union, key=version_key, reverse=True):
        if ceiling is None or version_key(version) < ceiling:
            print(version)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the mode and its flags, and answer.

    Returns:
        The mode's exit status.

    """
    parser = argparse.ArgumentParser(prog="previous_release.py")
    sub = parser.add_subparsers(dest="mode", required=True)
    graph = sub.add_parser("graph")
    graph.add_argument("--below")
    graph.add_argument("controls", nargs="+")
    for mode in ("guard", "fetch", "predecessor", "record"):
        p = sub.add_parser(mode)
        p.add_argument("--repo", required=True)
        p.add_argument("--version", required=True)
        if mode in {"guard", "fetch"}:
            p.add_argument("--crate-dir", required=True)
        if mode == "fetch":
            for flag in ("--pg", "--arch", "--tarball", "--dest"):
                p.add_argument(flag, required=True)
        if mode == "predecessor":
            p.add_argument("--control", required=True)
        if mode == "record":
            p.add_argument("--out", required=True)
            p.add_argument("controls", nargs="+")
    args = parser.parse_args(argv, namespace=Args())
    modes = {"fetch": fetch, "graph": targets, "predecessor": answer, "record": record}
    if args.mode == "guard":
        return guard(args.repo, args.version, args.crate_dir)
    return modes[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())

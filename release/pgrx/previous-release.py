#!/usr/bin/env python3
"""The pgrx-extension build's questions about the previous release (#1380).

Both run on the runner, before and after the containers, and read the
previous release through `gh` exactly as the workflow's inline shell
did. Stdlib only, so the logic sits under the coverage and mutation
legs (#1352).

    previous-release.py guard --repo R --version V --crate-dir D
        Refuse a control file that strands existing installations. The
        upgrade path from the previous published version is derived and
        proven on the Release PR by release/generate-pgrx-upgrade.sh, so
        by the time a tag exists the file exists. This guard is the
        BACKSTOP for a tag minted around that path — a missing script
        ships green while stranding every existing installation at
        ALTER EXTENSION UPDATE, so its absence refuses the release.
        Exit 1 on the refusal, 0 on every other path, each one named.

    previous-release.py fetch --repo R --version V --crate-dir D
            --pg PG --arch ARCH --tarball FILE --dest DIR
        Download and extract the previous release's tarball of this
        cell, for the upgrade execution, and print the previous version
        on stdout. Prints nothing, and says why on stderr, when no
        previous release shipped this cell: the fresh-install smokes are
        then the whole story.

The repository is always the workflow's own `GITHUB_REPOSITORY`, passed
in; nothing here names one.
"""

import argparse
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

# The runner's own, resolved on PATH once.
GH = shutil.which("gh") or "gh"
TAR = shutil.which("tar") or "tar"


def latest_tag(repo: str) -> str:
    """Ask `gh` for the newest release's tag, as the workflow did.

    Returns:
        The tag, or "" where `gh` answers nothing or fails: a repository
        with no release is answered by an error, and that is the first
        publish, not a defect.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    result = subprocess.run(
        [
            GH,
            "release",
            "view",
            "--repo",
            repo,
            "--json",
            "tagName",
            "--jq",
            ".tagName",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def extension_name(crate_dir: str) -> str:
    """Name the extension: the crate directory's basename, `-` as `_`.

    Returns:
        What `basename DIR | tr - _` printed.

    """
    return crate_dir.rstrip("/").rsplit("/", 1)[-1].replace("-", "_")


def guard(repo: str, version: str, crate_dir: str) -> int:
    """Refuse a release whose previous version has no upgrade path to it.

    Returns:
        1 on the refusal, 0 on every path that needs no upgrade script,
        or `gh`'s own status where reading the previous assets failed.

    """
    prev = latest_tag(repo)
    if not prev.startswith("v"):
        print("::notice::no previous release; first publish needs no upgrade path")
        return 0
    prev = prev.removeprefix("v")
    if prev == version:
        print(f"::notice::re-run of {prev}; upgrade path not applicable")
        return 0
    name = extension_name(crate_dir)
    # Only installations that can exist can be stranded: the upgrade path
    # is required only when the previous release actually shipped this
    # extension. A repository adding the class mid-life has a previous
    # release and no previous extension.
    jq = f'[.assets[].name | select(startswith("{name}-{prev}-pg"))] | length'
    # ruff: ignore[subprocess-without-shell-equals-true]
    shipped = subprocess.run(
        [
            GH,
            "release",
            "view",
            f"v{prev}",
            "--repo",
            repo,
            "--json",
            "assets",
            "--jq",
            jq,
        ],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if shipped.returncode != 0:
        return shipped.returncode
    if shipped.stdout.strip() == "0":
        print(
            f"::notice::v{prev} shipped no {name} tarballs; first publish of"
            " this extension, no upgrade path needed"
        )
        return 0
    upgrade = f"{crate_dir}/sql/{name}--{prev}--{version}.sql"
    if not Path(upgrade).is_file():
        print(
            f"::error::no upgrade path {upgrade} from published {prev}"
            " — existing installations would be stranded"
        )
        return 1
    print(f"::notice::upgrade path {prev} -> {version} present")
    return 0


def fetch(args: argparse.Namespace) -> int:
    """Fetch and extract the previous release's tarball of this cell.

    Returns:
        0, having printed the previous version where it was fetched;
        tar's own status where extracting it failed.

    """
    prev = latest_tag(args.repo)
    if not prev.startswith("v") or prev.removeprefix("v") == args.version:
        print(
            "::notice::no previous release; upgrade execution skipped", file=sys.stderr
        )
        return 0
    prev = prev.removeprefix("v")
    name = extension_name(args.crate_dir)
    asset = f"{name}-{prev}-pg{args.pg}-linux-{args.arch}.tar.gz"
    # ruff: ignore[subprocess-without-shell-equals-true]
    download = subprocess.run(
        [
            GH,
            "release",
            "download",
            f"v{prev}",
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
        print(
            f"::notice::v{prev} shipped no {asset}; upgrade execution skipped",
            file=sys.stderr,
        )
        return 0
    Path(args.dest).mkdir(parents=True, exist_ok=True)
    # ruff: ignore[subprocess-without-shell-equals-true]
    extract = subprocess.run([TAR, "-xzf", args.tarball, "-C", args.dest], check=False)
    if extract.returncode != 0:
        return extract.returncode
    print(prev)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the mode and its flags, and answer.

    Returns:
        The mode's exit status.

    """
    parser = argparse.ArgumentParser(prog="previous-release.py")
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("guard", "fetch"):
        p = sub.add_parser(mode)
        p.add_argument("--repo", required=True)
        p.add_argument("--version", required=True)
        p.add_argument("--crate-dir", required=True)
        if mode == "fetch":
            for flag in ("--pg", "--arch", "--tarball", "--dest"):
                p.add_argument(flag, required=True)
    args = parser.parse_args(argv)
    if args.mode == "guard":
        return guard(args.repo, args.version, args.crate_dir)
    return fetch(args)


if __name__ == "__main__":
    sys.exit(main())

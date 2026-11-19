#!/usr/bin/env python3
"""Report lock entries whose asset is not the architecture they promise.

A mise lock records, per platform, the exact URL and checksum a tool
installs from. Nothing checks that the asset it names is built for the
platform the key declares, and aqua's `rosetta2` makes the disagreement
easy to reach: a package whose registry entry predates its first arm64
build keeps resolving the amd64 asset forever, so `macos-arm64` carries
an x86_64 binary's checksum (#811). That is a pin describing a platform
other than the one it names, and it is silent — the install succeeds,
the binary runs under emulation, and the lock looks complete.

Reads lock paths on stdin, one per line. Prints one TAB-separated
candidate per disagreeing entry:

    lock  tool  platform  declared  asset  repo  tag  native_regex

The caller resolves `native_regex` against the release's real asset list
to separate the two cases this cannot tell apart offline: a native asset
EXISTS and was not chosen (a defect), or upstream ships none for that
platform and emulation is the only thing on offer (not a defect). That
second case is why this is an `audit:*` and not a `lint:*` — the answer
lives in a release listing, not in the diff.

Prints nothing when every entry agrees.
"""

import pathlib
import sys
import tomllib

# Architecture tokens as they appear in release asset names, longest
# first so `x86_64` is decided before `x86` and `aarch64` before `arm`.
# The family is the equivalence class we compare: an asset is wrong for
# a platform when its family differs, not when its spelling does.
ARCH_TOKENS: tuple[tuple[str, str], ...] = (
    ("x86_64", "x64"),
    ("x86-64", "x64"),
    ("aarch64", "arm64"),
    ("aarch_64", "arm64"),
    ("universal", "universal"),
    ("amd64", "x64"),
    ("arm64", "arm64"),
    ("armv7", "arm32"),
    ("armv6", "arm32"),
    ("win64", "x64"),
    ("64bit", "x64"),
    ("i686", "x86"),
    ("i386", "x86"),
    ("x64", "x64"),
    ("x86", "x86"),
)

# Spellings that count as native for a family, used to build the regex
# the caller matches against a release's asset names.
ARCH_SPELLINGS: dict[str, tuple[str, ...]] = {
    "x64": ("x86_64", "x86-64", "amd64", "x64"),
    "arm64": ("aarch64", "arm64"),
    "arm32": ("armv7", "armv6"),
    "x86": ("i686", "i386"),
}

OS_SPELLINGS: dict[str, tuple[str, ...]] = {
    "macos": ("apple-darwin", "darwin", "macos", "osx"),
    "linux": ("linux",),
    "windows": ("pc-windows", "windows", "win"),
}

# The architecture half of a mise platform key (`macos-arm64`,
# `linux-x64-musl`). Only these name an architecture; `musl` and the
# like are ABI suffixes and are ignored.
KEY_ARCH: dict[str, str] = {
    "x64": "x64",
    "arm64": "arm64",
    "x86": "x86",
    "armv7": "arm32",
}


def asset_arch(asset_name: str) -> str | None:
    """Architecture family named by an asset filename.

    Returns:
        The family, or None when the name carries no architecture at
        all — a Windows zip built for one architecture and not saying
        so, or a single macOS tarball with no sibling to choose
        between. Judging those would invent a disagreement.

    """
    low = asset_name.lower()
    best: tuple[int, str] | None = None
    for token, family in ARCH_TOKENS:
        if token not in low:
            continue
        if best is None or len(token) > best[0]:
            best = (len(token), family)
    return None if best is None else best[1]


def platform_families(platform_key: str) -> tuple[str, str] | None:
    """Split a lock platform key into its OS and architecture families.

    Returns:
        The (os, arch) pair, or None when either half is unrecognised —
        an unknown key is not evidence of anything.

    """
    parts = platform_key.split("-")
    if not parts:
        return None
    os_family = parts[0]
    if os_family not in OS_SPELLINGS:
        return None
    for part in parts[1:]:
        if part in KEY_ARCH:
            return (os_family, KEY_ARCH[part])
    return None


def native_regex(os_family: str, arch_family: str) -> str:
    """Build an ERE matching an asset native to this OS and architecture.

    Both orders are accepted because release naming does not agree on
    one: `wasm-pack-v0.15.0-aarch64-apple-darwin.tar.gz` puts the
    architecture first, `actionlint_1.7.12_darwin_arm64.tar.gz` puts it
    last.

    Returns:
        An extended regular expression, unanchored.

    """
    arch = "|".join(ARCH_SPELLINGS[arch_family])
    plat = "|".join(OS_SPELLINGS[os_family])
    return f"({arch}).*({plat})|({plat}).*({arch})"


def release_source(url: str) -> tuple[str, str] | None:
    """Recover the owner/repo and tag a release download URL came from.

    Returns:
        The (repo, tag) pair, or None when the URL is not a GitHub
        release download — a tool served from elsewhere has no asset
        list to consult.

    """
    marker = "/releases/download/"
    if "://github.com/" not in url or marker not in url:
        return None
    head, tail = url.split(marker, 1)
    repo = head.split("://github.com/", 1)[1]
    if repo.count("/") != 1 or "/" not in tail:
        return None
    return (repo, tail.rsplit("/", 1)[0])


def entry_line(
    lock_path: pathlib.Path, tool: str, key: str, value: object
) -> str | None:
    """Judge one platform entry of one tool.

    Returns:
        A candidate line when the asset contradicts the key, or None —
        which covers agreement and every case that cannot be judged at
        all: a key that is not a platform, an entry with no URL, an
        unrecognised platform, an asset naming no architecture, and a
        download that is not a GitHub release.

    """
    if not key.startswith("platforms.") or not isinstance(value, dict):
        return None
    url = value.get("url")
    if not isinstance(url, str) or not url:
        return None
    platform_key = key.removeprefix("platforms.")
    families = platform_families(platform_key)
    if families is None:
        return None
    os_family, arch_family = families
    asset = url.rsplit("/", 1)[-1]
    found = asset_arch(asset)
    # `universal` is a deliberate single build covering every
    # architecture, not a disagreement.
    if found is None or found in {"universal", arch_family}:
        return None
    source = release_source(url)
    if source is None:
        return None
    repo, tag = source
    return "\t".join((
        str(lock_path),
        tool,
        platform_key,
        f"{os_family}/{arch_family}",
        asset,
        repo,
        tag,
        native_regex(os_family, arch_family),
    ))


def candidates(lock_path: pathlib.Path) -> list[str]:
    """Find every entry in one lock whose asset contradicts its key.

    Returns:
        Ready-to-print TAB-separated candidate lines.

    """
    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"lock-arch: cannot read {lock_path}: {exc}", file=sys.stderr)
        return []
    lines: list[str] = []
    for tool, entries in sorted(data.get("tools", {}).items()):
        blocks = entries if isinstance(entries, list) else [entries]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key, value in sorted(block.items()):
                line = entry_line(lock_path, tool, key, value)
                if line is not None:
                    lines.append(line)
    return lines


def main() -> int:
    """Emit a candidate line for every disagreeing lock entry on stdin.

    Returns:
        0 always — finding a candidate is a report, not a failure. The
        caller decides, once it has asked the release what it publishes.

    """
    for raw in sys.stdin:
        name = raw.strip()
        if not name:
            continue
        path = pathlib.Path(name)
        if not path.is_file():
            continue
        for line in candidates(path):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

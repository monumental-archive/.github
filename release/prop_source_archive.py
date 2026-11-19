#!/usr/bin/env python3
"""source_archive.py, the source-archive build, on any repository name.

The property of `test_source_archive.py`'s fixed rows (#1556), over
generated owners, repository names and versions. `git` is replaced
in-process: `archive` writes one fixed tarball, `ls-files` lists as
many paths as it holds.

- V: the asset is dist/<name>-<VERSION>.tar.gz, archived under the
  prefix <name>-<VERSION>/ from the tag v<VERSION>, where <name> is the
  repository's name after the owner with exactly one leading dot
  stripped: `.github` ships `github`, `..x` ships `.x`, and a name with
  no leading dot ships as it is.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

import source_archive as source
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

# GitHub's own alphabets: an owner is letters, digits and hyphens; a
# repository name adds dots and underscores, and is never `.` or `..`.
OWNER = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9-]{0,10}", fullmatch=True)
NAME = st.from_regex(r"[A-Za-z0-9._-]{1,12}", fullmatch=True).filter(
    lambda n: n not in {".", ".."}
)
VERSION = st.from_regex(
    r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(-[a-z0-9.]{1,6})?", fullmatch=True
)


def tarball() -> bytes:
    """Make one small gzip tarball, the same bytes every call.

    Returns:
        Its bytes.

    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tar:
        for i in range(2):
            info = tarfile.TarInfo(f"p/file{i}")
            info.mtime = 0
            tar.addfile(info, io.BytesIO(b""))
    return buf.getvalue()


ARCHIVE = tarball()


def run_build(repository: str, version: str) -> tuple[str, list[list[str]], list[str]]:
    """Run the build for one repository and version.

    Returns:
        The notice line, every `git archive` call's arguments, and the
        names dist/ holds.

    """
    calls: list[list[str]] = []

    def git(*args: str) -> str:
        if args[0] == "ls-files":
            return "a\nb\n"
        calls.append(list(args))
        Path(args[args.index("-o") + 1]).write_bytes(ARCHIVE)
        return ""

    source.git = git
    here = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            notice = source.build({"VERSION": version, "GITHUB_REPOSITORY": repository})
        finally:
            os.chdir(here)
        dist = sorted(p.name for p in (Path(tmp) / "dist").iterdir())
    return notice, calls, dist


class AssetName(unittest.TestCase):
    """V: the name after the owner, one leading dot stripped."""

    @given(OWNER, NAME, VERSION)
    @example("monumental-archive", ".github", "1.70.2")
    @example("o", "..x", "1.0.0")
    @example("o", "x.", "1.0.0")
    def test_one_leading_dot_stripped(
        self, owner: str, name: str, version: str
    ) -> None:
        """The asset, its prefix and the tag follow the stripped name."""
        # ruff: ignore[slice-to-remove-prefix-or-suffix] - the model, written apart from the helper's removeprefix
        shipped = name[1:] if name.startswith(".") else name
        base = f"{shipped}-{version}"
        notice, calls, dist = run_build(f"{owner}/{name}", version)
        self.assertEqual(dist, [f"{base}.tar.gz"])
        self.assertTrue(notice.startswith(f"{base}.tar.gz: "), notice)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn(f"--prefix={base}/", call)
            self.assertEqual(call[-1], f"v{version}")


if __name__ == "__main__":
    unittest.main()

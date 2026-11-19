#!/usr/bin/env python3
"""Tests for deny_skips.py's one-entry variants (#1555).

The regression rows of the defect `prop_deny_skips.py` found: a
variant dropped every line naming the crate, not the one skip entry.
Two skips of one crate at two versions each lost both, so a needless
one of the pair was never caught; and a `deny` entry for the crate was
dropped beside its skip.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import runpy
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ds = SimpleNamespace(**runpy.run_path(str(HERE / "deny_skips.py")))

TWO_VERSIONS = """\
[bans]
skip = [
  { crate = "windows-sys@0.48", reason = "a" },
  { crate = "windows-sys@0.52", reason = "b" },
]
"""
DENIED_TOO = """\
[bans]
deny = [
  { crate = "openssl", wrappers = ["native-tls"] },
]
skip = [
  "openssl@0.10",
]
"""


def variants(text: str) -> list[tuple[str, dict]]:
    """Run the helper on a deny.toml.

    Returns:
        Each printed spec with its variant's `[bans]` table.

    Raises:
        AssertionError: the helper refused the file.

    """
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "deny.toml"
        path.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = ds.main(["deny_skips.py", str(path)])
        if status != 0:
            msg = f"deny_skips.py exited {status}"
            raise AssertionError(msg)
        found = []
        for line in out.getvalue().splitlines():
            spec, variant = line.split("\t")
            found.append((
                spec,
                tomllib.loads(Path(variant).read_text("utf-8"))["bans"],
            ))
    return found


class OneEntry(unittest.TestCase):
    """Each variant is missing its own entry and nothing else."""

    def test_two_versions_of_one_crate_are_two_variants(self) -> None:
        """Each version's variant keeps the other version's skip."""
        got = variants(TWO_VERSIONS)
        self.assertEqual(
            [(spec, [i["crate"] for i in bans["skip"]]) for spec, bans in got],
            [
                ("windows-sys@0.48", ["windows-sys@0.52"]),
                ("windows-sys@0.52", ["windows-sys@0.48"]),
            ],
        )

    def test_a_deny_entry_for_the_crate_is_kept(self) -> None:
        """Only the skip goes; the deny entry stays."""
        [(spec, bans)] = variants(DENIED_TOO)
        self.assertEqual(spec, "openssl@0.10")
        self.assertEqual(bans["skip"], [])
        self.assertEqual([d["crate"] for d in bans["deny"]], ["openssl"])


if __name__ == "__main__":
    unittest.main()

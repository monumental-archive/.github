#!/usr/bin/env python3
"""Tests for citation.sh's refusals: each absent field says which (#911).

`citation.sh` reads three fields out of `REUSE.toml` with `grep`, and
`grep` exits 1 when nothing matches — the very "this field is absent"
answer each guard was written to report. Under `set -euo pipefail` the
status left the pipeline and killed the script before the guard ran,
so a maintainer filling in a stub got a mute exit 1 from
`fix:citation`. Each row plants a `REUSE.toml` missing one field and
asserts the guard's own sentence, not only the exit status.

The script under test is the belt's own, run by bash in a temporary
directory. stdlib `unittest`, matching the other mise/test_*.py; run
through the gate as `mise run test`.
"""

import importlib.util
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


SCRIPT = Path(__file__).resolve().with_name("citation.sh")
BASH = shutil.which("bash") or "bash"

LICENCE = '[[annotations]]\npath = "**"\nSPDX-License-Identifier = "0BSD"\n'
SUPPLIER = 'SPDX-PackageSupplier = "A Maintainer <a@example.invalid>"\n'
LOCATION = (
    'SPDX-PackageDownloadLocation = "https://github.com/monumental-archive/demo"\n'
)


def run(reuse: str) -> subprocess.CompletedProcess[str]:
    """Run citation.sh in a directory holding only this REUSE.toml.

    Returns:
        The completed process; a refusal is the thing under test.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "REUSE.toml").write_text("version = 1\n" + reuse, encoding="utf-8")
        env = hermetic.env()
        env["CITATION_OUT"] = str(root / "out.cff")
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class AbsentFieldIsNamed(unittest.TestCase):
    """A missing field is a sentence, not a mute exit 1 (#911)."""

    def test_no_licence_expression_is_named(self) -> None:
        """No licence identifier at all: the licence guard speaks."""
        done = run(SUPPLIER + LOCATION)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml declares no licence expression"
            " — fill it first (lint:licence)\n",
            done.stderr,
        )

    def test_no_supplier_is_named(self) -> None:
        """No SPDX-PackageSupplier: the author guard speaks."""
        done = run(LOCATION + LICENCE)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml carries no SPDX-PackageSupplier"
            " — the citation needs an author\n",
            done.stderr,
        )

    def test_no_download_location_is_named(self) -> None:
        """No SPDX-PackageDownloadLocation: the repository guard speaks."""
        done = run(SUPPLIER + LICENCE)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml carries no SPDX-PackageDownloadLocation\n",
            done.stderr,
        )

    def test_all_three_fields_render(self) -> None:
        """The baseline: with every field present nothing is refused."""
        done = run(SUPPLIER + LOCATION + LICENCE)
        self.assertEqual(0, done.returncode, done.stderr)


if __name__ == "__main__":
    unittest.main()

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

import subprocess
import tempfile
import unittest
from pathlib import Path

import hermetic

SCRIPT = Path(__file__).resolve().with_name("citation.sh")
BASH = hermetic.tool("bash")

LICENCE = '[[annotations]]\npath = "**"\nSPDX-License-Identifier = "0BSD"\n'
SUPPLIER = 'SPDX-PackageSupplier = "A Maintainer <a@example.invalid>"\n'
LOCATION = (
    'SPDX-PackageDownloadLocation = "https://github.com/monumental-archive/demo"\n'
)


def run(
    reuse: str, states: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run citation.sh in a directory holding this REUSE.toml.

    `states`, when given, is planted as `.badge-states`.

    Returns:
        The completed process, and the render ("" if none); a refusal
        is the thing under test.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "REUSE.toml").write_text("version = 1\n" + reuse, encoding="utf-8")
        if states is not None:
            (root / ".badge-states").write_text(states, encoding="utf-8")
        # The task's name, as mise gives it to the script's helpers (#1979).
        env = hermetic.env(MISE_TASK_NAME="fix:citation")
        env["CITATION_OUT"] = str(root / "out.cff")
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        out = root / "out.cff"
        return done, out.read_text(encoding="utf-8") if out.exists() else ""


class AbsentFieldIsNamed(unittest.TestCase):
    """A missing field is a sentence, not a mute exit 1 (#911)."""

    def test_no_licence_expression_is_named(self) -> None:
        """No licence identifier at all: the licence guard speaks."""
        done, _ = run(SUPPLIER + LOCATION)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml declares no licence expression"
            " — fill it first (lint:licence)\n",
            done.stderr,
        )

    def test_no_supplier_is_named(self) -> None:
        """No SPDX-PackageSupplier: the author guard speaks."""
        done, _ = run(LOCATION + LICENCE)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml carries no SPDX-PackageSupplier"
            " — the citation needs an author\n",
            done.stderr,
        )

    def test_no_download_location_is_named(self) -> None:
        """No SPDX-PackageDownloadLocation: the repository guard speaks."""
        done, _ = run(SUPPLIER + LICENCE)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml carries no SPDX-PackageDownloadLocation\n",
            done.stderr,
        )

    def test_all_three_fields_render(self) -> None:
        """The baseline: with every field present nothing is refused."""
        done, _ = run(SUPPLIER + LOCATION + LICENCE)
        self.assertEqual(0, done.returncode, done.stderr)


# Two annotations: the project's own, and a copied fixture's (#1589).
TWO = (
    LICENCE
    + '\n[[annotations]]\npath = "fixture.crate"\n'
    + 'SPDX-License-Identifier = "MIT OR Apache-2.0"\n'
)


class SeveralExpressions(unittest.TestCase):
    """The citation states the licence the badge shield states (#1589)."""

    def test_the_declared_expression_is_the_citations(self) -> None:
        """With a `licence` line, the citation carries that expression."""
        done, cff = run(SUPPLIER + LOCATION + TWO, "licence 0BSD\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("\nlicense: 0BSD\n", cff)

    def test_the_other_declared_expression_is_the_citations(self) -> None:
        """The line decides, not the order of the annotations."""
        done, cff = run(SUPPLIER + LOCATION + TWO, "licence MIT OR Apache-2.0\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("\nlicense: MIT OR Apache-2.0\n", cff)

    def test_several_without_a_declaration_are_refused(self) -> None:
        """No line: refused, naming the line to add, as the badges do."""
        done, cff = run(SUPPLIER + LOCATION + TWO)
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: REUSE.toml names several licence expressions:\n"
            "  0BSD\n  MIT OR Apache-2.0\n"
            "  say which is the project's in .badge-states:"
            " 'licence <SPDX expression>'\n",
            done.stderr,
        )
        self.assertEqual("", cff)

    def test_a_declaration_reuse_does_not_name_is_red(self) -> None:
        """A licence no file is under is not the project's."""
        done, cff = run(SUPPLIER + LOCATION + TWO, "licence MIT\n")
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: .badge-states declares licence MIT,"
            " and REUSE.toml names no such expression:\n"
            "  0BSD\n  MIT OR Apache-2.0\n",
            done.stderr,
        )
        self.assertEqual("", cff)

    def test_a_declaration_is_held_with_one_expression_too(self) -> None:
        """One expression and a line that contradicts it: red, not ignored."""
        done, _ = run(SUPPLIER + LOCATION + LICENCE, "licence MIT\n")
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "fix:citation: .badge-states declares licence MIT,"
            " and REUSE.toml names no such expression:\n  0BSD\n",
            done.stderr,
        )


class TheRenderMatchesTheStamp(unittest.TestCase):
    """The render byte-matches what a release stamped (#327)."""

    def test_the_canons_stamped_citation_renders_unchanged(self) -> None:
        """The canon's own files: the release wrote date-released bare.

        The stamper rewrites the line unquoted at every release, so a
        render that quoted it reddened lint:citation on every release
        pull request (measured on canon v1.20.0's).
        """
        canon = Path(__file__).resolve().parent.parent
        stamped = (canon / "CITATION.cff").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            # The canon's REUSE.toml names two expressions (#1589), so
            # its licence is the `licence` line beside it.
            root = hermetic.canon(
                tmp, "REUSE.toml", ".badge-states", stubs={"CITATION.cff": stamped}
            )
            env = hermetic.env()
            env["CITATION_OUT"] = str(root / "out.cff")
            done = subprocess.run(
                [BASH, str(SCRIPT)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual(stamped, (root / "out.cff").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Every type the release notes map is a type committed allows (#55).

committed's own default type list omits `build`, `ci` and `revert`, and
the release notes group all three, so a config that leaves the list
unstated rejects at the gate exactly the commits the release machinery
expects: a correct `ci:` commit went red in two repositories in one
session. The list is stated in the delivered mise/committed.toml.

Each row runs the belt's own mise/committed.sh, with the belt directory
as ORG_BELT_DIR, on a one-line commit message, and committed runs for
real. The types are read from the `groups` convention in
release/prepare-release.sh, the one statement of what the notes map, so
a type added there and not here goes red by name.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
PREPARE = BELT.parent / "release" / "prepare-release.sh"
BASH = hermetic.tool("bash")


def mapped_types() -> list[str]:
    """Read the commit types the release notes convention maps.

    Returns:
        Each type once, scope dropped, in the order the convention
        states them.

    """
    pairs = ""
    for line in PREPARE.read_text(encoding="utf-8").splitlines():
        found = re.fullmatch(r'groups\+?="([^"]*)"', line)
        if found:
            pairs += found.group(1)
    types: list[str] = []
    for pair in pairs.split(","):
        kind = re.sub(r"\(.*\)", "", pair.split("=")[0])
        if kind and kind not in types:
            types.append(kind)
    return types


def check(subject: str) -> subprocess.CompletedProcess[str]:
    """Run committed.sh on a one-line commit message.

    Returns:
        The completed process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "COMMIT_EDITMSG"
        message.write_text(subject + "\n", encoding="utf-8")
        return subprocess.run(
            [BASH, str(BELT / "committed.sh"), "--commit-file", str(message)],
            cwd=tmp,
            env=hermetic.env(ORG_BELT_DIR=str(BELT)),
            capture_output=True,
            text=True,
            check=False,
        )


class MappedTypesAreAllowed(unittest.TestCase):
    """A type the notes group is a type the gate passes (#55)."""

    def test_the_convention_maps_the_three_the_default_omits(self) -> None:
        """The reader finds build, ci and revert, so the rows below run."""
        self.assertLessEqual({"build", "ci", "revert"}, set(mapped_types()))

    def test_every_mapped_type_passes_the_gate(self) -> None:
        """Each mapped type, as a whole subject, is accepted by committed."""
        for kind in mapped_types():
            with self.subTest(type=kind):
                done = check(f"{kind}: carry the thing")
                self.assertNotIn(f"Disallowed type `{kind}`", done.stdout + done.stderr)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_an_unmapped_type_is_still_refused(self) -> None:
        """The list is a list: a type nobody maps stays red, by name."""
        done = check("bogus: carry the thing")
        self.assertEqual(1, done.returncode)
        self.assertIn("error Disallowed type `bogus` used", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()

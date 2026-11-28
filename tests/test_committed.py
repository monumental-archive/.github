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
"""

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import support

BELT = support.MISE
PREPARE = BELT.parent / "release" / "prepare-release.sh"
BASH = support.tool("bash")


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
            env=support.env(ORG_BELT_DIR=str(BELT)),
            capture_output=True,
            text=True,
            check=False,
        )


class TestMappedTypesAreAllowed:
    """A type the notes group is a type the gate passes (#55)."""

    def test_the_convention_maps_the_three_the_default_omits(self) -> None:
        """The reader finds build, ci and revert, so the rows below run."""
        assert {"build", "ci", "revert"} <= set(mapped_types())

    @pytest.mark.parametrize("kind", mapped_types())
    def test_every_mapped_type_passes_the_gate(self, kind: object) -> None:
        """Each mapped type, as a whole subject, is accepted by committed."""
        done = check(f"{kind}: carry the thing")
        assert f"Disallowed type `{kind}`" not in done.stdout + done.stderr
        assert done.returncode == 0, done.stdout + done.stderr

    def test_an_unmapped_type_is_still_refused(self) -> None:
        """The list is a list: a type nobody maps stays red, by name."""
        done = check("bogus: carry the thing")
        assert done.returncode == 1
        assert "error Disallowed type `bogus` used" in done.stdout + done.stderr

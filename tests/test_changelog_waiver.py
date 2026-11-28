"""A scaffolded changelog passes lint:markdown with its entries verbatim (#173).

Changelog entries are commit subjects carried verbatim, with a link to
their pull request, and they run past rumdl's 80 columns. A recorded
subject's length is a fact about history, not prose to reflow, so the
changelog header carries an MD013 waiver. It was first written into the
generator's header; the generator retired (#507) and the header a new
repository starts from is now scaffold/CHANGELOG.md, which the release
path writes beneath.

The row joins the scaffold's preamble to one real release section from
this repository's own CHANGELOG.md and runs rumdl, as lint:markdown
does, with the delivered mise/rumdl.toml. The section is v1.23.1's,
because rumdl already passes a long line whose overflow is the pull
request link: its entry for #353 runs 148 columns with the subject
alone past 80, which is the shape the waiver exists for.
"""

import re
import subprocess
import tempfile
from pathlib import Path

from tests import support

BELT = support.MISE
CANON = BELT.parent
RUMDL = support.tool("rumdl")


SECTION = "1.23.1"
# rumdl's MD013 line length, the default the delivered config keeps.
WIDTH = 80


def section() -> str:
    """Read one release section of this repository's CHANGELOG.md.

    Returns:
        From SECTION's `## [` heading up to the next heading.

    """
    text = (CANON / "CHANGELOG.md").read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"^## \[", text, flags=re.MULTILINE)]
    head = text.index(f"## [{SECTION}]")
    return text[head : min(s for s in starts if s > head)]


# The waiver in the scaffold's header covers the entries below it.
def test_the_section_is_long_enough_to_need_the_waiver() -> None:
    """The captured section has a subject past 80 before its link."""
    subjects = [line.split(" ([#")[0] for line in section().splitlines()]
    assert any(len(subject) > WIDTH for subject in subjects)


def test_a_release_section_under_the_scaffold_header_lints_clean(
    env: support.Env,
) -> None:
    """No MD013 for a verbatim subject under the scaffold's preamble."""
    preamble = (CANON / "scaffold" / "CHANGELOG.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        changelog = Path(tmp) / "CHANGELOG.md"
        changelog.write_text(
            preamble.rstrip("\n") + "\n\n" + section().rstrip("\n") + "\n",
            encoding="utf-8",
        )
        done = subprocess.run(
            [RUMDL, "check", "--config", str(BELT / "rumdl.toml"), "CHANGELOG.md"],
            cwd=tmp,
            env=env(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert "MD013" not in done.stdout + done.stderr
        assert done.returncode == 0, done.stdout + done.stderr

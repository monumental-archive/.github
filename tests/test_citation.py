"""Tests for citation.sh's refusals: each absent field says which (#911).

`citation.sh` reads three fields out of `REUSE.toml` with `grep`, and
`grep` exits 1 when nothing matches — the very "this field is absent"
answer each guard was written to report. Under `set -euo pipefail` the
status left the pipeline and killed the script before the guard ran,
so a maintainer filling in a stub got a mute exit 1 from
`fix:citation`. Each row plants a `REUSE.toml` missing one field and
asserts the guard's own sentence, not only the exit status.

The script under test is the belt's own, run by bash in a temporary
directory.
"""

import subprocess
import tempfile
from pathlib import Path

from tests import support

SCRIPT = support.MISE / "citation.sh"
BASH = support.tool("bash")
GIT = support.tool("git")

LICENCE = '[[annotations]]\npath = "**"\nSPDX-License-Identifier = "0BSD"\n'
SUPPLIER = 'SPDX-PackageSupplier = "A Maintainer <a@example.invalid>"\n'
LOCATION = (
    'SPDX-PackageDownloadLocation = "https://github.com/monumental-archive/demo"\n'
)


def track(root: Path, env: dict[str, str]) -> None:
    """Make `root` a repository tracking every file in it (#2013)."""
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run([GIT, *argv], cwd=root, env=env, check=True)


def run(
    reuse: str, states: str | None = None, *, env: support.Env
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
        environ = env()
        track(root, environ)
        environ["CITATION_OUT"] = str(root / "out.cff")
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=root,
            env=environ,
            capture_output=True,
            text=True,
            check=False,
        )
        out = root / "out.cff"
        return done, out.read_text(encoding="utf-8") if out.exists() else ""


# A missing field is a sentence, not a mute exit 1 (#911).
def test_no_licence_expression_is_named(env: support.Env) -> None:
    """No licence identifier at all: the licence guard speaks."""
    done, _ = run(SUPPLIER + LOCATION, env=env)
    assert done.returncode == 1
    assert done.stderr == (
        "fix:citation: REUSE.toml declares no licence expression"
        " — fill it first (lint:licence)\n"
    )


def test_no_supplier_is_named(env: support.Env) -> None:
    """No SPDX-PackageSupplier: the author guard speaks."""
    done, _ = run(LOCATION + LICENCE, env=env)
    assert done.returncode == 1
    assert done.stderr == (
        "fix:citation: REUSE.toml carries no SPDX-PackageSupplier"
        " — the citation needs an author\n"
    )


def test_no_download_location_is_named(env: support.Env) -> None:
    """No SPDX-PackageDownloadLocation: the repository guard speaks."""
    done, _ = run(SUPPLIER + LICENCE, env=env)
    assert done.returncode == 1
    assert (
        done.stderr
        == "fix:citation: REUSE.toml carries no SPDX-PackageDownloadLocation\n"
    )


def test_all_three_fields_render(env: support.Env) -> None:
    """The baseline: with every field present nothing is refused."""
    done, _ = run(SUPPLIER + LOCATION + LICENCE, env=env)
    assert done.returncode == 0, done.stderr


# Two annotations: the project's own, and a copied fixture's (#1589).
TWO = (
    LICENCE
    + '\n[[annotations]]\npath = "fixture.crate"\n'
    + 'SPDX-License-Identifier = "MIT OR Apache-2.0"\n'
)


# The citation states the licence the badge shield states (#1589).
def test_the_declared_expression_is_the_citations(env: support.Env) -> None:
    """With a `licence` line, the citation carries that expression."""
    done, cff = run(SUPPLIER + LOCATION + TWO, "licence 0BSD\n", env=env)
    assert done.returncode == 0, done.stderr
    assert "\nlicense: 0BSD\n" in cff


def test_the_other_declared_expression_is_the_citations(env: support.Env) -> None:
    """The line decides, not the order of the annotations."""
    done, cff = run(SUPPLIER + LOCATION + TWO, "licence MIT OR Apache-2.0\n", env=env)
    assert done.returncode == 0, done.stderr
    assert "\nlicense: MIT OR Apache-2.0\n" in cff


def test_several_without_a_declaration_are_refused(env: support.Env) -> None:
    """No line: refused, naming the line to add, as the badges do."""
    done, cff = run(SUPPLIER + LOCATION + TWO, env=env)
    assert done.returncode == 1
    assert done.stderr == (
        "fix:citation: REUSE.toml names several licence expressions:\n"
        "  0BSD\n  MIT OR Apache-2.0\n"
        "  say which is the project's in .badge-states:"
        " 'licence <SPDX expression>'\n"
    )
    assert not cff


def test_a_declaration_reuse_does_not_name_is_red(env: support.Env) -> None:
    """A licence no file is under is not the project's."""
    done, cff = run(SUPPLIER + LOCATION + TWO, "licence MIT\n", env=env)
    assert done.returncode == 1
    assert done.stderr == (
        "fix:citation: .badge-states declares licence MIT,"
        " and REUSE.toml names no such expression:\n"
        "  0BSD\n  MIT OR Apache-2.0\n"
    )
    assert not cff


def test_a_declaration_is_held_with_one_expression_too(env: support.Env) -> None:
    """One expression and a line that contradicts it: red, not ignored."""
    done, _ = run(SUPPLIER + LOCATION + LICENCE, "licence MIT\n", env=env)
    assert done.returncode == 1
    assert done.stderr == (
        "fix:citation: .badge-states declares licence MIT,"
        " and REUSE.toml names no such expression:\n  0BSD\n"
    )


# The render byte-matches what a release stamped (#327).
def test_the_canons_stamped_citation_renders_unchanged(
    canon: support.Canon, env: support.Env
) -> None:
    """The canon's own files: the release wrote date-released bare.

    The stamper rewrites the line unquoted at every release, so a
    render that quoted it reddened lint:citation on every release
    pull request (measured on canon v1.20.0's).
    """
    canon_dir = support.CANON
    stamped = (canon_dir / "CITATION.cff").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        # The canon's REUSE.toml names two expressions (#1589), so
        # its licence is the `licence` line beside it.
        root = canon(
            tmp, "REUSE.toml", ".badge-states", stubs={"CITATION.cff": stamped}
        )
        environ = env()
        track(root, environ)
        environ["CITATION_OUT"] = str(root / "out.cff")
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=root,
            env=environ,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert stamped == (root / "out.cff").read_text(encoding="utf-8")

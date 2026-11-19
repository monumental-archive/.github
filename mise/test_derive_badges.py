#!/usr/bin/env python3
"""Table tests for derive-badges.sh's registry images (#1303).

A repository that publishes its image through the continuous archetype
has no publish stub and so no classes, and wore no `ghcr` shield.
The generator now reads a third source of image names: a tracked stub
at the policy's `continuous.stubPath` whose `uses:` calls the canon's
continuous.yml (`continuous.stubUses` followed by that path). The
image is the repository's name, lowercased — continuous.yml's own rule.

The script under test is the belt's own `derive-badges.sh`, run by bash
in a planted git tree, and the planted stubs are the canon's real
templates, so neither the predicate nor the templates can drift from
these rows. The policy is the canon's real `slsa/assert-policy.json`
except in the row that plants one without the `continuous` block.

Each red row is red-when-planted and asserts the message, not just the
exit status: a planted row scored on exit alone can pass for the wrong
reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)


CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
SCRIPT = BELT / "derive-badges.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")

POLICY = json.loads((CANON / "slsa" / "assert-policy.json").read_text(encoding="utf-8"))
CONT_PATH = POLICY["evidence"]["continuous"]["stubPath"]
CONT_STUB = (CANON / "workflow-templates" / "continuous.yml").read_text(
    encoding="utf-8"
)
PUBLISH_STUB = (CANON / "workflow-templates" / "publish.yml").read_text(
    encoding="utf-8"
)

DB = "monumental-archive-db"

# The policy guard's message, whole: the path varies with the tree.
POLICY_REFUSAL = (
    r"\Afix:badges: \S+/slsa/assert-policy\.json declares no "
    r"evidence\.continuous stubPath and stubUses\n\Z"
)


def shield(img: str) -> str:
    """Build the ghcr shield line the script renders for one image.

    Returns:
        The markdown line, without its newline.

    """
    esc = img.replace("-", "--")
    return (
        f"[![ghcr {img}](https://img.shields.io/badge/ghcr.io-"
        f"monumental--archive%2F{esc}-blue)]"
        f"(https://github.com/orgs/monumental-archive/packages/container/"
        f"package/{img})"
    )


def publish(classes: str) -> str:
    """Return the canon's publish template declaring other classes.

    Returns:
        The stub text with its `classes:` line swapped.

    Raises:
        ValueError: the template no longer carries the line, so the row
            would plant nothing.

    """
    old = "      classes: rust-crate\n"
    if PUBLISH_STUB.count(old) != 1:
        message = f"the publish template no longer carries exactly one {old!r}"
        raise ValueError(message)
    return PUBLISH_STUB.replace(old, f"      classes: {classes}\n")


def render(
    files: dict[str, str],
    repo: str = DB,
    untracked: tuple[str, ...] = (),
    policy: dict | None = None,
    *,
    canon: bool = True,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Render the badge block for one planted tree.

    Returns:
        The finished process and the rendered block ("" if none).

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tree"
        root.mkdir()
        reuse = (
            "version = 1\n"
            f'SPDX-PackageDownloadLocation = "https://github.com/'
            f'monumental-archive/{repo}"\n\n'
            "[[annotations]]\n"
            'path = "**"\n'
            'SPDX-License-Identifier = "0BSD"\n'
        )
        planted = {"REUSE.toml": reuse, **files}
        for name, text in planted.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        if policy is not None:
            fake = Path(tmp) / "canon"
            (fake / "slsa").mkdir(parents=True)
            (fake / "slsa" / "assert-policy.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            env["ORG_CANON_DIR"] = str(fake)
        if not canon:
            env.pop("ORG_CANON_DIR")
        git = [GIT, "-C", str(root)]
        subprocess.run([*git, "init", "-q"], check=True, env=env)  # ruff: ignore[subprocess-without-shell-equals-true]
        tracked = [n for n in planted if n not in untracked]
        subprocess.run([*git, "add", "--", *tracked], check=True, env=env)  # ruff: ignore[subprocess-without-shell-equals-true]
        out = Path(tmp) / "block.md"
        env["BADGES_OUT"] = str(out)
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        block = out.read_text(encoding="utf-8") if out.exists() else ""
        return done, block


def ghcr_lines(block: str) -> list[str]:
    """Return the block's ghcr shield lines.

    Returns:
        Every line that names a ghcr image.

    """
    return [ln for ln in block.splitlines() if ln.startswith("[![ghcr ")]


class Images(unittest.TestCase):
    """Which ghcr shields a tree's stubs imply."""

    def assert_block(self, files: dict[str, str], **kw: object) -> str:
        """Assert a clean render and hand back the block.

        Returns:
            The rendered block.

        """
        done, block = render(files, **kw)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        return block

    def test_continuous_stub_alone_renders_the_image_once(self) -> None:
        """A continuous publisher with no publish stub wears its image."""
        block = self.assert_block({CONT_PATH: CONT_STUB})
        self.assertEqual(ghcr_lines(block), [shield(DB)])
        self.assertEqual(
            block.count("ghcr.io-monumental--archive%2Fmonumental--archive--db"), 1
        )
        # The registry dot of fair-software fills with the image.
        self.assertIn("fair--software.eu-%E2%97%8F%20%E2%97%8F%20%E2%97%8F%20", block)

    def test_continuous_and_oci_image_render_the_image_once(self) -> None:
        """Both sources naming the repository's image render it once."""
        block = self.assert_block({
            CONT_PATH: CONT_STUB,
            ".github/workflows/publish.yml": publish("oci-image"),
        })
        self.assertEqual(ghcr_lines(block), [shield(DB)])

    def test_continuous_image_is_the_repository_lowercased(self) -> None:
        """continuous.yml lowercases GITHUB_REPOSITORY; so does the shield."""
        block = self.assert_block({CONT_PATH: CONT_STUB}, repo="Mixed-Case")
        self.assertEqual(ghcr_lines(block), [shield("mixed-case")])

    def test_continuous_and_a_ghcr_state_render_both(self) -> None:
        """A `ghcr <name>` state still adds an image; the derived one once."""
        block = self.assert_block({
            CONT_PATH: CONT_STUB,
            ".badge-states": f"ghcr {DB}\nghcr other-image\n",
        })
        self.assertEqual(ghcr_lines(block), [shield(DB), shield("other-image")])

    def test_an_extension_alone_renders_its_crate_image_not_the_repo(self) -> None:
        """A pgrx extension's image is named for its crate dir (#1172).

        edtf ships the extension alone and wore a ghcr shield for an
        image `edtf` that has never existed: the repository name was
        emitted for pgrx-extension as if it were oci-image.
        """
        stub = publish("pgrx-extension").replace(
            "      classes: pgrx-extension\n",
            "      classes: pgrx-extension\n"
            "      extension-crate-dir: crates/edtf-postgres\n",
        )
        block = self.assert_block({".github/workflows/publish.yml": stub}, repo="edtf")
        self.assertEqual([shield("edtf-postgres")], ghcr_lines(block))

    def test_no_stub_renders_no_image(self) -> None:
        """A tree with neither stub wears no ghcr shield and no registry dot."""
        block = self.assert_block({})
        self.assertEqual(ghcr_lines(block), [])
        self.assertIn("fair--software.eu-%E2%97%8F%20%E2%97%8F%20%E2%97%8B%20", block)

    def test_stub_path_calling_another_workflow_renders_no_image(self) -> None:
        """The file at stubPath counts only when it calls continuous.yml."""
        other = CONT_STUB.replace("workflows/continuous.yml@", "workflows/ci.yml@")
        self.assertNotEqual(other, CONT_STUB)
        block = self.assert_block({CONT_PATH: other})
        self.assertEqual(ghcr_lines(block), [])

    def test_commented_call_renders_no_image(self) -> None:
        """A `uses:` behind a comment is prose, not a call."""
        commented = CONT_STUB.replace("    uses: ", "    # uses: ")
        self.assertNotEqual(commented, CONT_STUB)
        block = self.assert_block({CONT_PATH: commented})
        self.assertEqual(ghcr_lines(block), [])

    def test_untracked_stub_renders_no_image(self) -> None:
        """An untracked continuous stub publishes nothing and wears nothing."""
        block = self.assert_block({CONT_PATH: CONT_STUB}, untracked=(CONT_PATH,))
        self.assertEqual(ghcr_lines(block), [])

    def test_stub_elsewhere_renders_no_image(self) -> None:
        """The stub is located by the policy's path, not by any filename."""
        block = self.assert_block({".github/workflows/deploy.yml": CONT_STUB})
        self.assertEqual(ghcr_lines(block), [])


class Refusals(unittest.TestCase):
    """The guards, each red-when-planted with its exact message."""

    def test_pgrx_guard_is_not_satisfied_by_the_continuous_image(self) -> None:
        """A continuous image cannot stand in for extension-crate-dir."""
        done, _ = render({
            CONT_PATH: CONT_STUB,
            ".github/workflows/publish.yml": publish("pgrx-extension"),
        })
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            "fix:badges: classes include pgrx-extension but the publish stub "
            "names no extension-crate-dir\n",
        )

    def test_policy_without_continuous_block_refuses(self) -> None:
        """A policy that no longer declares the predicate is refused by name."""
        policy = json.loads(json.dumps(POLICY))
        del policy["evidence"]["continuous"]
        done, _ = render({CONT_PATH: CONT_STUB}, policy=policy)
        self.assertEqual(done.returncode, 1)
        self.assertRegex(done.stderr, POLICY_REFUSAL)

    def test_policy_missing_stub_uses_refuses(self) -> None:
        """Half a predicate is no predicate."""
        policy = json.loads(json.dumps(POLICY))
        del policy["evidence"]["continuous"]["stubUses"]
        done, _ = render({CONT_PATH: CONT_STUB}, policy=policy)
        self.assertEqual(done.returncode, 1)
        self.assertRegex(done.stderr, POLICY_REFUSAL)

    def test_no_canon_dir_refuses(self) -> None:
        """No ORG_CANON_DIR means no belt, said by name."""
        done, _ = render({CONT_PATH: CONT_STUB}, canon=False)
        self.assertEqual(done.returncode, 1)
        self.assertRegex(
            done.stderr,
            r"ORG_CANON_DIR: unset — no belt \(CI: MISE_GLOBAL_CONFIG_FILE; "
            r"local: conf\.d symlink\)\n\Z",
        )


class FirstMatch(unittest.TestCase):
    """A first-line read survives a long input under pipefail (#747, #682)."""

    def test_a_long_level_stub_still_renders_its_tracks(self) -> None:
        """`| head -1` broke the pipe and killed the script without a word.

        Under `set -o pipefail` the writer `head` hangs up on dies of
        SIGPIPE once its output outruns the pipe buffer, and the whole
        assignment fails. `sed -n 1p` reads to the end and cannot break
        the pipe. 4,000 `tracks:` lines put the writer past 64 KiB.
        """
        stub = (
            "jobs:\n  level:\n"
            "    uses: monumental-archive/.github/.github/workflows/level.yml@v1\n"
            "    with:\n" + "      tracks: build source dependency\n" * 4000
        )
        done, block = render({".github/workflows/levels.yml": stub})
        self.assertEqual(done.returncode, 0, f"exit {done.returncode}: {done.stderr}")
        self.assertIn("[![SLSA build]", block)


class AbsentFieldIsNamed(unittest.TestCase):
    """A read whose answer may be "none" reaches its guard (#905)."""

    def test_no_download_location_says_so(self) -> None:
        """`grep` finding nothing used to kill the script before its sentence.

        Under `set -euo pipefail` grep's exit 1 left the pipeline and
        errexit fired, so fix:badges exited 1 with nothing on stderr.
        """
        reuse = 'version = 1\n[[annotations]]\npath = "**"\n'
        done, _ = render({"REUSE.toml": reuse})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            "fix:badges: REUSE.toml carries no SPDX-PackageDownloadLocation\n",
            done.stderr,
        )


def reuse_with(*expressions: str) -> str:
    """Return a REUSE.toml for the db repository with these annotations.

    Returns:
        The file's text, one annotation per expression.

    """
    head = (
        "version = 1\n"
        f'SPDX-PackageDownloadLocation = "https://github.com/monumental-archive/{DB}"\n'
    )
    return head + "".join(
        f'\n[[annotations]]\npath = "p{i}/**"\nSPDX-License-Identifier = "{e}"\n'
        for i, e in enumerate(expressions)
    )


class LicenceShield(unittest.TestCase):
    """The licence shield is derived from REUSE.toml, never hand-written (#1174).

    A shield written by hand below the block was read only by the
    Monday audit, so it could state a licence the repository does not
    carry for a week. The block now derives it.
    """

    def test_the_shield_states_the_expression_reuse_declares(self) -> None:
        """One expression across the annotations is the shield's message."""
        done, block = render({"REUSE.toml": reuse_with("Apache-2.0 OR MIT")})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(
            "[![license](https://img.shields.io/badge/license-"
            "Apache--2.0%20OR%20MIT-blue)](LICENSES/)",
            block,
        )

    def test_two_expressions_are_refused_by_name(self) -> None:
        """Annotations that disagree leave the shield nothing true to say."""
        done, _ = render({"REUSE.toml": reuse_with("0BSD", "MIT")})
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(
            "fix:badges: REUSE.toml names several licence expressions:\n"
            "  0BSD\n  MIT\n",
            done.stderr,
        )


class CoverageShield(unittest.TestCase):
    """The coverage shield is derived from the floor, never declared (#362)."""

    def test_a_declared_state_without_a_floor_renders_no_shield(self) -> None:
        """`coverage exemplary` once wore a shield over no data at all.

        The shield renders iff `.coverage-floor` exists, so a state line
        saying otherwise renders nothing and is not refused.
        """
        done, block = render({".badge-states": "coverage exemplary\n"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertNotIn("codecov.io", block)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""lint:signer-pin holds the signer identity to one statement (#317).

The signer was stated twice: in every `uses:` line that calls sign.yml,
and in security/signer.pin, which the verifier read. The two had no
Renovate manager in common, so a signer bump moved them on separate
branches, each red against the other until a human joined them, and
the gate stuck. #317 deleted the file and made the `uses:` lines the
single statement; the lint now refuses the second copy coming back,
and any workflow that still reads it.

Each row runs the task's own `run` body, read from mise/config.toml,
in a planted git tree, with the belt as ORG_BELT_DIR and this
repository as ORG_CANON_DIR, so the identity it reads is the real one.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
CANON = BELT.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
SHA = "0123456789abcdef0123456789abcdef01234567"


def body() -> str:
    """Read lint:signer-pin's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks["lint:signer-pin"], "lint:signer-pin")
    return decoded.as_str(task["run"], "lint:signer-pin's run")


def signer() -> str:
    """Read the signer workflow the identity declares.

    Returns:
        The `signer-workflow` field of security/identity.toml.

    """
    identity = decoded.load_toml((CANON / "security" / "identity.toml").read_bytes())
    return decoded.as_str(identity["signer-workflow"], "signer-workflow")


def workflow(extra: str = "") -> str:
    """Render a workflow whose one job calls the signer at SHA.

    Returns:
        The workflow text.

    """
    return f"on: push\njobs:\n  sign:\n    uses: {signer()}@{SHA} # main\n" + extra


def lint(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the task's body in a git tree tracking these files.

    Returns:
        The completed process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "repo"
        tree.mkdir()
        env = hermetic.env(
            root / "home", ORG_BELT_DIR=str(BELT), ORG_CANON_DIR=str(CANON)
        )
        for name, text in files.items():
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "add", "--", *files], cwd=tree, env=env, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body()],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class OneStatementOfTheSigner(unittest.TestCase):
    """The `uses:` lines are the signer identity, and nothing else is (#317)."""

    def test_the_uses_lines_alone_pass(self) -> None:
        """The baseline: one digest, no pin file."""
        done = lint({".github/workflows/sign.yml": workflow()})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            f"lint:signer-pin: all signer references agree on {SHA}\n", done.stdout
        )

    def test_a_pin_file_beside_them_is_refused(self) -> None:
        """security/signer.pin back, even agreeing, is the second copy."""
        done = lint({
            ".github/workflows/sign.yml": workflow(),
            "security/signer.pin": f"{SHA}\n",
        })
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "lint:signer-pin: security/signer.pin exists — the second copy of the"
            " signer identity was deleted (#316 finding 2)\n",
            done.stderr,
        )

    def test_a_workflow_reading_the_pin_is_refused(self) -> None:
        """A step still reading the deleted file is named."""
        step = "      - run: cat security/signer.pin\n"
        reader = workflow("  verify:\n    steps:\n" + step)
        done = lint({".github/workflows/sign.yml": reader})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "lint:signer-pin: workflows read security/signer.pin (on a non-comment"
            " line) but the file is deleted",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()

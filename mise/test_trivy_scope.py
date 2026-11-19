#!/usr/bin/env python3
"""lint:trivy scans tracked files, never the canon checked out beside them (#86).

In CI the shared workflows check the canon out INTO the workspace, and
trivy walks directories rather than tracked files, so every repository's
gate scanned the belt's own Dockerfiles and failed on findings whose
`.trivyignore` lives in the canon. The task now copies the tracked set
into a scratch tree and points trivy there.

The row runs the task's own `run` body, read from mise/config.toml, in a
planted git tree holding one tracked file and an untracked checkout
beside it. trivy is a stub that records every file in the tree it was
given: the row is about what reaches the scanner, and trivy's verdict on
a file is trivy's to prove.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).resolve().parent
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"

# Records the files under the directory trivy was asked to scan.
TRIVY = """\
#!/usr/bin/env bash
set -euo pipefail
target="${@: -1}"
(cd "${target}" && find . -type f -not -path "./.git/*" | sort) > "${TRIVY_LOG}"
"""


def body() -> str:
    """Read lint:trivy's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"]["lint:trivy"]["run"]


class TrivyScansTrackedFilesOnly(unittest.TestCase):
    """The checked-out toolbelt never reaches the scanner (#86)."""

    def test_an_untracked_checkout_is_not_scanned(self) -> None:
        """Only the tracked Dockerfile is handed to trivy."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "repo"
            tree.mkdir()
            env = hermetic.env(root / "home")
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
            (tree / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run([GIT, "add", "Dockerfile"], cwd=tree, env=env, check=True)
            toolbelt = tree / ".org-toolbelt"
            toolbelt.mkdir()
            (toolbelt / "Dockerfile").write_text("FROM ubuntu\n", encoding="utf-8")
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "trivy").write_text(TRIVY, encoding="utf-8")
            (bindir / "trivy").chmod(0o755)
            log = root / "trivy.log"
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env["TRIVY_LOG"] = str(log)
            # ruff: ignore[subprocess-without-shell-equals-true]
            done = subprocess.run(
                [BASH, "-euo", "pipefail", "-c", body()],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual("./Dockerfile\n", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

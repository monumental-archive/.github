#!/usr/bin/env python3
"""Run release/crates-io-members.sh over each shape of Cargo's `publish` key.

#1310: the rust-crate class's build and publisher once carried two
answers to "which crates does this release ship"; this helper is now
the one answer both run. Its rows are the key's four shapes as cargo
metadata reports them - measured on cargo 1.97.1 against a fixture
workspace: unset is `null`, `publish = false` is `[]`, a registry list
is the list - plus a package that is not a workspace member, which the
helper must not name.

WHAT RUNS. The helper as shipped, under bash, with a stub `cargo` first
on PATH answering `metadata` with a document shaped like cargo's. `jq`
is the real one.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
HELPER = CANON / "release" / "crates-io-members.sh"
BASH = shutil.which("bash")

CARGO_STUB = """#!/usr/bin/env bash
# Stub `cargo`: logs its arguments, answers `metadata` from STUB_METADATA.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
[[ $1 == metadata ]] || { echo "cargo stub: refusing $*" >&2; exit 101; }
cat "${STUB_METADATA}"
"""


def run_helper(
    packages: dict[str, list[str] | None], members: tuple[str, ...]
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the helper over a workspace with these `publish` keys.

    Args:
        packages: each package's `publish` key, as cargo metadata has it.
        members: which of them are workspace members.

    Returns:
        The completed run and the stub's argument log.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "bin").mkdir()
        (tmp / "bin" / "cargo").write_text(CARGO_STUB)
        (tmp / "bin" / "cargo").chmod(0o755)
        ids = {n: f"path+file:///work/{n}#0.1.0" for n in packages}
        (tmp / "metadata.json").write_text(
            json.dumps({
                "packages": [
                    {"name": n, "version": "0.1.0", "id": ids[n], "publish": key}
                    for n, key in packages.items()
                ],
                "workspace_members": [ids[n] for n in members],
            })
        )
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(HELPER)],
            env={
                **os.environ,
                "PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
                "STUB_LOG": str(tmp / "cargo.log"),
                "STUB_METADATA": str(tmp / "metadata.json"),
            },
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )
        return result, (tmp / "cargo.log").read_text().splitlines()


class OnePublishKeyPerRow(unittest.TestCase):
    """Each shape of the key, alone in a workspace."""

    def test_the_key_decides(self) -> None:
        """Unset and a list naming crates-io ship; false and elsewhere do not."""
        rows = (
            ("unset", None, "fx-unset\t0.1.0\n"),
            ("false", [], ""),
            ("crates-io", ["crates-io"], "fx-crates-io\t0.1.0\n"),
            ("among others", ["x", "crates-io"], "fx-among-others\t0.1.0\n"),
            ("another registry only", ["x"], ""),
        )
        for label, key, expected in rows:
            with self.subTest(label):
                name = "fx-" + label.replace(" ", "-")
                result, _ = run_helper({name: key}, (name,))
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout)


class TheWholeWorkspace(unittest.TestCase):
    """Every shape at once, plus a package outside the workspace."""

    def test_members_that_publish_to_crates_io_in_metadata_order(self) -> None:
        """Only workspace members, only crates.io's, name TAB version."""
        result, calls = run_helper(
            {
                "fx-unset": None,
                "fx-false": [],
                "fx-crates-io": ["crates-io"],
                "fx-elsewhere": ["x"],
                "fx-outside": None,
            },
            ("fx-unset", "fx-false", "fx-crates-io", "fx-elsewhere"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("fx-unset\t0.1.0\nfx-crates-io\t0.1.0\n", result.stdout)
        self.assertEqual(["metadata --format-version 1 --no-deps --locked"], calls)

    def test_a_cargo_failure_fails_the_helper(self) -> None:
        """An unread workspace is never an empty one."""
        with TemporaryDirectory() as d:
            stub = Path(d) / "cargo"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "echo 'error: lock file needs updating' >&2\n"
                "exit 101\n"
            )
            stub.chmod(0o755)
            result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [BASH, str(HELPER)],
                env={**os.environ, "PATH": f"{d}{os.pathsep}{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
                cwd=d,
            )
        self.assertEqual(101, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("error: lock file needs updating\n", result.stderr)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""lint:rust lints every workspace member, past a red one (#726).

Linted as one workspace, the first crate clippy refused ended the run:
the members after it were never linted, and their findings surfaced
only once the first was fixed, one crate per round trip. lint:rust now
lints each member as its own package and reports every red crate by
name, with cargo's own exit status.

The row runs the task's own `run` body, read from mise/config.toml, in
a planted git tree. The workspace is edtf's, from the `cargo metadata`
captured in release/testdata/edtf-cargo-metadata.json and read by the
belt's real mise/gate-surface.py. cargo and rustup are stubs: clippy
refuses the first member and passes the rest, and the stub records each
crate it was asked to lint. clippy's verdict on a crate is clippy's to
prove; the row is about which crates are asked.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import runpy
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

hermetic = SimpleNamespace(
    **runpy.run_path(str(Path(__file__).resolve().with_name("hermetic.py")))
)


BELT = Path(__file__).resolve().parent
METADATA = BELT.parent / "release" / "testdata" / "edtf-cargo-metadata.json"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
MEMBERS = [
    "edtf-core",
    "edtf-calendars",
    "edtf-normalize",
    "edtf-wasm",
    "edtf-cli",
    "edtf-postgres",
]

CARGO = """\
#!/usr/bin/env bash
case "$1" in
  fmt | fetch) ;;
  metadata) cat "${STUB_METADATA}" ;;
  clippy)
    echo "$3" >> "${CLIPPY_LOG}"
    if [[ $3 == edtf-core@* ]]; then
      echo "error: planted finding in $3"
      exit 101
    fi
    ;;
  *) exit 1 ;;
esac
"""

RUSTUP = """\
#!/usr/bin/env bash
printf 'clippy-x86_64-unknown-linux-gnu\\nrustfmt-x86_64-unknown-linux-gnu\\n'
"""


def body() -> str:
    """Read lint:rust's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    with (BELT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)["tasks"]["lint:rust"]["run"]


class EveryMemberIsLinted(unittest.TestCase):
    """One red crate does not hide the rest (#726)."""

    def test_a_red_first_member_leaves_the_rest_linted(self) -> None:
        """All six members are asked; the red one is named; cargo's 101 is kept."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "repo"
            (tree / "src").mkdir(parents=True)
            (tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
            (tree / "src" / "lib.rs").write_text("\n", encoding="utf-8")
            bindir = root / "bin"
            bindir.mkdir()
            for name, text in (("cargo", CARGO), ("rustup", RUSTUP)):
                (bindir / name).write_text(text, encoding="utf-8")
                (bindir / name).chmod(0o755)
            log = root / "clippy.log"
            env = hermetic.env(root / "home", ORG_BELT_DIR=str(BELT))
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
            # ruff: ignore[subprocess-without-shell-equals-true]
            subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env["STUB_METADATA"] = str(METADATA)
            env["CLIPPY_LOG"] = str(log)
            # ruff: ignore[subprocess-without-shell-equals-true]
            done = subprocess.run(
                [BASH, "-euo", "pipefail", "-c", body()],
                cwd=tree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            asked = log.read_text(encoding="utf-8").split()
        self.assertEqual(101, done.returncode, done.stdout + done.stderr)
        self.assertEqual([f"{m}@1.3.3" for m in MEMBERS], asked)
        self.assertIn(
            f"lint:rust: 6 crate(s) linted: {' '.join(MEMBERS)}\n", done.stdout
        )
        self.assertIn("lint:rust: red: edtf-core\n", done.stderr)


if __name__ == "__main__":
    unittest.main()

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
"""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import support

CANON = support.CANON
HELPER = CANON / "release" / "crates-io-members.sh"
BASH = support.tool("bash")

CARGO_STUB = """#!/usr/bin/env bash
# Stub `cargo`: logs its arguments, answers `metadata` from STUB_METADATA.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
[[ $1 == metadata ]] || { echo "cargo stub: refusing $*" >&2; exit 101; }
cat "${STUB_METADATA}"
"""


def run_helper(
    packages: dict[str, list[str] | None], members: tuple[str, ...], *, env: support.Env
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
        listed: list[dict[str, object]] = [
            {"name": n, "version": "0.1.0", "id": ids[n], "publish": key}
            for n, key in packages.items()
        ]
        metadata: dict[str, object] = {
            "packages": listed,
            "workspace_members": [ids[n] for n in members],
        }
        (tmp / "metadata.json").write_text(json.dumps(metadata))
        result = subprocess.run(
            [BASH, str(HELPER)],
            env=env(
                PATH=f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
                STUB_LOG=str(tmp / "cargo.log"),
                STUB_METADATA=str(tmp / "metadata.json"),
            ),
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )
        return result, (tmp / "cargo.log").read_text().splitlines()


# Each shape of the key, alone in a workspace.
def test_the_key_decides(env: support.Env) -> None:
    """Unset and a list naming crates-io ship; false and elsewhere do not."""
    rows: tuple[tuple[str, list[str] | None, str], ...] = (
        ("unset", None, "fx-unset\t0.1.0\n"),
        ("false", [], ""),
        ("crates-io", ["crates-io"], "fx-crates-io\t0.1.0\n"),
        ("among others", ["x", "crates-io"], "fx-among-others\t0.1.0\n"),
        ("another registry only", ["x"], ""),
    )
    for label, key, expected in rows:
        name = "fx-" + label.replace(" ", "-")
        result, _ = run_helper({name: key}, (name,), env=env)
        assert result.returncode == 0, result.stderr
        assert expected == result.stdout


# Every shape at once, plus a package outside the workspace.
def test_members_that_publish_to_crates_io_in_metadata_order(env: support.Env) -> None:
    """Only workspace members, only crates.io's, name TAB version."""
    packages: dict[str, list[str] | None] = {
        "fx-unset": None,
        "fx-false": [],
        "fx-crates-io": ["crates-io"],
        "fx-elsewhere": ["x"],
        "fx-outside": None,
    }
    result, calls = run_helper(
        packages, ("fx-unset", "fx-false", "fx-crates-io", "fx-elsewhere"), env=env
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "fx-unset\t0.1.0\nfx-crates-io\t0.1.0\n"
    want: list[str] = ["metadata --format-version 1 --no-deps --locked"]
    assert want == calls


def test_a_cargo_failure_fails_the_helper(env: support.Env) -> None:
    """An unread workspace is never an empty one."""
    with TemporaryDirectory() as d:
        stub = Path(d) / "cargo"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "echo 'error: lock file needs updating' >&2\n"
            "exit 101\n"
        )
        stub.chmod(0o755)
        result = subprocess.run(
            [BASH, str(HELPER)],
            env=env(PATH=f"{d}{os.pathsep}{os.environ['PATH']}"),
            capture_output=True,
            text=True,
            check=False,
            cwd=d,
        )
    assert result.returncode == 101
    assert result.stdout == ""
    assert result.stderr == "error: lock file needs updating\n"

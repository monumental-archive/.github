"""lint:msrv holds every tracked workspace, not only the root's (#2011).

Each row runs the task's helper, belt.msrv_lint's `main`, in this
process, standing in a planted git tree of two workspaces: the root `Cargo.toml` (crate
`app`) and `fuzz/Cargo.toml` (crate `app-fuzz`), each its own root, as
a cargo-fuzz crate is. `cargo`, `rustup` and `mise` are stubs on PATH:
cargo answers `locate-project` with the manifest it was given, and
`metadata` with the planted document for the `--manifest-path` it was
given, and logs every call; mise lists one pinned rust, 1.85.0. The
belt is the real one, and so are belt.rust_workspaces, belt.msrv_lint
and belt.msrv_plan; `gate-surface` stands in by monkeypatch and answers
no extension. The floor is the canon's own mise/rust-floor.toml, 1.85.

Before #2011 the body read `cargo metadata` at the root alone, so the
fuzz crate was held to nothing: each row fails there.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom; every environment
is built from support.env (#1542).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from belt import gate_surface, msrv_lint, rust_workspaces
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# The planted tree's roots, each checked.
ROOTS = 2

BELT = support.MISE
TASK = "lint:msrv"
GIT = support.tool("git")
FUZZ = "fuzz/Cargo.toml"

CARGO_STUB = r"""#!/usr/bin/env bash
where=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  [[ ${args[i]} != --manifest-path ]] || where=${args[i + 1]}
done
line="cargo $*"
[[ -z ${RUSTUP_TOOLCHAIN:-} ]] || line="${line} {${RUSTUP_TOOLCHAIN}}"
printf '%s\n' "${line}" >> "${PLANT_LOG}"
case $1 in
  locate-project) echo "$(pwd -P)/${where}" ;;
  metadata) cat "${PLANT_DIR}/${where//\//_}.json" ;;
  check) [[ ${where} != "${PLANT_REFUSE:-}" ]] || exit 101 ;;
esac
exit 0
"""
MISE_STUB = r"""#!/usr/bin/env bash
printf 'mise %s\n' "$*" >> "${PLANT_LOG}"
echo '[{"version": "1.85.0", "active": true, "installed": true}]'
"""
RUSTUP_STUB = r"""#!/usr/bin/env bash
printf 'rustup %s\n' "$*" >> "${PLANT_LOG}"
exit 99
"""


def no_extension(_argv: list[str]) -> int:
    """Stand in for gate-surface: read the metadata, answer no extension.

    Returns:
        0, as the real one does for a tree with no pgrx crate.

    """
    sys.stdin.read()
    return 0


def document(name: str, promise: str | None) -> str:
    """Build the cargo metadata of a one-crate workspace.

    Returns:
        The document, as JSON: one member, no dependencies.

    """
    package: dict[str, object] = {
        "id": f"path+file:///w/{name}#0.1.0",
        "name": name,
        "version": "0.1.0",
        "rust_version": promise,
    }
    node: dict[str, object] = {"id": package["id"], "deps": []}
    doc: dict[str, object] = {
        "workspace_members": [package["id"]],
        "packages": [package],
        "resolve": {"nodes": [node]},
    }
    return json.dumps(doc)


class TwoRoots:
    """A tree of two workspaces: the root's and a fuzz crate's."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant the tree, its metadata, the stubs and the log."""
        self.make_env = env
        tmp = tmp_path
        self.tree = tmp / "tree"
        (self.tree / "fuzz").mkdir(parents=True)
        for manifest in ("Cargo.toml", FUZZ):
            (self.tree / manifest).write_text("", encoding="utf-8")
        environ = self.make_env()
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=environ, check=True)
        subprocess.run([GIT, "add", "."], cwd=self.tree, env=environ, check=True)
        self.plants = tmp / "plants"
        self.plants.mkdir()
        bins = self.bins = tmp / "bin"
        bins.mkdir()
        stubs = {
            "cargo": CARGO_STUB,
            "mise": MISE_STUB,
            "rustup": RUSTUP_STUB,
        }
        for name, text in stubs.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.log = tmp / "log"
        self.log.touch()
        self.overrides = {
            "PATH": f"{bins}:{environ['PATH']}",
            "ORG_BELT_DIR": str(BELT),
            "ORG_FINDINGS": "",
            "MISE_TASK_NAME": TASK,
            "BASH_ENV": str(BELT / "findings.sh"),
            "PLANT_LOG": str(self.log),
            "PLANT_DIR": str(self.plants),
        }

    def plant(self, root: str | None, fuzz: str | None) -> None:
        """Write each workspace's metadata, with the rust-version each declares."""
        for manifest, name, promise in (
            ("Cargo.toml", "app", root),
            (FUZZ, "app-fuzz", fuzz),
        ):
            path = self.plants / f"{manifest.replace('/', '_')}.json"
            path.write_text(document(name, promise), encoding="utf-8")

    def run_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
        **plant: str,
    ) -> tuple[int, str, str]:
        """Run the task's helper in this process, standing in the planted tree.

        Returns:
            The helper's status, its stdout and its stderr.

        """
        monkeypatch.chdir(self.tree)
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in (self.make_env() | self.overrides | plant).items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(gate_surface, "main", no_extension)
        # cargo as the helpers resolved it at import, before PATH had the stub.
        monkeypatch.setattr(rust_workspaces, "CARGO", str(self.bins / "cargo"))
        capfd.readouterr()
        status = msrv_lint.main([])
        out, err = capfd.readouterr()
        return status, out, err

    def checks(self) -> list[str]:
        """Read the compiles the stub cargo was asked for.

        Returns:
            One line per `cargo check`, in order.

        """
        logged = self.log.read_text(encoding="utf-8").splitlines()
        return [line for line in logged if line.startswith("cargo check ")]


@fixture
def two_roots(tmp_path: Path, env: support.Env) -> TwoRoots:
    """Give each row its own TwoRoots.

    Returns:
        The row's TwoRoots.

    """
    return TwoRoots(tmp_path, env=env)


def test_the_second_roots_undeclared_crate_is_red_by_name(
    two_roots: TwoRoots,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The fuzz crate declares nothing: the undeclared finding, its root."""
    two_roots.plant("1.85", None)
    status, _out, err = two_roots.run_task(monkeypatch, capfd)
    assert status != 0
    head = (
        f"{TASK}: {FUZZ}: a minimum is undeclared or differs from the one"
        " the rule derives"
    )
    said = err.splitlines()
    assert head in said
    named = said[said.index(head) + 1]
    assert "app-fuzz" in named
    assert f"{TASK}: Cargo.toml: a minimum" not in err


def test_both_roots_declaring_the_derived_number_compile_each(
    two_roots: TwoRoots,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A check per root, each naming its manifest; the count covers both."""
    two_roots.plant("1.85", "1.85")
    status, out, err = two_roots.run_task(monkeypatch, capfd)
    assert status == 0, err
    want = [
        (
            "cargo check --locked --manifest-path Cargo.toml --all-features"
            " -p app@0.1.0 {1.85.0}"
        ),
        (
            f"cargo check --locked --manifest-path {FUZZ} --all-features"
            " -p app-fuzz@0.1.0 {1.85.0}"
        ),
    ]
    assert want == two_roots.checks()
    said = f"{TASK}: 2 declared minimum(s) compiled at the toolchain they name"
    assert said == out.splitlines()[-1]


def test_a_red_second_root_leaves_the_first_roots_check_run(
    two_roots: TwoRoots,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Only the fuzz check is refused: red, and the root's check ran."""
    two_roots.plant("1.85", "1.85")
    status, _out, err = two_roots.run_task(monkeypatch, capfd, PLANT_REFUSE=FUZZ)
    assert status == support.CARGO_FAILED, err
    first = (
        "cargo check --locked --manifest-path Cargo.toml --all-features"
        " -p app@0.1.0 {1.85.0}"
    )
    assert first == two_roots.checks()[0]
    assert len(two_roots.checks()) == ROOTS
    failed = f"{TASK}: {FUZZ}: app-fuzz@0.1.0 failed to compile at 1.85.0,"
    assert failed in err.splitlines()

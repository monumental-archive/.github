#!/usr/bin/env python3
"""lint:msrv holds every tracked workspace, not only the root's (#2011).

Each row runs the task's own `run` body, read from mise/config.toml, in
a planted git tree of two workspaces: the root `Cargo.toml` (crate
`app`) and `fuzz/Cargo.toml` (crate `app-fuzz`), each its own root, as
a cargo-fuzz crate is. `cargo`, `rustup` and `mise` are stubs on PATH:
cargo answers `locate-project` with the manifest it was given, and
`metadata` with the planted document for the `--manifest-path` it was
given, and logs every call; mise lists one pinned rust, 1.85.0. The
belt is the real one, and so are rust_workspaces.py, msrv_lint.py and
msrv_plan.py; `ORG_PYTHON` is a stub that answers gate_surface.py with
no extension and runs every other helper on this interpreter. The floor
is the canon's own mise/rust-floor.toml, 1.85.

Before #2011 the body read `cargo metadata` at the root alone, so the
fuzz crate was held to nothing: each row fails there.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom; every child
environment is built from hermetic.env (#1542). stdlib `unittest`, run
through the gate as `mise run test`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
TASK = "lint:msrv"
GIT = hermetic.tool("git")
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
PYTHON_STUB = r"""#!/usr/bin/env bash
if [[ $(basename "$1") == gate_surface.py ]]; then
  cat > /dev/null
  exit 0
fi
exec "${PLANT_PYTHON}" "$@"
"""


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


def body() -> str:
    """Read lint:msrv's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    entry = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(entry["run"], f"{TASK}.run")


class TwoRoots(unittest.TestCase):
    """A tree of two workspaces: the root's and a fuzz crate's."""

    @override
    def setUp(self) -> None:
        """Plant the tree, its metadata, the stubs and the log."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = tmp / "tree"
        (self.tree / "fuzz").mkdir(parents=True)
        for manifest in ("Cargo.toml", FUZZ):
            (self.tree / manifest).write_text("", encoding="utf-8")
        env = hermetic.env()
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=env, check=True)
        subprocess.run([GIT, "add", "."], cwd=self.tree, env=env, check=True)
        self.plants = tmp / "plants"
        self.plants.mkdir()
        bins = tmp / "bin"
        bins.mkdir()
        stubs = {
            "cargo": CARGO_STUB,
            "mise": MISE_STUB,
            "rustup": RUSTUP_STUB,
            "python": PYTHON_STUB,
        }
        for name, text in stubs.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.log = tmp / "log"
        self.log.touch()
        self.overrides = {
            "PATH": f"{bins}:{env['PATH']}",
            "ORG_BELT_DIR": str(BELT),
            "ORG_PYTHON": str(bins / "python"),
            "ORG_FINDINGS": "",
            "PLANT_PYTHON": sys.executable,
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

    def run_task(self, **plant: str) -> subprocess.CompletedProcess[str]:
        """Run the task's body in the planted tree, as mise runs it.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=hermetic.env() | self.overrides | plant | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def checks(self) -> list[str]:
        """Read the compiles the stub cargo was asked for.

        Returns:
            One line per `cargo check`, in order.

        """
        logged = self.log.read_text(encoding="utf-8").splitlines()
        return [line for line in logged if line.startswith("cargo check ")]

    def test_the_second_roots_undeclared_crate_is_red_by_name(self) -> None:
        """The fuzz crate declares nothing: the undeclared finding, its root."""
        self.plant("1.85", None)
        done = self.run_task()
        self.assertNotEqual(0, done.returncode)
        head = (
            f"{TASK}: {FUZZ}: a minimum is undeclared or differs from the one"
            " the rule derives"
        )
        err = done.stderr.splitlines()
        self.assertIn(head, err)
        named = err[err.index(head) + 1]
        self.assertIn("app-fuzz", named)
        self.assertNotIn(f"{TASK}: Cargo.toml: a minimum", done.stderr)

    def test_both_roots_declaring_the_derived_number_compile_each(self) -> None:
        """A check per root, each naming its manifest; the count covers both."""
        self.plant("1.85", "1.85")
        done = self.run_task()
        self.assertEqual(0, done.returncode, done.stderr)
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
        self.assertEqual(want, self.checks())
        said = f"{TASK}: 2 declared minimum(s) compiled at the toolchain they name"
        self.assertEqual(said, done.stdout.splitlines()[-1])

    def test_a_red_second_root_leaves_the_first_roots_check_run(self) -> None:
        """Only the fuzz check is refused: red, and the root's check ran."""
        self.plant("1.85", "1.85")
        done = self.run_task(PLANT_REFUSE=FUZZ)
        self.assertEqual(101, done.returncode, done.stderr)
        first = (
            "cargo check --locked --manifest-path Cargo.toml --all-features"
            " -p app@0.1.0 {1.85.0}"
        )
        self.assertEqual(first, self.checks()[0])
        self.assertEqual(2, len(self.checks()))
        failed = f"{TASK}: {FUZZ}: app-fuzz@0.1.0 failed to compile at 1.85.0,"
        self.assertIn(failed, done.stderr.splitlines())


if __name__ == "__main__":
    unittest.main()

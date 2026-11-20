#!/usr/bin/env python3
"""Table tests for lint:fuzz-build's probe and manifest guard (#1290).

The body under test is the task's own `run` string from
mise/config.toml, run by bash in a planted `fuzz/` crate. `cargo` and
`cargo-fuzz` are stubs (the canon pins no Rust): the stub answers
`metadata` with what the row plants and logs every call, which each
row asserts, so a row cannot pass by stopping somewhere else.

The cargo texts are measured, never written by hand. The stale-lock
refusal was produced in a scratch crate whose parent version moved
under its fuzz lock, under cargo 1.82.0, 1.90.0 and 1.97.1: the first
two print one dialect, 1.97.1 the other, and both rows run. The
resolution failure is the one #1290 reported, reproduced by nesting a
crate with no `[workspace]` table under another workspace (cargo
1.97.1). cargo-fuzz 0.13.2's `init` was measured too: it writes no
`[workspace]` table, so the remedy printed is the empty table cargo's
own error recommends, which edtf and release-lab carry.

Mutation-checked, one mutant at a time against the task body: every
failure called stale reds 2 rows, none called stale reds 2, matching
only 1.97's dialect reds 1, cargo's text dropped from the stale branch
reds 2 and from the other branch 2, the table guard removed reds 1, the
guard as a grep for a bare `[workspace]` line in place of the TOML read
reds 2, a parse error read as a missing table reds 1, and the probe's
stderr discarded again (the defect) reds 4.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")

# Stands in for cargo. `metadata` succeeds when no refusal is planted and
# otherwise prints the planted text on stderr and exits 101, as cargo
# does; `fuzz build` and `fuzz list` answer as a two-target crate would.
# Anything else is red, so a row that reaches for an unexpected command
# fails loudly.
CARGO_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${PLANT_LOG}"
case "$1 ${2:-}" in
  "metadata --locked")
    if [[ -f ${PLANT_REFUSAL} ]]; then
      cat "${PLANT_REFUSAL}" >&2
      exit 101
    fi
    echo '{}'
    ;;
  "fuzz build") ;;
  "fuzz list") printf 'parse\\nround_trip\\n' ;;
  *) exit 99 ;;
esac
"""
CARGO_FUZZ_STUB = "#!/usr/bin/env bash\nexit 99\n"

PROBE = "metadata --locked --manifest-path fuzz/Cargo.toml --format-version 1"
BUILT = [PROBE, "fuzz build -s none", "fuzz list"]

MANIFEST = """\
[package]
name = "probe-fuzz"
version = "0.0.0"
publish = false
edition = "2024"

[package.metadata]
cargo-fuzz = true

[dependencies]
libfuzzer-sys = "0.4"
"""
WORKSPACE = "\n[workspace]\n"

# cargo 1.82.0 and 1.90.0, byte for byte but the path.
STALE_OLD = (
    "error: the lock file {lock} needs to be updated but --locked was "
    "passed to prevent this\n"
    "If you want to try to generate the lock file without accessing the "
    "network, remove the --locked flag and use --offline instead.\n"
)
# cargo 1.97.1, byte for byte but the path.
STALE_NEW = (
    "error: cannot update the lock file {lock} because --locked was passed "
    "to prevent this\n"
    "help: to generate the lock file without accessing the network, remove "
    "the --locked flag and use --offline instead.\n"
)
# cargo 1.97.1 in a nested worktree, byte for byte but the paths.
NESTED = (
    "error: current package believes it's in a workspace when it's not:\n"
    "current:   {root}/fuzz/Cargo.toml\n"
    "workspace: /outer/Cargo.toml\n"
    "\n"
    "this may be fixable by adding `inner/fuzz` to the `workspace.members` "
    "array of the manifest located at: /outer/Cargo.toml\n"
    "Alternatively, to keep it out of the workspace, add the package to the "
    "`workspace.exclude` array, or add an empty `[workspace]` table to the "
    "package's manifest.\n"
)

STALE_HEAD = (
    "lint:fuzz-build: fuzz/Cargo.lock is stale — it would be rewritten by the build\n"
)
STALE_REMEDY = (
    "  fuzz/ is its own workspace: its lock names the parent crate's "
    "version and\n"
    "  the release script refreshes it (release/prepare-release.sh)\n"
    "  fix locally with: cargo update --workspace --manifest-path "
    "fuzz/Cargo.toml\n"
)
NO_WORKSPACE = (
    "lint:fuzz-build: fuzz/Cargo.toml declares no [workspace] table — its "
    "resolution depends on where the checkout sits\n"
    "  a workspace above the checkout (a nested worktree) claims the crate; "
    "make it its own root\n"
    "  with the empty table cargo itself recommends, in fuzz/Cargo.toml:\n"
    "    [workspace]\n"
)
UNRESOLVED_HEAD = "lint:fuzz-build: the fuzz manifest does not resolve — cargo says:\n"


def task_body(name: str) -> str:
    """Read a task's run string out of the belt as mise would render it.

    Returns:
        The body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    run = decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


# What a row expects of the cargo log, named with its element type.
NO_CALLS: list[str] = []
ONE_PROBE: list[str] = [PROBE]


class FuzzBuild(unittest.TestCase):
    """lint:fuzz-build says "stale" only when cargo says the lock is."""

    @override
    def setUp(self) -> None:
        """Plant a fuzz crate with a committed lock, and the stubs."""
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "fuzz").mkdir()
        (self.root / "fuzz" / "Cargo.lock").write_text("", encoding="utf-8")
        self.stubs = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.stubs, ignore_errors=True)
        for name, text in (("cargo", CARGO_STUB), ("cargo-fuzz", CARGO_FUZZ_STUB)):
            stub = self.stubs / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.log = self.stubs / "calls.log"
        self.refusal = self.stubs / "refusal.txt"

    def manifest(self, text: str) -> None:
        """Write fuzz/Cargo.toml."""
        (self.root / "fuzz" / "Cargo.toml").write_text(text, encoding="utf-8")

    def refuse(self, text: str) -> None:
        """Make the probe fail with cargo's `text` on stderr."""
        self.refusal.write_text(text, encoding="utf-8")

    def run_lint(self) -> tuple[int, str, str, list[str]]:
        """Run the task body in the planted crate.

        Returns:
            Exit status, stdout, stderr, and the cargo calls made.

        """
        if self.log.exists():
            self.log.unlink()
        env = hermetic.env() | {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ['PATH']}",
            "PLANT_LOG": str(self.log),
            "PLANT_REFUSAL": str(self.refusal),
        }
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", task_body("lint:fuzz-build")],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = (
            self.log.read_text(encoding="utf-8").splitlines()
            if self.log.exists()
            else []
        )
        return done.returncode, done.stdout, done.stderr, calls

    def test_a_current_lock_builds(self) -> None:
        """The probe passes, the build runs, and the count is reported."""
        for table in (
            "[workspace]\n",
            '[workspace]\nmembers = ["."]\n',
            "[workspace.metadata]\nx = 1\n",
        ):
            with self.subTest(table=table):
                self.manifest(MANIFEST + "\n" + table)
                status, out, err, calls = self.run_lint()
                self.assertEqual((status, err), (0, ""))
                self.assertEqual(
                    out,
                    "lint:fuzz-build: 2 target(s) build, lockfile current\n",
                )
                self.assertEqual(calls, BUILT)

    def test_a_stale_lock_is_called_stale(self) -> None:
        """Only cargo's lockfile refusal earns the stale message."""
        lock = f"{self.root}/fuzz/Cargo.lock"
        for dialect in (STALE_OLD, STALE_NEW):
            with self.subTest(dialect=dialect[:30]):
                self.manifest(MANIFEST + WORKSPACE)
                text = dialect.format(lock=lock)
                self.refuse(text)
                status, out, err, calls = self.run_lint()
                self.assertEqual((status, out), (1, ""))
                self.assertEqual(err, STALE_HEAD + text + STALE_REMEDY)
                self.assertEqual(calls, ONE_PROBE)

    def test_no_workspace_table_reds_before_the_probe(self) -> None:
        """Resolution must not depend on where the checkout sits."""
        self.manifest(MANIFEST)
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out, err), (1, "", NO_WORKSPACE))
        self.assertEqual(calls, NO_CALLS)

    def test_another_probe_failure_passes_cargo_through(self) -> None:
        """Cargo's own error, with no lockfile remedy that cannot help."""
        self.manifest(MANIFEST + WORKSPACE)
        text = NESTED.format(root=self.root)
        self.refuse(text)
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, UNRESOLVED_HEAD + text)
        self.assertNotIn("stale", err)
        self.assertNotIn("cargo update", err)
        self.assertEqual(calls, ONE_PROBE)

    def test_an_unparsable_manifest_is_left_to_cargo(self) -> None:
        """The table check never claims a parse error as a missing table."""
        self.manifest(MANIFEST + "[workspace\n")
        text = "error: failed to parse manifest at `fuzz/Cargo.toml`\n"
        self.refuse(text)
        status, _, err, calls = self.run_lint()
        self.assertEqual(status, 1)
        self.assertEqual(err, UNRESOLVED_HEAD + text)
        self.assertEqual(calls, ONE_PROBE)


if __name__ == "__main__":
    unittest.main()

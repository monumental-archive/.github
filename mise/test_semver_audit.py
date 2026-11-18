#!/usr/bin/env python3
"""Table tests for audit:semver's helper, mise/semver-audit.sh (#1341).

The helper runs for real in a planted git tree whose `Cargo.toml` is
tracked, with `cargo`, `cargo-semver-checks` and `curl` as stubs on
PATH. `cargo` answers `metadata` with the workspace the row plants;
`curl` answers the sparse index with the status the row plants per
crate; `cargo-semver-checks` exits 1 for a crate the row names red.
Every stub logs its arguments, and each row asserts the exact calls and
the exact verdict lines, so a row cannot pass by stopping somewhere
else.

gate-surface.py is the real one: the helper's population is its
derivation and nothing here re-states it. The Postgres provisioning is
the one thing replaced — the belt directory the helper reads holds the
real gate-surface.py beside a `pgrx-env.sh` whose `org_pgrx_provision`
only logs — because provisioning is pgrx-env.sh's to prove and needs a
Postgres; what is asserted here is that it runs once, before the first
pgrx judgment, and that a failed one reds the majors without stopping
the crates after it.

`publish = false` and `publish = []` are one row's planting twice:
cargo reports both as `"publish": []`, which is what the helper reads.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import json
import os
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


BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "semver-audit.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:semver"
INDEX = "https://index.crates.io"

CARGO_STUB = """#!/usr/bin/env bash
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$*" in
  "metadata --no-deps --format-version 1 --locked") cat "${PLANT_METADATA}" ;;
  *) exit 99 ;;
esac
"""
CURL_STUB = """#!/usr/bin/env bash
url="${*: -1}"
printf 'curl %s\\n' "${url}" >> "${PLANT_LOG}"
code=200
name="${url##*/}"
[[ ! -f ${PLANT_INDEX}/${name} ]] || code=$(cat "${PLANT_INDEX}/${name}")
printf '%s' "${code}"
[[ ${code} != 000 ]] || exit 7
"""
SEMVER_STUB = """#!/usr/bin/env bash
printf 'cargo-semver-checks %s\\n' "$*" >> "${PLANT_LOG}"
[[ " ${PLANT_RED:-} " != *" $3 "* ]] || exit 1
"""
PGRX_ENV_STUB = """
org_pgrx_provision() {
  printf 'provision %s|%s\\n' "$1" "$2" >> "${PLANT_LOG}"
  return "${PLANT_PROVISION:-0}"
}
"""

MAJORS = [14, 15, 16, 17, 18]
# The one major the red-at-one-major row plants red.
RED_MAJOR = 16


def clean_env() -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return hermetic.env()


def package(
    name: str,
    root: str,
    *,
    publish: list[str] | None = None,
    pgrx: bool = False,
    kinds: tuple[list[str], ...] = (["lib"],),
) -> dict:
    """Plant one workspace member as `cargo metadata` reports it.

    `kinds` is one `kind` list per target, as cargo writes them: edtf's
    measured shapes are `["lib"]`, `["cdylib", "rlib"]` (edtf-wasm),
    `["cdylib", "lib"]` (edtf-postgres) and `["bin"]` (edtf-cli).

    Returns:
        The package record.

    """
    features = {"default": []}
    deps = []
    if pgrx:
        features = {
            "default": ["pg18"],
            "pg_test": [],
            **{f"pg{m}": [f"pgrx/pg{m}"] for m in MAJORS},
        }
        deps = [{"name": "pgrx"}]
    return {
        "id": f"path+file://{root}/{name}#{name}@1.0.0",
        "name": name,
        "version": "1.0.0",
        "publish": publish,
        "dependencies": deps,
        "features": features,
        "targets": [{"kind": kind, "crate_types": kind} for kind in kinds],
        "manifest_path": f"{root}/{name}/Cargo.toml",
    }


def judged(crate: str, *extra: str) -> str:
    """Spell one cargo-semver-checks call the way the stub logs it.

    Returns:
        The log line.

    """
    return " ".join(["cargo-semver-checks check-release -p", crate, *extra])


def at(crate: str, major: int) -> str:
    """Spell a pgrx crate's call at one major.

    Returns:
        The log line.

    """
    return judged(crate, "--only-explicit-features", "--features", f"pg{major}")


class Planted(unittest.TestCase):
    """A planted tree, stub PATH and belt; the rows are in the classes below."""

    def setUp(self) -> None:
        """Plant a tracked tree, a stub PATH and a belt with stub provisioning."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, body in {
            "cargo": CARGO_STUB,
            "curl": CURL_STUB,
            "cargo-semver-checks": SEMVER_STUB,
        }.items():
            stub = bins / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        belt = self.tmp / "belt"
        belt.mkdir()
        (belt / "gate-surface.py").symlink_to(BELT / "gate-surface.py")
        # The one index rule, shared with the crates publisher (#1383).
        (belt / "crates-index.py").symlink_to(BELT / "crates-index.py")
        (belt / "pgrx-env.sh").write_text(PGRX_ENV_STUB, encoding="utf-8")
        self.index = self.tmp / "index"
        self.index.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.metadata = self.tmp / "metadata.json"
        self.env = clean_env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(belt),
            "PLANT_LOG": str(self.log),
            "PLANT_METADATA": str(self.metadata),
            "PLANT_INDEX": str(self.index),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=clean_env(), check=True
        )

    def plant(self, *packages: dict, index: dict | None = None) -> None:
        """Track a Cargo.toml, plant the metadata and the index answers."""
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        self.git("add", "Cargo.toml")
        root = str(self.tree)
        self.metadata.write_text(
            json.dumps({
                "packages": list(packages),
                "workspace_members": [p["id"] for p in packages],
                "workspace_root": root,
            }),
            encoding="utf-8",
        )
        for name, code in (index or {}).items():
            (self.index / name).write_text(code, encoding="utf-8")

    def run_helper(self, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        """Run the helper in the planted tree.

        Returns:
            The finished process and the stubs' call log.

        """
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def semver_calls(log: list[str]) -> list[str]:
        """Keep only the judgments from a call log.

        Returns:
            The cargo-semver-checks lines, in order.

        """
        return [line for line in log if line.startswith("cargo-semver-checks ")]

    @staticmethod
    def verdicts(done: subprocess.CompletedProcess) -> list[str]:
        """Keep only the closing verdict lines from stdout.

        Returns:
            Every stdout line but the per-judgment progress lines.

        """
        return [
            line
            for line in done.stdout.splitlines()
            if not line.startswith(f"{LABEL}: judging ")
        ]


class Judgment(Planted):
    """Lookup, judgment and verdict: what happens to a crate that is asked."""

    def test_ordinary_published_crate(self) -> None:
        """One call, no feature flags, judged clean."""
        self.plant(package("alpha", str(self.tree)))
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            log,
            [
                "cargo metadata --no-deps --format-version 1 --locked",
                f"curl {INDEX}/al/ph/alpha",
                judged("alpha"),
            ],
        )
        self.assertEqual(self.verdicts(done), [f"{LABEL}: alpha: judged clean"])

    def test_no_baseline_is_named_not_judged(self) -> None:
        """A definite 404 names the crate, judges nothing and exits 0."""
        self.plant(package("lab-core", str(self.tree)), index={"lab-core": "404"})
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.semver_calls(log), [])
        self.assertIn(f"curl {INDEX}/la/b-/lab-core", log)
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: lab-core: no published baseline on crates.io, not judged"],
        )

    def test_index_error_is_red_by_name(self) -> None:
        """A 500 is red and names the crate; it is never read as no baseline."""
        self.plant(package("alpha", str(self.tree)), index={"alpha": "500"})
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.semver_calls(log), [])
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: alpha: could not read the crates.io index (HTTP 500)"],
        )
        self.assertIn(f"{LABEL}: red", done.stderr)

    def test_no_answer_is_red(self) -> None:
        """A lookup that got no answer at all is red, not a skip."""
        self.plant(package("alpha", str(self.tree)), index={"alpha": "000"})
        done, _ = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: alpha: could not read the crates.io index (HTTP 000)"],
        )

    def test_throttle_is_red(self) -> None:
        """A 429 is a throttle, not a fact about the crate."""
        self.plant(package("alpha", str(self.tree)), index={"alpha": "429"})
        done, _ = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: alpha: could not read the crates.io index (HTTP 429)"],
        )

    def test_pgrx_crate_judged_per_major(self) -> None:
        """Five majors, five calls, each with only its own pgNN."""
        self.plant(
            package(
                "edtf-postgres",
                str(self.tree),
                pgrx=True,
                kinds=(["cdylib", "lib"],),
            )
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            log,
            [
                "cargo metadata --no-deps --format-version 1 --locked",
                f"curl {INDEX}/ed/tf/edtf-postgres",
                f"provision {LABEL}|",
                *[at("edtf-postgres", m) for m in MAJORS],
            ],
        )
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: edtf-postgres at pg{m}: judged clean" for m in MAJORS],
        )

    def test_pgrx_major_red_is_its_own_verdict(self) -> None:
        """A red at one major does not stop the others."""
        self.plant(package("ext", str(self.tree), pgrx=True))
        bins = Path(self.env["PATH"].split(os.pathsep)[0])
        (bins / "cargo-semver-checks").write_text(
            SEMVER_STUB.replace(
                '[[ " ${PLANT_RED:-} " != *" $3 "* ]] || exit 1',
                f'[[ "$*" != *"--features pg{RED_MAJOR}" ]] || exit 1',
            ),
            encoding="utf-8",
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.semver_calls(log), [at("ext", m) for m in MAJORS])
        self.assertEqual(
            self.verdicts(done),
            [
                f"{LABEL}: ext at pg{m}: judged {'red' if m == RED_MAJOR else 'clean'}"
                for m in MAJORS
            ],
        )

    def test_failed_provisioning_reds_majors_and_goes_on(self) -> None:
        """No Postgres reds every major; the next crate is still judged."""
        self.plant(
            package("ext", str(self.tree), pgrx=True),
            package("zeta", str(self.tree)),
        )
        done, log = self.run_helper(PLANT_PROVISION="1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.semver_calls(log), [judged("zeta")])
        self.assertEqual(
            self.verdicts(done),
            [
                *[
                    f"{LABEL}: ext at pg{m}: no Postgres could be provisioned, red"
                    for m in MAJORS
                ],
                f"{LABEL}: zeta: judged clean",
            ],
        )


class Population(Planted):
    """Which crates are asked at all: publish setting, targets, no manifest."""

    def test_publish_false_and_empty_are_skipped(self) -> None:
        """Both spellings arrive as `[]`: no lookup, no judgment."""
        self.plant(
            package("closed", str(self.tree), publish=[]),
            package("shut", str(self.tree), publish=[]),
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, ["cargo metadata --no-deps --format-version 1 --locked"])
        self.assertEqual(
            self.verdicts(done),
            [
                f"{LABEL}: closed: publish = false, skipped",
                f"{LABEL}: shut: publish = false, skipped",
            ],
        )

    def test_other_registry_named_not_judged(self) -> None:
        """A crate published elsewhere only is named, with the registry."""
        self.plant(
            package("inner", str(self.tree), publish=["corp"]),
            package("both", str(self.tree), publish=["corp", "crates-io"]),
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.semver_calls(log), [judged("both")])
        self.assertEqual(
            self.verdicts(done),
            [
                f"{LABEL}: inner: published to corp, not crates.io, not judged",
                f"{LABEL}: both: judged clean",
            ],
        )

    def test_one_red_among_three(self) -> None:
        """All three are judged before the exit, which is 1."""
        root = str(self.tree)
        self.plant(package("aaa", root), package("bb", root), package("c", root))
        done, log = self.run_helper(PLANT_RED="bb")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            log,
            [
                "cargo metadata --no-deps --format-version 1 --locked",
                f"curl {INDEX}/3/a/aaa",
                judged("aaa"),
                f"curl {INDEX}/2/bb",
                judged("bb"),
                f"curl {INDEX}/1/c",
                judged("c"),
            ],
        )
        self.assertEqual(
            self.verdicts(done),
            [
                f"{LABEL}: aaa: judged clean",
                f"{LABEL}: bb: judged red",
                f"{LABEL}: c: judged clean",
            ],
        )
        self.assertIn(f"{LABEL}: red", done.stderr)

    def test_bin_only_crate_is_named_not_judged(self) -> None:
        """No library target: no lookup, no judgment, exit 0."""
        self.plant(package("cli", str(self.tree), kinds=(["bin"], ["test"])))
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, ["cargo metadata --no-deps --format-version 1 --locked"])
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: cli: no library target, no API to judge, not judged"],
        )

    def test_proc_macro_crate_is_named_not_judged(self) -> None:
        """The tool does not count proc-macro as a library target; nor does this."""
        self.plant(package("derive", str(self.tree), kinds=(["proc-macro"],)))
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, ["cargo metadata --no-deps --format-version 1 --locked"])
        self.assertEqual(
            self.verdicts(done),
            [f"{LABEL}: derive: no library target, no API to judge, not judged"],
        )

    def test_lib_and_bin_crate_is_judged(self) -> None:
        """A library beside a binary is judged, once."""
        self.plant(package("both", str(self.tree), kinds=(["lib"], ["bin"])))
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.semver_calls(log), [judged("both")])
        self.assertEqual(self.verdicts(done), [f"{LABEL}: both: judged clean"])

    def test_every_library_kind_is_judged(self) -> None:
        """rlib, dylib, cdylib and staticlib count, as the tool counts them."""
        root = str(self.tree)
        self.plant(
            package("wasm", root, kinds=(["cdylib", "rlib"],)),
            package("dyn", root, kinds=(["dylib"],)),
            package("static", root, kinds=(["staticlib"],)),
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self.semver_calls(log),
            [judged("wasm"), judged("dyn"), judged("static")],
        )

    def test_bin_only_among_judged_crates(self) -> None:
        """The crates around a bin-only one are still judged."""
        root = str(self.tree)
        self.plant(
            package("core", root),
            package("cli", root, kinds=(["bin"],)),
            package("wasm", root, kinds=(["cdylib", "rlib"],)),
        )
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.semver_calls(log), [judged("core"), judged("wasm")])
        self.assertNotIn(f"curl {INDEX}/3/c/cli", log)
        self.assertEqual(
            self.verdicts(done),
            [
                f"{LABEL}: core: judged clean",
                f"{LABEL}: cli: no library target, no API to judge, not judged",
                f"{LABEL}: wasm: judged clean",
            ],
        )

    def test_index_path_lowercases(self) -> None:
        """The sparse index is keyed on the lowercased name."""
        self.plant(package("Mixed_Case", str(self.tree)))
        _, log = self.run_helper()
        self.assertIn(f"curl {INDEX}/mi/xe/mixed_case", log)

    def test_no_cargo_toml_skips_clean(self) -> None:
        """No tracked manifest: nothing is asked of cargo, exit 0."""
        done, log = self.run_helper()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, [])
        self.assertEqual(done.stdout, f"{LABEL}: no Cargo.toml tracked, skipped\n")


if __name__ == "__main__":
    unittest.main()

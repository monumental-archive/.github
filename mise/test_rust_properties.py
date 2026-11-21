#!/usr/bin/env python3
"""Table tests for the Rust property tasks and the belt's seed (#1930).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/rust-properties.sh` and
`mise/rust_properties.py`. `mise` is a STUB on PATH: the audit runs the
repository's own `test` task through it, and the stub records each
call, the ORG_PROPERTY_SEED it was handed and whether a PROPTEST_CASES
reached it. What proptest does with the seed the belt's `[env]` hands
it was measured on real crates and is recorded in
docs/tooling-verdicts.md; the rows here hold the two `[env]` lines to
exactly what was measured.

Each red row asserts its message, not just the exit status: a planted
row scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import os
import re
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import decoded
import hermetic
import rust_properties as rp
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
PYTHON = hermetic.tool("python3")

CONFIG = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
TASKS = decoded.as_object(CONFIG["tasks"], "tasks")


def run_body(name: str) -> str:
    """Read one belt task's `run` body.

    Returns:
        The bash the task runs.

    """
    return decoded.as_str(decoded.as_object(TASKS[name], name)["run"], f"{name}.run")


LINT = run_body("lint:rust-properties")
AUDIT = run_body("audit:rust-properties")
SKIP_LINT = "lint:rust-properties: no tracked Cargo.lock locks proptest, skipped\n"
# The stub's calls where it must never have been called.
NONE: list[str] = []
SKIP_AUDIT = "audit:rust-properties: no tracked Cargo.lock locks proptest, skipped\n"


def lock(*packages: tuple[str, str]) -> str:
    """Write a Cargo.lock holding the packages.

    Returns:
        The lock file's text.

    """
    entries = "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        for name, version in packages
    )
    return f"version = 4\n{entries}"


# A tree whose one lock holds proptest at what edtf locks.
LOCKED = {"Cargo.lock": lock(("proptest", "1.12.0"))}

# The repository's mise: records each call and what `run test` was
# handed, answers `tasks info test` with STUB_HAS_TEST, and exits
# STUB_EXIT from `run test`.
STUB = """\
#!/usr/bin/env bash
out="${STUB_OUT:?}"
echo "$*" >> "${out}/calls"
if [[ $1 == tasks && $2 == info ]]; then
  [[ ${STUB_HAS_TEST:-1} == 1 && $3 == test ]]
  exit
fi
echo "${ORG_PROPERTY_SEED-unset}" > "${out}/seed"
echo "${PROPTEST_CASES-unset}" > "${out}/cases"
exit "${STUB_EXIT:-0}"
"""

# Every inherited GIT_* goes (#857), as in test_typescript_properties.py.
_GIT_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


@dataclass(frozen=True)
class Ran:
    """A finished body: exit status, output, and what the stub saw."""

    returncode: int
    stdout: str
    stderr: str
    calls: list[str]
    seed: str | None
    cases: str | None


def run(body: str, files: dict[str, str], env: dict[str, str] | None = None) -> Ran:
    """Run a task body in a planted tree, with the stub mise on PATH.

    Returns:
        The exit status, stdout and stderr, and the stub's records.

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        out = Path(scratch) / "out"
        stubs = Path(scratch) / "bin"
        out.mkdir()
        stubs.mkdir()
        stub = stubs / "mise"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
        subprocess.run([GIT, "init", "-q", str(root)], check=True, env=_GIT_ENV)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if files:
            subprocess.run(
                [GIT, "-C", str(root), "add", "-A"], check=True, env=_GIT_ENV
            )
        search = [str(stubs), str(Path(GIT).parent), "/usr/bin", "/bin"]
        environ = hermetic.env(
            PATH=os.pathsep.join(search),
            HOME=scratch,
            ORG_BELT_DIR=str(BELT),
            ORG_PYTHON=PYTHON,
            STUB_OUT=str(out),
            **env or {},
        )
        if "ORG_PROPERTY_SEED" not in (env or {}):
            environ.pop("ORG_PROPERTY_SEED", None)
        environ.pop("PROPTEST_CASES", None)
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", body],
            cwd=root,
            env=environ,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = out / "calls"
        seed = out / "seed"
        cases = out / "cases"
        return Ran(
            returncode=done.returncode,
            stdout=done.stdout,
            stderr=done.stderr,
            calls=calls.read_text("utf-8").splitlines() if calls.exists() else [],
            seed=seed.read_text("utf-8").strip() if seed.exists() else None,
            cases=cases.read_text("utf-8").strip() if cases.exists() else None,
        )


class Lint(unittest.TestCase):
    """lint:rust-properties: a proptest that ignores the seed is red."""

    def test_proptest_below_1_7_is_red_by_file_and_version(self) -> None:
        """1.6.0 predates PROPTEST_RNG_SEED."""
        got = run(
            LINT,
            {"crates/a/Cargo.lock": lock(("proptest", "1.6.0"), ("rand", "0.9.0"))},
        )
        self.assertEqual(1, got.returncode, msg=got.stderr)
        self.assertIn("crates/a/Cargo.lock: proptest 1.6.0 is below 1.7.0", got.stderr)
        self.assertIn("ignores PROPTEST_RNG_SEED", got.stderr)

    def test_a_pre_release_of_1_7_is_red(self) -> None:
        """A pre-release sorts below its release."""
        got = run(LINT, {"Cargo.lock": lock(("proptest", "1.7.0-alpha.1"))})
        self.assertEqual(1, got.returncode, msg=got.stderr)
        self.assertIn("Cargo.lock: proptest 1.7.0-alpha.1 is below 1.7.0", got.stderr)

    def test_1_7_and_1_12_are_green(self) -> None:
        """The first release that reads the seed, and what edtf locks."""
        for version in ("1.7.0", "1.12.0"):
            with self.subTest(version=version):
                got = run(LINT, {"Cargo.lock": lock(("proptest", version))})
                self.assertEqual(0, got.returncode, msg=got.stderr)
                self.assertEqual(
                    f"lint:rust-properties: Cargo.lock: proptest {version},"
                    " reads the belt's seed\n",
                    got.stdout,
                )

    def test_one_old_lock_among_good_ones_is_red(self) -> None:
        """Every lock is read, not the first."""
        got = run(
            LINT,
            {
                "Cargo.lock": lock(("proptest", "1.12.0")),
                "fuzz/Cargo.lock": lock(("proptest", "1.4.0")),
            },
        )
        self.assertEqual(1, got.returncode, msg=got.stderr)
        self.assertIn("fuzz/Cargo.lock: proptest 1.4.0 is below 1.7.0", got.stderr)
        self.assertNotIn("Cargo.lock: proptest 1.12.0", got.stderr)

    def test_a_vendored_lock_is_not_read(self) -> None:
        """vendor/ is not the repository's, at the root or nested."""
        for name in ("vendor/x/Cargo.lock", "crates/vendor/y/Cargo.lock"):
            with self.subTest(name=name):
                got = run(LINT, {name: lock(("proptest", "1.6.0"))})
                self.assertEqual(0, got.returncode, msg=got.stderr)
                self.assertEqual(SKIP_LINT, got.stdout)

    def test_no_proptest_locked_skips_clean_by_name(self) -> None:
        """A lock without proptest, and no lock at all."""
        rows: list[dict[str, str]] = [
            {"Cargo.lock": lock(("rand", "0.9.0"))},
            {"README.md": "x\n"},
            {},
        ]
        for files in rows:
            with self.subTest(files=" ".join(files)):
                got = run(LINT, files)
                self.assertEqual(0, got.returncode, msg=got.stderr)
                self.assertEqual(SKIP_LINT, got.stdout)
                self.assertEqual(NONE, got.calls)

    def test_an_unreadable_lock_is_red_by_name(self) -> None:
        """A lock that is not TOML is named, never read as empty."""
        got = run(LINT, {"Cargo.lock": "[[package\n"})
        self.assertEqual(1, got.returncode, msg=got.stderr)
        self.assertIn("Cargo.lock: ", got.stderr)
        self.assertIn(
            "lint:rust-properties: a tracked Cargo.lock could not be read", got.stderr
        )


SEED_LINE = re.compile(
    r"^audit:rust-properties: seed (\d+) \(reproduce: ORG_PROPERTY_SEED=(\d+)\)$",
    re.MULTILINE,
)


class Audit(unittest.TestCase):
    """audit:rust-properties: the repository's test task at a drawn seed."""

    def test_an_unset_seed_is_drawn_printed_and_handed_on(self) -> None:
        """The printed seed is the one `mise run test` receives."""
        got = run(AUDIT, LOCKED)
        self.assertEqual(0, got.returncode, msg=got.stderr)
        found = SEED_LINE.search(got.stdout)
        if found is None:
            self.fail(f"no seed line: {got.stdout}")
        self.assertEqual(found.group(1), found.group(2))
        self.assertTrue(got.stdout.startswith(found.group(0)), msg=got.stdout)
        self.assertEqual(found.group(1), got.seed)
        self.assertLess(int(found.group(1)), 1 << 64)
        self.assertEqual("run test", got.calls[-1])
        self.assertEqual("unset", got.cases)

    def test_two_unset_runs_draw_two_seeds(self) -> None:
        """64 random bits: two draws that agree would be a fixed seed."""
        seeds = {run(AUDIT, LOCKED).seed for _ in range(2)}
        self.assertEqual(2, len(seeds), msg=seeds)

    def test_a_given_seed_is_the_one_handed_on(self) -> None:
        """ORG_PROPERTY_SEED=7 reruns seed 7."""
        got = run(AUDIT, LOCKED, {"ORG_PROPERTY_SEED": "7"})
        self.assertEqual(0, got.returncode, msg=got.stderr)
        self.assertIn(
            "audit:rust-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n",
            got.stdout,
        )
        self.assertEqual("7", got.seed)
        self.assertEqual("unset", got.cases)

    def test_a_seed_proptest_would_not_read_is_a_usage_error(self) -> None:
        """Proptest ignores a non-number without a word (measured)."""
        for given in ("notanumber", "-1", str(1 << 64)):
            with self.subTest(given=given):
                got = run(AUDIT, LOCKED, {"ORG_PROPERTY_SEED": given})
                self.assertEqual(2, got.returncode, msg=got.stderr)
                self.assertIn(
                    f"ORG_PROPERTY_SEED must be an integer from 0 to 2^64-1,"
                    f" got '{given}'",
                    got.stderr,
                )
                self.assertNotIn("run test", got.calls)

    def test_the_test_task_status_is_the_task_status(self) -> None:
        """A failing test task fails the audit and says where it lands."""
        for status in ("0", "1", "101"):
            with self.subTest(status=status):
                got = run(AUDIT, LOCKED, {"STUB_EXIT": status})
                self.assertEqual(int(status), got.returncode, msg=got.stderr)
                if status != "0":
                    self.assertIn("*.proptest-regressions", got.stderr)
                    rerun = f"ORG_PROPERTY_SEED={got.seed} mise run test"
                    self.assertIn(rerun, got.stderr)

    def test_no_proptest_locked_skips_and_calls_no_mise(self) -> None:
        """Nothing to run: the skip line, and the stub never called."""
        rows: list[dict[str, str]] = [{"Cargo.lock": lock(("rand", "0.9.0"))}, {}]
        for files in rows:
            with self.subTest(files=" ".join(files)):
                got = run(AUDIT, files)
                self.assertEqual(0, got.returncode, msg=got.stderr)
                self.assertEqual(SKIP_AUDIT, got.stdout)
                self.assertEqual(NONE, got.calls)

    def test_no_test_task_is_red_by_lock_file(self) -> None:
        """Proptest locked and nothing to run it: red, naming the lock."""
        files = {"crates/a/Cargo.lock": lock(("proptest", "1.12.0"))}
        got = run(AUDIT, files, {"STUB_HAS_TEST": "0"})
        self.assertEqual(1, got.returncode, msg=got.stderr)
        self.assertIn(
            "audit:rust-properties: crates/a/Cargo.lock locks proptest, but the"
            " repository defines no test task",
            got.stderr,
        )
        self.assertNotIn("run test", got.calls)
        self.assertNotIn("seed", got.stdout)


class Seed(unittest.TestCase):
    """The gate's seed is the belt's, and nothing in the gate moves it."""

    def test_the_belt_env_sets_the_seed_and_no_timeout(self) -> None:
        """mise/config.toml's two `[env]` lines, exactly as #1930 writes them."""
        env = decoded.as_object(CONFIG["env"], "env")
        self.assertEqual(
            "{{ get_env(name='ORG_PROPERTY_SEED', default='1') }}",
            env.get("PROPTEST_RNG_SEED"),
        )
        self.assertEqual("0", env.get("PROPTEST_TIMEOUT"))
        self.assertNotIn("PROPTEST_CASES", env)

    def test_the_gate_workflow_sets_no_property_seed(self) -> None:
        """ci.yml never sets ORG_PROPERTY_SEED, so the gate's seed is 1."""
        path = CANON / ".github" / "workflows" / "ci.yml"
        runs = "\n".join(r.text for r in workflow_yaml.runs(path))
        self.assertNotIn("ORG_PROPERTY_SEED", runs)
        self.assertNotIn("ORG_PROPERTY_SEED", path.read_text("utf-8"))


class Helper(unittest.TestCase):
    """rust_properties.py's readings, in process."""

    def test_versions_against_the_minimum(self) -> None:
        """Below, at and above 1.7.0, and what is no version."""
        rows = [
            ("1.6.0", True),
            ("1.6.99", True),
            ("0.10.1", True),
            ("1.7.0-rc.1", True),
            ("not-a-version", True),
            ("1.7.0", False),
            ("1.7.0+build", False),
            ("1.12.0", False),
            ("2.0.0", False),
        ]
        for version, below in rows:
            with self.subTest(version=version):
                self.assertEqual(below, rp.below_minimum(version))

    def test_locks_proptest_reads_tracked_locks_only(self) -> None:
        """audit_legs.py's answer: the lock files the tasks read."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "vendor").mkdir()
            (root / "Cargo.lock").write_text(lock(("rand", "0.9.0")), "utf-8")
            (root / "vendor" / "Cargo.lock").write_text(
                lock(("proptest", "1.12.0")), "utf-8"
            )
            (root / "bad").mkdir()
            (root / "bad" / "Cargo.lock").write_text("[[package\n", "utf-8")
            self.assertFalse(rp.locks_proptest(root, ["Cargo.lock"]))
            self.assertFalse(
                rp.locks_proptest(root, ["Cargo.lock", "vendor/Cargo.lock"])
            )
            self.assertTrue(rp.locks_proptest(root, ["bad/Cargo.lock"]))


if __name__ == "__main__":
    unittest.main()

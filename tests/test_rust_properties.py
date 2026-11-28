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
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from belt import decoded, workflow_yaml
from belt import rust_properties as rp
from tests import support

# Two unset runs, each drawing its own seed.
DRAWS = 2

CANON = support.CANON
BELT = CANON / "mise"
BASH = support.tool("bash")
GIT = support.tool("git")
PYTHON = support.tool("python3")

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
# Each body's task name, for the run that hands it to support.task_run.
NAMES = {LINT: "lint:rust-properties", AUDIT: "audit:rust-properties"}
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
GIT_ISOLATED = {
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


def run(
    body: str,
    files: dict[str, str],
    env: dict[str, str] | None = None,
    *,
    make_env: support.Env,
) -> Ran:
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
        subprocess.run(
            [GIT, "init", "-q", str(root)], check=True, env=make_env(**GIT_ISOLATED)
        )
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if files:
            subprocess.run(
                [GIT, "-C", str(root), "add", "-A"],
                check=True,
                env=make_env(**GIT_ISOLATED),
            )
        search = [str(stubs), str(Path(GIT).parent), "/usr/bin", "/bin"]
        environ = make_env(
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
        as_mise = support.task_run(NAMES[body], body)
        done = subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
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


# lint:rust-properties: a proptest that ignores the seed is red.
def test_proptest_below_1_7_is_red_by_file_and_version(env: support.Env) -> None:
    """1.6.0 predates PROPTEST_RNG_SEED."""
    got = run(
        LINT,
        {"crates/a/Cargo.lock": lock(("proptest", "1.6.0"), ("rand", "0.9.0"))},
        make_env=env,
    )
    assert got.returncode == 1, got.stderr
    assert "crates/a/Cargo.lock: proptest 1.6.0 is below 1.7.0" in got.stderr
    assert "ignores PROPTEST_RNG_SEED" in got.stderr


def test_a_pre_release_of_1_7_is_red(env: support.Env) -> None:
    """A pre-release sorts below its release."""
    got = run(LINT, {"Cargo.lock": lock(("proptest", "1.7.0-alpha.1"))}, make_env=env)
    assert got.returncode == 1, got.stderr
    assert "Cargo.lock: proptest 1.7.0-alpha.1 is below 1.7.0" in got.stderr


@pytest.mark.parametrize("version", ["1.7.0", "1.12.0"])
def test_1_7_and_1_12_are_green(version: object, env: support.Env) -> None:
    """The first release that reads the seed, and what edtf locks."""
    got = run(LINT, {"Cargo.lock": lock(("proptest", version))}, make_env=env)
    assert got.returncode == 0, got.stderr
    assert (
        f"lint:rust-properties: Cargo.lock: proptest {version}, reads the belt's seed\n"
    ) == got.stdout


def test_one_old_lock_among_good_ones_is_red(env: support.Env) -> None:
    """Every lock is read, not the first."""
    got = run(
        LINT,
        {
            "Cargo.lock": lock(("proptest", "1.12.0")),
            "fuzz/Cargo.lock": lock(("proptest", "1.4.0")),
        },
        make_env=env,
    )
    assert got.returncode == 1, got.stderr
    assert "fuzz/Cargo.lock: proptest 1.4.0 is below 1.7.0" in got.stderr
    assert "Cargo.lock: proptest 1.12.0" not in got.stderr


@pytest.mark.parametrize("name", ["vendor/x/Cargo.lock", "crates/vendor/y/Cargo.lock"])
def test_a_vendored_lock_is_not_read(name: object, env: support.Env) -> None:
    """vendor/ is not the repository's, at the root or nested."""
    got = run(LINT, {name: lock(("proptest", "1.6.0"))}, make_env=env)
    assert got.returncode == 0, got.stderr
    assert got.stdout == SKIP_LINT


def test_no_proptest_locked_skips_clean_by_name(env: support.Env) -> None:
    """A lock without proptest, and no lock at all."""
    rows: list[dict[str, str]] = [
        {"Cargo.lock": lock(("rand", "0.9.0"))},
        {"README.md": "x\n"},
        {},
    ]
    for files in rows:
        got = run(LINT, files, make_env=env)
        assert got.returncode == 0, got.stderr
        assert got.stdout == SKIP_LINT
        assert got.calls == NONE


def test_an_unreadable_lock_is_red_by_name(env: support.Env) -> None:
    """A lock that is not TOML is named, never read as empty."""
    got = run(LINT, {"Cargo.lock": "[[package\n"}, make_env=env)
    assert got.returncode == 1, got.stderr
    assert "Cargo.lock: " in got.stderr
    assert "lint:rust-properties: a tracked Cargo.lock could not be read" in got.stderr


SEED_LINE = re.compile(
    r"^audit:rust-properties: seed (\d+) \(reproduce: ORG_PROPERTY_SEED=(\d+)\)$",
    re.MULTILINE,
)


# audit:rust-properties: the repository's test task at a drawn seed.
def test_an_unset_seed_is_drawn_printed_and_handed_on(env: support.Env) -> None:
    """The printed seed is the one `mise run test` receives."""
    got = run(AUDIT, LOCKED, make_env=env)
    assert got.returncode == 0, got.stderr
    found = SEED_LINE.search(got.stdout)
    if found is None:
        pytest.fail(f"no seed line: {got.stdout}")
    assert found.group(1) == found.group(2)
    assert got.stdout.startswith(found.group(0)), got.stdout
    assert found.group(1) == got.seed
    assert int(found.group(1)) < 1 << 64
    assert got.calls[-1] == "run test"
    assert got.cases == "unset"


def test_two_unset_runs_draw_two_seeds(env: support.Env) -> None:
    """64 random bits: two draws that agree would be a fixed seed."""
    seeds = {run(AUDIT, LOCKED, make_env=env).seed for _ in range(DRAWS)}
    assert len(seeds) == DRAWS, seeds


def test_a_given_seed_is_the_one_handed_on(env: support.Env) -> None:
    """ORG_PROPERTY_SEED=7 reruns seed 7."""
    got = run(AUDIT, LOCKED, {"ORG_PROPERTY_SEED": "7"}, make_env=env)
    assert got.returncode == 0, got.stderr
    assert (
        "audit:rust-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n" in got.stdout
    )
    assert got.seed == "7"
    assert got.cases == "unset"


@pytest.mark.parametrize("given", ["notanumber", "-1", str(1 << 64)])
def test_a_seed_proptest_would_not_read_is_a_usage_error(
    given: object, env: support.Env
) -> None:
    """Proptest ignores a non-number without a word (measured)."""
    got = run(AUDIT, LOCKED, {"ORG_PROPERTY_SEED": given}, make_env=env)
    assert got.returncode == rp.EXIT_USAGE, got.stderr
    assert (
        f"ORG_PROPERTY_SEED must be an integer from 0 to 2^64-1, got '{given}'"
    ) in got.stderr
    assert "run test" not in got.calls


@pytest.mark.parametrize("status", ["0", "1", "101"])
def test_the_test_task_status_is_the_task_status(
    status: object, env: support.Env
) -> None:
    """A failing test task fails the audit and says where it lands."""
    got = run(AUDIT, LOCKED, {"STUB_EXIT": status}, make_env=env)
    assert int(status) == got.returncode, got.stderr
    if status != "0":
        assert "*.proptest-regressions" in got.stderr
        rerun = f"ORG_PROPERTY_SEED={got.seed} mise run test"
        assert rerun in got.stderr


def test_no_proptest_locked_skips_and_calls_no_mise(env: support.Env) -> None:
    """Nothing to run: the skip line, and the stub never called."""
    rows: list[dict[str, str]] = [{"Cargo.lock": lock(("rand", "0.9.0"))}, {}]
    for files in rows:
        got = run(AUDIT, files, make_env=env)
        assert got.returncode == 0, got.stderr
        assert got.stdout == SKIP_AUDIT
        assert got.calls == NONE


def test_no_test_task_is_red_by_lock_file(env: support.Env) -> None:
    """Proptest locked and nothing to run it: red, naming the lock."""
    files = {"crates/a/Cargo.lock": lock(("proptest", "1.12.0"))}
    got = run(AUDIT, files, {"STUB_HAS_TEST": "0"}, make_env=env)
    assert got.returncode == 1, got.stderr
    assert (
        "audit:rust-properties: crates/a/Cargo.lock locks proptest, but the"
        " repository defines no test task"
    ) in got.stderr
    assert "run test" not in got.calls
    assert "seed" not in got.stdout


# The gate's seed is the belt's, and nothing in the gate moves it.
def test_the_belt_env_sets_the_seed_and_no_timeout() -> None:
    """mise/config.toml's two `[env]` lines, exactly as #1930 writes them."""
    env = decoded.as_object(CONFIG["env"], "env")
    assert (
        env.get("PROPTEST_RNG_SEED")
        == "{{ get_env(name='ORG_PROPERTY_SEED', default='1') }}"
    )
    assert env.get("PROPTEST_TIMEOUT") == "0"
    assert "PROPTEST_CASES" not in env


def test_the_gate_workflow_sets_no_property_seed() -> None:
    """ci.yml never sets ORG_PROPERTY_SEED, so the gate's seed is 1."""
    path = CANON / ".github" / "workflows" / "ci.yml"
    runs = "\n".join(r.text for r in workflow_yaml.runs(path))
    assert "ORG_PROPERTY_SEED" not in runs
    assert "ORG_PROPERTY_SEED" not in path.read_text("utf-8")


# rust_properties.py's readings, in process.
def test_versions_against_the_minimum() -> None:
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
        assert below == rp.below_minimum(version)


def test_locks_proptest_reads_tracked_locks_only() -> None:
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
        assert not rp.locks_proptest(root, ["Cargo.lock"])
        assert not rp.locks_proptest(root, ["Cargo.lock", "vendor/Cargo.lock"])
        assert rp.locks_proptest(root, ["bad/Cargo.lock"])

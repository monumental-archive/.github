"""Table tests for the TypeScript property legs and their lint (#1549).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/typescript-properties.sh` and
`mise/typescript_properties.py`. The runner is a STUB standing at
`node_modules/.bin/vitest`, where the leg looks for the repository's
own: the belt pins neither vitest nor fast-check (#1357's rule), so
there is no real one to run here. The stub records its arguments, the
seed and run count it was handed and the wrapper config it was given,
prints a seed line per file and appends it to the seeded list as the
belt's setup file would, and writes the JSON report the leg verifies.
What fast-check does with the seed was measured on a real repository and is recorded in
docs/tooling-verdicts.md.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, shard, typed_std
from belt import typescript_properties as tp
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The restated settings the rows plant: every kind, then one file's.
RESTATED_IN_ALL = 7
RESTATED_IN_ONE = 3

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


GATE = run_body("test:typescript-properties")
AUDIT = run_body("audit:typescript-properties")
LINT = run_body("lint:typescript-properties")
# Each body's task name, for the run that hands it to support.task_run.
NAMES = {
    GATE: "test:typescript-properties",
    AUDIT: "audit:typescript-properties",
    LINT: "lint:typescript-properties",
}

# pnpm's shape, the proving repository's lockfile format.
LOCK_BOTH = "packages:\n\n  fast-check@4.9.0:\n    x\n\n  vitest@4.1.12:\n    x\n"
LOCK_VITEST = "packages:\n\n  vitest@4.1.12:\n    x\n"
LOCK_FC_ONLY = "packages:\n\n  '@fast-check/vitest@0.5.0':\n    x\n"

PROPERTY = """\
import { fc, test } from "@fast-check/vitest";

test.prop([fc.integer()])("doubles are even", (n) => (2 * n) % 2 === 0);
"""

ORDINARY = """\
import { expect, test } from "vitest";

test("one", () => {
  expect(1).toBe(1);
});
"""

# The repository's runner: records what it was handed, then writes the
# report vitest's JSON reporter would. STUB_SKIP leaves one file out of
# the report, as vitest does a file no project includes; STUB_UNSEEDED
# runs one without the belt's setup file, so it prints no seed line and
# is not in the seeded list; STUB_EXIT is its exit status. Asked to `list`, it answers
# with STUB_COLLECTS, the repository's own collection.
STUB = """\
#!/usr/bin/env bash
out="${STUB_OUT:?}"
if [[ $1 == list ]]; then
  # The files the repository's own config collects: STUB_COLLECTS,
  # space-separated, as `vitest list --filesOnly --json=<path>` writes.
  printf '%s\\n' "$@" > "${out}/list-argv"
  for arg in "$@"; do
    [[ ${arg} == --json=* ]] && listing="${arg#--json=}"
  done
  entries=""
  for f in ${STUB_COLLECTS:-}; do
    entries+="${entries:+,}{\\"file\\": \\"${PWD}/${f}\\"}"
  done
  echo "[${entries}]" > "${listing:?}"
  echo "listed"
  exit "${STUB_LIST_EXIT:-0}"
fi
printf '%s\\n' "$@" > "${out}/argv"
echo "${ORG_PROPERTY_SEED} ${ORG_PROPERTY_RUNS}" > "${out}/env"
config=""
files=()
while (($#)); do
  case "$1" in
    -c) config="$2"; shift 2 ;;
    --root) shift 2 ;;
    run) shift ;;
    *) files+=("$1"); shift ;;
  esac
done
cp "${config}" "${out}/wrapper"
results=""
for f in "${files[@]}"; do
  [[ ${f} == "${STUB_SKIP:-}" ]] && continue
  if [[ ${f} != "${STUB_UNSEEDED:-}" ]]; then
    echo "fast-check: ${f}: ${ORG_PROPERTY_RUNS} runs, seed ${ORG_PROPERTY_SEED}"
    echo "${PWD}/${f}" >> "${ORG_PROPERTY_SEEDED:?}"
  fi
  results+="${results:+,}{\\"name\\": \\"${PWD}/${f}\\", \\"assertionResults\\": [{}]}"
done
echo "{\\"testResults\\": [${results}]}" > "${ORG_PROPERTY_REPORT:?}"
exit "${STUB_EXIT:-0}"
"""

# Every inherited GIT_* goes (#857), as in test_python_properties.py.
GIT_ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def plant(root: Path, files: dict[str, str], *, runner: bool, env: support.Env) -> None:
    """Make `root` a git tree tracking `files`, with the stub installed."""
    subprocess.run([GIT, "init", "-q", str(root)], check=True, env=env(**GIT_ISOLATED))
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        subprocess.run(
            [GIT, "-C", str(root), "add", "-A"], check=True, env=env(**GIT_ISOLATED)
        )
    if runner:
        stub = root / "node_modules" / ".bin" / "vitest"
        stub.parent.mkdir(parents=True)
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)


@dataclass(frozen=True)
class Ran:
    """A finished body: exit status, output, and what the stub saw."""

    returncode: int
    stdout: str
    stderr: str
    root: str
    argv: list[str] | None
    seed: str | None
    runs: str | None
    wrapper: str | None

    def handed(self) -> list[str]:
        """Read what the runner was handed, where a row needs it to have run.

        Returns:
            The runner's arguments.

        Raises:
            AssertionError: the runner never ran.

        """
        if self.argv is None:
            msg = "the runner never ran"
            raise AssertionError(msg)
        return self.argv

    def written(self) -> str:
        """Read the wrapper config, where a row needs the runner to have run.

        Returns:
            The wrapper's text.

        Raises:
            AssertionError: the runner never ran.

        """
        if self.wrapper is None:
            msg = "the runner never ran"
            raise AssertionError(msg)
        return self.wrapper


def run(
    body: str,
    files: dict[str, str],
    *,
    runner: bool = True,
    env: dict[str, str] | None = None,
    make_env: support.Env,
) -> Ran:
    """Run a task body in a planted tree.

    Returns:
        The exit status, stdout and stderr, and the stub's records
        (`argv`, `seed`, `runs`, `wrapper`; None where it never ran).

    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "repo"
        out = Path(scratch) / "out"
        out.mkdir()
        plant(root, files, runner=runner, env=make_env)
        path = [str(Path(PYTHON).parent), str(Path(GIT).parent), "/usr/bin", "/bin"]
        environ = make_env(
            PATH=os.pathsep.join(path),
            HOME=scratch,
            ORG_BELT_DIR=str(BELT),
            STUB_OUT=str(out),
            **env or {},
        )
        as_mise = support.task_run(NAMES[body], body)
        done = subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        argv = out / "argv"
        seed, runs = (
            (out / "env").read_text("utf-8").split() if argv.exists() else (None, None)
        )
        return Ran(
            returncode=done.returncode,
            stdout=done.stdout,
            stderr=done.stderr,
            root=str(root.resolve()),
            argv=argv.read_text("utf-8").splitlines() if argv.exists() else None,
            seed=seed,
            runs=runs,
            wrapper=(out / "wrapper").read_text("utf-8") if argv.exists() else None,
        )


def args_after_config(argv: list[str]) -> list[str]:
    """Name the arguments the stub was handed after `-c <wrapper>`.

    Returns:
        The file filters, in order.

    """
    return argv[argv.index("-c") + 2 :]


# Nothing to run: exit 0, skipped, named.
@pytest.mark.parametrize(
    ("body", "label"),
    [
        (GATE, "test:typescript-properties"),
        (AUDIT, "audit:typescript-properties"),
        (LINT, "lint:typescript-properties"),
    ],
)
def test_no_fast_check_skips_clean_by_name(
    body: object, label: object, env: support.Env
) -> None:
    """A vitest lockfile without fast-check: every leg skips."""
    files = {"pnpm-lock.yaml": LOCK_VITEST, "a.prop.test.ts": PROPERTY}
    done = run(body, files, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{label}: no fast-check in a tracked lockfile, skipped\n"
    assert done.argv is None


def test_no_lockfile_skips_clean_by_name(env: support.Env) -> None:
    """No lockfile tracked at all: the same skip."""
    done = run(GATE, {"a.prop.test.ts": PROPERTY}, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "test:typescript-properties: no fast-check in a tracked lockfile, skipped\n"
    )


def test_an_untracked_lockfile_is_not_read(env: support.Env) -> None:
    """A lockfile git does not track is not the repository's."""
    done = run(
        GATE,
        {"a.prop.test.ts": PROPERTY, ".gitignore": "pnpm-lock.yaml\n"},
        make_env=env,
    )
    assert "no fast-check in a tracked lockfile, skipped" in done.stdout


def test_no_property_file_skips_clean_by_name(env: support.Env) -> None:
    """fast-check locked, no *.prop.test.*: skipped, named."""
    done = run(GATE, {"pnpm-lock.yaml": LOCK_BOTH, "a.test.ts": ORDINARY}, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "test:typescript-properties: no *.prop.test.{ts,tsx,mts,cts} tracked, skipped\n"
    )
    assert done.argv is None


# Properties tracked, but nothing the leg may run them with.
def test_no_vitest_in_the_lockfile_is_red(env: support.Env) -> None:
    """fast-check locked, vitest not: red, named."""
    done = run(
        GATE, {"pnpm-lock.yaml": LOCK_FC_ONLY, "a.prop.test.ts": PROPERTY}, make_env=env
    )
    assert done.returncode == 1
    assert (
        "test:typescript-properties: *.prop.test.* tracked but the lockfile pins"
        " no vitest"
    ) in done.stderr


def test_vitest_not_installed_is_red(env: support.Env) -> None:
    """Locked but no node_modules/.bin/vitest: red, with the remedy."""
    done = run(
        GATE,
        {"pnpm-lock.yaml": LOCK_BOTH, "a.prop.test.ts": PROPERTY},
        runner=False,
        make_env=env,
    )
    assert done.returncode == 1
    assert (
        "test:typescript-properties: vitest is in the lockfile but not installed"
        in done.stderr
    )


def test_a_bad_audit_seed_is_a_usage_error(env: support.Env) -> None:
    """ORG_PROPERTY_SEED that is not an integer: exit 2, named."""
    done = run(
        AUDIT,
        {"pnpm-lock.yaml": LOCK_BOTH, "a.prop.test.ts": PROPERTY},
        env={"ORG_PROPERTY_SEED": "seven"},
        make_env=env,
    )
    assert done.returncode == tp.EXIT_USAGE
    assert (
        "ORG_PROPERTY_SEED must be a non-negative integer, got 'seven'" in done.stderr
    )


GATE_FILES = {
    "pnpm-lock.yaml": LOCK_BOTH,
    "vitest.config.ts": "export default {};\n",
    "src/a.prop.test.ts": PROPERTY,
    "src/b.prop.test.tsx": PROPERTY,
    "src/c.test.ts": ORDINARY,
    "vendor/v.prop.test.ts": PROPERTY,
}
AUDIT_FILES = {"pnpm-lock.yaml": LOCK_BOTH, "a.prop.test.ts": PROPERTY}


# The gate leg: the fixed seed, and only the property files.
def test_the_runner_gets_the_fixed_seed_and_only_property_files(
    env: support.Env,
) -> None:
    """Seed 1336, 200 runs, the two property files and nothing else."""
    done = run(GATE, GATE_FILES, make_env=env)
    assert done.returncode == 0, done.stderr
    assert (done.seed, done.runs) == ("1336", "200")
    argv = done.handed()
    head: list[str] = ["run", "--root", done.root]
    assert argv[:3] == head
    assert argv[3] == "-c"
    assert argv[4].endswith("/org-property.config.mts"), argv
    filters: list[str] = ["src/a.prop.test.ts", "src/b.prop.test.tsx"]
    assert args_after_config(argv) == filters
    assert done.stdout == (
        "test:typescript-properties: seed 1336, 200 runs per property,"
        " vitest.config.ts\n"
        "fast-check: src/a.prop.test.ts: 200 runs, seed 1336\n"
        "fast-check: src/b.prop.test.tsx: 200 runs, seed 1336\n"
        "test:typescript-properties: every property file held\n"
    )


def test_two_runs_hand_the_runner_the_same_seed(env: support.Env) -> None:
    """Deterministic: the seed does not move between runs."""
    first, second = (
        run(GATE, GATE_FILES, make_env=env),
        run(GATE, GATE_FILES, make_env=env),
    )
    assert (first.seed, first.handed()[5:]) == (second.seed, second.handed()[5:])


def test_the_gate_ignores_a_given_seed(env: support.Env) -> None:
    """ORG_PROPERTY_SEED is the audit's replay, never the gate's."""
    done = run(GATE, GATE_FILES, env={"ORG_PROPERTY_SEED": "7"}, make_env=env)
    assert done.seed == "1336"


def test_the_wrapper_imports_the_repository_config(env: support.Env) -> None:
    """The repo's config, the exact files and the belt's setup file."""
    done = run(GATE, GATE_FILES, make_env=env)
    assert (
        f"import * as repo from {json.dumps(done.root + '/vitest.config.ts')};"
        in done.written()
    )
    assert (
        'const files = ["src/a.prop.test.ts", "src/b.prop.test.tsx"];' in done.written()
    )
    assert 'const exclude = ["**/node_modules/**", "**/.git/**"];' in done.written()
    assert re.search(r'const setup = ".*/org-property-setup\.mts";', done.written())
    assert (
        "project.config.setupFiles = [...project.config.setupFiles, setup];"
        in done.written()
    )
    assert 'reporters: ["default", "json", seeder],' in done.written()


@pytest.mark.parametrize(("body", "files"), [(GATE, GATE_FILES), (AUDIT, AUDIT_FILES)])
def test_every_project_has_no_per_test_limit(
    body: object, files: object, env: support.Env
) -> None:
    """No per-test limit per project, in both profiles; hooks untouched (#2144)."""
    done = run(body, files, make_env=env)
    assert done.returncode == 0, done.stderr
    written = done.written()
    assert "project.config.testTimeout = 0;" in written
    assert "hookTimeout =" not in written


def test_a_config_without_projects_narrows_include(env: support.Env) -> None:
    """No test.projects: include is exactly the tracked property files."""
    done = run(GATE, GATE_FILES, make_env=env)
    assert (
        "const narrowed = test.projects === undefined"
        " ? { include: files, exclude } : {};"
    ) in done.written()
    assert "...narrowed," in done.written()


def test_no_repository_config_still_runs(env: support.Env) -> None:
    """No vitest config: the wrapper stands alone, named so."""
    files = {k: v for k, v in GATE_FILES.items() if k != "vitest.config.ts"}
    done = run(GATE, files, make_env=env)
    assert done.returncode == 0, done.stderr
    assert "200 runs per property, no vitest config\n" in done.stdout
    assert "let config: any = {};" in done.written()
    assert "import * as repo" not in done.written()


def test_vitest_config_wins_over_vite_config(env: support.Env) -> None:
    """Follow vitest's own lookup order: vitest.config.* first."""
    files = {**GATE_FILES, "vite.config.ts": "export default {};\n"}
    done = run(GATE, files, make_env=env)
    assert "200 runs per property, vitest.config.ts\n" in done.stdout


def test_a_config_excluding_the_properties_passes(env: support.Env) -> None:
    """The repo's own run collects only ordinary tests: green."""
    done = run(GATE, GATE_FILES, env={"STUB_COLLECTS": "src/c.test.ts"}, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.argv is not None


def test_a_config_collecting_a_property_is_red_by_name(env: support.Env) -> None:
    """The repo's own run would run a property unseeded: red, the fix."""
    done = run(
        GATE,
        GATE_FILES,
        env={"STUB_COLLECTS": "src/c.test.ts src/b.prop.test.tsx"},
        make_env=env,
    )
    assert done.returncode == 1
    assert done.stderr == (
        "test:typescript-properties: the repository's own vitest run collects"
        " these property files,\n"
        "  so its test task runs them a second time with no seed:\n"
        "    src/b.prop.test.tsx\n"
        "  add the property pattern to test.exclude in vitest.config.ts,"
        " beside vitest's\n"
        "  defaults (configDefaults from vitest/config):\n"
        '    exclude: [...configDefaults.exclude, "**/*.prop.test.*"],\n'
    )
    assert done.argv is None, "nothing may run before the refusal"


def test_a_listing_that_fails_is_red(env: support.Env) -> None:
    """Refuse when vitest cannot resolve the repo's config: red, its output kept."""
    done = run(GATE, GATE_FILES, env={"STUB_LIST_EXIT": "1"}, make_env=env)
    assert done.returncode == 1
    assert (
        "test:typescript-properties: vitest could not list the files the"
        " repository's own config collects:\n  listed\n"
    ) in done.stderr


def test_the_audit_does_not_ask(env: support.Env) -> None:
    """The refusal is the gate's: the audit runs regardless."""
    done = run(
        AUDIT, AUDIT_FILES, env={"STUB_COLLECTS": "a.prop.test.ts"}, make_env=env
    )
    assert done.returncode == 0, done.stderr


def test_a_failing_property_is_red(env: support.Env) -> None:
    """The runner's red is the leg's red, its status kept."""
    done = run(GATE, GATE_FILES, env={"STUB_EXIT": "1"}, make_env=env)
    assert done.returncode == 1
    assert "every property file held" not in done.stdout


def test_a_file_that_ran_unseeded_is_red_by_name(env: support.Env) -> None:
    """In the report but not in the seeded list: red (#2104)."""
    done = run(
        GATE, GATE_FILES, env={"STUB_UNSEEDED": "src/a.prop.test.ts"}, make_env=env
    )
    assert done.returncode == 1
    assert done.stderr == (
        "test:typescript-properties: not every property file ran:\n"
        "  src/a.prop.test.ts: ran without the belt's seed\n"
    )
    assert "every property file held" not in done.stdout


def test_a_file_the_runner_did_not_run_is_red(env: support.Env) -> None:
    """Green from the runner but a file absent from its report: red."""
    done = run(GATE, GATE_FILES, env={"STUB_SKIP": "src/b.prop.test.tsx"}, make_env=env)
    assert done.returncode == 1
    assert (
        "test:typescript-properties: not every property file ran:\n"
        "  src/b.prop.test.tsx: did not run, or ran no test\n"
    ) in done.stderr


# Two inline projects, one with a globalSetup, and one declared as a
# string: the config #2104 found refused.
PROJECTS_CONFIG = """\
export default {
  test: {
    projects: [
      { test: { name: "unit", include: ["tests/unit/**/*.test.ts"] } },
      {
        test: {
          name: "stack",
          include: ["tests/stack/**/*.test.ts"],
          globalSetup: ["./global-setup.ts"],
        },
      },
      "packages/*",
    ],
  },
};
"""


# A config with test.projects: every project seeded, none refused.
def test_the_staged_wrapper_seeds_projects_and_does_not_refuse() -> None:
    """No refusal for test.projects; the onInit reporter is there."""
    with tempfile.TemporaryDirectory() as scratch:
        config = Path(scratch, "vitest.config.ts")
        config.write_text(PROJECTS_CONFIG, encoding="utf-8")
        staged = Path(scratch, "staged")
        staged.mkdir()
        tp.stage(str(staged), str(config), ["tests/unit/h.prop.test.ts"])
        wrapper = (staged / "org-property.config.mts").read_text("utf-8")
    assert "throw" not in wrapper
    assert "does not inherit the root setupFiles the belt" not in wrapper
    assert "onInit(vitest: any) {" in wrapper
    assert "for (const project of vitest.projects) {" in wrapper
    assert (
        "project.config.setupFiles = [...project.config.setupFiles, setup];" in wrapper
    )
    assert "project.config.exclude = [...exclude];" in wrapper
    assert "project.config.testTimeout = 0;" in wrapper
    assert 'reporters: ["default", "json", seeder],' in wrapper


def test_each_project_s_property_runs_seeded_and_an_orphan_is_red(
    env: support.Env,
) -> None:
    """A property per project prints seed 1336; one no project has is red."""
    files = {
        "pnpm-lock.yaml": LOCK_BOTH,
        "vitest.config.ts": PROJECTS_CONFIG,
        "tests/unit/h.prop.test.ts": PROPERTY,
        "tests/stack/trig.prop.test.ts": PROPERTY,
        "orphan/o.prop.test.ts": PROPERTY,
    }
    done = run(GATE, files, env={"STUB_SKIP": "orphan/o.prop.test.ts"}, make_env=env)
    assert done.returncode == 1
    for name in ("tests/stack/trig.prop.test.ts", "tests/unit/h.prop.test.ts"):
        assert f"fast-check: {name}: 200 runs, seed 1336\n" in done.stdout
    assert done.stderr == (
        "test:typescript-properties: not every property file ran:\n"
        "  orphan/o.prop.test.ts: did not run, or ran no test\n"
    )


# The cron leg: a printed seed and a far larger run count.
def test_the_audit_prints_its_seed_first_and_honours_a_given_one(
    env: support.Env,
) -> None:
    """ORG_PROPERTY_SEED reproduces a run; the seed line comes first."""
    done = run(AUDIT, AUDIT_FILES, env={"ORG_PROPERTY_SEED": "7"}, make_env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(
        "audit:typescript-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
    ), done.stdout
    assert (done.seed, done.runs) == ("7", "10000")


def test_an_unset_seed_is_random_and_printed(env: support.Env) -> None:
    """No seed given: a fresh one, and the printed one is the one used."""
    done = run(AUDIT, AUDIT_FILES, make_env=env)
    printed = re.match(
        r"audit:typescript-properties: seed (\d+)"
        r" \(reproduce: ORG_PROPERTY_SEED=\1\)\n",
        done.stdout,
    )
    assert printed is not None, done.stdout
    assert done.seed == (typed_std.group(printed, 1) if printed else None)


def test_a_failure_says_where_the_example_lands(env: support.Env) -> None:
    """Red at a seed: the seed and the remedy, on stderr."""
    done = run(
        AUDIT,
        AUDIT_FILES,
        env={"ORG_PROPERTY_SEED": "7", "STUB_EXIT": "1"},
        make_env=env,
    )
    assert done.returncode == 1
    assert (
        "audit:typescript-properties: seed 7 — commit each counterexample above"
        in done.stderr
    )


def lint(files: dict[str, str], *, env: support.Env) -> Ran:
    """Run the lint with fast-check locked.

    Returns:
        The finished body.

    """
    return run(LINT, {"pnpm-lock.yaml": LOCK_BOTH, **files}, runner=False, make_env=env)


# Imports outside a property file, and restated seeds, by name.
def test_an_import_outside_a_property_file_is_red_by_name(env: support.Env) -> None:
    """Every import form, in an ordinary test and a .js file."""
    done = lint(
        {
            "a.prop.test.ts": PROPERTY,
            "src/plain.test.ts": PROPERTY,
            "src/more.ts": (
                'import fc from "fast-check";\n'
                'const m = await import("fast-check/lib/x");\n'
                'const r = require("@fast-check/jest");\n'
                'import "@fast-check/worker";\n'
            ),
            "src/helper.prop.test.js": 'export * from "fast-check";\n',
        },
        env=env,
    )
    assert done.returncode == 1
    suffix = " but is not a *.prop.test.{ts,tsx,mts,cts} file\n"
    for site, package in (
        ("src/helper.prop.test.js:1", "fast-check"),
        ("src/more.ts:1", "fast-check"),
        ("src/more.ts:2", "fast-check"),
        ("src/more.ts:3", "@fast-check/jest"),
        ("src/more.ts:4", "@fast-check/worker"),
        ("src/plain.test.ts:1", "@fast-check/vitest"),
    ):
        assert f"  {site}: imports {package}{suffix}" in done.stderr
    assert "a.prop.test.ts" not in done.stderr


@pytest.mark.parametrize(
    ("site", "what"),
    [
        ("g.prop.test.cts:4", "configureGlobal()"),
        ("h.prop.test.mts:4", "seed"),
        ("n.prop.test.tsx:4", "numRuns"),
        ("s.prop.test.ts:4", "seed"),
    ],
)
def test_a_property_restating_the_seed_is_red_by_name(
    site: object, what: object, env: support.Env
) -> None:
    """seed, shorthand seed, numRuns, configureGlobal: each named."""
    done = lint(
        {
            "s.prop.test.ts": PROPERTY + "fc.assert(p, { seed: 4 });\n",
            "h.prop.test.mts": PROPERTY + "fc.assert(p, { seed });\n",
            "n.prop.test.tsx": PROPERTY
            + 'test.prop([fc.nat()], { numRuns: 5 })("x", () => true);\n',
            "g.prop.test.cts": PROPERTY + "fc.configureGlobal({});\n",
        },
        env=env,
    )
    assert done.returncode == 1
    tail = "; the belt's seed, run count and time bound own every property\n"
    assert f"  {site}: restates {what}{tail}" in done.stderr


def test_a_property_setting_its_own_time_is_red_by_name(env: support.Env) -> None:
    """Each of the six forms #2144 names, by name, file and line."""
    done = lint(
        {
            "a.prop.test.ts": PROPERTY
            + 'test.prop([fc.nat()])("x", (n) => n >= 0, 20_000);\n',
            "b.prop.test.ts": PROPERTY
            + 'it("x", { timeout: 9000 }, () => {});\n'
            + 'test("y", () => {\n  return;\n}, { timeout });\n',
            "c.prop.test.ts": PROPERTY + "vi.setConfig({ testTimeout: 1 });\n",
            "d.prop.test.ts": PROPERTY + "fc.assert(p, { timeout: 100 });\n",
            "e.prop.test.ts": PROPERTY
            + 'test.prop([fc.nat()], { interruptAfterTimeLimit: 5 })("x", f);\n',
            "f.prop.test.ts": PROPERTY
            + "fc.assert(p, { skipAllAfterTimeLimit: 5 });\n",
        },
        env=env,
    )
    assert done.returncode == 1
    tail = "; the belt's seed, run count and time bound own every property\n"
    for site, what in (
        ("a.prop.test.ts:4", "a per-test timeout (test()'s third argument)"),
        ("b.prop.test.ts:4", "vitest's timeout option"),
        ("b.prop.test.ts:7", "vitest's timeout option"),
        ("c.prop.test.ts:4", "vi.setConfig()"),
        ("d.prop.test.ts:4", "fast-check's timeout"),
        ("e.prop.test.ts:4", "interruptAfterTimeLimit"),
        ("f.prop.test.ts:4", "skipAllAfterTimeLimit"),
    ):
        assert f"  {site}: restates {what}{tail}" in done.stderr
    assert done.stderr.count(": restates ") == RESTATED_IN_ALL, done.stderr


def test_a_quote_in_a_regex_hides_no_bound(env: support.Env) -> None:
    """A regex literal's quote masks nothing: each bound still red."""
    done = lint(
        {
            "a.prop.test.ts": 'import fc from "fast-check";\n'
            "const q = /'/; fc.assert(p, { seed: 1, timeout: 9 });\n"
            'const r = /"/; fc.assert(p, { numRuns: 5 });\n',
        },
        env=env,
    )
    assert done.returncode == 1
    tail = "; the belt's seed, run count and time bound own every property\n"
    for site, what in (
        ("a.prop.test.ts:2", "seed"),
        ("a.prop.test.ts:2", "fast-check's timeout"),
        ("a.prop.test.ts:3", "numRuns"),
    ):
        assert f"  {site}: restates {what}{tail}" in done.stderr
    assert done.stderr.count(": restates ") == RESTATED_IN_ONE, done.stderr


@pytest.mark.parametrize(
    ("site", "what"),
    [
        ("a.prop.test.ts:4", "fast-check's timeout"),
        ("a.prop.test.ts:5", "vi.setConfig()"),
    ],
)
def test_a_bound_named_in_text_is_red(
    site: object, what: object, env: support.Env
) -> None:
    """A comment or string naming a bound reds: loud, never silent."""
    done = lint(
        {
            "a.prop.test.ts": PROPERTY
            + "// fc.assert(p, { timeout: 1 })\n"
            + 'const note = "vi.setConfig({})";\n',
        },
        env=env,
    )
    assert done.returncode == 1
    tail = "; the belt's seed, run count and time bound own every property\n"
    assert f"  {site}: restates {what}{tail}" in done.stderr


def test_a_two_argument_call_and_other_options_are_clean(env: support.Env) -> None:
    """A number inside a body, and options without timeout: clean."""
    done = lint(
        {
            "a.prop.test.ts": PROPERTY
            + 'it("waits, 5000", () => {\n  expect(Math.max(f(1), 5)).toBe(5);\n});\n'
            + 'it("named", { retry: 2 }, () => {});\n',
        },
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "lint:typescript-properties: 1 file(s) clean\n"


def test_clean_files_pass_and_text_is_not_an_import(env: support.Env) -> None:
    """A property file importing it, and the name in a string: clean."""
    done = lint(
        {
            "a.prop.test.ts": PROPERTY,
            "b.ts": 'export const name = "fast-check";\n// from fast-check\n',
        },
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "lint:typescript-properties: 2 file(s) clean\n"


def test_vendored_code_is_not_the_repository_s(env: support.Env) -> None:
    """vendor/ is upstream's code (the belt's walk rule)."""
    done = lint({"vendor/x.ts": 'import fc from "fast-check";\n'}, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "lint:typescript-properties: no TypeScript or JavaScript tracked, skipped\n"
    )


# The lint reads a property file's code: strings and comments blank.
def test_strings_and_comments_blank_with_offsets_kept() -> None:
    """Each literal form, escapes and a template's code included."""
    rows = (
        ('a("x\\"y", 1)', 'a("    ", 1)'),
        ("a('it''s')", "a('  '' ')"),
        ("a(`t\\`${b({ c: 1 })}u`)", "a(`   ${b({ c: 1 })} `)"),
        ("a(/* x */ 1) // y\nb()", "a(        1)     \nb()"),
        ("a(1) /* open", "a(1)        "),
        ('a("open\nb()', 'a("    \nb()'),
        ("{ x } }", "{ x } }"),
    )
    for text, want in rows:
        assert tp.masked(text) == want


def test_an_unclosed_call_and_a_bare_chain_still_read() -> None:
    """An unclosed group runs to the end; `test.each` uncalled stops."""
    assert tp.arguments_of("(a, b", 0) == ([(1, 2), (3, 5)], 5)
    assert len(tp.test_calls("test.each(rows);")) == 1


# vitest's report and file list, each refused by name when misshapen.
def document(tmp_path: Path, command: str, text: str) -> list[str]:
    """Write one document for verify or collected; spell the call's argv.

    Returns:
        The argv, the document's path second.

    """
    path = tmp_path / "doc.json"
    path.write_text(text, encoding="utf-8")
    seeded = tmp_path / "seeded"
    seeded.write_text("", encoding="utf-8")
    # verify also takes the seeded list; collected does not.
    extra = [str(seeded)] if command == "verify" else []
    return [command, str(path), *extra, "a.prop.test.ts"]


def test_a_missing_seeded_list_is_red(capfd: pytest.CaptureFixture[str]) -> None:
    """Refuse verify with no seeded list to read: red, named."""
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch, "report.json")
        report.write_text('{"testResults": []}', encoding="utf-8")
        gone = Path(scratch, "seeded")
        status = tp.main(["verify", str(report), str(gone), "a.prop.test.ts"])
        out = capfd.readouterr().out
    assert status == 1
    assert (
        out.replace(str(gone), "SEEDED")
        == "no seeded list at SEEDED (FileNotFoundError)\n"
    )


def test_a_report_of_the_wrong_shape(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Each field the leg reads, by name."""
    rows = (
        ("[]", "the report is not an object"),
        ('{"testResults": "x"}', "testResults is not a list"),
        ('{"testResults": [5]}', "testResults[0] is not an object"),
        ('{"testResults": [{"name": 3}]}', "testResults[0].name is not a string"),
        (
            '{"testResults": [{"name": "a", "assertionResults": "x"}]}',
            "testResults[0].assertionResults is not a list",
        ),
        ('{"a": 1, "a": 2}', 'duplicate key "a"'),
    )
    for text, why in rows:
        argv = document(tmp_path, "verify", text)
        assert tp.main(argv) == 1
        said = capfd.readouterr().out.replace(argv[1], "DOC")
        assert said == f"no vitest JSON report at DOC ({why})\n"


def test_a_listing_of_the_wrong_shape(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The list itself, an entry, and its file."""
    rows = (
        ("{}", "the listing is not a list"),
        ("[3]", "listing[0] is not an object"),
        ("[{}]", "listing[0].file is not a string"),
    )
    for text, why in rows:
        argv = document(tmp_path, "collected", text)
        assert tp.main(argv) == 1
        said = capfd.readouterr().out.replace(argv[1], "DOC")
        assert said == f"no readable vitest file list at DOC ({why})\n"


FILES = ("c.prop.test.ts", "a.prop.test.ts", "b.prop.test.mts")


@fixture
def sharded(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str], None]:
    """Hand the runner one shard of the property files (#2160).

    Returns:
        A function that sets ORG_SHARD to `spec`, reporting to `reports`.

    """

    def hand(spec: str, reports: str) -> None:
        for name, value in {
            "ORG_SHARD": spec,
            "ORG_SHARD_TASK": "test:typescript-properties",
            "ORG_SHARD_REPORTS": reports,
        }.items():
            monkeypatch.setenv(name, value)

    return hand


def kept(out: str) -> list[str]:
    """Read the files `share` printed, NUL-separated.

    Returns:
        The files it keeps.

    """
    return [f for f in out.split("\0") if f]


def test_two_shards_hold_every_file_once(
    sharded: Callable[[str, str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """1/2 and 2/2: sorted, every second file, the union whole."""
    with tempfile.TemporaryDirectory() as reports:
        got_shares: list[list[str]] = []
        for spec in ("1/2", "2/2"):
            sharded(spec, reports)
            status = tp.main(["share", *FILES])
            out, err = capfd.readouterr()
            assert status == 0, err
            got_shares.append(kept(out))
        want: list[list[str]] = [
            ["a.prop.test.ts", "c.prop.test.ts"],
            ["b.prop.test.mts"],
        ]
        assert want == got_shares
        none: list[str] = []
        got = shard.union(shard.load(Path(reports)), {"test:typescript-properties": 2})
        assert none == got


def test_no_shard_is_every_file(
    sharded: Callable[[str, str], None], capfd: pytest.CaptureFixture[str]
) -> None:
    """Unset or 1/1: every file, as given, and no report."""
    with tempfile.TemporaryDirectory() as reports:
        for spec in ("", "1/1"):
            sharded(spec, reports)
            status = tp.main(["share", *FILES])
            out, err = capfd.readouterr()
            assert status == 0, err
            every: list[str] = list(FILES)
            assert every == kept(out)
        written: list[Path] = list(Path(reports).iterdir())
        nothing: list[Path] = []
        assert nothing == written

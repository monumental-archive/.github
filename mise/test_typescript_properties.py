#!/usr/bin/env python3
"""Table tests for the TypeScript property legs and their lint (#1549).

The bodies under test are the tasks' own `run` strings, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
and they call the real `mise/typescript-properties.sh` and
`mise/typescript_properties.py`. The runner is a STUB standing at
`node_modules/.bin/vitest`, where the leg looks for the repository's
own: the belt pins neither vitest nor fast-check (#1357's rule), so
there is no real one to run here. The stub records its arguments, the
seed and run count it was handed and the wrapper config it was given,
and writes the JSON report the leg verifies. What fast-check does with
the seed was measured on a real repository and is recorded in
docs/tooling-verdicts.md.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
PYTHON = hermetic.tool("python3")

CONFIG = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
GATE = CONFIG["tasks"]["test:typescript-properties"]["run"]
AUDIT = CONFIG["tasks"]["audit:typescript-properties"]["run"]
LINT = CONFIG["tasks"]["lint:typescript-properties"]["run"]

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
# the report; STUB_EXIT is its exit status. Asked to `list`, it answers
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
  results+="${results:+,}{\\"name\\": \\"${PWD}/${f}\\", \\"assertionResults\\": [{}]}"
done
echo "{\\"testResults\\": [${results}]}" > "${ORG_PROPERTY_REPORT:?}"
exit "${STUB_EXIT:-0}"
"""

# Every inherited GIT_* goes (#857), as in test_python_properties.py.
_GIT_ENV = {
    **hermetic.env(),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def plant(root: Path, files: dict[str, str], *, runner: bool) -> None:
    """Make `root` a git tree tracking `files`, with the stub installed."""
    subprocess.run([GIT, "init", "-q", str(root)], check=True, env=_GIT_ENV)  # ruff: ignore[subprocess-without-shell-equals-true]
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, "-C", str(root), "add", "-A"], check=True, env=_GIT_ENV
        )
    if runner:
        stub = root / "node_modules" / ".bin" / "vitest"
        stub.parent.mkdir(parents=True)
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)


class Ran(SimpleNamespace):
    """A finished body: exit status, output, and what the stub saw."""


def run(
    body: str,
    files: dict[str, str],
    *,
    runner: bool = True,
    env: dict[str, str] | None = None,
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
        plant(root, files, runner=runner)
        path = [str(Path(PYTHON).parent), str(Path(GIT).parent), "/usr/bin", "/bin"]
        environ = hermetic.env(
            PATH=os.pathsep.join(path),
            HOME=scratch,
            ORG_BELT_DIR=str(BELT),
            STUB_OUT=str(out),
            **env or {},
        )
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", body],
            cwd=root,
            env=environ,
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


class Skips(unittest.TestCase):
    """Nothing to run: exit 0, skipped, named."""

    def test_no_fast_check_skips_clean_by_name(self) -> None:
        """A vitest lockfile without fast-check: every leg skips."""
        files = {"pnpm-lock.yaml": LOCK_VITEST, "a.prop.test.ts": PROPERTY}
        for body, label in (
            (GATE, "test:typescript-properties"),
            (AUDIT, "audit:typescript-properties"),
            (LINT, "lint:typescript-properties"),
        ):
            with self.subTest(label=label):
                done = run(body, files)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(
                    done.stdout,
                    f"{label}: no fast-check in a tracked lockfile, skipped\n",
                )
                self.assertIsNone(done.argv)

    def test_no_lockfile_skips_clean_by_name(self) -> None:
        """No lockfile tracked at all: the same skip."""
        done = run(GATE, {"a.prop.test.ts": PROPERTY})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "test:typescript-properties: no fast-check in a tracked lockfile,"
            " skipped\n",
        )

    def test_an_untracked_lockfile_is_not_read(self) -> None:
        """A lockfile git does not track is not the repository's."""
        done = run(GATE, {"a.prop.test.ts": PROPERTY, ".gitignore": "pnpm-lock.yaml\n"})
        self.assertIn("no fast-check in a tracked lockfile, skipped", done.stdout)

    def test_no_property_file_skips_clean_by_name(self) -> None:
        """fast-check locked, no *.prop.test.*: skipped, named."""
        done = run(GATE, {"pnpm-lock.yaml": LOCK_BOTH, "a.test.ts": ORDINARY})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "test:typescript-properties: no *.prop.test.{ts,tsx,mts,cts} tracked,"
            " skipped\n",
        )
        self.assertIsNone(done.argv)


class Refusals(unittest.TestCase):
    """Properties tracked, but nothing the leg may run them with."""

    def test_no_vitest_in_the_lockfile_is_red(self) -> None:
        """fast-check locked, vitest not: red, named."""
        done = run(GATE, {"pnpm-lock.yaml": LOCK_FC_ONLY, "a.prop.test.ts": PROPERTY})
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:typescript-properties: *.prop.test.* tracked but the lockfile pins"
            " no vitest",
            done.stderr,
        )

    def test_vitest_not_installed_is_red(self) -> None:
        """Locked but no node_modules/.bin/vitest: red, with the remedy."""
        done = run(
            GATE,
            {"pnpm-lock.yaml": LOCK_BOTH, "a.prop.test.ts": PROPERTY},
            runner=False,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:typescript-properties: vitest is in the lockfile but not installed",
            done.stderr,
        )

    def test_a_bad_audit_seed_is_a_usage_error(self) -> None:
        """ORG_PROPERTY_SEED that is not an integer: exit 2, named."""
        done = run(
            AUDIT,
            {"pnpm-lock.yaml": LOCK_BOTH, "a.prop.test.ts": PROPERTY},
            env={"ORG_PROPERTY_SEED": "seven"},
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn(
            "ORG_PROPERTY_SEED must be a non-negative integer, got 'seven'",
            done.stderr,
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


class Gate(unittest.TestCase):
    """The gate leg: the fixed seed, and only the property files."""

    def test_the_runner_gets_the_fixed_seed_and_only_property_files(self) -> None:
        """Seed 1336, 200 runs, the two property files and nothing else."""
        done = run(GATE, GATE_FILES)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((done.seed, done.runs), ("1336", "200"))
        self.assertEqual(done.argv[:3], ["run", "--root", done.root])
        self.assertEqual(done.argv[3], "-c")
        self.assertTrue(done.argv[4].endswith("/org-property.config.mts"), done.argv)
        self.assertEqual(
            args_after_config(done.argv), ["src/a.prop.test.ts", "src/b.prop.test.tsx"]
        )
        self.assertEqual(
            done.stdout,
            "test:typescript-properties: seed 1336, 200 runs per property,"
            " vitest.config.ts\n"
            "test:typescript-properties: every property file held\n",
        )

    def test_two_runs_hand_the_runner_the_same_seed(self) -> None:
        """Deterministic: the seed does not move between runs."""
        first, second = run(GATE, GATE_FILES), run(GATE, GATE_FILES)
        self.assertEqual((first.seed, first.argv[5:]), (second.seed, second.argv[5:]))

    def test_the_gate_ignores_a_given_seed(self) -> None:
        """ORG_PROPERTY_SEED is the audit's replay, never the gate's."""
        done = run(GATE, GATE_FILES, env={"ORG_PROPERTY_SEED": "7"})
        self.assertEqual(done.seed, "1336")

    def test_the_wrapper_imports_the_repository_config(self) -> None:
        """The repo's config, the exact files and the belt's setup file."""
        done = run(GATE, GATE_FILES)
        self.assertIn(
            f"import * as repo from {json.dumps(done.root + '/vitest.config.ts')};",
            done.wrapper,
        )
        self.assertIn(
            'const files = ["src/a.prop.test.ts", "src/b.prop.test.tsx"];', done.wrapper
        )
        self.assertIn('exclude: ["**/node_modules/**", "**/.git/**"]', done.wrapper)
        self.assertRegex(
            done.wrapper, r'setupFiles: \[\.\.\.prior, ".*/org-property-setup\.mts"\]'
        )
        self.assertIn("test.projects", done.wrapper)

    def test_no_repository_config_still_runs(self) -> None:
        """No vitest config: the wrapper stands alone, named so."""
        files = {k: v for k, v in GATE_FILES.items() if k != "vitest.config.ts"}
        done = run(GATE, files)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("200 runs per property, no vitest config\n", done.stdout)
        self.assertIn("let config: any = {};", done.wrapper)
        self.assertNotIn("import * as repo", done.wrapper)

    def test_vitest_config_wins_over_vite_config(self) -> None:
        """Follow vitest's own lookup order: vitest.config.* first."""
        files = {**GATE_FILES, "vite.config.ts": "export default {};\n"}
        done = run(GATE, files)
        self.assertIn("200 runs per property, vitest.config.ts\n", done.stdout)

    def test_a_config_excluding_the_properties_passes(self) -> None:
        """The repo's own run collects only ordinary tests: green."""
        done = run(GATE, GATE_FILES, env={"STUB_COLLECTS": "src/c.test.ts"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNotNone(done.argv)

    def test_a_config_collecting_a_property_is_red_by_name(self) -> None:
        """The repo's own run would run a property unseeded: red, the fix."""
        done = run(
            GATE,
            GATE_FILES,
            env={"STUB_COLLECTS": "src/c.test.ts src/b.prop.test.tsx"},
        )
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            "test:typescript-properties: the repository's own vitest run collects"
            " these property files,\n"
            "  so its test task runs them a second time with no seed:\n"
            "    src/b.prop.test.tsx\n"
            "  add the property pattern to test.exclude in vitest.config.ts,"
            " beside vitest's\n"
            "  defaults (configDefaults from vitest/config):\n"
            '    exclude: [...configDefaults.exclude, "**/*.prop.test.*"],\n',
        )
        self.assertIsNone(done.argv, "nothing may run before the refusal")

    def test_a_listing_that_fails_is_red(self) -> None:
        """Refuse when vitest cannot resolve the repo's config: red, its output kept."""
        done = run(GATE, GATE_FILES, env={"STUB_LIST_EXIT": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:typescript-properties: vitest could not list the files the"
            " repository's own config collects:\n  listed\n",
            done.stderr,
        )

    def test_the_audit_does_not_ask(self) -> None:
        """The refusal is the gate's: the audit runs regardless."""
        done = run(AUDIT, AUDIT_FILES, env={"STUB_COLLECTS": "a.prop.test.ts"})
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_failing_property_is_red(self) -> None:
        """The runner's red is the leg's red, its status kept."""
        done = run(GATE, GATE_FILES, env={"STUB_EXIT": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("every property file held", done.stdout)

    def test_a_file_the_runner_did_not_run_is_red(self) -> None:
        """Green from the runner but a file absent from its report: red."""
        done = run(GATE, GATE_FILES, env={"STUB_SKIP": "src/b.prop.test.tsx"})
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "test:typescript-properties: not every property file ran:\n"
            "  src/b.prop.test.tsx: did not run, or ran no test\n",
            done.stderr,
        )


class Audit(unittest.TestCase):
    """The cron leg: a printed seed and a far larger run count."""

    def test_the_audit_prints_its_seed_first_and_honours_a_given_one(self) -> None:
        """ORG_PROPERTY_SEED reproduces a run; the seed line comes first."""
        done = run(AUDIT, AUDIT_FILES, env={"ORG_PROPERTY_SEED": "7"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(
            done.stdout.startswith(
                "audit:typescript-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
            ),
            done.stdout,
        )
        self.assertEqual((done.seed, done.runs), ("7", "10000"))

    def test_an_unset_seed_is_random_and_printed(self) -> None:
        """No seed given: a fresh one, and the printed one is the one used."""
        done = run(AUDIT, AUDIT_FILES)
        printed = re.match(
            r"audit:typescript-properties: seed (\d+)"
            r" \(reproduce: ORG_PROPERTY_SEED=\1\)\n",
            done.stdout,
        )
        self.assertIsNotNone(printed, done.stdout)
        self.assertEqual(done.seed, printed.group(1))

    def test_a_failure_says_where_the_example_lands(self) -> None:
        """Red at a seed: the seed and the remedy, on stderr."""
        done = run(AUDIT, AUDIT_FILES, env={"ORG_PROPERTY_SEED": "7", "STUB_EXIT": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "audit:typescript-properties: seed 7 — commit each counterexample above",
            done.stderr,
        )


def lint(files: dict[str, str]) -> Ran:
    """Run the lint with fast-check locked.

    Returns:
        The finished body.

    """
    return run(LINT, {"pnpm-lock.yaml": LOCK_BOTH, **files}, runner=False)


class Lint(unittest.TestCase):
    """Imports outside a property file, and restated seeds, by name."""

    def test_an_import_outside_a_property_file_is_red_by_name(self) -> None:
        """Every import form, in an ordinary test and a .js file."""
        done = lint({
            "a.prop.test.ts": PROPERTY,
            "src/plain.test.ts": PROPERTY,
            "src/more.ts": (
                'import fc from "fast-check";\n'
                'const m = await import("fast-check/lib/x");\n'
                'const r = require("@fast-check/jest");\n'
                'import "@fast-check/worker";\n'
            ),
            "src/helper.prop.test.js": 'export * from "fast-check";\n',
        })
        self.assertEqual(done.returncode, 1)
        suffix = " but is not a *.prop.test.{ts,tsx,mts,cts} file\n"
        for site, package in (
            ("src/helper.prop.test.js:1", "fast-check"),
            ("src/more.ts:1", "fast-check"),
            ("src/more.ts:2", "fast-check"),
            ("src/more.ts:3", "@fast-check/jest"),
            ("src/more.ts:4", "@fast-check/worker"),
            ("src/plain.test.ts:1", "@fast-check/vitest"),
        ):
            with self.subTest(site=site):
                self.assertIn(f"  {site}: imports {package}{suffix}", done.stderr)
        self.assertNotIn("a.prop.test.ts", done.stderr)

    def test_a_property_restating_the_seed_is_red_by_name(self) -> None:
        """seed, shorthand seed, numRuns, configureGlobal: each named."""
        done = lint({
            "s.prop.test.ts": PROPERTY + "fc.assert(p, { seed: 4 });\n",
            "h.prop.test.mts": PROPERTY + "fc.assert(p, { seed });\n",
            "n.prop.test.tsx": PROPERTY
            + 'test.prop([fc.nat()], { numRuns: 5 })("x", () => true);\n',
            "g.prop.test.cts": PROPERTY + "fc.configureGlobal({});\n",
        })
        self.assertEqual(done.returncode, 1)
        tail = "; the belt's seed and run count own every property\n"
        for site, what in (
            ("g.prop.test.cts:4", "configureGlobal()"),
            ("h.prop.test.mts:4", "seed"),
            ("n.prop.test.tsx:4", "numRuns"),
            ("s.prop.test.ts:4", "seed"),
        ):
            with self.subTest(site=site):
                self.assertIn(f"  {site}: restates {what}{tail}", done.stderr)

    def test_clean_files_pass_and_text_is_not_an_import(self) -> None:
        """A property file importing it, and the name in a string: clean."""
        done = lint({
            "a.prop.test.ts": PROPERTY,
            "b.ts": 'export const name = "fast-check";\n// from fast-check\n',
        })
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "lint:typescript-properties: 2 file(s) clean\n")

    def test_vendored_code_is_not_the_repository_s(self) -> None:
        """vendor/ is upstream's code (the belt's walk rule)."""
        done = lint({"vendor/x.ts": 'import fc from "fast-check";\n'})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "lint:typescript-properties: no TypeScript or JavaScript tracked,"
            " skipped\n",
        )


if __name__ == "__main__":
    unittest.main()

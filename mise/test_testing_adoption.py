#!/usr/bin/env python3
"""Table tests for `lint:test-adoption` (#1910).

A repository could ship a language with no mutation file and no
`.coverage-floor`, and every leg skipped clean, by name, green. Each
row plants a git tree tracking the named files, then runs the task's
own `run` body, read out of mise/config.toml, under bash with
ORG_BELT_DIR at this belt, so the real helper answers. Before #1910 the
task does not exist and every row fails on the lookup.

Each red row asserts its message, not just the exit status: a planted
row scored on exit alone can pass for the wrong reason.

The table is held to the legs: each mutation leg's guard (`git ls-files
--error-unmatch "${var}"` and the `var=` it reads) names the file that
adopts it, and the table must name the same one. A planted copy of the
legs' texts, in memory, with one name changed is red by name.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic
import testing_adoption

BELT = Path(__file__).resolve().parent
CANON = BELT.parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
TASK = "lint:test-adoption"
FLOOR = ".coverage-floor"
MUTATION = "tracking it adopts the leg; docs/tooling-verdicts.md says what it holds"
ADOPT = "mise run coverage:adopt writes it"
SKIPPED = f"{TASK}: ships none of Rust, Go, Python, TypeScript, skipped\n"
GUARD = re.compile(
    r'^if ! git ls-files --error-unmatch (?:-- )?"\$\{(\w+)\}"', re.MULTILINE
)


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], "the task's run")


def run(tree: Path, home: Path) -> subprocess.CompletedProcess[str]:
    """Run the lint's body in a tree, with the belt as ORG_BELT_DIR.

    Returns:
        The finished run.

    """
    as_mise = hermetic.task_run("lint:test-adoption", body())
    return subprocess.run(
        as_mise.argv,
        cwd=tree,
        env=hermetic.env(home, ORG_BELT_DIR=str(BELT)) | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


def lint(files: list[str]) -> subprocess.CompletedProcess[str]:
    """Plant a tree tracking files and run the lint in it.

    Returns:
        The finished run.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "repo"
        tree.mkdir()
        env = hermetic.env(root / "home")
        (tree / "README.md").write_text("x\n", encoding="utf-8")
        for name in files:
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
        return run(tree, root / "home")


def guarded(text: str) -> str | None:
    """Read the file a mutation leg's guard skips without.

    Returns:
        The value of the variable the leg's first `git ls-files
        --error-unmatch` guard reads, or None when the leg has no guard.

    """
    found = GUARD.search(text)
    if found is None:
        return None
    assigned = re.search(rf'^{found.group(1)}="?([^"\s]+)"?$', text, re.MULTILINE)
    return assigned.group(1) if assigned else None


def legs() -> dict[str, str]:
    """Read each mutation leg the table names, from this belt.

    Returns:
        Each leg's file name and text.

    """
    return {
        language.leg: (BELT / language.leg).read_text(encoding="utf-8")
        for language in testing_adoption.LANGUAGES
    }


def drift(texts: dict[str, str]) -> list[str]:
    """Compare the table against the legs' texts.

    Returns:
        One line per language whose leg guards on another file.

    """
    lines = []
    for language in testing_adoption.LANGUAGES:
        leg = guarded(texts[language.leg])
        if leg != language.mutation:
            lines.append(
                f"{language.name}: the table names {language.mutation},"
                f" {language.leg} guards on {leg}"
            )
    return lines


class Adoption(unittest.TestCase):
    """A shipped language tracks its mutation file and a floor."""

    def test_rust_without_its_mutation_file_is_red(self) -> None:
        """`Cargo.toml` with no `.cargo/mutants.toml`: red, naming all three."""
        done = lint(["Cargo.toml", "src/lib.rs", FLOOR])
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            f"{TASK}: ships Rust (Cargo.toml) but tracks no"
            f" .cargo/mutants.toml — {MUTATION}\n",
            done.stderr,
        )

    def test_go_without_a_floor_is_red(self) -> None:
        """`go.mod` and `.gomuignore` with no floor: red, naming `coverage:adopt`."""
        done = lint(["go.mod", ".gomuignore", "main.go"])
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertEqual(
            f"{TASK}: ships Go (go.mod) but tracks no {FLOOR} — {ADOPT}\n",
            done.stderr,
        )

    def test_each_missing_file_is_its_own_line(self) -> None:
        """Two languages, nothing adopted: a line per mutation file, one floor line."""
        done = lint(["crates/b/Cargo.toml", "Cargo.toml", "tools/x.py"])
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertEqual(
            f"{TASK}: ships Rust (Cargo.toml) but tracks no"
            f" .cargo/mutants.toml — {MUTATION}\n"
            f"{TASK}: ships Python (tools/x.py) but tracks no"
            f" cosmic-ray.toml — {MUTATION}\n"
            f"{TASK}: ships Rust (Cargo.toml), Python (tools/x.py)"
            f" but tracks no {FLOOR} — {ADOPT}\n",
            done.stderr,
        )

    def test_python_tests_alone_ship_no_python(self) -> None:
        """A tree whose only `.py` is a test, property or conftest is green."""
        done = lint(["tests/test_x.py", "tests/prop_x.py", "tests/conftest.py"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIPPED, done.stdout)

    def test_python_without_cosmic_ray_is_red(self) -> None:
        """One non-test `.py` is shipped Python: red without `cosmic-ray.toml`."""
        done = lint(["tools/x.py", FLOOR])
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertEqual(
            f"{TASK}: ships Python (tools/x.py) but tracks no"
            f" cosmic-ray.toml — {MUTATION}\n",
            done.stderr,
        )

    def test_typescript_without_stryker_is_red(self) -> None:
        """A TypeScript source with no `stryker.config.json`: red."""
        done = lint(["src/a.ts", FLOOR])
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertEqual(
            f"{TASK}: ships TypeScript (src/a.ts) but tracks no"
            f" stryker.config.json — {MUTATION}\n",
            done.stderr,
        )

    def test_typescript_declarations_and_tests_ship_nothing(self) -> None:
        """Declarations, tests and specs alone are green."""
        done = lint(["src/a.d.ts", "src/a.test.ts", "src/b.spec.tsx", "src/c.d.mts"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIPPED, done.stdout)

    def test_fuzz_and_vendored_crates_ship_nothing(self) -> None:
        """A `Cargo.toml` only under `fuzz/` or `vendor/` is green."""
        for files in (["fuzz/Cargo.toml"], ["vendor/dep/Cargo.toml"]):
            with self.subTest(files=files):
                done = lint(files)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual(SKIPPED, done.stdout)

    def test_vendored_python_and_typescript_ship_nothing(self) -> None:
        """vendor/ is someone else's code, as in every belt lint."""
        done = lint(["vendor/lib/x.py", "vendor/lib/a.ts"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIPPED, done.stdout)

    def test_an_adopted_tree_is_green_in_each_language(self) -> None:
        """Each language with its mutation file and a floor is green."""
        for name, files in (
            ("Rust", ["Cargo.toml", ".cargo/mutants.toml"]),
            ("Go", ["go.mod", ".gomuignore"]),
            ("Python", ["tools/x.py", "cosmic-ray.toml"]),
            ("TypeScript", ["src/a.ts", "stryker.config.json"]),
        ):
            with self.subTest(language=name):
                done = lint([*files, FLOOR])
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual(f"{TASK}: ok ({name} adopted)\n", done.stdout)

    def test_a_nested_go_mod_is_not_shipped_go(self) -> None:
        """Go ships from the root `go.mod`, as the Go leg reads it."""
        done = lint(["tools/go.mod"])
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(SKIPPED, done.stdout)

    def test_the_canon_tree_is_green(self) -> None:
        """The canon's own tracked tree passes."""
        with tempfile.TemporaryDirectory() as tmp:
            done = run(CANON, Path(tmp) / "home")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"{TASK}: ok (Python adopted)\n", done.stdout)


class TableAgainstLegs(unittest.TestCase):
    """The table names the file each mutation leg guards on."""

    def test_the_table_matches_the_legs(self) -> None:
        """Every leg's guard names the table's file."""
        agreed: list[str] = []
        self.assertEqual(agreed, drift(legs()))

    def test_a_renamed_leg_file_is_red_by_name(self) -> None:
        """A planted copy whose Go leg guards on another file drifts, named."""
        texts = legs()
        text = texts["mutants-go.sh"]
        planted = text.replace('adoption=".gomuignore"', 'adoption=".gomutants"')
        self.assertNotEqual(text, planted)
        texts["mutants-go.sh"] = planted
        expected: list[str] = [
            "Go: the table names .gomuignore, mutants-go.sh guards on .gomutants"
        ]
        self.assertEqual(expected, drift(texts))

    def test_a_leg_without_a_guard_is_red(self) -> None:
        """A leg that lost its guard reads as no file, not as agreement."""
        texts = legs()
        text = texts["mutants-python.sh"]
        unguarded = text.replace("--error-unmatch", "--others")
        self.assertNotEqual(text, unguarded)
        texts["mutants-python.sh"] = unguarded
        line = (
            "Python: the table names cosmic-ray.toml, mutants-python.sh guards on None"
        )
        expected: list[str] = [line]
        self.assertEqual(expected, drift(texts))


if __name__ == "__main__":
    unittest.main()

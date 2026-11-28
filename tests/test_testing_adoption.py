"""Table tests for `lint:test-adoption` (#1910).

A repository could ship a language with no mutation file and no
`.coverage-floor`, and every leg skipped clean, by name, green. Each
row hands the judgment, in this process, a tracked list of the named
files on stdin, as `org_tracked` pipes it. One row runs the task's
own `run` body, read out of mise/config.toml, under bash with
ORG_BELT_DIR at this belt, over the canon's own tree: the wiring.

Each red row asserts its message, not just the exit status: a planted
row scored on exit alone can pass for the wrong reason.

The table is held to the legs: each mutation leg's guard
(`org_tracked_has "${var}"`, #2013, and the `var=` it reads) names the
file that adopts it, and the table must name the same one. A planted copy of the
legs' texts, in memory, with one name changed is red by name.
"""

import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

import pytest

from belt import decoded, testing_adoption
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

BELT = support.MISE
CANON = BELT.parent
BASH = support.tool("bash")
TASK = "lint:test-adoption"
FLOOR = ".coverage-floor"
MUTATION = "tracking it adopts the leg; docs/tooling-verdicts.md says what it holds"
ADOPT = "mise run coverage:adopt writes it"
SKIPPED = f"{TASK}: ships none of Rust, Go, Python, TypeScript, skipped\n"
GUARD = re.compile(r'^if ! org_tracked_has "\$\{(\w+)\}"', re.MULTILINE)
# The Python leg is a belt command: its adoption file is a constant.
SETUP = re.compile(r'^SETUP = "([^"]+)"$', re.MULTILINE)


def body() -> str:
    """Read the task's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], "the task's run")


def run(
    tree: Path, home: Path, *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the lint's body in a tree, with the belt as ORG_BELT_DIR.

    Returns:
        The finished run.

    """
    as_mise = support.task_run("lint:test-adoption", body())
    return subprocess.run(
        as_mise.argv,
        cwd=tree,
        env=env(home, ORG_BELT_DIR=str(BELT)) | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


class Judged(NamedTuple):
    """What one in-process judgment answered."""

    returncode: int
    stdout: str
    stderr: str


class Lint(Protocol):
    """Judge a tracked tree of the named files, in this process."""

    def __call__(self, files: list[str]) -> Judged:
        """Judge the files, with a README beside them, as `org_tracked` lists them."""
        ...


@fixture
def lint(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    as_task: Callable[[str], None],
) -> Lint:
    """Give a row the judgment over a tracked list, read from stdin.

    Returns:
        The judge: the files, NUL-separated on stdin, then `main`.

    """
    as_task(TASK)

    def judged(files: list[str]) -> Judged:
        listing = "\0".join(["README.md", *files])
        monkeypatch.setattr(sys, "stdin", io.StringIO(listing))
        status = testing_adoption.main([])
        out, err = capfd.readouterr()
        return Judged(status, out, err)

    return judged


def guarded(text: str) -> str | None:
    """Read the file a mutation leg's guard skips without.

    Returns:
        The value of the variable the leg's first `org_tracked_has`
        guard reads, or None when the leg has no guard.

    """
    setup = SETUP.search(text)
    if setup is not None:
        return setup.group(1)
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
        language.leg: (
            (support.PACKAGE if language.leg.endswith(".py") else BELT) / language.leg
        ).read_text(encoding="utf-8")
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


# A shipped language tracks its mutation file and a floor.
def test_rust_without_its_mutation_file_is_red(lint: Lint) -> None:
    """`Cargo.toml` with no `.cargo/mutants.toml`: red, naming all three."""
    done = lint(["Cargo.toml", "src/lib.rs", FLOOR])
    assert done.returncode == 1, done.stdout
    assert not done.stdout
    assert (
        f"{TASK}: ships Rust (Cargo.toml) but tracks no"
        f" .cargo/mutants.toml — {MUTATION}\n"
    ) == done.stderr


def test_go_without_a_floor_is_red(lint: Lint) -> None:
    """`go.mod` and `.gomuignore` with no floor: red, naming `coverage:adopt`."""
    done = lint(["go.mod", ".gomuignore", "main.go"])
    assert done.returncode == 1, done.stdout
    assert f"{TASK}: ships Go (go.mod) but tracks no {FLOOR} — {ADOPT}\n" == done.stderr


def test_each_missing_file_is_its_own_line(lint: Lint) -> None:
    """Two languages, nothing adopted: a line per mutation file, one floor line."""
    done = lint(["crates/b/Cargo.toml", "Cargo.toml", "tools/x.py"])
    assert done.returncode == 1, done.stdout
    assert (
        f"{TASK}: ships Rust (Cargo.toml) but tracks no"
        f" .cargo/mutants.toml — {MUTATION}\n"
        f"{TASK}: ships Python (tools/x.py) but tracks no"
        f" setup.cfg — {MUTATION}\n"
        f"{TASK}: ships Rust (Cargo.toml), Python (tools/x.py)"
        f" but tracks no {FLOOR} — {ADOPT}\n"
    ) == done.stderr


def test_python_tests_alone_ship_no_python(lint: Lint) -> None:
    """A tree whose only `.py` is a test, property or conftest is green."""
    done = lint(["tests/test_x.py", "tests/prop_x.py", "tests/conftest.py"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == SKIPPED


def test_python_without_a_mutmut_section_is_red(lint: Lint) -> None:
    """One non-test `.py` is shipped Python: red without `setup.cfg`."""
    done = lint(["tools/x.py", FLOOR])
    assert done.returncode == 1, done.stdout
    assert (
        f"{TASK}: ships Python (tools/x.py) but tracks no setup.cfg — {MUTATION}\n"
    ) == done.stderr


def test_typescript_without_stryker_is_red(lint: Lint) -> None:
    """A TypeScript source with no `stryker.config.json`: red."""
    done = lint(["src/a.ts", FLOOR])
    assert done.returncode == 1, done.stdout
    assert (
        f"{TASK}: ships TypeScript (src/a.ts) but tracks no"
        f" stryker.config.json — {MUTATION}\n"
    ) == done.stderr


def test_typescript_declarations_and_tests_ship_nothing(lint: Lint) -> None:
    """Declarations, tests and specs alone are green."""
    done = lint(["src/a.d.ts", "src/a.test.ts", "src/b.spec.tsx", "src/c.d.mts"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == SKIPPED


@pytest.mark.parametrize("files", [["fuzz/Cargo.toml"], ["vendor/dep/Cargo.toml"]])
def test_fuzz_and_vendored_crates_ship_nothing(files: list[str], lint: Lint) -> None:
    """A `Cargo.toml` only under `fuzz/` or `vendor/` is green."""
    done = lint(files)
    assert done.returncode == 0, done.stderr
    assert done.stdout == SKIPPED


def test_vendored_python_and_typescript_ship_nothing(lint: Lint) -> None:
    """vendor/ is someone else's code, as in every belt lint."""
    done = lint(["vendor/lib/x.py", "vendor/lib/a.ts"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == SKIPPED


@pytest.mark.parametrize(
    ("name", "files"),
    [
        ("Rust", ["Cargo.toml", ".cargo/mutants.toml"]),
        ("Go", ["go.mod", ".gomuignore"]),
        ("Python", ["tools/x.py", "setup.cfg"]),
        ("TypeScript", ["src/a.ts", "stryker.config.json"]),
    ],
)
def test_an_adopted_tree_is_green_in_each_language(
    name: str, files: list[str], lint: Lint
) -> None:
    """Each language with its mutation file and a floor is green."""
    done = lint([*files, FLOOR])
    assert done.returncode == 0, done.stderr
    assert f"{TASK}: ok ({name} adopted)\n" == done.stdout


def test_a_nested_go_mod_is_not_shipped_go(lint: Lint) -> None:
    """Go ships from the root `go.mod`, as the Go leg reads it."""
    done = lint(["tools/go.mod"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == SKIPPED


def test_the_canon_tree_is_green(env: support.Env) -> None:
    """The canon's own tracked tree passes."""
    with tempfile.TemporaryDirectory() as tmp:
        done = run(CANON, Path(tmp) / "home", env=env)
    assert done.returncode == 0, done.stderr
    assert f"{TASK}: ok (Python adopted)\n" == done.stdout


# The table names the file each mutation leg guards on.
def test_the_table_matches_the_legs() -> None:
    """Every leg's guard names the table's file."""
    agreed: list[str] = []
    assert agreed == drift(legs())


def test_a_renamed_leg_file_is_red_by_name() -> None:
    """A planted copy whose Go leg guards on another file drifts, named."""
    texts = legs()
    text = texts["mutants-go.sh"]
    planted = text.replace('adoption=".gomuignore"', 'adoption=".gomutants"')
    assert text != planted
    texts["mutants-go.sh"] = planted
    expected: list[str] = [
        "Go: the table names .gomuignore, mutants-go.sh guards on .gomutants"
    ]
    assert expected == drift(texts)


def test_a_leg_without_a_guard_is_red() -> None:
    """A leg that lost its guard reads as no file, not as agreement."""
    texts = legs()
    text = texts["mutants-rust.sh"]
    unguarded = text.replace("org_tracked_has", "org_tracked")
    assert text != unguarded
    texts["mutants-rust.sh"] = unguarded
    line = "Rust: the table names .cargo/mutants.toml, mutants-rust.sh guards on None"
    expected: list[str] = [line]
    assert expected == drift(texts)

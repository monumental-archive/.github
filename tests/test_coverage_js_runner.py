"""Tests for coverage_js_runner.py's readers (#1659, #2105).

The runner line a tree's package.json and lockfile answer, and the
vitest run `measured` reads off its own command line (`--measured-run`).
"""

import contextlib
import io
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import coverage_js_runner as cj
from belt import decoded
from belt.typed_pytest import fixture

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "coverage:check"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


# Every shape the runner reader decides (#1659).
def test_other_runners_are_named_from_the_manifest() -> None:
    """A dependency, a `test` script, npm's placeholder, or nothing."""
    placeholder = 'echo "Error: no test specified" && exit 1'
    for manifest, runner in (
        ({"devDependencies": {"cypress": "13"}}, "cypress"),
        ({"dependencies": "not a table", "scripts": {"test": "ava -v"}}, "ava"),
        ({"scripts": {"test": "node --test src/"}}, "node --test"),
        ({"scripts": {"test": placeholder}}, None),
        ({"scripts": {"test": "   "}}, None),
        ({"scripts": "not a table"}, None),
    ):
        assert runner == cj.other_runner(cj.read_manifest(manifest))


# One lockfile of each format the reader answers, and the versions each
# resolves for its package.
PNPM_LOCK = (
    "packages:\n\n  vitest@4.1.12:\n    resolution: {}\n\nsnapshots:\n\n"
    "  vitest@4.1.12(@types/node@22.0.0):\n    dependencies: {}\n"
    "  vitest@3.2.4:\n    dependencies: {}\n"
)
# A nested node_modules entry is another package's copy, not the root's.
NPM_LOCK = (
    '{"packages": {"node_modules/vitest": {"version": "4.1.12"},'
    ' "node_modules/x/node_modules/vitest": {"version": "1.0.0"}}}'
)
YARN_LOCK = 'vitest@^4, vitest@^4.1.0:\n  version "4.1.12"\n  resolved "x"\n'
BERRY_LOCK = '"vitest@npm:^4":\n  version: 4.1.12\n  resolution: "vitest@npm:4.1.12"\n'
BUN_LOCK = '{"packages": {"vitest": ["vitest@4.1.12", "", {}, "sha"]}}'
CARGO_LOCK = (
    '[[package]]\nname = "serde"\nversion = "1.0.154"\n\n'
    '[[package]]\nname = "serde_json"\nversion = "1.0.140"\n'
)
NONE: set[str] = set()
ONE: set[str] = {"4.1.12"}
TWO: set[str] = {"4.1.12", "3.2.4"}
CRATE: set[str] = {"1.0.154"}


# The version each lockfile resolves an entry to (#2145).
@pytest.mark.parametrize(
    ("lockfile", "lock", "name", "want"),
    [
        ("pnpm-lock.yaml", PNPM_LOCK, "vitest", TWO),
        ("package-lock.json", NPM_LOCK, "vitest", ONE),
        ("yarn.lock", YARN_LOCK, "vitest", ONE),
        ("yarn.lock", BERRY_LOCK, "vitest", ONE),
        ("bun.lock", BUN_LOCK, "vitest", ONE),
        ("Cargo.lock", CARGO_LOCK, "serde", CRATE),
    ],
)
def test_every_format_reads_the_installed_version(
    lockfile: object, lock: object, name: object, want: object
) -> None:
    """The version installed, never the range the entry was asked for."""
    assert want == cj.locked(lockfile, lock, name)


def test_an_indented_yarn_mention_is_not_an_entry() -> None:
    """Berry's `resolution:` line repeats an entry; alone it is none."""
    lock = '"other@npm:^1":\n  version: 1.0.0\n  resolution: "vitest@npm:4.1.12"\n'
    assert cj.locked("yarn.lock", lock, "vitest") == NONE


def test_a_name_is_matched_whole_and_literally() -> None:
    """`vitest` is not `@vitest/expect`, and a dot in a name is a dot."""
    lock = "packages:\n\n  '@vitest/expect@4.1.12':\n  lodashxget@4.4.2:\n"
    assert cj.locked("pnpm-lock.yaml", lock, "vitest") == NONE
    assert cj.locked("pnpm-lock.yaml", lock, "lodash.get") == NONE
    assert cj.locked("pnpm-lock.yaml", lock, "@vitest/expect") == ONE


@pytest.mark.parametrize(
    ("lockfile", "lock"),
    [
        ("package-lock.json", '{"packages": {"node_modules/vitest": {}}}'),
        ("yarn.lock", 'vitest@^4:\n  resolved "x"\n'),
    ],
)
def test_an_entry_without_a_version_reads_none(lockfile: object, lock: object) -> None:
    """An entry whose version is not where its format writes one."""
    assert cj.locked(lockfile, lock, "vitest") == NONE


@pytest.mark.parametrize(
    "lock", ["[[package]\n", "package = 1\n", '[[package]]\nname = "a"\n']
)
def test_a_cargo_lock_of_the_wrong_shape_is_refused(lock: object) -> None:
    """Not TOML, or a package table of another shape, raises by where."""
    with pytest.raises(decoded.UndecodableError):
        cj.locked("Cargo.lock", lock, "a")


class Main:
    """The runner line for a planted tree's package.json (#1659)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def main(self, tracked: list[str], files: dict[str, str]) -> tuple[int, str, str]:
        """Run main from a scratch tree holding `files`.

        Returns:
            The status, stdout and stderr.

        """
        tmp = tempfile.mkdtemp(dir=self.tmp)
        for name, text in files.items():
            (Path(tmp) / name).write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(tmp),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = cj.main(tracked)
        return status, out.getvalue(), err.getvalue()


@fixture
def main_state(tmp_path: Path) -> Main:
    """Give each row its own Main."""
    return Main(tmp_path)


def test_no_tracked_package_json_prints_nothing(main_state: Main) -> None:
    """An untracked package.json is never read."""
    assert main_state.main([], {"package.json": "{"}) == (0, "", "")


def test_an_unreadable_package_json_is_refused(main_state: Main) -> None:
    """Broken JSON is exit 1, named, never read as no runner."""
    status, out, err = main_state.main(["package.json"], {"package.json": "{"})
    assert (status, out) == (1, "")
    assert err.startswith(f"{TASK}: package.json: "), err


def test_a_package_json_that_is_not_an_object_names_no_runner(main_state: Main) -> None:
    """A JSON array declares nothing."""
    assert main_state.main(["package.json"], {"package.json": "[]"}) == (0, "", "")


def test_vitest_declared_without_a_lockfile_entry_is_named(main_state: Main) -> None:
    """Declared but not pinned: nothing the leg may run."""
    declared: dict[str, object] = {"devDependencies": {"vitest": "4.1.12"}}
    manifest = json.dumps(declared)
    assert main_state.main(["package.json"], {"package.json": manifest}) == (
        0,
        "unsupported vitest-without-a-lockfile-entry\n",
        "",
    )


def test_another_runner_is_named(main_state: Main) -> None:
    """A jest tree is named so the skip says what it skipped."""
    declared: dict[str, object] = {"devDependencies": {"jest": "29"}}
    manifest = json.dumps(declared)
    assert main_state.main(["package.json"], {"package.json": manifest}) == (
        0,
        "unsupported jest\n",
        "",
    )


FLAGS = [
    "--coverage.enabled=true",
    "--coverage.provider=v8",
    "--coverage.reporter=json-summary",
    "--coverage.reporter=lcov",
    "--coverage.reportsDirectory=/run/typescript",
]


class MeasuredRun:
    """`--measured-run`: the vitest run on `measured`'s line (#2105)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def call(
        self,
        *command: str,
        files: dict[str, str] | None = None,
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str]:
        """Call main in a scratch tree of a pnpm vitest tree with v8.

        Returns:
            The status, stdout and stderr.

        """
        lock = (
            "lockfileVersion: '9.0'\npackages:\n"
            "  vitest@4.1.12:\n  '@vitest/coverage-v8@4.1.12':\n"
        )
        planted = {"package.json": "{}", "pnpm-lock.yaml": lock} | (files or {})
        tmp = tempfile.mkdtemp(dir=self.tmp)
        for name, text in planted.items():
            (Path(tmp) / name).write_text(text, encoding="utf-8")
        argv = ["--measured-run", "/run/typescript", *planted, "--", *command]
        with pytest.MonkeyPatch.context() as patched:
            patched.chdir(tmp)
            status = cj.main(argv)
        out, err = capfd.readouterr()
        return status, out, err


@fixture
def measured_run_state(tmp_path: Path) -> MeasuredRun:
    """Give each row its own MeasuredRun."""
    return MeasuredRun(tmp_path)


def test_a_launched_run_gains_the_flags(
    measured_run_state: MeasuredRun, capfd: pytest.CaptureFixture[str]
) -> None:
    """`pnpm exec vitest run x`: the five flags, NUL-separated."""
    status, out, err = measured_run_state.call(
        "pnpm", "exec", "vitest", "run", "x", capfd=capfd
    )
    assert (status, err) == (0, "")
    want = ["pnpm", "exec", "vitest", "run", "x", *FLAGS]
    assert "".join(f"{w}\0" for w in want) == out


@pytest.mark.usefixtures("measured_run_state")
def test_no_subcommand_is_run() -> None:
    """A bare vitest is its watch mode; `run` is inserted."""
    got = cj.measured_run(
        "v8", "/run/typescript", ["node_modules/.bin/vitest", "--silent"]
    )
    want: list[str] = ["node_modules/.bin/vitest", "run", "--silent", *FLAGS]
    assert want == got


@pytest.mark.parametrize(
    "command",
    [
        ["vitest", "list"],
        ["bunx", "vitest", "run"],
        ["python3", "-m", "unittest"],
        [],
    ],
)
def test_what_is_not_a_run_is_unchanged(
    measured_run_state: MeasuredRun, command: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """Another subcommand, another launcher, or no vitest: nothing."""
    assert measured_run_state.call(*command, capfd=capfd) == (0, "", "")


def test_a_tree_the_leg_reports_on_is_unchanged(
    measured_run_state: MeasuredRun, capfd: pytest.CaptureFixture[str]
) -> None:
    """No provider pinned: the leg names it; measured adds nothing."""
    lock = "lockfileVersion: '9.0'\npackages:\n  vitest@4.1.12:\n"
    assert measured_run_state.call(
        "vitest", "run", files={"pnpm-lock.yaml": lock}, capfd=capfd
    ) == (0, "", "")


def test_a_coverage_flag_of_its_own_is_refused(
    measured_run_state: MeasuredRun, capfd: pytest.CaptureFixture[str]
) -> None:
    """The leg owns the coverage flags: exit 1, named."""
    status, out, err = measured_run_state.call(
        "vitest", "run", "--coverage.enabled=false", capfd=capfd
    )
    assert (status, out) == (1, "")
    assert err.startswith("measured: --coverage.enabled=false is on the vitest run"), (
        err
    )


@pytest.mark.usefixtures("measured_run_state")
def test_no_separator_is_a_usage_error(capfd: pytest.CaptureFixture[str]) -> None:
    """The command follows `--`; without it nothing is read."""
    status = cj.main(["--measured-run", "/d"])
    _out, err = capfd.readouterr()
    assert status == 2
    assert "usage" in err

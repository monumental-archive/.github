"""Table tests for the EXIT-trap completion guard (#1233).

Three tables. The lint rows plant scripts and task bodies and assert
the exact message each one reds with, strict on the clean rows, so a
row cannot pass by reddening for some other reason; each form of the
guard is planted under each shebang (#1251). The pattern rows run the
guard itself under `/bin/bash` where that is 3.2 — macOS, the shell the
defect lives in — and assert the exit code of each: the defect as
measured with a bare trap, then the same bodies under either form of
the guard. Where `/bin/bash` is not 3.2 (every runner) they skip by
name; bash 5 never had the defect, so there is nothing for them to show
there. The POSIX rows run the printed guard in a `#!/bin/sh` script
under `/bin/sh` and under `dash`, each against its own measured table,
and skip by name where that shell is absent or is neither.
"""

import itertools
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from belt import exit_trap as et
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:bash-portability"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BASH = Path("/bin/bash")
SH = Path("/bin/sh")
DASH = support.optional_tool("dash")
GUARDED = et.GUARD.format(cleanup='rm -f "${tmp}"')
BASH_GUARDED = et.BASH_GUARD.format(cleanup='rm -f "${tmp}"')
BARE = "trap 'rm -f \"${tmp}\"' EXIT"
NOT_GUARDED = "EXIT trap without the completion guard"
NOT_MARKED = "has an EXIT trap and its last statement is not org_done=1"
NOT_BASH = (
    "EXIT trap guard uses [[ ]], which the script's shell does not run:"
    " it has no bash shebang"
)
# The shape travels with every red, so the message hands over the lines to
# paste rather than a description of them.
SHAPE_LINE = et.GUARD.format(cleanup="<cleanup>")


def bash_version(shell: Path = BASH) -> str:
    """Report the bash a shell is, or "" when it is none or absent.

    Returns:
        The `BASH_VERSION` it prints; empty for dash, which has none.

    """
    if not shell.is_file():
        return ""
    done = subprocess.run(
        [str(shell), "-c", 'echo "${BASH_VERSION:-}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


IS_32 = bash_version().startswith("3.2")
SH_IS_32 = bash_version(SH).startswith("3.2")
SH_IS_DASH = SH.is_file() and Path(SH).resolve().name == "dash"


@fixture
def plant(tmp_path: Path) -> Callable[..., list[str]]:
    """Plant named files for the lint, the way the task lists them.

    Returns:
        A function that writes `(name, text)` pairs into a fresh
        directory and returns their paths.

    """
    made = itertools.count()

    def lay_out(*named: tuple[str, str]) -> list[str]:
        scratch = tmp_path / f"planted{next(made)}"
        scratch.mkdir()
        paths = []
        for name, text in named:
            path = scratch / name
            path.write_text(text, encoding="utf-8")
            paths.append(str(path))
        return paths

    return lay_out


def said(err: str, paths: list[str]) -> tuple[list[str], bool]:
    """Read the lint's stderr over planted paths.

    Returns:
        The finding lines, with the planted directory stripped so the
        rows read as relative paths; and whether the shape to paste was
        printed.

    """
    scratch = Path(paths[0]).parent if paths else Path()
    lines = [
        line.removeprefix(f"{TASK}: {scratch}/")
        for line in err.splitlines()
        if line.startswith(f"{TASK}: ")
    ]
    return lines, SHAPE_LINE in err


def script(*body: str, shebang: str = "#!/usr/bin/env bash") -> str:
    """Build a script under the interpreter flags the belt runs with.

    A bash shebang gets bash's flags; any other gets POSIX `set -eu`, and
    an empty one leaves the line out.

    Returns:
        The script text.

    """
    flags = "set -euo pipefail" if "bash" in shebang else "set -eu"
    head = [shebang] if shebang else []
    return "\n".join([*head, flags, *body, ""])


# Shebangs, and whether each names bash.
SHEBANGS = [
    ("#!/usr/bin/env bash", True),
    ("#!/bin/bash", True),
    ("#!/usr/bin/env -S bash -eu", True),
    ("#!/bin/sh", False),
    ("#!/usr/bin/env sh", False),
    ("#!/bin/dash", False),
    ("", False),
]


# The rule over planted scripts and task bodies.
def test_unguarded_trap_reds_both_ways(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """A bare trap owes the guard, and the mark it reads."""
    paths = plant(("a.sh", script("tmp=$(mktemp)", BARE, "echo ok")))
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [f"a.sh:4: {NOT_GUARDED}", f"a.sh:5: {NOT_MARKED}"]
    assert lines == want


def test_guarded_without_the_mark_reds(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """The guard alone turns every clean run into a failure."""
    paths = plant(
        (
            "a.sh",
            script("tmp=$(mktemp)", GUARDED, "echo ok"),
        )
    )
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [f"a.sh:5: {NOT_MARKED}"]
    assert lines == want


def test_mark_not_last_reds(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """A mark followed by more script is not the last statement."""
    paths = plant(("a.sh", script(GUARDED, "org_done=1", 'exit "${status}"')))
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [f"a.sh:5: {NOT_MARKED}"]
    assert lines == want


def test_guard_on_a_combined_signal_list_reds(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """The shape is EXIT alone; `EXIT INT` is a different trap."""
    paths = plant(
        ("a.sh", script(GUARDED.replace("' EXIT", "' EXIT INT"), "org_done=1"))
    )
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [f"a.sh:3: {NOT_GUARDED}"]
    assert lines == want


def test_guarded_and_marked_is_clean(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """The two lines, with comments and blanks after the mark."""
    paths = plant(
        (
            "a.sh",
            script(
                "tmp=$(mktemp)",
                et.PREASSIGN,
                GUARDED,
                "echo ok",
                "org_done=1  # the last statement",
                "",
                "# trailing prose",
            ),
        )
    )
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, lines, shaped) == (0, [], False)


def test_each_form_under_each_shebang(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """POSIX is clean anywhere; `[[ ]]` only under a bash shebang."""
    for shebang, is_bash in SHEBANGS:
        body = script(
            "tmp=$(mktemp)",
            et.PREASSIGN,
            "{guard}",
            "echo ok",
            "org_done=1",
            shebang=shebang,
        )
        # The guard is the fifth line, or the fourth without a shebang.
        line = 5 if shebang else 4
        for guard, want in (
            (GUARDED, []),
            (BASH_GUARDED, [] if is_bash else [f"a.sh:{line}: {NOT_BASH}"]),
        ):
            paths = plant(("a.sh", body.format(guard=guard)))
            status = et.main(paths)
            lines, shaped = said(capfd.readouterr().err, paths)
            assert (status, lines, shaped) == (
                1 if want else 0,
                want,
                bool(want),
            )


def test_bare_trap_under_sh_reds_as_unguarded(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """Under `#!/bin/sh` a bare trap owes the guard as anywhere."""
    paths = plant(("a.sh", script(BARE, "org_done=1", shebang="#!/bin/sh")))
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [f"a.sh:3: {NOT_GUARDED}"]
    assert lines == want


def test_task_body_takes_either_form(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """A task body runs under the belt's bash: both forms are clean."""
    config = (
        '[tasks."lint:a"]\n'
        "run = '''\n"
        f"{et.PREASSIGN}\n{GUARDED}\norg_done=1\n"
        "'''\n\n"
        '[tasks."lint:b"]\n'
        "run = '''\n"
        f"{et.PREASSIGN}\n{BASH_GUARDED}\norg_done=1\n"
        "'''\n"
    )
    paths = plant(("mise.toml", config))
    status = et.main(paths)
    assert (status, *said(capfd.readouterr().err, paths)) == (0, [], False)


def test_no_trap_and_reset_owe_nothing(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """No EXIT trap, a comment naming one, and `trap - EXIT`."""
    paths = plant(
        ("a.sh", script("echo ok")),
        ("b.sh", script(f"# {BARE}", "trap - EXIT", "echo ok")),
    )
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, lines, shaped) == (0, [], False)


def test_task_body_points_at_the_config_line(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """Each body is its own script; findings carry the file line."""
    config = (
        '[tasks."lint:a"]\n'
        "run = '''\n"
        "tmp=$(mktemp)\n"
        f"{BARE}\n"
        "echo ok\n"
        "'''\n\n"
        '[tasks."lint:b"]\n'
        "run = '''\n"
        f"{GUARDED}\n"
        "org_done=1\n"
        "'''\n"
    )
    paths = plant(("mise.toml", config))
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [
        f"mise.toml:4: task lint:a: {NOT_GUARDED}",
        f"mise.toml:5: task lint:a: {NOT_MARKED}",
    ]
    assert lines == want


def test_one_line_body_is_its_run_line(
    capfd: pytest.CaptureFixture[str], plant: Callable[..., list[str]]
) -> None:
    """A `run = "…"` body starts on the `run =` line itself."""
    paths = plant(("mise.toml", "[tasks.a]\nrun = \"trap 'rm -f x' EXIT\"\n"))
    status = et.main(paths)
    lines, shaped = said(capfd.readouterr().err, paths)
    assert (status, shaped) == (1, True)
    want: list[str] = [
        f"mise.toml:2: task a: {NOT_GUARDED}",
        f"mise.toml:2: task a: {NOT_MARKED}",
    ]
    assert lines == want


def run_bash(*body: str, shell: str = str(BASH), shebang: str = "") -> int:
    """Run a script under a shell, by default `/bin/bash` as the belt does.

    Returns:
        Its exit status.

    """
    head = {"shebang": shebang} if shebang else {}
    with TemporaryDirectory() as scratch:
        path = Path(scratch) / "s.sh"
        path.write_text(script("tmp=$(mktemp)", *body, **head), encoding="utf-8")
        done = subprocess.run(
            [shell, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode


# The defect, then the guard, on the shell that has the defect.
@fixture
def _set_up_test_pattern() -> None:
    """Skip every row unless /bin/bash is the shell with the defect."""
    if not IS_32:
        pytest.skip("/bin/bash is not 3.2; the defect is 3.2's (macOS)")


@pytest.mark.usefixtures("_set_up_test_pattern")
def test_bare_trap_reproduces_the_defect() -> None:
    """The issue's four rows, as measured on 3.2.57."""
    rows = [
        ((BARE, 'echo "${nope}"'), 0),
        ((BARE, ': "${nope:?unset}"'), 0),
        ((BARE, "false"), 1),
        (('echo "${nope}"',), 1),
    ]
    for body, code in rows:
        assert run_bash(*body) == code


@pytest.mark.usefixtures("_set_up_test_pattern")
def test_guard_fails_the_unfinished_run() -> None:
    """Both unset-variable deaths exit 1 under either form."""
    for guard in (GUARDED, BASH_GUARDED):
        for death in ('echo "${nope}"', ': "${nope:?unset}"'):
            assert run_bash(guard, death, "org_done=1") == 1


@pytest.mark.usefixtures("_set_up_test_pattern")
@pytest.mark.parametrize("guard", [GUARDED, BASH_GUARDED])
def test_guard_passes_the_finished_run(guard: object) -> None:
    """A run that reaches the mark exits 0."""
    assert run_bash(guard, "echo ok", "org_done=1") == 0


@pytest.mark.usefixtures("_set_up_test_pattern")
@pytest.mark.parametrize("guard", [GUARDED, BASH_GUARDED])
def test_guard_keeps_an_explicit_code(guard: object) -> None:
    """A non-zero exit, explicit or from errexit, keeps its code."""
    assert run_bash(guard, "exit 3", "org_done=1") == 3
    assert run_bash(guard, "false", "org_done=1") == 1


@pytest.mark.usefixtures("_set_up_test_pattern")
@pytest.mark.parametrize("guard", [GUARDED, BASH_GUARDED])
def test_early_exit_zero_needs_the_mark(guard: object) -> None:
    """`exit 0` reads as a death unless the mark is set first."""
    assert run_bash(guard, "exit 0", "org_done=1") == 1
    assert run_bash(guard, "org_done=1", "exit 0", "org_done=1") == 0


@pytest.mark.usefixtures("_set_up_test_pattern")
def test_cleanup_still_runs() -> None:
    """The guard wraps the cleanup; it does not replace it."""
    with TemporaryDirectory() as scratch:
        left = Path(scratch) / "left"
        status = run_bash(
            f'tmp="{left}"',
            'touch "${tmp}"',
            GUARDED,
            'echo "${nope}"',
            "org_done=1",
        )
        assert status == 1
        assert not left.exists()


# The printed guard in a `#!/bin/sh` script: each body, and the exit code
# it gives under bash 3.2 (macOS's `/bin/sh`) and under dash (Ubuntu's).
# dash has no defect to correct, so its unset-variable deaths keep its
# own 2. Measured 2026-11-15 (#1251).
POSIX_ROWS = [
    ('echo "${nope}"', 1, 2),
    (': "${nope:?unset}"', 1, 2),
    ("exit 3", 3, 3),
    ("false", 1, 1),
    ("echo ok", 0, 0),
    ("exit 0", 1, 1),
]


def run_sh(shell: str, body: str) -> int:
    """Run the printed guard around one body in a `#!/bin/sh` script.

    Returns:
        Its exit status.

    """
    return run_bash(
        et.PREASSIGN,
        GUARDED,
        body,
        "org_done=1",
        shell=shell,
        shebang="#!/bin/sh",
    )


# The printed guard under the shells a `#!/bin/sh` script meets.
@pytest.mark.parametrize(("body", "as_32", "as_dash"), POSIX_ROWS)
def test_under_bin_sh(body: object, as_32: object, as_dash: object) -> None:
    """`/bin/sh` gives the row for the shell it is."""
    if not (SH_IS_32 or SH_IS_DASH):
        pytest.skip("/bin/sh is neither bash 3.2 nor dash")
    want = as_32 if SH_IS_32 else as_dash
    assert run_sh(str(SH), body) == want


@pytest.mark.parametrize(("body", "_", "as_dash"), POSIX_ROWS)
def test_under_dash(body: object, _: object, as_dash: object) -> None:
    """Under dash the guard runs: a clean run exits 0, no `[[: not found`."""
    if not DASH:
        pytest.skip("dash is not installed")
    assert run_sh(support.tool("dash"), body) == as_dash


# The printed shape is the one the rule accepts.
def test_guard_matches_its_own_rule() -> None:
    """Each form matches its own pattern and not the other's."""
    assert re.search(et.POSIX_RE, GUARDED)
    assert re.search(et.BASH_RE, BASH_GUARDED)
    assert re.match(et.BASH_RE, GUARDED) is None
    assert re.match(et.POSIX_RE, BASH_GUARDED) is None
    for pattern in (et.POSIX_RE, et.BASH_RE):
        assert re.match(pattern, BARE) is None


def test_printed_shape_is_posix_and_waiver_free() -> None:
    """The remedy is the POSIX form after `rc=0`, with no directive."""
    assert "[[" not in et.SHAPE
    assert "shellcheck" not in et.SHAPE
    assert f"    {et.PREASSIGN}\n    {SHAPE_LINE}\n" in et.SHAPE

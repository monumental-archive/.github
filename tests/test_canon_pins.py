"""Table tests for lint:canon-pins and mise/canon_pins.py (#1911).

Each row plants files on the four surfaces and asserts the exit status
and every line printed: versioned and unversioned workflow pins (no
comment, a short SHA, a branch, a pre-release), comment lines and
other owners (not counted), a lefthook remote on a tag or a branch, a
Renovate preset with and without its tag, canon checkouts (counted,
each named), and the skip when nothing names the canon. Three rows run
the task's own body, read from mise/config.toml: the skip, every
surface end to end, and untracked files (never read); the rest call
the helper's `main` in this process: the order of the offenders, a
file that is not UTF-8 (read as bytes), a missing file, and the usage
error.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import canon_pins, decoded
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:canon-pins"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON_DIR = BELT.parent
GIT = support.tool("git")
CANON = "monumental-archive/.github"
SHA = "0123456789abcdef0123456789abcdef01234567"
PIN = f"      - uses: {CANON}/.github/actions/canon@{SHA} # v1.2.3\n"
SKIPPED = ["lint:canon-pins: no canon references, skipped"]
TAIL = [
    "  every canon reference pins a tag Renovate can bump (#133);",
    "  workflow pins: '@<sha> # vX.Y.Z', lefthook: 'ref: vX.Y.Z',",
    f"  preset: 'github>{CANON}#vX.Y.Z'; canon",
    "  content in a job comes only via $/.github/actions/canon (#165).",
]
UNPINNED = "(canon pin without '@<full-sha> # vX.Y.Z')"
BRANCH = "(lefthook canon remote must ref a v* tag, not a branch)"
UNTAGGED = "(preset reference must carry #vX.Y.Z)"


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read lint:canon-pins' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def ok(found: int) -> list[str]:
    """Build the clean line for some references.

    Returns:
        Its lines.

    """
    return [f"lint:canon-pins: {found} canon references, all versioned"]


def red(*offences: str) -> list[str]:
    """Build the red message naming some offences.

    Returns:
        Its lines.

    """
    listed = "".join(f" {o}" for o in offences)
    return [f"{TASK}: unversioned canon references:{listed}", *TAIL]


def steps(*lines: str) -> str:
    """Write a workflow with one job holding some step lines.

    Returns:
        Its text.

    """
    return "jobs:\n  a:\n    steps:\n" + "".join(lines)


def hook(ref: str) -> str:
    """Write a lefthook config with the canon as a remote at a ref.

    Returns:
        Its text.

    """
    return f"remotes:\n  - git_url: https://github.com/{CANON}\n    ref: {ref}\n"


def checkout(tail: str = "") -> str:
    """Write a checkout step of the canon.

    Returns:
        Its lines.

    """
    return (
        f"      - uses: actions/checkout@{SHA} # v5\n"
        f"        with:\n          repository: {CANON}{tail}\n"
    )


@pytest.mark.usefixtures("_set_up_test_planted")
class TestPlanted:
    """A planted git tree."""

    @pytest.fixture
    def _set_up_test_planted(self, tmp_path: Path) -> None:
        """Plant an empty tree."""
        self.tree = tmp_path
        self.env = support.env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON_DIR),
            ORG_PYTHON=sys.executable,
        )
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)

    def plant(self, rel: str, text: str | bytes, *, track: bool = True) -> str:
        """Write one file, tracked unless asked otherwise.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        if track:
            subprocess.run(
                [GIT, "add", "--", rel], cwd=self.tree, env=self.env, check=True
            )
        return rel

    def helper(
        self, *argv: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper's main in the planted tree.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        with pytest.MonkeyPatch.context() as patched:
            for name, value in self.env.items():
                patched.setenv(name, value)
            patched.chdir(self.tree)
            status = canon_pins.main(args)
        out, err = capfd.readouterr()
        return status, out, err

    def surfaces(
        self,
        yml: tuple[str, ...] = (),
        hooks: tuple[str, ...] = (),
        presets: tuple[str, ...] = (),
        flows: tuple[str, ...] = (),
        *,
        capfd: pytest.CaptureFixture[str],
    ) -> tuple[int, str, str]:
        """Call the helper with the four lists.

        Returns:
            The status and what it printed.

        """
        return self.helper(
            CANON, *yml, "--", *hooks, "--", *presets, "--", *flows, capfd=capfd
        )

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = support.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class TestTask(TestPlanted):
    """The task's own body: the canon's identity and the four lists."""

    def test_nothing_names_the_canon_is_skipped(self) -> None:
        """No reference anywhere: the task says it skipped."""
        self.plant(".github/workflows/ci.yml", steps("      - run: true\n"))
        done = self.task()
        same(done.returncode, 0, done.stderr)
        same(done.stdout.splitlines(), SKIPPED)

    def test_every_surface_through_the_task(self) -> None:
        """Each surface's offender, in surface order, one finding."""
        flow = self.plant(".github/workflows/ci.yml", steps(PIN, checkout()))
        bare = steps(PIN.replace(" # v1.2.3", ""))
        stub = self.plant("workflow-templates/a.yaml", bare)
        self.plant("vendor/x.yml", steps(PIN.replace(" # v1.2.3", "")))
        hooks = self.plant("lefthook.yml", hook("main"))
        preset = self.plant("renovate.json", f'{{"extends": ["github>{CANON}"]}}\n')
        done = self.task()
        same(done.returncode, canon_pins.EXIT_RED)
        expected = red(
            f"{stub}{UNPINNED}",
            f"{hooks}{BRANCH}",
            f"{preset}{UNTAGGED}",
            f"{flow}(1 canon checkout(s) — use $/.github/actions/canon)",
        )
        same(done.stderr.splitlines(), expected)

    def test_untracked_files_are_never_read(self) -> None:
        """A tracked versioned pin, and offenders git does not track: clean."""
        self.plant(".github/workflows/ci.yml", steps(PIN))
        self.plant("lefthook.yml", hook("main"), track=False)
        self.plant(".github/workflows/x.yml", steps(checkout()), track=False)
        done = self.task()
        same(done.returncode, 0, done.stderr)
        same(done.stdout.splitlines(), ok(1))


class TestPins(TestPlanted):
    """Workflow pins on any tracked YAML file."""

    def test_versioned_pins_are_counted_clean(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Each versioned pin line counts once."""
        tab = f"  - uses:\t{CANON}/x@{SHA} # v10.0.12\n"
        rel = self.plant("a.yml", steps(PIN, PIN, tab))
        status, out, err = self.surfaces(yml=(rel,), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), ok(3))

    def test_unversioned_pins_are_each_named(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No comment, a short SHA, a branch, a pre-release, a v-less tag."""
        lines = (
            f"  - uses: {CANON}/x@{SHA}\n",
            f"  - uses: {CANON}/x@{SHA[:12]} # v1.2.3\n",
            f"  - uses: {CANON}/x@main\n",
            f"  - uses: {CANON}/x@{SHA} # v1.2\n",
            f"  - uses: {CANON}/x@{SHA}  # v1.2.3\n",
        )
        rel = self.plant("a.yml", steps(*lines))
        status, _out, err = self.surfaces(yml=(rel,), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        same(err.splitlines(), red(*[f"{rel}{UNPINNED}"] * len(lines)))

    def test_comments_and_other_owners_are_not_pins(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A commented line, a `#` before uses:, another owner: not counted."""
        lines = (
            f"# uses: {CANON}/x@main\n",
            f"      - run: true # uses: {CANON}/x@main\n",
            "      - uses: other/.github/x@main\n",
            f"      - uses: {CANON.replace('.', 'x')}/x@main\n",
        )
        rel = self.plant("a.yml", steps(*lines))
        status, out, err = self.surfaces(yml=(rel,), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), SKIPPED)


class TestRemotes(TestPlanted):
    """Lefthook remotes and Renovate presets."""

    def test_lefthook_on_a_tag_is_clean(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A remote at `ref: v…`: one reference, clean."""
        rel = self.plant("lefthook.yml", hook("v1.76.0"))
        status, out, err = self.surfaces(hooks=(rel,), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), ok(1))

    def test_lefthook_on_a_branch_is_red(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A remote at a branch, or with no ref at all: named."""
        one = self.plant("lefthook.yml", hook("main"))
        two = self.plant("sub/lefthook.yml", f"# github.com/{CANON}\n")
        status, _out, err = self.surfaces(hooks=(one, two), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        same(err.splitlines(), red(f"{one}{BRANCH}", f"{two}{BRANCH}"))

    def test_lefthook_without_the_canon_is_not_counted(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A lefthook config that names no canon remote: nothing."""
        rel = self.plant("lefthook.yml", "pre-commit:\n  jobs: []\n")
        status, out, err = self.surfaces(hooks=(rel,), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), SKIPPED)

    def test_presets(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Tagged: clean; untagged: named; no canon: not counted."""
        tagged = self.plant("renovate.json", f'["github>{CANON}#v1.2.3"]\n')
        bare = self.plant("a/renovate.json", f'["github>{CANON}:default"]\n')
        other = self.plant(".github/renovate.json", '["github>other/x"]\n')
        status, out, err = self.surfaces(presets=(tagged, other), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), ok(1))
        status, out, err = self.surfaces(presets=(tagged, bare), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        same(err.splitlines(), red(f"{bare}{UNTAGGED}"))


class TestCheckouts(TestPlanted):
    """Canon checkouts in workflows."""

    def test_checkouts_are_counted_and_named(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Two checkouts, one with trailing blanks: counted twice, named once."""
        rel = self.plant(".github/workflows/a.yml", steps(checkout(), checkout(" \t")))
        status, _out, err = self.surfaces(flows=(rel,), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        named = f"{rel}(2 canon checkout(s) — use $/.github/actions/canon)"
        same(err.splitlines(), red(named))

    def test_what_is_not_a_checkout(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Commented, followed by more text, or another repository: nothing."""
        lines = (
            f"          # repository: {CANON}\n",
            f"          repository: {CANON}-lab\n",
            f"          repository: {CANON} # no\n",
            "          repository: other/.github\n",
        )
        rel = self.plant(".github/workflows/a.yml", steps(*lines))
        status, out, err = self.surfaces(flows=(rel,), capfd=capfd)
        same(status, 0, err)
        same(out.splitlines(), SKIPPED)


class TestCalls(TestPlanted):
    """Reading the files, and the call itself."""

    def test_the_order_is_surface_then_file(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Offenders surface by surface, files in the order given."""
        bad = steps(f"  - uses: {CANON}/x@main\n")
        one = self.plant("b.yml", bad)
        two = self.plant("a.yml", bad)
        hooks = self.plant("lefthook.yml", hook("main"))
        status, _out, err = self.surfaces(yml=(one, two), hooks=(hooks,), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        expected = red(f"{one}{UNPINNED}", f"{two}{UNPINNED}", f"{hooks}{BRANCH}")
        same(err.splitlines(), expected)

    def test_not_utf8_is_judged_on_its_bytes(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A file that is not UTF-8 is read as grep reads it: its pin is named."""
        text = f"x: \xff\n  - uses: {CANON}/x@main\n"
        rel = self.plant("a.yml", text.encode("latin-1"))
        status, _out, err = self.surfaces(yml=(rel,), capfd=capfd)
        same(status, canon_pins.EXIT_RED)
        same(err.splitlines(), red(f"{rel}{UNPINNED}"))

    def test_a_missing_file_is_an_error(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """A named file that is not there is refused by name, exit 2."""
        status, _out, err = self.surfaces(presets=("renovate.json",), capfd=capfd)
        same(status, canon_pins.EXIT_ERROR)
        assert err.startswith(f"{TASK}: renovate.json: cannot be read:"), err

    def test_a_bad_call_is_a_usage_error(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No canon, half a canon, or not three separators: exit 2."""
        usage = f"{TASK}: {canon_pins.USAGE}"
        calls: list[list[str]] = [
            [],
            [CANON, "--", "--"],
            ["/repo", "--", "--", "--"],
            ["owner/", "--", "--", "--"],
            [CANON, "--", "--", "--", "--"],
        ]
        for argv in calls:
            status, _out, err = self.helper(*argv, capfd=capfd)
            same(status, canon_pins.EXIT_ERROR)
            same(err.splitlines(), [usage])

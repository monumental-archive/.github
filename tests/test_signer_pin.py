"""lint:signer-pin holds the signer identity to one statement (#317).

The signer was stated twice: in every `uses:` line that calls sign.yml,
and in security/signer.pin, which the verifier read. The two had no
Renovate manager in common, so a signer bump moved them on separate
branches, each red against the other until a human joined them, and
the gate stuck. #317 deleted the file and made the `uses:` lines the
single statement; the lint now refuses the second copy coming back,
and any workflow that still reads it.

Each row runs the task's own `run` body, read from mise/config.toml,
in a planted git tree, with the belt as ORG_BELT_DIR and this
repository as ORG_CANON_DIR, so the identity it reads is the real one.
The `Branches` rows call mise/signer_pin.py's `main` in this process,
one per branch, each message asserted whole (#1911).
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, signer_pin
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

BELT = support.MISE
CANON = BELT.parent
BASH = support.tool("bash")
GIT = support.tool("git")
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
TASK = "lint:signer-pin"
CANON_NAME = "monumental-archive/.github"


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


def body() -> str:
    """Read lint:signer-pin's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks["lint:signer-pin"], "lint:signer-pin")
    return decoded.as_str(task["run"], "lint:signer-pin's run")


def signer() -> str:
    """Read the signer workflow the identity declares.

    Returns:
        The `signer-workflow` field of security/identity.toml.

    """
    identity = decoded.load_toml((CANON / "security" / "identity.toml").read_bytes())
    return decoded.as_str(identity["signer-workflow"], "signer-workflow")


def workflow(extra: str = "") -> str:
    """Render a workflow whose one job calls the signer at SHA.

    Returns:
        The workflow text.

    """
    return f"on: push\njobs:\n  sign:\n    uses: {signer()}@{SHA} # main\n" + extra


def lint(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the task's body in a git tree tracking these files.

    Returns:
        The completed process.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "repo"
        tree.mkdir()
        env = support.env(
            root / "home",
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON),
            ORG_PYTHON=sys.executable,
        )
        for name, text in files.items():
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        subprocess.run([GIT, "add", "--", *files], cwd=tree, env=env, check=True)
        as_mise = support.task_run("lint:signer-pin", body())
        return subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class TestOneStatementOfTheSigner:
    """The `uses:` lines are the signer identity, and nothing else is (#317)."""

    def test_the_uses_lines_alone_pass(self) -> None:
        """The baseline: one digest, no pin file."""
        done = lint({".github/workflows/sign.yml": workflow()})
        assert done.returncode == 0, done.stderr
        assert f"lint:signer-pin: all signer references agree on {SHA}\n" == done.stdout

    def test_no_workflows_is_skipped_by_name(self) -> None:
        """No tracked workflow, an untracked one beside: the task skips."""
        done = lint({"README": "x\n"})
        assert done.returncode == 0, done.stderr
        assert done.stdout == "lint:signer-pin: no workflows, skipped\n"

    def test_a_pin_file_beside_them_is_refused(self) -> None:
        """security/signer.pin back, even agreeing, is the second copy."""
        done = lint({
            ".github/workflows/sign.yml": workflow(),
            "security/signer.pin": f"{SHA}\n",
        })
        assert done.returncode == 1
        assert (
            "lint:signer-pin: security/signer.pin exists — the second copy of the"
            " signer identity was deleted (#316 finding 2)\n"
        ) in done.stderr

    def test_a_workflow_reading_the_pin_is_refused(self) -> None:
        """A step still reading the deleted file is named."""
        step = "      - run: cat security/signer.pin\n"
        reader = workflow("  verify:\n    steps:\n" + step)
        done = lint({".github/workflows/sign.yml": reader})
        assert done.returncode == 1
        assert (
            "lint:signer-pin: workflows read security/signer.pin (on a non-comment"
            " line) but the file is deleted"
        ) in done.stderr


def literal_message(*found: str) -> list[str]:
    """Build the literal finding's lines.

    Returns:
        Its lines.

    """
    return [
        (
            "lint:signer-pin: hand-written --signer-digest literal — nothing"
            " bumps it, so it drifts from the uses: pin the certificate will"
            " carry (#314):"
        ),
        *(f"  {line}" for line in found),
        f"  derive it instead: uses: {CANON_NAME}/.github/actions/verify-signed",
        "  ($/.github/actions/verify-signed from a canon-owned workflow)",
    ]


@pytest.mark.usefixtures("_set_up_test_branches")
class TestBranches:
    """Each branch of the helper, its whole message asserted."""

    @pytest.fixture
    def _set_up_test_branches(self, tmp_path: Path) -> None:
        """Plant an empty repository: the pin is asked of git (#2013)."""
        self.tree = tmp_path
        self.env = support.env(MISE_TASK_NAME=TASK)
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run([GIT, *args], cwd=self.tree, env=self.env, check=True)

    def plant(self, name: str, text: str | bytes, *, track: bool = True) -> str:
        """Write one file into the tree, and track it unless told not to.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        if track:
            self.git("add", "--", name)
        return name

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
            status = signer_pin.main(args)
        out, err = capfd.readouterr()
        return status, out, err

    def check(
        self, *files: str, capfd: pytest.CaptureFixture[str]
    ) -> tuple[int, str, str]:
        """Call the helper on these workflows with the real signer.

        Returns:
            The status and what it printed.

        """
        return self.helper(CANON_NAME, signer(), *files, capfd=capfd)

    def test_agreeing_references_pass(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Every reference at one digest, across files: ok, the digest named."""
        one = self.plant("a.yml", workflow())
        two = self.plant("b.yaml", workflow(f"  x:\n    uses: {signer()}@{SHA}\n"))
        status, out, err = self.check(one, two, capfd=capfd)
        assert status == 0, err
        want = [f"lint:signer-pin: all signer references agree on {SHA}"]
        assert want == out.splitlines()
        assert err == ""

    def test_no_reference_is_skipped_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No signer reference, and its dots are literal: skipped."""
        near = signer().replace(".github", "xgithub", 1)
        rel = self.plant("a.yml", f"x: {near}@{SHA}\n")
        status, out, err = self.check(rel, capfd=capfd)
        assert status == 0, err
        want = ["lint:signer-pin: no signer references, skipped"]
        assert want == out.splitlines()

    def test_two_digests_are_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Two digests, even on one line: each named once, sorted."""
        line = f"x: {signer()}@{SHA} {signer()}@{OTHER} {signer()}@{SHA}\n"
        rel = self.plant("a.yml", line)
        status, out, err = self.check(rel, capfd=capfd)
        assert status == signer_pin.EXIT_RED
        want = [
            (
                "lint:signer-pin: sign.yml pinned at more than one digest — a tree"
                " states the trusted signer once:"
            ),
            f"  {signer()}@{SHA}",
            f"  {signer()}@{OTHER}",
            (
                "  a partial bump left the tree disagreeing with itself; move every"
                " uses: line together"
            ),
        ]
        assert want == err.splitlines()

    def test_a_literal_in_one_file_is_red_without_its_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """One file searched: grep -n's `line:text`, no file name."""
        rel = self.plant("a.yml", f"a\n  run: v --signer-digest  {SHA}")
        status, out, err = self.check(rel, capfd=capfd)
        assert status == signer_pin.EXIT_RED
        found = f"2:  run: v --signer-digest  {SHA}"
        assert literal_message(found) == err.splitlines()

    def test_literals_in_two_files_are_red_with_their_names(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Two files searched: `file:line:text`, ahead of the pin file."""
        one = self.plant("a.yml", workflow())
        two = self.plant("b.yml", f"x: --signer-digest\t{OTHER}\n")
        self.plant(signer_pin.PIN_FILE, f"{SHA}\n")
        status, out, err = self.check(one, two, capfd=capfd)
        assert status == signer_pin.EXIT_RED
        found = f"{two}:1:x: --signer-digest\t{OTHER}"
        assert literal_message(found) == err.splitlines()

    def test_the_pin_file_is_red(self, capfd: pytest.CaptureFixture[str]) -> None:
        """The pin file back, agreeing or not: red, the split explained."""
        rel = self.plant("a.yml", workflow())
        self.plant(signer_pin.PIN_FILE, f"{SHA}\n")
        status, out, err = self.check(rel, capfd=capfd)
        assert status == signer_pin.EXIT_RED
        want = [
            (
                "lint:signer-pin: security/signer.pin exists — the second copy of"
                " the signer identity was deleted (#316 finding 2)"
            ),
            (
                "  the uses: lines are the single statement of the trusted signer;"
                " verifiers derive from them"
            ),
            (
                "  (a standalone pin file has no Renovate manager the first-party"
                " group can match, so it"
            ),
            (
                "  bumps on a separate branch that can never pass this lint — the"
                " two-branch split)"
            ),
        ]
        assert want == err.splitlines()

    def test_an_untracked_pin_file_is_no_second_copy(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """On disk and untracked, the pin states nothing: green (#2013)."""
        rel = self.plant("a.yml", workflow())
        self.plant(signer_pin.PIN_FILE, f"{SHA}\n", track=False)
        status, out, err = self.check(rel, capfd=capfd)
        assert status == 0, err
        want = [f"lint:signer-pin: all signer references agree on {SHA}"]
        assert want == out.splitlines()

    def test_readers_are_red_and_named(self, capfd: pytest.CaptureFixture[str]) -> None:
        """Readers on non-comment lines named; a comment is not a reader."""
        one = self.plant("a.yml", workflow("      - run: cat security/signer.pin\n"))
        prose = self.plant("b.yml", workflow("# security/signer.pin is gone\n"))
        two = self.plant("c.yml", "x: security/signer.pin\n")
        status, out, err = self.check(one, prose, two, capfd=capfd)
        assert status == signer_pin.EXIT_RED
        want = [
            (
                "lint:signer-pin: workflows read security/signer.pin (on a"
                " non-comment line) but the file is deleted (#316 finding 2) —"
                f" derive from the sign.yml uses: pin instead:{one}\\n{two}"
            ),
        ]
        assert want == err.splitlines()

    def test_not_utf8_is_an_error(self, capfd: pytest.CaptureFixture[str]) -> None:
        """A workflow that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant("bad.yml", b"on: \xff\n")
        status, out, err = self.check(rel, capfd=capfd)
        assert status == signer_pin.EXIT_ERROR
        assert err.startswith(f"lint:signer-pin: {rel}: cannot be read as UTF-8:"), err

    def test_usage_errors(self, capfd: pytest.CaptureFixture[str]) -> None:
        """No workflow, an empty owner, repo or signer: the usage line."""
        usage = [
            (
                "lint:signer-pin: usage: belt signer-pin OWNER/REPO SIGNER-WORKFLOW"
                " WORKFLOW..."
            )
        ]
        rows = {
            "nothing": [],
            "no workflow": [CANON_NAME, signer()],
            "no owner": ["/x", signer(), "a.yml"],
            "no repo": ["x/", signer(), "a.yml"],
            "no signer": [CANON_NAME, "", "a.yml"],
        }
        for row, argv in rows.items():
            status, out, err = self.helper(*argv, capfd=capfd)
            assert status == signer_pin.EXIT_ERROR
            assert usage == err.splitlines()

"""Table tests for audit:template-pins and mise/template_drift.py (#1911).

Each row plants workflow templates in a git tree of dated, tagged
commits and asserts the exit status and every line of the report: no
pin, two pins, a pin the clone lacks, a pin with no drift since it,
drift with the pin untagged, drift inside and past the post-release
window (from the next tag in version order, or from the pin's own when
it is the newest), a negative age, a failed git call, a template that is
not UTF-8, and a call with no canon or no template. Two rows run the
task's own body, read from mise/config.toml: the skip when there are no
templates, and a clean pin end to end; the rest call the helper's
`main` in this process, in the planted tree, at a `now` of the row's.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom.
"""

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from belt import decoded, template_drift
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:template-pins"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
CANON_DIR = BELT.parent
GIT = support.tool("git")
CANON = "monumental-archive/.github"
REUSABLE = f"{CANON}/.github/workflows/ci.yml"
STUB = "workflow-templates/ci.yml"
# The planted history's clock: commit n is dated T0 + n days.
T0 = 1_700_000_000
DAY = 86400
TAIL = [
    "  a repo standing up from the templates would not get these —",
    "  bump the template pins to current canon (see #110, #111).",
]


def same[T](got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    assert got == want, msg or None


def body() -> str:
    """Read audit:template-pins' run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(task["run"], f"{TASK}.run")


def template(*shas: str) -> str:
    """Write a template pinning the canon at some SHAs, beside a foreign pin.

    Returns:
        The template's text.

    """
    lines = [f"      - uses: {REUSABLE}@{sha} # v1.0.0\n" for sha in shas]
    foreign = "      - uses: actions/checkout@" + "a" * 40 + " # v5\n"
    return "jobs:\n  a:\n    steps:\n" + foreign + "".join(lines)


def window(pin_tag: str, age_hours: int, since: str) -> list[str]:
    """Build the post-release window report.

    Returns:
        Its lines.

    """
    return [
        (
            f"audit:template-pins: post-release window — pin at {pin_tag},"
            f" stale for {age_hours}h (since {since})"
        ),
        "  the lag is structural (#227); Renovate's chore(canon) bump",
        "  closes it. Red only past the 24h grace (#290, #310).",
    ]


class Planted:
    """Planted templates in a tree of dated commits."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant a tree whose first commit, day 0, touches mise/."""
        self.make_env = env
        self.tree = tmp_path
        self.env = self.make_env(
            MISE_TASK_NAME=TASK,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(CANON_DIR),
            ORG_PYTHON=sys.executable,
        )
        self.day = 0
        self.git("init", "-q")
        self.pin = self.commit("mise/a.sh")
        (self.tree / "workflow-templates").mkdir()

    def git(self, *args: str, day: int = 0) -> str:
        """Run git in the planted tree, unsigned, its clock at a day.

        Returns:
            Its standard output.

        """
        unsigned = ("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false")
        stamp = f"@{T0 + day * DAY} +0000"
        return subprocess.run(
            [GIT, *unsigned, *args],
            cwd=self.tree,
            env=self.env | {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def commit(self, path: str, tag: str = "") -> str:
        """Commit a change to one file on the next day, tagged when asked.

        Returns:
            Its SHA.

        """
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as out:
            out.write(f"{self.day}\n")
        self.git("add", "--", path, day=self.day)
        self.git("commit", "-q", "-m", f"change {path} on day {self.day}", day=self.day)
        if tag:
            self.git("tag", tag, day=self.day)
        self.day += 1
        return self.git("rev-parse", "HEAD").strip()

    def plant(self, text: str | bytes) -> str:
        """Write the one template.

        Returns:
            Its path from the tree's top.

        """
        path = self.tree / STUB
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        self.git("add", "--", STUB)
        return STUB

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the planted tree as the task's main meets it."""
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)

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

    def log(self, since: str) -> list[str]:
        """List the commits since a SHA touching the workflows or the belt.

        Returns:
            Each as `git log --oneline` prints it, indented.

        """
        paths = template_drift.PATHS
        span = f"{since}..HEAD"
        out = self.git("log", "--no-decorate", "--oneline", span, "--", *paths)
        return [f"  {line}" for line in out.splitlines()]


@fixture
def planted(tmp_path: Path, env: support.Env) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, env=env)


def test_no_templates_is_skipped_by_name(planted: Planted) -> None:
    """No workflow-templates/: the task says it skipped."""
    shutil.rmtree(planted.tree / "workflow-templates")
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    skipped = ["audit:template-pins: no workflow-templates/, skipped"]
    same(done.stdout.splitlines(), skipped)


def test_clean_pin_through_the_task(planted: Planted) -> None:
    """The body hands the helper the canon and the templates; no drift is ok."""
    planted.commit("docs/a.md")
    planted.plant(template(planted.pin))
    done = planted.task()
    same(done.returncode, 0, done.stderr)
    ok = (
        f"audit:template-pins: pin {planted.pin[:8]} carries all shipped"
        " workflow and belt changes"
    )
    same(done.stdout.splitlines(), [ok])


def test_no_org_pin_is_skipped_by_name(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Templates that pin no canon SHA: skipped, by name."""
    rel = planted.plant(template())
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    out, err = capfd.readouterr()
    same(status, 0, err)
    skipped = ["audit:template-pins: no org pins in templates, skipped"]
    same(out.splitlines(), skipped)


def test_a_short_pin_is_no_pin(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin that is not 40 hex is not read as one."""
    rel = planted.plant(template(planted.pin[:12]))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    out, err = capfd.readouterr()
    same(status, 0, err)
    skipped = ["audit:template-pins: no org pins in templates, skipped"]
    same(out.splitlines(), skipped)


def test_two_shas_are_red(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two distinct pinned SHAs: red, naming the lint that missed it."""
    other = planted.commit("mise/b.sh")
    rel = planted.plant(template(planted.pin, other, planted.pin))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_RED)
    expected = [
        (
            "audit:template-pins: templates pin more than one SHA"
            " (lint:template-pins should have failed)"
        )
    ]
    same(err.splitlines(), expected)


def test_a_sha_not_in_the_clone_is_red(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin the clone lacks: red, naming a shallow clone."""
    absent = "b" * 40
    rel = planted.plant(template(absent))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_RED)
    expected = [
        (
            "audit:template-pins: pinned SHA bbbbbbbb not in local history"
            " (shallow clone?)"
        )
    ]
    same(err.splitlines(), expected)


def test_drift_with_an_untagged_pin_is_red(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commits since an untagged pin touching either path: red, listed."""
    planted.commit("docs/a.md")
    planted.commit(".github/workflows/ci.yml")
    planted.commit("mise/b.sh")
    rel = planted.plant(template(planted.pin))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_RED)
    head = f"audit:template-pins: pin {planted.pin[:8]} misses shipped canon changes:"
    same(err.splitlines(), [head, *planted.log(planted.pin), *TAIL])
    same(len(planted.log(planted.pin)), 2)


def test_inside_the_window_from_the_next_tag(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift within 24h of the next tag in VERSION order: reported, ok."""
    planted.git("tag", "v1.9.0", planted.pin)
    planted.commit("mise/b.sh", tag="v1.10.0")
    planted.commit("mise/c.sh", tag="v1.11.0")
    rel = planted.plant(template(planted.pin))
    now = T0 + DAY + 5 * 3600 + 59
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], now)
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), window("v1.9.0", 5, "v1.10.0"))


def test_past_the_window_is_red(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift a full day after the next tag: red, every commit listed."""
    planted.git("tag", "v1.0.0", planted.pin)
    planted.commit("mise/b.sh", tag="v1.1.0")
    rel = planted.plant(template(planted.pin))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0 + 2 * DAY)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_RED)
    head = f"audit:template-pins: pin {planted.pin[:8]} misses shipped canon changes:"
    same(err.splitlines(), [head, *planted.log(planted.pin), *TAIL])


def test_newest_tag_measures_from_its_own(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin on the newest tag, with drift after it: the pin's own tag dates it."""
    planted.git("tag", "v1.0.0", planted.pin)
    planted.commit("mise/b.sh")
    rel = planted.plant(template(planted.pin))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0 + 3 * 3600)
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), window("v1.0.0", 3, "v1.0.0"))


def test_a_negative_age_truncates_toward_zero(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tag dated after `now`: its hours truncate as bash's `/` does."""
    planted.git("tag", "v1.0.0", planted.pin)
    planted.commit("mise/b.sh", tag="v1.1.0")
    rel = planted.plant(template(planted.pin))
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0 + DAY - 5400)
    out, err = capfd.readouterr()
    same(status, 0, err)
    same(out.splitlines(), window("v1.0.0", -1, "v1.1.0"))


def test_a_failed_git_call_is_an_error(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD unborn: the diff fails, and that is no empty drift."""
    rel = planted.plant(template(planted.pin))
    planted.git("checkout", "-q", "--orphan", "unborn")
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_ERROR)
    assert err.startswith(
        f"audit:template-pins: git diff --name-only {planted.pin}..HEAD --"
        " .github/workflows/ mise/ failed (128):"
    ), err


def test_not_utf8_is_an_error(
    planted: Planted, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template that is not UTF-8 is refused by name, exit 2."""
    rel = planted.plant(b"jobs: \xff\n")
    planted.enter(monkeypatch)
    status = template_drift.main([CANON, rel], T0)
    _out, err = capfd.readouterr()
    same(status, template_drift.EXIT_ERROR)
    assert err.startswith(f"audit:template-pins: {rel}: cannot be read as UTF-8:"), err


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            "belt template-drift: error: the following arguments are required: OWNER/REPO, TEMPLATE",
            id="nothing",
        ),
        pytest.param(
            [CANON],
            "belt template-drift: error: the following arguments are required: TEMPLATE",
            id="[CANON]",
        ),
        pytest.param(
            ["/repo", STUB],
            "belt template-drift: error: argument OWNER/REPO: '/repo' is not OWNER/REPO",
            id='["/repo", STUB]',
        ),
        pytest.param(
            ["owner/", STUB],
            "belt template-drift: error: argument OWNER/REPO: 'owner/' is not OWNER/REPO",
            id='["owner/", STUB]',
        ),
    ],
)
def test_a_bad_call_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """No canon, half a canon, or no template: exit 2 with the usage line: argparse's exit 2, its usage and its error."""
    with pytest.raises(SystemExit) as ended:
        template_drift.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt template-drift "), err
    assert err.splitlines()[-1] == error


@pytest.mark.usefixtures("planted")
def test_version_order() -> None:
    """Digit runs order as numbers: v1.10.0 after v1.9.0."""
    tags = ["v1.10.0", "v1.9.0", "v2.0.0", "v1.9.1"]
    ordered = sorted(tags, key=template_drift.version_key)
    same(ordered, ["v1.9.0", "v1.9.1", "v1.10.0", "v2.0.0"])

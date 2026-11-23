#!/usr/bin/env python3
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

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import template_drift

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:template-pins"


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


BELT = Path(__file__).resolve().parent
CANON_DIR = BELT.parent
GIT = hermetic.tool("git")
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


def same[T](case: unittest.TestCase, got: T, want: T, msg: str = "") -> None:
    """Assert two values equal, `want` typed by `got` (mypy.ini: no Any)."""
    case.assertEqual(got, want, msg or None)


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


class Planted(unittest.TestCase):
    """Planted templates in a tree of dated commits."""

    @override
    def setUp(self) -> None:
        """Plant a tree whose first commit, day 0, touches mise/."""
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree)
        self.env = hermetic.env(
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
        return STUB

    def helper(self, *argv: str, now: int = T0) -> hermetic.Called:
        """Call the helper's main in the planted tree at a given time.

        Returns:
            The status and what it printed.

        """
        args = list(argv)
        return hermetic.call_main(
            lambda: template_drift.main(args, now), env=self.env, cwd=self.tree
        )

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, body())
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


class Pins(Planted):
    """What the templates pin, and the task's own body."""

    def test_no_templates_is_skipped_by_name(self) -> None:
        """No workflow-templates/: the task says it skipped."""
        shutil.rmtree(self.tree / "workflow-templates")
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        skipped = ["audit:template-pins: no workflow-templates/, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_clean_pin_through_the_task(self) -> None:
        """The body hands the helper the canon and the templates; no drift is ok."""
        self.commit("docs/a.md")
        self.plant(template(self.pin))
        done = self.task()
        same(self, done.returncode, 0, done.stderr)
        ok = (
            f"audit:template-pins: pin {self.pin[:8]} carries all shipped"
            " workflow and belt changes"
        )
        same(self, done.stdout.splitlines(), [ok])

    def test_no_org_pin_is_skipped_by_name(self) -> None:
        """Templates that pin no canon SHA: skipped, by name."""
        rel = self.plant(template())
        done = self.helper(CANON, rel)
        same(self, done.returncode, 0, done.stderr)
        skipped = ["audit:template-pins: no org pins in templates, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_a_short_pin_is_no_pin(self) -> None:
        """A pin that is not 40 hex is not read as one."""
        rel = self.plant(template(self.pin[:12]))
        done = self.helper(CANON, rel)
        same(self, done.returncode, 0, done.stderr)
        skipped = ["audit:template-pins: no org pins in templates, skipped"]
        same(self, done.stdout.splitlines(), skipped)

    def test_two_shas_are_red(self) -> None:
        """Two distinct pinned SHAs: red, naming the lint that missed it."""
        other = self.commit("mise/b.sh")
        rel = self.plant(template(self.pin, other, self.pin))
        done = self.helper(CANON, rel)
        same(self, done.returncode, template_drift.EXIT_RED)
        expected = [
            (
                "audit:template-pins: templates pin more than one SHA"
                " (lint:template-pins should have failed)"
            )
        ]
        same(self, done.stderr.splitlines(), expected)

    def test_a_sha_not_in_the_clone_is_red(self) -> None:
        """A pin the clone lacks: red, naming a shallow clone."""
        absent = "b" * 40
        rel = self.plant(template(absent))
        done = self.helper(CANON, rel)
        same(self, done.returncode, template_drift.EXIT_RED)
        expected = [
            (
                "audit:template-pins: pinned SHA bbbbbbbb not in local history"
                " (shallow clone?)"
            )
        ]
        same(self, done.stderr.splitlines(), expected)


class Drift(Planted):
    """Commits since the pin, and the post-release window."""

    def test_drift_with_an_untagged_pin_is_red(self) -> None:
        """Commits since an untagged pin touching either path: red, listed."""
        self.commit("docs/a.md")
        self.commit(".github/workflows/ci.yml")
        self.commit("mise/b.sh")
        rel = self.plant(template(self.pin))
        done = self.helper(CANON, rel)
        same(self, done.returncode, template_drift.EXIT_RED)
        head = f"audit:template-pins: pin {self.pin[:8]} misses shipped canon changes:"
        same(self, done.stderr.splitlines(), [head, *self.log(self.pin), *TAIL])
        same(self, len(self.log(self.pin)), 2)

    def test_inside_the_window_from_the_next_tag(self) -> None:
        """Drift within 24h of the next tag in VERSION order: reported, ok."""
        self.git("tag", "v1.9.0", self.pin)
        self.commit("mise/b.sh", tag="v1.10.0")
        self.commit("mise/c.sh", tag="v1.11.0")
        rel = self.plant(template(self.pin))
        now = T0 + DAY + 5 * 3600 + 59
        done = self.helper(CANON, rel, now=now)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), window("v1.9.0", 5, "v1.10.0"))

    def test_past_the_window_is_red(self) -> None:
        """Drift a full day after the next tag: red, every commit listed."""
        self.git("tag", "v1.0.0", self.pin)
        self.commit("mise/b.sh", tag="v1.1.0")
        rel = self.plant(template(self.pin))
        done = self.helper(CANON, rel, now=T0 + 2 * DAY)
        same(self, done.returncode, template_drift.EXIT_RED)
        head = f"audit:template-pins: pin {self.pin[:8]} misses shipped canon changes:"
        same(self, done.stderr.splitlines(), [head, *self.log(self.pin), *TAIL])

    def test_newest_tag_measures_from_its_own(self) -> None:
        """A pin on the newest tag, with drift after it: the pin's own tag dates it."""
        self.git("tag", "v1.0.0", self.pin)
        self.commit("mise/b.sh")
        rel = self.plant(template(self.pin))
        done = self.helper(CANON, rel, now=T0 + 3 * 3600)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), window("v1.0.0", 3, "v1.0.0"))

    def test_a_negative_age_truncates_toward_zero(self) -> None:
        """A tag dated after `now`: its hours truncate as bash's `/` does."""
        self.git("tag", "v1.0.0", self.pin)
        self.commit("mise/b.sh", tag="v1.1.0")
        rel = self.plant(template(self.pin))
        done = self.helper(CANON, rel, now=T0 + DAY - 5400)
        same(self, done.returncode, 0, done.stderr)
        same(self, done.stdout.splitlines(), window("v1.0.0", -1, "v1.1.0"))

    def test_a_failed_git_call_is_an_error(self) -> None:
        """HEAD unborn: the diff fails, and that is no empty drift."""
        rel = self.plant(template(self.pin))
        self.git("checkout", "-q", "--orphan", "unborn")
        done = self.helper(CANON, rel)
        same(self, done.returncode, template_drift.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"audit:template-pins: git diff --name-only {self.pin}..HEAD --"
                " .github/workflows/ mise/ failed (128):"
            ),
            done.stderr,
        )

    def test_not_utf8_is_an_error(self) -> None:
        """A template that is not UTF-8 is refused by name, exit 2."""
        rel = self.plant(b"jobs: \xff\n")
        done = self.helper(CANON, rel)
        same(self, done.returncode, template_drift.EXIT_ERROR)
        self.assertTrue(
            done.stderr.startswith(
                f"audit:template-pins: {rel}: cannot be read as UTF-8:"
            ),
            done.stderr,
        )

    def test_a_bad_call_is_a_usage_error(self) -> None:
        """No canon, half a canon, or no template: exit 2 with the usage line."""
        usage = "audit:template-pins: usage: template_drift.py OWNER/REPO TEMPLATE..."
        calls: list[list[str]] = [[], [CANON], ["/repo", STUB], ["owner/", STUB]]
        for argv in calls:
            with self.subTest(argv=argv):
                done = self.helper(*argv)
                same(self, done.returncode, template_drift.EXIT_ERROR)
                same(self, done.stderr.splitlines(), [usage])

    def test_version_order(self) -> None:
        """Digit runs order as numbers: v1.10.0 after v1.9.0."""
        tags = ["v1.10.0", "v1.9.0", "v2.0.0", "v1.9.1"]
        ordered = sorted(tags, key=template_drift.version_key)
        same(self, ordered, ["v1.9.0", "v1.9.1", "v1.10.0", "v2.0.0"])


if __name__ == "__main__":
    unittest.main()

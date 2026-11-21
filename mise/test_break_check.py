#!/usr/bin/env python3
"""Table tests for break:check, mise/break-check.sh (#1906).

Each row plants a real git repository: a base commit, the pull request's
commit on a branch, and the merge commit the gate checks out, whose
first parent is the base. Each commit tracks a `metadata.json`, and the
stub `cargo` answers `metadata` with the one in its working directory —
so the base's answer is whatever `git archive HEAD^1` really wrote, and
the head's is the checkout's. `cargo-semver-checks` is a stub that logs
its arguments and `CARGO_TARGET_DIR`, and exits with the status the row
plants for the crate. Each red row asserts its message.

The declaration's reading (mise/declared_break.py) is held to the
reader: a class runs the belt's pinned stele over scratch commits and
asserts it files a commit under "Breaking" exactly when the reading says
the pull request declares a break.

stdlib `unittest`, the belt's one test idiom.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import declared_break
import hermetic
import workflow_yaml

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "break-check.sh"
CI = BELT.parent / ".github" / "workflows" / "ci.yml"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
STELE = hermetic.optional_tool("stele")
LABEL = "break:check"
DECLARED = f"{LABEL}: declared breaking, nothing to hold\n"
REMEDY = (
    f"{LABEL}: red — the change breaks what the base offered and the title"
    " does not say so: declare it in the TITLE (type(scope)!: …) or with a"
    " 'BREAKING CHANGE: ' footer in the BODY, and the release notes then list it"
)
UNMEASURED = (
    f"{LABEL}: red — a crate above could not be measured, and an unmeasured"
    " crate is never a pass"
)
ROOT = "/planted"
# Two lines of a pgrx schema snapshot, one statement per line.
FN_A = "CREATE FUNCTION a();\n"
FN_B = "CREATE FUNCTION b();\n"

CARGO_STUB = """#!/usr/bin/env bash
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$*" in
  "metadata --no-deps --format-version 1 --locked") cat metadata.json ;;
  *) exit 99 ;;
esac
"""
SEMVER_STUB = """#!/usr/bin/env bash
printf 'cargo-semver-checks %s\\n' "$*" >> "${PLANT_LOG}"
printf '%s\\n' "${CARGO_TARGET_DIR:-unset}" >> "${PLANT_LOG}.target"
status=0
[[ ! -f ${PLANT_EXITS}/$3 ]] || status=$(cat "${PLANT_EXITS}/$3")
exit "${status}"
"""


def package(
    name: str,
    *,
    publish: list[str] | None = None,
    majors: tuple[int, ...] = (),
    kinds: tuple[list[str], ...] = (["lib"],),
) -> dict[str, object]:
    """Plant one workspace member as `cargo metadata` reports it.

    A crate with `majors` is a pgrx extension: it depends on `pgrx` and
    carries a `pgNN` feature for each.

    Returns:
        The package record.

    """
    features: dict[str, list[str]] = {"default": []}
    deps: list[dict[str, str]] = []
    if majors:
        features |= {"pg_test": [], **{f"pg{m}": [f"pgrx/pg{m}"] for m in majors}}
        deps = [{"name": "pgrx"}]
    return {
        "id": f"path+file://{ROOT}/{name}#{name}@1.0.0",
        "name": name,
        "version": "1.0.0",
        "publish": publish,
        "dependencies": deps,
        "features": features,
        "targets": [{"kind": kind, "crate_types": kind} for kind in kinds],
        "manifest_path": f"{ROOT}/{name}/Cargo.toml",
    }


def metadata(*packages: dict[str, object]) -> str:
    """Render a workspace's `cargo metadata` document.

    Returns:
        The JSON text.

    """
    members: list[object] = [p["id"] for p in packages]
    document: dict[str, object] = {
        "packages": list(packages),
        "workspace_members": members,
        "workspace_root": ROOT,
    }
    return json.dumps(document)


def measured(crate: str) -> str:
    """Spell one cargo-semver-checks call the way the stub logs it.

    Returns:
        The log line.

    """
    return (
        f"cargo-semver-checks check-release -p {crate}"
        " --baseline-rev HEAD^1 --release-type minor"
    )


class Planted(unittest.TestCase):
    """A planted repository, stub PATH and belt; the rows are below."""

    @override
    def setUp(self) -> None:
        """Plant an empty repository, the stubs and a belt."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, text in {
            "cargo": CARGO_STUB,
            "cargo-semver-checks": SEMVER_STUB,
        }.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.exits = self.tmp / "exits"
        self.exits.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = hermetic.env(self.home) | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(hermetic.belt(self.tmp / "belt")),
            "PLANT_LOG": str(self.log),
            "PLANT_EXITS": str(self.exits),
        }
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(
            [GIT, "-c", "commit.gpgsign=false", *args],
            cwd=self.tree,
            env=hermetic.env(self.home),
            check=True,
            capture_output=True,
        )

    def commit(self, files: dict[str, str | None], subject: str) -> None:
        """Write (or, for None, remove) each file, and commit the tree."""
        for name, text in files.items():
            path = self.tree / name
            if text is None:
                self.git("rm", "-q", name)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.git("add", name)
        self.git("commit", "-q", "--allow-empty", "-m", subject)

    def merge(self, base: dict[str, str | None], head: dict[str, str | None]) -> None:
        """Plant the base, the pull request on a branch, and their merge."""
        self.commit(base, "chore: base")
        self.git("switch", "-q", "-c", "change")
        self.commit(head, "feat: the change")
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge the change", "change")

    def crates(
        self,
        base: list[dict[str, object]],
        head: list[dict[str, object]],
        *,
        base_files: dict[str, str | None] | None = None,
        head_files: dict[str, str | None] | None = None,
    ) -> None:
        """Plant a merge whose base and head are these workspaces."""
        base_tree: dict[str, str | None] = {
            "Cargo.toml": "[workspace]\n",
            "metadata.json": metadata(*base),
        }
        head_tree: dict[str, str | None] = {"metadata.json": metadata(*head)}
        self.merge(base_tree | (base_files or {}), head_tree | (head_files or {}))

    def red(self, crate: str, status: int) -> None:
        """Plant the stub's exit status for one crate."""
        (self.exits / crate).write_text(str(status), encoding="utf-8")

    def run_check(
        self, title: str | None = "feat: x", body: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the task's body in the planted tree, as the step does.

        Returns:
            The finished process and the stubs' call log.

        """
        env = self.env | {"PR_BODY": body}
        if title is not None:
            env["PR_TITLE"] = title
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def assert_lines(self, actual: list[str], expected: list[str]) -> None:
        """Assert two lists of lines are equal, typed for the checker."""
        self.assertEqual(actual, expected)

    @staticmethod
    def measurements(log: list[str]) -> list[str]:
        """Keep only the cargo-semver-checks calls from a call log.

        Returns:
            The calls, in order.

        """
        return [line for line in log if line.startswith("cargo-semver-checks")]


class TheDeclaration(Planted):
    """A break the title or body declares is not measured."""

    def assert_declared(self, title: str, body: str = "") -> None:
        """Assert the pull request is declared breaking and nothing ran."""
        self.crates([package("core")], [package("core")])
        self.red("core", 100)
        done, log = self.run_check(title, body)
        self.assertEqual((done.returncode, done.stdout), (0, DECLARED), done.stderr)
        self.assert_lines(log, [])

    def test_an_undeclared_break_is_red(self) -> None:
        """The measured break under `feat: x`, naming the crate and the remedy."""
        self.crates([package("core")], [package("core")])
        self.red("core", 100)
        done, log = self.run_check("feat: x")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [
                f"{LABEL}: core: cargo-semver-checks measured a break (above)",
                REMEDY,
            ],
        )
        self.assert_lines(self.measurements(log), [measured("core")])

    def test_bang_in_the_title(self) -> None:
        """`feat!:` declares the break."""
        self.assert_declared("feat!: x")

    def test_bang_after_a_scope(self) -> None:
        """`feat(scope)!:` declares the break."""
        self.assert_declared("feat(scope)!: x")

    def test_a_footer_in_the_body(self) -> None:
        """A `BREAKING CHANGE: ` footer in the body declares the break."""
        self.assert_declared("feat: x", "Why.\r\n\r\nBREAKING CHANGE: x is gone.\r\n")

    def test_unset_title_is_red_by_name(self) -> None:
        """No PR_TITLE: nothing to hold the change to."""
        self.crates([package("core")], [package("core")])
        done, log = self.run_check(None)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: PR_TITLE is unset — the gate step sets it from the pull"
            " request, and without it there is no declaration to hold\n",
        )
        self.assert_lines(log, [])


class TheRustApi(Planted):
    """An ordinary library crate in both trees is measured by the tool."""

    def test_no_break_is_green(self) -> None:
        """Exit 0: the crate is named, and the run is green."""
        self.crates([package("core")], [package("core")])
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout,
            f"{LABEL}: measuring core against HEAD^1\n{LABEL}: core: no break\n",
        )
        self.assert_lines(self.measurements(log), [measured("core")])

    def test_any_other_exit_could_not_be_measured(self) -> None:
        """Exit 1 is not a pass and not a break: red, by name."""
        self.crates([package("core")], [package("core")])
        self.red("core", 1)
        done, _ = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [
                f"{LABEL}: core: could not be measured (cargo-semver-checks exited 1)",
                UNMEASURED,
            ],
        )

    def test_the_call_and_its_target_directory(self) -> None:
        """Against HEAD^1 at minor, building outside the checkout."""
        self.crates([package("core")], [package("core")])
        self.run_check()
        targets = Path(f"{self.log}.target").read_text(encoding="utf-8").split()
        self.assertEqual(len(targets), 1)
        target = Path(targets[0]).resolve()
        self.assertTrue(target.is_absolute())
        self.assertFalse(target.is_relative_to(self.tree.resolve()), target)

    def test_every_crate_is_measured_before_the_verdict(self) -> None:
        """A red crate does not stop the next one."""
        self.crates([package("a"), package("b")], [package("a"), package("b")])
        self.red("a", 100)
        done, log = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(self.measurements(log), [measured("a"), measured("b")])
        self.assertIn(f"{LABEL}: b: no break", done.stdout)


class ThePopulation(Planted):
    """The crates judged, in the base and in the head."""

    def test_a_removed_crate_is_a_break(self) -> None:
        """Judged in the base and not in the head."""
        self.crates([package("core"), package("gone")], [package("core")])
        done, _ = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: gone: judged in the base and not in the head —"
                    " crate removed or renamed"
                ),
                REMEDY,
            ],
        )

    def test_a_new_crate_is_named(self) -> None:
        """Judged in the head and not in the base: nothing to break."""
        self.crates([package("core")], [package("core"), package("fresh")])
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"{LABEL}: fresh: new, nothing to break", done.stdout)
        self.assert_lines(self.measurements(log), [measured("core")])

    def test_unjudged_crates_are_not_measured(self) -> None:
        """`publish = false` and no library target: named, never measured."""
        both = [
            package("core"),
            package("private", publish=[]),
            package("cli", kinds=(["bin"],)),
        ]
        self.crates(both, both)
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_lines(
            done.stdout.splitlines(),
            [
                f"{LABEL}: measuring core against HEAD^1",
                f"{LABEL}: core: no break",
                f"{LABEL}: private: publish = false, not judged",
                f"{LABEL}: cli: no library target, not judged",
            ],
        )
        self.assert_lines(self.measurements(log), [measured("core")])

    def test_a_base_without_rust(self) -> None:
        """The pull request adds the workspace: every crate is new."""
        self.merge(
            {"README.md": "x\n"},
            {
                "Cargo.toml": "[workspace]\n",
                "metadata.json": metadata(package("core")),
            },
        )
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"{LABEL}: core: new, nothing to break\n")
        self.assert_lines(self.measurements(log), [])


class ThePgrxSurface(Planted):
    """A pgrx crate is held by its features and its SQL snapshot."""

    def test_a_removed_major_is_a_break(self) -> None:
        """pg14 and pg15 in the base, pg15 in the head; no Rust build."""
        self.crates(
            [package("core"), package("ext", majors=(14, 15))],
            [package("core"), package("ext", majors=(15,))],
        )
        done, log = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [f"{LABEL}: ext: feature pg14 removed", REMEDY],
        )
        self.assert_lines(self.measurements(log), [measured("core")])

    def test_kept_features_are_green(self) -> None:
        """An added major is not a break."""
        self.crates([package("ext", majors=(15,))], [package("ext", majors=(15, 16))])
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"{LABEL}: ext: every feature of the base kept\n")
        self.assert_lines(self.measurements(log), [])

    def test_a_snapshot_that_loses_a_line(self) -> None:
        """The lost line is named."""
        self.crates(
            [],
            [],
            base_files={"ext/schema.snapshot.sql": f"{FN_A}{FN_B}"},
            head_files={"ext/schema.snapshot.sql": "CREATE FUNCTION a();\n"},
        )
        done, _ = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [f"{LABEL}: ext/schema.snapshot.sql: lost CREATE FUNCTION b();", REMEDY],
        )

    def test_a_snapshot_that_gains_a_line(self) -> None:
        """An added line is not a break."""
        self.crates(
            [],
            [],
            base_files={"schema.snapshot.sql": "CREATE FUNCTION a();\n"},
            head_files={"schema.snapshot.sql": f"{FN_A}{FN_B}"},
        )
        done, _ = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout, f"{LABEL}: schema.snapshot.sql: every line of the base kept\n"
        )

    def test_a_snapshot_no_longer_tracked(self) -> None:
        """The file is gone from the head."""
        self.crates(
            [],
            [],
            base_files={"ext/schema.snapshot.sql": "CREATE FUNCTION a();\n"},
            head_files={"ext/schema.snapshot.sql": None},
        )
        done, _ = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assert_lines(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: ext/schema.snapshot.sql: tracked in the base and"
                    " not in the head"
                ),
                REMEDY,
            ],
        )


class TheCheckout(Planted):
    """What the task stands on: a merge commit, and a tracked Cargo.toml."""

    def test_one_parent_is_red_by_name(self) -> None:
        """HEAD is not a merge commit: there is no base to measure against."""
        self.commit({"Cargo.toml": "[workspace]\n", "metadata.json": metadata()}, "x")
        self.commit({"README.md": "y\n"}, "y")
        done, log = self.run_check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: HEAD has 1 parent(s), not 2 — the gate checks out the pull"
            " request's merge commit, whose first parent is the base it is"
            " measured against\n",
        )
        self.assert_lines(log, [])

    def test_no_cargo_toml_is_skipped(self) -> None:
        """No tracked Cargo.toml: the skip line, and nothing runs."""
        self.merge({"README.md": "x\n"}, {"README.md": "y\n"})
        done, log = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"{LABEL}: no Cargo.toml tracked, skipped\n")
        self.assert_lines(log, [])


class TheStep(unittest.TestCase):
    """ci.yml runs the task on every pull request, in the test leg."""

    def test_the_step(self) -> None:
        """In the gate job, before "Run the gate", with no author condition."""
        gate = workflow_yaml.steps(
            workflow_yaml.jobs(workflow_yaml.document(CI))["gate"]
        )
        names = [step.get("name") for step in gate]
        at = names.index("The title declares what the change breaks")
        step = gate[at]
        self.assertLess(at, names.index("Run the gate"))
        restore = names.index("Restore the cargo caches (unsigned gate only)")
        self.assertLess(restore, at)
        self.assertEqual(
            step.get("if"),
            "${{ matrix.leg == 'test' && github.event_name == 'pull_request' }}",
        )
        self.assertEqual(step.get("run"), "mise run break:check")
        env: dict[str, object] = {
            "PR_TITLE": "${{ github.event.pull_request.title }}",
            "PR_BODY": "${{ github.event.pull_request.body }}",
        }
        self.assertEqual(step.get("env"), env)


# The declarations held to the reader: a title and body, and whether
# the pull request declares a break.
DECLARATIONS: dict[str, tuple[str, str, bool]] = {
    "bang": ("feat!: x", "", True),
    "bang after a scope": ("feat(scope)!: x", "", True),
    "footer": ("feat: x", "Why.\n\nBREAKING CHANGE: x is gone.", True),
    "hyphened footer after another": ("feat: x", "Refs #1\nBREAKING-CHANGE: y", True),
    "nothing": ("feat: x", "", False),
    "lower-case footer": ("feat: x", "breaking change: x is gone.", False),
    "a bang in the description": ("feat: x! y", "", False),
}


class TheReaderAgrees(unittest.TestCase):
    """#1906: "declared breaking" is what stele files under "Breaking"."""

    @override
    def setUp(self) -> None:
        """Skip unless the belt's stele is installed; plant a repository."""
        if not STELE:
            self.skipTest("stele is the belt's release engine and renders the notes")
        self.stele = STELE
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = hermetic.env(self.tmp)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "chore: start")
        self.git("tag", "v1.0.0")

    def git(self, *args: str) -> None:
        """Run git in the scratch repository."""
        subprocess.run(
            [GIT, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            check=True,
            timeout=60,
        )

    def filed_breaking(self, title: str, body: str) -> bool:
        """Commit the squash message and render its notes.

        Returns:
            True when stele files the commit under "Breaking".

        """
        message = self.tmp / "message"
        text = f"{title}\n\n{body}\n" if body else f"{title}\n"
        message.write_text(text, encoding="utf-8")
        self.git(
            "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", str(message)
        )
        done = subprocess.run(
            [self.stele, "derive", "notes", "-git-dir", "."],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        tags = len(list((self.repo / ".git" / "refs" / "tags").iterdir()))
        self.git("tag", f"v{tags + 1}.0.0")
        return "### Breaking\n" in done.stdout

    def test_every_declaration(self) -> None:
        """The reading and the reader agree, row by row."""
        for row, (title, body, breaking) in DECLARATIONS.items():
            with self.subTest(row=row):
                reading = declared_break.declared(title, body)
                filed = self.filed_breaking(title, body)
                self.assertEqual((reading, filed), (breaking, breaking))


if __name__ == "__main__":
    unittest.main()

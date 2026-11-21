#!/usr/bin/env python3
"""The scheduled jobs' table holds every job to its task's guard (#1891).

Each reusable subject workflow's `applies` job decides from
`mise/audit-legs.toml` which jobs apply to the tree, and each
conditional job reads its one output in a job-level `if:`. The cost of
a table is that a task's guard widens and the row does not, so the job
is skipped where its task would have run. These rows move that into the
canon's own gate: for every job of every subject workflow they read the
`mise run audit:*` tasks its steps run, and every belt script those run,
with `mise/test_gate_tools.py`'s readers (#1410), find the guard each
skips on, and fail when the row is narrower than the guard or the job
carries no `if:` on it.

A GUARD is what a task tests before its first "skipped" exit (the
reading is test_gate_tools.py's). A task with no such exit, or one whose
guard names no pathspec, runs everywhere, and its job must be
unconditional. A job that runs no task (the seed draw) exists for the
jobs that need it and takes their guards.

The helper's rows are trees the scheduled runs meet: an empty tree, a
Rust-only tree shaped like edtf, a Go tree, and the canon's own. The
fuzz job's runner list (#1899) has rows of its own, and one holding it
to the targets coverage-measure.sh's `fuzz_targets` measures (#1896)
on one planted tree: a target the floor holds is a target the nightly
search runs.

stdlib `unittest`, matching the other mise/test_*.py.
"""

import contextlib
import copy
import io
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import audit_legs
import coverage_floor
import decoded
import gate_tools
import hermetic
import test_gate_tools as reading
import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = CANON / ".github" / "workflows"
GIT = hermetic.tool("git")
BASH = hermetic.tool("bash")
MEASURE = CANON / "mise" / "coverage-measure.sh"

# The jobs that are the shape, not a subject: the decision itself, and
# the red-check report every scheduled workflow ends with (#1887).
STRUCTURAL = ("applies", "report")
# repo-mutants.yml gates each language on its own config file in its
# `adoption` job; rerouting it through the table was rejected (#1891).
EXEMPT = ("repo-mutants",)
TASK = re.compile(r"mise run (audit:[\w:-]+)")
FLOOR_LEG = re.compile(r"coverage_floor\.py\"? read --leg ([\w-]+)")

Document = dict[str, object]
Table = dict[str, dict[str, audit_legs.Row]]


@dataclass(frozen=True)
class Guard:
    """What a job's tasks test before they skip."""

    who: str  # the tasks, or the jobs it exists for
    paths: frozenset[str] | None  # None: runs everywhere
    floors: frozenset[str] = frozenset()


def subject_workflows() -> dict[str, Document]:
    """Read every reusable subject workflow the table must cover.

    Returns:
        Workflow stem to its document: each repo-*.yml but EXEMPT.

    """
    return {
        path.stem: workflow_yaml.document(path)
        for path in sorted(WORKFLOWS.glob("repo-*.yml"))
        if path.stem not in EXEMPT
    }


def needs_of(job: Document) -> list[str]:
    """Read a job's `needs`, a string or a list.

    Returns:
        The job ids it needs.

    """
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return [needs]
    return [decoded.as_str(n, "needs") for n in decoded.as_array(needs, "needs")]


def task_guard(name: str, tasks: reading.Tasks) -> Guard:
    """Read one task's guard.

    Returns:
        Its pathspecs, None where it runs everywhere, and the floor legs
        it reads.

    """
    lines = reading.expand(reading.body(tasks[name]), set())
    text = "\n".join(t for _, t in lines)
    floors = frozenset(reading.found(FLOOR_LEG, text))
    skip = reading.first_skip(lines)
    paths = reading.guard_paths(lines[: skip + 1]) if skip is not None else set()
    if not paths or "*" in paths:
        return Guard(name, None, floors)
    return Guard(name, frozenset(paths), floors)


def union(who: str, guards: list[Guard]) -> Guard:
    """Join the guards of a job's parts: it applies where any part does.

    Returns:
        One guard; None paths when any part runs everywhere, or none.

    """
    floors = frozenset().union(*(g.floors for g in guards))
    if not guards or any(g.paths is None for g in guards):
        return Guard(who, None, floors)
    return Guard(who, frozenset().union(*(g.paths or () for g in guards)), floors)


def derivation(
    documents: dict[str, Document], tasks: reading.Tasks | None = None
) -> dict[str, dict[str, Guard]]:
    """Find every subject job's guard.

    Returns:
        Workflow stem to job id to its guard, STRUCTURAL jobs left out.

    """
    tasks = tasks if tasks is not None else reading.belt_tasks()
    out: dict[str, dict[str, Guard]] = {}
    for stem, document in documents.items():
        jobs = {
            k: v for k, v in workflow_yaml.jobs(document).items() if k not in STRUCTURAL
        }
        own: dict[str, Guard] = {}
        for job_id, job in jobs.items():
            text = "\n".join(
                decoded.as_str(step.get("run", ""), f"{job_id} run")
                for step in workflow_yaml.steps(job)
            )
            named = [t for t in reading.found(TASK, text) if t in tasks]
            if named:
                parts = [task_guard(t, tasks) for t in named]
                own[job_id] = union(" and ".join(named), parts)
        guards = dict(own)
        for job_id in jobs:
            if job_id in own:
                continue
            users = [k for k, v in jobs.items() if job_id in needs_of(v) and k in own]
            guards[job_id] = union(
                f"nothing (it exists for {', '.join(users) or 'no job'})",
                [own[u] for u in users],
            )
        out[stem] = guards
    return out


def condition(job: Document) -> str:
    """Read a job's `if:` without its `${{ }}`.

    Returns:
        The expression, "" where the job has none.

    """
    text = decoded.as_str(job.get("if", ""), "if").strip()
    return text.removeprefix("${{").removesuffix("}}").strip()


def table_drift(
    stem: str, rows: dict[str, audit_legs.Row], guards: dict[str, Guard]
) -> list[str]:
    """Hold one workflow's rows to its jobs' guards.

    Returns:
        One line per job with no row, row with no job, and row narrower
        or wider than its job's guard allows.

    """
    out = [
        f"{stem}.yml: {job} has no row in mise/audit-legs.toml"
        for job in guards
        if job not in rows
    ]
    out += [
        f"mise/audit-legs.toml: {stem}.{job} names no job of {stem}.yml"
        for job in rows
        if job not in guards
    ]
    for job, row in rows.items():
        guard = guards.get(job)
        if guard is None:
            continue
        if guard.paths is None and row.when is not None:
            out.append(
                f"{stem}.yml: {job} runs {guard.who} before any guard;"
                " its row must carry no `when`"
            )
        if row.floor is not None and row.floor not in guard.floors:
            out.append(f"{stem}.{job}: its floor {row.floor} is no leg its tasks read")
        if guard.paths is None or row.when is None:
            continue
        when = row.when
        missing = sorted(
            p
            for p in guard.paths
            if not any(w == p or gate_tools.matches(w, p) for w in when)
        )
        if missing:
            out.append(
                f"{stem}.yml: {job} runs {guard.who} on a tree tracking"
                f" {' '.join(missing)}; its row's `when` does not run it there"
            )
    return out


def job_drift(
    stem: str,
    document: Document,
    rows: dict[str, audit_legs.Row],
    guards: dict[str, Guard],
) -> list[str]:
    """Hold one workflow's jobs to their guards and rows.

    Returns:
        One line per guarded job with no `if:` on its output, per `if:`
        with no row to answer it, and per wrong `applies` or `report`.

    """
    out: list[str] = []
    jobs = workflow_yaml.jobs(document)
    conditional = [j for j, r in rows.items() if r.when is not None and j in jobs]
    for job, guard in guards.items():
        want = f"needs.applies.outputs.{job} == 'true'"
        gated = condition(jobs[job]) == want and "applies" in needs_of(jobs[job])
        row = rows.get(job)
        if guard.paths is not None and not gated:
            out.append(
                f"{stem}.yml: {job} runs {guard.who}, which skips on a tree"
                f" tracking none of {' '.join(sorted(guard.paths))}; the job"
                f" carries no `if: ${{{{ {want} }}}}`"
            )
        if gated and (row is None or row.when is None):
            out.append(
                f"{stem}.yml: {job} reads its applies output; its row carries no `when`"
            )
    applies = jobs.get("applies")
    if applies is None:
        if conditional:
            out.append(
                f"{stem}.yml: rows {' '.join(conditional)} are conditional;"
                " no `applies` job"
            )
        return out
    outputs = sorted(decoded.as_object(applies.get("outputs", {}), "outputs"))
    lists = [output for output, _, _ in audit_legs.MATRICES.get(stem, {}).values()]
    if outputs != sorted(conditional + lists):
        out.append(
            f"{stem}.yml: applies declares {' '.join(outputs)}; the conditional"
            f" rows and lists are {' '.join(sorted(conditional + lists))}"
        )
    runs = " ".join(
        decoded.as_str(s.get("run", ""), "run") for s in workflow_yaml.steps(applies)
    )
    if f"audit_legs.py {stem} " not in runs:
        out.append(f"{stem}.yml: applies runs no `audit_legs.py {stem}`")
    report = jobs.get("report")
    if report is not None and "applies" not in needs_of(report):
        out.append(f"{stem}.yml: report does not need applies")
    return out


def drift(
    table: Table,
    documents: dict[str, Document] | None = None,
    tasks: reading.Tasks | None = None,
) -> list[str]:
    """Hold the table and the workflows to the derivation.

    Returns:
        Every line `table_drift` and `job_drift` find, and one per
        subject workflow with no table, or table with no workflow.

    """
    documents = documents if documents is not None else subject_workflows()
    found = derivation(documents, tasks)
    out = [
        f"{s}.yml has no table in mise/audit-legs.toml"
        for s in documents
        if s not in table
    ]
    out += [
        f"mise/audit-legs.toml: {s} is no subject workflow"
        for s in table
        if s not in documents
    ]
    for stem, document in documents.items():
        rows = table.get(stem, {})
        out += table_drift(stem, rows, found[stem])
        out += job_drift(stem, document, rows, found[stem])
    return out


class Derivation(unittest.TestCase):
    """The table and the `if:`s cover every guard every job's task tests."""

    def test_the_table_holds_the_workflows(self) -> None:
        """The real table against the real workflows and task bodies."""
        none: list[str] = []
        self.assertEqual(none, drift(audit_legs.load_table()))

    def test_a_narrow_row_is_named(self) -> None:
        """Planted: go-vulns's row forgets the nested go.mod forms."""
        table = audit_legs.load_table()
        table["repo-dependencies"]["go-vulns"] = audit_legs.Row(when=("go.mod",))
        self.assertIn(
            "repo-dependencies.yml: go-vulns runs audit:go-vulns on a tree"
            " tracking */*/go.mod */go.mod; its row's `when` does not run it there",
            drift(table),
        )

    def test_a_guarded_job_with_no_if_is_named(self) -> None:
        """Planted: a job runs a guarded task with no `if:` on applies."""
        documents = subject_workflows()
        planted = copy.deepcopy(documents["repo-dependencies"])
        jobs = decoded.as_object(planted["jobs"], "jobs")
        jobs["planted"] = {
            "runs-on": "ubuntu-26.04",
            "steps": [{"run": "mise run audit:deny"}],
        }
        documents["repo-dependencies"] = planted
        table = audit_legs.load_table()
        table["repo-dependencies"]["planted"] = audit_legs.Row(
            when=("Cargo.lock", "*/Cargo.lock")
        )
        self.assertIn(
            "repo-dependencies.yml: planted runs audit:deny, which skips on a"
            " tree tracking none of */Cargo.lock Cargo.lock; the job carries no"
            " `if: ${{ needs.applies.outputs.planted == 'true' }}`",
            drift(table, documents),
        )

    def test_a_row_on_an_unguarded_job_is_named(self) -> None:
        """Planted: links runs everywhere, so a `when` would skip it."""
        table = audit_legs.load_table()
        table["repo-records"]["links"] = audit_legs.Row(when=("*.md",))
        got = drift(table)
        self.assertIn(
            "repo-records.yml: links runs audit:links before any guard;"
            " its row must carry no `when`",
            got,
        )
        self.assertIn(
            "repo-records.yml: applies declares bestpractices; the conditional"
            " rows and lists are bestpractices links",
            got,
        )

    def test_a_task_free_job_takes_its_users_guards(self) -> None:
        """The seed draw applies where the property job does."""
        guards = derivation(subject_workflows())["repo-deep-tests"]
        seed = guards["python-properties-seed"]
        self.assertEqual(guards["python-properties"].paths, seed.paths)
        self.assertEqual("nothing (it exists for python-properties)", seed.who)

    def test_a_floor_no_task_reads_is_named(self) -> None:
        """Planted: semver's row names a floor its task never reads."""
        table = audit_legs.load_table()
        row = table["repo-public-api"]["semver"]
        table["repo-public-api"]["semver"] = audit_legs.Row(
            when=row.when, floor="rust-branches"
        )
        self.assertIn(
            "repo-public-api.semver: its floor rust-branches is no leg its tasks read",
            drift(table),
        )

    def test_rows_and_jobs_match_one_for_one(self) -> None:
        """A job with no row, a row with no job, and the wiring are named."""
        table = audit_legs.load_table()
        del table["repo-records"]["regression-tests"]
        table["repo-records"]["gone"] = audit_legs.Row()
        table["nowhere"] = {}
        documents = subject_workflows()
        records = copy.deepcopy(documents["repo-records"])
        jobs = decoded.as_object(records["jobs"], "jobs")
        report = decoded.as_object(jobs["report"], "report")
        report["needs"] = ["links"]
        applies = decoded.as_object(jobs["applies"], "applies")
        applies["steps"] = []
        documents["repo-records"] = records
        public = copy.deepcopy(documents["repo-public-api"])
        del decoded.as_object(public["jobs"], "jobs")["applies"]
        documents["repo-public-api"] = public
        got = drift(table, documents)
        for line in (
            "repo-records.yml: regression-tests has no row in mise/audit-legs.toml",
            "mise/audit-legs.toml: repo-records.gone names no job of repo-records.yml",
            "mise/audit-legs.toml: nowhere is no subject workflow",
            "repo-records.yml: report does not need applies",
            "repo-records.yml: applies runs no `audit_legs.py repo-records`",
            "repo-public-api.yml: rows semver are conditional; no `applies` job",
        ):
            with self.subTest(line=line):
                self.assertIn(line, got)
        del table["repo-records"]
        self.assertIn(
            "repo-records.yml has no table in mise/audit-legs.toml", drift(table)
        )

    def test_an_if_with_no_row_is_named(self) -> None:
        """Planted: regression-tests reads an output no row declares."""
        documents = subject_workflows()
        records = copy.deepcopy(documents["repo-records"])
        job = decoded.as_object(
            decoded.as_object(records["jobs"], "jobs")["regression-tests"], "job"
        )
        job["needs"] = "applies"
        job["if"] = "${{ needs.applies.outputs.regression-tests == 'true' }}"
        documents["repo-records"] = records
        self.assertIn(
            "repo-records.yml: regression-tests reads its applies output;"
            " its row carries no `when`",
            drift(audit_legs.load_table(), documents),
        )


def git_tree(files: dict[str, str]) -> Path:
    """Plant a git tree with the files tracked.

    Returns:
        The tree's root, removed at process exit by tempfile.

    """
    root = Path(tempfile.mkdtemp(prefix="audit-legs-"))
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, "utf-8")
    env = hermetic.env()
    subprocess.run([GIT, "-C", str(root), "init", "-q"], check=True, env=env)
    subprocess.run([GIT, "-C", str(root), "add", "-A"], check=True, env=env)
    return root


def floor_file(*legs: str) -> str:
    """Write a .coverage-floor holding the given legs apart.

    Returns:
        The file, as the derivation writes it.

    """
    return coverage_floor.render(
        Decimal(95), "v1.0.0", separate=dict.fromkeys(legs, Decimal(80))
    )


def raw_outputs(root: Path, workflow: str) -> dict[str, str]:
    """Run the helper's CLI over a tree.

    Returns:
        Each output to its value, as $GITHUB_OUTPUT would read it.

    Raises:
        AssertionError: the helper exited non-zero.

    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        status = audit_legs.main(["--root", str(root), workflow])
    if status != 0:
        msg = f"audit_legs.py {workflow} exited {status}"
        raise AssertionError(msg)
    return dict(line.split("=", 1) for line in out.getvalue().splitlines())


def outputs(root: Path, workflow: str) -> dict[str, bool]:
    """Run the helper's CLI over a tree.

    Returns:
        Job id to whether it applies, as $GITHUB_OUTPUT would read it.

    Raises:
        AssertionError: the helper exited non-zero.

    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        status = audit_legs.main(["--root", str(root), workflow])
    if status != 0:
        msg = f"audit_legs.py {workflow} exited {status}"
        raise AssertionError(msg)
    pairs = [line.split("=", 1) for line in out.getvalue().splitlines()]
    return {job: value == "true" for job, value in pairs}


def every(root: Path) -> dict[str, bool]:
    """Run the helper for every workflow the table names.

    Returns:
        Job id to whether it applies, across workflows.

    """
    got: dict[str, bool] = {}
    for workflow in audit_legs.load_table():
        got.update(outputs(root, workflow))
    return got


# edtf's shape (run 44825163551): a Rust workspace with a pgrx crate,
# its fuzz crate, locks, workflows and records, and no Python,
# TypeScript or Go.
def fuzz_manifest(*targets: str) -> str:
    """Write a fuzz crate's manifest with one `[[bin]]` per target.

    Returns:
        The manifest.

    """
    bins = "".join(
        f'\n[[bin]]\nname = "{t}"\npath = "fuzz_targets/{t}.rs"\n' for t in targets
    )
    return f'[package]\nname = "x-fuzz"\n\n[workspace]\n{bins}'


GO_FUZZ_TEST = """\
package policy

import "testing"

func FuzzLoad(f *testing.F) {
\tf.Fuzz(func(t *testing.T, b []byte) {})
}
"""

EDTF = {
    "Cargo.toml": "[workspace]\n",
    "Cargo.lock": "",
    "crates/edtf/Cargo.toml": "[package]\n",
    "crates/edtf-pg/Cargo.toml": "[package]\n",
    "fuzz/Cargo.toml": fuzz_manifest("parse", "roundtrip"),
    "fuzz/Cargo.lock": "",
    "deny.toml": "",
    "mise.toml": "",
    "mise.lock": "",
    ".github/workflows/ci.yml": "on: push\n",
    "README.md": "# x\n",
    ".bestpractices.json": "{}\n",
    ".coverage-floor": floor_file("rust-branches"),
}
GO = {
    "go.mod": "module x\n",
    "cmd/x/main.go": "package main\n",
    "internal/policy/x_test.go": GO_FUZZ_TEST,
    ".github/workflows/ci.yml": "on: push\n",
    ".coverage-floor": floor_file("go-branches"),
}
# The twelve jobs edtf ran for a language it does not have: eight
# property shards are one job id here.
NOT_EDTF = (
    "python-properties-seed",
    "python-properties",
    "typescript-properties",
    "coverage-go-branches",
    "go-vulns",
)


class Helper(unittest.TestCase):
    """The helper's outputs for the trees the scheduled runs meet."""

    def test_an_empty_tree_runs_the_unconditional_jobs_only(self) -> None:
        """Nothing tracked: every conditional job is skipped."""
        got = every(git_tree({}))
        for workflow, rows in audit_legs.load_table().items():
            for job, row in rows.items():
                with self.subTest(workflow=workflow, job=job):
                    self.assertEqual(row.when is None, got[job])

    def test_a_rust_only_tree_shaped_like_edtf(self) -> None:
        """The jobs edtf ran for languages it lacks come out false."""
        got = every(git_tree(EDTF))
        for job in NOT_EDTF:
            with self.subTest(job=job):
                self.assertFalse(got[job])
        for job in (
            "deny",
            "fuzz",
            "semver",
            "coverage-branches",
            "actions",
            "action-pins",
            "lock-arch",
            "bestpractices",
        ):
            with self.subTest(job=job):
                self.assertTrue(got[job])

    def test_a_go_tree(self) -> None:
        """go.mod brings the Go jobs and fuzz, and nothing Rust."""
        got = every(git_tree(GO))
        for job, want in (
            ("go-vulns", True),
            ("coverage-go-branches", True),
            ("fuzz", True),
            ("deny", False),
            ("semver", False),
            ("coverage-branches", False),
            ("python-properties", False),
        ):
            with self.subTest(job=job):
                self.assertEqual(want, got[job])

    def test_the_canon_tree(self) -> None:
        """The canon has Python properties, workflows and a lock; no Rust or Go."""
        got = every(CANON)
        for job, want in (
            ("python-properties-seed", True),
            ("python-properties", True),
            ("lock-idempotent", True),
            ("bestpractices", True),
            ("deny", False),
            ("go-vulns", False),
            ("fuzz", False),
            ("coverage-branches", False),
            ("coverage-go-branches", False),
            ("typescript-properties", False),
        ):
            with self.subTest(job=job):
                self.assertEqual(want, got[job])

    def test_a_floor_leg_must_be_recorded(self) -> None:
        """A floor job applies only where .coverage-floor records its leg."""
        for files, want in (
            (
                {**GO, ".coverage-floor": floor_file()},
                "skipped: no go-branches line in .coverage-floor",
            ),
            (
                {k: v for k, v in GO.items() if k != ".coverage-floor"},
                "skipped: no .coverage-floor is tracked",
            ),
            ({**GO, ".coverage-floor": "not a floor\n"}, "applies: go.mod is tracked"),
            (GO, "applies: go.mod is tracked"),
        ):
            root = git_tree(files)
            with self.subTest(want=want):
                rows = audit_legs.load_table()["repo-deep-tests"]
                reasons = audit_legs.applies(rows, root, gate_tools.tracked_files(root))
                self.assertEqual(want, reasons["coverage-go-branches"])


# Every shape the enumeration meets: two Rust targets, a root-package
# and a nested-package Go target, the bare `Fuzz` name, a helper that
# takes no *testing.F, a nested module's target (#1899), a vendored one
# at the root and one in the nested module, and an untracked one. All
# but the vendored, the helper and the untracked are measured, so run.
PLANTED_TARGETS = {
    "fuzz/Cargo.toml": fuzz_manifest("parse", "roundtrip"),
    "go.mod": "module example.com/x\n",
    "x_test.go": "package x\n\nfunc Fuzz(f *testing.F) {}\n",
    "internal/policy/x_test.go": GO_FUZZ_TEST
    + "\nfunc FuzzHelper(t *testing.T) {}\nfunc FuzzSave(f *testing.F) {}\n",
    "tools/go.mod": "module example.com/x/tools\n",
    "tools/gen/gen_test.go": "package gen\n\nfunc FuzzGen(f *testing.F) {}\n",
    "vendor/y/y_test.go": "package y\n\nfunc FuzzY(f *testing.F) {}\n",
    "tools/vendor/z/z_test.go": "package z\n\nfunc FuzzZ(f *testing.F) {}\n",
}


class FuzzTargets(unittest.TestCase):
    """The fuzz job runs one runner per target the floor measures (#1899)."""

    def test_two_bins_and_one_go_target_emit_three_entries(self) -> None:
        """Two [[bin]]s and FuzzLoad in internal/policy: three entries, fuzz on.

        Against the helper before #1899 there is no `fuzz-targets`
        output, so this row fails there.
        """
        got = raw_outputs(
            git_tree({
                "fuzz/Cargo.toml": fuzz_manifest("parse", "roundtrip"),
                "go.mod": "module example.com/x\n",
                "internal/policy/x_test.go": GO_FUZZ_TEST,
            }),
            "repo-deep-tests",
        )
        want: list[audit_legs.Target] = [
            {"kind": "rust", "target": "parse"},
            {"kind": "rust", "target": "roundtrip"},
            {
                "kind": "go",
                "target": "FuzzLoad",
                "package": "internal/policy",
                "module": ".",
            },
        ]
        entries = decoded.as_array(decoded.load_json(got["fuzz-targets"]), "list")
        got_entries: list[dict[str, object]] = [
            decoded.as_object(e, "entry") for e in entries
        ]
        self.assertEqual(want, got_entries)
        self.assertEqual("true", got["fuzz"])

    def test_a_tree_with_neither_emits_an_empty_list(self) -> None:
        """No fuzz crate and no Go fuzz target: `[]`, and fuzz is off."""
        trees: list[dict[str, str]] = [
            {},
            {**GO, "internal/policy/x_test.go": "package policy\n"},
        ]
        for files in trees:
            got = raw_outputs(git_tree(files), "repo-deep-tests")
            with self.subTest(files=" ".join(sorted(files))):
                self.assertEqual("[]", got["fuzz-targets"])
                self.assertEqual("false", got["fuzz"])

    def test_a_fuzz_crate_with_no_bin_is_skipped_by_name(self) -> None:
        """fuzz/ applies by the table, but no [[bin]] means no runner."""
        root = git_tree({"fuzz/Cargo.toml": fuzz_manifest()})
        rows = audit_legs.load_table()["repo-deep-tests"]
        tracked = gate_tools.tracked_files(root)
        reasons, lists = audit_legs.matrices(
            "repo-deep-tests", audit_legs.applies(rows, root, tracked), root, tracked
        )
        self.assertEqual("skipped: no fuzz target is declared", reasons["fuzz"])
        names: list[str] = list(lists)
        want: list[str] = ["fuzz-targets"]
        self.assertEqual(want, names)
        self.assertEqual(0, len(lists["fuzz-targets"]))

    def test_an_unreadable_fuzz_manifest_is_red_naming_it(self) -> None:
        """A fuzz/Cargo.toml that will not parse fails the applies job by name."""
        for text, message in (
            ("[[bin]\n", "fuzz/Cargo.toml: "),
            ("[[bin]]\npath = 'x.rs'\n", "fuzz/Cargo.toml: [[bin]] 1.name is not"),
        ):
            root = git_tree({"fuzz/Cargo.toml": text})
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                status = audit_legs.main(["--root", str(root), "repo-deep-tests"])
            with self.subTest(text=text):
                self.assertEqual(1, status)
                self.assertTrue(
                    err.getvalue().startswith(f"::error::audit-legs: {message}"),
                    err.getvalue(),
                )

    def test_the_list_is_the_targets_the_floor_measures(self) -> None:
        """audit_legs.py and coverage-measure.sh's fuzz_targets agree on one tree."""
        root = git_tree(PLANTED_TARGETS)
        (root / "internal" / "policy" / "y_test.go").write_text(
            "package policy\n\nfunc FuzzUntracked(f *testing.F) {}\n", "utf-8"
        )
        listed: list[tuple[str, str, str, str]] = [
            (t["kind"], t.get("package", "-"), t["target"], t.get("module", "-"))
            for t in audit_legs.fuzz_targets(root, gate_tools.tracked_files(root))
        ]
        want: list[tuple[str, str, str, str]] = [
            ("rust", "-", "parse", "-"),
            ("rust", "-", "roundtrip", "-"),
            ("go", "internal/policy", "FuzzLoad", "."),
            ("go", "internal/policy", "FuzzSave", "."),
            ("go", "tools/gen", "FuzzGen", "tools"),
            ("go", ".", "Fuzz", "."),
        ]
        self.assertEqual(want, listed)
        self.assertEqual(listed, measured_targets(root))


def measured_targets(root: Path) -> list[tuple[str, str, str, str]]:
    """Run coverage-measure.sh's own `fuzz_targets` over a tree.

    The function is read out of the script, between its line and the
    next closing brace in column one, and run with the libraries it
    sources: the enumeration, not the measurement.

    Returns:
        Each target as (kind, package directory or `-`, name, module
        directory or `-`).

    Raises:
        AssertionError: the script defines no such function.

    """
    text = MEASURE.read_text(encoding="utf-8")
    found = re.search(r"^fuzz_targets\(\) \{\n.*?^\}\n", text, re.MULTILINE | re.DOTALL)
    if found is None:
        msg = f"{MEASURE} defines no fuzz_targets function"
        raise AssertionError(msg)
    script = (
        f'. "{CANON / "mise" / "fuzz-corpus-lib.sh"}"\n'
        f'. "{CANON / "mise" / "fuzz-measure-lib.sh"}"\n'
        f"{found.group(0)}"
        'label=probe\nfuzz_targets\nprintf "%s" "${fuzz_found}"\n'
    )
    run = subprocess.run(
        [BASH, "-euo", "pipefail", "-c", script],
        cwd=root,
        env=hermetic.env() | {"ORG_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=True,
    )
    records = [line.split(" ") for line in run.stdout.splitlines() if line]
    return [(kind, where, name, module) for _, kind, where, name, module in records]


class Refusals(unittest.TestCase):
    """A table of the wrong shape, or a workflow it lacks, is refused by name."""

    def run_main(self, table_text: str, workflow: str = "w") -> tuple[int, str, Path]:
        """Run the helper in-process over a table written here.

        Returns:
            Exit status, stderr and the table's path.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        table = Path(tmp.name) / "audit-legs.toml"
        table.write_text(table_text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            status = audit_legs.main([
                "--root",
                str(CANON),
                "--table",
                str(table),
                workflow,
            ])
        return status, err.getvalue(), table

    def test_wrong_shapes_in_the_table(self) -> None:
        """Each shape the decoder refuses, with the field it names."""
        rows = [
            ("[w\n", "Expected ']'"),
            ("w = 1\n", "w is not an object"),
            ("[w]\nx = 1\n", "w.x is not an object"),
            ('[w.x]\nwhen = "a"\n', "w.x.when is not a list of strings"),
            ("[w.x]\nwhen = [1]\n", "w.x.when is not a list of strings"),
            ('[w.x]\nfloor = "python"\n', "w.x.floor is not a leg held apart"),
            ('[w.x]\nfor = ["a"]\n', "w.x carries for: a row has `when` and `floor`"),
        ]
        for text, message in rows:
            with self.subTest(text=text):
                status, err, table = self.run_main(text)
                self.assertEqual(1, status)
                self.assertTrue(
                    err.startswith(f"::error::audit-legs: {table}: "), msg=err
                )
                self.assertIn(message, err)

    def test_an_unknown_workflow(self) -> None:
        """A workflow the table has no section for is refused, not run."""
        status, err, table = self.run_main("[w.x]\n", "v")
        self.assertEqual(1, status)
        self.assertEqual(f"::error::audit-legs: {table} has no table for v\n", err)

    def test_a_well_formed_table_decodes(self) -> None:
        """Absent fields take their defaults."""
        with tempfile.TemporaryDirectory() as d:
            table = Path(d) / "t.toml"
            table.write_text(
                '[w.a]\nwhen = ["x"]\nfloor = "go-branches"\n[w.b]\n', encoding="utf-8"
            )
            got = audit_legs.load_table(table)
        want: Table = {
            "w": {
                "a": audit_legs.Row(when=("x",), floor="go-branches"),
                "b": audit_legs.Row(),
            }
        }
        self.assertEqual(want, got)


if __name__ == "__main__":
    unittest.main()

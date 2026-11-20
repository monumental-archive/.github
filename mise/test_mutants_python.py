#!/usr/bin/env python3
"""Table tests for audit:mutants' Python leg, mise/mutants-python.sh (#1771).

The leg runs for real in a planted git tree, with a stand-in
`cosmic-ray` on PATH that answers in cosmic-ray 8.9.0's own forms:
`init` writes the session, `dump` prints one JSON work item per line
(the row's mutants), and `mutate-and-test PATH OPERATOR OCCURRENCE
COMMAND` rewrites the file, runs the command the leg handed it, prints
`{"worker_outcome", "output", "test_outcome", "diff"}` and restores the
file. Every call is logged; each row asserts the exact calls and the
exact messages, so a row cannot pass by stopping somewhere else. The
plan's measurement is real: every planted test file runs alone under
coverage.

The bytecode row has no stand-in: two same-sized versions of a module,
written within one second, each imported by a child process.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic
import mutants_python as helper
import tool_python

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-python.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
LABEL = "audit:mutants: python"

# The stand-in, in cosmic-ray 8.9.0's shape. PLANT_MUTANTS maps
# "PATH OPERATOR OCCURRENCE" to what the mutant does: `replace` (the old
# and new text), `worker` (a worker outcome other than normal), `leave`
# (the file is not restored), `kill_parent` (the judge dies mid-
# mutant) and `silent` (cosmic-ray exits 3 and prints nothing).
# PLANT_INIT_FAILS makes `init` fail. The command runs as cosmic-ray
# runs it: split with shlex, PYTHONDONTWRITEBYTECODE set,
# PLANT_UNDER_MUTANT set for the tests.
STAND_IN = """#!{python}
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
logged = args[:4] if args[0] == "mutate-and-test" else args[:1]
with open(os.environ["PLANT_LOG"], "a", encoding="utf-8") as log:
    log.write("cosmic-ray " + " ".join(logged) + "\\n")
if args[0] == "init":
    if os.environ.get("PLANT_INIT_FAILS"):
        sys.stderr.write("planted init failure\\n")
        sys.exit(2)
    Path(args[2]).write_text("", encoding="utf-8")
    sys.exit(0)
if args[0] == "dump":
    sys.stdout.write(Path(os.environ["PLANT_DUMP"]).read_text(encoding="utf-8"))
    sys.exit(0)
path, operator, occurrence, command = args[1:5]
plan = json.loads(Path(os.environ["PLANT_MUTANTS"]).read_text(encoding="utf-8"))
mutant = plan.get(f"{path} {operator} {occurrence}", {})
if mutant.get("silent"):
    sys.exit(3)
if "worker" in mutant:
    print(json.dumps({"worker_outcome": mutant["worker"], "output": None,
                      "test_outcome": None, "diff": None}))
    sys.exit(0)
original = Path(path).read_text(encoding="utf-8")
old, new = mutant.get("replace", ["", ""])
Path(path).write_text(original.replace(old, new, 1), encoding="utf-8")
if mutant.get("kill_parent"):
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(0)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PLANT_UNDER_MUTANT="1")
done = subprocess.run(shlex.split(command), env=env, capture_output=True, text=True)
if not mutant.get("leave"):
    Path(path).write_text(original, encoding="utf-8")
head = f"--- mutation diff ---\\n--- a/{path}\\n+++ b/{path}\\n"
diff = head + f"@@ -2 +2 @@\\n-{old}\\n+{new}"
print(json.dumps({
    "worker_outcome": "normal",
    "output": done.stdout,
    "test_outcome": "killed" if done.returncode else "survived",
    "diff": diff,
}))
"""

CONFIG = """[cosmic-ray]
module-path = ["pkg"]
timeout = 120.0
excluded-modules = ["pkg/test_*.py"]
test-command = "python3 -m unittest discover -s {dir} -p {name} -f"

[cosmic-ray.distributor]
name = "local"

[audit-mutants]
tests = ["pkg/test_*.py"]
"""
# `answer`'s return is on line 2; `unreached`'s on line 6, which no test
# executes.
MODULE = "def answer(x):\n    return x + 1\n\n\ndef unreached(x):\n    return x * 2\n"
# A test file that records, under a mutant, that it ran.
TEST = """import os
import time
import unittest

import mod


class Answer(unittest.TestCase):
    def test_answer(self):
        time.sleep({sleep})
        if os.environ.get("PLANT_UNDER_MUTANT"):
            with open(os.environ["PLANT_RAN"], "a", encoding="utf-8") as ran:
                ran.write("{name}\\n")
        self.assertEqual(mod.answer(1), 2)
"""
ADD = "core/ReplaceBinaryOperator_Add_Sub"
NUM = "core/NumberReplacer"
MUL = "core/ReplaceBinaryOperator_Mul_Add"
# What each planted mutant does to the file.
KILLS: dict[str, object] = {"replace": ["x + 1", "x - 1"]}
SURVIVES: dict[str, object] = {"replace": ["return x + 1", "return (x + 1)"]}
# A mutant whose test forks a tree of itself, 511 processes alive at
# once: past the runner's margin of 256, inside the row's own 1024.
# A fork that fails fails the test.
FANS_OUT: dict[str, object] = {
    "replace": [
        "return x + 1",
        """import os
    import time

    def grow(depth):
        kids, failed = [], False
        for _ in range(2 if depth else 0):
            try:
                pid = os.fork()
            except OSError:
                failed = True
                break
            if pid == 0:
                code = 1
                try:
                    code = grow(depth - 1)
                finally:
                    os._exit(code)
            kids.append(pid)
        if not depth:
            time.sleep(0.3)
        for kid in kids:
            failed = os.waitpid(kid, 0)[1] != 0 or failed
        return int(failed)

    if grow(8):
        raise OSError("a fork failed")
    return x + 1""",
    ]
}
# The row's own ceiling, set before the leg starts: the runner's
# `ceiling` with the row's margin, then the leg in its place.
CAPPED = """import os
import runpy
import sys

runpy.run_path(sys.argv[1])["ceiling"](int(sys.argv[2]))()
os.execv(sys.argv[3], sys.argv[3:])
"""
RED = [
    f"{LABEL}: red — kill each survivor with an assertion, or exclude it at its site",
    "  with '# pragma: no mutate -- <why the mutant is equivalent>'",
]
REASON = "  an exclusion records a mutant the suite cannot kill; say why at the site"


def work_item(path: str, operator: str, occurrence: int, line: int, name: str) -> str:
    """Write one `cosmic-ray dump` line, a work item with no result.

    Returns:
        The JSON line.

    """
    spec: dict[str, object] = {
        "module_path": path,
        "operator_name": operator,
        "occurrence": occurrence,
        "start_pos": [line, 11],
        "end_pos": [line, 12],
        "operator_args": {},
        "definition_name": name,
    }
    item: list[object] = [
        {"job_id": f"{path}-{operator}-{occurrence}", "mutations": [spec]},
        None,
    ]
    return json.dumps(item)


def calls(*mutants: tuple[str, str, int]) -> list[str]:
    """Spell the stand-in's log for a whole run.

    Returns:
        `init`, `dump`, then one `mutate-and-test` per mutant.

    """
    return [
        "cosmic-ray init",
        "cosmic-ray dump",
        *(f"cosmic-ray mutate-and-test {p} {o} {n}" for p, o, n in mutants),
    ]


def git_in(tree: Path, *args: str) -> None:
    """Run git in a planted tree, scrubbed of the caller's git state."""
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [GIT, *args], cwd=tree, env=hermetic.env(), check=True
    )


def git_out(tree: Path, *args: str) -> str:
    """Ask git in a planted tree.

    Returns:
        Its output, stripped.

    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [GIT, *args],
        cwd=tree,
        env=hermetic.env(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def init_tree(tree: Path) -> None:
    """Make an empty git tree with an identity of its own."""
    tree.mkdir(parents=True)
    git_in(tree, "init", "-q")
    git_in(tree, "config", "user.email", "t@example.invalid")
    git_in(tree, "config", "user.name", "t")


def stand_in_env(root: Path) -> dict[str, str]:
    """Install the stand-in under ROOT and build the leg's environment.

    Returns:
        The environment: the stand-in first on PATH, its files under
        ROOT, and no mutation state inherited.

    """
    bins = root / "bin"
    bins.mkdir()
    stand_in = bins / "cosmic-ray"
    stand_in.write_text(STAND_IN.replace("{python}", sys.executable), encoding="utf-8")
    stand_in.chmod(0o755)
    for name in ("log", "ran", "dump"):
        (root / name).touch()
    (root / "mutants.json").write_text("{}", encoding="utf-8")
    env = hermetic.env() | {
        "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
        "PLANT_LOG": str(root / "log"),
        "PLANT_RAN": str(root / "ran"),
        "PLANT_DUMP": str(root / "dump"),
        "PLANT_MUTANTS": str(root / "mutants.json"),
        "MUTANTS_SUMMARY_DIR": str(root / "summary"),
        "ORG_BELT_DIR": str(BELT),
        "ORG_PYTHON": sys.executable,
    }
    for name in ("MUTANTS_SHARD", "MUTANTS_PLAN", "PLANT_UNDER_MUTANT"):
        env.pop(name, None)
    return env


# The tree every judging row copies, planned once for real: the module,
# its test file, and a dump holding every judging row's mutants. A row
# that needs another module, dump or test file plants its own.
SHARED: dict[str, Path] = {}


def judged(*mutants: tuple[str, str, int]) -> list[str]:
    """Spell the stand-in's log for a shard judged from a plan.

    Returns:
        One `mutate-and-test` per mutant, nothing listed again.

    """
    return [f"cosmic-ray mutate-and-test {p} {o} {n}" for p, o, n in mutants]


class Planted(unittest.TestCase):
    """A planted tree and a stand-in cosmic-ray; the rows are below."""

    @override
    def setUp(self) -> None:
        """Plant a git tree and the stand-in."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.bins = self.tmp / "bin"
        self.log = self.tmp / "log"
        self.ran = self.tmp / "ran"
        self.dump = self.tmp / "dump"
        self.mutants = self.tmp / "mutants.json"
        self.summary = self.tmp / "summary"
        self.env = stand_in_env(self.tmp)

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        git_in(self.tree, *args)

    def track(self, path: str, text: str) -> None:
        """Write a file into the planted tree and track it."""
        if not self.tree.exists():
            init_tree(self.tree)
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", path)

    def plant(
        self,
        items: list[str],
        mutants: dict[str, dict[str, object]] | None = None,
        *,
        module: str = MODULE,
        config: str = CONFIG,
    ) -> None:
        """Track the adoption, the module and its test; plant the dump."""
        self.track("cosmic-ray.toml", config)
        self.track("pkg/mod.py", module)
        self.track("pkg/test_mod.py", TEST.format(sleep=0, name="test_mod"))
        self.git("commit", "-q", "-m", "plant")
        self.dump.write_text("".join(f"{i}\n" for i in items), encoding="utf-8")
        chosen: dict[str, dict[str, object]] = mutants or {}
        self.mutants.write_text(json.dumps(chosen), encoding="utf-8")

    def shared(
        self,
        keys: list[tuple[str, str, int]],
        mutants: dict[str, dict[str, object]] | None = None,
    ) -> Path:
        """Copy the shared tree, and its plan cut to this row's mutants.

        The tree is copied whole, `.git` included, so its HEAD is the
        plan's; the plan's line map is the one real coverage measured
        when the module was set up. Nothing the row does reaches another.

        Returns:
            The row's own plan directory, for MUTANTS_PLAN.

        """
        shutil.copytree(SHARED["tree"], self.tree, symlinks=True)
        document = decoded.as_object(
            decoded.load_json(SHARED["plan"].read_bytes()), "the shared plan"
        )
        wanted = {f"{p} {o} {n}" for p, o, n in keys}
        kept = [
            row
            for row in decoded.as_array(document.get("mutants"), "mutants")
            if " ".join(map(str, decoded.as_array(row, "a mutant")[:3])) in wanted
        ]
        self.assertEqual(len(kept), len(keys))
        document["mutants"] = kept
        plan = self.tmp / "plan"
        plan.mkdir()
        (plan / "plan.json").write_text(json.dumps(document), encoding="utf-8")
        chosen: dict[str, dict[str, object]] = mutants or {}
        self.mutants.write_text(json.dumps(chosen), encoding="utf-8")
        return plan

    def run_leg(
        self, *under: str, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the leg in the planted tree, under UNDER's command if any.

        Returns:
            The finished process and the stand-in's call log.

        """
        self.log.write_text("", encoding="utf-8")
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [*under, BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def call(self, *argv: str) -> hermetic.Called:
        """Run one of the helper's steps in this process, in the tree.

        Returns:
            What the step returned and wrote.

        """
        return hermetic.call_main(
            lambda: helper.main(list(argv)), env=self.env, cwd=self.tree
        )

    def counts(self) -> str:
        """Read the counts the leg wrote for the totals job.

        Returns:
            python.tsv's text.

        """
        return (self.summary / "python.tsv").read_text(encoding="utf-8")


ITEM_KILLED = work_item("pkg/mod.py", ADD, 0, 2, "answer")
ITEM_SURVIVOR = work_item("pkg/mod.py", NUM, 0, 2, "answer")
ITEM_UNREACHED = work_item("pkg/mod.py", MUL, 0, 6, "unreached")
KILLED = ("pkg/mod.py", ADD, 0)
SURVIVOR = ("pkg/mod.py", NUM, 0)
UNREACHED = ("pkg/mod.py", MUL, 0)
FOUR = [("pkg/mod.py", ADD, n) for n in range(4)]


def setUpModule() -> None:
    """Plant the shared tree and plan it once, with real coverage.

    Raises:
        RuntimeError: the shared plan failed; every judging row needs it.

    """
    root = Path(tempfile.mkdtemp())
    unittest.addModuleCleanup(shutil.rmtree, root)
    env = stand_in_env(root)
    tree = root / "tree"
    init_tree(tree)
    for path, text in (
        ("cosmic-ray.toml", CONFIG),
        ("pkg/mod.py", MODULE),
        ("pkg/test_mod.py", TEST.format(sleep=0, name="test_mod")),
    ):
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_text(text, encoding="utf-8")
    git_in(tree, "add", ".")
    git_in(tree, "commit", "-q", "-m", "shared")
    items = [ITEM_SURVIVOR, ITEM_UNREACHED]
    items += [work_item(*key, 2, "answer") for key in FOUR]
    (root / "dump").write_text("".join(f"{i}\n" for i in items), encoding="utf-8")
    plan = root / "plan"
    planned = hermetic.call_main(
        lambda: helper.main(["plan", "cosmic-ray.toml", str(plan)]), env=env, cwd=tree
    )
    if planned.returncode != 0:
        raise RuntimeError(planned.stderr)
    SHARED["tree"] = tree
    SHARED["plan"] = plan / "plan.json"


class Adoption(Planted):
    """cosmic-ray.toml is the switch."""

    def test_no_adoption_file_skips_clean(self) -> None:
        """No cosmic-ray.toml: exit 0, named, nothing called, skipped counted."""
        self.track("pkg/mod.py", MODULE)
        done, log = self.run_leg()
        self.assertEqual(
            (done.returncode, done.stdout, done.stderr),
            (
                0,
                (
                    f"{LABEL}: no cosmic-ray.toml tracked at the repository's"
                    " root, skipped\n"
                ),
                "",
            ),
        )
        self.assertEqual(log, list[str]())
        self.assertEqual(self.counts(), "0/1\tskipped\n")

    def test_cosmic_ray_missing_is_named(self) -> None:
        """Adopted, no tool: red, with the install line."""
        self.shared([KILLED])
        (self.bins / "cosmic-ray").unlink()
        # Every other directory on the path, less any that holds a real one.
        path = os.pathsep.join(
            d
            for d in self.env["PATH"].split(os.pathsep)
            if not Path(d, "cosmic-ray").exists()
        )
        done, _ = self.run_leg(PATH=path)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: cosmic-ray missing — the belt pins it lazy; install it"
            " by name:\n    mise install pipx:cosmic-ray\n",
        )


class Verdicts(Planted):
    """What each outcome comes to."""

    def test_clean_result(self) -> None:
        """Every mutant killed: exit 0, the summary line, the counts."""
        plan = self.shared([KILLED], {f"pkg/mod.py {ADD} 0": KILLS})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                f"{LABEL}: 1 mutants, 1 killed, 0 survived, 0 excluded\n"
            ),
            done.stdout,
        )
        self.assertEqual(log, judged(KILLED))
        self.assertEqual(self.counts(), "0/1\t1\t1\t0\t0\n")
        self.assertEqual(self.ran.read_text(encoding="utf-8"), "test_mod\n")

    def test_one_survivor_is_red_with_its_file_and_line(self) -> None:
        """A survivor: red, its file:line, operator and diff printed."""
        plan = self.shared([SURVIVOR], {f"pkg/mod.py {NUM} 0": SURVIVES})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            f"{LABEL}: survived: pkg/mod.py:2: {NUM} #0 (answer)\n"
            "    @@ -2 +2 @@\n"
            "    -return x + 1\n"
            "    +return (x + 1)\n"
            f"{LABEL}: 1 mutants, 0 killed, 1 survived, 0 excluded\n",
            done.stdout,
        )
        self.assertEqual(done.stderr.splitlines(), RED)
        self.assertEqual(log, judged(SURVIVOR))
        self.assertEqual(self.counts(), "0/1\t1\t0\t1\t0\n")

    def test_no_tests_runs_nothing(self) -> None:
        """A line no test file executes: "no tests", a survivor, nothing run."""
        plan = self.shared([UNREACHED], {f"pkg/mod.py {MUL} 0": KILLS})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            f"{LABEL}: no tests: pkg/mod.py:6: {MUL} #0 (unreached): return x * 2\n",
            done.stdout,
        )
        self.assertIn(
            f"{LABEL}: 1 mutants, 0 killed, 1 survived, 0 excluded\n", done.stdout
        )
        self.assertEqual(log, list[str]())
        self.assertEqual(done.stderr.splitlines(), RED)

    def test_abnormal_worker_is_never_judged(self) -> None:
        """A worker outcome `abnormal`: red by name, no counts written."""
        plan = self.shared([KILLED], {f"pkg/mod.py {ADD} 0": {"worker": "abnormal"}})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: never judged: pkg/mod.py:2: {ADD} #0 (answer):"
            " worker outcome 'abnormal'\n",
        )
        self.assertEqual(log, judged(KILLED))
        self.assertFalse((self.summary / "python.tsv").exists())

    def test_continuation_line_is_judged_by_its_statement(self) -> None:
        """A mutant on a call's second line is reached through the call's first."""
        module = "def answer(x):\n    return max(\n        x + 1, 0)\n"
        item = work_item("pkg/mod.py", ADD, 0, 3, "answer")
        self.plant([item], {f"pkg/mod.py {ADD} 0": KILLS}, module=module)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(log, calls(("pkg/mod.py", ADD, 0)))


class Exclusions(Planted):
    """A pragma on the mutant's first line, with its reason."""

    def test_reasoned_pragma_is_excluded_and_counted(self) -> None:
        """Excluded, counted, never run."""
        module = MODULE.replace(
            "x + 1\n", "x + 1  # pragma: no mutate -- the planted equivalent\n"
        )
        self.plant([ITEM_SURVIVOR], {f"pkg/mod.py {NUM} 0": SURVIVES}, module=module)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                f"{LABEL}: 0 mutants, 0 killed, 0 survived, 1 excluded\n"
            )
        )
        self.assertEqual(log, calls())
        self.assertEqual(self.counts(), "0/1\t0\t0\t0\t1\n")

    def test_pragma_without_a_reason_is_red_before_anything_runs(self) -> None:
        """No reason after ` -- `: red by name, no tool called."""
        self.plant(
            [ITEM_KILLED],
            module=MODULE.replace("x + 1\n", "x + 1  # pragma: no mutate\n"),
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: pkg/mod.py:2: # pragma: no mutate with no reason"
            " — write it after ' -- '\n" + REASON + "\n",
        )
        self.assertEqual(log, list[str]())

    def test_block_form_is_red_by_name(self) -> None:
        """`block` (and `start`, `end`): only the line form exists."""
        self.plant(
            [ITEM_KILLED],
            module=MODULE.replace(
                "x + 1\n", "x + 1  # pragma: no mutate block -- why\n"
            ),
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: pkg/mod.py:2: # pragma: no mutate block — only the line"
            " form exists; mark each line\n" + REASON + "\n",
        )
        self.assertEqual(log, list[str]())

    def test_pragma_in_a_string_is_not_a_site(self) -> None:
        """A string that spells a pragma excludes nothing."""
        module = MODULE + 'TEXT = "# pragma: no mutate"\n'
        self.plant([ITEM_KILLED], {f"pkg/mod.py {ADD} 0": KILLS}, module=module)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_mutated_test_file_is_red_by_name(self) -> None:
        """A mutant in a file `tests` matches is a leaked exclusion."""
        item = work_item("pkg/test_mod.py", ADD, 0, 1, "")
        self.plant([ITEM_KILLED, item])
        done = self.call("plan", "cosmic-ray.toml", str(self.tmp / "plan"))
        log = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: pkg/test_mod.py: a test file is mutated — exclude it in"
            " excluded-modules (a `**` skips dot-directories)\n",
        )
        self.assertEqual(log, calls())

    def test_dot_directory_test_file_is_red_by_name(self) -> None:
        """A test file under `.github/`, missed by `**/test_*.py`, is red."""
        config = (
            CONFIG
            .replace(
                'excluded-modules = ["pkg/test_*.py"]',
                'excluded-modules = ["**/test_*.py"]',
            )
            .replace('module-path = ["pkg"]', 'module-path = ["pkg", ".github/tool"]')
            .replace(
                'tests = ["pkg/test_*.py"]',
                'tests = ["pkg/test_*.py", ".github/tool/test_*.py"]',
            )
        )
        self.track(".github/tool/test_tool.py", "import unittest\n")
        item = work_item(".github/tool/test_tool.py", ADD, 0, 1, "")
        self.plant([ITEM_KILLED, item], config=config)
        done = self.call("plan", "cosmic-ray.toml", str(self.tmp / "plan"))
        log = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: .github/tool/test_tool.py: a test file is mutated — exclude"
            " it in excluded-modules (a `**` skips dot-directories)\n",
        )
        self.assertEqual(log, calls())


class Safety(Planted):
    """The tree starts clean and ends as git has it."""

    def test_dirty_tree_is_refused_and_kept(self) -> None:
        """A tracked change under module-path: refused by name, left as it is."""
        self.plant([ITEM_KILLED])
        edited = MODULE + "# the user's edit\n"
        (self.tree / "pkg" / "mod.py").write_text(edited, encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: the tracked tree differs from git under module-path —"
            " refused:\n  pkg/mod.py\n"
            "  the leg mutates these files in place and restores them from git\n",
        )
        self.assertEqual(log, list[str]())
        self.assertEqual(
            (self.tree / "pkg" / "mod.py").read_text(encoding="utf-8"), edited
        )

    def test_file_left_mutated_is_restored_and_red(self) -> None:
        """A mutant the worker did not undo: restored from git, red by name."""
        plan = self.shared([KILLED], {f"pkg/mod.py {ADD} 0": {**KILLS, "leave": True}})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: pkg/mod.py differed from git after pkg/mod.py:2: {ADD} #0"
            " (answer) — restored\n",
        )
        self.assertEqual(log, judged(KILLED))
        self.assertEqual(
            (self.tree / "pkg" / "mod.py").read_text(encoding="utf-8"), MODULE
        )

    def test_run_killed_mid_mutant_is_restored_by_the_trap(self) -> None:
        """The judge dies with a mutant applied: the exit trap restores it."""
        plan = self.shared(
            [KILLED], {f"pkg/mod.py {ADD} 0": {**KILLS, "kill_parent": True}}
        )
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            f"{LABEL}: pkg/mod.py differed from git at exit — restored\n", done.stderr
        )
        self.assertEqual(log, judged(KILLED))
        self.assertEqual(
            (self.tree / "pkg" / "mod.py").read_text(encoding="utf-8"), MODULE
        )

    def test_failing_test_files_are_each_named(self) -> None:
        """Every test file that fails alone is named; nothing is judged."""
        self.plant([ITEM_KILLED])
        failing = (
            "import unittest\n\n\n"
            "class F(unittest.TestCase):\n"
            "    def test_f(self):\n"
            "        self.fail('planted')\n"
        )
        self.track("pkg/test_a.py", failing)
        self.track("pkg/test_b.py", failing)
        self.git("commit", "-q", "-m", "failing")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        named = [line for line in done.stderr.splitlines() if line.startswith(LABEL)]
        want = [
            f"{LABEL}: pkg/test_a.py fails alone (exit 1) — nothing judged",
            f"{LABEL}: pkg/test_b.py fails alone (exit 1) — nothing judged",
        ]
        self.assertEqual(named, want)
        self.assertIn("AssertionError: planted", done.stderr)
        self.assertEqual(log, calls())


class Ceiling(Planted):
    """Each test file runs under a process ceiling (#1833)."""

    def test_fan_out_past_the_margin_is_killed_and_the_next_is_judged(self) -> None:
        """A mutant that forks 511 at once is killed; the next is judged.

        The row caps the whole leg first, in its own child, at the
        user's tasks plus 1024, so the machine is never at risk: under
        that cap alone the 511 fit and the mutant survives.
        """
        runner = self.tmp / "run.py"
        runner.write_text(helper.RUNNER, encoding="utf-8")
        capped = self.tmp / "capped.py"
        capped.write_text(CAPPED, encoding="utf-8")
        plan = self.shared(
            FOUR[:2],
            {f"pkg/mod.py {ADD} 0": FANS_OUT, f"pkg/mod.py {ADD} 1": KILLS},
        )
        done, log = self.run_leg(
            sys.executable,
            str(capped),
            str(runner),
            "1024",
            MUTANTS_PLAN=str(plan),
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(
            done.stdout.endswith(
                f"{LABEL}: 2 mutants, 2 killed, 0 survived, 0 excluded\n"
            ),
            done.stdout,
        )
        self.assertEqual(log, judged(*FOUR[:2]))
        self.assertEqual(self.counts(), "0/1\t2\t2\t0\t0\n")


class Shards(Planted):
    """k/N over the sorted list; a plan made once and judged in shards."""

    def test_two_shards_judge_every_mutant_exactly_once(self) -> None:
        """One plan; 0/2 and 1/2 are disjoint, and together the whole list."""
        plan = self.shared(FOUR, {f"pkg/mod.py {ADD} {n}": KILLS for n in range(4)})
        pragmas = self.tmp / "pragmas.tsv"
        pragmas.touch()
        logs = []
        for shard in ("0/2", "1/2"):
            self.log.write_text("", encoding="utf-8")
            done = self.call(
                "judge", str(plan), str(pragmas), shard, str(self.tmp / shard[0])
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.counts(), f"{shard}\t2\t2\t0\t0\n")
            logs.append(self.log.read_text(encoding="utf-8").splitlines())
        want = [judged(FOUR[0], FOUR[2]), judged(FOUR[1], FOUR[3])]
        self.assertEqual(logs, want)

    def test_plan_step_then_a_shard_judges_from_it(self) -> None:
        """MUTANTS_SHARD=plan writes the plan and stops; a shard judges alone."""
        self.plant([ITEM_KILLED], {f"pkg/mod.py {ADD} 0": KILLS})
        plan = self.tmp / "plan"
        done, log = self.run_leg(MUTANTS_SHARD="plan", MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{LABEL}: plan: 1 mutants, 1 test files", done.stdout)
        self.assertEqual(log, calls())
        self.assertFalse((self.summary / "python.tsv").exists())
        done, log = self.run_leg(MUTANTS_SHARD="0/1", MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [f"cosmic-ray mutate-and-test pkg/mod.py {ADD} 0"]
        self.assertEqual(log, want)
        self.assertEqual(self.counts(), "0/1\t1\t1\t0\t0\n")

    def test_shard_without_a_plan_is_red(self) -> None:
        """MUTANTS_PLAN naming no plan: red by name, nothing judged."""
        self.shared([KILLED])
        empty = self.tmp / "empty"
        empty.mkdir()
        done, log = self.run_leg(MUTANTS_SHARD="0/1", MUTANTS_PLAN=str(empty))
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{LABEL}: no plan to judge from in {empty}:", done.stderr)
        self.assertEqual(log, list[str]())

    def test_bad_shard_is_red(self) -> None:
        """K must be below N."""
        plan = self.shared([KILLED])
        done, log = self.run_leg(MUTANTS_SHARD="2/2", MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: MUTANTS_SHARD='2/2' — expected k/N with 0 <= k < N, or plan\n",
        )
        self.assertEqual(log, list[str]())


class Order(Planted):
    """A mutant's tests run fastest first, stopping at the first failure."""

    def test_faster_file_first_and_its_failure_stops_the_second(self) -> None:
        """Two files execute the line; the faster kills it; the other never runs."""
        self.plant(
            [ITEM_KILLED, ITEM_SURVIVOR],
            {
                f"pkg/mod.py {ADD} 0": KILLS,
                f"pkg/mod.py {NUM} 0": SURVIVES,
            },
        )
        # Named so that sorting by name would run the slow one first.
        self.track("pkg/test_a.py", TEST.format(sleep=0.25, name="test_a"))
        self.git("rm", "-q", "pkg/test_mod.py")
        self.track("pkg/test_b.py", TEST.format(sleep=0, name="test_b"))
        self.git("commit", "-q", "-m", "two files")
        plan = self.tmp / "plan"
        self.assertEqual(self.call("plan", "cosmic-ray.toml", str(plan)).returncode, 0)
        pragmas = self.tmp / "pragmas.tsv"
        pragmas.touch()
        judged = self.call(
            "judge", str(plan), str(pragmas), "0/1", str(self.tmp / "runs")
        )
        self.assertEqual(judged.returncode, 1)
        # Sorted by operator, the survivor (NumberReplacer) is judged first
        # and ran both files, fast first; the killed mutant ran the fast
        # file alone.
        want = ["test_b", "test_a", "test_b"]
        self.assertEqual(self.ran.read_text(encoding="utf-8").splitlines(), want)
        self.assertIn(
            f"{LABEL}: survived: pkg/mod.py:2: {NUM} #0 (answer)", judged.stdout
        )


class Refusals(Planted):
    """Each way the plan or the judge refuses, by name (#1771)."""

    def plan_in_process(self, **env: str) -> tuple[hermetic.Called, list[str]]:
        """Run the plan step in this process, in the planted tree.

        Returns:
            What it returned and wrote, and the stand-in's call log.

        """
        done = hermetic.call_main(
            lambda: helper.main(["plan", "cosmic-ray.toml", str(self.tmp / "plan")]),
            env=self.env | env,
            cwd=self.tree,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def test_adoption_without_its_tables_is_named(self) -> None:
        """No `[cosmic-ray]`, no module paths, no tests: each red by name."""
        for text, message in (
            ('[audit-mutants]\ntests = ["t"]\n', "[cosmic-ray] is not an object"),
            (
                CONFIG.replace('module-path = ["pkg"]', "module-path = []"),
                "module-path names no paths",
            ),
            (
                CONFIG.replace('tests = ["pkg/test_*.py"]', "tests = []"),
                "[audit-mutants] tests names no test files",
            ),
        ):
            config = self.tmp / "cosmic-ray.toml"
            config.write_text(text, encoding="utf-8")
            done = hermetic.call_main(
                lambda: helper.main(["paths", str(self.tmp / "cosmic-ray.toml")]),
                env=self.env,
            )
            self.assertEqual(
                (done.returncode, done.stdout, done.stderr),
                (1, "", f"{LABEL}: {config}: {message}\n"),
            )

    def test_cosmic_ray_failing_to_list_is_named(self) -> None:
        """`init` fails: red, with cosmic-ray's own words; nothing measured."""
        self.plant([ITEM_KILLED])
        done, log = self.plan_in_process(PLANT_INIT_FAILS="1")
        self.assertEqual(
            (done.returncode, done.stderr),
            (
                1,
                (
                    f"{LABEL}: cosmic-ray could not list the mutants (init, dump):\n"
                    "planted init failure\n"
                ),
            ),
        )
        self.assertEqual(log, calls())

    def test_dump_of_two_mutations_is_named(self) -> None:
        """A work item of two mutations is refused, at its own line."""
        item: list[object] = [
            {
                "job_id": "j",
                "mutations": [
                    decoded.as_array(decoded.load_json(ITEM_KILLED), "item")[0],
                    decoded.as_array(decoded.load_json(ITEM_SURVIVOR), "item")[0],
                ],
            },
            None,
        ]
        two = json.dumps(item)
        self.plant(["", two])
        done, log = self.plan_in_process()
        self.assertEqual(
            (done.returncode, done.stderr),
            (1, f"{LABEL}: cosmic-ray dump: dump line 2 holds 2 mutations, not one\n"),
        )
        self.assertEqual(log, calls())

    def test_coverage_missing_is_named(self) -> None:
        """No `coverage` on the path: red by name, no test file run."""
        self.plant([ITEM_KILLED])
        path = os.pathsep.join(
            d
            for d in self.env["PATH"].split(os.pathsep)
            if not Path(d, "coverage").exists()
        )
        done, log = self.plan_in_process(PATH=path)
        self.assertEqual(
            (done.returncode, done.stderr),
            (
                1,
                (
                    f"{LABEL}: coverage missing — the belt pins pipx:coverage;"
                    " run: mise install\n"
                ),
            ),
        )
        self.assertEqual(log, calls())
        self.assertEqual(self.ran.read_text(encoding="utf-8"), "")

    def test_coverage_on_another_python_is_named(self) -> None:
        """Coverage's interpreter is not the suite's python3: red by name."""
        self.plant([ITEM_KILLED])
        stub = self.bins / "coverage"
        stub.write_text(
            "#!/bin/sh\n"
            "echo '  coverage_module: /nowhere/coverage/__init__.py'\n"
            "echo '  executable: /usr/bin/false'\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        python3 = hermetic.tool("python3", path=self.env["PATH"])
        # ruff: ignore[subprocess-without-shell-equals-true]
        suite = subprocess.run(
            [python3, "-c", "import sys; print(sys.version)"],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        done, _ = self.plan_in_process()
        self.assertEqual(
            (done.returncode, done.stderr),
            (
                1,
                (
                    f"{LABEL}: coverage runs on 'no interpreter' but the suite's"
                    f" python3 is '{suite}' — the tool venv must be built on the"
                    " belt's python pin (reinstall: mise install -f pipx:coverage)\n"
                ),
            ),
        )

    def test_coverage_on_a_gone_interpreter_is_named(self) -> None:
        """Coverage's shebang names a missing interpreter: red by name (#1788)."""
        self.plant([ITEM_KILLED])
        stub = self.bins / "coverage"
        stub.write_text("#!/nowhere/python\n", encoding="utf-8")
        stub.chmod(0o755)
        python3 = hermetic.tool("python3", path=self.env["PATH"])
        suite = tool_python.ask(python3, tool_python.VERSION)
        done, _ = self.plan_in_process()
        self.assertEqual(
            (done.returncode, done.stderr),
            (
                1,
                (
                    f"{LABEL}: coverage runs on 'no interpreter' but the suite's"
                    f" python3 is '{suite}' — the tool venv must be built on the"
                    " belt's python pin (reinstall: mise install -f pipx:coverage)\n"
                ),
            ),
        )

    def test_suite_that_writes_no_coverage_reaches_nothing(self) -> None:
        """A test command coverage never sees: every mutant is "no tests"."""
        self.plant(
            [ITEM_KILLED],
            config=CONFIG.replace(
                "python3 -m unittest discover -s {dir} -p {name} -f", "true {test}"
            ),
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            f"{LABEL}: no tests: pkg/mod.py:2: {ADD} #0 (answer): return x + 1\n",
            done.stdout,
        )
        self.assertEqual(log, calls())

    def test_coverage_report_unread_reaches_nothing(self) -> None:
        """`coverage json` writes no report: the line map is empty, not a crash."""
        self.plant([ITEM_KILLED])
        real = hermetic.tool("coverage", path=self.env["PATH"])
        stub = self.bins / "coverage"
        stub.write_text(
            f'#!/bin/sh\n[ "$1" = json ] && exit 0\nexec {real} "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        # The wrapper stands in the venv, so the real venv's interpreter
        # is started from beside it, where the belt's check looks (#1788).
        venv_python = Path(os.path.realpath(real)).parent / "python"
        beside = self.bins / "python"
        beside.write_text(f'#!/bin/sh\nexec {venv_python} "$@"\n', encoding="utf-8")
        beside.chmod(0o755)
        done, _ = self.plan_in_process()
        self.assertEqual(done.returncode, 0, done.stderr)
        plan = decoded.as_object(
            decoded.load_json((self.tmp / "plan" / "plan.json").read_bytes()), "plan"
        )
        self.assertEqual(plan.get("lines"), dict[str, object]())

    def test_silent_cosmic_ray_is_never_judged(self) -> None:
        """cosmic-ray printing no result: red by name, with its exit status."""
        plan = self.shared([KILLED], {f"pkg/mod.py {ADD} 0": {"silent": True}})
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: never judged: pkg/mod.py:2: {ADD} #0 (answer):"
            " worker outcome 'cosmic-ray exited 3'\n",
        )
        self.assertEqual(log, judged(KILLED))

    def test_plan_of_another_commit_is_refused(self) -> None:
        """A shard's checkout is not the plan's commit: red by name."""
        plan = self.shared([KILLED])
        before = git_out(self.tree, "rev-parse", "HEAD")
        self.git("commit", "-q", "--allow-empty", "-m", "later")
        after = git_out(self.tree, "rev-parse", "HEAD")
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            f"{LABEL}: the plan is of {before}, but this checkout is {after}\n",
        )
        self.assertEqual(log, list[str]())

    def test_long_shard_says_how_far_it_is(self) -> None:
        """Every hundred mutants, one progress line."""
        plan = self.shared([UNREACHED])
        document = decoded.as_object(
            decoded.load_json((plan / "plan.json").read_bytes()), "plan"
        )
        document["mutants"] = [
            ["pkg/mod.py", MUL, n, 6, "unreached", 6] for n in range(100)
        ]
        (plan / "plan.json").write_text(json.dumps(document), encoding="utf-8")
        done, log = self.run_leg(MUTANTS_PLAN=str(plan))
        self.assertEqual(done.returncode, 1)
        want = [f"{LABEL}: judged 100 of 100", *RED]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertTrue(
            done.stdout.endswith(
                f"{LABEL}: 100 mutants, 0 killed, 100 survived, 0 excluded\n"
            )
        )
        self.assertEqual(log, list[str]())

    def test_report_without_a_summary_directory(self) -> None:
        """No MUTANTS_SUMMARY_DIR: the summary line, and no counts file."""
        env = {k: v for k, v in self.env.items() if k != "MUTANTS_SUMMARY_DIR"}
        done = hermetic.call_main(lambda: helper.report([], 0, "0/1"), env=env)
        self.assertEqual(
            (done.returncode, done.stdout),
            (0, f"{LABEL}: 0 mutants, 0 killed, 0 survived, 0 excluded\n"),
        )
        self.assertFalse(self.summary.exists())


class Bytecode(unittest.TestCase):
    """The cache trap, with no stand-in (#1771)."""

    def test_second_child_sees_the_second_version(self) -> None:
        """Two same-sized versions in one second: the second import sees v2.

        Python serves a `.pyc` whose recorded size and mtime (seconds)
        match the source. Without the cache removed, the second child
        prints the FIRST version: the control below proves the trap.
        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        module = tmp / "mod.py"
        env = hermetic.env()
        env.pop("PYTHONDONTWRITEBYTECODE", None)

        def child() -> str:
            done = subprocess.run(
                [sys.executable, "-c", "import mod; print(mod.VALUE)"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            return done.stdout.strip()

        def write(text: str) -> None:
            module.write_text(text, encoding="utf-8")
            os.utime(module, ns=(1_700_000_000_000_000_000,) * 2)

        write("VALUE = 1\n")
        self.assertEqual(child(), "1")
        write("VALUE = 2\n")
        self.assertEqual(child(), "1", "the control: the cached version is served")
        helper.uncache(str(module))
        self.assertEqual(child(), "2")


class Readers(unittest.TestCase):
    """The pure readers, row by row."""

    def test_shard_parsing(self) -> None:
        """k/N with 0 <= k < N; anything else is None."""
        self.assertEqual(helper.parse_shard("3/16"), (3, 16))
        for bad in ("16/16", "1/0", "plan", "a/2", "1", "-1/2"):
            self.assertIsNone(helper.parse_shard(bad), bad)

    def test_time_limit(self) -> None:
        """Three planned runs, never under ten seconds."""
        self.assertEqual(helper.limit_seconds(100), 10.0)
        self.assertEqual(helper.limit_seconds(5000), 15.0)

    def test_test_command_is_filled_per_word(self) -> None:
        """`{test}`, `{dir}` and `{name}`, in the words shlex splits."""
        filled = ["run", "a b", "test_c.py x", "a b/test_c.py"]
        bare = ["t", "."]
        self.assertEqual(
            helper.test_argv("run {dir} '{name} x' {test}", "a b/test_c.py"), filled
        )
        self.assertEqual(helper.test_argv("t {dir}", "test_c.py"), bare)

    def test_pragma_on_no_line_number_is_neither(self) -> None:
        """A record whose line is not a number is no site and no refusal."""
        found = helper.read_pragmas("f.py\tx\t# pragma: no mutate -- why\n")
        self.assertEqual((found.reasoned, found.refused), (set(), []))

    def test_unreadable_source_reads_as_nothing(self) -> None:
        """A file that cannot be read: its line is empty, its statement the line."""
        missing = "nowhere/gone.py"
        self.assertEqual(helper.source_line(missing, 1), "")
        placed = helper.with_statements([helper.Mutant(missing, ADD, 0, 7, "f")])
        self.assertEqual(placed[0].statement, 7)

    def test_plan_with_a_line_that_is_no_number_is_refused(self) -> None:
        """The line map's keys are line numbers, or the plan is refused."""
        document: dict[str, object] = {
            "head": "h",
            "command": "c",
            "mutants": [],
            "tests": [],
            "lines": {"a.py": {"x": [0]}},
        }
        text = json.dumps(document)
        with self.assertRaisesRegex(
            decoded.UndecodableError, r"^a\.py: line 'x' is not a number$"
        ):
            helper.read_plan(text)

    def test_statement_of_a_continuation_line(self) -> None:
        """The innermost statement holding the line; the line when none does."""
        spans = helper.statement_lines("if x:\n    y = f(\n        1)\n")
        self.assertEqual(helper.statement_of(spans, 3), 2)
        self.assertEqual(helper.statement_of(spans, 1), 1)
        self.assertEqual(helper.statement_of(spans, 9), 9)
        self.assertEqual(helper.statement_lines("def (:\n"), list[tuple[int, int]]())


if __name__ == "__main__":
    unittest.main()

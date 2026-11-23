#!/usr/bin/env python3
"""lint:deny and audit:deny run once per workspace (#2009).

Both checked the root lockfile only, so a second lockfile in the tree
(a cargo-fuzz crate's `fuzz/Cargo.lock`, in edtf, iiif-server and
release-lab) met no licence, ban, source or advisory policy. Each now
takes the tree's roots from `org_rust_workspace_roots` (#2004) and runs
once per root, against the `deny.toml` beside that root's manifest,
past a red root.

Each row runs the task's own `run` string, read out of mise/config.toml
(which runs mise/deny-lint.sh or mise/deny-audit.sh from ORG_BELT_DIR,
this belt), in a planted git tree with two roots: `Cargo.toml`, and
`fuzz/Cargo.toml` as its own workspace, each with its lock and its
deny.toml. cargo, cargo-deny and stele are stubs that log their
arguments: cargo answers `locate-project` by path and hands `cargo
deny` to cargo-deny as cargo does; cargo-deny refuses the root
PLANT_DENY_FAILS names and otherwise passes (its advisories scan writes
JSON to stderr and exits 1, as the real one does for a finding); stele
prints a PASS report. The real rust_workspaces.py, deny_advisories.py,
deny_skips.py and stele_findings.py run. Each refusing row asserts its
message (#650).

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
ADVISORY = "RUSTSEC-2021-0127"

# What the bodies reach for besides the stubs, linked into a PATH of
# their own so a row cannot reach a real cargo or stele.
TOOLS = ("bash", "git", "mktemp", "rm")

POLICY = '[bans]\nwildcards = "deny"\n'
# The two roots: each manifest, lock and policy, tracked.
TREE = {
    "Cargo.toml": "[workspace]\n",
    "Cargo.lock": "# planted\n",
    "deny.toml": POLICY,
    "fuzz/Cargo.toml": "[package]\nname = 'a-fuzz'\n\n[workspace]\n",
    "fuzz/Cargo.lock": "# planted\n",
    "fuzz/deny.toml": POLICY,
}
ROOTS = ("Cargo.toml", "fuzz/Cargo.toml")

CARGO_STUB = """#!{bash}
case "$1" in
  locate-project)
    case "$4" in
      fuzz/*) echo "${PWD}/fuzz/Cargo.toml" ;;
      *) echo "${PWD}/Cargo.toml" ;;
    esac
    exit 0
    ;;
esac
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$1" in
  fetch) ;;
  deny) exec cargo-deny "$@" ;;
  *) exit 99 ;;
esac
"""
DENY_STUB = """#!{bash}
printf 'cargo-deny %s\\n' "$*" >> "${PLANT_LOG}"
if [[ $3 == "${PLANT_DENY_FAILS:-}" ]]; then
  echo "error: planted: rejected licence in $3"
  exit 6
fi
if [[ $* == *advisories* ]]; then
  printf '%s\\n' '{"type":"diagnostic"}' >&2
  exit 1
fi
"""
STELE_STUB = """#!{bash}
printf 'stele %s\\n' "$*" >> "${PLANT_LOG}"
printf '%s' "${PLANT_REPORT}"
"""
REPORT_DOC: dict[str, object] = {
    "schema": 9,
    "target": "assert advisories",
    "subject": "Cargo.lock",
    "verdict": "PASS",
    "findings": list[object](),
}
REPORT = json.dumps(REPORT_DOC)


NO_POLICY = (
    "lint:deny: fuzz: Cargo.lock present but no deny.toml"
    " — copy scaffold/deny.toml from the canon\n"
)


def task_body(name: str) -> str:
    """Read a task's run string out of the belt.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    return decoded.as_str(decoded.as_object(tasks[name], name)["run"], "the run")


def checks(calls: list[str]) -> list[str]:
    """Pick out the policy checks cargo was asked for.

    Returns:
        Each `cargo deny … check bans licenses sources` call, in order.

    """
    return [
        c
        for c in calls
        if c.startswith("cargo deny ") and c.endswith("check bans licenses sources")
    ]


def lint_call(root: str) -> str:
    """Spell the policy check for one root.

    Returns:
        The logged cargo call.

    """
    config = root.replace("Cargo.toml", "deny.toml")
    return (
        f"cargo deny --manifest-path {root} --config {config}"
        " check bans licenses sources"
    )


def both() -> list[str]:
    """Spell the policy checks of both roots, top first.

    Returns:
        The logged cargo calls.

    """
    return [lint_call(r) for r in ROOTS]


def top_only() -> list[str]:
    """Spell the policy check of the top root alone.

    Returns:
        The logged cargo call.

    """
    return [lint_call("Cargo.toml")]


class Planted(unittest.TestCase):
    """A two-root tree, the stubs and the tools, per row."""

    @override
    def setUp(self) -> None:
        """Plant the stubs and the tools; the tree is planted per row."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "tree"
        self.root.mkdir()
        self.canon = self.tmp / "canon"
        (self.canon / "security" / "vex").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stubs = {"cargo": CARGO_STUB, "cargo-deny": DENY_STUB, "stele": STELE_STUB}
        for name, body in stubs.items():
            stub = self.bin / name
            stub.write_text(body.replace("{bash}", BASH), encoding="utf-8")
            stub.chmod(0o755)
        for tool in TOOLS:
            (self.bin / tool).symlink_to(hermetic.tool(tool))
        self.log = self.tmp / "calls.log"

    def plant(self, files: dict[str, str]) -> None:
        """Track `files` in the planted checkout."""
        env = hermetic.env()
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=self.root, env=env, check=True)
        subprocess.run([GIT, "add", "--", *files], cwd=self.root, env=env, check=True)

    def run_task(
        self, task: str, files: dict[str, str], deny_fails: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Plant `files` and run the task's body over them.

        Returns:
            The finished process and the stubs' logged calls.

        """
        self.plant(files)
        env = hermetic.env(
            PATH=str(self.bin),
            PLANT_LOG=str(self.log),
            PLANT_REPORT=REPORT,
            PLANT_DENY_FAILS=deny_fails,
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(self.canon),
            ORG_PYTHON=sys.executable,
            ORG_PYTHON_UNSET="unset",
            ORG_FINDINGS="",
            MISE_TASK_NAME=task,
        )
        as_mise = hermetic.task_run(task, task_body(task))
        done = subprocess.run(
            as_mise.argv,
            cwd=self.root,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = (
            self.log.read_text(encoding="utf-8").splitlines()
            if self.log.exists()
            else []
        )
        return done, calls


class LintDeny(Planted):
    """lint:deny checks each root against its own deny.toml."""

    def test_both_roots_are_checked_each_with_its_own_policy(self) -> None:
        """One policy check per root, each with its manifest and config."""
        done, calls = self.run_task("lint:deny", TREE)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(both(), checks(calls))
        fetches = [c for c in calls if c.startswith("cargo fetch")]
        want = [f"cargo fetch --locked --manifest-path {r}" for r in ROOTS]
        self.assertEqual(want, fetches)

    def test_a_missing_fuzz_policy_is_red_naming_fuzz(self) -> None:
        """No fuzz/deny.toml: the finding names `fuzz`; the top is still checked."""
        files = {k: v for k, v in TREE.items() if k != "fuzz/deny.toml"}
        done, calls = self.run_task("lint:deny", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(NO_POLICY, done.stderr)
        self.assertEqual(top_only(), checks(calls))

    def test_a_red_second_root_does_not_hide_the_first(self) -> None:
        """The stub refuses fuzz only: red, and the top was still checked."""
        done, calls = self.run_task("lint:deny", TREE, deny_fails="fuzz/Cargo.toml")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(both(), checks(calls))
        self.assertIn("rejected licence in fuzz/Cargo.toml", done.stdout)

    def test_a_red_first_root_does_not_hide_the_second(self) -> None:
        """The stub refuses the top: red, and fuzz is still checked."""
        done, calls = self.run_task("lint:deny", TREE, deny_fails="Cargo.toml")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(both(), checks(calls))

    def test_an_advisory_ignore_in_the_fuzz_policy_is_refused_by_file(self) -> None:
        """deny_advisories.py names fuzz/deny.toml, and fuzz is not checked."""
        files = dict(TREE)
        files["fuzz/deny.toml"] = f'[advisories]\nignore = ["{ADVISORY}"]\n'
        done, calls = self.run_task("lint:deny", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(
            f"lint:deny: {ADVISORY}: an advisory ignore in fuzz/deny.toml\n"
            "  delete the entry: the decision is the canon's security/vex/ file\n"
            "  for that advisory and package version, which audit:deny reads\n",
            done.stderr,
        )
        self.assertEqual(top_only(), checks(calls))

    def test_a_stale_skip_in_the_fuzz_policy_is_named_with_its_root(self) -> None:
        """A skip the fuzz graph no longer needs: the variant runs there."""
        files = dict(TREE)
        files["fuzz/deny.toml"] = (
            POLICY + 'skip = [\n  { crate = "jobserver@0.1.0", reason = "x" },\n]\n'
        )
        done, calls = self.run_task("lint:deny", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn(
            "lint:deny: fuzz: duplicate-version skip no longer needed:"
            " jobserver@0.1.0\n",
            done.stderr,
        )
        self.assertIn("  delete the entry from fuzz/deny.toml", done.stderr)
        variant = [
            c for c in calls if c.startswith("cargo deny ") and c.endswith(" bans")
        ]
        self.assertEqual(1, len(variant), calls)
        self.assertTrue(
            variant[0].startswith("cargo deny --manifest-path fuzz/Cargo.toml"),
            variant,
        )

    def test_a_lock_with_no_manifest_is_red(self) -> None:
        """A tracked lock and no Cargo.toml: nothing to check is a finding."""
        done, calls = self.run_task("lint:deny", {"Cargo.lock": "# planted\n"})
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(
            "lint:deny: Cargo.lock tracked but no Cargo.toml"
            " — a lock with no manifest is checked by nothing\n",
            done.stderr,
        )
        self.assertEqual(list[str](), calls)


class AuditDeny(Planted):
    """audit:deny scans each root and has stele judge each scan."""

    def test_each_root_is_scanned_and_judged_against_its_lock(self) -> None:
        """Two scans, two stele calls, `--subject` each root's lock."""
        done, calls = self.run_task("audit:deny", TREE)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        scans = [
            c
            for c in calls
            if c.startswith("cargo deny ") and c.endswith("check advisories")
        ]
        want = [
            f"cargo deny --manifest-path {r} --config"
            f" {r.replace('Cargo.toml', 'deny.toml')} --format json check advisories"
            for r in ROOTS
        ]
        self.assertEqual(want, scans)
        stele = [c.split(" ") for c in calls if c.startswith("stele ")]
        subjects = [args[args.index("--subject") + 1] for args in stele]
        locks: list[str] = ["Cargo.lock", "fuzz/Cargo.lock"]
        self.assertEqual(locks, subjects)
        self.assertEqual("audit:deny: stele: PASS\n" * 2, done.stdout)

    def test_a_missing_fuzz_policy_is_red_and_the_top_still_scanned(self) -> None:
        """No fuzz/deny.toml: its finding; the top root judged as before."""
        files = {k: v for k, v in TREE.items() if k != "fuzz/deny.toml"}
        done, calls = self.run_task("audit:deny", files)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(NO_POLICY.replace("lint:deny", "audit:deny"), done.stderr)
        stele = [c for c in calls if c.startswith("stele ")]
        self.assertEqual(1, len(stele), calls)
        self.assertIn("--subject Cargo.lock", stele[0])


if __name__ == "__main__":
    unittest.main()

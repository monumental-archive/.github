#!/usr/bin/env python3
"""A Rust advisory decision is written once (#1926).

Two halves, one table each.

`mise/deny_advisories.py`, which `lint:deny` runs in the gate over a
tracked deny.toml: an `[advisories] ignore` entry that names an
advisory, a bare string or a table with `id`, is red, named, with the
remedy; an entry keyed by `crate` (a yanked version) is green, and so
are an empty array and no `[advisories]` table. Each red row asserts
the exact message. `test_a_bare_id_is_red_by_name` fails before #1926,
when there was no such rule.

`audit:deny`, the task's own `run` string read out of mise/config.toml
and run by bash in a planted git tree, over stub `cargo`, `cargo-deny`
and `stele` (the form test_semver_audit.py uses). The stubs log their
arguments; cargo hands `cargo deny` to cargo-deny as cargo does;
cargo-deny writes its JSON to stderr and exits 1, as the real one does
for a finding; stele prints the row's report and exits the row's
status. The real mise/stele_findings.py reads it. So a row asserts
that cargo-deny's status is not the verdict and stele's is.

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
import deny_advisories as da
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
TASK = "audit:deny"
ADVISORY = "RUSTSEC-2021-0127"

# What the audit body reaches for besides the stubs, linked into a PATH
# of its own so a row cannot reach a real cargo or stele; cp is the
# stele stub's, keeping the scan the task deletes.
TOOLS = ("git", "mktemp", "rm", "cp")


def refused(*advisories: str) -> str:
    """Spell the helper's exact stderr for `advisories`, in order.

    Returns:
        The text.

    """
    return "".join(
        f"lint:deny: {advisory}: an advisory ignore in deny.toml\n"
        "  delete the entry: the decision is the canon's security/vex/ file\n"
        "  for that advisory and package version, which audit:deny reads\n"
        for advisory in advisories
    )


def lint(text: str | None) -> hermetic.Called:
    """Run the helper over a deny.toml holding `text`.

    Returns:
        The status and both streams.

    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deny.toml"
        path.write_text(text or "", encoding="utf-8")
        env = hermetic.env(MISE_TASK_NAME="lint:deny", ORG_FINDINGS="")
        return hermetic.call_main(
            lambda: da.main(["deny_advisories.py", str(path)]), env=env
        )


class DenyAdvisories(unittest.TestCase):
    """An advisory ignore is red by name; a crate ignore and none are green."""

    def assert_red(self, text: str, *advisories: str) -> None:
        """Assert exit 1 with exactly the message naming `advisories`."""
        done = lint(text)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, refused(*advisories))
        self.assertEqual(done.stdout, "")

    def assert_green(self, text: str) -> None:
        """Assert exit 0 with nothing said."""
        done = lint(text)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((done.stdout, done.stderr), ("", ""))

    def test_a_bare_id_is_red_by_name(self) -> None:
        """`ignore = ["RUSTSEC-…"]`, edtf's and release-lab's shape."""
        self.assert_red(f'[advisories]\nignore = ["{ADVISORY}"]\n', ADVISORY)

    def test_a_table_with_an_id_is_red(self) -> None:
        """The scaffold's old commented shape, written out."""
        self.assert_red(
            f'[advisories]\nignore = [{{ id = "{ADVISORY}", reason = "x" }}]\n',
            ADVISORY,
        )

    def test_every_advisory_is_named(self) -> None:
        """Two entries, both named, in the file's order; a crate between."""
        self.assert_red(
            "[advisories]\nignore = [\n"
            '  "RUSTSEC-2026-0543",\n'
            '  { crate = "a@1.0.0", reason = "x" },\n'
            '  { id = "RUSTSEC-2026-0542", reason = "x" },\n'
            "]\n",
            "RUSTSEC-2026-0543",
            "RUSTSEC-2026-0542",
        )

    def test_a_crate_entry_is_green(self) -> None:
        """A yanked version names no advisory, so no decision could stand for it."""
        self.assert_green(
            '[advisories]\nignore = [{ crate = "a@1.0.0", reason = "x" }]\n'
        )

    def test_an_empty_array_is_green(self) -> None:
        """`ignore = []` keeps nothing."""
        self.assert_green("[advisories]\nignore = []\n")

    def test_no_advisories_table_is_green(self) -> None:
        """A deny.toml with no `[advisories]` keeps nothing."""
        self.assert_green('[bans]\nwildcards = "deny"\n')

    def test_the_scaffold_is_green(self) -> None:
        """The stub every repository copies passes the rule it is held to."""
        self.assert_green(
            (BELT.parent / "scaffold" / "deny.toml").read_text(encoding="utf-8")
        )

    def test_an_ignore_that_is_not_an_array_is_refused_by_name(self) -> None:
        """A shape cargo-deny would refuse is never read as no ignore."""
        done = lint('[advisories]\nignore = "x"\n')
        self.assertEqual(done.returncode, 1)
        self.assertIn("advisories.ignore", done.stderr)

    def test_each_finding_is_recorded(self) -> None:
        """ORG_FINDINGS set: one record per advisory, through findings.py."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deny.toml"
            path.write_text(f'[advisories]\nignore = ["{ADVISORY}"]\n', encoding="utf-8")
            records = Path(tmp) / "findings.jsonl"
            env = hermetic.env(MISE_TASK_NAME="lint:deny", ORG_FINDINGS=str(records))
            done = hermetic.call_main(
                lambda: da.main(["deny_advisories.py", str(path)]), env=env
            )
            lines = records.read_text(encoding="utf-8").splitlines()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            [json.loads(line) for line in lines],
            [
                {
                    "v": 1,
                    "task": "lint:deny",
                    "subject": ADVISORY,
                    "message": "an advisory ignore in deny.toml",
                    "detail": [
                        "delete the entry: the decision is the canon's security/vex/ file",
                        "for that advisory and package version, which audit:deny reads",
                    ],
                }
            ],
        )


def task_body(name: str) -> str:
    """Read a task's run string out of the belt as mise would render it.

    Returns:
        The body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    run = decoded.as_str(decoded.as_object(tasks[name], name)["run"], "the run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


CARGO_STUB = """#!{bash}
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$1" in
  fetch) ;;
  deny) exec cargo-deny "$@" ;;
  *) exit 99 ;;
esac
"""
# cargo-deny 0.20.2's JSON goes to stderr and stdout is empty (probed on
# #1926); it exits 1 for a finding. The line is a stand-in: the stele
# stub does not read it, and the row asserts stele was handed its path.
DENY_STUB = """#!{bash}
printf 'cargo-deny %s\\n' "$*" >> "${PLANT_LOG}"
printf '%s\\n' '{"type":"diagnostic"}' >&2
exit 1
"""
STELE_STUB = """#!{bash}
printf 'stele %s\\n' "$*" >> "${PLANT_LOG}"
for ((i = 1; i <= $#; i++)); do
  [[ ${!i} != --scan ]] || {
    next=$((i + 1))
    cp "${!next}" "${PLANT_LOG}.scan"
  }
done
printf '%s' "${PLANT_REPORT}"
exit "${PLANT_STELE_RC}"
"""


def report(verdict: str, found: list[dict[str, str]]) -> str:
    """Spell stele's report for the stub to print.

    Returns:
        One JSON document of the schema stele_findings.py reads.

    """
    return json.dumps({
        "schema": 9,
        "target": "assert advisories",
        "subject": "Cargo.lock",
        "verdict": verdict,
        "findings": found,
    })


UNDECIDED = {
    "subject": "chrono@0.4.45",
    "assertion": "decided",
    "detail": "RUSTSEC-2026-0543 is undecided",
}


class AuditDeny(unittest.TestCase):
    """stele's status is the verdict; cargo-deny's is not read."""

    @override
    def setUp(self) -> None:
        """Plant a checkout, a canon with security/vex/, the stubs and tools."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "tree"
        self.root.mkdir()
        self.canon = self.tmp / "canon"
        (self.canon / "security" / "vex").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in {
            "cargo": CARGO_STUB,
            "cargo-deny": DENY_STUB,
            "stele": STELE_STUB,
        }.items():
            stub = self.bin / name
            stub.write_text(body.replace("{bash}", BASH), encoding="utf-8")
            stub.chmod(0o755)
        for tool in TOOLS:
            (self.bin / tool).symlink_to(hermetic.tool(tool))
        self.log = self.tmp / "calls.log"
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted checkout."""
        subprocess.run([GIT, *args], cwd=self.root, env=hermetic.env(), check=True)

    def plant(self) -> None:
        """Track a Cargo.lock and a deny.toml."""
        for name in ("Cargo.lock", "deny.toml"):
            (self.root / name).write_text("# planted\n", encoding="utf-8")
        self.git("add", "Cargo.lock", "deny.toml")

    def audit(
        self, *, rc: int = 0, verdict: str = "PASS", found: list[dict[str, str]] | None = None
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the task body with stele answering `verdict` and exiting `rc`.

        Returns:
            The finished process and the stubs' logged calls.

        """
        env = hermetic.env(
            PATH=str(self.bin),
            PLANT_LOG=str(self.log),
            PLANT_REPORT=report(verdict, found or []),
            PLANT_STELE_RC=str(rc),
            ORG_BELT_DIR=str(BELT),
            ORG_CANON_DIR=str(self.canon),
            ORG_PYTHON=sys.executable,
            ORG_PYTHON_UNSET="unset",
            ORG_FINDINGS="",
            MISE_TASK_NAME=TASK,
        )
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", task_body(TASK)],
            cwd=self.root,
            env=env,
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

    def stele_call(self, calls: list[str]) -> str:
        """Find the one stele call.

        Returns:
            Its arguments.

        """
        stele = [c.removeprefix("stele ") for c in calls if c.startswith("stele ")]
        self.assertEqual(len(stele), 1, calls)
        return stele[0]

    def test_a_decided_finding_is_green_though_cargo_deny_exits_1(self) -> None:
        """cargo-deny exits 1, stele exits 0: the task is green."""
        self.plant()
        done, calls = self.audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{TASK}: stele: PASS\n")
        self.assertEqual(
            calls[:3],
            [
                "cargo fetch --locked --quiet",
                "cargo deny --format json check advisories",
                "cargo-deny deny --format json check advisories",
            ],
        )

    def test_an_undecided_finding_is_red(self) -> None:
        """stele exits 1: the task is red, the finding named."""
        self.plant()
        done, _ = self.audit(rc=1, verdict="FAIL", found=[UNDECIDED])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"{TASK}: stele: FAIL\n")
        self.assertIn(
            f"{TASK}: chrono@0.4.45: decided: RUSTSEC-2026-0543 is undecided\n",
            done.stderr,
        )

    def test_stele_is_handed_the_scan_the_vex_and_the_lock(self) -> None:
        """The call names cargo-deny, the scan, the canon's security/vex, Cargo.lock."""
        self.plant()
        done, calls = self.audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        args = self.stele_call(calls).split(" ")
        self.assertEqual(args[:3], ["assert", "advisories", "--json"])
        self.assertEqual(args[args.index("--scanner") + 1], "cargo-deny")
        self.assertEqual(
            args[args.index("--vex") + 1], str(self.canon / "security" / "vex")
        )
        self.assertEqual(args[args.index("--subject") + 1], "Cargo.lock")
        scan = args[args.index("--scan") + 1]
        self.assertFalse(Path(scan).exists(), "the scan outlived the task")
        self.assertEqual(
            Path(f"{self.log}.scan").read_text(encoding="utf-8"),
            '{"type":"diagnostic"}\n',
        )

    def test_no_lock_prints_the_skip_line(self) -> None:
        """No tracked Cargo.lock: skipped, and nothing is run."""
        done, calls = self.audit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{TASK}: no Cargo.lock tracked, skipped\n")
        self.assertEqual(calls, [])

    def test_no_vex_directory_is_red_by_name(self) -> None:
        """The canon's decisions did not arrive: red before any scan."""
        self.plant()
        (self.canon / "security" / "vex").rmdir()
        done, calls = self.audit()
        self.assertEqual(done.returncode, 1)
        vex = self.canon / "security" / "vex"
        self.assertEqual(
            done.stderr,
            f"{TASK}: {vex} is not a directory, so the org's triage decisions cannot be read\n"
            "  the org belt did not arrive: CI sets MISE_GLOBAL_CONFIG_FILE,"
            " locally run: lefthook run post-checkout\n",
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

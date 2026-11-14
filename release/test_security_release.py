#!/usr/bin/env python3
"""Table tests for `release/security-release.sh`'s decision (#1167).

The script answers one question — should this repository release itself
to remedy an advisory? — as five smaller ones, and every "no" is a
sentence naming the question that failed. So every row here asserts THE
SENTENCE, never the bare `merge=false`: a row that declines for the
wrong reason must not pass for the right one.

WHAT IS REAL AND WHAT IS NOT. The script, `bash`, `jq`, `awk` and the
lockfile it parses are real. The four things it shells out to that
reach a network — `gh`, `stele assert blast-radius`, `mise run audit:*`
and `go list` — are stand-ins on PATH, because a decision table needs
inputs it can vary and the forge does not take requests.

The stand-in for `stele` replays REAL reports, not invented ones:
`testdata/security-release/report-affected.json` is the single-repository
blast-radius report for monumental-archive/stele on 2026-11-14 (supported
release v0.20.0, twenty undecided findings), and `report-clean.json` is
iiif-server's on the day v0.2.3 answered for v0.2.2. Both are the
engine's own output with the excused list dropped and, for the first,
the findings narrowed to the supported release. The facts the script
reads (`supportedReleases`, `supportedReleasesAffected`) and the finding
shape it parses are therefore the ones stele emits, not the ones this
file hoped it would.

The whole path was also run once against the live forge (stele's main,
its real Release PR, the real audits) before these rows were written;
that run is what found the missing-toolchain row below.

stdlib `unittest`, matching the other release tests. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import stat
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "release" / "security-release.sh"
DATA = CANON / "release" / "testdata" / "security-release"
BASH = shutil.which("bash")

AFFECTED = (DATA / "report-affected.json").read_text()
CLEAN = (DATA / "report-clean.json").read_text()

# What stele's main resolved on the day the affected report was taken:
# every flagged module bumped.
GO_FIXED = """\
github.com/klauspost/compress@v1.20.2
go.opentelemetry.io/otel/trace@v1.47.0
golang.org/x/crypto@v0.59.0
golang.org/x/net@v0.62.0
golang.org/x/text@v0.44.0
google.golang.org/grpc@v1.84.0
"""

# And what v0.20.0 itself resolved — a main that fixed nothing.
GO_UNFIXED = """\
github.com/klauspost/compress@v1.19.1
go.opentelemetry.io/otel/trace@v1.44.0
golang.org/x/crypto@v0.54.0
golang.org/x/net@v0.57.0
golang.org/x/text@v0.40.0
google.golang.org/grpc@v1.82.1
"""

PLAN = {
    "release": True,
    "version": "0.20.1",
    "bump": {"applied": "patch", "requested": "patch", "declared": False},
    "commit": {"subject": "chore: release v0.20.1"},
    "branch": {"name": "release/next"},
}

PR = {
    "number": 274,
    "title": "chore: release v0.20.1",
    "labels": [],
    "mergeStateStatus": "CLEAN",
}

# The real tools the script and the stand-ins call, linked one by one
# into a private directory that is the whole of PATH beside the
# stand-ins. Inheriting a system directory instead is what made the
# missing-toolchain row pass on a laptop and fail on its first runner:
# the runner ships a `go` in /usr/bin, so "no go on PATH" was a fact
# about the machine rather than about the row.
REAL_TOOLS = (
    "bash",
    "env",
    "jq",
    "awk",
    "sed",
    "grep",
    "tr",
    "sort",
    "cut",
    "cat",
    "cp",
    "rm",
    "mktemp",
    "head",
    "tail",
)

# One stand-in per external command. Each reads its script from files in
# $FAKE, so a row varies an input by writing a file, never by editing
# shell.
FAKES = {
    "gh": """\
        #!/usr/bin/env bash
        echo "gh $*" >> "${FAKE}/calls"
        if [[ -f ${FAKE}/gh.fail ]]; then exit 1; fi
        case "$1 $2" in
          "pr list") cat "${FAKE}/pr.json" ;;
          "issue list") cat "${FAKE}/issues-open" ;;
          "issue create") echo "https://example.invalid/issue/1" ;;
        esac
        """,
    "stele": """\
        #!/usr/bin/env bash
        while [[ $# -gt 0 ]]; do
          if [[ $1 == --policy ]]; then cp "$2" "${FAKE}/policy-seen.json"; fi
          shift
        done
        cat "${FAKE}/report.json"
        exit "$(cat "${FAKE}/stele.rc")"
        """,
    "mise": """\
        #!/usr/bin/env bash
        echo "mise $*" >> "${FAKE}/calls"
        if [[ -f ${FAKE}/fail-$2 ]]; then
          echo "$2: an advisory is in scope"
          exit 1
        fi
        """,
    "go": """\
        #!/usr/bin/env bash
        case "$1" in
          list) cat "${FAKE}/go-modules" ;;
          env) echo go1.27.4 ;;
        esac
        """,
}


class Repo:
    """A throwaway tree, the stand-ins, and the inputs a row may vary."""

    def __init__(self, root: Path) -> None:
        """Lay out the tree, the stand-ins and the passing case's inputs.

        Raises:
            FileNotFoundError: A real tool the script calls is not on
                PATH. Loud, never a skip: a skipped table reads as a
                passing one.

        """
        self.root = root
        self.tree = root / "tree"
        self.fake = root / "fake"
        self.bin = root / "bin"
        self.tools = root / "tools"
        self.canon = root / "canon"
        vex = self.canon / "security" / "vex"
        for d in (self.tree, self.fake, self.bin, self.tools, self.canon / "slsa", vex):
            d.mkdir(parents=True)
        for tool in REAL_TOOLS:
            found = shutil.which(tool)
            if found is None:
                message = f"{tool} is not on PATH, and the script under test calls it"
                raise FileNotFoundError(message)
            (self.tools / tool).symlink_to(found)
        for name, body in FAKES.items():
            path = self.bin / name
            path.write_text(textwrap.dedent(body))
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        (self.canon / "slsa" / "assert-policy.json").write_text(
            json.dumps({
                "blastRadius": {
                    "supported": "latest-release",
                    "canary": {
                        "repo": "release-lab",
                        "tag": "v0.17.0",
                        "advisory": "RUSTSEC-2021-0127",
                    },
                }
            })
        )
        # The passing case: a patch release, its own clean PR, an affected
        # supported release, a main that fixed it, audits green.
        self.plan(PLAN)
        self.pr(PR)
        self.report(AFFECTED, 1)
        (self.fake / "issues-open").write_text("0\n")
        (self.fake / "go-modules").write_text(GO_FIXED)
        (self.tree / "go.mod").write_text("module example.invalid/stele\n")

    def plan(self, doc: dict) -> None:
        """Write the release plan prepare-release.sh would have."""
        (self.root / "plan.json").write_text(json.dumps(doc))

    def pr(self, doc: dict | None) -> None:
        """Script what `gh pr list` answers; None is no open Release PR."""
        (self.fake / "pr.json").write_text(json.dumps(doc) if doc else "")

    def report(self, body: str, rc: int) -> None:
        """Script stele's report document and its exit status."""
        (self.fake / "report.json").write_text(body)
        (self.fake / "stele.rc").write_text(f"{rc}\n")

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        """Run the real script in the tree, with only the inputs named here.

        Returns:
            The finished process: its exit status, stdout and stderr.

        """
        out = self.root / "output"
        out.write_text("")
        # The stand-ins, then the linked real tools, and nothing else:
        # whether `go` exists is a row's decision, never the machine's.
        path = os.pathsep.join([str(self.bin), str(self.tools)])
        env = {
            "PATH": path,
            "HOME": str(self.root),
            "FAKE": str(self.fake),
            "PLAN": str(self.root / "plan.json"),
            "GITHUB_REPOSITORY": "monumental-archive/stele",
            "GITHUB_OUTPUT": str(out),
            "GH_TOKEN": "not-a-token",
            "ORG_CANON_DIR": str(self.canon),
        }
        env.update(overrides)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def outputs(self) -> dict[str, str]:
        """Read back the step outputs a workflow would see.

        Returns:
            Every `key=value` the script wrote to GITHUB_OUTPUT.

        """
        lines = (self.root / "output").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def calls(self) -> str:
        """Read the stand-ins' call log.

        Returns:
            Every `gh` and `mise` invocation the script made, in order.

        """
        log = self.fake / "calls"
        return log.read_text() if log.exists() else ""


NOTHING_REMEDIED = (
    "main still resolves every affected package of stele@v0.20.0 at its"
    " affected version, so a release would remedy nothing"
)
ISSUE_TITLE = "the supported release carries an advisory and main does not remedy it"


class DecisionCase(unittest.TestCase):
    """A fresh passing case per row; each row breaks exactly one input."""

    def setUp(self) -> None:
        """Build the passing case."""
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repo(Path(self._tmp.name))

    def declines(self, reason: str) -> None:
        """Run, and require a clean 'no' carrying exactly this sentence."""
        done = self.repo.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        got = self.repo.outputs()
        self.assertEqual(got.get("merge"), "false", done.stdout)
        self.assertEqual(got.get("reason"), reason)
        self.assertNotIn("pr", got)


class TheYes(DecisionCase):
    """The one path that ends in a merge, and what it took to get there."""

    def test_an_affected_release_that_main_remedies_releases(self) -> None:
        """The real report against the real main: twenty remedied, a yes."""
        done = self.repo.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self.repo.outputs(),
            {
                "merge": "true",
                "pr": "274",
                "reason": "stele@v0.20.0 is affected and main remedies it",
            },
        )
        # All twenty findings of the real report are remedied by the real
        # main, and the report says which.
        self.assertIn("GO-2026-6603:golang.org/x/net@v0.57.0", done.stdout)
        self.assertEqual(done.stdout.count("\n  G"), 20, done.stdout)
        # Both publish-path checks ran, on this tree, before the yes.
        self.assertIn("mise run audit:deny", self.repo.calls())
        self.assertIn("mise run audit:go-vulns", self.repo.calls())

    def test_the_org_canary_is_not_asked_of_one_repository(self) -> None:
        """The canary lives in another repository's history; it is dropped."""
        self.repo.run()
        seen = json.loads((self.repo.fake / "policy-seen.json").read_text())
        self.assertNotIn("canary", seen["blastRadius"])
        self.assertEqual(seen["blastRadius"]["supported"], "latest-release")


class TheReleaseIsNotOneToCut(DecisionCase):
    """Questions 1 and 2: the pending release and its pull request."""

    def test_nothing_to_release(self) -> None:
        """A plan that releases nothing ends the run."""
        self.repo.plan({"release": False})
        self.declines(
            "nothing to release: main carries no releasable change since the last tag"
        )

    def test_a_breaking_release_waits_for_a_maintainer(self) -> None:
        """Stele's own v0.21.0: 0.x applied a minor bump, the range asked major."""
        bump = {"applied": "minor", "requested": "major", "declared": False}
        self.repo.plan({**PLAN, "bump": bump})
        self.declines(
            "the pending release carries a breaking change,"
            " which waits for a maintainer"
        )

    def test_no_release_pr(self) -> None:
        """No open pull request on the plan's branch."""
        self.repo.pr(None)
        self.declines("no open Release PR on release/next")

    def test_a_release_pr_for_another_version_is_stale(self) -> None:
        """A PR not yet refreshed to the release main describes is left."""
        self.repo.pr({**PR, "title": "chore: release v0.20.0"})
        self.declines(
            "Release PR #274 is not the release main now describes"
            " (chore: release v0.20.1) — it has not been refreshed yet"
        )

    def test_the_hold_label_is_a_veto(self) -> None:
        """The maintainer's stop, among other labels."""
        self.repo.pr({**PR, "labels": [{"name": "dependencies"}, {"name": "hold"}]})
        self.declines("Release PR #274 carries the hold label")

    def test_a_release_pr_that_is_not_clean_is_left_alone(self) -> None:
        """Mergeable NOW, or not at all — nothing is armed for later."""
        self.repo.pr({**PR, "mergeStateStatus": "BLOCKED"})
        self.declines("Release PR #274 is not mergeable as it stands (BLOCKED)")


class TheSupportedReleaseIsNotAffected(DecisionCase):
    """Question 3: what was published, as the engine states it."""

    def test_a_clean_supported_release_needs_no_release(self) -> None:
        """iiif-server on the day v0.2.3 answered for v0.2.2."""
        self.repo.report(CLEAN, 0)
        # The clean report is iiif-server's; ask as that repository.
        done = self.repo.run(GITHUB_REPOSITORY="monumental-archive/iiif-server")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self.repo.outputs().get("reason"),
            "the supported release iiif-server@v0.2.3 carries no undecided advisory",
        )

    def test_no_support_window_declared(self) -> None:
        """A policy with no window yields no fact, and the run says so."""
        bare = json.loads(AFFECTED)
        bare["facts"] = []
        self.repo.report(json.dumps(bare), 1)
        self.declines(
            "no supported release could be named for stele:"
            " the policy declares no support window"
        )

    def test_a_withheld_window_is_said_in_the_engines_words(self) -> None:
        """A newest release not read whole: stele's reason is passed on."""
        withheld = json.loads(AFFECTED)
        why = "stele@v0.20.0(not read whole)"
        withheld["facts"] = [{"name": "supersessionWithheld", "value": why}]
        self.repo.report(json.dumps(withheld), 1)
        self.declines(f"no supported release could be named for stele: {why}")

    def test_a_scan_that_cannot_judge_releases_nothing(self) -> None:
        """CANNOT_JUDGE is a no, not an error and not a yes."""
        self.repo.report(AFFECTED, 4)
        self.declines("the published SBOMs could not be judged (stele: CANNOT_JUDGE)")


class MainDoesNotRemedyIt(DecisionCase):
    """Questions 4 and 5: would a release fix anything, and survive it."""

    def test_a_main_that_fixed_nothing_needs_a_human(self) -> None:
        """v0.20.0's own module versions on main: a release changes nothing."""
        (self.repo.fake / "go-modules").write_text(GO_UNFIXED)
        self.declines(NOTHING_REMEDIED)
        # Not asked to file, so nothing was filed and no audit was run.
        self.assertNotIn("issue create", self.repo.calls())
        self.assertNotIn("mise run", self.repo.calls())

    def test_one_remedied_package_is_enough(self) -> None:
        """Everything unfixed but grpc: a release remedies something."""
        partly = GO_UNFIXED.replace("grpc@v1.82.1", "grpc@v1.84.0")
        (self.repo.fake / "go-modules").write_text(partly)
        done = self.repo.run()
        self.assertEqual(self.repo.outputs().get("merge"), "true", done.stdout)
        self.assertIn("GO-2026-6443:google.golang.org/grpc@v1.82.1", done.stdout)
        self.assertNotIn("golang.org/x/net@v0.57.0", done.stdout)

    def test_a_tree_that_would_refuse_its_own_publish_is_not_tagged(self) -> None:
        """Stele v0.17.0's burn, caught before the version number is spent."""
        (self.repo.fake / "fail-audit:go-vulns").write_text("")
        self.declines(
            "main would refuse its own publish:"
            " audit:go-vulns fails on the tree being released"
        )

    def test_the_issue_is_filed_once(self) -> None:
        """Filed when asked and none is open; never a second time."""
        (self.repo.fake / "go-modules").write_text(GO_UNFIXED)
        self.repo.run(FILE_ISSUE="true")
        self.assertEqual(self.repo.calls().count("gh issue create"), 1)
        self.assertIn(ISSUE_TITLE, self.repo.calls())
        # An open one already says it.
        (self.repo.fake / "issues-open").write_text("1\n")
        (self.repo.fake / "calls").write_text("")
        self.repo.run(FILE_ISSUE="true")
        self.assertNotIn("gh issue create", self.repo.calls())

    def test_findings_only_in_an_ecosystem_this_tree_does_not_resolve(self) -> None:
        """An image red only in its OS layer: the rebuild is the remedy."""
        os_only = json.loads(AFFECTED)
        for finding in os_only["findings"]:
            finding["assertion"] = "CVE-2026-0001:libssl3@3.5.1-1"
            finding["detail"] = "CVE-2026-0001 affects libssl3@3.5.1-1 (Debian:13)"
        self.repo.report(json.dumps(os_only), 1)
        done = self.repo.run()
        self.assertEqual(self.repo.outputs().get("merge"), "true", done.stdout)
        self.assertIn("a rebuild is the remedy", done.stdout)

    def test_a_cargo_lock_is_read_for_crates(self) -> None:
        """The same question asked of a Rust tree, both answers."""
        crates = json.loads(AFFECTED)
        crates["findings"] = [
            {
                "subject": "stele@v0.20.0",
                "assertion": "RUSTSEC-2026-0386:tokio@1.53.1",
                "detail": "RUSTSEC-2026-0386 affects tokio@1.53.1 (crates.io)",
            }
        ]
        self.repo.report(json.dumps(crates), 1)
        (self.repo.tree / "go.mod").unlink()
        lock = '[[package]]\nname = "tokio"\nversion = "{}"\nsource = "registry"\n'
        (self.repo.tree / "Cargo.lock").write_text(lock.format("1.53.1"))
        self.declines(NOTHING_REMEDIED)
        (self.repo.tree / "Cargo.lock").write_text(lock.format("1.54.1"))
        done = self.repo.run()
        self.assertEqual(self.repo.outputs().get("merge"), "true", done.stdout)

    def test_a_module_with_no_toolchain_is_loud(self) -> None:
        """Found by the live run.

        With no `go`, every Go finding compared against nothing and the
        answer was "main remedies nothing" about a tree nobody had
        looked at.
        """
        (self.repo.bin / "go").unlink()
        done = self.repo.run()
        self.assertEqual(done.returncode, 1)
        self.assertIn("go.mod present but go missing", done.stderr)
        self.assertNotIn("merge", self.repo.outputs())

    def test_a_forge_that_cannot_be_read_is_not_an_absent_pr(self) -> None:
        """A failed read reds the run; it is never read as "no PR"."""
        (self.repo.fake / "gh.fail").write_text("")
        done = self.repo.run()
        self.assertEqual(done.returncode, 1)
        self.assertIn("could not read this repository's pull requests", done.stderr)
        self.assertNotIn("merge", self.repo.outputs())

    def test_an_engine_that_crashes_is_not_a_verdict(self) -> None:
        """Any exit but PASS, FAIL or CANNOT_JUDGE reds the run."""
        self.repo.report("", 2)
        done = self.repo.run()
        self.assertEqual(done.returncode, 1)
        self.assertIn("stele assert blast-radius exited 2", done.stderr)


if __name__ == "__main__":
    unittest.main()

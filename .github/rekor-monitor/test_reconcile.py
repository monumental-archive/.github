#!/usr/bin/env python3
"""reconcile.py against two real signer certificates (#1250).

Captured 2026-11-15, nothing hand-written:
- testdata/ct-443908396.json, the CT log's get-entries answer for the
  certificate behind canon v1.67.0 (release run 43671235061, the chain
  link is publish.yml);
- testdata/ct-335460347.json, the certificate of the canon's VEX
  signing on 2026-08-11 (run 31536050844, vex-attest.yml: a top-level
  canon workflow that calls the signer itself);
- testdata/forge-2026-11-15.json, every forge answer the reconciliation
  of those two read, with each file read stored under testdata/contents/.

Each failing row changes one claim or one forge answer and asserts the
condition it fails, by name and by message.
"""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "testdata"
_SPEC = importlib.util.spec_from_file_location("reconcile", HERE / "reconcile.py")
rc = importlib.util.module_from_spec(_SPEC)
sys.modules["reconcile"] = rc
_SPEC.loader.exec_module(rc)

CANON = "monumental-archive/.github"
SIGNER_REPO = "monumental-archive/signer"
SIGN = f"{SIGNER_REPO}/.github/workflows/sign.yml"
RELEASE = 443908396
RELEASE_RUN = "43671235061"
RELEASE_SIGNER = "cffec9441210d46f237099d8785098aacf5b8273"
RELEASE_SHA = "ebce3d46ea3130824b827cbbe383e16dfc64b17c"
VEX = 335460347
VEX_RUN = "31536050844"
VEX_SIGNER = "e50552beb52c8220c470e15702310561d7a3cfb1"
VEX_SHA = "cabe764282864c0724672b58dd899b423a120368"
OTHER = "0123456789abcdef0123456789abcdef01234567"

ENTRIES = {
    i: json.loads((DATA / f"ct-{i}.json").read_text(encoding="utf-8"))
    for i in (RELEASE, VEX)
}
CAPTURE = json.loads((DATA / "forge-2026-11-15.json").read_text(encoding="utf-8"))


def claims_of(index: int) -> dict[str, str]:
    """Decode a captured CT entry.

    Returns:
        The claims reconcile.py reads from that entry.

    """
    leaf = base64.b64decode(ENTRIES[index]["entries"][0]["leaf_input"])
    return rc.tbs_claims(rc.leaf_tbs(leaf))


def run_path(run_id: str, repo: str = CANON) -> str:
    """Build a run's API path.

    Returns:
        The path the forge is asked for, without the API host.

    """
    return f"/repos/{repo}/actions/runs/{run_id}"


def run_uri(repo: str, run_id: str) -> str:
    """Build a Run Invocation URI as Fulcio writes it.

    Returns:
        The URI of the run's first attempt.

    """
    return f"https://github.com/{repo}/actions/runs/{run_id}/attempts/1"


class UnexpectedReadError(AssertionError):
    """The code under test read something the capture does not hold."""


class FakeForge:
    """The captured answers, keyed by API path; each row edits a copy."""

    def __init__(self) -> None:
        """Start from the capture, unedited."""
        self.answers = copy.deepcopy(CAPTURE)

    def edit_run(self, run_id: str) -> dict:
        """Open a captured run for editing.

        Returns:
            The run object this fake will answer with.

        """
        return self.answers[run_path(run_id)][1]

    def put_file(self, repo: str, path: str, sha: str, text: str) -> None:
        """Answer a contents read with this text."""
        key = f"/repos/{repo}/contents/{path}?ref={sha}"
        self.answers[key] = [200, {"text": text}]

    def __call__(self, url: str, _token: str | None = None) -> tuple[int, object]:
        """Answer one GET.

        Returns:
            The status and body the forge or the log would return.

        Raises:
            UnexpectedReadError: a forge read the capture does not hold.

        """
        if url.startswith(rc.CT_LOG):
            index = int(url.split("start=")[1].split("&", maxsplit=1)[0])
            return 200, ENTRIES[index]
        path = url.removeprefix(rc.API)
        if path not in self.answers:
            if "/actions/runs/" in path:
                return 404, None
            if "/compare/" in path:
                return 200, {"status": "diverged"}
            raise UnexpectedReadError(path)
        status, body = self.answers[path]
        if isinstance(body, dict) and "file" in body:
            body = {"text": (DATA / body["file"]).read_text(encoding="utf-8")}
        if isinstance(body, dict) and "text" in body:
            encoded = base64.b64encode(body["text"].encode()).decode()
            body = {"encoding": "base64", "content": encoded}
        return status, copy.deepcopy(body)


def reconcile(index: int, claims: dict[str, str], fake: FakeForge) -> rc.Verdict:
    """Reconcile one certificate against a fake forge.

    Returns:
        The verdict.

    """
    members = rc.roster(ROOT, "monumental-archive")
    return rc.reconcile(index, claims, rc.Forge(None, get=fake), members, CANON)


class Decode(unittest.TestCase):
    """The claims read from the two real CT entries."""

    def test_release_certificate_claims(self) -> None:
        """Every claim of the v1.67.0 certificate, as Fulcio wrote it."""
        claims = claims_of(RELEASE)
        self.assertEqual(claims["san"], f"https://github.com/{SIGN}@{RELEASE_SIGNER}")
        self.assertEqual(claims["build_signer_digest"], RELEASE_SIGNER)
        self.assertEqual(
            claims["issuer"], "https://token.actions.githubusercontent.com"
        )
        self.assertEqual(claims["source_repository_uri"], f"https://github.com/{CANON}")
        self.assertEqual(claims["source_repository_ref"], "refs/tags/v1.67.0")
        self.assertEqual(
            claims["build_config_uri"],
            f"https://github.com/{CANON}/.github/workflows/self-publish.yml"
            "@refs/tags/v1.67.0",
        )
        self.assertEqual(claims["run_invocation_uri"], run_uri(CANON, RELEASE_RUN))

    def test_vex_certificate_claims(self) -> None:
        """The VEX certificate names vex-attest.yml and its own run."""
        claims = claims_of(VEX)
        self.assertEqual(claims["build_signer_digest"], VEX_SIGNER)
        self.assertEqual(
            claims["build_config_uri"],
            f"https://github.com/{CANON}/.github/workflows/vex-attest.yml"
            "@refs/heads/main",
        )
        self.assertEqual(claims["run_invocation_uri"], run_uri(CANON, VEX_RUN))

    def test_found_lines(self) -> None:
        """Each index once, ascending, from the monitor's Found lines."""
        log = (
            "Monitoring: x\nFound https://a https://b 7\n"
            "Found https://a https://b 3\nFound https://a https://b 7\n"
        )
        self.assertEqual(rc.found_indexes(log), [3, 7])


class Passing(unittest.TestCase):
    """Both real signings reconcile, each through its own chain link."""

    def test_release_reconciles_through_publish(self) -> None:
        """The release run's link is publish.yml at the release commit."""
        verdict = reconcile(RELEASE, claims_of(RELEASE), FakeForge())
        self.assertTrue(verdict.reconciled, verdict.detail)
        self.assertEqual(
            verdict.detail,
            [
                (
                    f"run https://github.com/{CANON}/actions/runs/{RELEASE_RUN}"
                    " (.github/workflows/self-publish.yml, push)"
                ),
                (
                    f"chain {CANON}/.github/workflows/publish.yml@{RELEASE_SHA[:12]}"
                    f" calls sign.yml@{RELEASE_SIGNER[:12]}"
                ),
                f"signer {RELEASE_SIGNER[:12]} on default branch",
            ],
        )

    def test_vex_reconciles_through_its_own_top_level_workflow(self) -> None:
        """The VEX run's link is its own top-level vex-attest.yml."""
        verdict = reconcile(VEX, claims_of(VEX), FakeForge())
        self.assertTrue(verdict.reconciled, verdict.detail)
        self.assertEqual(
            verdict.detail,
            [
                (
                    f"run https://github.com/{CANON}/actions/runs/{VEX_RUN}"
                    " (.github/workflows/vex-attest.yml, push)"
                ),
                (
                    f"chain {CANON}/.github/workflows/vex-attest.yml@{VEX_SHA[:12]}"
                    f" calls sign.yml@{VEX_SIGNER[:12]}"
                ),
                f"signer {VEX_SIGNER[:12]} on default branch",
            ],
        )


class Failing(unittest.TestCase):
    """One claim or forge answer changed per row; each names its condition."""

    def assert_fails(
        self, verdict: rc.Verdict, index: int, condition: str, message: str
    ) -> None:
        """Assert exactly one failed condition, its message, and its issue."""
        self.assertEqual(verdict.failed, [condition], verdict.detail)
        self.assertTrue(
            any(
                d.startswith(f"{condition}: ") and message in d for d in verdict.detail
            ),
            verdict.detail,
        )
        issue = verdict.issue()
        self.assertEqual(
            issue["title"],
            f"rekor-monitor: certificate at CT index {index}"
            f" did not reconcile ({condition})",
        )
        self.assertEqual(issue["key"], f"CT index {index}")
        self.assertIn(f"- **{condition}** ({rc.CONDITIONS[condition]})", issue["body"])
        self.assertIn(message, issue["body"])
        self.assertIn(f"- subject: https://github.com/{SIGN}@", issue["body"])

    def test_run_that_does_not_exist(self) -> None:
        """A run id the forge answers 404 for."""
        claims = claims_of(RELEASE)
        claims["run_invocation_uri"] = run_uri(CANON, "1")
        verdict = reconcile(RELEASE, claims, FakeForge())
        self.assert_fails(verdict, RELEASE, "run", f"run 1 does not exist in {CANON}")

    def test_repository_outside_the_roster(self) -> None:
        """A run in a repository the roster does not name."""
        claims = claims_of(RELEASE)
        claims["run_invocation_uri"] = run_uri("someone-else/fork", RELEASE_RUN)
        self.assert_fails(
            reconcile(RELEASE, claims, FakeForge()),
            RELEASE,
            "run",
            "someone-else/fork is not a repository of the roster",
        )

    def test_foreign_reusable_workflow(self) -> None:
        """A reusable workflow from outside the canon in the signing run."""
        fake = FakeForge()
        fake.edit_run(RELEASE_RUN)["referenced_workflows"].append({
            "path": f"someone-else/tools/.github/workflows/x.yml@{OTHER}",
            "ref": "refs/heads/main",
            "sha": OTHER,
        })
        self.assert_fails(
            reconcile(RELEASE, claims_of(RELEASE), fake),
            RELEASE,
            "foreign",
            f"the run called someone-else/tools/.github/workflows/x.yml@{OTHER},"
            " which is neither the canon's nor the signer's",
        )

    def test_signer_called_at_another_digest(self) -> None:
        """The run called sign.yml, but not at the certificate's digest."""
        fake = FakeForge()
        for w in fake.edit_run(RELEASE_RUN)["referenced_workflows"]:
            if w["path"].startswith(f"{SIGNER_REPO}/"):
                w["path"] = w["path"].replace(RELEASE_SIGNER, OTHER)
                w["sha"] = OTHER
        self.assert_fails(
            reconcile(RELEASE, claims_of(RELEASE), fake),
            RELEASE,
            "signer-in-run",
            f"the run called sign.yml at ['{OTHER}'],"
            f" the certificate says {RELEASE_SIGNER}",
        )

    def test_canon_workflow_off_the_default_branch(self) -> None:
        """The canon workflows the run called sit on an unmerged commit."""
        fake = FakeForge()
        compare = f"/repos/{CANON}/compare/main...{RELEASE_SHA}"
        fake.answers[compare] = [200, {"status": "diverged"}]
        self.assert_fails(
            reconcile(RELEASE, claims_of(RELEASE), fake),
            RELEASE,
            "canon-branch",
            f"{CANON}/.github/workflows/publish.yml at {RELEASE_SHA}"
            f" is not on {CANON}'s default branch",
        )

    def test_no_candidate_pins_the_digest(self) -> None:
        """No canon file the run used asks for the certificate's signer."""
        fake = FakeForge()
        path = ".github/workflows/publish.yml"
        stored = fake.answers[f"/repos/{CANON}/contents/{path}?ref={RELEASE_SHA}"]
        text = (DATA / stored[1]["file"]).read_text(encoding="utf-8")
        fake.put_file(CANON, path, RELEASE_SHA, text.replace(RELEASE_SIGNER, OTHER))
        self.assert_fails(
            reconcile(RELEASE, claims_of(RELEASE), fake),
            RELEASE,
            "chain",
            f"no canon file the run used calls sign.yml@{RELEASE_SIGNER}",
        )

    def test_non_canon_top_level_calls_the_signer(self) -> None:
        """A repository other than the canon calls the signer itself."""
        fake = FakeForge()
        repo = "monumental-archive/stele"
        run = copy.deepcopy(fake.edit_run(RELEASE_RUN))
        run["repository"]["full_name"] = repo
        run["html_url"] = f"https://github.com/{repo}/actions/runs/{RELEASE_RUN}"
        run["path"] = ".github/workflows/release.yml"
        fake.answers[run_path(RELEASE_RUN, repo)] = [200, run]
        fake.put_file(
            repo,
            run["path"],
            run["head_sha"],
            f"jobs:\n  sign:\n    uses: {SIGN}@{RELEASE_SIGNER}\n",
        )
        claims = claims_of(RELEASE)
        claims["run_invocation_uri"] = run_uri(repo, RELEASE_RUN)
        self.assert_fails(
            reconcile(RELEASE, claims, fake),
            RELEASE,
            "direct",
            f"{repo}'s .github/workflows/release.yml at {RELEASE_SHA}"
            " calls the signer itself",
        )

    def test_canon_top_level_run_off_the_default_branch(self) -> None:
        """A vex-attest.yml-shaped run whose head is not on the default branch."""
        fake = FakeForge()
        compare = f"/repos/{CANON}/compare/main...{VEX_SHA}"
        fake.answers[compare] = [200, {"status": "diverged"}]
        self.assert_fails(
            reconcile(VEX, claims_of(VEX), fake),
            VEX,
            "canon-branch",
            f"{CANON}/.github/workflows/vex-attest.yml at {VEX_SHA}"
            f" is not on {CANON}'s default branch",
        )

    def test_signer_off_its_default_branch(self) -> None:
        """The signer commit is not on the signer's default branch."""
        fake = FakeForge()
        compare = f"/repos/{SIGNER_REPO}/compare/main...{RELEASE_SIGNER}"
        fake.answers[compare] = [200, {"status": "diverged"}]
        self.assert_fails(
            reconcile(RELEASE, claims_of(RELEASE), fake),
            RELEASE,
            "signer-branch",
            f"signer commit {RELEASE_SIGNER} is not on {SIGNER_REPO}'s default branch",
        )


def run_main(fake: FakeForge, *indexes: int) -> tuple[int, str, Path, Path]:
    """Run the CLI against a fake forge.

    Returns:
        The exit code, the printed output, and the findings and summary
        paths it was given.

    """
    tmp = Path(tempfile.mkdtemp())
    findings, summary = tmp / "findings.json", tmp / "summary.md"
    argv = [a for i in indexes for a in ("--index", str(i))]
    argv += ["--root", str(ROOT), "--findings", str(findings)]
    argv += ["--summary", str(summary)]
    out = io.StringIO()
    with mock.patch.object(rc, "http_json", fake), redirect_stdout(out):
        code = rc.main(argv)
    return code, out.getvalue(), findings, summary


class Cli(unittest.TestCase):
    """The step's contract: exit code, findings file, run summary."""

    def test_forge_403_fails_the_job_and_reconciles_nothing(self) -> None:
        """A refused read is an error, never a reconciliation or a finding."""
        fake = FakeForge()
        fake.answers[run_path(RELEASE_RUN)] = [403, None]
        with self.assertRaises(rc.ReadError) as raised:
            reconcile(RELEASE, claims_of(RELEASE), fake)
        self.assertIn(
            f"GET runs/{RELEASE_RUN} in {CANON}: HTTP 403", str(raised.exception)
        )

        code, out, findings, summary = run_main(fake, VEX, RELEASE)
        self.assertEqual(code, 2)
        self.assertIn("::error::reconcile could not complete a read", out)
        self.assertIn("HTTP 403", out)
        self.assertFalse(findings.exists())
        self.assertFalse(summary.exists())

    def test_summary_names_both_real_signings(self) -> None:
        """Each reconciled certificate is one summary line naming its run."""
        code, out, findings, summary = run_main(FakeForge(), RELEASE, VEX)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(findings.read_text(encoding="utf-8")), [])
        text = summary.read_text(encoding="utf-8")
        self.assertIn("### Signer certificates: 2 found, 2 reconciled", text)
        for index, run_id in ((RELEASE, RELEASE_RUN), (VEX, VEX_RUN)):
            self.assertIn(
                f"- reconciled CT index {index}:"
                f" run https://github.com/{CANON}/actions/runs/{run_id}",
                text,
            )


if __name__ == "__main__":
    unittest.main()

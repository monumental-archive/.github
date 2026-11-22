#!/usr/bin/env python3
"""Table tests for audit:actions' signer filter (#1152, #1340).

Two fixtures, both zizmor 1.32.0's own `--format=json
--persona=auditor` output, run online on the canon's
`.github/workflows/`, unedited:

- `zizmor-1.32.0-canon.json`, 2026-11-15: the ten `stale-action-refs`
  findings on the signer.
- `zizmor-1.32.0-ref-version-mismatch.json`, 2026-11-17: the first of
  the ten `ref-version-mismatch` findings on the canon at `af30901`
  (`4564ce7^`, before #1345 moved the signer pin), which pins the
  signer at `0d6c6e2` while the signer's `main` was `b746956`.

Every other row is derived from one of those real findings by changing
one field, so each kept or dropped case is planted alone, not invented.
Those rows run `main` with `run_zizmor` answering the array (#1955).

`Records` asserts what remains is recorded, read back from
ORG_FINDINGS: subject the first location's file, message `<ident>:
<the first line of the first Primary location's feature>` (the
owner's ruling on #1955; Hidden and Related are context), detail
`desc`, the severity and each location with its line; the token and
offline-degradation findings; zizmor's status as the exit.
`PinnedZizmor` runs the belt's zizmor `--offline` on a planted
workflow, and two unpinned `uses:` in one file are two records with
two messages. `Task` runs `audit:actions`' own body over a stub zizmor:
one run, the record, zizmor's 11; a clean run, 0. Against
`origin/main` the `Records` and `Task` record rows fail.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import override
from unittest import mock

import decoded
import hermetic
import zizmor_signer as signer

HERE = Path(__file__).resolve().parent


def fixture(name: str) -> list[dict[str, object]]:
    """Read a recorded zizmor run: its finding array, each an object.

    Returns:
        The findings, as zizmor wrote them.

    """
    text = (HERE / "testdata" / name).read_text()
    found = decoded.as_array(decoded.load_json(text), name)
    return [decoded.as_object(f, f"{name}[{i}]") for i, f in enumerate(found)]


def locations(finding: dict[str, object]) -> list[dict[str, object]]:
    """Read a finding's locations, each an object.

    Returns:
        The locations themselves, so a change to one changes the finding.

    """
    found = decoded.as_array(finding["locations"], "locations")
    return [decoded.as_object(loc, f"locations[{i}]") for i, loc in enumerate(found)]


def symbolic(location: dict[str, object]) -> dict[str, object]:
    """Read a location's `symbolic` half.

    Returns:
        The object itself.

    """
    return decoded.as_object(location["symbolic"], "symbolic")


def concrete(location: dict[str, object]) -> dict[str, object]:
    """Read a location's `concrete` half.

    Returns:
        The object itself.

    """
    return decoded.as_object(location["concrete"], "concrete")


REAL = fixture("zizmor-1.32.0-canon.json")
MISMATCH = fixture("zizmor-1.32.0-ref-version-mismatch.json")
SIGNER_WORKFLOW = "monumental-archive/signer/.github/workflows/sign.yml"
SIGNER_REF = SIGNER_WORKFLOW + "@cffec9441210d46f237099d8785098aacf5b8273"
MISMATCH_REF = SIGNER_WORKFLOW + "@0d6c6e297684c8ba401fb9c212532628fe552148"
TASK = "audit:actions"
ARGS = ["--persona=auditor", ".github/workflows/"]
# A GH_TOKEN value: the rows never reach the API.
STANDIN = "a token"


def run(
    findings: object,
    workflow: str = SIGNER_WORKFLOW,
    *,
    status: int | None = None,
) -> tuple[int, str, str]:
    """Run the CLI with zizmor answering a finding array.

    zizmor's status is 14 for any answer but an empty array, unless the
    row names one.

    Returns:
        The exit status, what it printed, and what it printed to stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    code = status if status is not None else (14 if findings != [] else 0)
    ran = signer.Ran(code, json.dumps(findings).encode(), b"")
    env = {"GH_TOKEN": "a token", "MISE_TASK_NAME": TASK, "ORG_FINDINGS": ""}
    with (
        mock.patch.object(signer, "run_zizmor", return_value=ran),
        mock.patch.dict(os.environ, env),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = signer.main(["--signer-workflow", workflow, *ARGS])
    return code, out.getvalue(), err.getvalue()


def repointed(finding: dict[str, object], reference: str) -> dict[str, object]:
    """Return a copy of a real finding whose Primary reference is changed.

    Returns:
        The finding with every Primary location's feature replaced.

    """
    changed = copy.deepcopy(finding)
    for location in locations(changed):
        if symbolic(location)["kind"] == "Primary":
            concrete(location)["feature"] = reference
    return changed


def rewritten(
    finding: dict[str, object],
    kind: str,
    old: str,
    new: str,
) -> dict[str, object]:
    """Return a copy of a real finding with text replaced in one location kind.

    Returns:
        The finding with `old` replaced by `new` in the feature of every
        location of that kind.

    """
    changed = copy.deepcopy(finding)
    for location in locations(changed):
        if symbolic(location)["kind"] == kind:
            feature = decoded.as_str(concrete(location)["feature"], "feature")
            concrete(location)["feature"] = feature.replace(old, new)
    return changed


class RealCanonOutput(unittest.TestCase):
    """The canon's own zizmor run, as it came."""

    def test_the_fixture_is_the_ten_signer_findings(self) -> None:
        """The fixture is what the issue measured: ten, one rule, one ref."""
        self.assertEqual(10, len(REAL))
        idents: set[object] = {f["ident"] for f in REAL}
        rules: set[object] = {"stale-action-refs"}
        self.assertEqual(rules, idents)
        refs = {r for f in REAL for r in signer.references(f)}
        want: set[str] = {SIGNER_REF}
        self.assertEqual(want, refs)

    def test_all_ten_are_dropped_and_counted(self) -> None:
        """The canon goes green, and says what it dropped."""
        status, out, err = run(REAL)
        self.assertEqual(0, status, err)
        self.assertIn("10 stale-action-refs finding(s) on the signer dropped", out)
        self.assertEqual("", err)


class RefVersionMismatch(unittest.TestCase):
    """The signer's `# main` pin behind `main` (#1340), as zizmor wrote it."""

    def test_the_fixture_is_one_signer_mismatch(self) -> None:
        """The fixture is the measured shape: the reference rides the Hidden job."""
        self.assertEqual(1, len(MISMATCH))
        self.assertEqual("ref-version-mismatch", MISMATCH[0]["ident"])
        primary = [
            concrete(loc)["feature"]
            for loc in locations(MISMATCH[0])
            if symbolic(loc)["kind"] == "Primary"
        ]
        feature: list[object] = ["main"]
        self.assertEqual(feature, primary)
        want: list[str] = [MISMATCH_REF]
        self.assertEqual(want, signer.references(MISMATCH[0]))

    def test_the_signer_mismatch_is_dropped_and_counted(self) -> None:
        """The signer's `# main` behind its branch head goes green, said aloud."""
        status, out, err = run(MISMATCH)
        self.assertEqual(0, status, err)
        self.assertIn(
            "1 ref-version-mismatch finding(s) on the signer dropped"
            " (monumental-archive/signer is pinned on `# main`",
            out,
        )
        self.assertEqual("", err)

    def test_both_audits_together_are_dropped(self) -> None:
        """The scheduled run carries both; each is counted on its own line."""
        status, out, err = run([*REAL, *MISMATCH])
        self.assertEqual(0, status, err)
        self.assertIn("10 stale-action-refs finding(s) on the signer dropped", out)
        self.assertIn("1 ref-version-mismatch finding(s) on the signer dropped", out)

    def test_another_owner_mismatch_is_kept(self) -> None:
        """The same finding on another owner's repository still reds."""
        other = rewritten(
            MISMATCH[0],
            "Hidden",
            "monumental-archive/signer/",
            "someone-else/signer/",
        )
        status, out, _ = run([other])
        self.assertEqual(14, status)
        self.assertIn("0 ref-version-mismatch finding(s) on the signer dropped", out)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", out)

    def test_a_mismatch_with_no_readable_reference_is_kept(self) -> None:
        """A Hidden location with no `uses:` line names nothing; it is kept."""
        blind = rewritten(MISMATCH[0], "Hidden", "uses:", "calls:")
        none: list[str] = []
        self.assertEqual(none, signer.references(blind))
        status, out, _ = run([blind])
        self.assertEqual(14, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", out)

    def test_the_comment_alone_is_not_read_as_a_reference(self) -> None:
        """Primary carries only `main`; dropping the Hidden location blinds it."""
        blind = copy.deepcopy(MISMATCH[0])
        blind["locations"] = [
            loc for loc in locations(blind) if symbolic(loc)["kind"] == "Primary"
        ]
        status, out, _ = run([blind])
        self.assertEqual(14, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", out)

    def test_a_uses_in_prose_is_not_a_reference(self) -> None:
        """A commented `uses:` beside the signer's is not read as a second ref."""
        prose = rewritten(
            MISMATCH[0],
            "Hidden",
            "    with:\n",
            "    # relative `uses: actions/checkout@v4` in prose\n    with:\n",
        )
        want: list[str] = [MISMATCH_REF]
        self.assertEqual(want, signer.references(prose))
        self.assertEqual(0, run([prose])[0])

    def test_a_second_reference_elsewhere_keeps_it(self) -> None:
        """Every named reference must be the signer's, not just one of them."""
        two = rewritten(
            MISMATCH[0],
            "Hidden",
            "    with:\n",
            "    with:\n      - uses: actions/checkout@v4\n",
        )
        status, out, _ = run([two])
        self.assertEqual(14, status)
        self.assertIn("1 finding(s) remain: ref-version-mismatch", out)

    def test_another_audit_on_the_signer_is_kept(self) -> None:
        """An unrelated audit carrying the same locations still reds."""
        other_rule = copy.deepcopy(MISMATCH[0])
        other_rule["ident"] = "unpinned-uses"
        status, out, _ = run([other_rule])
        self.assertEqual(14, status)
        self.assertIn("1 finding(s) remain: unpinned-uses", out)


class Kept(unittest.TestCase):
    """Every finding the exemption does not name still reds."""

    def test_another_repository_reusable_workflow_is_kept(self) -> None:
        """The same audit on someone else's reusable workflow is reported."""
        other = repointed(
            REAL[0],
            "monumental-archive/other/.github/workflows/sign.yml@" + "a" * 40,
        )
        status, out, _ = run([other])
        self.assertEqual(14, status)
        self.assertIn("0 stale-action-refs finding(s) on the signer dropped", out)
        self.assertIn("1 finding(s) remain: stale-action-refs", out)

    def test_a_third_party_action_is_kept(self) -> None:
        """A stale third-party action pin is exactly what the audit is for."""
        third = repointed(REAL[0], "actions/checkout@" + "b" * 40)
        status, out, _ = run([*REAL, third])
        self.assertEqual(14, status)
        self.assertIn("1 finding(s) remain", out)

    def test_a_repository_that_only_shares_the_prefix_is_kept(self) -> None:
        """`signer-fork` is not `signer`: repositories match whole."""
        near = repointed(
            REAL[0],
            "monumental-archive/signer-fork/.github/workflows/sign.yml@" + "c" * 40,
        )
        self.assertEqual(14, run([near])[0])

    def test_another_audit_on_the_signer_is_kept(self) -> None:
        """The exemption is one rule wide, not the signer wide."""
        other_rule = copy.deepcopy(REAL[0])
        other_rule["ident"] = "impostor-commit"
        status, out, _ = run([other_rule])
        self.assertEqual(14, status)
        self.assertIn("remain: impostor-commit", out)

    def test_a_finding_with_no_readable_reference_is_kept(self) -> None:
        """When zizmor's shape gives no reference, nothing is assumed."""
        blind = copy.deepcopy(REAL[0])
        for location in locations(blind):
            symbolic(location)["kind"] = "Related"
        status, out, err = run([blind])
        self.assertEqual(1, status)
        self.assertIn("findings[0]: no location is Primary", err)
        self.assertEqual("", out)

    def test_a_reference_without_a_ref_is_kept(self) -> None:
        """A Primary feature with no `@` is not a reference."""
        self.assertEqual(14, run([repointed(REAL[0], "monumental-archive/signer")])[0])


class Matching(unittest.TestCase):
    """How the signer's repository is read and compared."""

    def test_case_differences_do_not_matter(self) -> None:
        """GitHub owner and repository names are case-insensitive."""
        upper = repointed(REAL[0], SIGNER_REF.replace("signer", "Signer", 1))
        self.assertEqual(0, run([upper])[0])

    def test_an_ignored_finding_is_not_reported(self) -> None:
        """Per-site `zizmor: ignore` comments are zizmor's, not this filter's."""
        ignored = repointed(REAL[0], "actions/checkout@" + "d" * 40)
        ignored["ignored"] = True
        self.assertEqual(0, run([*REAL, ignored])[0])

    def test_no_findings_is_green_and_counts_zero(self) -> None:
        """An empty run drops nothing and passes."""
        status, out, _ = run([])
        self.assertEqual(0, status)
        self.assertIn("0 stale-action-refs finding(s) on the signer dropped", out)


class Refusals(unittest.TestCase):
    """Reports the filter will not guess about: exit 1, by name."""

    def test_a_signer_declaration_with_no_repository_is_refused(self) -> None:
        """A declaration that names no owner/repo cannot exempt anything."""
        status, _, err = run(REAL, workflow="signer")
        self.assertEqual(1, status)
        self.assertIn("names no owner/repository", err)

    def test_a_finding_of_the_wrong_shape_is_refused_not_raised(self) -> None:
        """The shrunk examples (#1555): refused by name, never a traceback."""
        shapes: tuple[tuple[dict[str, object], str], ...] = (
            ({"ident": []}, "findings[0]: ident is not a string"),
            (
                {"ident": "stale-action-refs", "locations": [{"symbolic": "x"}]},
                "findings[0]: desc is not a string",
            ),
            (
                {"ident": "stale-action-refs", "desc": "d", "locations": 5},
                "findings[0]: determinations is not an object",
            ),
            (
                {
                    "ident": "stale-action-refs",
                    "desc": "d",
                    "determinations": {"severity": "Low"},
                    "locations": [{"symbolic": {"kind": "Primary"}, "concrete": 1}],
                },
                "findings[0]: locations[0]: symbolic.key is not an object",
            ),
        )
        for finding, said in shapes:
            with self.subTest(finding=finding):
                status, _, err = run([finding])
                self.assertEqual(1, status)
                self.assertIn(
                    f"{TASK}: zizmor exited 14: the output is not zizmor's report:"
                    f" {said}",
                    err,
                )

    def test_a_finding_with_no_primary_location_is_refused(self) -> None:
        """A remaining finding with no location at fault: refused by name."""
        third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
        for location in locations(third):
            symbolic(location)["kind"] = "Hidden"
        status, _, err = run([third])
        self.assertEqual(1, status)
        self.assertIn(
            f"{TASK}: zizmor exited 14: the output is not zizmor's report:"
            " findings[0]: no location is Primary",
            err,
        )

    def test_input_that_is_not_the_finding_array_is_refused(self) -> None:
        """A SARIF document or an error object is not silently empty."""
        status, out, err = run({"runs": []})
        self.assertEqual(1, status)
        self.assertIn("it is not zizmor's finding array", err)
        self.assertEqual("", out)


class Records(unittest.TestCase):
    """What remains is recorded; zizmor runs once, its status the exit."""

    @override
    def setUp(self) -> None:
        """Lay out the findings file and the environment a task gives."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "findings" / "job.jsonl"
        self.env = {
            "GH_TOKEN": "a token",
            "MISE_TASK_NAME": TASK,
            "ORG_FINDINGS": str(self.file),
        }

    def main(self, ran: signer.Ran) -> tuple[int, str, str, mock.MagicMock]:
        """Run the CLI with zizmor answering `ran`.

        Returns:
            The status, stdout, stderr, and the stand-in zizmor.

        """
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(signer, "run_zizmor", return_value=ran) as fake,
            mock.patch.dict(os.environ, self.env),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            status = signer.main(["--signer-workflow", SIGNER_WORKFLOW, *ARGS])
        return status, out.getvalue(), err.getvalue(), fake

    def records(self) -> list[object]:
        """Read every record appended.

        Returns:
            The records, in order; none when the file was not written.

        """
        if not self.file.exists():
            return []
        lines = self.file.read_text(encoding="utf-8").splitlines()
        return [decoded.load_json(line) for line in lines]

    def test_a_remaining_finding_is_one_record(self) -> None:
        """A third-party stale ref: one record, its lines, zizmor's 11.

        Its Hidden location (the job) comes first: the subject is that
        location's file, the message the Primary location's source.
        """
        third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
        both: list[dict[str, object]] = [*REAL, third]
        doc = json.dumps(both).encode()
        status, out, err, fake = self.main(signer.Ran(11, doc, b" INFO zizmor\n"))
        self.assertEqual(11, status, err)
        fake.assert_called_once_with(ARGS)
        path = ".github/workflows/continuous.yml"
        want: list[object] = [
            {
                "v": 1,
                "task": TASK,
                "subject": path,
                "message": "stale-action-refs: actions/checkout@" + "d" * 40,
                "detail": [
                    "commit hash does not point to a Git tag",
                    "severity: Low",
                    f"{path}:640: this job",
                    f"{path}:643: this job",
                ],
            }
        ]
        self.assertEqual(want, self.records())
        self.assertTrue(err.startswith(" INFO zizmor\n"), err)
        self.assertIn(f"{TASK}: 1 finding(s) remain: stale-action-refs", out)
        self.assertIn("10 stale-action-refs finding(s) on the signer dropped", out)

    def test_all_dropped_is_no_record_and_green(self) -> None:
        """Every finding the signer's: no record, 0, though zizmor said 11."""
        status, _, err, _ = self.main(signer.Ran(11, json.dumps(REAL).encode(), b""))
        self.assertEqual(0, status, err)
        self.assertFalse(self.records())

    def test_a_clean_run_is_green(self) -> None:
        """No findings: no record, zizmor's 0."""
        status, _, err, _ = self.main(signer.Ran(0, b"[]", b""))
        self.assertEqual(0, status, err)
        self.assertFalse(self.records())

    def test_zizmor_failing_is_its_status_and_its_stderr(self) -> None:
        """A status outside 0 and 10-14: zizmor's, its stderr, no record."""
        status, _, err, _ = self.main(signer.Ran(2, b"", b"error: bad input\n"))
        self.assertEqual(2, status)
        self.assertEqual("error: bad input\n", err)
        self.assertFalse(self.records())

    def test_no_token_is_a_finding_before_zizmor_runs(self) -> None:
        """GH_TOKEN unset: the blindness finding, 1, zizmor never run."""
        self.env["GH_TOKEN"] = ""
        status, _, _, fake = self.main(signer.Ran(0, b"[]", b""))
        self.assertEqual(1, status)
        fake.assert_not_called()
        want: list[object] = [
            {
                "v": 1,
                "task": TASK,
                "subject": "",
                "message": "GH_TOKEN is unset — the offline run skips every",
                "detail": [
                    "API-backed audit this task exists for; refusing to pass blind"
                ],
            }
        ]
        self.assertEqual(want, self.records())

    def test_an_offline_degradation_is_a_finding(self) -> None:
        """Zizmor dropped to offline: its stderr, the finding, 1."""
        said = b" WARN zizmor: running in offline mode\n"
        status, _, err, _ = self.main(signer.Ran(0, b"[]", said))
        self.assertEqual(1, status)
        self.assertTrue(err.startswith(said.decode()), err)
        want: list[object] = [
            {
                "v": 1,
                "task": TASK,
                "subject": "",
                "message": "zizmor degraded to offline despite GH_TOKEN —",
                "detail": [
                    "the online audits did not run; a green result would be blind"
                ],
            }
        ]
        self.assertEqual(want, self.records())

    def test_no_zizmor_is_refused_by_name(self) -> None:
        """No zizmor on PATH: refused by name, 1, its one record (#1934)."""
        with mock.patch.object(signer, "ZIZMOR", "/nonexistent/zizmor"):
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.dict(os.environ, self.env),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                status = signer.main(["--signer-workflow", SIGNER_WORKFLOW])
        self.assertEqual(1, status)
        self.assertIn(f"{TASK}: zizmor is not on PATH", err.getvalue())
        want: list[object] = [
            {
                "v": 1,
                "task": TASK,
                "subject": "",
                "message": "zizmor is not on PATH",
                "detail": [],
            }
        ]
        self.assertEqual(want, self.records())


WORKFLOW = """\
name: a
on: push
permissions: {}
concurrency:
  group: a
  cancel-in-progress: true
jobs:
  a:
    name: a
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/setup-go@v5
"""


class PinnedZizmor(unittest.TestCase):
    """The belt's pinned zizmor, `--offline`, on a planted workflow."""

    def judged(self, workflow: str) -> tuple[int, list[object], str]:
        """Run the CLI over the belt's zizmor on one planted workflow.

        Returns:
            The status, the records read back, and stderr.

        """
        if hermetic.optional_tool("zizmor") is None:
            self.skipTest("the belt's zizmor is not on PATH")
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".github" / "workflows").mkdir(parents=True)
        (tmp / ".github" / "workflows" / "a.yml").write_text(workflow, encoding="utf-8")
        file = tmp / "findings.jsonl"
        done = subprocess.run(
            [
                sys.executable,
                str(HERE / "zizmor_signer.py"),
                "--signer-workflow",
                SIGNER_WORKFLOW,
                "--offline",
                *ARGS,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(
                GH_TOKEN=STANDIN,
                MISE_TASK_NAME=TASK,
                ORG_FINDINGS=str(file),
                PYTHONUTF8="1",
            ),
            cwd=tmp,
            check=False,
        )
        lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
        got: list[object] = [decoded.load_json(line) for line in lines]
        return done.returncode, got, done.stderr

    def test_an_unpinned_action_is_the_record(self) -> None:
        """One unpinned `uses:`: the record read back, zizmor's 14."""
        status, got, err = self.judged(WORKFLOW)
        self.assertEqual(14, status, err)
        path = ".github/workflows/a.yml"
        want: list[object] = [
            {
                "v": 1,
                "task": TASK,
                "subject": path,
                "message": "unpinned-uses: actions/setup-go@v5",
                "detail": [
                    "unpinned action reference",
                    "severity: High",
                    f"{path}:12: this step",
                    (
                        f"{path}:12: action is not pinned to a hash"
                        " (required by blanket policy)"
                    ),
                ],
            }
        ]
        self.assertEqual(want, got)

    def test_two_of_one_audit_in_one_file_are_two_messages(self) -> None:
        """Two unpinned `uses:` in one file: two records, two messages."""
        two = WORKFLOW + "      - uses: actions/setup-node@v4\n"
        status, got, err = self.judged(two)
        self.assertEqual(14, status, err)
        messages = [decoded.as_object(r, "a record")["message"] for r in got]
        want: list[object] = [
            "unpinned-uses: actions/setup-go@v5",
            "unpinned-uses: actions/setup-node@v4",
        ]
        self.assertEqual(want, messages)


STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("\\t".join(sys.argv[1:]) + "\\n")
with open(os.environ["STUB_DOC"], encoding="utf-8") as doc:
    sys.stdout.write(doc.read())
sys.exit(int(os.environ["STUB_RC"]))
"""


class Task(unittest.TestCase):
    """audit:actions' own body over a stub zizmor, in a planted tree."""

    def run_task(
        self, document: object, status: int
    ) -> subprocess.CompletedProcess[str]:
        """Plant a workflow and the canon's identity, then run the body.

        Returns:
            The finished process.

        """
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "tree"
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "a.yml").write_text("on: push\n")
        (root / "security").mkdir()
        (root / "security" / "identity.toml").write_text(
            f'signer-workflow = "{SIGNER_WORKFLOW}"\n', encoding="utf-8"
        )
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "zizmor"
        stub.write_text(f"#!{sys.executable}\n{STUB}", encoding="utf-8")
        stub.chmod(0o755)
        doc = tmp / "doc.json"
        doc.write_text(json.dumps(document), encoding="utf-8")
        self.log = tmp / "zizmor.log"
        self.file = tmp / "findings.jsonl"
        config = decoded.load_toml((HERE / "config.toml").read_bytes())
        tasks = decoded.as_object(config["tasks"], "tasks")
        body = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], "run")
        run = hermetic.task_run(TASK, body)
        return subprocess.run(
            run.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(
                **run.env,
                PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                ORG_BELT_DIR=str(HERE),
                ORG_CANON_DIR=str(root),
                ORG_PYTHON=sys.executable,
                ORG_FINDINGS=str(self.file),
                GH_TOKEN=STANDIN,
                STUB_LOG=str(self.log),
                STUB_DOC=str(doc),
                STUB_RC=str(status),
                PYTHONUTF8="1",
            ),
            cwd=root,
            check=False,
        )

    def test_a_remaining_finding_is_recorded_and_zizmor_s_red(self) -> None:
        """A third-party stale ref: one record, zizmor's 11, one run."""
        third = repointed(REAL[0], "actions/checkout@" + "d" * 40)
        done = self.run_task([third], 11)
        self.assertEqual(11, done.returncode, done.stderr)
        logged: list[str] = self.log.read_text(encoding="utf-8").splitlines()
        once: list[str] = ["--format=json\t--persona=auditor\t.github/workflows/"]
        self.assertEqual(logged, once)
        lines = self.file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(lines))
        record = decoded.as_object(decoded.load_json(lines[0]), "the record")
        self.assertEqual(".github/workflows/continuous.yml", record["subject"])

    def test_a_clean_run_is_no_record_and_green(self) -> None:
        """No findings: no record, 0."""
        done = self.run_task([], 0)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()

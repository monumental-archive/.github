#!/usr/bin/env python3
"""Table tests for audit:security-config (#1213).

Every live answer here was fetched 2026-11-15 from monumental-archive
with read-only `gh api` calls and reduced only by `jq`:

  defaults.json            orgs/<org>/code-security/configurations/defaults
                           (jq .)
  repositories.json        .../configurations/265775/repositories
                           ({status, repository: {name, private}})
  listing.json             orgs/<org>/repos, paginated and slurped
                           ({name, private})
  monumental-archive.json  repos/<org>/monumental-archive
                           ({name, private, security_and_analysis})

`.../monumental-archive/vulnerability-alerts` answered 204 (enabled),
which has no body to keep.

The records and the roster are the tree's own. Each red case changes
one thing and asserts the exact message, not merely the exit status: a
case scored on exit alone can pass for the wrong reason.

The live private repository answered with Dependabot security updates
ENABLED on the day the fixtures were fetched, against a record that
says disabled. The agreement case states that record's value for the
field explicitly, so it proves agreement; the live disagreement is its
own case, because it is a real finding and not a fixture artefact.

stdlib `unittest`, the belt's one test idiom.
"""

import copy
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import decoded
import security_config as sc

if TYPE_CHECKING:
    from collections.abc import Callable

HERE = Path(__file__).parent
DATA = HERE / "testdata" / "security-config"
TREE = HERE.parent
OWNER = "monumental-archive"
CFG = "orgs/monumental-archive/code-security/configurations"
DEFAULTS = f"{CFG}/defaults"
ATTACHED = f"{CFG}/265775/repositories?per_page=100"
LISTING = "orgs/monumental-archive/repos?per_page=100"
PRIVATE = "repos/monumental-archive/monumental-archive"
ALERTS = f"{PRIVATE}/vulnerability-alerts"
OFF = "disabled"


def load(path: Path) -> object:
    """Read one JSON file.

    Returns:
        The parsed value.

    """
    return decoded.load_json(path.read_text(encoding="utf-8"))


class Live:
    """A fake `gh api`: the fetched answers, editable per case."""

    def __init__(self) -> None:
        """Load the live answers."""
        self.answers: dict[str, tuple[int, object]] = {
            LISTING: (200, load(DATA / "listing.json")),
            DEFAULTS: (200, load(DATA / "defaults.json")),
            ATTACHED: (200, load(DATA / "repositories.json")),
            PRIVATE: (200, load(DATA / "monumental-archive.json")),
            ALERTS: (204, None),
        }

    def __call__(self, endpoint: str, *, paginate: bool = False) -> tuple[int, object]:
        """Answer one read.

        Returns:
            (status, body) as gh_fetch would.

        """
        del paginate
        return copy.deepcopy(self.answers[endpoint])


def records(*, agreeing_private: bool = True) -> sc.Records:
    """Read the tree's records and roster.

    Returns:
        The inputs the task passes.

    """
    private = decoded.as_object(
        load(TREE / "security" / "org-private.json"), "org-private.json"
    )
    if agreeing_private:
        # The live repository has security updates on; see the module
        # docstring. Every other field is the tree's own record.
        private["dependabot_security_updates"] = "enabled"
    return sc.read_records(
        public=load(TREE / "security" / "org-default.json"),
        private=private,
        roster=load(TREE / "slsa" / "assert-policy.json"),
    )


def rows_of(live: Live, endpoint: str) -> list[object]:
    """Read one answer's body as the list of rows a case edits in place.

    Returns:
        The body itself, not a copy.

    """
    _, body = live.answers[endpoint]
    return decoded.as_array(body, endpoint)


def body_of(live: Live, endpoint: str) -> dict[str, object]:
    """Read one answer's body as the object a case edits in place.

    Returns:
        The body itself, not a copy.

    """
    _, body = live.answers[endpoint]
    return decoded.as_object(body, endpoint)


def field(value: object, *path: str) -> object:
    """Follow keys down through nested objects, as `value[a][b]` does.

    Returns:
        The value at the end of the path.

    """
    for key in path:
        value = decoded.as_object(value, key)[key]
    return value


def run(live: Live, recs: sc.Records) -> tuple[int, str, str]:
    """Judge and report.

    Returns:
        (exit status, stdout, stderr).

    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = sc.report(sc.Audit(OWNER, recs, live).run())
    return rc, out.getvalue(), err.getvalue()


class SecurityConfigTest(unittest.TestCase):
    """Agreement passes; each single disagreement reds by name."""

    def test_agreement_passes_with_a_line_per_repository(self) -> None:
        """The live answers against the tree's records agree."""
        rc, out, err = run(Live(), records())
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertIn(
            "org-default.json: 22 fields equal configuration 265775 "
            "(the default for new public repositories), nothing unstated",
            out,
        )
        for repo in (
            "edtf",
            "iiif-server",
            "monumental-archive-db",
            "monumental-archive-ledger",
            "release-lab",
            "signer",
            ".github",
            "stele",
        ):
            self.assertIn(
                f"audit:security-config: {repo}: public, attached to "
                "configuration 265775, enforced\n",
                out,
            )
        self.assertIn(
            "audit:security-config: monumental-archive: private, 7 fields "
            "equal org-private.json, not attached to configuration 265775 "
            "(12 fields have no repository-level reading)\n",
            out,
        )

    def test_changed_record_field_reds(self) -> None:
        """A record value that differs from live names both values."""
        recs = records()
        recs.public["secret_scanning_validity_checks"] = OFF
        rc, _, err = run(Live(), recs)
        self.assertEqual(rc, 1)
        self.assertIn(
            'org-default.json states secret_scanning_validity_checks="disabled"; '
            'configuration 265775 has "enabled"',
            err,
        )

    def test_unstated_live_field_reds(self) -> None:
        """A live field the record does not state is red."""
        recs = records()
        del recs.public["secret_scanning_generic_secrets_push_protection"]
        rc, _, err = run(Live(), recs)
        self.assertEqual(rc, 1)
        self.assertIn(
            "configuration 265775 sets "
            'secret_scanning_generic_secrets_push_protection="disabled", '
            "which org-default.json does not state",
            err,
        )

    def test_server_and_own_fields_are_not_findings(self) -> None:
        """id, url, timestamps, target_type, name and description are excepted."""
        _, _, err = run(Live(), records())
        for key in sorted(sc.SERVER_FIELDS | sc.OWN_FIELDS):
            self.assertNotIn(f"sets {key}=", err)

    def test_unattached_public_repository_reds(self) -> None:
        """A public roster member missing from the attachment list is named."""
        live = Live()
        ep = ATTACHED
        rows = rows_of(live, ep)
        live.answers[ep] = (
            200,
            [r for r in rows if field(r, "repository", "name") != "stele"],
        )
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn(
            "audit:security-config: stele: public and not attached to "
            "configuration 265775\n",
            err,
        )

    def test_unenforced_public_repository_reds(self) -> None:
        """Attached but not enforced is red with its status."""
        live = Live()
        ep = ATTACHED
        rows = rows_of(live, ep)
        for r in rows:
            if field(r, "repository", "name") == "edtf":
                decoded.as_object(r, "a row")["status"] = "failed"
        live.answers[ep] = (200, rows)
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn(
            "edtf: public and attached to configuration 265775 "
            "with status failed, not enforced",
            err,
        )

    def test_private_repository_with_secret_scanning_on_reds(self) -> None:
        """The metered product switched on by one click is named."""
        live = Live()
        ep = PRIVATE
        body = body_of(live, ep)
        scanning = field(body, "security_and_analysis", "secret_scanning")
        decoded.as_object(scanning, "secret_scanning")["status"] = "enabled"
        live.answers[ep] = (200, body)
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn(
            "monumental-archive: secret_scanning: org-private.json states "
            "disabled; secret_scanning is enabled",
            err,
        )

    def test_live_private_security_updates_disagree_with_the_record(self) -> None:
        """The tree's record against the live answer as fetched."""
        rc, _, err = run(Live(), records(agreeing_private=False))
        self.assertEqual(rc, 1)
        self.assertIn(
            "monumental-archive: dependabot_security_updates: org-private.json "
            "states disabled; dependabot_security_updates is enabled",
            err,
        )

    def test_private_repository_attached_to_public_shape_reds(self) -> None:
        """A private member on the public configuration bills, and is named."""
        live = Live()
        ep = ATTACHED
        rows = rows_of(live, ep)
        rows.append({
            "status": "enforced",
            "repository": {"name": "monumental-archive", "private": True},
        })
        live.answers[ep] = (200, rows)
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn(
            "monumental-archive: private and attached to the public "
            "configuration 265775",
            err,
        )

    def test_private_alerts_off_reds(self) -> None:
        """A 404 from vulnerability-alerts reads as disabled."""
        live = Live()
        live.answers[ALERTS] = (404, None)
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn("dependabot_alerts: org-private.json states enabled;", err)

    def test_org_403_is_cannot_judge_naming_the_grant(self) -> None:
        """A refused org read is never a pass, and names the grant."""
        live = Live()
        live.answers[DEFAULTS] = (403, None)
        rc, out, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertNotIn("public, attached", out)
        self.assertIn(
            f"CANNOT_JUDGE {DEFAULTS} "
            "(HTTP 403) — needs organisation permission Administration: read",
            err,
        )

    def test_hidden_security_and_analysis_is_cannot_judge(self) -> None:
        """Without admin GitHub omits the block; that is blind, not clean."""
        live = Live()
        ep = PRIVATE
        body = body_of(live, ep)
        del body["security_and_analysis"]
        live.answers[ep] = (200, body)
        rc, out, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertNotIn("monumental-archive: private", out)
        self.assertIn(
            f"CANNOT_JUDGE {PRIVATE} security_and_analysis "
            "(no HTTP status) — needs repository permission Administration: read",
            err,
        )

    def test_no_public_default_reds(self) -> None:
        """With no default for new public repositories there is nothing to describe."""
        live = Live()
        ep = DEFAULTS
        rows = rows_of(live, ep)
        decoded.as_object(rows[0], "[0]")["default_for_new_repos"] = (
            "private_and_internal"
        )
        live.answers[ep] = (200, rows)
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn(
            "0 configurations are the default for new public repositories", err
        )

    def test_roster_member_missing_from_listing_reds(self) -> None:
        """A roster repository the listing lacks is named."""
        live = Live()
        ep = LISTING
        rows = rows_of(live, ep)
        live.answers[ep] = (200, [r for r in rows if field(r, "name") != "signer"])
        rc, _, err = run(live, records())
        self.assertEqual(rc, 1)
        self.assertIn("signer: in the roster and not in the org listing", err)


class ShapeTest(unittest.TestCase):
    """An input or a live answer of the wrong shape is refused by name."""

    def test_records_and_roster(self) -> None:
        """Each input field the audit reads, wrongly shaped in turn."""
        roster = load(TREE / "slsa" / "assert-policy.json")
        names = sc.roster_names
        repos = "population.repositories"
        rows: list[tuple[Callable[[], object], str]] = [
            (
                lambda: sc.read_records([], {}, roster),
                "org-default.json is not an object",
            ),
            (
                lambda: sc.read_records({}, 1, roster),
                "org-private.json is not an object",
            ),
            (lambda: names([]), "the roster is not an object"),
            (lambda: names({"population": []}), "population is not an object"),
            (lambda: names({"population": {}}), f"{repos} is absent or empty"),
            (
                lambda: names({"population": {"repositories": []}}),
                f"{repos} is absent or empty",
            ),
            (
                lambda: names({"population": {"repositories": ["x"]}}),
                f"{repos}[0] is not an object",
            ),
            (
                lambda: names({"population": {"repositories": [{"r": 1}]}}),
                f"{repos}[0].repo is not a string",
            ),
            (
                lambda: sc.repo_status({"k": {"status": 1}}, "k"),
                "security_and_analysis.k.status is not a string",
            ),
            (lambda: sc.pages("e", {}), "e: the pages is not a list"),
            (lambda: sc.pages("e", [[1], 2]), "e: a page is not a list"),
        ]
        for call, message in rows:
            with (
                self.subTest(message=message),
                self.assertRaises(decoded.UndecodableError) as cm,
            ):
                call()
            self.assertEqual(str(cm.exception), message)

    def test_status_readings(self) -> None:
        """A status is read; an absent block or status is no reading."""
        self.assertEqual(sc.repo_status({"k": {"status": "on"}}, "k"), "on")
        self.assertIsNone(sc.repo_status({"k": {}}, "k"))
        self.assertIsNone(sc.repo_status({"k": "on"}, "k"))
        flat: list[object] = [1, 2, 3]
        self.assertEqual(sc.pages("e", [[1], [], [2, 3]]), flat)

    def test_live_answers(self) -> None:
        """Each live field the audit reads, wrongly shaped in turn."""
        cases: list[tuple[str, object, str]] = [
            (LISTING, [1], f"{LISTING}: [0] is not an object"),
            (
                LISTING,
                [{"name": "x", "private": "no"}],
                f"{LISTING}: [0].private is not a boolean",
            ),
            (LISTING, [{"private": True}], f"{LISTING}: [0].name is not a string"),
            (DEFAULTS, ["x"], f"{DEFAULTS}: [0] is not an object"),
            (
                ATTACHED,
                [{"status": "enforced"}],
                f"{ATTACHED}: [0].repository is not an object",
            ),
            (
                ATTACHED,
                [{"repository": {"name": 1}}],
                f"{ATTACHED}: [0].repository.name is not a string",
            ),
            (
                ATTACHED,
                [{"repository": {"name": "x"}, "status": 2}],
                f"{ATTACHED}: [0].status is not a string",
            ),
        ]
        for endpoint, body, message in cases:
            live = Live()
            live.answers[endpoint] = (200, body)
            audit = sc.Audit(OWNER, records(), live)
            with (
                self.subTest(message=message),
                self.assertRaises(decoded.UndecodableError) as cm,
            ):
                audit.run()
            self.assertEqual(str(cm.exception), message)

    def test_gh_reads_strictly_and_names_the_endpoint(self) -> None:
        """A duplicate key in gh's output is refused with the endpoint."""
        done = subprocess.CompletedProcess([], 0, stdout='{"a": 1, "a": 2}', stderr="")
        with (
            mock.patch.object(subprocess, "run", return_value=done),
            self.assertRaises(decoded.UndecodableError) as cm,
        ):
            sc.gh_fetch("x/y")
        self.assertEqual(str(cm.exception), 'x/y: duplicate key "a"')
        pages = subprocess.CompletedProcess([], 0, stdout="[[1], [2]]", stderr="")
        with mock.patch.object(subprocess, "run", return_value=pages):
            self.assertEqual(sc.gh_fetch("x/y", paginate=True), (200, [1, 2]))

    def test_main_reports_a_refusal_as_an_unreadable_input(self) -> None:
        """The refusal exits as the audit's other input errors exit."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "roster.json"
            bad.write_text('{"population": {"repositories": [{}]}}', encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = sc.main(
                    [
                        "--owner",
                        OWNER,
                        "--record",
                        str(TREE / "security" / "org-default.json"),
                        "--private-record",
                        str(TREE / "security" / "org-private.json"),
                        "--roster",
                        str(bad),
                    ],
                    Live(),
                )
        self.assertEqual(rc, 1)
        self.assertEqual(
            err.getvalue(),
            "audit:security-config: cannot read an input: "
            "population.repositories[0].repo is not a string\n",
        )


if __name__ == "__main__":
    unittest.main()

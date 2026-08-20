#!/usr/bin/env python3
"""Table tests for the Go advisory leg's written-decision exit.

These are the REGRESSION net, not the evidence. The evidence for #615 is
a real scan: govulncheck v1.7.0 against stele v0.17.0, the live
https://vuln.go.dev, and the real GO-2026-5932 — reachable through
Rekor's `pki` package, `Fixed in: N/A`, the finding that burned that
tag. Every message shape below is copied from that captured stream
rather than from the issue's prose, because prose flattens JSON and a
check written against a fixture agrees with itself no matter how wrong
both are (#358, canon v1.24.0).

What the table adds is the next regression. The guard branches are the
least exercised code in the belt and a guard that skips when it should
run looks exactly like success (#364), so both directions are driven
here and the hard-error paths are asserted as errors, never as
fallbacks — a decision that parses as nothing must not decide nothing
quietly.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "go_vulns",
    Path(__file__).with_name("go-vulns.py"),
)
gv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gv)

# The config message govulncheck v1.7.0 actually emitted, verbatim. Its
# presence is what separates "scanned, found nothing" from "did not
# scan", so it opens every stream a test builds.
CONFIG = {
    "config": {
        "protocol_version": "v1.0.0",
        "scanner_name": "govulncheck",
        "scanner_version": "v1.7.0",
        "db": "https://vuln.go.dev",
        "db_last_modified": "2026-08-19T17:06:06Z",
        "go_version": "go1.26.6",
        "scan_level": "symbol",
        "scan_mode": "source",
    },
}

ADVISORY = "GO-2026-5932"
MODULE = "golang.org/x/crypto"
VERSION = "v0.54.0"
PURL = f"pkg:golang/{MODULE}@{VERSION}"
KEY = gv.Key(ADVISORY, MODULE, VERSION)


def module_finding(fixed: str = "") -> dict:
    """Build the module-level finding shape: a bare module and version.

    Returns:
        One govulncheck `finding` message.

    """
    finding: dict = {"osv": ADVISORY, "trace": [{"module": MODULE, "version": VERSION}]}
    if fixed:
        finding["fixed_version"] = fixed
    return {"finding": finding}


def symbol_finding(module: str = MODULE, version: str = VERSION) -> dict:
    """Build the symbol-level finding shape: the reachable call chain.

    Returns:
        One govulncheck `finding` message, vulnerable frame first.

    """
    return {
        "finding": {
            "osv": ADVISORY,
            "trace": [
                {
                    "module": module,
                    "version": version,
                    "package": f"{module}/openpgp/s2k",
                    "function": "init",
                    "position": {
                        "filename": "openpgp/s2k/s2k.go",
                        "line": 14,
                        "column": 1,
                    },
                },
                {
                    "module": "github.com/monumental-archive/stele",
                    "package": "github.com/monumental-archive/stele/internal/trust",
                    "function": "init",
                    "position": {
                        "filename": "internal/trust/rekorbody.go",
                        "line": 36,
                        "column": 2,
                    },
                },
            ],
        },
    }


def package_finding() -> dict:
    """Build the package-level finding shape: imported, nothing called.

    Returns:
        One govulncheck `finding` message.

    """
    return {
        "finding": {
            "osv": ADVISORY,
            "trace": [
                {
                    "module": MODULE,
                    "version": VERSION,
                    "package": f"{MODULE}/openpgp",
                },
            ],
        },
    }


def stream(*msgs: dict) -> str:
    """Concatenate messages the way govulncheck writes them.

    Returns:
        Pretty-printed JSON objects back to back, no separators.

    """
    return "".join(json.dumps(msg, indent=2) for msg in msgs)


def document(
    *,
    advisory: str = ADVISORY,
    purl: str = PURL,
    status: str = "not_affected",
    timestamp: str | None = "2026-08-20T00:00:00Z",
    doc_timestamp: str | None = "2026-08-20T00:00:00Z",
) -> dict:
    """Build an OpenVEX document in the shape vexctl emits.

    Returns:
        A one-statement document, fields omitted when passed None.

    """
    statement: dict = {
        "vulnerability": {"name": advisory},
        "products": [{"@id": purl}],
        "status": status,
        "justification": "vulnerable_code_not_in_execute_path",
        "impact_statement": "recorded for the table",
    }
    if timestamp is not None:
        statement["timestamp"] = timestamp
    doc: dict = {"@context": "https://openvex.dev/ns/v0.2.0", "statements": [statement]}
    if doc_timestamp is not None:
        doc["timestamp"] = doc_timestamp
    return doc


def written(root: Path, name: str, body: object) -> None:
    """Write one decision document into a decisions directory."""
    text = body if isinstance(body, str) else json.dumps(body)
    (root / name).write_text(text, encoding="utf-8")


class TestParsePurl(unittest.TestCase):
    """The product identifier a decision joins on."""

    def test_go_module_keeps_its_whole_path(self) -> None:
        """The namespace and name join: the module path is the name."""
        self.assertEqual(gv.parse_purl(PURL), (MODULE, VERSION))

    def test_last_segment_is_not_the_name(self) -> None:
        """Keying on the last segment would decide a different package."""
        name, _ = gv.parse_purl("pkg:golang/example.com/dep@v1.0.0")
        self.assertEqual(name, "example.com/dep")

    def test_scoped_npm_name_splits_at_the_last_at(self) -> None:
        """A scoped name opens with @, so the first @ is not the version."""
        self.assertEqual(
            gv.parse_purl("pkg:npm/%40scope/pkg@1.2.3"),
            ("@scope/pkg", "1.2.3"),
        )

    def test_qualifiers_and_subpath_are_not_identity(self) -> None:
        """A repository_url or subpath does not change what is covered."""
        self.assertEqual(
            gv.parse_purl(f"{PURL}?repository_url=https://example.test#sub"),
            (MODULE, VERSION),
        )

    def test_unversioned_product_cannot_join(self) -> None:
        """A product with no version covers no exact package@version."""
        self.assertIsNone(gv.parse_purl("pkg:golang/example.com/dep"))
        self.assertIsNone(gv.parse_purl("pkg:golang/example.com/dep@"))

    def test_non_purl_cannot_join(self) -> None:
        """A product that is not a package URL is not a join key."""
        self.assertIsNone(gv.parse_purl("golang.org/x/crypto@v0.54.0"))


class TestLevels(unittest.TestCase):
    """How far into the code a finding reached, govulncheck's ranking."""

    def test_symbol_frame_is_called(self) -> None:
        """A frame naming a function is the reachable case, the gate."""
        frame = symbol_finding()["finding"]["trace"][0]
        self.assertEqual(gv.level_of(frame), "called")

    def test_package_frame_is_imported(self) -> None:
        """A package with no function is imported, never the gate."""
        frame = package_finding()["finding"]["trace"][0]
        self.assertEqual(gv.level_of(frame), "imported")

    def test_bare_module_frame_is_required(self) -> None:
        """A bare module is in the graph and nothing more."""
        frame = module_finding()["finding"]["trace"][0]
        self.assertEqual(gv.level_of(frame), "required")


class TestScan(unittest.TestCase):
    """Folding a stream into one record per advisory and module."""

    def test_highest_level_wins_in_either_order(self) -> None:
        """An advisory is reported at the level it reached, not last seen."""
        for msgs in (
            (CONFIG, module_finding(), symbol_finding()),
            (CONFIG, symbol_finding(), module_finding()),
        ):
            found, _ = gv.scan(list(msgs))
            self.assertEqual(found[KEY].level, "called")

    def test_fix_survives_being_outranked(self) -> None:
        """Govulncheck states the fix on the module finding; it must keep."""
        for msgs in (
            (CONFIG, module_finding(fixed="v0.55.0"), symbol_finding()),
            (CONFIG, symbol_finding(), module_finding(fixed="v0.55.0")),
        ):
            found, _ = gv.scan(list(msgs))
            self.assertEqual(found[KEY].fixed, "v0.55.0")

    def test_config_is_returned_for_the_did_it_run_guard(self) -> None:
        """The config message is what proves a scan happened at all."""
        _, config = gv.scan([CONFIG])
        self.assertEqual(config["scanner_version"], "v1.7.0")

    def test_trace_reads_entry_point_first(self) -> None:
        """A reader follows a call chain inward; govulncheck emits it out."""
        found, _ = gv.scan([CONFIG, symbol_finding()])
        self.assertEqual(found[KEY].trace, ["trust.init", "s2k.init"])
        self.assertEqual(
            found[KEY].position,
            "internal/trust/rekorbody.go:36:2",
        )


class TestMessages(unittest.TestCase):
    """Decoding govulncheck's concatenated JSON objects."""

    def test_concatenated_objects_all_decode(self) -> None:
        """The stream is objects back to back, not a JSON array."""
        errors: list[str] = []
        msgs = gv.messages(stream(CONFIG, module_finding()), errors)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(errors, [])

    def test_empty_stream_is_no_messages_not_an_error(self) -> None:
        """Emptiness is caught by the config guard, with a better message."""
        errors: list[str] = []
        self.assertEqual(gv.messages("   \n ", errors), [])
        self.assertEqual(errors, [])

    def test_undecodable_stream_is_an_error(self) -> None:
        """Garbage on stdin must never read as a clean scan."""
        errors: list[str] = []
        gv.messages("{not json", errors)
        self.assertEqual(len(errors), 1)


def read_one(body: object) -> tuple[dict, list[str]]:
    """Write one document into a fresh directory and parse it.

    Returns:
        The decisions held, and the errors raised.

    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        written(root, "d.openvex.json", body)
        errors: list[str] = []
        return gv.read_decisions(root, errors), errors


class TestReadDecisions(unittest.TestCase):
    """Parsing the canon's decisions, refusals included."""

    def test_absent_directory_is_an_empty_set(self) -> None:
        """The adopter's case: no decisions decides nothing, quietly."""
        errors: list[str] = []
        held = gv.read_decisions(Path("/nonexistent/security/vex"), errors)
        self.assertEqual(held, {})
        self.assertEqual(errors, [])

    def test_a_statement_becomes_its_triple(self) -> None:
        """The decision is keyed by advisory, package and version."""
        held, errors = read_one(document())
        self.assertEqual(errors, [])
        self.assertIn(gv.Key(ADVISORY, MODULE, VERSION), held)

    def test_product_purl_is_carried_verbatim(self) -> None:
        """A citation quotes what the human wrote, not a reassembly."""
        held, _ = read_one(document())
        self.assertEqual(held[gv.Key(ADVISORY, MODULE, VERSION)].purl, PURL)

    def test_only_openvex_documents_are_read(self) -> None:
        """The directory carries a README; it is not a decision."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            written(root, "README.md", "# not a decision")
            errors: list[str] = []
            self.assertEqual(gv.read_decisions(root, errors), {})
            self.assertEqual(errors, [])

    def test_unreadable_document_is_an_error(self) -> None:
        """A malformed judgment is never read as an absent one."""
        _, errors = read_one("{not json")
        self.assertEqual(len(errors), 1)

    def test_statement_without_status_is_an_error(self) -> None:
        """A decision that decides nothing is not a decision."""
        _, errors = read_one(document(status=""))
        self.assertEqual(len(errors), 1)

    def test_statement_without_vulnerability_is_an_error(self) -> None:
        """A statement naming no advisory cannot excuse one."""
        _, errors = read_one(document(advisory=""))
        self.assertEqual(len(errors), 1)

    def test_statement_falls_back_to_the_document_timestamp(self) -> None:
        """OpenVEX dates a statement or its document; either is a moment."""
        held, errors = read_one(document(timestamp=None))
        self.assertEqual(errors, [])
        self.assertEqual(
            held[gv.Key(ADVISORY, MODULE, VERSION)].decided,
            "2026-08-20T00:00:00Z",
        )

    def test_statement_dated_nowhere_is_an_error(self) -> None:
        """A judgment with no moment cannot be cited honestly."""
        _, errors = read_one(document(timestamp=None, doc_timestamp=None))
        self.assertEqual(len(errors), 1)

    def test_unversioned_product_is_skipped_not_refused(self) -> None:
        """A product that cannot join is not a malformed document."""
        held, errors = read_one(document(purl="pkg:golang/example.com/dep"))
        self.assertEqual(held, {})
        self.assertEqual(errors, [])

    def test_two_documents_deciding_one_triple_is_an_error(self) -> None:
        """One finding, one decision; a filename must not pick the winner."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            written(root, "a.openvex.json", document())
            written(root, "b.openvex.json", document(status="affected"))
            errors: list[str] = []
            gv.read_decisions(root, errors)
            self.assertEqual(len(errors), 1)


def found_called(version: str = VERSION) -> dict:
    """Scan a stream carrying one called advisory.

    Returns:
        The found set for that scan.

    """
    found, _ = gv.scan([CONFIG, symbol_finding(version=version)])
    return found


def held_one(**kwargs: object) -> dict:
    """Parse one decision document into the decided set.

    Returns:
        The decisions held.

    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        written(root, "d.openvex.json", document(**kwargs))
        return gv.read_decisions(root, [])


class TestExcusals(unittest.TestCase):
    """The join: what excuses, what does not, and what is stale."""

    def test_exact_triple_excuses(self) -> None:
        """A decision naming this module@version clears the finding."""
        excused, stale = gv.excusals(found_called(), held_one())
        self.assertEqual(list(excused), [gv.Key(ADVISORY, MODULE, VERSION)])
        self.assertEqual(stale, [])

    def test_false_positive_also_excuses(self) -> None:
        """The other dialect's spelling of the same judgment."""
        excused, _ = gv.excusals(found_called(), held_one(status="false_positive"))
        self.assertEqual(len(excused), 1)

    def test_affected_does_not_excuse(self) -> None:
        """A decision is not automatically an exit; that status is not one."""
        excused, stale = gv.excusals(found_called(), held_one(status="affected"))
        self.assertEqual(excused, {})
        self.assertEqual(stale, [])

    def test_under_investigation_does_not_excuse(self) -> None:
        """Knowing and looking is a real status, and it is not an exit."""
        excused, _ = gv.excusals(found_called(), held_one(status="under_investigation"))
        self.assertEqual(excused, {})

    def test_a_bumped_version_is_stale_and_excuses_nothing(self) -> None:
        """Coverage is derived: a version bump is a fresh judgment."""
        excused, stale = gv.excusals(found_called(version="v0.55.0"), held_one())
        self.assertEqual(excused, {})
        self.assertEqual([d.key.version for d in stale], [VERSION])

    def test_a_decision_for_another_advisory_is_not_stale_here(self) -> None:
        """A canon decision is org-wide; one graph cannot retire it."""
        held = held_one(advisory="GO-2026-9999", purl="pkg:cargo/serde_cbor@0.11.2")
        excused, stale = gv.excusals(found_called(), held)
        self.assertEqual(excused, {})
        self.assertEqual(stale, [])

    def test_an_uncalled_advisory_is_not_excused(self) -> None:
        """Only the called set is the gate, so only it needs an exit."""
        found, _ = gv.scan([CONFIG, module_finding()])
        excused, _ = gv.excusals(found, held_one())
        self.assertEqual(excused, {})


def run_main(text: str, canon: str | None, argv: list[str]) -> tuple[int, str]:
    """Drive main() over one stream and one canon directory.

    Returns:
        The exit status and everything printed, both streams.

    """
    env = {} if canon is None else {"ORG_CANON_DIR": canon}
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(sys, "argv", argv),
        mock.patch.object(sys, "stdin", io.StringIO(text)),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        status = gv.main()
    return status, out.getvalue() + err.getvalue()


def judge(text: str, *, decided: bool) -> tuple[int, str]:
    """Judge a stream against a canon that has a decision, or none.

    Returns:
        The exit status and the report.

    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp, "security", "vex")
        root.mkdir(parents=True)
        if decided:
            written(root, f"{ADVISORY}.openvex.json", document())
        return run_main(text, tmp, ["go-vulns.py", "."])


class TestMain(unittest.TestCase):
    """The verdict, end to end, including every refusal."""

    def test_called_and_undecided_refuses(self) -> None:
        """The unchanged case: a reachable advisory is a hard red."""
        status, report = judge(stream(CONFIG, symbol_finding()), decided=False)
        self.assertEqual(status, gv.VULNERABILITIES_FOUND)
        self.assertIn("no written decision", report)

    def test_called_and_decided_passes_and_cites(self) -> None:
        """The exit is a written decision, printed with what it cites."""
        status, report = judge(stream(CONFIG, symbol_finding()), decided=True)
        self.assertEqual(status, 0)
        self.assertIn(f"{ADVISORY}.openvex.json", report)
        self.assertIn(PURL, report)

    def test_uncalled_advisory_alone_passes(self) -> None:
        """A module in the graph nobody calls was never the gate."""
        status, report = judge(stream(CONFIG, module_finding()), decided=False)
        self.assertEqual(status, 0)
        self.assertIn("required", report)

    def test_clean_scan_passes(self) -> None:
        """Nothing found is the adopter's ordinary Monday."""
        status, report = judge(stream(CONFIG), decided=False)
        self.assertEqual(status, 0)
        self.assertIn("no reachable vulnerabilities", report)

    def test_the_excusal_set_is_always_printed(self) -> None:
        """An empty excusal set is stated, so nothing is excused unseen."""
        _, report = judge(stream(CONFIG, symbol_finding()), decided=False)
        self.assertIn("excused by written decision: none", report)

    def test_stream_without_config_refuses(self) -> None:
        """A scan that did not run must not read as a scan that was clean."""
        status, report = judge("", decided=False)
        self.assertEqual(status, 1)
        self.assertIn("the scan did not run", report)

    def test_unset_canon_refuses(self) -> None:
        """Without the belt there are no decisions, which is not "none"."""
        status, report = run_main(
            stream(CONFIG),
            None,
            ["go-vulns.py", "."],
        )
        self.assertEqual(status, 1)
        self.assertIn("ORG_CANON_DIR", report)

    def test_wrong_argument_count_refuses(self) -> None:
        """The module directory names which report a reader is reading."""
        with TemporaryDirectory() as tmp:
            status, _ = run_main(stream(CONFIG), tmp, ["go-vulns.py"])
        self.assertEqual(status, 1)

    def test_a_broken_decision_refuses_rather_than_ignoring_it(self) -> None:
        """A malformed judgment stops the run; it never silently excuses."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp, "security", "vex")
            root.mkdir(parents=True)
            written(root, "d.openvex.json", "{not json")
            status, report = run_main(
                stream(CONFIG, symbol_finding()),
                tmp,
                ["go-vulns.py", "."],
            )
        self.assertEqual(status, 1)
        self.assertIn("unreadable as OpenVEX", report)


if __name__ == "__main__":
    unittest.main()

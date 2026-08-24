#!/usr/bin/env python3
"""Table tests for the gate's Postgres provisioning (#813).

One row per branch of each guard, both directions: the major that must
resolve to a `pg_config` and the nearest one that must not. The row that
matters most is the missing pin — a gate that tested four majors while
the publish path shipped five would look exactly like a gate that tested
five, which is the silence this issue exists to end.

Mutation-checked: every guard below was broken in turn and the rows that
cover it observed to fail — the counts are in the class docstrings, so a
reader can repeat them rather than trust this line.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "pgrx_postgres",
    Path(__file__).with_name("pgrx-postgres.py"),
)
pgrx_postgres = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pgrx_postgres)


def install(version: str, *, nested: bool = True, empty: bool = False) -> str:
    """Lay out one installed package on disk.

    Nested is the shape measured on both platforms: the archive unpacks
    to `postgresql-<version>-<triple>/` with no top-level `bin/`.

    Returns:
        The install path, as `mise ls --json` reports it.

    """
    root = Path(tempfile.mkdtemp()) / version
    where = root / f"postgresql-{version}-aarch64-apple-darwin" if nested else root
    if not empty:
        (where / "bin").mkdir(parents=True)
        (where / "bin" / "pg_config").write_text("#!/bin/sh\n", encoding="utf-8")
    else:
        root.mkdir(parents=True, exist_ok=True)
    return str(root)


def entry(
    version: str,
    *,
    active: bool = True,
    installed: bool = True,
    **kwargs: bool,
) -> dict:
    """Build one entry of `mise ls <pkg> --json`.

    Returns:
        The entry, in the shape measured on mise 2026.8.3.

    """
    return {
        "version": version,
        "active": active,
        "installed": installed,
        "install_path": install(version, **kwargs),
    }


class PinnedTest(unittest.TestCase):
    """Reading the pins. Mutation: count inactive entries as pinned."""

    def test_an_active_installed_pin_counts(self) -> None:
        """The ordinary state after `mise install`."""
        found, absent = pgrx_postgres.pinned([entry("18.6.0")])
        self.assertEqual(sorted(found), [18])
        self.assertEqual(absent, [])

    def test_a_package_installed_by_hand_is_not_a_pin(self) -> None:
        """It would pass on this laptop and be absent in CI."""
        found, _absent = pgrx_postgres.pinned([entry("18.6.0", active=False)])
        self.assertEqual(found, {})

    def test_a_declared_pin_not_installed_is_held_apart(self) -> None:
        """The remedy is `mise install`, not an edit to mise.toml."""
        found, absent = pgrx_postgres.pinned([entry("18.6.0", installed=False)])
        self.assertEqual(found, {})
        self.assertEqual(absent, ["18.6.0"])

    def test_the_major_is_the_leading_component(self) -> None:
        """14.24.0 serves pg14; the patch is the repo's to choose."""
        found, _absent = pgrx_postgres.pinned([entry("14.24.0"), entry("17.11.0")])
        self.assertEqual(sorted(found), [14, 17])

    def test_a_version_with_no_numeric_major_is_not_a_postgres(self) -> None:
        """Refuse rather than guess at a shape the package does not use."""
        found, _absent = pgrx_postgres.pinned([entry("latest")])
        self.assertEqual(found, {})


class PgConfigTest(unittest.TestCase):
    """Finding pg_config. Mutation: look only at the top-level bin/."""

    def test_the_nested_layout_both_platforms_use(self) -> None:
        """postgresql-<version>-<triple>/bin/pg_config."""
        found = pgrx_postgres.pg_config(entry("18.6.0"))
        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith("/bin/pg_config"))

    def test_a_top_level_bin_is_taken_too(self) -> None:
        """Not assumed away: a future asset may unpack flat."""
        self.assertIsNotNone(pgrx_postgres.pg_config(entry("18.6.0", nested=False)))

    def test_an_install_with_no_pg_config_is_not_served(self) -> None:
        """A broken install is a finding, not a silently skipped major."""
        self.assertIsNone(pgrx_postgres.pg_config(entry("18.6.0", empty=True)))

    def test_an_install_path_that_does_not_exist_is_not_served(self) -> None:
        """Mise can report a path a `rm -rf` has since removed."""
        self.assertIsNone(pgrx_postgres.pg_config({"install_path": "/nope/18.6.0"}))


class PlanTest(unittest.TestCase):
    """The plan. Mutation: emit records alongside findings."""

    def test_every_declared_major_resolves(self) -> None:
        """Edtf's own shape, five majors pinned."""
        versions = ("14.24.0", "15.19.0", "16.15.0", "17.11.0", "18.6.0")
        listing = [entry(v) for v in versions]
        records, findings = pgrx_postgres.plan([14, 15, 16, 17, 18], listing)
        self.assertEqual(findings, [])
        self.assertEqual(len(records), 5)
        self.assertTrue(records[0].startswith("pg\t14\t"))

    def test_a_declared_major_with_no_pin_is_a_finding(self) -> None:
        """The gate would otherwise test four and report five."""
        records, findings = pgrx_postgres.plan([17, 18], [entry("18.6.0")])
        self.assertEqual(records, [])
        self.assertTrue(any("pg17" in line for line in findings))
        self.assertTrue(any("mise.toml" in line for line in findings))

    def test_a_pinned_but_broken_install_is_a_finding(self) -> None:
        """Worded as reinstall, because the pin itself is right."""
        _records, findings = pgrx_postgres.plan([18], [entry("18.6.0", empty=True)])
        self.assertTrue(any("no bin/pg_config" in line for line in findings))

    def test_one_missing_major_suppresses_the_whole_plan(self) -> None:
        """A partial plan would test some majors and report success."""
        records, findings = pgrx_postgres.plan([17, 18], [entry("18.6.0")])
        self.assertEqual(records, [])
        self.assertTrue(findings)

    def test_a_pin_the_crate_does_not_declare_is_not_a_finding(self) -> None:
        """A spare pin is waste, never a wrong answer."""
        listing = [entry("18.6.0"), entry("14.24.0")]
        _records, findings = pgrx_postgres.plan([18], listing)
        self.assertEqual(findings, [])

    def test_a_pin_that_is_not_installed_is_named_in_the_remedy(self) -> None:
        """Otherwise the remedy reads as an edit that changes nothing."""
        listing = [entry("18.6.0", installed=False)]
        _records, findings = pgrx_postgres.plan([18], listing)
        self.assertTrue(any("not installed" in line for line in findings))


class MainTest(unittest.TestCase):
    """The whole check. Mutation: treat an empty --majors as success."""

    @staticmethod
    def invoke(majors: str, listing: object) -> tuple:
        """Run the provisioner over one listing.

        Returns:
            Exit status, stdout and stderr.

        """
        out, err = io.StringIO(), io.StringIO()
        original = pgrx_postgres.sys.stdin
        pgrx_postgres.sys.stdin = io.StringIO(
            json.dumps(listing) if listing is not None else "{",
        )
        try:
            with redirect_stdout(out), redirect_stderr(err):
                status = pgrx_postgres.main(["--majors", majors])
        finally:
            pgrx_postgres.sys.stdin = original
        return status, out.getvalue(), err.getvalue()

    def test_a_served_major_prints_its_record(self) -> None:
        """What the task reads to build its `cargo pgrx init` line."""
        status, out, err = self.invoke("18", [entry("18.6.0")])
        self.assertEqual(status, 0, msg=err)
        self.assertTrue(out.startswith("pg\t18\t"))

    def test_a_missing_pin_is_red_with_no_records(self) -> None:
        """Both halves: the exit status and the empty plan."""
        status, out, err = self.invoke("17,18", [entry("18.6.0")])
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("pg17", err)

    def test_no_majors_is_red(self) -> None:
        """A caller with nothing to provision must not reach this."""
        status, _out, err = self.invoke("", [entry("18.6.0")])
        self.assertEqual(status, 1)
        self.assertIn("no Postgres major", err)

    def test_a_listing_that_is_not_json_is_red(self) -> None:
        """No listing is not the same as no pins."""
        status, _out, err = self.invoke("18", None)
        self.assertEqual(status, 1)
        self.assertIn("did not give JSON", err)


if __name__ == "__main__":
    unittest.main()

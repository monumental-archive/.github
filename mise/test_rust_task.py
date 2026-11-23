#!/usr/bin/env python3
"""Table tests for mise/rust_task.py, the Rust task helpers' shared steps.

One row per branch, in this process. The belt is a directory of this
file's own scripts, named by ORG_BELT_DIR: a `pgrx-env.sh` whose
functions answer from the row's variables, so a row says what each
call was given and what it returned without a Mac or a Postgres.
`mise` is a stub on PATH and ORG_PYTHON a stub interpreter; both log
what they were asked.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import hermetic
import rust_task

ALL: list[str] = ["--all-features"]
PREPARED: list[str] = ["prepare 17"]
NOTHING: list[str] = []

PGRX_ENV = r"""
org_pgrx_highest() {
  cat > "${PLANT_DIR}/highest-in"
  printf '%b' "${PLANT_HIGHEST:-}"
  return "${PLANT_HIGHEST_STATUS:-0}"
}
org_pgrx_union() {
  cat > "${PLANT_DIR}/union-in"
  printf '%s\n' "${PLANT_UNION:-}"
  return "${PLANT_UNION_STATUS:-0}"
}
org_pgrx_darwin_prepare() {
  printf 'prepare %s\n' "$*" >> "${PLANT_DIR}/log"
  [[ ${PLANT_PREPARE_STATUS:-0} == 0 ]] || return "${PLANT_PREPARE_STATUS}"
  export PLANT_SDK=sdk
  export PLANT_KEPT="${PLANT_KEPT}"
}
org_pgrx_major() {
  local major=$1
  shift
  ( export PLANT_MAJOR="${major}"; "$@" )
}
"""
MISE_STUB = r"""#!/usr/bin/env bash
printf 'mise %s\n' "$*" >> "${PLANT_DIR}/log"
echo '[installed]'
exit "${PLANT_MISE_STATUS:-0}"
"""
PYTHON_STUB = r"""#!/usr/bin/env bash
printf '%s %s <%s>\n' "$(basename "$1")" "${*:2}" "$(cat)" >> "${PLANT_DIR}/log"
exit "${PLANT_PYTHON_STATUS:-0}"
"""
LADDER = r"""
org_clippy_ladder() { echo "-D warnings  -W x"; return "${PLANT_LADDER_STATUS:-0}"; }
org_clippy_contradiction_count() {
  echo " 2 "
  return "${PLANT_COUNT_STATUS:-0}"
}
"""
RUSTUP_STUB = r"""#!/usr/bin/env bash
[[ $* == "component list --installed" ]] || exit 99
printf 'clippy-host\nrustfmt-host\n'
exit "${PLANT_RUSTUP_STATUS:-0}"
"""
CARGO_STUB = r"""#!/usr/bin/env bash
printf 'cargo %s\n' "$*" >> "${PLANT_DIR}/log"
echo '{"meta": 1}'
exit "${PLANT_CARGO_STATUS:-0}"
"""
# Prints what a compile under at_major would see.
SHOW = 'echo "major=${PLANT_MAJOR-unset} sdk=${PLANT_SDK-unset}"; exit "${1:-0}"'


class Planted(unittest.TestCase):
    """A planted belt, the stubs, and the row's environment."""

    @override
    def setUp(self) -> None:
        """Plant the belt, the stubs and the row's environment."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.belt = self.tmp / "belt"
        self.belt.mkdir()
        (self.belt / "pgrx-env.sh").write_text(PGRX_ENV, encoding="utf-8")
        (self.belt / "clippy-ladder.sh").write_text(LADDER, encoding="utf-8")
        bins = self.tmp / "bin"
        bins.mkdir()
        stubs = {
            "mise": MISE_STUB,
            "python": PYTHON_STUB,
            "rustup": RUSTUP_STUB,
            "cargo": CARGO_STUB,
        }
        for name, text in stubs.items():
            stub = bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        (self.tmp / "log").touch()
        env = hermetic.env()
        patched = mock.patch.dict(
            os.environ,
            env
            | {
                "PATH": f"{bins}:{env['PATH']}",
                "ORG_BELT_DIR": str(self.belt),
                "ORG_PYTHON": str(bins / "python"),
                "PLANT_DIR": str(self.tmp),
                "PLANT_KEPT": "same",
            },
            clear=True,
        )
        self.enterContext(patched)

    def logged(self) -> list[str]:
        """Read the stubs' log, the belt masked.

        Returns:
            One line per call, in order.

        """
        text = (self.tmp / "log").read_text(encoding="utf-8")
        return text.replace(str(self.belt), "<belt>").splitlines()


class Basics(Planted):
    """The belt, its Python, names, features, commands and components."""

    def test_the_belt_is_org_belt_dir_else_this_modules_directory(self) -> None:
        """ORG_BELT_DIR when set; this module's own directory unset or empty."""
        self.assertEqual(self.belt, rust_task.belt())
        os.environ["ORG_BELT_DIR"] = ""
        self.assertEqual(Path(rust_task.__file__).parent, rust_task.belt())
        del os.environ["ORG_BELT_DIR"]
        self.assertEqual(Path(rust_task.__file__).parent, rust_task.belt())

    def test_the_belts_python_is_org_python_else_this_interpreter(self) -> None:
        """ORG_PYTHON when set; this interpreter when it is unset or empty."""
        os.environ["ORG_PYTHON"] = "/belt/python"
        self.assertEqual("/belt/python", rust_task.python())
        os.environ["ORG_PYTHON"] = ""
        self.assertEqual(sys.executable, rust_task.python())
        del os.environ["ORG_PYTHON"]
        self.assertEqual(sys.executable, rust_task.python())

    def test_a_specs_name_is_before_its_last_at(self) -> None:
        """`${spec%@*}`: the last `@` only, and a spec with none is whole."""
        rows = {"plain@0.1.0": "plain", "odd@1@2": "odd@1", "bare": "bare", "@1": ""}
        for spec, name in rows.items():
            with self.subTest(spec=spec):
                self.assertEqual(name, rust_task.name_of(spec))

    def test_features_are_clippy_features_else_all(self) -> None:
        """CLIPPY_FEATURES split into words; --all-features unset or empty."""
        self.assertEqual(ALL, rust_task.features())
        os.environ["CLIPPY_FEATURES"] = ""
        self.assertEqual(ALL, rust_task.features())
        os.environ["CLIPPY_FEATURES"] = "--features  a,b\t--locked"
        words: list[str] = ["--features", "a,b", "--locked"]
        self.assertEqual(words, rust_task.features())

    def test_run_returns_the_status_and_passes_the_environment(self) -> None:
        """The command's own status, under the environment it was given."""
        bash = hermetic.tool("bash")
        self.assertEqual(3, rust_task.run([bash, "-c", "exit 3"]))
        script = "[[ $PLANT_X == y ]] && [[ -z ${PLANT_DIR-} ]]"
        self.assertEqual(0, rust_task.run([bash, "-c", script], {"PLANT_X": "y"}))

    def test_output_reads_stdout_and_feeds_stdin(self) -> None:
        """The status and what it printed, the input on its stdin."""
        bash = hermetic.tool("bash")
        self.assertEqual(
            (4, "got:in\n"),
            rust_task.output([bash, "-c", 'echo "got:$(cat)"; exit 4'], "in"),
        )

    def test_installed_components_are_rustups_listing(self) -> None:
        """One per line; none when rustup fails or is absent."""
        want: list[str] = ["clippy-host", "rustfmt-host"]
        self.assertEqual(want, rust_task.installed_components())
        os.environ["PLANT_RUSTUP_STATUS"] = "1"
        self.assertEqual(NOTHING, rust_task.installed_components())
        (self.tmp / "bin" / "rustup").unlink()
        os.environ["PATH"] = str(self.tmp / "bin")
        self.assertEqual(NOTHING, rust_task.installed_components())

    def test_a_component_is_had_when_a_line_starts_with_its_name(self) -> None:
        """`grep -q "^<name>"`: a prefix of a line, never a middle."""
        listing = ["clippy-host", "rustfmt-host"]
        self.assertTrue(rust_task.has(listing, "rustfmt"))
        self.assertFalse(rust_task.has(listing, "host"))
        self.assertFalse(rust_task.has([], "clippy"))


class BeltCalls(Planted):
    """Belt shell functions called through bash: the ladder, the surface."""

    def test_a_belt_call_sources_the_script_and_calls_the_function(self) -> None:
        """The function's status and output; stdin reaches it."""
        os.environ |= {"PLANT_UNION": "16,17", "PLANT_UNION_STATUS": "2"}
        got = rust_task.belt_call(rust_task.pgrx_env(), "org_pgrx_union", stdin="lines")
        self.assertEqual((2, "16,17\n"), got)
        self.assertEqual("lines", (self.tmp / "union-in").read_text(encoding="utf-8"))

    def test_exports_are_what_the_function_set_or_changed(self) -> None:
        """New and changed variables; not bash's own, not those unchanged."""
        got = rust_task.belt_exports(
            rust_task.pgrx_env(), "org_pgrx_darwin_prepare", "17"
        )
        self.assertEqual((0, {"PLANT_SDK": "sdk"}), got)
        self.assertEqual(PREPARED, self.logged())

    def test_a_failed_export_returns_its_status_and_nothing(self) -> None:
        """The function's status, and no variable taken from it."""
        os.environ["PLANT_PREPARE_STATUS"] = "6"
        got = rust_task.belt_exports(
            rust_task.pgrx_env(), "org_pgrx_darwin_prepare", "17"
        )
        self.assertEqual((6, {}), got)

    def test_the_ladder_is_its_flags_and_its_count(self) -> None:
        """The flags split into words, the count stripped."""
        flags: list[str] = ["-D", "warnings", "-W", "x"]
        self.assertEqual((0, flags, "2"), rust_task.ladder())

    def test_a_failed_ladder_stops_before_the_count(self) -> None:
        """The ladder's status, no flags; a failed count is its status."""
        os.environ["PLANT_LADDER_STATUS"] = "3"
        self.assertEqual((3, NOTHING, ""), rust_task.ladder())
        os.environ |= {"PLANT_LADDER_STATUS": "0", "PLANT_COUNT_STATUS": "4"}
        status, _, _ = rust_task.ladder()
        self.assertEqual(4, status)

    def test_the_surface_is_gate_surfaces_reading_of_cargos_metadata(self) -> None:
        """No-deps metadata on the helper's stdin, CLIPPY_EXCLUDE passed."""
        os.environ["CLIPPY_EXCLUDE"] = "held"
        self.assertEqual((0, ""), rust_task.surface(str(self.tmp / "bin" / "cargo")))
        want = [
            "cargo metadata --no-deps --format-version 1 --locked",
            'gate_surface.py --exclude held <{"meta": 1}>',
        ]
        self.assertEqual(want, self.logged())

    def test_the_surfaces_status_is_the_helpers_else_cargos(self) -> None:
        """gate_surface.py's status first, as pipefail reads the pipe."""
        cargo = str(self.tmp / "bin" / "cargo")
        rows = {("0", "5"): 5, ("3", "0"): 3, ("3", "5"): 5}
        for (meta, helper), want in rows.items():
            with self.subTest(meta=meta, helper=helper):
                os.environ |= {
                    "PLANT_CARGO_STATUS": meta,
                    "PLANT_PYTHON_STATUS": helper,
                }
                status, _ = rust_task.surface(cargo)
                self.assertEqual(want, status)


class Pgrx(Planted):
    """The pgrx derivation, the registration and the compile at a major."""

    def test_pairs_are_each_extension_and_its_major(self) -> None:
        """Each `spec<TAB>major` line, a blank or one-field line dropped."""
        os.environ |= {
            "PLANT_HIGHEST": "a@1\\t17\\n\\nb@2\\t16\\nlone\\n\\tx\\n",
            "PLANT_UNION": "16,17",
        }
        got = rust_task.pgrx_pairs("surface")
        self.assertEqual((0, [["a@1", "17"], ["b@2", "16"]], "16,17"), got)
        self.assertEqual(
            "surface", (self.tmp / "highest-in").read_text(encoding="utf-8")
        )
        union_in = (self.tmp / "union-in").read_text(encoding="utf-8")
        self.assertEqual("a@1\t17\n\nb@2\t16\nlone\n\tx\n", union_in)

    def test_a_failed_highest_stops_before_the_union(self) -> None:
        """The derivation's status, no pairs, and the union never asked."""
        os.environ |= {"PLANT_HIGHEST": "a@1\\t17\\n", "PLANT_HIGHEST_STATUS": "1"}
        self.assertEqual((1, [], ""), rust_task.pgrx_pairs("surface"))
        self.assertFalse((self.tmp / "union-in").exists())

    def test_a_failed_union_is_its_status(self) -> None:
        """The union's status rides back, so its caller stops."""
        os.environ |= {"PLANT_HIGHEST": "a@1\\tnext\\n", "PLANT_UNION_STATUS": "1"}
        status, _, _ = rust_task.pgrx_pairs("surface")
        self.assertEqual(1, status)

    def test_registering_prepares_then_checks_every_majors_postgres(self) -> None:
        """The prepare's exports, the listing on pgrx_postgres.py's stdin."""
        self.assertEqual((0, {"PLANT_SDK": "sdk"}), rust_task.register("16,17"))
        want = [
            "prepare 16,17",
            "mise ls aqua:theseus-rs/postgresql-binaries --json",
            "pgrx_postgres.py --majors 16,17 <[installed]>",
        ]
        self.assertEqual(want, self.logged())

    def test_a_failed_prepare_registers_nothing(self) -> None:
        """The prepare's status, no exports, and no Postgres looked at."""
        os.environ["PLANT_PREPARE_STATUS"] = "4"
        self.assertEqual((4, {}), rust_task.register("17"))
        self.assertEqual(PREPARED, self.logged())

    def test_registrations_status_is_the_check_else_the_listing(self) -> None:
        """pgrx_postgres.py's status first, as pipefail reads the pipe."""
        rows = {("0", "5"): 5, ("3", "0"): 3, ("3", "5"): 5}
        for (listing, check), want in rows.items():
            with self.subTest(listing=listing, check=check):
                os.environ |= {
                    "PLANT_MISE_STATUS": listing,
                    "PLANT_PYTHON_STATUS": check,
                }
                status, _ = rust_task.register("17")
                self.assertEqual(want, status)

    def test_a_compile_runs_at_its_major_with_what_was_prepared(self) -> None:
        """org_pgrx_major given the major; the prepared variables reach it."""
        bash = hermetic.tool("bash")
        out = self.tmp / "out"
        argv = [bash, "-c", f'{{ {SHOW}; }} > "{out}"', "show", "7"]
        self.assertEqual(7, rust_task.at_major("17", argv, {"PLANT_SDK": "sdk"}))
        self.assertEqual("major=17 sdk=sdk\n", out.read_text(encoding="utf-8"))
        self.assertEqual(0, rust_task.at_major("", argv[:-1], {}))
        self.assertEqual("major= sdk=unset\n", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

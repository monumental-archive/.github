#!/usr/bin/env python3
"""Table tests for mise/msrv_lint.py, lint:msrv's body (#1911).

One row per branch of the body, each asserting the message it prints
and the commands it ran, in order. The helper runs in this process;
`cargo` and `mise` are stubs on PATH that log what they were asked,
`ORG_PYTHON` is a stub that answers each belt helper by name, and the
belt is planted with pgrx-env.sh's prepare and a major's environment
replaced by marks (the mise/test_pgrx_task_majors.py shape). Every
stream is captured at the descriptor, so a stub's or a belt function's
line lands beside the helper's own. One row runs the task's own body,
read from mise/config.toml, for the guard and the script contract.

`PLANT_FAIL` names the one step that fails: `cargo-<subcommand>`,
`mise-<what>`, a helper's file name, or `prepare`.

GIT ENVIRONMENT IS SCRUBBED (#857), the belt's idiom. stdlib
`unittest`, run through the gate as `mise run test`.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, override
from unittest import mock

import decoded
import hermetic
import msrv_lint

if TYPE_CHECKING:
    from collections.abc import Generator

BELT = Path(__file__).resolve().parent
TASK = "lint:msrv"
GIT = hermetic.tool("git")

CARGO_STUB = r"""#!/usr/bin/env bash
line="cargo $*"
[[ -z ${BINDGEN_EXTRA_CLANG_ARGS:-} ]] || line="${line} [${BINDGEN_EXTRA_CLANG_ARGS}]"
[[ -z ${RUSTUP_TOOLCHAIN:-} ]] || line="${line} {${RUSTUP_TOOLCHAIN}}"
printf '%s\n' "${line}" >> "${PLANT_LOG}"
[[ ${PLANT_FAIL:-} != "cargo-$1" ]] || exit 101
[[ $1 != metadata ]] || echo '{"planted": true}'
exit 0
"""
MISE_STUB = r"""#!/usr/bin/env bash
printf 'mise %s\n' "$*" >> "${PLANT_LOG}"
[[ ${PLANT_FAIL:-} != "mise-$2" ]] || exit 3
echo '[]'
"""
# Reads its stdin to the end before it logs, so a pipeline's stages log
# in the order the pipeline runs them; logs what it was given on stdin.
PYTHON_STUB = r"""#!/usr/bin/env bash
script=$(basename "$1")
shift
given=""
[[ $1 == --failed ]] || given=$(cat)
printf '%s %s <%s>\n' "${script}" "$*" "${given}" >> "${PLANT_LOG}"
[[ $1 != --failed ]] || { echo "lint:msrv: remedy for $2" >&2; exit 1; }
[[ ${PLANT_FAIL:-} != "${script}" ]] || exit 5
case ${script} in
  gate_surface.py) cat "${PLANT_SURFACE}" ;;
  pgrx_postgres.py) echo 'pg\t17\t/pg17/pg_config' ;;
  msrv_plan.py) cat "${PLANT_PLAN}" ;;
  *) exit 99 ;;
esac
"""
PGRX_ENV_STUB = f"""
. "{BELT / "pgrx-env.sh"}"
org_pgrx_darwin_prepare() {{
  printf 'prepare %s\\n' "$*" >> "${{PLANT_LOG}}"
  [[ ${{PLANT_FAIL:-}} != prepare ]] || return 4
  export PLANT_PREPARED=sdk
}}
org_pgrx_major_env() {{
  export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1-${{PLANT_PREPARED}}"
}}
"""
PLAIN = "crate\tplain@0.1.0\tplain\t-\tplain\n"
EXTENSION = "crate\text@0.2.0\tpgrx\t15,16,17\text\n"
CHECK = "check\t1.85.0\tdefault\tplain@0.1.0 other@0.1.0\n"
PG_CHECK = "check\t1.85.0\t--no-default-features --features pg17\text@0.2.0\n"
NOTE = "note\tunverified: gone@0.1.0 (CLIPPY_EXCLUDE)\n"
META = '<{"planted": true}>'
FETCH = "cargo fetch --locked"
METADATA = "cargo metadata --format-version 1 --locked --all-features"
PINS = "mise ls rust --json"
# Nothing printed, or nothing called, typed for the comparison.
NONE: list[str] = []


def lines(*text: str) -> list[str]:
    """Name the lines a row expects, typed for the comparison.

    Returns:
        The lines, as a list.

    """
    return list(text)


@contextlib.contextmanager
def captured(out: Path, err: Path) -> Generator[None]:
    """Send this process's stdout and stderr descriptors to two files.

    Yields:
        Nothing; both descriptors are restored on the way out.

    """
    saved = [os.dup(1), os.dup(2)]
    try:
        for fd, path in ((1, out), (2, err)):
            sys.stdout.flush()
            sys.stderr.flush()
            target = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            os.dup2(target, fd)
            os.close(target)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for fd, old in zip((1, 2), saved, strict=True):
            os.dup2(old, fd)
            os.close(old)


def body() -> str:
    """Read lint:msrv's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "tasks")
    entry = decoded.as_object(tasks[TASK], TASK)
    return decoded.as_str(entry["run"], f"{TASK}.run")


class Body(unittest.TestCase):
    """Each branch of the body: its commands, its lines, its status."""

    @override
    def setUp(self) -> None:
        """Plant the stubs, the surface, the plan and the belt."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bins = self.tmp / "bin"
        self.bins.mkdir()
        for name, text in {
            "cargo": CARGO_STUB,
            "mise": MISE_STUB,
            "python": PYTHON_STUB,
        }.items():
            stub = self.bins / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.belt = hermetic.belt(
            self.tmp / "belt", stubs={"pgrx-env.sh": PGRX_ENV_STUB}
        )
        self.log = self.tmp / "log"
        self.log.touch()
        self.surface = self.tmp / "surface"
        self.plan = self.tmp / "plan"
        self.plant(PLAIN, CHECK)
        env = hermetic.env()
        self.env = env | {
            "PATH": f"{self.bins}:{env['PATH']}",
            "ORG_BELT_DIR": str(self.belt),
            "ORG_PYTHON": str(self.bins / "python"),
            "ORG_FINDINGS": "",
            "MISE_TASK_NAME": TASK,
            "BASH_ENV": str(BELT / "findings.sh"),
            "PLANT_LOG": str(self.log),
            "PLANT_SURFACE": str(self.surface),
            "PLANT_PLAN": str(self.plan),
        }

    def plant(self, surface: str, plan: str) -> None:
        """Write the surface gate_surface.py answers and the plan msrv_plan.py does."""
        self.surface.write_text(surface, encoding="utf-8")
        self.plan.write_text(plan, encoding="utf-8")

    def call(self, manifest: Path | None = None) -> tuple[int, list[str], list[str]]:
        """Run the helper in this process, as the task does.

        `manifest` set runs `workspace` alone, at that manifest, with
        the count line main would print.

        Returns:
            The status, and the stdout and stderr lines.

        """
        out, err = self.tmp / "out", self.tmp / "err"
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            mock.patch.object(msrv_lint, "BELT", self.belt),
            captured(out, err),
        ):
            if manifest is None:
                status = msrv_lint.main()
            else:
                status, checked = msrv_lint.workspace(manifest, self.tmp / "pins")
                msrv_lint.say(f"{checked} checked")
        said = out.read_text(encoding="utf-8").splitlines()
        return status, said, err.read_text(encoding="utf-8").splitlines()

    def logged(self) -> list[str]:
        """Read the stubs' log, the scratch directory masked.

        Returns:
            One line per call, in order.

        """
        text = self.log.read_text(encoding="utf-8")
        masked = re.sub(r"\S*/toolchains\.json", "<tmp>/toolchains.json", text)
        return masked.replace(str(self.belt), "<belt>").splitlines()

    @staticmethod
    def planned(pgrx: str = "") -> list[str]:
        """Name the calls up to and including the plan.

        Returns:
            The log lines every row that plans begins with.

        """
        plan = (
            "msrv_plan.py --toolchains <tmp>/toolchains.json --exclude "
            f" --pgrx {pgrx} --floor <belt>/rust-floor.toml {META}"
        )
        return [PINS, FETCH, METADATA, f"gate_surface.py --exclude  {META}", plan]

    def test_one_group_compiles_at_its_toolchain_and_is_counted(self) -> None:
        """A plain group: one `-p` per spec, the gate's features, the count."""
        status, said, err = self.call()
        self.assertEqual((0, NONE), (status, err))
        check = "cargo check --locked --all-features -p plain@0.1.0 -p other@0.1.0"
        self.assertEqual(lines(*self.planned(), f"{check} {{1.85.0}}"), self.logged())
        want = [
            "lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0",
            "lint:msrv: 1 declared minimum(s) compiled at the toolchain they name",
        ]
        self.assertEqual(want, said)

    def test_the_gates_features_are_clippy_features(self) -> None:
        """CLIPPY_FEATURES and CLIPPY_EXCLUDE reach every step that reads them."""
        self.env |= {"CLIPPY_FEATURES": "--features a", "CLIPPY_EXCLUDE": "gone"}
        status, _, _ = self.call()
        self.assertEqual(0, status)
        logged = self.logged()
        self.assertEqual(
            "cargo metadata --format-version 1 --locked --features a", logged[2]
        )
        self.assertEqual(f"gate_surface.py --exclude gone {META}", logged[3])
        self.assertIn("--exclude gone --pgrx ", logged[4])
        check = "cargo check --locked --features a -p plain@0.1.0 -p other@0.1.0"
        self.assertEqual(f"{check} {{1.85.0}}", logged[5])

    def test_a_note_is_said_and_nothing_verified_says_so(self) -> None:
        """Every minimum excluded: the note, then both lines, and green."""
        self.plant(PLAIN, NOTE)
        status, said, err = self.call()
        self.assertEqual((0, NONE), (status, err))
        self.assertEqual(self.planned(), self.logged())
        want = [
            "lint:msrv: unverified: gone@0.1.0 (CLIPPY_EXCLUDE)",
            "lint:msrv: every declared minimum sits outside the gate's compile surface",
            "lint:msrv: nothing verified — the exclusions above are what to remove",
        ]
        self.assertEqual(want, said)

    def test_an_extension_is_registered_and_compiled_at_its_major(self) -> None:
        """Its own flags in place of the gate's, prepared, at its major."""
        self.plant(PLAIN + EXTENSION, CHECK + PG_CHECK)
        status, said, err = self.call()
        self.assertEqual((0, NONE), (status, err))
        own = "cargo check --locked --no-default-features --features pg17 -p ext@0.2.0"
        want = [
            *self.planned("ext:17"),
            "prepare 17",
            "mise ls aqua:theseus-rs/postgresql-binaries --json",
            "pgrx_postgres.py --majors 17 <[]>",
            (
                "cargo check --locked --all-features -p plain@0.1.0 -p other@0.1.0"
                " {1.85.0}"
            ),
            f"{own} [-Iicu-pg17-sdk] {{1.85.0}}",
        ]
        self.assertEqual(want, self.logged())
        said_want = lines(
            "lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0",
            "lint:msrv: compiling at 1.85.0: ext@0.2.0",
            "lint:msrv: 2 declared minimum(s) compiled at the toolchain they name",
        )
        self.assertEqual(said_want, said)

    def test_an_extension_the_plan_never_compiles_registers_nothing(self) -> None:
        """An extension on the surface but no pg check in the plan: no prepare."""
        self.plant(PLAIN + EXTENSION, CHECK)
        status, _, _ = self.call()
        self.assertEqual(0, status)
        self.assertNotIn("prepare 17", self.logged())
        self.assertEqual(6, len(self.logged()))

    def test_a_red_compile_says_the_rule_and_keeps_cargos_status(self) -> None:
        """msrv_plan.py --failed for the group, cargo's 101, and nothing after."""
        self.plant(PLAIN, CHECK + CHECK)
        self.env["PLANT_FAIL"] = "cargo-check"
        status, said, err = self.call()
        self.assertEqual(101, status)
        failed = "msrv_plan.py --failed 1.85.0 plain@0.1.0 other@0.1.0 <>"
        self.assertEqual(failed, self.logged()[-1])
        self.assertEqual(7, len(self.logged()))
        compiling = lines("lint:msrv: compiling at 1.85.0: plain@0.1.0 other@0.1.0")
        self.assertEqual(compiling, said)
        self.assertEqual(lines("lint:msrv: remedy for 1.85.0"), err)

    def test_an_unreadable_record_is_red_by_its_kind(self) -> None:
        """A kind the plan never writes is a finding, and nothing compiles."""
        self.plant(PLAIN, "verdict\tx\n" + CHECK)
        status, said, err = self.call()
        self.assertEqual((1, NONE), (status, said))
        unreadable = "lint:msrv: unreadable plan record from msrv_plan.py: verdict"
        self.assertEqual(lines(unreadable), err)
        self.assertEqual(self.planned(), self.logged())

    def test_a_blank_plan_line_is_skipped(self) -> None:
        """An empty record is no record, as the body's `read` loop had it."""
        self.plant(PLAIN, "\n" + CHECK)
        status, said, _ = self.call()
        self.assertEqual(0, status)
        self.assertEqual(2, len(said))

    def test_no_cargo_is_red(self) -> None:
        """Cargo absent with a Cargo.toml tracked is the vacuous-success failure."""
        (self.bins / "cargo").unlink()
        self.env["PATH"] = str(self.bins)
        status, said, err = self.call()
        self.assertEqual((1, NONE), (status, said))
        missing = (
            "lint:msrv: Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        self.assertEqual(lines(missing), err)
        self.assertEqual(NONE, self.logged())

    def test_no_mise_is_red(self) -> None:
        """Mise absent: a check that sees no pins would call a repo unpinned."""
        (self.bins / "mise").unlink()
        self.env["PATH"] = str(self.bins)
        status, said, err = self.call()
        self.assertEqual((1, NONE), (status, said))
        missing = "lint:msrv: mise missing — the toolchain pins this reads are mise's"
        self.assertEqual(lines(missing), err)
        self.assertEqual(NONE, self.logged())

    def test_each_step_that_fails_stops_the_run_with_its_status(self) -> None:
        """The first failure's own status, and no step after it."""
        rows = {
            "mise-rust": (3, 1),
            "cargo-fetch": (101, 2),
            "cargo-metadata": (101, 3),
            "gate_surface.py": (5, 4),
            "msrv_plan.py": (5, 5),
        }
        for step, (code, calls) in rows.items():
            with self.subTest(step=step):
                self.log.write_text("", encoding="utf-8")
                self.env["PLANT_FAIL"] = step
                status, said, _ = self.call()
                self.assertEqual((code, NONE), (status, said))
                self.assertEqual(self.planned()[:calls], self.logged())

    def test_a_failed_registration_stops_before_any_compile(self) -> None:
        """The prepare's status, or the Postgres check's, and nothing compiled."""
        self.plant(PLAIN + EXTENSION, PG_CHECK)
        for step, code, last in (
            ("prepare", 4, "prepare 17"),
            ("pgrx_postgres.py", 5, "pgrx_postgres.py --majors 17 <[]>"),
            (
                "mise-aqua:theseus-rs/postgresql-binaries",
                3,
                "pgrx_postgres.py --majors 17 <>",
            ),
        ):
            with self.subTest(step=step):
                self.log.write_text("", encoding="utf-8")
                self.env["PLANT_FAIL"] = step
                status, said, _ = self.call()
                self.assertEqual((code, NONE), (status, said))
                self.assertEqual(last, self.logged()[-1])

    def test_an_extension_naming_no_major_is_red_before_the_plan(self) -> None:
        """pgrx-env.sh's union refuses it by name; msrv_plan.py never runs."""
        self.plant("crate\text@0.2.0\tpgrx\tnext\text\n", PG_CHECK)
        status, said, err = self.call()
        self.assertEqual((1, NONE), (status, said))
        refused = "lint:msrv: ext@0.2.0 names no Postgres major: 'next'"
        self.assertEqual(lines(refused), err)
        self.assertEqual(self.planned()[:4], self.logged())

    def test_a_workspace_manifest_reaches_every_cargo_call(self) -> None:
        """`workspace` at a manifest: the fetch, the graph and each compile name it."""
        self.plant(PLAIN, CHECK)
        status, said, _ = self.call(Path("fuzz/Cargo.toml"))
        self.assertEqual(0, status)
        at = "--manifest-path fuzz/Cargo.toml"
        logged = self.logged()
        self.assertEqual(f"{FETCH} {at}", logged[0])
        self.assertEqual(
            f"cargo metadata --format-version 1 --locked {at} --all-features", logged[1]
        )
        check = (
            f"cargo check --locked {at} --all-features -p plain@0.1.0 -p other@0.1.0"
        )
        self.assertEqual(f"{check} {{1.85.0}}", logged[-1])
        self.assertEqual("lint:msrv: 1 checked", said[-1])


class Helpers(unittest.TestCase):
    """The small functions the body's steps lean on."""

    def test_the_belts_python_falls_back_to_this_interpreter(self) -> None:
        """ORG_PYTHON when set; this interpreter when it is unset or empty."""
        with mock.patch.dict(os.environ, {"ORG_PYTHON": "/belt/python"}):
            self.assertEqual("/belt/python", msrv_lint.python())
        with mock.patch.dict(os.environ, {"ORG_PYTHON": ""}):
            self.assertEqual(sys.executable, msrv_lint.python())

    def test_a_short_record_is_padded_to_four_fields(self) -> None:
        """A note has two fields; the rest of a record rides in its last."""
        got = msrv_lint.records("note\tsaid\n\ncheck\t1.85.0\tdefault\ta b\tc\n")
        want = [["note", "said", "", ""], ["check", "1.85.0", "default", "a b\tc"]]
        self.assertEqual(want, got)


class Task(unittest.TestCase):
    """The task's own body: its guard, and the call that starts the helper."""

    @staticmethod
    def run_body(tree: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        """Run lint:msrv's body in a tree, as mise runs it.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, body())
        return subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_no_cargo_toml_tracked_is_skipped(self) -> None:
        """No tracked Cargo.toml: the skip line, green, and no helper."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            subprocess.run([GIT, "init", "-q", tmp], check=True, env=hermetic.env())
            env = hermetic.env() | {
                "ORG_BELT_DIR": str(BELT),
                "ORG_PYTHON": "/nonexistent",
            }
            done = Task.run_body(tree, env)
        self.assertEqual(
            (0, "lint:msrv: no Cargo.toml tracked, skipped\n"),
            (done.returncode, done.stdout),
        )

    def test_the_body_starts_the_helper_which_exits_with_its_status(self) -> None:
        """A tracked Cargo.toml and no cargo: the helper's finding and its 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            subprocess.run([GIT, "init", "-q", tmp], check=True, env=hermetic.env())
            (tree / "Cargo.toml").write_text("", encoding="utf-8")
            subprocess.run(
                [GIT, "-C", tmp, "add", "Cargo.toml"], check=True, env=hermetic.env()
            )
            bins = tree.parent / f"{tree.name}-bin"
            bins.mkdir()
            self.addCleanup(shutil.rmtree, bins)
            for tool in ("git", "bash", "dirname", "cat", "mktemp", "rm"):
                (bins / tool).symlink_to(hermetic.tool(tool))
            env = hermetic.env() | {
                "PATH": str(bins),
                "ORG_BELT_DIR": str(BELT),
                "ORG_PYTHON": sys.executable,
                "ORG_FINDINGS": "",
            }
            done = Task.run_body(tree, env)
        want = f"lint:msrv: {msrv_lint.NO_CARGO}\n"
        self.assertEqual((1, "", want), (done.returncode, done.stdout, done.stderr))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""lint:rust lints every workspace member, past a red one (#726).

Linted as one workspace, the first crate clippy refused ended the run:
the members after it were never linted, and their findings surfaced
only once the first was fixed, one crate per round trip. lint:rust now
lints each member as its own package and reports every red crate by
name, with cargo's own exit status.

The row runs the task's own `run` body, read from mise/config.toml, in
a planted git tree. The workspace is edtf's, from the `cargo metadata`
captured in release/testdata/edtf-cargo-metadata.json and read by the
belt's real mise/gate_surface.py. cargo and rustup are stubs: clippy
refuses the first member and passes the rest, and the stub records each
crate it was asked to lint. clippy's verdict on a crate is clippy's to
prove; the row is about which crates are asked.

The task's body after its guard is mise/rust_lint.py (#1911). The
rows from `Planted` on call its `main` in this process, one row per
branch of lint:rust and of fix:rust with its message asserted, against
a planted belt whose ladder and pgrx provisioning are stubs.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import decoded
import hermetic
import rust_lint

BELT = Path(__file__).resolve().parent
METADATA = BELT.parent / "release" / "testdata" / "edtf-cargo-metadata.json"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
MEMBERS = [
    "edtf-core",
    "edtf-calendars",
    "edtf-normalize",
    "edtf-wasm",
    "edtf-cli",
    "edtf-postgres",
]

# Every cargo stub answers `cargo locate-project` first, as the belt's
# workspace derivation asks it (#2004), and logs nothing for it: a
# manifest under fuzz/ is its own root (or STUB_FUZZ_ROOT's answer), any
# other is the tree's top Cargo.toml, and STUB_LOCATE_FAILS fails each.
# `manifest` is the call's --manifest-path, for the rest of the stub.
LOCATE = """\
#!/usr/bin/env bash
manifest=""
prev=""
for arg in "$@"; do
  [[ ${prev} != --manifest-path ]] || manifest=${arg}
  prev=${arg}
done
if [[ $1 == locate-project ]]; then
  if [[ -n ${STUB_LOCATE_FAILS:-} ]]; then
    echo "error: planted: ${manifest} could not be read" >&2
    exit 101
  fi
  case ${manifest} in
    fuzz/*) echo "${STUB_FUZZ_ROOT:-${PWD}/fuzz/Cargo.toml}" ;;
    *) echo "${PWD}/Cargo.toml" ;;
  esac
  exit 0
fi
"""
CARGO = (
    LOCATE
    + """\
case "$1" in
  fmt | fetch) ;;
  metadata) cat "${STUB_METADATA}" ;;
  clippy)
    echo "$3" >> "${CLIPPY_LOG}"
    if [[ $3 == edtf-core@* ]]; then
      echo "error: planted finding in $3"
      exit 101
    fi
    ;;
  *) exit 1 ;;
esac
"""
)

# The task asks `rustup ... | grep -q "^clippy"` under pipefail. grep -q
# leaves at its first match, and a stub that is still writing the next
# line then dies of SIGPIPE, which pipefail reads as "clippy missing":
# the task stops before clippy and writes no log (measured: 19 misses in
# 8,000 under four parallel loops; #1623's merge queue, run 44617571290).
# So the stub ignores SIGPIPE and exits 0 whether or not it was read.
RUSTUP = """\
#!/usr/bin/env bash
trap '' PIPE
printf 'clippy-x86_64-unknown-linux-gnu\\n' 2> /dev/null
printf 'rustfmt-x86_64-unknown-linux-gnu\\n' 2> /dev/null
exit 0
"""

# A listing longer than a pipe buffer: the row's first line, 200 kB of
# blank lines, then its last. A probe that stops at the first line closes
# the pipe on a rustup still writing (#1637).
RUSTUP_LONG = """\
#!/usr/bin/env bash
printf '%s\\n' "${STUB_FIRST}"
head -c 200000 /dev/zero | tr '\\0' '\\n'
printf '%s\\n' "${STUB_LAST}"
"""


def body() -> str:
    """Read lint:rust's run body from the belt's config.

    Returns:
        The bash the task runs.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks["lint:rust"], "lint:rust")
    return decoded.as_str(task["run"], "the task's run")


def assert_every_member_linted(
    case: unittest.TestCase, done: subprocess.CompletedProcess[str], asked: list[str]
) -> None:
    """Assert clippy reached every member and named the red one."""
    case.assertEqual(101, done.returncode, done.stdout + done.stderr)
    want = [f"{m}@1.3.3" for m in MEMBERS]
    case.assertEqual(want, asked)
    linted = f"lint:rust: Cargo.toml: 6 crate(s) linted: {' '.join(MEMBERS)}\n"
    case.assertIn(linted, done.stdout)
    case.assertIn("lint:rust: red: edtf-core (Cargo.toml)\n", done.stderr)


class EveryMemberIsLinted(unittest.TestCase):
    """One red crate does not hide the rest (#726)."""

    @staticmethod
    def lint(
        rustup: str = RUSTUP, planted: dict[str, str] | None = None, **stub: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run lint:rust over the planted workspace.

        `planted` maps a tracked path to its text, written over the
        default `Cargo.toml` and `src/lib.rs`.

        Returns:
            The finished process and the crates clippy was asked.

        """
        files = {"Cargo.toml": "[workspace]\n", "src/lib.rs": "\n"}
        files |= planted or {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "repo"
            for name, text in files.items():
                (tree / name).parent.mkdir(parents=True, exist_ok=True)
                (tree / name).write_text(text, encoding="utf-8")
            bindir = root / "bin"
            bindir.mkdir()
            for name, text in (("cargo", CARGO), ("rustup", rustup)):
                (bindir / name).write_text(text, encoding="utf-8")
                (bindir / name).chmod(0o755)
            log = root / "clippy.log"
            env = hermetic.env(
                root / "home",
                ORG_BELT_DIR=str(BELT),
            )
            subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
            subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env["STUB_METADATA"] = str(METADATA)
            env["CLIPPY_LOG"] = str(log)
            env |= stub
            as_mise = hermetic.task_run("lint:rust", body())
            done = subprocess.run(
                as_mise.argv,
                cwd=tree,
                env=env | as_mise.env,
                capture_output=True,
                text=True,
                check=False,
            )
            asked = log.read_text(encoding="utf-8").split() if log.exists() else []
        return done, asked

    def assert_every_member_linted(
        self, done: subprocess.CompletedProcess[str], asked: list[str]
    ) -> None:
        """Clippy reached every member and named the red one."""
        assert_every_member_linted(self, done, asked)

    def test_a_red_first_member_leaves_the_rest_linted(self) -> None:
        """All six members are asked; the red one is named; cargo's 101 is kept."""
        self.assert_every_member_linted(*self.lint())

    def test_clippy_ahead_of_a_long_listing_is_found(self) -> None:
        """`clippy…` first, then 200 kB: the component is there (#1637).

        On main the clippy probe stopped at the first line and
        reported the component missing (exit 1).
        """
        done, asked = self.lint(
            RUSTUP_LONG,
            STUB_FIRST="clippy-x86_64-unknown-linux-gnu",
            STUB_LAST="rustfmt-x86_64-unknown-linux-gnu",
        )
        self.assertNotIn("component missing", done.stderr)
        self.assert_every_member_linted(done, asked)

    def test_rustfmt_ahead_of_a_long_listing_is_found(self) -> None:
        """`rustfmt…` first, then 200 kB, then clippy: both are there (#1637).

        On main the rustfmt probe stopped at the first line and
        reported the component missing (exit 1).
        """
        done, asked = self.lint(
            RUSTUP_LONG,
            STUB_FIRST="rustfmt-x86_64-unknown-linux-gnu",
            STUB_LAST="clippy-x86_64-unknown-linux-gnu",
        )
        self.assertNotIn("component missing", done.stderr)
        self.assert_every_member_linted(done, asked)


LIB = "src/lib.rs"
MEMBER = "crates/a/Cargo.toml"
PACKAGE = '[package]\nname = "a"\nversion = "0.1.0"\n'
# What every red row of the #1857 check prints, whatever it found.
UNSEEN = "lint:rust: suppression clippy's allow_attributes cannot see:\n"
REMEDY = '    #[expect(the_lint, reason = "why this code is right as written")]\n'
NOT_ASKED: list[str] = []
GROUP = "lint:rust: crate-level suppression of a whole clippy group: src/lib.rs\n"


class EverySuppressionIsAnExpect(unittest.TestCase):
    """The suppressions clippy cannot see are refused by name (#1857).

    clippy's allow_attributes refuses an outer `#[allow]` and nothing
    else. Each red row plants one form it cannot see and asserts the
    task names the file and the form, before clippy is asked anything;
    on main the task let each of them through to clippy. Each green row
    plants a form that stays legal and asserts clippy reached every
    member, the unchanged verdict of `EveryMemberIsLinted`.
    """

    lint = staticmethod(EveryMemberIsLinted.lint)

    def assert_refused(self, planted: dict[str, str], finding: str) -> None:
        """Assert the task exits 1 naming `finding`, and clippy is never asked."""
        done, asked = self.lint(planted=planted)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(NOT_ASKED, asked)
        self.assertIn(UNSEEN, done.stderr)
        self.assertIn(f"  {finding}\n", done.stderr)
        self.assertIn(REMEDY, done.stderr)

    def assert_passed(self, planted: dict[str, str]) -> None:
        """Assert the check lets it through and clippy reaches every member."""
        done, asked = self.lint(planted=planted)
        self.assertNotIn(UNSEEN, done.stderr)
        assert_every_member_linted(self, done, asked)

    def test_an_inner_clippy_allow_is_refused(self) -> None:
        """`#![allow(clippy::unwrap_used, reason = "x")]`: red, by line."""
        attr = '#![allow(clippy::unwrap_used, reason = "x")]'
        self.assert_refused({LIB: f"//! A crate.\n{attr}\n"}, f"{LIB}:2: {attr}")

    def test_an_inner_cfg_attr_allow_is_refused(self) -> None:
        """`#![cfg_attr(test, allow(clippy::unwrap_used))]`: red."""
        attr = "#![cfg_attr(test, allow(clippy::unwrap_used))]"
        self.assert_refused({LIB: f"{attr}\n"}, f"{LIB}:1: {attr}")

    def test_an_inner_rustc_allow_is_refused(self) -> None:
        """`#![allow(dead_code)]`, a rustc lint: red."""
        self.assert_refused(
            {LIB: "#![allow(dead_code)]\n"}, f"{LIB}:1: #![allow(dead_code)]"
        )

    def test_a_cfg_attr_rustfmt_broke_across_lines_is_refused(self) -> None:
        """The multi-line shape rustfmt writes for a long cfg_attr: red."""
        text = "#![cfg_attr(\n    test,\n    allow(clippy::unwrap_used)\n)]\n"
        self.assert_refused(
            {LIB: text}, f"{LIB}:1: #![cfg_attr( test, allow(clippy::unwrap_used) )]"
        )

    def test_an_inner_expect_passes(self) -> None:
        """`#![expect(clippy::unwrap_used, reason = "x")]`: green."""
        self.assert_passed({LIB: '#![expect(clippy::unwrap_used, reason = "x")]\n'})

    def test_a_manifest_rustc_allow_is_refused(self) -> None:
        """`[lints.rust] dead_code = "allow"`, the form that works today: red."""
        text = f'{PACKAGE}\n[lints.rust]\ndead_code = "allow"\n'
        self.assert_refused(
            {MEMBER: text}, f'{MEMBER}: [lints.rust] dead_code = "allow"'
        )

    def test_a_manifest_clippy_allow_is_refused(self) -> None:
        """`[lints.clippy] unwrap_used = "allow"`: red."""
        text = f'{PACKAGE}\n[lints.clippy]\nunwrap_used = "allow"\n'
        self.assert_refused(
            {MEMBER: text}, f'{MEMBER}: [lints.clippy] unwrap_used = "allow"'
        )

    def test_a_workspace_table_form_allow_is_refused(self) -> None:
        """`[workspace.lints.clippy] x = { level = "allow", priority = 1 }`: red."""
        text = (
            "[workspace]\n\n[workspace.lints.clippy]\n"
            'unwrap_used = { level = "allow", priority = 1 }\n'
        )
        self.assert_refused(
            {"Cargo.toml": text},
            'Cargo.toml: [workspace.lints.clippy] unwrap_used = "allow"',
        )

    def test_a_manifest_warn_or_deny_passes(self) -> None:
        """`x = "warn"`, `y = { level = "deny" }`, `workspace = true`: green."""
        text = (
            f"{PACKAGE}\n[lints]\nworkspace = true\n\n[lints.clippy]\n"
            'unwrap_used = "warn"\nexpect_used = { level = "deny", priority = 1 }\n'
        )
        self.assert_passed({MEMBER: text})

    def test_a_group_allow_is_refused_by_the_group_grep(self) -> None:
        """`#![allow(clippy::pedantic)]`: red with the group message."""
        done, asked = self.lint(planted={LIB: "#![allow(clippy::pedantic)]\n"})
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(NOT_ASKED, asked)
        self.assertIn(GROUP, done.stderr)

    def test_a_group_expect_is_refused_by_the_group_grep(self) -> None:
        """`#![expect(clippy::pedantic, reason = "x")]`: red, the same."""
        planted = {LIB: '#![expect(clippy::pedantic, reason = "x")]\n'}
        done, asked = self.lint(planted=planted)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual(NOT_ASKED, asked)
        self.assertIn(GROUP, done.stderr)

    def test_a_lint_named_after_a_group_passes_the_group_grep(self) -> None:
        """`clippy::cargo_common_metadata` is one lint, not the cargo group."""
        planted = {LIB: '#![expect(clippy::cargo_common_metadata, reason = "x")]\n'}
        self.assert_passed(planted)


class TheLadderNamesItsContradictions(unittest.TestCase):
    """The ladder excludes single_call_fn and tests_outside_test_module (#1995).

    Both were suppressed at every site in every Rust repository: the
    first contradicts `too_many_lines` and cannot see a caller in
    another crate, a wasm host or a macro; the second demands a module
    that gates nothing in the `tests/` targets where it fires. The row
    sources the real mise/clippy-ladder.sh, so a name dropped from the
    list, or a pgrx-only list reintroduced beside it, goes red here.
    """

    def ladder(self, call: str) -> str:
        """Source the belt's ladder and run one of its functions.

        Returns:
            What the function printed.

        """
        done = subprocess.run(
            [BASH, "-c", f'. "$1" && {call}', "ladder", str(BELT / "clippy-ladder.sh")],
            capture_output=True,
            text=True,
            env=hermetic.env(),
            check=False,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return done.stdout

    def test_the_ladder_allows_both_lints(self) -> None:
        """org_clippy_ladder prints an `-A` for each of the two."""
        words = self.ladder("org_clippy_ladder").split()
        allowed = [b for a, b in itertools.pairwise(words) if a == "-A"]
        self.assertIn("clippy::single_call_fn", allowed)
        self.assertIn("clippy::tests_outside_test_module", allowed)

    def test_the_count_is_thirteen(self) -> None:
        """org_clippy_contradiction_count prints 13."""
        self.assertEqual("13\n", self.ladder("org_clippy_contradiction_count"))


class TheStubOutlivesItsReader(unittest.TestCase):
    """The rustup stub answers 0 even when nobody reads it (#1623)."""

    def test_a_reader_that_has_gone_does_not_kill_the_stub(self) -> None:
        """Written into a pipe whose reader is closed, the stub still exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "rustup"
            stub.write_text(RUSTUP, encoding="utf-8")
            stub.chmod(0o755)
            read, write = os.pipe()
            os.close(read)
            try:
                done = subprocess.run(
                    [BASH, str(stub), "component", "list", "--installed"],
                    stdout=write,
                    env=hermetic.env(),
                    check=False,
                )
            finally:
                os.close(write)
        self.assertEqual(0, done.returncode)


# The rows below call mise/rust_lint.py's `main` in this process (#1911),
# in a planted tree, against a planted belt whose ladder, pgrx
# provisioning and Postgres pin check are stubs. cargo, rustup and mise
# are stubs on a PATH that holds nothing else, and each logs what it was
# asked. The workspace: two plain members, `a` and `b`; `ext`, a pgrx
# extension at majors 16 and 17; and `held`, named in CLIPPY_EXCLUDE.
# The fuzz root's metadata is STUB_FUZZ_METADATA's; STUB_FMT_RED names
# the one root whose format check fails.
STUB_CARGO = (
    LOCATE
    + """\
printf 'cargo %s\\n' "$*" >> "${STUB_LOG}"
if [[ $1 == clippy ]]; then
  printf '%s' "${STUB_CLIPPY_OUT:-}"
  [[ $2 != --fix ]] || exit "${STUB_FIX_STATUS:-0}"
  [[ $3 != "${STUB_RED:-none}@"* ]] || exit 101
  exit 0
fi
case "$1" in
  fmt)
    if [[ ${manifest} == "${STUB_FMT_RED:-none}" ]]; then
      echo "Diff in ${manifest}"
      exit 1
    fi
    printf '%s' "${STUB_FMT_OUT:-}"
    exit "${STUB_FMT_STATUS:-0}"
    ;;
  fetch) exit "${STUB_FETCH_STATUS:-0}" ;;
  metadata)
    if [[ ${manifest} == fuzz/* ]]; then
      cat "${STUB_FUZZ_METADATA}"
    else
      cat "${STUB_METADATA}"
    fi
    ;;
  *) exit 99 ;;
esac
"""
)
STUB_RUSTUP = """\
#!/usr/bin/env bash
printf '%b' "${STUB_COMPONENTS-clippy-host\\nrustfmt-host\\n}"
"""
STUB_MISE = """\
#!/usr/bin/env bash
printf 'mise %s\\n' "$*" >> "${STUB_LOG}"
echo '[]'
"""
# What the belt's three provisioning files are replaced with: a ladder
# of one flag and no contradiction, a prepare that exports a mark the
# stub cargo logs (or fails), a major's headers as a second mark, and a
# pin check that reads its input and exits as the row says.
STUB_LADDER = """
org_clippy_ladder() { [[ -z ${STUB_LADDER_FAILS:-} ]] || return 3; echo "-D warnings"; }
org_clippy_contradiction_count() { echo 0; }
"""
STUB_PGRX_ENV = """
. "${STUB_REAL_BELT}/pgrx-env.sh"
org_pgrx_darwin_prepare() {
  printf 'prepare %s\\n' "$*" >> "${STUB_LOG}"
  [[ -z ${STUB_PREPARE_FAILS:-} ]] || return 5
  export STUB_PREPARED=yes
}
org_pgrx_major_env() { export STUB_MAJOR="pg$1"; }
"""
STUB_PIN = """
import os
import sys

sys.stdin.read()
sys.exit(int(os.environ.get("STUB_PIN_STATUS", "0")))
"""
FIX_CARGO = (
    LOCATE
    + """\
marks="${STUB_PREPARED:-} ${STUB_MAJOR:-}"
printf 'cargo %s [%s]\\n' "$*" "${marks}" >> "${STUB_LOG}"
if [[ $1 == metadata ]]; then
  if [[ ${manifest} == fuzz/* ]]; then
    cat "${STUB_FUZZ_METADATA}"
  else
    cat "${STUB_METADATA}"
  fi
fi
[[ $1 != clippy || $5 != ext ]] || exit "${STUB_EXT_STATUS:-0}"
exit 0
"""
)


def member(name: str, *, pgrx: bool = False) -> dict[str, object]:
    """Write one package of cargo metadata.

    Returns:
        The package, a pgrx extension at pg16 and pg17 when asked.

    """
    return {
        "id": f"{name} 0.1.0",
        "name": name,
        "version": "0.1.0",
        "features": {"pg16": [], "pg17": []} if pgrx else {},
        "dependencies": [{"name": "pgrx"}] if pgrx else [],
        "manifest_path": f"/ws/{name}/Cargo.toml",
    }


def workspace(*packages: dict[str, object]) -> str:
    """Write cargo metadata for a workspace of the given members.

    Returns:
        The JSON cargo prints.

    """
    members: list[object] = [pkg["id"] for pkg in packages]
    document: dict[str, object] = {
        "workspace_root": "/ws",
        "workspace_members": members,
        "packages": list(packages),
    }
    return json.dumps(document)


FOUR = workspace(member("a"), member("b"), member("ext", pgrx=True), member("held"))
PLAIN = workspace(member("a"), member("b"), member("held"))
# The second root of a two-root tree: a cargo-fuzz crate, its own
# workspace (#2004).
FUZZ = workspace(member("parse"))
EVERY_CRATE: list[str] = ["a@0.1.0", "b@0.1.0", "parse@0.1.0"]
TWO_ROOTS = {"fuzz/Cargo.toml": "[workspace]\n", "fuzz/fuzz_targets/parse.rs": "\n"}
OUTSIDE = (
    "fuzz/Cargo.toml belongs to a workspace outside this tree: /elsewhere/Cargo.toml"
)
OUTSIDE_REMEDY = (
    "give it its own [workspace] table, or make it a member of a tracked workspace"
)
SKIP_MESSAGE = [
    "#![rustfmt::skip] exempts an entire file — never allowed",
    "#[rustfmt::skip] must carry a '// rustfmt-skip: <why>' marker above it",
    (
        "rustfmt has no #[expect] analogue, so the comment is the only"
        " thing a reviewer sees"
    ),
]


class Planted(unittest.TestCase):
    """A planted tree, a planted belt and the stubs, for rust_lint.py's rows."""

    task = "lint:rust"
    cargo = STUB_CARGO

    @override
    def setUp(self) -> None:
        """Plant the tree, the belt and the stubs."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tree = tmp / "repo"
        self.bins = tmp / "bin"
        self.bins.mkdir()
        for name, text in (
            ("cargo", self.cargo),
            ("rustup", STUB_RUSTUP),
            ("mise", STUB_MISE),
        ):
            (self.bins / name).write_text(text, encoding="utf-8")
            (self.bins / name).chmod(0o755)
        # The stubs' own interpreter; the rest of PATH is the system's,
        # which holds no cargo, rustup or mise.
        (self.bins / "bash").symlink_to(BASH)
        self.belt = hermetic.belt(
            tmp / "belt",
            stubs={
                "clippy-ladder.sh": STUB_LADDER,
                "pgrx-env.sh": STUB_PGRX_ENV,
                "pgrx_postgres.py": STUB_PIN,
            },
        )
        self.log = tmp / "log"
        self.metadata = tmp / "metadata.json"
        self.metadata.write_text(FOUR, encoding="utf-8")
        self.fuzz_metadata = tmp / "fuzz-metadata.json"
        self.fuzz_metadata.write_text(FUZZ, encoding="utf-8")
        self.env = hermetic.env(
            tmp / "home",
            PATH=os.pathsep.join((str(self.bins), "/usr/bin", "/bin")),
            MISE_TASK_NAME=self.task,
            ORG_FINDINGS="",
            ORG_PYTHON=sys.executable,
            CLIPPY_EXCLUDE="held",
            STUB_LOG=str(self.log),
            STUB_METADATA=str(self.metadata),
            STUB_FUZZ_METADATA=str(self.fuzz_metadata),
            STUB_REAL_BELT=str(BELT),
        )
        self.plant({"Cargo.toml": "[workspace]\n", "src/lib.rs": "\n"})

    def plant(self, files: dict[str, str]) -> None:
        """Write files into the tree and track them."""
        for name, text in files.items():
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_text(text, encoding="utf-8")
        if not (self.tree / ".git").exists():
            subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        subprocess.run([GIT, "add", "."], cwd=self.tree, env=self.env, check=True)

    def call(self, *argv: str, **stub: str) -> hermetic.Called:
        """Call rust_lint.py's main in the tree, from the planted belt.

        Returns:
            Its status and what it wrote.

        """
        with mock.patch.object(rust_lint, "BELT", self.belt):
            return hermetic.call_main(
                lambda: rust_lint.main(list(argv)),
                env=self.env | stub,
                cwd=self.tree,
            )

    def logged(self) -> list[str]:
        """Read what the stubs were asked.

        Returns:
            One line per call.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def assert_finding(self, done: hermetic.Called, status: int, *lines: str) -> None:
        """Assert the status, and the finding's lines in order on stderr."""
        self.assertEqual(status, done.returncode, done.stdout + done.stderr)
        head, *detail = lines
        want = f"{self.task}: {head}\n" + "".join(f"  {line}\n" for line in detail)
        self.assertIn(want, done.stderr)


class LintBeforeClippy(Planted):
    """Each of lint:rust's branches before clippy, its message asserted (#1911)."""

    def test_cargo_missing_is_red(self) -> None:
        """No cargo on PATH: the toolchain is broken, never a skip."""
        (self.bins / "cargo").unlink()
        done = self.call("lint")
        self.assert_finding(
            done, 1, "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        self.assertEqual(NOT_ASKED, self.logged())

    def test_clippy_missing_is_red(self) -> None:
        """A rustup that lists no clippy: the remedy names the pin."""
        done = self.call("lint", STUB_COMPONENTS="rustfmt-host\\n")
        self.assert_finding(
            done,
            1,
            "clippy component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
            "(installed with the toolchain, before tasks run — never mid-run)",
        )

    def test_no_rustup_reads_as_no_clippy(self) -> None:
        """No rustup: no component is installed."""
        (self.bins / "rustup").unlink()
        done = self.call("lint")
        self.assertEqual(1, done.returncode)
        self.assertIn("lint:rust: clippy component missing", done.stderr)

    def test_a_failing_rustup_reads_as_no_clippy(self) -> None:
        """A rustup that exits nonzero: what it printed is not believed."""
        (self.bins / "rustup").write_text(
            "#!/usr/bin/env bash\necho clippy-host\nexit 1\n", encoding="utf-8"
        )
        done = self.call("lint")
        self.assertEqual(1, done.returncode)
        self.assertIn("lint:rust: clippy component missing", done.stderr)

    def test_a_local_clippy_toml_is_red(self) -> None:
        """A tracked clippy.toml lowers the org's knobs: red, by name."""
        self.plant({"clippy.toml": "\n", "crates/a/rustfmt.toml": "\n"})
        done = self.call("lint")
        self.assert_finding(
            done,
            1,
            "repo-local Rust tool configuration is not the org's:",
            "clippy.toml",
            "crates/a/rustfmt.toml",
            "the knobs live in the canon's mise/clippy.toml and mise/rustfmt.toml,",
            "delivered through CLIPPY_CONF_DIR and rustfmt's --config-path",
            (
                "a setting this repo genuinely needs is argued into the canon,"
                " not kept locally"
            ),
        )

    def test_the_canon_holds_its_delivered_configs(self) -> None:
        """With the identity tracked, mise/clippy.toml is the belt's own (#746)."""
        self.plant({
            "security/identity.toml": "\n",
            "mise/clippy.toml": "\n",
            "mise/rustfmt.toml": "\n",
        })
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_consumer_mise_clippy_toml_is_red(self) -> None:
        """Without the identity, mise/clippy.toml is a shadow config."""
        self.plant({"mise/clippy.toml": "\n"})
        done = self.call("lint")
        self.assertEqual(1, done.returncode)
        self.assertIn("lint:rust: repo-local Rust tool configuration", done.stderr)
        self.assertIn("  mise/clippy.toml\n", done.stderr)

    def test_rustfmt_missing_is_red(self) -> None:
        """A rustup that lists no rustfmt: the remedy names the pin."""
        done = self.call("lint", STUB_COMPONENTS="clippy-host\\n")
        self.assert_finding(
            done,
            1,
            "rustfmt component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
        )

    def test_a_whole_file_skip_is_red(self) -> None:
        """`#![rustfmt::skip]`, indented: never allowed."""
        self.plant({"src/lib.rs": "  #![rustfmt::skip]\n"})
        done = self.call("lint")
        self.assert_finding(
            done,
            1,
            "formatting suppressed without a written decision: src/lib.rs(whole-file)",
            *SKIP_MESSAGE,
        )
        self.assertEqual(NOT_ASKED, self.logged())

    def test_a_narrow_skip_without_a_marker_is_red(self) -> None:
        """`#[rustfmt::skip]` with no `rustfmt-skip:` comment: red."""
        self.plant({"src/a.rs": "#[rustfmt::skip]\nfn f() {}\n", "src/b.rs": "\n"})
        done = self.call("lint")
        self.assert_finding(
            done,
            1,
            "formatting suppressed without a written decision: src/a.rs(no marker)",
            *SKIP_MESSAGE,
        )

    def test_a_narrow_skip_with_a_marker_passes(self) -> None:
        """`// rustfmt-skip: <why>` above it: green."""
        text = "// rustfmt-skip: a table\n#[rustfmt::skip]\nfn f() {}\n"
        self.plant({"src/a.rs": text})
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_vendored_skip_is_not_read(self) -> None:
        """vendor/ is not the repository's code; a deleted file is passed over."""
        self.plant({"vendor/x/lib.rs": "#![rustfmt::skip]\n", "src/gone.rs": "\n"})
        (self.tree / "src" / "gone.rs").unlink()
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)

    def test_a_rustfmt_warning_is_red(self) -> None:
        """A rustfmt warning with exit 0: an ignored option is a weaker format."""
        out = "Warning: Unknown configuration option `x`\n"
        done = self.call("lint", STUB_FMT_OUT=out)
        self.assert_finding(
            done,
            1,
            "Cargo.toml: rustfmt warned — an ignored option means a weaker format,"
            " silently",
        )
        self.assertIn(out, done.stderr)

    def test_unformatted_code_is_red(self) -> None:
        """A failing format check: the diff is shown and fix:rust named."""
        out = "Diff in src/lib.rs at line 1:\n"
        done = self.call("lint", STUB_FMT_OUT=out, STUB_FMT_STATUS="1")
        self.assert_finding(
            done,
            1,
            "Cargo.toml: formatting differs from the org's — run 'mise run fix:rust'",
        )
        self.assertIn(out, done.stderr)
        self.assertNotIn("formatting clean", done.stdout)

    def test_a_suppression_clippy_cannot_see_is_red(self) -> None:
        """rust_suppressions.py reads every tracked manifest and source."""
        self.plant({"src/lib.rs": "#![allow(dead_code)]\n"})
        done = self.call("lint")
        self.assertEqual(1, done.returncode)
        self.assertIn(UNSEEN, done.stderr)
        self.assertNotIn("cargo fetch", "\n".join(self.logged()))


class LintClippy(Planted):
    """Each of lint:rust's branches from the ladder on, its message asserted."""

    def test_a_failing_ladder_keeps_its_status(self) -> None:
        """The ladder cannot be read: nothing is linted."""
        done = self.call("lint", STUB_LADDER_FAILS="1")
        self.assertEqual(3, done.returncode)
        self.assertNotIn("cargo fetch", "\n".join(self.logged()))

    def test_a_failing_fetch_keeps_its_status(self) -> None:
        """A failing `cargo fetch`: its status, and no metadata read."""
        done = self.call("lint", STUB_FETCH_STATUS="7")
        self.assertEqual(7, done.returncode)
        self.assertEqual(
            "cargo fetch --locked --manifest-path Cargo.toml", self.logged()[-1]
        )

    def test_unreadable_metadata_is_red(self) -> None:
        """Metadata that is not JSON: gate_surface.py's status, nothing linted.

        The finding is gate_surface.py's, on its own stderr, which this
        process does not capture; test_gate_surface.py asserts it.
        """
        self.metadata.write_text("not json", encoding="utf-8")
        done = self.call("lint")
        self.assertEqual(1, done.returncode)
        self.assertNotIn("clippy", "\n".join(self.logged()))

    def test_a_workspace_of_no_member_is_red(self) -> None:
        """Metadata naming no member: never a pass over nothing."""
        self.metadata.write_text(workspace(), encoding="utf-8")
        done = self.call("lint")
        self.assert_finding(
            done,
            1,
            "Cargo.toml: a Cargo.toml is tracked but cargo metadata names no"
            " workspace member",
        )

    def test_a_clean_workspace_prints_its_population(self) -> None:
        """Each plain member is linted; the extension and the excluded are named."""
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)
        said = [
            "lint:rust: Cargo.toml: formatting clean",
            "lint:rust: Cargo.toml: 2 crate(s) linted: a b",
            "lint:rust: Cargo.toml: pgrx extension(s), linted by lint:pg-clippy: ext",
            "lint:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
            (
                "lint:rust: clean at every group"
                " (restriction minus 0 named contradictions)"
            ),
        ]
        self.assertEqual(said, done.stdout.splitlines())
        clippy = (
            "--no-deps --all-targets --all-features --locked --keep-going"
            " --manifest-path Cargo.toml"
        )
        fmt = "--manifest-path Cargo.toml -- --config-path"
        want = [
            "cargo fetch --locked --manifest-path Cargo.toml",
            f"cargo fmt --all --check {fmt} {self.belt}/rustfmt.toml",
            (
                "cargo metadata --no-deps --format-version 1 --locked"
                " --manifest-path Cargo.toml"
            ),
            f"cargo clippy -p a@0.1.0 {clippy} -- -D warnings",
            f"cargo clippy -p b@0.1.0 {clippy} -- -D warnings",
        ]
        self.assertEqual(want, self.logged())

    def test_clippy_features_replace_all_features(self) -> None:
        """CLIPPY_FEATURES is split into words, in place of --all-features."""
        done = self.call("lint", CLIPPY_FEATURES="--no-default-features  --features x")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "cargo clippy -p a@0.1.0 --no-deps --all-targets --no-default-features"
            " --features x --locked --keep-going --manifest-path Cargo.toml"
            " -- -D warnings",
            self.logged(),
        )

    def test_a_red_member_is_named_with_clippys_status(self) -> None:
        """A red `a`: every member is still asked, and cargo's 101 is kept."""
        done = self.call("lint", STUB_RED="a")
        self.assert_finding(done, 101, "red: a (Cargo.toml)")
        self.assertIn("lint:rust: Cargo.toml: 2 crate(s) linted: a b\n", done.stdout)
        self.assertEqual(2, sum("cargo clippy" in line for line in self.logged()))

    def test_clippy_output_is_shown(self) -> None:
        """What clippy prints reaches stdout as it comes."""
        done = self.call("lint", STUB_CLIPPY_OUT="warning: a note\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(2, done.stdout.count("warning: a note\n"))

    def test_a_feature_some_crate_lacks_is_red(self) -> None:
        """Cargo's `does not contain this feature`: the CLIPPY_FEATURES finding."""
        out = "error: package `a` does not contain this feature: x\n"
        done = self.call("lint", STUB_CLIPPY_OUT=out)
        self.assert_finding(
            done,
            1,
            (
                "Cargo.toml: CLIPPY_FEATURES names a feature some crate in this"
                " workspace lacks"
            ),
            (
                "every crate is linted as its own package (#726),"
                " so the value has to be one"
            ),
            (
                "each member accepts — --all-features, or --no-default-features"
                " — and a crate"
            ),
            "that genuinely cannot take it is named in CLIPPY_EXCLUDE instead",
        )

    def test_an_unknown_lint_name_is_red(self) -> None:
        """E0602: the exclusion list names a lint this toolchain lacks."""
        done = self.call("lint", STUB_CLIPPY_OUT="warning[E0602]: unknown lint\n")
        self.assert_finding(
            done,
            1,
            (
                "Cargo.toml: a lint name above is unknown to this toolchain"
                " (see E0602/renamed above)"
            ),
            (
                "clippy renamed or removed it — update the exclusion list"
                " in mise/config.toml"
            ),
        )

    def test_a_renamed_lint_name_is_red(self) -> None:
        """`has been renamed to`: the same finding."""
        out = "warning: lint `x` has been renamed to `y`\n"
        done = self.call("lint", STUB_CLIPPY_OUT=out)
        self.assertEqual(1, done.returncode)
        self.assertIn(
            "lint:rust: Cargo.toml: a lint name above is unknown", done.stderr
        )

    def test_no_extension_and_no_exclusion_print_neither_line(self) -> None:
        """Only the lines that name something are printed."""
        self.metadata.write_text(workspace(member("a")), encoding="utf-8")
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("pgrx extension", done.stdout)
        self.assertNotIn("excluded", done.stdout)

    def test_no_subcommand_is_a_usage_error(self) -> None:
        """No task named: status 2, the usage."""
        done = self.call()
        self.assert_finding(done, 2, "usage: rust_lint.py lint|fix")


class FixBranches(Planted):
    """Each of fix:rust's branches, its message asserted (#1911)."""

    task = "fix:rust"
    cargo = FIX_CARGO

    def test_cargo_missing_is_red(self) -> None:
        """No cargo on PATH: red, as the gate leg."""
        (self.bins / "cargo").unlink()
        done = self.call("fix")
        self.assert_finding(
            done, 1, "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )

    def test_each_missing_component_is_red(self) -> None:
        """clippy, then rustfmt, each named."""
        for listed, missing in (
            ("rustfmt-host\\n", "clippy"),
            ("clippy-host\\n", "rustfmt"),
        ):
            with self.subTest(missing=missing):
                done = self.call("fix", STUB_COMPONENTS=listed)
                self.assert_finding(
                    done,
                    1,
                    f"{missing} component missing"
                    " — declare it with the rust pin in mise.toml",
                )

    def test_a_failing_format_keeps_its_status(self) -> None:
        """A failing `cargo fmt`: its status, and nothing fixed."""
        (self.bins / "cargo").write_text(LOCATE + "exit 4\n", encoding="utf-8")
        done = self.call("fix")
        self.assertEqual(4, done.returncode)
        self.assertNotIn("formatted", done.stdout)

    def test_a_failing_ladder_keeps_its_status(self) -> None:
        """The ladder cannot be read: nothing formatted or fixed."""
        done = self.call("fix", STUB_LADDER_FAILS="1")
        self.assertEqual(3, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(NOT_ASKED, self.logged())

    def test_a_workspace_without_extensions(self) -> None:
        """One --workspace pass, the excluded held out and named."""
        self.metadata.write_text(PLAIN, encoding="utf-8")
        done = self.call("fix")
        self.assertEqual(0, done.returncode, done.stderr)
        at = "--manifest-path Cargo.toml"
        want = [
            f"cargo fmt --all {at} -- --config-path {self.belt}/rustfmt.toml [ ]",
            f"cargo fetch --locked {at} [ ]",
            f"cargo metadata --no-deps --format-version 1 --locked {at} [ ]",
            (
                "cargo clippy --fix --allow-dirty --workspace --exclude held"
                f" --all-features --locked {at} -- -D warnings [ ]"
            ),
        ]
        self.assertEqual(want, self.logged())
        said = [
            "fix:rust: Cargo.toml: formatted",
            "fix:rust: Cargo.toml: a b rewritten where clippy could",
            "fix:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
            "fix:rust: applied what is machine-applicable — the rest is judgement",
        ]
        self.assertEqual(said, done.stdout.splitlines())

    def test_nothing_left_to_fix_says_none(self) -> None:
        """Every member excluded: ` none`, and no exclusion of an extension."""
        self.metadata.write_text(workspace(member("held")), encoding="utf-8")
        done = self.call("fix")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(
            "fix:rust: Cargo.toml: none rewritten where clippy could\n", done.stdout
        )

    def test_a_failing_fix_pass_keeps_its_status(self) -> None:
        """The workspace pass fails: its status, no extension pass."""
        self.plant({})
        (self.bins / "cargo").write_text(
            LOCATE + "[[ $1 != clippy ]] || exit 6\n"
            '[[ $1 != metadata ]] || cat "${STUB_METADATA}"\n',
            encoding="utf-8",
        )
        done = self.call("fix")
        self.assertEqual(6, done.returncode)
        self.assertNotIn("prepare 16,17", self.logged())

    def test_the_extension_is_fixed_at_its_highest_major(self) -> None:
        """The extension is held out of the workspace pass and fixed at pg17.

        What the prepare exported reaches the compile, and so does the
        major's own environment.
        """
        done = self.call("fix")
        self.assertEqual(0, done.returncode, done.stderr)
        own = "--no-deps --all-targets --locked --no-default-features"
        want = [
            (
                "cargo clippy --fix --allow-dirty --workspace --exclude held"
                " --exclude ext --all-features --locked --manifest-path Cargo.toml"
                " -- -D warnings [ ]"
            ),
            "prepare 17",
            "mise ls aqua:theseus-rs/postgresql-binaries --json",
            (
                "cargo clippy --fix --allow-dirty -p ext"
                f" {own} --features pg17 --manifest-path Cargo.toml"
                " -- -D warnings [yes pg17]"
            ),
        ]
        self.assertEqual(want, self.logged()[3:])
        said = [
            "fix:rust: Cargo.toml: formatted",
            "fix:rust: Cargo.toml: a b rewritten where clippy could",
            "fix:rust: Cargo.toml: pgrx extension(s) fixed: ext(pg17)",
            "fix:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
            "fix:rust: applied what is machine-applicable — the rest is judgement",
        ]
        self.assertEqual(said, done.stdout.splitlines())

    def test_a_failing_prepare_keeps_its_status(self) -> None:
        """The prepare fails: its status, and the extension never compiled."""
        done = self.call("fix", STUB_PREPARE_FAILS="1")
        self.assertEqual(5, done.returncode)
        self.assertEqual("prepare 17", self.logged()[-1])

    def test_a_failing_pin_check_keeps_its_status(self) -> None:
        """A major with no Postgres pinned: the check's status."""
        done = self.call("fix", STUB_PIN_STATUS="8")
        self.assertEqual(8, done.returncode)
        self.assertNotIn("ext(pg17)", done.stdout)

    def test_a_failing_extension_fix_keeps_its_status(self) -> None:
        """The extension's own pass fails: its status."""
        done = self.call("fix", STUB_EXT_STATUS="9")
        self.assertEqual(9, done.returncode)
        self.assertNotIn("ext(pg17)", done.stdout)

    def test_a_failing_union_keeps_its_status(self) -> None:
        """The majors cannot be derived: no pass runs."""
        self.belt.joinpath("pgrx-env.sh").write_text(
            STUB_PGRX_ENV + "org_pgrx_union() { return 4; }\n", encoding="utf-8"
        )
        done = self.call("fix")
        self.assertEqual(4, done.returncode)
        self.assertNotIn("--fix", "\n".join(self.logged()))

    def test_a_failing_highest_keeps_its_status(self) -> None:
        """Each extension's major cannot be derived: no pass runs."""
        self.belt.joinpath("pgrx-env.sh").write_text(
            STUB_PGRX_ENV + "org_pgrx_highest() { return 2; }\n", encoding="utf-8"
        )
        done = self.call("fix")
        self.assertEqual(2, done.returncode)
        self.assertNotIn("--fix", "\n".join(self.logged()))


class EveryWorkspace(Planted):
    """A tree of two workspaces: each is formatted, linted and fixed (#2004).

    `Cargo.toml` holds `a`, `b`, `ext` and `held`; `fuzz/Cargo.toml` is a
    cargo-fuzz crate, `parse`, its own workspace root. Before #2004 the
    task ran cargo in the root workspace alone, and `parse` was never
    formatted or linted.
    """

    @override
    def setUp(self) -> None:
        """Plant the fuzz crate as the tree's second root."""
        super().setUp()
        self.plant(TWO_ROOTS)

    def asked(self, verb: str) -> list[str]:
        """Read the stub log's calls of one cargo subcommand.

        Returns:
            Those lines, in order.

        """
        return [line for line in self.logged() if line.startswith(f"cargo {verb} ")]

    def test_each_root_is_formatted_and_linted(self) -> None:
        """An fmt and a clippy call for each root, one population line each."""
        done = self.call("lint")
        self.assertEqual(0, done.returncode, done.stderr)
        fmt = self.asked("fmt")
        self.assertEqual(2, len(fmt))
        self.assertIn("--manifest-path Cargo.toml --", fmt[0])
        self.assertIn("--manifest-path fuzz/Cargo.toml --", fmt[1])
        clippy = [line.split()[3] for line in self.asked("clippy")]
        self.assertEqual(EVERY_CRATE, clippy)
        self.assertIn("--manifest-path fuzz/Cargo.toml --", self.asked("clippy")[2])
        self.assertIn("lint:rust: Cargo.toml: 2 crate(s) linted: a b\n", done.stdout)
        self.assertIn(
            "lint:rust: fuzz/Cargo.toml: 1 crate(s) linted: parse\n", done.stdout
        )

    def test_an_unformatted_fuzz_root_is_red(self) -> None:
        """A format check refused for fuzz/Cargo.toml only: red, naming it."""
        done = self.call("lint", STUB_FMT_RED="fuzz/Cargo.toml")
        self.assert_finding(
            done,
            1,
            "fuzz/Cargo.toml: formatting differs from the org's"
            " — run 'mise run fix:rust'",
        )
        self.assertIn("lint:rust: Cargo.toml: 2 crate(s) linted: a b\n", done.stdout)

    def test_a_red_fuzz_crate_leaves_the_root_linted(self) -> None:
        """Clippy refuses `parse` only: red names it, a and b were asked."""
        done = self.call("lint", STUB_RED="parse")
        self.assert_finding(done, 101, "red: parse (fuzz/Cargo.toml)")
        clippy = [line.split()[3] for line in self.asked("clippy")]
        self.assertEqual(EVERY_CRATE, clippy)

    def test_a_root_outside_the_tree_is_red(self) -> None:
        """locate-project names a workspace above the checkout: red, exact."""
        done = self.call("lint", STUB_FUZZ_ROOT="/elsewhere/Cargo.toml")
        self.assert_finding(done, 1, OUTSIDE, OUTSIDE_REMEDY)
        self.assertEqual(NOT_ASKED, self.asked("clippy"))

    def test_a_failing_locate_project_says_cargos_words(self) -> None:
        """locate-project exits nonzero: cargo's own text is in the output."""
        done = self.call("lint", STUB_LOCATE_FAILS="1")
        self.assertEqual(1, done.returncode)
        self.assertIn("error: planted: Cargo.toml could not be read", done.stderr)
        self.assertEqual(NOT_ASKED, self.asked("clippy"))

    def test_fix_rust_formats_and_fixes_each_root(self) -> None:
        """fix:rust: cargo fmt and clippy --fix for both roots."""
        (self.bins / "cargo").write_text(FIX_CARGO, encoding="utf-8")
        done = self.call("fix")
        self.assertEqual(0, done.returncode, done.stderr)
        fmt = self.asked("fmt")
        self.assertEqual(2, len(fmt))
        self.assertIn("--manifest-path fuzz/Cargo.toml --", fmt[1])
        fixed = [line for line in self.asked("clippy") if "--workspace" in line.split()]
        self.assertEqual(2, len(fixed))
        self.assertIn("--manifest-path Cargo.toml --", fixed[0])
        self.assertIn("--manifest-path fuzz/Cargo.toml --", fixed[1])
        self.assertIn(
            "fix:rust: fuzz/Cargo.toml: parse rewritten where clippy could\n",
            done.stdout,
        )


if __name__ == "__main__":
    unittest.main()

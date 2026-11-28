"""lint:rust lints every workspace member, past a red one (#726).

Linted as one workspace, the first crate clippy refused ended the run:
the members after it were never linted, and their findings surfaced
only once the first was fixed, one crate per round trip. lint:rust now
lints each member as its own package and reports every red crate by
name, with cargo's own exit status.

The rows up to `Planted` call belt.rust_lint's `main` in this process,
standing in a planted git tree. The workspace is edtf's, from the `cargo metadata`
captured in release/testdata/edtf-cargo-metadata.json and read by the
belt's real belt.gate_surface. cargo and rustup are stubs: clippy
refuses the first member and passes the rest, and the stub records each
crate it was asked to lint. clippy's verdict on a crate is clippy's to
prove; the row is about which crates are asked.

The task's body after its guard is `belt rust-lint` (#1911). The
rows from `Planted` on call its `main` in this process too, one row per
branch of lint:rust and of fix:rust with its message asserted, against
a planted belt whose ladder and pgrx provisioning are stubs.
"""

import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple, Protocol

import pytest

from belt import pgrx_postgres, rust_lint, rust_workspaces
from belt.typed_pytest import fixture
from tests import support

BELT = support.MISE
METADATA = (
    support.CANON / "tests" / "release" / "testdata"
) / "edtf-cargo-metadata.json"
BASH = support.tool("bash")
GIT = support.tool("git")
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


class Linted(NamedTuple):
    """What one in-process run of lint:rust returned and printed."""

    returncode: int
    stdout: str
    stderr: str


class Lint(Protocol):
    """Run lint:rust over the planted workspace."""

    def __call__(
        self, rustup: str = ..., planted: dict[str, str] | None = ..., **stub: str
    ) -> tuple[Linted, list[str]]:
        """Plant the tree and stubs, then lint it in this process."""
        ...


def assert_every_member_linted(done: Linted, asked: list[str]) -> None:
    """Assert clippy reached every member and named the red one."""
    assert done.returncode == 101, done.stdout + done.stderr
    want = [f"{m}@1.3.3" for m in MEMBERS]
    assert want == asked
    linted = f"lint:rust: Cargo.toml: 6 crate(s) linted: {' '.join(MEMBERS)}\n"
    assert linted in done.stdout
    assert "lint:rust: red: edtf-core (Cargo.toml)\n" in done.stderr


# One red crate does not hide the rest (#726).
@fixture
def lint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
) -> Lint:
    """Give each row lint:rust over a planted workspace, run in this process.

    `planted` maps a tracked path to its text, written over the
    default `Cargo.toml` and `src/lib.rs`.

    Returns:
        The runner: it answers what lint:rust returned and printed, and
        the crates clippy was asked.

    """

    def run(
        rustup: str = RUSTUP, planted: dict[str, str] | None = None, **stub: str
    ) -> tuple[Linted, list[str]]:
        files = {"Cargo.toml": "[workspace]\n", "src/lib.rs": "\n"}
        files |= planted or {}
        tree = tmp_path / "repo"
        for name, text in files.items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text, encoding="utf-8")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, text in (("cargo", CARGO), ("rustup", rustup)):
            (bindir / name).write_text(text, encoding="utf-8")
            (bindir / name).chmod(0o755)
        log = tmp_path / "clippy.log"
        environ = env(tmp_path / "home", ORG_BELT_DIR=str(BELT))
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=environ, check=True)
        subprocess.run([GIT, "add", "."], cwd=tree, env=environ, check=True)
        environ["PATH"] = f"{bindir}{os.pathsep}{environ['PATH']}"
        environ |= {
            "STUB_METADATA": str(METADATA),
            "CLIPPY_LOG": str(log),
            "MISE_TASK_NAME": "lint:rust",
            "ORG_FINDINGS": "",
            "BASH_ENV": str(BELT / "findings.sh"),
        }
        environ |= stub
        monkeypatch.chdir(tree)
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in environ.items():
            monkeypatch.setenv(name, value)
        # cargo as the helpers resolved it at import, before PATH had the stub.
        monkeypatch.setattr(rust_workspaces, "CARGO", str(bindir / "cargo"))
        capfd.readouterr()
        status = rust_lint.main(["lint"])
        out, err = capfd.readouterr()
        asked = log.read_text(encoding="utf-8").split() if log.exists() else []
        return Linted(status, out, err), asked

    return run


def test_a_red_first_member_leaves_the_rest_linted(lint: Lint) -> None:
    """All six members are asked; the red one is named; cargo's 101 is kept."""
    assert_every_member_linted(*lint())


def test_clippy_ahead_of_a_long_listing_is_found(lint: Lint) -> None:
    """`clippy…` first, then 200 kB: the component is there (#1637).

    On main the clippy probe stopped at the first line and
    reported the component missing (exit 1).
    """
    done, asked = lint(
        RUSTUP_LONG,
        STUB_FIRST="clippy-x86_64-unknown-linux-gnu",
        STUB_LAST="rustfmt-x86_64-unknown-linux-gnu",
    )
    assert "component missing" not in done.stderr
    assert_every_member_linted(done, asked)


def test_rustfmt_ahead_of_a_long_listing_is_found(lint: Lint) -> None:
    """`rustfmt…` first, then 200 kB, then clippy: both are there (#1637).

    On main the rustfmt probe stopped at the first line and
    reported the component missing (exit 1).
    """
    done, asked = lint(
        RUSTUP_LONG,
        STUB_FIRST="rustfmt-x86_64-unknown-linux-gnu",
        STUB_LAST="clippy-x86_64-unknown-linux-gnu",
    )
    assert "component missing" not in done.stderr
    assert_every_member_linted(done, asked)


LIB = "src/lib.rs"
MEMBER = "crates/a/Cargo.toml"
PACKAGE = '[package]\nname = "a"\nversion = "0.1.0"\n'
# What every red row of the #1857 check prints, whatever it found.
UNSEEN = "lint:rust: suppression clippy's allow_attributes cannot see:\n"
REMEDY = '    #[expect(the_lint, reason = "why this code is right as written")]\n'
NOT_ASKED: list[str] = []
GROUP = "lint:rust: crate-level suppression of a whole clippy group: src/lib.rs\n"


# The suppressions clippy cannot see are refused by name (#1857).
#
# clippy's allow_attributes refuses an outer `#[allow]` and nothing
# else. Each red row plants one form it cannot see and asserts the
# task names the file and the form, before clippy is asked anything;
# on main the task let each of them through to clippy. Each green row
# plants a form that stays legal and asserts clippy reached every
# member, the unchanged verdict of `EveryMemberIsLinted`.
def assert_refused(lint: Lint, planted: dict[str, str], finding: str) -> None:
    """Assert the task exits 1 naming `finding`, and clippy is never asked."""
    done, asked = lint(planted=planted)
    assert done.returncode == 1, done.stdout + done.stderr
    assert asked == NOT_ASKED
    assert UNSEEN in done.stderr
    assert f"  {finding}\n" in done.stderr
    assert REMEDY in done.stderr


def assert_passed(lint: Lint, planted: dict[str, str]) -> None:
    """Assert the check lets it through and clippy reaches every member."""
    done, asked = lint(planted=planted)
    assert UNSEEN not in done.stderr
    assert_every_member_linted(done, asked)


def test_an_inner_clippy_allow_is_refused(lint: Lint) -> None:
    """`#![allow(clippy::unwrap_used, reason = "x")]`: red, by line."""
    attr = '#![allow(clippy::unwrap_used, reason = "x")]'
    assert_refused(lint, {LIB: f"//! A crate.\n{attr}\n"}, f"{LIB}:2: {attr}")


def test_an_inner_cfg_attr_allow_is_refused(lint: Lint) -> None:
    """`#![cfg_attr(test, allow(clippy::unwrap_used))]`: red."""
    attr = "#![cfg_attr(test, allow(clippy::unwrap_used))]"
    assert_refused(lint, {LIB: f"{attr}\n"}, f"{LIB}:1: {attr}")


def test_an_inner_rustc_allow_is_refused(lint: Lint) -> None:
    """`#![allow(dead_code)]`, a rustc lint: red."""
    assert_refused(
        lint, {LIB: "#![allow(dead_code)]\n"}, f"{LIB}:1: #![allow(dead_code)]"
    )


def test_a_cfg_attr_rustfmt_broke_across_lines_is_refused(lint: Lint) -> None:
    """The multi-line shape rustfmt writes for a long cfg_attr: red."""
    text = "#![cfg_attr(\n    test,\n    allow(clippy::unwrap_used)\n)]\n"
    assert_refused(
        lint, {LIB: text}, f"{LIB}:1: #![cfg_attr( test, allow(clippy::unwrap_used) )]"
    )


def test_an_inner_expect_passes(lint: Lint) -> None:
    """`#![expect(clippy::unwrap_used, reason = "x")]`: green."""
    assert_passed(lint, {LIB: '#![expect(clippy::unwrap_used, reason = "x")]\n'})


def test_a_manifest_rustc_allow_is_refused(lint: Lint) -> None:
    """`[lints.rust] dead_code = "allow"`, the form that works today: red."""
    text = f'{PACKAGE}\n[lints.rust]\ndead_code = "allow"\n'
    assert_refused(lint, {MEMBER: text}, f'{MEMBER}: [lints.rust] dead_code = "allow"')


def test_a_manifest_clippy_allow_is_refused(lint: Lint) -> None:
    """`[lints.clippy] unwrap_used = "allow"`: red."""
    text = f'{PACKAGE}\n[lints.clippy]\nunwrap_used = "allow"\n'
    assert_refused(
        lint, {MEMBER: text}, f'{MEMBER}: [lints.clippy] unwrap_used = "allow"'
    )


def test_a_workspace_table_form_allow_is_refused(lint: Lint) -> None:
    """`[workspace.lints.clippy] x = { level = "allow", priority = 1 }`: red."""
    text = (
        "[workspace]\n\n[workspace.lints.clippy]\n"
        'unwrap_used = { level = "allow", priority = 1 }\n'
    )
    assert_refused(
        lint,
        {"Cargo.toml": text},
        'Cargo.toml: [workspace.lints.clippy] unwrap_used = "allow"',
    )


def test_a_manifest_warn_or_deny_passes(lint: Lint) -> None:
    """`x = "warn"`, `y = { level = "deny" }`, `workspace = true`: green."""
    text = (
        f"{PACKAGE}\n[lints]\nworkspace = true\n\n[lints.clippy]\n"
        'unwrap_used = "warn"\nexpect_used = { level = "deny", priority = 1 }\n'
    )
    assert_passed(lint, {MEMBER: text})


def test_a_group_allow_is_refused_by_the_group_grep(lint: Lint) -> None:
    """`#![allow(clippy::pedantic)]`: red with the group message."""
    done, asked = lint(planted={LIB: "#![allow(clippy::pedantic)]\n"})
    assert done.returncode == 1, done.stdout + done.stderr
    assert asked == NOT_ASKED
    assert GROUP in done.stderr


def test_a_group_expect_is_refused_by_the_group_grep(lint: Lint) -> None:
    """`#![expect(clippy::pedantic, reason = "x")]`: red, the same."""
    planted = {LIB: '#![expect(clippy::pedantic, reason = "x")]\n'}
    done, asked = lint(planted=planted)
    assert done.returncode == 1, done.stdout + done.stderr
    assert asked == NOT_ASKED
    assert GROUP in done.stderr


def test_a_lint_named_after_a_group_passes_the_group_grep(lint: Lint) -> None:
    """`clippy::cargo_common_metadata` is one lint, not the cargo group."""
    planted = {LIB: '#![expect(clippy::cargo_common_metadata, reason = "x")]\n'}
    assert_passed(lint, planted)


# The ladder excludes single_call_fn and tests_outside_test_module (#1995).
#
# Both were suppressed at every site in every Rust repository: the
# first contradicts `too_many_lines` and cannot see a caller in
# another crate, a wasm host or a macro; the second demands a module
# that gates nothing in the `tests/` targets where it fires. The row
# sources the real mise/clippy-ladder.sh, so a name dropped from the
# list, or a pgrx-only list reintroduced beside it, goes red here.
def ladder(call: str, *, env: support.Env) -> str:
    """Source the belt's ladder and run one of its functions.

    Returns:
        What the function printed.

    """
    done = subprocess.run(
        [BASH, "-c", f'. "$1" && {call}', "ladder", str(BELT / "clippy-ladder.sh")],
        capture_output=True,
        text=True,
        env=env(),
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def test_the_ladder_allows_both_lints(env: support.Env) -> None:
    """org_clippy_ladder prints an `-A` for each of the two."""
    words = ladder("org_clippy_ladder", env=env).split()
    allowed = [b for a, b in itertools.pairwise(words) if a == "-A"]
    assert "clippy::single_call_fn" in allowed
    assert "clippy::tests_outside_test_module" in allowed


def test_the_count_is_thirteen(env: support.Env) -> None:
    """org_clippy_contradiction_count prints 13."""
    assert ladder("org_clippy_contradiction_count", env=env) == "13\n"


# The rustup stub answers 0 even when nobody reads it (#1623).
def test_a_reader_that_has_gone_does_not_kill_the_stub(env: support.Env) -> None:
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
                env=env(),
                check=False,
            )
        finally:
            os.close(write)
    assert done.returncode == 0


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
flags="${manifest} ${RUSTFLAGS-unset}"
[[ $1 != clippy ]] || printf '%s\\n' "${flags}" >> "${STUB_LOG}.flags"
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


def stub_pin(argv: list[str]) -> int:
    """Stand in for `belt pgrx-postgres`: read the listing, answer STUB_PIN_STATUS.

    Returns:
        The planted status, 0 unless the row names one.

    """
    del argv
    sys.stdin.read()
    return int(os.environ.get("STUB_PIN_STATUS", "0"))


FIX_CARGO = (
    LOCATE
    + """\
marks="${STUB_PREPARED:-} ${STUB_MAJOR:-}"
printf 'cargo %s [%s]\\n' "$*" "${marks}" >> "${STUB_LOG}"
flags="${manifest} ${RUSTFLAGS-unset}"
[[ $1 != clippy ]] || printf '%s\\n' "${flags}" >> "${STUB_LOG}.flags"
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
# The same root carrying the marker `cargo fuzz init` writes, which
# cargo-fuzz requires: built with `--cfg fuzzing`, as cargo-fuzz builds
# it (#2093).
MARKED = {
    "fuzz/Cargo.toml": (
        '[package]\nname = "parse"\n\n'
        "[package.metadata]\ncargo-fuzz = true\n\n[workspace]\n"
    )
}
# What each root's clippy calls saw in RUSTFLAGS: the root's first.
UNMARKED = ["Cargo.toml unset", "Cargo.toml unset", "fuzz/Cargo.toml unset"]
FUZZING = ["Cargo.toml unset", "Cargo.toml unset", "fuzz/Cargo.toml --cfg fuzzing"]
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


class Planted:
    """A planted tree, a planted belt and the stubs, for rust_lint.py's rows."""

    task = "lint:rust"
    cargo = STUB_CARGO

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        belt: support.Belt,
        env: support.Env,
    ) -> None:
        """Plant the tree, the belt and the stubs."""
        self.plant_belt = belt
        self.make_env = env
        # `belt pgrx-postgres`, which the registration calls in process.
        monkeypatch.setattr(pgrx_postgres, "main", stub_pin)
        tmp = tmp_path
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
        self.belt = self.plant_belt(
            tmp / "belt",
            stubs={
                "clippy-ladder.sh": STUB_LADDER,
                "pgrx-env.sh": STUB_PGRX_ENV,
            },
        )
        self.log = tmp / "log"
        self.metadata = tmp / "metadata.json"
        self.metadata.write_text(FOUR, encoding="utf-8")
        self.fuzz_metadata = tmp / "fuzz-metadata.json"
        self.fuzz_metadata.write_text(FUZZ, encoding="utf-8")
        self.env = self.make_env(
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

    def enter(self, monkeypatch: pytest.MonkeyPatch, **stub: str) -> None:
        """Stand in the tree from the planted belt, the row's stubs set.

        The derivation resolves cargo when it is imported; here, the
        stub on the row's PATH is the cargo it asks.
        """
        monkeypatch.setattr(rust_workspaces, "CARGO", str(self.bins / "cargo"))
        for name, value in (self.env | {"ORG_BELT_DIR": str(self.belt)} | stub).items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)

    def logged(self) -> list[str]:
        """Read what the stubs were asked.

        Returns:
            One line per call.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def finding(self, *lines: str) -> str:
        """Say a finding as the task writes it to stderr.

        Returns:
            Its head line, then each detail line indented.

        """
        head, *detail = lines
        return f"{self.task}: {head}\n" + "".join(f"  {line}\n" for line in detail)


@fixture
def planted_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    belt: support.Belt,
    env: support.Env,
) -> Planted:
    """Give each row its own Planted."""
    return Planted(tmp_path, monkeypatch, belt=belt, env=env)


def test_cargo_missing_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cargo on PATH: the toolchain is broken, never a skip."""
    (planted_state.bins / "cargo").unlink()
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        in err
    )
    assert planted_state.logged() == NOT_ASKED


def test_clippy_missing_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rustup that lists no clippy: the remedy names the pin."""
    planted_state.enter(monkeypatch, STUB_COMPONENTS="rustfmt-host\\n")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "clippy component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
            "(installed with the toolchain, before tasks run — never mid-run)",
        )
        in err
    )


def test_no_rustup_reads_as_no_clippy(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rustup: no component is installed."""
    (planted_state.bins / "rustup").unlink()
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 1
    assert "lint:rust: clippy component missing" in err


def test_a_failing_rustup_reads_as_no_clippy(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rustup that exits nonzero: what it printed is not believed."""
    (planted_state.bins / "rustup").write_text(
        "#!/usr/bin/env bash\necho clippy-host\nexit 1\n", encoding="utf-8"
    )
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 1
    assert "lint:rust: clippy component missing" in err


def test_a_local_clippy_toml_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tracked clippy.toml lowers the org's knobs: red, by name."""
    planted_state.plant({"clippy.toml": "\n", "crates/a/rustfmt.toml": "\n"})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
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
        in err
    )


def test_the_canon_holds_its_delivered_configs(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the identity tracked, mise/clippy.toml is the belt's own (#746)."""
    planted_state.plant({
        "security/identity.toml": "\n",
        "mise/clippy.toml": "\n",
        "mise/rustfmt.toml": "\n",
    })
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err


def test_a_consumer_mise_clippy_toml_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the identity, mise/clippy.toml is a shadow config."""
    planted_state.plant({"mise/clippy.toml": "\n"})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 1
    assert "lint:rust: repo-local Rust tool configuration" in err
    assert "  mise/clippy.toml\n" in err


def test_rustfmt_missing_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rustup that lists no rustfmt: the remedy names the pin."""
    planted_state.enter(monkeypatch, STUB_COMPONENTS="clippy-host\\n")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "rustfmt component missing — declare it with the rust pin in mise.toml:",
            'rust = { version = "1.xx.x", components = "clippy,rustfmt,llvm-tools" }',
        )
        in err
    )


def test_a_whole_file_skip_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`#![rustfmt::skip]`, indented: never allowed."""
    planted_state.plant({"src/lib.rs": "  #![rustfmt::skip]\n"})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "formatting suppressed without a written decision: src/lib.rs(whole-file)",
            *SKIP_MESSAGE,
        )
        in err
    )
    assert planted_state.logged() == NOT_ASKED


def test_a_narrow_skip_without_a_marker_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`#[rustfmt::skip]` with no `rustfmt-skip:` comment: red."""
    planted_state.plant({"src/a.rs": "#[rustfmt::skip]\nfn f() {}\n", "src/b.rs": "\n"})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "formatting suppressed without a written decision: src/a.rs(no marker)",
            *SKIP_MESSAGE,
        )
        in err
    )


def test_a_narrow_skip_with_a_marker_passes(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`// rustfmt-skip: <why>` above it: green."""
    text = "// rustfmt-skip: a table\n#[rustfmt::skip]\nfn f() {}\n"
    planted_state.plant({"src/a.rs": text})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err


def test_a_vendored_skip_is_not_read(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vendor/ is not the repository's code; a deleted file is passed over."""
    planted_state.plant({"vendor/x/lib.rs": "#![rustfmt::skip]\n", "src/gone.rs": "\n"})
    (planted_state.tree / "src" / "gone.rs").unlink()
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err


def test_a_rustfmt_warning_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rustfmt warning with exit 0: an ignored option is a weaker format."""
    warning = "Warning: Unknown configuration option `x`\n"
    planted_state.enter(monkeypatch, STUB_FMT_OUT=warning)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "Cargo.toml: rustfmt warned — an ignored option means a weaker format,"
            " silently"
        )
        in err
    )
    assert warning in err


def test_unformatted_code_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing format check: the diff is shown and fix:rust named."""
    diff = "Diff in src/lib.rs at line 1:\n"
    planted_state.enter(monkeypatch, STUB_FMT_OUT=diff, STUB_FMT_STATUS="1")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "Cargo.toml: formatting differs from the org's — run 'mise run fix:rust'"
        )
        in err
    )
    assert diff in err
    assert "formatting clean" not in out


def test_a_suppression_clippy_cannot_see_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rust_suppressions.py reads every tracked manifest and source."""
    planted_state.plant({"src/lib.rs": "#![allow(dead_code)]\n"})
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 1
    assert UNSEEN in err
    assert "cargo fetch" not in "\n".join(planted_state.logged())


def test_a_failing_ladder_keeps_its_status(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ladder cannot be read: nothing is linted."""
    planted_state.enter(monkeypatch, STUB_LADDER_FAILS="1")
    status = rust_lint.main(["lint"])
    assert status == 3
    assert "cargo fetch" not in "\n".join(planted_state.logged())


def test_a_failing_fetch_keeps_its_status(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing `cargo fetch`: its status, and no metadata read."""
    planted_state.enter(monkeypatch, STUB_FETCH_STATUS="7")
    status = rust_lint.main(["lint"])
    assert status == 7
    assert (
        planted_state.logged()[-1] == "cargo fetch --locked --manifest-path Cargo.toml"
    )


def test_unreadable_metadata_is_red(
    planted_state: Planted, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata that is not JSON: gate_surface.py's status, nothing linted.

    The finding is gate_surface.py's, on its own stderr, which this
    process does not capture; test_gate_surface.py asserts it.
    """
    planted_state.metadata.write_text("not json", encoding="utf-8")
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    assert status == 1
    assert "clippy" not in "\n".join(planted_state.logged())


def test_a_workspace_of_no_member_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata naming no member: never a pass over nothing."""
    planted_state.metadata.write_text(workspace(), encoding="utf-8")
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            "Cargo.toml: a Cargo.toml is tracked but cargo metadata names no"
            " workspace member"
        )
        in err
    )


def test_a_clean_workspace_prints_its_population(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each plain member is linted; the extension and the excluded are named."""
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 0, err
    said = [
        "lint:rust: Cargo.toml: formatting clean",
        "lint:rust: Cargo.toml: 2 crate(s) linted: a b",
        "lint:rust: Cargo.toml: pgrx extension(s), linted by lint:pg-clippy: ext",
        "lint:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
        ("lint:rust: clean at every group (restriction minus 0 named contradictions)"),
    ]
    assert said == out.splitlines()
    clippy = (
        "--no-deps --all-targets --all-features --locked --keep-going"
        " --manifest-path Cargo.toml"
    )
    fmt = "--manifest-path Cargo.toml -- --config-path"
    want = [
        "cargo fetch --locked --manifest-path Cargo.toml",
        f"cargo fmt --all --check {fmt} {planted_state.belt}/rustfmt.toml",
        (
            "cargo metadata --no-deps --format-version 1 --locked"
            " --manifest-path Cargo.toml"
        ),
        f"cargo clippy -p a@0.1.0 {clippy} -- -D warnings",
        f"cargo clippy -p b@0.1.0 {clippy} -- -D warnings",
    ]
    assert want == planted_state.logged()


def test_clippy_features_replace_all_features(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIPPY_FEATURES is split into words, in place of --all-features."""
    planted_state.enter(
        monkeypatch, CLIPPY_FEATURES="--no-default-features  --features x"
    )
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err
    assert (
        "cargo clippy -p a@0.1.0 --no-deps --all-targets --no-default-features"
        " --features x --locked --keep-going --manifest-path Cargo.toml"
        " -- -D warnings"
    ) in planted_state.logged()


def test_a_red_member_is_named_with_clippys_status(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A red `a`: every member is still asked, and cargo's 101 is kept."""
    planted_state.enter(monkeypatch, STUB_RED="a")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 101, out + err
    assert planted_state.finding("red: a (Cargo.toml)") in err
    assert "lint:rust: Cargo.toml: 2 crate(s) linted: a b\n" in out
    assert sum("cargo clippy" in line for line in planted_state.logged()) == 2


def test_clippy_output_is_shown(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What clippy prints reaches stdout as it comes."""
    planted_state.enter(monkeypatch, STUB_CLIPPY_OUT="warning: a note\n")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out.count("warning: a note\n") == 2


def test_a_feature_some_crate_lacks_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo's `does not contain this feature`: the CLIPPY_FEATURES finding."""
    out = "error: package `a` does not contain this feature: x\n"
    planted_state.enter(monkeypatch, STUB_CLIPPY_OUT=out)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            (
                "Cargo.toml: CLIPPY_FEATURES names a feature some crate in this"
                " workspace lacks"
            ),
            (
                "every crate is linted as its own package (#726), so the value has to be one"
            ),
            (
                "each member accepts — --all-features, or --no-default-features"
                " — and a crate"
            ),
            "that genuinely cannot take it is named in CLIPPY_EXCLUDE instead",
        )
        in err
    )


def test_an_unknown_lint_name_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E0602: the exclusion list names a lint this toolchain lacks."""
    planted_state.enter(monkeypatch, STUB_CLIPPY_OUT="warning[E0602]: unknown lint\n")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        planted_state.finding(
            (
                "Cargo.toml: a lint name above is unknown to this toolchain"
                " (see E0602/renamed above)"
            ),
            (
                "clippy renamed or removed it — update the exclusion list"
                " in mise/config.toml"
            ),
        )
        in err
    )


def test_a_renamed_lint_name_is_red(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`has been renamed to`: the same finding."""
    out = "warning: lint `x` has been renamed to `y`\n"
    planted_state.enter(monkeypatch, STUB_CLIPPY_OUT=out)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1
    assert "lint:rust: Cargo.toml: a lint name above is unknown" in err


def test_no_extension_and_no_exclusion_print_neither_line(
    planted_state: Planted,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the lines that name something are printed."""
    planted_state.metadata.write_text(workspace(member("a")), encoding="utf-8")
    planted_state.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "pgrx extension" not in out
    assert "excluded" not in out


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            "belt rust-lint: error: the following arguments are required: mode",
            id="nothing",
        ),
    ],
)
def test_no_subcommand_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """No task named: status 2, the usage: argparse's exit 2, its usage and its error."""
    with pytest.raises(SystemExit) as ended:
        rust_lint.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt rust-lint "), err
    assert err.splitlines()[-1] == error


class FixBranches(Planted):
    """Each of fix:rust's branches, its message asserted (#1911)."""

    task = "fix:rust"
    cargo = FIX_CARGO


@fixture
def fix_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    belt: support.Belt,
    env: support.Env,
) -> FixBranches:
    """Give each row its own FixBranches."""
    return FixBranches(tmp_path, monkeypatch, belt=belt, env=env)


def test_fix_branches_cargo_missing_is_red(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cargo on PATH: red, as the gate leg."""
    (fix_branches.bins / "cargo").unlink()
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        fix_branches.finding(
            "Cargo.toml present but cargo missing — pin rust in mise.toml"
        )
        in err
    )


@pytest.mark.parametrize(
    ("listed", "missing"),
    [
        ("rustfmt-host\\n", "clippy"),
        ("clippy-host\\n", "rustfmt"),
    ],
)
def test_each_missing_component_is_red(
    fix_branches: FixBranches,
    listed: object,
    missing: object,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clippy, then rustfmt, each named."""
    fix_branches.enter(monkeypatch, STUB_COMPONENTS=listed)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        fix_branches.finding(
            f"{missing} component missing — declare it with the rust pin in mise.toml"
        )
        in err
    )


def test_a_failing_format_keeps_its_status(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `cargo fmt`: its status, and nothing fixed."""
    (fix_branches.bins / "cargo").write_text(LOCATE + "exit 4\n", encoding="utf-8")
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out = capfd.readouterr().out
    assert status == 4
    assert "formatted" not in out


def test_fix_branches_a_failing_ladder_keeps_its_status(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ladder cannot be read: nothing formatted or fixed."""
    fix_branches.enter(monkeypatch, STUB_LADDER_FAILS="1")
    status = rust_lint.main(["fix"])
    out = capfd.readouterr().out
    assert status == 3
    assert out == ""
    assert fix_branches.logged() == NOT_ASKED


def test_a_workspace_without_extensions(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One --workspace pass, the excluded held out and named."""
    fix_branches.metadata.write_text(PLAIN, encoding="utf-8")
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 0, err
    at = "--manifest-path Cargo.toml"
    want = [
        f"cargo fmt --all {at} -- --config-path {fix_branches.belt}/rustfmt.toml [ ]",
        f"cargo fetch --locked {at} [ ]",
        f"cargo metadata --no-deps --format-version 1 --locked {at} [ ]",
        (
            "cargo clippy --fix --allow-dirty --workspace --exclude held"
            f" --all-features --locked {at} -- -D warnings [ ]"
        ),
    ]
    assert want == fix_branches.logged()
    said = [
        "fix:rust: Cargo.toml: formatted",
        "fix:rust: Cargo.toml: a b rewritten where clippy could",
        "fix:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
        "fix:rust: applied what is machine-applicable — the rest is judgement",
    ]
    assert said == out.splitlines()


def test_nothing_left_to_fix_says_none(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every member excluded: ` none`, and no exclusion of an extension."""
    fix_branches.metadata.write_text(workspace(member("held")), encoding="utf-8")
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert "fix:rust: Cargo.toml: none rewritten where clippy could\n" in out


def test_a_failing_fix_pass_keeps_its_status(
    fix_branches: FixBranches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace pass fails: its status, no extension pass."""
    fix_branches.plant({})
    (fix_branches.bins / "cargo").write_text(
        LOCATE + "[[ $1 != clippy ]] || exit 6\n"
        '[[ $1 != metadata ]] || cat "${STUB_METADATA}"\n',
        encoding="utf-8",
    )
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    assert status == 6
    assert "prepare 16,17" not in fix_branches.logged()


def test_the_extension_is_fixed_at_its_highest_major(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extension is held out of the workspace pass and fixed at pg17.

    What the prepare exported reaches the compile, and so does the
    major's own environment.
    """
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 0, err
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
    assert want == fix_branches.logged()[3:]
    said = [
        "fix:rust: Cargo.toml: formatted",
        "fix:rust: Cargo.toml: a b rewritten where clippy could",
        "fix:rust: Cargo.toml: pgrx extension(s) fixed: ext(pg17)",
        "fix:rust: Cargo.toml: excluded by CLIPPY_EXCLUDE: held",
        "fix:rust: applied what is machine-applicable — the rest is judgement",
    ]
    assert said == out.splitlines()


def test_a_failing_prepare_keeps_its_status(
    fix_branches: FixBranches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prepare fails: its status, and the extension never compiled."""
    fix_branches.enter(monkeypatch, STUB_PREPARE_FAILS="1")
    status = rust_lint.main(["fix"])
    assert status == 5
    assert fix_branches.logged()[-1] == "prepare 17"


def test_a_failing_pin_check_keeps_its_status(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A major with no Postgres pinned: the check's status."""
    fix_branches.enter(monkeypatch, STUB_PIN_STATUS="8")
    status = rust_lint.main(["fix"])
    out = capfd.readouterr().out
    assert status == 8
    assert "ext(pg17)" not in out


def test_a_failing_extension_fix_keeps_its_status(
    fix_branches: FixBranches,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extension's own pass fails: its status."""
    fix_branches.enter(monkeypatch, STUB_EXT_STATUS="9")
    status = rust_lint.main(["fix"])
    out = capfd.readouterr().out
    assert status == 9
    assert "ext(pg17)" not in out


def test_a_failing_union_keeps_its_status(
    fix_branches: FixBranches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The majors cannot be derived: no pass runs."""
    fix_branches.belt.joinpath("pgrx-env.sh").write_text(
        STUB_PGRX_ENV + "org_pgrx_union() { return 4; }\n", encoding="utf-8"
    )
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    assert status == 4
    assert "--fix" not in "\n".join(fix_branches.logged())


def test_a_failing_highest_keeps_its_status(
    fix_branches: FixBranches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each extension's major cannot be derived: no pass runs."""
    fix_branches.belt.joinpath("pgrx-env.sh").write_text(
        STUB_PGRX_ENV + "org_pgrx_highest() { return 2; }\n", encoding="utf-8"
    )
    fix_branches.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    assert status == 2
    assert "--fix" not in "\n".join(fix_branches.logged())


class EveryWorkspace(Planted):
    """A tree of two workspaces: each is formatted, linted and fixed (#2004).

    `Cargo.toml` holds `a`, `b`, `ext` and `held`; `fuzz/Cargo.toml` is a
    cargo-fuzz crate, `parse`, its own workspace root. Before #2004 the
    task ran cargo in the root workspace alone, and `parse` was never
    formatted or linted.
    """

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        belt: support.Belt,
        env: support.Env,
    ) -> None:
        """Plant the fuzz crate as the tree's second root."""
        super().__init__(tmp_path, monkeypatch, belt=belt, env=env)
        self.plant(TWO_ROOTS)

    def asked(self, verb: str) -> list[str]:
        """Read the stub log's calls of one cargo subcommand.

        Returns:
            Those lines, in order.

        """
        return [line for line in self.logged() if line.startswith(f"cargo {verb} ")]

    def flags(self) -> list[str]:
        """Read what RUSTFLAGS each clippy call saw, by its manifest.

        Returns:
            `<manifest> <RUSTFLAGS>` per call, `unset` for none.

        """
        log = self.log.with_name(f"{self.log.name}.flags")
        return log.read_text(encoding="utf-8").splitlines()


@fixture
def every_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    belt: support.Belt,
    env: support.Env,
) -> EveryWorkspace:
    """Give each row its own EveryWorkspace."""
    return EveryWorkspace(tmp_path, monkeypatch, belt=belt, env=env)


def test_each_root_is_formatted_and_linted(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An fmt and a clippy call for each root, one population line each."""
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 0, err
    fmt = every_workspace.asked("fmt")
    assert len(fmt) == 2
    assert "--manifest-path Cargo.toml --" in fmt[0]
    assert "--manifest-path fuzz/Cargo.toml --" in fmt[1]
    clippy = [line.split()[3] for line in every_workspace.asked("clippy")]
    assert clippy == EVERY_CRATE
    assert "--manifest-path fuzz/Cargo.toml --" in every_workspace.asked("clippy")[2]
    assert "lint:rust: Cargo.toml: 2 crate(s) linted: a b\n" in out
    assert "lint:rust: fuzz/Cargo.toml: 1 crate(s) linted: parse\n" in out


def test_an_unformatted_fuzz_root_is_red(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A format check refused for fuzz/Cargo.toml only: red, naming it."""
    every_workspace.enter(monkeypatch, STUB_FMT_RED="fuzz/Cargo.toml")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        every_workspace.finding(
            "fuzz/Cargo.toml: formatting differs from the org's — run 'mise run fix:rust'"
        )
        in err
    )
    assert "lint:rust: Cargo.toml: 2 crate(s) linted: a b\n" in out


def test_a_red_fuzz_crate_leaves_the_root_linted(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clippy refuses `parse` only: red names it, a and b were asked."""
    every_workspace.enter(monkeypatch, STUB_RED="parse")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 101, out + err
    assert every_workspace.finding("red: parse (fuzz/Cargo.toml)") in err
    clippy = [line.split()[3] for line in every_workspace.asked("clippy")]
    assert clippy == EVERY_CRATE


def test_a_root_outside_the_tree_is_red(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """locate-project names a workspace above the checkout: red, exact."""
    every_workspace.enter(monkeypatch, STUB_FUZZ_ROOT="/elsewhere/Cargo.toml")
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert every_workspace.finding(OUTSIDE, OUTSIDE_REMEDY) in err
    assert every_workspace.asked("clippy") == NOT_ASKED


def test_a_failing_locate_project_says_cargos_words(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """locate-project exits nonzero: cargo's own text is in the output."""
    every_workspace.enter(monkeypatch, STUB_LOCATE_FAILS="1")
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 1
    assert "error: planted: Cargo.toml could not be read" in err
    assert every_workspace.asked("clippy") == NOT_ASKED


def test_git_failing_to_name_the_tree_is_red(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derivation's git cannot name the top: red, git's words kept."""
    git = every_workspace.bins / "failing-git"
    git.write_text(
        "#!/usr/bin/env bash\necho 'fatal: planted' >&2\nexit 128\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    monkeypatch.setattr(rust_workspaces, "GIT", str(git))
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert (
        every_workspace.finding("git could not name the tree", "fatal: planted") in err
    )
    assert every_workspace.asked("clippy") == NOT_ASKED


def test_a_marked_fuzz_root_is_linted_with_cfg_fuzzing(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cargo-fuzz marker: its clippy has the cfg, the root's has not."""
    every_workspace.plant(MARKED)
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err
    assert every_workspace.flags() == FUZZING
    assert "RUSTFLAGS" not in os.environ


def test_a_fuzz_directory_without_the_marker_gets_no_cfg(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fuzz/Cargo.toml with no marker: no call is given the cfg."""
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err
    assert every_workspace.flags() == UNMARKED


def test_a_marked_root_keeps_the_rustflags_it_had(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUSTFLAGS=-Dwarnings set: the marked root's has the cfg appended."""
    every_workspace.plant(MARKED)
    every_workspace.enter(monkeypatch, RUSTFLAGS="-Dwarnings")
    status = rust_lint.main(["lint"])
    err = capfd.readouterr().err
    assert status == 0, err
    want = [
        "Cargo.toml -Dwarnings",
        "Cargo.toml -Dwarnings",
        "fuzz/Cargo.toml -Dwarnings --cfg fuzzing",
    ]
    assert want == every_workspace.flags()


def test_fix_rust_fixes_a_marked_root_with_cfg_fuzzing(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fix:rust: the marked root's clippy --fix has the cfg, the root's not."""
    (every_workspace.bins / "cargo").write_text(FIX_CARGO, encoding="utf-8")
    every_workspace.plant(MARKED)
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    err = capfd.readouterr().err
    assert status == 0, err
    seen = every_workspace.flags()
    fixed: list[str] = [line for line in seen if line.startswith("fuzz/")]
    want: list[str] = ["fuzz/Cargo.toml --cfg fuzzing"]
    assert want == fixed
    assert "Cargo.toml unset" in every_workspace.flags()


def test_fix_rust_formats_and_fixes_each_root(
    every_workspace: EveryWorkspace,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fix:rust: cargo fmt and clippy --fix for both roots."""
    (every_workspace.bins / "cargo").write_text(FIX_CARGO, encoding="utf-8")
    every_workspace.enter(monkeypatch)
    status = rust_lint.main(["fix"])
    out, err = capfd.readouterr()
    assert status == 0, err
    fmt = every_workspace.asked("fmt")
    assert len(fmt) == 2
    assert "--manifest-path fuzz/Cargo.toml --" in fmt[1]
    fixed = [
        line
        for line in every_workspace.asked("clippy")
        if "--workspace" in line.split()
    ]
    assert len(fixed) == 2
    assert "--manifest-path Cargo.toml --" in fixed[0]
    assert "--manifest-path fuzz/Cargo.toml --" in fixed[1]
    assert "fix:rust: fuzz/Cargo.toml: parse rewritten where clippy could\n" in out

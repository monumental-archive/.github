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

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

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

CARGO = """\
#!/usr/bin/env bash
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
    case.assertIn(f"lint:rust: 6 crate(s) linted: {' '.join(MEMBERS)}\n", done.stdout)
    case.assertIn("lint:rust: red: edtf-core\n", done.stderr)


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
            env = hermetic.env(root / "home", ORG_BELT_DIR=str(BELT))
            subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
            subprocess.run([GIT, "add", "."], cwd=tree, env=env, check=True)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
            env["STUB_METADATA"] = str(METADATA)
            env["CLIPPY_LOG"] = str(log)
            env |= stub
            done = subprocess.run(
                [BASH, "-euo", "pipefail", "-c", body()],
                cwd=tree,
                env=env,
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


if __name__ == "__main__":
    unittest.main()

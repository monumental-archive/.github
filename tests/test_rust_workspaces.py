"""Rows for the one derivation of the tree's Rust workspaces (#2004).

`mise/rust_workspaces.py` resolves every tracked manifest outside
`vendor/` to its workspace root with `cargo locate-project`, and
`org_rust_workspace_roots` (mise/rust-workspaces.sh) reads its list into
a task's array. Each row plants a tree with two roots, `Cargo.toml` and
`fuzz/Cargo.toml` (its own workspace, as cargo-fuzz crates are in edtf,
iiif-server and release-lab), a member of the first, and a vendored
crate. cargo is a stub that records every manifest it is asked about and
answers by path through a symlink to the tree, so a root is compared
physically. A refusing row asserts its message, not only its status
(#650).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import functools
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from belt import rust_workspaces
from tests import hermetic

BELT = hermetic.MISE
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
# The machine's own bash as well: on a Mac it is 3.2, and the reader
# fills its array by name, as org_tracked_read does.
SYSTEM_BASH = hermetic.optional_tool("bash", path="/bin")
SHELLS = (BASH,) if SYSTEM_BASH in {None, BASH} else (BASH, str(SYSTEM_BASH))

# The planted tree: a root workspace with one member, a fuzz crate that
# is its own workspace, and a vendored crate nobody may ask about.
FILES = {
    "Cargo.toml": "[workspace]\nmembers = ['crates/a']\n",
    "crates/a/Cargo.toml": "[package]\nname = 'a'\n",
    "fuzz/Cargo.toml": "[package]\nname = 'a-fuzz'\n\n[workspace]\n",
    "vendor/up/Cargo.toml": "[package]\nname = 'up'\n",
}

# `cargo locate-project --workspace --manifest-path M --message-format
# plain`: the fuzz crate is its own root, everything else is the top's.
# Paths are spelled through STUB_TOP, a symlink to the tree.
CARGO = """\
#!/usr/bin/env bash
[[ $1 == locate-project && $3 == --manifest-path ]] || exit 1
printf '%s\\n' "$4" >> "${STUB_LOG}"
case "${STUB_MODE:-}:$4" in
  fail:fuzz/Cargo.toml)
    echo "error: planted: failed to parse manifest at $4" >&2
    exit 101
    ;;
  outside:fuzz/Cargo.toml) echo "${STUB_OUTSIDE}/Cargo.toml" ;;
  *:fuzz/Cargo.toml) echo "${STUB_TOP}/fuzz/Cargo.toml" ;;
  *) echo "${STUB_TOP}/Cargo.toml" ;;
esac
"""

OUTSIDE_MESSAGE = (
    "lint:rust: fuzz/Cargo.toml belongs to a workspace outside this tree: {root}\n"
    "  give it its own [workspace] table, or make it a member of a tracked"
    " workspace\n"
)
# What a reader's run leaves in its scratch directory: its list file is
# gone.
LEFT: list[str] = ["asked.log", "bin", "link", "repo"]

FAILED_MESSAGE = (
    "lint:rust: cargo locate-project failed for fuzz/Cargo.toml\n"
    "  error: planted: failed to parse manifest at fuzz/Cargo.toml\n"
)


class Planted:
    """A planted two-root tree, the cargo stub, and their environment."""

    def __init__(self, case: unittest.TestCase, mode: str = "") -> None:
        """Plant the tree under a fresh directory the case cleans up."""
        scratch = tempfile.TemporaryDirectory()
        case.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        self.tree = self.base / "repo"
        self.log = self.base / "asked.log"
        self.outside = (self.base / "elsewhere").resolve()
        bindir = self.base / "bin"
        bindir.mkdir()
        self.cargo = bindir / "cargo"
        self.cargo.write_text(CARGO, encoding="utf-8")
        self.cargo.chmod(0o755)
        (self.base / "link").symlink_to(self.tree)
        for name, text in FILES.items():
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_text(text, encoding="utf-8")
        self.env = hermetic.env(
            self.base / "home",
            MISE_TASK_NAME="lint:rust",
            ORG_FINDINGS="",
            STUB_LOG=str(self.log),
            STUB_MODE=mode,
            STUB_OUTSIDE=str(self.outside),
            STUB_TOP=str(self.base / "link"),
        )
        subprocess.run([GIT, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        subprocess.run(
            [GIT, "add", "--", *FILES], cwd=self.tree, env=self.env, check=True
        )
        self.env["PATH"] = f"{bindir}:{self.env.get('PATH', '')}"

    def asked(self) -> list[str]:
        """Read the manifests the stub was asked about.

        Returns:
            Each manifest, in the order asked.

        """
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def main(self) -> hermetic.Called:
        """Run the `roots` subcommand in this process, in the tree.

        Returns:
            The status and both streams.

        """
        with mock.patch.object(rust_workspaces, "CARGO", str(self.cargo)):
            return hermetic.call_main(
                lambda: rust_workspaces.main(["roots"]), env=self.env, cwd=self.tree
            )

    def read(self, shell: str) -> subprocess.CompletedProcess[str]:
        """Run `org_rust_workspace_roots` and print what it filled.

        Returns:
            The finished run: one element per line on stdout.

        """
        script = (
            '. "${ORG_BELT_DIR}/rust-workspaces.sh"\n'
            "roots=() && org_rust_workspace_roots roots\n"
            '[[ -z ${roots[0]+set} ]] || printf "%s\\n" "${roots[@]}"\n'
        )
        return subprocess.run(
            [shell, "--norc", "-euo", "pipefail", "-c", script],
            cwd=self.tree,
            env=self.env
            | {
                "BASH_ENV": str(BELT / "findings.sh"),
                "ORG_BELT_DIR": str(BELT),
                "ORG_PYTHON": sys.executable,
                "TMPDIR": str(self.base),
            },
            capture_output=True,
            text=True,
            check=False,
        )


class TestRoots:
    """roots(): the distinct workspaces, the top one first."""

    def test_two_roots_top_first_vendor_never_asked(self) -> None:
        """Cargo.toml, then fuzz/Cargo.toml; the member folds into the top."""
        planted = Planted(self)
        done = planted.main()
        assert (done.returncode, done.stderr) == (0, "")
        assert done.stdout == "Cargo.toml\0fuzz/Cargo.toml\0"
        asked: list[str] = sorted(planted.asked())
        want = ["Cargo.toml", "crates/a/Cargo.toml", "fuzz/Cargo.toml"]
        assert want == asked

    def test_a_tree_with_no_top_manifest_lists_the_rest_sorted(self) -> None:
        """No Cargo.toml at the top: nothing is put first."""
        bare: list[str] = ["a", "b"]
        topped: list[str] = ["Cargo.toml", "a", "z"]
        assert bare == rust_workspaces.ordered({"b", "a"})
        assert topped == rust_workspaces.ordered({"z", "Cargo.toml", "a"})

    def test_a_root_outside_the_tree_is_red_by_name(self) -> None:
        """fuzz/Cargo.toml resolved outside: the message and remedy, exact."""
        planted = Planted(self, "outside")
        done = planted.main()
        assert (done.returncode, done.stdout) == (1, "")
        want = OUTSIDE_MESSAGE.format(root=planted.outside / "Cargo.toml")
        assert want == done.stderr

    def test_a_failing_locate_project_carries_cargos_text(self) -> None:
        """Cargo exits 101: its own words, and every other manifest still asked."""
        planted = Planted(self, "fail")
        done = planted.main()
        assert (done.returncode, done.stdout) == (1, "")
        assert done.stderr == FAILED_MESSAGE
        assert len(planted.asked()) == 3

    def test_git_failing_is_red_with_gits_words(self) -> None:
        """Outside a repository: git's error, never an empty list."""
        planted = Planted(self)
        outside = planted.base / "plain"
        outside.mkdir()
        done = hermetic.call_main(
            lambda: rust_workspaces.main(["roots"]), env=planted.env, cwd=outside
        )
        assert (done.returncode, done.stdout) == (1, "")
        assert "lint:rust: git could not name the tree\n" in done.stderr
        assert "not a git repository" in done.stderr

    def test_resolved_is_the_roots_or_none_having_said_why(self) -> None:
        """The roots; None on an unresolved manifest; None on git, said."""
        planted = Planted(self)
        roots: list[str] = ["Cargo.toml", "fuzz/Cargo.toml"]
        with mock.patch.object(rust_workspaces, "CARGO", str(planted.cargo)):
            done = hermetic.call_main(
                lambda: 0 if rust_workspaces.resolved() == roots else 3,
                env=planted.env,
                cwd=planted.tree,
            )
        assert (done.returncode, done.stderr) == (0, "")
        refused = Planted(self, "outside")
        with mock.patch.object(rust_workspaces, "CARGO", str(refused.cargo)):
            done = hermetic.call_main(
                lambda: 0 if rust_workspaces.resolved() is None else 3,
                env=refused.env,
                cwd=refused.tree,
            )
        assert done.returncode == 0
        assert "belongs to a workspace outside this tree" in done.stderr
        outside = planted.base / "plain"
        outside.mkdir()
        done = hermetic.call_main(
            lambda: 0 if rust_workspaces.resolved() is None else 3,
            env=planted.env,
            cwd=outside,
        )
        assert done.returncode == 0
        assert "lint:rust: git could not name the tree\n" in done.stderr

    def test_anything_but_roots_is_a_usage_error(self) -> None:
        """No subcommand, or another: exit 2 and the usage line."""
        argvs: list[list[str]] = [[], ["root"], ["roots", "x"]]
        for argv in argvs:
            with self.subTest(argv=argv):
                done = hermetic.call_main(functools.partial(rust_workspaces.main, argv))
                assert (done.returncode, done.stdout, done.stderr) == (
                    2,
                    "",
                    "usage: rust_workspaces.py roots\n",
                )


class TestReader:
    """org_rust_workspace_roots fills the array, or fails."""

    @pytest.mark.parametrize("shell", SHELLS)
    def test_the_reader_fills_one_element_per_root(self, shell: object) -> None:
        """Both roots in order, no empty last element, no file left."""
        planted = Planted(self)
        done = planted.read(shell)
        left: list[str] = sorted(p.name for p in planted.base.iterdir())
        assert (done.returncode, done.stdout, done.stderr) == (
            0,
            "Cargo.toml\nfuzz/Cargo.toml\n",
            "",
        )
        assert left == LEFT

    @pytest.mark.parametrize("shell", SHELLS)
    def test_the_reader_fails_when_the_helper_fails(self, shell: object) -> None:
        """A finding: exit 1 under errexit, the message, nothing filled."""
        planted = Planted(self, "fail")
        done = planted.read(shell)
        left: list[str] = sorted(p.name for p in planted.base.iterdir())
        assert (done.returncode, done.stdout) == (1, "")
        assert FAILED_MESSAGE in done.stderr
        assert left == LEFT


class TestFuzzing:
    """A cargo-fuzz root is built with `--cfg fuzzing`, by its marker (#2093)."""

    def manifest(self, text: str) -> str:
        """Write a root manifest in a scratch directory.

        Returns:
            Its path.

        """
        with tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8"
        ) as written:
            written.write(text)
        self.addCleanup(Path(written.name).unlink)
        return written.name

    @pytest.mark.parametrize(
        "text,want",
        (
            ("[package]\nmetadata = { cargo-fuzz = true }\n", True),
            ('[package]\nmetadata = { cargo-fuzz = "true" }\n', False),
            ("[package]\nmetadata = { cargo-fuzz = false }\n", False),
            ("[package]\nmetadata = { other = true }\n", False),
            ("[package]\nmetadata = 1\n", False),
            ('[package]\nname = "fuzz"\n', False),
            ("package = 1\n", False),
            ("[workspace]\nmetadata = { cargo-fuzz = true }\n", False),
        ),
    )
    def test_only_a_true_cargo_fuzz_in_package_metadata_is_the_marker(
        self, text: object, want: object
    ) -> None:
        """Every other shape of the manifest is not a cargo-fuzz crate."""
        assert want == rust_workspaces.fuzzing(self.manifest(text))

    def test_the_cfg_is_appended_to_the_variable_cargo_reads(self) -> None:
        """RUSTFLAGS, or the encoded variable whenever it is set at all."""
        marked = self.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
        encoded = "CARGO_ENCODED_RUSTFLAGS"
        for environ, want in (
            ({}, {"RUSTFLAGS": "--cfg fuzzing"}),
            ({"RUSTFLAGS": ""}, {"RUSTFLAGS": "--cfg fuzzing"}),
            ({"RUSTFLAGS": "-Dwarnings"}, {"RUSTFLAGS": "-Dwarnings --cfg fuzzing"}),
            ({encoded: ""}, {encoded: "--cfg\x1ffuzzing"}),
            (
                {encoded: "-D\x1fwarnings", "RUSTFLAGS": "-Cx"},
                {encoded: "-D\x1fwarnings\x1f--cfg\x1ffuzzing"},
            ),
        ):
            with self.subTest(environ=environ):
                assert want == rust_workspaces.rustflags(marked, environ)

    def test_an_unmarked_root_adds_nothing(self) -> None:
        """No marker: no variable is named, whatever is already set."""
        plain = self.manifest("[workspace]\n")
        nothing: dict[str, str] = {}
        added = rust_workspaces.rustflags(plain, {"RUSTFLAGS": "-Cx"})
        assert nothing == added

    def test_built_as_holds_the_flags_and_puts_them_back(self) -> None:
        """Set inside, restored after: a value kept, an absent one removed."""
        marked = self.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
        rows: list[tuple[dict[str, str], str]] = [
            ({}, "--cfg fuzzing"),
            ({"RUSTFLAGS": "-Cx"}, "-Cx --cfg fuzzing"),
        ]
        for before, inside in rows:
            with (
                self.subTest(before=before),
                mock.patch.dict(os.environ, before, clear=True),
            ):
                with rust_workspaces.built_as(marked):
                    assert inside == os.environ["RUSTFLAGS"]
                assert before.get("RUSTFLAGS") == os.environ.get("RUSTFLAGS")

    def test_built_as_restores_after_a_raise(self) -> None:
        """An exception inside still puts the environment back."""
        marked = self.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
        empty: dict[str, str] = {}
        with mock.patch.dict(os.environ, empty, clear=True):
            with pytest.raises(KeyError), rust_workspaces.built_as(marked):
                _ = os.environ["PLANTED_ABSENT"]
            assert "RUSTFLAGS" not in os.environ

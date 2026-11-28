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
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import rust_workspaces
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    pass

BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")
# The machine's own bash as well: on a Mac it is 3.2, and the reader
# fills its array by name, as org_tracked_read does.
SYSTEM_BASH = support.optional_tool("bash", path="/bin")
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

    def __init__(self, base: Path, mode: str = "", *, env: support.Env) -> None:
        """Plant the tree under `base`, a directory of the row's own."""
        self.make_env = env
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
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
        self.env = self.make_env(
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

    def enter(self, monkeypatch: pytest.MonkeyPatch, cwd: Path | None = None) -> None:
        """Stand in the tree (or `cwd`), under the planted environment."""
        monkeypatch.setattr(rust_workspaces, "CARGO", str(self.cargo))
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(cwd or self.tree)

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


# roots(): the distinct workspaces, the top one first.
def test_two_roots_top_first_vendor_never_asked(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo.toml, then fuzz/Cargo.toml; the member folds into the top."""
    planted = Planted(tmp_path, env=env)
    planted.enter(monkeypatch)
    status = rust_workspaces.main(["roots"])
    out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    assert out == "Cargo.toml\0fuzz/Cargo.toml\0"
    asked: list[str] = sorted(planted.asked())
    want = ["Cargo.toml", "crates/a/Cargo.toml", "fuzz/Cargo.toml"]
    assert want == asked


def test_a_tree_with_no_top_manifest_lists_the_rest_sorted() -> None:
    """No Cargo.toml at the top: nothing is put first."""
    bare: list[str] = ["a", "b"]
    topped: list[str] = ["Cargo.toml", "a", "z"]
    assert bare == rust_workspaces.ordered({"b", "a"})
    assert topped == rust_workspaces.ordered({"z", "Cargo.toml", "a"})


def test_a_root_outside_the_tree_is_red_by_name(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fuzz/Cargo.toml resolved outside: the message and remedy, exact."""
    planted = Planted(tmp_path / "outside", "outside", env=env)
    planted.enter(monkeypatch)
    status = rust_workspaces.main(["roots"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    want = OUTSIDE_MESSAGE.format(root=planted.outside / "Cargo.toml")
    assert want == err


def test_a_failing_locate_project_carries_cargos_text(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo exits 101: its own words, and every other manifest still asked."""
    planted = Planted(tmp_path, "fail", env=env)
    planted.enter(monkeypatch)
    status = rust_workspaces.main(["roots"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert err == FAILED_MESSAGE
    assert len(planted.asked()) == 3


def test_git_failing_is_red_with_gits_words(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a repository: git's error, never an empty list."""
    planted = Planted(tmp_path, env=env)
    outside = planted.base / "plain"
    outside.mkdir()
    planted.enter(monkeypatch, outside)
    status = rust_workspaces.main(["roots"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert "lint:rust: git could not name the tree\n" in err
    assert "not a git repository" in err


def test_resolved_is_the_roots_or_none_having_said_why(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The roots; None on an unresolved manifest; None on git, said."""
    planted = Planted(tmp_path, env=env)
    roots: list[str] = ["Cargo.toml", "fuzz/Cargo.toml"]
    planted.enter(monkeypatch)
    assert rust_workspaces.resolved() == roots
    assert not capfd.readouterr().err
    refused = Planted(tmp_path / "outside", "outside", env=env)
    refused.enter(monkeypatch)
    assert rust_workspaces.resolved() is None
    err = capfd.readouterr().err
    assert "belongs to a workspace outside this tree" in err
    outside = planted.base / "plain"
    outside.mkdir()
    planted.enter(monkeypatch, outside)
    assert rust_workspaces.resolved() is None
    err = capfd.readouterr().err
    assert "lint:rust: git could not name the tree\n" in err


@pytest.mark.parametrize("argv", [[], ["root"], ["roots", "x"]])
def test_anything_but_roots_is_a_usage_error(
    argv: list[str], capfd: pytest.CaptureFixture[str]
) -> None:
    """No subcommand, or another: exit 2 and the usage line."""
    assert rust_workspaces.main(argv) == 2
    assert capfd.readouterr() == ("", "usage: belt rust-workspaces roots\n")


# org_rust_workspace_roots fills the array, or fails.
@pytest.mark.parametrize("shell", SHELLS)
def test_the_reader_fills_one_element_per_root(
    shell: object, tmp_path: Path, env: support.Env
) -> None:
    """Both roots in order, no empty last element, no file left."""
    planted = Planted(tmp_path, env=env)
    done = planted.read(shell)
    left: list[str] = sorted(p.name for p in planted.base.iterdir())
    assert (done.returncode, done.stdout, done.stderr) == (
        0,
        "Cargo.toml\nfuzz/Cargo.toml\n",
        "",
    )
    assert left == LEFT


@pytest.mark.parametrize("shell", SHELLS)
def test_the_reader_fails_when_the_helper_fails(
    shell: object, tmp_path: Path, env: support.Env
) -> None:
    """A finding: exit 1 under errexit, the message, nothing filled."""
    planted = Planted(tmp_path, "fail", env=env)
    done = planted.read(shell)
    left: list[str] = sorted(p.name for p in planted.base.iterdir())
    assert (done.returncode, done.stdout) == (1, "")
    assert FAILED_MESSAGE in done.stderr
    assert left == LEFT


BUILT_AS_HOLDS_THE_FLAGS_AND_PUTS_THEM_BACK_ROWS: list[tuple[dict[str, str], str]] = [
    ({}, "--cfg fuzzing"),
    ({"RUSTFLAGS": "-Cx"}, "-Cx --cfg fuzzing"),
]


class Fuzzing:
    """A cargo-fuzz root is built with `--cfg fuzzing`, by its marker (#2093)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path

    def manifest(self, text: str) -> str:
        """Write a root manifest in a scratch directory.

        Returns:
            Its path.

        """
        with tempfile.NamedTemporaryFile(
            "w", dir=self.tmp, suffix=".toml", delete=False, encoding="utf-8"
        ) as written:
            written.write(text)
        return written.name


@fixture
def fuzzing_state(tmp_path: Path) -> Fuzzing:
    """Give each row its own Fuzzing."""
    return Fuzzing(tmp_path)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("[package]\nmetadata = { cargo-fuzz = true }\n", True),
        ('[package]\nmetadata = { cargo-fuzz = "true" }\n', False),
        ("[package]\nmetadata = { cargo-fuzz = false }\n", False),
        ("[package]\nmetadata = { other = true }\n", False),
        ("[package]\nmetadata = 1\n", False),
        ('[package]\nname = "fuzz"\n', False),
        ("package = 1\n", False),
        ("[workspace]\nmetadata = { cargo-fuzz = true }\n", False),
    ],
)
def test_only_a_true_cargo_fuzz_in_package_metadata_is_the_marker(
    fuzzing_state: Fuzzing, text: object, want: object
) -> None:
    """Every other shape of the manifest is not a cargo-fuzz crate."""
    assert want == rust_workspaces.fuzzing(fuzzing_state.manifest(text))


def test_the_cfg_is_appended_to_the_variable_cargo_reads(
    fuzzing_state: Fuzzing,
) -> None:
    """RUSTFLAGS, or the encoded variable whenever it is set at all."""
    marked = fuzzing_state.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
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
        assert want == rust_workspaces.rustflags(marked, environ)


def test_an_unmarked_root_adds_nothing(fuzzing_state: Fuzzing) -> None:
    """No marker: no variable is named, whatever is already set."""
    plain = fuzzing_state.manifest("[workspace]\n")
    nothing: dict[str, str] = {}
    added = rust_workspaces.rustflags(plain, {"RUSTFLAGS": "-Cx"})
    assert nothing == added


@pytest.mark.parametrize(
    ("before", "inside"), BUILT_AS_HOLDS_THE_FLAGS_AND_PUTS_THEM_BACK_ROWS
)
def test_built_as_holds_the_flags_and_puts_them_back(
    fuzzing_state: Fuzzing,
    before: dict[str, str],
    inside: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set inside, restored after: a value kept, an absent one removed."""
    marked = fuzzing_state.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in before.items():
        monkeypatch.setenv(name, value)
    with rust_workspaces.built_as(marked):
        assert inside == os.environ["RUSTFLAGS"]
    assert before.get("RUSTFLAGS") == os.environ.get("RUSTFLAGS")


def test_built_as_restores_after_a_raise(
    fuzzing_state: Fuzzing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside still puts the environment back."""
    marked = fuzzing_state.manifest("[package]\nmetadata = { cargo-fuzz = true }\n")
    empty: dict[str, str] = {}
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in empty.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(KeyError), rust_workspaces.built_as(marked):
        _ = os.environ["PLANTED_ABSENT"]
    assert "RUSTFLAGS" not in os.environ

"""The gate's tool cache keeps mise-action's name and adds the belt (#1406, #1414).

The fixture is not written by hand: the key below is the one mise-action
v5.2.7 logged restoring the canon's own gate on main, run 44151533572 at
d46ae3b (2026-11-17), and the hash is that commit's mise/config.toml then
mise.toml, digested as @actions/glob does — reproduced before this was
written. If these rows pass and the first run after #1406 still misses,
the reproduction is wrong, not the fixture. #1414 appends the belt
lockfile's digest to that key; the rows hold both halves. #1864 adds the
cargo caches' compiler digest, read from `rustc -vV`; #2045 the digest
of every other input that decides what the cargo caches hold.
"""

import contextlib
import hashlib
import io
import platform
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from belt import gate_cache_key
from tests import hermetic

LOGGED = (
    "mise-v2-linux-x64-ubuntu26-2026.11.6-p692-"
    "603294384eb88f9566d4631a33547c8f7b916d5edf000a69b2836d3af20d0ca8"
)
HASH = LOGGED.rsplit("-", 1)[1]
# 2026-11-17T18:47:13Z, when the run restored it.
WHEN = 1794941233.0
BELT = "b" * 64
RUNNER = {"ImageOS": "ubuntu26", "HOME": "/home/runner", "ORG_MISE_CONFIG_HASH": HASH}


class TestKey:
    """The key is the action's, byte for byte."""

    def test_the_logged_key(self) -> None:
        """Run 44151533572's inputs render the key it restored, then the belt."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        assert f"{LOGGED}-b{BELT}" == out["key"]

    def test_the_path_is_the_absolute_data_dir(self) -> None:
        """The path list is part of the cache version, so it must match."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        assert out["dir"] == "/home/runner/.local/share/mise"

    def test_the_data_dir_follows_mise(self) -> None:
        """MISE_DATA_DIR wins, then XDG_DATA_HOME, as the action resolves it."""
        env = {**RUNNER, "XDG_DATA_HOME": "/x"}
        assert gate_cache_key.data_dir(env) == "/x/mise"
        assert gate_cache_key.data_dir({**env, "MISE_DATA_DIR": "/m"}) == "/m"

    def test_the_fallback_stops_before_the_version(self) -> None:
        """A mise bump still restores the previous tools."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.12.0", "linux-x64", WHEN, BELT),
        )
        assert out["prefix"] == "mise-v2-linux-x64-ubuntu26-"
        assert LOGGED.startswith(out["prefix"])

    def test_a_new_name_shares_no_prefix(self) -> None:
        """The hand-moved name (#1165) still invalidates everything."""
        out = gate_cache_key.names(
            "mise-v3",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        assert not LOGGED.startswith(out["prefix"])

    def test_the_period_rolls_every_thirty_days(self) -> None:
        """The action's cache_max_age_days default is in the key."""
        later = WHEN + gate_cache_key.PERIOD_SECONDS
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", later, BELT),
        )
        assert "-p693-" in out["key"]

    def test_no_config_and_no_image(self) -> None:
        """An empty hash and a missing ImageOS render as the action does."""
        env = {"HOME": "/h"}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        assert (
            f"mise-v2-linux-x64-self-hosted-2026.11.6-p692-no-config-b{BELT}"
            == out["key"]
        )

    def test_mise_env_joins_with_dashes(self) -> None:
        """MISE_ENV's commas become dashes, after the version."""
        env = {**RUNNER, "MISE_ENV": "ci,linux"}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        assert "-2026.11.6-ci-linux-p692-" in out["key"]

    def test_the_left_out_digest_goes_last(self) -> None:
        """A narrowed belt (#1410) moves the key and keeps the prefix."""
        env = {**RUNNER, "ORG_GATE_BELT_DIGEST": "0123456789ab"}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        assert f"{LOGGED}-b{BELT}-off0123456789ab" == out["key"]
        assert out["key"].startswith(out["prefix"])

    def test_nothing_left_out_adds_nothing(self) -> None:
        """An empty digest is the full belt: no suffix."""
        env = {**RUNNER, "ORG_GATE_BELT_DIGEST": ""}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        assert f"{LOGGED}-b{BELT}" == out["key"]


class TestInputs:
    """The version and target are read, never assumed."""

    def test_one_version(self) -> None:
        """Every pin agrees: that is the version."""
        text = "        with:\n          version: 2026.11.6\n" * 3
        assert gate_cache_key.mise_version(text) == "2026.11.6"

    def test_disagreeing_pins_are_red(self) -> None:
        """Two versions would name a cache the install does not make."""
        text = "          version: 2026.11.6\n          version: 2026.11.5\n"
        with pytest.raises(ValueError, match=r"found \['2026.11.5', '2026.11.6'\]"):
            gate_cache_key.mise_version(text)

    def test_no_pin_is_red(self) -> None:
        """No pin names nothing."""
        with pytest.raises(ValueError, match="found none"):
            gate_cache_key.mise_version("jobs: {}\n")

    def test_the_gate_pins_one_version(self) -> None:
        """The real ci.yml reads clean."""
        ci = hermetic.CANON / ".github" / "workflows" / "ci.yml"
        assert re.search(
            r"^\d{4}\.", gate_cache_key.mise_version(ci.read_text("utf-8"))
        )

    def test_targets(self) -> None:
        """Node's names for the runner's machines."""
        assert gate_cache_key.target("Linux", "x86_64") == "linux-x64"
        assert gate_cache_key.target("Darwin", "arm64") == "macos-arm64"
        with pytest.raises(ValueError, match="no mise-action target"):
            gate_cache_key.target("Windows", "AMD64")


LOCK = """[[tools.ruff]]
version = "{}"
backend = "aqua:astral-sh/ruff"
"""
CONFIG = """[tools]
ruff = "0.17.4"

[tasks."lint:python"]
run = "{}"
"""


def run(canon: Path) -> dict[str, str]:
    """Run the helper over a canon tree as the workflow does.

    Returns:
        The outputs it wrote.

    Raises:
        AssertionError: the helper exited non-zero.

    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        env = {**RUNNER, "GITHUB_OUTPUT": str(out)}
        with pytest.MonkeyPatch.context() as patched, redirect_stdout(io.StringIO()):
            hermetic.patch_env(patched, env, clear=True)
            patched.setattr(platform, "system", hermetic.returning("Linux"))
            patched.setattr(platform, "machine", hermetic.returning("x86_64"))
            code = gate_cache_key.main(["tool", "mise-v2", str(canon)], now=WHEN)
        if code != 0:
            msg = f"gate-cache-key exited {code}"
            raise AssertionError(msg)
        lines = out.read_text("utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines)


def canon(root: Path, ruff: str = "0.17.4", body: str = "ruff check") -> Path:
    """Write a canon tree: the pin in ci.yml, a belt lockfile and config.

    Returns:
        The tree's root.

    """
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "mise").mkdir()
    ci = root / ".github" / "workflows" / "ci.yml"
    ci.write_text("          version: 2026.11.6\n", "utf-8")
    (root / "mise" / "mise.lock").write_text(LOCK.format(ruff), "utf-8")
    (root / "mise" / "config.toml").write_text(CONFIG.format(body), "utf-8")
    return root


class TestMain:
    """The workflow reads three outputs."""

    def test_writes_the_outputs(self) -> None:
        """key, prefix and dir land in GITHUB_OUTPUT."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            lock = (tree / "mise" / "mise.lock").read_bytes()
            got = run(tree)
        belt = hashlib.sha256(lock).hexdigest()
        assert f"{LOGGED}-b{belt}" == got["key"]
        assert got["prefix"] == "mise-v2-linux-x64-ubuntu26-"
        assert got["dir"] == "/home/runner/.local/share/mise"

    def test_a_missing_tree_is_red(self) -> None:
        """A canon tree without its files is a red step, not a key."""
        err = io.StringIO()
        with pytest.MonkeyPatch.context() as patched, redirect_stderr(err):
            hermetic.name_task(patched, "")
            patched.setattr(sys, "argv", ["gate_cache_key.py"])
            code = gate_cache_key.main(["tool", "mise-v2", "/nonexistent"])
            assert code == 1
        assert "gate_cache_key.py:" in err.getvalue()

    def test_a_tree_without_a_belt_lockfile_is_red(self) -> None:
        """No lockfile, no key: a key that cannot name the belt is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            (tree / "mise" / "mise.lock").unlink()
            err = io.StringIO()
            with redirect_stderr(err):
                assert gate_cache_key.main(["tool", "mise-v2", str(tree)]) == 1
        assert "mise.lock" in err.getvalue()


class TestBelt:
    """The key moves with the belt's tools and not with its tasks (#1414)."""

    def test_a_belt_tool_bump_names_a_new_cache(self) -> None:
        """Two lockfiles differing in one tool's version give two keys."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = run(canon(Path(a), ruff="0.17.4"))
            new = run(canon(Path(b), ruff="0.17.5"))
        assert old["key"] != new["key"]
        assert old["prefix"] == new["prefix"]

    def test_a_task_body_change_keeps_the_cache(self) -> None:
        """Two config.tomls differing only in a task body give one key."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = run(canon(Path(a), body="ruff check"))
            new = run(canon(Path(b), body="ruff check --preview"))
        assert old["key"] == new["key"]

    def test_the_real_belt_lockfile_is_read(self) -> None:
        """The tree's own mise/mise.lock is what the key digests."""
        tree = hermetic.CANON
        got = run(tree)
        lock = (tree / "mise" / "mise.lock").read_bytes()
        assert got["key"].endswith("-b" + hashlib.sha256(lock).hexdigest())


LEG = "jq rust@1.97.1 cargo:cargo-pgrx@0.19.3 aqua:theseus-rs/postgresql-binaries@"


def leg(major: int, install: str) -> dict[str, str]:
    """Name a pgrx leg's cache, at the logged run's inputs (#1408).

    Returns:
        The names the workflow reads.

    """
    facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    return gate_cache_key.names(f"mise-pgrx-v1-pg{major}", RUNNER, facts, install)


class TestInstallList:
    """A job that installs a list keys its cache by the list (#1408)."""

    def test_two_legs_have_two_prefixes(self) -> None:
        """By name: a pg14 leg never falls back to a pg18 leg's cache."""
        pg14, pg18 = leg(14, LEG + "14.24.1"), leg(18, LEG + "18.6.1")
        assert pg14["prefix"] == "mise-pgrx-v1-pg14-linux-x64-ubuntu26-"
        assert pg18["prefix"] == "mise-pgrx-v1-pg18-linux-x64-ubuntu26-"
        assert not pg18["key"].startswith(pg14["prefix"])
        assert not pg14["key"].startswith(pg18["prefix"])

    def test_a_pin_bump_keeps_the_prefix_and_moves_the_key(self) -> None:
        """One leg, two lists: same fallback, different caches."""
        before = leg(18, LEG + "18.6.1")
        after = leg(18, LEG.replace("1.97.1", "1.98.0") + "18.6.1")
        assert before["prefix"] == after["prefix"]
        assert before["key"] != after["key"]
        assert after["key"].startswith(before["prefix"])

    def test_the_digest_sits_before_the_belt(self) -> None:
        """The list's digest is in the key, after the config hash."""
        digest = gate_cache_key.install_digest(LEG + "18.6.1")
        assert leg(18, LEG + "18.6.1")["key"].endswith(
            f"-2026.11.6-p692-{HASH}-i{digest}-b{BELT}"
        )

    def test_the_digest_is_mise_actions(self) -> None:
        """Order and flags do not change it: sorted tools, flags dropped."""
        assert gate_cache_key.install_digest("b a") == gate_cache_key.install_digest(
            "--locked a  b"
        )
        assert (
            gate_cache_key.install_digest("a b")
            == "c8687a08aa5d6ed2044328fa6a697ab8e96dc34291e8c2034ae8c38e6fcc6d65"
        )

    @pytest.mark.parametrize("install", ("", "  ", "--locked"))
    def test_no_list_is_todays_key(self, install: object) -> None:
        """The gate passes no list, and its key does not move by a byte."""
        facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        out = gate_cache_key.names("mise-v2", RUNNER, facts, install)
        assert f"{LOGGED}-b{BELT}" == out["key"]
        assert out["prefix"] == "mise-v2-linux-x64-ubuntu26-"

    def test_main_reads_the_list(self) -> None:
        """`--install` reaches the key the workflow reads."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            out = Path(tmp) / "out"
            env = {**RUNNER, "GITHUB_OUTPUT": str(out)}
            with (
                pytest.MonkeyPatch.context() as patched,
                redirect_stdout(io.StringIO()),
            ):
                hermetic.patch_env(patched, env, clear=True)
                patched.setattr(platform, "system", hermetic.returning("Linux"))
                patched.setattr(platform, "machine", hermetic.returning("x86_64"))
                argv = ["tool", "mise-pgrx-v1-pg18", str(tree)]
                argv += ["--install", "jq rust@1"]
                assert gate_cache_key.main(argv, now=WHEN) == 0
            written = out.read_text("utf-8")
        digest = gate_cache_key.install_digest("jq rust@1")
        assert f"-i{digest}-b" in written
        assert "prefix=mise-pgrx-v1-pg18-linux-x64-ubuntu26-\n" in written


# `rustc -vV` as Rust 1.97.1 prints it on the gate's runner. The rows
# below vary one line of it at a time.
RUSTC = """rustc 1.97.1 (6b00bc388 2026-08-05)
binary: rustc
commit-hash: 6b00bc3880198600130e1cf62b8f8a93494488cc
commit-date: 2026-08-05
host: x86_64-unknown-linux-gnu
release: 1.97.1
LLVM version: 21.1.2
"""


def rustc_on_path(tmp: Path, script: str) -> str:
    """Write a `rustc` that runs `script`, in a directory of its own.

    Returns:
        A PATH holding only that directory.

    """
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    rustc = bin_dir / "rustc"
    rustc.write_text(f"#!/bin/sh\n{script}\n", "utf-8")
    rustc.chmod(rustc.stat().st_mode | stat.S_IXUSR)
    return str(bin_dir)


# The workflow's ORG_MISE_CONFIG_GLOBS, read from the real gate, so the
# rows hash exactly the configs the tool key does.
CI = hermetic.CANON / ".github" / "workflows" / "ci.yml"
GLOBS = "\n".join(
    line.strip()
    for line in CI
    .read_text("utf-8")
    .split("ORG_MISE_CONFIG_GLOBS: |-\n", 1)[1]
    .split("\n\n", 1)[0]
    .splitlines()
)
CANON_REF = "v1.76.0"
# A Rust tree as edtf's was before pg19: one crate, one major.
TREE = {
    "Cargo.toml": '[package]\nname = "edtf"\n\n[features]\npg18 = []\n',
    "Cargo.lock": "version = 4\n",
    "mise.toml": '[tools]\nrust = "1.97.1"\n',
}


def git_tree(root: Path, files: dict[str, str]) -> Path:
    """Write `files` into a fresh repository at `root` and track them.

    Returns:
        `root`.

    """
    git = hermetic.tool("git")
    env = hermetic.env()
    subprocess.run([git, "init", "-q", str(root)], env=env, check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, "utf-8")
    subprocess.run([git, "-C", str(root), "add", "--", *files], env=env, check=True)
    return root


def cargo_main(
    path: str,
    tree: Path | None = None,
    canon_ref: str = CANON_REF,
    **runner: str,
) -> tuple[int, str, str]:
    """Run `cargo` in `tree` with PATH set to `path`, as the workflow does.

    Returns:
        The exit code, what it wrote to GITHUB_OUTPUT and its stderr.

    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        out.touch()
        where = tree or git_tree(Path(tmp) / "tree", TREE)
        env = {
            **RUNNER,
            "ImageVersion": "20261116.1.0",
            "ORG_MISE_CONFIG_GLOBS": GLOBS,
            "GITHUB_OUTPUT": str(out),
            "PATH": path,
            **runner,
        }
        err = io.StringIO()
        with (
            pytest.MonkeyPatch.context() as patched,
            contextlib.chdir(where),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            hermetic.patch_env(patched, env, clear=True)
            code = gate_cache_key.main(["cargo", "--canon-ref", canon_ref])
        return code, out.read_text("utf-8"), err.getvalue()


def outputs(written: str) -> dict[str, str]:
    """Read what a step wrote to GITHUB_OUTPUT.

    Returns:
        Each `name=value` line, by name.

    """
    out: dict[str, str] = {}
    for line in written.splitlines():
        name, _, value = line.partition("=")
        out[name] = value
    return out


class TestCompiler:
    """The cargo caches' key names the compiler that fills them (#1864)."""

    def test_the_same_compiler_gives_the_same_digest(self) -> None:
        """One `rustc -vV`, one digest: a rerun hits."""
        assert gate_cache_key.rustc_digest(RUSTC) == gate_cache_key.rustc_digest(RUSTC)

    def test_each_line_moves_the_digest(self) -> None:
        """A change to any one line of `rustc -vV` names a new cache."""
        rows = [
            ("release", "release: 1.97.1", "release: 1.100.0"),
            ("LLVM", "LLVM version: 21.1.2", "LLVM version: 22.1.0"),
            ("commit", "6b00bc3880198600130e1cf62b8f8a93494488cc", "0" * 40),
            ("host", "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"),
        ]
        before = gate_cache_key.rustc_digest(RUSTC)
        for label, old, new in rows:
            assert old in RUSTC
            after = gate_cache_key.rustc_digest(RUSTC.replace(old, new))
            assert before != after

    def test_text_naming_no_compiler_is_red(self) -> None:
        """Empty output is refused, never digested into a key."""
        for text in ("", "binary: rustc\n"):
            with (
                self.subTest(text=text),
                pytest.raises(ValueError, match="printed no release line"),
            ):
                gate_cache_key.rustc_digest(text)

    def test_main_writes_the_digest_of_the_rustc_on_path(self) -> None:
        """`cargo` asks PATH's rustc and writes `digest` for the key."""
        with tempfile.TemporaryDirectory() as tmp:
            path = rustc_on_path(Path(tmp), f"printf '%s' '{RUSTC}'")
            code, written, _ = cargo_main(path)
        assert code == 0
        assert gate_cache_key.rustc_digest(RUSTC) == outputs(written)["digest"]
        want: set[str] = {"digest", "inputs"}
        got: set[str] = set(outputs(written))
        assert want == got

    def test_two_releases_on_path_give_two_keys(self) -> None:
        """Two toolchains differing only in release write two digests."""
        bumped = RUSTC.replace("release: 1.97.1", "release: 1.100.0")
        written: list[str] = []
        for text in (RUSTC, bumped):
            with tempfile.TemporaryDirectory() as tmp:
                path = rustc_on_path(Path(tmp), f"printf '%s' '{text}'")
                written.append(outputs(cargo_main(path)[1])["digest"])
        assert written[0] != written[1]

    def test_no_rustc_is_red(self) -> None:
        """A job with a cargo cache and no compiler fails by name."""
        with tempfile.TemporaryDirectory() as tmp:
            code, written, err = cargo_main(tmp)
        assert code == 1
        assert written == ""
        assert "no rustc on PATH" in err

    def test_a_failing_rustc_is_red(self) -> None:
        """A rustup proxy with no toolchain installed fails by its message."""
        with tempfile.TemporaryDirectory() as tmp:
            script = "echo 'toolchain 1.100.0 is not installed' >&2; exit 1"
            code, written, err = cargo_main(rustc_on_path(Path(tmp), script))
        assert code == 1
        assert written == ""
        assert "-vV exited 1: toolchain 1.100.0 is not installed" in err


@pytest.mark.usefixtures("_set_up_test_cargo_inputs")
class TestCargoInputs:
    """The cargo key names every declared input of its builds (#2045)."""

    @pytest.fixture
    def _set_up_test_cargo_inputs(self, tmp_path: Path) -> None:
        """Put a `rustc` on PATH for every row."""
        self.path = rustc_on_path(Path(str(tmp_path)), f"printf '%s' '{RUSTC}'")
        self.trees = 0

    def run_in(
        self,
        files: dict[str, str],
        untracked: dict[str, str] | None = None,
        **kw: str,
    ) -> dict[str, str]:
        """Run `cargo` in a tree of `files`, plus `untracked` beside them.

        Returns:
            The step's outputs.

        """
        self.trees += 1
        tree = git_tree(Path(str(tmp_path)) / f"tree{self.trees}", files)
        for name, text in (untracked or {}).items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text, "utf-8")
        code, written, err = cargo_main(self.path, tree, **kw)
        assert code == 0, err
        return outputs(written)

    def assert_moves_inputs_only(
        self, before: dict[str, str], after: dict[str, str]
    ) -> None:
        """Hold the compiler's digest and move `inputs`."""
        assert before["digest"] == after["digest"]
        assert before["inputs"] != after["inputs"]

    def test_a_feature_change_moves_inputs(self) -> None:
        """Edtf's pg19: one manifest's features, the measured case."""
        pg19 = TREE["Cargo.toml"] + "pg19 = []\n"
        self.assert_moves_inputs_only(
            self.run_in(TREE), self.run_in({**TREE, "Cargo.toml": pg19})
        )

    def test_a_member_manifest_change_moves_inputs(self) -> None:
        """A workspace member's manifest is named as the root's is."""
        member = {**TREE, "crates/pg/Cargo.toml": '[package]\nname = "pg"\n'}
        bumped = {**member, "crates/pg/Cargo.toml": '[package]\nname = "pg2"\n'}
        self.assert_moves_inputs_only(self.run_in(member), self.run_in(bumped))

    def test_a_mise_config_change_moves_inputs(self) -> None:
        """A tool pin (the Postgres pgrx records the path of) is an input."""
        pinned = {**TREE, "mise.toml": TREE["mise.toml"] + 'postgres = "18.1"\n'}
        self.assert_moves_inputs_only(self.run_in(TREE), self.run_in(pinned))

    def test_a_canon_change_moves_inputs(self) -> None:
        """The belt's flags and variables reach cargo: its ref is an input."""
        self.assert_moves_inputs_only(
            self.run_in(TREE), self.run_in(TREE, canon_ref="v1.77.0")
        )

    def test_an_image_build_number_holds_inputs(self) -> None:
        """Two fleets, one toolchain: the build number names nothing (#2066)."""
        assert self.run_in(TREE, ImageVersion="20261116.88.1") == self.run_in(
            TREE, ImageVersion="20261116.202.1"
        )

    def test_identical_trees_give_the_same_inputs(self) -> None:
        """Two runs over the same tree hit the same entry."""
        assert self.run_in(TREE) == self.run_in(TREE)

    def test_an_untracked_manifest_changes_nothing(self) -> None:
        """A file CI never checks out builds nothing and names nothing."""
        stray = {"Cargo.toml": "[workspace]\n", "scratch/Cargo.toml": "x\n"}
        assert self.run_in({
            k: v for k, v in TREE.items() if k != "Cargo.toml"
        }) == self.run_in(
            {k: v for k, v in TREE.items() if k != "Cargo.toml"},
            untracked=stray,
        )

    def test_no_canon_ref_is_red(self) -> None:
        """An empty ref is refused, never hashed into a key."""
        tree = git_tree(Path(str(tmp_path)) / "tree", TREE)
        code, written, err = cargo_main(self.path, tree, canon_ref="")
        assert code == 1
        assert written == ""
        assert "no --canon-ref" in err

    def test_no_globs_is_red(self) -> None:
        """A workflow that sets no mise globs would omit the tool pins."""
        tree = git_tree(Path(str(tmp_path)) / "tree", TREE)
        code, written, err = cargo_main(self.path, tree, ORG_MISE_CONFIG_GLOBS="")
        assert code == 1
        assert written == ""
        assert "ORG_MISE_CONFIG_GLOBS is unset" in err

    def test_the_globs_are_the_tool_keys(self) -> None:
        """The rows read the gate's own list, which names mise.toml."""
        assert "mise.toml" in GLOBS.splitlines()
        assert ".tool-versions" in GLOBS.splitlines()


# `--version` as the gate's runner prints it, first line (#2066).
CC = "x86_64-linux-gnu-gcc-15 (Ubuntu 15.2.0-16ubuntu1) 15.2.0"
CLANG = "Ubuntu clang version 21.1.8 (6ubuntu1)"
LDD = "ldd (Ubuntu GLIBC 2.43-2ubuntu2.7) 2.43"


@pytest.mark.usefixtures("_set_up_test_toolchain")
class TestToolchain:
    """The cargo key names the toolchain the build takes from the image."""

    @pytest.fixture
    def _set_up_test_toolchain(self, tmp_path: Path) -> None:
        """Put `rustc`, the toolchain and an `xcrun` on PATH for every row.

        The `xcrun` finds the row's `clang` on PATH, so the rows read the
        same stub on Darwin as `clang` on PATH does elsewhere.
        """
        self.path = rustc_on_path(Path(str(tmp_path)), f"printf '%s' '{RUSTC}'")
        self.trees = 0
        self.stub("xcrun", "command -v clang || exit 1")
        for name, text in (("cc", CC), ("c++", CC), ("clang", CLANG), ("ldd", LDD)):
            self.stub(name, f"echo '{text}'")

    def stub(self, name: str, script: str) -> Path:
        """Write a command `name` on the rows' PATH that runs `script`.

        Returns:
            Its path.

        """
        tool = Path(self.path) / name
        tool.write_text(f"#!/bin/sh\n{script}\n", "utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        return tool

    def run_with(self, **kw: str) -> tuple[int, str, str]:
        """Run `cargo` over TREE with the toolchain the stubs now hold.

        Returns:
            The exit code, what it wrote to GITHUB_OUTPUT and its stderr.

        """
        self.trees += 1
        tree = git_tree(Path(str(tmp_path)) / f"tree{self.trees}", TREE)
        return cargo_main(self.path, tree, **kw)

    def run_in(self, **kw: str) -> dict[str, str]:
        """Run `cargo` as `run_with` does, and require it to pass.

        Returns:
            The step's outputs.

        """
        code, written, err = self.run_with(**kw)
        assert code == 0, err
        return outputs(written)

    def assert_moves(self, name: str, **kw: str) -> None:
        """Move `inputs` and hold `digest` on a new `--version` from `name`."""
        before = self.run_in(**kw)
        self.stub(name, "echo 'a newer build'")
        after = self.run_in(**kw)
        assert before["digest"] == after["digest"]
        assert before["inputs"] != after["inputs"]

    def assert_holds(self, name: str, **kw: str) -> None:
        """Hold `inputs` on a new `--version` from `name`."""
        before = self.run_in(**kw)
        self.stub(name, "echo 'a newer build'")
        assert before == self.run_in(**kw)

    def test_a_cc_change_moves_inputs(self) -> None:
        """`cc` on PATH is the C compiler cargo's build scripts run."""
        self.assert_moves("cc")

    def test_a_cxx_change_moves_inputs(self) -> None:
        """`c++` on PATH is the C++ compiler they run."""
        self.assert_moves("c++")

    def test_a_clang_change_moves_inputs(self) -> None:
        """The clang driver pgrx runs, and so its libclang, is an input."""
        self.assert_moves("clang")

    def test_inputs_follow_cc(self) -> None:
        """`$CC` names the compiler, so `cc` on PATH is not read."""
        self.stub("gcc-15", f"echo '{CC}'")
        self.assert_moves("gcc-15", CC="gcc-15")
        self.assert_holds("cc", CC="gcc-15")

    def test_inputs_follow_cxx(self) -> None:
        """`$CXX` names the compiler, so `c++` on PATH is not read."""
        self.stub("g++-15", f"echo '{CC}'")
        self.assert_moves("g++-15", CXX="g++-15")
        self.assert_holds("c++", CXX="g++-15")

    def test_inputs_follow_clang_path(self) -> None:
        """`$CLANG_PATH` is the driver pgrx runs, as pgrx-env.sh leaves it."""
        driver = str(self.stub("clang-21", f"echo '{CLANG}'"))
        self.assert_moves("clang-21", CLANG_PATH=driver)
        self.assert_holds("clang", CLANG_PATH=driver)

    def test_a_libc_change_moves_inputs(self) -> None:
        """The first line of `ldd --version` carries the package revision."""
        before = self.run_in()
        self.stub("ldd", "echo 'ldd (Ubuntu GLIBC 2.43-2ubuntu2.8) 2.43'")
        after = self.run_in()
        assert before["digest"] == after["digest"]
        assert before["inputs"] != after["inputs"]

    def test_no_toolchain_is_absent_not_red(self) -> None:
        """A runner without them builds nothing that needs them."""
        present = self.run_in()
        for name in ("cc", "c++", "clang", "ldd", "xcrun"):
            (Path(self.path) / name).unlink()
        absent = self.run_in()
        assert present["inputs"] != absent["inputs"]

    def test_a_failing_ldd_is_absent(self) -> None:
        """An `ldd` that cannot report a glibc (musl) is not a finding."""
        self.stub("ldd", "exit 1")
        failing = self.run_in()
        (Path(self.path) / "ldd").unlink()
        assert failing == self.run_in()

    def test_a_clang_that_cannot_report_is_red(self) -> None:
        """The key never names a compiler it could not identify."""
        self.stub("clang", "echo broken >&2; exit 3")
        code, written, err = self.run_with()
        assert code == 1
        assert written == ""
        assert "clang --version exited 3" in err


def digest_main(path: str) -> tuple[int, str, str]:
    """Run `digest` with PATH set to `path`, as a coverage script does.

    Returns:
        The exit code, its stdout and its stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    env: dict[str, str] = {"PATH": path, "MISE_TASK_NAME": "coverage:check"}
    with (
        pytest.MonkeyPatch.context() as patched,
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        hermetic.patch_env(patched, env, clear=True)
        code = gate_cache_key.main(["digest"])
    return code, out.getvalue(), err.getvalue()


class TestDirectory:
    """Every coverage directory names its compiler (#1877)."""

    def digest_of(self, script: str) -> tuple[int, str, str]:
        """Run `digest` beside a `rustc` that runs `script`.

        Returns:
            The exit code, its stdout and its stderr.

        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return digest_main(rustc_on_path(Path(tmp.name), script))

    def test_the_same_rustc_gives_the_same_sixteen_hex(self) -> None:
        """One `rustc -vV`, one directory: the cache key's digest, cut to 16."""
        printed = f"printf '%s' '{RUSTC}'"
        first, second = self.digest_of(printed), self.digest_of(printed)
        whole = hashlib.sha256(RUSTC.encode()).hexdigest()
        assert (0, f"{whole[:16]}\n", "") == first
        assert first == second

    def test_a_different_release_gives_a_different_directory(self) -> None:
        """1.97.1 and 1.100.0 differ only in `release:`: two directories."""
        bumped = RUSTC.replace("release: 1.97.1", "release: 1.100.0")
        old = self.digest_of(f"printf '%s' '{RUSTC}'")
        new = self.digest_of(f"printf '%s' '{bumped}'")
        assert (old[0], new[0]) == (0, 0)
        assert re.search(r"^[0-9a-f]{16}\n$", new[1])
        assert old[1] != new[1]

    def test_no_rustc_is_red(self) -> None:
        """No compiler: red by name, and no directory printed."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = digest_main(tmp)
        assert (code, out) == (1, "")
        assert "coverage:check: no rustc on PATH" in err

    def test_a_failing_rustc_is_red(self) -> None:
        """A rustup proxy with no toolchain: red by its message."""
        script = "echo 'toolchain nightly-2026-07-20 is not installed' >&2; exit 1"
        code, out, err = self.digest_of(script)
        assert (code, out) == (1, "")
        assert "-vV exited 1: toolchain nightly-2026-07-20 is not installed" in err

    def test_no_release_line_is_red(self) -> None:
        """Output naming no compiler names no directory."""
        code, out, err = self.digest_of("echo 'binary: rustc'")
        assert (code, out) == (1, "")
        assert "printed no release line" in err

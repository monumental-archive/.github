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

import hashlib
import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from belt import gate_cache_key
from belt.typed_pytest import fixture
from tests import support

LOGGED = (
    "mise-v2-linux-x64-ubuntu26-2026.11.6-p692-"
    "603294384eb88f9566d4631a33547c8f7b916d5edf000a69b2836d3af20d0ca8"
)
HASH = LOGGED.rsplit("-", 1)[1]
# 2026-11-17T18:47:13Z, when the run restored it.
WHEN = 1794941233.0
BELT = "b" * 64
RUNNER = {"ImageOS": "ubuntu26", "HOME": "/home/runner", "ORG_MISE_CONFIG_HASH": HASH}


# The key is the action's, byte for byte.
def test_the_logged_key() -> None:
    """Run 44151533572's inputs render the key it restored, then the belt."""
    out = gate_cache_key.names(
        "mise-v2",
        RUNNER,
        gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
    )
    assert f"{LOGGED}-b{BELT}" == out["key"]


def test_the_path_is_the_absolute_data_dir() -> None:
    """The path list is part of the cache version, so it must match."""
    out = gate_cache_key.names(
        "mise-v2",
        RUNNER,
        gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
    )
    assert out["dir"] == "/home/runner/.local/share/mise"


def test_the_data_dir_follows_mise() -> None:
    """MISE_DATA_DIR wins, then XDG_DATA_HOME, as the action resolves it."""
    env = {**RUNNER, "XDG_DATA_HOME": "/x"}
    assert gate_cache_key.data_dir(env) == "/x/mise"
    assert gate_cache_key.data_dir({**env, "MISE_DATA_DIR": "/m"}) == "/m"


def test_the_fallback_stops_before_the_version() -> None:
    """A mise bump still restores the previous tools."""
    out = gate_cache_key.names(
        "mise-v2",
        RUNNER,
        gate_cache_key.Facts("2026.12.0", "linux-x64", WHEN, BELT),
    )
    assert out["prefix"] == "mise-v2-linux-x64-ubuntu26-"
    assert LOGGED.startswith(out["prefix"])


def test_a_new_name_shares_no_prefix() -> None:
    """The hand-moved name (#1165) still invalidates everything."""
    out = gate_cache_key.names(
        "mise-v3",
        RUNNER,
        gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
    )
    assert not LOGGED.startswith(out["prefix"])


def test_the_period_rolls_every_thirty_days() -> None:
    """The action's cache_max_age_days default is in the key."""
    later = WHEN + gate_cache_key.PERIOD_SECONDS
    out = gate_cache_key.names(
        "mise-v2",
        RUNNER,
        gate_cache_key.Facts("2026.11.6", "linux-x64", later, BELT),
    )
    assert "-p693-" in out["key"]


def test_no_config_and_no_image() -> None:
    """An empty hash and a missing ImageOS render as the action does."""
    env = {"HOME": "/h"}
    out = gate_cache_key.names(
        "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    )
    assert (
        f"mise-v2-linux-x64-self-hosted-2026.11.6-p692-no-config-b{BELT}" == out["key"]
    )


def test_mise_env_joins_with_dashes() -> None:
    """MISE_ENV's commas become dashes, after the version."""
    env = {**RUNNER, "MISE_ENV": "ci,linux"}
    out = gate_cache_key.names(
        "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    )
    assert "-2026.11.6-ci-linux-p692-" in out["key"]


def test_the_left_out_digest_goes_last() -> None:
    """A narrowed belt (#1410) moves the key and keeps the prefix."""
    env = {**RUNNER, "ORG_GATE_BELT_DIGEST": "0123456789ab"}
    out = gate_cache_key.names(
        "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    )
    assert f"{LOGGED}-b{BELT}-off0123456789ab" == out["key"]
    assert out["key"].startswith(out["prefix"])


def test_nothing_left_out_adds_nothing() -> None:
    """An empty digest is the full belt: no suffix."""
    env = {**RUNNER, "ORG_GATE_BELT_DIGEST": ""}
    out = gate_cache_key.names(
        "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    )
    assert f"{LOGGED}-b{BELT}" == out["key"]


# The version and target are read, never assumed.
def test_one_version() -> None:
    """Every pin agrees: that is the version."""
    text = "        with:\n          version: 2026.11.6\n" * 3
    assert gate_cache_key.mise_version(text) == "2026.11.6"


def test_disagreeing_pins_are_red() -> None:
    """Two versions would name a cache the install does not make."""
    text = "          version: 2026.11.6\n          version: 2026.11.5\n"
    with pytest.raises(ValueError, match=r"found \['2026.11.5', '2026.11.6'\]"):
        gate_cache_key.mise_version(text)


def test_no_pin_is_red() -> None:
    """No pin names nothing."""
    with pytest.raises(ValueError, match="found none"):
        gate_cache_key.mise_version("jobs: {}\n")


def test_the_gate_pins_one_version() -> None:
    """The real ci.yml reads clean."""
    ci = support.CANON / ".github" / "workflows" / "ci.yml"
    assert re.search(r"^\d{4}\.", gate_cache_key.mise_version(ci.read_text("utf-8")))


def test_targets() -> None:
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


@fixture
def runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Stand in the gate's runner: its environment, its machine, its outputs.

    Returns:
        The GITHUB_OUTPUT file the workflow reads.

    """
    out = tmp_path / "out"
    out.touch()
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in {**RUNNER, "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(name, value)

    def system() -> str:
        return "Linux"

    def machine() -> str:
        return "x86_64"

    monkeypatch.setattr(platform, "system", system)
    monkeypatch.setattr(platform, "machine", machine)
    return out


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


def outputs(written: str) -> dict[str, str]:
    """Read what a step wrote to GITHUB_OUTPUT, the last line of a name winning.

    Returns:
        Each `name=value` line, by name.

    """
    out: dict[str, str] = {}
    for line in written.splitlines():
        name, _, value = line.partition("=")
        out[name] = value
    return out


# The workflow reads three outputs.
def test_writes_the_outputs(runner: Path, tmp_path: Path) -> None:
    """key, prefix and dir land in GITHUB_OUTPUT."""
    tree = canon(tmp_path / "canon")
    status = gate_cache_key.main(["tool", "mise-v2", str(tree)], now=WHEN)
    got = outputs(runner.read_text("utf-8"))
    belt = hashlib.sha256((tree / "mise" / "mise.lock").read_bytes()).hexdigest()
    assert status == 0
    assert f"{LOGGED}-b{belt}" == got["key"]
    assert got["prefix"] == "mise-v2-linux-x64-ubuntu26-"
    assert got["dir"] == "/home/runner/.local/share/mise"


def test_a_missing_tree_is_red(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A canon tree without its files is a red step, not a key."""
    monkeypatch.setenv("MISE_TASK_NAME", "")
    monkeypatch.setenv("ORG_FINDINGS", "")
    monkeypatch.setattr(sys, "argv", ["gate_cache_key.py"])
    status = gate_cache_key.main(["tool", "mise-v2", "/nonexistent"])
    _out, err = capfd.readouterr()
    assert status == 1
    assert "gate_cache_key.py:" in err


def test_a_tree_without_a_belt_lockfile_is_red(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """No lockfile, no key: a key that cannot name the belt is refused."""
    tree = canon(tmp_path)
    (tree / "mise" / "mise.lock").unlink()
    status = gate_cache_key.main(["tool", "mise-v2", str(tree)])
    _out, err = capfd.readouterr()
    assert status == 1
    assert "mise.lock" in err


# The key moves with the belt's tools and not with its tasks (#1414).
def test_a_belt_tool_bump_names_a_new_cache(runner: Path, tmp_path: Path) -> None:
    """Two lockfiles differing in one tool's version give two keys."""
    first = gate_cache_key.main(
        ["tool", "mise-v2", str(canon(tmp_path / "a", ruff="0.17.4"))], now=WHEN
    )
    old = outputs(runner.read_text("utf-8"))
    second = gate_cache_key.main(
        ["tool", "mise-v2", str(canon(tmp_path / "b", ruff="0.17.5"))], now=WHEN
    )
    new = outputs(runner.read_text("utf-8"))
    assert (first, second) == (0, 0)
    assert old["key"] != new["key"]
    assert old["prefix"] == new["prefix"]


def test_a_task_body_change_keeps_the_cache(runner: Path, tmp_path: Path) -> None:
    """Two config.tomls differing only in a task body give one key."""
    first = gate_cache_key.main(
        ["tool", "mise-v2", str(canon(tmp_path / "a", body="ruff check"))], now=WHEN
    )
    old = outputs(runner.read_text("utf-8"))
    second = gate_cache_key.main(
        ["tool", "mise-v2", str(canon(tmp_path / "b", body="ruff check --preview"))],
        now=WHEN,
    )
    new = outputs(runner.read_text("utf-8"))
    assert (first, second) == (0, 0)
    assert old["key"] == new["key"]


def test_the_real_belt_lockfile_is_read(runner: Path) -> None:
    """The tree's own mise/mise.lock is what the key digests."""
    tree = support.CANON
    status = gate_cache_key.main(["tool", "mise-v2", str(tree)], now=WHEN)
    got = outputs(runner.read_text("utf-8"))
    lock = (tree / "mise" / "mise.lock").read_bytes()
    assert status == 0
    assert got["key"].endswith("-b" + hashlib.sha256(lock).hexdigest())


LEG = "jq rust@1.97.1 cargo:cargo-pgrx@0.19.3 aqua:theseus-rs/postgresql-binaries@"


def leg(major: int, install: str) -> dict[str, str]:
    """Name a pgrx leg's cache, at the logged run's inputs (#1408).

    Returns:
        The names the workflow reads.

    """
    facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    return gate_cache_key.names(f"mise-pgrx-v1-pg{major}", RUNNER, facts, install)


# A job that installs a list keys its cache by the list (#1408).
def test_two_legs_have_two_prefixes() -> None:
    """By name: a pg14 leg never falls back to a pg18 leg's cache."""
    pg14, pg18 = leg(14, LEG + "14.24.1"), leg(18, LEG + "18.6.1")
    assert pg14["prefix"] == "mise-pgrx-v1-pg14-linux-x64-ubuntu26-"
    assert pg18["prefix"] == "mise-pgrx-v1-pg18-linux-x64-ubuntu26-"
    assert not pg18["key"].startswith(pg14["prefix"])
    assert not pg14["key"].startswith(pg18["prefix"])


def test_a_pin_bump_keeps_the_prefix_and_moves_the_key() -> None:
    """One leg, two lists: same fallback, different caches."""
    before = leg(18, LEG + "18.6.1")
    after = leg(18, LEG.replace("1.97.1", "1.98.0") + "18.6.1")
    assert before["prefix"] == after["prefix"]
    assert before["key"] != after["key"]
    assert after["key"].startswith(before["prefix"])


def test_the_digest_sits_before_the_belt() -> None:
    """The list's digest is in the key, after the config hash."""
    digest = gate_cache_key.install_digest(LEG + "18.6.1")
    assert leg(18, LEG + "18.6.1")["key"].endswith(
        f"-2026.11.6-p692-{HASH}-i{digest}-b{BELT}"
    )


def test_the_digest_is_mise_actions() -> None:
    """Order and flags do not change it: sorted tools, flags dropped."""
    assert gate_cache_key.install_digest("b a") == gate_cache_key.install_digest(
        "--locked a  b"
    )
    assert (
        gate_cache_key.install_digest("a b")
        == "c8687a08aa5d6ed2044328fa6a697ab8e96dc34291e8c2034ae8c38e6fcc6d65"
    )


@pytest.mark.parametrize("install", ["", "  ", "--locked"])
def test_no_list_is_todays_key(install: str) -> None:
    """The gate passes no list, and its key does not move by a byte."""
    facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    out = gate_cache_key.names("mise-v2", RUNNER, facts, install)
    assert f"{LOGGED}-b{BELT}" == out["key"]
    assert out["prefix"] == "mise-v2-linux-x64-ubuntu26-"


def test_main_reads_the_list(runner: Path, tmp_path: Path) -> None:
    """`--install` reaches the key the workflow reads."""
    tree = canon(tmp_path / "canon")
    argv = ["tool", "mise-pgrx-v1-pg18", str(tree), "--install", "jq rust@1"]
    status = gate_cache_key.main(argv, now=WHEN)
    written = runner.read_text("utf-8")
    digest = gate_cache_key.install_digest("jq rust@1")
    assert status == 0
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
CI = support.CANON / ".github" / "workflows" / "ci.yml"
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

CARGO = ["cargo", "--canon-ref", CANON_REF]


def git_tree(
    root: Path, files: dict[str, str], git: str, environ: dict[str, str]
) -> Path:
    """Write `files` into a fresh repository at `root` and track them.

    Returns:
        `root`.

    """
    subprocess.run([git, "init", "-q", str(root)], env=environ, check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, "utf-8")
    subprocess.run([git, "-C", str(root), "add", "--", *files], env=environ, check=True)
    return root


class Cargo:
    """The gate's runner as the cargo caches' step meets it (#1864, #2045)."""

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, env: support.Env
    ) -> None:
        """Put a `rustc` on PATH and set the step's environment, nothing else."""
        self.tmp = tmp_path
        self.git_env = env()
        self.git = support.tool("git")
        self.path = rustc_on_path(tmp_path, f"printf '%s' '{RUSTC}'")
        self.out = tmp_path / "out"
        self.out.touch()
        self.trees = 0
        step = {
            **RUNNER,
            "ImageVersion": "20261116.1.0",
            "ORG_MISE_CONFIG_GLOBS": GLOBS,
            "GITHUB_OUTPUT": str(self.out),
            "PATH": self.path,
        }
        for name in list(os.environ):
            monkeypatch.delenv(name)
        for name, value in step.items():
            monkeypatch.setenv(name, value)

    def tree(
        self, files: dict[str, str] = TREE, untracked: dict[str, str] | None = None
    ) -> Path:
        """Track `files` in a repository of their own, `untracked` beside them.

        Returns:
            The tree's root.

        """
        self.trees += 1
        tree = git_tree(self.tmp / f"tree{self.trees}", files, self.git, self.git_env)
        for name, text in (untracked or {}).items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text, "utf-8")
        return tree

    def stub(self, name: str, script: str) -> Path:
        """Write a command `name` on the step's PATH that runs `script`.

        Returns:
            Its path.

        """
        tool = Path(self.path) / name
        tool.write_text(f"#!/bin/sh\n{script}\n", "utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        return tool

    def written(self) -> dict[str, str]:
        """Read the step's outputs so far.

        Returns:
            Each name's last value.

        """
        return outputs(self.out.read_text("utf-8"))


@fixture
def cargo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: support.Env) -> Cargo:
    """Give each row the cargo step's runner.

    Returns:
        The runner.

    """
    return Cargo(tmp_path, monkeypatch, env=env)


def test_the_same_compiler_gives_the_same_digest() -> None:
    """One `rustc -vV`, one digest: a rerun hits."""
    assert gate_cache_key.rustc_digest(RUSTC) == gate_cache_key.rustc_digest(RUSTC)


def test_each_line_moves_the_digest() -> None:
    """A change to any one line of `rustc -vV` names a new cache."""
    rows = [
        ("release", "release: 1.97.1", "release: 1.100.0"),
        ("LLVM", "LLVM version: 21.1.2", "LLVM version: 22.1.0"),
        ("commit", "6b00bc3880198600130e1cf62b8f8a93494488cc", "0" * 40),
        ("host", "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"),
    ]
    before = gate_cache_key.rustc_digest(RUSTC)
    for _label, old, new in rows:
        assert old in RUSTC
        after = gate_cache_key.rustc_digest(RUSTC.replace(old, new))
        assert before != after


@pytest.mark.parametrize("text", ["", "binary: rustc\n"])
def test_text_naming_no_compiler_is_red(text: str) -> None:
    """Empty output is refused, never digested into a key."""
    with pytest.raises(ValueError, match="printed no release line"):
        gate_cache_key.rustc_digest(text)


def test_main_writes_the_digest_of_the_rustc_on_path(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cargo` asks PATH's rustc and writes `digest` for the key."""
    monkeypatch.chdir(cargo.tree())
    status = gate_cache_key.main(CARGO)
    got = cargo.written()
    assert status == 0
    assert gate_cache_key.rustc_digest(RUSTC) == got["digest"]
    want: set[str] = {"digest", "inputs"}
    assert want == set(got)


def test_two_releases_on_path_give_two_keys(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two toolchains differing only in release write two digests."""
    bumped = RUSTC.replace("release: 1.97.1", "release: 1.100.0")
    monkeypatch.chdir(cargo.tree())
    first = gate_cache_key.main(CARGO)
    old = cargo.written()["digest"]
    cargo.stub("rustc", f"printf '%s' '{bumped}'")
    second = gate_cache_key.main(CARGO)
    new = cargo.written()["digest"]
    assert (first, second) == (0, 0)
    assert old != new


def test_no_rustc_is_red(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A job with a cargo cache and no compiler fails by name."""
    monkeypatch.chdir(cargo.tree())
    monkeypatch.setenv("PATH", str(cargo.tmp / "tree1"))
    status = gate_cache_key.main(CARGO)
    _out, err = capfd.readouterr()
    assert status == 1
    assert not cargo.out.read_text("utf-8")
    assert "no rustc on PATH" in err


def test_a_failing_rustc_is_red(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A rustup proxy with no toolchain installed fails by its message."""
    cargo.stub("rustc", "echo 'toolchain 1.100.0 is not installed' >&2; exit 1")
    monkeypatch.chdir(cargo.tree())
    status = gate_cache_key.main(CARGO)
    _out, err = capfd.readouterr()
    assert status == 1
    assert not cargo.out.read_text("utf-8")
    assert "-vV exited 1: toolchain 1.100.0 is not installed" in err


# The cargo key names every declared input of its builds (#2045).
def assert_moves_inputs_only(before: dict[str, str], after: dict[str, str]) -> None:
    """Hold the compiler's digest and move `inputs`."""
    assert before["digest"] == after["digest"]
    assert before["inputs"] != after["inputs"]


def test_a_feature_change_moves_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edtf's pg19: one manifest's features, the measured case."""
    pg19 = TREE["Cargo.toml"] + "pg19 = []\n"
    monkeypatch.chdir(cargo.tree())
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.chdir(cargo.tree({**TREE, "Cargo.toml": pg19}))
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, cargo.written())


def test_a_member_manifest_change_moves_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace member's manifest is named as the root's is."""
    member = {**TREE, "crates/pg/Cargo.toml": '[package]\nname = "pg"\n'}
    bumped = {**member, "crates/pg/Cargo.toml": '[package]\nname = "pg2"\n'}
    monkeypatch.chdir(cargo.tree(member))
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.chdir(cargo.tree(bumped))
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, cargo.written())


def test_a_mise_config_change_moves_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool pin (the Postgres pgrx records the path of) is an input."""
    pinned = {**TREE, "mise.toml": TREE["mise.toml"] + 'postgres = "18.1"\n'}
    monkeypatch.chdir(cargo.tree())
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.chdir(cargo.tree(pinned))
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, cargo.written())


def test_a_canon_change_moves_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The belt's flags and variables reach cargo: its ref is an input."""
    monkeypatch.chdir(cargo.tree())
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    second = gate_cache_key.main(["cargo", "--canon-ref", "v1.77.0"])
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, cargo.written())


def test_an_image_build_number_holds_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two fleets, one toolchain: the build number names nothing (#2066)."""
    monkeypatch.chdir(cargo.tree())
    monkeypatch.setenv("ImageVersion", "20261116.88.1")
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.setenv("ImageVersion", "20261116.202.1")
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert before == cargo.written()


def test_identical_trees_give_the_same_inputs(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs over the same tree hit the same entry."""
    monkeypatch.chdir(cargo.tree())
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.chdir(cargo.tree())
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert before == cargo.written()


def test_an_untracked_manifest_changes_nothing(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file CI never checks out builds nothing and names nothing."""
    stray = {"Cargo.toml": "[workspace]\n", "scratch/Cargo.toml": "x\n"}
    tracked = {k: v for k, v in TREE.items() if k != "Cargo.toml"}
    monkeypatch.chdir(cargo.tree(tracked))
    first = gate_cache_key.main(CARGO)
    before = cargo.written()
    monkeypatch.chdir(cargo.tree(tracked, untracked=stray))
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert before == cargo.written()


def test_no_canon_ref_is_red(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An empty ref is refused, never hashed into a key."""
    monkeypatch.chdir(cargo.tree())
    status = gate_cache_key.main(["cargo", "--canon-ref", ""])
    _out, err = capfd.readouterr()
    assert status == 1
    assert not cargo.out.read_text("utf-8")
    assert "no --canon-ref" in err


def test_no_globs_is_red(
    cargo: Cargo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A workflow that sets no mise globs would omit the tool pins."""
    monkeypatch.chdir(cargo.tree())
    monkeypatch.setenv("ORG_MISE_CONFIG_GLOBS", "")
    status = gate_cache_key.main(CARGO)
    _out, err = capfd.readouterr()
    assert status == 1
    assert not cargo.out.read_text("utf-8")
    assert "ORG_MISE_CONFIG_GLOBS is unset" in err


def test_the_globs_are_the_tool_keys() -> None:
    """The rows read the gate's own list, which names mise.toml."""
    assert "mise.toml" in GLOBS.splitlines()
    assert ".tool-versions" in GLOBS.splitlines()


# `--version` as the gate's runner prints it, first line (#2066).
CC = "x86_64-linux-gnu-gcc-15 (Ubuntu 15.2.0-16ubuntu1) 15.2.0"
CLANG = "Ubuntu clang version 21.1.8 (6ubuntu1)"
LDD = "ldd (Ubuntu GLIBC 2.43-2ubuntu2.7) 2.43"
NEWER = "echo 'a newer build'"


@fixture
def toolchain(cargo: Cargo, monkeypatch: pytest.MonkeyPatch) -> Cargo:
    """Put the image's toolchain and an `xcrun` on PATH, in a tracked tree.

    The `xcrun` finds the row's `clang` on PATH, so the rows read the
    same stub on Darwin as `clang` on PATH does elsewhere.

    Returns:
        The cargo step's runner, standing in the tree.

    """
    cargo.stub("xcrun", "command -v clang || exit 1")
    for name, text in (("cc", CC), ("c++", CC), ("clang", CLANG), ("ldd", LDD)):
        cargo.stub(name, f"echo '{text}'")
    monkeypatch.chdir(cargo.tree())
    return cargo


def test_a_cc_change_moves_inputs(toolchain: Cargo) -> None:
    """`cc` on PATH is the C compiler cargo's build scripts run."""
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub("cc", NEWER)
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, toolchain.written())


def test_a_cxx_change_moves_inputs(toolchain: Cargo) -> None:
    """`c++` on PATH is the C++ compiler they run."""
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub("c++", NEWER)
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, toolchain.written())


def test_a_clang_change_moves_inputs(toolchain: Cargo) -> None:
    """The clang driver pgrx runs, and so its libclang, is an input."""
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub("clang", NEWER)
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, toolchain.written())


@pytest.mark.parametrize(
    ("variable", "named", "default"),
    [("CC", "gcc-15", "cc"), ("CXX", "g++-15", "c++")],
)
def test_inputs_follow_the_named_compiler(
    toolchain: Cargo,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    named: str,
    default: str,
) -> None:
    """`$CC` and `$CXX` name the compiler, so the one on PATH is not read."""
    toolchain.stub(named, f"echo '{CC}'")
    monkeypatch.setenv(variable, named)
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub(named, NEWER)
    second = gate_cache_key.main(CARGO)
    moved = toolchain.written()
    toolchain.stub(default, NEWER)
    third = gate_cache_key.main(CARGO)
    assert (first, second, third) == (0, 0, 0)
    assert_moves_inputs_only(before, moved)
    assert moved == toolchain.written()


def test_inputs_follow_clang_path(
    toolchain: Cargo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`$CLANG_PATH` is the driver pgrx runs, as pgrx-env.sh leaves it."""
    monkeypatch.setenv("CLANG_PATH", str(toolchain.stub("clang-21", f"echo '{CLANG}'")))
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub("clang-21", NEWER)
    second = gate_cache_key.main(CARGO)
    moved = toolchain.written()
    toolchain.stub("clang", NEWER)
    third = gate_cache_key.main(CARGO)
    assert (first, second, third) == (0, 0, 0)
    assert_moves_inputs_only(before, moved)
    assert moved == toolchain.written()


def test_a_libc_change_moves_inputs(toolchain: Cargo) -> None:
    """The first line of `ldd --version` carries the package revision."""
    first = gate_cache_key.main(CARGO)
    before = toolchain.written()
    toolchain.stub("ldd", "echo 'ldd (Ubuntu GLIBC 2.43-2ubuntu2.8) 2.43'")
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert_moves_inputs_only(before, toolchain.written())


def test_no_toolchain_is_absent_not_red(toolchain: Cargo) -> None:
    """A runner without them builds nothing that needs them."""
    first = gate_cache_key.main(CARGO)
    present = toolchain.written()
    for name in ("cc", "c++", "clang", "ldd", "xcrun"):
        (Path(toolchain.path) / name).unlink()
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert present["inputs"] != toolchain.written()["inputs"]


def test_a_failing_ldd_is_absent(toolchain: Cargo) -> None:
    """An `ldd` that cannot report a glibc (musl) is not a finding."""
    toolchain.stub("ldd", "exit 1")
    first = gate_cache_key.main(CARGO)
    failing = toolchain.written()
    (Path(toolchain.path) / "ldd").unlink()
    second = gate_cache_key.main(CARGO)
    assert (first, second) == (0, 0)
    assert failing == toolchain.written()


def test_a_clang_that_cannot_report_is_red(
    toolchain: Cargo, capfd: pytest.CaptureFixture[str]
) -> None:
    """The key never names a compiler it could not identify."""
    toolchain.stub("clang", "echo broken >&2; exit 3")
    status = gate_cache_key.main(CARGO)
    _out, err = capfd.readouterr()
    assert status == 1
    assert not toolchain.out.read_text("utf-8")
    assert "clang --version exited 3" in err


# Every coverage directory names its compiler (#1877).
@fixture
def coverage_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as a coverage script runs `digest`: its task, no other variable."""
    for name in list(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("MISE_TASK_NAME", "coverage:check")


@pytest.mark.usefixtures("coverage_step")
def test_the_same_rustc_gives_the_same_sixteen_hex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """One `rustc -vV`, one directory: the cache key's digest, cut to 16."""
    monkeypatch.setenv("PATH", rustc_on_path(tmp_path, f"printf '%s' '{RUSTC}'"))
    first = gate_cache_key.main(["digest"])
    once = capfd.readouterr()
    second = gate_cache_key.main(["digest"])
    again = capfd.readouterr()
    whole = hashlib.sha256(RUSTC.encode()).hexdigest()
    assert (first, second) == (0, 0)
    assert (once.out, once.err) == (f"{whole[:16]}\n", "")
    assert once == again


@pytest.mark.usefixtures("coverage_step")
def test_a_different_release_gives_a_different_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """1.97.1 and 1.100.0 differ only in `release:`: two directories."""
    bumped = RUSTC.replace("release: 1.97.1", "release: 1.100.0")
    monkeypatch.setenv("PATH", rustc_on_path(tmp_path, f"printf '%s' '{RUSTC}'"))
    first = gate_cache_key.main(["digest"])
    old = capfd.readouterr().out
    (tmp_path / "b").mkdir()
    monkeypatch.setenv("PATH", rustc_on_path(tmp_path / "b", f"printf '%s' '{bumped}'"))
    second = gate_cache_key.main(["digest"])
    new = capfd.readouterr().out
    assert (first, second) == (0, 0)
    assert re.search(r"^[0-9a-f]{16}\n$", new)
    assert old != new


@pytest.mark.usefixtures("coverage_step")
def test_directory_no_rustc_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """No compiler: red by name, and no directory printed."""
    monkeypatch.setenv("PATH", str(tmp_path))
    status = gate_cache_key.main(["digest"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert "coverage:check: no rustc on PATH" in err


@pytest.mark.usefixtures("coverage_step")
def test_directory_a_failing_rustc_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A rustup proxy with no toolchain: red by its message."""
    script = "echo 'toolchain nightly-2026-07-20 is not installed' >&2; exit 1"
    monkeypatch.setenv("PATH", rustc_on_path(tmp_path, script))
    status = gate_cache_key.main(["digest"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert "-vV exited 1: toolchain nightly-2026-07-20 is not installed" in err


@pytest.mark.usefixtures("coverage_step")
def test_no_release_line_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Output naming no compiler names no directory."""
    monkeypatch.setenv("PATH", rustc_on_path(tmp_path, "echo 'binary: rustc'"))
    status = gate_cache_key.main(["digest"])
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert "printed no release line" in err

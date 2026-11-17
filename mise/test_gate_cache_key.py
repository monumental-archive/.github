#!/usr/bin/env python3
"""The gate's tool cache keeps mise-action's name and adds the belt (#1406, #1414).

The fixture is not written by hand: the key below is the one mise-action
v5.2.7 logged restoring the canon's own gate on main, run 44151533572 at
d46ae3b (2026-11-17), and the hash is that commit's mise/config.toml then
mise.toml, digested as @actions/glob does — reproduced before this was
written. If these rows pass and the first run after #1406 still misses,
the reproduction is wrong, not the fixture. #1414 appends the belt
lockfile's digest to that key; the rows hold both halves.
"""

import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "gate_cache_key", Path(__file__).with_name("gate-cache-key.py")
)
gate_cache_key = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate_cache_key)

LOGGED = (
    "mise-v2-linux-x64-ubuntu26-2026.11.6-p692-"
    "603294384eb88f9566d4631a33547c8f7b916d5edf000a69b2836d3af20d0ca8"
)
HASH = LOGGED.rsplit("-", 1)[1]
# 2026-11-17T18:47:13Z, when the run restored it.
WHEN = 1794941233.0
BELT = "b" * 64
RUNNER = {"ImageOS": "ubuntu26", "HOME": "/home/runner", "ORG_MISE_CONFIG_HASH": HASH}


class Key(unittest.TestCase):
    """The key is the action's, byte for byte."""

    def test_the_logged_key(self) -> None:
        """Run 44151533572's inputs render the key it restored, then the belt."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        self.assertEqual(f"{LOGGED}-b{BELT}", out["key"])

    def test_the_path_is_the_absolute_data_dir(self) -> None:
        """The path list is part of the cache version, so it must match."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        self.assertEqual("/home/runner/.local/share/mise", out["dir"])

    def test_the_data_dir_follows_mise(self) -> None:
        """MISE_DATA_DIR wins, then XDG_DATA_HOME, as the action resolves it."""
        env = {**RUNNER, "XDG_DATA_HOME": "/x"}
        self.assertEqual("/x/mise", gate_cache_key.data_dir(env))
        self.assertEqual("/m", gate_cache_key.data_dir({**env, "MISE_DATA_DIR": "/m"}))

    def test_the_fallback_stops_before_the_version(self) -> None:
        """A mise bump still restores the previous tools."""
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.12.0", "linux-x64", WHEN, BELT),
        )
        self.assertEqual("mise-v2-linux-x64-ubuntu26-", out["prefix"])
        self.assertTrue(LOGGED.startswith(out["prefix"]))

    def test_a_new_name_shares_no_prefix(self) -> None:
        """The hand-moved name (#1165) still invalidates everything."""
        out = gate_cache_key.names(
            "mise-v3",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT),
        )
        self.assertFalse(LOGGED.startswith(out["prefix"]))

    def test_the_period_rolls_every_thirty_days(self) -> None:
        """The action's cache_max_age_days default is in the key."""
        later = WHEN + gate_cache_key.PERIOD_SECONDS
        out = gate_cache_key.names(
            "mise-v2",
            RUNNER,
            gate_cache_key.Facts("2026.11.6", "linux-x64", later, BELT),
        )
        self.assertIn("-p693-", out["key"])

    def test_no_config_and_no_image(self) -> None:
        """An empty hash and a missing ImageOS render as the action does."""
        env = {"HOME": "/h"}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        self.assertEqual(
            f"mise-v2-linux-x64-self-hosted-2026.11.6-p692-no-config-b{BELT}",
            out["key"],
        )

    def test_mise_env_joins_with_dashes(self) -> None:
        """MISE_ENV's commas become dashes, after the version."""
        env = {**RUNNER, "MISE_ENV": "ci,linux"}
        out = gate_cache_key.names(
            "mise-v2", env, gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        )
        self.assertIn("-2026.11.6-ci-linux-p692-", out["key"])


class Inputs(unittest.TestCase):
    """The version and target are read, never assumed."""

    def test_one_version(self) -> None:
        """Every pin agrees: that is the version."""
        text = "        with:\n          version: 2026.11.6\n" * 3
        self.assertEqual("2026.11.6", gate_cache_key.mise_version(text))

    def test_disagreeing_pins_are_red(self) -> None:
        """Two versions would name a cache the install does not make."""
        text = "          version: 2026.11.6\n          version: 2026.11.5\n"
        with self.assertRaisesRegex(ValueError, r"found \['2026.11.5', '2026.11.6'\]"):
            gate_cache_key.mise_version(text)

    def test_no_pin_is_red(self) -> None:
        """No pin names nothing."""
        with self.assertRaisesRegex(ValueError, "found none"):
            gate_cache_key.mise_version("jobs: {}\n")

    def test_the_gate_pins_one_version(self) -> None:
        """The real ci.yml reads clean."""
        ci = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        self.assertRegex(
            gate_cache_key.mise_version(ci.read_text("utf-8")), r"^\d{4}\."
        )

    def test_targets(self) -> None:
        """Node's names for the runner's machines."""
        self.assertEqual("linux-x64", gate_cache_key.target("Linux", "x86_64"))
        self.assertEqual("macos-arm64", gate_cache_key.target("Darwin", "arm64"))
        with self.assertRaisesRegex(ValueError, "no mise-action target"):
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
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(gate_cache_key.platform, "system", return_value="Linux"),
            mock.patch.object(
                gate_cache_key.platform, "machine", return_value="x86_64"
            ),
            redirect_stdout(io.StringIO()),
        ):
            code = gate_cache_key.main(["mise-v2", str(canon)], now=WHEN)
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


class Main(unittest.TestCase):
    """The workflow reads three outputs."""

    def test_writes_the_outputs(self) -> None:
        """key, prefix and dir land in GITHUB_OUTPUT."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            lock = (tree / "mise" / "mise.lock").read_bytes()
            got = run(tree)
        belt = hashlib.sha256(lock).hexdigest()
        self.assertEqual(f"{LOGGED}-b{belt}", got["key"])
        self.assertEqual("mise-v2-linux-x64-ubuntu26-", got["prefix"])
        self.assertEqual("/home/runner/.local/share/mise", got["dir"])

    def test_a_missing_tree_is_red(self) -> None:
        """A canon tree without its files is a red step, not a key."""
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(1, gate_cache_key.main(["mise-v2", "/nonexistent"]))
        self.assertIn("gate-cache-key:", err.getvalue())

    def test_a_tree_without_a_belt_lockfile_is_red(self) -> None:
        """No lockfile, no key: a key that cannot name the belt is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            (tree / "mise" / "mise.lock").unlink()
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(1, gate_cache_key.main(["mise-v2", str(tree)]))
        self.assertIn("mise.lock", err.getvalue())


class Belt(unittest.TestCase):
    """The key moves with the belt's tools and not with its tasks (#1414)."""

    def test_a_belt_tool_bump_names_a_new_cache(self) -> None:
        """Two lockfiles differing in one tool's version give two keys."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = run(canon(Path(a), ruff="0.17.4"))
            new = run(canon(Path(b), ruff="0.17.5"))
        self.assertNotEqual(old["key"], new["key"])
        self.assertEqual(old["prefix"], new["prefix"])

    def test_a_task_body_change_keeps_the_cache(self) -> None:
        """Two config.tomls differing only in a task body give one key."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = run(canon(Path(a), body="ruff check"))
            new = run(canon(Path(b), body="ruff check --preview"))
        self.assertEqual(old["key"], new["key"])

    def test_the_real_belt_lockfile_is_read(self) -> None:
        """The tree's own mise/mise.lock is what the key digests."""
        tree = Path(__file__).resolve().parent.parent
        got = run(tree)
        lock = (tree / "mise" / "mise.lock").read_bytes()
        self.assertTrue(got["key"].endswith("-b" + hashlib.sha256(lock).hexdigest()))


LEG = "jq rust@1.97.1 cargo:cargo-pgrx@0.19.3 aqua:theseus-rs/postgresql-binaries@"


def leg(major: int, install: str) -> dict[str, str]:
    """Name a pgrx leg's cache, at the logged run's inputs (#1408).

    Returns:
        The names the workflow reads.

    """
    facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
    return gate_cache_key.names(f"mise-pgrx-v1-pg{major}", RUNNER, facts, install)


class InstallList(unittest.TestCase):
    """A job that installs a list keys its cache by the list (#1408)."""

    def test_two_legs_have_two_prefixes(self) -> None:
        """By name: a pg14 leg never falls back to a pg18 leg's cache."""
        pg14, pg18 = leg(14, LEG + "14.24.1"), leg(18, LEG + "18.6.1")
        self.assertEqual("mise-pgrx-v1-pg14-linux-x64-ubuntu26-", pg14["prefix"])
        self.assertEqual("mise-pgrx-v1-pg18-linux-x64-ubuntu26-", pg18["prefix"])
        self.assertFalse(pg18["key"].startswith(pg14["prefix"]))
        self.assertFalse(pg14["key"].startswith(pg18["prefix"]))

    def test_a_pin_bump_keeps_the_prefix_and_moves_the_key(self) -> None:
        """One leg, two lists: same fallback, different caches."""
        before = leg(18, LEG + "18.6.1")
        after = leg(18, LEG.replace("1.97.1", "1.98.0") + "18.6.1")
        self.assertEqual(before["prefix"], after["prefix"])
        self.assertNotEqual(before["key"], after["key"])
        self.assertTrue(after["key"].startswith(before["prefix"]))

    def test_the_digest_sits_before_the_belt(self) -> None:
        """The list's digest is in the key, after the config hash."""
        digest = gate_cache_key.install_digest(LEG + "18.6.1")
        self.assertTrue(
            leg(18, LEG + "18.6.1")["key"].endswith(
                f"-2026.11.6-p692-{HASH}-i{digest}-b{BELT}"
            )
        )

    def test_the_digest_is_mise_actions(self) -> None:
        """Order and flags do not change it: sorted tools, flags dropped."""
        self.assertEqual(
            gate_cache_key.install_digest("b a"),
            gate_cache_key.install_digest("--locked a  b"),
        )
        self.assertEqual(
            "c8687a08aa5d6ed2044328fa6a697ab8e96dc34291e8c2034ae8c38e6fcc6d65",
            gate_cache_key.install_digest("a b"),
        )

    def test_no_list_is_todays_key(self) -> None:
        """The gate passes no list, and its key does not move by a byte."""
        facts = gate_cache_key.Facts("2026.11.6", "linux-x64", WHEN, BELT)
        for install in ("", "  ", "--locked"):
            with self.subTest(install=install):
                out = gate_cache_key.names("mise-v2", RUNNER, facts, install)
                self.assertEqual(f"{LOGGED}-b{BELT}", out["key"])
                self.assertEqual("mise-v2-linux-x64-ubuntu26-", out["prefix"])

    def test_main_reads_the_list(self) -> None:
        """`--install` reaches the key the workflow reads."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = canon(Path(tmp))
            out = Path(tmp) / "out"
            env = {**RUNNER, "GITHUB_OUTPUT": str(out)}
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(
                    gate_cache_key.platform, "system", return_value="Linux"
                ),
                mock.patch.object(
                    gate_cache_key.platform, "machine", return_value="x86_64"
                ),
                redirect_stdout(io.StringIO()),
            ):
                argv = ["mise-pgrx-v1-pg18", str(tree), "--install", "jq rust@1"]
                self.assertEqual(0, gate_cache_key.main(argv, now=WHEN))
            written = out.read_text("utf-8")
        digest = gate_cache_key.install_digest("jq rust@1")
        self.assertIn(f"-i{digest}-b", written)
        self.assertIn("prefix=mise-pgrx-v1-pg18-linux-x64-ubuntu26-\n", written)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The gate's tool cache keeps the name mise-action gave it (#1406).

The fixture is not written by hand: the key below is the one mise-action
v5.2.7 logged restoring the canon's own gate on main, run 44151533572 at
d46ae3b (2026-11-17), and the hash is that commit's mise/config.toml then
mise.toml, digested as @actions/glob does — reproduced before this was
written. If these rows pass and the first run after #1406 still misses,
the reproduction is wrong, not the fixture.
"""

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
RUNNER = {"ImageOS": "ubuntu26", "HOME": "/home/runner", "ORG_MISE_CONFIG_HASH": HASH}


class Key(unittest.TestCase):
    """The key is the action's, byte for byte."""

    def test_the_logged_key(self) -> None:
        """The inputs of run 44151533572 render the key it restored."""
        out = gate_cache_key.names("mise-v2", "2026.11.6", "linux-x64", RUNNER, WHEN)
        self.assertEqual(LOGGED, out["key"])

    def test_the_path_is_the_absolute_data_dir(self) -> None:
        """The path list is part of the cache version, so it must match."""
        out = gate_cache_key.names("mise-v2", "2026.11.6", "linux-x64", RUNNER, WHEN)
        self.assertEqual("/home/runner/.local/share/mise", out["dir"])

    def test_the_data_dir_follows_mise(self) -> None:
        """MISE_DATA_DIR wins, then XDG_DATA_HOME, as the action resolves it."""
        env = {**RUNNER, "XDG_DATA_HOME": "/x"}
        self.assertEqual("/x/mise", gate_cache_key.data_dir(env))
        self.assertEqual("/m", gate_cache_key.data_dir({**env, "MISE_DATA_DIR": "/m"}))

    def test_the_fallback_stops_before_the_version(self) -> None:
        """A mise bump still restores the previous tools."""
        out = gate_cache_key.names("mise-v2", "2026.12.0", "linux-x64", RUNNER, WHEN)
        self.assertEqual("mise-v2-linux-x64-ubuntu26-", out["prefix"])
        self.assertTrue(LOGGED.startswith(out["prefix"]))

    def test_a_new_name_shares_no_prefix(self) -> None:
        """The hand-moved name (#1165) still invalidates everything."""
        out = gate_cache_key.names("mise-v3", "2026.11.6", "linux-x64", RUNNER, WHEN)
        self.assertFalse(LOGGED.startswith(out["prefix"]))

    def test_the_period_rolls_every_thirty_days(self) -> None:
        """The action's cache_max_age_days default is in the key."""
        later = WHEN + gate_cache_key.PERIOD_SECONDS
        out = gate_cache_key.names("mise-v2", "2026.11.6", "linux-x64", RUNNER, later)
        self.assertIn("-p693-", out["key"])

    def test_no_config_and_no_image(self) -> None:
        """An empty hash and a missing ImageOS render as the action does."""
        env = {"HOME": "/h"}
        out = gate_cache_key.names("mise-v2", "2026.11.6", "linux-x64", env, WHEN)
        self.assertEqual(
            "mise-v2-linux-x64-self-hosted-2026.11.6-p692-no-config", out["key"]
        )

    def test_mise_env_joins_with_dashes(self) -> None:
        """MISE_ENV's commas become dashes, after the version."""
        env = {**RUNNER, "MISE_ENV": "ci,linux"}
        out = gate_cache_key.names("mise-v2", "2026.11.6", "linux-x64", env, WHEN)
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


class Main(unittest.TestCase):
    """The workflow reads three outputs."""

    def test_writes_the_outputs(self) -> None:
        """key, prefix and dir land in GITHUB_OUTPUT."""
        with tempfile.TemporaryDirectory() as tmp:
            wf = Path(tmp) / "ci.yml"
            wf.write_text("          version: 2026.11.6\n", "utf-8")
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
                self.assertEqual(0, gate_cache_key.main(["mise-v2", str(wf)], now=WHEN))
            written = out.read_text("utf-8")
        self.assertIn(f"key={LOGGED}\n", written)
        self.assertIn("prefix=mise-v2-linux-x64-ubuntu26-\n", written)
        self.assertIn("dir=/home/runner/.local/share/mise\n", written)

    def test_a_bad_workflow_is_red(self) -> None:
        """A missing file is a red step with the reason, not a key."""
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(1, gate_cache_key.main(["mise-v2", "/nonexistent/ci.yml"]))
        self.assertIn("gate-cache-key:", err.getvalue())


if __name__ == "__main__":
    unittest.main()

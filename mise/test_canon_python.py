#!/usr/bin/env python3
"""The belt's Python as ORG_PYTHON: its install, and its refusal (#1543).

`.github/actions/canon/python.sh` is the canon action's last step: it
installs the belt's pinned, locked Python and exports its path as
ORG_PYTHON, the interpreter every helper a workflow starts runs under.
Each row plants a canon tree, copies the shipped script into the tree's
`.github/actions/canon/`, and runs it under bash with a stub `mise` first
on PATH that records how it was called.

Then the other half of the contract: every tracked shell script that
starts a helper as `"${ORG_PYTHON}"` refuses an unset or empty one by
name, never falling back to the runner's python3.

stdlib `unittest`, matching the other tests here.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from typing import override

import hermetic

CANON = pathlib.Path(__file__).resolve().parent.parent
VERSION = "3.15.2"
# No apostrophe in it: bash removes one inside `${VAR:?...}`, even
# within double quotes (measured: "belt's" printed as "belts").
REFUSAL = (
    "ORG_PYTHON: unset — the belt Python (CI: the canon action; local: the belt [env])"
)

# The stub: logs each call with the environment the script gave it, then
# answers `install` with STUB_INSTALL's status and `where` with STUB_WHERE.
STUB = """#!/usr/bin/env bash
{
  echo "args: $*"
  echo "cwd: $(pwd)"
  echo "MISE_LOCKED=${MISE_LOCKED:-}"
  echo "MISE_GLOBAL_CONFIG_FILE=${MISE_GLOBAL_CONFIG_FILE:-}"
  echo "MISE_TRUSTED_CONFIG_PATHS=${MISE_TRUSTED_CONFIG_PATHS:-}"
  echo "MISE_DATA_DIR=${MISE_DATA_DIR:-}"
} >> "${STUB_LOG}"
case "$1" in
  install) exit "${STUB_INSTALL:-0}" ;;
  where) echo "${STUB_WHERE}" ;;
  *) exit 2 ;;
esac
"""


def bash() -> str:
    """Find bash.

    Returns:
        Its path; a missing bash is red by name (hermetic.tool).

    """
    return hermetic.tool("bash")


class InstallTheBeltsPython(unittest.TestCase):
    """Each row: the planted tree and stub, and what the step did."""

    @override
    def setUp(self) -> None:
        """Give each row a canon tree, a stub mise and a runner's files."""
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "_actions" / "example-org" / ".github" / "v1.0.0"
        # The belt config and lock are the row's own: plant() rewrites them.
        hermetic.canon(
            self.root,
            ".github/actions/canon",
            stubs={"mise/config.toml": "", "mise/mise.lock": ""},
        )
        self.script = self.root / ".github" / "actions" / "canon" / "python.sh"
        self.plant(config=VERSION, lock=VERSION)
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "mise").write_text(STUB, encoding="utf-8")
        (stubs / "mise").chmod(0o755)
        self.path = f"{stubs}{os.pathsep}{os.environ['PATH']}"
        self.log = self.tmp / "mise.log"
        self.env_file = self.tmp / "github-env"
        self.env_file.write_text("", encoding="utf-8")
        self.runner_temp = self.tmp / "runner-temp"
        self.runner_temp.mkdir()
        self.where = self.tmp / "installs" / "python" / VERSION
        (self.where / "bin").mkdir(parents=True)
        self.python = self.where / "bin" / "python3"
        self.python.write_text("#!/bin/sh\n", encoding="utf-8")
        self.python.chmod(0o755)

    def plant(self, *, config: str | None, lock: str) -> None:
        """Write the tree's belt config and lock, pinning the given versions."""
        pin = f'python = "{config}"\n' if config is not None else ""
        (self.root / "mise" / "config.toml").write_text(
            f'[tools]\n"aqua:astral-sh/uv" = "0.13.7"\n{pin}', encoding="utf-8"
        )
        (self.root / "mise" / "mise.lock").write_text(
            '[[tools."aqua:astral-sh/uv"]]\nversion = "0.13.7"\n\n'
            f'[[tools.python]]\nversion = "{lock}"\nbackend = "core:python"\n',
            encoding="utf-8",
        )

    def install(self, *, status: int = 0) -> subprocess.CompletedProcess[str]:
        """Run the planted script as the action does.

        Returns:
            The run.

        """
        return subprocess.run(
            [bash(), str(self.script)],
            env=hermetic.env(
                PATH=self.path,
                HOME=str(self.tmp),
                GITHUB_ENV=str(self.env_file),
                RUNNER_TEMP=str(self.runner_temp),
                STUB_LOG=str(self.log),
                STUB_INSTALL=str(status),
                STUB_WHERE=str(self.where),
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def calls(self) -> str:
        """Read the stub's log.

        Returns:
            Every call, or "" when mise was never run.

        """
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_the_interpreters_path_is_exported(self) -> None:
        """Locked, from the belt's config, out of the caller's tree."""
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"the belt's python {VERSION}: {self.python}\n")
        self.assertEqual(
            self.env_file.read_text(encoding="utf-8"), f"ORG_PYTHON={self.python}\n"
        )
        work = self.runner_temp / "org-python"
        call = (
            f"cwd: {work / 'cwd'}\n"
            "MISE_LOCKED=1\n"
            f"MISE_GLOBAL_CONFIG_FILE={self.root / 'mise' / 'config.toml'}\n"
            f"MISE_TRUSTED_CONFIG_PATHS={self.root}\n"
            f"MISE_DATA_DIR={work / 'data'}\n"
        )
        self.assertEqual(
            self.calls(),
            f"args: install python\n{call}args: where python@{VERSION}\n{call}",
        )

    def test_a_failed_install_is_red_by_name(self) -> None:
        """Nothing is exported, and the message names the version and lock."""
        done = self.install(status=1)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"::error::python.sh: mise could not install python {VERSION}"
            f" from {self.root / 'mise' / 'mise.lock'}\n",
        )
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), "")

    def test_a_lock_without_the_version_is_red_by_name(self) -> None:
        """The config pins a version the lock does not hold: mise never runs."""
        self.plant(config=VERSION, lock="3.15.1")
        done = self.install()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"::error::python.sh: {self.root / 'mise' / 'mise.lock'} does"
            f" not hold python {VERSION} (it holds '3.15.1'), so the install"
            " cannot be checked\n",
        )
        self.assertEqual(self.calls(), "")
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), "")

    def test_a_config_without_a_python_pin_is_red_by_name(self) -> None:
        """No pin: nothing to install, and nothing is guessed."""
        self.plant(config=None, lock=VERSION)
        done = self.install()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"::error::python.sh: {self.root / 'mise' / 'config.toml'}"
            " does not pin exactly one python version\n",
        )
        self.assertEqual(self.calls(), "")

    def test_an_install_with_no_interpreter_is_red_by_name(self) -> None:
        """The stub says where, and nothing executable is there."""
        self.python.unlink()
        done = self.install()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(
            done.stderr,
            f"::error::python.sh: {self.python} is not an executable interpreter\n",
        )
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), "")

    def test_the_shipped_pin_is_in_the_shipped_lock(self) -> None:
        """The real belt: the script's own two checks pass on it."""
        config = (CANON / "mise" / "config.toml").read_text(encoding="utf-8")
        lock = (CANON / "mise" / "mise.lock").read_text(encoding="utf-8")
        pins = [ln for ln in config.splitlines() if ln.startswith('python = "')]
        self.assertEqual(len(pins), 1, pins)
        version = pins[0].split('"')[1]
        self.assertIn(f'[[tools.python]]\nversion = "{version}"\n', lock)


def starters() -> list[pathlib.Path]:
    """Name every tracked shell script that starts a helper as ORG_PYTHON.

    Returns:
        Their paths, sorted.

    """
    paths = [CANON / p for p in hermetic.tracked(CANON, "*.sh")]
    return sorted(p for p in paths if starts_helpers(p.read_text("utf-8")))


def starts_helpers(text: str) -> bool:
    """Say whether a script's code, not its comments, names ORG_PYTHON.

    Returns:
        True when a line that is not a comment carries `"${ORG_PYTHON}"`.

    """
    return any(
        '"${ORG_PYTHON}"' in ln
        for ln in text.splitlines()
        if not ln.lstrip().startswith("#")
    )


def run_without(argv: list[str], value: str | None) -> subprocess.CompletedProcess[str]:
    """Run `argv` in an empty directory, ORG_PYTHON absent or `value`.

    Returns:
        The run.

    """
    with tempfile.TemporaryDirectory() as tmp:
        env = hermetic.env(tmp)
        # The row decides ORG_PYTHON: unset, or the value it names.
        env.pop("ORG_PYTHON", None)
        if value is not None:
            env["ORG_PYTHON"] = value
        return subprocess.run(
            argv,
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


# Sourced, never run: run as a script each only defines its functions and
# exits 0, so each proves its refusal by calling them after sourcing.
SOURCED = {"pgrx-env.sh", "rust-coverage-env.sh"}


class AnUnsetOrgPythonIsRefused(unittest.TestCase):
    """A script that starts a helper names ORG_PYTHON when it is missing."""

    def test_the_scripts_are_found(self) -> None:
        """Not vacuous: the release and belt scripts that start helpers."""
        names = {p.name for p in starters()}
        self.assertLessEqual(
            {"derive-coverage-floor.sh", "pgrx-image-proof.sh", "pgrx-plan.sh"},
            names,
        )
        self.assertGreaterEqual(len(names), 14)

    def test_every_script_refuses_an_unset_or_empty_org_python(self) -> None:
        """Run each one: red, with ORG_PYTHON named, before anything else."""
        for script in starters():
            if script.name in SOURCED:
                continue
            for value in (None, ""):
                with self.subTest(script=script.name, value=value):
                    done = run_without([bash(), str(script)], value)
                    self.assertNotEqual(done.returncode, 0, done.stdout)
                    self.assertIn(REFUSAL, done.stderr)
                    self.assertEqual(done.stdout, "")

    def test_the_sourced_provisioner_refuses_an_unset_org_python(self) -> None:
        """pgrx-env.sh is sourced: its helper call refuses at the call."""
        env_sh = CANON / "mise" / "pgrx-env.sh"
        # A major's Postgres is found through pgrx_postgres.py (#1940).
        postgres = f'. "{env_sh}"; org_pgrx_postgres 17'
        for value in (None, ""):
            with self.subTest(value=value):
                done = run_without([bash(), "-c", postgres], value)
                self.assertNotEqual(done.returncode, 0, done.stdout)
                self.assertIn(REFUSAL, done.stderr)

    def test_the_sourced_coverage_env_refuses_an_unset_org_python(self) -> None:
        """rust-coverage-env.sh is sourced: its function refuses (#1877)."""
        env_sh = CANON / "mise" / "rust-coverage-env.sh"
        call = f'. "{env_sh}"; org_rust_coverage_env measured'
        for value in (None, ""):
            with self.subTest(value=value):
                done = run_without([bash(), "-c", call], value)
                self.assertNotEqual(done.returncode, 0, done.stdout)
                self.assertIn(REFUSAL, done.stderr)


if __name__ == "__main__":
    unittest.main()

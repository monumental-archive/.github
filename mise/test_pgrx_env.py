#!/usr/bin/env python3
"""Tests for pgrx-env.sh's `org_pgrx_init`, against a stand-in cargo (#1360).

`org_pgrx_init` registers each declared Postgres with `cargo pgrx init`.
One of its fixes is logic around that call rather than anything a live
Postgres has to show, so a stand-in `cargo` is enough to hold it:
`cargo pgrx init` rewrites `config.toml` on every call, and the new
timestamp made cargo rebuild pgrx-pg-sys on an exact cache hit (#1173).
An init that changed no registration keeps the old timestamp.

The function under test is the belt's own, sourced by bash exactly as a
task sources it. stdlib `unittest`, matching the other mise/test_*.py;
run through the gate as `mise run test`.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

ENV_SH = Path(__file__).resolve().with_name("pgrx-env.sh")
BASH = shutil.which("bash") or "bash"

# `cargo pgrx init` as far as these rows need it: it writes config.toml
# from $NEW_CONFIG, the way pgrx rewrites it on every call.
CARGO_STUB = r"""#!/usr/bin/env bash
[[ $1 == pgrx && $2 == init ]] || exit 1
mkdir -p "${PGRX_HOME}"
if [[ -n ${NEW_CONFIG:-} ]]; then
  printf '%s' "${NEW_CONFIG}" > "${PGRX_HOME}/config.toml"
fi
"""

OLD = 946684800  # 2000-01-01, far from any timestamp a row writes


class InitCase(unittest.TestCase):
    """A row with a stub cargo and its own PGRX_HOME."""

    def setUp(self) -> None:
        """Lay out the stand-ins and an empty PGRX_HOME location."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bindir = root / "bin"
        bindir.mkdir()
        for name, body in (
            ("cargo", CARGO_STUB),
            ("cargo-pgrx", "#!/usr/bin/env bash\nexit 0\n"),
        ):
            (bindir / name).write_text(body, encoding="utf-8")
            (bindir / name).chmod(0o755)
        self.home = root / "pgrx"
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env = env | {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "PGRX_HOME": str(self.home),
        }

    def init(self, **env: str) -> subprocess.Popen[str]:
        """Start one task's `org_pgrx_init` for Postgres 18.

        Returns:
            The running bash.

        """
        script = (
            f'. "{ENV_SH}"\n'
            "printf 'pg\\t18\\t/opt/pg18/bin/pg_config\\n' | org_pgrx_init\n"
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.Popen(
            [BASH, "-euo", "pipefail", "-c", script],
            env=self.env | env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


class UnchangedConfigKeepsItsTime(InitCase):
    """An init that registered nothing new does not touch the timestamp (#1173)."""

    def plant(self, text: str) -> Path:
        """Write an existing config.toml with an old timestamp.

        Returns:
            The file's path.

        """
        self.home.mkdir()
        config = self.home / "config.toml"
        config.write_text(text, encoding="utf-8")
        os.utime(config, (OLD, OLD))
        return config

    def run_init(self, new: str) -> None:
        """Run one init whose cargo writes `new` and wait for it."""
        task = self.init(NEW_CONFIG=new)
        code = task.wait(timeout=60)
        err = task.stderr.read()
        task.stdout.close()
        task.stderr.close()
        self.assertEqual(0, code, err)

    def test_same_registrations_in_another_order_keep_the_old_time(self) -> None:
        """The majors come in hash order; the set is what counts."""
        config = self.plant('[configs]\npg17 = "/a"\npg18 = "/b"\n')
        self.run_init('[configs]\npg18 = "/b"\npg17 = "/a"\n')
        self.assertEqual(OLD, int(config.stat().st_mtime))

    def test_a_changed_registration_takes_the_new_time(self) -> None:
        """A moved pg_config is a real change and must rebuild."""
        config = self.plant('[configs]\npg18 = "/b"\n')
        self.run_init('[configs]\npg18 = "/c"\n')
        self.assertNotEqual(OLD, int(config.stat().st_mtime))


if __name__ == "__main__":
    unittest.main()

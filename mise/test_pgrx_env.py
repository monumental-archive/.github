#!/usr/bin/env python3
"""Tests for pgrx-env.sh's `org_pgrx_init`, against a stand-in cargo (#1360).

`org_pgrx_init` registers each declared Postgres with `cargo pgrx init`.
Its fixes here are logic around that call rather than anything a live
Postgres has to show, so a stand-in `cargo` that records what happened
is enough to hold them:

- #1140, #1481: tasks the gate runs in parallel each initialised at
  once, and two inits beside each other both call initdb. One at a
  time, under a lock the kernel holds, which a killed holder cannot
  strand.
- #1173: `cargo pgrx init` rewrites `config.toml` on every call, and the
  new timestamp made cargo rebuild pgrx-pg-sys on an exact cache hit.
  An init that changed no registration keeps the old timestamp.

The function under test is the belt's own, sourced by bash exactly as a
task sources it. stdlib `unittest`, matching the other mise/test_*.py;
run through the gate as `mise run test`.
"""

import os
import shutil
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import time
import unittest
from pathlib import Path

ENV_SH = Path(__file__).resolve().with_name("pgrx-env.sh")
BASH = shutil.which("bash") or "bash"

# `cargo pgrx init` as far as these rows need it: it logs its start and
# end around $STUB_HOLD seconds of work (two by default), so two inits
# that ran together log start, start, end, end, and it writes
# config.toml from $NEW_CONFIG, the way pgrx rewrites it on every call.
CARGO_STUB = r"""#!/usr/bin/env bash
[[ $1 == pgrx && $2 == init ]] || exit 1
echo start >> "${PGRX_HOME}.log"
sleep "${STUB_HOLD:-2}"
mkdir -p "${PGRX_HOME}"
if [[ -n ${NEW_CONFIG:-} ]]; then
  printf '%s' "${NEW_CONFIG}" > "${PGRX_HOME}/config.toml"
fi
echo end >> "${PGRX_HOME}.log"
"""

OLD = 946684800  # 2000-01-01, far from any timestamp a row writes


class InitCase(unittest.TestCase):
    """A row with a stub cargo and its own PGRX_HOME."""

    def setUp(self) -> None:
        """Lay out the stand-ins and an empty PGRX_HOME location."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.root = root
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

    def init(self, *, group: bool = False, **env: str) -> subprocess.Popen[str]:
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
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=group,
        )

    @staticmethod
    def finish(task: subprocess.Popen[str]) -> tuple[int, str]:
        """Wait for one init and close its pipes.

        Returns:
            (exit status, stderr).

        """
        code = task.wait(timeout=120)
        err = task.stderr.read() if task.stderr else ""
        for pipe in (task.stdout, task.stderr):
            if pipe:
                pipe.close()
        return code, err

    def log(self) -> list[str]:
        """Read what the stand-in cargo recorded.

        Returns:
            One line per event, in order.

        """
        path = Path(f"{self.home}.log")
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class OneInitAtATime(InitCase):
    """Parallel tasks initialise in turn, never together (#1140, #1481)."""

    def test_two_tasks_never_init_at_once(self) -> None:
        """The gate starts several pgrx tasks together; their inits queue.

        With #1140's `mkdir` lock this logged start, start, end, end on
        a Linux runner (#1481): the waiters judged the holder by pid.
        """
        tasks = [self.init(), self.init()]
        results = [self.finish(t) for t in tasks]
        self.assertEqual([0, 0], [code for code, _ in results], results)
        self.assertEqual(["start", "end", "start", "end"], self.log(), results)

    def test_a_killed_holder_does_not_strand_the_next_init(self) -> None:
        """A holder killed mid-init takes the lock with it.

        The holder is killed mid-init. The next init proceeds at once
        rather than waiting out the ten-minute bound.
        """
        holder = self.init(group=True, STUB_HOLD="60")
        deadline = time.monotonic() + 30
        while self.log() != ["start"]:
            self.assertLess(time.monotonic(), deadline, "the holder never started")
            time.sleep(0.1)
        os.killpg(holder.pid, signal.SIGKILL)
        self.finish(holder)
        started = time.monotonic()
        code, err = self.finish(self.init(STUB_HOLD="0"))
        self.assertEqual(0, code, err)
        self.assertLess(time.monotonic() - started, 20, err)
        self.assertEqual(["start", "start", "end"], self.log())


class LoopMeasure(unittest.TestCase):
    """#1481 Done-when: fifty pairs with the new lock. MEASUREMENT ONLY."""

    def test_fifty_pairs(self) -> None:
        """Run fifty pairs and report every overlap."""
        overlaps = []
        for n in range(50):
            case = OneInitAtATime("test_two_tasks_never_init_at_once")
            case.setUp()
            tasks = [case.init(STUB_HOLD="1"), case.init(STUB_HOLD="1")]
            results = [case.finish(t) for t in tasks]
            log = case.log()
            if log != ["start", "end", "start", "end"]:
                overlaps.append(f"pair {n}: log {log} {results}")
            case.doCleanups()
        print(f"\nLOOP-MEASURE: {len(overlaps)} overlap(s) in 50 pairs", flush=True)
        for o in overlaps:
            print("LOOP-MEASURE " + o, flush=True)
        self.assertEqual([], overlaps)
        self.fail(
            "LOOP-MEASURE: 0 overlaps in 50 pairs (red on purpose, to show the log)"
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

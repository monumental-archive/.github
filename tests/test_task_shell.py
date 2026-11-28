"""Every task fails on error, by default (#401, #1745).

The belt's tasks ran under `bash -c`, so a body whose middle command
failed went on to its last line and reported that line's status: a
check could fail and the task still pass. #401 pinned the task shell to
`bash -euo pipefail -c` as `[task_config] shell` in mise/config.toml,
for the tasks in that file. A repository's own tasks run under the pin
in its own file, which `lint:belt-shell` reds it for leaving out; the
belt states no default for them (#1745).

Each row of the first table runs a body through the declared shell,
split as mise splits it: a failing command, a failing pipeline and an
unset name must each stop the body before its last line. The second
runs mise for real, in a planted repository that pins its own shell
and loads the belt through a clone's `conf.d` link.
"""

import shlex
import subprocess
import tempfile
from pathlib import Path

import pytest

from belt import decoded
from belt.typed_pytest import fixture
from tests import support

BELT = support.MISE
CONFIG = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
SHELL = decoded.as_str(
    decoded.as_object(CONFIG["task_config"], "task_config")["shell"],
    "task_config.shell",
)
# Each failure, then the line that must never run.
BODIES = {
    "a failing command": "false\necho reached\n",
    "a failing pipeline": "false | cat\necho reached\n",
    "an unset name": 'echo "${ORG_NEVER_SET}"\necho reached\n',
}
# What mise prints for a setting it refuses from a non-global file.
REFUSED = "ignored for security reasons"


# The declared shell stops a body at its first failure (#401).
@pytest.mark.parametrize(
    "body", [pytest.param(body, id=what) for what, body in BODIES.items()]
)
def test_the_declared_shell_stops_at_the_first_failure(
    body: str, env: support.Env
) -> None:
    """No body reaches its last line, and each run is red."""
    done = subprocess.run(
        [*shlex.split(SHELL), body],
        env=env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "reached" not in done.stdout
    assert done.returncode != 0


class ALinkedCloneThatPinsItsShell:
    """A real mise, the belt as a clone's link, no global config (#1745)."""

    def __init__(self, tmp_path: Path) -> None:
        """Give the row's scratch directories one home, removed after it."""
        self.tmp = tmp_path


@fixture
def a_linked_clone_that_pins_its_shell(tmp_path: Path) -> ALinkedCloneThatPinsItsShell:
    """Give each row its own ALinkedCloneThatPinsItsShell.

    Returns:
        The row's ALinkedCloneThatPinsItsShell.

    """
    return ALinkedCloneThatPinsItsShell(tmp_path)


def test_the_pin_holds_and_the_belt_sets_nothing_refused(
    a_linked_clone_that_pins_its_shell: ALinkedCloneThatPinsItsShell,
    belt: support.Belt,
    env: support.Env,
) -> None:
    """The failing pipeline reds the task, and mise warns of nothing."""
    tmp = Path(tempfile.mkdtemp(dir=a_linked_clone_that_pins_its_shell.tmp)).resolve()
    repo = tmp / "repo"
    conf = repo / ".config" / "mise" / "conf.d"
    conf.mkdir(parents=True)
    belt_dir = belt(tmp / "belt")
    (conf / "belt.toml").symlink_to(belt_dir / "config.toml")
    (repo / "mise.toml").write_text(
        f'[task_config]\nshell = "{SHELL}"\n\n'
        '[tasks.t]\nrun = "false | cat; echo reached"\n',
        encoding="utf-8",
    )
    (tmp / "config").mkdir()
    subprocess.run(
        [support.tool("git"), "init", "-q", "."],
        cwd=repo,
        env=env(),
        check=True,
    )
    environ = env(
        MISE_CONFIG_DIR=str(tmp / "config"),
        XDG_CONFIG_HOME=str(tmp / "config"),
        MISE_CEILING_PATHS=str(tmp),
        MISE_TRUSTED_CONFIG_PATHS=str(tmp),
        MISE_OFFLINE="1",
        MISE_YES="1",
        MISE_AUTO_INSTALL="0",
        MISE_TASK_RUN_AUTO_INSTALL="0",
    )
    done = subprocess.run(
        [support.tool("mise"), "run", "t"],
        cwd=repo,
        env=environ,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "reached" not in done.stdout, out
    assert REFUSED not in out

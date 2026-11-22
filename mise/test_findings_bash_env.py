#!/usr/bin/env python3
"""The finding writer reaches a task body through BASH_ENV (#1934).

The belt's `[env]` names `mise/findings.sh` in BASH_ENV, and no task
body sources it. These rows run a body the way mise runs one, through
`hermetic.task_run`: the belt's `[task_config]` shell (`bash --norc
-euo pipefail -c`), MISE_TASK_NAME set, and BASH_ENV naming the belt's
writer. They run under the belt's bash and under `/bin/bash` (3.2 on
macOS).

A body calls `org_finding`, and a bash the body starts calls it too:
both print the belt's form and append a record. Without BASH_ENV the
same body has no writer, so the rows hold the variable, not some
other route. The belt's `[env]` names the file beside the belt.

bash skips BASH_ENV for a `-c` string whose stdin is a socket, or with
SSH_CLIENT set, at a shell level under 2: its rsh/sshd branch reads
~/.bashrc and returns first. The belt's `--norc` turns that branch off
(#1934), so under it the body keeps the writer where a plain `bash -c`
loses it.

A script run outside `mise run` states its task with `org_task`
(#1979), and a Python child it starts records under that name, not
its own file's: the export is the function's, so no site can forget it.

stdlib `unittest`, matching the rest of mise/.
"""

import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

MISE = Path(__file__).resolve().parent
WRITER = MISE / "findings.sh"
SYSTEM_BASH = Path("/bin/bash")
TASK = "lint:demo"

BODY = """org_finding "a/b.toml" "is wrong" "first detail"
bash -c 'org_finding "" "said by a child"'
"""

PRINTED = f"{TASK}: a/b.toml: is wrong\n  first detail\n{TASK}: said by a child\n"

NO_DETAIL: list[str] = []
EXPECTED: list[dict[str, object]] = [
    {
        "v": 1,
        "task": TASK,
        "subject": "a/b.toml",
        "message": "is wrong",
        "detail": ["first detail"],
    },
    {
        "v": 1,
        "task": TASK,
        "subject": "",
        "message": "said by a child",
        "detail": NO_DETAIL,
    },
]


PROBE = "type -t org_finding || echo none"

PLANTED = """. "$1"
org_task test:planted
"$2" "$3"
"""

CHILD = f"""import sys
sys.path.insert(0, {str(MISE)!r})
import findings
findings.finding("", "m")
"""


def config() -> dict[str, object]:
    """Read the belt's config.

    Returns:
        Its top-level tables.

    """
    return decoded.load_toml((MISE / "config.toml").read_bytes())


def bashes() -> list[tuple[str, str]]:
    """List the bashes the rows run under: the belt's, and /bin/bash.

    Returns:
        Each bash's label and path.

    """
    out = [("belt bash", str(hermetic.tool("bash")))]
    if SYSTEM_BASH.exists():
        out.append(("/bin/bash", str(SYSTEM_BASH)))
    return out


class BashEnvRows(unittest.TestCase):
    """A body under the belt's shell and BASH_ENV has the writer."""

    @override
    def setUp(self) -> None:
        """Give each row a directory of its own."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.file = self.root / "out" / "job.jsonl"

    def run_body(
        self, bash: str, *, writer: bool = True, **env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run BODY as mise runs a task body, under one bash.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run(TASK, BODY)
        given = dict(as_mise.env)
        if not writer:
            given.pop("BASH_ENV")
        return subprocess.run(
            [bash, *as_mise.argv[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(PYTHONUTF8="1", **env) | given,
            check=False,
            cwd=self.root,
        )

    def probe(self, argv: list[str], *, ssh: bool) -> str:
        """Ask a bash whether it has the writer, its stdin a socket or not.

        With `ssh`, stdin is a pipe and SSH_CLIENT is set; without, stdin
        is one end of a socket pair. Either way no SHLVL is set.

        Returns:
            `function` with the writer, `none` without.

        """
        env = hermetic.env() | {"BASH_ENV": str(WRITER)}
        if ssh:
            env["SSH_CLIENT"] = "192.0.2.1 50000 22"
            done = subprocess.run(
                argv, env=env, input="", capture_output=True, text=True, check=False
            )
        else:
            near, far = socket.socketpair()
            self.addCleanup(near.close)
            self.addCleanup(far.close)
            done = subprocess.run(
                argv,
                env=env,
                stdin=far.fileno(),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotIn("SHLVL", env)
        return done.stdout.strip()

    def test_socket_stdin_or_ssh_skips_bash_env_unless_norc(self) -> None:
        """A plain `bash -c` loses the writer there; the belt's shell keeps it."""
        shell = hermetic.task_run(TASK, PROBE).argv
        self.assertIn("--norc", shell)
        for label, bash in bashes():
            for ssh in (False, True):
                where = "SSH_CLIENT" if ssh else "socket stdin"
                with self.subTest(bash=label, where=where):
                    self.assertEqual("none", self.probe([bash, "-c", PROBE], ssh=ssh))
                    self.assertEqual(
                        "function", self.probe([bash, *shell[1:]], ssh=ssh)
                    )

    def test_a_body_and_its_child_bash_have_the_writer(self) -> None:
        """BASH_ENV gives the body and a bash it starts org_finding."""
        for label, bash in bashes():
            with self.subTest(bash=label):
                self.file.unlink(missing_ok=True)
                done = self.run_body(bash, ORG_FINDINGS=str(self.file))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stderr, PRINTED)
                records = [
                    decoded.as_object(decoded.load_json(line), "a record")
                    for line in self.file.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(records, EXPECTED)

    def test_without_bash_env_a_body_has_no_writer(self) -> None:
        """The same body with no BASH_ENV dies on the missing name."""
        for label, bash in bashes():
            with self.subTest(bash=label):
                done = self.run_body(bash, writer=False)
                self.assertEqual(done.returncode, 127)
                self.assertIn("org_finding: command not found", done.stderr)

    def test_the_belt_names_its_writer_in_bash_env(self) -> None:
        """`[env]` names findings.sh beside the belt, empty with no belt."""
        env = decoded.as_object(config()["env"], "env")
        self.assertEqual(
            env["BASH_ENV"],
            "{% if env.ORG_BELT_DIR %}{{ env.ORG_BELT_DIR }}/findings.sh{% endif %}",
        )
        self.assertTrue(WRITER.is_file())


class OrgTaskRows(unittest.TestCase):
    """`org_task` names a bare script's task for every child (#1979)."""

    def run_planted(self, **env: str) -> subprocess.CompletedProcess[str]:
        """Run a script that sources the writer, then starts a Python child.

        Returns:
            The finished process.

        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "planted.sh").write_text(PLANTED, encoding="utf-8")
            (root / "child.py").write_text(CHILD, encoding="utf-8")
            given = hermetic.env(PYTHONUTF8="1", ORG_FINDINGS="", **env)
            if "MISE_TASK_NAME" not in env:
                self.assertNotIn("MISE_TASK_NAME", given)
            return subprocess.run(
                [
                    str(hermetic.tool("bash")),
                    str(root / "planted.sh"),
                    str(WRITER),
                    sys.executable,
                    str(root / "child.py"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=given,
                check=False,
                cwd=root,
            )

    def test_a_bare_script_names_its_task_to_a_python_child(self) -> None:
        """With no MISE_TASK_NAME, the child prints the task, not child.py."""
        done = self.run_planted()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "test:planted: m\n")

    def test_a_name_mise_gave_wins(self) -> None:
        """Under mise run, mise's own name reaches the child unchanged."""
        done = self.run_planted(MISE_TASK_NAME="lint:demo")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "lint:demo: m\n")


if __name__ == "__main__":
    unittest.main()

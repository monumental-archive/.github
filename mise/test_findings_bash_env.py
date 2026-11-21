#!/usr/bin/env python3
"""The finding writer reaches a task body through BASH_ENV (#1934).

The belt's `[env]` names `mise/findings.sh` in BASH_ENV, and no task
body sources it. These rows run a body the way mise runs one: the
belt's `[task_config]` shell (`bash -euo pipefail -c`), MISE_TASK_NAME
set, and BASH_ENV naming the belt's writer. They run under the belt's
bash and under `/bin/bash` (3.2 on macOS).

A body calls `org_finding`, and a bash the body starts calls it too:
both print the belt's form and append a record. Without BASH_ENV the
same body has no writer, so the rows hold the variable, not some
other route. The belt's `[env]` names the file beside the belt.

stdlib `unittest`, matching the rest of mise/.
"""

import shlex
import subprocess
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


def config() -> dict[str, object]:
    """Read the belt's config.

    Returns:
        Its top-level tables.

    """
    return decoded.load_toml((MISE / "config.toml").read_bytes())


def task_shell() -> list[str]:
    """Read the belt's task shell, its interpreter first.

    Returns:
        The shell's words, as `[task_config] shell` writes them.

    """
    table = decoded.as_object(config()["task_config"], "task_config")
    return shlex.split(decoded.as_str(table["shell"], "task_config.shell"))


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

    def run_body(self, bash: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run BODY as mise runs a task body, under one bash.

        Returns:
            The finished process.

        """
        words = task_shell()
        self.assertEqual(words[0], "bash", "the belt's task shell is bash")
        return subprocess.run(
            [bash, *words[1:], BODY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=hermetic.env(MISE_TASK_NAME=TASK, PYTHONUTF8="1", **env),
            check=False,
            cwd=self.root,
        )

    def test_a_body_and_its_child_bash_have_the_writer(self) -> None:
        """BASH_ENV gives the body and a bash it starts org_finding."""
        for label, bash in bashes():
            with self.subTest(bash=label):
                self.file.unlink(missing_ok=True)
                done = self.run_body(
                    bash, BASH_ENV=str(WRITER), ORG_FINDINGS=str(self.file)
                )
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
                done = self.run_body(bash)
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


if __name__ == "__main__":
    unittest.main()

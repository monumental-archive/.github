#!/usr/bin/env python3
"""The published-bytes fetch in verify-release.yml, against a stub (#1204).

The bytes leg pulls each published file back from its registry, and a
registry answers 404 for a while after it accepted a publish. The
release-lab v0.28.2 run stopped on the fifth 404 of a 75-second budget,
after npm already had the package. The loop now waits on a 404 for ten
minutes and gives anything else three tries; these rows hold both
halves and the message that tells them apart.

The body under test is the workflow's own `fetch_published` function,
lifted out of `.github/workflows/verify-release.yml` by its first and
last lines and run by bash. The workflow runs no checkout, so the loop
cannot live in a script file; reading it out of the YAML is what keeps
the test on the code that ships. `sleep` is shadowed by a bash function
that records each nap instead of taking it, so the ten-minute row runs
in milliseconds and asserts the exact schedule.

The stub is a local HTTP server answering a scripted sequence of
statuses, one per request, repeating the last.

stdlib `unittest`, matching the other tests here. Run through the gate
as `mise run test`, which `ci` collects.
"""

import http.server
import os
import pathlib
import shutil
import socket
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import threading
import unittest
from typing import Self

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "verify-release.yml"
FIRST = "fetch_published() {"
BODY = b"published bytes\n"
OK = 200


def function_text() -> str:
    """Lift fetch_published out of the workflow's run block.

    Returns:
        The function as bash, dedented to column zero.

    Raises:
        AssertionError: The workflow carries no single first line, or no
            closing brace at its indent. Loud, never a skip: a test that
            found nothing to run reads as a passing one.

    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == FIRST]
    if len(starts) != 1:
        msg = f"expected one {FIRST!r} in {WORKFLOW}, found {len(starts)}"
        raise AssertionError(msg)
    start = starts[0]
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    for end in range(start + 1, len(lines)):
        if lines[end] == f"{indent}}}":
            body = lines[start : end + 1]
            return "\n".join(line.removeprefix(indent) for line in body) + "\n"
    msg = f"no closing brace for {FIRST!r} at indent {len(indent)}"
    raise AssertionError(msg)


class Stub:
    """A local server answering a scripted status sequence."""

    def __init__(self, statuses: list[int]) -> None:
        """Serve `statuses` in order, the last one repeated."""
        self.statuses = statuses
        self.requests = 0
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status = stub.statuses[min(stub.requests, len(stub.statuses) - 1)]
                stub.requests += 1
                body = BODY if status == OK else b"nope\n"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/pkg/-/pkg-1.0.0.tgz"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        """Start serving.

        Returns:
            This stub, serving.

        """
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()


class FetchPublished(unittest.TestCase):
    """Each row: a status sequence, the outcome, the naps, the message."""

    def setUp(self) -> None:
        """Give each row its own directory."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def fetch(self, url: str) -> tuple[subprocess.CompletedProcess[str], list[int]]:
        """Run the lifted function once against `url`.

        Returns:
            The finished process, and every nap it asked for in order.

        """
        naps = self.tmp / "naps"
        naps.touch()
        script = (
            "set -euo pipefail\n"
            'sleep() { echo "$1" >> "${NAPS}"; }\n'
            f"{function_text()}"
            'fetch_published pkg-1.0.0.tgz "$1" "${DEST}"\n'
        )
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.tmp),
            "NAPS": str(naps),
            "DEST": str(self.tmp / "pkg-1.0.0.tgz"),
            "ORG_USER_AGENT": "test",
        }
        bash = shutil.which("bash")
        if bash is None:
            self.fail("bash is not on PATH")
        proc = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [bash, "-c", script, "fetch", url],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return proc, [int(n) for n in naps.read_text(encoding="utf-8").split()]

    def test_404_three_times_then_200_passes(self) -> None:
        """The propagation window: three 404s, then the bytes."""
        with Stub([404, 404, 404, 200]) as stub:
            proc, naps = self.fetch(stub.url)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(stub.requests, 4)
        self.assertEqual(naps, [10, 20, 30])
        self.assertEqual((self.tmp / "pkg-1.0.0.tgz").read_bytes(), BODY)
        self.assertNotIn("::error::", proc.stdout)

    def test_404_for_the_whole_budget_fails_not_served(self) -> None:
        """Ten minutes of 404s, capped at 60s a nap, then the message."""
        with Stub([404]) as stub:
            proc, naps = self.fetch(stub.url)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(naps, [10, 20, 30, 40, 50, 60, 60, 60, 60, 60, 60, 60, 30])
        self.assertEqual(sum(naps), 600)
        self.assertEqual(stub.requests, len(naps) + 1)
        self.assertIn(
            "::error::pkg-1.0.0.tgz: not served after 10 minutes", proc.stdout
        )

    def test_503_fails_after_three_tries_naming_the_status(self) -> None:
        """A registry that is down is not waited on for ten minutes."""
        with Stub([503]) as stub:
            proc, naps = self.fetch(stub.url)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(stub.requests, 3)
        self.assertEqual(naps, [10, 20])
        self.assertIn("::error::pkg-1.0.0.tgz: failed with 503", proc.stdout)
        self.assertNotIn("not served", proc.stdout.replace("not served yet", ""))

    def test_refused_connection_fails_after_three_tries(self) -> None:
        """No server at all is a failure with no HTTP status, not a 404."""
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc, naps = self.fetch(f"http://127.0.0.1:{port}/pkg-1.0.0.tgz")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(naps, [10, 20])
        self.assertIn(
            "::error::pkg-1.0.0.tgz: failed with no HTTP status (curl exit 7)",
            proc.stdout,
        )


if __name__ == "__main__":
    unittest.main()

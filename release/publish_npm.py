#!/usr/bin/env python3
"""Publish a packed tarball to npm with trusted publishing (#1383).

The body of publish-wasm-npm.yml's Publish step, moved out of the YAML so
it is tested where it lives. Run from the caller's checkout, with the
packed tarball under dist/ and the caller's pinned npm on PATH:

    publish_npm.py <name@version>

TWO HALVES OF ONE QUESTION (#907). Trusted publishing needs npm >= 11.5.1,
which detects the OIDC environment itself — no token is exchanged by hand
and none is ever printed. If the pinned npm is too old the answer is to
bump the pin, never to mutate the runner with an unpinned global install.
That proves npm CAN do trusted publishing; the second check proves the
runner OFFERED it. Neither implies the other, and the missing second half
is what cost edtf v1.3.2 three release runs: npm, finding no OIDC
environment, does not say so — it looks for a stored credential and dies
`ENEEDAUTH ... This command requires you to be logged in`, which reads as
a missing token secret and sends the reader to the registry's publisher
config, which was correct all along.

RESUMABLE. A version already on the registry is a completed step, not a
failure; the unpublish window makes re-runs real.

THE PUBLISH. npm generates provenance automatically under trusted
publishing; --provenance is asserted anyway so a silent fallback to a
weaker auth path fails instead of publishing unattested. The `./` prefix
is load-bearing: an argument with one slash and no leading `./` is a
GitHub SHORTHAND to npm (owner/repo), and it tried to git-clone
dist/<name>.tgz as a repository (found by the v0.14.0 run).

ON FAILURE ONLY, npm's own debug log is printed (#907): npm writes the
line naming the step it failed at to a file and prints only a path, so
three re-runs of edtf v1.3.2 were blind by construction. It is MASKED,
because it is a third-party CLI's log from an authenticated path landing
in a public job log: lines naming a credential are dropped outright
rather than rewritten — dropping cannot half-work — and any long opaque
token-shaped run of characters left elsewhere is redacted. The lines this
exists to read (`npm verb publish`, the OIDC calls) carry no credentials,
so the masking cannot eat the evidence.
"""

import os
import re

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

NEED = "11.5.1"
LOG_TAIL = 300
CREDENTIAL_LINE = re.compile(
    r"[Aa]uthorization|[Bb]earer|_authToken|[Ss]et-[Cc]ookie|npm-otp"
)
TOKEN_SHAPED = re.compile(r"[A-Za-z0-9_-]{32,}")


def say(line: str) -> None:
    """Print one job-log line at once, in order with npm's own."""
    print(line, flush=True)


def version_key(version: str) -> tuple[int, ...]:
    """Order versions as `sort -V` orders these: numerically, part by part.

    Returns:
        The version's numeric parts.

    """
    return tuple(int(part) for part in re.findall(r"\d+", version))


def npm(
    env: Mapping[str, str], cwd: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    """Run the caller's pinned npm, capturing nothing but its exit.

    Returns:
        The finished process; its stdout when captured by the caller.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        # ruff: ignore[start-process-with-partial-path] - npm is the caller's pin, on PATH
        ["npm", *args],
        env=dict(env),
        cwd=cwd,
        capture_output=args[0] in {"--version", "view", "config"},
        text=True,
        check=False,
    )


def masked(log: str) -> list[str]:
    """Drop credential lines and redact token-shaped runs, keeping the tail.

    Returns:
        The last LOG_TAIL lines that survive, each redacted.

    """
    kept = [
        TOKEN_SHAPED.sub("<redacted>", line)
        for line in log.splitlines()
        if not CREDENTIAL_LINE.search(line)
    ]
    return kept[-LOG_TAIL:]


def show_debug_log(env: Mapping[str, str], cwd: Path) -> None:
    """Print npm's latest debug log, masked, or say there is none."""
    asked = npm(env, cwd, "config", "get", "logs-dir").stdout.strip()
    logdir = Path(asked) if asked else None
    if logdir is None or not logdir.is_dir():
        logdir = Path(env.get("HOME", "~")) / ".npm" / "_logs"
    logs = (
        sorted(str(p) for p in logdir.rglob("*-debug-0.log") if p.is_file())
        if logdir.is_dir()
        else []
    )
    if not logs:
        say(f"::warning::npm left no debug log under {logdir}")
        return
    say(f"::group::npm debug log (masked) — {logs[-1]}")
    for line in masked(Path(logs[-1]).read_text(encoding="utf-8", errors="replace")):
        say(line)
    say("::endgroup::")


def run(package: str, env: Mapping[str, str], cwd: Path) -> int:
    """Publish the one tarball under dist/ unless the version is already out.

    Returns:
        The exit code: npm publish's own, 0 when already published, 1 on a refusal.

    """
    have = npm(env, cwd, "--version").stdout.strip()
    if version_key(have) < version_key(NEED):
        say(
            f"::error::npm {have} < {NEED}; trusted publishing needs {NEED}+"
            " — bump the pin in this repository's mise config"
        )
        return 1
    if not env.get("ACTIONS_ID_TOKEN_REQUEST_URL") or not env.get(
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
    ):
        say(
            "::error::the OIDC token endpoint is absent from this job's environment,"
            " so npm cannot use trusted publishing — every job in the call chain must"
            " grant id-token: write, including the caller's own job in its repository"
        )
        return 1
    say(f"trusted publishing: npm {have}, OIDC endpoint present")

    if npm(env, cwd, "view", package, "version").returncode == 0:
        say(f"::notice::{package} already on the registry; publish is complete")
        return 0

    # The shell's `dist/*.tgz`: hidden names are not matched.
    tarballs = sorted(
        str(p.relative_to(cwd))
        for p in (cwd / "dist").glob("*.tgz")
        if not p.name.startswith(".")
    )
    if len(tarballs) != 1:
        say(f"::error::expected exactly one tarball, found {len(tarballs)}")
        return 1

    rc = npm(
        env, cwd, "publish", f"./{tarballs[0]}", "--provenance", "--access", "public"
    ).returncode
    if rc != 0:
        show_debug_log(env, cwd)
    return rc


def main(argv: list[str]) -> int:
    """Publish argv's one package from the job's environment.

    Returns:
        The exit code.

    """
    if len(argv) != 1 or not argv[0]:
        say("usage: publish_npm.py <name@version>")
        return 2
    return run(argv[0], os.environ, Path.cwd())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

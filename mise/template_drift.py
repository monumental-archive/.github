#!/usr/bin/env python3
"""audit:template-pins: the template pin carries every shipped canon change.

The scheduled half of lint:template-pins (#110, #111): the workflow
templates pin one canon SHA, and what makes a stale pin dangerous is a
commit between that pin and HEAD touching what the pin delivers, the
shared workflows and the toolbelt (`.github/workflows/`, `mise/`). The
task's comment in mise/config.toml has the reasons; this is its logic
(#1911).

  no canon pin in the templates: skipped, by name;
  more than one pinned SHA: red (lint:template-pins should have failed);
  a SHA not in the clone: red (a shallow clone);
  no commit since the pin touching either path: ok;
  otherwise red, every such commit listed, unless the pin is inside the
  post-release window: a release cannot pin itself (#227), so for 24
  hours after the first release tag newer than the pin's own the lag
  is reported and passes (#290 finding 4). The clock starts at that
  EARLIEST newer tag (#310 finding 1), so a burst of releases is one
  short staleness and a week-old lag stays red. A pin on the newest tag
  measures from its own tag, as the shell's `getline` past the last
  line did.

Usage:
  template_drift.py OWNER/REPO TEMPLATE...

OWNER/REPO is the canon (security/identity.toml, read by the task).
Each template is read as UTF-8, strictly. Exit 0 with its report, 1 on
a finding, 2 on a usage or read error or a failed git call. Stdlib
only, and git on PATH, run in the canon's clone.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import findings
import typed_std

GIT = shutil.which("git") or "git"
EXIT_RED = 1
EXIT_ERROR = 2
# The canon and at least one template.
MIN_ARGS = 2
GRACE = 86400
HOUR = 3600
PATHS = (".github/workflows/", "mise/")
HEX = re.compile(r"[0-9a-f]{40}")
# sort -V's order on the tags this repository cuts: digit runs compare
# as numbers, everything else as text.
RUNS = re.compile(r"\d+|\D+")


class GitError(RuntimeError):
    """A git call failed: its answer is not an empty one."""


def git(*args: str) -> str:
    """Run git in the working directory.

    Returns:
        Its standard output.

    Raises:
        GitError: git exited non-zero.

    """
    done = subprocess.run([GIT, *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        msg = f"git {' '.join(args)} failed ({done.returncode}): {done.stderr.strip()}"
        raise GitError(msg)
    return done.stdout


def read(path: Path) -> str:
    """Read a template as UTF-8, strictly.

    Returns:
        Its text.

    Raises:
        ValueError: the file cannot be read or is not UTF-8.

    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        msg = f"{path}: cannot be read as UTF-8: {err}"
        raise ValueError(msg) from err


def pinned(texts: list[str], canon: str) -> list[str]:
    """Name the distinct canon SHAs the templates pin.

    Returns:
        Every 40-hex SHA inside a `uses: <canon>/…@<sha>` match, sorted
        and distinct, as `grep -o | grep -o | sort -u` gave them.

    """
    pin = re.compile(rf"uses:[ \t\r\f\v]*{re.escape(canon)}/[^@\n]+@[0-9a-f]{{40}}")
    shas: set[str] = set()
    for text in texts:
        for match in pin.finditer(text):
            found = HEX.finditer(typed_std.group(match) or "")
            shas.update(typed_std.group(sha) or "" for sha in found)
    return sorted(shas)


def version_key(tag: str) -> list[tuple[int, int, str]]:
    """Order a tag as `sort -V` does for `v<major>.<minor>.<patch>` names.

    Returns:
        Its runs, a digit run by number and any other run by text.

    """
    key: list[tuple[int, int, str]] = []
    for found in RUNS.finditer(tag):
        run = typed_std.group(found) or ""
        key.append((0, int(run), "") if run.isdigit() else (1, 0, run))
    return key


def successor(pin_tag: str) -> str:
    """Name the release tag after the pin's, in version order.

    Returns:
        The next tag; the pin's own when it is the newest (awk's
        `getline` at the end leaves the line as it was); "" when the pin
        names no tag.

    """
    if not pin_tag:
        return ""
    tags = sorted(git("tag", "--list", "v[0-9]*").split(), key=version_key)
    at = tags.index(pin_tag)
    return tags[at + 1] if at + 1 < len(tags) else pin_tag


def hours(age: int) -> int:
    """Whole hours in an age, truncated toward zero as bash's `/` is.

    Returns:
        The hours.

    """
    whole = abs(age) // HOUR
    return whole if age >= 0 else -whole


def in_window(sha: str, now: int) -> bool:
    """Report the post-release window when the pin is inside it.

    Returns:
        True when it is, after printing the report.

    """
    first = git("tag", "--points-at", sha, "--list", "v[0-9]*").split()
    pin_tag = first[0] if first else ""
    succ_tag = successor(pin_tag)
    if not pin_tag or not succ_tag:
        return False
    age = now - int(git("log", "-1", "--format=%ct", succ_tag).strip())
    if age >= GRACE:
        return False
    print(
        f"audit:template-pins: post-release window — pin at {pin_tag},"
        f" stale for {hours(age)}h (since {succ_tag})"
    )
    print("  the lag is structural (#227); Renovate's chore(canon) bump")
    print("  closes it. Red only past the 24h grace (#290, #310).")
    return True


def audit(sha: str, now: int) -> int:
    """Judge one pin against HEAD.

    Returns:
        0 when it carries every change or is inside the window, EXIT_RED
        otherwise.

    """
    if subprocess.run(
        [GIT, "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        check=False,
    ).returncode:
        findings.finding(
            "", f"pinned SHA {sha[:8]} not in local history (shallow clone?)"
        )
        return EXIT_RED
    if not git("diff", "--name-only", f"{sha}..HEAD", "--", *PATHS).strip():
        print(
            f"audit:template-pins: pin {sha[:8]} carries all shipped workflow"
            " and belt changes"
        )
        return 0
    if in_window(sha, now):
        return 0
    log = git("log", "--no-decorate", "--oneline", f"{sha}..HEAD", "--", *PATHS)
    findings.finding(
        "",
        f"pin {sha[:8]} misses shipped canon changes:",
        [
            *(line for line in log.split("\n") if line),
            "a repo standing up from the templates would not get these —",
            "bump the template pins to current canon (see #110, #111).",
        ],
    )
    return EXIT_RED


def main(argv: list[str], now: int | None = None) -> int:
    """Audit the templates' canon pin.

    Returns:
        0 when it holds or is skipped, EXIT_RED on a finding, EXIT_ERROR
        on a usage or read error or a failed git call.

    """
    owner, _, repo = argv[0].partition("/") if argv else ("", "", "")
    if not owner or not repo or len(argv) < MIN_ARGS:
        findings.finding("", "usage: template_drift.py OWNER/REPO TEMPLATE...")
        return EXIT_ERROR
    texts: list[str] = []
    for name in argv[1:]:
        try:
            texts.append(read(Path(name)))
        except ValueError as err:
            findings.finding("", str(err))
            return EXIT_ERROR
    shas = pinned(texts, argv[0])
    if not shas:
        print("audit:template-pins: no org pins in templates, skipped")
        return 0
    if len(shas) > 1:
        findings.finding(
            "",
            "templates pin more than one SHA (lint:template-pins should have failed)",
        )
        return EXIT_RED
    try:
        return audit(shas[0], int(time.time()) if now is None else now)
    except GitError as err:
        findings.finding("", str(err))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

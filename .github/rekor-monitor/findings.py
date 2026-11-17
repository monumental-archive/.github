#!/usr/bin/env python3
"""File one issue per certificate that did not reconcile (#1250, #1339).

`stele assert signer-certificates` judges the certificate log and seals
a report; this files what it found. Every finding in the report is one
condition a certificate failed, its subject `CT index <n>`. A
certificate is one issue, however many conditions it failed, titled

  rekor-monitor: certificate at CT index <n> did not reconcile (<conditions>)

and deduplicated by that index: a run that judges the same range again
files nothing twice. The report's findings are read whatever its
verdict: a run that stopped partway (CANNOT_JUDGE) still carries the
findings of every chunk it finished, and the next run starts past them.

The workflow saves the checkpoint only when this exits 0, so a finding
that could not be filed is judged again by the next run, never skipped.
Exits 2 by name, filing nothing more, when stele wrote no report, the
report is not stele's signer-certificates report, a finding's subject is
not a CT index, or a forge read or write fails.

Usage:
  findings.py --report FILE --run-url URL

The repository is GITHUB_REPOSITORY; no organisation, repository or log
is named here. Stdlib only, and `gh` on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] - gh is the forge client this workflow uses
import sys
from pathlib import Path

TARGET = "assert signer-certificates"
EXIT_ERROR = 2
SUBJECT = re.compile(r"^CT index (\d+)$")
PREFIX = "rekor-monitor: certificate at CT index"


class FindingsError(Exception):
    """Nothing more can be filed; the run must go red and save nothing."""


def title(index: int, conditions: list[str]) -> str:
    """Name a certificate's issue.

    Returns:
        The issue title, carrying the index and every failed condition.

    """
    failed = ", ".join(conditions)
    return f"{PREFIX} {index} did not reconcile ({failed})"


def filed_title(index: int) -> re.Pattern[str]:
    """Match a title this tool would have filed for one index.

    Returns:
        A pattern for that index alone: CT index 12 is not CT index 123,
        because the index is followed by the rest of the title.

    """
    return re.compile(rf"^{re.escape(PREFIX)} {index} did not reconcile")


def read_report(path: Path) -> dict:
    """Read stele's report.

    Returns:
        The report document.

    Raises:
        FindingsError: no report, or not stele's signer-certificates one.

    """
    if not path.is_file() or path.stat().st_size == 0:
        msg = (
            "stele wrote no report: it refused before judging"
            " (its reason is in its own step)"
        )
        raise FindingsError(msg)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        msg = f"stele's report is not JSON: {err}"
        raise FindingsError(msg) from err
    if not isinstance(report, dict) or report.get("target") != TARGET:
        msg = f"the report is not stele's {TARGET} report"
        raise FindingsError(msg)
    return report


def by_certificate(report: dict) -> dict[int, list[dict]]:
    """Group the report's findings by CT index.

    Returns:
        CT index to its findings, in report order.

    Raises:
        FindingsError: a finding whose subject is not a CT index.

    """
    grouped: dict[int, list[dict]] = {}
    for finding in report.get("findings") or []:
        subject = finding.get("subject", "")
        match = SUBJECT.match(subject)
        if not match:
            msg = f"a finding's subject is not a CT index: {subject!r}"
            raise FindingsError(msg)
        grouped.setdefault(int(match.group(1)), []).append(finding)
    return grouped


def body(index: int, findings: list[dict], log: str, run_url: str) -> str:
    """Write a certificate's issue body.

    Returns:
        The body: the log, each failed condition with stele's reason.

    """
    return "\n".join([
        f"The signer-identity watch found a certificate at CT index {index}",
        "that does not reconcile against a signing the org performed.",
        "",
        f"- CT index: {index} ({log})",
        f"- Found by: {run_url}",
        "",
        "Failed, with stele's reason and the claims it read:",
        *[f"- **{f.get('assertion', '')}**: {f.get('detail', '')}" for f in findings],
        "",
        'Runbook: docs/runbook.md, "rekor-monitor: a certificate did not reconcile".',
        "",
    ])


def gh(*args: str) -> str:
    """Run gh and return its output.

    Returns:
        gh's standard output.

    Raises:
        FindingsError: gh exited non-zero.

    """
    proc = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        ["gh", *args],  # ruff: ignore[start-process-with-partial-path] - gh is resolved on PATH, as in the workflow
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        verb = " ".join(args[:2])
        detail = proc.stderr.strip() or "no output"
        msg = f"gh {verb} failed (exit {proc.returncode}): {detail}"
        raise FindingsError(msg)
    return proc.stdout


def already_filed(repo: str, index: int) -> int | None:
    """Find this index's issue, open or closed.

    Returns:
        Its number, or None when none was filed.

    Raises:
        FindingsError: the search answered something that is not JSON.

    """
    out = gh(
        "issue", "list", "--repo", repo, "--state", "all", "--limit", "100",
        "--search", f'"CT index {index}" in:title', "--json", "number,title",
    )  # fmt: skip
    try:
        issues = json.loads(out)
    except json.JSONDecodeError as err:
        msg = f"gh issue list answered something that is not JSON: {err}"
        raise FindingsError(msg) from err
    pattern = filed_title(index)
    for issue in issues:
        if pattern.match(issue.get("title", "")):
            return int(issue["number"])
    return None


def file_all(repo: str, report: dict, run_url: str) -> list[str]:
    """File each certificate's issue that is not filed yet.

    Returns:
        One line per certificate, for the run's log.

    """
    log = str(report.get("subject", ""))
    done = []
    for index, findings in sorted(by_certificate(report).items()):
        existing = already_filed(repo, index)
        if existing is not None:
            done.append(f"CT index {index}: already filed as #{existing}")
            continue
        conditions = list(dict.fromkeys(str(f.get("assertion", "")) for f in findings))
        gh(
            "issue", "create", "--repo", repo, "--title", title(index, conditions),
            "--body", body(index, findings, log, run_url),
        )  # fmt: skip
        done.append(f"CT index {index}: filed ({', '.join(conditions)})")
    return done


def main(argv: list[str] | None = None) -> int:
    """File the report's findings.

    Returns:
        0 when every finding has its issue, EXIT_ERROR otherwise.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("findings: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return EXIT_ERROR
    try:
        done = file_all(repo, read_report(args.report), args.run_url)
    except FindingsError as err:
        print(f"findings: {err}; the checkpoint is not saved", file=sys.stderr)
        return EXIT_ERROR
    for line in done:
        print(f"findings: {line}")
    print(f"findings: {len(done)} certificate(s) did not reconcile")
    return 0


if __name__ == "__main__":
    sys.exit(main())

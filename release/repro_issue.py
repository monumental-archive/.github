#!/usr/bin/env python3
"""File the issue a failed reproducibility audit owes.

`repro-check.yml`'s "File an issue on failure" step calls this as its
one command (#1386); before that it was a 28-line `run:` block. The
behaviour is the block's, ported, and the title and body are the
block's, word for word.

INPUTS. From the environment, as the step sets them: `TAG`, `CLASS`,
`MANIFEST` (the typed manifest resolve handed on, or empty), `COMPARE`
(the compare step's outcome), `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`,
`GITHUB_RUN_ID`, and the `GH_TOKEN` the `gh` CLI reads.

WHICH STEP FAILED DECIDES WHAT THE ISSUE MAY SAY (#1262). The step runs
on `failure()`, which is true for ANY failed step in the job, and a mise
download that died five times on curl exit 22 filed release-lab#305 as
a pinning defect against a release whose bytes matched. Only a
comparison that ran and failed is a verdict; anything else (skipped,
cancelled) is no verdict, and is still filed, because an audit that
could not judge must be seen.

A VERDICT NAMES ITS SCOPE. One run rebuilds ONE class, so a finding
belongs to that class and to the population the verdict judged. The old
wording asserted "a build input has escaped the pinning" over the whole
release, which is how release-lab#226 and #227 were filed twice, under
identical titles, against a release that was fine. The title carries
the class for the same reason. The scope sentence reads the SAME input
that chose the document the verdict judged, so the two cannot disagree.

stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Run:
    """One failed audit run, as the step describes it."""

    tag: str
    cls: str
    manifest: str
    compare: str
    run_id: str
    run_url: str


def issue(run: Run) -> tuple[str, str]:
    """Word the issue for one failed run.

    Returns:
        The title and the body.

    """
    tag, cls, compare, run_id, run_url = (
        run.tag, run.cls, run.compare, run.run_id, run.run_url,
    )  # fmt: skip
    if compare != "failure":
        body = (
            f"The reproducibility audit set out to rebuild {tag} with the {cls}"
            " class workflow, and no verdict was reached: the job failed before"
            f" the comparison ran (the compare step was {compare})."
            " Nothing is known about the published bytes from this run."
            " Read the run to see which step failed, then dispatch it again:"
            f" {run_url}"
        )
        return f"repro-check: {tag} {cls} could not be compared (run {run_id})", body
    if run.manifest.strip():
        scope = f"the {cls} artifacts {tag} published"
    else:
        scope = (
            f"every build subject {tag} published — it carries no per-class"
            f" typing, so this {cls} rebuild could not be scoped to its own class"
        )
    body = (
        f"The reproducibility audit rebuilt {tag} with the {cls} class workflow"
        f" and the verdict is not a pass. Judged population: {scope}."
        " A digest that DIFFERS from the published one means a build input"
        " escaped the pinning."
        " An artifact reported ABSENT is one in the judged population this"
        " rebuild did not produce; read the scope the run states before"
        " treating it as a pinning defect."
        f" Read the run: {run_url}"
    )
    title = (
        f"repro-check: {tag} {cls} did not rebuild to the published bytes"
        f" (run {run_id})"
    )
    return title, body


def main() -> int:
    """File the issue.

    Returns:
        `gh issue create`'s exit status.

    """
    env = os.environ
    repo, run_id = env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"]
    title, body = issue(
        Run(
            tag=env["TAG"],
            cls=env["CLASS"],
            manifest=env.get("MANIFEST", ""),
            compare=env.get("COMPARE", ""),
            run_id=run_id,
            run_url=f"{env['GITHUB_SERVER_URL']}/{repo}/actions/runs/{run_id}",
        )
    )
    return subprocess.run(
        # ruff: ignore[start-process-with-partial-path]
        ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body],
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())

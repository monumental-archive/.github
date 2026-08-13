#!/usr/bin/env python3
"""The caller/callee permissions join (#358, seam 2).

GitHub makes `permissions:` caller-owned: a reusable workflow inherits
its caller's grant and can only downgrade it, so any workflow_call
workflow that gains a capability is a breaking change to every caller —
enforced at runtime as a startup_failure with no jobs and no log, the
worst available enforcement point. The requirement is nevertheless
statically computable: the union of the callee's job-level grants is
exactly what a caller must hold. This file computes that union and
compares it against caller grants, so the seam is checked at the PR
that adds the capability instead of discovered by the first consumer
release that dies on it.

One derivation site, three callers, all of them `check`. In the
canon's own gate `lint:caller-permissions` checks its callers and
every workflow-templates/ stub against this tree, i.e. canon HEAD. In
a CONSUMER's gate the same lint runs this script out of `.org-canon` —
the full canon tree ci.yml already places at the pin that repo
resolved — with `--canon .org-canon`, so requirements come from the
pinned canon and the consumer's own stubs are what get checked, on the
pin-bump PR, in the repo being bumped. `audit:caller-permissions`
(Monday cron) downloads every org consumer's workflow files and passes
them to `check` against canon HEAD — a forecast of the next bump, the
only question a caller that shipped atomically with its pin can still
fail.

The scanner is closed under its own coverage claim: a `uses:` line
shaped like a canon call that it cannot read, or a recognised call
whose callee is absent from the canon tree, is a hard failure, never a
silent skip — an unrecognised call is an unchecked grant, and a green
line for a check that did not run is the failure class the
audit-claims contract forbids.

Stdlib only (the gate runner has no pyyaml); the org's workflows are
written at fixed 2/4/6-space indentation, which is what the scanner
assumes — the same assumption every belt awk already makes.

Usage:
  workflow-permissions.py requirements [--canon DIR]
      print `<workflow> <scope> <level>` for each workflow_call
      workflow in DIR/.github/workflows/ (DIR defaults to `.`)
  workflow-permissions.py check [--canon DIR] [caller-file ...]
      verify every `uses:` job referencing a canon workflow grants at
      least the callee's requirement, computed from DIR. With no
      arguments and DIR `.`, checks the canon's own workflows and
      workflow-templates/; with no arguments and an explicit DIR,
      checks this repo's .github/workflows/ (consumer mode, where an
      empty or canon-caller-free set is a clean skip). Exits 1 on any
      under-grant, listing each as file:job missing scope.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

LEVELS = {"none": 0, "read": 1, "write": 2}
WRITE = LEVELS["write"]

# The org's workflows are written at fixed 2/4/6-space indentation, so
# these are the shape of a workflow file, not arbitrary numbers: a job
# key sits at 2, a job's own body (`permissions:`, `uses:`) at 4.
JOB_INDENT = 2
JOB_BODY_INDENT = 4
# `--canon DIR` consumes its flag and one value.
CANON_FLAG_ARITY = 2

# A `uses:` line that targets a canon reusable workflow, whether from
# inside this repo (local path) or from a consumer (pinned reference).
USES_RE = re.compile(
    r"""uses:\s*
        (?:\./)?(?:monumental-archive/\.github/)?
        \.github/workflows/([A-Za-z0-9._-]+\.ya?ml)
        (?:@[0-9a-f]{40})?\s*(?:\#.*)?$""",
    re.VERBOSE,
)
# The looser shape of the same target: anything this matches that
# USES_RE does not (a tag pin, a truncated SHA, a shape the scanner
# has never seen) is a canon call the check cannot prove, and refusing
# is the only honest answer. Third-party reusable workflows
# (`owner/repo/.github/workflows/…`) match neither and are not ours to
# check.
CANON_SHAPE_RE = re.compile(
    r"^\s*uses:\s*(?:\./)?(?:monumental-archive/\.github/)?"
    r"\.github/workflows/"
)
SCOPE_RE = re.compile(r"^\s*([a-z][a-z-]*):\s*(none|read|write)\s*(#.*)?$")
PERMS_RE = re.compile(r"^(\s*)permissions:\s*(\{\})?\s*(#.*)?$")
JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$")


def indent_of(line: str) -> int:
    """Return the number of leading spaces on `line`.

    Returns:
        The count of leading space characters.

    """
    return len(line) - len(line.lstrip(" "))


def code_lines(path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield `(lineno, line)` for every non-comment line of `path`.

    Yields:
        Each 1-based line number paired with the line, newline stripped,
        skipping whole-line comments.

    """
    with Path(path).open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if line.strip().startswith("#"):
                continue
            yield n, line


def _refuse_unreadable_call(path: str | Path, n: int, line: str) -> None:
    """Exit if `line` is a canon call this scanner cannot parse."""
    if CANON_SHAPE_RE.match(line) and not USES_RE.search(line):
        sys.exit(
            f"FAIL: {path}:{n}: unrecognised canon workflow "
            "call — a call the scanner cannot read is an "
            "unchecked grant"
        )


@dataclass
class _Parse:
    """Mutable state threaded through one workflow file's single pass.

    `block_indent`/`block_into` track the `permissions:` block currently
    accepting `scope: level` lines; both are None between blocks.
    """

    path: str | Path
    jobs: dict[str, dict] = field(default_factory=dict)
    job: str | None = None
    in_jobs: bool = False
    block_indent: int | None = None
    block_into: dict[str, int] | None = None


def _grant_for_indent(st: _Parse, ind: int) -> tuple[dict[str, int], bool]:
    """Return the grant dict a `permissions:` at `ind` fills.

    Returns:
        The grant dict, and whether it is the workflow-level one.

    """
    grant: dict[str, int] = {}
    if st.in_jobs and st.job is not None and ind == JOB_BODY_INDENT:
        st.jobs[st.job]["perms"] = grant
        return grant, False
    if ind == 0:
        return grant, True
    # A permissions key anywhere else (e.g. under `with:`) would be a
    # shape this scanner does not understand: refuse rather than misread.
    sys.exit(f"FAIL: {st.path}: permissions block at unexpected indent {ind}")


def _absorb_scope(st: _Parse, line: str, ind: int) -> bool:
    """Take one `scope: level` line into the open permissions block.

    Closes the block in place when `line` leaves it.

    Returns:
        True if the line belonged to the block and was consumed.

    """
    if st.block_indent is None:
        return False
    if ind > st.block_indent:
        m = SCOPE_RE.match(line)
        if m:
            st.block_into[m.group(1)] = LEVELS[m.group(2)]
            return True
    st.block_indent = None
    st.block_into = None
    return False


def _job_key(st: _Parse, line: str, ind: int) -> str | None:
    """Return the job name `line` declares, if it declares one.

    Returns:
        The job key, or None when the line is not a job declaration.

    """
    if not st.in_jobs:
        return None
    m = JOB_RE.match(line)
    return m.group(1) if m and ind == JOB_INDENT else None


def _record_permissions(
    st: _Parse,
    line: str,
    ind: int,
) -> tuple[bool, dict[str, int] | None]:
    """Record a `permissions:` line and open its scope block.

    Returns:
        Whether the line was a permissions block, and the grant dict if
        that block was the workflow-level one.

    """
    m = PERMS_RE.match(line)
    if not m:
        return False, None
    grant, is_workflow_level = _grant_for_indent(st, ind)
    if m.group(2) != "{}":
        st.block_indent, st.block_into = ind, grant
    return True, grant if is_workflow_level else None


def _record_uses(st: _Parse, line: str, ind: int) -> None:
    """Record the canon workflow this job calls, if this line is the call."""
    if not (st.in_jobs and st.job is not None and ind == JOB_BODY_INDENT):
        return
    target = _uses_target(line)
    if target is not None:
        st.jobs[st.job]["uses"] = target


def _uses_target(line: str) -> str | None:
    """Return the canon workflow filename `line` calls, if any.

    Returns:
        The callee's filename, or None when the line is not a `uses:`.

    """
    m = USES_RE.search(line)
    if m and line.lstrip().startswith("uses:"):
        return m.group(1)
    return None


def scan(path: str | Path) -> tuple[bool, dict[str, int] | None, dict[str, dict]]:
    """One pass: workflow-level grant, per-job grants, per-job uses target.

    Returns:
        `(is_workflow_call, workflow_grant, jobs)` where jobs is
        `{job: {"perms": {scope: level} | None, "uses": target | None}}`.
        A job with no permissions block has perms None (it takes the
        workflow-level default), which is distinct from an explicit `{}`.

    """
    st = _Parse(path=path)
    workflow_call = False
    wf_grant = None
    for n, line in code_lines(path):
        if not line.strip():
            continue
        _refuse_unreadable_call(path, n, line)
        ind = indent_of(line)
        if _absorb_scope(st, line, ind):
            continue
        if re.match(r"^\s*workflow_call:", line):
            workflow_call = True
        if line == "jobs:":
            st.in_jobs = True
            st.job = None
            continue
        if st.in_jobs and ind == 0:
            st.in_jobs = False
        key = _job_key(st, line, ind)
        if key is not None:
            st.job = key
            st.jobs[key] = {"perms": None, "uses": None}
            continue
        handled, workflow_level_grant = _record_permissions(st, line, ind)
        if handled:
            # `is not None`, never truthiness: an explicit `permissions: {}`
            # is an empty dict, and that empty grant is the meaningful
            # workflow-level default the org pins everywhere.
            if workflow_level_grant is not None:
                wf_grant = workflow_level_grant
            continue
        _record_uses(st, line, ind)
    return workflow_call, wf_grant, st.jobs


def requirements(canon: str = ".") -> dict[str, dict[str, int]]:
    """scope->level union per workflow_call workflow, keyed by filename.

    Union over every job's grant, including `uses:` jobs' restated
    grants: a nested callee's ask chains up through this workflow to
    its caller, so the restatement IS part of this workflow's
    requirement. Jobs without a block take the workflow-level default,
    which the org pins to `{}` everywhere — contributing nothing.

    Returns:
        `{workflow filename: {scope: level}}`, one entry per
        workflow_call workflow found under `canon`.

    """
    req = {}
    for path in sorted(Path(canon).glob(".github/workflows/*.y*ml")):
        is_wc, wf_grant, jobs = scan(path)
        if not is_wc:
            continue
        union: dict[str, int] = {}
        for j in jobs.values():
            grant = j["perms"] if j["perms"] is not None else (wf_grant or {})
            for scope, lvl in grant.items():
                union[scope] = max(union.get(scope, 0), lvl)
        req[path.name] = union
    return req


def check(
    paths: list[str] | list[Path],
    canon: str = ".",
) -> tuple[int, list[str]]:
    """Check each caller in `paths` against the canon's requirements.

    Returns:
        The number of canon-calling jobs checked, and one message per
        under-grant or unrecognised callee.

    """
    req = requirements(canon)
    bad = []
    checked = 0
    for path in paths:
        _, wf_grant, jobs = scan(path)
        for name, j in jobs.items():
            target = j["uses"]
            if target is None:
                continue
            if target not in req:
                bad.append(
                    f"{path}:{name} calls {target}, absent from the "
                    f"canon tree at {canon} — an unrecognised callee "
                    "is an unchecked grant"
                )
                continue
            checked += 1
            grant = j["perms"] if j["perms"] is not None else (wf_grant or {})
            for scope, lvl in req[target].items():
                if lvl > 0 and grant.get(scope, 0) < lvl:
                    bad.append(
                        f"{path}:{name} calls {target} but grants no "
                        f"'{scope}: {'write' if lvl == WRITE else 'read'}' — "
                        "the run dies as startup_failure, no jobs, no log"
                    )
    return checked, bad


def _parse_args(argv: list[str]) -> tuple[str, str, list[str]]:
    """Split argv into command, canon dir and caller paths.

    Returns:
        `(command, canon, paths)`; exits with usage on anything else.

    """
    if not argv or argv[0] not in {"requirements", "check"}:
        sys.exit(__doc__.strip())
    cmd, argv = argv[0], argv[1:]
    canon = "."
    if argv[:1] == ["--canon"]:
        if len(argv) < CANON_FLAG_ARITY:
            sys.exit(__doc__.strip())
        canon, argv = argv[1], argv[CANON_FLAG_ARITY:]
    return cmd, canon, argv


def _print_requirements(canon: str) -> None:
    """Print `<workflow> <scope> <level>` for every workflow_call file."""
    for wf, union in sorted(requirements(canon).items()):
        for scope, lvl in sorted(union.items()):
            level = next(k for k, v in LEVELS.items() if v == lvl)
            print(f"{wf} {scope} {level}")


def _default_paths(*, consumer: bool) -> list[str]:
    """Return the caller files to check when none were named.

    Returns:
        Sorted workflow paths: in consumer mode the repo's own
        .github/workflows/, otherwise the canon's plus its templates.

    """
    here = Path()
    if consumer:
        return sorted(str(p) for p in here.glob(".github/workflows/*.y*ml"))
    return sorted(
        str(p)
        for p in [
            *here.glob(".github/workflows/*.y*ml"),
            *here.glob("workflow-templates/*.y*ml"),
        ]
    )


def _report(checked: int, bad: list[str], canon: str, *, consumer: bool) -> None:
    """Print the check outcome, exiting 1 if any grant was short."""
    if bad:
        print("caller grants below the callee's computed requirement:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        sys.exit(1)
    if consumer and checked == 0:
        print("no canon callers, skipped")
        return
    if consumer:
        print(f"{checked} caller job(s) checked against the pinned canon at {canon}")
        return
    print(f"{checked} caller job(s) checked against computed requirements")


def main() -> None:
    """Entry point: `requirements` prints them, `check` enforces them."""
    cmd, canon, paths = _parse_args(sys.argv[1:])
    if cmd == "requirements":
        _print_requirements(canon)
        return
    consumer = canon != "."
    if not paths:
        paths = _default_paths(consumer=consumer)
        if consumer and not paths:
            print("no workflows, skipped")
            return
    checked, bad = check(paths, canon)
    _report(checked, bad, canon, consumer=consumer)


if __name__ == "__main__":
    main()

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

import glob
import os
import re
import sys

LEVELS = {"none": 0, "read": 1, "write": 2}

# A `uses:` line that targets a canon reusable workflow, whether from
# inside this repo (local path) or from a consumer (pinned reference).
USES_RE = re.compile(
    r"""uses:\s*
        (?:\./)?(?:monumental-archive/\.github/)?
        \.github/workflows/([A-Za-z0-9._-]+\.ya?ml)
        (?:@[0-9a-f]{40})?\s*(?:\#.*)?$""",
    re.X,
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


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def code_lines(path):
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if line.strip().startswith("#"):
                continue
            yield n, line


def scan(path):
    """One pass: workflow-level grant, per-job grants, per-job uses target.

    Returns (is_workflow_call, workflow_grant, jobs) where jobs is
    {job: {"perms": {scope: level} | None, "uses": target | None}}.
    A job with no permissions block has perms None (it takes the
    workflow-level default), which is distinct from an explicit `{}`.
    """
    workflow_call = False
    wf_grant = None
    jobs = {}
    job = None
    in_jobs = False
    perms_indent = None
    perms_into = None
    for n, line in code_lines(path):
        if not line.strip():
            continue
        if CANON_SHAPE_RE.match(line) and not USES_RE.search(line):
            sys.exit(f"FAIL: {path}:{n}: unrecognised canon workflow "
                     "call — a call the scanner cannot read is an "
                     "unchecked grant")
        ind = indent_of(line)
        if perms_indent is not None:
            if ind > perms_indent:
                m = SCOPE_RE.match(line)
                if m:
                    perms_into[m.group(1)] = LEVELS[m.group(2)]
                    continue
            perms_indent = None
            perms_into = None
        if re.match(r"^\s*workflow_call:", line):
            workflow_call = True
        if line == "jobs:":
            in_jobs = True
            job = None
            continue
        if in_jobs and ind == 0:
            in_jobs = False
        if in_jobs:
            m = JOB_RE.match(line)
            if m and ind == 2:
                job = m.group(1)
                jobs[job] = {"perms": None, "uses": None}
                continue
        m = PERMS_RE.match(line)
        if m:
            grant = {}
            if in_jobs and job is not None and ind == 4:
                jobs[job]["perms"] = grant
            elif ind == 0:
                wf_grant = grant
            else:
                # A permissions key anywhere else (e.g. under `with:`)
                # would be a shape this scanner does not understand:
                # refuse rather than misread.
                sys.exit(f"FAIL: {path}: permissions block at unexpected "
                         f"indent {ind}")
            if m.group(2) != "{}":
                perms_indent = ind
                perms_into = grant
            continue
        if in_jobs and job is not None and ind == 4:
            m = USES_RE.search(line)
            if m and line.lstrip().startswith("uses:"):
                jobs[job]["uses"] = m.group(1)
    return workflow_call, wf_grant, jobs


def requirements(canon="."):
    """scope->level union per workflow_call workflow, keyed by filename.

    Union over every job's grant, including `uses:` jobs' restated
    grants: a nested callee's ask chains up through this workflow to
    its caller, so the restatement IS part of this workflow's
    requirement. Jobs without a block take the workflow-level default,
    which the org pins to `{}` everywhere — contributing nothing.
    """
    req = {}
    for path in sorted(glob.glob(
            os.path.join(canon, ".github/workflows/*.y*ml"))):
        is_wc, wf_grant, jobs = scan(path)
        if not is_wc:
            continue
        union = {}
        for j in jobs.values():
            grant = j["perms"] if j["perms"] is not None else (wf_grant or {})
            for scope, lvl in grant.items():
                union[scope] = max(union.get(scope, 0), lvl)
        req[os.path.basename(path)] = union
    return req


def check(paths, canon="."):
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
                        f"'{scope}: {'write' if lvl == 2 else 'read'}' — "
                        "the run dies as startup_failure, no jobs, no log"
                    )
    return checked, bad


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("requirements", "check"):
        sys.exit(__doc__.strip())
    cmd, argv = argv[0], argv[1:]
    canon = "."
    if argv[:1] == ["--canon"]:
        if len(argv) < 2:
            sys.exit(__doc__.strip())
        canon, argv = argv[1], argv[2:]
    if cmd == "requirements":
        for wf, union in sorted(requirements(canon).items()):
            for scope, lvl in sorted(union.items()):
                level = [k for k, v in LEVELS.items() if v == lvl][0]
                print(f"{wf} {scope} {level}")
        return
    paths = argv
    consumer = canon != "."
    if not paths:
        if consumer:
            paths = sorted(glob.glob(".github/workflows/*.y*ml"))
            if not paths:
                print("no workflows, skipped")
                return
        else:
            paths = sorted(
                glob.glob(".github/workflows/*.y*ml")
                + glob.glob("workflow-templates/*.y*ml")
            )
    checked, bad = check(paths, canon)
    if bad:
        print("caller grants below the callee's computed requirement:",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        sys.exit(1)
    if consumer and checked == 0:
        print("no canon callers, skipped")
        return
    if consumer:
        print(f"{checked} caller job(s) checked against the pinned "
              f"canon at {canon}")
        return
    print(f"{checked} caller job(s) checked against computed requirements")


if __name__ == "__main__":
    main()

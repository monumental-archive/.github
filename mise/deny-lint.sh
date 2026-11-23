#!/usr/bin/env bash
# The body of `lint:deny`: the gate half of the dependency track
# (docs/dependency-track.md). Org canon, delivered by the belt and run
# as `bash "${ORG_BELT_DIR}/deny-lint.sh"`; extracted from
# mise/config.toml when #1926 added the advisory-ignore rule and the
# body passed lint:task-shell's ceiling.
#
# Deterministic: the outcome is a function of Cargo.lock + the committed
# deny.toml. `cargo fetch --locked` first — fetching pinned inputs is the
# same category as fetching the toolchain. Advisories are deliberately
# NOT here: RustSec is a moving feed, so they live in audit:deny
# (mise/deny-audit.sh).
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
org_task "lint:deny"
locks=() && org_tracked_read locks ':!vendor/**' "Cargo.lock" "*/Cargo.lock"
[[ -n ${locks[0]+set} ]] || {
  echo "lint:deny: no Cargo.lock tracked, skipped"
  exit 0
}
# A repo with a lock has rust pinned as a build input; cargo absent is a
# broken toolchain, and skipping would be the vacuous-success failure.
command -v cargo > /dev/null || {
  org_finding "" "Cargo.lock present but cargo missing — pin rust in mise.toml"
  exit 1
}
org_tracked_has deny.toml || {
  org_finding "" "Cargo.lock present but no deny.toml — copy scaffold/deny.toml from the canon"
  exit 1
}
# A REPOSITORY KEEPS NO ADVISORY IGNORE (#1926). The decision is written
# once, in the canon's security/vex/, and audit:deny has stele judge the
# scan against it on the exact (advisory, package, version) triple. An
# `ignore` entry is a second copy keyed by the advisory alone, so it
# excuses every version and outlives the decision it copied. It reads no
# feed, so refusing it is a property of the tree, and the gate's. An
# entry keyed by `crate` (a yanked version) names no advisory and stays.
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
"${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" "${belt}/deny_advisories.py" deny.toml
cargo fetch --locked
cargo deny check bans licenses sources
# An exception that outlives its cause is the failure `#[expect]` was
# chosen to prevent everywhere else, and cargo-deny does not have that
# property: a `skip`/`skip-tree` entry matching nothing is silent
# (measured). So necessity is tested rather than assumed — each declared
# skip is removed in turn and the check re-run. A variant that still
# passes proves its entry does nothing, and the gate names it. The
# helper resolves from the belt, which is where the org's policy lives;
# a repo running without the canon cannot quietly skip this.
helper="${belt}/deny_skips.py"
variants=$("${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" "${helper}" deny.toml)
stale=""
while IFS=$'\t' read -r name variant; do
  [[ -n ${name} ]] || continue
  if cargo deny --config "${variant}" check bans > /dev/null 2>&1; then
    stale="${stale} ${name}"
  fi
done <<< "${variants}"
if [[ -n ${stale} ]]; then
  org_finding "" "duplicate-version skip no longer needed:${stale}" \
    "the check passes without it, so the upstream lag it was written for is over" \
    "delete the entry from deny.toml — an exception that outlives its cause is not a decision"
  exit 1
fi

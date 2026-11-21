#!/usr/bin/env bash
# The body of `audit:deny`: the Rust lock's advisories, judged against
# the canon's decisions. Org canon, delivered by the belt and run as
# `bash "${ORG_BELT_DIR}/deny-audit.sh"` (#1926).
#
# audit:*, not lint:*: a new advisory fails unrelated work, which is a
# property of the feed, not the diff.
#
# THE EXIT IS A WRITTEN DECISION, NEVER AN OVERRIDE, and it is written
# once (#1926): the canon's security/vex/, joined on the exact
# (advisory, package, version) triple by `stele assert advisories`, as
# audit:go-vulns has done since #640. A repository keeps no advisory
# `ignore` — lint:deny refuses one — because an ignore is keyed by the
# advisory alone, excuses it at every version, and was a second copy of
# a decision the canon already holds.
#
# cargo-deny writes its JSON to STDERR (probed: stdout is empty), and
# its exit status is NOT the verdict and is not read: it is 1 for a
# finding and for a failure alike. A scan that did not finish is
# stele's CANNOT_JUDGE. The fetch is quiet because its download
# progress buried a red job's verdict (250 of 304 lines, measured).
#
# The subject is Cargo.lock: what the scan covers. The judgment, and
# every rule of it, is stele's (stele's docs/vex-join.md).
set -euo pipefail
locks=$(git ls-files ':!vendor/**' "Cargo.lock" "*/Cargo.lock")
[[ -n ${locks} ]] || {
  echo "audit:deny: no Cargo.lock tracked, skipped"
  exit 0
}
command -v cargo > /dev/null || {
  echo "audit:deny: Cargo.lock present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}
[[ -f deny.toml ]] || {
  echo "audit:deny: Cargo.lock present but no deny.toml — copy scaffold/deny.toml from the canon" >&2
  exit 1
}
command -v stele > /dev/null || {
  echo "audit:deny: stele missing from the belt" >&2
  exit 1
}
canon="${ORG_CANON_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
vex="${canon}/security/vex"
[[ -d ${vex} ]] || {
  echo "audit:deny: ${vex} is not a directory, so the org's triage decisions cannot be read" >&2
  echo "  the org belt did not arrive: CI sets MISE_GLOBAL_CONFIG_FILE, locally run: lefthook run post-checkout" >&2
  exit 1
}
scan=$(mktemp)
rc=0
trap 'rc=$?; rm -f "${scan}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
cargo fetch --locked --quiet
# `|| true`: the status is 1 for a finding and a failure alike, so it is
# no verdict; stele judges the scan, an unfinished one CANNOT_JUDGE.
cargo deny --format json check advisories 2> "${scan}" || true
"${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" "${belt}/stele_findings.py" assert advisories \
  --scanner cargo-deny --scan "${scan}" --vex "${vex}" --subject Cargo.lock
org_done=1

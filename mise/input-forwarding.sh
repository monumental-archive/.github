#!/usr/bin/env bash
# Walk every local reusable-workflow call site and report the universal
# inputs and secrets it fails to forward (#305). Org canon — lint:input-
# forwarding is this walk, and fix:input-forwarding inserts what it names.
#
# ONE walk, two callers, which is why this is a script and not a task body
# (#82, #764). fix:input-forwarding used to obtain the missing-input list
# by running `mise run lint:input-forwarding` from inside a task; that
# nested mise raced the ~40 lint tasks `ci` runs in parallel and failed to
# resolve its own lockfile — the failure #82 recorded. `fix:*` is outside
# the gate, so it could not red `ci` on its own, but it is the same class,
# and the class is what #764 killed. Both halves now invoke this file.
#
# The findings go to STDERR, one line per site, and are consumed as an
# API: fix:input-forwarding parses `<file>:<job> does not forward
# universal <kind> <name> of <callee>` and inserts the expression. Change
# the two together — the format is the contract between them, and it is
# the reason the walk is shared rather than re-derived.
#
# Why the contract is declared where an input is BORN rather than at the
# call sites that consume it, what the two markers mean, and why the scope
# is local `uses: ./` callers only is written at lint:input-forwarding,
# the task that names this script.
set -euo pipefail

files=$(git ls-files ':!vendor/**' ".github/workflows/*.y*ml")
[[ -n ${files} ]] || {
  echo "lint:input-forwarding: no workflows, skipped"
  exit 0
}
# shellcheck disable=SC2086  # the file list is newline-separated on purpose
grep -l "uses: ./.github/workflows/" ${files} > /dev/null 2>&1 || {
  echo "lint:input-forwarding: no local reusable-workflow callers, skipped"
  exit 0
}
# shellcheck disable=SC2086  # the file list is newline-separated on purpose
awk '
FNR == 1 { split(FILENAME, P, "/"); base = P[length(P)]; in_wc = 0; sect = ""; curkey = ""; in_jobs = 0; job = ""; mode = "" }
/^[^ ]/ { in_wc = 0; sect = ""; in_jobs = ($0 ~ /^jobs:/); job = ""; mode = "" }
/^  workflow_call:/ { if (!in_jobs) { in_wc = 1; sect = ""; next } }
in_wc && /^  [^ ]/ { in_wc = 0; sect = "" }
/# forwarding-default: discretionary/ { fdef[base] = 1 }
in_wc && /^    inputs:/ { sect = "input"; curkey = ""; next }
in_wc && /^    secrets:/ { sect = "secret"; curkey = ""; next }
in_wc && /^    [^ ]/ { sect = ""; curkey = "" }
in_wc && sect != "" && /^      [A-Za-z0-9_-]+:/ {
  curkey = $1; sub(/:.*/, "", curkey)
  ndecl++; dbase[ndecl] = base; dkind[ndecl] = sect; dname[ndecl] = curkey; dclass[ndecl] = ""
  last[base SUBSEP sect SUBSEP curkey] = ndecl
}
in_wc && sect != "" && curkey != "" && /# forwarding: universal/ { dclass[last[base SUBSEP sect SUBSEP curkey]] = "universal" }
in_wc && sect != "" && curkey != "" &&
  /# forwarding: discretionary/ { dclass[last[base SUBSEP sect SUBSEP curkey]] = "discretionary" }
in_jobs && /^  [A-Za-z0-9_-]+:/ { job = $1; sub(/:.*/, "", job); mode = "" }
in_jobs && job != "" && /^    uses: \.\/\.github\/workflows\// {
  callee = $2; sub(/^\.\/\.github\/workflows\//, "", callee)
  ncall++; cfile[ncall] = FILENAME; cjob[ncall] = job; cbase[ncall] = callee; called[callee] = 1
}
in_jobs && job != "" && /^    with:/ { mode = "input"; next }
in_jobs && job != "" && /^    secrets: inherit/ { inherited[FILENAME SUBSEP job] = 1; mode = ""; next }
in_jobs && job != "" && /^    secrets:/ { mode = "secret"; next }
in_jobs && job != "" && /^    [^ ]/ { mode = "" }
in_jobs && job != "" && mode != "" && /^      [A-Za-z0-9_-]+:/ {
  k = $1; sub(/:.*/, "", k); fwd[FILENAME SUBSEP job SUBSEP mode SUBSEP k] = 1
}
END {
  fail = 0
  for (i = 1; i <= ndecl; i++)
    if (dclass[i] == "" && called[dbase[i]] && !fdef[dbase[i]]) {
      printf "lint:input-forwarding: %s %s in %s carries no forwarding marker\n", dkind[i], dname[i], dbase[i] > "/dev/stderr"
      fail = 1
    }
  for (j = 1; j <= ncall; j++)
    for (i = 1; i <= ndecl; i++) {
      if (dbase[i] != cbase[j] || dclass[i] != "universal") continue
      if (dkind[i] == "secret" && inherited[cfile[j] SUBSEP cjob[j]]) continue
      if (!fwd[cfile[j] SUBSEP cjob[j] SUBSEP dkind[i] SUBSEP dname[i]]) {
        printf "lint:input-forwarding: %s:%s does not forward universal %s %s of %s\n",
          cfile[j], cjob[j], dkind[i], dname[i], dbase[i] > "/dev/stderr"
        fail = 1
      }
    }
  if (fail) exit 1
  print "lint:input-forwarding: every universal input is forwarded at every local call site"
}
' ${files}

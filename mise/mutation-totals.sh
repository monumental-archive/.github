#!/usr/bin/env bash
# The one answer across one language's audit:mutants shards (#1355). Org
# canon — the body of each language's totals job in repo-mutants.yml.
#
# repo-mutants.yml runs a language's leg as a matrix of shards
# (`--shard k/n`), each printing its own survivors and summary line. The
# measure is still one answer — no unexplained survivor in the WHOLE
# population — so this reads what every shard wrote and states the
# totals: mutants, killed, survived, excluded.
#
# Usage: mutation-totals.sh <dir> <language>. ONE LANGUAGE PER CALL:
# each language has its own shard jobs and its own totals job, so a red
# names its language at the job level. Each shard's summary arrives as
# its own artifact, so the directory holds one subdirectory per shard
# and, in each, `<language>.tsv`:
#
#   k/n <TAB> mutants <TAB> killed <TAB> survived <TAB> excluded
#   k/n <TAB> skipped
#
# Red, by name, when a shard of n is missing (a shard that died, or whose
# run judged nothing, writes no file), when shards disagree about n,
# about adoption, or about the whole-population excluded count (each
# shard reports the same figure; it is taken once), and when any
# survivor exists. No language is written here.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=audit:mutants}"

usage="usage: mutation-totals.sh <directory of per-shard summaries> <language>"
dir="${1:?${usage}}"
lang="${2:?${usage}}"
label="audit:mutants: ${lang}"

# No artifact at all means download-artifact makes no directory, and a
# `find` of a missing directory fails inside the pipeline above its own
# message — measured on the canon's first run (44208357536), which died
# on "No such file or directory" with nothing named.
rows=""
if [[ -d ${dir} ]]; then
  rows=$(find "${dir}" -name "${lang}.tsv" -type f -exec cat {} + | sort -n)
fi
if [[ -z ${rows} ]]; then
  org_finding "${lang}" "no shard reported a summary — every shard died before its last line"
  exit 1
fi

status=0
verdict=$(awk -F '\t' -v label="${label}" -v lang="${lang}" '
  {
    split($1, kn, "/")
    if (n == "") n = kn[2]
    if (kn[2] != n) { printf "%s\037shards disagree about n (%s, %s)\n", lang, n, kn[2]; bad = 1; next }
    if (kn[1] in seen) { printf "%s\037shard %s reported twice\n", lang, $1; bad = 1; next }
    seen[kn[1]] = 1
    if ($2 == "skipped") { skipped++; next }
    judged++
    mutants += $2; killed += $3; survived += $4
    if (excluded == "") excluded = $5
    else if ($5 != excluded) { printf "%s\037shards disagree about excluded (%s, %s)\n", lang, excluded, $5; bad = 1 }
  }
  END {
    if (skipped > 0 && judged > 0) { printf "%s\037some shards skipped and some judged\n", lang; bad = 1 }
    for (k = 0; k < n; k++) if (!(k in seen)) {
      printf "%s\037shard %d/%s reported nothing — it died or judged nothing\n", lang, k, n
      bad = 1
    }
    if (bad) exit 1
    if (judged == 0) { printf "%s: not adopted, skipped in all %s shards\n", label, n; exit 0 }
    printf "%s: across %s shards: %d mutants, %d killed, %d survived, %s excluded\n",
      label, n, mutants, killed, survived, excluded
    if (survived > 0) exit 2
  }' <<< "${rows}") || status=$?

case ${status} in
  0) echo "${verdict}" ;;
  2)
    echo "${verdict}"
    org_finding "${lang}" "red — the survivors are named in their shards' logs"
    exit 1
    ;;
  *)
    # Each refusal is a finding line for the writer (#1934).
    org_finding_lines <<< "${verdict}"
    exit 1
    ;;
esac

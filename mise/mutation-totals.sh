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
  echo "${label}: no shard reported a summary — every shard died before its last line" >&2
  exit 1
fi

status=0
verdict=$(awk -F '\t' -v label="${label}" '
  {
    split($1, kn, "/")
    if (n == "") n = kn[2]
    if (kn[2] != n) { printf "%s: shards disagree about n (%s, %s)\n", label, n, kn[2]; bad = 1; next }
    if (kn[1] in seen) { printf "%s: shard %s reported twice\n", label, $1; bad = 1; next }
    seen[kn[1]] = 1
    if ($2 == "skipped") { skipped++; next }
    judged++
    mutants += $2; killed += $3; survived += $4
    if (excluded == "") excluded = $5
    else if ($5 != excluded) { printf "%s: shards disagree about excluded (%s, %s)\n", label, excluded, $5; bad = 1 }
  }
  END {
    if (skipped > 0 && judged > 0) { printf "%s: some shards skipped and some judged\n", label; bad = 1 }
    for (k = 0; k < n; k++) if (!(k in seen)) {
      printf "%s: shard %d/%s reported nothing — it died or judged nothing\n", label, k, n
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
    echo "${label}: red — the survivors are named in their shards' logs" >&2
    exit 1
    ;;
  *)
    echo "${verdict}" >&2
    exit 1
    ;;
esac

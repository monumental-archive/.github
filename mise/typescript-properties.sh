#!/usr/bin/env bash
# A repository's fast-check properties, under the belt's seed.
# Org canon — the body of `test:typescript-properties` (the gate, `gate`)
# and `audit:typescript-properties` (the Monday audit, `audit`), one
# script so the two tasks find the same files the same way (#1549, the
# TypeScript half of #1332's shape).
#
# The subject is every tracked `*.prop.test.{ts,tsx,mts,cts}`, run by
# the repository's own vitest with fast-check's seed and run count set
# by the belt: a fixed seed in the gate, so two runs execute the same
# examples, and a fresh one on the cron, printed first so a failure
# replays. How the seed reaches fast-check, and why through a wrapper
# config, is typescript_properties.py's header. The third mode, `lint`,
# is `lint:typescript-properties`: the design conditions, no fast-check
# import outside a property file and no property restating the seed or
# run count, read from the source so a property that would override
# the belt's seed is red before it runs.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

profile="${1:-}"
case "${profile}" in
  gate)
    label="test:typescript-properties"
    # Fixed: the gate is deterministic. The number is the testing
    # standard's issue (#1336), and nothing else about it matters.
    seed=1336
    runs=200
    ;;
  audit)
    label="audit:typescript-properties"
    seed="${ORG_PROPERTY_SEED:-$(($(od -An -N4 -tu4 /dev/urandom) & 0x7fffffff))}"
    runs=10000
    [[ ${seed} =~ ^[0-9]+$ ]] || {
      org_finding "" "ORG_PROPERTY_SEED must be a non-negative integer, got '${seed}'"
      exit 2
    }
    ;;
  lint)
    label="lint:typescript-properties"
    ;;
  *)
    org_finding "" "expected gate, audit or lint, got '${profile}'"
    exit 2
    ;;
esac
# The task this script reports for, where mise named none (#1934).
org_task "${label}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

# The lockfile decides, as for the coverage leg (#1357): no fast-check
# in it, nothing here can be a property.
locks=$(git ls-files -- pnpm-lock.yaml package-lock.json yarn.lock bun.lock)
pins=""
if [[ -n ${locks} ]]; then
  pins=$(tr '\n' '\0' <<< "${locks}" | xargs -0 python3 "${belt}/typescript_properties.py" locked)
fi
grep -qx fast-check <<< "${pins}" || {
  echo "${label}: no fast-check in a tracked lockfile, skipped"
  exit 0
}

if [[ ${profile} == lint ]]; then
  sources=$(git ls-files ':!vendor/**' '*.ts' '*.tsx' '*.mts' '*.cts' '*.js' '*.jsx' '*.mjs' '*.cjs')
  [[ -n ${sources} ]] || {
    echo "${label}: no TypeScript or JavaScript tracked, skipped"
    exit 0
  }
  findings=$(tr '\n' '\0' <<< "${sources}" | xargs -0 python3 "${belt}/typescript_properties.py" lint) || {
    detail=()
    while IFS= read -r finding; do detail+=("${finding}"); done <<< "${findings}"
    org_finding "" "properties live in *.prop.test.* files and take the belt's seed:" \
      "${detail[@]}"
    exit 1
  }
  count=$(wc -l <<< "${sources}")
  echo "${label}: $((count)) file(s) clean"
  exit 0
fi

files=$(git ls-files ':!vendor/**' '*.prop.test.ts' '*.prop.test.tsx' '*.prop.test.mts' '*.prop.test.cts')
[[ -n ${files} ]] || {
  echo "${label}: no *.prop.test.{ts,tsx,mts,cts} tracked, skipped"
  exit 0
}

grep -qx vitest <<< "${pins}" || {
  org_finding "" "*.prop.test.* tracked but the lockfile pins no vitest —" \
    "the leg runs properties with the repository's own vitest (#1549)"
  exit 1
}
vitest="${PWD}/node_modules/.bin/vitest"
[[ -x ${vitest} ]] || {
  org_finding "" "vitest is in the lockfile but not installed — install from the" \
    "lockfile first (pnpm install --frozen-lockfile, or npm ci)"
  exit 1
}

# The repository's own config, in vitest's own lookup order.
config=""
for base in vitest.config vite.config; do
  for ext in ts mts cts js mjs cjs; do
    if [[ -z ${config} ]] && git ls-files --error-unmatch "${base}.${ext}" > /dev/null 2>&1; then
      config="${base}.${ext}"
    fi
  done
done

scratch=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${scratch}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
python3 "${belt}/typescript_properties.py" stage "${scratch}" ${config:+"${config}"} <<< "${files}"

args=()
while IFS= read -r f; do
  args+=("${f}")
done <<< "${files}"

# The repository's OWN run must not collect a property file (#1549,
# ruling of 2026-11-19): its `test` task would run the properties a
# second time, unseeded, inside the same gate. vitest answers which
# files it collects from the repository's config as vitest resolves
# it (`list --filesOnly` loads the config and runs no globalSetup), so
# this reads the evaluated include/exclude, never the config's text.
# The gate's question only: the audit is not a gate.
if [[ ${profile} == gate ]]; then
  "${vitest}" list --filesOnly --json="${scratch}/collected.json" --root "${PWD}" \
    > "${scratch}/list.log" 2>&1 || {
    org_finding "" "vitest could not list the files the repository's own config collects:"
    sed 's/^/  /' "${scratch}/list.log" >&2
    exit 1
  }
  leaked=$(python3 "${belt}/typescript_properties.py" collected "${scratch}/collected.json" "${args[@]}") || {
    detail=()
    while IFS= read -r line; do detail+=("  ${line}"); done <<< "${leaked}"
    org_finding "" "the repository's own vitest run collects these property files," \
      "so its test task runs them a second time with no seed:" \
      "${detail[@]}" \
      "add the property pattern to test.exclude in ${config:-a vitest.config.ts}, beside vitest's" \
      "defaults (configDefaults from vitest/config):" \
      '  exclude: [...configDefaults.exclude, "**/*.prop.test.*"],'
    exit 1
  }
fi

if [[ ${profile} == audit ]]; then
  echo "${label}: seed ${seed} (reproduce: ORG_PROPERTY_SEED=${seed})"
fi
echo "${label}: seed ${seed}, ${runs} runs per property, ${config:-no vitest config}"
status=0
ORG_PROPERTY_SEED="${seed}" ORG_PROPERTY_RUNS="${runs}" \
  ORG_PROPERTY_REPORT="${scratch}/report.json" \
  "${vitest}" run --root "${PWD}" -c "${scratch}/org-property.config.mts" "${args[@]}" || status=$?
if ((status != 0)); then
  if [[ ${profile} == audit ]]; then
    org_finding "" "seed ${seed} — commit each counterexample above as a fixed" \
      "example of its property, with its fix, and the gate replays it"
  fi
  exit "${status}"
fi
# vitest exits 0 for a filter that matched nothing it could run under a
# config that passes with no tests; the report is the proof each ran.
missing=$(python3 "${belt}/typescript_properties.py" verify "${scratch}/report.json" "${args[@]}") || {
  detail=()
  while IFS= read -r line; do detail+=("${line}"); done <<< "${missing}"
  org_finding "" "not every property file ran:" \
    "${detail[@]}"
  exit 1
}
echo "${label}: every property file held"
org_done=1

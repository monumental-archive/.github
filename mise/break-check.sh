#!/usr/bin/env bash
# A pull request's title against what the change breaks (#1906). Org
# canon — the body of `break:check`, which the gate's test leg runs on a
# pull request ("The title declares what the change breaks", ci.yml).
#
# WHY. A release's number comes from its commit types, and a commit's
# type is its pull request's title. edtf#289 removed the `pg14` feature
# under `feat(postgres): drop Postgres 14`, its body said "This is not
# breaking", and the Release PR proposed v1.4.0 where cargo's rules say
# 2.0.0. `audit:semver` saw it days later, on a schedule, somewhere that
# cannot refuse a merge. So each pull request is measured against its
# own base, and a break its title and body do not declare is red on the
# pull request, where editing the title is the remedy. A release's
# number is the largest claim in its range: if every merged change
# declares its own break, the number is right.
#
# THE DECLARATION is mise/declared_break.py's: `type!:`/`type(scope)!:`
# in the title, or a BREAKING CHANGE footer in the body read as the
# release engine reads it (#1924). Declared, nothing is measured.
#
# THE BASE IS HEAD^1. The gate checks out the pull request's merge
# commit, whose first parent is the commit the change lands on; the
# event's `base.sha` can lag it. A HEAD without exactly two parents is
# red by name. The base tree is `git archive HEAD^1`, in a scratch
# directory outside the checkout.
#
# THE POPULATION is mise/rust-api-population.sh's, shared with
# audit:semver, read for the base and for the head: a crate is judged
# when it may be published (`publish` is not false) and has a library
# target.
#
# EVERY MEASUREMENT IS MADE BEFORE THE VERDICT — the belt's two
# measurements of a public surface:
#
#   - a judged crate in the base and not in the head: a break;
#   - a judged crate in the head and not in the base: new, nothing to
#     break;
#   - an ordinary crate in both: cargo-semver-checks against HEAD^1 at
#     `--release-type minor`, which measures breaks, not additions, and
#     whatever the crate's version (measured, docs/tooling-verdicts.md).
#     Exit 0 is no break, 100 is a break, anything else is "could not be
#     measured" — red, never a pass. CARGO_TARGET_DIR is outside the
#     checkout: inside it, the tool's own baseline clone under `target/`
#     makes the second run's package ambiguous (measured);
#   - a pgrx crate in both: each `[features]` key of the base that the
#     head lacks — its Rust API is not built (2.5 minutes per major); an
#     extension's contract is the features it is built with and its SQL;
#   - each `schema.snapshot.sql` tracked in the base: each line the head's
#     lacks, and a file the head no longer tracks. An added line is not.
set -euo pipefail

label="break:check"
[[ -n ${PR_TITLE+set} ]] || {
  printf '%s%s\n' "${label}: PR_TITLE is unset — the gate step sets it from the pull request," \
    " and without it there is no declaration to hold" >&2
  exit 1
}
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

manifests=$(git ls-files ':!vendor/**' "Cargo.toml" "*/Cargo.toml")
[[ -n ${manifests} ]] || {
  echo "${label}: no Cargo.toml tracked, skipped"
  exit 0
}

declaration=$("${ORG_PYTHON}" "${belt}/declared_break.py")
if [[ ${declaration} == declared ]]; then
  echo "${label}: declared breaking, nothing to hold"
  exit 0
fi

parents=$(git rev-list --parents -n 1 HEAD | wc -w)
parents=$((parents - 1))
if [[ ${parents} -ne 2 ]]; then
  printf '%s%s\n' "${label}: HEAD has ${parents} parent(s), not 2 — the gate checks out the pull request's merge commit," \
    " whose first parent is the base it is measured against" >&2
  exit 1
fi
command -v cargo > /dev/null || {
  echo "${label}: Cargo.toml present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}

scratch=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
base="${scratch}/base"
mkdir "${base}"
git archive HEAD^1 | tar -x -C "${base}"

# shellcheck source=mise/rust-api-population.sh
. "${belt}/rust-api-population.sh"
# Assigned one step each: an empty here-string from a failed derivation
# would read as a workspace with no crates.
head_metadata=$(cargo metadata --no-deps --format-version 1 --locked)
head_population=$(org_rust_api_population "${head_metadata}")
base_metadata=""
base_population=""
if [[ -f ${base}/Cargo.toml ]]; then
  base_metadata=$(cd "${base}" && cargo metadata --no-deps --format-version 1 --locked)
  base_population=$(org_rust_api_population "${base_metadata}")
fi

# Judged: may be published, and has a library target. One list per
# tree, each name on a line of its own and the list wrapped in newlines,
# so membership is one pattern match (bash 3.2 on a Mac has no
# associative arrays).
judged_base=$(awk -F '\t' '$1 != "" && $2 != "none" && $2 != "-" && $3 == "lib" { print $1 }' \
  <<< "${base_population}")
judged_head=$(awk -F '\t' '$1 != "" && $2 != "none" && $2 != "-" && $3 == "lib" { print $1 }' \
  <<< "${head_population}")
judged_base=$'\n'${judged_base}$'\n'
judged_head=$'\n'${judged_head}$'\n'

breaks=()
unmeasured=()
verdicts=()

# In the base's own order, so the verdict reads the same every run.
while IFS=$'\t' read -r crate _where _library _kind _majors; do
  [[ -n ${crate} && ${judged_base} == *$'\n'"${crate}"$'\n'* ]] || continue
  [[ ${judged_head} != *$'\n'"${crate}"$'\n'* ]] || continue
  breaks+=("${label}: ${crate}: judged in the base and not in the head — crate removed or renamed")
done <<< "${base_population}"

while IFS=$'\t' read -r crate where library kind _majors; do
  [[ -n ${crate} ]] || continue
  if [[ ${where} == none ]]; then
    verdicts+=("${label}: ${crate}: publish = false, not judged")
    continue
  fi
  if [[ ${library} != lib ]]; then
    verdicts+=("${label}: ${crate}: no library target, not judged")
    continue
  fi
  if [[ ${judged_base} != *$'\n'"${crate}"$'\n'* ]]; then
    verdicts+=("${label}: ${crate}: new, nothing to break")
    continue
  fi
  if [[ ${kind} == pgrx ]]; then
    # Each tree's `[features]` keys for this member, one per line.
    base_features=$(jq -r --arg n "${crate}" '.workspace_members as $ids | .packages[]
      | select(.name == $n and (.id as $id | $ids | index($id))) | .features | keys[]' \
      <<< "${base_metadata}")
    head_features=$(jq -r --arg n "${crate}" '.workspace_members as $ids | .packages[]
      | select(.name == $n and (.id as $id | $ids | index($id))) | .features | keys[]' \
      <<< "${head_metadata}")
    kept=$'\n'${head_features}$'\n'
    lost=0
    while IFS= read -r feature; do
      [[ -n ${feature} && ${kept} != *$'\n'"${feature}"$'\n'* ]] || continue
      breaks+=("${label}: ${crate}: feature ${feature} removed")
      lost=1
    done <<< "${base_features}"
    [[ ${lost} -ne 0 ]] || verdicts+=("${label}: ${crate}: every feature of the base kept")
    continue
  fi
  echo "${label}: measuring ${crate} against HEAD^1"
  status=0
  CARGO_TARGET_DIR="${scratch}/target" cargo-semver-checks check-release \
    -p "${crate}" --baseline-rev HEAD^1 --release-type minor || status=$?
  case ${status} in
    0) verdicts+=("${label}: ${crate}: no break") ;;
    100) breaks+=("${label}: ${crate}: cargo-semver-checks measured a break (above)") ;;
    *) unmeasured+=("${label}: ${crate}: could not be measured (cargo-semver-checks exited ${status})") ;;
  esac
done <<< "${head_population}"

snapshots=$(git ls-tree -r --name-only HEAD^1 | awk '$0 == "schema.snapshot.sql" || /\/schema\.snapshot\.sql$/')
while IFS= read -r snapshot; do
  [[ -n ${snapshot} ]] || continue
  if ! git ls-files --error-unmatch -- "${snapshot}" > /dev/null 2>&1; then
    breaks+=("${label}: ${snapshot}: tracked in the base and not in the head")
    continue
  fi
  git show "HEAD^1:${snapshot}" > "${scratch}/base.sql"
  git show "HEAD:${snapshot}" > "${scratch}/head.sql"
  lost=$(awk 'NR == FNR { kept[$0] = 1; next } $0 != "" && !($0 in kept)' \
    "${scratch}/head.sql" "${scratch}/base.sql")
  if [[ -n ${lost} ]]; then
    while IFS= read -r line; do
      breaks+=("${label}: ${snapshot}: lost ${line}")
    done <<< "${lost}"
  else
    verdicts+=("${label}: ${snapshot}: every line of the base kept")
  fi
done <<< "${snapshots}"

[[ ${#verdicts[@]} -eq 0 ]] || printf '%s\n' "${verdicts[@]}"
[[ ${#unmeasured[@]} -eq 0 ]] || printf '%s\n' "${unmeasured[@]}" >&2
[[ ${#breaks[@]} -eq 0 ]] || printf '%s\n' "${breaks[@]}" >&2
if [[ ${#breaks[@]} -gt 0 ]]; then
  printf '%s%s%s\n' "${label}: red — the change breaks what the base offered and the title does not say so:" \
    " declare it in the TITLE (type(scope)!: …) or with a 'BREAKING CHANGE: ' footer in the BODY," \
    " and the release notes then list it" >&2
fi
if [[ ${#unmeasured[@]} -gt 0 ]]; then
  echo "${label}: red — a crate above could not be measured, and an unmeasured crate is never a pass" >&2
fi
[[ ${#breaks[@]} -eq 0 && ${#unmeasured[@]} -eq 0 ]] || exit 1
[[ ${#verdicts[@]} -gt 0 ]] || echo "${label}: no crate or schema snapshot to hold"
org_done=1

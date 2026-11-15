#!/usr/bin/env bash
# Decide whether this repository should release itself to remedy an
# advisory (#1167). Org canon — see docs/release.md, "A fixed main
# becomes a release".
#
# The defect this answers: Renovate lands a security fix on main within
# hours, and then nothing turns it into a release anyone can install.
# Measured 2026-11-14: stele's and iiif-server's current releases
# carried 26 and 11 undecided advisories, every fix already on main,
# stele's Release PR green and unmerged for twelve weeks.
#
# This script DECIDES and does nothing else. It merges nothing and holds
# no write token: the workflow's second job performs the merge, with the
# App token, only when this one says so. Every "no" is a sentence saying
# which question failed, because a scheduled job that quietly does
# nothing is indistinguishable from one that is broken.
#
# The questions, in order — the first "no" ends the run:
#
#   1. Is a release pending, and is it one a machine may cut? A plan
#      that releases nothing, or whose range asks for a breaking bump,
#      is not. A break waits for a maintainer: it is the kind of release
#      that needs a coordinated change somewhere else.
#   2. Is the Release PR the plan's own, unheld and mergeable? The
#      `hold` label is the maintainer's veto.
#   3. Is the SUPPORTED release affected? Asked of what was published —
#      the release's attested SBOMs, scanned and joined against the
#      org's decisions by `stele assert blast-radius` in its
#      single-repository form — and read from the fact stele states
#      (`supportedReleasesAffected`), never re-derived here. No release
#      is cut for an advisory on a release nobody is asked to install.
#   4. Would this release REMEDY any of it? At least one affected
#      `package@version` must no longer resolve on the tree being
#      released. Without this question the job is a loop: a finding
#      main does not fix stays red after the release, and every later
#      commit would cut another one.
#   5. Would the release survive its own publish? The same two belt
#      tasks the publish path runs at the tag run here first, on the
#      prepared tree. Failing here is free; failing after the tag burns
#      a version number (stele v0.17.0, GO-2026-5932).
#
# Questions 4 and 5 failing is the one case that needs a human — the
# supported release is affected and main does not fix it — so it is
# said in an issue, once, rather than in a log nobody opens.
#
# Inputs (environment):
#   PLAN               path to the release plan prepare-release.sh wrote
#   GITHUB_REPOSITORY  owner/name
#   GH_TOKEN           read access to this repository; issues: write
#                      when FILE_ISSUE is true
#   FILE_ISSUE         "true" to open the needs-a-human issue (the
#                      workflow sets it; a local run leaves it unset)
#   ORG_CANON_DIR      the canon tree (default .org-canon)
#
# Writes GITHUB_OUTPUT keys:
#   merge   true only when all five questions pass
#   pr      the Release PR number, when merge is true
#   reason  one sentence: why, or why not
set -euo pipefail

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

# no ends the run with the decision "do not release" and the sentence
# that explains it. Exit 0: declining is this job working.
no() {
  echo "security-release: no — $1"
  emit "merge=false"
  emit "reason=$1"
  # Declining is a completed run, so it carries the EXIT trap's mark.
  org_done=1
  exit 0
}

# needs_human is a "no" that only a person can turn into a "yes".
needs_human() {
  local why=$1 detail=$2
  if [[ ${FILE_ISSUE:-} == true ]]; then
    local title="the supported release carries an advisory and main does not remedy it"
    local open
    # A failed read is never read as "no issue yet": that files a
    # duplicate on every run for as long as the forge is degraded.
    if ! open=$(gh issue list --repo "${repo}" --state open \
      --search "in:title \"${title}\"" --json number --jq 'length'); then
      echo "security-release: could not read this repository's issues" >&2
      exit 1
    fi
    if [[ ${open} == 0 ]]; then
      local body
      body=$(
        cat << EOF
## Defect

\`${supported}\` is this repository's supported release and it ships an
advisory no decision covers. The scheduled security release declined to
cut a new one:

${why}

\`\`\`text
${detail}
\`\`\`

## Decided build

One of, per advisory: bump the dependency (or the toolchain) on \`main\`;
or record a decision in the canon's \`security/vex/\`. The scheduled job
releases by itself once \`main\` remedies at least one of them and passes
the publish path's own advisory checks.

## Done when

The canon's \`audit / advisory blast radius\` no longer names
\`${supported}\`.
EOF
      )
      gh issue create --repo "${repo}" --title "${title}" --body "${body}" > /dev/null
      echo "security-release: opened an issue — this one needs a maintainer"
    fi
  fi
  no "${why}"
}

plan="${PLAN:?security-release: PLAN is unset — prepare-release.sh writes it}"
repo="${GITHUB_REPOSITORY:?security-release: GITHUB_REPOSITORY is unset}"
name="${repo#*/}"
canon="${ORG_CANON_DIR:-.org-canon}"

for tool in jq gh stele; do
  command -v "${tool}" > /dev/null || {
    echo "security-release: ${tool} missing from the belt" >&2
    exit 1
  }
done

# 1. A release a machine may cut.
releasing=$(jq -r '.release' "${plan}")
if [[ ${releasing} != true ]]; then
  no "nothing to release: main carries no releasable change since the last tag"
fi
subject=$(jq -r '.commit.subject' "${plan}")
branch=$(jq -r '.branch.name' "${plan}")
requested=$(jq -r '.bump.requested // ""' "${plan}")
if [[ ${requested} == major ]]; then
  no "the pending release carries a breaking change, which waits for a maintainer"
fi

# 2. The plan's own Release PR, unheld and mergeable.
if ! pr=$(gh pr list --repo "${repo}" --head "${branch}" --state open \
  --json number,title,labels,mergeStateStatus --jq '.[0] // empty'); then
  echo "security-release: could not read this repository's pull requests" >&2
  exit 1
fi
[[ -n ${pr} ]] || no "no open Release PR on ${branch}"
number=$(jq -r '.number' <<< "${pr}")
title=$(jq -r '.title' <<< "${pr}")
if [[ ${title} != "${subject}" ]]; then
  no "Release PR #${number} is not the release main now describes (${subject}) — it has not been refreshed yet"
fi
if jq -e '[.labels[].name] | index("hold")' <<< "${pr}" > /dev/null; then
  no "Release PR #${number} carries the hold label"
fi
state=$(jq -r '.mergeStateStatus' <<< "${pr}")
if [[ ${state} != CLEAN ]]; then
  no "Release PR #${number} is not mergeable as it stands (${state})"
fi

# 3. The supported release, as published. The org canary lives in ONE
# repository's history, so the single-repository form cannot reproduce
# it and is asked without it. That is safe in this direction only: a
# scanner that cannot see reports nothing, nothing reported means no
# release, and the Monday audit — which does carry the canary — is the
# one that reds on a blind scanner.
policy=$(mktemp)
report=$(mktemp)
rc=0
trap 'rc=$?; rm -f "${policy}" "${report}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
jq 'del(.blastRadius.canary)' "${canon}/slsa/assert-policy.json" > "${policy}"

status=0
stele assert blast-radius --repo "${repo}" --policy "${policy}" \
  --vex "${canon}/security/vex" --json > "${report}" || status=$?
case "${status}" in
  0 | 1) ;;
  4) no "the published SBOMs could not be judged (stele: CANNOT_JUDGE)" ;;
  *)
    echo "security-release: stele assert blast-radius exited ${status}" >&2
    exit 1
    ;;
esac

fact() {
  jq -r --arg n "$1" '[.facts[]? | select(.name == $n) | .value] | first // ""' "${report}"
}
# One repo@tag out of a space-separated fact, by repository.
of_this_repo() {
  tr ' ' '\n' <<< "$1" | grep -E "^${name}@" | head -n 1 || true
}
all_supported=$(fact supportedReleases)
supported=$(of_this_repo "${all_supported}")
withheld=$(fact supersessionWithheld)
[[ -n ${supported} ]] \
  || no "no supported release could be named for ${name}: ${withheld:-the policy declares no support window}"
all_affected=$(fact supportedReleasesAffected)
affected=$(of_this_repo "${all_affected}")
[[ -n ${affected} ]] || no "the supported release ${supported} carries no undecided advisory"

# 4. Would releasing remedy any of it? The affected package versions,
# against what the tree being released resolves.
findings=$(jq -r --arg s "${supported}" \
  '.findings[]? | select(.subject == $s) | [.assertion, .detail] | @tsv' "${report}" | sort -u)

resolved=$(mktemp)
rc=0
trap 'rc=$?; rm -f "${policy}" "${report}" "${resolved}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
if [[ -f go.mod ]]; then
  # A module with no toolchain to resolve it is a loud failure, never a
  # skip: skipped, every Go finding would compare against nothing and
  # the answer below would be "main remedies nothing" about a tree
  # nobody looked at (found by running this against stele's main).
  command -v go > /dev/null || {
    echo "security-release: go.mod present but go missing — pin go in mise.toml" >&2
    exit 1
  }
  # Module paths lowercased: the finding's package is in purl-canonical
  # form, which for golang is lowercase (stele docs/vex-join.md).
  go list -m -f '{{if .Replace}}{{.Replace.Path}}@{{.Replace.Version}}{{else}}{{.Path}}@{{.Version}}{{end}}' all \
    | tr '[:upper:]' '[:lower:]' | sed 's/^/Go /' >> "${resolved}"
  toolchain=$(go env GOVERSION)
  echo "Go stdlib@v${toolchain#go}" >> "${resolved}"
fi
if [[ -f Cargo.lock ]]; then
  awk '
    $1 == "name" { n = $3 }
    $1 == "version" && n != "" {
      gsub(/"/, "", n)
      gsub(/"/, "", $3)
      print "crates.io " n "@" $3
      n = ""
    }' Cargo.lock >> "${resolved}"
fi
if [[ -f package-lock.json ]]; then
  jq -r '.packages // {} | to_entries[] | select(.key != "" and .value.version != null)
    | "npm " + (.key | sub("^.*node_modules/"; "")) + "@" + .value.version' package-lock.json >> "${resolved}"
fi

remedied=""
standing=""
compared=0
while IFS=$'\t' read -r assertion detail; do
  [[ -n ${assertion} ]] || continue
  # "<advisory>:<package>@<version>" and "... (<ecosystem>)".
  package=${assertion#*:}
  ecosystem=$(sed -E 's/^.*\(([^()]*)\)$/\1/' <<< "${detail}")
  ecosystem=${ecosystem%%:*}
  if ! grep -q "^${ecosystem} " "${resolved}"; then
    # Nothing here resolves that ecosystem — an OS layer, say. It is
    # neither proof of a remedy nor a reason to refuse one.
    continue
  fi
  compared=$((compared + 1))
  if grep -qxF "${ecosystem} ${package}" "${resolved}"; then
    standing+="${assertion}"$'\n'
  else
    remedied+="${assertion}"$'\n'
  fi
done <<< "${findings}"

# Nothing comparable is not "nothing remedied". An image whose only
# findings are in its OS layer is remedied by the rebuild a release IS,
# and this tree cannot say whether the base has moved — so the release
# is not refused on it, and the publish path's own checks below decide.
if [[ ${compared} -gt 0 && -z ${remedied} ]]; then
  needs_human \
    "main still resolves every affected package of ${supported} at its affected version, so a release would remedy nothing" \
    "${standing%$'\n'}"
fi
if [[ -n ${remedied} ]]; then
  echo "security-release: releasing would remedy:"
  remedied=${remedied%$'\n'}
  echo "  ${remedied//$'\n'/$'\n'  }"
else
  echo "security-release: no finding of ${supported} is in an ecosystem this tree resolves — a rebuild is the remedy"
fi

# 5. The publish path's own advisory checks, on the tree being released.
for task in audit:deny audit:go-vulns; do
  if ! out=$(mise run "${task}" 2>&1); then
    last=$(tail -n 20 <<< "${out}")
    needs_human \
      "main would refuse its own publish: ${task} fails on the tree being released" \
      "${last}"
  fi
done

echo "security-release: yes — merge Release PR #${number} (${subject})"
emit "merge=true"
emit "pr=${number}"
emit "reason=${supported} is affected and main remedies it"
org_done=1

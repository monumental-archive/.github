#!/usr/bin/env bash
# Every README shield in the org re-checked against its issuer. Org
# canon: the body of `audit:badges`, extracted (#1633, under #1544) so
# each issuer read has the room to tell an absence from an outage.
# The task keeps the canon-only guard and calls this; it runs in the
# canon's checkout with GITHUB_REPOSITORY_OWNER and the belt's env.
#
# A badge is a fact computed by somebody else's service, earned once and
# then trusted forever — exactly the claim shape this org refuses
# everywhere else (#215; audit:attestations is the pattern). This walks
# every org repo's README and, for every shield found, asks the issuer
# whether it still agrees. Loud by construction: an unrecognised shield
# fails (a badge nobody can check is a badge nobody is checking), an
# issuer returning nothing fails (a check that reads nothing must never
# report clean), and a Scorecard shield with no floor line fails. The
# Scorecard leg is a per-repo ratcheted floor (security/
# scorecard-floors.txt) — regression is the signal, never the absolute.
#
# Presence is asserted through the derivation, not re-derived here
# (#316 finding 5): each repo's badge block must be fix:badges' render
# (the markers prove it; lint:badges in that repo's own gate proves the
# render matches its tree), so a shield this walk does not see is one
# the derivation said does not apply — never one nobody looked for.
# .badge-states' declared lines (pending a human step / exemplary, no
# data by construction) are reported by name, so a Monday summary reads
# as state, not as expected-red noise (dependency-track.md:46).
set -euo pipefail

# The ratcheted floors are this leg's own data, and the task's guard no
# longer stands in for them (#738): a Scorecard shield judged against a
# floor file that is not there is a badge nobody is checking.
floors="security/scorecard-floors.txt"
[[ -f ${floors} ]] || {
  echo "audit:badges: on the canon, and the input this leg reads is missing: ${floors}" >&2
  echo "  a canon-only audit reds on a missing input, never skips (#738)." >&2
  exit 1
}
command -v gh > /dev/null || {
  echo "audit:badges: gh missing" >&2
  exit 1
}
: "${GITHUB_REPOSITORY_OWNER:?audit:badges walks an org the environment names (#579) — Actions sets it; a local run exports it}"
# The shield endpoints are identity, not scope (#579): a shield is only
# checkable when it points at this org's own projects on each issuer,
# and fix:badges renders the same URLs — the declaration is what both
# legs agree a shield is.
# shellcheck source=mise/identity.sh
. "${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}/identity.sh"
# ONE way to ask an issuer (#1161). Every leg that used `curl -sf`
# folded three different answers into one: the issuer said no, the
# issuer refused to talk, the issuer was down. All three were reported
# as the first — crates.io refuses an unidentified client with a 403
# before it looks anything up (#907), so edtf-core, published since
# July, was reported "not on crates.io" on the first run that reached
# the leg. `ask` identifies the client and keeps the status, so a leg
# says "absent" only on a 404; `unanswered` names everything else as a
# question nobody answered — still red, never an accusation. Since
# #1633 it is the only `curl` in this file, which test_audit_badges.py
# holds: Scorecard (#1621), Best Practices and the level shield had
# kept a bare read each.
ua="${GITHUB_REPOSITORY_OWNER}/.github audit:badges (https://github.com/${GITHUB_REPOSITORY_OWNER}/.github)"
ask_code=""
ask_body=""
ask_stale=""
ask_old=""
# A cached answer past its own lifetime (#1649). The Scorecard API
# served stele a 404 that had sat in its cache for twelve hours against
# `max-age=600` while the API held stele's result, and no request header
# asked past it. So `ask` keeps the headers and sets `ask_stale` to the
# seconds a response is past its lifetime — `Age` minus `s-maxage`, else
# `max-age`, when both are numbers and the difference is positive; empty
# otherwise (no header, no claim). A header block per attempt or hop
# starts at its status line, so the last block is the one read.
#
# What staleness costs depends on what was answered. An ABSENCE (a 404,
# or a 200 with nothing a leg can read) has to be the issuer's current
# word, because nothing in it says when it was true: stale, it is a
# question unanswered. A PRESENCE carries its own evidence, and is judged
# on it whatever its cache age — the first audit after #1649 found seven
# of eight Scorecard answers past `max-age=600`, six of them 200s holding
# a dated result. One bound holds for both: an answer older than this
# audit's own period is not this audit's answer.
#
# `hear <what the leg read from the answer>` is the one reading of
# that rule, and sets `heard`: `unanswered`, `absent` (a 404, or a 200
# with nothing read), or the HTTP status. Every leg reads `heard`, an
# `if` leg before judging, a `case` leg as what it switches on. A
# `case` leg whose evidence is the status itself passes the status.
#
# The audit runs weekly (badges.yml, Monday 07:00), so `Age` beyond a
# week is older than the run before this one.
audit_week=604800
ask_headers=$(mktemp)
rc=0
trap 'rc=$?; rm -f "${ask_headers}"; [[ -n ${org_done:-} || ${rc} -ne 0 ]] || rc=1; exit "${rc}"' EXIT
ask() {
  local out
  # Retried, because a single dropped connection is not an answer
  # either: doi.org reset two of three lookups in one run and served
  # all three a second later (measured 2026-11-14).
  : > "${ask_headers}"
  out=$(curl -s --retry 3 --retry-all-errors --retry-delay 2 --max-time 30 \
    -H "User-Agent: ${ua}" -D "${ask_headers}" -w '\n%{http_code}' "$@" || true)
  ask_code="${out##*$'\n'}"
  ask_body="${out%$'\n'*}"
  local cached
  cached=$(awk '
    { sub(/\r$/, ""); h = tolower($0) }
    h ~ /^http\// { age = ""; life = "" }
    h ~ /^age: *[0-9]+ *$/ { age = h; sub(/^age: */, "", age) }
    h ~ /^cache-control:/ {
      max = ""; shared = ""
      if (match(h, /s-maxage=[0-9]+/)) shared = substr(h, RSTART + 9, RLENGTH - 9)
      if (match(h, /(^|[ ,:])max-age=[0-9]+/)) { max = substr(h, RSTART, RLENGTH); sub(/.*=/, "", max) }
      life = (shared != "" ? shared : max)
    }
    END {
      stale = (age != "" && life != "" && age - life > 0) ? age - life : ""
      print stale "|" age
    }
  ' "${ask_headers}")
  ask_stale="${cached%%|*}"
  ask_old="${cached#*|}"
  [[ -n ${ask_old} && ${ask_old} -gt ${audit_week} ]] || ask_old=""
}
heard=""
hear() { # usage: hear <what the leg read from the answer>; sets heard
  heard="${ask_code}"
  [[ ${ask_code} != 404 && (${ask_code} != 200 || -n ${1}) ]] || heard="absent"
  [[ ${heard} != absent || -z ${ask_stale} ]] || heard="unanswered"
  [[ -z ${ask_old} ]] || heard="unanswered"
}
unanswered() {
  local why="${ask_stale:+, answered from a cache ${ask_stale} seconds past its lifetime}"
  [[ -z ${ask_old} ]] || why=", answered from a cache ${ask_old} seconds old, older than the audit's week"
  failed="${failed} ${repo}(${1}: ${2} did not answer — HTTP ${ask_code:-none}${why}"
  failed+=", a question unanswered, not an absence)"
}
sc_prefix=$(org_identity shield-scorecard)
wf_prefix=$(org_identity shield-actions)
ghcr_prefix=$(org_identity shield-ghcr)
cc_prefix=$(org_identity shield-codecov)
cc_api=$(org_identity shield-codecov-api)
repos=$(gh api "orgs/${GITHUB_REPOSITORY_OWNER}/repos" --paginate --jq '.[].name')
failed=""
checked=0
note() {
  echo "audit:badges: ${1}"
  [[ -n ${GITHUB_STEP_SUMMARY:-} ]] && echo "- ${1}" >> "${GITHUB_STEP_SUMMARY}" || true
}
while read -r repo; do
  [[ -n ${repo} ]] || continue
  readme=$(gh api "repos/${GITHUB_REPOSITORY_OWNER}/${repo}/readme" --jq '.content' 2> /dev/null || true)
  if [[ -z ${readme} ]]; then
    note "${repo}: no README, nothing asserted"
    continue
  fi
  body=$(base64 -d <<< "${readme}")
  # Adoption is asserted, not assumed (#316 finding 5): the block must
  # be fix:badges' render — the repo's own gate (lint:badges) proves
  # the render matches its tree, so this audit never re-derives; it
  # only needs the markers to exist to know a missing shield means
  # "the derivation said not applicable" rather than "nobody looked".
  if ! grep -q "<!-- badges:begin -->" <<< "${body}"; then
    failed="${failed} ${repo}(badge block not machine-rendered — no badges:begin marker;"
    failed+=" presence-blindness is only closed where fix:badges owns the block)"
    continue
  fi
  # Declared states: the debt lines the render could not derive.
  states=$(gh api "repos/${GITHUB_REPOSITORY_OWNER}/${repo}/contents/.badge-states" --jq '.content' 2> /dev/null \
    | base64 -d 2> /dev/null | grep -v "^#" || true)
  while read -r sk sv; do
    [[ -n ${sk} ]] || continue
    case "${sv}" in
      pending) note "${repo}: ${sk} pending a human step (declared)" ;;
      exemplary) note "${repo}: ${sk} exemplary, no data by construction (declared)" ;;
      # Any other value is not one of the two declared states, so it
      # claims nothing and the shield walk below judges the badge.
      *) ;;
    esac
  done <<< "${states}"
  # Shields are markdown images; comments are stripped first so a
  # deferred (commented-out) badge line is not audited as live. The
  # strip must handle a comment that opens and closes on ONE line —
  # every marker and every pending line is exactly that shape, and a
  # sed range address cannot end on the line it started on, so the
  # first `<!-- badges:begin -->` used to swallow every shield down to
  # the next `-->`: the walk read 25 live shields org-wide as zero and
  # reported clean. Inline comments are cut in place; only a genuinely
  # unterminated one opens the multi-line state.
  urls=$(awk '{
    while (match($0, /<!--.*-->/)) { $0 = substr($0, 1, RSTART - 1) substr($0, RSTART + RLENGTH) }
    if (inc) { if (sub(/.*-->/, "")) inc = 0; else next }
    if (sub(/<!--.*/, "")) inc = 1
    print
  }' <<< "${body}" | grep -oE '!\[[^]]*\]\([^)]+\)' | sed -E 's/^!\[[^]]*\]\(([^) ]+).*\)$/\1/' || true)
  if [[ -z ${urls} ]]; then
    # fix:badges renders the three SLSA shields unconditionally, so a
    # rendered block can never be shield-free: empty means the block
    # was truncated or the strip ate it, never "nothing applies".
    failed="${failed} ${repo}(badge block rendered no shields"
    failed+=" — the derivation always emits the SLSA row, so this block is truncated or unparsable)"
    continue
  fi
  # The repository wearing these shields, in the form derive-badges.sh
  # reads from REUSE.toml: an issuer's entry must name it (#1197).
  wearer="https://github.com/${GITHUB_REPOSITORY_OWNER}/${repo}"
  while read -r url; do
    [[ -n ${url} ]] || continue
    checked=$((checked + 1))
    case "${url}" in
      "${sc_prefix}"/*/badge)
        # Asked as every issuer is (#1621): the bare `curl -sf | jq` this
        # replaced died under pipefail on the 404 Scorecard answers for a
        # repository it holds nothing on, one line above the finding
        # written for it, and took every repository after it unchecked.
        # The shield is the project's own API path with `/badge` added.
        ask "${url%/badge}"
        score=$(jq -r '.score // empty' <<< "${ask_body}" 2> /dev/null || true)
        # The result dates itself; printed, never judged here (#1649).
        sc_date=$(jq -r '.date // empty' <<< "${ask_body}" 2> /dev/null || true)
        sc_date="${sc_date:+result of ${sc_date%%T*}}"
        floor=$(awk -v r="${repo}" '$1 == r {print $2}' "${floors}")
        hear "${score}"
        if [[ ${heard} == absent ]]; then
          failed="${failed} ${repo}(scorecard: issuer returned no score — a shield over nothing)"
        elif [[ ${heard} != 200 ]]; then
          unanswered scorecard "api.scorecard.dev, about ${repo},"
        elif [[ -z ${floor} ]]; then
          failed="${failed} ${repo}(scorecard: no floor in ${floors} — an unfloored badge is unwatched)"
        elif awk -v s="${score}" -v f="${floor}" 'BEGIN {exit !(s < f)}'; then
          failed="${failed} ${repo}(scorecard: ${score} below floor ${floor} — regression)"
        elif awk -v s="${score}" -v f="${floor}" 'BEGIN {exit !(s >= f + 0.3)}'; then
          note "${repo}: scorecard ${score} well above floor ${floor} — ratchet: raise the floor${sc_date:+ (${sc_date})}"
        else
          note "${repo}: scorecard ${score} (floor ${floor}${sc_date:+, ${sc_date}})"
        fi
        ;;
      https://www.bestpractices.dev/projects/*/badge | https://bestpractices.dev/projects/*/badge)
        id="${url##*/projects/}"
        id="${id%/badge}"
        ask "https://www.bestpractices.dev/projects/${id}.json"
        bp="${ask_body}"
        hear "${bp}"
        if [[ ${heard} == absent ]]; then
          failed="${failed} ${repo}(bestpractices: issuer returned nothing for project ${id})"
        elif [[ ${heard} != 200 ]]; then
          unanswered bestpractices "bestpractices.dev, about project ${id},"
        elif ! bp_owner=$("${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" "${ORG_BELT_DIR}/bestpractices_owner.py" \
          "${wearer}" <<< "${bp}"); then
          failed="${failed} ${repo}(bestpractices: ${bp_owner})"
        elif ! jq -e '(.badge_percentage_0 // 0) >= 100' <<< "${bp}" > /dev/null; then
          failed="${failed} ${repo}(bestpractices: passing percentage below 100)"
        elif jq -e '[.lost_baseline_1_at, .lost_baseline_2_at, .lost_baseline_3_at] | any(. != null)' \
          <<< "${bp}" > /dev/null 2>&1; then
          failed="${failed} ${repo}(bestpractices: issuer records a LOST baseline)"
        else
          note "${repo}: bestpractices ${id} passing"
        fi
        ;;
      https://www.bestpractices.dev/projects/*/baseline | https://bestpractices.dev/projects/*/baseline)
        # The OSPS Baseline shield on the same entry. Its own truth is
        # the achieved/lost pair, not the metal percentage: a shield
        # claiming a level the issuer never recorded, or one it has
        # since withdrawn, is the drift #168 watches for.
        id="${url##*/projects/}"
        id="${id%/baseline}"
        ask "https://www.bestpractices.dev/projects/${id}.json"
        bp="${ask_body}"
        hear "${bp}"
        if [[ ${heard} == absent ]]; then
          failed="${failed} ${repo}(baseline: issuer returned nothing for project ${id})"
        elif [[ ${heard} != 200 ]]; then
          unanswered baseline "bestpractices.dev, about project ${id},"
        elif ! bp_owner=$("${ORG_PYTHON:?${ORG_PYTHON_UNSET}}" "${ORG_BELT_DIR}/bestpractices_owner.py" \
          "${wearer}" <<< "${bp}"); then
          failed="${failed} ${repo}(baseline: ${bp_owner})"
        elif ! jq -e '[.achieved_baseline_1_at, .achieved_baseline_2_at, .achieved_baseline_3_at] | any(. != null)' \
          <<< "${bp}" > /dev/null 2>&1; then
          failed="${failed} ${repo}(baseline: shield worn but the issuer records no achieved level)"
        elif jq -e '[.lost_baseline_1_at, .lost_baseline_2_at, .lost_baseline_3_at] | any(. != null)' \
          <<< "${bp}" > /dev/null 2>&1; then
          failed="${failed} ${repo}(baseline: issuer records a LOST level)"
        else
          lvl=$(jq -r '[.achieved_baseline_3_at, .achieved_baseline_2_at, .achieved_baseline_1_at] | map(. != null)
            | index(true) | if . == 0 then 3 elif . == 1 then 2 else 1 end' <<< "${bp}")
          note "${repo}: OSPS Baseline level ${lvl} achieved"
        fi
        ;;
      https://api.reuse.software/badge/*)
        proj="${url#https://api.reuse.software/badge/}"
        ask "https://api.reuse.software/status/${proj}"
        st=$(jq -r '.status // empty' <<< "${ask_body}" 2> /dev/null || true)
        hear "${ask_code}"
        case "${heard}" in
          200)
            if [[ ${st} != "compliant" ]]; then
              failed="${failed} ${repo}(reuse: status '${st:-none}' is not compliant)"
            else
              note "${repo}: reuse compliant"
            fi
            ;;
          absent) failed="${failed} ${repo}(reuse: ${proj} is not registered at api.reuse.software)" ;;
          *) unanswered reuse "api.reuse.software" ;;
        esac
        ;;
      https://img.shields.io/endpoint?url=*)
        # A measured claim (#540, stele#5's endpoint addendum): the
        # badge is a render of the judgment `stele level` published,
        # so there is no value here to cross-check — the only thing
        # this leg can honestly ask is whether the published document
        # is live and shield-shaped. Level truth belongs to the
        # repository's own `level` stub, which publishes it, and to
        # the canon's `level-assert.yml`, which reds when a published
        # board disagrees with the org measurement (#554); a stale or
        # missing document is the finding here, never a number
        # disagreeing with a table.
        doc="${url#https://img.shields.io/endpoint?url=}"
        doc="${doc//%3A/:}"
        doc="${doc//%2F//}"
        ask "${doc}"
        sj="${ask_body}"
        hear "${sj}"
        if [[ ${heard} == absent ]]; then
          failed="${failed} ${repo}(level-shield: published judgment ${doc} unreadable — the badge renders nothing)"
        elif [[ ${heard} != 200 ]]; then
          unanswered level-shield "${doc}"
        elif ! jq -e '.schemaVersion == 1 and .label and .message' > /dev/null 2>&1 <<< "${sj}"; then
          failed="${failed} ${repo}(level-shield: ${doc} is not a shields endpoint document)"
        else
          shield=$(jq -r '.label + " " + .message' <<< "${sj}")
          note "${repo}: level shield ${shield} published and live"
        fi
        ;;
      "${wf_prefix}"/*/actions/workflows/*/badge.svg)
        wfp="${url#"${wf_prefix}"/}"
        wfr="${wfp%%/*}"
        wfn="${wfp##*/workflows/}"
        wfn="${wfn%/badge.svg}"
        # A badge.svg with no ?branch= renders the DEFAULT branch's
        # status, so the leg must ask the same question: an unfiltered
        # run query answers "newest run on any branch", which makes a
        # red PR-branch run read as a lying shield while the shield is
        # honestly green (observed on signer during #354, latent since
        # #322). Default branch read from the API, never assumed main.
        wfb=$(gh api "repos/${GITHUB_REPOSITORY_OWNER}/${wfr}" --jq '.default_branch // empty' 2> /dev/null || true)
        if [[ -z ${wfb} ]]; then
          failed="${failed} ${repo}(workflow-shield: could not read ${wfr}'s default branch)"
          continue
        fi
        conc=$(gh api \
          "repos/${GITHUB_REPOSITORY_OWNER}/${wfr}/actions/workflows/${wfn}/runs?per_page=1&status=completed&branch=${wfb}" \
          --jq '.workflow_runs[0].conclusion // empty' 2> /dev/null || true)
        if [[ -z ${conc} ]]; then
          failed="${failed} ${repo}(workflow-shield: ${wfn} has no completed runs on ${wfb} — a status badge over nothing)"
        elif [[ ${conc} != "success" ]]; then
          failed="${failed} ${repo}(workflow-shield: ${wfn} latest completed run on ${wfb} is ${conc})"
        else
          note "${repo}: workflow ${wfn} green on ${wfb}"
        fi
        ;;
      https://img.shields.io/badge/fair--software.eu-*)
        # Computed by fix:badges dot-by-dot; this leg re-checks the
        # three contingent dots against what the same README shows —
        # registry (dot 3: any registry shield), citation (dot 4:
        # CITATION.cff), checklist (dot 5: a live bestpractices
        # shield). A static 5/5 image fails here (#316 addendum E).
        dots="${url#*fair--software.eu-}"
        dots="${dots%-*}"
        e3="%E2%97%8B"
        grep -qE 'img.shields.io/(crates|npm)/|img.shields.io/badge/ghcr' <<< "${urls}" && e3="%E2%97%8F"
        e4="%E2%97%8B"
        gh api "repos/${GITHUB_REPOSITORY_OWNER}/${repo}/contents/CITATION.cff" > /dev/null 2>&1 && e4="%E2%97%8F"
        e5="%E2%97%8B"
        grep -q 'bestpractices.dev/projects/' <<< "${urls}" && e5="%E2%97%8F"
        expect="%E2%97%8F%20%E2%97%8F%20${e3}%20${e4}%20${e5}"
        if [[ ${dots} != "${expect}" ]]; then
          failed="${failed} ${repo}(fair-software: dots ${dots} disagree with the repo's own evidence ${expect})"
        else
          note "${repo}: fair-software dots agree with the evidence"
        fi
        ;;
      https://zenodo.org/badge/DOI/*)
        doi="${url#https://zenodo.org/badge/DOI/}"
        doi="${doi%.svg}"
        # A resolving DOI answers with a redirect to its record; HEAD,
        # and no -L, because the question is whether doi.org knows the
        # name, not whether the landing page is up.
        ask -I "https://doi.org/${doi}"
        hear "${ask_code}"
        case "${heard}" in
          2?? | 3??) note "${repo}: DOI resolves" ;;
          absent) failed="${failed} ${repo}(doi: ${doi} does not resolve)" ;;
          *) unanswered doi "doi.org, about ${doi}," ;;
        esac
        ;;
      https://img.shields.io/crates/v/* | https://img.shields.io/docsrs/*)
        crate="${url##*/}"
        crate="${crate%.svg}"
        ask "https://crates.io/api/v1/crates/${crate}"
        hear "${ask_code}"
        case "${heard}" in
          200) note "${repo}: crate ${crate} exists" ;;
          absent) failed="${failed} ${repo}(crates: ${crate} not on crates.io)" ;;
          *) unanswered crates "crates.io, about ${crate}," ;;
        esac
        ;;
      https://img.shields.io/npm/v/*)
        pkg="${url#https://img.shields.io/npm/v/}"
        pkg="${pkg%.svg}"
        ask "https://registry.npmjs.org/${pkg//%2F//}"
        hear "${ask_code}"
        case "${heard}" in
          200) note "${repo}: npm package exists" ;;
          absent) failed="${failed} ${repo}(npm: package not on the registry)" ;;
          *) unanswered npm "the npm registry" ;;
        esac
        ;;
      "${ghcr_prefix}"*)
        # The shield names an image; ask GitHub whether that package
        # exists. The old leg HEAD-requested the org packages page,
        # which returns 200 for ANY org that exists — a check that
        # reads nothing (#316 addendum C). The anonymous REST endpoint
        # 401s even for public containers, so this goes through gh;
        # the cron token carries packages: read for it.
        img="${url#"${ghcr_prefix}"}"
        img="${img%%-blue*}"
        img="${img//--/-}"
        if ! gh api "orgs/${GITHUB_REPOSITORY_OWNER}/packages/container/${img}" > /dev/null 2>&1; then
          failed="${failed} ${repo}(ghcr: package '${img}' does not exist in the org registry)"
        else
          note "${repo}: ghcr package ${img} exists"
        fi
        ;;
      "${cc_prefix}"/*/graph/badge.svg | "${cc_prefix}"/*/branch/*/graph/badge.svg)
        cc="${url#"${cc_prefix}"/}"
        # A badge URL that names a branch names it; otherwise the branch
        # is the forge's default (#1668), never the issuer's idea of it.
        ccb=""
        [[ ${cc} != */branch/* ]] || ccb="${cc#*/branch/}"
        ccb="${ccb%%/*}"
        cc="${cc%%/*}"
        # Knowing the repository is not having its coverage (#1161).
        # This leg used to pass on `.name` alone, which Codecov returns
        # for any repository its app can see — so edtf and iiif-server,
        # whose gate stubs never passed the upload token, wore a shield
        # reading "unknown" and were reported live.
        #
        # The question is the one the image answers: what does Codecov
        # hold for the HEAD of the branch. Not the repository's
        # `.totals` — measured on edtf's first upload, that stays null
        # while the branch head already carries 96.81 and the shield
        # already reads 97%. And the head Codecov knows must be the
        # head GitHub has: the upload job was skipped in stele for
        # days (an `if:` that a skipped pgrx leg turned false), the
        # shield kept rendering the last figure it was given, and a
        # check that only asked "is there a number" stayed green. An
        # hour's grace covers an upload still in flight.
        #
        # ONE request, the branch route (#1668): its `head_commit` and
        # that commit's coverage are all this leg judges. The
        # repository-detail route it used to ask first, only to learn
        # the branch, answers 404 for `.github` while the branch route
        # answers with main's head (measured 2026-11-19), so every run
        # called a live shield "no record".
        [[ -n ${ccb} ]] || ccb=$(gh api "repos/${GITHUB_REPOSITORY_OWNER}/${cc}" \
          --jq '.default_branch // empty' 2> /dev/null || true)
        if [[ -z ${ccb} ]]; then
          failed="${failed} ${repo}(codecov: could not read ${cc}'s default branch from GitHub)"
          continue
        fi
        gh_head=$(gh api "repos/${GITHUB_REPOSITORY_OWNER}/${cc}/commits/${ccb}" \
          --jq '[.sha, (.commit.committer.date | fromdate)] | @tsv' 2> /dev/null || true)
        gh_sha="${gh_head%%$'\t'*}"
        gh_at="${gh_head##*$'\t'}"
        now=$(date -u +%s)
        ask "${cc_api}/${cc}/branches/${ccb}"
        cov=$(jq -r '.head_commit.totals.coverage // empty' <<< "${ask_body}" 2> /dev/null || true)
        ccsha=$(jq -r '.head_commit.commitid // empty' <<< "${ask_body}" 2> /dev/null || true)
        hear "${cov}"
        if [[ ${heard} == absent && ${ask_code} == 404 ]]; then
          failed="${failed} ${repo}(codecov: issuer has no record of ${cc})"
        elif [[ ${heard} == absent ]]; then
          failed="${failed} ${repo}(codecov: no coverage report for ${cc}'s ${ccb}"
          failed+=" — the shield reads unknown; the gate stub must pass codecov-token)"
        elif [[ ${heard} != 200 ]]; then
          unanswered codecov "Codecov, about ${cc}'s ${ccb},"
        elif [[ -z ${gh_sha} ]]; then
          failed="${failed} ${repo}(codecov: could not read ${cc}'s ${ccb} head from GitHub)"
        elif [[ ${ccsha} != "${gh_sha}" && $((now - gh_at)) -gt 3600 ]]; then
          failed="${failed} ${repo}(codecov: stale — Codecov last heard about ${ccsha:0:7},"
          failed+=" ${ccb} is at ${gh_sha:0:7}; the upload job is not running)"
        else
          note "${repo}: codecov holds ${cov}% for ${cc} at ${ccsha:0:7}"
        fi
        ;;
      # The shields fix:badges renders from the tree alone (#1161):
      # release, licence, and the STATIC shields a repository declares
      # in .badge-states. No issuer computes them, so each is asked the
      # one question that can come back no.
      https://img.shields.io/github/v/release/*)
        rel="${url#https://img.shields.io/github/v/release/}"
        rel="${rel%%\?*}"
        if [[ ${rel} != "${GITHUB_REPOSITORY_OWNER}/${repo}" ]]; then
          failed="${failed} ${repo}(release: the shield names ${rel}, not this repository)"
        elif ! gh api "repos/${rel}/releases/latest" > /dev/null 2>&1; then
          failed="${failed} ${repo}(release: shield worn, no published release)"
        else
          note "${repo}: a published release stands behind the release shield"
        fi
        ;;
      https://img.shields.io/badge/license-* | https://img.shields.io/badge/licence-*)
        # The message is an SPDX expression in shields' escaping (`--`
        # is a hyphen, the last `-<colour>` is not part of it). Every
        # identifier in it must be a text under LICENSES/ — the REUSE
        # layout lint:licence already holds each repository to, so the
        # shield is checked against the tree and not against GitHub's
        # licence guess (which reads AGPL-3.0-only as AGPL-3.0).
        expr="${url#https://img.shields.io/badge/licen?e-}"
        expr="${expr%-*}"
        expr="${expr//--/-}"
        expr="${expr//%20/ }"
        expr="${expr//%28/ }"
        expr="${expr//%29/ }"
        expr="${expr//[()]/ }"
        lic_bad=""
        lic_n=0
        for id in ${expr}; do
          case "${id}" in OR | AND | WITH) continue ;; *) ;; esac
          lic_n=$((lic_n + 1))
          gh api "repos/${GITHUB_REPOSITORY_OWNER}/${repo}/contents/LICENSES/${id}.txt" > /dev/null 2>&1 \
            || lic_bad="${lic_bad} ${id}"
        done
        if [[ ${lic_n} -eq 0 ]]; then
          failed="${failed} ${repo}(licence: the shield names no SPDX identifier)"
        elif [[ -n ${lic_bad} ]]; then
          failed="${failed} ${repo}(licence: shield names${lic_bad}, and the tree carries no LICENSES/ text for it)"
        else
          note "${repo}: licence shield agrees with LICENSES/"
        fi
        ;;
      https://img.shields.io/badge/*)
        # Any other static shield is a sentence the repository wrote
        # about itself (`static <path> <label>=<message>` in
        # .badge-states), and no issuer exists to ask. What can be
        # asked is whether it names its enforcer: the shield LINKS the
        # file in the repository's own tree that makes the sentence
        # true, and that file must exist. fix:badges refuses to render
        # one whose file is untracked, so this is the same question
        # asked again from outside, on the default branch.
        target=$(awk -v u="](${url})](" '{
          i = index($0, u)
          if (i) { r = substr($0, i + length(u)); sub(/\).*/, "", r); print r; exit }
        }' <<< "${body}")
        target="${target%%#*}"
        target="${target#./}"
        if [[ -z ${target} ]]; then
          failed="${failed} ${repo}(static shield links to nothing"
          failed+=" — it must link the file in this repository that enforces it: ${url})"
        elif [[ ${target} == *://* ]]; then
          failed="${failed} ${repo}(static shield links outside the repository"
          failed+=" — it must link the file in this tree that enforces it: ${url})"
        elif ! gh api "repos/${GITHUB_REPOSITORY_OWNER}/${repo}/contents/${target}" > /dev/null 2>&1; then
          failed="${failed} ${repo}(static shield links ${target}, which is not in the tree: ${url})"
        else
          note "${repo}: static shield stands on ${target}"
        fi
        ;;
      *)
        # Not silently skipped, by design: silently skipping reproduces
        # the exact gap this audit closes.
        failed="${failed} ${repo}(unrecognised shield — nobody is checking it: ${url})"
        ;;
    esac
  done <<< "${urls}"
done <<< "${repos}"
if [[ -n ${failed} ]]; then
  echo "audit:badges: shield defects:${failed}" >&2
  echo "  every shield must agree with its source of truth; fix the fact, the floor, or the badge" >&2
  exit 1
fi
# The same law the legs enforce, applied to the walk itself: a check
# that read nothing must never report clean. The canon alone renders
# three SLSA shields, so zero here is always a broken walk, never a
# quiet org.
if ((checked == 0)); then
  echo "audit:badges: walked the org and checked zero shields — the walk is broken, not the org" >&2
  exit 1
fi
echo "audit:badges: ${checked} shield(s) checked, all agree with their issuers"
org_done=1

#!/usr/bin/env bash
# Off-CI dry run of the source emitter (#236), exercised by the canon's
# own gate (`lint:source-attest`).
#
# Runs the REAL scripts — claims, chain, emit, append — end-to-end short
# of signing, with no network and no runner: recorded rule fixtures
# replace the live rules API, a file-protocol stand-in replaces
# github.com, signing is skipped (no OIDC token exists here, and no
# Rekor entry may ever be minted for a run that cannot complete), and
# the final push is --dry-run. The gate stays deterministic.
#
# Global git config is replaced with testdata/gitconfig, whose
# `user.useConfigOnly` forbids git from auto-detecting an identity from
# the host — reproducing the runner's blank slate on any machine. An
# identity the scripts do not construct themselves is exactly the bug
# the genesis run died on (`fatal: empty ident name`), and removing the
# declaration from chain.sh makes this dry run fail (verified).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
canon_root="$(cd "${here}/../../.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
export GIT_CONFIG_GLOBAL="${here}/testdata/gitconfig" GIT_CONFIG_SYSTEM=/dev/null
unset GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GH_TOKEN || true

# A stand-in for the attested repo: one commit on main and the seeded
# notes ref (#202), served over the file protocol.
upstream="${tmp}/upstream.git"
git init -q --bare "${upstream}"
seed="${tmp}/seed"
git init -q -b main "${seed}"
git -C "${seed}" config user.name seed
git -C "${seed}" config user.email seed@invalid
echo canon > "${seed}/README.md"
git -C "${seed}" add README.md
git -C "${seed}" commit -qm "chore: seed"
git -C "${seed}" notes add -m "seeded (#202)" HEAD
git -C "${seed}" push -q "${upstream}" main "refs/notes/commits:refs/notes/commits"
rev="$(git -C "${seed}" rev-parse HEAD)"

export SA_WORK="${tmp}/work"
mkdir -p "${SA_WORK}"
export SA_CANON_ROOT="${canon_root}"
# A real commit, not a placeholder: emit.sh refuses a non-SHA canon ref
# (policy.digest needs the commit, #267), and the dry run must exercise
# the refusal's happy path with the same shape a live run sees.
SA_CANON_REF="$(git -C "${canon_root}" rev-parse HEAD)"
export SA_CANON_REF
export SA_IDENTITY="https://github.com/monumental-archive/.github/.github/workflows/source-attest.yml@refs/heads/main"
export SA_ISSUER="https://token.actions.githubusercontent.com"
export SA_RULES_FIXTURE_DIR="${here}/testdata"
export SA_REMOTE_URL="${upstream}"
export SA_SKIP_SIGN=true
export SA_PUSH_DRY_RUN=true
export SA_GENESIS=true
export GITHUB_REPOSITORY="monumental-archive/.github"
export GITHUB_SHA="${rev}"
export SA_ACTOR="dry-run"
export SA_ACTOR_ID="0"

# The read guards are code too (#240): an empty or unreadable tag read
# must refuse loudly, and a content lapse must drop exactly its own
# properties. Both degraded fixture sets are DERIVED here from the one
# canonical testdata — jq transforms, never second copies that drift.
fail() {
  echo "lint:source-attest: ${1}" >&2
  exit 1
}

blind="${tmp}/fixtures-blind"
mkdir -p "${blind}"
cp "${here}/testdata/branch-rules.json" "${here}/testdata/branch-ruleset-details.json" "${blind}/"
jq '[]' "${here}/testdata/tag-rulesets.json" > "${blind}/tag-rulesets.json"
guard_work="${tmp}/work-blind"
mkdir -p "${guard_work}"
if out=$(SA_WORK="${guard_work}" SA_RULES_FIXTURE_DIR="${blind}" "${here}/claims.sh" 2>&1); then
  fail "claims.sh accepted a tag read that saw no rulesets — the blind-read guard is gone (#240)"
fi
grep -q "refusing to claim from a blind read" <<< "${out}" \
  || fail "claims.sh refused the blind tag read but without its named error (#240)"

lapsed="${tmp}/fixtures-lapsed"
mkdir -p "${lapsed}"
cp "${here}/testdata/branch-rules.json" "${here}/testdata/branch-ruleset-details.json" "${lapsed}/"
# The lapse: someone grants a bypass on the all-tags ruleset. The
# ruleset is still visible and readable — only its content no longer
# matches, so exactly ORG_SOURCE_TAG_IMMUTABLE must drop.
jq '(.[] | select(.conditions.ref_name.include == ["~ALL"]) | .bypass_actors)
  += [{actor_id: 1, actor_type: "OrganizationAdmin", bypass_mode: "always"}]' \
  "${here}/testdata/tag-rulesets.json" > "${lapsed}/tag-rulesets.json"
lapse_work="${tmp}/work-lapsed"
mkdir -p "${lapse_work}"
SA_WORK="${lapse_work}" SA_RULES_FIXTURE_DIR="${lapsed}" "${here}/claims.sh" \
  || fail "claims.sh refused a readable-but-lapsed ruleset — a lapse must under-claim, not fail (#240)"
jq -e '[.controls[].property] | (index("ORG_SOURCE_TAG_IMMUTABLE") == null)
  and (index("ORG_SOURCE_RELEASE_TAG_MINTED") != null)' \
  "${lapse_work}/claims.json" > /dev/null \
  || fail "a lapsed tag ruleset did not drop exactly its own property (#240)"

"${here}/claims.sh"
"${here}/chain.sh"
"${here}/emit.sh"
"${here}/append.sh"
link="${SA_WORK}/links/${rev}"

# Shape validation against the spec's required fields — the statements a
# live run would sign, checked field by field, plus the level the full
# fixture set must reach.
jq -e --arg rev "${rev}" '
  ._type == "https://in-toto.io/Statement/v1"
  and .subject[0].digest.gitCommit == $rev
  and .subject[0].annotations.sourceRefs == ["refs/heads/main"]
  and .predicateType == "https://monumental-archive.github.io/attestations/source-provenance/v1"
  and (.predicate | .repository and .ref == "refs/heads/main"
    and (.parents | type == "array")
    and .actor.login and .commitTime and .rulesReadAt
    and (.controls | type == "array") and .canonRef
    and has("ledgerPrev") and has("revisionParent"))
' "${link}/provenance.json" > /dev/null || fail "provenance statement shape invalid"
jq -e --arg rev "${rev}" --arg id "${SA_IDENTITY}" --arg canon "${SA_CANON_REF}" '
  ._type == "https://in-toto.io/Statement/v1"
  and .subject[0].digest.gitCommit == $rev
  and (.subject[0].uri | endswith("/commit/" + $rev))
  and .predicateType == "https://slsa.dev/verification_summary/v1"
  and (.predicate | .verifier.id == $id and .timeVerified
    and .resourceUri and .policy.uri
    and .policy.digest.gitCommit == $canon
    and .slsaVersion == "1.2"
    and .verificationResult == "PASSED"
    and .verifiedLevels[0] == "SLSA_SOURCE_LEVEL_3")
' "${link}/vsa.json" > /dev/null \
  || fail "VSA shape invalid (the #267 SHOULDs are asserted too), or the full fixture set does not reach SLSA_SOURCE_LEVEL_3"
jq -e '.version == 2 and .provenance.statement and .provenance.bundle and .vsa.statement and .vsa.bundle' \
  "${link}/note.json" > /dev/null || fail "chain-link note shape invalid"

# ── Auto-heal (#265): a hole left by a lapsed run is healed by the next
# push, honestly. The genesis link is landed on the stand-in remote for
# real (file protocol, no auth), two commits advance main with no
# emitter run between them, and the next "push" must emit links for
# both — the hole marked repaired, the fresh one not.
git -C "${SA_WORK}/repo" push -q origin refs/notes/commits:refs/notes/commits
echo two > "${seed}/two.txt"
git -C "${seed}" add two.txt
git -C "${seed}" commit -qm "chore: two"
rev2="$(git -C "${seed}" rev-parse HEAD)"
echo three > "${seed}/three.txt"
git -C "${seed}" add three.txt
git -C "${seed}" commit -qm "chore: three"
rev3="$(git -C "${seed}" rev-parse HEAD)"
git -C "${seed}" push -q "${upstream}" main

heal_work="${tmp}/work-heal"
mkdir -p "${heal_work}"
# shellcheck disable=SC2030,SC2031  # the subshell IS the isolation: each
# scenario runs with its own SA_WORK and GITHUB_SHA and must not leak them
# into the next one. Locality is the point, not an accident.
(
  export SA_WORK="${heal_work}" GITHUB_SHA="${rev3}"
  SA_GENESIS=false "${here}/claims.sh"
  SA_GENESIS=false "${here}/chain.sh"
  # emit.sh runs WITHOUT SA_GENESIS, exactly as the action scoped it
  # before the fix that added it to the emit step — the first live heal
  # died on the unbound variable while this dry run masked it with a
  # global export. Never again: the default path is exercised here.
  unset SA_GENESIS
  "${here}/emit.sh"
  "${here}/append.sh"
)
# shellcheck disable=SC2312  # diagnostic output only; a failure here
# degrades a log line, it does not change what is written or decided.
[[ $(wc -l < "${heal_work}/manifest.tsv" | tr -d " ") == 2 ]] \
  || fail "the heal run did not emit exactly the hole and the pushed revision (#265)"
jq -e '.predicate.repaired.at' "${heal_work}/links/${rev2}/provenance.json" > /dev/null \
  || fail "the healed link does not carry the repaired marker (#265)"
jq -e '.predicate | has("repaired") | not' "${heal_work}/links/${rev3}/provenance.json" > /dev/null \
  || fail "the fresh link carries a repaired marker it must not have (#265)"
jq -e '.predicate.verifiedLevels[0] == "SLSA_SOURCE_LEVEL_3"' \
  "${heal_work}/links/${rev2}/vsa.json" > /dev/null \
  || fail "healed link with provable ruleset continuity did not reach the target level (#265)"
jq -e --arg p "${rev2}" '.predicate.ledgerPrev.revision == $p' \
  "${heal_work}/links/${rev3}/provenance.json" > /dev/null \
  || fail "the fresh link does not chain to the just-healed hole (#265)"
# The v2 split (#349 S3): after a heal the ledger pointer and the git
# parent AGREE here (the hole is the fresh link's parent), and both
# fields must say so independently.
jq -e --arg p "${rev2}" '.predicate.revisionParent == $p' \
  "${heal_work}/links/${rev3}/provenance.json" > /dev/null \
  || fail "the fresh link does not name its git first-parent in revisionParent (#349 S3)"

# ── The continuity guard under-claims when it cannot prove (#265): with
# a contributing ruleset changed AFTER the commits (a future updated_at
# in the fixture), healed links must claim level 2 while the fresh link
# still claims the target — the guard binds late emission, not fresh.
future="${tmp}/fixtures-future"
mkdir -p "${future}"
cp "${here}/testdata/branch-rules.json" "${future}/"
cp "${here}/testdata/tag-rulesets.json" "${future}/"
jq '[.[] | .updated_at = "2999-01-01T00:00:00Z"]' \
  "${here}/testdata/branch-ruleset-details.json" > "${future}/branch-ruleset-details.json"
echo four > "${seed}/four.txt"
git -C "${seed}" add four.txt
git -C "${seed}" commit -qm "chore: four"
rev4="$(git -C "${seed}" rev-parse HEAD)"
git -C "${seed}" push -q "${upstream}" main

guard2_work="${tmp}/work-future"
mkdir -p "${guard2_work}"
(
  # The stand-in remote never received the heal run's links (its push
  # was --dry-run), so this run heals rev2 and rev3 again — now with an
  # unprovable horizon.
  # shellcheck disable=SC2031  # deliberate: each scenario subshell owns its own state
  export SA_WORK="${guard2_work}" SA_GENESIS=false GITHUB_SHA="${rev4}" \
    SA_RULES_FIXTURE_DIR="${future}"
  "${here}/claims.sh"
  "${here}/chain.sh"
  "${here}/emit.sh"
  "${here}/append.sh"
)
jq -e '.predicate.verifiedLevels[0] == "SLSA_SOURCE_LEVEL_2"' \
  "${guard2_work}/links/${rev2}/vsa.json" > /dev/null \
  || fail "a healed link with unprovable ruleset continuity did not under-claim (#265)"
jq -e '.predicate.verifiedLevels[0] == "SLSA_SOURCE_LEVEL_3"' \
  "${guard2_work}/links/${rev4}/vsa.json" > /dev/null \
  || fail "the continuity guard wrongly bound a fresh link (#265)"

# ── Genesis stays refused on a founded history (#265): auto-heal must
# never become a quiet re-founding.
refound_work="${tmp}/work-refound"
mkdir -p "${refound_work}"
if out=$(SA_WORK="${refound_work}" SA_GENESIS=true GITHUB_SHA="${rev4}" "${here}/chain.sh" 2>&1); then
  fail "genesis was accepted on a history that already carries a link"
fi
grep -q "genesis refused" <<< "${out}" \
  || fail "genesis was refused but without its named error"

emsg="lint:source-attest: ok — read guards refuse blindness, a lapse under-claims, emitter dry run clean,"
emsg+=" holes heal with honest markers and computed levels, genesis stays refused, push negotiated"
echo "${emsg}"

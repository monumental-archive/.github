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
export SA_CANON_REF="dry-run"
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
cp "${here}/testdata/branch-rules.json" "${blind}/"
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
cp "${here}/testdata/branch-rules.json" "${lapsed}/"
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
    and has("prev"))
' "${SA_WORK}/provenance.json" > /dev/null || fail "provenance statement shape invalid"
jq -e --arg rev "${rev}" --arg id "${SA_IDENTITY}" '
  ._type == "https://in-toto.io/Statement/v1"
  and .subject[0].digest.gitCommit == $rev
  and .predicateType == "https://slsa.dev/verification_summary/v1"
  and (.predicate | .verifier.id == $id and .timeVerified
    and .resourceUri and .policy.uri
    and .verificationResult == "PASSED"
    and .verifiedLevels[0] == "SLSA_SOURCE_LEVEL_3")
' "${SA_WORK}/vsa.json" > /dev/null \
  || fail "VSA shape invalid, or the full fixture set does not reach SLSA_SOURCE_LEVEL_3"
jq -e '.version == 1 and .provenance.statement and .provenance.bundle and .vsa.statement and .vsa.bundle' \
  "${SA_WORK}/note.json" > /dev/null || fail "chain-link note shape invalid"

echo "lint:source-attest: ok — read guards refuse blindness, a lapse under-claims, emitter dry run clean, both statements shaped, note assembled, push negotiated"

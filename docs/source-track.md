# SLSA source track

The org's position against the SLSA v1.2 source track (published spec,
final): what is enforced, what is claimable, and what formal standing is
— stated honestly.

## Formal standing: Level 0, controls at Level 3 substance

The v1.2 source VSA is constitutive: "If the SCS DOES NOT generate a VSA
for a revision, the revision has Source Level 0." Nothing currently
emits source VSAs here, so the formal level is **0** regardless of
controls. The controls themselves are at L3 substance (below), all
enforced by org-level rulesets, with continuity clocks that derive from
GitHub's ruleset timestamps — they accrue now and survive any later
attestation stand-up.

The attestation machinery was stood up and proven in release-lab on
2026-08-10 (signed source provenance + VSA under the repo's own workflow
identity, stored in git notes, stranger-verified including an
`ORG_SOURCE_GATED` property), then **parked**: the engine,
`slsa-framework/source-tool`, is a proof-of-concept whose level
computation cannot yet serve an org-level-ruleset deployment under its
own signing identity. Four defects filed upstream: source-tool
issues 433 (org rulesets 404), 434 (dynamic required-check controls
dropped), 435 (genesis impossible on an empty notes ref), and 436
(chain evaluation ignores the configured signer identity). The genesis attestations remain
in release-lab's `refs/notes/commits` as the record.

## Requirement mapping (v1.2, L1–L3)

| Requirement | Level | Enforcement |
| --- | --- | --- |
| Choose an appropriate SCS | 1 | GitHub |
| Repository IDs / immutable revision IDs / human-readable diffs | 1 | GitHub (git SHAs, PR diffs) |
| Source VSAs | 1 | **Not emitted — parked** (the L0 gap) |
| Access control + reliable history | 2 | `org-default-branch` ruleset: deletion blocked, force push blocked, linear history, org-wide, empty bypass list |
| Tag immutability | 2 | `org-default-tag` ruleset: update/move/delete blocked, all tags, all repos |
| Safe expunging process | 2 | `docs/expunging.md` |
| Identity management | 2 | GitHub accounts, org 2FA required, DCO signoff + `required_signatures` attribute every change |
| Source provenance, contemporaneous | 2 | **Not emitted — parked** |
| Continuity | 2 | Ruleset timestamps (GitHub-recorded); a ruleset edit that weakens a control resets its clock — see `rulesets.md` |
| Continuous technical controls, documented | 3 | This document + `rulesets.md`; the control set: required `ci / ci` gate (bound to the Actions app), required signatures, linear history, review-thread resolution, squash-only merges, `v*` creation locked to the minting App, capability-boundary lint |
| Protected named references, org properties | 3 | Rulesets as above; `ORG_SOURCE_GATED` proven claimable in the lab pilot |

## L4: recorded headcount ceiling

Two-party review requires two trusted persons; GitHub will not let a
solo maintainer approve their own pull request. This is a decision
boundary, not an oversight. The spec's perpetual-exception allowance for
trusted robots means Renovate and the release App would not block L4 if
a second maintainer ever exists: flip
`required_approving_review_count` to 1+, and the clock for that control
starts at that revision.

## Re-adoption checklist

When upstream is stable on org-level rulesets (at minimum issues 433,
434 and 436 fixed in a release):

1. Verify the release binary against their SLSA provenance; no patches.
2. Per repo: copy the `source-attest` workflow (per-repo copy is
   deliberate — the workflow is the signer, and an in-repo workflow's
   identity is `@refs/heads/main`, stable forever; a SHA-pinned shared
   workflow changes identity on every bump, and an unpinned one costs
   Scorecard/zizmor findings — pick-two trilemma, documented in
   .github#120).
3. Seed `refs/notes/commits` if absent (until #435).
4. Restore `source-policies/` (in this repo's history at the park
   commit): shared `default.json`, per-repo override on divergence.
5. Genesis run, then move the policy `since` past it; L3 from the next
   revision.
6. Stranger-verify with `sourcetool verify --expected-san
   https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main`.

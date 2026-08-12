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
(chain evaluation ignores the configured signer identity). The genesis
attestations remain in release-lab's `refs/notes/commits` as the record.

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

## The claims contract: the `ORG_SOURCE_` set, named once

The v1.2 namespace for org-defined control claims in source VSAs. The
set is *all enforced controls*, enumerated from the live rulesets and
the belt (read 2026-08-12), not curated. **These names are a public
contract**: the moment a stranger's policy gates on one, a rename is a
breaking change. They are frozen here ahead of any emission so the
emitter, when it exists, implements this table rather than inventing
names.

| Property | Enforcement | Meaning |
| --- | --- | --- |
| `ORG_SOURCE_GATED` | `org-default-branch`: required check `ci / ci`, bound to the GitHub Actions app (integration 15368), strict policy | every revision passed the org gate — the full `lint:*` belt plus `test`/`build` — at the revision itself, not a stale merge base |
| `ORG_SOURCE_DCO` | `lint:dco` inside the gated check | every commit carries a DCO sign-off matching its author |
| `ORG_SOURCE_CAPABILITY_BOUNDARY` | `lint:capability-boundary` inside the gated check | no workflow declares `id-token: write` without an explicit boundary marker (the Build L3 signing boundary) |
| `ORG_SOURCE_HISTORY_PROTECTED` | `org-default-branch`: deletion blocked, non-fast-forward blocked, linear history required | default-branch history is append-only and ancestry-preserving |
| `ORG_SOURCE_SIGNED` | `org-default-branch`: `required_signatures` | every commit reaching the default branch has a signature GitHub verifies |
| `ORG_SOURCE_REVIEWED_THREADS` | `org-default-branch`: pull-request-required, review-thread resolution required, squash-only merges | every change lands via a pull request with all review threads resolved; the PR title/body is the permanent commit |
| `ORG_SOURCE_TAG_IMMUTABLE` | `org-default-tag`: update, move and deletion blocked, all tags, all repos | a tag, once created, points at its revision forever |
| `ORG_SOURCE_RELEASE_TAG_MINTED` | `org-release-tag`: `v*` creation restricted to the release App (integration 4534781) | every release tag was minted by the release pipeline, no human hands |

Nothing currently warrants `ORG_SOURCE_INTERNAL_`; the namespace is
reserved and its use documented here if that changes.

## Continuity ledger

The spec tracks each control's continuity from a specific start
revision; a lapse resets that control's clock from the next revision.
This ledger is append-only. Timestamps are GitHub's own (ruleset
`created_at`/`updated_at`, read from the API 2026-08-12); start
revisions are the first commit on each repo's default branch after the
boundary.

**Boundary A — 2026-08-09T16:29:06+01:00** (org rulesets created:
`org-default-branch`, `org-default-tag`, `org-release-tag`). Starts the
clocks for every property above. The belt-enforced properties
(`ORG_SOURCE_DCO`, `ORG_SOURCE_CAPABILITY_BOUNDARY`) also start here:
both lints predate the rulesets (PRs #41, #42), so the binding event —
the gate becoming required org-wide — governs.

| Repo | Start revision |
| --- | --- |
| `.github` | `84515e6` |
| `signer` | `40ada01` |
| `release-lab` | `80f9a7f` |

**Boundary B — 2026-08-10T21:41:46+01:00** (`org-default-branch`
hardened: `ci / ci` bound to integration 15368, merge methods narrowed
to squash-only). Strengthens `ORG_SOURCE_GATED` and
`ORG_SOURCE_REVIEWED_THREADS`; their clocks in the strengthened form
start here. All other clocks continue from boundary A.

| Repo | Start revision |
| --- | --- |
| `.github` | `b4c3f08` |
| `signer` | `b05fe88` (2026-08-12 — the identity-stub landing, first main commit after the boundary) |
| `release-lab` | `2281c5e` |

## The signing identity, reserved

The future source-signing identity is the per-repo workflow
`.github/workflows/source-attest.yml` at `@refs/heads/main` — the
certificate identity is the workflow path plus ref, so the path is the
contract. An inert stub now holds that path in every org repo
(`workflow-templates/source-attest.yml`, copied per repo like the
scorecard stub). **Renaming or moving the file is a breaking change to
the root-of-trust contract** published in `source-assessment.md`;
content changes freely, the path never. The stub carries no `id-token`
and no triggers beyond `workflow_dispatch` while inert — trigger and
body are content, not identity.

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
434 and 436 fixed in a release — the trigger is standing watch #199).
The pre-work landed 2026-08-12 shrank this list: the identity path is
reserved by the inert stub in every repo, `refs/notes/commits` is
seeded everywhere, the `ORG_SOURCE_` names and continuity ledger are
frozen above, and the self-assessment (`source-assessment.md`) is
published. Re-adoption is *activation*:

1. Verify the release binary against their SLSA provenance; no patches.
2. Restore `source-policies/` (in this repo's history at the park
   commit): shared `default.json`, per-repo override on divergence.
   Claims implement the `ORG_SOURCE_` table above, verbatim.
3. Activate the stub per repo: trigger becomes on-push to protected
   refs, body invokes the tool, `id-token: write` enters with the
   capability-boundary marker. The path does not change (the per-repo
   copy is deliberate — the workflow is the signer, and an in-repo
   workflow's identity is `@refs/heads/main`, stable forever; a
   SHA-pinned shared workflow changes identity on every bump, and an
   unpinned one costs Scorecard/zizmor findings — pick-two trilemma,
   documented in .github#120).
4. Genesis run, then move the policy `since` past it; L3 from the next
   revision, continuity claims from the ledger above.
5. Stranger-verify with `sourcetool verify --expected-san
   https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main`.

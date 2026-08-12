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

The attestation machinery was first stood up and proven in release-lab
on 2026-08-10 with `slsa-framework/source-tool`, then parked on four
upstream defects (433, 434, 435, 436 — watch #199). The org no longer
waits on that tracker: the emitter is now in-org (#207) — the canon's
`source-attest` action, invoked by each repo's `source-attest.yml`
under its reserved identity. Per push to `main` it reads the rules API
(enforcement, never intent), emits org-defined source provenance and
the spec's VSA, verifies the previous chain link against the pinned
identity, and appends both to `refs/notes/commits`. The old pilot's
genesis attestations remain in release-lab's notes as the historical
record — a different dialect, not a link in the new chain. If upstream
ships its fixes, source-tool becomes an independent *cross-check* of
these VSAs, never the thing that issues them.

Activation is per repo, deliberate, and lab-first: the formal level
stays **0** until a repo's chain is founded and stranger-verified, and
`docs/direction.md` moves only when that is real.

## Requirement mapping (v1.2, L1–L3)

| Requirement | Level | Enforcement |
| --- | --- | --- |
| Choose an appropriate SCS | 1 | GitHub |
| Repository IDs / immutable revision IDs / human-readable diffs | 1 | GitHub (git SHAs, PR diffs) |
| Source VSAs | 1 | **Emitter built (#207), not yet activated** (the L0 gap) |
| Access control + reliable history | 2 | `org-default-branch` ruleset: deletion blocked, force push blocked, linear history, org-wide, empty bypass list |
| Tag immutability | 2 | `org-default-tag` ruleset: update/move/delete blocked, all tags, all repos |
| Safe expunging process | 2 | `docs/expunging.md` |
| Identity management | 2 | GitHub accounts, org 2FA required, DCO signoff + `required_signatures` attribute every change |
| Source provenance, contemporaneous | 2 | **Emitter built (#207), not yet activated** |
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

## Activation checklist

The machinery landed with #207: the canon's `source-attest` action,
`source-policies/` (the org policy, `since` times from the ledger
above), the activated `workflow-templates/source-attest.yml`, and the
`audit:source-vsa` Monday walk. Activation is per repo, lab first:

1. Create the repo's `source-attest` environment holding
   `SOURCE_RULES_TOKEN`: a fine-grained read-only PAT that can read
   org-level ruleset details (`repos/{repo}/rulesets/{id}` for an
   org-parented id — the ambient `GITHUB_TOKEN` cannot, #240; the
   minimal grant is measured at lab activation and recorded here, the
   `AUDIT_TOKEN` precedent). The environment scoping is the
   `audit.yml` `baseline-drift` pattern: only the claims job — which
   holds no `id-token` and no `contents: write` — is ever issued the
   secret, so the token and the signing identity never share a job.
2. Copy the template over the repo's inert stub — same path, new
   content; requires canon **>= v1.12.0**, the release that ships the
   two-stage action (#240) — earlier emitters swallow an unreadable
   tag-ruleset read and under-claim L2 silently. (Not v1.9.0 for a
   second reason: that release shipped the action taking cosign from
   sigstore's installer, which the org Actions allowlist refuses at
   `Set up job`, #221.) Copy the template as it stands in the
   canon rather than reconstructing the pin — a template pin can name a
   canon release older than the newest, and for this template that is
   the difference between an emitter and a dead run.
   The per-repo copy is deliberate: the workflow is the signer, and an
   in-repo workflow's identity is `@refs/heads/main`, stable forever —
   while every line of logic lives once in the canon action, whose pin
   Renovate bumps without touching the identity (composite steps run
   inside the caller's job; pick-two trilemma resolved, .github#120).
   **The merge that lands this goes red, and should.** Its own push
   fires the workflow before any chain exists, and the emitter refuses
   to self-found: a missing previous link is loud, always, because the
   alternative is an emitter that quietly re-founds a chain somebody
   truncated. One red run per repo, once, at a moment when a human is
   already watching.
3. Found the chain: `gh workflow run source-attest -f genesis=true`.
   Genesis is refused if any link already exists on the history.
4. Push something ordinary; confirm the link chains to genesis.
5. Stranger-verify on a machine holding nothing but the published root
   of trust (`source-assessment.md`): extract the note, then
   `cosign verify-blob --bundle <bundle> --certificate-identity
   https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main
   --certificate-oidc-issuer https://token.actions.githubusercontent.com
   <statement>`.
6. Lab only: simulate a lapse — weaken one ruleset rule, push, confirm
   the property drops and the VSA under-claims level 2; restore, confirm
   recovery. The claim set must be a function of live enforcement.
7. When release-lab, signer and this repo are chained and verified:
   update `docs/direction.md`'s Source row to L3 and rewrite this
   document's formal-standing section — a level that becomes true and
   goes unrecorded is the same defect as one claimed before it was
   (#207's done condition).

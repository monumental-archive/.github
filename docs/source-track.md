# SLSA source track

The org's position against the SLSA v1.2 source track (published spec,
final): what is enforced, what is claimable, and what formal standing is
— stated honestly.

## Formal standing: Level 3, emitted and stranger-verifiable

The v1.2 source VSA is constitutive: "If the SCS DOES NOT generate a VSA
for a revision, the revision has Source Level 0." Since **2026-08-12**
the org emits them. Every revision reaching `main` in `.github`,
`signer` and `release-lab` carries signed source provenance and a source
VSA at `SLSA_SOURCE_LEVEL_3`, naming all eight `ORG_SOURCE_` properties,
chained in `refs/notes/commits` under each repo's reserved signing
identity. The formal level is **3**.

Genesis revisions: `.github` `624babfa`, `signer` `9adecf65`,
`release-lab` `ea49b2f0`. Verify any link with nothing but the root of
trust published in `source-assessment.md` — no org token, no canon
checkout:

```bash
# v3 signatures cover PAE(payloadType, statement), never the bare
# statement — build pae.bin per stele's docs/chain-format.md, then:
cosign verify-blob --bundle <bundle> \
  --certificate-identity \
  https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com pae.bin
```

That is step 1 of `verifying-source` — the signature and the issuing
identity. Step 2 is comparing the VSA against expectations, and these
are the values to expect, published here so a consumer forms them from
the producer rather than from the first VSA they happen to see:

| Field | Expected |
| --- | --- |
| `verifier.id` | the same `source-attest.yml@refs/heads/main` URI as the certificate identity, per repo |
| `subject[0].digest.gitCommit` | the revision you fetched |
| `subject[0].annotations.sourceRefs` | `["refs/heads/main"]` — the emitter attests protected-ref revisions only |
| `predicate.resourceUri` | `git+https://github.com/monumental-archive/<repo>` |
| `predicate.verificationResult` | `PASSED` |
| `predicate.verifiedLevels` | one source level, plus the `ORG_SOURCE_` properties live at that revision |

A VSA naming any other resource, ref or verifier is not this chain's.

### Gaps heal — the one that taught it

`.github@e1ad2dde` (#256) lost its emitter run to a toolchain-download
flake before anything signed, and for one day it stood as the track's
recorded gap: Source Level 0 by the spec's own rule, with no path to
backfill. #265 closed that class of defect — the emitter now walks
genesis→tip on every push and emits links for any holes, each carrying
`repaired: {at}` in its provenance and a **computed** level (the
target only when every contributing ruleset's `updated_at` predates
the revision's commit time; `SLSA_SOURCE_LEVEL_2` otherwise). The
revision itself was healed at `SLSA_SOURCE_LEVEL_3` on 2026-08-12,
continuity proven. A hole is now a transient state the system exits on
its own; `audit:source-vsa` is the alarm while it is in one. Field
semantics for healed links live in
[`source-provenance.md`](source-provenance.md).

**Ledger forks: the #434 record, closed.** The first diagnosis — a
lost-update race in the emitter's fetch–append–push loop — was wrong,
and the heal it prescribed made things worse: re-emission through the
same emitter minted one more bad link. The actual defect was a digest
disagreement. The ledger contract is SHA-256 over the note blob bytes
exactly as git stores them, trailing newline included; a formatting
pass (#393, 2026-08-14) rewrote the emitter's copy of that computation
into command substitution, which strips the trailing newline, so every
link emitted since named predecessor bytes that existed nowhere. The
walker's copy still hashed the stored bytes — two copies of "the"
digest, drifted, with no test comparing them.

Closed on 2026-08-16 by the emit cutover (stele#21, stele#24). The
emitter is now `stele emit chain`: one digest function shared by the
emit and verify legs, the note read back from the object store and
hashed as git kept it, and the predecessor hash computed inside the
push attempt that wins. Two copies of the computation is no longer
representable. The defective spans — 31 revisions on this repo, 15 on
`stele` — were deleted and re-emitted through the new emitter, each
carrying `repaired: {at}` and a continuity-guarded level; `signer` and
`release-lab` were never affected. All four chains verify end to end
at `SLSA_SOURCE_LEVEL_3`, and `audit:source-vsa` is green org-wide.

**release-lab's first five links claim `SLSA_SOURCE_LEVEL_2`, and stay
that way.** They were emitted before the claims job held a token that
could read org-level tag-ruleset details, so `ORG_SOURCE_TAG_IMMUTABLE`
and `ORG_SOURCE_RELEASE_TAG_MINTED` were absent and the VSA under-claimed
rather than asserting a control it could not see. They are not
backfilled: they are the record of what was verifiable at that moment,
and the evidence that honest degradation is a real behaviour rather than
a design intention.

**Where the mechanism is specified.** Since the emit cutover this
document is the ORG's record — what is enforced, what is claimed, and
the continuity ledger below. The emitter's behaviour (discovery, the
heal walk, level computation, the ledger contract) is specified by
`stele` and its tests: the note format in `internal/chain`, the
emission engine in `internal/emit`, the verifier that shares both in
`internal/verify`. The canon speaks for the org; stele speaks for the
mechanism.

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

Activation was per repo and lab-first: release-lab proved the emitter
end to end before `signer` and `.github` founded their chains, and
`docs/direction.md` moved only once all three were stranger-verified.

## Requirement mapping (v1.2, L1–L3)

<!-- tracks:source:begin -->

| Requirement | Level | Enforcement |
| --- | --- | --- |
| Choose an appropriate SCS | 1 | GitHub |
| Repository IDs / immutable revision IDs / human-readable diffs | 1 | GitHub (git SHAs, PR diffs) |
| Source VSAs | 1 | **Emitted** per push, all three repos (#207) |
| Access control + reliable history | 2 | `org-default-branch` ruleset: deletion blocked, force push blocked, linear history, org-wide, empty bypass list |
| History (SCS): every Named Reference change recorded — when, by whom, new revision ID | 2 | Each chain link records the actor, `commitTime`, the revision and its parents (`source-attest` emit) — contemporaneously with the push in the ordinary case, and honestly late on healed links (`repaired` marks the deviation — `source-provenance.md`, healed links); GitHub's ref history is the platform record beneath it. Distinct from the *Organization* requirement in the row above — this row was absent until #349 finding 4, met in substance but unmapped |
| Tag immutability | 2 | `org-default-tag` ruleset: update/move/delete blocked, all tags, all repos |
| Safe expunging process | 2 | `docs/expunging.md` |
| Identity management | 2 | GitHub accounts, org 2FA required, DCO signoff + `required_signatures` attribute every change |
| Source provenance, contemporaneous | 2 | **Emitted** contemporaneously with the ref update, in the repo where the push authentically exists (#207) — a healed link is the named deviation: `repaired` marks it and its level is computed under the continuity guard (`source-provenance.md`, healed links) |
| Continuity | 2 | Ruleset timestamps (GitHub-recorded); a ruleset edit that weakens a control resets its clock — see `rulesets.md` |
| Continuous technical controls, documented | 3 | This document + `rulesets.md`; the control set: required `ci / ci` gate (bound to the Actions app), required signatures, linear history, review-thread resolution, squash-only merges, `v*` creation locked to the minting App, capability-boundary lint |
| Protected named references, org properties | 3 | Rulesets as above; `ORG_SOURCE_GATED` emitted live in every source VSA since 2026-08-12 (#207) |

<!-- tracks:source:end -->

## The claims contract: the `ORG_SOURCE_` set, named once

The v1.2 namespace for org-defined control claims in source VSAs. The
set is *all enforced controls*, enumerated from the live rulesets and
the belt (read 2026-08-12), not curated. **These names are a public
contract**: the moment a stranger's policy gates on one, a rename is a
breaking change. They were frozen here ahead of emission so the emitter
would implement this table rather than invent names; the emitter has
existed since 2026-08-12 (#207), and all eight properties emit live in
every chain link.

**The executable table now lives in the policy** — the `source.claims`
section of `slsa/verify-policy.json` declares each property's scope and
matcher, `stele derive claims` derives the claims from it against the
live rules API, and the loader cross-checks it against
`requiredProperties` so a property a branch requires but the table
cannot derive refuses at load. The rows below are the org narrative —
what each property means and which convention enforces it; the matchers
in the policy are the mechanism, and disagreement between the two is a
policy bug, not a documentation one.

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

## The signing identity, frozen

The source-signing identity is the per-repo workflow
`.github/workflows/source-attest.yml` at `@refs/heads/main` — the
certificate identity is the workflow path plus ref, so the path is the
contract. Every org repo carries it (`workflow-templates/source-attest.yml`,
copied per repo like the scorecard stub). **Renaming or moving the file
is a breaking change to the root-of-trust contract** published in
`source-assessment.md`; content changes freely, the path never — which
is why the file went from an inert `workflow_dispatch` stub to a live
two-job emitter without the identity moving, and why chain links signed
before that change still verify against the same pattern today.

## L4: recorded headcount ceiling

Two-party review requires two trusted persons; GitHub will not let a
solo maintainer approve their own pull request. This is a decision
boundary, not an oversight. The spec's perpetual-exception allowance for
trusted robots means Renovate and the release App would not block L4 if
a second maintainer ever exists: flip
`required_approving_review_count` to 1+, and the clock for that control
starts at that revision.

## Activation checklist

The machinery landed with #207: the canon's `source-attest` action
(since the stele#24 cutover, a thin orchestration around `stele emit
chain`; the org policy is `slsa/verify-policy.json`, `since` times
from the ledger above), the activated
`workflow-templates/source-attest.yml`, and the `audit:source-vsa`
Monday walk. Activation is per repo, lab first:

1. Create the repo's `source-attest` environment holding
   `SOURCE_RULES_TOKEN`: a fine-grained read-only PAT that can read
   org-level ruleset details (`repos/{repo}/rulesets/{id}` for an
   org-parented id — the ambient `GITHUB_TOKEN` cannot, #240). **The
   measured minimal grant is `Administration: Read-only`** and nothing
   else, scoped to the repos that run the emitter (measured at the
   release-lab activation, 2026-08-12: the ambient token lists and
   reads the tag rulesets without error, but the payload it returns
   does not carry `bypass_actors`, so the content match fails and both
   tag properties drop; a read-only Administration grant returns
   `bypass_actors: []` and the match succeeds). Verify a candidate
   token before wiring it in — `gh api
   repos/monumental-archive/<repo>/rulesets/<tag-ruleset-id>` must
   return `bypass_actors` alongside the rules. The environment scoping is the
   `audit.yml` `baseline-drift` pattern: only the claims job — which
   holds no `id-token` and no `contents: write` — is ever issued the
   secret, so the token and the signing identity never share a job.
2. Copy the template over the repo's inert stub — same path, new
   content; requires canon **>= `.github@v1.11.1`**, the release that
   ships the two-stage action (#240) — earlier emitters swallow an
   unreadable tag-ruleset read and under-claim L2 silently. (Not
   `.github@v1.9.0` for a second reason: that release shipped the
   action taking cosign from sigstore's installer, which the org
   Actions allowlist refuses at `Set up job`, #221.) Copy the template
   as it stands in the canon rather than reconstructing the pin — a
   template pin can name a canon release older than the newest, and for
   this template that is the difference between an emitter and a dead
   run.
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
   of trust (`source-assessment.md`): extract the note, rebuild the
   PAE bytes per stele's `docs/chain-format.md`, then
   `cosign verify-blob --bundle <bundle> --certificate-identity
   https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main
   --certificate-oidc-issuer https://token.actions.githubusercontent.com
   pae.bin`.
6. Lab only: simulate a lapse — weaken one ruleset rule, push, confirm
   the property drops and the VSA under-claims level 2; restore, confirm
   recovery. The claim set must be a function of live enforcement.
7. When release-lab, signer and this repo are chained and verified:
   update `docs/direction.md`'s Source row to L3 and rewrite this
   document's formal-standing section — a level that becomes true and
   goes unrecorded is the same defect as one claimed before it was
   (#207's done condition).

# Build platform self-assessment

SLSA's `assessing-build-platforms` page says consumers "cannot trust
platforms to produce Build L3 artifacts and provenance unless they have
some proof that the provenance is unforgeable and the builds are
isolated", and closes with the obligation this document discharges:
organisations "can either self-attest to their answers or seek
certification from a third-party auditor", and evidence for
self-attestation "should be published on the internet".

The org claims Build L3. A stranger's root of trust maps a `builder.id`
to a level, and the `builder.id` on every artifact here is **ours**, not
GitHub's — so the answers below are ours to give. This is the build-track
counterpart of [`source-assessment.md`](source-assessment.md), written in
the same register: prompts answered honestly, limits named rather than
narrated around.

## Whose platform is this

The build platform is split, and the split is the design.

| Component | Operated by |
| --- | --- |
| Compute, isolation, ephemerality, runner images | GitHub |
| Control plane that generates and signs provenance | **This org** — `signer`'s `sign.yml`, the `builder.id` |
| Build definitions the tenant supplies | The caller repository |

GitHub's own conformance is GitHub's to assert; this document does not
restate it. What is assessed here is the layer the org operates: the
provenance-generating control plane, the shared workflows that constitute
the build definition, and the boundary between them and caller code.

`builder.id` resolves to `monumental-archive/signer/.github/workflows/sign.yml`
because GitHub's buildType spec defines it as the workflow that
*generated* the provenance, and provenance generation lives in a
repository that executes no caller-supplied code. That is the whole
security claim in one sentence; everything below is how it is kept true.

## External parameters

**How are they processed, and which are represented in provenance?** The
shared workflows declare typed `workflow_call` inputs, never free-form
steps — a caller supplies a version, a class list, a crate directory,
never a command. The GitHub Actions buildType enumerates
`externalParameters.workflow` (ref, repository, path) and requires
external parameters to be fully enumerated at L3; the stock GitHub
provenance satisfies that, and the org adds no parameter outside it.

**How is a future design change prevented from adding an unrepresented
parameter?** Structurally, by the same boundary: a new input is an input
to a workflow whose job runs on the caller's side of the line, and the
signing job takes only validated subject records and a predicate drawn
from an allowlist. `lint:capability-boundary` fails the gate if any
`workflow_call` workflow declares `id-token: write` without an explicit
marker stating why it is safe.

**Closed limit, recorded.** `gh attestation verify` exposes no flag for
`buildType` or `externalParameters`, two of the four fields
`verifying-artifacts` asks a verifier to compare. That was a gap in the
org's *verification* until #210: `verify-release.yml`'s verdict mode now
asserts the GitHub Actions buildType, rejects unrecognised
`externalParameters` fields, and compares `externalParameters.workflow`
against the run's own identity — all before any verdict field is
written. The equivalent consumer-side check is published in
[`runbook.md`](runbook.md) as jq over the verify call's JSON, so no new
tool enters the belt.

## Control plane

**Administration.** One human administrator, stated plainly. Accounts are
2FA-required org-wide. Org-level rulesets carry empty bypass lists and can
be edited only in the GitHub UI by an owner; every edit is timestamped by
GitHub and the Monday audit compares live settings against the recorded
baseline, so weakening a control is recorded evidence rather than a silent
act. There is no two-party review — the same headcount boundary as Source
L4 (#126), and it applies to the recorded *expectations* as much as to the
source: the threats page names two-party review as the mitigation for
tampering with a verifier's expectations. The compensating control is
observability, not headcount.

**Recovery.** Succession and break-glass are documented in
[`continuity.md`](continuity.md), including the one procedure that
deliberately disables a ruleset (hand-minting a tag when the App is dead)
and requires recording the event.

**Provenance generation.** Provenance is generated and signed in `signer`,
in a job that performs no checkout of caller code and runs no
caller-supplied step. Situations where provenance is not generated: dry-run
releases, which pass the manifest through and sign nothing — a rehearsal
must never produce a PASSED verdict. `audit:attestations` walks every
published release weekly and fails if any artifact class lacks its evidence
set, which converts "we attest" into "nothing ships unattested".

**Development practices.** The control plane's software is this
repository: version controlled, public, gated by its own `gate.yml` on
every pull request, every `uses:` pinned to a full commit SHA under the
org's `sha_pinning_required` policy, and the Actions allowlist set with
`verified_allowed: false` so a third-party action cannot run at all. Shared
workflow changes are proven in `release-lab` before any production repo
moves its pin. Communications are GitHub's TLS.

**Creating build environments.** The control plane does not share a
filesystem with build environments; caller artifacts reach the signing job
as validated subject records — sha256sum lines matched against a fixed
record shape — not as files or scripts.

**Managing cryptographic secrets.** There are none. Signing is keyless:
Fulcio issues a certificate from the workflow's ambient OIDC identity per
run, minutes-lived, and every signature lands in Rekor. Nothing is stored,
rotated, backed up or stealable, so the questions about secret storage,
memory protection and rotation cadence have no subject. Remediation for a
compromised identity is detection rather than rotation: `rekor-monitor`
watches the log for certificates minted under org identities that the org
did not make, and an unexpected issuance is a stop-everything event
([`runbook.md`](runbook.md)).

**Known limit.** Anything that can push a branch in `signer` mints a
matching identity, because `--signer-workflow` is a prefix regex with no
trailing anchor. `signer` therefore carries the same org rulesets as every
other repo, and consumers are told to pin `--signer-digest` as well as
`--signer-workflow`.

**Closed limit (#264).** Verdicts no longer route through the org
signer: `verify-release.yml` signs its own VSA, so `verifier.id` is the
certificate subject and "who computed this verdict" is a cryptographic
fact rather than a predicate field a stranger takes on the org's word.
The org carries two roots of trust — provenance and producer evidence
under `signer/sign.yml`, verdicts under
`.github/verify-release.yml` — with the consumer recipes and the
version boundary (verdicts before canon v1.14.0 verify under the old
identity) in [`runbook.md`](runbook.md). The spec never required the
split — `verification_summary`'s own example places the binding in the
consumer's `(signer, verifier)` allowlist — but the org's bar is that
strangers verify rather than trust, and now they can. The
`inputAttestations` cross-check remains: a *false* verdict must list
evidence that does not verify or does not cover the subject, and that
is checkable regardless of which identity signed it.

## Build environment

**Isolation.** GitHub-hosted runners, ephemeral per job, on the platform's
own isolation. `--deny-self-hosted-runners` is part of the published
verification recipe, so a consumer rejects anything built off-platform.
The runner image is a named pin (`ubuntu-24.04`) rolled by Renovate as a
visible diff rather than by GitHub's schedule, and enforced by
`lint:runner-pin` — a claim, not a convention, since #290 found the
signer floating on `ubuntu-latest` with nothing red.

**Persistence between builds.** Ephemerality is GitHub's guarantee; what
the org adds is the cache rule, because a cache is the one legitimate way
one build influences another. Caches are permitted only where a human is
waiting and nothing is signed — pull-request `ci` runs. Every path that
signs or publishes builds cold, and `lint:cold-attested` fails the gate on
a workflow that uses a cache without an `unattested-path:` marker
explaining why its path signs nothing.

**Container build environments.** The pgrx classes build inside postgres
containers, digest-pinned in `docker/pgrx-base-images.toml`, org-attested
by `base-attest.yml`, and verified fail-closed before any container runs.
The org is the build *platform* at that layer but not the build image
*producer*; the resulting BuildEnv standing is L0 and is stated as such in
[`direction.md`](direction.md) and [`slsa-reference.md`](slsa-reference.md).

**Network access.** Builds reach the network, and there is no egress
allowlist. That is a decision with its reasons written down
([`release.md`](release.md), "No runner-hardening agent"): SLSA's
"Isolated" requirement explicitly does not prohibit it, wrong allowlists
have broken releases in this org's earlier pipelines, and the attack class
an agent detects best — a backdoored third-party action — cannot run here
at all. Hermeticity is tracked as its own campaign (#119), not claimed.

**Forensics.** Build environments are ephemeral and not retained; what
survives a run is the provenance, the Rekor entry, the run log, and the
digest-pinned inputs. Post-hoc analysis is reconstruction from those, not
inspection of a preserved environment. Stated as a limit.

## Cache

Covered above: caches exist only on unsigned paths, and the rule is
mechanically enforced rather than remembered. Build inputs that would
otherwise be fetched fresh are pinned instead — `mise.lock` carries exact
versions with per-platform checksums and GitHub attestations, actions are
SHA-pinned, base images digest-pinned, lockfiles committed.

## Output storage

**Preventing one build from overwriting another's outputs.** Outputs land
in GitHub releases under org-level rulesets that restrict `v*` tag creation
to the release App, with immutable releases enabled: once published,
assets cannot be added, modified or deleted and the tag is locked to its
commit. Registry publishing is trusted-publishing only — no long-lived
tokens exist to steal — and crates.io exchange tokens are single-use.

**Processing of output artifacts by the control plane.** The control plane
pulls published bytes back from the registry and proves them against the
built digests before anything is signed (`verify-release.yml`, bytes mode), so the
attested subject is what a consumer downloads rather than what a build
claimed. It performs no transformation of artifact bytes.

## Evaluation

This is a self-attestation. No third-party certification has been sought.
The org's answers are verifiable in the open where they are mechanical —
every workflow, lint and ruleset referenced above is world-readable in
this repository, and every claim about a released artifact is checkable
with `gh attestation verify` per [`runbook.md`](runbook.md) — and are
plain assertions where they are organisational, chiefly the single
administrator and the absence of two-party review.

When a limit named here is closed, the closing change updates this
document; a self-assessment that lags the platform it describes is worse
than none.

# Build track: the conformance mapping

The Build track's per-requirement answer sheet, the sibling of
[`source-track.md`](source-track.md) and
[`dependency-track.md`](dependency-track.md): which requirement, met
how, verified by whom. It supersedes nothing —
[`release.md`](release.md) keeps the mechanism,
[`build-assessment.md`](build-assessment.md) the platform
self-assessment, [`slsa-reference.md`](slsa-reference.md) the spec
facts. This page is the conformance argument, in one place, so that a
claim and its refutation can never again live in different documents
(#213 — the BuildEnv contradiction survived precisely because nothing
sat between `direction.md`'s table and `slsa-reference.md`'s analysis
to force the question).

The claimed level is **Build L3, met**, against SLSA v1.2. L3 is the
track's published ceiling — Build L4 exists only in the spec's future
directions — so "max" on this track means L3 achieved, not "L3 for
now".

## Producer requirements

| Requirement | Discharged by |
| --- | --- |
| Choose an appropriate build platform | GitHub Actions plus the org's signer split — a platform whose control plane the org extended rather than trusted blind; capability assessed in [`build-assessment.md`](build-assessment.md) |
| Follow a consistent build process | The orchestrator (`publish.yml`): callers declare inputs, never steps; the step order is fixed in the canon and unrearrangeable by a caller; every `uses:` SHA-pinned |
| Distribute provenance | The attestation store (the same API `gh attestation verify` reads) plus the evidence bundle attached to every release; consumer recipes in [`runbook.md`](runbook.md) |

## Build platform requirements, per level

### L1 — provenance exists

Every artifact class emits full provenance: the platform's stock
statement (buildType `actions.github.io/buildtypes/workflow/v1`,
fully-enumerated `externalParameters`) assembled and signed by
`signer/sign.yml`, whose path is the org's `builder.id` — the GitHub
buildType defines that field as the entity that *generated the
provenance*, which is the signer, not the compiling job. Distribution
is the store plus release assets.

### L2 — provenance is authentic

Sigstore keyless signatures under the org identity; verification is
one command a stranger runs with published inputs only
([`runbook.md`](runbook.md), "Verifying, as a consumer would"). The
`--deny-self-hosted-runners` flag is part of every documented recipe.

### L3 — provenance is unforgeable

The capability boundary, which is the whole design: **no workflow that
runs caller-supplied code may declare `id-token: write`** without a
written `capability-boundary:` marker, enforced by
`lint:capability-boundary`. Signing material never shares a job with
user-defined build steps — the spec's actual L3 line ("MUST NOT be
accessible to the environment running the user-defined build steps").
`sign.yml` runs no checkout and no caller code; a compromised build job
can mint a certificate, but it bears the *caller's* identity, and every
documented verification pins `--signer-workflow` to the signer path
plus `--signer-digest` to the commit.

Where the claim rests on a spec carve-out, the carve-out by name:

- **Tenant-generated `subject`** — the digests are computed by the
  caller's build; explicit at L2–L3, and forging an output digest is a
  declared non-threat (threats page, E2). Reproducible builds are the
  leg that closes it; `repro-check` runs it per release.
- **Best-effort `resolvedDependencies`** — completeness is a SHOULD at
  every level. The org answers it above the letter with the
  **enrichment companion** (#200): a `build-enrichment/v1` predicate
  computed entirely in the verification control plane (toolbelt lock
  and base-image digests from the pinned canon tree, the released
  repo's lockfiles fetched at the attested source revision) and signed
  by the verifier — never tenant fields inside the platform envelope,
  which is the shape the spec's L3 constraint exists to prevent.

### Hosted, isolated

GitHub-hosted ephemeral runners; isolation is the platform's;
`--deny-self-hosted-runners` fails verification for anything else.
Residuals (cache poisoning posture, `cache: false` on signing paths)
in [`build-assessment.md`](build-assessment.md).

## The verdict: who says PASSED, and why you can believe them

Every class receives a signed VSA after publish, assembled by
`verify-release.yml` in verdict mode — every predicate field the
return value of a check that job just ran (#208, #209, #210) — and
**signed by the verifier itself** (#264): `verifier.id` is the
certificate subject, so "who computed this verdict" is cryptographic
fact. The org therefore carries two roots of trust — provenance and
producer evidence under `signer/sign.yml`, verdicts under
`.github/verify-release.yml` — with recipes and the pre-v1.14.0
version boundary in [`runbook.md`](runbook.md).

`SLSA_BUILD_REPRODUCED` is deliberately never claimed: `repro-check`
rebuilds on the same platform by design (skew-proofing makes a
mismatch mean nondeterminism), and the property requires two
independently operated build platforms — recorded as **unreachable**
in [`tooling-verdicts.md`](tooling-verdicts.md), not watched.

## Build Environment: a section, not a page

Formally **L0 at both layers, by attribution rather than effort**. The
producer obligations of BuildEnv L1 are implemented — the runner image
is a named, Renovate-rolled pin; pgrx base images are digest-pinned,
org-attested (`base-attest.yml`) and verified fail-closed before any
container runs. What would make those controls a *level* is a signed
build-image provenance from the image producers, which they do not
publish, and which building harder here cannot conjure. `base-attest.yml`
is the org attesting its own *approval* of a base digest — an
org-authored claim about a third-party artifact, deliberately not
passed off as the producer provenance it is not. The row moves to L1
the day base-image provenance arrives signed and the org's
verification of it is itself attested (#125 watches the runner half).

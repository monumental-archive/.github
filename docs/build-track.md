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

<!-- tracks:build:begin -->

| Requirement | Discharged by |
| --- | --- |
| Choose an appropriate build platform | GitHub Actions plus the org's signer split — a platform whose control plane the org extended rather than trusted blind; capability assessed in [`build-assessment.md`](build-assessment.md) |
| Follow a consistent build process | The orchestrator (`publish.yml`): callers declare inputs, never steps; the step order is fixed in the canon and unrearrangeable by a caller; every `uses:` SHA-pinned |
| Distribute provenance | The attestation store (the same API `gh attestation verify` reads) plus the evidence bundle attached to every release; consumer recipes in [`runbook.md`](runbook.md) |

<!-- tracks:build:end -->

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
plus `--signer-digest` to the commit — in workflows, always derived from
the tree's own `sign.yml` pin by the `verify-signed` action, never
hand-written (#314; `lint:signer-pin` enforces the ban).

Where the claim rests on a spec carve-out, the carve-out by name:

- **Tenant-generated `subject`** — the digests are computed by the
  caller's build; explicit at L2–L3, and forging an output digest is a
  declared non-threat (threats page, E2). Reproducible builds are the
  leg that closes it; `repro-check` runs it per release.
- **Best-effort `resolvedDependencies`** — completeness is a SHOULD at
  every level. The org answers it above the letter with the
  **enrichment companion** (#200): a `build-enrichment/v2` predicate
  computed entirely in the verification control plane (toolbelt lock
  from the pinned canon tree; base-image digests only for the majors
  the declaring class's build actually instantiated — a `FROM scratch`
  class claims none, because a signed false dependency is worse than an
  omitted one, #316 finding 3; the released
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
`.github/verify-release.yml` — with recipes and the version boundary
before `.github@v1.14.0` in [`runbook.md`](runbook.md).

`SLSA_BUILD_REPRODUCED` is deliberately never claimed: `repro-check`
rebuilds on the same platform by design (skew-proofing makes a
mismatch mean nondeterminism), and the property requires two
independently operated build platforms — recorded as **unreachable**
in [`tooling-verdicts.md`](tooling-verdicts.md), not watched.

## Build Environment: a section, not a page

Formally **L0 at both layers, by attribution rather than effort**. The
**platform** obligations of BuildEnv L1 are discharged **at the
container layer** — pgrx base images are digest-pinned, org-attested
(`base-attest.yml`) and verified fail-closed before any container runs.
At the **runner layer** they are not, and cannot be from here: GitHub
publishes no runner-image provenance, so there is nothing to verify
before instantiation and nothing to attest — the runner image is a
named, Renovate-rolled pin enforced by `lint:runner-pin`, which is
selection, not verification (#125 watches; the claim was over-broad
twice, #290 and #349 finding 5, and the qualifier is the sentence that
keeps the claim beside its refutation). The **producer**
obligations — generating Build L2+ provenance for the build images
themselves and allowing its independent verification — are not ours to
discharge and are unmet upstream, which is exactly why the row is L0:
a signed build-image provenance from the image producers is what would
make these controls a *level*, they do not publish one, and building
harder here cannot conjure it. (#290 finding 1 corrected this
paragraph, which previously called the discharged obligations the
producer's — the one page whose job is to keep a claim and its
refutation in the same place had them inverted.) `base-attest.yml`
verifies the upstream (unsigned, Build L1) BuildKit provenance names
the `docker-library` source the pin implies, and signs *that
verification* under org identity (#212) — the strongest claim
available without owning the builds, and also BuildEnv L1's
third-obligation artefact — deliberately never passed off as the
producer provenance it is not. The row moves to L1
the day base-image provenance arrives signed and the org's
verification of it is itself attested (#125 watches the runner half).

## Which base is approved by which mechanism

"Base-image approval" named two different scopes in one tree until
issue #715, which is how a first-party base came to be pinned by
digest and attributed to nobody. There are three, and each answers a
different question:

| Base | Mechanism | The question it answers |
| --- | --- | --- |
| The canon's pgrx build and smoke images (`docker/pgrx-base-images.toml`) | `base-attest.yml` mints a `base-image-approval/v1` over each pinned digest; `build-pgrx-extension.yml` verifies it fail-closed before any container runs | Did the org check the upstream provenance of this build environment and sign that it did? |
| A caller's `FROM` under `ghcr.io/<owner>/` | `build-oci-image.yml`'s base-approval step, before the build: `gh attestation verify` against the org signer (`security/identity.toml`) at the ref the pinned tag implies — `refs/tags/v<version>`, or `refs/heads/main` for a `latest` stream | Were these bytes produced by the org's own publish path, at a ref that could legitimately have produced them? |
| Every other `FROM` | `lint:from-digests` in the gate | Are the bytes pinned, so that whatever is instantiated is what was reviewed? |

The three are not interchangeable, and the middle one is the only one
whose subject the org **produced**. Its identity is therefore derived,
never declared: the signer comes from the canon's one identity
declaration, the org path from `GITHUB_REPOSITORY_OWNER` (scope is the
environment's to supply — `security/identity.toml`'s own rule), and the
ref from the tag beside the digest, the way the per-repo script this
replaced derived `1.2.3-pg18` → `refs/tags/v1.2.3`. A declared regex
per repository would be the literal nothing bumps (#314's class), and a
per-repo script would be the second derivation #670 deleted.

Deliberately *not* pinned in that verification: `--signer-digest`. It
names the signer commit, and a base signed at an older signer than the
tree building on it is the normal case rather than a finding —
measured, 2026-08-21: `release-lab@v0.27.0`'s published index carries
signer `e4a285f8`, while the canon tree pinned `e90a971e`. Pinning it
would refuse correct bases on every signer bump.

The check is network-bound (registry plus the attestation API), so it
can never be a `lint:*` — [`direction.md`](direction.md)'s determinism
rule — and a Monday `audit:*` would report a base already shipped. The
class build is the only place that is both fail-closed and early, and
it is the only place that knows the caller's Dockerfile.

**What this does not do**: it does not move the Build Environment row.
The runner layer is untouched (#125), and the producer obligation this
discharges is discharged only for bases the org itself published —
which is a property of those three bases, not of the population.

`slsa/assert-policy.json`'s `evidence.baseImages` block now carries
TYPED approval scopes (stele#247, epoch 7), so the schema can express
more than one mechanism. The canon declares the first row's scope,
`pin-file`, and only that one. The second row is expressible in
principle as a `provenance-verified` scope and is deliberately not
declared, for two measured reasons (#891): no base under
`ghcr.io/monumental-archive/` exists anywhere in the population today
— the org's only first-party base is `ghcr.io/carlallenn/edtf-postgres`,
still in the personal namespace ahead of #83's transfers, where the
scope's prefix-to-repository derivation does not hold — and the scope
carries a single `identity` template, while this org's claim has two
independent dimensions: the org's ONE shared signer workflow, and a
source ref derived from the pinned tag. Declaring it with a constant
identity would be strictly weaker than the gate that actually runs and
would manufacture the impression of coverage. The engine gap is
stele#269; the table above stays the honest account until it ships.

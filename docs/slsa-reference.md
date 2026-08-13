# SLSA and attestation reference

Settled facts about SLSA, GitHub's attestation machinery, and the scoring
frameworks this org targets — gathered from primary sources on 2026-08-09
so that the release design (issue #28, `docs/release.md`) rests on quoted
specification rather than recollection.

This is a reference, not a plan. Where sources disagree, both are recorded
and the disagreement is named. Where a question can only be settled by
running something, it is listed at the end rather than guessed at.

## Spec versions

**SLSA v1.2 is current and approved** (November 2025). v1.1 is retired —
anything written against "SLSA v1.0/v1.1" needs re-checking.

Two tracks are final in v1.2: **Build** and **Source**. Two exist only in
draft: **Build Environment** and **Dependency**. A **Platform Operations**
track is proposed.

## Build track

| Level | Definition |
| --- | --- |
| L0 | "No requirements—L0 represents the lack of SLSA." |
| L1 | "Package has provenance showing how it was built. Can be used to prevent mistakes but is trivial to bypass or forge." |
| L2 | "Forging the provenance … requires an explicit 'attack', though this may be easy to perform." Platform must "Generate and sign the provenance itself." |
| L3 | "Forging the provenance … requires exploiting a vulnerability that is beyond the capabilities of most adversaries." |

L3's normative requirements, from `build-requirements`:

- **Isolated** — "It MUST NOT be possible for two builds that overlap in
  time to influence one another"; nor for one build to "persist or
  influence the build environment of a subsequent build"; nor to "inject
  false entries into a build cache used by another build."
- **Secret material** — signing keys "MUST NOT be accessible to the
  environment running the user-defined build steps."
- **Unforgeable** — "Every field in the provenance MUST be generated or
  verified by the build platform in a trusted control plane."

Two carve-outs matter enormously here:

- **`subject` may be tenant-generated.** The spec explicitly permits "the
  names and cryptographic digests of the output artifacts, i.e. `subject`
  in SLSA Provenance" to come from the tenant.
- **Forging an output digest is a declared non-threat.** Threats page, E2:
  "None; this is not a problem. Any build claiming to produce a given
  artifact could have actually produced it by copying it verbatim."

**Hermetic builds are explicitly out of scope** for the Build track,
deferred to future directions.

### Why this org's design is L3

GitHub's docs say artifact attestations alone are **L2**, and describe
moving the *build* into a shared reusable workflow as the route to L3. The
GitHub-maintained buildType spec is more precise, and it is the document
that governs every attestation produced here:

> "The `builder.id` MUST represent the entity that **generated the
> provenance** … In practice, this is the workflow responsible for
> assembling/signing the provenance. When the provenance is generated
> within a **Reusable Workflow** that workflow will be used as the
> `builder.id`."

Confirmed in `actions/toolkit`: `builder.id` is
`${serverURL}/${claims.job_workflow_ref}`. GitHub's own `example.json`
pairs a caller repo's `release.yml` in `externalParameters` with a separate
shared builders repository as `builder.id`, in a file named
`builder_go_slsa3.yml`.

So a shared workflow that only *signs* is the specified meaning of
`builder.id`, not a loophole. Combined with the `subject` carve-out and
threat E2, and with the signing job running no caller code (which is the
requirement the default "attest in your own build job" pattern fails), the
architecture is L3-conformant.

## Source track (final in v1.2)

| Level | Requirement |
| --- | --- |
| L1 | Version controlled |
| L2 | Preserve change history; generate source provenance attestations; immutable history; no force push |
| L3 | Enforce organisational technical controls on protected branches, documented and continuously applied, with evidence in provenance |
| L4 | Two-party review |

**Control continuity**: enforcement must be continuous from an established
start point, and if it lapses, continuity **resets** at the next revision.
A temporarily disabled ruleset is a level-resetting event, not a blip.

This org's rulesets are the substance of L2/L3. The missing half is source
provenance attestations. Candidate tooling now exists (checked
2026-08-10): `slsa-framework/source-tool` with its `source-actions`
companion computes per-revision levels by recursing over prior
attestations and stores source provenance + a source VSA in git notes —
but it is PoC-grade, and as shipped its *reusable workflow's* identity
would sign our source claims. The adoption path is the rekor-monitor
one: run the binary under an org-owned workflow so the identity is ours.
Stood up and proven in the lab, then parked on four upstream defects —
and ultimately not adopted: the org built its own emitter instead
(#207), so this tool is now a candidate *cross-check* of our VSAs
rather than their issuer. Watch #199, position in `source-track.md`.

`gittuf` overlaps the same track from the git layer, and v1.2 names it
explicitly as an implementation route for both Identity Management and
Protected Named References. **Evaluated and declined**: it substitutes
for the ruleset half the org already satisfies platform-anchored, and
emits no source VSAs — so at the time of the evaluation it could not
have moved the org off Source L0, which was the only thing then blocking
the level (the org's own emitter closed that in #207). Its trust root is
self-held keys, which reintroduces the human key custody the keyless
architecture deliberately removed, for a platform-compromise threat
below the org's risk line, and a threshold root with one maintainer is a
threshold of one.

## Dependency track (draft)

L1 inventory of build dependencies · L2 all known vulnerabilities triaged
before release · L3 dependencies consumed from producer-controlled
locations · L4 enforced secure ingestion policy.

## Build Environment track (draft)

L1 signed build-image provenance, verified before the environment is
instantiated · L2 attested instantiation (vTPM, Secure Boot) · L3
hardware-attested. Not required for any Build level, still a **draft**
track — any conformance claim is a claim against a draft and says so.

L1 places three obligations, and it matters which party carries each.
On the **build image producer**: generate SLSA Build **L2 or higher**
provenance for the images they create, and allow independent automatic
verification of it. On the **build platform**: meet Build L2, and verify
the selected image's provenance before instantiating an environment,
emitting *"a signed attestation to the result of the SLSA Provenance
verification"*.

The fallback clause is narrower than it reads. It permits *"an
attestation asserting the expected hash value of the build image"* only
where the image artifact **cannot be published**, the spec's example
being intellectual-property concerns. It is an escape hatch for a
producer who withholds an image, not for a consumer whose upstream
publishes an image without provenance.

The org has two build-environment layers, and **both are formally L0**:

- **The runner VM image** is GitHub's: no signed runner-image
  provenance exists and nothing verifies before instantiation, so L1 is
  structurally out of reach at this layer. The maximum available move is
  made — `ubuntu-24.04` everywhere, a named input Renovate rolls as a
  visible diff, enforced by `lint:runner-pin` since the #290 audit found
  the signer itself floating on `ubuntu-latest` while four documents
  claimed this sentence — and the rest is a property of the platform,
  not of us. Watch #125.
- **The pgrx build containers** are stock Docker Official Images
  (`postgres:14`–`18`, bookworm and trixie, digest-pinned in
  `docker/pgrx-base-images.toml`). The org instantiates them, so at this
  layer the org is the build **platform** — but it is not the build
  **image producer**, and the producer's two obligations are therefore
  not the org's to discharge. `base-attest.yml` verifies, for every
  pinned digest and every platform in its index, that the upstream
  BuildKit provenance names the `docker-library/postgres` source and
  the pinned tag's `<major>/<suite>` directory, and signs **that
  verification** under org identity (#212); the build legs verify the
  signed result before any container runs and fail closed;
  `audit:attestations` proves the set stays complete. That is a real
  control and it is **not** the spec's fallback, which does not cover
  this case. L1's third obligation — the signed attestation of the
  verification result — is discharged by that same artefact.

Rebuilding the postgres bases in-org so they carry Build L3 provenance
of their own creation is the only route to the label, and it is
declined: it buys a draft-track level in exchange for owning a five-major
× two-suite build matrix and its end-of-life treadmill, permanently.

### What Docker Official Images actually publish

**Measured 2026-08-12, and it corrects an earlier measurement recorded
here.** The prior reading — "Docker Official Images publish no
provenance and no signatures", from `cosign verify` returning "no
signatures found" — was half wrong, and wrong in the instrument. `cosign
verify` looks for Sigstore signatures; BuildKit attestations are not
Sigstore signatures and are invisible to it. They ride the manifest list
as the `unknown/unknown` entries documented above under `actions/attest`.

Reading the registry directly for the pinned
`postgres:18-bookworm@sha256:8822…` index: each architecture has a
sibling manifest annotated `vnd.docker.reference.type:
attestation-manifest`, carrying two `application/vnd.in-toto+json`
layers — an SPDX SBOM and:

```json
{
  "predicateType": "https://slsa.dev/provenance/v0.2",
  "builder":  { "id": "https://github.com/docker-library" },
  "buildType": "https://mobyproject.org/buildkit@v1",
  "invocation": { "configSource": {
    "uri": "https://github.com/docker-library/postgres.git#4f9ced00…:18/bookworm",
    "entryPoint": "Dockerfile" } },
  "metadata": { "completeness":
    { "parameters": true, "environment": true, "materials": true },
    "reproducible": false }
}
```

So provenance exists, it is complete, and it names the exact upstream git
commit and Dockerfile that produced the pinned bytes.

**It is still not Build L2.** The blob is a bare in-toto Statement, not a
DSSE envelope — it carries no signature. Its integrity comes from the
digest chain (pin the index; the index covers the attestation manifest,
which covers the blob), not from a key held by the build platform. Build
L2's "Provenance is Authentic" requires validating *"the digital
signature of the provenance attestation"*, so what upstream publishes is
Build **L1** provenance, one level short of what BuildEnv L1 demands of
the producer.

The consequence is not "nothing to do". It is that the strongest
available claim is a *verification* claim rather than a level — and
that claim is now what `base-attest.yml` signs (#212): it fetches the
upstream provenance for every platform in the pinned index, asserts
`configSource.uri` names the `docker-library/postgres` repository and
the pinned tag's directory, and signs **that** under org identity —
"the org approved these bytes" became "the org verified these bytes
carry upstream provenance naming this source". The same artefact
discharges L1's third obligation, the signed attestation of the
verification result. It does not make the org BuildEnv L1, and the
claim is not written as though it did — `direction.md`'s row stays L0.

## Build L4 (planned, uncertain)

"Further hardening of the build platform and enabling corroboration of the
provenance." Four candidate requirements, explicitly "may or may not" be
included: **pinned dependencies**, **hermetic builds**, **complete
dependency documentation**, **reproducible builds**.

Hermeticity is the expensive one because it is per-build, not central:
dependency resolution, toolchain acquisition, `build.rs` scripts and native
dependency probing all reach the network by default, and proving absence of
egress requires enforcement rather than policy.

## The GitHub Actions buildType

`https://actions.github.io/buildtypes/workflow/v1`

**Only four event types are supported**: `create`, `release`, `push`,
`workflow_dispatch`. "This build type MUST NOT be used for any other event
type unless this specification is first updated." `pull_request` and
`repository_dispatch` are excluded as error-prone. Consumers "SHOULD reject
unrecognized external parameters."

`externalParameters.workflow` — `ref` (from `github.ref`), `repository`
(`server_url + "/" + repository`), `path` (parsed out of
`github.workflow_ref`). These are workflow *identity*, not build steps.

`internalParameters.github` — `event_name`, `repository_id`,
`repository_owner_id`, `runner_environment`. Numeric IDs are used "to
provide stable identifiers across account and repository renames and to
detect when an old name is reused for a new entity", so provenance survives
a transfer in the way that matters; verification policies should prefer the
numeric id.

`resolvedDependencies` — `git+<server>/<repo>@<ref>` with
`digest.gitCommit`.

## Fulcio certificate OIDs

Under `1.3.6.1.4.1.57264.1`:

| OID | Name | Holds |
| --- | --- | --- |
| .9 | Build Signer URI | `job_workflow_ref` — the called workflow |
| .10 | Build Signer Digest | `job_workflow_sha` |
| .11 | Runner Environment | platform-hosted or self-hosted |
| .12/.13/.14 | Source Repository URI / Digest / Ref | the caller |
| .21 | Run Invocation URI | the run |
| .23 | Deployment Environment | environment name |

## Verification

`gh attestation verify` flags: `--signer-workflow`, `--signer-digest`,
`--source-ref`, `--source-digest`, `--cert-identity[-regex]`,
`--predicate-type` (defaults to `https://slsa.dev/provenance/v1`),
`--deny-self-hosted-runners`, `--bundle`, `--bundle-from-oci`.

**`--signer-workflow` is a prefix regex, not a ref pin.** From `cli/cli`
source, the matcher is built as
`"^" + regexp.QuoteMeta("https://" + hostname + "/" + signerWorkflow)` —
anchored at the start with no trailing anchor, so it matches any ref of
that workflow path. `--signer-digest` pins OID .10 and is what actually
fixes the signer commit. **Pin both.** Consequence: anyone able to push a
*branch* in the signer repository mints a matching identity, so the signer
repo needs its own rulesets.

Consumer steps per `verifying-artifacts`: verify the envelope signature;
match `subject` to the artifact digest; check `predicateType`; look up the
SLSA level via `builder.id` in the roots of trust; compare builder identity,
source repository, `buildType` and `externalParameters` to expectations.
Caveat quoted: "SLSA Build L3 does **not** cover compromise of the build
platform itself."

**Two of those four expectation fields have no flag.**
`--signer-workflow`/`--signer-digest` cover builder identity and
`--source-ref`/`--source-digest` cover the canonical source repository,
but `gh attestation verify` exposes nothing for `buildType` or
`externalParameters` — and the spec asks verifiers to compare both, and
to "reject unrecognized fields in `externalParameters`". Closed on the
release path (#210): `verify-release.yml`'s verdict mode asserts the
GitHub Actions buildType, rejects unrecognised `externalParameters`
fields, and compares `externalParameters.workflow` — repository, ref
and path — against the run's own identity, all before any verdict field
is written. The equivalent consumer check is published in `runbook.md`;
it is jq over the verify call's JSON output, so no new tool enters the
belt.

**VSA** — `https://slsa.dev/verification_summary/v1`, with `verifier`,
`timeVerified`, `resourceUri`, `policy`, `verificationResult`
(PASSED/FAILED), `verifiedLevels`. Lets a consumer decide "without needing
to have access to all of the attestations" — the delegation primitive for a
cross-repo release train.

Required: `verifier.id`, `resourceUri`, `policy`, `verificationResult`,
`verifiedLevels`. `policy` **SHOULD** carry a `digest` beside its `uri`.
Optional and worth knowing: `slsaVersion` (absent means "unspecified
1.x", which is weaker than saying 1.2), `inputAttestations` (if present it
MUST list *all* attestations used, each with a digest), and
`verifier.version`. `resourceUri` **SHOULD** be the URI a consumer fetches
the artifact from; anything else obliges the producer to communicate the
expected value out of band.

The model matters as much as the schema: a VSA attests that the verifier
evaluated *"the artifact **and a bundle of attestations** against some
policy"*, and `verifier` "MUST reflect the trust base that consumers care
about". A verdict assembled without opening the provenance it summarises
is asserting a level from architecture rather than from evidence — legal,
since the policy URI can encode the architecture, but it should be
written down as what it is.

### Verified properties (new in v1.2)

v1.2 adds a page of named properties that may appear in `verifiedLevels`
alongside a level, for controls that do not fit a track:

- **`SLSA_SOURCE_TWO_PARTY_REVIEWED`** — issued only per the Source
  track's two-party-review requirements; may be issued at any source
  level. Headcount-blocked here, like Source L4.
- **`SLSA_BUILD_REPRODUCED`** — the artifact "has been reproduced by two
  or more builders", and MUST only be issued where the artifact has
  build provenance from **two or more independently operated build
  platforms** trusted by the VSA issuer.

The second has a direct consequence for this org: `repro-check` rebuilds
on the same platform from the same pinned workflow SHA — deliberately, because
skew-proofing is what makes a mismatch mean *nondeterminism*. A
same-platform rebuild is a real integrity control and it can **never**
earn `SLSA_BUILD_REPRODUCED`. Nothing here should ever label it so.

## Sigstore

Fulcio issues short-lived certificates after OIDC verification; Rekor is an
append-only log that "periodically signs the full Merkle tree along with a
timestamp"; the TUF root comes from a signing ceremony with five keyholders
across organisations.

Verification does **not** require an unexpired certificate: the verifier
checks the Rekor entry's signed timestamp to prove the artifact "was signed
while the certificate was valid". This is why an unreachable TUF repository
makes every artifact report as bad provenance rather than as a network
failure — without it a verifier cannot obtain the trusted keys at all.
`tuf-repo.github.com` and the TUF target store must be in the egress
allowlist of any job that verifies *under an egress policy*. This org runs
none (see `release.md`), which removes the failure mode rather than
managing it — but the fact is kept here because the day an allowlist
returns, omitting these two is what makes good artifacts look forged.

**rekor-monitor** offers consistency checking ("logs are tamper-evident but
not tamper-proof") and identity monitoring, configurable by certificate SAN
regex, workflow identity, or **Fulcio OID extension** — so it can watch
OIDs .9/.10 directly. It requires `id-token: write`, and its identity
matching currently supports **hashedrekord entries only**, while
attestations are DSSE.

## `actions/attest`

`attest-build-provenance` v4 is now a wrapper; new work targets
`actions/attest`.

Permissions: `id-token: write`, `attestations: write`,
`artifact-metadata: write` (plus `packages: write` for
`push-to-registry`).

Supports **multiple subjects in one call** (up to 1024) via globs,
comma/newline lists, or a checksums file: "a single attestation will be
created with references to each of the supplied subjects."

`artifact-metadata` is a fine-grained permission GA'd 2026-01-13, replacing
a `contents`-based path deprecated 2026-02-03. It enables **storage
records**, which can "only be created for artifacts built from
organization-owned repositories".

For images: `subject-name` must be the fully-qualified image name with
`subject-digest`; `push-to-registry` stores the attestation as an OCI
referrer, linked by the `subject` field of the attestation manifest.
GitHub's guidance does **not** cover multi-arch manifest lists.

**Measured 2026-08-09.** A two-architecture image was pushed to GHCR and
**only its index digest** attested. Verification then resolved as:

| Verified by | Result |
| --- | --- |
| tag — `oci://<image>:<tag>` | **passes** |
| index digest — `oci://<image>@sha256:<index>` | **passes** |
| per-architecture digest | **fails** |

So `gh attestation verify oci://…:<tag>` resolves the **index**, and
attesting the index is what covers the ordinary `docker pull <image>:<tag>`
path. Per-architecture digests are not covered by an index attestation: a
consumer who pins one architecture by digest gets a verification failure
unless those digests are attested too. Attest the index always, and the
per-arch digests as well only if consumers are expected to pin an
architecture.

Incidental, and worth knowing before reading a manifest list: `docker
buildx` adds its own `unknown/unknown` entries to the list — BuildKit's
provenance and SBOM attestations, which are a different mechanism from
GitHub's and are not what `gh attestation verify` reads. Filter on
`.platform.architecture` when extracting per-arch digests, or they appear
as phantom platforms.

The push itself used **preinstalled `docker buildx` and `docker login`**,
no `docker/*` actions, so the organisation's Actions allowlist does not
need to grow to build images.

## Reusable workflows

- Permissions "can only be maintained or reduced—not elevated". Docs say an
  excess request is constrained to the caller's level; this org's operational
  experience records it killing the run as `startup_failure` with no jobs,
  annotations or log. Design against the harsher behaviour and publish the
  exact scope list callers must grant.
- Nesting limit **10 levels**; loops disallowed.
- `secrets: inherit` reaches only the **directly** called workflow.
- **Environments are documented as not recommended** for reusable
  workflows: "Environment secrets cannot be passed from the caller
  workflow", and an environment's own secret wins over one passed in.

### Environment scoping, measured

The documentation does not say whose environment applies when a reusable
workflow's job declares one, and gives no exhaustive list of the keywords
a calling job may carry. Both were measured in the lab on 2026-08-09, with
`release-lab` calling a probe in `signer`, a `publish` environment in each
repository, and a different variable value in each.

**A reusable workflow's job may declare `environment:`, and it resolves
against the caller.** The probe printed the *caller's* value, and the
minted OIDC token carried `"environment": "publish"`. So the environment
reaches the identity, not merely the job — environment-scoped trusted
publishing works from a shared workflow.

**A job that calls a reusable workflow may not declare `environment:`.**
Adding it produced an immediate run failure with zero jobs and no
annotation — "This run likely failed because of a workflow file issue" —
the same shape as the permissions `startup_failure` recorded above.

Together these are one conclusion, and it is the opposite of what "not
recommended" suggests: **declaring `environment:` inside the reusable is
the only way to environment-scope a reusable workflow at all**, and it
already resolves in the right place. A signing or publishing workflow
therefore declares the environment itself, and every caller supplies the
`publish` environment that gates it. A `publish` environment in the shared
repository would never resolve and should not exist there, because its
presence would imply a protection it does not provide.

The same run confirmed the claim split by measurement rather than by
source reading: `workflow_ref` named the caller's entry file, and
`job_workflow_ref` named the reusable at its pinned SHA.

**Environment *secrets* do not cross the boundary, and the documentation
is wrong about this.** A second run put junk markers in a caller's
`publish` environment and asked the reusable job which arrived:

| Case | Result |
| --- | --- |
| environment secret, undeclared in `workflow_call` | **unreachable** — empty |
| name shared with a declared, passed secret | **the passed value wins** |

The second row contradicts the documented behaviour, which states that
with `environment` at the job level "the environment secret will be used,
and not the secret passed from the caller workflow". It was not. The log
corroborates the mechanism: the passed marker appeared masked as `***`,
proving it was registered as a secret, while the environment marker
appeared in plain text — so the environment's secrets were never loaded
into the job at all.

So a reusable workflow's job inherits three things from the caller's
environment — protection rules, **variables**, and the OIDC `environment`
claim — and **not** its secrets. The split between variables and secrets
is sharp and is documented nowhere.

Two consequences, in opposite directions:

- **Environment-scoped trusted publishing is unaffected**, because it
  needs the `environment` claim and no secret at all. Registry publishing
  from a shared reusable can be pinned to `environment: publish`.
- **A secret cannot be protected by an environment through a shared
  workflow.** Repository scoping (`visibility: selected` on an
  organisation secret) is the only mechanism that restricts *who can read*
  one; an environment restricts only *when a job runs*. Anything asserting
  that a key "lives behind" an environment while being consumed by a
  reusable workflow is asserting something the platform does not do.

Note also that `actionlint` models the reusable `secrets` context as
closed over the declared `workflow_call` secrets and rejects both the
dotted and indexed forms of an undeclared name. Its model turned out to
match the runtime, so the org's own gate would have prevented writing the
unreachable form in the first place.

## Distribution and evidence

SLSA `distributing-provenance` gives a normative naming SHOULD: provenance
"SHOULD have a filename that is directly related to the build artifact
filename", e.g. `<filename>.intoto.jsonl`. That is also exactly what
Scorecard's Signed-Releases check recognises for full marks — one
convention satisfies both.

This org ships `attestations-<class>.intoto.jsonl`: one multi-subject
bundle per artifact class, not one file per artifact. That satisfies
Scorecard and the "publish in at least one place" MUST, and it is a
deliberate deviation from the naming SHOULD — a single attestation may
carry up to 1024 subjects, and per-artifact copies of the same bundle
would be redundancy, not information. Recorded so the deviation is a
decision on the page rather than an omission.

Three endorsed venues: source-repository releases, package-registry sidecar
or OCI referrer (preferred long-term, since "clients already trust the
package registry"), and transparency logs.

**SBOM format**: GitHub natively produces **SPDX** (dependency graph
export, REST API); CycloneDX is third-party there. `actions/attest` accepts
either.

## Immutable releases

GA 2025-10-28. Enable at repository **or organization** level. Only new
releases are affected — not retroactive — and **disabling later does not
un-immutable** anything created while it was on.

On publish, assets cannot be added, modified or deleted, and the git tag is
locked to its commit and cannot be deleted while the release exists.
Publishing also **auto-generates a release attestation**: a GitHub-issued
signed binding of tag, commit SHA and asset digests, verified with
`gh release verify [<tag>]` and `gh release verify-asset [<tag>] <file>`.
This complements build provenance — theirs says "these assets belong to
this release", ours says "these bytes came from this build".

## Registries

### Which workflow a registry pins

Both registries match the **caller's entry workflow**, never the reusable
workflow that contains the publish step. The distinguishing claim is
`workflow_ref` — "the ref path to the workflow" — as against
`job_workflow_ref`, "for jobs using a reusable workflow, the ref path to
the reusable workflow". The first names the file the run started from; the
second names the file the job's steps came from, and is what Fulcio records
in OID .9.

crates.io's `GitHubClaims` deserialises exactly seven claims —
`repository_owner_id`, `repository`, `workflow_ref`, `environment`,
`event_name`, `run_id`, `sha` — and derives the configured filename by
regex over `workflow_ref`. `job_workflow_ref` is not among them and is
never read. npm documents the same behaviour, as a hazard: validation
"checks the calling workflow's name" rather than the one holding the
publish command.

Consequence for this org, and it is structural: **a registry publish step
may live in a shared reusable workflow.** What a registry pins is the
caller's repository plus the caller's entry filename, so that filename is
canon — `publish.yml`, exactly, in every repository, permanently — while
the steps behind it are free to be shared and to move.

An earlier draft of issue #28 concluded the opposite, and on that basis
placed registry OIDC in each caller's own publish job, accepting
`id-token: write` alongside caller-supplied code. That concession was
unnecessary; see `docs/release.md`.

### The registries themselves

**crates.io** — trusted publishing validates `owner`, `repo`, `workflow`
(filename, in `.github/workflows/`) and optional `environment`. First
publish of a crate must be manual. Tokens are short-lived (<1h). As of
January 2026, owners **can enforce trusted publishing and disable API-token
publishing per crate**, and `pull_request_target` / `workflow_run` triggers
are **blocked** from trusted publishing — rejected by name in the exchange
handler, with `push`, `release` and `workflow_dispatch` named as the
supported alternatives. That set is a subset of the four the GitHub Actions
buildType permits, so a trigger legal for provenance is not automatically
legal for publishing. Provenance display is not a shipped surface.

The exchanged token is **single-use**: the JWT's `jti` is recorded on
exchange and a replay is rejected, and the record is deliberately kept
alive past the JWT's `exp` for the full validation leeway so cleanup cannot
reopen the window. A resumed or retried publish must therefore mint a fresh
OIDC token; caching one across steps or runs fails on the second use.

**npm** — registers the exact workflow filename (case-sensitive), optional
environment, requires `id-token: write`. Token publishing can be disabled
via "Require two-factor authentication and disallow tokens". npm generates
**its own** provenance and publish attestations automatically under trusted
publishing, associated with the **caller's** workflow, and
`npm audit signatures` checks those. A single org-wide identity is therefore
not achievable on npm; document both verification paths. npm provenance also
requires a public repository — it is not generated from a private one even
when the package itself is public.

## Scoring frameworks

**Scorecard** — fifteen checks can reach 10/10 here; Packaging is
structurally **inconclusive (-1, excluded from the aggregate)** for every
repo publishing through the shared orchestrator — the check greps the
caller's own workflows for publish commands, and the caller's entire
publish surface is one `uses:` line (measured by local preflight; the
same gap made Scorecard special-case slsa-github-generator).
Signed-Releases awards 8
for signatures and **10 only when `*.intoto.jsonl` provenance is present**;
recognised patterns are `*.minisig`, `*.asc`, `*.sig`, `*.sign`,
`*.sigstore`, `*.sigstore.json`, `*.intoto.jsonl`. There is an **SBOM
check** requiring the SBOM as a **release artifact** for 10 (5 if only in
the pipeline). Branch-Protection supports rulesets as well as classic
protection; its tiers are 3/6/8/9/10 with tiers 4–5 requiring two reviewers.
CII-Best-Practices scores 10 only at Gold, 7 at Silver. Webhooks is
Critical risk and requires token authentication.

**Fuzzing credits Rust `cargo-fuzz` natively, and the published check
documentation is wrong about this.** The docs list Go, Haskell,
JavaScript/TypeScript, Erlang, C# and F#, and omit Rust — but
`checks/raw/fuzzing.go` carries a `clients.Rust` entry matching the
function pattern `libfuzzer_sys` across `*.rs`, and `internal/fuzzers`
names the result `RustCargoFuzzer`. The check is binary: any recognised
fuzzer scores the full 10.

Consequence, and it removes a planned cost rather than adding one: a Rust
repository with `cargo-fuzz` targets already scores Fuzzing 10/10, so
**ClusterFuzzLite is unnecessary** for that score. Its cost — a
`.clusterfuzzlite/` directory, a Dockerfile on `gcr.io/oss-fuzz-base/
base-builder:v1` (a floating tag, against this org's pinning rule), new
`gcr.io` egress and a Docker dependency — buys nothing here. A repository
with no code to fuzz, such as the continuous-archetype image repo, has no
route to this score and is capped at 0 by subject matter rather than by
engineering.

**OpenSSF Best Practices** — Silver MUSTs include `signed_releases`,
**`build_repeatable`** ("exactly the same bit-for-bit result"),
`test_statement_coverage80`, `regression_tests_added50`,
`static_analysis_common_vulnerabilities` and `access_continuity`
(documented succession). `bus_factor` is only SHOULD at Silver. Gold
requires `two_person_review`, `bus_factor` ≥2, `contributors_unassociated`,
90% statement and 80% branch coverage, and `build_reproducible`.

**OSPS Baseline** — three levels (L2's definition requires ≥2 maintainers),
eight families: AC, BR, DO, GV, LE, QA, SA, VM. Build & Release: BR-01
prevent untrusted input (BR-01.03 "prevent privileged credential access
from untrusted code snapshots" is the Ultralytics control), BR-02 unique
version identifiers, BR-03 encrypted channels, BR-04 change log, BR-05
standardised dependency tooling, BR-06 signatures and hashes, BR-07
secrets. Elsewhere: DO-03.01/.02 require documented verification of release
integrity *and* author identity; LE-01.01 requires DCO or CLA; VM-01.01
requires a disclosure policy with a stated response timeframe; QA-02.02
requires SBOMs with compiled released assets.

**SECURITY-INSIGHTS** — schema v2.2.0, at repo root or `.github/`.
`ReleaseDetails` requires `automated-pipeline` and `distribution-points`,
and takes an `attestations` array whose entries carry `predicate-uri`,
`location` and a usage `comment`. `SecurityPosture` takes `assessments` and
a `tools` array with type, name, version, rulesets, integration status and
results as attestations.

## Tooling notes

**CodeQL supports Rust** — public preview June 2025, and scanning Rust
without builds GA 2025-10-14, so `build-mode: none` works. Actions
workflows are also a supported language.

**zizmor** — this org runs `--offline --persona=pedantic`, which enables the
pedantic audits but excludes five that need the GitHub API:
`impostor-commit`, `known-vulnerable-actions`, `ref-confusion`,
`typosquat-uses`, `stale-action-refs`. Those belong in a scheduled online
audit. `forbidden-uses` supports an allow/deny list for `uses:` — an
unexploited lever for an org-wide action allowlist. `use-trusted-publishing`
flags the *absence* of trusted publishing.

**Renovate** — `config:best-practices` extends `config:recommended`,
`docker:pinDigests`, `helpers:pinGitHubActionDigests`, `:configMigration`,
`:pinDevDependencies`, `abandonments:recommended`,
`security:minimumReleaseAgeNpm` (3 days, npm only) and
`:maintainLockFilesWeekly`. This org's global `minimumReleaseAge: 7 days` is
broader and stricter. The mise manager detects `mise/config.toml` and
supports `mise.lock`, but refreshing it runs `mise lock`, which requires
**`allowedUnsafeExecutions`** — a **self-hosted-only** setting that "must
reside in bot/admin configuration … never in repository `renovate.json`".
On the hosted App this is not ours to set.

**ClusterFuzzLite** — needs `.clusterfuzzlite/` with `project.yaml`, a
Dockerfile `FROM gcr.io/oss-fuzz-base/base-builder:v1` (a floating tag), and
a `build.sh` linking targets with `$LIB_FUZZING_ENGINE` into `$OUT`.
Supports Rust. Scorecard's Fuzzing check credits it.

**OpenVEX** — JSON-LD; statements carry a vulnerability id, affected
products by purl, a status (`not_affected`, `affected`, `fixed`,
`under_investigation`) and a justification. Deliberately SBOM-agnostic.
`vexctl` can create, merge and **attest** documents, so a VEX file can be an
attestation subject. This is the format OSPS VM-04.02 asks for.

**Reproducible builds, Rust** — `--remap-path-prefix` (or
`CARGO_ENCODED_RUSTFLAGS`), `CARGO_INCREMENTAL=0`,
`CARGO_CACHE_RUSTC_INFO=0`, `SOURCE_DATE_EPOCH`, and profile determinism via
`CARGO_PROFILE_<name>_*`. Note `CARGO_PROFILE_RELEASE_STRIP=false`:
stripping discards the section `cargo-auditable` writes.

**`slsa-github-generator` is deprecated** as of 2026-08-07 — "no longer
actively maintained", pointing users to GitHub artifact attestations, with
verification moving from `slsa-verifier` to `gh attestation verify`.

## Operational constraint: OIDC subject claims change on transfer

From 2026-04-23, the default subject claim gained immutable identifiers:

```text
old: repo:octocat/my-repo:ref:refs/heads/main
new: repo:octocat@123456/my-repo@456789:ref:refs/heads/main
```

New repositories adopt it automatically from **2026-07-15**, and
**renames and transfers after that date also adopt it**. Existing
repositories are unchanged unless opted in, and a preview endpoint shows
the future claim prefix.

Anything asserting on the subject claim must be re-checked before a
transfer. crates.io validates discrete claims rather than the raw subject,
so it may be unaffected — verify rather than assume.

**Measured 2026-08-09, and it does not match the changelog.** Renaming
`edtf-release-lab` to `release-lab` — a rename well after the cutover date —
left the repository reporting:

```json
{"use_default": true,
 "use_immutable_subject": false,
 "sub_claim_prefix": "repo:monumental-archive@314831567/release-lab@1327949748"}
```

from `GET /repos/{owner}/{repo}/actions/oidc/customization/sub`. So the
rename did **not** flip `use_immutable_subject`, though the endpoint does
preview the prefix the new format would produce.

Two readings were possible — the flag may reflect only a deliberate opt-in
while the issued token differs, or automatic adoption may not behave as the
changelog describes.

**Settled 2026-08-09 by reading a real token.** The environment probe
minted one in `release-lab` and its subject was:

```text
repo:monumental-archive@314831567/release-lab@1327949748:environment:publish
```

That is the **new immutable format**, while the API went on reporting
`use_immutable_subject: false`. The first reading is therefore the correct
one: the flag tracks deliberate opt-in only, and automatic adoption on
rename has already happened irrespective of what it says. **Trust the
token, not the field.**

The consequence is milder than feared, at least for crates.io: its
`GitHubClaims` never deserialises `sub` at all, matching on `repository`,
`repository_owner_id` and `workflow_ref` instead, so no change to the
subject format can break its trusted publishing. npm is unverified and
must not be assumed to behave the same way.

## Questions that require an experiment

Documentation does not answer these; the release lab does.

1. ~~Can a shared signing reusable workflow be environment-scoped, and
   whose repository's environment applies?~~ **Answered** — yes, and the
   caller's; see "Environment scoping, measured".
2. ~~For a multi-arch image, should the attestation subject be the index
   digest or the per-arch digests, and what does
   `gh attestation verify oci://…:tag` resolve?~~ **Answered** — the
   index; see "Measured 2026-08-09" under `actions/attest`.
3. ~~What predicate type does GitHub's automatic release attestation
   use?~~ **Answered, measured 2026-08-12** —
   `https://in-toto.io/attestation/release/v0.2`, read from the
   attestations store for release-lab v0.20.1 and the canon's own
   v1.13.0. It appears beside our provenance and VSA on every published
   subject, including assets we do not attest ourselves (SBOM, VEX),
   which is what makes it a useful independent binding rather than a
   duplicate.
4. Does the OIDC subject-claim format change break either registry's
   trusted publishing configuration on transfer? **Answered for
   crates.io** — no, it never reads `sub`. npm still open.
5. Does the hosted Renovate bot permit the `mise` unsafe execution, i.e.
   can it refresh `mise.lock`?

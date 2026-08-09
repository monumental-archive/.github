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
provenance attestations, for which no off-the-shelf tooling was found.

## Dependency track (draft)

L1 inventory of build dependencies · L2 all known vulnerabilities triaged
before release · L3 dependencies consumed from producer-controlled
locations · L4 enforced secure ingestion policy.

## Build Environment track (draft)

L1 signed build-image provenance · L2 attested instantiation (vTPM, Secure
Boot) · L3 hardware-attested. Not required for any Build level, and on
GitHub-hosted runners it is a property of the platform, not of us.

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

**VSA** — `https://slsa.dev/verification_summary/v1`, with `verifier`,
`timeVerified`, `resourceUri`, `policy`, `verificationResult`
(PASSED/FAILED), `verifiedLevels`. Lets a consumer decide "without needing
to have access to all of the attestations" — the delegation primitive for a
cross-repo release train.

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
allowlist of any job that verifies.

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

## Distribution and evidence

SLSA `distributing-provenance` gives a normative naming SHOULD: provenance
"SHOULD have a filename that is directly related to the build artifact
filename", e.g. `<filename>.intoto.jsonl`. That is also exactly what
Scorecard's Signed-Releases check recognises for full marks — one
convention satisfies both.

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

**Scorecard** — sixteen checks can reach 10/10. Signed-Releases awards 8
for signatures and **10 only when `*.intoto.jsonl` provenance is present**;
recognised patterns are `*.minisig`, `*.asc`, `*.sig`, `*.sign`,
`*.sigstore`, `*.sigstore.json`, `*.intoto.jsonl`. There is an **SBOM
check** requiring the SBOM as a **release artifact** for 10 (5 if only in
the pipeline). Branch-Protection supports rulesets as well as classic
protection; its tiers are 3/6/8/9/10 with tiers 4–5 requiring two reviewers.
CII-Best-Practices scores 10 only at Gold, 7 at Silver. Webhooks is
Critical risk and requires token authentication.

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

Two readings are possible — the flag may reflect only a deliberate opt-in
while the issued token differs, or automatic adoption may not behave as the
changelog describes. Settling it requires reading the `sub` claim of an
actual OIDC token, not an API field.

Operationally: **do not assume a rename or transfer flips the format, and
do not assume it does not.** Query this endpoint before and after every
transfer, and confirm against a real token before relying on either
answer.

## Questions that require an experiment

Documentation does not answer these; the release lab does.

1. Can a shared signing reusable workflow be environment-scoped, and whose
   repository's environment applies?
2. For a multi-arch image, should the attestation subject be the index
   digest or the per-arch digests, and what does
   `gh attestation verify oci://…:tag` resolve?
3. What predicate type does GitHub's automatic release attestation use?
4. Does the OIDC subject-claim format change break either registry's
   trusted publishing configuration on transfer?
5. Does the hosted Renovate bot permit the `mise` unsafe execution, i.e.
   can it refresh `mise.lock`?

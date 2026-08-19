# Release engineering, org canon

How every repository in this organisation releases. This document is
authoritative: repositories conform to it, not the other way round. It was
designed greenfield in issue #28 from the three existing pipelines — most of
the invariants below were proved (or paid for) in edtf and iiif-server before
being promoted to canon here.

## The two archetypes

Every repository releases in exactly one of two ways. Archetype-shaped
decisions are made once, here.

**Versioned** (edtf, iiif-server, the future map normaliser): a release PR
carries the version decision; merging it is the commitment point. The
pipeline mints an umbrella `v*` tag, and a tag-triggered publish workflow
builds, proves, signs and publishes. Consumers get semver, a changelog, a
citable DOI and signed release assets.

**Continuous** (monumental-archive-db): digest publish on merge plus a weekly
rebuild, no tags, no version surface — the artifact's "version" is its pin
set, and scheduled republishing is the remediation path for unpinnable base
layers. The machinery is
[`continuous.yml`](../.github/workflows/continuous.yml): the oci-image class
build reused wholesale (which carries no cache, so every scheduled run is a
full rebuild by construction), a guard that refuses *tags* — the inverse of
the versioned guard — and the same signing identity over the index digest.
The caller stub is
[`workflow-templates/continuous.yml`](../workflow-templates/continuous.yml).
Continuous repositories share the toolbelt, the gate and the signer, and
nothing else in this document applies to them.

A repository whose release needs fit neither archetype is a design question
for this repo, not a licence to improvise.

## Phase 1: the release PR (stele derive)

The version is derived from conventional commits by **stele derive
version** — never typed by a human. The notes convention (groups, order,
URLs) lives once, in
[`release/prepare-release.sh`](../release/prepare-release.sh); the
load-bearing bump rules (pre-1.0 breaking changes bump the minor,
housekeeping commits produce no release) are stele derive's own
defaults, specified in its docs and tests, and are not repo-tunable.

A derivation step rather than a release tool, org-wide: release-plz
cannot drive workspaces whose interdependent crates are unpublished
(release-plz#2595, verified open and unfixed in 0.3.160),
release-please has no model for `[workspace.package]` inheritance at
all, and dist must own the binary build, which collides with hardened
runners and with publishing binaries extracted from the container
image. Meanwhile the tags-only job a release tool would do — bump,
changelog, release PR, tag, draft release — is exactly what the three
small scripts do around `stele derive` (the flow git-cliff carried
from iiif-server until stele's derive port replaced it, .github#505).
One flow, no structural bugs, no tool that wants to own the pipeline.

The machinery is shared: callers pin
[`release.yml`](../.github/workflows/release.yml) by SHA (the usual doubled
`.github` path), which runs the canonical scripts in
[`release/`](../release/) at that same SHA. The caller stub is
[`workflow-templates/release.yml`](../workflow-templates/release.yml).

**The canon itself is a versioned repository** and releases with this same
machinery, called locally
([`self-release.yml`](../.github/workflows/self-release.yml), the gate.yml
pattern): a repository cannot SHA-pin itself without being permanently one
commit behind, and under a local `uses:` the canon tree arrives via
`$/.github/actions/canon` at the released commit itself — so the atomic
pin holds by construction. (For remote callers the same `$/` resolution
delivers the tree at the caller's pinned ref; the two earlier carriers —
the `github.job_workflow_sha` context, which evaluated empty and silently
cloned main, and a release-time stamp the tag-mint App could not commit —
are #158 and #165.) Canon tags
are what consumers pin (`@<sha> # vX.Y.Z` — the comment is what Renovate's
`github-actions` manager reads and rewrites together with the SHA), what
the lefthook remote refs (`ref: vX.Y.Z`), and what the shared preset
reference carries (`github>monumental-archive/.github#vX.Y.Z`). One
release moves all three surfaces; Renovate fans it out (#133). The canon's
phase 2 is the **source-archive class**
([`self-publish.yml`](../.github/workflows/self-publish.yml)): its
artifact is the tagged tree, archived deterministically, signed through
the one signer, and published with its evidence bundle like every other
class. Its semver surface is named in
[`MAINTENANCE.md`](../MAINTENANCE.md).

### The version source

Phase 1 asks one question of a repository — *where does the current
version live?* — and answers it by detection, never configuration:

- **Cargo workspace** (`Cargo.toml` present): `[workspace.package].version`
  is the source; the release commit bumps it, the internal dependency
  constraints, the lockfile and `CITATION.cff`, and proves the tree still
  resolves.
- **No manifest** (the canon, and any docs/config/image-only repository):
  the `v*` tags are the source. Nothing mirrors the version into files, so
  the release commit carries only `CHANGELOG.md` (plus `CITATION.cff`
  where present), and the tag step reads the version from the release
  commit's subject — which the tag guard already authenticates.
- **Anything else** (package.json, single-crate Cargo, pyproject): an
  unbuilt branch of this contract. It is added in
  [`release/`](../release/) — at the version read, the version write and
  the release-commit file list, the only three points that vary — when a
  real repository needs it, and never speculatively. Identifying which
  branch a repository hits is a migration-playbook step; hitting an
  unbuilt one is a named prerequisite, not a release-day surprise.

Phase-1 rules, all proven in iiif-server:

- An ordinary push to `main` only refreshes the release PR. **Merging the
  release PR is the only commitment point.**
- **There is exactly one release branch, `release/next`, and one open
  release PR.** The branch is deliberately not keyed on the version:
  a version-keyed branch opens a second pull request whenever new commits
  change the bump, abandoning the first at a version that will never ship.
  The version belongs in the title, body and commit, not the ref.
- The release PR's commit is created through the GitHub API
  (`createCommitOnBranch`), so it is signed by GitHub and satisfies
  `required_signatures`; the script asserts the commit is `verified` rather
  than trusting that it is.
- The version bump script updates every place the version lives —
  `[workspace.package].version`, `[workspace.dependencies]` constraints,
  `CITATION.cff` — and fails loudly if any substitution misses.
- Workspaces inherit their version via `[workspace.package]`. That is the
  canonical shape; hand-coupled per-member versions are a conformance gap.

## The tag

**Umbrella `v*` only.** One tag per release, workspace-wide. Per-crate tags
are redundant under lockstep versioning and multiply the ruleset surface.

Tags are minted exclusively by the org's **tag-minting GitHub App** — humans
never push release tags, and the two historical PAT secrets
(`RELEASE_PLZ_TOKEN`, `RELEASE_TOKEN`) are retired. The mint is its own tiny
job holding a `contents: write` token scoped by
`actions/create-github-app-token`; the release-PR bot never holds tag-push
power.

The App's key does **not** live behind the protected `publish`
environment, as an earlier draft of this document asserted, and — measured
in the lab — **it cannot**. A reusable workflow's job inherits the
caller's environment protection rules, variables and OIDC `environment`
claim, but not its secrets ([`slsa-reference.md`](slsa-reference.md)). An
environment governs *when a job runs*, never *who can read a secret*.

The key lives as an organisation secret with `visibility: selected`,
readable only by the repositories ticked onto it — narrowing it required
re-supplying the encrypted value, so the App key was rotated first
(2026-08-09) and verified through both token paths. A repository joining
the release flow at transfer is added to the selection list; nothing is
readable org-wide by default.

The default `GITHUB_TOKEN` is never used to push a tag: tags it pushes
trigger no workflows, and a release that silently triggers nothing looks
exactly like a success.

**The ruleset lock**: `v*` tag creation is restricted org-wide with the App
as sole bypass actor (see [`rulesets.md`](rulesets.md)).
The lock and the pipeline land together — either alone is a lockout or a
hole. Break-glass for a dead App: an org admin disables the ruleset, in a
change that is itself recorded here.

Phase 1 and the lock are **proven end to end** in the release lab
(2026-08-09): two consecutive clean release cycles, a human tag push
rejected with `GH013`, and the App minting through its bypass under
`enforcement: "active"`. Proving it caught four defects first — three in the
shared machinery, one in the App's grant — none of which any local linter
could have found. The general lesson, recorded because it will recur: **a
shared workflow whose only exerciser lives in its own repository is
untested for the cross-repository case**, which is precisely the case every
consumer runs.

## Phase 2: publish, prove, sign

Triggered by the `v*` tag, so `github.ref` *is* the tag and provenance names
immutable bytes — a workflow on `main` can only attest a moving pointer
(edtf's v1.0.0 attestations are permanently wrong this way; Sigstore is
append-only).

The step order is not rearrangeable:

> build → smoke test → push → pull the published bytes back and prove them →
> attest → verify the attestation as a stranger would → publish the release

**That order is owned by this repository, not by the repositories that
release.** A caller's entire phase-2 obligation is one file holding one
`uses:` line and no `run:` step:

```yaml
# .github/workflows/publish.yml, in every versioned repository
jobs:
  publish:
    uses: monumental-archive/.github/.github/workflows/publish.yml@<sha> # vX.Y.Z
    with:
      class: rust-crate
```

An order written into each caller is an order each caller can rearrange,
and a repository that signed before it verified would go green while
asserting something false. Reusable workflows nest ten levels deep, so the
shared orchestrator calls the per-class build, the verifier, the signer and
the publisher itself, and the order exists in exactly one place.

### What stays in the calling repository

Three things, and only three:

- **The entry workflow file.** GitHub runs workflows from the repository
  they trigger in; nothing moves that. Its **filename is canon** —
  `publish.yml`, exactly, everywhere — because both registries pin the
  *caller's* entry filename rather than the reusable's
  ([`slsa-reference.md`](slsa-reference.md)). Renaming it breaks trusted
  publishing in a way no local check catches.
- **The `publish` environment.** Environments are repository objects.
- **Build inputs**: `Cargo.toml`, `Dockerfile`.

Every step, every ordering constraint and every permission lives here or in
`signer`.

### A shared workflow's permissions are a public contract

Changing what a shared workflow requires is a **breaking change for every
repository that calls it**, and it breaks in the least debuggable way
GitHub offers: `startup_failure`, no jobs, no annotation, no log.

The mechanism is the one recorded above — a `uses:` job with no
`permissions:` block takes the *workflow-level* default of the file it is
written in, never the caller's grant. So the moment a shared workflow asks
for one scope more than a silent caller's default provides, the callee is
requesting an elevation and the run dies before anything starts.

This is the exact inverse of the toolbelt property, and the contrast is
worth holding onto. Adding a `lint:*` task to the belt enforces a tool in
every repository with **no repository change at all**. Adding a
*permission* to a shared workflow requires editing **every** repository,
and breaks all of them until each one is edited by hand. Same
architecture, opposite blast radius — and it cannot be fixed centrally,
because a shared workflow cannot grant itself anything.

Observed: adding `contents: read` to `release.yml`, so that the derivation
could authenticate its GitHub API calls, took the release lab down within
a minute of merging.

So:

- **Decide a shared workflow's permission set once, deliberately.** Treat
  an addition as a breaking change, not a fix.
- **Prefer a design that needs no caller grant** where the cost is
  acceptable. Every grant is a coupling.
- **Every `uses:` job states its own permissions**, in callers as well as
  in reusable workflows, so the contract is visible in a diff rather than
  discovered in an unlogged failure. `lint:nested-permissions` enforces
  it.

### An input's forwarding class is declared where it is born

Two archetypes reuse each class build wholesale, and GitHub gives a
caller no way to say "forward everything" — every `with:` block is a
hand-copied list, and omitting an optional input is legal YAML that
`actionlint` validates and `zizmor` has no opinion on. That is the #299
failure class: a `prepare` input added to the oci-image build was
forwarded by `publish.yml`'s two legs and missed by `continuous.yml`,
and the divergence surfaced as a red run on a board, found by a human —
the detection method this org keeps trying to retire (#305).

The contract is therefore declared **once, at the declaration**, not at
the call sites that consume it. Every input and secret of a
locally-called `workflow_call` workflow carries a forwarding class:

- `# forwarding: universal` — every local caller must set the key. Any
  value counts, literals included: the repro leg's `repro: true` and
  repro-check's `smoke-test: ""` both satisfy it, and both *say* what
  they mean instead of leaning on a default.
- `# forwarding: discretionary — <reason>` — omission is legal; the
  default is the contract. Orchestrator-owned knobs (`repro`, `dry-run`,
  `ref`) live here.

An **unmarked input fails the gate**, so the classification #299
silently skipped is structurally unskippable at authoring time.
Consumer-surface workflows whose defaults *are* the API (`publish.yml`,
`verify-release.yml`) declare `# forwarding-default: discretionary`
once, file-level; a file-level default is never allowed to be universal.
The marker is a comment because GitHub's `workflow_call` schema rejects
extension fields — the platform ceiling, not a preference; the same
trade the `capability-boundary:` marker already makes.

`lint:input-forwarding` enforces both halves in `ci` — deterministic,
tracked files only. `fix:input-forwarding` is its write-mode sibling:
missing universal forwarding lines are machine-written, never
hand-authored, so drift can only exist as an uncommittable dirty tree.
Two interlocks close the chain, and neither may be "simplified" away:
the lint forces the forwarding line while **actionlint**, ahead of it in
the same gate, forces the caller-surface declaration any forwarded
`${{ inputs.* }}` refers to; and the lint's error-line format is the fix
task's parsing API, so the two change together. Scope is local
`uses: ./` call sites only — cross-repo consumers pin the archetype
entry points and never reach the class builds directly.

### The capability split

The boundary is between **jobs that run caller-supplied code and jobs that
do not**, and it falls inside this repository rather than between
repositories:

| Job | Caller code | Holds |
| --- | --- | --- |
| `build-*`, per artifact class | yes | `contents: read` |
| verify, publish, attach | no | `contents: write`, `packages: write` |
| `sign.yml` in `signer` | no | `id-token` + attestation writes |

Keeping `contents: write` out of a build job is not about token scope: that
token is the caller's own, over the caller's own repository, which the
caller could grant itself regardless. It is about **ordering**. "Attestation
happens last, and only on proof" is enforceable only while the job that
publishes cannot be reached from caller code — the SLSA L3 *unforgeable*
requirement doing real work rather than ceremony.

- Every phase-2 job refuses non-tag refs and refuses a tag whose version
  disagrees with the manifest (tag must point at the release-PR merge
  commit).
- Releases stay **drafts** until phase 2 finishes; immutability applies at
  publish, and a dead run leaves nothing public.
- **Attestation happens last, and only on proof.** A signature made before
  the published bytes are verified asserts something false, permanently.
- Registries use trusted publishing (OIDC) only; token publishing is
  disabled per crate/package once flows are proven. A crate's first publish
  is manual (crates.io limitation).
- Signing goes through **`signer`** — named for what it does, not for
  SLSA's `builder.id` field, which means "whoever generated the
  provenance" and has misled this project more than once. The shared build
  workflow hashes what it produced, the orchestrator passes the subject
  manifest on, and the caller-code-free workflow in `signer` signs it —
  never the artifacts themselves, and never a checkout of anyone.
  One certificate identity for every org artifact; `id-token: write` lives
  there and never here.
- The version DOI is minted by [`mint-doi.yml`](../.github/workflows/mint-doi.yml)
  through Zenodo's REST deposition API — after the release is published,
  after proof, like everything else. (The webhook flip-switch integration
  is deliberately not used: webhooks are Scorecard's one Critical-risk
  check, and the REST job is token-auth and in the pipeline where its
  failure is visible.) `ZENODO_TOKEN` is an organisation secret,
  `visibility: selected`, one production token granted per repo. There
  is no sandbox lever (#316): the lab's rehearsal releases mint real
  version DOIs under its one concept record — that pile-up is the
  design, because a rehearsal against a mirrored sandbox API never
  proves the path the permanent record takes.

There is one shared build workflow per **artifact class** — `rust-crate`,
`rust-binary`, `go-binary`, `oci-image`, `wasm-npm`, `pgrx-extension`,
`source-archive` — and callers
declare inputs, not steps. An earlier draft of this document kept
publish workflows per-repo,
on the grounds that pgrx matrices and musl statics were legitimate quirks.
They are class-shaped, not repo-shaped: a matrix belongs to the class that
needs one, and a repository that wants a different one is a design question
for this document. Keeping them per-repo also forfeited Build L3, since the
route to it is precisely that the signing identity sits behind a workflow
no caller controls.

Hardening is therefore a property of the shared workflows rather than a
skeleton every repository copies and drifts from: `permissions: {}` with
job-scoped grants, every `uses:` SHA-pinned under the organisation's
`sha_pinning_required` policy, tool caches disabled in any job whose
output is signed, and the evidence bundle — one checksum filename, one SBOM
format, Sigstore bundles — attached to the release in the shape Scorecard's
Signed-Releases check recognises.

### The cold-build rule

Caches are unattested, writable-from-any-branch inputs injected straight
into the build — cache poisoning is the classic attack provenance does
not capture. The rule (#117): **caches are permitted only where a human
is waiting and nothing is signed**. Every path that signs or publishes
builds cold; the runner image is a named pin (`ubuntu-24.04`, rolled
by Renovate as a visible diff, enforced by `lint:runner-pin`); the
toolchain — components included — is
fully installed before any task runs, which is also what retired the
rustup-race serialization (`wait_for`) from the belt. `lint:cold-attested`
enforces the rule mechanically: a workflow that uses a cache carries an
`unattested-path:` marker saying why its path signs nothing, or it fails
the gate. Base images are digest-pinned (`lint:from-digests`), org-
approved before use (`base-attest.yml`; the pgrx build legs verify the
approval and fail closed), and `audit:attestations` proves weekly that
nothing published lacks its evidence set — the difference between "we
attest" and "nothing ships unattested".

### The repro gate

Every irreversible step — the crates.io upload (yank-only), the npm
upload (72-hour unpublish window), every ghcr tag, every append-only
Sigstore entry, the release itself — sits downstream of a full-width
bit-for-bit proof (#118): each class builds twice, independently, in
the same run, and the `repro-gate` job compares sha256 subject
manifests for the file classes and per-arch (oci) / per-major-index
(pgrx) digests for the image classes. A mismatch fails the release
outright; there is no warn-and-ship path, because a warn path converts
the control into a log line. Skew-proofing is free by construction:
both builds resolve at the caller's pinned SHA in one orchestrator
run, so a mismatch can only mean *nondeterminism*.

The rewiring this required: the registry publishes were extracted from
their build workflows (`publish-rust-crate.yml`, `publish-wasm-npm.yml`
— trusted publishing survives because both registries pin the CALLER's
entry filename, `workflow_ref`, never `job_workflow_ref`); image
builds stop after untagged digest pushes, with the oci index assembled
and tagged exactly once post-gate (`assemble-oci-index.yml`) and the
pgrx per-major tags applied post-gate by manifest PUT
(`tag-images.yml`), digest preserved by construction. Rebuild legs
skip smoke tests — the gate proves bytes; the first build already
proved behaviour — and upload nothing matching the `release-*` glob
that attach fans in on. The scheduled `repro-check` stays untouched:
it re-verifies published history from cold, which the release-time
gate does not cover. And nothing here ever claims
`SLSA_BUILD_REPRODUCED` — a same-platform rebuild can never earn it,
by design ([`tooling-verdicts.md`](tooling-verdicts.md)).

### The verdict beside the evidence

Every claim the org signs beyond build provenance travels through the
same one file in `signer`, varying only a predicate type drawn from the
allowlist inside it — that case statement is the org's entire signing
surface, enumerable by reading it. The first such claim is the **artifact
VSA** (`slsa.dev/verification_summary/v1`), and one workflow performs
every check it rests on:
[`verify-release.yml`](../.github/workflows/verify-release.yml), a job
that runs no caller code, invoked twice per release. In **bytes** mode
(before the signer, registry classes only) it pulls the published bytes
back from the registry and proves them against the built digests — its
output manifest is what the signer attests. In **verdict** mode (after
the release publishes — see below) it re-proves
the subjects, then opens the evidence it is about to summarise: it
fetches
the attestation bundles for every verified digest from the same API a
stranger would use, verifies each cryptographically against the org
signer identity derived from the canon tree's own `sign.yml@<sha>`
`uses:` pins — the tree's single statement of the trusted signer, the
same derivation the `verify-signed` action performs (#314). There is
deliberately no second copy: the former `security/signer.pin` file
needed its own Renovate regex manager, whose URL package name the
first-party group could not match, so every signer bump split into two
branches — `uses:`-only and pin-only — neither able to pass
`lint:signer-pin` alone (#316 finding 2; before that, the manager's
in-file marker destroyed itself on its first bump, #279). The lint now
enforces that every `uses:` line agrees on one digest and refuses the
pin file's reintroduction, so the split state is unrepresentable. It
then checks `builder.id`
names the signer, asserts `buildType`
and `externalParameters` against the run's own identity (#210 — all
four of `verifying-artifacts`' comparisons), and checks the
provenance subjects equal the verified manifest exactly. Only then does
it assemble the predicate — `verificationResult: PASSED` is unreachable
unless both loops passed, `inputAttestations` is appended inside the
loop that verifies each bundle (complete by construction), `policy`
carries both the canon URI and the `gitCommit` digest of the tree that
rendered the verdict, and `slsaVersion` says 1.2 (#208). The verdict is
not asserted beside the verification; it is the verification's return
value. The signer then signs it over the same verified digests.

The provenance bundle is the *evidence*; the VSA is the *verdict*. A
stranger who trusts the org's policy gates on the verdict's one-liner
(see the runbook); a stranger who does not still has the evidence to
re-derive from — which is why the VSA never replaces the per-class
bundles. Dry-runs skip it, and verdict mode refuses a
dry-run on its own: a rehearsal must never sign "PASSED".

**Every class gets a verdict, and every verdict runs after publish**
(#209). Three proof shapes feed one manifest: registry artifacts are
pulled back by stable URL (crates.io, npm); release assets are pulled
back from the published release's download URL — publishing is what
makes that URL exist *and* what makes the release immutable, so the
proved bytes are exactly and permanently what a stranger fetches;
images are proven by **tag→digest binding** — content addressing
already proves an image's bytes, and re-hashing a fetched blob would
prove nothing, so the verdict asserts the one mutable claim instead:
that the tag the release advertises resolves in the registry to the
attested index digest, with the provenance then opened `oci://` at
that digest. The policy claims exactly what was checked, per shape,
nothing more.

Because verdicts follow publish and publishing seals the release,
**VSAs live in the attestation store alone** — the store the verdict
job itself reads evidence from, and the store the runbook's consumer
recipe queries. `audit:attestations` closes the loop weekly: every
subject covered by a release's own provenance bundles must carry a
store-resident VSA, with the obligation gated on the canon version
pinned at the tag (older releases carried the two registry-class VSA
bundles as assets instead, and are held to that shape).

OpenVEX travels the same surface and **is** emitted: `vex-attest.yml`
signs one statement per merge (subjects derived from published SBOMs)
and `stele derive vex` derives each release's own concrete VEX
from the dependency-keyed decisions in `security/vex/` — the
blast-radius query (#106, closed) is what makes an honest `not_affected`
possible at org scale. Source provenance deliberately does not route
through `sign.yml` at all — the source track's git-notes convention needs
`contents: write`, the one grant the signer must never hold — and gets
its own org-owned standalone workflow, per-repo and live since
2026-08-12 (`source-track.md`).

### Images build on native hardware

Every architecture is built on a runner of that architecture. No QEMU, and
therefore no single `platforms: linux/amd64,linux/arm64` build: emulation
tests the image on the wrong machine, and an image that passes under
emulation has not been shown to work where it will run.

**Build and push are separate steps**, for a reason that is easy to miss:
a combined build-and-push has no moment at which a testable image exists
un-published. The image is built and loaded, proved to boot and to do the
one thing it exists to do, and only then pushed. The manifest list is
assembled from the per-architecture images afterwards, and the published
bytes are pulled back **by digest** and proved again before anything is
signed — a signature must assert something demonstrated of the artifact a
stranger will pull, never of a local twin.

Both rules were arrived at independently in three repositories before
being promoted here.

**The compile lives outside the Dockerfile** (#295). The repro gate
measured the containerised cargo build nondeterministic — while the
rust-binary class built the identical crates bit-for-bit under the
belt's pinned toolchain in the same runs. The distinction was never
the code; it was the second, tag-pinned toolchain inside BuildKit.
So the oci-image class takes a `prepare` script: the binary is built
natively per architecture in the caller's mise-pinned toolchain,
under the same reproducibility flags as the rust-binary class, and
the Dockerfile is pure assembly — a digest-pinned or `scratch` base
plus a COPY of bytes the job just built. Nothing nondeterministic
remains inside the image build, the repro leg runs the same script,
and one toolchain serves both classes. A Dockerfile that compiles is
the failure mode, not a style choice.

### Image metadata: one map, resolved once

Every image the org publishes carries the `org.opencontainers.image.*`
facts as config labels on each per-architecture image and as annotations
on the index. The rule that makes them trustworthy: **every fact is
resolved once, before anything builds, and builds consume the map without
deriving anything** (`stele derive facts`, run by the `facts`
job in `publish.yml` and `continuous.yml`; the mechanism spec lives in
stele). A per-build derivation is a
drift surface, and a late derivation is a late failure — the predecessor
of this design stamped every extension image's `created` with a canon
commit's timestamp, because the deriving job's only checkout was canon.
Under this design that bug is unwritable.

Facts derive from exactly three sources — the guard-proven ref, in-tree
metadata at that commit, and the GitHub API's view of the repository —
and split into two kinds:

- **Provenance** — never caller inputs, validated, fail-closed:
  - `revision`: the full 40-hex SHA the tag points at.
  - `version`: the guard's own output; the continuous archetype's map
    simply never contains it.
  - `source`: `server_url/repository`, verbatim case, no trailing slash,
    no `.git` — one canonical rendering.
  - `created`: the released commit's committer time, RFC 3339 UTC — the
    same instant is exported as `SOURCE_DATE_EPOCH`, which BuildKit
    propagates into the config and index `created` fields, so every
    timestamp surface is a function of one resolved value and none is
    wall clock.
  - `licenses`: **precedence chooses which declaration speaks; SPDX
    validation makes speaking safe.** `[workspace.package].license`,
    else `[package].license`, read with taplo, never grep; a repository
    with no manifest falls to `GET /repos/{o}/{r}/license?ref=<sha>` —
    Licensee reading the actual LICENSE file. The tiers are deliberately
    *not* cross-checked against each other: the manifest field is the
    author's declaration and the API is a lossy heuristic that flattens
    `MIT OR Apache-2.0` to a single id, so they are not independent
    statements of one fact. Every expression must satisfy the SPDX
    grammar and id lists via an adopted SPDX library (stele's
    facts resolver — the vendored id list and its freshness alarm are
    retired with it). `LicenseRef-*` (dangles outside an SPDX
    document), `NOASSERTION`, `NONE` and non-canonical spellings are
    refused.
    Ecosystems beyond Cargo are unbuilt branches of this contract, added
    at the manifest-read only, when a real repository needs one — the
    version-source rule applied to licences.

    Measured, so the fallback tier's behaviour is not a surprise on
    release day: the API **does** honour a commit SHA (`cli/cli` answers
    `MIT` at branch and at SHA alike). A `NOASSERTION` answer means
    genuine ambiguity in the tree, not a broken ref — a two-file
    `LICENSE-MIT` + `LICENSE-APACHE` layout returns it, while
    `?ref=<default branch>` returns the repository's *cached, flattened*
    licence field, which is the one value this contract must never
    adopt. Refusing is therefore correct, not an obstacle to route
    around. **A manifest-less repository that ships images must carry a
    single unambiguous licence file, or declare its licence in-tree**;
    `monumental-archive-db` is that shape and wants checking before its
    first canonical release rather than at it.
- **Editorial** — `title` and `description`: caller inputs
  (`image-title`/`image-description`), defaulting to the repository's
  name and description, **omitted when absent** rather than emitted
  empty. Registry UI garnish; machinery to prevent a wrong one buys
  nothing.

Where a manifest declares `repository`, it must **equal** `source` —
those two *are* independent statements of one fact, npm trusted
publishing already dies on their mismatch at publish time (after images
are pushed), and a transferred repository's stale field is exactly what
this converts into a five-second failure with the remedy named.

An empty value is a failure, never a fact: a present-but-empty
annotation reads as set, which is worse than absent. That is
deliberately stricter than the OCI spec, which permits empty values.

Two properties belong to the remote object and stay checks rather than
construction. The per-arch push exporter sets `oci-mediatypes=true`: the
exporter default is `false`, and a Docker-mediatype manifest makes
`imagetools create` assemble a Docker manifest list, which has no
annotations field — buildx then drops index annotations **silently**
(docker/buildx#1965; the flag also predicates a buildx ≥ 0.12 floor,
asserted rather than assumed). Measured honestly: the images published
*before* this change already carried an OCI index, so on this path the
annotations were absent because nothing ever passed `--annotation`,
not because a manifest list swallowed them. The flag makes the media
type a guarantee rather than a property we happened to get, which is
what the assertion below is entitled to rely on. So
`stele assert image-facts` runs at the existing pull-back points and
asserts, of the published bytes by digest: the index media type is
OCI, the index annotations **equal** the map, and every per-arch
config's labels equal it too. Equality, not presence — presence lets a
wrong `revision` through, which is worse than a missing one. The
mechanism and its tests live in stele (stele#39 for the port record);
the env contract (`IMAGE`, `DIGEST`, `FACTS`) is unchanged from the
bash it replaced.

The artifact Dockerfile carries no `LABEL`s, deliberately: a `LABEL`
would be a second mechanism for the same facts, and whether a `--label`
overrides a same-key Dockerfile `LABEL` is unspecified CLI behaviour.
One map, generated identically at every surface; adding a key is a line
in the resolver, not an edit in three workflows.

### The source archive is an artifact class like any other

`source-archive` is for repositories whose deliverable is their own
content — the canon itself above all. The build is `git archive` of the
release tag, which is deterministic by construction (tree-object bytes,
commit-date mtimes); the build job proves it anyway, building twice and
refusing a digest mismatch. Like binaries there is no registry to pull
back from: the archive becomes a release asset, GitHub's release
attestation binds tag, commit and asset digests at publish, and the
signed subjects are the same bytes the attach job uploads. This is an
*artifact* claim — "this tarball is the tree of this tag, built by this
workflow" — not a SLSA Source-track claim about review and history;
those live in git notes, are contemporaneous with pushes, and remain the
standup tracked in #120.

### Binaries build on native hardware too

`rust-binary` ships statically linked musl binaries for Linux and native
binaries for macOS, each built **and tested** on a runner of its own
architecture — `cargo test --target` runs on the release target, so the
musl legs prove the statically linked binaries actually execute, which is
the property the shipped artifact claims. The target list is validated
against canon by a plan job that *emits* the build matrix: a typo in a
target name fails the release rather than silently building three
platforms instead of four, and the collect job refuses a partial set.

**Why a C toolchain, and why apt.** Rust's `*-musl` targets bundle their
own musl libc, so a pure-Rust binary self-links with no external
toolchain. The moment any dependency compiles C through `build.rs` — and
`ring` and `mimalloc` in iiif-server's lock both do — a musl-targeting C
compiler must exist. `musl-tools` is installed unconditionally on the
Linux legs: identical environments beat conditional ones, the packages are
distro-signed through Ubuntu's own mirrors, and Scorecard's
Pinned-Dependencies check does not examine apt (read from
`checks/raw/shell_download_validate.go`, not from the docs). It is the one
unpinned install in the pipeline, accepted deliberately; the pinnable
upgrade path, should it ever matter, is zig/cargo-zigbuild.

**Reproducible by construction** (Best Practices Silver
`build_repeatable`): `SOURCE_DATE_EPOCH` from the released commit's own
timestamp, `CARGO_INCREMENTAL=0`, `--remap-path-prefix`, stripping
disabled (it would destroy the section cargo-auditable lives in), and
normalised archives — sorted members, zeroed ownership, clamped mtimes,
`gzip -n`.

Binaries have no registry, so there is no pre-sign pull-back leg: they
ship as release assets, GitHub's own release attestation binds tag,
commit and asset digests at publish, and the subjects `signer` attests
are the same archives the attach job uploads. Their pull-back — and
their verdict — comes after publish, from the released download URL,
which by then is public and immutable ("The verdict beside the
evidence" above).

### go-binary: the same doctrine with the toolchain matrix gone

`go-binary` (stele#7) ships static Linux binaries and native macOS
binaries with the same shape as `rust-binary` — four native legs, plan
job refusing non-canon targets, collect job refusing a partial set,
release assets with no registry, repro-gated from its first release.
What Go's toolchain removes is the toolchain *matrix*, never the testing
obligation: GOOS/GOARCH could cross-compile every target from one
runner, and deliberately does not — each leg still builds **and tests**
on the hardware the binary ships for. With `CGO_ENABLED=0` there is one
compiler and a static output on every leg: no musl-tools, no target
installs, no apt at all, which retires rust-binary's one accepted
unpinned install for this class.

Hermeticity is stated by the class workflow, never inherited from the
caller's mise `[env]`: `CGO_ENABLED=0`, `GOTOOLCHAIN=local` (the
caller's mise pin is the only toolchain; a disagreeing `go` directive
fails loudly instead of auto-downloading), `-trimpath`, and go.sum
refusing any module byte drift through the checksummed proxy — the one
accepted network dependency, the un-vendoring decision recorded in
stele's CLAUDE.md.

The binary carries its own inventory: Go embeds the module list and the
VCS facts in every build, and `go version -m` reads them back out of the
shipped bytes — the property cargo-auditable is bolted onto rust-binary
to provide. The build *asserts* the stamp: `vcs.revision` must equal the
released commit, `vcs.modified` must be false, and the module list must
be present. There is no manifest-version check because Go has no
manifest version to check; the stamp assertion is the stronger
replacement — it binds the bytes to the commit, not a declaration to a
tag name. Release-time advisory triage gets a Go leg in the same sbom
job: `audit:go-vulns` (govulncheck, call-graph aware) at the tagged
checkout, red blocking the release with no warn path, exactly the
`audit:deny` contract.

### pgrx extensions build inside the Postgres consumers run

The fifth class, `pgrx-extension`, was designed from edtf's proven
pipeline — mined for lessons, never for shape (the legacy build was
correctness-by-patching; this is the same knowledge by design):

- **Built and tested inside `postgres:<major>-bookworm`**, digest-pinned
  from [`docker/pgrx-base-images.toml`](../docker/pgrx-base-images.toml)
  (one Renovate-managed mapping org-wide). The consumer's `pg_config` is
  structural — pgrx's own downloaded Postgres builds green and fails at
  `CREATE EXTENSION` for exactly the person the artifact serves — and the
  test suite runs per cell against the same build, not a lookalike.
- **The glibc floor is verified, not assumed**: bookworm sets 2.36, and
  readelf proves no symbol exceeds it, so a dependency dragging in a
  newer symbol fails the release instead of failing at dlopen on
  someone's Debian 12. Smoke runs on bookworm AND trixie: forward
  compatibility demonstrated, never presumed.
- **Upgrade path derived, proven AND executed**: upgrade SQL is a build
  product, never authored (#132 — a hand-maintained stub is derived
  state written by humans, and forgetting it burned immutable version
  numbers; PostGIS and TimescaleDB both generate theirs from the
  canonical schema for the same reason). On every Release-PR refresh,
  `release/generate-pgrx-upgrade.sh` takes the previous release's
  generated schema — out of its own signed tarball — and the
  candidate's, and derives `<ext>--<prev>--<new>.sql`:
  `CREATE OR REPLACE` for changed and new functions, `DROP` for removed
  members, a comment-only no-op when only the library changed, and a
  loud refusal for anything not soundly derivable (in-place type or
  table changes need a human decision). It is then PROVEN before it is
  committed: a live Postgres installs the previous release's real
  tarball, `CREATE EXTENSION` at the old version, overlays the
  candidate package plus the derived script, `ALTER EXTENSION UPDATE`,
  and the upgraded catalog must match a fresh install of the candidate
  exactly — read through `pg_depend`, because `pg_dump` cannot see
  extension members. An underivable or unsound change therefore fails
  the Release PR, pre-tag and free, never the publish. Data
  migrations — the one thing no schema mechanism can derive — go in an
  optional version-free `sql/next-data.sql`, folded into the derived
  script (and consumed by the release commit) under the same proof. At
  publish the guard still refuses a tag whose upgrade file is missing
  (the backstop), and each cell installs the previous release's real
  tarball into a live Postgres and crosses the gap with
  `ALTER EXTENSION UPDATE` — the path executes, not merely exists.
  Majors the previous release did not ship skip execution: only
  installations that can exist can be stranded.
- **Reproducible tarballs**, same flags and normalisation as rust-binary,
  named `<ext>-<version>-pg<major>-linux-<arch>.tar.gz`, each shipping
  both the Debian tree and the CloudNativePG ImageVolume layout.
- **One artifact image per PG major**, `FROM scratch`, both layouts, both
  architectures assembled in one build (nothing executes, so nothing is
  emulated), built in-run from the same verified tarballs the release
  attests — one byte path, no post-release download. Each index is
  signed under the org identity and proved the way a consumer uses it:
  pulled by digest, `COPY --from` into a stock postgres,
  `CREATE EXTENSION`. The runnable convenience image deliberately does
  not exist in canon: its CVE posture would be its base's, refreshable
  only by a release.

### wasm-npm and the second provenance

The wasm package is built by **wasm-pack** and packed once — `npm pack` in
the build job produces the tarball that is hashed, signed, verified and
then published as those exact bytes, with no re-pack to drift. wasm-pack
and node/npm are **caller build inputs**, pinned in the releasing repo's
own mise config exactly like its rust toolchain — not belt tools, because
the belt is the universal layer and this class is not universal. The jobs
assert their presence and fail with the remedy; nothing is installed
unpinned, and an npm too old for trusted publishing (< 11.5.1) is a
failed release with a message saying "bump the pin", never a runner
mutation.

npm trusted publishing needs no manual token exchange, and it mints
**npm's own** provenance and publish attestations, naming the caller's
workflow — so a wasm-npm release carries two independent evidence paths:
`npm audit signatures` checks npm's, `gh attestation verify` checks the
org's ([`slsa-reference.md`](slsa-reference.md): the two are documented,
not unified). The verifier pulls the published tarball back from
`registry.npmjs.org` by the declared package name — declared, because a
scoped package's tarball filename mangles the scope and cannot be trusted
to reconstruct the registry path.

First publish of a new package is manual, then trusted publishing is
configured against `publish.yml` and the `publish` environment and token
access disabled — the same sequence as crates.io.

### Scanning never blocks a publish, and never writes to nowhere

The CVE gate lives on pull requests, where it blocks. The scan in the
publish path is **report-only**, deliberately: a publish — above all a
scheduled remediation rebuild whose entire purpose is refreshing a layer
that nothing can pin — must not die because a scanner had a bad day.
Blocking a rebuild leaves the older, at-least-as-vulnerable image live,
so it preserves *more* exposure, not less.

That split is what makes the rule safe. Content changes are gated, because
they introduce something new; remediation is never gated, because it only
removes. The residual case — a rebuild introducing a regression the live
image lacked — is real, and the answer to it is still not to block, since
that trades a rare regression for a guaranteed weekly failure to
remediate.

**But a report-only scan must go somewhere a human or a machine will see
it.** A scan whose output lands in the log of a scheduled job that
succeeded is write-only: green cron runs are invisible, and a check nobody
reads is worse than no check because it manufactures the impression of
coverage. So report-only carries an obligation:

- findings into the job summary, and SARIF into code scanning, so a new
  critical raises an alert on its own without touching the publish
- and, properly, **attested against the digest** — `actions/attest` takes
  an arbitrary `predicate-type`, so the vulnerability state becomes part
  of the evidence bundle travelling with the artifact rather than
  something only the maintainer can look up. That is also what OSPS
  VM-04.02 asks for, in OpenVEX.

A scan that does neither comes out. Half a control is not half as good;
it is worse than none.

### No runner-hardening agent

Earlier drafts of this document and of issue #28 prescribed harden-runner
with audit-derived egress allowlists. That is retracted, deliberately.

It is required by nothing this organisation targets. SLSA Build L1–L3 does
not ask for it — the "Isolated" requirement explicitly "does not prohibit a
build from calling out to a remote execution service", and it is a
requirement on the build *platform* rather than on us. Neither the Source,
Dependency nor Build Environment track asks for it; the Dependency track's
nearest control is the opposite one, curated ingestion through a
producer-controlled mirror. None of Scorecard's twenty checks credits it,
and Pinned-Dependencies penalises it if ever left unpinned. No OpenSSF Best
Practices criterion through Gold requires it. No OSPS Baseline control
requires it — BR-01.03, the closest, is credential isolation and is already
met by zizmor-pedantic plus `cache: false`. Hermeticity remains a
future-directions candidate that "may or may not" become a level.

Against nothing, the costs are real and measured. Wrong egress allowlists
broke releases in this organisation's earlier pipelines, in a flow where
crates.io is yank-only and a pulled digest exists forever; adding
fragility to an irreversible pipeline to answer an unscored threat is the
wrong trade. Admitting the agent would also mean widening an Actions
allowlist that deliberately sets `verified_allowed: false` — weakening a
control that works to add one that scores nothing. And the attack class it
detects best, a backdoored third-party action, is one this organisation
already prevents structurally, since such an action cannot run here at all.

**Revisit only if SLSA promotes hermeticity into an actual level.** If that
happens the allowlist is derived from audit-mode data across real releases
and never written by construction, which is the mistake that burned the
previous attempt. The recovered list from the pre-strip signer is preserved
in that repository's history for that day.

## Version policy

- Versions follow semver **over each repository's named compatibility
  surface** (its `MAINTENANCE.md` or equivalent). Internal Rust APIs are
  covered only where crates are published.
- Pre-1.0, breaking changes bump the minor. **1.0.0 is never reached by
  automation** — it is set by hand, once, as a deliberate commitment.
- An MSRV bump is a minor version, never a patch.
- chore/ci/docs/style/test commits alone never produce a release.
- Published crates gate on `cargo-semver-checks` in CI.

## When something goes wrong

- **crates.io is yank-only**; npm unpublish has a 72-hour window; a GHCR
  digest anyone has pulled exists forever; published releases are immutable.
  There is no rollback, only roll-forward.
- Publish scripts are resumable: every step skips work that is already
  verifiably published, so a half-completed release is re-run, not repaired
  by hand.
- A release that fails before `publish` leaves a draft and unpublished
  artifacts — delete the draft, fix, re-tag never (tags are immutable);
  the fix ships as the next version.

## Conformance

| Repository | Gaps to close |
| --- | --- |
| edtf | drop release-plz + per-crate tags; adopt `[workspace.package]` inheritance; App-minted tag; re-anchor attestation to `signer` |
| iiif-server | adopt canonical `cliff.toml`/scripts (it is their source, but canon now lives here); App-minted tag replaces `RELEASE_TOKEN`; re-anchor to `signer` |
| monumental-archive-db | re-anchor cosign identity to `signer`; everything else exempt (continuous archetype) |

Risky release-machinery changes are proven in the **release lab**
(`release-lab`) before any production repository
moves — but the canon runs them first, and cheaply. A change to this
machinery cannot be rehearsed elsewhere (a consumer may not pin an
untagged canon SHA, publish accepts only `v*` tags, and
`self-publish.yml` requests no `dry-run`), so it is released and
watched: one class, minutes, a version number nobody pins if it reddens.
The lab then proves it at full width — four classes across PG 14–18.
Canon first for "does it run", lab for "does it work", production last.
See MAINTENANCE.md, "The seam rule".

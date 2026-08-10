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

## Phase 1: the release PR (git-cliff)

The version is derived from conventional commits by **git-cliff** — never
typed by a human. The canonical configuration is
[`scaffold/cliff.toml`](../scaffold/cliff.toml); its load-bearing settings
(pre-1.0 breaking changes bump the minor, housekeeping commits produce no
release, `trim = false`) are documented inline and are not repo-tunable.

git-cliff rather than a release tool, org-wide: release-plz cannot drive
workspaces whose interdependent crates are unpublished (release-plz#2595,
verified open and unfixed in 0.3.160), release-please has no model for
`[workspace.package]` inheritance at all, and dist must own the binary build,
which collides with hardened runners and with publishing binaries extracted
from the container image. Meanwhile the tags-only job a release tool would do
— bump, changelog, release PR, tag, draft release — is exactly what
iiif-server's git-cliff phase 1 already does in three small scripts. One
flow, no structural bugs, no tool that wants to own the pipeline.

The machinery is shared: callers pin
[`release.yml`](../.github/workflows/release.yml) by SHA (the usual doubled
`.github` path), which runs the canonical scripts in
[`release/`](../release/) at that same SHA. The caller stub is
[`workflow-templates/release.yml`](../workflow-templates/release.yml).

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

What the key actually has today is `visibility: all` on an organisation
secret, readable by any workflow in any repository in the organisation,
including one added tomorrow — `signer` and the release lab among them.
The only mechanism that narrows that is repository scoping,
`visibility: selected`, which cannot be set without re-supplying the
encrypted value and therefore means rotating the App key first. That is
deferred deliberately, not overlooked, and is recorded here so the
deferral is visible rather than implied.

The default `GITHUB_TOKEN` is never used to push a tag: tags it pushes
trigger no workflows, and a release that silently triggers nothing looks
exactly like a success.

**The ruleset lock**: `v*` tag creation is restricted org-wide with the App
as sole bypass actor ([`rulesets/org-release-tag.json`](../rulesets/README.md)).
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
- **Build inputs**: `Cargo.toml`, `Dockerfile`, `cliff.toml`.

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

Observed: adding `contents: read` to `release.yml`, so that git-cliff
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
- The Zenodo webhook mints the version DOI when the immutable release is
  published — after proof, like everything else.

There is one shared build workflow per **artifact class** — `rust-crate`,
`rust-binary`, `oci-image`, `wasm-npm` — and callers declare inputs, not
steps. An earlier draft of this document kept publish workflows per-repo,
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

Binaries have no registry to pull back from, so there is no
verify-published leg: they ship as release assets, GitHub's own release
attestation binds tag, commit and asset digests at publish, and the
subjects `signer` attests are the same archives the attach job uploads.

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
moves.

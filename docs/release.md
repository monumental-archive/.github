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
`--no-cache` rebuild, no tags, no version surface — the artifact's "version"
is its pin set, and scheduled republishing is the remediation path for
unpinnable base layers. Continuous repositories share the toolbelt, the gate
and the signing identity, and nothing else from this document applies to
them.

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
power. The App's key lives behind the protected `publish` environment.

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
skeleton every repository copies and drifts from: harden-runner with
audit-derived egress allowlists, tool caches disabled in any job whose
output is signed, and the evidence bundle — one checksum filename, one SBOM
format, Sigstore bundles — attached to the release in the shape Scorecard's
Signed-Releases check recognises. Egress is a property of the workflow and
the artifact class, so it is derived once, in the lab, against
class-representative fixtures — never written by construction.

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

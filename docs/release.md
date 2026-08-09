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

## Phase 2: publish, prove, sign

Triggered by the `v*` tag, so `github.ref` *is* the tag and provenance names
immutable bytes — a workflow on `main` can only attest a moving pointer
(edtf's v1.0.0 attestations are permanently wrong this way; Sigstore is
append-only).

The step order is not rearrangeable:

> build → smoke test → push → pull the published bytes back and prove them →
> attest → verify the attestation as a stranger would → publish the release

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
- Signing goes through **trusted-builder**: callers build their own quirky
  artifacts, pass hashes, and the caller-code-free reusable workflow signs.
  One certificate identity for every org artifact; `id-token: write` lives
  there and never here.
- The Zenodo webhook mints the version DOI when the immutable release is
  published — after proof, like everything else.

Publish workflows are per-repo (pgrx matrices, musl statics are legitimate
quirks) but share the hardening skeleton: harden-runner with audit-derived
egress allowlists, tool caches disabled in any job whose output is signed,
and the evidence bundle — one checksum filename, one SBOM format, Sigstore
bundles — attached to the release in the shape Scorecard's Signed-Releases
check recognises.

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
| edtf | drop release-plz + per-crate tags; adopt `[workspace.package]` inheritance; App-minted tag; re-anchor attestation to trusted-builder |
| iiif-server | adopt canonical `cliff.toml`/scripts (it is their source, but canon now lives here); App-minted tag replaces `RELEASE_TOKEN`; re-anchor to trusted-builder |
| monumental-archive-db | re-anchor cosign identity to trusted-builder; everything else exempt (continuous archetype) |

Risky release-machinery changes are proven in the **release lab**
(edtf-release-lab, promoted to org-generic) before any production repository
moves.

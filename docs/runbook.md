# Release runbook

The operating manual for the pipeline [`release.md`](release.md) specifies.
That document says what is true and why; this one says what to type. It is
written for two moments: wiring a repository in, and a release going wrong.

## Wiring in a repository

### Versioned (has releases, versions, tags)

1. Copy from [`scaffold/`](../scaffold/): `mise.toml`, `cliff.toml`,
   `committed.toml`, `.rumdl.toml`, and `SECURITY-INSIGHTS.yml` (fill the
   `<angle-bracket>` fields). Add `CITATION.cff` from the scaffold if the
   repository is citable.
2. Copy the three workflow stubs from
   [`workflow-templates/`](../workflow-templates/): `ci.yml`,
   `release.yml`, `publish.yml` — **the publish.yml filename is
   load-bearing**: both registries pin it, renaming it breaks trusted
   publishing with nothing local going red. Copy `scorecard.yml` and
   `dependency-review.yml` too.
3. Declare the artifact classes in the publish stub's `classes:` line
   (comma-separated, any subset):

   | Class | Extra inputs | Caller build inputs (mise.toml) |
   | --- | --- | --- |
   | `rust-crate` | `exclude` for container-built members | `rust` |
   | `rust-binary` | `binary-targets`, `binary-smoke-test`, `exclude` | `rust` |
   | `oci-image` | `dockerfile`, `context`, `smoke-test` | — |
   | `wasm-npm` | `crate-dir`, `npm-scope` | `rust`, `aqua:rustwasm/wasm-pack`, `node` |
   | `pgrx-extension` | `extension-crate-dir`, `pg-majors`, `extension-smoke-test` | `rust`, `cargo:cargo-pgrx` (must equal the pgrx crate dep) |

4. Run `settings/repo-baseline.sh apply` — settings baseline, immutable
   OIDC sub claim, and the `publish` **environment** (a repository object
   that cannot be shared; apply creates it automatically wherever the
   canonical entry `publish.yml` exists). Then `check` and fix any
   remaining drift it reports.
5. Attach the org security configuration (Settings → Code security — the
   one manual step on every new repository).
6. Workspace shape: version via `[workspace.package]` inheritance; a pgrx
   crate is a member but **not** a default-member, and goes in the
   `exclude:` input.
7. `lint:release-stub` (belt) enforces 2–3 from then on: `cliff.toml`
   present means the stubs must be, pinned.
8. Copy `scaffold/CODEOWNERS` to `.github/CODEOWNERS` and grant the
   `owners` team write access — documentation and reviewer routing now,
   enforcement when a second maintainer flips the Code Owners toggle.
9. **Score-ready extras**, so the repository badges the day it lands:
   commit a `.coverage-floor` (a bare number; the gate's `coverage:check`
   ratchet enforces it — Silver wants ≥ 80) and pass the `codecov-token`
   secret to the ci stub for the badge feed; copy `scaffold/REUSE.toml`
   and licence texts into `LICENSES/`; paste
   `scaffold/README-badges.md` into the README and fill the ids; answer
   the Best Practices form from [`best-practices.md`](best-practices.md)
   (post-transfer — it binds the repo URL).

### Continuous (no versions — the artifact's version is its pin set)

Copy `ci.yml` and `continuous.yml` stubs plus the config files from
scaffold; no cliff.toml, no release.yml, no publish.yml, no environment.
That is the whole wiring.

### Registries, first time only

- **crates.io**: first publish of each crate is manual
  (`cargo publish` with a token). Then per crate on crates.io: Settings →
  Trusted Publishing → repository + `publish.yml` (+ environment
  `publish`), and disable token publishing. Add the org team as owner:
  `cargo owner --add github:monumental-archive:owners <crate>` (needs a
  `change-owners`-scoped token; revoke it after).
- **npm**: packages are scoped `@monumental-archive/...` (`npm-scope`
  input). First publish is manual: `wasm-pack build --release --scope
  monumental-archive <crate>`, then `npm publish --access public` from
  `pkg/`. Then package Settings: Trusted Publisher (org / repo /
  `publish.yml` / `publish`, allow `npm publish`) and "Require 2FA and
  disallow tokens".
- **Zenodo**: `ZENODO_TOKEN` is an organisation secret with
  `visibility: selected` — tick the repository onto it rather than
  minting per-repo tokens (the value is a sandbox-account token until
  the first production DOI is wanted; swapping to a production token,
  scopes `deposit:write` + `deposit:actions`, is one `gh secret set`).
  Pass `mint-doi: true` + the secret in the publish stub. The pipeline
  mints the DOI **after** the release publishes — never the flip-switch
  webhook integration (no token auth). The lab proves against
  sandbox.zenodo.org; production zenodo.org is the workflow default.

## Cutting a release

Nothing to type. Merge conventional commits; the release PR tracks them;
**merging the release PR is the commitment point** — tag, publish, prove,
sign, attach, publish, DOI all follow. chore/ci/docs/style/test commits
alone never produce a release (by design — a caller-only change rides the
next feat/fix).

## When something goes wrong

Rules that frame every recovery: **crates.io is yank-only; npm unpublish
closes after 72h; a pulled GHCR digest exists forever; published releases
are immutable; tags are never reused.** There is no rollback, only
roll-forward.

- **Publish run failed before anything published**: the release is still
  a draft. Fix the cause (usually in canon — bump the caller's pin), and
  re-run: `gh workflow run publish --repo <repo> --ref <tag>` — ALWAYS
  `--ref <tag>`; the guard refuses branches. Note the dispatch runs the
  caller's `publish.yml` **as committed at the tag** — if the fix changed
  the caller (pin bump), dispatch cannot pick it up and the fix ships as
  the next version instead. Delete the dead draft.
- **Half-published** (e.g. crates uploaded, release not public): every
  publish step is resumable — a crate/package/tag already on the registry
  is a completed step, not a failure. Re-dispatch on the tag and it
  converges.
- **Gate red on the release PR**: fix on main; the release PR refreshes
  itself on the next push. Never edit the release branch by hand.
- **A cell of a matrix class failed**: `fail-fast: false` means the other
  cells finished; collect refused the partial set; nothing signed or
  published. Fix and roll forward — the tag was never consumed, delete
  the draft and let the next release PR re-mint... it cannot: tags are
  immutable. The failed version number is burned; ship the next one.
- **Tag-mint App dead**: break-glass in
  [`continuity.md`](continuity.md) — an org owner disables the tag
  ruleset, mints by hand, re-enables, records the event.
- **rekor-monitor filed an issue**: an unexpected certificate for the
  signer identity, or a monitoring failure — the run log distinguishes.
  An unexpected issuance is a stop-everything event: rotate nothing until
  you know how the identity was minted (the certificate names workflow,
  repo, ref and commit).
- **repro-check filed an issue**: a release stopped rebuilding to its
  published bytes — a build input escaped pinning. Not consumer-facing
  (the published bytes are still the attested ones); diagnose before the
  next release.
- **`startup_failure`, no jobs, no log**: a permissions elevation or an
  Actions-allowlist rejection. Check: does every `uses:` job restate its
  callee's permissions; is every action on the org allowlist (including
  actions used *inside* any third-party reusable).

## Verifying, as a consumer would

```bash
gh attestation verify <artifact> --owner monumental-archive \
  --signer-workflow monumental-archive/signer/.github/workflows/sign.yml \
  --signer-digest <signer-commit> --source-ref refs/tags/<tag> \
  --deny-self-hosted-runners
```

- The verification verdict (artifact VSA): same command plus
  `--predicate-type https://slsa.dev/verification_summary/v1` **and**
  `--bundle attestations-vsa-<class>.intoto.jsonl` from the release — the
  VSA lives in its own bundle, not in `attestations-<class>.intoto.jsonl`.
  The bundle is required: GitHub's attestations API rejects that
  predicate type as a query filter (`HTTP 422: invalid predicate type`,
  measured on v0.16.3) even though the attestation is present in the API.
  Gate on `verificationResult: PASSED` and `verifiedLevels` instead of
  re-deriving the policy yourself. Absent on dry-run releases, by design.
- Images: same command with `oci://<image>@<digest>` (the **index**
  digest — per-arch digests are not covered).
- Offline: add `--bundle attestations*.intoto.jsonl` from the release.
- GitHub's own binding: `gh release verify <tag>` /
  `gh release verify-asset <tag> <file>`.
- npm's independent provenance: `npm audit signatures` (names the
  caller's workflow, not the signer — two paths, both documented).
- Checksums without tooling: `sha256sum -c checksums.txt`.

## Canon changes (this repository)

A shared workflow's permissions and inputs are a public contract; adding
a permission breaks every caller as `startup_failure`. Prove risky
changes in `release-lab` before any production repo moves its pin. Stub
pins in `workflow-templates/` advance together to one SHA after merges.
The lab's cycles are cheap by design: version numbers there are spent
freely — a red run costs a patch number, never a consumer.

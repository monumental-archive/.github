# Release runbook

The operating manual for the pipeline [`release.md`](release.md) specifies.
That document says what is true and why; this one says what to type. It is
written for two moments: wiring a repository in, and a release going wrong.

## Wiring in a repository

### Versioned (has releases, versions, tags)

1. Copy from [`scaffold/`](../scaffold/): `mise.toml`, `cliff.toml`,
   `committed.toml`, `.rumdl.toml`, and `SECURITY-INSIGHTS.yml` (fill the
   `<angle-bracket>` fields). If the repository is citable (its release
   stub passes `mint-doi: true`), render `CITATION.cff` with
   `mise run fix:citation` — derived from `REUSE.toml`, enforced by
   `lint:citation`, never hand-filled (#316).
2. Copy the three workflow stubs from
   [`workflow-templates/`](../workflow-templates/): `ci.yml`,
   `release.yml`, `publish.yml` — **the publish.yml filename is
   load-bearing**: both registries pin it, renaming it breaks trusted
   publishing with nothing local going red. Copy `scorecard.yml` and
   `dependency-review.yml` too, and `source-attest.yml` — the repo's
   source-signing identity, live (docs/source-track.md); every repo
   joining the org carries it, created or transferred alike. It needs
   the `source-attest` environment holding `SOURCE_RULES_TOKEN` before
   its first run, and a genesis dispatch to found its chain — the
   activation checklist in `source-track.md` is the sequence, including
   the one expected red run. Seed the notes ref while at it:
   `git notes add -m "source-track: notes ref seeded" HEAD && git push
   origin refs/notes/commits`.
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
   secret to the ci stub for the badge feed; copy
   `scaffold/REUSE.toml.stub` to the root as `REUSE.toml` (the .stub
   suffix keeps the placeholder from governing the canon's own tree —
   any file literally named REUSE.toml rules its subtree) and licence
   texts into `LICENSES/`; `lint:reuse` then proves real REUSE-spec
   compliance in the gate, before any registration; add the
   `<!-- badges:begin -->`/`<!-- badges:end -->` marker pair to the
   README and run `mise run fix:badges` — the block is derived, never
   pasted (`scaffold/README-badges.md` is the catalogue, `.badge-states`
   holds the human-step lines, `lint:badges` reddens hand drift, #316).

   Then the two registrations, each a human step ending in a
   `.badge-states` line — **and in both cases the line goes in last**,
   because `audit:badges` re-asks each issuer whether a worn shield is
   still true and fails the repo when it disagrees:

   - **REUSE**: `lint:reuse` must be green first — it proves the tree
     compliant before anyone is asked to certify it. Register at
     <https://api.reuse.software/register>: name, email, and the
     project URL **without a scheme**, `github.com/<org>/<repo>` (the
     form supplies the surrounding `git://` and `.git`; a repo named
     `.github` is fine). Confirm by email, then wait for the first
     crawl — the API answers `uninitialised` until it finishes and
     `compliant` after, about a minute. Only then set
     `reuse registered`.
   - **Best Practices**: post-transfer, since the form binds the repo
     URL. Answer it from [`best-practices.md`](best-practices.md),
     which carries the form mechanics, the automation-proposal URLs and
     the `.bestpractices.json` shortcut — so this is a review, not 190
     clicks. **Reach 100% on the passing section before** setting
     `bestpractices <BP_ID>`: the audit gates that shield on the
     issuer's `badge_percentage_0`, so an id set early publishes a
     shield that reddens the next Monday cron.

   Re-run `mise run fix:badges` after each line lands; both the Best
   Practices and OSPS Baseline shields are derived from the one id.

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
  `visibility: selected` — one production token (scopes `deposit:write`
  and `deposit:actions`), granted per repo at migration by ticking the
  repository onto it, never per-repo tokens. Pass `mint-doi: true` +
  the secret in the publish stub, and render `CITATION.cff` first
  (`fix:citation` — the record's licence and creators come from it).
  The pipeline mints the DOI **after** the release publishes — never
  the flip-switch webhook integration (no token auth). There is no
  sandbox lever (#316): every mint is a real, permanent record, the
  lab's included — a rehearsal that mints test DOIs never proves the
  DOI path.
- **Codecov**: use the **repository** upload token, not the org-wide
  global one — the global token does not identify a repo, so the CLI
  needs an extra org/slug flag the pinned action does not pass, and
  release-lab's first upload died on exactly that. The repo token is a
  bare UUID from the repo's Codecov Settings -> Tokens page; paste only
  the value, never a copied command snippet. Two grants, deliberately
  different shapes (#316). The
  app (app.codecov.io, "Install Codecov") is installed on the org with
  **Only select repositories** — never all-repos: its grant includes
  WRITE on checks, statuses and pull requests, and a breached-before
  third party gets no standing write surface on repos (the signer, the
  canon) that consume nothing from it. Tick each coverage-adopting
  repo onto the installation at migration. The upload credential is
  the org **global upload token** held once as `CODECOV_TOKEN`
  (`visibility: selected`, tick the repo on — the ZENODO_TOKEN model);
  keep Codecov's "upload tokens required" setting ON, because
  tokenless ingest accepts forged reports from anyone. A leak
  pollutes only the badge feed: the gate's ratchet (`coverage:check`)
  computes locally and never reads Codecov.

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
- **Failed AFTER publish** (the DOI mint is the only job downstream of
  the commitment point — e.g. Zenodo down, v1.20.0's first mint):
  re-dispatch on the tag; attach proves the rebuilt bytes equal the
  published set and passes through the immutable release instead of
  clobbering it (#316). Releases published by a canon older than the
  converge path cannot resume past publish — their DOI rolls forward to
  the next version, which is why the canon's concept record starts at
  v1.21.0 and not v1.20.0, and skips v1.22.0 (the concept-resolution
  redirect bug, #316). A dispatch runs the tree AT THE TAG, so a fix on
  main never reaches an already-tagged release: version DOIs are not
  contiguous by design, the concept DOI covers the software, and the
  next release carries the fix.
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

That recipe is for a human at a terminal, where `<signer-commit>` is
looked up fresh each time — from the `sign.yml@<sha>` `uses:` pin in the
tree that produced the release, the tree's one statement of the trusted
signer.
**In a workflow, never write the digest as a literal**: nothing bumps a
SHA inside a shell command, so it drifts from the `uses:` pin the
certificate will actually carry — the #314 failure, a literal frozen at
the signer's first commit while the `uses:` line was bumped twice, every
dispatch a guaranteed identity mismatch. Workflows use the
`verify-signed` action, which derives `--signer-digest` from the calling
workflow's own `sign.yml` pin at run time (`lint:signer-pin` reddens any
literal):

```yaml
- uses: monumental-archive/.github/.github/actions/verify-signed@<sha> # vX.Y.Z
  with:
    files: out/artifact-one out/artifact-two
    # bundle: path/to/bundle.jsonl   # offline mode, same identity pins
```

- The verification verdict (artifact VSA): **every class carries one,
  in the attestation store** — verdicts are rendered after the release
  publishes, and a published release is immutable, so the store is the
  VSA's only home (#209; releases cut before canon v1.13.0 instead
  carry `attestations-vsa-{crates,npm}.intoto.jsonl` as assets, two
  classes only). Fetch-then-filter, because GitHub's attestations API
  rejects the VSA predicate type as a query filter (`HTTP 422`,
  measured on v0.16.3) even though the attestation is present:

  ```bash
  gh api "repos/<owner>/<repo>/attestations/sha256:<digest>" \
    --jq '.attestations[].bundle' |
    jq -c 'select(.dsseEnvelope.payload | @base64d | fromjson
      | .predicateType == "https://slsa.dev/verification_summary/v1")' \
    > vsa.jsonl
  gh attestation verify <file-or-oci://ref@digest> --repo <owner>/<repo> \
    --signer-workflow monumental-archive/.github/.github/workflows/verify-release.yml \
    --predicate-type https://slsa.dev/verification_summary/v1 \
    --bundle vsa.jsonl
  ```

  **The verdict is the org's second root of trust (#264).** Provenance
  and producer evidence verify under the signer identity above; the
  verdict verifies under the VERIFIER's own —
  `monumental-archive/.github/.github/workflows/verify-release.yml`, the
  doubled `.github` correct as always. The workflow that computed the
  verdict is the certificate subject, so `verifier.id` is a tautology
  rather than a field taken on faith; pin `--signer-digest` to the canon
  release commit the publishing repo pinned. **Version boundary:**
  verdicts on releases cut before canon v1.14.0 were signed by the org
  signer instead — verify those with `--signer-workflow
  monumental-archive/signer/.github/workflows/sign.yml`, the recipe this
  one replaced (the same shape as the pre-v1.13.0
  `attestations-vsa-*` asset caveat below).

  Gate on `verificationResult: PASSED` and `verifiedLevels` instead of
  re-deriving the policy yourself; a consumer who distrusts verdicts
  still has the provenance bundles in the release.

  **A verdict is falsifiable from its own evidence list.** The predicate
  carries `inputAttestations` — every bundle the verifier opened, by URI
  and sha256 — appended inside the loop that verified each one, so it
  cannot list what was not read. A consumer who wants the verdict
  checked rather than trusted fetches each entry, confirms it verifies
  under the signer and that its subjects cover the artifact in hand. A
  verdict asserting something untrue must either list evidence that
  fails those checks or list none at all; a verdict whose evidence
  checks out is one you could have derived yourself. Expect
  `resourceUri: pkg:github/<owner>/<repo>@v<version>` — the release the
  verdict covers; per the VSA spec, this stated expectation is the
  out-of-band channel, so a VSA naming any other resource must be
  rejected. `verifiedLevels` claims `SLSA_BUILD_LEVEL_3` and only that:
  one level, one track, exactly what the verdict's own loops verified.
  For image subjects the verdict's byte basis is content addressing
  itself; what the org verified is the tag→digest binding and the
  signed provenance at the index digest (`release.md`). Absent on
  dry-run releases, by design: a rehearsal must never sign "PASSED".
- **The two comparisons the CLI has no flag for.** `gh attestation
  verify` compares builder identity and canonical source repository, but
  exposes nothing for `buildType` or `externalParameters` — the other
  two of the four fields `verifying-artifacts` asks a verifier to
  compare. The org's release-path verification asserts both before any
  verdict is assembled (`verify-release.yml`, verdict mode). A consumer
  completing their own verification reads the statement out of the
  verify call's JSON and asserts on it — expected `<owner>/<repo>` and
  tag substituted:

  ```bash
  gh attestation verify <file> --repo <owner>/<repo> \
    --signer-workflow monumental-archive/signer/.github/workflows/sign.yml \
    --format json |
    jq -e '.[0].verificationResult.statement.predicate.buildDefinition
      | .buildType == "https://actions.github.io/buildtypes/workflow/v1"
        and (.externalParameters | keys - ["workflow", "inputs"] == [])
        and (.externalParameters.workflow
             | .repository == "https://github.com/<owner>/<repo>"
               and .ref == "refs/tags/<tag>"
               and .path == ".github/workflows/publish.yml")'
  ```

  The spec's own wording is to *reject* unrecognised
  `externalParameters` fields, which is what the `keys` subtraction
  does. The canon repository's entry workflow is `self-publish.yml`
  rather than `publish.yml`; everything else in the org uses the canon
  filename.
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

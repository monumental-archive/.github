# SLSA dependency track

The org's position against the SLSA dependency track: what is enforced,
what is claimable, and what formal standing is — stated honestly, the
`source-track.md` discipline applied to dependencies. Stood up in #106;
the target and the declined doors were decided there and in #121/#122.

## The target: Level 2, by choice

| Level | Demands | Standing |
| --- | --- | --- |
| L1 | Inventory: know what you depend on | **Have** — lockfiles committed everywhere, per-release SBOMs on every release |
| L2 | Known vulnerabilities triaged per release | **Mechanisms met** — deny in the gate, blast-radius on the cron, every undecided finding exits through a signed VEX. One gap: the advisory sweep is weekly, not release-gated (below) |
| L3 | Producer-controlled locations | **Declined in writing (#121)** — permanent vendor weight for availability coverage that checksum integrity does not need |
| L4 | Acceptable-risk policy over L3 | **Declined (#122)** — sequential on L3 |

No level demands vulnerability-free. L2 demands *known, decided, and
written down* — the workflow the org already practised informally, now
mechanised so the decision record is signed and machine-readable.

## The mechanisms

### Ingestion: dependency-review (live before this standup)

GitHub dependency-review on every PR catches advisories and licence
problems at the moment a dependency enters. Already org-wide.

### The gate: `lint:deny` (cargo-deny — bans, licenses, sources)

Deterministic over `Cargo.lock` + the committed `deny.toml`
(scaffold-copied, like every stub). Rust-only by nature, universal by
guard: no tracked `Cargo.lock` → skip clean. `Cargo.lock` present but
cargo absent → loud failure, never a vacuous green.

The policy, maximally defensible:

- **licenses**: pure allowlist (the only shape the current config format
  offers — `unlicensed`/`copyleft`/`allow-osi-fsf-free` were removed
  upstream). Anything not allowed is denied by default.
- **sources**: crates.io only. `unknown-registry = "deny"`,
  `unknown-git = "deny"` — where dependencies come from is a gated
  invariant, and a git dependency is a decision, not a default.
- **bans**: `wildcards = "deny"`; `multiple-versions = "warn"` — the one
  deliberately non-maximal setting. Transitive duplicate versions are
  endemic to the ecosystem and outside any repo's control; a gate that is
  expected-red teaches people to ignore gates. A decision, not a
  deferral.

`cargo fetch --locked` precedes the check: fetching pinned inputs is the
same category as fetching the toolchain — the result is fully determined
by the lock, so gate eligibility holds.

### The cron: `audit:deny` (advisories)

`cargo deny check advisories` — RustSec feed, network-bound, structurally
outside the gate; Monday cron beside links and settings drift. Every
`ignore` entry carries a `reason` and must cite the VEX statement that
records the decision — the config format itself enforces the L2 shape
(`severity-threshold` and lint-level knobs were removed upstream;
everything errors, and a written ignore is the only exit).

### The cron: `audit:blast-radius` (osv-scanner over published SBOMs)

The L2 triage mechanism at scale and the honesty input VEX statements
wait on: *an advisory lands — which published releases, images and majors
ship it?* Walks the org's published releases' SPDX SBOMs (verified as
release assets before trust), scans each with osv-scanner (OSV includes
malicious-package data), and aggregates one report: advisory → affected
releases / images / majors.

Findings already covered by a signed VEX statement are filtered — red
always means *new and undecided*, never the standing advisory again.

Two triage classes, decided when the first image SBOM landed (v0.18.1,
~30 Debian base findings, most unfixable in stable — the oldest from
2005): **ecosystem packages** (cargo, npm — the org's own code surface)
always gate; **OS packages** gate only when a shipped fix exists —
lagging a fix is actionable, while the perpetual unfixed base-layer
background's remediation path is the rebuild cadence (`docs/release.md`
already states this: remediation is never per-CVE), so those are
reported, never red, and never worth a VEX that decides nothing.
Zero standing infrastructure; deterministic inputs (immutable release
artifacts); network only for the feed — which is exactly why it is
`audit:*` and can never be gate-eligible.

Failure modes are constructed loud:

- **exit 128** (osv-scanner parsed no packages) fails the job — a scan
  that reads nothing must never report "clean".
- **The canary**: a pinned lab release with a known-advisoried crate
  must produce its finding on every run. A blast-radius job that
  silently cannot see is worse than none; this converts that state into
  a red run. (The seeded finding: RUSTSEC-2021-0127, serde_cbor,
  measured present in release-lab's lock.)
- The report lands in the job summary **and** as a durable report — a
  green cron run nobody reads is write-only, per the release canon's
  report-only obligation.

### The exit: VEX, signed, never an override

Every unremediated finding exits through an OpenVEX statement — assembled
with vexctl from the blast-radius query, signed through the org's one
signer (the OpenVEX predicate type was allowlisted there ahead of this
standup), and attached raw as a `.openvex.json` release asset so
consuming tools eat the document. No `not_affected` is ever signed
without the blast-radius query behind it: a signed wrong `not_affected`
suppresses consumers' scanner findings on our word.

There is no second bookkeeping: the VEX **is** the triage record.
`audit:deny` ignores cite it; blast-radius filters on it.

Delivery, shaped by immutability: published releases cannot gain assets,
so a post-hoc decision reaches consumers on two surfaces — the signed
claim lands in the attestation store the moment the statement merges
(`vex-attest.yml`, one statement per merge, enforced — a decision is
reviewed like a release), and the raw document rides the *next* release
of each affected repository (`release/collect-vex.sh`). Roll-forward,
like everything else.

## SBOMs are class-shaped, derived from what the class ships

The version-source rule applied to SBOMs: derivation is by detection of
what the artifact actually is, never configuration.

| Artifact class | SBOM source | Why |
| --- | --- | --- |
| rust-crate, rust-binary, pgrx tarballs | trivy over `Cargo.lock` at the tagged commit | Deterministic; every PURL versioned |
| oci-image, pgrx images, continuous db image | trivy over the **published image by digest**, at the pull-back step | Captures OS packages and (via cargo-auditable) the Rust deps of the artifact a stranger pulls, not a local twin |
| Manifest-less (the canon, source-archive) | GitHub dependency-graph export | Its dependencies are actions, which the graph covers |
| pgrx artifact images | none, deliberately | `FROM scratch`: no OS layer, and their only content is the attested tarballs whose lock-derived SBOM already ships — an image with no surface of its own derives nothing new |

trivy is the single generator — already in the belt, no new tooling.
The GitHub dependency-graph export was replaced for code and image
classes on measurement, not preference: the lab's v0.17.0 export carried
2 versionless PURLs out of 236 (`pkg:cargo/mimalloc`,
`pkg:cargo/wasm-bindgen`) — versionless means unmatchable, silently
invisible to every scanner. The trivy derivation of the same lock:
229/229 versioned. The defect is unwritable, not detected.

**cargo-auditable** closes the image-side half of the same gap, also
measured: trivy over the published lab image found 79 OS packages and
**zero** Rust dependencies — nothing embeds them in the binary. With
`cargo auditable` in the rust build classes the dependency list lives in
a linker section of the shipped binary itself (the pipeline already
disables stripping precisely to preserve that section), and image SBOMs
gain the Rust surface. Binary and SBOM then agree by construction.

## Where L2 is met by cadence, not construction

The requirement is worded against the release, not the pull request:
*"Triage all vulnerable dependencies **before release** … an organization
MUST triage all known vulnerabilities and either remediate the
vulnerability, or not remediate in the given release."*

What runs today: `lint:deny` in the gate covers bans, licences and
sources — not advisories. The advisory feed is `audit:deny`, and
blast-radius is `audit:blast-radius`; both are Monday cron only
(`repo-audit.yml`, `audit.yml`). So a release cut on a Wednesday ships
having been triaged as of Monday, and a Tuesday advisory reaches
consumers before it reaches a decision.

The gate-determinism rule is not what blocks closing this. That rule
keeps network-bound checks out of the **`ci` gate**, and it is right; but
the release path is already network-bound by construction — it publishes
to registries and pulls the bytes back to prove them. An `audit:deny`
step between build and publish is the same category as `verify-published`
and would make the triage a property of every release rather than of the
calendar. Until it exists, this row is honest as "mechanisms met, one
requirement satisfied by cadence" — and the issue that adds the step
carries flipping this section and the table row above as part of its
done condition.

## Recorded verdicts

- **cargo-audit: skipped-subsumed.** Same RustSec DB, strictly a subset
  of `cargo deny check advisories`.
- **GUAC: watch-gated.** A standing service (ingestion + graph store) —
  the first thing in the org that would need to run and stay up —
  evaluated against `audit:blast-radius` (zero standing infrastructure)
  and declined at current scale. Revisit when cross-release queries are
  needed at a volume or complexity a scheduled walk cannot serve.
- **Dependency L3/L4: declined** (#121/#122) — the doors stay findable.
  One correction to how the L4 declination reads: the **level** is
  sequential ("this capability builds on Level 3"), but **cargo-vet is
  not**. It needs no vendoring and could run tomorrow. Its real blocker
  is the one #122's own body names — with one maintainer the uncovered
  tail makes the maintainer the auditor of last resort and queues
  Renovate behind a reading list — and that is a headcount boundary, like
  Source L4, not a sequencing one. Recorded so nobody later reads the
  door as technically barred when it is deliberately unopened. Note also
  that most of L4's letter already runs for its own reasons: the 7-day
  `minimumReleaseAge` is a quarantine period, and OSV's
  malicious-package data rides `audit:blast-radius`.
- **`multiple-versions = "warn"`**: the one non-maximal enforcement
  setting, reasoned above.
- **Hermetic ingestion** is the build track's business (#119), not this
  track's.

## Measured facts (2026-08-11, the standup verification)

- trivy 0.73.0 `Cargo.lock` → SPDX: 229 packages, 0 versionless PURLs.
- osv-scanner 2.4.0 over that SBOM: all 229 parsed, RUSTSEC-2021-0127
  found (the canary seed).
- trivy over `ghcr.io/monumental-archive/release-lab`: 79 packages
  (78 deb + 1 oci), 0 versionless, 10 with base-layer findings — and 0
  language-specific files, the cargo-auditable gap measured.
- GitHub dependency-graph export of the same repo: 236 packages, 2
  versionless — the replaced mechanism's defect, measured.
- Tool availability: cargo-deny 0.20.2 (aqua), osv-scanner 2.4.0 (aqua;
  registry lags upstream's 2.5.0), vexctl 0.4.4 (aqua), cargo-auditable
  0.7.5 (cargo backend, the cargo-pgrx precedent). syft not needed.

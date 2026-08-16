# SLSA dependency track

The org's position against the SLSA dependency track: what is enforced,
what is claimable, and what formal standing is — stated honestly, the
`source-track.md` discipline applied to dependencies. Stood up in #106;
the target and the declined doors were decided there and in #121/#122.

## The target: Level 2, by choice

<!-- tracks:dependency:begin -->

| Level | Demands | Standing |
| --- | --- | --- |
| L1 | Inventory: know what you depend on | **Have** — lockfiles committed everywhere, per-release SBOMs on every release |
| L2 | Known vulnerabilities triaged per release | **Met by construction** — deny in the gate, blast-radius on the cron, and the release path's commit point refuses every publish job while an advisory in its SBOM is undecided (`derive-vex.sh` + the `commit-point` barrier, #349); every decision exits through a signed VEX keyed by `package@version`. First exercised on lab v0.20.0, which it refused a release — and whose npm and GHCR uploads raced past the then-unwired graph, the defect #349 finding 1 closed — see "Where L2 is met by construction" |
| L3 | Producer-controlled locations | **Declined in writing (#121)** — permanent vendor weight for availability coverage that checksum integrity does not need |
| L4 | Acceptable-risk policy over L3 | **Declined (#122)** — sequential on L3 |

<!-- tracks:dependency:end -->

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
- **bans**: `wildcards = "deny"`; `multiple-versions = "deny"`. This read
  `"warn"` until 2026-08-16 — the org's one deliberately non-maximal
  setting, on the reasoning that transitive duplicates are endemic and
  outside any repo's control, and that an expected-red gate teaches
  people to ignore gates. Raised with the clippy standup (#445), which
  brought a second tool stating the same policy and made the
  contradiction visible: an expected-red gate is a gate nobody reads, but
  a *warning* nobody reads is the same failure with no ratchet at all.
  The tree is something a repo resolves — by aligning its own
  constraints, or by waiting on the upstream bump. Where upstream truly
  forces a duplicate, the exit is a written `[[bans.skip]]` entry citing
  the upstream issue: an exception with a name and a reason, in one
  place, exactly like the advisory `ignore` entries that cite their VEX
  statements. Since #445 that exception also EXPIRES: `lint:deny` removes
  each skip in turn and re-runs, so an entry that has stopped doing
  anything fails the gate by name rather than sitting there forever.

  **This file is the org's only statement of that policy.**
  `clippy::multiple_crate_versions` (cargo group) asserts the same rule
  and is therefore allowed in the canon's `clippy.toml`, with the reason
  recorded there. It is not a weakening: the two tools differ in *where
  their exception lists live*. `deny.toml` is per-repo, so a skip
  describes the one tree it belongs to; the canon's `clippy.toml` is a
  single file shared org-wide, so recording a duplicate there would
  silently exempt every other repo from a rule they never broke. One
  policy, one home, at the layer that can describe a single tree.

`cargo fetch --locked` precedes the check: fetching pinned inputs is the
same category as fetching the toolchain — the result is fully determined
by the lock, so gate eligibility holds.

### The cron: `audit:deny` (advisories)

`cargo deny check advisories` — RustSec feed, network-bound, structurally
outside the gate. It runs on the Monday cadence but NOT in the canon's
`audit.yml` beside links and settings drift: it is a per-repo
obligation, so it runs from `repo-audit.yml` via each repo's own
`audit.yml` stub — which means adoption is per repo and enumerated
nowhere the way `audit:source-vsa` enumerates chains (#349 finding 8
holds that open; an `audit:adoption` over the population is the
recorded follow-up). Every
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

Findings are filtered per exact `(advisory, package@version)` against
the decisions in `security/vex/` — red always means *new and
undecided*, never the standing advisory again, and never toil: a
decision is keyed by the dependency, so every release that ships the
decided `package@version` is covered by derivation, with no product
list to extend per release (#187). The per-version join is also the
drift guard, by construction — a bumped version matches no decision and
goes red for a fresh judgment; an advisory-ID-only filter would
silently extend the old judgment to a version nobody looked at.

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

Two report-only sections ride the same run (#187's sibling
improvements). For repos with os-package findings, the report names the
pending Renovate digest PR when one exists — the remediation is never
per-CVE, so the pointer saves the hunt. And decisions matching no
current finding are listed as **candidates for retirement**: coverage is
derived, so a withdrawn advisory or a dropped/bumped dependency needs no
document edit anywhere — the decision simply stops matching on every
surface (audit join, `derive-vex.sh`, `vex-attest.yml` subjects) and
sits inert in `security/vex/` as history. Deleting it is housekeeping,
prompted by this list, never a correctness requirement.

### The exit: VEX, signed, never an override

Every unremediated finding exits through an OpenVEX statement — assembled
with vexctl from the blast-radius query, signed through the org's one
signer (the OpenVEX predicate type was allowlisted there ahead of this
standup). No `not_affected` is ever signed without the blast-radius
query behind it: a signed wrong `not_affected` suppresses consumers'
scanner findings on our word.

Decisions are **keyed by the dependency, never the release**: a
statement's products name the exact `package@version` the judgment was
made against (`pkg:cargo/serde_cbor@0.11.2`), and which releases are
covered is *derived* from published SBOMs wherever it is needed — the
audit joins on it, `vex-attest.yml` resolves signing subjects from it,
and each release derives its own concrete VEX document from it. A
stored per-tag product list would be retyped every release and would
silently extend to a bumped version; the keying removes both failure
modes at once (`security/vex/README.md` states the contract and the
version dialect).

There is no second bookkeeping: the VEX **is** the triage record.
`audit:deny` ignores cite it; blast-radius joins on it; releases derive
from it.

Delivery, shaped by immutability: published releases cannot gain assets,
so a post-hoc decision reaches consumers on two surfaces — the signed
claim lands in the attestation store the moment the statement merges
(`vex-attest.yml`, one statement per merge, enforced — a decision is
reviewed like a release, its affected-release subjects derived from
published SBOMs), and every *subsequent* release of each affected
repository ships its own derived document (`release/derive-vex.sh`:
product = the release purl, the decided `package@version` as
subcomponent — standard concrete-product OpenVEX for consuming tools,
generated as a pure function of the reviewed decision and the release
SBOM). Roll-forward, like everything else.

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

## Where L2 is met by construction, not cadence

The requirement is worded against the release, not the pull request:
*"Triage all vulnerable dependencies **before release** … an organization
MUST triage all known vulnerabilities and either remediate the
vulnerability, or not remediate in the given release."*

This is now a property of the release path itself, on two feeds. The
`sbom` job in `publish.yml` runs `release/derive-vex.sh` after
generating the SBOM, which scans it with osv-scanner and **fails the
release** if any gate-class advisory in it has no decision for its
exact `package@version` — and the `commit-point` barrier turns that
failure into a `needs` edge every publish job waits on, so nothing
reaches a registry past it. The edge is load-bearing history, not
belt-and-braces: until #349 finding 1 the ordering was only a race the
org kept winning, and lab v0.20.0's npm and GHCR uploads went out
eleven seconds after this very gate went red. The
same job then runs `audit:deny` against the tagged lock (#211): the
identical belt task the Monday cron runs, so cargo-deny reads RustSec
directly (no OSV import lag) and additionally refuses yanked crates,
with the triage record unchanged — every `ignore` in `deny.toml`
carries a reason citing its VEX statement, so the exit is a written
decision and only its timing moves. Neither leg has a warn path: a
warning here converts the control into a log line (the #118 rule). A
Tuesday advisory against a Wednesday release is triaged on Wednesday or
the release does not happen. The Monday sweeps over *already-published*
releases — which no release-time gate can cover — remain: `audit:deny`
from `repo-audit.yml` via each repo's audit stub, and
`audit:blast-radius` from the canon's own `audit.yml`.

The gate-determinism rule is untouched: it keeps network-bound checks
out of the **`ci` gate**, and it is right. The release path is already
network-bound by construction — it publishes to registries and pulls
the bytes back to prove them — so the OSV feed at release time is the
same category as `verify-release`. Both legs are lab-proven, and the
`audit:deny` leg caught a real finding on its first live run: the
lab's v0.20.0 release was refused — RUSTSEC-2021-0127, `serde_cbor`
unmaintained via pgrx itself, RustSec-direct where the OSV gate-class
filter had not flagged it — and the fix shipped as v0.20.1 only behind
an `ignore` entry citing the pre-existing
`security/vex/RUSTSEC-2021-0127.openvex.json` decision. The written-
decision exit and the roll-forward behaved as designed; the refusal
did not, in full: the release (attach, VSAs, SBOM asset, DOI) was
blocked, but the npm package and the GHCR index published anyway,
because no publish job then carried a `needs` edge to the gate (#349
finding 1, closed by the `commit-point` barrier). An earlier version
of this page called the blocked run "v0.19.1", a tag that never
existed, and claimed it was "blocked before anything built" — both
corrected here rather than reworded away.

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
- **`multiple-versions`**: was the one non-maximal enforcement setting;
  raised to `deny` on 2026-08-16 with the clippy standup (#445). The org
  now has no deliberately non-maximal dependency setting. Reasoned
  above, including why the rule is stated here and not in clippy.
- **Hermetic ingestion** is the build track's business (#119), not this
  track's.

## Measured facts (2026-08-11, the standup verification)

- trivy 0.73.0 `Cargo.lock` → SPDX: 229 packages, 0 versionless PURLs.
- osv-scanner 2.4.0 over that SBOM: all 229 parsed, RUSTSEC-2021-0127
  found (the canary seed).
- trivy over `ghcr.io/monumental-archive/release-lab`: 79 packages
  (78 deb + 1 oci), 0 versionless, 10 with base-layer findings — and 0
  language-specific files, the cargo-auditable gap measured.
  **Superseded by construction, 2026-08-13 (#347/#354):** that image
  was `FROM debian:trixie-slim`. #295 moved the compile out of the
  Dockerfile and the image is now `FROM scratch` over a musl-static
  binary, so the same scan reads **6 packages, 0 deb**. The Debian
  population that motivated the OS-package triage class below left the
  published surface with it — the class still governs correctly, it
  simply has nothing to govern in this repo today. Re-measured at the
  same time: the `lab-pg` extension images are OCI **artifact
  carriers** (`*-artifact` tags), 93 packages, 90 cargo + 1 oci, also
  0 deb; bookworm and trixie are the pgrx build and smoke bases and
  are never published. Every package in the shipped extension image
  appears in the release SBOM, so blast-radius coverage holds by
  containment — a measured fact about today's build, not an invariant
  anything enforces.
- GitHub dependency-graph export of the same repo: 236 packages, 2
  versionless — the replaced mechanism's defect, measured.
- Tool availability: cargo-deny 0.20.2 (aqua), osv-scanner 2.4.0 (aqua;
  registry lags upstream's 2.5.0), vexctl 0.4.4 (aqua), cargo-auditable
  0.7.5 (cargo backend, the cargo-pgrx precedent). syft not needed.

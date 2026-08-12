# Direction

What this repository is for, what it is aiming at, and where the
ceiling deliberately sits. Read this first; everything else in `docs/`
is mechanism.

## The thesis

This repository is the organisation's **conformance root**. Every
shared rule, tool, workflow and setting lives here and nowhere else;
every other repository pins this one by SHA or tag and conforms to it.
Enforcement is by mechanism, not convention: the toolbelt pins the
tools, the `ci` task contract collects the linters, the reusable gate
runs them, the rulesets require the gate, and Renovate fans out every
bump at zero age.

The centralisation is the point, not a hazard. A governance layer this
concentrated looks unusual — most `.github` repositories are a README
and some issue templates — but the shape was chosen deliberately, with
the trade-offs argued in the standup pull requests (#5–#26) and the
deferred decisions recorded there. Review questions here take the form
"does this change serve the targets below", not "should the
architecture be this centralised". Repositories conform to the canon;
the canon does not adapt to repositories.

## The targets

The organisation builds against **SLSA v1.2**. The levels below are the
**current ceiling, by choice**: higher levels exist, are understood,
and are tracked in issues as doors to open later — they are not being
chased now. A target here is a claim only where a stranger can verify
it; everywhere else it is labelled as what it is.

| Track | Ceiling | Status | Enforced by |
| --- | --- | --- | --- |
| Build | **L3** | Met, and verifiable by strangers | The signer split (`id-token: write` lives only in a repo that runs no caller code — the capability boundary, linted), the reusable gate, App-minted tags, evidence bundles per release |
| Source | **L3** | **Met**, and verifiable by strangers: every revision on `main` carries a verifying chain link — signed source provenance plus a source VSA naming the `ORG_SOURCE_` properties live at that revision, chained in `refs/notes/commits` — and a gap reddens `audit:source-vsa` rather than passing quietly. See `source-track.md` for the level each link claims and for the one recorded gap | Org-level rulesets: required gate, required signatures, linear history, squash-only, locked `v*` tags, empty bypass lists — read from the rules API at emission time, never from configuration intent |
| Dependencies | **L2** | Met by construction: the release path refuses to publish with an undecided advisory in its SBOM (see `dependency-track.md`; first exercised on lab v0.19.1, which it blocked) | Exact pins with checksums (`mise.lock`), SHA-pinned actions, 7-day minimum release age, Renovate fan-out, cargo-deny in the gate, signed dependency-keyed VEX as the only exit |
| Build Environment | **L0** | **Formally L0** at both layers: L1's verify-before-instantiation obligation is implemented, its signed-build-image-provenance obligation belongs to producers who do not meet it (see `slsa-reference.md`) | Runner image a named, Renovate-rolled pin; pgrx bases digest-pinned, org-attested and verified fail-closed before any container runs |

The Build Environment row is a zero by attribution, not by effort. Its
controls are built; the *attestation* that would make them a level
belongs to image producers who do not sign what they publish, and that
is not fixed by building harder here — which is why both columns say L0
rather than describing it as "effectively L1".

Source was the same shape until 2026-08-12. It stopped being so by
emitting: the org built its own SCS control plane (#207) rather than
continuing to wait on an upstream engine, and the level moved the day
strangers could verify it.

Beyond the ceiling, the doors and their triggers are recorded; opening
one is a decision, not drift:

- issue #121 — Dependencies L3, vendoring: declined, availability risk
  accepted
- issue #122 — Dependencies L4, trust map
- issue #125 — Build Environment L2+: waits on runner attestation
- issue #126 — Source L4: waits on a second maintainer
- issue #199 — Source track: upstream `source-tool` as an independent
  cross-check of our VSAs, never the thing that issues them

**When a gap closes, this table is the first thing that changes.** The
BuildEnv row becomes L1 at the container layer the day base-image
provenance arrives signed and the org's verification of it is itself
attested; the Source row became L3 the day VSAs emitted. Any issue that
delivers one of those carries updating this row as part of its done
condition — a level that becomes true and goes unrecorded is the same
defect as one claimed before it was.

## The claims policy

Assert only what strangers can verify. A build-track claim is backed by
an attestation a stranger checks with `gh attestation verify`; a
verdict is backed by a signed VSA; a level with an unverifiable half
(Source and Build Environment, today) is stated with its gap named — as
L0, not as the level it nearly reaches. Badges follow the same
rule: they derive from how a repository actually publishes (#88), this
repository wears the same badges as its consumers, and no badge asserts
a level this document does not.

## Where the detail lives

| | |
| --- | --- |
| The mechanism inventory | `README.md` |
| The release machinery, end to end | `release.md` |
| The SLSA and attestation source material | `slsa-reference.md` |
| The build-platform self-assessment | `build-assessment.md` |
| The source-track position, honestly | `source-track.md` |
| The source-control self-assessment | `source-assessment.md` |
| The dependency-track position | `dependency-track.md` |
| The branch and tag rules | `rulesets.md` |
| Bringing a repository into conformance | `migration-playbook.md` |

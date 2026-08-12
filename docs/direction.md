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
| Source | **L3** | Controls at L3 substance; **formally L0** until source VSAs are emitted (engine parked — see `source-track.md`) | Org-level rulesets: required gate, required signatures, linear history, squash-only, locked `v*` tags, empty bypass lists |
| Dependencies | **L2** | Mechanisms met; advisory triage runs on the Monday cron rather than on the release path — the one requirement satisfied by cadence, not construction (see `dependency-track.md`) | Exact pins with checksums (`mise.lock`), SHA-pinned actions, 7-day minimum release age, Renovate fan-out, cargo-deny in the gate, signed VEX as the only exit |
| Build Environment | **L1** | **Formally L0** at both layers: L1's verify-before-instantiation obligation is implemented, its signed-build-image-provenance obligation belongs to producers who do not meet it (see `slsa-reference.md`) | Runner image a named, Renovate-rolled pin; pgrx bases digest-pinned, org-attested and verified fail-closed before any container runs |

Two of those rows name a gap rather than a level, and both gaps are the
same shape: the controls are built, the *attestation* that would make
them a level is not ours to emit. Source waits on an upstream engine
(#199); Build Environment waits on image producers signing what they
publish. Neither is fixed by building harder here, which is why both are
stated as L0 rather than described as "effectively L1".

Beyond the ceiling, the doors and their triggers are recorded; opening
one is a decision, not drift:

- issue #121 — Dependencies L3, vendoring: declined, availability risk
  accepted
- issue #122 — Dependencies L4, trust map
- issue #125 — Build Environment L2+: waits on runner attestation
- issue #126 — Source L4: waits on a second maintainer
- issue #199 — Source track re-adoption: waits on the upstream engine

**When a gap closes, this table is the first thing that changes.** The
BuildEnv row becomes L1 at the container layer the day base-image
provenance arrives signed and the org's verification of it is itself
attested; the Source row becomes L3 the day VSAs emit. Any issue that
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

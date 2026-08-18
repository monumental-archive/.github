# Source provenance: the org's record

SLSA v1.2 leaves source provenance "undefined and up to the SCSs to
determine" — and then requires, normatively at L2+, that *"the SCS
MUST document the format and intent of all Source Provenance
attestations it produces"* and how each can be used to reason about
the properties in the summary attestation. That documentation is
discharged in two halves since the emit cutover (#437, stele#101):
**the format is specified by stele** —
[`docs/chain-format.md`](https://github.com/monumental-archive/stele/blob/main/docs/chain-format.md),
whose examples stele's own tests execute, so the spec cannot drift
from the engine — and **this file is the org's record**: which values
the org binds the format's open points to, and the history of its
chains. The predicate type URI

```text
https://monumental-archive.github.io/attestations/source-provenance/v1
```

is an identifier, not a promise of a resolvable page; this file is
its referent, and it delegates the bytes to stele's spec.

## The org's values

What the format leaves to policy and convention, bound here:

- **Emitter**: `.github/actions/source-attest/` (clone prep; the
  logic is `stele emit chain`) under each repository's reserved
  `source-attest.yml` identity — the frozen root-of-trust contract in
  [`source-assessment.md`](source-assessment.md), which also declares
  the storage (notes committer) identity.
- **Ref**: `refs/heads/main`, the one protected branch the org model
  uses; links live in `refs/notes/commits`.
- **Controls vocabulary**: the `ORG_SOURCE_` property table, frozen
  in [`source-track.md`](source-track.md); each link carries the live
  properties with the rule content proving them, so a lapse
  under-claims.
- **Policy**: `slsa/verify-policy.json` — the link identity, the
  provenance predicate type, the protected-branch property
  requirements and `underclaimLevel`, `since`-gated from the
  continuity ledger in [`source-track.md`](source-track.md).
- **Healed links**: the spec's contemporaneity ask is met per link or
  deviated from honestly — a healed link carries `repaired` and the
  healing run's actor, and its level is computed, not chosen: the
  target level is claimed only when every contributing ruleset's
  `updated_at` predates the revision's `commitTime`, else the link
  under-claims with a named warning. A consumer requiring
  contemporaneous emission gates on the absence of `repaired`.

## Verifying

`stele verify` is the reference consumer; `audit:source-vsa` runs it
on schedule against the policy above, walking coverage and linkage
as independent properties. To verify one link by hand, follow the
stranger recipe in stele's
[`chain-format.md`](https://github.com/monumental-archive/stele/blob/main/docs/chain-format.md)
with this org's values: the calling repository's reserved
`source-attest.yml@refs/heads/main` identity and the GitHub Actions
OIDC issuer. Signatures cover `PAE(payloadType, statement)` — a
bare-statement `cosign verify-blob` fails on every note by design.

## History of the chains

The record of foundings, defects, and heals — kept because the
chains' own rule is that bad history is recorded, never designed
around:

- **#213** wrote the first format documentation; **#256/#265** built
  self-healing after `.github@e1ad2dde` lost its emitter run, with
  the healed-link semantics above.
- **#349** split the overloaded `prev` pointer into `ledgerPrev`
  (emission order) and `revisionParent` (ancestry) — note format v2 —
  after the audit showed a v1 heal forks the chain and the
  `e1ad2dde` leaf was unreachable by construction.
- **#434** found the bash emitter's ledger digest hashed the note
  minus its trailing newline, forking `.github`'s and stele's
  ledgers. The structural fix landed in stele (one shared digest,
  read-back hashing, compare-and-swap append); the defective spans
  were deleted and re-emitted through it (#437, #443).
- **2026-08-18, note format v3** (stele#84, adopted at #504): DSSE
  payload-type authentication — signatures cover
  `PAE(payloadType, statement)` — and v1/v2 reading retired whole.
  All four source chains were re-emitted at v3 from genesis through
  the heal path, `repaired` markers carried honestly; the prior
  ledgers are archived at `refs/archive/notes-v2-2026-08-18`, a
  historical record and not links in the live chains.

Genesis is an explicit founding dispatch, exactly once per history;
a gap is debt healed by late links, never a re-founding.

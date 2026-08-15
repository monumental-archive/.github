# Source provenance: the format, field by field

SLSA v1.2 leaves source provenance "undefined and up to the SCSs to
determine" — and then requires, normatively at L2+, that *"the SCS
MUST document the format and intent of all Source Provenance
attestations it produces"* and how each can be used to reason about
the properties in the summary attestation. **This document is that
documentation** (#213). The emitter is
`.github/actions/source-attest/` (logic) under each repository's
reserved `source-attest.yml` identity; the controls and the
`ORG_SOURCE_` property table live in
[`source-track.md`](source-track.md); the storage self-assessment in
[`source-assessment.md`](source-assessment.md). The predicate type URI

    https://monumental-archive.github.io/attestations/source-provenance/v1

is an identifier, not a promise of a resolvable page; this file is its
referent.

## The statement

An in-toto Statement/v1 whose subject is the **revision**:

| Field | Meaning, and where it comes from |
| --- | --- |
| `subject[0].digest.gitCommit` | The attested revision — the spec's subject form for the source track |
| `subject[0].uri` | The revision's page for humans (`…/commit/<sha>`) — the VSA SHOULD, carried on both statements (#267) |
| `subject[0].annotations.sourceRefs` | `["refs/heads/main"]` — the protected ref this attestation is about; the emitter attests protected-ref revisions only |

## The predicate

| Field | Meaning | Source |
| --- | --- | --- |
| `repository` | The attested repository, `https://github.com/<owner>/<repo>` | `GITHUB_REPOSITORY` |
| `ref` | Always `refs/heads/main` — the one protected branch the org model uses | Constant |
| `parents` | ALL parent SHAs of the revision (array; >1 for merges) | `git rev-parse <rev>^@` against the fetched history |
| `actor.login`, `actor.id` | Who triggered the emitting run — for a fresh link, the pusher. For a healed link this is the actor of the *healing* run, not of the original push: the original push actor is unrecoverable once its run is lost, and a guessed value would be worse than an honest one. Consumers wanting push-actor identity gate on `repaired` (below) | `github.actor` / `github.actor_id` |
| `commitTime` | The revision's committer timestamp (ISO 8601) — a git fact, contemporaneous even when the signature is not | `git show -s --format=%cI` |
| `rulesReadAt` | When the enforcement state was read from the rules API — the moment the `controls` describe | claims stage |
| `controls[]` | `{property, evidence}` pairs: each live `ORG_SOURCE_` property with the rule content that proves it, matched by content, never by ruleset name. The frozen property table is in [`source-track.md`](source-track.md); a property whose rule is not live is simply absent, which is how a lapse under-claims | claims stage, rules API |
| `ledgerPrev` | Note version 2 (#349 S3): `{revision, noteSha256}` of the previous **emitted** note — emission order, so healed links extend the tail — or `null` exactly once (genesis). See "The chain", below | ledger tail |
| `revisionParent` | Note version 2: the revision's git first-parent SHA, or `null` for a root commit — ancestry, semantic only, deliberately separate from `ledgerPrev` | `git rev-parse <rev>^` |
| `prev` | Note version 1 only (links emitted before the v2 split): `{revision, noteSha256}` of the first-parent parent's link, carrying both meanings at once — which is exactly the overload #349 finding 3 caught. Readers accept both versions | chain walk (legacy) |
| `canonRef` | The canon commit whose action code and source policy produced this link — always a full SHA (#267 refuses otherwise) | the `uses:` pin resolution |
| `repaired` | Present only on healed links: `{at: <timestamp>}`, the moment the late link was emitted (#265). See "Healed links" | emit loop |

## The chain

The ledger pointer names the previous **emitted** note: `ledgerPrev
.revision` is the revision whose note this link was signed on top of,
and `ledgerPrev.noteSha256` is the SHA-256 of that note's **raw blob**
— the exact bytes in `refs/notes/commits`, so any re-encoding is
detectable. A `noteSha256` mismatch proves the predecessor's note
changed after this link was emitted: either the note was rewritten
(the notes ref is world-readable history; a rewrite is visible) or the
chain is being presented out of order. Git ancestry travels separately
in `revisionParent` (and the full parent set in `parents`); in the
common per-push cadence the two pointers agree, and after a heal they
legitimately do not — that divergence is the record of the lapse, not
a defect. The chain is founded exactly once by an explicit genesis
dispatch (`ledgerPrev: null`; `prev: null` in version-1 notes), and
genesis is refused forever after on that history — a gap is debt,
never a re-founding.

Emission is self-healing (#265): every push walks from the pushed
revision down to the genesis link and emits a link for every revision
that lacks one, oldest first, so **coverage** is complete — every span
between genesis and tip carries links. What healing does NOT restore is
a single hash chain (#349 finding 3): a link emitted before the heal
already named the healed revision's *predecessor* as its `prev`, so the
healed link becomes a leaf nothing points at — `prev` is carrying two
meanings at once (ledger order and git first-parent ancestry), and a
healed link cannot satisfy both without rewriting an immutable
predecessor. Measured on this repo: `e1ad2dde`'s healed link and
`2b28c903` both name `prev = d52d91b8`, so a tip→genesis walk of the
version-1 chain visits 82 links for 83 revisions, and deleting that
healed note would break no `noteSha256` anywhere. The version-2 split
is the structural fix: `ledgerPrev` carries emission order, so a v2
heal **extends the tail** instead of forking, and `audit:source-vsa`
now checks two independent properties — **coverage** by walking git
history, and **linkage** by walking the ledger to genesis, proving
each step's `noteSha256` against the target's raw note bytes and
requiring every v2 link to be reachable. The one pre-v2 leaf
(`e1ad2dde`) is reported by name as known legacy, never silently
tolerated and never a red: the documented per-revision verification
procedure fully covers it, and rewriting immutable history to
re-thread it is exactly what the chain exists to forbid. The ledger
tail is verified against the published identity before anything signs
on top of it.

## Healed links

The spec asks that source provenance be created *"contemporaneously
with the branch being updated"*. A healed link cannot be — that is a
named deviation, not a hidden one: everything a healed link attests
(`commitTime`, `parents`, the revision itself) is read from git's own
contemporaneous record; only the signature over it is late, and
`repaired.at` says exactly how late.

A healed link's **level is computed, not chosen**. The claims payload
carries the `updated_at` of every ruleset contributing to the claims;
the target level is claimed only when every one of them predates the
revision's `commitTime` — the rules provably have not changed between
the commit and the (late) read. Otherwise the link under-claims
`SLSA_SOURCE_LEVEL_2` with a named warning. A consumer whose policy
requires contemporaneous emission gates on the absence of `repaired`;
one who accepts the continuity argument gates on `verifiedLevels` as
usual.

## How the VSA is derived — the reasoning path the spec asks for

The VSA (`slsa.dev/verification_summary/v1`, rendered through the
shared assembler `release/vsa-predicate.jq`, #267) is derived from the
**verified** provenance only: the provenance is signed first,
self-verified against the published identity with a stranger's inputs,
and the level and properties are then read back out of the verified
statement — never recomputed from live state, which by then is a
different moment.

- `verifiedLevels[0]` — the source level: the branch's target when
  every required property in `slsa/verify-policy.json`
  (`source.protectedBranches`, `since`-gated against the commit time)
  appears in `controls[].property` (and, for healed links, the
  continuity guard passes); the policy's `underclaimLevel` otherwise.
  The computation is stele's, shared with its verifier by
  construction.
- `verifiedLevels[1..]` — every `controls[].property`, verbatim: the
  `ORG_SOURCE_` properties are claimable exactly because the
  provenance records the rule content that proves each one.
- `policy.uri` + `policy.digest.gitCommit` — the canon policy file at
  `canonRef`: the same commit, so the policy that judged and the code
  that emitted are one resolution.
- `subject`, `resourceUri`, `slsaVersion: "1.2"` — the same revision,
  `git+https://github.com/<repo>`, and the spec version the Source
  track exists in.

## Storage and verification

The chain link is one git note on the revision in `refs/notes/commits`:

    {version: 1,
     provenance: {statement: <base64>, bundle: <Sigstore bundle>},
     vsa:        {statement: <base64>, bundle: <Sigstore bundle>}}

Statements travel base64 so the signed bytes survive any JSON
re-encoding; the note's committer identity is the declared constant in
[`source-assessment.md`](source-assessment.md) (storage). GitHub's
attestation store is deliberately not written: its subjects are sha256
artifact digests and this track's subject is a `gitCommit`.

Verify any link exactly as `audit:source-vsa` does:

    git fetch origin '+refs/notes/commits:refs/notes/commits'
    git notes show <rev> | jq -r '.provenance.statement' | base64 -d > st.json
    git notes show <rev> | jq -c '.provenance.bundle' > pb.json
    cosign verify-blob --bundle pb.json \
      --certificate-identity "https://github.com/<owner>/<repo>/.github/workflows/source-attest.yml@refs/heads/main" \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      st.json

— and the same two commands with `.vsa.…` for the summary attestation.
The identity is the calling repository's reserved workflow path, the
frozen root-of-trust contract in
[`source-assessment.md`](source-assessment.md).

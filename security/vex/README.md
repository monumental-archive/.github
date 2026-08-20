# VEX decisions — the org's dependency triage record

One OpenVEX document per decision, `*.openvex.json`. This directory is
the record the whole dependency track keys on: `audit:blast-radius`
joins against it, `stele derive vex` (publish.yml) derives each release's own VEX
from it, and a finding with no decision here is *undecided* — it fails
the Monday audit **and** fails any release whose SBOM ships it, until a
decision is written.

## The keying rule — dependency, never release

A triage judgment is a fact about a dependency: *this advisory, against
this exact `package@version`*. It is never a fact about a release tag.
So a statement's `products` name the component:

- `pkg:cargo/serde_cbor@0.11.2`
- `pkg:deb/debian/util-linux@2.41-5`

**Version dialect**: write the version exactly as osv-scanner reports it
— no Debian epoch, no purl percent-encoding, no `?arch=` qualifiers.
Every join in the machinery is exact string equality on
`(advisory, name, version)`.

Coverage is **derived, not stored**: every release whose SBOM ships the
decided `package@version` is covered, past and future, with nothing to
retype (#187). A release that bumps the version is deliberately *not*
covered — no decision matches, the finding is undecided, and a human
makes a fresh judgment. Drift is structural, not guarded. Never
enumerate release tags in `products`; a hand-kept coverage list is the
toil and the drift hazard this keying removes.

## The two surfaces a decision reaches

1. **The attestation store, immediately** — on merge, `vex-attest.yml`
   derives the affected-release set from published SBOMs and signs the
   document through the org's one signer over those releases' digests.
   A `workflow_dispatch` of the same workflow is the recovery path: it
   signs every decision here the store does not already hold, so a
   decision that missed its merge (#596) is healed by running it, with
   no filename to choose and nothing to re-sign on a healthy tree.
2. **The document surface, at each release** — `stele derive vex` emits the
   release's own OpenVEX asset (product = the release purl, the decided
   `package@version` as subcomponent), shipped under GitHub's release
   attestation like the SBOM it derives from.

## The contract (docs/dependency-track.md)

- **No `not_affected` without the blast-radius query behind it** — a
  signed wrong `not_affected` suppresses consumers' scanner findings on
  our word.
- Every `deny.toml` advisory `ignore` cites its statement here.
- One decision per document, one document per merge (enforced by
  `vex-attest.yml`).

Authoring a statement:

```bash
vexctl create --product "pkg:cargo/<name>@<version>" \
  --vuln "RUSTSEC-XXXX-XXXX" --status not_affected \
  --justification vulnerable_code_not_in_execute_path \
  --file security/vex/RUSTSEC-XXXX-XXXX.openvex.json
```

Statuses: `not_affected` (with a justification), `affected` (with an
action statement — what a consumer should do), `fixed`, or
`under_investigation` (a real status: it makes "we know, we are looking"
a signed public fact rather than silence).

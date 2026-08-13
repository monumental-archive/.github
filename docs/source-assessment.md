# Source control system self-assessment

The SLSA v1.2 source track says consumers cannot trust a source control
system without proof it meets the requirements, and its
assessing-source-systems page provides the prompts. On GitHub the
platform is not the whole SCS for this track: GitHub provides identity
management, rulesets, history enforcement and diffs, but emits no
source provenance and no source VSAs. Whoever emits them operates a
control-plane extension and verifier, and the assessment applies to
*them*. That will be this organisation. This document answers the
prompts for that role. The emitter is in-org (#207,
`source-track.md`), activated per repo and lab-first; this assessment
was published before it was ever exercised, so the trust contract was
externally visible ahead of the first signature rather than written to
fit it.

## Change management interface

GitHub pull requests. Approval rights are governed by repository roles
under org-level rulesets with empty bypass lists (`rulesets.md`). Plain
text renders as diffs; the org's artefact classes carry no
non-plain-text source. Trusted robots: Renovate (dependency bumps,
automerge under the full required gate) and the release App (tag
minting only, `org-release-tag`). Both operate under the same required
checks as humans; neither can approve a pull request.

## Control configuration and technical controls

Controls are org-level rulesets plus belt lints inside the required
gate — the full set, with meanings, is the `ORG_SOURCE_` table in
`source-track.md`. Regression protection:

- Ruleset edits are possible only in the GitHub UI by an org owner;
  every edit is timestamped by GitHub and the Monday audit
  (`audit.yml`, repo-settings drift) compares live settings against
  the recorded baseline, so silent drift surfaces on a cadence.
- A single maintainer *can* tamper with controls — there is one human
  (see Administration). The design compensates with observability,
  not headcount: ruleset timestamps are GitHub's own and reset the
  continuity ledger (`source-track.md`), so weakening a control is
  recorded evidence, not a silent act.
- GitHub-the-platform's administrators are above this assessment's
  trust boundary, as they are for every GitHub-hosted SCS.

## Control plane and verifier

The emitter: the per-repo workflow
`.github/workflows/source-attest.yml@refs/heads/main` — the frozen
identity (`source-track.md`, the signing identity section) — whose body
is the canon's `source-attest` composite action (#207). Composite steps
run inside the caller's job, so the certificate names the per-repo path
while the logic lives once, reviewed and gated, in the canon.

It runs as two jobs with disjoint capabilities: a **claims** job holding
only an environment-scoped read token, and an **attest** job holding the
signing identity and no secrets, consuming the claim set as data across
the job boundary. Reading org-level ruleset details needs a credential
the runner is not given ambiently, and a secret must never share a job
with the signing identity — so a compromised attest job has no secret to
take, and the read token can neither sign nor push.

**Administration.** One human administrator, stated plainly. Accounts
are 2FA-required org-wide. There are no cryptographic secrets to
access: signing is keyless (Sigstore), the certificate minted from the
workflow's ambient OIDC identity per run — nothing to store, rotate,
steal, or back up. The compromise-remediation story is the identity's:
certificates are minutes-lived, every signing lands in Rekor, and the
org's rekor-monitor workflow watches the log for signatures under org
identities that the org did not make.

**Control effectiveness.** The claims in every VSA are read from
GitHub's rules API at emission time, not asserted from configuration
intent — the ground truth is the platform's enforcement state, and the
Monday drift audit checks that state against the baseline continuously.

**Provenance generation.** On-push, contemporaneous with the ref
update, in the repo where the push event authentically exists. The
workflow runs zero caller-supplied code (the narrowed
capability-boundary rule, marker carried in the workflow). Situations
with no provenance: revisions predating each repo's genesis, and any
lapse — both are visible as gaps against the chain, and the Monday
`audit:source-vsa` walk asserts every revision since genesis carries a
verifying link, reporting holes under the attestation-debt pattern.

**VSA generation.** Derived from the SCS-issued provenance only (the
L2+ requirement), never computed independently; each run verifies the
previous chain link against the pinned org identity before appending.

**Development practices.** The emitter workflow is itself version
controlled in the repo it attests, protected by the very rulesets it
claims, and gated by the same required check; the action it invokes is
version controlled and gated in the canon, and reaches the workflow
only through a SHA-pinned reference (`source-track.md`, activation
checklist). Communications are GitHub's TLS.

## Storage

Revisions, provenance and VSAs live in the repos themselves: git
notes, `refs/notes/commits` — seeded in every org repo, world-readable
because every org repo is public. GitHub's attestation store is
deliberately not written for this track — its subjects are sha256
artifact digests, and the source track's subject is a `gitCommit`
revision; a store entry would attest a different subject than the one
the spec verifies. Tampering with notes history is constrained by the
chain (each link verifies its predecessor against the pinned identity);
cross-project isolation is GitHub's repository model.

Every chain-link note is committed under the emitter's declared git
identity — `source-attest <source-attest@monumental-archive.github.io>`,
a constant set by the emitter action itself, never inherited from the
environment. It is part of this contract because the note author lands
in the world-readable ledger permanently and a consumer walking
`refs/notes/commits` sees it: notes authored under any other identity
were not written by the emitter. (The email is a designation, not a
mailbox — the domain is the org's Pages origin, same as the provenance
predicate type.)

## Recorded residuals

**The release tag object is unsigned** (#349 finding 9). The minting
script runs `git tag -a` as the App and nothing signs the tag object,
so `git verify-tag vX.Y.Z` returns nothing — platform behaviour, not
misconfiguration: API-created commits get GitHub's web-flow signature,
tag objects do not. No level is affected and no document over-claims:
the spec's tag requirement is immutability, which `org-default-tag`
enforces and `ORG_SOURCE_TAG_IMMUTABLE` claims, and the build-time
tag→revision binding is a signed claim in the provenance certificate
(`sourceRepositoryRef` + `sourceRepositoryDigest`). What is recorded
here is what remains: (1) the tag object is the only pointer in the
release chain whose integrity rests on a platform control plus GitHub's
API answer rather than a signature; (2) `ORG_SOURCE_RELEASE_TAG_MINTED`
evidences ruleset enforcement at attestation time, not the provenance
of any particular tag object — links are immutable and tags can be
created later; (3) the sharp edge is break-glass: a hand-minted tag
leaves no cryptographic trace at all, so the manual ledger line in
`continuity.md` is load-bearing evidence, not bookkeeping. The close is
gitsign at the mint (#349 S4) — keyless, the same Sigstore identity as
everything else — deferred because it touches the irreversible step and
takes a full lab cycle, not because it is optional.

## Root of trust

What a consumer pins, in the verifying-source page's configuration
shape:

```json
{
  "slsaSourceRootsOfTrust": [
    {
      "sigstore": {
        "root": "global",
        "subjectAlternativeNamePattern":
          "https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main"
      },
      "scsId":
        "https://github.com/monumental-archive/<repo>/.github/workflows/source-attest.yml@refs/heads/main",
      "slsaSourceLevel": 3
    }
  ]
}
```

One entry per org repo, `<repo>` substituted. The issuer is GitHub's
OIDC (`https://token.actions.githubusercontent.com`). **This identity
is frozen**: renaming or moving `source-attest.yml` is a breaking
change to this contract and will not happen; content changes freely.
These identities are live: since 2026-08-12 every revision reaching
`main` in each repo is signed under them (`source-track.md`). A consumer
configuring the above verifies real claims — genesis revisions and the
verification command are in `source-track.md`. Revisions predating each
repo's genesis carry no VSA and are Source Level 0 by the spec's own
rule; the chain says where it starts rather than implying it always
existed.

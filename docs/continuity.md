# Access continuity

The organisation currently has one maintainer. This document is the
succession and break-glass record that OpenSSF Best Practices Silver
(`access_continuity`) asks for: what happens if the maintainer is
unavailable, and which single points of failure are accepted rather than
accidental.

## Assets and their recovery paths

| Asset | Held by | If the maintainer is unavailable |
| --- | --- | --- |
| GitHub organisation ownership | Carl Allen (2FA: TOTP + passkey + recovery codes, stored in Apple Passwords) | GitHub's [deceased user policy](https://docs.github.com/en/site-policy/other-site-policies/github-deceased-user-policy) or account-recovery flow; recovery codes are in the personal password manager, reachable by the estate |
| Tag-mint App private key | Org secret, `visibility: selected` | An org owner can rotate: generate a new key on the App, replace the secret. A dead App is break-glass below |
| crates.io / npm ownership | Carl's registry accounts, trusted publishing only | Registry account recovery; no API tokens exist to leak or lose |
| Zenodo / DOI | Carl's Zenodo account | Zenodo support; DOIs already minted are permanent regardless |
| Signing identity | No key exists — Sigstore keyless via `signer`'s workflow identity | Nothing to lose: identity is the workflow ref, recreated by the repository itself |
| `SOURCE_RULES_TOKEN` | Fine-grained PAT, `Administration: Read-only`, held in each repo's `source-attest` environment | The one standing credential in the design. Mint a replacement and re-set the environment secret; its scope list names every repo running the emitter, so it grows on each import (`source-track.md`, activation). Expiry is loud, not silent: `stele derive claims` refuses to claim from a blind read, so the next push after expiry goes red rather than under-claiming |

## Break-glass: the tag-mint App dies

The `v*` tag ruleset lists the App as sole bypass. If the App is deleted
or its key unrecoverable: an org owner disables the tag ruleset, mints the
tag by hand, re-enables the ruleset, and records the event in the release
notes. This is deliberate: the lock protects against surprise, not against
the owner.

**It is also a continuity-resetting event, and must be recorded as one.**
Disabling `org-release-tag` drops `ORG_SOURCE_RELEASE_TAG_MINTED` from
every source VSA emitted while it is off, and the spec resets that
control's clock from the next revision. The emitter degrades honestly by
itself — the claim is read from the live rules API, so the property is
simply absent and the VSA under-claims — but the *ledger* is manual:
append a boundary to the continuity ledger in
[`source-track.md`](source-track.md) with the timestamp and each repo's
first revision after it. The same applies to the ruleset disable in
[`expunging.md`](expunging.md), which already says so.

## Succession

If the maintainer is permanently unavailable, the intended disposition is
recorded in the estate: the organisation and its repositories are public
and archival by design — every repository is buildable from a public
clone, releases carry their own evidence, and nothing requires a secret to
*consume*. A successor maintainer needs only organisation ownership; every
other credential can be rotated or recreated from it, per the table above.

## Deliberate single points of failure

- One human. Tier-2+ Branch-Protection, two-person review, and Best
  Practices Gold are acknowledged as unreachable until a second maintainer
  exists ([`slsa-reference.md`](slsa-reference.md)); the controls that do
  not need a second human are all enabled anyway.
- One password manager (Apple Passwords) for the recovery material. Its
  own recovery is the estate's problem, documented there.

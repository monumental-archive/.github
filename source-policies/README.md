# Source-track policies

`default.json` is the org policy, shared by every repo — consumed by each
repository's `source-attest` workflow via sourcetool's
`--use-local-policy`. It pins the protected branch's target SLSA source
level and the `ORG_SOURCE_` properties claimed for it (only
status-check-backed properties are expressible; everything else the
provenance carries as built-in `SLSA_SOURCE_SCS_*` controls). One file
works for all repos because the controls it names are org-level rulesets
scoped `~ALL`: nothing in the policy is repo-specific. A repo that ever
genuinely diverges gets its own `<repo>.json`, which its workflow prefers
over the default — the Renovate-preset pattern.

Kept here rather than in the upstream community policy repo
(`slsa-framework/source-policies`), deliberately: self-contained over
third-party two-party control. The trade is honest and documented — the
same maintainer the policy constrains can change it, but only through a
pull request onto this repo's gated, attested `main`, so a policy change
is itself a recorded, signed event in the org's own chain.

`Since` times are continuity starts (SLSA v1.2): they may only move
backwards with evidence, and weakening a policy is a level-resetting
event for the claims downstream of it.

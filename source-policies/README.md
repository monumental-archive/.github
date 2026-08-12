# Source-track policies

`default.json` is the org policy, shared by every repo — consumed by
each repository's `source-attest` workflow through the canon's
`source-attest` action (#207). It pins the protected branch's target
SLSA source level and the `ORG_SOURCE_` properties required to claim
it; the property names implement the frozen table in
`docs/source-track.md` verbatim. One file works for all repos because
the controls it names are org-level rulesets scoped `~ALL`: nothing in
the policy is repo-specific. A repo that ever genuinely diverges gets
its own `<repo>.json`, which the action prefers over the default — the
Renovate-preset pattern.

The emitter never claims from this file: claims are read from the rules
API at emission time, and the policy only decides what level the
verified claim set earns. A required property missing at emission
under-claims to level 2 in that revision's VSA — red for the Monday
audit, never a silent pass.

`since` times are continuity starts, copied from the ledger in
`docs/source-track.md` (GitHub's own ruleset timestamps, boundaries A
and B). They may only move backwards with evidence, and weakening a
policy is a level-resetting event for the claims downstream of it. The
first version of this file (#128) also carried a chain-start `since`
for the old engine's level computation; this emitter needs none — the
v1.2 VSA is constitutive, so the level 3 claim structurally starts at
each repo's genesis link and a revision without a VSA is level 0 by
definition.

Kept here rather than in the upstream community policy repo
(`slsa-framework/source-policies`), deliberately: self-contained over
third-party two-party control. The trade is honest and documented — the
same maintainer the policy constrains can change it, but only through a
pull request onto this repo's gated, attested `main`, so a policy change
is itself a recorded, signed event in the org's own chain.

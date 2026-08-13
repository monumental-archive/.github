<!--
The badge CATALOGUE (#88, #316 finding 5): every shield a repository in
this organisation could carry, each annotated with what earns it and
how the Monday audit decides it still tells the truth. This file is
reference, not a template to paste: the badge block itself is RENDERED
by `mise run fix:badges` between `badges:begin`/`badges:end` markers in
the README, from the repository's own tree facts plus the two-word
states in `.badge-states` — and `lint:badges` reddens a hand-edited
block. The canon is consumer #1: it wears the subset its own derivation
resolves, through the same machinery, with no special case.

Universal shields — derived for every repo:

  workflow status   the repo's own gate; earned by calling ci.yml
  OpenSSF Scorecard earned by scorecard.yml; audited against the
                    per-repo ratcheted floor in scorecard-floors.txt
  SLSA Build L3     the org's claimed tracks, from direction.md's
  SLSA Source L3    table — the audit parses track and level out of
  SLSA Dep L2       each shield and matches that row, so a shield can
                    never outrun the table. BuildEnv is deliberately
                    unshielded: L0 is the absence of a claim, and a
                    shield asserting an absence is noise (#316).
  REUSE status      earned by registering at api.reuse.software/register
                    (no account — name, email, project URL, confirmation
                    link; `lint:reuse` in the gate proves the tree
                    compliant BEFORE registering) — `reuse pending` in
                    .badge-states until the human step lands, then
                    `reuse registered`
  OpenSSF Best      earned by answering the form from
  Practices         docs/best-practices.md — `bestpractices pending`
                    until it binds, then `bestpractices <BP_ID>`
  OSPS Baseline     hosted by bestpractices.dev alongside the CII badge
                    (L1–3; #168 watches drift) — joins the render once
                    a BP_ID exists and the badge URL is confirmed at
                    registration
  coverage          earned by a `.coverage-floor` (the ratchet's
                    number); the canon carries `coverage exemplary`
                    instead — no data by construction until the kcov
                    standup, permanent, which is the point of an
                    exemplar wearing its own machinery
  DOI               earned by `mint-doi: true` + the concept DOI that
                    CITATION.cff records after the first mint; renders
                    pending until the concept exists
  fair-software     computed, never asserted: five dots for
                    repository/licence/registry/citation/checklist,
                    ● only for criteria this repo actually meets —
                    the static 5/5 image this file used to ship
                    asserted fullness regardless of truth (#316)

Registry-fact shields — derived from the publish stub's classes, named
by `.badge-states` lines where a name is not the repo's own
(`crates <name>`, `npm <name>`, extra `ghcr <name>` per image):

  crates.io + docs.rs   rust-crate classes (docs.rs builds on publish)
  npm                   wasm-npm classes (@monumental-archive scope)
  ghcr                  oci-image / pgrx-extension classes — the shield
                        names the IMAGE and links its package page, and
                        the audit asks GitHub whether that package
                        exists (the org packages page returns 200 for
                        any org and proves nothing, #316 addendum C)

Passive observers — NO wiring exists or is needed; they index public
repos on their own: deps.dev / OpenSSF criticality score, Libraries.io
SourceRank, Snyk Advisor, ClearlyDefined (harvests licence facts — our
SPDX/REUSE metadata is what it reads). Listed so nobody goes looking
for a switch that does not exist. CLOMonitor is
foundation-membership-scoped and deliberately out (#88).
-->

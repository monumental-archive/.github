# Org rulesets

The org's branch and tag rules, applied **org-level** in the GitHub UI
(Team plan), `enforcement: active`, scoped `~ALL` — which is dynamic, so
repositories transferring in later are covered with no further step.

The UI is the enforcement *and* the source of truth. This document is the
record of what is configured and why. There is deliberately no JSON mirror
and no apply script: three rulesets across one org is a settings screen,
not infrastructure. Keep this document true when a ruleset changes — and
note that weakening a rule is a SLSA **continuity-resetting event** for
every source-track claim that names it (see `slsa-reference.md`), so a
ruleset edit is never routine.

## The three rulesets

**`org-default-branch`** — every repo's default branch:

- block deletion, block force push (`non_fast_forward`)
- required linear history
- required signatures
- required status check `ci / ci`, strict (branch must be current)
- pull request required; stale reviews dismissed on push; review threads
  must be resolved; allowed merge methods `squash` and `rebase`
- `bypass_actors` empty

**`org-default-tag`** — all tags, every repo: no creation-time lock, but
block update, move and deletion. Tags are immutable once created.

**`org-release-tag`** — `v*` tags, every repo: creation restricted; sole
bypass actor is the tag-minting App (`monumental-archive-tag-mint`, App id
4534781, `Integration`, installed org-wide).

## A ruleset lands with its enabler, never before it

The `v*` creation lock's enabler is the minting App, which exists and is
the sole bypass actor — both halves proven (below), so it runs `active`.

The branch ruleset's enabler is the shared gate: `required_status_checks`
naming a context a repository never reports leaves its pull requests at
*"Expected — waiting for status to be reported"* forever. The fix is
always to adopt the gate in that repository, never to soften the rule.

The trap there: a repo can run perfectly good CI and still fail, because
the **check name is the contract**. A repo can lint harder than the shared
gate does and still never report `ci / ci`, which leaves its pull requests
waiting forever. A repository that transfers in must adopt the gate
*before* it can merge anything — the intended order, not a hazard.

## Why these rules and not more

Scored against OpenSSF Scorecard's `Branch-Protection` check, whose tiers
and weights are in `checks/evaluation/branch_protection.go`:

| Tier | Points | Requires |
| --- | --- | --- |
| 1 | 3 | block deletion + block force push |
| 2 | 3 | required approving reviews ≥ 1 **and** up-to-date branches + last-push approval + PRs required to change code |
| 3 | 2 | required status checks |
| 4 | 1 | `minReviews = 2` **and** code-owner review |
| 5 | 1 | dismiss stale reviews **and** protection applies to admins |

**The tiers are sequentially gated, and this is the part that matters.**
`computeFinalScore` adds a tier's points and then *returns early* if that
tier was not scored at its maximum:

```go
score += normalizeScore(basicScore, maxBasicScore, basicLevel)
if basicScore < maxBasicScore { return int(score), nil }
score += normalizeScore(adminNonAdminReviewScore, …)
if adminNonAdminReviewScore < … { return int(score), nil }
score += normalizeScore(contextScore, …)   // never reached unless tier 2 maxed
```

So a tier cannot be claimed out of order. Required status checks are tier
3 and score **nothing** while tier 2 is unmet, no matter how strictly they
are configured.

Tier 2 needs `required_approving_review_count > 0`, and GitHub will not
let you approve your own pull request. So for a solo maintainer the check
stops after tier 1: **3/10, and no configuration changes that.** Being an
organisation does not change it either — the Team plan buys org-level
application, which is governance rather than score.

**Everything above tier 1 is therefore configured for its own sake, not
for points**, and that is the right reason: required status checks, linear
history, required signatures and an empty bypass list each prevent a real
failure. The badge effort belongs on the other nineteen checks, which are
not gated and where sixteen can reach 10/10.

Revisit the moment a second maintainer exists: tiers 2, 3 and 4 unlock
together and are worth six points, not four.

## Deliberate choices

**`bypass_actors` is empty on the branch ruleset, deliberately.**
Scorecard's `branchProtectionAppliesToAdmins` probe checks exactly this,
and an exemption for yourself is the common way to score zero on it while
believing the branch is protected. `current_user_can_bypass: "never"` —
the org owner is bound too.

**Merge methods are `squash` and `rebase` only**: `required_linear_history`
blocks merge commits, so allowing the merge-commit button would offer a
method that always fails at merge time. (Whether `rebase` should also go —
squash-only is what makes "every revision reachable from a protected
branch was approved" claimable as an `ORG_SOURCE_` property — is a #120
decision.)

**`required_signatures` and the tag rules earn no Scorecard points** —
there is no probe for either. They are here because the attestation chain
depends on them: provenance names a tagged commit, and a movable tag
breaks every signature that references it.

## The release-tag lock (proven, and live)

`v*` tag creation is restricted so release tags cannot exist except via
the pipeline (`release.md`). Activation had a hard order, since doing it
early locks releasing out entirely, and every step is done, 2026-08-09:

- **Negative**: a human pushing `v9.9.9-negative-test` is rejected —
  `GH013 ... Cannot create ref due to creations being restricted.`
- **Positive**: merging a release PR mints an annotated tag and a draft
  release through the App's bypass.
- Applied org-wide at `active` on the Team upgrade, alongside a phase-2
  pipeline proven end to end in the lab.

**`evaluate` mode proves neither half.** It enforces nothing, so a human
push is not rejected; and a bypass actor records no evaluation, so the
App's bypass leaves no evidence either — the rule-suites log stays empty
and looks indistinguishable from a working lock. Prove the rule at
`active` on a throwaway repository, never at `evaluate`.

Break-glass for a dead App is an org admin disabling the release-tag
ruleset — record the change here, don't click and forget.

# Org rulesets, as code

The intended org-level rulesets, kept here so they are reviewable and
reproducible rather than clicked into a settings UI and hoped to match.

Org rulesets require **GitHub Team**. **Applied 2026-08-09**: all three are
live at org level, `enforcement: active`, scoped `~ALL` — which is dynamic,
so repositories transferring in later are covered with no further step. The
per-repository copies that stood in beforehand have been removed, so there
is one source of truth rather than two to reconcile.

```bash
gh api -X POST orgs/monumental-archive/rulesets --input rulesets/org-default-branch.json
```

The same JSON also applies per repository (drop the `repository_name`
condition — the repo-level API rejects it), which is how the release lab
ran its lock before the org upgrade.

**A ruleset lands with its enabler, never before it.**

The `v*` creation lock's enabler is the minting App, and it **exists**:
`monumental-archive-tag-mint`, App id 4534781, installed org-wide, the sole
`Integration` bypass actor in `org-release-tag.json`. Both halves are proven
(see below), so that ruleset ships `active` — there is nothing left to wait
for.

The branch ruleset's enabler is the shared gate: `required_status_checks`
naming a context a repository never reports leaves its pull requests at
*"Expected — waiting for status to be reported"* forever. The fix is always
to adopt the gate in that repository, never to soften the rule.

The trap there: a repo can run perfectly good CI and still fail, because the
**check name is the contract**. A repo can lint harder than the shared gate
does and still never report `ci / ci`, which leaves its pull requests
waiting forever.

**Cleared 2026-08-09.** `signer` and `release-lab` both adopted the shared
gate and both report `ci / ci` — observed on monumental-archive/signer#11
and monumental-archive/release-lab#11 — so the org ruleset went straight to
`enforcement: "active"` with no repository left waiting. A repository that
transfers in later must adopt the gate *before* it can merge anything,
which is the intended order rather than a hazard.

## Why these rules and not more

Scored against OpenSSF Scorecard's `Branch-Protection` check, whose tiers and
weights are in `checks/evaluation/branch_protection.go`:

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

So a tier cannot be claimed out of order. Required status checks are tier 3
and score **nothing** while tier 2 is unmet, no matter how strictly they are
configured.

Tier 2 needs `required_approving_review_count > 0`, and GitHub will not let
you approve your own pull request. So for a solo maintainer the check stops
after tier 1: **3/10, and no configuration changes that.** Two earlier
statements were wrong and are corrected here — this file previously claimed
tiers 1 and 3 were "fully claimed" (tier 3 is unreachable without tier 2),
and put "protection applies to admins" in tier 2 when the source puts
`branchProtectionAppliesToAdmins` in tier 5.

Being an organisation does not change this. What the Team plan buys is
org-level application, which is governance rather than score.

**Everything above tier 1 is therefore configured for its own sake, not for
points**, and that is the right reason: required status checks, linear
history, required signatures and an empty bypass list each prevent a real
failure. The badge effort belongs on the other nineteen checks, which are
not gated and where sixteen can reach 10/10.

Revisit the moment a second maintainer exists: tiers 2, 3 and 4 unlock
together and are worth six points, not four.

## What is deliberately absent

**`required_status_checks` requires `ci / ci`** — the shared gate's one
standard check name (caller job `ci` calling the reusable workflow's job
`ci`). Repo-specific checks stay repo-level, but the org gate is nameable
org-wide precisely because every repo funnels through one summary job.
Strict policy: the branch must be current before merging.

**`bypass_actors` is empty, deliberately.** Scorecard's
`branchProtectionAppliesToAdmins` probe checks exactly this, and an
exemption for yourself is the common way to score zero on it while believing
the branch is protected.

Merge methods are `squash` and `rebase` only: `required_linear_history`
blocks merge commits, so allowing the merge-commit button would offer a
method that always fails at merge time.

## The release-tag lock (proven, and live)

`org-release-tag.json` restricts `v*` tag **creation** so release tags cannot
exist except via the pipeline (docs/release.md). It is **live org-wide at
`active`**. Activation had a hard order, since doing it early locks
releasing out entirely, and every step is now done:

1. ~~The tag-minting GitHub App exists and its installation is org-wide.~~
   Done: `monumental-archive-tag-mint`, App id 4534781, installed org-wide.
2. ~~Its integration id is inserted as the sole bypass actor.~~ Done — it is
   the JSON's one `Integration` bypass entry.
3. ~~Both halves proven in the release lab.~~ Done, 2026-08-09, at repo
   level with `enforcement: "active"`:
   - **Negative**: a human pushing `v9.9.9-negative-test` is rejected —
     `GH013 ... Cannot create ref due to creations being restricted.`
   - **Positive**: merging a release PR mints an annotated tag and a draft
     release through the App's bypass.
   - `current_user_can_bypass: "never"` confirms the org owner is bound too.
4. ~~Apply org-wide and flip to `active`, together with the pipeline.~~
   Done 2026-08-09, on the Team upgrade, alongside a phase-2 pipeline
   proven end to end in the lab.

**`evaluate` mode proves neither half.** It enforces nothing, so a human
push is not rejected; and a bypass actor records no evaluation, so the App's
bypass leaves no evidence either — `rulesets/rule-suites` stays empty and
looks indistinguishable from a working lock. Prove the rule at `active` on a
throwaway repository, never at `evaluate`.

Break-glass for a dead App is an org admin disabling this ruleset — recorded
here as a change, not clicked and forgotten.

## Beyond Scorecard

`required_signatures` and the tag rules earn no Scorecard points — there is
no probe for either. They are here because the attestation chain depends on
them: provenance names a tagged commit, and a movable tag breaks every
signature that references it.

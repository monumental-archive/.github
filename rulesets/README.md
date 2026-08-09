# Org rulesets, as code

The intended org-level rulesets, kept here so they are reviewable and
reproducible rather than clicked into a settings UI and hoped to match.

**These files are not applied by anything.** Org rulesets are org settings,
not repository content. Apply with:

```bash
gh api -X POST orgs/monumental-archive/rulesets --input rulesets/org-default-branch.json
```

Org rulesets require **GitHub Team**. Until then the same JSON applies at
repository level, which works on Free for public repositories:

```bash
gh api -X POST repos/monumental-archive/<repo>/rulesets --input rulesets/org-default-branch.json
```

(Strip the `repository_name` condition for the repo-level call — it is only
meaningful org-wide.)

## Why these rules and not more

Scored against OpenSSF Scorecard's `Branch-Protection` check, whose tiers and
weights are in `checks/evaluation/branch_protection.go`:

| Tier | Points | Requires |
| --- | --- | --- |
| 1 | 3 | block deletion + block force push |
| 2 | 3 | required approving reviews ≥ 1, protection applies to admins |
| 3 | 2 | required status checks |
| 4 | 1 | `minReviews = 2` **and** code-owner review |
| 5 | 1 | admin thorough review |

Tiers 1 and 3 are fully claimed. **Tiers 2, 4 and 5 are unreachable for a
solo maintainer** and no setting substitutes: GitHub will not let you approve
your own pull request, so `required_approving_review_count: 1` would stop you
merging anything, and tier 4 hard-codes two reviewers. The count is therefore
deliberately `0` — raising it buys no score and costs the ability to ship.

Revisit the moment a second maintainer exists; tiers 2 and 4 become available
together and are worth four points.

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

## The release-tag lock (proven; staged for the org)

`org-release-tag.json` restricts `v*` tag **creation** so release tags cannot
exist except via the pipeline (docs/release.md). It still ships with
`enforcement: "disabled"` in this repo because org-level application waits on
the Team plan — but the rule itself is proven, and activation has a hard
order, since doing it early locks releasing out entirely:

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
4. Apply org-wide and flip to `active`, together with the pipeline. Blocked
   on the Team plan (org rulesets are a Team feature).

**`evaluate` mode proves neither half.** It enforces nothing, so a human
push is not rejected; and a bypass actor records no evaluation, so the App's
bypass leaves no evidence either — `rulesets/rule-suites` stays empty and
looks indistinguishable from a working lock. Prove the rule at `active` on a
throwaway repository, never at `evaluate`.

Break-glass for a dead App is an org admin disabling this ruleset — recorded
here as a change, not clicked and forgotten.

## Planned

- **Org-wide required status check** on a standard summary job name (e.g.
  `ci-gate`) once the shared CI workflow reports under one name everywhere.

## Beyond Scorecard

`required_signatures` and the tag rules earn no Scorecard points — there is
no probe for either. They are here because the attestation chain depends on
them: provenance names a tagged commit, and a movable tag breaks every
signature that references it.

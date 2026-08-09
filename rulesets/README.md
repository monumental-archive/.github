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

## Planned (blocked on step-3 workflow design)

- **Restrict `v*` tag creation** to the release workflow's GitHub App as the
  sole bypass actor, so release tags cannot exist except via the pipeline.
  Must land together with the release workflow or releasing locks out.
- **Org-wide required status check** on a standard summary job name (e.g.
  `ci-gate`) once the shared CI workflow reports under one name everywhere.

## Beyond Scorecard

`required_signatures` and the tag rules earn no Scorecard points — there is
no probe for either. They are here because the attestation chain depends on
them: provenance names a tagged commit, and a movable tag breaks every
signature that references it.

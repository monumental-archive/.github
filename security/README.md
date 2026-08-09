# Org security configuration, as code

The canonical org security configuration — the one class of repo settings
GitHub lets an org centrally **enforce**: with `"enforcement": "enforced"`,
repo admins cannot change any feature the configuration sets.

Like `rulesets/`, these files are not applied by anything; they are the
reviewable source of truth. Apply:

```bash
gh api -X POST orgs/monumental-archive/code-security/configurations --input security/org-default.json
```

Then (using the returned `id`) make it the default for new repos and attach
it to everything that already exists:

```bash
gh api -X PUT "orgs/monumental-archive/code-security/configurations/<id>/defaults" -f default_for_new_repos=all
```

```bash
gh api -X POST "orgs/monumental-archive/code-security/configurations/<id>/attach" -f scope=all
```

## Notes

- The config API rejects GHAS-gated fields (`code_scanning_default_setup`,
  validity checks, non-provider patterns) outright on a plan without GHAS
  billing — HTTP 400, not per-repo failure — so those are `not_set` here.
  Public repos can still enable code scanning per-repo for free; `not_set`
  means ungoverned, not disabled. Revisit these three when the plan changes.
- New repos inherit the default automatically; **transferred** repos do not.
  Attaching is a required step of every repo migration.
- Dependabot **version updates** are deliberately absent: they are per-repo
  `dependabot.yml` territory and Renovate owns version PRs org-wide.
  Security configs cover alerts + security updates only.

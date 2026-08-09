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
gh api -X PUT \
  "orgs/monumental-archive/code-security/configurations/<id>/defaults" \
  -f default_for_new_repos=all
```

```bash
gh api -X POST \
  "orgs/monumental-archive/code-security/configurations/<id>/attach" \
  -f scope=all
```

## Notes

- **Applied 2026-08-09 via the settings UI** as configuration id `265775`
  (`monumental-archive-org-config-1`): enforced, attached to all repos,
  default for all new repos. The REST API refuses GHAS-marked fields
  without GHAS billing (HTTP 400) — but the **UI wizard creates the same
  configuration fine**, running those features in their free-for-public
  mode ("private repositories will only have free features enabled").
  On the free plan, apply through the UI; the JSON here is the reviewable
  record of what the UI should be set to.
- All enabled features are free on **public** repos; nothing is purchased
  and nothing can bill — private repos just get the free subset.
- New repos inherit the default automatically; **transferred** repos do not.
  Attaching is a required step of every repo migration.
- Dependabot **version updates** are deliberately absent: they are per-repo
  `dependabot.yml` territory and Renovate owns version PRs org-wide.
  Security configs cover alerts + security updates only.

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

- All enabled features are free on **public** repos. On private repos the
  GHAS-billed ones (secret scanning extras, code scanning) fail to attach on
  the free plan — visible as per-repo `failed` status, not silent.
- New repos inherit the default automatically; **transferred** repos do not.
  Attaching is a required step of every repo migration.
- Dependabot **version updates** are deliberately absent: they are per-repo
  `dependabot.yml` territory and Renovate owns version PRs org-wide.
  Security configs cover alerts + security updates only.

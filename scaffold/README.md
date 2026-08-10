# New-repo scaffold

The canonical stubs a repo needs to join the org. Copy these to the repo
root at creation or migration time — they are the *entire* per-repo
footprint of the governance stack:

- `renovate.json` — extends the org preset
- `lefthook.yml` — pulls the org git hooks by remote
- `committed.toml` — conventional-commit canon (add repo `allowed_scopes`)
- `mise.toml` — repo-specific tools/tasks; the belt arrives globally
- `.rumdl.toml` — markdown canon (MD013 exempts code blocks)

Conditional stubs, wired per the runbook's "Wiring in a repository"
section (`docs/runbook.md`): `cliff.toml` and the release/publish
workflow templates for versioned repos, `CITATION.cff` where citable,
`REUSE.toml` + `README-badges.md` + `SECURITY-INSIGHTS.yml` for the
badge surface, `CODEOWNERS` for reviewer routing.

Plus, from the Actions tab, the **Org CI gate** workflow template (or copy
`workflow-templates/ci.yml` and replace `$default-branch` with `main`).

Then once per clone:

```bash
mise trust && mise install && mise run hooks:install
```

And per migration: attach the org security configuration (transferred
repos do not inherit the default) and run `settings/repo-baseline.sh
apply`. Rulesets need nothing — they are org-level, scope `~ALL`, and
cover a repository the moment it lands.

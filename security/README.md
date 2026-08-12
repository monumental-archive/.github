# Org security configuration, as code

The canonical org security configuration — the one class of repo settings
GitHub lets an org centrally **enforce**: with `"enforcement": "enforced"`,
repo admins cannot change any feature the configuration sets.

These files are not applied by anything; they are the reviewable source
of truth. Apply:

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

## The Actions allowlist

Which third-party code may execute in org CI — org-level, Settings →
Actions → General → *Allow select actions*:

- **GitHub-owned allowed** (`actions/*`, `github/*`).
- **Marketplace-verified NOT allowed.** A verification badge is a
  publisher identity check, not a supply-chain one, and it is not ours to
  revoke — the set it admits changes without our review.
- **Patterns:** `monumental-archive/*` (our own), `jdx/mise-action@*`
  (the belt installer), `codecov/codecov-action@*` (the coverage badge
  feed), `ossf/scorecard-action@*` (the Scorecard publish, which must be
  self-contained).

Read or re-apply it:

```bash
gh api orgs/monumental-archive/actions/permissions/selected-actions
```

Like the rulesets (`docs/rulesets.md`), the org setting is the
enforcement and this is the record — no JSON mirror for six patterns.
Two mechanisms keep it honest, because GitHub enforces the allowlist at
`Set up job`, where a run dies before any step executes and nothing is
red until then:

- `lint:actions-allowed` carries the enforcement copy in the belt, so
  every repo fails a forbidden `uses:` in the gate. It lints composite
  actions (`.github/actions/*/action.yml`) as well as workflows — the
  blind spot that let `sigstore/cosign-installer` reach a live lab run
  (#207).
- `audit:actions-allowlist` reconciles the belt's copy with the live org
  setting every Monday, so widening or narrowing the setting cannot
  quietly disagree with what the gate enforces.

Widening the allowlist is a supply-chain decision: prefer a belt tool
(checksums, attestations, no install scripts) over a new action, exactly
as the toolbelt conventions say.

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

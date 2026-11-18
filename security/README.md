# Org security configuration, as code

The canonical org security configurations — the one class of repo settings
GitHub lets an org centrally **enforce**: with `"enforcement": "enforced"`,
repo admins cannot change any feature the configuration sets.

There are **two shapes**, because visibility changes what the features
cost:

| record | for | Secret Protection + Code Security |
| --- | --- | --- |
| `org-default.json` | public repos | enabled (free on public) |
| `org-private.json` | private repos | disabled (metered on private) |

Nothing applies these files; they are the reviewable source of truth,
and `audit:security-config` holds them to what is live every Monday
(#1213). It derives the configuration `org-default.json` describes —
the org's default for new public repositories, never a recorded id —
and compares both ways: a record field that differs is red, and so is
a live field the record does not state, except the server's own (`id`,
`url`, `html_url`, `created_at`, `updated_at`, `target_type`) and
`name`/`description`. Every public roster member must be attached to
that configuration and `enforced`. Every private member must show the
`org-private.json` values in its `security_and_analysis` and its
Dependabot alerts setting, and must not be attached to it. A read the
token cannot make is CANNOT_JUDGE: the audit App needs Administration:
read on the organisation and on the repositories. Apply:

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

## The visibility rule

**A private repo never takes the `org-default` shape.** Secret Protection
and Code Security are metered products: on a private repository they do
not "fail to attach", they attach and bill, at $19 and $30 per 90-day
active committer per month. Measured 2026-08-21 on the org's first
private member (`monumental-archive`, imported under #672): applying the
default shape enabled both (`code_security: enabled`,
`secret_scanning: enabled`, CodeQL default setup configured 14:26Z), and
the meter read 0 only because it lags the first push. The org's decision
(Carl, 2026-08-21) is **no spend on private members**; the everything-on
shape for private repos was considered and rejected the same day.

The org configuration is *default for new repos: all*, which hands a
newly created — or newly **transferred** — private repository the public
shape. So the import runbook step is: **transfer, then move the repo to
`org-private` BEFORE the first push.** Until an `org-private`
configuration exists live, the same end state is set by hand at repo
level, which is weaker: an unattached repo can drift back to enabled by
one click, where an enforced attachment cannot.

Whether `org-private` is created as a real org configuration is a UI
click that has not been made; this record does not create or attach
anything. When it is, `settings/repo-baseline.sh` should check the
attachment per visibility.

## Attaching over advanced CodeQL setup refuses

The org configuration attach reports **`failed`** on any repo carrying a
CodeQL *advanced-setup* workflow (measured on `iiif-server` and on
`monumental-archive-db` before conformance): default setup refuses to
enable over advanced setup. This is a known condition, not a fault — the
conformance PR's deletion of the repo's `codeql.yml` clears it, and the
attach then succeeds on a re-run.

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

- `lint:actions-allowed` judges with the enforcement copy in the belt
  (`mise/actions-allowed.sh`, the one list the audit below reads too),
  so every repo fails a forbidden `uses:` in the gate. It lints composite
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
  (`monumental-archive-org-config-1`): enforced, and attached and
  `enforced` on all **8 public** repos (measured 2026-08-21). The REST
  API refuses GHAS-marked fields without GHAS billing (HTTP 400) — but
  the **UI wizard creates the same configuration fine**. On the free
  plan, apply through the UI; the JSON here is the reviewable record of
  what the UI should be set to.
- `org-default.json` equals configuration 265775 field for field as
  returned by `gh api
  orgs/monumental-archive/code-security/configurations/265775`, with two
  deliberate exceptions: `name` and `description` are the record's own
  (live, the UI assigned `monumental-archive-org-config-1` and the
  autogenerated description "Created on August 9, 2026"), and the
  server-generated `id`/`url`/`html_url`/`created_at`/`updated_at`/
  `target_type` are not recorded. The live configuration has
  `secret_scanning_generic_secrets` (AI-detected secrets) at `not_set`,
  not enabled; malware alerts are not a field this API returns at all.
- **Private vulnerability reporting is a public-repo feature.** Both
  records set it `enabled`, but on a private member it is inert:
  the repo's `private-vulnerability-reporting` REST endpoint returns 404
  (measured 2026-08-21).
- New repos inherit the default automatically; **transferred** repos do not.
  Attaching is a required step of every repo migration — see the
  visibility rule above for which shape.
- Dependabot **version updates** are deliberately absent: they are per-repo
  `dependabot.yml` territory and Renovate owns version PRs org-wide.
- Dependabot **security updates** are off; Dependabot **alerts** are on
  (#1146). Renovate's advisory path is the remedy: it reads the alerts
  and skips the release age and the schedule (`default.json`,
  `vulnerabilityAlerts`). With security updates on, Dependabot opened
  duplicates of Renovate's own pull requests, outside the org's commit
  template, red where Renovate's twin was green (seen 2026-11-13 in
  monumental-archive, stele and iiif-server). One updater, one
  template. The accepted cost: Renovate cannot remediate an advisory in
  a transitive cargo dependency (#667), and Dependabot sometimes can.
  For that set `audit:blast-radius` remains the detector and the weekly
  lock file maintenance the delivery.

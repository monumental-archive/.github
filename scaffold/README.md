# New-repo scaffold

The canonical stubs a repo needs to join the org. Copy these to the repo
root at creation or migration time — they are the *entire* per-repo
footprint of the governance stack:

- `renovate.json` — extends the org preset
- `lefthook.yml` — pulls the org git hooks by remote
- `committed.toml` — conventional-commit canon (add repo `allowed_scopes`)
- `mise.toml` — repo-specific tools/tasks; the belt arrives globally
- `.rumdl.toml` — markdown canon (MD013 exempts code blocks)

**Pick the licence — this is a step, not an afterthought (#214).** The
choice is per-repo (Rust repos conventionally dual-license
`MIT OR Apache-2.0`; the canon itself is 0BSD): land `LICENSE` at the
root, the matching text in `LICENSES/<SPDX-ID>.txt`, and fill
`REUSE.toml`'s `SPDX-License-Identifier`. With no licence the default
is all rights reserved — the opposite of publishing anything — and
`lint:licence` in the belt reddens the gate until the choice is made.

- `.bestpractices.json` — OpenSSF Best Practices pre-fill (#347). The
  badge app reads this file from the repo root and proposes answers, so
  registering a new repo means reviewing the handful that differ
  instead of answering ~190 questions by hand. Derived from the canon's
  earned entry ([project 14058](https://www.bestpractices.dev/projects/14058)),
  trimmed to answers that are true **org-wide because the controls are
  inherited** (rulesets, DCO, the gate, signed releases, SBOMs); every
  repo-varying criterion — everything about builds, tests, coverage,
  docs, crypto, hardening — is `?`, which the app ignores, so a stale
  placeholder proposes nothing rather than something false. Two traps
  the derivation already avoids, recorded so edits keep avoiding them:
  `build_*` answers must NOT be copied from the canon (N/A there, Met
  in any repo the repro gate builds), and a consuming repo will
  legitimately **outscore** the conformance root — that is the design,
  not a regression.

Conditional stubs, wired per the runbook's "Wiring in a repository"
section (`docs/runbook.md`): `cliff.toml` and the release/publish
workflow templates for versioned repos, `CITATION.cff` where citable —
rendered by `fix:citation` from `REUSE.toml`, and required by
`lint:citation` wherever the stub passes `mint-doi: true` (#316) —
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

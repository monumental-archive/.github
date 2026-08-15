# New-repo scaffold

The canonical stubs a repo needs to join the org. Copy these to the repo
root at creation or migration time — they are the *entire* per-repo
footprint of the governance stack:

- `renovate.json` — extends the org preset
- `lefthook.yml` — pulls the org git hooks by remote
- `committed.toml` — conventional-commit canon (add repo `allowed_scopes`)
- `mise.toml` — repo-specific tools/tasks; the belt arrives globally
- `.rumdl.toml` — markdown canon (MD013 exempts code blocks)
- `ruff.toml` — the Python canon at `select = ["ALL"]` plus preview.
  Copy it verbatim. `target-version` must not name a newer Python than
  the belt's `python` pin — the pyupgrade rules rewrite code into
  whatever it names, and ruff cannot know what will run the result;
  `lint:python-target` fails the gate on that drift. Targeting lower is
  legal, for a repo supporting older interpreters than it develops on.
  `lint:python` fails the
  gate if Python is tracked without a config — ruff's *default* selection
  is a few dozen rules out of ~900, so a missing config yields a green
  gate that checked almost nothing. Unlike `biome.json`, a nested copy is
  harmless: ruff's config discovery is hierarchical by design.
- `biome.json.stub` — the JS/TS/JSON canon at `preset: "all"`, copied to
  the root as `biome.json`. The `.stub` suffix is load-bearing, exactly
  as it is for `REUSE.toml.stub`: biome discovers configs by walking the
  tree regardless of the paths it is given, and a second file named
  `biome.json` anywhere below the root is a *nested root configuration*
  that hard-fails every run. Copy it verbatim; the rule set is org
  policy, not a repo choice, and
  `lint:biome` fails the gate if biome-parseable files are tracked
  without it (biome resolves its config from the repo, so a missing file
  silently downgrades the repo to biome's own defaults). **A repo with a
  framework adds its `domains` block** — `react`, `next`, `vue`, `test`,
  `project`, `types` and the rest, each at `"all"`. Domains are the one
  repo-shaped part: react and solid ship deliberately conflicting rules,
  so they cannot all be enabled at once, and naming the ones a repo
  actually uses is a statement of fact about the repo rather than a
  choice about how strict to be.
- `.golangci.yml` — the Go canon (#392): golangci-lint v2 at
  `default: all` with per-rule written disables, gofumpt (extra rules)
  and gci as formatters, and the depguard ban on `encoding/json`
  outside `internal/jsonx`. Copy it and fill the two `<module-path>`
  holes. `lint:go` fails the gate if Go is tracked without it —
  golangci-lint's default selection is five linters of ~105, so a
  missing config yields a green gate that checked almost nothing (the
  ruff trap, again). govulncheck rides repo-side: pin
  `"go:golang.org/x/vuln/cmd/govulncheck"` in `mise.toml` (the
  cargo-fuzz pattern; `audit:go-vulns` asserts it with the remedy).

**Pick the licence — this is a step, not an afterthought (#214).** The
choice is per-repo (Rust repos conventionally dual-license
`MIT OR Apache-2.0`; the canon itself is 0BSD): land `LICENSE` at the
root, the matching text in `LICENSES/<SPDX-ID>.txt`, and fill
`REUSE.toml`'s `SPDX-License-Identifier`. With no licence the default
is all rights reserved — the opposite of publishing anything — and
`lint:licence` in the belt reddens the gate until the choice is made.

- `.bestpractices.json` — OpenSSF Best Practices pre-fill (#347). The
  badge app reads this file from the repo root and proposes answers, so
  registering a new repo starts from ~50 answers already made rather
  than a blank form — the *majority* still differ per repo and are left
  as `?`, so treat this as a head start, not a shortcut. Derived from
  the canon's
  earned entry ([project 14058](https://www.bestpractices.dev/projects/14058)),
  trimmed to answers that are true **org-wide because the controls are
  inherited**: the org rulesets and security configuration, the DCO, the
  shared gate, and the community health files this repository serves as
  org defaults (SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, GOVERNANCE,
  SUPPORT). Anything a repo could vary is `?`, which the app ignores, so
  a stale placeholder proposes nothing rather than something false.

  **Signed releases, SBOMs and attestations are deliberately `?`, not
  Met.** They are true of the canon and false of a repo on its first
  day, and a stub that asserts them would have every new repo claim a
  release it has never cut. Same for the whole `version_*` and
  `release_notes*` family. Inherited *controls* may be carried; earned
  *outcomes* may not.

  Three traps the derivation avoids, recorded so edits keep avoiding
  them:

  - **Baseline criteria must use the `osps_ac_01_01` key form**, never
    the `OSPS-AC-01.01` form the project download renders. The input
    whitelist is built from the criteria YAML keys, and a miss is
    skipped in silence — an unrenamed file drops all 64 Baseline
    criteria while the metal series fills normally. See
    [`docs/best-practices.md`](../docs/best-practices.md) for the
    source references and the one-line check.
  - `build_*` answers must NOT be copied from the canon (N/A there, Met
    in any repo the repro gate builds), and each `build_*` criterion
    hangs on its *own* escape clause — never write "no build system
    exists".
  - A consuming repo will legitimately **outscore** the conformance
    root — that is the design, not a regression.

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

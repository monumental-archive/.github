# Repo migration playbook

The ordered runbook for moving a repository into the organisation's
governance. Authored with the full standup context; executed per-repo in
a fresh session **started in the target repo's checkout**.

## Session preamble (read to the session, verbatim)

> The `monumental-archive/.github` repository's documentation is the only
> authority: its CLAUDE.md, this playbook, `scaffold/README.md`, and the
> toolbelt. This repository's existing CLAUDE.md, configs, CI, and any
> prior session's decisions are **inputs to be judged, not followed**.
> Read every relevant doc end to end before acting on it. Nothing is
> migrated by copy; everything is re-derived against the canon.

## Prerequisites (once, before any migration)

- [ ] Release pass complete (`.github#28`): this repo has tags, the
      lefthook remote and workflow template pin versions, the `v*` tag
      ruleset and release App exist.
- [ ] **Identify the repo's version source** against the phase-1 contract
      (docs/release.md, "The version source"): Cargo workspace, tags-only,
      or an unbuilt branch (package.json, single-crate Cargo, …). An
      unbuilt branch is a named prerequisite to stand up in `release/`
      first — not a release-day surprise; phase 1 fails only at release
      time, the one time it runs.
- [ ] **Org-wide tooling survey** (its own session): inventory every
      tool, config, and CI job across all candidate repos. Verdict each:
      **belt** (docs-first standup here, org-wide), **repo-specific**
      (stays, e.g. atlas), or **retire** (duplicates a belt tool —
      e.g. codespell vs typos, markdownlint vs rumdl, taskfile vs mise).
      Migrations execute against a settled belt.

## Phase 1 — transfer and settings (owner, ~minutes)

- [ ] Transfer the repo into `monumental-archive`.
- [ ] Attach the org security configuration (transfers do **not**
      inherit the new-repo default): Settings → Advanced Security, or
      the API attach call in `security/README.md`.
- [ ] `./settings/repo-baseline.sh apply` (from the `.github` checkout —
      settings, immutable OIDC sub claim, and the `publish` environment
      where the canonical entry `publish.yml` exists).
- [ ] Rulesets need nothing: they are org-level, `enforcement: active`,
      scope `~ALL` — a transferred repo is covered the moment it lands.
- [ ] Verify: `./settings/repo-baseline.sh check` exits clean; ruleset
      shows `current_user_can_bypass: never`.

## Phase 2 — scaffold and toolchain (session, in-repo)

- [ ] Copy the `scaffold/` stubs per `scaffold/README.md` (configs
      always; CITATION/REUSE/badge-block/SECURITY-INSIGHTS where the
      runbook's wiring section says so — CITATION.cff is rendered by
      `fix:citation` from REUSE.toml, never copied filled); fill the
      repo-specific holes
      (`allowed_scopes` in committed.toml, real tools/tasks in
      mise.toml).
- [ ] **Pick and land the licence** (#214): `LICENSE` at the root,
      `LICENSES/<SPDX-ID>.txt`, the expression in `REUSE.toml`. The
      choice is per-repo (Rust convention `MIT OR Apache-2.0`; the
      canon is 0BSD). `lint:licence` reddens the gate until this is
      done, so a repo cannot be brought into conformance without it.
- [ ] Convert the existing task runner to mise tasks: every Taskfile (or
      Makefile/script) target becomes a task or is deliberately dropped,
      with the mapping recorded in the migration PR body. Repo-specific
      lint tools enter as `lint:*` tasks (auto-collected into `ci`);
      write-mode ones as `fix:*`; network-bound ones as `audit:*`.
- [ ] Delete tooling the survey retired. A repo carries no config for
      any belt tool unless it genuinely diverges — divergence is a
      documented exception, not a preference.
- [ ] `mise trust && mise install && mise run hooks:install`.

## Phase 3 — make the gate green (session, in-repo)

- [ ] `mise run ci` — fix everything it finds. Belt linters run at max;
      the repo conforms to the tools, never the reverse. Repo-specific
      exceptions (`_typos.toml` jargon, actionlint config-variables) are
      added only where a finding is genuinely wrong, each with a comment.
- [ ] SHA-pin every `uses:` in existing workflows (the org requires
      full-SHA pins; unpinned workflows fail at startup).
- [ ] Replace the repo's CI workflow with the six-line caller stub from
      `workflow-templates/ci.yml`. Runner-level needs (matrix, cache
      actions, service containers) are the **only** justification for
      extra workflow YAML — if claimed, verify the need is real, then
      design it so jobs still only run `mise run <task>`.

## Phase 4 — verify and close (session + owner)

- [ ] PR through the gate; confirm the cloud `ci / ci` check passes and
      is required.
- [ ] Rewrite the repo's CLAUDE.md/README to describe the post-migration
      world (greenfield voice; no stale runner or tooling references).
- [ ] Confirm Renovate opened or adopted the repo (dashboard issue
      appears; `renovate.json` stub recognised).
- [ ] Old artefacts: archive superseded repos (e.g. the personal-account
      original, `renovate-config` once its last consumer moves).
- [ ] Run `mise run audit:links` and the drift check once more, org-wide.

## Known landmines (learned the hard way, 2026-08-09)

- Ubuntu's `sh` is dash: tasks are bash — already pinned via
  `[task_config]`, do not write POSIX-only guards to compensate.
- `pull_request` CI sits on a synthetic merge commit; `lint:commits`
  already handles it. Do not "fix" ranges per-repo.
- Tool walkers find lefthook's remote cache inside `.git/`; belt tasks
  use `git ls-files`. Repo-added lint tasks must do the same.
- Fine-grained PATs are REST-only, and Administration needs read+write
  to see merge-settings fields.
- Transferred repos inherit **nothing** automatically: not the security
  configuration, not baseline settings (org rulesets are the exception —
  scope `~ALL` covers arrivals). Phase 1 is not optional.
- **Transfer flips the OIDC subject claim** (post-2026-07-15 rule) to the
  id-embedding `repo:owner@id/name@id:` format. Everything keyed on
  identity changes at that moment: registry trusted-publisher matches,
  `ghcr.io/<owner>/*` paths, attestation identities — and attestations do
  not survive the transfer. Preview the future claim via GitHub's
  endpoint and confirm registry configs against it BEFORE transferring;
  re-verify a publish in the lab pattern after. **No production repo cuts
  a real release before its transfer** (canon, release.md). The lab's own
  rename is the rehearsal for this flip and goes first.

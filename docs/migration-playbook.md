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

- [ ] Do NOT copy a config the belt delivers. Anything only a belt tool
      reads — clippy, rustfmt, pinact, typos — is passed to the tool from
      the canon at run time via `ORG_BELT_DIR`, so a repo carries no copy
      to drift and gets the current one at its pinned SHA (#445). The
      stubs below are the files something OUTSIDE the belt reads: editors
      (`.editorconfig`), GitHub (`renovate.json`), the release scripts, or
      the repo's own identity and policy (licences, `REUSE.toml`,
      `deny.toml`).
- [ ] **The deletion rides the pin bump** (#620). A delivered config may
      only replace a repo-local copy once the consumer's pinned reference
      names a release whose belt actually reads the delivered one — the
      deletion lands IN that pin-bump commit, never before it. This is the
      pin rule the mechanism template already states for policy edits,
      applied to the surface it demonstrably also governs: delete first
      and the repo answers to neither config, because the tool falls back
      to its own defaults and nothing says so.

      Worked example, measured 2026-08-20: #608 deleted the canon's
      repo-local `committed.toml` and rerouted the `commit-msg` hook to
      `mise run commits:check`, but the reroute reaches a repo only
      through `lefthook.yml`'s remote `ref:`, and the pinned ref predated
      it — so the cached hook ran bare `committed`, found no config, and
      enforced committed's built-in defaults: `Banana(belt): add a thing`
      and `Fixes the belt tasks` PASSED while `fix(belt): …` was
      REJECTED. Scope enforcement was NOT lost (`Fix(nosuchscope):`
      passes either way, measured) — the loss is the type, format and
      wrap-aware 72-column rules. `pre-push` and CI bound it: nothing
      non-conformant reached `main`, but feedback was wrong exactly where
      it is cheapest.

      **Renovate bumps `lefthook.yml`'s ref one release LATER than the
      rest of the first-party group** — consistent across six historical
      bumps — so this window is longer than one release cycle, not one
      bot poll. While in it: verify with `mise run commits:check` and
      commit with `LEFTHOOK_EXCLUDE=committed`.
- [ ] Copy the `scaffold/` stubs per `scaffold/README.md` (configs
      always; CITATION/REUSE/badge-block/SECURITY-INSIGHTS where the
      runbook's wiring section says so — CITATION.cff is rendered by
      `fix:citation` from REUSE.toml, never copied filled); fill the
      repo-specific holes (real tools and tasks in `mise.toml`, plus
      `ORG_COMMIT_SCOPES` there if the repo restricts commit scopes).
- [ ] **Scorecard preflight** (the policy #83 executes against,
      formerly #88's ledger): run
      `scorecard --repo=github.com/<owner>/<repo>` locally before AND
      after transfer — no repo transfers below the documented ceiling
      (structural caps recorded there: Packaging −1, Pinned-Deps
      dented by the `$/` self-reference, `Maintained` time-capped for
      90 days) — and record the measured score as the repo's row in
      `security/scorecard-floors.txt`. Not optional diligence: the
      first `fix:badges` render adds the Scorecard shield, and the
      Monday audit fails an unfloored shield, so a skipped preflight
      is a red cron, not a quiet gap.
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
- [ ] **If the repo fuzzes** (a tracked `fuzz/`, as edtf and iiif-server
      already have), declare the dated nightly beside the stable pin and
      name it — `rust = ["1.xx.x", "nightly-YYYY-MM-DD"]` plus
      `FUZZ_TOOLCHAIN` in `[env]` (#445). AddressSanitizer is
      cargo-fuzz's default and needs `-Zsanitizer`, so `audit:fuzz`
      refuses to run without it rather than quietly fuzzing unsanitized.
      The gate is unaffected: `lint:fuzz-build` compiles the targets on
      stable. What to fuzz is the repo's call and nobody else's — the
      belt enforces how fuzzing runs, never which functions deserve a
      target, the same division as `coverage:check` and its floor.
- [ ] **If the repo is Go, there is nothing to declare** (#445). The
      fuzzing engine ships in the toolchain, so no nightly, no sanitizer
      flag and no extra pin: `go.mod` is the whole setup. Any
      `func FuzzXxx(*testing.F)` is found by the belt on its own —
      `lint:go-fuzz-seeds` replays its seed corpus in the gate under the
      race detector, and `audit:go-fuzz` fuzzes it on the Monday cron,
      twice per target (plain, then race). `lint:go-tidy` needs nothing
      declared either. Same division as above: the belt enforces how
      fuzzing runs, the repo decides what deserves a target.
- [ ] `mise trust && mise install && mise run hooks:install`.

## Phase 3 — make the gate green (session, in-repo)

- [ ] `mise run ci` — fix everything it finds. Belt linters run at max;
      the repo conforms to the tools, never the reverse. Repo-specific
      exceptions are added only where a finding is genuinely wrong, each
      with a comment. A repo's own `_typos.toml` still works for domain
      jargon — typos merges it with the org vocabulary the belt passes.
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

- Ubuntu's `sh` is dash, and `[task_config]` does NOT layer — it governs
  the file it is written in and reaches no repo's own `mise.toml` (#700).
  The belt closes this from its side by setting the strict shell as mise's
  default (`[settings] unix_default_inline_shell_args`), so tasks are bash
  and POSIX-only guards are still the wrong compensation. A repo that
  defines tasks restates the pin anyway — `lint:belt-shell` reds it
  otherwise — because the belt's default is a layer that a bare clone does
  not have:

      [task_config]
      shell = "bash -euo pipefail -c"

  What went wrong before that setting existed is worth knowing, because
  the loud half is not the dangerous half: a body carrying its own
  `set -euo pipefail` died on the runner with `set: Illegal option -o
  pipefail`, but a body without one ran under dash with errexit and no
  pipefail — so a failing tool inside a pipeline passed the check.
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

## The belt does not name the org (#579)

The universal belt expresses org identity in data, never in its own
text — swept and measured at #579. The criterion, decided there: does
a task's correctness depend on the literal, or merely on its scope?

- **Scope** comes from the environment: every org walk reads
  `${GITHUB_REPOSITORY_OWNER}` — one spelling, set by Actions, exported
  by hand for a local audit run. An unset owner is a named refusal,
  never a quieter walk.
- **Literals under test** — the canon's own coordinates, the trusted
  signer workflow ref, the shield endpoints — live in one canon-owned
  declaration, `security/identity.toml`, read through the one reader
  `mise/identity.sh` via `ORG_CANON_DIR`. An adopter states their
  facts there instead of grepping the tasks; `lint:canon-policy`
  refuses a belt-carrying tree that lacks the declaration or the
  policy documents.

The claim is measured, not asserted:
`grep -c monumental-archive mise/config.toml` is **6**, and every
remaining line is prose citing this org's history or the mise tool pin
for stele, whose TOML `[tools]` key is a source coordinate no
declaration can reach (inline by decision, reason at the pin).

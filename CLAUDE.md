# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Purpose

This repo is the org's **conformance root** — see `docs/direction.md`
for the thesis and the SLSA targets (v1.2: Build L3 **met**; Source L3
**met** since 2026-08-12, the org emitting its own VSAs; Dep L2 met by
construction; BuildEnv **formally L0** with L1-verification controls,
gapped on attestations that are not ours to emit — the ceiling is by
choice, the L0 is not).
Never restate a target from memory: `direction.md` is the one table, and
it is the first thing a closing issue updates. Other repos pin this one
and conform to it; it does not adapt to them. The centralisation is the
design, decided in the standup PRs (#5–#26) — question changes against
the targets, never the architecture against priors.

## What this is

`monumental-archive/.github` — the org's governance repository. Everything
shared lives here and nowhere else. Five layers:

1. **GitHub-magic content** that only works from a repo with this name:
   community health files (SECURITY, CONTRIBUTING, CODE_OF_CONDUCT,
   SUPPORT, issue forms, PR template), `profile/README.md`, and
   `workflow-templates/`.
2. **The toolbelt** (`mise/config.toml` + `mise.lock`): the universal tool
   layer every repo and every machine consumes — exact pins, per-platform
   checksums, GitHub attestations. Consumed locally via a
   `~/.config/mise/conf.d` symlink and in CI via `MISE_GLOBAL_CONFIG_FILE`.
   The filename is load-bearing: both mise and Renovate's mise manager
   recognise `mise/config.toml` natively — do not rename.
3. **The task contract**: the global `ci` task wildcard-collects `lint:*`
   and optionally runs `test`/`build`. Repos never define `ci`; adding a
   `lint:<tool>` task here enforces that tool in every repo with no repo
   change. `fix:*` are write-mode siblings, never in the gate. `audit:*`
   are network-bound or noisy checks, structurally outside the gate.
4. **Shared workflows**: `ci.yml` (the reusable gate — callers pin one
   SHA, and `$/.github/actions/canon` delivers the toolbelt at that same
   resolution, #165),
   `gate.yml`
   (this repo self-applies it), `audit.yml` (Monday cron: link check +
   repo-settings drift), `self-release.yml` (this repo versions itself
   with its own release machinery via local `uses:` — canon tags are what
   every consumer pin, lefthook ref and preset reference resolve to, and
   what Renovate fans out; see #133, MAINTENANCE.md).
5. **Settings as code**: `docs/rulesets.md` (the org-level branch + tag
   rules — enforced and edited in the GitHub UI on the Team plan,
   recorded and explained in that doc; deliberately no JSON mirror),
   `security/` (the enforced org security configuration), `settings/`
   (repo baseline + check/apply
   script), `scaffold/` (the four stubs a new repo copies), and
   `default.json` (the org Renovate preset; this repo's own config is
   `renovate.json`).

## Rules that must not be broken

- **No workflow that runs caller-supplied code may declare `id-token:
  write`** — concretely, no `workflow_call` workflow, here or anywhere in
  the org, without an explicit `capability-boundary:` marker stating why it
  is safe. That split is the SLSA Build L3 boundary: a certificate minted
  in a job that also executes caller-supplied code would bear this repo's
  identity, and nothing would go red. `lint:capability-boundary` enforces
  it. The rule was previously an absolute ban on `id-token` in this repo;
  it was narrowed because the ban also blocked standalone scheduled
  workflows that run no caller code (Scorecard publishing, Rekor identity
  monitoring) — and declining to monitor the log for forged signer
  identities in order to protect the signer was self-defeating.
- **This repo must stay public.** Private `.github` repos serve no default
  community health files. Everything here is world-readable; write
  accordingly.
- **The gate is deterministic.** Nothing network-dependent (vulnerability
  feeds, link liveness, schema catalogs, zizmor online audits) belongs in
  `ci` — those are `audit:*` tasks or the scheduled workflow.

## Conventions

- Every `uses:` pinned to a full commit SHA with a trailing version
  comment. Reusable-workflow callers pin this repo by SHA too.
- Belt linters lint **tracked files only** (`git ls-files`), never a
  tool's own directory walker — lefthook caches remote configs inside
  `.git/`, and walkers find them.
- Belt linters guard for applicability and skip clean: a linter that
  cannot skip cannot be universal.
- Tasks are written in bash; `[task_config] shell` pins it (Ubuntu's `sh`
  is dash).
- Commits: conventional, **imperative**, lowercase subjects, 72-column
  ceiling — enforced by `committed` at commit-msg, pre-push, and in CI.
  PRs are squash-merged; the PR title and body become the permanent
  commit.
- Spelling registers: en-US in code and identifiers, en-GB in prose
  (typos runs locale `en`, which accepts both).
- New tools enter the belt only after a docs-first standup, at maximum
  defensible enforcement, verified against this repo before any other.
  Prefer aqua-backed tools (checksums, attestations, no install scripts).

## The path wart

Reusable workflows must live in `.github/workflows/`, so from here they
are referenced as
`monumental-archive/.github/.github/workflows/ci.yml@<sha>`. The doubled
`.github` is correct and unavoidable.

## Testing

`mise run ci` locally is exactly what CI runs — same tools, same
versions, same order, from the same lockfile. Shared-workflow changes are
exercised by this repo's own `gate.yml` on every PR; the release half is
exercised from `release-lab` before any production repo moves its
pin.

## Open items

The release pass (#28) is **complete and closed**: five artifact classes,
both archetypes, App-minted tags, evidence bundles, repro-check, DOI
minting — proven in the lab at full width and stranger-verified. The
final conformance pass (2026-08-12, canon v1.14.x) closed the last
level-bearing issues: the verifier signs its own verdicts (#264, two
roots of trust), the source chain self-heals with computed levels
(#265), the audit enumerates the population (#266), enrichment (#200),
licences + `lint:licence` (#214), and the per-track mapping docs
(#213) — the org is **done at its declared levels**, and importing
repos is mechanical conformance. Remaining, tracked as issues:
transfers (#83 — pure execution, lab rename first), badge stand-up
(#88 — checklist only), sponsorship (#27), and the watch issues
(`docs/tooling-verdicts.md` names them). Deferred decisions and their
reasons live in the standup PRs (#5–#26); the traps live in #28.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

`monumental-archive/.github` — the org's shared, **untrusted** infrastructure
repo. Two jobs:

1. **GitHub-magic content** that only works from a repo with this exact name:
   community health files, issue/PR templates, `profile/README.md`, and
   `workflow-templates/`.
2. **Shared reusable workflows** that run caller code: the CI lint gate, the
   Rust gate, and the release half (asset attachment, publishing drafts).

## The rule that must not be broken

**No workflow in this repository may ever declare `id-token: write`.**

Signing lives in
[monumental-archive/trusted-builder](https://github.com/monumental-archive/trusted-builder),
which holds `id-token` and runs *no* caller code. This repository is the
mirror image: it runs caller code and holds no signing identity.

That split is the SLSA v1.0 Build L3 boundary. A certificate minted here
would bear this repository's identity while executing code supplied by the
calling repo — exactly the property the boundary exists to remove. It is
also what lets consumers pin `--signer-repo monumental-archive/trusted-builder`
safely, because that repo contains nothing but signing workflows.

Adding `id-token: write` to anything here silently drops every consumer from
Build L3 to Build L2, and nothing goes red.

## This repository must stay public

GitHub does not support default community health files from a private
`.github` repo — "Private `.github` repositories are not supported." A public
`.github` serves **all** repos in the org, including private ones, so this is
not a limitation in practice. But everything here is world-readable; do not
put anything in it that shouldn't be.

(`.github-private` is unrelated — it exists solely for a member-only org
profile README.)

## The path wart

Reusable workflows must live in `.github/workflows/`, so from here they are
referenced as:

```yaml
uses: monumental-archive/.github/.github/workflows/lint.yml@<commit-sha>
```

The doubled `.github` is correct and unavoidable. It looks like a typo
forever.

## Conventions

- Every `uses:` pinned to a full commit SHA with a trailing `# vX.Y.Z`
  comment. Callers pin *this* repo by SHA too — `uses:` accepts no contexts
  or expressions.
- Caller-supplied values routed through `env:` with an `UNTRUSTED_` prefix,
  never expanded into `run:` code (zizmor template-injection).
- **Grant exactly the scopes a called workflow declares.** A called workflow
  may only downgrade the caller's grant; requesting a scope the caller
  withheld kills the run as `startup_failure` — no jobs, no annotations, no
  log. `actionlint` cannot catch it, because the contract spans two repos.
- Spelling registers match the edtf canon: **en-US in code and identifiers**,
  **en-GB in prose**.
- Commits are SSH-signed; conventional commits.

## Testing

Changes are exercised from
[monumental-archive/edtf-release-lab](https://github.com/monumental-archive/edtf-release-lab)
— dummy crates, real GitHub APIs, no registry publishes — before any
production repo moves its pin. The release half of a pipeline is covered by
nothing except live releases, which is the most expensive place to find a
defect.

## Related

- `trusted-builder` — signing only, the other half of the split
- `renovate-config` — stays a separate repo. Renovate's own docs recommend
  shared presets (explicit `extends`) over inherited config, and the
  inherited-config convention is a repo named `renovate-config` anyway.

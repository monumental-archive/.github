# .github

Shared infrastructure for the Monumental Archive organization.

## What lives here

| | |
| --- | --- |
| **Community health files** | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, `GOVERNANCE.md`, `FUNDING.yml`, issue and PR templates — inherited by every repo in the org that does not carry its own |
| **Org profile** | `profile/README.md` |
| **Workflow templates** | `workflow-templates/` — the starters offered in the Actions UI |
| **Shared reusable workflows** | the CI lint gate, the Rust gate, and the release half |
| **Renovate preset** | the shared policy every repo `extends` |
| **lefthook universal config** | consumed live over git by every repo's `lefthook.yml` |

## What does not live here

**Signing.** That is
[trusted-builder](https://github.com/monumental-archive/trusted-builder),
which holds `id-token: write` and runs no caller code.

This repository is the opposite: it runs caller code and **never** holds a
signing identity. That separation is the SLSA v1.0 Build L3 boundary, and
keeping it structural — one repo per side — is what lets consumers pin
`--signer-repo` safely instead of having to name an individual workflow file.

**Licences.** GitHub cannot default a `LICENSE`; every repo carries its own.

## Notes for consumers

Reusable workflows here are referenced with a doubled path, because workflows
must live under `.github/workflows/` and this repository is itself called
`.github`:

```yaml
uses: monumental-archive/.github/.github/workflows/lint.yml@<commit-sha>
```

Pin by commit SHA — `uses:` accepts no contexts or expressions.

A repo's own file always wins over the default. Note that issue and PR
template *folders* are all-or-nothing: if a repo has anything in its own
`.github/ISSUE_TEMPLATE`, none of the defaults apply to it.

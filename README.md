# .github

<!-- badges:begin -->
[![ci](https://github.com/monumental-archive/.github/actions/workflows/gate.yml/badge.svg)](https://github.com/monumental-archive/.github/actions/workflows/gate.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/monumental-archive/.github/badge)](https://scorecard.dev/viewer/?uri=github.com/monumental-archive/.github)
[![SLSA Build L3](https://img.shields.io/badge/SLSA-Build%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/runbook.md#verifying-as-a-consumer-would)
[![SLSA Source L3](https://img.shields.io/badge/SLSA-Source%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/source-track.md)
[![SLSA Dependencies L2](https://img.shields.io/badge/SLSA-Dependencies%20L2-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/dependency-track.md)
<!-- pending (human step): OpenSSF Best Practices — answer the form from docs/best-practices.md, then set 'bestpractices <BP_ID>' in .badge-states and re-run fix:badges -->
[![REUSE status](https://api.reuse.software/badge/github.com/monumental-archive/.github)](https://api.reuse.software/info/github.com/monumental-archive/.github)
[![coverage](https://codecov.io/gh/monumental-archive/.github/branch/main/graph/badge.svg)](https://codecov.io/gh/monumental-archive/.github)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21913604.svg)](https://doi.org/10.5281/zenodo.21913604)
[![fair-software](https://img.shields.io/badge/fair--software.eu-%E2%97%8F%20%E2%97%8F%20%E2%97%8B%20%E2%97%8F%20%E2%97%8B-orange)](https://fair-software.eu)
<!-- badges:end -->

The governance repository of the Monumental Archive organisation:
everything shared lives here and nowhere else. Repos carry only what is
genuinely theirs; this repo supplies the rest.

Why it is shaped this way and what it is aiming at — including the
SLSA v1.2 targets — is stated in [docs/direction.md](docs/direction.md).

## What lives here

| | |
| --- | --- |
| **Toolbelt** | `mise/config.toml` + `mise.lock` — the universal tools, exact-pinned with per-platform checksums, consumed identically by laptops (conf.d symlink) and CI (`MISE_GLOBAL_CONFIG_FILE`) |
| **Task contract** | the global `ci` task: wildcard-collects `lint:*`, optionally runs `test`/`build`; repos never define `ci` |
| **Shared workflows** | `ci.yml` (the reusable gate, pinned by one SHA that also pins the belt), `audit.yml` (scheduled link + drift checks) |
| **Settings as code** | `security/`, `settings/` — the enforced security configuration and the repo-settings baseline with drift check; the org-level branch/tag rules are UI-applied and recorded in `docs/rulesets.md` |
| **Renovate preset** | `default.json` — every repo `extends` it |
| **Git hooks** | `lefthook/org-hooks.yml`, consumed live over git by every repo's stub |
| **Scaffold** | `scaffold/` — the four files a new repo copies to be fully governed |
| **Community health files** | SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, SUPPORT, issue forms, PR template — inherited by every repo without its own |
| **Org profile & templates** | `profile/README.md`, `workflow-templates/` |

## What does not live here

**Signing.** That is
[signer](https://github.com/monumental-archive/signer),
which holds `id-token: write` and runs no caller code. This repository is
the opposite: it runs caller code and **never** holds a signing identity.
That separation is the SLSA v1.2 Build L3 boundary, and keeping it
structural — one repo per side — is what lets consumers pin
`--signer-repo` safely.

**Licences.** GitHub cannot default a `LICENSE`; every repo carries its
own.

## Notes for consumers

A repo joins the org's governance by copying `scaffold/` (four files) and
adopting the CI workflow template — about six lines:

```yaml
jobs:
  ci:
    uses: monumental-archive/.github/.github/workflows/ci.yml@<commit-sha>
```

The doubled `.github` in the path is correct: workflows must live under
`.github/workflows/` and this repository is itself called `.github`. Pin
by full commit SHA — `uses:` accepts no contexts or expressions.

A repo's own file always wins over any default here. Issue and PR
template *folders* are all-or-nothing: if a repo has anything in its own
`.github/ISSUE_TEMPLATE`, none of the defaults apply to it.

`mise run ci` locally is exactly what CI runs. If it is green on your
machine, it is green in the cloud.

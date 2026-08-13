# Best Practices self-attestation crib

The [bestpractices.dev](https://www.bestpractices.dev) form, answered once
here so each repository's submission (post-transfer — the form binds the
repo URL) is twenty minutes of clicking, not an afternoon of archaeology.
Answers below are the org-wide evidence; anything repo-specific is marked.

## Passing

| Criterion family | Answer | Evidence |
| --- | --- | --- |
| Basics: homepage, description, contribution | Met | README + CONTRIBUTING.md (org-wide health files) |
| FLOSS licence, in LICENSE | Met | per-repo LICENSE (0BSD in the org's own repos), `LICENSES/` + REUSE.toml, enforced by `lint:licence` (#214) |
| Change control: public VCS, unique versions, release notes | Met | GitHub; semver by git-cliff; CHANGELOG per release |
| Reporting: issue process, vulnerability process, ack ≤ 14 days | Met | issue forms; SECURITY.md (private reporting, 14-day ack) |
| Quality: build, automated test suite, new-functionality tests, warnings | Met | `mise run ci` = the cloud gate; clippy/tests enforced; warnings deny |
| Security: secure design knowledge, no unencrypted auth, vuln fix ≤ 60 days | Met | trusted publishing only, no tokens; Dependabot + Renovate |
| Static analysis | Met | CodeQL default setup (org-enforced) + belt linters |
| Dynamic analysis (suggested) | Met where fuzzing exists | cargo-fuzz via the belt's `lint:fuzz-build` + `audit:fuzz` pair, proven in release-lab (#316; edtf adopts at transfer); otherwise answer "not applicable" honestly |

## Silver — the MUSTs that need real answers

| Criterion | Answer | Evidence |
| --- | --- | --- |
| `access_continuity` | Met | [continuity.md](continuity.md) — succession + break-glass |
| `build_repeatable` | Met | the repro gate blocks every release on a bit-for-bit rebuild (#118); the scheduled repro-check re-verifies published history from cold |
| `test_statement_coverage80` | Met per repo once its `.coverage-floor` ≥ 80 | canonical `coverage:check` ratchet in the gate |
| `signed_releases` | Met | Sigstore evidence bundle on every release |
| `version_semver` / `version_tags` | Met | git-cliff + App-minted `v*` tags |
| `dco` | Met | `lint:dco` enforces Signed-off-by |
| `security_review`, `assurance_case` | Met | docs/release.md + slsa-reference.md are the written assurance case |
| `installation_common`, `external_dependencies` | Met | mise-pinned toolchain; lockfiles everywhere |

## Gold — walled by headcount, deliberately not claimed

`bus_factor` ≥ 2, `two_person_review`, `contributors_unassociated`: all
require a second maintainer. Recorded in
[slsa-reference.md](slsa-reference.md); revisit the moment one exists.

## Form mechanics

Submit per repository at bestpractices.dev/en/projects/new (GitHub login,
select the org repo). The badge id goes into the README badge block
(`scaffold/README-badges.md`) and the entry row on issue #88.

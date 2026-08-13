# Best Practices self-attestation crib

The [bestpractices.dev](https://www.bestpractices.dev) form, answered once
here so each repository's submission (post-transfer — the form binds the
repo URL) is a review, not an afternoon of archaeology. Answers below are
the org-wide evidence; anything repo-specific is marked.

The canon's own entry is
[project 14058](https://www.bestpractices.dev/projects/14058), answered in
full across all six sections on 2026-08-13 — **it is the worked example,
and `https://www.bestpractices.dev/projects/14058.json` is the machine
copy every other repository starts from**. Six sections, not three: the
metal series (passing, silver, gold) and the OSPS Baseline series
(baseline-1, -2, -3) are answered on the same entry. Read
[Form mechanics](#form-mechanics) before touching a form — the ordering
trap there will redden the Monday audit if you skip it.

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
| Dynamic analysis (suggested) | Met only where fuzzing exists | cargo-fuzz via the belt's `lint:fuzz-build` + `audit:fuzz` pair, proven in release-lab (#316; edtf adopts at transfer). **Do not claim this for a CI gate.** The criterion's own definition requires a tool that *varies its inputs*, or an automated test suite with ≥ 80% branch coverage — running the software on the same tree every time is neither. CodeQL is *static* by the same document's definition ("without executing it") and belongs under `static_analysis`. Answer Unmet with the reason where neither holds; it is SUGGESTED at passing, so it costs nothing |

## Silver — the MUSTs that need real answers

| Criterion | Answer | Evidence |
| --- | --- | --- |
| `access_continuity` | Met | [continuity.md](continuity.md) — succession + break-glass |
| `build_repeatable` | Met **where a build occurs** | the repro gate blocks every release on a bit-for-bit rebuild (#118); the scheduled repro-check re-verifies published history from cold. The criterion says to answer N/A where no building occurs, which is the canon's case and any continuous repo's — do not copy Met into a repo that compiles nothing |
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

## Baseline — the same entry, a second shield

The OSPS Baseline series is answered on the same project entry and has
its **own** shield at `/projects/<id>/baseline` (a sibling of the metal
`/badge`, not a query parameter — `?level=` and `?series=` are ignored,
`/baseline-<n>/badge` 404s). `fix:badges` renders both whenever
`.badge-states` names a BP_ID.

Baseline-1 is winnable solo where Gold is not: the canon reached 100% on
it at registration. Baseline-2's only blocker is a roles-and-
responsibilities document, and Baseline-3's are documentation plus one
headcount criterion (`osps_qa_07_01`, non-author approval). The
level-bearing facts live in the JSON as
`badge_percentage_baseline_1..3`, `achieved_baseline_N_at` and
`lost_baseline_N_at` — the last of which is what `audit:badges` watches
for a withdrawn level, and what #168 tracks as recurring drift.

## Form mechanics

Submit per repository at `bestpractices.dev/en/projects/new` (GitHub
login). Then, in order:

1. **Choose the Metal series**, not Baseline, on the New Badge page.
   Both get answered eventually, but `audit:badges` gates the shield on
   `badge_percentage_0 >= 100` — the metal passing percentage — so
   starting on Baseline leaves the audit's number at zero.
2. **Type both URLs by hand**: the version-control URL and the project
   home page are both the repository URL. The "select one of your GitHub
   repos" dropdown lists only *personal* repos — the site's OAuth app is
   not granted on the org — so it will not offer the org repository.
   Ownership does not come from the dropdown: anyone who can commit to
   the repo can edit the entry.
3. **Answer every section.** Do not hand-click 190 criteria — see
   [Filling it without clicking](#filling-it-without-clicking).
4. **Reach 100% on passing *before* touching `.badge-states`.** This is
   the trap: `audit:badges` fails any repo whose shield is worn while
   the issuer reports passing below 100%, so setting
   `bestpractices <BP_ID>` at 18% publishes a shield that reddens the
   next Monday cron. Leave the line at `pending` until the entry is
   green, then set the id and run `mise run fix:badges`.

### Filling it without clicking

Two supported mechanisms, both better than the form:

- **Automation proposals** — a URL pre-fills an edit form for review:
  `/<locale>/projects/<id>/<section>/edit?<criterion>_status=Met&<criterion>_justification=...`,
  where `<section>` is `passing`, `silver`, `gold`, `baseline-1`, `-2` or
  `-3` (`choose` lets the user pick). Proposals only fill blanks unless
  `overrides=<glob>` is passed. Fields outside the named section are
  silently ignored, so batch by section. Criterion names are the short
  metal names (`floss_license`) or the lowercased OSPS form
  (`osps_ac_01_01`, never `OSPS-AC-01.01`).
  [Spec](https://github.com/ossf/best-practices-badge/blob/main/docs/automation-proposals.md).
- **`.bestpractices.json`** — a file at the repo root (or
  `.project.d/bestpractices.json`) that the badge app reads to propose
  answers. A `?` or `"unknown"` status is ignored entirely, so
  placeholders are safe. This is the org-wide multiplier: take
  `projects/14058.json`, strip what is not true of the new repo, commit
  it, and the entry pre-fills instead of being typed.
  [Spec](https://github.com/ossf/best-practices-badge/blob/main/docs/bestpractices-json.md).

### Answers that must not be copied between repos

The crib above is org-wide evidence, but four answers are repo-shaped and
copying the canon's is wrong:

- `build_repeatable`, `build`, `build_common_tools`,
  `installation_standard_variables` — N/A for the canon (nothing is
  built); Met in any repo that compiles.
- `test_statement_coverage80` / `90`, `test_branch_coverage80` — Unmet
  for the canon (no belt-legal bash coverage tool, see
  [`tooling-verdicts.md`](tooling-verdicts.md)); winnable in Rust repos
  via cargo-llvm-cov. **A consuming repo can legitimately outscore the
  conformance root**, which should not read as a regression.
- `dynamic_analysis` — Met only where fuzzing actually runs.
- Registry and DOI criteria follow the publish stub's `classes:`.

### What the badge is worth elsewhere

Scorecard's `CII-Best-Practices` check scores by tier and reads **0/10
until the entry exists** — it moved 0 → 5 on the canon the same day
passing hit 100%, taking the aggregate 7.0 → 7.1. Silver would raise it
again. Registering is therefore two scores, not one.

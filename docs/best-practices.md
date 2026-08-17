# Best Practices self-attestation crib

The [bestpractices.dev](https://www.bestpractices.dev) form, answered once
here so each repository's submission (post-transfer — the form binds the
repo URL) is a review, not an afternoon of archaeology. Answers below are
the org-wide evidence; anything repo-specific is marked.

The canon's own entry is
[project 14058](https://www.bestpractices.dev/projects/14058), answered in
full across all six sections on 2026-08-13 — **it is the worked example,
and `.bestpractices.json` at this repository's root is the machine copy
every other repository starts from**. Six sections, not three: the
metal series (passing, silver, gold) and the OSPS Baseline series
(baseline-1, -2, -3) are answered on the same entry. Read
[Form mechanics](#form-mechanics) before touching a form — the ordering
trap there will redden the Monday audit if you skip it.

Standing at the last re-score: **passing 100% (earned), silver 96%, gold
70%, baseline-1 100% (earned), baseline-2 100% (earned), baseline-3
95%.** Treat those as the high-water mark to check against, never as the
current reading — fetch `projects/14058.json` before asserting a number.

**The entry drifts, and nothing watches it.** It was answered at 08:58Z
and was wrong by five criteria at 10:10Z, when a single pull request
shipped `GOVERNANCE.md`, two new `SECURITY.md` sections and
`CONTRIBUTING.md` item 3. `audit:badges` watches `badge_percentage_0`
and `lost_*` — it cannot see a document that turns an Unmet criterion
Met. Until something diffs `.bestpractices.json` against the live entry,
re-score by hand whenever a change lands that a criterion would notice.

## Passing

| Criterion family | Answer | Evidence |
| --- | --- | --- |
| Basics: homepage, description, contribution | Met | README + CONTRIBUTING.md (org-wide health files) |
| FLOSS licence, in LICENSE | Met | per-repo LICENSE (0BSD in the org's own repos), `LICENSES/` + REUSE.toml, enforced by `lint:licence` (#214) |
| Change control: public VCS, unique versions, release notes | Met | GitHub; semver by git-cliff; CHANGELOG per release |
| Reporting: issue process, vulnerability process, ack ≤ 14 days | Met | issue forms; SECURITY.md (private reporting, 14-day ack) |
| Quality: build, automated test suite, new-functionality tests, warnings | Met | `mise run ci` = the cloud gate: the repo's own `test` task and the `coverage:check` ratchet run the suite, and every belt linter fails the gate on a finding. `warnings_strict` is **Met for Rust repos since 2026-08-16** (#445): `lint:rust` runs clippy with `-D warnings`, which denies rustc's own warn-by-default lints as well as clippy's, at every group with restriction minus nine named contradictions. Answer it from the tasks that actually run, per repo — a repo with no `lint:rust`-eligible source is answering about a different language, not inheriting this row. History worth keeping: this read "clippy/tests enforced; warnings deny" until 2026-08-13, which was never true of any repo, and then Unmet-with-the-reason until the belt actually grew the task |
| Security: secure design knowledge, no unencrypted auth, vuln fix ≤ 60 days | Met | trusted publishing only, no tokens; Dependabot + Renovate |
| Static analysis | Met | CodeQL default setup (org-enforced) + belt linters |
| Dynamic analysis (suggested) | Met only where fuzzing exists | cargo-fuzz via the belt's `lint:fuzz-build` + `audit:fuzz` pair, proven in release-lab (#316; edtf adopts at transfer); in Go the equivalent pair is `lint:go-fuzz-seeds` + `audit:go-fuzz`, which need no pin or config because the engine is in the toolchain (#445). **Do not claim this for a CI gate.** The criterion's own definition requires a tool that *varies its inputs*, or an automated test suite with ≥ 80% branch coverage — running the software on the same tree every time is neither. CodeQL is *static* by the same document's definition ("without executing it") and belongs under `static_analysis`. Answer Unmet with the reason where neither holds; it is SUGGESTED at passing, so it costs nothing |

## Silver — the MUSTs that need real answers

| Criterion | Answer | Evidence |
| --- | --- | --- |
| `access_continuity` | Met | [continuity.md](continuity.md) — succession + break-glass |
| `build_repeatable` | Met wherever the publish stub declares any class | the repro gate blocks every release on a bit-for-bit rebuild (#118); the scheduled repro-check re-verifies published history from cold. **This includes `source-archive`** — see the trap below. N/A only for a continuous repo that publishes nothing |
| `test_statement_coverage80` | Met per repo once its `.coverage-floor` ≥ 80 — **Unmet for the canon**, see the wall below | canonical `coverage:check` ratchet in the gate, which is gated on `.coverage-floor && Cargo.toml` and so skips a bash repo clean |
| `signed_releases` | Met | Sigstore evidence bundle on every release |
| `version_semver` / `version_tags` | Met | git-cliff + App-minted `v*` tags |
| `version_tags_signed` | Met | the tag objects **are** signed — Sigstore keyless, by the release workflow. See the `no_user` trap below |
| `governance`, `roles_responsibilities` | Met | [GOVERNANCE.md](../GOVERNANCE.md) — decision model, roles table, succession |
| `test_policy_mandated`, `tests_documented_added` | Met | CONTRIBUTING.md, "Requirements for acceptable contributions" item 3 |
| `dco` | Met | `lint:dco` enforces Signed-off-by |
| `security_review`, `assurance_case` | Met | docs/release.md + slsa-reference.md are the written assurance case |
| `installation_common`, `external_dependencies` | Met | mise-pinned toolchain; lockfiles everywhere |

### The one wall that is not headcount

Silver stands at 96% on two MUSTs, and **neither is a second maintainer**:

- `regression_tests_added50` — a measurement, not a tool. Every `fix:`
  commit in the window must be classified as "shipped the check that
  would have caught it" or not, and ≥ 50% must be yes. Free to close;
  nobody has done the pass.
- `test_statement_coverage80` — needs a number, and the number needs a
  bash coverage tool. `kcov` and `bashcov` are both **404 in the aqua
  registry**, so no belt-legal tool can measure the canon today.

Note the escape hatch does **not** apply: the criterion is conditional on
"if there is at least one FLOSS tool that can measure this in the
selected language", and kcov and bashcov are FLOSS tools that measure
bash. Aqua packaging is *our* constraint, not the criterion's, so N/A
here would be a false claim.

The wall is therefore the **language**, not a packaging decision. It
comes down with #392 (decided 2026-08-15): the canon's bash moves to a
Go tool in its own repo, `go test -cover` supplies the number, and the
canon gains a `.coverage-floor` like any Rust repo — see #398 for the
retirement of this section and the criteria it gates. Until then this
row stays honestly Unmet. A bash test framework was considered as the
separable half and **refused** (#364): it would neither produce a
number — the criterion needs coverage, not tests — nor survive the port.

## Gold — walled by headcount, deliberately not claimed

`bus_factor` ≥ 2, `two_person_review`, `contributors_unassociated`: all
require a second maintainer. Recorded in
[slsa-reference.md](slsa-reference.md); revisit the moment one exists.

Gold is therefore **unreachable, and caps at 20/23 ≈ 87%** even with
silver earned and every coverage criterion measured. Two of its criteria
that once read as walls are not: `code_review_standards` is Met from
[GOVERNANCE.md](../GOVERNANCE.md#code-review), and `small_tasks` is Met
whenever an open issue carries `good first issue`. Do not spend on the
remainder to chase a badge that headcount forbids.

## Baseline — the same entry, a second shield

The OSPS Baseline series is answered on the same project entry and has
its **own** shield at `/projects/<id>/baseline` (a sibling of the metal
`/badge`, not a query parameter — `?level=` and `?series=` are ignored,
`/baseline-<n>/badge` 404s). `fix:badges` renders both whenever
`.badge-states` names a BP_ID.

Baseline-1 and Baseline-2 are both winnable solo where Gold is not, and
both are **earned**: baseline-1 at registration, baseline-2 once
[GOVERNANCE.md](../GOVERNANCE.md) supplied the roles-and-
responsibilities document that was its single blocker. Baseline-3's
documentation blockers are all closed too — support scope and
end-of-life in [SECURITY.md](../SECURITY.md), the SAST remediation
threshold in the same file, the collaborator-review policy in
GOVERNANCE.md, the test policy in CONTRIBUTING.md — leaving it at 95%
on one headcount criterion (`osps_qa_07_01`, non-author approval). The
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
  placeholders are safe. The canon carries its own at the root, and
  [`scaffold/.bestpractices.json`](../scaffold/.bestpractices.json) is
  the stub a new repo copies — the `?` entries there are exactly the
  questions that repo must answer for itself.
  [Spec](https://github.com/ossf/best-practices-badge/blob/main/docs/bestpractices-json.md).

  **Never build one by renaming `projects/<id>.json`.** The download and
  the input use *different key forms for baseline criteria*, and the
  mismatch fails silently. The download renders the display ID
  (`OSPS-AC-01.01_status`); the input whitelist is built from the
  criteria YAML keys (`osps_ac_01_01_status`) —
  `Project::PROJECT_PERMITTED_FIELDS`, via
  `ALL_CRITERIA_STATUS = Criteria.all.map(&:status)`.
  `CriterionFieldValidator.validate_field_name` returns `nil` on a miss
  and `RepoJsonDetective` does `next unless field_sym`, so an unrenamed
  file **drops all 64 baseline criteria without a warning** — the metal
  series fills, the Baseline series silently does not. It is the same
  rule the proposal URLs already follow one bullet above; it governs the
  JSON file too. Lowercase, `-` and `.` both become `_`.

  `lint:bestpractices` guards exactly this in the gate — it is offline
  and deterministic, so it greps for the display-ID key form and checks
  the size cap, and deliberately does not parse the JSON (jq is not a
  belt tool). The remaining limits below are documented, not enforced.

  Three more limits read out of the same source, not guessed:

  - `RepoJsonDetective::MAX_FILE_SIZE = 100_000` — 100 KB, checked
    against GitHub's reported size *before* download, so an oversized
    file is skipped entirely rather than truncated.
  - `Project::MAX_TEXT_LENGTH = 8192` — a longer justification is
    dropped by `validate_justification`, silently again.
  - The detective reports `confidence: 3.5`, and
    `Chief::CONFIDENCE_OVERRIDE` is `4`. **So this file can never
    overwrite a human-entered answer** — it only fills blank or unknown
    fields. Clicking **Save (and continue) 🤖** on a hand-tuned entry is
    therefore safe.

  It is **not** a default community health file. GitHub's inheritance
  list is closed (CODE_OF_CONDUCT, CONTRIBUTING, GOVERNANCE, SECURITY,
  SUPPORT, FUNDING, issue/PR templates, discussion forms), so a root
  JSON file in this repository reaches no other repository by itself;
  the badge app reads it from whichever repo the entry's `repo_url`
  names. Copying is deliberate, per repo. It only ever *proposes*: the
  app runs automations when you first edit a section, or when you click
  **Save (and continue) 🤖** to re-trigger after changing the file, and
  a human still submits.

  Check it landed rather than assuming, because every failure mode above
  is silent — after the first edit, the Baseline sections should show
  answers, not a wall of `?`:

  ```bash
  curl -sS "https://www.bestpractices.dev/projects/<id>.json" |
    jq '[to_entries[] | select(.key | test("^OSPS-.*_status"))
         | select(.value != "?")] | length'
  ```

### Three traps in the form itself

Each of these cost a wasted round trip on 2026-08-13:

1. **`(URL required)` criteria have no URL box.** Passing
   `<criterion>_url=` in an automation proposal is silently dropped and
   the criterion renders a red `?` even sitting at Met. The URL goes
   **inside the justification text**. Affects `governance`,
   `roles_responsibilities`, `code_review_standards`, `small_tasks`,
   `hardened_site`, `hardening`, `bus_factor` and peers.
2. **Higher levels demand lengthier justifications for criteria already
   answered below.** `repo_distributed` sat at `Git.` — fine at passing,
   "Warning: Requires lengthier justification" at gold. A section that
   reads 100% at its own level can still carry warnings on the level
   above, so check the gold view even when chasing silver.
3. **`OSPS-BR-01.02` is a retired criterion stored as the number `0`,**
   not a status string. It is in `baseline_criteria_retired.yml`
   (retired in the 2026-02-19 Baseline revision), scores nothing, and
   must never be copied into a `.bestpractices.json`. Written without a
   leading `v` deliberately: `audit:citations` reads a `vX.Y.Z` in
   `docs/` as a claim about an org tag, and this is an upstream revision
   name, not one of ours.

### Answers that must not be copied between repos

The crib above is org-wide evidence, but four answers are repo-shaped and
copying the canon's is wrong:

- `build`, `build_common_tools`, `build_floss_tools`,
  `installation_standard_variables` — N/A for the canon. Note the
  conditional these hang on: *"if the software **requires building for
  use**"*. Workflows and tasks are consumed straight from the
  repository, so it stays N/A here even though the project does build a
  release artifact. A repo whose users must compile something answers
  Met.

  **Give each its own reason; never write "no build system exists".**
  That sentence is false — one exists — and it sat on the live entry
  until 2026-08-13 flatly contradicting `build_repeatable`'s "building
  does occur". The criteria have *different* escape clauses and each
  justification must cite its own:

  - `build` — clause: requires building *for use*. Consumers
    reference the workflows by SHA; nothing is compiled to run them.
  - `build_standard_variables` — clause: *no native binaries*. There
    is no compiler or linker invocation to pass `CFLAGS` to.
  - `build_preserve_debug` — clause: no build *or installation*
    system. Nothing is compiled, so no debug information to strip.
  - `build_non_recursive` — same clause. The archive build is one
    `git archive` step, with no subdirectory recursion.
  - `build_repeatable` / `build_reproducible` — clause: *no building
    occurs*. Building **does** occur, so these are **Met**.
- `build_repeatable` / `build_reproducible` — **Met for the canon**,
  and this is the trap that caught the first pass of these answers. See
  below.
- `test_statement_coverage80` / `90`, `test_branch_coverage80` — Unmet
  for the canon (no belt-legal bash coverage tool, see
  [`tooling-verdicts.md`](tooling-verdicts.md)); winnable in Rust repos
  via cargo-llvm-cov. **A consuming repo can legitimately outscore the
  conformance root**, which should not read as a regression.
- `dynamic_analysis` — Met only where fuzzing actually runs.
- Registry and DOI criteria follow the publish stub's `classes:`.

### The trap: "no compiled code" is not "no build"

Answer the build, SBOM and signing criteria from **what the release
actually publishes**, never from a description of the repository. The
first pass of the canon's answers got three wrong by reasoning from
"this repo is configuration, not software" and never opening a release.

`source-archive` is an artifact class like any other. A repo that
declares it builds a tarball through `build-source`, rebuilds it
independently through `repro-build-source`, and the repro gate compares
the two bit for bit before anything is signed or attached. So a
repository that compiles nothing still has a build, and a reproducible
one. The canon's v1.25.0 publishes four assets:

```text
github-1.25.0.tar.gz          the built archive
github-1.25.0.spdx.json       an SPDX SBOM
checksums.txt                 per-asset digests
attestations.intoto.jsonl     the Sigstore evidence bundle
```

Which makes `build_repeatable` (silver), `build_reproducible` (gold)
and `osps_qa_02_02` (baseline-3, SBOM) all **Met**, where reasoning from
the repository's shape had answered N/A to each. N/A and Met both count
as complete, so the error costs no percentage — it just publishes a
weaker claim than the evidence supports, which is the wrong direction
for an entry strangers read.

Check first, every time:

```bash
gh release view "$(gh release list -L1 --json tagName --jq '.[0].tagName')" \
  --json assets --jq '[.assets[].name]'
```

### The trap: `no_user` does not mean unsigned

The same failure one layer down. The canon's first answers recorded
`version_tags_signed` as Unmet because "GitHub reports them unsigned".
GitHub reports no such thing. The release tags **are** cryptographically
signed — Sigstore keyless, by the release workflow, the signature
carried in the tag object itself:

```bash
sha=$(gh api repos/OWNER/REPO/git/ref/tags/vX.Y.Z --jq .object.sha)
gh api "repos/OWNER/REPO/git/tags/$sha" \
  --jq '{reason: .verification.reason, signed: (.verification.signature != null)}'
```

That returns `reason: "no_user", signed: true`. GitHub resolves
signatures to GitHub *accounts*, and a keyless workflow identity is not
an account — so `no_user` means "the signer is not a user here", never
"there is no signature". Verification is against the Fulcio
certificate's SAN, exactly as `signed_releases` describes. The commit
side of this same trap is in [continuity.md](continuity.md).

### What the badge is worth elsewhere

Scorecard's `CII-Best-Practices` check scores by tier and reads **0/10
until the entry exists** — it moved 0 → 5 on the canon the same day
passing hit 100%, taking the aggregate 7.0 → 7.1. Silver would raise it
again. Registering is therefore two scores, not one.

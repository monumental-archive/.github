# The tooling verdict register

Every SLSA/OpenSSF-adjacent tool the org has considered, with verdict,
reason and re-examination trigger — written once, here, so a settled
decision stops being re-litigated and a stranger's "why not X?" is
answerable by a link (#213). Where another document argues a verdict
at length, this register links rather than restates; where a verdict
existed only in a closed PR or an issue body, this is now its record.

The general priors behind most rows: zero standing infrastructure
(nothing in the org runs and stays up), keyless identity (no
self-held key custody), aqua-backed tools with checksums and
attestations, and a single-maintainer headcount that some doors are
honestly blocked on.

## Adopted, or adopted-in-equivalent

| Tool | Discharges |
| --- | --- |
| mise + aqua belt (`mise/config.toml` + lock) | Pinned, checksummed, attestation-verified toolchain, org-wide |
| Sigstore keyless (cosign, `actions/attest`) | All signing: provenance, VSAs, VEX, source chain |
| GitHub attestation store + evidence bundles | Provenance distribution (Build L1/L2) |
| cargo-deny | Advisory gate in `ci`; the release path's refusal mechanism |
| cargo-auditable | Dependency tree in-artifact; the SBOM's image-side closure |
| trivy | Published-image scanning by digest, misconfig + secrets in the gate |
| zizmor (offline in gate, online in audit) + actionlint | Workflow hygiene, the capability-boundary's supporting cast |
| OpenSSF Scorecard (org-owned workflow) | The external report card; publishes because monitoring beats blindness |
| CodeQL (org security configuration) | Code scanning across the org |
| rekor-monitor (org-owned workflow) | Fulcio certificate-log watch for forged signer identities (#1237), run by `stele assert signer-certificates` (#1339) — the reason the id-token rule was narrowed |
| Renovate (org preset, zero-age canon fan-out) | Pin freshness; the release-age quarantine |
| OSV via `audit:blast-radius` | Malicious-package and advisory sweep over every published SBOM |
| cargo-llvm-cov + `.coverage-floor` ratchet | Derived coverage floor in the gate (`coverage:check`), measured on the repository's own `test` run through `measured` and rendered by `cargo llvm-cov report` (#1653), re-derived by the release path, codecov badge feed off-gate; Rust branches as a floor of their own (`rust-branches`, #1359), measured with `--branch` on the repo's dated nightly and held by the Monday audit (`audit:coverage-branches`), never the gate |
| coverage.py (`pipx:` backend via aqua-backed uv) | The `python` and `python-branches` legs of the same ratchet (#1331): the repository's own `test` task run under coverage, child processes included, statements and branches |
| vitest's own coverage provider (the repository's, not the belt's) | The `typescript` and `typescript-branches` legs of the same ratchet (#1357): the repository's vitest under the provider its lockfile pins, lines and branches; the belt pins neither |
| plpgsql_check (the repository's test stack, never a published image; the belt pins nothing) | The `plpgsql` and `plpgsql-branches` legs of the same ratchet (#1547): `mise/plpgsql_coverage.py` reads the profiler's statement and branch coverage in the suite's teardown, through the repository's own `psql` |
| Hypothesis (`pipx:` backend via aqua-backed uv) | Python property testing (#1332): every tracked `prop_*.py`, fixed seed in the gate (`test:python-properties`), random seed and wider bounds on the Monday cron (`audit:python-properties`) |
| fast-check (the repository's, not the belt's) | TypeScript property testing (#1549): every tracked `*.prop.test.{ts,tsx,mts,cts}` under the repository's own vitest, fixed seed in the gate (`test:typescript-properties`), random seed and 10,000 runs on the Monday cron (`audit:typescript-properties`); `lint:typescript-properties` refuses an import outside a property file and a restated seed; the belt pins neither tool |
| cargo-fuzz (`cargo:` backend) | Fuzz targets: build proof in the gate (`lint:fuzz-build`, stable, no sanitizer), bounded runs under AddressSanitizer on the cron (`audit:fuzz`, repo-declared nightly) |
| reuse (`pipx:` backend via aqua-backed uv) | REUSE-spec compliance in the gate (`lint:reuse`), pre-registration |
| uv (`aqua:astral-sh/uv`) | The installer behind every `pipx:` belt tool; carries the org's release-age floor into Python |
| jq (`aqua:jqlang/jq`) | JSON on the command line for eleven belt tasks, one of them in the gate |
| pinact (`aqua:suzuki-shunsuke/pinact`) | The version-comment half of the pinning convention: `lint:action-pins` offline, `audit:action-pins` online, `fix:actions` |
| lychee (`aqua:lycheeverse/lychee`) | Link liveness over tracked markdown (`audit:links` — network, Monday cron, per repo since #681), org policy in the canon's `mise/lychee.toml` |
| biome (`aqua:biomejs/biome`) | JS/TS/JSON lint + format + assist in the gate (`lint:biome`) at `preset: "all"`, nursery included, domains from the repo's own `biome.json` and applied to nursery too (`lint:biome-domains`). **On this tool the pinned binary is the authority and the published docs are the cross-check, not the other way round:** biome's own configuration reference documents neither `linter.domains` nor `assist.actions.preset`, both of which exist in the 2.5.7 schema and both of which the org's config uses. Measure, then cite. Two stable rules are excluded — `complexity/useLiteralKeys`, a mechanical contradiction with the org's `noPropertyAccessFromIndexSignature` tsc dial (#759), the same class as clippy's `arbitrary_source_item_ordering` in #701, and `style/noTernary`, whose remedy does not exist in expression position (#767). Three more are off for TEST FILES only, through the org's one `overrides` entry — `style/noMagicNumbers` and `performance/useTopLevelRegex`, because in a test the literal IS the specification (#783), and `complexity/noExcessiveLinesPerFunction`, because a `describe` callback's span is the sum of its tests (#804); the glob list is closed, measured from every JS/TS file in the org, and a repository cannot widen it. One nursery rule is off beside them — `nursery/noConditionalExpect`, 8 findings and 8 false positives on its first real tree, one of them in production code, with an expiry at [biomejs/biome#11455](https://github.com/biomejs/biome/issues/11455) (#788). |
| ruff (`aqua:astral-sh/ruff`) | Python lint + format in the gate (`lint:python`) at `select = ["ALL"]` + preview |
| mypy (`pipx:` backend via aqua-backed uv) | Python type checking at its strictest (#1604), config delivered as `mise/mypy.ini`: `audit:python-types` while the canon conforms, then `lint:python-types` in the gate |
| golangci-lint (`aqua:golangci/golangci-lint`) | Go lint + format in the gate (`lint:go`) at `default: all` + curated disables; gofumpt (extra rules) + gci as its formatters |
| govulncheck (`go:` backend, repo-side pin) | Call-graph-aware Go advisory scan (`audit:go-vulns`, network — Monday cron) |
| gomu (`go:` backend, built by the repository's own go, lazy, sumdb-verified) | Go mutation testing (`audit:mutants`' Go leg — Monday cron, never `ci`): no unexplained surviving mutant, adopted per repo by a pattern-free `.gomuignore` — chosen over gremlins and go-mutesting on a measured comparison (#1356) |
| gobco (`go:` backend, built by the repository's own go, lazy, sumdb-verified) | Go condition coverage as a floor of its own (`go-branches`, #1546), held by the Monday audit (`audit:coverage-go-branches`), never the gate: each operand of `&&` and `\|\|` taken both ways, one package per call; chosen over gocoverageplus on a measured comparison |
| yamllint (`pipx:` backend via aqua-backed uv) | YAML lint in the gate (`lint:yaml`), full rule inventory at error + `--strict` |
| editorconfig-checker (`aqua:`, binary `editorconfig-checker`) | Whole-file hygiene in the gate (`lint:editorconfig`) against `.editorconfig`, the org's one written indentation rule |
| gitleaks (`aqua:gitleaks/gitleaks`) | Secret scanning at commit depth in the gate (`lint:gitleaks`, full history, `--redact`) |
| hadolint (`aqua:hadolint/hadolint`) | Dockerfile lint in the gate (`lint:hadolint`), every severity fails (`--failure-threshold style`) |
| cargo-machete (`github:` backend, upstream sha256s) | Unused Rust dependencies in the gate (`lint:machete`, static) — proven first in a consumer |
| cargo-semver-checks (`github:` backend, TOFU checksum) | Public API vs published baseline (`audit:semver` — network, Monday cron, per repo) — proven first in a consumer |
| cargo-mutants (`cargo:` backend, compiled `--locked`, pinned `lazy`) | Rust mutation testing (`audit:mutants`' Rust leg — its own weekly workflow, `repo-mutants.yml`, never the gate or the audit): no unexplained surviving mutant, never a score; adoption is a tracked `.cargo/mutants.toml`, each exclusion an `exclude_re` entry under a `#` reason (#1355) — proven first on release-lab |
| mutmut (`pipx:` backend, belt Python, pinned `lazy`) | Python mutation testing (`audit:mutants`' Python leg — its own jobs in `repo-mutants.yml`, never the gate or the audit): no unexplained surviving mutant; adoption is `[tool.mutmut]` in a tracked `pyproject.toml` (or `setup.cfg`), each exclusion a `# pragma: no mutate -- <reason>` at its site or a reasoned list entry (#1354) — proven first on the canon |
| StrykerJS (the repository's own, from its lockfile; the belt pins nothing) | TypeScript mutation testing (`audit:mutants`' TypeScript leg — its own jobs in `repo-mutants.yml`, never the gate or the audit): no unexplained surviving mutant; adoption is a tracked root `stryker.config.json`, each exclusion a `// Stryker disable next-line <Mutator>: <reason>` at its site; run `--inPlace` (#1358) — proven first on monumental-archive |
| cargo-auditable (`github:rust-secure-code/cargo-auditable`, repo-side pin, upstream sha256s) | Release build input of every class that ships a Rust binary (#1334), prebuilt where the `cargo:` backend compiled it per job (187–252 s on edtf v1.3.5). Not `aqua:`: mise's baked registry has no entry (mise 2026.11.6: `no aqua-registry found for rust-secure-code/cargo-auditable`). `mise lock` at 0.7.7 records, for all four platforms, checksums equal to upstream's `.tar.xz.sha256` files (measured 2026-11-17); on Linux mise selects the `unknown-linux-gnu` asset, not musl. A locked install took 2.9 s. Reopen when a mise release's baked aqua registry carries the package |
| sqlfluff (`pipx:` backend via aqua-backed uv) | SQL lint in the gate (`lint:sql`, all core rules, dialect postgres) + `fix:sql` — proven first in monumental-archive-db |
| shellcheck + shfmt over mise task bodies (`mise/belt_shell.py`) | The third shell surface in the gate (`lint:belt-shell`) at `--enable=all` with nothing excluded, this file's own tasks included — plus `fix:belt-shell` |
| clippy (rustup component of the repo's own pin) | Rust lint in the gate (`lint:rust`): every group at deny, restriction minus eleven named contradictions, `-D warnings`, org knobs in the canon's `mise/clippy.toml`, every workspace member linted as its own package — plus `fix:rust` |
| rustfmt (rustup component of the repo's own pin) | Rust format check in the gate (`lint:rust`, same task): the canon's `mise/rustfmt.toml` at rustfmt's **stable** maximum, `style_edition` pinned — plus `fix:rust` |
| a second pinned `rust` toolchain (`core:rust`, repo-side) | The declared minimum compiled in the gate (`lint:msrv`): every crate declaring `rust-version` is built at a pin of exactly that version, and `mise/msrv_plan.py` holds `Cargo.toml` and `mise.toml` in agreement about which version that is |
| `aqua:theseus-rs/postgresql-binaries` (repo-side, one pin per major) | A real Postgres for the gate: the pgrx extension compiles (`lint:pg-clippy`) and its suite runs at every declared major (`test:pgrx`), with `mise/pgrx_postgres.py` holding the crate's `pgNN` features and the repository's pins in agreement |
| `github:unicode-org/icu` (repo-side, macOS only, TOFU checksum) | ICU's headers for building a pgrx extension on a Mac: the prebuilt Postgres is configured `--with-icu` and ships the dylibs without the headers, and pgrx binds every Postgres header from 0.19.3. `mise/pgrx-env.sh` adds them to the build and holds the pin to the ICU major each pinned Postgres was built against |
| a canon-written check, no tool (`mise/vendored.sh`, bash) | Every tracked file under any `vendor/` directory pinned byte for byte to an upstream commit (`lint:vendored`), against the `PINNED.toml` beside it — the other half of every `:!vendor/**` in the belt |

**clippy, and the three things standing it up settled** (#445). The
belt's Rust gate, `lint:rust`. Three rulings worth keeping, because each
was reached by running the tool rather than reading about it.

*Levels are stated as groups minus exclusions, never as a list of lints
to enable.* Both forms measured identically at standup — 97 findings on
release-lab either way — and they fail in opposite directions. A list of
lints to enable is fail-open: when a toolchain bump adds restriction
lints, they are absent from the list, so enforcement quietly fails to
grow and nothing goes red. Stated as `-D clippy::restriction` minus named
exclusions it is fail-closed: a new lint arrives already enforced with no
canon edit, and a name that upstream renames away stops excluding
anything, so the contradiction it was hiding fires and the gate reddens
where someone reads it. The same reasoning should govern any future belt
list.

*The eleven exclusions are mechanical, not taste.* clippy's own book says
the restriction group must not be enabled wholesale, because its lints
contradict each other — enabling it as a group even makes clippy lint
you for doing so (`blanket_clippy_restriction_lints`, allowed here with
exactly that as its reason). Nine are the pairs where obeying one
lint disobeys another: `implicit_return` against style's
`needless_return`; `question_mark_used` against `question_mark`;
`self_named_module_files` against `mod_module_files` (the org enforces
the 2018 self-named layout); `pub_with_shorthand`, whose name is
inverted — it *bans* `pub(crate)`, so the org runs `pub_without_shorthand`
instead; `semicolon_outside_block`; `separated_literal_suffix`, since the
org writes `1_u32`; `big_endian_bytes` and `little_endian_bytes`, because
all three endianness lints together ban every byte conversion and what
the org actually wants banned is target-dependent `_ne_bytes`; and
`inline_asm_x86_intel_syntax`, Rust's own `asm!` default being Intel.
`multiple_crate_versions` is allowed for a different reason entirely —
the duplicate policy is stated once, in each repo's `deny.toml`
(docs/dependency-track.md).

The tenth, `arbitrary_source_item_ordering`, is a contradiction of the
same kind, just with an org ruling on the other side rather than another
clippy lint. It demands alphabetical source items, and this organisation
has already decided that question twice the other way: `rustfmt.toml`
keeps `reorder_keys` off because "alphabetical reordering destroys
semantic grouping", and `yamllint.yaml` disables key-ordering with the
same reason recorded. A lint that contradicts two standing rulings is
mechanical to exclude; leaving it on would have meant the belt enforcing
in Rust exactly what the belt forbids in TOML and YAML.

The eleventh, `unneeded_field_pattern`, is the first pair ruled
*against* the idiomatic form (2026-11-15, iiif-server#181). Rust 1.99
added `rest_pattern_accessible_field` to the restriction group, and the
two lints are mirror images: each one's "use instead" example is the
other's finding. Measured at 1.99.1 with both on, all four ways of
ignoring a field fail — `A { .. }` and `A { w, .. }` on the new lint,
`A { w: _, h: _ }` and `A { w, h: _ }` on the old — so no repository
could conform, and the bump reddened iiif-server (75 findings) and edtf
(11) on arrival. Which of the two goes was decided on their stated
reasons. The old lint's is brevity: `..` "is shorter". The new lint's is
correctness: a rest pattern "might lead to unhandled fields when the
struct changes". That is the argument the ladder already enforces for
enums through `wildcard_enum_match_arm`, where a catch-all arm is banned
so that a new variant must be handled; keeping `..` for struct fields
would have been the inconsistent ruling. So the old lint is excluded and
the new one stands. On a toolchain older than 1.99 the exclusion costs
only the old lint, since the new one does not exist there.

*The ladder was recalibrated against a real codebase (#687).* The 97
findings above are release-lab's — a fixture of small crates — and the
canon tracks no Rust at all, so until 2026-08-21 nothing had run this
ladder over a production tree. Importing edtf (#669) did: **955 errors
across roughly thirty restriction lints** from `mise run lint:rust` at
belt `1818749`, on a tree `cargo check` compiles cleanly. (The same tree
read 896 at belt `511af95` earlier the same day; a count from this ladder
is only meaningful with the belt SHA that produced it, which is why both
are stamped.) One of them, `arbitrary_source_item_ordering` at 113
findings, was the exclusion above. The rest stand, and edtf
conforms to them per site with `#[expect(..., reason = "...")]` — the
decision being that the ladder is right and the codebase answers to it,
not the reverse. Read the 97 as what a fixture measures, never as what
this ladder costs a real repository.

*One further contradiction, and it applies to pgrx crates only (#813).*
`tests_outside_test_module` demands `#[cfg(test)]`. pgrx demands
`#[cfg(any(test, feature = "pg_test"))]`, because `cargo pgrx test`
compiles the tests INTO the extension under that feature and runs them
inside a live Postgres; under a bare `#[cfg(test)]` they are not built
into the extension at all and the suite finds nothing. Two tools, one
module, incompatible attributes — the same test the ones above pass, with
pgrx standing where the other lint usually stands. Measured on edtf's
extension crate the first time it was ever linted at this level: **7 of
73 findings** were this lint, one per `#[pg_test]`, on a module carrying
exactly the cfg pgrx documents. It is named in `mise/clippy-ladder.sh`
rather than left to seven `#[expect]` attributes in every pgrx crate in
the org forever, and it is scoped by derivation — a crate that does not
depend on pgrx never sees it.

*What that first linting cost, recorded so the next one is not a
surprise.* The remaining **66** findings on `edtf-postgres` are ordinary
import debt of the #687 kind, not structural: 26 `missing_assert_message`,
13 `single_call_fn`, 9 `missing_panics_doc`, 5 `min_ident_chars`, 3
`default_numeric_fallback`, 2 each `missing_inline_in_public_items` and
`too_long_first_doc_paragraph`, and singletons including `wildcard_imports`
on `pgrx::prelude::*` and `inline_modules` on the `pub mod pg_test`
harness. release-lab's `lab-pg` reads 20 of the same classes. Each was
checked against pgrx's requirements and none of them is a contradiction —
which is exactly why they are debt to work through rather than exclusions
to add. The crate carried it for forty releases because `CLIPPY_EXCLUDE`
hid it.

*An `#[expect]` is written against a clippy configuration, not a lint
name (edtf#201).* So a crate can be held at exactly one. Measured on
`edtf@3106121`: seven `#[expect(clippy::missing_panics_doc)]` on private
`#[pg_test]` functions were correct under `lint:pg-clippy`, which
delivers the org `clippy.toml` and its `check-private-items = true`, and
a hard error under the repo's own `pg:lint`, which set no
`CLIPPY_CONF_DIR`: there the lint stops at the `pub` boundary, never
fires, and an expectation that does not fire is an error under
`-D warnings`. Neither run was wrong about the code. The suppression
itself depends on the configuration, which is why a repo-local clippy
task beside the belt's is not a second opinion but a contradiction, and
why the remedy was to delete `pg:lint` rather than teach it the org
ladder. Two corollaries from the same work. An expectation on an
allow-by-default lint is silent wherever that lint is off, so it proves
nothing there. And an expectation on `single_call_fn` cannot be
unconditional if the test module also calls the function: with tests
compiled the lint has two callers and goes quiet, so the shape that
survives both targets is
`#[cfg_attr(not(any(test, feature = "pg_test")), expect(...))]`.

*The whole workspace is linted every run, one crate at a time (#726).*
Under `-D warnings` a clippy finding IS a compile error, so a red crate
emits no metadata and every crate that depends on it is never linted at
all — which means the counts in the paragraph above are not what they
say they are. The 955 was `edtf-core` alone, with four crates behind it
unseen; iiif-server's 24 became 319 the moment the last `iiif-core`
finding was cleared, because the S3 backend behind it had never once
been linted (#669, #671). `--keep-going` is the flag cargo offers for
this and it is not enough: the Cargo Book defines it as building "as
many crates in the dependency graph as possible, rather than aborting
the build on the first one that fails to build", which covers siblings
and never a dependent, because a dependent has nothing to compile
against. Measured at `-j1` on a two-crate fixture carrying this ladder —
independent crates, the plain run reported 1 of 2 and `--keep-going` 2
of 2; the second crate depending on the first, both runs reported the
first alone. So each member is now linted as its own primary package,
with `cargo metadata`'s `workspace_members` naming the population and
the clippy book's `--no-deps` keeping a red dependency from failing the
pass before its dependent is ever reached. The task prints the crates it
linted, the crates `CLIPPY_EXCLUDE` held back and the crates that went
red, in both directions, so a truncated run can never read as a whole
one. On the iiif-server import branch that is 194 finding sites in one
crate before and 470 across three after, with `iiif-server` and
`iiif-sources` linted for the first time; on the edtf branch the verdict
is unchanged and the green line now names all five crates it covers.
`fix:rust` mirrors the population — `--workspace` rather than the
workspace's default members, and `CLIPPY_EXCLUDE` honoured — but not the
mechanism: `cargo fix` caps lint denials to warnings and says so in its
own note, so one pass already reaches every crate (measured, both).

*The exception mechanism is `#[expect(..., reason = "...")]` and nothing
else.* No clippy flag can beat a source attribute: a crate-level
`#![allow(clippy::pedantic)]` silences every level the task sets and
exits 0, and `-F` only downgrades that to a warning it still exits 0 on.
`--force-warn` defeats attributes but is capped at warn, so it cannot
fail a gate. What closes it is restriction's own `allow_attributes` and
`allow_attributes_without_reason` — both arriving with the group — which
together make `#[allow]` a hard error and an unreasoned suppression a
hard error, leaving `#[expect]`, which errors the moment it stops being
needed. `lint:rust` additionally greps tracked sources for crate-level
group suppression, which is policing rather than construction, and is
named as such.

**rustfmt, and why its maximum is smaller than it looks** (#445). The
format half of `lint:rust`, sharing the task with clippy on the
lint:go/lint:python precedent — one language, one subject-named task.

*The ceiling is stable's, and that is the whole design.* Nearly every
rustfmt option that would tighten anything is nightly-only:
`error_on_line_overflow`, `error_on_unformatted`, `wrap_comments`,
`comment_width`, `format_strings`, `format_code_in_doc_comments`,
`normalize_comments`, `normalize_doc_attributes`, `imports_granularity`,
`group_imports`, `reorder_impl_items`, `format_macro_matchers`,
`condense_wildcard_suffixes` — and `required_version`, which would have
pinned the formatter itself. What remains stable is mostly already at
maximum by default, because rustfmt's defaults *are* the Rust Style
Guide. So the org's config is short by necessity, not by restraint.

*Two fail-open surfaces, both measured, both turned into checks.* An
unknown key in `rustfmt.toml` is a warning with **exit 0**, and a
nightly-only option on a stable toolchain is a warning with **exit 0**
(`Warning: can't set wrap_comments = true, unstable features are only
available in nightly channel`). Either one formats to a weaker standard
behind a green gate, so `lint:rust` fails on any rustfmt warning rather
than trusting the exit code. This also caught `hex_literal_case`, which
the online reference lists as stable and 1.97.1 rejects — the reference
documents master, not any released toolchain, exactly as it did for the
clippy config. The option is wanted and blocked until it is stable in a
toolchain the org pins.

*`style_edition = "2024"` is the one setting that fixes a defect rather
than tightening taste.* rustfmt invoked directly defaults to style
edition 2015 while `cargo fmt` infers it from Cargo.toml, so the same
file formats two ways depending on how it was called — upstream's own
README warns about it. Pinned, both agree, and every repo formats to the
current Style Guide whatever edition its crate declares.

*Suppression is held to clippy's standard, minus the mechanism.*
`#[rustfmt::skip]` takes no reason and has no `#[expect]` analogue, so it
can never retire itself. A whole-file `#![rustfmt::skip]` is refused
outright; a narrow one must carry a `// rustfmt-skip: <why>` marker, the
idiom `lint:capability-boundary` already uses. All four states — a
formatting diff, a whole-file skip, an unmarked skip, and a marked one —
were run against a throwaway crate before this landed, because a check
that cannot go red is a claim.

*`cargo fmt` rather than tracked files*, which inverts the belt's usual
rule. rustfmt walks each target's module tree, so a `.rs` file no crate
references is never visited — but such a file is not a build input
either, and rustc cannot see it any more than rustfmt can. Feeding
`git ls-files` would additionally mean choosing an edition in the belt,
when Cargo.toml states it per crate and `cargo fmt` reads it there.

**The nightly question, open and deliberately not answered here.**
Adopting a pinned nightly would unlock rustfmt's unstable options and,
more importantly, cargo-fuzz's sanitizers — the actual gap behind "already
on the belt, not at maximum" (#445). mise can pin a dated nightly
(`rust = { version = "nightly-2026-07-20", ... }` installs and locks), so
determinism survives; a bare `nightly` would not, and is excluded by the
gate rule. The two tools pull differently, though. Fuzzing is per-repo
and belongs to `audit:fuzz` on the cron, so nightly there is opt-in by
the repos that fuzz and never touches the gate — which also lets
`lint:fuzz-build` become a lint that only reads (#374). A formatter is
org-wide-or-nothing: a half-nightly org formats to two standards with
nothing red. Costs on both: a dated nightly is not Renovate-bumpable, so
it would be the org's only hand-maintained version pin. *Reopen:* the
cargo-fuzz standup, where the payoff is finding memory bugs rather than
wrapping comments.

**Two gaps recorded rather than closed.** clippy cannot be belt-pinned:
it is a rustup component of each repo's own toolchain, so repos can in
principle run different clippy versions and a Rust bump changes what is
enforced with no canon change. Renovate bumping every repo to the same
version is what keeps them level in practice, and the levels being
stated as groups means a newer toolchain is strictly stricter rather
than differently strict — so the skew is bounded in the safe direction,
and a guard asserting version equality would be machinery policing
machinery. Rust also stays out of the belt deliberately: the belt carries
what the *belt* needs to run (Go for `go run stele`, Python for the
`pipx:` venvs), while repos carry what they *build* with — and every
cargo-needing belt task skips clean without a Cargo.toml, so nothing in
the belt needs cargo in a non-Rust repo. Second gap: `mise/clippy.toml`
is an allow-list of knobs, and unlike the lint levels it cannot
self-extend, so a future clippy option with a permissive default would be
missed silently. An unknown key is a hard error, so removals and renames
fail loudly — `min-ident-chars-lint-trait-impl`, taken from clippy's
online reference, which documents master rather than any released
toolchain, was rejected by 1.97.1 on the first real run. Closing the
remaining half would take an `audit:*` task diffing the config against
the toolchain's accepted key list.

**Zero Rust in the canon** — `lint:rust` skips clean here, like biome,
golangci-lint, sqlfluff and the existing Rust pair, so the rule set is
exercised first in release-lab. Stated outright rather than left to be
discovered: at adoption the full set measured 97 findings there across
ten lints, 72 of them one missing manifest block, and the task's own
plumbing (config delivery, component assertion, exit path) was run
against that repo before this landed.

**One lint enabled but inert.** `clippy::excessive_nesting` ships with
`excessive-nesting-threshold = 0`, which disables it. Any other value is
a number the org invented rather than the tool's maximum, so it stays
enabled and silent until someone argues a threshold. *Reopen:* a nesting
depth the org actually wants to refuse.

**The declared minimum is compiled, not asserted** (#820). `rust-version`
is a public API: cargo refuses to resolve the package for a consumer on
an older toolchain, so raising the real minimum without saying so is a
silent breaking change. edtf carried an `msrv:` job as a **required**
status check before its import; it vanished on conformance and nobody
ruled that it should — the same class as #813, a check that stopped
existing rather than one that went red. `lint:msrv` replaces it in the
belt, applicability-guarded on a tracked `Cargo.toml` that declares a
minimum and skipping clean otherwise.

*The repository pins its MSRV toolchain; the gate does not fetch one.*
`cargo msrv verify` is the tool built for this and is refused on the
gate's one hard law — it resolves the declared toolchain through rustup
at run time, and nothing network-dependent belongs in `ci`. So the MSRV
toolchain is a second entry in the repo's `rust` list, carried by
`mise.lock` like every other input, and `lint:msrv` compiles at it with
`RUSTUP_TOOLCHAIN`. That works because mise's rust backend installs
*through* rustup — measured: `mise install rust@1.82.0` puts
`1.82.0-<host>` in `~/.rustup/toolchains`, and `RUSTUP_TOOLCHAIN=1.82.0
rustc --version` reports 1.82.0 — the same mechanism `audit:fuzz` uses to
reach a repo's dated nightly.

*Which makes the version a fact in two files, so the two are compared
rather than either trusted.* `Cargo.toml` stays the source: the promise
is read from `cargo metadata`, which resolves `rust-version.workspace =
true` inheritance, and a declared minimum with no pin of exactly that
version is red with a remedy naming both files. The failure this
prevents is not "no check" — it is a **green check at the wrong
version**, which bumping the pin alone would otherwise buy. Matching is
patch-exact on the zero-padded form: `rust-version = "1.82"` promises
that 1.82.0 builds, and a green run at 1.82.1 answers a question nobody
asked. That is not pedantry about a hypothetical, either — mise publishes
rust `1.82.0` and no bare `1.82`, so the org's declared form and the
org's pinnable form differ by exactly this component.

*Measured on the two repos that declare a minimum, before this landed.*
edtf declares 1.82 and pinned no toolchain at it: red, with both files
named. With `{ version = "1.82.0" }` added to its `rust` list its five
gate-compiled members build clean at 1.82.0 — the promise was true, and
had simply gone unverified since the import. Planting `u64::midpoint`
(stabilised 1.85) in `edtf-core` then reds the same run at E0658 while
the stable pin compiles it without complaint, which is the whole shape
of the defect in one diff. release-lab needs no second toolchain at all
— its minimum *is* its build pin — but it exposed a defect of its own on
the first run: its `[workspace.package] rust-version` is inherited by no
member, so cargo reports `null` for all four and the declaration reaches
neither a consumer nor clippy's `incompatible_msrv`, contradicting the
comment written beside it. An inert declaration is invisible to
everything except a check that reads cargo's account rather than the
file's text.

*The compile surface is the gate's, stated once.* `CLIPPY_EXCLUDE` and
`CLIPPY_FEATURES` are read here rather than given MSRV-flavoured twins,
so a repo declares what the gate can compile in one place. The names say
clippy and the meaning is wider — a wart kept on purpose, because the
alternative is a second list that drifts. A member excluded there while
declaring a minimum is therefore **unverified**, and the task prints it
as such on every run rather than leaving the hole silent; #813 is what
removes those exclusions, and this check widens with them.

**The gate compiles what the publish path builds** (#813). edtf's v1.3.0
publish failed all ten pgrx jobs on a parse error, and the gate had been
green: it had never compiled that crate. A task existed and could not
matter — `pg:lint` sat outside the `lint:*` wildcard, so `ci` never
collected it, and it exited 101 on an unmodified tree, so it had never
once passed. #687's class exactly: not a test that was skipped, a test
that could not fail. release-lab carried the identical blind spot and had
simply not published into it yet.

*What replaced it.* `lint:pg-clippy` lints the extension at the org's
level, `test:pgrx` runs its suite at every major the crate declares, and
`lint:gate-surface` refuses any tree where a crate the publish path
builds is held out of the gate. All three are belt tasks, because a
repo-local task is what failed: the fix for a check nothing collected is
not a better-written repo task but a task the wildcard takes, in a file no
repository edits.

*Nothing is declared that can be derived.* A member is a pgrx extension
when it depends on `pgrx` **and** carries `pgNN` features; its majors are
those features. So `CLIPPY_EXCLUDE = "edtf-postgres"` — the line that hid
the crate for forty releases — is deleted rather than corrected, in both
repositories, and `mise/gate_surface.py` is the single reader that
`lint:rust`, `lint:msrv`, `lint:pg-clippy`, `test:pgrx` and `fix:rust` all
consume. Four copies of that answer would have drifted; the exclusion was
the first copy to.

*Postgres is pinned by the repository and everything else is the belt's.*
Belt tools install in every repository in the org, so five majors in
`mise/config.toml` would cost stele, monumental-archive, iiif-server and
the canon ~200M each for a Postgres they never use — and the org's own
rule already settles it: the belt carries what the belt needs to run,
repositories carry what they build with. So the repository pins
`aqua:theseus-rs/postgresql-binaries` once per major, and the belt owns
the task, the derivation and the agreement check that every declared major
has a pin. The full provisioning measurement — both platforms, both
mechanisms, what each costs — is recorded on #813; the short version is
that the aqua package installs in seconds with a checksum, carries
`include/server`, needs no apt or brew package on either platform, and
leaves the mise install directory untouched after a full `cargo pgrx
test`.

*The one correction the belt supplies.* The prebuilt macOS `pg_config`
reports its BUILD machine's flags — an `-isysroot` into an Xcode that does
not exist here and an `-I` into a Homebrew that does not either — and
pgrx hands them to clang, so bindgen dies on `inttypes.h`. `pgrx-env.sh`
prepends the machine's real SDK to both `BINDGEN_EXTRA_CLANG_ARGS` and
`CFLAGS`; both are needed, because the cshim is compiled by cc-rs, which
never reads a bindgen variable. Linux needs no correction.

*And the second, which arrived with pgrx 0.19.3.* The same `pg_config`
records `-I/opt/homebrew/opt/icu4c@78/include`: the prebuilt Postgres is
configured `--with-icu`, and it ships ICU's dylibs and none of its
headers. Through 0.19.2 that was invisible, because pgrx bound a chosen
subset of Postgres's headers and none reached ICU. 0.19.3 binds all of
them, `pg_locale.h` includes `unicode/ucol.h`, and on a Mac without
Homebrew's icu4c the build dies in bindgen — measured on release-lab at
pg14 and pg18 alike, in `test:pgrx`, `lint:pg-clippy` and
`coverage:check` together. The runner is unaffected: `libicu-dev` is in
the image. A gate that cannot run locally is the promise in CLAUDE.md
broken for one class of crate, so the headers are supplied rather than
the gap excused.

They are pinned by the repository, beside its Postgres and by the same
rule: `github:unicode-org/icu`, the project's own source archive
(`asset_pattern = "icu4c-*-sources.tgz"`), restricted with `os =
["macos"]` so the runner never downloads what it already has. Not aqua —
ICU publishes no macOS binary and no attestation, only a source archive
with a detached GPG signature mise does not check, so the lockfile's
checksum is trust-on-first-use, the cargo-semver-checks precedent and
stated as such. It costs 28M to fetch and 121M on disk, on a Mac, in a
repository that builds an extension; in the belt it would cost every
repository that. Nothing is compiled: bindgen and the cshim read two
header directories, and the extension links against nothing of ICU's —
the server that loads it carries its own.

Which ICU is derived, not declared. `pgrx-env.sh` reads the major out of
each pinned Postgres's own `pg_config --cppflags` and reds if the pin is
missing, is a different major, or is not the source archive; headers
from another major would bind declarations the shipped dylibs do not
have, and nothing downstream would say so. Only ACTIVE mise entries
count, on both sides — a copy installed by hand is not a pin, and the
first draft of this check passed on exactly that. The preset disables
major updates for the package for the same reason: an ICU major moves
when a Postgres build moves, by hand, never because a newer one exists.
Measured on release-lab at pgrx 0.19.3: `cargo pgrx test` green at pg14
and pg18 with the headers supplied, red without.

*A trap worth the line it costs.* These tasks pass records to bash `read`
loops with `IFS` set to tab, and **tab is a whitespace separator, so bash
collapses a run of them**: an empty field does not arrive empty, it
vanishes and every field after it shifts left. Measured here as a plain
crate whose empty major list made its directory arrive as its majors,
which then handed cargo a package spec as a positional argument. Every
record field is therefore non-empty by construction (`-`, `default`), and
a table test asserts it so the placeholders are not tidied away later.

**Config delivery: the belt hands its own configs to its own tools**
(#445). A config only a belt tool reads lives in `mise/` and is passed to
the tool by the task — `CLIPPY_CONF_DIR`, `--config-path`, `--config` —
rather than copied into every repo. Copies were the older shape, and
`MAINTENANCE.md` describes their weakness exactly: the scaffold is
"copied once, never consumed live", so a scaffold improvement reaches no
existing repo and nothing compares a repo's copy to it. Measured rather
than feared: release-lab was missing EIGHT scaffold files for five
releases, found only by running a newer belt against it. The fix is
deletion, not detection — a repo with no copy has nothing to drift, and
no check needed writing.

What made it possible: mise has no template for a global config's own
directory (`{{config_root}}` is the home directory for one), so tasks had
begun carrying private resolution blocks — six of them. `ORG_BELT_DIR` is
computed once in the belt's `[env]` via `exec()`, covering both carriers
(CI's `MISE_GLOBAL_CONFIG_FILE`, the local `conf.d` symlink) in one
expression, identifying the belt by a file only the belt has so a
differently-named symlink still resolves. 28ms per invocation.

**taplo was the last belt linter with no delivered config, and TOML width
was per-repo by accident** (#691, 2026-08-21). `lint:toml` passed no
`--config` at all, so taplo auto-discovered a repo-local `taplo.toml` and
the org's TOML format was whatever each repo happened to say — edtf at
`column_width = 120`, this repo at taplo's default 80, neither written
down. Not the vacuous-green trap (taplo's defaults are a real standard)
but the quieter one, where a verdict depends on a file nothing tells a
repo to carry, and where the org had no *stated* answer.

Delivered as `mise/taplo-org.toml`, and the width question was settled by
the org's own recorded rule rather than by the number in the issue. The
rule is rustfmt's — "a width the org invented is our opinion, not the
tool's" — so a tool's default stands unless a MECHANICAL constraint
derives another, the way yamllint's 130 comes from unbreakable pin lines.
No such constraint exists for TOML: `column_width` decides when arrays
expand and collapse, and it does not wrap strings. So 80, taplo's own
default, at which this repo's sixteen TOML files were already clean.

The "120-column org canon" both #691 and edtf's own `taplo.toml` cite
does not exist — measured against the delivered configs: 130
(`.editorconfig`, yaml and sh), 100 (rustfmt, its default, left alone),
80 (rumdl prose, its default). edtf cites "yamllint, markdownlint" for
its 120 and neither says 120. edtf reformats four files at its pin bump,
all of it `mise run fix:toml`.

`allowed_blank_lines = 1` is the one setting raised above taplo's
default, and it is the only knob here with a strictness axis rather than
a taste: the default of 2 lets an author choose one blank line or two,
and a format check accepting two spellings of one thing checks less than
one accepting a single spelling. Free for this repo, measured. The
options NOT taken are recorded in the file with their costs
(`indent_tables` 2 files here and 8 in edtf, `compact_inline_tables` 2
and 11, `align_entries` 11 and 14) — each rewrites the corpus to an
aesthetic nobody chose, which is a style, not a level. `reorder_keys`,
`reorder_arrays` and `reorder_inline_tables` are written out as false
because this file is where someone would go to turn them on, and the
category call against alphabetical reordering was already made for
yamllint's `key-ordering`.

Two mechanics decided the guards, and both are the shellcheck pair
from #696 in another costume. `--config` REPLACES a discovered config rather
than merging with it (measured: with the belt's file passed, a local
`array_trailing_comma = false` has no effect), so a repo-local
`taplo.toml` cannot lower the format — which is why it is refused rather
than tolerated: it could only be a file someone believes in that does
nothing. And an unreadable `--config` is a WARN at **exit 0**, taplo
formatting on at its own defaults, so the task checks the file before
running — without that check a belt that never arrived reports a green
gate at a level nobody chose (mutation-checked).

The delivered file is `taplo-org.toml`, not `taplo.toml`, for the reason
`biome-org.json` carries its suffix: the belt lives inside the canon, and
a delivered file under the discovered name is caught by the very refusal
it enforces. Measured, not predicted — with the natural name this repo's
own gate reported `tracked: mise/taplo.toml` and exited 1. The same
collision sat latent in `lint:rust`, which refuses `*/clippy.toml` and
`*/rustfmt.toml` while the belt delivers both under exactly those names;
it was invisible only because that task skips a repo with no tracked
`Cargo.toml`, and the canon tracks none.

**For clippy the rename road is closed, and closed in the fail-open
direction** (#746, 2026-08-24). `CLIPPY_CONF_DIR` names a *directory*,
and clippy reads `clippy.toml` or `.clippy.toml` inside it and nothing
else — measured three ways on release-lab with one deliberately invalid
key in the file: under `clippy.toml` and under `.clippy.toml` clippy
refuses to start, and under `clippy-org.toml` there is no error at all,
clippy lints on at its own defaults and exits 0. A `-org` rename here
would not fail to deliver loudly; it would deliver nothing quietly,
which is the exact trap the delivery rule exists to prevent. Renaming
`rustfmt.toml` alone would then put the two Rust configs on two
different roads to buy nothing, so the road is refused whole.

`lint:rust` instead excludes the two literal delivered paths when — and
only when — the tree tracks `security/identity.toml`, the canon's own
identity declaration (#579). The guard is identity, not path: a consumer
that tracks its own `mise/clippy.toml` is refused exactly as before, so
no shadow config hides behind the belt's directory layout. This is not
the `ORG_BELT_DIR` comparison #691 rejected, which assumed the belt
directory and the working tree name one checkout and went false-negative
on a second clone; an identity file assumes nothing about where the belt
arrived from.

The boundary needed a third clause, and #445 supplied it by measurement:
**a config may only be delivered when the tool treats it as data.** The
moment a tool derives its project root from the config's LOCATION,
handing it one from outside the tree silently changes what the tool
sees. biome does exactly that. Given `--config-path` into the belt
directory, its scanner looks for the project beside that copy, finds
none, and every project- or type-aware rule stops working while the
syntactic ones carry on — no error, no warning, a green gate. One
byte-identical config in two locations, measured on monumental-archive:
**69 findings never reported** — `noUnnecessaryConditions` 65,
`noUnresolvedImports` 2, `useImportExtensions` 1, `useArraySortCompare`
1 — and **73 false ones invented**, `noUndeclaredDependencies` reading
93 where the truth is 20, because it was hunting for `package.json` in
the canon. All 17 of biome's type-aware rules were dead the whole time.

So biome's config is now **generated at the repo root for the length of
one run** and removed by a trap: correct anchoring, no residue, and
nothing for a repo to `.gitignore`. Not a repo-root `biome.json` that
`extends` the belt's copy — that anchors correctly too, but a repo's own
config merges ON TOP and can switch an org rule off (proven), and a
config that exists only during the run has no repo-side surface to
lower. Since #695 the repo root may track a `biome.json` again, but it
carries the repo's DOMAINS and nothing else and is merged in by the
belt rather than read by biome — a nested or `.jsonc` one is still
refused outright, and that plus the generation is the entire
enforcement. The other eight delivered
configs were swept against the same test: ruff was the plausible
suspect — its isort first-party detection could have keyed on the config
directory — and measured identical from both locations; the rest carry
no paths at all.

The boundary, stated once: **who reads the file, whose content it is,
and whether the tool reads it as data.** Ten configs are delivered —
clippy, rustfmt, pinact, typos, ruff, biome (generated per run, per
above), yamllint, rumdl, sqlfluff, committed. Two stay repo-side for
content rather than readership: `deny.toml`'s skips describe one tree, and
`.golangci.yml` names the module path in gci's import prefix (plus
wrapcheck globs for that module's own packages in stele) — proven by
delivering it and watching stele's imports go red against a config
carrying another repo's identity. biome additionally forced a rename:
it walks the tree and treats any `biome.json` below the root as a nested
root config that hard-fails, so the belt's copy is `biome-org.json`, a
name its discovery ignores. Otherwise: An editor
(`.editorconfig`, and the language configs whose LSPs read them), GitHub
(`renovate.json`), or the repo itself (`deny.toml`'s skips, licences,
`REUSE.toml`) — those stay in the repo. Everything else is delivered.
*Reopen:* the language configs
(ruff, biome, golangci, sqlfluff, yamllint) are the live question — one
home versus in-editor linting on a fresh checkout.

**`committed.toml` is delivered, and the mixed-file case has an answer**
(#576). It was the boundary's hardest instance: the RULES are org
policy, but `allowed_scopes` is repo identity — the `.golangci.yml`
module-path class — and the two lived in one file. Five copies, four
distinct blobs, three still citing a `cliff.toml` retired in #507. Two
measurements decided the shape. `committed --config` REPLACES a
repo-local `committed.toml` rather than merging with it, so the typos
`-c` precedent does not carry over and the scope list cannot simply
stay beside the delivered file; and a partial `committed.toml` left in
a repo would silently become the WHOLE config for any bare `committed`,
which is a de-enforcement, not a compromise. So the belt COMPOSES:
`mise/committed.sh` concatenates the delivered rules with the scopes a
repo declares as `ORG_COMMIT_SCOPES` in its `mise.toml` `[env]` — the
`FUZZ_TOOLCHAIN` pattern, a repo-declared belt input — and passes the
result to committed. A token that is not a scope fails the task rather
than corrupting the TOML. The old objection, that the commit-msg hook
runs outside mise's env, was answered by routing the hook through mise
like every other lefthook job (`commits:check`).

One more thing the delivery bought: `lint:commits` and the
squash-subject step both guarded on `[[ -f committed.toml ]]` and
skipped clean without one. A repo that never copied the file answered
to no commit canon at all and nothing said so. Delivered, the guard has
nothing left to skip on.

**The commit ceiling is wrap-aware, and that is now written down**
(#576). committed's `subject_length` fails a subject only when
whitespace sits past the limit — an over-long UNWRAPPABLE tail is
exempt. Measured on 1.1.11: the last space at index 72 passes, at 73
fails, whatever the total length. That is why roughly forty subjects
over 72 columns are on main, the longest 86, while `chore(canon):
update dependency github:monumental-archive/stele to v0.16.0 (#574)`
at 81 could not merge. `hard_line_length` is the knob that makes 72
literal for everyone; it is deliberately unset (a human subject ending
in a URL has an excuse) and `lint:subject-budget` holds MACHINE-minted
subjects to the literal ceiling instead, where no such excuse exists.

**Duplicate-version skips now expire** (#445). cargo-deny is silent about
a `skip`/`skip-tree` entry that matches nothing, so an exception written
for an upstream lag outlives it by however long nobody looks — the
failure `#[expect]` exists to prevent. `lint:deny` removes each declared
skip in turn and re-runs; a variant that still passes proves its entry is
dead and the gate names it. `mise/deny_skips.py` emits the variants, by
line-based removal rather than a TOML round trip, because the comment on
a skip IS the decision and a serialiser drops it. It found a real one on
its first run: release-lab had a `pgrx` skip subsumed by `pgrx-tests`.

**shellcheck, retained despite the abandonment flag** (#290 finding 6).
Renovate's `abandonments:recommended` sweep flags shellcheck (last
release 2025-08-04) as past the abandonment threshold. Verdict:
retained, as a written exception rather than a silent one. shellcheck
is mature and feature-complete — a slow release cadence is its normal,
not its death — and it is the only engine actionlint can delegate
`run:` script analysis to, so dropping it would lower the belt's
enforcement to add none back. The exposure is also small: it parses
the org's own tracked scripts, offline, with no network surface.
*Reopen:* a shellcheck CVE, aqua dropping or freezing the package,
actionlint growing or switching script engines, or a maintained fork
becoming the community's canonical line.

**pinact, and the two gaps it exposed on the way in** (#82). CLAUDE.md
requires every `uses:` to carry "a full commit SHA with a trailing
version comment". Only the first half was enforced: zizmor's
`unpinned-uses` proves the SHA and is blind to what follows it, and
`lint:canon-pins` reads canon references only — so a third-party action
pinned to a bare SHA with no comment at all passed every check the org
had. The comment is what a human and Renovate read to judge whether a pin
is current, so its absence is a defect and a comment that *lies* is a
worse one.

Three legs, split by what each needs:

- `lint:action-pins` — `--fix=false --no-api`, so it asserts a
  40-character SHA and the presence of a comment while asking GitHub
  nothing. Deterministic and offline, therefore gate-eligible.
- `audit:action-pins` — `--verify-comment` resolves each SHA back to its
  tag to prove the comment is true, and `--min-age 7` carries the org's
  release-age floor to the one layer that lacked it (`minimum_release_age`
  governs mise tools, `UV_EXCLUDE_NEWER` Python dependencies, actions had
  nothing). Both need the API and both move with the clock, so both are
  Monday-cron work.
- `fix:actions` — the fixer nine other `fix:*` tasks had and action pins
  did not. `--update` is deliberately unwired: bumping pins is Renovate's
  job and a fixer that also upgraded would fight it.

Measured at standup, the belt's existing discipline is genuinely good:
across every workflow and composite action, **no comment lies about its
SHA and nothing is pinned to a release younger than seven days.** The
only finding is the signer.

**The signer is permanently unpublished, and that is correct.** Every
reference to `monumental-archive/signer` is SHA-pinned and commented
`# main`. The repo has zero tags and zero releases by design and will
keep having them, so there is no version for a comment to name: the SHA
is the whole identity and `# main` says only which line it came from.
`.pinact.yaml` carries a standing exemption for it, scoped as narrowly as
the tool allows: `VersionComment == ""`. pinact does not count `# main` as
a version comment — "main" is not a version, which is exactly why it
errors — so the condition exempts the one permanent situation (a signer
reference with no version to name) and nothing else. A signer reference
that ever carried a real version comment falls straight back under the
check, truthfulness included; verified by planting a false version
comment on one and watching the audit catch it.

Recorded because the first attempt got it wrong: the variable is
`VersionComment`, and guessing at `Comment` and `ActionVersionComment`
produced expression errors that were mistaken for "the comment is not
exposed at all". It is exposed, and it is documented in the project's
`docs/config.md` — which was reachable all along; a single 404 on the
docs *site* was taken for its absence.

**zizmor was never pointed at the composite actions.** It audits
`action.yml` files and flags unpinned `uses:` in them exactly as it does
workflows; the belt simply invoked it on `.github/workflows/` alone, so
`.github/actions/source-attest/action.yml` carried a live third-party
reference no check in the org could see. Widening the scope is one line —
and it surfaced a pre-existing `github-env` finding on the
`dirname "$(mise which cosign)" >> "${GITHUB_PATH}"` line. That is a
written exception now rather than a silent one: the audit is right in
general (a GITHUB_PATH write can shadow a later command) and wrong here,
because the value comes from mise's own locked install tree with no input
reaching it — zizmor rates its own confidence Low for that reason.
Removing the write means teaching `chain.sh` and `emit.sh` to resolve
cosign themselves, which is larger than the scope change that found it.

**zizmor runs at the auditor persona in the gate** (#1719). The gate
ran `pedantic` and the weekly audit `auditor`, though
`secrets-outside-env`, which only the auditor persona runs, reads
nothing but the workflow file. #1602's job drew an `audit`-environment
secret with no `environment:`, merged green, ran with an empty string,
and was found by the Monday audit. Measured on zizmor 1.32.0 at
`619d2bb`, `--offline --persona=auditor` over every action file: "No
findings to report (57 ignored)", the same count as `pedantic`, so no
ignore was added. The gate and `audit:actions` now share the persona
and differ only by the audits that need the API.

**A defect class this standup found in the belt itself, now closed.**
mise ran task bodies without `set -e`, so a task whose failure signal is
a tool's exit status silently passed if anything followed that tool.
`lint:python` ran `ruff check` then `ruff format --check`, so a lint
error was masked by the formatter passing — verified by introducing a
real violation and watching the gate stay green — and the pinact legs
had the same shape with a trailing `echo`.

The cure is **global**: `[task_config] shell = "bash -euo pipefail -c"`.
All 78 bodies inherit it and none carries a `set` line of its own.

This entry previously recorded the opposite — "the obvious one-line cure
does not work" and "the cure is per-task, not global" — and the
correction is worth keeping, because the measurement behind it was
right and only the conclusion drawn from it was wrong. The measurement
showed that flipping the shell reddens `audit:citations`. That is not
evidence the flag is wrong; it is the flag doing its job. Running the
whole set afterwards settled it:

| Task | old shell | strict | verdict |
| --- | --- | --- | --- |
| `audit:attestations` | red | red | pre-existing |
| `audit:actions` | red | red | pre-existing (GH_TOKEN unset locally) |
| `audit:template-pins` | red | red | pre-existing (pins behind canon) |
| `audit:citations` | green | red | the one real regression |

One task out of 78. A static scan had flagged 25 as candidates and
nearly all were already guarded or happened to hold on this tree, which
is exactly why the question was settled by running every task rather
than by reading them.

`audit:citations` collects with `xargs grep`, and **grep exits 1 for
"matched nothing"** — a legitimate answer, not an error. Both collectors
now end `|| true` with the reason beside them. Isolated rather than
guessed: the version collector matches 17 and exits 0, the run-URL
collector matches nothing and exits 1, because the docs cite no Actions
runs today.

The direction matters and is the general rule, not a detail of this
change. Strict-by-default with a named exception at each genuine site
beats a permissive default plus a preamble in every body: the second is
the same reading with a worse result — unsafe by default, opt-in, and no
way to prove every body was covered. Only 11 of 78 had remembered. A
rule you have to remember to apply is not a rule.

Worth recording how nearly the measurement was missed. A first
comparison run showed `audit:citations` failing under *both* shells,
which read as "pre-existing, not caused by the change", and the flip was
briefly treated as cleared. It was failing for an unrelated reason at
the time — a fake version string this very document had introduced as an
example, which `audit:citations` correctly flagged as a version cited in
docs and tagged nowhere. Fixing that unmasked the real result. A
confounded control looks exactly like a clean one, which is why the
settling run controlled every failure against the old shell
individually.

**jq, and the check for its whole class that was tried and abandoned**
(#82). jq is pinned at 1.8.2 because eleven belt tasks invoke it — nine
audit tasks parsing GitHub API responses, `fix:tracks`, and
`lint:bestpractices` in the gate — and it was never pinned at all. It sat
in the same position as `python3` and `curl`: a hard dependency of the
gate, satisfied by whatever the machine happened to have.

The interesting part is why nobody noticed. The belt's availability
canary looked like the check that would catch it, and was not: its tool
list was **hand-maintained**, so it proved the tools someone remembered
to list were present, and said nothing about the tools a task actually
invokes. jq was never missed by a check; there was no check. That canary
has since been deleted for exactly this, the entry below.

**The obvious fix does not work, and the attempt is recorded so it is not
retried blind.** A lint that parses task bodies, extracts invoked
binaries and asserts each is pinned or explicitly baseline was
prototyped against this repo. It produced roughly a hundred false
positives against a handful of real hits: bash keywords (`do`, `esac`,
`fi`), shell builtins, loop variables, and ordinary English from inside
`echo` strings are all indistinguishable from commands under a
first-word regex. Doing it correctly needs a real bash parser, not a
pattern. The class defect is therefore **open and known**, not closed —
which is the honest state, and better than a noisy check that would be
suppressed within a week.

One adjacent observation, recorded rather than acted on: 13 of the
belt's 72 jq invocations use `-e`. Without it jq exits 0 even when the
filter yields `null`, so a task reading a field that has moved gets an
empty value and a green line — the vacuous-success class the
audit-claims contract exists to forbid. Not blanket-fixable, since many
of those filters legitimately yield nothing and are checked by the
surrounding bash; it wants a per-site pass.

**`lint:belt-available` — deleted; the disposal is the record** (#658,
2026-08-21). The belt's availability canary is gone. It is written up
here on the revoke-in-place precedent (#619, #646) rather than vanishing
with its lines, because "the belt checks that its own tools are present"
is precisely the claim a stranger expects to find, and would re-propose
from scratch. Both halves died, for different reasons, both measured on
canon main at `511af95`:

- **The tool list was a hand restatement of a declaration that already
  existed.** The description promised that *every* belt tool resolves;
  the loop named **19** tools against a `[tools]` table declaring
  **34**. Nothing joined the two, so it drifted the way restatements
  drift. `ruff` happened to be on the list and was caught once (#603);
  the **15** that never were are `uv`, `lychee`, `python`, `stele`,
  `cosign`, `gitsign`, `hadolint`, `gitleaks`, `sqlfluff`, `yamllint`,
  `editorconfig-checker`, `typescript-go`, `cargo-llvm-cov`,
  `cargo-machete` and `cargo-semver-checks`.
- **The layer check's premise had been eliminated at the root.** Its
  comment said an absent belt makes every guarded lint task "skip
  clean" so the gate goes green having checked nothing. That was true
  at #445 and is not true now. The belt holds **42** `command -v`
  guards and every one of them ends in `exit 1` with a pointed message
  — the 42nd being this canary's own loop. `command -v` is the belt's
  only tool-presence idiom: no `which`, `type -p`, `hash` or `[[ -x ]]`
  probe appears anywhere in a task body. And every `exit 0` skip in the
  belt is keyed on FILES — no `go.mod`, no `Cargo.toml`, no tracked
  shell — which is *applicability*, a different axis. So: **no belt
  task skips on a missing tool.** A missing tool either meets an
  explicit `exit 1` or dies as `command not found` under
  `set -euo pipefail`.

An absent belt is not quiet either, because `ci` is itself belt-defined.
In a consumer tree with `MISE_GLOBAL_CONFIG_FILE=/dev/null` and no
`conf.d` carrier, `mise run ci` exits 1 with `mise ERROR unknown
command: ci` (mise 2026.8.3) — there is no gate left to go green. The
canon's own checkout cannot reach that state at all, because mise loads
`mise/config.toml` from a project root as a local config: the same
filename fact the belt is named for.

**The one real loss, recorded so nobody rediscovers it as a surprise.**
The canary's message was friendlier than the failures that replace it —
it named both carriers and said which to fix. `unknown command: ci` is
loud and terser. The messages that used to defer to it by name
(`identity.sh`, `committed.sh`, `subject_budget.py` twice,
`audit:go-vulns`) now state both carriers in place, so the guidance
survives at the points a reader actually meets it.

**This is not a verdict against canaries as a class**, and the contrast
is the whole reason to record it. `audit:blast-radius` pins a
known-vulnerable canary package with the opposite epistemics: a positive
control that goes RED when detection silently breaks, where "a zero
population or a missed canary is CANNOT_JUDGE, never PASS". A canary
whose absence is a failure is sound. `lint:belt-available` went GREEN
when nothing had been checked — the #82 defect it was itself the
evidence for, since shellcheck sat on its list, present and green, while
no task ran it. Same word, opposite direction; leave the good one alone.

*Reopen:* a belt task found to *skip* rather than fail on a missing
tool. That would restore the failure mode the canary existed for, and
reopens the delete-or-derive fork #658 closed on the measurement above.

**uv, adjudicated late — it entered as an implementation detail and was
never stood up** (#82). uv arrived inside the `reuse` adoption as "the
`pipx:` backend rides aqua-backed uv" and got no verdict of its own,
which is how the belt ended up with a supply-chain policy that stopped at
the language boundary without anyone deciding it should. The tool itself
is not in question — aqua-backed, checksummed, attested, and the reason
no `pipx` binary is pinned. What needed deciding is the property of the
path it carries. Measured, not assumed:

- **Hash verification is impossible on this path, and cannot be
  configured in.** `uv tool install` has no `--require-hashes`; the flag
  exists only on the `uv pip` layer, which mise does not use. Hashes
  supplied through `--constraints` **and** through `--with-requirements`
  are both ignored — each was tested with a deliberately wrong SHA256 and
  each installed happily, exit 0. So `pipx:` entries record a version and
  nothing else, and that is a property of the tool, not an oversight in
  the config. *Reopen:* `uv tool install` gaining hash enforcement, or an
  aqua package appearing for a pipx-only tool.
- **A repo-level `uv.toml` cannot help.** uv states it plainly: for
  `tool` commands, which operate at the user level, local configuration
  files are ignored and only user-level config is read. So the canon
  cannot ship a `uv.toml` and have it apply; the only repo-controlled
  routes are the command line and the environment.
- **The floor did not reach Python, and now does.** `minimum_release_age
  = "168h"` governs how mise resolves a tool's own version; it does not
  reach inside the install, where uv resolves the package's entire
  transitive closure. Standing up reuse pulled six further packages with
  no floor and no checksum. `UV_EXCLUDE_NEWER = "7 days"` in the belt's
  `[env]` closes it, verified two ways: mise's `[env]` demonstrably
  reaches the install subprocess (an absurd cutoff set there made the
  install fail), and the value demonstrably bites (at `1825 days`
  resolution drops to a reuse old enough that its build fails; at
  `7 days` it resolves 6.2.0 unchanged). uv accepts a **relative
  duration**, so it tracks with the clock and never needs bumping — which
  is what makes this workable where a fixed date would have silently
  frozen resolution.
- **`UV_INDEX_STRATEGY = "first-index"` is pinned although it is already
  the default**, on the `ruby.compile` precedent: a default flip would
  arrive unattended through an automerged bump, and this default is the
  one standing between a `pipx:` install and dependency confusion.

**And the interpreter it was hiding.** uv does not only install packages;
when the system Python is unsuitable it downloads one. Measured: this
maintainer's Mac built reuse on a uv-fetched CPython 3.12.13 (macOS ships
3.9.6, too old), while CI built the same reuse on whatever the runner
shipped — two interpreters for one tool, neither pinned, neither in
`mise.lock`, neither in any verdict. "No Python on the belt" was never
true; there was simply no record of the Python that was there.

`python = "3.14.6"` in `[tools]` plus `UV_NO_MANAGED_PYTHON` and
`UV_PYTHON_DOWNLOADS = "never"` in `[env]` put it under the same two
tools as everything else: mise pins and locks it, Renovate bumps it.
Verified by inspecting the tool venv's `pyvenv.cfg` before and after —
`home` moves from uv's own tree to the pinned install. The earlier
objection that this was "a whole runtime for one script" rested on the
belief that the belt had no interpreter, which was false: the runtime was
already being fetched, invisibly. Precompiled, so a cold install is about
four seconds rather than the minutes a source build would cost — and the
lock coverage turned out to be **full**, not the reduced `core:` tier
mise's own backend table implies: per-platform checksum, URL and
`provenance = "github-attestations"`, the same entry an aqua tool gets.
The interpreter is now the best-attested thing in this whole path.

One consequence worth stating because it looks like a coincidence: with
the interpreter pinned, `python3` inside a belt task now resolves to it
on every machine, which turned ruff's `target-version` from a defensive
floor into a fact. It rose from `py39` to `py314` at zero finding cost,
and `lint:python-target` now fails the gate if the config ever claims a
newer Python than the pin — the one direction that produces code the
runner cannot execute.

Defaults left alone, having been checked rather than assumed:
`keyring-provider` is already `disabled`, `no-index` false with
`allow-insecure-host` empty, and `resolution = "highest"` stays — the
conservative-looking `lowest` would pin the closure to ancient releases,
which is worse. `no-binary` is refused outright: building from source is
a larger attack surface, not a smaller one.

The timing matters. When this was written the belt carried one `pipx:`
tool; **yamllint (now landed, #403) and sqlfluff are both pipx-only and
both already agreed**, so this path was about to carry three dependency
closures instead of one — which is exactly what it now does.

**ruff at `select = ["ALL"]`, and the file it was adopted for** (#82).
One aqua-backed Rust binary that lints *and* formats Python and needs
no Python interpreter to do it — so the org gains Python coverage
without Python joining the belt. It was adopted against a real hole,
not a hypothetical one: `security/workflow-permissions.py` computed the
caller/callee permissions join that guards the Build L3 boundary, and
it was the one tracked file in a language no belt tool covered. Nothing
checked it.

That file is gone — stele#148 replaced the join with `stele assert
permissions`, and the adoption reason went with it. Re-adjudicated
2026-08-20 and **kept**: the belt's own helpers are Python
(`mise/deny_skips.py`, `mise/embedded_shell.py`), so the language a
belt tool must cover is still in the tree, and a universal layer that
dropped Python because the canon's own count fell from three files to
two would have to re-adopt it for the first adopter who writes one.

`ALL` in ruff means every *stable* rule; preview rules are excluded from
it by design, so `preview = true` is what makes ALL mean all — the same
call as biome's nursery, and mitigated the same way. Ruff is pre-1.0 and
its **minor versions may change lint results deliberately** (rules
promoted to stable, behaviour changed), which the pinned-plus-Renovate
path turns into a red bump PR.

The measurement that justifies `lint:python` demanding a tracked config:
the same script yields **48 findings under `ALL` and zero under ruff's
defaults** (E4/E7/E9/F). A repo that forgets `ruff.toml` does not get a
weaker gate — it gets a green one that looked at almost nothing. Unlike
biome, a nested ruff config is harmless: ruff's config
discovery is hierarchical by design, so it needs no `.stub` rename.

Three settings are not preferences:

- **The formatter-incompatible ignore list** (`W191`, `E111`, `E114`,
  `E117`, `D206`, `D300`, `Q000`–`Q004`, `COM812`, `COM819`, `ISC002`) is
  published by ruff itself; those rules demand output `ruff format` will
  not produce, and the formatter warns on every run while they are
  enabled. Two halves of one tool contradicting each other, not taste.
  `D203`/`D213` join them for a narrower reason: ruff *already* disables
  each as one half of a mutually-exclusive pair, so naming them changes
  no enforcement and only silences two warnings printed on every gate run
  forever.
- **`CPY001` is off**, and the reasoning previously on record for the
  wider policy was weak. "A header travels wrongly when the file is
  copied" is half-backwards — this repo is uniformly 0BSD and a 0BSD
  header would stay true anywhere. The real argument is that `lint:reuse`
  already proves REUSE-spec compliance in the gate, so a per-file header
  is a second copy of a machine-verified fact: nothing gained, drift to
  lose, and drift is exactly how an MIT header reached a 0BSD tree
  (#316). The scaffold sharpens it — those files are copied into repos
  deliberately licensed differently.
- **`target-version = "py39"` is a fact, not an aspiration.** The belt
  does not pin Python, so the script runs on whatever `python3` a machine
  has, and this repo's own maintainer machine resolves macOS's 3.9.6.
  Naming a higher target would let the pyupgrade rules rewrite the script
  into syntax that machine cannot execute. Measured at standup: findings
  are identical from py39 to py314 with zero UP diagnostics at any of
  them, so the conservative floor costs nothing today and is insurance
  against the first rewrite that would.

**`T201` is off** — `print` is the belt's Python CLI's output mechanism,
not a debugging leftover, and the rule exists to catch strays in library
code. **`EXE001` was fixed rather than ignored**: the script carried a
shebang with mode 644. `lint:exec-bits` exists because a missing
executable bit burned `.github@v1.5.0` with exit 126 after an immutable
tag — but it covers shell only, and ruff extended that hard-won rule to
Python and immediately found the same defect.

The remaining ~28 findings were fixed, not silenced, and the refactor was
verified behaviourally rather than by inspection: the pre-change
`requirements` and `check` outputs were captured and diffed against the
post-change ones, byte-identical both times. That caught one real bug the
refactor introduced — collapsing the workflow-level grant with `or`
silently discarded an explicit `permissions: {}`, because an empty dict
is falsy and that empty grant is the meaningful org-wide default.

**golangci-lint at `default: all`, and the disables that are not**
(#392). The Go layer, stood up docs-first for the stele standup: the
full 105-linter catalogue was read from the pinned binary itself
(`help linters`, v2.12.2) and the v2 reference config, and every
departure from `all` carries a written reason in
`scaffold/.golangci.yml` — the reasons live beside the keys they
govern, deliberately, so a copied config carries its own record. The
disables fall in exactly three classes, none of them reach: one winner
per rule family (five complexity linters → gocognit; misspell → the
belt's typos; nakedret vacuous under nonamedreturns), rules wrong for
this org permanently (per-file headers → the reuse verdict; forbidigo →
the ruff `T201` shape; exhaustruct → the decode layer distinguishes
absent from zero by omission; tagliatelle → tag names are spec facts),
and blank-line style with no defect story (wsl, nlreturn). Everything
else is on, including every library-specific linter — one that finds no
library finds nothing, and costs nothing.

Three settings are not preferences: `nolintlint` requires every
`//nolint` to name its linter and carry a reason — the disabled-rule
law at line granularity; `depguard` bans `encoding/json` outside
`internal/jsonx`, because a decoder that turns absent into zero is
hostile in a tool that asserts facts are present and no linter can see
it (a data property, made a lint the id-token way); and
`max-issues-per-linter`/`max-same-issues` are 0 — a capped report reads
as complete when it is not.

Like biome, **golangci-lint cannot be proven in this repository**: the
canon tracks no Go, so `lint:go` skips clean here by construction and
the rule set is exercised first in stele. Recorded rather than glossed,
the biome precedent exactly. `lint:go` requires a tracked
`.golangci.yml` once Go is tracked — golangci-lint's default selection
is five linters of ~105, the ruff-defaults trap re-measured in a new
tool.

**govulncheck, and the third backend exception** (#392). No upstream
binary releases and no aqua package exist, so the `go:` backend builds
it from a pinned source version with the repo's own pinned toolchain —
the documented exception class `cargo:` and `pipx:` established,
repo-side like cargo-fuzz because building it needs the toolchain only
Go repos pin. It earns the slot over feed-matching alternatives by
being call-graph aware: it reds only when a vulnerable function is
REACHABLE from the module's code, which is the precision the VEX triage
wants as input. Network-bound against <https://vuln.go.dev>, so
`audit:go-vulns`, never the gate; osv-scanner still reads go modules in
`audit:blast-radius`, and the two answer different questions (shipped
SBOMs vs source reachability). Since #615 the triage is not only its
input but also its exit: the task parses `-json` and clears a reachable
finding only against a written decision in `security/vex/`, because in
JSON mode govulncheck exits 0 whether or not it found anything
(measured, v1.7.0) and an unfixable advisory otherwise burns every
release. *Reopen:* upstream shipping attested binaries, or an aqua
package appearing.

**biome at every rule it has, and the two that are not** (#82, corrected
by #445). Adopted as the org's JS/TS/JSON layer: one aqua-backed binary,
checksums and GitHub attestations on all seven lock platforms, rules
compiled in. Pinned to the explicit `aqua:` backend rather than
the `biome` short name, which resolves to aqua **and** npm — an npm
resolution records version only in the lock, no checksum.

**This entry claimed "all 525 rules across all eight groups are on,
nursery included" for three days, and all three claims were false.**
Measured against the pinned 2.5.7 rather than read: there are **514**
rules, `preset: "all"` enables **427** of them, and of those 427 only
304 could fail anything. Three defects, one root cause — the canon has
no JavaScript, so its own gate can never contradict a claim about biome.

- **`preset: "all"` does not include nursery.** 87 rules, off. Biome's
  documentation says nursery rules "require explicit opt-in via
  configuration on stable versions" and never says the preset excludes
  them; reading it as "all means all" was not unreasonable, it was
  unverified. There is no bulk switch — group-level `"nursery":
  {"preset": "all"}` does nothing either. Naming each rule is the only
  mechanism, so `mise/biome-org.json` names all 87. That makes it an
  enable-list, fail-open in one direction only: biome hard-errors on an
  unknown key, so a graduated or renamed rule reddens the gate, while an
  ADDED rule is silent — `audit:biome-rules` is that alarm.
- **`--error-on-warnings` is not a severity floor.** 123 of the 427
  enabled rules default to `info`, and an info diagnostic prints and
  exits 0. Proven against the live config: a file whose only fault is a
  magic number reports `noMagicNumbers` and passes green; on
  monumental-archive it is 1,442 findings that could never fail
  anything. `lint:biome` now reads the JSON reporter's summary and fails
  on any diagnostic at any severity — a floor that needs no rule list,
  and which fails closed if that (experimental) reporter changes shape.
- **A config delivered by `--config-path` from the belt directory breaks
  every project-aware rule.** See the delivery entry below: biome takes
  the directory its config sits in as the project root.

Nursery is outside semver by Biome's own statement, but a pinned version
bumped through Renovate turns that into a red bump PR, never a surprise
in `main`.

Two deliberate departures, both settled against evidence rather than
taste:

- **`assist/source/useSortedKeys` is off**, and since #695 so are the six
  other alphabetisers beside it, each named rather than inherited. It
  does not fire at Biome's own default assist level — `preset: "all"` is
  what turns it on — and every peer tool defaults the same way: ESLint's
  `sort-keys` is off and frozen, Prettier preserves authored order and
  needs a third-party plugin to sort. The ecosystem's convergent answer
  for hand-authored config is `sort-package-json`'s: keep the
  conventional lead keys, sort the bulk underneath. Alphabetising
  top-to-bottom here put `name` last in the workflow-template metadata
  and buried `extends` — the line that says what a Renovate config
  inherits — under a twenty-line `customManagers` block. The fix is safe
  and automatic, so this is not a migration-cost objection; it is that
  four independent tools ship the behaviour off. *Reopen:* a repo whose
  JSON is generated rather than authored, where stable key placement
  beats reading order.
- **`indentStyle` is space/2, not Biome's tab default.** Tabs cannot be
  uniform here: YAML forbids tab indentation by spec, which is 49 of the
  canon's 153 tracked files. Space is the only setting that can hold
  across every format, and `lint:shell` already overrides shfmt's own
  tab default to 2-space for the same reason. This was practice plus
  two tool settings until #403 item 2: the written convention now
  lives in `.editorconfig`, enforced by `lint:editorconfig`.

One trap, found by running the standup rather than reading about it: the
belt copy is `mise/biome-org.json` — a name biome's own
discovery ignores, since it walks the tree regardless of the path it is
given and hard-fails on a second `biome.json` below the root (#455).
Biome discovers configuration by walking the tree independently of the
paths it is handed, so a second `biome.json` below the root is a *nested
root configuration* and every invocation hard-fails — including ones
that never touch `scaffold/`. Identical in shape to the REUSE stub that
"parsed as a real nested REUSE.toml" in #316, and the same `.stub`
suffix is the same cure.

**biome cannot be proven in this repository**, and that is a first. Every
other belt tool is verified here before anywhere else; the canon has 22
JSON files and no JavaScript, so `preset: "all"` finds **zero** lint
violations here by construction — the run that adopted it exercised the
formatter and one assist action, nothing more. Its linter is exercised
first in a consumer. Recorded rather than glossed: a green canon gate is
not evidence about biome's rule set.

That sentence was written at adoption and then not acted on. Three
defects rode in behind it and survived three days of green gates, and
none of them needed a consumer to find — a probe file and a run of the
tool would have caught all three the same afternoon. The rule the
episode leaves behind: **for a tool the canon cannot exercise, a claim
about its rule set is not recorded until it has been run against code
that violates it.** Not a fixture committed to the tree — a probe,
thrown away, whose result is what the doc then states.

**The domains trigger fired, and declaring what you are did nothing**
(#695). The scaffold had written the condition down in advance: the
first repo in the org to track a framework moves `biome.json` back to
`scaffold/`. monumental-archive is that repo — React in `apps/web`, a
Hono server in `apps/door` — and it arrived with ten
`useQwikValidLexicalScope` findings, a Qwik rule judging a React
component, plus 33 `noNodejsModules` on a service whose entire job is to
be a Node service.

What the issue assumed, and what measurement says, are different things,
and the mechanism turns on the difference. Measured on that tree against
2.5.7, one variable at a time:

| `linter.domains` | diagnostics | Qwik findings |
|---|---|---|
| absent (the shipped state) | 2164 | 10 |
| `{react: all, project: all, types: all, test: all}` | **2164** | **10** |
| the above plus `qwik: "none"` | 2154 | 0 |

Declaring what the repo IS changes nothing, because `preset: "all"` has
already turned every domain on. **Only the negative has an effect** —
and a repository writing "none" is a repository switching an org rule
off, which is the thing this whole layer exists to prevent (#694).

So the two halves are split by who can honestly state them. The repo
writes `biome.json` carrying `linter.domains` and nothing else, values
`"all"` and nothing else, over the eleven domains that are identity;
`mise/biome_config.py` merges it with the org's rules and writes every
`"none"` itself. A repo cannot write `"none"`, cannot name `project`,
`types` or `test`, cannot carry a rule or an `overrides` block, and
cannot stay silent about a framework its own package.json declares —
that last one is the hole this mechanism would otherwise open, since
silence would turn react's rules off for a React repo.

Which domains are identity is biome's metadata, not a judgement: each of
the eleven is auto-enabled by a dependency, and `project` and `types`
have no dependency trigger at all. `test` does and is still org-fixed,
for the one reason worth arguing: a repo testing under `node:test`
declares no runner package, and making `test` claimable would hand it a
way to have no test rules and look conformant. `mise/biome-domains.tsv`
records the table; `lint:biome-domains` re-derives all fourteen rows
from `biome explain` in the canon's own gate, and `audit:biome-rules`
holds the domain LIST against the published schema, because a
fifteenth domain would sit outside the table and stay on everywhere.

The generated config is what biome is run against, so the declaration
never reaches the tool even if it lies: proven by planting a `biome.json`
that claimed `qwik` and switched `noNodejsModules` off, and measuring no
change to either (0 Qwik findings, 33 `noNodejsModules`).

**Two "safe" fixes were not safe, and one of them deleted tests**
(#758). `fix:biome` promises a fixer runnable without reading its diff
first. On monumental-archive it made two edits that broke the code.

`nursery/useConsistentTestIt` rewrote `test.prop(...)` to `it.prop(...)`
and left `import { test } from "@fast-check/vitest"` untouched, so the
module threw on load: **22 files / 337 tests became 21 / 335**, and
nothing in the output said a fixer had removed coverage. Swapping the
rule's preferred direction does not help — measured, with
`options.function: "test"` an `it` imported by name from vitest is
renamed to `test(...)` and its import is left behind identically. The
defect is renaming a call whose callee is an **imported binding**, in
either direction, so the rule's FIX is off and its diagnostic stays on.

**The exclusion carries an expiry**, which is the difference between an
exception and scar tissue: reported upstream as
[biomejs/biome#11453](https://github.com/biomejs/biome/issues/11453),
after searching for an existing report and confirming the symmetric
reproduction on the pinned 2.5.7. When biome reclassifies the fix or
stops firing on an imported binding, the `fix` key comes out.

`correctness/useImportExtensions` rewrote `from "./App"` to
`from "./App.tsx"`, which `moduleResolution: bundler` without
`allowImportingTsExtensions` rejects (TS5097). This one is **configured
rather than demoted**, because the rule is right that a relative import
should carry an extension and only wrong about which.

`forceJsExtensions: true` was the first answer and covered two of the
four TypeScript source extensions (#868). It suggests `.js` whatever the
source is, which is right for `.ts` and `.tsx` and wrong for `.mts` and
`.cts`: TypeScript maps `.js`→`.ts`/`.tsx`, `.mjs`→`.mts` and
`.cjs`→`.cts`, so `./helper.js` cannot resolve `helper.mts`. The fixer
therefore wrote a specifier `tsc` refused with TS2307 **and biome's own
`noUnresolvedImports` refused too** — one tool contradicting itself, with
no repo-side edit that satisfies both. `extensionMappings` states all
four instead, so each extension maps to the one TypeScript emits.

Measured on biome 2.5.7 and TypeScript 7.0.2, every option setting
crossed with every project shape the org has and every source extension,
at the full dial set. What the fixer writes never depends on the shape:

| option | `.ts` | `.tsx` | `.mts` | `.cts` |
|---|---|---|---|---|
| shipped default | `.ts` | `.tsx` | `.mts` | `.cts` |
| `forceJsExtensions: true` | `.js` | `.js` | `.js` | `.js` |
| `extensionMappings` (org) | `.js` | `.js` | `.mjs` | `.cjs` |

| project | shipped default | `forceJsExtensions` | `extensionMappings` |
|---|---|---|---|
| bundler, no `allowImportingTsExtensions` | TS5097 ×4 | ok, ok, **TS2307**, **TS2307** | ok, ok, **ok**, ✳ |
| nodenext + `allowImportingTsExtensions` | clean ×4 | ok, ok, **TS2307**, **TS2307** | ok, ok, **ok**, ✳ |
| nodenext, plain — `scaffold/tsconfig.json` | TS5097 ×4 | ok, ok, **TS2307**, **TS2307** | ok, ok, **ok**, ✳ |

Two things to read off it. The shipped default was broken for the
scaffold's own default project, so this was never the per-repo accident
the first issue looked like. And in the `forceJsExtensions` column the
project shape never changes the answer while the source extension does —
the extension was the variable the first measurement did not vary, which
is how a three-shape table can be complete and still miss half the
population.

✳ `.cts` never reaches a resolution verdict: under the org's
`verbatimModuleSyntax` + `erasableSyntaxOnly` a `.cts` file can express
no module syntax at all — ESM `import`/`export` is TS1286/TS1287, and
`import x = require()` / `export =` is TS1294. The mapping is stated
because it is what is true, and it does remove TS2307 in all three
shapes; the two remaining walls are those dials working, each named by
the compiler, so the belt adds no ban of its own.

**The option is used outside its documented rationale on purpose.**
biome documents `forceJsExtensions` for "the `module: node16` setting
when building your code with `tsc`" and `extensionMappings` for bundling
to a package; neither mentions `moduleResolution: bundler`, and 2.5.7
ships **no bundler-aware option at all** — both keys choose which
extension is suggested, and neither can suppress the suggestion. Read
alone, that says no configuration serves the bundler case, which is what
the first lane concluded and wrote down. The tables above are the
correction, and the rule they repeat for this tool: the docs are the
cross-check, the pinned binary decides — and a measurement is only as
wide as the inputs it varied.

Proven end to end on the fixture: after the change
the fixer leaves `test.prop` alone, rewrites `./App` to `./App.js`,
still touches 73 files, and the tree comes out with `TS5097: 0`, 22 test
files and 337 tests passing.

What this costs the belt is a smaller promise, stated where the promise
lives: `fix:biome` applies every fix biome calls safe **minus the ones
the org has measured breaking code**. A rule that starts miscategorising
tomorrow will still get through; the mitigation is that a fixer runs on
a branch and its diff reaches a gate, not that the tool is infallible.

**The preset had never met TypeScript that crosses a database
boundary** (#759, #767), and three of its four largest rules broke on
that one seam. monumental-archive is the first repo in the org where
column names and row types enter TypeScript as Postgres spells them —
901 of 1494 remaining diagnostics, none of them careless code.

| rule | sites | verdict |
|---|---|---|
| `complexity/useLiteralKeys` | 590 | **off** — contradicts the org's `noPropertyAccessFromIndexSignature` dial (#759) |
| `style/useNamingConvention` | 157 | **narrowed** — two property selectors admit the names a database or a container owns |
| `style/noTernary` | 164 | **off** — 123 sites are expression positions with no statement form |

`noTernary` is off because its remedy is mostly impossible:
`...(c ? { k: v } : {})` has no if/else form, since an object literal
admits expressions and not statements. The 41 convertible sites measured
a wash — `const x = c ? a : b` becomes `let` plus if/else, trading the
finding for a formatting finding and costing the `const`.

`useNamingConvention` stays **on** and is narrowed to the two selectors
that touch the boundary: `typeProperty` and `objectLiteralProperty`
admit `snake_case` and `CONSTANT_CASE` beside `camelCase`. The org
already ruled this one tool over — sqlfluff's `RF04` flags
`public`/`method`/`label`, and #672 concluded that renaming a column is
a migration and not a lint fix — so the same column seen from
TypeScript cannot become a naming defect. A name the **environment**
owns is the same class of fact (a container reads `POSTGRES_DB` by that
exact key) and lands on the same selectors. The widening holds to the
selector, never to the case style at large, and that is planted rather
than asserted: on one file carrying `merged_ark`, `POSTGRES_DB`,
`bad_function_name`, `bad_variable_name`, `bad_class_name` and
`bad_type_name`, the two property names pass and the other four are
still reported.

Two corrections to the issue, both from re-deriving rather than
re-reading. It is **157** findings, not 147, and the split is the point:
147 are properties and the other **10 are function-scoped
`CONSTANT_CASE` consts in that repo's own tests**, owned by nothing
outside TypeScript and still enforced. After the narrowing **11**
remain — those 10, plus one PascalCase `TextEncoder` in a type literal.
One per-site `biome-ignore` is what per-site suppression is for; 147 of
them would have been the waiver #694 refused.

**In a test the literal is the specification** (#783), so two rules are
off for test files and nowhere else. This is the first `overrides`
entry the org delivers, and it is applicability declared once and owned
by the org — the #718 shape — rather than a suppression at every site,
which #694 refused.

| rule | sites | after the override |
|---|---|---|
| `style/noMagicNumbers` | 40 | **2** — both genuine, in production |
| `performance/useTopLevelRegex` | 19 | **0** — 19/19 were in tests |
| `complexity/noExcessiveLinesPerFunction` | 40 in tests, 27 of them `describe` callbacks | **0 in describes** — 33/60 groups exceeded 50 lines at 4.7 cases each (#804) |

`expect(x).toBe(3)` and `rejects.toThrow(/append-only/u)` are the thing
the test asserts; `expect(x).toBe(EXPECTED_ACT_COUNT)` hides it behind a
constant. For the regex the rule's own payoff is absent as well — not
recompiling a literal in a hot loop is worth nothing for a regex
evaluated once — and 16 of the 19 are `toThrow(/…/u)` and kin, the error
contract being asserted.

**One correction to the issue**, from re-deriving rather than
re-reading: it is **38 of the 40**, not 40 of 40. The other two are real
production magic numbers in `packages/ark/src/index.ts` — a
rejection-sampling bound (`if (b < 232)`) and a prefix width
(`s.slice(4)`) — and they still red, which is the rule working. They are
that repository's two-line fix, not the canon's.

**A describe's span is the sum of its tests** (#804), so the third rule
in the same entry is `complexity/noExcessiveLinesPerFunction`. The bound
of 50 is right about test bodies — 215 cases on monumental-archive #262
have a median of 20 lines and only 9% exceed it — but splitting a long
test makes its `describe` longer, and the only compliant move, fewer
cases per group, turns 33 groups into ~133: complying with a measurement
of the wrong thing. The other two structural rules stay on in tests; a
1,400-line test file genuinely splits and a test body's branching is
real. The cost is per-file and stated: the rule's 2.5.7 options are
`maxLines`, `skipBlankLines` and `skipIifes`, no per-construct selector,
so a 60-line helper in a `.test.ts` file goes quiet too. Planted both
directions on the pinned binary: a 60-line production function still
reds, a 60-line describe and a 60-line helper in `tests/` do not. One
trap: `biome lint --only=<rule>` bypasses `overrides` entirely — never
measure an override with it.

**The glob list is closed and measured**, not a guess at what a test
file might be called. Every JS/TS file in the org was enumerated:
release-lab, stele, signer and this repo have **zero**, so
monumental-archive is the entire population, and across its branches and
its history the only shapes that exist are `*.test.ts` (21 files) under
`apps/door/test/` and `tests/unit/`. The list is therefore
`**/*.test.ts`, `**/test/**`, `**/tests/**` — the two directory globs
also carrying that repo's test support (`stack.ts`, `global-setup.ts`),
which hold fixture literals for the same reason. Looked for and **not**
found anywhere, so deliberately unnamed: `*.spec.*`, `__tests__/**`,
`*.test.tsx`, `*.test.js`. A repo needing one of those comes here, which
is the mechanism rather than a gap — a repository cannot widen the list,
because `overrides` is refused by name in its own `biome.json` and the
generated config is what biome is run against.

**That an override merges with `preset: "all"` is measured, not read.**
The reference says only that a matched pattern "will be override the
top-level configuration", which read strictly could mean a test file
loses every other rule. Planted on six files with a control rule:
`noDebugger` still fires in all four test-glob files while the two named
rules go silent, and on the real tree no other rule moved by a single
finding. Both directions and the near miss:

| file | `noMagicNumbers` / `useTopLevelRegex` |
|---|---|
| `src/prod.ts` | **reported** |
| `src/probe-test.ts` — test-shaped, hyphen not dot | **reported** |
| `probe.test.ts`, `src/colocated.test.ts` | clean |
| `test/probe.test.ts`, `tests/unit/probe.test.ts` | clean |

One entry, and the count is load-bearing: the docs state that when a
file matches several patterns "only the first one is used", so a second
entry added later would not compose with this one.

**`suspicious/noMisplacedAssertion` is deliberately not in it.** Its 8
findings on the same tree are all false, but not because they are in
test files: six are the assertion-helper pattern (a helper literally
named `expectValidArk`) and two are the rule not recognising
`test.prop(...)` from `@fast-check/vitest`. A test-glob override would
remove the one class it guards — an `expect` genuinely stranded outside
`it`/`test` reports nothing and the suite passes green. So the rule
stays on, the eight sites take per-site `biome-ignore`, and the library
blindness went upstream as
[biomejs/biome#11454](https://github.com/biomejs/biome/issues/11454) —
searched first, no existing report, confirmed on the pinned 2.5.7
against the control that `test.each` (fixed in biomejs/biome#10680) is
clean in the same run. Same library as #11453: two rules, one
unrecognised test API.

**A detector with zero true positives, switched off** (#788).
`nursery/noConditionalExpect` produced 8 findings on the same tree and
all 8 are false, re-derived site by site against the pinned 2.5.7:

| site | what it actually is |
|---|---|
| `apps/door/src/submit.ts:176` | **production code** — the door's `assert` VERB read as a test assertion |
| `matrix.test.ts:224,227,229,324,325` | `try`/`catch` where **both** branches assert; at 324 the `try` ends in `throw new Error("unreachable")`, so the `catch` is guaranteed |
| `boundary.test.ts:682`, `replay.test.ts:925` | `expect` under a **filter over data**, not a control-flow branch |

The rule cannot tell a filter from a branch — "for every act with a null
payload, assert a tombstone exists" is vacuously true when no act
qualifies, which is the correct meaning, and it looks identical to a
branch that might never be taken. And the vacuous-assertion class the
rule exists to catch was not found anywhere on the tree.

**#783's test-file override could not have contained this**, which is
why the two are separate rulings: the first site is in `apps/door/src`.
The `test` domain is org-fixed at `"all"` and is switched on by a
`vitest` devDependency, so the rule reaches every file in the project,
and the defect is not file-shaped. Per-site suppression is refused for
the other reason — a rule wrong as a class, suppressed at every site, is
the waiver #694 refused.

**The cost is planted rather than asserted, and it is a real cost.** A
genuinely vacuous test — `try { await doThing() } catch (e) { expect(…) }`
— which passes having run no assertion at all when the call succeeds, is
reported with the rule on and **silent** with it off. The org is losing
a detector; what it is not losing is a true positive, because on the
first real tree the rule met there were none, and there was one false
positive in production code.

**The exclusion carries an expiry.** Reported upstream as
[biomejs/biome#11455](https://github.com/biomejs/biome/issues/11455) —
searched first (`noConditionalExpect in:title` returned nothing),
confirmed on the pinned 2.5.7 with a minimal reproduction of both
classes and the control that renaming the imported `assert` to
`applyVerb` clears the production finding while changing nothing else.
The rule returns when biome stops reporting a `try`/`catch` in which
every branch asserts, and stops reading a call whose callee is not an
assertion import as an assertion — its own sibling
`noMisplacedAssertion` already carries the import allowlist that would
fix the second, and in the same run is silent on the file this rule
reports:

| import of `assert` | `noMisplacedAssertion` | `noConditionalExpect` |
|---|---|---|
| `./verb.js` (local) | clean | **reports** |
| `node:assert` | reports | reports |

The rule is **named `"off"` rather than deleted** from the nursery
block: `preset: "all"` does not reach nursery, so the org's 87 names are
an enable-list, and a deleted name is indistinguishable from a rule
nobody has ruled on. `lint:biome-domains` holds the block against
`biome-nursery-domains.tsv` in both directions, and `audit:biome-rules`
keeps the watch.

**Two org controls demanded the opposite edit on the same line**
(#759). `complexity/useLiteralKeys` wants `row["id"]` written `row.id`.
`noPropertyAccessFromIndexSignature` — an org tsc dial, named in
`tsc-flags.txt`, forced on the compiler's command line and, since #731,
required in every repo's own `tsconfig.json` — makes exactly that
rewrite an error. Proven on two files rather than inferred from a
bulk conversion, given an index-signature type:

| form | biome | tsc at the org's flags |
|---|---|---|
| `row["id"]` | `useLiteralKeys` | clean |
| `row.id` | clean | `TS4111` |

There is no order of edits in which both are green. On
monumental-archive that is **590** sites, every one a database row read
through `postgres`'s index-signature `Row` type — and 590 per-site
`biome-ignore` comments for a rule wrong as a CLASS is the shape #694
refused: a waiver, not an exception.

The tsc dial wins, and the reason is not seniority. It is the
correctness distinction — it marks an index-signature lookup as one,
which is why it was adjudicated into the flag file — where the biome
rule is a style preference recommending the one form TypeScript
rejects. Measured: naming the rule does not reset its group under
`preset: "all"`, and the other five complexity rules that fire on that
tree are unchanged. The pair is pinned by a cross-file test rather than
by prose, because `lint:biome` reads one file and `lint:types` the
other, and each was green in isolation the whole time the pair was
unsatisfiable — which is precisely how it survived.

**A zero can mean "did not look".** The lane that found #759 first
measured `TS4111: 0` after converting the tree, and believed it long
enough to write it down. One line of the conversion had produced a
syntax error, and a project that does not PARSE never reaches semantic
checking — so tsc reported the parse error and nothing else. Fixing
that single line surfaced all 587. This is the org's own sqlfluff `PRS`
trap (#708) in TypeScript: one unparsable unit makes an entire file's
real findings invisible. **When a check that should find something
reports nothing, prove the tool got as far as looking before recording
the zero.**

**Domains stopped at the nursery door, and a named rule beat its own
domain** (#720, found building #695). `preset: "all"` excludes nursery,
so the org names all 87 rules individually — and an explicitly named
rule wins over its domain being `"none"`. The mechanism above was
therefore true of the eight stable groups and false of the ninth.
Measured on the fixture with `reactNative: "none"` throughout:
`noReactNativeRawText` fires **1** with the rule named `"on"` and **0**
with the name removed, nothing else changed. 56 of the 87 carry a
domain, so a React repository was drawing Vue's seven, Playwright's
eleven and Svelte's two whatever it declared.

The generated config now writes `"off"` for a nursery rule whose domain
the repository did not claim. `mise/biome-nursery-domains.tsv` records
which rule belongs to which domain, every row from `biome explain`, with
`-` for the 31 that have none — an explicit statement rather than an
empty column. `lint:biome-domains` re-derives all 87 in the canon's own
gate and holds the file's rule set against the org config's nursery
block **in both directions**, because a rule in one and not the other is
exactly how this escaped the first time.

Measured after: `noReactNativeRawText` 1 → 0; `useExplicitType` 33 and
`useUnicodeRegex` 30 unchanged, which is the proof that domainless rules
still run everywhere; `noConditionalExpect` 8 unchanged, the proof that
the org-fixed domains are untouched — that rule is off since #788, so
the reproducible control is now `useConsistentTestIt`, `test` domain,
2 unchanged on the same fixture. A repo claiming nothing silences
34 rules, one claiming `react` silences 28, and the difference is
exactly react's own six.

The cost was the open question and the estimate was wrong by 4.5×, in
the helpful direction. 87 `biome explain` calls take **1.0s**, not the
~4.5s the issue assumed — a call is ~12ms. That made deriving at run
time in every repo genuinely arguable, so it was measured rather than
dismissed: the recorded-table shape adds **no measurable time at all**
to a consumer's `lint:biome` (20 generator runs took 0.818s with the
table populated and 0.818s with every row blank), against 2.64s for one
`biome ci` over the same tree. One second per repo per gate run, versus
zero, for a file with an alarm on it.

**One rule domains cannot reach, recorded so its absence is not read as
an oversight.** `noNodejsModules` has no domain at all — `biome explain`
says so — so no belt mechanism can scope it to browser code. It is not
turned off org-wide, because it is a real check for the browser code the
org will import next. A Node application answers it where every other
exception in this org is answered: in the repo, per file,
`biome-ignore-all lint/correctness/noNodejsModules:` with a reason.

**Nursery stays on, and this is the ruling rather than the default.**
Biome prints "not yet stable and may change in the future" beside all 87
rules, and #695 asked whether a gate whose rule set can move in a patch
release belongs in `ci`. It does, because the version is pinned exact
with `minimum_release_age` at 168h: a changed rule set can only arrive
as a Renovate bump, which reddens that pull request and never `main`. A
gate that goes red on a version bump is the gate working. Nothing in the
87 was a mechanical contradiction on the fixture — the large tail
(`useExplicitType` 33, `useUnicodeRegex` 30, `useExplicitReturnType` 26)
is style the repo has never answered to, which is work, not a defect.

**The sorting family is ruled one action at a time**, because the issue
that reported it named the wrong rule and that is the lesson.
`fix:biome` did rewrite `package.json`, but `useSortedKeys` was already
off and provably so — delete the `assist.actions.source` block and it
fires, as shipped, zero. The action that actually rewrote the file was
`useSortedPackageJson`, never named and inheriting `preset: "all"`. The
`knip.jsonc` rewrite blamed on sorting was the formatter expanding an
array. Whole-tree assist inventory on the fixture: `organizeImports` 48,
`useSortedInterfaceMembers` 46, `useSortedPackageJson` 3,
`useSortedAttributes` 1, and `useSortedKeys` 0 with the block against
**622** without it.

All ten source actions are now named so none can be silently inherited
again. Seven are off on the org's existing reason — alphabetical order
destroys semantic grouping, already recorded for taplo (`reorder_keys`
off) and yamllint (`key-ordering` disabled) — and CSS property order
decides which declaration wins, so `useSortedProperties` is a
correctness argument as well as a legibility one.
`useSortedPackageJson` is ON and is not the exception it looks like: it
does not alphabetise. Measured, it rewrote
`name,private,type,engines,devDependencies` to
`name,private,type,devDependencies,engines` — neither the input order nor
alphabetical, but the conventional field order its own documentation
attributes to `sort-package-json`. Imposing a semantic grouping is what
the anti-alphabetisation ruling is FOR. `organizeImports` and
`noDuplicateClasses` stay on for plainer reasons: import statements carry
no author-chosen order, and removing a duplicate class is not sorting.

**This was ruled twice in one day, and the record of the reversal is the
point.** The first ruling read the standing rule as "everything complies
with the tools at max, not the other way round" and deleted the block
outright; the canon reformatted 24 of its own JSON files to comply. It
was reversed the same day because the org's rule is narrower than *max
always*: **max by default, and an exclusion only where max is measurably
worse, with the measurement written down.** Alphabetising a
hand-authored file is that case; it had already been adjudicated twice,
for two other tools, on evidence; and one tool shipping a behaviour is
not itself evidence against a ruling made on evidence. The consistent
position is biome's alphabetisers off here, clippy's
`arbitrary_source_item_ordering` excluded in #701, and
`rustfmt`/`yamllint` key ordering off — all four the same ruling, not
four preferences.

Issue #695 asked for one of two answers: make `useSortedKeys` agree with
the taplo and yamllint rulings, or record why JSON is different. The
answer is the first: it agrees, it always did, and now the other six
agree with it too.

One claim checked and withdrawn before it was written down:
`useSortedEnumMembers` is a SAFE-tier fix, which looked like a
value-changing hazard for numeric TypeScript enums. It is not — the rule
sorts only enums with string initializers, and a planted numeric enum
came back in its original order.

**Nested configs are refused, and biome's own documentation is the
reason.** biome 2.x supports a package-level `biome.json` carrying
`"root": false` and `"extends": "//"`, and the worked example in its
monorepo guide is a nested package switching `noConsole` off. That is
the mechanism working as designed and it is exactly the surface the org
does not have: a subtree lowering an org rule. So a nested or `.jsonc`
config fails the gate, the root `biome.json` carries domains only, and
the config biome actually reads is generated — `--config-path` "disables
the default configuration file resolution mechanism", which is why the
declaration cannot reach the tool even if it lies. Per-path IDENTITY is
expressible in the pinned schema (`overrides[].linter.domains`) and is
deliberately not built here: the omission cross-check is repo-wide, and
scoping identity by path needs per-path manifest attribution that no
issue has asked for yet.

**The flag that beats the repo does not reach the other readers**
(#699). `lint:types` forces every dial on the command line, and this
document recorded the consequence as settled: a repo's `tsconfig.json`
carries no strictness at all, because a copy would be "either
duplication or a lie". That reasoning assumes ONE consumer. There are
at least three — `tsc`, typescript-eslint and biome — and only the first
one sees a command line.

Measured on monumental-archive, three states, one variable changed:

| `tsconfig.json` | `eslint --max-warnings 0` |
|---|---|
| its own config, strictness present | 0 errors |
| stripped, per the scaffold instruction | **935 errors** |
| raised to the full org level | 0 errors |

The 935 are typescript-eslint reading a weaker program:
`@typescript-eslint/dot-notation` 590 once
`noPropertyAccessFromIndexSignature` is gone, and
`@typescript-eslint/no-unnecessary-condition` 345 once the null-checking
dials move. Nothing about the code changed; the org's enforcement did,
downward, silently, in the direction the design document called
impossible. biome is exposed identically — its `types` and `project`
domains resolve the same TypeScript program.

So the keys are neither duplication nor a lie. They are the only
statement of the level the second reader can see, and the command line
is the redundant copy for `tsc` alone. The org already accepts exactly
this arrangement and says so about `.editorconfig`: a cap stated there
never fights a belt formatter, it states the same fact where every
editor can read it.

`lint:tsconfig-dials` makes it enforcement rather than advice. The
comparison is derived from `mise/tsc-flags.txt` — the same file
`lint:types` passes to the compiler and `lint:tsc-flags` holds against
`tsc --all` — and never from a second list, because the list that
drifted low would look exactly like enforcement. A bare flag demands
`true`, `--flag false` demands `false`, **absent fails** because absent
is the defect, and stricter is never inspected: the hazard is
one-directional, which is the `lint:python-target` shape.

Two mechanics worth recording, both measured rather than assumed:

- The config judged is what `tsc -p <cfg> --showConfig` **resolves**,
  not the file's text. That is TypeScript's own parser, so JSONC
  comments, trailing commas and `extends` behave exactly as they do for
  the compiler, and a monorepo that states the dials once in a root
  config and extends it passes without writing them twice. Crucially,
  `--showConfig` does **not** materialise a default for a dial the
  config never mentions — which is the only reason "absent" is
  detectable at all.
- `declaration` is a required key, not an emit-only one. Setting
  `isolatedDeclarations` without it is `TS5069`, a hard config error, so
  a config carrying one and not the other does not load for any reader.
  That settled a split this build was otherwise about to invent a second
  list for: every flag in `tsc-flags.txt` is required, with no
  exceptions and no new grammar.

The fixture was already failing this on both counts when the check was
first run against it: monumental-archive's six tsconfigs each state
`"skipLibCheck": true` against the org's `false` — actively weaker, not
merely absent — and each omits `noImplicitReturns`,
`noUncheckedSideEffectImports`, `skipDefaultLibCheck`,
`erasableSyntaxOnly`, `isolatedDeclarations` and `declaration`. Raising
them to the org's level costs zero new eslint findings, which is the
third row of the table above.

**tsc, and the flag that beats the repo** (#445). The type checker joins
the belt as `lint:types` — the only tool on it that reasons about what a
value *is*. Biome reads the same files and never resolves a type.

The pin was only possible because the tool changed shape: **TypeScript 7
is the Go port**, so `tsc` is a native executable and the checker lands
without dragging Node onto the belt. Third `github:` backend, and the
weakest of the three on provenance — the release carries no attestation
(`gh attestation verify` 404s on the asset) while mise still narrates
"verify SLSA provenance" and passes under
`locked_verify_provenance = true`. The lock's per-platform checksums are
the only integrity control, recorded here rather than assumed away.

Everything below was proven by running 7.0.2, not read:

- **A compiler flag beats the same key in `tsconfig.json`, even under
  `-p`.** So the belt forces every dial from the command line and a repo
  cannot lower the org's level from its own config — stronger than
  clippy, where a source attribute still wins. (`--flag=false` is
  `TS5023`; the accepted form is `--flag false`.)
- **`tsc -b` refuses every compiler option it is handed (`TS5094`).** So
  following project references and enforcing the org's flags are
  mutually exclusive, and enforcement wins: **project references are
  refused org-wide**. TS 7 checks ~360 files a second, so the build
  orchestration references exist for is not a problem the org has.
- **`tsc -p` on a solution config (`files: []` + `references`) exits 0
  having checked nothing.** The fail-open that makes the refusal above
  cheap rather than costly.
- **Without `--noEmit` the checker writes `.js` beside the sources — and
  writes it when checking FAILED**, since `noEmitOnError` defaults
  false. `.tsbuildinfo` goes to a temp dir for the same reason (#374).
- **A file list is refused outright when a project exists (`TS5112`)**,
  so this is the one belt linter that cannot be driven by `git
  ls-files`. Hence the coverage check: every tracked `.ts` must appear
  in some project's program, because `exclude` is otherwise a green
  gate. Unlike clippy and rustfmt, an unknown or removed option is a
  hard error (`TS5023`/`TS5102`), so a stale config cannot fail open.
- **Suppression is clippy's ladder exactly**: `@ts-expect-error`
  self-retires (`TS2578` when the error goes) and survives, with a
  written reason; `@ts-ignore` is forever and silent, already banned by
  biome's `noTsIgnore`; `@ts-nocheck` silences a whole file, nothing
  catches it — biome ran clean over one — so the belt greps, as it does
  for crate-level `#![allow]`.

**The flag list is an enable-list, and this one closes offline.**
TypeScript cannot express its checks as a group minus exclusions the way
clippy can: `--strict` grows on its own between versions, but every dial
outside that family is named one at a time, so a strictness option added
by a toolchain bump would simply be absent and enforcement would stop
growing silently. `mise/tsc-flags.txt` is the single source both halves
read — `lint:types` passes what it lists, and `lint:tsc-flags` holds it
against `tsc --all`, the pinned compiler's own account of what it has.
Every type-checking boolean must be passed or carry an `adjudicated:`
line with its reason; eleven do (nine implied by `--strict`, plus
`deduplicatePackages` and `stableTypeOrdering`, which are resolution and
determinism rather than checks). No network and no vendored list, so
unlike `audit:biome-rules` this one lives in the gate — and it runs
unconditionally rather than only where TypeScript is tracked, because a
check that skipped the canon is precisely how three biome defects
survived three days.

`skipLibCheck: false` is the one setting worth its own note, because it
is the tool's maximum and the org has no exception mechanism for it. It
checks the type declarations *inside* dependencies. Measured on
monumental-archive: 51 failures, **all of them the repo's own missing
declarations** — 48 vanished by declaring `geojson` in `types`, 3 by
declaring the web-API lib — not sloppy libraries. Where a library truly
ships broken types the routes are to patch, replace, or drop it. Ruled
by Carl 2026-08-17: no escape hatch, repos conform to the org and the
tools rather than the reverse. Note that no SLSA level bears on this:
the dependency track is about a package's provenance, never about
whether its `.d.ts` is coherent.

`tsconfig.json` stays repo-side and carries **no strictness settings at
all** — the belt forces those, so a copy here would be duplication or a
lie. What is left is what the belt cannot invent: which files, server or
browser, which module system. Two consequences ride along: no
`references` (above), and **no `allowJs`**, because TypeScript refuses
`isolatedDeclarations` alongside it (`TS5053`) and a flag the belt
quietly drops for some repos is a hole.

Like biome, **tsc cannot be proven in this repository** — the canon
tracks no TypeScript, so `lint:types` skips clean here. Every guard was
therefore exercised against a throwaway probe (missing config,
`@ts-nocheck`, reasonless `@ts-expect-error`, an uncovered file,
declared references, and the clean pass) and the checker itself against
monumental-archive, where it finds real work: one `erasableSyntaxOnly`
violation, ten `isolatedDeclarations` annotations, three
dependency-declaration errors. One exception since #699:
`lint:tsconfig-dials` DOES run here, because the canon tracks
`scaffold/tsconfig.json` and a scaffold that fails the org's own check
would be worse than no scaffold at all. It is the one TypeScript claim
this repository can prove about itself. *Reopen:* the first repo that
needs project references, with evidence that per-project checking is
insufficient.

**`go mod tidy -diff` and `go test -fuzz`, both already in the toolchain**
(#445). Two of the seven needed no new binary at all — the belt pins `go`
already, and these are subcommands of it. Neither had been wired.

`lint:go-tidy` catches what golangci-lint structurally cannot: it reads
packages and never reads the manifest, so a dependency imported but not
required, a requirement nothing imports, or a `go.sum` that drifted from
either are all invisible to it. This is the `cargo-machete` gap in Go
form, answered by the toolchain rather than another tool. `-diff` is what
makes it a gate check rather than a fixer — it prints the unified diff
and exits non-zero without writing, proven in both untidy directions.
One honest limit: for a *missing* requirement tidy resolves the latest
version over the network, so the suggested diff is time-dependent. The
verdict is not, and a repo in that state is already failing.

Go fuzzing splits the way the Rust half does, for the same two reasons —
it is time-bound and it writes — but it is much cheaper to stand up: the
engine is in the toolchain, coverage instrumentation is built in on amd64
and arm64, and there is no sanitizer flag and **no nightly**, which was
the whole cost of the cargo-fuzz standup. `audit:go-fuzz` runs one target
per invocation (`-fuzz` matches exactly one target in one package by
design) with `-fuzztime` set, because **the documented default is to run
forever** — a task that forgot it would hang the cron rather than fail
it. Minimization and the pre-fuzz unit-test pass are left at their
defaults, which are the tool's maximum.

**Two passes per target, plain then race, rather than a choice.** The
race detector is Go's nearest equivalent to the AddressSanitizer the
Rust cron runs, and it costs **31× throughput** — measured on a probe at
425,319 execs/sec plain against 13,605 under `-race`. The two find
different things: the plain pass explores for panics and logic failures,
the race pass observes concurrency in targets whose code is concurrent
at all. Picking one drops a class of finding, and the org's answer to
instrumentation cost on a free weekly cron is already on record from the
`--careful` ruling — more seconds, not fewer checks. Both passes were
shown to catch a real bug. The gate's seed run carries `-race` too,
where it costs 0.33s → 1.35s, which is nothing.

`CGO_ENABLED=1` rides with it in the belt task, the measured exception
stele already documents for its own tests: `-race` needs cgo on linux,
the gate runner, while darwin tolerates it without — so without this a
local run stays green over a red CI. It is a test instrument; repos that
ship `CGO_ENABLED=0` binaries keep doing so.

The gate half, `lint:go-fuzz-seeds`, is the part worth explaining. A seed
corpus IS the regression suite: when the cron finds a crasher the engine
writes it into `testdata/fuzz/<Target>/`, and from that moment plain `go
test` fails on it. Proven on a probe — a 15-second run found an
invalid-UTF-8 reverse, wrote the input, and the next ordinary run went
red on it. That is why the seeds run under the belt rather than under
whatever a repo happens to define as `test`, which `ci` runs only if it
exists. Without `-fuzz` the engine never generates and never writes, so
the gate half is deterministic by construction.

Neither could be measured against a corpus that exercises them: stele is
the org's only Go repo and has no fuzz targets, so `lint:go-tidy` was
proven there (clean, and both failure directions on a probe) while the
fuzzing pair was proven end to end on a throwaway module — clean seeds,
a crash found and written, the gate then red on the committed input.
*Reopen:* the first Go repo with fuzz targets, to measure `FUZZ_SECONDS`
against something real rather than the 60s default.

**mypy at its strictest, chosen against ty and pyright** (#1604,
2026-11-19). Nothing type-checked the belt's Python, and the property
tests of #1555-#1557 had just found the cost: 25 bugs, 14 of them one
class, a `json.loads` or `tomllib` value of the wrong type reaching
`.get`, `in` or an index. The owner chose ty; the amendment ran three
checkers side by side, each at its strictest, from scratch
environments over the canon's 61 non-test helpers at `610447d`, and
over each buggy helper at the commit before its fix:

| | ty 0.0.101 | pyright 1.1.414 | mypy 2.4.1 |
| --- | --- | --- | --- |
| strictest setting | `--error all` (150 rules) | `strict` + every rule it leaves off | `--strict` + every `--disallow-any-*` + 13 optional codes |
| findings, 61 helpers | 181 | 1361 | 1701 |
| wall-clock, cold | 0.2 s | 2.2 s | 2.2 s |
| wrong-type bugs reported, of 14 | 1 | 8 (2 directly) | 14 (3 directly) |
| install under the belt | `aqua:`, sha256 + GitHub attestation | Node, or a run-time download | `pipx:` via uv |

The other 11 bugs (undecodable bytes, recursion depth, `isdigit` on
non-ASCII digits, logic) are not type errors and no checker reports
them. The 14 are the whole argument. Every one of those values is
typed `Any`, and **ty never reports `Any` where it is used**: its
`unsound-*` rules fire only at a typed return, assignment or yield,
and no rule refuses an explicit `Any`, so its own config cannot close
the hole. pyright strict reports *Unknown* but not explicit `Any`, so
it caught these only where a parameter was a bare `dict`.
`disallow_any_expr` refuses every expression of type `Any`, which is
the issue's sentence exactly: the class refused where the value is
first read. The cost is the same fact from the other side: 1701
findings, 1541 of them `Any` that must be validated into a typed
shape, which is the fix the properties forced one helper at a time.
ty's and pyright's refusals are under Skipped.

Proven by running 2.4.1:

- **`--config-file` replaces discovery.** A tracked `mypy.ini` and a
  `pyproject.toml` `[tool.mypy]`, each saying `ignore_errors`, change
  nothing beside it; alone, they silence the run. So the belt's
  `mise/mypy.ini` is delivered, never copied, and a repository cannot
  lower it from a config file.
- **The strictest setting is read from the tool.** `strict` is named,
  never spelled out, because it is a group that grows with mypy (the
  clippy rule: a group fails closed, an enable-list fails open). On
  top of it, every boolean `--strict` leaves at its lax value
  (`disallow_any_expr`, `_explicit`, `_decorated`, `_unimported`,
  `warn_unreachable`, `strict_equality_for_none`,
  `warn_unused_configs`), and every error code `mypy.errorcodes`
  marks off by default (13). No Python version: mypy takes the
  interpreter's, asserted to be the belt's pin.
- **A missing config refuses (exit 2), but an unknown key and an
  unparsable file each warn and exit 0 under mypy's defaults.** The
  task reds any line mypy prints about the config file.
- **Three seams in a source file lower the level and mypy reports none
  of them**: an inline `# mypy:` comment (`ignore-errors`,
  `allow-any-expr`, `disable-error-code`); a bare `# type: ignore`
  before the first statement, which ignores the module and which
  `ignore-without-code` does not fire on; and `typing.no_type_check`,
  silent even under `disallow_any_decorated`. `mise/python_types_seams.py`
  names each, read with `tokenize` and `ast` so the text in a string
  is not a finding. The one suppression left is
  `# type: ignore[<code>]`: it names its rule, and `unused-ignore`
  reds it once it suppresses nothing.
- **Hyphenated names and `importlib` loads (#1634).** No checker
  follows a `spec_from_file_location` load, so a helper loaded that
  way was a `ModuleType` and every use of it untyped: 2,439 findings.
  Every helper now has a module name (`lint:python-module-names` reds
  a hyphenated `.py` by name; ruff's N999 does not fire outside a
  package) and is imported by it. A file reaching a sibling root names
  it from its own `__file__` with a `sys.path` entry, which holds under
  every runner and inside mutmut's copy; a law row in
  `mise/test_helper_imports.py` refuses a load by path. mypy follows
  neither a script's own directory nor that entry, so `python-types.sh`
  passes the roots as `MYPYPATH`, derived from the tracked files by
  `mise/python_roots.py` and never listed in the delivered config.
- Two files that would be one module stop mypy ("Duplicate module");
  `python_roots.py` refuses them by name first, and
  `explicit_package_bases` keeps every other file's module name its
  path. `cache_dir = /dev/null`: the task writes nothing.
- The interpreter mypy resolves imports through is the Hypothesis
  venv's where Hypothesis is installed (the `prop_*.py` files import
  it; without it, 54 `import-not-found`), asserted to be the same
  Python as the repository's `python3`.

**`given`, typed by the belt** (ruled on #1604). Hypothesis types
`given(...)` as returning `Callable[..., None]`, so on every property
the strict config reds the decorator expression (`disallow_any_expr`,
191 on `301cf46`) and the decorated test (`disallow_any_decorated`,
197). Neither is the property's fault, and neither flag comes off:
`mise/typed_hypothesis.py` gives `given` an exact type instead, one
overload per arity, each strategy's value type tied to its parameter
after `self` and the decorated test taking `self` alone. Every `@given`
in the canon's 54 property files is that shape (positional strategies
on a `unittest.TestCase` method, 1 to 6 of them, `st.data()` and stacked
`@example` included), so 1 to 6 overloads cover all 191. It is stricter
than Hypothesis's own type: `@given(st.integers())` over `n: str` is
`Argument 1 has incompatible type`, and a seventh strategy is "no
overload variant" until one is written. One `cast`, in the module,
meets Hypothesis's type; no `type: ignore` anywhere. Measured after: 0
decorator and 0 decorated findings from `given`'s own type. 36
`@given` lines stay red because the STRATEGY passed carries `Any` in
its value type (recursive JSON strategies, values from helpers loaded
untyped): that is the strategy's type, answered by typing it. Rejected
on the way: two mypy passes with the decorated flag off for property
files (it left the 191 red and was a second run), a per-module section
(mypy refuses `prop_*` as a pattern, and a property file's module name
depends on where it sits), and a per-site ignore. `@st.composite` is
typed precisely by Hypothesis; its six findings were bare `dict`/`set`
return annotations (five, typed here) and a type from a helper loaded
through `importlib` (one, which #1634's rename answers). Reach: the
properties runner puts the belt's directory on the path for the run,
the types task adds it to `MYPYPATH` after the derived roots, and
`test:python-properties` reds a property file that takes `given` from
`hypothesis`, so a repository needs only
`from typed_hypothesis import given`.

**Staged, not advisory.** The canon held 10 874 findings over its 226
tracked files when the pin landed: 1719 in the helpers (`mise/` 1295,
`release/` 335, the rest 89) and 9155 in their tests. A `ci` task
would stop every merge until the last was fixed, so the task lands as
`audit:python-types`, run by the canon's Monday audit and red there
(`lint:audit-scheduled` refuses an audit task no workflow runs); the
findings are run down one lane per directory, and the pull request
that clears the last renames it `lint:python-types`, which `ci`
collects, and drops the audit leg. `pipx:mypy` is the
seventh `pipx:` exception; its five dependencies (`typing_extensions`,
`mypy_extensions`, `pathspec`, `librt`, `ast-serialize`) are unhashed
in `mise.lock`, held by uv under the `UV_*` floor, as Hypothesis's one
is. *Reopen:* an aqua package, or a `pipx:` lock entry that carries
hashes.

**gomu, the Go leg of `audit:mutants`, chosen against two others**
(#1356, under #1336's property 1, "fails when any behaviour changes").
Coverage counts a line as covered when a test runs it, whether or not
any test would notice a change to it, and Go's own coverage has no
branch figure either (gobco's condition figure, below, answers that
half since #1546). The leg
(`mise/mutants-go.sh`, its verdict in `mise/mutants_go.py`) runs on the
Monday cron, never in `ci` and never on a pull request, because the run
repeats the suite once per mutant; `mise/test_task_contract.py` holds
`ci` to reaching no audit task.

*Three candidates, measured on stele* (`9a4923d`, Go 1.27.4, the real
toolchain first on PATH; wall-clock taken at load averages of 186–226 on
a shared Mac, so not comparable between tools):

| | gremlins 0.6.0 | go-mutesting v2.3.1 | gomu v0.3.0 |
| --- | --- | --- | --- |
| Four-package sample | 226 mutants: 172 killed, 16 lived, 38 not covered; 150 s | not run on stele | convcommit 230 mutants (gremlins: 37), 175 killed, 29 survived, 26 not viable; workflow 452, 323 killed |
| A mutant that does not compile | killed (`go test` exits 1 on a build failure too) | killed (same reading) | NOT_VIABLE, from its own `go build` before the test |
| Lines no test executes | NOT COVERED, from its coverage step | run, reported escaped | run, reported SURVIVED |
| Per-site exclusion with a reason | none: file regexps | a next-line comment, but a reason after the mutator list silently breaks it, and an excluded mutant is never generated, so a stale one cannot be seen | none: `.gomuignore`, file patterns |
| How it mutates | a copy of the module per worker | the working tree, in place | `go build -overlay` / `go test -overlay` |
| Pin | `github:`, upstream checksums | `go:` at a pseudo-version only: v2.3.1 has no `/v2` module path, so the tag cannot be installed | `github:`, upstream checksums |

*Why gomu.* It produces eleven kinds of mutant gremlins cannot produce
even with every gremlins mutator enabled — a returned `err` replaced
with `nil`, return values flipped or zeroed, `if` and loop conditions
forced, statements, assignments and expressions removed, blocks
emptied, switch cases emptied, string and integer literals shifted, and
`!` removed — and those are the mutants that ask whether the error paths
and early returns of an evidence engine are tested at all. Its compile
check keeps a mutant that does not build out of the killed count; the
other two count it killed. Chosen by the owner, 2026-11-17.

*Why not gremlins.* Eleven months without a release, five default
mutators, and a run that killed everything and was wrong: the first full
run on stele read 4042 killed, 0 lived, in twenty seconds, because
gremlins runs `go test` in a temporary copy of the module, mise's `go`
shim refused the copy's untrusted `mise.toml` with exit 1, and gremlins
reads exit 1 as KILLED (`internal/engine/executor.go`).

*Why not go-mutesting.* Pinnable only at a pseudo-version, and it
rewrites the working tree in place, one mutant at a time — a job killed
mid-run leaves a mutated file behind.

*What the leg does for gomu, each measured from its v0.3.0 source and
held by a stub row* (`mise/test_mutants_go.py`; no row runs gomu):

- **Adoption is a tracked root `.gomuignore` with no pattern.** gomu
  has no config file; `.gomuignore` is the one file it reads. A pattern
  in any tracked one — gomu walks up from each file to the nearest — is
  red by name: it drops whole files from the run and the count, a
  denominator bought.
- **An equivalent mutant is recorded at its site**:
  `//mutants:equivalent <type> <reason>`, trailing the line or alone on
  the line above. Red by name with no reason, with a type that is not
  the survivor's, on a mutant no test runs, or matching nothing (stale).
  "Excluded" is the number of sound records in the tree, the same in
  every shard, because the totals across shards take it once.
- **NOT COVERED is the leg's own verdict.** gomu takes no coverage, so
  the leg takes each package's own coverage profile first and reports a
  survivor on a line no test executes as NOT COVERED, never as a
  survivor a record could explain. NOT_VIABLE is its own count, never
  killed; TIMED_OUT is killed, with the timeout ten times the package's
  own measured test time and never under 30 s.
- **gomu exits 0 whatever happened**, outside `--ci-mode`, and skips a
  file silently when generating or executing its mutants fails ("(error:
  …)", "(execution error: …)"). The verdict is read from the JSON, and
  either message, a count that disagrees with the results, or an ERROR
  result is red as not judged.
- **Nothing lands in the tree.** gomu writes `.gomu_history.json` and
  `mutation-report.json` to its working directory, so it runs from a
  scratch directory with the file's absolute path, `--incremental=false`
  always.
- **The real toolchain first on PATH**, and the leg refuses to run if
  `go` resolves elsewhere: a `go test` that fails for an environment
  reason is killed to gomu as well (probed on a module of one function).
- **gomu's binary, never mise's shim.** Called through the shim, gomu
  runs under mise's own environment, which puts the shims back ahead of
  that toolchain: its `go build` reached the `go` shim, which refused a
  directory that pins no go, and every mutant read NOT_VIABLE. Measured
  on a one-function module: the v1.69.1 leg reported 4 mutants, 0
  killed, 4 not viable, green; the binary itself, 3 killed and 1 not
  viable. stele's first run (44207892842) exited 2 on every file under
  the same shim. So the leg resolves a shim with `mise which gomu`, and
  a NOT_VIABLE counts only when its error is the compiler's, naming a
  `.go` file and line; any other is red as not judged.
- **One worker per shard.** A Mac ran out of memory (93 GB) while four
  parallel `-overlay` workers ran on stele beside other lanes' gates;
  the runner's memory is measured by the first audit runs before this is
  raised.

*Pin: built from source, never the release binary.*
`go:github.com/sivchari/gomu/cmd/gomu` at v0.3.0, released 2026-10-17,
pinned lazy so only the `mutants-go` job installs it, after the
repository's own go pin. It was first pinned on the `github:` backend,
the prebuilt binary with checksums equal to upstream's
`gomu_0.3.0_checksums.txt`, and that binary could not do the job: gomu
type-checks each file through export data that the repository's `go`
writes, its importer is the one it was built with, and the release is
built with Go 1.24.13. Against Go 1.27 it panicked on the first import
of every file — `cannot decode "fmt", export data version 5 is greater
than maximum supported version 2` — in all four shards of stele's run
44266340133, and on a Mac once `GOROOT` was exported as mise exports it
(without it the probes passed, which is how the panic got past them).
`go install` of the same v0.3.0 with Go 1.27.4 — the module verified by
Go's checksum database, `h1:Msjp6gpKH/pPGrD/IWMVFhD4NG8t3nXkUiNZn+4jS64=`
— ran the same probe clean. Built by the toolchain whose export data it
reads, the two match by construction, so a Go bump cannot strand it; the
lock records the version and the checksum database is the trust. The leg
holds it there: a gomu whose build Go (`go version -m`) is older than the
`go` it runs under is refused by name before it runs — measured, the
release binary is red that way and the source-built one runs. One
maintainer, recorded as the risk it is.

*Not proven on a Mac, by ruling.* No full mutation run is made locally:
the first real run is stele's dispatched audit, which gives the survivor
list, the count and the job's timeout where they matter. **Reopen** when
a Go release ships that gomu cannot type-check or build against, or when
a gomu release adds coverage or a per-site exclusion of its own (the leg
would then defer to it).

**The go statement leg measures the repository's own `go test`**
(#1651). The leg ran a command of its own, `go test ./...
-coverpkg=./... -coverprofile=… -covermode=atomic`, a second run of the
suite without the repository's flags. Measured on stele at `1d775be`,
whose `test` is `go test -race ./...` with `GOFLAGS=-trimpath`: the
task's own command with the cover flags gave the same 15884 of 16450
statements, the identical covered blocks, in 91 s against the leg's
85 s plus the plain run's 100 s.

The flags reach the repository's command through `measured` (#1667),
appended to the GOFLAGS the command has: `-cover -coverpkg=./...
-covermode=atomic -exec=<belt>/go-test-exec`. Measured on go 1.27.4:

- A `-coverprofile` in GOFLAGS names one path, and two `go test` runs
  in one command overwrite it; the figure reads low and nothing is red.
  So no profile path is used.
- `go test` ignores GOCOVERDIR, and sets it, per test process, to a
  temporary directory of its own. `-test.gocoverdir` cannot go through
  GOFLAGS ("unknown flag").
- `-exec` can. go starts each test binary through the program, handing
  it go's own `-test.gocoverdir` among the leading `-test.*` arguments.
  go-test-exec adds `-test.gocoverdir=$ORG_COVERAGE_RUN/go` after them,
  and the later flag wins. Every test process then writes its counters
  into the run's directory, named by hash, process and time. Two
  separate `go test` runs left two counter files, and `go tool covdata
  textfmt` merged them into one atomic profile covering both. Under
  `-race`, atomic mode, the same holds.
- `go build`, `go vet` and `go list` ignore the flags. `-exec` also
  applies to `go run`, so a binary that is not `*.test` runs unchanged.

A `-cover`, `-coverprofile`, `-coverpkg`, `-covermode` or `-exec` of
the test command's own, on a `go test` in the command or in GOFLAGS, and
a GOCOVERDIR already set, are refused by `measured` by name: each would
measure a second way or replace the program this rests on. A tree with
go.mod and no `test` task keeps the leg's own command, named.

**The rust leg measures the repository's own `test` task, once**
(#1653). The leg ran a build and a command of its own, `cargo llvm-cov
--workspace --locked …`: a second build and a second run of the suite,
with the belt's flags and not the repository's, no doctest and none of
its features. Measured cold on 2026-11-19: release-lab's `test` took
9 s, the leg 86 s, the task's own command instrumented 10 s; edtf's
25 s, 89 s and 27 to 29 s. Apart from the pgrx crate every file's
covered lines were identical between the two forms.

`measured` instruments the command where it runs. In a tree with
`Cargo.toml` it exports CARGO_TARGET_DIR as
`<target_directory>/llvm-cov-target` (the directory `cargo llvm-cov`
itself uses) and then what `cargo llvm-cov show-env --sh` prints for
it, so every cargo the command starts builds instrumented there; the
rust leg runs `cargo llvm-cov report` under the same environment and
builds nothing. The two share one sourced derivation,
`mise/rust-coverage-env.sh`. Measured with cargo-llvm-cov 0.9.1 on
release-lab:

- The order is the mechanism. With CARGO_TARGET_DIR exported first,
  LLVM_PROFILE_FILE is under it: the task's command wrote 3 profiles
  and `report` rendered 24 lines in 3 files. Exported after, the
  profiles went to the repository's own `target/` and `report` failed.
- A directory of its own, never the plain build's. The
  instrumentation rides RUSTC_WRAPPER, which cargo's fingerprint does
  not see: an instrumented run into a directory a plain `cargo test`
  had just built reused its binaries and wrote no profile.
- `report` exits 0 after a failed suite. The suite's exit status gates
  the figure: `coverage:check` depends on `test`, so mise never runs
  it after a red one.

The first `measured` call in a run removes old profiles (`cargo llvm-cov
clean --profraw-only`), its marker a directory in the run's directory,
so a restored cache or an earlier run cannot add to the figure and a
second call in the same run keeps the first one's profiles. The leg
reads profiles only beside that marker: no profile, or none from this
run, is red by name, naming the `measured` prefix (#1666). A pgrx
extension the command builds leaves the report by path
(`--ignore-filename-regex` over the extension's directory): its
functions run in the Postgres backend and `test:pgrx` measures them.
COVERAGE_EXCLUDE is removed: the command says what it leaves out, and
the variable set is refused by name in both Rust legs. A CARGO_TARGET_DIR,
RUSTC_WRAPPER or LLVM_PROFILE_FILE already set in the command's
environment, or `cargo llvm-cov` in the command, is refused by
`measured` by name, before anything runs. The `rust-branches` leg keeps
a build and a command of its own: an instrumented nightly build cannot
share the stable run. Rejected, so they are not re-proposed: adding
`--all-features` to the belt's command (#1623), sharing `target/` with
the plain build, a per-repository coverage-command variable (#1357),
keeping the belt's own command where `test` does not use `measured`,
reading the report whatever the suite's exit status, and a front for
`cargo` on PATH (measured and withdrawn on #1667).

**gobco, the Go branch figure, chosen against gocoverageplus** (#1546,
under #1336's property 2, "takes every decision both ways"). Go's own
`go test -cover` counts statements, and an `if` with no `else` has no
block for the path not taken, so a statement figure cannot see it; the
request for branch coverage in Go (golang/go#28888) was closed in 2019.
Calling Go's block coverage a branch figure was rejected for that
reason, and nothing was adopted on reputation: gobco's last release,
v1.3.4 (2024-03-08), predates three Go releases.

*Two candidates, measured on stele* (`1d775be`, Go 1.27.4, each built
from source by that Go, the real toolchain first on PATH, one package
at a time with one worker, 2026-11-19, load 15–19 on the Mac). A planted
module carried an untaken `else`, an `if` whose false path no test
takes, and an `a && b` that is true once and false only through `a`;
statement coverage read it as 90.0% and saw one of the three.

| | gobco v1.3.4 (default mode) | gocoverageplus v1.2.0 |
| --- | --- | --- |
| Planted untaken `else` | reported | not reported |
| Planted `if` false path | reported | not reported |
| Planted half-taken `a && b` | reported (`"b"` true, never false) | not reported: 3/3 branches, 100% |
| Every stele package | 46 of 46 clean | crashes on every profile: its `golang.org/x/tools` v0.26.0 fails on Go 1.27 (`package "encoding/base64" without types`), exit 1, no report |
| Figure | 10549/11602 conditions, 90.92% (each package's own tests) | none |
| What counts as a branch | each condition: every `if`, `for` and `switch` case condition and every operand of `&&` and `\|\|`, each needing both outcomes | one per `if` line, covered once the line runs: block coverage relabelled |
| Wall-clock | 1225 s against `go test`'s 199 s, per package in series: 6.15 times | 1–2 s before the crash; it reads an existing profile and runs no tests |
| Tree | untouched: it instruments a copy under `$TMPDIR` | untouched |
| `go test`'s result | unchanged on all 46 (exit 0 both ways) | n/a |

`-branch` mode was measured too and refused: it missed the planted
`a && b` (4/6), the very gap the issue named.

*What the leg does for gobco* (`mise/coverage-go-branches.sh`, held by
stub rows in `mise/test_coverage_go_branches.py`; no row runs gobco). A
helper of its own rather than a selector of `coverage-measure.sh`: the
gate's tool derivation (#1410) reads every call in a script a gate task
runs, and `coverage:check` runs that one, so a gobco call there would
record the gate as calling a tool it never runs.

- **One package per call.** v1.3.4 panics on more than one ("checking
  multiple packages doesn't work yet"), so the leg loops `go list ./...`
  and sums the counts. A package that fails under gobco is red by name,
  as is one that prints no count, and 0 of 0 overall is refused, never
  floored.
- **The real Go directory first on PATH**, and the leg refuses to run if
  `go` resolves elsewhere. gobco copies the module, its `mise.toml` with
  it, into a temporary directory and runs `go test` there; through mise's
  `go` shim the copy's config is untrusted and every run died "go test .:
  exit status 1" (measured). gobco's binary, never its shim, as gomu's.
- **A build Go older than the running Go is refused.** gobco parses with
  the go/parser it was built with; the belt builds it with the
  repository's own go, and a stale binary or a cache restored across a
  Go bump is red by name, as gomu's is.
- **Stated limits.** Only a package's own tests count: there is no
  `-coverpkg`, so a condition exercised only by a neighbouring package's
  tests reads untaken (the `go` leg measures with `-coverpkg=./...`). A
  package with no tests counts all its conditions untaken. `select` is
  not covered (gobco's README).

*Where it runs.* Item 2's rule, from the measured cost: 6.15 times
`go test` is far over the fifth of `coverage:check` the gate allows, so
the figure is held by `audit:coverage-go-branches` on the Monday cron in
`repo-audit.yml`, beside the Rust branch floor, and never in the gate.
Its floor is a second line of `.coverage-floor`, `go-branches
<percent>`, derived by `coverage:adopt` under the same law and band as
`rust-branches` and carried through by the release path. Chosen by the
owner, 2026-11-19. The runner's whole-module wall-clock comes from the
first dispatched audit after the pin.

*Pin.* `go:github.com/rillig/gobco` at v1.3.4, lazy, so only the audit
job's named install builds it, after the repository's own go pin. The
module is verified by Go's checksum database,
`h1:4weHt9u2CyTOnEJ/7z6fAcHZvJHy1pNXm8danDh3TA8=`. BSD-2-Clause. One
maintainer, recorded as the risk it is. **Reopen** when a Go release
ships syntax gobco cannot parse, when gobco gains multi-package runs (the
loop goes), or when Go gains branch coverage of its own (the leg becomes
a flag on the `go` leg and the line folds into the main floor).

**eslint, typescript-eslint and knip stay repo-side** — not a rejection,
a placement. A flat eslint config is a JS module that imports its plugins
from the repo's own `node_modules`, and typescript-eslint's type-aware
rules need the repo's TypeScript program; no central pin can supply
either, and eslint's peerDependency contract would make a canon bump a
simultaneous breaking change to every JS repo. `lint:eslint` and
`lint:knip` belong in the belt as *tasks* on the `lint:fuzz-build`
pattern — canon-written, skip-clean, assert-with-remedy — while the
binaries stay build inputs. Biome and eslint are complements, not rivals:
biome does have type-aware rules since v2 (the `types` and `project`
domains, behind its Scanner), but far fewer than typescript-eslint, and
none of eslint's plugin ecosystem. Configure both, switch the overlaps
off on one side. *Reopen:* nothing pending.

**prettier** — skipped, and recorded because its absence is otherwise
conspicuous. npm-only in the registry (`npm:prettier`, no aqua package),
so it would record version-only in the lock and re-admit the npm backend
one line after markdownlint-cli2 is retired for exactly that. It also
only formats, so it would need a linter beside it in any case, and biome
exists to replace it. The one real gap it would have filled: nothing on
the belt formats YAML — yamllint checks and does not rewrite. *Reopen:*
an aqua-backed prettier, or YAML formatting becoming worth the backend.

**Renovate `customManagers`: preset and repo arrays concatenate, never
replace** (#314). The open question that issue punted — whether a repo
defining its own `customManagers` while extending `default.json` would
silently drop the preset's lefthook and preset-ref managers — is
settled by Renovate's documented merge semantics: `customManagers` is a
*mergeable* list option, so values "will be added to any existing
object or array that existed with the same name". A repo's array
supplements the preset's; nothing is dropped. The signer-pin manager
this verdict originally governed is now **deleted along with its file**
(#316 finding 2): the manager's URL package name (`git-refs` needs one)
could not match the first-party group's `monumental-archive/**` glob,
so every signer bump split into two branches neither of which could go
green — the canon now states the trusted signer only in its `uses:`
lines, like every consumer, and `verify-release.yml` derives the
verdict identity from them. The merge-semantics fact stands for any
future custom manager. *Reopen:* a value that genuinely cannot live in
a natively-managed representation.

**yamllint, full inventory, the tree conforming to the tool** (#403).
Stood up greenfield from the tool's own documentation — every rule in
the inventory enabled, preset warnings raised to error, and the gate
runs `--strict` so a warning could not hide regardless. The starting
tree measured 642 findings and every one was conformed away rather than
configured away: `---`/`...` document markers on all 52 bare files,
`on:` → `"on":` (it *is* a boolean; the quoted key is better YAML, not
appeasement), bare triggers → `{}` (an honest empty mapping where
`empty-values` rightly forbids null), 54 flow sequences and 5 flow
mappings rewritten to block style, redundant quotes dropped, and every
over-length line rewrapped by hand.

The exceptions were adjudicated one by one against a zero-exceptions
default, and three survive, each with a reason about the rule or a
measured external constraint:

- **`key-ordering` disabled** — alphabetical keys destroy semantic
  order (`on`/`permissions`/`jobs`). The same ruling as taplo's
  `reorder_keys` and biome's `useSortedKeys`. *Reopen:* never; this is
  a category call.
- **`indentation.check-multi-line-strings: false`** — it applies YAML
  indentation rules to the bash inside `run:` blocks. The reason is
  about reach, which normally means the layout is wrong, so it carries
  a filed retirement trigger: #398 turns it on once the shell leaves
  `run:` blocks.
- **`comments.min-spaces-from-content: 1`, not the default 2** —
  pinact writes exactly one space before its version comment and is
  not configurable; measured on the tree, a belt-internal conflict.
  *Reopen:* pinact growing a spacing option.
- **`line-length: max: 130` is derived, not chosen** — the mandatory
  pin format (`uses: <path>@<40-char-sha> # vX.Y.Z`, enforced by
  `lint:action-pins`) produces unbreakable lines measured at up to 127
  characters, and the trailing comment defeats both
  `allow-non-breakable-*` exemptions. Sprinkling ~150 `disable-line`
  directives was rejected as policy-as-confetti. 130 is the tightest
  cap the mandatory format admits; per-language columns tighter than
  this belong to `.editorconfig`, which can express per-type caps.

Two consequences worth recording. First, the `brackets` rule's
flow-to-block rewrite broke `lint:release-phases`, whose awk parser
read only flow-style `needs:` lists — the parser learned block
sequences rather than the tree keeping flow style, because the org
conforms to the tool, not the tool to a parser. Second, yamllint lints
its own config, which therefore also carries `---`/`...`.

`lint:yaml` passes `-c mise/yamllint.yaml` from `ORG_BELT_DIR`, so the
config is DELIVERED and no repo tracks one (#455). The delivery is what
keeps the ruff/biome/golangci trap shut rather than an assertion: with
no config yamllint falls back silently to its `default` preset, a
fraction of the org policy, on a green gate — but `-c` beats discovery,
so that fallback is unreachable and the belt asserts nothing. No `fix:*`
sibling exists to wire: yamllint ships no writer, so conformance is
hand edits by design (prettier's verdict already records the YAML
formatting gap). pipx-only — no aqua package exists (404 in the
registry; pure Python, no binary releases) — making it the second
`pipx:` exception after reuse, riding checksummed uv under the same
`UV_*` floor environment, pinned in `[tools]` before first install so
the lock entry is never bare.

`lint:shell` delivers `mise/shellcheckrc` and refuses a repo-local
`.shellcheckrc` — the same trap in a **fifth** costume, and the one that
survived longest because it is invisible from any repo that happens to
carry the file (#696). shellcheck's defaults leave all eleven optional
checks off, and the settings used to be discovered by walking up from
each script, so the canon was strict and every consumer was not:
measured 2026-08-21 in release-lab, an unbraced `$x` was silent at exit
0 without the file and SC2250 at exit 1 with it, on the same belt and
the same tree. Three mechanics decided the shape, each measured on
shellcheck 0.11.0. `--rcfile` beats discovery, so a repo copy could not
win — it could only be a file someone believed in; `--norc` beats
`--rcfile` in either order, which is why `lint:actions` (actionlint
calls shellcheck with `--norc`) is the one surface that restates the
level as flags instead of reading the file; and an unreadable
`--rcfile` is a warning at **exit 0**, so the task asserts the file
before linting rather than letting a belt that never arrived read as a
clean run. `severity=` is not an rc directive at all — the floor stays
shellcheck's default `style`, which is already the lowest — and
`--check-sourced` is left off with its reason recorded in the file:
every tracked script is linted directly, so it duplicates findings
rather than adding any.

**editorconfig-checker, and the `.editorconfig` it lands with** (#403).
The point of doing it second: the file is the org's first *written*
indentation rule — until now 2-space was practice plus two tool
settings, a gap this doc recorded under biome. Per-type sections defer
to the formatter that owns each language (ruff/rustfmt 4, gofmt tab,
Makefiles and git-written config files tab), and the per-type
`max_line_length` caps live here because yamllint's single global knob
could not express them. biome reads the file natively
(`useEditorconfig` defaults to true in 2.x); shfmt deliberately does
NOT — any shfmt flag disables its editorconfig reading and `-s`
(simplify) has no editorconfig key, so `lint:shell` keeps its flags
authoritative and `.editorconfig` states the same facts for editors;
drift between the two cannot land silently because both are enforced in
the gate.

One check is off, with the reason about the check:
`-disable-indent-size`. ec's indent test is "leading spaces are a
multiple of N" and cannot know continuation alignment — measured at
standup, 73 of 76 findings were ec disagreeing with ruff-formatted
Python, shfmt-formatted shell, rumdl-clean markdown continuations and
yamllint-clean YAML. Indent *size* is enforced per language by its
owner; ec keeps the checks nothing else covers (indent_style, charset,
end_of_line, insert_final_newline, trim_trailing_whitespace,
max_line_length). The three real findings it did surface were
conformed: two `gh issue create --body` one-liners over 130 columns
(now variable builds) and the gitconfig fixture, which is *correctly*
tab-indented because git itself writes tabs — declared as such rather
than reformatted, on the fixture-is-not-evidence rule. "Indent SIZE is
enforced per language by its owner" turned out to cover indent STYLE
too: `[*.go]` is `indent_style = unset`, because ec's line-based check
cannot distinguish code indentation from bytes inside Go raw string
literals and reds space-indented test fixtures gofumpt must leave
verbatim — measured on stele, the org's first Go consumer, invisible
from the canon which tracks no Go. The line-length deferral was later
completed for the one uncovered format: `[*.sh]` carries
`max_line_length = 130`, matching the cap embedded shell already gets
through yamllint — a standalone script held to a looser bar than the
same code in a `run:` block would be incoherent (35 lines conformed;
markdown and Python need no row, rumdl and ruff cap them). *Reopen:*
ec growing a formatter-aware indent check.

**gitleaks, and why two secret scanners is a split rather than a
duplicate** (#403). The issue's one demand for this item was to state
the overlap with `trivy --scanners secret` explicitly, so: trivy's
secret scanner guards **artifacts** — the Dockerfile context in
`lint:trivy` and published image layers in the release path — at the
surface those artifacts present; gitleaks guards **source at commit
depth** via `gitleaks git`, which reads `git log -p`, so a secret
committed and then deleted — invisible to every tip-of-tree scanner,
still live to anyone who clones — is found. Neither reaches the
other's blind spot; retiring either would open one.

Standup rulings. `git` mode over `dir` mode: history scanning is
tracked-only by construction, so the lefthook-cache walker rule is
satisfied without a file list, and the scan is deterministic over the
commit set (353 commits, 1.9 MB, ~1 s, zero findings at adoption).
`--redact` because a public CI log printing the matched secret would
itself be the disclosure. **No config file, by design**: gitleaks'
default ruleset is its maximum, the exact opposite of the ruff/biome
weak-default trap, so `lint:gitleaks` requires no config and an
unexplained `.gitleaks.toml` appearing in a repo is itself a review
finding — the file exists only when a real finding demanded a written
allowlist. A red is a rotation first, then a fingerprint in
`.gitleaksignore` naming the rotation: the secret is in history, so
tip edits fix nothing. *Reopen:* nothing pending.

**hadolint at `--failure-threshold style`, one Dockerfile, guarded
everywhere else** (#403). The canon tracks exactly one Dockerfile
(`docker/pgrx-artifact.Dockerfile`) and it was clean at the maximum
threshold on the adoption run — so unlike yamllint this standup
conformed nothing, it raised the bar for whatever comes. The threshold
is the ruling: hadolint's default (`info`) exits clean on style
findings, the same silent-sitting-warning class biome's
`--error-on-warnings` exists to prevent. RUN lines get shellcheck
through hadolint itself — the mirror-the-solver rule, not a second
extraction. No `.hadolint.yaml`, same shape as gitleaks: the default
rule set is the maximum, a config only weakens (ignores) or adds label
schema, and an unexplained one is a review finding. The trivy overlap
is a split, stated: trivy's misconfig scanner runs the AVD policy
family (root user, exposed ports classes); hadolint runs the DL
best-practice family plus shell analysis inside RUN — they barely
intersect, and each catches classes the other has no rule for.
*Reopen:* a label-schema convention (`--require-label`), if the org
ever standardises OCI image labels beyond what the release path
already stamps.

**cargo-machete, static in the gate, metadata mode kept out of it**
(#403). Every Cargo.toml dependency must be imported somewhere or carry
a written `[package.metadata.cargo-machete] ignored` entry beside the
dependency it excuses — the written-exception culture, never a
task-level skip. Two rulings. First, the gate runs WITHOUT
`--with-metadata`: that flag shells out to `cargo metadata
--all-features` and can rewrite Cargo.lock, and a gate must not mutate
the tree it judges — the imprecision cost is bounded exactly by those
written ignored entries. Second, the backend: not in the aqua registry
(404 at standup), but upstream publishes per-platform tarballs with
sha256s, so the `github:` backend delivers the upstream binary
lock-checksummed across all seven platforms — the closest-to-policy
path without aqua, and the belt's first use of it. Like biome and
golangci-lint this tool is **unprovable in the canon** (no Rust
tracked; `lint:machete` skips clean here) and is exercised first in a
consumer. *Reopen:* an aqua-registry package appearing (move the pin),
or machete growing a no-write metadata mode (reconsider precision).

**cargo-semver-checks as `audit:semver`, and the recorded deviation
from #403's wording** (#403). The issue's close condition says every
tool gets a `lint:<tool>` task in the gate; this one cannot: the
baseline it checks against is whatever crates.io currently publishes,
fetched at run time, and "the gate is deterministic" is a
rule-that-must-not-be-broken — a red that can arrive with no local
change is a property of the feed, the exact class `audit:*` exists
for. So it rides `repo-audit.yml` beside `audit:deny` on the Monday
cron, per repo. A red means the public API diverged from the latest
release beyond what the next version number admits: fix the API or
bump the major — never both silently. Guarded to publishable crates
(`publish = false` has no registry baseline). Backend honesty: no aqua
package and, unlike machete, no upstream checksum assets either — the
`github:` backend locks a trust-on-first-use checksum, weaker than a
publisher-stated sum, recorded here rather than glossed. Needs
cargo+rustdoc at run time (repo-side toolchain, the `lint:deny`
shape). Unprovable in the canon — no Rust. *Reopen:* an aqua package
or upstream sha256 assets appearing; or the release path growing a
pre-tag semver leg so the break is caught at the version decision
rather than the Monday after.

**mutmut as `audit:mutants`' Python leg** (#1354, under #1336's
testing standard; the shape is cargo-mutants', below). The measure is
no unexplained surviving mutant, never a score. Measured 2026-11-18:
mutmut 3.8.1 (PyPI, `py3-none-any`) installs on the `pipx:` route under
the belt's `python` pin. Its tool venv's `pyvenv.cfg` names mise's
3.15.2 as `home`, so the hidden-Python trap does not bite. libcst,
setproctitle and coverage arrive as cp315 wheels; pyyaml 6.0.3 has no
cp315 wheel and builds its pure-Python fallback from the sdist, with no
compiler. Install took 123 s on an M1 Pro. Pinned `lazy`, like
cargo-mutants, so only `repo-mutants.yml`'s `mutants-python` job
installs it. cosmic-ray, the issue's fallback, was not needed.

Read from mutmut's own source, since no mutation run happens on a
laptop (owner's ruling; the first real run is the canon's, on a
runner):

- **Exclusion at the site.** `# pragma: no mutate` (line), `block`,
  and `start`/`end` regions. mutmut reads only the first word after
  `no mutate`, so the leg requires the reason after ` -- ` in the
  same comment (`end` needs none). The config's `do_not_mutate`,
  `do_not_mutate_patterns` and `only_mutate` entries each need a `#`
  line above them, and `mutate_only_covered_lines = true` is refused:
  it drops every untested line, with no reason anywhere.
- **What counts.** `mutmut results --all true` gives one status per
  mutant. killed, timeout, caught by type check and segfault count as
  killed. survived and "no tests" are survivors. suspicious, skipped,
  not checked and interrupted were never judged, and are red. The
  excluded count is SITES (reasoned pragmas and list entries): mutmut
  never generates an excluded mutant, so there is no mutant to count.
- **Where a survivor is.** `mutmut show` diffs the mutated function
  on its own, so its hunk counts lines from the function's `def`. The
  leg finds the file line from the `def` and the removed line.
- **No sharding.** 3.8.1 has no shard option, so the Python job is
  one job; a shard other than 0/1 is red.
- **Subprocess-run helpers (item 3), measured.** mutmut copies every
  file under its source paths into `mutants/`, and every mutated file
  starts by importing mutmut's trampoline. The canon's runs
  (self-mutants 44213905549) showed that a helper a test runs as a
  child `python3 mise/x.py` then died with "No module named 'mutmut'"
  on the unmutated baseline: the belt's Python has no mutmut. 53 of
  the canon's 56 non-test helpers are run that way. The leg therefore
  writes a start-up hook (`mutants_python.py hook`, owner's ruling on
  item 3): a temporary `sitecustomize` on `PYTHONPATH` that APPENDS
  mutmut's own site directory, derived from the installed tool, the
  way the coverage leg reaches its children, so a child no longer
  dies. What a child can do under a mutant was measured on #1673
  (mutmut 3.8.1, `answer(n): return n + 1`, mutant `n - 1`): a child
  that IMPORTS the helper's module runs the mutant (prints 0); a
  child that runs it AS A SCRIPT prints the original (2), since
  mutmut runs a mutant only when the function's `__module__` equals
  the module in the mutant's name, and a script's functions are in
  `__main__`. mutmut also chooses a mutant's tests from calls
  recorded in the test process, so a function reached only in a
  child comes out "no tests" either way. That is debt, not a judged
  mutant: a helper's logic is tested in-process (CONTRIBUTING.md),
  `lint:in-process` holds it per function, and the functions not yet
  reached are listed in `security/helper-import-debt.txt`, 164 of 697
  when the law landed. Rewriting the child's mutant name and writing
  its statistics back were rejected there: both reach into mutmut's
  internals.
- **A child's configuration (#1542), measured.** In the statistics
  pass every mutated function reads mutmut's configuration, which
  mutmut loads lazily from the CURRENT directory; a child a test
  started in a scratch directory found none and died with "Could not
  figure out where the code to mutate is" (self-mutants
  44298570855). The hook therefore also carries the directory the
  leg runs `mutmut run` in, and under a mutant (`MUTANT_UNDER_TEST`
  set) loads the configuration there through mutmut's own
  `config()`, then changes back; a hook that cannot load it fails
  the child by name. Cost, measured on mutmut 3.8.1 with Python
  3.15.2 (macOS, 60 starts each, median): a bare child `python3`
  starts in 30 ms without the load and 75 ms with it, almost all of
  it the import of mutmut's package, which a child running mutated
  code pays anyway. Every Python child of the statistics pass pays
  it once.

*Reopen:* mutmut gaining sharding (shard the Python job like Rust's).

**StrykerJS as `audit:mutants`' TypeScript leg** (#1358, under the
testing standard of #1336; the shape is the other legs'). The measure
is no unexplained surviving mutant, never a score. Measured 2026-11-19 on
StrykerJS 10.1.1 (2026-10-23): SLSA v1 provenance on every
`@stryker-mutator/*` package and `npm audit signatures` all verified; it
installs clean from the consumer's lockfile under pnpm's
`trustPolicy: no-downgrade`, the seven-day release age and
`strictDepBuilds` (no build script), and runs vitest 4.1.12. On one
source file with one worker: 144 mutants in 35 s. The consumer's
service-free scope instruments 932 mutants with a 2 s initial run, so
there is one job and no sharding (StrykerJS has none).

The belt pins no copy. StrykerJS is the repository's, in its
devDependencies beside its runner plugin, the way its test runner is;
the leg runs `node_modules/.bin/stryker`.

What the leg does, each measured on 10.1.1 and held by a stub row
(`mise/test_mutants_typescript.py`; no row runs StrykerJS):

- **Adoption is a tracked root `stryker.config.json`.** It names the
  paths to mutate, so a repository adopts its service-free code first.
  A tracked StrykerJS config in any other form is red, never a skip:
  the leg reads JSON. Refused in it, by name, because each drops
  mutants with no reason at any site: `mutator.excludedMutations`,
  `ignoreStatic`, `ignorers`, and `thresholds.break` (a score).
- **In place, never in the sandbox.** The sandbox copies the working
  folder without reading `.gitignore`, so a `.env` in the checkout is
  copied. It also links `node_modules` back to the original by
  absolute path, so a workspace package imported by name never sees
  its mutant: on one file, 144 of 144 "survived" and none was killed.
  `--inPlace` gave 106 killed, 5 timed out, 30 survived and 3 not
  covered, the same as a sandbox run with hand-written aliases. The
  leg passes `--inPlace` itself, refuses uncommitted tracked changes
  before the run and is red if one remains after it (orchestrator's
  ruling on #1358; keeping the sandbox with per-package aliases was
  rejected).
- **A runner only.** In place, a mutant runs with whatever
  environment the tree holds, so the leg refuses, by name, where `CI`
  is unset: mutated code never runs beside a workstation's live
  `.env` (same ruling).
- **What counts.** The JSON report (`reports/mutation/mutation.json`,
  schema 1.0) gives one status per mutant. Killed and Timeout are
  killed. Survived is a survivor, and NoCoverage is a survivor named
  `not covered`. Ignored is excluded only with a reason: the site
  comment's reason lands in `statusReason`, and with none StrykerJS
  writes `Ignored using a comment`, which is red. CompileError is
  counted as not viable, never as killed. RuntimeError, Pending and
  anything else are not judged, and red.
- **StrykerJS exits 0 whatever survives**, so the verdict is read
  from the report. A non-zero exit judged nothing, and is red with
  its output.
- **Every mutant, every week: incremental mode is deferred**
  (orchestrator's ruling on #1358). A runner starts with no earlier
  results, so `incremental: true` would read as on and do nothing, and
  the config check refuses it by name. Tracking the incremental file
  was rejected: the run rewrites it, which the clean-tree guard reds.
  *Reopen* when a full weekly run no longer fits the job; the response
  is a restored and saved incremental file outside the tree, in its
  own issue.
- **Under pnpm the config must name its runner in `plugins`.** Core
  sits in `.pnpm/` and cannot find its sibling ("no TestRunner
  plugins were loaded").

The leg was run against StrykerJS's real report once, on the Mac, on
that one file: the summary line, each survivor's file, line and
column, and both kinds of exclusion (with a reason, counted; without,
red) came out as the stub rows state.

*Reopen:* StrykerJS's sandbox resolving workspace packages inside
itself (the sandbox could then carry the run), or StrykerJS gaining
sharding (the job splits like Rust's).

**cargo-mutants as `audit:mutants`' Rust leg, compiled and kept out
of every other job** (#1355, under #1336's testing standard). Coverage
says a test ran a line; a mutation run changes the line and asks
whether any test noticed. The measure is **no unexplained surviving
mutant**, never a score: a percentage can be bought by deleting hard
code and lets the survivor that matters hide in the average. A
survivor is killed by an assertion or recorded in the repository's own
`.cargo/mutants.toml` as an `exclude_re` entry under a `#` line that
says why; an entry with no reason, an entry on the `exclude_re = [`
line itself, and `#[mutants::skip]` (a dependency in shipped code) are
each red by name. `exclude_globs` is held to the same reason rule — a
whole-file exclusion is an exclusion. Adoption is that file, tracked;
the belt keeps no list of adopters and delivers no exclusions. Every
workspace member is mutated, named one by one: measured on
release-lab, a bare `cargo mutants` follows `default-members` and left
the pgrx crate out. A pgrx extension is mutated in its own run under
its default major with `pg_test`, provisioned as every pgrx task is.

Measured 2026-11-17. **Backend:** v27.1.0 (2026-06-02) still ships
three assets — `x86_64-apple-darwin`, `x86_64-pc-windows-msvc`,
`x86_64-unknown-linux-gnu` — with no macOS arm64, no Linux arm64 and no
checksum file, the same as v27.0.0 and v26.2.0; not in mise's baked
aqua registry. The `github:` backend cannot cover the lock's
platforms, so the pin is `cargo:cargo-mutants`, exact, built with
`--locked` (mise's cargo default): 172 s to compile on an M1 Pro.
**Kept out of every default install:** mise's `lazy = true` (with
`lazy_bins`, which a non-registry backend needs) does it, measured on
mise 2026.11.6 with a probe config — the locked lazy tool is in the
lock, a bare `mise install` skips it, `mise run` of another task does
not install it, and `mise install <tool>` installs it. So no second
config file was needed (#1355's named fallback). repo-mutants.yml's
`mutants-rust` jobs are the one place it is built, and they run only
where `.cargo/mutants.toml` is tracked, since a repository with no Rust
has no cargo to build it with. **Its own workflow, not an audit leg** (owner's
ruling): `repo-mutants.yml`, called from each repository's
`workflow-templates/mutants.yml` stub on its own Monday slot (07:00,
the audit's is 06:00). Folded into `repo-audit.yml`, one survivor
turned the whole weekly audit red and nobody could tell what else had
passed; apart, each run's result means one thing. **Each language has
its own jobs** in that file — `mutants-rust` and `mutants-rust-totals`
— selecting its one leg with `MUTANTS_LEG`, so two adopted languages
are two ticks, two shard counts and two timeouts, and a red names its
language. They run only where an `adoption` job finds the config in the
checkout; that guard is a job because `hashFiles` is step-only
(measured: actionlint refuses it in a job's `if:`). **Sharded from the
start** (owner's ruling): a
matrix of four, `--shard k/4`, fail-fast off, each shard printing its
own survivors and summary, and a totals job stating the one
answer across them (`mise/mutation-totals.sh`). Measured on a nine-mutant
crate: `--list` honours `--shard`, and the default slice sharding cut
it 3/3/3/0 — an empty shard exits 0 and writes no `outcomes.json`, so
the leg says "no mutants in this shard" and stays green. Excluded is a
whole-population figure (a shard is cut after exclusion), reported the
same by every shard and taken once. The first runner run
(release-lab, run 44205918375, 2026-11-18) measured shards of 5m02s to
6m54s, mostly the cold toolchain install (163-221 s) and lab-pg's pgrx
baseline. The Rust jobs keep four shards and take a 25-minute
timeout, about three times the slowest; a larger consumer's run is
what moves either. Locally the leg was proven only against real
output on a small crate — five survivors red by name, then green with
five counted excluded under a reasoned `exclude_re` entry, then the
same crate across four shards and their totals. *Reopen:* upstream
publishing arm64 assets with checksums (move to `github:`), or an aqua
package appearing.

**A class every pgrx adopter meets: cargo-mutants mutates the pgrx
tests.** A pgrx extension's `#[pg_test]` module is compiled under
`cfg(any(test, feature = "pg_test"))` (pgrx builds the tests into the
extension). cargo-mutants' own test filter skips `#[test]` functions
and `cfg(test)` code and does not recognise that cfg, so it mutates
the tests themselves (`replace tests::answers with ()`). The tests
always pass with such a mutant, so it always survives. Measured on
release-lab's first run (run 44205918375). By the rule it stays a
per-repository exclusion, never belt-delivered, but it is the same
fact in every pgrx repository, so copy this into `.cargo/mutants.toml`
with the crate's own path and nothing broader:

```toml
exclude_re = [
  # <crate>'s `tests::` functions are test code: pgrx compiles its
  # #[pg_test] module under `cfg(any(test, feature = "pg_test"))`, which
  # cargo-mutants' own test filter (it skips `#[test]` and `cfg(test)`)
  # does not recognise, so it mutates the tests themselves.
  '^crates/<crate>/src/lib\.rs:[0-9]+:[0-9]+: replace (tests::|.* in tests::)',
]
```

The two alternatives are cargo-mutants' two name forms: a whole
function replaced (`replace tests::answers with ()`) and a mutant
inside one (`replace == with != in tests::answers`). A file other than
`lib.rs` that holds a `#[pg_test]` module gets its own entry.

A second pgrx fact for the triage: an assertion added to a pgrx
crate rides an existing `#[pg_test]` where the crate's `pg_test`
module carries `single_call_fn` expectations. pgrx's generated runner
calls `pg_test::setup` once per test, so a second test leaves those
expectations unfulfilled (measured with `lint:pg-clippy` on
release-lab).

**sqlfluff, the third `pipx:` exception, provable only in
monumental-archive-db** (#403). PyPI-only — no aqua package — so it
rides checksummed uv under the `UV_*` floor environment like reuse and
yamllint, closing the count this doc predicted when the uv layer was
built. sqlfluff's default rule set is its maximum (gitleaks' shape,
not ruff's), so `mise/sqlfluff.cfg` exists for exactly one thing the
tool refuses to guess: the dialect — postgres org-wide, the only SQL
the org ships. `templater = raw`: org SQL is DDL/migrations, not
Jinja, and a templater that expands nothing can corrupt nothing.
`lint:sql` is subject-named (one language, the lint:python rule) and
asserts nothing: the config is delivered with `--config` from
`ORG_BELT_DIR` (#455), so no repo tracks a `.sqlfluff` and the missing-
config case cannot arise — and would fail loud rather than green if it
did. `fix:sql` is the write-mode
sibling; `--force` is its non-interactive flag, not an unsafe-fix
mode. **No belt escape, by design (#708, #694)** — sqlfluff's own two
are repo content and work under the delivered `--config`, measured on
the pinned 4.3.0: `.sqlfluffignore` is honoured even on the explicit
paths this task passes (the escape for GENERATED files, a reason per
entry, `_typos.toml` footing), and per-line `-- noqa: CODE` suppresses
one finding. The noqa carries **one trap worth the row**: text after
`noqa:` is parsed as more rule codes, so `-- noqa: RF04 real column`
discards the suppression entirely and warns about nothing — 1 finding,
where `-- noqa: RF04` gives 0. The reason belongs on the line above.
**Zero SQL in the canon** — the task skips clean here, and the
rule set is exercised first in monumental-archive-db, stated outright
like biome, golangci-lint and the Rust pair. *Reopen:* Jinja-templated
SQL appearing anywhere in the org (revisit the raw templater).

**lychee, and the escape the belt could not see** (#681). The link
audit, and the second config the belt delivers for the reason
`mise/clippy.toml` is delivered. Measured on the pinned 0.24.2:

- a repo-local `lychee.toml` in the working directory is
  auto-discovered and obeyed, so `exclude = [".*"]` beside a README
  returns every link in the tree as *excluded*, exit 0, with a summary
  line no different from a clean run;
- an explicit `--config` REPLACES that discovery — the same repo-local
  file present and the same broken link, and the run reds;
- `.lycheeignore` is read from the working directory INDEPENDENTLY of
  `--config`, and is honoured with the belt config in force;
- config `exclude` and CLI `--exclude` merge rather than replace;
- an unknown or mistyped config key refuses to start (exit 3), so a
  delivered config cannot silently become a no-op.

So the judgement #681 left open answers itself, and it answers the
*other* way round from how the question was put: the question was
whether a consumer NEEDS a lychee config, and the measurement is that
every consumer already HAS one available to it and the belt could not
see it. That is a per-repo escape from an org check, which is the one
thing the belt does not have — so the config is delivered, and
`--config` is load-bearing rather than tidy.

`.lycheeignore` stays repo content, on the footing `deny.toml`'s skips
stand on: it is lychee's own per-link mechanism, it survives the
delivered config by construction rather than by permission, and a link
that is dead for a reason only that repo knows — a vendor that 403s
every bot, a login wall — is repo identity. Give each entry a `#`
reason comment above it, as every other per-site escape in the org
does; lychee tolerates the comments, and a comment excludes nothing on
its own (both measured). `--include-fragments` deliberately stays a
command-line flag: its config form takes an enum and the bare flag's
default could not be shown equal to any single value, so moving it
would have changed what the audit means while only tidying where it
was written. *Reopen:* lychee gaining a way to state "no repo-local
config may apply" in-config, which would make the `--config` argument
documentation rather than enforcement.

**lint:vendored, the half the vendor/ exclusions left out**
(#1160). Every belt linter skips tracked files under `vendor/` on
purpose — vendored text is someone else's, and linting it would mean
editing it — and with nothing else asking, a tracked file there was
checked by nothing at all. One repository had built the answer for
itself: monumental-archive's `scripts/check-vendored-pins.sh`, over
`vendor/agent-skills/` and `.md` files only. It moved into the belt
widened to every tracked file under any `vendor/` directory, reading
the same `PINNED.toml` format unchanged:

- each tracked file is covered by the `PINNED.toml` in its own
  directory, or the nearest one in an ancestor still under that
  `vendor/`; a file with no covering manifest is red;
- a manifest states `repo`, `upstream_path` and a `commit` of exactly
  40 hex characters — a branch, a tag or a short SHA moves, and is red;
- every covered file other than the manifest is listed under `[files]`
  with a sha256 that matches: unlisted, listed-and-untracked and
  listed-and-different are each red;
- `[omitted]` is the record of files deliberately not taken, parsed
  past and never checked.

**It never fetches upstream.** Whether the pinned commit really holds
these bytes is review's question, asked once when the manifest is
written; the gate is offline, and asks only whether the tree has moved
since. Fetching in the gate was proposed and rejected for exactly that
reason. Go's `vendor/modules.txt` and cargo's `.cargo-checksum.json`
are the toolchains' own vendoring records, and are not recognised:
no repository in the org vendors Go or Rust, so they have no consumer
yet, and that door is #121's. A Go or cargo vendor tree arriving today
would need a `PINNED.toml` like any other. Skips clean when nothing is
tracked under `vendor/`, which on 2026-11-15 was every repository but
one. *Reopen:* a repository vendoring Go modules or crates (#121).

## Skipped, with rationale and reopen trigger

- **Mutation testing of PL/pgSQL** (#1547) — no installable tool.
  Searched 2026-11-19, the web and GitHub: what exists is a tool for
  Oracle's PL/SQL (muPLSQL, a 2022 paper), a patent, and a SELECT-query
  mutator that is not published. PL/pgSQL is held by its coverage
  floor alone. *Reopen:* a maintained tool that mutates PL/pgSQL
  function bodies.
- **A property-testing tool inside the database** (#1547) — searched
  the same day: SqlProof is an early alpha that says not to rely on
  it, and pgTAP is example-based. A repository's PL/pgSQL is varied
  through its own language's property tests against the real
  database, which is that repository's work. *Reopen:* a maintained
  in-database property-testing tool past alpha.
- **Allstar / Minder** — continuous org-policy enforcement services.
  The org enforces settings as code (`settings/repo-baseline.sh`,
  org rulesets) and audits drift on Mondays with zero standing
  infrastructure. *Reopen:* drift the Monday audit structurally cannot
  see, or an org too large for a weekly sweep.
- **witness / Archivista** — in-toto attestation framework plus a
  standing attestation store. The GitHub attestation store already
  serves every consumer recipe, and Archivista is a service to keep
  up. *Reopen:* attestation needs the GitHub store cannot express
  (cross-org graphs, non-GitHub consumers at volume).
- **FRSCA / Tekton Chains** — a different build platform with signing
  in its control plane. The org's platform is GitHub Actions with the
  signer split providing the same property. *Reopen:* leaving GitHub
  Actions.
- **sigstore policy-controller** — admission control for Kubernetes.
  The org operates no cluster; verification happens at consumption via
  documented recipes. *Reopen:* an org-operated deployment surface.
- **cargo-audit** — skipped-subsumed: same RustSec DB, strictly a
  subset of `cargo deny check advisories`
  ([`dependency-track.md`](dependency-track.md), recorded verdicts).
- **cargo-msrv** — verifies (or bisects) a crate's declared minimum
  Rust version. Right about the thing that matters — one source of
  truth, no second pin to keep honest — and refused on the gate's one
  hard law: `cargo msrv verify` resolves the declared toolchain through
  rustup at run time, and nothing network-dependent belongs in `ci`.
  The org buys the same property with a pinned second toolchain and an
  agreement check (`lint:msrv`, above). *Reopen:* a verify mode that
  takes an already-installed toolchain and fetches nothing, or the org
  deciding a network fetch of a pinned, checksummed toolchain is not a
  network dependency in the sense the law means.
- **cargo-crev** — web-of-trust dependency review. Same boundary as
  cargo-vet below: with one maintainer the web has one node.
  *Reopen:* with cargo-vet.
- **cargo-vet + cackle** — the L4-track pair. The corrected record
  (#203): cargo-vet is **not** technically sequenced behind anything —
  it needs no vendoring and could run tomorrow. Its blocker is
  headcount: one maintainer becomes the auditor of last resort and
  Renovate queues behind a reading list
  ([`dependency-track.md`](dependency-track.md), #122). *Reopen:* a
  second maintainer.
- **reuse, adjudicated late and the lateness recorded** (#316): the
  scaffold cited "tool adjudication in #82" — a verdict never made, the
  cffconvert phantom class — and in the gap three compliance defects
  shipped past `lint:licence`, which proves only that a licence was
  chosen. Adopted at 6.2.0: PyPI-only (no aqua package), so the `pipx:`
  backend rides aqua-backed uv — the second documented exception to
  aqua-first after `cargo:` — with the `charset-normalizer` extra
  pinned via `uvx_args`/`pipx_args` (macOS has no `file` bindings; the
  bare install crashes on import). Runs in the gate over a scratch tree
  of tracked files, deterministic and offline. Per-file SPDX headers
  are refused as a matter of design, not tooling: a file inherits the
  repo's `REUSE.toml` declaration, and a hardcoded header both
  overrides it and travels wrongly when the file is copied (#214).
  *Reopen:* an aqua package appearing, or a repo genuinely needing
  per-file licence variance.
- **Hypothesis, adopted for Python property testing (#1332,
  2026-11-17).** Property 3 of the testing standard (#1336), "varies its
  inputs", had a Rust leg and a Go leg and nothing for the canon's own
  language. Adopted at 6.170.15 as `pipx:hypothesis`, the sixth `pipx:`
  exception, after mutmut (#1354); PyPI-only, no aqua package. `abi3`
  wheels for macOS arm64 and x86_64 and manylinux x86_64 and aarch64,
  plus an sdist, published 2026-11-06 and so past the seven-day floor;
  one dependency, `sortedcontainers` (2.4.0 resolved). The first
  third-party library the belt's tests import, and only from `prop_*.py`
  files: a repository's `test` task never collects them, so its ordinary
  suites stay stdlib `unittest` and run on an interpreter with nothing
  installed, and `test:python-properties` reds a tracked non-property
  file that imports Hypothesis. #364's refusal of a test framework does
  not reach this: it refused one for code being ported away, and the
  ordinary suites still take none. The tool venv's interpreter is the
  belt's `python` pin, read from its `pyvenv.cfg` (`home =
  …/installs/python/3.15.2/bin`, `version_info = 3.15.2`) and asserted
  by both tasks against the repository's `python3` before anything runs.
  **`mise.lock` does not cover the dependency's hash, or Hypothesis's
  own:** a `pipx:` entry records `version` and `backend` and nothing
  else (the lock gained exactly those two lines here), the same as
  `pipx:reuse` and its `--with charset-normalizer` extra. What holds the
  closure is uv's resolution under the belt's `UV_EXCLUDE_NEWER` floor
  and `UV_INDEX_STRATEGY = "first-index"`, and PyPI's own hashes, which
  uv checks at download; a pinned hash per wheel is not available
  through this backend. The profiles are the belt's
  (`mise/python_properties.py`): the gate derandomizes with a bounded
  `max_examples`, no example database and no deadline, so two runs print
  the same bytes; the cron takes a random seed it prints first. A test
  restating a bound is refused. *Reopen:* an aqua package, or a `pipx:`
  lock entry that carries hashes.
- **fast-check, adopted for TypeScript property testing (#1549,
  2026-11-19), the repository's own.** The TypeScript half of
  property 3 (#1336), #1332's shape. The belt pins neither fast-check
  nor vitest: both are read from the repository's lockfile, the
  coverage leg's rule (#1357). The decision was how the seed reaches
  fast-check, and it was measured on monumental-archive at `f060c67`
  (vitest 4.1.12, `@fast-check/vitest` 0.5.0, fast-check 4.9.0 as
  its pnpm lockfile pins them, installed `--frozen-lockfile`):
  - fast-check 4.9.0 reads **no environment variable** (no
    `process.env` in its `lib/`); unseeded, every run is a fresh
    seed. Only `fc.configureGlobal`, inside the test process, sets it.
  - vitest **refuses a setup file on its command line**: `vitest run
    --setupFiles <f>` exits on `CACError: Unknown option
    --setupFiles`. Item 4(a) by flag is not available.
  - Item 4(a) **through a wrapper config works**, so it is the design
    and (b) is not needed: the leg writes, in a scratch directory, a
    config that imports the repository's own unchanged (a function
    config is called), adds the belt's setup file to `setupFiles`,
    narrows `include` to exactly the tracked property files, resets
    `exclude` to vitest's default and adds a JSON report, and hands it
    to the repository's vitest with `-c` and `--root`. Nothing is
    written into the repository. monumental-archive's real config
    (a custom sequencer, `globalSetup`, `include`) loaded through it
    intact. A config with `test.projects` is refused by name: a
    project does not inherit root `setupFiles`.
  - The setup file must import what the repository locks: a bare
    `import "fast-check"` from it fails in that tree (`Cannot find
    package 'fast-check'`: pnpm keeps a transitive dependency off the
    top level), while the `fc` that `@fast-check/vitest` re-exports
    resolves and is the instance the tests use. The setup configures
    every one of the two that imports.
  - Behaviour, one probe property at 3 runs, examples printed by the
    property: seed 42 gave `9, -994490854, -1536816376` on two runs;
    seed 7 gave `-897639049, 922382085, 1907858427`. The global
    configuration reached all four forms the org writes —
    `test.prop` with a tuple and with a record, `fc.assert` on a
    property and on an async property — so the belt's reporter prints
    one line per property (test, runs, seed, a digest of every
    generated example) and two gate runs of one commit print the same
    lines. A failing property still throws fast-check's own report:
    counterexample `[10]` for `n < 10`, shrunk, with its replay path.
  - A `*.prop.test.ts` file also matches vitest's default
    `*.test.ts` include, so a repository's own `vitest run` would
    run its properties a second time, unseeded, in the same gate.
    Ruled 2026-11-19 (on #1549): the gate leg asks vitest which files
    the repository's own config collects (`vitest list --filesOnly
    --json`, which evaluates the config and, measured on
    monumental-archive's, runs no `globalSetup`) and is red by name on
    any property file in that set, with the line to add:
    `exclude: [...configDefaults.exclude, "**/*.prop.test.*"]`. With
    that line the probe tree's leg held; without it the leg was red
    before running anything. Renaming properties outside the default
    include (`*.prop.ts`) and a lint reading the config as text are
    refused in the ruling.
  The gate's seed is fixed (1336) at 200 runs; the cron's is fresh,
  printed first and taken from `ORG_PROPERTY_SEED` when set, at
  10,000. A property restating `seed`, `numRuns` or
  `configureGlobal(...)` is refused by `lint:typescript-properties`,
  as a restated bound is for Python; so is a fast-check import outside
  a `*.prop.test.*` file. A second property library pinned by the
  belt, an unseeded gate, and a coverage-guided TypeScript fuzzer are
  refused in the issue. *Reopen:* vitest accepting `setupFiles` on
  its command line (the wrapper retires), or fast-check reading a seed
  from the environment.
- **atheris, refused (#1332, 2026-11-17).** The one Python fuzzer
  Scorecard's `Fuzzing` check detects (the literal `import atheris`,
  ossf/scorecard `checks/raw/fuzzing.go`). It cannot be installed
  where the belt runs: 3.1.0 ships three wheels, all
  `manylinux2014_x86_64`, for cp312, cp313 and cp314, and no sdist
  (PyPI, read 2026-11-17) — none for the belt's Python 3.15, and none
  for macOS at all. ClusterFuzzLite is refused with it: it runs
  atheris in Google's Linux images, CI-only and network-bound, and a
  ClusterFuzzLite config kept only so Scorecard detects it would be
  buying the number (#1336). Scorecard `Fuzzing` therefore stays 0 by
  choice; the inputs are varied by Hypothesis, which Scorecard does
  not read for Python. *Reopen:* an `abi3` or cp315 wheel plus a
  macOS wheel.
- **cargo-fuzz's sanitizers — REOPENED AND ADOPTED, 2026-08-16 (#445).**
  This entry read "deliberately `--sanitizer none` on the pinned STABLE
  toolchain (#316)", on the reasoning that ASan needs nightly and a
  nightly in the gate means either a second toolchain pin or mid-run
  rustup, both refused. The reasoning held; the conclusion was drawn one
  step too wide. **`-s address` is cargo-fuzz's own DEFAULT**, so the org
  was running the tool below its default rather than below its maximum —
  a harder thing to justify. What resolves it is splitting by task class
  rather than by tool: `lint:fuzz-build` stays in the gate, on stable,
  `-s none`, proving only that targets still COMPILE — a compile check
  needs no sanitizer — while the sanitized run moves entirely to
  `audit:fuzz` on the Monday cron, where fuzzing already belonged. The
  gate therefore never sees a nightly, and the second toolchain lands
  only in repos that actually fuzz, declared in their own mise.toml.
  Measured before adopting: mise holds and locks both toolchains
  (`rust = ["1.97.1", "nightly-2026-07-20"]`), `RUSTUP_TOOLCHAIN`
  selects between them without fighting mise, and the lab's real target
  ran 11.7M executions in 13s under ASan. `--careful` rides along (std
  rebuilt with debug assertions and extra const-UB checks). Costs, stated
  plainly: a dated nightly is **581 MB** beside stable's 1.9 GB and
  cannot be Renovate-bumped, making it the one hand-maintained version
  pin in the org. Bare `nightly` is refused outright — a moving channel
  is not a pinned input. The `cargo:` backend remains a documented
  aqua-first exception: version-pinned, no attestations. *Reopen:*
  cargo-fuzz shipping sanitizer support on stable, which would delete the
  nightly entirely.
- **kcov (bash coverage for the canon)** — skipped. The instinct was
  right (#316 addendum: `lint:source-attest` already executes the
  source-attest scripts end-to-end, so instrumenting what runs beats
  adopting a test framework) but the tool fails the belt's priors on
  every axis: source-only distribution (v43 ships no binary assets),
  not in aqua, ptrace-based and Linux-only — a coverage number the
  gate could never reproduce locally on macOS. The field is exhausted,
  not merely unsurveyed: shellspec's `--coverage` **is** kcov
  underneath, bats has no coverage mechanism at all (the standard
  pairing is bats + kcov), and bashcov needs a pinned Ruby runtime and
  gem distribution — a worse supply chain than kcov, not a better one.
  **This is blocked, not permanent** (#347). The distinction matters
  and was previously stated the wrong way round: the canon's coverage
  was recorded as "exemplary, no data by construction — a stated
  condition, not deferred work", which reads as a ceiling chosen
  rather than one imposed. A verdict carrying a live reopen trigger is
  blocked work. Consequences, stated so they are not rediscovered:
  Best Practices **Silver** wants ≥80% statement coverage and **Gold**
  90% branch coverage, so the canon cannot earn those criteria until
  this unblocks; the same applies to signer, and not to release-lab,
  which is Rust and measures coverage today. The canon's codecov
  shield was **removed** in #347 rather than left reading "unknown"
  forever — it implied the canon is untested, when what is missing is
  measurement and not tests (`lint:source-attest` drives the real
  emitter scripts end-to-end against recorded fixtures every commit).
  Removing the shield does not soften the verdict; this entry is where
  it survives. **Resolved by relocation, 2026-08-19 (#392 shipped;
  close-out #398):** the bash whose coverage was unmeasurable no
  longer exists — the logic lives in stele, where `go test -cover`
  supplies the number, `.coverage-floor` ratchets it (≥ 90 committed,
  ~98.6 measured) and the codecov shield renders it. The reopen
  trigger fired the way the 2026-08-15 amendment predicted, with one
  correction to that prediction: the number lives in the tool's repo,
  not this one. The canon tree retains orchestration-only shell
  (one-line task bodies and workflow steps) and its Python belt
  helpers, so the canon takes no `.coverage-floor` and no shield: a
  coverage number over a workflow tree would measure nothing.
  **Amended 2026-08-20 (#576):** the clause that used to sit here — two
  helpers, "no unit-testable corpus of its own" — was true of a
  100-line skip emitter and a shell extractor and stopped being true
  with `mise/subject_budget.py`, 700 lines of manager walks, template
  resolution and budget arithmetic. It now has `mise/test_subject_budget.py`,
  42 stdlib-`unittest` table tests reached by the gate through the
  ordinary `ci` contract (a repo-level `mise.toml` defining `test`,
  which `ci` collects optionally). Nothing joins the belt to run them:
  #364 refused a test framework because adopting one is a further thing
  to port, and that reasoning bites a new dependency, not the batteries
  inside the pinned interpreter. The suite is checked against mutation
  rather than trusted — eight deliberate defects introduced one at a
  time (a fallback where the code must hard-error, indirect Go requires
  counted, a digest measured at its 40-character pin, a `>=`/`>`
  boundary slip, `matchManagers` ignored, growth applied to a
  fixed-width pseudo-version, JS named groups left untranslated, and an
  absent belt config defaulted), all eight caught. The coverage
  criteria are unaffected: there is still no measurement, only tests. The
  Best Practices coverage criteria are re-answered on that basis in
  [best-practices.md](best-practices.md).
  Note what was **revoked** here: this entry previously recorded a
  separable half — that bats and shellspec are both aqua-backed and
  belt-clean, so adopting a bash test framework was not blocked by any
  of this. That standup (#364) is closed and both tools are refused. A
  test framework for code that is being ported is a fourth thing to
  port, and in Go the question is `go test`. The defect class #364
  named — skip-clean guards are the least exercised code in the org and
  a guard that skips when it should run looks exactly like success —
  is real and survives its closure, carried into #392 as a table-test
  requirement.
  **Amended 2026-08-21 (#652):** two defects in the number itself,
  both measured on stele. The floor was a HAND-TYPED number the
  ceiling could drift away from: 98.6 on 08-18, 92.1 two days later
  with four features landed, nothing red and nothing noticed — the
  floor never moved, the ceiling fell into the headroom. And the Go
  leg ran `go test ./...` with no `-coverpkg`, so a statement
  exercised only by a neighbouring package's tests scored uncovered
  and the gate enforced a number below what the tests prove: measured
  94.9 without the flag against 95.4 with it, 0.5 points and 51
  statements. Both are closed by making the floor DERIVED STATE —
  `mise/coverage_floor.py` re-derives it at every release as
  `measured - 2`, it only rises, a release measuring below
  `floor + band` fails loudly, and a hand edit that disagrees with the
  file's own record is refused as drift rather than repaired. The
  measurement moved to `mise/coverage-measure.sh`, shared by the gate
  and the release path so the two cannot enforce and derive from
  different flags. Adoption stays opt-in and is now a measurement
  (`mise run coverage:adopt`), not a guess. Tested the way #576's
  helper is: `mise/test_coverage_floor.py`, 33 table rows over every
  guard branch in both directions, mutation-checked with seven
  deliberate defects (the drift check disabled, the ratchet disabled,
  the ratchet loosened by a band, `--adopt` honoured everywhere,
  rounding turned half-up, the weakest leg turned into the strongest,
  and a malformed measurement skipped instead of refused) — all seven
  caught.
  **Amended 2026-08-24 (#772): the release path is tested, and the
  boundary of what that claim covers is three-way.** `release/` had no
  tests at all until #816, and the edtf import (#669) found three
  untested assumptions in a row, each on `main` after a merge. The
  answer is the same shape as the two amendments above — stdlib
  `unittest` table rows reached by the ordinary `ci` contract, no tool
  adopted — and the line it draws is:
  - **In the gate, deterministically:** every pure decision. The
    predecessor search over a recorded forge listing
    (`release/test_generate_pgrx_upgrade.py`, including the three tag
    histories an import can arrive with); the derive-notes input
    contract and `prepare-release.sh`'s own tree refusals
    (`test_prepare_release.py`); the upgrade-script generator's whole
    statement-classification table and its psql-guard arithmetic
    (`test_upgrade_script.py`); and the applicability and
    input-validation branches of `record-draft.sh`,
    `derive-coverage-floor.sh`, `rust-build.sh`, `open-release-pr.sh`
    and `tag-release.sh` (`test_release_guards.py`). Mutation-checked,
    not trusted: eight deliberate defects in the generator, each caught
    by the row that claims it.
  - **Container truth, via #813's mechanism, not a second one.** A
    linter can say a line is 84 columns; it cannot say whether
    PostgreSQL will load the file. #792 is the proof — the three-line
    guard passed `lint:sql` and canon v1.58.2 shipped it, and the
    server rejects a bare `\quit` line because the extension loader
    ignores only lines beginning with `\echo`. That oracle is written,
    both directions, in `TheLoadableGuardOracle`, and skipped with #813
    as its stated reason rather than left absent. #772 deliberately
    built no Postgres provisioning of its own.
  - **Full-width integration stays release-lab's.** The candidate
    package build, the ALTER EXTENSION round trip against a live
    catalog, and the five artefact classes across PG 14–18 are proven
    where they always were. Nothing here replaces a lab run; what it
    replaces is discovering a pure input-to-decision bug by spending a
    version number.
  Fixtures are real captures, and where history offered nothing they are
  mutations of one: the pgrx schemas are `cargo pgrx package` output
  extracted unedited from published release-lab tarballs, so
  `.sqlfluffignore` carries them as the generated transcripts they are,
  while the derived upgrade script beside them stays in `lint:sql` —
  #792's ruling (a), that the org's derived files meet the org's own
  rules, held to rather than asserted.
  **Amended 2026-11-17 (#1331): the Python half is measured, and the
  canon takes a floor.** The 2026-08-19 sentence above — that the canon
  "takes no `.coverage-floor` and no shield" because "a coverage number
  over a workflow tree would measure nothing" — stopped describing the
  tree: on 2026-11-16 it tracked 62 Python files (25,610 lines, 41 of
  them `unittest` suites over three roots) against 32 shell files
  (5,626 lines). The canon is a Python codebase with a real test corpus,
  and the machinery could not measure it because every `coverage:*`
  task skipped without `Cargo.toml` or `go.mod`. coverage.py joins the
  belt (`pipx:coverage`, the fourth `pipx:` exception; a measurement
  tool, not a test framework, so #364's refusal does not reach it — the
  suites stay stdlib `unittest`), and `mise/coverage-measure.sh` gains
  a `python` leg: the repository's own `test` task, run under coverage
  with every child `python3` measured, printing `python <percent>`
  (statements) and `python-branches <percent>`. The floor is a minimum
  both hold. Applicability is "tracks `*.py` and defines `test`",
  answered once by the script's `probe` for all three tasks and the
  release path. Measured on the canon at this change: python 93.17,
  python-branches 89.32 with child processes measured, against
  92.45 and 88.66 with the unittest runners alone (3,021 statements,
  1,200 branches; test files omitted, since a suite executes itself
  completely by construction). The canon adopted on 2026-11-18, after
  the release that carried the leg and its upload: 94.9 statements and
  91.1 branches, `.coverage-floor` 89.1, the codecov shield rendered.
  **The bash half is unchanged and still blocked**: kcov is v43 with
  no binary assets and not in aqua, the trigger above stands, and the shell stays
  unmeasured — the floor covers the Python legs only. Rejected, so it
  is not re-proposed: stdlib `trace` (no Codecov format, an order of
  magnitude slower, no branch coverage), and waiting for a bash tool
  before measuring anything.
- **TypeScript coverage (#1357)** — measured with the runner the
  repository already has, never a tool the belt pins. The `typescript`
  leg of `mise/coverage-measure.sh` applies where a tracked root
  `package.json`'s lockfile pins vitest, and runs that vitest from the
  repository's `node_modules` under the coverage provider the same
  lockfile pins (`@vitest/coverage-v8` or `-istanbul`), printing
  `typescript <lines>` and `typescript-branches <branches>`; the floor
  holds both. `mise/coverage_js_runner.py` reads the runner and the
  provider from the lockfile (pnpm, npm, yarn or bun), so the version
  is the repository's. A lockfile with vitest and no provider reds with
  the remedy (add the provider at vitest's version), two providers red
  rather than being chosen between, and any other runner (jest, mocha,
  `node --test`, …) is named and skipped. The leg passes only the
  provider, the `json-summary` and `lcov` reporters and a report
  directory of its own; what the suite includes is the repository's
  vitest config. One consequence, measured from vitest 4's own CLI:
  without `coverage.include`, vitest counts only the files a test
  loaded, so a file no test imports is invisible rather than uncovered
  — a repository adopting the floor sets `coverage.include` over its
  sources. The upload is the measured run's lcov, as
  `lcov-typescript.info` (`lcov.info` is the Rust leg's). Proven on
  monumental-archive, the org's only TypeScript, by that repository's
  own adoption change on top of Renovate's pin bump
  (monumental-archive/monumental-archive#365); its counts live there,
  not here. **Rejected, so it is not re-proposed:** c8 or nyc pinned by the
  belt beside the runner — two instrumentations of one suite, and a
  provider version the belt would own where the repository's lockfile
  should.
  **Amended 2026-11-18: the run measured is the `test` task's own.**
  The first build ran vitest's whole configured suite, where the
  Decided build says the repository's own test command (#707: the suite
  measured is the suite the gate runs). The leg now reads the one vitest
  invocation from the `test` task and the tasks it depends on (`mise
  tasks info`), and runs the repository's vitest with exactly its
  arguments, from its task's `dir`, plus the coverage flags. A task it
  cannot read is refused by name: no vitest (`pnpm test` behind a
  package.json script), two, a shell expansion, a compound line, an
  unknown launcher, a subcommand other than `run`, or a coverage flag of
  its own. A vitest tree with no `test` task skips clean, as a Python
  one does. Running the task's scripts with a coverage-adding `vitest`
  first on PATH was measured and refused: `pnpm exec` and `npx` put
  `node_modules/.bin` at the head of PATH, so the repository's binary
  runs and the shim never does, and vitest reads no environment variable
  that enables coverage.
- **PL/pgSQL coverage (#1547)** — measured with plpgsql_check, which
  the repository's test stack carries and the belt does not. The org's
  published database image ships a deliberately small set of
  extensions, and a coverage extension is a test tool, so the
  repository's tests run against a layer built from that image which
  adds the distribution's package, preloads it
  (`shared_preload_libraries` gains `plpgsql,plpgsql_check`) and starts
  with `plpgsql_check.profiler=on`. The database lives only for the
  suite, so the reading is taken inside it: whenever
  `mise/coverage-measure.sh` runs the `test` task (and its depends) it
  sets `ORG_PLPGSQL_COVERAGE` naming a report, and the repository's
  teardown calls `mise/plpgsql_coverage.py collect <conninfo>` before
  it stops the database. A report written is the leg's answer; the
  leg searches no file. **Amended 2026-11-20 (#1666):** the first
  build switched the leg on wherever any tracked file carried the
  `LANGUAGE` clause, so a README or a fixture reddened a gate, and
  narrowing that search to `*.sql` would have missed functions declared
  inside another language's source or created at run time. Instead the
  floor records the legs it holds (`# legs:` in `.coverage-floor`,
  written by every derivation): a recorded leg that reports nothing is
  red by name, a measured leg not yet recorded is held and joins at the
  next derivation, and a leg leaves the record only through
  `# dropped: <leg> <reason>`. `coverage:adopt` always runs the `test`
  task, which is how a repository's PL/pgSQL leg is first found and
  recorded; a floor with no `legs:` line demands nothing until its
  next derivation writes one. Rejected, so it is not re-proposed: a
  `*.sql` search or a deny-list of prose, a search for the teardown's
  call, "no report means not applicable" with no record, a leg list in
  each repository's own configuration, and parsing SQL out of other
  languages' source. The helper lists every
  PL/pgSQL function outside the system schemas that no extension owns,
  asks `plpgsql_coverage_statements` and `plpgsql_coverage_branches`
  for each, and the leg prints `plpgsql <percent>` and
  `plpgsql-branches <percent>`; the floor holds both. Adoption is that
  one teardown call plus the floor. The belt pins no client: `psql` is
  the repository's own, already needed to run its tests.
  Measured 2026-11-19 on the proving repository's runner gate, two
  runs per setting (monumental-archive/monumental-archive#373):
  the layer builds from PGDG's `postgresql-18-plpgsql-check`
  2.10.16-1.pgdg13+2 for the image's Postgres 18 (amd64 and arm64
  both carry it), uncached in 38–53 s; a probe function never called
  reads statements 0 and branches 0, and the same body driven down
  both arms of its `IF` reads 1 and 1; the suite read 43.7 s and
  45.6 s with the profiler on against 33.7 s and 37.9 s with it off,
  about +9 s or +25%. The package is 2.10.16 where upstream is at
  2.11.0; both coverage functions and the profiler exist at 2.10.16,
  and the installed extension reports `extversion` 2.10. Three
  readings set the refusals. The functions return a fraction, not a
  percentage. With the profiler off every function reads 0 called or
  not, and one with no branches reads branches 1, so an off profiler
  is refused by reading the setting, never measured as 0%; a profiler
  missing from `shared_preload_libraries` is refused too, because its
  profile is per session and the teardown's session sees none of the
  suite's. postgis owns 22 PL/pgSQL functions in a non-system schema,
  which is why extension members (`pg_depend` `deptype 'e'`) are
  excluded. A function the extension cannot read is listed with its
  error and reds the leg; it is never dropped from the total.
  `plpgsql` is exact: executed statements over all statements, the
  statements being the extension's own denominator, the rows of
  `plpgsql_profiler_function_statements_tb`. `plpgsql-branches` is
  **statement-weighted**: the extension returns each function's branch
  fraction without the branch count behind it, so each fraction is
  weighted by its function's statement count. A branch-free function
  (fraction 1 with no `IF`, `CASE` or loop) reads 1 whether or not it
  ran and is left out of the branch total; one whose only branches are
  exception handlers, all taken, is read the same way and left out,
  which never raises the total. The leg's log lists every function's
  figures beside the totals, so the totals can be checked by hand.
  The two legs have no upload format (plpgsql_check numbers lines
  within a function, not a file): the report step names them and never
  counts them as writers, so a tree whose only legs are these uploads
  nothing and the badge job skips. The `test` task runs once for every
  leg that reads it: `coverage-measure.sh` walks it and its depends
  (depends first, each task once, each script from its own task's
  `dir`) and runs that walk under the union of the selected legs'
  variables. The python leg reads the same run, so a `test` task whose
  suite sits in a depend now has that depend measured for Python too,
  where it used to run the task's own `run` alone.
  *Reopen the weighting:* plpgsql_check exposing its branch counts.
  **The static check (#1605) rides the same report.** In the same
  teardown call and session, the helper asks
  `plpgsql_check_function_tb` about every listed function (a trigger
  function against each table its triggers sit on, since its `NEW` and
  `OLD` are that table's rows) and writes the findings beside the
  coverage reading; the reader reds every finding by function and line
  before it judges the floor, and refuses a reading with no findings
  answer. There is no second variable, report or record line. Measured
  2026-11-20 at 2.10.16 on the proving repository's runner gate: the
  checks and their defaults are `fatal_errors` (on), `other_warnings`
  (on), `extra_warnings` (on), `performance_warnings` (off),
  `security_warnings` (off) and `compatibility_warnings` (off), with
  `all_warnings`, `without_warnings`, `use_incomment_options` (on),
  `incomment_options_usage_warning` (off) and `constant_tracing` (on)
  beside them. The belt turns every check on and fatal errors off, so
  every error is reported rather than the first. Observed, by check:
  fatal errors, a missing column (level `error`, SQLSTATE 42703);
  other warnings, an unused variable (level `warning`); extra warnings,
  unreachable code (level `warning extra`); performance warnings, a
  routine marked `VOLATILE` that could be `IMMUTABLE` (level
  `performance`, no line); security and compatibility warnings
  reported nothing on the probes or the proving repository. Every
  level is red: a check is on so that what it reports is held. The
  proving repository: 78 checks (six trigger functions across their
  tables), one finding, an extra warning; its detail is that
  repository's own issue. Suppression: an in-comment option in the
  function body, `-- @plpgsql_check_options: <check> = false`, turns
  that check off for that function, and holds with every check asked
  for (measured); it is the only suppression. The helper reads each
  function's source from the database (`pg_proc.prosrc`, never a
  tracked file) and records every options line that turns a check off;
  the reader prints every suppression on every run, by function and
  line, and reds one whose options line has no comment line directly
  above it saying why. A line that only turns checks on is not a
  suppression; `without_warnings = true` is one. The positional pragma
  `plpgsql_check_pragma('disable:<check>')` did not suppress a finding
  in a declaration before it (measured), and is not the mechanism.
  **Rejected, so it is not re-proposed:** adding the extension to the
  published database image (a test tool in a production image); a
  report the repository computes itself (the number would be the
  repository's claim, not the belt's measurement); pgTAP (a test
  framework, where this is a measurement of the tests a repository
  already has); for the branch total, an unweighted mean of the
  functions' fractions, the lowest function's figure, and a branch
  count parsed from the function source (a second PL/pgSQL parser
  beside the one the extension already is).
- **Rust branch coverage in the gate** — skipped, for now
  (#1359, 2026-11-17). `cargo llvm-cov --branch` needs a nightly
  toolchain: measured 2026-11-17 on a one-function crate, the dated
  nightly `nightly-2026-07-20` reported 4 branches, 1 covered (25%),
  while stable 1.97.1 refused the flag and stable without it reported
  a branch count of 0 and a percent of 0. A second instrumented test
  run on a nightly would double a Rust repository's test time on every
  pull request and put a nightly in the deterministic gate, so the
  gate keeps its line leg and the branch figure is measured on the
  Monday cron: `audit:coverage-branches` in `repo-audit.yml`, on the
  nightly the repository already declares as `FUZZ_TOOLCHAIN` (no new
  toolchain), with its own build over the workspace, the pgrx
  extensions held out as the `rust` leg holds them (one function in
  `mise/coverage-measure.sh`; #1653 took the `rust` leg onto the
  repository's own run, which a nightly build cannot share). Its floor is a second
  line of `.coverage-floor`, `rust-branches <percent>`, derived by
  `coverage:adopt` from a measurement under the same law and band as
  the main floor; the release path carries it unchanged, since it
  measures on stable. A repository with no such line, or no
  `Cargo.toml`, skips clean. A measurement with no branches at all is
  refused rather than floored, because 0 of 0 is what an
  uninstrumented run reports. Rejected, so it is not re-proposed:
  waiting for branch coverage on stable, which has no date. *Reopen:*
  `--branch` working on a stable toolchain — then the cron leg is
  replaced by one flag on the gate's `rust` leg, and the
  `rust-branches` line folds into the main floor the way
  `python-branches` does.
- **A pgrx extension's coverage through `cargo llvm-cov --workspace`**
  — replaced (#1656, 2026-11-19). #917 put the extension into the rust
  leg so the floor would not omit a published crate, but an extension's
  functions run in the Postgres backend, which that run never collected
  from: measured on #1652, every extension line read uncovered (0 of 18
  on release-lab, 0 of 143 on edtf). The same suite built instrumented
  (`cargo llvm-cov show-env`, a target directory of its own, an
  absolute `LLVM_PROFILE_FILE`) writes backend profiles that survive
  pgrx's own `pg_ctl stop -m fast`: 14 of 18 and 143 of 143, within a
  second of the plain run cold and warm. So `test:pgrx` is the measured
  run where a repository holds a floor: each major prints
  `pgrx-pgNN <percent>` and holds the floor by itself, in its own gate
  job; the rust leg leaves the extension out; `coverage-measure.sh pgrx`
  gives `coverage:adopt` and the release path the same figures.
  Rejected, so they are not re-proposed: a second instrumented run of
  the suite beside the plain one (one run, the owner's ruling); one
  major standing for all (false the day an extension carries per-major
  code); merging the majors into one figure (profiles are per binary,
  and a union hides which major is under-tested); leaving the crate in
  the rust leg too (counted twice, once as zero). *Reopen:* a pgrx
  release whose test harness stops the server in a way no profile
  survives.
- **gocoverageplus for the Go branch figure** — skipped (#1546,
  2026-11-19), measured beside gobco on stele (the table under gobco's
  entry). It crashed on every stele profile under Go 1.27: its pinned
  `golang.org/x/tools` v0.26.0 cannot load the toolchain's packages
  (`internal error: package "encoding/base64" without types was imported
  from ".../internal/dsse"`), exit 1, no report. And what it calls a
  branch is one per `if` line, covered once the line runs, so a
  multi-condition statement is one branch (its own README): on the
  planted module it read 3/3, 100%, over an untaken `else`, an untaken
  `if` path and a half-taken `a && b`. That is block coverage, which the
  issue rejected as a branch figure. *Reopen:* a release that runs every
  stele package on the belt's Go and reports the planted `a && b` at
  condition level.
- **`lint:belt-shell` (shellcheck/shfmt over the belt's own task
  bodies)** — **this skip was revoked and the task built (#619,
  2026-08-20).** The entry stays here rather than being deleted, because
  the reasoning that skipped it belongs beside the reasoning that
  reversed it. The gap was stated correctly and remains the entry's
  value: `lint:toml` lints the *container* (taplo exits **0** on a task
  body containing an unclosed `[[`, a missing `fi` and
  `rm -rf $UNQUOTED` — a TOML string is a string under every
  configuration, so this was never a config gap); `lint:shell` selects
  with `shfmt --find`, which identifies by shebang or extension and
  returns zero hits for the file; `lint:shell-embedded` globs workflows
  and composite actions only; and `lint:bash-portability` reads the same
  two sources.
  What was wrong was the disposal — "building it would mean a third
  extractor … #392 deletes the need for all three". The port completed
  and the need did not go. #392 replaced the bash that made JUDGMENTS
  with `stele` lines; orchestration bodies were never in its scope,
  which its own boundary states ("not the belt"). 105 task bodies
  remain, they ARE the belt, and "a third extractor is evidence the
  layout is wrong" does not survive the question the org asks of any
  reach-shaped exception: the code did not move, so the argument bought
  nothing except the surface staying unlinted for another four months.
  The constraint list recorded here is exactly what the build then met,
  and every item held: strip tera `{% raw %}`; extract through a real
  TOML parse (two bodies use `"""` and carry `\\.` and `\\"`) with a
  separate line scan for anchors, since `tomllib` gives no line numbers;
  single-line `run =` forms that shfmt restructures into block form; and
  hand shellcheck its settings explicitly, because a
  discovered `.shellcheckrc` is not picked up for files written to a
  temp directory — first as literal flags, and since #696 as
  `--rcfile mise/shellcheckrc`, the same level from the one delivered
  definition and also immune to a `$HOME/.shellcheckrc`, which a temp
  path DOES reach. So was the disproved suspicion: shfmt and shellcheck do
  **not** conflict on unquoted `${var}` inside `[[ ]]`.
  Two of the numbers did not survive. The sampled `fix:input-forwarding`
  parse errors were the tera markers, as recorded — an extractor
  artefact, not a defect in the body. And 161 SC2312 was an artefact of
  modelling the wrong interpreter: mise runs a body as
  `bash -euo pipefail -c`, under which an assignment from a command
  substitution does not mask a failure at all. The honest count was 79.
  It is now 0, along with the SC2249 set, which closes #82 finding 2 on
  this surface rather than leaving it live.
- **cffconvert** — `CITATION.cff` validation. Not needed, and recorded
  now because the scaffold previously cited a verdict that was never
  made (#316): the file is generated — `fix:citation` renders it from
  `REUSE.toml`, `lint:citation` diffs the committed file against a
  fresh render — so the generator is its own validator, and a schema
  checker would only re-verify constants the generator wrote. *Reopen:*
  hand-authored CFF fields beyond `title`, or a consumer requiring CFF
  features the generator does not emit.
- **ClusterFuzzLite** — unnecessary for any targeted score, with a
  real cost surface ([`slsa-reference.md`](slsa-reference.md)).
  *Reopen:* fuzzing becomes a target (Best Practices beyond the
  current aim).
- **harden-runner** — retracted deliberately after measured breakage;
  required by nothing the org targets; the full argument lives in
  [`release.md`](release.md) ("No runner-hardening agent"). *Reopen:*
  SLSA promotes hermeticity into an actual level — and then derive
  allowlists from audit data, never by construction.
- **gittuf** — v1.2 names it explicitly as an implementation route for
  Identity Management and Protected Named References, and the verdict
  survives that: it substitutes for the ruleset half the org already
  satisfies platform-anchored, **emits no source VSAs** (so it could
  never have moved the level), and its self-held threshold keys
  reintroduce the key custody the keyless design removed — a threshold
  root with one maintainer is a threshold of one
  ([`slsa-reference.md`](slsa-reference.md)). *Reopen:* a second
  maintainer AND a platform-compromise threat model above the org's
  risk line.
- **slsa-github-generator** — deprecated upstream as of 2026-08-07;
  never adopted; the signer split provides the property it existed
  for.
- **upstream `source-tool`** — stood up and proven in the lab, parked
  on four upstream defects, ultimately not adopted as issuer: the org
  built its own emitter (#207). Lives on as a *candidate cross-check*
  of our VSAs, never their issuer — watched as #199.
- **ty, for Python type checking** (#1604, 2026-11-19) — the owner's
  first choice, measured against mypy and pyright (the mypy entry under
  Adopted holds the table). It passes every belt prior: `aqua:`,
  sha256 per asset and a verified GitHub attestation, every one of its
  150 rules holdable at error, a missing or unparsable config refused
  (exit 2), an unknown rule name red (exit 1), `--config-file`
  replacing discovery, and a ruleless or unused `# ty: ignore` red. It
  reports 1 of the 14 wrong-type bugs, because it follows the gradual
  guarantee: an `Any` is never reported where it is used, and no rule
  refuses one. It is also ten times faster. *Reopen:* a ty rule that
  refuses `Any` at a use site (or an explicit `Any`), measured against
  the same 14 pre-fix helpers.
- **pyright, for Python type checking** (#1604) — 8 of the 14
  wrong-type bugs at `strict` with every rule it leaves off turned on,
  and it reports *Unknown* but never explicit `Any`, so `json.loads`
  goes unseen unless the parameter was a bare `dict`. It also fails
  the install prior: it is an npm package, the belt carries no Node
  (tsc is native), and the PyPI wrapper downloads Node and pyright at
  run time. *Reopen:* a native, checksummed pyright release.

## Watched, as filed issues

The trigger lives on the issue, not duplicated here:
[#124](https://github.com/monumental-archive/.github/issues/124)
(GUAC — blast-radius insufficiency, not time),
[#125](https://github.com/monumental-archive/.github/issues/125)
(BuildEnv L2+ — GitHub shipping runner attestation),
[#126](https://github.com/monumental-archive/.github/issues/126)
(Source L4 — a second maintainer, nothing else),
[#168](https://github.com/monumental-archive/.github/issues/168)
(PVTR — recurring Baseline drift),
[#199](https://github.com/monumental-archive/.github/issues/199)
(source-tool as cross-check).

## Neither adopted nor skipped: unreachable

- **`SLSA_BUILD_REPRODUCED`** — requires provenance from two or more
  independently operated build platforms. `repro-check` rebuilds on
  the same platform *by design* (skew-proofing is what makes a
  mismatch mean nondeterminism), so this architecture cannot earn the
  property and nothing here may ever label it so
  ([`slsa-reference.md`](slsa-reference.md)). Recorded as unreachable
  rather than watched: no trigger short of a second build platform
  changes it, and that would be an architecture decision, not a tool
  adoption.
- **`SLSA_SOURCE_TWO_PARTY_REVIEWED`** — headcount-blocked, the Source
  L4 boundary; watched via #126 rather than here.

## The bash-shaped exceptions after the port (#398, closed 2026-08-19)

The port's close-out asked, of every suppression whose stated reason
was bash: does the reason still hold? Measured against this tree, item
by item. The in-scope test was #398's own: an exception survives only
if its reason is about the rule, never about reach.

- **`SHELLCHECK_EXCLUDE` (mise/embedded_shell.py) — kept, reason
  re-measured live.** The premise "no shell in `run:` blocks" did not
  arrive: 98 multi-line `run:` blocks remain in the canon's workflows,
  deliberately — the port moved *logic* to stele and left
  *orchestration* here (the scope boundary in stele's charter: step
  bodies became one `stele <verb>` line where a verb exists; staging,
  artifact plumbing and commit points are workflow orchestration and
  stay). Every excluded code is an artefact of `${{ }}` substitution
  or step `env:` supply, which that orchestration still exhibits.
- **yamllint `indentation.check-multi-line-strings` — stays false**,
  same measurement: the multi-line `run:` blocks it would misjudge
  still exist.
- **`lint:shell-embedded` — kept as is.** The #398 inversion (assert
  every `run:` block is a single line) presumed an end state the org
  decided against; asserting it would red the canon's own publish
  workflow. The task still lints a live surface.
- **`lint:bash-portability` — already gone**; nothing to decide.
- **The `set -e` class (#82 finding 2) — narrowed, not closed.** Task
  bodies that became one `stele` line return real errors; multi-line
  orchestration bodies remain and carry their own guards. The mise
  `shell =` cure stays refused for the reason recorded at #82.
- **Coverage ceiling — resolved by relocation**; the kcov entry above
  carries it.
- **git-cliff — already retired**; `release:preview` runs
  `stele derive version`, `cliff.toml` markers are gone.
- **`security/workflow-permissions.py` — ported and deleted**
  (2026-08-20). This entry previously recorded it as kept, on the
  grounds that porting the caller/callee join was a stele change and
  stele's port was closed. It was reopened as stele#148 with its own
  justification — the join is evidence-layer logic by the port's own
  test — and `stele assert permissions` replaced the 437-line script
  whole, with the three org literals it carried becoming the assert
  policy's `permissions` section. Ruff stays: see the entry above.

## The belt's blind spot is repositories unlike itself (2026-08-24)

Not a tool verdict but a coverage one, recorded here because it is the
standing reason a green canon gate is weaker evidence than it looks.

Four defects closed in three days — #860, #892, #905, #911 — were one
finding wearing four costumes: **each existed only outside the tree the
belt is developed in.** The canon tracks markdown and shell, so
`fix:markdown` and `fix:shell` never met an empty file list. It tracks
no `Cargo.toml`, so `fix:rust` never met its own formatter's output.
It has a level stub and a complete `REUSE.toml`, so `derive-badges.sh`
and `citation.sh` never met an absent field. Every one of them was
green here and broken in a consumer, and none was visible to reading —
each needed a repository shaped differently from this one.

`lint:scaffold-assembly` (#877) exists to shrink exactly this, and it
does: it assembles a repository from `scaffold/` and runs the org's own
fixers and gate over it, which is how two of the four were found. The
residual it cannot reach is worth naming, because its green is
otherwise easy to over-read:

- **Toolchain legs it does not pin.** The belt pins no `rust` or `go`,
  so the assembled repository never exercises those tasks. #892 lived
  there and the check prints that exclusion on every run.
- **Helper inputs the scaffold deliberately does not carry.**
  `REUSE.toml.stub` is declined by the assembly on purpose (its holes
  are the licence identity only the repo can choose, #214), so
  `fix:citation` returns at its first guard and #911's three sites were
  never entered. `scaffold/` ships no workflows, so `derive-badges.sh`
  returns before #905's.
- **Shapes a stub cannot have.** A repo with a Cargo workspace whose
  Rust is not yet canonical, a repo with no level stub, a repo whose
  `REUSE.toml` is half-filled — these are states of a real consumer
  mid-conformance, not of a freshly assembled scaffold.

So the honest reading: the assembly proves the scaffold is *runnable*,
not that the belt is *correct on every consumer*. The complement is
what actually found the other two — run the belt against real consumer
clones (`edtf`, `iiif-server`) with the belt redirected at the branch,
in both directions, before believing a fixer works. No check currently
does that automatically, and standing one up would need those repos'
trees at gate time, which is the reason it does not exist rather than
an oversight.

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
| rekor-monitor (org-owned workflow) | Transparency-log watch for forged signer identities — the reason the id-token rule was narrowed |
| Renovate (org preset, zero-age canon fan-out) | Pin freshness; the release-age quarantine |
| OSV via `audit:blast-radius` | Malicious-package and advisory sweep over every published SBOM |
| cargo-llvm-cov + `.coverage-floor` ratchet | Line-coverage floor in the gate (`coverage:check`), codecov badge feed off-gate |
| cargo-fuzz (`cargo:` backend, sanitizer none) | Fuzz targets: build proof in the gate (`lint:fuzz-build`), bounded runs on the cron (`audit:fuzz`) |
| reuse (`pipx:` backend via aqua-backed uv) | REUSE-spec compliance in the gate (`lint:reuse`), pre-registration |
| uv (`aqua:astral-sh/uv`) | The installer behind every `pipx:` belt tool; carries the org's release-age floor into Python |
| biome (`aqua:biomejs/biome`) | JS/TS/JSON lint + format + assist in the gate (`lint:biome`) at `preset: "all"` |
| ruff (`aqua:astral-sh/ruff`) | Python lint + format in the gate (`lint:python`) at `select = ["ALL"]` + preview |

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

Defaults left alone, having been checked rather than assumed:
`keyring-provider` is already `disabled`, `no-index` false with
`allow-insecure-host` empty, and `resolution = "highest"` stays — the
conservative-looking `lowest` would pin the closure to ancient releases,
which is worse. `no-binary` is refused outright: building from source is
a larger attack surface, not a smaller one.

The timing matters. The belt carries one `pipx:` tool today; **yamllint
and sqlfluff are both pipx-only and both already agreed**, so this path
was about to carry three dependency closures instead of one.

**ruff at `select = ["ALL"]`, and the one file it was adopted for**
(#82). One aqua-backed Rust binary that lints *and* formats Python and
needs no Python interpreter to do it — so the org gains Python coverage
without Python joining the belt. It was adopted against a real hole, not
a hypothetical one: `security/workflow-permissions.py` computes the
caller/callee permissions join that guards the Build L3 boundary, and it
was the one tracked file in a language no belt tool covered. Nothing
checked it.

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
biome, a nested `scaffold/ruff.toml` is harmless: ruff's config
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
executable bit burned v1.5.0 with exit 126 after an immutable tag — but
it covers shell only, and ruff extended that hard-won rule to Python and
immediately found the same defect.

The remaining ~28 findings were fixed, not silenced, and the refactor was
verified behaviourally rather than by inspection: the pre-change
`requirements` and `check` outputs were captured and diffed against the
post-change ones, byte-identical both times. That caught one real bug the
refactor introduced — collapsing the workflow-level grant with `or`
silently discarded an explicit `permissions: {}`, because an empty dict
is falsy and that empty grant is the meaningful org-wide default.

**biome at `preset: "all"`, and the two rules that are not** (#82).
Adopted as the org's JS/TS/JSON layer: one aqua-backed binary, checksums
and GitHub attestations on all seven lock platforms, rules compiled in,
config read as data. Pinned to the explicit `aqua:` backend rather than
the `biome` short name, which resolves to aqua **and** npm — an npm
resolution records version only in the lock, no checksum. All 525 rules
across all eight groups are on, nursery included: nursery is outside
semver by Biome's own statement, but a pinned version bumped through
Renovate turns that into a red bump PR, never a surprise in `main`.

Two deliberate departures, both settled against evidence rather than
taste:

- **`assist/source/useSortedKeys` is off.** It does not fire at Biome's
  own default assist level — `preset: "all"` is what turns it on — and
  every peer tool defaults the same way: ESLint's `sort-keys` is off and
  frozen, Prettier preserves authored order and needs a third-party
  plugin to sort. The ecosystem's convergent answer for hand-authored
  config is `sort-package-json`'s: keep the conventional lead keys,
  sort the bulk underneath. Alphabetising top-to-bottom here put `name`
  last in the workflow-template metadata and buried `extends` — the line
  that says what a Renovate config inherits — under a twenty-line
  `customManagers` block. The fix is safe and automatic, so this is not
  a migration-cost objection; it is that four independent tools ship the
  behaviour off. *Reopen:* a repo whose JSON is generated rather than
  authored, where stable key placement beats reading order.
- **`indentStyle` is space/2, not Biome's tab default.** Tabs cannot be
  uniform here: YAML forbids tab indentation by spec, which is 49 of the
  canon's 153 tracked files. Space is the only setting that can hold
  across every format, and `lint:shell` already overrides shfmt's own
  tab default to 2-space for the same reason. Note this is practice
  plus two tool settings, **not a written convention** — the org has no
  stated indentation rule, and `.editorconfig` is where one would live
  if editorconfig-checker lands.

One trap, found by running the standup rather than reading about it: the
scaffold copy must be `scaffold/biome.json.stub`, never `biome.json`.
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

## Skipped, with rationale and reopen trigger

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
- **cargo-fuzz, the nightly/sanitizer half** — the adopted entry above
  is deliberately `--sanitizer none` on the repo's pinned STABLE
  toolchain (#316): ASan needs nightly, and a nightly in the gate means
  either a second toolchain pin per repo or mid-run rustup — both
  refused (the coverage:check precedent). libFuzzer without a sanitizer
  still finds panics, which in memory-safe Rust is the live failure
  class. Like cargo-pgrx, the `cargo:` backend is a documented
  exception to aqua-first: version-pinned, no attestations. *Reopen:*
  an FFI-heavy crate joins the org (ASan then earns its nightly), or
  cargo-fuzz ships sanitizer support on stable.
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
  it survives. *Reopen:* kcov (or a bash-coverage peer) shipping
  checksummed cross-platform binaries through aqua — at which point
  re-add a `.coverage-floor` and the shield returns by derivation.
  Note the separable half: **bats and shellspec are both aqua-backed
  and belt-clean**, so adopting a test framework is not blocked by any
  of this and is tracked on its own.
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

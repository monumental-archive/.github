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
| jq (`aqua:jqlang/jq`) | JSON on the command line for eleven belt tasks, one of them in the gate |
| pinact (`aqua:suzuki-shunsuke/pinact`) | The version-comment half of the pinning convention: `lint:action-pins` offline, `audit:action-pins` online, `fix:actions` |
| biome (`aqua:biomejs/biome`) | JS/TS/JSON lint + format + assist in the gate (`lint:biome`) at `preset: "all"` |
| ruff (`aqua:astral-sh/ruff`) | Python lint + format in the gate (`lint:python`) at `select = ["ALL"]` + preview |
| golangci-lint (`aqua:golangci/golangci-lint`) | Go lint + format in the gate (`lint:go`) at `default: all` + curated disables; gofumpt (extra rules) + gci as its formatters |
| govulncheck (`go:` backend, repo-side pin) | Call-graph-aware Go advisory scan (`audit:go-vulns`, network — Monday cron) |
| yamllint (`pipx:` backend via aqua-backed uv) | YAML lint in the gate (`lint:yaml`), full rule inventory at error + `--strict` |
| editorconfig-checker (`aqua:`, binary `ec`) | Whole-file hygiene in the gate (`lint:editorconfig`) against `.editorconfig`, the org's one written indentation rule |
| gitleaks (`aqua:gitleaks/gitleaks`) | Secret scanning at commit depth in the gate (`lint:gitleaks`, full history, `--redact`) |
| hadolint (`aqua:hadolint/hadolint`) | Dockerfile lint in the gate (`lint:hadolint`), every severity fails (`--failure-threshold style`) |
| cargo-machete (`github:` backend, upstream sha256s) | Unused Rust dependencies in the gate (`lint:machete`, static) — proven first in a consumer |

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

- `lint:action-pins` — `-fix=false -no-api`, so it asserts a
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

The interesting part is why nobody noticed. `lint:belt-available` looks
like the check that would catch it, and is not: its tool list is
**hand-maintained**, so it proves the tools someone remembered to list
are present, and says nothing about the tools a task actually invokes.
jq was never missed by a check; there was no check.

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
SBOMs vs source reachability). *Reopen:* upstream shipping attested
binaries, or an aqua package appearing.

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
  tab default to 2-space for the same reason. This was practice plus
  two tool settings until #403 item 2: the written convention now
  lives in `.editorconfig`, enforced by `lint:editorconfig`.

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

`lint:yaml` requires a tracked `.yamllint.yaml` once YAML is tracked —
the ruff/biome/golangci trap in a fourth costume: without a config
yamllint silently falls back to its `default` preset, a fraction of the
org policy, on a green gate. Copy `scaffold/.yamllint.yaml`. No `fix:*`
sibling exists to wire: yamllint ships no writer, so conformance is
hand edits by design (prettier's verdict already records the YAML
formatting gap). pipx-only — no aqua package exists (404 in the
registry; pure Python, no binary releases) — making it the second
`pipx:` exception after reuse, riding checksummed uv under the same
`UV_*` floor environment, pinned in `[tools]` before first install so
the lock entry is never bare.

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
than reformatted, on the fixture-is-not-evidence rule. *Reopen:* ec
growing a formatter-aware indent check.

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
  it survives. *Reopen:* **the canon's bash goes away** (#392, decided
  2026-08-15 — a Go tool in its own repo), at which point `go test
  -cover` supplies the number, the canon gains a `.coverage-floor` and
  the shield returns by derivation. That, not aqua packaging, is now the
  live trigger: kcov shipping checksummed cross-platform binaries would
  also unblock it, but nobody is waiting on that. The retirement of this
  entry and everything that hangs off it is tracked in #398.
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
- **`lint:belt-shell` (shellcheck/shfmt over the belt's own task
  bodies)** — skipped, and recorded because the gap it would close is
  real and will otherwise be rediscovered. `mise/config.toml` holds 78
  task bodies, **~2409 lines of bash**, and every belt linter that could
  read it is structurally blind: `lint:toml` lints the *container*
  (taplo exits **0** on a task body containing an unclosed `[[`, a
  missing `fi` and `rm -rf $UNQUOTED` — a TOML string is a string under
  every configuration, so this is not a config gap and cannot be closed
  by one); `lint:shell` selects with `shfmt --find`, which identifies by
  shebang or extension and returns zero hits for the file;
  `lint:shell-embedded` globs workflows and composite actions only; and
  `lint:bash-portability` reads the same two sources. The census in #392
  was short by this whole population — ~8001 lines of bash org-wide, not
  5316. It is skipped anyway, because building it would mean a **third**
  extractor after `embedded-shell.py` and actionlint's own shellcheck
  delegation, and writing a parser to un-hide our code from our own
  tools is evidence the layout is wrong rather than a task to complete.
  #392 deletes the need for all three. Measured before deciding, so the
  probe is not written again (#397): at the `.shellcheckrc` bar the run
  reports 194 findings and 70 of 78 bodies not shfmt-clean, but **three
  sampled findings were all false positives** — `audit:badges`
  SC2221/SC2222 are alternatives within one branch routing to identical
  code, `fix:input-forwarding`'s parse errors came from feeding tera's
  `{% raw %}` markers to shellcheck, and `audit:blast-radius` SC2034 is
  the trailing field of `read -r id name ver where class kind`, required
  or `class` absorbs the last column. The state is *unmeasured*, not
  known-buggy. The untriaged remainder — 161 SC2312 (masked return
  values) and 11 SC2249 (`case` with no default branch), against 11 of
  78 bodies carrying `set -euo pipefail` — stays live as #82 finding 2.
  Constraints if one is ever written regardless: strip tera `{% raw %}`;
  extract through a real TOML parse (two bodies use `"""` and carry
  `\\.` and `\\"`) with a separate line scan for anchors, since
  `tomllib` gives no line numbers; 13 tasks use single-line `run =`
  forms that shfmt would restructure into block form; and pass
  `--enable=all --external-sources` explicitly, because `.shellcheckrc`
  is not picked up for files written to a temp directory. One suspicion
  tested and disproved: shfmt and shellcheck do **not** conflict on
  unquoted `${var}` inside `[[ ]]` — shellcheck exits 0 on shfmt's
  preferred form. *Reopen:* #392 stalling, at which point the honest
  interim is triaging the SC2312 and SC2249 sets by hand, still not
  building the linter.
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

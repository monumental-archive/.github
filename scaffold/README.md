# New-repo scaffold

The canonical stubs a repo needs to join the org. Copy these to the repo
root at creation or migration time — they are the *entire* per-repo
footprint of the governance stack.

What is deliberately NOT here: the config of any tool only the belt runs.
clippy, rustfmt, pinact, typos, ruff, biome's RULES, yamllint, rumdl and
sqlfluff are configured from `mise/` and passed
to the tool by the task that runs it (`ORG_BELT_DIR`, #445), so a repo
carries nothing to drift and gets the current rules at the canon SHA it
pins. Everything below is here because something OUTSIDE the belt reads
it — an editor, GitHub, a git hook, the release script — or because its
CONTENT is genuinely per-repo. `deny.toml`'s skips describe one tree;
`.golangci.yml` names the module path (gci's import prefix) and, in
stele, wrapcheck globs for that module's own packages. The belt can
deliver a config; it cannot invent a repo's identity. A repo may still
keep its own `_typos.toml` for domain jargon: typos merges it with the
org vocabulary the belt supplies.

`.sqlfluffignore` sits on that same `_typos.toml` footing, and it is not
in this directory because no repo needs one by default. Measured on the
belt's pin, sqlfluff 4.3.0, against the delivered `--config`: the file
is honoured **even when the path is given explicitly**, which is the
question worth asking, since `lint:sql` hands sqlfluff a `git ls-files`
list rather than letting it walk. A named file drops out of the run with
a `WARNING` naming the ignore, exit 0. That is the escape for a
GENERATED file only — a pgrx schema snapshot or any other transcript of
what a tool emits, where conforming the file to a style rule is what
breaks it, on the standing rule that a file reformatted away from what
its writer emits stops being evidence. It is repo content with a written
reason per entry, never a silent lowering, and never a home for findings
the repo simply has not fixed. It is not a seam: nothing in the belt
reads it, nothing in a repo declares anything to the belt, and the org
ships no escape mechanism of its own (#694).

Suppressing one finding rather than one file is sqlfluff's per-line
`-- noqa: CODE`, and it carries **one trap, measured in the same probe**:
everything after `noqa:` is parsed as a comma-separated list of rule
codes, so a reason written on the same line is read as further codes and
the whole suppression is silently discarded.

| line ending | findings |
| --- | --- |
| `DEFAULT FALSE` | 1 |
| `DEFAULT FALSE  -- noqa: RF04` | 0 |
| `DEFAULT FALSE  -- noqa: RF04 — real column` | 1 |
| `DEFAULT FALSE  -- noqa: RF04 real column` | 1 |

No warning is emitted — grepping the run for `warn|unknown|invalid`
returns nothing — so the only symptom is a finding that never went away.
The em dash is not the culprit; plain words behave identically. **The
reason therefore goes on the line ABOVE, as a `--` comment, and the
`noqa` carries nothing but its codes** (measured green in that shape).

One consequence to know before reaching for it: a belt-delivered config
is the SAME file for every repo, so anything genuinely repo-shaped in it
has nowhere to go. biome's `domains` block was the live example, and
monumental-archive is the repo that fired it (#695): a React app drew
ten `useQwikValidLexicalScope` findings, a Qwik rule judging React.
A `biome.json` stub is therefore back in this directory — but as an
identity declaration, not a config. The org's rules did not travel with
it and never will; see the entry below.

- `biome.json.stub` — copy it to the root as `biome.json`. It carries
  the repository's biome DOMAINS, and nothing else (#695). The `.stub`
  suffix is the `REUSE.toml.stub` reason exactly: biome walks the tree
  regardless of the path it is handed and treats any `biome.json` below
  a root as a nested root config, which hard-fails every invocation,
  including ones that never touch this directory.

  biome sorts its framework and library rules into domains, and under
  the org's `preset: "all"` every one of them runs whatever the tree
  contains. Declaring what you are does not fix that — measured, adding
  `react` changed nothing at all — so the belt writes `"none"` for every
  framework domain a repo did NOT claim, and computes the whole block
  from this file. Name each framework the repo actually uses:

  ```json
  { "linter": { "domains": { "react": "all" } } }
  ```

  Eleven domains are yours to claim — `react`, `reactNative`, `solid`,
  `next`, `qwik`, `svelte`, `vue`, `drizzle`, `tailwind`, `turborepo`,
  `playwright` — with `"all"` the only value you may write. `project`,
  `types` and `test` are the org's level, not your shape, and naming one
  fails the gate; so does a rule, an `overrides` block, or anything else
  in this file. Claiming a framework you do not have is legal and simply
  turns rules on. NOT claiming one you DO have is not: if a
  `package.json` in the repo declares `react`, the gate makes you say
  so, because silence would turn react's rules off. Leave `domains`
  empty when the repo tracks no framework at all.

  Claiming a domain also decides its NURSERY rules (#720). biome's
  `preset: "all"` does not reach nursery, so the org names all 87 rules
  one by one — and a named rule beats its own domain being off, which
  meant a React repo drew Vue's and Playwright's nursery rules whatever
  it declared. The belt now switches those off for you: claim `react`
  and you keep react's six, lose the other frameworks', and keep every
  rule that belongs to no framework at all.

  What this file cannot do is scope a rule that has no domain.
  `noNodejsModules` is the one that bites a Node service: biome gives it
  no domain, so the answer is biome's own per-file directive in the file
  that needs it — `biome-ignore-all lint/correctness/noNodejsModules:`
  with the reason — decided in the repo, like every other exception
  (#694).
- `renovate.json` — extends the org preset
- `lefthook.yml` — pulls the org git hooks by remote
- no `committed.toml`: the commit canon is delivered (#576). A repo that
  restricts its commit SCOPES declares them as `ORG_COMMIT_SCOPES` in
  `mise.toml` `[env]` and the belt composes the two — the rules have one
  definition, the scope list stays repo identity
- `mise.toml` — repo-specific tools/tasks; the belt arrives globally
- `.golangci.yml` — the Go canon (#392): golangci-lint v2 at
  `default: all` with per-rule written disables, gofumpt (extra rules)
  and gci as formatters, and the depguard ban on `encoding/json`
  outside `internal/jsonx`. Copy it and fill the two `<module-path>`
  holes. `lint:go` fails the gate if Go is tracked without it —
  golangci-lint's default selection is five linters of ~105, so a
  missing config yields a green gate that checked almost nothing (the
  ruff trap, again). govulncheck rides repo-side: pin
  `"go:golang.org/x/vuln/cmd/govulncheck"` in `mise.toml` (the
  cargo-fuzz pattern; `audit:go-vulns` asserts it with the remedy).

  Two further Go checks need **no config and no stub at all**, because
  both are subcommands of the `go` the belt already pins (#445).
  `lint:go-tidy` runs `go mod tidy -diff` per tracked `go.mod` — it
  fails with the diff and writes nothing, and `fix:go-tidy` applies it.
  It catches what golangci-lint structurally cannot: golangci reads
  packages and never the manifest, so an import with no requirement, a
  requirement nothing imports, or a drifted `go.sum` are invisible to
  it.

  And **fuzzing costs a Go repo nothing to switch on**: write a
  `func FuzzXxx(*testing.F)` in any `_test.go` and the belt finds it by
  asking the toolchain, with no list to register it in. From then on
  `lint:go-fuzz-seeds` replays its seed corpus in the gate under the
  race detector, and the Monday `audit:go-fuzz` fuzzes it twice —
  plain for throughput, then `-race`, because the two instruments find
  different things and the plain pass is 31× faster (measured). What to
  fuzz is the repo's call: the surfaces worth targeting are the ones
  that parse bytes someone else produced.

  The loop matters more than the run. When the cron finds a crasher the
  engine writes the failing input into `testdata/fuzz/<Target>/`, and
  from that moment plain `go test` fails on it — so **commit that
  file**: it is the regression test, and the gate replays it forever
  after. `repo-audit.yml` keeps it as an artifact when the cron goes
  red, because the runner is ephemeral and a crash nobody can retrieve
  is a crash nobody can act on. The cron never pushes it for you.
  Seed corpora belong in `f.Add(...)` or `testdata/fuzz/<Target>/`, and
  should come from real artifacts the system has actually produced
  rather than hand-written examples — an input that was never emitted
  by the real thing proves little about the real thing.
- `tsconfig.json` — the TypeScript canon (#445, corrected by #699). It
  carries **every one of the org's strictness dials**, and that is not
  duplication. This file had the opposite instruction until #699 — carry
  no strictness at all, because `lint:types` passes every dial on the
  command line where a compiler flag beats the same key even under `-p`.
  That is true of `tsc` and false of every other type-aware tool, because
  those tools read this file and never see the belt's command line.
  Measured on monumental-archive, three states, one variable:

  | `tsconfig.json` | `eslint --max-warnings 0` |
  |---|---|
  | its own config, strictness present | 0 errors |
  | stripped, per the instruction this replaces | **935 errors** |
  | raised to the full org level | 0 errors |

  The 935 are typescript-eslint reading a weaker program —
  `dot-notation` 590 once `noPropertyAccessFromIndexSignature` is gone,
  `no-unnecessary-condition` 345 once the null-checking dials move.
  Nothing about the code changed. biome moves the same way: its `types`
  and `project` domains resolve the same TypeScript program. One fact,
  two readers, and this file is the only one both can see — exactly what
  `.editorconfig` already does for caps that belt formatters also
  enforce.

  `lint:tsconfig-dials` holds you to it, deriving the comparison from
  `mise/tsc-flags.txt` and nothing else. A dial stated WEAKER than the
  org's fails; a dial ABSENT fails, because absent is the whole defect.
  Stricter is never inspected — name dials the org does not, freely.
  In a monorepo, state them once in the root config and `extends` it
  from each project: the check reads what `tsc --showConfig` resolves,
  so inheritance counts and you never write them twice.

  The rest of the file is still the only thing the belt cannot invent —
  what the project *is*: which files it contains, server or browser,
  which module system, which type packages. Adjust `include`, `lib` and
  `types` to the repo and leave the dials alone. Two rules ride with it: **no
  `references`** (build mode refuses the org's flags, so a referenced
  project would be checked at whatever level the repo chose) and **no
  `allowJs`** (TypeScript refuses `isolatedDeclarations` alongside it —
  a TypeScript repo is TypeScript). Every tracked `.ts` file must land
  in some project or the gate says so. One thing that bites on first
  contact: under `module: nodenext` the belt's `verbatimModuleSyntax`
  refuses `export` in a package that never declared `"type": "module"`
  in `package.json` (`TS1287`). That is the flag working — it is the
  check that stops a package from silently being CommonJS while its
  source reads as ESM — so declare the type rather than lower the flag.

**Pick the licence — this is a step, not an afterthought (#214).** The
choice is per-repo (Rust repos conventionally dual-license
`MIT OR Apache-2.0`; the canon itself is 0BSD): land `LICENSE` at the
root, the matching text in `LICENSES/<SPDX-ID>.txt`, and fill
`REUSE.toml`'s `SPDX-License-Identifier`. With no licence the default
is all rights reserved — the opposite of publishing anything — and
`lint:licence` in the belt reddens the gate until the choice is made.

- `.bestpractices.json` — OpenSSF Best Practices pre-fill (#347). The
  badge app reads this file from the repo root and proposes answers, so
  registering a new repo starts from ~50 answers already made rather
  than a blank form — the *majority* still differ per repo and are left
  as `?`, so treat this as a head start, not a shortcut. Derived from
  the canon's
  earned entry ([project 14058](https://www.bestpractices.dev/projects/14058)),
  trimmed to answers that are true **org-wide because the controls are
  inherited**: the org rulesets and security configuration, the DCO, the
  shared gate, and the community health files this repository serves as
  org defaults (SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, GOVERNANCE,
  SUPPORT). Anything a repo could vary is `?`, which the app ignores, so
  a stale placeholder proposes nothing rather than something false.

  **Signed releases, SBOMs and attestations are deliberately `?`, not
  Met.** They are true of the canon and false of a repo on its first
  day, and a stub that asserts them would have every new repo claim a
  release it has never cut. Same for the whole `version_*` and
  `release_notes*` family. Inherited *controls* may be carried; earned
  *outcomes* may not.

  Three traps the derivation avoids, recorded so edits keep avoiding
  them:

  - **Baseline criteria must use the `osps_ac_01_01` key form**, never
    the `OSPS-AC-01.01` form the project download renders. The input
    whitelist is built from the criteria YAML keys, and a miss is
    skipped in silence — an unrenamed file drops all 64 Baseline
    criteria while the metal series fills normally. See
    [`docs/best-practices.md`](../docs/best-practices.md) for the
    source references and the one-line check.
  - `build_*` answers must NOT be copied from the canon (N/A there, Met
    in any repo the repro gate builds), and each `build_*` criterion
    hangs on its *own* escape clause — never write "no build system
    exists".
  - A consuming repo will legitimately **outscore** the conformance
    root — that is the design, not a regression.

Conditional stubs, wired per the runbook's "Wiring in a repository"
section (`docs/runbook.md`): the release/publish workflow templates for
versioned repos (there is no `cliff.toml` stub — git-cliff retired in
issue #507, and `stele derive version` carries the conventions),
`CITATION.cff` where citable —
rendered by `fix:citation` from `REUSE.toml`, and required by
`lint:citation` wherever the stub passes `mint-doi: true` (#316) —
`REUSE.toml` + `README-badges.md` + `SECURITY-INSIGHTS.yml` for the
badge surface, `CODEOWNERS` for reviewer routing.

Plus, from the Actions tab, the **Org CI gate** workflow template (or copy
`workflow-templates/ci.yml` and replace `$default-branch` with `main`).

Then once per clone:

```bash
mise trust && mise install && mise run hooks:install
```

And per migration: attach the org security configuration (transferred
repos do not inherit the default) and run `settings/repo-baseline.sh
apply`. Rulesets need nothing — they are org-level, scope `~ALL`, and
cover a repository the moment it lands.

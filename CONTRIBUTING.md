# Contributing

Thank you for your interest. These projects are maintained with limited
review bandwidth, so the process below keeps contributions useful for
everyone.

## Process

- **Bugs and ideas**: open an issue. Bug reports and comments are accepted
  in English.
- **Pull requests**: welcome for small, self-contained fixes. For anything
  larger, **please open an issue first** so the approach can be agreed
  before you invest time — unsolicited large changes may be declined
  regardless of quality.

## Requirements for acceptable contributions

Every change arrives by pull request — nothing lands on `main` directly —
and must pass the organisation's CI gate, which is the coding standard in
executable form:

1. Copy nothing, configure nothing: run `mise trust && mise install` in the
   repository and the full toolchain arrives pinned.
2. `mise run ci` locally is **exactly** the check CI runs. If it passes on
   your machine, it passes in CI.
3. **A change that adds or changes behaviour adds the check that holds
   it.** In repositories with a test suite that means a test that fails
   without your change; in configuration and policy repositories it may
   be a lint, an audit task or an assertion in a workflow — whatever
   makes the property you are relying on go red when someone later
   breaks it. As a matter of policy the gate's checks are the
   specification: functionality nothing enforces is functionality the
   next change may silently remove. A bug fix should add the case that
   reproduced the bug.
4. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org)
   with imperative, lowercase subjects (`feat: add the widget`). The
   `commit-msg` hook (`mise run hooks:install`) tells you immediately.
5. Pull requests are squash-merged: the **PR title becomes the commit
   subject and the PR body becomes the commit body**. Write them as the
   permanent history they will be.
6. **A citation of an org release in `docs/` names its repository beside
   the version**: `` `.github@v1.24.1` ``,
   `` `monumental-archive/stele@v0.2.2` ``, `stele v0.16.0`, or a link to
   the release itself. `audit:citations` walks exactly that form on the
   Monday cron and resolves each against *that repository's* tags, so a
   fabricated or mistyped version is a red audit rather than a reading
   exercise. A bare `vX.Y.Z` is prose about the world and is not walked
   — golangci-lint's version in a tooling note is not a claim about this
   org's record and needs no anchor — which is exactly why the anchor is
   required of the claims that are. Prefer the `repo@version` form: the
   walk is line-based and a line wrap cannot split it.
7. **Workflow shell**: `lint:workflow-shell` holds every `run:` block
   in a workflow, a composite action or a workflow template to **15**
   non-blank, non-comment lines, heredoc bodies included. A step is the
   least reusable and least testable place logic can live. The blocks
   already over the ceiling are listed by file and step name in
   `security/workflow-shell-debt.txt`, which only shrinks:
   `fix:workflow-shell` lowers a number or removes an entry, and nothing
   adds one. There is no per-block ignore.

   **Task bodies** are held to the same ceiling by `lint:task-shell`:
   a mise task's `run` body, in any tracked mise config, holds at most
   **15** lines counted the same way, a `run` list of commands being
   one body. The bodies already over it are listed by config file and
   task name in `security/task-shell-debt.txt`, which only shrinks the
   same way. `fix:task-shell` generates that file where none exists,
   in the pull request that takes the pin, and from then on only
   lowers a number or removes an entry. Where the logic goes, for a
   block or a body:
   - a block that **judges or emits SLSA evidence** moves to stele,
     one stele issue per capability;
   - **anything else** moves to a helper under the repository's own
     tests (the canon's, for the canon's workflows);
   - the block that replaces it is one call, and the old block and its
     debt entry are deleted in the change that replaces them.

   **A helper's language**: logic (parsing, deciding, comparing,
   talking to an API) is Python, stdlib only, so it comes under the
   coverage and mutation legs. Shell is for a sequence of commands, and
   for code that must run where Python is not guaranteed (inside a
   build container, or before any tool is installed). Shell has no
   coverage figure and no mutation tool in the belt, so logic written
   in it can be tested but never measured.

## Testing standard

Coverage measures execution, not verification: a line counts as
covered when a test runs it, whether or not any test would fail if
the line were wrong. A repository can reach 100% and still ship bugs
its suite cannot see. A good suite has six properties, and each is
held by a mechanism with an issue of its own. The issue closes on its
own work and adds the task that runs it to its row.

| # | Property | What breaks without it | Mechanism | Issue |
|---|---|---|---|---|
| 1 | Fails when any behaviour changes | covered lines nothing checks | mutation testing, weekly | Python: `audit:mutants` (mutmut; adopt with `[tool.mutmut]` in `pyproject.toml`) [#1354], Rust: `audit:mutants` (cargo-mutants; adopt with `.cargo/mutants.toml`) [#1355], Go: `audit:mutants` (gomu; adopt with an empty root `.gomuignore`) [#1356], TypeScript [#1358] |
| 2 | Takes every decision both ways | the untaken `else` | branch coverage | Python [#1331], Rust [#1359], TypeScript [#1357], PL/pgSQL (`plpgsql-branches`, plpgsql_check in the repository's test stack; adopt with one teardown call; its static check of every function body, every check on, rides the same report [#1605]) [#1547], Go: `audit:coverage-go-branches` (gobco's condition coverage, the `go-branches` floor; [tooling verdict][go-branches]) [#1546] |
| 3 | Varies its inputs | the input nobody wrote down | property and fuzz testing | Rust, Go and Python exist (`lint:fuzz-build`, `audit:fuzz`, `audit:go-fuzz`; Hypothesis in `prop_*.py` files, `test:python-properties` and `audit:python-properties`, [#1332]); TypeScript in `*.prop.test.*` files (fast-check, the repository's own; `test:typescript-properties` and `audit:typescript-properties`, [#1549]) |
| 4 | Asserts the exact outcome | tests green for the wrong reason | a written rule; property 1's mechanism catches a breach | this section |
| 5 | Runs on real artifacts, on both platforms | the fixture's imagined world | a written rule: a recorded real artifact outranks a hand-written fixture; the gate already runs on Linux and the Mac | this section |
| 6 | Reproduces each bug before the fix | the same bug, twice | the regression line in every defect's Done-when; a weekly report; a backfill per repository | [#1335], the canon [#1360], [signer#86], [iiif-server#212] |

[#1331]: https://github.com/monumental-archive/.github/issues/1331
[#1332]: https://github.com/monumental-archive/.github/issues/1332
[#1335]: https://github.com/monumental-archive/.github/issues/1335
[#1354]: https://github.com/monumental-archive/.github/issues/1354
[#1355]: https://github.com/monumental-archive/.github/issues/1355
[#1356]: https://github.com/monumental-archive/.github/issues/1356
[#1357]: https://github.com/monumental-archive/.github/issues/1357
[#1358]: https://github.com/monumental-archive/.github/issues/1358
[#1359]: https://github.com/monumental-archive/.github/issues/1359
[#1360]: https://github.com/monumental-archive/.github/issues/1360
[#1546]: https://github.com/monumental-archive/.github/issues/1546
[#1547]: https://github.com/monumental-archive/.github/issues/1547
[#1549]: https://github.com/monumental-archive/.github/issues/1549
[#1605]: https://github.com/monumental-archive/.github/issues/1605
[go-branches]: docs/tooling-verdicts.md
[signer#86]: https://github.com/monumental-archive/signer/issues/86
[iiif-server#212]: https://github.com/monumental-archive/iiif-server/issues/212

### A copied fixture keeps its source's licence

Property 5 means fixtures captured from elsewhere, and the line that
decides their licence is authorship, not origin. A fixture that holds
another work's source code or prose (a published crate, a bundle of
commits, a vendored file, documentation text) is that work's, so the
pull request that adds it also adds a `REUSE.toml` annotation for its
path, `precedence = "override"`, quoting the source's own copyright
line and licence expression, and the text of each licence it names
under `LICENSES/`. A capture of facts about another party (a tool's
listing of a package, a registry's index record, a log's checkpoints,
an API's status answer) holds no authored work and stays under this
repository's own annotation. Nothing in a file's bytes says which kind
it is, so review holds the annotation; `lint:licence` holds the texts,
red on any identifier a `REUSE.toml` names without its
`LICENSES/<id>.txt` ([#1589]).

[#1589]: https://github.com/monumental-archive/.github/issues/1589

### No number can be bought

- The mutation measure is "no unexplained surviving mutant", never a
  score. Each survivor is killed by an assertion, or recorded as
  equivalent, with a reason, at one site in the repository: in the
  tool's own per-site exclusion where it has one (Rust: an `exclude_re`
  entry in `.cargo/mutants.toml`), otherwise in a comment at the site
  (Go: `//mutants:equivalent <type> <reason>`). There are no
  org-level exclusions.
- No `#[coverage(off)]`, `# pragma: no cover` or equivalent.
- No deleting fail-closed guards to shrink the denominator.
- No test that calls code without asserting on its result.
- A regression test counts only if it fails with its fix reverted, and
  that failure is shown.

### What a pull request pays

The gate stays fast and deterministic. A mechanism enters `ci` only
with its measured cost written in its issue. Mutation testing, which
repeats the suite once per mutant, runs on its own weekly mutation
workflow, apart from the audit, never on a pull request. Anything
that is random (the random half of property testing), needs a
second toolchain (Rust branch coverage) or re-runs the suite at
several times its cost (Go condition coverage) runs on the Monday
audit, never on a pull request.

### Every defect issue carries the regression line

A defect's Done-when includes a test that fails before the fix and
passes after, named in the issue — or, when no test can reach the
logic, the file it lives in. The mechanism issue template says so in
its "Done when" description, because work is done from sealed issues.

### Run every test a repository already has

A test the gate does not collect is not a test. A suite under a task
name that `ci` never collects runs only when someone remembers it:
monumental-archive's Atlas schema tests under `schema:*` are the case
in hand, monumental-archive#337.

## Licensing

By contributing, you agree that your contributions are licensed under the
repository's existing license.

### Developer Certificate of Origin

Every commit must carry a sign-off asserting you have the right to submit
it under that license — the [Developer Certificate of Origin
1.1](https://developercertificate.org/):

```bash
git commit -s
```

That appends a `Signed-off-by:` trailer using your git identity, which must
match the commit author. The gate rejects commits without one, and the
commit-msg hook catches it locally first, where redoing the commit is
cheaper than rebasing. To sign off a branch you already wrote:

```bash
git rebase --signoff origin/main
```

Automation signs off too — the release commit and Renovate both add the
trailer — so there is no exemption list to fall out of date.

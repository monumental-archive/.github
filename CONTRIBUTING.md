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
   adds one. There is no per-block ignore. Where the logic goes:
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

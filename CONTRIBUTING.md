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
3. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org)
   with imperative, lowercase subjects (`feat: add the widget`). The
   `commit-msg` hook (`mise run hooks:install`) tells you immediately.
4. Pull requests are squash-merged: the **PR title becomes the commit
   subject and the PR body becomes the commit body**. Write them as the
   permanent history they will be.

## Licensing

By contributing, you agree that your contributions are licensed under the
repository's existing license.

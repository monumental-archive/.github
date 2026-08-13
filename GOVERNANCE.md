# Governance

This document governs every repository in the `monumental-archive`
organisation. It is served from the organisation's `.github` repository
as a default community health file, so it applies to any repository that
does not carry its own `GOVERNANCE.md` — and **a repository that adds
its own silently stops inheriting this one**, which is a decision to
make deliberately, never a side effect of creating a file.

It describes the governance that actually exists. The organisation has
**one maintainer**; this page does not dress that up as a committee,
because a governance document that describes a fictional process fails
at the one thing it is for.

## Decision model

Decisions are made by the maintainer, [Carl Allen](https://github.com/CarlAllenn),
in the open:

- **Significant decisions are made in writing, in public, before they
  are implemented.** New tools, new controls and new conformance targets
  arrive by a docs-first standup — an issue or pull request that records
  the options, the choice and the reasons (see the standup record in the
  `.github` repository, PRs #5–#26, and `docs/tooling-verdicts.md` for
  decisions *against* adopting something). The written record is the
  decision; conversation is not.
- **Declined paths are recorded with reasons**, so they are re-litigated
  against the record rather than from scratch.
- Proposals from anyone are welcome as issues. What gets a change
  accepted is written down in [CONTRIBUTING](CONTRIBUTING.md) and
  enforced by the CI gate; acceptance is not discretionary where the
  gate can decide.

### Succession

There is no second maintainer today — that is a recorded headcount
limit, not an oversight (it is the same wall that caps the organisation
below SLSA Source L4 and the two-person-review criteria). The
succession plan is that nothing about these projects requires the
maintainer to continue them:

- Every repository is public, every tool is pinned by checksum, and
  `mise run ci` reproduces the full gate on any machine.
- Every release is verifiable by a stranger from published roots of
  trust — no key, secret or private context held by the maintainer is
  needed to *verify* the record, so the record outlives the accounts
  that made it.
- A fork that re-establishes the documented controls (the rulesets in
  `docs/rulesets.md`, the workflows in the `.github` repository) is a
  legitimate successor; verification rests on published identities, not
  on continuity of ownership.
- If a trusted collaborator exists at the time, GitHub's ownership
  transfer is preferred over a fork; the transfer checklist in the
  `.github` repository (#83) is the mechanical part.

## Roles and responsibilities

| Role | Who | Responsibilities |
| --- | --- | --- |
| Maintainer / owner | `@monumental-archive/owners` (currently one person) | Reviews and merges pull requests, triages issues and security reports, cuts releases, holds admin on organisation settings, answers for the conformance claims the organisation publishes |
| Contributor | Anyone | Issues and pull requests under [CONTRIBUTING](CONTRIBUTING.md); no standing permissions |
| Automation | The release App, Renovate, the audit workflows | Exactly the permissions each is documented to hold, and no more — automation identities are listed in the `.github` repository's `docs/` and their grants are settings-as-code |

`CODEOWNERS` routes review to `@monumental-archive/owners` in every
repository. Becoming a member of that team is what "becoming a
maintainer" concretely means.

## Code review

Every change to every repository lands by pull request; nothing reaches
a default branch directly. Review is conducted on the pull request, in
public:

- **What a reviewer checks**: that the change does what it says, that
  the claim it makes is one its checks actually enforce, that it does
  not weaken a documented control, and that documentation changed
  alongside behaviour. The mechanical floor — formatting, linting,
  licence headers, commit shape, tests — is the CI gate's job, not the
  reviewer's, and a red gate ends the review.
- **What makes a change acceptable**: a green gate, resolved review
  threads (enforced by ruleset), a DCO sign-off on every commit, and a
  PR title/body fit to become the permanent squash commit.
- With one maintainer, maintainer-authored changes are reviewed by the
  gate and by the maintainer's own pass, not by a second human. That is
  a recorded limitation (the same headcount wall as above), and the
  organisation's compensating control is that the gate is deliberately
  the coding standard in executable form.

## Escalation and permissions

Permissions in this organisation are deliberately boring: there is one
human with escalated access, and every automation identity holds a
documented, minimal grant.

- **A collaborator is reviewed before being granted any escalated
  permission.** Concretely: before an account joins
  `@monumental-archive/owners` or receives write access anywhere, the
  maintainer reviews who they are, why the access is needed and what
  the smallest sufficient grant is — and the grant is recorded in the
  settings-as-code baseline so drift is auditable.
- Organisation-level security settings (2FA required, Actions
  allowlist, rulesets) are recorded and checked in the `.github`
  repository (`security/`, `settings/`, `docs/rulesets.md`); changing
  them is a reviewed change there, not a console click.
- Disputes and appeals: open an issue. There is no separate committee
  to escalate to; the appeal path is the written record, and decisions
  reverse when the record shows they should.

## Changes to this document

By pull request to `monumental-archive/.github`, like everything else.
Substantive governance changes (a new maintainer, a changed decision
model) are announced in the changelog of the release that ships them.

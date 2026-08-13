# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, using GitHub's
private vulnerability reporting: open the **Security** tab on the affected
repository and choose **Report a vulnerability**
(<https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability>).

Private vulnerability reporting is enabled on every repository in this
organisation. Please do **not** disclose suspected vulnerabilities in
public issues, discussions, or pull requests.

## What to expect

- Acknowledgement of your report within **14 days**.
- We will work with you to assess, reproduce, and remediate the issue, and
  we will coordinate any public disclosure with you.
- Fixed vulnerabilities are disclosed through GitHub security advisories on
  the affected repository once a patched release is available.

## Supported versions and end of life

**Only the latest release of each project receives security updates.**
Fixes ship by rolling forward — a patched release from the current
`main` — never by backporting to earlier versions. A release is
therefore supported exactly until the next release exists, at which
point it is end-of-life for security purposes; there are no long-term
support branches and no announced support windows to outlive. This is a
deliberate consequence of the release design: every release is
reproducible and its evidence is permanent, but maintenance effort goes
to the tip. A repository with different needs states its own policy in
its own `SECURITY.md` (which then replaces this one for that
repository).

## Static analysis policy

The remediation threshold for static-analysis findings is **zero**: any
SAST finding (CodeQL, the lint belt, zizmor and peers) fails the CI
gate, and a red gate cannot merge — so findings are remediated or
explicitly, reviewably suppressed with a written reason before the
change lands. There is no severity triage queue for static findings
because there is no state in which one is outstanding on a default
branch.

## Scope

All repositories in the `monumental-archive` organisation are covered by
this policy unless a repository states otherwise in its own `SECURITY.md`.

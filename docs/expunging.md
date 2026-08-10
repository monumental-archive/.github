# Safe expunging process

The SLSA v1.2 source track requires a documented process for removing
content from a repository and its history without a public record of the
removed content — permitted **only** to meet legal or privacy
compliance requirements (takedowns, accidentally committed personal
data, credentials). This is that process. Everything else in this org's
history is immutable by ruleset and stays that way.

## When it applies

- Legal takedown (copyright, court order).
- Personal data committed in error.
- Leaked credentials (which are additionally rotated immediately —
  removal from history is hygiene, not remediation).

Convenience is never a reason. A broken commit is fixed forward.

## Process

1. Open a **private record** of the request: what must be removed, why,
   and the legal/privacy basis. If the trigger is not itself sensitive,
   record it as an issue in the affected repository; if it is, keep the
   record off-platform and note its existence in the issue.
2. Assess downstream impact: which revisions become invalid, who may
   have consumed them, whether tags or releases reference them. Removal
   rewrites every descendant revision ID — consumers pinned to old IDs
   lose their reference.
3. Perform the removal (`git filter-repo` or GitHub Support for
   platform-cached content). The branch ruleset blocks force pushes, so
   this requires an org admin temporarily disabling the ruleset — a
   **continuity-resetting event** for every source-track claim on the
   affected branch (see `source-track.md`); the reset is the honest
   price and is not worked around.
4. Re-enable the ruleset immediately; verify via the API that
   enforcement is active.
5. Log publicly that content was removed (commit range and reason
   class, not content): an entry in the repository's issue noted above.
   Prefer public logs; keep them private only where the law or the
   privacy interest demands it.

## Two-person rule: recorded headcount exception

The spec says expunging SHOULD require an administrator plus one
additional trusted person. This org has one maintainer; the exception is
recorded here deliberately, alongside the L4 ceiling in
`source-track.md`, and dissolves if a second maintainer exists.

# Maintenance and compatibility surface

Versions of this repository follow semver over the surface consumers
actually pin — the org canon's consumables. A change is **breaking** when
a repository consuming the previous version breaks by taking the new one
with no change of its own.

## The versioned surface

- **Shared workflows** (`.github/workflows/*.yml` with `workflow_call`):
  their call contracts — inputs, secrets, required caller permissions,
  and the artifact-class names `publish.yml` dispatches on. Removing or
  renaming any of these is breaking; adding an optional input is a minor.
- **The task contract**: the names and classes of belt tasks
  (`ci` collecting `lint:*`; `fix:*`, `audit:*` outside the gate).
  Removing or renaming a task a repo may invoke is breaking; a new
  `lint:*` task is a minor (it tightens every gate, by design).
- **The toolbelt** (`mise/config.toml` + `mise.lock`): the set of tools
  and their pins. Tool version bumps are dependency chores and ship with
  the next release rather than forcing one; removing a tool repos invoke
  directly is breaking.
- **Org hooks** (`lefthook/org-hooks.yml`): the hook set and what it
  enforces. Removing a hook is a minor (loosening); a new blocking hook
  is a minor (tightening is the product); renaming in ways that break
  `extends` consumers is breaking.
- **The Renovate preset** (`default.json`): its observable behaviour —
  automerge classes, age gates, custom managers. Narrowing automerge or
  removing a manager consumers rely on is breaking.

Not part of the surface: `scaffold/` and `workflow-templates/` (copied
once, never consumed live), `profile/`, community health files, docs,
and this repository's own gate plumbing (`gate.yml`, `self-release.yml`,
`self-publish.yml`).

## What a release means

Every tag `vX.Y.Z` versions all of the above at one SHA — the SHA the
caller's `uses:` pin resolves, and the same resolution delivers the
canon tree to every job via `$/.github/actions/canon` (#165) — so
workflow, toolbelt and lockfile move atomically. Consumers reference it
three ways, all carrying the same version: workflow pins
(`@<sha> # vX.Y.Z`), the lefthook remote (`ref: vX.Y.Z`), and the preset
(`github>monumental-archive/.github#vX.Y.Z`). Renovate fans each tag out
to every consumer (see #133).

**The post-release window (#227, #290).** A commit cannot contain its
own release SHA, so every release `vN` structurally ships its
self-references — and leaves every consumer — at `vN-1` until Renovate's
`chore(canon)` bump lands. That bump is deliberately release-neutral
(`cliff.toml` skips the scope, ending the self-bump loop) and reaches
the front of Renovate's queue via `prPriority` and the first-party
group in `default.json`, so the window is one bot cycle plus CI —
measured 10–30 minutes. `audit:canon-pins` and `audit:template-pins`
know the window: references exactly one release behind within 24 hours
of the tag report as a notice; anything beyond that is the real alarm
(Renovate paused or dead, the v0.16.1 failure class). The window never
reaches zero and no configuration can make it — treat a brief `vN-1`
reading after a release as the steady state, not drift.

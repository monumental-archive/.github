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
caller's `uses:` pin resolves, and the tag the workflows' stamped
`# canon-pin` checkouts clone (#158) — so workflow, toolbelt and
lockfile move atomically. Consumers reference it three ways, all carrying
the same version: workflow pins (`@<sha> # vX.Y.Z`), the lefthook remote
(`ref: vX.Y.Z`), and the preset
(`github>monumental-archive/.github#vX.Y.Z`). Renovate fans each tag out
to every consumer (see #133).

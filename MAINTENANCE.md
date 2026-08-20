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

Also part of the surface: the belt's own tool configs — `mise/clippy.toml`,
`mise/rustfmt.toml`, `mise/pinact.yaml`, `mise/typos.toml` — and
`ORG_BELT_DIR`, the `[env]` variable every repo uses to reach them.
These are CONSUMED LIVE at the pinned SHA, not copied, so a change to one
lands in every repo on its next pin bump and is breaking in exactly the
way a workflow change is (#445).

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

**The post-release window (#227, #290, #310).** A commit cannot contain
its own release SHA, so every release `vN` structurally ships its
self-references — and leaves every consumer — at `vN-1` until Renovate's
`chore(canon)` bump lands. That bump is deliberately release-neutral
(the notes convention leaves the scope unmapped, ending the self-bump
loop) and reaches
the front of Renovate's queue via `prPriority` and the first-party
group in `default.json`, so the window is one bot cycle plus CI.

**One bot cycle is Mend's poll, roughly hourly (#310).** Hosted Renovate
discovers a new tag on its own schedule; no reference anywhere can move
faster than the next poll, and no preset content changes the cadence.
During a release burst a consumer can lawfully read several releases
behind for under an hour — measured live on 2026-08-13, when three
releases in 73 minutes left the signer "three behind" and the grouped
bump PR landed 60 minutes after the last tag. That is why the pin
audits (`audit:canon-pins`, `audit:template-pins`, `audit:org-pins`)
grace on **staleness duration, not releases-behind**: the clock starts
at the earliest tag newer than the stale reference, and only past 24
hours is the state red (Renovate paused or dead, the v0.16.1 failure
class). The window never reaches zero and no configuration can make
it — treat a brief stale reading after a release as the steady state,
not drift. `audit:org-pins` watches every org repo from the canon's
own Monday audit, so a genuinely stuck consumer reddens where someone
looks rather than only inside itself.

## The audit-claims contract (#240, #266, #290, #310)

Every checking task — `audit:*`, `settings/repo-baseline.sh check`, and
anything that walks a population — must satisfy three properties, learned
the hard way seven separate times (`claims.sh` #240, `audit:source-vsa`
issue #266, `generate-sbom.sh`, `repo-baseline.sh` #290 finding 7,
`audit:actions` and the baseline output #310 findings 2–3, and
`audit:drafts` #604):

1. **Fail closed on blindness.** If the task cannot establish that it
   actually looked — token absent, tool degraded to offline, population
   smaller than the known count — it exits non-zero. A check that
   quietly does nothing manufactures the impression of coverage.
2. **State the coverage on success.** A green run prints what it
   examined — `N subjects across M repos` — so a reader of a green
   Monday audit sees the population in the output instead of having to
   reason their way to it. Soundness you have to derive is weaker than
   a line that says it.
3. **Prove the capability, never infer it.** A permission bit, a scope
   list or a documented grant describes what a token was *given*, not
   what the forge will *serve* it. `audit:drafts` asserted
   `.permissions.push` per repository before believing any listing —
   the right instinct — and the assert passed on all four repos while
   GitHub served none of the thirty-two drafts that existed. **No
   permission bit is a proof of visibility.** Where a task's soundness
   rests on being able to see something, it establishes that by seeing
   one: `audit:drafts` now creates an ephemeral draft, asserts it comes
   back in the same listing it is about to believe, and deletes it.
   Property 1 says fail closed on blindness; this says you do not get
   to decide you are sighted by reading your own paperwork.

New audits copy this shape from `audit:source-vsa`. Review any checking
task against both properties before it lands.

## The seam rule (#358, corrected by v1.24.0)

The gate is deterministic by design and therefore structurally blind to
integration seams: caller `permissions:` resolve at startup, attestation
lookups at verdict time, publish-state derivations at whatever moment
they run. #353 closed gate-green carrying four seam defects; every one
was found by running a release and none by a linter.
`lint:caller-permissions`, `lint:audit-scheduled` and
`audit:caller-permissions` guard the seams now enumerated — and the
first of those reaches across the seam rather than stopping at it: a
consumer's gate computes the requirement from the canon tree ci.yml
already places at `.org-canon`, so an under-granted stub reddens the
pin-bump PR in the repo being bumped, not a Monday cron or a 1s
`startup_failure`. For the rest,
what counts as "done" **depends on who executes the change first**, and
that is not a choice:

- **Consumer-path changes** (`ci.yml`, the task contract, the toolbelt,
  hooks, the preset) are done when a release-lab release has exercised
  them on a pin carrying them, before any production repo moves.
- **The canon's own release path** (`release.yml`, `publish.yml`,
  `verify-release.yml`, `release/*`) is executed **by the canon first**,
  and no amount of rule-writing changes that: `lint:canon-pins` requires
  every consumer pin to name a released `# vX.Y.Z`, so no repo may pin
  an untagged canon SHA; the publish guard accepts `refs/tags/v*` only;
  and `self-publish.yml` passes no `dry-run`. Cut the release and let it
  run.

That ordering is not a hazard, because **canon tags are cheap** — the
same way lab tags are. `self-publish.yml` ships one class
(`source-archive`): a tarball, an SBOM, checksums and a DOI, in minutes.
A red canon release costs a version number and nothing else; nobody is
pinned to it once the fix-forward ships, exactly as with a burned lab
patch. Spend them freely rather than reasoning about what might break.

What the canon's release does **not** prove is breadth. It exercises one
class, so it is a smoke test of the machinery. `release-lab` publishes
`rust-binary,oci-image,wasm-npm,pgrx-extension` across PG 14–18 with
`dry-run: false` — "the full-width proof in substance", as its own stub
says. So the sequence for a release-path change is: **release the canon
(cheap, fast, first), then move the lab's pin and cut a lab release
(heavy, full width), then production repos.** The first tells you it
runs; the second tells you it works.

v1.24.0 is the worked example: a verdict-leg check that had never met a
real attestation refused its own valid decision. The commit point held
nothing back — the proofs themselves had passed — so the release
published and its VSA was lost. The fix shipped as v1.24.1 and the cost
was one version number nobody pins.

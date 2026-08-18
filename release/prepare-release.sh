#!/usr/bin/env bash
# Release phase 1, step 1: work out the next version and stage everything the
# Release PR should contain. Org canon — see docs/release.md; proven in
# iiif-server before being promoted here.
#
# Runs in the caller repository's workspace. Assumes the canonical workspace
# shape: `[workspace.package].version` is the single source, members inherit
# it, and internal path dependencies carry a matching `version = "..."`
# constraint in `[workspace.dependencies]`.
#
# Writes GITHUB_OUTPUT keys:
#   release  true when there is something to release
#   version  bare version, e.g. 0.2.0
#   files    space-separated paths the release commit must contain
#
# Leaves the working tree modified; open-release-pr.sh commits it via the API.
#
# The version is derived, never typed: stele derive version reads the
# conventional commits since the last v* tag. The two decisions that
# matter are derive's own defaults — 0.x breaking changes bump the minor
# rather than reaching 1.0.0, and chore/ci/docs-only ranges release
# nothing (its docs and tests are the spec; stele#31).
set -euo pipefail

# The mirror kind is detected by derive bump itself, never here — the
# phase-1 contract in docs/release.md now lives behind that one call
# (stele#102: cargo-workspace, single-crate, none; CITATION.cff where
# present). The pre-bump version is the tag base, one read: the old
# per-kind manifest read was a second detection of a fact the tool
# owns, and derive bump refuses a drifted mirror rather than repairing
# it, so the two can never silently disagree.
current=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2> /dev/null || true)
current=${current#v}
# The notes conventions, previously cliff.toml: groups and URLs are the
# org convention stated once here; the bump rules (0.x breaking bumps
# minor, chore/ci/docs/style/test release nothing) are stele derive's
# own defaults — and unlike the pinned git-cliff, whose
# no_increment_regex was silently inert (2.13.1 drops unknown [bump]
# keys), the silent-types rule is now real. Bare chore is unmapped:
# release commits and self-pin bumps stay out of the notes, at the
# recorded cost of cliff's Miscellaneous heading.
repo_slug="${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
repo_url="https://github.com/${repo_slug}"
groups="feat=Added,fix=Fixed,perf=Performance,refactor=Changed"
groups+=",docs=Documentation,test=Testing,build=Build,ci=CI"
groups+=",chore(deps)=Dependencies,revert=Reverted"
order="Breaking,Added,Changed,Fixed,Performance,Documentation"
order+=",Testing,Build,CI,Dependencies,Reverted"
notes_flags=(
  --groups "${groups}"
  --group-order "${order}"
  --breaking-group "Breaking"
  --compare-url "${repo_url}/compare/"
  --release-url "${repo_url}/releases/tag/"
  --pull-url "${repo_url}/pull/"
)

# One tool call derives the version AND writes every mirror it owns
# (workspace/single-crate version, internal path-dependency
# constraints, CITATION.cff version + date-released): parsed for
# location, byte-spliced, re-read through the same reader before disk
# — never pattern-matched. Drift refuses by name (stele#102, #514).
# date-released defaults to the committer date of HEAD, the same
# no-wall-clock rule the old sed applied.
bumped=$(stele derive bump --git-dir .)
echo "${bumped}"
release=$(awk -F= '/^release=/{print $2}' <<< "${bumped}")
version=$(awk -F= '/^version=/{print $2}' <<< "${bumped}")
kind=$(awk -F= '/^kind=/{print $2}' <<< "${bumped}")
bump_files=$(awk -F= '/^files=/{print $2}' <<< "${bumped}")
if [[ ${release} != true ]]; then
  emit_pending=true
fi

echo "current: ${current:-<no tag yet>}"
echo "next:    ${version:-<none>}"

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

if [[ ${emit_pending:-false} == true ]]; then
  emit "release=false"
  exit 0
fi

files="${bump_files:+${bump_files} }CHANGELOG.md"

# The mirrors are already rewritten by derive bump above; what stays
# here is cargo's own derivation, never ours: refresh the lockfile's
# copy of the member versions and prove the tree still resolves
# before anyone is asked to review it.
if [[ ${kind} == cargo-* ]]; then
  if [[ -f Cargo.lock ]]; then
    cargo update --workspace --offline 2> /dev/null || cargo update --workspace
    files="${files} Cargo.lock"
  fi
  # fuzz/ is its own cargo workspace by cargo-fuzz convention, with its own
  # lockfile that path-depends on the crate being released. Left alone it
  # names the superseded version after every release, which is what made
  # lint:fuzz-build rewrite it on the next local gate run — four releases of
  # drift accumulated in the lab before anyone looked (#374). The lint now
  # refuses a stale fuzz lockfile instead of silently repairing it, so
  # refreshing it here is what keeps the Release PR green.
  if [[ -f fuzz/Cargo.lock ]]; then
    cargo update --workspace --manifest-path fuzz/Cargo.toml --offline 2> /dev/null \
      || cargo update --workspace --manifest-path fuzz/Cargo.toml
    files="${files} fuzz/Cargo.lock"
  fi
  cargo metadata --format-version 1 --no-deps > /dev/null
fi

# pgrx upgrade scripts are DERIVED, never authored: the release workflow
# runs generate-pgrx-upgrade.sh after this script, which builds the
# candidate schema, diffs it against the published release's own tarball,
# and proves the result with a live ALTER EXTENSION UPDATE before it
# rides the release commit. The old hand-authored <ext>--<from>--next.sql
# stub was derived state written by humans, and forgetting it burned
# immutable version numbers (#132) — a leftover stub is therefore a
# refusal, not a rename.
stubs=$(git ls-files '*--next.sql')
if [[ -n ${stubs} ]]; then
  echo "FAIL: --next.sql stubs are retired; upgrade scripts are derived by" >&2
  echo "release/generate-pgrx-upgrade.sh. Delete: ${stubs}" >&2
  echo "(A data migration for this cycle belongs in sql/next-data.sql.)" >&2
  exit 1
fi

# The splice writes the new section above the newest one and touches
# nothing else — existing sections are history, not something a release
# regenerates (stele derive notes is table-tested for the exact
# whitespace the org's markdownlint demands).
stele derive notes --git-dir . --changelog CHANGELOG.md "${notes_flags[@]}"

emit "release=true"
emit "version=${version}"
# The pre-bump version: generate-pgrx-upgrade.sh cross-checks it against
# the published release when deriving upgrade scripts.
emit "current=${current}"
emit "files=${files}"
echo "prepared ${version} (${files})"

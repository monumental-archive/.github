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

# The version source is detected, never configured — the phase-1 contract
# in docs/release.md. A Cargo workspace mirrors its version into manifests
# that must be bumped in the release commit; a repository with no manifest
# (the canon itself, and any docs/config/image-only repository) has no
# mirror: its tags are the source of truth and the release commit carries
# only the changelog (plus citation, where present). Further manifest
# kinds (package.json, single-crate Cargo) are added here, at the read,
# the write and the file list, when a real repository needs them — never
# speculatively.
if [[ -f Cargo.toml ]]; then
  source="cargo-workspace"
  current=$(taplo get -f Cargo.toml 'workspace.package.version')
else
  source="tags"
  current=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2> /dev/null || true)
  current=${current#v}
fi
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

derived=$(stele derive version --git-dir .)
echo "${derived}"
version=$(awk -F= '/^version=/{print $2}' <<< "${derived}")
if [[ -z ${version} ]]; then
  echo "nothing to release: no version-bumping commits since the last tag"
  emit_pending=true
fi

echo "source:  ${source}"
echo "current: ${current:-<no tag yet>}"
echo "next:    ${version}"

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

if [[ ${emit_pending:-false} == true ]]; then
  emit "release=false"
  exit 0
fi

if [[ ${version} == "${current}" ]]; then
  echo "nothing to release: no version-bumping commits since the last tag"
  emit "release=false"
  exit 0
fi

files="CHANGELOG.md"

if [[ ${source} == "cargo-workspace" ]]; then
  # Bump every place the version lives, and nowhere else. The two sed
  # expressions are deliberately narrow: the workspace package version is the
  # line `version = "current"` at column zero, and internal dependency
  # constraints are the only lines that pair `path = ` with a version. An
  # unrestricted substitution would also rewrite an external dependency that
  # happens to share the version string.
  sed -i.bak "s|^version = \"${current}\"\$|version = \"${version}\"|" Cargo.toml
  sed -i.bak "/path = /s|version = \"${current}\"|version = \"${version}\"|g" Cargo.toml
  rm -f Cargo.toml.bak

  # Fail loudly rather than open a PR that does not build: a survivor on
  # either line family means a substitution missed, which is how a workspace
  # ends up with an internal constraint pointing at a crate version that no
  # longer exists.
  if grep -n "path = .*version = \"${current}\"" Cargo.toml \
    || grep -qn "^version = \"${current}\"\$" Cargo.toml; then
    echo "FAIL: Cargo.toml still mentions ${current} after the bump" >&2
    exit 1
  fi

  files="Cargo.toml ${files}"

  # Refresh the lockfile's copy of the workspace member versions, then prove
  # the tree still resolves before anyone is asked to review it.
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

# The citation is release metadata like any other: a stale version there is
# the drift the Release PR exists to prevent. date-released is the commit
# date of the release candidate, not wall-clock time.
if [[ -f CITATION.cff ]]; then
  sed -i.bak "s|^version: .*\$|version: ${version}|" CITATION.cff
  released=$(git log -1 --format=%cs)
  sed -i.bak "s|^date-released: .*\$|date-released: ${released}|" CITATION.cff
  rm -f CITATION.cff.bak
  files="${files} CITATION.cff"
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

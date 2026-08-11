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
# The version is derived, never typed: git-cliff reads the conventional
# commits since the last v* tag. See scaffold/cliff.toml for the two
# decisions that matter — 0.x breaking changes bump the minor rather than
# reaching 1.0.0, and chore/ci/docs-only ranges release nothing.
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
next=$(git cliff --bumped-version)
version=${next#v}

echo "source:  ${source}"
echo "current: ${current:-<no tag yet>}"
echo "next:    ${version}"

emit() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

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
  cargo metadata --format-version 1 --no-deps > /dev/null
fi

# The citation is release metadata like any other: a stale version there is
# the drift the Release PR exists to prevent. date-released is the commit
# date of the release candidate, not wall-clock time.
if [[ -f CITATION.cff ]]; then
  sed -i.bak "s|^version: .*\$|version: ${version}|" CITATION.cff
  sed -i.bak "s|^date-released: .*\$|date-released: $(git log -1 --format=%cs)|" CITATION.cff
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

# The canon pin is stamped, never resolved at runtime (#158): workflows
# that clone this repository carry a literal tag on their `# canon-pin`
# ref lines, and this is where the literal becomes the release being cut
# — the tagged tree names its own tag, so the pin that resolved a
# workflow file and the tree that workflow clones cannot disagree. Only
# the canon carries such lines; every other repository skips clean.
# (The previous carrier, github.job_workflow_sha, evaluated empty at
# runtime and actions/checkout silently fell back to the default branch.)
pin_files=$(git grep -l "}} # canon-pin" -- '.github/workflows/*.yml' 2> /dev/null || true)
if [[ -n ${pin_files} ]]; then
  for f in ${pin_files}; do
    sed -i.bak "s|'v[0-9][0-9A-Za-z.+-]*' }} # canon-pin|'v${version}' }} # canon-pin|" "${f}"
    rm -f "${f}.bak"
  done
  # A canon-pin line the substitution missed would float exactly the way
  # #158 did — refuse before the Release PR opens, not after the tag.
  if git grep -n "}} # canon-pin" -- '.github/workflows/*.yml' | grep -v "'v${version}' }} # canon-pin"; then
    echo "FAIL: a # canon-pin line did not take v${version}" >&2
    exit 1
  fi
  files="${files} ${pin_files//$'\n'/ }"
fi

git cliff --bump --output CHANGELOG.md

# git-cliff separates releases with a trailing blank line, which at end of
# file is an MD012/MD047 violation — and markdown is linted with warnings as
# errors org-wide. Collapse to exactly one final newline.
changelog=$(cat CHANGELOG.md)
printf '%s\n' "${changelog}" > CHANGELOG.md

emit "release=true"
emit "version=${version}"
# The pre-bump version: generate-pgrx-upgrade.sh cross-checks it against
# the published release when deriving upgrade scripts.
emit "current=${current}"
emit "files=${files}"
echo "prepared ${version} (${files})"

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

current=$(taplo get -f Cargo.toml 'workspace.package.version')
next=$(git cliff --bumped-version)
version=${next#v}

echo "current: ${current}"
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

# Bump every place the version lives, and nowhere else. The two sed
# expressions are deliberately narrow: the workspace package version is the
# line `version = "current"` at column zero, and internal dependency
# constraints are the only lines that pair `path = ` with a version. An
# unrestricted substitution would also rewrite an external dependency that
# happens to share the version string.
sed -i.bak "s|^version = \"${current}\"\$|version = \"${version}\"|" Cargo.toml
sed -i.bak "/path = /s|version = \"${current}\"|version = \"${version}\"|g" Cargo.toml
rm -f Cargo.toml.bak

# Fail loudly rather than open a PR that does not build: a survivor on either
# line family means a substitution missed, which is how a workspace ends up
# with an internal constraint pointing at a crate version that no longer
# exists.
if grep -n "path = .*version = \"${current}\"" Cargo.toml \
  || grep -qn "^version = \"${current}\"\$" Cargo.toml; then
  echo "FAIL: Cargo.toml still mentions ${current} after the bump" >&2
  exit 1
fi

files="Cargo.toml CHANGELOG.md"

# Refresh the lockfile's copy of the workspace member versions, then prove
# the tree still resolves before anyone is asked to review it.
if [[ -f Cargo.lock ]]; then
  cargo update --workspace --offline 2> /dev/null || cargo update --workspace
  files="${files} Cargo.lock"
fi
cargo metadata --format-version 1 --no-deps > /dev/null

# The citation is release metadata like any other: a stale version there is
# the drift the Release PR exists to prevent. date-released is the commit
# date of the release candidate, not wall-clock time.
if [[ -f CITATION.cff ]]; then
  sed -i.bak "s|^version: .*\$|version: ${version}|" CITATION.cff
  sed -i.bak "s|^date-released: .*\$|date-released: $(git log -1 --format=%cs)|" CITATION.cff
  rm -f CITATION.cff.bak
  files="${files} CITATION.cff"
fi

# pgrx upgrade scripts: authors cannot know the next version — git-cliff
# decides it right here — so they write <ext>--<from>--next.sql (the
# `from` they always know: it is the manifest version they migrate from)
# and the bump renames `next` to the decided version, exactly as it owns
# the version in Cargo.toml and CITATION.cff. Guessed filenames strand
# installations; the class guard refuses them; this is why nobody has to
# guess.
while IFS= read -r pending; do
  [[ -n ${pending} ]] || continue
  renamed="${pending%--next.sql}--${version}.sql"
  git mv "${pending}" "${renamed}"
  files="${files} ${pending} ${renamed}"
  echo "upgrade path: ${pending} -> ${renamed}"
done < <(git ls-files '*--next.sql')

git cliff --bump --output CHANGELOG.md

# git-cliff separates releases with a trailing blank line, which at end of
# file is an MD012/MD047 violation — and markdown is linted with warnings as
# errors org-wide. Collapse to exactly one final newline.
changelog=$(cat CHANGELOG.md)
printf '%s\n' "${changelog}" > CHANGELOG.md

emit "release=true"
emit "version=${version}"
emit "files=${files}"
echo "prepared ${version} (${files})"

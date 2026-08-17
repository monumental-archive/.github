#!/usr/bin/env bash
# Print a Go module's package patterns, one per line, derived from the
# files git TRACKS rather than from Go's own directory walker.
#
# The belt's standing law (CLAUDE.md): belt tooling reads tracked files,
# never a tool's own walker. `go test ./...` breaks it — the pattern
# matches every directory under the module, so in a repository that
# also builds something else it walks that build output too. Two ways
# that bites, both observed:
#
#   * a stray .go file under a build directory becomes a package, and
#     its syntax errors fail a run that has nothing to do with it;
#   * worse, and what actually reddened release-lab's main: the belt
#     runs tasks in PARALLEL, so cargo was creating and deleting temp
#     files under target/ while Go walked it, and the walk died with
#     "pattern ./...: open target/debug/build/.../rustcXY6Nom: no such
#     file or directory". Nothing was wrong with the Go code; the
#     walker simply looked somewhere it had no business looking.
#
# Tracked files cannot race a build and cannot include build output,
# because build output is gitignored — the same reason every other belt
# linter reads `git ls-files`.
#
# Usage: go-packages.sh [module-dir]   (default: the current directory)
# Output: `.` for the module root, `./sub/dir` for the rest — the forms
# `go test`, `go list` and govulncheck all accept.
set -euo pipefail

dir="${1:-.}"
cd "${dir}"

# Pathspecs are relative to the current directory, so this lists only
# the module's own tracked Go files. Vendored code is excluded for the
# same reason every other belt task excludes it: it is not this
# repository's source.
git ls-files ':!vendor/**' '*.go' | while IFS= read -r f; do
  d=$(dirname "${f}")
  if [[ ${d} == "." ]]; then
    echo "."
  else
    echo "./${d}"
  fi
done | sort -u

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
# WHAT `./...` DELIBERATELY SKIPS, AND SO MUST THIS. Replacing a
# walker means inheriting its exclusions, or the replacement breaks
# repositories the walker handled. Measured on a fixture holding all
# three, where a naive tracked-files list built every one of them and
# `go test` failed three ways:
#
#   * `testdata/` — Go's reserved name for fixture data. The files in
#     it are frequently not valid Go ON PURPOSE (parser tests, golden
#     inputs), so compiling them fails by design.
#   * directories whose name starts with `_` or `.` — excluded from
#     every Go build by the same rule.
#   * NESTED MODULES — a subdirectory with its own go.mod belongs to
#     that module, not this one, and `./...` stops at the boundary.
#     Building it from here fails with "main module does not contain
#     package".
#
# Usage: go-packages.sh [module-dir]   (default: the current directory)
# Output: `.` for the module root, `./sub/dir` for the rest — the forms
# `go test`, `go list` and govulncheck all accept.
set -euo pipefail

dir="${1:-.}"
cd "${dir}"

# Nested module roots, relative to here. The pattern requires a slash,
# so this module's own go.mod is not in the list.
nested=$(git ls-files ':!vendor/**' '*/go.mod' | sed 's|/go\.mod$||')

git ls-files ':!vendor/**' '*.go' | while IFS= read -r f; do
  d=$(dirname "${f}")
  # The module root is always its own package dir, and the guards below
  # would read "./" as a dot-directory.
  if [[ ${d} == "." ]]; then
    echo "."
    continue
  fi
  # Go's own exclusions, in Go's own terms.
  case "/${d}/" in
    */testdata/* | */_* | */.*) continue ;;
    *) ;;
  esac
  # Inlined rather than a helper function deliberately: a function
  # called in an `if` condition disables `set -e` inside it (SC2310),
  # and the belt runs shellcheck with nothing suppressed.
  nested_hit=""
  while IFS= read -r n; do
    [[ -n ${n} ]] || continue
    case "${d}" in
      "${n}" | "${n}"/*)
        nested_hit=1
        break
        ;;
      *) ;;
    esac
  done <<< "${nested}"
  [[ -z ${nested_hit} ]] || continue
  echo "./${d}"
done | sort -u

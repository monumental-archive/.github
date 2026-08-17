#!/usr/bin/env bash
# Print a Go module's package patterns, one per line, without letting a
# tool walk the tree.
#
# TWO HALVES, EACH BY THE ONLY THING ENTITLED TO DECIDE IT:
#
#   WHERE TO LOOK — git. The belt's standing law (CLAUDE.md) is that
#   belt tooling reads tracked files, never a tool's own walker.
#   `go test ./...` breaks it: the pattern matches every directory
#   under the module, so in a repository that also builds something
#   else it walks that build output too. It reddened release-lab's
#   main — the belt runs tasks in PARALLEL, so cargo was creating and
#   deleting temp files under target/ while Go read them:
#
#     pattern ./...: open target/debug/build/…/rustcXY6Nom:
#     no such file or directory
#
#   Tracked paths cannot race a build and cannot name build output,
#   because build output is gitignored.
#
#   WHAT IS A PACKAGE — Go. A directory holding tracked .go files is
#   not necessarily a package Go would build, and those rules belong to
#   Go, not to a reimplementation here. Measured on a fixture holding
#   four cases a naive tracked-files list got wrong, each failing
#   `go test` in its own way: `testdata/` (reserved, and often not
#   valid Go on purpose), `_`- and `.`-prefixed directories (excluded
#   from every build), nested modules ("main module does not contain
#   package"), and packages whose files are all excluded by a build
#   constraint — `//go:build ignore`, the standard idiom for generator
#   scripts kept beside the code.
#
#   So the candidate list is filtered through `go list -e`, which
#   reports those as a per-package Error rather than failing, and the
#   survivors are exactly what `./...` would have matched. Naming the
#   directories explicitly means no walk, which is the whole point.
#
# Usage: go-packages.sh [module-dir]   (default: the current directory)
# Output: `.` for the module root, `./sub/dir` for the rest — the forms
# `go test`, `go list` and govulncheck all accept. Silent when the
# module has no packages; callers decide whether that is an error.
set -euo pipefail

dir="${1:-.}"
cd "${dir}"

# Nested module roots, relative to here. The pattern requires a slash,
# so this module's own go.mod is not in the list. Filtered here rather
# than left to `go list` only because a nested module's error message
# ("main module does not contain package") is confusing enough to be
# worth never producing.
nested=$(git ls-files ':!vendor/**' '*/go.mod' | sed 's|/go\.mod$||')

# Captured rather than read through a process substitution, so a failing
# git is a failing script rather than an empty list (SC2312).
go_files=$(git ls-files ':!vendor/**' '*.go')

candidates=""
while IFS= read -r f; do
  [[ -n ${f} ]] || continue
  d=$(dirname "${f}")

  if [[ ${d} == "." ]]; then
    candidates="${candidates}.
"
    continue
  fi

  # Directories Go excludes from pattern matching, in Go's own terms.
  case "/${d}/" in
    */testdata/* | */_* | */.*) continue ;;
    *) ;;
  esac

  # Another module's territory.
  skip=""
  while IFS= read -r n; do
    [[ -n ${n} ]] || continue
    case "${d}" in
      "${n}" | "${n}"/*) skip=1 ;;
      *) ;;
    esac
  done <<< "${nested}"
  [[ -z ${skip} ]] || continue

  candidates="${candidates}./${d}
"
done <<< "${go_files}"

[[ -n ${candidates} ]] || exit 0

sorted=$(printf '%s' "${candidates}" | sort -u)

pkgs=()
while IFS= read -r c; do
  [[ -n ${c} ]] || continue
  pkgs+=("${c}")
done <<< "${sorted}"

[[ -n ${pkgs[0]:-} ]] || exit 0

# The survivors, mapped back to the relative form callers pass on.
go list -e -f '{{if not .Error}}{{.Dir}}{{end}}' "${pkgs[@]}" 2> /dev/null \
  | while IFS= read -r abs; do
    [[ -n ${abs} ]] || continue
    rel="${abs#"${PWD}"}"
    rel="${rel#/}"
    if [[ -z ${rel} ]]; then
      echo "."
    else
      echo "./${rel}"
    fi
  done | sort -u

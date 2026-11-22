#!/usr/bin/env bash
# The one selection of the files the action lints read (#1448). Sourced
# by lint:actions-allowed, lint:action-pins, its fix:actions and
# audit:action-pins halves, lint:zizmor, lint:shell-embedded, and
# lint:workflow-shell with fix:workflow-shell (which add the workflow
# templates) — `. "${ORG_BELT_DIR}/action-files.sh"`, never executed —
# so they cannot disagree about what an action file is.
#
# The workflows, and every tracked action definition WHEREVER it lives:
# an action is identified by its file name, which the platform fixes
# (`action.yml` or `action.yaml`), never by a directory. A repository
# that publishes `actions/<name>/action.yml` (consumed as
# `owner/repo/actions/<name>@<commit>`) is read exactly as one holding
# `.github/actions/<name>/action.yml` is. Naming a second fixed
# directory was rejected: the next layout would be a third.
#
# Tracked files only (org_tracked, #1986), never a walker, and vendor/
# is not ours to lint. The pathspecs keep git's default meaning, the one
# mise/gate-tools.toml's `when` rows speak, so the gate installs pinact
# and zizmor wherever this selects a file; `*action.y*ml` also reaches
# `my-action.yml`, and the filter keeps exact file names only. Printed
# NUL-separated, as org_tracked prints them, so a name with a space is
# one path; a caller reads it with `read -r -d ''`. Under pipefail, git's
# failure is the function's; no match is an empty list, never a status.
action_files() { # usage: action_files -> NUL-separated paths on stdout
  local f
  org_tracked ':!vendor/**' ".github/workflows/*.y*ml" "*action.y*ml" \
    | while IFS= read -r -d '' f; do
      case ${f} in
        .github/workflows/*.y*ml | action.yml | action.yaml | */action.yml | */action.yaml)
          printf '%s\0' "${f}"
          ;;
        *) ;;
      esac
    done
}

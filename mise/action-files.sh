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
# Tracked files only (org_tracked_read, #1986), never a walker, and
# vendor/ is not ours to lint. The pathspecs name the file names
# exactly, in git's default meaning: `.github/workflows/*.y*ml` (at any
# depth below it), `action.yml` / `action.yaml` at the root, and
# `*/action.yml` / `*/action.yaml` at any depth, which no
# `my-action.yml` matches. mise/gate-tools.toml's `when` rows name the
# wider `*action.y*ml`, so the gate installs pinact and zizmor wherever
# this selects a file. The caller names the array it fills; git's
# failure is the function's.
action_files() { # usage: <array>=() && action_files <array>
  org_tracked_read "$1" ':!vendor/**' ".github/workflows/*.y*ml" \
    action.yml action.yaml "*/action.yml" "*/action.yaml"
}

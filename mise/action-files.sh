#!/usr/bin/env bash
# The one selection of the files the action lints read (#1448). Sourced
# by lint:actions-allowed, lint:action-pins, its fix:actions and
# audit:action-pins halves, and lint:zizmor
# (`. "${ORG_BELT_DIR}/action-files.sh"`), never executed, so they
# cannot disagree about what an action file is.
#
# The workflows, and every tracked action definition WHEREVER it lives:
# an action is identified by its file name, which the platform fixes
# (`action.yml` or `action.yaml`), never by a directory. A repository
# that publishes `actions/<name>/action.yml` (consumed as
# `owner/repo/actions/<name>@<commit>`) is read exactly as one holding
# `.github/actions/<name>/action.yml` is. Naming a second fixed
# directory was rejected: the next layout would be a third.
#
# Tracked files only (git ls-files), never a walker, and vendor/ is not
# ours to lint. The pathspecs keep git's default meaning, the one
# mise/gate-tools.toml's `when` rows speak, so the gate installs pinact
# and zizmor wherever this selects a file; `*action.y*ml` also reaches
# `my-action.yml`, and the filter keeps exact file names only. git runs
# on its own, so its failure is the caller's failure; awk, not grep, so
# no match is an empty list, never an exit status.
action_files() { # usage: action_files -> one path per line on stdout
  local listed
  listed=$(git ls-files ':!vendor/**' ".github/workflows/*.y*ml" "*action.y*ml") || return
  awk '/^\.github\/workflows\/.*\.y.*ml$/ || /(^|\/)action\.ya?ml$/' <<< "${listed}"
}

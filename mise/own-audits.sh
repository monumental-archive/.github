#!/usr/bin/env bash
# The one reader of "which audit:* tasks does THIS repository define"
# (#704). Sourced by belt tasks (`. "${ORG_BELT_DIR}/own-audits.sh"`),
# never executed, on identity.sh's footing and for identity.sh's reason:
# two callers need this set — the `audit` aggregator that RUNS them and
# `lint:audit-scheduled` that proves something runs them — and a fact
# derived at two sites with no join is exactly the seam #358 named.
#
# WHY NOT A WILDCARD. `ci` collects `lint:*` with mise's own glob, and
# the obvious symmetry would be `depends = ["audit:*"]`. It does not
# work here: the belt is delivered as mise's global config, so its own
# two dozen audit tasks are visible to every consumer and a wildcard
# would re-run the belt's legs a second time inside the leg that
# collected them. A wildcard also drops `audit:web:links` silently,
# because mise's `*` matches within ONE colon group.
#
# WHY NOT mise's `global` FLAG, which is the obvious answer and was the
# first build: it is right in a consumer and WRONG IN THE CANON, and it
# fails in the direction that matters. `global` describes how a config
# was LOADED, not whose it is. The canon carries the belt in its own
# tree, `$/.github/actions/canon` links `.org-canon` to that same tree
# when the canon calls itself, and mise then sees one file by two paths
# and classifies it as the project's. Measured on the runner (mise
# 2026.8.10): every belt audit task came back `global: false`, this
# function reported the belt absent, and the canon's own gate went red.
# It did NOT reproduce under mise 2026.8.3 locally, which is the whole
# argument against the flag — the answer moved with the tool version
# and the deployment shape, and neither is a fact about who owns a task.
#
# So the belt's tasks are identified by THE FILE THAT DEFINES THEM,
# which is a file we hold: the same `^[tasks."audit:..."]` grep
# lint:audit-scheduled already runs over the belt config. Everything
# mise reports that is not in that set belongs to the repository. A
# consumer that overrides a belt task name keeps the belt's leg, which
# already runs `mise run audit:<name>` and so resolves to the override
# — the task still runs, exactly once.
#
# FAILS CLOSED. Every failure path returns non-zero rather than an
# empty list, because an empty list is indistinguishable from "this
# repo defines no audits" — which is a green result. A check that
# cannot enumerate says so (#568).
own_audit_tasks() { # usage: own_audit_tasks -> zero or more names on stdout
  command -v jq > /dev/null 2>&1 || {
    echo "own-audits: jq is not on PATH — it is a belt pin, so the belt did not arrive" >&2
    return 1
  }
  local belt_config="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}/config.toml"
  [[ -f ${belt_config} ]] || {
    echo "own-audits: ${belt_config} missing — the belt's own task list is what" >&2
    echo "  every other audit task is measured against; without it nothing can be" >&2
    echo "  called this repository's own" >&2
    return 1
  }
  local belt raw
  # Split rather than piped: shellcheck's SC2312 is right that a pipeline
  # hides which stage failed, and grep legitimately exits 1 here — on a
  # belt config with no audit tasks, which is the refusal below, not an
  # error to swallow silently.
  raw=$(grep -oE '^\[tasks\."audit:[a-z:-]+"\]' "${belt_config}") || raw=""
  local line
  belt=""
  while IFS= read -r line; do
    [[ -n ${line} ]] || continue
    line=${line#'[tasks."'}
    belt+="${line%'"]'}"$'\n'
  done <<< "${raw}"
  belt=$(sort <<< "${belt}")
  # The belt carries two dozen audit tasks. Zero means the file is not
  # the one this function thinks it is reading, and every answer below
  # would be "all of them are yours".
  [[ -n ${belt} ]] || {
    echo "own-audits: no audit:* tasks found in ${belt_config} — the belt config is" >&2
    echo "  unreadable or has changed shape; refusing to call every audit task this" >&2
    echo "  repository's own" >&2
    return 1
  }
  local json all
  json=$(mise tasks ls --json) || {
    echo "own-audits: 'mise tasks ls --json' failed — refusing to report an empty" >&2
    echo "  audit set, which would read as 'this repository defines none'" >&2
    return 1
  }
  all=$(jq -r '[.[] | select(.name | startswith("audit:")) | .name] | sort | .[]' <<< "${json}") || {
    echo "own-audits: could not read the task list" >&2
    return 1
  }
  local own
  own=$(comm -23 <(printf '%s\n' "${all}") <(printf '%s\n' "${belt}"))
  [[ -n ${own} ]] || return 0
  printf '%s\n' "${own}"
}

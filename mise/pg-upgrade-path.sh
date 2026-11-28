#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) the monumental-archive contributors
# SPDX-License-Identifier: 0BSD
# Every PUBLISHED extension version must reach the current one by
# `ALTER EXTENSION <name> UPDATE`.
#
# A gate-time repository invariant, not a release step: it runs in `ci` so
# a Release PR goes red while the fix is still cheap. It is the check that
# caught #816 — the derivation had silently produced no upgrade path at
# all after a burned release, and this was the only thing that noticed.
# It lived in one repository when it did so (edtf's
# .github/scripts/assert-upgrade-path.sh); a conforming repo without it
# would have shipped a release that strands every existing installation.
#
# Why it matters. pgrx repos set `default_version` to the crate version,
# so every release mints a new extension version whether or not the SQL
# surface moved. With no path joining an installed version to the new one,
# a user's only route is DROP and CREATE — and these functions are
# routinely used inside CHECK constraints and expression indexes, so DROP
# either errors on those dependencies or, with CASCADE, silently removes
# them.
#
# REACHABILITY, not "is the current version some script's target".
# Postgres walks the shortest path across the whole graph, so the graph is
# what has to be checked: a single new script can leave older published
# versions stranded while a target-only check stays green.
#
# WHICH VERSIONS MUST REACH IT is two questions, and this check used to
# answer the second with the first (#825). The graph's own `from` halves
# of `<name>--<from>--<to>.sql` name every version a later release was
# derived from, and those must reach the current one. A version that is
# only ever a `to` is the open question: it burned — the Release PR
# committed its script and the publish then failed (#816, #821), so
# nobody could install it and nothing need reach the current version
# from it — OR it published fine and the next release simply was not
# derived from it, in which case every installation on it is stranded.
# Filenames cannot tell those apart. This check used to call every one of
# them "burned", which is green and untrue in the second case.
#
# So it does not infer. `.pgrx-installable` is DERIVED STATE written by
# release/generate-pgrx-upgrade.sh from the same forge walk that picks
# the derivation's predecessor: which versions a non-draft release still
# carries tarballs for, and therefore which versions a database can be
# sitting on. This reads that record as data — no forge call of its own,
# because the gate is deterministic — and the two agree by construction
# rather than by coincidence.
#
# The record is only ever allowed to ACCUSE, never to excuse: a `to`-only
# version it lists is stranded and reds, a `from` still owes a path
# whatever the record says, and a tree with no record gets a NOTE that
# states the open question instead of closing it. Not from git tags,
# either way: lint checkouts are shallow and tagless, imported repos
# carry pre-canon tag schemes, and a hand-maintained list has no owner
# once the Release PR is machine-generated.
#
# Applicability, in the belt's terms: a repository with no tracked
# `*.control` is not a pgrx repository and this skips clean. A control
# with no upgrade scripts is a FIRST PUBLISH and also skips clean — there
# is no published predecessor to strand. Only once a graph exists is
# reachability enforced. A linter that cannot skip cannot be universal.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
: "${ORG_CANON_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
# The canon's one writer of workflow commands (#1660), beside this file.
# shellcheck source=mise/workflow-command.sh
. "$(dirname "${BASH_SOURCE[0]}")/workflow-command.sh"
# The tracked-file producer (#1986), beside it.
# shellcheck source=mise/tracked.sh
. "$(dirname "${BASH_SOURCE[0]}")/tracked.sh"

controls=() && org_tracked_read controls '*.control'

if ((${#controls[@]} == 0)); then
  echo "lint:pg-upgrade-path: no extension control files tracked"
  exit 0
fi

# The derived record, at the repository root beside `.coverage-floor` and
# for the same reason (#652, #825). This name is also written by
# release/generate-pgrx-upgrade.sh; the two are released together at one
# canon resolution, and the end-to-end row in
# release/test_generate_pgrx_upgrade.py derives a tree and lints it, so a
# rename on one side reds rather than silently reading nothing.
record=".pgrx-installable"

checked=0
for control in "${controls[@]}"; do
  crate_dir=$(dirname "${control}")
  name=$(basename "${control}" .control)
  sql_dir="${crate_dir}/sql"

  scripts=() && org_tracked_read scripts "${sql_dir}/${name}--*--*.sql"

  if ((${#scripts[@]} == 0)); then
    org_say "lint:pg-upgrade-path: ${name} has no upgrade scripts; first publish has no predecessor to strand"
    continue
  fi

  version=$(cargo pkgid --manifest-path "${crate_dir}/Cargo.toml" | sed 's/.*[@#]//')
  if [[ -z ${version} ]]; then
    org_error "could not resolve the ${name} crate version from ${crate_dir}/Cargo.toml"
    exit 1
  fi

  RECORD="${record}" "${ORG_PYTHON}" -I "${ORG_CANON_DIR}/belt" pg-upgrade-path \
    "${name}" "${version}" "${sql_dir}" "${scripts[@]}"
  checked=$((checked + 1))
done

org_say "lint:pg-upgrade-path: ${checked} extension(s) checked"

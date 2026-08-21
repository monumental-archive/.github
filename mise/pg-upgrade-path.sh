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
# The installable set is derived from the graph's own `from` halves of
# `<name>--<from>--<to>.sql` — never from git tags. A version appears as
# a `from` only when a later release was derived from it, which is what
# "installable predecessor" means; a version that is only ever a `to`
# and not the current one is a dead end — the Release PR committed its
# script and the publish then burned (#816, #821), so nobody could have
# installed it and nothing need reach the current version from it. Such
# dead ends are reported as NOTE lines, never as errors. Not from tags:
# Lint checkouts are shallow and tagless, imported repos carry
# pre-canon tag schemes, and a hand-maintained list has no owner once the
# Release PR is machine-generated. It is also the same source the release
# derivation reads (#762, #816), so the two agree by construction rather
# than by coincidence.
#
# Applicability, in the belt's terms: a repository with no tracked
# `*.control` is not a pgrx repository and this skips clean. A control
# with no upgrade scripts is a FIRST PUBLISH and also skips clean — there
# is no published predecessor to strand. Only once a graph exists is
# reachability enforced. A linter that cannot skip cannot be universal.
set -euo pipefail

controls=()
# shellcheck disable=SC2312  # process substitution over tracked files: the
# empty case is handled explicitly below, which is stronger than a masked
# pipeline status.
while IFS= read -r tracked_control; do
  [[ -n ${tracked_control} ]] && controls+=("${tracked_control}")
done < <(git ls-files '*.control')

if ((${#controls[@]} == 0)); then
  echo "lint:pg-upgrade-path: no extension control files tracked"
  exit 0
fi

checked=0
for control in "${controls[@]}"; do
  crate_dir=$(dirname "${control}")
  name=$(basename "${control}" .control)
  sql_dir="${crate_dir}/sql"

  scripts=()
  # shellcheck disable=SC2312  # same as above: emptiness is the signal and
  # it is tested directly.
  while IFS= read -r tracked_sql; do
    [[ -n ${tracked_sql} ]] && scripts+=("${tracked_sql}")
  done < <(git ls-files "${sql_dir}/${name}--*--*.sql")

  if ((${#scripts[@]} == 0)); then
    echo "lint:pg-upgrade-path: ${name} has no upgrade scripts; first publish has no predecessor to strand"
    continue
  fi

  version=$(cargo pkgid --manifest-path "${crate_dir}/Cargo.toml" | sed 's/.*[@#]//')
  if [[ -z ${version} ]]; then
    echo "::error::could not resolve the ${name} crate version from ${crate_dir}/Cargo.toml"
    exit 1
  fi

  python3 - "${name}" "${version}" "${sql_dir}" "${scripts[@]}" << 'PY'
import collections
import os
import re
import sys

name, version, sql_dir, *paths = sys.argv[1:]

pattern = re.compile(r"^" + re.escape(name) + r"--(.+?)--(.+)\.sql$")
edges = collections.defaultdict(list)
released = set()
targets = set()

for path in paths:
    match = pattern.match(os.path.basename(path))
    if not match:
        print(f"::error::cannot parse upgrade script name: {os.path.basename(path)}")
        print(f"::error::expected {name}--<from>--<to>.sql")
        sys.exit(1)
    edges[match.group(1)].append(match.group(2))
    released.add(match.group(1))
    targets.add(match.group(2))


def reaches(start):
    """Can `start` get to `version` by any chain of upgrade scripts?"""
    seen, queue = set(), collections.deque([start])
    while queue:
        node = queue.popleft()
        if node == version:
            return True
        if node in seen:
            continue
        seen.add(node)
        queue.extend(edges.get(node, ()))
    return False


dead_ends = sorted(v for v in targets - released if v != version)
for dead in dead_ends:
    print(f"NOTE: {name} {dead} is a target nothing was derived from — burned; need not reach {version}")

stranded = sorted(v for v in released if v != version and not reaches(v))

if stranded:
    print(f"::error::no ALTER EXTENSION UPDATE path to {version} from: {' '.join(stranded)}")
    print(f"::error::add {sql_dir}/{name}--<from>--<to>.sql to connect them")
    print("::error::an empty file is correct when the SQL surface did not change")
    sys.exit(1)

# The current version must itself be an endpoint of the graph. If it is
# not, this release added no upgrade script — which is exactly what a
# burned predecessor produced in #816: a Release PR that derived nothing
# and would have stranded every installation.
if version not in released | targets:
    print(f"::error::{name} {version} is not an endpoint of the upgrade graph")
    print(f"::error::this release derived no upgrade script, so nothing can reach {version}")
    print(f"::error::expected {sql_dir}/{name}--<previous>--{version}.sql")
    sys.exit(1)

print(f"::notice::every published {name} version reaches {version}")
for path in sorted(paths):
    print(f"ok  {os.path.basename(path)}")
PY
  checked=$((checked + 1))
done

echo "lint:pg-upgrade-path: ${checked} extension(s) checked"

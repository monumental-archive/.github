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

  RECORD="${record}" \
    python3 - "${name}" "${version}" "${sql_dir}" "${scripts[@]}" << 'PY'
import collections
import os
import re
import sys

name, version, sql_dir, *paths = sys.argv[1:]
record_path = os.environ["RECORD"]

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


def read_record(path):
    """What the release path last observed at the forge.

    Returns (by_extension, provenance). `by_extension` is None when there
    is no record at all — a tree that has not been through a Release PR
    since #825, where the honest answer is that publication is unknown.
    A record that IS present is held to the derived-state law the way
    `.coverage-floor` is: it must carry the derivation that produced it,
    and anything else is drift, which reds rather than being repaired.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None, ""

    observed = derived = ""
    by_extension = {}
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            key, sep, value = stripped.lstrip("#").strip().partition(":")
            if sep and key.strip() == "observed":
                observed = value.strip()
            elif sep and key.strip() == "derived":
                derived = value.strip()
            continue
        if not stripped:
            continue
        extension, *carried = stripped.split()
        if extension in by_extension:
            problems.append(f"line {lineno}: {extension} is named twice")
            continue
        by_extension[extension] = set(carried)
    if not observed or not derived:
        problems.append("carries no `# observed:` and `# derived:` derivation")

    if problems:
        for problem in problems:
            print(f"::error::{path} {problem}")
        print(f"::error::{path} is derived state written by the release path;")
        print("::error::regenerate it by re-running the Release PR, never by hand")
        sys.exit(1)
    return by_extension, f"{derived}, observed {observed}"


record, provenance = read_record(record_path)
# None when the record does not exist, and also when it exists but does
# not name THIS extension — a crate added since the last release. Both
# mean the same thing to every judgment below: nobody has asked the forge
# about these versions, so nothing here may claim they did.
installable = None if record is None else record.get(name)

# A `to`-only version is the open question this check used to close by
# guessing (#825). Answer it from the record, or say plainly that it is
# open — never call it burned on the strength of a filename.
for dead in sorted(v for v in targets - released if v != version):
    if installable is None:
        print(f"NOTE: {name} {dead} is a target nothing was derived from; no")
        print(f"      {record_path} in this tree says whether it published, and")
        print("      if it did, installations on it are stranded")
    elif dead not in installable:
        print(f"NOTE: {name} {dead} is a target nothing was derived from, and no")
        print(f"      non-draft release carried its tarballs ({provenance}) —")
        print(f"      nothing can be installed on it; need not reach {version}")

# The record may ACCUSE, never excuse. A `from` owes a path whatever the
# record says about it: it is a `from` because a later release was
# derived from it, which is the forge's own answer at that time, and
# dropping that requirement on the strength of a later observation would
# let a deleted asset retire a real obligation.
owes_a_path = set(released)
if installable is not None:
    owes_a_path |= installable & targets

stranded = sorted(v for v in owes_a_path if v != version and not reaches(v))

# Explain only the versions the RECORD put on the hook. A `from` is
# already on it for a reason the reader can see in the graph, and saying
# "it is not a dead end" about a version nobody called a dead end is
# noise that buries the one line that carries new information.
for accused in stranded:
    if installable is not None and accused in installable and accused not in released:
        print(f"::error::{name} {accused} is a target nothing was derived from,")
        print(f"::error::but a non-draft release carries its tarballs ({provenance})")
        print("::error::— it published, so installations on it are stranded")

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

# Claim exactly what was checked. Without the record the check has seen
# no evidence about publication at all, and saying "every published
# version" would be the same sentence #825 was filed about.
if installable is None:
    print(f"::notice::every {name} version a later release was derived from")
    print(f"::notice::reaches {version}; no {record_path} here, so whether a")
    print("::notice::target-only version published was not checked")
else:
    print(f"::notice::every installable {name} version reaches {version}")
    print(f"::notice::installable set recorded {provenance}")
for path in sorted(paths):
    print(f"ok  {os.path.basename(path)}")
PY
  checked=$((checked + 1))
done

echo "lint:pg-upgrade-path: ${checked} extension(s) checked"

#!/usr/bin/env bash
# Derive the README badge block from the repository's own tree facts.
# Org canon — the enforcement half lives in lint:badges.
#
# ONE derivation site, two callers, which is why this is a script and not
# a task body (#82). lint:badges used to obtain the derivation by running
# `mise run fix:badges` from inside a task; that nested mise raced the ~40
# lint tasks `ci` runs in parallel and failed to resolve its own lockfile,
# so `mise run ci` was unrunnable locally — which meant the pre-push hook
# had to be bypassed, which meant nothing local enforced anything. A
# derivation both callers can source directly has no such race, and as an
# ordinary .sh it also comes under shellcheck and shfmt.
#
# Output: BADGES_OUT names a file to write the block to (what lint:badges
# wants); unset, the block is spliced into README.md between the markers.
set -euo pipefail

[[ -f REUSE.toml ]] || {
  echo "fix:badges: no REUSE.toml, no badge surface, skipped"
  exit 0
}
repo_url=$(grep "^SPDX-PackageDownloadLocation" REUSE.toml | head -1 | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')
[[ -n ${repo_url} ]] || {
  echo "fix:badges: REUSE.toml carries no SPDX-PackageDownloadLocation" >&2
  exit 1
}
repo="${repo_url##*/}"
org_path="${repo_url#https://github.com/}"
state() { [[ -f .badge-states ]] && awk -v k="${1}" '$1 == k {print $2; exit}' .badge-states || true; }
states_extra() { [[ -f .badge-states ]] && awk -v k="${1}" '$1 == k {print $2}' .badge-states || true; }
block=""
add() { block="${block}${1}
"; }
gate_wf=""
# A reusable workflow (workflow_call) has no runs of its own to badge —
# the canon's ci.yml is the gate every OTHER repo badges; its own
# status lives on gate.yml.
for wf in ci.yml gate.yml; do
  [[ -f ".github/workflows/${wf}" ]] || continue
  grep -qE "^[[:space:]]+workflow_call:" ".github/workflows/${wf}" && continue
  gate_wf="${wf}"
  break
done
if [[ -n ${gate_wf} ]]; then
  add "[![ci](https://github.com/${org_path}/actions/workflows/${gate_wf}/badge.svg)](https://github.com/${org_path}/actions/workflows/${gate_wf})"
fi
if [[ -f .github/workflows/scorecard.yml ]]; then
  add "[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/${org_path}/badge)](https://scorecard.dev/viewer/?uri=github.com/${org_path})"
fi
# The org's claimed tracks; the Monday audit parses each shield and
# matches it against direction.md's table, so these can never outrun it.
add "[![SLSA Build L3](https://img.shields.io/badge/SLSA-Build%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/runbook.md#verifying-as-a-consumer-would)"
add "[![SLSA Source L3](https://img.shields.io/badge/SLSA-Source%20L3-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/source-track.md)"
add "[![SLSA Dependencies L2](https://img.shields.io/badge/SLSA-Dependencies%20L2-2ea44f)](https://github.com/monumental-archive/.github/blob/main/docs/dependency-track.md)"
bp=$(state bestpractices)
bp_live=""
if [[ -n ${bp} && ${bp} != "pending" ]]; then
  bp_live=1
  add "[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/${bp}/badge)](https://www.bestpractices.dev/projects/${bp})"
  # The Baseline series is a second shield on the same entry, not a
  # variant of the first: /projects/<id>/baseline is a sibling of
  # /badge, and the image self-reports the highest level attained
  # (v2026.02.19 | 1). Confirmed at registration — /badge?level= and
  # ?series= are ignored, /baseline-<n>/badge 404s.
  add "[![OpenSSF Baseline](https://www.bestpractices.dev/projects/${bp}/baseline)](https://www.bestpractices.dev/projects/${bp})"
else
  add "<!-- pending (human step): OpenSSF Best Practices — answer the form from docs/best-practices.md, then set 'bestpractices <BP_ID>' in .badge-states and re-run fix:badges -->"
fi
reuse=$(state reuse)
if [[ ${reuse} == "registered" ]]; then
  add "[![REUSE status](https://api.reuse.software/badge/github.com/${org_path})](https://api.reuse.software/info/github.com/${org_path})"
else
  add "<!-- pending (human step): REUSE — register at https://api.reuse.software/register (no account: name, email, project URL, confirmation link), then set 'reuse registered' in .badge-states and re-run fix:badges -->"
fi
if [[ -f .coverage-floor ]]; then
  add "[![coverage](https://codecov.io/gh/${org_path}/branch/main/graph/badge.svg)](https://codecov.io/gh/${org_path})"
fi
cit=""
[[ -f CITATION.cff ]] && cit=1
concept=""
[[ -n ${cit} ]] && concept=$(awk '/^identifiers:/{f=1} f && /value:/{print $NF; exit}' CITATION.cff)
minting=$(git ls-files ".github/workflows/*.y*ml" | xargs grep -lE "^[^#]*mint-doi:[[:space:]]*true" 2> /dev/null || true)
if [[ -n ${concept} ]]; then
  add "[![DOI](https://zenodo.org/badge/DOI/${concept}.svg)](https://doi.org/${concept})"
elif [[ -n ${minting} ]]; then
  add "<!-- pending (first mint): DOI — the concept DOI lands in CITATION.cff after the first release mints it; re-run fix:badges -->"
fi
classes=""
for f in .github/workflows/publish.yml .github/workflows/self-publish.yml; do
  [[ -f ${f} ]] && classes="${classes} $(grep -E "^[^#]*classes:" "${f}" | head -1 | sed "s/.*classes:[[:space:]]*//" | tr -d "\"'")"
done
reg_live=""
if [[ ${classes} == *rust-crate* ]]; then
  crate=$(state crates)
  if [[ -z ${crate} ]]; then
    echo "fix:badges: classes include rust-crate but .badge-states names no crate ('crates <name>')" >&2
    exit 1
  fi
  reg_live=1
  add "[![crates.io](https://img.shields.io/crates/v/${crate}.svg)](https://crates.io/crates/${crate})"
  add "[![docs.rs](https://img.shields.io/docsrs/${crate})](https://docs.rs/${crate})"
fi
if [[ ${classes} == *wasm-npm* ]]; then
  pkg=$(state npm)
  if [[ -z ${pkg} ]]; then
    echo "fix:badges: classes include wasm-npm but .badge-states names no package ('npm <name>')" >&2
    exit 1
  fi
  reg_live=1
  add "[![npm](https://img.shields.io/npm/v/%40monumental-archive%2F${pkg}.svg)](https://www.npmjs.com/package/@monumental-archive/${pkg})"
fi
if [[ ${classes} == *oci-image* || ${classes} == *pgrx-extension* ]]; then
  reg_live=1
  # The shield names the IMAGE and links its package page — an
  # org-level packages link returns 200 for any org that exists and
  # proves nothing (#316 addendum C).
  for img in ${repo} $(states_extra ghcr); do
    add "[![ghcr ${img}](https://img.shields.io/badge/ghcr.io-monumental--archive%2F${img//-/--}-blue)](https://github.com/orgs/monumental-archive/packages/container/package/${img})"
  done
fi
# fair-software, computed dot by dot: repository, licence, registry,
# citation, checklist — filled only where this render's own truth
# fills it. The static 5/5 image asserted fullness regardless (#316).
d1="%E2%97%8F"
d2="%E2%97%8F"
d3="%E2%97%8B"
[[ -n ${reg_live} ]] && d3="%E2%97%8F"
d4="%E2%97%8B"
[[ -n ${cit} ]] && d4="%E2%97%8F"
d5="%E2%97%8B"
[[ -n ${bp_live} ]] && d5="%E2%97%8F"
colour="orange"
[[ "${d3}${d4}${d5}" == "%E2%97%8F%E2%97%8F%E2%97%8F" ]] && colour="green"
add "[![fair-software](https://img.shields.io/badge/fair--software.eu-${d1}%20${d2}%20${d3}%20${d4}%20${d5}-${colour})](https://fair-software.eu)"
if [[ -n ${BADGES_OUT:-} ]]; then
  printf '%s' "${block}" > "${BADGES_OUT}"
  echo "fix:badges: rendered block to ${BADGES_OUT}"
  exit 0
fi
[[ -f README.md ]] || {
  echo "fix:badges: no README.md" >&2
  exit 1
}
if ! grep -q "<!-- badges:begin -->" README.md || ! grep -q "<!-- badges:end -->" README.md; then
  echo "fix:badges: README.md carries no badges:begin/badges:end markers — add the pair where the block belongs" >&2
  exit 1
fi
# Block delivered via a file: awk -v cannot carry newlines portably
# (macOS awk refuses them).
blkfile=$(mktemp)
printf '%s' "${block}" > "${blkfile}"
awk -v blkfile="${blkfile}" '
  /<!-- badges:begin -->/ {print; while ((getline l < blkfile) > 0) print l; skip=1; next}
  /<!-- badges:end -->/ {skip=0}
  !skip {print}
' README.md > README.md.new && mv README.md.new README.md
rm -f "${blkfile}"
rendered=$(grep -c "img.shields.io\|scorecard.dev\|codecov.io\|zenodo.org\|badge.svg" <<< "${block}")
echo "fix:badges: rendered ${rendered} line(s) into README.md"

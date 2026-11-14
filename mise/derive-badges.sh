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
# `|| true` on every no-match-is-an-answer read in this file (#905), and
# it is load-bearing rather than defensive. `grep` exits 1 when nothing
# matches and 2 when a path does not exist; under the `set -euo pipefail`
# above, `pipefail` carries that out of the pipeline, the command
# substitution inherits it, and `errexit` kills the script THERE — before
# the line that was written to explain the situation. Every such read
# below is a question whose answer may legitimately be "none", so the
# status is discarded and the emptiness is judged by the guard that
# follows. Line 147's `minting=` already did this; the other four did
# not, and each one died mute.
#
# This one is the sharpest illustration: without the `|| true`, a
# REUSE.toml lacking the field never reached the message four lines down
# — `fix:badges` exited 1 with no output at all, and the diagnostic was
# unreachable code. Measured 2026-08-24.
repo_url=$(grep "^SPDX-PackageDownloadLocation" REUSE.toml | sed -n 1p | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/' || true)
[[ -n ${repo_url} ]] || {
  echo "fix:badges: REUSE.toml carries no SPDX-PackageDownloadLocation" >&2
  exit 1
}
repo="${repo_url##*/}"
org_path="${repo_url#https://github.com/}"
state() { [[ -f .badge-states ]] && awk -v k="${1}" '$1 == k {print $2; exit}' .badge-states || true; }
states_extra() { [[ -f .badge-states ]] && awk -v k="${1}" '$1 == k {print $2}' .badge-states || true; }
# The rest of a state line, for a value that holds spaces (an SPDX
# expression is `MIT OR Apache-2.0`).
state_rest() {
  [[ -f .badge-states ]] && awk -v k="${1}" '$1 == k {sub(/^[^[:space:]]+[[:space:]]+/, ""); print; exit}' .badge-states || true
}
# shields.io's static-badge escaping: a literal hyphen or underscore is
# doubled, and anything a URL or a markdown link would trip on is
# percent-encoded. `%` first, or the encodings would be re-encoded.
shield_esc() {
  local t="${1}"
  t="${t//%/%25}"
  t="${t//-/--}"
  t="${t//_/__}"
  t="${t// /%20}"
  t="${t//\//%2F}"
  t="${t//</%3C}"
  t="${t//>/%3E}"
  t="${t//(/%28}"
  t="${t//)/%29}"
  printf '%s' "${t}"
}
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
  emsg="[![ci](https://github.com/${org_path}/actions/workflows/${gate_wf}/badge.svg)]"
  emsg+="(https://github.com/${org_path}/actions/workflows/${gate_wf})"
  add "${emsg}"
fi
if [[ -f .github/workflows/scorecard.yml ]]; then
  emsg="[![OpenSSF"
  emsg+=" Scorecard](https://api.scorecard.dev/projects/github.com/${org_path}/badge)]"
  emsg+="(https://scorecard.dev/viewer/?uri=github.com/${org_path})"
  add "${emsg}"
fi
# The measured tracks (#540): each SLSA badge is a shields.io ENDPOINT
# reference to the judgment `stele level` published on THIS repository's
# own `levels` branch — a render of the computed level with no copy in
# between, so no value here can ever outrun the evidence. The link
# target is the published report the shield was sealed with.
#
# Own branch, not the canon's (#554). Each repository judges itself and
# publishes its own cells now, so the badge reads the branch of the
# repository it describes; the canon's branch carries only the canon's
# own cells. The path WITHIN the branch keeps the repository directory,
# because that is the layout the engine writes.
#
# WHICH tracks is read from this repository's own level stub, and from
# nowhere else. It used to be derived here a second time from tree facts
# — a release/publish workflow implying build and dependency, a
# source-attest workflow implying source — which was fine while the stub
# did not exist and became a THIRD statement of the same fact the moment
# it did (the stub declares them, the canon's population declares them).
# Two statements with a reconciler is the design; three with no
# reconciler between the third and the others is the drift this layer
# exists to make unrepresentable, and it would show up as a badge
# pointing at a cell nobody published.
#
# Located by its `uses:`, not by filename: the stub is whichever
# workflow CALLS the canon's level workflow. In a consumer that file is
# `level.yml`; in the canon itself `level.yml` is the CALLEE and the
# stub is `self-level.yml`, and a filename match would read the wrong
# one there. The match deliberately stops before any `@ref`, because a
# consumer pins by SHA (`…/workflows/level.yml@<sha>`) while the canon
# calls itself locally (`./.github/workflows/level.yml`) with no ref at
# all — and it anchors on `uses:` so a prose mention in a comment is
# not mistaken for a call.
#
# A repository with no stub publishes no levels and so renders no SLSA
# badge at all — the honest render, exactly as an absent track was
# before: a badge reading "unmeasured" would claim the measurement broke
# when nothing was ever there to measure. A track the repository does
# declare always renders, whatever it scores: a measured L0 is an answer
# like any other, and hiding a low grade would be the reach-shaped lie
# this whole layer exists to refuse.
levels_raw="https://raw.githubusercontent.com/${org_path}/levels"
repo_name="${org_path#*/}"
# No stub is the ordinary answer for a repo that publishes no level
# cells, and the `if` below already renders the block without SLSA
# shields for it — the same shape as the coverage shield, which renders
# iff .coverage-floor exists (#347). `|| true` per the note at the top:
# an unglobbed `.github/workflows/*.y*ml` makes grep exit 2, a matchless
# search makes it exit 1, and both used to be fatal and silent (#905).
stub=$(grep -lsE "^[[:space:]]*uses:.*workflows/level\.yml" .github/workflows/*.y*ml 2> /dev/null | sed -n 1p || true)
tracks=""
if [[ -n ${stub} ]]; then
  tracks=$(sed -n 's/^[[:space:]]*tracks:[[:space:]]*//p' "${stub}" | sed -n 1p)
fi
for track in ${tracks}; do
  # shields.io wants the url parameter percent-encoded.
  enc=$(printf '%s/%s/%s.shield.json' "${levels_raw}" "${repo_name}" "${track}" \
    | sed -e 's|:|%3A|g' -e 's|/|%2F|g')
  emsg="[![SLSA ${track}](https://img.shields.io/endpoint?url=${enc})]"
  emsg+="(${levels_raw}/${repo_name}/${track}.report.json)"
  add "${emsg}"
done
bp=$(state bestpractices)
bp_live=""
if [[ -n ${bp} && ${bp} != "pending" ]]; then
  bp_live=1
  emsg="[![OpenSSF Best"
  emsg+=" Practices](https://www.bestpractices.dev/projects/${bp}/badge)](https://www.bestpractices.dev/projects/${bp})"
  add "${emsg}"
  # The Baseline series is a second shield on the same entry, not a
  # variant of the first: /projects/<id>/baseline is a sibling of
  # /badge, and the image self-reports the highest level attained
  # (v2026.02.19 | 1). Confirmed at registration — /badge?level= and
  # ?series= are ignored, /baseline-<n>/badge 404s.
  add "[![OpenSSF Baseline](https://www.bestpractices.dev/projects/${bp}/baseline)](https://www.bestpractices.dev/projects/${bp})"
else
  emsg="<!-- pending (human step): OpenSSF Best Practices — answer the form from docs/best-practices.md, then"
  emsg+=" set 'bestpractices <BP_ID>' in .badge-states and re-run fix:badges -->"
  add "${emsg}"
fi
reuse=$(state reuse)
if [[ ${reuse} == "registered" ]]; then
  emsg="[![REUSE"
  emsg+=" status](https://api.reuse.software/badge/github.com/${org_path})]"
  emsg+="(https://api.reuse.software/info/github.com/${org_path})"
  add "${emsg}"
else
  emsg="<!-- pending (human step): REUSE — register at https://api.reuse.software/register"
  emsg+=" (no account: name, email, project URL, confirmation link), then set 'reuse registered' in"
  emsg+=" .badge-states and re-run fix:badges -->"
  add "${emsg}"
fi
# The licence shield (#1161): derived, because the tree already states
# it. REUSE.toml's annotations carry the SPDX expression every tracked
# file is under, and lint:reuse holds the tree to it — so where they all
# say one thing, that thing is the project's licence and no human needs
# to write it a second time on a badge. Where a tree legitimately
# carries several (code under one licence, documentation under
# another), nothing here can know which one is the PROJECT's, and
# guessing is how a badge comes to lie: the repository says so, once,
# as `licence <SPDX expression>`.
lic=$(state_rest licence)
if [[ -z ${lic} ]]; then
  lic=$(grep -E "^[[:space:]]*SPDX-License-Identifier[[:space:]]*=" REUSE.toml \
    | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/' | sort -u || true)
fi
if [[ -z ${lic} ]]; then
  echo "fix:badges: REUSE.toml carries no SPDX-License-Identifier — the licence shield has nothing to state" >&2
  exit 1
fi
if [[ ${lic} == *$'\n'* ]]; then
  echo "fix:badges: REUSE.toml names several licence expressions:" >&2
  printf '  %s\n' "${lic//$'\n'/$'\n'  }" >&2
  echo "  say which is the project's in .badge-states: 'licence <SPDX expression>'" >&2
  exit 1
fi
lic_esc=$(shield_esc "${lic}")
add "[![license](https://img.shields.io/badge/license-${lic_esc}-blue)](LICENSES/)"
# The coverage shield is fed by ONE job: the gate's coverage upload,
# which skips clean unless the gate stub passes `codecov-token`. A
# floor with no token passed is therefore a shield with no producer —
# it renders, Codecov never hears from the repository, and the image
# reads "unknown" for ever (edtf and iiif-server, #1161). The belt
# cannot pass the secret for a repository, so it refuses the render.
if [[ -f .coverage-floor ]]; then
  if ! grep -qsE "^[^#]*codecov-token:" .github/workflows/*.y*ml; then
    echo "fix:badges: .coverage-floor is tracked and no workflow passes codecov-token — the coverage shield has no producer" >&2
    echo "  add to the gate stub's job:  secrets: { codecov-token: \${{ secrets.CODECOV_TOKEN }} }" >&2
    exit 1
  fi
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
  emsg="<!-- pending (first mint): DOI — the concept DOI lands in CITATION.cff after the first release mints"
  emsg+=" it; re-run fix:badges -->"
  add "${emsg}"
fi
# The release shield (#1161): a versioned repository — one carrying the
# release stub — wears its latest release. `release pending` holds it
# back until the first one is published, the same set-it-last state the
# human-step shields use: the audit asks GitHub whether a release
# exists, and a tree cannot know that.
if [[ -f .github/workflows/release.yml ]]; then
  rel_state=$(state release)
  if [[ ${rel_state} == "pending" ]]; then
    emsg="<!-- pending (first release): release — remove 'release pending' from .badge-states once the first"
    emsg+=" release is published, and re-run fix:badges -->"
    add "${emsg}"
  else
    emsg="[![release](https://img.shields.io/github/v/release/${org_path})]"
    emsg+="(https://github.com/${org_path}/releases/latest)"
    add "${emsg}"
  fi
fi
classes=""
for f in .github/workflows/publish.yml .github/workflows/self-publish.yml; do
  # `|| true` per the note at the top: a publish workflow that declares
  # no `classes:` is a repo with no registry shields, not an error — and
  # the assignment is the LAST command of this `&&` list, so errexit was
  # reached here where the `[[ -f ]]` on its left is exempt (#905).
  [[ -f ${f} ]] && classes="${classes} $(grep -E "^[^#]*classes:" "${f}" | sed -n 1p \
    | sed "s/.*classes:[[:space:]]*//" | tr -d "\"'" || true)"
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
  emsg="[![npm](https://img.shields.io/npm/v/%40monumental-archive%2F${pkg}.svg)]"
  emsg+="(https://www.npmjs.com/package/@monumental-archive/${pkg})"
  add "${emsg}"
fi
if [[ ${classes} == *oci-image* || ${classes} == *pgrx-extension* ]]; then
  reg_live=1
  # The shield names the IMAGE and links its package page — an
  # org-level packages link returns 200 for any org that exists and
  # proves nothing (#316 addendum C).
  # WHICH images is read per class, because each class names its image
  # differently and only one of them names it after the repository
  # (#1161). oci-image pushes ghcr.io/<org>/<repo>. pgrx-extension
  # pushes ghcr.io/<org>/<basename of extension-crate-dir>, lowercased
  # — build-pgrx-images.yml's own rule, read here from the same stub
  # input the build reads. The repo name used to be emitted for both,
  # which was only ever right where a repository happened to ship both
  # classes: edtf ships the extension alone, and wore a shield for an
  # image `edtf` that has never existed. A `ghcr <name>` state line
  # still adds an image no class implies; a name already derived is
  # not rendered twice.
  imgs=""
  [[ ${classes} == *oci-image* ]] && imgs="${repo}"
  if [[ ${classes} == *pgrx-extension* ]]; then
    for f in .github/workflows/publish.yml .github/workflows/self-publish.yml; do
      [[ -f ${f} ]] || continue
      ext=$(grep -E "^[^#]*extension-crate-dir:" "${f}" | sed -n 1p \
        | sed -e "s/.*extension-crate-dir:[[:space:]]*//" -e "s/[[:space:]]*#.*//" | tr -d "\"'" || true)
      [[ -n ${ext} ]] && imgs="${imgs} $(basename "${ext}" | tr "[:upper:]" "[:lower:]")"
    done
    [[ ${classes} == *oci-image* || -n ${imgs} ]] || {
      echo "fix:badges: classes include pgrx-extension but the publish stub names no extension-crate-dir" >&2
      exit 1
    }
  fi
  seen=" "
  for img in ${imgs} $(states_extra ghcr); do
    [[ ${seen} == *" ${img} "* ]] && continue
    seen="${seen}${img} "
    emsg="[![ghcr"
    emsg+=" ${img}](https://img.shields.io/badge/ghcr.io-monumental--archive%2F${img//-/--}-blue)]"
    emsg+="(https://github.com/orgs/monumental-archive/packages/container/package/${img})"
    add "${emsg}"
  done
fi
# A repository's OWN shields (#1161). The block is what the tree can
# say about itself, and a repository may know something no derivation
# does — an image-size budget, a conformance count. It says so as
#
#   static <path> <label>=<message>
#
# and the shield is rendered here like every other, so a README has no
# hand-written badge anywhere and lint:badges covers all of them. The
# price of the sentence is its enforcer: <path> must be a tracked file
# — the script, test or workflow that makes the claim true — and the
# shield links it. A claim with nothing in the tree behind it does not
# render.
if [[ -f .badge-states ]]; then
  while read -r skey spath srest; do
    [[ ${skey} == "static" ]] || continue
    if [[ -z ${spath} || ${srest} != *=* || -z ${srest%%=*} || -z ${srest#*=} ]]; then
      echo "fix:badges: malformed static line — want 'static <path> <label>=<message>': ${skey} ${spath} ${srest}" >&2
      exit 1
    fi
    if ! git ls-files --error-unmatch -- "${spath}" > /dev/null 2>&1; then
      echo "fix:badges: static shield '${srest}' names ${spath}, which is not a tracked file — a claim needs its enforcer" >&2
      exit 1
    fi
    slabel="${srest%%=*}"
    smsg="${srest#*=}"
    slabel_esc=$(shield_esc "${slabel}")
    smsg_esc=$(shield_esc "${smsg}")
    add "[![${slabel}](https://img.shields.io/badge/${slabel_esc}-${smsg_esc}-blue)](${spath})"
  done < <(grep -v "^#" .badge-states || true)
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
emsg="[![fair-software](https://img.shields.io/badge/fair--software.eu-${d1}%20${d2}%20${d3}%20${d4}%20${d5}-${colour})]"
emsg+="(https://fair-software.eu)"
add "${emsg}"
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
# LATENT, not live, and guarded anyway. `grep -c` exits 1 when the COUNT
# IS ZERO — the one member of this family that is not about a missing
# file. Today it cannot fire: the fair-software shield above is added
# unconditionally, so the block always holds at least one matching line.
# Measured 2026-08-24 — a minimal repo (REUSE.toml and a marked README,
# nothing else) renders exactly that one shield and reports `rendered 1
# line(s)`. The guard is here because the thing keeping it unreachable is
# an unrelated decision two screens up: make fair-software conditional,
# as #316 could plausibly want, and this becomes a silent death AFTER
# README.md has already been rewritten. That is the worst position in the
# file to leave a landmine, and `|| true` costs nothing.
rendered=$(grep -c "img.shields.io\|scorecard.dev\|codecov.io\|zenodo.org\|badge.svg" <<< "${block}" || true)
echo "fix:badges: rendered ${rendered} line(s) into README.md"

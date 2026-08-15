#!/usr/bin/env bash
# This release's VEX, derived — and the release-gated triage.
#
# Decisions in the canon's security/vex/ are facts about a dependency:
# their products name the exact package@version the judgment was made
# against (pkg:cargo/serde_cbor@0.11.2), never a release tag. Coverage
# is therefore derived, not stored: this script scans the just-generated
# release SBOMs with osv-scanner and, for every finding whose
# (advisory, package@version) matches a decision, emits a statement into
# this release's own OpenVEX document — product = this release's purl,
# subcomponent = the decided package@version. The document ships as a
# release asset under GitHub's release attestation, the same integrity
# surface as the SBOM it derives from. A release is born covered; the
# hand-extension of product lists (#187) has nothing left to extend.
#
# The same scan is the L2 triage gate the dependency track wanted on the
# release path rather than the calendar (docs/dependency-track.md): a
# gate-class finding with NO matching decision fails the release before
# anything publishes. Drift is structural, not guarded: a bumped
# dependency version matches no decision and is simply undecided — red,
# for a human, with a fresh judgment to make.
#
# Class split, same policy as audit:blast-radius: ecosystem packages and
# OS packages with a shipped fix gate; unfixed base-layer OS findings are
# the rebuild cadence's input, reported and never red, and never worth a
# statement that decides nothing.
#
# Network-bound by nature (the OSV feed) — which is fine HERE and only
# here: the release path is already network-bound by construction, like
# verify-published. The `ci` gate stays deterministic; this never runs in
# it.
#
# Version dialect: decisions carry the version osv-scanner reports — no
# Debian epoch, no purl percent-encoding, no qualifiers. The join below
# is exact string equality on (advisory, name, version).
#
# Inputs: VERSION (required), SBOM_BASENAME (optional; defaults to the
# repository name, leading dot stripped like generate-sbom.sh),
# CANON_DIR (default .org-canon; the canon passes "."). Reads
# dist/*.spdx.json, writes dist/<base>-<VERSION>.vex.openvex.json.
set -euo pipefail

# Inputs, declared so the contract is explicit and a missing one fails
# by name instead of expanding to nothing (#82).
: "${GITHUB_REPOSITORY:?}"

[[ -n ${VERSION:-} ]] || {
  echo "::error::VERSION is required"
  exit 1
}
command -v osv-scanner > /dev/null || {
  echo "::error::osv-scanner missing from the belt"
  exit 1
}
canon="${CANON_DIR:-.org-canon}"
vexdir="${canon}/security/vex"
repo="${GITHUB_REPOSITORY##*/}"
base="${SBOM_BASENAME:-${repo}}"
base="${base#.}"

compgen -G "dist/*.spdx.json" > /dev/null || {
  echo "::error::no SBOM in dist/ — derive-vex runs after generate-sbom.sh"
  exit 1
}

# Decisions, flattened to joinable rows. A product that does not parse as
# pkg:<type>/.../<name>@<version> is a contract violation: refused loudly,
# because a decision that cannot match anything silently decides nothing.
decisions="[]"
if [[ -d ${vexdir} ]]; then
  decisions=$(cat "${vexdir}"/*.openvex.json 2> /dev/null | jq -cs '
    [.[] | .statements[]? as $s | $s.products[]?["@id"] // empty | . as $purl
      | capture("^pkg:.*/(?<name>[^/@]+)@(?<version>.+)$")
      | {vuln: $s.vulnerability.name, name: .name, version: .version,
         purl: $purl, statement: $s}]')
  bad=$(cat "${vexdir}"/*.openvex.json 2> /dev/null | jq -rs '
    [.[] | .statements[]?.products[]?["@id"] // empty
      | select(test("^pkg:.*/[^/@]+@.+$") | not)] | .[]')
  if [[ -n ${bad} ]]; then
    echo "::error::decision product is not a pkg:<type>/…/<name>@<version> purl:"
    # shellcheck disable=SC2001  # per-LINE prefix; ${x//} cannot do this legibly
    sed "s/^/  /" <<< "${bad}"
    exit 1
  fi
fi

work=$(mktemp -d)
all="${work}/findings.json"
echo "[]" > "${all}"
for sbom in dist/*.spdx.json; do
  out="${work}/$(basename "${sbom}").osv.json"
  set +e
  osv-scanner scan source -L "${sbom}" --format json > "${out}" 2> /dev/null
  ec=$?
  set -e
  if [[ ${ec} -eq 128 ]]; then
    echo "::error::${sbom##*/} parsed to zero packages — a scan that reads nothing must not report clean"
    exit 1
  fi
  if [[ ${ec} -ne 0 && ${ec} -ne 1 ]]; then
    echo "::error::osv-scanner errored (${ec}) on ${sbom##*/}"
    exit 1
  fi
  # Same class policy as audit:blast-radius (docs/dependency-track.md).
  jq -c '
    [.results[]?.packages[]? | select(.vulnerabilities != null) as $p
      | ($p.package.ecosystem // "" | ascii_downcase) as $eco
      | (if ($eco | test("debian|alpine|ubuntu|rocky|redhat|rpm")) then "os" else "eco" end) as $kind
      | $p.vulnerabilities[]
      | ([.affected[]?.ranges[]?.events[]? | select(.fixed)] | length > 0) as $fixable
      | (if $kind == "eco" then "gate"
         elif $fixable then "gate"
         else "base-unfixed" end) as $class
      | {id: .id, name: $p.package.name, version: $p.package.version, class: $class}]
  ' "${out}" > "${work}/one.json"
  jq -cs '.[0] + .[1] | unique' "${all}" "${work}/one.json" > "${all}.next"
  mv "${all}.next" "${all}"
done

# The join: a finding is decided iff a decision exists for its exact
# (advisory, package@version). Everything else about this script follows
# from this one expression.
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
release_purl="pkg:github/${GITHUB_REPOSITORY}@v${VERSION}"
all_findings=$(cat "${all}")
joined=$(jq -cn --argjson findings "${all_findings}" --argjson decisions "${decisions}" '
  {
    matched: [$findings[] as $f | $decisions[]
      | select(.vuln == $f.id and .name == $f.name and .version == $f.version)],
    undecided: [$findings[] | select(.class == "gate")
      | select([. as $f | $decisions[]
          | select(.vuln == $f.id and .name == $f.name and .version == $f.version)]
          | length == 0)],
    reported: [$findings[] | select(.class == "base-unfixed")]
  }')

n_matched=$(jq '.matched | length' <<< "${joined}")
n_reported=$(jq '.reported | length' <<< "${joined}")

undecided=$(jq '.undecided | length' <<< "${joined}")
if [[ ${undecided} -ne 0 ]]; then
  echo "::error::undecided advisories in this release's SBOM — triage before release (docs/dependency-track.md):"
  jq -r '.undecided[] | "  \(.id)  \(.name)@\(.version)"' <<< "${joined}"
  echo "::error::record each decision in the canon's security/vex/, keyed by package@version"
  exit 1
fi

if [[ ${n_matched} -eq 0 ]]; then
  emsg="::notice::no decided advisories apply to this release; no VEX document to derive"
  emsg+=" (${n_reported} base-layer finding(s) on the rebuild cadence)"
  echo "${emsg}"
  exit 0
fi

out="dist/${base}-${VERSION}.vex.openvex.json"
jq -n --argjson joined "${joined}" \
  --arg now "${now}" --arg release "${release_purl}" --arg repo "${GITHUB_REPOSITORY}" '
  {
    "@context": "https://openvex.dev/ns/v0.2.0",
    "@id": ("https://openvex.dev/docs/public/vex-" + ($repo | gsub("/"; "-")) + "-" + ($release | split("@")[1]) + "-derived"),
    author: "monumental-archive",
    version: 1,
    timestamp: $now,
    statements: ([$joined.matched[]
      | {vulnerability: .statement.vulnerability,
         products: [{"@id": $release, subcomponents: [{"@id": .purl}]}],
         status: .statement.status}
        + (.statement | {justification, impact_statement, action_statement}
           | with_entries(select(.value != null)))
        + {timestamp: $now}] | unique)
  }' > "${out}"
[[ -s ${out} ]] || {
  echo "::error::derivation produced nothing despite ${n_matched} match(es)"
  exit 1
}
# shellcheck disable=SC2312  # diagnostic output only; a failure here
# degrades a log line, it does not change what is written or decided.
emsg="derived VEX: $(jq '.statements | length' "${out}") statement(s)"
emsg+=" over ${release_purl} (${out##*/});"
emsg+=" ${n_reported} base-layer finding(s) on the rebuild cadence"
echo "::notice::${emsg}"

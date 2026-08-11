#!/usr/bin/env bash
# Resolve the OCI image metadata facts for a release, once, before anything
# is built. Org canon — see docs/release.md, "Image metadata: one map".
#
# Every fact the release will assert on its images is derived here, from
# exactly three sources: the guard-proven ref, in-tree metadata at that
# commit, and the GitHub API's view of the repository. Builds consume the
# map; they derive nothing. Provenance facts (source, revision, version,
# created, licences) are never caller inputs — a caller input is a place to
# be silently wrong about what gets signed. Editorial facts (title,
# description) are caller inputs with derived defaults, and are omitted
# rather than emitted empty.
#
# Runs in a job that executes no caller code, against the caller's checkout
# (cwd) and the canon checkout this script lives in. Outputs, to
# GITHUB_OUTPUT (or stdout when unset, for local runs):
#   facts=<compact JSON map of org.opencontainers.image.* keys>
#   epoch=<committer epoch of the released commit, for SOURCE_DATE_EPOCH>
#
# Environment contract:
#   ARCHETYPE          versioned | continuous
#   VERSION            required for versioned, forbidden for continuous
#   IMAGE_TITLE        optional editorial override
#   IMAGE_DESCRIPTION  optional editorial override
#   GH_TOKEN           for the licence fallback and the description default
set -euo pipefail

canon_dir=$(cd "$(dirname "$0")" && pwd)
spdx_data="${canon_dir}/spdx-license-data.json"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -f ${spdx_data} ]] || fail "missing ${spdx_data}"

case "${ARCHETYPE:-}" in
  versioned)
    [[ -n ${VERSION:-} ]] || fail "versioned archetype needs VERSION"
    ;;
  continuous)
    [[ -z ${VERSION:-} ]] || fail "continuous archetype has no version surface; VERSION must be unset"
    ;;
  *) fail "ARCHETYPE must be versioned or continuous, got '${ARCHETYPE:-}'" ;;
esac

# --- revision: the commit the guard-proven ref points at ------------------
revision="${GITHUB_SHA:-}"
[[ ${revision} =~ ^[0-9a-f]{40}$ ]] \
  || fail "GITHUB_SHA is not a full lowercase commit SHA: '${revision}'"

# --- source: the repository, exactly as GitHub names it -------------------
# Verbatim case, no trailing slash, no .git — one canonical rendering, so
# equality checks against it never need normalising the other side.
source_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:?}"

# --- created: committer time of the released commit, RFC 3339 UTC ---------
# The same instant every class already uses for SOURCE_DATE_EPOCH: commit
# time, never wall clock — the one wall-clock value would otherwise be
# published beside a commit-pinned one. Read from the CALLER checkout,
# which is the reason this script must run against it; deriving from the
# wrong checkout is the bug this design retired (build-pgrx-images once
# stamped every extension image with a canon commit's timestamp).
epoch=$(git log -1 --pretty=%ct)
[[ ${epoch} =~ ^[0-9]+$ ]] || fail "no committer epoch from the checkout"
# python, not `date`: -d @epoch is GNU-only and this script must run on the
# macOS bash 3.2 / BSD date that release scripts are tested on.
created=$(python3 -c 'import sys, time
print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(sys.argv[1]))))' "${epoch}")

# --- licences: precedence, then validation --------------------------------
# Precedence chooses which declaration speaks; SPDX validation is what
# makes speaking safe. The tiers are not cross-checked against each other:
# the in-tree field is the author's declaration and the API is Licensee's
# heuristic reading of one file (it flattens `MIT OR Apache-2.0` to a
# single id), so they are not independent statements of one fact.
license=""
repository_field=""
if [[ -f Cargo.toml ]]; then
  # taplo, not grep: the field can be written in more than one TOML form,
  # and a parser is the only reader that gets every form right. The same
  # belt binary and idiom tag-release.sh already uses for the version.
  read_toml() {
    taplo get -f Cargo.toml "$1" 2> /dev/null || true
  }
  license=$(read_toml workspace.package.license)
  [[ -n ${license} ]] || license=$(read_toml package.license)
  [[ -n ${license} ]] || fail "Cargo.toml declares no licence — set [workspace.package].license (or [package].license)"
  repository_field=$(read_toml workspace.package.repository)
  [[ -n ${repository_field} ]] || repository_field=$(read_toml package.repository)
else
  # No manifest: Licensee reading the actual LICENSE file at the released
  # commit. A single id or nothing — refusals below catch the nothing.
  # gh prints the error body to stdout on an HTTP error, so the response is
  # captured first and read with jq separately — piping --jq directly would
  # capture the raw error JSON as a "licence".
  resp=$(gh api "repos/${GITHUB_REPOSITORY}/license?ref=${revision}" 2> /dev/null || true)
  license=$(jq -r '.license.spdx_id // empty' <<< "${resp}" 2> /dev/null || true)
  case "${license}" in
    "" | null | NOASSERTION | NONE | OTHER)
      fail "no derivable licence: no manifest, and the API answered '${license:-nothing}' for ${revision}"
      ;;
  esac
fi

# Validate the expression against the SPDX grammar and the vendored id
# lists. Grammar alone would pass `MIT OR NotALicense`; membership alone
# would pass `MIT AND OR MIT`. Both, or the value does not ship.
SPDX_DATA="${spdx_data}" python3 - "${license}" << 'PYEOF'
import json, os, sys

expr = sys.argv[1]
with open(os.environ["SPDX_DATA"], "rb") as f:
    data = json.load(f)
licenses = {i.lower(): i for i in data["licenseIds"]}
exceptions = {i.lower(): i for i in data["exceptionIds"]}


def fail(msg):
    print(f"FAIL: licence expression '{expr}': {msg}", file=sys.stderr)
    sys.exit(1)


if "/" in expr:
    fail("legacy '/' syntax is not SPDX; write 'A OR B'")

tokens = expr.replace("(", " ( ").replace(")", " ) ").split()
if not tokens:
    fail("empty")
pos = 0


def peek():
    return tokens[pos] if pos < len(tokens) else None


def take():
    global pos
    tok = tokens[pos]
    pos += 1
    return tok


def ident(tok, table, kind):
    if tok.startswith(("LicenseRef-", "DocumentRef-")):
        fail(f"'{tok}': LicenseRef is a pointer into an SPDX document and "
             "dangles in a bare annotation; use listed identifiers")
    canonical = table.get(tok.lower())
    if canonical is None:
        hint = " (operators are case-sensitive: AND, OR, WITH)" \
            if tok.upper() in ("AND", "OR", "WITH") else ""
        fail(f"'{tok}' is not a listed SPDX {kind} id{hint}")
    if canonical != tok:
        fail(f"'{tok}': use the canonical capitalisation '{canonical}'")


def simple():
    tok = peek()
    if tok is None:
        fail("expression ends where a licence id was expected")
    if tok == "(":
        take()
        expression()
        if peek() != ")":
            fail("unbalanced parentheses")
        take()
        return
    if tok == ")":
        fail("unbalanced parentheses")
    take()
    ident(tok[:-1] if tok.endswith("+") else tok, licenses, "licence")
    if peek() == "WITH":
        take()
        exc = peek()
        if exc is None or exc in ("(", ")", "AND", "OR", "WITH"):
            fail("WITH must be followed by an exception id")
        take()
        ident(exc, exceptions, "exception")


def expression():
    simple()
    while peek() in ("AND", "OR"):
        take()
        simple()


expression()
if pos != len(tokens):
    fail(f"unexpected '{tokens[pos]}' — two ids need an operator between them")
PYEOF

# --- repository field must equal source, where declared -------------------
# These ARE two independent statements of one fact, and npm trusted
# publishing already fails on their mismatch — at publish time, after the
# images are pushed (trap 21). Checking here converts that into a
# five-second failure with the remedy named. The stale case is real: a
# transferred repository keeps its old URL in the manifest.
if [[ -n ${repository_field} ]]; then
  normalised="${repository_field%/}"
  normalised="${normalised%.git}"
  [[ ${normalised} == "${source_url}" ]] \
    || fail "manifest repository '${repository_field}' != '${source_url}' — update the repository field (stale after a transfer)"
fi

# --- editorial facts: caller input, derived default, omit when absent -----
title="${IMAGE_TITLE:-${GITHUB_REPOSITORY##*/}}"
description="${IMAGE_DESCRIPTION:-}"
if [[ -z ${description} ]]; then
  description=$(gh api "repos/${GITHUB_REPOSITORY}" --jq '.description // empty' || true)
fi

# --- assemble, validate hygiene, emit -------------------------------------
# jq --arg quotes every value; empty editorial values are dropped, and the
# continuous archetype's map simply never contains `version`.
facts=$(jq -cn \
  --arg source "${source_url}" \
  --arg revision "${revision}" \
  --arg version "${VERSION:-}" \
  --arg created "${created}" \
  --arg licenses "${license}" \
  --arg title "${title}" \
  --arg description "${description}" \
  '{
    "org.opencontainers.image.source": $source,
    "org.opencontainers.image.revision": $revision,
    "org.opencontainers.image.version": $version,
    "org.opencontainers.image.created": $created,
    "org.opencontainers.image.licenses": $licenses,
    "org.opencontainers.image.title": $title,
    "org.opencontainers.image.description": $description
  } | with_entries(select(.value != ""))')

# Hygiene, on the assembled map: a present-but-empty annotation reads as
# set, which is worse than absent (deliberately stricter than the OCI
# spec, which permits empty values); control characters and newlines would
# corrupt the CLI surfaces the values transit.
jq -e '
  to_entries | all(
    (.value | length > 0) and
    (.value | test("[\\x00-\\x1f\\x7f]") | not) and
    (.value == (.value | sub("^\\s+"; "") | sub("\\s+$"; "")))
  )' <<< "${facts}" > /dev/null \
  || fail "a fact is empty, padded, or carries control characters: ${facts}"

# Provenance keys must all be present (editorial may be omitted).
required='["org.opencontainers.image.source","org.opencontainers.image.revision","org.opencontainers.image.created","org.opencontainers.image.licenses"]'
[[ ${ARCHETYPE} == versioned ]] \
  && required=$(jq -c '. + ["org.opencontainers.image.version"]' <<< "${required}")
jq -e --argjson req "${required}" 'keys as $k | $req | all(. as $r | $k | index($r) != null)' \
  <<< "${facts}" > /dev/null || fail "a provenance fact is missing: ${facts}"

out="${GITHUB_OUTPUT:-/dev/stdout}"
{
  echo "facts=${facts}"
  echo "epoch=${epoch}"
} >> "${out}"
echo "resolved: ${facts}" >&2

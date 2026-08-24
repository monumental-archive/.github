#!/usr/bin/env bash
# Render the four SLSA track tables from slsa/*.toml. Org canon — the
# enforcement half lives in lint:tracks.
#
# ONE derivation site, two callers, which is why this is a script and not
# a task body (#82, #764). lint:tracks used to obtain the render by
# running `mise run fix:tracks` from inside a task; that nested mise
# raced the ~40 lint tasks `ci` runs in parallel and failed to resolve its
# own lockfile — the failure #82 recorded, and the same one lint:citation
# carried. It was not named in #764's sweep and is the identical shape: a
# gate lint invoking its own fix half. A derivation both callers invoke
# directly has no such race, and as an ordinary .sh it also comes under
# the belt's own shell linters.
#
# Renders in place, between the `tracks:<marker>:begin`/`:end` markers of
# each doc, relative to the current directory — which is what lets
# lint:tracks render into a copied tree and diff, without an output-path
# switch of its own. What the tables mean and why the TOML is the source
# is written at fix:tracks, the task that names this script.
set -euo pipefail

command -v taplo > /dev/null || {
  echo "fix:tracks: taplo missing from the belt" >&2
  exit 1
}
render() { # doc, marker, header-row, jq-rows-program, json
  local doc="${1}" marker="${2}" header="${3}" prog="${4}" json="${5}" body
  body=$(jq -r "${prog}" <<< "${json}")
  # ENVIRON, not -v: BSD awk warns on newlines in -v strings.
  M="${marker}" HDR="${header}" BODY="${body}" awk '
    $0 == "<!-- tracks:" ENVIRON["M"] ":begin -->" {
      print; print ""; print ENVIRON["HDR"]; print ENVIRON["BODY"]; skip = 1; next
    }
    $0 == "<!-- tracks:" ENVIRON["M"] ":end -->" { print ""; skip = 0 }
    !skip { print }
  ' "${doc}" > "${doc}.tmp" && mv "${doc}.tmp" "${doc}"
}
levels=$(taplo get -f slsa/levels.toml -o json)
reqs=$(taplo get -f slsa/requirements.toml -o json)
render docs/direction.md direction \
  "$(printf '| Track | Ceiling | Status | Enforced by |\n| --- | --- | --- | --- |')" \
  '.track[] | "| \(.name) | \(.ceiling) | \(.status) | \(.enforced) |"' "${levels}"
render docs/source-track.md source \
  "$(printf '| Requirement | Level | Enforcement |\n| --- | --- | --- |')" \
  '.source[] | "| \(.requirement) | \(.level) | \(.enforcement) |"' "${reqs}"
render docs/build-track.md build \
  "$(printf '| Requirement | Discharged by |\n| --- | --- |')" \
  '.build[] | "| \(.requirement) | \(.discharged_by) |"' "${reqs}"
render docs/dependency-track.md dependency \
  "$(printf '| Level | Demands | Standing |\n| --- | --- | --- |')" \
  '.dependency[] | "| \(.level) | \(.demands) | \(.standing) |"' "${reqs}"
echo "fix:tracks: four tables rendered"

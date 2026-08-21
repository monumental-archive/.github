#!/usr/bin/env bash
# The one reader of a Dockerfile's FROM lines (#715). Org canon.
#
# Two enforcement points ask the same question of the same text:
# `lint:from-digests` in the gate (is every base digest-pinned?) and
# build-oci-image.yml's base-approval step on the network-bound build
# path (does every org-published base carry its attestation?). A second
# parser beside the first is exactly the drift pair the belt's delivery
# model exists to kill — a config is DELIVERED, never copied (#445) —
# so the parse lives here and both callers ask it.
#
# Prints one EXTERNAL base reference per line, in file order, and
# nothing else, so a caller may read stdout as data:
#
#   - `FROM scratch` is not an image and is dropped.
#   - a reference naming a build stage this file declares is internal
#     and is dropped.
#   - `--platform=` is skipped; the reference is the word after it.
#
# Stage names are collected from the whole file before any line is
# judged, which is the behaviour lint:from-digests has had since #117.
# Docker resolves a stage name backwards only, so a forward reference
# is an error BuildKit reports at build time; duplicating that judgment
# here would be a second opinion about someone else's file.
#
# Two refusals, both loud, because either one silently returning
# nothing turns BOTH callers vacuously green: a path that cannot be
# read, and a FROM line carrying no reference at all. (A line that is
# the bare word `FROM` with nothing after it is not matched at all —
# the pattern wants whitespace — and needs no opinion here: BuildKit
# refuses that file before either caller's question arises.)
set -euo pipefail

f="${1:-}"
if [[ -z ${f} ]]; then
  echo "dockerfile-bases: usage: dockerfile-bases.sh <dockerfile>" >&2
  exit 2
fi
if [[ ! -r ${f} ]]; then
  echo "dockerfile-bases: ${f} is not readable — a missing Dockerfile is a refusal, never an empty base list" >&2
  exit 2
fi

# grep's no-match 1 is not a failure here: a Dockerfile with no FROM
# line is a file this reader has nothing to say about.
lines=$(grep -iE "^[[:space:]]*FROM[[:space:]]" "${f}" || true)
[[ -n ${lines} ]] || exit 0

stages=$(sed -nE 's/.*[[:space:]][Aa][Ss][[:space:]]+([A-Za-z0-9_.-]+).*/\1/p' <<< "${lines}")

while IFS= read -r line; do
  # shellcheck disable=SC2086  # deliberate split: a FROM line's words ARE the
  # positional parameters this reads $2 and $3 from
  set -- ${line}
  ref="${2:-}"
  if [[ ${ref} == --platform=* ]]; then
    ref="${3:-}"
  fi
  if [[ -z ${ref} ]]; then
    echo "dockerfile-bases: ${f}: a FROM line names no image: ${line}" >&2
    exit 2
  fi
  case "$(printf '%s' "${ref}" | tr '[:upper:]' '[:lower:]')" in
    scratch) continue ;;
    *) ;;
  esac
  grep -qxF "${ref}" <<< "${stages}" && continue
  printf '%s\n' "${ref}"
done <<< "${lines}"

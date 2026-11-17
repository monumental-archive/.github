#!/usr/bin/env bash
# Prove a pushed pgrx artifact image the way a consumer uses it (#1381).
#
#   pgrx-image-proof.sh <image> <digest-record.json> <pg-major> <canon-dir>
#
# Pulls the image back BY DIGEST, never by tag, and runs exactly the
# documented recipes (docs/release.md) against the stock postgres the
# extension was built on (docker/pgrx-base-images.toml, [build]). Each
# recipe's Dockerfile is rendered by release/pgrx-images.py, which
# refuses any reference not pinned by digest:
#
#   every major   `copy`: the image's one layout COPYed into the system
#                 directories, and docker/pgrx-copy-proof.sh runs
#                 CREATE EXTENSION (as root, through su postgres);
#   18 and above  `path`: the image whole under /extensions/ext, and
#                 docker/pgrx-path-proof.sh (as postgres) loads it
#                 through extension_control_path and dynamic_library_path.
#
# Below 18 there is no extension_control_path, so there is nothing to
# prove on the second recipe and it says so. The digest is the record
# the push step wrote ({pg, image, tag, digest}).
set -euo pipefail
image="${1:?image}"
record="${2:?digest record}"
pg="${3:?pg major}"
canon="${4:?canon dir}"
docker_dir=$(cd "${canon}/docker" && pwd -P)
digest=$(jq -r .digest "${record}")
base=$(python3 "${canon}/release/pgrx-images.py" base-image --pg "${pg}" \
  --table "${canon}/docker/pgrx-base-images.toml")
context=$(mktemp -d)
rc=0
trap 'rc=$?; rm -rf "${context}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
# Called directly, never in $(…), so a refused reference stops the proof.
render() { # recipe -> <context>/<recipe>.Dockerfile
  python3 "${canon}/release/pgrx-images.py" consumer-dockerfile --recipe "$1" \
    --image "${image}@${digest}" --base "${base}" --pg "${pg}" > "${context}/$1.Dockerfile"
}
render copy
docker buildx build --file "${context}/copy.Dockerfile" --load --tag "consumer-proof:pg${pg}" "${context}"
docker run --rm -v "${docker_dir}:/proof:ro" "consumer-proof:pg${pg}" \
  bash /proof/pgrx-copy-proof.sh
if ((pg < 18)); then
  echo "pg${pg}: below 18, no extension_control_path; path proof not applicable"
  org_done=1
  exit 0
fi
render path
docker buildx build --file "${context}/path.Dockerfile" --load --tag "consumer-path-proof:pg${pg}" "${context}"
docker run --rm --user postgres -v "${docker_dir}:/proof:ro" \
  "consumer-path-proof:pg${pg}" bash /proof/pgrx-path-proof.sh
org_done=1

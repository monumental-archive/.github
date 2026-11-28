#!/usr/bin/env bash
# The TypeScript leg off a runner (#1432; mise/mutants-typescript.sh's
# header has the ruling): run the leg in a clean copy of the committed
# tree, under `env -i` and a fixed list, its HOME an empty scratch
# directory. Org canon. Delivered on its own so the leg's body stays
# the in-place sequence a runner takes.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here
org_task "audit:mutants"

belt="${ORG_BELT_DIR:?unset — no belt}"
: "${ORG_PYTHON:?unset — the belt Python}"

dirty=$(git status --porcelain --untracked-files=no)
if [[ -n ${dirty} ]]; then
  while IFS= read -r item; do
    [[ -n ${item} ]] || continue
    org_finding "typescript" "uncommitted: ${item}" "the leg copies the committed tree; commit or stash first"
  done <<< "${dirty}"
  exit 1
fi

# Everything HOME or the caller's PATH used to supply, resolved here.
tools=$(mise bin-paths) || {
  org_finding "typescript" "mise bin-paths failed — the copy's PATH is the tree's own tools"
  exit 1
}
path="${ORG_PYTHON%/*}"
while IFS= read -r dir; do
  [[ -z ${dir} ]] || path+=":${dir}"
done <<< "${tools}"
docker=$(command -v docker || true)
[[ -z ${docker} ]] || path+=":${docker%/*}"
path+=":/usr/bin:/bin"
# Docker's daemon, read here: the copy's DOCKER_CONFIG holds no
# contexts, so the endpoint is passed as DOCKER_HOST.
docker_host="${DOCKER_HOST:-}"
if [[ -n ${docker} && -z ${docker_host} ]]; then
  docker_host=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2> /dev/null || true)
fi
base=""
if [[ -n ${MUTANTS_DIFF:-} ]]; then
  base=$("${ORG_PYTHON}" "${belt}/mutants_diff.py" base)
fi

rc=0
scratch=$(mktemp -d)
trap 'rc=$?; rm -rf "${scratch}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
mkdir "${scratch}/home"
# Docker's CLI plugins (compose among them) without its config: on
# Docker Desktop they are found through ~/.docker/cli-plugins, beside
# config.json's registry credentials and the contexts. The copy's
# DOCKER_CONFIG holds only `cli-plugins`, each plugin linked to its
# resolved target (the orchestrator's measurement, #1432).
mkdir -p "${scratch}/docker/cli-plugins"
plugins="${DOCKER_CONFIG:-${HOME}/.docker}/cli-plugins"
if [[ -d ${plugins} ]]; then
  for plugin in "${plugins}"/*; do
    [[ -e ${plugin} ]] || continue
    target=$("${ORG_PYTHON}" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${plugin}")
    ln -s "${target}" "${scratch}/docker/cli-plugins/${plugin##*/}"
  done
fi
head=$(git rev-parse HEAD)
git clone -q --no-hardlinks --no-checkout . "${scratch}/tree"
git -C "${scratch}/tree" checkout -q --detach "${head}"

install=()
if [[ -f ${scratch}/tree/pnpm-lock.yaml ]]; then
  store=$(pnpm store path)
  cache=$(pnpm config get cache-dir)
  if [[ -z ${cache} || ${cache} == undefined ]]; then
    case "$(uname -s)" in
      Darwin) cache="${HOME}/Library/Caches/pnpm" ;;
      *) cache="${XDG_CACHE_HOME:-${HOME}/.cache}/pnpm" ;;
    esac
  fi
  install=(pnpm install --offline --frozen-lockfile --store-dir "${store}" --cache-dir "${cache}")
elif [[ -f ${scratch}/tree/package-lock.json ]]; then
  install=(npm ci --offline --cache "$(npm config get cache)")
fi

# The fixed list, and nothing else, for everything run in the copy.
cleared=(env -i "PATH=${path}" "HOME=${scratch}/home" CI=1 "DOCKER_CONFIG=${scratch}/docker"
  "ORG_BELT_DIR=${belt}" "ORG_PYTHON=${ORG_PYTHON}" "MISE_TASK_NAME=${MISE_TASK_NAME}")
for name in MUTANTS_DIFF MUTANTS_SHARD MUTANTS_SUMMARY_DIR ORG_FINDINGS; do
  [[ -z ${!name:-} ]] || cleared+=("${name}=${!name}")
done
[[ -z ${MUTANTS_DIFF:-} ]] || cleared+=("ORG_MUTANTS_BASE=${base}")
[[ -z ${docker_host} ]] || cleared+=("DOCKER_HOST=${docker_host}")

envs() { # envs <when>: red on any untracked .env* in the copy
  local found
  found=$(git -C "${scratch}/tree" ls-files --others -- '.env*' '**/.env*')
  [[ -n ${found} ]] || return 0
  while IFS= read -r f; do
    org_finding "typescript" "${f} is in the copy $1, untracked — refused" \
      "mutated code never runs beside an environment file"
  done <<< "${found}"
  exit 1
}
envs "before the install"
if [[ -n ${install[0]+set} ]]; then
  (cd "${scratch}/tree" && "${cleared[@]}" "${install[@]}")
fi
envs "after the install"

echo "audit:mutants: typescript: off a runner — in a clean copy of ${head:0:12}, HOME scratch"
(cd "${scratch}/tree" && "${cleared[@]}" bash "${belt}/mutants-typescript.sh")
org_done=1

#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) the monumental-archive contributors
# SPDX-License-Identifier: 0BSD
# Every tracked file under a vendor/ directory is pinned to the upstream
# commit it was taken from, byte for byte (#1160).
#
# The belt's linters skip vendor/ on purpose: vendored text is someone
# else's, and linting it would mean editing it. Skipping it left nothing
# asking what is there, so a tracked file under vendor/ was checked by
# nothing at all. This is that missing half. It generalises the private
# repository's scripts/check-vendored-pins.sh, which covered one
# subdirectory and only `.md` files, and reads the same manifest format
# unchanged:
#
#   repo = "https://github.com/<owner>/<repo>"
#   commit = "<40 hex characters>"
#   upstream_path = "<path in that repository>"
#
#   [files]
#   "<path relative to the manifest>" = "<sha256>"
#
#   [omitted]
#   "<path>" = "<why it was not taken>"
#
# The rules, each red with its own message:
#
#   1. A tracked file under vendor/ is covered by the PINNED.toml in its
#      own directory, or else the nearest one in an ancestor that is
#      still under that vendor/ directory. No covering manifest is red.
#   2. A manifest states repo, commit and upstream_path, and commit is a
#      full 40-character SHA. A branch, a tag or a short SHA moves.
#   3. Every tracked file a manifest covers, other than the manifest, is
#      listed under [files] with its sha256. Unlisted is red, listed and
#      untracked is red, listed and different is red.
#
# [omitted] is a record of files deliberately not taken. It is parsed
# past and never checked: whether upstream still has them is a question
# about upstream.
#
# OFFLINE BY CONSTRUCTION. Nothing here fetches upstream, so nothing here
# proves the pinned commit really holds these bytes: that is review's
# job, when the manifest is written. What the gate proves is that the
# tree has not moved since. Tracked files only, hashed as they stand in
# the working tree.
set -euo pipefail

# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

name=lint:vendored
# The task this script reports for, where mise named none (#1934).
: "${MISE_TASK_NAME:=${name}}"

if command -v sha256sum > /dev/null 2>&1; then
  sha256=(sha256sum)
else
  sha256=(shasum -a 256)
fi

tracked=$(git -c core.quotePath=false ls-files -- ':(glob)**/vendor/**')
if [[ -z ${tracked} ]]; then
  echo "${name}: no tracked file under a vendor/ directory, skipped"
  exit 0
fi

# Newline-bounded sets, so membership is a substring test on whole lines
# (bash 3.2 has no associative arrays): [[ ${set} == *$'\n'"${x}"$'\n'* ]].
manifest_set=$'\n'
while IFS= read -r f; do
  case ${f} in
    PINNED.toml | */PINNED.toml) manifest_set="${manifest_set}${f}"$'\n' ;;
    *) ;;
  esac
done <<< "${tracked}"

# The outermost vendor/ directory a path sits under. A covering manifest
# is looked for up to and including it, never above it.
vendor_root() {
  if [[ $1 == vendor/* ]]; then
    printf 'vendor'
  else
    printf '%s/vendor' "${1%%/vendor/*}"
  fi
}

problems=0
red() {
  org_finding "" "$*"
  problems=$((problems + 1))
}

# manifest<TAB>path-relative-to-it for every covered file.
pairs=""
while IFS= read -r f; do
  [[ ${manifest_set} == *$'\n'"${f}"$'\n'* ]] && continue
  root=$(vendor_root "${f}")
  dir=${f%/*}
  found=""
  while :; do
    if [[ ${manifest_set} == *$'\n'"${dir}/PINNED.toml"$'\n'* ]]; then
      found=${dir}
      break
    fi
    [[ ${dir} == "${root}" ]] && break
    dir=${dir%/*}
  done
  if [[ -z ${found} ]]; then
    red "${f} has no PINNED.toml in its directory or any ancestor under" \
      "${root}/ — vendored text must be pinned to an upstream commit"
    continue
  fi
  pairs="${pairs}${found}/PINNED.toml"$'\t'"${f#"${found}"/}"$'\n'
done <<< "${tracked}"

key_re='^(repo|commit|upstream_path)[[:space:]]*=[[:space:]]*"([^"]*)"[[:space:]]*$'
section_re='^\[([^]]*)\][[:space:]]*$'
entry_re='^"([^"]+)"[[:space:]]*=[[:space:]]*"([0-9a-f]{64})"[[:space:]]*$'

verified=0
manifests=0
while IFS= read -r m; do
  [[ -n ${m} ]] || continue
  manifests=$((manifests + 1))
  mdir=${m%/PINNED.toml}

  covered=$'\n'
  while IFS=$'\t' read -r owner rel; do
    [[ ${owner} == "${m}" ]] && covered="${covered}${rel}"$'\n'
  done <<< "${pairs}"

  repo="" commit="" upstream_path="" have_commit=0
  section=""
  listed=$'\n'
  entries=""
  lineno=0
  while IFS= read -r line || [[ -n ${line} ]]; do
    lineno=$((lineno + 1))
    [[ ${line} =~ ^[[:space:]]*(#|$) ]] && continue
    if [[ ${line} =~ ${section_re} ]]; then
      section=${BASH_REMATCH[1]}
      continue
    fi
    case ${section} in
      "")
        if [[ ${line} =~ ${key_re} ]]; then
          case ${BASH_REMATCH[1]} in
            repo) repo=${BASH_REMATCH[2]} ;;
            commit)
              commit=${BASH_REMATCH[2]}
              have_commit=1
              ;;
            upstream_path) upstream_path=${BASH_REMATCH[2]} ;;
            *) ;;
          esac
        fi
        ;;
      files)
        if [[ ! ${line} =~ ${entry_re} ]]; then
          red "${m}:${lineno} is not a \"<path>\" = \"<sha256>\" entry" \
            "(64 lowercase hex characters)"
          continue
        fi
        path=${BASH_REMATCH[1]}
        if [[ ${listed} == *$'\n'"${path}"$'\n'* ]]; then
          red "${mdir}/${path} is listed twice in ${m}"
          continue
        fi
        listed="${listed}${path}"$'\n'
        entries="${entries}${path}"$'\t'"${BASH_REMATCH[2]}"$'\n'
        ;;
      # [omitted] and anything else: parsed past, never checked.
      *) ;;
    esac
  done < "${m}"

  [[ -n ${repo} ]] || red "${m} is missing required key 'repo'"
  [[ -n ${upstream_path} ]] || red "${m} is missing required key 'upstream_path'"
  if [[ ${have_commit} -eq 0 ]]; then
    red "${m} is missing required key 'commit'"
  elif [[ ! ${commit} =~ ^[0-9a-f]{40}$ ]]; then
    red "${m} pins commit '${commit}' — a full 40-character commit SHA is" \
      "required; a branch, a tag or a short SHA moves"
  fi

  while IFS=$'\t' read -r path want; do
    [[ -n ${path} ]] || continue
    if [[ ${covered} != *$'\n'"${path}"$'\n'* ]]; then
      red "${mdir}/${path} is listed in ${m} but not tracked under it"
      continue
    fi
    if [[ ! -f "${mdir}/${path}" ]]; then
      red "${mdir}/${path} is tracked but absent from the working tree"
      continue
    fi
    got=$("${sha256[@]}" "${mdir}/${path}" | cut -d' ' -f1)
    if [[ ${got} != "${want}" ]]; then
      red "${mdir}/${path} does not match its sha256 in ${m}" \
        "(edited in place, or re-fetched without updating the manifest)"
      continue
    fi
    verified=$((verified + 1))
  done <<< "${entries}"

  while IFS= read -r rel; do
    [[ -n ${rel} ]] || continue
    [[ ${listed} == *$'\n'"${rel}"$'\n'* ]] \
      || red "${mdir}/${rel} is tracked but not listed in ${m}"
  done <<< "${covered}"
done <<< "${manifest_set#$'\n'}"

if [[ ${problems} -ne 0 ]]; then
  org_finding "" "${verified} files verified against ${manifests} manifests, ${problems} problems above" \
    "vendored text is verified, never linted: re-fetch at a full commit and update PINNED.toml in the same change"
  exit 1
fi
echo "${name}: ${verified} files verified against ${manifests} manifests"

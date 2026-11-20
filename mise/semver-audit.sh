#!/usr/bin/env bash
# The public API of every published crate against its latest release on
# crates.io, crate by crate. Org canon — the body of `audit:semver`,
# extracted so it can judge each crate on its own terms (#1341).
#
# ONE RUN OVER THE WORKSPACE COULD NOT JUDGE TWO KINDS OF CRATE, and each
# made the leg red by construction:
#
#   - a pgrx extension: cargo-semver-checks' default enables every
#     feature at once, and pgrx refuses more than one `pgNN` ("Multiple
#     `pg$VERSION` features found", edtf, run 43884069155);
#   - a crate with no published baseline: publishable on purpose and not
#     uploaded yet ("lab-core not found in registry", release-lab, run
#     43886371474) — every crate before its first publish is in it.
#
# So the population is cargo's own account of the workspace, read through
# gate_surface.py — the one derivation of which crates are pgrx and which
# majors each declares — and each crate gets one verdict per thing that
# can be judged:
#
#   - `publish` false (cargo reports `[]`): not published, skipped;
#   - `publish` naming registries but not crates.io: not judged, said so;
#   - no library target: no API to judge, said so. "Library" is what
#     cargo-semver-checks itself checks (v0.51.0,
#     `is_lib_like_checkable_target`): a target of kind lib, rlib, dylib,
#     cdylib or staticlib. A bin-only crate is not one, and neither is a
#     proc-macro crate — the tool skips both. Asked of `-p` alone, the
#     tool refuses such a crate with an error, which would make every
#     workspace with a CLI red; the old whole-workspace run skipped it;
#   - otherwise the crates.io sparse index is asked FIRST. A 200 is a
#     baseline and the crate is judged; a 404 is a definite "never
#     published" and the crate is named as not judged; ANYTHING ELSE — a
#     throttle, a 5xx, no answer — is red. A lookup that failed is never
#     a skip: it would pass a crate nobody judged.
#
# AN ORDINARY CRATE is judged with the tool's own feature heuristic. A
# PGRX CRATE is judged once per declared major, with that `pgNN` and
# nothing else (`--only-explicit-features`): its other features are not
# enabled, because they cannot be combined safely by rule. Extend this
# when a pgrx crate ships a feature that changes its API.
#
# Every crate is judged before this exits: a red crate does not stop the
# next one. It ends with one line per verdict and exits 1 if any is red.
# No crate, major or feature name is written here.
set -euo pipefail
: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

label="audit:semver"
belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"

manifests=$(git ls-files ':!vendor/**' "Cargo.toml" "*/Cargo.toml")
[[ -n ${manifests} ]] || {
  echo "${label}: no Cargo.toml tracked, skipped"
  exit 0
}
command -v cargo > /dev/null || {
  echo "${label}: Cargo.toml present but cargo missing — pin rust in mise.toml" >&2
  exit 1
}

# Assigned one step each: an empty here-string from a failed derivation
# would read as a workspace with nothing to judge.
metadata=$(cargo metadata --no-deps --format-version 1 --locked)
surface=$("${ORG_PYTHON}" "${belt}/gate_surface.py" <<< "${metadata}")
# Per member: where it may be published, as cargo resolved it (`null` is
# anywhere, `[]` is `publish = false`, a list names registries, crates.io
# as `crates-io`), and whether it has a target the tool can judge (the
# kinds above). Never an empty field — see gate_surface.py on tabs.
where=$(jq -r '
  .workspace_members as $ids
  | .packages[]
  | select(.id as $id | $ids | index($id))
  | [.name, (if .publish == null then "any"
             elif (.publish | length) == 0 then "none"
             elif (.publish | index("crates-io")) then "any"
             else (.publish | join(",")) end),
     (if any(.targets[].kind[]; IN("lib", "rlib", "dylib", "cdylib", "staticlib"))
      then "lib" else "nolib" end)]
  | @tsv' <<< "${metadata}")

verdicts=()
red=0
provisioned=""
while IFS=$'\t' read -r record spec kind crate_majors _dir; do
  [[ ${record} == crate ]] || continue
  crate="${spec%@*}"
  target=$(awk -F '\t' -v n="${crate}" '$1 == n { print $2 }' <<< "${where}")
  library=$(awk -F '\t' -v n="${crate}" '$1 == n { print $3 }' <<< "${where}")
  if [[ -z ${target} || -z ${library} ]]; then
    verdicts+=("${label}: ${crate}: cargo reports no publish setting or targets for it, red")
    red=1
    continue
  fi
  if [[ ${target} == none ]]; then
    verdicts+=("${label}: ${crate}: publish = false, skipped")
    continue
  fi
  if [[ ${target} != any ]]; then
    verdicts+=("${label}: ${crate}: published to ${target}, not crates.io, not judged")
    continue
  fi
  if [[ ${library} != lib ]]; then
    verdicts+=("${label}: ${crate}: no library target, no API to judge, not judged")
    continue
  fi

  # The record's URL is the belt's one index rule, shared with the
  # crates publisher (#1383), so the two can never read different paths.
  url=$("${ORG_PYTHON}" "${belt}/crates_index.py" url "${crate}")
  # curl's own exit is not the verdict: the status is. No answer at all
  # writes 000, which is red like every status but 200 and 404.
  code=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --retry 3 --max-time 30 "${url}") || true
  if [[ ${code} == 404 ]]; then
    verdicts+=("${label}: ${crate}: no published baseline on crates.io, not judged")
    continue
  fi
  if [[ ${code} != 200 ]]; then
    verdicts+=("${label}: ${crate}: could not read the crates.io index (HTTP ${code:-000})")
    red=1
    continue
  fi

  if [[ ${kind} != pgrx ]]; then
    echo "${label}: judging ${crate}"
    if cargo-semver-checks check-release -p "${crate}"; then
      verdicts+=("${label}: ${crate}: judged clean")
    else
      verdicts+=("${label}: ${crate}: judged red")
      red=1
    fi
    continue
  fi

  # The provisioning every other pgrx task uses, once, and only when a
  # pgrx crate has a baseline to be judged against.
  if [[ -z ${provisioned} ]]; then
    provisioned=ok
    # shellcheck source=mise/pgrx-env.sh
    . "${belt}/pgrx-env.sh"
    # shellcheck disable=SC2310  # deliberate: a failed provisioning reds its
    # majors below and the crates after it are still judged
    org_pgrx_provision || provisioned=failed
  fi
  icu=ok
  if [[ ${provisioned} == ok ]]; then
    # shellcheck disable=SC2310  # deliberate: a missing pin reds this
    # crate's majors below and the crates after it are still judged
    org_pgrx_darwin_icu_check "${crate_majors}" || icu=failed
  fi
  for major in $(tr ',' ' ' <<< "${crate_majors}"); do
    if [[ ${provisioned} != ok ]]; then
      verdicts+=("${label}: ${crate} at pg${major}: no Postgres could be provisioned, red")
      red=1
      continue
    fi
    if [[ ${icu} != ok ]]; then
      verdicts+=("${label}: ${crate} at pg${major}: no ICU headers pinned for every major (above), red")
      red=1
      continue
    fi
    echo "${label}: judging ${crate} at pg${major}"
    # In a subshell holding this major's ICU headers (#1774), so one
    # major's include path never reaches the next.
    # shellcheck disable=SC2310  # deliberate: the judgment's status is
    # the verdict, and a red major does not stop the next
    if org_pgrx_with_icu "${major}" cargo-semver-checks check-release -p "${crate}" \
      --only-explicit-features --features "pg${major}"; then
      verdicts+=("${label}: ${crate} at pg${major}: judged clean")
    else
      verdicts+=("${label}: ${crate} at pg${major}: judged red")
      red=1
    fi
  done
done <<< "${surface}"

[[ ${#verdicts[@]} -gt 0 ]] || {
  echo "${label}: no crate in the workspace, skipped"
  exit 0
}
printf '%s\n' "${verdicts[@]}"
if [[ ${red} -ne 0 ]]; then
  echo "${label}: red — every crate above was judged; fix the API or bump the major" >&2
  exit 1
fi

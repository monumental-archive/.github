#!/usr/bin/env bash
# Provisioning a pgrx build, once, for every belt task that needs one
# (#813). Sourced by belt tasks (`. "${ORG_BELT_DIR}/pgrx-env.sh"`),
# never executed.
#
# The tasks that compile a pgrx extension — `lint:pg-clippy`,
# `test:pgrx`, `fix:rust`'s extension pass, and the workspace-wide
# mutation and semver audits — each need the same two things first: a
# `pg_config` per declared major registered with pgrx, and, on macOS, a
# correction to flags the prebuilt Postgres recorded on somebody else's
# machine. Written in each they would drift; written here they cannot.
#
# The coverage measurement is no longer among them (#1656). It compiled
# the extension under `cargo llvm-cov --workspace` (#917), but the
# extension's functions run in the Postgres backend, which that run never
# collected from, so every line read as zero (#1652). The rust leg now
# leaves the extension out, and `test:pgrx` measures it, each major's
# run built instrumented where the repository holds a floor.

org_pgrx_darwin_flags() { # correct the SDK the prebuilt pg_config names
  # MEASURED, and the one real cost of the aqua-backed mechanism. The
  # prebuilt macOS `pg_config` reports its BUILD machine's flags:
  #
  #   -isysroot /Applications/Xcode_16.4.app/.../MacOSX15.5.sdk
  #   -I/opt/homebrew/opt/icu4c/include
  #
  # Neither path exists on a machine that is not that runner — mine has
  # no /opt/homebrew at all — and pgrx hands those flags to clang, so
  # bindgen dies with "'inttypes.h' file not found". BOTH variables are
  # needed and neither is redundant: BINDGEN_EXTRA_CLANG_ARGS fixes the
  # bindings, and the cshim is compiled by cc-rs, which never reads a
  # bindgen variable and takes the same stale `--cppflags`. A later
  # -isysroot wins in clang, so prepending the real SDK is the whole fix.
  #
  # Nothing to correct on Linux: the linux pg_config records
  # `-D_GNU_SOURCE -I/usr/include/libxml2` and no sysroot at all, and
  # /usr/include/libxml2 is present on the runner (measured — libxml2-dev
  # is installed transitively, though the toolset list does not name it).
  local kernel sdk
  kernel=$(uname -s)
  [[ ${kernel} == "Darwin" ]] || return 0
  sdk=$(xcrun --show-sdk-path 2> /dev/null) || {
    echo "pgrx-env: xcrun could not name an SDK — install the Command Line Tools" >&2
    return 1
  }
  export BINDGEN_EXTRA_CLANG_ARGS="-isysroot ${sdk} ${BINDGEN_EXTRA_CLANG_ARGS:-}"
  export CFLAGS="-isysroot ${sdk} ${CFLAGS:-}"
  # ICU's headers are NOT added here (#1774): they are per Postgres
  # major, so each call site adds them for the one major it builds, with
  # org_pgrx_darwin_icu, below.
}

org_pgrx_darwin_icu() { # usage: org_pgrx_darwin_icu <Postgres major>, in the subshell that builds it
  # THE SECOND THING THE BUILD MACHINE LEFT BEHIND, and it only started
  # to matter at pgrx 0.19.3. The prebuilt macOS Postgres is configured
  # --with-icu and records
  #
  #   -I/opt/homebrew/opt/icu4c@78/include
  #
  # It ships ICU's dylibs and none of its headers. Through 0.19.2 that
  # was invisible: pgrx bound a chosen subset of Postgres's headers and
  # none of them reached ICU. 0.19.3 binds ALL of them, `pg_locale.h`
  # includes `unicode/ucol.h`, and a Mac without Homebrew's icu4c dies in
  # bindgen with "'unicode/ucol.h' file not found" — measured on
  # release-lab, at pg14 and pg18 alike, in test:pgrx, lint:pg-clippy and
  # coverage:check together. The Linux runner is unaffected: libicu-dev
  # is in the image.
  #
  # So a pgrx repository pins the headers beside its Postgres, macOS
  # only, and this adds them to the same two variables the SDK
  # correction uses and for the same reason (bindgen reads one, cc-rs the
  # other). Repository-side like the Postgres pin and by the same rule:
  # the belt carries what the belt needs to run, repos carry what they
  # build with — in the belt this would cost every repository 121M for
  # headers one class of crate reads.
  #
  # WHICH ICU IS DERIVED, never declared: the major is read out of the
  # pinned Postgres's own `pg_config --cppflags`, so the pin is checked
  # against what the server was actually built with. A header set from a
  # different major would bind against declarations the shipped dylibs
  # do not have, and nothing downstream would say so.
  #
  # ONE POSTGRES MAJOR AT A TIME (#1774). ICU renames its symbols per
  # major, so no one header set serves Postgres builds that name two —
  # and a repository that holds one Postgres major back across a release
  # where upstream's ICU moved has exactly that. Every cargo call that
  # compiles the extension names one Postgres major, so each runs this
  # for that major in a subshell with the cargo call: the include path is
  # that major's own, and it never reaches the next. One pin per ICU
  # major, and a mixed set is a supported state.
  #
  # A Postgres that names no ICU exports nothing, and so does any kernel
  # but Darwin.
  local kernel icu where rc=0
  kernel=$(uname -s)
  [[ ${kernel} == "Darwin" ]] || return 0
  icu=$(org_pgrx_darwin_icu_of "$1") || return 1
  [[ -n ${icu} ]] || return 0
  where=$(org_pgrx_darwin_icu_headers "${icu}" "$1") || rc=$?
  [[ ${rc} -ne 3 ]] || org_pgrx_darwin_icu_pin "${icu}" >&2
  [[ ${rc} -eq 0 ]] || return 1
  local include="-I${where}/source/common -I${where}/source/i18n"
  export BINDGEN_EXTRA_CLANG_ARGS="${include} ${BINDGEN_EXTRA_CLANG_ARGS:-}"
  export CFLAGS="${include} ${CFLAGS:-}"
}

org_pgrx_darwin_icu_check() { # usage: org_pgrx_darwin_icu_check <Postgres majors, comma-separated>
  # Before anything compiles, every major the task will build: one red,
  # naming each ICU major that has no headers pinned and the pin to add,
  # rather than a red per major found one compile at a time. The per-call
  # org_pgrx_darwin_icu still refuses on its own, so this is a shortcut,
  # never the only guard.
  local kernel major icu rc failed=0 seen="" missing=()
  kernel=$(uname -s)
  [[ ${kernel} == "Darwin" ]] || return 0
  for major in ${1//,/ }; do
    # lint:msrv hands its plan's `name:major` pairs; the name is not read.
    major="${major##*:}"
    icu=$(org_pgrx_darwin_icu_of "${major}") || return 1
    [[ -n ${icu} ]] || continue
    if [[ " ${seen} " == *" ${icu} "* ]]; then
      continue
    fi
    seen="${seen} ${icu}"
    rc=0
    org_pgrx_darwin_icu_headers "${icu}" "${major}" > /dev/null || rc=$?
    [[ ${rc} -ne 3 ]] || missing+=("${icu}")
    [[ ${rc} -eq 0 ]] || failed=1
  done
  [[ ${#missing[@]} -eq 0 ]] || org_pgrx_darwin_icu_pin "${missing[@]}" >&2
  return "${failed}"
}

org_pgrx_darwin_prepare() { # usage: org_pgrx_darwin_prepare <Postgres majors, comma-separated>
  # What a task does on a Mac before it compiles anything, in one call:
  # the SDK correction, which holds for every major, and the ICU check
  # over every major the task will build. Neither does anything on any
  # other kernel.
  org_pgrx_darwin_flags || return 1
  org_pgrx_darwin_icu_check "$1"
}

org_pgrx_with_icu() { # usage: org_pgrx_with_icu <Postgres major, or empty> <command…>
  # One command that compiles the extension, run in a subshell holding
  # that major's ICU headers (#1774): the include path is the major's own
  # and ends with the command, so the next major starts from the caller's
  # flags. Empty: a command that compiles no Postgres, run as it is.
  local major="$1"
  shift
  (
    [[ -z ${major} ]] || org_pgrx_darwin_icu "${major}" || exit 1
    "$@"
  )
}

org_pgrx_darwin_icu_of() { # usage: org_pgrx_darwin_icu_of <Postgres major>; prints its ICU major
  # Read out of the pinned Postgres of that major: ACTIVE entries only,
  # the rule pgrx_postgres.py states — `mise ls` reports everything
  # installed on the machine, and a copy somebody installed by hand is
  # not a pin. Prints nothing when that Postgres names no ICU, or when
  # no Postgres of that major is pinned, which pgrx_postgres.py reds in
  # its own words.
  local listing paths path pg_config flags
  listing=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  paths=$(jq -r --arg major "$1" '.[] | select(.active and .installed)
    | select(((.version // "") | split(".")[0]) == $major) | .install_path' <<< "${listing}") || return 1
  while read -r path; do
    [[ -n ${path} ]] || continue
    for pg_config in "${path}"/bin/pg_config "${path}"/*/bin/pg_config; do
      [[ -x ${pg_config} ]] || continue
      flags=$("${pg_config}" --cppflags) || return 1
      if [[ ${flags} =~ icu4c@([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
      fi
      return 0
    done
  done <<< "${paths}"
}

org_pgrx_darwin_icu_headers() { # usage: org_pgrx_darwin_icu_headers <ICU major> <Postgres major>
  # Prints the install path of the pinned ICU of that major, its headers
  # checked. Red otherwise with the reason, and status 3 when the remedy
  # is a pin to add, which the caller prints for every missing major at
  # once (org_pgrx_darwin_icu_pin).
  local listing pinned version where
  listing=$(mise ls "github:unicode-org/icu" --json) || return 1
  pinned=$(jq -r '[.[] | select(.active and .installed) | .version] | join(" ")' <<< "${listing}") || return 1
  version=$(jq -r --arg major "$1" '[.[] | select(.active and .installed)
    | select(((.version // "") | split(".")[0]) == $major)] | last | .version // empty' <<< "${listing}") || return 1
  where=$(jq -r --arg major "$1" '[.[] | select(.active and .installed)
    | select(((.version // "") | split(".")[0]) == $major)] | last | .install_path // empty' <<< "${listing}") || return 1
  if [[ -z ${version} || -z ${where} ]]; then
    if [[ -z ${pinned} ]]; then
      echo "pgrx-env: pg$2 was built against ICU $1, and mise.toml pins no ICU headers —" >&2
      echo "  the prebuilt Postgres ships none, and pgrx binds every Postgres header from 0.19.3" >&2
    else
      echo "pgrx-env: pg$2 was built against ICU $1; mise.toml pins ICU ${pinned} and no $1.x —" >&2
      echo "  the headers must be the major the shipped dylibs are, one pin per ICU major" >&2
    fi
    return 3
  fi
  if [[ ! -f ${where}/source/common/unicode/utypes.h || ! -f ${where}/source/i18n/unicode/ucol.h ]]; then
    echo "pgrx-env: ICU ${version} at ${where} carries no headers under source/common and source/i18n" >&2
    echo "  the pin must be ICU's SOURCE archive (asset_pattern = \"icu4c-*-sources.tgz\")" >&2
    return 1
  fi
  echo "${where}"
}

org_pgrx_darwin_icu_pin() { # usage: org_pgrx_darwin_icu_pin <ICU majors to add…>
  # The pin to write, whole: what mise.toml pins already and the majors
  # to add. One ICU major is the single-table form; more is the array
  # form, one table per ICU major, which mise installs side by side.
  local listing pinned version major entries=()
  local keys='version_prefix = "release-", asset_pattern = "icu4c-*-sources.tgz", os = ["macos"]'
  listing=$(mise ls "github:unicode-org/icu" --json) || return 1
  pinned=$(jq -r '.[] | select(.active and .installed) | .version' <<< "${listing}") || return 1
  while read -r version; do
    [[ -z ${version} ]] || entries+=("${version}")
  done <<< "${pinned}"
  for major in "$@"; do
    entries+=("${major}.<minor>")
  done
  echo "  pin them beside the Postgres in mise.toml (macOS only; the Linux runner has libicu-dev):"
  if [[ ${#entries[@]} -eq 1 ]]; then
    echo "  \"github:unicode-org/icu\" = { version = \"${entries[0]}\", ${keys} }"
    return 0
  fi
  echo '  "github:unicode-org/icu" = ['
  for version in "${entries[@]}"; do
    echo "    { version = \"${version}\", ${keys} },"
  done
  echo "  ]"
}

org_pgrx_init() { # usage: org_pgrx_init < records from pgrx_postgres.py
  # Registers every major in one call. Measured: five majors, including
  # the initdb each one needs, in 17 seconds — so this is run per task
  # rather than cached, and re-running it is idempotent.
  local args=() record major where
  while IFS=$'\t' read -r record major where; do
    [[ ${record} == "pg" && -n ${major} && -n ${where} ]] || continue
    args+=("--pg${major}" "${where}")
  done
  if [[ ${#args[@]} -eq 0 ]]; then
    echo "pgrx-env: no Postgres to register — the plan was empty" >&2
    return 1
  fi
  command -v cargo-pgrx > /dev/null || {
    echo "pgrx-env: cargo-pgrx missing — a pgrx crate pins it as a build input:" >&2
    echo '  "cargo:cargo-pgrx" = "0.19.2"   # must equal the pgrx dependency' >&2
    return 1
  }
  # ONE INIT AT A TIME. `ci` starts every task that needs a Postgres
  # together, and each registers the same majors into the same
  # PGRX_HOME. `cargo pgrx init` is idempotent run after run and not run
  # beside run: two of them both find no data directory, both call
  # initdb, and the loser dies on
  #
  #   initdb: error: directory ".../.pgrx/data-16" exists but is not empty
  #
  # Measured on the runner the first time coverage joined the three that
  # were already there (release-lab#311) — never locally, where the
  # tasks had only been run one after another. So the init is serialised
  # with an exclusive lock on a file beside PGRX_HOME. The waiter then
  # runs its own init over a finished home, which is the idempotent case.
  #
  # The lock is the KERNEL's (#1481): `with_lock.py` holds an `flock` for
  # as long as the locked command runs, and the kernel releases it when
  # the holder exits, killed included — mise stops the sibling tasks
  # when one fails. #1140's `mkdir` lock named its holder in a pid file
  # and let a waiter remove a lock whose holder `kill -0` called dead;
  # on a Linux runner two inits then ran at once with that lock in place
  # (#1481). A guess at liveness is the defect, so nothing here guesses:
  # no pid, no stale branch, and only the holder ever lets the lock go.
  # `flock(1)` would do the same and macOS does not ship it. A new name,
  # `.init.flock`, so a directory the old lock left behind cannot stand
  # where the file must be. The ten-minute bound is the helper's timeout.
  local home="${PGRX_HOME:-${HOME}/.pgrx}"
  local lock="${home}.init.flock"
  mkdir -p "$(dirname "${home}")"
  : "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
  "${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/with_lock.py" --timeout 600 \
    --message "pgrx-env: waited ten minutes for another task's Postgres init (${lock})" \
    "${lock}" -- bash -euo pipefail -c \
    "$(declare -f org_pgrx_init_locked); org_pgrx_init_locked \"\$@\"" \
    org_pgrx_init_locked "${home}" "${args[@]}"
}

org_pgrx_init_locked() { # usage: org_pgrx_init_locked <home> <init args…>, under the lock
  local home="$1" status=0
  shift
  # AN INIT THAT CHANGED NOTHING MUST NOT LOOK LIKE A CHANGE (#1165).
  # pgrx-pg-sys's build script declares `rerun-if-changed` on
  # ${PGRX_HOME}/config.toml, and `cargo pgrx init` rewrites that file
  # on every call — same registrations, new timestamp. Cargo compares
  # timestamps, so every task that initialised re-ran bindgen and
  # recompiled pgrx-pg-sys, pgrx, pgrx-tests and the extension, whatever
  # the target directory already held. Measured with cargo's own
  # fingerprint log on a second, unchanged `test:pgrx`:
  #
  #   dirty: FsStatusOutdated(StaleItem(ChangedFile {
  #     stale: ".../.pgrx/config.toml" ... }))
  #
  # and on the runner as 65 of a leg's 70 seconds, on an exact cache hit.
  #
  # So the file's timestamp is kept when its registrations are the ones
  # it already had. Compared as a sorted set: pgrx writes the majors in
  # hash order, which differs run to run and means nothing. A real
  # change — a major added, a Postgres pin moved, which moves the
  # pg_config path — differs in content, keeps its new timestamp and
  # rebuilds, which is the case the build script declared the file for.
  local config="${home}/config.toml" before=""
  if [[ -f ${config} ]]; then
    before=$(mktemp) || before=""
    [[ -z ${before} ]] || cp -p "${config}" "${before}" || {
      rm -f "${before}"
      before=""
    }
  fi
  cargo pgrx init "$@" || status=$?
  if [[ -n ${before} ]]; then
    # One step per assignment: a sort that failed must read as "changed"
    # and keep the new timestamp, never as two empty, equal sets.
    local was="" now=""
    if [[ ${status} -eq 0 && -f ${config} ]] \
      && was=$(LC_ALL=C sort "${before}") \
      && now=$(LC_ALL=C sort "${config}") \
      && [[ -n ${was} && ${was} == "${now}" ]]; then
      touch -r "${before}" "${config}"
    fi
    rm -f "${before}"
  fi
  return "${status}"
}

org_pgrx_provision() { # usage: org_pgrx_provision
  # For a caller that compiles the WHOLE workspace rather than naming the
  # extension (#917): the mutation and semver audits. A workspace-wide
  # cargo run reaches a pgrx crate exactly as `cargo pgrx test` does, and
  # `cargo llvm-cov --workspace` was the first such path, with no Postgres: measured on release-lab, it died in pgrx-bindgen on
  # "'inttypes.h' file not found" on macOS, and on a cold runner nothing
  # had registered a pg_config at all.
  #
  # The extension is DERIVED here as everywhere else, from the same file,
  # so a repository declares nothing.
  #
  # A workspace with no extension returns before sourcing anything else,
  # so it pays for one `cargo metadata` and nothing more.
  local belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
  local metadata surface installed plan record _spec kind crate_majors _dir
  local majors=""
  # One step per assignment, never a pipeline inside `$(...)`: this file
  # is sourced, so it cannot rely on its caller having set pipefail, and
  # a failed derivation that arrived as an empty surface would read as a
  # workspace with no extension — the skip #813 exists to end.
  metadata=$(cargo metadata --no-deps --format-version 1 --locked) || return 1
  : "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
  surface=$("${ORG_PYTHON}" "${belt}/gate_surface.py" <<< "${metadata}") || return 1
  while IFS=$'\t' read -r record _spec kind crate_majors _dir; do
    [[ ${record} == crate && ${kind} == pgrx ]] || continue
    majors="${majors}${majors:+,}${crate_majors}"
  done <<< "${surface}"
  [[ -n ${majors} ]] || return 0
  org_pgrx_darwin_flags || return 1
  installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  plan=$("${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "${majors}" <<< "${installed}") || return 1
  org_pgrx_init <<< "${plan}" > /dev/null
}

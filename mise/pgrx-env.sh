#!/usr/bin/env bash
# Provisioning a pgrx build, once, for every belt task that needs one
# (#813). Sourced by belt tasks (`. "${ORG_BELT_DIR}/pgrx-env.sh"`),
# never executed.
#
# The tasks that compile a pgrx extension — `lint:pg-clippy`,
# `test:pgrx`, `fix:rust`'s extension pass, and the workspace-wide
# mutation and semver audits — each need the same two things first: a
# Postgres of each major they build, theirs alone (#1940), and, on
# macOS, a correction to flags the prebuilt Postgres recorded on
# somebody else's machine. Written in each they would drift; written
# here they cannot.
#
# The coverage measurement is no longer among them (#1656). It compiled
# the extension under `cargo llvm-cov --workspace` (#917), but the
# extension's functions run in the Postgres backend, which that run never
# collected from, so every line read as zero (#1652). The rust leg now
# leaves the extension out, and `test:pgrx` measures it, each major's
# run built instrumented where the repository holds a floor.
# The writer beside this file, found with no command run (#1934).
org__here=${BASH_SOURCE[0]%/*}
[[ ${org__here} != "${BASH_SOURCE[0]}" ]] || org__here=.
# shellcheck source=mise/findings.sh
. "${org__here}/findings.sh"
unset org__here

org_pgrx_highest() { # usage: org_pgrx_highest < gate_surface.py's records
  # WHICH MAJOR EACH EXTENSION IS BUILT AT, derived once (#1795). The
  # tasks that compile one extension per major they name (lint:pg-clippy,
  # fix:rust's extension pass, lint:msrv) build each at its HIGHEST
  # declared major: that is where a newly deprecated API surfaces first,
  # and test:pgrx compiles every major anyway. Written in each task it
  # was three copies, and two of them kept only the last extension's
  # major, so a workspace with two extensions built both at one.
  #
  # One line per extension, `<spec><TAB><highest major>`, the major the
  # last of the record's comma-separated majors (gate_surface.py writes
  # them ascending). A record that is excluded, or a crate that is not an
  # extension, prints nothing; so does a workspace with no extension.
  local record spec kind majors _dir
  while IFS=$'\t' read -r record spec kind majors _dir; do
    [[ ${record} == crate && ${kind} == pgrx ]] || continue
    printf '%s\t%s\n' "${spec}" "${majors##*,}"
  done
  return 0
}

org_pgrx_union() { # usage: org_pgrx_union < org_pgrx_highest's lines
  # The majors those builds need, each once, ascending,
  # comma-separated: what org_pgrx_darwin_prepare and pgrx_postgres.py
  # --majors take. Nothing for no extension. A major that is not a
  # number reds by name rather than reaching pgrx as a feature it has
  # never heard of.
  local spec major joined="" wanted=()
  while IFS=$'\t' read -r spec major; do
    [[ -n ${spec} || -n ${major} ]] || continue
    if [[ ! ${major} =~ ^[1-9][0-9]*$ ]]; then
      org_finding "" "${spec} names no Postgres major: '${major}'"
      return 1
    fi
    wanted[major]=1
  done
  # An indexed array lists its indices ascending: the sort and the
  # de-duplication are bash's own.
  for major in "${!wanted[@]}"; do
    joined="${joined}${joined:+,}${major}"
  done
  [[ -z ${joined} ]] || echo "${joined}"
}

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
    org_finding "" "xcrun could not name an SDK — install the Command Line Tools"
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
  if [[ ${rc} -eq 3 ]]; then
    # The finding, the pin to add as its last detail lines: one call.
    org__pgrx_icu_findings "${where}" "${icu}" || return 1
  fi
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
  local kernel major icu rc out failed=0 seen="" missing=() found=()
  kernel=$(uname -s)
  [[ ${kernel} == "Darwin" ]] || return 0
  for major in ${1//,/ }; do
    icu=$(org_pgrx_darwin_icu_of "${major}") || return 1
    [[ -n ${icu} ]] || continue
    if [[ " ${seen} " == *" ${icu} "* ]]; then
      continue
    fi
    seen="${seen} ${icu}"
    rc=0
    out=$(org_pgrx_darwin_icu_headers "${icu}" "${major}") || rc=$?
    if [[ ${rc} -eq 3 ]]; then
      missing+=("${icu}")
      found+=("${out}")
    fi
    [[ ${rc} -eq 0 ]] || failed=1
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    org__pgrx_icu_findings "$(printf '%s\n' "${found[@]}")" "${missing[@]}" || return 1
  fi
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

org_pgrx_major() { # usage: org_pgrx_major <Postgres major, or empty> <command…>
  # One command that compiles the extension at one Postgres major, run in
  # a subshell holding that major's ICU headers (#1774) and its Postgres
  # (#1940): both end with the command, so the next major starts from the
  # caller's environment. Empty: a command that compiles no Postgres, run
  # as it is.
  local major="$1"
  shift
  (
    [[ -z ${major} ]] || org_pgrx_major_env "${major}" || exit 1
    "$@"
  )
}

org_pgrx_major_env() { # usage: org_pgrx_major_env <Postgres major>, in the subshell that builds it
  # What org_pgrx_major exports, for a subshell that runs more than one
  # command at the major (test:pgrx's measured run): the ICU headers,
  # then the Postgres.
  org_pgrx_darwin_icu "$1" || return 1
  org_pgrx_postgres "$1"
}

org_pgrx_postgres() { # usage: org_pgrx_postgres <Postgres major>, in the subshell that builds it
  # NO PGRX TASK WRITES OR READS STATE ANOTHER TASK CAN SEE (#1940).
  # pgrx finds its Postgres in ${PGRX_HOME}/config.toml, which `cargo
  # pgrx init` writes, and installs the extension it tests into that
  # Postgres. Both were one per machine: every init rewrote the file
  # while other tasks' builds digested it ("config.toml changed after
  # inspection", pgrx 0.20), and two worktrees testing one extension
  # overwrote each other's library in the one install. Two locks, a
  # timestamp restore and a cache line managed that sharing; this ends
  # it. The belt never runs `cargo pgrx init`. Instead:
  #
  #   - PGRX_PG_CONFIG_PATH names this major's pg_config. pgrx-pg-config
  #     takes it in place of the file, and `cargo pgrx test`, `cargo
  #     pgrx schema`, the build script and pgrx-tests all read it.
  #   - PGRX_HOME is an empty directory nothing writes: bindgen tracks
  #     config.toml only when it is a file, and none ever exists there.
  #     The value is the same on every call, so it never changes a
  #     build's fingerprint (PGRX_HOME is a recorded input), and pgrx
  #     0.20 watches the directory itself, so it is made once with a
  #     fixed, old mtime (org__pgrx_home): made fresh on a runner whose
  #     `target` came from the cache, a new mtime rebuilt pgrx-pg-sys
  #     and everything above it on every run (measured on #1940:
  #     1m36s against 3.1s).
  #   - The pg_config is a CLONE of the pinned install, one per cargo
  #     target directory (org__pgrx_clone). Tasks that share a target
  #     directory share a clone, so they give pgrx one
  #     PGRX_PG_CONFIG_PATH and never rebuild each other; tasks that
  #     install (test:pgrx, coverage, mutants) build in target
  #     directories of their own, so each installs into its own clone.
  #
  # The pinned install is pgrx_postgres.py's answer for the major, the
  # same check that reds a declared major with no pin.
  : "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
  local belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
  local installed plan record major where source="" target key cache clone
  installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  plan=$("${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "$1" <<< "${installed}") || return 1
  while IFS=$'\t' read -r record major where; do
    [[ ${record} == pg && ${major} == "$1" ]] || continue
    source=${where}
  done <<< "${plan}"
  if [[ ${source} != */bin/pg_config ]]; then
    org_finding "" "pg$1: pgrx_postgres.py named no pg_config under a bin/ directory: '${source}'"
    return 1
  fi
  target=$(org__pgrx_target) || return 1
  key=$(org__pgrx_key "${target}") || return 1
  cache="${XDG_CACHE_HOME:-${HOME}/.cache}/org-belt"
  org__pgrx_home "${cache}/pgrx-home" || return 1
  clone=$(org__pgrx_clone "${source%/bin/pg_config}" "${cache}/pgrx-postgres/${key}/pg$1") || return 1
  export PGRX_PG_CONFIG_PATH="${clone}/bin/pg_config"
  export PGRX_HOME="${cache}/pgrx-home"
}

org__pgrx_target() { # prints the absolute cargo target directory of the command about to run
  # CARGO_TARGET_DIR when set, as cargo reads it, relative to here; else
  # cargo's own answer, which also follows build.target-dir.
  local target="${CARGO_TARGET_DIR:-}" metadata
  if [[ -z ${target} ]]; then
    metadata=$(cargo metadata --no-deps --format-version 1) || return 1
    target=$(jq -r '.target_directory // empty' <<< "${metadata}") || return 1
    if [[ -z ${target} ]]; then
      org_finding "" "cargo metadata reported no target_directory"
      return 1
    fi
  fi
  [[ ${target} == /* ]] || target="${PWD}/${target}"
  echo "${target}"
}

org__pgrx_key() { # usage: org__pgrx_key <path>; prints the first 16 hex of its sha256
  local sum
  if command -v sha256sum > /dev/null; then
    sum=$(printf '%s' "$1" | sha256sum) || return 1
  else
    sum=$(printf '%s' "$1" | shasum -a 256) || return 1
  fi
  if [[ ! ${sum} =~ ^([0-9a-f]{16})[0-9a-f]{48}([[:space:]]|$) ]]; then
    org_finding "" "could not hash the target directory '$1': '${sum}'"
    return 1
  fi
  echo "${BASH_REMATCH[1]}"
}

org__pgrx_home() { # usage: org__pgrx_home <directory>; makes it once, empty, with a fixed old mtime
  # Made as `<directory>.<random>`, dated, and published by
  # org__pgrx_publish, so no task ever sees it with a new mtime: 2000-01-01
  # UTC is older than any build, so the watched path never reads as
  # changed. Two tasks racing both publish (rename(2) replaces an EMPTY
  # directory), and either one is the same empty, dated directory.
  local tmp
  [[ ! -d $1 ]] || return 0
  mkdir -p "${1%/*}" || return 1
  tmp=$(mktemp -d "$1.XXXXXX") || return 1
  TZ=UTC touch -t 200001010000 "${tmp}" || {
    rm -rf "${tmp}"
    return 1
  }
  org__pgrx_publish "${tmp}" "$1" || rm -rf "${tmp}"
  [[ -d $1 ]]
}

org__pgrx_publish() { # usage: org__pgrx_publish <made directory> <name>; status 0 when it took the name
  # A directory renamed onto its name: rename(2) of a directory creates
  # the name or fails when a non-empty directory holds it, atomically, so
  # the loser of a race for a clone removes its own and uses the
  # winner's. Not a
  # symbolic link (`ln -sn`, the belt tree's pattern): pg_config resolves
  # links, so behind one pgrx saw the random `<name>.<random>` path, a
  # new one on every runner, and rebuilt (#1940). Python's, because
  # macOS mv has no -T and would move the directory INTO an existing
  # one.
  "${ORG_PYTHON}" -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$1" "$2" 2> /dev/null
}

org__pgrx_clone() { # usage: org__pgrx_clone <install directory> <directory holding its clones>; prints the clone
  # MADE ONCE, THEN REUSED. Cloned beside its final name as
  # `<name>.<random>` and published by renaming it onto the name
  # (org__pgrx_publish), so its real path is the same on every machine
  # and every run. `cp -c` is an APFS clone on Darwin (0.67 s for pg16's
  # 261 MB, measured on #1940); Linux has no clone on ext4, and
  # `--reflink=auto` copies there, about a second. `-p` keeps the
  # install's mtimes: pgrx watches the include directories, and a clone
  # made fresh on a runner must not read as newer than the cached build.
  #
  # The install directory's name carries the full version and target
  # triple, so a Postgres pin that moves makes a new clone, and the
  # publish removes the clones of other versions of this major: what
  # else is under this directory. A `<name>.<random>` that is not
  # published yet is another task's clone in progress, and is left.
  local source="$1" dir="$2" name dest tmp kernel entry copied=0
  name=${source##*/}
  dest="${dir}/${name}"
  if [[ ! -x ${dest}/bin/pg_config ]]; then
    mkdir -p "${dir}" || return 1
    tmp=$(mktemp -d "${dest}.XXXXXX") || return 1
    kernel=$(uname -s)
    if [[ ${kernel} == Darwin ]]; then
      cp -cRp "${source}/." "${tmp}" && copied=1
    else
      cp -Rp --reflink=auto "${source}/." "${tmp}" && copied=1
    fi
    if [[ ${copied} -ne 1 ]]; then
      rm -rf "${tmp}"
      org_finding "" "could not clone the pinned Postgres ${source} to ${tmp}"
      return 1
    fi
    if org__pgrx_publish "${tmp}" "${dest}"; then
      for entry in "${dir}"/*; do
        [[ ${entry} == "${dest}" || ${entry} == "${dest}".* ]] || rm -rf "${entry}"
      done
    else
      rm -rf "${tmp}"
    fi
  fi
  if [[ ! -x ${dest}/bin/pg_config ]]; then
    org_finding "" "${dest} holds no bin/pg_config — remove it, and the next task clones it again"
    return 1
  fi
  echo "${dest}"
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
  # is a pin to add: then it prints the finding itself on stdout as one
  # line for org_finding_lines (#1934), and the caller appends the pin
  # for every missing major at once (org_pgrx_darwin_icu_pin) as its
  # detail.
  local listing pinned version where
  listing=$(mise ls "github:unicode-org/icu" --json) || return 1
  pinned=$(jq -r '[.[] | select(.active and .installed) | .version] | join(" ")' <<< "${listing}") || return 1
  version=$(jq -r --arg major "$1" '[.[] | select(.active and .installed)
    | select(((.version // "") | split(".")[0]) == $major)] | last | .version // empty' <<< "${listing}") || return 1
  where=$(jq -r --arg major "$1" '[.[] | select(.active and .installed)
    | select(((.version // "") | split(".")[0]) == $major)] | last | .install_path // empty' <<< "${listing}") || return 1
  if [[ -z ${version} || -z ${where} ]]; then
    if [[ -z ${pinned} ]]; then
      printf '\037%s\037%s\n' "pg$2 was built against ICU $1, and mise.toml pins no ICU headers —" \
        "the prebuilt Postgres ships none, and pgrx binds every Postgres header from 0.19.3"
    else
      printf '\037%s\037%s\n' "pg$2 was built against ICU $1; mise.toml pins ICU ${pinned} and no $1.x —" \
        "the headers must be the major the shipped dylibs are, one pin per ICU major"
    fi
    return 3
  fi
  if [[ ! -f ${where}/source/common/unicode/utypes.h || ! -f ${where}/source/i18n/unicode/ucol.h ]]; then
    org_finding "" "ICU ${version} at ${where} carries no headers under source/common and source/i18n" \
      "the pin must be ICU's SOURCE archive (asset_pattern = \"icu4c-*-sources.tgz\")"
    return 1
  fi
  echo "${where}"
}

org__pgrx_icu_findings() { # usage: org__pgrx_icu_findings <finding lines> <ICU majors to add…>
  # The headers' findings, one per line, through the writer, the pin to
  # add appended as the last one's detail (#1934): the remedy belongs to
  # the finding, so it is that call's detail, not a line of its own.
  local findings=$1 pin line last
  shift
  pin=$(org_pgrx_darwin_icu_pin "$@") || return 1
  last=${findings##*$'\n'}
  while IFS= read -r line; do
    last+=$'\037'"${line#  }"
  done <<< "${pin}"
  if [[ ${findings} == *$'\n'* ]]; then
    org_finding_lines <<< "${findings%$'\n'*}"$'\n'"${last}"
  else
    org_finding_lines <<< "${last}"
  fi
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

org_pgrx_provision() { # usage: org_pgrx_provision <variable>; sets it to the default major
  # For a caller that compiles the WHOLE workspace rather than naming the
  # extension (#917): the mutation and semver audits. A workspace-wide
  # cargo run reaches a pgrx crate exactly as `cargo pgrx test` does, and
  # `cargo llvm-cov --workspace` was the first such path, with no
  # Postgres: measured on release-lab, it died in pgrx-bindgen on
  # "'inttypes.h' file not found" on macOS, and on a cold runner nothing
  # had a pg_config for it at all.
  #
  # Such a run builds each extension at its DEFAULT major, the one `pgNN`
  # its `default` feature names, derived as mutants_rust.py's
  # default_major does. So this corrects the SDK, checks every declared
  # major has its pin, and sets the variable the caller names to that
  # major (the digits) for it to run its workspace command through
  # `org_pgrx_major` with (#1940). Into a variable, not on stdout: the
  # SDK correction is exported into the caller's shell, which a command
  # substitution's subshell would lose. The name must be none of this
  # function's locals, which would take the value instead. Two extensions defaulting to
  # different majors are red: cargo unifies their features in one build,
  # and pgrx compiles one major.
  #
  # The extension is DERIVED here as everywhere else, from the same file,
  # so a repository declares nothing.
  #
  # A workspace with no extension returns before sourcing anything else,
  # so it pays for one `cargo metadata` and nothing more, and leaves the
  # variable empty: its workspace command runs as it is.
  local belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: lefthook run post-checkout)}"
  local metadata surface installed record spec kind crate_majors _dir crate_default
  local majors="" defaults=""
  printf -v "$1" '%s' ""
  # One step per assignment, never a pipeline inside `$(...)`: this file
  # is sourced, so it cannot rely on its caller having set pipefail, and
  # a failed derivation that arrived as an empty surface would read as a
  # workspace with no extension — the skip #813 exists to end.
  metadata=$(cargo metadata --no-deps --format-version 1 --locked) || return 1
  : "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"
  surface=$("${ORG_PYTHON}" "${belt}/gate_surface.py" <<< "${metadata}") || return 1
  while IFS=$'\t' read -r record spec kind crate_majors _dir; do
    [[ ${record} == crate && ${kind} == pgrx ]] || continue
    majors="${majors}${majors:+,}${crate_majors}"
    crate_default=$("${ORG_PYTHON}" "${belt}/mutants_rust.py" default-major "${spec%@*}" <<< "${metadata}") || return 1
    [[ -z ${crate_default} || " ${defaults} " == *" ${crate_default#pg} "* ]] \
      || defaults="${defaults}${defaults:+ }${crate_default#pg}"
  done <<< "${surface}"
  [[ -n ${majors} ]] || return 0
  if [[ ${defaults} == *" "* ]]; then
    org_finding "" "the workspace's extensions default to different majors: ${defaults// /, }" \
      "a workspace-wide build unifies their features, and pgrx compiles one major"
    return 1
  fi
  org_pgrx_darwin_flags || return 1
  installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  "${ORG_PYTHON}" "${belt}/pgrx_postgres.py" --majors "${majors}" <<< "${installed}" > /dev/null || return 1
  printf -v "$1" '%s' "${defaults}"
}

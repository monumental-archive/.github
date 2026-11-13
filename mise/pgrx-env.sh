#!/usr/bin/env bash
# Provisioning a pgrx build, once, for every belt task that needs one
# (#813). Sourced by belt tasks (`. "${ORG_BELT_DIR}/pgrx-env.sh"`),
# never executed.
#
# Four tasks compile a pgrx extension — `lint:pg-clippy`, `test:pgrx`,
# `fix:rust`'s extension pass and the coverage measurement
# (`coverage:check`, `coverage:adopt` and `coverage:report` share it,
# #917) — and each needs the same two things first: a `pg_config` per
# declared major registered with pgrx, and, on macOS, a correction to
# flags the prebuilt Postgres recorded on somebody else's machine.
# Written four times they would drift; written here they cannot.

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
  org_pgrx_darwin_icu
}

org_pgrx_darwin_icu() { # put ICU's headers where the prebuilt Postgres expects them
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
  # WHICH ICU IS DERIVED, never declared: the major is read out of each
  # pinned Postgres's own `pg_config --cppflags`, so the pin is checked
  # against what the server was actually built with. A header set from a
  # different major would bind against declarations the shipped dylibs
  # do not have, and nothing downstream would say so.
  local listing paths path pg_config flags major needed="" icu version where
  listing=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  # ACTIVE entries only, the rule pgrx-postgres.py states: `mise ls`
  # reports everything installed on the machine, and a copy somebody
  # installed by hand is not a pin — it passes here and is absent in CI.
  paths=$(jq -r '.[] | select(.active and .installed) | .install_path' <<< "${listing}") || return 1
  while read -r path; do
    [[ -n ${path} ]] || continue
    for pg_config in "${path}"/bin/pg_config "${path}"/*/bin/pg_config; do
      [[ -x ${pg_config} ]] || continue
      flags=$("${pg_config}" --cppflags) || return 1
      [[ ${flags} =~ icu4c@([0-9]+) ]] || continue
      major="${BASH_REMATCH[1]}"
      [[ " ${needed} " == *" ${major} "* ]] || needed="${needed} ${major}"
    done
  done <<< "${paths}"
  # No pinned Postgres names an ICU: nothing was built against one, or no
  # Postgres is pinned yet and pgrx-postgres.py will say so in its own
  # words. Either way there is nothing here to correct.
  needed="${needed# }"
  [[ -n ${needed} ]] || return 0
  if [[ ${needed} == *" "* ]]; then
    echo "pgrx-env: the pinned Postgres builds were compiled against different ICU majors (${needed})" >&2
    echo "  one include path cannot serve them all — pin Postgres builds that share one" >&2
    return 1
  fi
  icu=$(mise ls "github:unicode-org/icu" --json) || return 1
  version=$(jq -r '[.[] | select(.active and .installed)] | last | .version // empty' <<< "${icu}") || return 1
  where=$(jq -r '[.[] | select(.active and .installed)] | last | .install_path // empty' <<< "${icu}") || return 1
  if [[ -z ${version} || -z ${where} ]]; then
    echo "pgrx-env: the pinned Postgres was built against ICU ${needed} and ships no headers —" >&2
    echo "  pgrx binds every Postgres header from 0.19.3, so the build needs them. Pin them" >&2
    echo "  beside the Postgres in mise.toml (macOS only; the Linux runner has libicu-dev):" >&2
    echo '  "github:unicode-org/icu" = { version = "<major>.<minor>", version_prefix = "release-",' >&2
    echo '    asset_pattern = "icu4c-*-sources.tgz", os = ["macos"] }' >&2
    return 1
  fi
  if [[ ${version%%.*} != "${needed}" ]]; then
    echo "pgrx-env: mise.toml pins ICU ${version}; the pinned Postgres was built against ICU ${needed}" >&2
    echo "  the headers must be the major the shipped dylibs are — pin a ${needed}.x release" >&2
    return 1
  fi
  if [[ ! -f ${where}/source/common/unicode/utypes.h || ! -f ${where}/source/i18n/unicode/ucol.h ]]; then
    echo "pgrx-env: ${where} carries no ICU headers under source/common and source/i18n" >&2
    echo "  the pin must be ICU's SOURCE archive (asset_pattern = \"icu4c-*-sources.tgz\")" >&2
    return 1
  fi
  local include="-I${where}/source/common -I${where}/source/i18n"
  export BINDGEN_EXTRA_CLANG_ARGS="${include} ${BINDGEN_EXTRA_CLANG_ARGS:-}"
  export CFLAGS="${include} ${CFLAGS:-}"
}

org_pgrx_init() { # usage: org_pgrx_init < records from pgrx-postgres.py
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
  cargo pgrx init "${args[@]}"
}

org_pgrx_provision() { # usage: org_pgrx_provision <label> <held-out members>
  # For a caller that compiles the WHOLE workspace rather than naming the
  # extension (#917). `cargo llvm-cov --workspace` reaches a pgrx crate
  # exactly as `cargo pgrx test` does, and it was the one path that got
  # no Postgres: measured on release-lab, it died in pgrx-bindgen on
  # "'inttypes.h' file not found" on macOS, and on a cold runner nothing
  # had registered a pg_config at all.
  #
  # The extension is DERIVED here as everywhere else, from the same file,
  # so a repository declares nothing. Which is also why a held-out list
  # that names one is refused rather than honoured: COVERAGE_EXCLUDE was
  # the last place a repository still declared a pgrx crate, and an
  # exclusion there is CLIPPY_EXCLUDE under a better name — a coverage
  # floor derived from a surface missing an entire published artifact.
  #
  # A workspace with no extension returns before sourcing anything else,
  # so it pays for one `cargo metadata` and nothing more.
  local label="$1" held="$2"
  local belt="${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}"
  local metadata surface installed plan record spec kind crate_majors _dir member
  local majors="" named=""
  # One step per assignment, never a pipeline inside `$(...)`: this file
  # is sourced, so it cannot rely on its caller having set pipefail, and
  # a failed derivation that arrived as an empty surface would read as a
  # workspace with no extension — the skip #813 exists to end.
  metadata=$(cargo metadata --no-deps --format-version 1 --locked) || return 1
  surface=$(python3 "${belt}/gate-surface.py" <<< "${metadata}") || return 1
  while IFS=$'\t' read -r record spec kind crate_majors _dir; do
    [[ ${record} == crate && ${kind} == pgrx ]] || continue
    for member in ${held}; do
      [[ ${member} != "${spec%@*}" ]] || named="${named} ${member}"
    done
    majors="${majors}${majors:+,}${crate_majors}"
  done <<< "${surface}"
  if [[ -n ${named} ]]; then
    echo "${label}: COVERAGE_EXCLUDE names a pgrx extension:${named}" >&2
    echo "  delete it from the list. The belt gives this task a Postgres, so the" >&2
    echo "  crate is measurable, and a floor that leaves out a published crate is" >&2
    echo "  a floor for something other than what ships (#917). Then re-derive:" >&2
    echo "    mise run coverage:adopt" >&2
    return 1
  fi
  [[ -n ${majors} ]] || return 0
  org_pgrx_darwin_flags || return 1
  installed=$(mise ls "aqua:theseus-rs/postgresql-binaries" --json) || return 1
  plan=$(python3 "${belt}/pgrx-postgres.py" --majors "${majors}" <<< "${installed}") || return 1
  org_pgrx_init <<< "${plan}" > /dev/null
}

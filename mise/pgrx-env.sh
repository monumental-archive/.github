#!/usr/bin/env bash
# Provisioning a pgrx build, once, for every belt task that needs one
# (#813). Sourced by belt tasks (`. "${ORG_BELT_DIR}/pgrx-env.sh"`),
# never executed.
#
# Three tasks compile a pgrx extension — `lint:pg-clippy`, `test:pgrx`
# and `fix:rust`'s extension pass — and each needs the same two things
# first: a `pg_config` per declared major registered with pgrx, and, on
# macOS, a correction to flags the prebuilt Postgres recorded on somebody
# else's machine. Written three times they would drift; written here they
# cannot.

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

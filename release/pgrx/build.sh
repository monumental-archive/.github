#!/usr/bin/env bash
# The pgrx-extension build's postgres half (#1380): toolchain, test
# suite, package, then the debug split and the floor block. Run by
# /pgrx/build-root.sh as the unprivileged postgres user.
set -euo pipefail
export HOME=/var/lib/postgresql
curl --proto "=https" --tlsv1.2 -sSf -o /tmp/rustup-init \
  "https://static.rust-lang.org/rustup/dist/$(uname -m)-unknown-linux-gnu/rustup-init"
chmod +x /tmp/rustup-init
/tmp/rustup-init -y --profile minimal \
  --default-toolchain "${RUST_VERSION}" --no-modify-path
. "${HOME}/.cargo/env"
cargo install cargo-pgrx --version "${PGRX_VERSION}" --locked
cargo install cargo-auditable --version "${AUDITABLE_VERSION}" --locked
# cargo-pgrx owns the cargo invocation, so cargo-auditable goes in
# through its documented wrapper mechanism (REPLACING_CARGO.md): a cargo
# shim first on PATH that delegates to the real cargo with the auditable
# subcommand. Every binary pgrx builds then carries its dependency tree
# in the .dep-v0 linker section, which the split keeps and asserts.
real_cargo=$(command -v cargo)
mkdir -p "${HOME}/cargo-shim"
printf "#!/bin/sh\nexec %s auditable \"\$@\"\n" \
  "${real_cargo}" > "${HOME}/cargo-shim/cargo"
chmod +x "${HOME}/cargo-shim/cargo"
export PATH="${HOME}/cargo-shim:${PATH}"
cargo pgrx init "--pg${PG}" /usr/bin/pg_config
cd "/build/${CRATE_DIR}"
# Reproducibility, by construction: same flags as the rust-binary class,
# applied inside the container.
export CARGO_INCREMENTAL=0
# The build must not strip, so the split has debug info to split. Not
# what keeps .dep-v0: that section is non-allocated PROGBITS, GNU strip
# keeps it at --strip-debug and --strip-unneeded, and only llvm-strip
# --strip-all removes it (measured, #1274).
export CARGO_PROFILE_RELEASE_STRIP=false
export RUSTFLAGS="--remap-path-prefix=/build=/src"
# The test suite, against the Postgres consumers run — real initdb, real
# clusters, not the pgrx-managed lookalike.
cargo pgrx test "pg${PG}"
cargo pgrx package \
  --no-default-features --features "pg${PG}" \
  --pg-config /usr/bin/pg_config \
  --out-dir /out/pkgroot
# Derived upgrade scripts (committed to sql/ by the Release PR) install
# alongside the generated schema: the guard demands sql/<ext>--A--B.sql
# exists, so the package must SHIP it, or ALTER EXTENSION UPDATE fails at
# the one consumer the guard protected. -print -quit, never a pipe into
# head: the first-match stop is built into find, so there is no pipe to
# break and no walk to finish (#682).
extdir=$(find /out/pkgroot -type d -name extension -print -quit)
if compgen -G "sql/*.sql" > /dev/null; then
  cp sql/*.sql "${extdir}/"
fi
/pgrx/debug-split.sh /out/pkgroot /out/debugroot
/pgrx/floor.sh "${extdir}" "${VERSION}" "${FLOOR_XY}" "${FLOOR_NUM}"
chmod -R a+rX /out/pkgroot /out/debugroot

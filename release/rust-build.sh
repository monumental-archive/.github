#!/usr/bin/env bash
# The org's Rust release compile, stated once (#775). Org canon — see
# docs/release.md, "The compile lives outside the Dockerfile".
#
# Two classes ship a Rust binary the org signs: `rust-binary`, whose
# artifact IS the binary, and `oci-image`, whose artifact is an image
# with a binary inside it. Before this script they compiled through two
# copies of one recipe — build-rust-binary.yml's own steps, and a
# `prepare` script every image consumer wrote for itself (measured
# 2026-08-21: release-lab's and iiif-server's were the same 25 lines
# with one binary name changed, #775). A second copy of a compile is a
# second set of reproducibility flags to forget, and the inventory plan
# the SBOM aggregator needs (#537) existed on only one of them, which is
# what made an oci-image-only repository unpublishable (#773).
#
# So: one definition, two callers. build-rust-binary.yml calls it with
# the whole workspace; build-oci-image.yml calls it with one package and
# stages into the image context. Everything a release depends on lives
# here — the determinism inputs, the toolchain assertions, the staging
# rule, and the plan — and a caller states only its scope.
#
# WHAT MAKES THE BYTES REPRODUCIBLE (#295, #118, #778), all five
# together — and the fifth is there because four was measured to be
# one short:
#
#   SOURCE_DATE_EPOCH          the released commit's own time, so the
#                              same tag rebuilds to the same bytes on
#                              any machine on any day. Measured: two
#                              clean builds of one crate differ without
#                              it and are byte-identical with it,
#                              because a build script that stamps a
#                              time has nothing else to read.
#   CARGO_INCREMENTAL=0        incremental artifacts embed local state
#   --remap-path-prefix        no runner paths inside the binary
#   CARGO_PROFILE_RELEASE_STRIP=false
#                              stripping would destroy the .dep-v0
#                              section cargo-auditable writes, which is
#                              the image-side dependency surface a
#                              scanner reads out of published bytes
#   CARGO_PROFILE_RELEASE_LTO=fat
#                              the release profile's default is thin
#                              LOCAL LTO across 16 codegen units, and
#                              that path does not reproduce (#778) —
#                              see the measurement below
#
# Inputs are environment variables, like every other script in release/:
#
#   TARGET                 rust target triple (required)
#   PACKAGE                one cargo package to build; empty builds the
#                          whole workspace
#   EXCLUDE                comma-separated workspace members to leave
#                          alone (workspace scope only — the
#                          pgrx-extension crate above all, which builds
#                          only inside its Postgres container)
#   STAGE_DIR              directory the staged binaries land in
#                          (required)
#   PLAN_CLASS             evidence class to emit an inventory plan for;
#                          empty emits none (the repro legs prove bytes,
#                          not inventories)
#   PLAN_DOC_PREFIX        document-name prefix for the plan, e.g.
#                          sbom-cargo- or sbom-image-. The prefix names
#                          what the artifact IS; the params name what it
#                          is MADE OF, and the two disagreeing silently
#                          is the #544 defect
#   PLAN_ARTIFACT_SUFFIX   appended to the package name to name the
#                          DOCUMENT apart from the package, when one
#                          package ships as more than one artifact
#   PLAN_DIR               directory the plan lands in (default: plan)
#   SOURCE_DATE_EPOCH      normally supplied by the caller; derived from
#                          the checked-out commit when it is not
set -euo pipefail

target="${TARGET:-}"
package="${PACKAGE:-}"
exclude="${EXCLUDE:-}"
stage_dir="${STAGE_DIR:-}"
plan_class="${PLAN_CLASS:-}"
plan_doc_prefix="${PLAN_DOC_PREFIX:-}"
plan_artifact_suffix="${PLAN_ARTIFACT_SUFFIX:-}"
plan_dir="${PLAN_DIR:-plan}"

[[ -n ${target} ]] || {
  echo "::error::rust-build: TARGET is unset"
  exit 1
}
[[ -n ${stage_dir} ]] || {
  echo "::error::rust-build: STAGE_DIR is unset"
  exit 1
}
if [[ -n ${plan_class} && -z ${plan_doc_prefix} ]]; then
  echo "::error::rust-build: PLAN_CLASS is set but PLAN_DOC_PREFIX is not — a plan names a document"
  exit 1
fi

# The scope, stated once and used by every step below: cargo's own
# metadata answers which packages exist and which of them ship binaries,
# because target/<triple>/release also holds build scripts and deps and a
# glob over it would ship someone else's bytes.
meta=$(cargo metadata --no-deps --format-version 1)
build_flags=()
if [[ -n ${package} ]]; then
  # A package is named by its manifest, never by its directory basename:
  # the two agree by convention and diverge without warning. Exactly one
  # package must live there, which is the same refusal build-wasm-npm
  # makes of its crate-dir.
  present=$(jq -r --arg p "${package}" \
    '[.packages[] | select(.name == $p) | .name] | length' <<< "${meta}")
  if [[ ${present} != "1" ]]; then
    echo "::error::rust-build: ${present} workspace package(s) named '${package}'"
    exit 1
  fi
  build_flags+=(--package "${package}")
  scope=$(jq -c --arg p "${package}" '[.packages[] | select(.name == $p)]' <<< "${meta}")
else
  build_flags+=(--workspace)
  scope=$(jq -c '.packages' <<< "${meta}")
  IFS=',' read -ra members <<< "${exclude// /}"
  for m in "${members[@]}"; do
    if [[ -n ${m} ]]; then
      build_flags+=(--exclude "${m}")
      scope=$(jq -c --arg m "${m}" '[.[] | select(.name != $m)]' <<< "${scope}")
    fi
  done
fi

# The toolchain the target needs, installed identically on every leg.
# musl-tools is unconditional on the Linux legs, deliberately: Rust's
# *-musl targets bundle their own musl libc and self-link pure Rust, but
# one C dependency (ring, mimalloc — both in the org's locks) needs a
# musl-targeting C compiler, and a transitive C dep can arrive in any PR.
# Identical environments beat conditional ones on a path that gets
# signed. Distro-signed via Ubuntu's own mirrors.
rustup target add "${target}"
if [[ ${target} == *-linux-musl ]]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq musl-tools
fi

# cargo-auditable is a build input like the toolchain itself, pinned in
# the caller's own mise config (cargo:cargo-auditable) — asserted, never
# installed here; an unpinned install on a release leg is a runner
# mutation.
command -v cargo-auditable > /dev/null || {
  emsg="cargo-auditable missing — pin cargo:cargo-auditable in this repository's mise config; it is a build"
  emsg+=" input of every class that ships a Rust binary"
  echo "::error::${emsg}"
  exit 1
}

if [[ -z ${SOURCE_DATE_EPOCH:-} ]]; then
  SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
fi
export SOURCE_DATE_EPOCH
export CARGO_INCREMENTAL=0
export CARGO_PROFILE_RELEASE_STRIP=false
# The fourth determinism input, and the one that was missing (#778).
# Cargo's release default is `lto = false`, which is NOT "no LTO": it
# performs thin LOCAL LTO across the profile's 16 codegen units, and
# that path does not reproduce. Measured on x86_64 GitHub runners,
# four independently allocated legs per candidate, cold, through this
# very script (iiif-server probe runs 32514356144 and 32514981377):
#
#   candidate            distinct digests / 4 legs   size      mean s
#   baseline (default)   4                           23.7 MB   141
#   codegen-units=1      4                           20.4 MB   148
#   lto="off"            4                           27.4 MB   118
#   lto="fat"            1                           18.2 MB   184
#   both                 1                           17.0 MB   182
#
# THE OBVIOUS CANDIDATE IS FALSIFIED, which is why the reasoning sits
# here rather than a bare flag. `codegen-units=1` removes every
# `.llvm.<hash>` symbol — rustc disables the local LTO entirely at one
# codegen unit — so `.strtab` becomes identical and the binaries still
# differ four ways out of four. Per-section digests say why: the
# sections that vary are `.note.gnu.build-id` (a hash of the content,
# so a consequence) and `.text` itself. The machine code varies, not
# just the symbol table, and only whole-graph LTO makes it stop.
# iiif-server v0.2.0's failure looked narrower than it was.
#
# `codegen-units=1` is deliberately NOT set beside this: measured
# insufficient alone, measured unnecessary with fat, and it costs
# front-half parallelism that grows with the workspace.
#
# The cost, stated rather than discovered: +30% wall time and −23%
# size on that graph, and fat LTO's peak memory scales with the whole
# dependency graph — a much larger workspace on a 7 GB runner could
# meet a limit thin-local never did. Checked before adopting: the
# `.dep-v0` section survives fat LTO (2881 bytes in the measured
# binaries), so the dependency surface a scanner reads is intact.
export CARGO_PROFILE_RELEASE_LTO=fat
# Set outright rather than appended: this script owns the flag set, so a
# caller's job-level RUSTFLAGS cannot half-apply it.
export RUSTFLAGS="--remap-path-prefix=${GITHUB_WORKSPACE:-${PWD}}=/build"

# `auditable`: the shipped binary carries its dependency tree in the
# .dep-v0 linker section (stripping is already off to preserve it), so
# image and binary scanners see the Rust surface of the artifact itself
# (docs/dependency-track.md).
cargo auditable build --locked --release --target "${target}" "${build_flags[@]}"

# Staging, from cargo's metadata rather than a directory listing. install(1)
# rather than cp: the mode is asserted, not inherited, so a COPY into an
# image cannot depend on the checkout's umask.
bin_list=$(jq -r '[.[].targets[] | select(.kind[] == "bin") | .name] | unique | .[]' <<< "${scope}")
bins=()
while IFS= read -r b; do
  if [[ -n ${b} ]]; then bins+=("${b}"); fi
done <<< "${bin_list}"
if ((${#bins[@]} == 0)); then
  echo "::error::rust-build: no bin targets in scope — nothing to ship"
  exit 1
fi
mkdir -p "${stage_dir}"
for b in "${bins[@]}"; do
  src="target/${target}/release/${b}"
  if [[ ! -x ${src} ]]; then
    echo "::error::rust-build: expected binary ${src} was not produced"
    exit 1
  fi
  install -m 0755 "${src}" "${stage_dir}/${b}"
done
echo "::notice::rust-build: staged ${#bins[@]} binary(ies) for ${target} in ${stage_dir}"

# The inventory plan: the artifact-to-package mapping as data, emitted
# where it is certain — here, in the job that just built the artifact
# from cargo's own metadata (#537). The sbom job derives each package's
# closure from it on the prove leg, so no caller ever declares (and
# drifts) a second copy of what this build knows. Every leg states the
# same mapping; the sbom job collapses identical entries and refuses
# divergent ones.
if [[ -n ${plan_class} ]]; then
  mkdir -p "${plan_dir}"
  jq --arg class "${plan_class}" --arg prefix "${plan_doc_prefix}" \
    --arg suffix "${plan_artifact_suffix}" \
    '[.[] | select([.targets[].kind[]] | index("bin")) | .name]
     | unique
     | map({class: $class, doc: ($prefix + .), params: {cargoPackage: .}}
           + (if $suffix == "" then {} else {artifact: (. + $suffix)} end))' \
    <<< "${scope}" > "${plan_dir}/plan.json"
  jq -r '.[] | "::notice::plans inventory \(.doc) for package \(.params.cargoPackage)"' \
    "${plan_dir}/plan.json"
fi

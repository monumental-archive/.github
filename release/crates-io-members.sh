#!/usr/bin/env bash
# The rust-crate class's one answer to "which crates does this release
# ship" (#1310): the workspace members whose Cargo `publish` key names
# crates.io, one `name<TAB>version` per line. Org canon — see the
# rust-crate section of docs/release.md.
#
# Two steps ask the question. build-rust-crate.yml's Package step keeps
# exactly these crates as release subjects (hashed, planned, uploaded
# and later fetched back from static.crates.io by the verifier), and
# publish-rust-crate.yml's Publish step uploads exactly these. They
# once carried two answers: `cargo package --workspace` writes a crate
# for a `publish = false` member too, so that member was a subject the
# verifier would fetch from a registry that never received it. Both
# steps now run this file, so there is no second copy to agree with.
#
# The key, as cargo metadata reports it (measured, cargo 1.97.1):
#   unset                 null           any registry, crates.io included
#   publish = false       []             none
#   publish = ["x", ...]  ["x", ...]     those registries only
#
# Run from the workspace root. Bash 3.2-clean: the release tests run it
# on a Mac, where /bin/bash is 3.2.
set -euo pipefail

cargo metadata --format-version 1 --no-deps --locked | jq -r '
  .workspace_members as $ws
  | .packages[]
  | select(.id as $id | $ws | index($id))
  | select(.publish == null or (.publish | index("crates-io")))
  | "\(.name)\t\(.version)"'

#!/usr/bin/env bash
# The org's pre-push gate: `mise run ci` over the working tree at HEAD.
#
# A script, not a `run:` line (#1281). lefthook 2.2.2 skips a pre-push
# `run:` job whose list of pushed files comes out empty, with or without
# a `glob`/`files` key (build_command.go:71-81 at v2.2.2), and builds
# that list from HEAD, keeping only paths that exist on disk. A push
# whose commits only delete files, or that pushes a ref while HEAD sits
# on a commit the remote already has, went out with the gate never run
# and one grey `(skip) no matching push files` line to say so. A
# `script:` job is never skipped for an empty list (build_script.go has
# no such check) — measured on every row of mise/test_prepush_hook.py.
#
# What it gates is the working tree at HEAD, never the ref being pushed:
# `git push origin other-branch` from a checkout on another commit gates
# the wrong tree. Stated here and in docs/migration-playbook.md, not
# fixed: pre-push hands lefthook the refs on stdin and this gate does
# not read them.
#
# lefthook finds this file in the canon's checkout under the CONSUMER's
# `source_dir` (default `.lefthook/`). A consumer that sets another
# `source_dir` gets no script, and lefthook fails the job ("script does
# not exist") rather than skipping it — measured in the same test.
#
# MISE_CEILING_PATHS: mise loads every config from the working directory
# up, so a worktree nested in its main checkout ran the MAIN checkout's
# tasks (#1232). The ceiling stops it at the tree being pushed.
# ONE RUN PER PUSH. lefthook runs a script from EVERY source directory
# that holds it, the tree's own `.lefthook/` before the remote's. The
# canon consumes itself, so its tree carries this file and the remote
# checkout carries it again: measured, `mise run ci` ran twice per push.
# The remote copy defers to the tree's, which lefthook has already run —
# in the canon that is the copy under review. Hooks are developer
# convenience and CI cannot be overridden, so a tree that plants its own
# copy here bypasses nothing the gate on GitHub does not catch.
set -euo pipefail

top="$(git rev-parse --show-toplevel)"
tree="${top}/.lefthook/pre-push/ci.sh"
if [[ -f ${tree} && ! ${BASH_SOURCE[0]} -ef ${tree} ]]; then
  echo "pre-push: ${tree} ran the gate for this push; not running it twice"
  exit 0
fi
MISE_CEILING_PATHS="$(dirname "${top}")" exec mise run ci

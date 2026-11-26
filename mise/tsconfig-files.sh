#!/usr/bin/env bash
# The one selection of a repository's TypeScript projects (#2133).
# Sourced, never executed: `. "${ORG_BELT_DIR}/tsconfig-files.sh"`.
# tsconfig_files is read by lint:types, lint:tsconfig-dials and
# mise/biome-generate.sh (lint:biome, fix:biome), so they cannot
# disagree about which projects a repository has: a config one checks
# and another does not is a program checked at a level nobody stated.
# The three used to carry the pathspecs each, kept alike by hand.
#
# A project is a tracked file named `tsconfig.json`, WHEREVER it lives.
# Tracked files only (org_tracked_read, #1986), never a walker, and
# vendor/ is not ours to check. The pathspecs are git's default meaning,
# where `*` crosses `/`: `tsconfig.json` is the root's and
# `*/tsconfig.json` every other, at any depth, which no
# `xtsconfig.json` matches. The copies also named `*/*/tsconfig.json`
# and `*/*/*/tsconfig.json`; measured, they selected nothing the two
# here do not. The caller names the array it fills; git's failure is
# the function's.
#
# What a project SAYS is asked beside this file, of the compiler:
# mise/tsconfig-resolve.sh. No task reads a tsconfig's text.
tsconfig_files() { # usage: <array>=() && tsconfig_files <array>
  org_tracked_read "$1" ':!vendor/**' "tsconfig.json" "*/tsconfig.json"
}

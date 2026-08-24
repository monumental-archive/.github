#!/usr/bin/env bash
# The org's clippy ladder, held once for every task that sets it (#813).
# Sourced by belt tasks (`. "${ORG_BELT_DIR}/clippy-ladder.sh"`), never
# executed.
#
# It moved out of `lint:rust`'s body when a SECOND task acquired the same
# obligation: `lint:pg-clippy` lints the pgrx extension the gate never
# compiled, at the same level, and a second copy of a ten-name list is
# the exact drift #752 recorded — the list said ten, the prose above it
# said nine, and `docs/best-practices.md` answered `warnings_strict` with
# nine. One list, two callers, no way for them to disagree.
#
# LEVELS ARE STATED AS GROUPS MINUS NAMED EXCLUSIONS, never as a list of
# lints to enable. Both forms measured identically at standup (97
# findings on release-lab) and they fail in opposite directions: an
# enable list is fail-OPEN, so a restriction group that grows leaves
# enforcement quietly behind; the exclusion form is fail-CLOSED, so a new
# lint arrives already enforced and a renamed exclusion stops applying
# and goes red where someone reads it. `docs/tooling-verdicts.md` carries
# the reasoning for each name.
#
# The two allows that are NOT contradictions stay at neither place: they
# are in the flags below with their own reasons, because they are
# properties of how the org runs clippy rather than pairs of lints that
# contradict each other.

# THE NAMED CONTRADICTIONS. An eleventh is one line, and every count a
# task prints follows it — which is the point of the list being here
# rather than spelled in prose. `docs/tooling-verdicts.md` is the counted
# source and says why each one is mechanical rather than taste.
ORG_CLIPPY_CONTRADICTIONS=(
  implicit_return
  question_mark_used
  self_named_module_files
  pub_with_shorthand
  semicolon_outside_block
  separated_literal_suffix
  big_endian_bytes
  little_endian_bytes
  arbitrary_source_item_ordering
  inline_asm_x86_intel_syntax
)

org_clippy_ladder() { # -> the flags that ride clippy's `--`, on one line
  # `-D warnings` also denies rustc's own warn-by-default lints, which is
  # the point: it is what makes the org answer `warnings_strict`
  # truthfully. It rides `--` rather than RUSTFLAGS, which the clippy
  # book's CI page suggests, because `--` args reach only workspace
  # members through RUSTC_WORKSPACE_WRAPPER — RUSTFLAGS would apply to
  # every dependency and invalidate the whole build cache.
  #
  # blanket_clippy_restriction_lints fires because we enable the group on
  # purpose; allowed with that as its reason. multiple_crate_versions is
  # allowed because the duplicate policy is stated once, in the repo's
  # deny.toml at `multiple-versions = "deny"` — this file is org-wide, so
  # an exception here would exempt every repo (docs/dependency-track.md).
  printf '%s' \
    "-D warnings" \
    " -D clippy::all -D clippy::pedantic -D clippy::nursery -D clippy::cargo" \
    " -D clippy::restriction" \
    " -A clippy::blanket_clippy_restriction_lints" \
    " -A clippy::multiple_crate_versions"
  local lint
  for lint in "${ORG_CLIPPY_CONTRADICTIONS[@]}"; do
    printf ' -A clippy::%s' "${lint}"
  done
  printf '\n'
}

# THE ONE FURTHER CONTRADICTION A PGRX EXTENSION CANNOT ESCAPE (#813).
# Same test as the ten above — obeying the lint means disobeying
# something else — and here the something else is pgrx itself.
#
# `tests_outside_test_module` demands `#[cfg(test)]`. pgrx demands
# `#[cfg(any(test, feature = "pg_test"))]`, because `cargo pgrx test`
# compiles the tests INTO the extension under that feature and runs them
# inside a live Postgres; under a bare `#[cfg(test)]` they are not built
# into the extension at all and the suite finds nothing. So the lint and
# the framework want incompatible attributes on the same module, and the
# repository cannot satisfy both.
#
# Measured on edtf's real extension crate before this was added: 7 of the
# 73 findings were this lint, one per `#[pg_test]`, on a module carrying
# exactly the cfg pgrx documents. It is named here rather than left to
# seven `#[expect]` attributes in every pgrx crate in the org forever,
# for the reason the ten above are named here: it is a property of the
# tools, not a judgement about this code.
ORG_CLIPPY_PGRX_CONTRADICTIONS=(
  tests_outside_test_module
)

org_clippy_pgrx_ladder() { # -> the ladder a pgrx extension is linted at
  local base lint
  base=$(org_clippy_ladder)
  printf '%s' "${base}"
  for lint in "${ORG_CLIPPY_PGRX_CONTRADICTIONS[@]}"; do
    printf ' -A clippy::%s' "${lint}"
  done
  printf '\n'
}

org_clippy_pgrx_contradiction_count() { # -> the pgrx ladder's count
  printf '%s\n' "$((${#ORG_CLIPPY_CONTRADICTIONS[@]} + ${#ORG_CLIPPY_PGRX_CONTRADICTIONS[@]}))"
}

org_clippy_contradiction_count() { # -> how many named contradictions apply
  # Read off the array's own length, which is legal HERE and is not legal
  # in a mise task body: mise renders bodies through Tera before bash
  # sees them, and bash's array-length syntax — dollar, brace, hash — is
  # also how a Tera comment opens, so a body using it dies at render time
  # with an unclosed-comment-tag error and never runs (#846 measured it,
  # and had to accumulate a counter by hand to avoid it). Moving the list
  # into this file retires that workaround along with the second copy.
  printf '%s\n' "${#ORG_CLIPPY_CONTRADICTIONS[@]}"
}

#!/usr/bin/env bash
# The one reader of the canon identity declaration (#579). Sourced by
# belt tasks (`. "${ORG_BELT_DIR}/identity.sh"`), never executed: a
# task whose correctness depends on a canon fact — the canon's own
# coordinates, the trusted signer ref, the shield endpoints — reads it
# through org_identity instead of restating it inline, so the belt
# holds one statement of each fact and the tasks cannot disagree about
# what a field means (the share-the-definition rule).
#
# Fails closed with the field named: an identity the reader cannot
# produce is a refusal, never a default or a silent skip (#568).
org_identity() { # usage: org_identity <field> -> value on stdout
  local file="${ORG_CANON_DIR:?ORG_CANON_DIR unset — the belt did not arrive (lint:belt-available)}/security/identity.toml"
  if [[ ! -f ${file} ]]; then
    echo "identity: ${file} missing — a tree carrying the belt owes the identity declaration (#579, lint:canon-policy)" >&2
    return 1
  fi
  local value
  value=$(sed -n "s/^${1} = \"\(.*\)\"$/\1/p" "${file}")
  if [[ -z ${value} ]]; then
    echo "identity: field '${1}' missing from ${file} — declare it; a task never defaults an identity (#579)" >&2
    return 1
  fi
  printf '%s\n' "${value}"
}

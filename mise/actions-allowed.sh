#!/usr/bin/env bash
# The belt's enforcement copy of the org Actions allowlist, in one place.
# Sourced by lint:actions-allowed, which judges every `uses:` against
# it, and by audit:actions-allowlist, which compares it with the live
# org setting: `. "${ORG_BELT_DIR}/actions-allowed.sh"`, never executed.
#
# The audit once scraped the lint's own text for its `allowed=` line.
# The lint's list became an array (#1448), the scrape matched nothing,
# and the audit died with no message: two readers of one list, one of
# them reading the other's spelling. Both now call this function, so a
# change to the list reaches both or neither.
#
# github_owned_allowed=true covers the actions/ and github/ owners.
# verified_allowed=false: a Marketplace verification badge is a
# publisher check, not a supply-chain one, and it is not ours to revoke.
# security/README.md is the record of the same list.
org_actions_allowed() { # usage: org_actions_allowed <owner> -> one pattern per line
  local owner="${1:?org_actions_allowed takes the canon owner}"
  printf '%s\n' "actions/*" "github/*" "${owner}/*" \
    "jdx/mise-action@*" "codecov/codecov-action@*" "ossf/scorecard-action@*"
}

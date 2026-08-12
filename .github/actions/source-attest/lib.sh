# Shared helpers for the source-attest scripts (#265). Sourced, not
# executed: chain.sh discovers what needs a link, emit.sh signs links,
# and both need the same three answers about a revision's note —
# whether it is a link, what its raw blob digest is, and whether it
# verifies against the published identity. One definition each, so the
# walker and the signer cannot disagree about what a link is.

# A link is a note that parses as a chain link (version + provenance +
# vsa) — the seed notes and unrelated annotations are not links.
is_link() {
  git notes show "${1}" 2> /dev/null \
    | jq -e '.version and .provenance.bundle and .vsa.bundle' > /dev/null 2>&1
}

# The chain digest is over the raw note blob — command substitution
# strips trailing newlines, and a digest two tools compute differently
# is no digest at all.
note_sha() {
  local obj
  obj=$(git notes list "${1}")
  git cat-file blob "${obj}" | sha256sum | cut -d" " -f1
}

# Verify the link at revision $1 against the pinned org identity — the
# same check a stranger runs with the published root of trust, nothing
# more. Dies with a named error rather than returning: extending a
# chain past a link that fails the contract is never a fallback.
verify_link() {
  local rev="${1}" note
  note=$(git notes show "${rev}")
  jq -r '.provenance.statement' <<< "${note}" | base64 -d > "${SA_WORK}/prev-statement.json"
  jq -c '.provenance.bundle' <<< "${note}" > "${SA_WORK}/prev-bundle.json"
  cosign verify-blob \
    --bundle "${SA_WORK}/prev-bundle.json" \
    --certificate-identity "${SA_IDENTITY}" \
    --certificate-oidc-issuer "${SA_ISSUER}" \
    "${SA_WORK}/prev-statement.json" > /dev/null 2>&1 || {
    echo "::error::link at ${rev} does not verify against ${SA_IDENTITY} — refusing to extend a chain that fails the published root of trust"
    exit 1
  }
  jq -e --arg c "${rev}" '.subject[0].digest.gitCommit == $c' \
    "${SA_WORK}/prev-statement.json" > /dev/null || {
    echo "::error::link at ${rev} attests a different revision than the commit it annotates"
    exit 1
  }
}

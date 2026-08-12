# The one VSA predicate assembler (#267). Both emitters — the source
# emitter (source-attest/emit.sh) and the release verifier
# (verify-release.yml, verdict mode) — render their
# slsa.dev/verification_summary/v1 predicate through this file, so the
# field set cannot drift between the org's two VSA kinds: a SHOULD
# added for one is structurally present in the other. The third
# conformance pass found exactly that asymmetry (slsaVersion,
# policy.digest missing from the source VSA only); this file is why it
# cannot recur.
#
# Callers pass every field as a jq argument and merge any track-specific
# extras (the build verdict's inputAttestations) onto the result. The
# assembler owns the invariants: exactly these keys, slsaVersion pinned
# to the spec the org builds against, policy always uri AND digest.
{
  verifier: {id: $verifier},
  timeVerified: $time,
  resourceUri: $resource,
  policy: {uri: $policyUri, digest: {gitCommit: $policySha}},
  verificationResult: $result,
  verifiedLevels: $levels,
  slsaVersion: "1.2"
}

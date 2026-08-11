# VEX statements — the org's dependency triage record

One OpenVEX document per decision, `*.openvex.json`, assembled with the
belt's pinned vexctl. This directory is the record `audit:blast-radius`
filters on: a finding with no statement here is *undecided* and fails the
Monday audit until a decision is written.

The contract (docs/dependency-track.md):

- **No `not_affected` without the blast-radius query behind it** — a
  signed wrong `not_affected` suppresses consumers' scanner findings on
  our word.
- Every `deny.toml` advisory `ignore` cites its statement here.
- Statements are signed through the org's one signer (the OpenVEX
  predicate type is allowlisted there) against the affected artifact
  digests, and ride the *next* release of each affected repo as a raw
  `.openvex.json` asset — published releases are immutable, so the fix
  path is roll-forward, like everything else.

Authoring a statement:

```bash
vexctl create --product "pkg:github/monumental-archive/<repo>@<tag>" \
  --vuln "RUSTSEC-XXXX-XXXX" --status not_affected \
  --justification vulnerable_code_not_in_execute_path \
  --file security/vex/RUSTSEC-XXXX-XXXX.openvex.json
```

Statuses: `not_affected` (with a justification), `affected` (with an
action statement — what a consumer should do), `fixed`, or
`under_investigation` (a real status: it makes "we know, we are looking"
a signed public fact rather than silence).

# Where these files come from

stele v0.24.0's own output, written by `stele assert signer-certificates
--policy slsa/assert-policy.json --org monumental-archive --json` against
`https://ctfe.sigstore.dev/2022` on 2026-11-17, from a workstation, with
this pull request's policy (#1339). Nothing in them was edited beyond
the whitespace biome formats JSON to (the documents decode identically);
a test row that needs a shape no live run produced edits a copy in code
and says which field.

| file | the run |
| --- | --- |
| `report-pass.json` | `--start-at 448221161` (150 behind the head) and a fresh checkpoint: 361 entries judged to the head, two requests, exit 0, PASS |
| `checkpoint-pass.json` | the checkpoint that run wrote: `next` is the tree size it verified |
| `report-budget.json` | a copy of that checkpoint with `next` set 25,000 behind, and `--budget 6s`: the budget ended before the first chunk was done, nothing judged, exit 4, CANNOT_JUDGE |
| `checkpoint-budget.json` | the checkpoint that run rewrote: the newer verified head, `next` unmoved |

The certificates the canon's reconciler was tested on
(`ct-443908396.json`, `ct-335460347.json`) and their forge captures
moved to stele with the reconciler (stele#387,
`internal/assert/testdata/signer-certificates/`), byte for byte.

# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
over the surface named in [MAINTENANCE.md](MAINTENANCE.md).

## [1.2.2](https://github.com/monumental-archive/.github/compare/v1.2.1...v1.2.2) - 2026-08-10

### Fixed

- widen the zero-cooldown rule to all first-party packages ([#142](https://github.com/monumental-archive/.github/pull/142))

## [1.2.1](https://github.com/monumental-archive/.github/compare/v1.2.0...v1.2.1) - 2026-08-10

### Fixed

- grant self-publish the orchestrator's full permission union ([#140](https://github.com/monumental-archive/.github/pull/140))

## [1.2.0](https://github.com/monumental-archive/.github/compare/v1.1.0...v1.2.0) - 2026-08-10

### Added

- publish the canon through the pipeline — the source-archive class ([#138](https://github.com/monumental-archive/.github/pull/138))

## [1.1.0](https://github.com/monumental-archive/.github/compare/v1.0.0...v1.1.0) - 2026-08-10

### Added

- pin every canon reference to v1.0.0 and alarm the bump loop ([#136](https://github.com/monumental-archive/.github/pull/136))

## [1.0.0](https://github.com/monumental-archive/.github/releases/tag/v1.0.0) - 2026-08-10

### Added

- the org rulesets, as reviewable JSON
- mise toolbelt skeleton with actionlint + shellcheck ([#5](https://github.com/monumental-archive/.github/pull/5))
- shellcheck at max — every optional check, no rc escape hatch ([#6](https://github.com/monumental-archive/.github/pull/6))
- the security configuration, as reviewable JSON ([#7](https://github.com/monumental-archive/.github/pull/7))
- the repo baseline — settings GitHub gives no org lever for ([#10](https://github.com/monumental-archive/.github/pull/10))
- org Renovate preset (default.json) + toolbelt rename ([#11](https://github.com/monumental-archive/.github/pull/11))
- the canonical task contract ([#12](https://github.com/monumental-archive/.github/pull/12))
- typos standup — spell-check in the belt ([#14](https://github.com/monumental-archive/.github/pull/14))
- lefthook standup — shared git hooks via remotes ([#15](https://github.com/monumental-archive/.github/pull/15))
- trivy standup — deterministic scanners in the gate ([#16](https://github.com/monumental-archive/.github/pull/16))
- zizmor standup — Actions security audit in the gate ([#17](https://github.com/monumental-archive/.github/pull/17))
- committed standup — conventional commits enforced ([#18](https://github.com/monumental-archive/.github/pull/18))
- taplo standup — TOML canon in the gate ([#19](https://github.com/monumental-archive/.github/pull/19))
- shfmt standup — shell formatting in the gate ([#20](https://github.com/monumental-archive/.github/pull/20))
- rumdl standup — markdown linting in the gate ([#21](https://github.com/monumental-archive/.github/pull/21))
- lychee standup — link auditing, deliberately outside the gate ([#22](https://github.com/monumental-archive/.github/pull/22))
- shared CI workflow — enforcement goes remote ([#23](https://github.com/monumental-archive/.github/pull/23))
- workflow template + new-repo scaffold ([#25](https://github.com/monumental-archive/.github/pull/25))
- org-default community health files ([#26](https://github.com/monumental-archive/.github/pull/26))
- shared release phase-1 workflow and canonical scripts ([#34](https://github.com/monumental-archive/.github/pull/34))
- wire the tag-mint app into the release-tag ruleset ([#35](https://github.com/monumental-archive/.github/pull/35))
- git-cliff standup — the phase-1 version decision joins the belt ([#37](https://github.com/monumental-archive/.github/pull/37))
- flip the v* creation lock to active ([#39](https://github.com/monumental-archive/.github/pull/39))
- narrow the id-token rule and enforce it; close three belt gaps ([#41](https://github.com/monumental-archive/.github/pull/41))
- require a DCO sign-off on every commit ([#42](https://github.com/monumental-archive/.github/pull/42))
- make immutable OIDC subject claims part of the repo baseline ([#44](https://github.com/monumental-archive/.github/pull/44))
- harden the toolbelt settings to the standard two repos already use ([#46](https://github.com/monumental-archive/.github/pull/46))
- exempt code blocks from the markdown line-length rule ([#48](https://github.com/monumental-archive/.github/pull/48))
- add the phase-2 publish orchestrator and the rust-crate class ([#57](https://github.com/monumental-archive/.github/pull/57))
- lint nested permissions, and add the phase-2 caller stub ([#59](https://github.com/monumental-archive/.github/pull/59))
- forbid bash 4+ syntax in belt tasks and release scripts ([#64](https://github.com/monumental-archive/.github/pull/64))
- detect packages left private under a public repository ([#70](https://github.com/monumental-archive/.github/pull/70))
- add the oci-image artifact class ([#65](https://github.com/monumental-archive/.github/pull/65))
- add the rust-binary and wasm-npm artifact classes ([#71](https://github.com/monumental-archive/.github/pull/71))
- add the badge layer — scorecard, dependency review, insights, continuity ([#75](https://github.com/monumental-archive/.github/pull/75))
- add the continuous archetype workflow ([#76](https://github.com/monumental-archive/.github/pull/76))
- watch the transparency log for the signer's identity ([#78](https://github.com/monumental-archive/.github/pull/78))
- prove releases rebuild to the published bytes ([#81](https://github.com/monumental-archive/.github/pull/81))
- add the pgrx-extension artifact class ([#84](https://github.com/monumental-archive/.github/pull/84))
- let the crate and binary classes exclude container-built members ([#85](https://github.com/monumental-archive/.github/pull/85))
- let the wasm-npm class publish under an npm scope ([#90](https://github.com/monumental-archive/.github/pull/90))
- add the runbook, the doi job, and the container-owned cnpg mirror ([#91](https://github.com/monumental-archive/.github/pull/91))
- make canon score-ready — coverage ratchet, badge feed, scaffolds ([#93](https://github.com/monumental-archive/.github/pull/93))
- let the bump rename pending upgrade scripts ([#99](https://github.com/monumental-archive/.github/pull/99))
- add lint:template-pins to the belt ([#113](https://github.com/monumental-archive/.github/pull/113))
- sign the artifact VSA — the verdict beside the evidence ([#115](https://github.com/monumental-archive/.github/pull/115))
- add source-track policies, org-owned ([#128](https://github.com/monumental-archive/.github/pull/128))
- share one source policy across the org ([#129](https://github.com/monumental-archive/.github/pull/129))
- move the policy clock past genesis ([#130](https://github.com/monumental-archive/.github/pull/130))
- version the canon and generalise the phase-1 version source ([#134](https://github.com/monumental-archive/.github/pull/134))

### Documentation

- what this repo is, and the rule that keeps L3 intact
- anything shared lives here, renovate-config included
- the UI applies what the API refuses — record the applied config ([#9](https://github.com/monumental-archive/.github/pull/9))
- rewrite CLAUDE.md, README, org profile; fix drift-audit token ([#29](https://github.com/monumental-archive/.github/pull/29))
- record the real AUDIT_TOKEN permission requirement ([#31](https://github.com/monumental-archive/.github/pull/31))
- repo migration playbook ([#32](https://github.com/monumental-archive/.github/pull/32))
- org release canon; stage v* tag-creation ruleset ([#33](https://github.com/monumental-archive/.github/pull/33))
- record the tag lock as proven, and why evaluate mode proves nothing ([#38](https://github.com/monumental-archive/.github/pull/38))
- SLSA and attestation reference from primary sources ([#40](https://github.com/monumental-archive/.github/pull/40))
- rename the lab to release-lab; record the OIDC claim measurement ([#43](https://github.com/monumental-archive/.github/pull/43))
- point the canon at signer, not trusted-builder ([#49](https://github.com/monumental-archive/.github/pull/49))
- correct the phase-2 canon on where publish steps may live ([#51](https://github.com/monumental-archive/.github/pull/51))
- settle experiment 1 and the subject-claim reading ([#52](https://github.com/monumental-archive/.github/pull/52))
- record that environment secrets do not reach a reusable ([#53](https://github.com/monumental-archive/.github/pull/53))
- retract the runner-hardening agent from the canon ([#54](https://github.com/monumental-archive/.github/pull/54))
- correct the fuzzing score route and the ruleset blocker ([#56](https://github.com/monumental-archive/.github/pull/56))
- canonise the image build rules and the scanning obligation ([#60](https://github.com/monumental-archive/.github/pull/60))
- settle the multi-arch attestation subject ([#63](https://github.com/monumental-archive/.github/pull/63))
- treat shared-workflow permissions as a public contract ([#68](https://github.com/monumental-archive/.github/pull/68))
- record the release pass as complete ([#109](https://github.com/monumental-archive/.github/pull/109))
- bring every doc in line with what is now true ([#112](https://github.com/monumental-archive/.github/pull/112))

### Fixed

- restore imperative commit subjects ([#24](https://github.com/monumental-archive/.github/pull/24))
- REST-only repo listing in the drift check ([#30](https://github.com/monumental-archive/.github/pull/30))
- canon checkouts must ride job_workflow_sha, not workflow_sha ([#36](https://github.com/monumental-archive/.github/pull/36))
- capability-boundary must not match id-token in comments ([#45](https://github.com/monumental-archive/.github/pull/45))
- stop narrowing lockfile_platforms; move locked back to CI ([#47](https://github.com/monumental-archive/.github/pull/47))
- quote the DCO hook message so the shared config parses ([#50](https://github.com/monumental-archive/.github/pull/50))
- carry the full commit-type list into the scaffold ([#55](https://github.com/monumental-archive/.github/pull/55))
- derive the release sign-off without the user endpoint ([#58](https://github.com/monumental-archive/.github/pull/58))
- correct the branch-protection scoring and record org application ([#61](https://github.com/monumental-archive/.github/pull/61))
- keep exactly one release branch and one release PR ([#62](https://github.com/monumental-archive/.github/pull/62))
- authenticate git-cliff so releases stop failing on rate limits ([#66](https://github.com/monumental-archive/.github/pull/66))
- make every caller state its grant, and lint for it ([#67](https://github.com/monumental-archive/.github/pull/67))
- identify the app by client id, and stop callers carrying it ([#69](https://github.com/monumental-archive/.github/pull/69))
- build legs install the caller's toolchain, never the belt ([#73](https://github.com/monumental-archive/.github/pull/73))
- survive download-artifact's single-bundle flattening ([#74](https://github.com/monumental-archive/.github/pull/74))
- run the rekor monitor ourselves, github-owned actions only ([#79](https://github.com/monumental-archive/.github/pull/79))
- keep trivy out of the toolbelt checkout ([#86](https://github.com/monumental-archive/.github/pull/86))
- require an upgrade path only from a release that shipped the extension ([#87](https://github.com/monumental-archive/.github/pull/87))
- run the pgrx build and tests as the unprivileged postgres user ([#89](https://github.com/monumental-archive/.github/pull/89))
- publish the tarball as a path, execute upgrades, teach repro-check classes ([#95](https://github.com/monumental-archive/.github/pull/95))
- refuse a wasm package whose repository does not name the caller ([#96](https://github.com/monumental-archive/.github/pull/96))
- gate the doi on attach explicitly and ship the upgrade scripts ([#97](https://github.com/monumental-archive/.github/pull/97))
- authenticate git-cliff in the tag job ([#100](https://github.com/monumental-archive/.github/pull/100))
- commit the renamed upgrade script's old path as a deletion ([#101](https://github.com/monumental-archive/.github/pull/101))
- probe for CITATION.cff by gh's exit status ([#103](https://github.com/monumental-archive/.github/pull/103))
- adapt the baseline to org-enforced signoff and entry-only environments ([#104](https://github.com/monumental-archive/.github/pull/104))
- let repro-check exclude workspace members like the publish does ([#105](https://github.com/monumental-archive/.github/pull/105))

### Miscellaneous

- move the stub pins to the four-class orchestrator ([#72](https://github.com/monumental-archive/.github/pull/72))
- move the stub pins to the continuous-archetype orchestrator ([#77](https://github.com/monumental-archive/.github/pull/77))
- remove the deprecated app-id input ([#80](https://github.com/monumental-archive/.github/pull/80))
- complete the badge block — registry shields and passive observers ([#94](https://github.com/monumental-archive/.github/pull/94))
- add the codeowners scaffold ([#98](https://github.com/monumental-archive/.github/pull/98))
- chore/publish env baseline ([#102](https://github.com/monumental-archive/.github/pull/102))
- move the workflow-template pins to today's canon ([#110](https://github.com/monumental-archive/.github/pull/110))
- bump the workflow-template pins to current canon ([#114](https://github.com/monumental-archive/.github/pull/114))
- move the workflow-template pins to today's canon ([#116](https://github.com/monumental-archive/.github/pull/116))
- replace the ruleset JSON mirror with docs/rulesets.md ([#127](https://github.com/monumental-archive/.github/pull/127))
- park the source track until upstream is org-ready ([#131](https://github.com/monumental-archive/.github/pull/131))

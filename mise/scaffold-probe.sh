#!/usr/bin/env bash
# lint:scaffold-assembly's probe files: what the probe adds to a scratch
# tree assembled from scaffold/, which is NOT the scaffold. Usage:
# scaffold-probe.sh <tree>, from the canon's root (it copies the canon's
# LICENSE and workflow template). Moved out of the task body when #1910
# grew it past its line debt (#1544).
#
# The smallest repository the stubs describe. Kept minimal and listed in
# the task's report, because anything added here can draw findings of
# its own — a hand-written package.json did exactly that while the task
# was being built.
#   LICENSE      — lint:licence asks only that the choice was MADE, and
#                  the choice is the one thing a stub cannot make (#214);
#                  the canon's own 0BSD text stands in for one.
#   package.json — `"type": "module"`, without which nodenext reads a
#                  .ts file as CommonJS and verbatimModuleSyntax refuses
#                  its export (TS1287). No dependencies, so the empty
#                  node_modules below is what "installed" means here —
#                  lint:types otherwise refuses a tracked package.json
#                  whose dependencies are absent, which is that guard
#                  working rather than anything about scaffold/.
#   src/<ext>/   — one extensionless relative import per TypeScript
#                  source extension: #758's shape, and the one line that
#                  makes the fixer/checker pair speak. TWO extensions,
#                  because #868 was `.js` being right for one of them and
#                  wrong for the other — a probe that varies neither
#                  proves neither. Each pair gets its own directory: with
#                  helper.ts and helper.mts side by side, `./helper` is
#                  ambiguous and tsc names the wrong counterpart.
#   stryker.config.json, .coverage-floor
#                — a repository that ships TypeScript tracks its
#                  mutation file and a floor (lint:test-adoption, #1910),
#                  and src/ above makes the probe one. What they hold is
#                  the legs' business and no lint:* or fix:* reads it,
#                  so each is the smallest file there is.
#   .github/workflows/ci.yml
#                — workflow-templates/ci.yml verbatim: the gate stub a new
#                  repository copies, so the probe gates what one gets.
set -euo pipefail

tree="${1:?usage: scaffold-probe.sh <tree>}"
mkdir -p "${tree}/node_modules" "${tree}/.github/workflows"
cp LICENSE "${tree}/LICENSE"
cp workflow-templates/ci.yml "${tree}/.github/workflows/ci.yml"
printf '{\n  "name": "scaffold-probe",\n  "private": true,\n  "type": "module"\n}\n' \
  > "${tree}/package.json"
printf '{}\n' > "${tree}/stryker.config.json"
printf '90\n' > "${tree}/.coverage-floor"
for ext in ts mts; do
  mkdir -p "${tree}/src/${ext}"
  printf 'export const helped = (): string => "probe";\n' > "${tree}/src/${ext}/helper.${ext}"
  printf 'import { helped } from "./helper";\n\nexport const main = (): string => helped();\n' \
    > "${tree}/src/${ext}/index.${ext}"
done

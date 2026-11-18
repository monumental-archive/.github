#!/usr/bin/env bash
# Measure statement coverage, one line per language leg. Org canon — the
# shared half of #652.
#
# `coverage:check` enforces this number, the release path derives the
# floor from it, and `coverage:report` takes the profile written here as
# the artifact it uploads — so there is ONE measurement with one set of
# flags rather than several that agree until they do not. That is not a
# hypothetical shape: a second hardcoded `--workspace` beside
# coverage:check's drifted from it the instant a repository excluded a
# member, and reddened main (#316); and a second `go test -coverprofile`
# without `-coverpkg` handed Codecov 94.9% while the gate held the same
# tree to a floor derived from 95.4% (#707, measured on stele).
#
# Prints `<leg> <percent>` on stdout, one line per language the tree
# tracks and nothing else; every tool's own output goes to stderr, so a
# caller may read stdout as data. A tree with no measurable language
# prints nothing and exits 0 — measuring is not adoption.
#
# Measurable, per leg: `go.mod` (go), `Cargo.toml` (rust), and tracked
# `*.py` plus a `test` task (python, #1331). Python carries no manifest
# that says "there is a suite", so the repository's own `test` task is
# that statement — a tree that tracks a helper script and tests nothing
# has no corpus to hold a floor over. A tracked root `package.json`
# whose lockfile pins vitest measures the typescript leg (#1357); any
# other runner is named and skipped, never guessed at. The `probe`
# selector prints the
# leg names that apply, one per line, without measuring anything: it is
# how the `coverage:*` tasks and the release path decide applicability,
# so "which languages does this tree measure" has one answer here rather
# than a manifest list copied into each guard.
#
# `rust-branches` (#1359) is a leg of its own and never part of `all`:
# branch instrumentation needs a nightly, and the gate runs none. It
# measures on the dated nightly the repository declares as
# FUZZ_TOOLCHAIN, over the same workspace and exclusions as the `rust`
# leg — one function below, not a copy — and prints
# `rust-branches <percent>` for the Monday audit and `coverage:adopt`.
#
# Invoked directly, never through a nested `mise run`: a nested mise
# inside a task races the parallel lint fan-out and fails to resolve its
# own lockfile, which made `mise run ci` unrunnable locally (#82).
set -euo pipefail

# The caller's name, so a remedy printed here names the task the session
# actually ran.
label="${1:-coverage:measure}"

# Which language legs to run. `coverage:report` needs the GO profile and
# renders its own Rust lcov — a format this script does not produce — so
# it asks for `go` rather than paying for a second Rust test run (#707);
# it asks for `python` to have the Cobertura report written beside the
# number, and `typescript` for the lcov beside its number. Default `all`: every caller that reads the percentages wants
# them all, because the floor is a minimum every leg must hold.
legs="${2:-all}"
case "${legs}" in
  all | go | rust | rust-branches | python | typescript | probe) ;;
  *)
    echo "${label}: unknown leg selector '${legs}' — expected all, go, rust, rust-branches, python, typescript or probe" >&2
    exit 1
    ;;
esac

# The repository's own `test` task, as mise resolved it, or nothing. Read
# with `mise tasks info --json`, the read-only query other belt tasks
# already make from inside a task — not a nested `mise run`, which is
# the #82 race. Guarded, not left to errexit: a mise that cannot answer
# means no test task, and the leg says so rather than dying mute.
python_test=""
tracked_py=$(git ls-files -- '*.py' 2> /dev/null) || tracked_py=""
if [[ -n ${tracked_py} ]]; then
  python_test=$(mise tasks info test --json 2> /dev/null) || python_test=""
fi

# The JavaScript runner and its coverage provider, read from the root
# package.json and its lockfile (#1357): `vitest <provider>`,
# `unsupported <runner>`, or nothing.
tracked_js=$(git ls-files -- package.json pnpm-lock.yaml package-lock.json yarn.lock bun.lock 2> /dev/null) || tracked_js=""
ts_runner=""
if [[ -n ${tracked_js} ]]; then
  # shellcheck disable=SC2086  # one argument per tracked path; none has a space
  ts_runner=$(python3 "$(dirname "${BASH_SOURCE[0]}")/coverage-js-runner.py" ${tracked_js}) || exit 1
fi

if [[ ${legs} == probe ]]; then
  [[ -f go.mod ]] && echo go
  [[ -f Cargo.toml ]] && echo rust
  [[ -n ${python_test} ]] && echo python
  case "${ts_runner}" in
    vitest\ *) echo typescript ;;
    unsupported\ *)
      echo "${label}: typescript: ${ts_runner#unsupported } is not a runner the leg supports (vitest), skipped" >&2
      ;;
    *) ;;
  esac
  exit 0
fi

# EVERY tracked language measures its own leg, because the floor is a
# minimum and applying it per language is the only reading that means
# anything: a mixed repository that measured Rust alone once reported
# "floor held" over a language nothing had measured (#392).
if [[ -f go.mod && ${legs} =~ ^(all|go)$ ]]; then
  if ! command -v go > /dev/null; then
    echo "${label}: go.mod present but go missing — pin go in mise.toml" >&2
    exit 1
  fi
  # -coverpkg=./... (#652). Without it a statement exercised only by a
  # NEIGHBOURING package's tests scores uncovered, so the gate enforces a
  # number below what the tests prove — measured at 0.5 points and 51
  # statements on stele, which is a floor's worth of understatement in a
  # repository whose floor is the point.
  go test ./... -coverpkg=./... -coverprofile=coverage.txt -covermode=atomic >&2
  go_pct=$(go tool cover -func=coverage.txt | awk '/^total:/ { sub(/%/, "", $3); print $3 }')
  if [[ -z ${go_pct} ]]; then
    echo "${label}: could not read a total from the Go profile — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "go ${go_pct}"
fi

# The Rust measurement surface, shared by the `rust` and `rust-branches`
# legs (#1359) so the line figure and the branch figure are taken over
# one workspace: the llvm-tools assertion for the toolchain the leg
# runs on, the pgrx provisioning, and the COVERAGE_EXCLUDE list. Sets
# `excludes`. The toolchain is empty for the repository's stable pin.
rust_surface() {
  local toolchain="$1" member
  # No task-time rustup, by rule: a component installed mid-run races
  # every other task driving the toolchain (measured twice on the lab's
  # v0.18.0 release PR, #117). llvm-tools is a declared build input —
  # pinned in the repository's mise.toml, installed before any task runs
  # — and this asserts it with a remedy, never installs it.
  if ! rustup component list --installed ${toolchain:+--toolchain "${toolchain}"} 2> /dev/null | grep -q "^llvm-tools"; then
    if [[ -z ${toolchain} ]]; then
      echo "${label}: llvm-tools component missing — declare it with the" >&2
      echo '  rust pin in mise.toml: rust = { version = "1.xx.x", components = "llvm-tools" }' >&2
    else
      echo "${label}: llvm-tools component missing on ${toolchain} — declare it with" >&2
      echo "  the nightly's pin in mise.toml:" >&2
      echo "    { version = \"${toolchain}\", components = \"rust-src,llvm-tools\" }" >&2
    fi
    echo "  (installed with the toolchain, before tasks run — never mid-run)" >&2
    exit 1
  fi
  # A pgrx extension in the workspace is compiled by the measurement like
  # any other member, so it is given its Postgres first (#917) — and a
  # COVERAGE_EXCLUDE that names one is refused there, not honoured.
  # shellcheck source=mise/pgrx-env.sh
  . "${ORG_BELT_DIR:?unset — no belt (CI: MISE_GLOBAL_CONFIG_FILE; local: conf.d symlink)}/pgrx-env.sh"
  org_pgrx_provision "${label}" "${COVERAGE_EXCLUDE:-}" >&2
  # COVERAGE_EXCLUDE ([env] in the repository's mise.toml): workspace
  # members whose tests cannot run on the gate runner. Never a pgrx
  # extension — that was its first use (the lab's lab-pg, #316) and is
  # now refused above.
  excludes=""
  for member in ${COVERAGE_EXCLUDE:-}; do excludes="${excludes} --exclude ${member}"; done
}

if [[ -f Cargo.toml && ${legs} =~ ^(all|rust)$ ]]; then
  rust_surface ""
  # `--json --summary-only` rather than `--fail-under-lines`: the release
  # path needs the NUMBER, and a leg that only ever answers pass/fail
  # cannot derive a floor. The comparison is unchanged — measured against
  # a fixture, --fail-under-lines 61.5 passes and 61.6 fails at
  # `.data[0].totals.lines.percent` 61.53846153846154, so comparing the
  # extracted percentage is the same test with the number visible.
  #
  # shellcheck disable=SC2086  # ${excludes} is a built '--exclude <member>' list,
  # which cargo must receive as separate arguments
  report=$(cargo llvm-cov --workspace --locked ${excludes} --json --summary-only)
  rust_pct=$(jq -r '.data[0].totals.lines.percent // empty' <<< "${report}")
  if [[ -z ${rust_pct} ]]; then
    echo "${label}: could not read a total from the Rust report — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "rust ${rust_pct}"
fi

if [[ -f Cargo.toml && ${legs} == rust-branches ]]; then
  # The nightly is DECLARED by the repository, never discovered — the
  # audit:fuzz rule, and the same pin: no second nightly for the belt to
  # keep in step. A leg that used whatever nightly a machine happened to
  # have would vary its figure by machine.
  if [[ -z ${FUZZ_TOOLCHAIN:-} ]]; then
    echo "${label}: FUZZ_TOOLCHAIN is not declared — Rust branch coverage needs a nightly" >&2
    echo "  (cargo llvm-cov --branch passes -Z flags stable refuses). Declare the dated" >&2
    echo "  nightly in this repo's mise.toml, both toolchains under one rust pin:" >&2
    echo '    rust = ["1.xx.x", { version = "nightly-YYYY-MM-DD", components = "rust-src,llvm-tools" }]' >&2
    echo '    [env] FUZZ_TOOLCHAIN = "nightly-YYYY-MM-DD"' >&2
    exit 1
  fi
  if ! rustup toolchain list 2> /dev/null | grep -q "^${FUZZ_TOOLCHAIN}"; then
    echo "${label}: FUZZ_TOOLCHAIN='${FUZZ_TOOLCHAIN}' is declared but not installed" >&2
    echo "  it rides the rust pin in mise.toml so it installs before tasks run, never mid-run" >&2
    exit 1
  fi
  rust_surface "${FUZZ_TOOLCHAIN}"
  # RUSTUP_TOOLCHAIN as a per-command prefix: mise exports the stable
  # pin's value into the task env, and only a command-scoped assignment
  # beats it (the lint:msrv shape).
  #
  # shellcheck disable=SC2086  # ${excludes} is a built '--exclude <member>' list,
  # which cargo must receive as separate arguments
  report=$(RUSTUP_TOOLCHAIN="${FUZZ_TOOLCHAIN}" cargo llvm-cov --workspace --locked ${excludes} --branch --json --summary-only)
  # A count of 0 is refused, never floored: an uninstrumented run reports
  # `branches` as count 0, percent 0 (measured on stable 1.97.1 without
  # --branch), so a zero count is the shape of a run that measured no
  # branches rather than one that covered none.
  br_count=$(jq -r '.data[0].totals.branches.count // empty' <<< "${report}")
  br_pct=$(jq -r '.data[0].totals.branches.percent // empty' <<< "${report}")
  if [[ -z ${br_count} || -z ${br_pct} || ${br_count} == 0 ]]; then
    echo "${label}: the Rust report counted no branches (count '${br_count:-absent}') — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "rust-branches ${br_pct}"
fi

if [[ -n ${python_test} && ${legs} =~ ^(all|python)$ ]]; then
  # coverage.py, measuring the repository's own `test` task (#1331). The
  # discovery roots stay where the repository wrote them; the belt never
  # restates them, so the suite measured is the suite the gate runs.
  if ! command -v coverage > /dev/null; then
    echo "${label}: tracked Python and a test task, but coverage is missing —" >&2
    echo "  the belt pins pipx:coverage; run: mise install" >&2
    exit 1
  fi
  # The tool venv's interpreter must BE the one the suite runs on: the
  # tracer is a compiled extension, and the child processes below import
  # it from the venv into the repository's `python3`. The belt's UV_*
  # env makes uv build the venv on the `python` pin; this asserts it,
  # because a venv uv built on an interpreter of its own (the hidden-
  # Python trap the UV_* env exists for) would measure nothing in every
  # child, silently.
  cov_sys=$(coverage debug sys 2> /dev/null) || cov_sys=""
  cov_python=$(awk '$1 == "executable:" { print $2 }' <<< "${cov_sys}")
  cov_module=$(awk '$1 == "coverage_module:" { print $2 }' <<< "${cov_sys}")
  cov_version=$("${cov_python:-false}" -c 'import sys; print(sys.version)' 2> /dev/null) || cov_version=""
  run_version=$(python3 -c 'import sys; print(sys.version)' 2> /dev/null) || run_version=""
  if [[ -z ${cov_module} || -z ${cov_version} || ${cov_version} != "${run_version}" ]]; then
    echo "${label}: coverage runs on '${cov_version:-no interpreter}'" >&2
    echo "  but the suite's python3 is '${run_version:-missing}' — the tool venv" >&2
    echo "  must be built on the belt's python pin (reinstall: mise install -f pipx:coverage)" >&2
    exit 1
  fi
  # The directory holding the `coverage` package: coverage/__init__.py,
  # two levels up.
  cov_site=$(dirname "$(dirname "${cov_module}")")

  # Every leg command is read before anything runs: a test task this
  # leg cannot run is refused by name, never measured as empty.
  test_dir=$(jq -r '.dir // empty' <<< "${python_test}")
  # base64 per entry: a run script is multi-line, and one record per
  # line is what a bash read loop can split without guessing.
  test_runs=$(jq -r '(.run // [])[] | @base64' <<< "${python_test}")
  if [[ -z ${test_runs} ]]; then
    echo "${label}: the test task has no \`run\` script — the python leg measures" >&2
    echo "  the repository's own test command and cannot measure a task it cannot run" >&2
    exit 1
  fi

  work=$(mktemp -d)
  # Completion-guarded (#1233): on bash 3.2 a script with an EXIT trap
  # that dies on an unset variable would otherwise exit 0.
  rc=0
  trap 'rc=$?; rm -rf "${work}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
  mkdir "${work}/hook" "${work}/data"
  # Child processes are measured, not only the runner (#1331): suites
  # exec their helper as `python3 helper.py`, and a leg that measured
  # the unittest process alone reports the in-process half of the code.
  # `patch = subprocess` cannot reach them — its .pth lives in the tool
  # venv, which the repository's python3 never reads — so every python3
  # the task starts loads this sitecustomize, which hands start-up to
  # coverage's own process_startup under COVERAGE_PROCESS_START.
  # Appended to sys.path, never prepended: the suite's imports resolve
  # exactly as they do unmeasured.
  site_literal=$(python3 -c 'import sys; print(repr(sys.argv[1]))' "${cov_site}")
  printf 'import sys\nsys.path.append(%s)\nimport coverage\ncoverage.process_startup()\n' \
    "${site_literal}" > "${work}/hook/sitecustomize.py"
  # Branches from the start (#1336 property 2). Test files are omitted:
  # a suite executes itself completely by construction, and counting it
  # would raise the number without one more line of the code under test
  # being run. relative_files, so the report's paths are the repository's
  # own and Codecov maps them without a fix-up.
  cat > "${work}/rc" << EOF
[run]
branch = true
parallel = true
source = ${PWD}
data_file = ${work}/data/.coverage
relative_files = true
omit = */test_*.py
disable_warnings = no-data-collected, module-not-measured

[report]
precision = 2
EOF

  while read -r encoded; do
    script=$(base64 --decode <<< "${encoded}")
    if ! (
      cd "${test_dir:-${PWD}}"
      COVERAGE_PROCESS_START="${work}/rc" \
        PYTHONPATH="${work}/hook${PYTHONPATH:+:${PYTHONPATH}}" \
        bash -euo pipefail -c "${script}"
    ) >&2; then
      echo "${label}: the test task failed under measurement — a suite that" >&2
      echo "  does not pass cannot measure a floor" >&2
      exit 1
    fi
  done <<< "${test_runs}"

  if ! coverage combine --rcfile="${work}/rc" --quiet >&2; then
    echo "${label}: the test task ran no measured Python — a measurement" >&2
    echo "  that reads nothing must not become a floor" >&2
    exit 1
  fi
  coverage json --rcfile="${work}/rc" --quiet -o "${work}/coverage.json" >&2
  py_pct=$(jq -r '.totals.percent_statements_covered // empty' "${work}/coverage.json")
  br_pct=$(jq -r '.totals.percent_branches_covered // empty' "${work}/coverage.json")
  if [[ -z ${py_pct} || -z ${br_pct} ]]; then
    echo "${label}: could not read a total from the Python report — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  # The report Codecov receives is rendered from this measurement's own
  # data, so the badge and the floor read one run (#707's law).
  if [[ ${legs} == python ]]; then
    coverage xml --rcfile="${work}/rc" --quiet -o coverage.xml >&2
  fi
  echo "python ${py_pct}"
  echo "python-branches ${br_pct}"
fi

if [[ ${ts_runner} == vitest\ * && ${legs} =~ ^(all|typescript)$ ]]; then
  # The repository's own runner under its own coverage provider (#1357):
  # vitest from the repository's node_modules, the provider its lockfile
  # pins. The belt pins neither, and adds no second instrumentation.
  provider=${ts_runner#vitest }
  case "${provider}" in
    none)
      echo "${label}: vitest runs the suite but the lockfile pins no coverage provider —" >&2
      echo "  add @vitest/coverage-v8 to devDependencies at vitest's own version" >&2
      echo "  (the belt pins no provider: the repository's lockfile owns it, #1357)" >&2
      exit 1
      ;;
    both)
      echo "${label}: the lockfile pins both @vitest/coverage-v8 and @vitest/coverage-istanbul —" >&2
      echo "  the leg does not choose between them; keep the one the suite uses" >&2
      exit 1
      ;;
    *) ;;
  esac
  vitest=node_modules/.bin/vitest
  if [[ ! -x ${vitest} ]]; then
    echo "${label}: vitest is in the lockfile but not installed — install from the" >&2
    echo "  tracked lockfile before measuring" >&2
    exit 1
  fi
  ts_work=$(mktemp -d)
  rc=0
  # Completion-guarded (#1233), and clearing the python leg's directory
  # too, since this trap replaces that one.
  trap 'rc=$?; rm -rf "${work:-}" "${ts_work}"; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
  # The repository's own vitest config decides what is in the suite and
  # in the report (`coverage.include`: vitest counts only files a test
  # loaded unless it is set). The leg sets only what makes the number
  # readable here: the provider, the two reporters, and a directory of
  # its own, so nothing lands in the tree unasked.
  if ! "${vitest}" run \
    --coverage.enabled=true \
    --coverage.provider="${provider}" \
    --coverage.reporter=json-summary \
    --coverage.reporter=lcov \
    --coverage.reportsDirectory="${ts_work}" >&2; then
    echo "${label}: the vitest suite failed under measurement — a suite that" >&2
    echo "  does not pass cannot measure a floor" >&2
    exit 1
  fi
  ts_pct=$(jq -r '.total.lines.pct // empty' "${ts_work}/coverage-summary.json" 2> /dev/null) || ts_pct=""
  ts_br=$(jq -r '.total.branches.pct // empty' "${ts_work}/coverage-summary.json" 2> /dev/null) || ts_br=""
  # A total over nothing reads "Unknown", not a number.
  if [[ ! ${ts_pct} =~ ^[0-9.]+$ || ! ${ts_br} =~ ^[0-9.]+$ ]]; then
    echo "${label}: could not read a total from the vitest report — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  # The upload is this run's own lcov (#707's law). A name of its own:
  # lcov.info is the Rust leg's.
  if [[ ${legs} == typescript ]]; then
    cp "${ts_work}/lcov.info" lcov-typescript.info
  fi
  echo "typescript ${ts_pct}"
  echo "typescript-branches ${ts_br}"
fi
org_done=1

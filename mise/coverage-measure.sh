#!/usr/bin/env bash
# Measure statement coverage, one line per language leg. Org canon — the
# shared half of #652.
#
# `coverage:check` enforces this number, the release path derives the
# floor from it, and the badge job uploads the reports written here by
# the gate's own run (#1528) — so there is ONE measurement with one set
# of flags rather than several that agree until they do not. That is
# not a hypothetical shape: a second hardcoded `--workspace` beside
# coverage:check's drifted from it the instant a repository excluded
# a member, and reddened main (#316); and a second `go test -coverprofile`
# without `-coverpkg` handed Codecov 94.9% while the gate held the same
# tree to a floor derived from 95.4% (#707, measured on stele). The
# badge job's own re-measurement, with a smaller toolset than the
# gate's, hung on main for a day (#1528).
#
# Every leg that runs writes the report Codecov parses beside its
# number: coverage.txt (go), lcov.info (rust), coverage.xml (python),
# lcov-typescript.info (typescript). Never in the working tree (#1628):
# a `.gitignore` is the repository's file, so a report left there is
# one `git add -A` from a commit. They go into ORG_COVERAGE_REPORTS,
# which the gate job names so its artifact step can keep them; unset,
# into a scratch directory outside the repository, removed on exit.
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
# FUZZ_TOOLCHAIN, with a build and a command of its own: an instrumented
# nightly build cannot share the gate's stable run (#1653). It reads the
# same surface as the `rust` leg — the llvm-tools assertion and the pgrx
# extensions held out, one function below, not a copy — and prints
# `rust-branches <percent>` for the Monday audit and `coverage:adopt`.
#
# The python and plpgsql legs run no suite (#1667). The repository's own
# `test` task runs once, through mise, and its test command runs through
# `"${ORG_BELT_DIR}/measured"`, which measures where the suite runs and
# writes into the run's directory, ORG_COVERAGE_RUN (coverage-run.sh).
# These legs read that directory after `test` has ended, which the
# `coverage:*` tasks order by depending on `test`.
#
# `plpgsql` (#1547) measures PL/pgSQL through plpgsql_check, in a
# database only the repository's suite has. It makes no search of the
# tree (#1666): `measured` sets ORG_PLPGSQL_COVERAGE, naming a report
# in the run's directory that the suite's teardown writes, and a report
# written means the leg reported: `plpgsql <percent>` and
# `plpgsql-branches <percent>`. Which legs a floor holds is the floor's
# own record (`legs:`), and coverage:check reds a recorded leg that
# reported nothing. The reading and the refusals are
# plpgsql_coverage.py's.
#
# `pgrx` (#1656) is not part of `all` either. A pgrx extension's code
# runs inside the Postgres backend, so only its own suite can cover it,
# and that suite is `test:pgrx`, which measures each major's run itself
# and holds it to the floor in the gate. `all` therefore leaves the
# extension crates out of the rust leg (their lines would read as zero)
# and names the `pgrx-pgNN` legs `test:pgrx` owns; the `pgrx` selector
# runs pgrx-test.sh's `measure` mode for every declared major and
# prints its `pgrx-pgNN <percent>` lines, for `coverage:adopt` and the
# release path, which derive the floor.
#
# `fuzz` (#1896) is a leg per fuzz target, in `all`: the line coverage
# of replaying the target's committed corpus once, a function of the
# tree. It prints `fuzz-<name> <percent>` per target, each a floor line
# of its own (coverage_floor.py), and writes no upload report. Rust:
# every [[bin]] of fuzz/Cargo.toml, its tracked corpus copied out of the
# tree by org_fuzz_corpus_copy (fuzz-corpus-lib.sh, the copy
# lint:fuzz-build replays) and replayed by `cargo fuzz coverage -s none`
# into a build directory of its compiler (#1877); the figure is
# llvm-cov's over the tracked sources outside fuzz/fuzz_targets/. Go:
# every `func Fuzz…(… *testing.F)` in a tracked _test.go of the root
# module, `go test -run '^<Name>$'` (which replays exactly the seed
# corpus) with -coverpkg naming the module's packages its package
# imports. Two targets deriving one leg name are red, naming both.
#
# Invoked directly, never through a nested `mise run`: a nested mise
# inside a task races the parallel lint fan-out and fails to resolve its
# own lockfile, which made `mise run ci` unrunnable locally (#82).
set -euo pipefail

: "${ORG_PYTHON:?unset — the belt Python (CI: the canon action; local: the belt [env])}"

# The caller's name, so a remedy printed here names the task the session
# actually ran.
label="${1:-coverage:measure}"
belt_dir=$(dirname "${BASH_SOURCE[0]}")

# Which language legs to run. Default `all`: every caller that reads
# the percentages wants them all, because the floor is a minimum every
# leg must hold. A single leg's selector measures that leg alone.
# `reports` measures every leg and then names the reports it wrote,
# below.
legs="${2:-all}"
case "${legs}" in
  all | go | rust | rust-branches | pgrx | pgrx-majors | python | typescript | plpgsql | fuzz | probe | reports) ;;
  *)
    echo "${label}: unknown leg selector '${legs}' — expected all, go, rust," \
      "rust-branches, pgrx, pgrx-majors, python, typescript, plpgsql, fuzz, probe or reports" >&2
    exit 1
    ;;
esac

# The upload reports, one per leg, in the order the badge job lists them
# (#1528). `reports` runs an `all` measurement into the same directory,
# prints its leg lines, then `<label>: upload <paths>` last, the line
# the gate log carries, naming the files that run wrote. It refuses a
# run that measured nothing, and one that wrote no report: a leg that
# ran and left none is refused where it happened (#1386). One process
# owns the directory, so a scratch one outlives the measurement that
# fills it and the listing that reads it, and no longer.
#
# Every leg is in exactly one of the two lists below. A leg in
# `formatless` has no format Codecov reads (#1547: plpgsql_check numbers
# lines within a function, never within a file), so it is named and
# never counted as a writer: where every leg measured is formatless the
# step says so and passes, and the badge job finds no artifact and
# skips, as it does for a tree without a floor. Every `fuzz-*` leg is
# formatless too (#1896): its figure is a corpus's depth, which no
# badge reports.
reports=(lcov.info lcov-typescript.info coverage.txt coverage.xml)
formatless=(plpgsql plpgsql-branches)
# The leg lines a `reports` run printed, as a file in the same
# directory: what the gate keeps for the release job (#1622).
figures=coverage-figures.txt
if [[ ${legs} != probe && ${legs} != pgrx-majors ]]; then
  # Completion-guarded (#1233): on bash 3.2 a script with an EXIT trap
  # that dies on an unset variable would otherwise exit 0. One trap for
  # every directory the run makes: the legs below only set theirs.
  cleanup() { rm -rf "${ts_work:-}" "${scratch:-}" "${fuzz_work:-}" "${fuzz_made:-}"; }
  rc=0
  trap 'rc=$?; cleanup; [ -n "${org_done:-}" ] || [ "${rc}" -ne 0 ] || rc=1; exit "${rc}"' EXIT
  out=${ORG_COVERAGE_REPORTS:-}
  if [[ -z ${out} ]]; then
    scratch=$(mktemp -d)
    out=${scratch}
  fi
  mkdir -p "${out}"
  out=$(cd "${out}" && pwd)
fi
if [[ ${legs} == reports ]]; then
  # The directory is the belt's by definition, so a report an earlier
  # run left in a named one is cleared: the list is this run's.
  for f in "${reports[@]}" "${figures}"; do rm -f -- "${out:?}/${f}"; done
  # A reader sweeps the run directories earlier runs left (coverage-run.sh).
  # shellcheck source=mise/coverage-run.sh
  . "${belt_dir}/coverage-run.sh"
  org_coverage_sweep "${ORG_COVERAGE_RUN:-}"
  measured=$(ORG_COVERAGE_REPORTS="${out}" "${BASH}" "${BASH_SOURCE[0]}" "${label}" all) || exit 1
  if [[ -z ${measured} ]]; then
    # A floor that records its legs names each one this run lost (#1666),
    # which is the case a repository most needs named; the generic line
    # is for a floor with no record.
    "${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/coverage_floor.py" hold --label "${label}" --job gate < /dev/null || exit 1
    echo "${label}: no leg was measured — a check that reads nothing must not pass" >&2
    exit 1
  fi
  # The leg lines themselves, beside the reports (#1622): the gate keeps
  # them for the release job, which derives the floor from this
  # measurement instead of measuring the same commit again.
  printf '%s\n' "${measured}" > "${out}/${figures}"
  files="" named="" writers=""
  for f in "${reports[@]}"; do
    [[ ! -f ${out}/${f} ]] || files="${files:+${files},}${out}/${f}"
  done
  while read -r leg _; do
    if [[ " ${formatless[*]} " == *" ${leg} "* || ${leg} == fuzz-* ]]; then
      named="${named:+${named}, }${leg}"
    else
      writers=1
    fi
  done <<< "${measured}"
  [[ -z ${named} ]] || echo "${label}: ${named}: no upload format, not counted as a report writer" >&2
  if [[ -z ${files} && -z ${writers} ]]; then
    printf '%s\n' "${measured}"
    echo "${label}: upload none — every leg measured has no upload format (${named})"
    org_done=1
    exit 0
  fi
  if [[ -z ${files} ]]; then
    echo "${label}: the legs measured wrote no report — nothing for the badge to upload" >&2
    exit 1
  fi
  printf '%s\n' "${measured}"
  echo "${label}: upload ${files}"
  org_done=1
  exit 0
fi

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
  ts_runner=$("${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/coverage_js_runner.py" ${tracked_js}) || exit 1
fi
# The leg measures the repository's own `test` task (#1357 item 2), so a
# vitest tree with no `test` task has no suite the gate runs: it skips
# clean, as a Python tree with no `test` task does.
ts_test=""
if [[ ${ts_runner} == vitest\ * ]]; then
  ts_test=${python_test}
  [[ -n ${ts_test} ]] || ts_test=$(mise tasks info test --json 2> /dev/null) || ts_test=""
fi

# The legs the floor's record names (#1666). Discovery
# (ORG_COVERAGE_DISCOVER, coverage:adopt) probes plpgsql wherever a
# `test` task exists: the suite's run is how a PL/pgSQL leg is first
# found and recorded.
recorded=$("${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/coverage_floor.py" legs 2> /dev/null) || recorded=""
test_task=${python_test}
if [[ -z ${test_task} && -n ${ORG_COVERAGE_DISCOVER:-} ]]; then
  test_task=$(mise tasks info test --json 2> /dev/null) || test_task=""
fi

if [[ ${legs} == probe ]]; then
  [[ -f go.mod ]] && echo go
  [[ -f Cargo.toml ]] && echo rust
  [[ -n ${python_test} ]] && echo python
  case "${ts_runner}" in
    vitest\ *) [[ -z ${ts_test} ]] || echo typescript ;;
    unsupported\ *)
      echo "${label}: typescript: ${ts_runner#unsupported } is not a runner the leg supports (vitest), skipped" >&2
      ;;
    *) ;;
  esac
  if [[ -n ${ORG_COVERAGE_DISCOVER:-} && -n ${test_task} ]]; then
    echo plpgsql
  fi
  # A leg the record names is probed whether or not the tree still shows
  # it: the run then measures none of it, and coverage:check reds it by
  # name rather than skipping (#1666).
  while read -r leg; do
    [[ -z ${leg} ]] || echo "${leg%-branches}"
  done <<< "${recorded}"
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
  # The repository's own `test` run, measured where it ran (#1651, #1667):
  # `measured` appended the cover flags and go-test-exec to the GOFLAGS
  # the test command had, so the figure is the run the gate trusts, with
  # its -race and its own flags, and the suite runs once. Every test
  # process wrote its counters into the run's `go` directory, and
  # `go tool covdata` merges them into one profile here.
  #
  # -coverpkg=./... (#652). Without it a statement exercised only by a
  # NEIGHBOURING package's tests scores uncovered, so the gate enforces a
  # number below what the tests prove — measured at 0.5 points and 51
  # statements on stele, which is a floor's worth of understatement in a
  # repository whose floor is the point.
  go_test=$(mise tasks info test --json 2> /dev/null) || go_test=""
  if [[ -n ${go_test} ]]; then
    counters=("${ORG_COVERAGE_RUN:-/nonexistent}"/go/covcounters.*)
    if [[ -z ${ORG_COVERAGE_RUN:-} || ! -e ${counters[0]} ]]; then
      echo "${label}: the test task ran no measured Go — its test command runs" >&2
      cat >&2 << 'REMEDY'
  through "${ORG_BELT_DIR}/measured" (#1651), or no suite ran under mise
REMEDY
      exit 1
    fi
    go tool covdata textfmt -i="${ORG_COVERAGE_RUN}/go" -o="${out}/coverage.txt" >&2
  else
    # No `test` task: nothing says what the repository's suite is, so the
    # leg measures `go test ./...` itself, and says so.
    echo "${label}: go: no test task: measuring \`go test ./...\`" >&2
    go test ./... -coverpkg=./... -coverprofile="${out}/coverage.txt" -covermode=atomic >&2
  fi
  go_pct=$(go tool cover -func="${out}/coverage.txt" | awk '/^total:/ { sub(/%/, "", $3); print $3 }')
  if [[ -z ${go_pct} ]]; then
    echo "${label}: could not read a total from the Go profile — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "go ${go_pct}"
fi

# The workspace's pgrx extensions, from gate_surface.py, the derivation
# every pgrx task reads (#813): sets `pgrx_names` (space-separated crate
# names), `pgrx_majors` (one major per line, sorted), `pgrx_dirs` (each
# extension's directory relative to the workspace root, one per line),
# `workspace_root` and `target_directory`. Empty in a workspace with
# none, which pays for one `cargo metadata`.
pgrx_surface() {
  local metadata surface record spec kind majors dir
  pgrx_names=""
  pgrx_majors=""
  pgrx_dirs=""
  metadata=$(cargo metadata --no-deps --format-version 1 --locked) || exit 1
  workspace_root=$(jq -r '.workspace_root // empty' <<< "${metadata}") || exit 1
  target_directory=$(jq -r '.target_directory // empty' <<< "${metadata}") || exit 1
  surface=$("${ORG_PYTHON}" "${belt_dir}/gate_surface.py" <<< "${metadata}") || exit 1
  while IFS=$'\t' read -r record spec kind majors dir; do
    [[ ${record} == crate && ${kind} == pgrx ]] || continue
    pgrx_names="${pgrx_names} ${spec%@*}"
    pgrx_majors="${pgrx_majors}${pgrx_majors:+,}${majors}"
    pgrx_dirs="${pgrx_dirs}${dir}"$'\n'
  done <<< "${surface}"
  pgrx_majors=$(tr ',' '\n' <<< "${pgrx_majors}" | sed '/^$/d' | sort -un)
}

# `pgrx-majors` (#1692) prints the majors the `pgrx` selector measures,
# one per line, from the same derivation, and measures nothing: the
# release path reads each major's figure the gate's pgrx job kept, and
# needs the list to know it read them all. Nothing in a tree with no
# extension.
if [[ ${legs} == pgrx-majors ]]; then
  if [[ -f Cargo.toml ]]; then
    pgrx_surface
    [[ -z ${pgrx_majors} ]] || printf '%s\n' "${pgrx_majors}"
  fi
  exit 0
fi

# regex_quote <text>: the text as a regex matching it literally, every
# metacharacter of the regex crate's syntax escaped (`--ignore-filename-
# regex` is read by cargo-llvm-cov, in Rust).
regex_quote() {
  local text="$1" quoted="" char i
  for ((i = 0; i < ${#text}; i++)); do
    char=${text:i:1}
    case "${char}" in
      [][\\.*^\$+?\(\){}\|]) quoted+="\\${char}" ;;
      *) quoted+=${char} ;;
    esac
  done
  printf '%s' "${quoted}"
}

# The Rust measurement surface, shared by the `rust` and `rust-branches`
# legs (#1359): the llvm-tools assertion for the toolchain the leg runs
# on, and the pgrx extensions held out. Sets `excludes` (rust-branches'
# `--exclude` list) and pgrx_surface's variables. The toolchain is empty
# for the repository's stable pin.
rust_surface() {
  local toolchain="$1" name
  # COVERAGE_EXCLUDE is removed (#1653). The rust leg measures what the
  # test task's own command builds, so a member it leaves out is left out
  # by that command, where a reviewer reads it; a second list beside it
  # is a second statement of the same surface, which drifts. Refused by
  # name in both legs, whatever it names.
  if [[ -n ${COVERAGE_EXCLUDE:-} ]]; then
    echo "${label}: COVERAGE_EXCLUDE is set (${COVERAGE_EXCLUDE}) — it is removed (#1653):" >&2
    echo "  the rust leg measures what the test task's own command builds, so the command" >&2
    echo "  says what it leaves out (cargo test --workspace --exclude <member>); delete" >&2
    echo "  the variable from mise.toml's [env] (a pgrx extension needs no line: test:pgrx" >&2
    echo "  measures it)" >&2
    exit 1
  fi
  llvm_tools "${toolchain}"
  # A pgrx extension is measured by its own suite, `test:pgrx`, and never
  # here (#1656): its functions run in the Postgres backend, which this
  # run does not collect from, so every one of its lines read as zero
  # (#1652). It leaves this leg's surface, so its lines are counted once,
  # by the leg that can cover them — and this leg needs no Postgres.
  pgrx_surface
  excludes=""
  for name in ${pgrx_names}; do excludes="${excludes} --exclude ${name}"; done
}

# llvm_tools <toolchain>: assert the llvm-tools component on the
# toolchain a leg runs on (empty: the repository's stable pin). No
# task-time rustup, by rule: a component installed mid-run races every
# other task driving the toolchain (measured twice on the lab's v0.18.0
# release PR, #117). llvm-tools is a declared build input — pinned in
# the repository's mise.toml, installed before any task runs — and this
# asserts it with a remedy, never installs it.
llvm_tools() {
  local toolchain="$1" installed
  installed=$(rustup component list --installed ${toolchain:+--toolchain "${toolchain}"} 2> /dev/null) || installed=""
  if ! grep -q "^llvm-tools" <<< "${installed}"; then
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
}

if [[ -f Cargo.toml && ${legs} =~ ^(all|rust)$ ]]; then
  rust_surface ""
  # The repository's own `test` run, measured where it ran (#1653): its
  # test command ran through `measured`, which instrumented every cargo it
  # started into the directory rust-coverage-env.sh derives, so the figure
  # is the build the gate tests, its features and doctests counted, and
  # the suite runs once. This leg builds and runs nothing: under the same
  # environment, `cargo llvm-cov report` renders what that run wrote.
  # coverage:check depends on `test`, so a suite that failed never
  # reaches here: `report` itself exits 0 after a failed run (probed), and
  # a lower figure from a failed suite could become a floor.
  #
  # shellcheck source=mise/rust-coverage-env.sh
  . "${belt_dir}/rust-coverage-env.sh"
  org_rust_coverage_env "${label}"
  # Profiles count only when this run's `measured` made its marker: the
  # profiles of an earlier run whose command no longer runs through it
  # would otherwise read as this one's.
  if [[ -z ${ORG_COVERAGE_RUN:-} || ! -d ${ORG_COVERAGE_RUN}/rust ]] \
    || ! compgen -G "$(dirname "${LLVM_PROFILE_FILE:-/nonexistent/x}")/*.profraw" > /dev/null; then
    echo "${label}: the test task ran no measured Rust — its test command runs" >&2
    cat >&2 << 'REMEDY'
  through "${ORG_BELT_DIR}/measured" (#1653), or no suite ran under mise
REMEDY
    exit 1
  fi
  # The extension's files leave the report by path (#1656): `report`
  # takes no `--exclude`, and the command may well have built it. Each
  # extension's directory under the workspace root, every regex
  # metacharacter escaped; the root package's own targets where the
  # extension is the root package.
  ignore=()
  while IFS= read -r dir; do
    [[ -n ${dir} && ${dir} != - ]] || continue
    if [[ ${dir} == . ]]; then
      at=$(regex_quote "${workspace_root}")
      ignore+=(--ignore-filename-regex "^${at}/(src|tests|benches|examples)/|^${at}/build\.rs$")
    else
      at=$(regex_quote "${workspace_root}/${dir}")
      ignore+=(--ignore-filename-regex "^${at}/")
    fi
  done <<< "${pgrx_dirs}"
  cargo llvm-cov report ${ignore[@]+"${ignore[@]}"} --lcov --output-path "${out}/lcov.info" >&2
  # The number is read out of lcov.info, the report Codecov receives
  # (#1528): one run is both the floor's measurement and the badge's
  # upload. Lines found and hit (LF, LH) summed over every file are the
  # json summary's `.data[0].totals.lines` count and covered: measured
  # equal on a two-member workspace, 7/17 whole and 5/11 with one member
  # excluded.
  rust_pct=$(awk -F: '
    $1 == "LF" { found += $2 }
    $1 == "LH" { hit += $2 }
    END { if (found > 0) printf "%.16g\n", hit * 100 / found }
  ' "${out}/lcov.info" 2> /dev/null) || rust_pct=""
  if [[ -z ${rust_pct} ]]; then
    echo "${label}: could not read a total from the Rust report — a" >&2
    echo "  measurement that reads nothing must not become a floor" >&2
    exit 1
  fi
  echo "rust ${rust_pct}"
  # The legs the gate holds elsewhere, named so a reader of this run sees
  # the extension is measured, not missing.
  for one in ${pgrx_majors}; do
    echo "${label}: pgrx-pg${one}: measured and held by test:pgrx" >&2
  done
fi

if [[ -f Cargo.toml && ${legs} == pgrx ]]; then
  # pgrx-test.sh's own run, every declared major, its leg lines kept on
  # stdout and everything else sent to stderr.
  ORG_PGRX_MAJOR="" "${belt_dir}/pgrx-test.sh" measure \
    | awk '/^pgrx-pg[0-9]+ [0-9.]+$/ { print; next } { print > "/dev/stderr" }'
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
  toolchains=$(rustup toolchain list 2> /dev/null) || toolchains=""
  if ! grep -q "^${FUZZ_TOOLCHAIN}" <<< "${toolchains}"; then
    echo "${label}: FUZZ_TOOLCHAIN='${FUZZ_TOOLCHAIN}' is declared but not installed" >&2
    echo "  it rides the rust pin in mise.toml so it installs before tasks run, never mid-run" >&2
    exit 1
  fi
  rust_surface "${FUZZ_TOOLCHAIN}"
  # RUSTUP_TOOLCHAIN as a per-command prefix: mise exports the stable
  # pin's value into the task env, and only a command-scoped assignment
  # beats it (the lint:msrv shape).
  #
  # The nightly's own directory (#1877): unset, cargo-llvm-cov falls back
  # to `target/llvm-cov-target`, where the stable leg's executables are,
  # and reports both. Named for the nightly's `rustc -vV` as the stable
  # leg's is for its own (rust-coverage-env.sh), so the two never meet.
  compiler=$(RUSTUP_TOOLCHAIN="${FUZZ_TOOLCHAIN}" "${ORG_PYTHON}" "${belt_dir}/gate_cache_key.py" digest) || exit 1
  if [[ -z ${target_directory} ]]; then
    echo "${label}: cargo metadata named no target_directory — nowhere to measure Rust into" >&2
    exit 1
  fi
  # shellcheck disable=SC2086  # ${excludes} is a built '--exclude <member>' list,
  # which cargo must receive as separate arguments
  report=$(CARGO_TARGET_DIR="${target_directory}/llvm-cov-target-${compiler}" \
    RUSTUP_TOOLCHAIN="${FUZZ_TOOLCHAIN}" \
    cargo llvm-cov --workspace --locked ${excludes} --branch --json --summary-only)
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

# One coverage.py report, with its one failure the leg can name (#1528):
# a file recorded under a path no tracked file has. `relative_files`
# records a file relative to the directory its process ran in, so a
# test that starts a Python child from a subdirectory leaves a path the
# report cannot open, and coverage stops on a raw `No source for code`.
# The leg names the file, the cause and the remedy. The file is not
# dropped from the report: a measurement that silently loses a file
# would hold a floor over less than it says.
py_report() {
  local out missing
  if out=$(coverage "$@" 2>&1); then
    [[ -z ${out} ]] || printf '%s\n' "${out}" >&2
    return 0
  fi
  missing=$(sed -n "s/^No source for code: '\([^']*\)'.*/\1/p" <<< "${out}" | sed -n 1p)
  if [[ -z ${missing} ]]; then
    printf '%s\n' "${out}" >&2
    exit 1
  fi
  echo "${label}: coverage recorded '${missing}', which no file in the tree matches —" >&2
  echo "  a test ran a Python child under measurement from a directory other than the" >&2
  echo "  repository root, and its files were recorded relative to that directory;" >&2
  echo "  start the child from the repository root" >&2
  exit 1
}

# The run's directory, where `measured` wrote what the suite measured.
run=${ORG_COVERAGE_RUN:-}

if [[ -n ${python_test} && ${legs} =~ ^(all|python)$ ]]; then
  # coverage.py's data from the repository's own `test` run (#1331,
  # #1667): every python3 the suite started under `measured` loaded the
  # belt's start-up hook and wrote here. The discovery roots stay where
  # the repository wrote them; the belt never restates them.
  if ! command -v coverage > /dev/null; then
    echo "${label}: tracked Python and a test task, but coverage is missing —" >&2
    echo "  the belt pins pipx:coverage; run: mise install" >&2
    exit 1
  fi
  data=("${run:-/nonexistent}"/python/.coverage*)
  if [[ -z ${run} || ! -f ${run}/coverage.rc || ! -e ${data[0]} ]]; then
    # No figure, and the floor's record decides whether that is red
    # (#1666): its remedy names `measured`.
    echo "${label}: python: the test run measured no Python — its test command runs" >&2
    cat >&2 << 'REMEDY'
  through "${ORG_BELT_DIR}/measured" (#1667), or no suite ran under mise
REMEDY
  else
    # The rc file `measured` wrote for this run: its data file, its
    # source, its branch and omit settings are the ones the suite ran
    # under.
    rc_file="${run}/coverage.rc"
    # --keep: the run's data stays for any other reader in the same run.
    if ! coverage combine --keep --rcfile="${rc_file}" --quiet >&2; then
      echo "${label}: the test task ran no measured Python — a measurement" >&2
      echo "  that reads nothing must not become a floor" >&2
      exit 1
    fi
    py_report json --rcfile="${rc_file}" --quiet -o "${out}/coverage.json"
    py_pct=$(jq -r '.totals.percent_statements_covered // empty' "${out}/coverage.json")
    br_pct=$(jq -r '.totals.percent_branches_covered // empty' "${out}/coverage.json")
    rm -f -- "${out}/coverage.json"
    if [[ -z ${py_pct} || -z ${br_pct} ]]; then
      echo "${label}: could not read a total from the Python report — a" >&2
      echo "  measurement that reads nothing must not become a floor" >&2
      exit 1
    fi
    # The report Codecov receives is rendered from this measurement's own
    # data, so the badge and the floor read one run (#707's law), and
    # the gate's run is the one the badge job uploads (#1528).
    py_report xml --rcfile="${rc_file}" --quiet -o "${out}/coverage.xml"
    echo "python ${py_pct}"
    echo "python-branches ${br_pct}"
  fi
fi

if [[ -n ${ts_test} && ${legs} =~ ^(all|typescript)$ ]]; then
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
  vitest="${PWD}/node_modules/.bin/vitest"
  if [[ ! -x ${vitest} ]]; then
    echo "${label}: vitest is in the lockfile but not installed — install from the" >&2
    echo "  tracked lockfile before measuring" >&2
    exit 1
  fi
  # The vitest run the gate's `test` task makes, read from the steps
  # task_walk.py walks (#1357 item 2, #1608): one plain invocation, its
  # arguments and its task's dir, or a refusal by name. The reading of
  # the scripts is coverage_js_runner.py's.
  command_json=$("${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/coverage_js_runner.py" \
    --vitest-command "${label}" test) || exit 1
  test_dir=$(jq -r '.dir' <<< "${command_json}")
  args_lines=$(jq -r '.args[]' <<< "${command_json}")
  test_args=()
  while IFS= read -r arg; do
    test_args+=("${arg}")
  done <<< "${args_lines}"
  # Removed by the EXIT trap above.
  ts_work=$(mktemp -d)
  # The repository's own vitest config decides what is in the suite and
  # in the report (`coverage.include`: vitest counts only files a test
  # loaded unless it is set). The leg sets only what makes the number
  # readable here: the provider, the two reporters, and a directory of
  # its own, so nothing lands in the tree unasked.
  if ! (cd "${test_dir}" && "${vitest}" "${test_args[@]}" \
    --coverage.enabled=true \
    --coverage.provider="${provider}" \
    --coverage.reporter=json-summary \
    --coverage.reporter=lcov \
    --coverage.reportsDirectory="${ts_work}") >&2; then
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
  cp "${ts_work}/lcov.info" "${out}/lcov-typescript.info"
  echo "typescript ${ts_pct}"
  echo "typescript-branches ${ts_br}"
fi
if [[ -n ${run} && ${legs} =~ ^(all|plpgsql)$ && -f ${run}/plpgsql-coverage.json ]]; then
  # The report the test run's teardown wrote; the reading and every
  # refusal are plpgsql_coverage.py's. No report is no plpgsql figure,
  # and the floor's record decides whether that is red.
  "${ORG_PYTHON}" "$(dirname "${BASH_SOURCE[0]}")/plpgsql_coverage.py" \
    totals "${label}" "${run}/plpgsql-coverage.json" || exit 1
fi

# fuzz_leg_name <name>: a fuzz target's leg (#1896), `fuzz-<name>`
# lower-cased, every character but [a-z0-9] turned into `-`, so it fits
# coverage_floor.py's leg grammar whatever the target is called.
fuzz_leg_name() {
  local name
  name=$(LC_ALL=C tr '[:upper:]' '[:lower:]' <<< "$1")
  printf 'fuzz-%s\n' "${name//[^a-z0-9]/-}"
}

# The fuzz targets, enumerated before any is measured, so two that
# derive one leg are refused before a minute is spent building: one
# record per target in `fuzz_found`, `<leg> <kind> <where> <name>`,
# kind `rust` (where `-`) or `go` (where the package directory).
fuzz_targets() {
  local manifest target files nested file dir names name mod n
  fuzz_found=""
  if [[ -f fuzz/Cargo.toml ]]; then
    manifest=$("${ORG_PYTHON}" -c 'import sys, tomllib
for b in tomllib.load(open(sys.argv[1], "rb")).get("bin", []):
    print(b["name"])' fuzz/Cargo.toml) || {
      echo "${label}: could not read the [[bin]] targets of fuzz/Cargo.toml" >&2
      exit 1
    }
    while IFS= read -r target; do
      [[ -n ${target} ]] || continue
      fuzz_found+="$(fuzz_leg_name "${target}") rust - ${target}"$'\n'
    done <<< "${manifest}"
  fi
  # Tracked files are read from a work tree; outside one there are none,
  # as the python leg reads it. Inside one, a listing that fails is red,
  # never a shorter list.
  [[ -f go.mod ]] && git rev-parse --is-inside-work-tree > /dev/null 2>&1 || return 0
  # The root module's tracked tests only: a nested module is its own, and
  # the go leg measures the root one.
  files=$(git ls-files -- '*_test.go' ':!vendor/**') || exit 1
  nested=$(git ls-files -- '*/go.mod') || exit 1
  while IFS= read -r file; do
    [[ -n ${file} ]] || continue
    dir=$(dirname "${file}")
    while IFS= read -r mod; do
      [[ -z ${mod} || ${dir}/ != "$(dirname "${mod}")"/* ]] || continue 2
    done <<< "${nested}"
    names=$(sed -nE 's/^func (Fuzz[A-Z0-9_][A-Za-z0-9_]*|Fuzz)\([A-Za-z_][A-Za-z0-9_]* \*testing\.F\).*/\1/p' "${file}") || exit 1
    while IFS= read -r name; do
      [[ -n ${name} ]] || continue
      if [[ ${dir} == . ]]; then
        n=$(fuzz_leg_name "${name#Fuzz}")
      else
        n=$(fuzz_leg_name "${dir}-${name#Fuzz}")
      fi
      fuzz_found+="${n} go ${dir} ${name}"$'\n'
    done <<< "${names}"
  done <<< "${files}"
}

# fuzz_origin <kind> <where> <name>: how a refusal names a target.
fuzz_origin() {
  if [[ $1 == rust ]]; then
    printf "cargo-fuzz target '%s'" "$3"
  else
    printf "Go fuzz target %s in %s" "$3" "$2"
  fi
}

if [[ ${legs} =~ ^(all|fuzz)$ ]]; then
  fuzz_targets
  # Two targets, one leg: red, naming both, before anything is built.
  clash=""
  while read -r leg kind where name; do
    [[ -n ${leg} ]] || continue
    while read -r other okind owhere oname; do
      [[ ${other} == "${leg}" && "${okind} ${owhere} ${oname}" != "${kind} ${where} ${name}" ]] || continue
      [[ " ${clash} " != *" ${leg} "* ]] || continue
      clash+=" ${leg}"
      one=$(fuzz_origin "${kind}" "${where}" "${name}")
      two=$(fuzz_origin "${okind}" "${owhere}" "${oname}")
      echo "${label}: ${one} and ${two} both derive the leg ${leg} — rename" \
        "one, so each target holds a floor of its own" >&2
    done <<< "${fuzz_found}"
  done <<< "${fuzz_found}"
  [[ -z ${clash} ]] || exit 1
fi

if [[ ${legs} =~ ^(all|fuzz)$ && ${fuzz_found} == *" rust "* ]]; then
  llvm_tools ""
  if ! command -v cargo-fuzz > /dev/null; then
    echo "${label}: fuzz/ exists but cargo-fuzz is missing — pin it as a build input:" >&2
    echo '  mise.toml: "cargo:cargo-fuzz" = "<version>" (installed before tasks, never mid-run)' >&2
    exit 1
  fi
  fuzz_work=$(mktemp -d)
  # The tracked sources, the figure's surface: everything the repository
  # tracks as Rust outside the harnesses themselves, by absolute path as
  # cargo records it. A file the target does not link counts nothing,
  # and nothing outside the repository (registry, toolchain, generated
  # code) is named.
  root=$(pwd -P)
  sources=()
  tracked_rs=$(git ls-files -- '*.rs' ':!fuzz/fuzz_targets/**') || exit 1
  while IFS= read -r file; do
    [[ -z ${file} ]] || sources+=("${root}/${file}")
  done <<< "${tracked_rs}"
  verbose=$(rustc -vV) || exit 1
  host=$(sed -n 's/^host: //p' <<< "${verbose}")
  sysroot=$(rustc --print sysroot) || exit 1
  llvm_cov="${sysroot}/lib/rustlib/${host}/bin/llvm-cov"
  if [[ -z ${host} || ! -x ${llvm_cov} ]]; then
    echo "${label}: no llvm-cov at ${llvm_cov} — the llvm-tools component is declared but" >&2
    echo "  its binaries are not where the toolchain keeps them" >&2
    exit 1
  fi
  # A build directory of the compiler's own (#1877), beside the fuzz
  # crate's other builds: a toolchain bump is a new, empty directory,
  # never an old compiler's binary read as this one's.
  fuzz_metadata=$(cargo metadata --no-deps --format-version 1 --locked --manifest-path fuzz/Cargo.toml) || exit 1
  fuzz_target_dir=$(jq -r '.target_directory // empty' <<< "${fuzz_metadata}") || exit 1
  if [[ -z ${fuzz_target_dir} ]]; then
    echo "${label}: cargo metadata named no target_directory for fuzz/ — nowhere to build into" >&2
    exit 1
  fi
  compiler=$("${ORG_PYTHON}" "${belt_dir}/gate_cache_key.py" digest) || exit 1
  build="${fuzz_target_dir}/coverage-${compiler}"
  # cargo fuzz coverage writes its profiles under fuzz/coverage/<target>/
  # and offers no other place; a directory this run made is removed by the
  # EXIT trap, and a target's stale raw profiles are cleared before its
  # run, which would otherwise merge into this one's.
  [[ -e fuzz/coverage ]] || fuzz_made=${PWD}/fuzz/coverage
  # shellcheck source=mise/fuzz-corpus-lib.sh
  . "${belt_dir}/fuzz-corpus-lib.sh"
  while read -r leg kind _ target; do
    [[ ${kind} == rust ]] || continue
    # A copy that fails says why itself and ends the run (errexit).
    copied=$(org_fuzz_corpus_copy "${target}" "${fuzz_work}/${target}")
    if [[ ${copied} == 0 ]]; then
      echo "${label}: ${leg}: fuzz target '${target}' has no committed corpus — a" >&2
      echo "  measurement that reads nothing must not become a floor (lint:fuzz-build names the remedy)" >&2
      exit 1
    fi
    rm -rf -- "fuzz/coverage/${target}/raw"
    if ! cargo fuzz coverage -s none --target-dir "${build}" "${target}" "${fuzz_work}/${target}" >&2; then
      echo "${label}: ${leg}: cargo fuzz coverage failed replaying the committed corpus (above)" >&2
      exit 1
    fi
    totals=$("${llvm_cov}" export -summary-only \
      -instr-profile="fuzz/coverage/${target}/coverage.profdata" \
      "${build}/${host}/release/${target}" ${sources[@]+"${sources[@]}"} \
      | jq -r '.data[0].totals.lines | "\(.count) \(.covered)"') || totals=""
    read -r count covered <<< "${totals}"
    if [[ ! ${count:-} =~ ^[0-9]+$ || ! ${covered:-} =~ ^[0-9]+$ || ${count} == 0 ]]; then
      echo "${label}: ${leg}: llvm-cov counted no tracked line (count '${count:-absent}') — a" >&2
      echo "  measurement that reads nothing must not become a floor" >&2
      exit 1
    fi
    awk -v leg="${leg}" -v n="${count}" -v c="${covered}" 'BEGIN { printf "%s %.16g\n", leg, c * 100 / n }'
  done <<< "${fuzz_found}"
fi

if [[ ${legs} =~ ^(all|fuzz)$ && ${fuzz_found} == *" go "* ]]; then
  if ! command -v go > /dev/null; then
    echo "${label}: go.mod present but go missing — pin go in mise.toml" >&2
    exit 1
  fi
  fuzz_work=${fuzz_work:-$(mktemp -d)}
  while read -r leg kind dir name; do
    [[ ${kind} == go ]] || continue
    # The module's packages the target's package imports, itself included:
    # the code its seeds can reach.
    pkgs=$(go list -deps -f '{{if and .Module .Module.Main}}{{.ImportPath}}{{end}}' "./${dir}") || exit 1
    coverpkg=$(sed '/^$/d' <<< "${pkgs}" | paste -sd, -)
    if [[ -z ${coverpkg} ]]; then
      echo "${label}: ${leg}: go list named no package of the module for ./${dir}" >&2
      exit 1
    fi
    profile="${fuzz_work}/${leg}.out"
    if ! go test -count=1 -run "^${name}\$" -coverpkg="${coverpkg}" \
      -coverprofile="${profile}" "./${dir}" >&2; then
      echo "${label}: ${leg}: ${name}'s seed corpus failed under measurement (above)" >&2
      exit 1
    fi
    pct=$(go tool cover -func="${profile}" | awk '/^total:/ { sub(/%/, "", $3); print $3 }')
    if [[ -z ${pct} ]]; then
      echo "${label}: ${leg}: could not read a total from the Go profile — a" >&2
      echo "  measurement that reads nothing must not become a floor" >&2
      exit 1
    fi
    echo "${leg} ${pct}"
  done <<< "${fuzz_found}"
fi
org_done=1

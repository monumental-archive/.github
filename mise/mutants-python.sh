#!/usr/bin/env bash
# The Python leg of `audit:mutants` (#1354): would the suite fail if the
# code were wrong? Org canon. mutmut changes the code one site at a time
# and runs the tests that reach it; a mutant the tests still pass is a
# SURVIVOR, and a survivor is a named line to fix.
#
# THE MEASURE IS "NO UNEXPLAINED SURVIVOR", NEVER A SCORE (#1336). Red
# while any mutant is neither killed by an assertion nor excluded, with
# a reason, at its site in the repository.
#
# ADOPTION IS THE TOOL'S OWN CONFIG. A repository adopts by tracking
# `[tool.mutmut]` in pyproject.toml (or `[mutmut]` in setup.cfg), which
# also names the paths to mutate and where the tests are: the roots stay
# the repository's, never the belt's. Without it this skips clean, by
# name.
#
# EVERY EXCLUSION CARRIES ITS REASON, at the site:
#
#   - `# pragma: no mutate` (and its `block` and `start` forms) carries
#     the reason in the same comment, after ` -- `:
#         x = 3  # pragma: no mutate -- the fixture's width, no caller
#     mutmut 3.8.1 reads only the first word after `no mutate`, so the
#     reason never changes what is excluded. `end` closes a region and
#     needs none.
#   - every entry of the config's `do_not_mutate`,
#     `do_not_mutate_patterns` and `only_mutate` lists sits on its own
#     line with a `#` line above it saying why.
#   - `mutate_only_covered_lines = true` is refused: it drops every line
#     no test runs, which is exactly where the survivors are, with no
#     reason anywhere.
#
# WHAT COUNTS. From `mutmut results --all true`, one status per mutant:
# killed, timeout, caught by type check and segfault are killed (the
# suite noticed); survived and "no tests" are survivors — a mutant no
# test reaches is the least explained of all; anything else
# (suspicious, skipped, not checked, interrupted) was never judged and
# is red by name.
#
# EXCLUDED COUNTS SITES, not mutants: mutmut never generates an excluded
# mutant, so what there is to count is the reasoned pragmas and list
# entries. The summary line says so.
#
# mutmut has no sharding (3.8.1 has no option for it), so the leg runs
# whole: a shard other than 0/1 is red, never a silent re-run of
# everything.
#
# Prints each survivor (file:line, the mutant, the changed line) and one
# summary line: mutants, killed, survived, excluded.
set -euo pipefail

label="audit:mutants: python"

shard="${MUTANTS_SHARD:-0/1}"
if [[ ${shard} != 0/1 ]]; then
  echo "${label}: MUTANTS_SHARD='${shard}' — mutmut cannot shard; the python jobs run one shard, 0/1" >&2
  exit 1
fi

summary() { # summary <row>: the counts the totals job reads
  [[ -n ${MUTANTS_SUMMARY_DIR:-} ]] || return 0
  mkdir -p "${MUTANTS_SUMMARY_DIR}"
  printf '%s\n' "$1" > "${MUTANTS_SUMMARY_DIR}/python.tsv"
}

config=""
if git ls-files --error-unmatch pyproject.toml > /dev/null 2>&1 \
  && grep -q '^\[tool\.mutmut\]' pyproject.toml; then
  config=pyproject.toml
elif git ls-files --error-unmatch setup.cfg > /dev/null 2>&1 \
  && grep -q '^\[mutmut\]' setup.cfg; then
  config=setup.cfg
fi
if [[ -z ${config} ]]; then
  echo "${label}: no [tool.mutmut] in a tracked pyproject.toml (or [mutmut] in setup.cfg), skipped"
  summary "0/1"$'\t'"skipped"
  exit 0
fi

# The config's own section only: from its header to the next header.
section='^\[tool\.mutmut\]'
[[ ${config} == pyproject.toml ]] || section='^\[mutmut\]'

# One pass over the section: list entries without a reason line above
# them (a TOML list carries quoted entries; setup.cfg one indented entry
# per line), entries on the opening line, the refused switch, and the
# count of reasoned entries. Records: line, kind, text; `listed` last.
# The section pattern rides the environment: `awk -v` would read its
# backslashes as escapes and turn `\[` into a bracket expression.
scan=$(SECTION="${section}" awk '
  function entry(text,   e) {
    e = text; sub(/^[[:space:]]*/, "", e); sub(/,[[:space:]]*$/, "", e); return e
  }
  /^\[/ { inside = ($0 ~ ENVIRON["SECTION"]); list = 0; next }
  !inside { next }
  /^[[:space:]]*mutate_only_covered_lines[[:space:]]*=[[:space:]]*(true|True|1|t)[[:space:]]*$/ {
    printf "%d\trefused\tmutate_only_covered_lines = true drops every line no test runs\n", NR
    next
  }
  /^[[:space:]]*(do_not_mutate|do_not_mutate_patterns|only_mutate)[[:space:]]*=/ {
    key = $0; sub(/^[[:space:]]*/, "", key); sub(/[[:space:]]*=.*/, "", key)
    rest = $0; sub(/^[^=]*=[[:space:]]*/, "", rest)
    first = rest; sub(/^\[[[:space:]]*/, "", first)
    if (rest ~ /^\[/) {
      if (first ~ /^["\047]/) printf "%d\t%s\tan entry on the opening line\n", NR, key
      list = (rest ~ /\]/) ? 0 : 1
    } else {
      if (rest ~ /[^[:space:]]/) printf "%d\t%s\tan entry on the opening line\n", NR, key
      list = 2
    }
    prev = ""
    next
  }
  list == 1 {
    if ($0 ~ /^[[:space:]]*\]/) { list = 0; next }
    if ($0 ~ /^[[:space:]]*["\047]/) {
      if (prev ~ /^[[:space:]]*#/) listed++
      else printf "%d\t%s\t%s\n", NR, key, entry($0)
      if ($0 ~ /\][[:space:]]*$/) list = 0
    }
    if ($0 !~ /^[[:space:]]*$/) prev = $0
    next
  }
  list == 2 {
    if ($0 !~ /^[[:space:]]/ || $0 ~ /^[[:space:]]*$/) { list = 0; next }
    if ($0 !~ /^[[:space:]]*[#;]/) {
      if (prev ~ /^[[:space:]]*[#;]/) listed++
      else printf "%d\t%s\t%s\n", NR, key, entry($0)
    }
    prev = $0
  }
  END { printf "listed\t%d\n", listed + 0 }
' "${config}")
listed=$(awk -F '\t' '$1 == "listed" { print $2 }' <<< "${scan}")
findings=$(awk -F '\t' '$1 != "listed"' <<< "${scan}")

# Pragmas without a reason, in every tracked Python file. `end` closes a
# region and is the one form that needs none. `git grep` narrows the
# files; the tokenizer decides, since mutmut reads pragmas from COMMENTS
# only and a string that spells one (a test fixture) is not a site.
candidates=$(git grep -l -E 'pragma:.*no mutate' -- ':!vendor/**' '*.py' || true)
pragmas=""
if [[ -n ${candidates} ]]; then
  files=()
  while IFS= read -r f; do files+=("${f}"); done <<< "${candidates}"
  pragmas=$(python3 "${ORG_BELT_DIR:?unset — no belt}/mutmut-pragmas.py" "${files[@]}")
fi
bare=()
sites=0
while IFS=$'\t' read -r file line comment; do
  [[ -n ${file} ]] || continue
  rest="${comment#*no mutate}"
  word=$(awk '{ sub(/^[: ]+/, ""); print $1 }' <<< "${rest}")
  [[ ${word} != end ]] || continue
  if [[ ${rest} =~ \ --\ +[^[:space:]] ]]; then
    sites=$((sites + 1))
  else
    bare+=("${file}:${line}")
  fi
done <<< "${pragmas}"

red=0
if [[ -n ${findings} ]]; then
  while IFS=$'\t' read -r line kind text; do
    if [[ ${kind} == refused ]]; then
      echo "${label}: ${config}:${line}: ${text}" >&2
    else
      echo "${label}: ${config}:${line}: ${kind} entry ${text} has no # reason line above it" >&2
    fi
  done <<< "${findings}"
  red=1
fi
for site in ${bare[@]+"${bare[@]}"}; do
  echo "${label}: ${site}: # pragma: no mutate with no reason — write it after ' -- '" >&2
  red=1
done
if [[ ${red} -ne 0 ]]; then
  echo "  an exclusion records a mutant the suite cannot kill; say why at the site" >&2
  exit 1
fi
excluded=$((sites + listed))

command -v mutmut > /dev/null || {
  echo "${label}: mutmut missing — the belt pins it lazy; install it by name:" >&2
  echo "    mise install pipx:mutmut" >&2
  exit 1
}

status=0
mutmut run || status=$?
if [[ ${status} -ne 0 ]]; then
  echo "${label}: mutmut run exited ${status} — nothing judged (a failing unmutated suite exits 1)" >&2
  exit 1
fi

results=$(mutmut results --all true)
total=0
killed=0
survived=0
unjudged=0
survivors=()
while IFS= read -r row; do
  [[ ${row} =~ ^[[:space:]]*([^[:space:]]+):[[:space:]]+(.+)$ ]] || continue
  name="${BASH_REMATCH[1]}"
  state="${BASH_REMATCH[2]}"
  total=$((total + 1))
  case ${state} in
    killed | timeout | "caught by type check" | segfault) killed=$((killed + 1)) ;;
    survived | "no tests")
      survived=$((survived + 1))
      survivors+=("${state}"$'\t'"${name}")
      ;;
    *) unjudged=$((unjudged + 1)) ;;
  esac
done <<< "${results}"

# `mutmut show` diffs the mutated FUNCTION on its own (mutmut 3.8.1,
# diff_apply.get_diff_for_mutant): the `---` header is the file, but the
# hunk's line numbers count from the function's first line, not the
# file's. So the file line is found in the file: the function's `def`
# (its name is the mutant's, between `x_` or `xǁClassǁ` and
# `__mutmut_N`), then the first line at or after it that reads as the
# diff's removed line.
for s in ${survivors[@]+"${survivors[@]}"}; do
  state="${s%%$'\t'*}"
  name="${s#*$'\t'}"
  shown=$(mutmut show "${name}")
  file=$(awk '/^--- / { print $2; exit }' <<< "${shown}")
  removed=$(awk '/^-[^-]/ { print substr($0, 2); exit }' <<< "${shown}")
  added=$(awk '/^\+[^+]/ { print substr($0, 2); exit }' <<< "${shown}")
  func="${name##*.}"
  func="${func%__mutmut_*}"
  func="${func##*ǁ}"
  func="${func#x_}"
  line=$(awk -v fn="${func}" -v want="${removed}" '
    function bare(t) { sub(/^[[:space:]]+/, "", t); sub(/[[:space:]]+$/, "", t); return t }
    BEGIN { want = bare(want) }
    !d && $0 ~ ("^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+" fn "[[:space:]]*\\(") { d = NR }
    d && bare($0) == want { print NR; found = 1; exit }
    END { if (!found) print d + 0 }' "${file}" 2> /dev/null || echo 0)
  added="${added#"${added%%[![:space:]]*}"}"
  echo "${label}: ${state}: ${file}:${line}: ${name}: ${added}"
done

echo "${label}: ${total} mutants, ${killed} killed, ${survived} survived, ${excluded} excluded (sites)"
if [[ ${unjudged} -gt 0 ]]; then
  echo "${label}: ${unjudged} mutant(s) never judged (suspicious, skipped, not checked or interrupted)" >&2
  exit 1
fi
summary "0/1"$'\t'"${total}"$'\t'"${killed}"$'\t'"${survived}"$'\t'"${excluded}"
if [[ ${survived} -gt 0 ]]; then
  echo "${label}: red — kill each survivor with an assertion, or exclude it at its site" >&2
  echo "  with '# pragma: no mutate -- <why the mutant is equivalent>'" >&2
  exit 1
fi

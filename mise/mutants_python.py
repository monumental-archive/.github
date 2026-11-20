#!/usr/bin/env python3
"""The reading and judging half of audit:mutants' Python leg (#1354, #1771).

mutants-python.sh is the sequence; everything that reads or decides is
here, one subcommand per step the shell takes, so that it comes under
the canon's coverage figure and its own mutation run (CONTRIBUTING.md,
"A helper's language", #1486). The tool is cosmic-ray, which mutates
the TRACKED FILE IN PLACE, one mutant at a time, and runs the
repository's own tests against it: no copy of the tree, no rewritten
imports, nothing a child process must be handed (#1771).

- `paths CONFIG`: the adoption file's `module-path`, one per line, for
  the shell's exit trap.
- `pragmas PRAGMAS`: every `# pragma: no mutate` the tokenizer found
  (mutation_pragmas.py) carries its reason after ` -- `, and is the line
  form: `block`, `start` and `end` are red by name.
- `plan CONFIG PLAN`: once per run. Refuses a dirty tracked tree under
  `module-path`; lists every mutant (`cosmic-ray init`, `dump`) of the
  tracked files, sorted by (path, operator, occurrence); reds a mutant
  in a file `tests` matches (a leaked exclusion); runs every test file
  alone through `test-command`, under coverage's start-up hook, and
  records its time and the lines it executes, child processes
  included. A test file that fails alone is red, every one named.
- `judge PLAN PRAGMAS SHARD OUT`: judges this shard's mutants, one at
  a time, and states the verdict. After each mutant, every tracked file
  under `module-path` that differs from git is restored and red by
  name with the mutant, so no mutant is judged against another's
  writes (#1832).

The adoption file is `cosmic-ray.toml` at the repository's root:
cosmic-ray's own `[cosmic-ray]` table (`module-path`, `excluded-modules`,
`test-command`) and the leg's `[audit-mutants]` (`tests`, git pathspecs
for the test files). `test-command` runs ONE test file: the leg fills
`{test}` (the file's path), `{dir}` and `{name}`. Those two are all the
leg knows about the suite: no layout, import style, runner or naming
rule is assumed.

Stdlib only.
"""

import argparse
import ast
import json
import operator
import os
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import decoded
import startup_hook
import tool_python

LABEL = "audit:mutants: python"
EXIT_RED = 1
CONFIG_TABLE = "cosmic-ray"
LEG_TABLE = "audit-mutants"
# What a mutant's judging can say (cosmic-ray 8.9.0, work_item.py).
# `incompetent` is a test command that could not start: the mutant broke
# it, so the suite noticed. A time limit is the runner's exit 1: killed.
KILLED = frozenset({"killed", "incompetent"})
NO_TESTS = "no tests"
# A time limit is never shorter than this, nor than three planned runs.
FLOOR_SECONDS = 10
LIMIT_FACTOR = 3
# The lines of a failing test file's output shown with its name.
TAIL_LINES = 20
# `git status --porcelain=v1`'s two status letters and a space.
STATUS_PREFIX = 3
# How often a long shard says how far it is.
PROGRESS_EVERY = 100


def which(name: str) -> str:
    """Find a command on the search path as it stands now.

    Returns:
        Its full path, or the bare name for the call to fail on.

    """
    return shutil.which(name) or name


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the current directory.

    Returns:
        The finished process, its output captured as text.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    return subprocess.run(
        [which("git"), *args], capture_output=True, text=True, check=False
    )


def red(message: str) -> None:
    """Print one refusal, labelled."""
    print(f"{LABEL}: {message}", file=sys.stderr)


@dataclass
class Adoption:
    """What `cosmic-ray.toml` says the leg needs."""

    paths: list[str]
    command: str
    tests: list[str]


def read_adoption(text: bytes) -> tuple[list[object], str, list[object]]:
    """Decode the two tables the leg reads.

    Returns:
        `module-path` (a string becomes a list of one), `test-command`
        and `tests`, still to be narrowed.

    """
    table = decoded.load_toml(text)
    tool = decoded.as_object(table.get(CONFIG_TABLE), f"[{CONFIG_TABLE}]")
    leg = decoded.as_object(table.get(LEG_TABLE), f"[{LEG_TABLE}]")
    raw = tool.get("module-path")
    paths: list[object] = (
        [raw] if isinstance(raw, str) else decoded.as_array(raw, "module-path")
    )
    command = decoded.as_str(tool.get("test-command"), "test-command")
    return paths, command, decoded.as_array(leg.get("tests"), f"[{LEG_TABLE}] tests")


def adoption(config: Path) -> Adoption | None:
    """Read the adoption file, or name what it lacks.

    Returns:
        The module paths, the test command and the test pathspecs; None
        with each refusal printed.

    """
    try:
        read = read_adoption(config.read_bytes())
    except (OSError, decoded.UndecodableError) as error:
        red(f"{config}: {error}")
        return None
    paths, command, tests = read
    if not decoded.is_strings(paths) or not paths:
        red(f"{config}: module-path names no paths")
        return None
    if not decoded.is_strings(tests) or not tests:
        red(f"{config}: [{LEG_TABLE}] tests names no test files")
        return None
    return Adoption(paths, command, tests)


def paths(config: Path) -> int:
    """Print `module-path`, one per line.

    Returns:
        0, or EXIT_RED when the adoption file cannot be read.

    """
    found = adoption(config)
    if found is None:
        return EXIT_RED
    for path in found.paths:
        print(path)
    return 0


def pragma_word(comment: str) -> tuple[str, str]:
    """Read what follows `no mutate` in a pragma comment.

    Returns:
        The first word after it (`block`, `start`, `end`, `--` or none),
        and the whole rest of the comment.

    """
    rest = comment.split("no mutate", 1)[1] if "no mutate" in comment else comment
    words = rest.lstrip(": ").split()
    return (words[0] if words else ""), rest


@dataclass
class Pragmas:
    """The pragma comments, read."""

    # (file, line) of each reasoned line-form pragma.
    reasoned: set[tuple[str, int]] = field(default_factory=set)
    # `file:line` and why, for each one refused.
    refused: list[str] = field(default_factory=list)


def read_pragmas(text: str) -> Pragmas:
    """Hold each `# pragma: no mutate` to the line form and a reason.

    Returns:
        The reasoned sites, and each refused one with its reason.

    """
    found = Pragmas()
    for line in text.split("\n"):
        parts = line.split("\t", 2)
        if not parts[0]:
            continue
        file, number, comment = [*parts, "", ""][:3]
        word, rest = pragma_word(comment)
        site = f"{file}:{number}"
        if word in {"block", "start", "end"}:
            found.refused.append(
                f"{site}: # pragma: no mutate {word} — only the line form"
                " exists; mark each line"
            )
        elif " -- " not in rest or not rest.split(" -- ", 1)[1].strip():
            found.refused.append(
                f"{site}: # pragma: no mutate with no reason — write it after ' -- '"
            )
        elif number.isdigit():
            found.reasoned.add((file, int(number)))
    return found


def pragmas(pragma_file: Path) -> int:
    """Refuse a pragma that is not the line form or has no reason.

    Returns:
        0, or EXIT_RED with each refusal printed.

    """
    found = read_pragmas(pragma_file.read_text(encoding="utf-8"))
    for refusal in found.refused:
        red(refusal)
    if found.refused:
        print(
            "  an exclusion records a mutant the suite cannot kill;"
            " say why at the site",
            file=sys.stderr,
        )
        return EXIT_RED
    return 0


@dataclass(frozen=True, order=True)
class Mutant:
    """One mutant, in the plan's sort order."""

    path: str
    operator: str
    occurrence: int
    line: int
    function: str
    # The line coverage reports for the statement holding `line`.
    statement: int = 0


def read_dump(text: str) -> list[Mutant]:
    """Read `cosmic-ray dump`: one JSON line per work item.

    Returns:
        Each mutant, sorted by (path, operator, occurrence).

    Raises:
        UndecodableError: a line is not a work item of one mutation.

    """
    mutants = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"dump line {number}"
        pair = decoded.as_array(decoded.load_json(line), where)
        item = decoded.as_object(pair[0] if pair else None, where)
        mutations = decoded.as_array(item.get("mutations"), where)
        if len(mutations) != 1:
            msg = f"{where} holds {len(mutations)} mutations, not one"
            raise decoded.UndecodableError(msg)
        spec = decoded.as_object(mutations[0], where)
        start = decoded.as_array(spec.get("start_pos"), f"{where} start_pos")
        name = spec.get("definition_name")
        mutants.append(
            Mutant(
                path=decoded.as_str(spec.get("module_path"), where),
                operator=decoded.as_str(spec.get("operator_name"), where),
                occurrence=decoded.as_int(spec.get("occurrence"), where),
                line=decoded.as_int(start[0] if start else None, where),
                function=name if isinstance(name, str) else "",
            )
        )
    return sorted(mutants)


def statement_lines(source: str) -> list[tuple[int, int]]:
    """List every statement's span, for finding a line's statement.

    Returns:
        (first line, last line) of each statement; none when the source
        does not parse.

    """
    try:
        tree = ast.parse(source)
    except SyntaxError, ValueError:
        return []
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
    ]


def statement_of(spans: list[tuple[int, int]], line: int) -> int:
    """Name the line coverage reports for the statement holding a line.

    Coverage reports a statement at its FIRST line, so a mutant on a
    continuation line of a call or a literal would read as a line no
    test executes (measured on the canon's squash_message.py: 12 of its
    52 mutants). The innermost statement holding the line is the one.

    Returns:
        That statement's first line; the line itself when none holds it.

    """
    holding = [first for first, last in spans if first <= line <= last]
    return max(holding, default=line)


def with_statements(mutants: list[Mutant]) -> list[Mutant]:
    """Give each mutant the line of the statement that holds it.

    Returns:
        The mutants, in the same order, each with `statement` set.

    """
    spans: dict[str, list[tuple[int, int]]] = {}
    placed = []
    for m in mutants:
        if m.path not in spans:
            try:
                text = Path(m.path).read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                text = ""
            spans[m.path] = statement_lines(text)
        placed.append(
            Mutant(
                m.path,
                m.operator,
                m.occurrence,
                m.line,
                m.function,
                statement_of(spans[m.path], m.line),
            )
        )
    return placed


def test_argv(template: str, test: str) -> list[str]:
    """Fill `test-command` for one test file.

    Returns:
        The argv, `{test}`, `{dir}` and `{name}` filled in each word.

    """
    where = Path(test)
    fill = {"test": test, "dir": str(where.parent), "name": where.name}
    argv = []
    for word in shlex.split(template):
        filled = word
        for key, value in fill.items():
            filled = filled.replace("{" + key + "}", value)
        argv.append(filled)
    return argv


def git_files(*pathspecs: str) -> list[str]:
    """List the tracked files the pathspecs match, git's meaning.

    Returns:
        The paths, sorted.

    """
    listed = git("ls-files", "-z", "--", *pathspecs).stdout
    return sorted(p for p in listed.split("\0") if p)


def dirty(module_paths: list[str]) -> list[str]:
    """Name the tracked files under the module paths that differ from git.

    Returns:
        Each path, sorted.

    """
    status = git(
        "status", "--porcelain=v1", "-z", "--untracked-files=no", "--", *module_paths
    ).stdout
    return sorted(
        entry[STATUS_PREFIX:]
        for entry in status.split("\0")
        if len(entry) > STATUS_PREFIX
    )


def coverage_site() -> str | None:
    """Find the directory coverage's package is imported from.

    The tool venv's interpreter must be the one the suite runs on: the
    tracer is a compiled extension. The belt's one check asks it
    (tool_python.py, #1788).

    Returns:
        The directory, or None with the refusal printed.

    """
    return tool_python.check(LABEL, "coverage", "coverage")


# One rc file per test file, all writing into one data directory: the
# test file's index is its STATIC CONTEXT, so one `combine` and one
# `json --show-contexts` read every file's lines at the end.
RC = """[run]
parallel = true
data_file = {data}
context = {context}
source = {root}
relative_files = true
disable_warnings = no-data-collected, module-not-measured
"""


@dataclass
class Measured:
    """One test file, run alone under coverage."""

    test: str
    millis: int
    status: int
    output: str


def write_rc(plan: Path, context: str) -> Path:
    """Write one coverage rc file into the plan's data directory.

    Returns:
        Its path.

    """
    where = plan / "coverage"
    where.mkdir(parents=True, exist_ok=True)
    rc = where / f"{context or 'report'}.rc"
    rc.write_text(
        RC.format(data=where / ".coverage", context=context, root=Path.cwd()),
        encoding="utf-8",
    )
    return rc


def executed(plan: Path) -> dict[str, dict[str, list[int]]]:
    """Read which test files executed which lines, child processes included.

    Returns:
        path -> line (as text) -> the indices of the test files that
        executed it; empty when no test wrote data.

    """
    rc = write_rc(plan, "")
    out = plan / "coverage" / "lines.json"
    # ruff: ignore[subprocess-without-shell-equals-true]
    combined = subprocess.run(
        [which("coverage"), "combine", f"--rcfile={rc}", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    if combined.returncode != 0:
        return {}
    # ruff: ignore[subprocess-without-shell-equals-true]
    subprocess.run(
        [
            which("coverage"),
            "json",
            f"--rcfile={rc}",
            "--show-contexts",
            "-q",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = decoded.as_object(decoded.load_json(out.read_bytes()), "report")
        files = decoded.as_object(report.get("files"), "files")
    except OSError, decoded.UndecodableError:
        return {}
    lines: dict[str, dict[str, list[int]]] = {}
    for path, entry in files.items():
        contexts = decoded.as_object(
            decoded.as_object(entry, path).get("contexts", {}), path
        )
        lines[path] = {
            line: sorted(
                int(c)
                for c in decoded.as_array(names, path)
                if isinstance(c, str) and c.isdigit()
            )
            for line, names in contexts.items()
        }
    return lines


def measure(index: int, test: str, template: str, plan: Path) -> Measured:
    """Run one test file alone, timed and under coverage.

    Returns:
        Its time, exit status and output.

    """
    rc = write_rc(plan, str(index))
    hook = str(plan / "hook")
    inherited = os.environ.get("PYTHONPATH", "")
    env = os.environ | {
        "COVERAGE_PROCESS_START": str(rc),
        "PYTHONPATH": f"{hook}{os.pathsep}{inherited}" if inherited else hook,
    }
    start = time.monotonic()
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        test_argv(template, test),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    millis = int((time.monotonic() - start) * 1000)
    return Measured(test, millis, done.returncode, done.stdout + done.stderr)


def leaked(mutants: list[Mutant], tests: list[str]) -> list[str]:
    """Name each test file cosmic-ray would mutate.

    A test file is what judges a mutant, never what is judged; one in
    the list is an exclusion `excluded-modules` missed (its `**` skips
    dot-directories).

    Returns:
        Each such file, once, sorted.

    """
    named = set(tests)
    return sorted({m.path for m in mutants if m.path in named})


def plan_mutants(config: Path, plan: Path, found: Adoption) -> list[Mutant] | None:
    """List every mutant of the tracked files under `module-path`.

    Returns:
        The sorted mutants, or None with the refusal printed.

    """
    session = plan / "session.sqlite"
    # ruff: ignore[subprocess-without-shell-equals-true]
    init = subprocess.run(
        [which("cosmic-ray"), "init", str(config), str(session)],
        capture_output=True,
        text=True,
        check=False,
    )
    # ruff: ignore[subprocess-without-shell-equals-true]
    dump = subprocess.run(
        [which("cosmic-ray"), "dump", str(session)],
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0 or dump.returncode != 0:
        red("cosmic-ray could not list the mutants (init, dump):")
        print((init.stderr + dump.stderr).rstrip(), file=sys.stderr)
        return None
    try:
        listed = read_dump(dump.stdout)
    except decoded.UndecodableError as error:
        red(f"cosmic-ray dump: {error}")
        return None
    tracked = set(git_files(*found.paths))
    return with_statements([m for m in listed if m.path in tracked])


def refused_dirty(module_paths: list[str]) -> bool:
    """Refuse a tracked tree under `module-path` that differs from git.

    Returns:
        True with each file named, when it differs.

    """
    changed = dirty(module_paths)
    if changed:
        red("the tracked tree differs from git under module-path — refused:")
        for path in changed:
            print(f"  {path}", file=sys.stderr)
        print(
            "  the leg mutates these files in place and restores them from git",
            file=sys.stderr,
        )
    return bool(changed)


def refused_leaked(mutants: list[Mutant], tests: list[str]) -> bool:
    """Refuse a mutant in a test file, each file named.

    Returns:
        True when one is in the list.

    """
    found = leaked(mutants, tests)
    for path in found:
        red(
            f"{path}: a test file is mutated — exclude it in excluded-modules"
            " (a `**` skips dot-directories)"
        )
    return bool(found)


def measure_all(
    tests: list[str], command: str, plan_dir: Path
) -> list[Measured] | None:
    """Run every test file alone under coverage's start-up hook.

    Returns:
        Each file's measurement, or None with every failing file named.

    """
    site = coverage_site()
    if site is None:
        return None
    startup_hook.write(
        plan_dir / "hook",
        f"import sys\nsys.path.append({site!r})\n"
        "import coverage\ncoverage.process_startup()\n",
    )
    runs = [measure(i, test, command, plan_dir) for i, test in enumerate(tests)]
    failed = [run for run in runs if run.status != 0]
    for run in failed:
        red(f"{run.test} fails alone (exit {run.status}) — nothing judged")
        for line in run.output.splitlines()[-TAIL_LINES:]:
            print(f"    {line}", file=sys.stderr)
    return None if failed else runs


def plan(config: Path, plan_dir: Path) -> int:
    """Write the plan: the mutants, each test file's time and lines.

    Returns:
        0 with the plan written, or EXIT_RED with each refusal printed.

    """
    found = adoption(config)
    if found is None or refused_dirty(found.paths):
        return EXIT_RED
    plan_dir.mkdir(parents=True, exist_ok=True)
    mutants = plan_mutants(config, plan_dir, found)
    if mutants is None:
        return EXIT_RED
    tests = git_files(*found.tests)
    if refused_leaked(mutants, tests):
        return EXIT_RED
    runs = measure_all(tests, found.command, plan_dir)
    if runs is None:
        return EXIT_RED
    write_plan(plan_dir, found, mutants, runs)
    seconds = sum(run.millis for run in runs) / 1000
    print(
        f"{LABEL}: plan: {len(mutants)} mutants, {len(runs)} test files"
        f" ({seconds:.1f} s alone)"
    )
    return 0


def write_plan(
    plan_dir: Path, found: Adoption, mutants: list[Mutant], runs: list[Measured]
) -> None:
    """Write plan.json: the commit, the mutants, the tests and the line map."""
    mutated = {m.path for m in mutants}
    lines = {p: v for p, v in executed(plan_dir).items() if p in mutated}
    document = {
        "head": git("rev-parse", "HEAD").stdout.strip(),
        "command": found.command,
        "paths": found.paths,
        "mutants": [
            [m.path, m.operator, m.occurrence, m.line, m.function, m.statement]
            for m in mutants
        ],
        "tests": [[run.test, run.millis] for run in runs],
        "lines": lines,
    }
    (plan_dir / "plan.json").write_text(to_json(document), encoding="utf-8")


def to_json(value: object) -> str:
    """Serialise one document the leg writes, keys sorted.

    Returns:
        The text, newline-terminated.

    """
    return json.dumps(value, sort_keys=True) + "\n"


@dataclass
class Plan:
    """plan.json, read."""

    head: str
    command: str
    mutants: list[Mutant]
    # `module-path`: what is checked against git after every mutant.
    paths: list[str]
    # (test file, planned milliseconds), by index.
    tests: list[tuple[str, int]]
    # (path, line) -> the indices of the test files that execute it.
    lines: dict[tuple[str, int], list[int]]


def read_plan(text: str) -> Plan:
    """Read plan.json.

    Returns:
        The plan.

    Raises:
        UndecodableError: it is not one this leg wrote.

    """
    document = decoded.as_object(decoded.load_json(text), "plan")
    mutants = []
    for row in decoded.as_array(document.get("mutants"), "mutants"):
        path, operator, occurrence, line, function, statement = ([
            *decoded.as_array(row, "a mutant"),
            *[None] * 6,
        ])[:6]
        mutants.append(
            Mutant(
                decoded.as_str(path, "a mutant's path"),
                decoded.as_str(operator, "a mutant's operator"),
                decoded.as_int(occurrence, "a mutant's occurrence"),
                decoded.as_int(line, "a mutant's line"),
                decoded.as_str(function, "a mutant's function"),
                decoded.as_int(statement, "a mutant's statement"),
            )
        )
    tests = []
    for row in decoded.as_array(document.get("tests"), "tests"):
        test, millis = ([*decoded.as_array(row, "a test"), None, None])[:2]
        tests.append((
            decoded.as_str(test, "a test's file"),
            decoded.as_int(millis, "a time"),
        ))
    lines = {}
    for path, per_file in decoded.as_object(document.get("lines"), "lines").items():
        for line, indices in decoded.as_object(per_file, path).items():
            if not line.isdigit():
                msg = f"{path}: line {line!r} is not a number"
                raise decoded.UndecodableError(msg)
            lines[path, int(line)] = [
                decoded.as_int(i, "a test index")
                for i in decoded.as_array(indices, path)
            ]
    return Plan(
        decoded.as_str(document.get("head"), "head"),
        decoded.as_str(document.get("command"), "command"),
        sorted(mutants),
        [
            decoded.as_str(path, "a module path")
            for path in decoded.as_array(document.get("paths"), "paths")
        ],
        tests,
        lines,
    )


def parse_shard(text: str) -> tuple[int, int] | None:
    """Read `k/N`.

    Returns:
        (k, N) with 0 <= k < N, or None.

    """
    k, slash, n = text.partition("/")
    if not slash or not k.isdigit() or not n.isdigit():
        return None
    if int(n) < 1 or int(k) >= int(n):
        return None
    return int(k), int(n)


def shard_of(mutants: list[Mutant], k: int, n: int) -> list[Mutant]:
    """Select the mutants whose position in the sorted list is k modulo n.

    Returns:
        This shard's mutants, in order.

    """
    return [m for position, m in enumerate(mutants) if position % n == k]


def tests_for(the_plan: Plan, mutant: Mutant) -> list[tuple[str, int]]:
    """Name the test files that execute a mutant's first line.

    A line is executed when coverage reports its statement executed.

    Returns:
        (test file, planned milliseconds), fastest first.

    """
    indices = the_plan.lines.get((mutant.path, mutant.statement), [])
    return sorted(
        (the_plan.tests[i] for i in set(indices)), key=operator.itemgetter(1, 0)
    )


def limit_seconds(millis: int) -> float:
    """Bound one test file's run under a mutant.

    Returns:
        Three times its planned time, and never under ten seconds.

    """
    return max(float(FLOOR_SECONDS), LIMIT_FACTOR * millis / 1000)


# The runner cosmic-ray starts as the test command. It is written into
# scratch before any mutant exists, so a mutant of a helper never runs
# it: the leg judges the canon, and the canon includes this file. Each
# test file runs in turn, stopping at the first failure; one that runs
# over its limit is killed with its process group, and that is a kill.
# Each also starts under a process ceiling, the user's tasks now plus
# MARGIN (#1833): a mutant that fans out without end gets a failed
# fork, its test fails, and that is a kill, where unbounded it took
# the runner and its shard down. Linux counts every thread of the real
# user against RLIMIT_NPROC, macOS every process (measured: the kernel
# admits about 20 more than ps lists); root is not bound on Linux.
RUNNER = """import json
import os
import resource
import signal
import subprocess
import sys

# Headroom over what the user runs, far above the property runner's
# fan-out of min(cores, files).
MARGIN = 256


def tasks():
    \"\"\"Count what RLIMIT_NPROC counts for this user now.\"\"\"
    uid = os.getuid()
    if not os.path.isdir("/proc/self/task"):
        listed = subprocess.run(
            ["ps", "-U", str(uid), "-o", "pid="],
            capture_output=True,
            text=True,
            check=True,
        )
        return len(listed.stdout.split())
    count = 0
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as status:
                fields = dict(line.split(":", 1) for line in status)
        except OSError:
            continue
        if int(fields["Uid"].split()[0]) == uid:
            count += int(fields["Threads"])
    return count


def ceiling(extra):
    \"\"\"Cap the user's tasks at their count now plus EXTRA, for a child.\"\"\"
    cap = tasks() + extra

    def apply():
        hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
        if hard != resource.RLIM_INFINITY:
            low = min(cap, hard)
        else:
            low = cap
        resource.setrlimit(resource.RLIMIT_NPROC, (low, low))

    return apply


def main():
    with open(sys.argv[1], encoding="utf-8") as handle:
        steps = json.load(handle)
    for step in steps:
        proc = subprocess.Popen(
            step["argv"], start_new_session=True, preexec_fn=ceiling(MARGIN)
        )
        try:
            status = proc.wait(timeout=step["limit"])
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
            print("time limit: " + step["test"])
            sys.exit(1)
        if status != 0:
            print("failed: " + step["test"])
            sys.exit(1)


if __name__ == "__main__":
    main()
"""


def uncache(module: str) -> None:
    """Remove a module's own cached bytecode, beside it.

    Python serves a `.pyc` when the source's size and its mtime in
    SECONDS match: a mutant the size of the last one, written in the
    same second, would run the cached code (#1771, 9 of 13 survivors
    wrong in the first scratch run).
    """
    path = Path(module)
    for cached in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


@dataclass
class Judged:
    """One mutant's outcome."""

    mutant: Mutant
    outcome: str
    diff: str = ""
    # The tracked files under `module-path` that differed from git after
    # it, each restored (#1832).
    restored: list[str] = field(default_factory=list)


def mutate_and_test(mutant: Mutant, command: str) -> tuple[str, str]:
    """Apply one mutant with cosmic-ray, run the command, restore it.

    Returns:
        The outcome (a test outcome when the worker ended normally, or
        the worker's own) and cosmic-ray's diff.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [
            which("cosmic-ray"),
            "mutate-and-test",
            mutant.path,
            mutant.operator,
            str(mutant.occurrence),
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        result = decoded.as_object(decoded.load_json(done.stdout), "result")
    except decoded.UndecodableError:
        return f"cosmic-ray exited {done.returncode}", ""
    worker = result.get("worker_outcome")
    test = result.get("test_outcome")
    diff = result.get("diff")
    diff_text = diff if isinstance(diff, str) else ""
    if worker != "normal":
        return str(worker), diff_text
    return str(test), diff_text


def judge_one(the_plan: Plan, mutant: Mutant, scratch: Path, index: int) -> Judged:
    """Judge one mutant against the test files that execute its line.

    Returns:
        What it came to.

    """
    tests = tests_for(the_plan, mutant)
    if not tests:
        return Judged(mutant, NO_TESTS)
    steps = [
        {
            "test": test,
            "argv": test_argv(the_plan.command, test),
            "limit": limit_seconds(millis),
        }
        for test, millis in tests
    ]
    runs = scratch / f"{index}.json"
    runs.write_text(to_json(steps), encoding="utf-8")
    command = shlex.join([sys.executable, str(scratch / "run.py"), str(runs)])
    uncache(mutant.path)
    outcome, diff = mutate_and_test(mutant, command)
    # The whole tree under `module-path`, not only the mutated file: a
    # mutant's tests can write another tracked file, and every later
    # mutant would be judged against it (#1832).
    restored = dirty(the_plan.paths)
    if restored:
        git("restore", "--", *restored)
    uncache(mutant.path)
    return Judged(mutant, outcome, diff, restored)


def source_line(path: str, line: int) -> str:
    """Read one line of a tracked file.

    Returns:
        The line, stripped; empty when it cannot be read.

    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError, UnicodeDecodeError:
        return ""
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def where(mutant: Mutant) -> str:
    """Spell a mutant for a message.

    Returns:
        `file:line: operator #occurrence (function)`.

    """
    name = f" ({mutant.function})" if mutant.function else ""
    return f"{mutant.path}:{mutant.line}: {mutant.operator} #{mutant.occurrence}{name}"


def hunk(diff: str) -> list[str]:
    """Keep the changed hunk of cosmic-ray's diff.

    Returns:
        The diff's lines after its headers.

    """
    return [
        line
        for line in diff.splitlines()
        if line and not line.startswith(("--- ", "+++ "))
    ]


def report(judged: list[Judged], excluded: int, shard: str) -> int:
    """Print the survivors where they are, the summary, and the verdict.

    Returns:
        0 when green, EXIT_RED for a survivor, a mutant never judged or
        a file left changed. The counts are written whenever every
        mutant reached an outcome: a restored file is red by name but
        withholds nothing (#1832).

    """
    killed = [j for j in judged if j.outcome in KILLED]
    survivors = [j for j in judged if j.outcome in {"survived", NO_TESTS}]
    unjudged = [j for j in judged if j not in killed and j not in survivors]
    for j in survivors:
        if j.outcome == NO_TESTS:
            text = source_line(j.mutant.path, j.mutant.line)
            print(f"{LABEL}: {NO_TESTS}: {where(j.mutant)}: {text}")
        else:
            print(f"{LABEL}: survived: {where(j.mutant)}")
            for line in hunk(j.diff):
                print(f"    {line}")
    print(
        f"{LABEL}: {len(judged)} mutants, {len(killed)} killed,"
        f" {len(survivors)} survived, {excluded} excluded"
    )
    for j in unjudged:
        red(f"never judged: {where(j.mutant)}: worker outcome '{j.outcome}'")
    for j in judged:
        for path in j.restored:
            red(f"{path} differed from git after {where(j.mutant)} — restored")
    if unjudged:
        return EXIT_RED
    summary_dir = os.environ.get("MUTANTS_SUMMARY_DIR", "")
    if summary_dir:
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "python.tsv").write_text(
            f"{shard}\t{len(judged)}\t{len(killed)}\t{len(survivors)}\t{excluded}\n",
            encoding="utf-8",
        )
    status = EXIT_RED if any(j.restored for j in judged) else 0
    if survivors:
        red("red — kill each survivor with an assertion, or exclude it at its site")
        print(
            "  with '# pragma: no mutate -- <why the mutant is equivalent>'",
            file=sys.stderr,
        )
        return EXIT_RED
    return status


def judge(plan_dir: Path, pragma_file: Path, shard: str, scratch: Path) -> int:
    """Judge this shard's mutants, one at a time, and state the verdict.

    Returns:
        The leg's exit status.

    """
    parsed = parse_shard(shard)
    if parsed is None:
        red(f"MUTANTS_SHARD='{shard}' — expected k/N with 0 <= k < N, or plan")
        return EXIT_RED
    try:
        the_plan = read_plan((plan_dir / "plan.json").read_text(encoding="utf-8"))
    except (OSError, decoded.UndecodableError) as error:
        red(f"no plan to judge from in {plan_dir}: {error}")
        return EXIT_RED
    head = git("rev-parse", "HEAD").stdout.strip()
    if head != the_plan.head:
        red(f"the plan is of {the_plan.head}, but this checkout is {head}")
        return EXIT_RED
    reasoned = read_pragmas(pragma_file.read_text(encoding="utf-8")).reasoned
    excluded = {m for m in the_plan.mutants if (m.path, m.line) in reasoned}
    selected = [m for m in shard_of(the_plan.mutants, *parsed) if m not in excluded]
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "run.py").write_text(RUNNER, encoding="utf-8")
    judged = []
    for index, mutant in enumerate(selected, start=1):
        judged.append(judge_one(the_plan, mutant, scratch, index))
        if index % PROGRESS_EVERY == 0:
            print(f"{LABEL}: judged {index} of {len(selected)}", file=sys.stderr)
    return report(judged, len(excluded), shard)


class Args(argparse.Namespace):
    """The subcommand and its arguments, each typed as parsed."""

    command: str
    config: Path
    pragmas: Path
    plan: Path
    shard: str
    scratch: Path


def parse_args(argv: list[str]) -> Args:
    """Read the subcommand and its arguments.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("paths").add_argument("config", type=Path)
    sub.add_parser("pragmas").add_argument("pragmas", type=Path)
    planned = sub.add_parser("plan")
    planned.add_argument("config", type=Path)
    planned.add_argument("plan", type=Path)
    judged = sub.add_parser("judge")
    judged.add_argument("plan", type=Path)
    judged.add_argument("pragmas", type=Path)
    judged.add_argument("shard")
    judged.add_argument("scratch", type=Path)
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str]) -> int:
    """Run one step.

    Returns:
        The step's exit status.

    """
    args = parse_args(argv)
    if args.command == "paths":
        return paths(args.config)
    if args.command == "pragmas":
        return pragmas(args.pragmas)
    if args.command == "plan":
        return plan(args.config, args.plan)
    return judge(args.plan, args.pragmas, args.shard, args.scratch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

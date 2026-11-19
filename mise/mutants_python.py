#!/usr/bin/env python3
"""The reading and judging half of audit:mutants' Python leg (#1354, #1486).

mutants-python.sh runs the commands: git, mutmut_pragmas.py's tokenizer,
`mutmut run`, `mutmut results`, `mutmut show`. Everything that reads or
decides is here, one subcommand per step the shell takes, so that it
comes under the canon's coverage figure and its own mutation run
(CONTRIBUTING.md, "A helper's language", #1352):

- `exclusions CONFIG PRAGMAS`: the adoption config's own section
  (`[tool.mutmut]` in pyproject.toml, `[mutmut]` in setup.cfg) holds
  every `do_not_mutate`, `do_not_mutate_patterns` and `only_mutate`
  entry to a reason line above it and refuses
  `mutate_only_covered_lines = true`; every `# pragma: no mutate` the
  tokenizer found carries its reason after ` -- ` (`end` needs none).
  Prints the count of reasoned sites and entries, or each refusal.
  Read as TEXT, never with tomllib or configparser: the rule is about
  the comment above an entry, and both parsers drop comments.
- `hook DIR`: writes DIR/sitecustomize.py, which APPENDS mutmut's own
  site directory to `sys.path` in every Python the run starts (#1354
  item 3). mutmut's mutated copies import its trampoline, so a test
  that runs a helper as a child `python3` died with "No module named
  'mutmut'" on the unmutated baseline (canon self-mutants run
  44213905549). Appended, never prepended, so the suite's own imports
  resolve as they do unmutated: the coverage leg's hook, the same way.
  The directory is derived from the installed tool, by asking the
  interpreter its `mutmut` script names where `mutmut` lives; that
  interpreter must be the suite's own Python version, or the children
  would load another build's compiled modules.
  The hook also carries the directory the leg runs `mutmut run` in (the
  directory `hook` is run from), and in a child started under a mutant
  (`MUTANT_UNDER_TEST` set) it loads mutmut's configuration there, once,
  through mutmut's own `config()` (#1542): every mutated function calls
  it from the trampoline, it reads the CURRENT directory, and a child a
  test started in a scratch directory found no configuration and died
  in the statistics pass (self-mutants run 44298570855). A hook that
  cannot load it fails the child by name, never silently.
- `survivors RESULTS`: the mutants `mutmut results --all true` calls
  survived or "no tests", one name per line, for the shell to `show`.
- `verdict RESULTS SHOWS`: maps every result state to killed, survived
  or never judged, locates each survivor's FILE line from its `mutmut
  show` diff (numbered from the function, not the file), prints them
  and the summary line, writes `python.tsv`, and returns the leg's exit
  status.

Stdlib only. Every message is the one the shell printed before #1486,
byte for byte: test_mutants_python.py's stub rows hold them.
"""

import argparse
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from dataclasses import dataclass, field
from pathlib import Path

import startup_hook

LABEL = "audit:mutants: python"
EXIT_RED = 1
KILLED = frozenset({"killed", "timeout", "caught by type check", "segfault"})
SURVIVED = frozenset({"survived", "no tests"})
SECTIONS = {"pyproject.toml": "[tool.mutmut]", "setup.cfg": "[mutmut]"}
REFUSED = "mutate_only_covered_lines = true drops every line no test runs"

# awk's [[:space:]] on one line: never a newline, always these.
SPACE = " \t\r\f\v"
S = f"[{SPACE}]"
REFUSED_LINE = re.compile(
    rf"^{S}*mutate_only_covered_lines{S}*={S}*(true|True|1|t){S}*$"
)
KEY_LINE = re.compile(rf"^{S}*(do_not_mutate|do_not_mutate_patterns|only_mutate){S}*=")
AFTER_EQUALS = re.compile(rf"^[^=]*={S}*")
OPEN_BRACKET = re.compile(rf"^\[{S}*")
QUOTED = re.compile(rf"^{S}*[\"']")
CLOSING = re.compile(rf"^{S}*\]")
CLOSED_AT_END = re.compile(rf"\]{S}*$")
COMMENT = re.compile(rf"^{S}*#")
INI_COMMENT = re.compile(rf"^{S}*[#;]")
INDENTED = re.compile(rf"^{S}")
BLANK = re.compile(rf"^{S}*$")
NOT_BLANK = re.compile(rf"[^{SPACE}]")
TRAILING_COMMA = re.compile(rf",{S}*$")
REASON = re.compile(rf" -- +[^{SPACE}\n]")
RESULT = re.compile(rf"^{S}*([^{SPACE}]+):{S}+(.+)$")
DEF = r"^{s}*(async{s}+)?def{s}+{name}{s}*\("


def lines_of(text: str) -> list[str]:
    """Split text into lines as awk does: on newline only.

    Returns:
        The lines, without a last empty one after a final newline.

    """
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    return lines


def entry_text(line: str) -> str:
    """Spell a list entry as the message names it.

    Returns:
        The line without its indentation or its trailing comma.

    """
    return TRAILING_COMMA.sub("", line.lstrip(SPACE))


# The list a line can be inside: none, a TOML list, a setup.cfg value.
NO_LIST, TOML_LIST, CFG_LIST = 0, 1, 2


@dataclass
class Scan:
    """What the config's section says about exclusions."""

    findings: list[tuple[int, str, str]] = field(default_factory=list)
    listed: int = 0
    # The reading state, line to line.
    inside: bool = False
    mode: int = NO_LIST
    key: str = ""
    prev: str = ""

    def entry(self, number: int, line: str, *, reasoned: bool) -> None:
        """Count a reasoned entry, or name an unreasoned one."""
        if reasoned:
            self.listed += 1
        else:
            self.findings.append((number, self.key, entry_text(line)))


def key_line(found: Scan, number: int, key: str, line: str) -> None:
    """Open a list: a TOML `key = [`, or a setup.cfg `key =`.

    An entry on the key's own line has nowhere to put a reason.
    """
    found.key = key
    rest = AFTER_EQUALS.sub("", line, count=1)
    if rest.startswith("["):
        if QUOTED.match(OPEN_BRACKET.sub("", rest, count=1)):
            found.findings.append((number, key, "an entry on the opening line"))
        found.mode = NO_LIST if "]" in rest else TOML_LIST
    else:
        if NOT_BLANK.search(rest):
            found.findings.append((number, key, "an entry on the opening line"))
        found.mode = CFG_LIST
    found.prev = ""


def toml_line(found: Scan, number: int, line: str) -> None:
    """Read one line inside a TOML list: quoted entries, `#` reasons."""
    if CLOSING.match(line):
        found.mode = NO_LIST
        return
    if QUOTED.match(line):
        found.entry(number, line, reasoned=bool(COMMENT.match(found.prev)))
        if CLOSED_AT_END.search(line):
            found.mode = NO_LIST
    if not BLANK.match(line):
        found.prev = line


def cfg_line(found: Scan, number: int, line: str) -> None:
    """Read one line of a setup.cfg value: indented entries, `#`/`;` reasons."""
    if not INDENTED.match(line) or BLANK.match(line):
        found.mode = NO_LIST
        return
    if not INI_COMMENT.match(line):
        found.entry(number, line, reasoned=bool(INI_COMMENT.match(found.prev)))
    found.prev = line


def scan(text: str, section: str) -> Scan:
    """Read the config's own section, from its header to the next header.

    A TOML list carries quoted entries, one per line under `key = [`;
    setup.cfg carries one indented entry per line under `key =`. Each
    entry needs a comment line (`#`, or `;` in setup.cfg) directly
    above it; blank lines between are allowed in a TOML list. The rules
    apply in the order the shell's awk applied them, so a key or the
    refused setting inside a list is read as such.

    Returns:
        (line, kind, text) for each finding — kind is the key, or
        "refused" — and the count of entries that carry a reason.

    """
    found = Scan()
    for number, line in enumerate(lines_of(text), start=1):
        if line.startswith("["):
            found.inside = line.startswith(section)
            found.mode = NO_LIST
        elif not found.inside:
            continue
        elif REFUSED_LINE.match(line):
            found.findings.append((number, "refused", REFUSED))
        elif match := KEY_LINE.match(line):
            key_line(found, number, match.group(1), line)
        elif found.mode == TOML_LIST:
            toml_line(found, number, line)
        elif found.mode == CFG_LIST:
            cfg_line(found, number, line)
    return found


def pragma_word(comment: str) -> tuple[str, str]:
    """Read what follows `no mutate` in a pragma comment.

    Returns:
        The first word after it (`end`, `block`, `start`, or none), and
        the whole rest of the comment.

    """
    rest = comment.split("no mutate", 1)[1] if "no mutate" in comment else comment
    words = rest.lstrip(": ").split()
    return (words[0] if words else ""), rest


def pragmas(text: str) -> tuple[int, list[str]]:
    """Hold each `# pragma: no mutate` the tokenizer found to its reason.

    Returns:
        The count of reasoned sites, and `file:line` for each bare one.

    """
    sites = 0
    bare = []
    for line in lines_of(text):
        parts = line.split("\t", 2)
        if not parts[0]:
            continue
        file, number, comment = [*parts, "", ""][:3]
        word, rest = pragma_word(comment)
        if word == "end":
            continue
        if REASON.search(rest):
            sites += 1
        else:
            bare.append(f"{file}:{number}")
    return sites, bare


def exclusions(config: Path, pragma_file: Path) -> int:
    """Check every exclusion's reason; print the excluded count.

    EXCLUDED COUNTS SITES, not mutants: mutmut never generates an
    excluded mutant, so what there is to count is the reasoned pragmas
    and list entries.

    Returns:
        0 with the count printed, 1 with each refusal printed.

    """
    text = config.read_text(encoding="utf-8", errors="replace")
    found = scan(text, SECTIONS[config.name])
    sites, bare = pragmas(pragma_file.read_text(encoding="utf-8"))
    for number, kind, what in found.findings:
        if kind == "refused":
            print(f"{LABEL}: {config}:{number}: {what}", file=sys.stderr)
        else:
            print(
                f"{LABEL}: {config}:{number}: {kind} entry {what}"
                " has no # reason line above it",
                file=sys.stderr,
            )
    for site in bare:
        print(
            f"{LABEL}: {site}: # pragma: no mutate with no reason"
            " — write it after ' -- '",
            file=sys.stderr,
        )
    if found.findings or bare:
        print(
            "  an exclusion records a mutant the suite cannot kill;"
            " say why at the site",
            file=sys.stderr,
        )
        return EXIT_RED
    print(sites + found.listed)
    return 0


@dataclass
class Results:
    """`mutmut results --all true`, classed."""

    total: int = 0
    killed: int = 0
    unjudged: int = 0
    # (state, mutant name) for each survivor, in mutmut's order.
    survivors: list[tuple[str, str]] = field(default_factory=list)


def read_results(text: str) -> Results:
    """Class every mutant by its state.

    killed, timeout, caught by type check and segfault are killed (the
    suite noticed); survived and "no tests" are survivors — a mutant no
    test reaches is the least explained of all; anything else
    (suspicious, skipped, not checked, interrupted) was never judged.

    Returns:
        The tally and the survivors.

    """
    results = Results()
    for line in lines_of(text):
        match = RESULT.match(line)
        if not match:
            continue
        name, state = match.groups()
        results.total += 1
        if state in KILLED:
            results.killed += 1
        elif state in SURVIVED:
            results.survivors.append((state, name))
        else:
            results.unjudged += 1
    return results


def function_of(name: str) -> str:
    """Name the function a mutant mutates.

    mutmut 3.8.1 names a mutant `<module>.x_<function>__mutmut_<n>`, or
    `<module>.xǁ<Class>ǁ<method>__mutmut_<n>` for a method.

    Returns:
        The function's own name.

    """
    func = name.rsplit(".", 1)[-1]
    if "__mutmut_" in func:
        func = func[: func.rindex("__mutmut_")]
    # A method's own name follows the last ǁ, whole: only a function's
    # name carries the `x_` prefix (#1555: method `x_offset` read as
    # `offset`, so its survivor was never located).
    if "ǁ" in func:
        return func.rsplit("ǁ", 1)[-1]
    return func.removeprefix("x_")


@dataclass
class Shown:
    """What one `mutmut show` diff says."""

    file: str
    removed: str
    added: str


def read_show(text: str) -> Shown:
    """Read the file and the first removed and added lines of a diff.

    Returns:
        The `---` header's path and the two lines, without their marks.

    """
    file = removed = added = None
    for line in lines_of(text):
        if file is None and line.startswith("--- "):
            words = line.split()
            file = words[1] if len(words) > 1 else ""
        elif removed is None and len(line) > 1 and line[0] == "-" and line[1] != "-":
            removed = line[1:]
        elif added is None and len(line) > 1 and line[0] == "+" and line[1] != "+":
            added = line[1:]
    return Shown(file or "", removed or "", added or "")


def locate(source: str, func: str, removed: str) -> int:
    """Find a survivor's line in its file.

    `mutmut show` diffs the mutated FUNCTION on its own (mutmut 3.8.1,
    diff_apply.get_diff_for_mutant): its hunk counts lines from the
    function's first line. So the line is found in the file: the
    function's `def`, then the first line at or after it that reads as
    the diff's removed line, whitespace aside.

    Returns:
        The 1-based line; the `def` line when no line reads as the
        removed one; 0 when there is no `def` either.

    """
    want = removed.strip(SPACE)
    pattern = re.compile(DEF.format(s=S, name=re.escape(func)))
    start = 0
    for number, line in enumerate(lines_of(source), start=1):
        if not start and pattern.match(line):
            start = number
        if start and line.strip(SPACE) == want:
            return number
    return start


# uv writes a long interpreter path as a `/bin/sh` re-exec, whose second
# line names the interpreter in quotes.
SH_REEXEC = re.compile(r"^'''exec' \"?([^\"\s]+)\"?")
# The answer is two lines: the site directory, then sys.version.
WHERE_LINES = 2
WHERE = (
    "import mutmut, os, sys; "
    "print(os.path.dirname(os.path.dirname(mutmut.__file__))); "
    "print(sys.version)"
)


# The hook's second half (#1542): under a mutant, a child loads mutmut's
# configuration where the run loaded it, then goes back where it was.
# os._exit, not sys.exit: a SystemExit out of sitecustomize is a fatal
# "Failed to import the site module" with a traceback, not a message.
CHILD_CONFIG = """import os
if os.environ.get("MUTANT_UNDER_TEST"):
    here = os.getcwd()
    try:
        os.chdir(RUN)
        import mutmut.configuration
        mutmut.configuration.config()
    except Exception as error:
        sys.stderr.write(REFUSED + str(error) + "\\n")
        sys.stderr.flush()
        os._exit(1)
    finally:
        os.chdir(here)
"""


def tool_interpreter(script: str) -> str:
    """Read the interpreter an installed console script runs on.

    Returns:
        The interpreter path, or "" when the script names none.

    """
    with Path(script).open(encoding="utf-8", errors="replace") as handle:
        first = handle.readline().strip()
        second = handle.readline().strip()
    if first.startswith("#!/bin/sh"):
        found = SH_REEXEC.match(second)
        return found.group(1) if found else ""
    named = first[2:].split()[0] if first.startswith("#!") and first[2:] else ""
    return named if "python" in Path(named).name else ""


def hook(directory: Path) -> int:
    """Write the start-up hook that lets child processes import mutmut.

    Returns:
        0 when written; EXIT_RED, named, when the tool cannot be read.

    """
    script = shutil.which("mutmut")
    interpreter = tool_interpreter(script) if script else ""
    if not interpreter:
        print(
            f"{LABEL}: cannot read mutmut's own interpreter from"
            f" {script or 'PATH'} (its first line names none)",
            file=sys.stderr,
        )
        return EXIT_RED
    # ruff: ignore[subprocess-without-shell-equals-true]
    asked = subprocess.run(
        [interpreter, "-c", WHERE],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = asked.stdout.splitlines()
    if asked.returncode != 0 or len(lines) < WHERE_LINES:
        print(
            f"{LABEL}: mutmut's interpreter {interpreter} could not say where"
            " mutmut lives",
            file=sys.stderr,
        )
        return EXIT_RED
    site, version = lines[0], "\n".join(lines[1:])
    if version != sys.version:
        print(
            f"{LABEL}: mutmut runs on '{version}' but the suite's python3 is"
            f" '{sys.version}' — the tool venv must be built on the belt's"
            " python pin (reinstall: mise install -f pipx:mutmut)",
            file=sys.stderr,
        )
        return EXIT_RED
    run = str(Path.cwd())
    refused = (
        f"{LABEL}: a child's start-up hook cannot load mutmut's configuration"
        f" in {run}: "
    )
    # One writer for every belt hook, which then runs the next one on the
    # path (#1542): a coverage measurement nested inside the mutated suite
    # puts its own hook first, and Python imports only the first.
    startup_hook.write(
        directory,
        f"import sys\nsys.path.append({site!r})\n"
        + CHILD_CONFIG.replace("RUN", repr(run)).replace("REFUSED", repr(refused)),
    )
    return 0


def survivors(results_file: Path) -> int:
    """Print the survivors' names, one per line, for the shell to show.

    Returns:
        0.

    """
    for _, name in read_results(results_file.read_text(encoding="utf-8")).survivors:
        print(name)
    return 0


def verdict(results_file: Path, shows: Path, excluded: int) -> int:
    """Print the survivors where they are, the summary, and the verdict.

    Returns:
        0 when green, 1 for a survivor or a mutant never judged.

    """
    results = read_results(results_file.read_text(encoding="utf-8"))
    for index, (state, name) in enumerate(results.survivors, start=1):
        shown = read_show((shows / f"{index}.diff").read_text(encoding="utf-8"))
        try:
            source = (
                Path(shown.file).read_text(encoding="utf-8") if shown.file else None
            )
        except OSError:
            source = None
        line = (
            locate(source, function_of(name), shown.removed)
            if source is not None
            else 0
        )
        added = shown.added.lstrip(SPACE)
        print(f"{LABEL}: {state}: {shown.file}:{line}: {name}: {added}")
    survived = len(results.survivors)
    print(
        f"{LABEL}: {results.total} mutants, {results.killed} killed,"
        f" {survived} survived, {excluded} excluded (sites)"
    )
    if results.unjudged:
        print(
            f"{LABEL}: {results.unjudged} mutant(s) never judged"
            " (suspicious, skipped, not checked or interrupted)",
            file=sys.stderr,
        )
        return EXIT_RED
    summary_dir = os.environ.get("MUTANTS_SUMMARY_DIR", "")
    if summary_dir:
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        Path(summary_dir, "python.tsv").write_text(
            f"0/1\t{results.total}\t{results.killed}\t{survived}\t{excluded}\n",
            encoding="utf-8",
        )
    if survived:
        print(
            f"{LABEL}: red — kill each survivor with an assertion,"
            " or exclude it at its site",
            file=sys.stderr,
        )
        print(
            "  with '# pragma: no mutate -- <why the mutant is equivalent>'",
            file=sys.stderr,
        )
        return EXIT_RED
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Read the subcommand and its arguments.

    Returns:
        The parsed arguments.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    checked = sub.add_parser("exclusions")
    checked.add_argument("config", type=Path)
    checked.add_argument("pragmas", type=Path)
    sub.add_parser("hook").add_argument("directory", type=Path)
    sub.add_parser("survivors").add_argument("results", type=Path)
    judged = sub.add_parser("verdict")
    judged.add_argument("results", type=Path)
    judged.add_argument("shows", type=Path)
    judged.add_argument("--excluded", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one step.

    Returns:
        The step's exit status.

    """
    args = parse_args(argv)
    if args.command == "exclusions":
        return exclusions(args.config, args.pragmas)
    if args.command == "hook":
        return hook(args.directory)
    if args.command == "survivors":
        return survivors(args.results)
    return verdict(args.results, args.shows, args.excluded)


if __name__ == "__main__":
    sys.exit(main())

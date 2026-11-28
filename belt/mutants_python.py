"""The Python leg of `audit:mutants` (#1354, #2177): would the suite fail?

mutmut mutates a copy of the repository's package in `mutants/`, keeps
its own map of which tests reach which function from one baseline run,
and judges each mutant by those tests alone. A mutant the tests still
pass is a SURVIVOR, and a survivor is a named function to fix. The
measure is no unexplained survivor, never a score (#1336): delete code
it proves redundant, kill it with an assertion, or (the code must stay
and no input can tell) exclude its line with a reason at the site.

ADOPTION IS MUTMUT'S OWN CONFIGURATION, tracked at the repository's
root: a `[mutmut]` section in `setup.cfg`, or `[tool.mutmut]` in
`pyproject.toml` (mutmut reads the second when it is there). Without
one the leg skips clean, by name; with both it is red, since mutmut
would silently read one. The section holds only the repository's own
facts, ALLOWED: what is mutated (`source_paths`) and the tests that
judge it (`pytest_add_cli_args_test_selection`). Every other key is the
belt's policy and red by name, a key a later mutmut adds included,
until it is weighed. mutmut 3.8.1's defaults are its maximum
(`mutate_only_covered_lines` false, `max_stack_depth` -1, nothing
excluded, no `type_check_command`).

The belt adds what is not the repository's: pytest reads the belt's
own `mise/pytest.toml` (`-c`), and the copy holds every tracked
top-level path, so a test reads the tree it reads in the gate. mutmut
runs in a process of its own, which imports nothing of the package
before the mutated copy is on its path.

EVERY EXCLUSION CARRIES ITS REASON, the line form only
(belt/mutation_pragmas.py): a pragma with no reason, or a region form
(`block`, `start`, `end`), is red before anything runs.

THE VERDICT. A survivor is red by its module and function; a mutant no
test reaches in process (`no tests`) is red by name, since code only a
child process runs is the shape #2177 removed; a timeout is red; so is
every other outcome that is not a kill (`suspicious`, `segfault`, `not
checked`). The counts are written for the totals job.

SHARDS: MUTANTS_SHARD=k/n (read as ORG_SHARD is, belt/shard.py) takes every n-th of the population's modules,
sorted, from the k-th, and runs mutmut on those modules' mutants; each
shard pays the baseline run once. Unset is 1/1, every module.

    belt mutants-python adopted      `adopted=true|false`, for GITHUB_OUTPUT
    belt mutants-python              run the leg
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path

from belt import findings, mutation_pragmas, shard, tool_python
from belt.tracked import has, tracked
from belt.tree import MISE

LABEL = "audit:mutants: python"
SHARD = "MUTANTS_SHARD"
ALLOWED = ("source_paths", "pytest_add_cli_args_test_selection")
SETUP = "setup.cfg"
PYPROJECT = "pyproject.toml"
COPY = "mutants"
KILLED = "killed"
EXIT_RED = 1
EXIT_USAGE = 2
# One result line of `mutmut results --all true`.
RESULT = re.compile(r"\s*(\S+): (.+)")
# A mutant's name: its module, its mangled function, its number.
MUTANT = re.compile(r"(?P<module>.+)\.x(?P<function>[^.]+)__mutmut_(?P<n>[0-9]+)")

# Run in mutmut's own process: the belt's policy added to the
# repository's configuration, then mutmut's command line.
BOOTSTRAP = """\
import json
import sys
from dataclasses import replace
from pathlib import Path

from mutmut import configuration

loaded = configuration._load_config()
configuration._config = replace(
    loaded,
    pytest_add_cli_args=["-c", sys.argv[1], "-p", "no:cacheprovider"],
    also_copy=[*loaded.also_copy, *(Path(p) for p in json.loads(sys.argv[2]))],
)
from mutmut.__main__ import cli

sys.argv = ["mutmut", *sys.argv[3:]]
cli()
"""


class AdoptionError(ValueError):
    """The repository's mutmut configuration cannot be read as the leg needs."""


def configuration(root: Path) -> tuple[str, dict[str, str]] | None:
    """Read the repository's tracked mutmut configuration.

    Returns:
        The file and its keys (each value as text), or None when the
        repository tracks none.

    Raises:
        AdoptionError: both files hold one, or one does not parse.

    """
    found: list[tuple[str, dict[str, str]]] = []
    if has(PYPROJECT, root=root):
        try:
            data = tomllib.loads((root / PYPROJECT).read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as bad:
            msg = f"{PYPROJECT} does not parse: {bad}"
            raise AdoptionError(msg) from bad
        table = data.get("tool", {})
        section = table.get("mutmut") if isinstance(table, dict) else None
        if isinstance(section, dict):
            found.append((PYPROJECT, {str(k): str(v) for k, v in section.items()}))
    if has(SETUP, root=root):
        parser = configparser.ConfigParser()
        try:
            parser.read_string((root / SETUP).read_text(encoding="utf-8"))
        except (OSError, configparser.Error) as bad:
            msg = f"{SETUP} does not parse: {bad}"
            raise AdoptionError(msg) from bad
        if parser.has_section("mutmut"):
            found.append((SETUP, dict(parser.items("mutmut"))))
    if len(found) > 1:
        msg = (
            f"both {PYPROJECT} [tool.mutmut] and {SETUP} [mutmut] are tracked;"
            " mutmut reads only the first, so keep one"
        )
        raise AdoptionError(msg)
    return found[0] if found else None


def refused_keys(keys: dict[str, str]) -> list[str]:
    """Name every key outside the allow-list.

    Returns:
        The refused keys, sorted.

    """
    return sorted(k for k in keys if k not in ALLOWED)


def source_paths(keys: dict[str, str]) -> list[str]:
    """Read what the repository mutates.

    Returns:
        Each source path, as mutmut reads the list (one per line).

    """
    raw = keys.get("source_paths", "")
    words = raw.strip("[]").replace(",", "\n").split("\n")
    return [w.strip().strip("'\"") for w in words if w.strip().strip("'\"")]


def module_of(path: str) -> str:
    """Name the module a file under a source path is.

    Returns:
        Its dotted name; a package's `__init__` is the package.

    """
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def population(root: Path, sources: list[str]) -> list[str]:
    """List the modules mutmut mutates, sorted.

    Returns:
        The dotted name of every tracked `.py` under the source paths.

    """
    files = tracked(*(f"{s.rstrip('/')}/*.py" for s in sources), root=root)
    return sorted({module_of(f) for f in files})


def patterns(modules: list[str]) -> list[str]:
    """Name each module's mutants as mutmut matches them.

    A mutant is `<module>.x<mangled function>__mutmut_<n>`, so the
    pattern reaches one module's functions and none of a submodule's.

    Returns:
        One pattern per module.

    """
    return [f"{m}.x*" for m in modules]


def function_of(name: str) -> tuple[str, str]:
    """Split a mutant's name into its module and its function.

    Returns:
        The module and the function, a method as `Class.method`.

    """
    found = MUTANT.fullmatch(name)
    if found is None:
        return name, ""
    function = found["function"].lstrip("_ǁ").replace("ǁ", ".")
    return found["module"], function


def results(text: str, modules: list[str]) -> dict[str, str]:
    """Read `mutmut results --all true` for this shard's modules.

    Returns:
        Each mutant's status, by name.

    """
    out: dict[str, str] = {}
    mine = set(modules)
    for line in text.splitlines():
        found = RESULT.fullmatch(line)
        if found is None:
            continue
        name, status = found[1], found[2].strip()
        if function_of(name)[0] in mine:
            out[name] = status
    return out


def verdict(judged: dict[str, str]) -> list[tuple[str, str, list[str]]]:
    """Judge the shard's mutants.

    Returns:
        One finding per refused outcome: its subject, message and the
        mutants it names, survivors by module and function.

    """
    out: list[tuple[str, str, list[str]]] = []
    survivors: dict[str, list[str]] = defaultdict(list)
    others: dict[str, list[str]] = defaultdict(list)
    for name, status in sorted(judged.items()):
        if status == KILLED:
            continue
        if status == "survived":
            module, function = function_of(name)
            survivors[f"{module}.{function}"].append(name)
        else:
            others[status].append(name)
    out += [
        (where, f"{len(names)} mutant(s) survived", names)
        for where, names in sorted(survivors.items())
    ]
    why = {
        "no tests": "no test reaches them in process",
        "timeout": "their tests ran past the time limit",
    }
    for status, names in sorted(others.items()):
        reason = why.get(status, "an outcome that is not a kill")
        out.append(("", f"{len(names)} mutant(s) {status}: {reason}", names))
    return out


def summary(where: str, row: str) -> None:
    """Write the shard's counts for the totals job, where it reads them."""
    folder = os.environ.get("MUTANTS_SUMMARY_DIR", "")
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "python.tsv").write_text(f"{where}\t{row}\n", encoding="utf-8")


def adopted(root: Path) -> int:
    """Print whether the repository adopts the leg, for GITHUB_OUTPUT.

    Returns:
        0, or 1 when its configuration cannot be read.

    """
    try:
        found = configuration(root)
    except AdoptionError as bad:
        findings.finding("python", str(bad))
        return EXIT_RED
    print(f"adopted={'true' if found else 'false'}")
    return 0


def mutmut(venv: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run mutmut, with the belt's policy, in a process of its own.

    Returns:
        The finished process; `results` is read from its output.

    """
    copied = sorted({p.split("/", 1)[0] for p in tracked(root=Path.cwd())} - {COPY})
    return subprocess.run(
        [
            venv,
            "-c",
            BOOTSTRAP,
            str(MISE / "pytest.toml"),
            json.dumps(copied),
            *args,
        ],
        capture_output=args[0] == "results",
        text=True,
        check=False,
    )


def run(root: Path, where: str) -> int:
    """Run the leg on this shard.

    Returns:
        0 when every mutant of the shard was killed, 1 otherwise.

    """
    try:
        found = configuration(root)
    except AdoptionError as bad:
        findings.finding("python", str(bad))
        return EXIT_RED
    if found is None:
        print(f"{LABEL}: no mutmut configuration tracked ({SETUP} [mutmut]), skipped")
        summary(where, "skipped")
        return 0
    name, keys = found
    refused = refused_keys(keys)
    if refused:
        findings.finding(
            "python",
            f"{name} [mutmut] holds keys that are the belt's policy, not the"
            " repository's facts:",
            [*refused, f"a repository names only {', '.join(ALLOWED)}"],
        )
        return EXIT_RED
    sources = source_paths(keys)
    if not sources:
        findings.finding("python", f"{name} [mutmut] names no source_paths")
        return EXIT_RED
    files = tracked(*(f"{s.rstrip('/')}/*.py" for s in sources), root=root)
    if mutation_pragmas.main(files) != 0:
        return EXIT_RED
    if tracked(f"{COPY}/", root=root):
        findings.finding("python", f"{COPY}/ is tracked; mutmut writes its copy there")
        return EXIT_RED
    venv = tool_python.check(LABEL, "pytest")
    if venv is None:
        return EXIT_RED
    every = population(root, sources)
    k, n = (int(x) for x in where.split("/"))
    mine = every[k - 1 :: n]
    shard.report("mutmut", " ".join(sources), every, mine)
    if not mine:
        print(f"{LABEL}: shard {where} holds none of the {len(every)} module(s)")
        summary(where, "0\t0\t0\t0\tshard")
        return 0
    shutil.rmtree(root / COPY, ignore_errors=True)
    cores = str(os.cpu_count() or 1)
    done = mutmut(venv, "run", "--max-children", cores, *patterns(mine))
    if done.returncode != 0:
        findings.finding("python", f"mutmut run exited {done.returncode}; see above")
        return EXIT_RED
    listed = mutmut(venv, "results", "--all", "true")
    judged = results(listed.stdout, mine)
    if not judged:
        findings.finding("python", "mutmut judged no mutant in this shard's modules")
        return EXIT_RED
    killed = sum(1 for s in judged.values() if s == KILLED)
    summary(where, f"{len(judged)}\t{killed}\t{len(judged) - killed}\t0\tshard")
    red = verdict(judged)
    for subject, message, names in red:
        findings.finding(subject, message, names)
    print(f"{LABEL}: {len(judged)} mutant(s), {killed} killed, shard {where}")
    return EXIT_RED if red else 0


def main(argv: list[str]) -> int:
    """Run the leg, or say whether the repository adopts it.

    Returns:
        The leg's status; 2 for a usage error.

    """
    root = Path.cwd()
    if argv == ["adopted"]:
        return adopted(root)
    if argv:
        findings.finding("", "usage: belt mutants-python [adopted]")
        return EXIT_USAGE
    try:
        named = shard.parse(os.environ.get(SHARD, ""))
    except shard.ShardError as bad:
        findings.finding("python", f"{SHARD}: {bad}")
        return EXIT_USAGE
    k, n = named or (1, 1)
    return run(root, f"{k}/{n}")


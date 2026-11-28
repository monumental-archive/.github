"""Hand a test command's share to its runner, in the runner's terms (#2160).

    belt shard-runner -- <command> [<argument>…]

`measured` runs this first, whenever ORG_SHARD names a shard other than
1/1 (mise/shard.py). It reads the command the repository's `test` task
gives `measured`, prints it back NUL-separated with this shard's share
in it, and writes the shard's report (what it chose from, what it kept)
for the gate's union check. Four runners, each in its own terms:

    vitest    `--shard=k/n` on the run; the population and the share
              are vitest's own `list --filesOnly`, with the run's own
              arguments, without and with it. vitest's contract
              (measured on 5.1.1, #2160): k from 1; files sorted by the
              sha1 of their root-relative path and cut in contiguous
              slices, the first N mod n one file longer; k < 1, k > n
              and n above the file count refused, exit 1 (without
              --passWithNoTests), so no shard is ever empty.
    nextest   `--partition slice:k/n` on `cargo nextest run`. nextest's
              contract (its partitioning docs; `slice:` since 0.9.127,
              `count:` deprecated): k from 1; every test across every
              binary, after the filters, dealt round-robin. The
              population is `cargo nextest list`'s matching tests, in
              its order (binary, then test name); the share is dealt
              here the same way, since the listing does not mark a
              partition. A shard dealt none runs `true`: nextest's
              default --no-tests=auto is exit 4.
    pytest    no shard of its own (#2177): a run is `pytest …` or
              `<python> -m pytest …`. The population is the test files
              its own arguments collect (`--collect-only -q`, rooted at
              the working directory, so a file is named as the command
              names it), sorted; the share is every n-th from the k-th,
              passed as the run's paths in place of the ones it named.
              A shard holding none runs `true`: pytest's exit for
              nothing collected is 5.
    go test   no shard of its own: the command's packages (`go list`
              over its patterns, `.` where it names none; import paths,
              sorted), every n-th from the k-th; the patterns are
              replaced by this shard's packages, and a shard holding
              none runs `true` (a bare `go test` would test `.`).

A command with none of the four goes unchanged: `measured` also runs
a task's setup lines, and the belt's property runners read ORG_SHARD
themselves. The plan refuses a
count above 1 on a task whose command names no adapter before any
runner starts, and the gate's union check reds a sharded task none of
whose shards reported a share, so a whole suite run on every shard is
red either way.

stdlib only.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from belt import decoded, findings, shard
from belt.coverage_js_runner import LAUNCHERS, SUBCOMMANDS

if TYPE_CHECKING:
    from collections.abc import Sequence

# go test's flags that take a value as the next argument (`go help
# testflag`, `go help build`): the word after one is not a package.
GO_VALUED = frozenset({
    "-run",
    "-skip",
    "-bench",
    "-benchtime",
    "-count",
    "-cpu",
    "-list",
    "-parallel",
    "-timeout",
    "-shuffle",
    "-fuzz",
    "-fuzztime",
    "-fuzzminimizetime",
    "-coverpkg",
    "-covermode",
    "-coverprofile",
    "-cpuprofile",
    "-memprofile",
    "-blockprofile",
    "-mutexprofile",
    "-trace",
    "-outputdir",
    "-o",
    "-exec",
    "-vet",
    "-p",
    "-tags",
    "-mod",
    "-modfile",
    "-ldflags",
    "-gcflags",
    "-asmflags",
    "-pkgdir",
    "-toolexec",
    "-overlay",
    "-pgo",
    "-C",
    "-buildmode",
    "-compiler",
    "-gccgoflags",
    "-installsuffix",
})


# pytest's options that take a value as the next argument (`pytest
# --help`, 9.2.0, and pytest-xdist's): the word after one is not a path.
PYTEST_VALUED = frozenset({
    "-c",
    "-k",
    "-m",
    "-n",
    "-o",
    "-p",
    "-W",
    "--basetemp",
    "--confcutdir",
    "--deselect",
    "--dist",
    "--durations",
    "--durations-min",
    "--ignore",
    "--ignore-glob",
    "--maxfail",
    "--maxprocesses",
    "--numprocesses",
    "--rootdir",
    "--tb",
})
# A file's line in pytest's `-qq` collection listing: `<file>: <count>`.
COUNTED = re.compile(r"(.+\.py): [0-9]+")
# `<python> -m pytest`: the interpreter, the flag and the module.
PYTHON_M_PYTEST = 3
# The interpreters a `<python> -m pytest` run may start with.
PYTHONS = re.compile(r"python(3(\.[0-9]+)?)?")

# `--` and at least one word of the command.
MIN_ARGS = 2


class NoAdapterError(Exception):
    """A runner's listing failed, so its share cannot be named."""


def run_out(argv: list[str]) -> str:
    """Run a listing command for its standard output.

    Returns:
        Its output.

    Raises:
        NoAdapterError: it failed, naming the command.

    """
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        # pytest reports a collection error on its standard output.
        sys.stderr.write(done.stdout)
        sys.stderr.write(done.stderr)
        msg = (
            f"`{' '.join(argv)}` failed (exit {done.returncode}),"
            " so the share cannot be listed"
        )
        raise NoAdapterError(msg)
    return done.stdout


def vitest(command: Sequence[str], k: int, n: int) -> list[str] | None:
    """Give a vitest run its share.

    Returns:
        The command with `--shard=k/n`, or None when it is not a vitest run.

    """
    at = next((i for i, w in enumerate(command) if Path(w).name == "vitest"), None)
    if at is None or tuple(command[:at]) not in LAUNCHERS:
        return None
    rest = list(command[at + 1 :])
    if rest and rest[0] in SUBCOMMANDS:
        return None
    if rest and rest[0] == "run":
        rest = rest[1:]
    head = list(command[: at + 1])
    listing = [*head, "list", "--filesOnly", *rest]
    every = run_out(listing).splitlines()
    mine = run_out([*listing, f"--shard={k}/{n}"]).splitlines()
    shard.report("vitest", " ".join(rest), every, mine)
    return [*head, "run", *rest, f"--shard={k}/{n}"]


def nextest(command: Sequence[str], k: int, n: int) -> list[str] | None:
    """Give a `cargo nextest run` its partition.

    Returns:
        The command with `--partition slice:k/n`, `true` for a shard
        dealt no test, or None when it is not one.

    """
    words = list(command)
    at = next(
        (
            i
            for i in range(len(words) - 2)
            if Path(words[i]).name == "cargo"
            and words[i + 1] == "nextest"
            and words[i + 2] == "run"
        ),
        None,
    )
    if at is None:
        return None
    head, rest = words[: at + 2], words[at + 3 :]
    listing = [*head, "list", *rest, "--message-format", "json"]
    every = matching(run_out(listing))
    mine = every[k - 1 :: n]
    shard.report("nextest", " ".join(rest), every, mine)
    if not mine:
        return ["true"]
    return [*head, "run", *rest, "--partition", f"slice:{k}/{n}"]


def matching(listing: str) -> list[str]:
    """Read the tests a `cargo nextest list --message-format json` selects.

    Returns:
        `<binary-id> <test>` for every test whose filter matches, by
        binary id, then test name: the order nextest deals in.

    """
    doc = decoded.as_object(decoded.load_json(listing), "nextest list")
    suites = decoded.as_object(doc.get("rust-suites"), "rust-suites")
    out = []
    for binary, suite in sorted(suites.items()):
        cases = decoded.as_object(
            decoded.as_object(suite, binary).get("testcases"), "testcases"
        )
        for name, case in sorted(cases.items()):
            match = decoded.as_object(
                decoded.as_object(case, name).get("filter-match"), "filter-match"
            )
            if match.get("status") == "matches":
                out.append(f"{binary} {name}")
    return out


def go_test(command: Sequence[str], k: int, n: int) -> list[str] | None:
    """Give a `go test` every n-th of its packages, from the k-th.

    Returns:
        The command with its patterns replaced by this shard's packages,
        `true` for a shard that holds none, or None when it is not one.

    """
    words = list(command)
    at = next(
        (
            i
            for i in range(len(words) - 1)
            if Path(words[i]).name == "go" and words[i + 1] == "test"
        ),
        None,
    )
    if at is None:
        return None
    rest = words[at + 2 :]
    end = rest.index("-args") if "-args" in rest else len(rest)
    flags: list[str] = []
    patterns: list[str] = []
    skip = False
    for word in rest[:end]:
        if skip:
            flags.append(word)
            skip = False
        elif word.startswith("-"):
            flags.append(word)
            skip = "=" not in word and "-" + word.lstrip("-") in GO_VALUED
        else:
            patterns.append(word)
    tags = [w for w in flags if w.startswith(("-tags", "--tags"))]
    every = sorted(
        run_out([*words[: at + 1], "list", *tags, *(patterns or ["."])]).split()
    )
    mine = every[k - 1 :: n]
    shard.report("go test", " ".join(patterns or ["."]), every, mine)
    if not mine:
        return ["true"]
    return [*words[: at + 2], *flags, *mine, *rest[end:]]


def listed(line: str) -> str:
    """Read the test file one line of pytest's `--collect-only` listing names.

    The listing is `<file>::<test>` per test, or `<file>: <count>` per
    file once the run's own `-q` makes it `-qq` (pytest 9.2.0).

    Returns:
        The file, or "" for a line that names none.

    """
    if line.startswith(" "):
        return ""
    if "::" in line:
        return line.split("::", 1)[0]
    counted = COUNTED.fullmatch(line)
    return counted.group(1) if counted else ""


def pytest(command: Sequence[str], k: int, n: int) -> list[str] | None:
    """Give a pytest run every n-th of its test files, from the k-th.

    Returns:
        The command with its paths replaced by this shard's files,
        `true` for a shard that holds none, or None when it is not one.

    """
    words = list(command)
    if words and Path(words[0]).name == "pytest":
        head, rest = words[:1], words[1:]
    elif (
        len(words) >= PYTHON_M_PYTEST
        and PYTHONS.fullmatch(Path(words[0]).name)
        and words[1:3] == ["-m", "pytest"]
    ):
        head, rest = words[:3], words[3:]
    else:
        return None
    options: list[str] = []
    paths: list[str] = []
    skip = False
    for word in rest:
        if skip:
            options.append(word)
            skip = False
        elif word.startswith("-"):
            options.append(word)
            skip = "=" not in word and word in PYTEST_VALUED
        else:
            paths.append(word)
    root = ["--rootdir", str(Path.cwd())]
    listing = [*head, *options, *paths, "--collect-only", "-q", *root]
    files = {listed(line) for line in run_out(listing).splitlines()} - {""}
    every = sorted(files)
    mine = every[k - 1 :: n]
    shard.report("pytest", " ".join(paths), every, mine)
    if not mine:
        return ["true"]
    return [*head, *options, *mine]


def share(command: Sequence[str], k: int, n: int) -> list[str]:
    """Hand the command's runner this shard.

    Returns:
        The command with its share, or as given when no adapter names it.

    """
    for adapt in (vitest, nextest, pytest, go_test):
        got = adapt(command, k, n)
        if got is not None:
            return got
    return list(command)


def main(argv: list[str]) -> int:
    """Print the command with this shard's share, NUL-separated.

    Returns:
        0, 1 when a runner's share could not be listed, 2 for a bad command line.

    """
    if argv[:1] != ["--"] or len(argv) < MIN_ARGS:
        findings.finding("", "usage: belt shard-runner -- <command> [<argument>…]")
        return 2
    command = argv[1:]
    named = shard.read()
    try:
        out = command if named is None else share(command, *named)
    except NoAdapterError as err:
        findings.finding("", str(err))
        return 1
    sys.stdout.write("".join(f"{w}\0" for w in out))
    return 0

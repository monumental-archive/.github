#!/usr/bin/env python3
"""Hand a test command's share to its runner, in the runner's terms (#2160).

    shard_runner.py -- <command> [<argument>…]

`measured` runs this first, whenever ORG_SHARD names a shard other than
1/1 (mise/shard.py). It reads the command the repository's `test` task
gives `measured`, prints it back NUL-separated with this shard's share
in it, and writes the shard's report (what it chose from, what it kept)
for the gate's union check. Three runners, each in its own terms:

    vitest    `--shard=k/n` on the run; the population and the share
              are vitest's own `list --filesOnly`, without and with it.
    nextest   `--partition count:k/n` on `cargo nextest run`; the
              population and the share are `cargo nextest list`'s
              matching tests, without and with it.
    go test   the command's packages (`go list` over its patterns,
              `.` where it names none), every n-th from the k-th; the
              patterns are replaced by this shard's packages, and a
              shard holding none runs `true`.

A command with none of the three goes unchanged: `measured` also runs
a task's setup lines, and the belt's own runners (unittest_guarded.py,
the property runners) read ORG_SHARD themselves. The plan refuses a
count above 1 on a task whose command names no adapter before any
runner starts, and the gate's union check reds a sharded task none of
whose shards reported a share, so a whole suite run on every shard is
red either way.

stdlib only.
"""

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import decoded
import findings
import shard
from coverage_js_runner import LAUNCHERS, SUBCOMMANDS

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
    filters = [w for w in rest if not w.startswith("-")]
    listing = [*head, "list", "--filesOnly", *filters]
    every = run_out(listing).split()
    mine = run_out([*listing, f"--shard={k}/{n}"]).split()
    shard.report("vitest", " ".join(filters), every, mine)
    return [*head, "run", *rest, f"--shard={k}/{n}"]


def nextest(command: Sequence[str], k: int, n: int) -> list[str] | None:
    """Give a `cargo nextest run` its partition.

    Returns:
        The command with `--partition count:k/n`, or None when it is not one.

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
    mine = matching(run_out([*listing, "--partition", f"count:{k}/{n}"]))
    shard.report("nextest", " ".join(rest), every, mine)
    return [*head, "run", *rest, "--partition", f"count:{k}/{n}"]


def matching(listing: str) -> list[str]:
    """Read the tests a `cargo nextest list --message-format json` selects.

    Returns:
        `<binary-id> <test>` for every test whose filter matches.

    """
    doc = decoded.as_object(decoded.load_json(listing), "nextest list")
    suites = decoded.as_object(doc.get("rust-suites"), "rust-suites")
    out = []
    for binary, suite in suites.items():
        cases = decoded.as_object(
            decoded.as_object(suite, binary).get("testcases"), "testcases"
        )
        for name, case in cases.items():
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


def share(command: Sequence[str], k: int, n: int) -> list[str]:
    """Hand the command's runner this shard.

    Returns:
        The command with its share, or as given when no adapter names it.

    """
    for adapt in (vitest, nextest, go_test):
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
        findings.finding("", "usage: shard_runner.py -- <command> [<argument>…]")
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

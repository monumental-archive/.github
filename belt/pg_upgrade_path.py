"""Judge one pgrx extension's upgrade graph for lint:pg-upgrade-path (#816, #825).

mise/pg-upgrade-path.sh finds each extension, its version and its
upgrade scripts; this is the judgment: every version that owes a path
must reach the current one by some chain of `<name>--<from>--<to>.sql`,
and the current version must itself be an endpoint of the graph.

Which versions owe a path is two questions (#825). A `from` owes one: a
later release was derived from it. A `to`-only version owes one only if
it published, which the graph cannot say; the release path's record
(RECORD, `.pgrx-installable`) can. The record may ACCUSE, never excuse,
and a record that is present is held to the derived-state law: it carries
the derivation that wrote it, and anything else is drift, which reds.

Usage: belt pg-upgrade-path NAME VERSION SQL_DIR SCRIPT..., with RECORD
the record's path.

Exit status: 0 every version reaches VERSION, 1 refused, 2 usage.
"""

import collections
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from belt import findings, workflow_command

if TYPE_CHECKING:
    from collections.abc import Iterable

EXIT_REFUSED = 1
EXIT_USAGE = 2
USAGE = "usage: belt pg-upgrade-path NAME VERSION SQL_DIR SCRIPT..."
# NAME VERSION SQL_DIR, then the scripts.
LEADING_ARGUMENTS = 3


class RefusedError(Exception):
    """The graph or the record is refused; the lines say why."""

    def __init__(self, *lines: str) -> None:
        """Hold the refusal's lines, said in order."""
        super().__init__(lines[0] if lines else "")
        self.lines = lines


class Graph:
    """One extension's upgrade scripts as a graph of versions."""

    def __init__(self, name: str, paths: Iterable[str]) -> None:
        """Read the graph off the scripts' names.

        Raises:
            RefusedError: a script's name is not `<name>--<from>--<to>.sql`.

        """
        pattern = re.compile(r"^" + re.escape(name) + r"--(.+?)--(.+)\.sql$")
        self.edges: dict[str, list[str]] = collections.defaultdict(list[str])
        self.released: set[str] = set()
        self.targets: set[str] = set()
        for path in paths:
            match = pattern.match(Path(path).name)
            if not match:
                said = (
                    f"cannot parse upgrade script name: {Path(path).name}",
                    f"expected {name}--<from>--<to>.sql",
                )
                raise RefusedError(*said)
            self.edges[match.group(1)].append(match.group(2))
            self.released.add(match.group(1))
            self.targets.add(match.group(2))

    def reaches(self, start: str, version: str) -> bool:
        """Say whether `start` gets to `version` by any chain of scripts.

        Returns:
            Whether it does.

        """
        seen: set[str] = set()
        queue = collections.deque([start])
        while queue:
            node = queue.popleft()
            if node == version:
                return True
            if node in seen:
                continue
            seen.add(node)
            queue.extend(self.edges.get(node, ()))
        return False


def read_record(path: str) -> tuple[dict[str, set[str]] | None, str]:
    """Read what the release path last observed at the forge.

    `None` is no record at all: a tree that has not been through a
    Release PR since #825, where the honest answer is that publication
    is unknown.

    Returns:
        The installable versions by extension, and the record's provenance.

    Raises:
        RefusedError: the record does not carry its derivation, or names
            an extension twice.

    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    observed = derived = ""
    by_extension: dict[str, set[str]] = {}
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            key, sep, value = stripped.lstrip("#").strip().partition(":")
            if sep and key.strip() == "observed":
                observed = value.strip()
            elif sep and key.strip() == "derived":
                derived = value.strip()
            continue
        if not stripped:
            continue
        extension, *carried = stripped.split()
        if extension in by_extension:
            problems.append(f"line {lineno}: {extension} is named twice")
            continue
        by_extension[extension] = set(carried)
    if not observed or not derived:
        problems.append("carries no `# observed:` and `# derived:` derivation")
    if problems:
        said = (
            *(f"{path} {problem}" for problem in problems),
            f"{path} is derived state written by the release path;",
            "regenerate it by re-running the Release PR, never by hand",
        )
        raise RefusedError(*said)
    return by_extension, f"{derived}, observed {observed}"


def notes(
    name: str,
    version: str,
    graph: Graph,
    installable: set[str] | None,
    where: tuple[str, str],
) -> list[str]:
    """Say what each `to`-only version is, from the record or as an open question.

    A `to`-only version is the open question this check used to close by
    guessing (#825): never call it burned on the strength of a filename.

    `where` is the record's path and its provenance.

    Returns:
        The lines to print.

    """
    record_path, provenance = where
    lines: list[str] = []
    for dead in sorted(v for v in graph.targets - graph.released if v != version):
        if installable is None:
            lines += [
                f"NOTE: {name} {dead} is a target nothing was derived from; no",
                f"      {record_path} in this tree says whether it published, and",
                "      if it did, installations on it are stranded",
            ]
        elif dead not in installable:
            lines += [
                f"NOTE: {name} {dead} is a target nothing was derived from, and no",
                f"      non-draft release carried its tarballs ({provenance}) —",
                f"      nothing can be installed on it; need not reach {version}",
            ]
    return lines


def stranded(
    name: str,
    version: str,
    graph: Graph,
    installable: set[str] | None,
    provenance: str,
) -> list[str]:
    """Name every version that owes a path and has none.

    A `from` owes a path whatever the record says about it: dropping the
    requirement on the strength of a later observation would let a
    deleted asset retire a real obligation. Only the versions the RECORD
    put on the hook are explained; a `from` is on it for a reason the
    graph already shows.

    Returns:
        The refusal's lines; none when every version reaches `version`.

    """
    owes = set(graph.released)
    if installable is not None:
        owes |= installable & graph.targets
    lost = sorted(v for v in owes if v != version and not graph.reaches(v, version))
    lines: list[str] = []
    for accused in lost:
        if (
            installable is not None
            and accused in installable
            and accused not in graph.released
        ):
            lines += [
                f"{name} {accused} is a target nothing was derived from,",
                f"but a non-draft release carries its tarballs ({provenance})",
                "— it published, so installations on it are stranded",
            ]
    if lost:
        lines.append(
            f"no ALTER EXTENSION UPDATE path to {version} from: {' '.join(lost)}"
        )
    return lines


def judge(argv: list[str], record_path: str) -> None:
    """Judge one extension's graph, printing what it found.

    Raises:
        RefusedError: a version is stranded, or the current version is no
            endpoint of the graph.

    """
    name, version, sql_dir, *paths = argv
    graph = Graph(name, paths)
    record, provenance = read_record(record_path)
    # None when the record does not exist, and also when it does not
    # name THIS extension (a crate added since the last release): nobody
    # has asked the forge about these versions.
    installable = None if record is None else record.get(name)
    for line in notes(name, version, graph, installable, (record_path, provenance)):
        print(line)
    lost = stranded(name, version, graph, installable, provenance)
    if lost:
        said = (
            *lost,
            f"add {sql_dir}/{name}--<from>--<to>.sql to connect them",
            "an empty file is correct when the SQL surface did not change",
        )
        raise RefusedError(*said)
    # The current version must itself be an endpoint: a release that
    # derived no upgrade script (#816, a burned predecessor) strands
    # every installation.
    if version not in graph.released | graph.targets:
        said = (
            f"{name} {version} is not an endpoint of the upgrade graph",
            f"this release derived no upgrade script, so nothing can reach {version}",
            f"expected {sql_dir}/{name}--<previous>--{version}.sql",
        )
        raise RefusedError(*said)
    # Claim exactly what was checked: without the record, nothing about
    # publication was seen.
    if installable is None:
        workflow_command.notice(
            f"every {name} version a later release was derived from"
        )
        workflow_command.notice(
            f"reaches {version}; no {record_path} here, so whether a"
        )
        workflow_command.notice("target-only version published was not checked")
    else:
        workflow_command.notice(f"every installable {name} version reaches {version}")
        workflow_command.notice(f"installable set recorded {provenance}")
    for path in sorted(paths):
        print(f"ok  {Path(path).name}")


def main(argv: list[str]) -> int:
    """Run the judgment.

    Returns:
        The exit status: 0 every version reaches the current one, 1
        refused, 2 usage.

    """
    if len(argv) < LEADING_ARGUMENTS:
        findings.finding("", USAGE)
        return EXIT_USAGE
    try:
        judge(argv, os.environ["RECORD"])
    except RefusedError as refused:
        for line in refused.lines:
            workflow_command.error(line)
        return EXIT_REFUSED
    return 0

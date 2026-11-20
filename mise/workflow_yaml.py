#!/usr/bin/env python3
"""Read a workflow or an action through yq, the belt's one YAML parser (#1840).

Every law that reads a workflow reads it here. Before this module each
test file carried its own reader, a guess at the grammar made line by
line from indentation, and each guess was wrong somewhere: a `needs:`
list read as steps, a single-quoted `run:` kept its quotes, and a step
whose block header carried a comment was read by no law (#1837). yq
is pinned in the belt (`mise/config.toml`), so every reader parses the
same YAML the same way, and a file it cannot parse is refused by name,
never read as empty.

- `document(path)` is the file as yq decodes it, JSON-shaped.
- `jobs(document)` is its `jobs` mapping, in file order.
- `steps(job)` is a job's `steps`, each the mapping YAML holds.
- `runs(path)` is one record per `run:` value: where it is, how it was
  written, and the text bash receives after folding and chomping.

stdlib plus the yq binary on PATH; a sibling root reaches it by
appending the `mise` root to `sys.path`, as it reaches `hermetic`.
"""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
from dataclasses import dataclass
from typing import TYPE_CHECKING

import decoded

if TYPE_CHECKING:
    from pathlib import Path

YQ = shutil.which("yq") or "yq"

# Every mapping holding a scalar `run` value, wherever it sits: a
# workflow's step, a composite action's step, in block or flow style,
# under a quoted key or through an alias. A `run` holding a mapping is
# `defaults: run:`, not a command, and is left out.
RUNS = """[.. | select(tag == "!!map" and has("run"))
  | select((.run | kind) == "scalar" or (.run | kind) == "alias")
  | {"line": (.run | line), "style": (.run | style), "run": .run,
     "kind": (.run | kind), "shell": .shell, "name": .name,
     "path": (path | join("/"))}]"""


@dataclass(frozen=True)
class Run:
    """One `run:` value, as yq reads it.

    `line` is the value's own line (a block's header), `style` how it
    was written (`literal`, `folded`, `single`, `double`, or empty for
    plain), `text` what bash receives, `path` the `/`-joined path to the
    mapping holding it, and `alias` whether the value is `*name`.
    """

    line: int
    style: str
    text: str
    name: str | None
    shell: str | None
    path: str
    alias: bool


def _yq(expression: str, path: Path | str) -> object:
    """Evaluate one yq expression over one file, as JSON.

    Returns:
        The decoded result.

    Raises:
        ValueError: yq could not read the file, with yq's own message.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [YQ, "-o=json", "-I=0", expression, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        message = done.stderr.strip() or f"yq exited {done.returncode}"
        msg = f"{path}: {message}"
        raise ValueError(msg)
    return decoded.load_json(done.stdout)


def document(path: Path | str) -> dict[str, object]:
    """Read a workflow or an action file.

    A file yq cannot parse, or one holding no mapping, is a ValueError
    carrying the reason, never an empty document.

    Returns:
        The file's top-level mapping, keys in file order.

    """
    return decoded.as_object(_yq(".", path), f"{path}")


def jobs(document: dict[str, object]) -> dict[str, dict[str, object]]:
    """Read a workflow's jobs.

    Returns:
        Job id to the job's mapping, in file order.

    """
    found = decoded.as_object(document.get("jobs", {}), "jobs")
    return {key: decoded.as_object(job, f"jobs.{key}") for key, job in found.items()}


def steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Read a job's steps, or an action's `runs`.

    A job with no `steps`, a reusable-workflow caller, has none.

    Returns:
        Each step's mapping, in order; `with` is a mapping of its own.

    """
    found = decoded.as_array(job.get("steps", []), "steps")
    return [decoded.as_object(step, f"steps[{i}]") for i, step in enumerate(found)]


def _optional(value: object, where: str) -> str | None:
    """Narrow a decoded value to a string, or None where it is absent.

    Returns:
        The string, or None.

    """
    return None if value is None else decoded.as_str(value, where)


def runs(path: Path | str) -> list[Run]:
    """Read every `run:` value in a workflow or an action file.

    A file yq cannot parse is a ValueError, as `document()` refuses it.

    Returns:
        One record per value, in file order.

    """
    out = []
    for i, raw in enumerate(decoded.as_array(_yq(RUNS, path), "runs")):
        record = decoded.as_object(raw, f"runs[{i}]")
        out.append(
            Run(
                line=decoded.as_int(record["line"], "line"),
                style=decoded.as_str(record["style"], "style"),
                text=decoded.as_str(record["run"], "run"),
                name=_optional(record["name"], "name"),
                shell=_optional(record["shell"], "shell"),
                path=decoded.as_str(record["path"], "path"),
                alias=record["kind"] == "alias",
            )
        )
    return out

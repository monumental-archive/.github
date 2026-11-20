#!/usr/bin/env python3
"""Each release job installs every tool it calls, and names the list (#1333).

The release, publish and continuous paths run uncached on purpose (cache
poisoning), so a job that installs the caller's whole mise config pays for
every tool in it, every release: edtf's v1.3.5 publish spent 4,484 runner
seconds installing, most of it compiling tools no job called. So every
uncached `jdx/mise-action` step on those paths installs a named list
(`install_args`): the belt's tools by the belt's own keys, the caller's
build inputs by `release/caller_pins.py`, which reads their versions from
the caller's config.

The cost of a list is that a tool a job starts calling is missing on the
runner, and the release fails "command not found" at its most expensive
moment. These rows move that failure into the canon's gate: for every job
in scope, each tool its `run:` steps call — directly, through the canon
scripts and belt tasks they run, and through the tools stele execs — must
be on the job's list. This generalises the tag job's own rows (#1322),
which it replaces.

A call is a tool's command name as a whole word on a line that is not a
comment. Where a job carries no belt (`MISE_GLOBAL_CONFIG_FILE` unset, the
binary build legs), a belt tool's name is the runner's copy and is not
counted; a caller build input always is.

The gate's own jobs are held the same way where they run one thing
(#1408): the `pgrx` legs run one major's suite, and installed the
caller's whole config to do it. They are cached, so the uncached scope
above never reached them; GATE_LISTS names them, and GATE_WHOLE the job
that keeps everything. The coverage upload installs nothing at all
(#1528): it uploads the reports the gate's coverage:check wrote, and
GATE_NONE holds it to measuring nothing.
"""

import ast
import copy
import io
import re
import shlex
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import caller_pins

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import call_names
import decoded
import hermetic
import workflow_yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

# A job and a step, as workflow_yaml reads them.
Job = dict[str, object]
Step = dict[str, object]

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = CANON / ".github" / "workflows"


# Item 4 of #1333: the scheduled audits and the level checks keep their
# installs. Everything else uncached is on the release, publish or
# continuous path and is held here.
OUT_OF_SCOPE = {
    "audit.yml",
    "repo-audit.yml",
    "repo-mutants.yml",
    "level.yml",
    "level-assert.yml",
}

# The jobs #1333 names, as the tree holds them. A new uncached mise-action
# job anywhere else fails the coverage row until it is listed here and
# installs a list of its own.
IN_SCOPE = [
    ("assemble-oci-index.yml", "manifest"),
    # Not on the release path: the scheduled base approvals, which judge
    # each pinned base with `stele verify base` (#1377). It installs
    # stele and the jq its predicate step calls, and this row holds the
    # list to its calls.
    ("base-attest.yml", "attest"),
    ("attach-release.yml", "attach"),
    ("build-go-binary.yml", "build"),
    # The collect jobs call `stele derive subjects` (#1364): stele alone,
    # and jq beside it where the images job still writes `images`.
    ("build-go-binary.yml", "collect"),
    # The base check ahead of the build legs (#1377): stele alone.
    ("build-oci-image.yml", "bases"),
    ("build-oci-image.yml", "build"),
    ("build-pgrx-extension.yml", "collect"),
    ("build-pgrx-images.yml", "collect"),
    ("build-pgrx-images.yml", "image"),
    ("build-rust-binary.yml", "build"),
    ("build-rust-binary.yml", "collect"),
    ("build-rust-crate.yml", "build"),
    ("build-wasm-npm.yml", "build"),
    ("continuous.yml", "commit-point"),
    ("continuous.yml", "facts"),
    ("continuous.yml", "sbom"),
    ("publish-rust-crate.yml", "publish"),
    ("publish-wasm-npm.yml", "publish"),
    ("publish.yml", "commit-point"),
    ("publish.yml", "facts"),
    # The class derivation, `stele derive classes` (#1378).
    ("publish.yml", "guard"),
    # The pre-release comparison, `stele verify repro` (#1376).
    ("publish.yml", "repro-gate"),
    ("publish.yml", "sbom"),
    # Not on the release path: the signer-identity watch, scheduled and
    # in sequential legs while a backlog lasts (#1339). It installs stele
    # alone, and this row holds the list to its calls.
    ("rekor-monitor.yml", "watch"),
    ("release.yml", "release-pr"),
    ("release.yml", "tag"),
    ("repro-check.yml", "compare"),
    ("security-release.yml", "assess"),
    ("verify-release.yml", "verify"),
    ("vex-attest.yml", "plan"),
]

# The one job in scope that installs the whole config, and why (ruled on
# #1333): the Release PR re-derives the coverage floor, which runs the
# CALLER's test suite on the runner (`cargo llvm-cov --workspace`, a pgrx
# crate's Postgres majors). Those calls are the caller's code, so no list
# could be held to them here, and a wrong list would red a consumer's
# Release PR instead of this gate.
WHOLE_CONFIG = {
    ("release.yml", "release-pr"): "it runs the caller's own test suite",
}

# The gate's jobs (ci.yml) that install a named list (#1408), and the one
# that installs the whole config, with why. Every mise-action job in
# ci.yml is one or the other.
GATE_LISTS = [("ci.yml", "pgrx")]
GATE_WHOLE = {
    ("ci.yml", "gate"): (
        "it runs every lint, so it needs the whole belt and every tool the"
        " caller's config pins"
    ),
}

# The gate's jobs that install no tool and run none (#1528), with why.
# The coverage upload re-measured every leg with a list of its own, five
# tools where the gate has the belt, and hung on main in a test the gate
# passes; it now uploads the gate's own reports.
GATE_NONE = {
    ("ci.yml", "coverage-upload"): (
        "it uploads the reports the gate's coverage:check wrote, and measures nothing"
    ),
}

# The caller's build inputs, by the commands that run them: the tool name
# is what `release/caller_pins.py` is asked for.
CALLER_COMMANDS = {
    "cargo": "rust",
    "rustup": "rust",
    "rustc": "rust",
    "node": "node",
    "npm": "node",
    "npx": "node",
    "go": "go",
    "wasm-pack": "wasm-pack",
    "cargo-auditable": "cargo-auditable",
    "cargo-pgrx": "cargo-pgrx",
    "cargo-fuzz": "cargo-fuzz",
    "govulncheck": "govulncheck",
    "postgresql-binaries": "postgresql-binaries",
}

# Calls a tool's name never appears in: the Postgres a pgrx task runs is
# located by pgrx_postgres.py from `mise ls` and registered with
# `cargo pgrx init`, both by path.
PROVISIONS = {
    r"pgrx_postgres\.py": ["postgresql-binaries"],
}

# What stele execs, read from its source (internal/cargo, internal/osv,
# internal/cli/emit.go): a call that never appears in a workflow's text.
STELE_EXECS = {
    r"stele\s+derive\s+sbom": ["cargo"],
    r"stele\s+derive\s+vex(?:-subjects)?": ["osv-scanner"],
    r"stele\s+assert\s+blast-radius": ["osv-scanner"],
    r"stele\s+emit\s+chain": ["cosign"],
    # The source verdict signs through the same signer seam (stele#468).
    r"stele\s+emit\s+source-vsa": ["cosign"],
}

# A cargo subcommand runs the `cargo-<sub>` binary.
CARGO_SUB = re.compile(r"(?<![\w./-])cargo\s+([a-z][\w-]*)")

# The subprocess calls whose first argument is the command (#1499).
SUBPROCESS_CALLS = {"run", "check_output", "check_call", "Popen"}

# Helpers that run a command this check cannot read (#1499): the argv is
# a variable, a splat or a computed path. Each contributes nothing to its
# job's calls, so each is named here with why; a new one is red by name.
DYNAMIC = {
    "release/attach_publish.py": "runs record-draft.sh by its path, which"
    " scripts() follows by name",
    "release/package_crates.py": "runs crates-io-members.sh by its path,"
    " which scripts() follows by name",
    "release/publish_crates.py": "runs crates-io-members.sh by its path, and"
    " builds its cargo metadata, package and publish argvs in variables",
    "mise/belt_shell.py": "splats its shfmt and shellcheck argvs from lists",
    "mise/embedded_shell.py": "splats its shfmt argv from a list",
    "mise/git_decoy.py": "runs its caller's command against a decoy repository",
    "mise/hermetic.py": "splats its caller's pathspecs into the git ls-files argv",
    "mise/mutants_python.py": "runs the repository's own test-command,"
    " filled per test file, and cosmic-ray on each mutant",
    "mise/with_lock.py": "runs its caller's command under the lock",
    "mise/unittest_guarded.py": "starts itself as a worker under the"
    " interpreter it runs on (#1623)",
    "mise/python_properties.py": "starts itself on each shard under the"
    " interpreter it runs on (#1741)",
    "mise/security_config.py": "builds its gh argv in a variable",
    "mise/tool_python.py": "asks a tool venv's interpreter and python3"
    " found on PATH (#1788)",
}

# Scripts that run before any tool is installed, so call nothing a list
# could carry (the reader prints tool names in its own prose).
PRE_INSTALL = {"caller_pins.py"}


def strings(*items: str) -> list[str]:
    """List the given strings, typed as strings for the assertions.

    Returns:
        The strings, in order.

    """
    return list(items)


def belt() -> dict[str, object]:
    """Read the belt's config.

    Returns:
        mise/config.toml, decoded.

    """
    return decoded.load_toml((CANON / "mise" / "config.toml").read_text("utf-8"))


def belt_table(name: str) -> dict[str, object]:
    """Read one table of the belt's config.

    Returns:
        mise/config.toml's `[name]`, or an empty table when there is none.

    """
    return decoded.as_object(belt().get(name, {}), f"[{name}]")


def belt_tools() -> dict[str, str]:
    """Map each belt tool's command name to its `[tools]` key.

    Returns:
        Command name to the key as mise/config.toml writes it.

    """
    return {caller_pins.tool_name(key): key for key in belt_table("tools")}


def scalar(value: object, where: str) -> str:
    """Spell a decoded scalar as the YAML text a law compares.

    Returns:
        The string; a bool as YAML writes it; "" where it is absent.

    Raises:
        UndecodableError: the value is a mapping or a list.

    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    msg = f"{where} is not a scalar"
    raise decoded.UndecodableError(msg)


def field(mapping: dict[str, object], key: str) -> str:
    """Read one scalar of a job or a step.

    Returns:
        Its text, "" where it is absent.

    """
    return scalar(mapping.get(key), key)


def table(mapping: dict[str, object], key: str) -> dict[str, str]:
    """Read one mapping of scalars of a job or a step: `with` or `env`.

    Returns:
        Each key to its text; empty where the mapping is absent.

    """
    found = decoded.as_object(mapping.get(key) or {}, key)
    return {k: scalar(v, f"{key}.{k}") for k, v in found.items()}


def scalars(node: object) -> Iterator[str]:
    """Walk every scalar value under a node, keys left out.

    Yields:
        Each value's text.

    """
    if isinstance(node, dict):
        for value in node.values():
            yield from scalars(value)
    elif isinstance(node, list):
        for value in node:
            yield from scalars(value)
    else:
        yield scalar(node, "a value")


def inputs(step: Step) -> dict[str, str]:
    """Read a step's `with:` inputs.

    Returns:
        Each input to its text.

    """
    return table(step, "with")


def whole(path: Path) -> dict[str, object]:
    """Read a workflow, once per file for the run.

    Returns:
        The document, as workflow_yaml reads it; a copy, so a planted
        change never reaches another row.

    """
    key = str(path)
    if key not in READ:
        READ[key] = workflow_yaml.document(path)
    return copy.deepcopy(READ[key])


def documents(path: Path) -> dict[str, Job]:
    """Read a workflow's jobs, once per file for the run.

    Returns:
        Job id to the job.

    """
    return workflow_yaml.jobs(whole(path))


def parsed(text: str) -> dict[str, object]:
    """Read YAML text through workflow_yaml, by a scratch file, once per text.

    Returns:
        The document; a copy, as `whole()` returns one.

    """
    if text not in PARSED:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "planted.yml"
            path.write_text(text, "utf-8")
            PARSED[text] = workflow_yaml.document(path)
    return copy.deepcopy(PARSED[text])


def appended(job: Job, *added: Step) -> Job:
    """Plant steps after a job's own.

    Returns:
        A copy of the job with the steps added.

    """
    return {**job, "steps": [*workflow_yaml.steps(job), *added]}


def mise_steps(job: Job) -> list[Step]:
    """Name a job's mise-action steps.

    Returns:
        Each step whose `uses:` is jdx/mise-action.

    """
    return [
        s for s in workflow_yaml.steps(job) if "jdx/mise-action" in field(s, "uses")
    ]


def uncached(step: Step) -> bool:
    """Say whether a mise-action step runs without the tool cache.

    Returns:
        True unless the step sets `cache: true`.

    """
    return inputs(step).get("cache", "true") != "true"


def tool_cache_restored(job: Job) -> bool:
    """Say whether a job restores its tool cache around the install (#1406).

    The gate's jobs install with `cache: false` because `actions/cache`
    steps either side restore and save the tool cache instead: cached, so
    not a release install, and held by release/test_gate_caches.py.

    Returns:
        True when an `actions/cache/restore` step restores the mise dir.

    """
    return any(
        "actions/cache/restore@" in field(s, "uses")
        and "tool-key.outputs.dir" in inputs(s).get("path", "")
        for s in workflow_yaml.steps(job)
    )


def installs(job: Job) -> tuple[list[str], list[str], list[str]]:
    """Read what a job installs.

    Returns:
        The belt keys its install step names, the caller tools its pins
        step needs, and the caller tools it takes where pinned.

    Raises:
        ValueError: no install list, or a list naming a pins step that
            does not exist, so a row would judge nothing.

    """
    (step,) = mise_steps(job)
    args = inputs(step).get("install_args", "")
    if not args:
        msg = "the job's mise-action step names no install_args"
        raise ValueError(msg)
    keys = re.sub(r"\$\{\{.*?\}\}", " ", args).split()
    ref = re.search(r"steps\.([\w-]+)\.outputs\.install", args)
    needed: list[str] = []
    optional: list[str] = []
    if ref:
        (pins,) = [
            s for s in workflow_yaml.steps(job) if field(s, "id") == ref.group(1)
        ]
        call = field(pins, "run").replace("\\\n", " ").split("caller_pins.py", 1)[1]
        words = shlex.split(call.split(">", 1)[0])
        it = iter(words[1:])
        for word in it:
            if word == "--input-of":
                next(it)
            elif word == "--if-pinned":
                optional.append(next(it))
            elif word == "--major":
                needed.append(next(it).partition("=")[0])
            else:
                needed.append(word)
    return keys, needed, optional


# The commands whose arguments the shell prints (#1785).
PRINTS = frozenset({"echo", "printf"})
# The expansion operators whose word is a message the shell prints.
UNSET_OPS = frozenset({":?", "?"})
# The nodes inside a printed span the shell still runs.
RUNS = frozenset({"CmdSubst", "ProcSubst"})
# One parse per distinct text: `code()` is cached on its argument. A
# dict, not functools.cache, whose signature is `Callable[..., _T]`,
# an Any expression the belt's mypy refuses.
REDUCED: dict[str, str] = {}
# One read per workflow file, for the same reason: `documents()`.
READ: dict[str, dict[str, object]] = {}
PARSED: dict[str, dict[str, object]] = {}


def code(text: str) -> str:
    """Reduce shell to the text that can call a tool (#1785).

    What is prose is read from shfmt's syntax tree, never guessed by
    line: a print's arguments (`echo`, `printf`), a value assigned
    only quoted parts (the `emsg+=` remedy lines), and the message of
    a `${NAME:?message}` or `${NAME?message}` expansion (#1782) go to
    spaces, every newline kept. A command or process substitution
    inside any of them stays: the shell runs it. Comment lines go too.

    Returns:
        The text that remains.

    Raises:
        ValueError: shfmt cannot parse the text; its message, never
            a fallback guess.

    """
    if text in REDUCED:
        return REDUCED[text]
    source = text.encode("utf-8")
    parsed = subprocess.run(
        [hermetic.tool("shfmt"), "--to-json"],
        input=source,
        capture_output=True,
        check=False,
        env=hermetic.env(),
    )
    if parsed.returncode:
        why = parsed.stderr.decode("utf-8", "replace").strip()
        msg = f"shfmt cannot parse the text: {why}"
        raise ValueError(msg)
    out = bytearray(source)
    for start, end in prose(decoded.load_json(parsed.stdout)):
        for at in range(start, end):
            if out[at] != ord("\n"):
                out[at] = ord(" ")
    REDUCED[text] = uncommented(out.decode("utf-8"))
    return REDUCED[text]


def uncommented(text: str) -> str:
    """Drop the comment lines of a text, shell or a whole job's YAML.

    Returns:
        Every line that does not open with `#`.

    """
    return "\n".join(ln for ln in text.splitlines() if not re.match(r"\s*#", ln))


def offsets(node: object, where: str) -> tuple[int, int]:
    """Read one shfmt node's byte span.

    Returns:
        Its start and end offsets.

    """
    found = decoded.as_object(node, where)
    start = decoded.as_object(found.get("Pos"), f"{where}.Pos")
    end = decoded.as_object(found.get("End"), f"{where}.End")
    return (
        decoded.as_int(start.get("Offset"), f"{where}.Pos.Offset"),
        decoded.as_int(end.get("Offset"), f"{where}.End.Offset"),
    )


def literal(word: object) -> str | None:
    """Read a word made of one literal part.

    Returns:
        The literal, or None for any other word.

    """
    if not decoded.is_object(word):
        return None
    parts = word.get("Parts")
    if not decoded.is_array(parts) or len(parts) != 1:
        return None
    part = parts[0]
    if not decoded.is_object(part) or part.get("Type") != "Lit":
        return None
    value = part.get("Value")
    return value if isinstance(value, str) else None


def quoted(word: object) -> bool:
    """Say whether a word is made only of quoted parts.

    Returns:
        True for a word of `"..."` and `'...'` parts and nothing else.

    """
    if not decoded.is_object(word):
        return False
    parts = word.get("Parts")
    return (
        decoded.is_array(parts)
        and bool(parts)
        and all(
            decoded.is_object(p) and p.get("Type") in {"DblQuoted", "SglQuoted"}
            for p in parts
        )
    )


def printed(node: dict[str, object]) -> list[object]:
    """Name the words of one node the shell prints and never runs.

    Returns:
        A print's arguments, an assignment's all-quoted value, an
        unset expansion's message; none for any other node.

    """
    kind = node.get("Type")
    words: list[object] = []
    if kind == "CallExpr":
        args = node.get("Args")
        if decoded.is_array(args) and args and literal(args[0]) in PRINTS:
            words += args[1:]
    assigns = node.get("Assigns") if kind == "CallExpr" else None
    if kind == "DeclClause":
        assigns = node.get("Args")
    if decoded.is_array(assigns):
        for assign in assigns:
            if decoded.is_object(assign) and quoted(assign.get("Value")):
                words.append(assign.get("Value"))
    exp = node.get("Exp") if kind == "ParamExp" else None
    if decoded.is_object(exp) and exp.get("Op") in UNSET_OPS and "Word" in exp:
        words.append(exp.get("Word"))
    return words


def runs(node: object) -> Iterator[tuple[int, int]]:
    """Find the span of each outermost substitution under a node.

    Yields:
        The spans, in the tree's order.

    """
    if decoded.is_array(node):
        for item in node:
            yield from runs(item)
    elif decoded.is_object(node):
        if node.get("Type") in RUNS:
            yield offsets(node, str(node.get("Type")))
            return
        for value in node.values():
            yield from runs(value)


def prose(node: object) -> Iterator[tuple[int, int]]:
    """Find every byte span of a shfmt tree the shell prints, not runs.

    Yields:
        The spans to blank, each printed word less its substitutions.

    """
    if decoded.is_array(node):
        for item in node:
            yield from prose(item)
        return
    if not decoded.is_object(node):
        return
    for word in printed(node):
        start, end = offsets(word, "a printed word")
        for left, right in runs(word):
            yield start, left
            start = right
        yield start, end
    for value in node.values():
        yield from prose(value)


def constants(tree: ast.Module) -> dict[str, str]:
    """Read the module-level names an argv can start with.

    Returns:
        Each name bound to a string constant, or to
        `shutil.which("x") or "x"`, with the command it stands for.

    """
    out = {}
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            continue
        value: ast.expr | None = node.value
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            value = which_or(value.values)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out[node.targets[0].id] = value.value
    return out


def which_or(values: list[ast.expr]) -> ast.expr | None:
    """Read `shutil.which("x") or "x"` as its fallback, "x".

    Returns:
        The fallback constant when the `or` is exactly that shape, else
        None.

    """
    match values:
        case [ast.Call() as call, ast.Constant() as name] if (
            ast.unparse(call) == f"shutil.which({ast.unparse(name)})"
        ):
            return name
    return None


def first_word(arg: ast.expr | None, names: dict[str, str]) -> str | None:
    """Read the command an argv names, where it names one literally.

    Returns:
        The first element of a list or tuple literal, or the first word
        of a string literal, when it is a string constant or a module
        name `constants()` resolved; None for anything else.

    """
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        head = arg.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
        if isinstance(head, ast.Name):
            return names.get(head.id)
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        words = arg.value.split()
        return words[0] if words else None
    return None


def argv_commands(tree: ast.Module) -> tuple[set[str], list[str]]:
    """Name the commands a helper's subprocess calls run (#1499).

    A command in a Python argv is a quoted string, which `word()` never
    reads as a call. Every call to subprocess.run, check_output,
    check_call or Popen, however imported, is read here instead.

    Returns:
        The commands, and one line per call whose argv cannot be read.

    """
    qualify = call_names.qualifier(tree)
    calls = {f"subprocess.{name}" for name in SUBPROCESS_CALLS}
    names = constants(tree)
    commands, dynamic = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or qualify(node.func) not in calls:
            continue
        arg = node.args[0] if node.args else None
        arg = arg or next((k.value for k in node.keywords if k.arg == "args"), None)
        command = first_word(arg, names)
        if command is None:
            shown = ast.unparse(arg) if arg is not None else "no argv"
            dynamic.append(f"line {node.lineno}: {shown}")
        else:
            commands.add(command)
    return commands, dynamic


def python_code(text: str, name: str = "a helper") -> tuple[str, set[str], list[str]]:
    """Reduce a Python helper to what can run a subprocess.

    Comments go by Python's own tokenizer (#1496): every COMMENT token,
    wherever it sits on its line, so `import subprocess  # ruff: ...`
    is not read as a call to ruff, and a `#` inside a string stays. The
    code is kept as written; only each comment's own columns are cut.

    Returns:
        The text without comments and docstrings ("" for a helper that
        imports no subprocess: it calls nothing), the commands its
        subprocess calls run, and a line per call whose argv cannot be
        read (`argv_commands`).

    Raises:
        ValueError: the tokenizer or the parser cannot read the helper.
            Read as "calls nothing", it would hide a real call.

    """
    lines = text.splitlines()
    try:
        comments = [
            token.start
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
        ]
        tree = ast.parse(text)
    except (tokenize.TokenError, SyntaxError) as error:
        message = f"{name}: the tokenizer cannot read it ({error})"
        raise ValueError(message) from error
    for row, col in comments:
        lines[row - 1] = lines[row - 1][:col].rstrip()
    text = "\n".join(lines)
    if not re.search(r"^import subprocess|^from subprocess", text, re.MULTILINE):
        return "", set(), []
    commands, dynamic = argv_commands(tree)
    return re.sub(r'"""(?:.|\n)*?"""', "", text), commands, dynamic


def unreadable(helpers: dict[str, str]) -> list[str]:
    """Name each helper whose argv cannot be read and is not in DYNAMIC.

    Returns:
        One line per such helper, with the calls, sorted.

    """
    out = []
    for name, text in sorted(helpers.items()):
        _, _, dynamic = python_code(text, name)
        if dynamic and name not in DYNAMIC:
            out.append(
                f"{name}: runs a command this check cannot read"
                f" ({'; '.join(dynamic)}); name it in DYNAMIC with why"
            )
    return out


def helpers() -> dict[str, str]:
    """Read every Python helper `scripts()` can follow.

    Returns:
        Path relative to the canon -> text, tests excluded.

    """
    root = CANON
    return {
        f"{home}/{p.name}": p.read_text("utf-8")
        for home in ("release", "mise")
        for p in sorted((root / home).glob("*.py"))
        if not p.name.startswith("test_")
    }


def scripts(text: str, seen: set[str]) -> str:
    """Follow the canon scripts and belt tasks a body runs.

    Returns:
        The code of every canon script and belt task the text reaches,
        transitively, each once.

    Raises:
        UndecodableError: a reached task whose run is not text.

    """
    out = []
    tasks = belt_table("tasks")
    for name in [m.group() for m in re.finditer(r"[\w-]+\.(?:sh|py)\b", text)]:
        if name in seen or name in PRE_INSTALL:
            continue
        for home in ("release", "mise"):
            path = CANON / home / name
            if path.is_file():
                seen.add(name)
                source = path.read_text("utf-8")
                commands: set[str] = set()
                if name.endswith(".py"):
                    body, commands, _ = python_code(source, f"{home}/{name}")
                else:
                    body = code(source)
                # A Python argv's command, as one bare word per line, so
                # it is judged exactly as a word in a shell body is
                # (#1499). The `;` keeps CARGO_SUB from pairing two.
                out += [body, *(f"{c};" for c in sorted(commands))]
                out.append(scripts(body, seen))
    # A task is reached by name, however it is run: `mise run audit:deny`
    # and `for task in audit:deny audit:go-vulns; do mise run "${task}"`.
    for task in [t for t in tasks if ":" in t and word(t, text)]:
        if task in seen:
            continue
        seen.add(task)
        run = decoded.as_object(tasks[task], f"[tasks.{task}]")["run"]
        if isinstance(run, str):
            body = code(run)
        elif decoded.is_strings(run):
            body = code("\n".join(run))
        else:
            msg = f"[tasks.{task}] run is neither a string nor strings"
            raise decoded.UndecodableError(msg)
        out += [body, scripts(body, seen)]
    return "\n".join(out)


def word(name: str, text: str) -> bool:
    """Say whether a command name is called in the text.

    A name opening a quoted string is a word in a message or a jq
    program (`"npm " + .key`), not a command: nothing here quotes a
    literal command name to run it.

    Returns:
        True when `name` stands as a whole word, not a path or a suffix.

    """
    return bool(re.search(rf"(?<![\w./$'\"-]){re.escape(name)}(?![\w./-])", text))


def envs(job: Job) -> dict[str, str]:
    """Read every `env:` a job sets, its own and its steps'.

    Returns:
        Each variable to its value; a step's wins over the job's.

    """
    found = table(job, "env")
    for step in workflow_yaml.steps(job):
        found.update(table(step, "env"))
    return found


def bodies(job: Job) -> str:
    """Read a job's `run:` values, comment lines dropped.

    Returns:
        Every step's run, joined.

    """
    return uncommented("\n".join(field(s, "run") for s in workflow_yaml.steps(job)))


def carries_belt(workflow: str, job: Job) -> bool:
    """Say whether a job has the belt in scope.

    Returns:
        True where the job sets `MISE_GLOBAL_CONFIG_FILE`, or where the
        workflow runs only in the canon (no `workflow_call`), whose own
        `mise/config.toml` IS the belt.

    """
    on = whole(WORKFLOWS / workflow).get("on")
    called = isinstance(on, dict) and "workflow_call" in on
    return "MISE_GLOBAL_CONFIG_FILE" in envs(job) or not called


def calls(job: Job, *, carries: bool) -> tuple[set[str], set[str]]:
    """Name every tool a job's run steps call.

    Returns:
        The belt keys called (none where the job carries no belt: a belt
        tool's name is then the runner's copy) and the caller tools.

    """
    body = "\n".join(code(field(s, "run")) for s in workflow_yaml.steps(job))
    body += "\n" + scripts(body, set())
    commands = {m.group(1) for m in CARGO_SUB.finditer(body)}
    commands = {f"cargo-{c}" for c in commands}
    for pattern, execs in [*STELE_EXECS.items(), *PROVISIONS.items()]:
        if re.search(pattern, body):
            commands.update(execs)
    names = set(belt_tools()) | set(CALLER_COMMANDS)
    commands |= {n for n in names if word(n, body)}
    caller = {CALLER_COMMANDS[c] for c in commands if c in CALLER_COMMANDS}
    tools = belt_tools()
    keys = {tools[c] for c in commands if c in tools} if carries else set()
    return keys, caller


def uninstalled(workflow: str, job_id: str, job: Job) -> list[str]:
    """Name each tool a job calls and does not install.

    Returns:
        One line per missing tool, sorted.

    """
    keys, needed, optional = installs(job)
    called_keys, called_caller = calls(job, carries=carries_belt(workflow, job))
    missing = sorted(called_keys - set(keys)) + sorted(
        called_caller - set(needed) - set(optional)
    )
    return [f"{workflow} {job_id}: calls {t} and does not install it" for t in missing]


def auto_installs(workflow: str, job_id: str, job: Job) -> list[str]:
    """Name a listed job whose `mise run` would install the rest anyway.

    Measured on mise 2026.11.6 (#1408): `mise run` installs every tool
    the config pins and the data directory lacks before it runs the
    task, so a list is only a list where the job turns that off.

    Returns:
        One line when the job runs `mise run` and does not set
        MISE_TASK_RUN_AUTO_INSTALL to "false"; none otherwise.

    """
    if not re.search(r"(?<![\w-])mise\s+run\b", bodies(job)):
        return []
    if envs(job).get("MISE_TASK_RUN_AUTO_INSTALL") == "false":
        return []
    return [
        (
            f"{workflow} {job_id}: runs `mise run`, which installs every tool"
            ' the config pins unless MISE_TASK_RUN_AUTO_INSTALL is "false"'
        )
    ]


def load(workflow: str) -> dict[str, Job]:
    """Read one workflow's jobs.

    Returns:
        Job id to the job.

    """
    return documents(WORKFLOWS / workflow)


def planted_job(workflow: str, job_id: str, old: str, new: str = "") -> Job:
    """Plant a change in a real workflow's text and read one job of it.

    Returns:
        The job, read from the changed text.

    Raises:
        AssertionError: `old` is not in the file, so the plant proves
            nothing.

    """
    source = (WORKFLOWS / workflow).read_text("utf-8")
    if old not in source:
        raise AssertionError(old)
    return workflow_yaml.jobs(parsed(source.replace(old, new)))[job_id]


def whole_config(workflow: str, job_id: str, job: Job) -> list[str]:
    """Name a job in scope that installs the whole config unexempted.

    Returns:
        One line when the job's mise-action step names no install_args
        and the job is not in WHOLE_CONFIG; none otherwise.

    """
    (step,) = mise_steps(job)
    if inputs(step).get("install_args") or (workflow, job_id) in WHOLE_CONFIG:
        return []
    return [
        (
            f"{workflow} {job_id}: installs the whole mise config; name its"
            " tools in install_args (#1333)"
        )
    ]


def readers(path: Path, text: str, root: Path = CANON) -> list[str]:
    """Name a reader of a caller's mise config other than the one reader.

    Returns:
        One line when `text` looks a tool up in a parsed `[tools]` table.

    """
    if path.name == "caller_pins.py" or path.name.startswith("test_"):
        return []
    if not re.search(r'tools\.get\(|\.get\("tools"|\["tools"\]', text):
        return []
    return [
        (
            f"{path.relative_to(root)}: reads a caller's mise config itself;"
            " read it through release/caller_pins.py (#1333)"
        )
    ]


class Coverage(unittest.TestCase):
    """The rows hold the real surface, not a remembered list."""

    def test_every_uncached_install_in_scope_is_held(self) -> None:
        """Every uncached mise-action job outside item 4 is a row."""
        found = sorted(
            (path.name, job_id)
            for path in (CANON / ".github" / "workflows").glob("*.yml")
            if path.name not in OUT_OF_SCOPE
            for job_id, job in documents(path).items()
            if any(uncached(s) for s in mise_steps(job))
            and not tool_cache_restored(job)
        )
        scoped: list[tuple[str, str]] = sorted(IN_SCOPE)
        self.assertEqual(scoped, found)

    def test_every_job_names_its_list(self) -> None:
        """No job installs the whole config, and none can be empty."""
        for workflow, job_id in IN_SCOPE:
            job = load(workflow)[job_id]
            with self.subTest(workflow=workflow, job=job_id):
                self.assertEqual(strings(), whole_config(workflow, job_id, job))
                if (workflow, job_id) not in WHOLE_CONFIG:
                    keys, needed, _ = installs(job)
                    self.assertTrue(keys or needed, "an empty list installs all")

    def test_the_exemption_is_still_true(self) -> None:
        """The exempt job really installs the whole config, and is in scope."""
        for workflow, job_id in WHOLE_CONFIG:
            with self.subTest(workflow=workflow, job=job_id):
                self.assertIn((workflow, job_id), IN_SCOPE)
                (step,) = mise_steps(load(workflow)[job_id])
                self.assertNotIn("install_args", inputs(step))

    def test_a_second_whole_config_job_is_named(self) -> None:
        """Planted: a job loses its list and is not the exempt one."""
        job = load("attach-release.yml")["attach"]
        (step,) = mise_steps(job)
        decoded.as_object(step["with"], "with").pop("install_args")
        self.assertEqual(
            strings(
                "attach-release.yml attach: installs the whole mise config;"
                " name its tools in install_args (#1333)"
            ),
            whole_config("attach-release.yml", "attach", job),
        )

    def test_every_gate_install_is_held(self) -> None:
        """Every mise-action job in ci.yml names a list or is the exemption."""
        found = sorted(
            ("ci.yml", job_id)
            for job_id, job in load("ci.yml").items()
            if mise_steps(job)
        )
        gated: list[tuple[str, str]] = sorted([*GATE_LISTS, *GATE_WHOLE])
        self.assertEqual(gated, found)

    def test_the_gate_lists_are_lists(self) -> None:
        """The legs name their tools; the gate does not."""
        for workflow, job_id in GATE_LISTS:
            job = load(workflow)[job_id]
            with self.subTest(job=job_id):
                keys, needed, optional = installs(job)
                self.assertTrue(keys and (needed or optional))
        for workflow, job_id in GATE_WHOLE:
            (step,) = mise_steps(load(workflow)[job_id])
            self.assertNotIn("install_args", inputs(step))

    def test_every_belt_key_is_a_belt_key(self) -> None:
        """A misspelt key would install nothing and say nothing."""
        known = set(belt_table("tools"))
        listed = [j for j in IN_SCOPE if j not in WHOLE_CONFIG]
        for workflow, job_id in [*listed, *GATE_LISTS]:
            with self.subTest(workflow=workflow, job=job_id):
                keys, _, _ = installs(load(workflow)[job_id])
                unknown: list[str] = [k for k in keys if k not in known]
                self.assertEqual(strings(), unknown)


class JobInstalls(unittest.TestCase):
    """Each job installs what it calls."""

    def test_every_job_installs_what_it_calls(self) -> None:
        """The real jobs against their real steps and scripts."""
        for workflow, job_id in [j for j in IN_SCOPE if j not in WHOLE_CONFIG]:
            with self.subTest(workflow=workflow, job=job_id):
                self.assertEqual(
                    strings(), uninstalled(workflow, job_id, load(workflow)[job_id])
                )

    def test_the_tag_job_installs_three_belt_tools(self) -> None:
        """The tag job's list (#1322), unchanged by the generalisation."""
        self.assertEqual(
            (
                ["jq", "github:monumental-archive/stele", "aqua:sigstore/gitsign"],
                [],
                [],
            ),
            installs(load("release.yml")["tag"]),
        )

    def test_a_dropped_install_is_named(self) -> None:
        """Planted: the tag job without gitsign, which signs the tag."""
        job = planted_job("release.yml", "tag", " aqua:sigstore/gitsign")
        self.assertEqual(
            strings(
                "release.yml tag: calls aqua:sigstore/gitsign and does not install it"
            ),
            uninstalled("release.yml", "tag", job),
        )

    def test_a_dropped_caller_input_is_named(self) -> None:
        """Planted: the binary build without cargo-auditable."""
        job = planted_job(
            "build-rust-binary.yml", "build", "rust cargo-auditable >>", "rust >>"
        )
        self.assertEqual(
            strings(
                "build-rust-binary.yml build: calls cargo-auditable and does not"
                " install it"
            ),
            uninstalled("build-rust-binary.yml", "build", job),
        )

    def test_a_new_call_is_named(self) -> None:
        """Planted: a step starts calling a belt tool the job lacks."""
        job = appended(
            load("attach-release.yml")["attach"],
            {"name": "Planted", "run": "cosign verify-blob dist/checksums.txt"},
        )
        self.assertEqual(
            strings(
                "attach-release.yml attach: calls aqua:sigstore/cosign and does not"
                " install it"
            ),
            uninstalled("attach-release.yml", "attach", job),
        )

    def test_a_new_indirect_call_is_named(self) -> None:
        """Planted: stele's VEX derivation execs osv-scanner."""
        job = appended(
            load("attach-release.yml")["attach"],
            {"name": "Planted", "run": "stele derive vex --out dist/x.json"},
        )
        self.assertEqual(
            strings(
                "attach-release.yml attach: calls aqua:google/osv-scanner and does"
                " not install it"
            ),
            uninstalled("attach-release.yml", "attach", job),
        )

    def test_a_tool_named_only_in_a_comment_is_not_a_call(self) -> None:
        """Prose about a tool installs nothing and needs nothing."""
        job = appended(
            load("attach-release.yml")["attach"],
            {"name": "Planted", "run": "# cosign could verify this one day\ntrue\n"},
        )
        self.assertEqual(strings(), uninstalled("attach-release.yml", "attach", job))


class GateInstalls(unittest.TestCase):
    """The gate's pgrx legs install what they call (#1408)."""

    def test_every_gate_list_installs_what_it_calls(self) -> None:
        """The real jobs against pgrx-test.sh and pgrx-env.sh."""
        for workflow, job_id in GATE_LISTS:
            with self.subTest(job=job_id):
                self.assertEqual(
                    strings(), uninstalled(workflow, job_id, load(workflow)[job_id])
                )

    def test_no_gate_list_is_undone_by_mise_run(self) -> None:
        """Each listed job that runs a task keeps `mise run` to the list."""
        for workflow, job_id in GATE_LISTS:
            with self.subTest(job=job_id):
                self.assertEqual(
                    strings(), auto_installs(workflow, job_id, load(workflow)[job_id])
                )

    def test_a_leg_that_lets_mise_run_install_is_named(self) -> None:
        """Planted: the setting gone, `mise run test:pgrx` installs it all."""
        job = load("ci.yml")["pgrx"]
        decoded.as_object(job["env"], "env").pop("MISE_TASK_RUN_AUTO_INSTALL")
        self.assertEqual(
            strings(
                "ci.yml pgrx: runs `mise run`, which installs every tool the"
                ' config pins unless MISE_TASK_RUN_AUTO_INSTALL is "false"'
            ),
            auto_installs("ci.yml", "pgrx", job),
        )

    def test_a_task_named_in_a_followed_scripts_comment_is_not_reached(
        self,
    ) -> None:
        """The reader follows a script's code, never its comments (#1408).

        pgrx-env.sh's comments name `coverage:check` and its siblings.
        `scripts()` once reassigned the text it searched for task names to
        each followed file's whole source, comments included, so a step
        sourcing it seemed to reach coverage-measure.sh and call Go.
        """
        job: Job = {
            "steps": [{"name": "Planted", "run": '. "${ORG_BELT_DIR}/pgrx-env.sh"'}]
        }
        self.assertEqual(
            ({"jq"}, {"rust", "cargo-pgrx", "postgresql-binaries"}),
            calls(job, carries=True),
        )

    def test_a_leg_installs_rust_cargo_pgrx_and_its_postgres(self) -> None:
        """The leg's list as derived: no linter, node, fuzz or MSRV."""
        self.assertEqual(
            (
                ["jq", "aqua:taiki-e/cargo-llvm-cov"],
                ["postgresql-binaries", "rust", "cargo-pgrx"],
                [],
            ),
            installs(load("ci.yml")["pgrx"]),
        )

    def test_a_leg_that_drops_cargo_pgrx_is_named(self) -> None:
        """Planted: cargo-pgrx gone from the leg, which runs `cargo pgrx`."""
        job = planted_job("ci.yml", "pgrx", "rust cargo-pgrx >>", "rust >>")
        self.assertEqual(
            strings("ci.yml pgrx: calls cargo-pgrx and does not install it"),
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_leg_that_drops_its_postgres_is_named(self) -> None:
        """Planted: no `--major`, so no Postgres for pgrx_postgres.py."""
        job = planted_job(
            "ci.yml",
            "pgrx",
            '            --major "postgresql-binaries=${ORG_PGRX_MAJOR}" \\\n',
        )
        self.assertEqual(
            strings("ci.yml pgrx: calls postgresql-binaries and does not install it"),
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_new_call_in_a_leg_is_named(self) -> None:
        """Planted: a leg starts building the fuzz targets."""
        job = appended(
            load("ci.yml")["pgrx"],
            {"name": "Planted", "run": "cargo fuzz build --sanitizer none"},
        )
        self.assertEqual(
            strings("ci.yml pgrx: calls cargo-fuzz and does not install it"),
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_new_belt_call_in_a_leg_is_named(self) -> None:
        """Planted: a leg starts running a belt linter."""
        job = appended(
            load("ci.yml")["pgrx"], {"name": "Planted", "run": "typos --format brief"}
        )
        self.assertEqual(
            strings("ci.yml pgrx: calls typos and does not install it"),
            uninstalled("ci.yml", "pgrx", job),
        )


def measures(workflow: str, job_id: str, job: Job) -> list[str]:
    """Name every way a job that must measure nothing installs or runs a tool.

    Returns:
        One line per mise-action step, caller-pins install, `mise run`,
        and tool the job's steps call; none for a job that only moves
        files.

    """
    found = [
        f"{workflow} {job_id}: installs tools with jdx/mise-action"
        for _ in mise_steps(job)
    ]
    body = bodies(job)
    if "caller_pins.py" in body:
        found.append(f"{workflow} {job_id}: installs the caller's build inputs")
    if re.search(r"(?<![\w-])mise\s+run\b", body):
        found.append(f"{workflow} {job_id}: runs a mise task")
    keys, caller = calls(job, carries=True)
    found += [f"{workflow} {job_id}: calls {t}" for t in sorted(keys | caller)]
    return found


def step(job: Job, name: str) -> Step:
    """Read one step of a job by its name.

    Returns:
        The step; empty when the job has no such step.

    """
    found = [s for s in workflow_yaml.steps(job) if field(s, "name") == name]
    return found[0] if found else {}


def names(job: Job) -> list[str]:
    """Name a job's steps, in order.

    Returns:
        Each step's `name`, "" where it has none.

    """
    return [field(s, "name") for s in workflow_yaml.steps(job)]


class CoverageFigures(unittest.TestCase):
    """The gate keeps its figures; the release job reads them (#1622)."""

    def test_the_gate_keeps_the_figures_for_the_commit_that_lands(self) -> None:
        """Merge group and default-branch push only, named for the commit."""
        kept = step(load("ci.yml")["gate"], "Keep the coverage figures for the release")
        when = flat(field(kept, "if"))
        self.assertIn("github.event_name == 'merge_group'", when)
        self.assertIn("github.event_name == 'push'", when)
        self.assertIn(
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)",
            when,
        )
        self.assertNotIn("refs/heads/main", when)
        self.assertNotIn("pull_request", when)
        self.assertEqual("coverage-figures-${{ github.sha }}", inputs(kept)["name"])
        self.assertEqual(
            "${{ runner.temp }}/coverage-reports/coverage-figures.txt",
            inputs(kept)["path"],
        )

    def test_the_release_job_reads_with_a_token_that_can(self) -> None:
        """The floor step has a token, and its job may read artifacts."""
        job = load("release.yml")["release-pr"]
        floor = step(job, "Re-derive the coverage floor")
        self.assertEqual(".org-canon/release/derive-coverage-floor.sh", floor["run"])
        self.assertEqual("${{ github.token }}", table(floor, "env")["GH_TOKEN"])
        self.assertEqual("read", table(job, "permissions").get("actions"))

    def test_every_caller_grants_the_read(self) -> None:
        """The template and the canon's own caller grant `actions: read`."""
        for path in (
            CANON / "workflow-templates" / "release.yml",
            WORKFLOWS / "self-release.yml",
        ):
            with self.subTest(caller=path.name):
                self.assertRegex(path.read_text("utf-8"), r"\n {6}actions: read\b")

    def test_each_pgrx_job_keeps_its_figure(self) -> None:
        """The pgrx job: the two events, the name with major and commit (#1692)."""
        job = load("ci.yml")["pgrx"]
        kept = step(job, "Keep the leg's coverage figure for the release")
        when = flat(field(kept, "if"))
        self.assertIn("github.event_name == 'merge_group'", when)
        self.assertIn("github.event_name == 'push'", when)
        self.assertIn(
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)",
            when,
        )
        self.assertNotIn("pull_request", when)
        self.assertEqual(
            "coverage-figures-pgrx-pg${{ matrix.major }}-${{ github.sha }}",
            inputs(kept)["name"],
        )
        self.assertEqual(
            "${{ runner.temp }}/coverage-reports/coverage-figures.txt",
            inputs(kept)["path"],
        )
        order = names(job)
        self.assertLess(
            order.index("Run the extension's suite at this major"),
            order.index("Keep the leg's coverage figure for the release"),
        )

    def test_the_figures_step_follows_the_gate(self) -> None:
        """Kept only after `mise run ci` passed: a red gate keeps nothing."""
        order = names(load("ci.yml")["gate"])
        self.assertLess(
            order.index("Run the gate"),
            order.index("Keep the coverage figures for the release"),
        )


class GateUploadMeasuresNothing(unittest.TestCase):
    """The coverage upload installs no measurement tool (#1528)."""

    def test_the_upload_installs_and_runs_nothing(self) -> None:
        """The real job: download, list, hand to Codecov."""
        for (workflow, job_id), why in GATE_NONE.items():
            with self.subTest(job=job_id, why=why):
                self.assertEqual(
                    strings(), measures(workflow, job_id, load(workflow)[job_id])
                )

    def test_the_upload_that_hung_on_main_is_named(self) -> None:
        """Planted: the job as it was, re-measuring with five tools."""
        job = appended(
            load("ci.yml")["coverage-upload"],
            {
                "name": "Install mise and the report's tools",
                "uses": "jdx/mise-action@b44d12e7d0d8ee7461bb893e1b1993500433185b",
                "with": {
                    "install_args": (
                        "jq aqua:taiki-e/cargo-llvm-cov python aqua:astral-sh/uv"
                    )
                },
            },
            {"name": "Generate the report", "run": "report=$(mise run coverage:check)"},
        )
        found = measures("ci.yml", "coverage-upload", job)
        self.assertEqual(
            strings(
                "ci.yml coverage-upload: installs tools with jdx/mise-action",
                "ci.yml coverage-upload: runs a mise task",
            ),
            found[:2],
        )
        # Followed into coverage:check and the measurement it runs.
        for tool in ("aqua:taiki-e/cargo-llvm-cov", "pipx:coverage", "go"):
            self.assertIn(f"ci.yml coverage-upload: calls {tool}", found)

    def test_a_measuring_call_in_the_upload_is_named(self) -> None:
        """Planted: the upload starts rendering a report itself."""
        job = appended(
            load("ci.yml")["coverage-upload"],
            {
                "name": "Planted",
                "run": "cargo llvm-cov --workspace --lcov --output-path lcov.info",
            },
        )
        self.assertEqual(
            strings(
                "ci.yml coverage-upload: calls aqua:taiki-e/cargo-llvm-cov",
                "ci.yml coverage-upload: calls rust",
            ),
            measures("ci.yml", "coverage-upload", job),
        )

    def test_no_gate_job_is_both_listed_and_measureless(self) -> None:
        """A job is a list, the whole config, or nothing: one of them."""
        none: set[tuple[str, str]] = set()
        self.assertEqual(none, set(GATE_NONE) & {*GATE_LISTS, *GATE_WHOLE})


# The variable a named-list job sets "false" in its own env (#1412).
# mise's task_run_auto_install is on by default, so without it `mise run`
# installs every other tool in scope before the task starts, and the
# list saves nothing in the jobs that run a task.
NO_TASK_INSTALL = "MISE_TASK_RUN_AUTO_INSTALL"


def task_auto_install(workflow: str, job_id: str, job: Job) -> list[str]:
    """Name a named-list job that leaves mise's task auto-install on.

    Returns:
        One line when the job's mise-action step names install_args and
        the job's own env does not turn task auto-install off.

    """
    (step,) = mise_steps(job)
    if not inputs(step).get("install_args"):
        return []
    if table(job, "env").get(NO_TASK_INSTALL) == "false":
        return []
    return [
        (
            f"{workflow} {job_id}: installs a named list but leaves mise's task"
            ' auto-install on; set MISE_TASK_RUN_AUTO_INSTALL: "false" in the'
            " job's env (#1412)"
        )
    ]


class TaskAutoInstall(unittest.TestCase):
    """A named list stays the whole install when a task runs (#1412)."""

    def test_every_named_list_job_turns_task_auto_install_off(self) -> None:
        """Every job in scope that installs a list, the exempt one aside."""
        for workflow, job_id in IN_SCOPE:
            with self.subTest(workflow=workflow, job=job_id):
                job = load(workflow)[job_id]
                self.assertEqual(strings(), task_auto_install(workflow, job_id, job))

    def test_a_job_without_the_setting_is_named(self) -> None:
        """Planted: the SBOM job, which runs audit:deny, loses the line."""
        job = load("publish.yml")["sbom"]
        decoded.as_object(job["env"], "env").pop(NO_TASK_INSTALL)
        self.assertEqual(
            strings(
                "publish.yml sbom: installs a named list but leaves mise's task"
                ' auto-install on; set MISE_TASK_RUN_AUTO_INSTALL: "false" in'
                " the job's env (#1412)"
            ),
            task_auto_install("publish.yml", "sbom", job),
        )

    def test_a_tool_only_a_task_calls_must_be_listed(self) -> None:
        """Planted: cargo-deny is reached only through `mise run audit:deny`."""
        job = planted_job("publish.yml", "sbom", " aqua:EmbarkStudios/cargo-deny")
        self.assertEqual(
            strings(
                "publish.yml sbom: calls aqua:EmbarkStudios/cargo-deny and does"
                " not install it"
            ),
            uninstalled("publish.yml", "sbom", job),
        )


class PythonCode(unittest.TestCase):
    """A Python helper's comments are not code, wherever they sit (#1496)."""

    def test_an_inline_suppression_is_not_a_call(self) -> None:
        """Measured on #1490: `# ruff: ignore[...]` after the code."""
        helper = (
            "import subprocess\n"
            'subprocess.run(["git", "status"], check=True)'
            "  # ruff: ignore[start-process-with-partial-path]\n"
        )
        reduced, _, _ = python_code(helper)
        self.assertEqual(
            'import subprocess\nsubprocess.run(["git", "status"], check=True)',
            reduced,
        )
        self.assertFalse(word("ruff", reduced))

    def test_a_bare_name_in_code_is_still_a_call(self) -> None:
        """Code before an inline comment is kept and still read."""
        helper = "import subprocess\nTOOL = ruff  # the linter\n"
        reduced, _, _ = python_code(helper)
        self.assertEqual("import subprocess\nTOOL = ruff", reduced)
        self.assertTrue(word("ruff", reduced))

    def test_a_hash_inside_a_string_is_code(self) -> None:
        """A `#` in a string literal is not a comment; the line stays whole."""
        line = 'subprocess.run(["sh", "-c", "echo # not a comment"])  # a comment'
        reduced, _, _ = python_code(f"import subprocess\n{line}\n")
        self.assertEqual(
            'import subprocess\nsubprocess.run(["sh", "-c", "echo # not a comment"])',
            reduced,
        )

    def test_an_unreadable_helper_fails_by_name(self) -> None:
        """Never "" for a helper the tokenizer cannot read."""
        with self.assertRaisesRegex(
            ValueError, r"^release/broken\.py: the tokenizer cannot read it \("
        ):
            python_code('import subprocess\nx = """unterminated\n', "release/broken.py")


PLANTED_RED = ["attach-release.yml attach: calls ruff and does not install it"]


def planted_uninstalled(helper: str) -> list[str]:
    """Plant `helper` as release/planted-call.py and read the attach job (#1499).

    Returns:
        What `uninstalled` says about the attach job running it.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = hermetic.canon(tmp, stubs={"release/planted-call.py": helper})
        job = appended(
            load("attach-release.yml")["attach"],
            {
                "name": "Planted",
                "run": 'python3 "${GITHUB_WORKSPACE}/release/planted-call.py"',
            },
        )
        with mock.patch(f"{__name__}.CANON", root):
            return uninstalled("attach-release.yml", "attach", job)


UNSET_RED = ["attach-release.yml attach: calls lefthook and does not install it"]


def planted_shell(helper: str) -> list[str]:
    """Plant `helper` as release/planted-call.sh and read the attach job (#1782).

    Returns:
        What `uninstalled` says about the attach job running it.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = hermetic.canon(tmp, stubs={"release/planted-call.sh": helper})
        job = appended(
            load("attach-release.yml")["attach"],
            {
                "name": "Planted",
                "run": 'bash "${GITHUB_WORKSPACE}/release/planted-call.sh"',
            },
        )
        with mock.patch(f"{__name__}.CANON", root):
            return uninstalled("attach-release.yml", "attach", job)


class UnsetMessages(unittest.TestCase):
    """A `${NAME:?message}` message is prose, not a call (#1782).

    Each planted shell helper names lefthook, which the attach job does
    not install, in the message of an unset expansion. The shell prints
    that message and exits; it runs nothing in it.
    """

    def test_a_local_assignment_message_is_not_a_call(self) -> None:
        """`local x="${V:?…}"`, the pgrx-env.sh shape."""
        helper = 'f() {\n  local x="${V:?run: lefthook run post-checkout}"\n}\n'
        self.assertEqual(strings(), planted_shell(helper))

    def test_a_colon_command_message_is_not_a_call(self) -> None:
        """`: "${V:?…}"`, the identity.sh shape."""
        helper = ': "${V:?run: lefthook run post-checkout}"\n'
        self.assertEqual(strings(), planted_shell(helper))

    def test_a_sourced_path_message_is_not_a_call(self) -> None:
        """`. "${V:?…}/f.sh"`, the audit-badges.sh shape; `?` alone too."""
        for op in (":?", "?"):
            with self.subTest(op=op):
                helper = f'. "${{V{op}run: lefthook run post-checkout}}/f.sh"\n'
                self.assertEqual(strings(), planted_shell(helper))

    def test_a_substitution_in_a_message_is_a_call(self) -> None:
        """`${V:?$(lefthook …)}`: the shell runs it, so it stays code."""
        for sub in ("$(lefthook version)", "`lefthook version`"):
            with self.subTest(sub=sub):
                helper = f': "${{V:?{sub}}}"\n'
                self.assertEqual(UNSET_RED, planted_shell(helper))


class ProseFromTheParser(unittest.TestCase):
    """What is prose in shell is read from shfmt's tree (#1785).

    The call rows are the shapes the line regex dropped whole; the
    prose rows are the remedy lines it existed to drop.
    """

    def test_a_call_beside_prose_is_named(self) -> None:
        """A quoted substitution and a pipe out of a print are calls."""
        for helper in (
            'x="$(lefthook version)"\n',
            'x="`lefthook version`"\n',
            'echo "$(lefthook version)"\n',
            "echo ok | lefthook version\n",
        ):
            with self.subTest(helper=helper):
                self.assertEqual(UNSET_RED, planted_shell(helper))

    def test_a_remedy_line_names_nothing(self) -> None:
        """A quoted literal assigned or printed is prose."""
        for helper in (
            'emsg+="run lefthook install"\n',
            'echo "run lefthook install"\n',
        ):
            with self.subTest(helper=helper):
                self.assertEqual(strings(), planted_shell(helper))

    def test_the_jq_calls_on_assignment_lines_are_read(self) -> None:
        """The two `jq` calls the line regex dropped are seen."""
        for rel in ("mise/roster-reconcile.sh", "release/inventory-digests.sh"):
            with self.subTest(rel=rel):
                text = (CANON / rel).read_text("utf-8")
                self.assertTrue(word("jq", code(text)), rel)

    def test_each_span_is_blanked_or_kept(self) -> None:
        """One row per branch: what goes to spaces, what stays."""
        rows = [
            # A print's arguments go; its command word stays.
            ('echo "run: typos --fix"', "echo" + " " * 19),
            ("printf '%s' x", "printf" + " " * 7),
            # A substitution inside a blanked span stays: the shell runs it.
            ('echo "a $(taplo fmt) b"', "echo    $(taplo fmt)   "),
            ('printf "%s" <(taplo fmt)', "printf      <(taplo fmt)"),
            # A command after a print's pipe is its own call.
            ("echo x | sed s/x/y/", "echo   | sed s/x/y/"),
            # Not a print: the first word is not the literal.
            ('"echo" lefthook', '"echo" lefthook'),
            # A value made only of quoted parts goes, double or single.
            ('emsg+="run lefthook install"', "emsg+=                      "),
            ("x='lefthook'", "x=          "),
            ("x=\"a\"'b'", "x=      "),
            ('local y="lefthook"', "local y=          "),
            # ... keeping its substitution, backquoted or not.
            ('x="$(jq -r .x)" || exit 1', "x= $(jq -r .x)  || exit 1"),
            ('x="`jq`"', "x= `jq` "),
            # A value with an unquoted part stays whole.
            ('x=a"lefthook"', 'x=a"lefthook"'),
            ("x=$(jq)", "x=$(jq)"),
            # An unset expansion's message goes, `:?` or `?`.
            (': "${V:?run: lefthook}"', ': "${V:?             }"'),
            (': "${V?run: lefthook}"', ': "${V?             }"'),
            ('x="${V:?$(taplo fmt)}"', "x=      $(taplo fmt)  "),
            # Any other operator's word is code.
            (': "${V:-lefthook}"', ': "${V:-lefthook}"'),
            # A comment line goes; a heredoc body stays.
            ("# lefthook\nls", "ls"),
            ("cat <<EOF\nlefthook\nEOF", "cat <<EOF\nlefthook\nEOF"),
            # Every newline a span holds is kept.
            ('echo "a\nb" x', "echo   \n    "),
        ]
        for text, want in rows:
            with self.subTest(text=text):
                self.assertEqual(want, code(text))

    def test_text_shfmt_refuses_is_named(self) -> None:
        """No fallback: a parse error is a ValueError naming shfmt's message."""
        with self.assertRaisesRegex(
            ValueError, r"^shfmt cannot parse the text: 1:1: `if` must be followed"
        ):
            code("if then\n")

    def test_a_job_is_only_uncommented(self) -> None:
        """A whole job's YAML loses its comment lines and nothing else."""
        job = '  # lefthook\n  run: echo "x"\n  x: ${{ y }}'
        self.assertEqual('  run: echo "x"\n  x: ${{ y }}', uncommented(job))


class ArgvCalls(unittest.TestCase):
    """A command a Python helper runs is a call (#1499).

    Each planted helper sits beside the real release scripts in a scratch
    canon root whose mise/ is the real belt, and the attach job gains a
    step that runs it. The job installs no ruff.
    """

    def test_a_list_argv_is_a_call(self) -> None:
        """`subprocess.run(["ruff", "check"])`: red on main, which saw nothing."""
        helper = 'import subprocess\nsubprocess.run(["ruff", "check"], check=True)\n'
        self.assertEqual(PLANTED_RED, planted_uninstalled(helper))

    def test_a_string_command_is_a_call(self) -> None:
        """The first word of a string argv is the command."""
        helper = 'import subprocess\nsubprocess.run("ruff check .", shell=True)\n'
        self.assertEqual(PLANTED_RED, planted_uninstalled(helper))

    def test_a_module_constant_argv_is_a_call(self) -> None:
        """A module name bound to the command, plain or through which()."""
        for binding in ('"ruff"', 'shutil.which("ruff") or "ruff"'):
            with self.subTest(binding=binding):
                helper = (
                    "import shutil\nimport subprocess\n"
                    f"RUFF = {binding}\n"
                    'subprocess.check_output([RUFF, "check"])\n'
                )
                self.assertEqual(PLANTED_RED, planted_uninstalled(helper))

    def test_a_name_in_a_message_is_not_a_call(self) -> None:
        """A tool named in a string or a list no subprocess call runs.

        Only a subprocess call's argv is read as a command. A name inside
        a message, after its first word, is still a bare word to `word()`
        as it is in a shell body; this row keeps to what argv adds.
        """
        helper = (
            "import subprocess\n"
            'print("ruff: not run here")\n'
            'print(["ruff", "check"])\n'
            'subprocess.run(["git", "status"], check=True)\n'
        )
        self.assertEqual(strings(), planted_uninstalled(helper))

    def test_every_import_form_is_read(self) -> None:
        """An aliased module and an imported function, all four calls."""
        helper = (
            "import subprocess as sp\n"
            "from subprocess import Popen as Spawn, check_call\n"
            'sp.run(["one"])\nsp.check_output(("two", "x"))\n'
            'check_call(args=["three"])\nSpawn("four x")\n'
        )
        self.assertEqual(
            ({"one", "two", "three", "four"}, []),
            argv_commands(ast.parse(helper)),
        )

    def test_the_dynamic_helpers_are_the_named_ones(self) -> None:
        """The real tree: every unreadable argv is named, and only those."""
        found = {name for name, text in helpers().items() if python_code(text)[2]}
        named: list[str] = sorted(DYNAMIC)
        unread: list[str] = sorted(found)
        self.assertEqual(named, unread)
        self.assertEqual(strings(), unreadable(helpers()))

    def test_an_unnamed_dynamic_helper_is_red_by_name(self) -> None:
        """A new helper that runs a variable is named, with its call."""
        helper = "import subprocess\n\n\ndef go(args):\n    subprocess.run(args)\n"
        self.assertEqual(
            strings(
                "release/planted.py: runs a command this check cannot read"
                " (line 5: args); name it in DYNAMIC with why"
            ),
            unreadable({"release/planted.py": helper}),
        )


class OneReader(unittest.TestCase):
    """release/caller_pins.py is the canon's one reader of a caller's pins."""

    def test_no_other_reader_in_release_or_the_workflows(self) -> None:
        """Every script under release/ and every workflow, read whole."""
        root = CANON
        paths = [
            *sorted((root / "release").glob("*.sh")),
            *sorted((root / "release").glob("*.py")),
            *sorted((root / ".github" / "workflows").glob("*.yml")),
        ]
        found = [ln for p in paths for ln in readers(p, p.read_text("utf-8"), root)]
        self.assertEqual(strings(), found)

    def test_a_planted_reader_is_named(self) -> None:
        """Planted: the inline reader the pgrx build carried before #1333."""
        path = WORKFLOWS / "build-pgrx-extension.yml"
        text = path.read_text("utf-8") + (
            '          tools = t.get("tools", {})\n'
            '          pgrx = tools.get("cargo:cargo-pgrx", "")\n'
        )
        self.assertEqual(
            strings(
                ".github/workflows/build-pgrx-extension.yml: reads a caller's"
                " mise config itself; read it through release/caller_pins.py"
                " (#1333)"
            ),
            readers(path, text),
        )

    def test_the_pgrx_build_calls_the_reader(self) -> None:
        """Both pgrx readers moved onto the helper, not merely deleted."""
        for path in (
            WORKFLOWS / "build-pgrx-extension.yml",
            CANON / "release" / "generate-pgrx-upgrade.sh",
        ):
            with self.subTest(path=path.name):
                self.assertIn("caller_pins.py", path.read_text("utf-8"))


# The canon's helpers run under the belt's Python (#1543). The runner's
# python3 is pinned by nothing, and on 2026-11-18 it refused the belt's
# own 3.14 spelling in release/registry.py and broke every image publish
# in the org. So a `run:` step starts a tracked helper one way only,
# `"${ORG_PYTHON}" <helper.py>`, and the job has run the canon action,
# which installs that interpreter and exports it, before any step uses
# the name.
ACTIONS = CANON / ".github" / "actions"
ORG_PYTHON_START = re.compile(r'"\$\{ORG_PYTHON\}"\s+$')
INTERPRETER = re.compile(r"(?<![\w./$-])(python[\d.]*)\s+$")
COMMAND_POSITION = re.compile(r"(?:^|[;&|(]|\$\()\s*$")


def tracked_helpers() -> set[str]:
    """Name every tracked Python file by its file name.

    Returns:
        The names of the canon's tracked `*.py`.

    """
    return {Path(p).name for p in hermetic.tracked(CANON, "*.py")}


def helper_starts(rel: str, job_id: str, job: Job, tracked: set[str]) -> list[str]:
    """Name each step that starts a tracked helper but not as `"${ORG_PYTHON}"`.

    A helper path is a start where it stands in a command's place (a
    line's first word, or after `;`, `|`, `&`, `(` or `$(`), or after an
    interpreter's name. Anywhere else it is an argument.

    Returns:
        One line per such start, naming the file, job, step and helper.

    """
    out = []
    for step in workflow_yaml.steps(job):
        body = re.sub(r"\$\{\{.*?\}\}", "${{expr}}", field(step, "run"))
        body = code(body.replace("\\\n", " "))
        for line in body.splitlines():
            for token in re.finditer(r"\S*\.py\b[\"']?", line):
                name = token.group(0).rstrip("\"')").rsplit("/", 1)[-1]
                if name not in tracked:
                    continue
                before = line[: token.start()].lstrip("'")
                if ORG_PYTHON_START.search(before):
                    continue
                interpreter = INTERPRETER.search(before)
                if interpreter:
                    how = f"with the runner's {interpreter.group(1)}"
                elif COMMAND_POSITION.search(before):
                    how = "by its own first line"
                else:
                    continue
                out.append(
                    f"{rel} {job_id}: step '{field(step, 'name') or '?'}' starts"
                    f' {name} {how}; run it as "${{ORG_PYTHON}}" {name} (#1543)'
                )
    return out


def uncanoned(rel: str, job_id: str, job: Job) -> list[str]:
    """Name a job that uses ORG_PYTHON before the canon action exports it.

    Returns:
        One line when a step names ORG_PYTHON and no earlier step runs
        `$/.github/actions/canon`; none otherwise.

    """
    canon = False
    for step in workflow_yaml.steps(job):
        if field(step, "uses") == "$/.github/actions/canon":
            canon = True
        elif any("ORG_PYTHON" in v for v in scalars(step)) and not canon:
            return [
                (
                    f"{rel} {job_id}: step '{field(step, 'name') or '?'}' uses"
                    " ORG_PYTHON and no step before it runs"
                    " $/.github/actions/canon, which exports it (#1543)"
                )
            ]
    return []


def interpreter_findings(texts: dict[str, str], tracked: set[str]) -> list[str]:
    """Hold every workflow and action file to the one way to start a helper.

    Returns:
        Every red line, file by file.

    """
    out = []
    for rel, source in texts.items():
        document = parsed(source)
        if rel.startswith(".github/actions/"):
            runs = decoded.as_object(document["runs"], f"{rel} runs")
            out += helper_starts(rel, "runs", runs, tracked)
            continue
        for job_id, job in workflow_yaml.jobs(document).items():
            out += helper_starts(rel, job_id, job, tracked)
            out += uncanoned(rel, job_id, job)
    return out


def workflow_and_action_texts() -> dict[str, str]:
    """Read every workflow and action file the canon carries.

    Returns:
        Path relative to the canon -> text.

    """
    root = CANON
    workflows, actions = root / ".github" / "workflows", root / ".github" / "actions"
    paths = [*sorted(workflows.glob("*.yml")), *sorted(actions.glob("*/action.yml"))]
    return {str(p.relative_to(root)): p.read_text("utf-8") for p in paths}


PLANTED_JOB = """jobs:
  build:
    runs-on: ubuntu-26.04
    steps:
      - name: Resolve the canon tree
        uses: $/.github/actions/canon
"""


class BeltPython(unittest.TestCase):
    """Every helper a workflow or action starts runs under ORG_PYTHON (#1543)."""

    def test_every_helper_start_names_org_python(self) -> None:
        """The tree: no step starts a tracked helper any other way."""
        self.assertEqual(
            strings(),
            interpreter_findings(workflow_and_action_texts(), tracked_helpers()),
        )

    def test_the_scan_reaches_the_helper_starts(self) -> None:
        """Not vacuous: the tree starts helpers under ORG_PYTHON, many times."""
        self.assertLessEqual(
            {"registry.py", "caller_pins.py", "verdict.py"}, tracked_helpers()
        )
        starts = sum(
            text.count('"${ORG_PYTHON}" ')
            for text in workflow_and_action_texts().values()
        )
        self.assertGreater(starts, 40)

    def test_the_runners_python3_is_named(self) -> None:
        """Planted: the image publish's call before #1543."""
        text = PLANTED_JOB + (
            "      - name: Push the image\n"
            "        run: |\n"
            '          python3 .org-canon/release/registry.py push --image "${IMAGE}"\n'
        )
        self.assertEqual(
            strings(
                ".github/workflows/x.yml build: step 'Push the image' starts"
                " registry.py with the runner's python3; run it as"
                ' "${ORG_PYTHON}" registry.py (#1543)'
            ),
            interpreter_findings({".github/workflows/x.yml": text}, {"registry.py"}),
        )

    def test_a_start_by_first_line_is_named(self) -> None:
        """Planted: a helper executed by its own `#!` line, in both shapes."""
        text = PLANTED_JOB + (
            "      - name: Attach\n"
            "        run: .org-canon/release/attach_publish.py\n"
            "      - name: Fetch\n"
            "        run: |\n"
            "          prev=$(.org-canon/release/pgrx/previous_release.py fetch \\\n"
            '            --tag "${TAG}")\n'
        )
        self.assertEqual(
            strings(
                (
                    ".github/workflows/x.yml build: step 'Attach' starts"
                    " attach_publish.py by its own first line; run it as"
                    ' "${ORG_PYTHON}" attach_publish.py (#1543)'
                ),
                (
                    ".github/workflows/x.yml build: step 'Fetch' starts"
                    " previous_release.py by its own first line; run it as"
                    ' "${ORG_PYTHON}" previous_release.py (#1543)'
                ),
            ),
            interpreter_findings(
                {".github/workflows/x.yml": text},
                {"attach_publish.py", "previous_release.py"},
            ),
        )

    def test_a_start_in_an_action_is_named(self) -> None:
        """Planted: an action file is held as a workflow is."""
        text = (
            "name: x\nruns:\n  using: composite\n  steps:\n"
            "    - name: Derive\n      shell: bash\n"
            '      run: python3 "${GITHUB_ACTION_PATH}/derive.py"\n'
        )
        self.assertEqual(
            strings(
                ".github/actions/x/action.yml runs: step 'Derive' starts"
                " derive.py with the runner's python3; run it as"
                ' "${ORG_PYTHON}" derive.py (#1543)'
            ),
            interpreter_findings({".github/actions/x/action.yml": text}, {"derive.py"}),
        )

    def test_org_python_before_the_canon_action_is_named(self) -> None:
        """Planted: the name used where nothing has exported it yet."""
        text = (
            "jobs:\n  plan:\n    runs-on: ubuntu-26.04\n    steps:\n"
            "      - name: Plan\n"
            "        run: '\"${ORG_PYTHON}\" release/matrix.py base-images'\n"
            "      - name: Resolve the canon tree\n"
            "        uses: $/.github/actions/canon\n"
        )
        self.assertEqual(
            strings(
                ".github/workflows/x.yml plan: step 'Plan' uses ORG_PYTHON and no"
                " step before it runs $/.github/actions/canon, which exports it"
                " (#1543)"
            ),
            interpreter_findings({".github/workflows/x.yml": text}, {"matrix.py"}),
        )

    def test_an_argument_an_untracked_file_and_inline_code_are_not_starts(
        self,
    ) -> None:
        """Only a tracked helper in a command's place is a start."""
        text = PLANTED_JOB + (
            "      - name: Others\n"
            "        run: |\n"
            "          git ls-files '*.py' | wc -l\n"
            "          ./caller-build.py --release\n"
            "          python3 -c 'print(1)'\n"
            '          "${ORG_PYTHON}" .org-canon/mise/gate_tools.py'
            " --helper registry.py\n"
            "          # python3 .org-canon/release/registry.py tag\n"
        )
        self.assertEqual(
            strings(),
            interpreter_findings(
                {".github/workflows/x.yml": text}, {"registry.py", "gate_tools.py"}
            ),
        )


JOB_TOKENS = ("github.token", "secrets.GITHUB_TOKEN")
STELE_VERB = re.compile(r"(?<![\w/.:-])stele\s+[a-z]")
ORG_FLAG = re.compile(r"(?<![\w-])--org\b")


class StepHeaders(unittest.TestCase):
    """A commented block header hides no body from these laws (#1837).

    Each header form is read by workflow_yaml, whose own rows hold them;
    these hold the tree's commented header and a start beneath one.
    """

    def test_the_commented_header_in_the_tree_is_read(self) -> None:
        """source-attest's `run: | # zizmor: ...` step is read whole."""
        action = workflow_yaml.document(
            CANON / ".github/actions/source-attest/action.yml"
        )
        runs = decoded.as_object(action["runs"], "runs")
        found = [
            s
            for s in workflow_yaml.steps(runs)
            if field(s, "name") == "Install the belt's cosign and stele"
        ]
        self.assertEqual(1, len(found))
        self.assertIn("set -euo pipefail", field(found[0], "run"))

    def test_a_start_under_a_commented_header_is_named(self) -> None:
        """Planted: a helper started in a commented-header body is red."""
        text = (
            "name: x\nruns:\n  using: composite\n  steps:\n"
            "    - name: Derive\n      shell: bash\n"
            "      run: | # zizmor: ignore[github-env]\n"
            '        python3 "${GITHUB_ACTION_PATH}/derive.py"\n'
        )
        self.assertEqual(
            strings(
                ".github/actions/x/action.yml runs: step 'Derive' starts"
                " derive.py with the runner's python3; run it as"
                ' "${ORG_PYTHON}" derive.py (#1543)'
            ),
            interpreter_findings({".github/actions/x/action.yml": text}, {"derive.py"}),
        )


def population_reads_on_job_token(texts: dict[str, str]) -> list[str]:
    """Name each stele `--org` step whose GH_TOKEN is the job token (#1602).

    A population read reconciles the declared population against the org
    listing; a job token lists only what its own repository sees, so the
    private member is never in it and the read refuses. The token a step
    holds is its own `env`, else its job's, else the workflow's.

    Returns:
        `workflow: job / step` per finding.

    """
    found = []
    for rel, source in sorted(texts.items()):
        document = parsed(source)
        for job_id, job in workflow_yaml.jobs(document).items():
            for step in workflow_yaml.steps(job):
                body = field(step, "run").splitlines()
                run = "\n".join(ln for ln in body if not ln.startswith("#"))
                if not (STELE_VERB.search(run) and ORG_FLAG.search(run)):
                    continue
                token = (
                    table(step, "env").get("GH_TOKEN")
                    or table(job, "env").get("GH_TOKEN")
                    or table(document, "env").get("GH_TOKEN")
                )
                if token and any(t in token for t in JOB_TOKENS):
                    found.append(f"{rel}: {job_id} / {field(step, 'name') or '?'}")
    return found


class PopulationToken(unittest.TestCase):
    """A population read holds a token that sees the population (#1602)."""

    def test_no_stele_org_step_reads_on_the_job_token(self) -> None:
        """The tree: every `stele ... --org` step holds a minted token."""
        root = CANON
        texts = {
            str(p.relative_to(root)): p.read_text("utf-8")
            for p in sorted((root / ".github" / "workflows").glob("*.yml"))
        }
        self.assertEqual(strings(), population_reads_on_job_token(texts))

    def test_the_scan_reaches_the_population_reads(self) -> None:
        """Not vacuous: the tree's population reads are all in view."""
        root = CANON
        texts = {
            str(p.relative_to(root)): p.read_text("utf-8")
            for p in sorted((root / ".github" / "workflows").glob("*.yml"))
        }
        planted = {
            rel: text.replace("steps.token.outputs.token", "github.token")
            for rel, text in texts.items()
        }
        reads = population_reads_on_job_token(planted)
        self.assertIn(
            ".github/workflows/vex-attest.yml: plan / "
            "Resolve subjects from the products the statements name",
            reads,
        )
        self.assertGreaterEqual(len(reads), 4)

    def test_the_job_token_is_named_at_every_level(self) -> None:
        """Planted: step, job and workflow env, and the secrets spelling."""
        run = (
            "        run: |\n"
            '          args=(--org "${OWNER}")\n'
            '          stele derive vex-subjects "${args[@]}"\n'
        )
        step = "    steps:\n      - name: Read\n"
        texts = {
            "a.yml": "jobs:\n  a:\n"
            + step
            + "        env:\n          GH_TOKEN: ${{ github.token }}\n"
            + run,
            "b.yml": "jobs:\n  b:\n    env:\n      GH_TOKEN: "
            "${{ secrets.GITHUB_TOKEN }}\n" + step + run,
            "c.yml": "env:\n  GH_TOKEN: ${{ github.token }}\njobs:\n  c:\n"
            + step
            + run,
            "d.yml": "env:\n  GH_TOKEN: ${{ github.token }}\njobs:\n  d:\n"
            + step
            + "        env:\n          GH_TOKEN: ${{ steps.token.outputs.token }}\n"
            + run,
            "e.yml": "jobs:\n  e:\n"
            + step
            + "        env:\n          GH_TOKEN: ${{ github.token }}\n"
            "        run: |\n          # stele level --org x\n"
            "          gh release list --org-less\n",
        }
        self.assertEqual(
            strings("a.yml: a / Read", "b.yml: b / Read", "c.yml: c / Read"),
            population_reads_on_job_token(texts),
        )


def mint_step(workflow: str, job_id: str) -> tuple[int, Step]:
    """Find a job's audit App mint step.

    Returns:
        The step's index in the job and the step.

    """
    found = [
        (i, s)
        for i, s in enumerate(workflow_yaml.steps(load(workflow)[job_id]))
        if "actions/create-github-app-token" in field(s, "uses")
    ]
    return found[0] if found else (-1, {})


VEX_GRANTS = {
    "permission-contents": "read",
    "permission-metadata": "read",
    "permission-attestations": "read",
}


class PopulationGrants(unittest.TestCase):
    """vex-attest's population read is granted three reads, no more (#1602)."""

    def test_the_vex_attest_mint_names_three_reads(self) -> None:
        """Contents, metadata and attestations: no review history."""
        _, mint = mint_step("vex-attest.yml", "plan")
        grants: dict[str, str] = {
            k: v for k, v in inputs(mint).items() if k.startswith("perm")
        }
        self.assertEqual(VEX_GRANTS, grants)


class VexAttestToken(unittest.TestCase):
    """vex-attest's plan job mints the audit App's read token (#1602)."""

    def test_the_plan_mints_before_the_step_that_reads(self) -> None:
        """The mint precedes the derivation, which holds its output."""
        at, mint = mint_step("vex-attest.yml", "plan")
        plan = workflow_yaml.steps(load("vex-attest.yml")["plan"])
        reads = [i for i, s in enumerate(plan) if field(s, "id") == "collect"]
        self.assertEqual(1, len(reads))
        self.assertEqual("token", field(mint, "id"))
        self.assertLess(at, reads[0])
        self.assertEqual(
            "${{ steps.token.outputs.token }}",
            table(plan[reads[0]], "env")["GH_TOKEN"],
        )

    def test_the_mint_mirrors_the_audit_walks(self) -> None:
        """Same pin, same client and key, owner scope as the audit's mint."""
        _, mint = mint_step("vex-attest.yml", "plan")
        _, audit = mint_step("audit.yml", "blast-radius")
        self.assertEqual(field(audit, "uses"), field(mint, "uses"))
        keys = ("client-id", "private-key", "owner")
        audits: dict[str, str | None] = {k: inputs(audit).get(k) for k in keys}
        mints: dict[str, str | None] = {k: inputs(mint).get(k) for k in keys}
        self.assertEqual(audits, mints)

    def test_the_signing_jobs_never_see_the_token(self) -> None:
        """Only the plan job names the App or its token."""
        for job_id, job in load("vex-attest.yml").items():
            if job_id == "plan":
                continue
            values = "\n".join(scalars(job))
            self.assertNotIn("AUDIT_APP", values, job_id)
            self.assertNotIn("steps.token", values, job_id)


# Each environment's secrets, as the forge issues them (measured
# 2026-11-20: the repository and the organisation hold none of these).
ENVIRONMENT_SECRETS = {
    "audit": ("AUDIT_APP_PRIVATE_KEY", "AUDIT_TOKEN", "PACKAGES_TOKEN"),
    "source-attest": ("SOURCE_RULES_TOKEN",),
}


def environment(job: Job) -> str | None:
    """Read a job's `environment:`, in either of its two forms.

    Returns:
        The environment's name, or None where the job declares none.

    """
    declared = job.get("environment")
    if isinstance(declared, dict):
        return field(decoded.as_object(declared, "environment"), "name") or None
    return field(job, "environment") or None


def secrets_outside_environment(texts: dict[str, str]) -> list[str]:
    """Name each job that reads an environment's secret outside it (#1602).

    A secret that lives only in an environment is issued to a job that
    declares that environment, and to any other job as an empty string.

    Returns:
        `workflow: job reads SECRET outside environment ENV` per finding.

    """
    found: list[str] = []
    for rel, source in sorted(texts.items()):
        for job_id, job in workflow_yaml.jobs(parsed(source)).items():
            body = uncommented("\n".join(scalars(job)))
            has = environment(job)
            found.extend(
                f"{rel}: {job_id} reads {name} outside environment {env}"
                for env, names in ENVIRONMENT_SECRETS.items()
                for name in names
                if f"secrets.{name}" in body and has != env
            )
    return found


class EnvironmentSecrets(unittest.TestCase):
    """A job reading an environment's secret declares it (#1602)."""

    def test_every_job_declares_its_secrets_environment(self) -> None:
        """The tree: no tracked workflow reads a key it is not issued."""
        root = CANON
        texts = {
            rel: (root / rel).read_text("utf-8")
            for rel in hermetic.tracked(root, ".github/workflows/*.yml")
        }
        self.assertEqual(strings(), secrets_outside_environment(texts))

    def test_the_scan_reaches_the_environment_jobs(self) -> None:
        """Not vacuous: stripping every declaration names each reader."""
        root = CANON
        texts = {
            rel: re.sub(
                r"^    environment: .*\n",
                "",
                (root / rel).read_text("utf-8"),
                flags=re.MULTILINE,
            )
            for rel in hermetic.tracked(root, ".github/workflows/*.yml")
        }
        found = secrets_outside_environment(texts)
        self.assertIn(
            ".github/workflows/vex-attest.yml: plan reads AUDIT_APP_PRIVATE_KEY"
            " outside environment audit",
            found,
        )
        self.assertIn(
            ".github/workflows/source-attest.yml: claims reads"
            " SOURCE_RULES_TOKEN outside environment source-attest",
            found,
        )
        self.assertGreaterEqual(len(found), 10)

    def test_each_form_and_a_wrong_environment_are_read(self) -> None:
        """Planted: none, the wrong one, both forms, a comment."""
        step = "    steps:\n      - run: x\n        env:\n"
        key = "          K: ${{ secrets.AUDIT_TOKEN }}\n"
        texts = {
            "a.yml": "jobs:\n  a:\n" + step + key,
            "b.yml": "jobs:\n  b:\n    environment: publish\n" + step + key,
            "c.yml": "jobs:\n  c:\n    environment: audit # why\n" + step + key,
            "d.yml": "jobs:\n  d:\n    environment:\n      name: audit\n" + step + key,
            "e.yml": "jobs:\n  e:\n" + step + "        # secrets.AUDIT_TOKEN\n",
        }
        self.assertEqual(
            strings(
                "a.yml: a reads AUDIT_TOKEN outside environment audit",
                "b.yml: b reads AUDIT_TOKEN outside environment audit",
            ),
            secrets_outside_environment(texts),
        )


class GateCoverageReports(unittest.TestCase):
    """The gate keeps coverage:check's reports from outside the tree (#1628).

    The reports were written into the checkout, where nothing ignores
    them and one `git add -A` commits them. The gate names a directory
    under the runner's temporary directory, and keeps that directory.
    """

    REPORTS = "${{ runner.temp }}/coverage-reports"

    def test_the_gate_names_the_directory_its_artifact_keeps(self) -> None:
        """The run step's variable is the artifact step's path."""
        gate = workflow_yaml.steps(load("ci.yml")["gate"])
        runs = [s for s in gate if field(s, "run").strip() == RUN_LEG]
        keeps = [s for s in gate if inputs(s).get("name") == "coverage-reports"]
        self.assertEqual(1, len(runs))
        self.assertEqual(1, len(keeps))
        self.assertEqual(
            self.REPORTS, table(runs[0], "env").get("ORG_COVERAGE_REPORTS")
        )
        self.assertEqual(self.REPORTS, inputs(keeps[0]).get("path"))

    def test_each_pgrx_leg_names_the_directory_its_artifact_keeps(self) -> None:
        """The leg's suite writes where its artifact step reads (#1656)."""
        leg = workflow_yaml.steps(load("ci.yml")["pgrx"])
        runs = [s for s in leg if field(s, "run").strip() == "mise run test:pgrx"]
        # The badge's report upload; the figures the release reads are
        # kept by a step of their own (#1692), held in CoverageFigures.
        keeps = [
            s
            for s in leg
            if "upload-artifact" in field(s, "uses")
            and inputs(s).get("name", "").startswith("coverage-reports")
        ]
        self.assertEqual(1, len(runs))
        self.assertEqual(1, len(keeps))
        self.assertEqual(
            self.REPORTS, table(runs[0], "env").get("ORG_COVERAGE_REPORTS")
        )
        self.assertEqual(self.REPORTS, inputs(keeps[0]).get("path"))
        self.assertEqual(
            "coverage-reports-pgrx-pg${{ matrix.major }}", inputs(keeps[0]).get("name")
        )

    def test_the_badge_job_downloads_beside_the_checkout(self) -> None:
        """Every report artifact lands in the workspace, where the list reads it.

        The gate's and each pgrx leg's (#1656), merged: their file names
        never collide.
        """
        upload = workflow_yaml.steps(load("ci.yml")["coverage-upload"])
        fetch = [s for s in upload if "download-artifact" in field(s, "uses")]
        self.assertEqual(1, len(fetch))
        self.assertEqual("coverage-reports*", inputs(fetch[0]).get("pattern"))
        self.assertEqual("true", inputs(fetch[0]).get("merge-multiple"))
        self.assertNotIn("path", inputs(fetch[0]))


# THE GATE'S THREE LEGS (#1740). One job, a matrix over the legs, its
# setup written once; each leg runs its own belt task, and a step that
# belongs to one leg says so in its `if:`. The cache steps' legs are held
# in test_gate_caches.py beside the caches.
GATE_LEGS = ("lint", "test", "properties")
RUN_LEG = 'mise run "ci:${LEG}"'
LINT_ONLY = "matrix.leg == 'lint'"
TEST_ONLY = "matrix.leg == 'test'"
LEG_BOUND = {
    "A merge group is the pull request's current head": LINT_ONLY,
    "The squash message is the PR title and body — hold it to the commit canon": (
        LINT_ONLY
    ),
    "Keep the coverage reports for the badge (default branch only)": TEST_ONLY,
    "Keep the coverage figures for the release": TEST_ONLY,
}


def flat(value: str) -> str:
    """Collapse a value's whitespace, as a folded scalar does.

    Returns:
        The value on one line.

    """
    return " ".join(value.split())


def leg_findings(text: str) -> list[str]:
    """Hold ci.yml's gate to its three legs, and its readers to the matrix.

    Returns:
        One line per breach: the gate not a matrix of the three legs, its
        legs cancelling one another, its run step not this leg's task, a
        one-leg step run on every leg, a job output a matrix cannot keep,
        `ci` not needing the gate, and the badge job reading its output.

    """
    found = workflow_yaml.jobs(parsed(text))
    gate = found["gate"]
    strategy = decoded.as_object(gate.get("strategy") or {}, "strategy")
    matrix = decoded.as_object(strategy.get("matrix") or {}, "matrix")
    out = []
    if matrix.get("leg") != list(GATE_LEGS):
        out.append(f"gate: not a matrix of the legs {list(GATE_LEGS)}")
    if strategy.get("fail-fast") is not False:
        out.append("gate: fail-fast is not false, so one red leg cancels the others")
    if field(gate, "name") != "gate (${{ matrix.leg }})":
        out.append("gate: its name does not carry the leg")
    if "outputs" in gate:
        out.append("gate: declares a job output, which one leg's result overwrites")
    gate_steps = workflow_yaml.steps(gate)
    runs = [s for s in gate_steps if field(s, "run").strip() == RUN_LEG]
    if len(runs) != 1 or table(runs[0], "env").get("LEG") != "${{ matrix.leg }}":
        out.append(f"gate: no step runs `{RUN_LEG}` with LEG from the matrix")
    by_name = {field(s, "name"): s for s in gate_steps}
    for name, leg in LEG_BOUND.items():
        if leg not in flat(field(by_name.get(name, {}), "if")):
            out.append(f"gate: '{name}' runs on every leg, not only where {leg}")
    needs = found["ci"].get("needs")
    if not (needs == "gate" or (isinstance(needs, list) and "gate" in needs)):
        out.append("ci: does not need gate, so a red leg would not red ci / ci")
    if any("needs.gate." in v for v in scalars(found["coverage-upload"])):
        out.append("coverage-upload: reads a gate output, which a matrix cannot keep")
    return out


def ci_text() -> str:
    """Read ci.yml.

    Returns:
        The workflow as the tree holds it.

    """
    return (WORKFLOWS / "ci.yml").read_text("utf-8")


def planted(old: str, new: str) -> str:
    """Replace, in the real ci.yml, text that must be there.

    Returns:
        The workflow with the first `old` replaced.

    Raises:
        AssertionError: `old` is not in the file, so the plant proves
            nothing.

    """
    text = ci_text()
    if old not in text:
        raise AssertionError(old)
    return text.replace(old, new, 1)


class GateLegs(unittest.TestCase):
    """The gate is three legs side by side (#1740)."""

    def test_the_real_gate_conforms(self) -> None:
        """A matrix of the three, each one-leg step carrying its leg."""
        self.assertEqual(strings(), leg_findings(ci_text()))

    def test_one_job_and_no_copies(self) -> None:
        """One matrix job, not three copied ones."""
        found = workflow_yaml.jobs(parsed(ci_text()))
        copies: list[str] = [j for j in found if j.startswith("gate-")]
        self.assertEqual(strings(), copies)

    def test_planted_breaches_are_named(self) -> None:
        """Each property broken once on the real file, by its message."""
        rows = [
            (
                "a leg dropped",
                ("          - test\n          - properties\n", "          - test\n"),
                "gate: not a matrix of the legs ['lint', 'test', 'properties']",
            ),
            (
                "fail-fast on",
                (
                    "      fail-fast: false\n      matrix:\n        leg:",
                    "      fail-fast: true\n      matrix:\n        leg:",
                ),
                "gate: fail-fast is not false, so one red leg cancels the others",
            ),
            (
                "the plain name",
                ("    name: gate (${{ matrix.leg }})", "    name: gate"),
                "gate: its name does not carry the leg",
            ),
            (
                "the whole gate on every leg",
                (RUN_LEG, "mise run ci"),
                f"gate: no step runs `{RUN_LEG}` with LEG from the matrix",
            ),
            (
                "the squash check on every leg",
                (
                    "${{ matrix.leg == 'lint' && github.event_name == 'pull_request'",
                    "${{ github.event_name == 'pull_request'",
                ),
                (
                    "gate: 'The squash message is the PR title and body — hold it to"
                    " the commit canon' runs on every leg, not only where"
                    " matrix.leg == 'lint'"
                ),
            ),
            (
                "the merge-group check on every leg",
                (
                    "${{ matrix.leg == 'lint' && github.event_name == 'merge_group' }}",
                    "${{ github.event_name == 'merge_group' }}",
                ),
                (
                    "gate: 'A merge group is the pull request's current head' runs on"
                    " every leg, not only where matrix.leg == 'lint'"
                ),
            ),
            (
                "the badge reports from every leg",
                (
                    "${{ matrix.leg == 'test' && github.event_name == 'push'",
                    "${{ github.event_name == 'push'",
                ),
                (
                    "gate: 'Keep the coverage reports for the badge (default branch"
                    " only)' runs on every leg, not only where matrix.leg == 'test'"
                ),
            ),
            (
                "the release figures from every leg",
                (
                    "${{ matrix.leg == 'test' && (github.event_name == 'merge_group'",
                    "${{ (github.event_name == 'merge_group'",
                ),
                (
                    "gate: 'Keep the coverage figures for the release' runs on every"
                    " leg, not only where matrix.leg == 'test'"
                ),
            ),
            (
                "a job output",
                (
                    "    timeout-minutes: 30\n    env:\n      # Both checkouts",
                    (
                        "    timeout-minutes: 30\n    outputs:\n      x: y\n"
                        "    env:\n      # Both checkouts"
                    ),
                ),
                "gate: declares a job output, which one leg's result overwrites",
            ),
            (
                "ci without the gate",
                (
                    "      - plan\n      - gate\n      - pgrx\n",
                    "      - plan\n      - pgrx\n",
                ),
                "ci: does not need gate, so a red leg would not red ci / ci",
            ),
            (
                "the badge reading an output",
                (
                    "      ${{ !cancelled() && needs.ci.result == 'success'\n",
                    (
                        "      ${{ !cancelled() && needs.ci.result == 'success'\n"
                        "      && needs.gate.outputs.coverage-reports != ''\n"
                    ),
                ),
                "coverage-upload: reads a gate output, which a matrix cannot keep",
            ),
        ]
        for label, (old, new), message in rows:
            with self.subTest(row=label):
                self.assertEqual(strings(message), leg_findings(planted(old, new)))


# A default-branch test compares the ref with the event's own name for
# it, `format('refs/heads/{0}', github.event.repository.default_branch)`,
# never with a literal: a repository whose default branch is not `main`
# would run the gate and silently skip what those steps keep (#1693).
# A literal tag or pull ref is not a branch and is not matched.
REF = r"github\.ref(?:_name)?"
LITERAL_BRANCH = re.compile(
    rf"(?P<ref>{REF})\s*[!=]=\s*'(?P<a>[^']*)'"
    rf"|'(?P<b>[^']*)'\s*[!=]=\s*(?P<fer>{REF})"
)
IF_KEY = re.compile(r"^(?P<lead>\s*(?:-\s+)?)if:")


def is_branch(ref: str, literal: str) -> bool:
    """Say whether a literal compared with a ref names a branch.

    Returns:
        True for `refs/heads/...` against github.ref, or any literal
        against github.ref_name.

    """
    if ref == "github.ref_name":
        return True
    return literal.startswith("refs/heads/")


def literal_branches(label: str, text: str) -> list[str]:
    """Name each `if:` line comparing the ref with a literal branch name.

    An `if:` runs on from its key through every following line indented
    deeper than the key, so a folded condition is read whole and the
    line named is the one carrying the literal.

    Returns:
        One `<label>:<line>` entry per offending line, in file order.

    """
    found: list[str] = []
    depth = -1
    for number, line in enumerate(text.splitlines(), start=1):
        indent = len(line) - len(line.lstrip())
        key = IF_KEY.match(line)
        if key:
            depth = len(key["lead"])
        elif line.strip() and indent <= depth:
            depth = -1
        if depth < 0:
            continue
        for match in LITERAL_BRANCH.finditer(line):
            ref = match["ref"] or match["fer"]
            literal = match["a"] if match["ref"] else match["b"]
            if is_branch(ref, literal):
                found.append(f"{label}:{number}")
                break
    return found


class DefaultBranchByName(unittest.TestCase):
    """No `if:` tests for the default branch by a literal name (#1693)."""

    PUSH = "      - name: x\n        if: ${{ github.event_name == 'push' && %s }}\n"

    def test_no_workflow_or_template_names_the_branch(self) -> None:
        """Every workflow and workflow template, read whole."""
        root = CANON
        paths = sorted(
            path
            for home in (root / ".github" / "workflows", root / "workflow-templates")
            for pattern in ("*.yml", "*.yaml")
            for path in home.glob(pattern)
        )
        found = [
            hit
            for path in paths
            for hit in literal_branches(
                str(path.relative_to(root)), path.read_text("utf-8")
            )
        ]
        self.assertEqual(strings(), found)

    def test_a_literal_ref_is_refused_by_line(self) -> None:
        """The form ci.yml carried at three sites before #1693."""
        text = "jobs:\n" + self.PUSH % "github.ref == 'refs/heads/main'"
        self.assertEqual(strings("x.yml:3"), literal_branches("x.yml", text))

    def test_a_literal_ref_name_is_refused(self) -> None:
        """The short name is the same test."""
        text = "jobs:\n" + self.PUSH % "github.ref_name == 'main'"
        self.assertEqual(strings("x.yml:3"), literal_branches("x.yml", text))

    def test_the_literal_first_and_not_equal_are_refused(self) -> None:
        """Operand order and the operator do not change what is named."""
        text = "jobs:\n" + self.PUSH % "'refs/heads/trunk' != github.ref"
        self.assertEqual(strings("x.yml:3"), literal_branches("x.yml", text))

    def test_a_folded_condition_names_the_literal_line(self) -> None:
        """The coverage-upload job's shape: the literal three lines down."""
        text = (
            "jobs:\n  up:\n    if: >-\n      ${{ !cancelled()\n"
            "      && github.event_name == 'push'\n"
            "      && github.ref == 'refs/heads/main' }}\n"
            "    runs-on: ubuntu-26.04\n"
        )
        self.assertEqual(strings("x.yml:6"), literal_branches("x.yml", text))

    def test_the_default_branch_form_is_allowed(self) -> None:
        """The event's own name for the branch."""
        text = "jobs:\n" + self.PUSH % (
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)"
        )
        self.assertEqual(strings(), literal_branches("x.yml", text))

    def test_a_tag_literal_is_not_a_branch(self) -> None:
        """A tag ref is not a default-branch test."""
        text = "jobs:\n" + self.PUSH % "github.ref != 'refs/tags/v1'"
        self.assertEqual(strings(), literal_branches("x.yml", text))

    def test_an_identity_string_outside_an_if_is_not_matched(self) -> None:
        """The source-attest identity is a signed name, not a condition."""
        text = (
            "env:\n  ID: x.yml@refs/heads/main\n"
            "  REF: ${{ github.ref == 'refs/heads/main' }}\n"
            "jobs:\n" + self.PUSH % "true"
        )
        self.assertEqual(strings(), literal_branches("x.yml", text))


if __name__ == "__main__":
    unittest.main()

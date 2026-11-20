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
import io
import re
import shlex
import sys
import tempfile
import tokenize
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import caller_pins

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import call_names
import hermetic

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = CANON / ".github" / "workflows"


def source() -> Path:
    """Name the tree a law over the canon's own files reads (#1726).

    Returns:
        `hermetic.source(CANON)`: the checkout, or the repository's top
        from mutmut's copy, where the files are as their authors wrote
        them. Rows that run the copied files keep `CANON`.

    """
    return hermetic.source(CANON)


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

# The indent of a step's own keys in every workflow here.
STEP = 8

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
    "mise/mutants_python.py": "asks mutmut's own interpreter, read from its"
    " installed script, where mutmut lives",
    "mise/with_lock.py": "runs its caller's command under the lock",
    "mise/unittest_guarded.py": "starts itself as a worker under the"
    " interpreter it runs on (#1623)",
    "mise/python_properties.py": "starts itself on each shard under the"
    " interpreter it runs on (#1741)",
    "mise/security_config.py": "builds its gh argv in a variable",
}

# Scripts that run before any tool is installed, so call nothing a list
# could carry (the reader prints tool names in its own prose).
PRE_INSTALL = {"caller_pins.py"}


def belt() -> dict:
    """Read the belt's config.

    Returns:
        mise/config.toml, decoded.

    """
    return tomllib.loads((CANON / "mise" / "config.toml").read_text("utf-8"))


def belt_tools() -> dict[str, str]:
    """Map each belt tool's command name to its `[tools]` key.

    Returns:
        Command name to the key as mise/config.toml writes it.

    """
    return {caller_pins.tool_name(key): key for key in belt()["tools"]}


def jobs(text: str) -> dict[str, list[str]]:
    """Split a workflow's text into its jobs.

    Returns:
        Job id to the job's lines, in file order.

    """
    lines = text.splitlines()
    top = lines.index("jobs:")
    starts = [
        i for i in range(top + 1, len(lines)) if re.match(r"^  [\w-]+:\s*$", lines[i])
    ]
    return {
        lines[a].strip().rstrip(":"): lines[a:b]
        for a, b in zip(starts, [*starts[1:], len(lines)], strict=True)
    }


def steps(job: list[str]) -> list[dict[str, str]]:
    """Split a job into its steps.

    Returns:
        One mapping per step: each `key: value` at the step's own level,
        `with.<key>` for its inputs, and `run` as the whole body.

    """
    out: list[dict[str, str]] = []
    block, depth, section = "", 0, ""
    for raw in job:
        line = raw
        if line.startswith("      - "):
            out.append({})
            line = "        " + line[8:]
        if not out or not line.strip():
            if out and block:
                out[-1][block] += "\n"
            continue
        indent = len(line) - len(line.lstrip())
        if block and indent > depth:
            out[-1][block] += line.strip() + "\n"
            continue
        block = ""
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if key.startswith("#") or indent not in {STEP, STEP + 2}:
            continue
        if indent == STEP:
            section = key
        name = key if indent == STEP else f"{section}.{key}"
        if value in {"|", "|-", ">", ">-"}:
            out[-1][name], block, depth = "", name, indent
        else:
            out[-1][name] = value + "\n" if key == "run" else value
    return out


def mise_steps(job: list[str]) -> list[dict[str, str]]:
    """Name a job's mise-action steps.

    Returns:
        Each step whose `uses:` is jdx/mise-action.

    """
    return [s for s in steps(job) if "jdx/mise-action" in s.get("uses", "")]


def uncached(step: dict[str, str]) -> bool:
    """Say whether a mise-action step runs without the tool cache.

    Returns:
        True unless the step sets `cache: true`.

    """
    return step.get("with.cache", "true") != "true"


def tool_cache_restored(job: list[str]) -> bool:
    """Say whether a job restores its tool cache around the install (#1406).

    The gate's jobs install with `cache: false` because `actions/cache`
    steps either side restore and save the tool cache instead: cached, so
    not a release install, and held by release/test_gate_caches.py.

    Returns:
        True when an `actions/cache/restore` step restores the mise dir.

    """
    return any(
        "actions/cache/restore@" in s.get("uses", "")
        and "tool-key.outputs.dir" in s.get("with.path", "")
        for s in steps(job)
    )


def installs(job: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Read what a job installs.

    Returns:
        The belt keys its install step names, the caller tools its pins
        step needs, and the caller tools it takes where pinned.

    Raises:
        ValueError: no install list, or a list naming a pins step that
            does not exist, so a row would judge nothing.

    """
    (step,) = mise_steps(job)
    args = step.get("with.install_args", "")
    if not args:
        msg = "the job's mise-action step names no install_args"
        raise ValueError(msg)
    keys = re.sub(r"\$\{\{.*?\}\}", " ", args).split()
    ref = re.search(r"steps\.([\w-]+)\.outputs\.install", args)
    needed: list[str] = []
    optional: list[str] = []
    if ref:
        (pins,) = [s for s in steps(job) if s.get("id") == ref.group(1)]
        call = pins["run"].replace("\\\n", " ").split("caller_pins.py", 1)[1]
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


def code(text: str) -> str:
    """Reduce shell to the lines that can call a tool.

    Comment lines go, and so does prose a reader would mistake for a
    call: a line that only prints (`echo`, `printf`) and a line that only
    assigns a double-quoted literal (the `emsg+=` remedy lines).

    Returns:
        The text that remains.

    """
    kept = [
        ln
        for ln in text.splitlines()
        if not re.match(r"\s*(#|echo\b|printf\b|[\w]+\+?=\")", ln)
    ]
    return "\n".join(kept)


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
        value = node.value
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
    root = source()
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

    """
    out = []
    tasks = belt().get("tasks", {})
    for name in re.findall(r"[\w-]+\.(?:sh|py)\b", text):
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
        run = tasks[task]["run"]
        body = code(run if isinstance(run, str) else "\n".join(run))
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


def carries_belt(workflow: str, job: list[str]) -> bool:
    """Say whether a job has the belt in scope.

    Returns:
        True where the job sets `MISE_GLOBAL_CONFIG_FILE`, or where the
        workflow runs only in the canon (no `workflow_call`), whose own
        `mise/config.toml` IS the belt.

    """
    text = (WORKFLOWS / workflow).read_text("utf-8")
    return "MISE_GLOBAL_CONFIG_FILE" in code("\n".join(job)) or (
        "workflow_call:" not in text
    )


def calls(job: list[str], *, carries: bool) -> tuple[set[str], set[str]]:
    """Name every tool a job's run steps call.

    Returns:
        The belt keys called (none where the job carries no belt: a belt
        tool's name is then the runner's copy) and the caller tools.

    """
    body = "\n".join(code(s.get("run", "")) for s in steps(job))
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


def uninstalled(workflow: str, job_id: str, job: list[str]) -> list[str]:
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


def auto_installs(workflow: str, job_id: str, job: list[str]) -> list[str]:
    """Name a listed job whose `mise run` would install the rest anyway.

    Measured on mise 2026.11.6 (#1408): `mise run` installs every tool
    the config pins and the data directory lacks before it runs the
    task, so a list is only a list where the job turns that off.

    Returns:
        One line when the job runs `mise run` and does not set
        MISE_TASK_RUN_AUTO_INSTALL to "false"; none otherwise.

    """
    text = "\n".join(job)
    if not re.search(r"(?<![\w-])mise\s+run\b", code(text)):
        return []
    if re.search(r'^\s+MISE_TASK_RUN_AUTO_INSTALL: "false"$', text, re.MULTILINE):
        return []
    return [
        (
            f"{workflow} {job_id}: runs `mise run`, which installs every tool"
            ' the config pins unless MISE_TASK_RUN_AUTO_INSTALL is "false"'
        )
    ]


def load(workflow: str) -> dict[str, list[str]]:
    """Read one workflow's jobs.

    Returns:
        Job id to the job's lines.

    """
    return jobs((WORKFLOWS / workflow).read_text("utf-8"))


def whole_config(workflow: str, job_id: str, job: list[str]) -> list[str]:
    """Name a job in scope that installs the whole config unexempted.

    Returns:
        One line when the job's mise-action step names no install_args
        and the job is not in WHOLE_CONFIG; none otherwise.

    """
    (step,) = mise_steps(job)
    if step.get("with.install_args") or (workflow, job_id) in WHOLE_CONFIG:
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
            for path in (source() / ".github" / "workflows").glob("*.yml")
            if path.name not in OUT_OF_SCOPE
            for job_id, job in jobs(path.read_text("utf-8")).items()
            if any(uncached(s) for s in mise_steps(job))
            and not tool_cache_restored(job)
        )
        self.assertEqual(sorted(IN_SCOPE), found)

    def test_every_job_names_its_list(self) -> None:
        """No job installs the whole config, and none can be empty."""
        for workflow, job_id in IN_SCOPE:
            job = load(workflow)[job_id]
            with self.subTest(workflow=workflow, job=job_id):
                self.assertEqual([], whole_config(workflow, job_id, job))
                if (workflow, job_id) not in WHOLE_CONFIG:
                    keys, needed, _ = installs(job)
                    self.assertTrue(keys or needed, "an empty list installs all")

    def test_the_exemption_is_still_true(self) -> None:
        """The exempt job really installs the whole config, and is in scope."""
        for workflow, job_id in WHOLE_CONFIG:
            with self.subTest(workflow=workflow, job=job_id):
                self.assertIn((workflow, job_id), IN_SCOPE)
                (step,) = mise_steps(load(workflow)[job_id])
                self.assertNotIn("with.install_args", step)

    def test_a_second_whole_config_job_is_named(self) -> None:
        """Planted: a job loses its list and is not the exempt one."""
        job = [
            ln
            for ln in load("attach-release.yml")["attach"]
            if "install_args:" not in ln
        ]
        self.assertEqual(
            [
                (
                    "attach-release.yml attach: installs the whole mise config;"
                    " name its tools in install_args (#1333)"
                )
            ],
            whole_config("attach-release.yml", "attach", job),
        )

    def test_every_gate_install_is_held(self) -> None:
        """Every mise-action job in ci.yml names a list or is the exemption."""
        found = sorted(
            ("ci.yml", job_id)
            for job_id, job in load("ci.yml").items()
            if mise_steps(job)
        )
        self.assertEqual(sorted([*GATE_LISTS, *GATE_WHOLE]), found)

    def test_the_gate_lists_are_lists(self) -> None:
        """The legs name their tools; the gate does not."""
        for workflow, job_id in GATE_LISTS:
            job = load(workflow)[job_id]
            with self.subTest(job=job_id):
                keys, needed, optional = installs(job)
                self.assertTrue(keys and (needed or optional))
        for workflow, job_id in GATE_WHOLE:
            (step,) = mise_steps(load(workflow)[job_id])
            self.assertNotIn("with.install_args", step)

    def test_every_belt_key_is_a_belt_key(self) -> None:
        """A misspelt key would install nothing and say nothing."""
        known = set(belt()["tools"])
        listed = [j for j in IN_SCOPE if j not in WHOLE_CONFIG]
        for workflow, job_id in [*listed, *GATE_LISTS]:
            with self.subTest(workflow=workflow, job=job_id):
                keys, _, _ = installs(load(workflow)[job_id])
                self.assertEqual([], [k for k in keys if k not in known])


class JobInstalls(unittest.TestCase):
    """Each job installs what it calls."""

    def test_every_job_installs_what_it_calls(self) -> None:
        """The real jobs against their real steps and scripts."""
        for workflow, job_id in [j for j in IN_SCOPE if j not in WHOLE_CONFIG]:
            with self.subTest(workflow=workflow, job=job_id):
                self.assertEqual(
                    [], uninstalled(workflow, job_id, load(workflow)[job_id])
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
        job = [
            ln.replace(" aqua:sigstore/gitsign", "")
            for ln in load("release.yml")["tag"]
        ]
        self.assertEqual(
            ["release.yml tag: calls aqua:sigstore/gitsign and does not install it"],
            uninstalled("release.yml", "tag", job),
        )

    def test_a_dropped_caller_input_is_named(self) -> None:
        """Planted: the binary build without cargo-auditable."""
        job = [
            ln.replace("rust cargo-auditable >>", "rust >>")
            for ln in load("build-rust-binary.yml")["build"]
        ]
        self.assertEqual(
            [
                (
                    "build-rust-binary.yml build: calls cargo-auditable and does not"
                    " install it"
                )
            ],
            uninstalled("build-rust-binary.yml", "build", job),
        )

    def test_a_new_call_is_named(self) -> None:
        """Planted: a step starts calling a belt tool the job lacks."""
        job = [
            *load("attach-release.yml")["attach"],
            "      - name: Planted",
            "        run: cosign verify-blob dist/checksums.txt",
        ]
        self.assertEqual(
            [
                (
                    "attach-release.yml attach: calls aqua:sigstore/cosign and does not"
                    " install it"
                )
            ],
            uninstalled("attach-release.yml", "attach", job),
        )

    def test_a_new_indirect_call_is_named(self) -> None:
        """Planted: stele's VEX derivation execs osv-scanner."""
        job = [
            *load("attach-release.yml")["attach"],
            "      - name: Planted",
            "        run: stele derive vex --out dist/x.json",
        ]
        self.assertEqual(
            [
                (
                    "attach-release.yml attach: calls aqua:google/osv-scanner and does"
                    " not install it"
                )
            ],
            uninstalled("attach-release.yml", "attach", job),
        )

    def test_a_tool_named_only_in_a_comment_is_not_a_call(self) -> None:
        """Prose about a tool installs nothing and needs nothing."""
        job = [
            *load("attach-release.yml")["attach"],
            "      - name: Planted",
            "        run: |",
            "          # cosign could verify this one day",
            "          true",
        ]
        self.assertEqual([], uninstalled("attach-release.yml", "attach", job))


class GateInstalls(unittest.TestCase):
    """The gate's pgrx legs install what they call (#1408)."""

    def test_every_gate_list_installs_what_it_calls(self) -> None:
        """The real jobs against pgrx-test.sh and pgrx-env.sh."""
        for workflow, job_id in GATE_LISTS:
            with self.subTest(job=job_id):
                self.assertEqual(
                    [], uninstalled(workflow, job_id, load(workflow)[job_id])
                )

    def test_no_gate_list_is_undone_by_mise_run(self) -> None:
        """Each listed job that runs a task keeps `mise run` to the list."""
        for workflow, job_id in GATE_LISTS:
            with self.subTest(job=job_id):
                self.assertEqual(
                    [], auto_installs(workflow, job_id, load(workflow)[job_id])
                )

    def test_a_leg_that_lets_mise_run_install_is_named(self) -> None:
        """Planted: the setting gone, `mise run test:pgrx` installs it all."""
        job = [
            ln
            for ln in load("ci.yml")["pgrx"]
            if "MISE_TASK_RUN_AUTO_INSTALL" not in ln
        ]
        self.assertEqual(
            [
                (
                    "ci.yml pgrx: runs `mise run`, which installs every tool the"
                    ' config pins unless MISE_TASK_RUN_AUTO_INSTALL is "false"'
                )
            ],
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
        job = [
            "      - name: Planted",
            '        run: . "${ORG_BELT_DIR}/pgrx-env.sh"',
        ]
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
        job = [
            ln.replace("rust cargo-pgrx >>", "rust >>") for ln in load("ci.yml")["pgrx"]
        ]
        self.assertEqual(
            ["ci.yml pgrx: calls cargo-pgrx and does not install it"],
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_leg_that_drops_its_postgres_is_named(self) -> None:
        """Planted: no `--major`, so no Postgres for pgrx_postgres.py."""
        job = [ln for ln in load("ci.yml")["pgrx"] if "--major" not in ln]
        self.assertEqual(
            ["ci.yml pgrx: calls postgresql-binaries and does not install it"],
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_new_call_in_a_leg_is_named(self) -> None:
        """Planted: a leg starts building the fuzz targets."""
        job = [
            *load("ci.yml")["pgrx"],
            "      - name: Planted",
            "        run: cargo fuzz build --sanitizer none",
        ]
        self.assertEqual(
            ["ci.yml pgrx: calls cargo-fuzz and does not install it"],
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_a_new_belt_call_in_a_leg_is_named(self) -> None:
        """Planted: a leg starts running a belt linter."""
        job = [
            *load("ci.yml")["pgrx"],
            "      - name: Planted",
            "        run: typos --format brief",
        ]
        self.assertEqual(
            ["ci.yml pgrx: calls typos and does not install it"],
            uninstalled("ci.yml", "pgrx", job),
        )


def measures(workflow: str, job_id: str, job: list[str]) -> list[str]:
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
    text = code("\n".join(job))
    if "caller_pins.py" in text:
        found.append(f"{workflow} {job_id}: installs the caller's build inputs")
    if re.search(r"(?<![\w-])mise\s+run\b", text):
        found.append(f"{workflow} {job_id}: runs a mise task")
    keys, caller = calls(job, carries=True)
    found += [f"{workflow} {job_id}: calls {t}" for t in sorted(keys | caller)]
    return found


def step(job: list[str], name: str) -> str:
    """Read one step of a job, from its `- name:` line to the next step.

    Returns:
        The step's lines, joined; empty when the job has no such step.

    """
    start = next(
        (i for i, line in enumerate(job) if line.strip() == f"- name: {name}"), None
    )
    if start is None:
        return ""
    end = next(
        (i for i in range(start + 1, len(job)) if job[i].strip().startswith("- name:")),
        len(job),
    )
    return "\n".join(job[start:end])


class CoverageFigures(unittest.TestCase):
    """The gate keeps its figures; the release job reads them (#1622)."""

    def test_the_gate_keeps_the_figures_for_the_commit_that_lands(self) -> None:
        """Merge group and default-branch push only, named for the commit."""
        kept = step(load("ci.yml")["gate"], "Keep the coverage figures for the release")
        self.assertIn("github.event_name == 'merge_group'", kept)
        self.assertIn("github.event_name == 'push'", kept)
        self.assertIn(
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)",
            kept,
        )
        self.assertNotIn("refs/heads/main", kept)
        self.assertNotIn("pull_request", kept)
        self.assertIn("name: coverage-figures-${{ github.sha }}", kept)
        self.assertIn(
            "path: ${{ runner.temp }}/coverage-reports/coverage-figures.txt", kept
        )

    def test_the_release_job_reads_with_a_token_that_can(self) -> None:
        """The floor step has a token, and its job may read artifacts."""
        job = load("release.yml")["release-pr"]
        floor = step(job, "Re-derive the coverage floor")
        self.assertIn("run: .org-canon/release/derive-coverage-floor.sh", floor)
        self.assertIn("GH_TOKEN: ${{ github.token }}", floor)
        self.assertIn("      actions: read", "\n".join(job))

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
        self.assertIn("github.event_name == 'merge_group'", kept)
        self.assertIn("github.event_name == 'push'", kept)
        self.assertIn(
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)",
            kept,
        )
        self.assertNotIn("pull_request", kept)
        self.assertIn(
            "name: coverage-figures-pgrx-pg${{ matrix.major }}-${{ github.sha }}", kept
        )
        self.assertIn(
            "path: ${{ runner.temp }}/coverage-reports/coverage-figures.txt", kept
        )
        names = [line.strip() for line in job if line.strip().startswith("- name:")]
        self.assertLess(
            names.index("- name: Run the extension's suite at this major"),
            names.index("- name: Keep the leg's coverage figure for the release"),
        )

    def test_the_figures_step_follows_the_gate(self) -> None:
        """Kept only after `mise run ci` passed: a red gate keeps nothing."""
        job = load("ci.yml")["gate"]
        names = [line.strip() for line in job if line.strip().startswith("- name:")]
        self.assertLess(
            names.index("- name: Run the gate"),
            names.index("- name: Keep the coverage figures for the release"),
        )


class GateUploadMeasuresNothing(unittest.TestCase):
    """The coverage upload installs no measurement tool (#1528)."""

    def test_the_upload_installs_and_runs_nothing(self) -> None:
        """The real job: download, list, hand to Codecov."""
        for (workflow, job_id), why in GATE_NONE.items():
            with self.subTest(job=job_id, why=why):
                self.assertEqual([], measures(workflow, job_id, load(workflow)[job_id]))

    def test_the_upload_that_hung_on_main_is_named(self) -> None:
        """Planted: the job as it was, re-measuring with five tools."""
        job = [
            *load("ci.yml")["coverage-upload"],
            "      - name: Install mise and the report's tools",
            "        uses: jdx/mise-action@b44d12e7d0d8ee7461bb893e1b1993500433185b",
            "        with:",
            "          install_args: >-",
            "            jq aqua:taiki-e/cargo-llvm-cov python aqua:astral-sh/uv",
            "      - name: Generate the report",
            "        run: report=$(mise run coverage:check)",
        ]
        found = measures("ci.yml", "coverage-upload", job)
        self.assertEqual(
            [
                "ci.yml coverage-upload: installs tools with jdx/mise-action",
                "ci.yml coverage-upload: runs a mise task",
            ],
            found[:2],
        )
        # Followed into coverage:check and the measurement it runs.
        for tool in ("aqua:taiki-e/cargo-llvm-cov", "pipx:coverage", "go"):
            self.assertIn(f"ci.yml coverage-upload: calls {tool}", found)

    def test_a_measuring_call_in_the_upload_is_named(self) -> None:
        """Planted: the upload starts rendering a report itself."""
        job = [
            *load("ci.yml")["coverage-upload"],
            "      - name: Planted",
            "        run: cargo llvm-cov --workspace --lcov --output-path lcov.info",
        ]
        self.assertEqual(
            [
                "ci.yml coverage-upload: calls aqua:taiki-e/cargo-llvm-cov",
                "ci.yml coverage-upload: calls rust",
            ],
            measures("ci.yml", "coverage-upload", job),
        )

    def test_no_gate_job_is_both_listed_and_measureless(self) -> None:
        """A job is a list, the whole config, or nothing: one of them."""
        self.assertEqual(set(), set(GATE_NONE) & {*GATE_LISTS, *GATE_WHOLE})


# The line a named-list job carries at job level (#1412). mise's
# task_run_auto_install is on by default, so without it `mise run`
# installs every other tool in scope before the task starts, and the
# list saves nothing in the jobs that run a task.
NO_TASK_INSTALL = '      MISE_TASK_RUN_AUTO_INSTALL: "false"'


def task_auto_install(workflow: str, job_id: str, job: list[str]) -> list[str]:
    """Name a named-list job that leaves mise's task auto-install on.

    Returns:
        One line when the job's mise-action step names install_args and
        the job's own env does not turn task auto-install off.

    """
    (step,) = mise_steps(job)
    if not step.get("with.install_args") or NO_TASK_INSTALL in job:
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
                self.assertEqual([], task_auto_install(workflow, job_id, job))

    def test_a_job_without_the_setting_is_named(self) -> None:
        """Planted: the SBOM job, which runs audit:deny, loses the line."""
        job = [ln for ln in load("publish.yml")["sbom"] if ln != NO_TASK_INSTALL]
        self.assertEqual(
            [
                (
                    "publish.yml sbom: installs a named list but leaves mise's task"
                    ' auto-install on; set MISE_TASK_RUN_AUTO_INSTALL: "false" in'
                    " the job's env (#1412)"
                )
            ],
            task_auto_install("publish.yml", "sbom", job),
        )

    def test_a_tool_only_a_task_calls_must_be_listed(self) -> None:
        """Planted: cargo-deny is reached only through `mise run audit:deny`."""
        job = [
            ln.replace(" aqua:EmbarkStudios/cargo-deny", "")
            for ln in load("publish.yml")["sbom"]
        ]
        self.assertEqual(
            [
                (
                    "publish.yml sbom: calls aqua:EmbarkStudios/cargo-deny and does"
                    " not install it"
                )
            ],
            uninstalled("publish.yml", "sbom", job),
        )


class PythonCode(unittest.TestCase):
    """A Python helper's comments are not code, wherever they sit (#1496)."""

    def test_an_inline_suppression_is_not_a_call(self) -> None:
        """Measured on #1490: `# ruff: ignore[...]` after an import."""
        helper = (
            "import subprocess  # ruff: ignore[suspicious-subprocess-import]\n"
            'subprocess.run(["git", "status"], check=True)\n'
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
        job = [
            *load("attach-release.yml")["attach"],
            "      - name: Planted",
            '        run: python3 "${GITHUB_WORKSPACE}/release/planted-call.py"',
        ]
        with mock.patch(f"{__name__}.CANON", root):
            return uninstalled("attach-release.yml", "attach", job)


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
        self.assertEqual([], planted_uninstalled(helper))

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
        self.assertEqual(sorted(DYNAMIC), sorted(found))
        self.assertEqual([], unreadable(helpers()))

    def test_an_unnamed_dynamic_helper_is_red_by_name(self) -> None:
        """A new helper that runs a variable is named, with its call."""
        helper = "import subprocess\n\n\ndef go(args):\n    subprocess.run(args)\n"
        self.assertEqual(
            [
                (
                    "release/planted.py: runs a command this check cannot read"
                    " (line 5: args); name it in DYNAMIC with why"
                )
            ],
            unreadable({"release/planted.py": helper}),
        )


class OneReader(unittest.TestCase):
    """release/caller_pins.py is the canon's one reader of a caller's pins."""

    def test_no_other_reader_in_release_or_the_workflows(self) -> None:
        """Every script under release/ and every workflow, read whole."""
        root = source()
        paths = [
            *sorted((root / "release").glob("*.sh")),
            *sorted((root / "release").glob("*.py")),
            *sorted((root / ".github" / "workflows").glob("*.yml")),
        ]
        found = [ln for p in paths for ln in readers(p, p.read_text("utf-8"), root)]
        self.assertEqual([], found)

    def test_a_planted_reader_is_named(self) -> None:
        """Planted: the inline reader the pgrx build carried before #1333."""
        path = WORKFLOWS / "build-pgrx-extension.yml"
        text = path.read_text("utf-8") + (
            '          tools = t.get("tools", {})\n'
            '          pgrx = tools.get("cargo:cargo-pgrx", "")\n'
        )
        self.assertEqual(
            [
                (
                    ".github/workflows/build-pgrx-extension.yml: reads a caller's"
                    " mise config itself; read it through release/caller_pins.py"
                    " (#1333)"
                )
            ],
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
    return {Path(p).name for p in hermetic.tracked(source(), "*.py")}


def action_job(text: str) -> list[str]:
    """Read an action file's steps at a workflow job's indent.

    Returns:
        The action's lines from `runs:` on, indented as a job's are, so
        `steps()` reads them.

    """
    lines = text.splitlines()
    return ["  " + ln for ln in lines[lines.index("runs:") :]]


def helper_starts(
    rel: str, job_id: str, job: list[str], tracked: set[str]
) -> list[str]:
    """Name each step that starts a tracked helper but not as `"${ORG_PYTHON}"`.

    A helper path is a start where it stands in a command's place (a
    line's first word, or after `;`, `|`, `&`, `(` or `$(`), or after an
    interpreter's name. Anywhere else it is an argument.

    Returns:
        One line per such start, naming the file, job, step and helper.

    """
    out = []
    for step in steps(job):
        body = re.sub(r"\$\{\{.*?\}\}", "${{expr}}", step.get("run", ""))
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
                    f"{rel} {job_id}: step '{step.get('name', '?')}' starts"
                    f' {name} {how}; run it as "${{ORG_PYTHON}}" {name} (#1543)'
                )
    return out


def uncanoned(rel: str, job_id: str, job: list[str]) -> list[str]:
    """Name a job that uses ORG_PYTHON before the canon action exports it.

    Returns:
        One line when a step names ORG_PYTHON and no earlier step runs
        `$/.github/actions/canon`; none otherwise.

    """
    canon = False
    for step in steps(job):
        if step.get("uses", "") == "$/.github/actions/canon":
            canon = True
        elif "ORG_PYTHON" in "\n".join(step.values()) and not canon:
            return [
                (
                    f"{rel} {job_id}: step '{step.get('name', '?')}' uses ORG_PYTHON"
                    " and no step before it runs $/.github/actions/canon, which"
                    " exports it (#1543)"
                )
            ]
    return []


def interpreter_findings(texts: dict[str, str], tracked: set[str]) -> list[str]:
    """Hold every workflow and action file to the one way to start a helper.

    Returns:
        Every red line, file by file.

    """
    out = []
    for rel, text in texts.items():
        if rel.startswith(".github/actions/"):
            out += helper_starts(rel, "runs", action_job(text), tracked)
            continue
        for job_id, job in jobs(text).items():
            out += helper_starts(rel, job_id, job, tracked)
            out += uncanoned(rel, job_id, job)
    return out


def workflow_and_action_texts() -> dict[str, str]:
    """Read every workflow and action file the canon carries.

    Returns:
        Path relative to the canon -> text.

    """
    root = source()
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
            [], interpreter_findings(workflow_and_action_texts(), tracked_helpers())
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
            [
                (
                    ".github/workflows/x.yml build: step 'Push the image' starts"
                    " registry.py with the runner's python3; run it as"
                    ' "${ORG_PYTHON}" registry.py (#1543)'
                )
            ],
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
            [
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
            ],
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
            [
                (
                    ".github/actions/x/action.yml runs: step 'Derive' starts"
                    " derive.py with the runner's python3; run it as"
                    ' "${ORG_PYTHON}" derive.py (#1543)'
                )
            ],
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
            [
                (
                    ".github/workflows/x.yml plan: step 'Plan' uses ORG_PYTHON and no"
                    " step before it runs $/.github/actions/canon, which exports it"
                    " (#1543)"
                )
            ],
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
            [],
            interpreter_findings(
                {".github/workflows/x.yml": text}, {"registry.py", "gate_tools.py"}
            ),
        )


JOB_TOKENS = ("github.token", "secrets.GITHUB_TOKEN")
STELE_VERB = re.compile(r"(?<![\w/.:-])stele\s+[a-z]")
ORG_FLAG = re.compile(r"(?<![\w-])--org\b")


def env_token(lines: list[str], indent: int) -> str | None:
    """Read `GH_TOKEN` from the `env:` block at an indent, if any.

    Returns:
        The value, or None where the block or the key is absent.

    """
    pad, inside = " " * indent, False
    for line in lines:
        if line.startswith(pad + "env:"):
            inside = True
        elif inside and line.startswith(pad + "  GH_TOKEN:"):
            return line.partition(":")[2].strip()
        elif inside and line.strip() and not line.startswith(pad + "  "):
            inside = False
    return None


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
    for rel, text in sorted(texts.items()):
        top = text.splitlines()
        top = top[: top.index("jobs:")]
        for job_id, job in jobs(text).items():
            for step in steps(job):
                body = step.get("run", "").splitlines()
                run = "\n".join(ln for ln in body if not ln.startswith("#"))
                if not (STELE_VERB.search(run) and ORG_FLAG.search(run)):
                    continue
                token = (
                    step.get("env.GH_TOKEN") or env_token(job, 4) or env_token(top, 0)
                )
                if token and any(t in token for t in JOB_TOKENS):
                    found.append(f"{rel}: {job_id} / {step.get('name', '?')}")
    return found


class PopulationToken(unittest.TestCase):
    """A population read holds a token that sees the population (#1602)."""

    def test_no_stele_org_step_reads_on_the_job_token(self) -> None:
        """The tree: every `stele ... --org` step holds a minted token."""
        root = source()
        texts = {
            str(p.relative_to(root)): p.read_text("utf-8")
            for p in sorted((root / ".github" / "workflows").glob("*.yml"))
        }
        self.assertEqual([], population_reads_on_job_token(texts))

    def test_the_scan_reaches_the_population_reads(self) -> None:
        """Not vacuous: the tree's population reads are all in view."""
        root = source()
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
            ["a.yml: a / Read", "b.yml: b / Read", "c.yml: c / Read"],
            population_reads_on_job_token(texts),
        )


def mint_step(workflow: str, job_id: str) -> tuple[int, dict[str, str]]:
    """Find a job's audit App mint step.

    Returns:
        The step's index in the job and the step.

    """
    found = [
        (i, s)
        for i, s in enumerate(steps(load(workflow)[job_id]))
        if "actions/create-github-app-token" in s.get("uses", "")
    ]
    return found[0] if found else (-1, {})


VEX_GRANTS = {
    "with.permission-contents": "read",
    "with.permission-metadata": "read",
    "with.permission-attestations": "read",
}


class PopulationGrants(unittest.TestCase):
    """vex-attest's population read is granted three reads, no more (#1602)."""

    def test_the_vex_attest_mint_names_three_reads(self) -> None:
        """Contents, metadata and attestations: no review history."""
        _, mint = mint_step("vex-attest.yml", "plan")
        self.assertEqual(
            VEX_GRANTS, {k: v for k, v in mint.items() if k.startswith("with.perm")}
        )


class VexAttestToken(unittest.TestCase):
    """vex-attest's plan job mints the audit App's read token (#1602)."""

    def test_the_plan_mints_before_the_step_that_reads(self) -> None:
        """The mint precedes the derivation, which holds its output."""
        at, mint = mint_step("vex-attest.yml", "plan")
        plan = steps(load("vex-attest.yml")["plan"])
        reads = [i for i, s in enumerate(plan) if s.get("id") == "collect"]
        self.assertEqual(1, len(reads))
        self.assertEqual("token", mint.get("id"))
        self.assertLess(at, reads[0])
        self.assertEqual(
            "${{ steps.token.outputs.token }}", plan[reads[0]]["env.GH_TOKEN"]
        )

    def test_the_mint_mirrors_the_audit_walks(self) -> None:
        """Same pin, same client and key, owner scope as the audit's mint."""
        _, mint = mint_step("vex-attest.yml", "plan")
        _, audit = mint_step("audit.yml", "blast-radius")
        keys = ("uses", "with.client-id", "with.private-key", "with.owner")
        self.assertEqual(
            {k: audit.get(k) for k in keys}, {k: mint.get(k) for k in keys}
        )

    def test_the_signing_jobs_never_see_the_token(self) -> None:
        """Only the plan job names the App or its token."""
        for job_id, job in load("vex-attest.yml").items():
            if job_id == "plan":
                continue
            text = "\n".join(job)
            self.assertNotIn("AUDIT_APP", text, job_id)
            self.assertNotIn("steps.token", text, job_id)


# Each environment's secrets, as the forge issues them (measured
# 2026-11-20: the repository and the organisation hold none of these).
ENVIRONMENT_SECRETS = {
    "audit": ("AUDIT_APP_PRIVATE_KEY", "AUDIT_TOKEN", "PACKAGES_TOKEN"),
    "source-attest": ("SOURCE_RULES_TOKEN",),
}


def declared_environment(job: list[str]) -> str | None:
    """Read a job's `environment:`, in either of its two forms.

    Returns:
        The environment's name, or None where the job declares none.

    """
    for i, line in enumerate(job):
        key, _, value = line.partition(":")
        if key != "    environment":
            continue
        if value.strip():
            return value.split("#")[0].strip()
        for inner in job[i + 1 :]:
            if inner.startswith("      name:"):
                return inner.partition(":")[2].split("#")[0].strip()
            if not inner.startswith("      "):
                break
    return None


def secrets_outside_environment(texts: dict[str, str]) -> list[str]:
    """Name each job that reads an environment's secret outside it (#1602).

    A secret that lives only in an environment is issued to a job that
    declares that environment, and to any other job as an empty string.

    Returns:
        `workflow: job reads SECRET outside environment ENV` per finding.

    """
    found = []
    for rel, text in sorted(texts.items()):
        for job_id, job in jobs(text).items():
            body = "\n".join(ln for ln in job if not ln.strip().startswith("#"))
            has = declared_environment(job)
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
        root = source()
        texts = {
            rel: (root / rel).read_text("utf-8")
            for rel in hermetic.tracked(root, ".github/workflows/*.yml")
        }
        self.assertEqual([], secrets_outside_environment(texts))

    def test_the_scan_reaches_the_environment_jobs(self) -> None:
        """Not vacuous: stripping every declaration names each reader."""
        root = source()
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
            [
                "a.yml: a reads AUDIT_TOKEN outside environment audit",
                "b.yml: b reads AUDIT_TOKEN outside environment audit",
            ],
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
        gate = steps(load("ci.yml")["gate"])
        runs = [s for s in gate if s.get("run", "").strip() == "mise run ci"]
        keeps = [s for s in gate if s.get("id") == "coverage-reports"]
        self.assertEqual(1, len(runs))
        self.assertEqual(1, len(keeps))
        self.assertEqual(self.REPORTS, runs[0].get("env.ORG_COVERAGE_REPORTS"))
        self.assertEqual(self.REPORTS, keeps[0].get("with.path"))

    def test_each_pgrx_leg_names_the_directory_its_artifact_keeps(self) -> None:
        """The leg's suite writes where its artifact step reads (#1656)."""
        leg = steps(load("ci.yml")["pgrx"])
        runs = [s for s in leg if s.get("run", "").strip() == "mise run test:pgrx"]
        # The badge's report upload; the figures the release reads are
        # kept by a step of their own (#1692), held in CoverageFigures.
        keeps = [
            s
            for s in leg
            if "upload-artifact" in s.get("uses", "")
            and s.get("with.name", "").startswith("coverage-reports")
        ]
        self.assertEqual(1, len(runs))
        self.assertEqual(1, len(keeps))
        self.assertEqual(self.REPORTS, runs[0].get("env.ORG_COVERAGE_REPORTS"))
        self.assertEqual(self.REPORTS, keeps[0].get("with.path"))
        self.assertEqual(
            "coverage-reports-pgrx-pg${{ matrix.major }}", keeps[0].get("with.name")
        )

    def test_the_badge_job_downloads_beside_the_checkout(self) -> None:
        """Every report artifact lands in the workspace, where the list reads it.

        The gate's and each pgrx leg's (#1656), merged: their file names
        never collide.
        """
        upload = steps(load("ci.yml")["coverage-upload"])
        fetch = [s for s in upload if "download-artifact" in s.get("uses", "")]
        self.assertEqual(1, len(fetch))
        self.assertEqual("coverage-reports*", fetch[0].get("with.pattern"))
        self.assertEqual("true", fetch[0].get("with.merge-multiple"))
        self.assertNotIn("with.path", fetch[0])


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
        root = source()
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
        self.assertEqual([], found)

    def test_a_literal_ref_is_refused_by_line(self) -> None:
        """The form ci.yml carried at three sites before #1693."""
        text = "jobs:\n" + self.PUSH % "github.ref == 'refs/heads/main'"
        self.assertEqual(["x.yml:3"], literal_branches("x.yml", text))

    def test_a_literal_ref_name_is_refused(self) -> None:
        """The short name is the same test."""
        text = "jobs:\n" + self.PUSH % "github.ref_name == 'main'"
        self.assertEqual(["x.yml:3"], literal_branches("x.yml", text))

    def test_the_literal_first_and_not_equal_are_refused(self) -> None:
        """Operand order and the operator do not change what is named."""
        text = "jobs:\n" + self.PUSH % "'refs/heads/trunk' != github.ref"
        self.assertEqual(["x.yml:3"], literal_branches("x.yml", text))

    def test_a_folded_condition_names_the_literal_line(self) -> None:
        """The coverage-upload job's shape: the literal three lines down."""
        text = (
            "jobs:\n  up:\n    if: >-\n      ${{ !cancelled()\n"
            "      && github.event_name == 'push'\n"
            "      && github.ref == 'refs/heads/main' }}\n"
            "    runs-on: ubuntu-26.04\n"
        )
        self.assertEqual(["x.yml:6"], literal_branches("x.yml", text))

    def test_the_default_branch_form_is_allowed(self) -> None:
        """The event's own name for the branch."""
        text = "jobs:\n" + self.PUSH % (
            "github.ref == format('refs/heads/{0}',"
            " github.event.repository.default_branch)"
        )
        self.assertEqual([], literal_branches("x.yml", text))

    def test_a_tag_literal_is_not_a_branch(self) -> None:
        """A tag ref is not a default-branch test."""
        text = "jobs:\n" + self.PUSH % "github.ref != 'refs/tags/v1'"
        self.assertEqual([], literal_branches("x.yml", text))

    def test_an_identity_string_outside_an_if_is_not_matched(self) -> None:
        """The source-attest identity is a signed name, not a condition."""
        text = (
            "env:\n  ID: x.yml@refs/heads/main\n"
            "  REF: ${{ github.ref == 'refs/heads/main' }}\n"
            "jobs:\n" + self.PUSH % "true"
        )
        self.assertEqual([], literal_branches("x.yml", text))


if __name__ == "__main__":
    unittest.main()

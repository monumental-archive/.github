#!/usr/bin/env python3
"""Each release job installs every tool it calls, and names the list (#1333).

The release, publish and continuous paths run uncached on purpose (cache
poisoning), so a job that installs the caller's whole mise config pays for
every tool in it, every release: edtf's v1.3.5 publish spent 4,484 runner
seconds installing, most of it compiling tools no job called. So every
uncached `jdx/mise-action` step on those paths installs a named list
(`install_args`): the belt's tools by the belt's own keys, the caller's
build inputs by `release/caller-pins.py`, which reads their versions from
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
(#1408): the `pgrx` legs run one major's suite and the coverage upload
renders one report, and each installed the caller's whole config to do
it. They are cached, so the uncached scope above never reached them;
GATE_LISTS names them, and GATE_WHOLE the job that keeps everything.
"""

import ast
import importlib.util
import io
import re
import shlex
import tempfile
import tokenize
import tomllib
import unittest
from pathlib import Path
from unittest import mock

CANON = Path(__file__).resolve().parent.parent
WORKFLOWS = CANON / ".github" / "workflows"

_SPEC = importlib.util.spec_from_file_location(
    "caller_pins", Path(__file__).with_name("caller-pins.py")
)
caller_pins = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(caller_pins)

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
    # stele alone, and this row holds the list to its calls.
    ("base-attest.yml", "attest"),
    ("attach-release.yml", "attach"),
    ("build-go-binary.yml", "build"),
    # The collect jobs call `stele derive subjects` (#1364): stele alone,
    # and jq beside it where the images job still writes `images`.
    ("build-go-binary.yml", "collect"),
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
GATE_LISTS = [("ci.yml", "pgrx"), ("ci.yml", "coverage-upload")]
GATE_WHOLE = {
    ("ci.yml", "gate"): (
        "it runs every lint, so it needs the whole belt and every tool the"
        " caller's config pins"
    ),
}

# The caller's build inputs, by the commands that run them: the tool name
# is what `release/caller-pins.py` is asked for.
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
# located by pgrx-postgres.py from `mise ls` and registered with
# `cargo pgrx init`, both by path.
PROVISIONS = {
    r"pgrx-postgres\.py": ["postgresql-binaries"],
}

# What stele execs, read from its source (internal/cargo, internal/osv,
# internal/cli/emit.go): a call that never appears in a workflow's text.
STELE_EXECS = {
    r"stele\s+derive\s+sbom": ["cargo"],
    r"stele\s+derive\s+vex(?:-subjects)?": ["osv-scanner"],
    r"stele\s+assert\s+blast-radius": ["osv-scanner"],
    r"stele\s+emit\s+chain": ["cosign"],
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
    "release/attach-publish.py": "runs record-draft.sh by its path, which"
    " scripts() follows by name",
    "release/package-crates.py": "runs crates-io-members.sh by its path,"
    " which scripts() follows by name",
    "release/publish-crates.py": "runs crates-io-members.sh by its path, and"
    " builds its cargo metadata, package and publish argvs in variables",
    "mise/belt-shell.py": "splats its shfmt and shellcheck argvs from lists",
    "mise/embedded-shell.py": "splats its shfmt argv from a list",
    "mise/git-decoy.py": "runs its caller's command against a decoy repository",
    "mise/with-lock.py": "runs its caller's command under the lock",
    "mise/security-config.py": "builds its gh argv in a variable",
}

# Scripts that run before any tool is installed, so call nothing a list
# could carry (the reader prints tool names in its own prose).
PRE_INSTALL = {"caller-pins.py"}


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
        call = pins["run"].replace("\\\n", " ").split("caller-pins.py", 1)[1]
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
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {
                a.asname or a.name for a in node.names if a.name == "subprocess"
            }
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions |= {
                a.asname or a.name for a in node.names if a.name in SUBPROCESS_CALLS
            }
    names = constants(tree)
    commands, dynamic = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in SUBPROCESS_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ) and not (isinstance(func, ast.Name) and func.id in functions):
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
    return {
        f"{home}/{p.name}": p.read_text("utf-8")
        for home in ("release", "mise")
        for p in sorted((CANON / home).glob("*.py"))
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


def readers(path: Path, text: str) -> list[str]:
    """Name a reader of a caller's mise config other than the one reader.

    Returns:
        One line when `text` looks a tool up in a parsed `[tools]` table.

    """
    if path.name == "caller-pins.py" or path.name.startswith("test_"):
        return []
    if not re.search(r'tools\.get\(|\.get\("tools"|\["tools"\]', text):
        return []
    return [
        (
            f"{path.relative_to(CANON)}: reads a caller's mise config itself;"
            " read it through release/caller-pins.py (#1333)"
        )
    ]


class Coverage(unittest.TestCase):
    """The rows hold the real surface, not a remembered list."""

    def test_every_uncached_install_in_scope_is_held(self) -> None:
        """Every uncached mise-action job outside item 4 is a row."""
        found = sorted(
            (path.name, job_id)
            for path in WORKFLOWS.glob("*.yml")
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
        """The legs and the upload name their tools; the gate does not."""
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
    """The gate's pgrx legs and coverage upload install what they call (#1408)."""

    def test_every_gate_list_installs_what_it_calls(self) -> None:
        """The real jobs against pgrx-test.sh, pgrx-env.sh, coverage:report."""
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
            (["jq"], ["postgresql-binaries", "rust", "cargo-pgrx"], []),
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
        """Planted: no `--major`, so no Postgres for pgrx-postgres.py."""
        job = [ln for ln in load("ci.yml")["pgrx"] if "--major" not in ln]
        self.assertEqual(
            ["ci.yml pgrx: calls postgresql-binaries and does not install it"],
            uninstalled("ci.yml", "pgrx", job),
        )

    def test_an_upload_that_drops_cargo_llvm_cov_is_named(self) -> None:
        """Planted: the report's `cargo llvm-cov` with nothing to run it."""
        job = [
            ln.replace("jq aqua:taiki-e/cargo-llvm-cov ", "jq ")
            for ln in load("ci.yml")["coverage-upload"]
        ]
        self.assertEqual(
            [
                (
                    "ci.yml coverage-upload: calls aqua:taiki-e/cargo-llvm-cov and"
                    " does not install it"
                )
            ],
            uninstalled("ci.yml", "coverage-upload", job),
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
        root = Path(tmp)
        (root / "mise").symlink_to(CANON / "mise")
        (root / "release").mkdir()
        for path in (CANON / "release").iterdir():
            (root / "release" / path.name).symlink_to(path)
        (root / "release" / "planted-call.py").write_text(helper, "utf-8")
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
    """release/caller-pins.py is the canon's one reader of a caller's pins."""

    def test_no_other_reader_in_release_or_the_workflows(self) -> None:
        """Every script under release/ and every workflow, read whole."""
        paths = [
            *sorted((CANON / "release").glob("*.sh")),
            *sorted((CANON / "release").glob("*.py")),
            *sorted(WORKFLOWS.glob("*.yml")),
        ]
        found = [ln for p in paths for ln in readers(p, p.read_text("utf-8"))]
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
                    " mise config itself; read it through release/caller-pins.py"
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
                self.assertIn("caller-pins.py", path.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()

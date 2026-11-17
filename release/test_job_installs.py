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
"""

import importlib.util
import re
import shlex
import tomllib
import unittest
from pathlib import Path

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
OUT_OF_SCOPE = {"audit.yml", "repo-audit.yml", "level.yml", "level-assert.yml"}

# The jobs #1333 names, as the tree holds them. A new uncached mise-action
# job anywhere else fails the coverage row until it is listed here and
# installs a list of its own.
IN_SCOPE = [
    ("assemble-oci-index.yml", "manifest"),
    ("attach-release.yml", "attach"),
    ("build-go-binary.yml", "build"),
    ("build-oci-image.yml", "build"),
    ("build-pgrx-images.yml", "image"),
    ("build-rust-binary.yml", "build"),
    ("build-rust-crate.yml", "build"),
    ("build-wasm-npm.yml", "build"),
    ("continuous.yml", "facts"),
    ("continuous.yml", "sbom"),
    ("publish-rust-crate.yml", "publish"),
    ("publish-wasm-npm.yml", "publish"),
    ("publish.yml", "facts"),
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


def python_code(text: str) -> str:
    """Reduce a Python helper to what can run a subprocess.

    Returns:
        "" for a helper that imports no subprocess (it calls nothing),
        else its text without comments and docstrings.

    """
    if not re.search(r"^import subprocess|^from subprocess", text, re.MULTILINE):
        return ""
    text = re.sub(r'"""(?:.|\n)*?"""', "", text)
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


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
                text = path.read_text("utf-8")
                body = python_code(text) if name.endswith(".py") else code(text)
                out += [body, scripts(body, seen)]
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
    for pattern, execs in STELE_EXECS.items():
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

    def test_every_belt_key_is_a_belt_key(self) -> None:
        """A misspelt key would install nothing and say nothing."""
        known = set(belt()["tools"])
        for workflow, job_id in [j for j in IN_SCOPE if j not in WHOLE_CONFIG]:
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

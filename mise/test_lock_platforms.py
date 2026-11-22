#!/usr/bin/env python3
"""Table tests for lint:lock-platforms (#810).

edtf v1.3.0's publish failed its x86_64-apple-darwin leg after the tag
was minted: wasm-pack and node were locked for three platforms, not the
four the release builds on, and the gate never asks for macos-x64. The
lint joins the publish stub's declared binary targets to the lock.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted git tree — not
a copy of it. The defaults come from the canon's real publish.yml, so
the lint and the input it derives from cannot drift apart.

Each guard is planted both directions: the baseline tree is red with
its exact message, and removing the guard's input turns that same tree
into the guard's skip line. Red rows assert the message, not just the
exit status (#650).

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic
import lock_platforms

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
PYTHON = sys.executable
DEFAULTS = CANON / ".github" / "workflows" / "publish.yml"

NAME = "lint:lock-platforms"
STUB_PATH = ".github/workflows/publish.yml"
REMOTE = "monumental-archive/.github/.github/workflows/publish.yml@" + "a" * 40


def setUpModule() -> None:
    """Run every row as the task does, recording no finding (#1934)."""
    unittest.enterModuleContext(hermetic.as_task(NAME))


def stub(with_lines: str, uses: str = REMOTE) -> str:
    """Build a publish caller with the given `with:` body.

    Returns:
        The workflow text.

    """
    return (
        "---\n"
        "name: publish\n"
        '"on":\n'
        "  push:\n"
        "    tags:\n"
        "      - v*\n"
        "permissions: {}\n"
        "jobs:\n"
        "  publish:\n"
        f"    uses: {uses} # v1.0.0\n"
        "    with:\n"
        f"{with_lines}"
        "    permissions:\n"
        "      contents: read\n"
        "...\n"
    )


RUST_BINARY = stub("      classes: rust-crate,rust-binary # the CLI\n")
ICU = '"github:unicode-org/icu" = { version = "78.3", os = ["macos"] }\n'
CONFIG = '[tools]\nnode = "26.5.1"\n"cargo:cargo-fuzz" = "0.13.2"\n'


def entry(tool: str, version: str, platforms: tuple[str, ...]) -> str:
    """Build one lock entry with a table per platform.

    Returns:
        The entry's TOML.

    """
    out = f'[[tools."{tool}"]]\nversion = "{version}"\nbackend = "x:{tool}"\n\n'
    for key in platforms:
        out += f'[tools."{tool}"."platforms.{key}"]\nchecksum = "sha256:00"\n\n'
    return out


THREE = ("linux-arm64", "linux-x64", "macos-arm64")
FOUR = (*THREE, "macos-x64")
# The baseline: v1.3.0's shape — node locked for three of four.
LOCK = entry("node", "26.5.1", THREE) + entry("cargo:cargo-fuzz", "0.13.2", ())

RED = (
    f"{NAME}: mise.lock lacks platforms the release builds on:\n"
    "  macos-x64: node@26.5.1\n"
    f"    declared by {STUB_PATH} (rust-binary: binary-targets, the canon default)\n"
    "  each binary leg runs `mise install --locked` on its target's own runner\n"
    "  and fails there, after the tag is minted (#810). Resolve them with:\n"
    "    mise lock --platform macos-x64 node\n"
)


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the [tasks] table")
    run = decoded.as_str(decoded.as_object(tasks[NAME], NAME)["run"], "the run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


def lint(
    files: dict[str, str], untracked: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the lint in a git tree holding `files` tracked.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        for rel, text in (files | (untracked or {})).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)
        if files:
            subprocess.run([GIT, "add", "--", *files], cwd=root, env=env, check=True)
        as_mise = hermetic.task_run("lint:lock-platforms", BODY)
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


def plant(root: Path, files: dict[str, str], untracked: dict[str, str]) -> None:
    """Write a git tree holding `files` tracked and `untracked` beside them."""
    env = hermetic.env()
    for rel, text in (files | untracked).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=root, env=env, check=True)
    if files:
        subprocess.run([GIT, "add", "--", *files], cwd=root, env=env, check=True)


def check(
    files: dict[str, str], untracked: dict[str, str] | None = None
) -> hermetic.Called:
    """Call the helper in-process with what the task body hands it (#1697).

    The body finds the tree's config and lock and pipes its tracked
    workflows to the helper; the rows' trees always hold `mise.toml` and
    `mise.lock`, so the call names them and reads the same listing.

    Returns:
        The status and both streams.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plant(root, files, untracked or {})
        try:
            names = hermetic.tracked(
                root,
                ":!vendor/**",
                ".github/workflows/*.yml",
                ".github/workflows/*.yaml",
            )
        except RuntimeError:
            names = []  # the body's listing is empty here too
        listing = "".join(f"{name}\0" for name in names)
        return hermetic.call_main(
            lambda: lock_platforms.main([
                "--defaults",
                str(DEFAULTS),
                "--config",
                "mise.toml",
                "--lock",
                "mise.lock",
            ]),
            cwd=root,
            stdin=listing,
        )


def tree(**overrides: str | None) -> dict[str, str]:
    """Build the baseline tree with files replaced or (None) removed.

    Returns:
        path -> text.

    """
    files = {"mise.toml": CONFIG, "mise.lock": LOCK, STUB_PATH: RUST_BINARY}
    for key, value in overrides.items():
        rel = {"config": "mise.toml", "lock": "mise.lock", "stub": STUB_PATH}[key]
        if value is None:
            files.pop(rel)
        else:
            files[rel] = value
    return files


class Case(unittest.TestCase):
    """Assertions shared by the rows."""

    def assert_red(self, files: dict[str, str], message: str = RED) -> None:
        """Assert exit 1 with exactly `message` on stderr."""
        done = check(files)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, message)
        self.assertEqual(done.stdout, "")

    def assert_out(
        self, files: dict[str, str], line: str, **kw: dict[str, str]
    ) -> None:
        """Assert exit 0 with exactly one stdout line and no stderr."""
        done = check(files, **kw)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{NAME}: {line}\n")
        self.assertEqual(done.stderr, "")

    def assert_body_out(self, files: dict[str, str], line: str) -> None:
        """Assert the task body's own skip line: its subject is the body."""
        done = lint(files)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"{NAME}: {line}\n")
        self.assertEqual(done.stderr, "")

    def assert_ok(self, files: dict[str, str], count: int, platforms: str) -> None:
        """Assert the green summary line."""
        self.assert_out(
            files,
            f"{count} configured entries with platform tables in mise.lock"
            f" cover {platforms}",
        )


ALL = "linux-arm64,linux-x64,macos-arm64,macos-x64"


class Guards(Case):
    """Each guard planted both directions against the baseline."""

    def test_the_baseline_is_red(self) -> None:
        """Every guard's input present: the v1.3.0 shape reds."""
        self.assert_red(tree())

    def test_no_mise_config_skips(self) -> None:
        """No config: nothing installs, nothing to cover."""
        self.assert_body_out(tree(config=None), "no mise config, skipped")

    def test_no_lock_skips(self) -> None:
        """No lock: --locked is not in play."""
        self.assert_body_out(tree(lock=None), "no mise.lock, skipped")

    def test_no_tracked_workflow_skips(self) -> None:
        """No workflows at all: no publish stub."""
        self.assert_out(tree(stub=None), "no publish stub, skipped")

    def test_an_untracked_stub_is_not_read(self) -> None:
        """Tracked files only: a stub on disk but not in git is not the stub."""
        self.assert_out(
            tree(stub=None),
            "no publish stub, skipped",
            untracked={STUB_PATH: RUST_BINARY},
        )

    def test_a_workflow_calling_something_else_skips(self) -> None:
        """Only a caller of publish.yml is the publish stub."""
        other = RUST_BINARY.replace("workflows/publish.yml", "workflows/release.yml")
        self.assert_out(tree(stub=other), "no publish stub, skipped")

    def test_no_binary_class_skips(self) -> None:
        """The canon's own shape: source-archive builds on no target runner."""
        self.assert_out(
            tree(stub=stub("      classes: source-archive\n")),
            f"{STUB_PATH} declares no binary class, skipped",
        )


class Green(Case):
    """Shapes the lint must pass."""

    def test_all_four_platforms_pass(self) -> None:
        """The fixed lock: edtf after #185."""
        self.assert_ok(tree(lock=entry("node", "26.5.1", FOUR)), 1, ALL)

    def test_a_tool_without_platform_tables_is_outside(self) -> None:
        """cargo: and rust entries carry no tables; nothing to judge."""
        lock = entry("node", "26.5.1", FOUR) + entry("cargo:cargo-fuzz", "0.13.2", ())
        self.assert_ok(tree(lock=lock), 1, ALL)

    def test_a_stale_lock_entry_is_not_installed(self) -> None:
        """v1.3.0's lock carried nine unconfigured entries; the leg named none."""
        lock = entry("node", "26.5.1", FOUR) + entry("gitleaks", "8.30.1", THREE)
        self.assert_ok(tree(lock=lock), 1, ALL)

    def test_an_os_restricted_tool_is_held_only_where_it_installs(self) -> None:
        """Edtf's icu is macos-only; its linux tables are not owed."""
        config = CONFIG + ICU
        lock = entry("node", "26.5.1", FOUR) + entry(
            "github:unicode-org/icu", "78.3", ("macos-arm64", "macos-x64")
        )
        self.assert_ok(tree(config=config, lock=lock), 2, ALL)

    def test_a_declared_subset_is_all_that_is_owed(self) -> None:
        """binary-targets read from the stub override the canon default."""
        subset = stub(
            "      classes: rust-binary\n"
            '      binary-targets: "x86_64-unknown-linux-musl,aarch64-apple-darwin"\n'
        )
        self.assert_ok(tree(stub=subset), 1, "linux-x64,macos-arm64")

    def test_the_canons_local_caller_is_read(self) -> None:
        """self-publish.yml's `uses: ./.github/workflows/publish.yml` counts."""
        local = stub(
            "      classes: rust-binary\n", uses="./.github/workflows/publish.yml"
        )
        self.assert_red(tree(stub=local))


class Red(Case):
    """Planted defects red with the stated message."""

    def test_an_os_restricted_tool_missing_its_own_platform_is_red(self) -> None:
        """Os = ["macos"] still owes macos-x64."""
        config = "[tools]\n" + ICU
        lock = entry("github:unicode-org/icu", "78.3", ("macos-arm64",))
        self.assert_red(
            tree(config=config, lock=lock),
            RED.replace("node@26.5.1", "github:unicode-org/icu@78.3").replace(
                "macos-x64 node", "macos-x64 github:unicode-org/icu"
            ),
        )

    def test_go_binary_reads_its_own_input(self) -> None:
        """go-binary's default is go-targets', named as such."""
        self.assert_red(
            tree(stub=stub("      classes: go-binary\n")),
            RED.replace("rust-binary: binary-targets", "go-binary: go-targets"),
        )

    def test_every_missing_platform_gets_its_command(self) -> None:
        """Two platforms missing: two resolve lines, sorted."""
        lock = entry("node", "26.5.1", ("linux-x64", "macos-arm64"))
        source = (
            f"    declared by {STUB_PATH}"
            " (rust-binary: binary-targets, the canon default)\n"
        )
        self.assert_red(
            tree(lock=lock),
            f"{NAME}: mise.lock lacks platforms the release builds on:\n"
            "  linux-arm64: node@26.5.1\n"
            + source
            + "  macos-x64: node@26.5.1\n"
            + source
            + "  each binary leg runs `mise install --locked`"
            " on its target's own runner\n"
            "  and fails there, after the tag is minted (#810). Resolve them with:\n"
            "    mise lock --platform linux-arm64 node\n"
            "    mise lock --platform macos-x64 node\n",
        )


class Unreadable(Case):
    """A declaration that cannot be read is refused, never passed."""

    def assert_refused(self, files: dict[str, str], detail: str) -> None:
        """Assert exit 2 and the refusal naming `detail`."""
        done = check(files)
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertEqual(done.stdout, "")
        first = done.stderr.splitlines()[0]
        self.assertTrue(
            first.startswith(f"{NAME}: cannot read the declared release targets"), first
        )
        self.assertIn(detail, first)

    def test_a_config_or_lock_it_cannot_read_is_refused_by_name(self) -> None:
        """Not TOML, or `tools` / an entry not a table: named, exit 2."""
        for files, said in (
            (tree(config="[tools\n"), "cannot read mise.toml — "),
            (
                tree(config="tools = 1\n"),
                "cannot read mise.toml — tools is not an object",
            ),
            (
                tree(lock="tools = 1\n"),
                "cannot read mise.lock — tools is not an object",
            ),
            (
                tree(lock='[tools]\nnode = ["26.5.1"]\n'),
                "cannot read mise.lock — tools.node[0] is not an object",
            ),
        ):
            with self.subTest(said=said):
                done = lint(files)
                self.assertEqual((2, ""), (done.returncode, done.stdout))
                lines = done.stderr.splitlines()
                self.assertTrue(lines[0].startswith(f"{NAME}: {said}"), done.stderr)
                self.assertEqual(
                    "  a file this cannot read is not one it may pass", lines[1]
                )

    def test_a_block_scalar_is_refused(self) -> None:
        """A folded list would otherwise read as no targets."""
        folded = stub(
            "      classes: rust-binary\n"
            "      binary-targets: >-\n"
            "        x86_64-apple-darwin\n"
        )
        self.assert_refused(
            tree(stub=folded), "'>-' is not a plain comma-separated scalar"
        )

    def test_an_expression_is_refused(self) -> None:
        """A value known only at run time cannot be checked here."""
        expr = stub("      classes: ${{ vars.CLASSES }}\n")
        self.assert_refused(tree(stub=expr), "is not a plain comma-separated scalar")

    def test_an_unknown_target_is_refused(self) -> None:
        """No runner platform known: the build would refuse it too."""
        win = stub(
            "      classes: rust-binary\n      binary-targets: x86_64-pc-windows-msvc\n"
        )
        self.assert_refused(
            tree(stub=win), "'x86_64-pc-windows-msvc' has no known runner platform"
        )


class Defaults(unittest.TestCase):
    """The canon's declared defaults, read from the real publish.yml."""

    def test_every_default_target_maps(self) -> None:
        """Each canon default target has a platform key — all four, both classes."""
        lock = entry("node", "26.5.1", ("windows-x64",))
        both = stub("      classes: rust-binary,go-binary\n")
        done = check(tree(lock=lock, stub=both))
        self.assertEqual(done.returncode, 1, done.stderr)
        for key in ALL.split(","):
            self.assertIn(f"  {key}: node@26.5.1\n", done.stderr)


class ReadDocuments(unittest.TestCase):
    """`read_documents` in process: both files read, or one refused."""

    @staticmethod
    def read(config: str, lock: str) -> tuple[object, str, Path, Path]:
        """Plant mise.toml and mise.lock and read them in process.

        Returns:
            What `read_documents` returned, its stderr, and both paths.

        """
        root = Path(tempfile.mkdtemp())
        config_path, lock_path = root / "mise.toml", root / "mise.lock"
        config_path.write_text(config, encoding="utf-8")
        lock_path.write_text(lock, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = lock_platforms.read_documents(config_path, lock_path)
        return got, err.getvalue(), config_path, lock_path

    def test_both_files_read(self) -> None:
        """The baseline tree: node and cargo-fuzz configured, node locked."""
        got, err, _config, _lock = self.read(CONFIG, LOCK)
        self.assertEqual("", err)
        self.assertEqual(
            (
                {
                    "node": [("26.5.1", None)],
                    "cargo:cargo-fuzz": [("0.13.2", None)],
                },
                [("node", "26.5.1", set(THREE))],
            ),
            got,
        )

    def test_each_unreadable_file_is_named_and_none_returned(self) -> None:
        """The refused file, and only that one, is named."""
        for config, lock, which, said in (
            ("tools = 1\n", LOCK, "config", "tools is not an object"),
            (CONFIG, "tools = 1\n", "lock", "tools is not an object"),
        ):
            with self.subTest(which=which):
                got, err, config_path, lock_path = self.read(config, lock)
                path = config_path if which == "config" else lock_path
                self.assertIsNone(got)
                self.assertEqual(
                    f"{NAME}: cannot read {path} — {said}\n"
                    "  a file this cannot read is not one it may pass\n",
                    err,
                )


class Script(unittest.TestCase):
    """The one row that starts the helper as a process (#1697)."""

    def test_the_script_contract(self) -> None:
        """Started as a process, it reads its arguments and stdin, and exits 1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plant(root, tree(), {})
            done = subprocess.run(
                [
                    *(PYTHON, str(BELT / "lock_platforms.py")),
                    *("--defaults", str(DEFAULTS), "--config", "mise.toml"),
                    *("--lock", "mise.lock"),
                ],
                cwd=root,
                env=hermetic.env(MISE_TASK_NAME=NAME),
                input=f"{STUB_PATH}\0",
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stderr, RED)


if __name__ == "__main__":
    unittest.main()

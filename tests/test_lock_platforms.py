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
"""

import io
import itertools
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from belt import decoded, lock_platforms
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The helper's status for a lockfile it refuses.
REFUSED = 2

CANON = support.CANON
BELT = CANON / "mise"
BASH = support.tool("bash")
GIT = support.tool("git")
DEFAULTS = CANON / ".github" / "workflows" / "publish.yml"

NAME = "lint:lock-platforms"
STUB_PATH = ".github/workflows/publish.yml"
REMOTE = "monumental-archive/.github/.github/workflows/publish.yml@" + "a" * 40


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding (#1934)."""
    as_task(NAME)


pytestmark = pytest.mark.usefixtures("_task")


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
    files: dict[str, str], untracked: dict[str, str] | None = None, *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the lint in a git tree holding `files` tracked.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        environ = env()
        environ |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        for rel, text in (files | (untracked or {})).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        subprocess.run([GIT, "init", "-q"], cwd=root, env=environ, check=True)
        if files:
            subprocess.run(
                [GIT, "add", "--", *files], cwd=root, env=environ, check=True
            )
        as_mise = support.task_run("lint:lock-platforms", BODY)
        return subprocess.run(
            as_mise.argv,
            cwd=root,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


def plant(
    root: Path, files: dict[str, str], untracked: dict[str, str], *, env: support.Env
) -> None:
    """Write a git tree holding `files` tracked and `untracked` beside them."""
    environ = env()
    for rel, text in (files | untracked).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=root, env=environ, check=True)
    if files:
        subprocess.run([GIT, "add", "--", *files], cwd=root, env=environ, check=True)


# What the task body hands the helper when the tree holds both files.
CHECK = ["--defaults", str(DEFAULTS), "--config", "mise.toml", "--lock", "mise.lock"]


class Checked(Protocol):
    """What the `checked` fixture hands a row."""

    def __call__(
        self, files: dict[str, str], untracked: dict[str, str] | None = ...
    ) -> None:
        """Run checked."""
        ...


@fixture
def checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env
) -> Checked:
    """Plant a tree and pipe its listing, as the task body does (#1697).

    The body finds the tree's config and lock and pipes its tracked
    workflows to the helper; the rows' trees always hold `mise.toml` and
    `mise.lock`, so the call names them (`CHECK`) and reads the same
    listing.

    Returns:
        A function that plants `files` (and `untracked`) and steps in.

    """
    made = itertools.count()

    def lay_out(files: dict[str, str], untracked: dict[str, str] | None = None) -> None:
        root = tmp_path / f"tree{next(made)}"
        root.mkdir()
        plant(root, files, untracked or {}, env=env)
        try:
            names = support.tracked(
                root,
                ":!vendor/**",
                ".github/workflows/*.yml",
                ".github/workflows/*.yaml",
            )
        except RuntimeError:
            names = []  # the body's listing is empty here too
        listing = "".join(f"{name}\0" for name in names)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "stdin", io.StringIO(listing))

    return lay_out


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


# Assertions shared by the rows.
def assert_red(
    files: dict[str, str],
    message: str = RED,
    *,
    capfd: pytest.CaptureFixture[str],
    checked: Checked,
) -> None:
    """Assert exit 1 with exactly `message` on stderr."""
    checked(files)
    status = lock_platforms.main(CHECK)
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert err == message
    assert not out


def assert_out(
    files: dict[str, str],
    line: str,
    *,
    capfd: pytest.CaptureFixture[str],
    checked: Checked,
    **kw: dict[str, str],
) -> None:
    """Assert exit 0 with exactly one stdout line and no stderr."""
    checked(files, **kw)
    status = lock_platforms.main(CHECK)
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == f"{NAME}: {line}\n"
    assert not err


def assert_body_out(files: dict[str, str], line: str, *, env: support.Env) -> None:
    """Assert the task body's own skip line: its subject is the body."""
    done = lint(files, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{NAME}: {line}\n"
    assert not done.stderr


def assert_ok(
    files: dict[str, str],
    count: int,
    platforms: str,
    *,
    capfd: pytest.CaptureFixture[str],
    checked: Checked,
) -> None:
    """Assert the green summary line."""
    assert_out(
        files,
        f"{count} configured entries with platform tables in mise.lock"
        f" cover {platforms}",
        capfd=capfd,
        checked=checked,
    )


ALL = "linux-arm64,linux-x64,macos-arm64,macos-x64"


# Each guard planted both directions against the baseline.
def test_the_baseline_is_red(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Every guard's input present: the v1.3.0 shape reds."""
    assert_red(tree(), capfd=capfd, checked=checked)


def test_no_mise_config_skips(env: support.Env) -> None:
    """No config: nothing installs, nothing to cover."""
    assert_body_out(tree(config=None), "no mise config, skipped", env=env)


def test_no_lock_skips(env: support.Env) -> None:
    """No lock: --locked is not in play."""
    assert_body_out(tree(lock=None), "no mise.lock, skipped", env=env)


def test_an_untracked_config_is_no_config(env: support.Env) -> None:
    """A mise.toml on disk and not in git is not the tree's (#2013)."""
    done = lint(tree(config=None), untracked={"mise.toml": CONFIG}, env=env)
    assert (0, f"{NAME}: no mise config, skipped\n") == (
        done.returncode,
        done.stdout,
    )


def test_an_untracked_lock_is_no_lock(env: support.Env) -> None:
    """A mise.lock on disk and not in git is not the tree's (#2013)."""
    done = lint(tree(lock=None), untracked={"mise.lock": LOCK}, env=env)
    assert (0, f"{NAME}: no mise.lock, skipped\n") == (done.returncode, done.stdout)


def test_no_tracked_workflow_skips(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """No workflows at all: no publish stub."""
    assert_out(
        tree(stub=None), "no publish stub, skipped", capfd=capfd, checked=checked
    )


def test_an_untracked_stub_is_not_read(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Tracked files only: a stub on disk but not in git is not the stub."""
    assert_out(
        tree(stub=None),
        "no publish stub, skipped",
        untracked={STUB_PATH: RUST_BINARY},
        capfd=capfd,
        checked=checked,
    )


def test_a_workflow_calling_something_else_skips(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Only a caller of publish.yml is the publish stub."""
    other = RUST_BINARY.replace("workflows/publish.yml", "workflows/release.yml")
    assert_out(
        tree(stub=other), "no publish stub, skipped", capfd=capfd, checked=checked
    )


def test_no_binary_class_skips(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """The canon's own shape: source-archive builds on no target runner."""
    assert_out(
        tree(stub=stub("      classes: source-archive\n")),
        f"{STUB_PATH} declares no binary class, skipped",
        capfd=capfd,
        checked=checked,
    )


# Shapes the lint must pass.
def test_all_four_platforms_pass(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """The fixed lock: edtf after #185."""
    assert_ok(
        tree(lock=entry("node", "26.5.1", FOUR)), 1, ALL, capfd=capfd, checked=checked
    )


def test_a_tool_without_platform_tables_is_outside(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """cargo: and rust entries carry no tables; nothing to judge."""
    lock = entry("node", "26.5.1", FOUR) + entry("cargo:cargo-fuzz", "0.13.2", ())
    assert_ok(tree(lock=lock), 1, ALL, capfd=capfd, checked=checked)


def test_a_stale_lock_entry_is_not_installed(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """v1.3.0's lock carried nine unconfigured entries; the leg named none."""
    lock = entry("node", "26.5.1", FOUR) + entry("gitleaks", "8.30.1", THREE)
    assert_ok(tree(lock=lock), 1, ALL, capfd=capfd, checked=checked)


def test_an_os_restricted_tool_is_held_only_where_it_installs(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Edtf's icu is macos-only; its linux tables are not owed."""
    config = CONFIG + ICU
    lock = entry("node", "26.5.1", FOUR) + entry(
        "github:unicode-org/icu", "78.3", ("macos-arm64", "macos-x64")
    )
    assert_ok(tree(config=config, lock=lock), 2, ALL, capfd=capfd, checked=checked)


def test_a_declared_subset_is_all_that_is_owed(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """binary-targets read from the stub override the canon default."""
    subset = stub(
        "      classes: rust-binary\n"
        '      binary-targets: "x86_64-unknown-linux-musl,aarch64-apple-darwin"\n'
    )
    assert_ok(
        tree(stub=subset), 1, "linux-x64,macos-arm64", capfd=capfd, checked=checked
    )


def test_the_canons_local_caller_is_read(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """self-publish.yml's `uses: ./.github/workflows/publish.yml` counts."""
    local = stub("      classes: rust-binary\n", uses="./.github/workflows/publish.yml")
    assert_red(tree(stub=local), capfd=capfd, checked=checked)


# Planted defects red with the stated message.
def test_an_os_restricted_tool_missing_its_own_platform_is_red(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Os = ["macos"] still owes macos-x64."""
    config = "[tools]\n" + ICU
    lock = entry("github:unicode-org/icu", "78.3", ("macos-arm64",))
    assert_red(
        tree(config=config, lock=lock),
        RED.replace("node@26.5.1", "github:unicode-org/icu@78.3").replace(
            "macos-x64 node", "macos-x64 github:unicode-org/icu"
        ),
        capfd=capfd,
        checked=checked,
    )


def test_go_binary_reads_its_own_input(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """go-binary's default is go-targets', named as such."""
    assert_red(
        tree(stub=stub("      classes: go-binary\n")),
        RED.replace("rust-binary: binary-targets", "go-binary: go-targets"),
        capfd=capfd,
        checked=checked,
    )


def test_every_missing_platform_gets_its_command(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Two platforms missing: two resolve lines, sorted."""
    lock = entry("node", "26.5.1", ("linux-x64", "macos-arm64"))
    source = (
        f"    declared by {STUB_PATH}"
        " (rust-binary: binary-targets, the canon default)\n"
    )
    assert_red(
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
        capfd=capfd,
        checked=checked,
    )


# A declaration that cannot be read is refused, never passed.
def assert_refused(
    files: dict[str, str],
    detail: str,
    *,
    capfd: pytest.CaptureFixture[str],
    checked: Checked,
) -> None:
    """Assert exit 2 and the refusal naming `detail`."""
    checked(files)
    status = lock_platforms.main(CHECK)
    out, err = capfd.readouterr()
    assert status == REFUSED, out
    assert not out
    first = err.splitlines()[0]
    assert first.startswith(f"{NAME}: cannot read the declared release targets"), first
    assert detail in first


@pytest.mark.parametrize(
    ("files", "said"),
    [
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
    ],
)
def test_a_config_or_lock_it_cannot_read_is_refused_by_name(
    files: dict[str, str], said: str, env: support.Env
) -> None:
    """Not TOML, or `tools` / an entry not a table: named, exit 2."""
    done = lint(files, env=env)
    assert (done.returncode, done.stdout) == (2, "")
    lines = done.stderr.splitlines()
    assert lines[0].startswith(f"{NAME}: {said}"), done.stderr
    assert lines[1] == "  a file this cannot read is not one it may pass"


def test_a_block_scalar_is_refused(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """A folded list would otherwise read as no targets."""
    folded = stub(
        "      classes: rust-binary\n"
        "      binary-targets: >-\n"
        "        x86_64-apple-darwin\n"
    )
    assert_refused(
        tree(stub=folded),
        "'>-' is not a plain comma-separated scalar",
        capfd=capfd,
        checked=checked,
    )


def test_an_expression_is_refused(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """A value known only at run time cannot be checked here."""
    expr = stub("      classes: ${{ vars.CLASSES }}\n")
    assert_refused(
        tree(stub=expr),
        "is not a plain comma-separated scalar",
        capfd=capfd,
        checked=checked,
    )


def test_an_unknown_target_is_refused(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """No runner platform known: the build would refuse it too."""
    win = stub(
        "      classes: rust-binary\n      binary-targets: x86_64-pc-windows-msvc\n"
    )
    assert_refused(
        tree(stub=win),
        "'x86_64-pc-windows-msvc' has no known runner platform",
        capfd=capfd,
        checked=checked,
    )


# The canon's declared defaults, read from the real publish.yml.
def test_every_default_target_maps(
    capfd: pytest.CaptureFixture[str], checked: Checked
) -> None:
    """Each canon default target has a platform key — all four, both classes."""
    lock = entry("node", "26.5.1", ("windows-x64",))
    both = stub("      classes: rust-binary,go-binary\n")
    checked(tree(lock=lock, stub=both))
    status = lock_platforms.main(CHECK)
    err = capfd.readouterr().err
    assert status == 1, err
    for key in ALL.split(","):
        assert f"  {key}: node@26.5.1\n" in err


# `read_documents` in process: both files read, or one refused.
def documents(root: Path, config: str, lock: str) -> tuple[Path, Path]:
    """Plant mise.toml and mise.lock for `read_documents`.

    Returns:
        Both paths.

    """
    config_path, lock_path = root / "mise.toml", root / "mise.lock"
    config_path.write_text(config, encoding="utf-8")
    lock_path.write_text(lock, encoding="utf-8")
    return config_path, lock_path


def test_both_files_read(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """The baseline tree: node and cargo-fuzz configured, node locked."""
    got = lock_platforms.read_documents(*documents(tmp_path, CONFIG, LOCK))
    err = capfd.readouterr().err
    assert not err
    assert (
        {
            "node": [("26.5.1", None)],
            "cargo:cargo-fuzz": [("0.13.2", None)],
        },
        [("node", "26.5.1", set(THREE))],
    ) == got


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(
            ("tools = 1\n", LOCK, "config", "tools is not an object"), id="tools = 1\n"
        ),
        pytest.param((CONFIG, "tools = 1\n", "lock", "tools is not an object"), id="1"),
    ],
)
def test_each_unreadable_file_is_named_and_none_returned(
    row: tuple[str, str, object, object],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The refused file, and only that one, is named."""
    config, lock, which, said = row
    config_path, lock_path = documents(tmp_path, config, lock)
    got = lock_platforms.read_documents(config_path, lock_path)
    err = capfd.readouterr().err
    path = config_path if which == "config" else lock_path
    assert got is None
    assert (
        f"{NAME}: cannot read {path} — {said}\n"
        "  a file this cannot read is not one it may pass\n"
    ) == err


@fixture
def located(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env
) -> Callable[[dict[str, str]], None]:
    """Plant a tree for the helper to find its files in, as the body does.

    Returns:
        A function that plants `files` and steps in, no --config given.

    """
    made = itertools.count()

    def lay_out(files: dict[str, str]) -> None:
        root = tmp_path / f"tree{next(made)}"
        root.mkdir()
        plant(root, files, {}, env=env)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{STUB_PATH}\0"))

    return lay_out


OK_LOCK = entry("node", "26.5.1", FOUR)


# The helper finds the tree's config and the lock beside it (#1911).
def assert_skip(
    files: dict[str, str],
    line: str,
    *,
    capfd: pytest.CaptureFixture[str],
    located: Callable[[dict[str, str]], None],
) -> None:
    """Assert a skip, said on stdout, with nothing on stderr."""
    located(files)
    status = lock_platforms.main(["--defaults", str(DEFAULTS)])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == f"{NAME}: {line}\n"
    assert not err


def test_no_config_skips(
    capfd: pytest.CaptureFixture[str], located: Callable[[dict[str, str]], None]
) -> None:
    """None of the three configs: a skip, the stub never read."""
    assert_skip(
        {STUB_PATH: RUST_BINARY},
        "no mise config, skipped",
        capfd=capfd,
        located=located,
    )


def test_no_lock_beside_the_config_skips(
    capfd: pytest.CaptureFixture[str], located: Callable[[dict[str, str]], None]
) -> None:
    """A lock beside another config is not this config's lock."""
    files = {STUB_PATH: RUST_BINARY, "mise/config.toml": CONFIG, "mise.lock": LOCK}
    assert_skip(files, "no mise/mise.lock, skipped", capfd=capfd, located=located)


def test_each_config_is_found_in_order(
    capfd: pytest.CaptureFixture[str], located: Callable[[dict[str, str]], None]
) -> None:
    """mise/config.toml, then mise.toml, then .mise.toml; its lock beside."""
    ok = (
        "1 configured entries with platform tables in {} cover"
        " linux-arm64,linux-x64,macos-arm64,macos-x64"
    )
    rows = {
        "nested first": (
            {"mise/config.toml": CONFIG, "mise/mise.lock": OK_LOCK},
            {"mise.toml": CONFIG, "mise.lock": LOCK},
            ok.format("mise/mise.lock"),
        ),
        "mise.toml second": (
            {"mise.toml": CONFIG, "mise.lock": OK_LOCK},
            {".mise.toml": "[tools]\n"},
            ok.format("mise.lock"),
        ),
        "dotted last": (
            {".mise.toml": CONFIG, "mise.lock": OK_LOCK},
            {},
            ok.format("mise.lock"),
        ),
    }
    for found, beside, line in rows.values():
        assert_skip(
            {STUB_PATH: RUST_BINARY} | found | beside,
            line,
            capfd=capfd,
            located=located,
        )


def test_a_found_lock_is_named_in_the_red(
    capfd: pytest.CaptureFixture[str], located: Callable[[dict[str, str]], None]
) -> None:
    """The nested lock short of a platform: red, the lock named."""
    files = {
        STUB_PATH: RUST_BINARY,
        "mise/config.toml": CONFIG,
        "mise/mise.lock": LOCK,
    }
    located(files)
    status = lock_platforms.main(["--defaults", str(DEFAULTS)])
    out, err = capfd.readouterr()
    assert status == 1, out + err
    assert err == RED.replace("mise.lock", "mise/mise.lock", 1)
    assert not out

"""Table tests for lint:pg-upgrade-path's guard branches, both directions.

#825: the guard inferred installability from the derivation's past
behaviour. A version that is only ever an upgrade TARGET was called
"burned" — the Release PR committed its script and the publish then
failed, so nobody could install it — when the same filenames are equally
consistent with the version having published fine and the next release
simply not being derived from it. In that second case every installation
on it is stranded and the guard said the opposite, in green.

The graph planted below is the one the issue was measured on:

    1.2.2--1.2.3, 1.2.3--1.3.1, 1.2.3--1.3.2, current 1.3.2

so 1.3.1 is the target nothing was derived from — the open question. It
resolves from `.pgrx-installable`, the derived record the release path
writes (release/generate-pgrx-upgrade.sh), and BOTH directions are
planted here: 1.3.1 present in the record must red, absent must pass,
and neither message may assert a fact the record does not hold. The
no-record interim path is a branch in its own right and gets its own
rows, because a tree that has not been through a Release PR since #825
is the normal state of every repository the day this lands.

THE RECORD MAY ACCUSE, NEVER EXCUSE. A `from` version owes a path
whatever the record says about it — it is a `from` because a later
release was derived from it, which is the forge's own answer at that
time — so a row plants a `from` the record omits and requires the guard
to still red. A deleted asset must not be able to retire a real
obligation.

The trees are real: a control file, a crate `cargo pkgid` can resolve,
a tracked upgrade graph, and `git ls-files` answering for all of it,
because that is what the script reads. The lockfile is generated
`--offline` (the crates have no dependencies, so nothing is fetched).

GIT ENVIRONMENT IS SCRUBBED, deliberately — see #857. `git` reads
`GIT_DIR` and `GIT_INDEX_FILE` from the environment and applies them
regardless of `cwd`, and git EXPORTS `GIT_DIR` to hooks run in a linked
worktree. A harness that inherits the caller's environment therefore
writes its fixture into the caller's real index when the suite runs from
a pre-push hook, which is how three lanes lost their indexes on
2026-08-24. Every subprocess below is handed an environment with the
`GIT_*` family removed.

Run through the gate as `mise run test`, which `ci` collects.
"""

import subprocess
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict, Unpack

import pytest

from belt import pg_upgrade_path
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
SCRIPT = CANON / "mise" / "pg-upgrade-path.sh"
RECORD = ".pgrx-installable"

# Absolute, resolved once: a partial path lets PATH decide which binary a
# test ran.
BASH = support.tool("bash")
GIT = support.tool("git")
CARGO = support.optional_tool("cargo")

# The graph the defect was measured on. 1.3.1 is the target nothing was
# derived from; 1.2.2 and 1.2.3 are `from` halves and always owe a path.
PLANTED = [("1.2.2", "1.2.3"), ("1.2.3", "1.3.1"), ("1.2.3", "1.3.2")]
CURRENT = "1.3.2"


def clean_env(*, env: support.Env) -> dict[str, str]:
    """Build the caller's environment with git's own variables removed.

    Returns:
        A copy of os.environ carrying no GIT_* variable, so a `git` call
        below cannot be redirected at the caller's repository (#857).

    """
    return env()


def record_file(
    *lines: str,
    observed: str = "2026-08-24",
    derived: str = "v1.3.2",
) -> str:
    """Render a `.pgrx-installable` with the given extension lines.

    Returns:
        The whole file, provenance comments and all.

    """
    head = [
        "# pgrx installable versions -- DERIVED STATE (#825).",
        f"# observed: {observed}",
        f"# derived: {derived}",
    ]
    return "\n".join([*head, *lines]) + "\n"


class Layout(TypedDict, total=False):
    """What a row lays out, each with its default."""

    version: str
    edges: list[tuple[str, str]] | None
    record: str | None
    name: str


class Tree:
    """A throwaway pgrx repository with a chosen graph and record."""

    def __init__(
        self,
        root: str,
        *,
        env: support.Env,
        **options: Unpack[Layout],
    ) -> None:
        """Lay out the control file, the crate, the graph and the record."""
        version = options.get("version", CURRENT)
        edges = options.get("edges")
        record = options.get("record")
        name = options.get("name", "demo_pg")
        self.make_env = env
        self.root = Path(root)
        self.name = name
        # pgrx extensions are named with underscores and their crates with
        # hyphens; one argument, not two that can disagree.
        self.crate_dir = self.root / "crates" / name.replace("_", "-")
        (self.crate_dir / "sql").mkdir(parents=True)
        (self.crate_dir / "src").mkdir()
        (self.crate_dir / "src" / "lib.rs").write_text("pub fn f() {}\n")
        (self.crate_dir / f"{name}.control").write_text(
            "default_version = '@CARGO_VERSION@'\n"
        )
        (self.crate_dir / "Cargo.toml").write_text(
            textwrap.dedent(f"""\
                [package]
                name = "{name.replace("_", "-")}"
                version = "{version}"
                edition = "2021"
                """)
        )
        for frm, to in edges or []:
            (self.crate_dir / "sql" / f"{name}--{frm}--{to}.sql").write_text("")
        if record is not None:
            (self.root / RECORD).write_text(record)

        self.git("init", "-q", ".")
        self.git("add", "-A")

    def git(self, *args: str) -> None:
        """Run one git command against this tree, and nothing else (#857)."""
        subprocess.run(
            [GIT, *args],
            cwd=self.root,
            env=clean_env(env=self.make_env),
            check=True,
            capture_output=True,
        )

    def drop_control(self) -> None:
        """Make this an ordinary Rust repository, not a pgrx one."""
        (self.crate_dir / f"{self.name}.control").unlink()
        self.git("add", "-A")

    def lint(self) -> subprocess.CompletedProcess[str]:
        """Run the real guard over this tree.

        Returns:
            The completed process; rows assert on stdout and the status.

        """
        env = clean_env(env=self.make_env)
        subprocess.run(
            [
                support.tool("cargo"),
                "generate-lockfile",
                "--offline",
                "--manifest-path",
                str(self.crate_dir / "Cargo.toml"),
            ],
            cwd=self.root,
            env=env,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


# Rows the guard answers by resolving the crate version with cargo.
@fixture
def _set_up_test_needs_cargo() -> None:
    """Skip the row where cargo is not on PATH."""
    if not CARGO:
        pytest.skip("cargo resolves the crate version the guard reads")


# A target-only version: burned dead end, or stranded installation.
@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_recorded_installable_target_only_version_reds(env: support.Env) -> None:
    """1.3.1 published: installations exist on it and nothing reaches."""
    with TemporaryDirectory() as d:
        result = Tree(
            d,
            edges=PLANTED,
            record=record_file("demo_pg 1.3.2 1.3.1 1.2.3 1.2.2"),
            env=env,
        ).lint()
    assert result.returncode == 1, result.stdout
    assert "demo_pg 1.3.1 is a target nothing was derived from," in result.stdout
    assert "it published, so installations on it are stranded" in result.stdout
    assert "no ALTER EXTENSION UPDATE path to 1.3.2 from: 1.3.1" in result.stdout
    assert "--<from>--<to>.sql to connect them" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_target_only_version_absent_from_the_record_passes(env: support.Env) -> None:
    """1.3.1 burned: no release carries it, so nothing can be on it."""
    with TemporaryDirectory() as d:
        result = Tree(
            d, edges=PLANTED, record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"), env=env
        ).lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing can be installed on it" in result.stdout
    assert "every installable demo_pg version reaches 1.3.2" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_neither_outcome_asserts_what_the_record_does_not_hold(
    env: support.Env,
) -> None:
    """The word the defect was filed about is gone from both branches."""
    with TemporaryDirectory() as d:
        passing = Tree(
            d, edges=PLANTED, record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"), env=env
        ).lint()
    with TemporaryDirectory() as d:
        failing = Tree(
            d,
            edges=PLANTED,
            record=record_file("demo_pg 1.3.2 1.3.1 1.2.3 1.2.2"),
            env=env,
        ).lint()
    for result in (passing, failing):
        assert "burned" not in result.stdout
    # The pass cites the record it relied on rather than asserting a
    # bare fact about the forge.
    assert "v1.3.2, observed 2026-08-24" in passing.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_the_record_may_accuse_but_never_excuse(env: support.Env) -> None:
    """A `from` the record omits still owes a path to the current one."""
    with TemporaryDirectory() as d:
        result = Tree(
            d,
            edges=[("1.0.0", "1.1.0"), *PLANTED],
            # 1.0.0 is a `from`, and deliberately absent here.
            record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            env=env,
        ).lint()
    assert result.returncode == 1, result.stdout
    assert "1.0.0" in result.stdout


# No record: the question is open and the guard has to say so.
@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_no_record_states_the_question_instead_of_closing_it(env: support.Env) -> None:
    """Green, but it does not pretend to know that 1.3.1 burned."""
    with TemporaryDirectory() as d:
        result = Tree(d, edges=PLANTED, env=env).lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "if it did, installations on it are stranded" in result.stdout
    assert "burned" not in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_no_record_does_not_claim_every_published_version_reaches(
    env: support.Env,
) -> None:
    """The success line is the one #825 was filed about. Bound it."""
    with TemporaryDirectory() as d:
        result = Tree(d, edges=PLANTED, env=env).lint()
    assert "every published demo_pg version" not in result.stdout
    assert "was not checked" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_record_naming_another_extension_is_the_interim_path(
    env: support.Env,
) -> None:
    """A crate added since the last release was never asked about."""
    with TemporaryDirectory() as d:
        result = Tree(
            d, edges=PLANTED, record=record_file("other_ext 9.9.9"), env=env
        ).lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "if it did, installations on it are stranded" in result.stdout


# A record that cannot be trusted reds; it is never quietly repaired.
@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_record_without_provenance_is_refused(env: support.Env) -> None:
    """Same law as .coverage-floor: derived state carries its derivation."""
    with TemporaryDirectory() as d:
        result = Tree(
            d, edges=PLANTED, record="demo_pg 1.3.2 1.2.3 1.2.2\n", env=env
        ).lint()
    assert result.returncode == 1, result.stdout
    assert "carries no" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_record_naming_an_extension_twice_is_refused(env: support.Env) -> None:
    """Two answers to one question is drift, not a merge to guess at."""
    with TemporaryDirectory() as d:
        result = Tree(
            d,
            edges=PLANTED,
            record=record_file("demo_pg 1.2.3", "demo_pg 1.2.2"),
            env=env,
        ).lint()
    assert result.returncode == 1, result.stdout
    assert "is named twice" in result.stdout


# The behaviour #825 must not have cost, in both directions.
@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_repo_with_no_control_file_skips_clean(env: support.Env) -> None:
    """A linter that cannot skip cannot be universal."""
    with TemporaryDirectory() as d:
        tree = Tree(d, edges=PLANTED, env=env)
        tree.drop_control()
        result = tree.lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no extension control files tracked" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_an_empty_graph_is_a_first_publish_and_skips_clean(env: support.Env) -> None:
    """No upgrade scripts means no published predecessor to strand."""
    with TemporaryDirectory() as d:
        result = Tree(d, version="1.0.0", edges=[], env=env).lint()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "first publish has no predecessor" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_a_release_that_derived_no_script_still_reds(env: support.Env) -> None:
    """#816's own catch, kept: a bump with no new script strands all.

    The graph stops at 1.3.2 while the crate says 1.4.0, which is
    exactly what a burned predecessor produced in #816. Reachability
    fails for every version before the endpoint guard downstream of
    it is consulted, so that guard stays the backstop it has always
    been and this asserts what actually fires.
    """
    with TemporaryDirectory() as d:
        result = Tree(
            d,
            version="1.4.0",
            edges=PLANTED,
            record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            env=env,
        ).lint()
    assert result.returncode == 1, result.stdout
    assert "no ALTER EXTENSION UPDATE path to 1.4.0" in result.stdout


@pytest.mark.usefixtures("_set_up_test_needs_cargo")
def test_only_the_record_sourced_accusation_is_explained(env: support.Env) -> None:
    """A `from` is on the hook by the graph, and needs no record line.

    The explanation exists to carry information the reader cannot get
    from the filenames. Printing it for every stranded version buried
    the one line that did.
    """
    with TemporaryDirectory() as d:
        result = Tree(
            d,
            version="1.4.0",
            edges=PLANTED,
            record=record_file("demo_pg 1.3.2 1.2.3 1.2.2"),
            env=env,
        ).lint()
    # 1.3.2 is a target-only version the record accuses; 1.2.2 and
    # 1.2.3 are `from` halves and get no such line.
    assert "demo_pg 1.3.2 is a target nothing was derived from," in result.stdout
    assert "demo_pg 1.2.2 is a target" not in result.stdout
    assert "demo_pg 1.2.3 is a target" not in result.stdout


# The judgment itself, in process: the rows above run the task body,
# which starts the judgment as a child, so they measure none of it.
NAME = "demo_pg"
SQL = "sql"


def scripts(edges: list[tuple[str, str]]) -> list[str]:
    """Name one upgrade script per edge, as the task body hands them over.

    Returns:
        The scripts' paths under SQL.

    """
    return [f"{SQL}/{NAME}--{start}--{end}.sql" for start, end in edges]


def judged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    record: str | None = None,
) -> int:
    """Run the command in process with RECORD in a fresh directory.

    Returns:
        The command's exit status.

    """
    path = tmp_path / RECORD
    if record is not None:
        path.write_text(record, encoding="utf-8")
    monkeypatch.setenv("RECORD", str(path))
    return pg_upgrade_path.main(argv)


def test_a_script_name_off_the_pattern_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A script the graph cannot read is named, with the shape it expected."""
    argv = [NAME, CURRENT, SQL, f"{SQL}/other--1.0--1.1.sql"]
    assert judged(tmp_path, monkeypatch, argv) == pg_upgrade_path.EXIT_REFUSED
    out = capfd.readouterr().out
    assert "::error::cannot parse upgrade script name: other--1.0--1.1.sql" in out
    assert f"::error::expected {NAME}--<from>--<to>.sql" in out


def test_no_record_passes_and_says_publication_was_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Without the record the target-only version is an open question."""
    argv = [NAME, CURRENT, SQL, *scripts(PLANTED)]
    assert judged(tmp_path, monkeypatch, argv) == 0
    out = capfd.readouterr().out
    assert f"NOTE: {NAME} 1.3.1 is a target nothing was derived from; no" in out
    assert "whether a" in out
    assert "target-only version published was not checked" in out
    assert f"ok  {NAME}--1.2.2--1.2.3.sql" in out


def test_a_recorded_target_absent_from_the_record_passes_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A target the record does not carry need not reach the current one."""
    argv = [NAME, CURRENT, SQL, *scripts(PLANTED)]
    record = record_file(f"{NAME} 1.3.2 1.2.3 1.2.2", "", "other_pg 9.9")
    assert judged(tmp_path, monkeypatch, argv, record) == 0
    out = capfd.readouterr().out
    assert "nothing can be installed on it; need not reach 1.3.2" in out
    assert f"::notice::every installable {NAME} version reaches {CURRENT}" in out
    assert "::notice::installable set recorded v1.3.2, observed 2026-08-24" in out


def test_a_recorded_target_is_owed_a_path_and_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The record accuses: a published target-only version is stranded."""
    argv = [NAME, CURRENT, SQL, *scripts(PLANTED)]
    record = record_file(f"{NAME} 1.3.1 1.3.2 1.2.3 1.2.2")
    assert judged(tmp_path, monkeypatch, argv, record) == 1
    out = capfd.readouterr().out
    assert f"::error::{NAME} 1.3.1 is a target nothing was derived from," in out
    assert "::error::no ALTER EXTENSION UPDATE path to 1.3.2 from: 1.3.1" in out
    assert f"::error::add {SQL}/{NAME}--<from>--<to>.sql to connect them" in out
    assert "NOTE:" not in out


def test_a_stranded_from_gets_no_record_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A cycle that never reaches the current version strands every `from` in it."""
    edges = [("1.0.0", "1.1.0"), ("1.1.0", "1.0.0"), ("1.2.0", "2.0.0")]
    argv = [NAME, "2.0.0", SQL, *scripts(edges)]
    assert judged(tmp_path, monkeypatch, argv, record_file(f"{NAME} 1.0.0")) == 1
    out = capfd.readouterr().out
    assert "::error::no ALTER EXTENSION UPDATE path to 2.0.0 from: 1.0.0 1.1.0" in out
    assert "is a target nothing was derived from" not in out


def test_a_current_version_no_script_reaches_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """With no scripts at all, the current version is no endpoint."""
    assert judged(tmp_path, monkeypatch, [NAME, CURRENT, SQL]) == 1
    out = capfd.readouterr().out
    assert f"::error::{NAME} {CURRENT} is not an endpoint of the upgrade graph" in out
    assert f"::error::expected {SQL}/{NAME}--<previous>--{CURRENT}.sql" in out


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        (
            "# observed: 2026-08-24\n# a comment\n# key: value\n",
            "carries no `# observed:` and `# derived:` derivation",
        ),
        (
            record_file(f"{NAME} 1.3.2", f"{NAME} 1.3.1"),
            f"line 5: {NAME} is named twice",
        ),
    ],
    ids=["no derivation", "named twice"],
)
def test_a_record_off_the_derived_state_law_is_refused(
    record: str,
    problem: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A record the release path did not write is drift, and reds."""
    argv = [NAME, CURRENT, SQL, *scripts(PLANTED)]
    assert judged(tmp_path, monkeypatch, argv, record) == 1
    out = capfd.readouterr().out
    assert f"::error::{tmp_path / RECORD} {problem}" in out
    assert "regenerate it by re-running the Release PR, never by hand" in out


def test_a_missing_argument_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The parser refuses a call with no SQL_DIR, with its own message."""
    with pytest.raises(SystemExit) as stopped:
        judged(tmp_path, monkeypatch, [NAME, CURRENT])
    assert stopped.value.code == support.USAGE
    assert "the following arguments are required: SQL_DIR" in capfd.readouterr().err

"""Table tests for the workspace-inheritance check, guard branches first.

Every fixture here is shaped from a MEASURED cargo account, never from
what cargo's documentation implies. A hand-written fixture proves only
that the code agrees with the fixture, and three of the four resolution
shapes below would have been guessed wrong — each one a false RED on a
repository that had done nothing at all.

Measured 2026-08-24 with `cargo metadata --no-deps --format-version 1`
against a purpose-built two-member workspace (one member inheriting every
key, one inheriting none), and against release-lab and edtf:

    declared                     inheriting member    member taking none
    version/edition/license/…    the value verbatim   own value, or None
    authors/keywords/categories  the list verbatim    []  (not None)
    readme = "README.md"         "../../README.md"    None
    readme = true                "../../README.md"    None
    publish = false              []                   []   <- identical
    publish = true               None                 []

The `[]` row is why "every member reports null" is not the test, the
rebased-path row is why value equality alone is not the test, and the
two `publish` rows are why publish gets no verdict at all.

Run through the gate as `mise run test`, which `ci` collects.
"""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import workspace_inherit as wi
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

BELT = support.MISE

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:workspace-inherit"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


ROOT = Path("/ws")
# What a row expects when no key is inert.
NO_FINDINGS: list[wi.Finding] = []

# `readme = true` and `readme = false` as TOML hands them over: a
# declared VALUE the check resolves, never a flag on the call.
DISCOVER = True
NO_README = False


def member(name: str, **fields: object) -> wi.Member:
    """Build one member as the check decodes it from cargo metadata.

    Returns:
        The decoded member of `raw_member`'s package.

    """
    return wi.read_member(raw_member(name, **fields), name)


def raw_member(name: str, **fields: object) -> dict[str, object]:
    """Build one package as cargo metadata reports it.

    Returns:
        A package dict carrying a manifest two directories down, which
        is the layout every workspace in the org uses.

    """
    return {
        "name": name,
        "id": f"path+file:///ws/crates/{name}#0.1.0",
        "manifest_path": f"/ws/crates/{name}/Cargo.toml",
        **fields,
    }


# The one question this check asks, per key and per member.
def test_a_verbatim_value_is_taken() -> None:
    """The ordinary shape: cargo reports the declaration unchanged."""
    taker = member("taker", rust_version="1.82")
    assert wi.inherits("rust-version", "1.82", taker, ROOT) is True


def test_a_member_with_its_own_value_takes_nothing() -> None:
    """A different value is a different declaration, not inheritance."""
    own = member("own", edition="2018")
    assert wi.inherits("edition", "2021", own, ROOT) is False


def test_an_absent_field_takes_nothing() -> None:
    """release-lab's live case: rust_version None on all four."""
    bare = member("bare", rust_version=None)
    assert wi.inherits("rust-version", "1.97", bare, ROOT) is False


def test_a_list_valued_key_reports_empty_not_null() -> None:
    """Measured: a member taking no `keywords` reports `[]`.

    So an inertness test written as "every member reports null"
    would call this key taken and never fire.
    """
    rows = [member("bare", keywords=[]), member("taker", keywords=["k"])]
    got: list[bool | None] = [wi.inherits("keywords", ["k"], row, ROOT) for row in rows]
    want: list[bool | None] = [False, True]
    assert got == want


def test_a_path_key_is_rebased_by_cargo_and_still_counts() -> None:
    """THE row that value equality gets wrong.

    release-lab declares `readme = "README.md"` and all four members
    take it; cargo reports `../../README.md` for each. Comparing the
    two as strings reds a key nobody got wrong.
    """
    taker = member("taker", readme="../../README.md")
    assert wi.inherits("readme", "README.md", taker, ROOT) is True


def test_a_rebased_path_naming_another_file_does_not_count() -> None:
    """The other direction: rebasing is not a free pass."""
    other = member("other", readme="../../OTHER.md")
    assert wi.inherits("readme", "README.md", other, ROOT) is False


def test_a_bool_readme_resolves_to_whatever_cargo_found() -> None:
    """A readme declared true asks cargo to discover the file.

    The declared bool appears nowhere in the account, so the test is
    whether the member reports a readme at all.
    """
    rows = [member("taker", readme="../../README.md"), member("bare", readme=None)]
    got: list[bool | None] = [
        wi.inherits("readme", DISCOVER, row, ROOT) for row in rows
    ]
    want: list[bool | None] = [True, False]
    assert got == want


def test_a_false_readme_is_undecidable() -> None:
    """A readme declared false and "no readme" are one account.

    A member taking it reports None, and so does a member taking
    nothing — so this returns None rather than a verdict that would
    be right by luck.
    """
    bare = member("bare", readme=None)
    assert wi.inherits("readme", NO_README, bare, ROOT) is None


# The rule: red only where NO member takes the key.
def test_a_key_every_member_takes_is_not_reported() -> None:
    """Every member inheriting, which is edtf's live shape."""
    members = [member("a", rust_version="1.82"), member("b", rust_version="1.82")]
    assert wi.judge({"rust-version": "1.82"}, members, ROOT, []) == NO_FINDINGS


def test_a_key_only_one_member_takes_is_not_reported() -> None:
    """THE branch a stricter rule would get wrong.

    A workspace legitimately declares a key only some members want.
    One taker is a reader, and a key with a reader is not inert.
    """
    members = [member("a", rust_version="1.82"), member("b", rust_version=None)]
    assert wi.judge({"rust-version": "1.82"}, members, ROOT, []) == NO_FINDINGS


def test_a_key_no_member_takes_is_reported_with_every_member() -> None:
    """release-lab's live case: the remedy names who could take it."""
    members = [member("a", rust_version=None), member("b", rust_version=None)]
    findings = wi.judge({"rust-version": "1.97"}, members, ROOT, [])
    assert len(findings) == 1
    assert findings[0].key == "rust-version"
    takers: list[str] = ["a", "b"]
    assert findings[0].takers == takers


def test_publish_is_reported_unjudged_rather_than_guessed() -> None:
    """Measured: an inherited and an own publish are one account.

    A declared false comes back `[]` either way, so a verdict here
    would be right by luck in both directions.
    """
    report: list[str] = []
    members = [member("a", publish=[])]
    assert wi.judge({"publish": False}, members, ROOT, report) == NO_FINDINGS
    assert any("publish" in line for line in report)


def test_a_key_cargo_does_not_report_is_named_not_skipped() -> None:
    """`exclude` and `include` have no per-package value at all."""
    report: list[str] = []
    members = [member("a")]
    assert wi.judge({"exclude": ["x"]}, members, ROOT, report) == NO_FINDINGS
    assert any("exclude" in line for line in report)


def test_an_unknown_key_is_named_not_skipped() -> None:
    """A key this task cannot model is stated, never passed quietly."""
    report: list[str] = []
    assert wi.judge({"invented": 1}, [member("a")], ROOT, report) == NO_FINDINGS
    assert any("invented" in line for line in report)


def test_an_undecidable_key_is_not_red() -> None:
    """No taker plus no verdict is a note, not a finding."""
    report: list[str] = []
    members = [member("a", readme=None)]
    assert wi.judge({"readme": False}, members, ROOT, report) == NO_FINDINGS
    assert report


# Reading the declaration, which is the one thing read as TEXT.
#
# Only the declared KEYS come from the manifest; whether anyone takes
# them is always cargo's account. Reading the members' text is what
# made this class invisible, and is what the issue rejected.
def test_reads_the_declared_table() -> None:
    """The table arrives as written."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Cargo.toml").write_text(
            '[workspace.package]\nrust-version = "1.82"\n',
            encoding="utf-8",
        )
        want: dict[str, object] = {"rust-version": "1.82"}
        assert wi.workspace_package(root) == want


def test_a_workspace_without_the_table_reads_empty() -> None:
    """The skip-clean branch's input."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\n',
            encoding="utf-8",
        )
        none: dict[str, object] = {}
        assert wi.workspace_package(root) == none


def test_an_unreadable_manifest_stops_the_task(tmp_path: Path) -> None:
    """A manifest missing where cargo named the root is broken."""
    with pytest.raises(SystemExit) as stop:
        wi.workspace_package(tmp_path)
    assert stop.value.code == 1


# Each wrong-shaped account, and where its refusal says it is wrong.
WRONG_SHAPES: tuple[tuple[object, str], ...] = (
    ([], "the document is not an object"),
    ({"workspace_root": 1}, "workspace_root is not a string"),
    (
        {"workspace_root": "/ws", "workspace_members": [1]},
        "workspace_members is not a list of strings",
    ),
    ({"workspace_root": "/ws", "packages": {}}, "packages is not a list"),
    (
        {"workspace_root": "/ws", "packages": [1]},
        "packages[0] is not an object",
    ),
    (
        {
            "workspace_root": "/ws",
            "workspace_members": ["a"],
            "packages": [{"id": "a", "name": 1}],
        },
        "packages[0].name is not a string",
    ),
)


# The guards, driven end to end over a real metadata document.
def account(root: Path, manifest: str, packages: list[dict[str, object]]) -> str:
    """Lay out a workspace root at `root`, and say cargo's account of it.

    Returns:
        The metadata document, as cargo prints it.

    """
    (root / "Cargo.toml").write_text(manifest, encoding="utf-8")
    for package in packages:
        crate = root / "crates" / decoded.as_str(package["name"], "name")
        package["manifest_path"] = str(crate / "Cargo.toml")
    account: dict[str, object] = {
        "workspace_root": str(root),
        "workspace_members": [p["id"] for p in packages],
        "packages": packages,
    }
    return json.dumps(account)


def test_no_workspace_package_table_skips_clean(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Nothing is declared here, so nothing can be inert."""
    document = account(
        tmp_path, '[workspace]\nmembers = ["crates/*"]\n', [raw_member("a")]
    )
    status = wi.check(document, "Cargo.toml")
    out = capfd.readouterr().out
    assert status == 0
    assert f"{TASK}: Cargo.toml: no [workspace.package] table, skipped\n" in out


def test_an_inherited_key_passes_and_says_what_it_measured(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The green path counts the keys and the members."""
    document = account(
        tmp_path,
        '[workspace.package]\nrust-version = "1.82"\n',
        [raw_member("a", rust_version="1.82")],
    )
    status = wi.check(document, "Cargo.toml")
    out = capfd.readouterr().out
    assert status == 0
    assert f"{TASK}: Cargo.toml: 1 [workspace.package] key, every" in out


def test_an_inert_key_fails_with_the_one_line_remedy(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The red path names the key, the members and the fix."""
    document = account(
        tmp_path,
        '[workspace.package]\nrust-version = "1.97"\n',
        [raw_member("a", rust_version=None), raw_member("b", rust_version=None)],
    )
    status = wi.check(document, "Cargo.toml")
    err = capfd.readouterr().err
    assert status == 1
    assert "reach no member" in err
    assert "rust-version.workspace = true" in err
    assert "a, b" in err


def test_a_declared_table_with_no_member_fails(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A declaration with nobody to read it, in the limit.

    Skipping here would be the vacuous success this whole class is
    about: the keys are declared and reach nothing.
    """
    document = account(tmp_path, '[workspace.package]\nversion = "0.1.0"\n', [])
    status = wi.check(document, "Cargo.toml")
    err = capfd.readouterr().err
    assert status == 1
    assert "names no workspace member" in err


@pytest.mark.parametrize(("document", "where"), WRONG_SHAPES)
def test_metadata_of_the_wrong_shape_stops_the_task_by_where(
    document: object, where: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A wrong-shaped account is refused naming where, never a crash."""
    said = f"{TASK}: Cargo.toml: cargo metadata is unreadable"
    assert wi.check(json.dumps(document), "Cargo.toml") == 1
    err = capfd.readouterr().err
    assert f"{said} ({where})\n" == err


def test_a_manifest_of_the_wrong_shape_stops_the_task(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """`[workspace]` that is not a table is named, never a crash."""
    (tmp_path / "Cargo.toml").write_text("workspace = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        wi.workspace_package(tmp_path)
    err = capfd.readouterr().err
    assert stop.value.code == 1
    assert (
        f"{TASK}: {tmp_path}/Cargo.toml is unreadable (workspace is not an object)\n"
        == err
    )


def test_unreadable_metadata_stops_the_task() -> None:
    """A broken account is refused, never read as an empty one."""
    assert wi.check("not json", "Cargo.toml") == 1


# cargo for the two-root rows (#2004): locate-project names the fuzz
# crate its own root and every other manifest the top's, unless
# STUB_LOCATE_FAILS names the manifest; fetch passes
# unless STUB_FETCH_FAILS names the manifest; metadata prints the
# planted account of the root it is asked about.
CARGO = """\
#!/usr/bin/env bash
case "$1" in
  locate-project)
    if [[ $4 == "${STUB_LOCATE_FAILS:-}" ]]; then
      echo "error: planted: failed to locate $4" >&2
      exit 101
    fi
    case "$4" in
      fuzz/*) echo "${PWD}/fuzz/Cargo.toml" ;;
      *) echo "${PWD}/Cargo.toml" ;;
    esac
    ;;
  fetch)
    if [[ $4 == "${STUB_FETCH_FAILS:-}" ]]; then
      echo "error: planted: failed to fetch for $4" >&2
      exit 101
    fi
    ;;
  metadata) cat "${STUB_ACCOUNTS}/${7//\\//_}.json" ;;
  *) exit 1 ;;
esac
"""

# Two workspaces: the top one's key is taken; the fuzz crate's own
# workspace declares `edition`, and its one member takes none.
TWO_ROOTS = {
    "Cargo.toml": '[workspace]\nmembers = ["crates/a"]\n\n'
    '[workspace.package]\nrust-version = "1.82"\n',
    "crates/a/Cargo.toml": "[package]\nname = 'a'\n",
    "fuzz/Cargo.toml": "[package]\nname = 'a-fuzz'\n\n"
    '[workspace]\n\n[workspace.package]\nedition = "2024"\n',
}
INERT_IN_FUZZ = (
    f"{TASK}: fuzz/Cargo.toml: 1 of 1 [workspace.package] key reach no member\n"
    "  \n"
    "  edition = '2024' is declared and inherited by no member\n"
    "        could take it: a-fuzz\n"
    "        add `edition.workspace = true`\n"
)
ROOTS = ["Cargo.toml", "fuzz/Cargo.toml"]
TOP_GREEN = (
    f"{TASK}: Cargo.toml: 1 [workspace.package] key, every one inherited"
    " by a member of 1\n"
)


def account_of(root: Path, crate: str, **fields: object) -> dict[str, object]:
    """Build cargo's account of a one-member workspace at `root`.

    Returns:
        The metadata document.

    """
    package_id = f"path+file://{root}#{crate}@0.1.0"
    return {
        "workspace_root": str(root),
        "workspace_members": [package_id],
        "packages": [
            {
                "name": crate,
                "id": package_id,
                "manifest_path": str(root / "Cargo.toml"),
                **fields,
            },
        ],
    }


class TwoRoots:
    """A planted tree with two workspaces, cargo stubbed."""

    def __init__(
        self,
        scratch: Path,
        fetch_fails: str = "",
        locate_fails: str = "",
        *,
        env: support.Env,
    ) -> None:
        """Plant the tree, the stub and each root's account in `scratch`."""
        self.make_env = env
        base = scratch.resolve()
        self.tree = base / "repo"
        accounts = base / "accounts"
        accounts.mkdir()
        bindir = base / "bin"
        bindir.mkdir()
        self.cargo = bindir / "cargo"
        self.cargo.write_text(CARGO, encoding="utf-8")
        self.cargo.chmod(0o755)
        for name, text in TWO_ROOTS.items():
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_text(text, encoding="utf-8")
        planted = {
            "Cargo.toml": account_of(self.tree, "a", rust_version="1.82"),
            "fuzz_Cargo.toml": account_of(self.tree / "fuzz", "a-fuzz", edition="2021"),
        }
        for name, document in planted.items():
            (accounts / f"{name}.json").write_text(json.dumps(document), "utf-8")
        self.env = self.make_env(
            base / "home",
            MISE_TASK_NAME=TASK,
            ORG_FINDINGS="",
            STUB_ACCOUNTS=str(accounts),
            STUB_FETCH_FAILS=fetch_fails,
            STUB_LOCATE_FAILS=locate_fails,
        )
        self.env["PATH"] = f"{bindir}:{self.env.get('PATH', '')}"

    def enter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in the tree as the task does, the stub cargo answering."""
        monkeypatch.setattr(wi, "CARGO", str(self.cargo))
        for name, value in self.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.chdir(self.tree)

    def task(self) -> subprocess.CompletedProcess[str]:
        """Run the task's own body in the tree, as mise does.

        Returns:
            The finished run, both streams captured.

        """
        git = support.tool("git")
        subprocess.run([git, "init", "-q"], cwd=self.tree, env=self.env, check=True)
        subprocess.run(
            [git, "add", "--", *TWO_ROOTS], cwd=self.tree, env=self.env, check=True
        )
        config = decoded.load_toml((BELT / "config.toml").read_bytes())
        tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
        entry = decoded.as_object(tasks[TASK], TASK)
        as_mise = support.task_run(TASK, decoded.as_str(entry["run"], "run"))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env
            | as_mise.env
            | {"ORG_BELT_DIR": str(BELT), "ORG_PYTHON": sys.executable},
            capture_output=True,
            text=True,
            check=False,
        )


# Each workspace in the tree is judged, past a red one (#2004).
def test_a_key_no_member_of_the_second_root_takes_is_red(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fuzz/Cargo.toml's `edition`: nonzero, naming the key and the root."""
    planted = TwoRoots(tmp_path, env=env)
    planted.enter(monkeypatch)
    status = wi.main(ROOTS)
    out, err = capfd.readouterr()
    assert (status, out) == (1, TOP_GREEN)
    assert err.startswith(INERT_IN_FUZZ), err
    declared = f"  Declared in {planted.tree}/fuzz/Cargo.toml under"
    assert declared in err


def test_a_failing_cargo_is_red_with_its_text_and_the_rest_judged(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    env: support.Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch refused for the top root: cargo's words; fuzz still judged."""
    TwoRoots(tmp_path, fetch_fails="Cargo.toml", env=env).enter(monkeypatch)
    status = wi.main(ROOTS)
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"{TASK}: Cargo.toml: cargo fetch failed\n"
        "  error: planted: failed to fetch for Cargo.toml\n"
    ) in err
    assert INERT_IN_FUZZ in err


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            (
                "belt workspace-inherit: error: the following arguments are required: "
                "CARGO_TOML"
            ),
            id="nothing",
        ),
    ],
)
def test_no_root_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A usage error: argparse's exit 2 and its error.

    The task always passes one; none at all is refused, exit 2.
    """
    with pytest.raises(SystemExit) as ended:
        wi.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt workspace-inherit "), err
    assert err.splitlines()[-1] == error


def test_the_task_reads_the_roots_and_judges_both(
    tmp_path: Path, env: support.Env
) -> None:
    """The body, end to end: the derivation's roots, the fuzz one red."""
    done = TwoRoots(tmp_path, env=env).task()
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout == TOP_GREEN
    assert INERT_IN_FUZZ in done.stderr


def test_the_task_stops_when_the_derivation_fails(
    tmp_path: Path, env: support.Env
) -> None:
    """locate-project refused: its finding, exit 1, the helper never run."""
    done = TwoRoots(tmp_path, locate_fails="fuzz/Cargo.toml", env=env).task()
    assert (done.returncode, done.stdout) == (1, "")
    assert (
        f"{TASK}: cargo locate-project failed for fuzz/Cargo.toml\n"
        "  error: planted: failed to locate fuzz/Cargo.toml\n"
    ) in done.stderr
    assert "usage:" not in done.stderr
    assert "[workspace.package]" not in done.stderr

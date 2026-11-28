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

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self

import pytest

from belt import decoded
from belt import workspace_inherit as wi
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

BELT = support.MISE

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:workspace-inherit"


@pytest.fixture
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


class Stops:
    """Expect the task to stop: exit 1, its finding said on stderr (#1934).

    The refusal is a finding, written through the writer; `said` is what
    it printed, without the final newline.
    """

    def __init__(self) -> None:
        """Start with nothing said."""
        self.said = ""
        self._err = io.StringIO()
        self._redirect = redirect_stderr(self._err)

    def __enter__(self) -> Self:
        """Start capturing stderr.

        Returns:
            This expectation, to read `said` from afterwards.

        """
        self._redirect.__enter__()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Stop capturing, and hold the block to a stop with status 1.

        Returns:
            True: the expected stop is swallowed.

        """
        self._redirect.__exit__(kind, exc, tb)
        self.said = self._err.getvalue().rstrip("\n")
        if not isinstance(exc, SystemExit):
            pytest.fail(f"the task did not stop: {exc!r}")
        assert exc.code == 1, self.said
        return True


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


class TestInherits:
    """The one question this check asks, per key and per member."""

    def test_a_verbatim_value_is_taken(self) -> None:
        """The ordinary shape: cargo reports the declaration unchanged."""
        taker = member("taker", rust_version="1.82")
        assert wi.inherits("rust-version", "1.82", taker, ROOT) == True

    def test_a_member_with_its_own_value_takes_nothing(self) -> None:
        """A different value is a different declaration, not inheritance."""
        own = member("own", edition="2018")
        assert wi.inherits("edition", "2021", own, ROOT) == False

    def test_an_absent_field_takes_nothing(self) -> None:
        """release-lab's live case: rust_version None on all four."""
        bare = member("bare", rust_version=None)
        assert wi.inherits("rust-version", "1.97", bare, ROOT) == False

    def test_a_list_valued_key_reports_empty_not_null(self) -> None:
        """Measured: a member taking no `keywords` reports `[]`.

        So an inertness test written as "every member reports null"
        would call this key taken and never fire.
        """
        rows = [member("bare", keywords=[]), member("taker", keywords=["k"])]
        got: list[bool | None] = [
            wi.inherits("keywords", ["k"], row, ROOT) for row in rows
        ]
        want: list[bool | None] = [False, True]
        assert got == want

    def test_a_path_key_is_rebased_by_cargo_and_still_counts(self) -> None:
        """THE row that value equality gets wrong.

        release-lab declares `readme = "README.md"` and all four members
        take it; cargo reports `../../README.md` for each. Comparing the
        two as strings reds a key nobody got wrong.
        """
        taker = member("taker", readme="../../README.md")
        assert wi.inherits("readme", "README.md", taker, ROOT) == True

    def test_a_rebased_path_naming_another_file_does_not_count(self) -> None:
        """The other direction: rebasing is not a free pass."""
        other = member("other", readme="../../OTHER.md")
        assert wi.inherits("readme", "README.md", other, ROOT) == False

    def test_a_bool_readme_resolves_to_whatever_cargo_found(self) -> None:
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

    def test_a_false_readme_is_undecidable(self) -> None:
        """A readme declared false and "no readme" are one account.

        A member taking it reports None, and so does a member taking
        nothing — so this returns None rather than a verdict that would
        be right by luck.
        """
        bare = member("bare", readme=None)
        assert wi.inherits("readme", NO_README, bare, ROOT) is None


class TestJudge:
    """The rule: red only where NO member takes the key."""

    def test_a_key_every_member_takes_is_not_reported(self) -> None:
        """Every member inheriting, which is edtf's live shape."""
        members = [member("a", rust_version="1.82"), member("b", rust_version="1.82")]
        assert wi.judge({"rust-version": "1.82"}, members, ROOT, []) == NO_FINDINGS

    def test_a_key_only_one_member_takes_is_not_reported(self) -> None:
        """THE branch a stricter rule would get wrong.

        A workspace legitimately declares a key only some members want.
        One taker is a reader, and a key with a reader is not inert.
        """
        members = [member("a", rust_version="1.82"), member("b", rust_version=None)]
        assert wi.judge({"rust-version": "1.82"}, members, ROOT, []) == NO_FINDINGS

    def test_a_key_no_member_takes_is_reported_with_every_member(self) -> None:
        """release-lab's live case: the remedy names who could take it."""
        members = [member("a", rust_version=None), member("b", rust_version=None)]
        findings = wi.judge({"rust-version": "1.97"}, members, ROOT, [])
        assert len(findings) == 1
        assert findings[0].key == "rust-version"
        takers: list[str] = ["a", "b"]
        assert findings[0].takers == takers

    def test_publish_is_reported_unjudged_rather_than_guessed(self) -> None:
        """Measured: an inherited and an own publish are one account.

        A declared false comes back `[]` either way, so a verdict here
        would be right by luck in both directions.
        """
        report: list[str] = []
        members = [member("a", publish=[])]
        assert wi.judge({"publish": False}, members, ROOT, report) == NO_FINDINGS
        assert any("publish" in line for line in report)

    def test_a_key_cargo_does_not_report_is_named_not_skipped(self) -> None:
        """`exclude` and `include` have no per-package value at all."""
        report: list[str] = []
        members = [member("a")]
        assert wi.judge({"exclude": ["x"]}, members, ROOT, report) == NO_FINDINGS
        assert any("exclude" in line for line in report)

    def test_an_unknown_key_is_named_not_skipped(self) -> None:
        """A key this task cannot model is stated, never passed quietly."""
        report: list[str] = []
        assert wi.judge({"invented": 1}, [member("a")], ROOT, report) == NO_FINDINGS
        assert any("invented" in line for line in report)

    def test_an_undecidable_key_is_not_red(self) -> None:
        """No taker plus no verdict is a note, not a finding."""
        report: list[str] = []
        members = [member("a", readme=None)]
        assert wi.judge({"readme": False}, members, ROOT, report) == NO_FINDINGS
        assert report


class TestWorkspacePackage:
    """Reading the declaration, which is the one thing read as TEXT.

    Only the declared KEYS come from the manifest; whether anyone takes
    them is always cargo's account. Reading the members' text is what
    made this class invisible, and is what the issue rejected.
    """

    def test_reads_the_declared_table(self) -> None:
        """The table arrives as written."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[workspace.package]\nrust-version = "1.82"\n',
                encoding="utf-8",
            )
            want: dict[str, object] = {"rust-version": "1.82"}
            assert wi.workspace_package(root) == want

    def test_a_workspace_without_the_table_reads_empty(self) -> None:
        """The skip-clean branch's input."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/*"]\n',
                encoding="utf-8",
            )
            none: dict[str, object] = {}
            assert wi.workspace_package(root) == none

    def test_an_unreadable_manifest_stops_the_task(self) -> None:
        """A manifest missing where cargo named the root is broken."""
        with TemporaryDirectory() as tmp, Stops():
            wi.workspace_package(Path(tmp))


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


class TestMain:
    """The guards, driven end to end over a real metadata document."""

    @staticmethod
    def _run(manifest: str, packages: list[dict[str, object]]) -> tuple[int, str, str]:
        """Lay out a workspace root, feed cargo's account, capture all.

        Returns:
            The exit status, stdout and stderr.

        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(manifest, encoding="utf-8")
            for package in packages:
                crate = root / "crates" / decoded.as_str(package["name"], "name")
                package["manifest_path"] = str(crate / "Cargo.toml")
            document: dict[str, object] = {
                "workspace_root": str(root),
                "workspace_members": [p["id"] for p in packages],
                "packages": packages,
            }
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = wi.check(json.dumps(document), "Cargo.toml")
            return status, out.getvalue(), err.getvalue()

    def test_no_workspace_package_table_skips_clean(self) -> None:
        """Nothing is declared here, so nothing can be inert."""
        status, out, _ = self._run(
            '[workspace]\nmembers = ["crates/*"]\n',
            [raw_member("a")],
        )
        assert status == 0
        assert f"{TASK}: Cargo.toml: no [workspace.package] table, skipped\n" in out

    def test_an_inherited_key_passes_and_says_what_it_measured(self) -> None:
        """The green path counts the keys and the members."""
        status, out, _ = self._run(
            '[workspace.package]\nrust-version = "1.82"\n',
            [raw_member("a", rust_version="1.82")],
        )
        assert status == 0
        assert f"{TASK}: Cargo.toml: 1 [workspace.package] key, every" in out

    def test_an_inert_key_fails_with_the_one_line_remedy(self) -> None:
        """The red path names the key, the members and the fix."""
        status, _, err = self._run(
            '[workspace.package]\nrust-version = "1.97"\n',
            [raw_member("a", rust_version=None), raw_member("b", rust_version=None)],
        )
        assert status == 1
        assert "reach no member" in err
        assert "rust-version.workspace = true" in err
        assert "a, b" in err

    def test_a_declared_table_with_no_member_fails(self) -> None:
        """A declaration with nobody to read it, in the limit.

        Skipping here would be the vacuous success this whole class is
        about: the keys are declared and reach nothing.
        """
        status, _, err = self._run('[workspace.package]\nversion = "0.1.0"\n', [])
        assert status == 1
        assert "names no workspace member" in err

    @pytest.mark.parametrize(("document", "where"), WRONG_SHAPES)
    def test_metadata_of_the_wrong_shape_stops_the_task_by_where(
        self, document: object, where: str
    ) -> None:
        """A wrong-shaped account is refused naming where, never a crash."""
        said = f"{TASK}: Cargo.toml: cargo metadata is unreadable"
        err = io.StringIO()
        with redirect_stderr(err):
            assert wi.check(json.dumps(document), "Cargo.toml") == 1
        assert f"{said} ({where})\n" == err.getvalue()

    def test_a_manifest_of_the_wrong_shape_stops_the_task(self) -> None:
        """`[workspace]` that is not a table is named, never a crash."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text("workspace = 1\n", encoding="utf-8")
            with Stops() as stopped:
                wi.workspace_package(root)
        assert (
            f"{TASK}: {root}/Cargo.toml is unreadable (workspace is not an object)"
            == stopped.said
        )

    def test_unreadable_metadata_stops_the_task(self) -> None:
        """A broken account is refused, never read as an empty one."""
        with redirect_stderr(io.StringIO()):
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
        self, scratch: Path, fetch_fails: str = "", locate_fails: str = ""
    ) -> None:
        """Plant the tree, the stub and each root's account in `scratch`."""
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
        self.env = support.env(
            base / "home",
            MISE_TASK_NAME=TASK,
            ORG_FINDINGS="",
            STUB_ACCOUNTS=str(accounts),
            STUB_FETCH_FAILS=fetch_fails,
            STUB_LOCATE_FAILS=locate_fails,
        )
        self.env["PATH"] = f"{bindir}:{self.env.get('PATH', '')}"

    def main(self, *, capfd: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
        """Run the helper over both roots, in this process.

        Returns:
            The status and both streams.

        """
        roots = ["Cargo.toml", "fuzz/Cargo.toml"]
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(wi, "CARGO", str(self.cargo))
            with pytest.MonkeyPatch.context() as patched:
                for name, value in self.env.items():
                    patched.setenv(name, value)
                patched.chdir(self.tree)
                status, out, err = wi.main(roots)
            out, err = capfd.readouterr()
            return status, out, err

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


class TestEveryRoot:
    """Each workspace in the tree is judged, past a red one (#2004)."""

    def test_a_key_no_member_of_the_second_root_takes_is_red(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """fuzz/Cargo.toml's `edition`: nonzero, naming the key and the root."""
        planted = TwoRoots(tmp_path)
        status, out, err = planted.main(capfd=capfd)
        assert (status, out) == (1, TOP_GREEN)
        assert err.startswith(INERT_IN_FUZZ), err
        declared = f"  Declared in {planted.tree}/fuzz/Cargo.toml under"
        assert declared in err

    def test_a_failing_cargo_is_red_with_its_text_and_the_rest_judged(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Fetch refused for the top root: cargo's words; fuzz still judged."""
        done = TwoRoots(tmp_path, fetch_fails="Cargo.toml").main(capfd=capfd)
        assert (done.returncode, done.stdout) == (1, "")
        assert (
            f"{TASK}: Cargo.toml: cargo fetch failed\n"
            "  error: planted: failed to fetch for Cargo.toml\n"
        ) in done.stderr
        assert INERT_IN_FUZZ in done.stderr

    def test_no_root_is_a_usage_error(self, capfd: pytest.CaptureFixture[str]) -> None:
        """The task always passes one; none at all is refused, exit 2."""
        none: list[str] = []
        status, out, err = wi.main(none)
        out, err = capfd.readouterr()
        assert (status, out, err) == (
            2,
            "",
            "usage: belt workspace-inherit <root Cargo.toml>...\n",
        )

    def test_the_task_reads_the_roots_and_judges_both(self, tmp_path: Path) -> None:
        """The body, end to end: the derivation's roots, the fuzz one red."""
        done = TwoRoots(tmp_path).task()
        assert done.returncode == 1, done.stdout + done.stderr
        assert done.stdout == TOP_GREEN
        assert INERT_IN_FUZZ in done.stderr

    def test_the_task_stops_when_the_derivation_fails(self, tmp_path: Path) -> None:
        """locate-project refused: its finding, exit 1, the helper never run."""
        done = TwoRoots(tmp_path, locate_fails="fuzz/Cargo.toml").task()
        assert (done.returncode, done.stdout) == (1, "")
        assert (
            f"{TASK}: cargo locate-project failed for fuzz/Cargo.toml\n"
            "  error: planted: failed to locate fuzz/Cargo.toml\n"
        ) in done.stderr
        assert "usage:" not in done.stderr
        assert "[workspace.package]" not in done.stderr

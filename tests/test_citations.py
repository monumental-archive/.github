"""Rows for audit:citations and mise/citations.py, one per branch (#1911).

The body was 79 lines of shell; its logic is the helper now, and each
message it printed is asserted here, word for word. The live org is a
stand-in (`Org`): the rows read no network. Each row plants a canon-
shaped repository whose docs/ git tracks, because the helper reads
docs/ from git (#2013): an untracked docs/ is a missing one.
"""

import itertools
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from belt import citations, decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

TASK = "audit:citations"
OWNER = "monumental-archive"
BELT = support.MISE
GIT = support.tool("git")
TAIL = (
    "  a citation is a claim about the record — correct the document\n"
    "  or the record, never leave them disagreeing (#349 finding 2)\n"
)


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


@dataclass
class Org:
    """The live org as a row states it."""

    repos: list[str] | None = field(default_factory=lambda: [".github", "stele"])
    tags: dict[str, set[str]] = field(default_factory=dict)
    runs: set[str] = field(default_factory=set)
    asked: list[str] = field(default_factory=list)

    def org_repos(self, owner: str) -> list[str] | None:
        """Answer the listing.

        Returns:
            The row's repositories.

        """
        self.asked.append(f"repos {owner}")
        return self.repos

    def repo_tags(self, owner: str, repo: str) -> set[str]:
        """Answer one repository's tags.

        Returns:
            The row's tags for it.

        """
        self.asked.append(f"tags {owner}/{repo}")
        return self.tags.get(repo, set())

    def run_resolves(self, owner: str, repo: str, run_id: str) -> bool:
        """Answer one run.

        Returns:
            Whether the row names it.

        """
        self.asked.append(f"run {owner}/{repo}/{run_id}")
        return f"{repo}/{run_id}" in self.runs


def plant(tree: Path, files: dict[str, str], *, env: support.Env) -> None:
    """Make `tree` a repository tracking `files`."""
    environ = env(tree.parent / "home")
    for name, text in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=tree, env=environ, check=True)
    if files:
        subprocess.run([GIT, "add", "--", *files], cwd=tree, env=environ, check=True)


class Repo(Protocol):
    """What the `repo` fixture hands a row."""

    def __call__(self, files: dict[str, str], *, owner: str = ...) -> Path:
        """Run repo."""
        ...


@fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: support.Env) -> Repo:
    """Plant a repository tracking a row's files, under its owner.

    Returns:
        A function that plants `files` and returns the repository's top.

    """
    made = itertools.count()

    def lay_out(files: dict[str, str], *, owner: str = OWNER) -> Path:
        tree = tmp_path / f"run{next(made)}" / "repo"
        tree.mkdir(parents=True)
        plant(tree, files, env=env)
        monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", owner)
        return tree

    return lay_out


DOC = {"docs/a.md": "# A\n\nNothing cited.\n"}


# docs/ is read from git, and a missing one is red by name.
def test_an_untracked_docs_is_missing(
    env: support.Env, capfd: pytest.CaptureFixture[str]
) -> None:
    """docs/ on disk, nothing tracked under it: the missing-input red."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "repo"
        tree.mkdir()
        plant(tree, {"README.md": "x\n"}, env=env)
        (tree / "docs").mkdir()
        (tree / "docs" / "a.md").write_text("x\n", encoding="utf-8")
        status = citations.main([], net=Org(), top=tree)
        err = capfd.readouterr().err
    assert status == 1
    assert (
        f"{TASK}: on the canon, and the input this leg walks is missing: docs/\n"
        "  a canon-only audit reds on a missing input, never skips (#738).\n"
    ) == err


def test_docs_without_markdown_is_red(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """A tracked docs/ holding no markdown resolves nothing: red."""
    tree = repo({"docs/a.txt": "x\n"})
    status = citations.main([], net=Org(), top=tree)
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"{TASK}: on the canon, and docs/ holds no tracked markdown —\n"
        "  there is nothing to resolve, so there is nothing to report"
        " clean (#738).\n"
    ) == err


def test_no_gh_is_red(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    repo: Repo,
) -> None:
    """With no network stand-in and no gh on PATH: `gh missing`."""
    monkeypatch.setattr("shutil.which", support.answers_none)
    tree = repo(DOC)
    status = citations.main([], net=None, top=tree)
    err = capfd.readouterr().err
    assert (1, f"{TASK}: gh missing\n") == (status, err)


def test_no_owner_is_red(capfd: pytest.CaptureFixture[str], repo: Repo) -> None:
    """The org is the environment's to name (#579)."""
    tree = repo(DOC, owner="")
    status = citations.main([], net=Org(), top=tree)
    err = capfd.readouterr().err
    assert (1, f"{TASK}: {citations.OWNER_UNSET}\n") == (status, err)


# The org listing is the population; a broken one resolves nothing.
def test_a_failed_listing_is_red(capfd: pytest.CaptureFixture[str], repo: Repo) -> None:
    """Gh could not list: nothing was resolved."""
    tree = repo(DOC)
    status = citations.main([], net=Org(repos=None), top=tree)
    err = capfd.readouterr().err
    assert (
        1,
        f"{TASK}: could not enumerate org repos — nothing was resolved\n",
    ) == (status, err)


def test_an_empty_listing_is_red(capfd: pytest.CaptureFixture[str], repo: Repo) -> None:
    """Zero repositories is a broken walk, not clean docs."""
    tree = repo(DOC)
    status = citations.main([], net=Org(repos=[]), top=tree)
    err = capfd.readouterr().err
    assert (
        1,
        (
            f"{TASK}: the org listing named zero repositories — the walk"
            " is\n  broken, not the docs, and nothing was resolved\n"
        ),
    ) == (status, err)


# Each citation form resolves against its own repository's tags.
CITING = (
    "Pinned at `.github@v1.24.1`, and `monumental-archive/stele@v0.2.2`;\n"
    "stele v0.16.0 too, and\n"
    "https://github.com/monumental-archive/stele/releases/tag/v0.3.0.\n"
    "Bare v9.9.9 is prose and is not walked.\n"
    "Run https://github.com/monumental-archive/stele/actions/runs/42 passed.\n"
)


def test_every_citation_resolving_passes_with_counts(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """Four release citations and one run, all live: the pass line."""
    org = Org(
        tags={".github": {"v1.24.1"}, "stele": {"v0.2.2", "v0.16.0", "v0.3.0"}},
        runs={"stele/42"},
    )
    tree = repo({"docs/b.md": CITING, **DOC})
    status = citations.main([], net=org, top=tree)
    out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    assert (
        f"{TASK}: 2 doc(s) walked, 4 org release citation(s) and 1 run URL(s)"
        " all resolve\n"
    ) == out
    asked: list[str] = sorted(a for a in org.asked if a.startswith("tags "))
    want: list[str] = [f"tags {OWNER}/.github", f"tags {OWNER}/stele"]
    assert want == asked


def test_a_missing_tag_and_a_dead_run_are_named(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """Each citation that resolves to nothing is a line of its own."""
    org = Org(tags={".github": {"v1.24.1"}, "stele": {"v0.2.2", "v0.3.0"}})
    tree = repo({"docs/a.md": CITING})
    status = citations.main([], net=org, top=tree)
    out, err = capfd.readouterr()
    assert (status, out) == (1, "")
    assert (
        f"{TASK}: citations that resolve to nothing:\n"
        "  cited in docs/ as an org release, but no such tag in stele: v0.16.0\n"
        "  run URL cited in docs/ does not resolve:"
        " https://github.com/monumental-archive/stele/actions/runs/42\n" + TAIL
    ) == err


def test_a_version_resolves_against_its_own_repository(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """`.github@v0.2.2` is not satisfied by stele's v0.2.2."""
    org = Org(tags={".github": set(), "stele": {"v0.2.2"}})
    tree = repo({"docs/a.md": "At `.github@v0.2.2`.\n"})
    status = citations.main([], net=org, top=tree)
    err = capfd.readouterr().err
    assert status == 1
    assert "no such tag in .github: v0.2.2" in err


def test_the_longer_name_wins_a_shared_prefix(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """`stele-lab@v1.0.0` is stele-lab's, never stele's."""
    org = Org(repos=["stele", "stele-lab"], tags={"stele-lab": {"v1.0.0"}})
    tree = repo({"docs/a.md": "At stele-lab@v1.0.0.\n"})
    status = citations.main([], net=org, top=tree)
    out = capfd.readouterr().out
    assert status == 0
    assert "1 org release citation(s)" in out


def test_a_dot_in_a_name_is_literal(
    capfd: pytest.CaptureFixture[str], repo: Repo
) -> None:
    """`.github` does not match `xgithub`."""
    tree = repo({"docs/a.md": "At xgithub@v1.0.0.\n"})
    status = citations.main([], net=Org(), top=tree)
    out = capfd.readouterr().out
    assert status == 0
    assert "0 org release citation(s)" in out


def run_body(
    files: dict[str, str], *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the task's body in a tree tracking `files`.

    Returns:
        The finished run.

    """
    config = decoded.load_toml((BELT / "config.toml").read_bytes())
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    body = decoded.as_str(decoded.as_object(tasks[TASK], TASK)["run"], TASK)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "repo"
        tree.mkdir()
        plant(tree, files, env=env)
        as_mise = support.task_run(TASK, body)
        environ = env(Path(tmp) / "home", ORG_BELT_DIR=str(BELT))
        return subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=environ | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


# The task's body: the canon guard, then the helper.
def test_not_the_canon_skips(env: support.Env) -> None:
    """No tracked security/identity.toml: skipped, exit 0."""
    done = run_body({"README.md": "x\n"}, env=env)
    assert (
        0,
        f"{TASK}: no tracked security/identity.toml, not the canon, skipped\n",
    ) == (done.returncode, done.stdout)


def test_the_canon_reaches_the_helper(env: support.Env) -> None:
    """The canon with no docs/: the helper's missing-input red."""
    done = run_body({"security/identity.toml": "x\n"}, env=env)
    assert done.returncode == 1
    assert (
        f"{TASK}: on the canon, and the input this leg walks is missing: docs/"
        in done.stderr
    )

#!/usr/bin/env python3
"""Rows for audit:citations and mise/citations.py, one per branch (#1911).

The body was 79 lines of shell; its logic is the helper now, and each
message it printed is asserted here, word for word. The live org is a
stand-in (`Org`): the rows read no network. Each row plants a canon-
shaped repository whose docs/ git tracks, because the helper reads
docs/ from git (#2013): an untracked docs/ is a missing one.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import citations
import decoded
import hermetic

TASK = "audit:citations"
OWNER = "monumental-archive"
BELT = Path(__file__).resolve().parent
GIT = hermetic.tool("git")
TAIL = (
    "  a citation is a claim about the record — correct the document\n"
    "  or the record, never leave them disagreeing (#349 finding 2)\n"
)


def setUpModule() -> None:
    """Run every row as the task does, recording no finding."""
    unittest.enterModuleContext(hermetic.as_task(TASK))


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


def plant(tree: Path, files: dict[str, str]) -> None:
    """Make `tree` a repository tracking `files`."""
    env = hermetic.env(tree.parent / "home")
    for name, text in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
    if files:
        subprocess.run([GIT, "add", "--", *files], cwd=tree, env=env, check=True)


def audit(
    files: dict[str, str], org: Org | None, *, owner: str = OWNER
) -> tuple[int, str, str]:
    """Plant `files` and run the helper's main against `org`.

    Returns:
        The exit status, stdout and stderr.

    """
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "repo"
        tree.mkdir()
        plant(tree, files)
        out, err = io.StringIO(), io.StringIO()
        named: dict[str, str] = {"GITHUB_REPOSITORY_OWNER": owner}
        with (
            mock.patch.dict(os.environ, named),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            status = citations.main(org, tree)
        return status, out.getvalue(), err.getvalue()


DOC = {"docs/a.md": "# A\n\nNothing cited.\n"}


class Inputs(unittest.TestCase):
    """docs/ is read from git, and a missing one is red by name."""

    def test_an_untracked_docs_is_missing(self) -> None:
        """docs/ on disk, nothing tracked under it: the missing-input red."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "repo"
            tree.mkdir()
            plant(tree, {"README.md": "x\n"})
            (tree / "docs").mkdir()
            (tree / "docs" / "a.md").write_text("x\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                status = citations.main(Org(), tree)
        self.assertEqual(1, status)
        self.assertEqual(
            f"{TASK}: on the canon, and the input this leg walks is missing: docs/\n"
            "  a canon-only audit reds on a missing input, never skips (#738).\n",
            err.getvalue(),
        )

    def test_docs_without_markdown_is_red(self) -> None:
        """A tracked docs/ holding no markdown resolves nothing: red."""
        status, out, err = audit({"docs/a.txt": "x\n"}, Org())
        self.assertEqual((1, ""), (status, out))
        self.assertEqual(
            f"{TASK}: on the canon, and docs/ holds no tracked markdown —\n"
            "  there is nothing to resolve, so there is nothing to report"
            " clean (#738).\n",
            err,
        )

    def test_no_gh_is_red(self) -> None:
        """With no network stand-in and no gh on PATH: `gh missing`."""
        with mock.patch("shutil.which", return_value=None):
            status, _, err = audit(DOC, None)
        self.assertEqual((1, f"{TASK}: gh missing\n"), (status, err))

    def test_no_owner_is_red(self) -> None:
        """The org is the environment's to name (#579)."""
        status, _, err = audit(DOC, Org(), owner="")
        self.assertEqual((1, f"{TASK}: {citations.OWNER_UNSET}\n"), (status, err))


class Listing(unittest.TestCase):
    """The org listing is the population; a broken one resolves nothing."""

    def test_a_failed_listing_is_red(self) -> None:
        """Gh could not list: nothing was resolved."""
        status, _, err = audit(DOC, Org(repos=None))
        self.assertEqual(
            (1, f"{TASK}: could not enumerate org repos — nothing was resolved\n"),
            (status, err),
        )

    def test_an_empty_listing_is_red(self) -> None:
        """Zero repositories is a broken walk, not clean docs."""
        status, _, err = audit(DOC, Org(repos=[]))
        self.assertEqual(
            (
                1,
                (
                    f"{TASK}: the org listing named zero repositories — the walk"
                    " is\n  broken, not the docs, and nothing was resolved\n"
                ),
            ),
            (status, err),
        )


class Resolution(unittest.TestCase):
    """Each citation form resolves against its own repository's tags."""

    CITING = (
        "Pinned at `.github@v1.24.1`, and `monumental-archive/stele@v0.2.2`;\n"
        "stele v0.16.0 too, and\n"
        "https://github.com/monumental-archive/stele/releases/tag/v0.3.0.\n"
        "Bare v9.9.9 is prose and is not walked.\n"
        "Run https://github.com/monumental-archive/stele/actions/runs/42 passed.\n"
    )

    def test_every_citation_resolving_passes_with_counts(self) -> None:
        """Four release citations and one run, all live: the pass line."""
        org = Org(
            tags={".github": {"v1.24.1"}, "stele": {"v0.2.2", "v0.16.0", "v0.3.0"}},
            runs={"stele/42"},
        )
        status, out, err = audit({"docs/b.md": self.CITING, **DOC}, org)
        self.assertEqual((0, ""), (status, err))
        self.assertEqual(
            f"{TASK}: 2 doc(s) walked, 4 org release citation(s) and 1 run URL(s)"
            " all resolve\n",
            out,
        )
        asked: list[str] = sorted(a for a in org.asked if a.startswith("tags "))
        want: list[str] = [f"tags {OWNER}/.github", f"tags {OWNER}/stele"]
        self.assertEqual(want, asked)

    def test_a_missing_tag_and_a_dead_run_are_named(self) -> None:
        """Each citation that resolves to nothing is a line of its own."""
        org = Org(tags={".github": {"v1.24.1"}, "stele": {"v0.2.2", "v0.3.0"}})
        status, out, err = audit({"docs/a.md": self.CITING}, org)
        self.assertEqual((1, ""), (status, out))
        self.assertEqual(
            f"{TASK}: citations that resolve to nothing:\n"
            "  cited in docs/ as an org release, but no such tag in stele: v0.16.0\n"
            "  run URL cited in docs/ does not resolve:"
            " https://github.com/monumental-archive/stele/actions/runs/42\n" + TAIL,
            err,
        )

    def test_a_version_resolves_against_its_own_repository(self) -> None:
        """`.github@v0.2.2` is not satisfied by stele's v0.2.2."""
        org = Org(tags={".github": set(), "stele": {"v0.2.2"}})
        status, _, err = audit({"docs/a.md": "At `.github@v0.2.2`.\n"}, org)
        self.assertEqual(1, status)
        self.assertIn("no such tag in .github: v0.2.2", err)

    def test_the_longer_name_wins_a_shared_prefix(self) -> None:
        """`stele-lab@v1.0.0` is stele-lab's, never stele's."""
        org = Org(repos=["stele", "stele-lab"], tags={"stele-lab": {"v1.0.0"}})
        status, out, _ = audit({"docs/a.md": "At stele-lab@v1.0.0.\n"}, org)
        self.assertEqual(0, status)
        self.assertIn("1 org release citation(s)", out)

    def test_a_dot_in_a_name_is_literal(self) -> None:
        """`.github` does not match `xgithub`."""
        status, out, _ = audit({"docs/a.md": "At xgithub@v1.0.0.\n"}, Org())
        self.assertEqual(0, status)
        self.assertIn("0 org release citation(s)", out)


def run_body(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
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
        plant(tree, files)
        as_mise = hermetic.task_run(TASK, body)
        env = hermetic.env(Path(tmp) / "home", ORG_BELT_DIR=str(BELT))
        return subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )


class Body(unittest.TestCase):
    """The task's body: the canon guard, then the helper."""

    def test_not_the_canon_skips(self) -> None:
        """No tracked security/identity.toml: skipped, exit 0."""
        done = run_body({"README.md": "x\n"})
        self.assertEqual(
            (0, f"{TASK}: no tracked security/identity.toml, not the canon, skipped\n"),
            (done.returncode, done.stdout),
        )

    def test_the_canon_reaches_the_helper(self) -> None:
        """The canon with no docs/: the helper's missing-input red."""
        done = run_body({"security/identity.toml": "x\n"})
        self.assertEqual(1, done.returncode)
        self.assertIn(
            f"{TASK}: on the canon, and the input this leg walks is missing: docs/",
            done.stderr,
        )


if __name__ == "__main__":
    unittest.main()

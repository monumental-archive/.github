#!/usr/bin/env python3
"""A scheduled check's name says what a red means (#1858).

GitHub's failure notification carries the workflow's name and the run
page carries `<caller job> / <job name>`. When every repository-side
check was one workflow named `audit` with seventeen jobs named
`audit / ...`, a dead link, a stale lock and a failed fuzz target all
arrived as "Run failed: audit". The names are the interface, so they
are held here:

- no two stubs in workflow-templates/ share a `name:`, or two subjects
  arrive as one;
- every job that runs steps has a `name:` of at least three words — a
  sentence about the subject, not a label (a `${{ }}` expression is no
  word);
- no job name begins with a workflow's name and ` / `: the run page
  already prefixes the caller's job, so the subject would be said
  twice, and a schedule word is not a subject.

The set is every stub in workflow-templates/ and every reusable
workflow a stub calls, read from the stub's own `uses:` line. It has no
list of files to skip. A job that calls a workflow (`uses:`) is named
by its id on the run page, so it is held by the callee's jobs, not its
own `name:`.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
# A stub reaches a reusable workflow of this repository by pin, or (the
# canon's own self-* callers) by local path.
CALL = re.compile(r"^(?:[\w.-]+/[\w.-]+/|\./)\.github/workflows/([\w.-]+\.ya?ml)")
EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
# Fewer words than this is a label, not a sentence about the subject.
LEAST_WORDS = 3


def stubs(root: Path) -> list[Path]:
    """Return every workflow stub the tree ships.

    Returns:
        The stubs, sorted by path.

    """
    return sorted((root / "workflow-templates").glob("*.y*ml"))


def callees(root: Path, stub_docs: dict[Path, dict[str, object]]) -> list[Path]:
    """Return every reusable workflow of this tree a stub calls.

    Returns:
        The called workflows, sorted, each once.

    """
    found: set[Path] = set()
    for doc in stub_docs.values():
        for job in workflow_yaml.jobs(doc).values():
            uses = job.get("uses")
            match = CALL.match(uses) if isinstance(uses, str) else None
            if match:
                found.add(root / ".github" / "workflows" / match.group(1))
    return sorted(found)


def words(name: str) -> int:
    """Count the words of a job name, expressions left out.

    Returns:
        The number of words.

    """
    return sum(1 for _ in WORD.finditer(EXPRESSION.sub(" ", name)))


def findings(root: Path) -> list[str]:
    """Judge every stub and every workflow a stub calls by its names.

    Returns:
        One line per finding, empty when every name holds.

    """
    out: list[str] = []
    stub_docs = {path: workflow_yaml.document(path) for path in stubs(root)}
    named: dict[str, Path] = {}
    for path, doc in stub_docs.items():
        name = doc.get("name")
        if not isinstance(name, str) or not name:
            out.append(f"{path.relative_to(root)}: the stub has no name:")
            continue
        if name in named:
            first = named[name].relative_to(root)
            out.append(
                f"{first} and {path.relative_to(root)} share name: {name}"
                " — two subjects would arrive as one"
            )
        named.setdefault(name, path)
    docs = dict(stub_docs)
    for path in callees(root, stub_docs):
        docs[path] = workflow_yaml.document(path)
    prefixes = sorted({
        n for d in docs.values() if isinstance(n := d.get("name"), str) and n
    })
    for path, doc in docs.items():
        where = path.relative_to(root)
        for job_id, job in workflow_yaml.jobs(doc).items():
            if "uses" in job:
                continue
            name = job.get("name")
            if not isinstance(name, str) or not name.strip():
                out.append(f"{where}: job {job_id} has no name:")
                continue
            if words(name) < LEAST_WORDS:
                out.append(
                    f"{where}: job {job_id} is named {name!r}, fewer than three words"
                )
            out.extend(
                f"{where}: job {job_id} is named {name!r}, which begins with"
                f" the workflow name {prefix!r} and ' / '"
                for prefix in prefixes
                if name.startswith(f"{prefix} / ")
            )
    return out


class Names(unittest.TestCase):
    """The tree's own names, and planted defects each named."""

    def plant(self) -> Path:
        """Copy the stubs and the workflows into a scratch tree.

        Returns:
            The scratch tree's root.

        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(CANON / "workflow-templates", tmp / "workflow-templates")
        shutil.copytree(CANON / ".github" / "workflows", tmp / ".github" / "workflows")
        return tmp

    @staticmethod
    def edit(path: Path, old: str, new: str) -> None:
        """Replace exactly one occurrence of `old` in a planted file.

        Raises:
            AssertionError: `old` is absent or occurs more than once.

        """
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            msg = f"{path.name}: {old!r} occurs {text.count(old)} times"
            raise AssertionError(msg)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def test_the_tree_holds(self) -> None:
        """Every stub and every workflow a stub calls is named for its subject."""
        none: list[str] = []
        self.assertEqual(findings(CANON), none)

    def test_the_set_reaches_the_subject_workflows(self) -> None:
        """The set read from the stubs holds the five subject workflows."""
        docs = {p: workflow_yaml.document(p) for p in stubs(CANON)}
        called = {p.name for p in callees(CANON, docs)}
        for subject in ("dependencies", "deep-tests", "public-api", "records"):
            self.assertIn(f"repo-{subject}.yml", called)
        self.assertIn("repo-own-audits.yml", called)

    def test_a_shared_stub_name_is_red(self) -> None:
        """Two stubs named alike would arrive as one notification."""
        root = self.plant()
        self.edit(
            root / "workflow-templates" / "records.yml",
            "name: records\n",
            "name: dependencies\n",
        )
        expected = [
            (
                "workflow-templates/dependencies.yml and"
                " workflow-templates/records.yml share name: dependencies"
                " — two subjects would arrive as one"
            ),
        ]
        self.assertEqual(findings(root), expected)

    def test_an_audit_slash_links_job_is_red(self) -> None:
        """The old shape, `audit / links`, is a label and not a sentence."""
        root = self.plant()
        self.edit(
            root / ".github" / "workflows" / "repo-records.yml",
            "    name: tracked links resolve\n",
            "    name: audit / links\n",
        )
        expected = [
            (
                ".github/workflows/repo-records.yml: job links is named"
                " 'audit / links', fewer than three words"
            ),
        ]
        self.assertEqual(findings(root), expected)

    def test_a_job_that_repeats_its_workflow_is_red(self) -> None:
        """A job name opening with a workflow's name says the subject twice."""
        root = self.plant()
        self.edit(
            root / ".github" / "workflows" / "repo-records.yml",
            "    name: tracked links resolve\n",
            "    name: records / tracked links resolve\n",
        )
        expected = [
            (
                ".github/workflows/repo-records.yml: job links is named"
                " 'records / tracked links resolve', which begins with the"
                " workflow name 'records' and ' / '"
            ),
        ]
        self.assertEqual(findings(root), expected)

    def test_a_job_with_no_name_is_red(self) -> None:
        """A job with no name is shown by its id, which says nothing."""
        root = self.plant()
        self.edit(
            root / ".github" / "workflows" / "repo-records.yml",
            "    name: tracked links resolve\n",
            "",
        )
        expected = [".github/workflows/repo-records.yml: job links has no name:"]
        self.assertEqual(findings(root), expected)


if __name__ == "__main__":
    unittest.main()

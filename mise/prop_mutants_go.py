#!/usr/bin/env python3
"""mutants-go.py's readers and survivor classes, on inputs nobody wrote.

The properties of `test_mutants_go.py`'s fixed rows (#1555), over
generated `git grep` hits, coverage profiles and gomu results:

- Round trip (T): a `//mutants:equivalent` comment `git grep -n`
  reports reads back as its path, line, mutator and reason, and names
  its own line as the site when code precedes it, the next line when
  it stands alone.
- Round trip (T): a `set`-mode profile's executed blocks read back as
  line ranges under the file's path from the root; a block never
  executed, or in a package outside the shard, is dropped.
- Invariance (V): a path relative to the root and back is the path.
- Invariance (V): a surviving mutant on a covered line with a sound
  record of its mutator at its site is explained, without one it
  survives, and on an uncovered line it is not covered, whatever
  order the records come in.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "mutants_go",
    Path(__file__).with_name("mutants-go.py"),
)
mg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mg)

# A Go module's file paths: no `:` (module zips refuse one), no spaces.
SEGMENT = st.from_regex(r"[a-z][a-z0-9_.-]{0,6}", fullmatch=True)
PATH = st.lists(SEGMENT, min_size=1, max_size=3).map(lambda p: "/".join(p) + ".go")
KIND = st.from_regex(r"[A-Z][A-Za-z]{2,10}", fullmatch=True)
REASON = st.from_regex(r"[a-z][a-z ,.]{0,30}", fullmatch=True).map(str.strip)
CODE = st.sampled_from(["", "\t", "\tx := a + b ", "\treturn n "])


class Records(unittest.TestCase):
    """A grep hit reads back as the comment it found."""

    @given(
        st.lists(st.tuples(PATH, st.integers(1, 9999), CODE, KIND, REASON), max_size=5)
    )
    def test_hits_read_back(self, hits: list[tuple]) -> None:
        """Path, line, site, mutator and reason."""
        text = "".join(
            f"{p}:{n}:{code}{mg.MARK} {kind} {reason}\n"
            for p, n, code, kind, reason in hits
        )
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "records.txt").write_text(text, encoding="utf-8")
            got = mg.read_records(Path(scratch))
        self.assertEqual(
            [(r.path, r.line, r.site, r.kind, r.reason) for r in got],
            [
                (p, n, n if code.strip() else n + 1, kind, reason)
                for p, n, code, kind, reason in hits
            ],
        )


@st.composite
def profile(draw: st.DrawFn) -> tuple[str, dict[str, str], dict[str, list[range]]]:
    """Write a coverage profile over some packages.

    Returns:
        The profile, the import path to directory map, and the ranges
        it should read back as.

    """
    packages = draw(
        st.dictionaries(
            st.from_regex(r"example\.com/m(/[a-z]{1,4}){0,2}", fullmatch=True),
            st.one_of(
                st.just(""), st.from_regex(r"[a-z]{1,4}(/[a-z]{1,4})?", fullmatch=True)
            ),
            min_size=1,
            max_size=3,
        )
    )
    known = sorted(packages)
    lines = ["mode: set"]
    expected: dict[str, list[range]] = {}
    for _ in range(draw(st.integers(0, 8))):
        package = draw(st.sampled_from([*known, "example.com/other"]))
        base = draw(SEGMENT) + ".go"
        start = draw(st.integers(1, 500))
        end = start + draw(st.integers(0, 40))
        count = draw(st.integers(0, 3))
        lines.append(
            f"{package}/{base}:{start}.2,{end}.9 {draw(st.integers(1, 9))} {count}"
        )
        directory = packages.get(package)
        if count and directory is not None:
            path = f"{directory}/{base}" if directory else base
            expected.setdefault(path, []).append(range(start, end + 1))
    return "\n".join(lines) + "\n", packages, expected


class Coverage(unittest.TestCase):
    """Executed blocks, under the path from the root."""

    @given(profile())
    def test_profile_reads_back(self, drawn: tuple) -> None:
        """Count-zero and foreign blocks dropped; the rest as ranges."""
        text, packages, expected = drawn
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / "cover.out").write_text(text, encoding="utf-8")
            self.assertEqual(mg.read_coverage(Path(scratch), packages), expected)


class Relative(unittest.TestCase):
    """A path from the root, either way the root is spelt."""

    @given(PATH, st.lists(SEGMENT, min_size=1, max_size=3), st.sampled_from(["", "/"]))
    def test_relative_strips_the_root(
        self, path: str, root: list[str], slash: str
    ) -> None:
        """root/path is path; the root itself is empty."""
        top = "/" + "/".join(root)
        self.assertEqual(mg.relative(f"{top}/{path}", top + slash), path)
        self.assertEqual(mg.relative(top, top), "")


class Survivors(unittest.TestCase):
    """Not covered, explained, or a survivor."""

    @given(
        st.data(),
        st.tuples(PATH, st.integers(1, 200), KIND),
        st.sampled_from(["uncovered", "explained", "survived"]),
        st.lists(st.tuples(st.integers(1, 200), KIND), max_size=4),
    )
    def test_three_classes(
        self,
        data: st.DataObject,
        site: tuple[str, int, str],
        how: str,
        others: list[tuple[int, str]],
    ) -> None:
        """The class follows coverage and a matching sound record."""
        path, line, kind = site
        records = [
            mg.Record(path=path, line=n, site=n, kind=k, reason="why")
            for n, k in others
            if (n, k) != (line, kind)
        ]
        if how == "explained":
            records.append(
                mg.Record(path=path, line=line, site=line, kind=kind, reason="why")
            )
        records = data.draw(st.permutations(records))
        covered = {} if how == "uncovered" else {path: [range(line, line + 1)]}
        shard = mg.Shard("/r", {}, {path}, {path}, covered, records)
        verdict = mg.Verdict()
        mutant = {"filePath": f"/r/{path}", "line": line, "type": kind}
        mg.class_survivor(shard, verdict, mutant, "site")
        self.assertEqual(verdict.not_covered, ["site"] if how == "uncovered" else [])
        self.assertEqual(verdict.survived, ["site"] if how == "survived" else [])


if __name__ == "__main__":
    unittest.main()

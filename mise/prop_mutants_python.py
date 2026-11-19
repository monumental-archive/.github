#!/usr/bin/env python3
"""mutants_python.py's readers, on configs and listings nobody wrote.

The properties of `test_mutants_python.py`'s fixed rows (#1555), over
generated adoption configs, pragma records, results and diffs:

- Invariance (V): in either dialect (pyproject's `[tool.mutmut]` TOML
  list, setup.cfg's indented value), an exclusion entry is a finding
  exactly when no comment line stands directly above it, named at its
  own line under its own key; a reasoned entry is counted instead.
  Adding the reason clears it, removing it reds. Another section's
  lists are never read.
- Invariance (V): a pragma is reasoned exactly when ` -- ` and a word
  follow it, and an `end` pragma owes nothing.
- Round trip (T): a `mutmut results` listing reads back as its
  killed, surviving and unjudged tallies; a mutant name built from a
  function, or a method, reads back as that function (#1555: a method
  whose name began `x_` lost the prefix).
- Round trip (T): a survivor's removed line is found at its own line,
  after its function's `def`.
- Refusal (R): `scan`, `pragmas`, `read_results` and `read_show` read
  any text and never raise.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "mutants_python",
    Path(__file__).with_name("mutants_python.py"),
)
mp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mp)

KEY = st.sampled_from(["do_not_mutate", "do_not_mutate_patterns", "only_mutate"])
ENTRY = st.from_regex(r"[a-z_*./]{1,12}", fullmatch=True)
IDENT = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)
Finding = tuple[int, str, str]


@st.composite
def config(draw: st.DrawFn, dialect: str) -> tuple[str, list[Finding], int]:
    """Write an adoption config with exclusion lists.

    Returns:
        The text, the expected findings, and the reasoned count.

    """
    section = mp.SECTIONS[dialect]
    lines = ["[other]", "do_not_mutate = [", '  "not.read",', "]", section]
    findings: list[Finding] = []
    reasoned = 0
    for key in draw(st.lists(KEY, unique=True, max_size=3)):
        lines.append(f"{key} = [" if dialect == "pyproject.toml" else f"{key} =")
        for entry in draw(st.lists(ENTRY, max_size=4)):
            explained = draw(st.booleans())
            if explained:
                mark = (
                    "#" if dialect == "pyproject.toml" else draw(st.sampled_from("#;"))
                )
                lines.append(f"  {mark} {draw(ENTRY)} is equivalent")
                if dialect == "pyproject.toml":
                    lines += [""] * draw(st.integers(0, 1))
            spelt = f'"{entry}",' if dialect == "pyproject.toml" else entry
            lines.append(f"  {spelt}")
            if explained:
                reasoned += 1
            else:
                findings.append((len(lines), key, spelt.rstrip(",")))
        if dialect == "pyproject.toml":
            lines.append("]")
        else:
            lines.append("")
    lines += ["[after]", "only_mutate = [", '  "not.read",', "]"]
    return "\n".join(lines) + "\n", findings, reasoned


class Exclusions(unittest.TestCase):
    """Each entry without a reason, by line and key."""

    @given(st.sampled_from(sorted(mp.SECTIONS)).flatmap(config))
    def test_exactly_the_unexplained_entries(self, drawn: tuple) -> None:
        """Named at their lines; reasoned ones counted; others unread."""
        text, findings, reasoned = drawn
        section = next(s for s in mp.SECTIONS.values() if s in text.splitlines())
        got = mp.scan(text, section)
        self.assertEqual(got.findings, findings, text)
        self.assertEqual(got.listed, reasoned)

    @given(st.text(max_size=200), st.sampled_from(sorted(mp.SECTIONS.values())))
    def test_any_text_is_scanned(self, text: str, section: str) -> None:
        """Findings and a count, never a traceback."""
        got = mp.scan(text, section)
        self.assertGreaterEqual(got.listed, 0)


class Pragmas(unittest.TestCase):
    """A reason after ` -- `, or `end`."""

    @given(
        st.lists(
            st.tuples(
                IDENT,
                st.integers(1, 999),
                st.sampled_from(["", " block", " start", " end"]),
                st.one_of(st.just(""), IDENT.map(" -- {}".format)),
            ),
            max_size=6,
        )
    )
    def test_reasoned_or_bare(self, rows: list[tuple]) -> None:
        """Bare sites by file:line; `end` never owes a reason."""
        text = "".join(
            f"{f}.py\t{n}\t# pragma: no mutate{word}{reason}\n"
            for f, n, word, reason in rows
        )
        sites, bare = mp.pragmas(text)
        owed = [(f, n, r) for f, n, w, r in rows if w != " end"]
        self.assertEqual(sites, sum(1 for _, _, r in owed if r))
        self.assertEqual(bare, [f"{f}.py:{n}" for f, n, r in owed if not r])

    @given(st.text(max_size=200))
    def test_any_text_is_read(self, text: str) -> None:
        """A count and a list, never a traceback."""
        sites, bare = mp.pragmas(text)
        self.assertGreaterEqual(sites, 0)
        self.assertIsInstance(bare, list)


STATE = st.sampled_from([
    *sorted(mp.KILLED),
    *sorted(mp.SURVIVED),
    "suspicious",
    "skipped",
    "not checked",
])


class Results(unittest.TestCase):
    """The listing's tallies, and the function behind each mutant."""

    @given(st.lists(st.tuples(IDENT, IDENT, st.integers(1, 99), STATE), max_size=8))
    def test_tallies_read_back(self, rows: list[tuple]) -> None:
        """Killed, survivors in order, the rest unjudged."""
        text = "".join(f"    pkg.{m}.x_{f}__mutmut_{n}: {s}\n" for m, f, n, s in rows)
        got = mp.read_results(text)
        self.assertEqual(got.total, len(rows))
        self.assertEqual(got.killed, sum(1 for *_, s in rows if s in mp.KILLED))
        self.assertEqual(
            got.survivors,
            [
                (s, f"pkg.{m}.x_{f}__mutmut_{n}")
                for m, f, n, s in rows
                if s in mp.SURVIVED
            ],
        )
        self.assertEqual(got.total, got.killed + len(got.survivors) + got.unjudged)

    @given(IDENT, IDENT, st.one_of(st.none(), IDENT), st.integers(1, 99))
    @example("a", "x_", "a", 1)
    def test_function_of_round_trip(
        self, module: str, func: str, cls: str | None, n: int
    ) -> None:
        """A function's or a method's mutant names it."""
        name = f"x_{func}" if cls is None else f"xǁ{cls}ǁ{func}"
        self.assertEqual(mp.function_of(f"pkg.{module}.{name}__mutmut_{n}"), func)

    @given(st.text(max_size=200))
    def test_any_text_is_read(self, text: str) -> None:
        """Tallies and a diff, never a traceback."""
        self.assertGreaterEqual(mp.read_results(text).total, 0)
        self.assertIsInstance(mp.read_show(text).file, str)


class Locate(unittest.TestCase):
    """The removed line, after its own def."""

    @given(
        st.lists(IDENT, min_size=1, max_size=4, unique=True),
        st.data(),
    )
    def test_survivor_line(self, funcs: list[str], data: st.DataObject) -> None:
        """The first line reading as the removed one at or after the def."""
        lines: list[str] = []
        where: dict[str, tuple[int, int]] = {}
        for func in funcs:
            lines.append(f"def {func}(x):")
            start = len(lines)
            body = data.draw(st.lists(IDENT, min_size=1, max_size=3, unique=True))
            lines += [f"    {b} = x" for b in body]
            where[func] = (start, start + 1)
        func = data.draw(st.sampled_from(funcs))
        start, first = where[func]
        removed = lines[first - 1]
        self.assertEqual(
            mp.locate("\n".join(lines) + "\n", func, removed + "  "), first
        )
        self.assertEqual(mp.locate("\n".join(lines) + "\n", func, "nowhere()"), start)


if __name__ == "__main__":
    unittest.main()

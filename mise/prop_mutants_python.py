#!/usr/bin/env python3
"""mutants_python.py's readers, on dumps, pragmas and plans nobody wrote.

The properties of `test_mutants_python.py`'s fixed rows (#1555, #1771):

- Invariance (V): `cosmic-ray dump` read in any line order is the same
  sorted list; the sort is (path, operator, occurrence).
- Invariance (V): shards k/N for k in 0..N-1 judge every mutant
  exactly once, each shard in the list's order.
- Invariance (V): a pragma is reasoned exactly when it is the line form
  and ` -- ` and a word follow it; `block`, `start` and `end` are each
  refused by name.
- Round trip (T): a shard spelt `k/N` reads back as (k, N) exactly when
  0 <= k < N; a plan written by the leg's writer reads back as its
  mutants, tests and line map.
- Round trip (T): a line inside a statement maps to that statement's
  first line, never past the line itself.
- Refusal (R): `read_pragmas`, `read_dump` and `statement_lines` read
  any text and never raise anything but the decoder's named refusal.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import json
import operator
import unittest

import decoded
import mutants_python as mp
from hypothesis import strategies as st
from typed_hypothesis import given

IDENT = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)
MODULE: st.SearchStrategy[str] = IDENT.map("{}.py".format)
OPERATOR: st.SearchStrategy[str] = IDENT.map("core/{}".format)
MUTANT = st.builds(
    mp.Mutant,
    path=MODULE,
    operator=OPERATOR,
    occurrence=st.integers(0, 50),
    line=st.integers(1, 500),
    function=IDENT,
    statement=st.integers(1, 500),
)
MUTANTS = st.lists(
    MUTANT, max_size=12, unique_by=lambda m: (m.path, m.operator, m.occurrence)
)


def dump_line(m: mp.Mutant) -> str:
    """Write a mutant as one `cosmic-ray dump` line.

    Returns:
        The JSON line.

    """
    spec = {
        "module_path": m.path,
        "operator_name": m.operator,
        "occurrence": m.occurrence,
        "start_pos": [m.line, 0],
        "end_pos": [m.line, 1],
        "operator_args": {},
        "definition_name": m.function,
    }
    line: list[object] = [{"job_id": "j", "mutations": [spec]}, None]
    return json.dumps(line)


def bare(m: mp.Mutant) -> mp.Mutant:
    """Drop the statement line, which the dump does not carry.

    Returns:
        The mutant as `read_dump` reads it.

    """
    return mp.Mutant(m.path, m.operator, m.occurrence, m.line, m.function)


class Dump(unittest.TestCase):
    """The dump, in any order, is one sorted list."""

    @given(
        MUTANTS.flatmap(
            lambda ms: st.tuples(
                st.just(ms), st.permutations([dump_line(m) for m in ms])
            )
        )
    )
    def test_order_does_not_matter(
        self, drawn: tuple[list[mp.Mutant], list[str]]
    ) -> None:
        """Lines in any order read back sorted by (path, operator, occurrence)."""
        mutants, lines = drawn
        got = mp.read_dump("\n".join(lines) + "\n")
        expected = sorted(bare(m) for m in mutants)
        self.assertEqual(got, expected)
        keys = [(m.path, m.operator, m.occurrence) for m in got]
        sorted_keys = sorted(keys)
        self.assertEqual(keys, sorted_keys)

    @given(st.text(max_size=200))
    def test_any_text_is_read_or_refused_by_name(self, text: str) -> None:
        """A list, or the decoder's own refusal."""
        with contextlib.suppress(decoded.UndecodableError):
            self.assertIsInstance(mp.read_dump(text), list)


class Shards(unittest.TestCase):
    """k/N partitions the sorted list."""

    @given(MUTANTS, st.integers(1, 8))
    def test_every_mutant_exactly_once(self, mutants: list[mp.Mutant], n: int) -> None:
        """The shards' union is the list, each mutant in one shard."""
        ordered = sorted(mutants)
        shards = [mp.shard_of(ordered, k, n) for k in range(n)]
        union = sorted(m for s in shards for m in s)
        self.assertEqual(union, ordered)
        for shard in shards:
            in_order = sorted(shard)
            self.assertEqual(shard, in_order)

    @given(st.integers(0, 40), st.integers(0, 40))
    def test_spelling_round_trips(self, k: int, n: int) -> None:
        """`k/N` reads back exactly when 0 <= k < N."""
        expected = (k, n) if k < n else None
        self.assertEqual(mp.parse_shard(f"{k}/{n}"), expected)


class Pragmas(unittest.TestCase):
    """The line form, with a reason after ` -- `."""

    @given(
        st.lists(
            st.tuples(
                IDENT,
                st.integers(1, 999),
                st.sampled_from(["", " block", " start", " end"]),
                st.one_of(st.just(""), IDENT.map(" -- {}".format)),
            ),
            max_size=6,
            unique_by=operator.itemgetter(0, 1),
        )
    )
    def test_reasoned_refused_or_by_form(
        self, rows: list[tuple[str, int, str, str]]
    ) -> None:
        """Reasoned sites read back; every other one is refused by its site."""
        text = "".join(
            f"{f}.py\t{n}\t# pragma: no mutate{word}{reason}\n"
            for f, n, word, reason in rows
        )
        got = mp.read_pragmas(text)
        reasoned = {(f"{f}.py", n) for f, n, w, r in rows if not w and r}
        self.assertEqual(got.reasoned, reasoned)
        refused = [(f, n, w, r) for f, n, w, r in rows if w or not r]
        self.assertEqual(len(got.refused), len(refused))
        for (f, n, w, _), message in zip(refused, got.refused, strict=True):
            self.assertTrue(message.startswith(f"{f}.py:{n}: "), message)
            if w:
                self.assertIn(f"no mutate{w} — only the line form", message)

    @given(st.text(max_size=200))
    def test_any_text_is_read(self, text: str) -> None:
        """Sites and refusals, never a traceback."""
        got = mp.read_pragmas(text)
        self.assertIsInstance(got.refused, list)


class Statements(unittest.TestCase):
    """A line maps to the first line of the statement that holds it."""

    @given(st.lists(st.integers(0, 3), min_size=1, max_size=6), st.data())
    def test_continuation_lines_map_to_their_call(
        self, extra: list[int], data: st.DataObject
    ) -> None:
        """Each line of a call spread over lines maps to the call's first."""
        lines: list[str] = []
        firsts: list[tuple[int, int]] = []
        for n, more in enumerate(extra):
            first = len(lines) + 1
            lines.append(f"v{n} = f(" if more else f"v{n} = f()")
            lines += ["    0," for _ in range(more)]
            if more:
                lines.append(")")
            firsts.append((first, len(lines)))
        spans = mp.statement_lines("\n".join(lines) + "\n")
        first, last = data.draw(st.sampled_from(firsts))
        line = data.draw(st.integers(first, last))
        self.assertEqual(mp.statement_of(spans, line), first)

    @given(st.text(max_size=200), st.integers(1, 50))
    def test_any_source_never_maps_past_the_line(self, text: str, line: int) -> None:
        """Never a traceback, never a later line."""
        self.assertLessEqual(mp.statement_of(mp.statement_lines(text), line), line)


class Plan(unittest.TestCase):
    """What the writer writes, the reader reads."""

    @given(
        MUTANTS,
        st.lists(st.tuples(IDENT, st.integers(0, 10**6)), max_size=5),
        st.data(),
    )
    def test_round_trip(
        self,
        mutants: list[mp.Mutant],
        tests: list[tuple[str, int]],
        data: st.DataObject,
    ) -> None:
        """Mutants, tests and the line map come back as they went."""
        lines: dict[str, dict[str, list[int]]] = {}
        for m in mutants:
            if tests:
                indices = data.draw(
                    st.lists(st.integers(0, len(tests) - 1), max_size=3)
                )
                lines.setdefault(m.path, {})[str(m.statement)] = indices
        text = mp.to_json({
            "head": "abc",
            "command": "t {test}",
            "mutants": [
                [m.path, m.operator, m.occurrence, m.line, m.function, m.statement]
                for m in mutants
            ],
            "tests": [list(t) for t in tests],
            "lines": lines,
        })
        got = mp.read_plan(text)
        self.assertEqual((got.head, got.command), ("abc", "t {test}"))
        ordered = sorted(mutants)
        self.assertEqual(got.mutants, ordered)
        self.assertEqual(got.tests, tests)
        mapped = {(p, int(n)): v for p, per in lines.items() for n, v in per.items()}
        self.assertEqual(got.lines, mapped)


if __name__ == "__main__":
    unittest.main()

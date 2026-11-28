"""coverage_js_runner.py's readers, on lockfiles and commands nobody wrote.

The properties of `test_coverage_js_runner.py`'s fixed rows (#1548),
over generated lockfile entries and `measured` command lines:

- Invariance (V): `providers` names exactly the vitest coverage
  providers a lockfile's entries carry, in any of the four lockfile
  spellings and in any order, and nothing for a package whose name
  only begins like one.
- Invariance (V): `measured_run` keeps every word of a vitest run
  behind any launcher the leg knows, in order, inserts `run` only where
  there is no subcommand, and appends the coverage flags last (#2105).
- Refusal (R): vitest behind any other command, or no vitest at all,
  is left unchanged: never a run the leg would measure.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt import coverage_js_runner as cj
from belt.typed_hypothesis import given

VERSION = st.from_regex(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True)
# One entry per lockfile dialect, for a package at a version.
DIALECTS = (
    "  {name}@{version}:",
    "  '{name}@{version}':",
    "{name}@^{version}:",
    '"{name}@npm:{version}":',
    '    "{name}@{version}",',
    '    "node_modules/{name}": {{',
)
PACKAGE = st.sampled_from([
    "@vitest/coverage-v8",
    "@vitest/coverage-istanbul",
    "@vitest/coverage-v8x",
    "vitest",
    "left-pad",
])
WORD = st.from_regex(r"[a-z][a-z0-9.=-]{0,8}", fullmatch=True)


def draw_lockfile(draw: st.DrawFn) -> tuple[str, set[str]]:
    """Draw lockfile lines naming packages.

    Returns:
        The text and the providers it carries.

    """
    lines, expected = [], set()
    for _ in range(draw(st.integers(0, 6))):
        name = draw(PACKAGE)
        lines.append(
            draw(st.sampled_from(DIALECTS)).format(name=name, version=draw(VERSION))
        )
        if name in {"@vitest/coverage-v8", "@vitest/coverage-istanbul"}:
            expected.add(name.rsplit("-", 1)[-1])
    return "\n".join(lines) + "\n", expected


lockfile = st.composite(draw_lockfile)


class TestProviders:
    """Every dialect, every order."""

    @given(lockfile())
    @example(('"node_modules/@vitest/coverage-v8x": {\n', set()))
    def test_exactly_the_providers_named(self, drawn: tuple[str, set[str]]) -> None:
        """v8 and istanbul as the entries carry them, nothing else."""
        text, expected = drawn
        assert cj.providers(text) == expected


BINARY = st.sampled_from(["vitest", "node_modules/.bin/vitest", "./x/vitest"])


# Words after vitest: file filters and options, never a subcommand or a
# coverage flag (WORD starts with a letter).
def not_subcommand(word: str) -> bool:
    """Say whether a word is not a vitest subcommand.

    Returns:
        True for a file filter or an option.

    """
    return word not in cj.SUBCOMMANDS


ARGS = st.lists(WORD.filter(not_subcommand), max_size=4)
# `run` given, or no subcommand at all.
RUN: list[str] = ["run"]
NONE: list[str] = []
SUBCOMMAND = (RUN, NONE)


class TestMeasuredRun:
    """Known launchers measured; any other command left as it is."""

    @given(st.sampled_from(cj.LAUNCHERS), BINARY, st.sampled_from(SUBCOMMAND), ARGS)
    def test_a_known_launcher_keeps_its_words(
        self, launcher: tuple[str, ...], binary: str, run: list[str], args: list[str]
    ) -> None:
        """Every word kept in order, `run` once, the flags appended."""
        head = [*launcher, binary]
        given_words = [*run, *args]
        rest = given_words if given_words[:1] == ["run"] else ["run", *given_words]
        got = cj.measured_run("v8", "/d", [*head, *given_words])
        if got is None:
            pytest.fail("a vitest run behind a known launcher went unmeasured")
        assert got[: len(head)] == head
        assert got[len(head) : len(head) + len(rest)] == rest
        tail = got[len(head) + len(rest) :]
        assert len(tail) == 5
        assert all(w.startswith("--coverage.") for w in tail), tail

    @given(st.sampled_from(["sudo", "env", "time", "bunx", "nice"]), BINARY, ARGS)
    def test_an_unknown_launcher_is_unchanged(
        self, prefix: str, binary: str, args: list[str]
    ) -> None:
        """Never measured, so the leg's report names the remedy."""
        assert cj.measured_run("v8", "/d", [prefix, binary, *args]) is None

    @given(ARGS)
    def test_no_vitest_is_unchanged(self, args: list[str]) -> None:
        """A command naming no vitest is not the leg's."""
        command = ["pytest", *(a for a in args if a != "vitest")]
        assert cj.measured_run("v8", "/d", command) is None

#!/usr/bin/env python3
"""python_properties.py, called in the process that runs it (#1696).

The runner imports Hypothesis, which the ordinary suite's interpreter
does not carry, so its in-process rows live here and run under
`test:python-properties`, on the belt's Hypothesis. Each row plants
property files in a scratch directory and calls `main` with the same
arguments the leg passes; `test_python_properties.py` keeps the leg's
own rows (which start python-properties.sh) and the helper's one
script row.

A call loads its own profile, so each restores the profile it found:
the rest of this run keeps the bounds it was started with.

- Refusal (R): any argument list that names no profile and a file is
  a usage error, exit 2, never a traceback.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import hermetic
import python_properties as pp
from hypothesis import settings
from hypothesis import strategies as st
from typed_hypothesis import given

HOLDS = """\
import unittest

from hypothesis import strategies as st
from typed_hypothesis import given


class Holds(unittest.TestCase):
    @given(st.integers())
    def test_double_is_even(self, n: int) -> None:
        self.assertEqual(2 * n % 2, 0)
"""

FAILS = """\
import unittest

from hypothesis import strategies as st
from typed_hypothesis import given


class Fails(unittest.TestCase):
    @given(st.integers(0, 100))
    def test_small(self, n: int) -> None:
        self.assertLess(n, 10)
"""

NO_PROPERTY = """\
import unittest


class Plain(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertTrue(True)
"""

OWN_BOUNDS = """\
import unittest

from hypothesis import settings, strategies as st
from typed_hypothesis import given


class Bounded(unittest.TestCase):
    @settings(max_examples=3)
    @given(st.integers())
    def test_few(self, n: int) -> None:
        self.assertIsInstance(n, int)
"""


def call(
    argv: list[str], files: dict[str, str], env: dict[str, str]
) -> hermetic.Called:
    """Plant `files` in a scratch directory and call main from it.

    `env` adds to the environment a child would have had (`hermetic.env`);
    the profile in force before the call is loaded again after it, and
    the planted modules leave `sys.modules`.

    Returns:
        The status main returned and what it wrote to each stream.

    """
    before = settings.get_current_profile_name()
    with tempfile.TemporaryDirectory() as scratch:
        for name, text in files.items():
            Path(scratch, name).write_text(text, encoding="utf-8")
        try:
            return hermetic.call_main(
                lambda: pp.main(argv), env=hermetic.env(**env), cwd=scratch
            )
        finally:
            settings.load_profile(before)
            for index, name in enumerate(argv[1:]):
                sys.modules.pop(f"prop_{index}_{Path(name).stem}", None)


class Gate(unittest.TestCase):
    """The gate profile over planted files."""

    def test_a_passing_file_holds(self) -> None:
        """One property that holds: exit 0, the test and the count."""
        done = call(["gate", "prop_ok.py"], {"prop_ok.py": HOLDS}, {})
        self.assertEqual(
            done,
            hermetic.Called(
                0,
                "prop_ok.py: prop_0_prop_ok.Holds.test_double_is_even ... ok\n"
                "test:python-properties: 1 test(s) in 1 file(s) held\n",
                "",
            ),
        )

    def test_a_failing_property_is_red_with_its_example(self) -> None:
        """A property that fails: red, the shrunk example printed."""
        done = call(["gate", "prop_bad.py"], {"prop_bad.py": FAILS}, {})
        self.assertEqual(done.returncode, 1)
        self.assertIn("prop_0_prop_bad.Fails.test_small ... FAIL\n", done.stdout)
        self.assertIn("AssertionError: 10 not less than 10", done.stderr)
        self.assertIn("    n=10,\n", done.stderr)

    def test_a_file_with_no_property_is_refused(self) -> None:
        """A prop_*.py holding no @given test: red, named."""
        done = call(["gate", "prop_none.py"], {"prop_none.py": NO_PROPERTY}, {})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            "test:python-properties: prop_none.py: holds no Hypothesis test"
            " (no @given)\n",
        )

    def test_a_test_restating_a_bound_is_refused(self) -> None:
        """@settings(max_examples=…) on a test: red, the bound named."""
        done = call(["gate", "prop_own.py"], {"prop_own.py": OWN_BOUNDS}, {})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr,
            "test:python-properties: prop_own.py: prop_0_prop_own.Bounded.test_few"
            " sets its own bounds (max_examples=3); the belt's profile owns them\n",
        )

    def test_a_file_that_cannot_be_imported_is_refused(self) -> None:
        """An import error is the file's own report, by name."""
        done = call(
            ["gate", "prop_broken.py"], {"prop_broken.py": "import nowhere\n"}, {}
        )
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stderr.startswith(
                "test:python-properties: prop_broken.py: could not be imported\n"
            ),
            done.stderr,
        )


class Audit(unittest.TestCase):
    """The audit profile: its seed printed first, a given one honoured."""

    def test_the_audit_prints_its_seed_and_honours_a_given_one(self) -> None:
        """ORG_PROPERTY_SEED reproduces a run; the seed comes first."""
        done = call(
            ["audit", "prop_bad.py"], {"prop_bad.py": FAILS}, {"ORG_PROPERTY_SEED": "7"}
        )
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stdout.startswith(
                "audit:python-properties: seed 7 (reproduce: ORG_PROPERTY_SEED=7)\n"
            ),
            done.stdout,
        )
        self.assertIn(
            "audit:python-properties: seed 7 — commit each falsifying example above"
            " as an @example row with its fix\n",
            done.stderr,
        )


USAGE = "usage: python_properties.py gate|audit FILE...\n"


class Usage(unittest.TestCase):
    """Arguments that name no profile and a file."""

    @given(
        st.one_of(
            st.lists(st.text(max_size=6), max_size=1),
            st.lists(
                st.text(max_size=6).filter(lambda word: word not in pp.LABELS),
                min_size=2,
                max_size=4,
            ),
        )
    )
    def test_a_usage_error_is_two(self, argv: list[str]) -> None:
        """R: exit 2 with the usage line, and nothing run."""
        done = hermetic.call_main(lambda: pp.main(argv))
        self.assertEqual(done, hermetic.Called(2, "", USAGE))


if __name__ == "__main__":
    unittest.main()

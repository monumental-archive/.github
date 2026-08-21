#!/usr/bin/env python3
"""Table tests for the tsconfig dial floor (#699).

One row per branch of each guard, both directions: a config that states
the org's level and the nearest one that does not. The org's level itself
is read from the DELIVERED `mise/tsc-flags.txt` rather than a copy, so a
dial added to that file is covered the moment it lands — which is the
whole point of deriving the comparison from it.

Mutation-checked: each test was run against a deliberately broken helper
and observed to fail. A floor check that admits a weaker config looks
exactly like a floor check that works.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "tsconfig_dials",
    Path(__file__).with_name("tsconfig-dials.py"),
)
tsconfig_dials = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tsconfig_dials)

FLAGS_FILE = Path(__file__).with_name("tsc-flags.txt")
REQUIRED, UNREADABLE = tsconfig_dials.dials(FLAGS_FILE)


def flags(*lines: str) -> Path:
    """Write a flags file into a temporary tree.

    Returns:
        Its path.

    """
    path = Path(tempfile.mkdtemp()) / "tsc-flags.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class DeliveredFlagsTest(unittest.TestCase):
    """The file the org actually ships must be comparable as it stands."""

    def test_every_flag_is_readable(self) -> None:
        """A flag this check cannot compare is a dial nobody enforces."""
        self.assertEqual(UNREADABLE, [])

    def test_the_level_is_not_empty(self) -> None:
        """An empty level would pass every config in the org."""
        self.assertTrue(REQUIRED)

    def test_every_dial_is_a_boolean(self) -> None:
        """The comparison is boolean; a non-boolean would need new law."""
        for key, want in REQUIRED.items():
            with self.subTest(key=key):
                self.assertIsInstance(want, bool)

    def test_the_inverted_dials_are_required_false(self) -> None:
        """`--allowUnreachableCode false` must demand false, not true."""
        self.assertEqual(REQUIRED["allowUnreachableCode"], False)
        self.assertEqual(REQUIRED["allowUnusedLabels"], False)
        self.assertEqual(REQUIRED["skipLibCheck"], False)

    def test_a_bare_flag_is_required_true(self) -> None:
        """The ordinary shape."""
        self.assertEqual(REQUIRED["strict"], True)
        self.assertEqual(REQUIRED["noUncheckedIndexedAccess"], True)

    def test_declaration_is_required(self) -> None:
        """TS5069: isolatedDeclarations cannot be set without it."""
        self.assertIn("declaration", REQUIRED)
        self.assertIn("isolatedDeclarations", REQUIRED)

    def test_adjudicated_lines_are_not_dials(self) -> None:
        """`# adjudicated:` records a decision; it enforces nothing."""
        self.assertNotIn("adjudicated:", REQUIRED)
        self.assertNotIn("alwaysStrict", REQUIRED)


class DialsTest(unittest.TestCase):
    """Reading the org's level out of the flags file."""

    def test_the_two_shapes_the_file_uses(self) -> None:
        """A bare flag and an explicit literal, and nothing else."""
        required, unreadable = tsconfig_dials.dials(
            flags("--strict", "--allowUnreachableCode false", "--noEmit true"),
        )
        self.assertEqual(unreadable, [])
        self.assertEqual(
            required,
            {"strict": True, "allowUnreachableCode": False, "noEmit": True},
        )

    def test_comments_and_blanks_are_not_dials(self) -> None:
        """The file is mostly prose; none of it may become a dial."""
        required, unreadable = tsconfig_dials.dials(
            flags(
                "# The strict family, which GROWS on its own",
                "",
                "--strict  # trailing reason",
                "# adjudicated: noImplicitAny — implied by --strict",
            ),
        )
        self.assertEqual(unreadable, [])
        self.assertEqual(required, {"strict": True})

    def test_a_value_the_check_cannot_compare_stops_it(self) -> None:
        """Guessing at an unknown shape would leave a dial unenforced."""
        _required, unreadable = tsconfig_dials.dials(flags("--target es2025"))
        self.assertEqual(len(unreadable), 1)
        self.assertIn("target", unreadable[0])

    def test_a_line_that_is_not_a_flag_stops_it(self) -> None:
        """A stray word must not be silently skipped."""
        _required, unreadable = tsconfig_dials.dials(flags("strict"))
        self.assertEqual(len(unreadable), 1)
        self.assertIn("not a flag", unreadable[0])


class JudgeTest(unittest.TestCase):
    """Comparing one resolved config against the level."""

    def test_a_config_at_the_level_passes(self) -> None:
        """Equality is the pass, for both polarities."""
        self.assertEqual(
            tsconfig_dials.judge(
                {"strict": True, "skipLibCheck": False},
                {"strict": True, "skipLibCheck": False},
            ),
            [],
        )

    def test_an_absent_dial_fails(self) -> None:
        """Absent is the defect #699 is about, so absent must fail."""
        findings = tsconfig_dials.judge({}, {"strict": True})
        self.assertEqual(len(findings), 1)
        self.assertIn("absent", findings[0])
        self.assertIn("strict", findings[0])

    def test_a_weaker_dial_fails_in_both_polarities(self) -> None:
        """`false` where the org wants true, and true where it wants false."""
        pairs = (({"strict": False}, True), ({"skipLibCheck": True}, False))
        for stated, want in pairs:
            with self.subTest(stated=stated):
                key = next(iter(stated))
                findings = tsconfig_dials.judge(stated, {key: want})
                self.assertEqual(len(findings), 1)
                self.assertIn("weaker", findings[0])

    def test_the_live_finding_from_the_fixture(self) -> None:
        """monumental-archive states skipLibCheck true against the org's false."""
        findings = tsconfig_dials.judge({"skipLibCheck": True}, REQUIRED)
        self.assertTrue(any('"skipLibCheck": true is weaker' in f for f in findings))

    def test_stricter_is_never_inspected(self) -> None:
        """A repo may name dials the org does not; the hazard is one-way."""
        self.assertEqual(
            tsconfig_dials.judge(
                {"strict": True, "noImplicitAny": True, "target": "es2025"},
                {"strict": True},
            ),
            [],
        )

    def test_a_non_boolean_value_counts_as_weaker(self) -> None:
        """A dial set to something odd is not the org's level either."""
        findings = tsconfig_dials.judge({"strict": "yes"}, {"strict": True})
        self.assertEqual(len(findings), 1)

    def test_findings_keep_the_flags_file_order(self) -> None:
        """The report reads in the order the org states its level."""
        findings = tsconfig_dials.judge({}, {"a": True, "b": True, "c": True})
        self.assertEqual([f.split('"')[1] for f in findings], ["a", "b", "c"])


class MainTest(unittest.TestCase):
    """End to end, through the entry point the belt task pipes into."""

    @staticmethod
    def invoke(resolved: object, flags_file: Path | None = None) -> tuple:
        """Run the checker over one resolved config.

        Returns:
            Exit status, stdout and stderr.

        """
        argv = [
            "--flags",
            str(flags_file or FLAGS_FILE),
            "--name",
            "tsconfig.json",
        ]
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO(json.dumps(resolved) if resolved is not None else "{")
        original = tsconfig_dials.sys.stdin
        tsconfig_dials.sys.stdin = stdin
        try:
            with redirect_stdout(out), redirect_stderr(err):
                status = tsconfig_dials.main(argv)
        finally:
            tsconfig_dials.sys.stdin = original
        return status, out.getvalue(), err.getvalue()

    def test_a_config_at_the_org_level_passes(self) -> None:
        """Built from the delivered level itself, so it cannot drift."""
        status, out, _err = self.invoke({"compilerOptions": dict(REQUIRED)})
        self.assertEqual(status, 0)
        self.assertIn(f"{len(REQUIRED)} dial(s)", out)

    def test_the_scaffold_stub_passes_its_own_check(self) -> None:
        """A scaffold that fails the org's gate is worse than none."""
        stub = json.loads(
            (Path(__file__).parents[1] / "scaffold" / "tsconfig.json").read_text(
                encoding="utf-8",
            ),
        )
        status, _out, err = self.invoke(stub)
        self.assertEqual(status, 0, msg=err)

    def test_a_stripped_config_fails_with_every_dial_named(self) -> None:
        """The old scaffold instruction, refused, and told what is missing."""
        status, _out, err = self.invoke(
            {"compilerOptions": {"lib": ["es2025"], "module": "nodenext"}},
        )
        self.assertEqual(status, 1)
        for key in REQUIRED:
            self.assertIn(f'"{key}"', err)

    def test_one_weaker_dial_fails_the_whole_config(self) -> None:
        """The fixture's actual state."""
        options = dict(REQUIRED)
        options["skipLibCheck"] = True
        status, _out, err = self.invoke({"compilerOptions": options})
        self.assertEqual(status, 1)
        self.assertIn("skipLibCheck", err)

    def test_a_config_with_no_compiler_options_fails(self) -> None:
        """Nothing stated is nothing enforced."""
        status, _out, _err = self.invoke({})
        self.assertEqual(status, 1)

    def test_a_compileroptions_that_is_not_an_object_fails(self) -> None:
        """Malformed input gets a sentence, not a traceback."""
        status, _out, err = self.invoke({"compilerOptions": []})
        self.assertEqual(status, 1)
        self.assertIn("not an object", err)

    def test_unresolvable_json_fails(self) -> None:
        """Output from tsc that is not JSON must not pass."""
        status, _out, err = self.invoke(None)
        self.assertEqual(status, 1)
        self.assertIn("did not resolve", err)

    def test_a_flags_file_naming_no_dial_fails(self) -> None:
        """An empty level would silently pass every repo in the org."""
        status, _out, err = self.invoke(
            {"compilerOptions": {}},
            flags("# nothing but prose"),
        )
        self.assertEqual(status, 1)
        self.assertIn("no dial", err)

    def test_a_flags_file_the_check_cannot_read_fails(self) -> None:
        """Refuse rather than enforce a subset nobody chose."""
        status, _out, err = self.invoke(
            {"compilerOptions": {"strict": True}},
            flags("--strict", "--target es2025"),
        )
        self.assertEqual(status, 1)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()

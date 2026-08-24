#!/usr/bin/env python3
r"""Table tests for the derived pgrx upgrade script (#772).

`generate-pgrx-upgrade.sh` is mostly capability — download a tarball,
run a container, prove the result against a live Postgres. One part of
it is a pure function, and it is the part that decides what consumers
run: two generated schemas in, one upgrade script or one refusal out.
Every branch of it is reachable with two strings and no container.

WHAT DRIVES WHAT. The generator is a heredoc inside the shell script, so
these tests EXTRACT it and run the shipped bytes rather than a copy that
could drift. If the block is renamed the extraction fails loudly, which
is the behaviour a copy would not have.

FIXTURES ARE REAL, then mutated where history offers nothing.
`testdata/lab_pg--0.27.0.sql` and `testdata/lab_pg--0.28.1.sql` are
`cargo pgrx package` output, extracted unedited from the published
`lab_pg-0.27.0-pg18-linux-amd64.tar.gz` and its 0.28.1 sibling on
release-lab, and `testdata/lab_pg--0.27.0--0.28.1.sql` is the upgrade
script the release path itself derived and shipped between them. So one
row here is a true oracle: real input, real expected output, byte for
byte.

The other rows mutate that capture, because the branches they exercise
have never happened in this organisation — lab_pg has carried the same
two functions since 0.14.2, and every published upgrade script it has is
a no-op. A mutation of a real capture still carries pgrx's own dialect
(`CREATE  FUNCTION` with two spaces, the `/* <begin connected objects>
*/` banners, the `-- src/lib.rs:6` provenance lines); only the delta is
mine. Inventing the dialect as well is what [[fixture-is-not-evidence]]
warns about, and burned canon v1.24.0.

THE GUARD IS THE OTHER HALF. #792 found that the emitted psql guard
exceeded the org's own 80-column SQL rule once the extension name was
long enough — invisible until then because `lab_pg` is six characters
and landed at 78. #794 fixed it by splitting the guard across three
lines; that shipped in canon v1.58.2 and was WRONG, because PostgreSQL's
extension loader ignores only lines BEGINNING WITH `\\echo`, so a bare
`\\quit` line is parsed and fails. #801 reverted to one shortened line.
The arithmetic is table-tested below; the half a linter cannot answer —
whether a database will actually load the file — is the oracle in
`TheLoadableGuardOracle`, which is blocked on #813 and skipped with its
reason rather than quietly absent.

stdlib `unittest`, matching mise/test_*.py — #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "release" / "generate-pgrx-upgrade.sh"
TESTDATA = CANON / "release" / "testdata"
PYTHON = shutil.which("python3")

# The real capture, and the real thing the release path derived from it.
REAL_PREV = (TESTDATA / "lab_pg--0.27.0.sql").read_text()
REAL_NEW = (TESTDATA / "lab_pg--0.28.1.sql").read_text()
REAL_DERIVED = (TESTDATA / "lab_pg--0.27.0--0.28.1.sql").read_text()

# The guard the loader tolerates, and the one canon v1.58.2 shipped.
# Kept as data because `TheLoadableGuardOracle` must red on the second
# one the moment #813 gives it a database to ask.
GOOD_GUARD = "\\echo Use ALTER EXTENSION {ext} UPDATE to load this file. \\quit"
V1_58_2_GUARD = (
    "\\echo Use \"ALTER EXTENSION {ext} UPDATE TO '{new}'\"\n"
    "\\echo to load this file.\n"
    "\\quit"
)
# The org's SQL column rule, named once so the rows below compare against
# it rather than against a literal nobody can grep for.
GUARD_COLUMNS = 80
# 58 fixed characters, so the longest name that fits is 22.
GUARD_FIXED = len(GOOD_GUARD.format(ext=""))
LONGEST_NAME_THAT_FITS = GUARD_COLUMNS - GUARD_FIXED


def generator_source() -> str:
    """Return the derivation exactly as `generate-pgrx-upgrade.sh` ships it.

    Returns:
        The body of the script's `EOPY` heredoc.

    Raises:
        AssertionError: if the block is not found exactly once, which
            means the script was reshaped and these tests are no longer
            driving what it runs.

    """
    lines = SCRIPT.read_text().splitlines()
    opens = [i for i, line in enumerate(lines) if line.rstrip().endswith("<< 'EOPY'")]
    closes = [i for i, line in enumerate(lines) if line.strip() == "EOPY"]
    if len(opens) != 1 or len(closes) != 1 or closes[0] < opens[0]:
        msg = (
            f"expected exactly one EOPY heredoc in {SCRIPT.name}, found "
            f"{len(opens)} open and {len(closes)} close markers — the "
            "generator moved and this suite is testing nothing"
        )
        raise AssertionError(msg)
    return "\n".join(lines[opens[0] + 1 : closes[0]]) + "\n"


class Derivation:
    """One run of the shipped generator over two schemas."""

    def __init__(self, root: str) -> None:
        """Write the extracted generator beside the files it will read."""
        self.root = Path(root)
        self.source = self.root / "derive.py"
        self.source.write_text(generator_source())

    def run(
        self,
        prev: str,
        new: str,
        *,
        ext: str = "lab_pg",
        versions: tuple[str, str] = ("0.27.0", "0.28.1"),
        data: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        """Derive an upgrade script from two schema texts.

        Returns:
            The completed process and the emitted script, which is the
            empty string when the generator refused.

        """
        (self.root / "prev.sql").write_text(prev)
        (self.root / "new.sql").write_text(new)
        out = self.root / "upgrade.sql"
        if out.exists():
            out.unlink()
        args = [str(self.root / "prev.sql"), str(self.root / "new.sql"), str(out)]
        if data is not None:
            (self.root / "next-data.sql").write_text(data)
            args.append(str(self.root / "next-data.sql"))
        prev_v, new_v = versions
        env = dict(os.environ)
        env.update(EXT_NAME=ext, PREV_V=prev_v, NEW_V=new_v)
        # ruff: ignore[subprocess-without-shell-equals-true]
        result = subprocess.run(
            [PYTHON, str(self.source), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, out.read_text() if out.exists() else ""


# The DDL deltas, named once. Kept out of the concatenations below
# because a SQL literal inside a `+` expression reads as query
# construction, and the org suppresses no rule anywhere.
PAIR_OF_INTS = "CREATE TYPE lab_pair AS (a INT, b INT);\n"
PAIR_WIDENED = "CREATE TYPE lab_pair AS (a INT, b TEXT);\n"
WIDEN_MIGRATION = "ALTER TYPE lab_pair ALTER ATTRIBUTE b TYPE TEXT;"
SEED_ROW = "INSERT INTO lab_seed VALUES (1);\n"
ANSWER_COMMENT = "COMMENT ON FUNCTION lab_answer() IS 'the answer';\n"


def a_function(name: str, returns: str = "INT /* i32 */", args: str = "") -> str:
    """Return one pgrx-shaped function statement.

    pgrx's own spacing and banners, copied from the real capture: the
    double space after CREATE is what `FUNC_RE` has to tolerate, so a
    fixture that tidied it would stop testing the thing.

    Returns:
        A `/* <begin connected objects> */`-wrapped CREATE FUNCTION.

    """
    return (
        "/* <begin connected objects> */\n"
        f"-- crates/lab-pg/src/lib.rs:6\n"
        f"-- lab_pg::{name}\n"
        f'CREATE  FUNCTION "{name}"({args}) RETURNS {returns}\n'
        "STRICT\n"
        "LANGUAGE c /* Rust */\n"
        f"AS 'MODULE_PATHNAME', '{name}_wrapper';\n"
        "/* </end connected objects> */\n"
    )


@unittest.skipUnless(PYTHON, "python3 runs the extracted generator")
class TheGuardArithmetic(unittest.TestCase):
    """The psql guard must fit 80 columns AND stay one line (#792, #801)."""

    def test_the_real_derived_script_is_reproduced_byte_for_byte(self) -> None:
        """The oracle row: real inputs, real published output.

        release-lab v0.28.1 shipped this exact file. If the generator
        stops producing it, something in the emission changed under a
        release path whose output is immutable once tagged.
        """
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(REAL_PREV, REAL_NEW)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(REAL_DERIVED, script)

    def test_the_guard_is_one_line_carrying_both_meta_commands(self) -> None:
        r"""#801's invariant: only `\echo` lines are ignored by the loader.

        A bare `\\quit` on its own line is parsed by the server and
        fails. This is the shape assertion that v1.58.2 would not have
        survived.
        """
        with TemporaryDirectory() as d:
            _, script = Derivation(d).run(REAL_PREV, REAL_NEW)
        first = script.splitlines()[0]
        self.assertTrue(first.startswith("\\echo "), first)
        self.assertIn("\\quit", first)
        self.assertNotIn("\\quit", script.splitlines()[1:])

    def test_every_emitted_line_fits_the_orgs_eighty_columns(self) -> None:
        """The org's derived files meet the org's own rules (#792, ruled (a))."""
        with TemporaryDirectory() as d:
            _, script = Derivation(d).run(REAL_PREV, REAL_NEW)
        too_long = [line for line in script.splitlines() if len(line) > GUARD_COLUMNS]
        self.assertEqual([], too_long)

    def test_the_longest_name_that_fits_is_emitted_at_exactly_eighty(self) -> None:
        """The ceiling is a measured boundary, not a rounded one."""
        name = "e" * LONGEST_NAME_THAT_FITS
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(REAL_PREV, REAL_NEW, ext=name)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(GUARD_COLUMNS, len(script.splitlines()[0]))

    def test_one_character_more_refuses_with_the_arithmetic(self) -> None:
        """A name a database would accept but the guard cannot carry.

        A PostgreSQL identifier may be 63 bytes, so this is a real
        ceiling. Failing here with the sum beats failing two minutes
        later in `lint:sql` with a column count.
        """
        name = "e" * (LONGEST_NAME_THAT_FITS + 1)
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(REAL_PREV, REAL_NEW, ext=name)
        self.assertEqual(1, result.returncode)
        self.assertIn("the psql guard would exceed 80 columns", result.stderr)
        self.assertIn(f"= {GUARD_COLUMNS + 1} > {GUARD_COLUMNS}", result.stderr)
        self.assertEqual("", script)


@unittest.skipUnless(PYTHON, "python3 runs the extracted generator")
class TheStatementDecisions(unittest.TestCase):
    """What the diff of two generated schemas is allowed to become.

    Four classes, each with its own rule: functions are replaceable,
    named objects are creatable and droppable but not changeable,
    COMMENT/GRANT/REVOKE are always replayable, and everything else is
    opaque — fine while identical, underivable otherwise.
    """

    def test_identical_schemas_derive_a_library_only_release(self) -> None:
        """No DDL at all is a valid answer, and says which release it was."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(REAL_PREV, REAL_PREV)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("No schema change between 0.27.0 and 0.28.1", script)
        self.assertNotIn("CREATE", script)

    def test_a_new_function_is_created_replaceably(self) -> None:
        """CREATE OR REPLACE, so a re-run over a partial upgrade is safe."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV, REAL_PREV + a_function("lab_extra")
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('CREATE OR REPLACE FUNCTION "lab_extra"()', script)

    def test_an_unchanged_function_emits_nothing(self) -> None:
        """The previous install already ran it; re-emitting it is noise."""
        with TemporaryDirectory() as d:
            _, script = Derivation(d).run(
                REAL_PREV, REAL_PREV + a_function("lab_extra")
            )
        self.assertNotIn("lab_answer", script)
        self.assertNotIn("lab_version", script)

    def test_a_removed_function_is_dropped_by_signature(self) -> None:
        """DROP FUNCTION names the identity arguments, never the defaults."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV + a_function("lab_extra", args="a INT DEFAULT 1, b TEXT"),
                REAL_PREV,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('DROP FUNCTION "lab_extra"(a INT, b TEXT);', script)
        self.assertNotIn("DEFAULT", script)

    def test_a_changed_return_type_is_dropped_before_it_is_recreated(self) -> None:
        """CREATE OR REPLACE cannot change a return type; Postgres refuses it."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV + a_function("lab_extra", returns="INT /* i32 */"),
                REAL_PREV + a_function("lab_extra", returns="TEXT /* String */"),
            )
        self.assertEqual(0, result.returncode, result.stderr)
        drop = script.index('DROP FUNCTION "lab_extra"()')
        create = script.index('CREATE OR REPLACE FUNCTION "lab_extra"()')
        self.assertLess(drop, create, script)

    def test_drops_come_out_in_reverse_creation_order(self) -> None:
        """The closest thing to a correct destroy order two schemas offer."""
        removed = a_function("lab_first") + a_function("lab_second")
        with TemporaryDirectory() as d:
            _, script = Derivation(d).run(REAL_PREV + removed, REAL_PREV)
        self.assertLess(
            script.index('DROP FUNCTION "lab_second"'),
            script.index('DROP FUNCTION "lab_first"'),
            script,
        )

    def test_a_new_named_object_is_created_and_a_removed_one_dropped(self) -> None:
        """Named kinds are creatable when new and droppable when gone."""
        typ = PAIR_OF_INTS
        with TemporaryDirectory() as d:
            added, _ = Derivation(d).run(REAL_PREV, REAL_PREV + typ), None
            result_added, script_added = added
            result_gone, script_gone = Derivation(d).run(REAL_PREV + typ, REAL_PREV)
        self.assertEqual(0, result_added.returncode, result_added.stderr)
        self.assertIn("CREATE TYPE lab_pair AS (a INT, b INT);", script_added)
        self.assertEqual(0, result_gone.returncode, result_gone.stderr)
        self.assertIn("DROP TYPE lab_pair;", script_gone)

    def test_a_changed_named_object_refuses_and_names_the_remedy(self) -> None:
        """An in-place change to a TYPE is not derivable from two schemas."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV + PAIR_OF_INTS, REAL_PREV + PAIR_WIDENED
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("TYPE lab_pair changed since 0.27.0", result.stderr)
        self.assertIn("sql/next-data.sql", result.stderr)
        self.assertEqual("", script)

    def test_a_new_opaque_statement_refuses(self) -> None:
        """Anything the generator cannot classify must not be guessed at."""
        with TemporaryDirectory() as d:
            result, _ = Derivation(d).run(REAL_PREV, REAL_PREV + SEED_ROW)
        self.assertEqual(1, result.returncode)
        self.assertIn("new since 0.27.0 and not derivable", result.stderr)

    def test_a_removed_opaque_statement_refuses(self) -> None:
        """Both directions: a disappearing opaque statement is not a drop."""
        with TemporaryDirectory() as d:
            result, _ = Derivation(d).run(REAL_PREV + SEED_ROW, REAL_PREV)
        self.assertEqual(1, result.returncode)
        self.assertIn("removed since 0.27.0 and not derivable", result.stderr)

    def test_an_unchanged_opaque_statement_is_fine_and_silent(self) -> None:
        """It is already in the installed extension; replaying it is not owed."""
        opaque = SEED_ROW
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(REAL_PREV + opaque, REAL_PREV + opaque)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("INSERT", script)

    def test_a_new_comment_is_replayed(self) -> None:
        """COMMENT/GRANT/REVOKE are always safe to re-apply."""
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV,
                REAL_PREV + ANSWER_COMMENT,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("COMMENT ON FUNCTION lab_answer() IS 'the answer';", script)

    def test_an_unchanged_comment_is_not_replayed(self) -> None:
        """Replayable is not the same as replayed: identical means nothing to do."""
        comment = ANSWER_COMMENT
        with TemporaryDirectory() as d:
            _, script = Derivation(d).run(REAL_PREV + comment, REAL_PREV + comment)
        self.assertNotIn("COMMENT ON", script)

    def test_a_data_fragment_downgrades_refusals_and_is_folded_in(self) -> None:
        """`sql/next-data.sql` is the author declaring they handled it.

        The declaration is not trusted — the round-trip proof still
        compares catalogs — but it must stop the generator refusing, and
        the fragment must reach the emitted script.
        """
        with TemporaryDirectory() as d:
            result, script = Derivation(d).run(
                REAL_PREV + PAIR_OF_INTS,
                REAL_PREV + PAIR_WIDENED,
                data=WIDEN_MIGRATION,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("::warning::", result.stderr)
        self.assertIn("the round-trip proof decides", result.stderr)
        self.assertIn("ALTER TYPE lab_pair ALTER ATTRIBUTE b TYPE TEXT;", script)

    def test_an_unterminated_statement_fails_rather_than_truncating(self) -> None:
        """A schema the splitter cannot finish reading is not a smaller schema."""
        with TemporaryDirectory() as d:
            result, _ = Derivation(d).run(
                REAL_PREV, REAL_PREV + PAIR_OF_INTS.replace(";\n", "\n")
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("unterminated statement", result.stdout + result.stderr)


@unittest.skip(
    "blocked by #813: the loadable-guard oracle needs the belt-provisioned "
    "PostgreSQL #813 owns, and #772 refuses to build a second provisioning "
    "for it. Unskip when #813 lands, pointing SERVER at its mechanism."
)
class TheLoadableGuardOracle(unittest.TestCase):
    r"""Does a real PostgreSQL accept the file, and not merely a linter.

    This is the half #792 proved a linter cannot answer. `lint:sql` said
    the three-line guard was fine; the server rejected it with
    `syntax error at or near "\"` on the bare `\quit`, because the
    extension loader ignores only lines BEGINNING WITH `\echo`. That
    shape shipped in canon v1.58.2 and was reverted by #801.

    Written and skipped rather than absent, so the gap is a recorded
    obligation with a body to run rather than a note somebody has to
    remember to act on. Both rows are required: the current guard must
    LOAD, and v1.58.2's must FAIL. A one-directional oracle would have
    passed for v1.58.2 too.
    """

    def test_the_current_guard_loads(self) -> None:
        """CREATE EXTENSION then ALTER EXTENSION UPDATE over the derived file."""
        raise NotImplementedError(self.__doc__)

    def test_the_v1_58_2_guard_is_rejected_by_the_server(self) -> None:
        """The negative control, and the reason this oracle exists."""
        raise NotImplementedError(self.__doc__)


if __name__ == "__main__":
    unittest.main()

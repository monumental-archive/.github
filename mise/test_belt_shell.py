#!/usr/bin/env python3
"""Table tests for the task-body extractor's guards (#619).

These are the REGRESSION net, not the evidence. The evidence for #619 is
the live sweep recorded on the PR: the issue's own planted probe reddens
`mise run ci`, removing any one of the file's `# shellcheck disable`
directives reddens it, and a tree with no mise config skips clean.

What the table adds is the part a live sweep on one repository cannot
reach. The refusals here fire on shapes the canon does not contain — a
list `run`, a body whose comments look like Tera, an unterminated
`'''` — and a guard that skips when it should run looks exactly like
success (#364). Each is driven in both directions, and the rewrite path
is asserted through the same `tomllib` read the writer verifies itself
with, never against the splicer's own bookkeeping.

The one that matters most is the list `run`. mise documents it, the
canon uses none, so nothing in a live sweep would ever have exercised
it — and the writer would have spliced a `'''` block over the `run = [`
line and eaten the array in the first adopter that had one.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import importlib.util
import io
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

_SPEC = importlib.util.spec_from_file_location(
    "belt_shell",
    Path(__file__).with_name("belt-shell.py"),
)
bs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bs)

# A body mise would run verbatim, already in the org's format.
CLEAN = 'echo "hello"\n'
# The same body before shfmt: a one-line block holding two statements,
# and a quoted variable inside `[[ ]]` that `-s` simplifies.
DIRTY = '[[ -n "${x}" ]] || { echo "no"; exit 0; }\n'
# The two lines every task-defining config owes (#700). Spelled out here
# rather than built from SHELL_PIN, so a change to the constant has to
# face a test that disagrees with it.
PIN = '[task_config]\nshell = "bash -euo pipefail -c"\n'


def write(tmp: str, text: str) -> Path:
    """Put a config on disk and hand back its path.

    Returns:
        The written file.

    """
    path = Path(tmp) / "mise.toml"
    path.write_text(text, encoding="utf-8")
    return path


class Locate(unittest.TestCase):
    """Where each body starts, and how it is quoted."""

    def test_header_forms_and_delimiters(self) -> None:
        """Bare and quoted task names, all three quoting forms."""
        found = bs.locate(
            '[tasks.ci]\nrun = "one"\n\n'
            "[tasks.\"lint:a\"]\nrun = '''\nbody\n'''\n\n"
            '[tasks."fix:b"]\ndescription = "d"\nrun = """\nbody\n"""\n',
        )
        self.assertEqual(found["ci"], (2, ""))
        self.assertEqual(found["lint:a"], (5, "'''"))
        self.assertEqual(found["fix:b"], (11, '"""'))

    def test_other_tables_end_the_task(self) -> None:
        """A `run` under a later table is not the task's own."""
        found = bs.locate('[tasks.a]\n[env]\nrun = "not a task body"\n')
        self.assertNotIn("a", found)

    def test_subtable_ends_the_task(self) -> None:
        """`[tasks.a.env]` closes `[tasks.a]`; `run` cannot live in one."""
        found = bs.locate(
            '[tasks.a]\n[tasks.a.env]\nrun = "x"\n[tasks.b]\nrun = "y"\n',
        )
        self.assertNotIn("a", found)
        self.assertEqual(found["b"], (5, ""))

    def test_first_run_wins(self) -> None:
        """A duplicate key is TOML's error, not this scanner's to invent."""
        found = bs.locate('[tasks.a]\nrun = "first"\nrun = "second"\n')
        self.assertEqual(found["a"], (2, ""))


class Read(unittest.TestCase):
    """What one config contributes."""

    def test_list_run_is_checked_and_not_rewritable(self) -> None:
        """Every command is a body; none owns a splice region."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.a]\nrun = ["echo one", "echo two"]\n')
            bodies = bs.read(path).bodies
        self.assertEqual([b.source for b in bodies], ["echo one", "echo two"])
        self.assertEqual({b.run_line for b in bodies}, {2})
        self.assertFalse(any(b.rewritable for b in bodies))

    def test_string_run_is_rewritable(self) -> None:
        """The ordinary case, so the flag above is not vacuously false."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.a]\nrun = "echo one"\n')
            bodies = bs.read(path).bodies
        self.assertTrue(bodies[0].rewritable)

    def test_bodyless_and_blank_tasks_contribute_nothing(self) -> None:
        """`file =`, `depends`-only, and an empty string are not bodies."""
        with TemporaryDirectory() as tmp:
            path = write(
                tmp,
                '[tasks.a]\nfile = "x.sh"\n\n[tasks.b]\ndepends = ["a"]\n\n'
                '[tasks.c]\nrun = "   "\n',
            )
            self.assertEqual(bs.read(path).bodies, [])

    def test_env_names_skip_mise_directives(self) -> None:
        """`_.file` and friends are mise directives, not variable names."""
        with TemporaryDirectory() as tmp:
            path = write(
                tmp,
                '[env]\nA = "1"\n"_.file" = ".env"\n\n'
                '[tasks.t]\nrun = "x"\n[tasks.t.env]\nB = "2"\n',
            )
            self.assertEqual(bs.read(path).env, {"A", "B"})

    def test_no_tasks_is_an_empty_contribution(self) -> None:
        """A TOML file that is not a mise config reads as nothing."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[package]\nname = "not-mise"\n')
            config = bs.read(path)
        self.assertEqual(config.bodies, [])
        self.assertIsNone(config.shell)


class Prelude(unittest.TestCase):
    """The header that makes a body faithful to what mise runs."""

    def test_flags_become_a_set_line(self) -> None:
        """`bash -euo pipefail -c` is a dialect AND an errexit contract."""
        self.assertEqual(
            bs.prelude("bash -euo pipefail -c", ["B", "A"]),
            '#!/usr/bin/env bash\nset -euo pipefail\nexport A=""\nexport B=""\n',
        )

    def test_no_flags_emits_no_set_line(self) -> None:
        """Mise's own bare default has nothing to declare."""
        self.assertEqual(bs.prelude("sh -c", []), "#!/usr/bin/env sh\n")

    def test_names_are_exported_not_assigned(self) -> None:
        """An assigned-but-unused name would be a finding of its own."""
        self.assertIn('export A=""', bs.prelude("bash -c", ["A"]))


class Tera(unittest.TestCase):
    """Template statements are not shell, and must survive a round trip."""

    def test_markers_become_comments_and_come_back(self) -> None:
        """The `fix:input-forwarding` shape, which #624 read as a defect."""
        source = "{% raw %}\nval='${{ inputs.x }}'\n{% endraw %}\n"
        shell, count = bs.shellify(source)
        self.assertEqual(count, 2)
        self.assertNotIn("\n{%", "\n" + shell)
        self.assertEqual(bs.unshellify(shell, count), source)

    def test_indented_markers_keep_their_indent(self) -> None:
        """Shfmt may move a comment; the marker still has to come back."""
        shell, count = bs.shellify("  {% raw %}\n  x\n  {% endraw %}\n")
        self.assertEqual(bs.unshellify(shell, count).splitlines()[0], "  {% raw %}")

    def test_count_mismatch_refuses(self) -> None:
        """A body whose own comments look like Tera is not rewritten blind."""
        self.assertIsNone(bs.unshellify("#{% raw %}\nx\n", 2))

    def test_one_liner_gains_a_trailing_newline(self) -> None:
        """A single-line TOML string carries none; shfmt wants one."""
        self.assertEqual(bs.shellify("echo hi")[0], "echo hi\n")


class Splice(unittest.TestCase):
    """The rewrite region, and every way it refuses to be found."""

    @staticmethod
    def body(line: int, delim: str) -> object:
        """Build a Body pointing at a given line.

        Returns:
            The Body under test.

        """
        return bs.Body("t", "x", line, delim, rewritable=True)

    def test_block_is_replaced_whole(self) -> None:
        """The closing delimiter bounds the region."""
        lines = ["[tasks.t]", "run = '''", "old", "'''", "after"]
        out = bs.splice(lines, self.body(2, "'''"), "new\n")
        self.assertEqual(out, ["[tasks.t]", "run = '''", "new", "'''", "after"])

    def test_one_liner_is_promoted_to_a_block(self) -> None:
        """A formatter that adds lines needs a form that can hold them."""
        out = bs.splice(['run = "a; b"'], self.body(1, ""), "a\nb\n")
        self.assertEqual(out, ["run = '''", "a", "b", "'''"])

    def test_text_containing_the_delimiter_refuses(self) -> None:
        """`'''` inside the body would close the string it is written into."""
        lines = ["run = '''", "old", "'''"]
        self.assertIsNone(bs.splice(lines, self.body(1, "'''"), "x='''\n"))

    def test_unterminated_block_refuses(self) -> None:
        """No closing delimiter means no region, so nothing is guessed."""
        self.assertIsNone(bs.splice(["run = '''", "old"], self.body(1, "'''"), "new\n"))

    def test_wrong_anchor_refuses(self) -> None:
        """The recorded line must actually be a `run =` key."""
        self.assertIsNone(bs.splice(["not a run key"], self.body(1, ""), "new\n"))

    def test_out_of_range_anchor_refuses(self) -> None:
        """A body located at line 0 has no anchor at all."""
        self.assertIsNone(bs.splice(["run = 'x'"], self.body(0, ""), "new\n"))


class Rewrite(unittest.TestCase):
    """Writing, and the re-read that is allowed to contradict the writer."""

    def test_dirty_body_lands_and_decodes(self) -> None:
        """Verified through tomllib, never from the splice's bookkeeping."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"[tasks.t]\nrun = '''\n{DIRTY}'''\n")
            changed, refused = bs.rewrite(path, bs.read(path).bodies)
            body = tomllib.loads(path.read_text(encoding="utf-8"))["tasks"]["t"]["run"]
        self.assertEqual(changed, 1)
        self.assertEqual(refused, [])
        self.assertEqual(body, '[[ -n ${x} ]] || {\n  echo "no"\n  exit 0\n}\n')

    def test_clean_body_is_left_alone(self) -> None:
        """Idempotence: nothing to do means the file is not touched."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"[tasks.t]\nrun = '''\n{CLEAN}'''\n")
            before = path.read_text(encoding="utf-8")
            changed, refused = bs.rewrite(path, bs.read(path).bodies)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual((changed, refused), (0, []))

    def test_dirty_list_run_is_refused_by_name(self) -> None:
        """The array survives, and the refusal says why rather than skipping."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = ["[[ -n \\"${x}\\" ]] || echo no"]\n')
            before = path.read_text(encoding="utf-8")
            changed, refused = bs.rewrite(path, bs.read(path).bodies)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(changed, 0)
        self.assertEqual(len(refused), 1)
        self.assertIn("list `run` is checked but never rewritten", refused[0])

    def test_clean_list_run_is_silent(self) -> None:
        """A body needing nothing is not reported for how it is written."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = ["echo one", "echo two"]\n')
            self.assertEqual(bs.rewrite(path, bs.read(path).bodies), (0, []))

    def test_tera_body_round_trips_through_the_writer(self) -> None:
        """The markers go back, so mise still gets its template."""
        source = "{% raw %}\n" + DIRTY + "{% endraw %}\n"
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"[tasks.t]\nrun = '''\n{source}'''\n")
            changed, refused = bs.rewrite(path, bs.read(path).bodies)
            body = tomllib.loads(path.read_text(encoding="utf-8"))["tasks"]["t"]["run"]
        self.assertEqual((changed, refused), (1, []))
        self.assertTrue(body.startswith("{% raw %}\n"))
        self.assertTrue(body.rstrip("\n").endswith("{% endraw %}"))


class Findings(unittest.TestCase):
    """What the check reports, and where it says the finding is."""

    @staticmethod
    def check(path: Path) -> list[str]:
        """Run the lint half over one config.

        Returns:
            The finding lines.

        """
        config = bs.read(path)
        head = bs.prelude(config.shell or bs.DEFAULT_SHELL, sorted(config.env))
        return [f for b in config.bodies for f in bs.findings(b, path, head, [])]

    def test_a_block_body_reports_the_real_line(self) -> None:
        """Line 3 of the file, not line 2 of a temporary script."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, "[tasks.t]\nrun = '''\necho $x\n'''\n")
            found = self.check(path)
        self.assertTrue(any(f"{path}:3:" in f and "SC2086" in f for f in found))

    def test_a_one_liner_reports_its_run_line(self) -> None:
        """There is one line to point at, so it is the `run =` key."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = "echo $x"\n')
            found = self.check(path)
        self.assertTrue(any(f"{path}:2:" in f for f in found))

    def test_declared_env_is_not_an_unassigned_variable(self) -> None:
        """SC2154 answered by reading `[env]`, not by excluding the code."""
        with TemporaryDirectory() as tmp:
            path = write(
                tmp,
                '[env]\nMINE = "x"\n\n[tasks.t]\nrun = "echo \\"${MINE}\\""\n',
            )
            self.assertEqual(self.check(path), [])

    def test_undeclared_env_still_reports(self) -> None:
        """The other direction: the check is live, not switched off."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = "echo ${NOBODY_DECLARED}"\n')
            found = self.check(path)
        self.assertTrue(any("SC2154" in f for f in found))

    def test_errexit_is_modelled(self) -> None:
        """Under the declared shell, an assignment does not mask a failure.

        Without the flags this is SC2312; with them it is nothing, which
        is the 167-to-79 correction recorded on #619.
        """
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = "x=$(date)\\necho \\"${x}\\""\n')
            self.assertEqual(self.check(path), [])


class Main(unittest.TestCase):
    """The command surface's own exits."""

    def test_no_files_is_not_a_failure(self) -> None:
        """The task guards for this, and so does the helper behind it."""
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bs.main([]), 0)
        self.assertIn("nothing to check", out.getvalue())

    def test_a_non_shell_task_shell_skips(self) -> None:
        """Shellcheck has nothing to say about a `pwsh` body."""
        with TemporaryDirectory() as tmp:
            path = write(
                tmp,
                '[task_config]\nshell = "pwsh -c"\n\n[tasks.t]\nrun = "echo hi"\n',
            )
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(bs.main([str(path)]), 0)
        self.assertIn("not shell — skipped", out.getvalue())

    def test_a_clean_config_exits_zero(self) -> None:
        """And says how much it checked, so a vacuous pass is visible."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"{PIN}\n[tasks.t]\nrun = '''\n{CLEAN}'''\n")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(bs.main([str(path)]), 0)
        self.assertIn("1 task body(ies)", out.getvalue())

    def test_a_finding_exits_one(self) -> None:
        """The gate's whole point."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, "[tasks.t]\nrun = '''\necho $x\n'''\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(bs.main([str(path)]), 1)


class Unpinned(unittest.TestCase):
    """The task-shell pin assertion (#700).

    Driven in both directions per #650: every row that fires is paired
    with the row that must not. The failure this guards against is the
    silent one — a repo task under dash gets no pipefail and no nounset,
    so a body whose failure signal is a tool's exit status inside a
    pipeline goes green having not checked.
    """

    def test_tasks_without_a_pin_are_a_finding(self) -> None:
        """The defect shape: a repo defines tasks and restates nothing."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = "echo hi"\n')
            found = bs.unpinned({path: bs.read(path)})
        self.assertEqual(len(found), 1)

    def test_the_finding_carries_the_remedy(self) -> None:
        """A red gate that does not say what to paste is half a guard."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tasks.t]\nrun = "echo hi"\n')
            found = bs.unpinned({path: bs.read(path)})
        self.assertIn("[task_config]", found[0])
        self.assertIn('shell = "bash -euo pipefail -c"', found[0])
        self.assertIn(str(path), found[0])

    def test_a_pinned_config_is_silent(self) -> None:
        """The other direction, so the row above is not vacuously true."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f'{PIN}\n[tasks.t]\nrun = "echo hi"\n')
            self.assertEqual(bs.unpinned({path: bs.read(path)}), [])

    def test_a_config_with_no_tasks_is_silent(self) -> None:
        """Skip clean by name: nothing to run means no shell to pin."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, '[tools]\njq = "1.8.2"\n')
            self.assertEqual(bs.unpinned({path: bs.read(path)}), [])

    def test_a_pin_to_something_other_than_bash_is_still_a_pin(self) -> None:
        """The assertion is that the file DECIDES, not which shell it picks."""
        with TemporaryDirectory() as tmp:
            other = '[task_config]\nshell = "pwsh -c"\n'
            path = write(tmp, f'{other}\n[tasks.t]\nrun = "x"\n')
            self.assertEqual(bs.unpinned({path: bs.read(path)}), [])

    def test_main_reds_on_an_unpinned_config(self) -> None:
        """End to end, through the exit status the gate reads."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"[tasks.t]\nrun = '''\n{CLEAN}'''\n")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(bs.main([str(path)]), 1)
        self.assertIn("pins no task shell", err.getvalue())

    def test_an_env_from_config_is_not_asserted_on(self) -> None:
        """It is read for its `[env]`; its tasks are not being checked.

        The belt's own config arrives this way in every consumer repo, so
        asserting on it would red every repo for a file it does not own.
        """
        with TemporaryDirectory() as tmp:
            checked = write(tmp, f'{PIN}\n[tasks.t]\nrun = "echo hi"\n')
            other = Path(tmp) / "belt.toml"
            other.write_text('[tasks.b]\nrun = "echo belt"\n', encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = bs.main([str(checked), "--env-from", str(other)])
        self.assertEqual(rc, 0)

    def test_write_mode_does_not_assert(self) -> None:
        """`fix:belt-shell` formats bodies; it cannot add a pin for you."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"[tasks.t]\nrun = '''\n{CLEAN}'''\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(bs.main([str(path), "--write"]), 0)

    def test_an_unpinned_config_reds_even_when_modelling_is_skipped(self) -> None:
        """A file the tools decline to read still answers for its pin.

        The shell resolves to `pwsh` from the env-from config, so the body
        lint skips — the pin assertion must not skip with it.
        """
        with TemporaryDirectory() as tmp:
            checked = write(tmp, '[tasks.t]\nrun = "echo hi"\n')
            other = Path(tmp) / "belt.toml"
            other.write_text('[task_config]\nshell = "pwsh -c"\n', encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = bs.main([str(checked), "--env-from", str(other)])
        self.assertEqual(rc, 1)
        self.assertIn("not shell — skipped", out.getvalue())
        self.assertIn("pins no task shell", err.getvalue())


def nested(body: str) -> list[str]:
    """Run the nested-mise check over one planted body.

    Returns:
        The findings, which the row then reads or asserts empty.

    """
    with TemporaryDirectory() as tmp:
        path = write(tmp, f"{PIN}\n[tasks.\"lint:probe\"]\nrun = '''\n{body}'''\n")
        return bs.nested({path: bs.read(path)})


class Nested(unittest.TestCase):
    """Which `mise run` in a body is executed, and which is only written.

    The live evidence for #764 is on the PR: the canon's four sites are
    three extractions and one marker, and `mise run ci` is clean after.
    What the table adds is both directions of every branch — an echoed
    remedy MAY say "run mise run fix:x" to a human, and lint:audit-
    scheduled greps the workflows for that exact string, so a check that
    cannot tell text from a command would red the belt for documenting
    its own rule.
    """

    def test_a_planted_invocation_reds_naming_the_task_and_line(self) -> None:
        """The finding has to be actionable: which task, which line."""
        found = nested('echo "one"\nmise run fix:probe\n')
        self.assertEqual(len(found), 1)
        self.assertIn("lint:probe", found[0])
        # `run = '''` is line 5 of the config `write` lays down, so the
        # second line of the body is line 7.
        self.assertIn(":7:", found[0])
        self.assertIn("nested mise races the parallel lint fan-out", found[0])

    def test_an_unplanted_body_is_green(self) -> None:
        """The other direction, so the row above is not vacuously true."""
        self.assertEqual(nested('echo "hello"\n'), [])

    def test_an_echoed_remedy_stays_green(self) -> None:
        """A message MAY tell a human to run the task. It may not run it."""
        self.assertEqual(nested('echo "  run mise run fix:probe" >&2\n'), [])

    def test_a_grep_pattern_stays_green(self) -> None:
        """lint:audit-scheduled greps the workflows for this exact string."""
        self.assertEqual(
            nested('grep -rEq "mise run audit:${t}" .github/workflows/\n'),
            [],
        )

    def test_a_comment_inside_a_body_stays_green(self) -> None:
        """The belt states the rule in prose beside the code obeying it."""
        self.assertEqual(
            nested("# never obtained via `mise run fix:probe`\ntrue\n"),
            [],
        )

    def test_an_unquoted_argument_is_not_a_command(self) -> None:
        """Position decides, not the presence of the words."""
        self.assertEqual(nested("echo mise run fix:probe\n"), [])

    def test_a_quoted_span_across_lines_stays_quoted(self) -> None:
        """The belt's awk programs run to fifty lines inside one `'…'`.

        Scrubbing line by line would end the span at the first newline and
        read the program's own text as shell — which is how a check like
        this reds a body that executes nothing.
        """
        body = "awk '\nBEGIN { print \"x\" }\nmise run not-a-command\n' file\n"
        self.assertEqual(nested(body), [])

    def test_an_assignment_prefix_is_still_a_command(self) -> None:
        """`CITATION_OUT=… mise run fix:citation` was the #764 site itself."""
        self.assertEqual(
            len(nested('OUT="${tmp}" mise run fix:probe > /dev/null\n')),
            1,
        )

    def test_an_operator_leaves_command_position(self) -> None:
        """lint:tracks reached its fix half through `&&` inside a subshell."""
        self.assertEqual(
            len(nested('(cd "${tmp}" && mise run fix:probe) || true\n')),
            1,
        )

    def test_a_command_substitution_is_a_command(self) -> None:
        """fix:input-forwarding read its API out of `$(mise run …)`."""
        self.assertEqual(len(nested("missing=$(mise run lint:probe 2>&1)\n")), 1)

    def test_scrub_preserves_offsets(self) -> None:
        """Findings name lines, so the scrub may not move a character."""
        source = "echo \"a#b\" 'c'  # tail\nmise run x\n"
        self.assertEqual(len(bs.scrub(source)), len(source))
        self.assertEqual(bs.scrub(source).count("\n"), source.count("\n"))

    def test_main_reds_on_a_planted_invocation(self) -> None:
        """End to end, through the exit status the gate reads."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"{PIN}\n[tasks.t]\nrun = '''\nmise run other\n'''\n")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(bs.main([str(path)]), 1)
        self.assertIn("execute `mise run`", err.getvalue())

    def test_main_greens_when_the_invocation_is_removed(self) -> None:
        """The same file with the one line gone, so the red was the line."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"{PIN}\n[tasks.t]\nrun = '''\n{CLEAN}'''\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(bs.main([str(path)]), 0)

    def test_write_mode_does_not_assert(self) -> None:
        """fix:belt-shell formats bodies; it cannot extract a helper for you."""
        with TemporaryDirectory() as tmp:
            path = write(tmp, f"{PIN}\n[tasks.t]\nrun = '''\nmise run other\n'''\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(bs.main([str(path), "--write"]), 0)

    def test_an_env_from_config_is_not_asserted_on(self) -> None:
        """The belt arrives this way in every consumer; it is not theirs."""
        with TemporaryDirectory() as tmp:
            checked = write(tmp, f'{PIN}\n[tasks.t]\nrun = "echo hi"\n')
            other = Path(tmp) / "belt.toml"
            other.write_text(
                "[tasks.b]\nrun = '''\nmise run x\n'''\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = bs.main([str(checked), "--env-from", str(other)])
        self.assertEqual(rc, 0)


class NestedMarker(unittest.TestCase):
    """What excuses an invocation, and what only looks like it does.

    The marker is the only seam the rule allows, so both directions of
    every branch are driven here: a reason-less marker, and one that has
    drifted off the line it was written for, are the two ways an
    exemption stops being one.
    """

    def test_a_marker_above_the_line_excuses_it(self) -> None:
        """Where dispatching by name IS the job, the reason is written down."""
        body = '# nested-mise: dispatching by name is the job\nmise run "${t}"\n'
        self.assertEqual(nested(body), [])

    def test_a_marker_may_end_a_block_of_comment_lines(self) -> None:
        """A reason long enough to be a reason takes more than one line."""
        body = (
            "# nested-mise: the names come from the caller's own config\n"
            "# and this task is collected by nothing, so the fan-out the\n"
            "# race needs never runs it.\n"
            'mise run "${t}"\n'
        )
        self.assertEqual(nested(body), [])

    def test_a_trailing_marker_excuses_its_own_line(self) -> None:
        """On the line is as close as a marker can sit to what it excuses."""
        self.assertEqual(
            nested('mise run "${t}"  # nested-mise: dispatch is the job\n'),
            [],
        )

    def test_a_marker_with_no_reason_is_not_a_marker(self) -> None:
        """An empty exemption is the seam the marker exists to prevent."""
        self.assertEqual(len(nested("# nested-mise:\nmise run fix:probe\n")), 1)

    def test_code_between_the_marker_and_the_line_breaks_the_block(self) -> None:
        """A marker must answer for the invocation, not for its neighbour."""
        body = "# nested-mise: a reason\necho hi\nmise run fix:probe\n"
        self.assertEqual(len(nested(body)), 1)


if __name__ == "__main__":
    unittest.main()

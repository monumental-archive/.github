#!/usr/bin/env python3
"""Table tests for audit:mutants' Python leg, mise/mutants-python.sh (#1354).

The leg runs for real in a planted git tree, with `mutmut` as a stub on
PATH. The stub answers `run` with the row's exit status, `results --all
true` with the statuses the row plants, and `show <mutant>` with a diff
in mutmut 3.8.1's own shape: the mutated FUNCTION on its own, so the
hunk counts lines from the function's first line, never the file's.
Every call is logged; each row asserts the exact calls and the exact
messages, so a row cannot pass by stopping somewhere else.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import importlib.util
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import unittest
from pathlib import Path

_HERMETIC = importlib.util.spec_from_file_location(
    "hermetic", Path(__file__).resolve().with_name("hermetic.py")
)
if _HERMETIC is None or _HERMETIC.loader is None:  # pragma: no cover
    msg = "cannot load mise/hermetic.py"
    raise RuntimeError(msg)
hermetic = importlib.util.module_from_spec(_HERMETIC)
sys.modules.setdefault("hermetic", hermetic)
_HERMETIC.loader.exec_module(hermetic)


BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-python.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:mutants: python"

_SPEC = importlib.util.spec_from_file_location(
    "mutants_python", BELT / "mutants-python.py"
)
helper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helper)

MUTMUT_STUB = """#!/usr/bin/env bash
printf 'mutmut %s\\n' "$*" >> "${PLANT_LOG}"
case "$1" in
  run) exit "${PLANT_RUN:-0}" ;;
  results) cat "${PLANT_RESULTS}" ;;
  show) cat "${PLANT_SHOW}/$2" ;;
  *) exit 99 ;;
esac
"""

CONFIG = '[tool.mutmut]\nsource_paths = ["pkg"]\n'
# The module the survivor rows mutate: `answer` starts on line 10, and
# its body's return is on line 12.
MODULE = (
    "\n" * 9
    + "def answer(x):\n"
    + '    """The answer."""\n'
    + "    return x + 1\n"
    + "\n"
    + "\n"
    + "def other():\n"
    + "    return x + 1\n"
)
SURVIVOR = "pkg.mod.x_answer__mutmut_1"
# mutmut 3.8.1's `show`: the function alone, numbered from its `def`.
DIFF = (
    "--- pkg/mod.py\n"
    "+++ pkg/mod.py\n"
    "@@ -1,3 +1,3 @@\n"
    " def answer(x):\n"
    '     """The answer."""\n'
    "-    return x + 1\n"
    "+    return x - 1\n"
)
RED = [
    f"{LABEL}: red — kill each survivor with an assertion, or exclude it at its site",
    "  with '# pragma: no mutate -- <why the mutant is equivalent>'",
]
REASON = "  an exclusion records a mutant the suite cannot kill; say why at the site"


def clean_env() -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return hermetic.env()


class Planted(unittest.TestCase):
    """A planted tree and a stub mutmut; the rows are in the classes below."""

    def setUp(self) -> None:
        """Plant a git tree, the stub and its answers."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        stub = bins / "mutmut"
        stub.write_text(MUTMUT_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.tmp / "log"
        self.log.touch()
        self.results = self.tmp / "results"
        self.results.touch()
        self.show = self.tmp / "show"
        self.show.mkdir()
        self.summary = self.tmp / "summary"
        self.env = clean_env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "PLANT_LOG": str(self.log),
            "PLANT_RESULTS": str(self.results),
            "PLANT_SHOW": str(self.show),
            "MUTANTS_SUMMARY_DIR": str(self.summary),
            "ORG_BELT_DIR": str(BELT),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [GIT, *args], cwd=self.tree, env=clean_env(), check=True
        )

    def track(self, path: str, text: str) -> None:
        """Write a file into the planted tree and track it."""
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", path)

    def plant(
        self,
        statuses: dict[str, str],
        *,
        config: str = CONFIG,
        module: str = MODULE,
    ) -> None:
        """Track the config and module; plant what `results` reports."""
        self.track("pyproject.toml", config)
        self.track("pkg/mod.py", module)
        self.results.write_text(
            "".join(f"    {name}: {state}\n" for name, state in statuses.items()),
            encoding="utf-8",
        )
        (self.show / SURVIVOR).write_text(DIFF, encoding="utf-8")

    def run_leg(self, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        """Run the leg in the planted tree.

        Returns:
            The finished process and the stub's call log.

        """
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    def counts(self) -> str:
        """Read the counts the leg wrote for the totals job.

        Returns:
            python.tsv's text.

        """
        return (self.summary / "python.tsv").read_text(encoding="utf-8")


KILLED = {
    "pkg.mod.x_answer__mutmut_2": "killed",
    "pkg.mod.x_answer__mutmut_3": "timeout",
    "pkg.mod.x_other__mutmut_1": "caught by type check",
}


class Adoption(Planted):
    """Adoption is the tool's own config section, tracked."""

    def test_no_config_skips_clean_by_name(self) -> None:
        """No [tool.mutmut]: exit 0, named, nothing called, skipped counted."""
        self.track("pyproject.toml", '[project]\nname = "x"\n')
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                (
                    f"{LABEL}: no [tool.mutmut] in a tracked pyproject.toml"
                    " (or [mutmut] in setup.cfg), skipped"
                )
            ],
        )
        self.assertEqual(log, [])
        self.assertEqual(self.counts(), "0/1\tskipped\n")

    def test_untracked_config_is_not_adoption(self) -> None:
        """A config on disk that git does not track adopts nothing."""
        (self.tree / "pyproject.toml").write_text(CONFIG, encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("skipped", done.stdout)
        self.assertEqual(log, [])

    def test_setup_cfg_adopts(self) -> None:
        """`[mutmut]` in a tracked setup.cfg is the other adoption file."""
        self.track("setup.cfg", "[mutmut]\nsource_paths = pkg\n")
        self.track("pkg/mod.py", MODULE)
        self.results.write_text("", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log, ["mutmut run", "mutmut results --all true"])


class Verdict(Planted):
    """Statuses, survivors and the summary line."""

    def test_clean(self) -> None:
        """Every mutant killed: exit 0, the summary line, the counts."""
        self.plant(KILLED)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [f"{LABEL}: 3 mutants, 3 killed, 0 survived, 0 excluded (sites)"],
        )
        self.assertEqual(done.stderr, "")
        self.assertEqual(log, ["mutmut run", "mutmut results --all true"])
        self.assertEqual(self.counts(), "0/1\t3\t3\t0\t0\n")

    def test_one_survivor_is_red_with_file_and_line(self) -> None:
        """The file line, not the function-relative hunk line, is printed."""
        self.plant(KILLED | {SURVIVOR: "survived"})
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                f"{LABEL}: survived: pkg/mod.py:12: {SURVIVOR}: return x - 1",
                f"{LABEL}: 4 mutants, 3 killed, 1 survived, 0 excluded (sites)",
            ],
        )
        self.assertEqual(done.stderr.splitlines(), RED)
        self.assertEqual(
            log,
            ["mutmut run", "mutmut results --all true", f"mutmut show {SURVIVOR}"],
        )
        self.assertEqual(self.counts(), "0/1\t4\t3\t1\t0\n")

    def test_a_backslash_in_the_removed_line_is_found_at_its_true_line(self) -> None:
        """A raw regex in the mutated line: line 12, not the def's line 10.

        Fails against the shell leg before #1486: it passed the removed
        line through `awk -v`, which turns backslash-d into `d`, so no line
        matched and the survivor was reported at the `def` (measured: 10).
        """
        module = MODULE.replace(
            "    return x + 1\n", '    return re.match(r"\\d+", x)\n', 1
        )
        self.plant(KILLED | {SURVIVOR: "survived"}, module=module)
        (self.show / SURVIVOR).write_text(
            DIFF.replace(
                "-    return x + 1", '-    return re.match(r"\\d+", x)'
            ).replace("+    return x - 1", '+    return re.match(r"\\D+", x)'),
            encoding="utf-8",
        )
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines()[0],
            f"{LABEL}: survived: pkg/mod.py:12: {SURVIVOR}:"
            ' return re.match(r"\\D+", x)',
        )

    def test_no_tests_is_a_survivor(self) -> None:
        """A mutant no test reaches is the least explained of all."""
        self.plant({SURVIVOR: "no tests"})
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines()[0],
            f"{LABEL}: no tests: pkg/mod.py:12: {SURVIVOR}: return x - 1",
        )

    def test_never_judged_is_red_and_writes_no_counts(self) -> None:
        """Suspicious or not checked: red by name, nothing for the totals."""
        self.plant(KILLED | {"pkg.mod.x_other__mutmut_2": "suspicious"})
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: 1 mutant(s) never judged"
                    " (suspicious, skipped, not checked or interrupted)"
                )
            ],
        )
        self.assertFalse((self.summary / "python.tsv").exists())

    def test_failing_baseline_judges_nothing(self) -> None:
        """`mutmut run` exits 1 when the unmutated suite fails: red."""
        self.plant(KILLED)
        done, log = self.run_leg(PLANT_RUN="1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: mutmut run exited 1"
                    " — nothing judged (a failing unmutated suite exits 1)"
                )
            ],
        )
        self.assertEqual(log, ["mutmut run"])

    def test_a_shard_is_refused(self) -> None:
        """Sharding is refused: anything but 0/1 is red, nothing run."""
        self.plant(KILLED)
        done, log = self.run_leg(MUTANTS_SHARD="1/4")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: MUTANTS_SHARD='1/4'"
                    " — mutmut cannot shard; the python jobs run one shard, 0/1"
                )
            ],
        )
        self.assertEqual(log, [])


class Reasons(Planted):
    """Every exclusion carries its reason, at its site."""

    def test_pragma_with_reason_is_counted_excluded(self) -> None:
        """A reasoned pragma: exit 0, one excluded site."""
        module = MODULE.replace(
            "    return x + 1\n\n\ndef other",
            "    return x + 1  # pragma: no mutate -- off-by-one is the contract\n"
            "\n\ndef other",
        )
        self.plant(KILLED, module=module)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [f"{LABEL}: 3 mutants, 3 killed, 0 survived, 1 excluded (sites)"],
        )

    def test_pragma_without_reason_is_red_by_name(self) -> None:
        """A bare pragma: red by file and line, nothing run."""
        module = MODULE.replace(
            "def other():\n", "def other():  # pragma: no mutate block\n"
        )
        self.plant(KILLED, module=module)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: pkg/mod.py:15: # pragma: no mutate"
                    " with no reason — write it after ' -- '"
                ),
                REASON,
            ],
        )
        self.assertEqual(log, [])

    def test_pragma_text_in_a_string_is_not_a_site(self) -> None:
        """Pragmas live in comments only; a fixture string is not a site.

        The canon's own first run went red on this file's fixtures, which
        spell pragmas inside strings (run 44208357536).
        """
        module = MODULE + 'FIXTURE = "x = 1  # pragma: no mutate"\n'
        self.plant(KILLED, module=module)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("0 excluded (sites)", done.stdout)

    def test_untokenizable_file_is_red_by_name(self) -> None:
        """A file the tokenizer cannot read is a site nobody checked."""
        self.plant(KILLED)
        self.track("pkg/broken.py", 'x = """  # pragma: no mutate\n')
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertIn("mutmut-pragmas: pkg/broken.py: could not tokenize:", done.stderr)
        self.assertEqual(log, [])

    def test_region_end_needs_no_reason(self) -> None:
        """`start` carries the reason; `end` only closes the region."""
        module = (
            MODULE.replace(
                "def other():\n",
                "# pragma: no mutate start -- generated table, checked elsewhere\n"
                "def other():\n",
            )
            + "# pragma: no mutate end\n"
        )
        self.plant(KILLED, module=module)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("1 excluded (sites)", done.stdout)

    def test_list_entry_without_reason_is_red_by_name(self) -> None:
        """A do_not_mutate entry with no # line above it: red by line."""
        config = (
            CONFIG
            + "do_not_mutate = [\n"
            + "  # tests are what judges, not what is judged\n"
            + '  "pkg/test_*.py",\n'
            + '  "pkg/generated.py",\n'
            + "]\n"
        )
        self.plant(KILLED, config=config)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: pyproject.toml:6: do_not_mutate entry"
                    ' "pkg/generated.py" has no # reason line above it'
                ),
                REASON,
            ],
        )
        self.assertEqual(log, [])

    def test_reasoned_list_entries_are_counted_excluded(self) -> None:
        """Each reasoned entry is an excluded site."""
        config = (
            CONFIG
            + "do_not_mutate = [\n"
            + "  # tests are what judges, not what is judged\n"
            + '  "pkg/test_*.py",\n'
            + "]\n"
            + "do_not_mutate_patterns = [\n"
            + "  # logging lines change no result\n"
            + '  "log\\\\.debug",\n'
            + "]\n"
        )
        self.plant(KILLED, config=config)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("2 excluded (sites)", done.stdout)

    def test_entry_on_the_opening_line_is_red(self) -> None:
        """`do_not_mutate = ["x"]` leaves nowhere to write the reason."""
        config = CONFIG + '# generated\nonly_mutate = ["pkg/mod.py"]\n'
        self.plant(KILLED, config=config)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: pyproject.toml:4: only_mutate entry"
                    " an entry on the opening line has no # reason line above it"
                ),
                REASON,
            ],
        )

    def test_covered_lines_only_is_refused(self) -> None:
        """mutate_only_covered_lines drops the untested lines unexplained."""
        config = CONFIG + "mutate_only_covered_lines = true\n"
        self.plant(KILLED, config=config)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: pyproject.toml:3:"
                    " mutate_only_covered_lines = true drops every line no test runs"
                ),
                REASON,
            ],
        )

    def test_setup_cfg_entry_without_reason_is_red(self) -> None:
        """setup.cfg's one-entry-per-line form, held to the same rule."""
        self.track(
            "setup.cfg",
            "[mutmut]\nsource_paths = pkg\ndo_not_mutate =\n"
            "    # tests judge\n    pkg/test_*.py\n    pkg/generated.py\n",
        )
        self.track("pkg/mod.py", MODULE)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    f"{LABEL}: setup.cfg:6: do_not_mutate entry"
                    " pkg/generated.py has no # reason line above it"
                ),
                REASON,
            ],
        )


class ConfigReader(unittest.TestCase):
    """mutants-python.py's reader of the adoption config, on planted text (#1486)."""

    def test_a_toml_list_counts_reasoned_entries_and_names_the_rest(self) -> None:
        """Reason above: counted. None: named with its line."""
        text = (
            "[tool.mutmut]\n"
            "do_not_mutate = [\n"
            "  # generated\n"
            '  "pkg/gen.py",\n'
            '  "pkg/other.py",\n'
            "]\n"
        )
        found = helper.scan(text, "[tool.mutmut]")
        self.assertEqual(found.listed, 1)
        self.assertEqual(found.findings, [(5, "do_not_mutate", '"pkg/other.py"')])

    def test_an_entry_on_the_opening_line_is_named(self) -> None:
        """In a TOML list or a setup.cfg value alike."""
        toml = helper.scan('[tool.mutmut]\nonly_mutate = ["a"]\n', "[tool.mutmut]")
        cfg = helper.scan("[mutmut]\ndo_not_mutate = a.py\n", "[mutmut]")
        self.assertEqual(
            toml.findings, [(2, "only_mutate", "an entry on the opening line")]
        )
        self.assertEqual(
            cfg.findings, [(2, "do_not_mutate", "an entry on the opening line")]
        )

    def test_setup_cfg_entries_take_a_hash_or_semicolon_reason(self) -> None:
        """One indented entry per line; a dedent or blank ends the list."""
        text = (
            "[mutmut]\n"
            "do_not_mutate_patterns =\n"
            "  ; vendored\n"
            "  vendor/*\n"
            "  # tests\n"
            "  tests/*\n"
            "  bare/*\n"
            "\n"
            "  after/*\n"
        )
        found = helper.scan(text, "[mutmut]")
        self.assertEqual(found.listed, 2)
        self.assertEqual(found.findings, [(7, "do_not_mutate_patterns", "bare/*")])

    def test_mutate_only_covered_lines_true_is_refused(self) -> None:
        """Every spelling of true; false is fine."""
        for value in ("true", "True", "1", "t"):
            found = helper.scan(
                f"[tool.mutmut]\nmutate_only_covered_lines = {value}\n", "[tool.mutmut]"
            )
            self.assertEqual(found.findings, [(2, "refused", helper.REFUSED)], value)
        found = helper.scan(
            "[tool.mutmut]\nmutate_only_covered_lines = false\n", "[tool.mutmut]"
        )
        self.assertEqual(found.findings, [])

    def test_only_the_config_section_is_read(self) -> None:
        """The same keys under another header are someone else's."""
        text = (
            '[tool.other]\ndo_not_mutate = ["x"]\n'
            '[tool.mutmut]\nsource_paths = ["pkg"]\n'
        )
        found = helper.scan(text, "[tool.mutmut]")
        self.assertEqual((found.findings, found.listed), ([], 0))


class PragmaRule(unittest.TestCase):
    """The reason rule for `# pragma: no mutate`, directly (#1486)."""

    def test_a_reason_after_two_dashes_counts_the_site(self) -> None:
        """Reasoned: counted. Bare: named. `end`: needs none."""
        text = (
            "a.py\t3\t# pragma: no mutate -- the fixture's width\n"
            "a.py\t9\t# pragma: no mutate block\n"
            "b.py\t4\t# pragma: no mutate: end\n"
            "b.py\t5\t# pragma: no mutate --\n"
        )
        self.assertEqual(helper.pragmas(text), (1, ["a.py:9", "b.py:5"]))

    def test_the_word_after_no_mutate(self) -> None:
        """Colons and spaces before it are not the word."""
        self.assertEqual(helper.pragma_word("# pragma: no mutate: end")[0], "end")
        self.assertEqual(helper.pragma_word("# pragma: no mutate")[0], "")


class ResultsAndLines(unittest.TestCase):
    """The state mapping and the line locator, directly (#1486)."""

    def test_every_state_lands_in_one_class(self) -> None:
        """Killed four ways, survived two ways, the rest never judged."""
        text = "\n".join(
            f"    m.x_f__mutmut_{i}: {state}"
            for i, state in enumerate([
                "killed",
                "timeout",
                "caught by type check",
                "segfault",
                "survived",
                "no tests",
                "suspicious",
                "skipped",
                "not checked",
            ])
        )
        results = helper.read_results(text + "\nnot a result line\n")
        self.assertEqual((results.total, results.killed, results.unjudged), (9, 4, 3))
        self.assertEqual(
            results.survivors,
            [("survived", "m.x_f__mutmut_4"), ("no tests", "m.x_f__mutmut_5")],
        )

    def test_the_function_is_read_from_the_mutant_name(self) -> None:
        """A function's `x_` and a method's class are not its name."""
        self.assertEqual(helper.function_of("pkg.mod.x_answer__mutmut_12"), "answer")
        self.assertEqual(helper.function_of("pkg.mod.xǁShapeǁarea__mutmut_3"), "area")

    def test_a_show_diff_names_the_file_and_both_lines(self) -> None:
        """The `---` path, the first removed and added lines, marks dropped."""
        shown = helper.read_show(DIFF)
        self.assertEqual(
            (shown.file, shown.removed, shown.added),
            ("pkg/mod.py", "    return x + 1", "    return x - 1"),
        )

    def test_the_line_is_found_from_the_def_not_the_file_top(self) -> None:
        """MODULE has the same line in `other` too; `answer`'s is 12."""
        self.assertEqual(helper.locate(MODULE, "answer", "    return x + 1"), 12)
        self.assertEqual(helper.locate(MODULE, "other", "return x + 1"), 16)

    def test_no_matching_line_falls_back_to_the_def_then_zero(self) -> None:
        """The `def` when the line is gone; 0 when the function is too."""
        self.assertEqual(helper.locate(MODULE, "answer", "gone"), 10)
        self.assertEqual(helper.locate(MODULE, "absent", "return x + 1"), 0)
        self.assertEqual(helper.locate("async def go():\n    pass\n", "go", "pass"), 2)


if __name__ == "__main__":
    unittest.main()

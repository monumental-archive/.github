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

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-python.sh"
BASH = shutil.which("bash") or "bash"
GIT = shutil.which("git") or "git"
LABEL = "audit:mutants: python"

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
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


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


if __name__ == "__main__":
    unittest.main()

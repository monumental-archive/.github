"""Every gate row's measurement merges to one runner's figure (#2160).

A split test leg measures each task, and each shard of one, on a runner
of its own; mise/coverage_merge.py gathers the rows' run directories
into the one coverage:check reads. These rows plant a two-task,
two-shard layout, measure each of the four rows for real (coverage.py
under the belt's test runner, at ORG_SHARD 1/2 and 2/2), merge them,
and hold the figure to one runner's over the same suite; then the lcov
merge (the TypeScript and Rust legs) to its union, its totals to
istanbul's own rounding, and each refusal to its message.

stdlib `unittest`, run by `mise run test`.
"""

import contextlib
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from belt import coverage_merge, decoded
from tests import hermetic

HERE = hermetic.MISE
RUNNER = HERE / "unittest_guarded.py"
COVERAGE = hermetic.tool("coverage")

CODE = """\
def f(x):
    if x > 0:
        return "up"
    return "down"


def g(x):
    return x * 2


def h(x):
    if x:
        return 1
    return 0
"""


def case(name: str, body: str) -> str:
    """Write one test file calling the code.

    Returns:
        The file's text.

    """
    return (
        "import unittest\n\nimport code_under_test\n\n\n"
        f"class T(unittest.TestCase):\n    def test_{name}(self):\n        {body}\n"
    )


# Two tasks, each two files, each file reaching code the others do not.
TASKS = {
    "one": {
        "test_f_up.py": case("up", "code_under_test.f(1)"),
        "test_g.py": case("g", "code_under_test.g(1)"),
    },
    "two": {
        "test_h_true.py": case("t", "code_under_test.h(1)"),
        "test_f_down.py": case("down", "code_under_test.f(0)"),
    },
}


@pytest.mark.usefixtures("_set_up_test_measured")
class TestMeasured:
    """Two tasks on two shards each merge to one runner's figure."""

    @pytest.fixture
    def _set_up_test_measured(self, tmp_path: Path) -> None:
        """Plant the code, the two tasks' suites, and the run's rc file."""
        self.base = Path(str(tmp_path))
        self.tree = self.base / "tree"
        self.tree.mkdir()
        (self.tree / "code_under_test.py").write_text(CODE, encoding="utf-8")
        for task, files in TASKS.items():
            (self.tree / task).mkdir()
            for name, body in files.items():
                (self.tree / task / name).write_text(body, encoding="utf-8")
        self.run_dir = self.base / "run"

    def rc(self) -> Path:
        """Write the run's rc file, as `measured` writes it.

        Returns:
            Its path.

        """
        (self.run_dir / "python").mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "coverage.rc"
        path.write_text(
            "[run]\nbranch = true\nparallel = true\n"
            f"source = {self.tree}\ndata_file = {self.run_dir}/python/.coverage\n"
            "relative_files = true\nomit = */test_*.py\n",
            encoding="utf-8",
        )
        return path

    def measure(self, task: str, spec: str) -> None:
        """Run one row: one task's suite, one shard, measured."""
        rc = self.rc()
        done = subprocess.run(
            [
                COVERAGE,
                "run",
                f"--rcfile={rc}",
                str(RUNNER),
                "discover",
                "--start-directory",
                task,
                "--pattern",
                "test_*.py",
            ],
            cwd=self.tree,
            env=hermetic.env(
                ORG_SHARD=spec, ORG_TEST_WORKERS="1", PYTHONPATH=f"{self.tree}:{HERE}"
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr

    def figure(self, run: Path) -> tuple[float, float]:
        """Combine a run directory's data and read its totals.

        Returns:
            The statement and branch percentages.

        """
        rc = run / "coverage.rc"
        for args in (
            ["combine", "--keep", "--quiet"],
            ["json", "--quiet", "-o", str(run / "c.json")],
        ):
            done = subprocess.run(
                [COVERAGE, *args, f"--rcfile={rc}"],
                cwd=self.tree,
                env=hermetic.env(),
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 0, done.stderr
        doc = decoded.as_object(decoded.load_json((run / "c.json").read_bytes()), "c")
        totals = decoded.as_object(doc.get("totals"), "totals")
        statements = totals.get("percent_statements_covered")
        branches = totals.get("percent_branches_covered")
        if not isinstance(statements, float) or not isinstance(branches, float):
            pytest.fail(f"no totals in {doc}")
        return statements, branches

    def test_four_rows_merge_to_one_runners_figure(self) -> None:
        """Two tasks at 1/2 and 2/2, merged: the one-runner figure, exactly."""
        rows = self.base / "rows"
        for index, (task, spec) in enumerate(
            (t, s) for t in TASKS for s in ("1/2", "2/2")
        ):
            self.measure(task, spec)
            # Every row measures at one path, as every gate row does; the
            # artifact carries its row's directory, merged flat (#2178).
            shutil.move(self.run_dir, rows / f"coverage-run-{index}")
        merged = self.base / "merged"
        none: list[str] = []
        assert none == coverage_merge.merge(sorted(rows.iterdir()), merged)
        # The merged directory is read at the rows' own path, as the job reads it.
        shutil.move(merged, self.run_dir)
        split = self.figure(self.run_dir)
        shutil.rmtree(self.run_dir)
        for task in TASKS:
            self.measure(task, "")
        whole = self.figure(self.run_dir)
        assert whole == split
        assert split[1] < 100.0  # `h(0)` is never run: a branch is missed

    def test_one_row_merges_as_one_row(self) -> None:
        """A rows directory holding one `coverage-run-<n>/` is one row.

        The layout a lone match downloads to (#2178): the artifact
        carries its row's directory, so one row is never read as its
        own subdirectories.
        """
        self.measure("one", "")
        rows = self.base / "rows"
        rows.mkdir()
        shutil.move(self.run_dir, rows / "coverage-run-6")
        merged = self.base / "merged"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = coverage_merge.main([str(rows), str(merged)])
        assert code == 0
        assert f"coverage_merge: 1 row(s) gathered into {merged}\n" == out.getvalue()
        shutil.move(merged, self.run_dir)
        split = self.figure(self.run_dir)
        shutil.rmtree(self.run_dir)
        self.measure("one", "")
        assert self.figure(self.run_dir) == split


ONE = """SF:a.ts
BRDA:1,0,0,1
BRDA:1,0,1,0
DA:1,1
DA:2,0
DA:3,1
LF:3
LH:2
end_of_record
"""

TWO = """SF:a.ts
BRDA:1,0,0,0
BRDA:1,0,1,2
DA:1,1
DA:2,1
DA:3,0
LF:3
LH:2
end_of_record
SF:b.ts
DA:1,0
LF:1
LH:0
end_of_record
"""


class TestLcov:
    """The lcov merge: a hit on any row is a hit, totals counted afresh."""

    def test_two_partials_merge_to_their_union(self) -> None:
        """Each row covered a different line and branch: the merge has both."""
        data = coverage_merge.read_lcov(ONE)
        coverage_merge.merge_lcov(data, coverage_merge.read_lcov(TWO))
        text = coverage_merge.write_lcov(data)
        assert "SF:a.ts\nBRDA:1,0,0,1\nBRDA:1,0,1,2\nBRF:2\nBRH:2\n" in text
        assert "DA:1,2\nDA:2,1\nDA:3,1\nLF:3\nLH:3\nend_of_record\n" in text
        assert "SF:b.ts\nBRF:0\nBRH:0\nDA:1,0\nLF:1\nLH:0\n" in text

    def test_the_summary_is_istanbuls(self) -> None:
        """Lines 3 of 4 and branches 2 of 2, floored to two places as istanbul does."""
        data = coverage_merge.read_lcov(ONE)
        coverage_merge.merge_lcov(data, coverage_merge.read_lcov(TWO))
        got = coverage_merge.summary(data)
        want = {
            "total": {
                "lines": {"total": 4, "covered": 3, "pct": 75.0},
                "branches": {"total": 2, "covered": 2, "pct": 100.0},
            }
        }
        assert want == got
        assert coverage_merge.percent(2, 3) == 66.66
        assert coverage_merge.percent(0, 0) == 100.0

    def test_rows_are_merged_by_leg(self) -> None:
        """TypeScript and Rust reports merge; Python and Go files sit side by side."""
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            for index, text in enumerate((ONE, TWO)):
                row = base / "rows" / f"r{index}"
                (row / "typescript").mkdir(parents=True)
                (row / "typescript" / "lcov.info").write_text(text, "utf-8")
                (row / "rust-lcov.info").write_text(text, "utf-8")
                (row / "go").mkdir()
                (row / "go" / f"covcounters.x.{index}").write_text("c", "utf-8")
                (row / "go" / "covmeta.x").write_text("m", "utf-8")
                (row / "python").mkdir()
                (row / "python" / f".coverage.host.{index}").write_text("d", "utf-8")
            run = base / "run"
            none: list[str] = []
            assert none == coverage_merge.merge(sorted((base / "rows").iterdir()), run)
            summary = decoded.load_json(
                (run / "typescript" / "coverage-summary.json").read_bytes()
            )
            total = decoded.as_object(decoded.as_object(summary, "s").get("total"), "t")
            lines = decoded.as_object(total.get("lines"), "lines")
            assert lines.get("pct") == 75.0
            assert "LH:3" in (run / "rust-lcov.info").read_text("utf-8")
            assert (run / "rust").is_dir()
            got = sorted(p.name for p in (run / "go").iterdir())
            want: list[str] = ["covcounters.x.0", "covcounters.x.1", "covmeta.x"]
            assert want == got
            assert len(list((run / "python").iterdir())) == 2

    def test_a_clash_is_red(self) -> None:
        """One name, two contents: red by name, never overwritten."""
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            for index in range(2):
                (base / "rows" / f"r{index}" / "go").mkdir(parents=True)
                (base / "rows" / f"r{index}" / "go" / "covmeta.x").write_text(
                    str(index), "utf-8"
                )
            got = coverage_merge.merge(sorted((base / "rows").iterdir()), base / "run")
            want: list[str] = ["covmeta.x: two rows wrote it with different contents"]
            assert want == got

    def test_two_rows_plpgsql_reports_reach_one_directory(self) -> None:
        """Each row's reports, both shapes, side by side for #2163's merge."""
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            (base / "rows" / "r0" / "plpgsql-coverage").mkdir(parents=True)
            (base / "rows" / "r0" / "plpgsql-coverage" / "db-a.json").write_text(
                "{}", "utf-8"
            )
            (base / "rows" / "r1").mkdir(parents=True)
            (base / "rows" / "r1" / "plpgsql-coverage.json").write_text("[]", "utf-8")
            run = base / "run"
            none: list[str] = []
            got = coverage_merge.merge(sorted((base / "rows").iterdir()), run)
            assert none == got
            files: list[str] = sorted(
                p.name for p in (run / "plpgsql-coverage").iterdir()
            )
            want: list[str] = ["db-a.json", "r1.json"]
            assert want == files
            assert not (run / "plpgsql-coverage.json").exists()

    def test_one_single_report_is_kept_as_the_file(self) -> None:
        """One row wrote the single report: kept as it is, and in the directory."""
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            (base / "rows" / "r0").mkdir(parents=True)
            (base / "rows" / "r0" / "plpgsql-coverage.json").write_text("{}", "utf-8")
            (base / "rows" / "r1").mkdir(parents=True)
            run = base / "run"
            none: list[str] = []
            assert none == coverage_merge.merge(sorted((base / "rows").iterdir()), run)
            assert (run / "plpgsql-coverage.json").read_text("utf-8") == "{}"
            assert (run / "plpgsql-coverage" / "r0.json").is_file()

    def test_the_command_line(self) -> None:
        """Two directories, or the usage exit."""
        assert coverage_merge.main([]) == 2
        with tempfile.TemporaryDirectory() as scratch:
            assert coverage_merge.main([scratch, f"{scratch}/run"]) == 0

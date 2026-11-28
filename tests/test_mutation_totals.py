"""Table tests for mise/mutation-totals.sh, the one answer across shards.

Each language's shard matrix in repo-mutants.yml uploads each shard's
counts as its own artifact, so that language's totals job sees one
subdirectory per shard holding `<language>.tsv`, in the shape
mise/mutants-rust.sh writes, and calls the helper with its language.
Each row plants that tree and asserts the exact lines and exit status.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests import support

BELT = support.MISE
SCRIPT = BELT / "mutation-totals.sh"
BASH = support.tool("bash")
LABEL = "audit:mutants: rust"


@pytest.mark.usefixtures("_set_up_test_totals")
class TestTotals:
    """Plant per-shard summaries and total them."""

    @pytest.fixture
    def _set_up_test_totals(self, tmp_path: Path) -> None:
        """Make an empty download directory."""
        self.dir = tmp_path

    def shard(self, k: int, row: str, lang: str = "rust") -> None:
        """Plant one shard's artifact the way download-artifact lays it out."""
        sub = self.dir / f"mutants-summary-{k}"
        sub.mkdir(exist_ok=True)
        (sub / f"{lang}.tsv").write_text(row + "\n", encoding="utf-8")

    def run_totals(self, lang: str = "rust") -> subprocess.CompletedProcess[str]:
        """Run the helper over the planted directory.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, str(SCRIPT), str(self.dir), lang],
            cwd=self.dir.parent,
            env=support.env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_four_clean_shards_one_empty(self) -> None:
        """Counts summed, excluded taken once, an empty shard counts as zero."""
        self.shard(0, "0/4\t3\t3\t0\t2\twhole")
        self.shard(1, "1/4\t3\t3\t0\t2\twhole")
        self.shard(2, "2/4\t3\t3\t0\t2\twhole")
        self.shard(3, "3/4\t0\t0\t0\t2\twhole")
        done = self.run_totals()
        assert done.returncode == 0, done.stderr
        want = [
            f"{LABEL}: across 4 shards: 9 mutants, 9 killed, 0 survived, 2 excluded"
        ]
        assert done.stdout.splitlines() == want

    def test_a_survivor_in_one_shard_reds_the_total(self) -> None:
        """The measure is one answer: any survivor anywhere is red."""
        for k in range(3):
            self.shard(k, f"{k}/4\t3\t3\t0\t0\twhole")
        self.shard(3, "3/4\t3\t2\t1\t0\twhole")
        done = self.run_totals()
        assert done.returncode == 1
        want = [
            f"{LABEL}: across 4 shards: 12 mutants, 11 killed, 1 survived, 0 excluded"
        ]
        assert done.stdout.splitlines() == want
        want = [f"{LABEL}: red — the survivors are named in their shards' logs"]
        assert done.stderr.splitlines() == want

    def test_missing_shard_is_red_by_name(self) -> None:
        """A shard that died writes nothing; its absence is named."""
        for k in (0, 1, 3):
            self.shard(k, f"{k}/4\t3\t3\t0\t0\twhole")
        done = self.run_totals()
        assert done.returncode == 1
        want = [f"{LABEL}: shard 2/4 reported nothing — it died or judged nothing"]
        assert done.stderr.splitlines() == want

    def test_unadopted_everywhere_is_skipped(self) -> None:
        """Every shard skipped: one skip line, green."""
        for k in range(4):
            self.shard(k, f"{k}/4\tskipped")
        done = self.run_totals()
        assert done.returncode == 0, done.stderr
        want = [f"{LABEL}: not adopted, skipped in all 4 shards"]
        assert done.stdout.splitlines() == want

    def test_whole_shards_disagreeing_about_excluded_is_red(self) -> None:
        """A `whole` row reports the population's figure; the rows must agree."""
        self.shard(0, "0/2\t3\t3\t0\t1\twhole")
        self.shard(1, "1/2\t3\t3\t0\t2\twhole")
        done = self.run_totals()
        assert done.returncode == 1
        want = [f"{LABEL}: shards disagree about excluded (1, 2)"]
        assert done.stderr.splitlines() == want

    def test_shard_rows_sum_their_excluded_counts(self) -> None:
        """A `shard` row counts its own files' exclusions: summed (#2153)."""
        self.shard(0, "0/3\t4\t3\t0\t1\tshard", lang="typescript")
        self.shard(1, "1/3\t2\t2\t0\t2\tshard", lang="typescript")
        self.shard(2, "2/3\t0\t0\t0\t0\tshard", lang="typescript")
        done = self.run_totals("typescript")
        assert done.returncode == 0, done.stderr
        want = [
            (
                "audit:mutants: typescript: across 3 shards:"
                " 6 mutants, 5 killed, 0 survived, 3 excluded"
            )
        ]
        assert done.stdout.splitlines() == want

    def test_whole_and_shard_rows_mixed_is_red(self) -> None:
        """Rows that disagree about what excluded covers cannot be totalled."""
        self.shard(0, "0/2\t3\t3\t0\t1\twhole")
        self.shard(1, "1/2\t3\t3\t0\t1\tshard")
        done = self.run_totals()
        assert done.returncode == 1
        want = [f"{LABEL}: shards disagree about what excluded covers (whole, shard)"]
        assert done.stderr.splitlines() == want

    def test_a_row_with_no_coverage_column_is_red(self) -> None:
        """A five-column row says nothing about its excluded count: red."""
        self.shard(0, "0/1\t3\t3\t0\t1")
        done = self.run_totals()
        assert done.returncode == 1
        want = [
            (
                f'{LABEL}: shard 0/1 says its excluded count covers "",'
                " not whole or shard"
            )
        ]
        assert done.stderr.splitlines() == want

    def test_no_mutant_in_any_shard_is_red(self) -> None:
        """Each shard may hold none; the whole population may not."""
        self.shard(0, "0/2\t0\t0\t0\t0\tshard", lang="typescript")
        self.shard(1, "1/2\t0\t0\t0\t0\tshard", lang="typescript")
        done = self.run_totals("typescript")
        assert done.returncode == 1
        want = [
            (
                "audit:mutants: typescript: no shard mutated anything"
                " — across 2 shards, 0 mutants"
            )
        ]
        assert done.stdout == ""
        assert done.stderr.splitlines() == want

    def test_no_summary_at_all_is_red(self) -> None:
        """Nothing downloaded: every shard died, never a clean zero."""
        done = self.run_totals()
        assert done.returncode == 1
        want = [
            (
                f"{LABEL}: no shard reported a summary"
                " — every shard died before its last line"
            )
        ]
        assert done.stderr.splitlines() == want

    def test_missing_directory_is_named_not_mute(self) -> None:
        """No artifact means no directory: the same named red, not a crash."""
        shutil.rmtree(self.dir)
        done = self.run_totals()
        assert done.returncode == 1
        want = [
            (
                f"{LABEL}: no shard reported a summary"
                " — every shard died before its last line"
            )
        ]
        assert done.stderr.splitlines() == want

    def test_only_the_named_language_is_totalled(self) -> None:
        """One language per call: another leg's files are not read."""
        for k in range(2):
            self.shard(k, f"{k}/2\t2\t2\t0\t0\twhole")
            self.shard(k, f"{k}/2\t5\t1\t4\t0\twhole", lang="go")
        done = self.run_totals()
        assert done.returncode == 0, done.stderr
        want = [
            (f"{LABEL}: across 2 shards: 4 mutants, 4 killed, 0 survived, 0 excluded"),
        ]
        assert done.stdout.splitlines() == want
        other = self.run_totals("go")
        assert other.returncode == 1
        want = [
            (
                "audit:mutants: go: across 2 shards:"
                " 10 mutants, 2 killed, 8 survived, 0 excluded"
            ),
        ]
        assert other.stdout.splitlines() == want

    def test_language_is_required(self) -> None:
        """No language named: refused, never a total of everything."""
        done = subprocess.run(
            [BASH, str(SCRIPT), str(self.dir)],
            cwd=self.dir.parent,
            env=support.env(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode != 0
        assert "<language>" in done.stderr

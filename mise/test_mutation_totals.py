#!/usr/bin/env python3
"""Table tests for mise/mutation-totals.sh, the one answer across shards.

repo-audit.yml's `mutants` matrix uploads each shard's counts as its own
artifact, so the totals job sees one subdirectory per shard holding one
`<language>.tsv` per leg, in the shape mise/mutants-rust.sh writes. Each
row plants that tree and asserts the exact lines and exit status.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutation-totals.sh"
BASH = shutil.which("bash") or "bash"
LABEL = "audit:mutants: rust"


class Totals(unittest.TestCase):
    """Plant per-shard summaries and total them."""

    def setUp(self) -> None:
        """Make an empty download directory."""
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def shard(self, k: int, row: str, lang: str = "rust") -> None:
        """Plant one shard's artifact the way download-artifact lays it out."""
        sub = self.dir / f"mutants-summary-{k}"
        sub.mkdir(exist_ok=True)
        (sub / f"{lang}.tsv").write_text(row + "\n", encoding="utf-8")

    def run_totals(self) -> subprocess.CompletedProcess:
        """Run the helper over the planted directory.

        Returns:
            The finished process.

        """
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT), str(self.dir)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_four_clean_shards_one_empty(self) -> None:
        """Counts summed, excluded taken once, an empty shard counts as zero."""
        self.shard(0, "0/4\t3\t3\t0\t2")
        self.shard(1, "1/4\t3\t3\t0\t2")
        self.shard(2, "2/4\t3\t3\t0\t2")
        self.shard(3, "3/4\t0\t0\t0\t2")
        done = self.run_totals()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [f"{LABEL}: across 4 shards: 9 mutants, 9 killed, 0 survived, 2 excluded"],
        )

    def test_a_survivor_in_one_shard_reds_the_total(self) -> None:
        """The measure is one answer: any survivor anywhere is red."""
        for k in range(3):
            self.shard(k, f"{k}/4\t3\t3\t0\t0")
        self.shard(3, "3/4\t3\t2\t1\t0")
        done = self.run_totals()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                (
                    f"{LABEL}: across 4 shards:"
                    " 12 mutants, 11 killed, 1 survived, 0 excluded"
                )
            ],
        )
        self.assertEqual(
            done.stderr.splitlines(),
            [f"{LABEL}: red — the survivors are named in their shards' logs"],
        )

    def test_missing_shard_is_red_by_name(self) -> None:
        """A shard that died writes nothing; its absence is named."""
        for k in (0, 1, 3):
            self.shard(k, f"{k}/4\t3\t3\t0\t0")
        done = self.run_totals()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [f"{LABEL}: shard 2/4 reported nothing — it died or judged nothing"],
        )

    def test_unadopted_everywhere_is_skipped(self) -> None:
        """Every shard skipped: one skip line, green."""
        for k in range(4):
            self.shard(k, f"{k}/4\tskipped")
        done = self.run_totals()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(), [f"{LABEL}: not adopted, skipped in all 4 shards"]
        )

    def test_shards_disagreeing_about_excluded_is_red(self) -> None:
        """Each shard reports the whole population's figure; it must agree."""
        self.shard(0, "0/2\t3\t3\t0\t1")
        self.shard(1, "1/2\t3\t3\t0\t2")
        done = self.run_totals()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [f"{LABEL}: shards disagree about excluded (1, 2)"],
        )

    def test_no_summary_at_all_is_red(self) -> None:
        """Nothing downloaded: every shard died, never a clean zero."""
        done = self.run_totals()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [
                (
                    "audit:mutants: no shard reported a summary"
                    " — every shard died before its last line"
                )
            ],
        )

    def test_each_language_is_totalled_on_its_own(self) -> None:
        """A second leg's file is totalled beside the first, by name."""
        for k in range(2):
            self.shard(k, f"{k}/2\t2\t2\t0\t0")
            self.shard(k, f"{k}/2\tskipped", lang="go")
        done = self.run_totals()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                "audit:mutants: go: not adopted, skipped in all 2 shards",
                (
                    f"{LABEL}: across 2 shards:"
                    " 4 mutants, 4 killed, 0 survived, 0 excluded"
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()

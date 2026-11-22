#!/usr/bin/env python3
"""Table tests for lint:fuzz-build's probe, manifest guard and corpus (#1290, #1889).

The body under test is the task's own `run` string from
mise/config.toml, run by bash in a planted `fuzz/` crate. `cargo` and
`cargo-fuzz` are stubs (the canon pins no Rust): the stub answers
`metadata` with what the row plants and logs every call, which each
row asserts, so a row cannot pass by stopping somewhere else.

The cargo texts are measured, never written by hand. The stale-lock
refusal was produced in a scratch crate whose parent version moved
under its fuzz lock, under cargo 1.82.0, 1.90.0 and 1.97.1: the first
two print one dialect, 1.97.1 the other, and both rows run. The
resolution failure is the one #1290 reported, reproduced by nesting a
crate with no `[workspace]` table under another workspace (cargo
1.97.1). cargo-fuzz 0.13.2's `init` was measured too: it writes no
`[workspace]` table, so the remedy printed is the empty table cargo's
own error recommends, which edtf and release-lab carry.

The corpus rows (#1889) plant a git repository, because the task reads
the committed corpus through `git ls-files`: a target with no tracked
file under fuzz/corpus/<target>/ is red by name, an untracked corpus is
no corpus, and the tracked seeds are copied to a scratch directory and
replayed once each (`cargo fuzz run -s none <target> <scratch> --
-runs=0`). The stub logs the directory it was given and the seeds in it,
so a row proves the replay never reads the tree's own directory and
never sees an untracked file. A seed containing CRASH makes the stub
exit as libFuzzer does on a crashing input. That half lives in
mise/fuzz-corpus.sh, which the body calls. Mutation-checked against it:
the replay handed fuzz/corpus/<target> reds 3 rows, a failed replay
ignored reds 2, the listing read with `find` in place of `git ls-files`
reds 3, and a missing corpus ignored reds 3. A crashing seed's artifact
goes to the scratch directory too (`-artifact_prefix=`, after cargo-fuzz's
own, so it wins), never into fuzz/artifacts/.

Mutation-checked, one mutant at a time against the task body: every
failure called stale reds 2 rows, none called stale reds 2, matching
only 1.97's dialect reds 1, cargo's text dropped from the stale branch
reds 2 and from the other branch 2, the table guard removed reds 1, the
guard as a grep for a bare `[workspace]` line in place of the TOML read
reds 2, a parse error read as a missing table reds 1, and the probe's
stderr discarded again (the defect) reds 4.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

import decoded
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")

# Stands in for cargo. `metadata` succeeds when no refusal is planted and
# otherwise prints the planted text on stderr and exits 101, as cargo
# does; `fuzz build` and `fuzz list` answer as a two-target crate would.
# `fuzz run` logs the seeds in the directory it was given (relative, one
# line) and exits 77 with a libFuzzer-shaped report when one holds CRASH.
# Anything else is red, so a row that reaches for an unexpected command
# fails loudly.
CARGO_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${PLANT_LOG}"
case "$1 ${2:-}" in
  "metadata --locked")
    if [[ -f ${PLANT_REFUSAL} ]]; then
      cat "${PLANT_REFUSAL}" >&2
      exit 101
    fi
    echo '{}'
    ;;
  "fuzz build") ;;
  "fuzz list") printf 'parse\\nround_trip\\n' ;;
  "fuzz run")
    seeds=$(cd "$6" && find . -type f | LC_ALL=C sort | tr '\\n' ' ')
    printf 'seeds:%s\\n' "${seeds}" >> "${PLANT_LOG}"
    if grep -rqs CRASH "$6"; then
      echo "==1== ERROR: libFuzzer: deadly signal" >&2
      echo "SUMMARY: libFuzzer: deadly signal" >&2
      exit 77
    fi
    echo "INFO: seed corpus: files: 1"
    ;;
  *) exit 99 ;;
esac
"""
CARGO_FUZZ_STUB = "#!/usr/bin/env bash\nexit 99\n"

PROBE = "metadata --locked --manifest-path fuzz/Cargo.toml --format-version 1"
BUILT = [PROBE, "fuzz build -s none", "fuzz list"]

MANIFEST = """\
[package]
name = "probe-fuzz"
version = "0.0.0"
publish = false
edition = "2024"

[package.metadata]
cargo-fuzz = true

[dependencies]
libfuzzer-sys = "0.4"

[[bin]]
name = "parse"
path = "fuzz_targets/parse.rs"

[[bin]]
name = "round_trip"
path = "fuzz_targets/round_trip.rs"
"""
WORKSPACE = "\n[workspace]\n"

# cargo 1.82.0 and 1.90.0, byte for byte but the path.
STALE_OLD = (
    "error: the lock file {lock} needs to be updated but --locked was "
    "passed to prevent this\n"
    "If you want to try to generate the lock file without accessing the "
    "network, remove the --locked flag and use --offline instead.\n"
)
# cargo 1.97.1, byte for byte but the path.
STALE_NEW = (
    "error: cannot update the lock file {lock} because --locked was passed "
    "to prevent this\n"
    "help: to generate the lock file without accessing the network, remove "
    "the --locked flag and use --offline instead.\n"
)
# cargo 1.97.1 in a nested worktree, byte for byte but the paths.
NESTED = (
    "error: current package believes it's in a workspace when it's not:\n"
    "current:   {root}/fuzz/Cargo.toml\n"
    "workspace: /outer/Cargo.toml\n"
    "\n"
    "this may be fixable by adding `inner/fuzz` to the `workspace.members` "
    "array of the manifest located at: /outer/Cargo.toml\n"
    "Alternatively, to keep it out of the workspace, add the package to the "
    "`workspace.exclude` array, or add an empty `[workspace]` table to the "
    "package's manifest.\n"
)

STALE_HEAD = (
    "lint:fuzz-build: fuzz/Cargo.lock is stale — it would be rewritten by the build\n"
)
STALE_REMEDY = (
    "  fuzz/ is its own workspace: its lock names the parent crate's "
    "version and\n"
    "  the release script refreshes it (release/prepare-release.sh)\n"
    "  fix locally with: cargo update --workspace --manifest-path "
    "fuzz/Cargo.toml\n"
)
NO_WORKSPACE = (
    "lint:fuzz-build: fuzz/Cargo.toml declares no [workspace] table — its "
    "resolution depends on where the checkout sits\n"
    "  a workspace above the checkout (a nested worktree) claims the crate; "
    "make it its own root\n"
    "  with the empty table cargo itself recommends, in fuzz/Cargo.toml:\n"
    "    [workspace]\n"
)
UNRESOLVED_HEAD = "lint:fuzz-build: the fuzz manifest does not resolve — cargo says:\n"
NO_CORPUS = (
    "lint:fuzz-build: fuzz target '{target}' has no committed corpus "
    "(no tracked file under fuzz/corpus/{target}/)\n"
)
NO_CORPUS_REMEDY = (
    "  commit seed inputs under fuzz/corpus/<target>/ — the gate replays "
    "them on every pull request\n"
    "  and make sure fuzz/.gitignore does not ignore corpus/\n"
)
CRASHED = (
    "lint:fuzz-build: fuzz target '{target}' fails on its committed corpus "
    "— libFuzzer says:\n"
)
# libFuzzer's own report, passed through after the finding (#1934).
LIBFUZZER_SAYS = (
    "==1== ERROR: libFuzzer: deadly signal\nSUMMARY: libFuzzer: deadly signal\n"
)
CRASHED_REMEDY = (
    "  every tracked file under fuzz/corpus/<target>/ is replayed once; "
    "fix the target or the seed\n"
)


def task_body(name: str) -> str:
    """Read a task's run string out of the belt as mise would render it.

    Returns:
        The body with any `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    run = decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


# What a row expects of the cargo log, named with its element type.
NO_CALLS: list[str] = []
ONE_PROBE: list[str] = [PROBE]
# What a replay is given, before and after its target and directory.
RUN_HEAD: list[str] = ["fuzz", "run", "-s", "none"]
RUN_TAIL: list[str] = ["--", "-runs=0"]
ARTIFACT_FLAG = "-artifact_prefix="
# (target, the seeds its replay saw), per replay in order.
BOTH_SEEDED: list[tuple[str, str]] = [("parse", "./a "), ("round_trip", "./b ")]
NESTED_SEEDED: list[tuple[str, str]] = [
    ("parse", "./a "),
    ("round_trip", "./nested/b "),
]


class FuzzBuild(unittest.TestCase):
    """lint:fuzz-build says "stale" only when cargo says the lock is."""

    @override
    def setUp(self) -> None:
        """Plant a fuzz crate with a committed lock, and the stubs."""
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "fuzz").mkdir()
        (self.root / "fuzz" / "Cargo.lock").write_text("", encoding="utf-8")
        self.stubs = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.stubs, ignore_errors=True)
        for name, text in (("cargo", CARGO_STUB), ("cargo-fuzz", CARGO_FUZZ_STUB)):
            stub = self.stubs / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.log = self.stubs / "calls.log"
        self.refusal = self.stubs / "refusal.txt"
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, under the fixture environment."""
        subprocess.run(
            [GIT, *args],
            cwd=self.root,
            env=hermetic.env(),
            capture_output=True,
            check=True,
        )

    def seed(self, target: str, name: str, text: str, *, track: bool = True) -> None:
        """Write fuzz/corpus/<target>/<name>, and track it unless told not to."""
        path = self.root / "fuzz" / "corpus" / target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if track:
            self.git("add", "--", str(path.relative_to(self.root)))

    def manifest(self, text: str) -> None:
        """Write fuzz/Cargo.toml."""
        (self.root / "fuzz" / "Cargo.toml").write_text(text, encoding="utf-8")

    def refuse(self, text: str) -> None:
        """Make the probe fail with cargo's `text` on stderr."""
        self.refusal.write_text(text, encoding="utf-8")

    def run_lint(self) -> tuple[int, str, str, list[str]]:
        """Run the task body in the planted crate.

        Returns:
            Exit status, stdout, stderr, and the cargo calls made.

        """
        if self.log.exists():
            self.log.unlink()
        env = hermetic.env() | {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ['PATH']}",
            "PLANT_LOG": str(self.log),
            "PLANT_REFUSAL": str(self.refusal),
            "ORG_BELT_DIR": str(BELT),
        }
        as_mise = hermetic.task_run("lint:fuzz-build", task_body("lint:fuzz-build"))
        done = subprocess.run(
            as_mise.argv,
            cwd=self.root,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = (
            self.log.read_text(encoding="utf-8").splitlines()
            if self.log.exists()
            else []
        )
        return done.returncode, done.stdout, done.stderr, calls

    def replays(self, calls: list[str]) -> list[tuple[str, Path, str]]:
        """Split the calls after the build into (target, directory, seeds).

        Returns:
            One triple per `fuzz run`, in order.

        """
        self.assertEqual(calls[: len(BUILT)], BUILT)
        rest: list[str] = calls[len(BUILT) :]
        self.assertEqual(len(rest) % 2, 0, rest)
        runs: list[str] = rest[::2]
        listed: list[str] = rest[1::2]
        out: list[tuple[str, Path, str]] = []
        for run, seeds in zip(runs, listed, strict=True):
            words = run.split(" ")
            self.assertEqual(words[:4], RUN_HEAD, run)
            self.assertEqual(words[6:8], RUN_TAIL, run)
            self.assertEqual(len(words), 9, run)
            self.assertTrue(words[8].startswith(ARTIFACT_FLAG), run)
            self.assertTrue(seeds.startswith("seeds:"), seeds)
            out.append((words[4], Path(words[5]), seeds.removeprefix("seeds:")))
        return out

    def seeded(self, calls: list[str]) -> list[tuple[str, str]]:
        """Return (target, seeds seen) per replay.

        Returns:
            The pairs, in replay order.

        """
        return [(target, seeds) for target, _, seeds in self.replays(calls)]

    def test_a_current_lock_builds(self) -> None:
        """The probe passes, the build runs, the corpus replays, all reported."""
        self.seed("parse", "a", "x")
        self.seed("round_trip", "b", "y")
        for table in (
            "[workspace]\n",
            '[workspace]\nmembers = ["."]\n',
            "[workspace.metadata]\nx = 1\n",
        ):
            with self.subTest(table=table):
                self.manifest(MANIFEST + "\n" + table)
                status, out, err, calls = self.run_lint()
                self.assertEqual((status, err), (0, ""))
                self.assertEqual(
                    out,
                    "lint:fuzz-build: 2 target(s) build, lockfile current, "
                    "2 committed seed(s) replay clean\n",
                )
                self.assertEqual(self.seeded(calls), BOTH_SEEDED)

    def test_a_target_with_no_tracked_corpus_is_red(self) -> None:
        """No tracked file under fuzz/corpus/<target>/ reds, naming it."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "x")
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, NO_CORPUS.format(target="round_trip") + NO_CORPUS_REMEDY)
        self.assertEqual(calls, BUILT)

    def test_an_untracked_corpus_is_no_corpus(self) -> None:
        """A corpus on disk that git does not track is red: tracked files only."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "x")
        self.seed("round_trip", "b", "y", track=False)
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, NO_CORPUS.format(target="round_trip") + NO_CORPUS_REMEDY)
        self.assertEqual(calls, BUILT)

    def test_one_tracked_seed_per_target_passes(self) -> None:
        """One seed is a corpus; an untracked sibling is never replayed."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "x")
        self.seed("parse", "local-find", "CRASH", track=False)
        self.seed("round_trip", "nested/b", "y")
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(
            out,
            "lint:fuzz-build: 2 target(s) build, lockfile current, "
            "2 committed seed(s) replay clean\n",
        )
        self.assertEqual(self.seeded(calls), NESTED_SEEDED)

    def test_a_crashing_seed_is_red(self) -> None:
        """A tracked seed the target fails on reds, naming the target."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "CRASH")
        self.seed("round_trip", "b", "y")
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(
            err, CRASHED.format(target="parse") + CRASHED_REMEDY + LIBFUZZER_SAYS
        )
        self.assertEqual(self.seeded(calls), BOTH_SEEDED)

    def test_the_replay_reads_a_directory_outside_the_tree(self) -> None:
        """The replay never gets fuzz/corpus, and its scratch copy is removed."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "x")
        self.seed("round_trip", "b", "y")
        status, _, err, calls = self.run_lint()
        self.assertEqual((status, err), (0, ""))
        replays = self.replays(calls)
        self.assertEqual(len(replays), 2)
        for line in calls:
            self.assertNotIn("fuzz/corpus", line)
        for _, directory, _ in replays:
            self.assertTrue(directory.is_absolute(), directory)
            self.assertFalse(directory.resolve().is_relative_to(self.root), directory)
            self.assertFalse(directory.exists(), directory)

    def test_a_stale_lock_is_called_stale(self) -> None:
        """Only cargo's lockfile refusal earns the stale message."""
        lock = f"{self.root}/fuzz/Cargo.lock"
        for dialect in (STALE_OLD, STALE_NEW):
            with self.subTest(dialect=dialect[:30]):
                self.manifest(MANIFEST + WORKSPACE)
                text = dialect.format(lock=lock)
                self.refuse(text)
                status, out, err, calls = self.run_lint()
                self.assertEqual((status, out), (1, ""))
                self.assertEqual(err, STALE_HEAD + STALE_REMEDY + text)
                self.assertEqual(calls, ONE_PROBE)

    def test_no_workspace_table_reds_before_the_probe(self) -> None:
        """Resolution must not depend on where the checkout sits."""
        self.manifest(MANIFEST)
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out, err), (1, "", NO_WORKSPACE))
        self.assertEqual(calls, NO_CALLS)

    def test_another_probe_failure_passes_cargo_through(self) -> None:
        """Cargo's own error, with no lockfile remedy that cannot help."""
        self.manifest(MANIFEST + WORKSPACE)
        text = NESTED.format(root=self.root)
        self.refuse(text)
        status, out, err, calls = self.run_lint()
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, UNRESOLVED_HEAD + text)
        self.assertNotIn("stale", err)
        self.assertNotIn("cargo update", err)
        self.assertEqual(calls, ONE_PROBE)

    def test_an_unparsable_manifest_is_left_to_cargo(self) -> None:
        """The table check never claims a parse error as a missing table."""
        self.manifest(MANIFEST + "[workspace\n")
        text = "error: failed to parse manifest at `fuzz/Cargo.toml`\n"
        self.refuse(text)
        status, _, err, calls = self.run_lint()
        self.assertEqual(status, 1)
        self.assertEqual(err, UNRESOLVED_HEAD + text)
        self.assertEqual(calls, ONE_PROBE)

    def test_a_crash_artifact_lands_in_the_scratch_directory(self) -> None:
        """LibFuzzer's -artifact_prefix names the scratch copy, never the tree."""
        self.manifest(MANIFEST + WORKSPACE)
        self.seed("parse", "a", "CRASH")
        self.seed("round_trip", "b", "y")
        status, _, _, calls = self.run_lint()
        self.assertEqual(status, 1)
        replays = self.replays(calls)
        self.assertEqual(len(replays), 2)
        for line, (_, directory, _) in zip(
            [c for c in calls if c.startswith("fuzz run ")], replays, strict=True
        ):
            prefix = Path(line.split(" ")[8].removeprefix(ARTIFACT_FLAG))
            self.assertTrue(line.endswith("/"), line)
            self.assertEqual(prefix, directory.parent)
            self.assertFalse(prefix.resolve().is_relative_to(self.root), line)
            self.assertNotIn("fuzz/artifacts", line)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Table tests for the coverage floor's fuzz leg (#1896).

Every fuzz target is a leg of the floor: coverage-measure.sh's `fuzz`
leg replays each target's committed corpus once under coverage and
prints `fuzz-<name> <percent>`, and coverage_floor.py holds each to a
line of its own. The bodies under test are the real
`mise/coverage-measure.sh` and the `coverage:check` task's own `run`
string, read out of mise/config.toml, run by bash in a planted git tree.
Only the measuring tools are stubs (`cargo`, `cargo-fuzz`, `rustup`,
`rustc`, the toolchain's `llvm-cov`, `go`, `mise`), each answering with
a planted figure and logging what it was handed, so a row states the
measurement and asserts what the leg did with it.

The stand-in cargo's `fuzz coverage` writes into its log every entry of
the corpus directory it was given, `file <path> <bytes>` or `link
<path>`: a row proves the directory is outside the tree, holds the
tracked seeds as regular files and nothing untracked.

The real tools were probed once, end to end, on a scratch crate
(cargo-fuzz 0.13.2, rustc 1.97.1, its llvm-tools): a target linking a
twelve-line library, its corpus one tracked seed, one tracked link to a
file outside the tree and one untracked seed, gave `fuzz-parse-it
66.66666666666667` (8 of 12 lines) three runs running, a fuzz/coverage
the run made was removed and one that was there was kept.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import override

import coverage_floor as cf
import decoded
import gate_cache_key
import hermetic

BELT = Path(__file__).resolve().parent
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
MEASURE = str(BELT / "coverage-measure.sh")

# The host the stand-in rustc reports (hermetic.rustc_verbose).
HOST = "x86_64-unknown-linux-gnu"
# The directory digest of that rustc under the `stable` toolchain (#1877).
STABLE = gate_cache_key.directory_digest(hermetic.rustc_verbose("stable"))

# rustc: hermetic's stand-in, which also answers `--print sysroot`.
RUSTC = hermetic.RUSTC_STUB.replace(
    "#!/bin/sh\n",
    '#!/bin/sh\nif [ "$1" = --print ]; then echo "${STUB_SYSROOT}"; exit 0; fi\n',
    1,
)

RUSTUP = "#!/usr/bin/env bash\necho llvm-tools-stub\n"

# Present on PATH, never run: `cargo fuzz` goes through cargo.
CARGO_FUZZ = "#!/usr/bin/env bash\nexit 99\n"

# cargo: `metadata` names fuzz/target as the fuzz crate's target
# directory; `fuzz coverage -s none --target-dir <dir> <target> <corpus>`
# logs every entry of the corpus it was handed and writes the profile
# where cargo-fuzz 0.13.2 does, naming its target.
CARGO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/cargo.log"
case "$1 ${2:-}" in
  "metadata --no-deps")
    printf '{"target_directory": "%s/fuzz/target"}\\n' "${PWD}"
    ;;
  "fuzz coverage")
    target=$7 corpus=$8
    while IFS= read -r f; do
      if [[ -L ${corpus}/${f} ]]; then
        echo "  link ${f}"
      elif [[ -f ${corpus}/${f} ]]; then
        echo "  file ${f} $(< "${corpus}/${f}")"
      fi
    done < <(cd "${corpus}" && find . -mindepth 1 | LC_ALL=C sort) \\
      >> "${STUB_DIR}/cargo.log"
    mkdir -p "fuzz/coverage/${target}"
    echo "${target}" > "fuzz/coverage/${target}/coverage.profdata"
    ;;
  *) exit 1 ;;
esac
"""

# llvm-cov: logs its arguments; `export -summary-only` answers the
# planted `target:count:covered` of STUB_LINES for the profile's target.
LLVM_COV = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/llvm-cov.log"
profile=""
for arg in "$@"; do
  case "${arg}" in -instr-profile=*) profile=${arg#*=} ;; *) ;; esac
done
target=$(< "${profile}")
for record in ${STUB_LINES}; do
  IFS=: read -r name count covered <<< "${record}"
  [[ ${name} == "${target}" ]] || continue
  printf '{"data": [{"totals": {"lines": {"count": %s, "covered": %s}}}]}\\n' \\
    "${count}" "${covered}"
  exit 0
done
exit 1
"""

# go: `list -deps` the planted packages (a blank line among them, as the
# template prints for a package outside the module); `test` writes the
# profile; `tool cover` the planted total, the fuzz leg's for a profile
# it named.
GO = """\
#!/usr/bin/env bash
echo "$*" >> "${STUB_DIR}/go.log"
echo "${PWD} $*" >> "${STUB_DIR}/go-dir.log"
out=""
for arg in "$@"; do
  case "${arg}" in -coverprofile=* | -func=*) out=${arg#*=} ;; *) ;; esac
done
case "$1 ${2:-}" in
  "list -deps")
    printf 'example.com/x/internal/policy\\n\\nexample.com/x/internal/lib\\n'
    ;;
  "test "*) [[ -z ${out} ]] || echo "mode: set" > "${out}" ;;
  "tool cover")
    pct=${STUB_GO}
    [[ $(basename "${out}") != fuzz-* ]] || pct=${STUB_GO_FUZZ}
    printf 'total:\\t(statements)\\t%s%%\\n' "${pct}"
    ;;
  *) exit 1 ;;
esac
"""

# mise: no `test` task, so the go leg measures `go test ./...` itself.
MISE = "#!/usr/bin/env bash\nexit 1\n"


def manifest(*targets: str) -> str:
    """Render a fuzz/Cargo.toml declaring `targets` as [[bin]]s.

    Returns:
        The manifest.

    """
    bins = "".join(
        f'\n[[bin]]\nname = "{t}"\npath = "fuzz_targets/{t}.rs"\n' for t in targets
    )
    return f'[package]\nname = "probe-fuzz"\nversion = "0.0.0"\n\n[workspace]\n{bins}'


GO_TEST = """\
package policy

import "testing"

func FuzzLoad(f *testing.F) {
\tf.Fuzz(func(t *testing.T, b []byte) {})
}

func FuzzyHelper(t *testing.T) {}
"""

# A floor the derived-state law accepts, recording the go leg.
FLOOR = """\
# band: 2
# measured: 82.0
# derived: v1.0.0
# legs: go
80.0
"""

LOAD = "fuzz-internal-policy-load"

# What a row expects when nothing was handed over, named with its type.
NO_LINES: list[str] = []
NO_REPLAYS: dict[str, tuple[list[str], list[str]]] = {}


def task_body(name: str) -> str:
    """Read a task's run string out of the belt.

    Returns:
        The body.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    return decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")


class FuzzCase(unittest.TestCase):
    """A planted repository and the stand-in tools, one per row."""

    @override
    def setUp(self) -> None:
        """Plant an empty repository and the stubs beside it."""
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        stubs = self.tmp / "stubs"
        sysroot = self.tmp / "sysroot"
        tools = sysroot / "lib" / "rustlib" / HOST / "bin"
        tools.mkdir(parents=True)
        for where, name, text in (
            (stubs, "cargo", CARGO),
            (stubs, "cargo-fuzz", CARGO_FUZZ),
            (stubs, "rustup", RUSTUP),
            (stubs, "rustc", RUSTC),
            (stubs, "go", GO),
            (stubs, "mise", MISE),
            (tools, "llvm-cov", LLVM_COV),
        ):
            where.mkdir(parents=True, exist_ok=True)
            (where / name).write_text(text, encoding="utf-8")
            (where / name).chmod(0o755)
        env = hermetic.env()
        self.env = env | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_PYTHON": sys.executable,
            "ORG_COVERAGE_REPORTS": str(self.tmp / "reports"),
            "RUSTUP_TOOLCHAIN": "stable",
            "PATH": f"{stubs}{os.pathsep}{env.get('PATH', '')}",
            "STUB_DIR": str(self.tmp),
            "STUB_SYSROOT": str(sysroot),
            "STUB_LINES": "parse_marker:11:11 round_trip:20:15",
            "STUB_GO": "93.0",
            "STUB_GO_FUZZ": "43.6",
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree."""
        subprocess.run(
            [GIT, *args], cwd=self.tree, env=self.env, capture_output=True, check=True
        )

    def write(self, name: str, text: str, *, track: bool = True) -> Path:
        """Write a file into the tree, and track it unless told not to.

        Returns:
            Its path.

        """
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if track:
            self.git("add", "--", name)
        return path

    def rust(self, *targets: str) -> None:
        """Plant a fuzz crate with `targets`, one tracked seed each."""
        self.write("Cargo.toml", "[workspace]\n")
        self.write("src/lib.rs", "pub fn f() {}\n")
        self.write("fuzz/Cargo.toml", manifest(*targets))
        for target in targets:
            self.write(f"fuzz/fuzz_targets/{target}.rs", "#![no_main]\n")
            self.write(f"fuzz/corpus/{target}/seed", f"{target}-seed")

    def go(self) -> None:
        """Plant a Go module with one fuzz target, FuzzLoad."""
        self.write("go.mod", "module example.com/x\n")
        self.write("internal/policy/load_test.go", GO_TEST)

    def measure(self, selector: str = "fuzz") -> subprocess.CompletedProcess[str]:
        """Run coverage-measure.sh with `selector` in the planted tree.

        Returns:
            The finished process.

        """
        return subprocess.run(
            [BASH, MEASURE, "coverage:check", selector],
            cwd=self.tree,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def check(self) -> subprocess.CompletedProcess[str]:
        """Run coverage:check's own body in the planted tree.

        Returns:
            The finished process.

        """
        as_mise = hermetic.task_run("coverage:check", task_body("coverage:check"))
        return subprocess.run(
            as_mise.argv,
            cwd=self.tree,
            env=self.env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def log(self, tool: str) -> list[str]:
        """Read what a stand-in tool was handed.

        Returns:
            Its log lines, none when it was never called.

        """
        path = self.tmp / f"{tool}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def replays(self) -> dict[str, tuple[list[str], list[str]]]:
        """Split cargo's log into each replay's arguments and corpus.

        Returns:
            Target to (the `fuzz coverage` arguments, the corpus entries).

        """
        out: dict[str, tuple[list[str], list[str]]] = {}
        current = ""
        for line in self.log("cargo"):
            if line.startswith("fuzz coverage "):
                words = line.split(" ")
                current = words[6]
                out[current] = (words, [])
            elif line.startswith("  ") and current:
                out[current][1].append(line.strip())
        return out


class MeasureRows(FuzzCase):
    """The `fuzz` leg: one line per target, its name derived."""

    def test_two_rust_targets_and_one_go_target_print_three_legs(self) -> None:
        """Rust `fuzz-<target>`, Go `fuzz-<dir>-<name>`, each its figure."""
        self.rust("parse_marker", "round_trip")
        self.go()
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "fuzz-parse-marker 100\nfuzz-round-trip 75\n"
            "fuzz-internal-policy-load 43.6\n",
            done.stdout,
        )
        # The Go target ran alone, over the module's packages it imports.
        ran = self.log("go")[1].split(" ")
        argv: list[str] = [
            "test",
            "-count=1",
            "-run",
            "^FuzzLoad$",
            "-coverpkg=example.com/x/internal/policy,example.com/x/internal/lib",
        ]
        self.assertEqual(argv, ran[:5])
        self.assertTrue(ran[5].endswith(f"/{LOAD}.out"), ran[5])
        self.assertEqual("./internal/policy", ran[6])
        # llvm-cov reads the tracked sources, never the harnesses.
        sources = self.log("llvm-cov")[0].split(" ")[4:]
        tracked: list[str] = [f"{self.tree}/src/lib.rs"]
        self.assertEqual(tracked, sources)

    def test_a_nested_module_target_is_measured_in_its_module(self) -> None:
        """tools/ is a module of its own: go runs there, on ./gen (#1899)."""
        self.write("tools/go.mod", "module example.com/x/tools\n")
        self.write("tools/gen/gen_test.go", GO_TEST.replace("policy", "gen"))
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("fuzz-tools-gen-load 43.6\n", done.stdout)
        module = f"{self.tree}/tools"
        calls = self.log("go-dir")
        want: list[str] = [
            f"{module} list -deps",
            f"{module} test -count=1",
            f"{module} tool cover",
        ]
        got: list[str] = [" ".join(c.split(" ")[:3]) for c in calls]
        self.assertEqual(want, got)
        ran = calls[1].split(" ")
        self.assertEqual(
            "-coverpkg=example.com/x/internal/policy,example.com/x/internal/lib",
            ran[5],
        )
        self.assertEqual("./gen", ran[-1])

    def test_no_environment_variable_narrows_or_redirects_the_leg(self) -> None:
        """The search's measurement is a function, not a knob on the gate (#1897).

        A repository's mise.toml [env] reaches this script in the gate, so
        a variable that named the targets or the corpus would be a seam in
        the floor. The names a first draft used change nothing.
        """
        self.rust("parse_marker", "round_trip")
        self.go()
        plain = self.measure()
        self.env |= {
            "ORG_FUZZ_ONLY": "rust - round_trip",
            "ORG_FUZZ_CORPUS": str(self.tmp / "elsewhere"),
        }
        knobbed = self.measure()
        self.assertEqual(0, knobbed.returncode, knobbed.stderr)
        self.assertEqual(plain.stdout, knobbed.stdout)
        self.assertEqual(
            "fuzz-parse-marker 100\nfuzz-round-trip 75\n"
            "fuzz-internal-policy-load 43.6\n",
            knobbed.stdout,
        )
        replayed = [
            line for line in self.log("cargo") if line.startswith("fuzz coverage ")
        ]
        self.assertFalse(any("elsewhere" in line for line in replayed), replayed)

    def test_the_build_directory_carries_the_compiler_digest(self) -> None:
        """`--target-dir` is named for the rustc that builds (#1877)."""
        self.rust("parse_marker")
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        build = f"{self.tree}/fuzz/target/coverage-{STABLE}"
        words, _ = self.replays()["parse_marker"]
        replay: list[str] = [
            "fuzz",
            "coverage",
            "-s",
            "none",
            "--target-dir",
            build,
            "parse_marker",
        ]
        self.assertEqual(replay, words[:7])
        self.assertEqual(
            f"export -summary-only -instr-profile=fuzz/coverage/parse_marker/"
            f"coverage.profdata {build}/{HOST}/release/parse_marker",
            " ".join(self.log("llvm-cov")[0].split(" ")[:4]),
        )

    def test_two_targets_deriving_one_leg_are_red_naming_both(self) -> None:
        """`parse_marker` and `parse-marker` are one leg: red before any build."""
        self.rust("parse_marker", "parse-marker")
        done = self.measure()
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(
            "coverage:check: cargo-fuzz target 'parse_marker' and cargo-fuzz"
            " target 'parse-marker' both derive the leg fuzz-parse-marker —"
            " rename one, so each target holds a floor of its own\n",
            done.stderr,
        )
        self.assertEqual(NO_REPLAYS, self.replays())

    def test_a_rust_and_a_go_target_deriving_one_leg_are_red(self) -> None:
        """A root-package FuzzLoad and a target `load` clash across languages."""
        self.rust("load")
        self.write("go.mod", "module example.com/x\n")
        self.write("load_test.go", GO_TEST)
        done = self.measure()
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            "coverage:check: cargo-fuzz target 'load' and Go fuzz target"
            " FuzzLoad in . both derive the leg fuzz-load — rename one, so"
            " each target holds a floor of its own\n",
            done.stderr,
        )

    def test_a_corpus_of_links_is_handed_over_as_regular_files(self) -> None:
        """The tool reads the links' bytes, never fuzz/corpus/<target>/."""
        self.rust("round_trip")
        self.git("rm", "-q", "--cached", "fuzz/corpus/round_trip/seed")
        (self.tree / "fuzz/corpus/round_trip/seed").unlink()
        self.write("tests/fixtures/one.bin", "fixture-one")
        self.write("tests/fixtures/two.bin", "fixture-two")
        corpus = self.tree / "fuzz" / "corpus" / "round_trip"
        for name, fixture in (("a", "one.bin"), ("b", "two.bin")):
            (corpus / name).symlink_to(Path("../../../tests/fixtures") / fixture)
            self.git("add", "--", f"fuzz/corpus/round_trip/{name}")
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("fuzz-round-trip 75\n", done.stdout)
        words, entries = self.replays()["round_trip"]
        self.assertFalse(Path(words[7]).is_relative_to(self.tree), words[7])
        handed: list[str] = ["file ./a fixture-one", "file ./b fixture-two"]
        self.assertEqual(handed, entries)

    def test_an_untracked_seed_is_not_handed_over(self) -> None:
        """A local find beside the tracked seed is not in the tool's directory."""
        self.rust("parse_marker")
        self.write("fuzz/corpus/parse_marker/local-find", "untracked", track=False)
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        _, entries = self.replays()["parse_marker"]
        handed: list[str] = ["file ./seed parse_marker-seed"]
        self.assertEqual(handed, entries)

    def test_the_profiles_directory_the_run_made_is_removed(self) -> None:
        """Cargo fuzz writes fuzz/coverage/; one the run made does not stay."""
        self.rust("parse_marker")
        done = self.measure()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse((self.tree / "fuzz" / "coverage").exists())

    def test_a_tree_without_fuzz_targets_prints_nothing(self) -> None:
        """No fuzz crate, no `func Fuzz…`: the leg skips clean."""
        self.write("go.mod", "module example.com/x\n")
        self.write("x_test.go", "package x\n\nfunc FuzzyHelper() {}\n")
        done = self.measure()
        self.assertEqual((0, "", ""), (done.returncode, done.stdout, done.stderr))


class FloorRows(FuzzCase):
    """coverage:check holds every target to its own line (#1896)."""

    def test_a_target_with_no_floor_line_is_red_naming_it(self) -> None:
        """A floor that predates the leg reds by name, the remedy adopt."""
        self.go()
        self.write(".coverage-floor", FLOOR)
        done = self.check()
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn(
            f"coverage:check: {LOAD}: the fuzz target has no line in"
            " .coverage-floor — every fuzz target is a leg of the floor"
            " (#1896): run `mise run coverage:adopt` and commit .coverage-floor\n",
            done.stderr,
        )
        self.assertIn(
            "coverage:check: go leg holds the floor 80.0 (93.0%)\n", done.stdout
        )

    def test_adopt_writes_each_target_line_under_the_law(self) -> None:
        """`min(90, measured - 2)` per target, and the check then holds it."""
        self.go()
        measured = self.measure("all")
        self.assertEqual(0, measured.returncode, measured.stderr)
        self.assertEqual(f"go 93.0\n{LOAD} 43.6\n", measured.stdout)
        written = subprocess.run(
            [
                sys.executable,
                str(BELT / "coverage_floor.py"),
                "write",
                "--provenance",
                "coverage:adopt",
                "--adopt",
            ],
            cwd=self.tree,
            env=self.env,
            input=measured.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, written.returncode, written.stderr)
        self.assertEqual(
            "coverage-floor: adopted .coverage-floor at 90 (measured 93.0),"
            f" {LOAD} 41.6 (measured 43.6)\n",
            written.stdout,
        )
        text = (self.tree / ".coverage-floor").read_text(encoding="utf-8")
        self.assertIn(f"# {LOAD} measured: 43.6\n{LOAD} 41.6\n", text)
        parsed, problems = cf.parse(text)
        self.assertEqual(NO_LINES, problems)
        lines: dict[str, tuple[Decimal, Decimal]] = {
            LOAD: (Decimal("41.6"), Decimal("43.6"))
        }
        self.assertIsNotNone(parsed)
        if parsed is not None:
            self.assertEqual(lines, parsed.separate)
        held = self.check()
        self.assertEqual(0, held.returncode, held.stderr)
        self.assertIn(
            f"coverage:check: {LOAD} leg holds its floor 41.6 (43.6%)\n", held.stdout
        )

    def test_a_hand_edited_fuzz_line_is_refused_as_drift(self) -> None:
        """A line typed over its record never reaches the measurement."""
        self.go()
        self.write(".coverage-floor", f"{FLOOR}# {LOAD} measured: 43.6\n{LOAD} 50\n")
        done = self.check()
        self.assertEqual(1, done.returncode)
        self.assertEqual(
            f"coverage-floor: .coverage-floor {LOAD} floor 50 disagrees with its"
            " own record: measured 43.6 under band 2 and the cap 90 derives"
            " 41.6. Refused as drift and never repaired — restore the"
            " derivation, or delete both lines and re-derive with `mise run"
            " coverage:adopt`\n",
            done.stderr,
        )
        self.assertEqual(NO_LINES, self.log("go"))


if __name__ == "__main__":
    unittest.main()

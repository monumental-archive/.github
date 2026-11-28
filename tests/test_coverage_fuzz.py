"""Table tests for fuzz targets leaving the coverage floor (#2090).

No fuzz target is a leg of the floor: coverage-measure.sh measures no
fuzz target, coverage:check holds no `fuzz-*` line, coverage:adopt
writes none, and a floor file that still states one is refused by name.
The bodies under test are the real `mise/coverage-measure.sh` and the
`coverage:check` task's own `run` string, read out of mise/config.toml,
run by bash in a planted git tree holding a cargo-fuzz crate and a Go
fuzz target. Only the measuring tools are stubs (`cargo`, `cargo-fuzz`,
`rustup`, `rustc`, the toolchain's `llvm-cov`, `go`, `mise`), each
logging what it was handed, so a row proves no fuzz target was built,
replayed or measured.

Each row asserts the message, not just the exit status: a planted row
scored on exit alone can pass for the wrong reason.
"""

import io
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from belt import coverage_floor as cf
from belt import decoded
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

BELT = support.MISE
BASH = support.tool("bash")
GIT = support.tool("git")
MEASURE = str(BELT / "coverage-measure.sh")

# The host the stand-in rustc reports (support.rustc_verbose).
HOST = "x86_64-unknown-linux-gnu"

# rustc: support's stand-in, which also answers `--print sysroot`.
RUSTC = support.RUSTC_STUB.replace(
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
# profile; `tool cover` the planted total, STUB_GO_FUZZ for a `fuzz-`
# profile, which no row may reach.
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
# derived: v1.0.0
# legs: go
# go measured: 82.0
go 80.0
"""

# What a row expects when a stand-in was never called.
NO_LINES: list[str] = []

# coverage_floor.py's refusal of a fuzz line left in the floor (#2090).
LEFT = (
    "fuzz legs left the floor (#2090): delete the line, its"
    ' "measured" record and its comment'
)


def task_body(name: str) -> str:
    """Read a task's run string out of the belt.

    Returns:
        The body.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    return decoded.as_str(decoded.as_object(tasks[name], name)["run"], f"{name}'s run")


class FuzzCase:
    """A planted repository and the stand-in tools, one per row."""

    def __init__(self, tmp_path: Path, *, env: support.Env) -> None:
        """Plant an empty repository and the stubs beside it."""
        self.make_env = env
        self.tmp = tmp_path
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
        environ = self.make_env()
        self.env = environ | {
            "ORG_BELT_DIR": str(BELT),
            "ORG_PYTHON": sys.executable,
            "ORG_COVERAGE_REPORTS": str(self.tmp / "reports"),
            "RUSTUP_TOOLCHAIN": "stable",
            "PATH": f"{stubs}{os.pathsep}{environ.get('PATH', '')}",
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

    def measure(self, selector: str = "all") -> subprocess.CompletedProcess[str]:
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
        as_mise = support.task_run("coverage:check", task_body("coverage:check"))
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

    def fuzz_calls(self) -> list[str]:
        """Collect every stand-in call that built, replayed or measured a target.

        Returns:
            cargo's `fuzz` calls, every llvm-cov call, and every go call
            naming a `Fuzz` function or a `fuzz-` profile.

        """
        cargo = [c for c in self.log("cargo") if c.startswith("fuzz ")]
        go = [c for c in self.log("go") if "Fuzz" in c or "fuzz-" in c]
        return cargo + self.log("llvm-cov") + go

    def both(self) -> None:
        """Plant a cargo-fuzz crate and a Go module with a fuzz target."""
        self.go()
        self.write("fuzz/Cargo.toml", manifest("parse_marker"))
        self.write("fuzz/fuzz_targets/parse_marker.rs", "#![no_main]\n")
        self.write("fuzz/corpus/parse_marker/seed", "parse_marker-seed")


@fixture
def fuzz_case(tmp_path: Path, env: support.Env) -> FuzzCase:
    """Give each row its own FuzzCase.

    Returns:
        The row's FuzzCase.

    """
    return FuzzCase(tmp_path, env=env)


def test_a_floor_with_no_fuzz_line_passes_and_no_target_is_measured(
    fuzz_case: FuzzCase,
) -> None:
    """A Rust and a Go target, no fuzz line: green, no fuzz call.

    Before #2090 the gate measured both targets and refused the
    floor: "the fuzz target has no line".
    """
    fuzz_case.both()
    fuzz_case.write(".coverage-floor", FLOOR)
    done = fuzz_case.check()
    assert done.returncode == 0, done.stderr
    assert "coverage:check: go leg holds its floor 80.0 (93.0%)\n" in done.stdout
    # The row's own scratch path carries its name, which says "fuzz".
    said = (done.stdout + done.stderr).replace(str(fuzz_case.tmp), "<tmp>")
    assert "fuzz" not in said
    assert fuzz_case.fuzz_calls() == NO_LINES


def test_a_fuzz_line_and_its_record_are_refused_by_name(fuzz_case: FuzzCase) -> None:
    """`fuzz-x 50` and its record: red before anything is measured."""
    fuzz_case.both()
    fuzz_case.write(".coverage-floor", f"{FLOOR}# fuzz-x measured: 52.0\nfuzz-x 50\n")
    done = fuzz_case.check()
    assert done.returncode == 1, done.stdout
    assert f"coverage:check: .coverage-floor states fuzz-x: {LEFT}\n" == done.stderr
    assert fuzz_case.log("go") == NO_LINES


def test_adopt_writes_no_fuzz_line(
    fuzz_case: FuzzCase,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coverage:adopt's measurement and writer, over fuzz targets: no `fuzz-`."""
    fuzz_case.both()
    measured = fuzz_case.measure()
    assert measured.returncode == 0, measured.stderr
    assert measured.stdout == "go 93.0\n"
    capfd.readouterr()
    for key, value in fuzz_case.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.chdir(fuzz_case.tree)
    monkeypatch.setattr(sys, "stdin", io.StringIO(measured.stdout))
    monkeypatch.setattr(sys, "argv", ["coverage-floor"])
    status = cf.main(["write", "--provenance", "coverage:adopt", "--adopt"])
    out, err = capfd.readouterr()
    assert status == 0, err
    assert out == "coverage-floor: adopted .coverage-floor: go 90 (measured 93.0)\n"
    text = (fuzz_case.tree / ".coverage-floor").read_text(encoding="utf-8")
    assert "fuzz-" not in text
    parsed, problems = cf.parse(text)
    assert problems == NO_LINES
    assert parsed is not None
    assert fuzz_case.fuzz_calls() == NO_LINES


def test_the_fuzz_selector_is_gone(fuzz_case: FuzzCase) -> None:
    """`fuzz` is no leg the script accepts, so no caller can ask for one."""
    fuzz_case.both()
    done = fuzz_case.measure("fuzz")
    assert done.returncode == 1, done.stdout
    assert "unknown leg selector 'fuzz'" in done.stderr
    assert fuzz_case.fuzz_calls() == NO_LINES

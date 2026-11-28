"""Table tests for audit:semver's helper, mise/semver-audit.sh (#1341).

The helper runs for real in a planted git tree whose `Cargo.toml` is
tracked, with `cargo`, `cargo-semver-checks` and `curl` as stubs on
PATH. `cargo` answers `metadata` with the workspace the row plants;
`curl` answers the sparse index with the status the row plants per
crate; `cargo-semver-checks` exits 1 for a crate the row names red.
Every stub logs its arguments, and each row asserts the exact calls and
the exact verdict lines, so a row cannot pass by stopping somewhere
else.

gate_surface.py is the real one: the helper's population is its
derivation and nothing here re-states it. The Postgres provisioning is
the one thing replaced — the belt directory the helper reads holds the
real gate_surface.py beside a `pgrx-env.sh` whose `org_pgrx_provision`
only logs — because provisioning is pgrx-env.sh's to prove and needs a
Postgres; what is asserted here is that it runs once, before the first
pgrx judgment, and that a failed one reds the majors without stopping
the crates after it.

`publish = false` and `publish = []` are one row's planting twice:
cargo reports both as `"publish": []`, which is what the helper reads.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from belt.typed_pytest import fixture
from tests import support

BELT = support.MISE
SCRIPT = BELT / "semver-audit.sh"
BASH = support.tool("bash")
GIT = support.tool("git")
LABEL = "audit:semver"
INDEX = "https://index.crates.io"

CARGO_STUB = """#!/usr/bin/env bash
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$*" in
  "metadata --no-deps --format-version 1 --locked") cat "${PLANT_METADATA}" ;;
  *) exit 99 ;;
esac
"""
CURL_STUB = """#!/usr/bin/env bash
url="${*: -1}"
printf 'curl %s\\n' "${url}" >> "${PLANT_LOG}"
code=200
name="${url##*/}"
[[ ! -f ${PLANT_INDEX}/${name} ]] || code=$(cat "${PLANT_INDEX}/${name}")
printf '%s' "${code}"
[[ ${code} != 000 ]] || exit 7
"""
SEMVER_STUB = """#!/usr/bin/env bash
printf 'cargo-semver-checks %s\\n' "$*" >> "${PLANT_LOG}"
printf '%s|%s|%s\\n' "${6:-}" "${BINDGEN_EXTRA_CLANG_ARGS:-}" "${CFLAGS:-}" \\
  >> "${PLANT_LOG}.icu"
[[ " ${PLANT_RED:-} " != *" $3 "* ]] || exit 1
"""
# The real org_pgrx_major, read out of pgrx-env.sh rather than
# written again here: its subshell is the property the rows rely on.
WITH_MAJOR = re.search(
    r"^org_pgrx_major\(\) \{.*?^\}\n",
    (BELT / "pgrx-env.sh").read_text(encoding="utf-8"),
    re.MULTILINE | re.DOTALL,
)
if WITH_MAJOR is None:
    msg = "org_pgrx_major is gone from pgrx-env.sh"
    raise LookupError(msg)
PGRX_ENV_STUB = """
org_pgrx_provision() {
  local into=$1
  shift
  {
    printf 'provision'
    (($# == 0)) || printf ' [%s]' "$@"
    printf '\\n'
  } >> "${PLANT_LOG}"
  printf -v "$into" '%s' "${PLANT_DEFAULT:-}"
  return "${PLANT_PROVISION:-0}"
}
# The per-major ICU headers (#1774), as the real ones behave on a Mac:
# the check logs what it was asked and answers PLANT_ICU_CHECK; each
# major adds its own marker to both flags, so a compile that saw another
# major's would show it.
org_pgrx_darwin_icu_check() {
  printf 'icu-check [%s]\\n' "$1" >> "${PLANT_LOG}.icu"
  return "${PLANT_ICU_CHECK:-0}"
}
org_pgrx_major_env() {
  export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1 ${BINDGEN_EXTRA_CLANG_ARGS:-}"
  export CFLAGS="-Iicu-pg$1 ${CFLAGS:-}"
}
""" + WITH_MAJOR.group(0)

MAJORS = [14, 15, 16, 17, 18]
# The one major the red-at-one-major row plants red.
RED_MAJOR = 16


def clean_env(*, env: support.Env) -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return env()


def package(
    name: str,
    root: str,
    *,
    publish: list[str] | None = None,
    pgrx: bool = False,
    kinds: tuple[list[str], ...] = (["lib"],),
) -> dict[str, object]:
    """Plant one workspace member as `cargo metadata` reports it.

    `kinds` is one `kind` list per target, as cargo writes them: edtf's
    measured shapes are `["lib"]`, `["cdylib", "rlib"]` (edtf-wasm),
    `["cdylib", "lib"]` (edtf-postgres) and `["bin"]` (edtf-cli).

    Returns:
        The package record.

    """
    features: dict[str, list[str]] = {"default": []}
    deps: list[dict[str, str]] = []
    if pgrx:
        features = {
            "default": ["pg18"],
            "pg_test": [],
            **{f"pg{m}": [f"pgrx/pg{m}"] for m in MAJORS},
        }
        deps = [{"name": "pgrx"}]
    return {
        "id": f"path+file://{root}/{name}#{name}@1.0.0",
        "name": name,
        "version": "1.0.0",
        "publish": publish,
        "dependencies": deps,
        "features": features,
        "targets": [{"kind": kind, "crate_types": kind} for kind in kinds],
        "manifest_path": f"{root}/{name}/Cargo.toml",
    }


def judged(crate: str, *extra: str) -> str:
    """Spell one cargo-semver-checks call the way the stub logs it.

    Returns:
        The log line.

    """
    return " ".join(["cargo-semver-checks check-release -p", crate, *extra])


def at(crate: str, major: int) -> str:
    """Spell a pgrx crate's call at one major.

    Returns:
        The log line.

    """
    return judged(crate, "--only-explicit-features", "--features", f"pg{major}")


class Planted:
    """A planted tree, stub PATH and belt; the rows are in the classes below."""

    def __init__(self, tmp_path: Path, *, belt: support.Belt, env: support.Env) -> None:
        """Plant a tracked tree, a stub PATH and a belt with stub provisioning."""
        self.plant_belt = belt
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, body in {
            "cargo": CARGO_STUB,
            "curl": CURL_STUB,
            "cargo-semver-checks": SEMVER_STUB,
        }.items():
            stub = bins / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        belt = self.plant_belt(self.tmp / "belt", stubs={"pgrx-env.sh": PGRX_ENV_STUB})
        self.index = self.tmp / "index"
        self.index.mkdir()
        self.log = self.tmp / "log"
        self.log.touch()
        self.metadata = self.tmp / "metadata.json"
        self.env = clean_env(env=self.make_env) | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(belt),
            "PLANT_LOG": str(self.log),
            "PLANT_METADATA": str(self.metadata),
            "PLANT_INDEX": str(self.index),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(
            [GIT, *args], cwd=self.tree, env=clean_env(env=self.make_env), check=True
        )

    def plant(
        self, *packages: dict[str, object], index: dict[str, str] | None = None
    ) -> None:
        """Track a Cargo.toml, plant the metadata and the index answers."""
        (self.tree / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        self.git("add", "Cargo.toml")
        root = str(self.tree)
        metadata: dict[str, object] = {
            "packages": list(packages),
            "workspace_members": [p["id"] for p in packages],
            "workspace_root": root,
        }
        self.metadata.write_text(json.dumps(metadata), encoding="utf-8")
        answers: dict[str, str] = index or {}
        for name, code in answers.items():
            (self.index / name).write_text(code, encoding="utf-8")

    def run_helper(
        self, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the helper in the planted tree.

        Returns:
            The finished process and the stubs' call log.

        """
        done = subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()


@fixture
def planted(tmp_path: Path, belt: support.Belt, env: support.Env) -> Planted:
    """Give each row its own Planted.

    Returns:
        The row's Planted.

    """
    return Planted(tmp_path, belt=belt, env=env)


def semver_calls(log: list[str]) -> list[str]:
    """Keep only the judgments from a call log.

    Returns:
        The cargo-semver-checks lines, in order.

    """
    return [line for line in log if line.startswith("cargo-semver-checks ")]


def verdicts(done: subprocess.CompletedProcess[str]) -> list[str]:
    """Keep only the closing verdict lines from stdout.

    Returns:
        Every stdout line but the per-judgment progress lines.

    """
    return [
        line
        for line in done.stdout.splitlines()
        if not line.startswith(f"{LABEL}: judging ")
    ]


def test_ordinary_published_crate(planted: Planted) -> None:
    """One call, no feature flags, judged clean."""
    planted.plant(package("alpha", str(planted.tree)))
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [
        "cargo metadata --no-deps --format-version 1 --locked",
        f"curl {INDEX}/al/ph/alpha",
        judged("alpha"),
    ]
    assert log == want
    want = [f"{LABEL}: alpha: judged clean"]
    assert verdicts(done) == want


def test_no_baseline_is_named_not_judged(planted: Planted) -> None:
    """A definite 404 names the crate, judges nothing and exits 0."""
    planted.plant(package("lab-core", str(planted.tree)), index={"lab-core": "404"})
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    assert semver_calls(log) == list[str]()
    assert f"curl {INDEX}/la/b-/lab-core" in log
    want = [f"{LABEL}: lab-core: no published baseline on crates.io, not judged"]
    assert verdicts(done) == want


def test_index_error_is_red_by_name(planted: Planted) -> None:
    """A 500 is red and names the crate; it is never read as no baseline."""
    planted.plant(package("alpha", str(planted.tree)), index={"alpha": "500"})
    done, log = planted.run_helper()
    assert done.returncode == 1
    assert semver_calls(log) == list[str]()
    want = [f"{LABEL}: alpha: could not read the crates.io index (HTTP 500)"]
    assert verdicts(done) == want
    assert f"{LABEL}: red" in done.stderr


def test_no_answer_is_red(planted: Planted) -> None:
    """A lookup that got no answer at all is red, not a skip."""
    planted.plant(package("alpha", str(planted.tree)), index={"alpha": "000"})
    done, _ = planted.run_helper()
    assert done.returncode == 1
    want = [f"{LABEL}: alpha: could not read the crates.io index (HTTP 000)"]
    assert verdicts(done) == want


def test_throttle_is_red(planted: Planted) -> None:
    """A 429 is a throttle, not a fact about the crate."""
    planted.plant(package("alpha", str(planted.tree)), index={"alpha": "429"})
    done, _ = planted.run_helper()
    assert done.returncode == 1
    want = [f"{LABEL}: alpha: could not read the crates.io index (HTTP 429)"]
    assert verdicts(done) == want


def test_pgrx_crate_judged_per_major(planted: Planted) -> None:
    """Five majors, five calls, each with only its own pgNN."""
    planted.plant(
        package(
            "edtf-postgres",
            str(planted.tree),
            pgrx=True,
            kinds=(["cdylib", "lib"],),
        )
    )
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [
        "cargo metadata --no-deps --format-version 1 --locked",
        "provision",
        f"curl {INDEX}/ed/tf/edtf-postgres",
        *[at("edtf-postgres", m) for m in MAJORS],
    ]
    assert log == want
    judged = [f"{LABEL}: edtf-postgres at pg{m}: judged clean" for m in MAJORS]
    assert verdicts(done) == judged


def test_each_major_compiles_with_its_own_icu_only(planted: Planted) -> None:
    """No major's ICU include path reaches the next major's compile (#1774).

    Each judgment runs in a subshell with its own major's headers,
    and the parent's flags are what every subshell started from.
    """
    planted.plant(package("ext", str(planted.tree), pgrx=True))
    done, _ = planted.run_helper(BINDGEN_EXTRA_CLANG_ARGS="parent-b", CFLAGS="parent-c")
    assert done.returncode == 0, done.stderr
    icu = Path(f"{planted.log}.icu").read_text(encoding="utf-8").splitlines()
    want = [
        f"icu-check [{','.join(map(str, MAJORS))}]",
        *[f"pg{m}|-Iicu-pg{m} parent-b|-Iicu-pg{m} parent-c" for m in MAJORS],
    ]
    assert icu == want


def test_missing_icu_headers_red_the_majors_and_go_on(planted: Planted) -> None:
    """A failed ICU check reds that crate by name; nothing is compiled."""
    planted.plant(package("ext", str(planted.tree), pgrx=True))
    done, log = planted.run_helper(PLANT_ICU_CHECK="1")
    assert done.returncode == 1, done.stderr
    assert semver_calls(log) == list[str]()
    judged = [
        f"{LABEL}: ext at pg{m}: no ICU headers pinned for every major (above), red"
        for m in MAJORS
    ]
    assert verdicts(done) == judged


def test_pgrx_major_red_is_its_own_verdict(planted: Planted) -> None:
    """A red at one major does not stop the others."""
    planted.plant(package("ext", str(planted.tree), pgrx=True))
    bins = Path(planted.env["PATH"].split(os.pathsep)[0])
    (bins / "cargo-semver-checks").write_text(
        SEMVER_STUB.replace(
            '[[ " ${PLANT_RED:-} " != *" $3 "* ]] || exit 1',
            f'[[ "$*" != *"--features pg{RED_MAJOR}" ]] || exit 1',
        ),
        encoding="utf-8",
    )
    done, log = planted.run_helper()
    assert done.returncode == 1
    calls = [at("ext", m) for m in MAJORS]
    assert semver_calls(log) == calls
    judged = [
        f"{LABEL}: ext at pg{m}: judged {'red' if m == RED_MAJOR else 'clean'}"
        for m in MAJORS
    ]
    assert verdicts(done) == judged


def test_failed_provisioning_reds_majors_and_goes_on(planted: Planted) -> None:
    """No Postgres reds every major; the next crate is still judged."""
    planted.plant(
        package("ext", str(planted.tree), pgrx=True),
        package("zeta", str(planted.tree)),
    )
    done, log = planted.run_helper(PLANT_PROVISION="1")
    assert done.returncode == 1
    want = [judged("zeta")]
    assert semver_calls(log) == want
    want = [
        *[
            f"{LABEL}: ext at pg{m}: no Postgres could be provisioned, red"
            for m in MAJORS
        ],
        f"{LABEL}: zeta: judged clean",
    ]
    assert verdicts(done) == want


def test_publish_false_and_empty_are_skipped(planted: Planted) -> None:
    """Both spellings arrive as `[]`: no lookup, no judgment."""
    planted.plant(
        package("closed", str(planted.tree), publish=[]),
        package("shut", str(planted.tree), publish=[]),
    )
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = ["cargo metadata --no-deps --format-version 1 --locked"]
    assert log == want
    want = [
        f"{LABEL}: closed: publish = false, skipped",
        f"{LABEL}: shut: publish = false, skipped",
    ]
    assert verdicts(done) == want


def test_other_registry_named_not_judged(planted: Planted) -> None:
    """A crate published elsewhere only is named, with the registry."""
    planted.plant(
        package("inner", str(planted.tree), publish=["corp"]),
        package("both", str(planted.tree), publish=["corp", "crates-io"]),
    )
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [judged("both")]
    assert semver_calls(log) == want
    want = [
        f"{LABEL}: inner: published to corp, not crates.io, not judged",
        f"{LABEL}: both: judged clean",
    ]
    assert verdicts(done) == want


def test_one_red_among_three(planted: Planted) -> None:
    """All three are judged before the exit, which is 1."""
    root = str(planted.tree)
    planted.plant(package("aaa", root), package("bb", root), package("c", root))
    done, log = planted.run_helper(PLANT_RED="bb")
    assert done.returncode == 1
    want = [
        "cargo metadata --no-deps --format-version 1 --locked",
        f"curl {INDEX}/3/a/aaa",
        judged("aaa"),
        f"curl {INDEX}/2/bb",
        judged("bb"),
        f"curl {INDEX}/1/c",
        judged("c"),
    ]
    assert log == want
    want = [
        f"{LABEL}: aaa: judged clean",
        f"{LABEL}: bb: judged red",
        f"{LABEL}: c: judged clean",
    ]
    assert verdicts(done) == want
    assert f"{LABEL}: red" in done.stderr


def test_bin_only_crate_is_named_not_judged(planted: Planted) -> None:
    """No library target: no lookup, no judgment, exit 0."""
    planted.plant(package("cli", str(planted.tree), kinds=(["bin"], ["test"])))
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = ["cargo metadata --no-deps --format-version 1 --locked"]
    assert log == want
    want = [f"{LABEL}: cli: no library target, no API to judge, not judged"]
    assert verdicts(done) == want


def test_proc_macro_crate_is_named_not_judged(planted: Planted) -> None:
    """The tool does not count proc-macro as a library target; nor does this."""
    planted.plant(package("derive", str(planted.tree), kinds=(["proc-macro"],)))
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = ["cargo metadata --no-deps --format-version 1 --locked"]
    assert log == want
    want = [f"{LABEL}: derive: no library target, no API to judge, not judged"]
    assert verdicts(done) == want


def test_lib_and_bin_crate_is_judged(planted: Planted) -> None:
    """A library beside a binary is judged, once."""
    planted.plant(package("both", str(planted.tree), kinds=(["lib"], ["bin"])))
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [judged("both")]
    assert semver_calls(log) == want
    want = [f"{LABEL}: both: judged clean"]
    assert verdicts(done) == want


def test_every_library_kind_is_judged(planted: Planted) -> None:
    """rlib, dylib, cdylib and staticlib count, as the tool counts them."""
    root = str(planted.tree)
    planted.plant(
        package("wasm", root, kinds=(["cdylib", "rlib"],)),
        package("dyn", root, kinds=(["dylib"],)),
        package("static", root, kinds=(["staticlib"],)),
    )
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [judged("wasm"), judged("dyn"), judged("static")]
    assert semver_calls(log) == want


def test_bin_only_among_judged_crates(planted: Planted) -> None:
    """The crates around a bin-only one are still judged."""
    root = str(planted.tree)
    planted.plant(
        package("core", root),
        package("cli", root, kinds=(["bin"],)),
        package("wasm", root, kinds=(["cdylib", "rlib"],)),
    )
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    want = [judged("core"), judged("wasm")]
    assert semver_calls(log) == want
    assert f"curl {INDEX}/3/c/cli" not in log
    want = [
        f"{LABEL}: core: judged clean",
        f"{LABEL}: cli: no library target, no API to judge, not judged",
        f"{LABEL}: wasm: judged clean",
    ]
    assert verdicts(done) == want


def test_index_path_lowercases(planted: Planted) -> None:
    """The sparse index is keyed on the lowercased name."""
    planted.plant(package("Mixed_Case", str(planted.tree)))
    _, log = planted.run_helper()
    assert f"curl {INDEX}/mi/xe/mixed_case" in log


def test_no_cargo_toml_skips_clean(planted: Planted) -> None:
    """No tracked manifest: nothing is asked of cargo, exit 0."""
    done, log = planted.run_helper()
    assert done.returncode == 0, done.stderr
    assert log == list[str]()
    assert done.stdout == f"{LABEL}: no Cargo.toml tracked, skipped\n"

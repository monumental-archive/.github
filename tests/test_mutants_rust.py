"""Table tests for audit:mutants' Rust leg, mise/mutants-rust.sh (#1355).

The leg runs for real in a planted git tree, with `cargo` as a stub on
PATH. The stub answers `metadata` with the workspace the row plants,
`mutants --list` with the mutant names the row plants (all of them
under `--no-config`, the configured subset otherwise, less any name an
`--exclude-re` on the command line matches, as cargo-mutants adds
command-line exclusions to the config's), and a mutation
run by writing the `outcomes.json` and `missed.txt` the row plants into
the `--output` directory and exiting with the row's status. Every call
is logged; each row asserts the exact calls and the exact messages, so
a row cannot pass by stopping somewhere else.

gate_surface.py and jq are the real ones. Postgres provisioning is the
one thing replaced: `org_pgrx_provision` only logs, because
provisioning is pgrx-env.sh's to prove and needs a Postgres.

GIT ENVIRONMENT IS SCRUBBED (#857): git exports GIT_DIR to hooks run in
a linked worktree, and a fixture that inherited it would write into the
caller's index when this suite runs from the pre-push hook.
"""

import io
import json
import os
import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import mutants_rust as helper
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The mutants a listing with two named lines holds; the seven of every
# configuration minus the five listed; and the site the planted hit's
# record names.
LISTED = 2
EXCLUDED = 2
SITE = 7

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "audit:mutants"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


BELT = support.MISE
SCRIPT = BELT / "mutants-rust.sh"
BASH = support.tool("bash")
GIT = support.tool("git")
LABEL = "audit:mutants: rust"
CONFIG = ".cargo/mutants.toml"


CARGO_STUB = """#!/usr/bin/env bash
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
# A listing less every name the command line's --exclude-re matches.
listing() {
  local file="$1" prev="" patterns=()
  shift
  for word in "$@"; do
    [[ ${prev} != --exclude-re ]] || patterns+=("${word}")
    prev="${word}"
  done
  "${ORG_PYTHON}" -c 'import re, sys
patterns = [re.compile(p) for p in sys.argv[2:]]
for name in open(sys.argv[1], encoding="utf-8"):
    if not any(p.search(name.rstrip("\\n")) for p in patterns):
        sys.stdout.write(name)' "${file}" ${patterns[@]+"${patterns[@]}"}
}
case "$*" in
  "metadata --no-deps --format-version 1 --locked") cat "${PLANT_METADATA}" ;;
  "mutants --list --no-config "*) cat "${PLANT_ALL}" ;;
  "mutants --list --shard "*) listing "${PLANT_SHARD}" "$@" ;;
  "mutants --list "*) listing "${PLANT_LISTED}" "$@" ;;
  "mutants --output "*)
    out="$3/mutants.out"
    mkdir -p "${out}"
    [[ ! -f ${PLANT_OUTCOMES} ]] || cp "${PLANT_OUTCOMES}" "${out}/outcomes.json"
    cp "${PLANT_MISSED}" "${out}/missed.txt"
    exit "${PLANT_STATUS:-0}"
    ;;
  *) exit 99 ;;
esac
"""
CARGO_MUTANTS_STUB = "#!/usr/bin/env bash\nexit 99\n"
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
# The per-major ICU headers (#1774): the check answers PLANT_ICU_CHECK,
# and the headers are a mark the stub cargo could show.
org_pgrx_darwin_icu_check() { return "${PLANT_ICU_CHECK:-0}"; }
org_pgrx_major_env() {
  printf 'major %s\\n' "$1" >> "${PLANT_LOG}"
  export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1"
}
""" + WITH_MAJOR.group(0)

SURVIVOR = "src/lib.rs:12:5: replace answer -> u32 with 0"
NAMES = [
    "src/lib.rs:7:5: replace banner -> String with String::new()",
    SURVIVOR,
    "src/lib.rs:20:9: replace == with != in check",
]
METADATA = "cargo metadata --no-deps --format-version 1 --locked"
REASON = (
    "  an exclusion records a mutant the suite cannot kill;"
    " say why, one # line above each entry"
)
MARK = "// mutants:equivalent"
SPELLING = f"{MARK} <mutation>: <reason>"
RED = [
    (
        f"{LABEL}: red — delete code a survivor proves redundant,"
        " kill each survivor with an assertion,"
        " or record an equivalent mutant at its site"
    ),
    f"  on its line or the line above: {SPELLING}",
]
# The site whose mutant the record rows explain: NAMES[2], line 20.
EQUAL = "replace == with !="
DERIVED = r"^src/lib\.rs:20:[0-9]+: replace == with !=( in .*)?$"
FORM = (
    f"  {SPELLING} — the mutation is cargo-mutants' own text"
    " for the mutant, without its ' in <function>'"
)


def clean_env(*, env: support.Env) -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return env(MISE_TASK_NAME=TASK)


def package(
    name: str, root: str, *, default: list[str] | None = None
) -> dict[str, object]:
    """Plant one workspace member as `cargo metadata` reports it.

    A `default` makes the member a pgrx extension declaring pg17 and pg18,
    with that list as its `default` feature.

    Returns:
        The package record.

    """
    features: dict[str, list[str]] = {"default": []}
    deps: list[dict[str, str]] = []
    if default is not None:
        features = {
            "default": default,
            "pg_test": [],
            "pg17": ["pgrx/pg17"],
            "pg18": ["pgrx/pg18"],
        }
        deps = [{"name": "pgrx"}]
    return {
        "id": f"path+file://{root}/{name}#{name}@1.0.0",
        "name": name,
        "version": "1.0.0",
        "publish": None,
        "dependencies": deps,
        "features": features,
        "targets": [{"kind": ["lib"], "crate_types": ["lib"]}],
        "manifest_path": f"{root}/{name}/Cargo.toml",
    }


def outcomes(
    total: int, caught: int, missed: int, timeout: int = 0
) -> dict[str, object]:
    """Plant the counts cargo-mutants 27.1.0 writes to outcomes.json.

    Returns:
        The outcomes record, its `outcomes` list left empty: the leg reads
        only the counts.

    """
    return {
        "total_mutants": total,
        "missed": missed,
        "caught": caught,
        "timeout": timeout,
        "unviable": total - caught - missed - timeout,
        "success": 0,
        "cargo_mutants_version": "27.1.0",
        "outcomes": [],
    }


class Planted:
    """A planted tree, a stub PATH and a belt with stub provisioning."""

    def __init__(self, tmp_path: Path, *, belt: support.Belt, env: support.Env) -> None:
        """Plant a git tree, the stubs and the belt the leg reads."""
        self.plant_belt = belt
        self.make_env = env
        self.tmp = tmp_path
        self.tree = self.tmp / "tree"
        self.tree.mkdir()
        bins = self.tmp / "bin"
        bins.mkdir()
        for name, body in {
            "cargo": CARGO_STUB,
            "cargo-mutants": CARGO_MUTANTS_STUB,
        }.items():
            stub = bins / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        belt_dir = self.plant_belt(
            self.tmp / "belt", stubs={"pgrx-env.sh": PGRX_ENV_STUB}
        )
        self.log = self.tmp / "log"
        self.log.touch()
        self.files = {
            name: self.tmp / name
            for name in (
                "metadata.json",
                "all",
                "listed",
                "outcomes.json",
                "missed",
                "shard",
            )
        }
        self.env = clean_env(env=self.make_env) | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(belt_dir),
            "PLANT_LOG": str(self.log),
            "PLANT_METADATA": str(self.files["metadata.json"]),
            "PLANT_ALL": str(self.files["all"]),
            "PLANT_LISTED": str(self.files["listed"]),
            "PLANT_OUTCOMES": str(self.files["outcomes.json"]),
            "PLANT_MISSED": str(self.files["missed"]),
            "PLANT_SHARD": str(self.files["shard"]),
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        """Run git in the planted tree, scrubbed of the caller's git state."""
        subprocess.run(
            [GIT, *args], cwd=self.tree, env=clean_env(env=self.make_env), check=True
        )

    def track(self, path: str, text: str) -> None:
        """Write a file into the planted tree and track it."""
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", path)

    def plant(
        self,
        *packages: dict[str, object],
        config: str = "",
        listed: list[str] | None = None,
        result: dict[str, object] | None = None,
        missed: list[str] | None = None,
        shard: list[str] | None = None,
    ) -> None:
        """Track a workspace and its config; plant what the stub answers."""
        self.track("Cargo.toml", "[workspace]\n")
        self.track(CONFIG, config)
        root = str(self.tree)
        metadata: dict[str, object] = {
            "packages": list(packages),
            "workspace_members": [p["id"] for p in packages],
            "workspace_root": root,
        }
        self.files["metadata.json"].write_text(json.dumps(metadata), encoding="utf-8")
        self.files["all"].write_text("\n".join(NAMES) + "\n", encoding="utf-8")
        chosen = NAMES if listed is None else listed
        self.files["listed"].write_text("\n".join(chosen) + "\n", encoding="utf-8")
        if result is not None:
            self.files["outcomes.json"].write_text(json.dumps(result), encoding="utf-8")
        self.files["missed"].write_text(
            "".join(f"{m}\n" for m in missed or []), encoding="utf-8"
        )
        self.files["shard"].write_text(
            "".join(f"{m}\n" for m in shard or []), encoding="utf-8"
        )

    def run_leg(self, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the leg in the planted tree.

        Returns:
            The finished process and the stub's call log.

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


def calls(log: list[str]) -> list[str]:
    """Spell each logged call with the scratch output directory blanked.

    Returns:
        The log, `--output <dir>` written as `--output OUT`.

    """
    spelled = []
    for line in log:
        words = line.split(" ")
        if words[1:3] == ["mutants", "--output"]:
            words[3] = "OUT"
        spelled.append(" ".join(words))
    return spelled


def test_no_config_skips_clean_by_name(planted: Planted) -> None:
    """No .cargo/mutants.toml: exit 0, named, nothing called."""
    planted.track("Cargo.toml", "[workspace]\n")
    done, log = planted.run_leg()
    assert done.returncode == 0, done.stderr
    want = [f"{LABEL}: no {CONFIG} tracked, skipped"]
    assert done.stdout.splitlines() == want
    assert log == list[str]()


def test_untracked_config_is_not_adoption(planted: Planted) -> None:
    """A config on disk that git does not track adopts nothing."""
    planted.track("Cargo.toml", "[workspace]\n")
    (planted.tree / ".cargo").mkdir()
    (planted.tree / CONFIG).write_text("", encoding="utf-8")
    done, log = planted.run_leg()
    assert done.returncode == 0, done.stderr
    want = [f"{LABEL}: no {CONFIG} tracked, skipped"]
    assert done.stdout.splitlines() == want
    assert log == list[str]()


def test_clean(planted: Planted) -> None:
    """Every mutant killed: exit 0 and the summary line."""
    planted.plant(package("alpha", str(planted.tree)), result=outcomes(3, 3, 0))
    done, log = planted.run_leg(PLANT_DEFAULT="18")
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
        f"{LABEL}: 3 mutants, 3 killed, 0 survived, 0 excluded",
    ]
    assert done.stdout.splitlines() == want
    assert not done.stderr
    want = [
        METADATA,
        "cargo mutants --list --no-config --package alpha",
        "cargo mutants --list --package alpha",
        "cargo mutants --output OUT --package alpha",
    ]
    assert calls(log) == want


def test_one_survivor_is_red_with_file_and_line(planted: Planted) -> None:
    """A survivor: red, printed as file:line:col and the mutation."""
    planted.plant(
        package("alpha", str(planted.tree)),
        result=outcomes(3, 2, 1),
        missed=[SURVIVOR],
    )
    done, _ = planted.run_leg(PLANT_STATUS="2")
    assert done.returncode == 1
    want = [
        f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
        f"{LABEL}: survived: {SURVIVOR}",
        f"{LABEL}: 3 mutants, 2 killed, 1 survived, 0 excluded",
    ]
    assert done.stdout.splitlines() == want
    assert done.stderr.splitlines() == RED


def test_excluded_survivor_with_reason_is_counted_excluded(planted: Planted) -> None:
    """An exclude_re entry under a # reason: exit 0, counted excluded."""
    config = (
        "exclude_re = [\n"
        "  # the answer is a fixture constant no caller can observe\n"
        '  "replace answer -> u32",\n'
        "]\n"
    )
    kept = [n for n in NAMES if n != SURVIVOR]
    planted.plant(
        package("alpha", str(planted.tree)),
        config=config,
        listed=kept,
        result=outcomes(2, 2, 0),
    )
    done, _ = planted.run_leg()
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: mutating workspace (2 mutants, 1 excluded)",
        f"{LABEL}: 2 mutants, 2 killed, 0 survived, 1 excluded",
    ]
    assert done.stdout.splitlines() == want


def test_timeout_counts_as_killed(planted: Planted) -> None:
    """Exit 3 (timeouts) is a result: a hung suite noticed the mutant."""
    planted.plant(package("alpha", str(planted.tree)), result=outcomes(3, 2, 0, 1))
    done, _ = planted.run_leg(PLANT_STATUS="3")
    assert done.returncode == 0, done.stderr
    assert (
        f"{LABEL}: 3 mutants, 3 killed, 0 survived, 0 excluded"
        in done.stdout.splitlines()
    )


def test_red_baseline_judges_nothing(planted: Planted) -> None:
    """Exit 4 (the unmutated suite failed) is red, never a clean zero."""
    planted.plant(package("alpha", str(planted.tree)))
    done, _ = planted.run_leg(PLANT_STATUS="4")
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: workspace: cargo mutants exited 4"
            " — nothing judged (a red baseline is exit 4)"
        ),
        *RED,
    ]
    assert done.stderr.splitlines() == want


def test_an_outcomes_json_that_does_not_decode_is_red_by_name(planted: Planted) -> None:
    """A document decoded's loader refuses is not judged, never a crash."""
    planted.plant(package("alpha", str(planted.tree)), result=outcomes(3, 3, 0))
    planted.files["outcomes.json"].write_text(
        '{"missed": 0, "missed": 1}', encoding="utf-8"
    )
    done, _ = planted.run_leg()
    assert done.returncode == 1
    want = [f'{LABEL}: workspace: outcomes.json: duplicate key "missed"', *RED]
    assert done.stderr.splitlines() == want


def test_a_count_outcomes_json_does_not_state_is_red_by_name(planted: Planted) -> None:
    """No `timeout` in outcomes.json: not judged, never read as zero."""
    result = outcomes(3, 3, 0)
    del result["timeout"]
    planted.plant(package("alpha", str(planted.tree)), result=result)
    done, _ = planted.run_leg(MUTANTS_SUMMARY_DIR=str(planted.tmp / "summary"))
    assert done.returncode == 1
    want = [f"{LABEL}: workspace: outcomes.json states no timeout", *RED]
    assert done.stderr.splitlines() == want
    assert (
        f"{LABEL}: 0 mutants, 0 killed, 0 survived, 0 excluded"
        in done.stdout.splitlines()
    )
    assert not (planted.tmp / "summary" / "rust.tsv").exists()


def test_exclude_re_without_reason_is_red_by_name(planted: Planted) -> None:
    """An entry with no # line above it: red, by line and entry."""
    config = (
        "exclude_re = [\n"
        "  # the banner is cosmetic\n"
        '  "replace banner",\n'
        '  "replace answer -> u32",\n'
        "]\n"
    )
    planted.plant(package("alpha", str(planted.tree)), config=config)
    done, log = planted.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: {CONFIG}:4: exclude_re entry"
            ' "replace answer -> u32" has no # reason line above it'
        ),
        REASON,
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def test_exclude_globs_needs_a_reason_too(planted: Planted) -> None:
    """A whole-file exclusion is an exclusion: the same rule."""
    config = 'exclude_globs = [\n  "src/generated.rs",\n]\n'
    planted.plant(package("alpha", str(planted.tree)), config=config)
    done, _ = planted.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: {CONFIG}:2: exclude_globs entry"
            ' "src/generated.rs" has no # reason line above it'
        ),
        REASON,
    ]
    assert done.stderr.splitlines() == want


def test_entry_on_the_opening_line_is_red(planted: Planted) -> None:
    """`exclude_re = ["x"]` leaves nowhere to write the reason."""
    config = '# cosmetic\nexclude_re = ["replace banner"]\n'
    planted.plant(package("alpha", str(planted.tree)), config=config)
    done, _ = planted.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: {CONFIG}:2: exclude_re entry"
            " an entry on the opening line has no # reason line above it"
        ),
        REASON,
    ]
    assert done.stderr.splitlines() == want


def test_mutants_skip_attribute_is_refused(planted: Planted) -> None:
    """#[mutants::skip] is an exclusion with nowhere for the reason."""
    planted.plant(package("alpha", str(planted.tree)), result=outcomes(3, 3, 0))
    planted.track("alpha/src/lib.rs", "#[mutants::skip]\nfn f() {}\n")
    done, log = planted.run_leg()
    assert done.returncode == 1
    want = [
        f"{LABEL}: #[mutants::skip] in the tree:",
        "  alpha/src/lib.rs:1:#[mutants::skip]",
        (f"  record an equivalent mutant at its site instead: {SPELLING}"),
    ]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


def source(**lines: str) -> str:
    """Write a 24-line Rust file with the named lines planted (`l20=...`).

    Returns:
        The file's text: `fn line_N() {}` on every line not planted.

    """
    return "".join(
        lines.get(f"l{n}", f"fn line_{n}() {{}}") + "\n" for n in range(1, 25)
    )


def test_the_leg_end_to_end_derives_counts_and_holds_its_records(
    planted: Planted,
) -> None:
    """One run, two records: the sound one excludes, the stale one reds.

    The config excludes nothing and the unconfigured listing names
    three mutants, so the one excluded is the record's (test_clean,
    on the same planting, is 0). Every cut listing and the run carry
    exactly the records' derived patterns; the stale record is named
    by its file and line.
    """
    planted.plant(package("alpha", str(planted.tree)), result=outcomes(2, 2, 0))
    text = source(
        l3=f"    {MARK} delete !: the flag is always set",
        l19=f"    {MARK} {EQUAL}: a and b are one value",
    )
    planted.track("src/lib.rs", text)
    done, log = planted.run_leg()
    assert done.returncode == 1
    want = [
        f"{LABEL}: mutating workspace (2 mutants, 1 excluded)",
        f"{LABEL}: 2 mutants, 2 killed, 0 survived, 1 excluded",
    ]
    assert done.stdout.splitlines() == want
    want = [
        (
            f"{LABEL}: src/lib.rs:3: {MARK} delete ! is stale:"
            " no such mutant at line 4 — delete it"
        ),
        *RED,
    ]
    assert done.stderr.splitlines() == want
    stale = r"^src/lib\.rs:4:[0-9]+: delete !( in .*)?$"
    derived = f"--exclude-re {stale} --exclude-re {DERIVED}"
    want = [
        METADATA,
        "cargo mutants --list --no-config --package alpha",
        f"cargo mutants --list {derived} --package alpha",
        f"cargo mutants --output OUT {derived} --package alpha",
    ]
    assert calls(log) == want


# Each record and config rule, through mutants_rust.py's own steps.
@fixture
def grepped(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Pipe a planted `git grep -n` to the `records` step.

    Returns:
        A function that puts `grep` on stdin.

    """

    def feed(grep: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(grep))

    return feed


@fixture
def configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[str], None]:
    """Plant a config for the `reasons` step and step into its tree.

    Returns:
        A function that writes `config` as the tree's CONFIG.

    """

    def plant(config: str) -> None:
        (tmp_path / ".cargo").mkdir(exist_ok=True)
        (tmp_path / CONFIG).write_text(config, encoding="utf-8")
        monkeypatch.chdir(tmp_path)

    return plant


def test_a_trailing_record_derives_its_lines_pattern(
    capfd: pytest.CaptureFixture[str], grepped: Callable[[str], None]
) -> None:
    """Code before the comment: the site is the comment's own line."""
    grep = f"src/lib.rs:20:if a == b {{ {MARK} {EQUAL}: one value\n"
    grepped(grep)
    status = helper.main(["records"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, f"{DERIVED}\n", "")


def test_a_record_on_the_line_above_derives_the_next_lines_pattern(
    capfd: pytest.CaptureFixture[str], grepped: Callable[[str], None]
) -> None:
    """A comment alone on its line: the site is the line below it."""
    grep = f"src/lib.rs:19:    {MARK} {EQUAL}: one value\n"
    grepped(grep)
    status = helper.main(["records"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, f"{DERIVED}\n", "")


def test_a_record_with_no_mutation_text_is_red_by_file_and_line(
    capfd: pytest.CaptureFixture[str], grepped: Callable[[str], None]
) -> None:
    """A reason with nothing before it: nothing to match, no pattern."""
    grep = f"src/lib.rs:19:    {MARK} : one value\n"
    want = f"{LABEL}: src/lib.rs:19: {MARK} names no mutation\n{FORM}\n"
    grepped(grep)
    status = helper.main(["records"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", want)


def test_a_record_with_no_reason_is_red_by_file_and_line(
    capfd: pytest.CaptureFixture[str], grepped: Callable[[str], None]
) -> None:
    """Mutation text and no reason: nothing says why it cannot be killed."""
    grep = f"src/lib.rs:19:    {MARK} {EQUAL}:\n"
    want = f"{LABEL}: src/lib.rs:19: {MARK} {EQUAL} gives no reason\n{FORM}\n"
    grepped(grep)
    status = helper.main(["records"])
    out, err = capfd.readouterr()
    assert (status, out, err) == (1, "", want)


def test_a_record_in_a_file_the_run_does_not_mutate_is_red() -> None:
    """No mutant of the listing is in its file: named, never matched."""
    found = helper.read_records(f"src/other.rs:2:    {MARK} delete !: why\n")
    want = [f"src/other.rs:2: {MARK} delete ! is in no mutated file"]
    assert helper.unmatched(found, NAMES) == want


def test_a_stale_record_is_red_by_file_and_line() -> None:
    """Its file is mutated, but no mutant of its text is at its site."""
    found = helper.read_records(
        f"src/lib.rs:3:    {MARK} delete !: why\n"
        f"src/lib.rs:19:    {MARK} {EQUAL}: why\n"
    )
    want = [
        (
            f"src/lib.rs:3: {MARK} delete ! is stale:"
            " no such mutant at line 4 — delete it"
        )
    ]
    assert helper.unmatched(found, NAMES) == want


def test_a_pinned_exclude_re_entry_is_red_by_name(
    capfd: pytest.CaptureFixture[str], configured: Callable[[str], None]
) -> None:
    """`:line:col:` in an entry: record it at its site instead."""
    config = (
        "exclude_re = [\n"
        "  # a and b are one value\n"
        '  "src/lib\\\\.rs:20:9: replace == with !=",\n'
        "]\n"
    )
    want = (
        f"{LABEL}: {CONFIG}:3: exclude_re entry"
        ' "src/lib\\\\.rs:20:9: replace == with !=" pins a line and column\n'
        "  record the mutant at its site instead, on its line or"
        f" the line above: {SPELLING}\n"
    )
    configured(config)
    status = helper.main(["reasons", CONFIG])
    _out, err = capfd.readouterr()
    assert (status, err) == (1, want)


def test_an_unpinned_exclude_re_entry_with_its_reason_is_accepted(
    capfd: pytest.CaptureFixture[str], configured: Callable[[str], None]
) -> None:
    """A rule that is not a site stays in the config; [0-9]+ is no position."""
    config = (
        "exclude_re = [\n"
        "  # pgrx's generated test shims are not the extension's code\n"
        '  "tests::",\n'
        "  # every banner is cosmetic\n"
        '  "src/lib\\\\.rs:[0-9]+:[0-9]+: replace banner",\n'
        "]\n"
    )
    configured(config)
    status = helper.main(["reasons", CONFIG])
    _out, err = capfd.readouterr()
    assert (status, err) == (0, "")


def test_pgrx_crate_runs_under_its_default_major(planted: Planted) -> None:
    """Every member mutated; the extension under pg18 with pg_test."""
    root = str(planted.tree)
    planted.plant(
        package("alpha", root),
        package("ext", root, default=["pg18"]),
        result=outcomes(3, 3, 0),
    )
    done, log = planted.run_leg(PLANT_DEFAULT="18")
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
        (f"{LABEL}: ext is a pgrx extension, mutated under its default major, pg18"),
        f"{LABEL}: mutating ext-pg18 (3 mutants, 0 excluded)",
        f"{LABEL}: 6 mutants, 6 killed, 0 survived, 0 excluded",
    ]
    assert done.stdout.splitlines() == want
    want = [
        METADATA,
        # Provisioned once, before the workspace run, which compiles
        # at the default major it names (#1940).
        "provision",
        "major 18",
        "cargo mutants --list --no-config --package alpha",
        "cargo mutants --list --package alpha",
        "cargo mutants --output OUT --package alpha",
        "major 18",
        "cargo mutants --list --no-config --package ext --features pg_test",
        "cargo mutants --list --package ext --features pg_test",
        "cargo mutants --output OUT --package ext --features pg_test",
    ]
    assert calls(log) == want


def test_pgrx_crate_without_a_single_default_major_is_red(planted: Planted) -> None:
    """No pgNN in `default`: red by name, never a guessed major."""
    planted.plant(package("ext", str(planted.tree), default=[]))
    done, log = planted.run_leg()
    assert done.returncode == 1
    want = [
        (
            f"{LABEL}: ext: a pgrx extension whose default features name"
            " no single pgNN — it is mutated under its default major"
        ),
        *RED,
    ]
    assert done.stderr.splitlines() == want
    mutated = [c for c in log if "--package ext" in c]
    assert mutated == list[str]()


def test_failed_provisioning_is_red(planted: Planted) -> None:
    """No Postgres: the extension is red, never skipped."""
    planted.plant(package("ext", str(planted.tree), default=["pg18"]))
    done, _ = planted.run_leg(PLANT_PROVISION="1")
    assert done.returncode == 1
    want = [f"{LABEL}: ext at pg18: no Postgres could be provisioned", *RED]
    assert done.stderr.splitlines() == want


def test_missing_icu_headers_are_red(planted: Planted) -> None:
    """No ICU headers for the default major: red by name, never mutated."""
    planted.plant(package("ext", str(planted.tree), default=["pg18"]))
    done, log = planted.run_leg(PLANT_ICU_CHECK="1")
    assert done.returncode == 1
    want = [f"{LABEL}: ext at pg18: no ICU headers for it are pinned (above)", *RED]
    assert done.stderr.splitlines() == want
    mutated = [c for c in log if c.startswith("cargo mutants")]
    assert mutated == list[str]()


class Shards(Planted):
    """One shard of repo-mutants.yml's matrix: MUTANTS_SHARD=k/n."""

    def summary(self) -> str:
        """Read the counts the leg wrote for the totals job.

        Returns:
            rust.tsv's text.

        """
        return (self.tmp / "summary" / "rust.tsv").read_text(encoding="utf-8")

    def shard_env(self, shard: str) -> dict[str, str]:
        """Spell the environment the matrix job gives the leg.

        Returns:
            MUTANTS_SHARD and a fresh MUTANTS_SUMMARY_DIR.

        """
        (self.tmp / "summary").mkdir(exist_ok=True)
        return {
            "MUTANTS_SHARD": shard,
            "MUTANTS_SUMMARY_DIR": str(self.tmp / "summary"),
        }


@fixture
def shards(tmp_path: Path, belt: support.Belt, env: support.Env) -> Shards:
    """Give each row its own Shards.

    Returns:
        The row's Shards.

    """
    return Shards(tmp_path, belt=belt, env=env)


def test_shard_runs_its_round_robin_share_and_reports_its_counts(
    shards: Shards,
) -> None:
    """Round-robin --shard cuts listing and run; summary names the shard."""
    shards.plant(
        package("alpha", str(shards.tree)),
        shard=NAMES[:2],
        result=outcomes(2, 1, 1),
        missed=[SURVIVOR],
    )
    done, log = shards.run_leg(PLANT_STATUS="2", **shards.shard_env("1/4"))
    assert done.returncode == 1
    want = [
        f"{LABEL}: mutating workspace, shard 1/4 (2 mutants, 0 excluded)",
        f"{LABEL}: survived: {SURVIVOR}",
        f"{LABEL}: shard 1/4: 2 mutants, 1 killed, 1 survived, 0 excluded",
    ]
    assert done.stdout.splitlines() == want
    want = [
        METADATA,
        "cargo mutants --list --no-config --package alpha",
        "cargo mutants --list --package alpha",
        ("cargo mutants --list --shard 1/4 --sharding round-robin --package alpha"),
        (
            "cargo mutants --output OUT --shard 1/4 --sharding round-robin"
            " --package alpha"
        ),
    ]
    assert calls(log) == want
    assert shards.summary() == "1/4\t2\t1\t1\t0\twhole\n"


def test_empty_shard_is_green_by_name(shards: Shards) -> None:
    """Measured: an empty shard exits 0 and writes no outcomes.json."""
    shards.plant(package("alpha", str(shards.tree)), shard=[])
    done, log = shards.run_leg(**shards.shard_env("3/4"))
    assert done.returncode == 0, done.stderr
    want = [
        f"{LABEL}: mutating workspace, shard 3/4 (0 mutants, 0 excluded)",
        (f"{LABEL}: workspace, shard 3/4: no mutants in this shard, nothing to run"),
        f"{LABEL}: shard 3/4: 0 mutants, 0 killed, 0 survived, 0 excluded",
    ]
    assert done.stdout.splitlines() == want
    assert (
        "cargo mutants --output OUT --shard 3/4 --sharding round-robin --package alpha"
    ) not in calls(log)
    assert shards.summary() == "3/4\t0\t0\t0\t0\twhole\n"


def test_excluded_is_the_whole_population(shards: Shards) -> None:
    """Every shard reports the same excluded figure; totals take it once."""
    kept = [n for n in NAMES if n != SURVIVOR]
    config = 'exclude_re = [\n  # fixture constant\n  "replace answer",\n]\n'
    shards.plant(
        package("alpha", str(shards.tree)),
        config=config,
        listed=kept,
        shard=kept[:1],
        result=outcomes(1, 1, 0),
    )
    done, _ = shards.run_leg(**shards.shard_env("0/4"))
    assert done.returncode == 0, done.stderr
    assert shards.summary() == "0/4\t1\t1\t0\t1\twhole\n"


def test_unadopted_shard_writes_skipped(shards: Shards) -> None:
    """No config: skipped by name, and the totals are told so."""
    shards.track("Cargo.toml", "[workspace]\n")
    done, _ = shards.run_leg(**shards.shard_env("2/4"))
    assert done.returncode == 0, done.stderr
    assert shards.summary() == "2/4\tskipped\n"


def test_red_run_writes_no_counts(shards: Shards) -> None:
    """A run that judged nothing reports nothing; the totals name it."""
    shards.plant(package("alpha", str(shards.tree)), shard=NAMES[:1])
    done, _ = shards.run_leg(PLANT_STATUS="4", **shards.shard_env("0/4"))
    assert done.returncode == 1
    assert not (shards.tmp / "summary" / "rust.tsv").exists()


@pytest.mark.parametrize("bad", ["4/4", "1", "a/4", "1/0"])
def test_malformed_shard_is_red(shards: Shards, bad: str) -> None:
    """Shard k must be below n: a typo never quietly runs everything n times."""
    shards.plant(package("alpha", str(shards.tree)))
    done, log = shards.run_leg(MUTANTS_SHARD=bad)
    assert done.returncode == 1
    want = [f"{LABEL}: MUTANTS_SHARD='{bad}' is not k/n with k < n"]
    assert done.stderr.splitlines() == want
    assert log == list[str]()


# mutants_rust.py's reader of `.cargo/mutants.toml`, on planted text (#1486).
def test_every_entry_under_its_reason_is_clean() -> None:
    """A `#` line above each entry, blank lines between allowed."""
    text = (
        "exclude_re = [\n"
        "  # the width is a constant\n"
        '  "replace width",\n'
        "  # a log line\n"
        "\n"
        '  "delete log",\n'
        "]\n"
    )
    assert helper.unexplained(text) == list[tuple[int, str, str]]()


def test_an_entry_with_no_reason_is_named_with_its_line() -> None:
    """The second entry follows the first, not a comment."""
    text = 'exclude_re = [\n  # why\n  "one",\n  "two",\n]\n'
    want = [(4, "exclude_re", '"two"')]
    assert helper.unexplained(text) == want


def test_an_entry_on_the_opening_line_has_nowhere_for_a_reason() -> None:
    """`key = ["x"]` is named as such, for either key."""
    text = 'exclude_globs = ["src/gen.rs"]\n'
    want = [(1, "exclude_globs", "an entry on the opening line")]
    assert helper.unexplained(text) == want


def test_a_closing_bracket_on_the_entry_ends_the_list() -> None:
    """After `"x"]`, a quoted line is not an entry."""
    text = 'exclude_re = [\n  # why\n  "x"]\n"not = an entry"\n'
    assert helper.unexplained(text) == list[tuple[int, str, str]]()


def test_other_keys_are_not_read() -> None:
    """Only the two exclusion lists carry the rule."""
    text = 'additional_cargo_args = [\n  "--all-features",\n]\n'
    assert helper.unexplained(text) == list[tuple[int, str, str]]()


# mutants_rust.py's other readers, directly (#1486).
def test_default_major_is_the_one_pg_feature() -> None:
    """One pgNN in `default` is the major; none or two is no answer."""
    meta = {
        "packages": [
            {"name": "one", "features": {"default": ["pg17", "serde"]}},
            {"name": "two", "features": {"default": ["pg17", "pg18"]}},
            {"name": "none", "features": {"default": []}},
            {"name": "bare", "features": {}},
        ]
    }
    packages = helper.read_metadata(meta)
    assert helper.default_major(packages, "one") == "pg17"
    assert not helper.default_major(packages, "two")
    assert not helper.default_major(packages, "none")
    assert not helper.default_major(packages, "bare")
    assert not helper.default_major(packages, "absent")


def test_metadata_of_the_wrong_shape_is_refused_by_name() -> None:
    """Each field the default major reads, where it is."""
    rows: tuple[tuple[object, str], ...] = (
        ([], "the metadata is not an object"),
        ({"packages": {}}, "packages is not a list"),
        ({"packages": [3]}, "packages[0] is not an object"),
        ({"packages": [{"name": 3}]}, "packages[0].name is not a string"),
        (
            {"packages": [{"name": "a", "features": ["default"]}]},
            "packages[0].features is not an object",
        ),
        (
            {"packages": [{"name": "a", "features": {"default": "pg17"}}]},
            "packages[0].features.default is not a list",
        ),
        (
            {"packages": [{"name": "a", "features": {"default": [17]}}]},
            "packages[0].features.default[0] is not a string",
        ),
    )
    for value, why in rows:
        with pytest.raises(decoded.UndecodableError) as caught:
            helper.read_metadata(value)
        assert str(caught.value) == why


def test_default_major_refuses_metadata_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The step says so on stderr and exits 1; the leg stops with it."""
    monkeypatch.setattr("sys.stdin", io.StringIO('{"packages": 1}'))
    status = helper.main(["default-major", "a"])
    err = capfd.readouterr().err
    assert status == 1
    assert err == f"{LABEL}: cargo metadata: packages is not a list\n"


def test_default_major_prints_the_crates_major(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The step prints the one pgNN in the crate's default features."""
    meta = {"packages": [{"name": "a", "features": {"default": ["pg17"]}}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(meta)))
    assert helper.main(["default-major", "a"]) == 0
    assert capfd.readouterr().out == "pg17\n"


def test_a_listing_counts_lines_with_anything_on_them() -> None:
    """Blank and whitespace-only lines are not mutants."""
    assert helper.count_listed("a\n\n  \t\nb\n") == LISTED
    assert helper.count_listed("") == 0


def test_excluded_is_the_unconfigured_minus_the_configured() -> None:
    """Never the shard's figure."""
    assert helper.Plan(every=7, listed=5, here=1).excluded == EXCLUDED


def test_a_timeout_is_killed() -> None:
    """Caught plus timeout is killed; missed is survived."""
    counts = {"total_mutants": 6, "caught": 3, "timeout": 2, "missed": 1}
    assert helper.tally(counts) == (6, 5, 1)


def test_a_count_not_stated_is_named_never_zero() -> None:
    """Absent, null, a string or a bool is not a stated count."""
    counts = {"total_mutants": 6, "caught": None, "timeout": "2", "missed": True}
    want = ["caught", "timeout", "missed"]
    assert helper.missing_counts(counts) == want
    every = list(helper.COUNTS)
    assert helper.missing_counts([]) == every
    complete = dict.fromkeys(helper.COUNTS, 0)
    assert helper.missing_counts(complete) == list[str]()


def test_red_is_a_survivor_or_a_run_that_judged_nothing() -> None:
    """Clean is neither."""
    assert not helper.red(helper.Verdict(total=3, killed=3))
    assert helper.red(helper.Verdict(survived=1))
    assert helper.red(helper.Verdict(runs=["x: exited 1"]))


def test_a_pattern_escapes_what_rusts_regex_escape_escapes() -> None:
    """Every meta character behind a backslash; `<`, `>`, `:` and `!` bare."""
    assert helper.escape(r"a.b+c*?()|[]{}^$#&-~\\") == (
        r"a\.b\+c\*\?\(\)\|\[\]\{\}\^\$\#\&\-\~\\\\"
    )
    assert helper.escape("-> Option<u8> with None!:") == (r"\-> Option<u8> with None!:")


def test_a_record_reads_its_mutation_up_to_the_first_colon_and_space() -> None:
    """`String::new()` is part of the mutation; `: ` begins the reason."""
    hit = (
        "crates/a/src/x.rs:7:    v // mutants:equivalent replace f -> String"
        " with String::new(): the caller trims: always empty here\n"
    )
    (record,) = helper.read_records(hit + "\n")
    assert record.site == SITE
    assert record.mutation == "replace f -> String with String::new()"
    assert record.reason == "the caller trims: always empty here"
    assert record.pattern == (
        r"^crates/a/src/x\.rs:7:[0-9]+:"
        r" replace f \-> String with String::new\(\)( in .*)?$"
    )


def test_a_derived_pattern_matches_its_site_with_or_without_a_function() -> None:
    """Any column, an optional ` in <fn>`, and nothing else."""
    (record,) = helper.read_records(f"src/lib.rs:19:  {MARK} {EQUAL}: why\n")
    pattern = re.compile(record.pattern)
    for name in (
        "src/lib.rs:20:9: replace == with != in check",
        "src/lib.rs:20:13: replace == with !=",
    ):
        assert pattern.search(name) is not None, name
    for name in (
        "src/lib.rs:21:9: replace == with != in check",
        "src/lib.rs:200:9: replace == with != in check",
        "xsrc/lib.rs:20:9: replace == with != in check",
        "src/libXrs:20:9: replace == with != in check",
        "src/lib.rs:20:9: replace == with !== in check",
    ):
        assert pattern.search(name) is None, name


def test_a_malformed_record_is_named_for_its_first_fault() -> None:
    """No mutation outranks no reason; a colon or blanks are no reason."""
    rows = (
        (f"{MARK}", "names no mutation"),
        (f"{MARK} : why", "names no mutation"),
        (f"{MARK}   ", "names no mutation"),
        (f"{MARK} {EQUAL}", f"{EQUAL} gives no reason"),
        (f"{MARK} {EQUAL}:", f"{EQUAL} gives no reason"),
        (f"{MARK} {EQUAL}:  \t", f"{EQUAL} gives no reason"),
    )
    for comment, fault in rows:
        found = helper.read_records(f"src/lib.rs:19:  {comment}\n")
        want = [f"src/lib.rs:19: {MARK} {fault}"]
        assert helper.malformed(found) == want
    sound = helper.read_records(f"src/lib.rs:19: {MARK} {EQUAL}: why\n")
    assert helper.malformed(sound) == list[str]()


def test_records_prints_each_pattern_when_every_record_is_sound(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """One pattern a line, in the grep's order; blank input is no record."""
    grep = (
        f"src/lib.rs:19:  {MARK} {EQUAL}: why\n"
        f"src/lib.rs:20:  x {MARK} delete -: why\n\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(grep))
    status = helper.main(["records"])
    out = capfd.readouterr().out
    assert status == 0
    want = [DERIVED, r"^src/lib\.rs:20:[0-9]+: delete \-( in .*)?$"]
    assert out.splitlines() == want


def test_a_pinned_entry_is_an_exclude_re_entry_holding_a_position() -> None:
    """Digits between colons, in exclude_re only."""
    text = (
        "exclude_re = [\n"
        "  # one\n"
        '  "a\\\\.rs:20:9: replace",\n'
        "  # two\n"
        '  "a\\\\.rs:[0-9]+:[0-9]+: replace",\n'
        "  # three\n"
        '  "a:1: b",\n'
        "]\n"
        "exclude_globs = [\n"
        "  # four\n"
        '  "x:1:2:",\n'
        "]\n"
    )
    want = [(3, '"a\\\\.rs:20:9: replace"')]
    assert helper.pinned(text) == want


def test_only_0_2_and_3_are_judged() -> None:
    """4 is a red baseline; 1 is a failure to run."""
    want = frozenset({0, 2, 3})
    assert want == helper.JUDGED


# The plan and verdict steps in this process, over the files the shell
# writes: the rows above run the shell, whose steps they do not measure.
def test_an_unexplained_exclusion_is_red_by_name(
    capfd: pytest.CaptureFixture[str], configured: Callable[[str], None]
) -> None:
    """An entry with no # line above it: named, with what to write."""
    configured('exclude_re = [\n  "tests::",\n]\n')
    status = helper.main(["reasons", CONFIG])
    _out, err = capfd.readouterr()
    entry = f'{CONFIG}:2: exclude_re entry "tests::"'
    assert (status, err) == (
        1,
        f"{LABEL}: {entry} has no # reason line above it\n{REASON}\n",
    )


def listings(tmp_path: Path, **texts: str) -> list[str]:
    """Write each listing as `<name>.txt` and spell the `plan` step's options.

    Returns:
        `--<name> <path>` for each.

    """
    argv: list[str] = []
    for name, text in texts.items():
        path = tmp_path / f"{name}.txt"
        path.write_text(text, encoding="utf-8")
        argv += [f"--{name}", str(path)]
    return argv


def test_the_plan_counts_and_records_a_shard(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Three in all, two listed, one here: one excluded, said and recorded."""
    every, listed, here = "a\nb\n\t\nc", "a\nb\n", "a\n"
    argv = listings(tmp_path, all=every, listed=listed, here=here)
    out_dir = tmp_path / "run"
    status = helper.main([
        "plan",
        "--name=demo",
        "--where=,pkg",
        f"--dir={out_dir}",
        *argv,
    ])
    assert (status, capfd.readouterr().out) == (
        0,
        f"{LABEL}: mutating demo,pkg (1 mutants, 1 excluded)\n",
    )
    assert (out_dir / "counts").read_text(encoding="utf-8") == "3\t2\t1\n"
    assert not (out_dir / "empty").exists()


def test_an_empty_shard_says_so_and_marks_its_run(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """No --here, nothing listed: nothing to run, `empty` written."""
    argv = listings(tmp_path, all="a\n", listed="")
    out_dir = tmp_path / "run"
    status = helper.main(["plan", "--name=demo", "--where=", f"--dir={out_dir}", *argv])
    said = [
        f"{LABEL}: mutating demo (0 mutants, 1 excluded)",
        f"{LABEL}: demo: no mutants in this shard, nothing to run",
    ]
    assert (status, capfd.readouterr().out.splitlines()) == (0, said)
    assert (out_dir / "empty").is_file()


class Scratch:
    """The shell's scratch directory: its log of runs, and each run's files."""

    def __init__(self, root: Path) -> None:
        """Start with no run logged."""
        self.root = root
        self.log: list[str] = []

    def red(self, message: str) -> None:
        """Log a run the shell itself found red."""
        self.log.append(f"red\t{message}")

    def run(self, name: str, files: dict[str, str]) -> Path:
        """Log a run and write its files, by path under its directory.

        Returns:
            The run's directory.

        """
        run = self.root / name
        run.mkdir(parents=True)
        for relative, text in files.items():
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.log.append(f"run\t{name}\t{run}")
        return run

    def verdict(self, *, where: str = "") -> int:
        """Write the log and run the verdict step.

        Returns:
            Its exit status.

        """
        text = "".join(f"{line}\n" for line in self.log)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "runs.tsv").write_text(text, encoding="utf-8")
        return helper.main([
            "verdict",
            str(self.root),
            f"--where={where}",
            "--shard=0/1",
        ])


@fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scratch:
    """Give a row its scratch directory, the summary written beside it.

    Returns:
        The row's Scratch.

    """
    monkeypatch.setenv("MUTANTS_SUMMARY_DIR", str(tmp_path / "summary"))
    return Scratch(tmp_path / "scratch")


def judged(total: int, caught: int, missed: int, *, timeout: int = 0) -> dict[str, str]:
    """Spell a run cargo-mutants judged: its counts, listing and outcomes.

    Returns:
        The run's files, by path under its directory.

    """
    return {
        "counts": "3\t2\t2\n",
        "all.txt": "".join(f"{name}\n" for name in NAMES),
        "status": "2\n" if missed else "0\n",
        "mutants.out/outcomes.json": json.dumps(
            outcomes(total, caught, missed, timeout)
        ),
        "mutants.out/missed.txt": f"{SURVIVOR}\n" if missed else "",
    }


def summary(tmp_path: Path) -> str | None:
    """Read the shard's summary line, where the verdict wrote one.

    Returns:
        The rust.tsv text, or None.

    """
    path = tmp_path / "summary" / "rust.tsv"
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_a_clean_run_is_green_and_summarised(
    scratch: Scratch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Every mutant caught or timed out: green, the counts written."""
    scratch.run("a", judged(3, 1, 0, timeout=1))
    status = scratch.verdict(where=",pkg")
    out, err = capfd.readouterr()
    assert (status, err) == (0, "")
    assert out == f"{LABEL}:pkg: 3 mutants, 2 killed, 0 survived, 1 excluded\n"
    assert summary(tmp_path) == "0/1\t3\t2\t0\t1\twhole\n"


def test_no_run_logged_is_nothing_judged_and_green(
    scratch: Scratch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A scratch with no log: zero of everything, still summarised."""
    scratch.root.mkdir()
    status = helper.main(["verdict", str(scratch.root), "--where=", "--shard=1/2"])
    assert (status, capfd.readouterr().out) == (
        0,
        f"{LABEL}: 0 mutants, 0 killed, 0 survived, 0 excluded\n",
    )
    assert summary(tmp_path) == "1/2\t0\t0\t0\t0\twhole\n"


def test_a_survivor_is_printed_and_red(
    scratch: Scratch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A missed mutant: named from missed.txt, then the red finding."""
    scratch.run("a", judged(3, 1, 1))
    status = scratch.verdict()
    out, err = capfd.readouterr()
    assert status == 1
    assert out.splitlines()[0] == f"{LABEL}: survived: {SURVIVOR}"
    assert err.splitlines() == RED


@pytest.mark.parametrize(
    ("files", "said"),
    [
        (
            {"status": "4\n"},
            "a: cargo mutants exited 4 — nothing judged (a red baseline is exit 4)",
        ),
        ({"status": "0\n"}, "a: cargo mutants wrote no outcomes.json"),
        (
            {"status": "0\n", "mutants.out/outcomes.json": "{"},
            "a: outcomes.json: ",
        ),
        (
            {"status": "0\n", "mutants.out/outcomes.json": '{"total_mutants": 1}'},
            "a: outcomes.json states no caught",
        ),
    ],
    ids=["a red baseline", "no outcomes", "undecodable outcomes", "a count unstated"],
)
def test_a_run_that_judged_nothing_is_red_and_unsummarised(
    files: dict[str, str],
    said: str,
    scratch: Scratch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Each run that judged nothing is a finding; no summary is written."""
    listing = {"counts": "3\t3\t3\n", "all.txt": "".join(f"{n}\n" for n in NAMES)}
    scratch.run("a", listing | files)
    assert scratch.verdict() == 1
    err = capfd.readouterr().err
    assert err.startswith(f"{LABEL}: {said}")
    assert err.splitlines()[-len(RED) :] == RED
    assert summary(tmp_path) is None


def test_a_run_the_shell_found_red_is_named(
    scratch: Scratch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A `red` line in the log is a finding as written; a blank one is none."""
    scratch.red("pkg: cargo metadata failed")
    scratch.log.append("")
    assert scratch.verdict() == 1
    assert capfd.readouterr().err.splitlines()[0] == (
        f"{LABEL}: pkg: cargo metadata failed"
    )


def test_an_empty_shard_counts_its_exclusions_and_judges_nothing(
    scratch: Scratch, capfd: pytest.CaptureFixture[str]
) -> None:
    """`empty` in a run: its exclusions counted, no status read."""
    scratch.run("a", {"counts": "4\t1\t0\n", "all.txt": "x:1:1: y", "empty": ""})
    assert scratch.verdict() == 0
    assert capfd.readouterr().out == (
        f"{LABEL}: 0 mutants, 0 killed, 0 survived, 3 excluded\n"
    )


def test_a_record_matching_nothing_is_red_after_the_runs(
    scratch: Scratch, capfd: pytest.CaptureFixture[str]
) -> None:
    """records.txt holds a record in no mutated file: named, red."""
    scratch.run("a", judged(3, 3, 0))
    (scratch.root / "records.txt").write_text(
        f"src/other.rs:2:    {MARK} delete !: why\n", encoding="utf-8"
    )
    assert scratch.verdict() == 1
    assert capfd.readouterr().err.splitlines()[0] == (
        f"{LABEL}: src/other.rs:2: {MARK} delete ! is in no mutated file"
    )


def test_a_name_off_the_listing_form_holds_no_record() -> None:
    """A listing line that is no `path:line:col:` name adds no file."""
    found = helper.read_records(f"src/lib.rs:19:    {MARK} {EQUAL}: why\n")
    want = [f"src/lib.rs:19: {MARK} {EQUAL} is in no mutated file"]
    assert helper.unmatched(found, ["a listing banner"]) == want

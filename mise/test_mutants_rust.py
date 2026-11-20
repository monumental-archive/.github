#!/usr/bin/env python3
"""Table tests for audit:mutants' Rust leg, mise/mutants-rust.sh (#1355).

The leg runs for real in a planted git tree, with `cargo` as a stub on
PATH. The stub answers `metadata` with the workspace the row plants,
`mutants --list` with the mutant names the row plants (all of them
under `--no-config`, the configured subset otherwise), and a mutation
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

stdlib `unittest`, the belt's one test idiom. Run through the gate as
`mise run test`, which `ci` collects.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import decoded
import hermetic
import mutants_rust as helper

BELT = Path(__file__).resolve().parent
SCRIPT = BELT / "mutants-rust.sh"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
LABEL = "audit:mutants: rust"
CONFIG = ".cargo/mutants.toml"


CARGO_STUB = """#!/usr/bin/env bash
printf 'cargo %s\\n' "$*" >> "${PLANT_LOG}"
case "$*" in
  "metadata --no-deps --format-version 1 --locked") cat "${PLANT_METADATA}" ;;
  "mutants --list --no-config "*) cat "${PLANT_ALL}" ;;
  "mutants --list --shard "*) cat "${PLANT_SHARD}" ;;
  "mutants --list "*) cat "${PLANT_LISTED}" ;;
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
# The real org_pgrx_with_icu, read out of pgrx-env.sh rather than
# written again here: its subshell is the property the rows rely on.
WITH_ICU = re.search(
    r"^org_pgrx_with_icu\(\) \{.*?^\}\n",
    (BELT / "pgrx-env.sh").read_text(encoding="utf-8"),
    re.MULTILINE | re.DOTALL,
)
if WITH_ICU is None:
    msg = "org_pgrx_with_icu is gone from pgrx-env.sh"
    raise LookupError(msg)
PGRX_ENV_STUB = """
org_pgrx_provision() {
  {
    printf 'provision'
    (($# == 0)) || printf ' [%s]' "$@"
    printf '\\n'
  } >> "${PLANT_LOG}"
  return "${PLANT_PROVISION:-0}"
}
# The per-major ICU headers (#1774): the check answers PLANT_ICU_CHECK,
# and the headers are a mark the stub cargo could show.
org_pgrx_darwin_icu_check() { return "${PLANT_ICU_CHECK:-0}"; }
org_pgrx_darwin_icu() { export BINDGEN_EXTRA_CLANG_ARGS="-Iicu-pg$1"; }
""" + WITH_ICU.group(0)

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
RED = [
    f"{LABEL}: red — kill each survivor with an assertion, or record it in {CONFIG}",
    "  as an exclude_re entry under a # line saying why the mutant is equivalent",
]


def clean_env() -> dict[str, str]:
    """Copy the environment without git's own variables.

    Returns:
        os.environ minus every GIT_* key.

    """
    return hermetic.env()


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


class Planted(unittest.TestCase):
    """A planted tree, a stub PATH and a belt with stub provisioning."""

    @override
    def setUp(self) -> None:
        """Plant a git tree, the stubs and the belt the leg reads."""
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
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
        belt = hermetic.belt(self.tmp / "belt", stubs={"pgrx-env.sh": PGRX_ENV_STUB})
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
        self.env = clean_env() | {
            "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}",
            "ORG_BELT_DIR": str(belt),
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
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, str(SCRIPT)],
            cwd=self.tree,
            env=self.env | env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, self.log.read_text(encoding="utf-8").splitlines()

    @staticmethod
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


class Adoption(Planted):
    """Adoption is the tracked config file; without it, a named skip."""

    def test_no_config_skips_clean_by_name(self) -> None:
        """No .cargo/mutants.toml: exit 0, named, nothing called."""
        self.track("Cargo.toml", "[workspace]\n")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [f"{LABEL}: no {CONFIG} tracked, skipped"]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_untracked_config_is_not_adoption(self) -> None:
        """A config on disk that git does not track adopts nothing."""
        self.track("Cargo.toml", "[workspace]\n")
        (self.tree / ".cargo").mkdir()
        (self.tree / CONFIG).write_text("", encoding="utf-8")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [f"{LABEL}: no {CONFIG} tracked, skipped"]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(log, list[str]())


class Verdict(Planted):
    """Survivors, exclusions and the summary line."""

    def test_clean(self) -> None:
        """Every mutant killed: exit 0 and the summary line."""
        self.plant(package("alpha", str(self.tree)), result=outcomes(3, 3, 0))
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
            f"{LABEL}: 3 mutants, 3 killed, 0 survived, 0 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(done.stderr, "")
        want = [
            METADATA,
            "cargo mutants --list --no-config --package alpha",
            "cargo mutants --list --package alpha",
            "cargo mutants --output OUT --package alpha",
        ]
        self.assertEqual(self.calls(log), want)

    def test_one_survivor_is_red_with_file_and_line(self) -> None:
        """A survivor: red, printed as file:line:col and the mutation."""
        self.plant(
            package("alpha", str(self.tree)),
            result=outcomes(3, 2, 1),
            missed=[SURVIVOR],
        )
        done, _ = self.run_leg(PLANT_STATUS="2")
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
            f"{LABEL}: survived: {SURVIVOR}",
            f"{LABEL}: 3 mutants, 2 killed, 1 survived, 0 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertEqual(done.stderr.splitlines(), RED)

    def test_excluded_survivor_with_reason_is_counted_excluded(self) -> None:
        """An exclude_re entry under a # reason: exit 0, counted excluded."""
        config = (
            "exclude_re = [\n"
            "  # the answer is a fixture constant no caller can observe\n"
            '  "replace answer -> u32",\n'
            "]\n"
        )
        kept = [n for n in NAMES if n != SURVIVOR]
        self.plant(
            package("alpha", str(self.tree)),
            config=config,
            listed=kept,
            result=outcomes(2, 2, 0),
        )
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: mutating workspace (2 mutants, 1 excluded)",
            f"{LABEL}: 2 mutants, 2 killed, 0 survived, 1 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)

    def test_timeout_counts_as_killed(self) -> None:
        """Exit 3 (timeouts) is a result: a hung suite noticed the mutant."""
        self.plant(package("alpha", str(self.tree)), result=outcomes(3, 2, 0, 1))
        done, _ = self.run_leg(PLANT_STATUS="3")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(
            f"{LABEL}: 3 mutants, 3 killed, 0 survived, 0 excluded",
            done.stdout.splitlines(),
        )

    def test_red_baseline_judges_nothing(self) -> None:
        """Exit 4 (the unmutated suite failed) is red, never a clean zero."""
        self.plant(package("alpha", str(self.tree)))
        done, _ = self.run_leg(PLANT_STATUS="4")
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: workspace: cargo mutants exited 4"
                " — nothing judged (a red baseline is exit 4)"
            ),
            *RED,
        ]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_an_outcomes_json_that_does_not_decode_is_red_by_name(self) -> None:
        """A document decoded's loader refuses is not judged, never a crash."""
        self.plant(package("alpha", str(self.tree)), result=outcomes(3, 3, 0))
        self.files["outcomes.json"].write_text(
            '{"missed": 0, "missed": 1}', encoding="utf-8"
        )
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [f'{LABEL}: workspace: outcomes.json: duplicate key "missed"', *RED]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_a_count_outcomes_json_does_not_state_is_red_by_name(self) -> None:
        """No `timeout` in outcomes.json: not judged, never read as zero."""
        result = outcomes(3, 3, 0)
        del result["timeout"]
        self.plant(package("alpha", str(self.tree)), result=result)
        done, _ = self.run_leg(MUTANTS_SUMMARY_DIR=str(self.tmp / "summary"))
        self.assertEqual(done.returncode, 1)
        want = [f"{LABEL}: workspace: outcomes.json states no timeout", *RED]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertIn(
            f"{LABEL}: 0 mutants, 0 killed, 0 survived, 0 excluded",
            done.stdout.splitlines(),
        )
        self.assertFalse((self.tmp / "summary" / "rust.tsv").exists())


class Reasons(Planted):
    """Every exclusion carries its reason, in the repository's own file."""

    def test_exclude_re_without_reason_is_red_by_name(self) -> None:
        """An entry with no # line above it: red, by line and entry."""
        config = (
            "exclude_re = [\n"
            "  # the banner is cosmetic\n"
            '  "replace banner",\n'
            '  "replace answer -> u32",\n'
            "]\n"
        )
        self.plant(package("alpha", str(self.tree)), config=config)
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: {CONFIG}:4: exclude_re entry"
                ' "replace answer -> u32" has no # reason line above it'
            ),
            REASON,
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())

    def test_exclude_globs_needs_a_reason_too(self) -> None:
        """A whole-file exclusion is an exclusion: the same rule."""
        config = 'exclude_globs = [\n  "src/generated.rs",\n]\n'
        self.plant(package("alpha", str(self.tree)), config=config)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: {CONFIG}:2: exclude_globs entry"
                ' "src/generated.rs" has no # reason line above it'
            ),
            REASON,
        ]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_entry_on_the_opening_line_is_red(self) -> None:
        """`exclude_re = ["x"]` leaves nowhere to write the reason."""
        config = '# cosmetic\nexclude_re = ["replace banner"]\n'
        self.plant(package("alpha", str(self.tree)), config=config)
        done, _ = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: {CONFIG}:2: exclude_re entry"
                " an entry on the opening line has no # reason line above it"
            ),
            REASON,
        ]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_mutants_skip_attribute_is_refused(self) -> None:
        """#[mutants::skip] is an exclusion with nowhere for the reason."""
        self.plant(package("alpha", str(self.tree)), result=outcomes(3, 3, 0))
        self.track("alpha/src/lib.rs", "#[mutants::skip]\nfn f() {}\n")
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: #[mutants::skip] in the tree:",
            "  alpha/src/lib.rs:1:#[mutants::skip]",
            (
                f"  record the site as an exclude_re entry in {CONFIG},"
                " under a # reason line"
            ),
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        self.assertEqual(log, list[str]())


class Pgrx(Planted):
    """A pgrx extension: its own run, under its default major, provisioned."""

    def test_pgrx_crate_runs_under_its_default_major(self) -> None:
        """Every member mutated; the extension under pg18 with pg_test."""
        root = str(self.tree)
        self.plant(
            package("alpha", root),
            package("ext", root, default=["pg18"]),
            result=outcomes(3, 3, 0),
        )
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: mutating workspace (3 mutants, 0 excluded)",
            (
                f"{LABEL}: ext is a pgrx extension,"
                " mutated under its default major, pg18"
            ),
            f"{LABEL}: mutating ext-pg18 (3 mutants, 0 excluded)",
            f"{LABEL}: 6 mutants, 6 killed, 0 survived, 0 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        want = [
            METADATA,
            "cargo mutants --list --no-config --package alpha",
            "cargo mutants --list --package alpha",
            "cargo mutants --output OUT --package alpha",
            "provision",
            "cargo mutants --list --no-config --package ext --features pg_test",
            "cargo mutants --list --package ext --features pg_test",
            "cargo mutants --output OUT --package ext --features pg_test",
        ]
        self.assertEqual(self.calls(log), want)

    def test_pgrx_crate_without_a_single_default_major_is_red(self) -> None:
        """No pgNN in `default`: red by name, never a guessed major."""
        self.plant(package("ext", str(self.tree), default=[]))
        done, log = self.run_leg()
        self.assertEqual(done.returncode, 1)
        want = [
            (
                f"{LABEL}: ext: a pgrx extension whose default features name"
                " no single pgNN — it is mutated under its default major"
            ),
            *RED,
        ]
        self.assertEqual(done.stderr.splitlines(), want)
        provisioned = [c for c in log if c.startswith("provision")]
        self.assertEqual(provisioned, list[str]())

    def test_failed_provisioning_is_red(self) -> None:
        """No Postgres: the extension is red, never skipped."""
        self.plant(package("ext", str(self.tree), default=["pg18"]))
        done, _ = self.run_leg(PLANT_PROVISION="1")
        self.assertEqual(done.returncode, 1)
        want = [f"{LABEL}: ext at pg18: no Postgres could be provisioned", *RED]
        self.assertEqual(done.stderr.splitlines(), want)

    def test_missing_icu_headers_are_red(self) -> None:
        """No ICU headers for the default major: red by name, never mutated."""
        self.plant(package("ext", str(self.tree), default=["pg18"]))
        done, log = self.run_leg(PLANT_ICU_CHECK="1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.splitlines(),
            [f"{LABEL}: ext at pg18: no ICU headers for it are pinned (above)", *RED],
        )
        self.assertEqual([c for c in log if c.startswith("cargo mutants")], [])


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

    def test_shard_runs_its_slice_and_reports_its_counts(self) -> None:
        """--shard reaches the run; the summary line and file name the shard."""
        self.plant(
            package("alpha", str(self.tree)),
            shard=NAMES[:2],
            result=outcomes(2, 1, 1),
            missed=[SURVIVOR],
        )
        done, log = self.run_leg(PLANT_STATUS="2", **self.shard_env("1/4"))
        self.assertEqual(done.returncode, 1)
        want = [
            f"{LABEL}: mutating workspace, shard 1/4 (2 mutants, 0 excluded)",
            f"{LABEL}: survived: {SURVIVOR}",
            f"{LABEL}: shard 1/4: 2 mutants, 1 killed, 1 survived, 0 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        want = [
            METADATA,
            "cargo mutants --list --no-config --package alpha",
            "cargo mutants --list --package alpha",
            "cargo mutants --list --shard 1/4 --package alpha",
            "cargo mutants --output OUT --shard 1/4 --package alpha",
        ]
        self.assertEqual(self.calls(log), want)
        self.assertEqual(self.summary(), "1/4\t2\t1\t1\t0\n")

    def test_empty_shard_is_green_by_name(self) -> None:
        """Measured: an empty shard exits 0 and writes no outcomes.json."""
        self.plant(package("alpha", str(self.tree)), shard=[])
        done, log = self.run_leg(**self.shard_env("3/4"))
        self.assertEqual(done.returncode, 0, done.stderr)
        want = [
            f"{LABEL}: mutating workspace, shard 3/4 (0 mutants, 0 excluded)",
            (
                f"{LABEL}: workspace, shard 3/4:"
                " no mutants in this shard, nothing to run"
            ),
            f"{LABEL}: shard 3/4: 0 mutants, 0 killed, 0 survived, 0 excluded",
        ]
        self.assertEqual(done.stdout.splitlines(), want)
        self.assertNotIn(
            "cargo mutants --output OUT --shard 3/4 --package alpha", self.calls(log)
        )
        self.assertEqual(self.summary(), "3/4\t0\t0\t0\t0\n")

    def test_excluded_is_the_whole_population(self) -> None:
        """Every shard reports the same excluded figure; totals take it once."""
        kept = [n for n in NAMES if n != SURVIVOR]
        config = 'exclude_re = [\n  # fixture constant\n  "replace answer",\n]\n'
        self.plant(
            package("alpha", str(self.tree)),
            config=config,
            listed=kept,
            shard=kept[:1],
            result=outcomes(1, 1, 0),
        )
        done, _ = self.run_leg(**self.shard_env("0/4"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.summary(), "0/4\t1\t1\t0\t1\n")

    def test_unadopted_shard_writes_skipped(self) -> None:
        """No config: skipped by name, and the totals are told so."""
        self.track("Cargo.toml", "[workspace]\n")
        done, _ = self.run_leg(**self.shard_env("2/4"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.summary(), "2/4\tskipped\n")

    def test_red_run_writes_no_counts(self) -> None:
        """A run that judged nothing reports nothing; the totals name it."""
        self.plant(package("alpha", str(self.tree)), shard=NAMES[:1])
        done, _ = self.run_leg(PLANT_STATUS="4", **self.shard_env("0/4"))
        self.assertEqual(done.returncode, 1)
        self.assertFalse((self.tmp / "summary" / "rust.tsv").exists())

    def test_malformed_shard_is_red(self) -> None:
        """Shard k must be below n: a typo never quietly runs everything n times."""
        self.plant(package("alpha", str(self.tree)))
        for bad in ("4/4", "1", "a/4", "1/0"):
            with self.subTest(bad=bad):
                done, log = self.run_leg(MUTANTS_SHARD=bad)
                self.assertEqual(done.returncode, 1)
                want = [f"{LABEL}: MUTANTS_SHARD='{bad}' is not k/n with k < n"]
                self.assertEqual(done.stderr.splitlines(), want)
                self.assertEqual(log, list[str]())


class ReasonRule(unittest.TestCase):
    """mutants_rust.py's reader of `.cargo/mutants.toml`, on planted text (#1486)."""

    def test_every_entry_under_its_reason_is_clean(self) -> None:
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
        self.assertEqual(helper.unexplained(text), list[tuple[int, str, str]]())

    def test_an_entry_with_no_reason_is_named_with_its_line(self) -> None:
        """The second entry follows the first, not a comment."""
        text = 'exclude_re = [\n  # why\n  "one",\n  "two",\n]\n'
        want = [(4, "exclude_re", '"two"')]
        self.assertEqual(helper.unexplained(text), want)

    def test_an_entry_on_the_opening_line_has_nowhere_for_a_reason(self) -> None:
        """`key = ["x"]` is named as such, for either key."""
        text = 'exclude_globs = ["src/gen.rs"]\n'
        want = [(1, "exclude_globs", "an entry on the opening line")]
        self.assertEqual(helper.unexplained(text), want)

    def test_a_closing_bracket_on_the_entry_ends_the_list(self) -> None:
        """After `"x"]`, a quoted line is not an entry."""
        text = 'exclude_re = [\n  # why\n  "x"]\n"not = an entry"\n'
        self.assertEqual(helper.unexplained(text), list[tuple[int, str, str]]())

    def test_other_keys_are_not_read(self) -> None:
        """Only the two exclusion lists carry the rule."""
        text = 'additional_cargo_args = [\n  "--all-features",\n]\n'
        self.assertEqual(helper.unexplained(text), list[tuple[int, str, str]]())


class Readers(unittest.TestCase):
    """mutants_rust.py's other readers, directly (#1486)."""

    def test_default_major_is_the_one_pg_feature(self) -> None:
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
        self.assertEqual(helper.default_major(packages, "one"), "pg17")
        self.assertEqual(helper.default_major(packages, "two"), "")
        self.assertEqual(helper.default_major(packages, "none"), "")
        self.assertEqual(helper.default_major(packages, "bare"), "")
        self.assertEqual(helper.default_major(packages, "absent"), "")

    def test_metadata_of_the_wrong_shape_is_refused_by_name(self) -> None:
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
            with self.subTest(value=value):
                with self.assertRaises(decoded.UndecodableError) as caught:
                    helper.read_metadata(value)
                self.assertEqual(str(caught.exception), why)

    def test_default_major_refuses_metadata_it_cannot_read(self) -> None:
        """The step says so on stderr and exits 1; the leg stops with it."""
        err = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO('{"packages": 1}')),
            contextlib.redirect_stderr(err),
        ):
            status = helper.main(["default-major", "a"])
        self.assertEqual(status, 1)
        self.assertEqual(
            err.getvalue(), f"{LABEL}: cargo metadata: packages is not a list\n"
        )

    def test_a_listing_counts_lines_with_anything_on_them(self) -> None:
        """Blank and whitespace-only lines are not mutants."""
        self.assertEqual(helper.count_listed("a\n\n  \t\nb\n"), 2)
        self.assertEqual(helper.count_listed(""), 0)

    def test_excluded_is_the_unconfigured_minus_the_configured(self) -> None:
        """Never the shard's figure."""
        self.assertEqual(helper.Plan(every=7, listed=5, here=1).excluded, 2)

    def test_a_timeout_is_killed(self) -> None:
        """Caught plus timeout is killed; missed is survived."""
        counts = {"total_mutants": 6, "caught": 3, "timeout": 2, "missed": 1}
        self.assertEqual(helper.tally(counts), (6, 5, 1))

    def test_a_count_not_stated_is_named_never_zero(self) -> None:
        """Absent, null, a string or a bool is not a stated count."""
        counts = {"total_mutants": 6, "caught": None, "timeout": "2", "missed": True}
        want = ["caught", "timeout", "missed"]
        self.assertEqual(helper.missing_counts(counts), want)
        every = list(helper.COUNTS)
        self.assertEqual(helper.missing_counts([]), every)
        complete = dict.fromkeys(helper.COUNTS, 0)
        self.assertEqual(helper.missing_counts(complete), list[str]())

    def test_red_is_a_survivor_or_a_run_that_judged_nothing(self) -> None:
        """Clean is neither."""
        self.assertFalse(helper.red(helper.Verdict(total=3, killed=3)))
        self.assertTrue(helper.red(helper.Verdict(survived=1)))
        self.assertTrue(helper.red(helper.Verdict(runs=["x: exited 1"])))

    def test_only_0_2_and_3_are_judged(self) -> None:
        """4 is a red baseline; 1 is a failure to run."""
        want = frozenset({0, 2, 3})
        self.assertEqual(helper.JUDGED, want)


if __name__ == "__main__":
    unittest.main()

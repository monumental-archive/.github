#!/usr/bin/env python3
"""Run the shipped crates `Publish` step against a stub index and cargo.

#1218: edtf v1.3.3's crates leg uploaded four members, failed on the
fifth, and every re-run then stopped on the first:

    error: crate edtf-core@1.3.3 already exists on crates.io index

The step said it was resumable and ran one `cargo publish --workspace`,
which refuses the whole workspace when any member exists. It now asks
the sparse index for each member first and excludes the ones already
there - if they are the same bytes.

WHAT RUNS. The `run:` body of the `Publish` step, cut out of
`.github/workflows/publish-rust-crate.yml` as shipped, under `bash -c`,
with stub `curl` and `cargo` first on PATH. `jq` and `sha256sum` are the
real ones. The stub `curl` answers the index from a directory laid out
like the index host, so a wrong path layout reads as 404 and fails the
"some published" row rather than passing quietly.

FIXTURES ARE REAL. Captured 2026-11-15:

  testdata/crates-index/ed/tf/edtf-core   https://index.crates.io/ed/tf/edtf-core
  testdata/crates-index/ed/tf/edtf-wasm   https://index.crates.io/ed/tf/edtf-wasm
  testdata/edtf-core-1.3.3.crate          static.crates.io, the bytes v1.3.3 uploaded
  testdata/edtf-cargo-metadata.json       `cargo metadata --no-deps` at edtf v1.3.3

The index files are byte-for-byte the host's answer. edtf-core's
carries 1.3.3 with the crate file's sha256; edtf-wasm's is the shape
the 1.3.3 burn left - a file for the crate with no 1.3.3 line, which
must read as "not published". The metadata is reduced to the fields
the step reads, and the checkout path in each package id is rewritten
to /work/edtf (consistently, so ids still match workspace_members);
nothing else is edited. `cargo package --no-verify --locked -p
edtf-core` on a fresh checkout of the v1.3.3 tag reproduced the
index's checksum exactly - the comparison the step makes is one a
genuine resume passes.

stdlib `unittest`, matching the rest of release/. Run through the gate
as `mise run test`, which `ci` collects.
"""

import hashlib
import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
WORKFLOW = CANON / ".github" / "workflows" / "publish-rust-crate.yml"
TESTDATA = CANON / "release" / "testdata"
INDEX = TESTDATA / "crates-index"
CRATE = TESTDATA / "edtf-core-1.3.3.crate"
METADATA = TESTDATA / "edtf-cargo-metadata.json"
BASH = shutil.which("bash")

CORE_CKSUM = "7001e4071709b45e67be9351b209b4465672b0ba633a143528b50f81805d428b"
USER_AGENT = (
    "monumental-archive-publish (https://github.com/monumental-archive/.github)"
)
OIDC_URL = "https://oidc.invalid/token?api-version=2.0"
CALLER_EXCLUDES = " --exclude edtf-postgres"
PUBLISH = "publish --workspace --locked --no-verify --exclude edtf-postgres"

CURL_STUB = """#!/usr/bin/env bash
# Stub `curl`: the OIDC request and the token exchange answer canned
# JSON; index.crates.io answers from STUB_INDEX, laid out like the host.
# <path>.status planted beside a path answers that status with no body.
set -euo pipefail
out=/dev/stdout
fmt=""
url=""
ua=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -w) fmt="$2"; shift ;;
    -H) if [[ $2 == "User-Agent: "* ]]; then ua="${2#User-Agent: }"; fi; shift ;;
    -X | --data) shift ;;
    https://*) url="$1" ;;
  esac
  shift
done
printf '%s\\t%s\\n' "${url}" "${ua}" >> "${STUB_LOG}/curl"
case ${url} in
  "${ACTIONS_ID_TOKEN_REQUEST_URL}"*) echo '{"value":"stub-oidc"}' ;;
  https://crates.io/api/v1/trusted_publishing/tokens) echo '{"token":"stub"}' ;;
  https://index.crates.io/*)
    rel="${url#https://index.crates.io/}"
    if [[ -f ${STUB_INDEX}/${rel}.status ]]; then
      : > "${out}"
      code=$(cat "${STUB_INDEX}/${rel}.status")
    elif [[ -f ${STUB_INDEX}/${rel} ]]; then
      cp "${STUB_INDEX}/${rel}" "${out}"
      code=200
    else
      : > "${out}"
      code=404
    fi
    if [[ ${fmt} == '%{http_code}' ]]; then printf '%s' "${code}"; fi
    ;;
  *) echo "curl stub: refusing ${url}" >&2; exit 7 ;;
esac
"""

CARGO_STUB = """#!/usr/bin/env bash
# Stub `cargo`: logs every invocation; `metadata` answers the captured
# edtf metadata with the target directory moved into the fixture;
# `package -p <name>...` copies STUB_CRATES/<name>-*.crate where cargo
# would write it; `publish` succeeds.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}/cargo"
case $1 in
  metadata) jq --arg t "${STUB_TARGET}" '.target_directory = $t' "${STUB_METADATA}" ;;
  package)
    mkdir -p "${STUB_TARGET}/package"
    while [[ $# -gt 0 ]]; do
      if [[ $1 == -p ]]; then
        cp "${STUB_CRATES}/$2"-*.crate "${STUB_TARGET}/package/"
      fi
      shift
    done
    ;;
  publish) ;;
  *) echo "cargo stub: refusing $*" >&2; exit 101 ;;
esac
"""


def publish_body() -> str:
    """Cut the `Publish` step's `run:` block out of the shipped workflow.

    Returns:
        The block, dedented, exactly as the runner would hand it to bash.

    """
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index("      - name: Publish")
    run = next(i for i in range(step, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


@dataclass
class Run:
    """What one run of the step did."""

    returncode: int
    stdout: str
    stderr: str
    cargo: list[str]
    curl: list[tuple[str, str]]

    def publishes(self) -> list[str]:
        """Pick out the `cargo publish` invocations.

        Returns:
            Each `cargo publish` argument line, in the order run.

        """
        return [c for c in self.cargo if c.startswith("publish ")]


def run_publish(
    index: dict[str, str],
    crate: bytes | None = None,
    excludes: str = CALLER_EXCLUDES,
) -> Run:
    """Run the shipped body with the index serving `index`.

    Args:
        index: index paths to serve - a fixture file's name to serve it,
            or a bare status code to answer that status.
        crate: the bytes `cargo package` produces for edtf-core 1.3.3;
            the real uploaded crate when None.
        excludes: the caller's EXCLUDE_FLAGS, as the previous step
            writes them.

    Returns:
        The run's status, output and the stubs' logs.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, log, served, crates = (tmp / n for n in ("bin", "log", "index", "c"))
        for p in (stubs, log, served, crates):
            p.mkdir()
        for name, body in (("curl", CURL_STUB), ("cargo", CARGO_STUB)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        for rel, answer in index.items():
            dest = served / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if answer.isdigit():
                dest.with_name(dest.name + ".status").write_text(answer)
            else:
                shutil.copy(INDEX / answer, dest)
        (crates / "edtf-core-1.3.3.crate").write_bytes(
            CRATE.read_bytes() if crate is None else crate
        )
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "EXCLUDE_FLAGS": excludes,
            "ORG_USER_AGENT": USER_AGENT,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "stub-request-token",
            "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
            "STUB_LOG": str(log),
            "STUB_INDEX": str(served),
            "STUB_CRATES": str(crates),
            "STUB_TARGET": str(tmp / "target"),
            "STUB_METADATA": str(METADATA),
        }
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-c", publish_body()],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp,
        )

        def read(name: str) -> list[str]:
            path = log / name
            return path.read_text().splitlines() if path.exists() else []

        return Run(
            result.returncode,
            result.stdout,
            result.stderr,
            read("cargo"),
            [tuple(line.split("\t", 1)) for line in read("curl")],
        )


def index_lines(rel: str) -> list[dict[str, object]]:
    """Parse one captured index file.

    Returns:
        One object per published version, in the file's order.

    """
    return [json.loads(x) for x in (INDEX / rel).read_text().splitlines()]


CORE = {"ed/tf/edtf-core": "ed/tf/edtf-core"}
WASM = {"ed/tf/edtf-wasm": "ed/tf/edtf-wasm"}


class TheFixturesAreTheRealShapes(unittest.TestCase):
    """The captured index answers carry what the rows are named for."""

    def test_the_crate_is_the_bytes_the_index_names(self) -> None:
        """edtf-core 1.3.3's index line names this crate file's sha256."""
        lines = index_lines("ed/tf/edtf-core")
        sums = [x["cksum"] for x in lines if x["vers"] == "1.3.3"]
        self.assertEqual([CORE_CKSUM], sums)
        self.assertEqual(CORE_CKSUM, hashlib.sha256(CRATE.read_bytes()).hexdigest())

    def test_wasm_has_a_file_but_no_1_3_3(self) -> None:
        """The burn's shape: the crate exists, the version does not."""
        lines = index_lines("ed/tf/edtf-wasm")
        self.assertTrue(lines)
        self.assertNotIn("1.3.3", [x["vers"] for x in lines])


class PublishResumes(unittest.TestCase):
    """The step does what its comment says (#1218)."""

    def test_nothing_published_adds_no_exclusion(self) -> None:
        """404s and a file without the version leave cargo's flags alone."""
        run = run_publish(WASM)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual([PUBLISH], run.publishes())
        self.assertNotIn("skipped", run.stdout)
        self.assertFalse([c for c in run.cargo if c.startswith("package")])

    def test_some_published_excludes_exactly_those_and_names_them(self) -> None:
        """The v1.3.3 re-run: edtf-core is there, the rest are not."""
        run = run_publish(CORE | WASM)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual([f"{PUBLISH} --exclude edtf-core"], run.publishes())
        self.assertEqual(
            ["edtf-core: already on crates.io at 1.3.3, skipped"],
            [x for x in run.stdout.splitlines() if "skipped" in x],
        )
        self.assertIn("package --locked --no-verify -p edtf-core", run.cargo)

    def test_all_published_runs_no_cargo_publish_and_exits_0(self) -> None:
        """A finished leg is success, and it mints no token."""
        others = ("calendars", "normalize", "wasm", "cli", "postgres")
        excludes = "".join(f" --exclude edtf-{m}" for m in others)
        run = run_publish(CORE, excludes=excludes)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual([], run.publishes())
        self.assertEqual(
            "edtf-core: already on crates.io at 1.3.3, skipped\n"
            "every crate this release publishes is already on crates.io"
            " at its version; nothing to publish\n",
            run.stdout,
        )
        self.assertEqual(
            ["https://index.crates.io/ed/tf/edtf-core"], [u for u, _ in run.curl]
        )

    def test_a_checksum_mismatch_fails_naming_both(self) -> None:
        """A resume must be resuming the same bytes."""
        planted = b"not the bytes v1.3.3 uploaded\n"
        run = run_publish(CORE | WASM, crate=planted)
        self.assertNotEqual(0, run.returncode)
        self.assertEqual(
            f"::error::edtf-core 1.3.3 is on crates.io with checksum {CORE_CKSUM},"
            f" but this tree packages {hashlib.sha256(planted).hexdigest()}\n",
            run.stdout,
        )
        self.assertEqual([], run.publishes())

    def test_an_index_503_fails_naming_the_status(self) -> None:
        """An unread index is never read as "not published"."""
        run = run_publish({"ed/tf/edtf-core": "503"} | WASM)
        self.assertNotEqual(0, run.returncode)
        self.assertEqual(
            "::error::the crates.io index answered 503 for edtf-core"
            " (https://index.crates.io/ed/tf/edtf-core); an unread index"
            ' is not "not published"\n',
            run.stdout,
        )
        self.assertEqual([], run.publishes())

    def test_every_index_read_names_the_org_and_the_layout(self) -> None:
        """One read per member the caller did not exclude, each with the UA."""
        run = run_publish(WASM)
        reads = [(u, a) for u, a in run.curl if u.startswith("https://index.")]
        self.assertEqual(
            [
                (f"https://index.crates.io/ed/tf/edtf-{m}", USER_AGENT)
                for m in ("core", "calendars", "normalize", "wasm", "cli")
            ],
            reads,
        )


if __name__ == "__main__":
    unittest.main()

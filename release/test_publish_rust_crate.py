#!/usr/bin/env python3
"""Run the shipped crates `Publish` step against a stub index and cargo.

#1218: edtf v1.3.3's crates leg uploaded four members, failed on the
fifth, and every re-run then stopped on the first:

    error: crate edtf-core@1.3.3 already exists on crates.io index

The step said it was resumable and ran one `cargo publish --workspace`,
which refuses the whole workspace when any member exists. It now asks
the sparse index for each member first and excludes the ones already
there - if they are the same bytes.

#1273: the step also read the caller's `exclude` as "never publish",
so edtf-postgres - excluded only because the crate job cannot compile
it - stayed at 1.2.3 on crates.io. The set to publish is now Cargo's
own: every workspace member whose `publish` key names crates.io. The
caller's old exclusion still rides in the environment of every row
here, as edtf's caller would have set it, so a row proves the step's
bytes no longer read it; a member with `publish = false` (`"publish":
[]` in cargo metadata, measured on cargo 1.97.1) is the only way out.

WHAT RUNS. release/publish-crates.py, the step's body since #1383,
in-process through its `run`, with a stub `cargo` first on PATH and the
helper's one HTTP function replaced by a stub that answers the index
from a directory laid out like the index host. `jq` and `sha256sum` are
the real ones, and so is release/crates-io-members.sh, the member set
the publisher shares with the build (#1310). The index path is the
belt's one rule (mise/crates-index.py), so a wrong layout reads as 404
and fails the "some published" row rather than passing quietly. Until
#1383 these rows cut the step's `run:` block out of the workflow; they
are the same rows, re-pointed.

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
import importlib.util
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

CANON = Path(__file__).resolve().parent.parent
TESTDATA = CANON / "release" / "testdata"
INDEX = TESTDATA / "crates-index"
CRATE = TESTDATA / "edtf-core-1.3.3.crate"
METADATA = TESTDATA / "edtf-cargo-metadata.json"

_SPEC = importlib.util.spec_from_file_location(
    "publish_crates", CANON / "release" / "publish-crates.py"
)
publish_crates = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish_crates)

CORE_CKSUM = "7001e4071709b45e67be9351b209b4465672b0ba633a143528b50f81805d428b"
USER_AGENT = (
    "monumental-archive-publish (https://github.com/monumental-archive/.github)"
)
OIDC_URL = "https://oidc.invalid/token?api-version=2.0"
OLD_CALLER_EXCLUDES = " --exclude edtf-postgres"
PUBLISH = "publish --workspace --locked --no-verify"

CARGO_STUB = """#!/usr/bin/env bash
# Stub `cargo`: logs every invocation; `metadata` answers the captured
# edtf metadata with the target directory moved into the fixture;
# `package -p <name>...` copies STUB_CRATES/<name>-*.crate where cargo
# would write it; `publish` records the token it was handed and exits
# STUB_PUBLISH_RC (0 when unset).
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
  publish)
    printf 'token=%s\\n' "${CARGO_REGISTRY_TOKEN:-}" >> "${STUB_LOG}/cargo-env"
    exit "${STUB_PUBLISH_RC:-0}"
    ;;
  *) echo "cargo stub: refusing $*" >&2; exit 101 ;;
esac
"""


@dataclass
class Fetch:
    """The helper's HTTP, stubbed.

    The index answers from a served directory, and the OIDC endpoint and
    the token exchange from canned answers. A served path's
    `<path>.status` file answers that status with no body.
    """

    served: Path
    oidc: int = 200
    exchange: int = 200
    requests: list[tuple[str, str]] = field(default_factory=list)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], data: bytes | None = None
    ) -> publish_crates.Response:
        """Answer one request and record it as the old curl stub logged it.

        Returns:
            The canned or served answer.

        """
        del method, data
        self.requests.append((url, headers.get("User-Agent", "")))
        if url.startswith(OIDC_URL):
            return publish_crates.Response(self.oidc, b'{"value":"stub-oidc"}')
        if url == publish_crates.EXCHANGE:
            return publish_crates.Response(self.exchange, b'{"token":"stub"}')
        rel = url.removeprefix("https://index.crates.io/")
        status = self.served / f"{rel}.status"
        if status.is_file():
            code = status.read_text()
            return publish_crates.Response(None if code == "000" else int(code), b"")
        if (self.served / rel).is_file():
            return publish_crates.Response(200, (self.served / rel).read_bytes())
        return publish_crates.Response(404, b"")


@dataclass
class Run:
    """What one run of the step did."""

    returncode: int
    stdout: str
    stderr: str
    cargo: list[str]
    curl: list[tuple[str, str]]
    tokens: list[str]

    def publishes(self) -> list[str]:
        """Pick out the `cargo publish` invocations.

        Returns:
            Each `cargo publish` argument line, in the order run.

        """
        return [c for c in self.cargo if c.startswith("publish ")]


def run_publish(
    index: dict[str, str],
    crate: bytes | None = None,
    unpublished: tuple[str, ...] = (),
    **answers: int,
) -> Run:
    """Run the helper with the index serving `index`.

    Args:
        index: index paths to serve - a fixture file's name to serve it,
            or a bare status code to answer that status.
        crate: the bytes `cargo package` produces for edtf-core 1.3.3;
            the real uploaded crate when None.
        unpublished: members whose manifest says `publish = false`,
            written into the captured metadata as cargo reports it.
        answers: `oidc=` and `exchange=` statuses, and `publish_rc=`, the
            stub cargo publish's exit.

    Returns:
        The run's status, output and the stubs' logs.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, log, served, crates = (tmp / n for n in ("bin", "log", "index", "c"))
        for p in (stubs, log, served, crates):
            p.mkdir()
        (stubs / "cargo").write_text(CARGO_STUB)
        (stubs / "cargo").chmod(0o755)
        for rel, answer in index.items():
            dest = served / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if answer.isdigit():
                dest.with_name(dest.name + ".status").write_text(answer)
            else:
                shutil.copy(INDEX / answer, dest)
        metadata = tmp / "metadata.json"
        captured = json.loads(METADATA.read_text())
        for package in captured["packages"]:
            if package["name"] in unpublished:
                package["publish"] = []
        metadata.write_text(json.dumps(captured))
        (crates / "edtf-core-1.3.3.crate").write_bytes(
            CRATE.read_bytes() if crate is None else crate
        )
        env = {
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "EXCLUDE_FLAGS": OLD_CALLER_EXCLUDES,
            "ORG_USER_AGENT": USER_AGENT,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "stub-request-token",
            "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL,
            "STUB_LOG": str(log),
            "STUB_CRATES": str(crates),
            "STUB_TARGET": str(tmp / "target"),
            "STUB_METADATA": str(metadata),
            "STUB_PUBLISH_RC": str(answers.get("publish_rc", 0)),
        }
        fetch = Fetch(
            served, oidc=answers.get("oidc", 200), exchange=answers.get("exchange", 200)
        )
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                code = publish_crates.run(env, tmp, fetch)
            except publish_crates.RefusalError as e:
                print(e)
                code = 1

        def read(name: str) -> list[str]:
            path = log / name
            return path.read_text().splitlines() if path.exists() else []

        return Run(
            code, out.getvalue(), "", read("cargo"), fetch.requests, read("cargo-env")
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
        run = run_publish(CORE, unpublished=tuple(f"edtf-{m}" for m in others))
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
        """One read per member that publishes, each with the UA."""
        run = run_publish(WASM)
        reads = [(u, a) for u, a in run.curl if u.startswith("https://index.")]
        self.assertEqual(
            [
                (f"https://index.crates.io/ed/tf/edtf-{m}", USER_AGENT)
                for m in ("core", "calendars", "normalize", "wasm", "cli", "postgres")
            ],
            reads,
        )


class AnUnreadableIndexIsUnread(unittest.TestCase):
    """#1556's shrunk examples: a 200 the reader cannot read refuses.

    Each died with a traceback before (AttributeError, UnicodeDecodeError,
    KeyError), never naming the crate or the index.
    """

    def test_each_shape_refuses_by_name(self) -> None:
        """Not an object, not UTF-8, our record with no cksum."""
        for body in (b"0", b"\x80", b'{"vers": "1.3.3"}\n'):
            with self.subTest(body=body):
                with self.assertRaises(publish_crates.RefusalError) as refused:
                    publish_crates.published_checksum(
                        "edtf-core",
                        "1.3.3",
                        "ua",
                        lambda *_, b=body: publish_crates.Response(200, b),
                    )
                self.assertEqual(
                    str(refused.exception),
                    "::error::the crates.io index for edtf-core"
                    " (https://index.crates.io/ed/tf/edtf-core) is not one JSON"
                    " record per line with a cksum; an unread index is not"
                    ' "not published"',
                )


class PublishFollowsCargo(unittest.TestCase):
    """Cargo's `publish` key decides, never the build's exclusion (#1273)."""

    def test_a_member_the_build_excluded_still_publishes(self) -> None:
        """edtf-postgres: in the old exclusion, `publish` unset - it ships."""
        run = run_publish(WASM)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertIn(
            ("https://index.crates.io/ed/tf/edtf-postgres", USER_AGENT), run.curl
        )
        self.assertEqual([PUBLISH], run.publishes())
        self.assertNotIn("--exclude", run.publishes()[0])

    def test_a_publish_false_member_is_not_in_the_set(self) -> None:
        """`publish = false` is the one way out, and it is the manifest's."""
        run = run_publish(CORE, unpublished=("edtf-postgres",))
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(
            [
                f"https://index.crates.io/ed/tf/edtf-{m}"
                for m in ("core", "calendars", "normalize", "wasm", "cli")
            ],
            [u for u, _ in run.curl if u.startswith("https://index.")],
        )
        self.assertEqual([f"{PUBLISH} --exclude edtf-core"], run.publishes())


class PublishMintsItsOwnToken(unittest.TestCase):
    """Trusted publishing by hand: what the step's curl calls did (#907)."""

    def test_the_token_is_masked_and_handed_to_cargo(self) -> None:
        """OIDC, then the exchange with the org's UA, masked, into cargo's env."""
        run = run_publish(WASM)
        self.assertEqual(0, run.returncode, run.stdout)
        self.assertIn("::add-mask::stub\n", run.stdout)
        self.assertEqual(["token=stub"], run.tokens)
        self.assertEqual(
            [
                (f"{OIDC_URL}&audience=crates.io", ""),
                ("https://crates.io/api/v1/trusted_publishing/tokens", USER_AGENT),
            ],
            [r for r in run.curl if not r[0].startswith("https://index.")],
        )

    def test_a_refused_exchange_names_the_status_and_the_header(self) -> None:
        """The 403 a missing UA draws is named, with where to look first."""
        run = run_publish(WASM, exchange=403)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::the crates.io token exchange answered 403 — if 403, check"
            " the User-Agent before the publisher config (#907)\n",
            run.stdout,
        )
        self.assertEqual([], run.publishes())

    def test_a_refused_oidc_request_names_the_status(self) -> None:
        """No OIDC token, no exchange, no publish."""
        run = run_publish(WASM, oidc=500)
        self.assertEqual(1, run.returncode)
        self.assertEqual(
            "::error::the runner's OIDC token endpoint answered 500\n", run.stdout
        )
        self.assertNotIn(publish_crates.EXCHANGE, [u for u, _ in run.curl])

    def test_an_index_that_never_answered_is_named(self) -> None:
        """No answer at all is "nothing", never "not published"."""
        run = run_publish({"ed/tf/edtf-core": "000"} | WASM)
        self.assertEqual(1, run.returncode)
        self.assertIn(
            "::error::the crates.io index answered nothing for edtf-core", run.stdout
        )

    def test_cargo_publish_s_own_exit_is_the_run_s(self) -> None:
        """A failed upload fails the run with cargo's own status."""
        run = run_publish(WASM, publish_rc=101)
        self.assertEqual(101, run.returncode)


class Main(unittest.TestCase):
    """The command: refusals and failed commands as job-log lines."""

    @staticmethod
    def main(failure: BaseException) -> tuple[int, str]:
        """Run main with `run` raising failure.

        Returns:
            The exit code and stdout.

        """
        out = io.StringIO()
        with (
            mock.patch.object(publish_crates, "run", side_effect=failure),
            mock.patch.object(publish_crates.sys, "stdout", out),
        ):
            code = publish_crates.main()
        return code, out.getvalue()

    def test_a_refusal_is_printed_and_exits_1(self) -> None:
        """The refusal's own line, exit 1."""
        self.assertEqual(
            (1, "::error::stop\n"),
            self.main(publish_crates.RefusalError("::error::stop")),
        )

    def test_a_failed_command_names_itself_and_its_status(self) -> None:
        """A failed cargo or member helper is named, with its exit."""
        failed = publish_crates.subprocess.CalledProcessError(
            101, ["cargo", "package", "-p", "x"]
        )
        self.assertEqual(
            (101, "::error::cargo package -p x exited 101\n"), self.main(failed)
        )

    def test_success_is_run_s_exit(self) -> None:
        """Without a failure, main returns what run returned."""
        with (
            mock.patch.object(publish_crates, "run", return_value=0),
            mock.patch.object(publish_crates.sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(0, publish_crates.main())


if __name__ == "__main__":
    unittest.main()

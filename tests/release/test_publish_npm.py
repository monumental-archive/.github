"""Run the npm publisher against a stub `npm` (#1383).

publish-wasm-npm.yml's Publish step had no test. Its body is now
belt/release/publish_npm.py, run here in-process through `run`, with a stub
`npm` first on PATH: `--version` answers STUB_NPM_VERSION, `view` exits
STUB_VIEW_RC, `config get logs-dir` answers STUB_LOGS_DIR, and `publish`
logs its arguments, writes a debug log beside the real npm's, and exits
STUB_PUBLISH_RC.

Every refusal's message is asserted whole. The masking rows plant a
token-shaped string and a credential header in the debug log, and assert
neither reaches the job log while the lines naming the failing step do.
"""

import itertools
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict, Unpack

import pytest

from belt.release import publish_npm
from belt.typed_pytest import fixture

# The status the stub `npm publish` fails with in the row that reads it.
NPM_FAILED = 7

PACKAGE = "@acme/widget@1.2.3"
NPM_CREDENTIAL = "npm_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

NPM_STUB = """#!/usr/bin/env bash
# Stub `npm`: answers from the environment, logs every invocation.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
case $1 in
  --version) echo "${STUB_NPM_VERSION}" ;;
  view) exit "${STUB_VIEW_RC:-1}" ;;
  config)
    if [[ -n ${STUB_LOGS_DIR:-} ]]; then echo "${STUB_LOGS_DIR}"; else exit 1; fi
    ;;
  publish)
    if [[ -n ${STUB_WRITE_LOG:-} ]]; then
      mkdir -p "${STUB_WRITE_LOG}"
      cp "${STUB_DEBUG_LOG}" "${STUB_WRITE_LOG}/2026-11-18T00_00_00_000Z-debug-0.log"
    fi
    exit "${STUB_PUBLISH_RC:-0}"
    ;;
  *) echo "npm stub: refusing $*" >&2; exit 2 ;;
esac
"""

DEBUG_LOG = f"""0 verbose cli /usr/bin/node /usr/bin/npm
1 info using npm@11.17.0
2 verb publish [ './dist/widget-1.2.3.tgz' ]
3 http fetch GET 200 https://registry.npmjs.org/-/npm/v1/oidc/token/exchange
4 verb headers authorization: Bearer {NPM_CREDENTIAL}
5 verb oidc exchanged token {NPM_CREDENTIAL}
6 error code ENEEDAUTH
"""


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["publish-npm"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


@dataclass
class Run:
    """What one run of the publisher did."""

    returncode: int
    stdout: str
    npm: list[str]
    # The refusals: the finding writer prints to stderr (#1934).
    stderr: str = ""


@dataclass
class Npm:
    """One job laid out: the publisher's env, its tree and the stub's log."""

    env: dict[str, str]
    work: Path
    npm_log: Path

    def ran(self, status: int, said: tuple[str, str]) -> Run:
        """Read what the publisher's run did.

        Returns:
            The exit code, the job log and the stub's invocations.

        """
        out, err = said
        log = self.npm_log
        return Run(
            status, out, log.read_text().splitlines() if log.exists() else [], err
        )


class Answers(TypedDict, total=False):
    """What the stub npm answers, each with its default."""

    version: str
    oidc: bool
    view_rc: int
    tarballs: tuple[str, ...]
    publish_rc: int
    logs: str


class NpmCall(Protocol):
    """What the `npm` fixture hands a row."""

    def __call__(self, **options: Unpack[Answers]) -> Npm:
        """Lay out a job with the stub answering as told."""
        ...


@fixture
def npm(tmp_path: Path) -> NpmCall:
    """Lay out the publish job's stub npm and dist/.

    Returns:
        A function that lays out one job and returns it.

    """
    made = itertools.count()

    def lay_out(
        **options: Unpack[Answers],
    ) -> Npm:
        """Lay out a job with the stub answering as told.

        Args:
            version: what `npm --version` answers.
            oidc: whether the runner offered the OIDC endpoint.
            view_rc: `npm view`'s exit; 0 means the version is published.
            tarballs: the files under dist/.
            publish_rc: `npm publish`'s exit.
            logs: where the debug log lands - "config" under the directory
                `npm config get logs-dir` names, "home" under ~/.npm/_logs
                with config answering nothing, "none" nowhere.

        Returns:
            The job: the publisher's env and working tree.

        """
        version = options.get("version", "11.17.0")
        oidc = options.get("oidc", True)
        view_rc = options.get("view_rc", 1)
        tarballs = options.get("tarballs", ("widget-1.2.3.tgz",))
        publish_rc = options.get("publish_rc", 0)
        logs = options.get("logs", "config")
        tmp = tmp_path / f"npm{next(made)}"
        stubs, work, home, configured = (
            tmp / n for n in ("bin", "work", "home", "logs")
        )
        for p in (stubs, work / "dist", home):
            p.mkdir(parents=True)
        (stubs / "npm").write_text(NPM_STUB)
        (stubs / "npm").chmod(0o755)
        for name in tarballs:
            (work / "dist" / name).write_bytes(b"tarball")
        debug = tmp / "debug.log"
        debug.write_text(DEBUG_LOG)
        env = {
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "STUB_LOG": str(tmp / "npm.log"),
            "STUB_NPM_VERSION": version,
            "STUB_VIEW_RC": str(view_rc),
            "STUB_PUBLISH_RC": str(publish_rc),
            "STUB_DEBUG_LOG": str(debug),
        }
        if oidc:
            env |= {
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.invalid/token",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "stub-request-token",
            }
        if logs == "config":
            env["STUB_LOGS_DIR"] = str(configured)
            env["STUB_WRITE_LOG"] = str(configured)
        elif logs == "home":
            env["STUB_WRITE_LOG"] = str(home / ".npm" / "_logs")
        return Npm(env, work, tmp / "npm.log")

    return lay_out


# Each refusal the step printed, word for word.
def test_npm_below_the_required_version(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """An npm of 11.5.0 cannot do trusted publishing: bump the pin."""
    job = npm(version="11.5.0")
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == (
        "::error::publish-npm: npm 11.5.0 < 11.5.1; trusted publishing needs"
        " 11.5.1+"
        " — bump the pin in this repository's mise config\n"
    )
    want: list[str] = ["--version"]
    assert want == run.npm


def test_versions_compare_as_numbers(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """11.10.0 is newer than 11.5.1, which a string compare gets wrong."""
    job = npm(version="11.10.0")
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout


def test_no_oidc_endpoint_in_the_environment(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """#907's second half: npm can, but the runner did not offer it."""
    job = npm(oidc=False)
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr == (
        "::error::publish-npm: the OIDC token endpoint is absent from this"
        " job's environment,"
        " so npm cannot use trusted publishing — every job in the call chain"
        " must grant id-token: write, including the caller's own job in its"
        " repository\n"
    )
    assert "publish" not in " ".join(run.npm)


@pytest.mark.parametrize(("tarballs", "count"), [((), 0), (("a.tgz", "b.tgz"), 2)])
def test_not_exactly_one_tarball(
    tarballs: tuple[()] | tuple[str, str],
    count: int,
    capfd: pytest.CaptureFixture[str],
    npm: NpmCall,
) -> None:
    """None, or two, is refused with the count."""
    job = npm(tarballs=tarballs)
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert run.stderr.endswith(
        f"::error::publish-npm: expected exactly one tarball, found {count}\n"
    ), run.stderr
    published: list[str] = [c for c in run.npm if c.startswith("publish")]
    assert not published


def test_a_hidden_tarball_is_not_a_tarball(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """The shell's `dist/*.tgz` matched no dotfile; neither does this."""
    job = npm(tarballs=("widget-1.2.3.tgz", ".partial.tgz"))
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout


# The path that uploads, and the one that converges.
def test_the_package_already_on_the_registry_converges(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """A finished leg is success, and nothing is uploaded."""
    job = npm(view_rc=0)
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 0
    assert (
        "trusted publishing: npm 11.17.0, OIDC endpoint present\n"
        f"::notice::{PACKAGE} already on the registry; publish is complete\n"
    ) == run.stdout
    want: list[str] = ["--version", f"view {PACKAGE} version"]
    assert want == run.npm


def test_the_one_tarball_is_published_by_its_relative_path(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """`./dist/<name>.tgz`, never `dist/...`, which npm reads as owner/repo."""
    job = npm()
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 0, run.stdout
    assert run.npm[-1] == "publish ./dist/widget-1.2.3.tgz --provenance --access public"


# #907: the failing step's own words reach the job log, masked.
def test_the_log_is_printed_with_the_token_redacted(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """The credential line is dropped and the token-shaped run redacted."""
    job = npm(publish_rc=1)
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert run.returncode == 1
    assert NPM_CREDENTIAL not in run.stdout
    assert "authorization" not in run.stdout
    assert "2 verb publish [ './dist/widget-1.2.3.tgz' ]\n" in run.stdout
    assert "5 verb oidc exchanged token <redacted>\n" in run.stdout
    assert "6 error code ENEEDAUTH\n::endgroup::\n" in run.stdout
    assert re.search(
        r"::group::npm debug log \(masked\) — .*-debug-0\.log\n", run.stdout
    )


def test_npm_s_own_exit_is_the_run_s(npm: NpmCall) -> None:
    """A failed upload fails with npm's status, after the log."""
    job = npm(publish_rc=NPM_FAILED)
    assert publish_npm.run(PACKAGE, job.env, job.work) == NPM_FAILED


def test_the_default_log_directory_when_config_names_none(
    capfd: pytest.CaptureFixture[str], npm: NpmCall
) -> None:
    """No logs-dir from npm config: ~/.npm/_logs, as npm defaults."""
    job = npm(publish_rc=1, logs="home")
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert "/.npm/_logs/2026-11-18T00_00_00_000Z-debug-0.log\n" in run.stdout


def test_no_debug_log_is_said(capfd: pytest.CaptureFixture[str], npm: NpmCall) -> None:
    """A failure that left no log says where it looked."""
    job = npm(publish_rc=1, logs="none")
    status = publish_npm.run(PACKAGE, job.env, job.work)
    run = job.ran(status, capfd.readouterr())
    assert re.search(
        r"::warning::npm left no debug log under .*/\.npm/_logs\n$", run.stdout
    )


def test_only_the_last_300_lines_survive() -> None:
    """The tail, as the step's `tail -300`."""
    log = "\n".join(f"{i} verb line" for i in range(400))
    want: list[str] = [f"{i} verb line" for i in range(100, 400)]
    assert want == publish_npm.masked(log)


# The command's one argument.
@pytest.mark.parametrize(
    ("argv", "error"),
    [
        pytest.param(
            [],
            (
                "belt publish-npm: error: the following arguments are required: "
                "NAME@VERSION"
            ),
            id="nothing",
        ),
        pytest.param(
            ["a", "b"],
            "belt publish-npm: error: unrecognized arguments: b",
            id='["a", "b"]',
        ),
    ],
)
def test_a_missing_package_is_a_usage_error(
    argv: list[str], error: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """A usage error: argparse's exit 2 and its error.

    No package, or an empty one, exits 2 with the usage line.
    """
    with pytest.raises(SystemExit) as ended:
        publish_npm.main(argv)
    out, err = capfd.readouterr()
    assert (ended.value.code, out) == (2, "")
    assert err.startswith("usage: belt publish-npm "), err
    assert err.splitlines()[-1] == error


def test_the_package_reaches_run_with_the_job_s_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One argument: the job's own environment and directory."""
    asked: list[tuple[object, ...]] = []

    def run(*args: object) -> int:
        asked.append(args)
        return 0

    monkeypatch.setattr(publish_npm, "run", run)
    assert publish_npm.main([PACKAGE]) == 0
    assert asked == [(PACKAGE, os.environ, Path.cwd())]

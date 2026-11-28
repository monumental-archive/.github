"""Run the npm publisher against a stub `npm` (#1383).

publish-wasm-npm.yml's Publish step had no test. Its body is now
release/publish_npm.py, run here in-process through `run`, with a stub
`npm` first on PATH: `--version` answers STUB_NPM_VERSION, `view` exits
STUB_VIEW_RC, `config get logs-dir` answers STUB_LOGS_DIR, and `publish`
logs its arguments, writes a debug log beside the real npm's, and exits
STUB_PUBLISH_RC.

Every refusal's message is asserted whole. The masking rows plant a
token-shaped string and a credential header in the debug log, and assert
neither reaches the job log while the lines naming the failing step do.
"""

import io
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from belt.release import publish_npm

PACKAGE = "@acme/widget@1.2.3"
# ruff: ignore[hardcoded-password-string] - a planted fake, to prove it is redacted
TOKEN = "npm_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

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
4 verb headers authorization: Bearer {TOKEN}
5 verb oidc exchanged token {TOKEN}
6 error code ENEEDAUTH
"""


@pytest.fixture
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


# ruff: ignore[too-many-arguments] - one keyword per stub answer
def run_publish(
    *,
    version: str = "11.17.0",
    oidc: bool = True,
    view_rc: int = 1,
    tarballs: tuple[str, ...] = ("widget-1.2.3.tgz",),
    publish_rc: int = 0,
    logs: str = "config",
) -> Run:
    """Run the publisher with the stub answering as told.

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
        The exit code, the job log and the stub's invocations.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
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
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = publish_npm.run(PACKAGE, env, work)
        log = tmp / "npm.log"
        return Run(
            code,
            out.getvalue(),
            log.read_text().splitlines() if log.exists() else [],
            err.getvalue(),
        )


class TestRefusals:
    """Each refusal the step printed, word for word."""

    def test_npm_below_the_required_version(self) -> None:
        """An npm of 11.5.0 cannot do trusted publishing: bump the pin."""
        run = run_publish(version="11.5.0")
        assert run.returncode == 1
        assert run.stderr == (
            "::error::publish-npm: npm 11.5.0 < 11.5.1; trusted publishing needs"
            " 11.5.1+"
            " — bump the pin in this repository's mise config\n"
        )
        want: list[str] = ["--version"]
        assert want == run.npm

    def test_versions_compare_as_numbers(self) -> None:
        """11.10.0 is newer than 11.5.1, which a string compare gets wrong."""
        run = run_publish(version="11.10.0")
        assert run.returncode == 0, run.stdout

    def test_no_oidc_endpoint_in_the_environment(self) -> None:
        """#907's second half: npm can, but the runner did not offer it."""
        run = run_publish(oidc=False)
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
    def test_not_exactly_one_tarball(self, tarballs: object, count: object) -> None:
        """None, or two, is refused with the count."""
        run = run_publish(tarballs=tarballs)
        assert run.returncode == 1
        assert run.stderr.endswith(
            f"::error::publish-npm: expected exactly one tarball, found {count}\n"
        ), run.stderr
        published: list[str] = [c for c in run.npm if c.startswith("publish")]
        assert not published

    def test_a_hidden_tarball_is_not_a_tarball(self) -> None:
        """The shell's `dist/*.tgz` matched no dotfile; neither does this."""
        run = run_publish(tarballs=("widget-1.2.3.tgz", ".partial.tgz"))
        assert run.returncode == 0, run.stdout


class TestPublishes:
    """The path that uploads, and the one that converges."""

    def test_the_package_already_on_the_registry_converges(self) -> None:
        """A finished leg is success, and nothing is uploaded."""
        run = run_publish(view_rc=0)
        assert run.returncode == 0
        assert (
            "trusted publishing: npm 11.17.0, OIDC endpoint present\n"
            f"::notice::{PACKAGE} already on the registry; publish is complete\n"
        ) == run.stdout
        want: list[str] = ["--version", f"view {PACKAGE} version"]
        assert want == run.npm

    def test_the_one_tarball_is_published_by_its_relative_path(self) -> None:
        """`./dist/<name>.tgz`, never `dist/...`, which npm reads as owner/repo."""
        run = run_publish()
        assert run.returncode == 0, run.stdout
        assert (
            run.npm[-1]
            == "publish ./dist/widget-1.2.3.tgz --provenance --access public"
        )


class TestAFailedPublishShowsItsLog:
    """#907: the failing step's own words reach the job log, masked."""

    def test_the_log_is_printed_with_the_token_redacted(self) -> None:
        """The credential line is dropped and the token-shaped run redacted."""
        run = run_publish(publish_rc=1)
        assert run.returncode == 1
        assert TOKEN not in run.stdout
        assert "authorization" not in run.stdout
        assert "2 verb publish [ './dist/widget-1.2.3.tgz' ]\n" in run.stdout
        assert "5 verb oidc exchanged token <redacted>\n" in run.stdout
        assert "6 error code ENEEDAUTH\n::endgroup::\n" in run.stdout
        assert re.search(
            r"::group::npm debug log \(masked\) — .*-debug-0\.log\n", run.stdout
        )

    def test_npm_s_own_exit_is_the_run_s(self) -> None:
        """A failed upload fails with npm's status, after the log."""
        assert run_publish(publish_rc=7).returncode == 7

    def test_the_default_log_directory_when_config_names_none(self) -> None:
        """No logs-dir from npm config: ~/.npm/_logs, as npm defaults."""
        run = run_publish(publish_rc=1, logs="home")
        assert "/.npm/_logs/2026-11-18T00_00_00_000Z-debug-0.log\n" in run.stdout

    def test_no_debug_log_is_said(self) -> None:
        """A failure that left no log says where it looked."""
        run = run_publish(publish_rc=1, logs="none")
        assert re.search(
            r"::warning::npm left no debug log under .*/\.npm/_logs\n$", run.stdout
        )

    def test_only_the_last_300_lines_survive(self) -> None:
        """The tail, as the step's `tail -300`."""
        log = "\n".join(f"{i} verb line" for i in range(400))
        want: list[str] = [f"{i} verb line" for i in range(100, 400)]
        assert want == publish_npm.masked(log)


class TestMain:
    """The command's one argument."""

    @pytest.mark.parametrize("argv", [[], [""], ["a", "b"]])
    def test_a_missing_package_is_a_usage_error(self, argv: object) -> None:
        """No package, or an empty one, exits 2 with the usage line."""
        out = io.StringIO()
        with redirect_stdout(out):
            assert publish_npm.main(argv) == 2
        assert out.getvalue() == "usage: belt publish-npm <name@version>\n"

    def test_the_package_reaches_run_with_the_job_s_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One argument: the job's own environment and directory."""
        asked: list[tuple[object, ...]] = []

        def run(*args: object) -> int:
            asked.append(args)
            return 0

        monkeypatch.setattr(publish_npm, "run", run)
        assert publish_npm.main([PACKAGE]) == 0
        assert asked == [(PACKAGE, os.environ, Path.cwd())]

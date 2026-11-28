"""Run `belt pack-wasm`, the wasm-npm pack step, on a stub npm (#1384).

WHAT RUNS. The helper's `main`, called in this process, with a
`.org-canon` link to this tree, from a workspace holding the crate's
pkg/package.json, with a stub `npm` first on PATH. GITHUB_OUTPUT is a
real file the helper writes and the test reads back.

THE STUB IS NPM PACK'S SHAPE: `npm pack <dir> --silent` writes
<name>-<version>.tgz into its working directory and prints that name
alone. A row can make it write an empty file, write nothing and print
nothing, or fail.
"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from belt import workflow_yaml
from belt.release import pack_wasm
from belt.typed_pytest import fixture
from tests import support

CANON = support.CANON
WORKFLOW = CANON / ".github" / "workflows" / "build-wasm-npm.yml"
COMMAND = "pack-wasm"
REPO = "monumental-archive/edtf"
WANT = f"https://github.com/{REPO}"
VERSION = "1.3.4"
TARBALL = f"edtf-wasm-{VERSION}.tgz"
BYTES = b"the packed tarball\n"
SUM = hashlib.sha256(BYTES).hexdigest()

NPM_STUB = """#!/usr/bin/env bash
# Stub `npm`, in npm pack's shape; STUB_MODE picks a row's failure.
set -euo pipefail
printf '%s\\n' "$*" >> "${STUB_LOG}"
case ${STUB_MODE} in
  fail) echo "npm error code E404" >&2; exit 7 ;;
  silent) exit 0 ;;
  empty) : > "${STUB_TARBALL}" ;;
  *) printf 'the packed tarball\\n' > "${STUB_TARBALL}" ;;
esac
printf '%s\\n' "${STUB_TARBALL}"
"""


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["pack-wasm"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


def strings(*items: str) -> list[str]:
    """List the given strings, typed as strings for the assertions.

    Returns:
        The strings, in order.

    """
    return list(items)


@dataclass
class Run:
    """What the helper did."""

    returncode: int
    stdout: str
    stderr: str
    npm: list[str]
    outputs: str
    pkg: str


def run_helper(
    manifest: object, mode: str = "ok", *, capfd: pytest.CaptureFixture[str]
) -> Run:
    """Call the helper on one package.json, in this process.

    Returns:
        Its status, output, the npm log and what it wrote.

    """
    with TemporaryDirectory() as d:
        tmp = Path(d)
        stubs, work = tmp / "bin", tmp / "work"
        pkg = work / "crates" / "edtf-wasm" / "pkg"
        for p in (stubs, pkg):
            p.mkdir(parents=True)
        (stubs / "npm").write_text(NPM_STUB)
        (stubs / "npm").chmod(0o755)
        support.canon(work / ".org-canon")
        (pkg / "package.json").write_text(json.dumps(manifest))
        log, outputs = tmp / "npm.log", tmp / "github_output"
        outputs.touch()
        env = support.env(
            PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
            UNTRUSTED_VERSION=VERSION,
            UNTRUSTED_CRATE_DIR="crates/edtf-wasm",
            GITHUB_REPOSITORY=REPO,
            GITHUB_OUTPUT=str(outputs),
            STUB_LOG=str(log),
            STUB_MODE=mode,
            STUB_TARBALL=TARBALL,
        )
        with pytest.MonkeyPatch.context() as patched:
            for name, value in env.items():
                patched.setenv(name, value)
            patched.chdir(work)
            patched.setattr(sys, "argv", [COMMAND])
            status = pack_wasm.main([])
        out, err = capfd.readouterr()
        return Run(
            status,
            out,
            err,
            log.read_text().splitlines() if log.exists() else [],
            outputs.read_text(),
            str(pkg.resolve()),
        )


def package(**fields: object) -> dict[str, object]:
    """Build wasm-pack's package.json, with a row's fields over it.

    Returns:
        The manifest; a field set to None is left out.

    """
    base: dict[str, object] = {
        "name": "edtf-wasm",
        "version": VERSION,
        "repository": {"type": "git", "url": WANT},
    }
    base.update(fields)
    return {k: v for k, v in base.items() if v is not None}


def refusal(repo_url: str) -> str:
    """Spell the repository refusal exactly as the step printed it.

    Returns:
        The error line, newline included.

    """
    return (
        f"::error::pack-wasm: package.json repository is '{repo_url}', npm"
        f" provenance"
        f" requires '{WANT}' — set repository = \"{WANT}\" in the crate's"
        " Cargo.toml (canon: [workspace.package] repository with"
        " repository.workspace = true)\n"
    )


# The job runs the helper these rows run.
def test_the_shipped_step_calls_the_helper() -> None:
    """The step's run: is the helper call alone."""
    document = workflow_yaml.document(WORKFLOW)
    (step,) = [
        s
        for job in workflow_yaml.jobs(document).values()
        for s in workflow_yaml.steps(job)
        if s.get("name") == "Pack and hash the exact bytes that will publish"
    ]
    # Under the belt's Python (#1543), on one line.
    assert f'"${{ORG_PYTHON}}" -I .org-canon/belt {COMMAND}' == step["run"]


# The exact bytes are packed, hashed and named.
def test_the_tarball_is_hashed_into_every_output(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Subjects, package and registry-subjects carry one digest."""
    run = run_helper(package(), capfd=capfd)
    assert run.returncode == 0, run.stdout + run.stderr
    line = f"{SUM}  {TARBALL}"
    assert f"{line}\n" == run.stdout
    assert strings(f"pack {run.pkg} --silent") == run.npm
    assert (
        f"subjects<<SUBJECTS_EOF\n{line}\nSUBJECTS_EOF\n"
        f"package=edtf-wasm@{VERSION}\n"
        f"registry-subjects=package {line}\n"
    ) == run.outputs


@pytest.mark.parametrize("url", [f"git+{WANT}.git", f"git+{WANT}", f"{WANT}.git"])
def test_the_git_url_forms_are_the_repository(
    url: object, capfd: pytest.CaptureFixture[str]
) -> None:
    """`git+` and `.git` are the same repository."""
    run = run_helper(package(repository={"type": "git", "url": url}), capfd=capfd)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_string_repository_is_read(capfd: pytest.CaptureFixture[str]) -> None:
    """The shorthand form names the repository too."""
    run = run_helper(package(repository=WANT), capfd=capfd)
    assert run.returncode == 0, run.stdout + run.stderr


# One row per refusal; nothing is packed or output after one.
def assert_refused(run: Run, message: str) -> None:
    """Hold a refusal: status 1, the message alone, no pack, no outputs.

    The message is a finding, so it is on stderr (#1934).
    """
    assert run.returncode == 1
    assert message == run.stderr
    assert strings() == run.npm
    assert run.outputs == ""


def test_another_repository_is_refused_with_the_remedy(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The registry would refuse its provenance (the v0.14.1 run)."""
    other = "https://github.com/someone/fork"
    run = run_helper(package(repository={"type": "git", "url": other}), capfd=capfd)
    assert_refused(run, refusal(other))


def test_no_repository_is_refused_with_the_remedy(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Cargo.toml without `repository` leaves none in package.json (#96)."""
    assert_refused(run_helper(package(repository=None), capfd=capfd), refusal(""))


def test_a_repository_without_a_url_is_refused(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """An object with no url is not this repository."""
    run = run_helper(package(repository={"type": "git"}), capfd=capfd)
    assert_refused(run, refusal('{"type": "git"}'))


def test_a_version_that_is_not_the_tags_is_refused(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The package would publish under a version the tag does not name."""
    run = run_helper(package(version="1.3.3"), capfd=capfd)
    assert_refused(
        run, f"::error::pack-wasm: package.json says 1.3.3, tag says {VERSION}\n"
    )


def test_no_version_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """A missing version reads as null, as jq read it."""
    run = run_helper(package(version=None), capfd=capfd)
    assert_refused(
        run, f"::error::pack-wasm: package.json says null, tag says {VERSION}\n"
    )


def test_a_repository_spanning_lines_stays_one_error(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A newline in the caller's text is escaped (#1556's audit seed).

    A repository of one newline split the `::error::pack-wasm: ` line in two,
    and the second line was the caller's, free to be a workflow
    command.
    """
    injected = "x\n::warning::injected\r%"
    run = run_helper(package(repository={"type": "git", "url": injected}), capfd=capfd)
    assert_refused(run, refusal("x%0A::warning::injected%0D%25"))


def test_a_package_json_that_is_not_an_object_is_refused(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`0` and `[]` refuse by name (#1556's shrunk examples).

    Both died with an AttributeError traceback before.
    """
    manifests: tuple[object, ...] = (0, [])
    for manifest in manifests:
        assert_refused(
            run_helper(manifest, capfd=capfd),
            "::error::pack-wasm: crates/edtf-wasm/pkg/package.json is not"
            " a JSON object\n",
        )


# npm pack must leave bytes behind.
def test_an_empty_tarball_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """A zero-byte file is nothing to publish."""
    run = run_helper(package(), mode="empty", capfd=capfd)
    assert run.returncode == 1
    assert run.stderr == "::error::pack-wasm: npm pack produced nothing\n"
    assert run.outputs == ""


def test_no_tarball_named_is_refused(capfd: pytest.CaptureFixture[str]) -> None:
    """A pack that names no tarball is nothing to publish."""
    run = run_helper(package(), mode="silent", capfd=capfd)
    assert run.returncode == 1
    assert run.stderr == "::error::pack-wasm: npm pack produced nothing\n"
    assert run.outputs == ""


def test_a_failing_npm_fails_the_step_with_its_status(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The failing npm's status and message, no outputs."""
    run = run_helper(package(), mode="fail", capfd=capfd)
    assert run.returncode == 7
    assert run.stderr == "npm error code E404\n"
    assert run.stdout == ""
    assert run.outputs == ""

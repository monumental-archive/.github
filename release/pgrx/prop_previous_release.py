#!/usr/bin/env python3
"""previous-release.py's guard and fetch, on any tag `gh` answers.

The properties of `test_pgrx_build.py`'s PreviousRelease rows (#1556),
over generated tags, versions, crate directories and asset listings.
`gh` and `tar` are replaced in-process by the row's answers.

The issue's line for this helper read "`latest_tag` is the greatest
version whatever the order"; read in full, the helper keeps no listing
to order: `latest_tag` is GitHub's own latest release, as `gh release
view` names it. The properties are sharpened to what it decides:

- V: the guard refuses exactly when the latest tag is `v<prev>`, prev is
  not this version, that release shipped this extension, and the
  upgrade script `<crate>/sql/<name>--<prev>--<version>.sql` is absent;
  the refusal names that path. Every other path exits 0, and a failed
  asset read exits with gh's own status.
- V: the extension's name is what `basename DIR | tr - _` prints, for
  any crate directory below the checkout, trailing slashes or not.
- R: on any tag text and any `gh` answer, guard and fetch exit with
  0, 1 or the tool's own status; neither dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import importlib.util
import io
import os
import posixpath
import subprocess  # ruff: ignore[suspicious-subprocess-import] - PIPE only; the run is faked
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hypothesis import assume, example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "previous_release",
    Path(__file__).with_name("previous-release.py"),
)
previous = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(previous)

VERSION = st.from_regex(
    r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}(-rc\.[0-9])?", fullmatch=True
)
SEGMENT = st.from_regex(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,8}", fullmatch=True).filter(
    lambda s: s not in {".", ".."}
)
CRATE = st.lists(SEGMENT, min_size=1, max_size=3).map("/".join)
# Whatever `gh` may print for the latest tag: a release tag, this
# version's own, or any text.
TAG = st.one_of(VERSION.map("v{}".format), st.text(max_size=12))


def fake(tag: str, tag_rc: int, count: str, assets_rc: int, tar_rc: int = 0) -> object:
    """Answer gh and tar as the row says.

    Returns:
        A stand-in for the subprocess module.

    """

    def run(argv: list[str], **_: object) -> SimpleNamespace:
        if argv[0] == previous.TAR:
            return SimpleNamespace(returncode=tar_rc, stdout="")
        if "tagName" in argv:
            return SimpleNamespace(returncode=tag_rc, stdout=f"{tag}\n")
        if "assets" in argv:
            return SimpleNamespace(returncode=assets_rc, stdout=f"{count}\n")
        return SimpleNamespace(returncode=assets_rc, stdout="")

    return SimpleNamespace(run=run, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL)


def quietly(call: object, *args: object) -> tuple[int, str]:
    """Run a mode with its output captured.

    Returns:
        Its status and stdout.

    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = call(*args)
    return code, out.getvalue()


@st.composite
def guard_cases(draw: st.DrawFn) -> SimpleNamespace:
    """Draw what the guard reads: gh's two answers and the tree.

    Returns:
        The case.

    """
    return SimpleNamespace(
        tag=draw(TAG),
        tag_rc=draw(st.sampled_from([0, 1])),
        version=draw(VERSION),
        crate=draw(CRATE),
        count=draw(st.sampled_from(["0", "1", "3"])),
        assets_rc=draw(st.sampled_from([0, 4])),
        scripted=draw(st.sampled_from(["script", "none"])) == "script",
    )


def stranded(tag: str, version: str, crate: str) -> SimpleNamespace:
    """Write a case whose previous release shipped and has no script.

    Returns:
        The case.

    """
    return SimpleNamespace(
        tag=tag,
        tag_rc=0,
        version=version,
        crate=crate,
        count="2",
        assets_rc=0,
        scripted=False,
    )


def plant(script: str) -> bool:
    """Write the upgrade script, where this filesystem can hold its name.

    Returns:
        Whether it was written (macOS refuses some code points; the
        runner's filesystem does not, so such a name is no example).

    """
    try:
        Path(script).parent.mkdir(parents=True, exist_ok=True)
        Path(script).write_text("", "utf-8")
    except OSError:
        return False
    return True


class GuardRefusesExactly(unittest.TestCase):
    """V: a stranded installation, and only that, is refused."""

    @given(guard_cases())
    @example(stranded("v1.0.0", "1.1.0", "crates/edtf-pg"))
    @example(stranded("v1.1.0", "1.1.0", "crates/edtf-pg"))
    def test_refused_exactly_when_stranded(self, case: SimpleNamespace) -> None:
        """The model's verdict is the guard's."""
        previous.subprocess = fake(case.tag, case.tag_rc, case.count, case.assets_rc)
        name = posixpath.basename(case.crate).replace("-", "_")
        latest = case.tag.strip() if case.tag_rc == 0 else ""
        prev = latest.removeprefix("v")
        script = f"{case.crate}/sql/{name}--{prev}--{case.version}.sql"
        here = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                if case.scripted:
                    assume(plant(script))
                code, out = quietly(previous.guard, "o/r", case.version, case.crate)
            finally:
                os.chdir(here)
        if not latest.startswith("v") or prev == case.version:
            want = 0
        elif case.assets_rc:
            want = case.assets_rc
        elif case.count == "0" or case.scripted:
            want = 0
        else:
            want = 1
        self.assertEqual(code, want, out)
        if want == 1:
            self.assertIn(
                f"::error::no upgrade path {script} from published {prev}", out
            )


class ExtensionName(unittest.TestCase):
    """V: the name is `basename DIR | tr - _`."""

    @given(CRATE, st.integers(0, 2))
    def test_basename(self, crate: str, slashes: int) -> None:
        """Trailing slashes or not, the last segment, dashes as underscores."""
        self.assertEqual(
            previous.extension_name(crate + "/" * slashes),
            posixpath.basename(crate).replace("-", "_"),
        )


class AnyTag(unittest.TestCase):
    """R: any tag text and any gh answer are answered by a status."""

    @given(
        st.text(max_size=16),
        st.sampled_from([0, 1, 4]),
        st.text(max_size=4),
        st.sampled_from([0, 1, 4]),
        st.sampled_from([0, 2]),
        VERSION,
    )
    def test_guard_and_fetch_answer(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] - one per gh answer
        self, tag: str, tag_rc: int, count: str, rc: int, tar_rc: int, version: str
    ) -> None:
        """A status, never a raise."""
        previous.subprocess = fake(tag, tag_rc, count, rc, tar_rc)
        here = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                code, _ = quietly(previous.guard, "o/r", version, "crate")
                self.assertIn(code, {0, 1, rc})
                args = SimpleNamespace(
                    repo="o/r",
                    version=version,
                    crate_dir="crate",
                    pg="16",
                    arch="amd64",
                    tarball="prev.tar.gz",
                    dest="prev",
                )
                code, _ = quietly(previous.fetch, args)
                self.assertIn(code, {0, tar_rc})
            finally:
                os.chdir(here)


if __name__ == "__main__":
    unittest.main()

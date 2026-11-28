"""previous_release.py's one predecessor, on any graph and any listing.

The properties of `test_pgrx_build.py`'s PreviousRelease and
OnePredecessor rows (#1556, #1592), over generated upgrade graphs,
release listings, versions and crate directories. `gh` and `tar` are
replaced in-process by the row's answers.

The predecessor is no longer GitHub's latest release (#1592): it is the
newest version the extension's upgrade graph upgrades TO, LOWER than the
version under release, that a non-draft release carries tarballs for,
and that release's tag whatever it is named. So:

- V: `predecessor` is the numerically greatest graph target lower than
  the version, among those the listing carries (a release on an older
  line never upgrades from a newer one), whatever order the
  listing and the directory come in, with the first release in listing
  order that carries it; nothing when no target is carried.
- V: the guard refuses exactly when the listing was read, this version
  is not already carried, a predecessor exists, and the upgrade script
  `<crate>/sql/<name>--<prev>--<version>.sql` is absent; the refusal
  names that path and the predecessor's release. Every other path
  exits 0, and a failed listing read exits with gh's own status.
- V: the extension's name is what `basename DIR | tr - _` prints, for
  any crate directory below the checkout, trailing slashes or not.
- R: on any listing text and any `gh` answer, guard and fetch exit with
  0, 1 or the tool's own status; neither dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import posixpath
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt.release.pgrx import previous_release as previous
from belt.typed_hypothesis import given

if TYPE_CHECKING:
    pass

# A small pool, so graphs and listings meet: 1.0.10 beside 1.0.2 is the
# order a string sort gets wrong.
VERSION = st.sampled_from(["0.2.0", "1.0.2", "1.0.10", "1.2.3", "1.3.0", "10.0.0"])
NAME = st.sampled_from(["demo", "demo_ext"])
TAG = st.one_of(
    VERSION.map("v{}".format),
    st.from_regex(r"[a-z-]{1,8}v[0-9.]{1,6}", fullmatch=True),
)


def as_the_step(patched: pytest.MonkeyPatch, tmp: Path) -> None:
    """Set one example's world: the stand-ins, outside mise, in `tmp`.

    A run outside mise records nothing and is named by its file (#1934).
    """
    patched.setenv("MISE_TASK_NAME", "")
    patched.setenv("ORG_FINDINGS", "")
    patched.setattr(sys, "argv", ["previous_release.py"])
    patched.setattr(previous, "GH", str(tmp / ".stubs" / "gh"))
    patched.setattr(previous, "TAR", str(tmp / ".stubs" / "tar"))
    patched.chdir(tmp)


def not_dots(segment: str) -> bool:
    """Say whether a path segment is anything but `.` or `..`.

    Returns:
        True for a segment that names an entry.

    """
    return segment not in {".", ".."}


SEGMENT = st.from_regex(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,8}", fullmatch=True).filter(
    not_dots
)
CRATE = st.lists(SEGMENT, min_size=1, max_size=3).map("/".join)
# Numerically 1.0.10 is newer; as text it sorts first.
CARRIES_1_0_10 = "demo-1.0.10-pg18-linux-amd64.tar.gz"
CARRIES_1_0_2 = "demo-1.0.2-pg18-linux-amd64.tar.gz"
# An older line: releasing 1.2.3 beside a published 1.3.0.
CARRIES_1_3_0 = "demo-1.3.0-pg18-linux-amd64.tar.gz"
EDGES = st.lists(st.tuples(VERSION, VERSION), max_size=5)


# A listing that lists the newer of two carried targets first, and one
# whose newest carried target is above the version released.
STRING_SORTED: list[tuple[str, str]] = [
    ("v1.0.10", CARRIES_1_0_10),
    ("v1.0.2", CARRIES_1_0_2),
]
NEWER_FIRST: list[tuple[str, str]] = [
    ("v1.3.0", CARRIES_1_3_0),
    ("v1.0.10", CARRIES_1_0_10),
]
TO_1_0_10: list[tuple[str, str]] = [("0.2.0", "1.0.2"), ("1.0.2", "1.0.10")]
TO_1_3_0: list[tuple[str, str]] = [("1.0.2", "1.0.10"), ("1.0.10", "1.3.0")]


def rows(name: str) -> st.SearchStrategy[list[tuple[str, str]]]:
    """Draw a listing: this extension's tarballs, another's, and noise.

    Returns:
        The strategy of `(tag, asset)` per asset, in listing order.

    """
    asset = st.one_of(
        VERSION.map(lambda v: f"{name}-{v}-pg18-linux-amd64.tar.gz"),
        VERSION.map(lambda v: f"other-{v}-pg18-linux-amd64.tar.gz"),
        st.sampled_from(["checksums.txt", f"{name}.cdx.json"]),
    )
    return st.lists(st.tuples(TAG, asset), max_size=8)


def model(
    listing: list[tuple[str, str]],
    edges: list[tuple[str, str]],
    name: str,
    version: str,
) -> tuple[str, str] | None:
    """Answer the predecessor from its definition, independently.

    Returns:
        `(version, tag)`, or None.

    """
    carried: dict[str, str] = {}
    for tag, asset in listing:
        for _, to in edges:
            if asset.startswith(f"{name}-{to}-pg"):
                carried.setdefault(to, tag)

    def numeric(v: str) -> list[int]:
        return [int(p) for p in v.split(".")]

    lower = {v: t for v, t in carried.items() if numeric(v) < numeric(version)}
    if not lower:
        return None
    newest = max(lower, key=numeric)
    return newest, lower[newest]


def lay(sql: Path, name: str, edges: list[tuple[str, str]]) -> None:
    """Write the graph's scripts, empty, beside a README and a stranger's."""
    sql.mkdir(parents=True, exist_ok=True)
    (sql / "README.md").write_text("", encoding="utf-8")
    (sql / "other--0.2.0--10.0.0.sql").write_text("", encoding="utf-8")
    for frm, to in edges:
        (sql / f"{name}--{frm}--{to}.sql").write_text("", encoding="utf-8")


def stubs(
    root: Path, listing: str, list_rc: int, tar_rc: int = 0, dl_rc: int = 0
) -> None:
    """Stand a `gh` and a `tar` in, answering as the row says.

    `gh api` prints `listing` and exits `list_rc`; any other `gh` call
    exits `dl_rc`; `tar` exits `tar_rc`. They are written under `root`.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "listing").write_text(listing, encoding="utf-8")
    gh = (
        "#!/bin/sh\n"
        'case " $* " in *" api "*) cat "$(dirname "$0")/listing";'
        f" exit {list_rc} ;; esac\n"
        f"exit {dl_rc}\n"
    )
    for name, text in (("gh", gh), ("tar", f"#!/bin/sh\nexit {tar_rc}\n")):
        (root / name).write_text(text, encoding="utf-8")
        (root / name).chmod(0o755)


def served(listing: list[tuple[str, str]]) -> str:
    """Print a listing as the helper's jq does, a line per asset.

    Returns:
        The lines.

    """
    return "".join(f"{tag}\t{asset}\n" for tag, asset in listing)


# V: the predecessor is the definition's, in any order.
@given(rows("demo"), EDGES, VERSION, st.booleans())
@example(listing=STRING_SORTED, edges=TO_1_0_10, version="1.2.3", backwards=False)
@example(listing=NEWER_FIRST, edges=TO_1_3_0, version="1.2.3", backwards=False)
def test_the_greatest_carried_target(
    listing: list[tuple[str, str]],
    edges: list[tuple[str, str]],
    version: str,
    backwards: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - drawn
) -> None:
    """The newest installable target below `version`, its first carrier."""
    want = model(listing, edges, "demo", version)
    with tempfile.TemporaryDirectory() as tmp:
        sql = Path(tmp) / "sql"
        lay(sql, "demo", edges[::-1] if backwards else edges)
        got = previous.predecessor(listing, sql, "demo", version)
    assert want == (None if got is None else (got.version, got.tag))


# V: a stranded installation, and only that, is refused.
@given(st.data(), CRATE, EDGES, VERSION, st.sampled_from([0, 4]), st.booleans())
def test_refused_exactly_when_stranded(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] - one per drawn input
    data: st.DataObject,
    crate: str,
    edges: list[tuple[str, str]],
    version: str,
    list_rc: int,
    scripted: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - drawn
) -> None:
    """The model's verdict is the guard's."""
    name = posixpath.basename(crate).replace("-", "_")
    listing = data.draw(rows(name))
    rerun = any(a.startswith(f"{name}-{version}-pg") for _, a in listing)
    prev = model(listing, edges, name, version)
    said = io.StringIO()
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as patched,
        contextlib.redirect_stdout(said),
        contextlib.redirect_stderr(said),
    ):
        stubs(Path(tmp) / ".stubs", served(listing), list_rc)
        as_the_step(patched, Path(tmp))
        lay(Path(crate) / "sql", name, edges)
        script = ""
        if prev is not None:
            script = f"{crate}/sql/{name}--{prev[0]}--{version}.sql"
            if scripted:
                Path(script).write_text("", encoding="utf-8")
        code = previous.guard("o/r", version, crate)
    out = said.getvalue()
    if list_rc:
        want = list_rc
    elif rerun or prev is None or scripted or (prev[0], version) in edges:
        want = 0
    else:
        want = 1
    assert code == want, out
    # 1 is the model's refusal alone, which needs a predecessor.
    if want == 1 and prev is not None:
        assert (
            f"::error::previous_release.py: no upgrade path {script} from"
            f" published {prev[0]}"
            f" ({prev[1]})"
        ) in out


# V: the name is `basename DIR | tr - _`.
@given(CRATE, st.integers(0, 2))
def test_basename(crate: str, slashes: int) -> None:
    """Trailing slashes or not, the last segment, dashes as underscores."""
    assert previous.extension_name(crate + "/" * slashes) == posixpath.basename(
        crate
    ).replace("-", "_")


# R: any listing text and any gh answer are answered by a status.
@given(
    st.text(max_size=40),
    st.sampled_from([0, 1, 4]),
    st.sampled_from([0, 1]),
    st.sampled_from([0, 2]),
    VERSION,
)
def test_guard_and_fetch_answer(
    listing: str, rc: int, dl_rc: int, tar_rc: int, version: str
) -> None:
    """A status, never a raise."""
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as patched,
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        stubs(Path(tmp) / ".stubs", listing, rc, tar_rc, dl_rc)
        as_the_step(patched, Path(tmp))
        lay(Path("crate") / "sql", "crate", [("0.2.0", "1.0.2")])
        code = previous.guard("o/r", version, "crate")
        guarded: set[int] = {0, 1, rc}
        assert code in guarded
        args = previous.Args(
            repo="o/r",
            version=version,
            crate_dir="crate",
            pg="16",
            arch="amd64",
            tarball="prev.tar.gz",
            dest="prev",
        )
        code = previous.fetch(args)
        fetched: set[int] = {0, rc, tar_rc}
        assert code in fetched

"""previous_release.py's one predecessor, on any graph and any listing.

The properties of `test_pgrx_build.py`'s PreviousRelease and
OnePredecessor rows (#1556, #1592), over generated upgrade graphs,
release listings, versions and crate directories. Each property tests
one of the helper's pure functions; what `gh` and `tar` answer is the
I/O layer's, held by the table rows in test_pgrx_build.py.

The predecessor is no longer GitHub's latest release (#1592): it is the
newest version the extension's upgrade graph upgrades TO, LOWER than the
version under release, that a non-draft release carries tarballs for,
and that release's tag whatever it is named. So:

- V: `predecessor` is the numerically greatest graph target lower than
  the version, among those the listing carries (a release on an older
  line never upgrades from a newer one), whatever order the
  listing and the directory come in, with the first release in listing
  order that carries it; nothing when no target is carried.
- V: the guard refuses exactly when this version is not already
  carried, a predecessor exists, and the upgrade script
  `<name>--<prev>--<version>.sql` is not among the `sql/` directory's
  files; the refusal names `<crate>/sql/` and that script, and the
  predecessor's release.
- V: fetch wants the predecessor's tarball of the cell exactly when the
  guard has a predecessor to check, and names it by the cell.
- V: the extension's name is what `basename DIR | tr - _` prints, for
  any crate directory below the checkout, trailing slashes or not.
- R: any listing text is read without a raise, each row a tab-split
  line of it, and judged.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import posixpath

from hypothesis import example
from hypothesis import strategies as st

from belt.release.pgrx import previous_release as previous
from belt.typed_hypothesis import given

# A small pool, so graphs and listings meet: 1.0.10 beside 1.0.2 is the
# order a string sort gets wrong.
VERSION = st.sampled_from(["0.2.0", "1.0.2", "1.0.10", "1.2.3", "1.3.0", "10.0.0"])
NAME = st.sampled_from(["demo", "demo_ext"])
TAG = st.one_of(
    VERSION.map("v{}".format),
    st.from_regex(r"[a-z-]{1,8}v[0-9.]{1,6}", fullmatch=True),
)


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


def scripts(name: str, edges: list[tuple[str, str]]) -> list[str]:
    """Name the graph's scripts, beside a README and a stranger's.

    Returns:
        The `sql/` directory's file names.

    """
    edge = [f"{name}--{frm}--{to}.sql" for frm, to in edges]
    return ["README.md", "other--0.2.0--10.0.0.sql", *edge]


# V: the predecessor is the definition's, in any order.
@given(rows("demo"), EDGES, VERSION, st.sampled_from(["listed", "reversed"]))
@example(listing=STRING_SORTED, edges=TO_1_0_10, version="1.2.3", order="listed")
@example(listing=NEWER_FIRST, edges=TO_1_3_0, version="1.2.3", order="listed")
def test_the_greatest_carried_target(
    listing: list[tuple[str, str]],
    edges: list[tuple[str, str]],
    version: str,
    order: str,
) -> None:
    """The newest installable target below `version`, its first carrier."""
    want = model(listing, edges, "demo", version)
    names = scripts("demo", edges)
    graph = previous.graph_of(names[::-1] if order == "reversed" else names, "demo")
    got = previous.predecessor(listing, graph, "demo", version)
    assert want == (None if got is None else (got.version, got.tag))


# V: a stranded installation, and only that, is refused.
@given(st.data(), CRATE, EDGES, VERSION)
def test_refused_exactly_when_stranded(
    data: st.DataObject,
    crate: str,
    edges: list[tuple[str, str]],
    version: str,
) -> None:
    """The model's verdict is the guard's, and the refusal names the path."""
    name = posixpath.basename(crate).replace("-", "_")
    listing = data.draw(rows(name))
    # Whether the tree carries the direct script to `version` itself.
    scripted = data.draw(st.booleans())
    rerun = any(a.startswith(f"{name}-{version}-pg") for _, a in listing)
    prev = model(listing, edges, name, version)
    names = scripts(name, edges)
    if prev is not None and scripted:
        names.append(f"{name}--{prev[0]}--{version}.sql")
    verdict = previous.judge(listing, names, crate, version)
    stranded = not (rerun or prev is None or scripted or (prev[0], version) in edges)
    assert verdict.refused == stranded, verdict.message
    if stranded and prev is not None:
        assert verdict.message == (
            f"no upgrade path {crate}/sql/{name}--{prev[0]}--{version}.sql"
            f" from published {prev[0]} ({prev[1]})"
            " — existing installations would be stranded"
        )


# V: fetch wants the predecessor the guard checks, named by the cell.
@given(st.data(), CRATE, EDGES, VERSION, st.sampled_from(["16", "18"]))
def test_fetch_wants_the_predecessor_s_cell(
    data: st.DataObject,
    crate: str,
    edges: list[tuple[str, str]],
    version: str,
    pg: str,
) -> None:
    """None on a re-run or a first publish; else the model's, by cell."""
    name = posixpath.basename(crate).replace("-", "_")
    listing = data.draw(rows(name))
    rerun = any(a.startswith(f"{name}-{version}-pg") for _, a in listing)
    prev = model(listing, edges, name, version)
    got = previous.wanted(listing, scripts(name, edges), crate, version, (pg, "arm64"))
    if rerun or prev is None:
        assert got is None
    else:
        asset = f"{name}-{prev[0]}-pg{pg}-linux-arm64.tar.gz"
        assert got == (previous.Predecessor(*prev), asset)


# V: the name is `basename DIR | tr - _`.
@given(CRATE, st.integers(0, 2))
def test_basename(crate: str, slashes: int) -> None:
    """Trailing slashes or not, the last segment, dashes as underscores."""
    assert previous.extension_name(crate + "/" * slashes) == posixpath.basename(
        crate
    ).replace("-", "_")


# R: any listing text is read, and judged, without a raise.
@given(st.text(max_size=40), VERSION)
def test_any_listing_is_read_and_judged(text: str, version: str) -> None:
    """Each row a tab-split line; a verdict, never a raise."""
    listing = previous.parse_listing(text)
    want = [
        (tag, asset)
        for tag, sep, asset in (line.partition("\t") for line in text.splitlines())
        if sep
    ]
    assert listing == want
    verdict = previous.judge(
        listing, scripts("crate", [("0.2.0", "1.0.2")]), "crate", version
    )
    assert verdict.message

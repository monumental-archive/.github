"""build_go_commands.py, the go-binary build, on any package graph.

The properties of `test_build_go_commands.py`'s fixed rows (#1556),
over generated `go list` output. `go` is replaced in-process: `list`
prints the row's main packages, `build -o OUT PKG` writes OUT.

- V: two main packages sharing a last path element are refused, by
  that element, whatever order `go list` prints them in; packages whose
  last elements are distinct each build, once, to staged/<element>,
  and nothing else is built.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt.release import build_go_commands as commands
from belt.typed_hypothesis import given

if TYPE_CHECKING:
    import random

    from tests import support

# An import path as `go list` prints one; lowercase, as the shipped
# names are on a case-sensitive runner.
ELEMENT = st.from_regex(r"[a-z0-9][a-z0-9._-]{0,6}", fullmatch=True)
IMPORT_PATH = st.lists(ELEMENT, min_size=1, max_size=4).map("/".join)
# Two packages, one binary name.
CLASHING: list[str] = ["example.com/a/tool", "example.com/b/tool"]


def fake_go(pkgs: list[str]) -> tuple[support.Tool, list[str]]:
    """Stand a `go` in for the build: `list` prints `pkgs`, a build writes.

    Returns:
        The stand-in, and the packages it built, in the order built.

    """
    built: list[str] = []

    def go(*args: str) -> str:
        if args[0] == "list":
            return "".join(f"{p}\n" for p in pkgs)
        _, _, out, pkg = args
        built.append(pkg)
        Path(out).write_text(pkg, "utf-8")
        return ""

    return go, built


# V: one binary name, two packages: refused whatever the order.
@given(
    st.lists(IMPORT_PATH, min_size=1, max_size=6, unique=True),
    st.randoms(use_true_random=False),
)
@example(CLASHING, None)
def test_refused_exactly_on_a_shared_last_element(
    pkgs: list[str], shuffle: random.Random | None
) -> None:
    """A shared element is refused by name; distinct ones each build."""
    if shuffle is not None:
        shuffle.shuffle(pkgs)
    names = [p.rpartition("/")[2] for p in pkgs]
    go, built = fake_go(pkgs)
    refused = None
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as patched,
    ):
        patched.setattr(commands, "go", go)
        patched.chdir(tmp)
        try:
            commands.build()
        except commands.RefusedError as e:
            refused = str(e)
        staged = sorted(p.name for p in (Path(tmp) / "staged").iterdir())
    clash = [n for i, n in enumerate(names) if n in names[:i]]
    if clash:
        assert refused == f"two main packages both build a binary named {clash[0]}"
    else:
        assert refused is None
        assert built == pkgs
        want: list[str] = sorted(names)
        assert staged == want

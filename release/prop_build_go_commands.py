#!/usr/bin/env python3
"""build-go-commands.py, the go-binary build, on any package graph.

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

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "build_go_commands",
    Path(__file__).with_name("build-go-commands.py"),
)
commands = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(commands)

# An import path as `go list` prints one; lowercase, as the shipped
# names are on a case-sensitive runner.
ELEMENT = st.from_regex(r"[a-z0-9][a-z0-9._-]{0,6}", fullmatch=True)
IMPORT_PATH = st.lists(ELEMENT, min_size=1, max_size=4).map("/".join)


def run_build(pkgs: list[str]) -> tuple[str | None, list[str], list[str]]:
    """Run the build in a scratch module whose `go list` prints `pkgs`.

    Returns:
        The refusal's message or None, the packages built in the order
        built, and the names staged/ holds.

    """
    built: list[str] = []

    def go(*args: str) -> str:
        if args[0] == "list":
            return "".join(f"{p}\n" for p in pkgs)
        _, _, out, pkg = args
        built.append(pkg)
        Path(out).write_text(pkg, "utf-8")
        return ""

    commands.go = go
    here = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            commands.build()
            refused = None
        except commands.RefusedError as e:
            refused = str(e)
        finally:
            os.chdir(here)
        staged = sorted(p.name for p in (Path(tmp) / "staged").iterdir())
    return refused, built, staged


class SameNameRefused(unittest.TestCase):
    """V: one binary name, two packages: refused whatever the order."""

    @given(
        st.lists(IMPORT_PATH, min_size=1, max_size=6, unique=True),
        st.randoms(use_true_random=False),
    )
    @example(["example.com/a/tool", "example.com/b/tool"], None)
    def test_refused_exactly_on_a_shared_last_element(
        self, pkgs: list[str], shuffle: object
    ) -> None:
        """A shared element is refused by name; distinct ones each build."""
        if shuffle is not None:
            shuffle.shuffle(pkgs)
        names = [p.rpartition("/")[2] for p in pkgs]
        refused, built, staged = run_build(pkgs)
        clash = [n for i, n in enumerate(names) if n in names[:i]]
        if clash:
            self.assertEqual(
                refused, f"two main packages both build a binary named {clash[0]}"
            )
        else:
            self.assertIsNone(refused)
            self.assertEqual(built, pkgs)
            self.assertEqual(staged, sorted(names))


if __name__ == "__main__":
    unittest.main()

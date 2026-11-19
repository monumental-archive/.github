#!/usr/bin/env python3
"""pgrx-images.py's names and readers, on any crate, tree and table.

The properties of `test_pgrx_images.py`'s fixed rows (#1556):

- T: `tarball_name` parses back: from the name alone, the crate's
  last path segment (with `-` as `_`), the version, the major and the
  architecture are read back as given, whatever the version's own
  dashes and dots.
- R: on any base-image table — any TOML document, whatever its
  `[build]` holds — `base-image` prints one reference or refuses by
  name (exit 1, one `::error::` line); `extension-name` does the same on
  a tree holding any set of control files and other files.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from org_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "pgrx_images",
    Path(__file__).with_name("pgrx-images.py"),
)
images = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(images)

SEGMENT = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,10}", fullmatch=True)
VERSION = st.from_regex(
    r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(-[a-z0-9.-]{1,8})?", fullmatch=True
)
MAJOR = st.integers(10, 99).map(str)
# The name back out: the crate's segment holds no `-`, so the first
# dash ends it; the major and architecture are the fixed tail.
PARSE = re.compile(
    r"(?P<name>[^-]*)-(?P<version>.+)-pg(?P<pg>[0-9]+)-linux-(?P<arch>amd64|arm64)\.tar\.gz"
)
# TOML has no null, and floats add nothing the reader branches on.
SCALAR = st.booleans() | st.integers() | st.text(max_size=8)
VALUE = st.recursive(
    SCALAR,
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=8,
)


def toml_value(value: object) -> str:
    """Render a value as an inline TOML value (no floats are drawn).

    Returns:
        The TOML text.

    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    elif isinstance(value, list):
        text = "[" + ", ".join(toml_value(v) for v in value) + "]"
    else:
        pairs = (f"{toml_value(str(k))} = {toml_value(v)}" for k, v in value.items())
        text = "{" + ", ".join(pairs) + "}"
    return text


def run(argv: list[str]) -> tuple[int, str, str]:
    """Run one verb.

    Returns:
        The exit status, stdout and stderr.

    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = images.main(argv)
    return code, out.getvalue(), err.getvalue()


def assert_answered(case: unittest.TestCase, got: tuple[int, str, str]) -> None:
    """Hold a run to one printed line, or one refusal line."""
    code, out, err = got
    if code == 0:
        case.assertEqual(err, "")
        case.assertTrue(out.endswith("\n") and out.count("\n") == 1, out)
    else:
        case.assertEqual((code, out), (1, ""))
        case.assertTrue(err.startswith("::error::"), err)
        case.assertEqual(err.count("\n"), 1, err)


class TarballNameParsesBack(unittest.TestCase):
    """T: the name says the crate, version, major and arch it was made of."""

    @given(
        st.lists(SEGMENT, min_size=1, max_size=3),
        VERSION,
        MAJOR,
        st.sampled_from(images.ARCHES),
    )
    @example(["crates", "edtf-pg"], "1.3.4-rc.1", "16", "arm64")
    def test_parses_back(
        self, segments: list[str], version: str, pg: str, arch: str
    ) -> None:
        """Read back from the name alone."""
        name = images.tarball_name("/".join(segments), version, pg, arch)
        found = PARSE.fullmatch(name)
        self.assertIsNotNone(found, name)
        self.assertEqual(
            found.groupdict(),
            {
                "name": segments[-1].replace("-", "_"),
                "version": version,
                "pg": pg,
                "arch": arch,
            },
        )


class AnyTable(unittest.TestCase):
    """R: any base-image table is read or refused by name."""

    @given(
        st.one_of(
            st.dictionaries(MAJOR, VALUE, max_size=3),
            VALUE,
        ),
        MAJOR,
    )
    @example("x16", "16")
    @example(["16"], "16")
    @example({"16": 1}, "16")
    def test_base_image(self, build: object, pg: str) -> None:
        """One reference, or the step's own refusal."""
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "pgrx-base-images.toml"
            table.write_text(f"build = {toml_value(build)}\n", "utf-8")
            got = run(["base-image", "--pg", pg, "--table", str(table)])
        assert_answered(self, got)


class AnyTree(unittest.TestCase):
    """R: any extension tree is named or refused by name."""

    @given(
        st.lists(
            st.one_of(
                st.builds("{}.control".format, SEGMENT),
                st.builds("{}.sql".format, SEGMENT),
                SEGMENT,
            ),
            max_size=4,
            unique=True,
        )
    )
    def test_extension_name(self, files: list[str]) -> None:
        """One name, or the step's own refusal."""
        with tempfile.TemporaryDirectory() as tmp:
            for f in files:
                (Path(tmp) / f).write_text("", "utf-8")
            got = run(["extension-name", "--tree", tmp])
        assert_answered(self, got)
        controls = [f for f in files if f.endswith(".control")]
        self.assertEqual(got[0] == 0, len(controls) == 1, files)


if __name__ == "__main__":
    unittest.main()

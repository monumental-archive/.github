#!/usr/bin/env python3
"""caller_pins.py, the reader of a caller's build-input pins, on any TOML.

The properties of `test_caller_pins.py`'s fixed rows (#1556), over
generated mise configs:

- T: a pin written under any backend key, in any of the three value
  forms a `[tools]` entry takes (a string, a list whose first entry is
  the one on PATH, an inline table's `version`), is read back by
  `install` as the caller's own `key@version`, and by `env` as the
  version.
- R: on any TOML document, whatever shape its `tools` value takes, the
  reader answers or refuses by name (exit 0 or 1, the refusal on
  stderr as `::error::`); it never dies with a traceback. The same
  documents never raise from `pinned_names`, which the gate reads.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from operator import itemgetter
from pathlib import Path

import caller_pins
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

INPUT_OF = "a generated job"
NAME = st.from_regex(r"[a-z][a-z0-9-]{0,12}", fullmatch=True)
VERSION = st.from_regex(r"[0-9a-z][0-9a-z.+-]{0,15}", fullmatch=True)
OWNER = st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True)


def toml_string(s: str) -> str:
    """Render a TOML basic string.

    Returns:
        The quoted string: JSON's escapes, which TOML shares, plus DEL,
        which TOML forbids raw and JSON leaves alone.

    """
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_float(value: float) -> str:
    """Render a TOML float, the two non-finite spellings included.

    Returns:
        The TOML text.

    """
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return repr(value)


def toml_value(value: object) -> str:
    """Render a value as an inline TOML value.

    Returns:
        The TOML text.

    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = toml_float(value)
    elif isinstance(value, str):
        text = toml_string(value)
    elif isinstance(value, list):
        text = "[" + ", ".join(toml_value(v) for v in value) + "]"
    else:
        pairs = (f"{toml_string(k)} = {toml_value(v)}" for k, v in value.items())
        text = "{" + ", ".join(pairs) + "}"
    return text


def toml_document(doc: dict) -> str:
    """Render a top-level table, one `key = value` line per key.

    Returns:
        The TOML text.

    """
    return "".join(f"{toml_string(k)} = {toml_value(v)}\n" for k, v in doc.items())


SCALAR = st.one_of(
    st.text(max_size=8), st.integers(), st.booleans(), st.floats(width=64)
)
VALUE = st.recursive(
    SCALAR,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.dictionaries(st.text(max_size=6), inner, max_size=3),
    ),
    max_leaves=10,
)
# A key mise reads as a tool, any backend form, or any text at all.
KEY = st.one_of(
    NAME,
    st.builds("cargo:{}".format, NAME),
    st.builds(lambda o, n: f"github:{o}/{n}", OWNER, NAME),
    st.text(max_size=10),
)
TOOLS = st.one_of(VALUE, st.dictionaries(KEY, VALUE, max_size=4))
DOCUMENT = st.builds(
    lambda tools, rest: {**rest, "tools": tools} if tools is not None else rest,
    st.none() | TOOLS,
    st.dictionaries(
        st.text(max_size=6).filter(lambda k: k != "tools"), VALUE, max_size=2
    ),
)


def run(config: str, argv: list[str]) -> tuple[int, str, str]:
    """Run the reader against a repository whose mise.toml is `config`.

    Returns:
        The exit status, stdout and stderr.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "mise.toml").write_text(config, "utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = caller_pins.main(argv, root=root)
        return code, out.getvalue(), err.getvalue()


@st.composite
def pinned(draw: st.DrawFn) -> tuple[str, str, str, object]:
    """Draw one tool pinned under some backend key, in some value form.

    Returns:
        The tool name, the key written, the version on PATH, and the
        `[tools]` value written for it.

    """
    name = draw(NAME)
    key = draw(
        st.sampled_from([
            name,
            f"cargo:{name}",
            f"github:{draw(OWNER)}/{name}",
            f"aqua:{draw(OWNER)}/{name}[bin={name}]",
        ])
    )
    version = draw(VERSION)
    form = draw(st.sampled_from(["string", "list", "table"]))
    if form == "list":
        value: object = [version, *draw(st.lists(VERSION, max_size=2))]
    elif form == "table":
        value = {"version": version, **draw(st.dictionaries(NAME, VERSION, max_size=2))}
    else:
        value = version
    return name, key, version, value


class WrittenReadsBack(unittest.TestCase):
    """T: a pin written is read back as `key@version`."""

    @given(st.lists(pinned(), min_size=1, max_size=4, unique_by=itemgetter(0)))
    def test_install_and_env_read_the_pin(
        self, pins: list[tuple[str, str, str, object]]
    ) -> None:
        """Every tool, named in any order, answers with its own pin."""
        config = toml_document({"tools": {key: value for _, key, _, value in pins}})
        names = [name for name, _, _, _ in pins]
        code, out, err = run(config, ["install", "--input-of", INPUT_OF, *names])
        self.assertEqual((code, err), (0, ""), config)
        want = " ".join(f"{key}@{version}" for _, key, version, _ in pins)
        self.assertEqual(out, f"install={want}\n")
        pairs = [f"V{i}={name}" for i, name in enumerate(names)]
        code, out, err = run(config, ["env", "--input-of", INPUT_OF, *pairs])
        self.assertEqual((code, err), (0, ""), config)
        want = "".join(f"V{i}={version}\n" for i, (_, _, version, _) in enumerate(pins))
        self.assertEqual(out, want)


class AnyToml(unittest.TestCase):
    """R: any TOML document is answered or refused by name."""

    @given(DOCUMENT, st.lists(NAME, max_size=3), st.lists(NAME, max_size=2))
    @example({"tools": "rust"}, ["rust"], [])
    @example({"tools": ["rust"]}, ["rust"], [])
    @example({"tools": 1}, [], ["rust"])
    def test_answers_or_refuses(
        self, doc: dict, needed: list[str], optional: list[str]
    ) -> None:
        """Exit 0 with a pin line, or 1 with `::error::` lines; never a raise."""
        config = toml_document(doc)
        argv = ["install", "--input-of", INPUT_OF, *needed]
        for name in optional:
            argv += ["--if-pinned", name]
        if not needed and not optional:
            argv.append("rust")
        code, out, err = run(config, argv)
        if code == 0:
            self.assertTrue(out.startswith("install="), out)
            self.assertEqual(err, "")
        else:
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertTrue(err)
            for line in err.splitlines():
                self.assertTrue(line.startswith("::error::"), line)

    @given(DOCUMENT)
    @example({"tools": 1})
    @example({"tools": [{"a": 1}]})
    def test_pinned_names_never_raises(self, doc: dict) -> None:
        """The gate's question, which tools are pinned, has an answer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mise.toml").write_text(toml_document(doc), "utf-8")
            names = caller_pins.pinned_names(root)
        self.assertIsInstance(names, set)


if __name__ == "__main__":
    unittest.main()

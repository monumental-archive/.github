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
from pathlib import Path

import caller_pins
import decoded
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
    elif decoded.is_array(value):
        text = "[" + ", ".join(toml_value(v) for v in value) + "]"
    else:
        table = decoded.as_object(value, "a drawn value")
        pairs = (f"{toml_string(k)} = {toml_value(v)}" for k, v in table.items())
        text = "{" + ", ".join(pairs) + "}"
    return text


def toml_document(doc: dict[str, object]) -> str:
    """Render a top-level table, one `key = value` line per key.

    Returns:
        The TOML text.

    """
    return "".join(f"{toml_string(k)} = {toml_value(v)}\n" for k, v in doc.items())


SCALAR: st.SearchStrategy[object] = st.one_of(
    st.text(max_size=8), st.integers(), st.booleans(), st.floats(width=64)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or a table.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    tables: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=6), inner, max_size=3
    )
    return st.one_of(arrays, tables)


def github_key(owner: str, name: str) -> str:
    """Spell a github-backend key.

    Returns:
        The key.

    """
    return f"github:{owner}/{name}"


def document(tools: object, rest: dict[str, object]) -> dict[str, object]:
    """Put `[tools]` beside other keys, unless there is none.

    Returns:
        The document.

    """
    return {**rest, "tools": tools} if tools is not None else rest


def not_tools(key: str) -> bool:
    """Say whether a top-level key is any but `tools`.

    Returns:
        True for a key that is not `tools`.

    """
    return key != "tools"


VALUE: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=10)
# A key mise reads as a tool, any backend form, or any text at all.
KEY = st.one_of(
    NAME,
    st.builds("cargo:{}".format, NAME),
    st.builds(github_key, OWNER, NAME),
    st.text(max_size=10),
)
TABLES: st.SearchStrategy[object] = st.dictionaries(KEY, VALUE, max_size=4)
TOOLS: st.SearchStrategy[object] = st.one_of(VALUE, TABLES)
MAYBE_TOOLS: st.SearchStrategy[object] = st.none() | TOOLS
REST: st.SearchStrategy[dict[str, object]] = st.dictionaries(
    st.text(max_size=6).filter(not_tools), VALUE, max_size=2
)
DOCUMENT: st.SearchStrategy[dict[str, object]] = st.builds(document, MAYBE_TOOLS, REST)


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


# A tool written under some backend key, in some value form: the
# tool name, the key written, the version on PATH, and the `[tools]`
# value written for it.
Pin = tuple[str, str, str, object]
# What a pin is drawn from: the name, two owners, the key's backend,
# and the version with its value form and that form's extras.
Drawn = tuple[str, str, str, str, tuple[str, str, list[str], dict[str, str]]]


def assemble(drawn: Drawn) -> Pin:
    """Write one drawn tool's key and `[tools]` value.

    Returns:
        The tool name, the key written, the version on PATH, and the
        `[tools]` value written for it.

    """
    name, owner, other, backend, (version, form, more, extra) = drawn
    key = {
        "plain": name,
        "cargo": f"cargo:{name}",
        "github": f"github:{owner}/{name}",
        "aqua": f"aqua:{other}/{name}[bin={name}]",
    }[backend]
    if form == "list":
        value: object = [version, *more]
    elif form == "table":
        value = {"version": version, **extra}
    else:
        value = version
    return name, key, version, value


# One tool pinned under some backend key, in some value form.
PINNED = st.tuples(
    NAME,
    OWNER,
    OWNER,
    st.sampled_from(["plain", "cargo", "github", "aqua"]),
    st.tuples(
        VERSION,
        st.sampled_from(["string", "list", "table"]),
        st.lists(VERSION, max_size=2),
        st.dictionaries(NAME, VERSION, max_size=2),
    ),
).map(assemble)
# A `[tools]` value of each wrong shape.
TOOLS_STRING: dict[str, object] = {"tools": "rust"}
TOOLS_LIST: dict[str, object] = {"tools": ["rust"]}
TOOLS_INT: dict[str, object] = {"tools": 1}
TOOLS_TABLES: dict[str, object] = {"tools": [{"a": 1}]}
RUST: list[str] = ["rust"]
NONE: list[str] = []


def first(pin: Pin) -> str:
    """Name the tool a pin is for, to keep one pin per tool.

    Returns:
        The tool name.

    """
    return pin[0]


class WrittenReadsBack(unittest.TestCase):
    """T: a pin written is read back as `key@version`."""

    @given(st.lists(PINNED, min_size=1, max_size=4, unique_by=first))
    def test_install_and_env_read_the_pin(self, pins: list[Pin]) -> None:
        """Every tool, named in any order, answers with its own pin."""
        tools: dict[str, object] = {key: value for _, key, _, value in pins}
        config = toml_document({"tools": tools})
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
    @example(TOOLS_STRING, RUST, NONE)
    @example(TOOLS_LIST, RUST, NONE)
    @example(TOOLS_INT, NONE, RUST)
    def test_answers_or_refuses(
        self, doc: dict[str, object], needed: list[str], optional: list[str]
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
    @example(TOOLS_INT)
    @example(TOOLS_TABLES)
    def test_pinned_names_never_raises(self, doc: dict[str, object]) -> None:
        """The gate's question, which tools are pinned, has an answer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mise.toml").write_text(toml_document(doc), "utf-8")
            names = caller_pins.pinned_names(root)
        self.assertIsInstance(names, set)


if __name__ == "__main__":
    unittest.main()

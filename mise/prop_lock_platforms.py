#!/usr/bin/env python3
"""lock-platforms.py's stub reader, on workflows nobody wrote down.

The properties of `test_lock_platforms.py`'s fixed rows (#1548), over
generated publish stubs, scalars and tool configs:

- Round trip (T): a publish call's `with:` inputs read back as written,
  whatever comments, blank lines and other jobs surround them, and
  only from jobs whose `uses:` is publish.yml.
- Invariance (V): a plain scalar reads the same bare, single-quoted,
  double-quoted or with a trailing comment; a block, flow or
  expression form is refused by name.
- Refusal (R): `publish_calls` reads any text and never raises.
- Invariance (V): an `os` restriction excludes exactly the platforms
  it does not name.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "lock_platforms",
    Path(__file__).with_name("lock-platforms.py"),
)
lp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lp)

KEY = st.from_regex(r"[a-z][a-z-]{0,10}", fullmatch=True)
VALUE = st.from_regex(r"[a-z0-9][a-z0-9_.,-]{0,20}", fullmatch=True)
JOB = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)
NOISE = st.sampled_from(["", "    # a comment", "  "])
PUBLISH = "monumental-archive/.github/.github/workflows/publish.yml@0123abcd # v1.0.0"


@st.composite
def workflow(draw: st.DrawFn) -> tuple[str, list[dict[str, str]]]:
    """Write a workflow with some jobs calling publish.yml.

    Returns:
        The text and the inputs of each publish call, in job order.

    """
    lines = ["name: release", "on:", "  push:", "jobs:"]
    calls = []
    for job in draw(st.lists(JOB, unique=True, min_size=1, max_size=4)):
        publish = draw(st.booleans())
        lines += [f"  {job}:", draw(NOISE)]
        lines.append(
            f"    uses: {PUBLISH if publish else 'org/x/.github/workflows/other.yml@1'}"
        )
        inputs = draw(st.dictionaries(KEY, VALUE, max_size=4))
        if inputs:
            lines.append("    with:")
            for key, value in inputs.items():
                lines += [draw(NOISE), f"      {key}: {value}"]
        lines.append("    secrets: inherit")
        if publish:
            calls.append(inputs)
    return "\n".join(lines) + "\n", calls


def read(text: str) -> list[dict[str, str]]:
    """Read a workflow's publish calls from a file.

    Returns:
        The calls.

    """
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "release.yml"
        path.write_text(text, encoding="utf-8")
        return lp.publish_calls(path)


class Stub(unittest.TestCase):
    """The publish calls' inputs, as written."""

    @given(workflow())
    def test_inputs_read_back(self, drawn: tuple[str, list[dict[str, str]]]) -> None:
        """Each publish job's `with:` block, and only those."""
        text, calls = drawn
        self.assertEqual(read(text), calls)

    @given(st.text(max_size=200))
    def test_any_text_is_read(self, text: str) -> None:
        """A list of calls, never a traceback."""
        self.assertIsInstance(read(text), list)


class Scalar(unittest.TestCase):
    """A plain scalar, however quoted; anything else refused."""

    @given(VALUE, st.sampled_from(["", "'", '"']), st.sampled_from(["", "  # why"]))
    def test_plain_scalar_reads_bare(
        self, value: str, quote: str, comment: str
    ) -> None:
        """Quotes and comment stripped."""
        self.assertEqual(lp.scalar(f"{quote}{value}{quote}{comment}", "w"), value)

    @given(st.sampled_from([">", "|", "[a]", "{a: b}", "&x", "*x", "${{ inputs.x }}"]))
    def test_other_forms_refused(self, raw: str) -> None:
        """UnreadableError, by name."""
        with self.assertRaises(lp.UnreadableError):
            lp.scalar(raw, "w")


class InstallsOn(unittest.TestCase):
    """An os restriction excludes what it does not name."""

    @given(
        st.lists(
            st.sampled_from(["linux", "macos", "windows"]), min_size=1, unique=True
        ),
        st.sampled_from(["linux", "macos", "windows"]),
        st.sampled_from(["x64", "arm64"]),
    )
    def test_restriction(self, allowed: list[str], os_name: str, arch: str) -> None:
        """Installs exactly where the restriction names the OS."""
        rows = [("1.0", allowed)]
        self.assertEqual(
            lp.installs_on(rows, "1.0", f"{os_name}-{arch}"), os_name in allowed
        )


if __name__ == "__main__":
    unittest.main()

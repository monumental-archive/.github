"""workflow_command.py, the one writer of workflow commands, on any text.

The property #1660 asks for, over any message and any property values:

- T: what the writer prints is exactly one line, and unescaping it as
  the runner does gives back the message, and each property's value,
  as given — whatever `%`, CR, LF, `:` or `,` they carry.

The runner's unescape is modelled apart from the writer: one scan left
to right, each `%25`, `%0D`, `%0A` (and, in a property, `%3A`, `%2C`)
read back as its character.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import io
import re
from contextlib import redirect_stdout

from hypothesis import example
from hypothesis import strategies as st

from belt import workflow_command
from belt.typed_hypothesis import given

DATA = {"%25": "%", "%0D": "\r", "%0A": "\n"}
PROPERTY = {**DATA, "%3A": ":", "%2C": ","}
WRITERS = {
    "error": workflow_command.error,
    "warning": workflow_command.warning,
    "notice": workflow_command.notice,
}
# Text that leans on what must be escaped.
TEXT = st.text(alphabet=st.characters() | st.sampled_from(list("%\r\n:,0ADC")))


def unescape(text: str, table: dict[str, str]) -> str:
    """Read escaped text back as the runner does.

    Returns:
        The text, each escape read once, left to right.

    """
    pattern = "|".join(re.escape(k) for k in table)

    def back(found: re.Match[str]) -> str:
        return table[found.group(0)]

    return re.sub(pattern, back, text)


class TestOneLineReadsBack:
    """T: one line, and the runner reads back what was given."""

    @given(
        st.sampled_from(sorted(WRITERS)),
        TEXT,
        st.one_of(st.none(), TEXT),
        st.one_of(st.none(), st.integers(1, 10**6)),
        st.one_of(st.none(), TEXT),
    )
    @example("error", "a\n::error::b", None, None, None)
    @example("notice", "%0A", "a:b,c", 7, "%")
    def test_one_line_reads_back(
        self,
        command: str,
        message: str,
        file: str | None,
        line: int | None,
        title: str | None,
    ) -> None:
        """Exactly one line; message and properties read back whole."""
        out = io.StringIO()
        with redirect_stdout(out):
            WRITERS[command](message, file=file, line=line, title=title)
        text = out.getvalue()
        assert text.endswith("\n"), text
        body = text[:-1]
        assert "\n" not in body
        assert "\r" not in body
        head, sep, data = body[2:].partition("::")
        assert body.startswith("::") and sep, body
        assert unescape(data, DATA) == message
        name, _, props = head.partition(" ")
        assert name == command
        given_props = {
            k: str(v)
            for k, v in (("file", file), ("line", line), ("title", title))
            if v is not None
        }
        read = {}
        if props:
            for pair in props.split(","):
                key, _, value = pair.partition("=")
                read[key] = unescape(value, PROPERTY)
        assert read == given_props

#!/usr/bin/env python3
"""typed_std.py's two readers, row by row (#1676).

`group`: a matched group, an optional group that did not take part, a
named group and group 0. `stdin_text`: standard input with text, and a
process started with none, in process and as a real process whose
descriptor 0 is closed. `urlopen` (#1682): urllib's answer and an
opener's, read through `Response`; an answer that is not one, refused
by name; and the law that no other tracked file opens a URL itself.

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import ast
import io
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import unittest
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Self, override
from unittest import mock

import call_names
import hermetic
import typed_std

if TYPE_CHECKING:
    from collections.abc import Callable

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
BASH = hermetic.tool("bash")
URL = "https://example.invalid/x"
# The calls typeshed answers as `Any`, made only in typed_std.py.
OPENS = frozenset({"urllib.request.urlopen", "urllib.request.OpenerDirector.open"})
OPENER = "urllib.request.OpenerDirector"
BUILD = "urllib.request.build_opener"
HOME = "mise/typed_std.py"
VERSION = re.compile(r"v(?P<major>\d+)\.(\d+)(-(?P<pre>\w+))?")


class Group(unittest.TestCase):
    """The group's text by its span, or None, never Any."""

    @override
    def setUp(self) -> None:
        """Match one version with no pre-release part."""
        found = VERSION.fullmatch("v1.70")
        if found is None:
            self.fail("the row's pattern did not match its text")
        self.match = found

    def test_a_matched_group(self) -> None:
        """A numbered group that took part: its text."""
        self.assertEqual("70", typed_std.group(self.match, 2))

    def test_an_unmatched_optional_group(self) -> None:
        """An optional group that did not take part: None."""
        self.assertIsNone(typed_std.group(self.match, 3))
        self.assertIsNone(typed_std.group(self.match, "pre"))

    def test_a_named_group(self) -> None:
        """A group by its name."""
        self.assertEqual("1", typed_std.group(self.match, "major"))

    def test_group_zero(self) -> None:
        """The whole match, by default and by number."""
        self.assertEqual("v1.70", typed_std.group(self.match))
        self.assertEqual("v1.70", typed_std.group(self.match, 0))

    def test_an_empty_group_is_text(self) -> None:
        """A group that took part and matched nothing is "", not None."""
        found = re.match(r"(a*)b", "b")
        if found is None:
            self.fail("the row's pattern did not match its text")
        self.assertEqual("", typed_std.group(found, 1))


class StdinText(unittest.TestCase):
    """All of standard input, or the one refusal."""

    def test_text(self) -> None:
        """Standard input's text, read to its end."""
        with mock.patch("sys.stdin", io.StringIO("one\ntwo\n")):
            self.assertEqual("one\ntwo\n", typed_std.stdin_text())

    def test_absent_in_process(self) -> None:
        """No standard input (sys.stdin is None): refused by name."""
        with (
            mock.patch("sys.stdin", None),
            self.assertRaises(typed_std.NoStdinError) as caught,
        ):
            typed_std.stdin_text()
        self.assertEqual(
            "no standard input: the process was started without one",
            str(caught.exception),
        )

    def test_absent_in_a_real_process(self) -> None:
        """A process whose descriptor 0 is closed: the same refusal."""
        program = (
            "import sys\n"
            f"sys.path.append({str(HERE)!r})\n"
            "import typed_std\n"
            "try:\n"
            "    typed_std.stdin_text()\n"
            "except typed_std.NoStdinError as err:\n"
            "    print(err)\n"
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        done = subprocess.run(
            [BASH, "-c", 'exec 0<&-; exec "$0" -c "$1"', sys.executable, program],
            capture_output=True,
            text=True,
            check=True,
            env=hermetic.env(),
        )
        self.assertEqual(
            "no standard input: the process was started without one\n", done.stdout
        )


class Answer:
    """An HTTP answer with exactly what `Response` names."""

    def __init__(self, status: int, body: bytes, ctype: str) -> None:
        """Hold the answer's status, body and Content-Type."""
        self.status = status
        self.body = body
        self.ctype = ctype

    def read(self) -> bytes:
        """Read the body.

        Returns:
            The body.

        """
        return self.body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        """Read the one header this answer carries.

        Returns:
            The Content-Type, or `default` for any other name.

        """
        return self.ctype if name == "Content-Type" else default

    def __enter__(self) -> Self:
        """Enter.

        Returns:
            The answer.

        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave; nothing to close."""


class Opener(urllib.request.OpenerDirector):
    """An opener that answers one canned value and records the request."""

    def __init__(self, answer: object) -> None:
        """Hold the answer every open gives."""
        super().__init__()
        self.answer = answer
        self.asked: list[tuple[str, float | None]] = []

    @override
    def open(
        self,
        fullurl: str | urllib.request.Request,
        data: object = None,
        timeout: float | None = None,
    ) -> object:
        """Answer the canned value.

        Returns:
            It.

        """
        url = fullurl if isinstance(fullurl, str) else fullurl.full_url
        del data
        self.asked.append((url, timeout))
        return self.answer


class Urlopen(unittest.TestCase):
    """One HTTP answer, typed; never `Any`."""

    def test_urllib_s_answer_is_returned_and_read(self) -> None:
        """No opener: urllib's own urlopen answers, with the timeout."""
        answer = Answer(200, b"body", "text/plain")
        timeouts: list[float] = []

        def opened(request: urllib.request.Request, *, timeout: float) -> Answer:
            del request
            timeouts.append(timeout)
            return answer

        # typed_std's urllib.request is this module: the one urlopen it calls.
        with (
            mock.patch.object(urllib.request, "urlopen", opened),
            typed_std.urlopen(urllib.request.Request(URL), timeout=7) as got,
        ):
            self.assertEqual(
                (200, b"body", "text/plain"),
                (got.status, got.read(), got.getheader("Content-Type")),
            )
        want: list[float] = [7]
        self.assertEqual(want, timeouts)

    def test_an_opener_s_answer_is_used(self) -> None:
        """An opener handed in answers instead of urllib's default."""
        opener = Opener(Answer(201, b"made", "application/json"))
        got = typed_std.urlopen(urllib.request.Request(URL), timeout=3, opener=opener)
        self.assertEqual((201, b"made"), (got.status, got.read()))
        want: list[tuple[str, float | None]] = [(URL, 3)]
        self.assertEqual(want, opener.asked)

    def test_an_answer_that_is_not_a_response_is_refused(self) -> None:
        """Refused by name, the URL and what came back named."""
        with self.assertRaises(typed_std.NotAResponseError) as caught:
            typed_std.urlopen(
                urllib.request.Request(URL), timeout=1, opener=Opener(None)
            )
        self.assertEqual(
            f"{URL} answered NoneType, not an HTTP response", str(caught.exception)
        )


def receivers(tree: ast.AST, qualify: Callable[[ast.expr], str | None]) -> set[str]:
    """Spell every expression that holds an opener, as written.

    Returns:
        Each assignment target whose value builds an opener, whose
        annotation is OpenerDirector, or whose value names one of those.

    """
    held: set[str] = set()
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and qualify(node.annotation) == OPENER:
                # A class's field is read as `self.<field>`.
                name = ast.unparse(node.target)
                held.update({name, f"self.{name}"})
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value = node.value
            builds = any(
                isinstance(n, ast.Call) and qualify(n.func) == BUILD
                for n in ast.walk(value)
            )
            names = any(ast.unparse(n) in held for n in ast.walk(value))
            if builds or names:
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                held.update(ast.unparse(t) for t in targets)
    return held


def opener_held(
    receiver: ast.expr, held: set[str], qualify: Callable[[ast.expr], str | None]
) -> bool:
    """Say whether `.open` is called on an opener.

    Returns:
        True for a receiver the file holds an opener in, or one it builds.

    """
    if ast.unparse(receiver) in held:
        return True
    return isinstance(receiver, ast.Call) and qualify(receiver.func) == BUILD


def opens(text: str) -> list[int]:
    """Find the lines that open a URL themselves.

    Returns:
        The line of each call to urlopen, OpenerDirector.open, or `.open`
        on an opener the file builds or annotates, in order.

    """
    tree = ast.parse(text)
    qualify = call_names.qualifier(tree)
    held = receivers(tree, qualify)
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if qualify(func) in OPENS or (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and opener_held(func.value, held, qualify)
        ):
            found.append(node.lineno)
    return sorted(found)


class TheLaw(unittest.TestCase):
    """No tracked file but typed_std.py opens a URL itself (#1682)."""

    def test_the_tree_holds_the_law(self) -> None:
        """Every tracked Python file opens URLs through typed_std."""
        root = hermetic.source(CANON)
        listed = hermetic.tracked(root, "*.py")
        breaches = [
            f"{path}:{line}: opens a URL outside {HOME}"
            for path in listed
            if path != HOME
            for line in opens((root / path).read_text(encoding="utf-8"))
        ]
        none: list[str] = []
        self.assertEqual(none, breaches)

    def test_each_spelling_is_an_open(self) -> None:
        """Planted: every way a file can open a URL itself is found."""
        for text in (
            "import urllib.request\nurllib.request.urlopen(r)\n",
            "import urllib.request as u\nu.urlopen(r)\n",
            "from urllib.request import urlopen as go\ngo(r)\n",
            "import urllib.request\nurllib.request.OpenerDirector.open(o, r)\n",
            "import urllib.request\nurllib.request.build_opener().open(r)\n",
            "import urllib.request\no = urllib.request.build_opener()\no.open(r)\n",
            (
                "import urllib.request\n"
                "class Z:\n"
                "    a: urllib.request.OpenerDirector\n"
                "    def f(self, r):\n"
                "        opener = self.a if r else self.a\n"
                "        opener.open(r)\n"
            ),
        ):
            with self.subTest(text=text):
                self.assertTrue(opens(text), text)

    def test_the_typed_opener_and_a_file_are_not_opens(self) -> None:
        """Planted: typed_std's urlopen, a path's open, and open() pass."""
        text = (
            "import typed_std\nfrom pathlib import Path\n"
            "typed_std.urlopen(r, timeout=1, opener=o)\n"
            "Path('x').open()\nopen('x')\n"
        )
        none: list[int] = []
        self.assertEqual(none, opens(text))


if __name__ == "__main__":
    unittest.main()

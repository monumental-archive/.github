"""Tests for rust_suppressions.py, one row per lexer and manifest branch (#1857).

`test_rust_lint.py` runs the task over planted trees; these rows hold
the helper's own branches: where text that reads `#![allow(` is not an
attribute (a comment, a doc comment, a string, a raw string, a char),
where an attribute's own literal holds `allow(`, and each manifest
shape that is or is not a lint table.
"""

import io
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded
from belt import rust_suppressions as rsup
from belt.typed_pytest import fixture

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:rust"


@fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


Found = list[tuple[int, str]]
NOTHING: Found = []
NO_KEYS: list[str] = []


def allows(text: str) -> Found:
    """Run the source scan.

    Returns:
        What `inner_allows` found.

    """
    return rsup.inner_allows(text)


# Only a real inner attribute that allows is found.
def test_plain_and_cfg_attr_allows_are_found() -> None:
    """Both shapes, with their lines, in source order."""
    text = "#![allow(dead_code)]\nfn f() {}\n#![cfg_attr(test, allow(x))]\n"
    want: Found = [
        (1, "#![allow(dead_code)]"),
        (3, "#![cfg_attr(test, allow(x))]"),
    ]
    assert want == allows(text)


def test_spacing_inside_the_opener_is_still_an_attribute() -> None:
    """`# ! [allow (x)]` is the same attribute to rustc."""
    want: Found = [(1, "# ! [allow (x)]")]
    assert want == allows("# ! [allow (x)]\n")


def test_an_inner_attribute_in_a_module_body_is_found() -> None:
    """Inner attributes open any module or fn body, not only a file."""
    text = "mod m {\n    #![allow(unused)]\n}\n"
    want: Found = [(2, "#![allow(unused)]")]
    assert want == allows(text)


def test_expect_and_outer_allow_are_not_this_checks() -> None:
    """An inner expect is legal; an outer allow is clippy's to refuse."""
    text = (
        '#![expect(clippy::unwrap_used, reason = "x")]\n'
        "#![cfg_attr(test, expect(dead_code))]\n"
        "#[allow(dead_code)]\nfn f() {}\n"
        "#![deny(clippy::allow_attributes)]\n"
    )
    assert allows(text) == NOTHING


def test_text_in_comments_and_literals_is_not_an_attribute() -> None:
    """Doc comments, block comments, strings, raw strings: none."""
    text = (
        "//! #![allow(dead_code)]\n"
        "// #![allow(dead_code)]\n"
        "/* outer /* #![allow(x)] */ still #![allow(y)] */\n"
        'const A: &str = "#![allow(x)] \\" #![allow(y)]";\n'
        'const B: &str = r#"#![allow(x)] " #![allow(y)]"#;\n'
        'const C: &[u8] = br"#![allow(x)]";\n'
        "const D: char = '#';\nconst E: char = '\\'';\n"
        "fn g<'a>(x: &'a str) -> &'a str { x }\n"
    )
    assert allows(text) == NOTHING


def test_allow_inside_an_attributes_own_literal_is_not_an_allow() -> None:
    """`allow(` in a reason or a doc string is not the attribute's."""
    text = (
        '#![expect(dead_code, reason = "we never allow(x) here")]\n'
        '#![doc = "allow(x)"]\n'
        '#![doc = r#"allow(]x"#]\n'
        "#![expect(dead_code /* allow(x) */)]\n"
    )
    assert allows(text) == NOTHING


def test_a_raw_identifier_is_not_a_raw_string() -> None:
    """`r#allow` is a name; the attribute after it is still read."""
    text = "fn r#allow() {}\n#![allow(x)]\n"
    want: Found = [(2, "#![allow(x)]")]
    assert want == allows(text)


@pytest.mark.parametrize("text", ["/* open", '"open', "'\\", 'r#"open', "#![allow(x"])
def test_unterminated_input_ends_the_scan(text: object) -> None:
    """An unclosed comment, string, char or attribute ends cleanly."""
    want: Found = [(1, "#![allow(x")]
    assert (want if text.startswith("#!") else NOTHING) == allows(text)


# Every `allow` level in every lint table, nothing else.
def test_every_table_and_both_forms_are_found() -> None:
    """`[lints.*]` and `[workspace.lints.*]`, string and table form."""
    manifest = decoded.load_toml(
        '[lints.rust]\ndead_code = "allow"\nunused = "warn"\n'
        '[lints.rustdoc]\nbroken = { level = "allow" }\n'
        '[workspace.lints.clippy]\nx = { level = "allow", priority = 1 }\n'
        'y = { level = "deny" }\n'
    )
    want = [
        '[lints.rust] dead_code = "allow"',
        '[lints.rustdoc] broken = "allow"',
        '[workspace.lints.clippy] x = "allow"',
    ]
    assert want == rsup.manifest_allows(manifest)


@pytest.mark.parametrize(
    "text",
    [
        "[lints]\nworkspace = true\n",
        'workspace = "x"\nlints = 1\n',
        '[package]\nname = "a"\n',
    ],
)
def test_shapes_that_are_no_lint_table_are_passed_over(text: object) -> None:
    """`workspace = true`, a scalar workspace, no lints at all."""
    assert rsup.manifest_allows(decoded.load_toml(text)) == NO_KEYS


# The whole-group grep, moved here from the task body.
def test_a_group_suppression_is_found_on_its_own_line() -> None:
    """Allow and expect of a group; a lint named after one is not."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases = {
            "allow.rs": ("  #![allow(clippy::pedantic)]\n", True),
            "expect.rs": (
                '#![expect(dead_code, clippy::all, reason = "x")]\n',
                True,
            ),
            "lint.rs": ("#![expect(clippy::cargo_common_metadata)]\n", False),
            "split.rs": ("#![allow(x,\n  clippy::pedantic)]\n", False),
        }
        for name, (text, want) in cases.items():
            (root / name).write_text(text)
            assert want == rsup.group_suppressed(root / name)
        assert not rsup.group_suppressed(root / "Cargo.toml")
        assert not rsup.group_suppressed(root / "gone.rs")


# The command: paths on stdin, findings and remedy on stderr.
@fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path, str], None]:
    """Stand each row in a tree as the task does, the files named on stdin.

    Returns:
        A function that steps into `root` with `names` on stdin.

    """

    def stand(root: Path, names: str) -> None:
        monkeypatch.setenv("MISE_TASK_NAME", TASK)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "stdin", io.StringIO(names))

    return stand


def test_findings_name_each_file_and_the_remedy(
    capfd: pytest.CaptureFixture[str], listed: Callable[[Path, str], None]
) -> None:
    """Both kinds reported together; exit 1."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Cargo.toml").write_text('[lints.rust]\nx = "allow"\n')
        (root / "lib.rs").write_text("\n#![allow(y)]\n")
        listed(root, "Cargo.toml\0\0lib.rs\0gone.rs\0")
        status = rsup.main([])
        out, err = capfd.readouterr()
    assert status == 1
    assert out == ""
    assert (
        "lint:rust: suppression clippy's allow_attributes cannot see:\n"
        '  Cargo.toml: [lints.rust] x = "allow"\n'
        "  lib.rs:2: #![allow(y)]\n" + "".join(f"{line}\n" for line in rsup.REMEDY)
    ) == err


def test_clean_input_prints_nothing(
    capfd: pytest.CaptureFixture[str], listed: Callable[[Path, str], None]
) -> None:
    """No finding: exit 0, silent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "lib.rs").write_text('#![expect(y, reason = "z")]\n')
        listed(root, "lib.rs\0")
        status = rsup.main([])
        out, err = capfd.readouterr()
    assert (status, out, err) == (0, "", "")


@pytest.mark.parametrize(
    ("name", "raw"), [("Cargo.toml", b"[lints\n"), ("lib.rs", b"\xff\n")]
)
def test_undecodable_files_are_refused_by_name(
    name: str,
    raw: bytes,
    capfd: pytest.CaptureFixture[str],
    listed: Callable[[Path, str], None],
) -> None:
    """A manifest that is not TOML, a source that is not UTF-8."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / name).write_bytes(raw)
        listed(Path(tmp), f"{name}\0")
        status = rsup.main([])
        out, err = capfd.readouterr()
        assert status == 1
        assert err.startswith(f"lint:rust: {name}: ")


def test_main_in_process(capfd: pytest.CaptureFixture[str]) -> None:
    """`main` read in-process, so its own lines are measured."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lib.rs"
        path.write_text("#![allow(y)]\n")
        stdin = sys.stdin
        sys.stdin = io.StringIO(f"{path}\0")
        try:
            status = rsup.main([])
            err = capfd.readouterr().err
        finally:
            sys.stdin = stdin
    assert status == 1
    assert f"  {path}:1: #![allow(y)]\n" in err

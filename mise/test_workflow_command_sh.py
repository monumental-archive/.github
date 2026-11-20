#!/usr/bin/env python3
"""The shell writer of workflow commands, and the law over the shell (#1660).

Rows for `mise/workflow-command.sh`, run under bash: what each function
writes for a message carrying a newline, a CR or a `%` (the escape bash
3.2 gets wrong when the `%` is unquoted), and for several arguments.

The law, read line by line from every tracked script, workflow step and
task body in the canon (the tree on disk: this suite runs with git aimed
at a decoy repository), the function file excepted:

1. A quoted string that begins a workflow command (`::name::` or
   `::name `) expands nothing inside its quotes: no `$` or backtick in
   double quotes, no `{` in a Python f-string, no jq interpolation or printf
   `%s` in single quotes. Outside text goes through `org_error` and its
   siblings, which escape it.
2. In a script, step or task that prints a workflow command, text with
   an expansion in it reaches the log only through the writer's
   functions (`org_error` … `org_say`, the plain line), or as
   an argument to a `printf` whose format is a literal whose conversions
   are all `%q` (bash writes the value as one shell word: a newline
   comes out escaped, on the same line). A raw value with a
   newline would begin a line of its own, which the runner reads as a
   command if it looks like one. Text written to a file, a pipe, a
   command substitution or a `{ … } >> file` group is not the log.

stdlib `unittest`, matching the rest of mise/.
"""

import os
import re

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import unittest
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parents[1]
WRITER = CANON / "mise" / "workflow-command.sh"
BASH = hermetic.tool("bash")
SKIP = {".git", "node_modules", "target", "vendor", "__pycache__", ".venv", "testdata"}

# Where a command begins: a quote, `::`, a name, then `::` or a space.
COMMAND = re.compile(r"""(f?)(["'])::[A-Za-z][A-Za-z0-9_-]*(?:::| )""")
# A unit prints a command when it writes one or calls the writer. The
# command's `::` opens the printed text: `clippy::all` is no command.
PRINTS = re.compile(
    r"(?:^|[\"'\s])::[A-Za-z][A-Za-z0-9_-]*(?:::| )"
    r"|\borg_(?:error|warning|notice|mask|group|endgroup)\b"
)
# An echo or printf at a command position, and what follows it.
PRINTER = re.compile(
    r"""(?:^|&&|\|\||;|\bthen\b|\belse\b|\bdo\b|[{(])\s*(echo|printf)\b(.*)$"""
)
# Output that goes to a file, a pipe or a substitution is not the log.
NOT_THE_LOG = re.compile(r""">>?\s*["$/\w]|\|\s*[\w$"]""")
# A printf conversion, `%%` aside.
CONVERSION = re.compile(r"%(?!%)[-+ #0-9.*]*[a-zA-Z]")


def bash(script: str) -> str:
    """Run a script with the writer sourced.

    Returns:
        Its standard output.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    done = subprocess.run(
        [BASH, "-c", f'. "{WRITER}"\n{script}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def quoted(line: str, start: int, quote: str) -> str:
    """Read one quoted string from its opening quote.

    Returns:
        The text between the quotes, to the line's end when unclosed.

    """
    i = start + 1
    while i < len(line):
        if line[i] == "\\" and quote == '"':
            i += 2
            continue
        if line[i] == quote:
            return line[start + 1 : i]
        i += 1
    return line[start + 1 :]


def expands(text: str, quote: str, *, fstring: bool) -> bool:
    """Say whether a quoted command string expands outside text.

    Returns:
        True when the string interpolates anything.

    """
    if quote == '"':
        # `\(` is jq's interpolation, inside the jq program's own quotes.
        return "$" in text or "`" in text or "\\(" in text or (fstring and "{" in text)
    return "\\(" in text or bool(re.search(r"%[sdq]", text))


def rule_one(line: str) -> bool:
    """Say whether a line writes a command that expands outside text.

    Returns:
        True when the line breaks rule 1.

    """
    for found in COMMAND.finditer(line):
        quote = found.group(2)
        start = found.start(2)
        if expands(quoted(line, start, quote), quote, fstring=bool(found.group(1))):
            return True
    return False


def literal_format(rest: str) -> tuple[str, str] | None:
    """Split a printf's arguments into a literal format and the rest.

    Returns:
        The format's text and the arguments after it, or None when the
        format is not one quoted literal.

    """
    rest = rest.lstrip()
    if rest[:1] not in {"'", '"'}:
        return None
    text = quoted(rest, 0, rest[0])
    after = rest[len(text) + 2 :]
    if rest[0] == '"' and ("$" in text or "`" in text):
        return None
    return text, after


def rule_two(line: str) -> bool:
    """Say whether a line writes outside text to the log unguarded.

    Returns:
        True when an echo, or a printf with anything but a literal
        all-`%q` format, prints an expansion to the log.

    """
    found = PRINTER.search(line)
    if not found:
        return False
    if "$(" in line[: found.start() + 1]:
        return False
    verb, rest = found.groups()
    if NOT_THE_LOG.search(rest) or not ("$" in rest or "`" in rest):
        return False
    if verb == "echo":
        return True
    split = literal_format(rest)
    if split is None:
        return True
    fmt, _ = split
    return any(c[-1] != "q" for c in CONVERSION.findall(fmt))


def grouped(unit: list[tuple[int, str]]) -> set[int]:
    """Find the lines inside `{ … } > file` groups: a file, not the log.

    Returns:
        The line numbers inside each such group.

    """
    inside: set[int] = set()
    opened: list[int] = []
    for n, line in unit:
        text = line.strip()
        if text == "{":
            opened.append(n)
        elif opened and re.match(r"^\}\s*>>?\s*\S", text):
            start = opened.pop()
            inside.update(m for m, _ in unit if start < m < n)
        elif opened and text.startswith("}"):
            opened.pop()
    return inside


def units(path: Path, text: str) -> list[list[tuple[int, str]]]:
    """Split a file into the units rule 2 reads: steps, tasks or a script.

    Returns:
        Each unit's numbered lines.

    """
    lines = list(enumerate(text.split("\n"), 1))
    if path.suffix in {".yml", ".yaml"}:
        out = []
        for i, (_, line) in enumerate(lines):
            if re.match(r"^\s+run: [|>]", line):
                depth = len(line) - len(line.lstrip())
                body = []
                for n, inner in lines[i + 1 :]:
                    if inner.strip() and len(inner) - len(inner.lstrip()) <= depth:
                        break
                    body.append((n, inner))
                out.append(body)
        return out
    if path.suffix == ".toml":
        out, current = [], []
        for n, line in lines:
            if line.startswith("[tasks."):
                out.append(current)
                current = []
            current.append((n, line))
        return [*out, current]
    return [lines]


def held_files(root: Path) -> list[Path]:
    """List the shell the law reads: scripts, workflows, actions, task bodies.

    Returns:
        Each file, relative to the root.

    """
    found: list[Path] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP and (d == ".github" or not d.startswith("."))
        )
        for name in sorted(files):
            rel = (Path(here) / name).relative_to(root)
            held = name.endswith(".sh") or name in {"config.toml", "mise.toml"}
            held = held or (
                rel.parts[0] == ".github" and rel.suffix in {".yml", ".yaml"}
            )
            if held and rel != Path("mise/workflow-command.sh"):
                found.append(rel)
    return found


def findings(root: Path) -> list[str]:
    """Hold every shell file below `root` to both rules.

    Returns:
        `file:line rule` for each line that breaks one.

    """
    out: list[str] = []
    for rel in held_files(root):
        text = (root / rel).read_text(encoding="utf-8")
        for unit in units(rel, text):
            code = [(n, ln) for n, ln in unit if not ln.lstrip().startswith("#")]
            prints = any(PRINTS.search(ln) for _, ln in code)
            to_a_file = grouped(code)
            for n, line in code:
                if rule_one(line):
                    out.append(f"{rel}:{n} 1")
                elif prints and n not in to_a_file and rule_two(line):
                    out.append(f"{rel}:{n} 2")
    return out


class Writer(unittest.TestCase):
    """What the shell writer prints, one line each time."""

    def test_a_newline_cannot_begin_a_second_command(self) -> None:
        """#1660's row, in bash: a newline and a command are one line."""
        self.assertEqual(
            bash("org_error $'a\\n::error::b'"), "::error::a%0A::error::b\n"
        )

    def test_percent_is_replaced_never_appended(self) -> None:
        """The bash 3.2 trap: an unquoted `%` after `//` is the end anchor."""
        self.assertEqual(
            bash("org_warning $'50% of 1%\\r'"), "::warning::50%25 of 1%25%0D\n"
        )

    def test_arguments_join_as_echo_joins_them(self) -> None:
        """Several arguments are one message, one space apart."""
        self.assertEqual(bash("org_notice a 'b c' d"), "::notice::a b c d\n")

    def test_mask_group_and_endgroup(self) -> None:
        """The mask and the group escape too; endgroup is bare."""
        self.assertEqual(
            bash("org_mask $'s3cr\\net'; org_group $'log\\n::x::'; org_endgroup"),
            "::add-mask::s3cr%0Aet\n::group::log%0A::x::\n::endgroup::\n",
        )


class Say(unittest.TestCase):
    """org_say: the plain line, one line whatever the text holds."""

    def test_a_newline_and_a_command_stay_on_one_line(self) -> None:
        """LF and CR are written as the two characters backslash-n and backslash-r."""
        out = bash("org_say $'a\\n::error::x\\r'")
        self.assertEqual(out, "a\\n::error::x\\r\n")
        self.assertEqual(len(out.splitlines()), 1)

    def test_readable_text_is_unchanged(self) -> None:
        """Spaces, brackets, commas and quotes print as they are."""
        self.assertEqual(
            bash("org_say 'chore: release v1.1.0' '[16, 17]' \"it's 100%\""),
            "chore: release v1.1.0 [16, 17] it's 100%\n",
        )


class TheLaw(unittest.TestCase):
    """No shell line writes outside text into a command unescaped."""

    def test_the_tree_keeps_both_rules(self) -> None:
        """Every finding is a site to route through the writer."""
        self.assertEqual(findings(CANON), list[str]())

    def test_rule_one_sees_each_shape(self) -> None:
        """Expanding commands are refused; literal ones are not."""
        red = [
            'echo "::error::${x}"',
            'echo "::notice::$x resolves"',
            'echo "::warning::`date`"',
            "jq -r '.[] | \"::notice::\\(.doc)\"'",
            "printf '::error::%s\\n' \"$x\"",
            'print(f"::error::{path}")',
            'echo "::add-mask::${token}"',
            'echo "::set-output name=x::${v}"',
        ]
        green = [
            'echo "::error::stele is not on the runner"',
            '[[ -n ${ASSET} ]] || { echo "::error::no bundle" && exit 1; }',
            'echo "std::vec::Vec ${x}"',
            'org_error "${x}"',
            'print("::error::a literal")',
        ]
        for line in red:
            with self.subTest(red=line):
                self.assertTrue(rule_one(line))
        for line in green:
            with self.subTest(green=line):
                self.assertFalse(rule_one(line))

    def test_rule_two_allows_only_percent_q_for_outside_text(self) -> None:
        """An expansion reaches the log as a `%q` argument, or not at all."""
        red = [
            'echo "  ref: ${REF}"',
            'echo "${REF} resolves"',
            "printf '  ref: %s\\n' \"${REF}\"",
            'printf "  ref: ${REF}\\n"',
            'printf \'  %q and %s\\n\' "${a}" "${b}"',
            '[[ -n ${x} ]] || echo "${x}"',
            'echo "${refusals}" >&2',
        ]
        green = [
            "printf '  ref: %q\\n' \"${REF}\"",
            'printf \'  %q: %q\\n\' "${name}" "${result}" >&2',
            'echo "  a literal"',
            'echo "${x}" >> "${GITHUB_OUTPUT}"',
            'echo "${x}" | jq .',
            'flags=$(printf " -A %s" "${lint}")',
            "printf '100%% done\\n'",
        ]
        for line in red:
            with self.subTest(red=line):
                self.assertTrue(rule_two(line))
        for line in green:
            with self.subTest(green=line):
                self.assertFalse(rule_two(line))

    def test_a_group_written_to_a_file_is_not_the_log(self) -> None:
        """`{ echo "X=${x}"; } >> "${GITHUB_ENV}"` writes a file."""
        unit = [
            (1, "{"),
            (2, '  echo "SA_WORK=${work}"'),
            (3, '} >> "${GITHUB_ENV}"'),
            (4, 'echo "${x}"'),
        ]
        self.assertEqual(grouped(unit), {2})

    def test_clippy_lint_names_are_no_command(self) -> None:
        """`clippy::all` holds `::` and a name, but opens nothing."""
        self.assertIsNone(PRINTS.search('" -D clippy::all -D clippy::pedantic"'))
        self.assertIsNotNone(PRINTS.search('echo "::error::x"'))

    def test_percent_q_keeps_a_newline_and_a_command_on_one_line(self) -> None:
        """End to end: a literal command, then a detail line, is two lines."""
        out = bash(
            "v=$'a\\n::error::x'\n"
            'echo "::error::a literal command"\n'
            "printf '  planned: %q\\n' \"${v}\""
        )
        self.assertEqual(
            out.splitlines(),
            ["::error::a literal command", "  planned: $'a\\n::error::x'"],
        )

    def test_rule_two_reads_only_units_that_print_a_command(self) -> None:
        """A step that prints no command may echo its text as it likes."""
        with self.subTest(prints=False):
            unit = [(1, 'echo "${x}"')]
            self.assertFalse(any(PRINTS.search(ln) for _, ln in unit))
        with self.subTest(prints=True):
            unit = [(1, 'echo "${x}"'), (2, 'echo "::error::literal"')]
            self.assertTrue(any(PRINTS.search(ln) for _, ln in unit))


if __name__ == "__main__":
    unittest.main()

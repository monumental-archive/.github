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
2. In a script, step or task that prints a workflow command, no plain
   `echo` or `printf` begins its text with an expansion (`echo
   "${REF} …"`): a line of outside text must never begin a log line,
   where its first characters could be a command. Text written to a
   file or a pipe is not the log and is not held.

stdlib `unittest`, matching the rest of mise/.
"""

import os
import re
import shutil

# ruff: ignore[suspicious-subprocess-import]
import subprocess
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[1]
WRITER = CANON / "mise" / "workflow-command.sh"
BASH = shutil.which("bash") or "bash"
SKIP = {".git", "node_modules", "target", "vendor", "__pycache__", ".venv", "testdata"}

# Where a command begins: a quote, `::`, a name, then `::` or a space.
COMMAND = re.compile(r"""(f?)(["'])::[A-Za-z][A-Za-z0-9_-]*(?:::| )""")
# A unit prints a command when it writes one or calls the writer.
PRINTS = re.compile(
    r"::[A-Za-z][A-Za-z0-9_-]*(?:::| )"
    r"|\borg_(?:error|warning|notice|mask|group|endgroup)\b"
)
# An echo or printf, at a command position, whose text begins with `$`.
LEADING = re.compile(
    r"""(?:^|&&|\|\||;|\bthen\b|\belse\b|\bdo\b|[{(])\s*(?:echo|printf)(?:\s+-[a-zA-Z]+)*\s+"?\$"""
)
# Output that goes to a file or into a pipe is not the log.
NOT_THE_LOG = re.compile(r""">>?\s*["$/\w]|\|\s*[\w$"]""")


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


def rule_two(line: str) -> bool:
    """Say whether a line begins its log text with an expansion.

    Returns:
        True when the line breaks rule 2.

    """
    return bool(LEADING.search(line)) and not NOT_THE_LOG.search(
        line[LEADING.search(line).end() :]
    )


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
            for n, line in code:
                if rule_one(line):
                    out.append(f"{rel}:{n} 1")
                elif prints and rule_two(line):
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

    def test_rule_two_refuses_a_leading_expansion(self) -> None:
        """Outside text may follow a fixed prefix, never begin the line."""
        red = [
            'echo "${REF} resolves"',
            'echo "$x"',
            '[[ -n ${x} ]] || echo "${x}"',
            'echo "${refusals}" >&2',
        ]
        green = [
            'echo "  ref: ${REF}"',
            'echo "${x}" >> "${GITHUB_OUTPUT}"',
            'echo "${x}" | jq .',
            'echo "deriving ${name}"',
        ]
        for line in red:
            with self.subTest(red=line):
                self.assertTrue(rule_two(line))
        for line in green:
            with self.subTest(green=line):
                self.assertFalse(rule_two(line))

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

"""Tests for biome_suppressions.py, one row per branch (#2137).

The helper's rows hold each form (`-all`, `-start`, `-end`), each
comment shape biome honours (line, block, JSDoc, a block's later line,
CSS, JSONC, GraphQL), and each place the text is not a suppression (a
one-line ignore, a string, a template literal, a regex, prose).

`LintBiome` runs the belt's real `lint:biome` over a planted tree: the
`-all` and `-start`/`-end` plants are red by file, line and remedy, and
a one-line ignore that suppresses a finding, or the form inside a
string, is clean. Those rows are the before/after test: before the
helper, `lint:biome` was green on both red plants.
"""

import contextlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import biome_suppressions as bsup
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

# The task that runs the helper, whose name its findings carry (#1934).
TASK = "lint:biome"
BELT = support.MISE
SCAFFOLD = BELT.parent / "scaffold"
GIT = support.tool("git")
# biome colours its report whatever NO_COLOR says; the rows read words.
STYLE = re.compile(r"\x1b\[[0-9;]*m")

Found = list[tuple[int, str]]
NOTHING: Found = []


@pytest.fixture
def _task(as_task: Callable[[str], None]) -> None:
    """Run every row as the task does, recording no finding."""
    as_task(TASK)


pytestmark = pytest.mark.usefixtures("_task")


def wide(name: str, text: str) -> Found:
    """Run the scan on `text` as the file `name`.

    Returns:
        What `wide_ignores` found.

    """
    return bsup.wide_ignores(Path(name), text)


class TestForms:
    """Each wide form is found; the one-line form is not."""

    def test_all_is_found(self) -> None:
        """A file-wide ignore, as a line comment and as JSDoc."""
        text = (
            "// biome-ignore-all lint/suspicious/noDebugger: x\n"
            "/** biome-ignore-all lint/style/noMagicNumbers: y */\n"
        )
        want: Found = [(1, "biome-ignore-all"), (2, "biome-ignore-all")]
        assert want == wide("a.ts", text)

    def test_start_and_end_are_found(self) -> None:
        """Both ends of a range, each on its own line."""
        text = (
            "// biome-ignore-start lint/suspicious/noConsole: x\n"
            "export const a = 1;\n"
            "// biome-ignore-end lint/suspicious/noConsole: x\n"
        )
        want: Found = [(1, "biome-ignore-start"), (3, "biome-ignore-end")]
        assert want == wide("a.js", text)

    def test_a_block_comments_later_line_is_found(self) -> None:
        """The form past a JSDoc `*` on a later line, which biome honours."""
        text = "const a = 1;\n/*\n * biome-ignore-all lint/x/y: z\n */\n"
        want: Found = [(3, "biome-ignore-all")]
        assert want == wide("a.tsx", text)

    def test_the_one_line_form_passes(self) -> None:
        """The one-line form, which biome reports when it suppresses nothing."""
        text = "// biome-ignore lint/suspicious/noDebugger: x\ndebugger;\n"
        assert wide("a.ts", text) == NOTHING

    def test_a_longer_word_is_not_the_form(self) -> None:
        """`biome-ignore-allx` and `biome-ignore-all-y` are not forms."""
        text = "// biome-ignore-allx\n// biome-ignore-all-y\n// biome-ignore-ends\n"
        assert wide("a.ts", text) == NOTHING

    def test_prose_that_mentions_the_form_passes(self) -> None:
        """Mid-sentence, biome does not read it, and neither does this."""
        text = "// never write biome-ignore-all here\n/* see biome-ignore-start */\n"
        assert wide("a.ts", text) == NOTHING


class TestScriptLiterals:
    """In JavaScript and TypeScript, a literal's text is not a comment."""

    def test_the_form_in_a_string_passes(self) -> None:
        """Both quote styles, an escaped quote inside one."""
        text = (
            'const a = "// biome-ignore-all lint/x/y: z";\n'
            "const b = '\\' /* biome-ignore-start */';\n"
        )
        assert wide("a.ts", text) == NOTHING

    def test_the_form_in_a_template_literal_passes(self) -> None:
        """A template's text, across lines, is not a comment."""
        text = "const a = `\n// biome-ignore-all lint/x/y: z\n`;\n"
        assert wide("a.ts", text) == NOTHING

    def test_a_template_expression_holds_code_again(self) -> None:
        """A comment in `${…}` is one; the text after it is not."""
        text = (
            "const a = `${{ b: 1 }.b /* biome-ignore-all x */}\n"
            "// biome-ignore-end x\n`;\n"
            "// biome-ignore-start x\n"
        )
        want: Found = [(1, "biome-ignore-all"), (4, "biome-ignore-start")]
        assert want == wide("a.ts", text)

    def test_the_form_in_a_regex_passes(self) -> None:
        """A regex literal, a class holding `/`, after `return`."""
        text = (
            "const a = /\\/\\/ biome-ignore-all/u;\n"
            "const b = /[/]\\/* biome-ignore-start */u;\n"
            "function f() {\n  return /'/u;\n}\n"
        )
        assert wide("a.ts", text) == NOTHING

    def test_division_is_not_a_regex(self) -> None:
        """After an operand, `/` divides, so a comment after it counts."""
        text = "const a = (4) / 2; // biome-ignore-all x\nconst b = c / d;\n"
        want: Found = [(1, "biome-ignore-all")]
        assert want == wide("a.ts", text)

    def test_an_unterminated_string_ends_at_its_line(self) -> None:
        """A stray quote (JSX text, `don't`) cannot hide the next line."""
        text = "const a = <p>don't</p>;\n// biome-ignore-all x\n"
        want: Found = [(2, "biome-ignore-all")]
        assert want == wide("a.tsx", text)

    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("/* biome-ignore-all x", [(1, "biome-ignore-all")]),
            ("const a = `abc", []),
            ("const a = /abc", []),
        ],
    )
    def test_unterminated_input_ends_the_scan(self, text: object, want: object) -> None:
        """An open comment, template or regex at the end is no crash."""
        assert want == wide("a.js", text)


class TestOtherLanguages:
    """CSS, JSONC and GraphQL comments, by each one's own syntax."""

    def test_css_block_comment_is_found(self) -> None:
        """CSS has `/* */` only, and its strings are not comments."""
        text = (
            "/* biome-ignore-all lint/suspicious/noDuplicateProperties: x */\n"
            'a { content: "/* biome-ignore-start */"; }\n'
        )
        want: Found = [(1, "biome-ignore-all")]
        assert want == wide("a.css", text)

    def test_css_has_no_line_comment(self) -> None:
        """`//` in CSS is not a comment: the text after it is code."""
        text = "a { background: url(//x); } // biome-ignore-all x\n"
        assert wide("a.css", text) == NOTHING

    def test_jsonc_comments_are_found(self) -> None:
        """Line and block comments; a string value is not one."""
        text = (
            "// biome-ignore-all lint/suspicious/noDuplicateObjectKeys: x\n"
            '{"a": "// biome-ignore-start", /* biome-ignore-end x */ "b": 1}\n'
        )
        want: Found = [(1, "biome-ignore-all"), (2, "biome-ignore-end")]
        assert want == wide("a.jsonc", text)
        assert want == wide("a.json", text)

    def test_graphql_hash_comment_is_found(self) -> None:
        """`#` comments; quoted and block strings are not."""
        text = (
            "# biome-ignore-all lint/suspicious/noDuplicateFields: x\n"
            'query Q { a(s: "# biome-ignore-start") }\n'
            '"""\n# biome-ignore-end\n"""\n'
        )
        want: Found = [(1, "biome-ignore-all")]
        assert want == wide("a.graphql", text)
        assert want == wide("a.gql", text)

    def test_an_extension_biome_is_not_handed_is_passed_over(self) -> None:
        """The task hands biome only the extensions it parses."""
        assert wide("a.md", "// biome-ignore-all x\n") == NOTHING


class TestMain:
    """The command: paths on stdin, findings and remedy on stderr."""

    @staticmethod
    def run_helper(
        root: Path, names: str, capfd: pytest.CaptureFixture[str]
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper in `root` with `names` on stdin, in process.

        Returns:
            Its status and what it wrote, as a finished process's.

        """
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv("MISE_TASK_NAME", TASK)
            patched.chdir(root)
            patched.setattr(sys, "stdin", io.StringIO(names))
            status = bsup.main([])
        out, err = capfd.readouterr()
        return subprocess.CompletedProcess([], status, out, err)

    def test_findings_name_each_file_line_and_the_remedy(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Every file's forms together, then the remedy; exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.ts").write_text("\n// biome-ignore-all x\n")
            (root / "b.css").write_text("/* biome-ignore-start x */\n")
            done = self.run_helper(root, "a.ts\0\0b.css\0gone.ts\0", capfd)
        assert done.returncode == 1
        assert done.stdout == ""
        assert (
            f"lint:biome: {bsup.HEAD}\n"
            "  a.ts:2: biome-ignore-all\n"
            "  b.css:1: biome-ignore-start\n"
            "lint:biome: suppress one line, or change the config\n"
            + "".join(f"  {line}\n" for line in bsup.REMEDY)
        ) == done.stderr

    def test_clean_input_prints_nothing(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """No finding: exit 0, silent."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.ts").write_text("// biome-ignore lint/x/y: z\n")
            done = self.run_helper(root, "a.ts\0", capfd)
        assert (done.returncode, done.stdout, done.stderr) == (0, "", "")

    def test_a_file_that_is_not_utf8_is_refused_by_name(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Name a file biome could not read either."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.ts").write_bytes(b"\xff\n")
            done = self.run_helper(Path(tmp), "a.ts\0", capfd)
        assert done.returncode == 1
        assert done.stderr == "lint:biome: a.ts: not UTF-8\n"

    def test_main_in_process(self) -> None:
        """`main` read in-process, so its own lines are measured."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.ts"
            path.write_text("// biome-ignore-end x\n")
            err = io.StringIO()
            stdin = sys.stdin
            sys.stdin = io.StringIO(f"{path}\0")
            try:
                with contextlib.redirect_stderr(err):
                    status = bsup.main([])
            finally:
                sys.stdin = stdin
        assert status == 1
        assert f"  {path}:1: biome-ignore-end\n" in err.getvalue()


PACKAGE = '{\n  "name": "fixture",\n  "private": true,\n  "type": "module"\n}\n'
ALL = (
    "/** biome-ignore-all lint/suspicious/noDebugger: planted */\n"
    "export const PLANT_ALL: number = 0;\n"
)
RANGE = (
    "// biome-ignore-start lint/suspicious/noConsole: planted\n"
    "export const PLANT_RANGE: number = 1;\n"
    "// biome-ignore-end lint/suspicious/noConsole: planted\n"
)
ONE_LINE = (
    "export function plantLine(): void {\n"
    "  // biome-ignore lint/suspicious/noDebugger: planted\n"
    "  debugger;\n"
    "}\n"
)
IN_STRING = (
    'export const PLANT_STRING: string = "// biome-ignore-all lint/x/y: planted";\n'
)


def lint_biome(base: Path, source: str) -> subprocess.CompletedProcess[str]:
    """Plant `source` as `src/plant.ts` in a scaffolded tree; run the task.

    Returns:
        The finished `mise run lint:biome`, its output uncoloured.

    """
    tree = base / "tree"
    (tree / "src").mkdir(parents=True)
    # Installed, for a repository that declares no dependencies.
    (tree / "node_modules").mkdir()
    for name, dest in (("mise.toml", "mise.toml"), ("biome.json.stub", "biome.json")):
        text = (SCAFFOLD / name).read_text(encoding="utf-8")
        (tree / dest).write_text(text, encoding="utf-8")
    (tree / "package.json").write_text(PACKAGE, encoding="utf-8")
    (tree / "src" / "plant.ts").write_text(source, encoding="utf-8")
    env = support.env()
    for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
        subprocess.run(argv, cwd=tree, env=env, check=True)
    config = base / "config"
    config.mkdir()
    scratch = base / "tmp"
    scratch.mkdir()
    done = subprocess.run(
        [support.tool("mise"), "run", TASK],
        cwd=tree,
        env=support.env(
            MISE_CONFIG_DIR=str(config),
            XDG_CONFIG_HOME=str(config),
            MISE_GLOBAL_CONFIG_FILE=str(BELT / "config.toml"),
            MISE_TRUSTED_CONFIG_PATHS=f"{base}:{BELT.parent}",
            MISE_OFFLINE="1",
            MISE_YES="1",
            MISE_AUTO_INSTALL="0",
            MISE_TASK_RUN_AUTO_INSTALL="0",
            NO_COLOR="1",
            TMPDIR=str(scratch),
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    done.stdout = STYLE.sub("", done.stdout)
    done.stderr = STYLE.sub("", done.stderr)
    return done


class TestLintBiome:
    """The belt's real task: the before/after rows of #2137."""

    def assert_red(self, source: str, *where: str) -> None:
        """Assert the plant reds the task by file:line and remedy."""
        with tempfile.TemporaryDirectory() as tmp:
            done = lint_biome(Path(tmp), source)
        out = done.stdout + done.stderr
        assert done.returncode == 1, out
        assert bsup.HEAD in out
        for line in where:
            assert f"  {line}\n" in out
        for line in bsup.REMEDY:
            assert f"  {line}\n" in out
        assert "lint:biome: clean across" not in out

    def assert_clean(self, source: str) -> None:
        """Assert the plant leaves the task green."""
        with tempfile.TemporaryDirectory() as tmp:
            done = lint_biome(Path(tmp), source)
        out = done.stdout + done.stderr
        assert done.returncode == 0, out
        assert "lint:biome: clean across" in out

    def test_a_file_wide_ignore_is_red(self) -> None:
        """`biome-ignore-all`, which biome never reports unused."""
        self.assert_red(ALL, "src/plant.ts:1: biome-ignore-all")

    def test_a_range_ignore_is_red(self) -> None:
        """`biome-ignore-start` and `-end`, both named."""
        self.assert_red(
            RANGE,
            "src/plant.ts:1: biome-ignore-start",
            "src/plant.ts:3: biome-ignore-end",
        )

    def test_a_one_line_ignore_is_clean(self) -> None:
        """The form that expires, suppressing the finding it names."""
        self.assert_clean(ONE_LINE)

    def test_the_form_in_a_string_is_clean(self) -> None:
        """Text in a literal is not a suppression."""
        self.assert_clean(IN_STRING)

"""embedded_shell.py's `run:` reader and writer, on files nobody wrote.

The properties of `test_embedded_shell.py`'s fixed rows (#1555, #1846),
over generated workflow steps and debt lines. Each tests a pure
function: what yq reads from a file is the I/O layer's, held by the
rows of test_workflow_yaml.py for every way a `run:` is written; here
yq's answer is the one the drawn file's model says it gives.

- Round trip (T): every `run:` value yq answers for a workflow, however
  it is written — a literal block, anchored or not, a plain, single- or
  double-quoted single-line value, a flow mapping, a quoted key — reads
  through `runs_from()` as the value generated, with its step's name,
  its `shell:` (bash when none) and its line, whatever indentation the
  file uses and whatever blank lines follow a block.
- Round trip (T): `rewritten` with a formatter that returns each script as
  it is leaves the file byte for byte; with one that changes each
  script, every literal block reads back formatted, every other value
  as it was, and every line outside the blocks as it was.
- Invariance (V): a folded block of plain lines reads as its lines
  joined by single spaces, and weighs as many lines as the file holds.
- Refusal (R): `records` reads any answer into values or refuses it
  with a ValueError, `runs_from` reads any lines beside them, and
  `parse_entry` reads any debt line; none raises anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

from typing import TYPE_CHECKING

from hypothesis import example
from hypothesis import strategies as st

from belt import embedded_shell as es
from belt import workflow_yaml
from belt.typed_hypothesis import given

if TYPE_CHECKING:
    from collections.abc import Callable

TEXT = st.from_regex(r"[a-z][a-z0-9 =$\"'_./-]{0,20}", fullmatch=True).map(str.rstrip)
# A single-line command every scalar style holds as written: no quote,
# no `#`, no `:`, no flow indicator, and a leading word YAML resolves to
# no boolean or number.
LINE = st.from_regex(r"echo [a-z0-9 =$_./-]{0,15}", fullmatch=True).map(str.rstrip)


def well_ended(lines: list[str]) -> bool:
    """Tell whether a script starts unindented and ends on a non-blank line.

    Returns:
        True when it does.

    """
    return lines[0][:1] not in {"", " "} and bool(lines[-1].strip())


SCRIPT = st.lists(
    st.one_of(TEXT, st.just(""), TEXT.map("  {}".format)), min_size=1, max_size=5
).filter(well_ended)
# A name YAML reads as a string in every style: never a word it resolves.
RESOLVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}


def unresolved(name: str) -> bool:
    """Tell whether YAML reads a plain name as a string.

    Returns:
        True when it does.

    """
    return name.lower() not in RESOLVED


NAME = (
    st
    .from_regex(r"[A-Za-z][A-Za-z0-9 ]{0,12}", fullmatch=True)
    .map(str.strip)
    .filter(unresolved)
)
# A block whose body is a blank line, then text; and a value YAML reads
# as a boolean, which the parser refuses by name.
BLANK_THEN_TEXT: list[str] = ["run: |", "   ", "x"]
NO_ANSWER: object = []
RUN_TRUE_LINES: list[str] = ["runs:", "  steps:", "    - run: true"]
RUN_TRUE: object = [
    {"line": "3", "style": "", "kind": "scalar", "shell": None, "name": None}
]
# Every way a step can write its `run:`, and the style yq says it is.
STYLES = {
    "literal": "literal",
    "anchored": "literal",
    "plain": "",
    "single": "single",
    "double": "double",
    "flow": "",
    "quoted-key": "",
}
FORMS = tuple(STYLES)
BLOCKS = {"literal", "anchored"}
# A value's line, text, step name and shell, as the step writes them.
Value = tuple[int, str, str | None, str]
# The file's lines, each value, and what yq answers for each.
Drawn = tuple[list[str], list[Value], list[workflow_yaml.Run]]


def draw_step(
    draw: st.DrawFn, i: int, base: int, lines: list[str]
) -> tuple[Value, workflow_yaml.Run]:
    """Write one step, its `run:` in a drawn form, onto the file's lines.

    Returns:
        The value's line (1-based), text, step name and shell; and the
        record yq answers for it.

    """
    form = draw(st.sampled_from(FORMS))
    value, shell = written(draw, i, base, lines, form)
    line, text, name, _ = value
    path = f"jobs/build/steps/{i}"
    return value, workflow_yaml.Run(
        line, STYLES[form], text, name, shell, path, alias=False
    )


def written(
    draw: st.DrawFn, i: int, base: int, lines: list[str], form: str
) -> tuple[Value, str | None]:
    """Write one step's `run:` in `form`.

    Returns:
        The value as `draw_step` gives it, and the `shell:` written.

    """
    name = draw(st.one_of(st.none(), NAME.filter(bool)))
    quote = draw(st.sampled_from(["", "'", '"']))
    shell = draw(st.sampled_from([None, "bash", "sh", "python"]))
    lead = " " * base + "- "
    keys = " " * (base + 2)
    if form == "flow":
        text = draw(LINE)
        pairs = [f"name: {quote}{name}{quote}"] if name is not None else []
        pairs += [f"shell: {shell}"] if shell is not None else []
        lines.append(lead + "{" + ", ".join([*pairs, f"run: {text}"]) + "}")
        return (len(lines), text, name, shell or "bash"), shell
    if name is not None:
        lines.append(f"{lead}name: {quote}{name}{quote}")
        lead = keys
    if shell is not None:
        lines.append(f"{lead}shell: {shell}")
        lead = keys
    if form in BLOCKS:
        script = draw(SCRIPT)
        anchor = f"&a{i} " if form == "anchored" else ""
        lines.append(f"{lead}run: {anchor}|")
        header = len(lines)
        pad = " " * (base + 2 + draw(st.sampled_from([2, 4])))
        lines += [(pad + s) if s else "" for s in script]
        lines += [""] * draw(st.integers(0, 2))
        return (header, "\n".join(script) + "\n", name, shell or "bash"), shell
    text = draw(LINE)
    key = '"run"' if form == "quoted-key" else "run"
    held = {"single": f"'{text}'", "double": f'"{text}"'}.get(form, text)
    lines.append(f"{lead}{key}: {held}")
    return (len(lines), text, name, shell or "bash"), shell


def draw_workflow(draw: st.DrawFn) -> Drawn:
    """Write a job's steps, each a `run:` value in a drawn form.

    Returns:
        The file's lines; per value its line, text, name and shell; and
        yq's record of each.

    """
    base = draw(st.sampled_from([4, 6]))
    lines = ["on: push", "jobs:", "  build:", "    runs-on: ubuntu-latest"]
    lines.append(" " * (base - 2) + "steps:")
    drawn = [draw_step(draw, i, base, lines) for i in range(draw(st.integers(1, 4)))]
    return lines, [value for value, _ in drawn], [record for _, record in drawn]


workflow = st.composite(draw_workflow)


# A workflow's values, as written.
@given(workflow())
def test_values_read_back(drawn: Drawn) -> None:
    """Line, text, name and shell of every value, in every form."""
    lines, expected, answered = drawn
    got = [(r.line, r.text, r.name, r.shell) for r in es.runs_from(lines, answered)]
    assert got == expected, "\n".join(lines)


# Anything a JSON answer can hold, in the places RUNS's records go.
SCALAR: st.SearchStrategy[object] = (
    st.none() | st.booleans() | st.integers() | st.text(max_size=8)
)
RECORD: st.SearchStrategy[object] = st.dictionaries(
    st.sampled_from(["line", "style", "kind", "shell", "name", "path", "run"]),
    SCALAR,
    max_size=7,
)
ANSWER: st.SearchStrategy[object] = SCALAR | st.lists(RECORD | SCALAR, max_size=3)


@given(st.lists(st.text(max_size=30), max_size=12), ANSWER)
@example(BLANK_THEN_TEXT, NO_ANSWER)
@example(RUN_TRUE_LINES, RUN_TRUE)
def test_any_answer_is_read_or_refused(lines: list[str], answer: object) -> None:
    """Values or a ValueError naming where, never a traceback."""
    try:
        values = workflow_yaml.records(answer)
    except ValueError:
        return
    for run in es.runs_from(lines, values):
        assert run.source.endswith("\n")


def formatter(*, change: bool) -> Callable[[str], str | None]:
    """Stand in for shfmt: echo the script, or append a marker line.

    Returns:
        A formatter for es.rewritten.

    """

    def run(source: str) -> str:
        return source + "echo formatted\n" if change else source

    return run


# The writer puts back exactly what the formatter gives, where it was.
@given(workflow(), st.sampled_from(["same", "changed"]))
def test_rewrite_round_trip(drawn: Drawn, how: str) -> None:
    """Unchanged scripts, unchanged lines; changed ones, where they were."""
    lines, expected, answered = drawn
    found = [r for r in es.runs_from(lines, answered) if es.shell_family(r)]
    count, after = es.rewritten(lines, found, formatter(change=how == "changed"))
    # A block's text ends in a line break; a single-line value's never.
    blocks = [v[3] in {"bash", "sh"} and v[1].endswith("\n") for v in expected]
    if how == "same":
        assert (count, after) == (0, lines)
        return
    assert count == sum(blocks)
    marked = [i for i, line in enumerate(after) if "echo formatted" in line]
    assert len(marked) == count
    kept = [line for line in after if "echo formatted" not in line]
    assert kept == lines


# Plain lines fold to one line, and weigh as many as the file holds.
@given(st.lists(TEXT.filter(bool), min_size=1, max_size=6))
def test_plain_lines_join_and_are_counted(body: list[str]) -> None:
    """The ceiling counts each of the file's lines, not the folded text."""
    lines = ["runs:", "  steps:", "    - run: >-"]
    lines += [f"        {x}" for x in body]
    lines.append("    - run: echo after")
    answered = [
        workflow_yaml.Run(
            3, "folded", " ".join(body), None, None, "runs/steps/0", alias=False
        ),
        workflow_yaml.Run(
            len(lines), "", "echo after", None, None, "runs/steps/1", alias=False
        ),
    ]
    [run, after] = list(es.runs_from(lines, answered))
    assert run.source == "\n".join(body) + "\n"
    assert es.weight(run.source) == len(body)
    assert after.source == "echo after\n"


# A debt line is an entry or a sentence, never a traceback.
@given(
    st.one_of(
        st.text(max_size=40),
        st.lists(st.text(max_size=10), min_size=4, max_size=4).map("\t".join),
    )
)
@example("ci.yml\tbuild\t²\t# #1")
def test_any_line_is_read(raw: str) -> None:
    """An Entry over the ceiling, or a message."""
    got = es.parse_entry(raw)
    if isinstance(got, str):
        assert got
    else:
        assert got.lines > es.CEILING


@given(
    st.from_regex(r"\.github/workflows/[a-z]{1,6}\.yml", fullmatch=True),
    NAME.filter(bool),
    st.integers(1, 200),
    st.integers(1, 9999),
)
@example(".github/workflows/a.yml", "A", 16, 1352)
def test_an_entry_reads_back(file: str, step: str, count: int, issue: int) -> None:
    """Written as the file spells it, read back as written."""
    note = f"# #{issue}"
    got = es.parse_entry(f"{file}\t{step}\t{count}\t{note}")
    # The ceiling's own issue is no remover: a note naming it must say
    # `unassigned` (the rule's own refusal, not a parse failure).
    umbrella = es.WORKFLOW.umbrella.search(note) is not None
    if count <= es.CEILING or umbrella:
        assert isinstance(got, str)
    else:
        assert got == es.Entry(file, step, count, 0)

"""workflow_yaml.py reads every shape a workflow can take (#1840).

The fixture is one workflow holding each shape the hand readers it
replaced did not know: a three-entry `needs:` list where a step's
indent would be, a single-quoted `run:`, a block header carrying a
comment (#1837), a flow mapping, a quoted `"run"` key, a folded block,
an anchored block and its alias, a step list at its key's own indent,
a reusable-workflow caller with no steps, and `defaults: run:`, which
holds a mapping and is no command. Each row reads one of them; the
refusals are asserted by message.
test`, with the belt's yq on PATH.
"""

from pathlib import Path

import pytest

from belt import decoded, workflow_yaml
from belt.typed_pytest import fixture

FIXTURE = """\
name: every shape
on: push
defaults:
  run:
    shell: bash
jobs:
  first:
    needs:
      - one
      - two
      - three
    runs-on: ubuntu-latest
    steps:
      - name: commented header
        run: |  # the comment #1837 hid a body behind
          echo one
          echo two
      - name: single
        run: 'echo "single"'
      - name: double
        shell: sh
        run: "echo double"
      - {name: flow, run: echo flow}
      - "run": echo quoted key
      - name: folded
        run: >-
          echo folded
          across lines
      - name: anchored
        run: &body |
          echo anchored
      - name: alias
        run: *body
      - name: inputs
        uses: example/action@0000000000000000000000000000000000000000
        with:
          path: one
          key: two
          run: echo $x
      - name: boolean
        run: true
      - name: number
        run: 1.10
  flush:
    steps:
    - name: at its key's indent
      run: echo flush
  caller:
    uses: example/repo/.github/workflows/ci.yml@0000000000000000000000000000000000000000
"""

ACTION = """\
name: an action
runs:
  using: composite
  steps:
    - name: inside
      shell: bash
      run: echo action
"""


class Fixture:
    """The fixture, read once per row from a file of its own."""

    def __init__(self, tmp_path: Path) -> None:
        """Write the fixture and the action to a scratch directory."""
        self.dir = Path(str(tmp_path))
        self.workflow = self.dir / "workflow.yml"
        self.workflow.write_text(FIXTURE, encoding="utf-8")
        self.action = self.dir / "action.yml"
        self.action.write_text(ACTION, encoding="utf-8")

    def first(self) -> list[dict[str, object]]:
        """Read the first job's steps.

        Returns:
            Its steps.

        """
        return workflow_yaml.steps(
            workflow_yaml.jobs(workflow_yaml.document(self.workflow))["first"]
        )


@fixture
def fixture_state(tmp_path: Path) -> Fixture:
    """Give each row its own Fixture.

    Returns:
        The row's Fixture.

    """
    return Fixture(tmp_path)


def test_a_needs_list_is_not_steps(fixture_state: Fixture) -> None:
    """A three-entry `needs:` list sits at a step's indent; it is no step."""
    names = [step.get("name") for step in fixture_state.first()]
    want = [
        "commented header",
        "single",
        "double",
        "flow",
        None,
        "folded",
        "anchored",
        "alias",
        "inputs",
        "boolean",
        "number",
    ]
    assert want == names


def test_a_single_quoted_run_comes_back_without_its_quotes(
    fixture_state: Fixture,
) -> None:
    """The value is what bash receives, not the YAML that spelt it."""
    (single,) = [s for s in fixture_state.first() if s.get("name") == "single"]
    assert single["run"] == 'echo "single"'


def test_a_commented_block_header_keeps_its_body(fixture_state: Fixture) -> None:
    """#1837: a comment after `|` hid a step's whole body."""
    (step,) = [s for s in fixture_state.first() if s.get("name") == "commented header"]
    assert step["run"] == "echo one\necho two\n"


def test_every_written_shape_is_read(fixture_state: Fixture) -> None:
    """Flow, quoted key, double quotes, folded, anchor and alias."""
    runs = [step.get("run") for step in fixture_state.first()]
    want = [
        "echo one\necho two\n",
        'echo "single"',
        "echo double",
        "echo flow",
        "echo quoted key",
        "echo folded across lines",
        "echo anchored\n",
        "echo anchored\n",
        None,
        True,
        1.1,
    ]
    assert want == runs


def test_with_is_a_mapping(fixture_state: Fixture) -> None:
    """A step's inputs are nested, in file order."""
    (step,) = [s for s in fixture_state.first() if s.get("name") == "inputs"]
    want: dict[str, object] = {"path": "one", "key": "two", "run": "echo $x"}
    assert want == step["with"]


def test_a_step_list_at_its_key_indent_is_read(fixture_state: Fixture) -> None:
    """`- ` flush with `steps:` is the same list."""
    found = workflow_yaml.jobs(workflow_yaml.document(fixture_state.workflow))["flush"]
    want: list[dict[str, object]] = [
        {"name": "at its key's indent", "run": "echo flush"}
    ]
    assert want == workflow_yaml.steps(found)


def test_a_reusable_workflow_caller_has_no_steps(fixture_state: Fixture) -> None:
    """A job that only `uses:` another workflow returns an empty list."""
    found = workflow_yaml.jobs(workflow_yaml.document(fixture_state.workflow))["caller"]
    assert list[dict[str, object]]() == workflow_yaml.steps(found)


def test_an_action_reads_through_its_runs(fixture_state: Fixture) -> None:
    """A composite action's steps are `runs.steps`."""
    action = workflow_yaml.document(fixture_state.action)
    want: list[dict[str, object]] = [
        {"name": "inside", "shell": "bash", "run": "echo action"}
    ]
    assert want == workflow_yaml.steps(decoded.as_object(action["runs"], "runs"))


# Each block header, commented or not, and the text bash receives from
# a body of `a: b` then `echo x` under it (#1837).
HEADERS = {
    "|": "a: b\necho x\n",
    "|-": "a: b\necho x",
    "|+": "a: b\necho x\n",
    ">": "a: b echo x\n",
    ">-": "a: b echo x",
    ">2": "a: b echo x\n",
    "|2-": "a: b\necho x",
    "|-2": "a: b\necho x",
    "| # note": "a: b\necho x\n",
    ">- # note": "a: b echo x",
}


class Headers(Fixture):
    """Every block header opens its body, in `run:` and in an input."""

    def read(self, text: str) -> list[dict[str, object]]:
        """Read the one job of a workflow written for a row.

        Returns:
            Its steps.

        """
        path = self.dir / "headers.yml"
        path.write_text(text, encoding="utf-8")
        (job,) = workflow_yaml.jobs(workflow_yaml.document(path)).values()
        return workflow_yaml.steps(job)


@fixture
def headers(tmp_path: Path) -> Headers:
    """Give each row its own Headers.

    Returns:
        The row's Headers.

    """
    return Headers(tmp_path)


@pytest.mark.parametrize(("header", "want"), HEADERS.items())
def test_each_header_form_opens_a_block(
    headers: Headers, header: str, want: str
) -> None:
    """Each chomping and indentation form, commented or not."""
    found = headers.read(
        "jobs:\n  j:\n    steps:\n      - name: s\n"
        f"        run: {header}\n          a: b\n          echo x\n"
    )
    steps: list[dict[str, object]] = [{"name": "s", "run": want}]
    assert steps == found


def test_an_input_header_opens_a_block(headers: Headers) -> None:
    """`with:` `script: | # note` is read as its body too."""
    found = headers.read(
        "jobs:\n  j:\n    steps:\n      - name: s\n        with:\n"
        "          script: | # note\n            a: b\n            echo x\n"
    )
    want: list[dict[str, object]] = [
        {"name": "s", "with": {"script": "a: b\necho x\n"}}
    ]
    assert want == found


def test_a_value_that_is_not_a_header_is_one_line(headers: Headers) -> None:
    """A pipe and a comment inside a command are not a header."""
    found = headers.read(
        "jobs:\n  j:\n    steps:\n      - name: s\n        run: echo a | cat # note\n"
    )
    want: list[dict[str, object]] = [{"name": "s", "run": "echo a | cat"}]
    assert want == found


def test_jobs_are_in_file_order(fixture_state: Fixture) -> None:
    """The mapping's order is the file's."""
    found = workflow_yaml.jobs(workflow_yaml.document(fixture_state.workflow))
    want: list[str] = ["first", "flush", "caller"]
    order: list[str] = list(found)
    assert want == order


def test_on_stays_the_string_on(fixture_state: Fixture) -> None:
    """YAML 1.1's boolean `on` is not what yq reads."""
    found = workflow_yaml.document(fixture_state.workflow)
    assert found["on"] == "push"


def test_a_file_with_no_jobs_has_none(fixture_state: Fixture) -> None:
    """An action has no `jobs`; reading them is empty, not an error."""
    none: dict[str, dict[str, object]] = {}
    assert none == workflow_yaml.jobs(workflow_yaml.document(fixture_state.action))


def test_a_file_yq_cannot_parse_is_refused_with_its_message(
    fixture_state: Fixture,
) -> None:
    """Never an empty document: yq's own message, by file."""
    broken = fixture_state.dir / "broken.yml"
    broken.write_text("jobs: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken\.yml: .*did not find"):
        workflow_yaml.document(broken)


def test_a_missing_file_is_refused(fixture_state: Fixture) -> None:
    """A path that is not there is not an empty workflow."""
    with pytest.raises(ValueError, match=r"absent\.yml: "):
        workflow_yaml.document(fixture_state.dir / "absent.yml")


def test_a_document_that_is_no_mapping_is_refused(fixture_state: Fixture) -> None:
    """A list at the top is not a workflow."""
    listed = fixture_state.dir / "list.yml"
    listed.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"list\.yml"):
        workflow_yaml.document(listed)


class Runs(Fixture):
    """`runs()` places each `run:` value and says how it was written."""

    def read_steps(self, steps: str) -> list[tuple[str, bool]]:
        """Read a job of the given step lines through runs().

        Returns:
            Each record's text and whether it is an alias.

        """
        path = self.dir / "steps.yml"
        path.write_text(f"jobs:\n  j:\n    steps:\n{steps}", encoding="utf-8")
        return [(r.text, r.alias) for r in workflow_yaml.runs(path)]


@fixture
def runs_state(tmp_path: Path) -> Runs:
    """Give each row its own Runs.

    Returns:
        The row's Runs.

    """
    return Runs(tmp_path)


def test_every_run_value_is_one_record(runs_state: Runs) -> None:
    """Line, style, text, name, shell, path, and the alias."""
    run = workflow_yaml.Run
    steps = "jobs/first/steps"
    want = [
        run(
            15,
            "literal",
            "echo one\necho two\n",
            "commented header",
            None,
            f"{steps}/0",
            alias=False,
        ),
        run(19, "single", 'echo "single"', "single", None, f"{steps}/1", alias=False),
        run(22, "double", "echo double", "double", "sh", f"{steps}/2", alias=False),
        run(23, "", "echo flow", "flow", None, f"{steps}/3", alias=False),
        run(24, "", "echo quoted key", None, None, f"{steps}/4", alias=False),
        run(
            26,
            "folded",
            "echo folded across lines",
            "folded",
            None,
            f"{steps}/5",
            alias=False,
        ),
        run(
            30,
            "literal",
            "echo anchored\n",
            "anchored",
            None,
            f"{steps}/6",
            alias=False,
        ),
        run(33, "", "echo anchored\n", "alias", None, f"{steps}/7", alias=True),
        run(41, "", "true", "boolean", None, f"{steps}/9", alias=False),
        run(43, "", "1.10", "number", None, f"{steps}/10", alias=False),
        run(
            47,
            "",
            "echo flush",
            "at its key's indent",
            None,
            "jobs/flush/steps/0",
            alias=False,
        ),
    ]
    assert want == workflow_yaml.runs(runs_state.workflow)


def test_defaults_run_is_no_command(runs_state: Runs) -> None:
    """`defaults: run:` holds a mapping; no record names it."""
    paths = [r.path for r in workflow_yaml.runs(runs_state.workflow)]
    assert "defaults" not in paths
    assert all(p.startswith("jobs/") for p in paths), paths


def test_an_input_named_run_is_no_command(runs_state: Runs) -> None:
    """`with: run:` is an input to the action; no record names it (#1861)."""
    inputs = runs_state.dir / "inputs.yml"
    inputs.write_text(
        "jobs:\n  j:\n    steps:\n      - uses: a/b@c\n        with:\n"
        "          run: echo $x\n      - {uses: a/b@c, with: {run: echo $y}}\n"
        "      - run: echo step\n",
        encoding="utf-8",
    )
    texts = [r.text for r in workflow_yaml.runs(inputs)]
    want: list[str] = ["echo step"]
    assert want == texts


def test_a_run_yaml_reads_as_no_string_is_its_text(runs_state: Runs) -> None:
    """`run: true` and `run: 1.10` are what GitHub runs, as written (#1861)."""
    found = {
        r.name: (r.text, r.style)
        for r in workflow_yaml.runs(runs_state.workflow)
        if r.name in {"boolean", "number"}
    }
    want: dict[str | None, tuple[str, str]] = {
        "boolean": ("true", ""),
        "number": ("1.10", ""),
    }
    assert want == found


def test_an_alias_of_a_non_string_is_its_anchors_text(runs_state: Runs) -> None:
    """`run: *a` of `run: &a true` is `true`, as the anchor is (#1861)."""
    found = runs_state.read_steps(
        "      - run: &a true\n      - run: *a\n      - run: &b 1.10\n      - run: *b\n"
    )
    want: list[tuple[str, bool]] = [
        ("true", False),
        ("true", True),
        ("1.10", False),
        ("1.10", True),
    ]
    assert want == found


def test_a_null_run_is_no_command(runs_state: Runs) -> None:
    """`run: ~`, `run: null` and a bare `run:` are empty text (#1861)."""
    found = runs_state.read_steps(
        "      - run: ~\n      - run: null\n      - run:\n        name: bare\n"
    )
    want: list[tuple[str, bool]] = [("", False), ("", False), ("", False)]
    assert want == found


def test_an_action_run_is_placed(runs_state: Runs) -> None:
    """A composite action's step is read with its shell."""
    want = [
        workflow_yaml.Run(
            7, "", "echo action", "inside", "bash", "runs/steps/0", alias=False
        )
    ]
    assert want == workflow_yaml.runs(runs_state.action)


def test_a_file_yq_cannot_parse_is_refused(runs_state: Runs) -> None:
    """runs() refuses as document() does."""
    broken = runs_state.dir / "broken.yml"
    broken.write_text("jobs: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken\.yml: "):
        workflow_yaml.runs(broken)

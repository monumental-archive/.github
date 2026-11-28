"""Fixed rows for `belt hypothesis-imports` (#1332, #1548, #1604).

Each row writes modules into a fresh directory and calls `main` in this
process, reading what it prints. The module sources are strings: this
file imports no Hypothesis, which is what the command looks for.
`prop_hypothesis_imports.py` holds the properties over generated
modules.
"""

from typing import TYPE_CHECKING

import pytest

from belt import hypothesis_imports

if TYPE_CHECKING:
    from pathlib import Path


def judged(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], *flags: str, **modules: str
) -> list[str]:
    """Write each module as `<name>.py` and run the command over them.

    Returns:
        The lines it printed, each path relative to `tmp_path`.

    """
    paths = []
    for name, text in modules.items():
        path = tmp_path / f"{name}.py"
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    assert hypothesis_imports.main([*flags, *paths]) == 0
    out = capfd.readouterr().out
    return [line.removeprefix(f"{tmp_path}/") for line in out.splitlines()]


@pytest.mark.parametrize(
    "source",
    [
        "import hypothesis\n",
        "import os, hypothesis.strategies as st\n",
        "from hypothesis import given\n",
        (
            "def f():\n    try:\n        from hypothesis.strategies import text\n"
            "    except ImportError:\n        pass\n"
        ),
    ],
    ids=["import", "dotted in a list", "from", "inside a try in a function"],
)
def test_an_absolute_import_anywhere_is_an_importer(
    source: str, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Top level, a list, a function or a `try`: each is named."""
    assert judged(tmp_path, capfd, module=source) == ["module.py"]


@pytest.mark.parametrize(
    "source",
    [
        'SOURCE = "import hypothesis"\n',
        "import hypothesis_x\nimport hypothesisx\n",
        "from . import hypothesis\n",
        "from .hypothesis import given\n",
        "import os\n",
    ],
    ids=["a string", "lookalike names", "relative", "relative module", "other"],
)
def test_a_lookalike_is_no_importer(
    source: str, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Hypothesis in a string, a lookalike or a relative import is not named."""
    assert judged(tmp_path, capfd, module=source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from hypothesis import given\n",
        "from hypothesis.core import given\n",
        "import hypothesis\n\n\n@hypothesis.given()\ndef test(): ...\n",
    ],
    ids=["from hypothesis", "from a submodule", "by attribute"],
)
def test_given_from_hypothesis_itself_is_named(
    source: str, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """With `--given`, each way of taking Hypothesis's own `given` is named."""
    assert judged(tmp_path, capfd, "--given", module=source) == ["module.py"]


@pytest.mark.parametrize(
    "source",
    [
        "from belt.typed_hypothesis import given\n",
        "from hypothesis import strategies, settings\n",
        "from . import given\n",
        "import hypothesis\n\nhypothesis.settings\n",
        "import other\n\nother.given\n",
    ],
    ids=["the belt's given", "other names", "relative", "other attribute", "other"],
)
def test_given_from_elsewhere_is_not_named(
    source: str, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """With `--given`, the belt's typed `given` and everything else pass."""
    assert judged(tmp_path, capfd, "--given", module=source) == []


def test_an_unreadable_file_is_named_with_its_reason(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A file that does not parse, or is not there, cannot be cleared."""
    missing = tmp_path / "gone.py"
    lines = judged(tmp_path, capfd, str(missing), broken="def (:\n")
    assert [line.partition(" ")[0] for line in lines] == ["gone.py", "broken.py"]
    assert lines[0].startswith("gone.py (could not be read: [Errno 2]")
    assert lines[1].startswith("broken.py (could not be read: invalid syntax")


def test_no_file_prints_nothing(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """No file given is no importer, exit 0."""
    assert judged(tmp_path, capfd) == []

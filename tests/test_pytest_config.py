"""The belt's pytest config, mise/pytest.toml, at maximum strictness (#2177).

Each row plants one test that a strictness option exists to refuse, runs
pytest on it with the delivered config (`-c`, as every belt run passes
it), and asserts the red: the exit status and pytest's own message. The
option set was probed on pytest 9.2.0 and posted on #2177's thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests import hermetic

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = hermetic.MISE / "pytest.toml"
# pytest's exit statuses (pytest.ExitCode).
FAILED, INTERRUPTED, USAGE, NO_TESTS = 1, 2, 4, 5


def run(pytester: pytest.Pytester, config: Path = CONFIG) -> pytest.RunResult:
    """Run the planted tree under the config, as the belt does.

    Returns:
        pytest's result.

    """
    return pytester.runpytest_inprocess("-c", str(config), "--rootdir", str(pytester.path))


@pytest.mark.parametrize(
    ("source", "status", "line"),
    [
        pytest.param(
            "import pytest\n\n@pytest.mark.nosuch\ndef test_a():\n    pass\n",
            INTERRUPTED,
            "*'nosuch' not found in `markers` configuration option*",
            id="an unregistered marker",
        ),
        pytest.param(
            "import pytest\n\n@pytest.mark.xfail(reason='r')\ndef test_a():\n    pass\n",
            FAILED,
            "*[[]XPASS(strict)[]] r*",
            id="an xpass",
        ),
        pytest.param(
            "import warnings\n\ndef test_a():\n    warnings.warn('w', UserWarning)\n",
            FAILED,
            "*UserWarning: w*",
            id="a warning",
        ),
        pytest.param(
            "import pytest\n\n@pytest.mark.parametrize('x', [])\ndef test_a(x):\n"
            "    pass\n",
            INTERRUPTED,
            "*Empty parameter set in 'test_a' at line 3*",
            id="an empty parameter set",
        ),
        pytest.param(
            "import pytest\n\n@pytest.mark.parametrize('x', [1, 2], ids=['a', 'a'])\n"
            "def test_a(x):\n    pass\n",
            INTERRUPTED,
            "*Duplicate parametrization IDs detected, but"
            " strict_parametrization_ids is set.*",
            id="a duplicate id",
        ),
        pytest.param("X = 1\n", NO_TESTS, "*no tests ran*", id="no tests collected"),
    ],
)
def test_a_planted_row_is_red_by_its_option(
    pytester: pytest.Pytester, source: str, status: int, line: str
) -> None:
    """One row per option: its status and pytest's message."""
    pytester.makepyfile(test_row=source)
    result = run(pytester)
    assert result.ret == status
    result.stdout.fnmatch_lines([line])


@pytest.mark.parametrize(
    ("extra", "line"),
    [
        pytest.param(
            "no_such_key = 1\n",
            "*Unknown config option: no_such_key*",
            id="an unknown config key",
        ),
        pytest.param(
            'required_plugins = ["pytest-no-such-plugin"]\n',
            "*Missing required plugins: pytest-no-such-plugin*",
            id="a missing plugin",
        ),
        pytest.param(
            'minversion = "99.0"\n',
            "*'minversion' requires pytest-99.0, actual pytest-*",
            id="an older pytest",
        ),
    ],
)
def test_a_config_the_run_cannot_honour_is_refused(
    pytester: pytest.Pytester, extra: str, line: str
) -> None:
    """A key a strict config refuses, or a requirement unmet: exit 4."""
    pytester.makepyfile(test_row="def test_a():\n    pass\n")
    base = CONFIG.read_text(encoding="utf-8")
    for key in ("required_plugins", "minversion"):
        if extra.startswith(key):
            base = "\n".join(
                row for row in base.splitlines() if not row.startswith(key)
            ) + "\n"
    config = pytester.path / "planted.toml"
    config.write_text(base + extra, encoding="utf-8")
    result = run(pytester, config)
    assert result.ret == USAGE
    result.stderr.fnmatch_lines([line])


def test_the_delivered_config_passes_a_clean_tree(pytester: pytest.Pytester) -> None:
    """The control: one plain passing test is green under every option."""
    pytester.makepyfile(test_row="def test_a():\n    assert True\n")
    run(pytester).assert_outcomes(passed=1)

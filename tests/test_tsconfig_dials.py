"""Table tests for the tsconfig dial floor (#699).

One row per branch of each guard, both directions: a config that states
the org's level and the nearest one that does not. The org's level itself
is read from the DELIVERED `mise/tsc-flags.txt` rather than a copy, so a
dial added to that file is covered the moment it lands — which is the
whole point of deriving the comparison from it.

Mutation-checked: each test was run against a deliberately broken helper
and observed to fail. A floor check that admits a weaker config looks
exactly like a floor check that works.
"""

import io
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt import decoded, tsconfig_dials
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

FLAGS_FILE = support.MISE / "tsc-flags.txt"
REQUIRED, UNREADABLE = tsconfig_dials.dials(FLAGS_FILE)
# What a row expects when nothing is unreadable or nothing is found.
NONE: list[str] = []


def flags(*lines: str) -> Path:
    """Write a flags file into a temporary tree.

    Returns:
        Its path.

    """
    path = Path(tempfile.mkdtemp()) / "tsc-flags.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# The file the org actually ships must be comparable as it stands.
def test_every_flag_is_readable() -> None:
    """A flag this check cannot compare is a dial nobody enforces."""
    assert UNREADABLE == NONE


def test_the_level_is_not_empty() -> None:
    """An empty level would pass every config in the org."""
    assert REQUIRED


@pytest.mark.parametrize(
    "want", [pytest.param(want, id=key) for key, want in REQUIRED.items()]
)
def test_every_dial_is_a_boolean(*, want: bool) -> None:
    """The comparison is boolean; a non-boolean would need new law."""
    assert isinstance(want, bool)


def test_the_inverted_dials_are_required_false() -> None:
    """`--allowUnreachableCode false` must demand false, not true."""
    assert REQUIRED["allowUnreachableCode"] is False
    assert REQUIRED["allowUnusedLabels"] is False
    assert REQUIRED["skipLibCheck"] is False


def test_a_bare_flag_is_required_true() -> None:
    """The ordinary shape."""
    assert REQUIRED["strict"] is True
    assert REQUIRED["noUncheckedIndexedAccess"] is True


def test_declaration_is_required() -> None:
    """TS5069: isolatedDeclarations cannot be set without it."""
    assert "declaration" in REQUIRED
    assert "isolatedDeclarations" in REQUIRED


def test_adjudicated_lines_are_not_dials() -> None:
    """`# adjudicated:` records a decision; it enforces nothing."""
    assert "adjudicated:" not in REQUIRED
    assert "alwaysStrict" not in REQUIRED


# Reading the org's level out of the flags file.
def test_the_two_shapes_the_file_uses() -> None:
    """A bare flag and an explicit literal, and nothing else."""
    required, unreadable = tsconfig_dials.dials(
        flags("--strict", "--allowUnreachableCode false", "--noEmit true"),
    )
    assert unreadable == NONE
    want: dict[str, bool] = {
        "strict": True,
        "allowUnreachableCode": False,
        "noEmit": True,
    }
    assert required == want


def test_comments_and_blanks_are_not_dials() -> None:
    """The file is mostly prose; none of it may become a dial."""
    required, unreadable = tsconfig_dials.dials(
        flags(
            "# The strict family, which GROWS on its own",
            "",
            "--strict  # trailing reason",
            "# adjudicated: noImplicitAny — implied by --strict",
        ),
    )
    assert unreadable == NONE
    want: dict[str, bool] = {"strict": True}
    assert required == want


def test_a_value_the_check_cannot_compare_stops_it() -> None:
    """Guessing at an unknown shape would leave a dial unenforced."""
    _required, unreadable = tsconfig_dials.dials(flags("--target es2025"))
    assert len(unreadable) == 1
    assert "target" in unreadable[0]


def test_a_line_that_is_not_a_flag_stops_it() -> None:
    """A stray word must not be silently skipped."""
    _required, unreadable = tsconfig_dials.dials(flags("strict"))
    assert len(unreadable) == 1
    assert "not a flag" in unreadable[0]


# Comparing one resolved config against the level.
def test_a_config_at_the_level_passes() -> None:
    """Equality is the pass, for both polarities."""
    assert (
        tsconfig_dials.judge(
            {"strict": True, "skipLibCheck": False},
            {"strict": True, "skipLibCheck": False},
        )
        == NONE
    )


def test_an_absent_dial_fails() -> None:
    """Absent is the defect #699 is about, so absent must fail."""
    findings = tsconfig_dials.judge({}, {"strict": True})
    assert len(findings) == 1
    assert "absent" in findings[0]
    assert "strict" in findings[0]


def test_a_weaker_dial_fails_in_both_polarities() -> None:
    """`false` where the org wants true, and true where it wants false."""
    pairs: tuple[tuple[dict[str, object], bool], ...] = (
        ({"strict": False}, True),
        ({"skipLibCheck": True}, False),
    )
    for stated, want in pairs:
        key = next(iter(stated))
        findings = tsconfig_dials.judge(stated, {key: want})
        assert len(findings) == 1
        assert "weaker" in findings[0]


def test_the_live_finding_from_the_fixture() -> None:
    """monumental-archive states skipLibCheck true against the org's false."""
    findings = tsconfig_dials.judge({"skipLibCheck": True}, REQUIRED)
    assert any('"skipLibCheck": true is weaker' in f for f in findings)


def test_stricter_is_never_inspected() -> None:
    """A repo may name dials the org does not; the hazard is one-way."""
    assert (
        tsconfig_dials.judge(
            {"strict": True, "noImplicitAny": True, "target": "es2025"},
            {"strict": True},
        )
        == NONE
    )


def test_a_non_boolean_value_counts_as_weaker() -> None:
    """A dial set to something odd is not the org's level either."""
    findings = tsconfig_dials.judge({"strict": "yes"}, {"strict": True})
    assert len(findings) == 1


def test_findings_keep_the_flags_file_order() -> None:
    """The report reads in the order the org states its level."""
    findings = tsconfig_dials.judge({}, {"a": True, "b": True, "c": True})
    keys: list[str] = [f.split('"')[1] for f in findings]
    want: list[str] = ["a", "b", "c"]
    assert keys == want


# End to end, through the entry point the belt task pipes into.
@fixture
def fed(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
    """Put one resolved config, as JSON, on stdin, as the task pipes it.

    Returns:
        A function that feeds one document; None feeds a truncated one.

    """

    def feed(doc: object) -> None:
        text = json.dumps(doc) if doc is not None else "{"
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    return feed


def test_a_config_at_the_org_level_passes(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """Built from the delivered level itself, so it cannot drift."""
    fed({"compilerOptions": dict(REQUIRED)})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    out = capfd.readouterr().out
    assert status == 0
    assert f"{len(REQUIRED)} dial(s)" in out


def test_the_scaffold_stub_passes_its_own_check(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """A scaffold that fails the org's gate is worse than none."""
    stub = decoded.load_json(
        (support.CANON / "scaffold" / "tsconfig.json").read_text(
            encoding="utf-8",
        ),
    )
    fed(stub)
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 0, err


def test_a_stripped_config_fails_with_every_dial_named(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The old scaffold instruction, refused, and told what is missing."""
    fed({"compilerOptions": {"lib": ["es2025"], "module": "nodenext"}})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    for key in REQUIRED:
        assert f'"{key}"' in err


def test_one_weaker_dial_fails_the_whole_config(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The fixture's actual state."""
    options = dict(REQUIRED)
    options["skipLibCheck"] = True
    fed({"compilerOptions": options})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert "skipLibCheck" in err


def test_a_config_without_ts_extension_imports_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """One import form, the file on disk, so every config states it (#2107)."""
    options = dict(REQUIRED)
    options.pop("allowImportingTsExtensions", None)
    fed({"compilerOptions": options})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert '"allowImportingTsExtensions" is absent' in err


def test_a_config_with_no_compiler_options_fails(fed: Callable[[object], None]) -> None:
    """Nothing stated is nothing enforced."""
    fed({})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    assert status == 1


def test_a_compileroptions_that_is_not_an_object_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """Malformed input gets a sentence, not a traceback."""
    fed({"compilerOptions": []})
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert "not an object" in err


def test_a_resolved_config_that_is_not_an_object_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """The shrunk example, `[]`: a sentence, not AttributeError (#1555)."""
    shapes: tuple[object, ...] = ([], 1, "x")
    for resolved in shapes:
        fed(resolved)
        status = tsconfig_dials.main([
            "--flags",
            str(FLAGS_FILE),
            "--name",
            "tsconfig.json",
        ])
        err = capfd.readouterr().err
        assert status == 1
        assert "did not resolve to an object" in err


def test_unresolvable_json_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """Output from tsc that is not JSON must not pass."""
    fed(None)
    status = tsconfig_dials.main([
        "--flags",
        str(FLAGS_FILE),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert "did not resolve" in err


def test_a_flags_file_naming_no_dial_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """An empty level would silently pass every repo in the org."""
    fed({"compilerOptions": {}})
    status = tsconfig_dials.main([
        "--flags",
        str(flags("# nothing but prose")),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert "no dial" in err


def test_a_flags_file_the_check_cannot_read_fails(
    capfd: pytest.CaptureFixture[str], fed: Callable[[object], None]
) -> None:
    """Refuse rather than enforce a subset nobody chose."""
    fed({"compilerOptions": {"strict": True}})
    status = tsconfig_dials.main([
        "--flags",
        str(flags("--strict", "--target es2025")),
        "--name",
        "tsconfig.json",
    ])
    err = capfd.readouterr().err
    assert status == 1
    assert "cannot read" in err

"""Table tests for the reader of a caller's build-input pins (#1333).

One row per branch of the reader: each value form a `[tools]` entry
takes, each backend key a tool can be pinned under, and each refusal,
whose message is asserted whole — a refusal that exits 1 with the wrong
words sends a repository to the wrong remedy.
"""

import itertools
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from belt.release import caller_pins
from belt.typed_pytest import fixture
from tests import support

if TYPE_CHECKING:
    from collections.abc import Callable

INPUT_OF = "every class that ships a Rust binary"


@fixture
def _outside_mise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the helper by its command, as a run outside mise does (#1934).

    An in-process call reads argv, whatever task runs this suite; an
    empty ORG_FINDINGS records nothing (unset on a runner records to the
    job's file, #1976).
    """
    monkeypatch.setattr(sys, "argv", ["caller-pins"])
    monkeypatch.delenv("MISE_TASK_NAME", raising=False)
    monkeypatch.setenv("ORG_FINDINGS", "")


pytestmark = pytest.mark.usefixtures("_outside_mise")


@fixture
def repo(tmp_path: Path) -> Callable[..., Path]:
    """Lay out a repository holding a row's mise config.

    Returns:
        A function that writes `config` (None writes nothing) and
        returns the repository's root.

    """
    made = itertools.count()

    def lay_out(config: str | None, name: str = "mise.toml") -> Path:
        root = tmp_path / f"repo{next(made)}"
        root.mkdir()
        if config is not None:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config, "utf-8")
        return root

    return lay_out


def installing(*tools: str) -> list[str]:
    """Spell the install mode's argv for `tools`.

    Returns:
        The argv.

    """
    return ["install", "--input-of", INPUT_OF, *tools]


# Each form a `[tools]` value takes yields the version on PATH.
def test_a_single_version(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """A plain string is the version."""
    status = caller_pins.main(
        installing("rust"), root=repo('[tools]\nrust = "1.97.1"\n')
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1\n",
        "",
    )


def test_a_list_yields_its_first_entry(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """A second toolchain beside the first: the first is on PATH."""
    config = (
        "[tools]\nrust = [\n"
        '  { version = "1.97.1", components = "clippy,rustfmt,llvm-tools" },\n'
        '  { version = "nightly-2026-07-20", components = "rust-src" },\n'
        '  "1.82.0",\n]\n'
    )
    status = caller_pins.main(installing("rust"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "install=rust@1.97.1\n", "")


def test_a_list_of_strings_yields_its_first_entry(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The same rule for the plain-string list form."""
    config = '[tools]\nrust = ["1.96.0", "1.82.0"]\n'
    status = caller_pins.main(installing("rust"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "install=rust@1.96.0\n", "")


def test_an_inline_table(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """An inline table yields its `version`."""
    config = '[tools]\nrust = { version = "1.97.1", components = "llvm-tools" }\n'
    status = caller_pins.main(installing("rust"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (0, "install=rust@1.97.1\n", "")


def test_the_tools_come_out_in_the_order_named(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """install_args reads in the job's order, not the config's."""
    config = (
        '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
        'node = "26.13.0"\nrust = "1.97.1"\n'
    )
    status = caller_pins.main(installing("rust", "cargo-auditable"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1 github:rust-secure-code/cargo-auditable@0.7.7\n",
        "",
    )


# A tool is found by its name under whichever key pins it.
def test_a_tool_under_the_cargo_key(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The compile-from-source form, emitted under its own key."""
    config = '[tools]\nrust = "1.97.1"\n"cargo:cargo-pgrx" = "0.19.3"\n'
    status = caller_pins.main(installing("cargo-pgrx"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=cargo:cargo-pgrx@0.19.3\n",
        "",
    )


def test_the_same_tool_under_a_github_key(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The prebuilt form (#1334), read without being told the key."""
    config = '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
    status = caller_pins.main(installing("cargo-auditable"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=github:rust-secure-code/cargo-auditable@0.7.7\n",
        "",
    )


def test_a_key_with_options_keeps_them(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The key is the caller's, brackets and all."""
    config = '[tools]\n"ubi:owner/tool[exe=tool]" = "1.0.0"\n'
    status = caller_pins.main(installing("tool"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=ubi:owner/tool[exe=tool]@1.0.0\n",
        "",
    )


def test_the_same_tool_under_two_keys_is_refused(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Both would install and one would win: refused, by name."""
    config = (
        '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
        '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
    )
    status = caller_pins.main(installing("cargo-pgrx"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: cargo-pgrx is pinned under 2 keys in"
            " mise.toml"
            " (cargo:cargo-pgrx, github:pgcentralfoundation/cargo-pgrx);"
            " mise would install each and put one on PATH, so pin it under"
            " one key\n"
        ),
    )


# A needed input the config does not pin is refused by name.
def test_a_missing_pin(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The assertion wording the builds already used, no fallback."""
    status = caller_pins.main(
        installing("rust", "cargo-auditable"), root=repo('[tools]\nrust = "1.97.1"\n')
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: cargo-auditable missing — pin"
            " cargo-auditable in this"
            " repository's mise config; it is a build input of every class"
            " that ships a Rust binary\n"
        ),
    )


def test_every_missing_pin_is_named(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Two missing inputs, two lines: one run names both remedies."""
    code = caller_pins.main(
        installing("rust", "cargo-auditable"), root=repo('[tools]\njq = "1.8.2"\n')
    )
    out, err = capfd.readouterr()
    assert (code, out) == (1, "")
    want: list[str] = [
        (
            "::error::caller-pins: rust missing — pin rust in this"
            " repository's mise"
            " config; it is a build input of every class that ships a Rust"
            " binary"
        ),
        (
            "::error::caller-pins: cargo-auditable missing — pin"
            " cargo-auditable in this"
            " repository's mise config; it is a build input of every class"
            " that ships a Rust binary"
        ),
    ]
    assert want == err.splitlines()


def test_an_empty_version_is_missing(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """An empty list or table pins nothing."""
    code = caller_pins.main(installing("rust"), root=repo("[tools]\nrust = []\n"))
    err = capfd.readouterr().err
    assert code == 1
    assert "::error::caller-pins: rust missing — pin rust" in err


def test_no_config(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """No config at all: refused, naming what it could not read."""
    status = caller_pins.main(installing("rust", "cargo-auditable"), root=repo(None))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: no mise config in this repository"
            " (mise.toml,"
            " .mise.toml, mise/config.toml), so rust, cargo-auditable cannot"
            " be read; pin them in this repository's mise config: they are"
            " build inputs of every class that ships a Rust binary\n"
        ),
    )


@pytest.mark.parametrize(
    "config", ["tools = 0\n", "tools = [0]\n", 'tools = ["rust"]\n']
)
def test_tools_that_is_not_a_table(
    config: object, capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """`tools = 0` and `tools = [0]` refuse by name (#1556's shrunk examples).

    Both died with a TypeError before: the reader iterated the value
    as if it were a table. The gate's `pinned_names` reads them as
    pinning nothing.
    """
    status = caller_pins.main(installing("rust"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: tools in mise.toml is not a"
            " table, so no tool"
            " is pinned there; pin the build inputs of every class"
            " that ships a Rust binary under [tools]\n"
        ),
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mise.toml").write_text(config, "utf-8")
        none: set[str] = set()
        assert none == caller_pins.pinned_names(Path(tmp))


@pytest.mark.parametrize("name", [".mise.toml", "mise/config.toml"])
def test_the_other_config_names_are_read(
    name: object, capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """`.mise.toml` and `mise/config.toml` are configs too."""
    status = caller_pins.main(
        installing("rust"), root=repo('[tools]\nrust = "1.97.1"\n', name=name)
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1\n",
        "",
    )


# An input a job needs only where the repository has it.
CONFIG = '[tools]\nrust = "1.97.1"\n"go:golang.org/x/vuln/cmd/govulncheck" = "v1.1.4"\n'


def test_pinned_is_installed(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Pinned: emitted under its own key, after the needed tools."""
    status = caller_pins.main(
        installing("--if-pinned", "govulncheck", "rust"), root=repo(CONFIG)
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1 go:golang.org/x/vuln/cmd/govulncheck@v1.1.4\n",
        "",
    )


def test_unpinned_is_skipped(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Not pinned: nothing emitted and nothing refused."""
    status = caller_pins.main(
        installing("--if-pinned", "go", "rust"), root=repo(CONFIG)
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1\n",
        "",
    )


def test_the_same_input_unmarked_is_refused(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Without --if-pinned, the same absent pin is refused by name."""
    status = caller_pins.main(installing("go", "rust"), root=repo(CONFIG))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: go missing — pin go in this"
            " repository's mise"
            " config; it is a build input of every class that ships a"
            " Rust binary\n"
        ),
    )


def test_nothing_pinned_emits_an_empty_list(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The job's own belt keys still stand beside an empty list."""
    status = caller_pins.main(
        installing("--if-pinned", "go", "--if-pinned", "cargo-pgrx"), root=repo(CONFIG)
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=\n",
        "",
    )


def test_two_keys_are_refused_even_when_optional(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Optional is about presence, not about ambiguity."""
    config = (
        '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
        '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
    )
    status = caller_pins.main(
        installing("--if-pinned", "cargo-pgrx"), root=repo(config)
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: cargo-pgrx is pinned under 2 keys in"
            " mise.toml"
            " (cargo:cargo-pgrx, github:pgcentralfoundation/cargo-pgrx);"
            " mise would install each and put one on PATH, so pin it under"
            " one key\n"
        ),
    )


# The versions alone, for a build that installs in its own container.
ENV_CONFIG = (
    "[tools]\nrust = [\n"
    '  { version = "1.99.1", components = "clippy" },\n'
    '  { version = "nightly-2026-07-20" },\n]\n'
    '"cargo:cargo-pgrx" = "0.19.3"\n'
    '"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
)


def test_each_variable_gets_its_tool_version(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The pgrx build's three inputs, from either backend key."""
    status = caller_pins.main(
        [
            "env",
            "--input-of",
            "the pgrx-extension class",
            "RUST_VERSION=rust",
            "PGRX_VERSION=cargo-pgrx",
            "AUDITABLE_VERSION=cargo-auditable",
        ],
        root=repo(ENV_CONFIG),
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "RUST_VERSION=1.99.1\nPGRX_VERSION=0.19.3\nAUDITABLE_VERSION=0.7.7\n",
        "",
    )


def test_a_missing_pin_is_refused_with_the_class(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The same refusal, naming the class the input belongs to."""
    status = caller_pins.main(
        [
            "env",
            "--input-of",
            "the pgrx-extension class",
            "RUST_VERSION=rust",
            "PGRX_VERSION=cargo-pgrx",
        ],
        root=repo('[tools]\nrust = "1.99.1"\n'),
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: cargo-pgrx missing — pin cargo-pgrx in"
            " this"
            " repository's mise config; it is a build input of the"
            " pgrx-extension class\n"
        ),
    )


# A call that names nothing would install everything: refused.
def test_install_with_no_tools_is_a_usage_error() -> None:
    """An empty install_args is `mise install`, the whole config."""
    with pytest.raises(SystemExit) as raised:
        caller_pins.main(["install", "--input-of", INPUT_OF], root=Path())
    assert raised.value.code == support.USAGE


def test_env_with_a_malformed_pair_is_a_usage_error() -> None:
    """A pair without `=` names no variable."""
    with pytest.raises(SystemExit) as raised:
        caller_pins.main(["env", "--input-of", INPUT_OF, "rust"], root=Path())
    assert raised.value.code == support.USAGE


PG = '"aqua:theseus-rs/postgresql-binaries"'
PG_LIST = (
    "[tools]\n"
    'rust = "1.97.1"\n'
    f'{PG} = ["18.6.1", "17.11.1", "16.15.1", "15.19.1", "14.24.1"]\n'
)
PG_INPUT_OF = "the gate's pgrx legs"


def by_major(spec: str, *tools: str) -> list[str]:
    """Spell the install mode's argv with one `--major postgresql-binaries=SPEC`.

    Returns:
        The argv.

    """
    return [
        "install",
        "--input-of",
        PG_INPUT_OF,
        "--major",
        f"postgresql-binaries={spec}",
        *tools,
    ]


# A tool pinned at several majors yields the majors a job runs (#1408).
def test_the_legs_major_among_a_list(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """One leg, one Postgres: the entry that begins with its major."""
    status = caller_pins.main(by_major("16", "rust"), root=repo(PG_LIST))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1 aqua:theseus-rs/postgresql-binaries@16.15.1\n",
        "",
    )


def test_a_list_of_majors_yields_each_in_the_order_named(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The plan's whole list, as the coverage upload asks for it."""
    status = caller_pins.main(by_major("[14,18]"), root=repo(PG_LIST))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        (
            "install=aqua:theseus-rs/postgresql-binaries@14.24.1"
            " aqua:theseus-rs/postgresql-binaries@18.6.1\n"
        ),
        "",
    )


def test_the_plans_own_list_is_strings(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The list as `mise/pgrx-plan.sh` prints it: strings, not numbers.

    Recorded from the coverage upload of release-lab's gate run
    44207730352, which refused `["14","15","16","17","18"]` as a
    usage error.
    """
    status = caller_pins.main(by_major('["14","18"]'), root=repo(PG_LIST))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        (
            "install=aqua:theseus-rs/postgresql-binaries@14.24.1"
            " aqua:theseus-rs/postgresql-binaries@18.6.1\n"
        ),
        "",
    )


def test_the_legs_major_absent_is_refused_by_name(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """A planned major the pin has no entry for: refused, never newest."""
    status = caller_pins.main(by_major("13", "rust"), root=repo(PG_LIST))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: postgresql-binaries pins no 13.x in"
            " mise.toml (pinned:"
            " 18.6.1, 17.11.1, 16.15.1, 15.19.1, 14.24.1); this job runs"
            " major 13 — pin it beside the others; it is a build input of"
            " the gate's pgrx legs\n"
        ),
    )


def test_a_single_valued_pin_at_the_major(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """One Postgres pinned as a plain string serves its own major."""
    config = f'[tools]\n{PG} = "17.11.1"\n'
    status = caller_pins.main(by_major("17"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=aqua:theseus-rs/postgresql-binaries@17.11.1\n",
        "",
    )


def test_a_single_valued_pin_at_another_major_is_refused(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """...and only its own: the first-entry rule must not stand in."""
    config = f'[tools]\n{PG} = {{ version = "17.11.1" }}\n'
    status = caller_pins.main(by_major("18"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: postgresql-binaries pins no 18.x in"
            " mise.toml (pinned:"
            " 17.11.1); this job runs major 18 — pin it beside the others;"
            " it is a build input of the gate's pgrx legs\n"
        ),
    )


def test_a_major_pinned_twice_is_refused(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """Two entries at one major: mise installs both, the job takes either."""
    config = f'[tools]\n{PG} = ["18.6.1", "18.5.0", "17.11.1"]\n'
    status = caller_pins.main(by_major("18"), root=repo(config))
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: postgresql-binaries pins major 18 2"
            " times in mise.toml"
            " (18.6.1, 18.5.0); mise would install each and the job could"
            " take either, so pin one\n"
        ),
    )


def test_a_major_of_an_unpinned_tool_is_missing(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """A major asked of a tool the config does not pin at all."""
    status = caller_pins.main(
        by_major("18", "rust"), root=repo('[tools]\nrust = "1.97.1"\n')
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        1,
        "",
        (
            "::error::caller-pins: postgresql-binaries missing — pin"
            " postgresql-binaries"
            " in this repository's mise config; it is a build input of the"
            " gate's pgrx legs\n"
        ),
    )


def test_no_major_planned_asks_for_nothing(
    capfd: pytest.CaptureFixture[str], repo: Callable[..., Path]
) -> None:
    """The plan's `[]`: no extension, so no Postgres pin is needed."""
    status = caller_pins.main(
        by_major("[]", "rust"), root=repo('[tools]\nrust = "1.97.1"\n')
    )
    out, err = capfd.readouterr()
    assert (status, out, err) == (
        0,
        "install=rust@1.97.1\n",
        "",
    )


@pytest.mark.parametrize(
    "spec", ["pg18", '[18, "pg17"]', "[18.5]", '["-18"]', "true", ""]
)
def test_a_malformed_major_is_a_usage_error(
    spec: str, repo: Callable[..., Path]
) -> None:
    """Not a whole number or a list of them: refused before reading."""
    root = repo(PG_LIST)
    with pytest.raises(SystemExit) as exit_:
        caller_pins.main(by_major(spec), root=root)
    assert exit_.value.code == support.USAGE

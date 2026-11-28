"""Table tests for the reader of a caller's build-input pins (#1333).

One row per branch of the reader: each value form a `[tools]` entry
takes, each backend key a tool can be pinned under, and each refusal,
whose message is asserted whole — a refusal that exits 1 with the wrong
words sends a repository to the wrong remedy.
"""

import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from belt.release import caller_pins

INPUT_OF = "every class that ships a Rust binary"


@pytest.fixture
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


def run(
    config: str | None, *argv: str, name: str = "mise.toml"
) -> tuple[int, str, str]:
    """Run the reader against a repository holding `config`.

    Returns:
        The exit status, stdout and stderr.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if config is not None:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(config, "utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = caller_pins.main(list(argv), root=root)
        return code, out.getvalue(), err.getvalue()


def install(
    config: str | None, *tools: str, name: str = "mise.toml"
) -> tuple[int, str, str]:
    """Run the install mode for `tools` against `config`.

    Returns:
        The exit status, stdout and stderr.

    """
    return run(config, "install", "--input-of", INPUT_OF, *tools, name=name)


class TestVersions:
    """Each form a `[tools]` value takes yields the version on PATH."""

    def test_a_single_version(self) -> None:
        """A plain string is the version."""
        assert install('[tools]\nrust = "1.97.1"\n', "rust") == (
            0,
            "install=rust@1.97.1\n",
            "",
        )

    def test_a_list_yields_its_first_entry(self) -> None:
        """A second toolchain beside the first: the first is on PATH."""
        config = (
            "[tools]\nrust = [\n"
            '  { version = "1.97.1", components = "clippy,rustfmt,llvm-tools" },\n'
            '  { version = "nightly-2026-07-20", components = "rust-src" },\n'
            '  "1.82.0",\n]\n'
        )
        assert install(config, "rust") == (0, "install=rust@1.97.1\n", "")

    def test_a_list_of_strings_yields_its_first_entry(self) -> None:
        """The same rule for the plain-string list form."""
        config = '[tools]\nrust = ["1.96.0", "1.82.0"]\n'
        assert install(config, "rust") == (0, "install=rust@1.96.0\n", "")

    def test_an_inline_table(self) -> None:
        """An inline table yields its `version`."""
        config = '[tools]\nrust = { version = "1.97.1", components = "llvm-tools" }\n'
        assert install(config, "rust") == (0, "install=rust@1.97.1\n", "")

    def test_the_tools_come_out_in_the_order_named(self) -> None:
        """install_args reads in the job's order, not the config's."""
        config = (
            '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
            'node = "26.13.0"\nrust = "1.97.1"\n'
        )
        assert install(config, "rust", "cargo-auditable") == (
            0,
            "install=rust@1.97.1 github:rust-secure-code/cargo-auditable@0.7.7\n",
            "",
        )


class TestKeys:
    """A tool is found by its name under whichever key pins it."""

    def test_a_tool_under_the_cargo_key(self) -> None:
        """The compile-from-source form, emitted under its own key."""
        config = '[tools]\nrust = "1.97.1"\n"cargo:cargo-pgrx" = "0.19.3"\n'
        assert install(config, "cargo-pgrx") == (
            0,
            "install=cargo:cargo-pgrx@0.19.3\n",
            "",
        )

    def test_the_same_tool_under_a_github_key(self) -> None:
        """The prebuilt form (#1334), read without being told the key."""
        config = '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
        assert install(config, "cargo-auditable") == (
            0,
            "install=github:rust-secure-code/cargo-auditable@0.7.7\n",
            "",
        )

    def test_a_key_with_options_keeps_them(self) -> None:
        """The key is the caller's, brackets and all."""
        config = '[tools]\n"ubi:owner/tool[exe=tool]" = "1.0.0"\n'
        assert install(config, "tool") == (
            0,
            "install=ubi:owner/tool[exe=tool]@1.0.0\n",
            "",
        )

    def test_the_same_tool_under_two_keys_is_refused(self) -> None:
        """Both would install and one would win: refused, by name."""
        config = (
            '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
            '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
        )
        assert install(config, "cargo-pgrx") == (
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


class TestRefusals:
    """A needed input the config does not pin is refused by name."""

    def test_a_missing_pin(self) -> None:
        """The assertion wording the builds already used, no fallback."""
        assert install('[tools]\nrust = "1.97.1"\n', "rust", "cargo-auditable") == (
            1,
            "",
            (
                "::error::caller-pins: cargo-auditable missing — pin"
                " cargo-auditable in this"
                " repository's mise config; it is a build input of every class"
                " that ships a Rust binary\n"
            ),
        )

    def test_every_missing_pin_is_named(self) -> None:
        """Two missing inputs, two lines: one run names both remedies."""
        code, out, err = install('[tools]\njq = "1.8.2"\n', "rust", "cargo-auditable")
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

    def test_an_empty_version_is_missing(self) -> None:
        """An empty list or table pins nothing."""
        code, _, err = install("[tools]\nrust = []\n", "rust")
        assert code == 1
        assert "::error::caller-pins: rust missing — pin rust" in err

    def test_no_config(self) -> None:
        """No config at all: refused, naming what it could not read."""
        assert install(None, "rust", "cargo-auditable") == (
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
        "config", ("tools = 0\n", "tools = [0]\n", 'tools = ["rust"]\n')
    )
    def test_tools_that_is_not_a_table(self, config: object) -> None:
        """`tools = 0` and `tools = [0]` refuse by name (#1556's shrunk examples).

        Both died with a TypeError before: the reader iterated the value
        as if it were a table. The gate's `pinned_names` reads them as
        pinning nothing.
        """
        assert install(config, "rust") == (
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

    @pytest.mark.parametrize("name", (".mise.toml", "mise/config.toml"))
    def test_the_other_config_names_are_read(self, name: object) -> None:
        """`.mise.toml` and `mise/config.toml` are configs too."""
        assert install('[tools]\nrust = "1.97.1"\n', "rust", name=name) == (
            0,
            "install=rust@1.97.1\n",
            "",
        )


class TestIfPinned:
    """An input a job needs only where the repository has it."""

    CONFIG = (
        '[tools]\nrust = "1.97.1"\n"go:golang.org/x/vuln/cmd/govulncheck" = "v1.1.4"\n'
    )

    def test_pinned_is_installed(self) -> None:
        """Pinned: emitted under its own key, after the needed tools."""
        assert install(self.CONFIG, "--if-pinned", "govulncheck", "rust") == (
            0,
            "install=rust@1.97.1 go:golang.org/x/vuln/cmd/govulncheck@v1.1.4\n",
            "",
        )

    def test_unpinned_is_skipped(self) -> None:
        """Not pinned: nothing emitted and nothing refused."""
        assert install(self.CONFIG, "--if-pinned", "go", "rust") == (
            0,
            "install=rust@1.97.1\n",
            "",
        )

    def test_the_same_input_unmarked_is_refused(self) -> None:
        """Without --if-pinned, the same absent pin is refused by name."""
        assert install(self.CONFIG, "go", "rust") == (
            1,
            "",
            (
                "::error::caller-pins: go missing — pin go in this"
                " repository's mise"
                " config; it is a build input of every class that ships a"
                " Rust binary\n"
            ),
        )

    def test_nothing_pinned_emits_an_empty_list(self) -> None:
        """The job's own belt keys still stand beside an empty list."""
        assert install(
            self.CONFIG, "--if-pinned", "go", "--if-pinned", "cargo-pgrx"
        ) == (0, "install=\n", "")

    def test_two_keys_are_refused_even_when_optional(self) -> None:
        """Optional is about presence, not about ambiguity."""
        config = (
            '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
            '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
        )
        assert install(config, "--if-pinned", "cargo-pgrx") == (
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


class TestEnv:
    """The versions alone, for a build that installs in its own container."""

    CONFIG = (
        "[tools]\nrust = [\n"
        '  { version = "1.99.1", components = "clippy" },\n'
        '  { version = "nightly-2026-07-20" },\n]\n'
        '"cargo:cargo-pgrx" = "0.19.3"\n'
        '"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
    )

    def test_each_variable_gets_its_tool_version(self) -> None:
        """The pgrx build's three inputs, from either backend key."""
        assert run(
            self.CONFIG,
            "env",
            "--input-of",
            "the pgrx-extension class",
            "RUST_VERSION=rust",
            "PGRX_VERSION=cargo-pgrx",
            "AUDITABLE_VERSION=cargo-auditable",
        ) == (
            0,
            "RUST_VERSION=1.99.1\nPGRX_VERSION=0.19.3\nAUDITABLE_VERSION=0.7.7\n",
            "",
        )

    def test_a_missing_pin_is_refused_with_the_class(self) -> None:
        """The same refusal, naming the class the input belongs to."""
        assert run(
            '[tools]\nrust = "1.99.1"\n',
            "env",
            "--input-of",
            "the pgrx-extension class",
            "RUST_VERSION=rust",
            "PGRX_VERSION=cargo-pgrx",
        ) == (
            1,
            "",
            (
                "::error::caller-pins: cargo-pgrx missing — pin cargo-pgrx in"
                " this"
                " repository's mise config; it is a build input of the"
                " pgrx-extension class\n"
            ),
        )


class TestUsage:
    """A call that names nothing would install everything: refused."""

    def test_install_with_no_tools_is_a_usage_error(self) -> None:
        """An empty install_args is `mise install`, the whole config."""
        with pytest.raises(SystemExit) as raised, redirect_stderr(io.StringIO()):
            caller_pins.main(["install", "--input-of", INPUT_OF], root=Path())
        assert raised.value.code == 2

    def test_env_with_a_malformed_pair_is_a_usage_error(self) -> None:
        """A pair without `=` names no variable."""
        with pytest.raises(SystemExit) as raised, redirect_stderr(io.StringIO()):
            caller_pins.main(["env", "--input-of", INPUT_OF, "rust"], root=Path())
        assert raised.value.code == 2


PG = '"aqua:theseus-rs/postgresql-binaries"'
PG_LIST = (
    "[tools]\n"
    'rust = "1.97.1"\n'
    f'{PG} = ["18.6.1", "17.11.1", "16.15.1", "15.19.1", "14.24.1"]\n'
)
PG_INPUT_OF = "the gate's pgrx legs"


def by_major(config: str | None, spec: str, *tools: str) -> tuple[int, str, str]:
    """Run the install mode with one `--major postgresql-binaries=SPEC`.

    Returns:
        The exit status, stdout and stderr.

    """
    return run(
        config,
        "install",
        "--input-of",
        PG_INPUT_OF,
        "--major",
        f"postgresql-binaries={spec}",
        *tools,
    )


class TestMajors:
    """A tool pinned at several majors yields the majors a job runs (#1408)."""

    def test_the_legs_major_among_a_list(self) -> None:
        """One leg, one Postgres: the entry that begins with its major."""
        assert by_major(PG_LIST, "16", "rust") == (
            0,
            "install=rust@1.97.1 aqua:theseus-rs/postgresql-binaries@16.15.1\n",
            "",
        )

    def test_a_list_of_majors_yields_each_in_the_order_named(self) -> None:
        """The plan's whole list, as the coverage upload asks for it."""
        assert by_major(PG_LIST, "[14,18]") == (
            0,
            (
                "install=aqua:theseus-rs/postgresql-binaries@14.24.1"
                " aqua:theseus-rs/postgresql-binaries@18.6.1\n"
            ),
            "",
        )

    def test_the_plans_own_list_is_strings(self) -> None:
        """The list as `mise/pgrx-plan.sh` prints it: strings, not numbers.

        Recorded from the coverage upload of release-lab's gate run
        44207730352, which refused `["14","15","16","17","18"]` as a
        usage error.
        """
        assert by_major(PG_LIST, '["14","18"]') == (
            0,
            (
                "install=aqua:theseus-rs/postgresql-binaries@14.24.1"
                " aqua:theseus-rs/postgresql-binaries@18.6.1\n"
            ),
            "",
        )

    def test_the_legs_major_absent_is_refused_by_name(self) -> None:
        """A planned major the pin has no entry for: refused, never newest."""
        assert by_major(PG_LIST, "13", "rust") == (
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

    def test_a_single_valued_pin_at_the_major(self) -> None:
        """One Postgres pinned as a plain string serves its own major."""
        config = f'[tools]\n{PG} = "17.11.1"\n'
        assert by_major(config, "17") == (
            0,
            "install=aqua:theseus-rs/postgresql-binaries@17.11.1\n",
            "",
        )

    def test_a_single_valued_pin_at_another_major_is_refused(self) -> None:
        """...and only its own: the first-entry rule must not stand in."""
        config = f'[tools]\n{PG} = {{ version = "17.11.1" }}\n'
        assert by_major(config, "18") == (
            1,
            "",
            (
                "::error::caller-pins: postgresql-binaries pins no 18.x in"
                " mise.toml (pinned:"
                " 17.11.1); this job runs major 18 — pin it beside the others;"
                " it is a build input of the gate's pgrx legs\n"
            ),
        )

    def test_a_major_pinned_twice_is_refused(self) -> None:
        """Two entries at one major: mise installs both, the job takes either."""
        config = f'[tools]\n{PG} = ["18.6.1", "18.5.0", "17.11.1"]\n'
        assert by_major(config, "18") == (
            1,
            "",
            (
                "::error::caller-pins: postgresql-binaries pins major 18 2"
                " times in mise.toml"
                " (18.6.1, 18.5.0); mise would install each and the job could"
                " take either, so pin one\n"
            ),
        )

    def test_a_major_of_an_unpinned_tool_is_missing(self) -> None:
        """A major asked of a tool the config does not pin at all."""
        assert by_major('[tools]\nrust = "1.97.1"\n', "18", "rust") == (
            1,
            "",
            (
                "::error::caller-pins: postgresql-binaries missing — pin"
                " postgresql-binaries"
                " in this repository's mise config; it is a build input of the"
                " gate's pgrx legs\n"
            ),
        )

    def test_no_major_planned_asks_for_nothing(self) -> None:
        """The plan's `[]`: no extension, so no Postgres pin is needed."""
        assert by_major('[tools]\nrust = "1.97.1"\n', "[]", "rust") == (
            0,
            "install=rust@1.97.1\n",
            "",
        )

    def test_a_malformed_major_is_a_usage_error(self) -> None:
        """Not a whole number or a list of them: refused before reading."""
        for spec in ("pg18", '[18, "pg17"]', "[18.5]", '["-18"]', "true", ""):
            with (
                self.subTest(spec=spec),
                pytest.raises(SystemExit) as exit_,
                redirect_stderr(io.StringIO()),
            ):
                by_major(PG_LIST, spec)
            assert exit_.value.code == 2

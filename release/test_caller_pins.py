#!/usr/bin/env python3
"""Table tests for the reader of a caller's build-input pins (#1333).

One row per branch of the reader: each value form a `[tools]` entry
takes, each backend key a tool can be pinned under, and each refusal,
whose message is asserted whole — a refusal that exits 1 with the wrong
words sends a repository to the wrong remedy.
"""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "caller_pins",
    Path(__file__).with_name("caller-pins.py"),
)
caller_pins = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(caller_pins)

INPUT_OF = "every class that ships a Rust binary"


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


class Versions(unittest.TestCase):
    """Each form a `[tools]` value takes yields the version on PATH."""

    def test_a_single_version(self) -> None:
        """A plain string is the version."""
        self.assertEqual(
            (0, "install=rust@1.97.1\n", ""),
            install('[tools]\nrust = "1.97.1"\n', "rust"),
        )

    def test_a_list_yields_its_first_entry(self) -> None:
        """A second toolchain beside the first: the first is on PATH."""
        config = (
            "[tools]\nrust = [\n"
            '  { version = "1.97.1", components = "clippy,rustfmt,llvm-tools" },\n'
            '  { version = "nightly-2026-07-20", components = "rust-src" },\n'
            '  "1.82.0",\n]\n'
        )
        self.assertEqual((0, "install=rust@1.97.1\n", ""), install(config, "rust"))

    def test_a_list_of_strings_yields_its_first_entry(self) -> None:
        """The same rule for the plain-string list form."""
        config = '[tools]\nrust = ["1.96.0", "1.82.0"]\n'
        self.assertEqual((0, "install=rust@1.96.0\n", ""), install(config, "rust"))

    def test_an_inline_table(self) -> None:
        """An inline table yields its `version`."""
        config = '[tools]\nrust = { version = "1.97.1", components = "llvm-tools" }\n'
        self.assertEqual((0, "install=rust@1.97.1\n", ""), install(config, "rust"))

    def test_the_tools_come_out_in_the_order_named(self) -> None:
        """install_args reads in the job's order, not the config's."""
        config = (
            '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
            'node = "26.13.0"\nrust = "1.97.1"\n'
        )
        self.assertEqual(
            (
                0,
                "install=rust@1.97.1 github:rust-secure-code/cargo-auditable@0.7.7\n",
                "",
            ),
            install(config, "rust", "cargo-auditable"),
        )


class Keys(unittest.TestCase):
    """A tool is found by its name under whichever key pins it."""

    def test_a_tool_under_the_cargo_key(self) -> None:
        """The compile-from-source form, emitted under its own key."""
        config = '[tools]\nrust = "1.97.1"\n"cargo:cargo-pgrx" = "0.19.3"\n'
        self.assertEqual(
            (0, "install=cargo:cargo-pgrx@0.19.3\n", ""),
            install(config, "cargo-pgrx"),
        )

    def test_the_same_tool_under_a_github_key(self) -> None:
        """The prebuilt form (#1334), read without being told the key."""
        config = '[tools]\n"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
        self.assertEqual(
            (0, "install=github:rust-secure-code/cargo-auditable@0.7.7\n", ""),
            install(config, "cargo-auditable"),
        )

    def test_a_key_with_options_keeps_them(self) -> None:
        """The key is the caller's, brackets and all."""
        config = '[tools]\n"ubi:owner/tool[exe=tool]" = "1.0.0"\n'
        self.assertEqual(
            (0, "install=ubi:owner/tool[exe=tool]@1.0.0\n", ""),
            install(config, "tool"),
        )

    def test_the_same_tool_under_two_keys_is_refused(self) -> None:
        """Both would install and one would win: refused, by name."""
        config = (
            '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
            '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
        )
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::cargo-pgrx is pinned under 2 keys in mise.toml"
                    " (cargo:cargo-pgrx, github:pgcentralfoundation/cargo-pgrx);"
                    " mise would install each and put one on PATH, so pin it under"
                    " one key\n"
                ),
            ),
            install(config, "cargo-pgrx"),
        )


class Refusals(unittest.TestCase):
    """A needed input the config does not pin is refused by name."""

    def test_a_missing_pin(self) -> None:
        """The assertion wording the builds already used, no fallback."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::cargo-auditable missing — pin cargo-auditable in this"
                    " repository's mise config; it is a build input of every class"
                    " that ships a Rust binary\n"
                ),
            ),
            install('[tools]\nrust = "1.97.1"\n', "rust", "cargo-auditable"),
        )

    def test_every_missing_pin_is_named(self) -> None:
        """Two missing inputs, two lines: one run names both remedies."""
        code, out, err = install('[tools]\njq = "1.8.2"\n', "rust", "cargo-auditable")
        self.assertEqual((1, ""), (code, out))
        self.assertEqual(
            [
                (
                    "::error::rust missing — pin rust in this repository's mise"
                    " config; it is a build input of every class that ships a Rust"
                    " binary"
                ),
                (
                    "::error::cargo-auditable missing — pin cargo-auditable in this"
                    " repository's mise config; it is a build input of every class"
                    " that ships a Rust binary"
                ),
            ],
            err.splitlines(),
        )

    def test_an_empty_version_is_missing(self) -> None:
        """An empty list or table pins nothing."""
        code, _, err = install("[tools]\nrust = []\n", "rust")
        self.assertEqual(1, code)
        self.assertIn("::error::rust missing — pin rust", err)

    def test_no_config(self) -> None:
        """No config at all: refused, naming what it could not read."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::no mise config in this repository (mise.toml,"
                    " .mise.toml, mise/config.toml), so rust, cargo-auditable cannot"
                    " be read; pin them in this repository's mise config: they are"
                    " build inputs of every class that ships a Rust binary\n"
                ),
            ),
            install(None, "rust", "cargo-auditable"),
        )

    def test_the_other_config_names_are_read(self) -> None:
        """`.mise.toml` and `mise/config.toml` are configs too."""
        for name in (".mise.toml", "mise/config.toml"):
            with self.subTest(name=name):
                self.assertEqual(
                    (0, "install=rust@1.97.1\n", ""),
                    install('[tools]\nrust = "1.97.1"\n', "rust", name=name),
                )


class IfPinned(unittest.TestCase):
    """An input a job needs only where the repository has it."""

    CONFIG = (
        '[tools]\nrust = "1.97.1"\n"go:golang.org/x/vuln/cmd/govulncheck" = "v1.1.4"\n'
    )

    def test_pinned_is_installed(self) -> None:
        """Pinned: emitted under its own key, after the needed tools."""
        self.assertEqual(
            (
                0,
                "install=rust@1.97.1 go:golang.org/x/vuln/cmd/govulncheck@v1.1.4\n",
                "",
            ),
            install(self.CONFIG, "--if-pinned", "govulncheck", "rust"),
        )

    def test_unpinned_is_skipped(self) -> None:
        """Not pinned: nothing emitted and nothing refused."""
        self.assertEqual(
            (0, "install=rust@1.97.1\n", ""),
            install(self.CONFIG, "--if-pinned", "go", "rust"),
        )

    def test_the_same_input_unmarked_is_refused(self) -> None:
        """Without --if-pinned, the same absent pin is refused by name."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::go missing — pin go in this repository's mise"
                    " config; it is a build input of every class that ships a"
                    " Rust binary\n"
                ),
            ),
            install(self.CONFIG, "go", "rust"),
        )

    def test_nothing_pinned_emits_an_empty_list(self) -> None:
        """The job's own belt keys still stand beside an empty list."""
        self.assertEqual(
            (0, "install=\n", ""),
            install(self.CONFIG, "--if-pinned", "go", "--if-pinned", "cargo-pgrx"),
        )

    def test_two_keys_are_refused_even_when_optional(self) -> None:
        """Optional is about presence, not about ambiguity."""
        config = (
            '[tools]\n"cargo:cargo-pgrx" = "0.19.3"\n'
            '"github:pgcentralfoundation/cargo-pgrx" = "0.19.3"\n'
        )
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::cargo-pgrx is pinned under 2 keys in mise.toml"
                    " (cargo:cargo-pgrx, github:pgcentralfoundation/cargo-pgrx);"
                    " mise would install each and put one on PATH, so pin it under"
                    " one key\n"
                ),
            ),
            install(config, "--if-pinned", "cargo-pgrx"),
        )


class Env(unittest.TestCase):
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
        self.assertEqual(
            (
                0,
                "RUST_VERSION=1.99.1\nPGRX_VERSION=0.19.3\nAUDITABLE_VERSION=0.7.7\n",
                "",
            ),
            run(
                self.CONFIG,
                "env",
                "--input-of",
                "the pgrx-extension class",
                "RUST_VERSION=rust",
                "PGRX_VERSION=cargo-pgrx",
                "AUDITABLE_VERSION=cargo-auditable",
            ),
        )

    def test_a_missing_pin_is_refused_with_the_class(self) -> None:
        """The same refusal, naming the class the input belongs to."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::cargo-pgrx missing — pin cargo-pgrx in this"
                    " repository's mise config; it is a build input of the"
                    " pgrx-extension class\n"
                ),
            ),
            run(
                '[tools]\nrust = "1.99.1"\n',
                "env",
                "--input-of",
                "the pgrx-extension class",
                "RUST_VERSION=rust",
                "PGRX_VERSION=cargo-pgrx",
            ),
        )


class Usage(unittest.TestCase):
    """A call that names nothing would install everything: refused."""

    def test_install_with_no_tools_is_a_usage_error(self) -> None:
        """An empty install_args is `mise install`, the whole config."""
        with self.assertRaises(SystemExit) as raised, redirect_stderr(io.StringIO()):
            caller_pins.main(["install", "--input-of", INPUT_OF], root=Path())
        self.assertEqual(2, raised.exception.code)

    def test_env_with_a_malformed_pair_is_a_usage_error(self) -> None:
        """A pair without `=` names no variable."""
        with self.assertRaises(SystemExit) as raised, redirect_stderr(io.StringIO()):
            caller_pins.main(["env", "--input-of", INPUT_OF, "rust"], root=Path())
        self.assertEqual(2, raised.exception.code)


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


class Majors(unittest.TestCase):
    """A tool pinned at several majors yields the majors a job runs (#1408)."""

    def test_the_legs_major_among_a_list(self) -> None:
        """One leg, one Postgres: the entry that begins with its major."""
        self.assertEqual(
            (
                0,
                "install=rust@1.97.1 aqua:theseus-rs/postgresql-binaries@16.15.1\n",
                "",
            ),
            by_major(PG_LIST, "16", "rust"),
        )

    def test_a_list_of_majors_yields_each_in_the_order_named(self) -> None:
        """The plan's whole list, as the coverage upload asks for it."""
        self.assertEqual(
            (
                0,
                (
                    "install=aqua:theseus-rs/postgresql-binaries@14.24.1"
                    " aqua:theseus-rs/postgresql-binaries@18.6.1\n"
                ),
                "",
            ),
            by_major(PG_LIST, "[14,18]"),
        )

    def test_the_legs_major_absent_is_refused_by_name(self) -> None:
        """A planned major the pin has no entry for: refused, never newest."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::postgresql-binaries pins no 13.x in mise.toml (pinned:"
                    " 18.6.1, 17.11.1, 16.15.1, 15.19.1, 14.24.1); this job runs"
                    " major 13 — pin it beside the others; it is a build input of"
                    " the gate's pgrx legs\n"
                ),
            ),
            by_major(PG_LIST, "13", "rust"),
        )

    def test_a_single_valued_pin_at_the_major(self) -> None:
        """One Postgres pinned as a plain string serves its own major."""
        config = f'[tools]\n{PG} = "17.11.1"\n'
        self.assertEqual(
            (0, "install=aqua:theseus-rs/postgresql-binaries@17.11.1\n", ""),
            by_major(config, "17"),
        )

    def test_a_single_valued_pin_at_another_major_is_refused(self) -> None:
        """...and only its own: the first-entry rule must not stand in."""
        config = f'[tools]\n{PG} = {{ version = "17.11.1" }}\n'
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::postgresql-binaries pins no 18.x in mise.toml (pinned:"
                    " 17.11.1); this job runs major 18 — pin it beside the others;"
                    " it is a build input of the gate's pgrx legs\n"
                ),
            ),
            by_major(config, "18"),
        )

    def test_a_major_pinned_twice_is_refused(self) -> None:
        """Two entries at one major: mise installs both, the job takes either."""
        config = f'[tools]\n{PG} = ["18.6.1", "18.5.0", "17.11.1"]\n'
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::postgresql-binaries pins major 18 2 times in mise.toml"
                    " (18.6.1, 18.5.0); mise would install each and the job could"
                    " take either, so pin one\n"
                ),
            ),
            by_major(config, "18"),
        )

    def test_a_major_of_an_unpinned_tool_is_missing(self) -> None:
        """A major asked of a tool the config does not pin at all."""
        self.assertEqual(
            (
                1,
                "",
                (
                    "::error::postgresql-binaries missing — pin postgresql-binaries"
                    " in this repository's mise config; it is a build input of the"
                    " gate's pgrx legs\n"
                ),
            ),
            by_major('[tools]\nrust = "1.97.1"\n', "18", "rust"),
        )

    def test_no_major_planned_asks_for_nothing(self) -> None:
        """The plan's `[]`: no extension, so no Postgres pin is needed."""
        self.assertEqual(
            (0, "install=rust@1.97.1\n", ""),
            by_major('[tools]\nrust = "1.97.1"\n', "[]", "rust"),
        )

    def test_a_malformed_major_is_a_usage_error(self) -> None:
        """Not a whole number or a list of them: refused before reading."""
        for spec in ("pg18", '[18, "17"]', "true", ""):
            with (
                self.subTest(spec=spec),
                self.assertRaises(SystemExit) as exit_,
                redirect_stderr(io.StringIO()),
            ):
                by_major(PG_LIST, spec)
            self.assertEqual(2, exit_.exception.code)


if __name__ == "__main__":
    unittest.main()

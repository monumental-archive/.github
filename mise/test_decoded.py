#!/usr/bin/env python3
"""decoded.py's refusals and readers, and the law that it alone parses (#1676).

The rows: each refusal of `load_json` and `load_toml` by its message
(a duplicate key, `NaN`, `Infinity`, overflow, depth, bytes that are
not UTF-8, a syntax error), and each reader narrowing or refusing by
`where`.

The law: no tracked `.py` parses JSON or TOML outside `mise/decoded.py`.
It is read from the syntax tree with import aliases resolved, so
`import json as j`, `from json import loads` and
`from tomllib import load as l` are each the same parse; `json.dumps`
writes and is free. MOVING lists the files not yet moved, one per line,
and may only shrink: a listed file with no parse is red until it is
dropped, and an unlisted file with one is red by file and line. The
conformance issues filed from #1604 empty it; the one that empties it
deletes it (#1544's shape: it holds files, never findings).

stdlib `unittest`, matching the other mise/test_*.py; run through the
gate as `mise run test`.
"""

import ast
import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import unittest
from collections.abc import Callable
from pathlib import Path

import decoded
import hermetic

HERE = Path(__file__).resolve().parent
CANON = HERE.parent
GIT = shutil.which("git") or "git"
DEEP = 100_000
# A raising reader: a decoded value and where it came from.
Reader = Callable[[object, str], object]

# The parses the law refuses outside decoded.py, fully qualified.
PARSES = frozenset({
    "json.load",
    "json.loads",
    "json.JSONDecoder",
    "json.decoder.JSONDecoder",
    "tomllib.load",
    "tomllib.loads",
})

# The one file that parses.
LOADER = "mise/decoded.py"

# Files that still parse a document themselves. Only shrinks.
MOVING = frozenset({
    ".github/rekor-monitor/findings.py",
    ".github/rekor-monitor/standing_issue.py",
    ".github/rekor-monitor/test_findings.py",
    ".github/rekor-monitor/test_standing_issue.py",
    ".github/rekor-monitor/test_verdict.py",
    ".github/rekor-monitor/verdict.py",
    "mise/adoption_population.py",
    "mise/belt_shell.py",
    "mise/bestpractices_drift.py",
    "mise/bestpractices_owner.py",
    "mise/biome_config.py",
    "mise/coverage_js_runner.py",
    "mise/deny_skips.py",
    "mise/embedded_shell.py",
    "mise/gate_surface.py",
    "mise/gate_tools.py",
    "mise/licence_texts.py",
    "mise/lock_arch.py",
    "mise/lock_platforms.py",
    "mise/msrv_plan.py",
    "mise/mutants_go.py",
    "mise/mutants_rust.py",
    "mise/mutants_typescript.py",
    "mise/pgrx_postgres.py",
    "mise/plan_producers.py",
    "mise/project_licence.py",
    "mise/prop_coverage_js_runner.py",
    "mise/prop_deny_skips.py",
    "mise/prop_security_config.py",
    "mise/security_config.py",
    "mise/subject_budget.py",
    "mise/test_action_lints.py",
    "mise/test_audit_scheduled.py",
    "mise/test_belt_shell.py",
    "mise/test_bestpractices_drift.py",
    "mise/test_bestpractices_owner.py",
    "mise/test_bestpractices_stub.py",
    "mise/test_biome_config.py",
    "mise/test_codecov_yml.py",
    "mise/test_coverage_branches.py",
    "mise/test_coverage_exclusions.py",
    "mise/test_coverage_go_branches.py",
    "mise/test_coverage_js_runner.py",
    "mise/test_coverage_tasks.py",
    "mise/test_dco.py",
    "mise/test_deny_skips.py",
    "mise/test_derive_badges.py",
    "mise/test_embedded_shell.py",
    "mise/test_first_line.py",
    "mise/test_fuzz_build.py",
    "mise/test_gate_stub.py",
    "mise/test_gate_tools.py",
    "mise/test_git_decoy.py",
    "mise/test_go_fuzz_seeds.py",
    "mise/test_hook_jobs.py",
    "mise/test_lock_platforms.py",
    "mise/test_mutants_go.py",
    "mise/test_pg_schema.py",
    "mise/test_plan_producers.py",
    "mise/test_plpgsql_coverage.py",
    "mise/test_python_module_names.py",
    "mise/test_python_properties.py",
    "mise/test_python_types.py",
    "mise/test_release_phases.py",
    "mise/test_release_planned.py",
    "mise/test_rust_lint.py",
    "mise/test_security_config.py",
    "mise/test_signer_pin.py",
    "mise/test_subject_budget.py",
    "mise/test_task_contract.py",
    "mise/test_task_python.py",
    "mise/test_task_shell.py",
    "mise/test_task_walk.py",
    "mise/test_trivy_scope.py",
    "mise/test_tsconfig_dials.py",
    "mise/test_typescript_properties.py",
    "mise/test_unittest_guarded.py",
    "mise/test_vex_one_per_merge.py",
    "mise/test_zizmor_signer.py",
    "mise/tsconfig_dials.py",
    "mise/typescript_properties.py",
    "mise/workspace_inherit.py",
    "mise/zizmor_signer.py",
    "release/attach_publish.py",
    "release/caller_pins.py",
    "release/matrix.py",
    "release/mint_doi.py",
    "release/pack_wasm.py",
    "release/pgrx/inventory_plan.py",
    "release/pgrx/prop_inventory_plan.py",
    "release/pgrx_images.py",
    "release/prop_matrix.py",
    "release/prop_mint_doi.py",
    "release/publish_crates.py",
    "release/registry.py",
    "release/repro_resolve.py",
    "release/test_build_rust_crate.py",
    "release/test_generate_pgrx_upgrade.py",
    "release/test_job_installs.py",
    "release/test_mint_doi.py",
    "release/test_pgrx_images.py",
    "release/test_prepare_release.py",
    "release/test_publish_rust_crate.py",
    "release/test_registry.py",
    "release/test_release_guards.py",
    "release/test_security_release.py",
    "settings/test_repo_baseline.py",
})


def dotted(node: ast.expr) -> str | None:
    """Spell an attribute chain rooted at a name.

    Returns:
        `a.b.c`, or None when the chain is not rooted at a name.

    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def imported(tree: ast.Module) -> tuple[dict[str, str], list[int]]:
    """Resolve what each imported name stands for.

    Returns:
        Each local name mapped to the qualified name it binds, and the
        line of each import that binds a parse directly.

    """
    names: dict[str, str] = {}
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                names[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                qualified = f"{node.module}.{alias.name}"
                if qualified in PARSES:
                    found.append(node.lineno)
                else:
                    names[alias.asname or alias.name] = qualified
    return names, found


def parses(text: str) -> list[int]:
    """Find the lines that parse a document, aliases resolved.

    Returns:
        The line of each import or use of a parse, in order.

    """
    tree = ast.parse(text)
    names, found = imported(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = dotted(node)
        if chain is None:
            continue
        first, _, rest = chain.partition(".")
        if first in names and f"{names[first]}.{rest}" in PARSES:
            found.append(node.lineno)
    return sorted(found)


def judge(root: Path) -> list[str]:
    """Hold the tracked Python under root to the law and its list.

    Returns:
        One line per breach: an unlisted parse by file and line, or a
        listed file that no longer parses.

    """
    # ruff: ignore[subprocess-without-shell-equals-true]
    listed = subprocess.run(
        [GIT, "-C", str(root), "ls-files", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
        env=hermetic.env(),
    ).stdout.split()
    out: list[str] = []
    for path in listed:
        if path == LOADER:
            continue
        lines = parses((root / path).read_text(encoding="utf-8"))
        if path in MOVING and not lines:
            out.append(f"{path}: moved: drop it from the list")
        elif path not in MOVING:
            out.extend(f"{path}:{line}: parses outside {LOADER}" for line in lines)
    out.extend(
        f"{path}: moved: drop it from the list" for path in sorted(MOVING - set(listed))
    )
    return out


class LoadJson(unittest.TestCase):
    """Strict JSON: every input is a value or one named refusal."""

    def refused(self, source: str | bytes) -> str:
        """Load source and return the refusal's text.

        Returns:
            The message of the UndecodableError.

        """
        with self.assertRaises(decoded.UndecodableError) as caught:
            decoded.load_json(source)
        return str(caught.exception)

    def test_a_document_decodes(self) -> None:
        """Strict JSON in, the same value out, from text or bytes."""
        text = '{"a": [1, 2.5, "x", true, null], "b": {}}'
        want = {"a": [1, 2.5, "x", True, None], "b": {}}
        self.assertEqual(want, decoded.load_json(text))
        self.assertEqual(want, decoded.load_json(text.encode()))

    def test_a_duplicate_key_is_refused(self) -> None:
        """I-JSON's unique keys (RFC 7493), at any depth."""
        self.assertEqual('duplicate key "a"', self.refused('{"a": 1, "a": 2}'))
        self.assertEqual('duplicate key "b"', self.refused('[{"b": 1, "b": 1}]'))

    def test_nan_and_the_infinities_are_refused(self) -> None:
        """JSON has no NaN, Infinity or -Infinity."""
        for name in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(name=name):
                self.assertEqual(
                    f"{name} is not a JSON number", self.refused(f"[{name}]")
                )

    def test_a_number_past_a_finite_float_is_refused(self) -> None:
        """1e999 would decode to inf; it is refused instead."""
        self.assertEqual("1e999 does not fit a finite number", self.refused("[1e999]"))

    def test_nesting_too_deep_is_refused(self) -> None:
        """A depth the decoder cannot reach is a refusal, not a crash."""
        self.assertEqual("nested too deep to decode", self.refused("[" * DEEP))

    def test_bytes_that_are_not_utf8_are_refused(self) -> None:
        """Only UTF-8; json's own UTF-16 and UTF-32 detection is not used."""
        self.assertEqual("not UTF-8: byte 1 cannot be decoded", self.refused(b"[\xff]"))
        # UTF-16 with its byte-order mark, which json.loads would accept.
        self.assertEqual(
            "not UTF-8: byte 0 cannot be decoded",
            self.refused("[1]".encode("utf-16")),
        )

    def test_a_syntax_error_keeps_json_s_message(self) -> None:
        """The decoder's own text, which says where."""
        self.assertEqual("Expecting value: line 1 column 2 (char 1)", self.refused("["))


class LoadToml(unittest.TestCase):
    """TOML: a table or one named refusal; TOML's nan and inf stay."""

    def refused(self, source: str | bytes) -> str:
        """Load source and return the refusal's text.

        Returns:
            The message of the UndecodableError.

        """
        with self.assertRaises(decoded.UndecodableError) as caught:
            decoded.load_toml(source)
        return str(caught.exception)

    def test_a_document_decodes(self) -> None:
        """A table in, the same table out; inf is TOML's own."""
        table = decoded.load_toml(b'a = 1\n[b]\nc = "x"\nd = inf\n')
        want: dict[str, object] = {"a": 1, "b": {"c": "x", "d": float("inf")}}
        self.assertEqual(want, table)

    def test_a_toml_error_keeps_tomllib_s_message(self) -> None:
        """The decoder's own text, which says where."""
        self.assertEqual("Invalid value (at end of document)", self.refused("a ="))

    def test_bytes_that_are_not_utf8_are_refused(self) -> None:
        """TOML is UTF-8."""
        self.assertEqual(
            "not UTF-8: byte 5 cannot be decoded", self.refused(b'a = "\xff"')
        )

    def test_nesting_too_deep_is_refused(self) -> None:
        """A depth the decoder cannot reach is a refusal, not a crash."""
        depth = 10_000
        source = "a = " + "[" * depth + "]" * depth
        self.assertEqual("nested too deep to decode", self.refused(source))


class Readers(unittest.TestCase):
    """Each reader narrows a decoded value or refuses it by `where`."""

    def test_the_predicates(self) -> None:
        """is_object, is_array and is_strings, over every JSON kind."""
        values: list[object] = [{"a": 1}, {1: 1}, [1], ["a"], "a", 1, None]
        objects: list[bool] = [decoded.is_object(v) for v in values]
        arrays: list[bool] = [decoded.is_array(v) for v in values]
        strings: list[bool] = [decoded.is_strings(v) for v in values]
        no, yes = False, True
        want_objects: list[bool] = [yes, no, no, no, no, no, no]
        want_arrays: list[bool] = [no, no, yes, yes, no, no, no]
        want_strings: list[bool] = [no, no, no, yes, no, no, no]
        self.assertEqual(want_objects, objects)
        self.assertEqual(want_arrays, arrays)
        self.assertEqual(want_strings, strings)

    def test_each_raising_reader_names_where(self) -> None:
        """A wrong kind is refused with `where` and the kind it is not."""
        empty: list[object] = []
        rows: list[tuple[str, Reader, object, str]] = [
            ("as_object", decoded.as_object, empty, "the doc is not an object"),
            ("as_object", decoded.as_object, {1: 1}, "the doc is not an object"),
            ("as_array", decoded.as_array, {"a": 1}, "the doc is not a list"),
            ("as_str", decoded.as_str, 1, "the doc is not a string"),
            ("as_int", decoded.as_int, "1", "the doc is not an integer"),
            ("as_int", decoded.as_int, True, "the doc is not an integer"),
        ]
        for name, reader, value, message in rows:
            with self.subTest(reader=name, value=value):
                with self.assertRaises(decoded.UndecodableError) as caught:
                    reader(value, "the doc")
                self.assertEqual(message, str(caught.exception))

    def test_each_raising_reader_returns_its_kind(self) -> None:
        """The right kind comes back as it went in."""
        table: dict[str, object] = {"a": 1}
        items: list[object] = [1]
        self.assertEqual(table, decoded.as_object(table, "w"))
        self.assertEqual(items, decoded.as_array(items, "w"))
        self.assertEqual("a", decoded.as_str("a", "w"))
        self.assertEqual(3, decoded.as_int(3, "w"))


class TheLaw(unittest.TestCase):
    """Only decoded.py parses; the list of files not yet moved only shrinks."""

    def test_the_tree_holds_the_law(self) -> None:
        """No unlisted parse, and no listed file that has moved."""
        none: list[str] = []
        self.assertEqual(none, judge(CANON))

    def test_the_list_holds_files_that_exist(self) -> None:
        """MOVING names tracked paths, never a finding or a pattern."""
        self.assertTrue(all(p.endswith(".py") for p in MOVING))

    def test_each_spelling_is_a_parse(self) -> None:
        """Every import spelling of a parse is found on its own line."""
        text = (
            "import json\n"
            "import json as j\n"
            "import tomllib as t\n"
            "from json import loads\n"
            "from tomllib import load as l\n"
            "from json import JSONDecoder\n"
            "import json.decoder\n"
            "a = json.loads('1')\n"
            "b = j.load(f)\n"
            "c = t.loads('')\n"
            "d = json.decoder.JSONDecoder()\n"
            "e = json.JSONDecoder\n"
        )
        want: list[int] = [4, 5, 6, 8, 9, 10, 11, 12]
        self.assertEqual(want, parses(text))

    def test_writing_is_not_parsing(self) -> None:
        """json.dumps, and a parse named only in a string, are free."""
        text = "import json\nx = json.dumps({})\ny = 'json.loads(s)'\n"
        none: list[int] = []
        self.assertEqual(none, parses(text))

    def test_the_loader_is_the_one_exception(self) -> None:
        """decoded.py parses, and it is the file the law excepts."""
        self.assertTrue(parses((CANON / LOADER).read_text(encoding="utf-8")))

    def test_a_document_round_trips_through_the_loader(self) -> None:
        """What the canon writes with json.dumps, decoded.py reads back."""
        value: object = {"a": [1, 2.5, "x", None], "b": {"c": True}}
        self.assertEqual(value, decoded.load_json(json.dumps(value)))


if __name__ == "__main__":
    unittest.main()

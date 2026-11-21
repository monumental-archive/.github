#!/usr/bin/env python3
"""Table tests for the preset's multi-version mise managers (#1773).

Renovate's mise manager updates only the FIRST entry of a tool pinned
as an array (its readme, "Renovate only updates primary versions"), so
default.json carries a regex manager per multi-version tool that claims
every entry AFTER the first: the native manager keeps the first, and the
`mise lock` artifact step with it. These rows read each pattern out of
default.json, never a copy, and run it over mise.toml fixtures the way
Renovate's `recursive` strategy does: every match of one pattern is the
content the next one runs over, and the named groups of every level
combine into the dependency.

Renovate compiles matchStrings with RE2; Python's `re` is not RE2. Two
consequences, both handled here rather than assumed. A named group is
`(?<name>…)` in the preset and `(?P<name>…)` to Python, so the
patterns are translated by subject_budget's own JS_GROUP (the one place
the belt already does that) and by nothing else. And a construct RE2
lacks — lookaround, atomic groups, backreferences — would pass here and
match nothing for Renovate, so every pattern is refused by name if it
carries one.

The fixtures are the shapes as tracked: edtf's Postgres array and ICU
table on its main (2026-11-20), and #1774's two-table ICU array.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import re
import unittest
from pathlib import Path

import decoded
from subject_budget import JS_GROUP

PRESET = Path(__file__).resolve().parent.parent / "default.json"
MISE_FILES = "/(^|/)mise\\.toml$/"
POSTGRES = "theseus-rs/postgresql-binaries"
ICU = "unicode-org/icu"
# Renovate trims a GitHub sourceUrl to owner/repo, so this is the form
# it compares, whatever crates.io carries (#1867).
PGRX = "https://github.com/pgcentralfoundation/pgrx"

# What RE2 refuses that Python accepts: lookahead and lookbehind, atomic
# groups, numbered and named backreferences.
NOT_RE2 = re.compile(r"\(\?<?[=!]|\(\?>|\\[1-9]|\\k<|\(\?P=")

POSTGRES_FIVE = """\
[tools]
rust = ["1.95.0", "1.94.1"]
"aqua:theseus-rs/postgresql-binaries" = [
  "18.7.0",
  "17.11.1",
  "16.15.1",
  "15.19.1",
  "14.24.1",
]
node = "26.13.1"
"""
POSTGRES_ONE = """\
[tools]
rust = ["1.95.0", "1.94.1"]
"aqua:theseus-rs/postgresql-binaries" = "18.7.0"
"""
# The keys after `version`, as tracked; joined below so each fixture
# line is the tracked line, byte for byte, within the line limit here.
ICU_KEYS = 'version_prefix = "release-", asset_pattern = "icu4c-*-sources.tgz"'
ICU_TWO = (
    "[tools]\n"
    '"github:unicode-org/icu" = [\n'
    f'  {{ version = "78.3", {ICU_KEYS}, os = ["macos"] }},\n'
    f'  {{ version = "79.1", {ICU_KEYS}, os = ["macos"] }},\n'
    "]\n"
    '"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
)
ICU_ONE = (
    "[tools]\n"
    f'"github:unicode-org/icu" = {{ version = "78.3", {ICU_KEYS}, os = [\n'
    '  "macos",\n'
    "] }\n"
    '"github:rust-secure-code/cargo-auditable" = "0.7.7"\n'
)


def preset() -> dict[str, object]:
    """Read default.json through the canon's one decoder (#1676).

    Returns:
        The parsed preset.

    """
    return decoded.as_object(decoded.load_json(PRESET.read_bytes()), "default.json")


def manager(dep: str) -> dict[str, object]:
    """Find the regex manager over mise.toml that names `dep`.

    Returns:
        The manager object, as written.

    Raises:
        AssertionError: when the preset carries no such manager.

    """
    managers = decoded.as_array(preset().get("customManagers", []), "customManagers")
    for i, raw in enumerate(managers):
        m = decoded.as_object(raw, f"customManagers[{i}]")
        files = decoded.as_array(m.get("managerFilePatterns", []), "patterns")
        if MISE_FILES in files and m.get("depNameTemplate") == dep:
            return m
    msg = f"default.json: no regex manager over mise.toml names {dep}"
    raise AssertionError(msg)


def compiled(pattern: str) -> re.Pattern[str]:
    """Translate one matchString's named groups and compile it.

    Returns:
        The pattern, as Python's `re` reads it.

    Raises:
        AssertionError: when the pattern uses what RE2 lacks.

    """
    found = NOT_RE2.search(pattern)
    if found:
        msg = f"not RE2, Renovate cannot run it: {found.group(0)!r} in {pattern!r}"
        raise AssertionError(msg)
    return re.compile(JS_GROUP.sub("(?P<", pattern))


def recursive(
    patterns: list[str], content: str, groups: dict[str, str]
) -> list[dict[str, str]]:
    """Run matchStrings the way Renovate's `recursive` strategy does.

    Returns:
        One combined group dict per match of the last pattern.

    """
    if not patterns:
        return [groups]
    head, rest = compiled(patterns[0]), patterns[1:]
    out: list[dict[str, str]] = []
    for match in head.finditer(content):
        taken = {k: v for k, v in match.groupdict().items() if v is not None}
        out.extend(recursive(rest, match.group(0), {**groups, **taken}))
    return out


# A manager that takes nothing, as a typed list.
NONE: list[str] = []


def versions(dep: str, content: str) -> list[str]:
    """Extract what the manager naming `dep` would look up in `content`.

    Returns:
        Each currentValue, in file order.

    """
    m = manager(dep)
    raw = decoded.as_array(m["matchStrings"], "matchStrings")
    patterns = [decoded.as_str(p, "matchStrings[]") for p in raw]
    return [g["currentValue"] for g in recursive(patterns, content, {})]


class Managers(unittest.TestCase):
    """Each manager takes every entry after the first, and only those."""

    def test_postgres_five_entries_yield_the_last_four(self) -> None:
        """The native manager has 18.7.0; the regex one the other four."""
        last_four: list[str] = ["17.11.1", "16.15.1", "15.19.1", "14.24.1"]
        self.assertEqual(versions(POSTGRES, POSTGRES_FIVE), last_four)

    def test_postgres_one_string_yields_none(self) -> None:
        """A one-version pin is the native manager's alone (rust's array too)."""
        self.assertEqual(versions(POSTGRES, POSTGRES_ONE), NONE)

    def test_icu_two_tables_yield_the_second(self) -> None:
        """The native manager has 78.3; the regex one 79.1."""
        regex_only: list[str] = ["79.1"]
        self.assertEqual(versions(ICU, ICU_TWO), regex_only)

    def test_icu_one_table_yields_none(self) -> None:
        """A one-table pin, as edtf tracks it, is the native manager's alone."""
        self.assertEqual(versions(ICU, ICU_ONE), NONE)

    def test_shape(self) -> None:
        """Recursive, github-releases, and the file pattern hits mise.toml."""
        for dep in (POSTGRES, ICU):
            with self.subTest(dep=dep):
                m = manager(dep)
                self.assertEqual(m["customType"], "regex")
                self.assertEqual(m["matchStringsStrategy"], "recursive")
                self.assertEqual(m["datasourceTemplate"], "github-releases")
                files = MISE_FILES[1:-1]
                self.assertRegex("mise.toml", files)
                self.assertRegex("crates/x/mise.toml", files)
                self.assertNotRegex("mise/config.toml", files)

    def test_icu_tag_to_version(self) -> None:
        """ICU tags `release-79.1`; the lookup compares the bare version."""
        template = compiled(
            decoded.as_str(manager(ICU)["extractVersionTemplate"], "template")
        )
        found = template.search("release-79.1")
        self.assertIsNotNone(found)
        self.assertEqual(found.group("version") if found else None, "79.1")
        self.assertIsNone(template.search("icu4x/2026-08-31/79.x"))

    def test_not_re2_is_refused(self) -> None:
        """The RE2 guard itself reds on a lookbehind, by name."""
        with self.assertRaisesRegex(AssertionError, r"not RE2.*\(\?<="):
            compiled('(?<=,)\\s*"(?<currentValue>[0-9.]+)"')


class Rules(unittest.TestCase):
    """Postgres moves as one set, each entry inside its own major."""

    @staticmethod
    def rules(name: str) -> list[dict[str, object]]:
        """Every packageRule that names `name` in matchPackageNames.

        Returns:
            The rules, in preset order.

        """
        raw = decoded.as_array(preset()["packageRules"], "packageRules")
        rules = [decoded.as_object(r, "packageRules[]") for r in raw]
        return [
            r
            for r in rules
            if name in decoded.as_array(r.get("matchPackageNames", []), "names")
        ]

    def test_postgres_grouped(self) -> None:
        """Every entry's update lands in one pull request."""
        for name in (POSTGRES, f"aqua:{POSTGRES}"):
            with self.subTest(name=name):
                groups = [r["groupName"] for r in self.rules(name) if "groupName" in r]
                group: list[str] = ["postgresql-binaries"]
                self.assertEqual(groups, group)

    def test_postgres_major_disabled(self) -> None:
        """A new major is a new entry, added by hand with its crate feature."""
        for name in (POSTGRES, f"aqua:{POSTGRES}"):
            with self.subTest(name=name):
                off = [
                    r
                    for r in self.rules(name)
                    if r.get("matchUpdateTypes") == ["major"]
                    and r.get("enabled") is False
                ]
                self.assertEqual(len(off), 1)


def pgrx_groups(rules: list[object]) -> list[str]:
    """Collect the groupName of every rule keyed on pgrx's source.

    Returns:
        One entry per matching rule, in preset order.

    Raises:
        AssertionError: when no rule matches the source.

    """
    groups: list[str] = []
    for raw in rules:
        r = decoded.as_object(raw, "packageRules[]")
        urls = decoded.as_array(r.get("matchSourceUrls", []), "matchSourceUrls")
        if PGRX in urls:
            groups.append(decoded.as_str(r.get("groupName", ""), "groupName"))
    if not groups:
        msg = f"default.json: no packageRule matches the source {PGRX}"
        raise AssertionError(msg)
    return groups


class Pgrx(unittest.TestCase):
    """pgrx's packages release together, so they move as one set (#1867)."""

    def test_pgrx_grouped_by_source(self) -> None:
        """Exactly one rule matches pgrx's source, and it groups as pgrx."""
        raw = decoded.as_array(preset()["packageRules"], "packageRules")
        group: list[str] = ["pgrx"]
        self.assertEqual(pgrx_groups(raw), group)

    def test_pgrx_rule_removed_is_refused(self) -> None:
        """Planted: the preset without the rule reds, by name."""
        raw = decoded.as_array(preset()["packageRules"], "packageRules")
        kept = [
            r
            for r in raw
            if PGRX
            not in decoded.as_array(
                decoded.as_object(r, "packageRules[]").get("matchSourceUrls", []),
                "matchSourceUrls",
            )
        ]
        self.assertEqual(len(kept), len(raw) - 1)
        with self.assertRaisesRegex(
            AssertionError, r"no packageRule matches the source .*/pgrx$"
        ):
            pgrx_groups(kept)


if __name__ == "__main__":
    unittest.main()

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

The age rows (#2097) hold where the release-age floor sits and what it
says. Renovate applies an extended preset's packageRules before this
file's own, so a top-level age loses to any of them; the floor is this
file's FIRST rule, the npm rule follows it, and both agree with the
belt's mise and pnpm numbers, read from mise/config.toml.

The limit rows (#2162) hold that no concurrency or hourly limit
applies to the deliveries the org depends on: lock file maintenance
and both first-party rules, repositories and ghcr images, carry
prConcurrentLimit 0 and prHourlyLimit 0, because a shared limit
skipped them. The first-party age rows hold that both rules wait
zero days: Renovate names an image with its registry, so the
repositories' glob never reached one, and every first-party image
bump sat seven days under Pending Status Checks.
"""

import re

import pytest

from belt import decoded, typed_std
from belt.subject_budget import JS_GROUP
from tests import support

PRESET = support.CANON / "default.json"
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


# Each manager takes every entry after the first, and only those.
def test_postgres_five_entries_yield_the_last_four() -> None:
    """The native manager has 18.7.0; the regex one the other four."""
    last_four: list[str] = ["17.11.1", "16.15.1", "15.19.1", "14.24.1"]
    assert versions(POSTGRES, POSTGRES_FIVE) == last_four


def test_postgres_one_string_yields_none() -> None:
    """A one-version pin is the native manager's alone (rust's array too)."""
    assert versions(POSTGRES, POSTGRES_ONE) == NONE


def test_icu_two_tables_yield_the_second() -> None:
    """The native manager has 78.3; the regex one 79.1."""
    regex_only: list[str] = ["79.1"]
    assert versions(ICU, ICU_TWO) == regex_only


def test_icu_one_table_yields_none() -> None:
    """A one-table pin, as edtf tracks it, is the native manager's alone."""
    assert versions(ICU, ICU_ONE) == NONE


@pytest.mark.parametrize("dep", [POSTGRES, ICU])
def test_shape(dep: str) -> None:
    """Recursive, github-releases, and the file pattern hits mise.toml."""
    m = manager(dep)
    assert m["customType"] == "regex"
    assert m["matchStringsStrategy"] == "recursive"
    assert m["datasourceTemplate"] == "github-releases"
    files = MISE_FILES[1:-1]
    assert re.search(files, "mise.toml")
    assert re.search(files, "crates/x/mise.toml")
    assert not re.search(files, "mise/config.toml")


def test_icu_tag_to_version() -> None:
    """ICU tags `release-79.1`; the lookup compares the bare version."""
    template = compiled(
        decoded.as_str(manager(ICU)["extractVersionTemplate"], "template")
    )
    found = template.search("release-79.1")
    assert found is not None
    assert typed_std.group(found, "version") == "79.1"
    assert template.search("icu4x/2026-08-31/79.x") is None


def test_not_re2_is_refused() -> None:
    """The RE2 guard itself reds on a lookbehind, by name."""
    with pytest.raises(AssertionError, match=r"not RE2.*\(\?<="):
        compiled('(?<=,)\\s*"(?<currentValue>[0-9.]+)"')


# Postgres moves as one set, each entry inside its own major.
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


@pytest.mark.parametrize("name", [POSTGRES, f"aqua:{POSTGRES}"])
def test_postgres_grouped(name: str) -> None:
    """Every entry's update lands in one pull request."""
    groups = [r["groupName"] for r in rules(name) if "groupName" in r]
    group: list[str] = ["postgresql-binaries"]
    assert groups == group


@pytest.mark.parametrize("name", [POSTGRES, f"aqua:{POSTGRES}"])
def test_postgres_major_disabled(name: str) -> None:
    """A new major is a new entry, added by hand with its crate feature."""
    off = [
        r
        for r in rules(name)
        if r.get("matchUpdateTypes") == ["major"] and r.get("enabled") is False
    ]
    assert len(off) == 1


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


# pgrx's packages release together, so they move as one set (#1867).
def test_pgrx_grouped_by_source() -> None:
    """Exactly one rule matches pgrx's source, and it groups as pgrx."""
    raw = decoded.as_array(preset()["packageRules"], "packageRules")
    group: list[str] = ["pgrx"]
    assert pgrx_groups(raw) == group


def test_pgrx_rule_removed_is_refused() -> None:
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
    assert len(kept) == len(raw) - 1
    with pytest.raises(
        AssertionError, match=r"no packageRule matches the source .*/pgrx$"
    ):
        pgrx_groups(kept)


BELT = support.MISE / "config.toml"
AGE = re.compile(r"^([0-9]+) ?(h|days)$")


def hours(age: str, where: str) -> int:
    """Read a Renovate (`7 days`) or mise (`168h`) age as hours.

    Returns:
        The age in hours.

    Raises:
        AssertionError: the age has neither shape.

    """
    m = AGE.fullmatch(age)
    if m is None:
        msg = f"{where}: {age!r} is not an age in hours or days"
        raise AssertionError(msg)
    count = typed_std.group(m, 1) or "0"
    return int(count) * (24 if typed_std.group(m, 2) == "days" else 1)


def age_findings(doc: dict[str, object], belt: dict[str, object]) -> list[str]:
    """Every way the preset's ages miss the shape #2097 decided.

    Returns:
        One message per miss; empty when the preset holds.

    """
    found: list[str] = []
    if "minimumReleaseAge" in doc:
        found.append(
            "default.json: a top-level minimumReleaseAge loses to every"
            " extended preset's packageRules; state it as the first rule"
        )
    raw = decoded.as_array(doc.get("packageRules", []), "packageRules")
    rules = [decoded.as_object(r, "packageRules[]") for r in raw]
    settings = decoded.as_object(belt["settings"], "[settings]")
    env = decoded.as_object(belt["env"], "[env]")
    floor = rules[0] if rules else {}
    matchers = sorted(k for k in floor if k.startswith(("match", "exclude")))
    if (
        matchers != ["matchPackageNames"]
        or floor["matchPackageNames"] != ["*"]
        or "minimumReleaseAge" not in floor
    ):
        found.append("default.json: packageRules[0] is not the age floor over *")
    else:
        age = decoded.as_str(floor.get("minimumReleaseAge", ""), "floor")
        mise = decoded.as_str(settings["minimum_release_age"], "settings")
        if hours(age, "packageRules[0]") != hours(mise, "minimum_release_age"):
            found.append(
                f"default.json: the floor says {age}, the belt's"
                f" minimum_release_age {mise}"
            )
    npm = [
        i
        for i, r in enumerate(rules)
        if r.get("matchDatasources") == ["npm"] and "minimumReleaseAge" in r
    ]
    if npm != [1]:
        found.append(
            f"default.json: the npm age rule sits at {npm}, not right after the floor"
        )
    else:
        age = decoded.as_str(rules[1]["minimumReleaseAge"], "npm rule")
        pnpm = decoded.as_str(
            env["pnpm_config_minimum_release_age"], "pnpm_config_minimum_release_age"
        )
        if hours(age, "npm rule") * 60 != int(pnpm):
            found.append(
                f"default.json: npm waits {age}, the belt's pnpm {pnpm} minutes"
            )
    return found


def belt() -> dict[str, object]:
    """Decode the belt's mise/config.toml.

    Returns:
        Its top-level table.

    """
    return decoded.load_toml(BELT.read_bytes())


# The age floor outranks every inherited rule (#2097).
def test_preset_holds() -> None:
    """The tracked preset and belt agree, floor first."""
    none: list[str] = []
    assert age_findings(preset(), belt()) == none


def test_top_level_age_is_refused() -> None:
    """Planted: the floor back at top level reds, by name."""
    doc = {**preset(), "minimumReleaseAge": "7 days"}
    found = age_findings(doc, belt())
    assert len(found) == 1
    assert r"top-level minimumReleaseAge loses" in found[0]


def test_floor_not_first_is_refused() -> None:
    """Planted: an exemption ahead of the floor reds."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    doc = {**doc, "packageRules": [raw[-1], *raw]}
    found = age_findings(doc, belt())
    assert re.search(r"packageRules\[0\] is not the age floor", found[0])


def test_floor_off_the_belt_is_refused() -> None:
    """Planted: a floor that disagrees with mise reds."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    first = {**decoded.as_object(raw[0], "floor"), "minimumReleaseAge": "3 days"}
    doc = {**doc, "packageRules": [first, *raw[1:]]}
    found = age_findings(doc, belt())
    floor = "default.json: the floor says 3 days"
    want: list[str] = [f"{floor}, the belt's minimum_release_age 168h"]
    assert found == want


def test_npm_off_pnpm_is_refused() -> None:
    """Planted: npm waiting less than pnpm reds."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    npm = {**decoded.as_object(raw[1], "npm"), "minimumReleaseAge": "2 days"}
    doc = {**doc, "packageRules": [raw[0], npm, *raw[2:]]}
    found = age_findings(doc, belt())
    want: list[str] = ["default.json: npm waits 2 days, the belt's pnpm 4320 minutes"]
    assert found == want


def test_npm_rule_missing_is_refused() -> None:
    """Planted: without the npm rule, upstream's three days is unstated."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    doc = {**doc, "packageRules": [raw[0], *raw[2:]]}
    found = age_findings(doc, belt())
    where = "default.json: the npm age rule sits at []"
    want: list[str] = [f"{where}, not right after the floor"]
    assert found == want


FIRST_PARTY = ["monumental-archive/**"]
# Renovate names an image with its registry, and a glob anchors at the
# start of the name, so the repos' pattern never reaches one.
FIRST_PARTY_IMAGES = ["ghcr.io/monumental-archive/**"]
UNLIMITED = ("prConcurrentLimit", "prHourlyLimit")


def rule_for(rules: list[dict[str, object]], names: list[str]) -> dict[str, object]:
    """Find the one rule that selects exactly these names.

    Returns:
        That rule; empty when none or several do.

    """
    found = [r for r in rules if r.get("matchPackageNames") == names]
    return found[0] if len(found) == 1 else {}


def limit_findings(doc: dict[str, object]) -> list[str]:
    """Every delivery the org depends on that a limit can skip (#2162).

    Returns:
        One message per missing zero; empty when the preset holds.

    """
    raw = decoded.as_array(doc.get("packageRules", []), "packageRules")
    rules = [decoded.as_object(r, "packageRules[]") for r in raw]
    blocks: list[tuple[str, dict[str, object]]] = [
        (
            "lockFileMaintenance",
            decoded.as_object(
                doc.get("lockFileMaintenance", {}), "lockFileMaintenance"
            ),
        ),
        ("the first-party rule", rule_for(rules, FIRST_PARTY)),
        ("the first-party image rule", rule_for(rules, FIRST_PARTY_IMAGES)),
    ]
    found: list[str] = []
    for where, block in blocks:
        for key in UNLIMITED:
            value = block.get(key)
            if value != 0 or isinstance(value, bool):
                found.append(
                    f"default.json: {where} has {key} {value!r}, not 0;"
                    " a limit can skip it"
                )
    return found


def first_party_age_findings(doc: dict[str, object]) -> list[str]:
    """Every first-party selector the age floor still holds.

    Returns:
        One message per selector without its zero age, by name.

    """
    raw = decoded.as_array(doc.get("packageRules", []), "packageRules")
    rules = [decoded.as_object(r, "packageRules[]") for r in raw]
    found: list[str] = []
    for names in (FIRST_PARTY, FIRST_PARTY_IMAGES):
        age = rule_for(rules, names).get("minimumReleaseAge")
        if age != "0 days":
            found.append(
                f"default.json: {names[0]} waits {age!r}, not 0 days;"
                " the floor holds first-party"
            )
    return found


# No limit skips lock file maintenance or the canon bump (#2162).
def test_limits_preset_holds() -> None:
    """Both deliveries carry both zeros."""
    none: list[str] = []
    assert limit_findings(preset()) == none


def test_maintenance_limited_is_refused() -> None:
    """Planted: lock file maintenance without the zeros reds, by name."""
    doc = preset()
    block = decoded.as_object(doc["lockFileMaintenance"], "lockFileMaintenance")
    kept = {k: v for k, v in block.items() if k not in UNLIMITED}
    found = limit_findings({**doc, "lockFileMaintenance": kept})
    where = "default.json: lockFileMaintenance has"
    want: list[str] = [
        f"{where} prConcurrentLimit None, not 0; a limit can skip it",
        f"{where} prHourlyLimit None, not 0; a limit can skip it",
    ]
    assert found == want


def test_first_party_limited_is_refused() -> None:
    """Planted: the first-party rule with a limit of 2 reds, by name."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    planted = [
        {**decoded.as_object(r, "packageRules[]"), "prHourlyLimit": 2}
        if decoded.as_object(r, "packageRules[]").get("matchPackageNames")
        == FIRST_PARTY
        else r
        for r in raw
    ]
    found = limit_findings({**doc, "packageRules": planted})
    where = "default.json: the first-party rule has"
    want: list[str] = [f"{where} prHourlyLimit 2, not 0; a limit can skip it"]
    assert found == want


def test_image_limited_is_refused() -> None:
    """Planted: the image rule without its zeros reds, by name."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    planted = [
        {
            k: v
            for k, v in decoded.as_object(r, "packageRules[]").items()
            if k not in UNLIMITED
        }
        if decoded.as_object(r, "packageRules[]").get("matchPackageNames")
        == FIRST_PARTY_IMAGES
        else r
        for r in raw
    ]
    found = limit_findings({**doc, "packageRules": planted})
    where = "default.json: the first-party image rule has"
    want: list[str] = [
        f"{where} prConcurrentLimit None, not 0; a limit can skip it",
        f"{where} prHourlyLimit None, not 0; a limit can skip it",
    ]
    assert found == want


# No first-party package waits out the age floor, images included.
def test_first_party_age_preset_holds() -> None:
    """Both first-party selectors carry the zero age."""
    none: list[str] = []
    assert first_party_age_findings(preset()) == none


def test_image_rule_missing_is_refused() -> None:
    """Planted: without the image rule, ghcr images wait seven days."""
    doc = preset()
    raw = decoded.as_array(doc["packageRules"], "packageRules")
    kept = [
        r
        for r in raw
        if decoded.as_object(r, "packageRules[]").get("matchPackageNames")
        != FIRST_PARTY_IMAGES
    ]
    found = first_party_age_findings({**doc, "packageRules": kept})
    where = "default.json: ghcr.io/monumental-archive/**"
    why = "the floor holds first-party"
    want: list[str] = [f"{where} waits None, not 0 days; {why}"]
    assert found == want

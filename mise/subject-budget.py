#!/usr/bin/env python3
"""Prove every declared dependency mints a commit subject that fits.

Org canon — the enforcement half of #576. Renovate is the org's only
producer of PERMANENT commit subjects that mints from unbounded input:
`squash_merge_commit_title` is PR_TITLE, so whatever the bot renders
becomes an immutable commit subject. Holding that OUTPUT to the commit
canon fails at the one moment nobody can fix it — the bot owns its
titles and regenerates them from a template, so a manual retitle is
reverted minutes later (#574). Length is enforced here instead, over the
INPUT set, on the pull request where a human introduces the input.

The model: for every dependency this repo declares, render the widest
subject the org's owned template could mint for it, and hold that to the
ceiling. A dependency that passes cannot produce a title that does not.

Reads the tracked file list on stdin, the belt convention: the task
enumerates with `git ls-files` and the helper consumes, so the walk obeys
the standing rule in one place and this script starts no processes.
"""

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------
# The pinned allowances.
#
# PR_NUMBER_DIGITS — GitHub appends " (#N)" to the squash subject, and N
# is the repo's shared issue and pull request counter. Pinning today's
# width would re-open this class the moment a repo rolls over a digit, so
# it is pinned at a worst case instead: the canon reached #594 in five
# months, about 1,400 a year, so six digits is on the order of seven
# centuries at the org's own rate. The choice costs two characters of
# dependency name against five digits, which is why it is stated here
# rather than assumed.
#
# Under committed's wrap-aware ceiling the suffix is in fact free — the
# " (#N)" tail is unwrappable, so it never trips the gate at any width
# (measured; see mise/committed.toml). It is counted anyway, because this
# budget holds machine-minted subjects to a LITERAL 72 columns: a
# template has no excuse for an overhanging tail, and the margin is what
# keeps a bot pull request off the gate entirely.
#
# THE ADVISORY SUBJECT SPENDS THIS ALLOWANCE ON THE SUFFIX (#686). It is
# an allowance, not the ceiling: one ceiling is read from committed.toml
# and one comparison is made against it, here as everywhere. What the
# advisory rendering does not do is charge BOTH the eleven columns
# Renovate appends and the ten this margin costs, because charging both
# is unsatisfiable rather than strict. Measured 2026-08-21 against a real
# stele checkout: a Go module on a pseudo-version floors at 78 columns
# with a ONE-character topic — the version is fixed-width at 34 by
# construction and takes no growth allowance — so `jcs`, already the
# shortest name in the org and chosen for this dependency on #576, would
# be six past a ceiling no rename can reach. A delivered gate a repo
# cannot pass is not enforcement.
#
# The suffix plus its space is eleven columns against this margin's ten,
# so the advisory rendering is the binding one for every dependency and
# the ordinary check rides free underneath it: the content budget is 61
# columns rather than 62. Still strictly stronger than the gate, whose
# real condition is a last whitespace at or before column 72 — which is
# exactly what `content + suffix <= 72` proves.
PR_NUMBER_DIGITS = 6

# VERSION_GROWTH — the subject carries the NEW version, which by
# definition is not in the tree yet, so the budget measures the declared
# one and adds room to grow. A single bump widens a stable version by at
# most one character (`v9.9.9` to `v10.0.0`); three covers a rollover in
# every component of a semver triple, which no dependency in this org has
# done. Renovate's ignoreUnstable keeps prerelease suffixes out of
# newValue, so the widths this must cover are release tags and short
# digests, nothing longer.
#
# The allowance is cumulative, not per-bump: each real widening spends
# one of the three. A dependency that exhausts them goes red on a bot
# pull request — loudly, with the remedy this script prints — after
# roughly three major-digit rollovers.
VERSION_GROWTH = 3

# The squash tail GitHub appends, at the pinned worst case. Named once
# so the advisory rendering can say what it is not charging (#686).
PR_TAIL = f" (#{'9' * PR_NUMBER_DIGITS})"

# The five fields that compose a commit subject. Every one is READ from
# default.json — there is no default table here and no fallback, by
# review decision on #576: a simulation that fills in a value the org did
# not write is agreeing with Renovate from memory, and an upstream
# default changing underneath it would be invisible. So the preset sets
# all five explicitly, including where the value equals Renovate's own
# default, and an absent one is a hard error naming the field.
MESSAGE_CONFIG = (
    "commitMessageAction",
    "commitMessageTopic",
    "commitMessageExtra",
    "semanticCommitType",
    "semanticCommitScope",
)
# The FOUR that carry text into the subject, in the order Renovate
# composes them; the other two form the `type(scope):` prefix.
#
# commitMessageSuffix is the sixth field and it is not in MESSAGE_CONFIG
# above, because it does not arrive the way the other five do (#686).
# No repo sets it at the top level; the one suffix in force org-wide
# comes from the `vulnerabilityAlerts` block, where Renovate's own
# default appends `[SECURITY]` — eleven columns, in force whether or not
# a preset writes it (measured at renovatebot/renovate@b7f19dc9, and
# written out explicitly in default.json since #667). So it is resolved
# from that block rather than from packageRules, and every dependency is
# rendered TWICE: the ordinary subject and the advisory one. Both are
# held to the ceiling, because an advisory pull request is the single
# worst one in the org to have wedged on a naming rule.
MESSAGE_FIELDS = (
    "commitMessageAction",
    "commitMessageTopic",
    "commitMessageExtra",
    "commitMessageSuffix",
)

MISE_CONFIG = re.compile(r"^\.?mise(\.[\w-]+)?\.toml$")
MISE_NESTED = re.compile(r"^config(\.[\w-]+)?\.toml$")
CARGO_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
NPM_TABLES = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)
USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*[\"']?([^\s\"'#]+)[\"']?[ \t]*(?:#[ \t]*(\S+))?",
    re.MULTILINE,
)
# Direct requires only. An `// indirect` module moves when the direct
# dependency that pulls it in moves — `gomodTidy` rewrites the block —
# so Renovate raises no pull request of its own for one, and modelling
# them would measure subjects nothing mints. stele: 16 direct, 74
# indirect.
GO_REQUIRE = re.compile(
    r"^\s*([a-z0-9.-]+\.[a-z]{2,}/\S+)\s+(v\S+)[ \t]*(//.*)?$",
    re.MULTILINE,
)
# A Go pseudo-version is fixed-width by construction
# (`v0.0.0-20060102150405-abcdefabcdef`), so the growth allowance below
# does not apply to one: it is replaced by another of the same width, or
# by a release tag that is shorter.
PSEUDO_VERSION = re.compile(r"^v.*-\d{14}-[0-9a-f]{12}$")
VERSION_LIKE = re.compile(r"v?\d[\w.+-]*")
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
# What Renovate renders for a digest update: newDigestShort, seven hex
# characters — `chore(deps): update monumental-archive/signer digest to
# b05fe88`. The 40-character pin in the tree is never what reaches the
# subject, so measuring it would refuse every SHA-pinned action in the
# org over a string no title can contain.
DIGEST_SHORT = 7
BACKEND = re.compile(r"^[a-z][a-z0-9]*:")
# Renovate's matchStrings are JavaScript regexes, where a named group is
# `(?<name>…)`; Python spells it `(?P<name>…)` and raises on the other.
# Translating is the whole of the difference for the patterns a
# customManager can carry — everything else in them is ordinary PCRE.
JS_GROUP = re.compile(r"\(\?<(?=[A-Za-z_])")


class Dep(NamedTuple):
    """One declared dependency, as Renovate would name and value it.

    `manager` is Renovate's manager id, carried because the org's preset
    scopes a rule by it (gomod keeps `fix` where everything else takes
    `chore`), so the renderer cannot resolve the prefix without it.
    """

    name: str
    current: str
    manager: str
    origin: str


class Template(NamedTuple):
    """The owned subject template, gathered from where the org wrote it.

    Three sources, because the org writes the template in three places
    and Renovate reads all three: the extended preset, the repo's own
    packageRules which resolve after it (#677), and the advisory suffix
    which lives in the vulnerabilityAlerts block rather than in any rule
    (#686). Carried as one value so no caller can supply two of the
    three and silently model a subject the bot will not mint.
    """

    preset: dict
    repo_rules: list[dict]
    suffix: str


class Finding(NamedTuple):
    """A dependency whose widest possible subject overruns the ceiling."""

    width: int
    dep: Dep
    subject: str


def read_text(path: Path, report: list[str]) -> str | None:
    """Read a tracked file, recording rather than raising on failure.

    Returns:
        The file's text, or None when it could not be read.

    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        report.append(f"{path}: unreadable ({exc})")
        return None


def load_toml(path: Path, report: list[str]) -> dict:
    """Parse a tracked TOML file, recording rather than raising.

    Returns:
        The parsed document, or an empty one when it could not be read.

    """
    text = read_text(path, report)
    if text is None:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        report.append(f"{path}: unreadable as TOML ({exc})")
        return {}


def load_json(path: Path, report: list[str]) -> dict:
    """Parse a tracked JSON file, recording rather than raising.

    Returns:
        The parsed document, or an empty one when it could not be read.

    """
    text = read_text(path, report)
    if text is None:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        report.append(f"{path}: unreadable as JSON ({exc})")
        return {}


# ---------------------------------------------------------------------
# The owned template.


def preset_path(files: list[Path]) -> Path:
    """Locate the subject template in force for this repository.

    A consumer repo extends the PINNED canon, so the template that mints
    its titles is the delivered one. The canon itself owns default.json,
    and its own gate must measure the file being EDITED rather than the
    release it currently pins — a canon can never contain its own next
    tag, so the pinned copy is always N-1 (#227). Tracked file wins.

    Returns:
        The path to the default.json whose template applies here.

    """
    if Path("default.json") in files:
        return Path("default.json")
    canon = os.environ.get("ORG_CANON_DIR", "")
    if not canon:
        sys.exit(
            "lint:subject-budget: ORG_CANON_DIR is unset, so the owned "
            "template cannot be read; the org belt did not arrive: "
            "CI sets MISE_GLOBAL_CONFIG_FILE, locally it is a "
            "~/.config/mise/conf.d symlink into a canon checkout",
        )
    return Path(canon) / "default.json"


def matches(pattern: str, dep_name: str) -> bool:
    """Test a Renovate matchPackageNames pattern against a dependency.

    Exact names, `*`, and glob patterns. Matching is tried against the
    backend-stripped name too, because Renovate matches on packageName
    where one exists: `monumental-archive/**` selected the mise dep
    `github:monumental-archive/stele` on #574, which is how that pull
    request came out scoped `chore(canon)`.

    Returns:
        True when the pattern selects this dependency.

    """
    if pattern == "*":
        return True
    names = {dep_name, BACKEND.sub("", dep_name)}
    if not any(ch in pattern for ch in "*?"):
        return pattern in names
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    regex = regex.replace(r"\?", ".")
    return any(re.fullmatch(regex, name) for name in names)


def selects(rule: dict, dep: Dep) -> bool:
    """Test whether a packageRule applies to one dependency.

    Only the two matchers the org's preset uses are modelled, and both
    must hold when both are present — Renovate ANDs across matcher kinds
    and ORs within one. A rule carrying neither selects nothing here: it
    is setting something this task does not render.

    Returns:
        True when the rule's matchers all admit this dependency.

    """
    managers = rule.get("matchManagers")
    names = rule.get("matchPackageNames")
    if managers is None and names is None:
        return False
    if managers is not None and dep.manager not in managers:
        return False
    return names is None or any(matches(p, dep.name) for p in names)


def apply_rules(config: dict, rules: list[dict], dep: Dep) -> None:
    """Fold one packageRules list into a resolved config, in order.

    Later wins per field, which is Renovate's own precedence within a
    list. Mutates `config`; returns nothing, because there is one
    resolution being built and two lists folded into it.
    """
    for rule in rules:
        if selects(rule, dep):
            config.update({k: rule[k] for k in MESSAGE_CONFIG if k in rule})


def effective(template: Template, dep: Dep) -> dict:
    """Resolve the message fields Renovate would use for one dependency.

    packageRules are applied in order and later wins per field, which is
    Renovate's own precedence. The repo's OWN rules are folded in after
    the preset's, because that is where Renovate puts them: a repo
    extends the canon, so the extended preset's list resolves first and
    the repo's appends to it (#677). Modelling the preset alone left a
    repo-level rule invisible to the simulation that exists to prove
    these fields fit — fail-SAFE only by luck, since the one such rule
    in the org narrows (#668's `fix` against the preset's `chore`), and
    a repo-level `commitMessageTopic` (the remedy this task's own
    failure message prints) would have widened it green.

    The absent-field check sits BETWEEN the two lists, and that placement
    is the rule rather than an accident: a repo may override a field the
    preset already sets, but it may not be the only place one is
    written. Otherwise a consumer that does not extend the canon
    inherits nothing and the hard error stops meaning what it says.

    Returns:
        The five message fields, every one of them written by the org.

    """
    preset = template.preset
    config = {k: preset[k] for k in MESSAGE_CONFIG if k in preset}
    apply_rules(config, preset.get("packageRules", []), dep)
    missing = [k for k in MESSAGE_CONFIG if k not in config]
    if missing:
        sys.exit(
            "lint:subject-budget: the owned template does not set "
            + ", ".join(missing)
            + ".\n  Every field the subject is composed from is set"
            " explicitly in default.json (#576); this task will not"
            " assume Renovate's default for one.",
        )
    apply_rules(config, template.repo_rules, dep)
    return config


def advisory_suffix(preset: dict, repo_config: dict) -> str:
    """Read the suffix Renovate appends to an ADVISORY subject.

    It lives in the `vulnerabilityAlerts` block, not in packageRules,
    and Renovate merges that block child-over-parent — so a repo's own
    block wins the field where it sets one, and the extended preset's
    answers otherwise.

    Absent everywhere is a hard error, the same law the five composed
    fields answer to (#576): Renovate's default appends `[SECURITY]`
    whether or not the org writes it, so a budget that reads no suffix
    is not modelling "no suffix" — it is agreeing with an upstream
    default from memory, eleven columns wide, on the one pull request
    nobody can afford to have wedged.

    `enabled` is deliberately not read. A repo that turned advisories
    off would mint no such subject, but modelling one anyway costs
    margin and never correctness, and the org enforces the block
    enabled everywhere (#667).

    Returns:
        The suffix, exactly as the org wrote it.

    """
    for source in (repo_config, preset):
        block = source.get("vulnerabilityAlerts", {})
        if "commitMessageSuffix" in block:
            return block["commitMessageSuffix"]
    sys.exit(
        "lint:subject-budget: the owned template does not set "
        "vulnerabilityAlerts.commitMessageSuffix.\n  Renovate appends "
        "one to every advisory subject whether or not the org writes it "
        "(#686); this task will not assume its default for the field it "
        "measures.",
    )


def render(config: dict, dep_name: str, width: int) -> tuple[str, str | None]:
    """Compose the widest subject the template can mint for a dependency.

    Returns:
        The subject, and the text of any field carrying a placeholder
        this task cannot resolve — so an unmodelled template is reported
        rather than silently measured as a literal.

    """
    unmodelled = None
    version = "9" * width
    parts = [f"{config['semanticCommitType']}({config['semanticCommitScope']}):"]
    for key in MESSAGE_FIELDS:
        text = config[key]
        if not text:
            continue
        for token, value in (("depName", dep_name), ("newValue", version)):
            text = text.replace("{{{" + token + "}}}", value)
            text = text.replace("{{" + token + "}}", value)
        if "{{" in text:
            unmodelled = f"{key}: {config[key]}"
        parts.append(text)
    return " ".join(p for p in parts if p) + PR_TAIL, unmodelled


# ---------------------------------------------------------------------
# The declared dependency set, one manager at a time.


def is_mise_config(path: Path) -> bool:
    """Test whether Renovate's mise manager would read this file.

    Returns:
        True for the mise config filenames, nested and top-level.

    """
    if MISE_CONFIG.fullmatch(path.name):
        return True
    return bool(MISE_NESTED.fullmatch(path.name)) and path.parent.name in {
        "mise",
        ".mise",
    }


def widest_version(spec: object) -> str:
    """Pick the widest version a mise tool entry declares.

    A tool may be pinned as a string, a table, or an array of tables (a
    repo that fuzzes holds two toolchains). The budget takes the widest,
    because any of them can be the one that moves.

    Returns:
        The version string, or empty when the entry declares none.

    """
    entries = spec if isinstance(spec, list) else [spec]
    widest = ""
    for entry in entries:
        value = entry if isinstance(entry, str) else entry.get("version", "")
        if isinstance(value, str) and len(value) > len(widest):
            widest = value
    return widest


def from_mise(files: list[Path], report: list[str]) -> list[Dep]:
    """Collect the tool pins Renovate's mise manager raises for.

    Returns:
        One entry per declared tool.

    """
    deps: list[Dep] = []
    for path in files:
        if not is_mise_config(path):
            continue
        for tool, spec in load_toml(path, report).get("tools", {}).items():
            version = widest_version(spec)
            if not version:
                report.append(f"{path}: [tools] {tool} declares no readable version")
                continue
            deps.append(Dep(tool, version, "mise", str(path)))
    return deps


def is_action_file(path: Path) -> bool:
    """Test whether Renovate's github-actions manager would read a file.

    Returns:
        True for workflows and composite action definitions.

    """
    workflow = str(path).startswith(".github/workflows/")
    return (workflow and path.suffix in {".yml", ".yaml"}) or path.name in {
        "action.yml",
        "action.yaml",
    }


def from_actions(files: list[Path], report: list[str]) -> list[Dep]:
    """Collect the actions and reusable workflows this repo pins.

    Returns:
        One entry per action repository, at its widest declared version.

    """
    found: dict[str, Dep] = {}
    for path in files:
        if not is_action_file(path):
            continue
        content = read_text(path, report)
        if content is None:
            continue
        for match in USES.finditer(content):
            ref = match.group(1)
            # Local (`./`), self-repository (`$/`) and container steps
            # name no upstream dependency, and Renovate raises nothing.
            if ref.startswith(("./", "$/", "docker://")) or "@" not in ref:
                continue
            target, _, pinned = ref.partition("@")
            repo = "/".join(target.split("/")[:2])
            if repo.count("/") != 1:
                continue
            # The trailing `# vX.Y.Z` is what Renovate writes into the
            # subject for a SHA-pinned action; without one, the ref is.
            comment = match.group(2) or ""
            if VERSION_LIKE.fullmatch(comment):
                current = comment
            elif SHA_PIN.fullmatch(pinned):
                current = "0" * DIGEST_SHORT
            else:
                current = pinned
            if len(current) > len(found.get(repo, Dep("", "", "", "")).current):
                found[repo] = Dep(repo, current, "github-actions", str(path))
    return list(found.values())


def registry_deps(table: dict) -> list[tuple[str, str]]:
    """Pick the entries of a manifest table that name a registry version.

    Path and workspace-inherited entries resolve inside the tree, so
    Renovate raises nothing for them.

    Returns:
        Name and version-constraint pairs.

    """
    found = []
    for name, spec in table.items():
        if isinstance(spec, str):
            found.append((name, spec))
        elif isinstance(spec, dict) and not ("path" in spec or spec.get("workspace")):
            version = spec.get("version")
            if isinstance(version, str):
                found.append((name, version))
    return found


def cargo_tables(data: dict) -> list[dict]:
    """Gather every manifest table Renovate's cargo manager reads.

    Returns:
        The dependency tables, including workspace and target-specific.

    """
    tables = [data.get(key, {}) for key in CARGO_TABLES]
    tables += [data.get("workspace", {}).get(key, {}) for key in CARGO_TABLES]
    for triple in data.get("target", {}).values():
        tables += [triple.get(key, {}) for key in CARGO_TABLES]
    return tables


def from_cargo(files: list[Path], report: list[str]) -> list[Dep]:
    """Collect the crates this repo declares.

    Returns:
        One entry per declared crate.

    """
    deps: list[Dep] = []
    for path in files:
        if path.name != "Cargo.toml":
            continue
        for table in cargo_tables(load_toml(path, report)):
            deps += [Dep(n, v, "cargo", str(path)) for n, v in registry_deps(table)]
    return deps


def from_npm(files: list[Path], report: list[str]) -> list[Dep]:
    """Collect the npm packages this repo declares.

    Returns:
        One entry per declared package.

    """
    deps: list[Dep] = []
    for path in files:
        if path.name != "package.json":
            continue
        data = load_json(path, report)
        for key in NPM_TABLES:
            deps += [
                Dep(name, spec, "npm", str(path))
                for name, spec in data.get(key, {}).items()
                if isinstance(spec, str)
            ]
    return deps


def from_gomod(files: list[Path], report: list[str]) -> list[Dep]:
    """Collect the Go modules this repo requires.

    Beyond the manager list #576 enumerates, on the same mechanism:
    stele tracks a go.mod, so gomod raises Renovate pull requests there,
    and leaving it out would have left the class open in the one repo
    that has it.

    Returns:
        One entry per required module.

    """
    deps: list[Dep] = []
    for path in files:
        if path.name != "go.mod":
            continue
        content = read_text(path, report)
        if content is None:
            continue
        found = GO_REQUIRE.finditer(content)
        deps += [
            Dep(m.group(1), m.group(2), "gomod", str(path))
            for m in found
            if "indirect" not in (m.group(3) or "")
        ]
    return deps


def manager_targets(manager: dict, files: list[Path]) -> list[Path]:
    """Select the tracked files one customManager claims.

    Returns:
        The files whose path matches any of its patterns.

    """
    patterns = []
    for raw in manager.get("managerFilePatterns", []):
        bare = raw.startswith("/") and raw.endswith("/")
        patterns.append(raw[1:-1] if bare else raw)
    return [f for f in files if any(re.search(p, str(f)) for p in patterns)]


def custom_matches(manager: dict, path: Path, content: str, origin: str) -> list[Dep]:
    """Run one customManager's patterns over one file, as Renovate does.

    A literal depNameTemplate names the dependency outright; otherwise
    the depName comes from the matchString's own named group, so the
    patterns are executed over the files they claim rather than guessed
    at.

    Returns:
        One entry per match, named by template or by capture.

    """
    template = manager.get("depNameTemplate", "")
    literal = bool(template) and "{{" not in template
    deps: list[Dep] = []
    for raw in manager.get("matchStrings", []):
        for match in re.finditer(JS_GROUP.sub("(?P<", raw), content):
            groups = match.groupdict()
            name = template if literal else groups.get("depName")
            if not name:
                unresolved = f"{origin}: {path} (template {template!r})"
                deps.append(Dep("", "", "custom.regex", unresolved))
                continue
            current = groups.get("currentValue") or groups.get("currentDigest") or ""
            deps.append(Dep(name, current, "custom.regex", origin))
    return deps


def from_custom_managers(
    sources: list[tuple[str, dict]],
    files: list[Path],
    report: list[str],
) -> list[Dep]:
    """Collect what the regex managers would raise pull requests for.

    Anything whose depName this task cannot resolve is reported, never
    skipped in silence.

    Returns:
        One entry per resolvable match.

    """
    deps: list[Dep] = []
    for origin, config in sources:
        for manager in config.get("customManagers", []):
            for path in manager_targets(manager, files):
                content = read_text(path, report)
                if content is None:
                    continue
                for dep in custom_matches(manager, path, content, origin):
                    if dep.name:
                        deps.append(dep)
                    else:
                        report.append(f"{dep.origin} names no resolvable depName")
    return deps


# ---------------------------------------------------------------------


def ceiling() -> int:
    """Read the ONE definition of the ceiling: the delivered config.

    Returns:
        The subject_length every commit in the org answers to.

    """
    belt = os.environ.get("ORG_BELT_DIR", "")
    config = Path(belt) / "committed.toml" if belt else None
    if config is None or not config.is_file():
        sys.exit(
            "lint:subject-budget: ORG_BELT_DIR carries no committed.toml, so "
            "the ceiling has no definition; the org belt did not arrive: "
            "CI sets MISE_GLOBAL_CONFIG_FILE, locally it is a "
            "~/.config/mise/conf.d symlink into a canon checkout",
        )
    return tomllib.loads(config.read_text(encoding="utf-8"))["subject_length"]


def collect(files: list[Path], report: list[str]) -> list[Dep]:
    """Enumerate every dependency the repo declares, across managers.

    Returns:
        One entry per declaration, before de-duplication.

    """
    deps: list[Dep] = []
    for manager in (from_mise, from_actions, from_cargo, from_npm, from_gomod):
        deps += manager(files, report)
    preset = load_json(preset_path(files), report)
    sources = [("default.json", preset)]
    repo_config = load_json(Path("renovate.json"), report)
    if repo_config.get("customManagers"):
        sources.append(("renovate.json", repo_config))
    return deps + from_custom_managers(sources, files, report)


def judge(
    deps: list[Dep],
    template: Template,
    limit: int,
    report: list[str],
) -> list[Finding]:
    """Measure each dependency's widest subject against the ceiling.

    TWO subjects are rendered per dependency, not one (#686): the
    ordinary one, and the advisory one carrying the template's suffix.
    Renovate mints both from the same template, so proving only the
    first proves the template fits for every pull request EXCEPT the one
    it would hurt most to have wedged. Neither leg is optional, which is
    why the three sources arrive as one Template rather than as
    arguments a caller can leave out.

    Only the widest overrunning subject is reported per dependency. The
    advisory one is never narrower, and the remedy for both is the same
    single rule, so a second finding would be a second line naming one
    fix. The subject printed carries the suffix where it is the culprit,
    which is what tells the two apart.

    Returns:
        The dependencies that overrun it, widest first.

    """
    findings: list[Finding] = []
    seen: set[str] = set()
    for dep in sorted(deps):
        if dep.name in seen:
            continue
        seen.add(dep.name)
        base = effective(template, dep)
        growth = 0 if PSEUDO_VERSION.fullmatch(dep.current) else VERSION_GROWTH
        width = len(dep.current) + growth
        widest, charged = "", 0
        for value in ("", template.suffix):
            subject, unmodelled = render(
                {**base, "commitMessageSuffix": value},
                dep.name,
                width,
            )
            line = f"{dep.name}: template field is unresolved — {unmodelled}"
            if unmodelled and line not in report:
                report.append(line)
            # The advisory rendering spends the pull-request-number
            # allowance on the suffix rather than charging both; the
            # reasoning is pinned beside PR_TAIL.
            cost = len(subject) - (len(PR_TAIL) if value else 0)
            if cost > charged:
                widest, charged = subject, cost
        if charged > limit:
            findings.append(Finding(charged, dep, widest))
    return sorted(findings, reverse=True)


def explain(findings: list[Finding], limit: int) -> None:
    """Print the over-budget dependencies and the remedy for them."""
    for width, dep, subject in findings:
        over = width - limit
        print(f"  {width:3d} cols (over by {over})  {dep.name}", file=sys.stderr)
        print(f"        from {dep.manager} ({dep.origin})", file=sys.stderr)
        print(f"        {subject}", file=sys.stderr)
    print(
        "\n  The subject template is the org's, so the fix is the org's: give the\n"
        "  dependency a shorter name in default.json rather than letting a bot\n"
        "  mint a subject nobody can shorten once it is a commit.\n\n"
        '    { "matchPackageNames": ["<depName>"],\n'
        '      "commitMessageTopic": "<short name>" }\n\n'
        "  A subject printed with a suffix is the ADVISORY one: Renovate appends\n"
        "  vulnerabilityAlerts.commitMessageSuffix to it, so every dependency is\n"
        "  measured twice and the same one rule shortens both. Its width above\n"
        f"  excludes the {PR_NUMBER_DIGITS}-digit pull request tail, which the\n"
        "  suffix spends that allowance in place of.\n\n"
        f"  Widths above allow {VERSION_GROWTH} characters of version growth, and\n"
        f"  an ordinary subject a {PR_NUMBER_DIGITS}-digit pull request number;\n"
        "  both are pinned and reasoned in mise/subject-budget.py.",
        file=sys.stderr,
    )


def main() -> int:
    """Run the budget over this repository.

    Returns:
        0 when every declared dependency fits, 1 when any does not.

    """
    files = [Path(line) for line in sys.stdin.read().split("\n") if line]
    if Path("renovate.json") not in files:
        print("lint:subject-budget: no renovate.json tracked, skipped")
        return 0

    report: list[str] = []
    deps = collect(files, report)
    if not deps:
        print("lint:subject-budget: no declared dependencies, skipped")
        for line in report:
            print(f"lint:subject-budget: unmodelled — {line}", file=sys.stderr)
        return 0

    limit = ceiling()
    preset = load_json(preset_path(files), report)
    # The repo's own packageRules resolve after the extended preset's,
    # so they are read from the working tree here and folded in there
    # (#677). Loaded a second time rather than threaded out of collect()
    # for the same reason the preset is: the two readers want different
    # halves of the same file, and one guaranteed-tracked re-read is
    # cheaper than a return type that carries both.
    repo_config = load_json(Path("renovate.json"), report)
    # The sixth field is resolved from the vulnerabilityAlerts block
    # rather than from packageRules, because that is where the org's
    # only suffix lives and where Renovate's own default applies it
    # (#686, #667).
    template = Template(
        preset=preset,
        repo_rules=repo_config.get("packageRules", []),
        suffix=advisory_suffix(preset, repo_config),
    )
    findings = judge(deps, template, limit, report)
    for line in report:
        print(f"lint:subject-budget: unmodelled — {line}", file=sys.stderr)

    counted = len({dep.name for dep in deps})
    if not findings:
        noun = "dependency" if counted == 1 else "dependencies"
        print(
            f"lint:subject-budget: {counted} {noun}, every subject the "
            f"owned template can mint fits {limit} columns",
        )
        return 0

    noun = "dependency" if counted == 1 else "dependencies"
    print(
        f"lint:subject-budget: {len(findings)} of {counted} {noun} would "
        f"mint a subject past {limit} columns\n",
        file=sys.stderr,
    )
    explain(findings, limit)
    return 1


if __name__ == "__main__":
    sys.exit(main())

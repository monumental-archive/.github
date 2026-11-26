#!/usr/bin/env python3
"""Generate the biome config a repository is checked against.

Org canon — the mechanism half of #695. The org's biome rules are one
file for every repo (`mise/biome-org.json`) and a repository never gets a
copy of it: `lint:biome` materialises the config for the length of one
run and a trap removes it, so there is no repo-side surface on which an
org rule can be switched off (#445).

That property is worth keeping and it costs the one thing a shared file
cannot carry: what the repository IS. biome sorts its framework and
library rules into DOMAINS, and under `preset: "all"` every domain is on
regardless — measured on monumental-archive, a React tree drew ten
`useQwikValidLexicalScope` findings, a Qwik rule judging a React
component against another framework's lexical-scope contract.

The fix is not to hand the config over. It is to let the repository state
one fact and keep the arithmetic here:

  the repo says   {"linter": {"domains": {"react": "all"}}}
  the belt writes react "all", every other identity domain "none", and
                  project/types/test "all" because they are org strictness

A repo can therefore only ever turn a domain ON for itself, and only for
a domain that is a statement about its dependencies. It cannot write
"none", cannot name `project`, `types` or `test`, and cannot put anything
else in the file — each refused by name below. Declaring nothing is not
an escape either: a repository whose own manifests carry a domain's
trigger package MUST claim it, because a react repo that stays silent
would otherwise get react's rules turned off by this very mechanism.

Over-claiming is deliberately legal. Naming a domain the repo has no
dependency for turns rules ON, and the hazard is one-directional — the
`lint:python-target` shape.

One rule has no domain and is still a fact about the project rather than
the org: `correctness/noNodejsModules`, which forbids `node:fs` and is
right only for code that cannot reach Node (#2116). The repository
already states which projects run on Node, in each `tsconfig.json`'s
`compilerOptions.types`, so the belt reads it there: the task resolves
every tracked tsconfig with `tsc --showConfig` and hands the documents
here, and a project whose resolved `types` names `node` gets one
`overrides` entry switching the rule off under the tsconfig's directory.
No tsconfig text is parsed here, and the repository writes nothing.
"""

import argparse
import copy
import json
import sys
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple

import findings
from decoded import (
    UndecodableError,
    as_array,
    as_object,
    is_object,
    is_strings,
    load_json,
)
from tracked import stdin_paths

if TYPE_CHECKING:
    from collections.abc import Mapping

NAME = "biome-config"

# The only value a repository may write. "none" is the belt's to write and
# "recommended" would be a repo lowering a domain it did claim.
CLAIM = "all"

# What `mise/biome-nursery-domains.tsv` writes where biome reports no
# domain for a rule. An explicit marker rather than an empty column: "no
# domain" is a statement the file makes.
NO_DOMAIN = "-"

# The value written for a nursery rule whose domain this repository did
# not claim. `preset: "all"` does not reach nursery, so the org names each
# rule "on" — and a named rule beats its own domain being "none" (#720),
# which is the whole reason this file has to intervene at all.
SILENCED = "off"

# Manifest sections a dependency can be declared in. `bundleDependencies`
# is deliberately absent: it is a list of names already required
# elsewhere, not a declaration of its own.
MANIFEST_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


# The `types` entry that makes a TypeScript project a Node program.
NODE_TYPES = "node"

# The one rule a Node project has switched off, as biome's config names it.
NODE_RULE = ("correctness", "noNodejsModules")


class Args(argparse.Namespace):
    """The parsed command line, typed."""

    org: Path
    domains: Path
    nursery: Path
    out: Path
    declaration: Path
    tsconfig: list[list[str]] | None = None


class Domains(NamedTuple):
    """The domain table, split by who decides each domain."""

    identity: dict[str, list[str]]
    org: list[str]


def read_domains(path: Path) -> Domains:
    """Read the delivered domain table.

    Returns:
        Identity domains mapped to their trigger packages, and the
        org-fixed domains in file order.

    """
    identity: dict[str, list[str]] = {}
    org: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        domain, kind, _rule, *rest = line.split("\t")
        if kind == "identity":
            triggers = rest[0] if rest else ""
            identity[domain] = [p for p in triggers.split(",") if p]
        else:
            org.append(domain)
    return Domains(identity=identity, org=org)


def read_nursery(path: Path) -> dict[str, str]:
    """Read which nursery rule belongs to which domain.

    Returns:
        Rule name mapped to its domain, or to NO_DOMAIN when it has none.

    """
    table: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        rule, domain = line.split("\t")
        table[rule] = domain
    return table


def declaration(
    document: object, identity: dict[str, list[str]]
) -> tuple[set[str], list[str]]:
    """Read a repository's `biome.json` against what a repo may say.

    Returns:
        The domains it claims, and one line per refusal, empty when the
        declaration is admissible.

    """
    if not is_object(document):
        return set(), ["the file must be a JSON object"]
    problems = [
        f'"{key}" — a repository states its domains and nothing else'
        for key in document
        if key not in {"$schema", "linter"}
    ]
    linter = document.get("linter", {})
    if not is_object(linter):
        return set(), [*problems, '"linter" must be an object']
    problems += [
        f'"linter.{key}" — the org owns every rule; the repo owns only domains'
        for key in linter
        if key != "domains"
    ]
    domains = linter.get("domains", {})
    if not is_object(domains):
        return set(), [*problems, '"linter.domains" must be an object']
    for domain, value in domains.items():
        if domain not in identity:
            problems.append(
                f'"{domain}" is not a domain a repository may claim — '
                "project, types and test are org strictness",
            )
        elif value != CLAIM:
            problems.append(
                f'"{domain}": "{value}" — the only value a repository may '
                f'write is "{CLAIM}"; the belt writes every "none"',
            )
    return set(domains), problems


def declared_packages(manifests: list[Path]) -> dict[str, list[str]]:
    """Collect every dependency the repository's own manifests name.

    Returns:
        Package name mapped to the manifests declaring it.

    """
    found: dict[str, list[str]] = {}
    for manifest in manifests:
        try:
            document = load_json(manifest.read_text(encoding="utf-8"))
        except OSError, UndecodableError:
            continue
        if not is_object(document):
            continue
        for section in MANIFEST_SECTIONS:
            block = document.get(section, {})
            if is_object(block):
                for package in block:
                    found.setdefault(package, []).append(str(manifest))
    return found


def omissions(
    claimed: set[str],
    identity: dict[str, list[str]],
    packages: dict[str, list[str]],
) -> list[str]:
    """Find domains the repository has the dependency for but did not claim.

    Returns:
        One line per unclaimed domain, empty when the declaration is honest.

    """
    missing = []
    for domain, triggers in identity.items():
        if domain in claimed:
            continue
        evidence = [(t, packages[t][0]) for t in triggers if t in packages]
        if evidence:
            package, manifest = evidence[0]
            missing.append(f'"{domain}" — {manifest} declares {package}')
    return missing


def silence_nursery(
    config: dict[str, object],
    claimed: set[str],
    domains: Domains,
    nursery: dict[str, str],
) -> int:
    """Switch off nursery rules belonging to a framework this repo is not.

    A domain at "none" stops the rules of the eight stable groups and does
    NOT stop a nursery rule the org named "on" — biome offers no bulk
    switch for nursery, so every one of the 87 is named, and a named rule
    wins. This closes that in the one place the org can: the generated
    config, where the repository's identity is already known.

    Returns:
        How many rules were switched off. A `linter`, `rules` or
        `nursery` that is not an object is refused by `decoded`, naming
        which.

    """
    linter = as_object(config.get("linter", {}), "linter")
    rules = as_object(linter.get("rules", {}), "linter.rules")
    block = as_object(rules.get("nursery", {}), "linter.rules.nursery")
    silenced = 0
    for rule in block:
        domain = nursery.get(rule, NO_DOMAIN)
        # No domain, or a domain the org fixes for every repo, means the
        # rule is not a statement about what this repository is.
        if domain in {NO_DOMAIN, *domains.org} or domain in claimed:
            continue
        block[rule] = SILENCED
        silenced += 1
    return silenced


def is_node_project(resolved: object, name: str) -> bool:
    """Say whether one resolved tsconfig declares Node's types.

    `resolved` is `tsc -p <config> --showConfig`'s output, so `extends`
    is already followed and the text already parsed by TypeScript.

    Returns:
        True when its `compilerOptions.types` holds "node".

    """
    document = as_object(resolved, name)
    options = as_object(document.get("compilerOptions", {}), f"{name} compilerOptions")
    types = options.get("types", [])
    return is_strings(types) and NODE_TYPES in types


def node_overrides(resolved: Mapping[str, object]) -> list[dict[str, object]]:
    """Write one override per Node project, scoped to its tsconfig's directory.

    `resolved` maps each tracked tsconfig's path to its resolved document.
    The directory is the tsconfig's own, whatever its `include` says: a
    file outside it keeps the rule, which is the safe direction.

    Returns:
        The entries, in tsconfig order; none when no project is Node.

    """
    group, rule = NODE_RULE
    entries: list[dict[str, object]] = []
    for name, document in resolved.items():
        if not is_node_project(document, name):
            continue
        directory = PurePosixPath(name).parent
        pattern = "**" if str(directory) == "." else f"{directory}/**"
        entries.append({
            "includes": [pattern],
            "linter": {"rules": {group: {rule: SILENCED}}},
        })
    return entries


def generate(
    org: dict[str, object],
    claimed: set[str],
    domains: Domains,
    nursery: dict[str, str],
    resolved: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], int]:
    """Merge the org's rules with the repository's identity.

    `resolved` maps each tracked tsconfig to its `tsc --showConfig`
    document; a Node project among them gets its override appended to
    the org's. biome applies every override a file matches (measured on
    2.5.7), so the org's test-file entry and a Node project's compose.

    Returns:
        The config biome is actually run against, and how many nursery
        rules its identity switched off.

    """
    config = copy.deepcopy(org)
    written = {d: CLAIM if d in claimed else "none" for d in domains.identity}
    written.update(dict.fromkeys(domains.org, CLAIM))
    linter = as_object(config.get("linter", {}), "linter")
    linter["domains"] = written
    config["linter"] = linter
    silenced = silence_nursery(config, claimed, domains, nursery)
    entries = node_overrides(resolved or {})
    if entries:
        overrides = as_array(config.get("overrides", []), "overrides")
        config["overrides"] = [*overrides, *entries]
    return config, silenced


def complain(headline: str, lines: list[str], remedy: str) -> int:
    """Print a refusal in the belt's shape.

    Returns:
        A process exit status.

    """
    findings.finding("", headline, [*lines, remedy])
    return 1


def read_resolved(pairs: list[list[str]]) -> dict[str, object]:
    """Read each tsconfig's resolved document, keyed by the tsconfig's path.

    Returns:
        The tracked tsconfig path mapped to its decoded document.

    Raises:
        UndecodableError: a document is not JSON, naming its tsconfig.

    """
    resolved: dict[str, object] = {}
    for name, path in pairs:
        try:
            resolved[name] = load_json(Path(path).read_text(encoding="utf-8"))
        except UndecodableError as bad:
            msg = f"{name}: tsc --showConfig printed no JSON: {bad}"
            raise UndecodableError(msg) from bad
    return resolved


def run(args: Args, manifests: list[Path]) -> int:
    """Validate the declaration and write the generated config.

    Returns:
        A process exit status.

    """
    domains = read_domains(args.domains)
    nursery = read_nursery(args.nursery)
    claimed: set[str] = set()
    if args.declaration.is_file():
        try:
            document = load_json(args.declaration.read_text(encoding="utf-8"))
        except UndecodableError as bad:
            return complain(
                f"{args.declaration} is not valid JSON",
                [str(bad)],
                "it declares only this repository's biome domains",
            )
        claimed, problems = declaration(document, domains.identity)
        if problems:
            return complain(
                f"{args.declaration} says more than a repository may say",
                problems,
                "the org's rules are delivered; this file carries linter.domains only",
            )

    unclaimed = omissions(claimed, domains.identity, declared_packages(manifests))
    if unclaimed:
        return complain(
            "a domain this repository HAS is not declared, so the belt would "
            "turn its rules off",
            unclaimed,
            f'add it to {args.declaration} as "<domain>": "{CLAIM}" — '
            "silence here lowers an org rule",
        )

    try:
        resolved = read_resolved(args.tsconfig or [])
        node = len(node_overrides(resolved))
    except UndecodableError as bad:
        return complain(
            "a resolved tsconfig.json is not the shape the belt reads",
            [str(bad)],
            "it is tsc --showConfig's own output; re-run the task",
        )
    try:
        org = as_object(load_json(args.org.read_text(encoding="utf-8")), "the document")
        config, silenced = generate(org, claimed, domains, nursery, resolved)
    except UndecodableError as bad:
        return complain(
            f"{args.org} cannot be read",
            [str(bad)],
            "it is the belt's delivered config; restore it from the canon",
        )
    args.out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    named = ", ".join(sorted(claimed)) if claimed else "none"
    print(
        f"{NAME}: domains claimed: {named}; {silenced} nursery rule(s) off; "
        f"noNodejsModules off in {node} Node project(s)"
    )
    return 0


def main(argv: list[str]) -> int:
    """Generate one repository's biome config from stdin's manifest list.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the generated biome config")
    parser.add_argument("--org", type=Path, required=True)
    parser.add_argument("--domains", type=Path, required=True)
    parser.add_argument("--nursery", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, default=Path("biome.json"))
    parser.add_argument(
        "--tsconfig",
        nargs=2,
        action="append",
        metavar=("CONFIG", "RESOLVED"),
        help="a tracked tsconfig.json and the file holding its tsc --showConfig",
    )
    args = parser.parse_args(argv, namespace=Args())
    manifests = [Path(path) for path in stdin_paths()]
    return run(args, manifests)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

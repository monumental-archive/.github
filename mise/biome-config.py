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
"""

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

NAME = "biome-config"

# The only value a repository may write. "none" is the belt's to write and
# "recommended" would be a repo lowering a domain it did claim.
CLAIM = "all"

# Manifest sections a dependency can be declared in. `bundleDependencies`
# is deliberately absent: it is a list of names already required
# elsewhere, not a declaration of its own.
MANIFEST_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


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


def declaration_problems(document: object, identity: dict[str, list[str]]) -> list[str]:
    """Judge a repository's `biome.json` against what a repo may say.

    Returns:
        One line per refusal, empty when the declaration is admissible.

    """
    if not isinstance(document, dict):
        return ["the file must be a JSON object"]
    problems = [
        f'"{key}" — a repository states its domains and nothing else'
        for key in document
        if key not in {"$schema", "linter"}
    ]
    linter = document.get("linter", {})
    if not isinstance(linter, dict):
        return [*problems, '"linter" must be an object']
    problems += [
        f'"linter.{key}" — the org owns every rule; the repo owns only domains'
        for key in linter
        if key != "domains"
    ]
    domains = linter.get("domains", {})
    if not isinstance(domains, dict):
        return [*problems, '"linter.domains" must be an object']
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
    return problems


def declared_packages(manifests: list[Path]) -> dict[str, list[str]]:
    """Collect every dependency the repository's own manifests name.

    Returns:
        Package name mapped to the manifests declaring it.

    """
    found: dict[str, list[str]] = {}
    for manifest in manifests:
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if not isinstance(document, dict):
            continue
        for section in MANIFEST_SECTIONS:
            block = document.get(section, {})
            if isinstance(block, dict):
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


def generate(org: dict, claimed: set[str], domains: Domains) -> dict:
    """Merge the org's rules with the repository's identity.

    Returns:
        The config biome is actually run against.

    """
    config = json.loads(json.dumps(org))
    written = {d: CLAIM if d in claimed else "none" for d in domains.identity}
    written.update(dict.fromkeys(domains.org, CLAIM))
    config.setdefault("linter", {})["domains"] = written
    return config


def complain(headline: str, lines: list[str], remedy: str) -> int:
    """Print a refusal in the belt's shape.

    Returns:
        A process exit status.

    """
    print(f"{NAME}: {headline}", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)
    print(f"  {remedy}", file=sys.stderr)
    return 1


def run(args: argparse.Namespace, manifests: list[Path]) -> int:
    """Validate the declaration and write the generated config.

    Returns:
        A process exit status.

    """
    domains = read_domains(args.domains)
    org = json.loads(args.org.read_text(encoding="utf-8"))

    claimed: set[str] = set()
    if args.declaration.is_file():
        try:
            document = json.loads(args.declaration.read_text(encoding="utf-8"))
        except json.JSONDecodeError as bad:
            return complain(
                f"{args.declaration} is not valid JSON",
                [str(bad)],
                "it declares only this repository's biome domains",
            )
        problems = declaration_problems(document, domains.identity)
        if problems:
            return complain(
                f"{args.declaration} says more than a repository may say",
                problems,
                "the org's rules are delivered; this file carries linter.domains only",
            )
        claimed = set(document.get("linter", {}).get("domains", {}))

    unclaimed = omissions(claimed, domains.identity, declared_packages(manifests))
    if unclaimed:
        return complain(
            "a domain this repository HAS is not declared, so the belt would "
            "turn its rules off",
            unclaimed,
            f'add it to {args.declaration} as "<domain>": "{CLAIM}" — '
            "silence here lowers an org rule",
        )

    args.out.write_text(
        json.dumps(generate(org, claimed, domains), indent=2) + "\n",
        encoding="utf-8",
    )
    named = ", ".join(sorted(claimed)) if claimed else "none"
    print(f"{NAME}: domains claimed: {named}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Generate one repository's biome config from stdin's manifest list.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the generated biome config")
    parser.add_argument("--org", type=Path, required=True)
    parser.add_argument("--domains", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, default=Path("biome.json"))
    args = parser.parse_args(argv)
    manifests = [Path(line) for line in sys.stdin.read().split() if line]
    return run(args, manifests)


if __name__ == "__main__":
    sys.exit(main())

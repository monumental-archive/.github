#!/usr/bin/env python3
"""Judge a govulncheck scan against the org's written triage decisions.

Org canon — the Go half of #615. The Rust leg has always had a
written-decision exit: every `ignore` in deny.toml carries a reason
citing the VEX statement that records the judgment, so `audit:deny`
can go green on an advisory nobody can fix without anyone overriding
anything. The Go leg was scan-only, which held for exactly as long as
every advisory had a fix to take. GO-2026-5932 (x/crypto/openpgp,
unmaintained by design, `Fixed in: N/A`) is the case that showed the
gap: reachable, unfixable, and therefore a permanent red that burned
stele v0.17.0 and would have burned every release after it.

This is the same exit in the same shape — a listed decision, never a
silent skip:

- a finding is excused only by an OpenVEX `not_affected` /
  `false_positive` statement naming its exact module@version, and
  every excusal is printed with the statement it cites;
- an advisory with no covering statement is a hard red, unchanged;
- a statement naming a version this scan did not find excuses nothing
  and is named as stale — coverage is derived, never stored, so a
  version bump is a fresh judgment (security/vex/README.md).

The decisions are the canon's, resolved from ORG_CANON_DIR the way
`subject-budget.py` resolves the org's Renovate preset. Nothing here
is org-shaped: a belt with no decisions directory has an empty excusal
set and the scan's own verdict, which is what every adopter gets until
they write one.

WHY THIS PARSES JSON. `govulncheck -json` EXITS 0 WHETHER OR NOT IT
FOUND ANYTHING (measured, v1.7.0, on stele v0.17.0: 21 findings,
exit 0 — the text mode that exits 3 is a different renderer over the
same data). So the exit code cannot be the signal here and the verdict
is computed from the findings. The caller checks govulncheck's own
exit separately, because a scan that failed to build also exits
nonzero and emits a partial stream — config message only, measured on
a deliberately broken module.

WHAT COUNTS AS RED. govulncheck reports each advisory at the highest
level it reached: `called` (a vulnerable symbol is reachable from this
module's code), `imported` (the vulnerable package is imported, no
symbol reached), `required` (the module is in the graph, the package
is not imported). Only `called` reds, which is what the text mode's
exit 3 means, and what this reproduces — the other two are the
"but your code doesn't appear to call these" population govulncheck
prints and does not fail on.

Reads one scan's JSON on stdin; the module directory is argv[1], so a
multi-module repo names which module a report belongs to.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

# argv[0] plus the module directory the scan came from.
EXPECTED_ARGC = 2

# Where the canon keeps its triage decisions, the one directory the
# whole dependency track keys on (security/vex/README.md).
DECISIONS_SUBPATH = ("security", "vex")
DECISIONS_GLOB = "*.openvex.json"

# The two statuses that excuse. OpenVEX v0.2.0 defines `not_affected`,
# `affected`, `fixed` and `under_investigation`; `false_positive` is
# the spelling other VEX dialects use for the same judgment and is
# accepted so a decision written in one does not silently decide
# nothing. The other statuses are decisions too — they simply are not
# exits, and a finding under one stays red.
EXCUSING = ("not_affected", "false_positive")

# govulncheck's own exit code for "vulnerabilities found", preserved so
# the leg's contract with its callers is byte-identical to the scan-only
# task this replaces. A code this script owns — 1 — means it could not
# reach a verdict at all, which is never the same answer as "clean".
VULNERABILITIES_FOUND = 3

# A package URL's scheme and type, stripped to leave the namespace,
# name and version a decision joins on. Deliberately the same parse as
# stele's internal/vexjoin, which is the org's other consumer of these
# documents: two joins that disagree about what a statement covers
# would make one of them wrong about a signed judgment.
PURL_PREFIX = re.compile(r"^pkg:[A-Za-z0-9.+-]+/")

# How govulncheck ranks what it found, weakest first. The names are
# govulncheck's own vocabulary, kept so a reader can put this report
# beside the text one and see the same population.
LEVELS = ("required", "imported", "called")
CALLED = "called"


class Key(NamedTuple):
    """The exact triple a decision matches on.

    Advisory, package and version, compared as strings — no version
    ranges, no normalisation. A decision is a fact about one
    package@version, so a graph that moved off that version matches
    nothing and asks for a fresh judgment.
    """

    advisory: str
    package: str
    version: str

    def render(self) -> str:
        """Name the triple the way both halves of the report name it.

        Returns:
            The advisory and the package it was found in.

        """
        return f"{self.advisory}  {self.package}@{self.version}"


class Decision(NamedTuple):
    """One parsed statement: what it covers and what it judged.

    `purl` is carried verbatim rather than reassembled from the parsed
    name and version, so a citation quotes the product the human wrote.
    """

    key: Key
    origin: str
    purl: str
    status: str
    justification: str
    impact: str
    action: str
    decided: str


class Found(NamedTuple):
    """One advisory as this scan found it, in one module."""

    key: Key
    level: str
    fixed: str
    summary: str
    url: str
    trace: list[str]
    position: str


def decisions_root() -> Path | None:
    """Resolve the canon's decisions directory from the belt's env.

    Returns:
        The directory, or None when the belt did not arrive.

    """
    canon = os.environ.get("ORG_CANON_DIR", "")
    if not canon:
        return None
    return Path(canon).joinpath(*DECISIONS_SUBPATH)


def parse_purl(purl: str) -> tuple[str, str] | None:
    """Split a product package URL into the name and version it covers.

    The name is the NAMESPACE AND NAME TOGETHER: pkg:golang/example.com/dep
    names the module example.com/dep, and keying it as "dep" would decide
    a vulnerability in some other package that happens to end the same
    way. Scanners report the joined form, so this is also the spelling a
    finding arrives in — govulncheck names the module path, and the org's
    published SBOMs purl it identically (`pkg:golang/golang.org/x/crypto@v0.54.0`,
    read from stele 0.17.1's SPDX document).

    Returns:
        The name and version, or None when the product cannot join.

    """
    rest = PURL_PREFIX.sub("", purl, count=1)
    if rest == purl:
        return None  # no scheme and type: not a package URL
    # Qualifiers and subpath are not part of the identity.
    rest = rest.split("?", 1)[0].split("#", 1)[0]
    # The LAST @ separates the version: an npm scoped name opens with
    # one, so cutting at the first would split the name in half.
    at = rest.rfind("@")
    if at <= 0 or at == len(rest) - 1:
        return None  # unversioned products cannot join
    return unquote(rest[:at]), rest[at + 1 :]


def text_of(value: object) -> str:
    """Read a JSON field that must be a string, or nothing.

    Returns:
        The string, or "" for any other shape including null.

    """
    return value if isinstance(value, str) else ""


def statement_decisions(
    doc: dict,
    origin: str,
    errors: list[str],
) -> list[Decision]:
    """Parse one OpenVEX document into the decisions it records.

    A foreign format with a spec, decoded leniently — but a statement
    missing what the join needs is an error, never a silent skip: a
    decision that parses as nothing decides nothing, quietly. The same
    refusals as stele's vexjoin, so a document valid to one half of the
    org's machinery is valid to the other.

    Returns:
        Every joinable decision the document carries.

    """
    out: list[Decision] = []
    statements = doc.get("statements")
    if not isinstance(statements, list):
        errors.append(f"{origin}: carries no statements array")
        return out
    doc_time = text_of(doc.get("timestamp"))
    for index, stmt in enumerate(statements):
        if not isinstance(stmt, dict):
            errors.append(f"{origin}: statement {index} is not an object")
            continue
        vuln = stmt.get("vulnerability")
        name = text_of(vuln.get("name")) if isinstance(vuln, dict) else ""
        if not name:
            errors.append(f"{origin}: statement {index} names no vulnerability")
            continue
        status = text_of(stmt.get("status"))
        if not status:
            errors.append(
                f"{origin}: statement {index} carries no status — a decision "
                f"that decides nothing is not a decision",
            )
            continue
        # A statement dates itself where the format allows it and falls
        # back to its document. Absent from both is refused: a judgment
        # with no moment cannot be cited honestly, and substituting a
        # clock would invent one.
        decided = text_of(stmt.get("timestamp")) or doc_time
        if not decided:
            errors.append(f"{origin}: statement {index} carries no timestamp")
            continue
        out.extend(
            _products(stmt, name, status, decided, origin),
        )
    return out


def _products(
    stmt: dict,
    name: str,
    status: str,
    decided: str,
    origin: str,
) -> list[Decision]:
    """Expand one statement's products into decisions.

    Returns:
        One decision per product that is a versioned package URL.

    """
    out: list[Decision] = []
    products = stmt.get("products")
    if not isinstance(products, list):
        return out
    for product in products:
        if not isinstance(product, dict):
            continue
        purl = text_of(product.get("@id"))
        parsed = parse_purl(purl)
        if parsed is None:
            continue  # a product that is not a versioned purl cannot join
        out.append(
            Decision(
                key=Key(advisory=name, package=parsed[0], version=parsed[1]),
                origin=origin,
                purl=purl,
                status=status,
                justification=text_of(stmt.get("justification")),
                impact=text_of(stmt.get("impact_statement")),
                action=text_of(stmt.get("action_statement")),
                decided=decided,
            ),
        )
    return out


def read_decisions(root: Path, errors: list[str]) -> dict[Key, Decision]:
    """Read every decision the canon records.

    An absent directory is an empty set, which is the adopter's case and
    decides nothing. An unreadable document in a directory that exists is
    an error: the belt cannot tell a malformed judgment from an absent
    one, and guessing "absent" is how a decision goes missing silently.

    Returns:
        Every decision, keyed by the triple it covers.

    """
    held: dict[Key, Decision] = {}
    if not root.is_dir():
        return held
    for path in sorted(root.glob(DECISIONS_GLOB)):
        origin = path.name
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{origin}: unreadable as OpenVEX ({exc})")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{origin}: is not an OpenVEX document")
            continue
        for decision in statement_decisions(doc, origin, errors):
            prior = held.get(decision.key)
            # Two decisions for one triple is a contradiction to
            # surface, never a race the directory order settles. The
            # human retires one; this code picks neither.
            if prior is not None:
                errors.append(
                    f"{prior.origin} and {origin} both decide "
                    f"{decision.key.render()} — one finding, one decision",
                )
                continue
            held[decision.key] = decision
    return held


def messages(text: str, errors: list[str]) -> list[dict]:
    """Decode govulncheck's stream of concatenated JSON objects.

    Returns:
        Every object in the stream, in the order it was emitted.

    """
    decoder = json.JSONDecoder()
    out: list[dict] = []
    index, end = 0, len(text)
    while index < end:
        while index < end and text[index].isspace():
            index += 1
        if index >= end:
            break
        try:
            obj, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            errors.append(f"govulncheck output is not decodable JSON ({exc})")
            return out
        if isinstance(obj, dict):
            out.append(obj)
    return out


def short(frame: dict) -> str:
    """Name a call-stack frame the way govulncheck's own report does.

    Returns:
        The package's last segment and the function, or "" for neither.

    """
    function = text_of(frame.get("function"))
    if not function:
        return ""
    package = text_of(frame.get("package"))
    tail = package.rsplit("/", 1)[-1] if package else ""
    return f"{tail}.{function}" if tail else function


def position(frame: dict) -> str:
    """Render a frame's source position.

    Returns:
        file:line:column, or "" when the frame carries no position.

    """
    where = frame.get("position")
    if not isinstance(where, dict):
        return ""
    name = text_of(where.get("filename"))
    if not name:
        return ""
    return f"{name}:{where.get('line')}:{where.get('column')}"


def level_of(frame: dict) -> str:
    """Rank one finding by how far into the code it reached.

    Returns:
        One of LEVELS.

    """
    if text_of(frame.get("function")):
        return CALLED
    return "imported" if text_of(frame.get("package")) else "required"


def scan(msgs: list[dict]) -> tuple[dict[Key, Found], dict]:
    """Fold a scan's messages into one record per advisory and module.

    An advisory is reported at the highest level it reached, which is
    what govulncheck's text renderer does and therefore what "unchanged
    behavior" means.

    Returns:
        The found set keyed by triple, and the scanner's config message.

    """
    config: dict = {}
    osvs: dict[str, dict] = {}
    found: dict[Key, Found] = {}
    for msg in msgs:
        if isinstance(msg.get("config"), dict):
            config = msg["config"]
        if isinstance(msg.get("osv"), dict):
            osvs[text_of(msg["osv"].get("id"))] = msg["osv"]
        finding = msg.get("finding")
        if isinstance(finding, dict):
            _fold(finding, found)
    return {key: _describe(rec, osvs) for key, rec in found.items()}, config


def _fold(finding: dict, found: dict[Key, Found]) -> None:
    """Merge one finding into the record for its advisory and module."""
    trace = finding.get("trace")
    if not isinstance(trace, list) or not trace or not isinstance(trace[0], dict):
        return
    vulnerable = trace[0]
    key = Key(
        advisory=text_of(finding.get("osv")),
        package=text_of(vulnerable.get("module")),
        version=text_of(vulnerable.get("version")),
    )
    if not key.advisory or not key.package:
        return
    level = level_of(vulnerable)
    prior = found.get(key)
    # The advisory's fix is a fact about the advisory, not about the
    # level a particular finding reached, so it survives being outranked:
    # govulncheck states it on the module-level finding, which the
    # symbol-level one then replaces.
    fixed = text_of(finding.get("fixed_version")) or (prior.fixed if prior else "")
    if prior is not None and LEVELS.index(level) <= LEVELS.index(prior.level):
        found[key] = prior._replace(fixed=fixed)
        return
    frames = [f for f in trace if isinstance(f, dict)]
    found[key] = Found(
        key=key,
        level=level,
        fixed=fixed,
        summary="",
        url="",
        # The chain reads entry point first, the direction a reader
        # follows it: govulncheck emits the vulnerable symbol first.
        trace=[name for name in (short(f) for f in reversed(frames)) if name],
        position=position(frames[-1]) if frames else "",
    )


def _describe(rec: Found, osvs: dict[str, dict]) -> Found:
    """Attach the advisory's own words to a found record.

    Returns:
        The record with the database's summary and link filled in.

    """
    osv = osvs.get(rec.key.advisory, {})
    specific = osv.get("database_specific")
    url = text_of(specific.get("url")) if isinstance(specific, dict) else ""
    return rec._replace(summary=text_of(osv.get("summary")), url=url)


def excusals(
    found: dict[Key, Found],
    held: dict[Key, Decision],
) -> tuple[dict[Key, Decision], list[Decision]]:
    """Join the scan against the decisions.

    Staleness is scoped to advisories THIS scan found. A canon decision
    is org-wide, so one repository's module graph cannot retire it —
    that judgment belongs to `audit:blast-radius`, which walks every
    published SBOM. What belongs here is the case the join is for: an
    advisory is live, a decision for it exists, and it names a version
    this graph no longer carries, so it excuses nothing and the finding
    is undecided.

    Returns:
        The decisions that excuse a called finding, and the stale ones.

    """
    excused = {
        key: held[key]
        for key, rec in found.items()
        if rec.level == CALLED and key in held and held[key].status in EXCUSING
    }
    live = {key.advisory for key in found}
    covered = {(key.advisory, key.package, key.version) for key in found}
    stale = [
        decision
        for key, decision in sorted(held.items())
        if decision.status in EXCUSING
        and key.advisory in live
        and (key.advisory, key.package, key.version) not in covered
    ]
    return excused, stale


def cite(decision: Decision, indent: str) -> None:
    """Print the statement a decision is excused by, in full."""
    why = decision.justification or "no justification"
    print(f"{indent}status:   {decision.status} ({why})")
    print(f"{indent}cited:    {decision.origin}")
    print(f"{indent}product:  {decision.purl}")
    print(f"{indent}decided:  {decision.decided}")
    for label, words in (("impact", decision.impact), ("action", decision.action)):
        if words:
            print(f"{indent}{label}:   {words}")


def show_population(found: dict[Key, Found], held: dict[Key, Decision]) -> None:
    """Print every advisory the scan found, red-bearing ones first."""
    for level in reversed(LEVELS):
        at_level = [rec for _, rec in sorted(found.items()) if rec.level == level]
        if not at_level:
            continue
        gate = "the gate" if level == CALLED else "informational, never the gate"
        print(f"\n  {level} ({gate}):")
        for rec in at_level:
            fix = f"fixed in {rec.fixed}" if rec.fixed else "no fix available"
            print(f"    {rec.key.render()}  {fix}")
            if rec.summary:
                print(f"        {rec.summary}")
            if rec.url:
                print(f"        {rec.url}")
            if rec.level == CALLED and rec.trace:
                print(f"        reached by {' -> '.join(rec.trace)}")
                if rec.position:
                    print(f"        at {rec.position}")
            decision = held.get(rec.key)
            if decision is not None and decision.status not in EXCUSING:
                print(
                    f"        decided {decision.status} in {decision.origin} — "
                    f"that status does not excuse",
                )


def show_excusals(
    excused: dict[Key, Decision],
    stale: list[Decision],
    root: Path,
    count: int,
) -> None:
    """Print the excusal set, empty or not, and any stale statement."""
    held = f"{count} read from {root}"
    if not excused:
        print(f"\n  excused by written decision: none ({held})")
    else:
        print(f"\n  excused by written decision ({len(excused)} of {held}):")
        for key, decision in sorted(excused.items()):
            print(f"    {key.render()}")
            cite(decision, " " * 8)
    if not stale:
        return
    print("\n  stale — names a version this scan did not find, so it excuses nothing:")
    for decision in stale:
        print(f"    {decision.key.render()}  ({decision.origin})")
    print(
        "        Coverage is derived, never stored: a version bump is a fresh\n"
        "        judgment, not a decision to re-point. Decide the version the\n"
        "        scan names, and retire the statement above if nothing ships it.",
    )


def remedy(unexcused: list[Found], root: Path, label: str) -> None:
    """Print why the gate is red and what a written exit would be."""
    # The population above went to stdout, which is block-buffered when a
    # CI job redirects it while stderr is not. Without this the remedy
    # lands ahead of the findings it is the remedy for.
    sys.stdout.flush()
    count = len(unexcused)
    noun = "advisory" if count == 1 else "advisories"
    print(
        f"\naudit:go-vulns: {label}: {count} called {noun} with no written decision",
        file=sys.stderr,
    )
    for rec in unexcused:
        print(f"  {rec.key.render()}", file=sys.stderr)
    print(
        "\n  Take the fix where there is one — that is the whole remedy for an\n"
        "  advisory with a fixed version. Where there is none, the exit is a\n"
        "  written decision, never an override:\n\n"
        f'    vexctl create --product "pkg:golang/<module>@<version>" \\\n'
        f'      --vuln "<advisory>" --status not_affected \\\n'
        f"      --justification <justification> \\\n"
        f"      --file {root}/<advisory>.openvex.json\n\n"
        "  The product names the dependency, never a release tag, and the\n"
        "  version is the one above verbatim. No not_affected is written\n"
        "  without the blast-radius query behind it: a signed wrong\n"
        "  not_affected suppresses consumers' findings on the org's word.",
        file=sys.stderr,
    )


def main() -> int:
    """Judge one module's scan.

    Returns:
        0 when nothing called is undecided, 3 when something is, and 1
        when this could not reach a verdict at all.

    """
    if len(sys.argv) != EXPECTED_ARGC:
        print("usage: go-vulns.py <module-dir> < scan.json", file=sys.stderr)
        return 1
    label = sys.argv[1]
    root = decisions_root()
    if root is None:
        print(
            f"audit:go-vulns: {label}: ORG_CANON_DIR is unset, so the org's "
            f"triage decisions cannot be read; lint:belt-available says why",
            file=sys.stderr,
        )
        return 1
    errors: list[str] = []
    msgs = messages(sys.stdin.read(), errors)
    found, config = scan(msgs)
    # A stream with no config message is a scan that did not happen —
    # truncated, empty, or from something that is not govulncheck. The
    # verdict would be "nothing called, clean", which is the
    # vacuous-success failure this leg exists to not have.
    if not config:
        errors.append("govulncheck emitted no config message — the scan did not run")
    held = read_decisions(root, errors)
    if errors:
        for line in errors:
            print(f"audit:go-vulns: {label}: {line}", file=sys.stderr)
        return 1

    print(
        f"audit:go-vulns: {label}: {config.get('scanner_name')} "
        f"{config.get('scanner_version')}, db {config.get('db')} "
        f"({config.get('db_last_modified')}), scan level "
        f"{config.get('scan_level')}",
    )
    excused, stale = excusals(found, held)
    counts = ", ".join(
        f"{sum(1 for rec in found.values() if rec.level == lv)} {lv}"
        for lv in reversed(LEVELS)
    )
    print(f"audit:go-vulns: {label}: {len(found)} advisories in scope — {counts}")
    show_population(found, held)
    show_excusals(excused, stale, root, len(held))

    unexcused = [
        rec
        for _, rec in sorted(found.items())
        if rec.level == CALLED and rec.key not in excused
    ]
    if unexcused:
        remedy(unexcused, root, label)
        return VULNERABILITIES_FOUND
    called = sum(1 for rec in found.values() if rec.level == CALLED)
    verdict = (
        f"every called advisory ({called}) carries a written decision"
        if called
        else "no reachable vulnerabilities"
    )
    print(f"\naudit:go-vulns: {label}: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Compare a published Best Practices entry with the file it is answered from.

Org canon (#1536). A repository's answers live in its own
`.bestpractices.json`, and bestpractices.dev reads that file only when
the form is re-entered. Nothing compared the two, so the published
entry sat behind the measured answers: on 2026-11-18 the canon's entry
still read Unmet on six criteria its file answered Met.

THE FILE IS THE SOURCE. A difference is fixed by re-entering the form
from the file (docs/best-practices.md), never by editing the file to
match the site, so every finding names that step.

Two subcommands, both stdlib:

    project README          print the project id the README's live
                            bestpractices.dev shield names; nothing
                            when it carries none
    compare FILE REPO ID    the entry's JSON on stdin; exit 1 naming
                            each criterion whose status or
                            justification differs from FILE

The site renders Baseline criteria under their display names
(`OSPS-AC-01.01_status`) while the file carries the input names the
app accepts (`osps_ac_01_01_status`, #379), so a file key is looked up
under its display name. Every `_status` and `_justification` key in
the file is compared; nothing the file does not answer is.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# A live shield on the entry: the metal badge or the Baseline one.
SHIELD = re.compile(
    r"https://(?:www\.)?bestpractices\.dev/projects/(\d+)/(?:badge|baseline)\b"
)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
BASELINE = re.compile(r"osps_([a-z]{2})_(\d{2})_(\d{2})_(status|justification)")
FIELD = re.compile(r"(.+)_(status|justification)")
STEP = (
    "  re-enter the form from the file (docs/best-practices.md); the file is\n"
    "  the source, never edit it to match the site (#1536)"
)


def project(readme: str) -> tuple[str | None, str | None]:
    """Find the entry a README's live shields name.

    Commented-out shields are not live, so comments are cut first.

    Returns:
        (id, None) for one entry, (None, None) for none, and
        (None, finding) when the shields name more than one entry.

    """
    ids = sorted(set(SHIELD.findall(COMMENT.sub("", readme))), key=int)
    if len(ids) > 1:
        return None, f"README shields name more than one entry: {', '.join(ids)}"
    return (ids[0] if ids else None), None


def site_key(key: str) -> str:
    """Return the name the site's JSON carries a file key under.

    Returns:
        `OSPS-XX-NN.MM_<field>` for a Baseline key, else the key itself.

    """
    found = BASELINE.fullmatch(key)
    if found is None:
        return key
    family, control, item, field = found.groups()
    return f"OSPS-{family.upper()}-{control}.{item}_{field}"


def shown(value: object) -> str:
    """Render a status for a finding.

    Returns:
        The value as JSON would write it, or `absent`.

    """
    return "absent" if value is None else json.dumps(value)


def drift(answers: dict, entry: dict) -> list[str]:
    """List the criteria whose published answer differs from the file's.

    A justification the site holds as null equals an empty one.

    Returns:
        One line per criterion, in the file's order; empty when they agree.

    """
    found: dict[str, list[str]] = {}
    for key, want in answers.items():
        match = FIELD.fullmatch(key)
        if match is None:
            continue
        criterion, field = match.groups()
        have = entry.get(site_key(key))
        if field == "justification":
            if (have or "") != (want or ""):
                found.setdefault(criterion, []).append("justification differs")
        elif have != want:
            found.setdefault(criterion, []).append(
                f"status {shown(have)} on the site, {shown(want)} in the file"
            )
    return [f"{criterion}: {'; '.join(why)}" for criterion, why in found.items()]


def load(text: str, what: str) -> dict:
    """Parse one JSON object.

    Returns:
        The object.

    Raises:
        ValueError: the text is not JSON.
        TypeError: the JSON is not an object.

    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        message = f"{what} is not JSON: {err}"
        raise ValueError(message) from err
    if not isinstance(data, dict):
        message = f"{what} is not a JSON object"
        raise TypeError(message)
    return data


def compare(path: str, repo: str, ident: str, entry_text: str) -> int:
    """Judge the entry on stdin against the file.

    Returns:
        The process exit status.

    """
    label = f"audit:bestpractices: {repo} (bestpractices.dev project {ident})"
    try:
        answers = load(Path(path).read_text(encoding="utf-8"), path)
        entry = load(entry_text, "the site's answer")
    except (ValueError, TypeError) as err:
        print(f"{label}: {err}", file=sys.stderr)
        return 1
    lines = drift(answers, entry)
    if not lines:
        print(f"{label}: the published entry matches {path}")
        return 0
    print(f"{label}: the published entry differs from {path}:", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)
    print(STEP, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Dispatch a subcommand.

    Returns:
        The process exit status.

    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("project", help="the entry a README's shields name")
    one.add_argument("readme", help="the tracked README, or empty for none")
    two = sub.add_parser("compare", help="the entry on stdin against the file")
    two.add_argument("file", help="the repository's .bestpractices.json")
    two.add_argument("repo", help="the repository, for the findings")
    two.add_argument("id", help="the bestpractices.dev project id")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare(args.file, args.repo, args.id, sys.stdin.read())
    text = Path(args.readme).read_text(encoding="utf-8") if args.readme else ""
    ident, problem = project(text)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    if ident is not None:
        print(ident)
    return 0


if __name__ == "__main__":
    sys.exit(main())

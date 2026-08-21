#!/usr/bin/env python3
"""Hold a repository's tsconfig.json to the org's TypeScript level.

Org canon — the mechanism half of #699. `lint:types` passes every dial on
the COMMAND LINE, where a compiler flag beats the same key in a repo's
config even under `-p`, and the scaffold read that as licence to carry no
strictness settings at all: "a copy of those settings here would be
either duplication or a lie".

That is true of `tsc` and false of every other type-aware tool, because
those tools read `tsconfig.json` and never see the belt's command line.
Measured on monumental-archive, one variable changed and nothing else:
its own config gave `eslint --max-warnings 0` **1** error, and the same
tree stripped per the scaffold's instruction gave **936** — 935 new
findings from typescript-eslint reading a weaker program. biome's
`types` and `project` domains resolve the same program and move the same
way.

So the keys are neither duplication nor a lie. They are the only
statement of the level the second reader can see, and the belt's command
line is the redundant copy — for `tsc` alone. The org already accepts
exactly this arrangement and says so about `.editorconfig`: a cap stated
there never fights a belt formatter, it states the same fact where every
editor can read it.

This makes that advisory rather than aspirational. For every dial in
`mise/tsc-flags.txt` — the same file `lint:types` and `lint:tsc-flags`
read, never a second list — a repository's config must state it and must
not state it WEAKER. Absent fails, because absent is the defect. Stricter
stays legal and is never inspected: a repo may name dials the org does
not, and the hazard here is one-directional, which is the
`lint:python-target` shape.

The config being judged is the one `tsc --showConfig` resolves, not the
file's own text. That is deliberate — it is TypeScript's own parser, so
comments, trailing commas and `extends` all behave exactly as they do for
the compiler, and a repository that states the dials once in a root
config and inherits them everywhere passes without stating them twice.
Measured: `--showConfig` does not materialise a default for a dial the
config never mentions, which is what makes "absent" detectable at all.
"""

import argparse
import json
import sys
from pathlib import Path

NAME = "tsconfig-dials"

# A bare flag turns its dial on; `--flag false` is how tsc-flags.txt
# writes the dials whose permissive default is `undefined`. Those are the
# only two shapes the file uses, and a third one must stop the check
# rather than be guessed at — a dial nobody compared is a dial nobody
# enforced.
LITERALS = {"true": True, "false": False}


def dials(flags_file: Path) -> tuple[dict[str, bool], list[str]]:
    """Read the org's level out of the file that already defines it.

    Returns:
        Each dial mapped to the value a conforming config must state,
        and one line per flag whose shape this cannot compare.

    """
    required: dict[str, bool] = {}
    unreadable: list[str] = []
    for raw in flags_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        flag, *rest = line.split()
        if not flag.startswith("--"):
            unreadable.append(f"{line} — not a flag")
            continue
        key = flag.removeprefix("--")
        if not rest:
            required[key] = True
        elif len(rest) == 1 and rest[0] in LITERALS:
            required[key] = LITERALS[rest[0]]
        else:
            unreadable.append(
                f"{line} — a value this check cannot compare; teach it the "
                "shape rather than letting the dial go unenforced",
            )
    return required, unreadable


def judge(stated: dict, required: dict[str, bool]) -> list[str]:
    """Compare one resolved config against the org's level.

    Returns:
        One line per dial that is absent or weaker, in the file's order.

    """
    findings = []
    for key, want in required.items():
        if key not in stated:
            findings.append(
                f'"{key}" is absent — the belt forces it for tsc, but every '
                "other type-aware tool reads this file and would not",
            )
        elif stated[key] != want:
            findings.append(
                f'"{key}": {json.dumps(stated[key])} is weaker than the org\'s '
                f"{json.dumps(want)}",
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Judge one resolved tsconfig handed over on stdin.

    Returns:
        A process exit status.

    """
    parser = argparse.ArgumentParser(description="the org's TypeScript level")
    parser.add_argument("--flags", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    required, unreadable = dials(args.flags)
    if unreadable:
        print(
            f"{NAME}: {args.flags} has a flag this check cannot read",
            file=sys.stderr,
        )
        for line in unreadable:
            print(f"  {line}", file=sys.stderr)
        return 1
    if not required:
        print(f"{NAME}: {args.flags} names no dial at all", file=sys.stderr)
        return 1

    try:
        resolved = json.loads(sys.stdin.read())
    except json.JSONDecodeError as bad:
        print(f"{NAME}: {args.name}: tsc did not resolve to JSON", file=sys.stderr)
        print(f"  {bad}", file=sys.stderr)
        return 1

    stated = resolved.get("compilerOptions", {})
    if not isinstance(stated, dict):
        print(f"{NAME}: {args.name}: compilerOptions is not an object", file=sys.stderr)
        return 1

    findings = judge(stated, required)
    if findings:
        print(f"{NAME}: {args.name} does not state the org's level", file=sys.stderr)
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        print(
            "  copy the dials from scaffold/tsconfig.json, or extend a config "
            "that has them —",
            file=sys.stderr,
        )
        print(
            "  stricter than the org is always allowed and is never inspected",
            file=sys.stderr,
        )
        return 1
    print(f"{NAME}: {args.name}: {len(required)} dial(s) at the org's level")
    return 0


if __name__ == "__main__":
    sys.exit(main())

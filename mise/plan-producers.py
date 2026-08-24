#!/usr/bin/env python3
"""Join the policy's planned SBOM obligations to the legs that produce them.

Org canon — the mechanism half of #833. `slsa/assert-policy.json` declares,
per evidence class, the inventory documents a release owes:
`assetPrefixes` entries carrying `"planned": true`. The build legs are what
produce them, each emitting an inventory plan naming its class and the
document it will become. Nothing joined the two before a release ran.

`rust-crate` declared `sbom-cargo-` from machinery 1.42.0 and no leg ever
emitted a plan for it. Forty canon releases went by; the first repository
to declare the class in anger (edtf v1.3.1) refused pre-publish, correctly,
in `stele assert plans`. The obligation table and the emitting legs are two
committed files with one inconsistent row between them — no runner and no
release was needed to see it, only something that looked.

This is that look, and it is bidirectional, because the defect reads three
ways:

  * a class declares a planned prefix and nothing produces it (#833);
  * a leg emits plans under a prefix its class never declared;
  * one side is renamed and the other is not.

WHY A MARKER RATHER THAN A DERIVATION. Two of the five producers state
their class inline (`class: "wasm-npm"` in the jq that writes the plan);
two hand it to `release/rust-build.sh` as `PLAN_CLASS`; and the fifth —
`oci-image-crate` — deliberately does NOT state its class in the leg at
all. #843 put that name in exactly one place, `publish.yml`'s guard, so the
leg is GIVEN the class it plans under and a leg that picked its own name
would be that rule stated twice. Deriving the class from each leg would
therefore mean tracing an input through a call site through a job output —
a small compiler, and a fragile one.

So the class↔prefix pair is DECLARED, in a marker beside the place the
class name is written:

    # plan-producer: <class> <prefix>

and the declaration is held to the two facts around it. Against the policy:
the class must exist and must declare that prefix as planned, and every
planned obligation must have a marker. Against the workflows: the set of
prefixes markers claim must equal the set of prefixes the legs actually
emit, so a marker cannot outlive its producer and a producer cannot arrive
without one. A marker is only ever a name for something both files already
say.

Deterministic: two sets of tracked files, no network, no runner.
"""

import argparse
import json
import re
import sys
from pathlib import Path

NAME = "plan-producers"

# The declaration this check joins on. A line comment, so it reads the same
# in a YAML comment and inside a `run:` block's bash.
MARKER = re.compile(r"^\s*#\s*plan-producer:(?P<rest>.*)$")

# The two ways a leg states the document prefix it will emit under. Both
# are the real emission, never a comment about one: the env var
# `release/rust-build.sh` reads, and the jq that writes `plan.json`.
ENV_PREFIX = re.compile(r"^\s*PLAN_DOC_PREFIX:\s*(?P<prefix>\S+)\s*$")
JQ_DOC = re.compile(r"""doc:\s*\(\s*(?P<quote>["'])(?P<prefix>[^"']*)(?P=quote)""")

# A leg that hands its plan to the sbom job. `pattern:` is the DOWNLOAD
# side and deliberately not matched — publish.yml fetches `sbom-plan-*`
# and emits nothing.
PLAN_UPLOAD = re.compile(r"^\s*name:\s*sbom-plan-")

# A marker states a class and a prefix, and nothing else: a line carrying
# more or fewer words is a marker nobody can read.
MARKER_WORDS = 2


def obligations(policy: dict) -> set[tuple[str, str]]:
    """Read the planned obligations out of the assert policy.

    Args:
        policy: the decoded `slsa/assert-policy.json`.

    Returns:
        Every (class, prefix) pair the policy declares as planned. Only
        `planned` entries: an `assetPrefixes` row without it is an asset
        some other leg attaches, not a document a plan produces.

    """
    out = set()
    classes = policy.get("evidence", {}).get("classes", {})
    for name, body in classes.items():
        for entry in body.get("assetPrefixes", []):
            if entry.get("planned") and entry.get("prefix"):
                out.add((name, entry["prefix"]))
    return out


def markers(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Read the plan-producer markers out of one file.

    Args:
        text: the file's contents.

    Returns:
        The (class, prefix) pairs declared, and one complaint per marker
        line that does not carry exactly those two words. A marker nobody
        can read is a producer nobody joined, so it is refused rather than
        skipped.

    """
    pairs: list[tuple[str, str]] = []
    malformed: list[str] = []
    for line in text.splitlines():
        found = MARKER.match(line)
        if not found:
            continue
        words = found.group("rest").split()
        if len(words) != MARKER_WORDS:
            malformed.append(line.strip())
            continue
        pairs.append((words[0], words[1]))
    return pairs, malformed


def emitted(text: str) -> set[str]:
    """Read the document prefixes one file's legs actually emit under.

    Args:
        text: the file's contents.

    Returns:
        Every prefix named by the two emission idioms.

    """
    lines = text.splitlines()
    env = {
        found.group("prefix").strip("\"'")
        for found in (ENV_PREFIX.match(line) for line in lines)
        if found
    }
    jq = {found.group("prefix") for line in lines for found in JQ_DOC.finditer(line)}
    return env | jq


def uploads_plan(text: str) -> bool:
    """Report whether the file uploads an inventory plan artifact.

    Args:
        text: the file's contents.

    Returns:
        True when some step ships an `sbom-plan-*` artifact. The
        fail-closed half: such a file must state a prefix this check can
        read, or a new producer arrives invisible to the join.

    """
    return any(PLAN_UPLOAD.match(line) for line in text.splitlines())


def strip_markers(text: str) -> str:
    """Drop the marker lines, leaving what the file says on its own.

    Args:
        text: the file's contents.

    Returns:
        The same text without its marker lines, so a marker cannot be its
        own evidence that the class belongs to the file it sits in.

    """
    return "\n".join(line for line in text.splitlines() if not MARKER.match(line))


class Claims:
    """What the markers, once judged against the policy, actually claim."""

    def __init__(self) -> None:
        """Start with nothing claimed."""
        self.pairs: set[tuple[str, str]] = set()
        self.prefixes: dict[str, str] = {}

    def add(self, path: str, klass: str, prefix: str) -> None:
        """Record one surviving marker.

        Args:
            path: the file the marker sits in.
            klass: the evidence class it declares a producer for.
            prefix: the document prefix that producer emits under.

        """
        self.pairs.add((klass, prefix))
        self.prefixes.setdefault(prefix, path)


def claims(policy: dict, files: dict[str, str]) -> tuple[Claims, list[str]]:
    """Read the markers and hold each to the policy it names.

    Args:
        policy: the decoded assert policy.
        files: workflow path to contents.

    Returns:
        What the surviving markers claim, and one finding per marker the
        policy does not bear out. A marker that fails here claims
        nothing: it must not go on to satisfy the obligation it misnames.

    """
    findings: list[str] = []
    declared = obligations(policy)
    classes = policy.get("evidence", {}).get("classes", {})
    claimed = Claims()

    for path in sorted(files):
        text = files[path]
        pairs, malformed = markers(text)
        findings.extend(
            f"{path}: marker {line!r} is not `# plan-producer: <class> <prefix>`"
            for line in malformed
        )
        body = strip_markers(text)
        for klass, prefix in pairs:
            if klass not in classes:
                findings.append(
                    f"{path}: marker names class {klass!r}, which "
                    f"slsa/assert-policy.json does not declare",
                )
            elif (klass, prefix) not in declared:
                findings.append(
                    f"{path}: marker claims prefix {prefix!r} for class "
                    f"{klass!r}, which declares no such planned obligation",
                )
            elif klass not in body:
                findings.append(
                    f"{path}: marker names class {klass!r}, which this file "
                    f"never states — the marker belongs where the class "
                    f"name is written",
                )
            else:
                claimed.add(path, klass, prefix)

    return claimed, findings


def production(files: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Read the prefixes the legs actually emit under.

    Args:
        files: workflow path to contents.

    Returns:
        Each emitted prefix mapped to the file emitting it, and one
        finding per file that ships a plan whose prefix cannot be read.

    """
    findings: list[str] = []
    produced: dict[str, str] = {}

    for path in sorted(files):
        text = files[path]
        prefixes = emitted(text)
        for prefix in sorted(prefixes):
            produced.setdefault(prefix, path)
        if uploads_plan(text) and not prefixes:
            findings.append(
                f"{path}: uploads an sbom-plan artifact and states no "
                f"document prefix this check can read — state it as "
                f"`PLAN_DOC_PREFIX:` or as the leading literal of `doc:`",
            )

    return produced, findings


def judge(policy: dict, files: dict[str, str]) -> list[str]:
    """Join the policy's planned obligations to the workflows' producers.

    Args:
        policy: the decoded assert policy.
        files: workflow path to contents.

    Returns:
        One finding per broken join, in a stable order.

    """
    claimed, findings = claims(policy, files)
    produced, upload_findings = production(files)

    findings.extend(
        f"class {klass!r} declares the planned obligation {prefix!r} and no "
        f"workflow declares itself its producer — a release declaring the "
        f"class refuses pre-publish in `stele assert plans`"
        for klass, prefix in sorted(obligations(policy) - claimed.pairs)
    )
    findings.extend(upload_findings)
    findings.extend(
        f"{produced[prefix]}: emits plans under {prefix!r} and no "
        f"`# plan-producer:` marker claims it for a class"
        for prefix in sorted(set(produced) - set(claimed.prefixes))
    )
    findings.extend(
        f"{claimed.prefixes[prefix]}: a marker claims {prefix!r} and no "
        f"workflow emits a document under it — the producer was renamed "
        f"or removed on one side only"
        for prefix in sorted(set(claimed.prefixes) - set(produced))
    )

    return findings


def main() -> int:
    """Run the check over the tracked policy and workflows.

    Returns:
        0 when every planned obligation has its producer and every producer
        its obligation, 1 otherwise.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("workflows", nargs="*", type=Path)
    args = parser.parse_args()

    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as bad:
        print(f"{NAME}: {args.policy} does not read as JSON", file=sys.stderr)
        print(f"  {bad}", file=sys.stderr)
        return 1

    files = {}
    for path in args.workflows:
        try:
            files[str(path)] = path.read_text(encoding="utf-8")
        except OSError as bad:
            print(f"{NAME}: {path} does not read", file=sys.stderr)
            print(f"  {bad}", file=sys.stderr)
            return 1

    declared = obligations(policy)
    if not declared:
        print(f"{NAME}: the policy declares no planned obligations, nothing to join")
        return 0

    findings = judge(policy, files)
    if findings:
        print(
            f"{NAME}: the planned obligations and their producers disagree",
            file=sys.stderr,
        )
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        print(
            "  a planned obligation is a document some build leg must emit an "
            "inventory plan for;",
            file=sys.stderr,
        )
        print(
            "  declare the producer with `# plan-producer: <class> <prefix>` "
            "beside the class name.",
            file=sys.stderr,
        )
        return 1

    print(
        f"{NAME}: {len(declared)} planned obligation(s) joined to their "
        f"producers across {len(files)} workflow(s)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

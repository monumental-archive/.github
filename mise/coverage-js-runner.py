#!/usr/bin/env python3
"""Name a JavaScript tree's test runner and its coverage provider.

Org canon (#1357). The `typescript` leg of `coverage-measure.sh` runs the
repository's own runner under that runner's own coverage provider, and
both are READ, never chosen: the runner and the provider are whatever
the repository's lockfile pins, and the belt pins neither. A second
instrumentation of the suite (c8 or nyc beside the runner) is rejected
in the issue, and so is a provider version the belt would own where the
repository's lockfile should.

Run from the repository root, with the tracked files among
`package.json` and the lockfiles as arguments (the caller asks git, so
an untracked lockfile is never read). Prints one line, or nothing:

    vitest <provider>     the leg applies; <provider> is `v8`,
                          `istanbul`, `none` (the lockfile carries no
                          provider) or `both` (it carries two, and the leg
                          does not choose)
    unsupported <runner>  the tree has a test runner the leg does not
                          support; the caller names it and skips
    (nothing)             no tracked root package.json, or one that
                          declares no test runner at all

The leg supports vitest. Any other runner is named, from the root
package.json's dependencies or its `test` script, so a skip says what
it skipped rather than reading as a tree with nothing to measure.
"""

import json
import re
import sys
from pathlib import Path

# Lockfiles the leg reads, in the order a package manager would prefer.
LOCKFILES = ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lock")

# A package's entry in any of the four: pnpm's `  vitest@4.1.12:` and
# `'@vitest/coverage-v8@4.1.12':`, yarn's `vitest@^4.1.12:` and berry's
# `"vitest@npm:4.1.12":`, bun's `"vitest@4.1.12"`, and npm's
# `"node_modules/vitest":`.
ENTRY = r'(?:(?:^|[\s"\'])({name})@(?:npm:)?[\^~]?\d|"node_modules/({name})")'
RUNNER = re.compile(ENTRY.format(name="vitest"), re.MULTILINE)
PROVIDER = re.compile(
    ENTRY.format(name=r"@vitest/coverage-(?:v8|istanbul)"),
    re.MULTILINE,
)

# Runners the leg does not support, by the package that provides them.
OTHER_RUNNERS = (
    "jest",
    "mocha",
    "ava",
    "jasmine",
    "tap",
    "uvu",
    "karma",
    "@playwright/test",
    "cypress",
)

# The `test` script npm writes into a new package.json: no runner at all.
NPM_PLACEHOLDER = "no test specified"


def providers(lock: str) -> set[str]:
    """Read the vitest coverage providers a lockfile carries.

    Returns:
        `v8` and/or `istanbul`.

    """
    found = set()
    for match in PROVIDER.finditer(lock):
        package = match.group(1) or match.group(2)
        found.add(package.rsplit("-", 1)[-1])
    return found


def other_runner(manifest: dict) -> str | None:
    """Name a test runner the leg does not support.

    Returns:
        The runner's package, the first word of the `test` script, or
        None when the manifest declares no test runner.

    """
    declared: dict = {}
    for field in ("dependencies", "devDependencies"):
        section = manifest.get(field)
        if isinstance(section, dict):
            declared |= section
    for runner in OTHER_RUNNERS:
        if runner in declared:
            return runner
    scripts = manifest.get("scripts")
    script = scripts.get("test") if isinstance(scripts, dict) else None
    if isinstance(script, str) and script.strip() and NPM_PLACEHOLDER not in script:
        words = script.split()
        if words[:2] == ["node", "--test"]:
            return "node --test"
        return words[0]
    return None


def main(tracked: list[str]) -> int:
    """Print the runner line for the tree in the working directory.

    Returns:
        0, or 1 when the root package.json is not readable JSON.

    """
    if "package.json" not in tracked:
        return 0
    try:
        manifest = json.loads(Path("package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"coverage-js-runner: package.json: {exc}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        manifest = {}
    lockfile = next((name for name in LOCKFILES if name in tracked), None)
    lock = Path(lockfile).read_text(encoding="utf-8") if lockfile else ""
    if RUNNER.search(lock):
        found = providers(lock)
        provider = found.pop() if len(found) == 1 else ("both" if found else "none")
        print(f"vitest {provider}")
        return 0
    runner = other_runner(manifest)
    if runner == "vitest" or (runner is None and "vitest" in json.dumps(manifest)):
        # Declared, but the lockfile pins no vitest: nothing the leg may run.
        print("unsupported vitest-without-a-lockfile-entry")
        return 0
    if runner is not None:
        print(f"unsupported {runner}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

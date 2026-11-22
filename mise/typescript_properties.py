#!/usr/bin/env python3
"""A repository's fast-check properties, under the belt's seed (#1549).

The Python half of `test:typescript-properties` (the gate),
`audit:typescript-properties` (the Monday audit) and
`lint:typescript-properties`, called by `typescript-properties.sh` and
by the lint's body. #1332's shape for TypeScript: the runner (vitest)
and fast-check are the repository's own, read from its lockfile, and
the belt pins neither; what the belt owns is the seed and the run
count.

HOW THE SEED REACHES FAST-CHECK, measured on vitest 4.1.12 and
fast-check 4.9.0 as monumental-archive locks them (the verdict entry in
docs/tooling-verdicts.md has the numbers). fast-check reads no
environment variable, so the seed has to be set from inside the test
process: `fc.configureGlobal`. vitest's CLI refuses `--setupFiles`
(`CACError: Unknown option`), so the setup file reaches vitest through
a WRAPPER CONFIG this script writes to a scratch directory and the
shell hands vitest with `-c`. The wrapper imports the repository's own
vitest config unchanged and adds four things for this run only: the
belt's setup file, `include` narrowed to exactly the tracked property
files (so a property the repository's own globs miss still runs), the
default `exclude` (so a repository that keeps properties out of its own
suite still has them run here), and a JSON report, which `verify`
reads to prove every file ran. Nothing is written into the repository.

The setup file configures every fast-check instance the repository can
import — `fast-check` itself and the `fc` that `@fast-check/vitest`
re-exports — because a pnpm tree that depends only on
`@fast-check/vitest` cannot import `fast-check` by name. It records
every example (verbosity 2) and installs a reporter that prints, per
property, its test, run count, seed and a digest of every generated
example: two gate runs of one commit print the same lines, which is
the determinism claim made visible. A failure still throws fast-check's
own report, counterexample and replay path included.

Subcommands, run from the repository root:

  locked LOCKFILE...         prints `fast-check` and/or `vitest` for
                             what the lockfiles carry, one per line
  lint FILE...               every tracked TypeScript/JavaScript file;
                             prints each import of fast-check outside a
                             property file and each property file that
                             restates the seed or run count
  stage DIR [CONFIG]         writes the wrapper and setup file into
                             DIR; the property files come on stdin,
                             NUL-separated (org_tracked, #1986)
  verify REPORT FILE...      exits 1 unless every FILE ran, with at
                             least one test, in vitest's JSON report
  collected LIST FILE...     exits 1, printing each, when a FILE is in
                             `vitest list --filesOnly --json`'s LIST:
                             the repository's own run collects it

Exits 0 when clean, 1 on a finding, 2 on a usage error.
"""

import json
import re
import sys
from pathlib import Path

import coverage_js_runner
import decoded
import findings
import typed_std
from tracked import stdin_paths

EXIT_USAGE = 2
# stage takes a directory and, optionally, the config; verify a report
# and at least one file.
STAGE_ARGS = (1, 2)
VERIFY_MIN_ARGS = 2

# The lockfile reader is coverage_js_runner.py's, so the TypeScript
# legs agree on what "the lockfile pins vitest" means (#1357).
ENTRY: str = coverage_js_runner.ENTRY
VITEST: re.Pattern[str] = coverage_js_runner.RUNNER
FAST_CHECK = re.compile(
    ENTRY.format(name=r"fast-check|@fast-check/[\w.-]+"), re.MULTILINE
)

# A property file: `*.prop.test.` and a TypeScript extension. The four
# are the four TypeScript compiles; a property in a .js file is refused
# as an import outside a property file, since a TypeScript repository
# is TypeScript (#445).
PROPERTY = re.compile(r"\.prop\.test\.(?:ts|tsx|mts|cts)$")

# An import of fast-check or any @fast-check/* package, in every form
# a module can take it: `from`, side-effect `import "…"`, dynamic
# `import(…)` and `require(…)`, subpaths included.
IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)"""
    r"""["'](fast-check|@fast-check/[\w.-]+)(?:/[^"']*)?["']"""
)

# A property restating what the belt owns: a `seed` or `numRuns`
# parameter (shorthand `{ seed }` included) or a call that replaces the
# global configuration the setup file installs.
RESTATED = re.compile(r"\b(seed|numRuns)\b\s*[:,}]|\b(configureGlobal)\s*\(")

# vitest's own default exclude (vitest 4: node_modules and .git only).
DEFAULT_EXCLUDE = ["**/node_modules/**", "**/.git/**"]

SETUP = """\
// Written by the belt's typescript_properties.py for one run (#1549).
import { createHash } from "node:crypto";
import { expect } from "vitest";

const seed = Number(process.env.ORG_PROPERTY_SEED);
const numRuns = Number(process.env.ORG_PROPERTY_RUNS);
if (!Number.isInteger(seed) || !Number.isInteger(numRuns)) {
  throw new Error(
    "typescript-properties: ORG_PROPERTY_SEED and ORG_PROPERTY_RUNS"
    + " must be integers",
  );
}
const found: any[] = [];
try { found.push(await import("fast-check")); } catch {}
try { found.push((await import("@fast-check/vitest")).fc); } catch {}
if (found.length === 0) {
  throw new Error(
    "typescript-properties: neither fast-check nor @fast-check/vitest"
    + " is importable here",
  );
}
for (const fc of found) {
  fc.configureGlobal({
    ...fc.readConfigureGlobal(),
    seed,
    numRuns,
    verbose: 2,
    reporter: (out: any) => {
      const digest = createHash("sha256");
      for (const tree of out.executionSummary) {
        digest.update(`${fc.stringify(tree.value)}\\n`);
      }
      const examples =
        out.verbose >= 2 ? digest.digest("hex").slice(0, 16) : "unrecorded";
      console.log(
        `fast-check: ${expect.getState().currentTestName}: ${out.numRuns} runs,`
        + ` seed ${out.seed}, examples ${examples}`,
      );
      if (out.failed) {
        throw new Error(fc.defaultReportMessage({ ...out, verbose: 1 }));
      }
    },
  });
}
"""

WRAPPER = """\
// Written by the belt's typescript_properties.py for one run (#1549).
{import_line}
const files = {files};
export default async (env: any) => {{
  let config: any = {config_expr};
  if (typeof config === "function") config = await config(env);
  config = (await config) ?? {{}};
  const test = config.test ?? {{}};
  if (test.projects !== undefined || test.workspace !== undefined) {{
    throw new Error(
      "typescript-properties: the vitest config defines test.projects, and a"
      + " project does not inherit the root setupFiles the belt's seed rides on",
    );
  }}
  const prior = test.setupFiles === undefined ? [] : [test.setupFiles].flat();
  return {{
    ...config,
    test: {{
      ...test,
      include: files,
      exclude: {exclude},
      setupFiles: [...prior, {setup}],
      reporters: ["default", "json"],
      outputFile: {{ json: process.env.ORG_PROPERTY_REPORT }},
    }},
  }};
}};
"""


def locked(lockfiles: list[str]) -> int:
    """Print what the lockfiles carry of fast-check and vitest.

    Returns:
        0.

    """
    text = "\n".join(Path(f).read_text(encoding="utf-8") for f in lockfiles)
    if FAST_CHECK.search(text):
        print("fast-check")
    if VITEST.search(text):
        print("vitest")
    return 0


def lint(files: list[str]) -> int:
    """Name each import outside a property file and each restatement.

    Returns:
        0 when clean, 1 with a finding.

    """
    findings = []
    for name in files:
        try:
            text = Path(name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            findings.append(f"{name}: cannot be read ({err.__class__.__name__})")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not PROPERTY.search(name):
                findings.extend(
                    f"{name}:{number}: imports {typed_std.group(match, 1)} but is not a"
                    " *.prop.test.{ts,tsx,mts,cts} file"
                    for match in IMPORT.finditer(line)
                )
                continue
            for match in RESTATED.finditer(line):
                what = typed_std.group(match, 1) or f"{typed_std.group(match, 2)}()"
                findings.append(
                    f"{name}:{number}: restates {what}; the belt's seed and run"
                    " count own every property"
                )
    for finding in findings:
        print(finding)
    return 1 if findings else 0


def stage(scratch: str, config: str | None, files: list[str]) -> int:
    """Write the wrapper config and the setup file into `scratch`.

    Returns:
        0.

    """
    out = Path(scratch)
    setup = out / "org-property-setup.mts"
    setup.write_text(SETUP, encoding="utf-8")
    if config:
        target = json.dumps(str(Path(config).resolve()))
        import_line = f"import * as repo from {target};"
        config_expr = "repo.default"
    else:
        import_line = "// The repository has no vitest config."
        config_expr = "{}"
    (out / "org-property.config.mts").write_text(
        WRAPPER.format(
            import_line=import_line,
            config_expr=config_expr,
            files=json.dumps(files),
            exclude=json.dumps(DEFAULT_EXCLUDE),
            setup=json.dumps(str(setup)),
        ),
        encoding="utf-8",
    )
    return 0


def report_runs(data: object) -> dict[str, int]:
    """Read vitest's JSON report: how many tests each file ran.

    A missing or empty `testResults`, `name` or `assertionResults`
    reads as none, as vitest leaves them out; a field of another shape
    is refused by name, through `decoded`'s readers.

    Returns:
        Each test file's resolved path, and its test count.

    """
    out: dict[str, int] = {}
    results = decoded.as_object(data, "the report").get("testResults")
    for index, item in enumerate(
        decoded.as_array(results, "testResults") if results else []
    ):
        where = f"testResults[{index}]"
        result = decoded.as_object(item, where)
        name = decoded.as_str(result.get("name", ""), f"{where}.name")
        tests = result.get("assertionResults")
        count = (
            len(decoded.as_array(tests, f"{where}.assertionResults")) if tests else 0
        )
        out[str(Path(name).resolve())] = count
    return out


def listed_files(data: object) -> set[str]:
    """Read `vitest list --filesOnly --json`: the files it collects.

    An entry of another shape is refused by name, through `decoded`'s
    readers.

    Returns:
        Each file's resolved path.

    """
    out: set[str] = set()
    for index, item in enumerate(decoded.as_array(data, "the listing")):
        where = f"listing[{index}]"
        entry = decoded.as_object(item, where)
        file = decoded.as_str(entry.get("file"), f"{where}.file")
        out.add(str(Path(file).resolve()))
    return out


def verify(report: str, files: list[str]) -> int:
    """Prove every property file ran, with at least one test.

    Returns:
        0 when each did, 1 otherwise.

    """
    try:
        ran = report_runs(decoded.load_json(Path(report).read_bytes()))
    except OSError as err:
        print(f"no vitest JSON report at {report} ({err.__class__.__name__})")
        return 1
    except decoded.UndecodableError as err:
        print(f"no vitest JSON report at {report} ({err})")
        return 1
    missing = [f for f in files if ran.get(str(Path(f).resolve()), 0) == 0]
    for name in missing:
        print(f"{name}: did not run, or ran no test")
    return 1 if missing else 0


def collected(listing: str, files: list[str]) -> int:
    """Name each property file the repository's own vitest run collects.

    Returns:
        0 when none is, 1 when one is or the listing cannot be read.

    """
    try:
        found = listed_files(decoded.load_json(Path(listing).read_bytes()))
    except OSError as err:
        print(f"no readable vitest file list at {listing} ({err.__class__.__name__})")
        return 1
    except decoded.UndecodableError as err:
        print(f"no readable vitest file list at {listing} ({err})")
        return 1
    leaked = [f for f in files if str(Path(f).resolve()) in found]
    for name in leaked:
        print(name)
    return 1 if leaked else 0


def main(argv: list[str]) -> int:
    """Dispatch a subcommand.

    Returns:
        The subcommand's exit status, or 2 on a usage error.

    """
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    if command == "locked" and args:
        return locked(args)
    if command == "lint":
        return lint(args)
    if command == "stage" and len(args) in STAGE_ARGS:
        files = stdin_paths()
        return stage(args[0], args[1] if len(args) > 1 else None, files)
    if command == "verify" and len(args) >= VERIFY_MIN_ARGS:
        return verify(args[0], args[1:])
    if command == "collected" and len(args) >= VERIFY_MIN_ARGS:
        return collected(args[0], args[1:])
    head, *rest = __doc__.split("Subcommands", 1)[0].strip().splitlines()
    findings.finding("", head, rest)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

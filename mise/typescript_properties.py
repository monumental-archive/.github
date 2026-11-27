#!/usr/bin/env python3
"""A repository's fast-check properties, under the belt's seed (#1549).

The Python half of `test:typescript-properties` (the gate),
`audit:typescript-properties` (the Monday audit) and
`lint:typescript-properties`, called by `typescript-properties.sh` and
by the lint's body. #1332's shape for TypeScript: the runner (vitest)
and fast-check are the repository's own, read from its lockfile, and
the belt pins neither; what the belt owns is every bound on a
property: the seed, the run count and the time (#2144).

HOW THE SEED REACHES FAST-CHECK, measured on vitest 4.1.12 and
fast-check 4.9.0 as monumental-archive locks them (the verdict entry in
docs/tooling-verdicts.md has the numbers). fast-check reads no
environment variable, so the seed has to be set from inside the test
process: `fc.configureGlobal`. vitest's CLI refuses `--setupFiles`
(`CACError: Unknown option`), so the setup file reaches vitest through
a WRAPPER CONFIG this script writes to a scratch directory and the
shell hands vitest with `-c`. The wrapper imports the repository's own
vitest config unchanged and adds, for this run only, a JSON report,
which `verify` reads to prove every file ran, and a reporter whose
`onInit` hands the belt's setup file to every project vitest resolved,
however the config declared it (#2104): a project does not inherit
the root's `setupFiles`. The same hook sets each project's `exclude`
to vitest's default, so a repository that keeps properties out of its
own suite still has them run here, and sets each project's
`testTimeout` to 0, which vitest reads as no per-test limit (#2144):
the audit runs 50 times the gate's examples, so a limit that scales
with neither is the job's `timeout-minutes`, as `deadline=None` is for
Python. Hook timeouts are untouched. A root `test.testTimeout` alone
does not reach an inline project, so the bound is set where the seed
is. Where the config has no
`test.projects`, the wrapper also narrows `include` to exactly the
tracked property files, so a property the repository's own globs miss
still runs; where it has projects, the file filters put each property
file in the project whose `include` matches it, and a file no project
includes does not run. Nothing is written into the repository.

The setup file configures every fast-check instance the repository can
import — `fast-check` itself and the `fc` that `@fast-check/vitest`
re-exports — because a pnpm tree that depends only on
`@fast-check/vitest` cannot import `fast-check` by name. It records
every example (verbosity 2) and installs a reporter that prints, per
property, its test, run count, seed and a digest of every generated
example: two gate runs of one commit print the same lines, which is
the determinism claim made visible. A failure still throws fast-check's
own report, counterexample and replay path included. Last, it appends
its test file's path to the file `ORG_PROPERTY_SEEDED` names: `onInit`
changing a project's config is not a contract vitest documents, and
that list is what makes a vitest that stops loading the seed red by
name rather than green at fast-check's own random seed.

Subcommands, run from the repository root:

  locked LOCKFILE...         prints `fast-check` and/or `vitest` for
                             what the lockfiles carry, one per line
  lint FILE...               every tracked TypeScript/JavaScript file;
                             prints each import of fast-check outside a
                             property file and each property file that
                             restates the seed, the run count or a time
                             bound
  stage DIR [CONFIG]         writes the wrapper and setup file into
                             DIR; the property files come on stdin,
                             NUL-separated (org_tracked, #1986)
  verify REPORT SEEDED FILE...
                             exits 1 unless every FILE ran, with at
                             least one test, in vitest's JSON report,
                             and is in SEEDED, the setup file's list
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
# stage takes a directory and, optionally, the config; verify a
# report, the seeded list and at least one file; collected a listing
# and at least one file.
STAGE_ARGS = (1, 2)
VERIFY_MIN_ARGS = 3
COLLECTED_MIN_ARGS = 2

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

# A property restating what the belt owns (#2144: the seed, the run
# count and the time): a `seed` or `numRuns` parameter (shorthand
# `{ seed }` included), fast-check's two time limits that end a run
# before its examples are done, or a call that replaces the global
# configuration the setup file installs (fast-check's, or vitest's
# `vi.setConfig`, which can set the test timeout). A `timeout` key is
# TIMEOUT's, as it belongs to whichever call holds it.
RESTATED = re.compile(
    r"\b(seed|numRuns|interruptAfterTimeLimit|skipAllAfterTimeLimit)\b\s*[:,}]"
    r"|\b(configureGlobal|vi\s*\.\s*setConfig)\s*\("
)
# A `timeout` key, shorthand included: vitest's per-test option where
# it sits in an object argument of a `test`/`it` call, fast-check's
# everywhere else (`fc.assert`'s parameters, `test.prop`'s first call).
TIMEOUT = re.compile(r"\btimeout\b\s*[:,}]")

# The head of a vitest test call: `test(`, `it(`, and any chain on them
# (`test.prop([...])(`, `it.concurrent(`, `test.each(rows)(`). Read on
# the masked source, so a name in a string or comment is not a call.
TEST_CALL = re.compile(r"(?<![\w$.])(?:test|it)((?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\(")
# Chain members whose own call comes before the test's arguments.
CURRIED = ("prop", "each", "for")
# vitest's third argument: a test's timeout, or its options.
TEST_ARGS_TIMEOUT = 2

# vitest's own default exclude (vitest 4: node_modules and .git only).
DEFAULT_EXCLUDE = ["**/node_modules/**", "**/.git/**"]

SETUP = """\
// Written by the belt's typescript_properties.py for one run (#1549).
import { createHash } from "node:crypto";
import { appendFileSync } from "node:fs";
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
// The proof this file ran seeded (#2104): `verify` reds a file in the
// report and not in this list, so a vitest that stops loading the seed
// is red by name, never green at fast-check's own random seed.
const seeded = process.env.ORG_PROPERTY_SEEDED;
if (!seeded) {
  throw new Error("typescript-properties: ORG_PROPERTY_SEEDED is unset");
}
appendFileSync(seeded, `${expect.getState().testPath}\\n`);
"""

WRAPPER = """\
// Written by the belt's typescript_properties.py for one run (#1549).
{import_line}
const files = {files};
const setup = {setup};
const exclude = {exclude};
// Every project vitest resolved, however the config declared it (an
// inline object, a string, a glob), takes the belt's seed (#2104). A
// project does not inherit the root's setupFiles, and onInit runs once
// after vitest has resolved them all, the root project included.
const seeder = {{
  onInit(vitest: any) {{
    for (const project of vitest.projects) {{
      project.config.setupFiles = [...project.config.setupFiles, setup];
      project.config.exclude = [...exclude];
      // No per-test limit (#2144): the job's timeout-minutes bounds the
      // run, at 200 examples or 10,000. 0 is vitest's "no limit";
      // hookTimeout stays the repository's.
      project.config.testTimeout = 0;
    }}
  }},
}};
export default async (env: any) => {{
  let config: any = {config_expr};
  if (typeof config === "function") config = await config(env);
  config = (await config) ?? {{}};
  const test = config.test ?? {{}};
  // Without projects the root's include is narrowed to exactly the
  // tracked property files; with them, the file filters pick each
  // file's project, and a file no project includes does not run.
  const narrowed = test.projects === undefined ? {{ include: files, exclude }} : {{}};
  return {{
    ...config,
    test: {{
      ...test,
      ...narrowed,
      reporters: ["default", "json", seeder],
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


def _quoted_end(text: str, i: int) -> int:
    """Find where the string literal opening at `i` ends.

    Returns:
        The offset of its closing quote, or of the newline or end of
        text that leaves it unclosed.

    """
    j = i + 1
    while j < len(text) and text[j] not in {text[i], "\n"}:
        j += 2 if text[j] == "\\" else 1
    return min(j, len(text))


def _comment_end(text: str, i: int) -> int:
    """Find where the comment opening at `i` ends, or -1 for none there.

    Returns:
        The offset just past it: its newline kept, `*/` consumed.

    """
    if text.startswith("//", i):
        end = text.find("\n", i)
        return len(text) if end < 0 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    return -1


def _template_step(text: str, i: int, stack: list[str]) -> tuple[int, bool]:
    """Take one step inside a template literal's text.

    Returns:
        The next offset, and whether the characters stepped over are
        the template's text, to blank.

    """
    if text[i] == "\\":
        return i + 2, True
    if text[i] == "`":
        stack.pop()
        return i + 1, False
    if text.startswith("${", i):
        stack.append("{")
        return i + 2, False
    return i + 1, True


def _code_step(text: str, i: int, stack: list[str]) -> tuple[int, int]:
    """Take one step in code: a comment, a string, or one character.

    Returns:
        The next offset, and where the span to blank starts (the next
        offset when there is none).

    """
    comment = _comment_end(text, i)
    if comment >= 0:
        return comment, i
    c = text[i]
    if c in {'"', "'"}:
        end = _quoted_end(text, i)
        return end + 1, i + 1
    if c in {"`", "{"} and (c == "`" or stack):
        stack.append(c)
    elif c == "}" and stack:
        stack.pop()
    return i + 1, i + 1


def masked(text: str) -> str:
    """Blank every comment and string body, keeping offsets and newlines.

    Quotes and template delimiters stay; a template's `${...}` stays
    code. A regex literal is read as code, which a property file's
    test calls do not depend on.

    Returns:
        `text` of the same length, with those characters as spaces.

    """
    out = list(text)
    stack: list[str] = []  # "`" in a template, "{" in code inside one
    i = 0
    while i < len(text):
        if stack and stack[-1] == "`":
            nxt, text_part = _template_step(text, i, stack)
            blank_from = i if text_part else nxt
            blank_to = nxt
        else:
            nxt, blank_from = _code_step(text, i, stack)
            # A string's closing quote stays; a comment goes whole.
            blank_to = nxt - 1 if blank_from == i + 1 and nxt > i + 1 else nxt
        for k in range(blank_from, min(blank_to, len(text))):
            if out[k] != "\n":
                out[k] = " "
        i = nxt
    return "".join(out)


def arguments_of(code: str, start: int) -> tuple[list[tuple[int, int]], int]:
    """Split the bracketed group opening at `start` into its arguments.

    Returns:
        Each top-level argument's (start, end) span, and the offset just
        past the group's closing bracket (len(code) when unclosed).

    """
    args: list[tuple[int, int]] = []
    depth, begin, i = 0, start + 1, start
    while i < len(code):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append((begin, i))
                return args, i + 1
        elif c == "," and depth == 1:
            args.append((begin, i))
            begin = i + 1
        i += 1
    args.append((begin, i))
    return args, i


def test_calls(code: str) -> list[list[tuple[int, int]]]:
    """Find each vitest test call's own arguments in the masked source.

    Returns:
        Each call's argument spans, the curried calls skipped.

    """
    calls = []
    for match in TEST_CALL.finditer(code):
        chain: list[str] = re.findall(
            r"[A-Za-z_$][\w$]*", typed_std.group(match, 1) or ""
        )
        at = match.end() - 1
        args, after = arguments_of(code, at)
        for _ in range(sum(member in CURRIED for member in chain)):
            nxt = re.compile(r"\s*\(").match(code, after)
            if nxt is None:
                break
            args, after = arguments_of(code, nxt.end() - 1)
        calls.append(args)
    return calls


def time_bounds(name: str, text: str) -> list[str]:
    """Name each time a property file sets itself (#2144).

    Returns:
        One finding per vitest per-test timeout (a third argument, or a
        `timeout` in a test's options) and per fast-check `timeout`.

    """
    code = masked(text)

    def line(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    found: list[tuple[int, str]] = []
    options: list[tuple[int, int]] = []
    for args in test_calls(code):
        for start, end in args[1:]:
            if code[start:end].strip().startswith("{"):
                options.append((start, end))
        # `test(name, fn, timeout)`; in `test(name, options, fn)` the
        # third is the body, and the options' `timeout` is TIMEOUT's.
        second = code[args[1][0] : args[1][1]].strip() if len(args) > 1 else ""
        if len(args) > TEST_ARGS_TIMEOUT and not second.startswith("{"):
            start, end = args[TEST_ARGS_TIMEOUT]
            third = code[start:end]
            if third.strip() and not third.strip().startswith("{"):
                at = start + len(third) - len(third.lstrip())
                found.append((at, "a per-test timeout (test()'s third argument)"))
    for match in TIMEOUT.finditer(code):
        at = match.start()
        vitest = any(start <= at < end for start, end in options)
        what = "vitest's timeout option" if vitest else "fast-check's timeout"
        found.append((at, what))
    return [
        f"{name}:{line(at)}: restates {what}; the belt's seed, run count and"
        " time bound own every property"
        for at, what in sorted(found)
    ]


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
        if not PROPERTY.search(name):
            for number, line in enumerate(text.splitlines(), 1):
                findings.extend(
                    f"{name}:{number}: imports {typed_std.group(match, 1)} but is not a"
                    " *.prop.test.{ts,tsx,mts,cts} file"
                    for match in IMPORT.finditer(line)
                )
            continue
        # A property file's bounds are read from its code: a comment or
        # string that names one sets nothing.
        for number, line in enumerate(masked(text).splitlines(), 1):
            for match in RESTATED.finditer(line):
                bound = typed_std.group(match, 1)
                call = re.sub(r"\s+", "", typed_std.group(match, 2) or "")
                what = bound or f"{call}()"
                findings.append(
                    f"{name}:{number}: restates {what}; the belt's seed, run"
                    " count and time bound own every property"
                )
        findings.extend(time_bounds(name, text))
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


def verify(report: str, seeded: str, files: list[str]) -> int:
    """Prove every property file ran, with at least one test, seeded.

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
    try:
        lines = Path(seeded).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as err:
        print(f"no seeded list at {seeded} ({err.__class__.__name__})")
        return 1
    under_seed = {str(Path(line).resolve()) for line in lines if line}
    found = []
    for name in files:
        path = str(Path(name).resolve())
        if ran.get(path, 0) == 0:
            found.append(f"{name}: did not run, or ran no test")
        elif path not in under_seed:
            found.append(f"{name}: ran without the belt's seed")
    for finding in found:
        print(finding)
    return 1 if found else 0


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
        return verify(args[0], args[1], args[2:])
    if command == "collected" and len(args) >= COLLECTED_MIN_ARGS:
        return collected(args[0], args[1:])
    head, *rest = __doc__.split("Subcommands", 1)[0].strip().splitlines()
    findings.finding("", head, rest)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Derive a pgrx extension's upgrade script from two generated schemas (#772).

release/generate-pgrx-upgrade.sh downloads the previous release's
schema, builds the candidate's, and hands both here; this is the pure
part between them: two schemas in, one upgrade script or one refusal
out. It understands pgrx's own regular, deterministic output, not
arbitrary SQL, and refuses anything it cannot soundly migrate, naming
the statement. The script's round-trip proof (ALTER EXTENSION UPDATE
against a live installation, catalog compared with a fresh install) is
the authority either way.

Usage: belt upgrade-script PREV NEW OUT [DATA], with EXT_NAME, PREV_V
and NEW_V naming the extension and the two versions. DATA is the
cycle's sql/next-data.sql, the author's declaration that an underivable
change is handled there.

Exit status: 0 written, 1 refused, 2 usage.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from belt import findings, workflow_command

EXIT_REFUSED = 1

DOLLAR = re.compile(r"\$[A-Za-z0-9_]*\$")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# pgrx emits "CREATE  FUNCTION" (double space): match on \s+, always.
FUNC_RE = re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+", re.IGNORECASE)
DEFAULT = re.compile(r"\sDEFAULT\s", re.IGNORECASE)
LANGUAGE = re.compile(r"\bLANGUAGE\b", re.IGNORECASE)

# Named object kinds: creatable when new, droppable by name when removed,
# and REFUSED when changed: an in-place change to one of these is not
# derivable from two schemas (it may need data-aware DDL), so it must be
# provided via sql/next-data.sql, visibly.
NAMED_KINDS = (
    ("CREATE TYPE ", "TYPE"),
    ("CREATE SCHEMA ", "SCHEMA"),
    ("CREATE TABLE ", "TABLE"),
    ("CREATE UNLOGGED TABLE ", "TABLE"),
    ("CREATE VIEW ", "VIEW"),
    ("CREATE SEQUENCE ", "SEQUENCE"),
)
# Always safe to re-apply.
REPLAY = ("COMMENT ON ", "GRANT ", "REVOKE ")

# The PostgreSQL floor block (#1274) is packaging, not schema: the build
# prepends it to every script that installs or updates to a release,
# AFTER `cargo pgrx package`, so a previous release's shipped schema
# begins with it and no package build (this derivation's candidate
# included) ever emits it. It is dropped from the previous schema
# before the diff, and only as the one leading statement: the tag is
# reserved, so anywhere else in either schema it is a refusal, never
# something stripped or diffed.
FLOOR_TAG = "pgrx_floor"
FLOOR_BLOCK = re.compile(r"\ADO \$pgrx_floor\$\n.*?\n\$pgrx_floor\$;\n+", re.DOTALL)

# The psql guard. It MUST be one line, and the constraint is documented
# rather than remembered: PostgreSQL, "Packaging Related Objects into an
# Extension" (doc/extend-extensions): the extension mechanism ignores
# lines beginning with \echo, which scripts use to throw an error when
# fed to psql rather than loaded via CREATE EXTENSION.
#
# Read precisely: the loader ignores lines BEGINNING WITH \echo. `\quit`
# does not begin with \echo, so on a line of its own the server parses it
# and fails, measured, not inferred:
#
#   ERROR:  syntax error at or near "\"
#   LINE 1: \quit
#
# That is what #794 did and why it was reverted (#792): splitting the
# guard satisfied lint:sql and broke the artifact, caught by the proof
# stage with no version spent. `\quit` therefore rides on the \echo line,
# and the whole guard is one line.
#
# So the line is shortened instead. The version leaves the prose (it is
# still in the filename and in the ALTER target the reader is about to
# type), leaving 58 fixed characters:
#
#   58 + len(ext) <= 80   =>   len(ext) <= 22
#
# Measured against the form below: lab_pg 64, postgis 65, timescaledb 69,
# edtf_postgres 71, pg_stat_statements 76. The longest name in common use
# keeps four columns of headroom.
#
# A PostgreSQL identifier may be 63 bytes, so 22 is a real ceiling and is
# asserted, not assumed: a longer name fails HERE, with the arithmetic,
# instead of emitting a file that either the linter or the server rejects.
GUARD = "\\echo Use ALTER EXTENSION {ext} UPDATE to load this file. \\quit"
GUARD_COLUMNS = 80


class RefusedError(Exception):
    """The derivation stops; the message says why."""


@dataclass(frozen=True)
class Release:
    """The extension and the two versions between which it upgrades."""

    ext: str
    prev_v: str
    new_v: str


@dataclass(frozen=True)
class Statement:
    """One statement of a schema: its class, its key, normalised and as written.

    A function's key is its DROP signature, upper-cased; a named object's
    is its kind and name (`TABLE foo`); any other statement's is itself,
    normalised.
    """

    kind: str
    key: str
    normal: str
    text: str


@dataclass
class Schema:
    """A schema's statements, indexed by what the diff asks of them."""

    funcs: dict[str, str] = field(default_factory=dict[str, str])
    named: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    other: dict[str, int] = field(default_factory=dict[str, int])
    order: list[Statement] = field(default_factory=list[Statement])


@dataclass
class Diff:
    """What the upgrade must do, and what it cannot derive."""

    drops: list[str] = field(default_factory=list[str])
    body: list[str] = field(default_factory=list[str])
    failures: list[str] = field(default_factory=list[str])


def before(pattern: re.Pattern[str], text: str) -> str:
    """Cut `text` at the first match of `pattern`.

    Returns:
        What precedes it, stripped; all of `text` when nothing matches.

    """
    m = pattern.search(text)
    return (text[: m.start()] if m else text).strip()


def strip_leading_comments(stmt: str) -> str:
    """Drop the `-- src/...` lines and the /* banner */ pgrx puts before a statement.

    Returns:
        The statement from its first word.

    Raises:
        RefusedError: a block comment never ends.

    """
    stmt = stmt.strip()
    while True:
        if stmt.startswith("/*"):
            end = stmt.find("*/")
            if end < 0:
                msg = f"unterminated block comment: {stmt[:200]!r}"
                raise RefusedError(msg)
            stmt = stmt[end + 2 :].lstrip()
        elif stmt.startswith("--"):
            nl = stmt.find("\n")
            if nl < 0:
                return ""
            stmt = stmt[nl + 1 :].lstrip()
        else:
            return stmt


def split_statements(text: str) -> list[str]:
    """Split on statement-terminating semicolons, dollar-quote aware.

    Returns:
        The statements, leading comments stripped.

    Raises:
        RefusedError: the schema ends inside a statement.

    """
    stmts: list[str] = []
    buf: list[str] = []
    tag: str | None = None
    for line in text.splitlines():
        buf.append(line)
        for m in DOLLAR.finditer(line):
            if tag is None:
                tag = m.group(0)
            elif m.group(0) == tag:
                tag = None
        if tag is None and line.rstrip().endswith(";"):
            stmt = strip_leading_comments("\n".join(buf))
            buf = []
            if stmt:
                stmts.append(stmt)
    if strip_leading_comments("\n".join(buf)):
        msg = f"unterminated statement in generated schema: {buf[0][:200]!r}"
        raise RefusedError(msg)
    return stmts


def norm(stmt: str) -> str:
    """Normalise a statement: no comments, whitespace collapsed, no `;`.

    Returns:
        The statement as the diff compares it.

    """
    body = "\n".join(
        line for line in stmt.splitlines() if not line.strip().startswith("--")
    )
    body = BLOCK_COMMENT.sub(" ", body)
    return re.sub(r"\s+", " ", body).strip().rstrip(";")


def paren_span(text: str, start: int) -> int:
    """Find the parenthesis closing the one at `start`.

    Returns:
        Its index.

    Raises:
        RefusedError: it is never closed.

    """
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    msg = f"unbalanced parentheses in: {text[:200]!r}"
    raise RefusedError(msg)


def func_parts(n: str) -> tuple[str, str, str]:
    """Split a normalised CREATE FUNCTION into its name, arguments and the rest.

    Returns:
        The name, the parenthesised argument list, and what follows it.

    """
    m = FUNC_RE.match(n)
    rest = n[m.end() :] if m else n
    p = rest.index("(")
    close = paren_span(rest, p)
    return rest[:p].strip(), rest[p : close + 1], rest[close + 1 :].strip()


def drop_sig(name: str, args: str) -> str:
    """Spell the signature DROP FUNCTION takes: DEFAULT clauses stripped.

    Postgres ignores argument names in DROP FUNCTION but refuses defaults.

    Returns:
        `name(arg, arg, ...)`.

    """
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in args[1:-1]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    cleaned = [kept for part in parts if (kept := before(DEFAULT, part))]
    return f"{name}({', '.join(cleaned)})"


def returns_region(after: str) -> str:
    """Read the text between the argument list and LANGUAGE.

    A change here means CREATE OR REPLACE would be refused, so the
    function is dropped and recreated.

    Returns:
        That text.

    """
    return before(LANGUAGE, after)


def classify(stmt: str) -> Statement:
    """Class one statement: function, named object, replayable or opaque.

    Everything that is not a function, a named object or a replay
    (extension_sql! blocks, ALTER, INSERT, DO, operator machinery,
    aggregates, casts, ...) is opaque: fine while identical across
    versions (the previous install already ran it), underivable when it
    appears, changes or disappears.

    Returns:
        The statement, classed and keyed.

    Raises:
        RefusedError: a named object's name cannot be read.

    """
    n = norm(stmt)
    u = n.upper()
    if FUNC_RE.match(n):
        name, args, _after = func_parts(n)
        return Statement("function", drop_sig(name, args).upper(), n, stmt)
    for prefix, kind in NAMED_KINDS:
        if u.startswith(prefix):
            m = re.match(r"[^\s(;]+", n[len(prefix) :])
            if not m:
                msg = f"cannot name {kind} in: {n[:200]!r}"
                raise RefusedError(msg)
            return Statement("named", f"{kind} {m.group(0)}", n, stmt)
    if u.startswith(REPLAY):
        return Statement("replay", n, n, stmt)
    return Statement("opaque", n, n, stmt)


def index(stmts: list[str]) -> Schema:
    """Index a schema's statements by class.

    Returns:
        The schema.

    """
    schema = Schema()
    for s in stmts:
        one = classify(s)
        schema.order.append(one)
        if one.kind == "function":
            schema.funcs[one.key] = one.normal
        elif one.kind == "named":
            schema.named.setdefault(one.key, []).append(one.normal)
        else:
            schema.other[one.normal] = schema.other.get(one.normal, 0) + 1
    return schema


def terminated(text: str) -> str:
    """End a statement with its `;`.

    Returns:
        The statement, terminated.

    """
    return text if text.rstrip().endswith(";") else text + ";"


def drops(prev: Schema, new: Schema, release: Release, diff: Diff) -> None:
    """Drop what the new schema no longer has.

    In reverse creation order of the previous schema: the closest thing
    to a correct destroy order two schemas can offer. Anything it gets
    wrong fails the round-trip proof, red on the Release PR.
    """
    seen: set[str] = set()
    for one in prev.order:
        if one.kind == "function" and one.key not in new.funcs:
            name, args, _after = func_parts(one.normal)
            diff.drops.append(f"DROP FUNCTION {drop_sig(name, args)};")
        elif one.kind == "named" and one.key not in new.named and one.key not in seen:
            seen.add(one.key)
            diff.drops.append(f"DROP {one.key};")
        elif one.kind == "opaque" and one.normal not in new.other:
            diff.failures.append(
                f"statement removed since {release.prev_v} and not derivable:"
                f" {one.normal[:160]!r}"
            )
    diff.drops.reverse()


def function(one: Statement, prev: Schema, diff: Diff) -> None:
    """Create or replace a function new or changed since the previous schema."""
    prev_n = prev.funcs.get(one.key)
    if prev_n == one.normal:
        return
    stmt = FUNC_RE.sub("CREATE OR REPLACE FUNCTION ", one.text.strip(), count=1)
    if prev_n is not None:
        _, _, p_after = func_parts(prev_n)
        name, args, n_after = func_parts(one.normal)
        if returns_region(p_after) != returns_region(n_after):
            diff.body.append(f"DROP FUNCTION {drop_sig(name, args)};")
    diff.body.append(terminated(stmt))


def creates(prev: Schema, new: Schema, release: Release, diff: Diff) -> None:
    """Create what is new, replace what changed, and name what is underivable."""
    for one in new.order:
        if one.kind == "function":
            function(one, prev, diff)
        elif one.kind == "named":
            if one.key not in prev.named:
                diff.body.append(terminated(one.text))
            elif prev.named[one.key] != new.named[one.key]:
                diff.failures.append(
                    f"{one.key} changed since {release.prev_v}; in-place changes"
                    " to this kind are not derivable — provide the migration"
                    " in sql/next-data.sql"
                )
        elif one.normal in prev.other:
            continue
        elif one.kind == "replay":
            diff.body.append(terminated(one.text))
        else:
            diff.failures.append(
                f"statement new since {release.prev_v} and not derivable:"
                f" {one.normal[:160]!r}"
            )


def schemas(prev_text: str, new_text: str, release: Release) -> tuple[Schema, Schema]:
    """Read both schemas, the previous one's floor block dropped.

    Returns:
        The previous schema and the candidate's.

    Raises:
        RefusedError: either schema carries the reserved floor tag where
            no floor block may be.

    """
    floor = FLOOR_BLOCK.match(prev_text)
    if floor:
        prev_text = prev_text[floor.end() :]
    if FLOOR_TAG in prev_text:
        msg = (
            f"previous schema {release.ext}--{release.prev_v}.sql uses the reserved"
            f" ${FLOOR_TAG}$ tag other than as its one leading floor block"
        )
        raise RefusedError(msg)
    if FLOOR_TAG in new_text:
        msg = (
            f"candidate schema {release.ext}--{release.new_v}.sql carries the"
            f" reserved ${FLOOR_TAG}$ tag; the floor block is packaging, never schema"
        )
        raise RefusedError(msg)
    return index(split_statements(prev_text)), index(split_statements(new_text))


def guard(ext: str) -> str:
    """Spell the one-line psql guard, held to the column rule.

    Returns:
        The guard line.

    Raises:
        RefusedError: the extension's name makes it longer than the rule.

    """
    fixed = len(GUARD.format(ext=""))
    if fixed + len(ext) > GUARD_COLUMNS:
        msg = (
            f"the psql guard would exceed {GUARD_COLUMNS} columns: "
            f"{fixed} + len('{ext}') = {fixed + len(ext)} > {GUARD_COLUMNS}. "
            "The guard must stay one line (only \\echo lines are ignored by the "
            "extension loader), so shorten the extension name or re-shape the "
            "guard in this script."
        )
        raise RefusedError(msg)
    return GUARD.format(ext=ext)


def render(release: Release, diff: Diff, data: str) -> str:
    """Write the upgrade script: the guard, the header, then the change.

    Returns:
        The script's text.

    """
    lines = [
        guard(release.ext),
        "",
        (
            f"-- Derived by release/generate-pgrx-upgrade.sh: {release.prev_v}"
            f" -> {release.new_v}."
        ),
        "-- DO NOT EDIT. Regenerated on every Release-PR refresh from the",
        (
            f"-- published v{release.prev_v} schema and the v{release.new_v} build,"
            " and proven by"
        ),
        "-- ALTER EXTENSION UPDATE against a live installation before commit.",
        "",
    ]
    if not diff.drops and not diff.body and not data:
        lines.extend((
            f"-- No schema change between {release.prev_v} and {release.new_v}: this",
            "-- release alters only the compiled library.",
        ))
    else:
        lines.extend(diff.drops)
        if diff.drops and diff.body:
            lines.append("")
        lines.extend(diff.body)
        if data:
            lines.extend((
                "",
                "-- Authored data migration for this cycle (sql/next-data.sql):",
                data,
            ))
    return "\n".join(lines) + "\n"


def derive(prev_text: str, new_text: str, release: Release) -> Diff:
    """Diff the two schemas into the upgrade's drops and body.

    Returns:
        The diff, with what it could not derive named in its failures.

    """
    prev, new = schemas(prev_text, new_text, release)
    diff = Diff()
    drops(prev, new, release, diff)
    creates(prev, new, release, diff)
    return diff


def accepted(diff: Diff, release: Release, *, declared: bool) -> bool:
    """Judge what the diff could not derive.

    Without a sql/next-data.sql each is a finding; with one, each is a
    warning that the fragment is assumed to handle it.

    Returns:
        Whether the derivation goes on.

    """
    if diff.failures and not declared:
        for failure in diff.failures:
            findings.finding(release.ext, failure, annotate=True)
        return False
    for failure in diff.failures:
        workflow_command.warning(
            f"{release.ext}: {failure} — assuming sql/next-data.sql handles it;"
            " the round-trip proof decides",
            stream=sys.stderr,
        )
    return True


class Args(argparse.Namespace):
    """The command's arguments, typed as parsed."""

    prev: str
    new: str
    out: str
    data: str


def main(argv: list[str]) -> int:
    """Run the derivation.

    Underivable changes end it, unless the author shipped a
    sql/next-data.sql fragment: the explicit declaration that the
    underivable part is handled there. The declaration is not trusted:
    the round-trip proof compares the upgraded catalog against a fresh
    install either way, so a fragment that does not close the gap still
    fails the Release PR.

    Returns:
        The exit status: 0 written, 1 refused, 2 usage.

    """
    parser = argparse.ArgumentParser(
        prog="belt upgrade-script", description="Derive the extension's upgrade script"
    )
    parser.add_argument("prev", metavar="PREV")
    parser.add_argument("new", metavar="NEW")
    parser.add_argument("out", metavar="OUT")
    parser.add_argument("data", nargs="?", default="", metavar="DATA")
    args = parser.parse_args(argv, namespace=Args())
    prev_path, new_path, out_path, data_path = args.prev, args.new, args.out, args.data
    release = Release(os.environ["EXT_NAME"], os.environ["PREV_V"], os.environ["NEW_V"])
    prev_text = Path(prev_path).read_text(encoding="utf-8")
    new_text = Path(new_path).read_text(encoding="utf-8")
    data = Path(data_path).read_text(encoding="utf-8").strip() if data_path else ""
    try:
        diff = derive(prev_text, new_text, release)
        if not accepted(diff, release, declared=bool(data_path)):
            return EXIT_REFUSED
        text = render(release, diff, data)
    except RefusedError as refused:
        findings.finding("", str(refused))
        return EXIT_REFUSED
    Path(out_path).write_text(text, encoding="utf-8")
    return 0

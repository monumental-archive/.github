"""subject_budget.py's globs and templates, on inputs nobody wrote down.

The properties of `test_subject_budget.py`'s fixed rows (#1332), over
generated dependency names, paths and commit templates:

- `glob_matches`: a path matches every pattern made from it by turning
  slash-free stretches into `*`, a leading run of directories into
  `**/`, or nothing at all (a literal), whatever regex metacharacters
  the path carries; and with no `**` a pattern never matches a path
  deeper than itself, because a single `*` does not cross a slash.
- `render`: the subject grows by exactly one column per `newValue`
  placeholder for each column of version width, carries the dependency
  name wherever the template asks for it, and reports a template as
  unmodelled exactly when a placeholder it cannot resolve is left;
  and `newVersion` on the lock-file branch of the one conditional it
  models, `newValue` on the other, each at its own width (#2145).
- `coverage_js_runner.locked`: every lock format the belt reads
  (pnpm, npm, yarn classic and berry, bun, Cargo) gives back the
  version written into it, whatever the package's name.
- Refusal (R): the dependency readers (`from_cargo`, `from_npm`,
  `from_gomod`, `from_actions`, `from_mise`) read any bytes, and any
  well-formed document of any shape, and answer with dependencies and
  unmodelled lines, never a traceback (#1555). A table of the wrong
  type, a non-object package.json and a file that is not UTF-8 all
  raised until this found them.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from hypothesis import example
from hypothesis import strategies as st

from belt import coverage_js_runner
from belt import subject_budget as sb
from belt.typed_hypothesis import given

if TYPE_CHECKING:
    from collections.abc import Callable

# A path segment: never empty, never a slash or a glob character, and
# rich in what a regex would misread if the pattern were not escaped.
SEGMENT = st.text(alphabet="abz09.-_@+()[]{}^$|\\", min_size=1, max_size=8)
PATH = st.lists(SEGMENT, min_size=1, max_size=5)


def draw_globbed(draw: st.DrawFn) -> tuple[str, str, bool]:
    """Draw a path and a pattern made from it.

    Returns:
        The path, the pattern, and whether the pattern uses `**`.

    """
    segments = draw(PATH)
    parts = []
    for segment in segments:
        how = draw(st.sampled_from(["keep", "star", "inner"]))
        if how == "star":
            parts.append("*")
        elif how == "inner":
            start = draw(st.integers(0, len(segment)))
            end = draw(st.integers(start, len(segment)))
            parts.append(f"{segment[:start]}*{segment[end:]}")
        else:
            parts.append(segment)
    deep = draw(st.integers(0, len(parts) - 1))
    double = draw(st.booleans())
    if double:
        parts = ["**", *parts[deep:]]
    return "/".join(segments), "/".join(parts), double


globbed = st.composite(draw_globbed)


# A dependency name as the managers read them: backend prefixes,
# scopes and paths, but no template braces.
DEP = st.from_regex(r"[a-z][a-z0-9@/._:-]{0,30}", fullmatch=True)
LITERAL = st.text(alphabet="abc -_()[]:.", max_size=12)
TOKEN = st.sampled_from([
    "{{depName}}",
    "{{{depName}}}",
    "{{newValue}}",
    "{{{newValue}}}",
    "{{newVersion}}",
    "{{unknownField}}",
])
FIELD = st.lists(st.one_of(LITERAL, TOKEN), max_size=5).map("".join)
CONFIG: st.SearchStrategy[dict[str, str]] = st.fixed_dictionaries({
    "semanticCommitType": LITERAL,
    "semanticCommitScope": LITERAL,
    **dict.fromkeys(sb.MESSAGE_FIELDS, FIELD),
})


# Renovate's globs, as `glob_matches` models them.
@given(globbed())
@example(("a.b/c", "a.b/*", False))
@example(("Cargo.toml", "**/Cargo.toml", True))
def test_a_path_matches_its_own_patterns(drawn: tuple[str, str, bool]) -> None:
    """Every pattern made from a path selects it."""
    path, pattern, _ = drawn
    assert sb.glob_matches(pattern, path), f"{pattern!r} vs {path!r}"


@given(globbed(), SEGMENT)
def test_a_star_never_crosses_a_slash(drawn: tuple[str, str, bool], extra: str) -> None:
    """Without `**`, a pattern does not select a deeper path."""
    path, pattern, double = drawn
    if double or pattern == "*":
        return
    assert not sb.glob_matches(pattern, f"{path}/{extra}")
    assert not sb.glob_matches(pattern, f"{extra}/{path}")


# The widest subject a commit template can mint.
@given(CONFIG, DEP, st.integers(1, 40))
def test_width_name_and_unmodelled(
    config: dict[str, str], dep: str, width: int
) -> None:
    """Growth per column, the name where asked, unmodelled when left."""
    fields = [config[k] for k in sb.MESSAGE_FIELDS]
    versions = sum(f.count("{{newValue}}") + f.count("{{newVersion}}") for f in fields)
    names = sum(f.count("{{depName}}") for f in fields)
    narrow, unmodelled = sb.render(config, dep, width)
    wide, _ = sb.render(config, dep, width + 1)
    assert len(wide) - len(narrow) == versions
    if names:
        assert dep in narrow
    left = any("{{unknownField}}" in f for f in fields)
    assert (unmodelled is not None) == left
    assert narrow.endswith(sb.PR_TAIL)


BRANCH = st.text(alphabet="abc -_()[]:.", max_size=8)


# The one conditional the model renders (#2145).
@given(BRANCH, BRANCH, DEP, st.integers(1, 20), st.integers(1, 20))
def test_each_branch_renders_its_own_width(
    lock: str, other: str, dep: str, width: int, version: int
) -> None:
    """The lock-file branch carries newVersion, the other newValue."""
    extra = (
        f"to {{{{#if isLockfileUpdate}}}}{lock}{{{{newVersion}}}}"
        f"{{{{else}}}}{other}{{{{newValue}}}}{{{{/if}}}}"
    )
    config = {
        "semanticCommitType": "chore",
        "semanticCommitScope": "deps",
        **dict.fromkeys(sb.MESSAGE_FIELDS, ""),
        "commitMessageExtra": extra,
    }
    locked, unmodelled = sb.render(config, dep, width, version=version, lockfile=True)
    ordinary, left = sb.render(config, dep, width, version=version)
    assert unmodelled is None
    assert left is None
    assert locked == f"chore(deps): to {lock}{'9' * version}{sb.PR_TAIL}"
    assert ordinary == f"chore(deps): to {other}{'9' * width}{sb.PR_TAIL}"


# A package name as the npm and cargo lockfiles write them, and a
# version as they resolve one.
PACKAGE = st.from_regex(
    r"(@[a-z][a-z0-9-]{0,6}/)?[a-z][a-z0-9._-]{0,12}", fullmatch=True
)
VERSION = st.from_regex(
    r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(-[a-z0-9.]{1,8})?", fullmatch=True
)


def npm_lock(name: str, version: str) -> str:
    """Write package-lock.json's entry for one package.

    Returns:
        The lockfile.

    """
    entry: dict[str, object] = {"version": version}
    packages: dict[str, object] = {f"node_modules/{name}": entry}
    document: dict[str, object] = {"packages": packages}
    return json.dumps(document)


def bun_lock(name: str, version: str) -> str:
    """Write bun.lock's entry for one package.

    Returns:
        The lockfile.

    """
    entry: list[object] = [f"{name}@{version}", "", {}]
    packages: dict[str, object] = {name: entry}
    document: dict[str, object] = {"packages": packages}
    return json.dumps(document)


def cargo_lock(name: str, version: str) -> str:
    """Write Cargo.lock's table for one crate.

    Returns:
        The lockfile.

    """
    return f"[[package]]\nname = {json.dumps(name)}\nversion = {json.dumps(version)}\n"


LOCK_FORMATS: dict[str, Callable[[str, str], str]] = {
    "pnpm-lock.yaml": lambda n, v: f"packages:\n\n  '{n}@{v}':\n    x: {{}}\n",
    "package-lock.json": npm_lock,
    "yarn.lock": lambda n, v: f'"{n}@^1", "{n}@npm:^1.2":\n  version "{v}"\n',
    "berry.yarn.lock": lambda n, v: f'"{n}@npm:^1":\n  version: {v}\n',
    "bun.lock": bun_lock,
    "Cargo.lock": cargo_lock,
}


# Every lock format reads back the version written into it.
@given(st.sampled_from(sorted(LOCK_FORMATS)), PACKAGE, VERSION)
def test_the_locked_version_is_read_back(
    lockfile: str, name: str, version: str
) -> None:
    """One entry, one version, whatever the name and the version."""
    lock = LOCK_FORMATS[lockfile](name, version)
    read = coverage_js_runner.locked(lockfile.removeprefix("berry."), lock, name)
    want: set[str] = {version}
    assert want == read


SCALAR: st.SearchStrategy[object] = st.one_of(
    st.booleans(), st.integers(-9, 9), st.text(max_size=8)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or a table.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    tables: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=8), inner, max_size=3
    )
    return arrays | tables


VALUE: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=10)
MANIFEST_KEY = st.sampled_from([
    "dependencies",
    "dev-dependencies",
    "devDependencies",
    "workspace",
    "target",
    "tools",
    "x",
])


def toml(value: object) -> str:
    """Write a value as a TOML inline value.

    Returns:
        The TOML text.

    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        inner = ", ".join(f"{json.dumps(k)} = {toml(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    return json.dumps(value)


def as_json(value: object) -> bytes:
    """Write a value as a JSON document.

    Returns:
        Its UTF-8 bytes.

    """
    return json.dumps(value).encode()


def as_toml(document: dict[str, object]) -> str:
    """Write a document's top keys as TOML lines.

    Returns:
        The TOML text.

    """
    return "".join(f"{json.dumps(k)} = {toml(v)}\n" for k, v in document.items())


DOCUMENT: st.SearchStrategy[dict[str, object]] = st.dictionaries(
    MANIFEST_KEY, VALUE, max_size=4
)
READERS: dict[str, Callable[[list[Path], list[str]], list[sb.Dep]]] = {
    "Cargo.toml": sb.from_cargo,
    "package.json": sb.from_npm,
    "go.mod": sb.from_gomod,
    ".github/workflows/ci.yml": sb.from_actions,
    "mise.toml": sb.from_mise,
}


# Any manifest is read or recorded as unmodelled.
@given(
    st.sampled_from(sorted(READERS)),
    st.one_of(
        st.binary(max_size=80),
        DOCUMENT.map(as_json),
        DOCUMENT.map(as_toml).map(str.encode),
        VALUE.map(as_json),
    ),
)
@example("mise.toml", b'"tools"={""=false}')
def test_any_manifest_is_answered(name: str, data: bytes) -> None:
    """Dependencies and report lines, never a traceback."""
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        report: list[str] = []
        deps = READERS[name]([path], report)
    assert isinstance(deps, list)
    for line in report:
        assert line.startswith(str(path)), line

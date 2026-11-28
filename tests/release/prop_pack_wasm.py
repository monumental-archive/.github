"""pack_wasm.py's package checks, on any package.json and repository.

The properties of `test_pack_wasm.py`'s fixed rows (#1556), over
generated callers, repository fields and package.json documents. `npm`
is replaced in-process: `pack` writes a one-byte tarball and names it.

- V: the repository check accepts exactly the URL forms npm provenance
  matches against the caller — `https://github.com/<caller>`, with or
  without a `git+` prefix and a `.git` suffix — given as a string or
  as `repository.url`; every other spelling, a near miss included, is
  refused with the remedy.
- R: on any package.json text, the step packs or refuses by name
  (exit 0, or 1 with one `::error::` line); it never dies with anything
  else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import example
from hypothesis import strategies as st

from belt.release import pack_wasm as pack
from belt.typed_hypothesis import given

# A GitHub owner and repository name (GitHub drops a `.git` ending
# from a repository's name, so none is drawn).
OWNER = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9-]{0,10}", fullmatch=True)


def a_name(name: str) -> bool:
    """Say whether GitHub would keep the name as drawn.

    Returns:
        True for a name that is not a dot entry and has no `.git` ending.

    """
    return name not in {".", ".."} and not name.endswith(".git")


NAME = st.from_regex(r"[A-Za-z0-9._-]{1,12}", fullmatch=True).filter(a_name)
CALLER = st.builds("{}/{}".format, OWNER, NAME)
VERSION = "1.2.3"
NEAR_MISS = st.sampled_from([
    "http://github.com/{}",
    "https://github.com/{}/",
    "https://www.github.com/{}",
    "git://github.com/{}.git",
    "git+ssh://git@github.com/{}.git",
    "github:{}",
    "{}",
    "git+git+https://github.com/{}",
    "https://github.com/{}.git.git",
    " https://github.com/{}",
])
SCALAR: st.SearchStrategy[object] = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text()
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or an object.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    objects: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=6), inner, max_size=3
    )
    return arrays | objects


JSON: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=12)


def dumped(value: object) -> str:
    """Write a value as JSON text.

    Returns:
        The text.

    """
    return json.dumps(value)


SHAPE = st.sampled_from(["string", "table"])


def placed(url: str, shape: str) -> dict[str, object]:
    """Write a repository field the two ways package.json takes it.

    Returns:
        A package.json document carrying it, at the release's version.

    """
    repo: object = {"type": "git", "url": url} if shape == "table" else url
    return {"name": "pkg", "version": VERSION, "repository": repo}


def verdict(manifest: dict[str, object], caller: str) -> str | None:
    """Run the repository and version check.

    Returns:
        None when accepted, else the refusal.

    """
    try:
        read = pack.Manifest(
            name="pkg", version=VERSION, repository=pack.repository(manifest)
        )
        pack.check(read, VERSION, caller)
    except pack.RefusedError as refused:
        return str(refused)
    return None


# V: the four matching forms pass; every near miss is refused.
@given(CALLER, st.sampled_from(["", "git+"]), st.sampled_from(["", ".git"]), SHAPE)
def test_matching_forms_pass(caller: str, prefix: str, suffix: str, shape: str) -> None:
    """`git+` and `.git`, either or both, string or table: accepted."""
    url = f"{prefix}https://github.com/{caller}{suffix}"
    assert verdict(placed(url, shape), caller) is None


@given(CALLER, NEAR_MISS, SHAPE)
@example("o/r", "https://github.com/{}.git.git", "string")
def test_near_misses_are_refused(caller: str, form: str, shape: str) -> None:
    """A spelling npm would not match is refused, naming the remedy."""
    url = form.format(caller)
    refused = verdict(placed(url, shape), caller)
    want = f"https://github.com/{caller}"
    assert refused == (
        f"package.json repository is '{url}', npm provenance requires"
        f" '{want}' — set repository = \"{want}\" in the crate's Cargo.toml"
        " (canon: [workspace.package] repository with repository.workspace ="
        " true)"
    )


@given(CALLER, CALLER, SHAPE)
def test_another_repository_is_refused(caller: str, other: str, shape: str) -> None:
    """Another caller's URL is accepted only where it is the caller's."""
    url = f"https://github.com/{other}"
    refused = verdict(placed(url, shape), caller)
    assert (refused is None) == (other == caller)


def npm(cwd: Path, *_: str) -> str:
    """Stand an `npm pack` in: one tarball, named.

    Returns:
        The tarball's name.

    """
    (cwd / "pkg-1.2.3.tgz").write_bytes(b"x")
    return "pkg-1.2.3.tgz"


# A package.json shaped like one, with any value where a field goes.
THE_VERSION: st.SearchStrategy[object] = st.just(VERSION)
THE_CALLER: st.SearchStrategy[object] = st.just("https://github.com/o/r")
PACKAGE_JSON: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries(
    {},
    optional={
        "name": JSON,
        "version": THE_VERSION | JSON,
        "repository": THE_CALLER | JSON,
    },
)


# R: any package.json is packed or refused by name.
@given(PACKAGE_JSON.map(dumped) | JSON.map(dumped) | st.text())
@example("[]")
@example("{")
@example('{"repository": "\\n"}')
def test_packs_or_refuses(package_json: str) -> None:
    """Exit 0, or 1 with one error line; never a raise."""
    out = io.StringIO()
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as patched,
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(out),
    ):
        pkg = Path(tmp) / "crate" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(package_json, encoding="utf-8")
        env = {
            "UNTRUSTED_CRATE_DIR": "crate",
            "UNTRUSTED_VERSION": VERSION,
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_OUTPUT": str(Path(tmp) / "out"),
        }
        # A run outside mise, recording nothing (#1934): named by its file.
        patched.setenv("MISE_TASK_NAME", "")
        patched.setenv("ORG_FINDINGS", "")
        patched.setattr(sys, "argv", ["pack_wasm.py"])
        patched.setattr(pack, "npm", npm)
        patched.chdir(tmp)
        code = pack.main([], env=env)
    said = out.getvalue()
    if code != 0:
        assert code == 1
        assert said.startswith("::error::pack_wasm.py: "), said
        assert said.count("\n") == 1, said

"""inventory_plan.py, one pgrx cell's inventory plan, on any manifest.

The properties of `test_pgrx_inventory_plan.py`'s fixed rows (#1556),
over generated crate manifests, majors and document prefixes:

- V: the plan keeps the crate's own package name — the identity the
  purl is minted from — whatever the class's document prefix: the
  `cargoPackage` and the artifact name are the same under every
  prefix, and only the document name carries the prefix, once, in
  front.
- R: on any TOML manifest, whatever its `[package]` holds, the step
  writes a plan or refuses by name (exit 1, one `::error::` line); it
  never dies with anything else.

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

from belt import decoded
from belt.release.pgrx import inventory_plan as inventory
from belt.typed_hypothesis import given

CRATE = st.from_regex(r"[A-Za-z][A-Za-z0-9_-]{0,15}", fullmatch=True)
MAJOR = st.integers(10, 99).map(str)
PREFIX = st.text(max_size=12)
# TOML has no null; floats add nothing the reader branches on.
SCALAR: st.SearchStrategy[object] = st.one_of(
    st.booleans(), st.integers(), st.text(max_size=8)
)


def nested(inner: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    """Wrap values in an array or a table.

    Returns:
        The strategy one level deeper.

    """
    arrays: st.SearchStrategy[object] = st.lists(inner, max_size=3)
    tables: st.SearchStrategy[object] = st.dictionaries(
        st.text(max_size=4), inner, max_size=3
    )
    return arrays | tables


VALUE: st.SearchStrategy[object] = st.recursive(SCALAR, nested, max_leaves=8)
# A `[package]` with any of the two keys the reader takes.
PACKAGE: st.SearchStrategy[object] = st.dictionaries(
    st.sampled_from(["name", "version"]), VALUE, max_size=2
)


def toml_value(value: object) -> str:
    """Render a value as an inline TOML value.

    Returns:
        The TOML text.

    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    elif decoded.is_array(value):
        text = "[" + ", ".join(toml_value(v) for v in value) + "]"
    else:
        table = decoded.as_object(value, "a drawn value")
        pairs = (f"{toml_value(str(k))} = {toml_value(v)}" for k, v in table.items())
        text = "{" + ", ".join(pairs) + "}"
    return text


# V: the prefix names the document, never the package.
@given(CRATE, MAJOR, PREFIX.filter(bool), PREFIX.filter(bool))
@example("edtf-pg", "16", "pgrx-extension:", "x-")
def test_prefix_moves_only_the_document(
    crate: str, major: str, one: str, other: str
) -> None:
    """Package and artifact equal under both; doc is prefix + artifact."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Cargo.toml").write_text(
            f"[package]\nname = {toml_value(crate)}\n", "utf-8"
        )
        plans = [inventory.plan(Path(tmp), major, p) for p in (one, other)]
    for prefix, (entry,) in zip((one, other), plans, strict=True):
        params = decoded.as_object(entry["params"], "params")
        assert params["cargoPackage"] == crate
        assert entry["artifact"] == f"{crate}-pg{major}"
        assert entry["doc"] == f"{prefix}{crate}-pg{major}"
    undocumented: list[dict[str, object]] = [
        {k: v for k, v in plan[0].items() if k != "doc"} for plan in plans
    ]
    assert undocumented[0] == undocumented[1]


# R: any TOML manifest is planned or refused by name.
@given(
    st.one_of(PACKAGE, VALUE),
    MAJOR,
)
@example(1, "16")
@example("edtf", "16")
def test_plans_or_refuses(package: object, major: str) -> None:
    """Exit 0 with a plan, or 1 with one error line and no plan."""
    said = io.StringIO()
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.MonkeyPatch.context() as patched,
        contextlib.redirect_stdout(said),
        contextlib.redirect_stderr(said),
    ):
        (Path(tmp) / "crate").mkdir()
        manifest = f"package = {toml_value(package)}\n"
        (Path(tmp) / "crate" / "Cargo.toml").write_text(manifest, "utf-8")
        # A run outside mise, recording nothing (#1934): named by its file.
        patched.setenv("MISE_TASK_NAME", "")
        patched.setenv("ORG_FINDINGS", "")
        patched.setattr(sys, "argv", ["inventory_plan.py"])
        patched.setenv("PLAN_DOC_PREFIX", "p:")
        patched.chdir(tmp)
        code = inventory.main(["crate", major])
        written = Path(tmp) / "plan" / "plan.json"
        plan = (
            decoded.load_json(written.read_text("utf-8")) if written.is_file() else None
        )
    out = said.getvalue()
    if code == 0:
        assert isinstance(plan, list)
    else:
        assert code == 1
        assert plan is None
        assert out.startswith("::error::inventory_plan.py: "), out
        assert out.count("\n") == 1, out

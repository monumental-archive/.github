#!/usr/bin/env python3
"""inventory-plan.py, one pgrx cell's inventory plan, on any manifest.

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
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "inventory_plan",
    Path(__file__).with_name("inventory-plan.py"),
)
inventory = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventory)

CRATE = st.from_regex(r"[A-Za-z][A-Za-z0-9_-]{0,15}", fullmatch=True)
MAJOR = st.integers(10, 99).map(str)
PREFIX = st.text(max_size=12)
# TOML has no null; floats add nothing the reader branches on.
VALUE = st.recursive(
    st.booleans() | st.integers() | st.text(max_size=8),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=8,
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
    elif isinstance(value, list):
        text = "[" + ", ".join(toml_value(v) for v in value) + "]"
    else:
        pairs = (f"{toml_value(str(k))} = {toml_value(v)}" for k, v in value.items())
        text = "{" + ", ".join(pairs) + "}"
    return text


def run_step(manifest: str, major: str, prefix: str) -> tuple[int, str, object]:
    """Run the step on a crate whose Cargo.toml is `manifest`.

    Returns:
        The exit status, stdout, and the plan written (None when none).

    """
    here = Path.cwd()
    saved = os.environ.get("PLAN_DOC_PREFIX")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "crate").mkdir()
        (Path(tmp) / "crate" / "Cargo.toml").write_text(manifest, "utf-8")
        os.environ["PLAN_DOC_PREFIX"] = prefix
        os.chdir(tmp)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = inventory.main(["crate", major])
        finally:
            os.chdir(here)
            if saved is None:
                os.environ.pop("PLAN_DOC_PREFIX", None)
            else:
                os.environ["PLAN_DOC_PREFIX"] = saved
        written = Path(tmp) / "plan" / "plan.json"
        plan = json.loads(written.read_text("utf-8")) if written.is_file() else None
    return code, out.getvalue(), plan


class NameKeptUnderAnyPrefix(unittest.TestCase):
    """V: the prefix names the document, never the package."""

    @given(CRATE, MAJOR, PREFIX.filter(bool), PREFIX.filter(bool))
    @example("edtf-pg", "16", "pgrx-extension:", "x-")
    def test_prefix_moves_only_the_document(
        self, crate: str, major: str, one: str, other: str
    ) -> None:
        """Package and artifact equal under both; doc is prefix + artifact."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Cargo.toml").write_text(
                f"[package]\nname = {toml_value(crate)}\n", "utf-8"
            )
            plans = [inventory.plan(Path(tmp), major, p) for p in (one, other)]
        for prefix, (entry,) in zip((one, other), plans, strict=True):
            self.assertEqual(entry["params"]["cargoPackage"], crate)
            self.assertEqual(entry["artifact"], f"{crate}-pg{major}")
            self.assertEqual(entry["doc"], f"{prefix}{crate}-pg{major}")
        self.assertEqual(
            {k: v for k, v in plans[0][0].items() if k != "doc"},
            {k: v for k, v in plans[1][0].items() if k != "doc"},
        )


class AnyManifest(unittest.TestCase):
    """R: any TOML manifest is planned or refused by name."""

    @given(
        st.one_of(
            st.dictionaries(st.sampled_from(["name", "version"]), VALUE, max_size=2),
            VALUE,
        ),
        MAJOR,
    )
    @example(1, "16")
    @example("edtf", "16")
    def test_plans_or_refuses(self, package: object, major: str) -> None:
        """Exit 0 with a plan, or 1 with one error line and no plan."""
        code, out, plan = run_step(f"package = {toml_value(package)}\n", major, "p:")
        if code == 0:
            self.assertIsInstance(plan, list)
        else:
            self.assertEqual(code, 1)
            self.assertIsNone(plan)
            self.assertTrue(out.startswith("::error::"), out)
            self.assertEqual(out.count("\n"), 1, out)


if __name__ == "__main__":
    unittest.main()

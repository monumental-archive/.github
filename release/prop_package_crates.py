#!/usr/bin/env python3
"""package_crates.py's verified-then-unverified packaging, on any bytes.

The property of `test_build_rust_crate.py`'s fixed rows (#1556), over
generated workspaces. `cargo` is replaced in-process: the verified run
writes the verified members' crates, the `--no-verify` run writes every
member's — the verified ones again, each maybe changed — and the
crates.io member list names every member.

- V: any byte difference in a verified crate between the two runs — a
  flipped bit, a byte added, a byte lost — is a refusal naming that
  crate and both digests, and every crate whose bytes did not move goes
  unnamed; with no difference the step packages, and an excluded
  member's crate, never verified, is never compared.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import package_crates as crates
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

MEMBER = st.from_regex(r"[a-z][a-z0-9_-]{0,8}", fullmatch=True)


@st.composite
def changed(draw: st.DrawFn, data: bytes) -> bytes:
    """Change some bytes: flip a bit, add a byte, or lose one.

    Returns:
        Bytes that differ from `data`.

    """
    how = draw(st.sampled_from(["flip", "add", "lose"] if data else ["add"]))
    if how == "add":
        at = draw(st.integers(0, len(data)))
        return data[:at] + bytes([draw(st.integers(0, 255))]) + data[at:]
    at = draw(st.integers(0, len(data) - 1))
    if how == "lose":
        return data[:at] + data[at + 1 :]
    bit = 1 << draw(st.integers(0, 7))
    return data[:at] + bytes([data[at] ^ bit]) + data[at + 1 :]


@st.composite
def workspaces(
    draw: st.DrawFn,
) -> tuple[dict[str, str], dict[str, bytes], dict[str, bytes], set[str]]:
    """Draw members, which are excluded, and each run's bytes.

    Returns:
        Each member's version, the verified run's crates, the
        unverified run's crates (by crate file name), and the verified
        crates whose bytes moved.

    """
    members = draw(st.lists(MEMBER, min_size=2, max_size=5, unique=True))
    versions = {m: f"0.{i}.0" for i, m in enumerate(members)}
    excluded = set(
        draw(st.lists(st.sampled_from(members), min_size=1, max_size=len(members) - 1))
    )
    first, second, moved = {}, {}, set()
    for m in members:
        crate = f"{m}-{versions[m]}.crate"
        data = draw(st.binary(max_size=24))
        again = data
        if m not in excluded:
            first[crate] = data
            if draw(st.booleans()):
                again = draw(changed(data))
                moved.add(crate)
        second[crate] = again
    return versions, first, second, moved


def run_package(drawn: tuple[dict, dict, dict, set]) -> list[str]:
    """Run the step on one drawn workspace, an exclusion in force.

    Returns:
        The refusal lines.

    """
    versions, first, second, _ = drawn
    runs = iter([first, second])

    def cargo(*_: str) -> None:
        for name, data in next(runs).items():
            (crates.PACKAGE / name).write_bytes(data)

    crates.cargo = cargo
    crates.published = lambda: list(versions.items())
    here = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            crates.PACKAGE.mkdir(parents=True)
            return crates.package(" --exclude x")
        finally:
            os.chdir(here)


def digest(data: bytes) -> str:
    """Hash bytes as the step does.

    Returns:
        The sha256, hex.

    """
    return hashlib.sha256(data).hexdigest()


class AnyDifferenceRefused(unittest.TestCase):
    """V: a verified crate's bytes may not move, by so much as a bit."""

    @given(workspaces())
    @example((
        {"a": "0.0.0", "b": "0.1.0"},
        {"a-0.0.0.crate": b"\x00"},
        {"a-0.0.0.crate": b"\x01", "b-0.1.0.crate": b""},
        {"a-0.0.0.crate"},
    ))
    def test_refused_exactly_on_moved_bytes(
        self, drawn: tuple[dict, dict, dict, set]
    ) -> None:
        """Each moved crate is named with both digests; no other is."""
        _, first, second, moved = drawn
        refused = run_package(drawn)
        want = [
            f"{name} changed when packaged again without verifying:"
            f" {digest(first[name])} verified, {digest(second[name])} now"
            for name in sorted(moved)
        ]
        self.assertEqual(refused, want)


if __name__ == "__main__":
    unittest.main()

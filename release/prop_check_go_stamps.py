#!/usr/bin/env python3
"""check-go-stamps.py, the go-binary stamp check, on any build info.

The properties of `test_check_go_stamps.py`'s fixed rows (#1556), over
generated `go version -m` output. `go` is replaced in-process by the
row's text, so each example costs no subprocess:

- V: a stamp is accepted exactly when its `vcs.revision` setting is
  the released commit, its `vcs.modified` setting is `false`, and the
  main module's `mod` line is present — whatever other build settings
  the binary carries, including an `-ldflags` value that spells a
  `vcs.` setting of its own, and whatever dependencies it lists.
- R: on any text at all, the check accepts or refuses by name
  (`RefusedError`); it never dies with anything else.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import importlib.util
import unittest
from pathlib import Path

from hypothesis import example
from hypothesis import strategies as st
from org_hypothesis import given

_SPEC = importlib.util.spec_from_file_location(
    "check_go_stamps",
    Path(__file__).with_name("check-go-stamps.py"),
)
stamps = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stamps)

BINARY = Path("staged/tool")
HEAD = "<HEAD>"
SHA = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
# A setting's value as go prints it: any text on one line.
LINE = st.text(st.characters(exclude_characters="\n\r"), max_size=30)
MODULE_PATH = st.from_regex(
    r"[a-z0-9.-]{1,10}(/[a-z0-9._~-]{1,8}){0,3}", fullmatch=True
)
# The settings go stamps besides the VCS ones; their values are the
# builder's own text, so they may say anything.
SETTING = st.tuples(
    st.sampled_from([
        "-buildmode",
        "-compiler",
        "-ldflags",
        "-tags",
        "CGO_ENABLED",
        "GOOS",
    ]),
    st.one_of(
        LINE,
        # go quotes a value only when it holds a space, a tab or a
        # quote, so `-X=` spells the setting bare.
        # HEAD stands for the released commit, filled in per example.
        st.builds(lambda v: f'"-X main.v=vcs.revision={v}"', SHA | st.just(HEAD)),
        st.builds("-X=main.v=vcs.revision={}".format, SHA | st.just(HEAD)),
        st.builds(
            "-X=main.m=vcs.modified={}".format, st.sampled_from(["false", "true"])
        ),
    ),
)


def render(
    rev: str | None,
    modified: str | None,
    module: str | None,
    deps: list[str],
    settings: list[tuple[str, str]],
) -> str:
    """Render the build info `go version -m` prints for one binary.

    Returns:
        The text, in go's order: path, main module, deps, settings, then
        the VCS settings; an absent one is left out.

    """
    lines = ["staged/tool: go1.26.6", "\tpath\texample.com/m/cmd/tool"]
    if module is not None:
        lines.append(f"\tmod\t{module}\tv1.0.0\t")
    lines += [f"\tdep\t{d}\tv0.1.0\th1:x=" for d in deps]
    lines += [f"\tbuild\t{k}={v}" for k, v in settings]
    if rev is not None:
        lines += ["\tbuild\tvcs=git", f"\tbuild\tvcs.revision={rev}"]
    if modified is not None:
        lines.append(f"\tbuild\tvcs.modified={modified}")
    return "\n".join(lines) + "\n"


def verdict(info: str, head: str) -> str | None:
    """Run the check on one binary whose build info is `info`.

    Returns:
        None when accepted, else the refusal's message.

    """
    stamps.go = lambda *_: info
    try:
        stamps.check(BINARY, head)
    except stamps.RefusedError as refused:
        return str(refused)
    return None


@st.composite
def stamped(draw: st.DrawFn) -> tuple[str, str, bool]:
    """Draw a released commit and the build info of one binary.

    Returns:
        The commit, the build info, and whether the model accepts it.

    """
    head = draw(SHA)
    rev = draw(st.one_of(st.none(), SHA, st.just(""), st.just(HEAD)))
    rev = head if rev == HEAD else rev
    modified = draw(st.one_of(st.none(), st.sampled_from(["false", "true", ""])))
    module = draw(st.one_of(st.none(), MODULE_PATH))
    deps = draw(st.lists(MODULE_PATH, max_size=3))
    settings = [
        (k, v.replace(HEAD, head)) for k, v in draw(st.lists(SETTING, max_size=4))
    ]
    good = rev == head and modified == "false" and module is not None
    return head, render(rev, modified, module, deps, settings), good


HEAD_A, HEAD_B = "a" * 40, "b" * 40


class AcceptedExactly(unittest.TestCase):
    """V: accepted exactly when revision is HEAD, clean, and stamped."""

    @given(stamped())
    @example((
        HEAD_A,
        render(
            None,
            None,
            "example.com/m",
            [],
            [
                ("-ldflags", f"-X=main.v=vcs.revision={HEAD_A}"),
                ("-ldflags", "-X=main.m=vcs.modified=false"),
            ],
        ),
        False,
    ))
    @example((
        HEAD_A,
        render(
            HEAD_B,
            "false",
            "example.com/m",
            [],
            [
                ("-ldflags", f"-X=main.v=vcs.revision={HEAD_A}"),
            ],
        ),
        False,
    ))
    @example((
        HEAD_A,
        render(
            HEAD_A,
            "true",
            "example.com/m",
            [],
            [
                ("-ldflags", '"-X main.m=vcs.modified=false"'),
            ],
        ),
        False,
    ))
    def test_accepted_exactly_when_stamped_with_head(
        self, drawn: tuple[str, str, bool]
    ) -> None:
        """No other setting, dependency or value moves the verdict."""
        head, info, good = drawn
        refused = verdict(info, head)
        self.assertEqual(refused is None, good, f"{info!r}: {refused}")
        if refused is not None:
            self.assertTrue(refused.startswith(f"{BINARY.name}: "), refused)


class AnyText(unittest.TestCase):
    """R: any text is accepted or refused by name."""

    @given(st.text(), st.text(max_size=40))
    def test_refuses_by_name_only(self, info: str, head: str) -> None:
        """The check raises nothing but its own refusal, which names the binary."""
        refused = verdict(info, head)
        if refused is not None:
            self.assertTrue(refused.startswith(f"{BINARY.name}: "), refused)


if __name__ == "__main__":
    unittest.main()

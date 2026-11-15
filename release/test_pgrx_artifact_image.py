#!/usr/bin/env python3
"""The pgrx artifact image copies exactly one tree (#1298).

The image carried the extension twice, in two folder layouts, on the
premise that choosing one would break the other. `COPY --from` reads
any path in the source image, so one layout serves both consumers: the
image now holds `/lib` and `/share/extension`, the same on every major,
copied from one staged tree. A second `COPY` (or an `ADD`) is how the
duplicate would come back, so the check counts every instruction that
puts files into the image and holds the one there is to the staged
tree and the root.

The rows run `one_tree()` over the shipped Dockerfile and over planted
fixtures, one per refusal branch, each asserting the exact message.

stdlib `unittest`, matching the other tests here. Run through the gate
as `mise run test`, which `ci` collects.
"""

import pathlib
import shlex
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "pgrx-artifact.Dockerfile"
TREE = ("staged/${TARGETARCH}/image/", "/")
PLACERS = {"COPY", "ADD"}


def instructions(text: str) -> list[list[str]]:
    """Split a Dockerfile into instructions, continuations joined.

    Returns:
        One token list per instruction, its keyword upper-cased first.

    """
    out: list[list[str]] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        tokens = shlex.split(pending + line, comments=False)
        pending = ""
        if tokens:
            out.append([tokens[0].upper(), *tokens[1:]])
    return out


def one_tree(text: str) -> str | None:
    """Refuse a Dockerfile that does not copy exactly the one tree.

    Returns:
        None when the only file-placing instruction is
        `COPY staged/${TARGETARCH}/image/ /`; otherwise the refusal.

    """
    placers = [i for i in instructions(text) if i[0] in PLACERS]
    if not placers:
        return "copies no tree; the artifact image carries exactly one (#1298)"
    if len(placers) > 1:
        return (
            f"copies {len(placers)} trees; the artifact image carries"
            " exactly one layout (#1298)"
        )
    placer = placers[0]
    if placer[0] != "COPY" or tuple(placer[1:]) != TREE:
        return (
            f"places {shlex.join(placer)}; the one tree is"
            f" COPY {TREE[0]} {TREE[1]} (#1298)"
        )
    return None


HEAD = "FROM scratch\nARG TARGETARCH\n"
TAIL = "USER 65534:65534\n"


class OneTree(unittest.TestCase):
    """Every branch of `one_tree()`, on the real file and on plants."""

    def test_shipped_dockerfile_copies_one_tree(self) -> None:
        """The Dockerfile the release builds passes."""
        self.assertIsNone(one_tree(DOCKERFILE.read_text(encoding="utf-8")))

    def test_continuation_and_comments_still_pass(self) -> None:
        """A continued instruction and comments do not change the count."""
        text = HEAD + "# a comment\nCOPY \\\n  staged/${TARGETARCH}/image/ \\\n  /\n"
        self.assertIsNone(one_tree(text + TAIL))

    def test_refusals(self) -> None:
        """Each planted Dockerfile is refused with its exact message."""
        rows = [
            (
                "no tree",
                HEAD + TAIL,
                "copies no tree; the artifact image carries exactly one (#1298)",
            ),
            (
                "the two-layout image #1298 retired",
                HEAD
                + "COPY staged/${TARGETARCH}/pkgroot/ /\n"
                + "COPY staged/${TARGETARCH}/cnpgroot/ /\n"
                + TAIL,
                (
                    "copies 2 trees; the artifact image carries exactly one"
                    " layout (#1298)"
                ),
            ),
            (
                "the one tree plus an ADD",
                HEAD
                + "COPY staged/${TARGETARCH}/image/ /\n"
                + "add extra.tar /\n"
                + TAIL,
                (
                    "copies 2 trees; the artifact image carries exactly one"
                    " layout (#1298)"
                ),
            ),
            (
                "a distribution's path baked in",
                HEAD
                + "COPY staged/${TARGETARCH}/image/lib/ /usr/lib/postgresql/18/lib/\n"
                + TAIL,
                (
                    "places COPY 'staged/${TARGETARCH}/image/lib/'"
                    " /usr/lib/postgresql/18/lib/; the one tree is"
                    " COPY staged/${TARGETARCH}/image/ / (#1298)"
                ),
            ),
            (
                "the old tree alone",
                HEAD + "COPY staged/${TARGETARCH}/pkgroot/ /\n" + TAIL,
                (
                    "places COPY 'staged/${TARGETARCH}/pkgroot/' /; the one tree"
                    " is COPY staged/${TARGETARCH}/image/ / (#1298)"
                ),
            ),
            (
                "the one tree by ADD",
                HEAD + "ADD staged/${TARGETARCH}/image/ /\n" + TAIL,
                (
                    "places ADD 'staged/${TARGETARCH}/image/' /; the one tree"
                    " is COPY staged/${TARGETARCH}/image/ / (#1298)"
                ),
            ),
            (
                "a flag on the COPY",
                HEAD + "COPY --chown=0:0 staged/${TARGETARCH}/image/ /\n" + TAIL,
                (
                    "places COPY --chown=0:0 'staged/${TARGETARCH}/image/' /;"
                    " the one tree is COPY staged/${TARGETARCH}/image/ / (#1298)"
                ),
            ),
        ]
        for name, text, want in rows:
            with self.subTest(name):
                self.assertEqual(one_tree(text), want)


if __name__ == "__main__":
    unittest.main()

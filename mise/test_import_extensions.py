#!/usr/bin/env python3
"""One import form: a relative specifier names the file on disk (#2107).

A planted repository with the scaffold's own `tsconfig.json`, run through
the belt's real `lint:biome` and `lint:types`. Both directions:

- `./helper.ts` and `./hm.mts` are clean in both tasks. This is the form
  Node loads when it runs `node src/main.ts` directly; it loads a
  specifier as written.
- `./helper.js` and `./hm.mjs` are red in `lint:biome`, by the rule's
  name. That is the form the withdrawn `extensionMappings` wrote, which
  tsc and bundlers resolve and Node does not (ERR_MODULE_NOT_FOUND).

Node itself is not run here: the belt pins no Node, so the suite has
none it could call its own. The `node src/main.ts` measurement is in
the comment above `fix:biome` and in `docs/tooling-verdicts.md`.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import hermetic

BELT = Path(__file__).parent
SCAFFOLD = BELT.parent / "scaffold"
GIT = hermetic.tool("git")
# biome colours its report whatever NO_COLOR says; the rows read words.
STYLE = re.compile(r"\x1b\[[0-9;]*m")

PACKAGE = '{\n  "name": "fixture",\n  "private": true,\n  "type": "module"\n}\n'
HELPER = 'export const helped = (): string => "helper";\n'
HM = 'export const moduled = (): string => "hm";\n'
MAIN = """\
import { helped } from "./helper@TS@";
import { moduled } from "./hm@MTS@";

if (`${helped()} ${moduled()}` !== "helper hm") {
  throw new Error("the imports did not load");
}
"""

def plant(base: Path, ts: str, mts: str) -> Path:
    """Assemble and commit the fixture with the two specifier suffixes.

    Returns:
        The tree.

    """
    tree = base / "tree"
    (tree / "src").mkdir(parents=True)
    # Installed, for a repository that declares no dependencies.
    (tree / "node_modules").mkdir()
    for name, dest in (
        ("mise.toml", "mise.toml"),
        ("tsconfig.json", "tsconfig.json"),
        ("biome.json.stub", "biome.json"),
    ):
        (tree / dest).write_text(
            (SCAFFOLD / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (tree / "package.json").write_text(PACKAGE, encoding="utf-8")
    (tree / "src" / "helper.ts").write_text(HELPER, encoding="utf-8")
    (tree / "src" / "hm.mts").write_text(HM, encoding="utf-8")
    (tree / "src" / "main.ts").write_text(
        MAIN.replace("@TS@", ts).replace("@MTS@", mts),
        encoding="utf-8",
    )
    env = hermetic.env()
    for argv in ([GIT, "init", "-q"], [GIT, "add", "-A"]):
        subprocess.run(argv, cwd=tree, env=env, check=True)
    return tree


def run(tree: Path, task: str) -> subprocess.CompletedProcess[str]:
    """Run one belt task in the tree, the belt global.

    Returns:
        The finished run.

    """
    base = tree.parent
    config = base / "config"
    config.mkdir(exist_ok=True)
    scratch = base / "tmp"
    scratch.mkdir(exist_ok=True)
    env = hermetic.env(
        MISE_CONFIG_DIR=str(config),
        XDG_CONFIG_HOME=str(config),
        MISE_GLOBAL_CONFIG_FILE=str(BELT / "config.toml"),
        MISE_TRUSTED_CONFIG_PATHS=f"{base}:{BELT.parent}",
        MISE_OFFLINE="1",
        MISE_YES="1",
        MISE_AUTO_INSTALL="0",
        MISE_TASK_RUN_AUTO_INSTALL="0",
        NO_COLOR="1",
        TMPDIR=str(scratch),
    )
    return subprocess.run(
        [hermetic.tool("mise"), "run", task],
        cwd=tree,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


class FileOnDisk(unittest.TestCase):
    """`./helper.ts` and `./hm.mts`: the form Node loads."""

    @classmethod
    def setUpClass(cls) -> None:
        """Plant once; both rows read the same tree."""
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tree = plant(Path(cls.tmp.name), ".ts", ".mts")

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the tree."""
        cls.tmp.cleanup()

    def test_lint_biome_is_clean(self) -> None:
        """The rule with no options accepts the source extension."""
        done = run(self.tree, "lint:biome")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("lint:biome: clean across", out)

    def test_lint_types_is_clean(self) -> None:
        """The flag the belt forces makes the form legal (no TS5097)."""
        done = run(self.tree, "lint:types")
        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertNotIn("TS5097", out)
        self.assertIn("lint:types: 1 project(s) checked, 3 files covered", out)


class MappedToJavaScript(unittest.TestCase):
    """`./helper.js` and `./hm.mjs`: the withdrawn mapping's form."""

    def test_lint_biome_names_the_rule(self) -> None:
        """Red by name, for each of the two specifiers."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = plant(Path(tmp), ".js", ".mjs")
            done = run(tree, "lint:biome")
        out = STYLE.sub("", done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, 0, out)
        self.assertEqual(out.count("lint/correctness/useImportExtensions"), 2, out)
        self.assertIn("Safe fix: Add import extension .ts.", out)
        self.assertIn("Safe fix: Add import extension .mts.", out)


if __name__ == "__main__":
    unittest.main()

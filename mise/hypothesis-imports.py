#!/usr/bin/env python3
"""Name the Python files that import Hypothesis (#1332).

`test:python-properties` holds the design condition that a repository's
ordinary tests never import Hypothesis: property tests live in
`prop_*.py`, so the `test` task runs on an interpreter with nothing
installed. This reads each file's syntax tree, so a test that carries
Hypothesis source as a string (a planted fixture) is not an importer,
and an import inside a function or a `try` is.

Stdlib only: it runs on the repository's `python3`, before the
Hypothesis venv is looked for.

Usage:
  hypothesis-imports.py FILE...
  hypothesis-imports.py --given FILE...

Prints each importing file, one per line, and always exits 0, so the
caller reads the list and never a status. A file that does not parse is
printed with the reason: it cannot be cleared.

With `--given` it prints instead each file that takes `given` from
Hypothesis itself (`from hypothesis import given`, from any
`hypothesis.*` module, or `hypothesis.given` by attribute). A property
file takes the belt's typed `given` from `org_hypothesis` (#1604):
Hypothesis's own is typed `Callable[..., None]`, which the belt's mypy
reds on every property.
"""

import ast
import sys
from pathlib import Path


def imports_hypothesis(tree: ast.AST) -> bool:
    """Say whether a module imports Hypothesis anywhere.

    Returns:
        True for `import hypothesis…` or `from hypothesis… import`.

    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        if any(
            name == "hypothesis" or name.startswith("hypothesis.") for name in names
        ):
            return True
    return False


def takes_hypothesis_given(tree: ast.AST) -> bool:
    """Say whether a module takes `given` from Hypothesis itself.

    Returns:
        True for `from hypothesis… import given` or `hypothesis.given`.

    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            from_hypothesis = module == "hypothesis" or module.startswith("hypothesis.")
            if from_hypothesis and any(alias.name == "given" for alias in node.names):
                return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "given"
            and isinstance(node.value, ast.Name)
            and node.value.id == "hypothesis"
        ):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Print the importing files.

    Returns:
        0: the list is the answer.

    """
    names = sys.argv[1:] if argv is None else argv
    judge = imports_hypothesis
    if names[:1] == ["--given"]:
        names, judge = names[1:], takes_hypothesis_given
    for name in names:
        try:
            tree = ast.parse(Path(name).read_text(encoding="utf-8"), filename=name)
        except (OSError, SyntaxError, ValueError) as err:
            print(f"{name} (could not be read: {err})")
            continue
        if judge(tree):
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

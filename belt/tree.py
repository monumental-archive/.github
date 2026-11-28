"""Where the package sits, and the delivered files beside it (#2177).

The files the package reads that are not Python (task bodies, tool
configs, tables) stay where the belt delivers them, named here once
from this file's place in the tree:

- ROOT: the tree the package sits in, the canon checkout;
- MISE: the belt's own directory, `ORG_BELT_DIR` when the tree is the
  belt in play;
- RELEASE: the release scripts.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MISE = ROOT / "mise"
RELEASE = ROOT / "release"

"""The belt's one launcher (#2177).

Every belt Python command is run one way, from the pinned tree:

    "${ORG_PYTHON}" -I "${ORG_BELT_ROOT}/belt" <command> [<argument>…]

`-I` keeps out PYTHONPATH, the user's site directory and the caller's
working directory, so nothing in a consumer's environment can shadow
the package. Python puts this file's own directory first on the path;
this is the one file that touches `sys.path`, and it replaces that
entry with the tree the directory sits in, so `belt` resolves from the
tree the gate stub pins and from nowhere else. The dispatch is
belt/launcher.py's.
"""

import sys
from pathlib import Path

sys.path[0] = str(Path(__file__).resolve().parent.parent)

from belt.launcher import launch  # ruff: ignore[module-import-not-at-top-of-file] - the path is set first

sys.exit(launch(sys.argv[1:]))

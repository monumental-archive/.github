"""The belt's one launcher (#2177).

Every belt Python command is run one way, from the pinned tree:

    "${ORG_PYTHON}" -I "${ORG_CANON_DIR}/belt" <command> [<argument>…]

`-I` keeps out PYTHONPATH, the user's site directory and the caller's
working directory, so nothing in a consumer's environment can shadow
the package. Python puts this file's own directory first on the path;
this is the one file that touches `sys.path`, and it replaces that
entry with the tree the directory sits in, so `belt` resolves from the
tree the gate stub pins and from nowhere else. The path is made
absolute, not resolved: a tree of links (a test's planted belt) is the
tree its own links name. The dispatch is belt/launcher.py's.
"""

import sys
from pathlib import Path

sys.path.pop(0)
sys.path.insert(0, str(Path(__file__).absolute().parent.parent))

from belt.launcher import launch

sys.exit(launch(sys.argv[1:]))

"""Name a belt or release script a helper runs, the one way (#2037).

A helper that runs a script beside it (a `.sh` it sources or runs, a
`.py` it starts under an interpreter) reaches the script's path here,
with the file name as a string literal:

    belt_call(script(belt(), "pgrx-env.sh"), "org_pgrx_union")
    subprocess.run([str(script(HERE, "record-draft.sh"))], check=False)

belt/python_runs.py reads that literal as the run, and nothing else:
a script named in a message, a table or the helper's own usage line
runs nothing. So the gate's tool table (tests/test_gate_tools.py) and
the release job installs (tests/release/test_job_installs.py) see exactly
the scripts a helper runs, and charge their calls.

Stdlib only; every belt module, the release helpers included, reaches
it the one way, `from belt import belt_scripts` (#2177).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def script(home: Path, name: str) -> Path:
    """Name the script a helper runs.

    Returns:
        `name` in `home`, the directory the helper runs it from.

    """
    return home / name

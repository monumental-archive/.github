#!/usr/bin/env python3
"""Only the default branch writes a gate cache; every other ref falls back (#1406).

GitHub scopes a cache to the ref that saved it and lets every ref read
only the default branch's, so each pull request and queue entry that saved
its own copy wrote an entry nothing else could read — and those copies held
every repository at the 10 GB limit, evicting the one copy every ref could
use. These rows hold each cache step in ci.yml to the shape that fixes it:

- every cache WRITE is an `actions/cache/save` step conditioned on the
  workflow's ORG_GATE_CACHE_SAVE, which is true on a push to the default
  branch and nowhere else; an action that writes on its own (combined
  `actions/cache`, a mise-action or setup action left caching) writes
  from every ref and is red;
- every restore carries its prefix fallback, conditioned on
  ORG_GATE_CACHE_FALLBACK — off the default branch only, because a
  fallback restored and saved back on the default branch is the growth
  #1285 and #1330 measured;
- every restore is paired with a save of the same paths under the
  restore's own primary key, and only the gate's three jobs hold caches.

The planted rows each break one property on the real file and assert the
message that names it.
"""

import importlib.util
import re
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
CI = CANON / ".github" / "workflows" / "ci.yml"

_SPEC = importlib.util.spec_from_file_location(
    "job_installs", Path(__file__).with_name("test_job_installs.py")
)
job_installs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(job_installs)

# The jobs #1406 names. A cache anywhere else is a new cache, and the
# design conditions say no release, publish or continuous job gains one.
CACHE_JOBS = {"gate", "pgrx", "coverage-upload"}

DEFAULT_REF = "format('refs/heads/{0}', github.event.repository.default_branch)"
SAVE = f"${{{{ github.event_name == 'push' && github.ref == {DEFAULT_REF} }}}}"
FALLBACK = f"${{{{ github.ref != {DEFAULT_REF} }}}}"

SAVE_IF = re.compile(
    r"^\$\{\{ env\.ORG_GATE_CACHE_SAVE == 'true' && (?P<rest>.*) \}\}$"
)
FALLBACK_KEYS = re.compile(
    r"^\$\{\{ env\.ORG_GATE_CACHE_FALLBACK == 'true' && (?P<prefix>.+) \|\| '' \}\}$"
)
PRIMARY = re.compile(r"^\$\{\{ steps\.(?P<id>[\w-]+)\.outputs\.cache-primary-key \}\}$")


def flat(value: str) -> str:
    """Collapse a value's whitespace, as a folded scalar does.

    Returns:
        The value on one line.

    """
    return " ".join(value.split())


def workflow_env(text: str) -> dict[str, str]:
    """Read the workflow-level `env:` block.

    Returns:
        Each variable to its value, folded onto one line.

    """
    lines = text.splitlines()
    out: dict[str, str] = {}
    if "env:" not in lines:
        return out
    name = ""
    for line in lines[lines.index("env:") + 1 :]:
        if line and not line.startswith(" "):
            break
        m = re.match(r"^  ([A-Z_]+):\s*(.*)$", line)
        if m:
            name = m.group(1)
            out[name] = "" if m.group(2) in {">-", "|-", "|", ">"} else m.group(2)
        elif name and line.startswith("    "):
            out[name] += " " + line.strip()
    return {k: flat(v) for k, v in out.items()}


def writes(step: dict[str, str]) -> str | None:
    """Say how a step writes a cache, if it does.

    Returns:
        `save` for an explicit save step, a reason for a step that writes
        on its own, or None for a step that writes nothing.

    """
    uses = step.get("uses", "")
    if "actions/cache/save@" in uses:
        return "save"
    if "actions/cache@" in uses:
        return "combined actions/cache saves from every ref"
    if "jdx/mise-action@" in uses and step.get("with.cache", "true") != "false":
        return "mise-action with its cache on saves from every ref"
    if "actions/setup-" in uses and step.get("with.cache") not in {None, "false"}:
        return "a setup action's cache saves from every ref"
    return None


def env_findings(text: str) -> list[str]:
    """Hold the two predicates to their decided text.

    Returns:
        One message per predicate that is not the decided one.

    """
    env = workflow_env(text)
    out = []
    if env.get("ORG_GATE_CACHE_SAVE") != SAVE:
        out.append(f"ORG_GATE_CACHE_SAVE is not {SAVE}")
    if env.get("ORG_GATE_CACHE_FALLBACK") != FALLBACK:
        out.append(f"ORG_GATE_CACHE_FALLBACK is not {FALLBACK}")
    return out


def step_findings(job_id: str, step: dict[str, str]) -> list[str]:
    """Hold one step: where its cache is, how it writes, how it falls back.

    Returns:
        One message per breach in the step.

    """
    where = f"{job_id}: '{step.get('name', '?')}'"
    kind = writes(step)
    restore = "actions/cache/restore@" in step.get("uses", "")
    out = []
    if (kind or restore) and job_id not in CACHE_JOBS:
        out.append(f"{where} is a cache outside {sorted(CACHE_JOBS)}")
    if kind and kind != "save":
        out.append(f"{where} writes a cache from every ref: {kind}")
    if kind == "save" and not SAVE_IF.match(flat(step.get("if", ""))):
        out.append(
            f"{where} saves a cache without the default-branch condition"
            " (env.ORG_GATE_CACHE_SAVE == 'true' && ...)"
        )
    keys = flat(step.get("with.restore-keys", ""))
    if restore and not keys:
        out.append(f"{where} restores off the default branch with no fallback")
    elif restore and not FALLBACK_KEYS.match(keys):
        out.append(
            f"{where} carries a fallback on the default branch"
            " (restore-keys not conditioned on env.ORG_GATE_CACHE_FALLBACK)"
        )
    return out


def pair_findings(job_id: str, job: list[str]) -> list[str]:
    """Pair each save with the restore whose key it writes.

    Returns:
        One message per save with no restore, per save of other paths, and
        per restore nothing saves.

    """
    steps = job_installs.steps(job)
    uses = [(s, s.get("uses", "")) for s in steps]
    restores = {s.get("id", ""): s for s, u in uses if "actions/cache/restore@" in u}
    out = []
    paired = set()
    for save in (s for s, u in uses if "actions/cache/save@" in u):
        where = f"{job_id}: '{save.get('name', '?')}'"
        m = PRIMARY.match(flat(save.get("with.key", "")))
        src = restores.get(m.group("id")) if m else None
        if m is None or src is None:
            out.append(f"{where} saves under a key no restore in the job computed")
            continue
        paired.add(m.group("id"))
        if flat(src.get("with.path", "")) != flat(save.get("with.path", "")):
            out.append(f"{where} saves other paths than '{src.get('name')}' restores")
    out.extend(
        f"{job_id}: '{src.get('name', '?')}' is restored and never saved,"
        " even on the default branch"
        for sid, src in restores.items()
        if sid not in paired
    )
    return out


def findings(text: str) -> list[str]:
    """Hold every cache step in a workflow to #1406's shape.

    Returns:
        One message per breach; empty when the workflow conforms.

    """
    out = env_findings(text)
    for job_id, job in job_installs.jobs(text).items():
        for step in job_installs.steps(job):
            out.extend(step_findings(job_id, step))
        out.extend(pair_findings(job_id, job))
    return out


def real() -> str:
    """Read ci.yml.

    Returns:
        The workflow as the tree holds it.

    """
    return CI.read_text("utf-8")


def plant(old: str, new: str) -> str:
    """Replace, in the real ci.yml, text that must be there.

    Returns:
        The workflow with the first `old` replaced.

    Raises:
        AssertionError: `old` is not in the file — a plant that missed
            would prove nothing.

    """
    text = real()
    if old not in text:
        raise AssertionError(old)
    return text.replace(old, new, 1)


TOOL_SAVE_IF = (
    "        if: ${{ env.ORG_GATE_CACHE_SAVE == 'true'"
    " && steps.tool-cache.outputs.cache-hit != 'true' }}\n"
)
GO_KEYS = (
    "restore-keys: ${{ env.ORG_GATE_CACHE_FALLBACK == 'true'"
    " && format('gate-go-v2-{0}-', runner.os) || '' }}"
)
TOOL_KEYS = (
    "          restore-keys: ${{ env.ORG_GATE_CACHE_FALLBACK == 'true'"
    " && steps.tool-key.outputs.prefix || '' }}\n"
)
GO_SAVE_KEY = "          key: ${{ steps.go-cache.outputs.cache-primary-key }}\n"
TOOL_SAVE = "'Save the tool cache (default branch only)'"
TOOL_RESTORE = "'Restore the tool cache (unsigned gate only)'"
GO_SAVE = "'Save the Go caches (default branch only)'"
GO_RESTORE = "'Restore the Go caches (unsigned gate only)'"
EVERY_REF = "writes a cache from every ref"


class Real(unittest.TestCase):
    """The tree's ci.yml conforms, and its caches are where #1406 says."""

    def test_ci_conforms(self) -> None:
        """No breach in the real workflow."""
        self.assertEqual([], findings(real()))

    def test_every_job_has_its_caches(self) -> None:
        """The three jobs each restore and save their tool cache."""
        for job_id in CACHE_JOBS:
            job = job_installs.jobs(real())[job_id]
            names = [s.get("name") for s in job_installs.steps(job)]
            with self.subTest(job=job_id):
                self.assertIn(TOOL_RESTORE.strip("'"), names)
                self.assertIn(TOOL_SAVE.strip("'"), names)


class Planted(unittest.TestCase):
    """Each property, broken once on the real file, is named."""

    def assert_named(self, text: str, message: str) -> None:
        """Assert the breach is reported with its message."""
        got = findings(text)
        self.assertTrue(any(message in g for g in got), f"{message!r} not in {got}")

    def test_an_unconditioned_save(self) -> None:
        """A save with its condition dropped writes from every ref."""
        self.assert_named(
            plant(TOOL_SAVE_IF, ""),
            f"gate: {TOOL_SAVE} saves a cache without the default-branch condition",
        )

    def test_a_save_conditioned_on_something_else(self) -> None:
        """A condition that is not the default branch's is no condition."""
        text = plant(
            "if: ${{ env.ORG_GATE_CACHE_SAVE == 'true' && steps.tool-cache",
            "if: ${{ github.event_name != 'schedule' && steps.tool-cache",
        )
        self.assert_named(text, "saves a cache without the default-branch condition")

    def test_a_save_in_a_fourth_job(self) -> None:
        """A save step added to the plan job is a cache outside the three."""
        notice = '          echo "::notice::pgrx legs planned: ${majors}"\n'
        save = (
            "      - name: Save the plan cache (default branch only)\n"
            "        if: ${{ env.ORG_GATE_CACHE_SAVE == 'true' && always() }}\n"
            "        uses: actions/cache/save@"
            "55cc8345863c7cc4c66a329aec7e433d2d1c52a9\n"
            "        with:\n"
            "          path: target\n"
            "          key: ${{ steps.plan-cache.outputs.cache-primary-key }}\n"
        )
        self.assert_named(
            plant(notice, notice + save),
            "plan: 'Save the plan cache (default branch only)' is a cache outside"
            " ['coverage-upload', 'gate', 'pgrx']",
        )

    def test_a_fallback_on_the_default_branch(self) -> None:
        """An unconditioned restore-keys falls back on the default branch too."""
        self.assert_named(
            plant(GO_KEYS, "restore-keys: gate-go-v2-${{ runner.os }}-"),
            f"gate: {GO_RESTORE} carries a fallback on the default branch",
        )

    def test_a_restore_without_its_fallback(self) -> None:
        """A restore with no restore-keys leaves a pull request cold."""
        self.assert_named(
            plant(TOOL_KEYS, ""),
            f"gate: {TOOL_RESTORE} restores off the default branch with no fallback",
        )

    def test_the_combined_action_returns(self) -> None:
        """The combined actions/cache saves in its post step from any ref."""
        self.assert_named(
            plant("actions/cache/restore@", "actions/cache@"),
            f"{EVERY_REF}: combined actions/cache saves from every ref",
        )

    def test_mise_action_caching_again(self) -> None:
        """mise-action's own cache saves from every ref."""
        self.assert_named(
            plant("          cache: false\n", "          cache: true\n"),
            f"{EVERY_REF}: mise-action with its cache on",
        )

    def test_mise_action_cache_by_default(self) -> None:
        """mise-action caches unless told not to."""
        self.assert_named(
            plant("          cache: false\n", ""),
            f"{EVERY_REF}: mise-action with its cache on",
        )

    def test_a_restore_never_saved(self) -> None:
        """A restore with no save starves the default branch's copy."""
        text = plant(GO_SAVE_KEY, "          key: gate-go-v2-${{ runner.os }}-\n")
        self.assert_named(text, f"gate: {GO_RESTORE} is restored and never saved")
        self.assert_named(
            text, f"gate: {GO_SAVE} saves under a key no restore in the job computed"
        )

    def test_a_save_of_other_paths(self) -> None:
        """A save must write what its restore reads, or the next hit misses."""
        paths = "            ~/go/pkg/mod\n            ~/.cache/go-build\n"
        self.assert_named(
            plant(paths + GO_SAVE_KEY, "            ~/go/pkg/mod\n" + GO_SAVE_KEY),
            f"gate: {GO_SAVE} saves other paths than",
        )

    def test_a_loosened_save_predicate(self) -> None:
        """A SAVE that is true off the default branch is named."""
        self.assert_named(
            plant(
                "    ${{ github.event_name == 'push'\n",
                "    ${{ github.event_name != 'schedule'\n",
            ),
            "ORG_GATE_CACHE_SAVE is not",
        )

    def test_a_loosened_fallback_predicate(self) -> None:
        """A FALLBACK that is true on the default branch is named."""
        self.assert_named(
            plant("    ${{ github.ref != format(", "    ${{ github.ref == format("),
            "ORG_GATE_CACHE_FALLBACK is not",
        )


if __name__ == "__main__":
    unittest.main()

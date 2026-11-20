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
  restore's own primary key, and only the gate's three jobs hold caches;
- in the gate's matrix (#1740), the lint and test legs each keep their
  own cargo and Go caches, keyed by leg, and the test leg alone saves
  the tool cache every leg restores;
- every cargo cache in every workflow names the compiler that fills it
  (#1864): its key and fallback prefix carry the `rustc -vV` digest
  before Cargo.lock's hash, from a step after the job's install.

The planted rows each break one property on the real file and assert the
message that names it.
"""

import re
import sys
import unittest
from pathlib import Path

import test_job_installs as job_installs
from test_job_installs import Job, Step, field, inputs, parsed, table

# Appended, so this file's own directory and the standard library
# come first.
sys.path.append(str(Path(__file__).resolve().parents[1] / "mise"))

import workflow_yaml

CANON = Path(__file__).resolve().parent.parent
CI = CANON / ".github" / "workflows" / "ci.yml"


# The jobs #1406 names, less the coverage upload, which installs and
# builds nothing since #1528. A cache anywhere else is a new cache, and
# the design conditions say no release, publish or continuous job gains
# one.
CACHE_JOBS = {"gate", "pgrx"}
# A workflow with nothing to report.
NONE: list[str] = []

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


def writes(step: Step) -> str | None:
    """Say how a step writes a cache, if it does.

    Returns:
        `save` for an explicit save step, a reason for a step that writes
        on its own, or None for a step that writes nothing.

    """
    uses = field(step, "uses")
    cache = inputs(step).get("cache")
    if "actions/cache/save@" in uses:
        return "save"
    if "actions/cache@" in uses:
        return "combined actions/cache saves from every ref"
    if "jdx/mise-action@" in uses and cache != "false":
        return "mise-action with its cache on saves from every ref"
    if "actions/setup-" in uses and cache not in {None, "false"}:
        return "a setup action's cache saves from every ref"
    return None


def env_findings(document: dict[str, object]) -> list[str]:
    """Hold the two predicates to their decided text.

    Returns:
        One message per predicate that is not the decided one.

    """
    env = {k: flat(v) for k, v in table(document, "env").items()}
    out = []
    if env.get("ORG_GATE_CACHE_SAVE") != SAVE:
        out.append(f"ORG_GATE_CACHE_SAVE is not {SAVE}")
    if env.get("ORG_GATE_CACHE_FALLBACK") != FALLBACK:
        out.append(f"ORG_GATE_CACHE_FALLBACK is not {FALLBACK}")
    return out


def step_findings(job_id: str, step: Step) -> list[str]:
    """Hold one step: where its cache is, how it writes, how it falls back.

    Returns:
        One message per breach in the step.

    """
    where = f"{job_id}: '{field(step, 'name') or '?'}'"
    kind = writes(step)
    restore = "actions/cache/restore@" in field(step, "uses")
    out = []
    if (kind or restore) and job_id not in CACHE_JOBS:
        out.append(f"{where} is a cache outside {sorted(CACHE_JOBS)}")
    if kind and kind != "save":
        out.append(f"{where} writes a cache from every ref: {kind}")
    if kind == "save" and not SAVE_IF.match(flat(field(step, "if"))):
        out.append(
            f"{where} saves a cache without the default-branch condition"
            " (env.ORG_GATE_CACHE_SAVE == 'true' && ...)"
        )
    keys = flat(inputs(step).get("restore-keys", ""))
    if restore and not keys:
        out.append(f"{where} restores off the default branch with no fallback")
    elif restore and not FALLBACK_KEYS.match(keys):
        out.append(
            f"{where} carries a fallback on the default branch"
            " (restore-keys not conditioned on env.ORG_GATE_CACHE_FALLBACK)"
        )
    return out


def pair_findings(job_id: str, job: Job) -> list[str]:
    """Pair each save with the restore whose key it writes.

    Returns:
        One message per save with no restore, per save of other paths, and
        per restore nothing saves.

    """
    steps = workflow_yaml.steps(job)
    uses = [(s, field(s, "uses")) for s in steps]
    restores = {field(s, "id"): s for s, u in uses if "actions/cache/restore@" in u}
    out = []
    paired = set()
    for save in (s for s, u in uses if "actions/cache/save@" in u):
        where = f"{job_id}: '{field(save, 'name') or '?'}'"
        m = PRIMARY.match(flat(inputs(save).get("key", "")))
        src = restores.get(m.group("id")) if m else None
        if m is None or src is None:
            out.append(f"{where} saves under a key no restore in the job computed")
            continue
        paired.add(m.group("id"))
        if flat(inputs(src).get("path", "")) != flat(inputs(save).get("path", "")):
            out.append(
                f"{where} saves other paths than '{field(src, 'name')}' restores"
            )
    out.extend(
        f"{job_id}: '{field(src, 'name') or '?'}' is restored and never saved,"
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
    document = parsed(text)
    out = env_findings(document)
    for job_id, job in workflow_yaml.jobs(document).items():
        for step in workflow_yaml.steps(job):
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
    "        if: >-\n"
    "          ${{ env.ORG_GATE_CACHE_SAVE == 'true' && matrix.leg == 'test'\n"
    "          && steps.tool-cache.outputs.cache-hit != 'true' }}\n"
)
GO_KEYS = (
    "restore-keys: ${{ env.ORG_GATE_CACHE_FALLBACK == 'true'"
    " && format('gate-go-v2-{0}-{1}-', matrix.leg, runner.os) || '' }}"
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
        self.assertEqual(NONE, findings(real()))

    def test_every_job_has_its_caches(self) -> None:
        """The two jobs each restore and save their tool cache."""
        for job_id in CACHE_JOBS:
            job = workflow_yaml.jobs(parsed(real()))[job_id]
            names = job_installs.names(job)
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
        notice = '          org_notice "pgrx legs planned: ${majors}"\n'
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
            " ['gate', 'pgrx']",
        )

    def test_a_fallback_on_the_default_branch(self) -> None:
        """An unconditioned restore-keys falls back on the default branch too."""
        self.assert_named(
            plant(
                GO_KEYS, "restore-keys: gate-go-v2-${{ matrix.leg }}-${{ runner.os }}-"
            ),
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
        text = plant(
            GO_SAVE_KEY,
            "          key: gate-go-v2-${{ matrix.leg }}-${{ runner.os }}-\n",
        )
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


# WHICH LEG HOLDS WHICH CACHE (#1740). The gate is a matrix of three
# legs. Clippy's `target` and the test build's are different contents,
# so the lint and test legs each keep a cargo and a Go cache under a name
# carrying the leg, and the properties leg, which builds neither, keeps
# none. Every leg installs the same tools under the one key, so every leg
# restores the tool cache and one, the test leg, saves it.
BUILD_LEGS = "matrix.leg != 'properties'"
LEG_CACHES = {
    "Restore the Go caches (unsigned gate only)": BUILD_LEGS,
    "Save the Go caches (default branch only)": BUILD_LEGS,
    "Restore the cargo caches (unsigned gate only)": BUILD_LEGS,
    "Save the cargo caches (default branch only)": BUILD_LEGS,
    "Save the tool cache (default branch only)": "matrix.leg == 'test'",
}
LEG_KEYS = {
    "go-cache": "gate-go-v2-${{ matrix.leg }}-",
    "cargo-cache": "gate-cargo-${{ matrix.leg }}-",
}
LEG_PREFIXES = {
    "go-cache": "format('gate-go-v2-{0}-{1}-', matrix.leg, runner.os)",
    "cargo-cache": (
        "format('gate-cargo-{0}-{1}-{2}-', matrix.leg, runner.os,"
        " steps.rustc.outputs.digest)"
    ),
}


def leg_cache_findings(text: str) -> list[str]:
    """Hold the gate's caches to the legs that build them.

    Returns:
        One line per cache step run on a leg it does not belong to, per
        build cache whose key or fallback prefix omits the leg, and for a
        tool cache restored on fewer than every leg.

    """
    gate = workflow_yaml.steps(workflow_yaml.jobs(parsed(text))["gate"])
    by_name = {field(s, "name"): s for s in gate}
    by_id = {field(s, "id"): s for s in gate if field(s, "id")}
    out = []
    for name, leg in LEG_CACHES.items():
        if leg not in flat(field(by_name.get(name, {}), "if")):
            out.append(f"gate: '{name}' runs on every leg, not only where {leg}")
    for sid, key in LEG_KEYS.items():
        step = by_id.get(sid, {})
        if not inputs(step).get("key", "").startswith(key):
            out.append(f"gate: the {sid} key does not start {key}")
        if LEG_PREFIXES[sid] not in flat(inputs(step).get("restore-keys", "")):
            out.append(f"gate: the {sid} fallback is not {LEG_PREFIXES[sid]}")
    if "matrix.leg" in field(by_name.get(TOOL_RESTORE.strip("'"), {}), "if"):
        out.append(f"gate: {TOOL_RESTORE} is not restored by every leg")
    return out


class LegCaches(unittest.TestCase):
    """Each gate leg holds the caches it builds (#1740)."""

    def test_the_real_gate_conforms(self) -> None:
        """Two build legs keyed by leg, one tool-cache saver."""
        self.assertEqual(NONE, leg_cache_findings(real()))

    def test_planted_breaches_are_named(self) -> None:
        """Each property broken once on the real file, by its message."""
        rows = [
            (
                "a shared cargo key",
                (
                    "key: gate-cargo-${{ matrix.leg }}-${{ runner.os }}",
                    "key: gate-cargo-${{ runner.os }}",
                ),
                (
                    "gate: the cargo-cache key does not start"
                    " gate-cargo-${{ matrix.leg }}-"
                ),
            ),
            (
                "a shared Go fallback",
                (GO_KEYS, GO_KEYS.replace("{0}-{1}-', matrix.leg, ", "{0}-', ")),
                (
                    "gate: the go-cache fallback is not"
                    " format('gate-go-v2-{0}-{1}-', matrix.leg, runner.os)"
                ),
            ),
            (
                "every leg saves the tools",
                (" && matrix.leg == 'test'\n", "\n"),
                (
                    f"gate: {TOOL_SAVE} runs on every leg, not only where"
                    " matrix.leg == 'test'"
                ),
            ),
            (
                "the properties leg restores cargo",
                (
                    (
                        "id: cargo-cache\n        if: ${{ matrix.leg != 'properties'"
                        " && hashFiles('Cargo.lock')"
                    ),
                    "id: cargo-cache\n        if: ${{ hashFiles('Cargo.lock')",
                ),
                (
                    "gate: 'Restore the cargo caches (unsigned gate only)' runs on"
                    " every leg, not only where matrix.leg != 'properties'"
                ),
            ),
            (
                "one leg restores the tools",
                (
                    "        id: tool-cache\n",
                    "        id: tool-cache\n        if: ${{ matrix.leg == 'test' }}\n",
                ),
                f"gate: {TOOL_RESTORE} is not restored by every leg",
            ),
        ]
        for label, (old, new), message in rows:
            with self.subTest(row=label):
                want: list[str] = [message]
                self.assertEqual(want, leg_cache_findings(plant(old, new)))


# THE CARGO CACHES NAME THEIR COMPILER (#1864). `target` is what the
# compiler that built it made, so a key on Cargo.lock alone restored the
# previous toolchain's objects after a bump and coverage read both. Every
# cache holding a cargo path, in any workflow, keys on the digest
# `gate_cache_key.py cargo` writes, before Cargo.lock's hash, and its
# fallback prefix carries the digest too. The step runs after the job's
# install, so the `rustc` it reads is the one the tasks build with, and
# under the restore's own condition, so it runs wherever the restore does.
CARGO_PATHS = ("target", "~/.cargo/registry")
DIGEST = "steps.rustc.outputs.digest"
COMPILER_KEY = f"${{{{ {DIGEST} }}}}-${{{{ hashFiles('Cargo.lock') }}}}"
WORKFLOWS = CANON / ".github" / "workflows"


def holds_cargo(step: Step) -> bool:
    """Say whether a cache step holds a cargo path.

    Returns:
        True for an actions/cache step whose paths include one.

    """
    if "actions/cache" not in field(step, "uses"):
        return False
    paths = inputs(step).get("path", "").split()
    return any(p in CARGO_PATHS for p in paths)


def compiler_findings(text: str, where: str = "ci.yml") -> list[str]:
    """Hold each cargo cache restore to the compiler's digest.

    Returns:
        One line per restore whose key or fallback omits the digest, and
        per job whose digest step is missing, misplaced or conditioned
        otherwise than the restore.

    """
    out = []
    for job_id, job in workflow_yaml.jobs(parsed(text)).items():
        steps = workflow_yaml.steps(job)
        restores = [
            (i, s)
            for i, s in enumerate(steps)
            if holds_cargo(s) and "/save@" not in field(s, "uses")
        ]
        if not restores:
            continue
        name = f"{where}: {job_id}"
        installs = [i for i, s in enumerate(steps) if s in job_installs.mise_steps(job)]
        named = [(i, s) for i, s in enumerate(steps) if field(s, "id") == "rustc"]
        if not named or "gate_cache_key.py cargo" not in field(named[0][1], "run"):
            out.append(f"{name}: no `gate_cache_key.py cargo` step with id rustc")
            continue
        at, digest_step = named[0]
        if not installs or at < installs[0]:
            out.append(f"{name}: the rustc digest is read before the install")
        for i, restore in restores:
            label = f"{name}: '{field(restore, 'name')}'"
            if COMPILER_KEY not in inputs(restore).get("key", ""):
                out.append(f"{label} key does not end {COMPILER_KEY}")
            if DIGEST not in flat(inputs(restore).get("restore-keys", "")):
                out.append(f"{label} fallback does not carry {DIGEST}")
            if at > i:
                out.append(f"{label} restores before the rustc digest is read")
            if flat(field(digest_step, "if")) != flat(field(restore, "if")):
                out.append(f"{label} runs under another condition than its digest")
    return out


class Compiler(unittest.TestCase):
    """Every cargo cache names the compiler that fills it (#1864)."""

    def test_the_real_gate_conforms(self) -> None:
        """Both jobs' cargo caches key on the digest."""
        self.assertEqual(NONE, compiler_findings(real()))

    def test_every_workflow_conforms(self) -> None:
        """No workflow holds a cargo cache keyed otherwise."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            with self.subTest(workflow=path.name):
                text = path.read_text("utf-8")
                self.assertEqual(NONE, compiler_findings(text, path.name))

    def test_the_gate_and_pgrx_hold_the_cargo_caches(self) -> None:
        """The surface today: one restore in each of two jobs."""
        held = {
            job_id
            for job_id, job in workflow_yaml.jobs(parsed(real())).items()
            for s in workflow_yaml.steps(job)
            if holds_cargo(s)
        }
        self.assertEqual({"gate", "pgrx"}, held)

    def test_planted_breaches_are_named(self) -> None:
        """Each property broken once on the real file, by its message."""
        gate = "ci.yml: gate: 'Restore the cargo caches (unsigned gate only)'"
        pgrx = "ci.yml: pgrx: 'Restore the cargo caches (unsigned gate only)'"
        rows = [
            (
                "a key on Cargo.lock alone",
                (
                    "${{ runner.os }}-${{ steps.rustc.outputs.digest }}-",
                    "${{ runner.os }}-",
                ),
                f"{gate} key does not end {COMPILER_KEY}",
            ),
            (
                "a fallback across compilers",
                (
                    (
                        "format('gate-pgrx-{0}-{1}-{2}-', matrix.major, runner.os,"
                        " steps.rustc.outputs.digest)"
                    ),
                    "format('gate-pgrx-{0}-{1}-', matrix.major, runner.os)",
                ),
                f"{pgrx} fallback does not carry {DIGEST}",
            ),
            (
                "the digest on the properties leg too",
                (
                    (
                        "        id: rustc\n"
                        "        if: ${{ matrix.leg != 'properties'"
                        " && hashFiles('Cargo.lock') != '' }}\n"
                    ),
                    "        id: rustc\n",
                ),
                f"{gate} runs under another condition than its digest",
            ),
            (
                "no digest step",
                (
                    "'\"${ORG_PYTHON}\" .org-canon/mise/gate_cache_key.py cargo'",
                    "'true'",
                ),
                "ci.yml: gate: no `gate_cache_key.py cargo` step with id rustc",
            ),
        ]
        for label, (old, new), message in rows:
            with self.subTest(row=label):
                self.assertEqual([message], compiler_findings(plant(old, new)))

    def test_a_digest_read_before_the_install_is_named(self) -> None:
        """Planted: the step moved above mise-action reads no toolchain."""
        step = (
            "      - name: Name the compiler the cargo caches hold\n"
            "        id: rustc\n"
            "        run: '\"${ORG_PYTHON}\" .org-canon/mise/gate_cache_key.py cargo'\n"
            "\n"
        )
        install = "      - name: Install mise and the leg's tools\n"
        text = plant(step, "")
        text = text.replace(install, step + install, 1)
        self.assertEqual(
            ["ci.yml: pgrx: the rustc digest is read before the install"],
            compiler_findings(text),
        )


def install_key_findings(text: str) -> list[str]:
    """Name a listed job whose cache key digests a list it does not install.

    A job that installs a list (#1408) passes the list to the cache-name
    step as ORG_TOOL_INSTALL, and the key carries its digest. The two
    copies must be the same words, or the key names a cache of tools the
    job does not install.

    Returns:
        One line per job whose two copies differ.

    """
    out = []
    for job_id, job in workflow_yaml.jobs(parsed(text)).items():
        steps = workflow_yaml.steps(job)
        named = [s for s in steps if field(s, "id") == "tool-key"]
        installs = job_installs.mise_steps(job)
        if not named or not installs:
            continue
        listed = flat(inputs(installs[0]).get("install_args", ""))
        keyed = flat(table(named[0], "env").get("ORG_TOOL_INSTALL", ""))
        if listed != keyed:
            out.append(
                f"{job_id}: the cache key digests '{keyed}' and the job"
                f" installs '{listed}'"
            )
    return out


class InstallKeys(unittest.TestCase):
    """A listed job's cache key digests what it installs (#1408)."""

    def test_the_real_jobs_agree(self) -> None:
        """The legs key by their own install list."""
        self.assertEqual(NONE, install_key_findings(real()))

    def test_the_listed_jobs_pass_their_list(self) -> None:
        """The listed job names a list; the gate names none."""
        jobs = workflow_yaml.jobs(parsed(real()))
        for job_id, wanted in (("pgrx", True),):
            (named,) = [
                s
                for s in workflow_yaml.steps(jobs[job_id])
                if field(s, "id") == "tool-key"
            ]
            self.assertEqual(wanted, bool(table(named, "env").get("ORG_TOOL_INSTALL")))
            self.assertIn("--install", field(named, "run"))
        (gate,) = [
            s for s in workflow_yaml.steps(jobs["gate"]) if field(s, "id") == "tool-key"
        ]
        self.assertNotIn("--install", field(gate, "run"))

    def test_a_list_the_key_does_not_carry_is_named(self) -> None:
        """Planted: the leg installs a tool its key's list leaves out."""
        listed = "jq aqua:taiki-e/cargo-llvm-cov"
        pins = "${{ steps.pins.outputs.install }}"
        text = plant(
            f"          install_args: {listed} {pins}",
            f"          install_args: {listed} typos {pins}",
        )
        want: list[str] = [
            (
                f"pgrx: the cache key digests '{listed} {pins}'"
                " and the job installs"
                f" '{listed} typos {pins}'"
            )
        ]
        self.assertEqual(want, install_key_findings(text))


if __name__ == "__main__":
    unittest.main()

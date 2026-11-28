"""The required check's verdict step, every branch (#1386).

`ci.yml`'s "Every part of the gate passed" step is `ci / ci`, the one
context the org ruleset names, so it must be red unless every part of
the gate was green. It stays shell in the workflow (it only prints what
other jobs computed), so these rows lift its `run:` block out of the
YAML by step name and run it under bash. Reading it out of the YAML is
what keeps the test on the code that ships.
"""

from __future__ import annotations

import os
import subprocess

from tests import support

WORKFLOW = support.CANON / ".github/workflows/ci.yml"
STEP = "- name: Every part of the gate passed"


def step_script() -> str:
    """Lift the verdict step's `run: |` block out of the workflow.

    Returns:
        The block as bash, dedented to column zero.

    Raises:
        AssertionError: No single step of that name, no `run: |` under
            it, or an empty block. Loud, never a skip: a test that found
            nothing to run reads as a passing one.

    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == STEP]
    if len(starts) != 1:
        msg = f"expected one {STEP!r} in {WORKFLOW}, found {len(starts)}"
        raise AssertionError(msg)
    runs = [i for i in range(starts[0] + 1, len(lines)) if lines[i].strip() == "run: |"]
    if not runs:
        msg = f"no 'run: |' under {STEP!r}"
        raise AssertionError(msg)
    key = lines[runs[0]]
    key_indent = len(key) - len(key.lstrip())
    block: list[str] = []
    for line in lines[runs[0] + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= key_indent:
            break
        block.append(line)
    while block and not block[-1].strip():
        block.pop()
    if not block:
        msg = f"empty run block under {STEP!r}"
        raise AssertionError(msg)
    indent = len(block[0]) - len(block[0].lstrip())
    return "\n".join(line[indent:] for line in block) + "\n"


# Each row: the jobs' results, and the verdict printed.
def verdict(  # ruff: ignore[too-many-arguments] -- one keyword per job result
    plan: str,
    gate: str,
    pgrx: str,
    *,
    majors: str = "[16, 17]",
    coverage: str = "skipped",
    split: str = "false",
    env: support.Env,
) -> subprocess.CompletedProcess[str]:
    """Run the lifted step on one set of job results.

    Returns:
        The run.

    """
    bash = support.tool("bash")
    return subprocess.run(
        [bash, "-c", step_script()],
        env=env(
            PATH=os.environ["PATH"],
            PLAN=plan,
            GATE=gate,
            PGRX=pgrx,
            MAJORS=majors,
            COVERAGE=coverage,
            SPLIT=split,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_every_part_green(env: support.Env) -> None:
    """Plan, gate and every planned leg passed."""
    done = verdict("success", "success", "success", env=env)
    assert done.returncode == 0, done.stderr
    assert (
        done.stdout == "ci: plan, gate and every pgrx leg (\\[16\\,\\ 17\\]) passed\n"
    )


def test_no_legs_planned_and_none_ran(env: support.Env) -> None:
    """A tree with no extension skips the legs, and that is green."""
    done = verdict("success", "success", "skipped", majors="[]", env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "ci: plan, gate and every pgrx leg (\\[\\]) passed\n"


def test_planned_legs_that_were_skipped(env: support.Env) -> None:
    """A skipped required leg must not count as passing."""
    done = verdict("success", "success", "skipped", env=env)
    assert done.returncode == 1, done.stderr
    assert done.stdout == (
        "::error::pgrx legs were skipped although majors were planned\n"
        "  planned: \\[16\\,\\ 17\\]\n"
    )


def test_a_failed_plan(env: support.Env) -> None:
    """The plan's result is named."""
    done = verdict("failure", "skipped", "skipped", majors="", env=env)
    assert done.returncode == 1, done.stderr
    assert done.stdout == (
        "::error::a job the gate needs did not succeed\n  job plan: failure\n"
        "::error::a job the gate needs did not succeed\n  job gate: skipped\n"
        "::error::pgrx legs were skipped although majors were planned\n"
        "  planned: ''\n"
    )


def test_a_cancelled_gate(env: support.Env) -> None:
    """The gate's result is named."""
    done = verdict("success", "cancelled", "success", env=env)
    assert done.returncode == 1, done.stderr
    assert (
        done.stdout
        == "::error::a job the gate needs did not succeed\n  job gate: cancelled\n"
    )


def test_a_failed_leg(env: support.Env) -> None:
    """A leg's failure is named."""
    done = verdict("success", "success", "failure", env=env)
    assert done.returncode == 1, done.stderr
    assert done.stdout == (
        "::error::a job the gate needs did not succeed\n  job pgrx\\ legs: failure\n"
    )


def test_sharded_tests_judged(env: support.Env) -> None:
    """A split leg's coverage job passed: green (#2049, #2160)."""
    done = verdict(
        "success", "success", "success", coverage="success", split="true", env=env
    )
    assert done.returncode == 0, done.stderr


def test_sharded_tests_never_judged(env: support.Env) -> None:
    """A split leg whose coverage job was skipped is red, by name."""
    done = verdict("success", "success", "success", split="true", env=env)
    assert done.returncode == 1, done.stderr
    assert (
        done.stdout
        == "::error::a job the gate needs did not succeed\n  job coverage: skipped\n"
    )


def test_a_failed_coverage_job(env: support.Env) -> None:
    """The coverage job's failure is named."""
    done = verdict(
        "success", "success", "success", coverage="failure", split="true", env=env
    )
    assert done.returncode == 1, done.stderr
    assert (
        done.stdout
        == "::error::a job the gate needs did not succeed\n  job coverage: failure\n"
    )

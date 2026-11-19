#!/usr/bin/env python3
"""Table tests for lint:release-phases' tag-order rule (#1280).

A registry tag is the last thing a release does. edtf v1.3.2 and v1.3.3
burned at the crates.io upload while the extension images' tag job,
which needed only the commit point, had already named their digests:
pullable images of versions that were never released. The tag jobs now
need `attach`, and the lint holds every job that calls tag-images.yml
to that edge. An orchestrator with no attach job — the continuous
archetype, a stream with no release — holds its tag job to a job that
calls the signer's sign.yml instead: the signature is the last thing
before the name.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted tree — not a
copy of it — so a change to the lint has to face these rows. The Tera
raw markers are dropped the way mise's render drops them.

Each red row asserts the whole of stderr, not just the exit status: a
planted row scored on exit alone can pass for the wrong reason (another
rule tripping instead). The green rows plant the canon's real
publish.yml and continuous.yml, so the orchestrators and the lint
cannot drift apart.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import]
import tempfile
import tomllib
import unittest
from pathlib import Path

import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")

FLOWS = CANON / ".github" / "workflows"
PUBLISH = (FLOWS / "publish.yml").read_text(encoding="utf-8")
CONTINUOUS = (FLOWS / "continuous.yml").read_text(encoding="utf-8")

# The smallest graph the barrier rules accept, with one tag job whose
# needs are filled in per row.
FIXTURE = """\
on:
  workflow_call:
jobs:
  # phase: admit
  guard:
    runs-on: ubuntu-26.04
  # phase: prove
  repro-gate:
    needs: guard
  # phase: commit-point
  commit-point:
    needs:
      - guard
      - repro-gate
  # phase: publish
  publish-crates:
    needs: commit-point
    if: ${{ needs.commit-point.result == 'success' }}
  # phase: attest
  attach:
    needs: publish-crates
  # phase: publish
  tag-extension-images:
    needs:
TAG_NEEDS
    if: ${{ needs.commit-point.result == 'success' }}
    uses: ./.github/workflows/tag-images.yml # zizmor: ignore[self-repository]
"""

# The continuous shape: no attach, a signing job, and the tag job.
STREAM = """\
on:
  workflow_call:
jobs:
  # phase: admit
  guard:
    runs-on: ubuntu-26.04
  # phase: prove
  repro-gate:
    needs: guard
  # phase: commit-point
  commit-point:
    needs:
      - guard
      - repro-gate
  # phase: publish
  assemble:
    needs: commit-point
    if: ${{ needs.commit-point.result == 'success' }}
  # phase: attest
  sign:
    needs: assemble
    uses: monumental-archive/signer/.github/workflows/sign.yml@SHA # main
  # phase: publish
  tag:
    needs:
TAG_NEEDS
    if: ${{ needs.commit-point.result == 'success' }}
    uses: ./.github/workflows/tag-images.yml # zizmor: ignore[self-repository]
"""

TRAILER = (
    "  every publish/attest job must reach commit-point (#349 finding 1);\n"
    "  see the PHASES header in the orchestrator named above\n"
)


def message(job: str) -> str:
    """Return the whole of stderr for one tag job missing its attach edge.

    Returns:
        The lint's red output, line for line.

    """
    return (
        "lint:release-phases: .github/workflows/publish.yml: the commitment"
        " barrier is unwired:\n"
        f"  tag job {job} calls tag-images.yml without needing attach"
        " — a registry tag is the last thing a release does (#1280)\n" + TRAILER
    )


def stream_message(job: str) -> str:
    """Return the whole of stderr for a stream's tag job missing its signer.

    Returns:
        The lint's red output, line for line.

    """
    return (
        "lint:release-phases: .github/workflows/continuous.yml: the commitment"
        " barrier is unwired:\n"
        f"  tag job {job} calls tag-images.yml without needing a sign.yml job"
        " — with no attach, a tag names only signed bytes (#1280)\n" + TRAILER
    )


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body with the `{% raw %}` / `{% endraw %}` lines removed.

    """
    config = tomllib.loads((BELT / "config.toml").read_text(encoding="utf-8"))
    run = config["tasks"]["lint:release-phases"]["run"]
    return "".join(
        line
        for line in run.splitlines(keepends=True)
        if line.strip() not in {"{% raw %}", "{% endraw %}"}
    )


BODY = body()


def lint(workflow: str, name: str = "publish.yml") -> subprocess.CompletedProcess[str]:
    """Run the lint against one planted orchestrator.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        flows = Path(tmp) / ".github" / "workflows"
        flows.mkdir(parents=True)
        (flows / name).write_text(workflow, encoding="utf-8")
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [BASH, "-euo", "pipefail", "-c", BODY],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


def fixture(*needs: str, graph: str = FIXTURE) -> str:
    """Return a fixture graph with the tag job needing exactly these.

    Returns:
        The planted workflow text.

    """
    return graph.replace("TAG_NEEDS", "\n".join(f"      - {n}" for n in needs))


def planted(old: str, new: str, text: str = PUBLISH) -> str:
    """Return a real orchestrator with one block swapped.

    Returns:
        The orchestrator text with the swap made.

    Raises:
        ValueError: the block to swap is not in the file exactly once, so
            the row would plant nothing.

    """
    if text.count(old) != 1:
        msg = f"the orchestrator no longer carries exactly one {old!r}"
        raise ValueError(msg)
    return text.replace(old, new)


class Green(unittest.TestCase):
    """The shapes the rule must pass."""

    def test_the_canons_publish_graph_passes(self) -> None:
        """The real orchestrator, both tag jobs after attach, is clean."""
        done = lint(PUBLISH)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertRegex(
            done.stdout,
            r"^lint:release-phases: \.github/workflows/publish\.yml:"
            r" \d+ jobs, barrier intact\n$",
        )

    def test_a_tag_job_needing_attach_passes(self) -> None:
        """The fixture with the edge in place is clean."""
        done = lint(fixture("commit-point", "attach"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertEqual(
            done.stdout,
            "lint:release-phases: .github/workflows/publish.yml: 6 jobs,"
            " barrier intact\n",
        )

    def test_the_canons_continuous_graph_passes(self) -> None:
        """The real stream orchestrator, its tag job after sign, is clean."""
        done = lint(CONTINUOUS, "continuous.yml")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertRegex(
            done.stdout,
            r"^lint:release-phases: \.github/workflows/continuous\.yml:"
            r" \d+ jobs, barrier intact\n$",
        )

    def test_a_stream_tag_job_needing_its_signer_passes(self) -> None:
        """With no attach, the edge to the signing job is the rule."""
        done = lint(fixture("commit-point", "sign", graph=STREAM), "continuous.yml")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertEqual(
            done.stdout,
            "lint:release-phases: .github/workflows/continuous.yml: 6 jobs,"
            " barrier intact\n",
        )


class Red(unittest.TestCase):
    """A tag job that does not wait for attach, named in the message."""

    def assert_red(self, workflow: str, job: str) -> None:
        """Assert the run is red with exactly the tag-order message."""
        done = lint(workflow)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, message(job))

    def test_a_tag_job_needing_only_commit_point_is_red(self) -> None:
        """The shape that burned edtf v1.3.2 and v1.3.3."""
        self.assert_red(fixture("commit-point"), "tag-extension-images")

    def test_the_extension_tag_job_without_attach_is_red(self) -> None:
        """The real pgrx tag job, its attach edge dropped."""
        self.assert_red(
            planted(
                "      - build-extension-images\n"
                "      - commit-point\n"
                "      - attach\n",
                "      - build-extension-images\n      - commit-point\n",
            ),
            "tag-extension-images",
        )

    def test_the_image_tag_job_without_attach_is_red(self) -> None:
        """The real oci-image tag job, its attach edge dropped."""
        self.assert_red(
            planted(
                "      - assemble-oci\n      - commit-point\n      - attach\n",
                "      - assemble-oci\n      - commit-point\n",
            ),
            "tag-image",
        )


class RedStream(unittest.TestCase):
    """A stream's tag job that does not wait for its signature."""

    def assert_red(self, workflow: str, job: str) -> None:
        """Assert the run is red with exactly the stream tag-order message."""
        done = lint(workflow, "continuous.yml")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, stream_message(job))

    def test_a_stream_tag_job_needing_only_commit_point_is_red(self) -> None:
        """The name would land before the signature does."""
        self.assert_red(fixture("commit-point", graph=STREAM), "tag")

    def test_a_stream_tag_job_needing_a_non_signer_is_red(self) -> None:
        """Needing the job that pushed the index is not needing its signer."""
        self.assert_red(fixture("commit-point", "assemble", graph=STREAM), "tag")

    def test_the_continuous_tag_job_without_sign_is_red(self) -> None:
        """The real stream tag job, its sign edge dropped."""
        self.assert_red(
            planted(
                "      - assemble\n      - commit-point\n      - sign\n",
                "      - assemble\n      - commit-point\n",
                CONTINUOUS,
            ),
            "tag",
        )


if __name__ == "__main__":
    unittest.main()

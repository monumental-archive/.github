#!/usr/bin/env python3
"""Table tests for the pgrx upgrade derivation's predecessor selection.

#816: after edtf v1.3.0 was tagged and failed to publish, the next
release asked only whether the graph HEAD (1.3.0) had published tarballs,
found none, and announced "first publish of this extension" for an
extension with eleven published versions. It derived nothing and exited
0. Only edtf's repo-local upgrade-path lint noticed.

The three cases below are distinct and were being conflated:

  burned predecessor   the graph head is not installable, but an older
                       version is - derive from the older one
  mid-life adoption    a repo with real releases and no extension
                       tarballs at all - genuinely no predecessor
  true first publish   no upgrade graph - decided before any release
                       call is made

FIXTURES ARE REAL. `testdata/edtf-releases.json` and
`testdata/stele-releases.json` are `gh api repos/<r>/releases` output,
captured 2026-08-21, reduced to the fields the script reads
(tag_name, draft, assets[].name) and pretty-printed by the belt's own
formatter. No value is edited. A hand-written
listing would only prove the script agrees with my idea of one - the
edtf fixture carries the exact shape that caused the bug, a DRAFT v1.3.0
with zero assets beside a non-draft `edtf-postgres-v1.2.3` carrying
twenty. See [[fixture-is-not-evidence]]: canon v1.24.0 was burned by a
hand-written fixture that no real artifact matched.

WHAT IS ASSERTED. The script is driven for real, with a stub `gh` on
PATH that answers the release listing from a fixture and refuses
everything else. Selection happens before the first download, so these
tests assert the script's own stdout up to that point and deliberately
do NOT assert the exit status: past selection the script wants a
tarball, a container and a live Postgres, which is the publish path's
job to prove and not something a unit test should pretend to do.

stdlib `unittest`, matching mise/test_*.py - #364 refused a test
framework as a fourth thing to port.

Run through the gate as `mise run test`, which `ci` collects.
"""

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CANON = Path(__file__).resolve().parent.parent
SCRIPT = CANON / "release" / "generate-pgrx-upgrade.sh"
TESTDATA = CANON / "release" / "testdata"
# Absolute, resolved once: a partial path lets PATH decide which binary a
# test ran, and the stub `gh` this file installs is proof PATH is in play.
BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")

GH_STUB = """#!/usr/bin/env bash
# Stub `gh` for the derivation tests: answers the release listing from a
# fixture and refuses everything else, so a test stops at the first step
# that wants real bytes.
set -uo pipefail
if [[ ${1:-} == api && ${2:-} == *release* ]]; then
  expr=""
  while [[ $# -gt 0 ]]; do
    if [[ $1 == --jq ]]; then expr="$2"; fi
    shift
  done
  jq -r "${expr}" "${GH_FIXTURE}"
  exit 0
fi
echo "gh stub: refusing '$*' (the test stops here by design)" >&2
exit 1
"""


class Repo:
    """A throwaway pgrx repository with a chosen upgrade graph."""

    def __init__(
        self,
        root: str,
        name: str,
        crate: str,
        version: str,
        edges: list[tuple[str, str]],
    ) -> None:
        """Lay out a control file, an upgrade graph and the tool pins."""
        self.root = Path(root)
        self.name = name
        crate_dir = self.root / "crates" / crate
        (crate_dir / "sql").mkdir(parents=True)
        (crate_dir / f"{name}.control").write_text(
            "default_version = '@CARGO_VERSION@'\n"
        )
        (crate_dir / "Cargo.toml").write_text(
            textwrap.dedent(f"""\
                [package]
                name = "{crate}"
                version = "{version}"
                edition = "2021"
                """)
        )
        for frm, to in edges:
            (crate_dir / "sql" / f"{name}--{frm}--{to}.sql").write_text("")
        (self.root / "mise.toml").write_text(
            textwrap.dedent("""\
                [tools]
                rust = "1.97.1"
                "cargo:cargo-pgrx" = "0.19.2"
                """)
        )
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run([GIT, "init", "-q", "."], cwd=self.root, check=True)
        # ruff: ignore[subprocess-without-shell-equals-true]
        subprocess.run(
            [GIT, "add", "-A"], cwd=self.root, check=True, capture_output=True
        )

    def derive(
        self, version: str, prev_manifest: str, fixture: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the real derivation with a stub forge.

        Returns:
            The completed process; callers assert on its stdout, since the
            script wants real bytes past the point these tests cover.

        """
        bindir = self.root / "stubbin"
        bindir.mkdir(exist_ok=True)
        gh = bindir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        env = dict(os.environ)
        env.update(
            PATH=f"{bindir}:{env['PATH']}",
            VERSION=version,
            PREV_MANIFEST=prev_manifest,
            # Not a credential: the script only asserts GH_TOKEN is set, and
            # every call that would use one is answered by the stub.
            GH_TOKEN="stub",  # ruff: ignore[hardcoded-password-func-arg]
            GITHUB_REPOSITORY="monumental-archive/under-test",
            GH_FIXTURE=str(TESTDATA / fixture),
        )
        env.pop("GITHUB_OUTPUT", None)
        # ruff: ignore[subprocess-without-shell-equals-true]
        return subprocess.run(
            [BASH, str(SCRIPT)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


# The eleven published edtf versions, plus the burned 1.3.0 the failed
# release left behind. Endpoints only; the bodies are irrelevant here.
EDTF_EDGES = [
    ("0.2.0", "1.0.2"),
    ("1.0.0", "1.0.2"),
    ("1.0.1", "1.0.2"),
    ("1.0.2", "1.1.0"),
    ("1.1.0", "1.1.1"),
    ("1.1.1", "1.1.2"),
    ("1.1.2", "1.2.0"),
    ("1.2.0", "1.2.1"),
    ("1.2.1", "1.2.2"),
    ("1.2.2", "1.2.3"),
    ("1.2.3", "1.3.0"),
]


@unittest.skipUnless(JQ, "jq is a belt tool and drives the gh stub")
class SelectsThePredecessor(unittest.TestCase):
    """The three cases the old code collapsed into one."""

    def test_burned_head_falls_back_to_the_newest_installable(self) -> None:
        """Select 1.2.3: #816 itself, where only the graph head is burned."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "edtf_postgres", "edtf-postgres", "1.3.1", EDTF_EDGES)
            out = repo.derive("1.3.1", "1.3.0", "edtf-releases.json").stdout
        self.assertIn("1.3.0 is in the upgrade graph but no non-draft release", out)
        self.assertIn("deriving edtf_postgres from 1.2.3", out)
        self.assertNotIn("first publish", out)

    def test_the_fixture_really_carries_the_shape_that_caused_it(self) -> None:
        """Guard the guard: the capture still says what the bug needed.

        A refreshed listing that lost the draft v1.3.0, or the tarballs on
        edtf-postgres-v1.2.3, would turn every test above into a tautology
        without failing. So assert the fixture's shape, not just its use.
        """
        data = json.loads((TESTDATA / "edtf-releases.json").read_text())
        by_tag = {r["tag_name"]: r for r in data}
        burned = by_tag["v1.3.0"]
        self.assertTrue(burned["draft"], "v1.3.0 must still be the burn draft")
        self.assertEqual([], burned["assets"], "v1.3.0 must carry nothing")
        carried = by_tag["edtf-postgres-v1.2.3"]["assets"]
        self.assertTrue(
            any(a["name"].startswith("edtf_postgres-1.2.3-pg") for a in carried),
            "the real predecessor must still carry its tarballs",
        )

    def test_mid_life_adoption_has_no_predecessor(self) -> None:
        """Real releases, no extension tarballs: first publish is correct."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.1.0", [("1.0.0", "1.0.1")])
            result = repo.derive("1.1.0", "1.0.1", "stele-releases.json")
        self.assertIn("no non-draft release carries any demo_pg tarball", result.stdout)
        self.assertIn("first publish", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_true_first_publish_never_asks_the_forge(self) -> None:
        """An empty graph is decided before any release call is made."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.0.0", [])
            result = repo.derive("1.0.0", "", "stele-releases.json")
        self.assertIn("no upgrade graph", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("gh stub", result.stderr)

    def test_a_graph_that_already_reaches_the_version_derives_nothing(self) -> None:
        """A re-run over an unchanged graph is a no-op, not a re-derivation."""
        with TemporaryDirectory() as d:
            repo = Repo(d, "demo_pg", "demo-pg", "1.0.1", [("1.0.0", "1.0.1")])
            result = repo.derive("1.0.1", "1.0.0", "stele-releases.json")
        self.assertIn("already reaches 1.0.1", result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()

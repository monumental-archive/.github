#!/usr/bin/env python3
"""repro_issue.py's wording, on any run the step can describe.

The properties of `test_repro_issue.py`'s fixed rows (#1556), over
generated tags, classes, manifests and compare-step outcomes:

- V: the issue names the comparison — a verdict, a differing digest,
  an escaped pin — exactly when the compare step's outcome is
  `failure`; every other outcome, whatever its spelling, files an
  issue that says no verdict was reached and names that outcome.
- V: a verdict's scope is the class's own artifacts exactly when the
  manifest handed on has anything but whitespace in it, and the whole
  release otherwise; the title carries the tag, the class and the run
  whichever way it went.

Run by `test:python-properties` under the belt's Hypothesis profile;
the ordinary suite (`mise run test`) never imports this file.
"""

import dataclasses
import unittest

import repro_issue as repro
from hypothesis import example
from hypothesis import strategies as st
from typed_hypothesis import given

TAG = st.from_regex(
    r"v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(-[a-z0-9.]{1,6})?", fullmatch=True
)
CLASS = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)
RUN_ID = st.integers(1, 10**12).map(str)
# Every outcome a step reports, and spellings near `failure`.
COMPARE = st.sampled_from([
    "failure",
    "success",
    "skipped",
    "cancelled",
    "",
    "Failure",
    " failure",
    "failure\n",
]) | st.text(max_size=10)
MANIFEST = st.text(max_size=20) | st.sampled_from(["", " ", "\n\t", '{"entries":[]}'])
VERDICT = (
    "did not rebuild to the published bytes",
    "escaped the pinning",
    "not a pass",
)


def run_of(drawn: tuple[str, str, str, str, str]) -> repro.Run:
    """Make one failed audit run of its drawn fields.

    Returns:
        The run.

    """
    run_id, tag, cls, manifest, compare = drawn
    return repro.Run(
        tag=tag,
        cls=cls,
        manifest=manifest,
        compare=compare,
        run_id=run_id,
        run_url=f"https://github.com/o/r/actions/runs/{run_id}",
    )


# One failed audit run.
RUNS = st.tuples(RUN_ID, TAG, CLASS, MANIFEST, COMPARE).map(run_of)


class NamesTheComparisonOnlyWhenItFailed(unittest.TestCase):
    """V: a verdict is worded exactly when the comparison failed."""

    @given(RUNS)
    @example(repro.Run("v1.0.0", "go-binary", "", "skipped", "7", "https://x/7"))
    @example(repro.Run("v1.0.0", "go-binary", " \n", "failure", "7", "https://x/7"))
    def test_verdict_exactly_on_failure(self, run: repro.Run) -> None:
        """The verdict words appear iff compare is `failure`."""
        title, body = repro.issue(run)
        failed = run.compare == "failure"
        # The outcome is echoed as written; its own words are not a verdict.
        echoed = f"(the compare step was {run.compare})"
        text = f"{title}\n{body.replace(echoed, '')}"
        for words in VERDICT:
            self.assertEqual(words in text, failed, (words, run))
        self.assertEqual("could not be compared" in title, not failed)
        if not failed:
            self.assertIn("no verdict was reached", body)
            self.assertIn(f"(the compare step was {run.compare})", body)
        self.assertTrue(title.startswith(f"repro-check: {run.tag} {run.cls} "), title)
        self.assertTrue(title.endswith(f"(run {run.run_id})"), title)
        self.assertIn(run.run_url, body)

    @given(RUNS)
    def test_scope_follows_the_manifest(self, run: repro.Run) -> None:
        """Per-class scope iff the manifest is not blank."""
        failed = dataclasses.replace(run, compare="failure")
        _, body = repro.issue(failed)
        scoped = f"Judged population: the {run.cls} artifacts {run.tag} published."
        self.assertEqual(scoped in body, bool(run.manifest.strip()), run)
        self.assertEqual(
            "carries no per-class typing" in body, not run.manifest.strip()
        )


if __name__ == "__main__":
    unittest.main()

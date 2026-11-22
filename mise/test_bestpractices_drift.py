#!/usr/bin/env python3
"""Table tests for audit:bestpractices (#1536).

The leg compares a repository's published bestpractices.dev entry with
its own `.bestpractices.json`, criterion by criterion, and names the
step a red asks for: re-enter the form from the file.

The body under test is the task's own `run` string, read out of
mise/config.toml with tomllib and run by bash in a planted git tree,
with `curl` a stub on PATH that serves the row's planted entry and
status and logs the URL it was asked. Each red row asserts the whole
message, not just the exit status.

The answers planted are the canon's real `.bestpractices.json`, so a
row exercises every key shape the file carries, Baseline display names
included, and the entry is the same answers under the names the site
serves them by.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override
from unittest import mock

import bestpractices_drift as drift
import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
ANSWERS = decoded.as_object(
    decoded.load_json((CANON / ".bestpractices.json").read_text(encoding="utf-8")),
    ".bestpractices.json",
)
REPO = "monumental-archive/example"
ID = "14058"
URL = f"https://www.bestpractices.dev/projects/{ID}.json"
LABEL = f"audit:bestpractices: {REPO} (bestpractices.dev project {ID})"
README = (
    "# example\n\n<!-- badges:begin -->\n"
    f"[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/{ID}/badge)]"
    f"(https://www.bestpractices.dev/projects/{ID})\n"
    "<!-- badges:end -->\n"
)
STEP = (
    "  re-enter the form from the file (docs/best-practices.md); the file is\n"
    "  the source, never edit it to match the site (#1536)\n"
)
SKIPPED = "README carries no bestpractices.dev shield, skipped"
CURL_STUB = """#!/usr/bin/env bash
printf 'curl %s\\n' "${*: -1}" >> "${PLANT_LOG}"
cat "${PLANT_ENTRY}"
printf '\\n%s' "${PLANT_CODE}"
"""


def body() -> str:
    """Read the task's run string as mise would render it.

    Returns:
        The task body; it carries no Tera, so it renders as written.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "tasks")
    task = decoded.as_object(tasks["audit:bestpractices"], "audit:bestpractices")
    return decoded.as_str(task["run"], "audit:bestpractices.run")


BODY = body()


def published(edit: dict[str, object] | None = None) -> dict[str, object]:
    """Return the canon's answers as the site serves them, edited.

    Returns:
        The entry: every file key under the site's name, plus the
        fields the site carries that the file does not.

    """
    entry: dict[str, object] = {drift.site_key(k): v for k, v in ANSWERS.items()}
    entry |= {"id": int(ID), "name": "example", "badge_percentage_0": 100}
    entry |= {drift.site_key(k): v for k, v in (edit or {}).items()}
    return entry


def run(
    *,
    entry: dict[str, object] | str | None = None,
    code: str = "200",
    readme: str | None = README,
    answers: bool = True,
    bulk: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the leg in a planted tree.

    `bulk` tracks that many more (empty) files, sorted after the
    README, then a second, empty `readme.md` after them: the probe's
    second match, written long after its first.

    Returns:
        The finished process and the stub's log of URLs asked.

    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tree = root / "tree"
        bin_dir = root / "bin"
        tree.mkdir()
        bin_dir.mkdir()
        stub = bin_dir / "curl"
        stub.write_text(CURL_STUB, encoding="utf-8")
        stub.chmod(0o755)
        planted = root / "entry.json"
        if entry is None:
            entry = published()
        planted.write_text(
            entry if isinstance(entry, str) else json.dumps(entry),
            encoding="utf-8",
        )
        log = root / "log"
        log.touch()
        env = hermetic.env()
        env |= {
            "ORG_BELT_DIR": str(BELT),
            "GITHUB_REPOSITORY": REPO,
            "PATH": f"{bin_dir}:{env['PATH']}",
            "PLANT_ENTRY": str(planted),
            "PLANT_CODE": code,
            "PLANT_LOG": str(log),
        }
        subprocess.run([GIT, "init", "-q"], cwd=tree, env=env, check=True)
        if answers:
            (tree / ".bestpractices.json").write_text(
                json.dumps(ANSWERS, indent=2) + "\n", encoding="utf-8"
            )
        if readme is not None:
            (tree / "README.md").write_text(readme, encoding="utf-8")
        subprocess.run([GIT, "add", "-A"], cwd=tree, env=env, check=True)
        if bulk:
            blob = subprocess.run(
                [GIT, "hash-object", "-w", "--stdin"],
                cwd=tree,
                env=env,
                input="",
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            names = [*(f"p/{n:0>72}.txt" for n in range(bulk)), "readme.md"]
            index = "".join(f"100644 {blob}\t{name}\n" for name in names)
            subprocess.run(
                [GIT, "update-index", "--index-info"],
                cwd=tree,
                env=env,
                input=index,
                text=True,
                check=True,
            )
        as_mise = hermetic.task_run("audit:bestpractices", BODY)
        done = subprocess.run(
            as_mise.argv,
            cwd=tree,
            env=env | as_mise.env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, log.read_text(encoding="utf-8")


class Green(unittest.TestCase):
    """The shapes the leg passes or skips clean, by name."""

    def test_a_matching_entry_passes(self) -> None:
        """The published entry says what the file says."""
        done, log = run()
        self.assertEqual(done.stderr, "")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(
            done.stdout,
            f"{LABEL}: the published entry matches .bestpractices.json\n",
        )
        self.assertEqual(log, f"curl {URL}\n")

    def test_a_readme_ahead_of_a_long_listing_is_found(self) -> None:
        """A second README a pipe buffer later cannot unname the first (#1637).

        A probe that stopped at its first match closed the pipe; the
        grep still reading `git ls-files` died writing its second
        match, and `|| true` read the killed pipeline as "no README":
        a skip where a comparison was owed. The first match is the one
        read, `README.md`, which wears the shield.
        """
        done, log = run(bulk=3000)
        self.assertEqual(done.stderr, "")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(
            done.stdout,
            f"{LABEL}: the published entry matches .bestpractices.json\n",
        )
        self.assertEqual(log, f"curl {URL}\n")

    def test_the_readme_probe_reads_to_the_end_and_masks_nothing(self) -> None:
        """No early-exit reader and no `|| true` on the probe (#1637).

        The row above passes with the old probe too: its `grep`
        block-buffers into the pipe and reads `git ls-files` to the
        end, and whatever dies after the first match, `head` has
        already printed it (measured with 16,384 case variants of a
        README behind a 237 kB listing). So the form is what is held:
        the one reader that matches, keeps the first and reads on.
        """
        probe = next(line for line in BODY.splitlines() if line.startswith("readme="))
        self.assertEqual(
            probe,
            "readme=$(git ls-files | awk"
            " 'tolower($0) ~ /^readme(\\.md|\\.markdown)?$/ && !seen++')",
        )

    def test_a_null_justification_equals_an_empty_one(self) -> None:
        """The site's null and the file's empty string are one answer."""
        key = next(k for k in ANSWERS if k.endswith("_justification"))
        words: dict[str, str | None] = {
            k: decoded.as_str(v, k) for k, v in ANSWERS.items()
        }
        answers = dict(words)
        answers[key] = ""
        site = drift.published(published({key: None}), list(ANSWERS))
        self.assertEqual(drift.drift(answers, site), list[str]())
        # And only then: against the file's real words, null is a drift.
        expected = [f"{key.removesuffix('_justification')}: justification differs"]
        self.assertEqual(drift.drift(words, site), expected)

    def test_a_repository_with_no_badge_is_skipped_by_name(self) -> None:
        """No shield names an entry, so there is nothing to compare."""
        done, log = run(readme="# example\n")
        self.assertEqual(done.stderr, "")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(
            done.stdout,
            f"audit:bestpractices: {REPO}: README carries no bestpractices.dev"
            " shield, skipped\n",
        )
        self.assertEqual(log, "")

    def test_a_commented_out_badge_is_not_a_badge(self) -> None:
        """A shield inside a comment is not worn."""
        shield = README.splitlines()[3]
        done, log = run(readme=README.replace(shield, f"<!-- {shield} -->"))
        self.assertEqual(done.returncode, 0)
        self.assertIn(SKIPPED, done.stdout)
        self.assertEqual(log, "")

    def test_a_repository_with_no_readme_is_skipped_by_name(self) -> None:
        """No README, so no shield."""
        done, _ = run(readme=None)
        self.assertEqual(done.returncode, 0)
        self.assertIn(SKIPPED, done.stdout)

    def test_a_repository_with_no_file_is_skipped(self) -> None:
        """Nothing tracks the answers, so nothing is compared."""
        done, log = run(answers=False)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(
            done.stdout,
            "audit:bestpractices: no tracked .bestpractices.json, skipped\n",
        )
        self.assertEqual(log, "")


class Red(unittest.TestCase):
    """Each red row is red when planted, with the stated message."""

    def assert_red(
        self,
        message: str,
        *,
        entry: dict[str, object] | str | None = None,
        code: str = "200",
        readme: str | None = README,
    ) -> None:
        """Assert exit 1 and exactly this message on stderr."""
        done, _ = run(entry=entry, code=code, readme=readme)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, message)

    def test_a_status_that_differs_is_red_by_criterion(self) -> None:
        """The defect measured on 14058: Unmet on the site, Met in the file."""
        self.assert_red(
            f"{LABEL}: the published entry differs from .bestpractices.json:\n"
            '  test_most: status "Unmet" on the site, "Met" in the file\n' + STEP,
            entry=published({"test_most_status": "Unmet"}),
        )

    def test_a_baseline_status_is_read_under_its_display_name(self) -> None:
        """`osps_ac_01_01` in the file is `OSPS-AC-01.01` on the site."""
        self.assert_red(
            f"{LABEL}: the published entry differs from .bestpractices.json:\n"
            '  osps_ac_01_01: status "?" on the site, "Met" in the file\n' + STEP,
            entry=published({"osps_ac_01_01_status": "?"}),
        )

    def test_a_justification_that_differs_is_red(self) -> None:
        """Same status, older words: the 16735 shape."""
        self.assert_red(
            f"{LABEL}: the published entry differs from .bestpractices.json:\n"
            "  regression_tests_added50: justification differs\n" + STEP,
            entry=published({
                "regression_tests_added50_justification": "Not measured."
            }),
        )

    def test_both_fields_of_one_criterion_are_one_line(self) -> None:
        """A criterion is named once, with every field that differs."""
        self.assert_red(
            f"{LABEL}: the published entry differs from .bestpractices.json:\n"
            '  dynamic_analysis: status "Unmet" on the site, "Met" in the file;'
            " justification differs\n" + STEP,
            entry=published({
                "dynamic_analysis_status": "Unmet",
                "dynamic_analysis_justification": "Not met.",
            }),
        )

    def test_a_criterion_the_site_lacks_is_red(self) -> None:
        """An answer the entry does not carry at all has not reached it."""
        entry = published()
        del entry["test_most_status"]
        self.assert_red(
            f"{LABEL}: the published entry differs from .bestpractices.json:\n"
            '  test_most: status absent on the site, "Met" in the file\n' + STEP,
            entry=entry,
        )

    def test_an_issuer_that_does_not_answer_is_red_not_drift(self) -> None:
        """A 503 is a question unanswered, never a finding about answers."""
        self.assert_red(
            f"audit:bestpractices: {REPO}: {URL} did not answer — HTTP 503,"
            " a question unanswered, not a drift\n",
            code="503",
            entry="busy",
        )

    def test_curl_answering_nothing_is_red_not_drift(self) -> None:
        """No status at all is named as none, never as a finding."""
        self.assert_red(
            f"audit:bestpractices: {REPO}: {URL} did not answer — HTTP none,"
            " a question unanswered, not a drift\n",
            code="",
            entry="",
        )

    def test_an_answer_that_is_not_json_is_red(self) -> None:
        """A 200 over HTML is not an entry."""
        done, _ = run(entry="<html>")
        self.assertEqual(done.returncode, 1)
        self.assertTrue(
            done.stderr.startswith(f"{LABEL}: the site's answer is not JSON: "),
            done.stderr,
        )

    def test_an_answer_that_is_not_an_object_is_red(self) -> None:
        """A JSON list is not a project."""
        self.assert_red(
            f"{LABEL}: the site's answer is not a JSON object\n", entry="[]"
        )

    def test_a_site_answer_that_is_not_a_string_is_refused(self) -> None:
        """A number where a status belongs is no answer to compare (#1681)."""
        self.assert_red(
            f"{LABEL}: the site's answer: test_most_status is not a string or null\n",
            entry=published({"test_most_status": 3}),
        )

    def test_a_baseline_answer_is_refused_under_its_display_name(self) -> None:
        """The refusal names the key the site carries (#1681)."""
        self.assert_red(
            f"{LABEL}: the site's answer: OSPS-AC-01.01_justification is not"
            " a string or null\n",
            entry=published({"osps_ac_01_01_justification": ["x"]}),
        )

    def test_two_entries_on_one_readme_are_red(self) -> None:
        """Which entry the file answers for cannot be guessed."""
        other = README.replace(ID, "16735")
        self.assert_red(
            f"audit:bestpractices: {REPO}: cannot tell which entry to compare\n"
            f"  README shields name more than one entry: {ID}, 16735\n",
            readme=README + other,
        )


class Helper(unittest.TestCase):
    """The helper's pieces, in process."""

    @override
    def setUp(self) -> None:
        """Run as the task does, recording no finding (#1934)."""
        self.enterContext(hermetic.as_task("audit:bestpractices"))

    def test_site_key_maps_baseline_and_keeps_the_rest(self) -> None:
        """Only Baseline input names are renamed."""
        self.assertEqual(drift.site_key("osps_qa_02_02_status"), "OSPS-QA-02.02_status")
        self.assertEqual(
            drift.site_key("osps_br_04_01_justification"),
            "OSPS-BR-04.01_justification",
        )
        self.assertEqual(drift.site_key("test_most_status"), "test_most_status")

    def test_the_baseline_shield_names_the_entry_too(self) -> None:
        """Either shield on the entry names it."""
        readme = f"![b](https://bestpractices.dev/projects/{ID}/baseline)\n"
        self.assertEqual(drift.project(readme), (ID, None))

    def test_keys_that_are_not_answers_are_ignored(self) -> None:
        """Only `_status` and `_justification` keys are compared."""
        document: dict[str, object] = {"name": "x", "a_status": "Met"}
        expected: dict[str, str | None] = {"a_status": "Met"}
        self.assertEqual(drift.answers(document, "f"), expected)

    def test_an_empty_readme_names_no_entry(self) -> None:
        """No README text, no shield."""
        self.assertEqual(drift.project(""), (None, None))

    def test_no_curl_is_red(self) -> None:
        """The fetch is curl's; without it the leg cannot ask."""
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(README, encoding="utf-8")
            with (
                mock.patch.object(shutil, "which", return_value=None),
                contextlib.redirect_stderr(io.StringIO()) as err,
            ):
                status = drift.main([".bestpractices.json", REPO, str(readme)])
        self.assertEqual(status, 1)
        self.assertEqual(err.getvalue(), "audit:bestpractices: curl missing\n")

    def test_a_file_answer_that_is_not_a_string_is_refused(self) -> None:
        """The file's answers are strings or null, read once (#1681)."""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", encoding="utf-8"
        ) as handle:
            handle.write('{"name": 1, "test_most_status": true}')
            handle.flush()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(drift.compare(handle.name, REPO, ID, "{}"), 1)
        self.assertEqual(
            err.getvalue(),
            f"{LABEL}: {handle.name}: test_most_status is not a string or null\n",
        )

    def test_an_unreadable_file_is_red(self) -> None:
        """A file that is not JSON is a finding, not a crash."""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", encoding="utf-8"
        ) as handle:
            handle.write("{")
            handle.flush()
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(drift.compare(handle.name, REPO, ID, "{}"), 1)
        self.assertIn(" is not JSON: ", err.getvalue())


if __name__ == "__main__":
    unittest.main()

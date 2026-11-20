#!/usr/bin/env python3
"""Table tests for lint:bestpractices' check on the stub (#1288).

`scaffold/.bestpractices.json` is the file a new repository copies to
pre-fill its OpenSSF Best Practices entry, and the site publishes what
it proposes. It answered for the canon on every copier: the canon's
licence, lockfile and documents read as Met on repositories they are
false for. The stub now answers only what an org-level control makes
true of every repository, and every answer that is not `?` cites that
control by a URL under the canon repository. The lint holds it there.

The body under test is the task's own `run` string, read out of
mise/config.toml with decoded.load_toml and run by bash in a planted git
tree, not a copy of it, so a change to the lint has to face these rows.

Each red row asserts the message, not just the exit status: a planted
row scored on exit alone can pass for the wrong reason. The green rows
plant the canon's real stub, so the stub and the lint cannot drift.

stdlib `unittest`, matching the other mise/test_*.py. Run through the
gate as `mise run test`, which `ci` collects.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import decoded
import hermetic

CANON = Path(__file__).resolve().parent.parent
BELT = CANON / "mise"
BASH = hermetic.tool("bash")
GIT = hermetic.tool("git")
STUB_PATH = "scaffold/.bestpractices.json"
STUB = (CANON / STUB_PATH).read_text(encoding="utf-8")
CANON_URL = "https://github.com/monumental-archive/.github/"
CLEAN = "lint:bestpractices: 1 file(s) clean\n"


def unsourced(*criteria: str) -> str:
    """Return the red message for answers that cite no canon URL.

    Returns:
        The stderr the lint prints, line for line.

    """
    return (
        f"lint:bestpractices: {STUB_PATH}: pre-set answers that cite no URL"
        f" under {CANON_URL}:\n"
        + "".join(f"  {c}\n" for c in criteria)
        + "  the stub answers only what an org-level control makes true of every\n"
        '  repository, and cites that control; answer the rest "?" (#1288)\n'
    )


def body() -> str:
    """Read the lint's run string as mise would render it.

    Returns:
        The task body; it carries no Tera, so it renders as written.

    """
    config = decoded.load_toml((BELT / "config.toml").read_text(encoding="utf-8"))
    tasks = decoded.as_object(config["tasks"], "the belt's [tasks]")
    task = decoded.as_object(tasks["lint:bestpractices"], "lint:bestpractices")
    return decoded.as_str(task["run"], "lint:bestpractices's run")


BODY = body()


def lint(text: str, path: str = STUB_PATH) -> subprocess.CompletedProcess[str]:
    """Run the lint against one planted, tracked file.

    Returns:
        The finished process, stdout and stderr captured as text.

    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        env = hermetic.env()
        env |= {"ORG_BELT_DIR": str(BELT), "ORG_CANON_DIR": str(CANON)}
        subprocess.run([GIT, "init", "-q"], cwd=tmp, env=env, check=True)
        subprocess.run([GIT, "add", path], cwd=tmp, env=env, check=True)
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", BODY],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    # BSD wc pads its count with spaces; GNU wc does not.
    done.stdout = re.sub(r" +", " ", done.stdout)
    return done


def planted(edit: dict[str, str | None]) -> str:
    """Return the canon's stub with keys set or removed, layout kept.

    Returns:
        The stub text, written the way the stub itself is written.

    Raises:
        ValueError: a key to change is not in the stub, so the row would
            plant nothing.

    """
    data = decoded.as_object(decoded.load_json(STUB), "the stub")
    missing = sorted(set(edit) - set(data))
    if missing:
        message = f"the stub carries no {missing!r}"
        raise ValueError(message)
    out: dict[str, object] = {}
    for key, value in data.items():
        new = edit.get(key, value)
        if new is not None:
            out[key] = new
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def kept(criterion: str) -> str:
    """Return a kept answer's justification, asserting it is kept.

    Returns:
        The justification text.

    Raises:
        ValueError: the criterion is `?` in the stub, so the row would
            plant a change to an answer that is not there.

    """
    data = decoded.as_object(decoded.load_json(STUB), "the stub")
    if data.get(f"{criterion}_status", "?") == "?":
        message = f"{criterion} is not a pre-set answer in the stub"
        raise ValueError(message)
    return decoded.as_str(
        data[f"{criterion}_justification"], f"{criterion}_justification"
    )


class Green(unittest.TestCase):
    """The shapes the lint must pass."""

    def assert_ok(self, text: str, path: str = STUB_PATH) -> None:
        """Assert a clean pass with the ok line and nothing on stderr."""
        done = lint(text, path)
        self.assertEqual(done.stderr, "")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, CLEAN)

    def test_the_canons_stub_passes(self) -> None:
        """The stub every repository copies is clean."""
        self.assert_ok(STUB)

    def test_a_question_mark_needs_no_justification(self) -> None:
        """A `?` is ignored by the site, so it owes no citation."""
        self.assert_ok(
            planted({"dco_status": "?", "dco_justification": None}),
        )

    def test_a_copied_file_is_not_the_stub(self) -> None:
        """A copied file answers for its own repository; no citation rule."""
        self.assert_ok(
            planted({"dco_justification": "Signed off. No URL."}),
            path=".bestpractices.json",
        )


class Red(unittest.TestCase):
    """Each red row is red when planted, with the stated message."""

    def assert_red(self, text: str, message: str) -> None:
        """Assert exit 1 and exactly this message on stderr."""
        done = lint(text)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr, message)

    def test_an_answer_with_no_url_is_red(self) -> None:
        """The defect itself: a pre-set answer that says whose fact it is nowhere."""
        text = kept("dco").split(" https://")[0]
        self.assert_red(planted({"dco_justification": text}), unsourced("dco"))

    def test_a_url_outside_the_canon_is_red(self) -> None:
        """One repository's file is not an org-level control."""
        text = kept("dco").replace(
            CANON_URL, "https://github.com/monumental-archive/stele/"
        )
        self.assert_red(planted({"dco_justification": text}), unsourced("dco"))

    def test_a_sibling_repository_sharing_the_prefix_is_red(self) -> None:
        """The URL is matched to the canon's own path, trailing slash and all."""
        text = kept("dco").replace(
            CANON_URL, "https://github.com/monumental-archive/.github-lab/"
        )
        self.assert_red(planted({"dco_justification": text}), unsourced("dco"))

    def test_an_answer_with_no_justification_is_red(self) -> None:
        """A Met with nothing beside it cites nothing."""
        self.assert_red(
            planted({"dco_justification": None}),
            unsourced("dco"),
        )

    def test_a_mixed_case_criterion_is_read(self) -> None:
        """require_2FA is a real key; a lowercase-only reader skips it."""
        self.assert_red(
            planted({"require_2FA_justification": "Required org-wide."}),
            unsourced("require_2FA"),
        )

    def test_every_offender_is_named(self) -> None:
        """Two unsourced answers are both listed, in file order."""
        self.assert_red(
            planted({
                "osps_le_01_01_justification": "Signed off.",
                "dco_justification": "Signed off.",
            }),
            unsourced("osps_le_01_01", "dco"),
        )

    def test_a_file_not_one_key_per_line_is_red(self) -> None:
        """The check reads lines; a layout it cannot read is red by name."""
        text = json.dumps(decoded.load_json(STUB)) + "\n"
        keys = STUB.count('_status": ')
        self.assert_red(
            text,
            f"lint:bestpractices: {STUB_PATH}: {keys} status keys, 0 on lines"
            " of their own;\n"
            "  the stub is read one key per line, as json.dumps(indent=2)"
            " writes it\n",
        )


if __name__ == "__main__":
    unittest.main()

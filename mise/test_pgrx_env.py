#!/usr/bin/env python3
"""Tests for pgrx-env.sh's `org_pgrx_init`, against a stand-in cargo (#1360).

`org_pgrx_init` registers each declared Postgres with `cargo pgrx init`.
Its fixes here are logic around that call rather than anything a live
Postgres has to show, so a stand-in `cargo` that records what happened
is enough to hold them:

- #1140, #1481: tasks the gate runs in parallel each initialised at
  once, and two inits beside each other both call initdb. One at a
  time, under a lock the kernel holds, which a killed holder cannot
  strand.
- #1173: `cargo pgrx init` rewrites `config.toml` on every call, and the
  new timestamp made cargo rebuild pgrx-pg-sys on an exact cache hit.
  An init that changed no registration keeps the old timestamp.

And `org_pgrx_darwin_icu`, against stand-in `pg_config`s and a
stand-in `mise ls` (#1774): on a Mac each Postgres major gets the ICU
headers its own build names, in a subshell with its one cargo call, so
pinned Postgres builds that name two ICU majors are a supported state.

The function under test is the belt's own, sourced by bash exactly as a
task sources it. stdlib `unittest`, matching the other mise/test_*.py;
run through the gate as `mise run test`.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import override

import hermetic

ENV_SH = Path(__file__).resolve().with_name("pgrx-env.sh")
BASH = hermetic.tool("bash")
# The task a sourced pgrx-env.sh reports for: its findings name the
# task that sourced it (#1934), here test:pgrx.
TASK = "test:pgrx"

# `cargo pgrx init` as far as these rows need it: it logs its start and
# end around $STUB_HOLD seconds of work (two by default), so two inits
# that ran together log start, start, end, end, and it writes
# config.toml from $NEW_CONFIG, the way pgrx rewrites it on every call.
CARGO_STUB = r"""#!/usr/bin/env bash
[[ $1 == pgrx && $2 == init ]] || exit 1
echo start >> "${PGRX_HOME}.log"
sleep "${STUB_HOLD:-2}"
mkdir -p "${PGRX_HOME}"
if [[ -n ${NEW_CONFIG:-} ]]; then
  printf '%s' "${NEW_CONFIG}" > "${PGRX_HOME}/config.toml"
fi
echo end >> "${PGRX_HOME}.log"
"""

OLD = 946684800  # 2000-01-01, far from any timestamp a row writes


class InitCase(unittest.TestCase):
    """A row with a stub cargo and its own PGRX_HOME."""

    @override
    def setUp(self) -> None:
        """Lay out the stand-ins and an empty PGRX_HOME location."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.root = root
        bindir = root / "bin"
        bindir.mkdir()
        for name, body in (
            ("cargo", CARGO_STUB),
            ("cargo-pgrx", "#!/usr/bin/env bash\nexit 0\n"),
        ):
            (bindir / name).write_text(body, encoding="utf-8")
            (bindir / name).chmod(0o755)
        self.home = root / "pgrx"
        env = hermetic.env(MISE_TASK_NAME=TASK)
        self.env = env | {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "PGRX_HOME": str(self.home),
        }

    def init(self, *, group: bool = False, **env: str) -> subprocess.Popen[str]:
        """Start one task's `org_pgrx_init` for Postgres 18.

        Returns:
            The running bash.

        """
        script = (
            f'. "{ENV_SH}"\n'
            "printf 'pg\\t18\\t/opt/pg18/bin/pg_config\\n' | org_pgrx_init\n"
        )
        return subprocess.Popen(
            [BASH, "-euo", "pipefail", "-c", script],
            env=self.env | env,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=group,
        )

    @staticmethod
    def finish(task: subprocess.Popen[str]) -> tuple[int, str]:
        """Wait for one init and close its pipes.

        Returns:
            (exit status, stderr).

        """
        code = task.wait(timeout=120)
        err = task.stderr.read() if task.stderr else ""
        for pipe in (task.stdout, task.stderr):
            if pipe:
                pipe.close()
        return code, err

    def log(self) -> list[str]:
        """Read what the stand-in cargo recorded.

        Returns:
            One line per event, in order.

        """
        path = Path(f"{self.home}.log")
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class OneInitAtATime(InitCase):
    """Parallel tasks initialise in turn, never together (#1140, #1481)."""

    def test_two_tasks_never_init_at_once(self) -> None:
        """The gate starts several pgrx tasks together; their inits queue.

        With #1140's `mkdir` lock this logged start, start, end, end on
        a Linux runner (#1481): the waiters judged the holder by pid.
        """
        tasks = [self.init(), self.init()]
        results = [self.finish(t) for t in tasks]
        codes = [code for code, _ in results]
        both_clean = [0, 0]
        self.assertEqual(both_clean, codes, results)
        want = ["start", "end", "start", "end"]
        self.assertEqual(want, self.log(), results)

    def test_a_killed_holder_does_not_strand_the_next_init(self) -> None:
        """A holder killed mid-init takes the lock with it.

        The holder is killed mid-init. The next init proceeds at once
        rather than waiting out the ten-minute bound.
        """
        holder = self.init(group=True, STUB_HOLD="60")
        deadline = time.monotonic() + 30
        while self.log() != ["start"]:
            self.assertLess(time.monotonic(), deadline, "the holder never started")
            time.sleep(0.1)
        os.killpg(holder.pid, signal.SIGKILL)
        self.finish(holder)
        started = time.monotonic()
        code, err = self.finish(self.init(STUB_HOLD="0"))
        self.assertEqual(0, code, err)
        self.assertLess(time.monotonic() - started, 20, err)
        want = ["start", "start", "end"]
        self.assertEqual(want, self.log())


class UnchangedConfigKeepsItsTime(InitCase):
    """An init that registered nothing new does not touch the timestamp (#1173)."""

    def plant(self, text: str) -> Path:
        """Write an existing config.toml with an old timestamp.

        Returns:
            The file's path.

        """
        self.home.mkdir()
        config = self.home / "config.toml"
        config.write_text(text, encoding="utf-8")
        os.utime(config, (OLD, OLD))
        return config

    def run_init(self, new: str) -> None:
        """Run one init whose cargo writes `new` and wait for it."""
        task = self.init(NEW_CONFIG=new)
        code = task.wait(timeout=60)
        if task.stdout is None or task.stderr is None:
            self.fail("init was started without its pipes")
        err = task.stderr.read()
        task.stdout.close()
        task.stderr.close()
        self.assertEqual(0, code, err)

    def test_same_registrations_in_another_order_keep_the_old_time(self) -> None:
        """The majors come in hash order; the set is what counts."""
        config = self.plant('[configs]\npg17 = "/a"\npg18 = "/b"\n')
        self.run_init('[configs]\npg18 = "/b"\npg17 = "/a"\n')
        self.assertEqual(OLD, int(config.stat().st_mtime))

    def test_a_changed_registration_takes_the_new_time(self) -> None:
        """A moved pg_config is a real change and must rebuild."""
        config = self.plant('[configs]\npg18 = "/b"\n')
        self.run_init('[configs]\npg18 = "/c"\n')
        self.assertNotEqual(OLD, int(config.stat().st_mtime))


# gate_surface.py's records: two extensions, one plain crate between
# them, and an excluded extension that no task builds (#1795).
TWO_EXTENSIONS = (
    "crate\tfirst@0.1.0\tpgrx\t15,16,17\tfirst\n"
    "crate\tplain@0.1.0\tplain\t-\tplain\n"
    "crate\tsecond@0.1.0\tpgrx\t14,15,16\tsecond\n"
    "excluded\theld@0.1.0\tpgrx\t18\theld\n"
)


class Majors(unittest.TestCase):
    """Each extension's major and their union, derived once (#1795)."""

    @staticmethod
    def derive(surface: str) -> subprocess.CompletedProcess[str]:
        """Run both functions as a task does, over `surface` on stdin.

        Returns:
            The finished bash: the pairs, then `union=` and the union.

        """
        script = (
            f'. "{ENV_SH}"\n'
            "pairs=$(org_pgrx_highest)\n"
            'majors=$(org_pgrx_union <<< "${pairs}")\n'
            'printf \'%s\\n\' "${pairs}" "union=${majors}"\n'
        )
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", script],
            input=surface,
            env=hermetic.env(MISE_TASK_NAME=TASK),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_two_extensions_each_at_its_own_highest(self) -> None:
        """Two extensions: a line each at its highest, and the union of both.

        The excluded extension and the plain crate print nothing.
        """
        done = self.derive(TWO_EXTENSIONS)
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["first@0.1.0\t17", "second@0.1.0\t16", "union=16,17"]
        self.assertEqual(want, done.stdout.splitlines())
        self.assertEqual("", done.stderr)

    def test_a_shared_highest_is_registered_once(self) -> None:
        """Two extensions at the same highest major name it once."""
        surface = "crate\ta@1.0.0\tpgrx\t15,16,17\ta\ncrate\tb@1.0.0\tpgrx\t17\tb\n"
        done = self.derive(surface)
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["a@1.0.0\t17", "b@1.0.0\t17", "union=17"]
        self.assertEqual(want, done.stdout.splitlines())

    def test_no_extension_yields_nothing(self) -> None:
        """Plain crates and an excluded extension: no pairs, no majors."""
        surface = (
            "crate\tplain@0.1.0\tplain\t-\tplain\n"
            "excluded\theld@0.1.0\tpgrx\t18\theld\n"
        )
        done = self.derive(surface)
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["", "union="]
        self.assertEqual(want, done.stdout.splitlines())
        self.assertEqual("", done.stderr)

    def test_a_major_that_is_no_number_is_red_by_name(self) -> None:
        """A line whose major is not a number stops the union, named."""
        script = f'. "{ENV_SH}"\norg_pgrx_union'
        done = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", script],
            input="first@0.1.0\t17\nsecond@0.1.0\tpg16\n",
            env=hermetic.env(MISE_TASK_NAME=TASK),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        want = [f"{TASK}: second@0.1.0 names no Postgres major: 'pg16'"]
        self.assertEqual(want, done.stderr.splitlines())


# `mise ls <package> --json`, answered from the listing a row planted
# for that package, and `uname -s` answering the kernel a row names
# (Darwin unless planted otherwise), so the Mac branch runs anywhere.
MISE_STUB = r"""#!/usr/bin/env bash
[[ $1 == ls && $3 == --json ]] || exit 1
echo "mise ls $2" >> "${PLANT_ROOT}/mise.log"
case "$2" in
  aqua:theseus-rs/postgresql-binaries) cat "${PLANT_ROOT}/pg.json" ;;
  github:unicode-org/icu) cat "${PLANT_ROOT}/icu.json" ;;
  *) exit 1 ;;
esac
"""
UNAME_STUB = """#!/usr/bin/env bash
[[ $1 == -s ]] && echo "${PLANT_KERNEL:-Darwin}"
"""
XCRUN_STUB = """#!/usr/bin/env bash
echo /sdk
"""
KEYS = (
    'version_prefix = "release-", asset_pattern = "icu4c-*-sources.tgz", os = ["macos"]'
)
PIN_HEAD = (
    "  pin them beside the Postgres in mise.toml"
    " (macOS only; the Linux runner has libicu-dev):"
)
NO_PIN = (
    "  the prebuilt Postgres ships none, and pgrx binds every Postgres"
    " header from 0.19.3"
)
WRONG_MAJOR = (
    "  the headers must be the major the shipped dylibs are, one pin per ICU major"
)
LINUX_FLAGS = "-D_GNU_SOURCE -I/usr/include/libxml2"


def homebrew(icu: int) -> str:
    """Spell the cppflags a prebuilt macOS Postgres records for its ICU.

    Returns:
        The flags, as the build machine left them.

    """
    sdk = "/Applications/Xcode.app/MacOSX15.5.sdk"
    return f"-isysroot {sdk} -I/opt/homebrew/opt/icu4c@{icu}/include"


def none_pinned(pg: int, icu: int) -> str:
    """Spell the first line of remedy one: no ICU headers pinned at all.

    Returns:
        The line.

    """
    return (
        f"{TASK}: pg{pg} was built against ICU {icu},"
        " and mise.toml pins no ICU headers —"
    )


def other_pinned(pg: int, icu: int, pinned: str) -> str:
    """Spell the first line of remedy two: a pin of another ICU major only.

    Returns:
        The line.

    """
    return (
        f"{TASK}: pg{pg} was built against ICU {icu};"
        f" mise.toml pins ICU {pinned} and no {icu}.x —"
    )


def entry(version: str, where: Path) -> dict[str, object]:
    """Spell one active, installed entry of `mise ls --json`.

    Returns:
        The entry.

    """
    return {
        "version": version,
        "active": True,
        "installed": True,
        "install_path": str(where),
    }


class IcuCase(unittest.TestCase):
    """A Mac with planted Postgres and ICU pins, and nothing else."""

    @override
    def setUp(self) -> None:
        """Lay out the stand-ins; each row plants its own pins."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        bindir = self.root / "bin"
        bindir.mkdir()
        for name, body in (
            ("mise", MISE_STUB),
            ("uname", UNAME_STUB),
            ("xcrun", XCRUN_STUB),
        ):
            (bindir / name).write_text(body, encoding="utf-8")
            (bindir / name).chmod(0o755)
        self.env = hermetic.env(MISE_TASK_NAME=TASK) | {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "PLANT_ROOT": str(self.root),
        }
        self.postgres({})
        self.icu({})

    def postgres(self, flags: dict[str, str]) -> None:
        """Pin one Postgres per version, its pg_config printing `flags`.

        The first is laid out flat, the rest nested the way the package
        extracts on the runner, so both lookups are exercised.
        """
        listing = []
        for n, (version, cppflags) in enumerate(flags.items()):
            where = self.root / "pg" / version
            bindir = (
                where / "bin"
                if n == 0
                else where / f"postgresql-{version}-darwin" / "bin"
            )
            bindir.mkdir(parents=True)
            config = bindir / "pg_config"
            config.write_text(
                f'#!/usr/bin/env bash\n[[ $1 == --cppflags ]] && echo "{cppflags}"\n',
                encoding="utf-8",
            )
            config.chmod(0o755)
            listing.append(entry(version, where))
        # A copy installed by hand is not a pin, so it must change nothing.
        stray = entry("17.0.0", Path("/nowhere")) | {"active": False}
        listing.append(stray)
        (self.root / "pg.json").write_text(json.dumps(listing), encoding="utf-8")

    def icu(self, pins: dict[str, bool]) -> None:
        """Pin one ICU release per version, with its headers or without.

        A row may plant twice; the second planting replaces the first.
        """
        shutil.rmtree(self.root / "icu", ignore_errors=True)
        listing = []
        for version, headers in pins.items():
            where = self.root / "icu" / version
            where.mkdir(parents=True)
            if headers:
                for part, name in (("common", "utypes.h"), ("i18n", "ucol.h")):
                    (where / "source" / part / "unicode").mkdir(parents=True)
                    (where / "source" / part / "unicode" / name).touch()
            listing.append(entry(version, where))
        (self.root / "icu.json").write_text(json.dumps(listing), encoding="utf-8")

    def include(self, version: str) -> str:
        """Spell the include path one pinned ICU release adds.

        Returns:
            The two -I flags, in the order the function writes them.

        """
        where = self.root / "icu" / version
        return f"-I{where}/source/common -I{where}/source/i18n"

    def bash(self, script: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Source pgrx-env.sh under the parent's own flags, then run `script`.

        Returns:
            The finished bash.

        """
        parent = {"BINDGEN_EXTRA_CLANG_ARGS": "parent-b", "CFLAGS": "parent-c"}
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c", f'. "{ENV_SH}"\n{script}'],
            env=self.env | parent | env,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def per_major(self, *majors: int) -> subprocess.CompletedProcess[str]:
        """Add each major's headers in its own subshell, as a call site does.

        Each subshell prints what it exported; the parent prints its own
        flags last.

        Returns:
            The finished bash.

        """
        lines = [
            f"( org_pgrx_darwin_icu {m} || exit 1; "
            f'echo "pg{m}|$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS" )'
            for m in majors
        ]
        lines.append('echo "parent|$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS"')
        return self.bash("\n".join(lines))

    def check(self, majors: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run the before-any-compile check over `majors`.

        Returns:
            The finished bash.

        """
        return self.bash(f'org_pgrx_darwin_icu_check "{majors}"', **env)

    def exported(self, major: int, version: str) -> str:
        """Spell what one subshell prints with one ICU release's headers.

        Returns:
            The line.

        """
        flags = self.include(version)
        return f"pg{major}|{flags} parent-b|{flags} parent-c"


class MixedIcuSet(IcuCase):
    """Two Postgres majors built against two ICU majors (#1774)."""

    @override
    def setUp(self) -> None:
        """Pin pg17 built against ICU 78 and pg18 built against ICU 79."""
        super().setUp()
        self.postgres({"17.6.0": homebrew(78), "18.1.0": homebrew(79)})

    def test_both_pinned_each_major_exports_its_own_include_path(self) -> None:
        """Each subshell carries its own major's headers and nothing else.

        Before #1774 this was a refusal: one include path for the whole
        task, so two ICU majors could not both be served.
        """
        self.icu({"78.3": True, "79.1": True})
        done = self.per_major(17, 18)
        self.assertEqual(0, done.returncode, done.stderr)
        want = [
            self.exported(17, "78.3"),
            self.exported(18, "79.1"),
            "parent|parent-b|parent-c",
        ]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )
        self.assertEqual("", done.stderr)
        checked = self.check("17,18")
        self.assertEqual((0, ""), (checked.returncode, checked.stderr))

    def test_only_78_pinned_reds_naming_79_and_the_pin_line(self) -> None:
        """A held-back major's ICU is pinned; the new one is named, with the array."""
        self.icu({"78.3": True})
        expected = [
            other_pinned(18, 79, "78.3"),
            WRONG_MAJOR,
            PIN_HEAD,
            '  "github:unicode-org/icu" = [',
            f'    {{ version = "78.3", {KEYS} }},',
            f'    {{ version = "79.<minor>", {KEYS} }},',
            "  ]",
        ]
        checked = self.check("17,18")
        self.assertEqual(1, checked.returncode, checked.stderr)
        self.assertEqual(expected, checked.stderr.splitlines())
        # The per-call refusal says the same, so it is never the weaker guard.
        done = self.per_major(18)
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual(expected, done.stderr.splitlines())
        # And the held-back major still builds.
        done = self.per_major(17)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(self.exported(17, "78.3"), done.stdout.splitlines())

    def test_neither_pinned_names_both_at_once(self) -> None:
        """One red before any compile, naming every ICU major to add."""
        checked = self.check("17,18")
        self.assertEqual(1, checked.returncode, checked.stderr)
        want = [
            none_pinned(17, 78),
            NO_PIN,
            none_pinned(18, 79),
            NO_PIN,
            PIN_HEAD,
            '  "github:unicode-org/icu" = [',
            f'    {{ version = "78.<minor>", {KEYS} }},',
            f'    {{ version = "79.<minor>", {KEYS} }},',
            "  ]",
        ]
        self.assertEqual(
            want,
            checked.stderr.splitlines(),
        )


class OneIcuMajor(IcuCase):
    """One Postgres major, one ICU pin: the case before #1774, unchanged."""

    @override
    def setUp(self) -> None:
        """Pin pg18 built against ICU 78."""
        super().setUp()
        self.postgres({"18.1.0": homebrew(78)})

    def test_one_major_one_pin(self) -> None:
        """The headers are added, and the check passes."""
        self.icu({"78.3": True})
        done = self.per_major(18)
        self.assertEqual(0, done.returncode, done.stderr)
        want = [self.exported(18, "78.3"), "parent|parent-b|parent-c"]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )
        checked = self.check("18")
        self.assertEqual((0, ""), (checked.returncode, checked.stderr))

    def test_no_pin_names_the_single_table(self) -> None:
        """Remedy one: nothing pinned, so the one-table form to add."""
        checked = self.check("18")
        self.assertEqual(1, checked.returncode, checked.stderr)
        want = [
            none_pinned(18, 78),
            NO_PIN,
            PIN_HEAD,
            f'  "github:unicode-org/icu" = {{ version = "78.<minor>", {KEYS} }}',
        ]
        self.assertEqual(
            want,
            checked.stderr.splitlines(),
        )

    def test_another_major_only_names_the_array(self) -> None:
        """Remedy two: a pin of another ICU major only, kept beside the new one."""
        self.icu({"79.1": True})
        checked = self.check("18")
        self.assertEqual(1, checked.returncode, checked.stderr)
        want = [
            other_pinned(18, 78, "79.1"),
            WRONG_MAJOR,
            PIN_HEAD,
            '  "github:unicode-org/icu" = [',
            f'    {{ version = "79.1", {KEYS} }},',
            f'    {{ version = "78.<minor>", {KEYS} }},',
            "  ]",
        ]
        self.assertEqual(
            want,
            checked.stderr.splitlines(),
        )

    def test_not_the_source_archive(self) -> None:
        """Remedy three: the right major, and no headers in it."""
        self.icu({"78.3": False})
        where = self.root / "icu" / "78.3"
        reason = [
            (
                f"{TASK}: ICU 78.3 at {where} carries no headers"
                " under source/common and source/i18n"
            ),
            (
                "  the pin must be ICU's SOURCE archive"
                ' (asset_pattern = "icu4c-*-sources.tgz")'
            ),
        ]
        checked = self.check("18")
        self.assertEqual(1, checked.returncode, checked.stderr)
        self.assertEqual(reason, checked.stderr.splitlines())
        done = self.per_major(18)
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual(reason, done.stderr.splitlines())

    def test_not_macos_adds_nothing_and_reads_nothing(self) -> None:
        """Any kernel but Darwin: no headers, no listing read, no red."""
        done = self.bash(
            "( org_pgrx_darwin_icu 18; "
            'echo "pg18|$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS" )\n'
            'org_pgrx_darwin_icu_check 18\necho "check $?"',
            PLANT_KERNEL="Linux",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["pg18|parent-b|parent-c", "check 0"]
        self.assertEqual(want, done.stdout.splitlines())
        self.assertEqual("", done.stderr)
        self.assertFalse((self.root / "mise.log").exists())

    def test_sdk_correction_adds_no_icu(self) -> None:
        """org_pgrx_darwin_flags is the SDK and nothing else now."""
        self.icu({"78.3": True})
        done = self.bash(
            'org_pgrx_darwin_flags\necho "$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS"'
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["-isysroot /sdk parent-b|-isysroot /sdk parent-c"]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )


class NoIcuNamed(IcuCase):
    """A Postgres whose flags name no ICU."""

    def test_exports_nothing_and_checks_clean(self) -> None:
        """Nothing was built against an ICU, so there is nothing to add."""
        self.postgres({"18.1.0": LINUX_FLAGS})
        done = self.per_major(18)
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["pg18|parent-b|parent-c", "parent|parent-b|parent-c"]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )
        checked = self.check("18")
        self.assertEqual((0, ""), (checked.returncode, checked.stderr))


class WithIcu(IcuCase):
    """org_pgrx_with_icu: one command, its major's headers, then gone."""

    @override
    def setUp(self) -> None:
        """Pin pg17 and pg18 against ICU 78 and 79, both pinned."""
        super().setUp()
        self.postgres({"17.6.0": homebrew(78), "18.1.0": homebrew(79)})
        self.icu({"78.3": True, "79.1": True})

    def test_each_command_sees_its_own_and_the_parent_keeps_its_flags(self) -> None:
        """Sequential commands: no major's include path reaches the next."""
        show = "bash -c 'echo \"$0|$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS\"'"
        done = self.bash(
            f"org_pgrx_with_icu 17 {show} pg17\n"
            f"org_pgrx_with_icu 18 {show} pg18\n"
            f"org_pgrx_with_icu '' {show} plain\n"
            'echo "parent|$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS"'
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want = [
            self.exported(17, "78.3"),
            self.exported(18, "79.1"),
            "plain|parent-b|parent-c",
            "parent|parent-b|parent-c",
        ]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )

    def test_missing_headers_run_nothing(self) -> None:
        """A major whose headers are not pinned reds before its command."""
        self.icu({"78.3": True})
        done = self.bash("org_pgrx_with_icu 18 echo ran")
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual(other_pinned(18, 79, "78.3"), done.stderr.splitlines()[0])

    def test_the_command_status_is_its_own(self) -> None:
        """A failed compile is the helper's failure, in a condition too."""
        done = self.bash(
            "if org_pgrx_with_icu 17 false; then echo ok; else echo red $?; fi"
        )
        self.assertEqual((0, "red 1\n"), (done.returncode, done.stdout))

    def test_prepare_is_the_sdk_and_the_check(self) -> None:
        """The SDK for every major, and one red naming each missing ICU."""
        done = self.bash(
            'org_pgrx_darwin_prepare "17,18"\necho "$BINDGEN_EXTRA_CLANG_ARGS|$CFLAGS"'
        )
        self.assertEqual(0, done.returncode, done.stderr)
        want = ["-isysroot /sdk parent-b|-isysroot /sdk parent-c"]
        self.assertEqual(
            want,
            done.stdout.splitlines(),
        )
        self.icu({"79.1": True})
        done = self.bash('org_pgrx_darwin_prepare "17,18"')
        self.assertEqual(1, done.returncode)
        self.assertEqual(other_pinned(17, 78, "79.1"), done.stderr.splitlines()[0])


# Every site that compiles the extension (#1774), and how many compiles
# each runs with a major's headers: test:pgrx runs a suite three ways,
# one of them in a subshell of its own (measure_one).
SITES = {
    "mise/pgrx-test.sh": 3,
    "mise/pg-schema.sh": 1,
    "mise/mutants-rust.sh": 1,
    "mise/semver-audit.sh": 1,
    "fix:rust": 1,
    "lint:msrv": 1,
    "lint:pg-clippy": 1,
}
CANON = ENV_SH.parent.parent
COMPILE = re.compile(r"\bcargo(-semver-checks)? |\bmutate ")
CALL = re.compile(r'\borg_pgrx_(with_icu|darwin_icu) "')
CHECK = re.compile(r"\borg_pgrx_darwin_(prepare|icu_check) ")
BODY = "^run = '''\n(.*?)^'''$"


def site_text(site: str) -> str:
    """Read one call site: a script, or a task's `run` body.

    Returns:
        The text bash runs.

    Raises:
        LookupError: no such task in mise/config.toml.

    """
    if site.startswith("mise/"):
        return (CANON / site).read_text(encoding="utf-8")
    config = (CANON / "mise" / "config.toml").read_text(encoding="utf-8")
    found = re.search(
        rf'^\[tasks\."{re.escape(site)}"\]\n.*?{BODY}',
        config,
        re.MULTILINE | re.DOTALL,
    )
    if found is None:
        msg = f"no run body for {site} in mise/config.toml"
        raise LookupError(msg)
    return found.group(1)


def indent(line: str) -> int:
    """Count a line's leading blanks.

    Returns:
        The indentation.

    """
    return len(line) - len(line.lstrip())


def command(lines: list[str], at: int) -> str:
    """Join line `at` with the lines its trailing backslashes continue.

    Returns:
        The one command, as bash reads it.

    """
    out = [lines[at]]
    while out[-1].rstrip().endswith("\\") and at + 1 < len(lines):
        at += 1
        out.append(lines[at])
    return " ".join(out)


def subshell(lines: list[str], at: int) -> list[str]:
    """Find the `( ... )` that holds line `at`.

    Returns:
        Its lines, opener to closer, or [] when the line is in none.

    """
    depth = indent(lines[at])
    for j in range(at - 1, -1, -1):
        text = lines[j].strip()
        if not text or text.startswith("#") or indent(lines[j]) >= depth:
            continue
        if text == "(" or text.endswith(" ("):
            for k in range(at + 1, len(lines)):
                closes = lines[k].strip().startswith(")")
                if closes and indent(lines[k]) == indent(lines[j]):
                    return lines[j : k + 1]
        return []
    return []


class NoLeak(unittest.TestCase):
    """No call site lets one major's include path reach the next (#1774).

    A subshell cannot change its parent's BINDGEN_EXTRA_CLANG_ARGS or
    CFLAGS, which WithIcu and MixedIcuSet show in bash. Here every site
    is read for that shape: each compile goes through org_pgrx_with_icu,
    whose subshell ends with the command, or adds the headers itself
    inside a subshell that compiles after it. And the check runs first.
    """

    def test_every_compile_carries_its_major_in_a_subshell(self) -> None:
        """Each site's compiles are the shapes above, and checked first."""
        for site, calls in SITES.items():
            with self.subTest(site=site):
                lines = site_text(site).splitlines()
                hits = [
                    i
                    for i, line in enumerate(lines)
                    if CALL.search(line) and not line.lstrip().startswith("#")
                ]
                self.assertEqual(calls, len(hits))
                text = "\n".join(lines)
                first_check = CHECK.search(text)
                first_call = CALL.search(text)
                self.assertIsNotNone(first_check, "no check before compiling")
                self.assertIsNotNone(first_call)
                if first_check is None or first_call is None:
                    return
                self.assertLess(first_check.start(), first_call.start())
                for at in hits:
                    if "org_pgrx_with_icu" in lines[at]:
                        self.assertRegex(command(lines, at), COMPILE)
                        continue
                    block = subshell(lines, at)
                    self.assertTrue(block, f"line {at + 1} is in no subshell")
                    after = block[block.index(lines[at]) + 1 :]
                    self.assertTrue(
                        any(COMPILE.search(line) for line in after),
                        f"line {at + 1}'s subshell compiles nothing after it",
                    )

    def test_no_site_beyond_the_seven(self) -> None:
        """A new compile site cannot add headers without joining the table."""
        found = set()
        for path in sorted((CANON / "mise").glob("*.sh")):
            text = path.read_text(encoding="utf-8")
            if path.name != "pgrx-env.sh" and CALL.search(text):
                found.add(f"mise/{path.name}")
        found.update(
            task
            for task in ("fix:rust", "lint:msrv", "lint:pg-clippy")
            if CALL.search(site_text(task))
        )
        config = (CANON / "mise" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(3, len(list(CALL.finditer(config))))
        sites = set(SITES)
        self.assertEqual(sites, found)


if __name__ == "__main__":
    unittest.main()

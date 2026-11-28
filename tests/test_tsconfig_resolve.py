"""Table tests for the one tsconfig resolver (#2133).

`mise/tsconfig-resolve.sh` is run as the belt runs it, with the belt's
own tsc: every row plants a real tsconfig and reads what the compiler
did. tsc 7.0.2's `--showConfig` exits 0 on a tsconfig it cannot parse
and prints the keys it could read, so a row here that only checked the
exit status of that flag would pass before the fix and after it; each
refusal asserts the compiler's own diagnostic code on the finding.

Both directions are held. A config tsc cannot parse is refused, through
`extends` too; and a fact about the tree rather than the config (a
source that does not parse, an `include` matching nothing, a type
package that is not installed) is not, because `lint:types` owns those.

The selection beside it, `mise/tsconfig-files.sh`, is held the same
way, and so is every caller: `lint:types`, `lint:tsconfig-dials` and
the biome generator each run over a planted tree.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from belt import decoded
from tests import support

# The task the rows report under, as a caller's name reaches the script.
TASK = "lint:tsconfig-dials"

BELT = support.MISE
SCRIPT = BELT / "tsconfig-resolve.sh"
GENERATE = BELT / "biome-generate.sh"
SCAFFOLD = BELT.parent / "scaffold" / "tsconfig.json"

SOURCE = "export const a = 1;\n"
GOOD = '{"compilerOptions": {"strict": true}, "include": ["src"]}\n'
MODULE = '{"name": "fixture", "private": true, "type": "module"}\n'
# What every accepted row's document states.
STRICT = True
# The file list `--listFilesOnly` prints beside a diagnostic.
LIB = "lib.es5.d.ts"

# A tsconfig tsc cannot parse, and the diagnostic it raises for it.
# Every one of these `--showConfig` resolves with exit 0.
REFUSED: tuple[tuple[str, str, str], ...] = (
    ("truncated", "{", "tsconfig.json(1,2): error TS1005:"),
    (
        "an unknown option beside a real one",
        '{"compilerOptions": {"strict": true, "nonsense": true}}',
        "tsconfig.json(1,38): error TS5023:",
    ),
    (
        "a value outside the option's enum",
        '{"compilerOptions": {"module": "zzz"}}',
        "tsconfig.json(1,32): error TS6046:",
    ),
    (
        "a value of the wrong type",
        '{"compilerOptions": {"strict": "yes"}}',
        "tsconfig.json(1,32): error TS5024:",
    ),
    (
        "a parent that does not exist",
        '{"extends": "./gone.json", "compilerOptions": {"strict": true}}',
        "error TS5083:",
    ),
)

# A config tsc parses, in a tree that would red `lint:types`.
ACCEPTED: tuple[tuple[str, str, str], ...] = (
    ("a plain project", GOOD, SOURCE),
    (
        "a type package that is not installed",
        '{"compilerOptions": {"strict": true, "types": ["node"]}}',
        SOURCE,
    ),
    ("a source that does not parse", GOOD, "export const = ;;; {{{\n"),
    (
        "an include matching nothing",
        '{"compilerOptions": {"strict": true}, "include": ["nowhere"]}',
        SOURCE,
    ),
    (
        "comments and a trailing comma",
        '{\n  // the level\n  "compilerOptions": {"strict": true,},\n}\n',
        SOURCE,
    ),
)


def environment(*, env: support.Env) -> dict[str, str]:
    """Build the environment a belt task hands the script.

    Returns:
        The suite's allow-list with the belt's tsc and jq first on PATH.

    """
    dirs = [str(Path(support.tool(name)).parent) for name in ("tsc", "jq")]
    path = ":".join([*dirs, env().get("PATH", "")])
    return env(
        PATH=path,
        MISE_TASK_NAME=TASK,
        ORG_PYTHON=sys.executable,
        ORG_BELT_DIR=str(BELT),
    )


def plant(tree: Path, config: str, source: str = SOURCE) -> None:
    """Write one project at the tree's root."""
    (tree / "src").mkdir()
    (tree / "src" / "a.ts").write_text(source, encoding="utf-8")
    (tree / "tsconfig.json").write_text(config, encoding="utf-8")


def resolve(
    tree: Path, config: str = "tsconfig.json", *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run the resolver over one tsconfig in the tree.

    Returns:
        The finished process; the document is `resolved.json` beside it.

    """
    return subprocess.run(
        [support.tool("bash"), str(SCRIPT), config, str(tree / "resolved.json")],
        cwd=tree,
        env=environment(env=env),
        capture_output=True,
        text=True,
        check=False,
    )


def options(tree: Path) -> dict[str, object]:
    """Read the resolved document's compiler options.

    Returns:
        The `compilerOptions` table tsc printed.

    """
    text = (tree / "resolved.json").read_text(encoding="utf-8")
    document = decoded.as_object(decoded.load_json(text), "resolved")
    return decoded.as_object(document["compilerOptions"], "compilerOptions")


# A tsconfig tsc cannot parse is a finding with the compiler's message.
@pytest.mark.parametrize(
    ("config", "said"),
    [pytest.param(config, said, id=name) for name, config, said in REFUSED],
)
def test_each_malformed_config_is_refused_by_its_diagnostic(
    config: object, said: object, env: support.Env
) -> None:
    """One row per way `--showConfig` reads a bad config as a good one."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        plant(tree, config)
        done = resolve(tree, env=env)
        assert done.returncode == 1, done.stderr
        lines = done.stderr.splitlines()
        assert lines[0] == f"{TASK}: tsconfig.json does not resolve"
        assert lines[1].startswith(f"  {said}"), done.stderr
        assert LIB not in done.stderr, "the file list was printed"


def test_a_malformed_parent_is_named_through_extends(env: support.Env) -> None:
    """The diagnostic points at the config that is wrong, not the child."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        plant(tree, '{"extends": "./base.json", "include": ["src"]}')
        (tree / "base.json").write_text(
            '{"compilerOptions": {"strict": true, "nonsense": 1}}',
            encoding="utf-8",
        )
        done = resolve(tree, env=env)
    assert done.returncode == 1, done.stderr
    lines = done.stderr.splitlines()
    assert lines[0] == f"{TASK}: tsconfig.json does not resolve"
    assert lines[1].startswith("  base.json(1,38): error TS5023:"), lines


def test_a_tsconfig_that_is_not_there_carries_the_reason(env: support.Env) -> None:
    """TS5058 is printed on stdout; the finding has it all the same."""
    with tempfile.TemporaryDirectory() as tmp:
        done = resolve(Path(tmp), env=env)
    assert done.returncode == 1, done.stderr
    lines = done.stderr.splitlines()
    assert lines[0] == f"{TASK}: tsconfig.json does not resolve"
    assert lines[1].startswith("  error TS5058:"), done.stderr


# What `lint:types` owns is not the resolver's to refuse.
@pytest.mark.parametrize(
    ("config", "source"),
    [pytest.param(config, source, id=name) for name, config, source in ACCEPTED],
)
def test_each_parsed_config_resolves_whatever_its_tree(
    config: object, source: object, env: support.Env
) -> None:
    """The document is written and nothing is said."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        plant(tree, config, source)
        done = resolve(tree, env=env)
        assert done.returncode == 0, done.stderr + done.stdout
        assert not done.stderr
        assert options(tree)["strict"] == STRICT
        leftovers = sorted(p.name for p in tree.iterdir())
        expected: list[str] = ["resolved.json", "src", "tsconfig.json"]
        assert leftovers == expected, "the run left residue"


def test_a_value_inherited_through_extends_is_resolved(env: support.Env) -> None:
    """The reason the document is tsc's and not the file's text."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        plant(tree, '{"extends": "./base.json", "include": ["src"]}')
        (tree / "base.json").write_text(
            '{"compilerOptions": {"types": ["node"]}}', encoding="utf-8"
        )
        done = resolve(tree, env=env)
        assert done.returncode == 0, done.stderr
        types: list[str] = ["node"]
        assert options(tree)["types"] == types


def test_the_scaffold_stub_resolves_with_no_source_beside_it(env: support.Env) -> None:
    """The canon tracks the stub alone; its `include` matches nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        (tree / "tsconfig.json").write_bytes(SCAFFOLD.read_bytes())
        done = resolve(tree, env=env)
        assert done.returncode == 0, done.stderr
        assert options(tree)["strict"] == STRICT


def with_one_unknown_option() -> str:
    """Write the scaffold's tsconfig with one option tsc does not know.

    Returns:
        A config stating every dial, which tsc itself refuses.

    """
    text = SCAFFOLD.read_text(encoding="utf-8")
    opened = '"compilerOptions": {'
    return text.replace(opened, f'{opened}\n    "nonsense": true,', 1)


def tracked_tree(tree: Path, config: str, env: dict[str, str]) -> None:
    """Plant one project and track it, as a repository holds it."""
    plant(tree, config)
    git = support.tool("git")
    subprocess.run([git, "init", "-q"], cwd=tree, env=env, check=True)
    subprocess.run([git, "add", "."], cwd=tree, env=env, check=True)


def run_body(
    name: str, body: str, tree: Path, *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run one task body in the tree, as mise runs it.

    Returns:
        The finished process.

    """
    as_mise = support.task_run(name, body)
    return subprocess.run(
        as_mise.argv,
        cwd=tree,
        env=environment(env=env) | as_mise.env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_task(
    name: str, tree: Path, *, env: support.Env
) -> subprocess.CompletedProcess[str]:
    """Run one belt task's own body in the tree.

    Returns:
        The finished process.

    """
    tasks = decoded.as_object(
        decoded.load_toml((BELT / "config.toml").read_bytes())["tasks"], "tasks"
    )
    task = decoded.as_object(tasks[name], name)
    return run_body(name, decoded.as_str(task["run"], f"{name}.run"), tree, env=env)


SELECT = """
. "${ORG_BELT_DIR}/tsconfig-files.sh"
found=() && tsconfig_files found
printf '%s\\n' ${found[@]+"${found[@]}"}
"""

# Tracked files, and whether each is one of the repository's projects.
PROJECTS: tuple[tuple[str, bool], ...] = (
    ("tsconfig.json", True),
    ("apps/a/tsconfig.json", True),
    ("one/two/three/four/five/tsconfig.json", True),
    ("vendor/lib/tsconfig.json", False),
    ("apps/a/xtsconfig.json", False),
    ("apps/a/tsconfig.build.json", False),
    ("apps/a/tsconfig.json.bak", False),
)


# mise/tsconfig-files.sh: which projects a repository has.
def test_a_project_is_a_tracked_tsconfig_at_any_depth(env: support.Env) -> None:
    """Named exactly, outside vendor/, and tracked."""
    environ = environment(env=env)
    git = support.tool("git")
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        for name, _ in PROJECTS:
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text("{}", encoding="utf-8")
        subprocess.run([git, "init", "-q"], cwd=tree, env=environ, check=True)
        subprocess.run([git, "add", "."], cwd=tree, env=environ, check=True)
        (tree / "untracked").mkdir()
        (tree / "untracked" / "tsconfig.json").write_text("{}", encoding="utf-8")
        done = run_body("select", SELECT, tree, env=env)
    assert done.returncode == 0, done.stderr
    want: list[str] = sorted(name for name, selected in PROJECTS if selected)
    found: list[str] = sorted(done.stdout.splitlines())
    assert found == want


def test_a_tree_with_none_selects_nothing(env: support.Env) -> None:
    """An empty list, and git's success."""
    environ = environment(env=env)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        (tree / "README").write_text("x", encoding="utf-8")
        git = support.tool("git")
        subprocess.run([git, "init", "-q"], cwd=tree, env=environ, check=True)
        subprocess.run([git, "add", "."], cwd=tree, env=environ, check=True)
        done = run_body("select", SELECT, tree, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "\n"


def test_a_git_that_cannot_answer_is_the_function_s_failure(env: support.Env) -> None:
    """Outside a repository the selection fails; it is never empty."""
    with tempfile.TemporaryDirectory() as tmp:
        done = run_body("select", SELECT, Path(tmp), env=env)
    assert done.returncode != 0, done.stdout


def scaffold_with(extra: str) -> str:
    """Write the scaffold's tsconfig with one more line before its options.

    Returns:
        The config's text.

    """
    text = SCAFFOLD.read_text(encoding="utf-8")
    opened = '"compilerOptions": {'
    return text.replace(opened, f"{extra}\n  {opened}", 1)


# `lint:types` asks the compiler what a config says, never its text.
def test_the_word_references_in_a_comment_is_not_a_finding(env: support.Env) -> None:
    """A grep of the file's text refused this project."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        config = scaffold_with('// no "references" here, by the org\'s rule')
        # An ES module, installed, for a project with no dependencies.
        (tree / "package.json").write_text(MODULE, encoding="utf-8")
        (tree / "node_modules").mkdir()
        tracked_tree(tree, config, environment(env=env))
        done = run_task("lint:types", tree, env=env)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "lint:types: 1 project(s) checked, 1 files covered" in done.stdout


def test_declared_references_are_refused_by_name(env: support.Env) -> None:
    """The refusal itself is unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        config = scaffold_with('"references": [{"path": "../other"}],')
        tracked_tree(tree, config, environment(env=env))
        done = run_task("lint:types", tree, env=env)
    assert done.returncode == 1, done.stderr + done.stdout
    lines = done.stderr.splitlines()
    said = "lint:types: project references are not supported org-wide"
    assert lines[0] == said
    assert lines[1] == "  declares references: tsconfig.json"


def test_a_config_tsc_cannot_parse_ends_the_task_by_name(env: support.Env) -> None:
    """The one resolver's finding, before any program is built."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        tracked_tree(tree, with_one_unknown_option(), environment(env=env))
        done = run_task("lint:types", tree, env=env)
    assert done.returncode == 1, done.stderr + done.stdout
    lines = done.stderr.splitlines()
    assert lines[0] == "lint:types: tsconfig.json does not resolve"
    assert "error TS5023: Unknown compiler option 'nonsense'" in lines[1]


# Both sites refuse the config `--showConfig` alone resolved (#2133).
def test_the_dials_task_refuses_a_config_stating_every_dial(env: support.Env) -> None:
    """Every dial is there, so before the resolver this passed."""
    environ = environment(env=env)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        tracked_tree(tree, with_one_unknown_option(), environ)
        done = run_task(TASK, tree, env=env)
    assert done.returncode == 1, done.stderr + done.stdout
    lines = done.stderr.splitlines()
    assert lines[0] == f"{TASK}: tsconfig.json does not resolve"
    assert "error TS5023: Unknown compiler option 'nonsense'" in lines[1]
    assert "dial(s) at the org's level" not in done.stdout


def test_the_biome_generator_refuses_a_malformed_node_project(env: support.Env) -> None:
    """It used to generate a config from a project tsc itself refuses."""
    environ = environment(env=env) | {"MISE_TASK_NAME": "lint:biome"}
    config = '{"compilerOptions": {"types": ["node"], "nonsense": true}}'
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        tracked_tree(tree, config, environ)
        out = tree / "generated.json"
        done = subprocess.run(
            [support.tool("bash"), str(GENERATE), str(out)],
            cwd=tree,
            env=environ,
            capture_output=True,
            text=True,
            check=False,
        )
        written = out.exists()
    assert done.returncode == 1, done.stderr + done.stdout
    lines = done.stderr.splitlines()
    assert lines[0] == "lint:biome: tsconfig.json does not resolve"
    assert "error TS5023: Unknown compiler option 'nonsense'" in lines[1]
    assert not written, "a config was generated from a refused project"

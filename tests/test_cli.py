"""End-to-end tests for the infinit research controller, on a toy project in a temp directory."""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from infinit_research import cli, report

# The toy harness is frozen; its behaviour comes from the editable x.txt, so a test can
# make "the agent" try a crash, a hang, a fake metric line, and so on.
SCORE = r"""
import os
import pathlib
import signal
import subprocess
import sys
import time

run_dir = pathlib.Path(os.environ.get("INFINIT_RESEARCH_RUN_DIR", "."))
mode = pathlib.Path("x.txt").read_text().strip()


def started():
    (run_dir / "started").write_text(str(os.getpgid(0)))


if mode == "crash":
    sys.exit(1)
if mode == "crash-after-metric":
    print("loss: 1.0")
    sys.exit(1)
if mode == "silent":
    sys.exit(0)
if mode == "twice":
    print("loss: 1.0\nloss: 1.0")
    sys.exit(0)
if mode == "nan":
    print("loss: nan")
    sys.exit(0)
if mode == "input":
    input()
if mode == "sleep":
    started()
    time.sleep(60)
if mode in ("group", "trap"):
    if mode == "trap":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen(["sleep", "60"])
    started()
    time.sleep(60)
if mode == "write":
    pathlib.Path("stray.txt").write_text("oops")
    mode = "1"
if mode == "touch-tracked":
    with open("README", "a") as handle:
        handle.write("changed by the run\n")
    mode = "1"
if mode == "touch-latin1":
    pathlib.Path("README").write_bytes(b"caf\xe9\n")
    mode = "1"
if mode == "artifact":
    (run_dir / "plot.png").write_bytes(b"\x89PNG")
    mode = "2"
if mode in ("tamper", "tamper-probes"):
    name = "runs.jsonl" if mode == "tamper" else "probes.jsonl"
    with open(f".infinit-research/{name}", "a") as handle:
        handle.write('{"forged": true}\n')
    mode = "1"
if mode == "lab":
    print(f"lab_visible: {int(os.path.exists('lab'))}")
    mode = "1"
if mode == "make-lab":
    os.makedirs("lab", exist_ok=True)
    pathlib.Path("lab/planted.txt").write_text("x")
    mode = "1"
x = float(mode)
print("steps: 10")
print(f"loss:          {(x - 3) ** 2:.6f}")
"""


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / ".infinit-research"

    def set_x(self, value: object) -> None:
        (self.root / "x.txt").write_text(f"{value}\n")

    def run(self, message: str = "try") -> int:
        return cli.main(["run", "-m", message])

    def records(self) -> list[cli.Record]:
        return cli.read_records(self.root)

    def head(self) -> str:
        return git(self.root, "rev-parse", "HEAD")

    def git(self, *args: str) -> str:
        return git(self.root, *args)

    def run_dir(self, run_id: str) -> Path:
        return self.state / "runs" / run_id


def init_args(
    goal: str = "minimize", timeout: float = 30.0, editable: tuple[str, ...] = ("x.txt",)
):
    command = f"{shlex.quote(sys.executable)} score.py"
    return ["init", "--metric", "loss", "--goal", goal, "--run", command, "--editable", *editable,
            "--timeout", str(timeout)]  # fmt: skip


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    monkeypatch.setattr(cli, "KILL_GRACE", 1.0)


def toy_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    monkeypatch.chdir(root)
    (root / "score.py").write_text(SCORE)
    (root / "x.txt").write_text("0\n")
    (root / "README").write_text("toy project\n")
    return root


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Project]:
    def factory(
        *,
        goal: str = "minimize",
        timeout: float = 30.0,
        editable: tuple[str, ...] = ("x.txt",),
        min_improvement: float = 0.0,
        commit: bool = True,
    ) -> Project:
        root = toy_repo(tmp_path, monkeypatch)
        assert cli.main(init_args(goal, timeout, editable)) == 0
        manifest = root / "infinit-research.toml"
        manifest.write_text(
            manifest.read_text().replace(
                "min_improvement = 0.0", f"min_improvement = {min_improvement}"
            )
        )
        if commit:
            git(root, "add", "-A")
            git(root, "commit", "-qm", "scaffold")
        return Project(root)

    return factory


@pytest.fixture
def project(make: Callable[..., Project]) -> Project:
    return make()


def wait_for(path: Path, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)


def assert_group_dead(pgid: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        assert time.monotonic() < deadline, f"process group {pgid} is still alive"
        time.sleep(0.05)


def spawn_cli(root: Path, message: str) -> subprocess.Popen[str]:
    command = [sys.executable, "-m", "infinit_research.cli", "run", "-m", message]
    return subprocess.Popen(
        command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


# init ---------------------------------------------------------------------------------


def test_init_writes_a_complete_project(project: Project) -> None:
    root = project.root
    manifest = cli.load_manifest(root)
    assert (manifest.metric, manifest.goal, manifest.editable) == ("loss", "minimize", ("x.txt",))
    program = (root / "program.md").read_text()
    assert "{{" not in program
    assert "`loss`" in program and "`x.txt`" in program
    assert "## Your research harness" in program
    assert (root / "lab" / "strategy.md").read_text().startswith("# Research strategy")
    assert (root / "lab" / "skills" / "when-stuck.md").exists()
    assert "## Steering (human)" in (root / "notes.md").read_text()
    ignored = set((root / ".gitignore").read_text().splitlines())
    assert {
        ".infinit-research/",
        "notes.md",
        "__pycache__/",
        ".claude/settings.local.json",
        "lab/",
    } <= ignored
    settings = json.loads((root / ".claude" / "settings.json").read_text())
    allow = set(settings["permissions"]["allow"])
    assert {"Bash(infinit-research run *)", "Edit(/x.txt)", "Edit(/notes.md)"} <= allow
    assert {"Edit(/lab/**)", "Bash(infinit-research review)"} <= allow
    assert settings["env"]["BASH_MAX_TIMEOUT_MS"] == "150000"
    assert Path(git(root, "rev-parse", "--show-toplevel")).resolve() == root
    assert project.git("status", "--porcelain") == ""


def test_init_refuses_to_overwrite_program_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "program.md").write_text("mine\n")
    assert cli.main(init_args()) == 2
    assert "git mv program.md" in capsys.readouterr().err
    assert (tmp_path / "program.md").read_text() == "mine\n"
    assert not (tmp_path / "infinit-research.toml").exists()


def test_init_refuses_a_tracked_notes_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q")
    (tmp_path / "notes.md").write_text("my project notes\n")
    git(tmp_path, "add", "notes.md")
    assert cli.main(init_args()) == 2
    assert "notes.md is tracked by git" in capsys.readouterr().err
    assert not (tmp_path / "infinit-research.toml").exists()


def test_init_refuses_an_existing_project(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(init_args()) == 2
    assert "infinit-research.toml already exists" in capsys.readouterr().err


def test_init_refuses_a_repo_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert cli.main(init_args()) == 2
    assert "repository root" in capsys.readouterr().err


def test_init_merges_existing_claude_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()
    existing = {"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}
    existing["env"] = {"BASH_MAX_TIMEOUT_MS": "999999999"}
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))
    assert cli.main(init_args()) == 0
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus"
    assert settings["permissions"]["allow"][0] == "Bash(ls)"
    assert "Bash(infinit-research run *)" in settings["permissions"]["allow"]
    assert settings["env"] == {
        "BASH_MAX_TIMEOUT_MS": "999999999",
        "BASH_DEFAULT_TIMEOUT_MS": "150000",
    }


def test_init_can_rerun_after_fixing_broken_claude_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{ broken")
    assert cli.main(init_args()) == 2
    assert "not valid JSON" in capsys.readouterr().err
    settings.write_text("{}")
    assert cli.main(init_args()) == 0


@pytest.mark.parametrize("editable", ["*", "*.md", ".claude/*", "../x.txt", "/abs.txt"])
def test_init_rejects_editable_globs_that_are_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    editable: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(init_args(editable=(editable,))) == 2
    assert "editable" in capsys.readouterr().err
    assert not (tmp_path / "infinit-research.toml").exists()


# run: keep, discard, revert ----------------------------------------------------------


def test_baseline_then_improvement_is_kept(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    assert project.run("baseline") == 0
    project.set_x(1)
    assert project.run("x=1") == 0
    base, better = project.records()
    assert (base["status"], base["metric"]) == ("keep", 9.0)
    assert (better["status"], better["metric"]) == ("keep", 4.0)
    assert better["metrics"]["steps"] == 10
    assert project.head() == better["commit"]
    assert project.git("status", "--porcelain") == ""
    assert "(was 9, -55.56%)" in capsys.readouterr().out


def test_regression_is_reverted_and_archived(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    parent = project.head()
    project.set_x(10)
    assert project.run("x=10") == 0
    record = project.records()[-1]
    assert (record["status"], record["metric"]) == ("discard", 49.0)
    assert project.head() == parent
    assert (project.root / "x.txt").read_text() == "0\n"
    archived = f"refs/infinit-research/{record['branch']}/{record['id']}"
    assert project.git("rev-parse", archived) == record["commit"]
    assert project.git("show", f"{record['commit']}:x.txt") == "10"
    assert f"git restore --source={record['commit'][:7]} -- x.txt" in capsys.readouterr().out


def test_a_tie_with_the_best_is_discarded(project: Project) -> None:
    project.run("baseline")
    project.set_x(6)
    project.run("same loss as the baseline")
    assert project.records()[-1]["status"] == "discard"


def test_min_improvement_is_relative_to_the_best(make: Callable[..., Project]) -> None:
    project = make(min_improvement=0.1)
    project.run("baseline")  # loss 9
    project.set_x(0.1)
    project.run("loss 8.41: 6.6% better, below the 10% floor")
    project.set_x(1)
    project.run("loss 4: 56% better")
    project.set_x(2.9)
    project.run("loss 0.01: an absolute floor sized for 9 would reject this")
    assert [r["status"] for r in project.records()] == ["keep", "discard", "keep", "keep"]


def test_unknown_manifest_keys_are_rejected(project: Project, capsys: pytest.CaptureFixture[str]):
    manifest = project.root / "infinit-research.toml"
    manifest.write_text(manifest.read_text().replace("min_improvement", "min_delta"))
    project.git("commit", "-qam", "old manifest")
    assert project.run() == 2
    assert "unknown keys metric.min_delta" in capsys.readouterr().err


def test_maximize_keeps_higher_values(make: Callable[..., Project]) -> None:
    project = make(goal="maximize")
    project.run("baseline")
    project.set_x(10)
    project.run("up")
    project.set_x(3)
    project.run("down")
    assert [r["status"] for r in project.records()] == ["keep", "keep", "discard"]


def test_crashed_first_run_on_head_is_not_a_baseline(make: Callable[..., Project]) -> None:
    project = make(commit=False)
    project.set_x("crash")
    project.git("add", "-A")
    project.git("commit", "-qm", "scaffold")
    scaffold = project.head()
    project.run("baseline")
    assert project.records()[-1]["status"] == "crash"
    assert project.head() == scaffold
    project.set_x(0)
    project.run("baseline again")
    assert project.records()[-1]["status"] == "keep"


def test_crashed_first_run_with_changes_reverts_to_the_users_commit(project: Project) -> None:
    scaffold = project.head()
    project.set_x("crash")
    project.run("baseline")
    assert project.records()[-1]["status"] == "crash"
    assert project.head() == scaffold
    assert (project.root / "x.txt").read_text() == "0\n"
    project.run("baseline on HEAD")
    assert project.records()[-1]["status"] == "keep"


def test_second_run_without_changes_is_refused(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    assert project.run("again") == 2
    assert "nothing changed" in capsys.readouterr().err
    assert len(project.records()) == 1
    assert not project.run_dir("0002").exists()


def test_commits_bypass_hooks_and_signing(project: Project) -> None:
    project.run("baseline")
    project.git("config", "commit.gpgsign", "true")
    project.git("config", "gpg.program", "false")
    for name in ("pre-commit", "prepare-commit-msg", "commit-msg"):
        hook = project.root / ".git" / "hooks" / name
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    project.set_x(1)
    assert project.run("x=1") == 0
    assert project.records()[-1]["status"] == "keep"


# run: repository preconditions --------------------------------------------------------


def test_run_needs_a_commit(make: Callable[..., Project], capsys: pytest.CaptureFixture[str]):
    project = make(commit=False)
    assert project.run() == 2
    assert "no commits" in capsys.readouterr().err


def test_run_needs_a_branch(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.git("checkout", "-q", "--detach")
    assert project.run() == 2
    assert "detached" in capsys.readouterr().err


def test_run_refuses_a_project_below_the_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "init", "-q")
    sub = tmp_path / "proj"
    sub.mkdir()
    manifest = cli.render_manifest("loss", "minimize", "true", 10, ["x.txt"])
    (sub / "infinit-research.toml").write_text(manifest)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    monkeypatch.chdir(sub)
    assert cli.main(["run", "-m", "x"]) == 2
    assert "repository root" in capsys.readouterr().err


def test_stop_file_refuses_new_runs(project: Project, capsys: pytest.CaptureFixture[str]):
    project.state.mkdir(exist_ok=True)
    (project.state / "stop").write_text("going to bed\n")
    assert project.run() == 2
    assert "STOP REQUESTED (going to bed)" in capsys.readouterr().err


def test_low_disk_space_is_refused(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MIN_FREE_BYTES", 1 << 62)
    assert project.run() == 2
    assert "disk left" in capsys.readouterr().err


def test_lock_blocks_a_concurrent_run(project: Project, capsys: pytest.CaptureFixture[str]):
    with cli.project_lock(project.root):
        assert project.run() == 2
    assert "in progress" in capsys.readouterr().err


# run: the guard -----------------------------------------------------------------------


def rename_into_editable(root: Path) -> None:
    (root / "x.txt").unlink()
    (root / "README").rename(root / "x.txt")


TAMPERING: dict[str, Callable[[Path], object]] = {
    "modify": lambda root: (root / "README").write_text("changed\n"),
    "delete": lambda root: (root / "README").unlink(),
    "untracked": lambda root: (root / "helper.py").write_text(""),
    "rename": rename_into_editable,
    "unicode": lambda root: (root / "é.txt").write_text(""),
    "program": lambda root: (root / "program.md").write_text("new rules\n"),
}


@pytest.mark.parametrize("tamper", TAMPERING)
def test_guard_rejects_changes_outside_the_editable_set(
    project: Project, capsys: pytest.CaptureFixture[str], tamper: str
) -> None:
    project.run("baseline")
    before = project.head()
    TAMPERING[tamper](project.root)
    assert project.run("tamper") == 2
    err = capsys.readouterr().err
    assert "outside the editable set" in err
    assert project.head() == before
    assert len(project.records()) == 1
    if tamper == "unicode":
        assert "é.txt" in err


def test_star_glob_does_not_unfreeze_subdirectories(
    make: Callable[..., Project], capsys: pytest.CaptureFixture[str]
) -> None:
    project = make(editable=("x.txt", "*.py"))
    project.run("baseline")
    (project.root / "harness").mkdir()
    (project.root / "harness" / "evil.py").write_text("x = 1\n")
    assert project.run("sneak") == 2
    assert "harness/evil.py" in capsys.readouterr().err


def test_long_path_lists_are_summarized(project: Project, capsys: pytest.CaptureFixture[str]):
    project.run("baseline")
    build = project.root / "target" / "release"
    build.mkdir(parents=True)
    for number in range(50):
        (build / f"obj{number}.o").write_text("")
    assert project.run("after a build") == 2
    err = capsys.readouterr().err
    assert "target/ (50 files)" in err
    assert "obj7.o" not in err


def test_summarize_caps_the_number_of_items() -> None:
    paths = [f"dir{number}/file.txt" for number in range(12)] + ["a/1", "a/2"]
    summary = cli.summarize(paths)
    assert summary.startswith("dir0/file.txt, dir1/file.txt")
    assert summary.endswith(", and 5 more")


def test_agent_commits_are_refused(project: Project, capsys: pytest.CaptureFixture[str]):
    project.run("baseline")
    project.set_x(1)
    project.git("commit", "-qam", "the agent committed on its own")
    assert project.run("after") == 2
    assert "no run has evaluated" in capsys.readouterr().err


def test_human_program_commits_are_allowed(project: Project) -> None:
    project.run("baseline")
    program = project.root / "program.md"
    program.write_text(program.read_text() + "\nFocus on x.\n")
    project.git("commit", "-qam", "steer the research")
    project.set_x(1)
    assert project.run("x=1") == 0
    assert project.records()[-1]["status"] == "keep"


def test_ignored_clutter_does_not_trip_the_guard(project: Project) -> None:
    project.run("baseline")
    (project.root / "__pycache__").mkdir()
    (project.root / "__pycache__" / "score.cpython-313.pyc").write_bytes(b"")
    (project.root / ".DS_Store").write_bytes(b"")
    project.set_x(1)
    assert project.run("x=1") == 0


# run: what counts as a failed run -----------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("crash-after-metric", "exit code 1"),
        ("silent", "printed 0 times"),
        ("twice", "printed 2 times"),
        ("nan", "not a finite number"),
        ("input", "exit code 1"),
    ],
)
def test_failed_runs_are_crashes_and_reverted(project: Project, mode: str, reason: str) -> None:
    project.run("baseline")
    parent = project.head()
    project.set_x(mode)
    started = time.monotonic()
    assert project.run(mode) == 0
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert record["reason"] is not None and reason in record["reason"]
    assert record["metric"] is None
    assert project.head() == parent
    assert time.monotonic() - started < 20


def test_a_run_that_only_deletes_files_prints_no_restore_command(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    (project.root / "x.txt").unlink()
    project.run("delete x.txt")
    assert project.records()[-1]["status"] == "crash"
    assert "restore with" not in capsys.readouterr().out


def test_files_written_into_the_repo_are_moved_aside(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    project.set_x("write")
    project.run("writes a stray file")
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert record["reason"] is not None and "stray.txt" in record["reason"]
    assert (project.run_dir(record["id"]) / "stray" / "stray.txt").read_text() == "oops"
    assert project.git("status", "--porcelain") == ""
    assert "moved to .infinit-research/runs/0002/stray/" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mode", "written"), [("touch-tracked", b"changed by the run"), ("touch-latin1", b"caf\xe9")]
)
def test_writes_to_tracked_files_are_undone_and_saved(
    project: Project, mode: str, written: bytes
) -> None:
    project.run("baseline")
    project.set_x(mode)
    project.run("touches README")
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert (project.root / "README").read_text() == "toy project\n"
    patch = project.run_dir(record["id"]) / "stray" / "tracked.patch"
    assert written in patch.read_bytes()
    assert project.git("status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("mode", "name"), [("tamper", "runs.jsonl"), ("tamper-probes", "probes.jsonl")]
)
def test_a_run_that_edits_the_records_is_undone_and_stops_the_loop(
    project: Project, mode: str, name: str
) -> None:
    project.run("baseline")
    project.set_x(mode)
    project.run("forges a record")
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert record["reason"] is not None and name in record["reason"]
    target = project.state / name
    assert not target.exists() or "forged" not in target.read_text()
    assert "forged" in (project.run_dir(record["id"]) / f"tampered-{name}").read_text()
    assert name in (project.state / "stop").read_text()


def test_consecutive_failures_request_a_stop(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "FAILURE_LIMIT", 3)
    project.run("baseline")
    for attempt in range(3):
        project.set_x("crash")
        project.run(f"crash {attempt}")
    assert "3 consecutive runs failed" in (project.state / "stop").read_text()
    project.set_x(1)
    assert project.run("after") == 2


@pytest.mark.parametrize(
    ("log", "value", "reason"),
    [
        ("loss:          0.997900\n", 0.9979, None),
        ("loss: 1e-3\n", 0.001, None),
        ("step 1\rstep 2\rloss: 2.5\n", 2.5, None),
        ("loss: nan\n", None, "not a finite number"),
        ("loss: inf\n", None, "not a finite number"),
        ("loss: -inf\n", None, "not a finite number"),
        ("loss: 1\nloss: 2\n", None, "printed 2 times"),
        ("nothing here\n", None, "printed 0 times"),
        ("  loss: 1\n", None, "printed 0 times"),
        ("lossy: 1\nloss.x: 3\n", None, "printed 0 times"),
    ],
)
def test_parse_log(tmp_path: Path, log: str, value: float | None, reason: str | None) -> None:
    path = tmp_path / "run.log"
    path.write_text(log)
    parsed = cli.parse_log(path, "loss")
    assert parsed.value == (pytest.approx(value) if value is not None else None)
    if reason is None:
        assert parsed.reason is None
    else:
        assert parsed.reason is not None and reason in parsed.reason


def test_parse_log_survives_bad_utf8(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(b"\xff\xfe garbage\nloss: 3\n")
    assert cli.parse_log(path, "loss").value == 3


def test_parse_log_matches_metric_names_literally(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_text("valXbpb: 9\nval.bpb: 1.5\n")
    assert cli.parse_log(path, "val.bpb").value == 1.5


# run: artifacts, status ---------------------------------------------------------------


def test_artifacts_are_listed_and_reported(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    project.set_x("artifact")
    project.run("with a plot")
    assert "artifacts in .infinit-research/runs/0002/: plot.png" in capsys.readouterr().out
    html = (project.state / "report.html").read_text()
    assert 'src="runs/0002/plot.png"' in html


def test_status_summarizes_the_branch(project: Project, capsys: pytest.CaptureFixture[str]):
    project.run("baseline")
    project.set_x(1)
    project.run("x=1")
    project.set_x(9)
    project.run("x=9")
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "3 runs: 2 keep, 1 discard" in out
    assert "baseline 9 -> best 4 (-55.56%)" in out
    assert "#0003 discard" in out


def test_check_leaves_git_and_records_alone(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    before = project.head()
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "check ok" in out and "loss=9" in out
    assert project.head() == before
    assert project.records() == []


def test_check_reports_files_the_run_wrote(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.set_x("write")
    assert cli.main(["check"]) == 1
    assert "wrote files into the repo (stray.txt)" in capsys.readouterr().out


# processes and signals ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["group", "trap"])
def test_timeout_kills_the_whole_process_group(make: Callable[..., Project], mode: str):
    project = make(timeout=2)
    project.run("baseline")
    project.set_x(mode)
    project.run("hangs")
    record = project.records()[-1]
    assert record["status"] == "timeout"
    assert record["seconds"] < 2 + cli.KILL_GRACE + 5
    assert_group_dead(int((project.run_dir(record["id"]) / "started").read_text()))


def test_sigterm_reverts_and_releases_the_lock(project: Project) -> None:
    project.run("baseline")
    parent = project.head()
    project.set_x("sleep")
    proc = spawn_cli(project.root, "sleepy")
    marker = project.run_dir("0002") / "started"
    wait_for(marker)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=30)
    assert proc.returncode == 130
    assert project.head() == parent
    assert_group_dead(int(marker.read_text()))
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert record["reason"] == "interrupted by SIGTERM"
    with cli.project_lock(project.root):
        pass


def test_a_signal_before_the_experiment_starts_is_recorded_and_reverted(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    project.run("baseline")
    parent = project.head()
    commit_all = cli.commit_all

    def commit_then_signal(root: Path, description: str) -> bool:
        created = commit_all(root, description)
        os.kill(os.getpid(), signal.SIGTERM)  # Esc in Claude Code while the run commits
        return created

    monkeypatch.setattr(cli, "commit_all", commit_then_signal)
    project.set_x(1)
    assert project.run("x=1") == 130
    record = project.records()[-1]
    assert (record["status"], record["reason"]) == ("crash", "interrupted by SIGTERM")
    assert project.head() == parent


def test_sigkill_is_recovered_by_the_next_run(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    parent = project.head()
    project.set_x("sleep")
    proc = spawn_cli(project.root, "sleepy")
    marker = project.run_dir("0002") / "started"
    wait_for(marker)
    proc.kill()
    proc.communicate(timeout=30)
    pgid = int(marker.read_text())
    os.killpg(pgid, 0)  # the orphaned experiment is still running
    project.set_x(1)  # meanwhile the agent moved on to its next idea
    assert project.run("next idea") == 2
    assert "recovered from run #0002" in capsys.readouterr().err
    assert_group_dead(pgid)
    assert project.head() == parent
    assert (project.root / "x.txt").read_text() == "1\n"
    assert project.records()[-1]["status"] == "crash"
    project.git("restore", "x.txt")  # what the recovery message suggests
    assert (project.root / "x.txt").read_text() == "0\n"
    project.set_x(1)
    assert project.run("next idea") == 0
    assert project.records()[-1]["status"] == "keep"


def test_a_kill_between_a_discard_and_its_revert_is_undone_by_the_next_run(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    parent = project.head()
    project.set_x(10)  # worse than the baseline, so it is discarded
    die_before_revert = (
        "import os, signal, sys\n"
        "from infinit_research import cli\n"
        "cli.revert = lambda root, trial: os.kill(os.getpid(), signal.SIGKILL)\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )
    command = [sys.executable, "-c", die_before_revert, "run", "-m", "x=10"]
    killed = subprocess.run(command, cwd=project.root, capture_output=True, check=False)
    assert killed.returncode == -signal.SIGKILL
    assert project.records()[-1]["status"] == "discard"
    assert project.head() != parent
    assert project.run("next idea") == 2
    assert "recovered from run #0002" in capsys.readouterr().err
    assert project.head() == parent
    assert len(project.records()) == 2  # the verdict is not recorded twice
    project.git("restore", "x.txt")
    assert (project.root / "x.txt").read_text() == "0\n"


def test_head_moving_during_a_run_is_reported(make: Callable[..., Project]) -> None:
    project = make(timeout=3)
    project.run("baseline")
    project.set_x("sleep")
    proc = spawn_cli(project.root, "sleepy")
    wait_for(project.run_dir("0002") / "started")
    project.git("commit", "-q", "--allow-empty", "-m", "someone else committed")
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == 2
    assert "HEAD moved during run #0002" in err
    assert project.records()[-1]["status"] == "timeout"


# the research harness in lab/ ----------------------------------------------------------


def test_init_refuses_an_existing_lab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    assert cli.main(init_args()) == 2
    assert "lab already exists" in capsys.readouterr().err
    assert not (tmp_path / "infinit-research.toml").exists()


@pytest.mark.parametrize("command", [["check"], ["run", "-m", "baseline"]])
def test_a_fresh_clone_gets_the_seed_lab(
    project: Project, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    shutil.rmtree(project.root / "lab")  # gitignored, so a clone has none
    assert cli.main(command) == 0
    assert "seeded lab/" in capsys.readouterr().err
    assert (project.root / "lab" / "strategy.md").read_text().startswith("# Research strategy")


def test_runs_record_and_save_the_lab_version(project: Project) -> None:
    lab = project.root / "lab"
    project.run("baseline")
    first = project.records()[-1]["lab"]
    saved = project.state / "lab-versions" / first
    assert (saved / "strategy.md").read_text() == (lab / "strategy.md").read_text()
    assert (saved / "skills" / "when-stuck.md").exists()
    assert (project.run_dir("0001") / "notes.md").exists()
    with (lab / "library.md").open("a") as handle:
        handle.write("a lesson\n")
    project.set_x(1)
    project.run("next")
    assert project.records()[-1]["lab"] not in ("", first)


def drop_strategy(lab: Path) -> None:
    (lab / "strategy.md").unlink()


def bloat_strategy(lab: Path) -> None:
    (lab / "strategy.md").write_text("line\n" * (cli.STRATEGY_LINES + 1))


def add_unnamed_skill(lab: Path) -> None:
    (lab / "skills" / "secret.md").write_text("# never named\n")


def drop_named_skill(lab: Path) -> None:
    (lab / "skills" / "when-stuck.md").unlink()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (drop_strategy, "lab/strategy.md is missing"),
        (bloat_strategy, "81 lines, over the limit of 80"),
        (add_unnamed_skill, "lab/skills/secret.md is not named in strategy.md"),
        (drop_named_skill, "names lab/skills/when-stuck.md, which is missing"),
    ],
)
def test_runs_refuse_a_lab_that_breaks_its_form(
    project: Project,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[Path], None],
    message: str,
) -> None:
    change(project.root / "lab")
    assert project.run("baseline") == 2
    assert message in capsys.readouterr().err
    assert project.records() == []
    assert not (project.state / "runs").exists() or not any((project.state / "runs").iterdir())


def test_runs_refuse_a_lab_too_big_to_save(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "LAB_MAX_BYTES", 100)
    assert project.run("baseline") == 2
    assert "lab/ holds" in capsys.readouterr().err


def test_lab_is_hidden_while_the_harness_runs(project: Project) -> None:
    project.set_x("lab")
    project.run("baseline")
    assert project.records()[-1]["metrics"]["lab_visible"] == 0
    assert (project.root / "lab" / "strategy.md").exists()
    assert cli.main(["check"]) == 0
    assert (project.root / "lab" / "strategy.md").exists()


def test_a_run_that_writes_lab_crashes(project: Project) -> None:
    project.run("baseline")
    project.set_x("make-lab")
    project.run("plants a lab")
    record = project.records()[-1]
    assert record["status"] == "crash"
    assert record["reason"] is not None and "the run wrote lab/" in record["reason"]
    assert (project.run_dir(record["id"]) / "stray" / "lab" / "planted.txt").exists()
    assert not (project.root / "lab" / "planted.txt").exists()
    assert (project.root / "lab" / "strategy.md").exists()


def test_a_killed_run_hides_lab_until_status_or_the_next_run(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    project.set_x("sleep")
    proc = spawn_cli(project.root, "sleepy")
    marker = project.run_dir("0002") / "started"
    wait_for(marker)
    proc.kill()
    proc.communicate(timeout=30)
    assert not (project.root / "lab").exists()
    assert cli.main(["status"]) == 0
    assert_group_dead(int(marker.read_text()))
    assert json.loads((project.state / "lock").read_text())["pgid"] is None  # killed only once
    assert (project.root / "lab" / "strategy.md").exists()
    project.set_x(1)
    assert project.run("next idea") == 2
    assert "recovered from run #0002" in capsys.readouterr().err
    assert project.records()[-1]["lab"] == project.records()[0]["lab"]


def test_a_review_is_due_after_each_window_until_reviews_md_has_its_section(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(report, "WINDOW_RUNS", 2)
    project.run("baseline")
    assert "review due" not in capsys.readouterr().out
    project.set_x(1)
    project.run("x=1")
    assert "review due" in capsys.readouterr().out
    write_probe(project, 'echo "quick: 1"\n')  # changing lab/ is not a review
    cli.main(["status"])
    assert "review due" in capsys.readouterr().out
    with (project.root / "lab" / "reviews.md").open("a") as handle:
        handle.write("## Review after #2\n")  # the run id's leading zeros are optional
    cli.main(["status"])
    assert "review due" not in capsys.readouterr().out


def test_review_prints_the_procedure_and_the_evidence(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    write_probe(project, 'echo "quick: 4"\n')
    cli.main(["probe", "-m", "screen x=1"])
    project.set_x(1)
    project.run("x=1")
    capsys.readouterr()
    assert cli.main(["review"]) == 0
    out = capsys.readouterr().out
    assert "# Review the research harness" in out
    assert "#0001-#0002  kept 2/2  failed 0  probes 1  gain +55.56%" in out
    assert "Runs in the last window:" in out and "#0002 keep" in out
    assert "probe #0001 ok       quick=4" in out


def write_probe(project: Project, body: str) -> None:
    (project.root / "lab" / "probe.sh").write_text(body)


def test_a_probe_runs_the_agents_evaluation_without_committing(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    project.run("baseline")
    head = project.head()
    write_probe(project, 'echo "arg: $1"\necho hi > "$INFINIT_RESEARCH_RUN_DIR/out.txt"\n')
    project.set_x(1)  # an idea the agent screens before spending a run on it
    capsys.readouterr()
    assert cli.main(["probe", "-m", "screen x=1", "7"]) == 0
    out = capsys.readouterr().out
    assert "probe #0001 ok" in out and "arg=7" in out and "out.txt" in out
    (probe,) = cli.read_probes(project.root)
    assert (probe["status"], probe["metrics"], probe["runs"]) == ("ok", {"arg": 7.0}, 1)
    assert (project.state / "lab-versions" / probe["lab"] / "probe.sh").exists()
    assert 'href="probes/0001/run.log"' in (project.state / "report.html").read_text()
    assert project.head() == head
    assert (project.root / "x.txt").read_text() == "1\n"
    assert len(project.records()) == 1


def test_probe_args_after_a_double_dash_reach_the_script_without_it(project: Project) -> None:
    write_probe(project, 'echo "count: $#"\n[ "$1" = --seed ] && echo "seed: $2"\n')
    assert cli.main(["probe", "-m", "flags", "--", "--seed", "7"]) == 0
    (probe,) = cli.read_probes(project.root)
    assert probe["metrics"] == {"count": 2.0, "seed": 7.0}


def test_a_probe_that_writes_into_the_repo_is_undone(project: Project) -> None:
    write_probe(project, "echo oops > stray.txt\necho 2 > x.txt\n")
    assert cli.main(["probe", "-m", "messy"]) == 0
    (probe,) = cli.read_probes(project.root)
    assert probe["status"] == "crash"
    assert probe["reason"] is not None and "stray.txt" in probe["reason"]
    assert not (project.root / "stray.txt").exists()
    assert (project.root / "x.txt").read_text() == "2\n"  # editable, so the probe may tune it


def test_probe_needs_a_probe_script(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["probe", "-m", "nothing to run"]) == 2
    assert "write lab/probe.sh first" in capsys.readouterr().err


def test_a_killed_probe_is_cleaned_up_by_the_next_run(project: Project) -> None:
    pgid = f'{shlex.quote(sys.executable)} -c "import os; print(os.getpgid(0))"'
    marker_tmp = '"$INFINIT_RESEARCH_RUN_DIR/started.tmp"'
    # mv makes the marker appear only once it holds the id, so the test never reads it empty
    script = (
        f'{pgid} > {marker_tmp}\nmv {marker_tmp} "$INFINIT_RESEARCH_RUN_DIR/started"\nsleep 60\n'
    )
    write_probe(project, script)
    command = [sys.executable, "-m", "infinit_research.cli", "probe", "-m", "slow"]
    proc = subprocess.Popen(command, cwd=project.root, stdout=subprocess.PIPE, text=True)
    marker = project.state / "probes" / "0001" / "started"
    wait_for(marker)
    proc.kill()
    proc.communicate(timeout=30)
    assert project.run("baseline") == 0
    assert_group_dead(int(marker.read_text()))
    assert project.records()[-1]["status"] == "keep"


# plugin launcher -----------------------------------------------------------------------

LAUNCHER = Path(__file__).resolve().parent.parent / "bin" / "infinit-research"


def test_plugin_launcher_runs_the_bundled_controller(tmp_path: Path) -> None:
    command = [str(LAUNCHER), *init_args()]
    init = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert init.returncode == 0, init.stderr
    assert "{{" not in (tmp_path / "program.md").read_text()
    status = subprocess.run(
        [str(LAUNCHER), "status"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert "no baseline yet" in status.stdout


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no system python3")
def test_plugin_launcher_runs_on_the_system_python(tmp_path: Path) -> None:
    env = {**os.environ, "PATH": "/usr/bin:/bin"}

    def launch(*args: str) -> subprocess.CompletedProcess[str]:
        command = [str(LAUNCHER), *args]
        return subprocess.run(
            command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
        )

    (tmp_path / "score.py").write_text(SCORE)
    (tmp_path / "x.txt").write_text("0\n")
    assert launch(*init_args()).returncode == 0
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "scaffold")
    assert launch("run", "-m", "baseline").returncode == 0
    (tmp_path / "x.txt").write_text("1\n")
    assert launch("run", "-m", "x=1").returncode == 0
    status = launch("status")
    assert "2 runs: 2 keep" in status.stdout, status.stderr


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("x.py", "*.py", True),
        ("harness/x.py", "*.py", False),
        ("solutions/a.py", "solutions/*.py", True),
        ("solutions/sub/a.py", "solutions/*.py", False),
        ("src/b.py", "src/**/*.py", True),
        ("src/a/b.py", "src/**/*.py", True),
        ("b.py", "**/*.py", True),
        ("solution.py", "./solution.py", True),
        ("Solution.py", "solution.py", False),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert cli.glob_match(path, pattern) is expected


def test_parse_toml_reads_the_manifest_subset() -> None:
    text = '# top\n[run]  # table comment\ncommand = "make # not a comment"  # comment\n'
    text += 'editable = ["a.py", "b/*.rs"]\ntimeout = 480\n[metric]\nmin_improvement = 2e-2\n'
    assert cli.parse_toml(text) == {
        "run": {"command": "make # not a comment", "editable": ["a.py", "b/*.rs"], "timeout": 480},
        "metric": {"min_improvement": 0.02},
    }


@pytest.mark.parametrize(
    "line", ["name = 'single quotes'", 'editable = [\n"a.py"]', "timeout", 'name = "a" b']
)
def test_parse_toml_rejects_what_it_does_not_support(line: str) -> None:
    with pytest.raises(cli.UsageError, match="line 2"):
        cli.parse_toml(f"[metric]\n{line}\n")

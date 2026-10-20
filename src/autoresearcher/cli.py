"""Experiment controller bundled with the autoresearcher Claude Code plugin.

The controller owns the parts of an autoresearch loop that agents get wrong when they do
them by hand: committing each experiment, enforcing the timeout, parsing the metric,
keeping or reverting, and logging. The agent only edits the files the manifest marks
editable, then calls ``autoresearch run``; the plugin's bin/ puts that command on its PATH.
It runs on any Python 3.9+, so it needs nothing beyond the system interpreter.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import fnmatch
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from types import FrameType
from typing import Any, TypedDict

from autoresearcher import report

MANIFEST = "autoresearch.toml"
PROGRAM = "program.md"
NOTES = "notes.md"
STATE = ".autoresearch"
GOALS = ("minimize", "maximize")
MANIFEST_KEYS = {
    "metric": ("name", "goal", "min_improvement"),
    "run": ("command", "timeout", "editable"),
}
# The agent must never be able to edit these: they define the rules of the game.
PROTECTED = (MANIFEST, PROGRAM, ".gitignore", ".claude/settings.json")
IGNORES = (
    f"{STATE}/",
    NOTES,
    "__pycache__/",
    ".DS_Store",
    ".venv/",
    ".claude/settings.local.json",
)
METRIC_NAME = re.compile(r"[A-Za-z_][\w.\-/]*")
METRIC_LINE = re.compile(r"([A-Za-z_][\w.\-/]*):[ \t]+(\S+)[ \t]*")
KILL_GRACE = 10.0
MIN_FREE_BYTES = 2 * 1024**3
FAILURE_LIMIT = 10
TAIL_LINES = 30
TAIL_CHARS = 300
SHOWN_ARTIFACTS = 6
SHOWN_PATHS = 8
NOTES_SEED = """\
# Research notes

## Steering (human)
<!-- Humans write guidance here at any time. The agent reads it every iteration and never
     edits this section. -->

## Current best

## What worked

## Dead ends

## Next ideas (ranked)
"""


class UsageError(Exception):
    """A problem the user or agent must fix; printed without a traceback."""


class Interrupted(BaseException):
    """Raised by the signal handler while an experiment is running."""


class Record(TypedDict):
    """One line of runs.jsonl."""

    id: str
    time: str
    branch: str
    commit: str
    status: str  # keep | discard | crash | timeout
    metric: float | None
    metrics: dict[str, float]
    seconds: float
    description: str
    reason: str | None


@dataclass(frozen=True)
class Manifest:
    """The validated contents of autoresearch.toml."""

    metric: str
    goal: str
    min_improvement: float  # fraction of the best a run must beat it by to be kept
    command: str
    timeout: float
    editable: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    """How the experiment process ended."""

    status: str  # ok | crash | timeout
    seconds: float
    reason: str | None = None


@dataclass(frozen=True)
class Parsed:
    """What the run log says."""

    metrics: dict[str, float]
    value: float | None
    reason: str | None


@dataclass(frozen=True)
class Trial:
    """One experiment in flight."""

    id: str
    branch: str
    description: str
    parent: str
    commit: str | None  # None when a baseline ran on HEAD without a new commit
    run_dir: Path

    @property
    def sha(self) -> str:
        return self.commit or self.parent

    def lock_state(self, pgid: int | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "branch": self.branch,
            "description": self.description,
            "parent": self.parent,
            "commit": self.commit,
            "pgid": pgid,
            "started": time.time(),
        }


# Manifest -----------------------------------------------------------------------------


def find_root(start: Path) -> Path:
    for path in (start, *start.parents):
        if (path / MANIFEST).is_file():
            return path
    raise UsageError(f"no {MANIFEST} in {start} or its parents; run `autoresearch init` first")


def load_manifest(root: Path) -> Manifest:
    return parse_manifest((root / MANIFEST).read_text(encoding="utf-8"))


def parse_toml(text: str) -> dict[str, Any]:
    """Parse the TOML subset a manifest uses: `[table]` lines and one-line `key = value` pairs."""
    data: dict[str, Any] = {}
    table = data
    decoder = json.JSONDecoder()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            table = data.setdefault(line[1:].split("]", 1)[0].strip(), {})
            continue
        key, equals, rest = (part.strip() for part in line.partition("="))
        try:
            if not equals:
                raise ValueError("expected `key = value`")
            value, end = decoder.raw_decode(rest)
            if rest[end:].strip()[:1] not in ("", "#"):
                raise ValueError("unexpected text after the value")
        except ValueError as exc:
            raise UsageError(
                f"{MANIFEST} line {number}: {exc}. Use double-quoted strings and keep arrays on "
                "one line."
            ) from exc
        table[key] = value
    return data


def parse_manifest(text: str) -> Manifest:
    data = parse_toml(text)
    unknown = unknown_keys(data)
    if unknown:
        raise UsageError(f"{MANIFEST}: unknown keys {', '.join(unknown)}")
    metric, run = _table(data, "metric"), _table(data, "run")
    min_improvement = metric.get("min_improvement", 0.0)
    if isinstance(min_improvement, bool) or not isinstance(min_improvement, (int, float)):
        raise UsageError(f"{MANIFEST}: [metric] min_improvement must be a number")
    editable = _field(run, "editable", list, "run")
    if not all(isinstance(glob, str) for glob in editable):
        raise UsageError(f"{MANIFEST}: [run] editable must be a list of strings")
    manifest = Manifest(
        metric=_field(metric, "name", str, "metric"),
        goal=_field(metric, "goal", str, "metric"),
        min_improvement=float(min_improvement),
        command=_field(run, "command", str, "run"),
        timeout=float(_field(run, "timeout", (int, float), "run")),
        editable=tuple(editable),
    )
    validate(manifest)
    return manifest


def unknown_keys(data: dict[str, Any]) -> list[str]:
    unknown = [key for key in data if key not in MANIFEST_KEYS]
    for table, keys in MANIFEST_KEYS.items():
        section = data.get(table, {})
        if isinstance(section, dict):
            unknown += [f"{table}.{key}" for key in section if key not in keys]
    return unknown


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    table = data.get(key, {})
    if not isinstance(table, dict):
        raise UsageError(f"{MANIFEST}: [{key}] must be a table")
    return table


def _field(table: dict[str, Any], key: str, kind: Any, where: str) -> Any:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise UsageError(f"{MANIFEST}: [{where}] {key} is missing or has the wrong type")
    return value


def validate(manifest: Manifest) -> None:
    problems = []
    if not METRIC_NAME.fullmatch(manifest.metric):
        problems.append(f"metric name {manifest.metric!r} must look like an identifier")
    if manifest.goal not in GOALS:
        problems.append(f"goal must be one of {', '.join(GOALS)}")
    if not (math.isfinite(manifest.min_improvement) and manifest.min_improvement >= 0):
        problems.append("min_improvement must be a number >= 0")
    if not (math.isfinite(manifest.timeout) and manifest.timeout > 0):
        problems.append("timeout must be a positive number of seconds")
    if not manifest.command.strip():
        problems.append("run command is empty")
    problems += editable_problems(manifest.editable)
    if problems:
        raise UsageError(f"{MANIFEST}: " + "; ".join(problems))


def editable_problems(globs: tuple[str, ...]) -> list[str]:
    if not globs:
        return ["editable must list at least one glob"]
    problems = []
    for glob in globs:
        path = PurePosixPath(glob)
        if not glob or path.is_absolute() or ".." in path.parts:
            problems.append(f"editable glob {glob!r} must be a relative path inside the project")
    covered = [name for name in PROTECTED if is_editable(name, globs)]
    if covered:
        problems.append(f"editable globs must not cover {', '.join(covered)}")
    return problems


def is_editable(path: str, globs: tuple[str, ...]) -> bool:
    return any(glob_match(path, glob) for glob in globs)


def glob_match(path: str, pattern: str) -> bool:
    """Match a repo path against a glob in which `*` stays in one directory and `**` spans any."""
    patterns = [part for part in pattern.split("/") if part not in ("", ".")]
    return _match_parts(path.split("/"), patterns)


def _match_parts(parts: list[str], patterns: list[str]) -> bool:
    if not patterns:
        return not parts
    if patterns[0] == "**":
        return any(_match_parts(parts[i:], patterns[1:]) for i in range(len(parts) + 1))
    if not parts or not fnmatch.fnmatchcase(parts[0], patterns[0]):
        return False
    return _match_parts(parts[1:], patterns[1:])


# Git ----------------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise UsageError(f"`git {' '.join(args)}` failed: {detail}")
    return proc.stdout


def git_ok(root: Path, *args: str) -> bool:
    return (
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=False).returncode == 0
    )


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def current_branch(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "symbolic-ref", "-q", "--short", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None


def toplevel(root: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


def check_repo(root: Path) -> str:
    """Validate the git state a run needs and return the current branch."""
    top = toplevel(root)
    if top is None:
        raise UsageError(f"{root} is not a git repository; run `git init` and commit the harness")
    if top != root.resolve():
        raise UsageError(f"{MANIFEST} must sit at the git repository root ({top}), not in {root}")
    if not git_ok(root, "rev-parse", "--verify", "-q", "HEAD"):
        raise UsageError(
            "the repository has no commits; commit the harness first: "
            "git add -A && git commit -m 'Scaffold autoresearch project'"
        )
    branch = current_branch(root)
    if branch is None:
        raise UsageError("HEAD is detached; check out a branch: git switch -c autoresearch/<tag>")
    return branch


def dirty_paths(root: Path) -> list[tuple[str, str]]:
    out = git(root, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    return [(entry[:2], entry[3:]) for entry in out.split("\0") if entry]


def summarize(paths: list[str]) -> str:
    """List paths, collapsing a directory with several files (a build output, say) to one item."""
    groups: dict[str, list[str]] = {}
    for path in paths:
        groups.setdefault(path.split("/", 1)[0], []).append(path)
    items = [
        files[0] if len(files) == 1 else f"{top}/ ({len(files)} files)"
        for top, files in groups.items()
    ]
    more = f", and {len(items) - SHOWN_PATHS} more" if len(items) > SHOWN_PATHS else ""
    return ", ".join(items[:SHOWN_PATHS]) + more


def guard(root: Path, manifest: Manifest) -> None:
    frozen = [p for _, p in dirty_paths(root) if not is_editable(p, manifest.editable)]
    if frozen:
        raise UsageError(
            f"files outside the editable set ({', '.join(manifest.editable)}) changed: "
            f"{summarize(frozen)}. Undo them (git restore <file>, or delete new files). "
            "Gitignore build output and caches, write other outputs to $AUTORESEARCH_RUN_DIR, "
            "and commit program.md edits yourself."
        )


def check_drift(root: Path, kept_commit: str) -> None:
    out = git(root, "diff", "--name-only", "-z", kept_commit, "HEAD", "--", ".", f":!{PROGRAM}")
    changed = [path for path in out.split("\0") if path]
    if changed:
        raise UsageError(
            f"HEAD contains changes no run has evaluated ({', '.join(changed)}). Only "
            f"`autoresearch run` should commit; undo with `git reset --keep {kept_commit[:7]}`. "
            "If you changed the harness on purpose, start a new branch for a fresh baseline."
        )


def commit_all(root: Path, description: str) -> bool:
    git(root, "add", "-A")
    if git_ok(root, "diff", "--cached", "--quiet"):
        return False
    hooks_off = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false")
    git(root, *hooks_off, "commit", "-q", "-m", description)
    return True


def archive_ref(branch: str, run_id: str) -> str:
    # Per branch, because worktrees share refs but each keeps its own run counter.
    return f"refs/autoresearch/{branch}/{run_id}"


def changed_files(root: Path, parent: str, commit: str) -> list[str]:
    out = git(root, "diff", "--name-only", "--diff-filter=d", "-z", parent, commit)
    return [path for path in out.split("\0") if path]


def quarantine(root: Path, run_dir: Path, spare: tuple[str, ...] = ()) -> list[str]:
    """Move what the experiment wrote into the repo to run_dir/stray, keeping a copy.

    Paths matching ``spare`` are left alone.
    """
    dirty = [(code, path) for code, path in dirty_paths(root) if not is_editable(path, spare)]
    if not dirty:
        return []
    stray = run_dir / "stray"
    stray.mkdir(parents=True, exist_ok=True)
    tracked = [path for code, path in dirty if code != "??"]
    if tracked:
        patch = git(root, "--literal-pathspecs", "diff", "--binary", "HEAD", "--", *tracked)
        (stray / "tracked.patch").write_text(patch)
        git(root, "--literal-pathspecs", "restore", "--staged", "--worktree", "--", *tracked)
    for path in (path for code, path in dirty if code == "??"):
        target = stray / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(root / path, target)
    return [path for _, path in dirty]


# Records ------------------------------------------------------------------------------


def records_path(root: Path) -> Path:
    return root / STATE / "runs.jsonl"


def read_records(root: Path) -> list[Record]:
    path = records_path(root)
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise UsageError(f"{path} line {number} is not JSON ({exc}); fix or delete it") from exc
    return records


def branch_records(root: Path, branch: str) -> list[Record]:
    return [record for record in read_records(root) if record["branch"] == branch]


def append_record(root: Path, record: Record) -> None:
    with records_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def next_id(root: Path, records: list[Record]) -> str:
    runs = root / STATE / "runs"
    numbers = [int(record["id"]) for record in records]
    if runs.exists():
        numbers += [int(path.name) for path in runs.iterdir() if path.name.isdigit()]
    return f"{max(numbers, default=0) + 1:04d}"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stop_path(root: Path) -> Path:
    return root / STATE / "stop"


def request_stop(root: Path, reason: str) -> None:
    stop_path(root).parent.mkdir(exist_ok=True)
    stop_path(root).write_text(reason + "\n", encoding="utf-8")


def stop_reason(root: Path) -> str | None:
    path = stop_path(root)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip() or "requested by a human"


# Lock and recovery ----------------------------------------------------------------------


@contextlib.contextmanager
def project_lock(root: Path) -> Generator[int]:
    """Hold an exclusive lock on the project; the kernel releases it if we die."""
    (root / STATE).mkdir(exist_ok=True)
    fd = os.open(root / STATE / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UsageError("another autoresearch run or check is in progress here") from None
        yield fd
    finally:
        os.close(fd)


def read_lock(fd: int) -> dict[str, Any]:
    raw = os.pread(fd, 1 << 16, 0)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("autoresearch: warning: ignoring a corrupt lock file", file=sys.stderr)
        return {}


def write_lock(fd: int, state: dict[str, Any]) -> None:
    os.ftruncate(fd, 0)
    if state:
        os.pwrite(fd, json.dumps(state).encode(), 0)


def recover(root: Path, fd: int, manifest: Manifest) -> None:
    """Clean up after a controller that was killed (SIGKILL, OOM) in the middle of a run.

    The interrupted experiment's changes stay in the working tree, uncommitted, next to any
    edits made since; the caller must look at them before running again.
    """
    state = read_lock(fd)
    if not state:
        return
    if isinstance(state.get("pgid"), int):
        # shortcut: a dead group's id could in theory be reused by now; compare process start
        # times if that ever matters.
        kill_leftovers(state["pgid"])
    recorded = any(record["id"] == state["id"] for record in read_records(root))
    if not recorded:
        quarantine(root, root / STATE / "runs" / state["id"], spare=manifest.editable)
        commit = state.get("commit")
        if commit and head(root) == commit:
            git(root, "update-ref", archive_ref(state["branch"], state["id"]), commit)
            git(root, "reset", "-q", "--soft", state["parent"])
        append_record(root, interrupted_record(state))
    write_lock(fd, {})
    if not recorded:
        raise UsageError(
            f"recovered from run #{state['id']}, whose controller was killed mid-run. Its "
            "changes are uncommitted in the working tree (see `git status`); keep or "
            "`git restore` them, then run again."
        )


def interrupted_record(state: dict[str, Any]) -> Record:
    return Record(
        id=state["id"],
        time=now(),
        branch=state["branch"],
        commit=state.get("commit") or state["parent"],
        status="crash",
        metric=None,
        metrics={},
        seconds=round(time.time() - state.get("started", time.time()), 1),
        description=state["description"],
        reason="interrupted: the controller was killed during the run",
    )


# Running an experiment ------------------------------------------------------------------


class Signals:
    """Turn TERM/HUP/INT into Interrupted while an experiment runs; defer them otherwise."""

    WATCHED = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)

    def __init__(self) -> None:
        self.received: int | None = None
        self.live = False
        self._previous: dict[int, Any] = {}

    def __enter__(self) -> Signals:
        for sig in self.WATCHED:
            self._previous[sig] = signal.signal(sig, self._handle)
        return self

    def __exit__(self, *_exc: object) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)

    def _handle(self, signum: int, _frame: FrameType | None) -> None:
        self.received = signum
        if self.live:
            raise Interrupted(signum)


def kill_leftovers(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def stop_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the experiment's process group, then SIGKILL what is left after a grace period."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=KILL_GRACE)
    kill_leftovers(proc.pid)
    proc.wait()


def signal_name(signum: int) -> str:
    return signal.Signals(signum).name


def execute(
    root: Path,
    manifest: Manifest,
    run_dir: Path,
    signals: Signals,
    on_spawn: Callable[[int], None],
) -> Outcome:
    """Run the command, and treat any change it makes to the records as tampering."""
    path = records_path(root)
    before = path.read_bytes() if path.exists() else None
    outcome = run_process(root, manifest, run_dir, signals, on_spawn)
    after = path.read_bytes() if path.exists() else None
    if after == before:
        return outcome
    if after is not None:
        (run_dir / f"tampered-{path.name}").write_bytes(after)
    if before is None:
        path.unlink()
    else:
        path.write_bytes(before)
    reason = (
        f"the run changed {path.relative_to(root)}, which only autoresearch writes; it was "
        f"restored and the changed copy saved in {run_dir.relative_to(root)}/"
    )
    request_stop(root, reason)
    print(f"autoresearch: STOP REQUESTED: {reason}", file=sys.stderr)
    return Outcome("crash", outcome.seconds, reason)


def run_process(
    root: Path,
    manifest: Manifest,
    run_dir: Path,
    signals: Signals,
    on_spawn: Callable[[int], None],
) -> Outcome:
    if signals.received:
        return Outcome("crash", 0.0, f"interrupted by {signal_name(signals.received)}")
    env = {**os.environ, "AUTORESEARCH_RUN_DIR": str(run_dir), "PYTHONUNBUFFERED": "1"}
    start = time.monotonic()
    with (run_dir / "run.log").open("wb") as log:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", manifest.command],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            on_spawn(proc.pid)
            signals.live = True
            if signals.received:
                raise Interrupted(signals.received)
            code = proc.wait(timeout=manifest.timeout)
        except subprocess.TimeoutExpired:
            signals.live = False
            stop_group(proc)
            reason = f"killed after {manifest.timeout:g} s"
            return Outcome("timeout", time.monotonic() - start, reason)
        except Interrupted as exc:
            signals.live = False
            stop_group(proc)
            reason = f"interrupted by {signal_name(exc.args[0])}"
            return Outcome("crash", time.monotonic() - start, reason)
        finally:
            signals.live = False
            kill_leftovers(proc.pid)
    seconds = time.monotonic() - start
    return Outcome("ok", seconds) if code == 0 else Outcome("crash", seconds, f"exit code {code}")


def parse_log(log_path: Path, name: str) -> Parsed:
    metrics: dict[str, float] = {}
    hits: list[str] = []
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = METRIC_LINE.fullmatch(line.rstrip())
            if match is None:
                continue
            key, raw = match.groups()
            if key == name:
                hits.append(raw)
            value = to_float(raw)
            if value is not None and math.isfinite(value):
                metrics[key] = value
    if len(hits) != 1:
        return Parsed(metrics, None, f"`{name}:` printed {len(hits)} times (print it exactly once)")
    value = to_float(hits[0])
    if value is None or not math.isfinite(value):
        return Parsed(metrics, None, f"`{name}: {hits[0]}` is not a finite number")
    return Parsed(metrics, value, None)


def to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def classify(outcome: Outcome, parsed: Parsed, wrote: list[str]) -> tuple[str, str | None]:
    if outcome.status != "ok":
        return outcome.status, outcome.reason
    if parsed.reason:
        return "crash", parsed.reason
    if wrote:
        return "crash", (
            f"the run wrote files into the repo ({summarize(wrote)}); gitignore build output "
            "and caches, and write other outputs to $AUTORESEARCH_RUN_DIR"
        )
    return "ok", None


def decide(status: str, value: float | None, kept: list[Record], manifest: Manifest) -> str:
    if status != "ok" or value is None:
        return status if status != "ok" else "crash"
    if not kept:
        return "keep"
    best = kept[-1]["metric"]
    assert best is not None
    gain = best - value if manifest.goal == "minimize" else value - best
    return "keep" if gain > manifest.min_improvement * abs(best) else "discard"


def log_tail(log_path: Path) -> str:
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=TAIL_LINES)
    return "\n".join(f"  | {line.rstrip()[:TAIL_CHARS]}" for line in lines)


# run ----------------------------------------------------------------------------------


def preflight(root: Path) -> str:
    branch = check_repo(root)
    reason = stop_reason(root)
    if reason is not None:
        raise UsageError(
            f"STOP REQUESTED ({reason}): end your turn now. Delete {STATE}/stop to resume."
        )
    free = shutil.disk_usage(root).free
    if free < MIN_FREE_BYTES:
        raise UsageError(
            f"only {free / 1024**3:.1f} GB of disk left; old runs are in {STATE}/runs/"
        )
    return branch


def start_trial(root: Path, branch: str, trial_id: str, description: str, baseline: bool) -> Trial:
    run_dir = root / STATE / "runs" / trial_id
    run_dir.mkdir(parents=True)
    parent = head(root)
    created = commit_all(root, description)
    if not created and not baseline:
        run_dir.rmdir()
        raise UsageError("nothing changed since the last run; edit the editable files first")
    return Trial(trial_id, branch, description, parent, head(root) if created else None, run_dir)


def finish_trial(
    root: Path, manifest: Manifest, trial: Trial, outcome: Outcome, kept: list[Record]
) -> Record:
    parsed = parse_log(trial.run_dir / "run.log", manifest.metric)
    status, reason = classify(outcome, parsed, quarantine(root, trial.run_dir))
    verdict = decide(status, parsed.value, kept, manifest)
    record = Record(
        id=trial.id,
        time=now(),
        branch=trial.branch,
        commit=trial.sha,
        status=verdict,
        metric=parsed.value if verdict in ("keep", "discard") else None,
        metrics=parsed.metrics,
        seconds=round(outcome.seconds, 1),
        description=trial.description,
        reason=reason,
    )
    append_record(root, record)
    if verdict != "keep" and trial.commit:
        revert(root, trial)
    return record


def revert(root: Path, trial: Trial) -> None:
    assert trial.commit is not None
    current = head(root)
    if current != trial.commit:
        raise UsageError(
            f"HEAD moved during run #{trial.id} (now {current[:7]}, expected "
            f"{trial.commit[:7]}); not reverting. Inspect it, then run "
            f"`git reset --keep {trial.parent[:7]}`."
        )
    git(root, "update-ref", archive_ref(trial.branch, trial.id), trial.commit)
    try:
        git(root, "reset", "-q", "--keep", trial.parent)
    except UsageError as exc:
        raise UsageError(
            f"could not revert run #{trial.id}: {exc}. Resolve it, then run "
            f"`git reset --keep {trial.parent[:7]}`."
        ) from exc


def format_number(value: float) -> str:
    return f"{value:.6g}"


def percent(value: float, reference: float) -> str:
    if reference == 0:
        return ""
    return f", {(value - reference) / abs(reference) * 100:+.2f}%"


def format_verdict(record: Record, metric: str, best_before: float | None) -> str:
    prefix = f"#{record['id']} {record['status']:<8}"
    seconds = f"{record['seconds']:g}s"
    value = record["metric"]
    if value is None:
        return f"{prefix} {record['reason']}  {seconds}"
    if best_before is None:
        return f"{prefix} {metric} {format_number(value)} (baseline)  {seconds}"
    label = "was" if record["status"] == "keep" else "best"
    versus = f"{label} {format_number(best_before)}{percent(value, best_before)}"
    return f"{prefix} {metric} {format_number(value)} ({versus})  {seconds}"


def verdict_details(root: Path, trial: Trial, record: Record) -> list[str]:
    lines = []
    relative = trial.run_dir.relative_to(root)
    if record["status"] != "keep" and trial.commit:
        files = " ".join(shlex.quote(p) for p in changed_files(root, trial.parent, trial.commit))
        lines.append(f"  restore with: git restore --source={trial.commit[:7]} -- {files}")
    if (trial.run_dir / "stray").exists():
        lines.append(f"  files the run wrote into the repo were moved to {relative}/stray/")
    artifacts = sorted(
        p.name for p in trial.run_dir.iterdir() if p.name not in ("run.log", "stray")
    )
    if artifacts:
        more = ", ..." if len(artifacts) > SHOWN_ARTIFACTS else ""
        shown = ", ".join(artifacts[:SHOWN_ARTIFACTS])
        lines.append(f"  artifacts in {relative}/: {shown}{more}")
    if record["status"] in ("crash", "timeout"):
        lines.append(f"  last lines of {relative}/run.log:")
        lines.append(log_tail(trial.run_dir / "run.log"))
    return lines


def after_run(root: Path, manifest: Manifest, branch: str) -> None:
    """Refresh the report and request a stop when every recent run failed."""
    mine = branch_records(root, branch)
    try:
        refresh_report(root, manifest, branch, mine)
    except OSError as exc:
        print(f"autoresearch: warning: could not write the report: {exc}", file=sys.stderr)
    recent = mine[-FAILURE_LIMIT:]
    if len(recent) == FAILURE_LIMIT and all(r["status"] in ("crash", "timeout") for r in recent):
        reason = (
            f"{FAILURE_LIMIT} consecutive runs failed (last: {recent[-1]['reason']}). The "
            f"environment may be broken; check the logs in {STATE}/runs/, then delete "
            f"{STATE}/stop to resume."
        )
        request_stop(root, reason)
        print(f"autoresearch: STOP REQUESTED: {reason}", file=sys.stderr)


def refresh_report(root: Path, manifest: Manifest, branch: str, records: list[Record]) -> Path:
    title = f"{root.name} · {branch}"
    return report.write_report(root / STATE, title, manifest.metric, manifest.goal, records)


def cmd_run(args: argparse.Namespace) -> int:
    description = " ".join(args.message.split())
    if not description:
        raise UsageError("describe the experiment: autoresearch run -m '<what you tried>'")
    root = find_root(Path.cwd())
    manifest = load_manifest(root)
    with project_lock(root) as fd, Signals() as signals:
        recover(root, fd, manifest)
        branch = preflight(root)
        records = read_records(root)
        kept = [r for r in records if r["branch"] == branch and r["status"] == "keep"]
        guard(root, manifest)
        if kept:
            check_drift(root, kept[-1]["commit"])
        if signals.received:
            return 130
        trial = start_trial(root, branch, next_id(root, records), description, baseline=not kept)
        write_lock(fd, trial.lock_state())
        outcome = execute(
            root,
            manifest,
            trial.run_dir,
            signals,
            lambda pid: write_lock(fd, trial.lock_state(pid)),
        )
        try:
            record = finish_trial(root, manifest, trial, outcome, kept)
        finally:
            write_lock(fd, {})
        print(format_verdict(record, manifest.metric, kept[-1]["metric"] if kept else None))
        for line in verdict_details(root, trial, record):
            print(line)
        after_run(root, manifest, branch)
    return 130 if signals.received else 0


# check, status, report ------------------------------------------------------------------


def cmd_check(_args: argparse.Namespace) -> int:
    root = find_root(Path.cwd())
    manifest = load_manifest(root)
    with project_lock(root), Signals() as signals:
        run_dir = root / STATE / "check"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        before = {path for _, path in dirty_paths(root)}
        outcome = execute(root, manifest, run_dir, signals, lambda _pid: None)
        parsed = parse_log(run_dir / "run.log", manifest.metric)
        wrote = sorted({path for _, path in dirty_paths(root)} - before)
        status, reason = classify(outcome, parsed, wrote)
    shown = ", ".join(f"{key}={format_number(value)}" for key, value in parsed.metrics.items())
    print(f"check {status}  {outcome.seconds:.1f}s  {reason or ''}".rstrip())
    print(f"  metrics: {shown or 'none'}")
    if status != "ok":
        print(f"  last lines of {(run_dir / 'run.log').relative_to(root)}:")
        print(log_tail(run_dir / "run.log"))
    if signals.received:
        return 130
    return 0 if status == "ok" else 1


def summary_line(manifest: Manifest, branch: str, records: list[Record]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    tally = ", ".join(f"{n} {status}" for status, n in counts.items()) or "none"
    return f"branch {branch} · {manifest.goal} {manifest.metric} · {len(records)} runs: {tally}"


def best_line(records: list[Record]) -> str:
    kept = [record for record in records if record["status"] == "keep"]
    if not kept:
        return 'no baseline yet: start with `autoresearch run -m "baseline"`'
    base, best = kept[0], kept[-1]
    assert base["metric"] is not None and best["metric"] is not None
    change = percent(best["metric"], base["metric"]).lstrip(", ")
    return (
        f"baseline {format_number(base['metric'])} -> best {format_number(best['metric'])}"
        f"{f' ({change})' if change else ''} at #{best['id']} {best['commit'][:7]} "
        f"{best['description']!r}"
    )


def run_line(record: Record) -> str:
    value = format_number(record["metric"]) if record["metric"] is not None else "-"
    reason = f"  [{record['reason']}]" if record["reason"] else ""
    return (
        f"  #{record['id']} {record['status']:<8} {value:>12}  {record['seconds']:>7g}s  "
        f"{record['description']}{reason}"
    )


def status_text(manifest: Manifest, branch: str, records: list[Record], limit: int) -> str:
    lines = [summary_line(manifest, branch, records), best_line(records)]
    lines += [run_line(record) for record in (records[-limit:] if limit > 0 else [])]
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    root = find_root(Path.cwd())
    manifest = load_manifest(root)
    branch = current_branch(root) or "HEAD"
    print(status_text(manifest, branch, branch_records(root, branch), args.n))
    reason = stop_reason(root)
    if reason is not None:
        print(f"STOP REQUESTED: {reason}")
    print(f"notes: {NOTES} · report: {STATE}/report.html")
    return 0


# init ---------------------------------------------------------------------------------


def render_manifest(
    metric: str, goal: str, command: str, timeout: float, editable: list[str]
) -> str:
    quote = json.dumps
    globs = ", ".join(quote(glob) for glob in editable)
    return (
        "# autoresearch manifest. Frozen during a run; change it only when starting a new branch.\n"
        "[metric]\n"
        f"name = {quote(metric)}  # the run prints this name, a colon, and a number, exactly once\n"
        f"goal = {quote(goal)}\n"
        "min_improvement = 0.0  # keep a run only if it beats the best by this fraction (0.02 = 2%)"
        "\n"
        "\n"
        "[run]\n"
        f"command = {quote(command)}\n"
        f"timeout = {timeout:g}  # seconds; the whole process group is killed after this\n"
        f"editable = [{globs}]  # globs the agent may change; everything else is frozen\n"
    )


def render_program(manifest: Manifest) -> str:
    template = resources.files("autoresearcher").joinpath("program.md").read_text(encoding="utf-8")
    values = {
        "metric": manifest.metric,
        "goal_word": "low" if manifest.goal == "minimize" else "high",
        "editable": ", ".join(f"`{glob}`" for glob in manifest.editable),
        "timeout": f"{manifest.timeout:g}",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def bash_timeout_env(timeout: float) -> dict[str, str]:
    milliseconds = str(int((timeout + 120) * 1000))
    return {"BASH_DEFAULT_TIMEOUT_MS": milliseconds, "BASH_MAX_TIMEOUT_MS": milliseconds}


def loop_permissions(editable: tuple[str, ...]) -> list[str]:
    rules = [
        "Bash(autoresearch run *)",
        "Bash(autoresearch status)",
        "Bash(autoresearch status *)",
        "Bash(git status)",
        "Bash(git status *)",
        "Bash(git diff)",
        "Bash(git diff *)",
        "Bash(git log *)",
        "Bash(git show *)",
        "Bash(git restore *)",
        f"Bash(rm -f {STATE}/stop)",
    ]
    rules += [f"Edit(/{path})" for path in (*editable, NOTES, f"{STATE}/stop")]
    return rules


def write_claude_settings(root: Path, manifest: Manifest) -> None:
    """Merge the loop's permissions and Bash timeout into .claude/settings.json."""
    path = root / ".claude" / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path} is not valid JSON ({exc}); fix it and rerun init") from exc
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    if not isinstance(allow, list):
        raise UsageError(f"{path}: permissions.allow must be a list")
    allow += [rule for rule in loop_permissions(manifest.editable) if rule not in allow]
    env = settings.setdefault("env", {})
    for key, value in bash_timeout_env(manifest.timeout).items():
        current = str(env.get(key, ""))
        if not (current.isdigit() and int(current) >= int(value)):
            env[key] = value
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def update_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in IGNORES if line not in text.splitlines()]
    if missing:
        prefix = "" if not text or text.endswith("\n") else "\n"
        addition = prefix + "# autoresearch\n" + "\n".join(missing) + "\n"
        path.write_text(text + addition, encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    root = Path.cwd()
    for name in (MANIFEST, PROGRAM):
        if (root / name).exists():
            hint = " (move it aside with `git mv program.md program.orig.md`, then merge it)"
            raise UsageError(f"{name} already exists" + (hint if name == PROGRAM else ""))
    top = toplevel(root)
    if top is not None and top != root.resolve():
        raise UsageError(f"an autoresearch project must be a git repository root; {top} is")
    text = render_manifest(args.metric, args.goal, args.run_command, args.timeout, args.editable)
    manifest = parse_manifest(text)
    if top is None:
        git(root, "init", "-q", "-b", "main")
    (root / MANIFEST).write_text(text, encoding="utf-8")
    (root / PROGRAM).write_text(render_program(manifest), encoding="utf-8")
    if not (root / NOTES).exists():
        (root / NOTES).write_text(NOTES_SEED, encoding="utf-8")
    update_gitignore(root)
    write_claude_settings(root, manifest)
    print(f"initialized an autoresearch project in {root}")
    print("next: fill in the research brief in program.md, run `autoresearch check`, then commit")
    return 0


# Entry point --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoresearch", description="Run autoresearch experiments with guardrails."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="turn the current directory into an autoresearch project")
    init.add_argument("--metric", required=True, help="metric the run prints as 'name: value'")
    init.add_argument("--goal", required=True, choices=GOALS)
    init.add_argument("--run", dest="run_command", required=True, help="one experiment's command")
    init.add_argument("--editable", required=True, nargs="+", help="globs the agent may change")
    init.add_argument("--timeout", type=float, default=480.0, help="seconds before a run is killed")
    init.set_defaults(func=cmd_init)

    check = sub.add_parser("check", help="run the experiment once as-is, without git or records")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="commit, run, then keep or revert one experiment")
    run.add_argument("-m", "--message", required=True, help="what this experiment tries")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="summarize the runs on the current branch")
    status.add_argument("-n", type=int, default=15, help="how many recent runs to list")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"autoresearch: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

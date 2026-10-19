---
name: new
description: Create a new autoresearch project from a research idea, or turn an existing repo into one. Designs a frozen harness that measures one metric under a fixed time budget, a simple editable baseline, and the autoresearch.toml contract, then validates it with `autoresearch check`. Use when the user wants to set up autoresearch, an autonomous experiment loop, or an agent that optimizes something overnight.
argument-hint: "[research idea]"
---

# Create an autoresearch project

An autoresearch project has three parts:

- a **frozen harness** that runs one experiment within a fixed time budget and prints one metric,
- an **editable candidate** that the research agent improves, run after run,
- **`autoresearch.toml`**, which tells the bundled `autoresearch` command how to run, measure,
  and judge.

The `autoresearch` command that ships with this plugin owns commits, timeouts, metric
parsing, keep/revert, and the log. Your job here is the
harness. An agent will run a hundred experiments against it with nobody watching; any gap it
leaves, the loop will eventually find.

Idea: $ARGUMENTS

## 1. Preflight

- `autoresearch --help` must work. The plugin provides it and runs it on the system Python
  3.9+; if it fails, ask the user to install Python 3 and stop.
- Detect the hardware: `nvidia-smi -L` (CUDA), `uname -sm` (Darwin arm64 means Apple
  Silicon, so PyTorch `mps`), otherwise CPU. Note the memory too.
- Check that the toolchain the project needs is installed (`uv --version`,
  `cargo --version`, `node --version`, and so on).

## 2. Interview

Ask with AskUserQuestion, skipping whatever the idea already answers:

1. The research question, and what "better" means: the metric, its direction, and how it is
   computed.
2. What the agent may change: one file (like karpathy's `train.py`) or a directory of
   candidates (`solutions/*.py`), and in which language. An existing repo keeps its own
   toolchain; a new machine-learning project defaults to Python and PyTorch, managed with uv
   when it is installed.
3. The budget per experiment (2 to 10 minutes; 5 minutes is about 10 runs an hour) and where
   it runs.
4. The data: where it comes from, its size, and how evaluation data stays hidden from the
   candidate.
5. How the metric could be gamed, and which rules must go in the research brief.
6. The target directory: a new folder, or the current repo (adopt mode, below).

## 3. Design and write the harness

Read [harness-guide.md](harness-guide.md) and follow it. Then write:

- the frozen harness (for example `harness/evaluate.py`, or a Rust `src/bin/bench.rs`), plus
  any data preparation,
- a simple candidate that works, not a clever one; the loop does the clever part,
- the toolchain's dependency manifest and lockfile, with versions you looked up (`uv add`,
  `cargo add`, `npm install --save-exact`), installed once,
- `.gitignore` entries for the toolchain's build output and caches (`target/`,
  `node_modules/`, `build/`, `dist/`). `init` adds Python's; any other file a run leaves in
  the repo makes it a crash.

Then initialize, choosing `--timeout` at about twice the budget plus startup and build time:

```bash
autoresearch init --metric <name> --goal minimize|maximize \
  --run "uv run --locked python -m harness.evaluate" --editable "solution.py" --timeout 480
# or, for a Rust benchmark:
autoresearch init --metric ns_per_element --goal minimize \
  --run "cargo run --release --locked --bin bench" --editable "src/sort.rs" --timeout 300
```

Fill in the **Research brief** section of the generated `program.md`: the question, how the
harness measures, which files to read first, the rules that code can't enforce, and the
hardware. Leave the rest of `program.md` as generated.

### Adopt mode (existing repo)

Same steps, but write only what is missing. For a clone of karpathy/autoresearch:
`git mv program.md program.orig.md`, then
`autoresearch init --metric val_bpb --goal minimize --run "uv run train.py" --editable train.py --timeout 600`,
merge the goal and rules of `program.orig.md` into the brief, and delete it. Upstream needs an
NVIDIA GPU; on a Mac, start from an MLX or MPS fork instead.

## 4. Validate

1. `autoresearch check`. The first run also installs, builds, and warms caches. It must print
   `check ok` and leave `git status` unchanged. If it reports that the run wrote files into
   the repo and they are build output or caches, add them to `.gitignore` and check again.
2. Run `autoresearch check` twice more and compare the metric. Set `min_improvement` in
   `autoresearch.toml` to about twice the relative spread, (max - min) / mean, or leave it at
   0 when the runs are identical. It is a fraction of the best, so the bar shrinks as the
   metric improves.
3. Floor: temporarily swap in a trivial candidate (a constant, a no-op). `check` must score
   clearly worse. Restore the real one.
4. Leak: temporarily make the candidate read the evaluation data or call the oracle after
   training. The harness must turn that into a crash. If it can't be enforced in code, state
   the rule in the brief instead. Restore the real candidate.
5. `git status` should list only files you meant to create.

## 5. Hand off

Ask the user before the first commit, then
`git add -A && git commit -m "Scaffold autoresearch project"`. Tell them how to start, and
that the first `claude` launch in the new folder asks them to trust it; until they accept,
Claude Code ignores the project's `.claude/settings.json`:

- Start the loop: `cd <dir> && caffeinate -is claude --permission-mode dontAsk "/autoresearcher:run"`.
  It runs in a normal session. `caffeinate` keeps a Mac awake (leave it out on Linux), and
  `dontAsk` turns permission prompts into refusals, so nothing waits for them. When they're
  watching, typing `/autoresearcher:run` in a session in `<dir>` works too.
- Optional, for a long night: restart sessions that end on an API error:
  `caffeinate -is sh -c 'until [ -f .autoresearch/stop ]; do claude -p "/autoresearcher:run" --permission-mode dontAsk; sleep 60; done'`
- Progress: open `.autoresearch/report.html`; it refreshes after every run.

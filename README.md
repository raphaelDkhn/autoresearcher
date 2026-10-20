# infinit research

A Claude Code plugin that lets an agent improve your code while you sleep. A frozen harness
measures one number. The plugin commits each change the agent makes, keeps it if the number
improved, and reverts it if not. Inspired by
[karpathy/autoresearch](https://github.com/karpathy/autoresearch) and
[fractalsearch](https://github.com/MaxRobinsonTheGreat/fractalsearch).

## Install

```bash
claude plugin install infinit-research --marketplace raphaelDkhn/infinit-research
```

Needs git and Python 3.9+.

## Use

Describe what to improve. The plugin writes a frozen harness, a simple baseline, and the
contract below, then checks them:

```text
/infinit-research:new make the sort in src/sort.rs as fast as possible
```

Start the loop in the new project. It runs until you stop it:

```bash
caffeinate -is claude --permission-mode dontAsk --effort max --model opus "/infinit-research:run"
open .infinit-research/report.html   # watch progress
touch .infinit-research/stop         # stop after the current run
```

`caffeinate` keeps the Mac awake, and `dontAsk` refuses permission prompts instead of waiting
for you. An API error ends the session, so for a long night, restart it in a loop:

```bash
caffeinate -is sh -c 'until [ -f .infinit-research/stop ]; do claude -p "/infinit-research:run" --permission-mode dontAsk --effort max --model opus; sleep 60; done'
```

## The contract

`infinit-research.toml` says how to run and judge one experiment. The plugin enforces it on every
run, so the agent never grades its own work.

```toml
[metric]
name = "ns_per_element"     # the run prints "ns_per_element: <number>" once
goal = "minimize"           # or "maximize"
min_improvement = 0.02      # keep a change only if it beats the best by 2%

[run]
command = "cargo run --release --locked --bin bench"   # any language
timeout = 300               # seconds; longer runs are killed
editable = ["src/sort.rs"]  # the only files the agent may change
```

A run may save images and logs to `$INFINIT_RESEARCH_RUN_DIR`. Anything else it leaves in the repo
makes it a crash, so gitignore build output such as `target/`.

## Recursive mode

In the default auto mode, the agent improves your code. In recursive mode it also improves how
it researches, following [Harness²](https://arxiv.org/abs/2610.14224). Ask for it when you
create the project:

```text
/infinit-research:new make the sort in src/sort.rs as fast as possible, in recursive mode
```

The agent keeps its method in `lab/`: a short strategy, skills, its own quick evaluations, and
lessons. Every 10 runs it reviews that method against the numbers and keeps only the changes
the runs support. The harness still judges every run and never changes, and `lab/` is hidden
while it runs. This mode needs evaluation data drawn fresh each run, like the random inputs of
a benchmark.

## Notes

- Open a new project in Claude Code once and accept the trust prompt. Until then, Claude Code
  ignores the project's permissions.
- Steer a running loop in the "Steering (human)" section of `notes.md`.
- Reverted attempts stay restorable under `refs/infinit-research/`.
- Existing repos work too: run `/infinit-research:new` inside one and ask it to adopt the repo.
- On a remote machine: install Claude Code and the plugin, set `CLAUDE_CODE_OAUTH_TOKEN` (from
  `claude setup-token` on your laptop), clone the project and open it once with `claude`, then
  run the long-night loop inside `tmux` without `caffeinate`. Push results with
  `git push origin <branch> 'refs/infinit-research/*:refs/infinit-research/*'`.
- The plugin is not a sandbox: the agent's code runs as you.

## Develop

```bash
uv sync && uv run pytest && prek install   # uv is only needed to develop the plugin
claude --plugin-dir .                      # try the plugin from this checkout
```

# autoresearcher

A Claude Code plugin that lets an AI agent run experiments on your code while you sleep. A
frozen harness measures one metric; the plugin commits each change, keeps it if the metric
improved, and reverts it if not. Inspired by
[karpathy/autoresearch](https://github.com/karpathy/autoresearch) and
[fractalsearch](https://github.com/MaxRobinsonTheGreat/fractalsearch).

## Install

```bash
claude plugin install autoresearcher --marketplace raphaelDkhn/autoresearcher
```

Needs git and Python 3.9+.

## Use

In Claude Code, describe the research idea. The plugin builds the harness and a baseline, then
checks them:

```text
/autoresearcher:new approximate the Mandelbrot set with a small neural network
```

Then, in the new project, start the loop. It runs in a normal Claude Code session until you
stop it:

```bash
caffeinate -is claude --permission-mode dontAsk "/autoresearcher:run"   # run the loop
open .autoresearch/report.html                                          # watch progress
touch .autoresearch/stop                                                # stop after the current run
```

`caffeinate` keeps the Mac awake and `dontAsk` turns permission prompts into refusals, so nothing
waits for you. When you're watching, typing `/autoresearcher:run` in a session works too.

Optional, for a long night: an API error ends a session, so restart it automatically:

```bash
caffeinate -is sh -c 'until [ -f .autoresearch/stop ]; do claude -p "/autoresearcher:run" --permission-mode dontAsk; sleep 60; done'
```

## The contract

`/autoresearcher:new` writes `autoresearch.toml`, and every run enforces it, so the agent never
grades its own work: the plugin reads the metric, keeps or reverts the change, kills runs that
overrun, and refuses to run if a file outside `editable` changed.

```toml
# autoresearch.toml
[metric]
name = "mse"                # the run prints "mse: <number>" exactly once
goal = "minimize"           # or "maximize"
min_improvement = 0.02      # keep a run only if it beats the best by 2%

[run]
command = "uv run --locked python -m harness.evaluate"   # any language: cargo, node, make...
timeout = 480               # seconds
editable = ["solution.py"]  # the only files the agent may change
```

The command runs one experiment and exits 0. It may write images and logs to
`$AUTORESEARCH_RUN_DIR`. Anything else it leaves in the repo makes the run a crash, so
gitignore build output (`target/`, `node_modules/`).

## Recursive mode

Ask `/autoresearcher:new` for recursive mode and the agent also improves how it researches,
following [Harness²](https://arxiv.org/abs/2610.14224). Its method lives in `lab/`: a short
strategy it reads every iteration, skills it reads when their trigger fires, and a library of
lessons. Every 10 runs it reviews that method against numbers the plugin computes. It judges
its last changes run by run, keeps at most one lesson, has two subagents propose changes (how
it researches, and what the problem is), and keeps only what the runs support.

The harness that judges runs stays frozen and is the only thing that can keep a change.
`lab/` is moved aside while it runs, every version of `lab/` is saved in
`.autoresearch/lab-versions/`, and the report shows how each window of 10 runs went.

## Notes

- Open a new project in Claude Code once and accept the trust prompt; until then Claude Code
  ignores its permission rules.
- Steer a running loop in the "Steering (human)" section of `notes.md`.
- Reverted attempts stay restorable under `refs/autoresearch/`.
- Existing repos work too: run `/autoresearcher:new` inside one (karpathy/autoresearch, say)
  and ask it to adopt the repo.
- Remote GPU: install Claude Code and the plugin; set `CLAUDE_CODE_OAUTH_TOKEN` (from
  `claude setup-token` on your laptop); clone the project and open it once with `claude`;
  run the long-night loop, minus `caffeinate`, inside `tmux`. Push results with
  `git push origin <branch> 'refs/autoresearch/*:refs/autoresearch/*'`.
- The plugin checks what changed in the repo, and a run that edits its records is undone and
  stops the loop. It is not a sandbox: the agent's code runs as you.

## Develop

```bash
uv sync && uv run pytest && prek install   # uv is only needed to develop the plugin
claude --plugin-dir .   # try the plugin from this checkout
```

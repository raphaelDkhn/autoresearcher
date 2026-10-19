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
- The plugin checks what changed in the repo. It is not a sandbox: the agent's code runs as
  you.

## Develop

```bash
uv sync && uv run pytest && prek install   # uv is only needed to develop the plugin
claude --plugin-dir .   # try the plugin from this checkout
```

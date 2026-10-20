---
name: run
description: Start or resume the autoresearch experiment loop in the current project and keep it running unattended until the user stops it.
argument-hint: "[run tag]"
disable-model-invocation: true
disallowed-tools:
  - AskUserQuestion
allowed-tools:
  - Bash(autoresearch run *)
  - Bash(autoresearch status)
  - Bash(autoresearch check)
  - Bash(autoresearch status *)
  - Bash(autoresearch review)
  - Bash(autoresearch probe *)
  - Bash(git status)
  - Bash(git status *)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(git show *)
  - Bash(git restore *)
  - Bash(git switch -c autoresearch/*)
  - Bash(git branch --list *)
  - Bash(rm -f .autoresearch/stop)
hooks:
  Stop:
    - hooks:
        - type: command
          command: >-
            test -f "$CLAUDE_PROJECT_DIR/.autoresearch/stop" ||
            test ! -f "$CLAUDE_PROJECT_DIR/autoresearch.toml" ||
            echo '{"decision": "block", "reason": "The autoresearch loop is running: re-read program.md and notes.md, then start the next experiment. If the user asked to stop, write .autoresearch/stop and end your turn."}'
---

# Run the autoresearch loop

You are about to run experiments for hours with nobody watching. The `autoresearch` command
(bundled with this plugin) handles git, timeouts, measurement, and keep/revert. You bring the
ideas.

## 1. Preflight

To stop on a failed check, write the reason to `.autoresearch/stop` (the Stop hook lets you end
only once that file exists), tell the user, and end your turn.

1. `autoresearch status` works from the project root.
2. If `.autoresearch/runs.jsonl` doesn't exist yet (a fresh clone), run `autoresearch check`
   once, so installing dependencies and a cold build don't eat the first run's timeout.
3. If `autoresearch status` shows STOP REQUESTED, tell the user the reason, then
   `rm -f .autoresearch/stop`.
4. `git status`: uncommitted edits to the editable files are an unfinished idea from the last
   session; finish it as your first experiment or `git restore` it. Changes to any other
   file are not yours to clean up: stop.

## 2. Branch

- Already on an `autoresearch/*` branch: resume it.
- Otherwise run `git switch -c autoresearch/<tag>`. The tag is `$ARGUMENTS`, or today's date
  such as `oct19`, with `-2`, `-3` appended if `git branch --list 'autoresearch/*'` shows it
  is taken.

## 3. Tell the user once, then start

- To stop: press Esc and say "stop" (you then write `.autoresearch/stop`), or run
  `touch .autoresearch/stop` in another terminal. The current experiment finishes first.
- For an unattended night, an API error ends a session, so restart it in a shell loop:
  `caffeinate -is sh -c 'until [ -f .autoresearch/stop ]; do claude -p "/autoresearcher:run" --permission-mode dontAsk; sleep 60; done'`
- Progress: `.autoresearch/report.html` in a browser; it refreshes after every run.

## 4. Loop

Follow program.md. Run the baseline first if `autoresearch status` says there is none. Then
repeat:

1. Re-read program.md and notes.md, and `lab/strategy.md` in recursive mode; context
   compaction drops them.
2. Run `autoresearch status`.
3. Pick one idea and edit only the editable files.
4. Run `autoresearch run -m "<what you tried>"` in the foreground. The project settings raise
   the Bash timeout above the manifest's `timeout`.
5. Read the verdict, open the artifacts it lists, and update notes.md. When the verdict or
   `autoresearch status` says a review is due, run `autoresearch review` and follow it before
   the next idea.

Never ask whether to continue, and never stop on your own: when you run out of ideas, use the
research habits in program.md (`lab/strategy.md` in recursive mode). When `autoresearch run` prints STOP REQUESTED, or the user asks
you to stop, write `.autoresearch/stop` if it is missing and end your turn.

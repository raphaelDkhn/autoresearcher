---
name: run
description: Start or resume the infinit research experiment loop in the current project and keep it running unattended until the user stops it.
argument-hint: "[run tag]"
disable-model-invocation: true
disallowed-tools:
  - AskUserQuestion
allowed-tools:
  - Bash(infinit-research run *)
  - Bash(infinit-research status)
  - Bash(infinit-research check)
  - Bash(infinit-research status *)
  - Bash(infinit-research review)
  - Bash(infinit-research probe *)
  - Bash(git status)
  - Bash(git status *)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(git show *)
  - Bash(git restore *)
  - Bash(git switch -c infinit-research/*)
  - Bash(git branch --list *)
  - Bash(rm -f .infinit-research/stop)
hooks:
  Stop:
    - hooks:
        - type: command
          command: >-
            test -f "$CLAUDE_PROJECT_DIR/.infinit-research/stop" ||
            test ! -f "$CLAUDE_PROJECT_DIR/infinit-research.toml" ||
            echo '{"decision": "block", "reason": "The infinit research loop is running: re-read program.md and notes.md, then start the next experiment. If the user asked to stop, write .infinit-research/stop and end your turn."}'
---

# Run the infinit research loop

You are about to run experiments for hours with nobody watching. The `infinit-research` command
(bundled with this plugin) handles git, timeouts, measurement, and keep/revert. You bring the
ideas.

## 1. Preflight

To stop on a failed check, write the reason to `.infinit-research/stop` (the Stop hook lets you end
only once that file exists), tell the user, and end your turn.

1. `infinit-research status` works from the project root.
2. If `.infinit-research/runs.jsonl` doesn't exist yet (a fresh clone), run `infinit-research check`
   once, so installing dependencies and a cold build don't eat the first run's timeout.
3. If `infinit-research status` shows STOP REQUESTED, tell the user the reason, then
   `rm -f .infinit-research/stop`.
4. `git status`: uncommitted edits to the editable files are an unfinished idea from the last
   session; finish it as your first experiment or `git restore` it. Changes to any other
   file are not yours to clean up: stop.

## 2. Branch

- Already on an `infinit-research/*` branch: resume it.
- Otherwise run `git switch -c infinit-research/<tag>`. The tag is `$ARGUMENTS`, or today's date
  such as `oct19`, with `-2`, `-3` appended if `git branch --list 'infinit-research/*'` shows it
  is taken.

## 3. Tell the user once, then start

- To stop: press Esc and say "stop" (you then write `.infinit-research/stop`), or run
  `touch .infinit-research/stop` in another terminal. The current experiment finishes first.
- For an unattended night, an API error ends a session, so restart it in a shell loop:
  `caffeinate -is sh -c 'until [ -f .infinit-research/stop ]; do claude -p "/infinit-research:run" --permission-mode dontAsk; sleep 60; done'`
- Progress: `.infinit-research/report.html` in a browser; it refreshes after every run.

## 4. Loop

Follow program.md. Run the baseline first if `infinit-research status` says there is none. Then
repeat:

1. Re-read program.md, `lab/strategy.md`, and notes.md; context compaction drops them.
2. Run `infinit-research status`.
3. Pick one idea and edit only the editable files.
4. Run `infinit-research run -m "<what you tried>"` in the foreground. The project settings raise
   the Bash timeout above the manifest's `timeout`.
5. Read the verdict, open the artifacts it lists, and update notes.md. When the verdict or
   `infinit-research status` says a review is due, run `infinit-research review` and follow
   it before the next idea.

Never ask whether to continue, and never stop on your own: when you run out of ideas, follow
`lab/strategy.md` and its skills. When `infinit-research run` prints STOP REQUESTED, or the user
asks you to stop, write `.infinit-research/stop` if it is missing and end your turn.

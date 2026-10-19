# Autoresearch protocol

You are an autonomous researcher working in this repository. Your objective: make `{{metric}}`
as {{goal_word}} as possible. The harness measures it; you improve the code it measures. The
human who set this up may be asleep, so work independently and never stop to ask whether to
continue.

## Research brief

TODO: the research question, what the harness measures and how, the in-scope files to read,
and the rules specific to this project (what counts as cheating here). Humans edit this
section between runs and commit the change.

## Rules

- Change only files matching {{editable}}. Everything else is frozen: `autoresearch run`
  refuses to start if anything else changed, committed or not.
- Do not add dependencies or download data.
- Do not game the measurement: no reading or reproducing the evaluation data, no
  special-casing the metric, no shortcut the research brief forbids. Treat a suspiciously
  large gain as a bug until you can explain it.
- Never commit, reset, or switch branches yourself. `autoresearch run` commits each
  experiment, keeps it if it wins, and reverts it otherwise.
- Experiments write outputs only to `$AUTORESEARCH_RUN_DIR`.

## Setup (once per run tag)

1. Work on a fresh branch `autoresearch/<tag>` (`/autoresearcher:run` creates it).
2. Read this file, `autoresearch.toml`, `notes.md`, and the in-scope files from the brief.
3. Establish the baseline: `autoresearch run -m "baseline"`.

## The loop (repeat until told to stop)

1. Re-read this file and `notes.md` (the human may have left steering notes), then run
   `autoresearch status`.
2. Pick ONE idea that tests a clear hypothesis. Edit only the editable files.
3. Run `autoresearch run -m "<what you tried, in a few words>"`. It runs the experiment
   (up to {{timeout}} s, so give your shell a timeout above that) and prints one verdict:
   - `keep`: it beat the best by more than `min_improvement` (a fraction of the best, set in
     `autoresearch.toml`); the branch now includes it.
   - `discard`: not better, so it was reverted. Restore it with the printed `git restore`
     command if you want to build on it.
   - `crash` or `timeout`: reverted; the log tail is printed. For a trivial bug, restore,
     fix, and run again. Otherwise move on.
   - `STOP REQUESTED`: end your turn immediately.
4. Look at the run's artifacts in `.autoresearch/runs/<id>/` (images, plots) when it has any.
5. Update `notes.md` in place and keep it under about 150 lines: the current best and why it
   works, what failed and why, and a ranked list of next ideas. Future agents start from it.

## Research habits

- Every ~10 runs, try an idea from a different family than the current best.
- Never declare convergence. When stuck, re-read the harness for structure to exploit,
  profile where the time goes, combine near-misses, or try a radically different approach.
- When two ideas gain about the same, prefer the simpler one.

# New evaluation

Use this before spending a run on an idea you are unsure of, or when runs are too slow or too
noisy to tell ideas apart.

An evaluation is your own measurement. `infinit-research probe -m "<what it checks>" [args]` runs
`lab/probe.sh` with those args, under the same time limit and checks as a run, and records it
in `.infinit-research/probes.jsonl`. A probe never commits or keeps anything; only
`infinit-research run` can.

1. Decide what it predicts: whether the harness will keep a candidate, sooner or with less
   noise than a run. A smaller budget, a smaller problem, more seeds, or a breakdown of the
   error are the usual levers.
2. Write `lab/probe.sh` and anything it calls under `lab/`. It evaluates the candidate in the
   working tree, prints results as `name: value` lines, and writes outputs to
   `$INFINIT_RESEARCH_RUN_DIR`. It never reads the harness's evaluation data.
3. Check it before you trust it. With no uncommitted edits, restore a candidate the harness
   already judged (`git restore --source=<commit> -- <files>`; commits are in
   `.infinit-research/runs.jsonl`), probe it, and `git restore -- <files>` back. Do this for two or
   three candidates. Keep the evaluation if it ranks them as the harness did; fix or drop it
   otherwise.
4. Note in notes.md what a probe costs and how often it agreed with the harness.

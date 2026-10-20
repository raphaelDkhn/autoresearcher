# Designing a harness that holds up overnight

## The contract with the controller

The harness can be in any language. The controller only sees a shell command, its output, and git.

- The run command (`[run] command`) is any shell command, for example
  `uv run --locked python -m harness.evaluate`, `cargo run --release --locked --bin bench`,
  `node harness/bench.mjs`, or `make eval`. It runs one experiment and exits 0 when it
  succeeds. Any failure, including going over budget or producing a wrong result, exits
  non-zero.
- It prints `metric_name: number` exactly once (spaces after the colon are fine). Other
  `key: number` lines are recorded as secondary metrics.
- `$INFINIT_RESEARCH_RUN_DIR` is a fresh directory per run. Write images, plots, and checkpoints
  there.
- Anything else the run leaves in the repo that git can see counts as a stray write: the controller
  moves it aside and marks the run crashed. So the toolchain's build output and caches
  (`target/`, `node_modules/`, `build/`, `dist/`, `zig-out/`, `.venv/`) must be gitignored.
- stdin is closed. Nothing can prompt.
- The controller kills the whole process group at `[run] timeout`.

## Rules

1. **The harness computes the metric, not the candidate.** Call the candidate (import it, link
   it, or run it as a subprocess) and score its output yourself. If the candidate prints its
   own metric line, the metric appears twice and the run counts as a crash.
2. **Two time limits.** The soft limit belongs to the harness: a training clock (say 120 to
   300 s) or a fixed amount of benchmark work. The manifest `timeout` is the hard limit the
   controller enforces. Set it to about twice the soft limit plus startup, and remember it also
   covers compiling. Exit non-zero if the candidate overruns the soft limit.
3. **Short iterations.** 2 to 10 minutes per run. Many short runs beat a few long ones.
4. **Hide the evaluation data.** The candidate must never see the inputs it is scored on.
   Draw them fresh each run (random seeds, offsets, or jitter), or load a private split only
   after training ends. If the harness offers an oracle (the target for any input), close it
   before predicting. For speed work, check every output against a frozen reference
   implementation, because the fastest wrong answer is the agent's easiest win.
5. **Measure the noise.** Fix seeds where results should repeat. For timings, warm up, repeat,
   and report the median. Then run `infinit-research check` three times and set
   `min_improvement` to about twice the relative spread.
6. **No network during runs.** Download and prepare data once, into a gitignored cache.
7. **Lock dependencies.** Commit the lockfile and run in locked mode, so a run can't change it:
   `uv run --locked`, `cargo run --locked`, or `npm ci` once and then plain `node`.
8. **Gitignore build output before the first check,** and warm the build with one
   `infinit-research check` before a loop, so a cold compile doesn't eat a run's timeout.
9. **Know the hardware.** For GPU work, pick cuda, then mps, then cpu; MPS has no float64.
   For timings, other load on the machine is noise; say so in the brief.
10. **Save a picture.** A prediction image, an error map, or sample output in the run
    directory lets the agent see what the number hides.
11. **Write down what code can't enforce.** Rules such as "stay a general-purpose learner" or
    "no hard-coded answers" go in the research brief, where the agent reads them every
    iteration.
12. **Recursive mode: fresh evaluation data every run.** The agent writes its own
    evaluations, and its code can read any file in the project, so it could learn a fixed
    evaluation set through them. Draw evaluation inputs from a seed taken from `os.urandom`
    or the clock, never from a file, and print the seed once as `eval_seed: <n>`.

## Sketch: learn a function (Python, fit/predict)

Adapt this; don't copy it blindly. The candidate (`solution.py`, editable) defines
`fit(ctx) -> predict`, and the harness owns the clock, the data, and the score.

```python
# harness/evaluate.py (frozen)
import importlib.util
import os
import sys
import time
from pathlib import Path

BUDGET = 120.0  # seconds of training per run
RUN_DIR = Path(os.environ.get("INFINIT_RESEARCH_RUN_DIR", "."))


class Context:
    """What the candidate gets during fit: a clock and training data, nothing else."""

    def __init__(self, budget: float) -> None:
        self.deadline = time.monotonic() + budget
        self.open = True

    def time_left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def sample(self, n: int):
        if not self.open:
            raise RuntimeError("training data is closed once fit returns")
        return draw_training_batch(n)  # inputs and targets, never the eval set


def main() -> int:
    spec = importlib.util.spec_from_file_location("solution", "solution.py")
    solution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solution)
    ctx = Context(BUDGET)
    start = time.monotonic()
    predict = solution.fit(ctx)
    ctx.open = False
    if time.monotonic() - start > BUDGET + 5:
        print("fit overran its budget", file=sys.stderr)
        return 1
    inputs, targets = fresh_eval_set()  # drawn now, after training
    predictions = predict(inputs)
    save_pictures(RUN_DIR, predictions, targets)
    print(f"mse: {mean_squared_error(predictions, targets):.8f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Sketch: make code faster (Rust, benchmark)

The candidate is a module (`src/sort.rs`, editable) that a frozen binary links against;
`src/lib.rs` (frozen) declares `pub mod sort;`. Run it with
`cargo run --release --locked --bin bench`, and gitignore `target/`.

```rust
// src/bin/bench.rs (frozen)
use std::time::{Instant, SystemTime, UNIX_EPOCH};

fn main() {
    // Fresh inputs every run, so the candidate can't be tuned to one dataset.
    let mut seed = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as u64 | 1;
    let mut next = move || { seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17; seed };
    let mut per_element = Vec::new();
    for _ in 0..30 {
        let input: Vec<u64> = (0..1_000_000).map(|_| next()).collect();
        let mut data = input.clone();
        let start = Instant::now();
        mycrate::sort::sort(&mut data);
        per_element.push(start.elapsed().as_nanos() as f64 / data.len() as f64);
        let mut expected = input;
        expected.sort_unstable(); // the frozen reference
        if data != expected {
            eprintln!("wrong result");
            std::process::exit(1);
        }
    }
    per_element.sort_by(f64::total_cmp);
    println!("ns_per_element: {:.4}", per_element[per_element.len() / 2]); // median, once
}
```

The same shape works in any language: generate fresh inputs, time the candidate, compare
with a frozen reference, print the median once, and exit non-zero on a mismatch.

## When the candidate is a whole training script

For a karpathy-style `train.py`, keep the evaluation function in a frozen module and have the
training script call it last. That is weaker, since the script controls the call, so say in
the brief that the evaluation call and its printed line must not change.

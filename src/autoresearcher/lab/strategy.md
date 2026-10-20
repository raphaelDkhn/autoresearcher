# Research strategy

How you choose, design, and read experiments. You own this file and change it in reviews,
when the runs show your method is the bottleneck. It is read every iteration, so keep it
short: procedures with a clear trigger go in `lab/skills/`, named here.

## Choosing the next experiment

- Keep a ranked backlog in notes.md: expected gain times the chance it works, divided by its
  cost in runs. Take the top idea unless a skill below says otherwise.
- Write the expected effect in the run message: `-m "wider hidden layer (expect -3%)"`. A
  surprise means your model of the problem is wrong; write down why in notes.md.
- Don't retry a dead end from notes.md unless something it depended on has changed.

## Designing it

- Change one thing per run, so the verdict says what mattered.
- Make the candidate print diagnostics as `name: value` lines and save pictures to
  `$AUTORESEARCH_RUN_DIR`, so a discard still teaches you something.
- Bracket a numeric knob with values 2 to 4 times apart instead of nudging it.
- After a large win, ablate it: remove its parts one at a time to find what carried it.

## Reading the result

- A gain inside the noise floor is not a finding. When two variants tie, keep the simpler.
- Treat a suspiciously large gain as a bug until you can explain it.
- Record what each run established in notes.md, with its run id.

## Skills

- After 5 discards in a row, or when the backlog is empty, read `lab/skills/when-stuck.md`.

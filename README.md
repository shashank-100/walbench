# WALBench

Crash-consistency repair tasks for coding agents, with an exact, adversarial verifier.

A small write-ahead-logged key-value store (put / delete / non-idempotent increment, interleaved transactions, aborts,
checkpoints written as temp-file + rename + WAL truncation) and a deterministic crash simulator that can stop the world
at any write: torn records, flipped bits, half-written checkpoint files, a renamed checkpoint with the WAL not yet
truncated. An **independent model** of what was durably committed gives the expected state for every crash, so grading
does not trust any recovery code (including ours).

Each task ships a broken `recovery.py` and a user-style bug report. The agent fixes it; a **hidden grader replays 300
seeded crashes** and the fix passes only if every recovered state is exactly right.

## Tasks (25, all verified: the broken file fails the hidden grader, the reference passes 300/300)
- **single (14):** one injected bug each: applying uncommitted or aborted transactions, trusting a half-written
  checkpoint, skipping checksums, failing on a torn tail, double-replaying records already in the checkpoint (visible
  only through non-idempotent increments), reordering ops inside a transaction, losing deletes, treating "" as delete,
  stopping at the first abort, flushing other open transactions on commit, an off-by-one in record parsing, increments
  overwriting, skipping the first record after a checkpoint. Several pass 288-295 of 300 crashes: nearly invisible
  without real testing.
- **hard (10):** three bugs at once with a vague report ("data is sometimes wrong after crashes").
- **from_scratch (1):** only the format spec; write recovery from nothing.

Self-tests: the crash model agrees with itself on every recorded disk state (0 mismatches over 300 workloads) and the
reference recovery passes 3,000/3,000 crashes across seeds.

## Results (Claude Opus 5.5, medium effort, headless Claude Code)
| Tier | B: read & reason only | C: + `./check` (40 visible crashes) |
|---|---|---|
| single (14) | **14/14** | 12/12 (interrupted) |
| hard (11) | **10/11** | **11/11** |

Frontier agents solve almost everything by reading a 60-line file carefully, so WALBench's main use is as a
**verified RL environment**: the 300-crash grader is an exact, hard-to-game reward for training smaller models on
systems reasoning. (The visible `./check` is deliberately weaker than the hidden grader, like a real CI suite: it passes
several of the subtle bugs.)

## Run
```bash
uv sync
uv run python -m walbench.tasks                                   # regenerate + verify all tasks
uv run python -m walbench.grader tasks/double_replay/recovery.py  # grade any recovery.py (300 crashes)
uv run python -m walbench.agent --arm read_only --tier hard       # agent runs (Claude Code login)
```

# Nested Loops in fb2d: Principles and Patterns

Claude Opus 4.6, 2026-02-15 — distilled from building factorial-03.fb2d
and studying square-08-final.fb2d, triple-loop-05.fb2d, and ifbc.py v0.1.

## The Core Loop Pattern: `( P ... %`

Every while-loop in fb2d uses the same three-opcode skeleton:

```
  BODY_ROW:  /  [body_ops_reversed]  \        ← body going W
  CODE_ROW:  (  P  [cl_moves]  [pad]  %  ]    ← control going E
```

- `(` = `\` reflect if `grid[EX] != 0`. Distinguishes first entry (EX=0, pass
  through E) from re-entry (EX>0, reflect S->E).
- `P` = `grid[EX]++`. Leaves a breadcrumb for reversibility.
- `%` = `/` reflect if `grid[CL] != 0`. The loop condition. Reflects E->N into
  the body when true; passes through E to exit when false.
- `]` = EX East. On the exit path only. Advances EX to a fresh column for the
  next loop construct.
- `/` and `\` on the body row are unconditional mirrors that form the loop-back
  rectangle.

Flow:
1. **First entry** (IP going E): `(` passes (EX=0) -> `P` (EX=1) -> `%` reflects
   E->N (CL!=0) -> body going W -> `\` turns N -> `\` at top-right -> `/` at
   top-left turns S -> back to `(`.
2. **Re-entry** (IP going S): `(` reflects S->E (EX>0) -> `P` (EX++) -> `%`
   reflects E->N -> body -> ... repeat.
3. **Exit** (CL=0): `%` passes E -> `]` advances EX -> post-loop code.

## The Nested Loop Problem

If an inner loop also uses `( P %`, it needs `grid[EX]=0` on first entry. But
on the *second* outer iteration, the inner loop's EX cell still has breadcrumbs
from the first iteration. The inner `(` sees EX!=0 and reflects — treating it
as a re-entry when it should be a first entry.

This is the fundamental challenge. CL-conditional loops (using `%` or `&`
without EX) don't have this problem, but they can't distinguish first entry
from re-entry at all — the mirror geometry is unsolvable without `(`.

## The Solution: Monotonic EX Consumption

**EX advances forward and never retracts.** Every `(` except the very first is
preceded by `]`, which advances EX to a guaranteed-fresh cell with `grid[EX]=0`.

### Rule 1: `]` before every inner `(`

When nesting loops, emit `]` immediately before each inner `(` in the body.
This is on the body path, so it executes every outer iteration, advancing EX
to a new column each time. The inner `(` always sees a fresh cell.

### Rule 2: `]` on every exit path

After `%` passes through (loop exits), emit `]` to advance past the current
loop's EX cell. This separates sequential loops from each other.

### Rule 3: The outer `(` freeloads

The outer `(` does NOT need its own dedicated `P` that it manages across
iterations. When the body contains inner loops, those inner `P` operations
leave EX cells non-zero. When the IP returns to the outer `(`, EX points to
whatever cell was last touched by inner operations — always non-zero.

If the body does NOT contain inner loops (the base case), then the outer loop's
own `P` handles things — this is the standard ifbc v0.1 pattern.

### Rule 4: EX cost is O(outer_iters * inner_loops)

Each outer iteration consumes one EX column per inner loop (each `]` advances
one column). For factorial with N=5 (two inner loops — multiply and cleanup),
that's ~10 EX cells. A 32-wide grid gives ~30 usable columns, enough for
moderate inputs.

## Reversibility: "Burning Zeroes"

The EX trail is a reversibility resource. `P` increments cells; the reverse
operation `Q` decrements them. `]` advances EX; `[` retracts it. When running
backward, the simulator reconstructs the exact forward path.

The key insight: **you must burn zeroes in the garbage bytes.** Fresh EX cells
(value 0) are consumed by `P` to record that a loop iteration occurred. Without
fresh zeroes, `(` can't distinguish first entry from re-entry, and reversibility
breaks. The monotonic advance ensures there are always fresh zeroes ahead.

This was verified: factorial-03.fb2d with N=3 runs 165 steps forward, producing
`(0,1)=6`. Running 165 steps backward restores ALL state — grid, IP, CL, H0,
H1, EX — and the EX trail is completely clean (all zeroes).

## Row Allocation for Nesting

Each nesting level uses two rows:

```
  Row 0:  DATA (variables, one per column)
  Row 1:  Level-0 BODY (going W)
  Row 2:  Level-0 CODE (going E) — outermost ( P % here
  Row 3:  EX trail (zeroes, EX starts here)
  Row 4:  Level-1 BODY (going W)
  Row 5:  Level-1 CODE (going E) — inner ( P % here
  Row 6:  Level-2 BODY ...
  Row 7:  Level-2 CODE ...
```

Transitions between levels use mirror pairs to change IP direction.

## Canonical Head Reset

All heads (H0, H1, CL) must be at known positions when the IP reaches any `(`
or `%`. The ifbc v0.1 strategy of "canonical reset to (DATA_ROW, 0) at end of
every body" extends naturally to nested loops. Each inner body resets before
returning to the outer body.

## Comparison with factorial-03.fb2d (hand-coded)

Steve's hand-coded factorial uses a more fluid layout: the outer `(` at (20,5)
and `%` at (18,5) are on *different rows*, with the body flowing E on row 20
and returning W on row 18. Inner loops use standard `( P %` with `]` before
each `(`. The cleanup loop uses `& P - $` (a variant using `$` = `/` if EX!=0
and `&` = `\` if CL!=0).

The compiler can use a more regular structure (same-row `( P %` with body on
the row above) while achieving the same correctness.

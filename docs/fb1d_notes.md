# fb1d: A 1D Reversible Tape Simulator (and Why It Doesn't Quite Work)

## What this is

`fb1d.py` is a 1D analog of `fb2d.py` — a single tape (code + data + fuel)
with five heads (IP, H0, H1, CL, EX), a small ISA (21 opcodes), and a
conditional jump `J`. The simulator runs the built-in `dec_loop` example
end-to-end: 3 → 2 → 1 → 0 forward, then a full backward reversal that
restores the initial state and clears the EX trail.

Run `python3 fb1d.py` and type `help` or `ops` for the REPL and ISA
reference. It works. But it doesn't quite achieve the property we wanted.

## The property we wanted

`fb2d` claims **physical reversibility under valid-everywhere**: every
state has a unique predecessor derivable from state alone, regardless
of what the grid contains initially. Every byte value is a legal
opcode-or-NOP; every byte sequence is a legal program; and `step_back()`
is purely deductive.

We wanted the same for `fb1d`: a 1D language with the same three
properties (valid everywhere, reversible physics, Turing-complete).

## What we got

`fb1d` uses a run-length EX trail for reversal:

- Non-jump step: `tape[EX]++` (extend the current run count).
- Jump step: EX advances 2 cells, deposits return address and a fresh
  count of 1 in the new cells.

`step_back` reads `tape[EX]`:

- `> 1`: last step was non-jump. Decrement, predecessor at `IP-1`.
- `== 1`: last step was a jump. Restore the two fuel cells, unjump.

This is **functionally reversible given a clean fuel region** — if the
EX area is guaranteed to be zeros before execution, everything works
and the dec_loop trace demonstrates full round-trip. But it is **not
physically reversible** in fb2d's sense, for two reasons:

### 1. Non-zero fuel breaks reversal

The jump deposit sets `tape[EX] = return_addr`, overwriting whatever
was there. If the cell wasn't zero, the old value is lost. Reversal
writes `0` back, not the original value.

fb2d never has this problem because its per-op physics is bijective on
all cell values — `P` and `Q` are inverses regardless of `[EX]`, and
mirrors only touch the direction register (not on the grid). fb1d's
"jump deposit" is a SET, not a swap, and depends on the destination
being zero for correctness.

### 2. Count wrapping

With 8-bit cells, after 255 non-jump steps `tape[EX]` wraps to 0 → 1,
and `step_back` misreads the `1` as a jump boundary. Wider cells defer
this but don't eliminate it.

## Why the gap can't be closed

We explored several approaches to close the gap. None worked:

- **Momentum register / bounces (like fb2d mirrors in 1D):** Direction
  works as a machine register, so it disambiguates predecessors —
  but the loop body executes both eastward and westward, so operations
  fire twice per iteration. fb2d avoids this because return paths live
  on separate rows; 1D collapses forward and return paths onto one axis.

- **Swap-based jump `swap(mom, tape[CL])`:** Momentum becomes reversible
  storage. But loops still burn one garbage cell per iteration, and the
  cells must be pre-loaded with correct offsets — same structural cost
  as the run-length scheme.

- **`[`/`]` bracket pairs with off-tape mode + depth registers:** The
  mode register (like fb2d's direction plus a "skip mode" bit) does
  disambiguate skip vs fall-through *during* skip traversal. But at
  the exit point, the mode collapses back to `EN` (east-normal), and
  the distinguishing information is lost. In fb2d, direction is
  *persistent* — once flipped, it stays flipped until another mirror.
  A 1D skip-mode is *transient*, so the join point ambiguity returns.

- **"No branch unless [EX]==0" rule (Steve's insight):** Makes `J`
  physically two-conditional — fires only if `[CL]!=0 AND [EX]==0`.
  This eliminates the overwrite problem (writes only happen onto
  known-zero cells, so reversal legitimately restores zero). But the
  reversal *coincidence* problem remains: `tape[E-1]` being nonzero
  could indicate "a J just fired here" or "this cell started nonzero
  by chance and happens to look like a valid return address." Two
  different predecessor states can produce the same current state.

## The apparent wall

Every scheme that stores branch history on the tape runs into the
same problem: "valid everywhere" means the initial tape can be
anything, so any diagnostic pattern we look for during reversal can
appear coincidentally. In fb2d, the direction register is not on the
grid — an adversarial initial grid can't pre-populate it — which is
precisely why fb2d works.

In 1D, jumps are non-local. The return address is `O(log n)` bits,
and no finite off-tape register can encode the unbounded history of
jump-or-not decisions. So branch history *must* go on the tape, and
that opens the door to coincidence.

Formally: for physical reversibility, the `step()` function must be
injective on the state space. Under "valid everywhere," we can
construct two distinct predecessor states — one where a J fired,
one where it didn't — that both map to the same successor state. The
function isn't injective, therefore not reversible.

This isn't a proof (we haven't shown *no* scheme could work), but
after several attempts we haven't found a way around it.

## What could still be worth pursuing

**Steve's "clean-fuel precondition" as an honest spec.** If we relax
"valid everywhere" to "valid-everywhere-provided-a-bounded-fuel-region-
starts-clean," Steve's `[EX]==0` precondition combined with the
run-length trail gives *honest* physical reversibility within that
regime. This is more or less what fb2d assumes anyway — its "waste row"
is required to be zeros — just made explicit as part of the language
spec rather than a hidden requirement.

That would be a legitimate contribution: a 1D reversible language whose
"non-valid-everywhere" caveat is a single, precisely-stated structural
requirement rather than an unstated assumption.

## Files

- `fb1d.py` — the simulator. REPL with `s` / `b` for forward/back
  stepping, `d` for tape display, `ex` for trail inspection, `ops`
  for ISA reference. Auto-loads the `dec_loop` example on startup.

## Related project notes

- `docs/sams-ir-idea.text` — Sam Eisenstat's IR mechanism (documented
  as ambiguous in the same file).
- `docs/old-docs/fuckbrain_reversible_summary-2026-01-31.md` — earlier
  summary of the 1D exploration, including the run-length GP scheme
  that this simulator implements.

# Plan: porting the Wikivore to fb1d

*2026-09-07, renamed 2026-09-25; M1 done 2026-09-25, M2 done 2026-09-26.  Naming: the 8-bit bracket language
formerly called RBFF is now **fb1d8** (`fb1d8.py`, `fb1d8.html`); the
16-bit extension described here, formerly "RBFF-16", is **fb1d**
(`fb1d.py`).  The April 2026 1D simulator that used to be `fb1d.py` is
archived as `old-files/1d-and-before/fb1d-v0-extrail.py`.*

## Goal

A 1D self-maintaining agent with the same three ingredients as the fb2d
Wikivore: two gadgets that correct each other's code under bit-flip
noise, a metabolism that turns compressible fuel into the zeros the
corrections consume, and a hunger timer so it eats before it starves.
The host language must stay reversible and valid everywhere.

Plain fb1d8 (8-bit ASCII cells, two heads, one IP) cannot host this.  The
port is really two jobs: extend fb1d8 into 16-bit fb1d, then rewrite the
agent as nested-bracket code instead of 2D routing.  Most of the
extension is a straight transplant from `fb2d.py`.

## Part 1: fb1d, the language extension

Everything below already exists in fb2d and is reversible there; in 1D
the arguments carry over unchanged because none of it depends on
geometry.

### Cells

- 16-bit cells, systematic Hamming(16,11) SECDED, exactly fb2d's
  encoding (`DATA_MASK`, `hamming_encode`, `_CELL_TO_PAYLOAD_RAW` for
  instruction fetch and arithmetic; `_CELL_TO_PAYLOAD` with inline
  single-bit correction only for R/L/Y rotation operands, per fb2d
  v1.17).  Import the tables from `fb2d.py` rather than copying.
  Fetch must NOT Hamming-correct the instruction cell: the [11,6,4]
  opcode decoder already handles all 1- and 2-bit errors, and parity
  bits are for the repair program, not the physics.
- Opcodes are payload codewords from the same [11,6,4] code with
  nearest-codeword decoding: a 1-bit data flip still executes the right
  op, 2-bit flips decode to NOP.  Reuse fb2d's `OPCODE_PAYLOADS`
  numbering so the noise and MTTF tooling transfers.
- NOP filler payload 1017; boundary cell 0xFFFF, shown as `~`.

Why this matters more in 1D than in 2D: in ASCII, one bit flip turns
`[` (0x5B) into `{` (0x7B), which unbalances the whole block.  With the
opcode code a bracket needs 2 flips to become NOP and 3 to become
another op, same as fb2d's mirrors.

### Heads and ops

Per IP: `a`, `b`, `ix`, `p`.  All wrap modulo N.

| Group | Ops | Notes |
|---|---|---|
| fb1d8 core | `< > { } [ ]` | brackets test `tape[a] & DATA_MASK` (payload nonzero), like fb2d's mirrors |
| Payload arithmetic | `+ -` on `tape[a]` | fb2d's delta-p inc/dec: bijective on all 65536 values, parity fixed up |
| Raw XOR | `. ,` | full 16-bit `tape[b] ^= tape[a]` / `tape[a] ^= tape[b]`; this is fb2d's `x`, not fb2d's `.` |
| Interoceptor | `ix` moves (two ops), `m`, `I`, `V`, `j` | `tape[a] ^= tape[ix]`, `^= syndrome_5bit(tape[ix])`, `^= 1 << syndrome_4bit(tape[ix])`, `tape[ix] ^= tape[a]`; all XOR-shaped, self-inverse |
| Optional | `( )` brackets that test `tape[b]` | saves head shuffling; decide during M2 |

Dropped from fb2d: all mirrors, direction register, CL and EX heads,
`P Q K Z T G F f z r l R L Y : ;`, IX momentum ops `A B C D U O`.  In 1D
a sweep is a walk, and boundary detection is "step ix until the cell is
0xFFFF", testable with `m` into a zero scratch cell followed by `+` and
an IF-ZERO (payload 2047 wraps to 0), exactly fb2d's `m T : ? ; T m`
trick minus the bridge op.

### Guards

- Executing-byte guard: any write to `tape[p]` of the *writing* IP is a
  no-op.  Writes to another IP's `p` are allowed; reverse-order undo
  handles it (same as fb2d's cross-IP interference).
- Aliasing guards: `. ,` NOP when `a == b`; `m I` NOP when `a == ix`;
  `j` NOP when `ix == a`.
- Unmatched bracket is a no-op.  Matching is stack matching over the
  linear tape.  **Proposed tweak:** treat 0xFFFF boundary cells as
  matching barriers, so brackets never pair across a boundary.  This
  keeps a corrupted gadget from capturing its neighbour's brackets and
  costs nothing in reversibility (brackets do not write).  Decide in M1.

### Multi-IP

`step_all()` steps IPs round-robin; `step_back_all()` undoes in reverse
order.  Identical to fb2d.  Bracket matching is shared and recomputed
lazily; invalidate the cache only when a bracket byte is written.

### Noise and pools

`NoisePool` already returns `(row, col, bit)`; use row 0 and a column
range covering both code blocks, or give it a flat address range.
Rate stays "flips per 1M step_alls".  `WastePool` is not needed for the
agent (metabolism supplies zeros) but is handy for an immunity-only
milestone, so keep the hook.

### Deliverable

`fb1d.py`: simulator + `--test` (random-state bijectivity on 16-bit
cells, multi-IP round trips, opcode aliasing sweep like
`test_reversibility.py`).  Keep `fb1d8.py` as the didactic 8-bit
version.

## Part 2: tape layout

    | ~ | gadget A code | ~ | A stomach | A fuel ........ | ~ | gadget B code | ~ | B stomach | B fuel ........ | ~ |

- Each code block is balanced, so its internal matching is independent
  of everything outside it.  Boundaries around code blocks give the
  partner's `ix` walk its stop condition.
- Stomach: a handful of zero scratch cells at fixed offsets from the
  gadget's home position, reached by relative head moves.  Everything
  in the code is position-relative, as in the `fib` example.
- Fuel: the A/B/C/D payload rotation fb2d uses, so the free-food cheat
  ports unchanged.
- IP A runs block A and its `ix` walks block B; IP B the reverse.

## Part 3: the gadget program

Control flow is the real translation work.  fb2d's gadget is a route
through rows: probe, bypass row, correction rows, copy-over rows,
handler, metabolism rows, corridor.  In fb1d every one of those becomes
a nested block, and fb1d only has IF-ZERO natively.  Three idioms cover
it (all already exercised in `fb1d8.py` or trivially derived):

- **IF-ZERO** `[X]`: run `X` iff `tape[a] == 0`, `X` must return `a`
  and leave the tested cell unchanged.
- **IF-NONZERO** via a flag: `f = 0; [ f+=1 ]` sets `f` iff the cell is
  zero; then IF-ZERO on `f` is "if the cell was nonzero"; afterwards
  `[ f-=1 ]` uncomputes `f` provided the tested cell is still unchanged.
  This is why the probe result must be kept until after the branch.
- **FOR** for counted loops and **walk** (`[>>>>]`-style) for scans.

Sketch of one pass, one gadget (heads at home, stomach zero):

    outer: [ K += 1
      walk ix to partner block start (past boundary)
      scan: [ ... per cell:
          s ^= I(ix)                    probe: s = 5-bit syndrome of tape[ix]
          IF-NONZERO(s):                dirty path
              w ^= m(ix)                copy the cell in (w zero)
              w ^= V(ix)                apply correction mask locally
              j: tape[ix] ^= w ^ copy   write back (as fb2d: mask via j)
              deposit s into a fuel zero (two XORs), s now 0
              ate_flag toggle if needed
          IF-ZERO(s): bypass path
              hunger -= 1
              IF-ZERO(hunger): set eat flag; hunger ^= period copy
          uncompute flags
          ix += 1
        ] until tape[ix] is boundary (via m-into-scratch, +, test)
      walk ix back
      IF-NONZERO(eat flag): metabolism block, clear flag
    ]

Points to settle when writing it for real:

1. **Correction garbage.**  After `j` fixes the remote cell, `I(ix)`
   returns 0, so the probe result `s` cannot be uncomputed and must be
   dumped into a fuel zero.  Same cost as fb2d's PA deposit, roughly
   two zeros per correction.  This is the Landauer cost and is
   language-independent.
2. **2-bit copy-over.**  fb2d fetches the gadget's own copy of the
   corrupted cell.  In 1D that is `ix` walking to a fixed offset in the
   own block, one `m`, walk back, one `j`.  Defer to M5; single-bit
   correction first.
3. **Metabolism loop polarity.**  fb2d's compression is "advance while
   XOR gives zero, walk back on mismatch".  fb1d's `]` repeats on
   nonzero, the opposite polarity, so write it as a FOR over the bite
   size with an IF-NONZERO mismatch flag that ends the bite early, or
   keep the flag and let the FOR run out.  Reference cell in `b`,
   fuel walked by `a`; `,` does the XOR in place.
4. **Hunger counter.**  Countdown in a stomach cell, reset by XOR from a
   period constant (`hunger ^= period` when it hits zero), no corridor
   needed.
5. **Outer loop entry.**  Use the `K += 1` pattern (enter on zero, runs
   until K wraps) or the counter's start-inside trick.  For an
   immortal agent the wrap is irrelevant: K wrapping to 0 exits the
   loop and the IP falls into the stomach.  Prefer testing a constant
   1 cell like the counter does, with the start-at-`+` convention.

Estimated size: fb2d's correction gadget is 147 ops plus routing; the
fb1d version should land in the same range, with the routing rows
replaced by bracket nesting.  Metabolism plus hunger perhaps 60 to 100
ops.  Whole gadget under 300 cells; two gadgets plus stomach and fuel
comfortably under 1000 cells.

## Part 4: tooling

- **`fb1d8.html` extension** (or a new `fb1d.html`): render 16-bit cells as
  opcode char plus payload, colour syndrome-nonzero cells, show all
  three heads per IP with an IP selector, noise controls (seed, rate,
  enable), free-food button, step counters, "corrections so far".
  The current inspector already explains brackets; add "which IP's ix
  points here".
- **MTTF harness**: port `compare-agents-mttf.py` logic: run to failure
  under a given rate, classify opcode corruption vs zero starvation,
  report mean and spread.  Same units as fb2d so numbers compare
  directly, with the caveat that steps per pass differ.

## M1 status (done 2026-09-25) and decisions taken

`fb1d.py` implements Part 1.  `python3 fb1d.py --test` (~4 s) passes;
`--test --long` adds a 2M-round, 2-IP noisy round trip (603 flips,
every cell changed, 0 diffs after reversing; ~15 s, ~420 MB because
`NoisePool` caches every round's event).  Deliberately breaking any
guard, the barrier rule, or the `-` inverse makes the suite fail.

Decisions, so later milestones don't reopen them:

- **Opcode numbering.**  Each fb1d op uses the fb2d opcode number of the
  *same character* (`[` = 30, `+` = 15, `m` = 53, ...).  Semantics can
  differ from fb2d's op of that character: fb1d `.` and `,` are raw XOR.
  Codewords of fb2d-only ops are NOPs in fb1d, so 6144 of 65536 words
  are live ops.
- **ix moves are `A` (ix += 1) and `B` (ix -= 1)**, fb2d's IX
  advance/retreat codes.  This avoids a clash with the head named `a`.
- **Boundary barrier: adopted.**  A cell whose *raw payload* is 2047
  empties the bracket stack, so brackets never pair across a boundary.
  Raw payload rather than exact 0xFFFF, so a parity-bit flip on a
  boundary cell leaves it a barrier.  `Machine(barriers=False)` turns it
  off for MTTF comparisons in M3/M5.
- **Bracket test** is `tape[a] & DATA_MASK != 0`, as fb2d's mirrors.
- **`( )` brackets on `b`: not added.**  Revisit in M2 if head
  shuffling hurts.
- **Noise** reuses `pools.NoisePool` with one "row" and a flat column
  range; applied after each `step_all()`, undone first in
  `step_back_all()`, indexed by the round count (as `fb2d_server.py`).
- **Examples:** fb1d8's copy / counter / add / fib / fact run unchanged
  on fb1d (payloads mod 2048), reusing fb1d8's `FOR` / `ADD` macros.
- **Guards** are exactly the list in Part 1, and `I` / `V` share `m`'s
  `a == ix` guard.

## M2 status (done 2026-09-26) and decisions taken

`programs/fb1d-immunity-m2.py` builds and tests a 131-cell gadget that
corrects a static partner block (a copy of its own code, boundaries
included).  `python3 programs/fb1d-immunity-m2.py` (~18 s) passes:

- every (cell, bit) single-bit error in the partner block, 2128 cases,
  is corrected within one pass, with the frame healthy afterwards and
  the trail contiguous nonzero; 40 of them stepped back to the initial
  state exactly;
- two errors in one pass; 60 passes under 300 flips/1M noise (94 flips,
  24 single-bit errors corrected, 0 missed), 383k-round exact reversal.

Measured costs:

| Quantity | Value |
|---|---|
| Garbage per correction | 2 fuel cells (old marker `M`, bumped mask), same as fb2d's EV + PA |
| Fuel per pass | 1 cell (the "moult" that resets `M`, see below) |
| Steps per cell visit | ~24 clean (`WORK` is 34 ops; a skipped block costs one step) |
| Steps per pass | 2L+3 visits, 6370 rounds at L = 131 |
| Code size | 131 cells; both sweeps carry a full `WORK`, so ~40% is duplicated |

Decisions:

- **`( )` brackets on `b`: added** (fb2d opcode numbers 34/35, own
  matching stack, barriers empty both stacks).  Not for head shuffling
  but for the merge: a block that tests `a` can move `b` to any cell
  without testing it, and vice versa.  `fb1d.py --test` covers them.
- **The merge needs a clean-path trace, as in fb2d.**  Exhaustive
  scratch searches over all `[ ]`/`( )` programs up to 9 ops, with the
  garbage trail modelled as a periodic pattern, found no way to bring
  the clean and dirty paths to the same frame-relative state when the
  clean path writes nothing.  The reason is that every block exits with
  its tested head on a zero cell, so the last conditional leaves both
  cases on cells of the same type.  fb2d pays this with `P` on the EX
  cell on the bypass path; fb1d pays with `M += 2` per clean cell
  (`<+>` at both ends of `WORK`), where `M` is the marker cell under
  `b` whose zero-ness is the merge witness.
- **`M` is bounded by a pass-end moult**, `}+>`: b and a step right by
  one, the old `s` becomes `M' = 1`, the old `g` becomes `s'`.  One
  fuel cell per pass; `M` stays in `[1, 4L+5]` and never wraps.  The
  alternative, decrementing during the rewind, fails when the
  correction happens on the first scanned cell (`M` lands on 0).
- **Frame, not stomach.**  The working cells `[M s g]` move right
  through the fuel with the garbage (2 cells per correction) left
  behind; there is no fixed stomach and no walking.  All code is
  position-relative.  Hence the free-food cheat for M4 must refill
  *ahead* of the frame.
- **`WORK` runs in both sweep directions and before every boundary
  test**, so a data-bit flip on a `~` is repaired before it can
  misdirect `ix`.  Coverage is therefore fb2d-v3-style ping-pong (each
  cell visited twice per pass), not v4 rewind-loop; revisit in M3 if
  the MTTF comparison wants uniform gaps.  No opcode payload is one
  flip from 2047, so a false boundary cannot arise from one flip.
- **2-bit errors are miscorrected** (a 2-bit error has a nonzero
  syndrome, `V` then flips a third bit), exactly as fb2d before its
  copy-over row.  M5.
- **Not needed after all:** the "IF-NONZERO via flag" idiom is used
  once (`[>+<]` sets `g`), and its uncompute is the merge above; no
  `FOR` loops; no `WastePool`.

## Recommended model and effort per stage

Written 2026-09-25 as budget guidance: spend on the strongest model
where the hard part is inventing reversible control flow, and use
cheaper models where a strong test suite catches mistakes.

| Stage | Why | Model | Effort |
|---|---|---|---|
| M1 `fb1d.py` core | transplant, test-checked | Opus 5.5 | high (done) |
| M2 single gadget | novel bracket-nested reversible gadget, uncompute bookkeeping | Fable 5.1 | high (done) |
| M3 dual gadgets | cross-IP interaction | Opus 5.5 | high |
| M3 MTTF harness | port of `compare-agents-mttf.py` | Sonnet 5 | medium |
| M4 metabolism + hunger | loop-polarity redesign, same difficulty as M2 | Fable 5.1 | high |
| M4 GUI + free food | extend `fb1d8.html` | Sonnet 5 | medium |
| M5 2-bit copy-over | builds on M2 idioms | Opus 5.5 | high |
| docs / renames | mechanical | Haiku 4.5 or Sonnet 5 | low |

Cost-saving pattern for M2 and M4: have Fable write an op-by-op design
(tape layout, head positions at each phase, what is uncomputed and what
is dumped as garbage), commit it to `docs/`, then have Opus implement
and debug it against the tests.

## Milestones

| # | Deliverable | Done when |
|---|---|---|
| M1 ✓ | `fb1d.py` core, tests, noise hook, boundary-barrier decision | bijectivity tests pass; 2M-step multi-IP round trip with noise gives zero diffs |
| M2 ✓ | Single gadget correcting a static partner block (no partner IP) | corrects injected 1-bit errors anywhere in the block; garbage per correction measured |
| M3 | Dual gadgets, mutual correction, MTTF harness | MTTF curves at 100/200/300 flips per 1M, compared to fb2d narrow agent |
| M4 | Metabolism + hunger + free-food cheat + GUI | agent runs indefinitely with free food at 200 flips per 1M; starves without it, as fb2d does |
| M5 | 2-bit copy-over, boundary tweak if adopted, notes | MTTF gain measured; `docs/` updated |

M1 is mostly transplant work.  M2 is where the idioms get stress-tested
and is the milestone most likely to change the plan.  M3 and M4 reuse
fb2d's experimental setup nearly verbatim.

## Risks

- **Bracket corruption is global within a block.**  A NOP'd `]` shifts
  every pairing after it.  fb2d's mirror corruption is local but also
  fatal to the pass, so the practical difference is small, and the
  boundary-barrier tweak contains the blast radius to one gadget.
- **Nonlocality.**  Each bracket step consults the whole tape.  For the
  thermodynamics story this is action at a distance; fb2d's step is
  local.  Worth a paragraph in `theory-notes.md`, not a blocker.
- **Loop polarity friction.**  Every "while nonzero" in the fb2d design
  needs the flag idiom.  Expect the first draft of M2 to be twice the
  size of the second.
- **Head shuffling.**  Two data heads instead of four means more moves
  per operation.  Adding `( )` on `b` is the cheap fix if it hurts.

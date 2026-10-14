# fb1d M4: metabolism and hunger, op by op

*2026-10-13.  Design for milestone M4 of `docs/fb1d-port-plan.md`:
the M3 pair of gadgets (`programs/fb1d-immunity-m3.py`) plus a
metabolism that turns runs of identical fuel cells into the zeros the
corrections consume, a hunger test that triggers it, and the free-food
cheat.  Implemented in `programs/fb1d-agent-m4.py`.*

## Summary

- **Two new bracket families** in fb1d: `a1[ ]a1` (chars `? !`) and
  `b1[ ]b1` (chars `% &`), which enter on *nonzero* and repeat while
  *zero*, mirroring `a0[ ]a0` and `b0[ ]b0`.  Same reversibility
  argument (the skip condition at the opener equals the repeat
  condition at the closer), own matching stacks, barriers empty them.
  Every walk in the metabolism then starts on a nonzero cell, which is
  what the zero-polarity brackets could not express.
- **Hunger is a lookahead, not a countdown.**  At the end of each
  pass the gadget tests the cell `d` places ahead of `b`: nonzero means
  fewer than `d` clean zeros remain, so it eats one run.  No period
  constant, no reset, and the agent senses its reserve directly.
- **One eat zeroes a whole run of `K` identical cells** (the bite).
  The run's value is kept in a stomach cell `r` and dumped into the
  moult cell at the end of the same pass, so an eat costs no extra fuel
  cell and the eat flag is uncomputed from `r` itself (no witness).
- **Fuel accounting per pass:** 1 cell (moult) + 2 per correction, as
  M2; one eat gives `K` zeros.  At every pass start at least
  `min(d, K) - 1` zeros lie ahead of `b` (the moult follows the hunger
  test), so up to `(min(d, K) - 1) / 2` corrections in one pass are
  safe.  Defaults `K = d = 15` (7).
- **Free food** is a conveyor: when fewer than two bites of food lie
  ahead of `b`, the cheat shifts the fuel region left so that `b`'s
  cell becomes the first fuel cell, discards the garbage trail, and
  fills the freed tail with food continuing the A/B/C/D rotation.  This
  is fb2d's "replace the garbage with food" on a torus, written for a
  line.  Non-reversible, as in fb2d.

## Tape layout

    ~ [A code] ~ [sA gA rA] [LA] [A fuel ...............] ~ [B code] ~ [sB gB rB] [LB] [B fuel ...............]
    ^ixB         ^aA        ^bA                           ^ixA         ^aB        ^bB

Per gadget, after the trailing wall: stomach `s g r` (three zero cells
under head `a`, which rests on `s`), then `L0` (seeded 1, head `b`),
then the fuel region: `z0` zeros, then food in runs of `K` identical
cells with payloads cycling 189, 250, 380, 639 (fb2d's A/B/C/D), up to
the next wall or the end of the tape.

Invariant ahead of `b` at a pass start: a contiguous block of zeros
(at least `min(d, K) - 1` of them), then unbroken food runs, each exactly
`K` long and different from its neighbours, then whatever follows.
Behind `b`: the garbage trail, every cell nonzero (M2's invariant);
`b` sits on its last cell `L`.

Why runs must be exactly `K`: the only place the eat needs to leave a
zero cell is `F`, the first cell of the run, and a head on a zero
amid zeros cannot start any bracket walk.  The eat therefore leaves
`F` for last and walks off it with `K` unconditional moves, which land
on the next run's first cell because the run is `K` long.  (Why `F`
cannot simply stay nonzero: a nonzero cell inside the zeros is a
landmine for the correction path `}.,}`, which needs two fresh zeros
and has no test.  Why the old zeros and the new ones cannot be merged
with a dump behind `b` instead: the reverse computation would have to
know where `F` was, i.e. how many zeros preceded it, so that count
would have to be recorded; `F = M - K` records it for free.)

## Stomach and heads at the pass end

M2's outer loop is `[ - WORK(rest) scan rewind }P + ]` with the IP
started inside, `a` on `s`, `b` on `L`, `ix` back on the partner's
leading wall.  M4 replaces the moult `}P` with the block below.  `s`
and `g` are zero, `r` is zero, `L` is nonzero.

## The pass end, op by op

Readable names in the right column (`a>` = `>`, `b>` = `}`, `b1[` = `%`,
etc.).  `K` and `d` are build parameters; `}×d` means `d` copies.

```
HUNGER TEST                                        a on s, b on L
  >            a to g                              a>
  }×d          b to L+d                            b> ...
  b1[ + ]b1    g += 1 iff tape[L+d] != 0           b1[ a+ ]b1
               (the block leaves b's cell as it was, so it is a plain
                IF-NONZERO; g = 1 means hungry: fewer than d zeros)
  {×d          b back to L                         b< ...

EAT (skipped when g == 0)                          a on g, b on L
  a1[          enter iff g != 0                    a1[
    >          a to r                              a>
    b1[ } ]b1  walk b right over zeros to F, the first nonzero cell
               (enters because L != 0; repeats while the new cell is 0)
    ,          r ^= tape[F]: r = v, F unchanged    a^=b
    b1[ } . ]b1  compress: b += 1, tape[b] ^= r; repeat while the
               result is 0.  Each of F+1 .. F+K-1 held v and is now 0.
               Stops on M = F+K, the next run's first cell, which now
               holds w ^ v != 0 (w != v because neighbouring runs
               differ; if the run is the last and M was 0, M holds v).
    .          tape[M] ^= r: M restored to w        b^=a
    b1[ { ]b1  walk b left over the K-1 zeros back to F (F = v != 0)
    .          tape[F] ^= r: F = 0                  b^=a
    }×K        b to M, unconditionally (the run was exactly K long)
    b1[ { ]b1  walk b left over the K zeros, and the zeros before F,
               back to L (the first nonzero cell)
    <          a to g                              a<
  ]a1          g still 1: falls through

UNCOMPUTE THE FLAG                                 a on g, b on L
  >            a to r                              a>
  a1[ < - > ]a1  g -= 1 iff r != 0.  r != 0 exactly when the eat ran
               (v != 0), so g is 0 afterwards in both cases.

MOULT WITH DUMP                                    a on r, b on L
  }            b to L+1, a fresh zero              b>
  .            tape[L+1] ^= r: v if we ate, else unchanged 0
  ,            r ^= tape[L+1]: r = 0 in both cases
  P            tape[L+1] payload += 1: the new L is v+1 or 1, nonzero
  <<           a to s                              a< a<
```

Then M2's `+ ]` closes the outer loop.  Cost: `2d + K + 36` ops
(`81` at `K = d = 15`), on top of M2's 106 minus the old `}P`.

## What each path leaves behind

| Path | Fuel cells used | Zeros gained | Trail cells written |
|---|---|---|---|
| clean pass | 1 (moult) | 0 | new `L = 1` |
| pass with an eat | 1 (moult) | `K` | new `L = v + 1` |
| each correction (M2) | 2 | 0 | `mask + 1`, new `L = 1` |

The eat path has no witness cell: the merge of the eat and no-eat
paths is done through `r`, which is nonzero iff the eat ran, and `r` is
then erased into the moult cell that a clean pass writes anyway.  So
the Landauer cost of "did I eat" rides on the cost of "which pass is
this".

Zeros ahead of `b` after the hunger test: if not hungry, at least
`d`; if hungry, at least `K` (the eaten run) plus whatever was left.
The moult then takes one, so `min(d, K) - 1` at every pass start, and
a pass with `c` corrections needs `2c` of them: `c <= (min(d, K) - 1) / 2`.

## Why the lookahead rather than a countdown

A countdown needs a period constant and a reset `h ^= period`, but
fb1d's XOR ops pair `a` with `b` or with `ix`, both of which are away
from the stomach at the pass end; a reset by repeated `+` costs
`period` ops.  The lookahead costs `2d + 3` ops, no constant, and
measures the reserve rather than time, so the agent eats exactly when
its stock is low.  The price is a fixed margin: a pass with more than
`(min(d, K) - 1) / 2` corrections runs out of fresh zeros mid-pass and
the correction path corrupts the stomach (verified: 7 survive, 8 do not).  At 300 flips per 1M rounds
over two blocks of ~190 cells a gadget corrects about one error per
pass, so 7 in one pass is a 1e-5 event per pass.

Options kept in reserve, not implemented: test hunger after each sweep
instead of each pass (halves the window), or eat an extra run when a
correction happened (an `eat` flag set in WORK, which the pass end
reads).

## Failure modes without food

When the food is gone, the walk to `F` finds the wall (or the next
gadget's code); the "eat" of that one-cell run changes nothing lasting
but dumps `0xFFFF` into the moult cell, whose `P` makes it 0, so `L`
becomes 0 and the next pass's merge misfires.  The harness reports it
as `zero_starved` (b near the region end) or `L_wrong`.  The agent
starves without the cheat, as fb2d's does.

## Free-food conveyor (cheat)

Applied between rounds, at a gadget's pass start, by the harness and
the workbench; never by the gadget.  With `b` on `L` and the fuel
region `[L0, hi]`:

1. Count the food cells in `[b+1, hi]` (payload in the food set).  If
   at least `2K`, do nothing.
2. Shift `tape[b .. hi]` down to `tape[L0 .. L0 + hi - b]`, set
   `b = L0`.  The garbage trail `[L0, b-1]` is overwritten.  Nothing
   else moves: `a`, `ix`, the IP and the code are untouched, and the
   gadget is position-relative to `b`, so the shift is invisible to it.
3. Fill the freed tail with food: first complete the last (possibly
   partial) run to `K` cells with its own payload, then whole runs in
   rotation; the final run may be partial, and the next refill
   completes it before `b` can reach it (the trigger fires while at
   least one full run is still ahead).

Equivalent to fb2d's cheat on its toroidal EX row.  The simulation is
not reversible across a cheat; the workbench will not step back past
the last one.

## Tests (`programs/fb1d-agent-m4.py`)

- clean lockstep passes: zeros ahead never below `min(d, K)`, an eat
  every `K` passes, `r = 0` and `g = 0` at every pass start, `L` is 1
  or food+1, exact reversal;
- one eat in isolation: the run is zero, the next run intact, `b`
  back on `L`, the moult cell holds `v + 1`;
- every (block, cell, bit) single-bit error repaired by the partner
  within a pass (as M3), with the fuel accounting above;
- many corrections in one pass (up to 7) survive, 8 do not;
- noise at 200 and 300 per 1M with the conveyor for hundreds of
  passes: no missed single-bit error, stomachs clean;
- no conveyor: the agent starves at the expected pass;
- noisy run reversed exactly (conveyor off).

## Measurement (`programs/fb1d-mttf.py`, M4 is the default agent)

Same harness; the conveyor is on unless `--no-free-food`.  New
invariants: `r = 0` at a pass start, `L` in {1, food+1}.  Results
(2026-10-13, 10 seeds, 10M rounds): 9.13M / 5.88M / 2.84M mean rounds
to failure at 100 / 200 / 300 flips per 1M over both blocks, against
8.25M / 5.23M / 2.52M for the M3 pair; no starvation with the cheat,
all trials starve at ~387k rounds without it on a 150-cell region.
Details and the reading of the failure causes: the M4 status section
of `docs/fb1d-port-plan.md`.

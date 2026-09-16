# RBFF: a 1D reversible, valid-everywhere, Turing-complete BFF

*2026-09-07.  Design from a ChatGPT conversation forwarded by a friend of
Steve's; independently re-implemented and verified in `rbff.py`.*

This corrects the conclusion in `fb1d_notes.md`.  A 1D reversible BFF
with all three properties exists, and it is small.

## The language

State is `(tape, a, b, p)`: N byte cells, two data heads, one
instruction pointer.  Every step executes `tape[p]` and then does
`p += 1` (always, including after a jump, so a jump lands one past the
partner bracket).

| Op | Effect |
|----|--------|
| `<` `>` | `a -= 1`, `a += 1` |
| `{` `}` | `b -= 1`, `b += 1` |
| `-` `+` | `tape[a] -= 1`, `tape[a] += 1` |
| `.` | `tape[b] ^= tape[a]` |
| `,` | `tape[a] ^= tape[b]` |
| `[` | if `tape[a] != 0`: jump to matching `]` (enter body only when zero) |
| `]` | if `tape[a] != 0`: jump to matching `[` (repeat body while nonzero) |
| other | no-op |

Three guards make the step function a bijection on the entire state
space:

1. A write whose target is `tape[p]` is a no-op (the executing byte is
   protected, so `step_back` can read which op to undo).
2. XOR with `a == b` is a no-op (`x ^ x = 0` would lose information).
3. Unmatched brackets are no-ops.  Matching is ordinary nesting over
   the linear tape `0..N-1`, no wrap.

`step_back` is purely deductive: `p -= 1`, read `tape[p]`, apply the
inverse (`+`/`-` and the head moves swap; XOR and brackets are
involutions).  No history register, no trail, no clean-fuel region.

## Why the brackets work

Both brackets jump on the *same* condition, `tape[a] != 0`, and a
jump changes nothing but `p`.  So after any bracket step you are one
past some bracket `r`, and the tested cell still holds what it held
during the step:

- landed at `r+1` with `tape[a] != 0`: you jumped here from `r`'s partner
- landed at `r+1` with `tape[a] == 0`: you fell through `r`

That is the whole trick.  Formally the bracket step is a conditional
transposition `ell <-> r` on `p`, hence an involution, and `p += 1` is a
bijection, so the composite is a bijection.

Original BFF uses *asymmetric* conditions (`[` skips on zero, `]`
falls through on zero).  Then landing at `r+1` with `tape[a] == 0` is
ambiguous: skipped from `ell`, or fell through `r`?  That ambiguity is
exactly the join-point problem our 1D attempts hit.

`[body]` therefore means Janus's `from x==0 loop body until x==0`:
enter only if the cell is zero, repeat while nonzero.  It is not BFF's
`while x != 0`.

## Why our "wall" argument was wrong

`fb1d_notes.md` argued: jumps are nonlocal, the jump-or-not decision
is one bit per jump, no finite off-tape register can hold unbounded
history, therefore history must go on the tape, therefore an
adversarial initial tape can fake it.

The missing case: the decision need not be *stored* anywhere if it is
a *function of the post-state*.  RBFF makes it one by (a) pairing jump
sites so the origin is recoverable from the landing site, and (b)
using the same, unchanged condition cell at both ends.  Nothing about
this needs 2D.  fb2d's off-grid direction register solves the same
problem a different way (and locally); RBFF solves it nonlocally,
exactly as BFF's own bracket matching already is.

## Tools

- `rbff.py`: interpreter, terminal REPL, examples, `--test` suite.
- `rbff.html`: browser workbench with the same interpreter in JavaScript
  and the same examples.  Open the file directly.  Hover any cell for
  its value, op meaning, segment, bracket partner and whether it would
  jump right now; click a cell to edit it; step and play in both
  directions; edit the code in the text box and reload it.

## Verification (`python3 rbff.py --test`)

- Exhaustive bijectivity: every tape over a 12-symbol alphabet at N=3
  and an 8-symbol alphabet at N=4, times every `(a, b, p)`.  Image
  count equals state count; every state round-trips.  (A scratch run
  also did N=4 with all 12 symbols: 1,327,104 states, bijective.)
- 200 random self-modifying trajectories × 500 steps reverse exactly.
- The binary counter, `add`, `fib`, and `fact` examples all run
  forward and are then stepped back deductively to their initial state.
- The ChatGPT compilation of Brainfuck into RBFF's `+ - < > [ ]` subset
  was also checked (in a scratch script) on six programs including
  triple-nested loops: outputs match plain Brainfuck and reverse.

## Turing completeness

The `+ - < > [ ]` subset with an unbounded tape simulates Brainfuck by
Bennett's method: each source loop decision records one history flag in
a fresh cell.  Byte cells with unbounded tape is the same finite caveat
fb2d carries.  Garbage grows with runtime when simulating an
*irreversible* program; RBFF programs written reversibly (the counter,
the FOR idiom below) run garbage-free.

For the agent this is the important point: given spare zero cells,
RBFF can do ordinary irreversible-style computation, paying one fresh
cell per branch.  That is the same economy the fb2d agent already lives
in (zeros on the EX row are the fuel).

## Programming idioms (used in `rbff.py` examples)

**IF-ZERO.**  With `a` on cell `x`, `[ X ]` runs `X` iff `x == 0`,
provided `X` leaves `x` unchanged and returns `a` to `x`.  (`[` enters
on zero; `]` then sees zero and falls through.)

**Copy.**  `[.>}]` with `a`, `b` on the zero cells just before a
zero-terminated source and an all-zero destination.  XOR into zeros is
a copy; XOR into non-zeros mixes, which is the thermodynamic point.

**Walk.**  `[>>>>]` from a zero cell walks right in strides of 4 while
the cells are nonzero, stopping on the first zero.  Head position is
recomputed from data each pass, so loops can be position-relative
(no absolute addresses in code).

**Binary counter** (ChatGPT's): `[>[,>][,<,]<-+]`, 15 bytes, no
garbage, O(1) amortized per increment.  Head `b` sits on a constant 1
so `,` flips the bit under `a`.  `[,>]` clears trailing ones,
`[,<,]` sets the next bit and walks back over the cleared ones,
restoring the sentinel.  Startup: begin at the final `+` so the
constant gets created and `]` jumps to the top.

**FOR (garbage-free counted loop).**  The hard part of RBFF is that
`[` needs zero to enter and `]` needs zero to exit, at the same head
position, with the same body on first entry and re-entry.  So the
tested cell must be zero at the start, zero at the end, nonzero in
between.  Scratch cells `t c d` and a bound `x`:

    d = c ^ x                         (zero iff c == x)
    t = c ^ (x if c == x else 0)      (zero iff c == 0 or c == x)

Body: uncompute `t` and `d`, run the work, `c += 1`, recompute `d`
and `t`; `]` tests `t`.  Afterwards `c == x`, and one more XOR clears
it.  Costs about 40 ops of overhead per iteration but leaves every
scratch cell zero.  `add`, `fib` (two FORs) and `fact` (a FOR whose
body is a FOR) are built from it.  See `FOR()` in `rbff.py`.

## Caveats

- **Nonlocal jumps.**  Bracket matching scans the tape, and a bracket
  byte written far away can change which `]` a `[` pairs with.  This is
  how BFF and cubff already work, but it is not local physics like
  fb2d's mirrors.  Whether a *local* 1D reversible valid-everywhere TC
  language exists is still open.
- **Loop semantics are unfamiliar.**  `[body]` enters only on zero.
  Most BFF-style loops need the FOR idiom or the position-walk idiom.
- **Copy needs zero destinations.**  Replication can only XOR into
  clean tape, which changes the evolutionary dynamics.

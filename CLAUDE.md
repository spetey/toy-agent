# CLAUDE.md — toy-agent project context

## Project Goal

Build a "toy agent" in a digital world that can resist its own degradation
by noise. The first milestone is a *reversible*, *Turing-complete*,
*valid-everywhere* programming language called **fuckbrain 2D** (fb2d),
based on Google's BFF from the Computational Life paper, which is in turn
based on brainfuck. ("fuckbrain" = reversible brainfuck.)

## Key Properties

- **Reversible**: every state has a unique predecessor, inferred from state
  alone (no history needed). `step_back()` is purely deductive.
- **Valid everywhere**: every byte value (0-255) is either a known opcode
  or NOP. Any byte sequence is a valid program.
- **Turing-complete**: proven via counter machine simulation (see
  `docs/tc_proof_sketch.md`). The 8-bit cell size makes the implementation
  a finite approximation; true TC requires unbounded integers (see below).

## Architecture

fb2d is a 2D reversible esoteric language where:
- Multiple instruction pointers (IPs) move on a toroidal grid, interleaved
  round-robin (IP0 steps, IP1 steps, IP0, ...). Each IP has independent
  heads (H0, H1, IX, CL, EX); the grid is shared.
- Mirrors (`/`, `\`) and conditional mirrors change IP direction
- Multiple heads (H0, H1, IX, CL, EX) per IP point into the grid for data access
- Code and data share the same surface (von Neumann architecture)

## Notation Convention

- `H0`, `H1`, `IX`, `CL`, `EX` = head *positions* (addresses)
- `[H0]`, `[H1]`, `[IX]`, `[CL]`, `[EX]` = *values* at those positions
- `H0++` = move head (e.g., East)
- `[H0]++` = increment cell value

## Files

### Core

- **`fb2d.py`** — The simulator. Interactive REPL with grid display,
  forward/reverse stepping, save/load. Run with `python3 fb2d.py`.
- **`ifbc.py`** — Compiler from "intermediate fuckbrain" (ifb) to fb2d
  grid files. Supports variables, arithmetic, nested while loops, swap,
  zero, stream I/O. Run tests: `python3 ifbc.py --test-all`
- **`pools.py`** — Reversible virtual pools for waste cleanup and noise
  injection. WastePool provides infinite clean zeros (consumed via swap
  with dirty working-area cells). NoisePool provides deterministic,
  seed-based noise (rate-tunable flips per 1M rounds). Both are fully
  reversible for `step_back()`. Run tests: `python3 test_pools.py`
- **`programs/immunity-gadgets-v5-low-waste.py`** — Low-EX-waste
  dual-gadget builder and test suite (★ current MWE). Builds
  `immunity-gadgets-v5-low-waste-w100.fb2d`. Key ideas: (1) EX sits
  on a dirty cell in neutral state; `(` at main merge fires on
  [EX]!=0 for bypass, NOPs for correction (clean EX from Phase G).
  (2) Rewind loop uses `(` re-entry with P-based signaling instead
  of `&` with CL accumulation — no T Z ] deposit needed, CWL stays
  clean. (3) Bypass does zero EX consumption (just uncomputes stomach).
  (4) Phase G `+Z]+Z]` deposits EV+PA with `+` ensuring non-zero
  trail (no blanks). 374 ops, W=100, 58% clean-path speedup.
  Run tests: `python3 programs/immunity-gadgets-v5-low-waste.py`
- **`programs/immunity-gadgets-v4-loop.py`** — Rewind-loop dual-gadget
  builder and test suite. Builds `immunity-gadgets-v4-loop-w99.fb2d`.
  R+7 layout with uniform top-down sweep. Superseded by v5 but kept
  for reference. Run tests: `python3 programs/immunity-gadgets-v4-loop.py`
- **`programs/immunity-gadgets-v3-bypass.py`** — Previous probe-bypass
  dual-gadget builder (ping-pong sweep). Superseded by v4 but kept for
  reference and comparison.
- **`programs/sweep-model.py`** — Analytical + Monte Carlo comparison of
  ping-pong vs rewind-loop sweep strategies. Computes worst-case gaps,
  MTTF, crossover points, gadget-size sensitivity.
  Run: `python3 programs/sweep-model.py`
- **`programs/`** — Example .fb2d state files and .ifb source files.
  `load`/`save` in fb2d.py defaults to this directory.

### Documentation

- **`docs/tc_proof_sketch.md`** — Turing completeness proof sketch via
  counter machine simulation using Fredkin dispatch blocks.
- **`docs/nested-loops-notes.md`** — How nested loops work with monotonic
  EX consumption ("burning zeroes").
- **`docs/sams-ir-idea.text`** — Sam Eisenstat's instruction register idea
  for resolving ambiguity in the 1D predecessor.

### Historical

- **`old-files/1d-and-before/`** — Earlier 1D simulators and programs.
- **`old-files/2d-older/`** — Earlier 2D simulator iterations.
- **`old-files/ifbc-02.py`** — Previous compiler version.

## Minimal Working Example

**`programs/immunity-gadgets-v5-low-waste-w100.fb2d`** — Two mutually-correcting
Hamming(16,11) gadgets on a 22×100 grid with probe-bypass parity skip,
top-down rewind loop, and )P low-EX-waste conditional advance. Load in the GUI:

```bash
python3 fb2d_server.py          # default port 5001
python3 fb2d_server.py 5002     # second instance on different port
# Open http://localhost:5001, load immunity-gadgets-v5-low-waste-w100
# Enable noise (seed 42, 50 flips/1M), watch corrections in real-time
```

Each gadget's IX scans the other's code+handler+return+bypass rows.
Clean cells (~95%) take a 28-op bypass; dirty cells get full 358-op
Hamming correction. Boundary rows use 0xFFFF (shown as `~`). NOP filler
(payload 1017, shown as `o` in GUI) is 2-bit-error safe — the 64th
codeword of the [11,6,4] opcode code. Waste cleanup is auto-enabled.

Build and test: `python3 programs/immunity-gadgets-v5-low-waste.py`

## ISA Summary (v1.15, 62 opcodes + NOP)

### Mirrors
| Op | Code | Meaning |
|----|------|---------|
| `/` | 1 | Unconditional / reflect |
| `\` | 2 | Unconditional \ reflect |
| `%` | 3 | / reflect if [CL] != 0 |
| `?` | 4 | / reflect if [CL] == 0 |
| `&` | 5 | \ reflect if [CL] != 0 |
| `!` | 6 | \ reflect if [CL] == 0 |

Mirror geometry: `/` maps E<->N, S<->W. `\` maps E<->S, N<->W.

### Head Movement
| Op | Code | Meaning |
|----|------|---------|
| `N/S/E/W` | 7-10 | H0 move North/South/East/West |
| `n/s/e/w` | 11-14 | H1 move North/South/East/West |
| `H/h/a/d` | 49-52 | IX move North/South/East/West |
| `^/v/>/< ` | 25-26,23-24 | CL move N/S/E/W |
| `{/}/]/[` | 32,31,29,30 | EX move N/S/E/W |

### Byte-Level Data
| Op | Code | Meaning |
|----|------|---------|
| `+` | 15 | [H0]++ |
| `-` | 16 | [H0]-- |
| `.` | 17 | [H0] += [H1] |
| `,` | 18 | [H0] -= [H1] |
| `X` | 19 | swap([H0], [H1]) |
| `F` | 20 | if [CL]!=0: swap([H0], [H1]) — Fredkin gate |
| `G` | 21 | swap(H1_register, [H0]) — indirect H1 |
| `T` | 22 | swap([CL], [H0]) — bridge |

### Bit-Level Data (v1.6 + v1.7)
| Op | Code | Meaning |
|----|------|---------|
| `x` | 39 | [H0] ^= [H1] — XOR, self-inverse |
| `r` | 40 | [H0] rotate right 1 bit — inverse: `l` |
| `l` | 41 | [H0] rotate left 1 bit — inverse: `r` |
| `f` | 42 | if [CL]&1: swap([H0], [H1]) — bit-0 Fredkin |
| `z` | 43 | swap(bit0 of [H0], bit0 of [H1]) |
| `R` | 44 | [H0] rotate right by ([CL]&15) bits — inverse: `L` |
| `L` | 45 | [H0] rotate left by ([CL]&15) bits — inverse: `R` |
| `Y` | 46 | [H0] ^= ror([H1], [CL]&15) — fused rotate-XOR, self-inverse |
| `:` | 47 | [CL]++ — inverse: `;` |
| `;` | 48 | [CL]-- — inverse: `:` |

### EX (Exteroceptor) Ops
| Op | Code | Meaning |
|----|------|---------|
| `P` | 27 | [EX]++ — leave breadcrumb |
| `Q` | 28 | [EX]-- — erase breadcrumb |
| `(` | 34 | \ reflect if [EX] != 0 |
| `)` | 35 | \ reflect if [EX] == 0 |
| `$` | 37 | / reflect if [EX] != 0 |
| `#` | 36 | / reflect if [EX] == 0 |
| `K` | 33 | swap(CL_register, EX_register) |
| `Z` | 38 | swap([H0], [EX]) — byte-level EX swap |

### IX (Interoceptor) Ops (v1.9)
| Op | Code | Meaning |
|----|------|---------|
| `m` | 53 | [H0] ^= [IX] — raw 16-bit XOR (self-inverse, copy-in/uncompute) |
| `I` | 54 | [H0] ^= syndrome_5bit([IX]) — syndrome inspect (self-inverse) |
| `j` | 55 | [IX] ^= [H0] — write-back (raw 16-bit, self-inverse) |
| `V` | 56 | swap([CL], [IX]) — test bridge (self-inverse) |

IX is a programmable interoceptor for cross-gadget correction.
In the dual-gadget architecture, each gadget's IX points at the
other gadget's code cells. The copy-down pattern: `m` copies a
remote codeword to a local EX cell, correction runs locally,
then `j` writes the correction mask back to the remote cell.

### IX Horizontal Momentum Ops (v1.10)
| Op | Code | Meaning |
|----|------|---------|
| `A` | 57 | Advance IX in `ix_dir` — inverse: `B` |
| `B` | 58 | Retreat IX opposite `ix_dir` — inverse: `A` |
| `U` | 59 | Flip `ix_dir` via XOR 2 (E↔W, N↔S) — self-inverse |

Per-IP field `ix_dir` (defaults to `DIR_E`). Enables serpentine
scanning: IX sweeps east, detects boundary via `m T ? T m` (local-only
test), retreats (`B`), advances vertically (`C`), flips direction (`U`).

### IX Vertical Momentum Ops (v1.12)
| Op | Code | Meaning |
|----|------|---------|
| `C` | 60 | Advance IX in `ix_vdir` — inverse: `D` |
| `D` | 61 | Retreat IX opposite `ix_vdir` — inverse: `C` |
| `O` | 62 | Flip `ix_vdir` via XOR 2 (N↔S, E↔W) — self-inverse |

Per-IP field `ix_vdir` (defaults to `DIR_S`). Used for vertical IX
movement in both sweep strategies. In v3 ping-pong: `C` advances IX
after horizontal boundary, `D O C` bounces at vertical boundaries.
In v4 rewind loop: `C` advances IX south from top boundary back into
the scan area after the rewind completes; `D` retreats IX during the
upward rewind loop. Boundary detection uses 0xFFFF cells (shown as
`~`), tested via `m T : ? ; T m` (`:` wraps payload 2047→0).

### Reversibility Pairs
- `+` / `-` are inverses
- `.` / `,` are inverses
- `m` is self-inverse (raw XOR)
- `r` / `l` are inverses
- `R` / `L` are inverses
- `:` / `;` are inverses
- `A` / `B` are inverses (IX horizontal momentum advance/retreat)
- `C` / `D` are inverses (IX vertical momentum advance/retreat)
- `H`/`h`, `a`/`d` are inverses (IX head movement)
- `N`/`S`, `E`/`W`, etc. are inverses (all head movement pairs)
- `X`, `F`, `G`, `T`, `K`, `Z`, `x`, `f`, `z`, `Y`, `I`, `j`, `V`, `U`, `O` are self-inverse

### Reversibility Invariants and NOP Guards (v1.13)

fb2d claims every state has a unique predecessor, inferred from state
alone (no history). `step_back()` is purely deductive: it looks behind
the IP to read the opcode, then undoes it. This works only if the grid
cell at the IP's previous position still contains the opcode that was
actually executed. Two classes of NOP guard protect this invariant:

**Head-overlap guards** (pre-v1.13): when two heads alias the same cell,
some operations become non-bijective (e.g., `[H0] ^= [H1]` with H0==H1
always gives 0, losing the original value). These are NOP when the
aliasing occurs:
- `x`, `.`, `,`, `Y`: NOP when H0 == H1
- `m`, `I`: NOP when H0 == IX
- `j`: NOP when IX == H0
- `F`: NOP when CL == H0 or CL == H1
- `f`: NOP when CL == H0 or CL == H1

**CL-overlap guards** (v1.14): ops that write [H0] using [CL] as a
parameter (rotation amount) are non-reversible when H0 == CL, because
the write changes the parameter that step_back would read. These are
NOP when H0 == CL:
- `R`, `L`: rotate [H0] by payload([CL])&15 — rotation changes the amount
- `Y`: [H0] ^= ror([H1], payload([CL])&15) — XOR changes the rotation amount

**IP-cell write guard** (v1.13): if a data op writes to the grid cell
the IP is currently sitting on, it changes the opcode that `step_back()`
will later read. step_back would then undo the wrong operation. Guard:
any grid write whose target address == the IP's flat position is NOP.
This applies to all data ops that modify grid cells (+, -, ., ,, X, F,
G, T, P, Q, Z, x, r, l, f, z, R, L, Y, :, ;, m, I, j, V). Head
movement ops and mirrors don't write to the grid, so they need no guard.

**G value guard** (v1.13): `G` (swap H1 register with grid[H0]) is NOP
when `grid[H0] >= grid_size`. Without this, the modulo clamp
`h1 % grid_size` loses information, making the swap irreversible.

**Why payload arithmetic is already bijective**: the Δp operations
(+, -, ., ,, :, ;, P, Q) work by extracting data bits directly from
fixed bit positions (DATA_MASK), NOT by guessing the nearest valid
codeword. The XOR flip pattern changes both the data bits and the
matching parity bits. Any error pattern in the parity bits is carried
through unchanged. This means `+` then `-` always restores the original
cell value — even on corrupted cells with multi-bit errors. Nearest-
codeword decoding is only used for opcode dispatch (which instruction
to execute), never for arithmetic.

**Multi-IP reversibility**: `step_back_all()` undoes IPs in reverse
order (last IP first). This ensures each IP's undo sees the grid state
that existed immediately after that IP's forward step — because all
later IPs have already been undone. The ordering is correct; cross-IP
head interference does not break reversibility.

## ifb Language (intermediate fuckbrain)

A Janus-like imperative language that compiles to fb2d:

```
var x = 5           // variable declaration with initial value
var y = 0
x += 3              // add constant
x -= 1              // subtract constant
x += y              // add variable
x -= y              // subtract variable
swap x y            // byte swap
zero x              // zero a variable (via EX swap)
while x do          // loop while x != 0
    ...
end
// Stream I/O:
input 10 13 11      // declare input byte sequence
read x              // [H0] += [H1] from stream
advance             // move stream pointer east
output x            // write to EX trail, zero var
```

## Current Limitations / Open Problems

1. **8-bit cell values**: all arithmetic is mod 256. Variables > 255 wrap.
   This makes the system an LBA, not truly TC.

2. **Unbounded loops**: the EX breadcrumb (`P`) wraps at 256 iterations,
   causing the loop entry check `(` to misfire. Nested loops mitigate this
   via monotonic EX advance, but single loops are still bounded.

3. **Carry arithmetic**: a spatial "carry corridor" pattern demonstrates
   unbounded increment using zero-terminated LE base-256 numbers
   (see `programs/carry-demo.py`). The IP walks through carry gadgets
   (`+ % E >` per digit); the zero terminator acts as a fresh digit on
   overflow. This is the path to true TC with 8-bit cells.

4. **ifb compiler nesting**: CompilerV2 handles nested while loops via
   single-code-row layout with corridor rows for each nesting level.
   Works for moderate depths but corridor space can be tight.

5. **Torus periodicity**: fb2d on a torus is always periodic — there is
   no halting condition. Loops must be designed so that re-sweeping
   already-corrected data is harmless. The Hamming gadget is a no-op on
   correct codewords (syndrome=0, no correction applied), but the dirty
   EX trail (PA, SYND cells) from previous passes must not pollute future
   passes' scratch space. This is the central design constraint for the
   self-correcting sweep architecture.

## Running Tests

```bash
# Low-EX-waste dual-gadget tests (★ main MWE — layout, cycle, correction):
python3 programs/immunity-gadgets-v5-low-waste.py

# Previous rewind-loop dual-gadget tests (v4, for comparison):
python3 programs/immunity-gadgets-v4-loop.py

# Sweep strategy comparison (analytical + Monte Carlo):
python3 programs/sweep-model.py

# Previous probe-bypass tests (ping-pong sweep, for comparison):
python3 programs/immunity-gadgets-v3-bypass.py

# Compiler tests (11 tests including factorial, nested loops, stream I/O):
python3 ifbc.py --test-all

# Reversible pool tests (waste cleanup + noise injection):
python3 test_pools.py

# Carry arithmetic demo (10 tests including multi-byte carry):
python3 programs/carry-demo.py

# Exhaustive reversibility test (all opcodes × all head aliasings):
python3 test_reversibility.py

# Interactive GUI (load immunity-gadgets-v4-loop-w99 for the main example):
python3 fb2d_server.py              # port 5001 (default)
python3 fb2d_server.py 5002         # run a second instance on a different port
```

## Self-Correcting Agent Architecture (Design Sketch)

The long-term goal: an agent on the torus that resists its own degradation
by noise. The architecture has several layers:

### Grid Layout (v4 Rewind Loop, per gadget, R+7 rows)

```
Row 0:        BOUNDARY ROW (0xFFFF cells, shown as ~ in GUI/REPL)
Row 1:        BYPASS ROW (NOP filler, bypass ops going West)
Row 2:        RETURN ROW (NOP filler, rewind loop return path)
Row 3:        HANDLER ROW (NOP filler, boundary handlers going East)
Rows 4..R+3:  CODE ROWS (boustrophedon, correction gadget)
Row R+4:      BOUNDARY ROW (0xFFFF cells, shown as ~)
Row R+5:      STOMACH (working area: 9 fixed cells for H0, H1, CL)
Row R+6:      WASTE ROW (EX roams East, eats zeros, deposits waste)
```

Two gadgets (A and B) stacked vertically. Each IP runs its own gadget's
code; each IP's IX scans the other gadget's bypass+return+handler+code
rows (R+3 scan rows). Boundary rows are 0xFFFF (payload 2047), shown
as `~` in the GUI — not a valid opcode, testable via `m T : ? ; T m`
(`:` wraps payload 2047→0, `?` fires on zero payload).

The agent corrects single-bit errors via Hamming(16,11) SECDED.
Each correction consumes ~2 clean waste cells (PA, signal).

### Sweep Strategies: Rewind Loop vs Ping-Pong

Two vertical sweep strategies have been implemented and compared:

**v3 Ping-pong** (`/ D O ; X \` handler, R+6 layout): IX bounces
between top and bottom, reversing direction at each boundary. Simple
(6-op handler) but creates non-uniform coverage: boundary rows wait
up to (2S−1) sweeps between corrections while interior rows wait ≤S.

**v4 Rewind loop** (`/ D & B D A m T : % ; T m C ; \` handler, R+7
layout): on hitting the bottom boundary, IX loops back to the top
row-by-row, then resumes downward. Uniform coverage: every row visited
every S sweeps. Costs 13 extra ops and one additional row (return row).

Quantitative comparison (`programs/sweep-model.py`):
- Worst-case gap: v3 = 160K steps (2S−1=11 sweeps), v4 = 120K steps
  (S=7 sweeps). v4 is 33% better on worst case.
- MTTF (mean time to first uncorrectable error): v4 is ~14% longer,
  robust across all noise rates (1e-9 to 1e-6) and grid widths.
- The rewind overhead (86 steps) is <0.1% of cycle time.
- Crossover: v4 wins up to +105 extra ops; current overhead is +13.
- The advantage approaches 2× for deeper gadgets (more code rows).

The rewind loop is strictly better for resilience. The `&` re-entry
gate uses the `;` signal convention: first entry has [CL]=0 (& is NOP,
transparent), return row sets [CL]≠0 via `;` so `&` fires on re-entry.

### Fuel → Zeros → Corrections

- Clean zeros are the fundamental resource. They power EX-based computation.
- A "compressor" gadget converts compressible fuel into clean zeros.
- Those zeros are swapped into the EX row ahead of the correction gadget.
- A reservoir of pre-made zeros IS already-compressed fuel — same thing.
- The system is viable as long as zero production rate ≥ consumption rate.

### Bootstrapping: Mutual Correction

A single Hamming gadget (~253 ops) can't correct itself — if any of its
opcodes is corrupted, the whole correction is wrong. Solution: **multiple
gadgets correcting each other**.

- **Minimum viable**: 2 gadgets, A sweeps B's code, B sweeps A's code.
  Works if errors are sparse enough that both aren't corrupted at once.
- **Longer-term**: 3+ components (A→B→C→A), each correcting + compressing
  for the next. Each has its own IP + heads.
- **Multiple IPs**: each gadget runs on its own IP with independent heads,
  executing simultaneously on the shared grid. Required for real-time
  mutual correction.

### 16-Bit Cells

Cells are 16-bit with systematic Hamming(16,11) SECDED:
- 11 data bits (payload) at DATA_MASK=0xFEE8 positions
- 5 parity bits at positions 0, 1, 2, 4, 8
- IP extracts payload → nearest-codeword opcode decoding ([11,6,4] code)
- 63 opcodes use 63 of 64 codewords; the 64th (payload 1017) is NOP filler
- Bit-level ops (r, l, R, L, Y, z, x) act on full 16 bits — needed by
  the correction gadget to access parity bit positions.
- Arithmetic ops (+, -, ., ,, :, ;) preserve the Hamming invariant via
  Δp parity fixup. Bijective on all 65536 values, not just valid codewords.

### IX Interoceptor and Copy-Down Architecture (v1.9)

For mutual correction, each gadget needs to read/correct the other's
code. Problem: H0 shuttles between the EX row and the data row 6 times
per cycle in the existing gadget — this doesn't scale when the target
code is on distant rows in a boustrophedon layout.

Solution: **copy-down pattern** using the IX interoceptor.
- IX is a programmable head that each gadget steers through the other's
  code cells (eventually with adaptive boundary detection).
- `m` copies [IX] to a local EX-row cell (since it's zero).
- All correction logic runs locally on the EX row (H0, H1, CL, EX).
- `m` uncomputes the local copy (XOR again with unchanged remote zeroes it).
- `j` writes the correction mask back: [IX] ^= [H0].
- `V` enables boundary detection: swap [CL] ↔ [IX] to test remote cells
  with conditional mirrors.

This means only IX touches remote rows; all other heads stay local.

### Adaptive Sweep Boundaries (Future)

Instead of hardcoding which rows each gadget sweeps, detect agent
boundaries adaptively by testing for N consecutive NOPs. IX probes ahead,
`V` swaps the cell value into CL for testing with `?`/`%`, then `V`
restores. Nest N deep for "N consecutive NOPs = boundary."

Open problem: on a non-zero-background grid (where agents earn energy by
metabolizing compressible non-zero data), NOP detection can't just test
for zero. Need a range test: "is payload > 56?" (above all valid opcodes).
A dedicated opcode-validity test may be needed, or a subtraction-based
range check using existing ops. For now, hardcode sweep ranges.

### Development Roadmap

1. ~~Upgrade to 16-bit cells with systematic Hamming(16,11) SECDED.~~ ✓
2. ~~Build Hamming(16,11) correction gadget (sliding-window).~~ ✓
3. ~~Add IX interoceptor for cross-gadget correction.~~ ✓
4. ~~Two gadgets correcting each other with hardcoded layout,
   using copy-down pattern (m/M/j ops via IX).~~ ✓
5. ~~Add multiple IP support to the simulator.~~ ✓
   Interleaved round-robin: `step_all()` steps each IP in order.
   Per-IP state: ip_row, ip_col, ip_dir, h0, h1, ix, cl, ex.
   Grid is shared. REPL: `ip`, `addip`, `rmip` commands.
5b. ~~IX vertical momentum (C/D/O) for bounded scanning.~~ ✓
    Originally ping-pong (`/ D O ; X \`), now upgraded to top-down
    rewind loop (`/ D & B D A m T : % ; T m C ; \`) in v4. Uniform
    sweep frequency — every row visited every S sweeps, no 2× edge
    exposure. 0xFFFF boundary rows (shown as `~`) replace blank rows.
    Boundary test: `m T : ? ; T m` (`:` wraps payload 2047→0).
    See `programs/sweep-model.py` for quantitative comparison.
6. ~~Simulated noise: verify mutual correction under random bit flips.~~ ✓
   Deterministic noise via NoisePool (seed-based, rate in flips/1M rounds).
   d_min=4 opcode encoding ensures single data-bit flips → NOP (not wrong
   opcodes). Noise restricted to code+handler rows, columns 1–(W-2) to
   avoid boundary columns.
6b. ~~Reversible waste cleanup for unbounded correction.~~ ✓
    WastePool provides virtual infinite zeros. Working-area rows are
    cleaned every round by swapping dirty cells into the pool. Fully
    reversible: `step_back()` restores dirty values from the pool (LIFO).
    Replaces the old "infinite-zeros cheat" which destroyed breadcrumbs.
    Sweep length: ~864 cycles per full IX down-up sweep at W=99.
7. ~~G opcode modulo clamp: NOP when `grid[H0] >= grid_size`.~~ ✓
   Removes the lossy `h1 % grid_size` clamp. G is now NOP when the cell
   value is too large to be a valid flat index, preserving reversibility.
   Same guard in step_back. Only used by ifbc compiler, not immunity gadgets.
7b. ~~IP-cell write guard: data ops NOP when write target == IP cell.~~ ✓
   Same principle as existing head-overlap NOP guards (H0==H1 → NOP):
   writing to the IP's instruction cell destroys info needed by step_back.
   Guard added to all grid-writing ops in both step() and step_back().
   Not triggered in normal gadget operation (heads on stomach, IP on code
   rows), but prevents irreversibility during cascading failures.
   Note: Δp operations (+, -, ., ,, :, ;, P, Q) were confirmed to be
   already bijective on ALL 65536 cell values because `_CELL_TO_PAYLOAD`
   extracts raw data bits (not nearest-codeword payloads). The error
   syndrome is preserved through arithmetic — no fix was needed.
8. **[NEXT]** Cross-gadget consultation for 2+-bit errors: when SECDED
   detects an uncorrectable error (syndrome≠0, p_all=0), consult the
   partner gadget's corresponding cell. If the partner copy is clean
   (syndrome=0), XOR it in as the correction. Natural extension toward
   replication — consult all cells = spawn a replica.
9. ~~Probe-bypass parity skip for clean-cell fast path.~~ ✓
   Checks overall parity before full correction. Clean cells (syndrome=0)
   branch to a 28-op bypass row; dirty cells get full 358-op Hamming
   correction. 57% step savings (v4). Key discoveries:
   (a) `;` not `:` for signaling — `:` (0→1) invisible to DATA_MASK
   since bit 0 is a parity position. `;` (0→0xFFFF) sets all bits.
   (b) Bypass must undo Phase A+B's 15 `:` increments (15 `;` ops).
   (c) NOP filler payload 1017: 64th codeword of [11,6,4] code, both
   1-bit and 2-bit data-error safe (all decode to NOP). Payload 15 was
   unsafe (8/11 single-bit flips → real opcodes N/n/e/w).
   (d) `z` swaps bit0 of [H0] with [H1], not [EX] (ISA doc corrected).
9b. ~~Top-down rewind loop for uniform sweep frequency.~~ ✓
   `programs/immunity-gadgets-v4-loop.py`. Replaces v3's ping-pong
   bounce with a rewind loop: on hitting bottom boundary, IX loops
   row-by-row back to top, then resumes downward. R+7 layout adds
   return row. 16-op handler: `/ D & B D A m T : % ; T m C ; \`.
   `&` re-entry gate: first entry CL=0 (NOP), return row sets CL≠0
   via `;` so `&` fires on subsequent iterations. `C` at end advances
   IX from top boundary back into scan area.
   Effectiveness: 33% better worst-case gap, ~14% longer MTTF,
   robust across all noise rates and grid widths. See sweep-model.py.
9c. ~~Low-EX-waste )P conditional advance.~~ ✓
   `programs/immunity-gadgets-v5-low-waste.py`. Replaces CL-based `&`
   merge gates and `T Z ]` waste deposits with EX-based `)` merges
   and `P` dirty-marking. Invariant: EX sits on a dirty cell in
   neutral state. Handler/bypass paths include `]` to advance EX
   to clean cell; `)` fires on [EX]==0 (S→E merge); `P` re-dirts.
   Non-handler path: `)` NOP (EX dirty), `P` harmlessly increments.
   Key changes from v4: horizontal handler `/ ] ; T m B C U \`
   (9 ops, includes test undo), rewind handler 17 ops using `(`
   re-entry with P-based signaling (`/ D ] ( B D A m T : % ; T m
   C ] \`): return row does `; T m P` instead of `; T m ;` — no CL
   accumulation across iterations, CWL stays clean, no T Z ] deposit
   needed at exit. Main merge `(` at col 2 (\ if [EX]!=0): bypass
   arrives South with dirty EX (fires S→E), correction arrives East
   with clean EX from Phase G (NOP). Bypass does zero EX consumption
   (just uncomputes stomach — PA=0 for clean cells). Phase G `+Z]+Z]`
   deposits both EV and PA; `+` before each Z bumps payload by 1 to
   guarantee non-zero trail cells (no blanks). PA MUST be deposited:
   PA=1 residual (from bit-0 errors) causes false-positive correction
   on next cycle that actively corrupts via j [IX]^=1.
   374 ops, W=100, 58% clean-path savings. Zero EX consumption on
   non-boundary non-correction steps.
   **P-wrapping**: for the full gadget, not a concern — Phase G / bypass
   resets EX each cycle (max payload ~4). For standalone boundary
   scanners (manual-boundary-low-garbage): with 2 P per cycle (even
   step), payload stays odd → never hits 0. Adding a 3rd P (coprime
   step 3) would visit all values including 0. Width < 1024 is safe
   for horizontal boundary resets (even payload wraps after ~1023 steps).
10. **[NEXT]** Compression: XOR-of-identical-pairs to replace infinite-
   zero reservoir with finite fuel. Two identical cells XOR to zero
   (fuel for EX). Reversible: the non-zero residual is waste.
11. Reversible noise injection (multibaker-map style): a stored iid
    string determines when to swap two random bits on the grid.
    Deterministic at micro-level (reversible), stochastic-looking at
    macro-level. Same mechanism could serve as reversible waste sink.
12. ~~Non-zero boundary cells: 0xFFFF as boundary marker.~~ ✓
    Payload 2047 (0xFFFF), shown as `~` in GUI/REPL. Not a valid opcode
    (decodes to NOP). Testable with `m T : ? ; T m` (`:` wraps payload
    2047→0, `?` fires on zero). Used in v4 rewind loop for IX boundary
    detection. Enables agents in non-zero soup where zero ≠ empty.
13. Adaptive sweep boundaries via IX + boundary cell probe.
14. Non-zero background: agents metabolize compressible data for energy.

## Design Decisions Log

- **x -> X rename (v1.6)**: byte-level swap promoted to uppercase `X`.
  Lowercase `x` now means XOR. This frees lowercase letters for bit ops.
- **Bit-level ops (v1.6)**: `x` (XOR), `r`/`l` (rotate), `f` (bit-0
  Fredkin), `z` (bit-0 H1 swap). Motivated by need for carry detection,
  LEB128 encoding, and future error correction.
- **GP = garbage pointer**: records "breadcrumbs" for reversibility.
  Loops use `( P ... %` pattern. Nested loops use monotonic GP advance.
- **Zero-terminated LE base-256**: the chosen multi-cell integer encoding.
  The zero terminator doubles as a growth digit on carry overflow.
- **H2 scan head (v1.9)**: programmable head for cross-gadget correction.
  Chosen over auto-boustrophedon head because: (a) on large grids, the
  scanning pattern should be in gadget code, not hardware; (b) agents need
  to detect boundaries adaptively, not assume a fixed sweep topology;
  (c) programmable H2 + `V` test bridge enables future boundary detection.
  Key insight: copy-down pattern (`m`/`j`) means only H2 touches
  remote rows, eliminating the H0 shuttle problem entirely.
- **Nearest-codeword payload decoding**: the d_min=4 opcode encoding
  ([11,6,4] linear code) is now used as an error-CORRECTING code, not
  just error-detecting. Each 11-bit payload within Hamming distance 1
  of a valid opcode codeword decodes to that opcode (not NOP). This
  means a single data-bit error in an opcode cell still executes the
  CORRECT opcode. Safety: 2-bit errors → NOP (guaranteed by d_min=4).
  Each opcode has a "neighborhood" of 12 payloads (1 center + 11
  single-bit neighbors). 672 of 2048 payloads decode to valid opcodes.
- **NOP filler = payload 1017**: the 64th (last unused) codeword of the
  [11,6,4] opcode code. As a true codeword, it has d_min=4 from all
  other codewords: all 1-bit AND 2-bit data errors still decode to NOP
  (0/55 two-bit pairs → real opcodes). Data-bit distance 8 from zero
  (robust edge detection). Previous choices were worse: payload 15
  (8/11 one-bit flips → real opcodes!), payload 1019 (1-bit safe but
  30/55 two-bit → real opcodes). Lesson: NOP filler must be a codeword
  of the opcode code, not just "in the correction ball."
- **`;` not `:` for merge-gate signaling**: `:` increments CL from 0 to
  1. Value 1 = bit 0 only. The `&` mirror tests `grid[CL] & DATA_MASK`,
  and bit 0 is a Hamming parity position NOT in DATA_MASK (0xFEE8). So
  `1 & 0xFEE8 = 0` and `&` never fires. `;` decrements 0→0xFFFF (all
  bits set), making `&` fire correctly.
- **Probe-bypass architecture**: check overall parity before expensive
  Hamming correction. Clean cells (95%+) take a short bypass path.
  The bypass must undo all CL increments from Phase A+B (15 `;` ops)
  plus one more `;` for merge-gate signaling. The "stomach" (working
  area) has 9 fixed cells; GP ("the mouth") roams the waste row.
- **0xFFFF boundary rows** (replacing blank rows): boundary rows use
  0xFFFF (payload 2047, shown as `~`). Tested via `m T : ? ; T m` —
  `:` wraps payload 2047→0, `?` fires on zero. Works in non-zero
  environments where zero cells are not reliably empty. Previous design
  used blank (zero) rows, which was fragile on non-zero backgrounds.
- **GP → EX, H2 → IX rename**: GP (garbage pointer) renamed to EX
  (exteroceptor) — the external-facing head that roams the environment.
  H2 (scan head) renamed to IX (interoceptor) — the internal-facing
  head that scans for and corrects errors. Both are two-character names
  matching H0, H1, CL convention. The biological metaphor: perception
  (EX) vs interoception (IX). Opcode chars unchanged.
- **Rewind loop vs ping-pong (v4)**: top-down rewind loop replaces
  ping-pong vertical bounce. Ping-pong's worst-case gap scales as
  (2S−1) sweeps (boundary rows wait nearly twice as long as interior).
  Rewind loop: every row has gap = S sweeps (uniform). Cost: 13 extra
  ops (373 vs 360), 1 extra scan row (return row), 28 more clean-path
  steps. Benefit: 33% shorter worst-case gap → ~14% longer MTTF.
  The `&` re-entry gate solved the first-vs-subsequent iteration
  problem: first entry has CL=0 so `&` is NOP; return row sets CL≠0.
  Crossover at +105 extra ops — well above current +13.
- **NOP guards for reversibility (v1.13)**: two new guard classes added.
  (1) IP-cell write guard: data ops that write to the IP's own
  instruction cell are NOP, preventing step_back from reading a modified
  opcode. Same principle as existing head-overlap guards. (2) G value
  guard: G is NOP when grid[H0] ≥ grid_size, removing the lossy modulo
  clamp. Also confirmed that Δp arithmetic (payload increment etc.) was
  already bijective on all 65536 cell values — it extracts raw data bits,
  not nearest-codeword payloads, so error patterns are preserved through
  arithmetic. The initial suspicion that Δp ops broke on corrupted cells
  was wrong.
- **CL-overlap NOP guards (v1.14)**: R, L, and Y use [CL] as a rotation
  parameter and write to [H0]. When H0==CL, the write changes the
  parameter that step_back reads, making the operation irreversible.
  Guard: R, L, Y are NOP when H0==CL. Discovered via snapshot-based
  round-trip testing at 262207 steps — the first step where head
  degradation caused H0 and CL to alias the same cell. Verified with
  2M-step round-trip (466 noise flips, 1588 cells changed, 0 diffs).

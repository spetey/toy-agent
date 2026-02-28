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
  heads (H0, H1, H2, CL, GP); the grid is shared.
- Mirrors (`/`, `\`) and conditional mirrors change IP direction
- Multiple heads (H0, H1, H2, CL, GP) per IP point into the grid for data access
- Code and data share the same surface (von Neumann architecture)

## Notation Convention

- `H0`, `H1`, `H2`, `CL`, `GP` = head *positions* (addresses)
- `[H0]`, `[H1]`, `[H2]`, `[CL]`, `[GP]` = *values* at those positions
- `H0++` = move head (e.g., East)
- `[H0]++` = increment cell value

## Files

### Core

- **`fb2d.py`** — The simulator. Interactive REPL with grid display,
  forward/reverse stepping, save/load. Run with `python3 fb2d.py`.
- **`ifbc.py`** — Compiler from "intermediate fuckbrain" (ifb) to fb2d
  grid files. Supports variables, arithmetic, nested while loops, swap,
  zero, stream I/O. Run tests: `python3 ifbc.py --test-all`
- **`programs/`** — Example .fb2d state files and .ifb source files.
  `load`/`save` in fb2d.py defaults to this directory.

### Documentation

- **`docs/tc_proof_sketch.md`** — Turing completeness proof sketch via
  counter machine simulation using Fredkin dispatch blocks.
- **`docs/nested-loops-notes.md`** — How nested loops work with monotonic
  GP consumption ("burning zeroes").
- **`docs/sams-ir-idea.text`** — Sam Eisenstat's instruction register idea
  for resolving ambiguity in the 1D predecessor.

### Historical

- **`old-files/1d-and-before/`** — Earlier 1D simulators and programs.
- **`old-files/2d-older/`** — Earlier 2D simulator iterations.
- **`old-files/ifbc-02.py`** — Previous compiler version.

## ISA Summary (v1.9, 56 opcodes + NOP)

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
| `H/h/a/d` | 49-52 | H2 move North/South/East/West |
| `^/v/>/< ` | 25-26,23-24 | CL move N/S/E/W |
| `{/}/]/[` | 32,31,29,30 | GP move N/S/E/W |

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
| `z` | 43 | swap(bit0 of [H0], bit0 of [GP]) |
| `R` | 44 | [H0] rotate right by ([CL]&15) bits — inverse: `L` |
| `L` | 45 | [H0] rotate left by ([CL]&15) bits — inverse: `R` |
| `Y` | 46 | [H0] ^= ror([H1], [CL]&15) — fused rotate-XOR, self-inverse |
| `:` | 47 | [CL]++ — inverse: `;` |
| `;` | 48 | [CL]-- — inverse: `:` |

### GP (Garbage Pointer) Ops
| Op | Code | Meaning |
|----|------|---------|
| `P` | 27 | [GP]++ — leave breadcrumb |
| `Q` | 28 | [GP]-- — erase breadcrumb |
| `(` | 34 | \ reflect if [GP] != 0 |
| `)` | 35 | \ reflect if [GP] == 0 |
| `$` | 37 | / reflect if [GP] != 0 |
| `#` | 36 | / reflect if [GP] == 0 |
| `K` | 33 | swap(CL_register, GP_register) |
| `Z` | 38 | swap([H0], [GP]) — byte-level GP swap |

### H2 (Scan Head) Ops (v1.9)
| Op | Code | Meaning |
|----|------|---------|
| `m` | 53 | [H0] ^= [H2] — raw 16-bit XOR (self-inverse, copy-in/uncompute) |
| `M` | 54 | payload(H0) -= payload(H2) — Δp payload subtract |
| `j` | 55 | [H2] ^= [H0] — write-back (raw 16-bit, self-inverse) |
| `V` | 56 | swap([CL], [H2]) — test bridge (self-inverse) |

H2 is a programmable scan head for cross-gadget correction.
In the dual-gadget architecture, each gadget's H2 points at the
other gadget's code cells. The copy-down pattern: `m` copies a
remote codeword to a local GP cell, correction runs locally,
then `j` writes the correction mask back to the remote cell.

### Reversibility Pairs
- `+` / `-` are inverses
- `.` / `,` are inverses
- `m` is self-inverse (raw XOR)
- `r` / `l` are inverses
- `R` / `L` are inverses
- `:` / `;` are inverses
- `H`/`h`, `a`/`d` are inverses (H2 head movement)
- `N`/`S`, `E`/`W`, etc. are inverses (all head movement pairs)
- `X`, `F`, `G`, `T`, `K`, `Z`, `x`, `f`, `z`, `Y`, `j`, `V` are self-inverse

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
zero x              // zero a variable (via GP swap)
while x do          // loop while x != 0
    ...
end
// Stream I/O:
input 10 13 11      // declare input byte sequence
read x              // [H0] += [H1] from stream
advance             // move stream pointer east
output x            // write to GP trail, zero var
```

## Current Limitations / Open Problems

1. **8-bit cell values**: all arithmetic is mod 256. Variables > 255 wrap.
   This makes the system an LBA, not truly TC.

2. **Unbounded loops**: the GP breadcrumb (`P`) wraps at 256 iterations,
   causing the loop entry check `(` to misfire. Nested loops mitigate this
   via monotonic GP advance, but single loops are still bounded.

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
   GP trail (PA, SYND cells) from previous passes must not pollute future
   passes' scratch space. This is the central design constraint for the
   self-correcting sweep architecture.

## Running Tests

```bash
# Compiler tests (11 tests including factorial, nested loops, stream I/O):
python3 ifbc.py --test-all

# Carry arithmetic demo (10 tests including multi-byte carry):
python3 programs/carry-demo.py

# Interactive simulator:
python3 fb2d.py
# Then: load factorial-03
```

## Self-Correcting Agent Architecture (Design Sketch)

The long-term goal: an agent on the torus that resists its own degradation
by noise. The architecture has several layers:

### Grid Layout

```
[FUEL rows: compressible data, consumed in-place leaving waste behind]
[AGENT rows: code (correction gadgets + loops) in boustrophedon layout]
[GP row(s): dirty trail behind GP ← GP ← clean zeros ahead]
```

The agent sweeps its own code and data, correcting single-bit errors via
Hamming SECDED. Each correction consumes ~2 clean GP cells (PA, SYND).

### Fuel → Zeros → Corrections

- Clean zeros are the fundamental resource. They power GP-based computation.
- A "compressor" gadget converts compressible fuel into clean zeros.
- Those zeros are swapped into the GP row ahead of the correction gadget.
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

### 16-Bit Cells (Planned)

Upgrade from 8-bit to 16-bit cells with systematic Hamming(16,11) SECDED:
- 11 data bits (payload): opcodes (0–48) + data values (0–2047)
- 5 parity bits: maintained by Δp fixup on arithmetic ops (+, -, etc.)
- IP reads `cell % 2048` as the opcode. Valid opcode → execute, else NOP.
- Bit-level ops (r, l, R, L, Y, z, x) act on full 16 bits — needed by
  the correction gadget to access parity bit positions.
- Arithmetic ops (+, -, ., ,, :, ;) preserve the Hamming invariant via
  Δp parity fixup. Bijective on all 65536 values, not just valid codewords.

### H2 Scan Head and Copy-Down Architecture (v1.9)

For mutual correction, each gadget needs to read/correct the other's
code. Problem: H0 shuttles between the GP row and the data row 6 times
per cycle in the existing gadget — this doesn't scale when the target
code is on distant rows in a boustrophedon layout.

Solution: **copy-down pattern** using the H2 scan head.
- H2 is a programmable head that each gadget steers through the other's
  code cells (eventually with adaptive boundary detection).
- `m` copies [H2] to a local GP-row cell (since it's zero).
- All correction logic runs locally on the GP row (H0, H1, CL, GP).
- `M` uncomputes the local copy (before writing back, so remote is still
  original and the subtraction cleanly zeroes).
- `j` writes the correction mask back: [H2] ^= [H0].
- `V` enables boundary detection: swap [CL] ↔ [H2] to test remote cells
  with conditional mirrors.

This means only H2 touches remote rows; all other heads stay local.

### Adaptive Sweep Boundaries (Future)

Instead of hardcoding which rows each gadget sweeps, detect agent
boundaries adaptively by testing for N consecutive NOPs. H2 probes ahead,
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
3. ~~Add H2 scan head for cross-gadget correction.~~ ✓
4. **[CURRENT]** Two gadgets correcting each other with hardcoded layout,
   using copy-down pattern (m/M/j ops via H2).
5. ~~Add multiple IP support to the simulator.~~ ✓
   Interleaved round-robin: `step_all()` steps each IP in order.
   Per-IP state: ip_row, ip_col, ip_dir, h0, h1, h2, cl, gp.
   Grid is shared. REPL: `ip`, `addip`, `rmip` commands.
6. Simulated noise: verify mutual correction under random bit flips.
   GUI noise injection with per-sweep rate. d_min=4 opcode encoding
   ensures single data-bit flips → NOP (not wrong opcodes).
7. Fast-path parity check gadget: multi-row layout where clean cells
   (parity=0) skip correction rows. Parity check = 36 ops; full
   correction = 323 ops. In W=48 boustrophedon: clean cell = 48 steps
   vs dirty cell = 336 steps → 5× faster sweep at 95% clean cells.
   Deferred until boustrophedon layout with agent border detection.
8. Add simple compression (XOR-of-identical-pairs) to replace infinite
   zero reservoir with finite fuel.
9. Adaptive sweep boundaries via H2 + V probe.
10. Non-zero background: agents metabolize compressible data for energy.

## Design Decisions Log

- **x -> X rename (v1.6)**: byte-level swap promoted to uppercase `X`.
  Lowercase `x` now means XOR. This frees lowercase letters for bit ops.
- **Bit-level ops (v1.6)**: `x` (XOR), `r`/`l` (rotate), `f` (bit-0
  Fredkin), `z` (bit-0 GP swap). Motivated by need for carry detection,
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
  Key insight: copy-down pattern (`m`/`M`/`j`) means only H2 touches
  remote rows, eliminating the H0 shuttle problem entirely.
- **Nearest-codeword payload decoding**: the d_min=4 opcode encoding
  ([11,6,4] linear code) is now used as an error-CORRECTING code, not
  just error-detecting. Each 11-bit payload within Hamming distance 1
  of a valid opcode codeword decodes to that opcode (not NOP). This
  means a single data-bit error in an opcode cell still executes the
  CORRECT opcode. Safety: 2-bit errors → NOP (guaranteed by d_min=4).
  Each opcode has a "neighborhood" of 12 payloads (1 center + 11
  single-bit neighbors). 672 of 2048 payloads decode to valid opcodes.

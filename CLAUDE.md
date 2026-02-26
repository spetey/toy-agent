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
- An instruction pointer (IP) moves on a toroidal grid
- Mirrors (`/`, `\`) and conditional mirrors change IP direction
- Multiple heads (H0, H1, CL, GP) point into the grid for data access
- Code and data share the same surface (von Neumann architecture)

## Notation Convention

- `H0`, `H1`, `CL`, `GP` = head *positions* (addresses)
- `[H0]`, `[H1]`, `[CL]`, `[GP]` = *values* at those positions
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

## ISA Summary (v1.8, 48 opcodes + NOP)

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

### Reversibility Pairs
- `+` / `-` are inverses
- `.` / `,` are inverses
- `r` / `l` are inverses
- `R` / `L` are inverses
- `:` / `;` are inverses
- `N`/`S`, `E`/`W`, etc. are inverses (all head movement pairs)
- `X`, `F`, `G`, `T`, `K`, `Z`, `x`, `f`, `z`, `Y` are self-inverse

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

### Adaptive Sweep Boundaries (Future)

Instead of hardcoding which rows each gadget sweeps, detect agent
boundaries adaptively by testing for N consecutive NOPs. Implementation:
nested conditional mirrors — each level moves H0 east, loads the cell
value into a testable head (CL via T), and tests with `?` (CL==0). If
all N tests pass (N consecutive NOPs), take the "reverse" path. If any
test fails, backtrack head movements and continue sweeping. N=3 or 4
should suffice.

Open problem: we currently have no "test for NOP" opcode — only ==0 and
!=0 tests. For 16-bit cells, NOP is any payload not in {1–48}, not just
zero. A range test or opcode-validity test may be needed as a new opcode.
For now, hardcode sweep ranges.

### Development Roadmap

1. Upgrade to 16-bit cells with systematic Hamming(16,11) SECDED, so that
   opcodes are Hamming-protected codewords.
2. Build new Hamming(16,11) correction gadget.
3. Add multiple IP support to the simulator.
4. Start with infinite zero reservoir (special GP row), get two correction
   gadgets correcting each other under simulated noise.
5. Add simple compression (XOR-of-identical-pairs) to replace infinite
   reservoir with finite fuel.
6. Explore adaptive sweep boundaries and learning/adaptation.

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

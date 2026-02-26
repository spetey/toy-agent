# fuckbrain 2D (fb2d)

A reversible, valid-everywhere, Turing-complete 2D programming language
and simulator — designed as the substrate for a self-correcting agent.

fb2d is the core of a "toy agent" that can resist its own degradation
by noise. It's based on Google's BFF from the
[Computational Life paper](https://arxiv.org/abs/2406.19108), which is
in turn based on brainfuck. (fuckbrain = reversible brainfuck.)

## Key Properties

- **Reversible**: every state has a unique predecessor, inferred from the
  current state alone. The simulator can step backward as easily as
  forward.
- **Valid everywhere**: every cell value is either a known opcode or NOP.
  Any grid state is a valid program. There are no syntax errors.
- **Turing-complete**: via counter machine simulation with Fredkin
  dispatch blocks. See `docs/tc_proof_sketch.md`.
- **16-bit Hamming-protected cells**: each cell is a 16-bit
  Hamming(16,11) SECDED codeword with 11 data bits and 5 parity bits.
  The IP reads the payload (data bits) as the opcode. Arithmetic ops
  automatically maintain the Hamming invariant.
- **Self-correcting**: a spatial gadget (336 opcodes) detects and
  corrects single-bit errors in any cell, consuming only 1 clean zero
  cell per correction.

## Quick Start

```bash
# Interactive simulator
python3 fb2d.py

# Inside the simulator:
#   load factorial        — load a program from programs/
#   run 1000              — run 1000 steps forward
#   back 1000             — run 1000 steps backward (perfectly reversed)
#   show                  — display the grid
#   help                  — full command reference

# Compile an ifb program to fb2d
python3 ifbc.py programs/factorial.ifb programs/factorial-out.fb2d

# Run compiler tests
python3 ifbc.py --test-all

# Run carry arithmetic demo
python3 programs/carry-demo.py

# Run Hamming correction gadget tests (barrel-shifter algorithm)
python3 programs/hamming-gadget-demo.py

# Generate a Hamming correction .fb2d program to step through:
python3 programs/make-hamming16.py 42 --error 5 --wrap 60
# Then: python3 fb2d.py → load hamming16-p42-err5-w60
```

## Architecture

An instruction pointer (IP) moves on a toroidal 2D grid. Mirrors (`/`,
`\`) and conditional mirrors change the IP's direction. Four heads point
into the grid for data access:

| Head | Purpose |
|------|---------|
| H0 | Primary data head |
| H1 | Secondary data head |
| CL | Condition latch (used by conditional mirrors and rotation amounts) |
| GP | Garbage pointer (breadcrumb trail for reversibility) |

Code and data share the same surface (von Neumann architecture). The
ISA has 48 opcodes (byte-level arithmetic, bit-level operations, head
movement, mirrors, and garbage-pointer operations). See `CLAUDE.md` for
the full ISA reference.

### 16-Bit Cells

Every cell is a 16-bit Hamming(16,11) SECDED codeword:

```
Bit: 15 14 13 12 11 10  9  8  7  6  5  4  3  2  1  0
     d10 d9 d8 d7 d6 d5 d4 p3 d3 d2 d1 p2 d0 p1 p0 p_all
```

- **Payload** (11 data bits): the opcode or data value (0-2047).
  The IP reads `payload(cell)` to determine the opcode.
- **Parity** (5 bits): Hamming check bits at positions 0, 1, 2, 4, 8.
  Maintained automatically by arithmetic ops (+, -, etc.).
- **Syndrome**: when a single bit flips, the 4-bit Hamming syndrome
  equals the position number (0-15) of the flipped bit, enabling
  direct correction.

### Hamming Correction Gadget

The barrel-shifter gadget corrects single-bit errors in 336 ops:

1. Compute overall parity and syndrome via Y (fused rotate-XOR)
2. Build a 1-hot correction mask using a barrel shifter (conditional
   rotation via paired `f` gates with `l`/`r`)
3. XOR the mask into the codeword to flip the bad bit back
4. Clean up to leave at most 1 dirty garbage cell

See `docs/barrel-shifter-correction.md` for a full walkthrough.

## ifb (intermediate fuckbrain)

A Janus-like imperative language that compiles to fb2d grid files:

```
var n = 5
var result = 1
var acc = 0
var count = 0

while n do
    count += n
    while count do
        acc += result
        count -= 1
    end
    swap acc result
    zero acc
    n -= 1
end
// result = 120 (5!)
```

## Project Structure

```
fb2d.py                          Simulator (interactive REPL, 16-bit cells)
fb2d_server.py                   WebSocket server for browser GUI
fb2d_gui.html                    Browser-based GUI simulator
ifbc.py                          ifb-to-fb2d compiler
programs/                        Example programs
  hamming-gadget-demo.py         Hamming(16,11) correction gadget + tests
  make-hamming16.py              Generate .fb2d files for Hamming demos
  hamming.py                     Hamming(16,11) encode/decode/inject library
  carry-demo.py                  Multi-cell carry arithmetic demo
  *.fb2d                         State files (loadable in simulator)
  *.ifb                          ifb source files
docs/                            Design documents
  barrel-shifter-correction.md   Barrel-shifter correction algorithm walkthrough
  tc_proof_sketch.md             Turing completeness proof sketch
  nested-loops-notes.md          Nested loop implementation notes
CLAUDE.md                        Detailed project context for AI assistants
```

## Status

This is active research software. The language design is stabilizing
around v1.8 (48 opcodes). Recent milestones:

- **16-bit Hamming-protected cells** with automatic parity maintenance
- **Barrel-shifter correction gadget** (336 ops, 1 dirty cell per
  correction) — the core primitive for a self-correcting agent
- **Standard-form Hamming(16,11)** where syndrome = bit position
- **Boustrophedon (serpentine) code layout** for compact grid programs

Next steps: multiple IPs for mutual correction, fuel/compression for
sustainable zero production, and adaptive sweep boundaries.

## License

Research use. License TBD.

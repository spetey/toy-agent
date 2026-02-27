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
- **Self-correcting**: a Hamming correction gadget (323 opcodes) detects
  and corrects single-bit errors in any cell via the H2 copy-down pattern.
  Two gadgets can correct each other's code simultaneously (mutual
  correction).

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

# Run mutual correction demo (two gadgets correcting each other)
python3 programs/mutual-correction-demo.py

# Run H2 copy-down correction gadget tests
python3 programs/dual-gadget-demo.py

# Run Hamming correction gadget tests (barrel-shifter algorithm)
python3 programs/hamming-gadget-demo.py
```

## Architecture

Multiple instruction pointers (IPs) move on a toroidal 2D grid,
interleaved round-robin. Mirrors (`/`, `\`) and conditional mirrors
change each IP's direction. Each IP has five independent heads:

| Head | Purpose |
|------|---------|
| H0 | Primary data head |
| H1 | Secondary data head |
| H2 | Scan head (for cross-gadget correction via copy-down pattern) |
| CL | Condition latch (used by conditional mirrors and rotation amounts) |
| GP | Garbage pointer (breadcrumb trail for reversibility) |

Code and data share the same surface (von Neumann architecture). The
ISA has 56 opcodes (byte-level arithmetic, bit-level operations, head
movement, mirrors, garbage-pointer operations, and H2 scan ops). See
[`docs/isa.md`](docs/isa.md) for the full ISA reference.

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

The H2 copy-down correction gadget (323 ops) corrects single-bit errors:

1. `m` copies a remote codeword to local GP scratch (via H2 scan head)
2. Compute overall parity and syndrome via Y (fused rotate-XOR)
3. Build a 1-hot correction mask using a barrel shifter
4. `m` uncomputes the local copy, `j` writes the correction mask back
5. All heads advance — ready for the next codeword

Only H2 touches remote data; all other heads stay local. This enables
**mutual correction**: two gadgets on separate IPs, each correcting the
other's code via H2.

See `docs/barrel-shifter-correction.md` for algorithm details.

## Browser GUI

A browser-based visual simulator with full stepping, editing, and
annotation support. Requires Flask (`pip install flask`).

```bash
python3 fb2d_server.py
# Open http://localhost:5000
```

**Features:**

- **Canvas grid display** with color-coded head markers: IP (red/orange),
  H0 (cyan), H1 (green), H2 (blue), CL (purple), GP (gold)
- **Multi-IP support**: add/remove IPs, click IP labels in the status bar
  to switch which IP's heads are highlighted
- **Stepping**: forward/back by 1 or 10 steps, play/pause with adjustable
  speed, reset to step 0
- **Navigation**: drag to pan, scroll wheel to zoom, fit-to-grid, follow-IP
  modes (center, edge, off)
- **Cell tooltips**: hover any cell to see its payload (decoded from the
  16-bit SECDED codeword), Hamming syndrome status, and opcode name
- **Edit mode** (`E`): click a cell to open the opcode picker (grouped by
  category), drag to select regions, copy/cut/paste/delete, save to file.
  Raw 16-bit values can also be set directly.
- **Annotations**: right-click a cell to add a note (shown with an orange
  dot); shift+drag to label a rectangular region
- **Keyboard shortcuts**: arrow keys (step), Space (play/pause), `R`
  (reset), `F` (fit), `+`/`-` (zoom), `E` (edit mode), `?` (help overlay)

The server wraps the same `FB2DSimulator` used by the CLI REPL, so both
interfaces operate on the same engine. Load any `.fb2d` file from the
dropdown to explore it visually.

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
fb2d_server.py                   Flask server for browser GUI
fb2d_gui.html                    Browser-based GUI simulator
ifbc.py                          ifb-to-fb2d compiler
programs/                        Example programs
  mutual-correction-demo.py      Two gadgets correcting each other (★)
  dual-gadget-demo.py            H2 copy-down correction gadget + tests (★)
  hamming-gadget-demo.py         Hamming(16,11) barrel-shifter gadget + tests
  make-hamming16.py              Generate .fb2d files for Hamming demos
  hamming.py                     Hamming(16,11) encode/decode/inject library
  carry-demo.py                  Multi-cell carry arithmetic demo
  *.fb2d                         State files (loadable in simulator)
  *.ifb                          ifb source files
docs/                            Design documents
  isa.md                         ISA reference (56 opcodes, v1.9)
  barrel-shifter-correction.md   Barrel-shifter correction algorithm walkthrough
  tc_proof_sketch.md             Turing completeness proof sketch
  nested-loops-notes.md          Nested loop implementation notes
CLAUDE.md                        Detailed project context for AI assistants
```

## Status

This is active research software. The language design is at v1.9
(56 opcodes). Recent milestones:

- **Mutual correction**: two identical Hamming gadgets on separate IPs,
  each correcting the other's code via H2 copy-down — the first proof
  of self-maintaining code
- **Multi-IP support**: interleaved round-robin execution with independent
  heads per IP, full reversibility
- **H2 scan head** (v1.9): 8 new opcodes for cross-gadget correction
  using the copy-down pattern (`m`/`j` for XOR copy-in / write-back)
- **16-bit Hamming-protected cells** with automatic parity maintenance
- **Standard-form Hamming(16,11)** where syndrome = bit position

### Key Programs to Explore

| Program | Load in GUI | Description |
|---------|-------------|-------------|
| `mutual-correction-demo` | 2 IPs, 4×325 | Two gadgets correcting each other's parity errors |
| `h2-correction-demo` | 1 IP, 8×64 | Single gadget correcting a corrupted codeword (boustrophedon) |
| `factorial-03` | 1 IP, 8×64 | Factorial computation (compiled from ifb) |

Next steps: noise injection experiments, data-bit error convergence,
fuel/compression for sustainable zero production.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

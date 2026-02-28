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
  Any grid state is a valid program. There are no syntax errors. A d_min=4
  opcode encoding ensures single-bit data errors decode to the correct
  opcode (not NOP or a wrong opcode).
- **Turing-complete**: via counter machine simulation with Fredkin
  dispatch blocks. See `docs/tc_proof_sketch.md`.
- **16-bit Hamming-protected cells**: each cell is a 16-bit
  Hamming(16,11) SECDED codeword with 11 data bits and 5 parity bits.
  The IP reads the payload (data bits) as the opcode. Arithmetic ops
  automatically maintain the Hamming invariant.
- **Self-correcting**: a Hamming correction gadget (323 opcodes) detects
  and corrects single-bit errors in any cell via the H2 copy-down pattern.
  Two gadgets can correct each other's code simultaneously (mutual
  correction). With nearest-codeword decoding and GP cleanup, the mutual
  correction demo sustains indefinitely at 1 random bit-flip per sweep.

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

#### d_min=4 Opcode Encoding

The 57 opcodes are mapped to 11-bit payloads using an [11,6,4] linear
code with minimum Hamming distance 4 between any pair of codewords.
This provides two layers of protection:

1. **Nearest-codeword decoding**: each opcode "owns" all payloads within
   Hamming distance 1 (12 payloads per opcode: 1 center + 11 single-bit
   neighbors). A single data-bit error executes the *correct* opcode,
   not NOP. 672 of 2048 payloads decode to valid opcodes.
2. **Safety guarantee**: 2-bit data errors always decode to NOP
   (guaranteed by d_min=4). Only 3+ bit errors can cause a wrong opcode.

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
- **Syndrome cell coloring**: yellow background = 1-bit error (correctable),
  red = 2-bit error (detected), green = non-zero GP row data (correction
  breadcrumbs). Makes error correction visually legible in real time.
- **Multi-IP support**: add/remove IPs, per-IP visibility toggles (click
  IP labels in the status bar)
- **Stepping**: forward/back by 1 step or by batch size, play/pause with
  separate batch and delay controls, reset to step 0
- **Noise injection** (`N`): Poisson-distributed random bit flips on code
  rows with configurable rate (errors per sweep), type (any/parity/data),
  and live stats. Enables testing error correction under realistic noise.
- **GP cleanup** (`G`): auto-zeros GP rows when the garbage pointer wraps,
  enabling indefinite correction sweeps without metabolism. A temporary
  cheat until fuel/compression is implemented.
- **Navigation**: drag to pan, scroll wheel to zoom, fit-to-grid, follow-IP
  modes (center, edge, off)
- **Cell tooltips**: hover any cell to see its payload (decoded from the
  16-bit SECDED codeword), Hamming syndrome status, and opcode name
- **Edit mode** (`E`): click a cell to open the opcode picker (grouped by
  category), drag to select regions, copy/cut/paste/delete, save to file.
  Raw 16-bit values can also be set directly.
- **Annotations**: right-click a cell to add a note (shown with an orange
  dot); shift+drag to label a rectangular region
- **Keyboard shortcuts**: arrow keys (step), Shift+arrows (step by batch),
  Space (play/pause), `R` (reset), `N` (noise), `G` (GP cleanup), `F`
  (fit), `+`/`-` (zoom), `E` (edit mode), `?` (help overlay)

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
programs/                        Example programs and analysis scripts
  mutual-correction-demo.py      Two gadgets correcting each other (★)
  dual-gadget-demo.py            H2 copy-down correction gadget + tests (★)
  hamming-gadget-demo.py         Hamming(16,11) barrel-shifter gadget + tests
  noise-injection-experiment.py  Programmatic noise resilience testing
  hamming-distance-d4-search.py  Search for d_min=4 opcode encodings
  hamming-distance-analysis.py   Analyze code properties and sphere packing
  cl-ordering-optimize.py        Exhaustive CL adjustment optimization
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

- **Noise resilience demonstrated**: with d_min=4 opcode encoding,
  nearest-codeword decoding, and GP cleanup, the mutual correction
  demo sustains indefinitely at 1 random bit-flip per sweep (~325
  columns). Tested stable for 28+ sweeps. At higher noise rates
  (~15 errors/sweep), the agent persists for extended periods before
  eventually cascading.
- **d_min=4 opcode encoding**: [11,6,4] linear code maps all 57 opcodes
  to payloads with minimum Hamming distance 4. Combined with
  nearest-codeword decoding (each opcode owns its radius-1 Hamming
  ball), a single data-bit error in an opcode cell executes the
  *correct* opcode — not NOP, not a wrong opcode.
- **Noise injection GUI**: Poisson-distributed random bit flips on code
  rows, configurable per-sweep rate, syndrome-colored cells (yellow =
  correctable error, red = detected double-bit, green = GP correction
  data). Makes error correction visually legible in real time.
- **Mutual correction**: two identical Hamming gadgets on separate IPs,
  each correcting the other's code via H2 copy-down — the first proof
  of self-maintaining code.
- **Multi-IP support**: interleaved round-robin execution with independent
  heads per IP, full reversibility.
- **H2 scan head** (v1.9): 8 new opcodes for cross-gadget correction
  using the copy-down pattern (`m`/`j` for XOR copy-in / write-back).
- **16-bit Hamming-protected cells** with automatic parity maintenance.

### Key Programs to Explore

| Program | Load in GUI | Description |
|---------|-------------|-------------|
| `mutual-correction-demo` | 2 IPs, 4×325 | **Start here.** Two gadgets correcting each other. Enable noise (`N`) + GP cleanup (`G`), set batch to 325, and watch errors get corrected in real time. |
| `h2-correction-demo` | 1 IP, 8×64 | Single gadget correcting a corrupted codeword (boustrophedon layout) |
| `factorial-03` | 1 IP, 8×64 | Factorial computation (compiled from ifb) |

### Recommended Demo: Mutual Correction Under Noise

```bash
python3 fb2d_server.py
# Open http://localhost:5000
# 1. Load "mutual-correction-demo" from the dropdown
# 2. Press N to enable noise injection (default: 1 error/sweep)
# 3. Press G to enable GP cleanup (auto-zeros garbage rows)
# 4. Set Batch to 325 (one full cycle per tick)
# 5. Press Space to play
# Watch: yellow cells appear (errors) → green GP data grows
#         (correction in progress) → yellow cells disappear (fixed!)
```

Next steps: fuel/compression for sustainable zero production (replacing
GP cleanup cheat), fast-path parity check gadget for faster sweeps,
adaptive sweep boundaries via H2 probe.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

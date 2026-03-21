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
  forward. NOP guards on all data ops prevent self-modification of the
  IP's instruction cell and head-aliasing edge cases that would break
  the bijection. See "Reversibility" below.
- **Valid everywhere**: every cell value is either a known opcode or NOP.
  Any grid state is a valid program. There are no syntax errors. A d_min=4
  opcode encoding ensures single-bit data errors decode to the correct
  opcode (not NOP or a wrong opcode).
- **Turing-complete**: via counter machine simulation with Fredkin
  dispatch blocks. See `docs/tc_proof_sketch.md`.
- **16-bit Hamming-protected cells**: each cell is a 16-bit
  Hamming(16,11) SECDED codeword with 11 data bits and 5 parity bits.
  Corrects 1-bit errors, detects 2-bit errors. The IP reads the payload
  (data bits) as the opcode. Arithmetic ops automatically maintain the
  Hamming invariant.
- **Self-correcting**: a Hamming correction gadget detects and corrects
  single-bit errors in any cell via the IX copy-down pattern. Two gadgets
  can correct each other's code simultaneously (mutual correction). The
  probe-bypass architecture adds a fast path: clean cells (parity=0) skip
  the full barrel-shifter correction, reducing per-cell cost. NOP filler
  cells use payload 1017 (the 64th codeword of the [11,6,4] code), which
  is immune to both 1-bit and 2-bit data errors.

## Quick Start

### GUI (recommended)

```bash
pip install flask          # one-time setup
python3 fb2d_server.py     # starts on http://localhost:5001
```

1. Load **immunity-gadgets-v4-loop-w99** from the dropdown
2. Enable waste cleanup (click the "Waste" button or press `W`)
3. Enable noise — press `N`, set rate to ~50 flips/1M rounds, seed 42
4. Press Space to play

Watch: IP0 (red) corrects gadget B's code via IX (blue), IP1 (orange)
corrects gadget A simultaneously. Clean cells (NOP filler `o`) take the
bypass shortcut. Dirty cells (yellow = 1-bit error) get full Hamming
correction. Boundary rows (`~` = 0xFFFF) mark the IX scan boundaries.
Green cells on the waste row show consumed zeros.

### Tests

```bash
# Immunity gadgets v4 — rewind-loop dual correction (★ start here)
python3 programs/immunity-gadgets-v4-loop.py

# Sweep strategy comparison — ping-pong vs rewind loop
python3 programs/sweep-model.py

# Compiler tests (16 tests: factorial, nested loops, stream I/O, reversal)
python3 ifbc.py --test-all

# Carry arithmetic demo (multi-byte increment with carry propagation)
python3 programs/carry-demo.py

# Reversible pool tests (waste cleanup + noise injection)
python3 test_pools.py

# Exhaustive reversibility proof (all opcodes × all head aliasings)
python3 test_reversibility.py
```

### CLI REPL

```bash
python3 fb2d.py

# Inside the simulator:
#   load immunity-gadgets-v4-loop-w99      — load the main demo
#   run 1000    — run 1000 steps forward
#   back 1000   — run 1000 steps backward (perfectly reversed)
#   show        — display the grid
#   help        — full command reference
```

## Architecture

Multiple instruction pointers (IPs) move on a toroidal 2D grid,
interleaved round-robin. Mirrors (`/`, `\`) and conditional mirrors
change each IP's direction. Each IP has five independent heads:

| Head | Purpose |
|------|---------|
| H0 | Primary data head |
| H1 | Secondary data head |
| IX | Interoceptor — scans remote cells for cross-gadget error correction via copy-down pattern |
| CL | Condition latch — tested by conditional mirrors, also controls rotation amounts |
| EX | Exteroceptor — the metabolism head. Roams the waste row consuming clean zeros to fuel computation. Also leaves breadcrumb trails that record the execution history for reversibility |

Code and data share the same surface (von Neumann architecture). The
ISA has 62 opcodes (byte-level arithmetic, bit-level operations, head
movement, mirrors, EX operations, IX scan ops, and IX momentum ops
for serpentine scanning with top-down rewind loop). See
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

#### Error Protection (two layers)

| Layer | Code | Corrects | Detects |
|-------|------|----------|---------|
| Cell level | Hamming(16,11) SECDED, d_min=3 | 1-bit errors in the 16-bit cell | 2-bit errors |
| Opcode level | [11,6,4] linear code, d_min=4 | 1 data-bit error in opcode identity | 2 data-bit errors (→ NOP) |

#### Special Cell Values

| Symbol | Payload | Raw | Purpose |
|--------|---------|-----|---------|
| `o` | 1017 | 0x7E8E | **NOP filler**. 64th codeword of [11,6,4] code. All 1-bit and 2-bit data errors decode to NOP (not a real opcode). Used for padding on code, bypass, return, and handler rows. |
| `~` | 2047 | 0xFFFF | **Boundary marker**. All bits set. Decodes to NOP. Used for IX scan boundary rows and columns. Detected via `m T : ? ; T m` (`:` wraps 2047→0). |

### Hamming Correction Gadget

The IX copy-down correction gadget corrects single-bit errors:

1. `m` copies a remote codeword to local EX scratch (via IX interoceptor)
2. Compute overall parity and syndrome via Y (fused rotate-XOR)
3. Build a 1-hot correction mask using a barrel shifter
4. `m` uncomputes the local copy, `j` writes the correction mask back
5. All heads advance — ready for the next codeword

Only IX touches remote data; all other heads stay local. This enables
**mutual correction**: two gadgets on separate IPs, each correcting the
other's code via IX.

The **probe-bypass** variant adds a fast path: before running the full
barrel-shifter, a parity probe tests whether the cell has any error at
all. Clean cells (overall parity = 0) branch to a bypass row that undoes
the probe and skips correction entirely. Only dirty cells pay the full
correction cost (~65% step savings).

See `docs/barrel-shifter-correction.md` for algorithm details.

### Reversibility

fb2d is reversible: `step_back()` reconstructs the previous state from
the current state alone, with no history log. It looks behind the IP to
read the opcode that was just executed, then undoes its effect.

This works only if certain invariants hold. Three classes of NOP guard
protect them:

- **Head-overlap guards**: when two heads alias the same cell, some
  operations become non-bijective (e.g., XOR of a cell with itself
  always gives zero). These ops are NOP when heads alias.
- **CL-overlap guards** (v1.14): ops that write [H0] using [CL] as a
  parameter (R, L, Y — rotation amount comes from [CL]) are NOP when
  H0 == CL. Otherwise the write changes the parameter that step_back
  would read, producing a different rotation amount on undo.
- **IP-cell write guard** (v1.13): if a data op would write to the grid
  cell the IP is sitting on, it's NOP. Otherwise it would change the
  opcode that step_back later reads, causing it to undo the wrong thing.
- **G value guard** (v1.13): the G opcode (indirect H1 addressing) is
  NOP when the cell value exceeds the grid size, preventing a lossy
  modulo clamp.

**Payload arithmetic is bijective on all 65536 cell values** — not just
valid codewords. The Δp operations (+, -, etc.) extract the raw data
bits directly from fixed bit positions, not via nearest-codeword
guessing. The XOR flip pattern changes both data and parity bits
together, so any error pattern in the parity bits is preserved through
arithmetic. Forward then backward always restores the original cell
value, even on corrupted cells with multi-bit errors.

**Multi-IP reversibility**: `step_back_all()` undoes IPs in reverse
order (last first). Each IP's undo sees the grid state that existed
right after its forward step, because all later IPs have already been
undone.

## Browser GUI

A browser-based visual simulator with full stepping, editing, and
annotation support. Requires Flask (`pip install flask`).

```bash
python3 fb2d_server.py
# Open http://localhost:5001
```

**Features:**

- **Canvas grid display** with color-coded head markers: IP (red/orange),
  H0 (cyan), H1 (green), IX (blue), CL (purple), EX (gold)
- **Syndrome cell coloring**: yellow background = 1-bit error (correctable),
  red = 2-bit error (detected), green = non-zero EX row data (correction
  waste). Makes error correction visually legible in real time.
- **Multi-IP support**: add/remove IPs, per-IP visibility toggles (click
  IP labels in the status bar)
- **Stepping**: forward/back by 1 step or by batch size, play/pause with
  separate batch and delay controls, reset to step 0
- **Noise injection** (`N`): random bit flips on code rows with
  configurable rate (flips per 1M rounds), type (any/parity/data), seed,
  and live stats
- **Waste cleanup** (`W`): reversible waste pool — zeros stomach cells
  after each round, storing dirty values for step_back. Enables
  indefinite correction sweeps.
- **Navigation**: drag to pan, scroll wheel to zoom, fit-to-grid, follow-IP
  modes (center, edge, off)
- **Cell tooltips**: hover any cell to see its payload, Hamming syndrome
  status, and opcode name
- **Edit mode** (`E`): click a cell to open the opcode picker (grouped by
  category), drag to select regions, copy/cut/paste/delete, save to file.
  Raw 16-bit values can also be set directly.
- **Annotations**: right-click a cell to add a note (shown with an orange
  dot); shift+drag to label a rectangular region
- **Keyboard shortcuts**: arrow keys (step), Shift+arrows (step by batch),
  Space (play/pause), `R` (reset), `N` (noise), `W` (waste cleanup), `F`
  (fit), `+`/`-` (zoom), `E` (edit mode), `?` (help overlay)

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
pools.py                         Reversible waste pool + noise pool
test_pools.py                    Pool tests (waste, noise, integration)
test_reversibility.py            Exhaustive opcode reversibility proof
programs/                        Example programs and demos
  immunity-gadgets-v4-loop.py        Rewind-loop dual gadgets + tests (★ MWE)
  immunity-gadgets-v4-loop-w99.fb2d  Loadable state file for v4 demo
  sweep-model.py                     Ping-pong vs rewind-loop comparison model
  immunity-gadgets-v3-bypass.py      Probe-bypass dual gadgets (ping-pong, prev.)
  immunity-gadgets-v3-bypass-w99.fb2d  Loadable state file for v3
  immunity-gadgets-v2-serpentine.py   Serpentine dual gadgets with IX momentum
  immunity-gadgets-v2-serpentine-w99.fb2d
  immunity-gadget-v1.py              Original mutual correction demo
  dual-gadget-demo.py               IX copy-down correction gadget + tests
  hamming-gadget-demo.py             Hamming(16,11) barrel-shifter gadget + tests
  hamming.py                         Hamming(16,11) encode/decode/inject library
  carry-demo.py                      Multi-cell carry arithmetic demo
  factorial.ifb / factorial.fb2d     Factorial (compiled from ifb)
  fibonacci.ifb / fibonacci.fb2d     Fibonacci (compiled from ifb)
docs/                            Design documents
  isa.md                         ISA reference (62 opcodes, v1.14)
  barrel-shifter-correction.md   Barrel-shifter correction algorithm walkthrough
  tc_proof_sketch.md             Turing completeness proof sketch
  nested-loops-notes.md          Nested loop implementation notes
CLAUDE.md                        Detailed project context for AI assistants
```

## Status

This is active research software. The language design is at v1.14
(62 opcodes + NOP). Recent milestones:

- **CL-overlap NOP guards** (v1.14): R, L, and Y use [CL] as a rotation
  parameter and write to [H0]. When H0==CL, the write changes the
  parameter step_back would read. Now NOP-guarded. Discovered via
  round-trip testing at ~262K steps — the first point where noise
  degradation caused H0 and CL to alias. Verified with 2M-step
  round-trip (466 noise flips, 0 diffs).
- **Reversibility NOP guards** (v1.13): IP-cell write guard and G value
  guard. Data ops that would write to the IP's instruction cell are NOP;
  G is NOP when the cell value exceeds grid size. Payload arithmetic is
  bijective on all 65536 cell values (corrupted or not).
- **Rewind-loop sweep** (v4): the main MWE. Replaces ping-pong with
  top-down rewind loop for uniform sweep frequency. Every scan row
  is corrected every S sweeps (no 2× boundary exposure). 16-op rewind
  handler with `&` re-entry gate. Quantitative analysis shows 33%
  shorter worst-case gap and ~14% longer MTTF vs ping-pong.
  See `programs/sweep-model.py`.
- **Probe-bypass fast-path correction**: dual self-correcting gadgets
  where clean cells skip the full barrel-shifter via a parity probe +
  EX-conditional branch. Per-gadget layout (R+7 rows):
  boundary → bypass → return → handler → code rows → boundary → stomach → waste.
  Boundary rows use 0xFFFF (shown as `~`). NOP filler uses payload 1017
  (2-bit data-error safe).
- **Reversible waste cleanup**: WastePool provides virtual infinite
  clean zeros via LIFO swap. Fully reversible — step_back restores
  dirty values from the pool.
- **IX momentum scanning** (v1.10-v1.12): horizontal (`A`/`B`/`U`) and
  vertical (`C`/`D`/`O`) momentum ops enable serpentine scanning with
  top-down rewind loop.
- **d_min=4 opcode encoding**: [11,6,4] linear code maps all 62 opcodes
  to payloads with minimum Hamming distance 4. Single data-bit errors
  execute the *correct* opcode — not NOP, not a wrong opcode.
- **Multi-IP support**: interleaved round-robin execution with
  independent heads per IP, full reversibility.

Next steps: cross-gadget consultation for double-bit errors, reversible
fuel/compression (replacing the EX cleanup cheat), adaptive sweep
boundaries via IX + 0xFFFF boundary cell probe.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

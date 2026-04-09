# The Wikivore: A Digital Deacon Autogen

A self-correcting, self-fueling agent that resists its own degradation
by noise on a 2D toroidal grid. Two mutually-correcting Hamming(16,11)
gadgets repair each other's code while a metabolism phase compresses
fuel into the zeros that power error correction.

The agent runs on **fb2d**, a reversible, Turing-complete,
valid-everywhere 2D programming language. fb2d is based on Google's BFF
from the [Computational Life paper](https://arxiv.org/abs/2406.19108),
which is in turn based on Brainfuck — hence the name (fb = "fuckbrain",
i.e. reversible Brainfuck; 2D for the toroidal grid).

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
- **Self-correcting + self-fueling**: two gadgets correct each other's
  code via the IX copy-down pattern, using `I` (syndrome inspect) and `V`
  (correction mask) opcodes — 147 ops per gadget. A metabolism phase
  compresses duplicate fuel runs into zeros via XOR, replacing the
  infinite zero reservoir with finite fuel. The agent resists noise
  while earning its own energy.

## Quick Start

### GUI (recommended)

```bash
pip install flask          # one-time setup
python3 fb2d_server.py     # starts on http://localhost:5001
```

1. Load **agent-v1-w89** from the dropdown (the self-fueling agent)
2. Click the **Food** button to enable the free-food cheat (auto-refill fuel)
3. Enable noise — press `N`, set rate to ~200 flips/1M rounds, seed 42
4. Press Space to play

Watch: two gadgets correct each other while metabolizing fuel. Each IP
runs correction code (boustrophedon rows), then drops south into
metabolism rows where EX walks east through fuel, XORing each cell
against a reference. Matches become zeros (fuel for correction);
mismatches trigger walk-back. The corridor returns the IP north to
correction code for the next cycle.

The **Food** button auto-refills fuel when the contiguous food stretch
drops below 2× bite size (default 15). It replaces garbage cells with
continuing food in the A/B/C/D rotation pattern, picking up where the
existing food left off.

For **immunity-only** testing: load **immunity-gadgets-v8-correction-mask-w88**
and enable waste cleanup (`W` key) instead of food.

### Tests

```bash
# Self-fueling agent (★ flagship — immunity + metabolism)
python3 programs/agent-v1.py

# Metabolism standalone tests (compression, walk-back, full cycle)
python3 programs/metabolism-v1.py

# Immunity gadgets v8 — I+V opcodes, 147-op gadget
python3 programs/immunity-gadgets-v8-correction-mask.py

# Compiler tests (16 tests: factorial, nested loops, stream I/O, reversal)
python3 ifbc.py --test-all

# Exhaustive reversibility proof (all opcodes × all head aliasings)
python3 test_reversibility.py

# Reversible pool tests (waste cleanup + noise injection)
python3 test_pools.py
```

### CLI REPL

```bash
python3 fb2d.py

# Inside the simulator:
#   load agent-v1-w89              — load the self-fueling agent
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
movement, mirrors, EX operations, IX scan/syndrome/correction ops, and
IX momentum ops for serpentine scanning with top-down rewind loop). See
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

The correction architecture has evolved through several versions:

**v5 (baseline, 374 ops)**: IX copy-down pattern with probe-bypass. `m`
copies a remote codeword locally, Y ops compute syndrome, a barrel
shifter builds the correction mask, `j` writes it back. Clean cells
(parity=0) bypass to save ~58% of steps. Low-waste EX design: 0 cells
per clean scan, 3 per correction.

**v7 (379 ops)**: Adds the `I` opcode (syndrome inspect). A pre-syndrome
filter `I T ?` tests [IX]'s integrity *before* copy-in. Clean cells
(syndrome=0 AND p_all=0) skip the entire correction pipeline in ~84
steps. 2-bit errors get copy-over correction via IX round-trip to the
gadget's own corresponding cell. 2.6× longer MTTF than v5 at 200
flips/1M noise.

**v8 (147 ops, ★ current)**: Adds the `V` opcode (correction mask).
Replaces Phase C (syndrome computation, ~60 ops) + Phase D (barrel
shifter, ~40 ops) + Phase C' (uncompute, ~60 ops) with a single `V`
that computes `1 << syndrome([IX])` — the exact correction mask. The
gadget is 61% smaller than v5, correction sweeps are 2× faster, and
MTTF is **4.8–6.8× longer** than v5 under noise.

| Version | Ops | Clean path | Dirty path | 2-bit fix | MTTF vs v5 |
|---------|-----|------------|------------|-----------|------------|
| v5 | 374 | 162 steps | 390 steps | No | 1× |
| v7 | 379 | 84 steps | 394 steps | Yes | 2.6× |
| v8 | 147 | 84 steps | 198 steps | Yes | 4.8–6.8× |

See [`docs/i-opcode-design.md`](docs/i-opcode-design.md) for the I and V
opcode design, and [`docs/isa.md`](docs/isa.md) for the full ISA reference.

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

## ifb (intermediate fb)

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
  agent-v1.py                       Self-fueling agent + hunger timer (★ flagship)
  agent-v1-w89.fb2d                 Loadable state: immunity + metabolism + hunger
  compare-agents-mttf.py            Empirical MTTF comparison under noise
  metabolism-v1.py                   Standalone metabolism loop tests
  metabolism-v1-manual.fb2d          Hand-built metabolism prototype
  immunity-gadgets-v8-correction-mask.py  V-opcode dual gadgets (147 ops)
  immunity-gadgets-v5-low-waste.py   Low-EX-waste dual gadgets (374 ops)
  dual-gadget-demo.py               IX copy-down correction + GadgetBuilder
  hamming.py                         Hamming(16,11) encode/decode/inject
  sweep-model.py                     Ping-pong vs rewind-loop comparison
  carry-demo.py                      Multi-cell carry arithmetic demo
  factorial.ifb / factorial.fb2d     Factorial (compiled from ifb)
docs/                            Design documents
  isa.md                         ISA reference (62 opcodes, v1.14)
  tc_proof_sketch.md             Turing completeness proof sketch
  theory-notes.md                Local vs global reversibility, thermodynamic analogies
CLAUDE.md                        Detailed project context for AI assistants
```

## Status

This is active research software. The language design is at v1.15
(62 opcodes + NOP). Recent milestones:

- **Self-fueling agent with hunger timer** (agent-v1, ★ current flagship):
  dual immunity gadget with XOR-based metabolism and periodic eating.
  Each gadget corrects the other's code via `I` (pre-syndrome filter)
  and `V` (correction mask), and fuels itself by compressing duplicate
  fuel runs into zeros. A hunger timer (HUNGER_PERIOD=300) triggers
  metabolism every 300 bypass cycles, preventing zero starvation at low
  noise rates. The countdown lives in DSL_S2; the bypass row includes
  T/I undo for pre-syndrome state cleanup. R+11 layout per gadget.
  Requires width ≥ 89 (code_left=4 for col 3 vertical NOP express lane).
  GUI "free food" cheat auto-refills fuel for indefinite testing.
- **V-opcode correction** (v8, 147 ops): replaces 160 ops of syndrome
  computation with a single `V` opcode. 4.8-6.8x longer MTTF than v5.
  Pre-syndrome filter (`I T ?`) lets 95% of cells bypass in ~84 steps.
  Copy-over row handles 2-bit errors via partner cell consultation.
- **Reversibility guards** (v1.13-v1.14): IP-cell write guard, G value
  guard, CL-overlap guard. Payload arithmetic bijective on all 65536
  cell values. Verified with 2M-step round-trip (466 noise flips, 0 diffs).
- **d_min=4 opcode encoding**: [11,6,4] linear code ensures single
  data-bit errors execute the correct opcode, not NOP or wrong opcode.
- **Multi-IP support**: interleaved round-robin with independent heads,
  full reversibility.

Next steps: adaptive sweep boundaries via IX + boundary cell probe,
agents in non-zero background environments.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

# The Journey to fb2d v1.9

A brief history of building a self-correcting reversible agent.

## Origins: Lattice Gas

The project started as an FHP-III lattice gas simulation — a hexagonal
lattice model of fluid dynamics. We added particle injection/absorption
for density gradients, von Karman vortex street experiments, and Numba
JIT acceleration (~400x speedup). The goal was exploring emergent
structure in simple physics, but it became clear that the more
interesting question was: can a digital agent resist its own degradation
by noise?

## fuckbrain 2D (v1.6)

We pivoted to a reversible esoteric language based on Google's BFF from
the Computational Life paper. The key properties were set from the start:

- **Reversible**: every state has a unique predecessor, deducible without
  history
- **Valid everywhere**: any byte is a valid opcode or NOP — no crashes
- **2D toroidal grid**: code and data share the same surface

v1.6 added bit-level ops (`x`, `r`, `l`, `f`, `z`) and the carry
arithmetic demo, which showed unbounded increment using spatial carry
corridors — the path to Turing completeness with 8-bit cells.

An investigation into prefix-free binary encodings confirmed that
reversible TC universally requires a quiescent (zeroed) background.
This isn't a limitation of fb2d — it's true of Bennett, Morita, Fredkin,
Janus, and every known reversible system.

## Fuel Demos

Three progressively more capable torus agents demonstrated the resource
model:

1. **fuel-demo**: XOR-compress identical fuel pairs to extract clean
   zeros, shuttle them to the GP trail
2. **fuel-demo-v2**: inner bounded loop powered by fuel-derived zeros
   (the "digestive system")
3. **fuel-demo-v3**: error-checking agent that rotates data bytes using
   fuel — the first step toward self-maintenance

Key insight: clean zeros are the fundamental resource. Fuel is
compressible data; compression produces zeros; zeros power GP-based
reversible computation.

## Hamming(8,4) Error Correction

The first error correction gadget operated on 8-bit cells. 592 test
cases covering no-error, single-bit correction, and double-bit detection,
all with full `step_back()` reversibility. This proved error correction
was feasible in fb2d's reversible instruction set.

## v1.8: Rotate-by-CL and Re-Entrant Gadget

Added variable-amount rotation ops (`R`, `L`, `Y`) and CL arithmetic
(`:`, `;`). These eliminated the need for constant cells on the data
row — the gadget could manipulate its own CL register inline. The
Hamming gadget became fully re-entrant: after correcting one codeword,
all scratch cells were clean and heads were positioned for the next.

## 16-Bit Cells (The Big Upgrade)

Every cell became a 16-bit Hamming(16,11) SECDED codeword: 11 data bits
(payload) + 5 parity bits. This was the single largest change to the
system.

- The IP reads `payload(cell)` as the opcode
- Arithmetic ops (`+`, `-`, `.`, `,`, `:`, `;`, `P`, `Q`) automatically
  maintain the Hamming parity invariant via XOR delta tables
- Bit-level ops (`r`, `l`, `x`, `f`, `z`, `R`, `L`, `Y`) act on all 16
  raw bits — needed by the correction gadget to access parity positions
- The barrel-shifter correction gadget (336 ops) uses paired `f` gates
  with rotations to build a 1-hot evidence mask for XOR correction

Standard-form Hamming was chosen (parity bits at positions 0, 1, 2, 4,
8) so that the syndrome value directly equals the bit position of a
single-bit error.

## Sliding-Slot Gadget and Boustrophedon Layout

The correction gadget was redesigned for a self-contained boustrophedon
(serpentine) code layout: code rows zigzag left-to-right then
right-to-left, connected by mirror columns. This eliminated dependence
on torus wrapping for the IP loop and enabled the gadget to process
adjacent codewords with a sliding GP scratch window that advances by 1
per cycle (322 ops/cycle).

## v1.9: H2 Scan Head and Copy-Down Architecture

The central problem of mutual correction: if gadget A needs to correct
gadget B's code, H0 would have to shuttle between A's GP row and B's
code rows — 6 round trips per correction cycle. This doesn't scale when
the target code is on distant rows.

Solution: the H2 scan head with 4 new opcodes:

- `m` (`[H0] ^= [H2]`): raw XOR copies a remote codeword to a local
  zero cell
- `M` (`payload(H0) -= payload(H2)`): uncomputes the local copy after
  correction
- `j` (`[H2] ^= [H0]`): writes the correction mask back to the remote
  cell
- `V` (`swap([CL], [H2])`): test bridge for future boundary detection

Critical design insight: `m` must be raw 16-bit XOR (not payload
arithmetic with parity re-encoding), because the correction gadget needs
to see the corrupted raw bits to compute the syndrome. Payload arithmetic
would re-encode with fresh parity, hiding the error.

With copy-down, only H2 touches remote rows. All other heads stay local
on the GP row.

## Multi-IP and Mutual Correction

The simulator gained interleaved round-robin multi-IP execution: each IP
has independent heads (H0, H1, H2, CL, GP) but shares the grid. Two
identical Hamming gadgets on separate IPs correct each other's code via
H2 copy-down.

Key finding: parity-bit errors (bits 0, 1, 2, 4, 8) are safe — they
change the Hamming codeword but not the opcode, so both IPs execute
correctly while fixing each other's parity. Data-bit errors that change
the opcode require the correction rate to outpace error accumulation.

## d_min=4 Encoding and Noise Resilience

The final layer of defense: opcode payloads were re-encoded using an
[11,6,4] linear code with minimum Hamming distance 4 between any two
valid payloads.

- 1-bit data error in an opcode cell: still executes the **correct**
  opcode (nearest-codeword decoding)
- 2-bit data error: becomes NOP (safe, guaranteed by d_min=4)
- 3-bit error: possibly wrong opcode (extremely rare)

Combined with Hamming SECDED correction, the system sustains ~1 error
per sweep indefinitely. Tested stable for 28+ sweeps under continuous
noise injection.

## v4: Rewind-Loop Sweep

The ping-pong vertical sweep (IX bounces top-to-bottom-to-top) had a
non-uniform coverage problem: boundary rows waited up to (2S−1) sweeps
between corrections while interior rows waited ≤S. The rewind-loop
replaces this with a top-down sweep: at the bottom boundary, IX loops
row-by-row back to the top, then resumes downward. Every row is visited
every S sweeps — uniform coverage. The `&` re-entry gate solved the
first-vs-subsequent iteration problem: first entry has CL=0 (NOP),
return row sets CL≠0 via `;`. Quantitative comparison: 33% shorter
worst-case gap, ~14% longer MTTF. The rewind overhead (86 steps) is
<0.1% of cycle time.

## v5: Low-EX-Waste Architecture

The critical insight for metabolism: every clean zero consumed by the
EX trail is a resource the agent must eventually earn by compressing
environmental data. In v4, even clean cells consumed EX zeros (for the
bypass Z deposit and merge signal). v5 eliminates this waste.

Key techniques:

1. **EX-dirty invariant**: EX sits on a dirty cell in neutral state.
   The main merge at col 2 uses `(` (\ if [EX]!=0): bypass arrives
   South with dirty EX (fires S→E), correction arrives East with clean
   EX from Phase G (NOP). Bypass just uncomputes the stomach — zero EX
   consumption.

2. **`(` rewind loop**: the return row signals re-entry via `P` (dirty
   the EX cell) instead of `;` (CL accumulation). No CL dirt
   accumulates across iterations, so no T Z ] deposit is needed at
   exit. CWL stays clean throughout. 17 ops (was 18 in v4).

3. **`+Z]+Z]` Phase G**: deposits both EV and PA waste. The `+` before
   each Z bumps payload by 1, guaranteeing non-zero trail cells — no
   blanks in the wake. PA must be deposited: a PA=1 residual from
   bit-0 errors causes Phase B z to transfer bit0 into EV on the next
   cycle, and j then writes [IX]^=1, actively corrupting a clean cell.

4. **Rolling waste cleanup**: the GUI server clears half the waste row
   at 90%/10% EX position thresholds instead of zeroing everything
   every step. The dirty trail is preserved between cleanups.

Net EX budget: 0 cells per clean scan, 3 per correction, 1-2 per
boundary. At typical error rates (~5% of cells), average consumption
is ~0.2 cells per scan step — down from ~2 in v4.

P-wrapping safety: with 2P per non-boundary cycle (even step from odd
start), the payload stays odd and never hits 0. Width < 1024 is safe.

## Where It Stands

The v5 low-waste architecture is the current proof of concept: two
agents on a shared grid, each correcting the other's code, resilient to
continuous noise, with near-zero resource consumption on clean cells.
What remains is closing the resource loop — metabolism, where the agent
earns its clean zeros by compressing environmental data into fuel.

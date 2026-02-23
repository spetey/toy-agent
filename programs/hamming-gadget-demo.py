#!/usr/bin/env python3
"""
hamming-gadget-demo.py — SECDED Hamming(8,4) as a spatial fb2d gadget.

Full spatial program on a toroidal grid. Computes syndrome AND corrects
in one forward pass, with full step_back() reversibility.

Uses Y (fused rotate-XOR, v1.7) for syndrome/parity computation,
R/L for the correction rotation, and : / ; (v1.8) for inline CL value
manipulation (no rotation constants needed on the data row).

ARCHITECTURE (3-row torus, v2 — re-entrant, with uncomputation):

  Row 0 (DATA):  CW          ← single cell! ready for another correction loop
  Row 1 (CODE):  [opcodes left-to-right ...]
  Row 2 (GP):    PA SYND S0 S1 S2 MASK FIX TEMP ROT
                  0   1   2  3  4   5   6   7    8

All scratch cells live on the GP row (assumed zero).
After correction: S0=S1=S2=TEMP=FIX=MASK=ROT = 0 (clean).
PA and SYND at cols 0,1 — dirty trail is contiguous at the slot start.
GP ends on SYND (col 1). ≤2 cells garbage per correction (≤16 bits).
When no error: 0 garbage. Bit-0 error: 1 dirty (PA only, SYND=0).

CL sits on ROT (col 8); its value is changed inline via : / ;.
FIX is created with + and cleaned with -. TEMP is used during syndrome
residual cleanup and cleared by XOR with SYND.

Y opcode: [H0] ^= ror([H1], [CL]&7)  — self-inverse.

ALGORITHM PHASES (all on CODE row, IP walks East):

  Phase 1 — SYNDROME (~38 ops):
    H0 on GP-row scratch cells, H1 on CW (row 0). CL on ROT.
    s2: Y with [CL]={4,5,6,7}
    s1: Y with [CL]={2,3,6,7}
    s0: Y with [CL]={1,3,5,7}

  Phase 2 — SYNDROME ASSEMBLY + UNCOMPUTE (~50 ops):
    z-extract bit0 of S2, S1, S0 into SYND (all on GP row).
    Y-uncompute S0/S1/S2 (re-XOR with unchanged CW, self-inverse).
    Clear residual bits: z-pack into TEMP, XOR TEMP with SYND → TEMP=0.

  Phase 3 — OVERALL PARITY (~18 ops):
    H0 on PA (GP row), H1 on CW. Y with [CL]={0,1,2,...,7}.

  Phase 4 — CORRECTION (~30 ops):
    Create FIX=1 via +. f MASK↔FIX (gated on PA bit0).
    Move H0 to CW, R, x, L, restore f, clean FIX with -.
    Recover ROT to 0 via ;.

  Phase 5 — RE-ENTRANCY EPILOGUE (~20 ops):
    Return H0 and H1 to (DATA_ROW, CW). Move GP to SYND (col 1).
    After this phase, heads are in canonical positions for re-entry:
      H0 = H1 = (DATA_ROW, CW)
      CL = (GP_ROW, ROT), value 0
      GP = (GP_ROW, SYND=1)  — last garbage byte

RE-ENTRANCY SLOT LAYOUT:
    Each correction cycle uses a 9-column "slot" (SLOT_WIDTH = ROT + 1).
    The codeword sits at slot_base on DATA_ROW; scratch cells occupy
    slot_base..slot_base+8 on GP_ROW. CW is directly above GP_S0.

      Slot 0: cols 0..8    CW at (DATA, 0),   scratch at (GP, 0..8)
      Slot 1: cols 9..17   CW at (DATA, 9),   scratch at (GP, 9..17)
      ...

    Between cycles, the outer loop advances all heads east by SLOT_WIDTH:
      H0, H1 += 9 east   (next codeword)
      GP += 8 east        (SYND(1) → next PA(9), skipping clean S0..ROT)
      CL += 9 east        (next ROT cell)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, inject_double_error, decode

# ── Data cell columns (row 0) ── (just the codeword!)
CW   = 0   # codeword

# ── GP row scratch cell columns (assumed zero) ──
# After gadget: S0=S1=S2=TEMP=FIX=MASK=ROT=0 (clean). PA,SYND dirty (≤2 cells garbage).
# PA and SYND at cols 0,1 — the first cells of each slot. GP starts at col 0
# and ends on SYND (col 1). Dirty trail is contiguous at the slot start.
# (PA always dirty when any error; SYND dirty only when syn≠0.)
GP_PA   = 0   # overall parity accumulator (DIRTY — garbage, always dirty on error)
GP_SYND = 1   # assembled syndrome 0-7 (DIRTY — garbage, but 0 when no syndrome)
GP_S0   = 2   # syndrome bit 0 accumulator (cleaned by uncompute)
GP_S1   = 3   # syndrome bit 1 accumulator (cleaned by uncompute)
GP_S2   = 4   # syndrome bit 2 accumulator (cleaned by uncompute)
GP_MASK = 5   # conditional XOR mask (cleaned by restore-f)
GP_FIX  = 6   # FIX cell (created with +, cleaned with -)
GP_TEMP = 7   # temp for residual bit packing (cleaned by XOR with SYND)
ROT     = 8   # CL rotation counter (starts 0, recovered to 0)

DATA_ROW  = 0
CODE_ROW  = 1
GP_ROW    = 2
N_ROWS    = 3
N_DATA    = 1   # just CW on data row


class GadgetBuilder:
    """Build an opcode sequence and track head positions.

    Uses Y (fused rotate-XOR) for efficient bit-position XOR accumulation.
    CL value is manipulated inline via : and ; — no constant cells needed.
    Tracks both row and col for H0 (since it moves between data and GP rows).
    """

    def __init__(self, h0_row=DATA_ROW, h0_col=CW,
                 h1_row=DATA_ROW, h1_col=CW,
                 n_rows=3):
        self.ops = []           # list of opchar strings
        self.cursor = 0         # current column on CODE_ROW
        self.h0_row = h0_row
        self.h0_col = h0_col
        self.h1_row = h1_row
        self.h1_col = h1_col
        self.cl_val = 0         # tracked CL value (not position — CL stays on ROT)
        self.gp_col = 0         # GP column on GP row
        self.n_rows = n_rows

    def emit(self, opchar):
        """Emit an opcode at the current cursor position."""
        self.ops.append(opchar)
        self.cursor += 1

    def emit_n(self, opchar, n):
        """Emit an opcode n times."""
        for _ in range(n):
            self.emit(opchar)

    def pos(self):
        """Current cursor column."""
        return self.cursor

    def move_h0_col(self, target_col):
        """Move H0 east/west to target column (same row)."""
        diff = target_col - self.h0_col
        for _ in range(abs(diff)):
            self.emit('E' if diff > 0 else 'W')
        self.h0_col = target_col

    def move_h0_row(self, target_row):
        """Move H0 north/south to target row (toroidal, pick shorter path)."""
        if target_row == self.h0_row:
            return
        diff = (target_row - self.h0_row) % self.n_rows
        if diff <= self.n_rows // 2:
            self.emit_n('S', diff)
        else:
            self.emit_n('N', self.n_rows - diff)
        self.h0_row = target_row

    def move_h0(self, target_row, target_col):
        """Move H0 to (target_row, target_col)."""
        self.move_h0_row(target_row)
        self.move_h0_col(target_col)

    def move_h1_col(self, target_col):
        diff = target_col - self.h1_col
        for _ in range(abs(diff)):
            self.emit('e' if diff > 0 else 'w')
        self.h1_col = target_col

    def move_h1_row(self, target_row):
        if target_row == self.h1_row:
            return
        diff = (target_row - self.h1_row) % self.n_rows
        if diff <= self.n_rows // 2:
            self.emit_n('s', diff)
        else:
            self.emit_n('n', self.n_rows - diff)
        self.h1_row = target_row

    def move_h1(self, target_row, target_col):
        """Move H1 to (target_row, target_col)."""
        self.move_h1_row(target_row)
        self.move_h1_col(target_col)

    def move_gp_col(self, target_col):
        diff = target_col - self.gp_col
        for _ in range(abs(diff)):
            self.emit(']' if diff > 0 else '[')
        self.gp_col = target_col

    def set_cl_val(self, target):
        """Adjust [CL] to target value via : and ; ops."""
        diff = target - self.cl_val
        if diff > 0:
            self.emit_n(':', diff)
        elif diff < 0:
            self.emit_n(';', -diff)
        self.cl_val = target

    def xor_accumulate_bits(self, bit_positions):
        """XOR specific bit positions of [H1] into [H0] via Y.

        Uses : and ; to set CL value for each rotation amount.
        H0 and H1 must already be positioned.
        """
        for bit_pos in bit_positions:
            self.set_cl_val(bit_pos)
            self.emit('Y')


def build_gadget(gp_distance=2, n_rows=3):
    """Build the complete Hamming SECDED gadget (v2 — re-entrant, uncompute).

    Scratch cells on GP row, CL value via :/ ;, no constants on data row.
    Data row has only CW. S0/S1/S2 are uncomputed after syndrome assembly,
    leaving only PA and SYND as garbage (2 cells = 16 bits per correction).

    Args:
        gp_distance: how many rows south from DATA_ROW to GP_ROW.
            Default 2 (DATA=0, CODE=1, GP=2).
        n_rows: total grid rows (for toroidal shortcuts).

    Returns: (code_ops, total_cols, end_col)
        code_ops: list of opchar strings
        total_cols: minimum grid width needed
        end_col: column after last opcode (termination column)
    """
    gb = GadgetBuilder(n_rows=n_rows)
    gp_row_idx = gp_distance

    # ── Phase 1: Syndrome computation using Y ──
    # H0 on GP-row scratch cells, H1 on CW (row 0), CL on ROT.
    gb.move_h0(gp_row_idx, GP_S2)

    # s2 = XOR of bits {4,5,6,7}
    gb.xor_accumulate_bits([4, 5, 6, 7])

    # s1 = XOR of bits {2,3,6,7}
    gb.move_h0_col(GP_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7])

    # s0 = XOR of bits {1,3,5,7}
    gb.move_h0_col(GP_S0)
    gb.xor_accumulate_bits([1, 3, 5, 7])

    # ── Phase 2: Syndrome assembly via z-extraction ──
    gb.move_gp_col(GP_SYND)

    gb.move_h0_col(GP_S2)
    gb.emit('z')              # SYND.bit0 = s2

    gb.move_h0_col(GP_SYND)
    gb.emit('l')              # rotate SYND left
    gb.move_h0_col(GP_S1)
    gb.emit('z')              # SYND.bit0 = s1

    gb.move_h0_col(GP_SYND)
    gb.emit('l')              # rotate SYND left
    gb.move_h0_col(GP_S0)
    gb.emit('z')              # SYND.bit0 = s0

    # SYND = s0 | s1<<1 | s2<<2 (clean 0-7)
    # S0/S1/S2 each have bit0 replaced by 0 (the old SYND bits, which were 0)

    # ── Phase 2b: Y-uncompute S0/S1/S2 ──
    # Re-run the same Y ops. Since Y is self-inverse and CW hasn't changed,
    # all bits EXCEPT bit0 clear to 0. Bit0 = the original syndrome bit
    # (because bit0 was modified by z before uncomputation).
    #
    # After uncompute: S2.bit0=s2 (rest 0), S1.bit0=s1 (rest 0), S0.bit0=s0 (rest 0)

    # Uncompute S0 (currently at H0=GP_S0)
    gb.xor_accumulate_bits([1, 3, 5, 7])      # same bits as phase 1

    # Uncompute S1
    gb.move_h0_col(GP_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7])

    # Uncompute S2
    gb.move_h0_col(GP_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7])

    # ── Phase 2c: Clear residual bits from S0/S1/S2 ──
    # Each S cell has 1 dirty bit (bit0 = syndrome bit). Pack all 3 into
    # TEMP via z + rotate, then XOR TEMP with SYND to clear TEMP.
    #
    # Strategy: z each S cell's bit0 into TEMP, rotating between each
    # to place bits at positions 0,1,2. Result: TEMP = s0|s1<<1|s2<<2 = SYND.
    # Then x(TEMP, SYND) clears TEMP to 0.

    gb.move_gp_col(GP_TEMP)    # GP → TEMP cell

    # S2.bit0 = s2 → TEMP
    # H0 already on GP_S2
    gb.emit('z')              # TEMP.bit0 = s2, S2.bit0 = 0 → S2 = 0 ✓
    gb.move_h0_col(GP_TEMP)
    gb.emit('l')              # TEMP: s2 → bit1, bit0 = 0

    # S1.bit0 = s1 → TEMP
    gb.move_h0_col(GP_S1)
    gb.emit('z')              # TEMP.bit0 = s1, S1.bit0 = 0 → S1 = 0 ✓
    gb.move_h0_col(GP_TEMP)
    gb.emit('l')              # TEMP: s1 → bit1, s2 → bit2, bit0 = 0

    # S0.bit0 = s0 → TEMP
    gb.move_h0_col(GP_S0)
    gb.emit('z')              # TEMP.bit0 = s0, S0.bit0 = 0 → S0 = 0 ✓
    # TEMP = s0 | s1<<1 | s2<<2 = same as SYND

    # XOR TEMP with SYND to clear TEMP
    gb.move_h0_col(GP_TEMP)
    gb.move_h1_row(gp_row_idx)
    gb.move_h1_col(GP_SYND)
    gb.emit('x')              # TEMP ^= SYND → TEMP = 0 ✓

    # S0=S1=S2=TEMP=0. Only SYND remains dirty.

    # ── Phase 3: Overall parity using Y ──
    gb.move_h0_col(GP_PA)
    gb.move_h1_row(DATA_ROW)
    gb.move_h1_col(CW)
    gb.xor_accumulate_bits([0, 1, 2, 3, 4, 5, 6, 7])

    # ── Phase 4: Correction using R/L ──
    # Create FIX = 1
    gb.move_h0_col(GP_FIX)
    gb.emit('+')              # FIX = 1

    # Fredkin gate: MASK↔FIX gated on PA.bit0
    # Move CL from ROT to PA for f gate
    gb.emit_n('<', ROT - GP_PA)  # CL: ROT(8) → PA(4)

    gb.move_h0_col(GP_MASK)
    gb.move_h1_row(gp_row_idx)
    gb.move_h1_col(GP_FIX)
    gb.emit('f')              # if PA&1: swap MASK↔FIX

    # Move CL to SYND for R/L rotation (SYND is right of PA)
    gb.emit_n('>', GP_SYND - GP_PA)  # CL: PA(0) → SYND(1)

    # Move H0 to CW for rotation
    gb.move_h0(DATA_ROW, CW)

    gb.emit('R')              # ror(CW, SYND)

    # XOR to flip bit 0 (conditional on MASK)
    gb.move_h1(gp_row_idx, GP_MASK)
    gb.emit('x')              # CW ^= MASK

    # Un-rotate CW
    gb.emit('L')              # rol(CW, SYND)

    # Restore Fredkin gate — move CL back to PA (left of SYND)
    gb.emit_n('<', GP_SYND - GP_PA)  # CL: SYND(1) → PA(0)

    gb.move_h0(gp_row_idx, GP_MASK)
    gb.move_h1_col(GP_FIX)
    gb.emit('f')              # restore: MASK↔FIX

    # Clean FIX
    gb.move_h0_col(GP_FIX)
    gb.emit('-')              # FIX = 0 ✓

    # Move CL back to ROT
    gb.emit_n('>', ROT - GP_PA)  # CL: PA(4) → ROT(8)

    # Recover ROT value to 0
    gb.set_cl_val(0)          # ; × 7 to recover ROT = 0

    # ── Phase 5: Re-entrancy epilogue ──
    # Return H0 and H1 to (DATA_ROW, CW) so the gadget can be re-entered.
    # Park GP on SYND (col 1) — the last of the two garbage cells at the
    # slot start. PA(0) and SYND(1) form a contiguous dirty trail.
    # The outer loop advances GP past the remaining clean scratch cells
    # (S0..ROT at cols 2-8) to fresh zeros for the next correction cycle.

    # H0: (GP_ROW, GP_FIX=6) → (DATA_ROW, CW=0)
    gb.move_h0(DATA_ROW, CW)

    # H1: (GP_ROW, GP_FIX=6) → (DATA_ROW, CW=0)
    gb.move_h1(DATA_ROW, CW)

    # GP: GP_TEMP(col 7) → GP_SYND(col 1) — last garbage byte
    gb.move_gp_col(GP_SYND)

    # Final state:
    #   H0 = (DATA_ROW, CW)     — ready for next codeword
    #   H1 = (DATA_ROW, CW)     — ready for next codeword
    #   CL = (GP_ROW, ROT), value 0   — outer loop moves CL to next ROT
    #   GP = (GP_ROW, SYND=1)   — last garbage byte

    end_col = gb.pos()
    total_cols = gb.pos() + 2
    return gb.ops, total_cols, end_col


def make_hamming_gadget(codeword, wrap_width=None):
    """Build a torus with the Hamming SECDED gadget (v2 — re-entrant).

    Data row has only CW. All scratch on GP row. CL on GP row.

    If wrap_width is None, uses a single-row layout (3 rows total).
    If wrap_width is given, wraps the code into that width using
    boustrophedon (serpentine) mirrors.
    """
    if wrap_width is None:
        # Single-row layout
        code_ops, min_cols, end_col = build_gadget(
            gp_distance=2, n_rows=N_ROWS)
        op_values = [OP[ch] for ch in code_ops]

        cols = max(ROT + 2, min_cols)  # ensure GP row has room for all scratch
        sim = FB2DSimulator(rows=N_ROWS, cols=cols)

        # Place code row opcodes
        for i, opchar in enumerate(code_ops):
            sim.grid[sim._to_flat(CODE_ROW, i)] = OP[opchar]

        gp_row = GP_ROW
        sim._wrap_end_row = CODE_ROW
        sim._wrap_end_col = end_col
        sim._wrap_end_dir = 1  # East

    else:
        # Wrapped layout: iterate because gp_distance depends on code size
        cols = wrap_width
        gp_dist = 2
        n_rows = N_ROWS

        for _ in range(5):  # converges in 1-2 iterations
            code_ops, _, _ = build_gadget(
                gp_distance=gp_dist, n_rows=n_rows)
            op_values = [OP[ch] for ch in code_ops]

            # Compute rows needed for wrapping
            first_row_slots = cols - 1
            remaining = len(op_values) - first_row_slots
            if remaining <= 0:
                code_rows = 1
            else:
                code_rows = 1 + -(-remaining // (cols - 2))
            new_n_rows = 1 + code_rows + 1
            new_gp_dist = new_n_rows - 1

            if new_gp_dist == gp_dist and new_n_rows == n_rows:
                break
            gp_dist = new_gp_dist
            n_rows = new_n_rows

        gp_row = n_rows - 1
        sim = FB2DSimulator(rows=n_rows, cols=cols)

        rows_used, end_row, last_op_col, end_dir = sim.wrap_code(
            op_values, cols, start_row=CODE_ROW, start_col=0
        )

        if end_dir == 1:    # East
            term_col = last_op_col + 1
        else:               # West
            term_col = last_op_col - 1

        sim._wrap_end_row = end_row
        sim._wrap_end_col = term_col
        sim._wrap_end_dir = end_dir

    # Place codeword on row 0 (only data cell!)
    sim.grid[sim._to_flat(DATA_ROW, CW)] = codeword

    # Initial head positions
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, CW)
    sim.h1 = sim._to_flat(DATA_ROW, CW)
    sim.cl = sim._to_flat(gp_row, ROT)     # CL on ROT cell (GP row, col 8)
    sim.gp = sim._to_flat(gp_row, 0)

    return sim


def run_gadget(codeword, verbose=False, wrap_width=None):
    """Run the Hamming gadget on a codeword.

    Returns: (result_cw, ref_syndrome, ref_p_all, forward_steps, reverse_ok)
    """
    sim = make_hamming_gadget(codeword, wrap_width=wrap_width)

    end_row = sim._wrap_end_row
    end_col = sim._wrap_end_col
    end_dir = sim._wrap_end_dir

    max_steps = 5000
    for _ in range(max_steps):
        if (sim.ip_row == end_row and sim.ip_col == end_col
                and sim.ip_dir == end_dir):
            break
        sim.step()
    else:
        if verbose:
            print(f"  TIMEOUT at step {sim.step_count},"
                  f" IP=({sim.ip_row},{sim.ip_col}) dir={sim.ip_dir}")
        return codeword, -1, -1, max_steps, False, False

    forward_steps = sim.step_count
    result_cw = sim.grid[sim._to_flat(DATA_ROW, CW)]

    # Check re-entrancy head positions
    gp_row = sim.rows - 1
    reentrant_ok = check_reentrant(sim, gp_row, verbose=verbose)

    # Reference syndrome for reporting
    _, ref_syn, ref_p_all, _ = decode(codeword)

    # Reverse all steps
    for _ in range(forward_steps):
        sim.step_back()

    # Verify full restoration — data row has only CW
    reverse_ok = (
        sim.grid[sim._to_flat(DATA_ROW, CW)] == codeword
    )

    # GP row scratch cells should be back to 0 after reversal
    for col in range(ROT + 1):
        if sim.grid[sim._to_flat(gp_row, col)] != 0:
            reverse_ok = False

    if verbose and not reverse_ok:
        print(f"    [WARN] Reverse failed:")
        print(f"      CW={sim.grid[sim._to_flat(DATA_ROW, CW)]}"
              f" (expected {codeword})")
        gp_names = ['PA', 'SYND', 'S0', 'S1', 'S2', 'MASK', 'FIX', 'TEMP', 'ROT']
        for i, name in enumerate(gp_names):
            val = sim.grid[sim._to_flat(gp_row, i)]
            print(f"      GP.{name}={val}")

    return result_cw, ref_syn, ref_p_all, forward_steps, reverse_ok, reentrant_ok


def check_reentrant(sim, gp_row, verbose=False):
    """Verify head positions are correct for re-entrancy after forward pass.

    Returns True if all heads are in the expected re-entrant positions:
      H0 = (DATA_ROW, CW)
      H1 = (DATA_ROW, CW)
      CL = (GP_ROW, ROT), value 0
      GP = (GP_ROW, GP_SYND) — last garbage byte
    """
    ok = True
    expected_h0 = sim._to_flat(DATA_ROW, CW)
    expected_h1 = sim._to_flat(DATA_ROW, CW)
    expected_cl = sim._to_flat(gp_row, ROT)
    expected_gp = sim._to_flat(gp_row, GP_SYND)

    if sim.h0 != expected_h0:
        if verbose:
            h0_r, h0_c = sim.h0 // sim.cols, sim.h0 % sim.cols
            print(f"    [REENTRY] H0 at ({h0_r},{h0_c}), expected ({DATA_ROW},{CW})")
        ok = False
    if sim.h1 != expected_h1:
        if verbose:
            h1_r, h1_c = sim.h1 // sim.cols, sim.h1 % sim.cols
            print(f"    [REENTRY] H1 at ({h1_r},{h1_c}), expected ({DATA_ROW},{CW})")
        ok = False
    if sim.cl != expected_cl:
        if verbose:
            cl_r, cl_c = sim.cl // sim.cols, sim.cl % sim.cols
            print(f"    [REENTRY] CL at ({cl_r},{cl_c}), expected ({gp_row},{ROT})")
        ok = False
    if sim.grid[sim.cl] != 0:
        if verbose:
            print(f"    [REENTRY] [CL]={sim.grid[sim.cl]}, expected 0")
        ok = False
    if sim.gp != expected_gp:
        if verbose:
            gp_r, gp_c = sim.gp // sim.cols, sim.gp % sim.cols
            print(f"    [REENTRY] GP at ({gp_r},{gp_c}), expected ({gp_row},{GP_SYND})")
        ok = False

    return ok


def run_test(data4, error_bit=None, error_bit2=None, verbose=False,
             wrap_width=None):
    """Test Hamming correction on a single codeword."""
    cw = encode(data4)

    if error_bit2 is not None:
        bad = inject_double_error(cw, error_bit, error_bit2)
        error_desc = f"double flip bits {error_bit},{error_bit2}"
    elif error_bit is not None:
        bad = inject_error(cw, error_bit)
        error_desc = f"flip bit {error_bit}"
    else:
        bad = cw
        error_desc = "no error"

    result, syndrome, p_all_err, steps, reverse_ok, reentrant_ok = run_gadget(
        bad, verbose, wrap_width=wrap_width)

    if error_bit2 is not None:
        expected = bad          # double error: don't correct
        ok = (result == expected)
    elif error_bit is not None:
        expected = cw           # single error: correct
        ok = (result == expected)
    else:
        expected = cw           # no error: untouched
        ok = (result == expected)

    if verbose or not ok or not reverse_ok or not reentrant_ok:
        print(f"  data={data4:04b} cw={cw:08b} {error_desc}")
        print(f"    input={bad:08b} syn={syndrome:03b} p_all={p_all_err}"
              f" → result={result:08b} expected={expected:08b}"
              f" {'ok' if ok else 'FAIL'}")
        print(f"    {steps} steps, reverse={'ok' if reverse_ok else 'FAIL'}"
              f", reentry={'ok' if reentrant_ok else 'FAIL'}")

    return ok and reverse_ok and reentrant_ok


SLOT_WIDTH = ROT + 1   # 9 columns per correction slot


def run_reentrant_test(cases, verbose=False):
    """Test re-entrancy: run the gadget N times on N consecutive codewords.

    Each case is (data4, error_bit_or_None).

    LAYOUT: Each correction cycle uses a 9-column "slot". Within each slot,
    the codeword sits at the slot's first column on DATA_ROW, and the scratch
    cells use all 9 columns on GP_ROW (S0..ROT). This means the codeword is
    directly above the scratch base, so the gadget's relative head movements
    are position-independent.

      Slot 0: cols 0..8    CW at (DATA, 0),  scratch at (GP, 0..8)
      Slot 1: cols 9..17   CW at (DATA, 9),  scratch at (GP, 9..17)
      Slot 2: cols 18..26  CW at (DATA, 18), scratch at (GP, 18..26)

    Between cycles the outer loop advances all heads east by SLOT_WIDTH (9).
    """
    n = len(cases)
    code_ops, _, _ = build_gadget(gp_distance=2, n_rows=3)
    op_values = [OP[ch] for ch in code_ops]

    # Grid sizing
    slots_cols = n * SLOT_WIDTH
    code_cols_needed = len(code_ops) + 2
    cols = max(slots_cols, code_cols_needed)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # Place code
    for i, opchar in enumerate(code_ops):
        sim.grid[sim._to_flat(CODE_ROW, i)] = OP[opchar]

    # Place codewords in slot-based layout
    expected_results = []
    for i, (data4, error_bit) in enumerate(cases):
        cw = encode(data4)
        if error_bit is not None:
            bad = inject_error(cw, error_bit)
            expected_results.append(cw)
        else:
            bad = cw
            expected_results.append(cw)
        data_col = i * SLOT_WIDTH  # CW at first column of each slot
        sim.grid[sim._to_flat(DATA_ROW, data_col)] = bad

    # Initial head positions
    gp_row = GP_ROW
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, 0)        # (0, 0) — slot 0 CW
    sim.h1 = sim._to_flat(DATA_ROW, 0)        # (0, 0) — slot 0 CW
    sim.cl = sim._to_flat(gp_row, ROT)         # (2, 8) — slot 0 ROT
    sim.gp = sim._to_flat(gp_row, 0)           # (2, 0) — slot 0 base

    end_col = len(code_ops)
    all_ok = True

    for cycle in range(n):
        # Run the gadget forward
        sim.ip_row = CODE_ROW
        sim.ip_col = 0
        sim.ip_dir = 1  # East

        max_steps = 5000
        steps = 0
        for _ in range(max_steps):
            if (sim.ip_row == CODE_ROW and sim.ip_col == end_col
                    and sim.ip_dir == 1):
                break
            sim.step()
            steps += 1
        else:
            if verbose:
                print(f"    Cycle {cycle}: TIMEOUT")
            return False

        # Check result
        slot_base = cycle * SLOT_WIDTH
        result = sim.grid[sim._to_flat(DATA_ROW, slot_base)]
        expected = expected_results[cycle]
        ok = (result == expected)

        # Check re-entrancy head positions
        expected_h0 = sim._to_flat(DATA_ROW, slot_base)
        expected_h1 = sim._to_flat(DATA_ROW, slot_base)
        expected_gp = sim._to_flat(gp_row, slot_base + GP_SYND)
        expected_cl = sim._to_flat(gp_row, slot_base + ROT)

        heads_ok = (
            sim.h0 == expected_h0
            and sim.h1 == expected_h1
            and sim.gp == expected_gp
            and sim.cl == expected_cl
            and sim.grid[sim.cl] == 0
        )

        if verbose or not ok or not heads_ok:
            data4, error_bit = cases[cycle]
            err_desc = f"bit {error_bit}" if error_bit is not None else "none"
            print(f"    Cycle {cycle}: data={data4:04b} err={err_desc}"
                  f" result={result:08b} expected={expected:08b}"
                  f" {'ok' if ok else 'FAIL'}"
                  f" heads={'ok' if heads_ok else 'FAIL'}"
                  f" ({steps} steps)")
            if not heads_ok and verbose:
                h0_r, h0_c = sim.h0 // sim.cols, sim.h0 % sim.cols
                h1_r, h1_c = sim.h1 // sim.cols, sim.h1 % sim.cols
                gp_r, gp_c = sim.gp // sim.cols, sim.gp % sim.cols
                cl_r, cl_c = sim.cl // sim.cols, sim.cl % sim.cols
                e_h0_r, e_h0_c = expected_h0 // sim.cols, expected_h0 % sim.cols
                e_gp_r, e_gp_c = expected_gp // sim.cols, expected_gp % sim.cols
                e_cl_r, e_cl_c = expected_cl // sim.cols, expected_cl % sim.cols
                print(f"      H0=({h0_r},{h0_c}) exp ({e_h0_r},{e_h0_c})")
                print(f"      H1=({h1_r},{h1_c}) exp ({e_h0_r},{e_h0_c})")
                print(f"      GP=({gp_r},{gp_c}) exp ({e_gp_r},{e_gp_c})")
                print(f"      CL=({cl_r},{cl_c}) exp ({e_cl_r},{e_cl_c})"
                      f" [CL]={sim.grid[sim.cl]}")

        all_ok &= ok and heads_ok

        # ── Outer loop glue for next cycle ──
        if cycle < n - 1:
            # Advance all heads east by SLOT_WIDTH to next slot.
            # H0: (DATA, slot_base) → (DATA, slot_base + 9)
            for _ in range(SLOT_WIDTH):
                sim.h0 = sim._move_head(sim.h0, 1)   # East

            # H1: same
            for _ in range(SLOT_WIDTH):
                sim.h1 = sim._move_head(sim.h1, 1)   # East

            # GP: from SYND (slot_base + 1) to next slot base (slot_base + 9)
            # = 8 steps east
            for _ in range(SLOT_WIDTH - GP_SYND):
                sim.gp = sim._move_head(sim.gp, 1)   # East

            # CL: from ROT (slot_base + 8) to next ROT (slot_base + 17)
            # = 9 steps east
            for _ in range(SLOT_WIDTH):
                sim.cl = sim._move_head(sim.cl, 1)    # East

    return all_ok


def save_fb2d_files(wrap_width=None):
    """Generate .fb2d state files for the Hamming gadget."""
    prog_dir = os.path.dirname(os.path.abspath(__file__))

    cases = [
        ('hamming-noerror',  0b1010, None, None),
        ('hamming-bit0err',  0b1010, 0,    None),
        ('hamming-correct',  0b1010, 5,    None),
    ]

    suffix = f'-w{wrap_width}' if wrap_width else ''

    for name, data4, err_bit, err_bit2 in cases:
        cw = encode(data4)
        if err_bit2 is not None:
            cw = inject_double_error(cw, err_bit, err_bit2)
        elif err_bit is not None:
            cw = inject_error(cw, err_bit)

        sim = make_hamming_gadget(cw, wrap_width=wrap_width)
        fn = os.path.join(prog_dir, f'{name}{suffix}.fb2d')
        sim.save_state(fn)
        print(f"  Saved {fn}  ({sim.rows}×{sim.cols})")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Hamming SECDED gadget tests')
    parser.add_argument('--wrap', type=int, default=None, metavar='WIDTH',
                        help='Wrap code into boustrophedon layout of given width')
    parser.add_argument('--save', action='store_true',
                        help='Save .fb2d state files')
    args = parser.parse_args()

    wrap_width = args.wrap

    all_ok = True
    label = f' (wrapped {wrap_width}-wide)' if wrap_width else ''
    print(f"=== Hamming SECDED Gadget v2 (re-entrant, v1.8){label} ===\n")

    code_ops, total_cols, end_col = build_gadget(gp_distance=2, n_rows=3)
    print(f"Gadget: {len(code_ops)} code ops (linear), {total_cols} columns")
    print(f"  (Y fused rotate-XOR, : ; for CL value, scratch on GP row)")
    if wrap_width:
        sim_sample = make_hamming_gadget(encode(0), wrap_width=wrap_width)
        print(f"  Wrapped: {sim_sample.rows}×{sim_sample.cols} grid")

    print("\n--- No errors ---")
    no_err_ok = True
    for data in range(16):
        no_err_ok &= run_test(data, wrap_width=wrap_width)
    print(f"  16/16 no-error cases: {'PASS' if no_err_ok else 'FAIL'}")
    all_ok &= no_err_ok

    print("\n--- Single-bit error correction ---")
    single_ok = True
    count = 0
    for data in range(16):
        for bit in range(8):
            single_ok &= run_test(data, error_bit=bit, wrap_width=wrap_width)
            count += 1
    print(f"  {count}/{count} single-bit errors: {'PASS' if single_ok else 'FAIL'}")
    all_ok &= single_ok

    print("\n--- Double-bit error detection ---")
    double_ok = True
    count = 0
    for data in range(16):
        for b1 in range(8):
            for b2 in range(b1 + 1, 8):
                double_ok &= run_test(data, error_bit=b1, error_bit2=b2,
                                      wrap_width=wrap_width)
                count += 1
    print(f"  {count}/{count} double-bit errors: {'PASS' if double_ok else 'FAIL'}")
    all_ok &= double_ok

    print("\n--- Re-entrancy (multi-byte correction) ---")
    # Test 1: Two bytes, both with errors
    re_ok = run_reentrant_test([
        (0b1010, 5),   # bit 5 error
        (0b0110, 3),   # bit 3 error
    ], verbose=True)
    print(f"  2 bytes (both errors): {'PASS' if re_ok else 'FAIL'}")
    all_ok &= re_ok

    # Test 2: Three bytes, mixed (no error, error, no error)
    re_ok2 = run_reentrant_test([
        (0b1111, None),  # no error
        (0b0001, 7),     # bit 7 error
        (0b1000, None),  # no error
    ], verbose=True)
    print(f"  3 bytes (mixed): {'PASS' if re_ok2 else 'FAIL'}")
    all_ok &= re_ok2

    # Test 3: Four bytes, all different error types
    re_ok3 = run_reentrant_test([
        (0b0101, 0),     # bit 0 error (parity-only)
        (0b1010, 5),     # bit 5 error (full correction)
        (0b1100, None),  # no error
        (0b0011, 2),     # bit 2 error
    ], verbose=True)
    print(f"  4 bytes (all types): {'PASS' if re_ok3 else 'FAIL'}")
    all_ok &= re_ok3

    print("\n--- Verbose examples ---")
    run_test(0b1010, verbose=True, wrap_width=wrap_width)
    run_test(0b1010, error_bit=3, verbose=True, wrap_width=wrap_width)
    run_test(0b1010, error_bit=7, verbose=True, wrap_width=wrap_width)
    run_test(0b1010, error_bit=0, verbose=True, wrap_width=wrap_width)
    run_test(0b1010, error_bit=0, error_bit2=3, verbose=True,
             wrap_width=wrap_width)

    print(f"\n{'='*55}")
    print(f"{'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*55}")

    if args.save:
        print(f"\n--- Saving .fb2d state files{label} ---")
        save_fb2d_files(wrap_width=wrap_width)

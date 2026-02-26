#!/usr/bin/env python3
"""
hamming-gadget-demo.py — Hamming(16,11) SECDED correction as a spatial fb2d gadget.

Full spatial program on a toroidal grid. Computes syndrome AND corrects
in one forward pass, with full step_back() reversibility.

Uses Y (fused rotate-XOR, v1.7) for syndrome/parity computation and a
barrel shifter (paired f gates with l/r rotations) for 1-hot correction
mask assembly. Only 1 dirty GP cell per single-bit correction (0 for
no-error and double-error cases).

ARCHITECTURE (3-row torus, re-entrant, full Y-uncompute):

  Row 0 (DATA):  CW          ← single cell, ready for another correction loop
  Row 1 (CODE):  [opcodes left-to-right ...]
  Row 2 (GP):    PA S0 S1 S2 S3 EV SCR ROT
                  0  1  2  3  4  5   6   7

All scratch cells live on the GP row (assumed zero).
After correction: S0-S3,SCR,ROT clean (Y-uncomputed/unused). ≤1 dirty cell
(PA or EV depending on error type; 0 dirty for no-error/double-error).

Y opcode: [H0] ^= ror([H1], payload([CL]) & 15)  — self-inverse.
f opcode: if [CL]&1: swap([H0], [H1])  — bit-0 Fredkin, reads raw bit0.
z opcode: swap(bit0 of [H0], bit0 of [GP])  — raw bit swap.

ALGORITHM PHASES (all on CODE row, IP walks East):

  Phase A — OVERALL PARITY (~32 ops):
    H0 on PA (GP row), H1 on CW. Y with CL payload 0..15.
    PA.bit0 = XOR of all 16 CW bits = overall parity (p_all).

  Phase B — z-EXTRACT PA → EVIDENCE (~6 ops):
    z swaps PA.bit0 into EVIDENCE as raw 0 or 1.
    After: EV = raw p_all, PA.bit0 = 0.

  Phase A' — Y-UNCOMPUTE PA (~36 ops):
    Same Y ops reversed (15→0). Cancels Y accumulation in PA.
    Since PA.bit0 was modified by z, PA ends up as raw p_all (0 or 1).

  Phase C — SYNDROME (~84 ops):
    H0 on S0-S3, H1 on CW. CL on ROT. Standard Y accumulation.
    S_i.bit0 = syndrome bit i. Rest = Y-accumulated junk.

  Phase D — BARREL SHIFTER (~55 ops):
    For each syndrome bit i (0..3): l×(2^i), f, r×(2^i), f.
    CL on S_i for f gating. H0 on EV, H1 on SCR.
    Conditionally rotates EV left by 2^i when s_i=1.
    After: EV = p_all << syndrome (1-hot correction mask).
    SCR = 0 (always cleaned by f-pair symmetry).

  Phase C' — Y-UNCOMPUTE S0-S3 (~94 ops):
    Reverse Y ops clean S0-S3 back to 0.

  Phase E — CORRECTION XOR (~9 ops):
    CW ^= EVIDENCE. Flips the error bit (no-op if EV=0).

  Phase F — CLEANUP z+x (~13 ops):
    z(EV, PA) + x(EV, PA) merges two residuals into ≤1 dirty cell.

  Phase G — EPILOGUE (~7 ops):
    Return H0, H1 to (DATA_ROW, CW). CL at ROT(0), GP at PA.

DIRTY CELL ANALYSIS:
  No error (p=0,s=0):         0 dirty cells
  Double error (p=0,s≠0):     0 dirty cells (uncorrectable, CW unchanged)
  Bit-0 error (p=1,s=0):      1 dirty cell (PA=1)
  Bit-k error k≠0 (p=1,s=k):  1 dirty cell (EV=(1<<k)|1)

RE-ENTRANCY SLOT LAYOUT:
    Each correction cycle uses an 8-column "slot" (SLOT_WIDTH = 8).
    The codeword sits at slot_base on DATA_ROW; scratch cells occupy
    slot_base..slot_base+7 on GP_ROW.

      Slot 0: cols 0..7    CW at (DATA, 0),   scratch at (GP, 0..7)
      Slot 1: cols 8..15   CW at (DATA, 8),   scratch at (GP, 8..15)
      ...

    Between cycles, the outer loop advances all heads east by SLOT_WIDTH:
      H0, H1, CL, GP += 8 east each
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES, hamming_encode, cell_to_payload

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, inject_double_error, decode

# ── Data cell columns (row 0) ── (just the codeword!)
CW = 0   # codeword

# ── GP row scratch cell columns (assumed zero) ──
# Barrel-shifter layout: PA and EV are the only potentially dirty cells.
# Dirty after gadget: ≤1 of PA(0) or EV(5), depending on error type.
# Clean after gadget: S0-S3(1-4) via Y-uncompute, SCR(6), ROT(7).
GP_PA   = 0   # overall parity (0 or raw 1; only dirty for bit-0 errors)
GP_S0   = 1   # syndrome bit 0 accumulator (cleaned by Y-uncompute)
GP_S1   = 2   # syndrome bit 1 accumulator (cleaned by Y-uncompute)
GP_S2   = 3   # syndrome bit 2 accumulator (cleaned by Y-uncompute)
GP_S3   = 4   # syndrome bit 3 accumulator (cleaned by Y-uncompute)
GP_EV   = 5   # EVIDENCE: 1-hot correction mask (only dirty for bit-k errors)
GP_SCR  = 6   # SCRATCH for barrel shifter f-pairs (always cleaned)
ROT     = 7   # CL rotation counter (starts 0, recovered to 0)

DATA_ROW = 0
CODE_ROW = 1
GP_ROW   = 2
N_ROWS   = 3
N_DATA   = 1   # just CW on data row

# Syndrome bit positions for standard-form Hamming(16,11).
# si covers positions where bit i of the position number is 1.
SYNDROME_POSITIONS = [
    [1, 3, 5, 7, 9, 11, 13, 15],    # s0: positions with bit 0 set
    [2, 3, 6, 7, 10, 11, 14, 15],   # s1: positions with bit 1 set
    [4, 5, 6, 7, 12, 13, 14, 15],   # s2: positions with bit 2 set
    [8, 9, 10, 11, 12, 13, 14, 15], # s3: positions with bit 3 set
]

# Scratch cell columns for each syndrome accumulator
GP_SI = [GP_S0, GP_S1, GP_S2, GP_S3]


class GadgetBuilder:
    """Build an opcode sequence and track head positions.

    Uses Y (fused rotate-XOR) for efficient bit-position XOR accumulation.
    CL payload is manipulated inline via : and ; — no constant cells needed.
    Tracks row and col for H0, H1, CL, and GP.
    """

    def __init__(self, h0_row=DATA_ROW, h0_col=CW,
                 h1_row=DATA_ROW, h1_col=CW,
                 cl_col=ROT, cl_payload=0,
                 gp_col=GP_PA,
                 n_rows=3):
        self.ops = []           # list of opchar strings
        self.cursor = 0         # current column on CODE_ROW
        self.h0_row = h0_row
        self.h0_col = h0_col
        self.h1_row = h1_row
        self.h1_col = h1_col
        self.cl_col = cl_col    # CL position (column on GP row)
        self.cl_payload = cl_payload  # tracked payload at CL cell
        self.gp_col = gp_col    # GP position (column on GP row)
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

    def move_cl_col(self, target_col):
        """Move CL east/west to target column on GP row.

        After moving, cl_payload becomes unknown (new cell's value).
        Caller must set cl_payload if they want to track it.
        """
        diff = target_col - self.cl_col
        for _ in range(abs(diff)):
            self.emit('>' if diff > 0 else '<')
        self.cl_col = target_col
        self.cl_payload = None  # unknown — new cell

    def move_gp_col(self, target_col):
        diff = target_col - self.gp_col
        for _ in range(abs(diff)):
            self.emit(']' if diff > 0 else '[')
        self.gp_col = target_col

    def set_cl_payload(self, target):
        """Adjust [CL] payload to target value via : and ; ops.

        Only valid when cl_payload is known (not None).
        """
        assert self.cl_payload is not None, \
            f"CL payload unknown (CL at col {self.cl_col})"
        diff = target - self.cl_payload
        if diff > 0:
            self.emit_n(':', diff)
        elif diff < 0:
            self.emit_n(';', -diff)
        self.cl_payload = target

    def xor_accumulate_bits(self, bit_positions):
        """XOR specific bit positions of [H1] into [H0] via Y.

        Uses : and ; to set CL payload for each rotation amount.
        H0 and H1 must already be positioned. CL payload must be known.
        """
        for bit_pos in bit_positions:
            self.set_cl_payload(bit_pos)
            self.emit('Y')


def build_gadget(gp_distance=2, n_rows=3):
    """Build the barrel-shifter Hamming(16,11) SECDED gadget.

    Standard-form Hamming where syndrome == bit position. Uses a barrel
    shifter (paired f gates with l/r rotations) to build a 1-hot EVIDENCE
    mask directly from overall parity and syndrome bits. Only 1 dirty GP
    cell per single-bit correction (0 for no-error and double-error).

    Args:
        gp_distance: how many rows south from DATA_ROW to GP_ROW.
        n_rows: total grid rows (for toroidal shortcuts).

    Returns: (code_ops, total_cols, end_col)
    """
    gb = GadgetBuilder(n_rows=n_rows)
    gp_row_idx = gp_distance

    # ── Phase A: Overall parity via Y ──
    # H0 on PA (GP row), H1 on CW (DATA row), CL on ROT (payload 0).
    # Y at rotations 0..15 → PA.bit0 = XOR of all 16 CW bits = p_all.

    gb.move_h0_row(gp_row_idx)   # DATA→GP: toroidal shortcut
    # H0 now at (GP_ROW, PA=0). H1 on CW. CL at ROT, payload 0.
    gb.xor_accumulate_bits(list(range(16)))   # CL: 0→15

    phase_a_ops = gb.pos()

    # ── Phase B: z-extract PA.bit0 → EVIDENCE ──
    # Move H0 to EV. z swaps bit0 of [H0=EV(=0)] with [GP=PA].
    # After: EV = raw p_all (0 or 1), PA.bit0 = 0.

    gb.move_h0_col(GP_EV)       # PA(0) → EV(5): E×5
    gb.emit('z')                 # EV.bit0 ← PA.bit0; PA.bit0 ← 0

    phase_b_ops = gb.pos() - phase_a_ops

    # ── Phase A': Y-uncompute PA ──
    # Same Y ops reversed (15→0). Cancels all Y-accumulated junk in PA.
    # PA.bit0 was zeroed by z, so after uncompute PA = raw p_all (0 or 1).

    gb.move_h0_col(GP_PA)       # EV(5) → PA(0): W×5
    gb.xor_accumulate_bits(list(range(15, -1, -1)))   # CL: 15→0

    phase_ap_ops = gb.pos() - phase_a_ops - phase_b_ops

    # ── Phase C: Syndrome computation via Y ──
    # H0 on S0-S3 (GP row), H1 on CW, CL on ROT (payload 0).
    # Same optimized ordering as before for minimal :; ops.

    # H0: PA(0) → S0(1): E×1
    gb.move_h0_col(GP_S0)

    # s0: {1,3,5,7,9,11,13,15} ascending (CL: 0→15)
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])

    # s1: {2,3,6,7,10,11,14,15} descending (CL: 15→2)
    gb.move_h0_col(GP_S1)
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])

    # s2: {4,5,6,7,12,13,14,15} ascending (CL: 2→15)
    gb.move_h0_col(GP_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])

    # s3: {8,9,10,11,12,13,14,15} descending (CL: 15→8)
    gb.move_h0_col(GP_S3)
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    phase_c_ops = gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops

    # ── Phase D: Barrel shifter ──
    # Conditionally rotate EVIDENCE by 2^i for each syndrome bit i.
    # Pattern per stage: l×(2^i), f, r×(2^i), f
    #   If s_i=1: first f swaps rotated EV into SCR, r operates on 0 (no-op),
    #     second f swaps back → net rotation of 2^i. SCR stays 0.
    #   If s_i=0: no swaps, l and r cancel → EV unchanged. SCR stays 0.
    # After all 4 stages: EV = p_all << syndrome (1-hot mask, or 0).

    # Position H0 on EV, H1 on SCR
    gb.move_h0_col(GP_EV)                   # S3(4) → EV(5): E×1
    gb.move_h1(gp_row_idx, GP_SCR)          # CW(DATA,0) → SCR(GP,6)

    # Move CL to S0 for first barrel stage
    gb.move_cl_col(GP_S0)                    # ROT(7) → S0(1): <×6

    for i in range(4):
        if i > 0:
            gb.move_cl_col(GP_SI[i])         # S(i-1) → S(i): >×1
        shift = 1 << i
        gb.emit_n('l', shift)                # rotate EV left by 2^i
        gb.emit('f')                         # conditional swap EV↔SCR
        gb.emit_n('r', shift)                # rotate [H0] right by 2^i
        gb.emit('f')                         # conditional swap back

    phase_d_ops = (gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops
                   - phase_c_ops)

    # ── Phase C': Y-uncompute S0-S3 ──
    # Reverse the Y accumulations to clean S0-S3 back to 0.
    # Must hit the same rotation sets (XOR commutes, order is flexible).
    # Ordering chosen to minimize :; ops from current CL position.

    # Move H0 to S3, H1 back to CW, CL back to ROT
    gb.move_h0_col(GP_S3)                    # EV(5) → S3(4): W×1
    gb.move_h1(DATA_ROW, CW)                 # SCR(GP,6) → CW(DATA,0)
    gb.move_cl_col(ROT)                      # S3(4) → ROT(7): >×3
    gb.cl_payload = 8                        # ROT unchanged since Phase C

    # Uncompute s3: {8,9,10,11,12,13,14,15} ascending (CL: 8→15)
    gb.xor_accumulate_bits([8, 9, 10, 11, 12, 13, 14, 15])

    # Uncompute s2: {4,5,6,7,12,13,14,15} descending (CL: 15→4)
    gb.move_h0_col(GP_S2)
    gb.xor_accumulate_bits([15, 14, 13, 12, 7, 6, 5, 4])

    # Uncompute s1: {2,3,6,7,10,11,14,15} ascending (CL: 4→2→...→15)
    gb.move_h0_col(GP_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7, 10, 11, 14, 15])

    # Uncompute s0: {1,3,5,7,9,11,13,15} descending (CL: 15→1)
    gb.move_h0_col(GP_S0)
    gb.xor_accumulate_bits([15, 13, 11, 9, 7, 5, 3, 1])

    # Clean CL: payload 1 → 0
    gb.set_cl_payload(0)                     # ;×1

    phase_cp_ops = (gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops
                    - phase_c_ops - phase_d_ops)

    # ── Phase E: Correction XOR ──
    # CW ^= EVIDENCE. Flips the error bit (no-op if EV=0).

    gb.move_h0(DATA_ROW, CW)                # S0(GP,1) → CW(DATA,0)
    gb.move_h1(gp_row_idx, GP_EV)           # CW(DATA,0) → EV(GP,5)
    gb.emit('x')                             # CW ^= EVIDENCE

    phase_e_ops = (gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops
                   - phase_c_ops - phase_d_ops - phase_cp_ops)

    # ── Phase F: Cleanup z+x ──
    # Merge PA and EV residuals into ≤1 dirty cell.
    # z: swap bit0 of EV with GP(=PA).  x: EV ^= PA.
    #   No error:       PA=0, EV=0 → both stay 0.      (0 dirty)
    #   Double error:   PA=0, EV=0 → both stay 0.      (0 dirty)
    #   Bit-0 error:    PA=1, EV=1 → z(same), x: EV=0. (PA=1 dirty)
    #   Bit-k (k≠0):   PA=1, EV=1<<k → z: EV|=1, PA=0. x: nop.
    #                   EV=(1<<k)|1 dirty.               (1 dirty)

    gb.move_h0(gp_row_idx, GP_EV)           # CW(DATA,0) → EV(GP,5)
    gb.move_h1_col(GP_PA)                    # EV(GP,5) → PA(GP,0): w×5
    gb.emit('z')                             # swap bit0 of EV with PA
    gb.emit('x')                             # EV ^= PA

    phase_f_ops = (gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops
                   - phase_c_ops - phase_d_ops - phase_cp_ops - phase_e_ops)

    # ── Phase G: Epilogue ──
    # Return H0 and H1 to (DATA_ROW, CW).
    # CL at ROT(7), payload 0.  GP at PA(0).

    gb.move_h0(DATA_ROW, CW)                # EV(GP,5) → CW(DATA,0)
    gb.move_h1(DATA_ROW, CW)                # PA(GP,0) → CW(DATA,0)

    phase_g_ops = (gb.pos() - phase_a_ops - phase_b_ops - phase_ap_ops
                   - phase_c_ops - phase_d_ops - phase_cp_ops
                   - phase_e_ops - phase_f_ops)

    # Final state:
    #   H0 = (DATA_ROW, CW)     — ready for next codeword
    #   H1 = (DATA_ROW, CW)     — ready for next codeword
    #   CL = (GP_ROW, ROT), payload 0
    #   GP = (GP_ROW, GP_PA=0)  — slot base

    end_col = gb.pos()
    total_cols = gb.pos() + 2
    return gb.ops, total_cols, end_col


SLOT_WIDTH = ROT + 1   # 8 columns per correction slot


def make_hamming_gadget(codeword, wrap_width=None):
    """Build a torus with the Hamming(16,11) SECDED gadget.

    Data row has only CW. All scratch on GP row. CL on GP row.

    If wrap_width is None, uses a single-row layout (3 rows total).
    If wrap_width is given, wraps the code into that width using
    boustrophedon (serpentine) mirrors.
    """
    if wrap_width is None:
        # Single-row layout
        code_ops, min_cols, end_col = build_gadget(
            gp_distance=2, n_rows=N_ROWS)

        cols = max(ROT + 2, min_cols)
        sim = FB2DSimulator(rows=N_ROWS, cols=cols)

        # Place code row opcodes (Hamming-encoded)
        for i, opchar in enumerate(code_ops):
            sim.grid[sim._to_flat(CODE_ROW, i)] = hamming_encode(OP[opchar])

        gp_row = GP_ROW
        sim._wrap_end_row = CODE_ROW
        sim._wrap_end_col = end_col
        sim._wrap_end_dir = 1  # East

    else:
        # Wrapped layout: iterate because gp_distance depends on code size
        cols = wrap_width
        gp_dist = 2
        n_rows = N_ROWS

        for _ in range(5):
            code_ops, _, _ = build_gadget(
                gp_distance=gp_dist, n_rows=n_rows)
            op_values = [OP[ch] for ch in code_ops]  # raw values; wrap_code encodes

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

    # Place codeword on row 0
    sim.grid[sim._to_flat(DATA_ROW, CW)] = codeword

    # Initial head positions
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, CW)
    sim.h1 = sim._to_flat(DATA_ROW, CW)
    sim.cl = sim._to_flat(gp_row, ROT)     # CL on ROT cell
    sim.gp = sim._to_flat(gp_row, GP_PA)   # GP on PA cell

    return sim


def run_gadget(codeword, verbose=False, wrap_width=None):
    """Run the Hamming gadget on a codeword.

    Returns: (result_cw, ref_syndrome, ref_p_all, forward_steps, reverse_ok, reentrant_ok)
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
    for col in range(SLOT_WIDTH):
        if sim.grid[sim._to_flat(gp_row, col)] != 0:
            reverse_ok = False

    if verbose and not reverse_ok:
        print(f"    [WARN] Reverse failed:")
        print(f"      CW=0x{sim.grid[sim._to_flat(DATA_ROW, CW)]:04x}"
              f" (expected 0x{codeword:04x})")
        gp_names = ['PA', 'S0', 'S1', 'S2', 'S3', 'EV', 'SCR', 'ROT']
        for idx, name in enumerate(gp_names):
            val = sim.grid[sim._to_flat(gp_row, idx)]
            print(f"      GP.{name}=0x{val:04x}")

    return result_cw, ref_syn, ref_p_all, forward_steps, reverse_ok, reentrant_ok


def check_reentrant(sim, gp_row, verbose=False):
    """Verify head positions are correct for re-entrancy after forward pass.

    Expected positions:
      H0 = (DATA_ROW, CW)
      H1 = (DATA_ROW, CW)
      CL = (GP_ROW, ROT), payload 0
      GP = (GP_ROW, GP_PA)
    """
    ok = True
    expected_h0 = sim._to_flat(DATA_ROW, CW)
    expected_h1 = sim._to_flat(DATA_ROW, CW)
    expected_cl = sim._to_flat(gp_row, ROT)
    expected_gp = sim._to_flat(gp_row, GP_PA)

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
            print(f"    [REENTRY] [CL]=0x{sim.grid[sim.cl]:04x}, expected 0")
        ok = False
    if sim.gp != expected_gp:
        if verbose:
            gp_r, gp_c = sim.gp // sim.cols, sim.gp % sim.cols
            print(f"    [REENTRY] GP at ({gp_r},{gp_c}), expected ({gp_row},{GP_PA})")
        ok = False

    return ok


def run_test(payload, error_bit=None, error_bit2=None, verbose=False,
             wrap_width=None):
    """Test Hamming correction on a single codeword.

    payload: 11-bit data value (0-2047)
    error_bit: bit to flip (0-15) or None
    error_bit2: second bit to flip for double-error test
    """
    cw = encode(payload)

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
        print(f"  payload={payload} (0x{payload:03x}) cw=0x{cw:04x} {error_desc}")
        print(f"    input=0x{bad:04x} syn={syndrome:04b} p_all={p_all_err}"
              f" → result=0x{result:04x} expected=0x{expected:04x}"
              f" {'ok' if ok else 'FAIL'}")
        print(f"    {steps} steps, reverse={'ok' if reverse_ok else 'FAIL'}"
              f", reentry={'ok' if reentrant_ok else 'FAIL'}")

    return ok and reverse_ok and reentrant_ok


def run_reentrant_test(cases, verbose=False):
    """Test re-entrancy: run the gadget N times on N consecutive codewords.

    Each case is (payload_11bit, error_bit_or_None).

    LAYOUT: Each correction cycle uses a 9-column "slot".
      Slot 0: cols 0..8    CW at (DATA, 0),  scratch at (GP, 0..8)
      Slot 1: cols 9..17   CW at (DATA, 9),  scratch at (GP, 9..17)

    Between cycles the outer loop advances all heads east by SLOT_WIDTH (9).
    """
    n = len(cases)
    code_ops, _, _ = build_gadget(gp_distance=2, n_rows=3)

    # Grid sizing
    slots_cols = n * SLOT_WIDTH
    code_cols_needed = len(code_ops) + 2
    cols = max(slots_cols, code_cols_needed)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # Place code (Hamming-encoded)
    for i, opchar in enumerate(code_ops):
        sim.grid[sim._to_flat(CODE_ROW, i)] = hamming_encode(OP[opchar])

    # Place codewords in slot-based layout
    expected_results = []
    for i, (payload, error_bit) in enumerate(cases):
        cw = encode(payload)
        if error_bit is not None:
            bad = inject_error(cw, error_bit)
            expected_results.append(cw)
        else:
            bad = cw
            expected_results.append(cw)
        data_col = i * SLOT_WIDTH
        sim.grid[sim._to_flat(DATA_ROW, data_col)] = bad

    # Initial head positions
    gp_row = GP_ROW
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, 0)
    sim.h1 = sim._to_flat(DATA_ROW, 0)
    sim.cl = sim._to_flat(gp_row, ROT)
    sim.gp = sim._to_flat(gp_row, GP_PA)

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
        expected_gp = sim._to_flat(gp_row, slot_base + GP_PA)
        expected_cl = sim._to_flat(gp_row, slot_base + ROT)

        heads_ok = (
            sim.h0 == expected_h0
            and sim.h1 == expected_h1
            and sim.gp == expected_gp
            and sim.cl == expected_cl
            and sim.grid[sim.cl] == 0
        )

        if verbose or not ok or not heads_ok:
            payload, error_bit = cases[cycle]
            err_desc = f"bit {error_bit}" if error_bit is not None else "none"
            print(f"    Cycle {cycle}: payload={payload} err={err_desc}"
                  f" result=0x{result:04x} expected=0x{expected:04x}"
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
                      f" [CL]=0x{sim.grid[sim.cl]:04x}")

        all_ok &= ok and heads_ok

        # ── Outer loop glue for next cycle ──
        if cycle < n - 1:
            for _ in range(SLOT_WIDTH):
                sim.h0 = sim._move_head(sim.h0, 1)   # East
            for _ in range(SLOT_WIDTH):
                sim.h1 = sim._move_head(sim.h1, 1)   # East
            # GP: from PA (slot_base + 0) to next PA (slot_base + 9) = 9 east
            for _ in range(SLOT_WIDTH):
                sim.gp = sim._move_head(sim.gp, 1)   # East
            for _ in range(SLOT_WIDTH):
                sim.cl = sim._move_head(sim.cl, 1)    # East

    return all_ok


def save_fb2d_files(wrap_width=None):
    """Generate .fb2d state files for the Hamming gadget."""
    prog_dir = os.path.dirname(os.path.abspath(__file__))

    cases = [
        ('hamming16-noerror',  42,  None, None),
        ('hamming16-bit0err',  42,  0,    None),
        ('hamming16-correct',  42,  5,    None),
    ]

    suffix = f'-w{wrap_width}' if wrap_width else ''

    for name, payload, err_bit, err_bit2 in cases:
        cw = encode(payload)
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
    import random

    parser = argparse.ArgumentParser(
        description='Hamming(16,11) SECDED gadget tests')
    parser.add_argument('--wrap', type=int, default=None, metavar='WIDTH',
                        help='Wrap code into boustrophedon layout of given width')
    parser.add_argument('--save', action='store_true',
                        help='Save .fb2d state files')
    parser.add_argument('--exhaustive', action='store_true',
                        help='Run exhaustive tests (all 2048 payloads × 16 bits)')
    args = parser.parse_args()

    wrap_width = args.wrap

    all_ok = True
    label = f' (wrapped {wrap_width}-wide)' if wrap_width else ''
    print(f"=== Hamming(16,11) SECDED Gadget (standard form){label} ===\n")

    code_ops, total_cols, end_col = build_gadget(gp_distance=2, n_rows=3)
    print(f"Gadget: {len(code_ops)} code ops (linear), {total_cols} columns")
    print(f"  (barrel-shifter: Y syndrome, l/f/r/f correction, z+x cleanup)")
    if wrap_width:
        sim_sample = make_hamming_gadget(encode(0), wrap_width=wrap_width)
        print(f"  Wrapped: {sim_sample.rows}×{sim_sample.cols} grid")

    # ── Choose test payloads ──
    if args.exhaustive:
        test_payloads = list(range(2048))
    else:
        # Always test 0-47 (covers all opcodes) + 48 random larger values
        random.seed(42)
        test_payloads = list(range(48))
        test_payloads += random.sample(range(48, 2048), 48)
        test_payloads.sort()

    n_payloads = len(test_payloads)

    print(f"\n--- No errors ({n_payloads} payloads) ---")
    no_err_ok = True
    for payload in test_payloads:
        no_err_ok &= run_test(payload, wrap_width=wrap_width)
    print(f"  {n_payloads}/{n_payloads} no-error cases:"
          f" {'PASS' if no_err_ok else 'FAIL'}")
    all_ok &= no_err_ok

    print(f"\n--- Single-bit error correction ({n_payloads} × 16 bits) ---")
    single_ok = True
    count = 0
    for payload in test_payloads:
        for bit in range(16):
            single_ok &= run_test(payload, error_bit=bit,
                                  wrap_width=wrap_width)
            count += 1
    print(f"  {count}/{count} single-bit errors:"
          f" {'PASS' if single_ok else 'FAIL'}")
    all_ok &= single_ok

    # Double-bit: use smaller sample (16 payloads × all bit pairs)
    dbl_payloads = test_payloads[:16] if not args.exhaustive else test_payloads
    n_dbl = len(dbl_payloads)
    print(f"\n--- Double-bit error detection ({n_dbl} payloads × 120 pairs) ---")
    double_ok = True
    count = 0
    for payload in dbl_payloads:
        for b1 in range(16):
            for b2 in range(b1 + 1, 16):
                double_ok &= run_test(payload, error_bit=b1, error_bit2=b2,
                                      wrap_width=wrap_width)
                count += 1
    print(f"  {count}/{count} double-bit errors:"
          f" {'PASS' if double_ok else 'FAIL'}")
    all_ok &= double_ok

    print("\n--- Re-entrancy (multi-codeword correction) ---")
    # Test 1: Two codewords, both with errors
    re_ok = run_reentrant_test([
        (42, 5),     # bit 5 error
        (100, 11),   # bit 11 error
    ], verbose=True)
    print(f"  2 codewords (both errors): {'PASS' if re_ok else 'FAIL'}")
    all_ok &= re_ok

    # Test 2: Three codewords, mixed (no error, error, no error)
    re_ok2 = run_reentrant_test([
        (2047, None),  # no error (max payload)
        (1, 15),       # bit 15 error
        (500, None),   # no error
    ], verbose=True)
    print(f"  3 codewords (mixed): {'PASS' if re_ok2 else 'FAIL'}")
    all_ok &= re_ok2

    # Test 3: Four codewords, all different error types
    re_ok3 = run_reentrant_test([
        (7, 0),       # bit 0 error (overall parity only)
        (42, 5),      # bit 5 error (full correction)
        (999, None),  # no error
        (0, 8),       # bit 8 error
    ], verbose=True)
    print(f"  4 codewords (all types): {'PASS' if re_ok3 else 'FAIL'}")
    all_ok &= re_ok3

    print("\n--- Verbose examples ---")
    run_test(42, verbose=True, wrap_width=wrap_width)
    run_test(42, error_bit=3, verbose=True, wrap_width=wrap_width)
    run_test(42, error_bit=15, verbose=True, wrap_width=wrap_width)
    run_test(42, error_bit=0, verbose=True, wrap_width=wrap_width)
    run_test(42, error_bit=0, error_bit2=3, verbose=True,
             wrap_width=wrap_width)

    print(f"\n{'='*60}")
    print(f"{'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*60}")

    if args.save:
        print(f"\n--- Saving .fb2d state files{label} ---")
        save_fb2d_files(wrap_width=wrap_width)

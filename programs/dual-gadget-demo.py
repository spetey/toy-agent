#!/usr/bin/env python3
"""
dual-gadget-demo.py — Two Hamming(16,11) correction gadgets correcting each other.

Uses the IX interoceptor (v1.9) with the copy-down pattern:
  m: copy remote codeword to local EX cell
  M: uncompute local copy
  j: write correction mask back to remote cell

ALL computation happens on the local EX row. Only IX touches remote data.

SLOT LAYOUT (relative to EX cycle-start position):

  EX row:  [EV]  PA  CWL  S0  S1  S2  S3  SCR  ROT
  offset:    0    1    2    3   4   5   6    7    8

  EV  = evidence / waste (dirty after correction)
  PA  = overall parity accumulator
  CWL = local copy of remote codeword (copied via m, zeroed via M)
  S0-S3 = syndrome bit accumulators
  SCR = barrel shifter scratch
  ROT = CL rotation counter

PHASES (same algorithm as hamming-gadget-demo.py, but all on EX row):

  Copy-in:   m                    — [H0] += [IX] (CWL was 0, now = remote CW)
  Phase A:   Y parity (H0→PA, H1→CWL)
  Phase B:   z-extract PA.bit0 → EV
  Phase A':  Y-uncompute PA
  Phase C:   Y syndrome (H0 on S0-S3, H1 on CWL)
  Phase D:   Barrel shifter → EV has correction mask
  Phase C':  Y-uncompute S0-S3
  Uncompute: M                    — [H0] -= [IX] (CWL = 0 again, IX unchanged)
  Write-back: j                   — [IX] ^= [H0] (remote gets mask XOR)
  Phase F:   z+x cleanup (EV and PA)
  Epilogue:  return heads to CWL
  Advance:   E e ] > a            — all 5 heads east by 1
"""

import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  encode_opcode, OPCODE_PAYLOADS)

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, decode

# ── Sliding slot offsets (IX copy-down layout) ──
DSL_EV   = 0   # evidence / waste
DSL_PA   = 1   # overall parity
DSL_CWL  = 2   # local copy of remote codeword
DSL_S0   = 3   # syndrome bit 0
DSL_S1   = 4   # syndrome bit 1
DSL_S2   = 5   # syndrome bit 2
DSL_S3   = 6   # syndrome bit 3
DSL_SCR  = 7   # barrel shifter scratch
DSL_ROT  = 8   # CL rotation counter
DSL_SI   = [DSL_S0, DSL_S1, DSL_S2, DSL_S3]
DSL_SLOT_WIDTH = 9

# Syndrome bit positions for standard-form Hamming(16,11)
SYNDROME_POSITIONS = [
    [1, 3, 5, 7, 9, 11, 13, 15],
    [2, 3, 6, 7, 10, 11, 14, 15],
    [4, 5, 6, 7, 12, 13, 14, 15],
    [8, 9, 10, 11, 12, 13, 14, 15],
]

# Row assignments for the single-gadget test torus
REMOTE_ROW = 0   # where IX scans (test codewords)
CODE_ROW   = 1   # gadget opcodes (IP walks east)
EX_ROW     = 2   # scratch cells (all start zero)
N_ROWS     = 3


class GadgetBuilder:
    """Build an opcode sequence tracking head positions on EX row.

    Identical to hamming-gadget-demo.py GadgetBuilder but with no
    row tracking needed (everything is on EX row).
    """

    def __init__(self, h0_col=DSL_CWL, h1_col=DSL_CWL,
                 cl_col=DSL_ROT, cl_payload=0, gp_col=DSL_EV):
        self.ops = []
        self.cursor = 0
        self.h0_col = h0_col
        self.h1_col = h1_col
        self.cl_col = cl_col
        self.cl_payload = cl_payload
        self.gp_col = gp_col

    def emit(self, opchar):
        self.ops.append(opchar)
        self.cursor += 1

    def emit_n(self, opchar, n):
        for _ in range(n):
            self.emit(opchar)

    def pos(self):
        return self.cursor

    def move_h0_col(self, target_col):
        diff = target_col - self.h0_col
        for _ in range(abs(diff)):
            self.emit('E' if diff > 0 else 'W')
        self.h0_col = target_col

    def move_h1_col(self, target_col):
        diff = target_col - self.h1_col
        for _ in range(abs(diff)):
            self.emit('e' if diff > 0 else 'w')
        self.h1_col = target_col

    def move_cl_col(self, target_col):
        diff = target_col - self.cl_col
        for _ in range(abs(diff)):
            self.emit('>' if diff > 0 else '<')
        self.cl_col = target_col
        self.cl_payload = None

    def move_gp_col(self, target_col):
        diff = target_col - self.gp_col
        for _ in range(abs(diff)):
            self.emit(']' if diff > 0 else '[')
        self.gp_col = target_col

    def set_cl_payload(self, target):
        assert self.cl_payload is not None, \
            f"CL payload unknown (CL at col {self.cl_col})"
        diff = target - self.cl_payload
        if diff > 0:
            self.emit_n(':', diff)
        elif diff < 0:
            self.emit_n(';', -diff)
        self.cl_payload = target

    def xor_accumulate_bits(self, bit_positions):
        for bit_pos in bit_positions:
            self.set_cl_payload(bit_pos)
            self.emit('Y')


def build_h2_correction_gadget():
    """Build the IX-based sliding-slot Hamming(16,11) correction gadget.

    All computation on the EX row. IX points at remote code to correct.
    Uses copy-down pattern: m copies [IX] to local CWL, correction runs
    locally, M uncomputes the copy, j writes the correction mask back.

    Initial head positions (relative to slot start):
      H0 = (EX_ROW, CWL=2)   — worker head
      H1 = (EX_ROW, CWL=2)   — reference head
      IX = (REMOTE_ROW, col)  — interoceptor on remote data
      CL = (EX_ROW, ROT=8)   — rotation control
      EX = (EX_ROW, EV=0)    — waste/evidence pointer

    Returns: list of opchar strings
    """
    gb = GadgetBuilder()

    # ── Copy-in: m copies [IX] to [H0] at CWL ──
    # H0 at CWL (=0), IX at remote codeword.
    # After: [H0] = payload(remote), IX unchanged.
    gb.emit('m')

    # ── Phase A: Overall parity via Y ──
    # H0 on PA, H1 on CWL. Y at rotations 0..15.
    gb.move_h0_col(DSL_PA)          # CWL(2) → PA(1): W×1
    gb.xor_accumulate_bits(list(range(16)))   # CL: 0→15

    # ── Phase B: z-extract PA.bit0 → EV ──
    gb.move_h0_col(DSL_EV)          # PA(1) → EV(0): W×1
    gb.move_h1_col(DSL_PA)          # CWL(2) → PA(1): w×1
    gb.emit('z')                     # EV.bit0 ← PA.bit0; PA.bit0 ← 0
    gb.move_h1_col(DSL_CWL)         # PA(1) → CWL(2): e×1

    # ── Phase A': Y-uncompute PA ──
    gb.move_h0_col(DSL_PA)          # EV(0) → PA(1): E×1
    gb.xor_accumulate_bits(list(range(15, -1, -1)))   # CL: 15→0

    # ── Phase C: Syndrome computation via Y ──
    gb.move_h0_col(DSL_S0)          # PA(1) → S0(3): E×2

    # s0: ascending (CL: 0→15)
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])

    # s1: descending (CL: 15→2)
    gb.move_h0_col(DSL_S1)          # S0(3) → S1(4): E×1
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])

    # s2: ascending (CL: 2→15)
    gb.move_h0_col(DSL_S2)          # S1(4) → S2(5): E×1
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])

    # s3: descending (CL: 15→8)
    gb.move_h0_col(DSL_S3)          # S2(5) → S3(6): E×1
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    # ── Phase D: Barrel shifter ──
    # H0 on EV, H1 on SCR, CL on S0.
    gb.move_h0_col(DSL_EV)          # S3(6) → EV(0): W×6
    gb.move_h1_col(DSL_SCR)         # CWL(2) → SCR(7): e×5
    gb.move_cl_col(DSL_S0)          # ROT(8) → S0(3): <×5

    for i in range(4):
        if i > 0:
            gb.move_cl_col(DSL_SI[i])
        shift = 1 << i
        gb.emit_n('l', shift)
        gb.emit('f')
        gb.emit_n('r', shift)
        gb.emit('f')

    # ── Phase C': Y-uncompute S0-S3 ──
    gb.move_h0_col(DSL_S3)          # EV(0) → S3(6): E×6
    gb.move_h1_col(DSL_CWL)         # SCR(7) → CWL(2): w×5
    gb.move_cl_col(DSL_ROT)         # S3(6) → ROT(8): >×2
    gb.cl_payload = 8               # ROT unchanged since Phase C

    # Uncompute s3: ascending (CL: 8→15)
    gb.xor_accumulate_bits([8, 9, 10, 11, 12, 13, 14, 15])

    # Uncompute s2: descending (CL: 15→4)
    gb.move_h0_col(DSL_S2)
    gb.xor_accumulate_bits([15, 14, 13, 12, 7, 6, 5, 4])

    # Uncompute s1: ascending (CL: 4→2→...→15)
    gb.move_h0_col(DSL_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7, 10, 11, 14, 15])

    # Uncompute s0: descending (CL: 15→1)
    gb.move_h0_col(DSL_S0)
    gb.xor_accumulate_bits([15, 13, 11, 9, 7, 5, 3, 1])

    # Clean CL: payload 1 → 0
    gb.set_cl_payload(0)

    # ── Uncompute local copy ──
    # H0 to CWL. m: [H0] ^= [IX] → CWL = cw ^ cw = 0 (XOR self-inverse).
    # MUST happen before write-back (IX still has original value).
    gb.move_h0_col(DSL_CWL)         # S0(3) → CWL(2): W×1
    gb.emit('m')                     # [H0] ^= [IX] → CWL = 0

    # ── Write correction to remote ──
    # H0 to EV (the correction mask). j: [IX] ^= [H0].
    gb.move_h0_col(DSL_EV)          # CWL(2) → EV(0): W×2
    gb.emit('j')                     # [IX] ^= [H0] → remote corrected

    # ── Phase F: Cleanup z+x ──
    # H0 at EV (already), H1 to PA. z+x merges residuals.
    gb.move_h1_col(DSL_PA)          # CWL(2) → PA(1): w×1
    gb.emit('z')                     # swap bit0 of EV with PA
    gb.emit('x')                     # EV ^= PA

    # ── Epilogue: return H0, H1 to CWL ──
    gb.move_h0_col(DSL_CWL)         # EV(0) → CWL(2): E×2
    gb.move_h1_col(DSL_CWL)         # PA(1) → CWL(2): e×1

    gadget_ops = gb.pos()

    # ── Head advance: all 5 heads east by 1 ──
    gb.emit('E')      # H0 east
    gb.emit('e')      # H1 east
    gb.emit(']')      # EX east
    gb.emit('>')      # CL east
    gb.emit('a')      # IX east (next remote codeword)

    total_ops = gb.pos()

    return gb.ops


# ═══════════════════════════════════════════════════════════════════
# Grid layout builders
# ═══════════════════════════════════════════════════════════════════

def place_boustrophedon(sim, op_values, left_col, right_col, start_row):
    """Place Hamming-encoded opcodes in boustrophedon layout.

    The IP enters at (start_row, left_col) going East and snakes through
    rows using mirrors at the column boundaries.

    op_values: list of opcode numbers (int). Negative values are treated
    as pre-encoded raw cell values (abs(val)) — not passed through
    encode_opcode. Use this for NOP filler cells.

    Does NOT place the final turn mirror on the last row (leaves the IP
    free to continue into the return corridor).

    Returns: (rows_used, end_row, last_op_col, end_dir)
    """
    def _encode(val):
        if val < 0:
            return -val  # pre-encoded raw cell value
        return encode_opcode(val)
    total = len(op_values)
    if total == 0:
        return 0, start_row, left_col, 1

    placed = 0
    row = start_row
    row_count = 0

    while placed < total:
        row_count += 1

        if row_count == 1:
            # First row: going East, code from left_col to right_col-1
            # Mirror \ at right_col to turn E→S
            slots = right_col - left_col   # e.g. 61-2 = 59
            n = min(slots, total - placed)
            for i in range(n):
                sim.grid[sim._to_flat(row, left_col + i)] = _encode(
                    op_values[placed])
                placed += 1
            if placed >= total:
                return row_count, row, left_col + n - 1, 1  # DIR_E
            sim.grid[sim._to_flat(row, right_col)] = encode_opcode(OP['\\'])

        elif row_count % 2 == 0:
            # Even row_count: going West
            # / at right_col (entry: S→W), code from right_col-1 to left_col+1
            sim.grid[sim._to_flat(row, right_col)] = encode_opcode(OP['/'])
            slots = right_col - left_col - 1   # e.g. 61-2-1 = 58
            n = min(slots, total - placed)
            for i in range(n):
                sim.grid[sim._to_flat(row, right_col - 1 - i)] = _encode(
                    op_values[placed])
                placed += 1
            if placed >= total:
                return row_count, row, right_col - 1 - (n - 1), 3  # DIR_W
            sim.grid[sim._to_flat(row, left_col)] = encode_opcode(OP['/'])

        else:
            # Odd row_count (not first): going East
            # \ at left_col (entry: S→E), code from left_col+1 to right_col-1
            sim.grid[sim._to_flat(row, left_col)] = encode_opcode(OP['\\'])
            slots = right_col - left_col - 1
            n = min(slots, total - placed)
            for i in range(n):
                sim.grid[sim._to_flat(row, left_col + 1 + i)] = _encode(
                    op_values[placed])
                placed += 1
            if placed >= total:
                return row_count, row, left_col + 1 + (n - 1), 1  # DIR_E
            sim.grid[sim._to_flat(row, right_col)] = encode_opcode(OP['\\'])

        row += 1

    return row_count, row - 1, left_col, 1  # shouldn't reach here


def make_h2_boustrophedon_torus(cases, first_cw_col=2,
                                 grid_width=64, code_left=2, code_right=61):
    """Build a boustrophedon torus for IX-based correction.

    Layout:
      Row 0:       REMOTE — codewords (IX scans here)
      Rows 1..R:   CODE   — boustrophedon in cols code_left..code_right
      Row R+1:     EX     — scratch cells (all zero)
      Col 1:       return corridor (mirrors on first and last code rows)

    cases: list of (payload_11bit, error_bit_or_None)

    Returns: (sim, expected_results, cycle_length, ex_start_col)
    """
    code_ops = build_h2_correction_gadget()
    op_values = [OP[ch] for ch in code_ops]
    n_ops = len(op_values)
    n = len(cases)

    # How many code rows?
    first_row_slots = code_right - code_left      # 59
    inner_slots = code_right - code_left - 1       # 58
    if n_ops <= first_row_slots:
        code_rows = 1
    else:
        remaining = n_ops - first_row_slots
        code_rows = 1 + -(-remaining // inner_slots)   # ceiling division

    first_code_row = 1
    last_code_row = first_code_row + code_rows - 1
    ex_row = last_code_row + 1
    n_rows = ex_row + 1

    # EX layout
    ex_start_col = first_cw_col - DSL_CWL   # EV at col 0
    max_gp_col = ex_start_col + n - 1 + DSL_ROT
    assert max_gp_col < grid_width, (
        f"Too many codewords ({n}): EX scratch extends to col {max_gp_col}"
        f" but grid is only {grid_width} wide")

    sim = FB2DSimulator(rows=n_rows, cols=grid_width)

    # Place code via boustrophedon
    rows_used, end_row, last_op_col, end_dir = place_boustrophedon(
        sim, op_values, code_left, code_right, start_row=first_code_row)

    # Return corridor at col 1
    # \ at (last_code_row, 1): W→N
    sim.grid[sim._to_flat(last_code_row, 1)] = encode_opcode(OP['\\'])
    # / at (first_code_row, 1): N→E
    sim.grid[sim._to_flat(first_code_row, 1)] = encode_opcode(OP['/'])

    # Place test codewords on REMOTE_ROW
    expected = []
    for i, (payload, error_bit) in enumerate(cases):
        cw = encode(payload)
        if error_bit is not None:
            bad = inject_error(cw, error_bit)
        else:
            bad = cw
        expected.append(cw)
        sim.grid[sim._to_flat(REMOTE_ROW, first_cw_col + i)] = bad

    # Initial head positions
    sim.ip_row = first_code_row
    sim.ip_col = code_left
    sim.ip_dir = 1    # East

    sim.h0 = sim._to_flat(ex_row, ex_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(ex_row, ex_start_col + DSL_CWL)
    sim.ix = sim._to_flat(REMOTE_ROW, first_cw_col)
    sim.cl = sim._to_flat(ex_row, ex_start_col + DSL_ROT)
    sim.ex = sim._to_flat(ex_row, ex_start_col + DSL_EV)

    # Cycle length: rows_used * (code_width + 1)
    code_width = code_right - code_left + 1
    cycle_length = rows_used * (code_width + 1)

    return sim, expected, cycle_length, ex_start_col


def make_h2_test_torus(cases, first_cw_col=2):
    """Build a linear torus to test IX-based correction.

    Layout:
      Row 0: REMOTE — test codewords (IX scans here)
      Row 1: CODE   — gadget opcodes (IP goes east, linear)
      Row 2: EX     — scratch cells (all zero)

    cases: list of (payload_11bit, error_bit_or_None)

    Returns: (sim, expected_results, cycle_length)
    """
    code_ops = build_h2_correction_gadget()
    op_values = [OP[ch] for ch in code_ops]
    n_ops = len(op_values)
    n = len(cases)

    # Grid width must fit the code AND the scratch cells
    # EX scratch extends from first_cw_col - 1 (EV) to first_cw_col - 1 + n + DSL_ROT
    ex_start_col = first_cw_col - DSL_CWL   # EV at col (first_cw_col - 2)
    max_scratch_col = ex_start_col + n + DSL_ROT
    cols = max(n_ops + 2, max_scratch_col + 2, first_cw_col + n + DSL_SLOT_WIDTH)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # Place gadget code on CODE_ROW (linear, Hamming-encoded)
    for i, opval in enumerate(op_values):
        sim.grid[sim._to_flat(CODE_ROW, i)] = encode_opcode(opval)

    # Place test codewords on REMOTE_ROW
    expected = []
    for i, (payload, error_bit) in enumerate(cases):
        cw = encode(payload)
        if error_bit is not None:
            bad = inject_error(cw, error_bit)
        else:
            bad = cw
        expected.append(cw)
        sim.grid[sim._to_flat(REMOTE_ROW, first_cw_col + i)] = bad

    # Initial head positions
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1    # East

    sim.h0 = sim._to_flat(EX_ROW, ex_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(EX_ROW, ex_start_col + DSL_CWL)
    sim.ix = sim._to_flat(REMOTE_ROW, first_cw_col)
    sim.cl = sim._to_flat(EX_ROW, ex_start_col + DSL_ROT)
    sim.ex = sim._to_flat(EX_ROW, ex_start_col + DSL_EV)

    # Cycle length = cols (IP wraps around the torus)
    cycle_length = cols

    return sim, expected, cycle_length, ex_start_col


def run_h2_test(cases, verbose=True, check_reverse=True,
                make_torus_fn=None):
    """Test IX-based correction on remote codewords.

    Args:
        make_torus_fn: optional torus builder function(cases, first_cw_col=2)
            -> (sim, expected, cycle_length, ex_start_col).
            Defaults to make_h2_test_torus (linear layout).

    Returns: bool (all tests passed)
    """
    if make_torus_fn is None:
        make_torus_fn = make_h2_test_torus
    n = len(cases)
    first_cw_col = 2
    sim, expected, cycle_length, ex_start_col = make_torus_fn(
        cases, first_cw_col=first_cw_col)

    # EX row is always the last row (works for both linear and boustrophedon)
    ex_row = sim.rows - 1

    # Run N cycles
    total_steps = n * cycle_length
    for _ in range(total_steps):
        sim.step()

    # Check results on REMOTE_ROW
    all_ok = True
    for i in range(n):
        data_col = first_cw_col + i
        result = sim.grid[sim._to_flat(REMOTE_ROW, data_col)]
        ok = (result == expected[i])
        if verbose or not ok:
            payload, error_bit = cases[i]
            err_desc = f"bit {error_bit}" if error_bit is not None else "none"
            print(f"    CW[{i}] col={data_col}: payload={payload} err={err_desc}"
                  f" result=0x{result:04x} expected=0x{expected[i]:04x}"
                  f" {'ok' if ok else 'FAIL'}")
        all_ok &= ok

    # Check head positions
    final_cw = ex_start_col + DSL_CWL + n
    final_gp = ex_start_col + DSL_EV + n
    final_rot = ex_start_col + DSL_ROT + n
    final_h2 = first_cw_col + n

    heads_ok = True
    h0_exp = sim._to_flat(ex_row, final_cw)
    h1_exp = sim._to_flat(ex_row, final_cw)
    gp_exp = sim._to_flat(ex_row, final_gp)
    cl_exp = sim._to_flat(ex_row, final_rot)
    h2_exp = sim._to_flat(REMOTE_ROW, final_h2)

    if sim.h0 != h0_exp or sim.h1 != h1_exp or sim.ex != gp_exp \
            or sim.cl != cl_exp or sim.ix != h2_exp:
        heads_ok = False
    if verbose or not heads_ok:
        print(f"    Final heads: H0={sim.h0 % sim.cols} H1={sim.h1 % sim.cols}"
              f" EX={sim.ex % sim.cols} CL={sim.cl % sim.cols}"
              f" IX=({sim.ix // sim.cols},{sim.ix % sim.cols})"
              f" {'ok' if heads_ok else 'FAIL'}")
        if not heads_ok:
            print(f"      Expected: H0={final_cw} H1={final_cw}"
                  f" EX={final_gp} CL={final_rot}"
                  f" IX=(0,{final_h2})")
    all_ok &= heads_ok

    # EX dirty trail
    if verbose:
        dirty = 0
        for i in range(n):
            ev_col = ex_start_col + DSL_EV + i
            if sim.grid[sim._to_flat(ex_row, ev_col)] != 0:
                dirty += 1
        print(f"    Dirty trail: {dirty}/{n} waste cells nonzero")

    if verbose:
        print(f"    Grid: {sim.rows}×{sim.cols}, {cycle_length} steps/cycle,"
              f" {n} cycles, {total_steps} total steps")
        print(f"    Gadget: {len(build_h2_correction_gadget())} ops")

    # Full reverse check
    if check_reverse:
        for _ in range(total_steps):
            sim.step_back()

        reverse_ok = True
        for i in range(n):
            data_col = first_cw_col + i
            payload, error_bit = cases[i]
            cw = encode(payload)
            orig = inject_error(cw, error_bit) if error_bit is not None else cw
            result = sim.grid[sim._to_flat(REMOTE_ROW, data_col)]
            if result != orig:
                reverse_ok = False
                if verbose:
                    print(f"    [REVERSE] CW[{i}]: 0x{result:04x}"
                          f" != expected 0x{orig:04x}")

        # Check EX row clean
        for col in range(sim.cols):
            v = sim.grid[sim._to_flat(ex_row, col)]
            if v != 0:
                reverse_ok = False
                if verbose:
                    print(f"    [REVERSE] EX col {col}: 0x{v:04x} != 0")
                break

        if verbose:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_h2_single_correction():
    """Test: single codeword corrected via IX."""
    print("=== IX single correction ===")
    # payload=42, flip bit 3
    ok = run_h2_test([(42, 3)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_no_error():
    """Test: no error case (IX should be no-op)."""
    print("=== IX no error ===")
    ok = run_h2_test([(42, None)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_bit0_error():
    """Test: bit-0 error (overall parity bit)."""
    print("=== IX bit-0 error ===")
    ok = run_h2_test([(42, 0)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_multiple():
    """Test: multiple codewords with various errors."""
    print("=== IX multiple codewords ===")
    cases = [
        (1, 1),      # payload 1, flip bit 1
        (2, 2),      # payload 2, flip bit 2
        (100, None),  # no error
        (200, 15),   # flip bit 15
        (0, None),   # zero payload, no error
        (2047, 0),   # max payload, flip bit 0
    ]
    ok = run_h2_test(cases)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_all_error_positions():
    """Test: every possible single-bit error position (0-15)."""
    print("=== IX all 16 error positions ===")
    cases = [(42, bit) for bit in range(16)]
    ok = run_h2_test(cases, verbose=False)
    if ok:
        print(f"  All 16 error positions: PASS")
    else:
        # Re-run verbose on failure
        run_h2_test(cases, verbose=True)
        print(f"  FAIL")
    return ok


def test_h2_random():
    """Test: random payloads and error positions."""
    print("=== IX random (20 codewords) ===")
    random.seed(42)
    cases = []
    for _ in range(20):
        payload = random.randint(0, 2047)
        if random.random() < 0.2:
            error_bit = None
        else:
            error_bit = random.randint(0, 15)
        cases.append((payload, error_bit))
    ok = run_h2_test(cases, verbose=False)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_boustrophedon():
    """Test: IX correction with boustrophedon code layout."""
    print("=== IX boustrophedon layout ===")
    cases = [
        (42, 3),     # standard error
        (100, 7),    # mid-range
        (0, None),   # no error
        (2047, 15),  # max payload, high bit
    ]
    ok = run_h2_test(cases, make_torus_fn=make_h2_boustrophedon_torus)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_boustrophedon_all_positions():
    """Test: all 16 error positions in boustrophedon layout."""
    print("=== IX boustrophedon all 16 error positions ===")
    cases = [(42, bit) for bit in range(16)]
    ok = run_h2_test(cases, verbose=False,
                     make_torus_fn=make_h2_boustrophedon_torus)
    if ok:
        print(f"  All 16 error positions: PASS")
    else:
        run_h2_test(cases, verbose=True,
                    make_torus_fn=make_h2_boustrophedon_torus)
        print(f"  FAIL")
    return ok


def save_demo(filename=None, payload=42, error_bit=3):
    """Save a loadable .fb2d file with one corrupted codeword.

    Uses boustrophedon layout (8×64 grid) for readable display.

    Usage from command line:
        python3 programs/dual-gadget-demo.py --save
        python3 programs/dual-gadget-demo.py --save --payload 100 --error 7

    Then in the interactive simulator:
        python3 fb2d.py
        > load h2-correction-demo
        > s 366     (one full cycle — corrects the codeword)
        > show
    """
    cases = [(payload, error_bit)]
    sim, expected, cycle_length, ex_start_col = make_h2_boustrophedon_torus(
        cases, first_cw_col=2)
    ex_row = sim.rows - 1

    if filename is None:
        filename = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'h2-correction-demo.fb2d')

    sim.save_state(filename)

    cw = encode(payload)
    bad = inject_error(cw, error_bit) if error_bit is not None else cw
    err_desc = f"bit {error_bit}" if error_bit is not None else "none"

    print(f"Saved: {filename}")
    print(f"  Grid: {sim.rows}×{sim.cols}"
          f" (row 0=REMOTE, rows 1-{ex_row-1}=CODE, row {ex_row}=EX)")
    print(f"  Codeword: payload={payload} (0x{cw:04x}), error={err_desc}"
          f" → 0x{bad:04x}")
    print(f"  Expected after 1 cycle ({cycle_length} steps): 0x{expected[0]:04x}")
    print(f"  Gadget: {len(build_h2_correction_gadget())} ops")
    print()
    print(f"  H0,H1 at ({ex_row},{ex_start_col + DSL_CWL})"
          f" on EX row (local CWL slot)")
    print(f"  IX at (0,2) on REMOTE row (corrupted codeword)")
    print(f"  CL at ({ex_row},{ex_start_col + DSL_ROT}) on EX row (ROT slot)")
    print(f"  EX at ({ex_row},{ex_start_col + DSL_EV}) on EX row (EV slot)")
    print()
    print(f"In the simulator:")
    print(f"  load h2-correction-demo")
    print(f"  s {cycle_length}    # run one correction cycle")
    print(f"  show")


if __name__ == '__main__':
    if '--save' in sys.argv:
        payload = 42
        error_bit = 3
        for i, arg in enumerate(sys.argv):
            if arg == '--payload' and i + 1 < len(sys.argv):
                payload = int(sys.argv[i + 1])
            if arg == '--error' and i + 1 < len(sys.argv):
                error_bit = int(sys.argv[i + 1])
        save_demo(payload=payload, error_bit=error_bit)
        sys.exit(0)

    print(f"Gadget size: {len(build_h2_correction_gadget())} ops")
    print()

    all_ok = True
    all_ok &= test_h2_single_correction()
    print()
    all_ok &= test_h2_no_error()
    print()
    all_ok &= test_h2_bit0_error()
    print()
    all_ok &= test_h2_multiple()
    print()
    all_ok &= test_h2_all_error_positions()
    print()
    all_ok &= test_h2_random()
    print()
    all_ok &= test_h2_boustrophedon()
    print()
    all_ok &= test_h2_boustrophedon_all_positions()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL IX CORRECTION TESTS PASSED")
        print("=" * 60)

        # Also save a demo file
        print()
        save_demo()
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

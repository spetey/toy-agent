#!/usr/bin/env python3
"""
dual-gadget-demo.py — Two Hamming(16,11) correction gadgets correcting each other.

Uses the H2 scan head (v1.9) with the copy-down pattern:
  m: copy remote codeword to local GP cell
  M: uncompute local copy
  j: write correction mask back to remote cell

ALL computation happens on the local GP row. Only H2 touches remote data.

SLOT LAYOUT (relative to GP cycle-start position):

  GP row:  [EV]  PA  CWL  S0  S1  S2  S3  SCR  ROT
  offset:    0    1    2    3   4   5   6    7    8

  EV  = evidence / waste (dirty after correction)
  PA  = overall parity accumulator
  CWL = local copy of remote codeword (copied via m, zeroed via M)
  S0-S3 = syndrome bit accumulators
  SCR = barrel shifter scratch
  ROT = CL rotation counter

PHASES (same algorithm as hamming-gadget-demo.py, but all on GP row):

  Copy-in:   m                    — [H0] += [H2] (CWL was 0, now = remote CW)
  Phase A:   Y parity (H0→PA, H1→CWL)
  Phase B:   z-extract PA.bit0 → EV
  Phase A':  Y-uncompute PA
  Phase C:   Y syndrome (H0 on S0-S3, H1 on CWL)
  Phase D:   Barrel shifter → EV has correction mask
  Phase C':  Y-uncompute S0-S3
  Uncompute: M                    — [H0] -= [H2] (CWL = 0 again, H2 unchanged)
  Write-back: j                   — [H2] ^= [H0] (remote gets mask XOR)
  Phase F:   z+x cleanup (EV and PA)
  Epilogue:  return heads to CWL
  Advance:   E e ] > a            — all 5 heads east by 1
"""

import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES, hamming_encode, cell_to_payload

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, decode

# ── Sliding slot offsets (H2 copy-down layout) ──
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
REMOTE_ROW = 0   # where H2 scans (test codewords)
CODE_ROW   = 1   # gadget opcodes (IP walks east)
GP_ROW     = 2   # scratch cells (all start zero)
N_ROWS     = 3


class GadgetBuilder:
    """Build an opcode sequence tracking head positions on GP row.

    Identical to hamming-gadget-demo.py GadgetBuilder but with no
    row tracking needed (everything is on GP row).
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
    """Build the H2-based sliding-slot Hamming(16,11) correction gadget.

    All computation on the GP row. H2 points at remote code to correct.
    Uses copy-down pattern: m copies [H2] to local CWL, correction runs
    locally, M uncomputes the copy, j writes the correction mask back.

    Initial head positions (relative to slot start):
      H0 = (GP_ROW, CWL=2)   — worker head
      H1 = (GP_ROW, CWL=2)   — reference head
      H2 = (REMOTE_ROW, col)  — scan head on remote data
      CL = (GP_ROW, ROT=8)   — rotation control
      GP = (GP_ROW, EV=0)    — waste/evidence pointer

    Returns: list of opchar strings
    """
    gb = GadgetBuilder()

    # ── Copy-in: m copies [H2] to [H0] at CWL ──
    # H0 at CWL (=0), H2 at remote codeword.
    # After: [H0] = payload(remote), H2 unchanged.
    gb.emit('m')

    # ── GP: EV → PA (for z ops later) ──
    gb.emit(']')
    gb.gp_col = DSL_PA

    # ── Phase A: Overall parity via Y ──
    # H0 on PA, H1 on CWL. Y at rotations 0..15.
    gb.move_h0_col(DSL_PA)          # CWL(2) → PA(1): W×1
    gb.xor_accumulate_bits(list(range(16)))   # CL: 0→15

    # ── Phase B: z-extract PA.bit0 → EV ──
    gb.move_h0_col(DSL_EV)          # PA(1) → EV(0): W×1
    gb.emit('z')                     # EV.bit0 ← PA.bit0; PA.bit0 ← 0

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
    # H0 to CWL. m: [H0] ^= [H2] → CWL = cw ^ cw = 0 (XOR self-inverse).
    # MUST happen before write-back (H2 still has original value).
    gb.move_h0_col(DSL_CWL)         # S0(3) → CWL(2): W×1
    gb.emit('m')                     # [H0] ^= [H2] → CWL = 0

    # ── Write correction to remote ──
    # H0 to EV (the correction mask). j: [H2] ^= [H0].
    gb.move_h0_col(DSL_EV)          # CWL(2) → EV(0): W×2
    gb.emit('j')                     # [H2] ^= [H0] → remote corrected

    # ── Phase F: Cleanup z+x ──
    # H0 at EV (already), H1 to PA. z+x merges residuals.
    gb.move_h1_col(DSL_PA)          # CWL(2) → PA(1): w×1
    gb.emit('z')                     # swap bit0 of EV with PA
    gb.emit('x')                     # EV ^= PA

    # ── GP: PA → EV ──
    gb.emit('[')
    gb.gp_col = DSL_EV

    # ── Epilogue: return H0, H1 to CWL ──
    gb.move_h0_col(DSL_CWL)         # EV(0) → CWL(2): E×2
    gb.move_h1_col(DSL_CWL)         # PA(1) → CWL(2): e×1

    gadget_ops = gb.pos()

    # ── Head advance: all 5 heads east by 1 ──
    gb.emit('E')      # H0 east
    gb.emit('e')      # H1 east
    gb.emit(']')      # GP east
    gb.emit('>')      # CL east
    gb.emit('a')      # H2 east (next remote codeword)

    total_ops = gb.pos()

    return gb.ops


def make_h2_test_torus(cases, first_cw_col=2):
    """Build a linear torus to test H2-based correction.

    Layout:
      Row 0: REMOTE — test codewords (H2 scans here)
      Row 1: CODE   — gadget opcodes (IP goes east, linear)
      Row 2: GP     — scratch cells (all zero)

    cases: list of (payload_11bit, error_bit_or_None)

    Returns: (sim, expected_results, cycle_length)
    """
    code_ops = build_h2_correction_gadget()
    op_values = [OP[ch] for ch in code_ops]
    n_ops = len(op_values)
    n = len(cases)

    # Grid width must fit the code AND the scratch cells
    # GP scratch extends from first_cw_col - 1 (EV) to first_cw_col - 1 + n + DSL_ROT
    gp_start_col = first_cw_col - DSL_CWL   # EV at col (first_cw_col - 2)
    max_scratch_col = gp_start_col + n + DSL_ROT
    cols = max(n_ops + 2, max_scratch_col + 2, first_cw_col + n + DSL_SLOT_WIDTH)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # Place gadget code on CODE_ROW (linear, Hamming-encoded)
    for i, opval in enumerate(op_values):
        sim.grid[sim._to_flat(CODE_ROW, i)] = hamming_encode(opval)

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

    sim.h0 = sim._to_flat(GP_ROW, gp_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(GP_ROW, gp_start_col + DSL_CWL)
    sim.h2 = sim._to_flat(REMOTE_ROW, first_cw_col)
    sim.cl = sim._to_flat(GP_ROW, gp_start_col + DSL_ROT)
    sim.gp = sim._to_flat(GP_ROW, gp_start_col + DSL_EV)

    # Cycle length = cols (IP wraps around the torus)
    cycle_length = cols

    return sim, expected, cycle_length, gp_start_col


def run_h2_test(cases, verbose=True, check_reverse=True):
    """Test H2-based correction on remote codewords.

    Returns: bool (all tests passed)
    """
    n = len(cases)
    first_cw_col = 2
    sim, expected, cycle_length, gp_start_col = make_h2_test_torus(
        cases, first_cw_col=first_cw_col)

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
    final_cw = gp_start_col + DSL_CWL + n
    final_gp = gp_start_col + DSL_EV + n
    final_rot = gp_start_col + DSL_ROT + n
    final_h2 = first_cw_col + n

    heads_ok = True
    h0_exp = sim._to_flat(GP_ROW, final_cw)
    h1_exp = sim._to_flat(GP_ROW, final_cw)
    gp_exp = sim._to_flat(GP_ROW, final_gp)
    cl_exp = sim._to_flat(GP_ROW, final_rot)
    h2_exp = sim._to_flat(REMOTE_ROW, final_h2)

    if sim.h0 != h0_exp or sim.h1 != h1_exp or sim.gp != gp_exp \
            or sim.cl != cl_exp or sim.h2 != h2_exp:
        heads_ok = False
    if verbose or not heads_ok:
        print(f"    Final heads: H0={sim.h0 % sim.cols} H1={sim.h1 % sim.cols}"
              f" GP={sim.gp % sim.cols} CL={sim.cl % sim.cols}"
              f" H2=({sim.h2 // sim.cols},{sim.h2 % sim.cols})"
              f" {'ok' if heads_ok else 'FAIL'}")
        if not heads_ok:
            print(f"      Expected: H0={final_cw} H1={final_cw}"
                  f" GP={final_gp} CL={final_rot}"
                  f" H2=(0,{final_h2})")
    all_ok &= heads_ok

    # GP dirty trail
    if verbose:
        dirty = 0
        for i in range(n):
            ev_col = gp_start_col + DSL_EV + i
            if sim.grid[sim._to_flat(GP_ROW, ev_col)] != 0:
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

        # Check GP row clean
        for col in range(sim.cols):
            v = sim.grid[sim._to_flat(GP_ROW, col)]
            if v != 0:
                reverse_ok = False
                if verbose:
                    print(f"    [REVERSE] GP col {col}: 0x{v:04x} != 0")
                break

        if verbose:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_h2_single_correction():
    """Test: single codeword corrected via H2."""
    print("=== H2 single correction ===")
    # payload=42, flip bit 3
    ok = run_h2_test([(42, 3)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_no_error():
    """Test: no error case (H2 should be no-op)."""
    print("=== H2 no error ===")
    ok = run_h2_test([(42, None)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_bit0_error():
    """Test: bit-0 error (overall parity bit)."""
    print("=== H2 bit-0 error ===")
    ok = run_h2_test([(42, 0)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_h2_multiple():
    """Test: multiple codewords with various errors."""
    print("=== H2 multiple codewords ===")
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
    print("=== H2 all 16 error positions ===")
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
    print("=== H2 random (20 codewords) ===")
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


if __name__ == '__main__':
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

    if all_ok:
        print("=" * 60)
        print("ALL H2 CORRECTION TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

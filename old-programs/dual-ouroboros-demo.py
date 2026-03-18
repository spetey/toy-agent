#!/usr/bin/env python3
"""
dual-ouroboros-demo.py — Two-bit error correction via neighbor copy.

Extends the Hamming(16,11) SECDED correction gadget with 2-bit error
detection and correction by copying from a neighbor gadget.

ARCHITECTURE:
  Two identical ouroboroi (A and B) stacked:
    Row 0: Ouroboros A, east code  (corrects row 1)
    Row 1: Ouroboros A, west code  (corrects row 0)
    Row 2: EX for A
    Row 3: Ouroboros B, east code  (corrects row 4)
    Row 4: Ouroboros B, west code  (corrects row 3)
    Row 5: EX for B

  When A corrects a cell on row 1 and detects a 2-bit error (syndrome != 0
  but p_all = 0), it copies the corresponding cell from B's row 4 (3 rows
  south via IX).

2-BIT DETECTION (inserted between Phase D and Phase C'):
  After Phase D, the barrel shifter has produced:
    - EV = correction mask (nonzero for 1-bit, 0 for clean/2-bit)
    - S0-S3 have syndrome bits in bit0

  Step 1: Pack syndrome-OR into ACC via z+l on S0-S3.
          ACC = s0<<3 | s1<<2 | s2<<1 | s3.
          Nonzero iff syndrome != 0 (any error present).
          S0-S3.bit0 = 0 (extracted by z).

  Step 2: Copy neighbor via IX jump:
          h*delta (IX to neighbor row)
          m: NBR ^= [IX] (copy neighbor value)
          H*delta (IX back)
          x: NBR ^= CWL (XOR with our codeword = copy_mask)

  Step 3: Fredkin gate F: if ACC != 0, swap EV <-> NBR.
          For 2-bit: EV gets copy_mask, NBR gets 0 (old EV).
          For 1-bit: EV gets copy_mask (replaces barrel-shift mask).
          For clean: ACC=0, no swap, EV stays 0.

  Step 4: Undo syndrome-OR pack (z+r in reverse).
          Restores S0-S3.bit0 and zeros ACC.

  Phase C' then proceeds normally (S0-S3 fully restored).

NOTE: Without P0 filter, 1-bit errors also use the neighbor copy instead
of the barrel-shift correction. This works if the neighbor is clean but
may take 2 sweeps if the neighbor also has an error on the same cell.
The P0 filter can be added later for optimal single-sweep 1-bit correction.

SLOT LAYOUT:
  [EV PA CWL S0 S1 S2 S3 SCR ROT ACC NBR]
  offsets: 0  1   2  3  4  5  6   7   8   9  10
  SLOT_WIDTH = 11

  Advance = SLOT_WIDTH (11 steps per head on EX, 1 step IX).
  This avoids dirty-cell overlap between consecutive cycles.
  NBR is dirty for 1-bit error cases (gets old barrel-shift mask).

Run tests:  python3 programs/dual-ouroboros-demo.py
"""

import sys
import os
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, encode_opcode, OPCODE_PAYLOADS)

# Import from dual-gadget-demo.py
_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

GadgetBuilder = dgd.GadgetBuilder
SYNDROME_POSITIONS = dgd.SYNDROME_POSITIONS

from hamming import encode, inject_error, decode

OP = OPCODES

# ── Extended slot layout for 2-bit detection ──
DSL2_EV   = 0   # evidence / waste
DSL2_PA   = 1   # overall parity
DSL2_CWL  = 2   # local copy of remote codeword
DSL2_S0   = 3   # syndrome bit 0
DSL2_S1   = 4   # syndrome bit 1
DSL2_S2   = 5   # syndrome bit 2
DSL2_S3   = 6   # syndrome bit 3
DSL2_SCR  = 7   # barrel shifter scratch
DSL2_ROT  = 8   # CL rotation counter
DSL2_ACC  = 9   # syndrome-OR accumulator
DSL2_NBR  = 10  # neighbor copy / copy mask
DSL2_SI   = [DSL2_S0, DSL2_S1, DSL2_S2, DSL2_S3]
DSL2_SLOT_WIDTH = 11


def build_h2_correction_gadget_2bit(neighbor_delta_rows=3):
    """Build IX correction gadget with 2-bit neighbor copy.

    All computation on the EX row. IX points at remote code to correct.
    When a 2-bit error is detected (syndrome != 0 but barrel shift gives
    no correction), copies the value from the neighbor gadget's
    corresponding cell.

    Args:
        neighbor_delta_rows: rows between our target row and the neighbor's
            corresponding row. Positive = south (h ops), negative = north.

    Returns: (ops, gadget_end_idx)
        ops: list of opchar strings (full gadget + advance)
        gadget_end_idx: index where the correction ops end (before advance)
    """
    gb = GadgetBuilder(h0_col=DSL2_CWL, h1_col=DSL2_CWL,
                       cl_col=DSL2_ROT, cl_payload=0, gp_col=DSL2_EV)

    # ── Copy-in: m copies [IX] to [H0] at CWL ──
    gb.emit('m')

    # ── Phase A: Overall parity via Y ──
    gb.move_h0_col(DSL2_PA)
    gb.xor_accumulate_bits(list(range(16)))

    # ── Phase B: z-extract PA.bit0 → EV ──
    gb.move_h0_col(DSL2_EV)
    gb.move_h1_col(DSL2_PA)          # CWL(2) → PA(1): w×1
    gb.emit('z')
    gb.move_h1_col(DSL2_CWL)        # PA(1) → CWL(2): e×1

    # ── Phase A': Y-uncompute PA ──
    gb.move_h0_col(DSL2_PA)
    gb.xor_accumulate_bits(list(range(15, -1, -1)))

    # ── Phase C: Syndrome computation via Y ──
    gb.move_h0_col(DSL2_S0)

    # s0: ascending
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])

    # s1: descending
    gb.move_h0_col(DSL2_S1)
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])

    # s2: ascending
    gb.move_h0_col(DSL2_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])

    # s3: descending
    gb.move_h0_col(DSL2_S3)
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    # ── Phase D: Barrel shifter ──
    gb.move_h0_col(DSL2_EV)
    gb.move_h1_col(DSL2_SCR)
    gb.move_cl_col(DSL2_S0)

    for i in range(4):
        if i > 0:
            gb.move_cl_col(DSL2_SI[i])
        shift = 1 << i
        gb.emit_n('l', shift)
        gb.emit('f')
        gb.emit_n('r', shift)
        gb.emit('f')

    # ═══════════════════════════════════════════════════════════════
    # 2-BIT DETECTION + NEIGHBOR COPY
    # ═══════════════════════════════════════════════════════════════
    # State: H0=EV(0), H1=SCR(7), CL@S3(6), EX@EV(0)

    # ── Step 1: Pack syndrome-OR into ACC ──
    # Move H0 to ACC, H1 to S0
    gb.move_h0_col(DSL2_ACC)      # EV(0) → ACC(9): E×9
    gb.move_h1_col(DSL2_S0)       # SCR(7) → S0(3): w×4

    # z+l packing: extract S0-S3 bit0 into ACC
    gb.emit('z')                   # ACC.bit0 ↔ S0.bit0
    gb.emit('l')                   # rotate ACC left
    gb.move_h1_col(DSL2_S1)       # e → S1
    gb.emit('z')                   # ACC.bit0 ↔ S1.bit0
    gb.emit('l')                   # rotate ACC left
    gb.move_h1_col(DSL2_S2)       # e → S2
    gb.emit('z')                   # ACC.bit0 ↔ S2.bit0
    gb.emit('l')                   # rotate ACC left
    gb.move_h1_col(DSL2_S3)       # e → S3
    gb.emit('z')                   # ACC.bit0 ↔ S3.bit0
    # State: ACC = s0<<3|s1<<2|s2<<1|s3, S0-S3.bit0=0
    # H0=ACC(9), H1@S3(6)

    # Shift packed value left 9: bits 0-3 → bits 9-12 (all data positions).
    # F's DATA_MASK strips Hamming parity positions {0,1,2,4,8} — without
    # this shift, syndrome bits at positions 0-2 would be invisible to F.
    # Shift by 3 is insufficient: bit1→bit4 is also a parity position.
    # Shift by 9: bit0→9(data), bit1→10(data), bit2→11(data), bit3→12(data).
    gb.emit_n('l', 9)

    # ── Step 2: Neighbor copy into NBR ──
    gb.move_h0_col(DSL2_NBR)      # ACC(9) → NBR(10): E×1
    gb.move_h1_col(DSL2_CWL)      # S3(6) → CWL(2): w×4

    # Move IX to neighbor row
    if neighbor_delta_rows > 0:
        gb.emit_n('h', neighbor_delta_rows)   # IX south
    else:
        gb.emit_n('H', -neighbor_delta_rows)  # IX north

    gb.emit('m')                   # [H0=NBR] ^= [IX=neighbor]

    # Move IX back
    if neighbor_delta_rows > 0:
        gb.emit_n('H', neighbor_delta_rows)   # IX north (back)
    else:
        gb.emit_n('h', -neighbor_delta_rows)

    gb.emit('x')                   # [H0=NBR] ^= [H1=CWL] → copy_mask
    # State: NBR=copy_mask, H0=NBR(10), H1=CWL(2)

    # ── Step 3: Fredkin gate ──
    gb.move_cl_col(DSL2_ACC)      # S3(6) → ACC(9): >×3
    gb.move_h0_col(DSL2_EV)       # NBR(10) → EV(0): W×10
    gb.move_h1_col(DSL2_NBR)      # CWL(2) → NBR(10): e×8
    gb.emit('F')                   # if [CL=ACC]≠0: swap EV ↔ NBR
    # 2-bit: EV=copy_mask, NBR=0
    # 1-bit: EV=copy_mask (replaces barrel-shift), NBR=old_EV
    # Clean: no swap, EV=0, NBR=0

    # ── Step 4: Undo syndrome-OR pack ──
    # Restore S0-S3.bit0 from ACC (Fredkin didn't modify ACC)
    gb.move_h0_col(DSL2_ACC)      # EV(0) → ACC(9): E×9

    # Shift packed value right 9: undo the left-9 shift before unpacking
    gb.emit_n('r', 9)

    # H1 at NBR(10), move to S3 for z unpacking
    gb.move_h1_col(DSL2_S3)       # NBR(10) → S3(6): w×4
    gb.emit('z')                   # ACC.bit0 ↔ S3.bit0 → restore S3
    gb.emit('r')                   # rotate ACC right
    gb.move_h1_col(DSL2_S2)       # w → S2
    gb.emit('z')                   # restore S2
    gb.emit('r')
    gb.move_h1_col(DSL2_S1)       # w → S1
    gb.emit('z')                   # restore S1
    gb.emit('r')
    gb.move_h1_col(DSL2_S0)       # w → S0
    gb.emit('z')                   # restore S0
    # After: ACC=0, S0-S3 restored
    # H0=ACC(9), H1@S0(3)

    # ═══════════════════════════════════════════════════════════════
    # Phase C': Y-uncompute S0-S3
    # ═══════════════════════════════════════════════════════════════
    gb.move_h0_col(DSL2_S3)       # ACC(9) → S3(6): W×3
    gb.move_h1_col(DSL2_CWL)      # S0(3) → CWL(2): w×1
    gb.move_cl_col(DSL2_ROT)      # ACC(9) → ROT(8): <×1
    gb.cl_payload = 8              # [ROT] = 8 (from Phase C)

    # Uncompute s3: ascending (CL: 8→15)
    gb.xor_accumulate_bits([8, 9, 10, 11, 12, 13, 14, 15])

    # Uncompute s2: descending (CL: 15→4)
    gb.move_h0_col(DSL2_S2)
    gb.xor_accumulate_bits([15, 14, 13, 12, 7, 6, 5, 4])

    # Uncompute s1: ascending (CL: 4→15)
    gb.move_h0_col(DSL2_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7, 10, 11, 14, 15])

    # Uncompute s0: descending (CL: 15→1)
    gb.move_h0_col(DSL2_S0)
    gb.xor_accumulate_bits([15, 13, 11, 9, 7, 5, 3, 1])

    # Clean CL: payload 1 → 0
    gb.set_cl_payload(0)

    # ── Uncompute local copy (M) ──
    gb.move_h0_col(DSL2_CWL)      # S0(3) → CWL(2): W×1
    gb.emit('m')                   # [H0] ^= [IX] → CWL = 0

    # ── Write correction to remote (j) ──
    gb.move_h0_col(DSL2_EV)       # CWL(2) → EV(0): W×2
    gb.emit('j')                   # [IX] ^= [H0] → remote corrected

    # ── Phase F: Cleanup z+x ──
    gb.move_h1_col(DSL2_PA)       # CWL(2) → PA(1): w×1
    gb.emit('z')                   # swap bit0 of EV with H1@PA
    gb.emit('x')                   # EV ^= PA

    # ── Epilogue: return H0, H1 to CWL ──
    gb.move_h0_col(DSL2_CWL)      # EV(0) → CWL(2): E×2
    gb.move_h1_col(DSL2_CWL)      # PA(1) → CWL(2): e×1

    gadget_end = gb.pos()

    # ── Advance: all heads east by SLOT_WIDTH on EX, IX east by 1 ──
    gb.emit_n('E', DSL2_SLOT_WIDTH)    # H0 east ×11
    gb.emit_n('e', DSL2_SLOT_WIDTH)    # H1 east ×11
    gb.emit_n(']', DSL2_SLOT_WIDTH)    # EX east ×11
    gb.emit_n('>', DSL2_SLOT_WIDTH)    # CL east ×11
    gb.emit('a')                       # IX east ×1

    return gb.ops, gadget_end


# ═══════════════════════════════════════════════════════════════════
# Test grid builders
# ═══════════════════════════════════════════════════════════════════

# Layout for linear test (like dual-gadget-demo but with neighbor row):
#   Row 0: TARGET — codewords to correct (IX scans here)
#   Row 1: CODE   — gadget opcodes (IP goes east)
#   Row 2: EX     — scratch cells
#   Row 3: NEIGHBOR — clean codewords (reference copies)
TARGET_ROW = 0
CODE_ROW = 1
GP_ROW_LINEAR = 2
NEIGHBOR_ROW = 3
N_ROWS_LINEAR = 4


def make_2bit_test_torus(cases, first_cw_col=2, neighbor_delta=3):
    """Build a linear torus for testing 2-bit correction.

    Layout:
      Row 0: TARGET — test codewords (some with 2-bit errors)
      Row 1: CODE   — gadget opcodes
      Row 2: EX     — scratch cells
      Row 3: NEIGHBOR — clean reference codewords

    IX starts on row 0. Neighbor is 3 rows south (delta=3).

    cases: list of (payload, error_bits)
        error_bits: None (clean), int (1-bit), or (int,int) (2-bit)

    Returns: (sim, expected_results, steps_per_cycle, ex_start_col)
    """
    ops, gadget_end = build_h2_correction_gadget_2bit(neighbor_delta)
    op_values = [OP[ch] for ch in ops]
    n_ops = len(op_values)
    n = len(cases)

    # EX scratch layout
    ex_start_col = first_cw_col - DSL2_CWL
    max_gp_col = ex_start_col + (n - 1) * DSL2_SLOT_WIDTH + DSL2_NBR
    cols = max(n_ops + 2, max_gp_col + 2, first_cw_col + n + 2)

    sim = FB2DSimulator(rows=N_ROWS_LINEAR, cols=cols)

    # Place gadget code on CODE_ROW (Hamming-encoded)
    for i, opval in enumerate(op_values):
        sim.grid[sim._to_flat(CODE_ROW, i)] = encode_opcode(opval)

    # Place test codewords on TARGET_ROW and clean copies on NEIGHBOR_ROW
    expected = []
    for i, (payload, error_bits) in enumerate(cases):
        cw = encode(payload)
        expected.append(cw)

        # Target: inject error(s)
        if error_bits is None:
            bad = cw
        elif isinstance(error_bits, int):
            bad = inject_error(cw, error_bits)
        else:
            # 2-bit error: inject two bit flips
            bad = cw
            for bit in error_bits:
                bad = inject_error(bad, bit)
        sim.grid[sim._to_flat(TARGET_ROW, first_cw_col + i)] = bad

        # Neighbor: clean copy (no errors)
        sim.grid[sim._to_flat(NEIGHBOR_ROW, first_cw_col + i)] = cw

    # Initial head positions
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1    # East

    sim.h0 = sim._to_flat(GP_ROW_LINEAR, ex_start_col + DSL2_CWL)
    sim.h1 = sim._to_flat(GP_ROW_LINEAR, ex_start_col + DSL2_CWL)
    sim.ix = sim._to_flat(TARGET_ROW, first_cw_col)
    sim.cl = sim._to_flat(GP_ROW_LINEAR, ex_start_col + DSL2_ROT)
    sim.ex = sim._to_flat(GP_ROW_LINEAR, ex_start_col + DSL2_EV)

    # Cycle length = cols (IP wraps around torus)
    steps_per_cycle = cols

    return sim, expected, steps_per_cycle, ex_start_col


def run_2bit_test(cases, verbose=True, check_reverse=True,
                  neighbor_delta=3):
    """Test 2-bit correction on codewords.

    cases: list of (payload, error_bits)
        error_bits: None, int (1-bit), or tuple (2-bit)

    Returns: bool
    """
    n = len(cases)
    first_cw_col = 2

    sim, expected, steps_per_cycle, gp_start = make_2bit_test_torus(
        cases, first_cw_col=first_cw_col, neighbor_delta=neighbor_delta)

    ex_row = GP_ROW_LINEAR

    # Run N cycles (one per codeword)
    total_steps = n * steps_per_cycle
    for _ in range(total_steps):
        sim.step()

    # Check results
    all_ok = True
    for i in range(n):
        col = first_cw_col + i
        result = sim.grid[sim._to_flat(TARGET_ROW, col)]
        ok = (result == expected[i])
        if verbose or not ok:
            payload, error_bits = cases[i]
            if error_bits is None:
                err_desc = "none"
            elif isinstance(error_bits, int):
                err_desc = f"1-bit:{error_bits}"
            else:
                err_desc = f"2-bit:{error_bits}"
            print(f"    CW[{i}] col={col}: payload={payload} err={err_desc}"
                  f" result=0x{result:04x} expected=0x{expected[i]:04x}"
                  f" {'ok' if ok else 'FAIL'}")
        all_ok &= ok

    # Check head positions
    heads_ok = True
    final_slot_start = gp_start + n * DSL2_SLOT_WIDTH
    h0_exp = sim._to_flat(ex_row, final_slot_start + DSL2_CWL)
    h1_exp = sim._to_flat(ex_row, final_slot_start + DSL2_CWL)
    gp_exp = sim._to_flat(ex_row, final_slot_start + DSL2_EV)
    cl_exp = sim._to_flat(ex_row, final_slot_start + DSL2_ROT)
    h2_exp = sim._to_flat(TARGET_ROW, first_cw_col + n)

    if (sim.h0 != h0_exp or sim.h1 != h1_exp or sim.ex != gp_exp
            or sim.cl != cl_exp or sim.ix != h2_exp):
        heads_ok = False
    if verbose or not heads_ok:
        print(f"    Final heads: H0={sim.h0 % sim.cols} H1={sim.h1 % sim.cols}"
              f" EX={sim.ex % sim.cols} CL={sim.cl % sim.cols}"
              f" IX=({sim.ix // sim.cols},{sim.ix % sim.cols})"
              f" {'ok' if heads_ok else 'FAIL'}")
        if not heads_ok:
            print(f"      Expected: H0={final_slot_start + DSL2_CWL}"
                  f" H1={final_slot_start + DSL2_CWL}"
                  f" EX={final_slot_start + DSL2_EV}"
                  f" CL={final_slot_start + DSL2_ROT}"
                  f" IX=(0,{first_cw_col + n})")
    all_ok &= heads_ok

    if verbose:
        ops, _ = build_h2_correction_gadget_2bit(neighbor_delta)
        print(f"    Grid: {sim.rows}x{sim.cols}, {steps_per_cycle} steps/cycle,"
              f" {n} cycles, {total_steps} total steps")
        print(f"    Gadget: {len(ops)} ops (slot width={DSL2_SLOT_WIDTH})")

    # Reverse check
    if check_reverse:
        for _ in range(total_steps):
            sim.step_back()

        reverse_ok = True
        for i in range(n):
            col = first_cw_col + i
            payload, error_bits = cases[i]
            cw = encode(payload)
            if error_bits is None:
                orig = cw
            elif isinstance(error_bits, int):
                orig = inject_error(cw, error_bits)
            else:
                orig = cw
                for bit in error_bits:
                    orig = inject_error(orig, bit)
            result = sim.grid[sim._to_flat(TARGET_ROW, col)]
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
                    print(f"    [REVERSE] EX col {col}: 0x{v:04x}")
                break

        if verbose:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_2bit_single():
    """Test: single 2-bit error corrected via neighbor copy."""
    print("=== 2-bit: single 2-bit error ===")
    # 2-bit error: flip bits 3 and 7
    ok = run_2bit_test([(42, (3, 7))])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_2bit_clean():
    """Test: clean cell (no error, should be no-op)."""
    print("=== 2-bit: clean cell ===")
    ok = run_2bit_test([(42, None)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_2bit_1bit():
    """Test: 1-bit error (should still correct via neighbor copy)."""
    print("=== 2-bit: 1-bit error (neighbor copy) ===")
    ok = run_2bit_test([(42, 5)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_2bit_mixed():
    """Test: mix of clean, 1-bit, and 2-bit errors."""
    print("=== 2-bit: mixed errors ===")
    cases = [
        (42, None),       # clean
        (100, 3),         # 1-bit
        (200, (1, 5)),    # 2-bit
        (0, None),        # clean
        (2047, (0, 15)),  # 2-bit (extreme bits)
        (500, 10),        # 1-bit
    ]
    ok = run_2bit_test(cases)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_2bit_all_1bit_positions():
    """Test: all 16 single-bit error positions (via neighbor copy)."""
    print("=== 2-bit: all 16 1-bit positions ===")
    cases = [(42, bit) for bit in range(16)]
    ok = run_2bit_test(cases, verbose=False)
    if ok:
        print(f"  All 16 positions: PASS")
    else:
        run_2bit_test(cases, verbose=True)
        print(f"  FAIL")
    return ok


def test_2bit_various_pairs():
    """Test: various 2-bit error pairs."""
    print("=== 2-bit: various 2-bit error pairs ===")
    cases = [
        (42, (0, 1)),
        (42, (0, 15)),
        (42, (3, 7)),
        (42, (5, 10)),
        (42, (7, 8)),
        (42, (14, 15)),
    ]
    ok = run_2bit_test(cases, verbose=True)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_2bit_random():
    """Test: random payloads with random 1-bit and 2-bit errors."""
    print("=== 2-bit: random (20 cases) ===")
    random.seed(42)
    cases = []
    for _ in range(20):
        payload = random.randint(0, 2047)
        r = random.random()
        if r < 0.2:
            error_bits = None
        elif r < 0.6:
            error_bits = random.randint(0, 15)
        else:
            b1 = random.randint(0, 15)
            b2 = random.randint(0, 15)
            while b2 == b1:
                b2 = random.randint(0, 15)
            error_bits = (b1, b2)
        cases.append((payload, error_bits))
    ok = run_2bit_test(cases, verbose=False)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


# ═══════════════════════════════════════════════════════════════════
# Save demo .fb2d file
# ═══════════════════════════════════════════════════════════════════

def save_demo(payload=42, error_bits=(3, 7), filename=None):
    """Save a 2-bit correction demo as a .fb2d state file.

    Creates a torus with a single codeword (with error) on the TARGET row,
    a clean copy on the NEIGHBOR row, and the correction gadget on the CODE
    row. Load in the interactive simulator to step through correction.

    Args:
        payload: data payload to encode (0-2047)
        error_bits: None, int (1-bit), or tuple (2-bit)
        filename: output path (default: programs/dual-ouroboros-2bit-demo.fb2d)
    """
    cases = [(payload, error_bits)]
    sim, expected, cycle_length, gp_start = make_2bit_test_torus(
        cases, first_cw_col=2)

    if filename is None:
        filename = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'dual-ouroboros-2bit-demo.fb2d')

    sim.save_state(filename)

    from hamming import encode, inject_error
    cw = encode(payload)
    if error_bits is None:
        bad = cw
        err_desc = "none"
    elif isinstance(error_bits, int):
        bad = inject_error(cw, error_bits)
        err_desc = f"1-bit:{error_bits}"
    else:
        bad = cw
        for bit in error_bits:
            bad = inject_error(bad, bit)
        err_desc = f"2-bit:{error_bits}"

    ops, gadget_end = build_h2_correction_gadget_2bit()

    print(f"Saved: {filename}")
    print(f"  Grid: {sim.rows}×{sim.cols}")
    print(f"    Row 0: TARGET (codewords to correct, IX scans here)")
    print(f"    Row 1: CODE   (gadget opcodes, IP goes east)")
    print(f"    Row 2: EX     (scratch cells)")
    print(f"    Row 3: NEIGHBOR (clean reference copies)")
    print()
    print(f"  Codeword: payload={payload} (0x{cw:04x}), error={err_desc}"
          f" → 0x{bad:04x}")
    print(f"  Expected after 1 cycle ({cycle_length} steps): 0x{expected[0]:04x}")
    print(f"  Gadget: {len(ops)} ops ({gadget_end} correction"
          f" + {len(ops) - gadget_end} advance)")
    print(f"  Slot width: {DSL2_SLOT_WIDTH}")
    print()
    print(f"  H0,H1 at (2,{gp_start + DSL2_CWL}) on EX row (CWL slot)")
    print(f"  IX at (0,2) on TARGET row")
    print(f"  CL at (2,{gp_start + DSL2_ROT}) on EX row (ROT slot)")
    print(f"  EX at (2,{gp_start + DSL2_EV}) on EX row (EV slot)")
    print()
    print(f"In the simulator:")
    print(f"  python3 fb2d.py")
    print(f"  > load dual-ouroboros-2bit-demo")
    print(f"  > s {cycle_length}    # run one correction cycle")
    print(f"  > show")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if '--save' in sys.argv:
        payload = 42
        error_bits = (3, 7)
        for i, arg in enumerate(sys.argv):
            if arg == '--payload' and i + 1 < len(sys.argv):
                payload = int(sys.argv[i + 1])
            if arg == '--error' and i + 1 < len(sys.argv):
                val = sys.argv[i + 1]
                if ',' in val:
                    parts = val.split(',')
                    error_bits = (int(parts[0]), int(parts[1]))
                else:
                    error_bits = int(val)
        save_demo(payload=payload, error_bits=error_bits)
        sys.exit(0)

    ops, gadget_end = build_h2_correction_gadget_2bit()
    print(f"2-bit gadget: {len(ops)} ops total"
          f" ({gadget_end} correction + {len(ops) - gadget_end} advance)")
    print(f"Slot width: {DSL2_SLOT_WIDTH}")
    print()

    all_ok = True
    all_ok &= test_2bit_clean()
    print()
    all_ok &= test_2bit_1bit()
    print()
    all_ok &= test_2bit_single()
    print()
    all_ok &= test_2bit_mixed()
    print()
    all_ok &= test_2bit_all_1bit_positions()
    print()
    all_ok &= test_2bit_various_pairs()
    print()
    all_ok &= test_2bit_random()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL 2-BIT CORRECTION TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

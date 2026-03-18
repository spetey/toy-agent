#!/usr/bin/env python3
"""
hamming-demo.py — SECDED Hamming(8,4) error correction in fb2d.

Demonstrates single-bit error correction and double-bit error detection
using only fb2d reversible operations, executed on the actual fb2d simulator.

APPROACH:
  All opcodes are laid out sequentially on a code row (with appropriate
  head movements between them). The IP walks East through the code row,
  executing each opcode in order. This means step_back() can reverse
  through the exact same opcode sequence — full reversibility.

ALGORITHM:
  Phase 1 — SYNDROME: For each syndrome bit (s0, s1, s2), accumulate the
  XOR of specific bit positions in the codeword. Method: rotate the codeword
  right to bring target bit to position 0, XOR full byte into scratch cell,
  rotate back. Only bit 0 of each scratch cell matters.

  Phase 2 — OVERALL PARITY: XOR all 8 bits of codeword into a parity cell,
  using the same rotate-XOR-unrotate pattern for all 8 bit positions.

  Phase 3 — CORRECT: If single error (p_all=1), rotate CW right by syndrome
  positions, XOR with fix cell (=1) to flip bit 0, rotate left back.

  All operations: r (rotate right), l (rotate left), x (XOR bytes),
  E/W (move H0), e/w (move H1).

GRID LAYOUT:
  Row 0 (DATA):  cw  s0  s1  s2  pa  fix
                  0   1   2   3   4   5
  Row 1 (CODE):  [opcodes laid out sequentially...]
  Row 2 (EX):    0 0 0 0 ...

  H0 points to the cell being operated on (CW or scratch).
  H1 points to the XOR source.
  Heads start at (DATA, CW). Code runs left-to-right on row 1.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

# Import from hamming.py
from hamming import encode, inject_error, inject_double_error, decode


# ── Column assignments on DATA row ──
CW  = 0   # codeword
S0  = 1   # syndrome bit 0 accumulator
S1  = 2   # syndrome bit 1 accumulator
S2  = 3   # syndrome bit 2 accumulator
PA  = 4   # overall parity accumulator
FIX = 5   # constant 1 (for XOR bit flip)

DATA_ROW = 0
CODE_ROW = 1
EX_ROW = 2


def build_opcode_sequence(syndrome_val, do_correct):
    """Build the complete opcode sequence for syndrome + optional correction.

    Returns a list of (opchar, description) tuples for debugging.

    Head convention:
      H0 = cell to operate on (r/l/x target is [H0])
      H1 = XOR source (x does [H0] ^= [H1])

    Head positions at start: H0=(DATA,CW), H1=(DATA,CW)
    """
    ops = []

    def emit(opchar, desc=""):
        ops.append((opchar, desc))

    def move_h0_to(target_col, current_col):
        """Move H0 east/west from current_col to target_col on DATA row."""
        diff = target_col - current_col
        for _ in range(abs(diff)):
            emit('E' if diff > 0 else 'W')
        return target_col

    def move_h1_to(target_col, current_col):
        """Move H1 east/west from current_col to target_col on DATA row."""
        diff = target_col - current_col
        for _ in range(abs(diff)):
            emit('e' if diff > 0 else 'w')
        return target_col

    def rotate_h0_right(n, desc=""):
        for _ in range(n):
            emit('r', desc)

    def rotate_h0_left(n, desc=""):
        for _ in range(n):
            emit('l', desc)

    # Track head positions
    h0_col = CW
    h1_col = CW

    # ── Phase 1: Syndrome computation ──

    # s0: XOR of bits {1, 3, 5, 7} of codeword
    # H0 on CW, H1 on S0
    h1_col = move_h1_to(S0, h1_col)  # H1 → S0
    for bit_pos in [1, 3, 5, 7]:
        # H0 should be on CW
        assert h0_col == CW
        rotate_h0_right(bit_pos, f"rot CW right {bit_pos} for s0")
        # Move H0 to S0, H1 stays on CW? No — x does [H0]^=[H1].
        # We want S0 ^= rotated_CW. So H0=S0, H1=CW.
        # But we just rotated CW with H0 on CW. Now we need to switch:
        # Move H0 from CW to S0, and H1 from S0 to CW.
        h0_col = move_h0_to(S0, h0_col)  # H0 → S0
        h1_col = move_h1_to(CW, h1_col)  # H1 → CW
        emit('x', f"S0 ^= rotated_CW (bit {bit_pos})")
        # Move H0 back to CW to un-rotate
        h0_col = move_h0_to(CW, h0_col)  # H0 → CW
        h1_col = move_h1_to(S0, h1_col)  # H1 → S0
        rotate_h0_left(bit_pos, f"un-rot CW left {bit_pos}")

    # s1: XOR of bits {2, 3, 6, 7}
    h1_col = move_h1_to(S1, h1_col)  # H1 → S1
    for bit_pos in [2, 3, 6, 7]:
        assert h0_col == CW
        rotate_h0_right(bit_pos, f"rot CW right {bit_pos} for s1")
        h0_col = move_h0_to(S1, h0_col)
        h1_col = move_h1_to(CW, h1_col)
        emit('x', f"S1 ^= rotated_CW (bit {bit_pos})")
        h0_col = move_h0_to(CW, h0_col)
        h1_col = move_h1_to(S1, h1_col)
        rotate_h0_left(bit_pos, f"un-rot CW left {bit_pos}")

    # s2: XOR of bits {4, 5, 6, 7}
    h1_col = move_h1_to(S2, h1_col)
    for bit_pos in [4, 5, 6, 7]:
        assert h0_col == CW
        rotate_h0_right(bit_pos, f"rot CW right {bit_pos} for s2")
        h0_col = move_h0_to(S2, h0_col)
        h1_col = move_h1_to(CW, h1_col)
        emit('x', f"S2 ^= rotated_CW (bit {bit_pos})")
        h0_col = move_h0_to(CW, h0_col)
        h1_col = move_h1_to(S2, h1_col)
        rotate_h0_left(bit_pos, f"un-rot CW left {bit_pos}")

    # ── Phase 2: Overall parity ──
    # PA = XOR of all 8 bits of CW, accumulated at bit 0.
    # Rotate CW right by each bit position 0-7, XOR into PA, rotate back.
    h1_col = move_h1_to(PA, h1_col)
    for bit_pos in range(8):
        assert h0_col == CW
        if bit_pos > 0:
            rotate_h0_right(bit_pos, f"rot CW right {bit_pos} for PA")
        h0_col = move_h0_to(PA, h0_col)
        h1_col = move_h1_to(CW, h1_col)
        emit('x', f"PA ^= rotated_CW (bit {bit_pos})")
        h0_col = move_h0_to(CW, h0_col)
        h1_col = move_h1_to(PA, h1_col)
        if bit_pos > 0:
            rotate_h0_left(bit_pos, f"un-rot CW left {bit_pos}")

    # ── Phase 3: Correction ──
    # If do_correct is True, apply correction for given syndrome_val.
    # In a full fb2d program, this would use a bounded loop to rotate
    # by a variable amount. For this demo, we bake in the syndrome value.
    if do_correct and syndrome_val >= 0:
        # Rotate CW right by syndrome_val positions
        if syndrome_val > 0:
            rotate_h0_right(syndrome_val, f"rot CW right by syndrome={syndrome_val}")
        # XOR with FIX cell (contains 1) to flip bit 0
        h0_col = move_h0_to(CW, h0_col)  # ensure H0 on CW (should be)
        h1_col = move_h1_to(FIX, h1_col)  # H1 → FIX
        emit('x', "CW ^= FIX (flip bit 0)")
        # Rotate back
        if syndrome_val > 0:
            rotate_h0_left(syndrome_val, f"un-rot CW left by syndrome={syndrome_val}")

    return ops


def run_correction(codeword, verbose=False):
    """Run Hamming SECDED correction on a codeword using the fb2d simulator.

    Returns (result_cw, syndrome, p_all_err, forward_steps, reverse_ok).
    """
    # First pass: compute syndrome without correction to learn syndrome value
    # We need syndrome to know how much to rotate for correction.
    # Build syndrome-only opcode sequence
    syn_ops = build_opcode_sequence(syndrome_val=-1, do_correct=False)
    syn_opcodes = [op for op, _ in syn_ops]

    # Need enough columns for data cells + opcode sequence
    n_data_cols = 6  # CW, S0, S1, S2, PA, FIX
    n_code_cols = len(syn_opcodes) + 2  # +2 for padding
    cols = max(n_data_cols, n_code_cols) + 4

    sim = FB2DSimulator(rows=3, cols=cols)

    # Place data
    sim.grid[sim._to_flat(DATA_ROW, CW)] = codeword
    sim.grid[sim._to_flat(DATA_ROW, FIX)] = 1
    # S0, S1, S2, PA all start at 0

    # Place opcode sequence on CODE_ROW
    for i, opchar in enumerate(syn_opcodes):
        sim.grid[sim._to_flat(CODE_ROW, i)] = OP[opchar]

    # Set initial state
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, CW)
    sim.h1 = sim._to_flat(DATA_ROW, CW)
    sim.cl = sim._to_flat(DATA_ROW, 0)
    sim.ex = sim._to_flat(EX_ROW, 0)

    # Run until IP exits the opcode sequence
    for _ in range(len(syn_opcodes)):
        sim.step()

    # Read syndrome
    s0 = sim.grid[sim._to_flat(DATA_ROW, S0)] & 1
    s1 = sim.grid[sim._to_flat(DATA_ROW, S1)] & 1
    s2 = sim.grid[sim._to_flat(DATA_ROW, S2)] & 1
    pa = sim.grid[sim._to_flat(DATA_ROW, PA)] & 1
    syndrome = (s2 << 2) | (s1 << 1) | s0

    # Now reverse to restore state
    syn_steps = sim.step_count
    for _ in range(syn_steps):
        sim.step_back()

    # Verify restoration
    cw_after_reverse = sim.grid[sim._to_flat(DATA_ROW, CW)]
    s0_after = sim.grid[sim._to_flat(DATA_ROW, S0)]
    s1_after = sim.grid[sim._to_flat(DATA_ROW, S1)]
    s2_after = sim.grid[sim._to_flat(DATA_ROW, S2)]
    pa_after = sim.grid[sim._to_flat(DATA_ROW, PA)]

    syn_reverse_ok = (cw_after_reverse == codeword and
                      s0_after == 0 and s1_after == 0 and
                      s2_after == 0 and pa_after == 0)

    if verbose and not syn_reverse_ok:
        print(f"    [WARN] Syndrome reverse failed: CW={cw_after_reverse:08b} "
              f"S0={s0_after} S1={s1_after} S2={s2_after} PA={pa_after}")

    # Decide whether to correct
    do_correct = (pa == 1)  # single error detected
    bit_to_flip = syndrome if syndrome > 0 else 0

    if not do_correct:
        return codeword, syndrome, pa, syn_steps, syn_reverse_ok

    # Second pass: full correction with known syndrome
    full_ops = build_opcode_sequence(syndrome_val=bit_to_flip, do_correct=True)
    full_opcodes = [op for op, _ in full_ops]

    n_code_cols2 = len(full_opcodes) + 2
    cols2 = max(n_data_cols, n_code_cols2) + 4

    sim2 = FB2DSimulator(rows=3, cols=cols2)
    sim2.grid[sim2._to_flat(DATA_ROW, CW)] = codeword
    sim2.grid[sim2._to_flat(DATA_ROW, FIX)] = 1

    for i, opchar in enumerate(full_opcodes):
        sim2.grid[sim2._to_flat(CODE_ROW, i)] = OP[opchar]

    sim2.ip_row = CODE_ROW
    sim2.ip_col = 0
    sim2.ip_dir = 1
    sim2.h0 = sim2._to_flat(DATA_ROW, CW)
    sim2.h1 = sim2._to_flat(DATA_ROW, CW)
    sim2.cl = sim2._to_flat(DATA_ROW, 0)
    sim2.gp = sim2._to_flat(EX_ROW, 0)

    for _ in range(len(full_opcodes)):
        sim2.step()

    result_cw = sim2.grid[sim2._to_flat(DATA_ROW, CW)]
    forward_steps = sim2.step_count

    # Reverse all steps
    for _ in range(forward_steps):
        sim2.step_back()

    reversed_cw = sim2.grid[sim2._to_flat(DATA_ROW, CW)]
    reversed_s0 = sim2.grid[sim2._to_flat(DATA_ROW, S0)]
    reversed_s1 = sim2.grid[sim2._to_flat(DATA_ROW, S1)]
    reversed_s2 = sim2.grid[sim2._to_flat(DATA_ROW, S2)]
    reversed_pa = sim2.grid[sim2._to_flat(DATA_ROW, PA)]
    reversed_fix = sim2.grid[sim2._to_flat(DATA_ROW, FIX)]

    reverse_ok = (reversed_cw == codeword and
                  reversed_s0 == 0 and reversed_s1 == 0 and
                  reversed_s2 == 0 and reversed_pa == 0 and
                  reversed_fix == 1)

    if verbose and not reverse_ok:
        print(f"    [WARN] Full reverse failed: CW={reversed_cw:08b} "
              f"S0={reversed_s0} S1={reversed_s1} S2={reversed_s2} "
              f"PA={reversed_pa} FIX={reversed_fix}")

    return result_cw, syndrome, pa, forward_steps, reverse_ok


def run_test(data4, error_bit=None, error_bit2=None, label="", verbose=False):
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

    result, syndrome, p_all_err, steps, reverse_ok = run_correction(bad, verbose=verbose)

    # Check correctness
    if error_bit2 is not None:
        # Double error: should NOT correct (p_all_err=0, left as-is)
        expected = bad
        ok = (result == expected and p_all_err == 0)
    elif error_bit is not None:
        # Single error: should correct back to original
        expected = cw
        ok = (result == expected)
    else:
        # No error: should be untouched
        expected = cw
        ok = (result == expected and syndrome == 0 and p_all_err == 0)

    if verbose or not ok or not reverse_ok:
        print(f"  data={data4:04b} cw={cw:08b} {error_desc}")
        print(f"    input={bad:08b} syn={syndrome:03b} p_all={p_all_err}"
              f" → result={result:08b} expected={expected:08b}"
              f" {'ok' if ok else 'FAIL'}")
        print(f"    {steps} ops, reverse={'ok' if reverse_ok else 'FAIL'}")

    return ok and reverse_ok


if __name__ == '__main__':
    all_ok = True

    print("=== Hamming SECDED in fb2d simulator ===\n")

    # Verify opcode sequence generation
    syn_ops = build_opcode_sequence(-1, False)
    print(f"Syndrome-only: {len(syn_ops)} opcodes")
    full_ops = build_opcode_sequence(5, True)
    print(f"Full correction (syndrome=5): {len(full_ops)} opcodes")

    # No-error tests
    print("\n--- No errors ---")
    no_err_ok = True
    for data in range(16):
        no_err_ok &= run_test(data, label=f"data={data:04b}")
    print(f"  16/16 no-error cases: {'PASS' if no_err_ok else 'FAIL'}")
    all_ok &= no_err_ok

    # Single-error correction
    print("\n--- Single-bit error correction ---")
    single_ok = True
    count = 0
    for data in range(16):
        for bit in range(8):
            single_ok &= run_test(data, error_bit=bit)
            count += 1
    print(f"  {count}/{count} single-bit errors: {'PASS' if single_ok else 'FAIL'}")
    all_ok &= single_ok

    # Double-error detection (no miscorrection)
    print("\n--- Double-bit error detection ---")
    double_ok = True
    count = 0
    for data in range(16):
        for b1 in range(8):
            for b2 in range(b1 + 1, 8):
                double_ok &= run_test(data, error_bit=b1, error_bit2=b2)
                count += 1
    print(f"  {count}/{count} double-bit errors: {'PASS' if double_ok else 'FAIL'}")
    all_ok &= double_ok

    # Verbose examples
    print("\n--- Verbose examples ---")
    run_test(0b1010, verbose=True)
    run_test(0b1010, error_bit=3, verbose=True)
    run_test(0b1010, error_bit=7, verbose=True)
    run_test(0b1010, error_bit=0, verbose=True)  # p_all error
    run_test(0b1010, error_bit=0, error_bit2=3, verbose=True)  # double

    print(f"\n{'='*50}")
    print(f"{'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*50}")

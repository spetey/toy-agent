#!/usr/bin/env python3
"""
mutual-correction-demo.py — Two Hamming(16,11) gadgets correcting each other.

This is the first proof of mutual correction: two identical correction gadgets
(Gadget A on IP0, Gadget B on IP1) each using the IX copy-down pattern to
correct the other's code. They share the grid but have independent heads.

LAYOUT (4 rows × 325 cols, linear):

  Row 0: Gadget A code  (IP0 executes, IP1's IX corrects)
  Row 1: Gadget A EX    (IP0's scratch: H0, H1, CL, EX here)
  Row 2: Gadget B code  (IP1 executes, IP0's IX corrects)
  Row 3: Gadget B EX    (IP1's scratch)

HEAD POSITIONS:
                IP0 (Gadget A)          IP1 (Gadget B)
  IP:          (0, 0) → East           (2, 0) → East
  H0:          (1, 2) CWL              (3, 2) CWL
  H1:          (1, 2) CWL              (3, 2) CWL
  IX:          (2, 2) B's code         (0, 2) A's code
  CL:          (1, 8) ROT              (3, 8) ROT
  EX:          (1, 0) EV               (3, 0) EV

CYCLE MATH:
  1 cycle = grid_width step_all() calls = 2×grid_width step() calls
  Error at col K corrected at cycle K - first_cw_col
  EX scratch: ~36 cycles max (325 / DSL_SLOT_WIDTH)

Run tests:  python3 programs/mutual-correction-demo.py
Save demo:  python3 programs/mutual-correction-demo.py --save
"""

import sys
import os
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, encode_opcode, OPCODE_PAYLOADS)

# Import from dual-gadget-demo.py (hyphenated filename needs importlib)
_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

build_h2_correction_gadget = dgd.build_h2_correction_gadget
GadgetBuilder = dgd.GadgetBuilder
DSL_EV = dgd.DSL_EV
DSL_PA = dgd.DSL_PA
DSL_CWL = dgd.DSL_CWL
DSL_ROT = dgd.DSL_ROT
DSL_SLOT_WIDTH = dgd.DSL_SLOT_WIDTH

from hamming import encode, inject_error

OP = OPCODES

# ── Layout constants ──
ROW_A_CODE = 0
ROW_A_GP   = 1
ROW_B_CODE = 2
ROW_B_GP   = 3
N_ROWS     = 4
FIRST_CW_COL = 2


# ═══════════════════════════════════════════════════════════════════
# Torus builder
# ═══════════════════════════════════════════════════════════════════

def make_mutual_torus(errors_a=None, errors_b=None):
    """Build a 4-row linear torus for mutual correction.

    Args:
        errors_a: dict {col: bit_pos} — errors to inject into A's code
        errors_b: dict {col: bit_pos} — errors to inject into B's code

    Returns: (sim, correct_cells, gadget_ops, grid_width)
        correct_cells: list of correct Hamming-encoded cell values per col
    """
    gadget_ops = build_h2_correction_gadget()
    op_values = [OP[ch] for ch in gadget_ops]
    n_ops = len(op_values)

    ex_start_col = FIRST_CW_COL - DSL_CWL  # = 0
    grid_width = n_ops + 2                  # = 325

    sim = FB2DSimulator(rows=N_ROWS, cols=grid_width)

    # Build correct encoded cells for the full code row
    correct_cells = []
    for opval in op_values:
        correct_cells.append(encode_opcode(opval))
    # Padding columns (NOP = 0 → encode(0))
    for _ in range(grid_width - n_ops):
        correct_cells.append(encode_opcode(0))

    # Place identical code on both rows
    for c in range(grid_width):
        sim.grid[sim._to_flat(ROW_A_CODE, c)] = correct_cells[c]
        sim.grid[sim._to_flat(ROW_B_CODE, c)] = correct_cells[c]

    # Inject errors
    if errors_a:
        for col, bit in errors_a.items():
            flat = sim._to_flat(ROW_A_CODE, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)
    if errors_b:
        for col, bit in errors_b.items():
            flat = sim._to_flat(ROW_B_CODE, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # ── IP0 (Gadget A): executes row 0, IX scans row 2 ──
    sim.ip_row = ROW_A_CODE
    sim.ip_col = 0
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(ROW_A_GP, ex_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(ROW_A_GP, ex_start_col + DSL_CWL)
    sim.ix = sim._to_flat(ROW_B_CODE, FIRST_CW_COL)  # ← scans B
    sim.cl = sim._to_flat(ROW_A_GP, ex_start_col + DSL_ROT)
    sim.ex = sim._to_flat(ROW_A_GP, ex_start_col + DSL_EV)
    sim._save_active()

    # ── IP1 (Gadget B): executes row 2, IX scans row 0 ──
    sim.add_ip(
        ip_row=ROW_B_CODE, ip_col=0, ip_dir=DIR_E,
        h0=sim._to_flat(ROW_B_GP, ex_start_col + DSL_CWL),
        h1=sim._to_flat(ROW_B_GP, ex_start_col + DSL_CWL),
        h2=sim._to_flat(ROW_A_CODE, FIRST_CW_COL),  # ← scans A
        cl=sim._to_flat(ROW_B_GP, ex_start_col + DSL_ROT),
        gp=sim._to_flat(ROW_B_GP, ex_start_col + DSL_EV),
    )

    return sim, correct_cells, gadget_ops, grid_width


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def find_errors(sim, code_row, correct_cells, cols):
    """Return list of (col, actual, expected) for mismatches."""
    errs = []
    for c in range(cols):
        actual = sim.grid[sim._to_flat(code_row, c)]
        if actual != correct_cells[c]:
            errs.append((c, actual, correct_cells[c]))
    return errs


def verify_code_row(sim, code_row, correct_cells, cols, label=""):
    """Check a code row against expected. Returns True if all match."""
    errs = find_errors(sim, code_row, correct_cells, cols)
    if errs:
        for col, actual, expected in errs:
            pl_a = cell_to_payload(actual)
            pl_e = cell_to_payload(expected)
            print(f"    [{label}] col {col}: 0x{actual:04x} (pl={pl_a})"
                  f" != expected 0x{expected:04x} (pl={pl_e})")
        return False
    return True


def verify_gp_clean(sim, ex_row, cols):
    """Check EX row is all zeros. Returns True if clean."""
    for c in range(cols):
        v = sim.grid[sim._to_flat(ex_row, c)]
        if v != 0:
            print(f"    EX row {ex_row} col {c}: 0x{v:04x} != 0")
            return False
    return True


def run_cycles(sim, n_cycles, grid_width):
    """Run n_cycles of step_all (each cycle = grid_width step_all calls)."""
    total = n_cycles * grid_width
    for _ in range(total):
        sim.step_all()
    return total


def reverse_cycles(sim, n_cycles, grid_width):
    """Reverse n_cycles of step_back_all."""
    total = n_cycles * grid_width
    for _ in range(total):
        sim.step_back_all()
    return total


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_a_corrects_b():
    """A is clean, B has parity-only errors. IP0 corrects B via IX.

    Key insight: parity-bit errors (bits 0,1,2,4,8) change the Hamming
    codeword (syndrome ≠ 0) but NOT the payload/opcode. IP1 running B
    executes identically to correct code. IP0's IX detects and fixes
    the parity errors.
    """
    print("\n=== Test: A corrects B (parity errors) ===")

    # Parity bit positions: 0 (overall), 1 (p0), 2 (p1), 4 (p2), 8 (p3)
    errors_b = {5: 1, 10: 2, 50: 4, 100: 8, 200: 0}
    sim, correct, gadget_ops, width = make_mutual_torus(
        errors_a=None, errors_b=errors_b)

    max_err_col = max(errors_b.keys())
    n_cycles = max_err_col - FIRST_CW_COL + 1  # = 199

    # Verify errors are present before running
    errs_before = find_errors(sim, ROW_B_CODE, correct, width)
    assert len(errs_before) == len(errors_b), \
        f"Expected {len(errors_b)} errors, found {len(errs_before)}"
    print(f"    B has {len(errs_before)} parity errors before correction")

    # Run
    total = run_cycles(sim, n_cycles, width)
    print(f"    Ran {n_cycles} cycles ({total} step_all calls,"
          f" {sim.step_count} total steps)")

    # Verify B corrected
    b_ok = verify_code_row(sim, ROW_B_CODE, correct, width, "B")
    print(f"    B after correction: {'PASS' if b_ok else 'FAIL'}")

    # Verify A unchanged
    a_ok = verify_code_row(sim, ROW_A_CODE, correct, width, "A")
    print(f"    A unchanged: {'PASS' if a_ok else 'FAIL'}")

    # Reverse
    reverse_cycles(sim, n_cycles, width)
    errs_after_rev = find_errors(sim, ROW_B_CODE, correct, width)
    rev_ok = len(errs_after_rev) == len(errors_b)
    gp_a_ok = verify_gp_clean(sim, ROW_A_GP, width)
    gp_b_ok = verify_gp_clean(sim, ROW_B_GP, width)
    print(f"    Reverse: errors restored={rev_ok},"
          f" GP_A clean={gp_a_ok}, GP_B clean={gp_b_ok}")

    ok = b_ok and a_ok and rev_ok and gp_a_ok and gp_b_ok
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_b_corrects_a():
    """B is clean, A has parity-only errors. IP1 corrects A via IX."""
    print("\n=== Test: B corrects A (parity errors) ===")

    errors_a = {3: 0, 20: 1, 80: 2, 150: 4, 300: 8}
    sim, correct, gadget_ops, width = make_mutual_torus(
        errors_a=errors_a, errors_b=None)

    max_err_col = max(errors_a.keys())
    n_cycles = max_err_col - FIRST_CW_COL + 1  # = 299

    errs_before = find_errors(sim, ROW_A_CODE, correct, width)
    assert len(errs_before) == len(errors_a), \
        f"Expected {len(errors_a)} errors, found {len(errs_before)}"
    print(f"    A has {len(errs_before)} parity errors before correction")

    total = run_cycles(sim, n_cycles, width)
    print(f"    Ran {n_cycles} cycles ({total} step_all calls,"
          f" {sim.step_count} total steps)")

    a_ok = verify_code_row(sim, ROW_A_CODE, correct, width, "A")
    print(f"    A after correction: {'PASS' if a_ok else 'FAIL'}")

    b_ok = verify_code_row(sim, ROW_B_CODE, correct, width, "B")
    print(f"    B unchanged: {'PASS' if b_ok else 'FAIL'}")

    # Reverse
    reverse_cycles(sim, n_cycles, width)
    errs_after_rev = find_errors(sim, ROW_A_CODE, correct, width)
    rev_ok = len(errs_after_rev) == len(errors_a)
    gp_a_ok = verify_gp_clean(sim, ROW_A_GP, width)
    gp_b_ok = verify_gp_clean(sim, ROW_B_GP, width)
    print(f"    Reverse: errors restored={rev_ok},"
          f" GP_A clean={gp_a_ok}, GP_B clean={gp_b_ok}")

    ok = a_ok and b_ok and rev_ok and gp_a_ok and gp_b_ok
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_mutual_correction():
    """Both A and B have parity errors. Each corrects the other.

    Since parity-bit errors don't change the payload, both IPs execute
    their code identically to clean code. Meanwhile, each IP's IX detects
    and fixes the other's parity errors. This is true mutual correction.
    """
    print("\n=== Test: Mutual correction (bilateral, parity errors) ===")

    # Both have parity-bit-only errors at various columns
    errors_a = {10: 0, 50: 1, 150: 2, 250: 4, 310: 8}
    errors_b = {15: 8, 60: 4, 120: 2, 200: 1, 315: 0}

    sim, correct, gadget_ops, width = make_mutual_torus(
        errors_a=errors_a, errors_b=errors_b)

    max_err_col = max(max(errors_a.keys()), max(errors_b.keys()))
    n_cycles = max_err_col - FIRST_CW_COL + 1  # = 314

    errs_a = find_errors(sim, ROW_A_CODE, correct, width)
    errs_b = find_errors(sim, ROW_B_CODE, correct, width)
    print(f"    A has {len(errs_a)} errors, B has {len(errs_b)} errors")

    total = run_cycles(sim, n_cycles, width)
    print(f"    Ran {n_cycles} cycles ({total} step_all calls,"
          f" {sim.step_count} total steps)")

    a_ok = verify_code_row(sim, ROW_A_CODE, correct, width, "A")
    print(f"    A after correction: {'PASS' if a_ok else 'FAIL'}")

    b_ok = verify_code_row(sim, ROW_B_CODE, correct, width, "B")
    print(f"    B after correction: {'PASS' if b_ok else 'FAIL'}")

    # Reverse
    reverse_cycles(sim, n_cycles, width)
    errs_a_rev = find_errors(sim, ROW_A_CODE, correct, width)
    errs_b_rev = find_errors(sim, ROW_B_CODE, correct, width)
    rev_ok = (len(errs_a_rev) == len(errors_a) and
              len(errs_b_rev) == len(errors_b))
    gp_a_ok = verify_gp_clean(sim, ROW_A_GP, width)
    gp_b_ok = verify_gp_clean(sim, ROW_B_GP, width)
    print(f"    Reverse: errors restored={rev_ok},"
          f" GP_A clean={gp_a_ok}, GP_B clean={gp_b_ok}")

    ok = a_ok and b_ok and rev_ok and gp_a_ok and gp_b_ok
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


# ═══════════════════════════════════════════════════════════════════
# Demo file generation
# ═══════════════════════════════════════════════════════════════════

def save_demo(filename=None, errors_b=None):
    """Save a mutual correction demo as .fb2d for the GUI.

    Default: A clean, B has 1 parity error at col 50 bit 1.
    """
    if errors_b is None:
        errors_b = {50: 1}

    sim, correct, gadget_ops, width = make_mutual_torus(
        errors_a=None, errors_b=errors_b)

    if filename is None:
        prog_dir = os.path.dirname(os.path.abspath(__file__))
        filename = os.path.join(prog_dir, 'mutual-correction-demo.fb2d')

    sim.save_state(filename)

    max_err_col = max(errors_b.keys())
    n_cycles = max_err_col - FIRST_CW_COL + 1
    total_steps = n_cycles * width

    print(f"\nSaved: {filename}")
    print(f"  Grid: {sim.rows}×{sim.cols}")
    print(f"  Row 0: Gadget A code (IP0 executes, clean)")
    print(f"  Row 1: Gadget A EX (IP0 scratch)")
    print(f"  Row 2: Gadget B code (IP1 executes, {len(errors_b)} error(s))")
    print(f"  Row 3: Gadget B EX (IP1 scratch)")
    print(f"  2 IPs: IP0 at (0,0)→E, IP1 at (2,0)→E")
    print(f"  IP0's IX at (2,{FIRST_CW_COL}) — corrects B")
    print(f"  IP1's IX at (0,{FIRST_CW_COL}) — corrects A")
    print(f"  Cycle length: {width} step_all() calls")
    print(f"  To correct all errors: {n_cycles} cycles"
          f" = {total_steps} step_all() calls")
    print(f"\nIn the simulator:")
    print(f"  load mutual-correction-demo")
    print(f"  s {total_steps}    # {n_cycles} correction cycles")
    print(f"  show")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f"Gadget size: {len(build_h2_correction_gadget())} ops")

    if '--save' in sys.argv:
        save_demo()
        sys.exit(0)

    all_ok = True
    all_ok &= test_a_corrects_b()
    all_ok &= test_b_corrects_a()
    all_ok &= test_mutual_correction()

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL MUTUAL CORRECTION TESTS PASSED")
        print("=" * 60)
        save_demo()
    else:
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

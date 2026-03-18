#!/usr/bin/env python3
"""
hamming-loop.py — Hamming SECDED gadget in constrained boustrophedon layout.

Step 1: Place the gadget in a 64-wide grid with code snaking between
columns LEFT_COL..RIGHT_COL (boustrophedon), starting at CODE_START_ROW.
This leaves room in the margins for loop control later.

Step 2 (future): Add a loop around the gadget that iterates over
consecutive data bytes in row 0, error-correcting each one.

GRID LAYOUT:
  Row 0:             DATA — codewords at cols 0, 1, 2, ...
  Rows 1..3:         (reserved for loop control)
  Rows 4..4+N:       CODE — boustrophedon between cols LEFT..RIGHT
  Row 4+N+1:         EX — scratch cells (PA, SYND, S0..ROT)

The gadget's heads (H0, H1, CL, EX) point into rows 0 and EX_ROW.
Code wrapping only constrains where the IP travels.
"""

import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hamming import encode, inject_error, decode

import importlib
_hgd = importlib.import_module('hamming-gadget-demo')
build_gadget = _hgd.build_gadget
GadgetBuilder = _hgd.GadgetBuilder
check_reentrant = _hgd.check_reentrant
DATA_ROW = _hgd.DATA_ROW
ROT = _hgd.ROT
GP_SYND = _hgd.GP_SYND
SLOT_WIDTH = _hgd.SLOT_WIDTH

OP = OPCODES

# ── Layout constants ──
LEFT_COL = 4
RIGHT_COL = 59          # mirrors go here; opcodes up to RIGHT_COL - 1
CODE_START_ROW = 4
GRID_COLS = 64


def wrap_code_constrained(sim, op_values, left_col, right_col, start_row):
    """Wrap opcodes boustrophedon between left_col and right_col.

    First row (East):  opcodes at cols left..right-1, \\ mirror at right
    Odd rows (West):   / at right, opcodes right-1..left+1, / at left
    Even rows (East):  \\ at left, opcodes left+1..right-1, \\ at right

    Returns: (rows_used, end_row, last_op_col, end_dir)
    """
    ops = list(op_values)
    total = len(ops)
    if total == 0:
        return 0, start_row, left_col, 1

    placed = 0
    row = start_row

    # First row: going East, cols left_col to right_col - 1
    first_slots = right_col - left_col   # e.g. 59 - 4 = 55
    n = min(first_slots, total - placed)
    for i in range(n):
        sim.grid[sim._to_flat(row, left_col + i)] = ops[placed]
        placed += 1

    if placed >= total:
        return 1, row, left_col + n - 1, 1  # DIR_E

    # Need to wrap: place \ at right_col
    sim.grid[sim._to_flat(row, right_col)] = OP['\\']
    row_count = 1

    while placed < total:
        row += 1
        row_count += 1

        if row_count % 2 == 0:
            # Going West: / at right_col, opcodes right-1..left+1, / at left
            sim.grid[sim._to_flat(row, right_col)] = OP['/']
            slots = right_col - left_col - 1   # e.g. 54
            n = min(slots, total - placed)
            for i in range(n):
                sim.grid[sim._to_flat(row, right_col - 1 - i)] = ops[placed]
                placed += 1
            if placed >= total:
                return row_count, row, right_col - 1 - (n - 1), 3  # DIR_W
            sim.grid[sim._to_flat(row, left_col)] = OP['/']
        else:
            # Going East: \ at left_col, opcodes left+1..right-1, \ at right
            sim.grid[sim._to_flat(row, left_col)] = OP['\\']
            slots = right_col - left_col - 1   # e.g. 54
            n = min(slots, total - placed)
            for i in range(n):
                sim.grid[sim._to_flat(row, left_col + 1 + i)] = ops[placed]
                placed += 1
            if placed >= total:
                return row_count, row, left_col + 1 + (n - 1), 1  # DIR_E
            sim.grid[sim._to_flat(row, right_col)] = OP['\\']

    return row_count, row, left_col, 1  # shouldn't reach


def compute_layout(left_col, right_col, code_start_row):
    """Iteratively compute grid dimensions for the constrained layout.

    Returns: (code_ops, op_values, n_rows, ex_row, code_rows)
    """
    first_slots = right_col - left_col         # 55
    subsequent_slots = right_col - left_col - 1  # 54

    gp_dist = code_start_row + 3  # initial guess
    n_rows = gp_dist + 1

    for _ in range(10):
        code_ops, _, _ = build_gadget(gp_distance=gp_dist, n_rows=n_rows)
        op_values = [OP[ch] for ch in code_ops]

        remaining = len(op_values) - first_slots
        if remaining <= 0:
            code_rows = 1
        else:
            code_rows = 1 + math.ceil(remaining / subsequent_slots)

        ex_row = code_start_row + code_rows
        new_n_rows = ex_row + 1
        new_gp_dist = ex_row  # EX_ROW - DATA_ROW(0) = ex_row

        if new_gp_dist == gp_dist and new_n_rows == n_rows:
            break
        gp_dist = new_gp_dist
        n_rows = new_n_rows

    return code_ops, op_values, n_rows, ex_row, code_rows


def make_constrained_gadget(codeword):
    """Build a grid with the Hamming gadget in constrained boustrophedon layout.

    Code snakes between LEFT_COL and RIGHT_COL, starting at CODE_START_ROW.
    Data on row 0. EX scratch on the row after the last code row.
    """
    code_ops, op_values, n_rows, ex_row, code_rows = compute_layout(
        LEFT_COL, RIGHT_COL, CODE_START_ROW)

    sim = FB2DSimulator(rows=n_rows, cols=GRID_COLS)

    rows_used, end_row, last_op_col, end_dir = wrap_code_constrained(
        sim, op_values, LEFT_COL, RIGHT_COL, CODE_START_ROW)

    if end_dir == 1:    # East
        term_col = last_op_col + 1
    else:               # West
        term_col = last_op_col - 1

    sim._wrap_end_row = end_row
    sim._wrap_end_col = term_col
    sim._wrap_end_dir = end_dir

    # Place codeword
    sim.grid[sim._to_flat(DATA_ROW, 0)] = codeword

    # Head positions
    sim.ip_row = CODE_START_ROW
    sim.ip_col = LEFT_COL
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, 0)
    sim.h1 = sim._to_flat(DATA_ROW, 0)
    sim.cl = sim._to_flat(ex_row, ROT)
    sim.ex = sim._to_flat(ex_row, 0)

    return sim, ex_row


def run_constrained_test(data4, error_bit=None, verbose=False):
    """Test the constrained-layout gadget on a single codeword."""
    cw = encode(data4)
    if error_bit is not None:
        bad = inject_error(cw, error_bit)
        error_desc = f"flip bit {error_bit}"
    else:
        bad = cw
        error_desc = "no error"

    sim, ex_row = make_constrained_gadget(bad)

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
            print(f"  TIMEOUT at step {sim.step_count}")
        return False

    forward_steps = sim.step_count
    result = sim.grid[sim._to_flat(DATA_ROW, 0)]

    # Check re-entrancy
    reentrant_ok = check_reentrant(sim, ex_row, verbose=verbose)

    # Reference
    _, ref_syn, ref_p_all, _ = decode(bad)
    expected = cw

    ok = (result == expected)

    # Reverse
    for _ in range(forward_steps):
        sim.step_back()

    reverse_ok = (sim.grid[sim._to_flat(DATA_ROW, 0)] == bad)
    for col in range(ROT + 1):
        if sim.grid[sim._to_flat(ex_row, col)] != 0:
            reverse_ok = False

    if verbose or not ok or not reverse_ok or not reentrant_ok:
        print(f"  data={data4:04b} cw={cw:08b} {error_desc}")
        print(f"    input={bad:08b} syn={ref_syn:03b} p_all={ref_p_all}"
              f" -> result={result:08b} expected={expected:08b}"
              f" {'ok' if ok else 'FAIL'}")
        print(f"    {forward_steps} steps, reverse={'ok' if reverse_ok else 'FAIL'}"
              f", reentry={'ok' if reentrant_ok else 'FAIL'}")

    return ok and reverse_ok and reentrant_ok


def save_constrained_fb2d():
    """Save a .fb2d file with the constrained-layout gadget."""
    prog_dir = os.path.dirname(os.path.abspath(__file__))

    cases = [
        ('hamming-constrained-noerror',  0b1010, None),
        ('hamming-constrained-bit5err',  0b1010, 5),
    ]

    for name, data4, err_bit in cases:
        cw = encode(data4)
        if err_bit is not None:
            cw = inject_error(cw, err_bit)
        sim, ex_row = make_constrained_gadget(cw)
        fn = os.path.join(prog_dir, f'{name}.fb2d')
        sim.save_state(fn)
        print(f"  Saved {fn}  ({sim.rows}x{sim.cols})")


if __name__ == '__main__':
    code_ops, op_values, n_rows, ex_row, code_rows = compute_layout(
        LEFT_COL, RIGHT_COL, CODE_START_ROW)

    print(f"=== Hamming SECDED — Constrained Boustrophedon Layout ===\n")
    print(f"Grid: {n_rows} rows x {GRID_COLS} cols")
    print(f"Code zone: cols {LEFT_COL}..{RIGHT_COL}, rows {CODE_START_ROW}..{CODE_START_ROW + code_rows - 1}")
    print(f"Gadget: {len(code_ops)} ops, {code_rows} code rows")
    print(f"EX row: {ex_row}")
    print()

    # Show the IP termination point
    sim, _ = make_constrained_gadget(encode(0))
    print(f"IP termination: row={sim._wrap_end_row} col={sim._wrap_end_col}"
          f" dir={'E' if sim._wrap_end_dir == 1 else 'W'}")
    print()

    all_ok = True

    print("--- No errors ---")
    no_err_ok = True
    for data in range(16):
        no_err_ok &= run_constrained_test(data)
    print(f"  16/16 no-error cases: {'PASS' if no_err_ok else 'FAIL'}")
    all_ok &= no_err_ok

    print("\n--- Single-bit error correction ---")
    single_ok = True
    count = 0
    for data in range(16):
        for bit in range(8):
            single_ok &= run_constrained_test(data, error_bit=bit)
            count += 1
    print(f"  {count}/{count} single-bit errors: {'PASS' if single_ok else 'FAIL'}")
    all_ok &= single_ok

    print("\n--- Verbose examples ---")
    run_constrained_test(0b1010, verbose=True)
    run_constrained_test(0b1010, error_bit=5, verbose=True)
    run_constrained_test(0b1010, error_bit=0, verbose=True)

    print(f"\n{'='*55}")
    print(f"{'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*55}")

    if all_ok:
        print(f"\n--- Saving .fb2d state files ---")
        save_constrained_fb2d()

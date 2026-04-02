#!/usr/bin/env python3
"""
metabolism-v1.py -- Standalone compression loop + walk-back test.

Tests the core metabolism primitive: XOR runs of identical fuel cells
against a reference held in H1, producing zeros on the EX row.

Each loop is a 2-row structure (main row + return row above), same
pattern as the IX rewind loop in the immunity gadget:

  Compression loop:  Z x T ? T x Z   (match: ? fires E→N, bounce via return)
  Walk-back loop:    [ Z T ? T Z     (zero: ? fires E→N, bounce via return)
  Advance-to-fuel:   ] Z T ? T Z     (zero: ? fires E→N, bounce via return)

The return row has: \\ (N→W entry), T (restore CL), optionally ] (advance),
/ (W→S exit).  The / lands on the main row's \\ (S→E re-entry).

Run:  python3 programs/metabolism-v1.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  encode_opcode, DIR_E, DIR_N, DIR_S, DIR_W)

OP = OPCODES
NOP_CELL = hamming_encode(1017)
BOUNDARY_CELL = 0xFFFF


# ===================================================================
# Helpers
# ===================================================================

def place_op(sim, row, col, op_char):
    sim.grid[sim._to_flat(row, col)] = encode_opcode(OP[op_char])

def place_nop(sim, row, col):
    sim.grid[sim._to_flat(row, col)] = NOP_CELL

def fill_row_nop(sim, row):
    for col in range(sim.cols):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def run_until(sim, predicate, max_steps=100000):
    """Run until predicate(sim) is True.  Returns step count or -1."""
    for i in range(1, max_steps + 1):
        sim.step()
        if predicate(sim):
            return i
    return -1


# ===================================================================
# Test: Compression loop alone
# ===================================================================

def test_compression_loop():
    """Test the compression loop: Z x T ? T x Z.

    Grid layout (5 rows × W cols):
      Row 0:  RETURN ROW
      Row 1:  MAIN ROW (compression loop)
      Row 2:  (blank — IP exits south on mismatch via /)
      Row 3:  STOMACH
      Row 4:  FUEL ROW (EX)

    The IP enters at (1, LOOP+1) going east (past the \\ re-entry).
    On match, ? fires E→N → return row bounces back.
    On mismatch, continues east through T x Z → / → exits north.
    """
    print("=== Compression loop: 8 identical cells ===")
    V = 42
    N_FUEL = 8
    W = 40
    sim = FB2DSimulator(rows=5, cols=W)

    RETURN_ROW = 0
    MAIN_ROW = 1
    STOMACH_ROW = 3
    FUEL_ROW = 4
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    # Stomach: H0=0, H1=reference V, CL=0
    sim.grid[sim._to_flat(STOMACH_ROW, H1_COL)] = hamming_encode(V)

    # Fuel row: all V starting at col 10, then something different at col 10+N_FUEL
    FUEL_START = 10
    for i in range(N_FUEL):
        sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + i)] = hamming_encode(V)
    # Mismatch cell after the fuel run
    sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + N_FUEL)] = hamming_encode(77)

    # Compression loop on main row: \ Z x T ? T x Z
    LOOP = 6  # starting col of the \ re-entry
    loop_ops = ['\\', 'Z', 'x', 'T', '?', 'T', 'x', 'Z']
    for i, op in enumerate(loop_ops):
        place_op(sim, MAIN_ROW, LOOP + i, op)

    # Exit after mismatch: / at col LOOP+8
    EXIT_COL = LOOP + len(loop_ops)
    place_op(sim, MAIN_ROW, EXIT_COL, '/')

    # Return row:
    # ? is at col LOOP+4.  Return: \ at col LOOP+4, T at col LOOP+3,
    # ] at col LOOP+2, NOP at col LOOP+1, / at col LOOP.
    Q_COL = LOOP + 4  # where ? is
    place_op(sim, RETURN_ROW, Q_COL, '\\')      # N→W entry
    place_op(sim, RETURN_ROW, Q_COL - 1, 'T')   # restore CL
    place_op(sim, RETURN_ROW, Q_COL - 2, ']')   # advance EX
    place_nop(sim, RETURN_ROW, Q_COL - 3)        # padding
    place_op(sim, RETURN_ROW, LOOP, '/')          # W→S exit → \ at (MAIN, LOOP)

    fill_row_nop(sim, RETURN_ROW)
    fill_row_nop(sim, MAIN_ROW)

    # IP starts past the \ re-entry, at (MAIN_ROW, LOOP+1) going east
    sim.ip_row = MAIN_ROW
    sim.ip_col = LOOP + 1  # first real op: Z
    sim.ip_dir = DIR_E

    # Heads
    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, FUEL_START)  # start on first fuel cell
    sim.ix = 0

    # Run until IP exits (hits / and goes north)
    steps = run_until(sim, lambda s: s.ip_col == EXIT_COL and s.ip_dir == DIR_N)

    if steps < 0:
        print("    FAIL: timeout")
        return False

    # Check fuel row
    n_zeros = 0
    for i in range(N_FUEL):
        val = sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + i)]
        if val == 0:
            n_zeros += 1

    # Mismatch cell should be untouched
    mismatch_val = cell_to_payload(sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + N_FUEL)])
    mismatch_ok = (mismatch_val == 77)

    # H1 should still have V
    h1_p = cell_to_payload(sim.grid[sim.h1])
    # H0, CL should be clean
    h0_p = cell_to_payload(sim.grid[sim.h0])
    cl_p = cell_to_payload(sim.grid[sim.cl])

    # EX should be at FUEL_START + N_FUEL (the mismatch cell, where the loop exited)
    ex_row, ex_col = sim._to_rc(sim.ex)

    print(f"    Steps: {steps}")
    print(f"    Zeros: {n_zeros}/{N_FUEL} (expected {N_FUEL})")
    print(f"    Mismatch cell: {mismatch_val} (expected 77) {'ok' if mismatch_ok else 'FAIL'}")
    print(f"    H1 ref: {h1_p} (expected {V})")
    print(f"    H0={h0_p} CL={cl_p} (expected 0,0)")
    print(f"    EX: ({ex_row},{ex_col}) (expected ({FUEL_ROW},{FUEL_START + N_FUEL}))")

    ok = (n_zeros == N_FUEL and mismatch_ok and h1_p == V
          and h0_p == 0 and cl_p == 0
          and ex_col == FUEL_START + N_FUEL)
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================
# Test: Walk-back loop alone
# ===================================================================

def test_walkback_loop():
    """Test walk-back: [ Z T ? T Z.

    EX starts in a region of zeros and walks west until it finds
    a non-zero cell.

    Grid layout (5 rows):
      Row 0: RETURN ROW
      Row 1: MAIN ROW (walk-back loop)
      Row 2: blank
      Row 3: STOMACH
      Row 4: FUEL ROW
    """
    print("=== Walk-back loop: find dirty cell ===")
    W = 40
    sim = FB2DSimulator(rows=5, cols=W)

    RETURN_ROW = 0
    MAIN_ROW = 1
    STOMACH_ROW = 3
    FUEL_ROW = 4
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    # Fuel row: dirty cell at col 2, zeros at cols 3-15, EX starts at col 15
    DIRTY_COL = 2
    sim.grid[sim._to_flat(FUEL_ROW, DIRTY_COL)] = hamming_encode(999)
    for col in range(DIRTY_COL + 1, 16):
        sim.grid[sim._to_flat(FUEL_ROW, col)] = 0  # zeros

    # Walk-back loop: \ [ Z T ? T Z
    LOOP = 6
    wb_ops = ['\\', '[', 'Z', 'T', '?', 'T', 'Z']
    for i, op in enumerate(wb_ops):
        place_op(sim, MAIN_ROW, LOOP + i, op)

    # Exit: / after walk-back
    EXIT_COL = LOOP + len(wb_ops)
    place_op(sim, MAIN_ROW, EXIT_COL, '/')

    # Return row:
    Q_COL = LOOP + 4  # where ? is
    place_op(sim, RETURN_ROW, Q_COL, '\\')
    place_op(sim, RETURN_ROW, Q_COL - 1, 'T')
    place_nop(sim, RETURN_ROW, Q_COL - 2)
    place_nop(sim, RETURN_ROW, Q_COL - 3)
    place_op(sim, RETURN_ROW, LOOP, '/')

    fill_row_nop(sim, RETURN_ROW)
    fill_row_nop(sim, MAIN_ROW)

    # IP at (MAIN, LOOP+1) going east
    sim.ip_row = MAIN_ROW
    sim.ip_col = LOOP + 1
    sim.ip_dir = DIR_E

    # Heads
    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, 15)  # start in the zeros
    sim.ix = 0

    steps = run_until(sim, lambda s: s.ip_col == EXIT_COL and s.ip_dir == DIR_N)
    if steps < 0:
        print("    FAIL: timeout")
        return False

    ex_row, ex_col = sim._to_rc(sim.ex)
    ex_val = sim.grid[sim.ex]
    h0_p = cell_to_payload(sim.grid[sim.h0])
    cl_p = cell_to_payload(sim.grid[sim.cl])

    # All zero cells should still be zero (no damage)
    zeros_ok = all(sim.grid[sim._to_flat(FUEL_ROW, c)] == 0
                   for c in range(DIRTY_COL + 1, 16))
    # Dirty cell should be intact
    dirty_val = sim.grid[sim._to_flat(FUEL_ROW, DIRTY_COL)]
    dirty_ok = (cell_to_payload(dirty_val) == 999)

    print(f"    Steps: {steps}")
    print(f"    EX: ({ex_row},{ex_col}) (expected ({FUEL_ROW},{DIRTY_COL}))")
    print(f"    EX value: 0x{ex_val:04x} (non-zero: {'yes' if ex_val != 0 else 'NO'})")
    print(f"    H0={h0_p} CL={cl_p} (expected 0,0)")
    print(f"    Zero cells intact: {'yes' if zeros_ok else 'NO'}")
    print(f"    Dirty cell intact: {'yes' if dirty_ok else 'NO'}")

    ok = (ex_col == DIRTY_COL and ex_val != 0 and h0_p == 0 and cl_p == 0
          and zeros_ok and dirty_ok)
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================
# Test: Advance-to-fuel loop
# ===================================================================

def test_advance_to_fuel():
    """Test advance-to-fuel: ] Z T ? T Z.

    EX starts on a dirty cell, walks east past zeros to first non-zero
    fuel cell.
    """
    print("=== Advance-to-fuel: walk east past zeros ===")
    W = 40
    sim = FB2DSimulator(rows=5, cols=W)

    RETURN_ROW = 0
    MAIN_ROW = 1
    STOMACH_ROW = 3
    FUEL_ROW = 4
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    # Fuel row: dirty at col 0, zeros cols 1-9, fuel V at col 10
    sim.grid[sim._to_flat(FUEL_ROW, 0)] = hamming_encode(999)
    for col in range(1, 10):
        sim.grid[sim._to_flat(FUEL_ROW, col)] = 0
    FUEL_COL = 10
    sim.grid[sim._to_flat(FUEL_ROW, FUEL_COL)] = hamming_encode(42)

    # Advance loop: \ ] Z T ? T Z
    LOOP = 6
    adv_ops = ['\\', ']', 'Z', 'T', '?', 'T', 'Z']
    for i, op in enumerate(adv_ops):
        place_op(sim, MAIN_ROW, LOOP + i, op)

    EXIT_COL = LOOP + len(adv_ops)
    place_op(sim, MAIN_ROW, EXIT_COL, '/')

    # Return row: same as walk-back but without ] (advance already done by main row)
    Q_COL = LOOP + 4
    place_op(sim, RETURN_ROW, Q_COL, '\\')
    place_op(sim, RETURN_ROW, Q_COL - 1, 'T')
    place_nop(sim, RETURN_ROW, Q_COL - 2)
    place_nop(sim, RETURN_ROW, Q_COL - 3)
    place_op(sim, RETURN_ROW, LOOP, '/')

    fill_row_nop(sim, RETURN_ROW)
    fill_row_nop(sim, MAIN_ROW)

    sim.ip_row = MAIN_ROW
    sim.ip_col = LOOP + 1  # start at ]
    sim.ip_dir = DIR_E

    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, 0)  # start on dirty cell
    sim.ix = 0

    steps = run_until(sim, lambda s: s.ip_col == EXIT_COL and s.ip_dir == DIR_N)
    if steps < 0:
        print("    FAIL: timeout")
        return False

    ex_row, ex_col = sim._to_rc(sim.ex)
    h0_p = cell_to_payload(sim.grid[sim.h0])
    cl_p = cell_to_payload(sim.grid[sim.cl])

    # EX should be on the fuel cell
    # After advance: ] moves EX east from col 0 to col 1 (zero).
    # Z picks up 0, T bridges, ? fires.  Loop.
    # Eventually ] moves EX to col 10 (fuel).  Z picks up V, T bridges.
    # ? doesn't fire (V ≠ 0).  T restores, Z restores fuel.
    # EX is at col 10, fuel cell intact.

    fuel_intact = (cell_to_payload(sim.grid[sim._to_flat(FUEL_ROW, FUEL_COL)]) == 42)

    print(f"    Steps: {steps}")
    print(f"    EX: ({ex_row},{ex_col}) (expected ({FUEL_ROW},{FUEL_COL}))")
    print(f"    H0={h0_p} CL={cl_p} (expected 0,0)")
    print(f"    Fuel intact: {'yes' if fuel_intact else 'NO'}")

    ok = (ex_col == FUEL_COL and h0_p == 0 and cl_p == 0 and fuel_intact)
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================
# Test: Reference swap
# ===================================================================

def test_ref_swap():
    """Test Z X Z ] reference swap.  Not a loop, just a linear sequence."""
    print("=== Reference swap: Z X Z ] ===")
    V_payload = 42
    OLD_REF = 77
    W = 40
    sim = FB2DSimulator(rows=4, cols=W)

    MAIN_ROW = 0
    STOMACH_ROW = 2
    FUEL_ROW = 3
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    # H1 has old reference
    sim.grid[sim._to_flat(STOMACH_ROW, H1_COL)] = hamming_encode(OLD_REF)
    # Fuel cell at col 10
    FUEL_COL = 10
    sim.grid[sim._to_flat(FUEL_ROW, FUEL_COL)] = hamming_encode(V_payload)

    # Place ref swap ops
    START = 6
    for i, op in enumerate(['Z', 'X', 'Z', ']', '/']):
        place_op(sim, MAIN_ROW, START + i, op)

    fill_row_nop(sim, MAIN_ROW)

    sim.ip_row = MAIN_ROW
    sim.ip_col = START
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, FUEL_COL)
    sim.ix = 0

    # Run 5 steps (Z X Z ] /)
    for _ in range(5):
        sim.step()

    # After Z X Z ]:
    # Z: H0=V, [EX]=0
    # X: H1=V, H0=old_ref
    # Z: H0=0, [EX]=old_ref
    # ]: EX east to col 11
    h1_p = cell_to_payload(sim.grid[sim.h1])
    h0_p = cell_to_payload(sim.grid[sim.h0])
    ex_row, ex_col = sim._to_rc(sim.ex)
    # Old ref should be at col 10
    cell_at_fuel = cell_to_payload(sim.grid[sim._to_flat(FUEL_ROW, FUEL_COL)])

    print(f"    H1: {h1_p} (expected {V_payload})")
    print(f"    H0: {h0_p} (expected 0)")
    print(f"    EX: col {ex_col} (expected {FUEL_COL + 1})")
    print(f"    Cell at fuel col: {cell_at_fuel} (expected {OLD_REF})")

    ok = (h1_p == V_payload and h0_p == 0
          and ex_col == FUEL_COL + 1 and cell_at_fuel == OLD_REF)
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================
# Test: Full cycle (advance + ref swap + compress + walk-back)
# ===================================================================

def test_full_cycle():
    """Test the full compression cycle by running each phase sequentially
    on the same grid, manually repositioning IP between phases.

    This verifies the state transitions (H0, H1, CL, EX, fuel row)
    are correct end-to-end, deferring the inter-phase mirror routing
    to the real gadget integration.
    """
    print("=== Full cycle: advance + ref swap + compress + walk-back ===")
    V = 42
    N_FUEL = 10
    W = 60
    sim = FB2DSimulator(rows=5, cols=W)

    RETURN_ROW = 0
    MAIN_ROW = 1
    STOMACH_ROW = 3
    FUEL_ROW = 4
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    # Fuel row: dirty at col 0, zeros cols 1-4, fuel V×10 at cols 5-14,
    # then W at col 15 (mismatch trigger)
    DIRTY_COL = 0
    sim.grid[sim._to_flat(FUEL_ROW, DIRTY_COL)] = hamming_encode(999)
    for col in range(1, 5):
        sim.grid[sim._to_flat(FUEL_ROW, col)] = 0
    FUEL_START = 5
    for i in range(N_FUEL):
        sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + i)] = hamming_encode(V)
    sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + N_FUEL)] = hamming_encode(77)

    # Place all loops on one main row with one shared return row.
    # Loops are spaced apart so their return-row columns don't overlap.

    def place_loop(loop_col, ops, ret_extra):
        """Place a loop. ret_extra: ops between \\ entry and T on return."""
        for i, op in enumerate(ops):
            place_op(sim, MAIN_ROW, loop_col + i, op)
        q_col = loop_col + ops.index('?')
        place_op(sim, RETURN_ROW, q_col, '\\')
        ret_col = q_col - 1
        place_op(sim, RETURN_ROW, ret_col, 'T')  # restore CL
        ret_col -= 1
        for op in ret_extra:
            place_op(sim, RETURN_ROW, ret_col, op)
            ret_col -= 1
        while ret_col > loop_col:
            place_nop(sim, RETURN_ROW, ret_col)
            ret_col -= 1
        place_op(sim, RETURN_ROW, loop_col, '/')
        exit_col = loop_col + len(ops)
        place_op(sim, MAIN_ROW, exit_col, '/')
        return exit_col

    # Advance-to-fuel: \ ] Z T ? T Z  (cols 6..12, exit at 13)
    ADV = 6
    adv_exit = place_loop(ADV, ['\\', ']', 'Z', 'T', '?', 'T', 'Z'], [])

    # Ref swap: Z X Z ]  (cols 15..18, gap at 14 for NOP)
    REF = adv_exit + 2
    for i, op in enumerate(['Z', 'X', 'Z', ']']):
        place_op(sim, MAIN_ROW, REF + i, op)

    # Compression: \ Z x T ? T x Z  (cols 21..28, exit at 29)
    COMP = REF + 6
    comp_exit = place_loop(COMP, ['\\', 'Z', 'x', 'T', '?', 'T', 'x', 'Z'], [']'])

    # Walk-back: \ [ Z T ? T Z  (cols 31..37, exit at 38)
    WB = comp_exit + 2
    wb_exit = place_loop(WB, ['\\', '[', 'Z', 'T', '?', 'T', 'Z'], [])

    fill_row_nop(sim, RETURN_ROW)
    fill_row_nop(sim, MAIN_ROW)

    # Heads
    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, DIRTY_COL)
    sim.ix = 0

    total_steps = 0

    # ---- Phase 1: Advance-to-fuel ----
    sim.ip_row, sim.ip_col, sim.ip_dir = MAIN_ROW, ADV + 1, DIR_E
    s = run_until(sim, lambda s: s.ip_col == adv_exit and s.ip_dir == DIR_N)
    if s < 0:
        print("    FAIL: advance timeout"); return False
    total_steps += s
    ex_r, ex_c = sim._to_rc(sim.ex)
    adv_ok = (ex_c == FUEL_START)
    print(f"    Advance: {s} steps, EX col {ex_c} (exp {FUEL_START}) {'ok' if adv_ok else 'FAIL'}")

    # ---- Phase 2: Ref swap (linear: Z X Z ]) ----
    sim.ip_row, sim.ip_col, sim.ip_dir = MAIN_ROW, REF, DIR_E
    for _ in range(4):
        sim.step()
    total_steps += 4
    h1_p = cell_to_payload(sim.grid[sim.h1])
    h0_p = cell_to_payload(sim.grid[sim.h0])
    ref_ok = (h1_p == V and h0_p == 0)
    print(f"    Ref swap: H1={h1_p} H0={h0_p} {'ok' if ref_ok else 'FAIL'}")

    # ---- Phase 3: Compression ----
    sim.ip_row, sim.ip_col, sim.ip_dir = MAIN_ROW, COMP + 1, DIR_E
    s = run_until(sim, lambda s: s.ip_col == comp_exit and s.ip_dir == DIR_N)
    if s < 0:
        print("    FAIL: compress timeout"); return False
    total_steps += s
    n_zeros = sum(1 for i in range(N_FUEL)
                  if sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + i)] == 0)
    print(f"    Compress: {s} steps, {n_zeros}/{N_FUEL} zeros")

    # ---- Phase 4: Walk-back ----
    sim.ip_row, sim.ip_col, sim.ip_dir = MAIN_ROW, WB + 1, DIR_E
    s = run_until(sim, lambda s: s.ip_col == wb_exit and s.ip_dir == DIR_N)
    if s < 0:
        print("    FAIL: walk-back timeout"); return False
    total_steps += s
    ex_r, ex_c = sim._to_rc(sim.ex)
    h0_p = cell_to_payload(sim.grid[sim.h0])
    cl_p = cell_to_payload(sim.grid[sim.cl])
    h1_p = cell_to_payload(sim.grid[sim.h1])

    print(f"    Walk-back: {s} steps, EX ({ex_r},{ex_c})")
    print(f"    Total: {total_steps} steps")
    print(f"    Result: {n_zeros} zeros, H1={h1_p}, H0={h0_p}, CL={cl_p}, EX col={ex_c}")

    ok = (n_zeros == N_FUEL and h1_p == V and h0_p == 0 and cl_p == 0
          and ex_c == DIRTY_COL and adv_ok and ref_ok)
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================
# Test: Reversibility
# ===================================================================

def test_reversibility():
    """Forward N steps then backward N steps = original state."""
    print("=== Reversibility ===")
    V = 42
    sim = FB2DSimulator(rows=5, cols=40)

    # Set up a simple compression loop (same as test_compression_loop)
    RETURN_ROW = 0
    MAIN_ROW = 1
    STOMACH_ROW = 3
    FUEL_ROW = 4
    H0_COL, H1_COL, CL_COL = 3, 4, 5

    sim.grid[sim._to_flat(STOMACH_ROW, H1_COL)] = hamming_encode(V)
    FUEL_START = 10
    for i in range(8):
        sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + i)] = hamming_encode(V)
    sim.grid[sim._to_flat(FUEL_ROW, FUEL_START + 8)] = hamming_encode(77)

    LOOP = 6
    for i, op in enumerate(['\\', 'Z', 'x', 'T', '?', 'T', 'x', 'Z']):
        place_op(sim, MAIN_ROW, LOOP + i, op)
    place_op(sim, MAIN_ROW, LOOP + 8, '/')

    Q_COL = LOOP + 4
    place_op(sim, RETURN_ROW, Q_COL, '\\')
    place_op(sim, RETURN_ROW, Q_COL - 1, 'T')
    place_op(sim, RETURN_ROW, Q_COL - 2, ']')
    place_nop(sim, RETURN_ROW, Q_COL - 3)
    place_op(sim, RETURN_ROW, LOOP, '/')

    fill_row_nop(sim, RETURN_ROW)
    fill_row_nop(sim, MAIN_ROW)

    sim.ip_row = MAIN_ROW
    sim.ip_col = LOOP + 1
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(STOMACH_ROW, H0_COL)
    sim.h1 = sim._to_flat(STOMACH_ROW, H1_COL)
    sim.cl = sim._to_flat(STOMACH_ROW, CL_COL)
    sim.ex = sim._to_flat(FUEL_ROW, FUEL_START)
    sim.ix = 0

    grid_before = sim.grid[:]
    state_before = (sim.ip_row, sim.ip_col, sim.ip_dir,
                    sim.h0, sim.h1, sim.cl, sim.ex)

    N = 300
    for _ in range(N):
        sim.step()
    for _ in range(N):
        sim.step_back()

    grid_ok = (sim.grid == grid_before)
    state_ok = ((sim.ip_row, sim.ip_col, sim.ip_dir,
                 sim.h0, sim.h1, sim.cl, sim.ex) == state_before)

    if not grid_ok:
        diffs = sum(1 for i in range(len(sim.grid)) if sim.grid[i] != grid_before[i])
        print(f"    Grid diffs: {diffs}")

    print(f"    Grid: {'ok' if grid_ok else 'FAIL'}")
    print(f"    State: {'ok' if state_ok else 'FAIL'}")
    ok = grid_ok and state_ok
    print(f"    {'PASS' if ok else 'FAIL'}")
    return ok


# ===================================================================

if __name__ == '__main__':
    all_ok = True
    all_ok &= test_compression_loop()
    print()
    all_ok &= test_walkback_loop()
    print()
    all_ok &= test_advance_to_fuel()
    print()
    all_ok &= test_ref_swap()
    print()
    all_ok &= test_full_cycle()
    print()
    all_ok &= test_reversibility()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL METABOLISM v1 TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

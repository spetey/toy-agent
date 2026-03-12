#!/usr/bin/env python3
"""
contained-gadget-demo.py — Contained Hamming(16,11) correction gadget.

This gadget runs in a bounded region using a return corridor (no torus
reliance for control flow). The IP bounces between the code row and a
return row via mirrors, correcting target cells one per pass.

ARCHITECTURE:
  Row 0:  TARGET — codewords to correct (H2 scans here)
  Row 1:  CODE   — gadget opcodes (IP goes east)
  Row 2:  RETURN — mirrors for loop-back (IP goes west)
  Row 3:  GP     — scratch cells (all zero)

CODE ROW LAYOUT:
  Col 1:  % (return entry: / reflect if CL!=0 → N→E)
  Col 2:  V (restore: swap CL ↔ [H2], restoring probed cell)
  Col 3+: [correction cycle 323 ops] [E e ] > a] [V probe] [& loop]

LOOP MECHANISM:
  After correction + advance + V_probe:
    CL = next_target_cell_value (nonzero) or 0 (NOP boundary)
  & (\ reflect if CL!=0):
    CL!=0 → reflect E→S → return via Row 2 → back to code → V_restore → next correction
    CL==0 → pass → idle (IP wraps, does harmless no-op corrections)

BOUNDARY DETECTION CONSTRAINT:
  V_probe uses CL=0 as the boundary signal. Since encode(0) = 0, payload=0
  is indistinguishable from an empty cell. All target cells must have nonzero
  payloads. This is always satisfied for code cells (opcodes 1-56 all have
  nonzero payloads).
  % at return entry (/ reflect if CL!=0):
    On return (IP going N, CL=saved_cell!=0): reflects N→E → re-enters code ✓
    On idle wrap (IP going E, CL=0): passes → continues east (harmless) ✓

V SAVE/RESTORE:
  V_probe (end of pass): swap CL ↔ [H2]. Saves next cell into CL, leaves [H2]=0.
  V_restore (start of next pass): swap CL ↔ [H2]. Restores cell, CL=0 for correction.
  First pass: IP starts AFTER V_restore → first cell corrected directly.
  Subsequent passes: IP enters via return → V_restore runs → cell restored.

GP BEHAVIOR:
  The correction cycle's sliding window advances GP by 1 per correction.
  After all target cells corrected, idle passes continue advancing GP (no-op
  corrections consume GP cells). This is the "GP burn" cost of the idle state.

Run tests:  python3 programs/contained-gadget-demo.py
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

build_h2_correction_gadget = dgd.build_h2_correction_gadget
DSL_EV = dgd.DSL_EV
DSL_CWL = dgd.DSL_CWL
DSL_ROT = dgd.DSL_ROT
DSL_SLOT_WIDTH = dgd.DSL_SLOT_WIDTH

from hamming import encode, inject_error

OP = OPCODES

# ── Layout constants ──
TARGET_ROW = 0
CODE_ROW = 1
RETURN_ROW = 2
GP_ROW = 3
N_ROWS = 4

FIRST_CW_COL = 2   # first target codeword column

# Code layout: V_restore at col 2, correction starts at col 3
CODE_V_RESTORE_COL = 2
CODE_CORR_START_COL = 3


# ═══════════════════════════════════════════════════════════════════
# Gadget builder
# ═══════════════════════════════════════════════════════════════════

def build_contained_gadget_ops():
    """Build opcode sequence for the contained correction gadget.

    Returns: (full_ops, correction_start_offset)
        full_ops: list of opchar strings for the full code row
        correction_start_offset: index where the correction cycle starts
                                 (IP should start here on first pass)
    """
    # build_h2_correction_gadget() returns 323 ops total:
    #   [0:318] = correction cycle (318 ops)
    #   [318:323] = advance (E e ] > a, 5 ops)
    base_ops = build_h2_correction_gadget()
    correction_ops = base_ops[:318]   # correction only (no advance)

    ops = []

    # Col offset 0: % (return entry — / reflect if CL!=0)
    # When IP comes from return (going N, CL=saved_cell!=0): reflects N→E ✓
    # When IP wraps on idle (going E, CL=0): passes → continues east ✓
    ops.append('%')    # col 1 (offset 0 in the placed code)

    # Col offset 1: V (restore [H2] from CL)
    ops.append('V')    # col 2

    # Correction cycle: 323 ops (col 3 to col 325)
    correction_start = len(ops)   # = 2
    ops.extend(correction_ops)

    # Advance: E e ] > a (H0, H1, GP, CL east; H2 east)
    ops.extend(['E', 'e', ']', '>', 'a'])

    # V probe: CL = [next_H2_cell], [H2] = 0
    ops.append('V')

    # & (loop check: \ reflect if CL!=0 → E→S → return via Row 2)
    ops.append('&')

    return ops, correction_start


# ═══════════════════════════════════════════════════════════════════
# Grid builder
# ═══════════════════════════════════════════════════════════════════

def make_contained_torus(cases, first_cw_col=FIRST_CW_COL):
    """Build a contained correction gadget on a 4-row grid.

    Args:
        cases: list of (payload_11bit, error_bit_or_None)

    Returns: (sim, expected_results, cycle_length, gp_start_col)
    """
    gadget_ops, corr_start_offset = build_contained_gadget_ops()
    n = len(cases)

    # Compute grid width
    # Code occupies cols 1 to (1 + len(gadget_ops) - 1)
    code_start_col = 1   # % at col 1
    code_end_col = code_start_col + len(gadget_ops) - 1
    loop_check_col = code_end_col   # & at the last code col

    # GP scratch: starts at first_cw_col - DSL_CWL, extends n + DSL_ROT cells
    gp_start_col = first_cw_col - DSL_CWL
    gp_end_col = gp_start_col + n + DSL_ROT

    # Grid width: fit code, target, and GP scratch
    grid_width = max(code_end_col + 4,      # code + small padding
                     first_cw_col + n + 2,   # target cells + sentinel
                     gp_end_col + 2)         # GP scratch

    sim = FB2DSimulator(rows=N_ROWS, cols=grid_width)

    # ── Place gadget code on CODE_ROW ──
    for i, opchar in enumerate(gadget_ops):
        col = code_start_col + i
        sim.grid[sim._to_flat(CODE_ROW, col)] = encode_opcode(OP[opchar])

    # ── Place return corridor on RETURN_ROW ──
    # / at loop_check_col: S→W (IP comes from & going south, turns west)
    sim.grid[sim._to_flat(RETURN_ROW, loop_check_col)] = encode_opcode(OP['/'])
    # \ at col 1: W→N (IP going west, turns north to CODE_ROW)
    sim.grid[sim._to_flat(RETURN_ROW, 1)] = encode_opcode(OP['\\'])

    # ── Place target codewords on TARGET_ROW ──
    expected = []
    for i, (payload, error_bit) in enumerate(cases):
        cw = encode(payload)
        if error_bit is not None:
            bad = inject_error(cw, error_bit)
        else:
            bad = cw
        expected.append(cw)
        sim.grid[sim._to_flat(TARGET_ROW, first_cw_col + i)] = bad

    # ── Initial head positions ──
    # IP starts at the correction cycle (AFTER V_restore), so the first
    # target cell is corrected directly without V_restore clobbering it.
    sim.ip_row = CODE_ROW
    sim.ip_col = code_start_col + corr_start_offset  # first correction op
    sim.ip_dir = DIR_E

    # H0, H1 at CWL slot on GP row
    sim.h0 = sim._to_flat(GP_ROW, gp_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(GP_ROW, gp_start_col + DSL_CWL)
    # H2 at first target cell
    sim.h2 = sim._to_flat(TARGET_ROW, first_cw_col)
    # CL at ROT slot on GP row
    sim.cl = sim._to_flat(GP_ROW, gp_start_col + DSL_ROT)
    # GP at EV slot on GP row
    sim.gp = sim._to_flat(GP_ROW, gp_start_col + DSL_EV)

    # Cycle length: one full pass through the code + return corridor
    # Code: from corr_start to & = len(gadget_ops) - corr_start_offset ops
    # On first pass: code_end - corr_start + 1 steps on code row
    # Return: loop_check_col → (RETURN, loop_check_col) → west to col 1 →
    #         (CODE, 1) → % → V → correction...
    # Total per cycle: grid_width (code row) + grid_width (return row)?
    # Actually: code traversal + 2 mirror steps + return traversal
    # Simpler: count empirically or compute from geometry.
    #
    # Code row: cols corr_start to loop_check_col = len(gadget_ops)-corr_start ops
    # Drop to return row: 1 step (south)
    # Return row: loop_check_col to col 1 = loop_check_col - 1 steps (west)
    # Rise to code row: 1 step (north)
    # Code row: % at col 1 → V at col 2 = 1 step (east, executes %)
    # Then V and correction start again.
    #
    # Total: (len(gadget_ops) - corr_start) + 1 + (loop_check_col - 1) + 1 + 1
    # = len(gadget_ops) - corr_start + loop_check_col + 2
    # But the return path also has V_restore before correction, so:
    # cycle = (len(gadget_ops) - corr_start) + 1 + (loop_check_col - 1) + 1 + 1
    #       = len(gadget_ops) - 2 + loop_check_col + 2
    #       = len(gadget_ops) + loop_check_col
    #
    # For first pass: just the code from corr_start to end.
    # first_pass_steps = len(gadget_ops) - corr_start_offset

    # Compute cycle length precisely:
    # A "cycle" = one complete loop from the start of the correction cycle
    # back to the start of the next correction cycle.
    #
    # From corr start (col = code_start + corr_start_offset):
    #   Execute ops from corr_start to end of gadget: len(gadget_ops) - corr_start ops
    #   & reflects south: 1 step (moves to RETURN_ROW)
    #   / at return col: 1 step (executes / → west)
    #   Travel west: (loop_check_col - 1 - 1) NOP steps on return row
    #   \ at col 1: 1 step (executes \ → north)
    #   Move north to CODE_ROW: 1 step
    #   % at col 1: 1 step (executes % → east)
    #   V at col 2: 1 step (executes V)
    #   Arrive at corr start (col 3): ready for next cycle
    #
    # Wait, the IP advances AFTER executing each opcode. So:
    #   At & (col loop_check_col): execute &, then advance to (RETURN, loop_check_col)
    #   At / (RETURN, loop_check_col): execute /, then advance west to (RETURN, lcc-1)
    #   ... traverse return NOPs ...
    #   At \ (RETURN, 1): execute \, then advance north to (CODE, 1)
    #   At % (CODE, 1): execute %, then advance east to (CODE, 2)
    #   At V (CODE, 2): execute V, then advance east to (CODE, 3) = corr start
    #
    # Steps from corr_start to corr_start:
    #   Code: (loop_check_col - (code_start + corr_start_offset)) + 1 = ops from corr to &
    #   Wait, each op takes 1 step. From corr_start col to &:
    n_code_ops = len(gadget_ops) - corr_start_offset  # correction + advance + V + &
    n_return_ops = loop_check_col  # / at lcc, NOPs to col 2, \ at col 1
    n_reentry = 2  # % at col 1, V at col 2
    cycle_length = n_code_ops + n_return_ops + n_reentry

    # First pass: only the code ops (no return entry)
    first_pass_steps = n_code_ops

    return sim, expected, cycle_length, gp_start_col, first_pass_steps


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def run_contained_test(cases, verbose=True, check_reverse=True):
    """Test the contained correction gadget.

    Returns: bool (all tests passed)
    """
    n = len(cases)
    sim, expected, cycle_length, gp_start_col, first_pass_steps = \
        make_contained_torus(cases)

    if verbose:
        gadget_ops, _ = build_contained_gadget_ops()
        print(f"    Grid: {sim.rows}×{sim.cols}")
        print(f"    Gadget: {len(gadget_ops)} ops (contained)")
        print(f"    Cycle: {cycle_length} steps/cycle,"
              f" first pass: {first_pass_steps} steps")

    # Run: first pass + (n-1) return passes
    total_steps = first_pass_steps + (n - 1) * cycle_length
    for _ in range(total_steps):
        sim.step()

    # Check results on TARGET_ROW
    all_ok = True
    for i in range(n):
        data_col = FIRST_CW_COL + i
        result = sim.grid[sim._to_flat(TARGET_ROW, data_col)]
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
    final_h2 = FIRST_CW_COL + n

    heads_ok = True
    h0_exp = sim._to_flat(GP_ROW, final_cw)
    h1_exp = sim._to_flat(GP_ROW, final_cw)
    gp_exp = sim._to_flat(GP_ROW, final_gp)
    cl_exp = sim._to_flat(GP_ROW, final_rot)
    h2_exp = sim._to_flat(TARGET_ROW, final_h2)

    if sim.h0 != h0_exp or sim.h1 != h1_exp or sim.gp != gp_exp \
            or sim.cl != cl_exp or sim.h2 != h2_exp:
        heads_ok = False
    if verbose or not heads_ok:
        actual_h2_row = sim.h2 // sim.cols
        actual_h2_col = sim.h2 % sim.cols
        print(f"    Final heads: H0={sim.h0 % sim.cols} H1={sim.h1 % sim.cols}"
              f" GP={sim.gp % sim.cols} CL={sim.cl % sim.cols}"
              f" H2=({actual_h2_row},{actual_h2_col})"
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

    # Full reverse check
    if check_reverse:
        for _ in range(total_steps):
            sim.step_back()

        reverse_ok = True
        for i in range(n):
            data_col = FIRST_CW_COL + i
            payload, error_bit = cases[i]
            cw = encode(payload)
            orig = inject_error(cw, error_bit) if error_bit is not None else cw
            result = sim.grid[sim._to_flat(TARGET_ROW, data_col)]
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

def test_single_correction():
    """Test: single codeword corrected by contained gadget."""
    print("=== Contained: single correction ===")
    ok = run_contained_test([(42, 3)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_no_error():
    """Test: no error case (should be no-op)."""
    print("=== Contained: no error ===")
    ok = run_contained_test([(42, None)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_bit0_error():
    """Test: bit-0 error (overall parity bit)."""
    print("=== Contained: bit-0 error ===")
    ok = run_contained_test([(42, 0)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_multiple():
    """Test: multiple codewords with various errors.

    Note: payload=0 encodes to codeword 0, which is indistinguishable from
    an empty (boundary) cell. The V_probe mechanism uses CL=0 as the boundary
    signal, so all target payloads must be nonzero. This is always true for
    code cells (valid opcodes are 1-56).
    """
    print("=== Contained: multiple codewords ===")
    cases = [
        (1, 1),
        (2, 2),
        (100, None),
        (200, 15),
        (48, None),     # max opcode (';'), not payload=0 (boundary ambiguity)
        (2047, 0),
    ]
    ok = run_contained_test(cases)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_all_error_positions():
    """Test: every possible single-bit error position (0-15)."""
    print("=== Contained: all 16 error positions ===")
    cases = [(42, bit) for bit in range(16)]
    ok = run_contained_test(cases, verbose=False)
    if ok:
        print(f"  All 16 error positions: PASS")
    else:
        run_contained_test(cases, verbose=True)
        print(f"  FAIL")
    return ok


def test_random():
    """Test: random nonzero payloads and error positions."""
    print("=== Contained: random (20 codewords) ===")
    random.seed(42)
    cases = []
    for _ in range(20):
        payload = random.randint(1, 2047)   # nonzero (payload=0 = boundary)
        if random.random() < 0.2:
            error_bit = None
        else:
            error_bit = random.randint(0, 15)
        cases.append((payload, error_bit))
    ok = run_contained_test(cases, verbose=False)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def save_demo_state(filename=None):
    """Save an interactive demo state file for loading in fb2d.py REPL.

    Creates a grid with 6 target codewords (some with errors) ready to
    correct. Load with: python3 fb2d.py → load contained-gadget-demo
    Then step forward to watch the correction happen.
    """
    cases = [
        (15, 3),        # opcode '+', bit 3 error
        (16, 7),        # opcode '-', bit 7 error
        (19, None),     # opcode 'X', no error
        (42, 11),       # opcode 'z', bit 11 error
        (1, 0),         # opcode '/', bit 0 error (overall parity)
        (48, 14),       # opcode ';', bit 14 error
    ]
    sim, expected, cycle_length, gp_start_col, first_pass_steps = \
        make_contained_torus(cases)

    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'contained-gadget-demo.fb2d')
    sim.save_state(filename)
    n = len(cases)
    total_steps = first_pass_steps + (n - 1) * cycle_length
    print(f"Saved: {filename}")
    print(f"  Grid: {sim.rows}×{sim.cols}")
    print(f"  {n} target codewords on row 0 (cols 2–{1+n})")
    print(f"  Gadget code on row 1 (327 ops)")
    print(f"  Return corridor on row 2")
    print(f"  GP scratch on row 3")
    print(f"  Total steps to correct all: {total_steps}")
    print(f"  ({first_pass_steps} first pass + {n-1}×{cycle_length} cycles)")
    print()
    print(f"Load in REPL:  python3 fb2d.py")
    print(f"  load contained-gadget-demo")
    print(f"  step {total_steps}")


if __name__ == '__main__':
    if '--save' in sys.argv:
        save_demo_state()
        sys.exit(0)

    gadget_ops, corr_offset = build_contained_gadget_ops()
    print(f"Contained gadget: {len(gadget_ops)} ops"
          f" (correction starts at offset {corr_offset})")
    print()

    all_ok = True
    all_ok &= test_single_correction()
    print()
    all_ok &= test_no_error()
    print()
    all_ok &= test_bit0_error()
    print()
    all_ok &= test_multiple()
    print()
    all_ok &= test_all_error_positions()
    print()
    all_ok &= test_random()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL CONTAINED GADGET TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

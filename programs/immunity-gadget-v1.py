#!/usr/bin/env python3
"""
immunity-gadget-v1.py — Self-correcting ouroboros: 1 IP, 2 code rows eating each
other's errors.

The IP zigzags: east on row 0 (correcting row 1), west on row 1 (correcting
row 0). Each east+west cycle corrects ONE column on both rows, then IX
advances. The IP loops perpetually — re-correcting clean cells is a no-op
(syndrome=0, correction mask=0).

ARCHITECTURE:
  Row 0 (east):  / X [318 corr] E e ] > H \    (326 ops, cols 0-325)
  Row 1 (west):  \ h a > ] e E [318 corr] /    (326 ops, cols 0-325, physical)
  Row 2:         EX scratch (slides east on both passes)

  / at (0, 0):   return from west pass (N→E)
  \ at (0, 325): exit to west pass (E→S)
  / at (1, 325): entry from east pass (S→W)
  \ at (1, 0):   exit to east pass (W→N)

  All mirrors are unconditional — no boundary detection needed since the
  ouroboros loops forever.

ADVANCE:
  East pass:  E e ] > H     (5 ops: H0/H1/EX/CL east, IX north to row 0)
  West pass:  E e ] > a h   (6 ops: H0/H1/EX/CL east, IX east + south to row 1)

  East advance does NOT move IX horizontally — just switches row.
  West advance moves IX east by 1 AND switches row.
  Result: each cycle corrects the same column on both rows, then advances.

PADDING:
  X (swap [H0]↔[H1]) on east row between / and correction start.
  At this point H0 and H1 both point at CWL — swapping a cell with itself
  is a no-op.

WHY NO V_PROBE:
  The original design used V (swap [CL]↔[IX]) for boundary detection: save
  the next target cell into CL, test it with conditional mirrors. But this
  caused a fatal reversibility bug: when IX pointed at the V opcode cell
  itself, V would zero its own cell, making step_back() unable to determine
  what opcode was there (sees NOP instead of V).

  Since the ouroboros loops forever (every cell is a valid opcode, no
  boundaries), boundary detection is unnecessary. Unconditional mirrors
  give the same looping behavior without the self-destructive V issue.

EX BEHAVIOR:
  Both passes slide the EX slot east. Each correction consumes 1 EX advance.
  After N cycles (2N corrections), EX has advanced 2N cells east.

Run tests:  python3 programs/immunity-gadget-v1.py
Save demo:  python3 programs/immunity-gadget-v1.py --save
"""

import sys
import os
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, encode_opcode, OPCODE_PAYLOADS)

# Import correction cycle from dual-gadget-demo.py
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
EAST_ROW = 0    # code that corrects row 1 (IP goes east)
WEST_ROW = 1    # code that corrects row 0 (IP goes west)
EX_ROW = 2
N_ROWS = 3
CODE_WIDTH = 326   # ops per code row


# ═══════════════════════════════════════════════════════════════════
# Gadget builder
# ═══════════════════════════════════════════════════════════════════

def build_ouroboros_ops():
    """Build opcode sequences for both rows of the ouroboros.

    Returns: (east_ops, west_physical, correction_start_offset)
        east_ops: list of opchars for row 0, left-to-right (= execution order)
        west_physical: list of opchars for row 1, left-to-right (reversed
                       from execution order, since IP goes west)
        correction_start_offset: col where correction starts on east row
                                 (IP starts here on first pass)
    """
    # Get correction cycle (318 ops, no advance)
    base_ops = build_h2_correction_gadget()
    correction_ops = base_ops[:318]

    # ── East row (row 0): IP goes east, corrects row 1 ──
    # / X [318 corr] E e ] > H \
    east_ops = []
    east_ops.append('/')           # col 0: return entry (N→E)
    east_ops.append('X')           # col 1: padding (no-op: H0=H1=CWL)
    corr_start = len(east_ops)     # = 2
    east_ops.extend(correction_ops)  # cols 2-319: 318 correction ops
    east_ops.extend(['E', 'e', ']', '>', 'H'])  # cols 320-324: advance
    east_ops.append('\\')          # col 325: exit to west (E→S)
    assert len(east_ops) == CODE_WIDTH, f"east_ops={len(east_ops)} != {CODE_WIDTH}"

    # ── West row (row 1): IP goes west, corrects row 0 ──
    # Execution order (as west-going IP sees them):
    #   / [318 corr] E e ] > a h \
    # Physical layout (left-to-right on grid) = reversed:
    west_exec = []
    west_exec.append('/')           # transition mirror (S→W)
    west_exec.extend(correction_ops)  # 318 correction ops
    west_exec.extend(['E', 'e', ']', '>', 'a', 'h'])  # advance (6 ops)
    west_exec.append('\\')          # exit to east (W→N)
    assert len(west_exec) == CODE_WIDTH, f"west_exec={len(west_exec)} != {CODE_WIDTH}"

    # Reverse for physical placement (west IP reads right-to-left)
    west_physical = list(reversed(west_exec))

    return east_ops, west_physical, corr_start


# ═══════════════════════════════════════════════════════════════════
# Grid builder
# ═══════════════════════════════════════════════════════════════════

def make_ouroboros_torus(n_cycles, errors=None):
    """Build the ouroboros grid and inject optional errors.

    Args:
        n_cycles: how many east+west cycles to run
        errors: list of (row, col, bit) tuples for error injection

    Returns: (sim, first_cycle_steps, full_cycle_steps)
    """
    east_ops, west_physical, corr_start = build_ouroboros_ops()

    # Grid width: code is CODE_WIDTH cols. EX needs room for 2*n_cycles advances.
    gp_need = DSL_SLOT_WIDTH + 2 * n_cycles + 4
    grid_width = max(CODE_WIDTH, gp_need)

    sim = FB2DSimulator(rows=N_ROWS, cols=grid_width)

    # ── Place code on both rows ──
    for col, opchar in enumerate(east_ops):
        sim.grid[sim._to_flat(EAST_ROW, col)] = encode_opcode(OP[opchar])
    for col, opchar in enumerate(west_physical):
        sim.grid[sim._to_flat(WEST_ROW, col)] = encode_opcode(OP[opchar])

    # ── Inject errors ──
    if errors:
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # ── Initial head positions ──
    sim.ip_row = EAST_ROW
    sim.ip_col = corr_start   # skip / X on first pass
    sim.ip_dir = DIR_E

    gp_start = 0   # EX slot starts at col 0 on EX row
    sim.h0 = sim._to_flat(EX_ROW, gp_start + DSL_CWL)
    sim.h1 = sim._to_flat(EX_ROW, gp_start + DSL_CWL)
    sim.ix = sim._to_flat(WEST_ROW, 0)   # IX starts at row 1, col 0
    sim.cl = sim._to_flat(EX_ROW, gp_start + DSL_ROT)
    sim.ex = sim._to_flat(EX_ROW, gp_start + DSL_EV)

    # ── Step counts ──
    # First east pass: col 2 to col 325 = 324 ops
    # Transition: / at (row 1, 325) = 1 step
    # First west pass: col 324 to col 0 = 325 ops
    # First cycle total: 324 + 1 + 325 = 650
    first_east = CODE_WIDTH - corr_start   # 324
    transition = 1                          # /
    west_pass = CODE_WIDTH - 1             # 325 (col 324 to col 0)
    first_cycle = first_east + transition + west_pass   # 650

    # Subsequent cycles: / at (0,0) + cols 1-325 (325) + transition (1) + west (325) = 652
    full_cycle = 1 + (CODE_WIDTH - 1) + transition + west_pass   # 652

    return sim, first_cycle, full_cycle


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def run_ouroboros_test(errors, n_cycles, label="", check_reverse=True):
    """Test the ouroboros correction.

    Args:
        errors: list of (row, col, bit) for error injection
        n_cycles: how many east+west cycles to run
        label: test name
    Returns: bool
    """
    if label:
        print(f"=== {label} ===")

    sim, first_cycle, full_cycle = make_ouroboros_torus(n_cycles, errors)

    # Save original grid for expected values
    east_ops, west_physical, _ = build_ouroboros_ops()
    expected_grid = {}
    for col, opchar in enumerate(east_ops):
        expected_grid[(EAST_ROW, col)] = encode_opcode(OP[opchar])
    for col, opchar in enumerate(west_physical):
        expected_grid[(WEST_ROW, col)] = encode_opcode(OP[opchar])

    total_steps = first_cycle + max(0, n_cycles - 1) * full_cycle

    if label:
        print(f"    Grid: {sim.rows}x{sim.cols}")
        print(f"    Errors: {len(errors)} injected")
        print(f"    Cycles: {n_cycles} ({total_steps} steps)")

    for _ in range(total_steps):
        sim.step()

    # Check corrected cells — IX has visited cols 0..n_cycles-1 on both rows
    all_ok = True
    for row, col, bit in errors:
        if col < n_cycles:
            flat = sim._to_flat(row, col)
            result = sim.grid[flat]
            exp = expected_grid[(row, col)]
            ok = (result == exp)
            if label or not ok:
                row_name = "east" if row == EAST_ROW else "west"
                print(f"    ({row_name}, col {col}) bit {bit}:"
                      f" 0x{result:04x} expected 0x{exp:04x}"
                      f" {'ok' if ok else 'FAIL'}")
            all_ok &= ok

    # Reverse check
    if check_reverse:
        for _ in range(total_steps):
            sim.step_back()

        reverse_ok = True
        # Check that errors are restored
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            result = sim.grid[flat]
            exp_original = inject_error(expected_grid[(row, col)], bit)
            if result != exp_original:
                reverse_ok = False
                if label:
                    print(f"    [REVERSE] ({row},{col}): 0x{result:04x}"
                          f" expected 0x{exp_original:04x}")

        # Check EX row clean
        for col in range(sim.cols):
            v = sim.grid[sim._to_flat(EX_ROW, col)]
            if v != 0:
                reverse_ok = False
                if label:
                    print(f"    [REVERSE] EX col {col}: 0x{v:04x}")
                break

        if label:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_single_row1():
    """One error on row 1 (corrected by east pass)."""
    return run_ouroboros_test(
        errors=[(WEST_ROW, 5, 3)],
        n_cycles=8,
        label="Ouroboros: single error on row 1")

def test_single_row0():
    """One error on row 0 (corrected by west pass)."""
    return run_ouroboros_test(
        errors=[(EAST_ROW, 5, 7)],
        n_cycles=8,
        label="Ouroboros: single error on row 0")

def test_both_rows():
    """Errors on both rows at the same column."""
    return run_ouroboros_test(
        errors=[(EAST_ROW, 3, 11), (WEST_ROW, 3, 2)],
        n_cycles=6,
        label="Ouroboros: errors on both rows, same column")

def test_multiple():
    """Multiple errors scattered across both rows."""
    errors = [
        (EAST_ROW, 2, 0),
        (EAST_ROW, 7, 14),
        (EAST_ROW, 12, 5),
        (WEST_ROW, 1, 9),
        (WEST_ROW, 6, 3),
        (WEST_ROW, 11, 15),
    ]
    return run_ouroboros_test(
        errors=errors,
        n_cycles=15,
        label="Ouroboros: multiple errors, both rows")

def test_all_16_positions():
    """Every bit position on one cell."""
    all_ok = True
    for bit in range(16):
        ok = run_ouroboros_test(
            errors=[(WEST_ROW, 4, bit)],
            n_cycles=6,
            label="")
        if not ok:
            print(f"    bit {bit}: FAIL")
            all_ok = False
    print(f"=== Ouroboros: all 16 error positions ===")
    print(f"  {'PASS' if all_ok else 'FAIL'}")
    return all_ok

def test_no_error():
    """No errors — correction should be a no-op."""
    return run_ouroboros_test(
        errors=[],
        n_cycles=4,
        label="Ouroboros: no errors (no-op sweep)")

def test_random():
    """Random errors on both rows (unique cells only)."""
    random.seed(42)
    seen = set()
    errors = []
    for _ in range(10):
        row = random.choice([EAST_ROW, WEST_ROW])
        col = random.randint(2, 20)   # avoid mirror/padding cols 0-1
        if (row, col) in seen:
            continue
        seen.add((row, col))
        bit = random.randint(0, 15)
        errors.append((row, col, bit))
    max_col = max(col for _, col, _ in errors)
    return run_ouroboros_test(
        errors=errors,
        n_cycles=max_col + 3,
        label=f"Ouroboros: random ({len(errors)} errors)")


# ═══════════════════════════════════════════════════════════════════
# Demo state saver
# ═══════════════════════════════════════════════════════════════════

def save_demo_state(filename=None):
    """Save an interactive demo state file."""
    errors = [
        (EAST_ROW, 5, 3),
        (EAST_ROW, 10, 11),
        (WEST_ROW, 3, 7),
        (WEST_ROW, 8, 14),
    ]
    max_col = max(col for _, col, _ in errors)
    n_cycles = max_col + 3

    sim, first_cycle, full_cycle = make_ouroboros_torus(n_cycles, errors)
    total_steps = first_cycle + (n_cycles - 1) * full_cycle

    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'immunity-gadget-v1.fb2d')
    sim.save_state(filename)
    print(f"Saved: {filename}")
    print(f"  Grid: {sim.rows}x{sim.cols}")
    print(f"  Row 0 (east): {CODE_WIDTH} ops, corrects row 1")
    print(f"  Row 1 (west): {CODE_WIDTH} ops, corrects row 0")
    print(f"  Row 2: EX scratch")
    print(f"  {len(errors)} errors injected")
    print(f"  Total steps for {n_cycles} cycles: {total_steps}")
    print(f"  ({first_cycle} first + {n_cycles-1}x{full_cycle})")
    print()
    print(f"Load in REPL:  python3 fb2d.py")
    print(f"  load immunity-gadget-v1")
    print(f"  step {total_steps}")


if __name__ == '__main__':
    if '--save' in sys.argv:
        save_demo_state()
        sys.exit(0)

    east_ops, west_phys, corr_start = build_ouroboros_ops()
    print(f"Ouroboros: {len(east_ops)} ops/row"
          f" (correction at offset {corr_start})")
    print()

    all_ok = True
    all_ok &= test_single_row1()
    print()
    all_ok &= test_single_row0()
    print()
    all_ok &= test_both_rows()
    print()
    all_ok &= test_multiple()
    print()
    all_ok &= test_all_16_positions()
    print()
    all_ok &= test_no_error()
    print()
    all_ok &= test_random()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL OUROBOROS TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

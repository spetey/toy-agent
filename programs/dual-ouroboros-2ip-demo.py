#!/usr/bin/env python3
"""
dual-ouroboros-2ip-demo.py — Two IPs correcting each other's code.

Two identical ouroboros gadgets stacked vertically, each correcting the other.
IP0 runs gadget A, correcting gadget B via H2.
IP1 runs gadget B, correcting gadget A via H2.
Both use IDENTICAL code — only initial head positions differ.

LAYOUT (6 rows × CODE_WIDTH cols):
  Row 0: IP0 east code (corrects row 4 via H2)
  Row 1: IP0 west code (corrects row 3 via H2)
  Row 2: IP0 GP scratch
  Row 3: IP1 east code (corrects row 1 via H2)  ← same code as row 0
  Row 4: IP1 west code (corrects row 0 via H2)  ← same code as row 1
  Row 5: IP1 GP scratch

ADVANCE (same as ouroboros):
  East pass:  E e ] > H      H0/H1/GP/CL east; H2 north (target west→east)
  West pass:  E e ] > a h    H0/H1/GP/CL east; H2 east + south (target east→west)

KEY PROPERTY:
  Both IPs use EXACTLY the same code. The correction code uses m/j to
  access [H2] without caring which row H2 is on. The advance uses H/h
  (relative movement) which works from any starting row.

  IP0: H2 oscillates between rows 3-4 (IP1's code)
  IP1: H2 oscillates between rows 0-1 (IP0's code)
  Both correct the other's code in lockstep.

IP INTERLEAVING:
  step_all() steps IP0 then IP1 each round. Both operate on the SAME
  shared grid. Since they correct DIFFERENT rows, there's no conflict.

Run tests:  python3 programs/dual-ouroboros-2ip-demo.py
Save demo:  python3 programs/dual-ouroboros-2ip-demo.py --save
"""

import sys
import os
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, encode_opcode, OPCODE_PAYLOADS)

# Import from dual-gadget-demo.py (GadgetBuilder, correction gadget)
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

# Also import ouroboros builder
_ouro_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'ouroboros-demo.py')
_spec2 = importlib.util.spec_from_file_location('ouro', _ouro_path)
ouro = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ouro)

build_ouroboros_ops = ouro.build_ouroboros_ops

from hamming import encode, inject_error

OP = OPCODES

# ── Layout constants ──
IP0_EAST = 0
IP0_WEST = 1
IP0_GP   = 2
IP1_EAST = 3
IP1_WEST = 4
IP1_GP   = 5
N_ROWS   = 6


# ═══════════════════════════════════════════════════════════════════
# Grid builder
# ═══════════════════════════════════════════════════════════════════

def make_dual_ouroboros(n_cycles, errors=None):
    """Build the 2-IP dual ouroboros grid.

    Args:
        n_cycles: how many east+west cycles to run PER IP
        errors: list of (row, col, bit) tuples for error injection.
                Row must be 0-1 (IP0 code) or 3-4 (IP1 code).

    Returns: (sim, first_cycle_steps, full_cycle_steps)
        step counts are PER round (step_all = 1 step per IP)
    """
    east_ops, west_physical, corr_start = build_ouroboros_ops()
    code_width = len(east_ops)

    # GP needs room for advances (1 cell per correction, 2 corrections per cycle)
    gp_need = DSL_SLOT_WIDTH + 2 * n_cycles + 4
    grid_width = max(code_width, gp_need)

    sim = FB2DSimulator(rows=N_ROWS, cols=grid_width)

    # ── Place IDENTICAL code on both gadgets ──
    for col, opchar in enumerate(east_ops):
        encoded = encode_opcode(OP[opchar])
        sim.grid[sim._to_flat(IP0_EAST, col)] = encoded
        sim.grid[sim._to_flat(IP1_EAST, col)] = encoded

    for col, opchar in enumerate(west_physical):
        encoded = encode_opcode(OP[opchar])
        sim.grid[sim._to_flat(IP0_WEST, col)] = encoded
        sim.grid[sim._to_flat(IP1_WEST, col)] = encoded

    # ── Inject errors ──
    if errors:
        for row, col, bit in errors:
            assert row in (IP0_EAST, IP0_WEST, IP1_EAST, IP1_WEST), \
                f"Error row {row} not a code row"
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # ── IP0: runs on rows 0-1, H2 scans rows 3-4 ──
    gp_start = 0
    sim.ip_row = IP0_EAST
    sim.ip_col = corr_start
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(IP0_GP, gp_start + DSL_CWL)
    sim.h1 = sim._to_flat(IP0_GP, gp_start + DSL_CWL)
    sim.h2 = sim._to_flat(IP1_WEST, 0)  # H2 starts at IP1's west row, col 0
    sim.cl = sim._to_flat(IP0_GP, gp_start + DSL_ROT)
    sim.gp = sim._to_flat(IP0_GP, gp_start + DSL_EV)

    # ── IP1: runs on rows 3-4, H2 scans rows 0-1 ──
    ip1_idx = sim.add_ip(
        ip_row=IP1_EAST, ip_col=corr_start, ip_dir=DIR_E,
        h0=sim._to_flat(IP1_GP, gp_start + DSL_CWL),
        h1=sim._to_flat(IP1_GP, gp_start + DSL_CWL),
        h2=sim._to_flat(IP0_WEST, 0),  # H2 at IP0's west row, col 0
        cl=sim._to_flat(IP1_GP, gp_start + DSL_ROT),
        gp=sim._to_flat(IP1_GP, gp_start + DSL_EV),
    )

    # ── Step counts (same as ouroboros) ──
    first_east = code_width - corr_start
    transition = 1
    west_pass = code_width - 1
    first_cycle = first_east + transition + west_pass
    full_cycle = 1 + (code_width - 1) + transition + west_pass

    return sim, first_cycle, full_cycle


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def run_dual_test(errors, n_cycles, label="", check_reverse=True):
    """Test the dual ouroboros correction with step_all().

    Args:
        errors: list of (row, col, bit) for error injection
        n_cycles: east+west cycles per IP
        label: test name
    Returns: bool
    """
    if label:
        print(f"=== {label} ===")

    sim, first_cycle, full_cycle = make_dual_ouroboros(n_cycles, errors)

    # Expected (clean) grid
    east_ops, west_physical, _ = build_ouroboros_ops()
    expected_grid = {}
    for col, opchar in enumerate(east_ops):
        enc = encode_opcode(OP[opchar])
        expected_grid[(IP0_EAST, col)] = enc
        expected_grid[(IP1_EAST, col)] = enc
    for col, opchar in enumerate(west_physical):
        enc = encode_opcode(OP[opchar])
        expected_grid[(IP0_WEST, col)] = enc
        expected_grid[(IP1_WEST, col)] = enc

    # Total rounds for step_all (each round = 1 step per IP)
    total_rounds = first_cycle + max(0, n_cycles - 1) * full_cycle

    if label:
        print(f"    Grid: {sim.rows}x{sim.cols}, {sim.n_ips} IPs")
        print(f"    Errors: {len(errors)} injected")
        print(f"    Cycles: {n_cycles} ({total_rounds} rounds)")

    for _ in range(total_rounds):
        sim.step_all()

    # Check corrected cells
    all_ok = True
    row_names = {0: 'A-east', 1: 'A-west', 3: 'B-east', 4: 'B-west'}
    for row, col, bit in errors:
        if col < n_cycles:
            flat = sim._to_flat(row, col)
            result = sim.grid[flat]
            exp = expected_grid[(row, col)]
            ok = (result == exp)
            if label or not ok:
                rn = row_names.get(row, f'row{row}')
                print(f"    ({rn}, col {col}) bit {bit}:"
                      f" 0x{result:04x} expected 0x{exp:04x}"
                      f" {'ok' if ok else 'FAIL'}")
            all_ok &= ok

    # Reverse check
    if check_reverse:
        for _ in range(total_rounds):
            sim.step_back_all()

        reverse_ok = True
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            result = sim.grid[flat]
            exp_original = inject_error(expected_grid[(row, col)], bit)
            if result != exp_original:
                reverse_ok = False
                if label:
                    rn = row_names.get(row, f'row{row}')
                    print(f"    [REVERSE] ({rn},{col}): 0x{result:04x}"
                          f" expected 0x{exp_original:04x}")

        # Check GP rows clean
        for gp_row in (IP0_GP, IP1_GP):
            for col in range(sim.cols):
                v = sim.grid[sim._to_flat(gp_row, col)]
                if v != 0:
                    reverse_ok = False
                    if label:
                        print(f"    [REVERSE] GP row {gp_row} col {col}: 0x{v:04x}")
                    break

        if label:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_ip0_corrects_ip1():
    """Errors on IP1's code — corrected by IP0."""
    return run_dual_test(
        errors=[(IP1_WEST, 5, 3), (IP1_EAST, 8, 11)],
        n_cycles=12,
        label="Dual: IP0 corrects IP1")


def test_ip1_corrects_ip0():
    """Errors on IP0's code — corrected by IP1."""
    return run_dual_test(
        errors=[(IP0_EAST, 3, 7), (IP0_WEST, 10, 14)],
        n_cycles=12,
        label="Dual: IP1 corrects IP0")


def test_both_correct():
    """Errors on both gadgets — mutual correction."""
    return run_dual_test(
        errors=[
            (IP0_EAST, 5, 3),
            (IP0_WEST, 7, 9),
            (IP1_EAST, 4, 12),
            (IP1_WEST, 6, 0),
        ],
        n_cycles=12,
        label="Dual: mutual correction")


def test_same_column():
    """Errors at the same column on both gadgets."""
    return run_dual_test(
        errors=[
            (IP0_EAST, 5, 3),
            (IP1_EAST, 5, 7),
        ],
        n_cycles=8,
        label="Dual: same column, both gadgets")


def test_no_errors():
    """No errors — both IPs sweep without effect."""
    return run_dual_test(
        errors=[],
        n_cycles=4,
        label="Dual: no errors (no-op sweep)")


def test_all_16_positions():
    """Every bit position on IP1's code."""
    all_ok = True
    for bit in range(16):
        ok = run_dual_test(
            errors=[(IP1_WEST, 4, bit)],
            n_cycles=6,
            label="")
        if not ok:
            print(f"    bit {bit}: FAIL")
            all_ok = False
    print(f"=== Dual: all 16 error positions on IP1 ===")
    print(f"  {'PASS' if all_ok else 'FAIL'}")
    return all_ok


def test_random():
    """Random errors on all 4 code rows."""
    random.seed(42)
    code_rows = [IP0_EAST, IP0_WEST, IP1_EAST, IP1_WEST]
    seen = set()
    errors = []
    for _ in range(12):
        row = random.choice(code_rows)
        col = random.randint(2, 20)
        if (row, col) in seen:
            continue
        seen.add((row, col))
        bit = random.randint(0, 15)
        errors.append((row, col, bit))
    max_col = max(col for _, col, _ in errors)
    return run_dual_test(
        errors=errors,
        n_cycles=max_col + 3,
        label=f"Dual: random ({len(errors)} errors)")


# ═══════════════════════════════════════════════════════════════════
# Save demo
# ═══════════════════════════════════════════════════════════════════

def save_demo(filename=None):
    """Save an interactive demo .fb2d file."""
    errors = [
        (IP0_EAST, 5, 3),
        (IP0_WEST, 10, 11),
        (IP1_EAST, 3, 7),
        (IP1_WEST, 8, 14),
    ]
    max_col = max(col for _, col, _ in errors)
    n_cycles = max_col + 3

    sim, first_cycle, full_cycle = make_dual_ouroboros(n_cycles, errors)
    total_rounds = first_cycle + (n_cycles - 1) * full_cycle

    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'dual-ouroboros-2ip.fb2d')
    sim.save_state(filename)

    _, _, corr_start = build_ouroboros_ops()

    print(f"Saved: {filename}")
    print(f"  Grid: {sim.rows}×{sim.cols}")
    print(f"  Row 0: IP0 east code (corrects row 4)")
    print(f"  Row 1: IP0 west code (corrects row 3)")
    print(f"  Row 2: IP0 GP scratch")
    print(f"  Row 3: IP1 east code (corrects row 1)")
    print(f"  Row 4: IP1 west code (corrects row 0)")
    print(f"  Row 5: IP1 GP scratch")
    print(f"  {len(errors)} errors injected across both gadgets")
    print(f"  Correction starts at col {corr_start}")
    print()
    print(f"  IP0: code rows 0-1, GP row 2, H2 scans rows 3-4")
    print(f"  IP1: code rows 3-4, GP row 5, H2 scans rows 0-1")
    print()
    print(f"In the simulator:")
    print(f"  python3 fb2d.py")
    print(f"  > load dual-ouroboros-2ip")
    print(f"  > ip           # show both IPs")
    print(f"  > s {total_rounds}    # run {n_cycles} correction cycles")
    print(f"  > show")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if '--save' in sys.argv:
        save_demo()
        sys.exit(0)

    east_ops, _, corr_start = build_ouroboros_ops()
    print(f"Dual ouroboros: 2 IPs × {len(east_ops)} ops/row")
    print(f"  Grid: {N_ROWS} rows × {len(east_ops)} cols")
    print()

    all_ok = True
    all_ok &= test_ip0_corrects_ip1()
    print()
    all_ok &= test_ip1_corrects_ip0()
    print()
    all_ok &= test_both_correct()
    print()
    all_ok &= test_same_column()
    print()
    all_ok &= test_no_errors()
    print()
    all_ok &= test_all_16_positions()
    print()
    all_ok &= test_random()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL DUAL OUROBOROS TESTS PASSED")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

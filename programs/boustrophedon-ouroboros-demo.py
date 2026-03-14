#!/usr/bin/env python3
"""
boustrophedon-ouroboros-demo.py — Compact dual ouroboros with diagonal H2 scanning.

Two identical gadgets correcting each other's code, wrapped at configurable width W.
Uses diagonal H2 scanning (east+south per cycle) to cover all grid cells.

ARCHITECTURE:
  Gadget A code: rows 0..R-1 (boustrophedon at width W)
  Gadget A GP:   row R
  Gadget B code: rows R+1..2R (identical boustrophedon)
  Gadget B GP:   row 2R+1

  Total: T = (2R+2) rows × W cols

H2 SCANNING (diagonal):
  Each IP's H2 advances east+south per correction cycle (a h).
  After T×W cycles, every cell on the torus is visited exactly once.
  Requires gcd(W, T) = 1 for full coverage.

  Correcting non-code cells (zeros, GP scratch) is a no-op (syndrome=0).
  Correcting own code cells is also a no-op (they're correct).
  Only the other gadget's corrupted cells produce actual corrections.

IDENTICAL CODE:
  Both gadgets have identical boustrophedon code. H2 advance is the same
  for both (a h = east+south). Only initial head positions differ.

GP CONSUMPTION:
  1 cell per correction cycle (same as linear ouroboros). After W cycles,
  GP wraps. For full sweeps, use the server's GP cleanup feature or
  accept partial sweeps.

WIDTH SELECTION:
  W must satisfy gcd(W, 2R+2) = 1 where R = code rows per gadget.
  Good defaults: W=99 (T=10), W=65 (T=14), W=101 (T=10), W=67 (T=14).
  Bad: W=100 (T=10, gcd=10), W=64 (T=14, gcd=2).

Run tests:  python3 programs/boustrophedon-ouroboros-demo.py [--width W]
Save demo:  python3 programs/boustrophedon-ouroboros-demo.py --save [--width W]
"""

import sys
import os
import math
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, DIR_S, DIR_W, encode_opcode, OPCODE_PAYLOADS)

# Import correction gadget builder from dual-gadget-demo.py
_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

build_h2_correction_gadget = dgd.build_h2_correction_gadget
place_boustrophedon = dgd.place_boustrophedon
DSL_EV = dgd.DSL_EV
DSL_CWL = dgd.DSL_CWL
DSL_ROT = dgd.DSL_ROT
DSL_SLOT_WIDTH = dgd.DSL_SLOT_WIDTH

from hamming import encode, inject_error

OP = OPCODES


# ═══════════════════════════════════════════════════════════════════
# Layout calculations
# ═══════════════════════════════════════════════════════════════════

def compute_layout(width):
    """Compute the grid layout for a given width.

    Returns dict with:
        code_rows: R, number of boustrophedon rows per gadget
        total_rows: T = 2R + 2
        ga_code: (start_row, end_row) for gadget A code
        ga_gp: row index for gadget A GP
        gb_code: (start_row, end_row) for gadget B code
        gb_gp: row index for gadget B GP
        code_left, code_right: column bounds for boustrophedon
        coprime: whether gcd(W, T) == 1
        full_sweep_cycles: T * W (cycles to visit every cell)
    """
    code_left = 2
    code_right = width - 2

    first_row_slots = code_right - code_left      # W - 4
    inner_row_slots = code_right - code_left - 1   # W - 5

    # Correction gadget: 318 ops + 6 advance (E e ] > a h)
    gadget_ops = build_diagonal_gadget()
    n_ops = len(gadget_ops)

    if n_ops <= first_row_slots:
        code_rows = 1
    else:
        remaining = n_ops - first_row_slots
        code_rows = 1 + math.ceil(remaining / inner_row_slots)

    total_rows = 2 * code_rows + 2

    layout = {
        'width': width,
        'n_ops': n_ops,
        'code_rows': code_rows,
        'total_rows': total_rows,
        'ga_code': (0, code_rows - 1),
        'ga_gp': code_rows,
        'gb_code': (code_rows + 1, 2 * code_rows),
        'gb_gp': 2 * code_rows + 1,
        'code_left': code_left,
        'code_right': code_right,
        'first_row_slots': first_row_slots,
        'inner_row_slots': inner_row_slots,
        'coprime': math.gcd(width, total_rows) == 1,
        'full_sweep_cycles': total_rows * width,
    }
    return layout


def build_diagonal_gadget():
    """Build correction gadget with diagonal H2 advance (east + south).

    Takes the standard 318-op correction from build_h2_correction_gadget()
    and replaces the advance with: E e ] > a h
    (H0/H1/GP/CL east by 1, H2 east+south by 1).

    Returns: list of opchar strings (324 ops total)
    """
    base_ops = build_h2_correction_gadget()  # 323 ops (318 corr + 5 advance)
    correction_ops = base_ops[:318]

    # New advance: E e ] > a h
    advance_ops = ['E', 'e', ']', '>', 'a', 'h']

    return correction_ops + advance_ops


# ═══════════════════════════════════════════════════════════════════
# Grid builder
# ═══════════════════════════════════════════════════════════════════

def make_boustrophedon_ouroboros(width=99, errors=None):
    """Build the boustrophedon dual ouroboros grid.

    Args:
        width: grid width (must be coprime with grid height)
        errors: list of (row, col, bit) tuples for error injection

    Returns: (sim, layout, cycle_length)
        sim: FB2DSimulator instance
        layout: dict from compute_layout()
        cycle_length: steps per correction cycle (one IP loop)
    """
    layout = compute_layout(width)
    assert layout['coprime'], (
        f"Width {width} and height {layout['total_rows']} are not coprime "
        f"(gcd={math.gcd(width, layout['total_rows'])}). "
        f"Try {_suggest_widths(width)}."
    )

    gadget_ops = build_diagonal_gadget()
    op_values = [OP[ch] for ch in gadget_ops]

    T = layout['total_rows']
    W = width
    code_left = layout['code_left']
    code_right = layout['code_right']

    sim = FB2DSimulator(rows=T, cols=W)

    # ── Place IDENTICAL boustrophedon code for both gadgets ──
    ga_start = layout['ga_code'][0]
    gb_start = layout['gb_code'][0]

    place_boustrophedon(sim, op_values, code_left, code_right, ga_start)
    place_boustrophedon(sim, op_values, code_left, code_right, gb_start)

    # ── Return corridors at col 1 ──
    ga_end = layout['ga_code'][1]
    gb_end = layout['gb_code'][1]

    # Gadget A: \ at last code row col 1, / at first code row col 1
    sim.grid[sim._to_flat(ga_end, 1)] = encode_opcode(OP['\\'])
    sim.grid[sim._to_flat(ga_start, 1)] = encode_opcode(OP['/'])

    # Gadget B: same
    sim.grid[sim._to_flat(gb_end, 1)] = encode_opcode(OP['\\'])
    sim.grid[sim._to_flat(gb_start, 1)] = encode_opcode(OP['/'])

    # ── Inject errors ──
    if errors:
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # ── GP setup ──
    ga_gp = layout['ga_gp']
    gb_gp = layout['gb_gp']
    gp_start = 0  # GP scratch starts at col 0

    # ── IP0: runs gadget A, H2 scans starting at gadget B ──
    sim.ip_row = ga_start
    sim.ip_col = code_left   # first code op
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(ga_gp, gp_start + DSL_CWL)
    sim.h1 = sim._to_flat(ga_gp, gp_start + DSL_CWL)
    sim.h2 = sim._to_flat(gb_start, 0)  # H2 starts at gadget B row 0, col 0
    sim.cl = sim._to_flat(ga_gp, gp_start + DSL_ROT)
    sim.gp = sim._to_flat(ga_gp, gp_start + DSL_EV)

    # ── IP1: runs gadget B, H2 scans starting at gadget A ──
    sim.add_ip(
        ip_row=gb_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(gb_gp, gp_start + DSL_CWL),
        h1=sim._to_flat(gb_gp, gp_start + DSL_CWL),
        h2=sim._to_flat(ga_start, 0),  # H2 at gadget A row 0, col 0
        cl=sim._to_flat(gb_gp, gp_start + DSL_ROT),
        gp=sim._to_flat(gb_gp, gp_start + DSL_EV),
    )

    # ── Compute cycle length (steps per IP loop) ──
    cycle_length = _compute_cycle_length(sim, layout)

    return sim, layout, cycle_length


def _compute_cycle_length(sim, layout):
    """Compute steps for one full IP loop through the boustrophedon.

    Traces the IP path from the start position through all code rows,
    the return corridor, and back to start.
    """
    W = layout['width']
    code_left = layout['code_left']
    code_right = layout['code_right']
    code_rows = layout['code_rows']
    start_row = layout['ga_code'][0]

    # Trace IP path
    r, c, d = start_row, code_left, DIR_E
    steps = 0

    while True:
        steps += 1
        # Read cell
        flat = sim._to_flat(r, c)
        val = sim.grid[flat]
        from fb2d import _CELL_TO_PAYLOAD, _PAYLOAD_TO_OPCODE
        payload = _CELL_TO_PAYLOAD[val]
        opcode = _PAYLOAD_TO_OPCODE[payload]

        # Handle mirrors
        SLASH = {DIR_E: DIR_N, DIR_N: DIR_E, DIR_S: DIR_W, DIR_W: DIR_S}
        BACKSLASH = {DIR_E: DIR_S, DIR_S: DIR_E, DIR_N: DIR_W, DIR_W: DIR_N}
        if opcode == 1:  # /
            d = SLASH[d]
        elif opcode == 2:  # backslash
            d = BACKSLASH[d]

        # Move
        dr = [-1, 0, 1, 0]  # N, E, S, W
        dc = [0, 1, 0, -1]
        r = (r + dr[d]) % sim.rows
        c = (c + dc[d]) % sim.cols

        # Check if we're back at start
        if r == start_row and c == code_left and d == DIR_E:
            break

        if steps > 10000:
            raise RuntimeError(f"Cycle length exceeded 10000 — IP not looping? "
                               f"At ({r},{c}) dir={d}")

    return steps


def _suggest_widths(target):
    """Suggest nearby coprime widths."""
    suggestions = []
    for delta in range(-5, 6):
        w = target + delta
        if w < 10:
            continue
        layout = compute_layout(w)
        if layout['coprime']:
            suggestions.append(w)
    return suggestions[:5]


# ═══════════════════════════════════════════════════════════════════
# Diagonal scan: which cycle corrects which cell
# ═══════════════════════════════════════════════════════════════════

def h2_cell_at_cycle(k, start_row, start_col, T, W):
    """Return (row, col) that H2 visits at cycle k."""
    return (start_row + k) % T, (start_col + k) % W


def cycle_for_cell(target_row, target_col, start_row, start_col, T, W):
    """Return the cycle number when H2 visits (target_row, target_col).

    Uses the diagonal scan: row = (start_row + k) % T, col = (start_col + k) % W.
    Returns the smallest non-negative k.
    """
    # k ≡ target_col - start_col (mod W)
    # k ≡ target_row - start_row (mod T)
    # Solve via CRT since gcd(W, T) = 1

    a = (target_col - start_col) % W
    b = (target_row - start_row) % T

    # CRT: k = a (mod W), k = b (mod T)
    # k = a + W * ((b - a) * W^{-1} mod T)
    w_inv = pow(W, -1, T)  # modular inverse (Python 3.8+)
    m = ((b - a) * w_inv) % T
    k = a + W * m
    return k


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def run_boustrophedon_test(width, errors, label="", check_reverse=False):
    """Test boustrophedon ouroboros correction.

    Args:
        width: grid width
        errors: list of (row, col, bit)
        label: test name
        check_reverse: whether to test step_back_all (slow for many cycles)
    Returns: bool
    """
    if label:
        print(f"=== {label} ===")

    sim, layout, cycle_length = make_boustrophedon_ouroboros(width, errors)
    T = layout['total_rows']
    W = width

    # Save expected (clean) grid values for error cells
    expected = {}
    for row, col, bit in errors:
        flat = sim._to_flat(row, col)
        # The clean value is the current grid value with the error un-injected
        clean = inject_error(sim.grid[flat], bit)  # XOR the bit back
        expected[(row, col)] = clean

    # Figure out how many cycles we need to correct all error cells.
    # IP0's H2 starts at (gb_start, 0), IP1's H2 starts at (ga_start, 0).
    ga_start = layout['ga_code'][0]
    gb_start = layout['gb_code'][0]

    max_cycles_needed = 0
    for row, col, bit in errors:
        # Determine which IP corrects this cell
        if ga_start <= row <= layout['ga_code'][1]:
            # Cell on gadget A code → corrected by IP1's H2
            h2_start_row = ga_start
        elif gb_start <= row <= layout['gb_code'][1]:
            # Cell on gadget B code → corrected by IP0's H2
            h2_start_row = gb_start
        else:
            # Error on GP row — corrected by either IP as a no-op
            continue

        k = cycle_for_cell(row, col, h2_start_row, 0, T, W)
        max_cycles_needed = max(max_cycles_needed, k + 1)

    # Add a small margin
    n_cycles = max_cycles_needed + 2

    total_rounds = n_cycles * cycle_length

    if label:
        print(f"    Grid: {T}×{W} ({layout['code_rows']} code rows/gadget)")
        print(f"    Gadget: {layout['n_ops']} ops, cycle={cycle_length} steps")
        print(f"    Errors: {len(errors)} injected")
        print(f"    Cycles needed: {max_cycles_needed}, running {n_cycles}"
              f" ({total_rounds} rounds)")

    for _ in range(total_rounds):
        sim.step_all()

    # Check corrected cells
    all_ok = True
    for row, col, bit in errors:
        flat = sim._to_flat(row, col)
        result = sim.grid[flat]
        exp = expected[(row, col)]
        ok = (result == exp)
        if label or not ok:
            print(f"    ({row},{col}) bit {bit}:"
                  f" 0x{result:04x} expected 0x{exp:04x}"
                  f" {'ok' if ok else 'FAIL'}")
        all_ok &= ok

    # Optional reverse check
    if check_reverse:
        for _ in range(total_rounds):
            sim.step_back_all()

        reverse_ok = True
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            result = sim.grid[flat]
            exp_original = expected[(row, col)]
            exp_original = inject_error(exp_original, bit)  # re-inject error
            if result != exp_original:
                reverse_ok = False
                if label:
                    print(f"    [REVERSE] ({row},{col}): 0x{result:04x}"
                          f" expected 0x{exp_original:04x}")

        if label:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Helpers: cells reachable within GP budget
# ═══════════════════════════════════════════════════════════════════

def early_cells(layout, ip_idx, max_cycle=None):
    """Return list of (cycle, row, col) for cells visited within GP budget.

    ip_idx: 0 for IP0 (H2 starts at gb_code), 1 for IP1 (H2 starts at ga_code)
    max_cycle: limit (default: width - DSL_ROT - 2, safe GP range)
    """
    T = layout['total_rows']
    W = layout['width']
    if max_cycle is None:
        max_cycle = W - DSL_ROT - 2  # safe before CL wraps to dirty EV

    if ip_idx == 0:
        h2_start_row = layout['gb_code'][0]
    else:
        h2_start_row = layout['ga_code'][0]

    cells = []
    for k in range(max_cycle):
        r = (h2_start_row + k) % T
        c = k % W
        cells.append((k, r, c))
    return cells


def early_code_cells(layout, ip_idx, max_cycle=None):
    """Return only code cells (not GP rows) from early_cells."""
    T = layout['total_rows']
    ga_code = layout['ga_code']
    gb_code = layout['gb_code']
    ga_gp = layout['ga_gp']
    gb_gp = layout['gb_gp']

    result = []
    for k, r, c in early_cells(layout, ip_idx, max_cycle):
        if ga_code[0] <= r <= ga_code[1] or gb_code[0] <= r <= gb_code[1]:
            result.append((k, r, c))
    return result


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_layout_info(width=99):
    """Print layout information for a given width."""
    layout = compute_layout(width)
    print(f"=== Layout for W={width} ===")
    print(f"    Gadget: {layout['n_ops']} ops")
    print(f"    Code rows per gadget: {layout['code_rows']}")
    print(f"    Grid: {layout['total_rows']}×{width}"
          f" = {layout['total_rows'] * width} cells")
    print(f"    Gadget A code: rows {layout['ga_code'][0]}-{layout['ga_code'][1]}")
    print(f"    Gadget A GP: row {layout['ga_gp']}")
    print(f"    Gadget B code: rows {layout['gb_code'][0]}-{layout['gb_code'][1]}")
    print(f"    Gadget B GP: row {layout['gb_gp']}")
    print(f"    Coprime: {layout['coprime']}"
          f" (gcd={math.gcd(width, layout['total_rows'])})")
    print(f"    Full sweep: {layout['full_sweep_cycles']} cycles")
    print(f"    GP-safe cycles: {width - DSL_ROT - 2}")
    # Show first few diagonal cells for IP0
    cells = early_code_cells(layout, 0, max_cycle=20)
    print(f"    IP0 H2 first code cells: "
          + ", ".join(f"({r},{c})@{k}" for k, r, c in cells[:8]))
    return True


def test_cycle_length(width=99):
    """Verify cycle length computation."""
    print(f"=== Cycle length (W={width}) ===")
    sim, layout, cycle_length = make_boustrophedon_ouroboros(width)
    print(f"    Cycle length: {cycle_length} steps")

    # Verify by running one cycle and checking IP returns to start
    start_row = sim.ip_row
    start_col = sim.ip_col
    start_dir = sim.ip_dir

    for _ in range(cycle_length):
        sim.step_all()

    ok = (sim.ip_row == start_row and sim.ip_col == start_col
          and sim.ip_dir == start_dir)
    print(f"    IP returns to start: {'ok' if ok else 'FAIL'}"
          f" ({sim.ip_row},{sim.ip_col} dir={sim.ip_dir})")
    return ok


def test_single_error_gb(width=99):
    """Single error on gadget B code, corrected by IP0 (early diagonal cell)."""
    layout = compute_layout(width)
    # Pick the first gadget B code cell visited by IP0's diagonal
    cells = early_code_cells(layout, 0, max_cycle=80)
    gb_cells = [(k, r, c) for k, r, c in cells
                if layout['gb_code'][0] <= r <= layout['gb_code'][1]]
    assert gb_cells, "No gadget B cells in early diagonal!"
    k, row, col = gb_cells[0]
    return run_boustrophedon_test(
        width=width,
        errors=[(row, col, 3)],
        label=f"Single error on gadget B ({row},{col})@cycle{k} (W={width})")


def test_single_error_ga(width=99):
    """Single error on gadget A code, corrected by IP1 (early diagonal cell)."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 1, max_cycle=80)
    ga_cells = [(k, r, c) for k, r, c in cells
                if layout['ga_code'][0] <= r <= layout['ga_code'][1]]
    assert ga_cells, "No gadget A cells in early diagonal!"
    k, row, col = ga_cells[0]
    return run_boustrophedon_test(
        width=width,
        errors=[(row, col, 7)],
        label=f"Single error on gadget A ({row},{col})@cycle{k} (W={width})")


def test_errors_both_gadgets(width=99):
    """Errors on both gadgets — mutual correction (early diagonal cells)."""
    layout = compute_layout(width)

    # IP0 corrects gadget B
    cells0 = early_code_cells(layout, 0, max_cycle=80)
    gb_cells = [(k, r, c) for k, r, c in cells0
                if layout['gb_code'][0] <= r <= layout['gb_code'][1]]

    # IP1 corrects gadget A
    cells1 = early_code_cells(layout, 1, max_cycle=80)
    ga_cells = [(k, r, c) for k, r, c in cells1
                if layout['ga_code'][0] <= r <= layout['ga_code'][1]]

    errors = []
    # 2 errors on gadget B (corrected by IP0)
    for i in range(min(2, len(gb_cells))):
        k, r, c = gb_cells[i]
        errors.append((r, c, 3 + i * 4))
    # 2 errors on gadget A (corrected by IP1)
    for i in range(min(2, len(ga_cells))):
        k, r, c = ga_cells[i]
        errors.append((r, c, 7 + i * 4))

    return run_boustrophedon_test(
        width=width,
        errors=errors,
        label=f"Errors on both gadgets ({len(errors)} errors, W={width})")


def test_all_code_rows(width=99):
    """Error on every code row (all boustrophedon rows, early cells).

    Key: IP0 corrects GB cells, IP1 corrects GA cells. We must pick
    GB cells from IP0's early scan and GA cells from IP1's early scan.
    """
    layout = compute_layout(width)
    ga_code = layout['ga_code']
    gb_code = layout['gb_code']

    # IP0 corrects GB → pick GB cells from IP0's early diagonal
    cells0 = early_code_cells(layout, 0, max_cycle=80)
    gb_row_to_cell = {}
    for k, r, c in cells0:
        if gb_code[0] <= r <= gb_code[1] and r not in gb_row_to_cell:
            gb_row_to_cell[r] = (k, r, c)

    # IP1 corrects GA → pick GA cells from IP1's early diagonal
    cells1 = early_code_cells(layout, 1, max_cycle=80)
    ga_row_to_cell = {}
    for k, r, c in cells1:
        if ga_code[0] <= r <= ga_code[1] and r not in ga_row_to_cell:
            ga_row_to_cell[r] = (k, r, c)

    errors = []
    for row in range(ga_code[0], ga_code[1] + 1):
        if row in ga_row_to_cell:
            _, r, c = ga_row_to_cell[row]
            errors.append((r, c, 3))
    for row in range(gb_code[0], gb_code[1] + 1):
        if row in gb_row_to_cell:
            _, r, c = gb_row_to_cell[row]
            errors.append((r, c, 7))

    return run_boustrophedon_test(
        width=width,
        errors=errors,
        label=f"Error on every code row ({len(errors)} errors, W={width})")


def test_all_16_positions(width=99):
    """Every bit position on an early gadget B cell."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=80)
    gb_cells = [(k, r, c) for k, r, c in cells
                if layout['gb_code'][0] <= r <= layout['gb_code'][1]]
    assert gb_cells, "No gadget B cells in early diagonal!"
    _, row, col = gb_cells[0]

    all_ok = True
    for bit in range(16):
        ok = run_boustrophedon_test(
            width=width,
            errors=[(row, col, bit)],
            label="")
        if not ok:
            print(f"    bit {bit}: FAIL")
            all_ok = False
    print(f"=== All 16 error positions at ({row},{col}) (W={width}) ===")
    print(f"  {'PASS' if all_ok else 'FAIL'}")
    return all_ok


def test_mirror_cells(width=99):
    """Errors on mirror cells (boundary columns) within GP budget."""
    layout = compute_layout(width)
    T = layout['total_rows']
    # Find mirror/corridor cells reachable within GP budget
    cells0 = early_cells(layout, 0, max_cycle=80)

    code_right = layout['code_right']
    gb_code = layout['gb_code']

    # Filter for cells that are on mirror positions
    mirror_errors = []
    for k, r, c in cells0:
        if gb_code[0] <= r <= gb_code[1]:
            if c == code_right or c == 1 or c == layout['code_left']:
                mirror_errors.append((r, c, 5))
                if len(mirror_errors) >= 4:
                    break

    if not mirror_errors:
        print(f"=== Mirror cell errors (W={width}) ===")
        print(f"    No mirror cells in early diagonal — skipping")
        return True

    return run_boustrophedon_test(
        width=width,
        errors=mirror_errors,
        label=f"Mirror cell errors ({len(mirror_errors)}, W={width})")


def test_random_early(width=99, n_errors=8, seed=42):
    """Random errors on early diagonal cells only.

    Key: only use GB cells from IP0's early scan (IP0 corrects GB),
    and GA cells from IP1's early scan (IP1 corrects GA).
    """
    random.seed(seed)
    layout = compute_layout(width)
    ga_code = layout['ga_code']
    gb_code = layout['gb_code']

    # IP0 corrects GB cells
    cells0 = early_code_cells(layout, 0, max_cycle=80)
    gb_candidates = [(r, c) for k, r, c in cells0
                     if gb_code[0] <= r <= gb_code[1]]

    # IP1 corrects GA cells
    cells1 = early_code_cells(layout, 1, max_cycle=80)
    ga_candidates = [(r, c) for k, r, c in cells1
                     if ga_code[0] <= r <= ga_code[1]]

    # Remove duplicates
    seen = set()
    unique = []
    for r, c in gb_candidates + ga_candidates:
        if (r, c) not in seen:
            seen.add((r, c))
            unique.append((r, c))

    random.shuffle(unique)
    errors = []
    for r, c in unique[:n_errors]:
        bit = random.randint(0, 15)
        errors.append((r, c, bit))

    return run_boustrophedon_test(
        width=width,
        errors=errors,
        label=f"Random early ({len(errors)} errors, W={width})")


def test_reverse(width=99):
    """Test reversibility (step_back_all)."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=20)
    gb_cells = [(k, r, c) for k, r, c in cells
                if layout['gb_code'][0] <= r <= layout['gb_code'][1]]
    assert gb_cells
    _, row, col = gb_cells[0]

    return run_boustrophedon_test(
        width=width,
        errors=[(row, col, 3)],
        label=f"Reverse check ({row},{col}) (W={width})",
        check_reverse=True)


# ═══════════════════════════════════════════════════════════════════
# Save demo
# ═══════════════════════════════════════════════════════════════════

def save_demo(width=99, filename=None):
    """Save an interactive demo .fb2d file."""
    layout = compute_layout(width)
    ga_start = layout['ga_code'][0]
    gb_start = layout['gb_code'][0]
    ga_end = layout['ga_code'][1]
    gb_end = layout['gb_code'][1]

    errors = [
        (ga_start, 5, 3),
        (ga_end, 15, 11),
        (gb_start, 8, 7),
        (gb_end, 20, 14),
    ]

    sim, layout, cycle_length = make_boustrophedon_ouroboros(width, errors)
    T = layout['total_rows']

    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f'boustrophedon-ouroboros-w{width}.fb2d')
    sim.save_state(filename)

    print(f"Saved: {filename}")
    print(f"  Grid: {T}×{width}"
          f" ({layout['code_rows']} code rows/gadget)")
    print(f"  Gadget: {layout['n_ops']} ops, cycle={cycle_length} steps")
    print(f"  Gadget A code: rows {ga_start}-{ga_end}, GP: row {layout['ga_gp']}")
    print(f"  Gadget B code: rows {gb_start}-{gb_end}, GP: row {layout['gb_gp']}")
    print(f"  {len(errors)} errors injected")
    print(f"  Full sweep: {T * width} cycles"
          f" × {cycle_length} steps = {T * width * cycle_length} total steps")
    print()
    print(f"  IP0: code rows {ga_start}-{ga_end},"
          f" GP row {layout['ga_gp']}, H2 starts at ({gb_start},0)")
    print(f"  IP1: code rows {gb_start}-{gb_end},"
          f" GP row {layout['gb_gp']}, H2 starts at ({ga_start},0)")
    print()
    print(f"In fb2d_server GUI:")
    print(f"  Load: boustrophedon-ouroboros-w{width}")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def find_coprime_widths(targets=None):
    """Find widths near common targets that are coprime with grid height."""
    if targets is None:
        targets = [64, 100]
    print("=== Coprime width finder ===")
    for target in targets:
        print(f"\n  Near W={target}:")
        for delta in range(-5, 6):
            w = target + delta
            if w < 10:
                continue
            layout = compute_layout(w)
            tag = " ✓" if layout['coprime'] else ""
            print(f"    W={w}: T={layout['total_rows']}"
                  f" ({layout['code_rows']} code rows)"
                  f" gcd={math.gcd(w, layout['total_rows'])}{tag}")


if __name__ == '__main__':
    # Parse args
    width = 99  # default
    for i, arg in enumerate(sys.argv):
        if arg == '--width' and i + 1 < len(sys.argv):
            width = int(sys.argv[i + 1])

    if '--find-widths' in sys.argv:
        find_coprime_widths()
        sys.exit(0)

    if '--save' in sys.argv:
        save_demo(width=width)
        sys.exit(0)

    # Quick layout check
    layout = compute_layout(width)
    if not layout['coprime']:
        print(f"WARNING: W={width} and T={layout['total_rows']}"
              f" are NOT coprime! Diagonal scan won't cover all cells.")
        print(f"Suggested widths: {_suggest_widths(width)}")
        print()

    print(f"Boustrophedon dual ouroboros (W={width})")
    print(f"  {layout['code_rows']} code rows/gadget,"
          f" {layout['total_rows']} total rows,"
          f" {layout['n_ops']} ops/cycle")
    print()

    all_ok = True

    all_ok &= test_layout_info(width)
    print()

    all_ok &= test_cycle_length(width)
    print()

    all_ok &= test_single_error_gb(width)
    print()

    all_ok &= test_single_error_ga(width)
    print()

    all_ok &= test_errors_both_gadgets(width)
    print()

    all_ok &= test_all_code_rows(width)
    print()

    all_ok &= test_all_16_positions(width)
    print()

    all_ok &= test_mirror_cells(width)
    print()

    all_ok &= test_random_early(width)
    print()

    all_ok &= test_reverse(width)
    print()

    if all_ok:
        print("=" * 60)
        print(f"ALL BOUSTROPHEDON OUROBOROS TESTS PASSED (W={width})")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

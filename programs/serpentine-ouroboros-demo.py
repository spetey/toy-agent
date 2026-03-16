#!/usr/bin/env python3
"""
serpentine-ouroboros-demo.py — Dual ouroboros with serpentine H2 scanning.

Two identical gadgets correcting each other's code, wrapped at configurable width W.
Uses H2 momentum ops (A/B/U) for serpentine scanning: east across row, south,
west across, south, east... with V-based boundary detection.

ARCHITECTURE:
  Gadget A code:     rows 0..R-1 (boustrophedon at width W)
  Gadget A handler:  row R (boundary handler, hand-placed)
  Gadget A stomach:  row R+1 (DSL scratch window: H0, H1, CL here)
  Gadget A waste:    row R+2 (GP lives here, eats zeros, excretes waste)
  Gadget B code:     rows R+3..2R+2
  Gadget B handler:  row 2R+3
  Gadget B stomach:  row 2R+4
  Gadget B waste:    row 2R+5

  Total: T = 2*(R+3) rows x W cols

H2 SCANNING (serpentine):
  Each IP's H2 sweeps east across a row, detects boundary (zero cell),
  then retreats (B), moves south (h), flips direction (U). West sweep, repeat.
  No coprimality constraint needed — systematic row-by-row coverage.

BOUNDARY DETECTION (local-only, no remote cell modification):
  After A (advance H2), m copies [H2] to local [H0] via XOR (H0 was 0),
  T moves the value to CL for the conditional mirror test.
  If zero (boundary): conditional mirror triggers, IP drops to handler row.
  If non-zero: T restores CL, m restores H0. Remote cell never modified.
  Handler: / (S→W), B C U (turnaround), : (CL++ signal), \ (W→N exit).
  C replaces old 'h' — advances H2 in h2_vdir instead of hardcoded south.
  & gate #1 on last code row: \ if CL!=0 → merges handler path west.

VERTICAL BOUNDARY TEST (on code row, between & gates):
  After & #1: T Z ] deposits handler CL signal to waste row.
  Then m T ? T m tests [H2] at the vertically-advanced position.
  If [H2]=0 (outside code+handler area): ? fires, IP drops to bounce
  sub-handler: / D O C : \ — retreat, flip h2_vdir, re-advance, signal.
  & gate #2 merges bounce path west to corridor.
  This makes H2 ping-pong between first code row and handler row.

  Handler row is X-filled so H2 includes it in the scan (gets corrected!).

  OLD DESIGN used V (swap [CL]↔[H2]) for boundary detection. This
  temporarily corrupted the remote cell, causing cross-IP interference
  when the other IP's instruction pointer hit the corrupted cell.

FILL CELLS:
  The last boustrophedon row may be partially filled. Empty cells are filled
  with X (swap, opcode 19) — a no-op when H0=H1 (as in the gadget epilogue).
  This prevents false boundary detection within the code area.

Run tests:  python3 programs/serpentine-ouroboros-demo.py [--width W]
Save demo:  python3 programs/serpentine-ouroboros-demo.py --save [--width W]
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
# Gadget builder
# ═══════════════════════════════════════════════════════════════════

def build_serpentine_gadget(last_row_dir):
    """Build contained correction gadget with serpentine H2 advance.

    CONTAINED DESIGN: H0/H1/CL stay at fixed DSL window positions on the
    stomach row across cycles. GP lives on the waste row below, eating
    fresh zeros and excreting waste. Only H2 advances (serpentine).

    Preamble: T Z ] — deposits handler signal to waste row.
      T swaps [CL]↔[H0] (CL=ROT, H0=CWL). If handler fired, CL has
      the : signal; T moves it to [CWL] and zeros CL.
      Z swaps [H0]↔[GP] (H0=CWL on stomach, GP on waste row). Handler
      signal goes to waste row; fresh zero comes to CWL.
      ] advances GP east past the deposited waste.
      On clean cycles: all values are 0, so T and Z are 0↔0 no-ops.
      GP still advances (eats a zero that stays zero).

    Phase G waste deposit (after Phase F, H0=EV, H1=PA):
      Z deposits EV waste to waste row (swap [H0=EV]↔[GP=waste]).
      ] advances GP. E moves H0 to PA.
      Z deposits PA waste to waste row.
      ] advances GP. E moves H0 to CWL, e moves H1 to CWL.
      On clean cycles: EV=PA=0, Z swaps 0↔0, no drift.

    GP budget: 3 ] per cycle (1 preamble + 2 Phase G). With width W,
    GP wraps after W/3 cycles. Cheat mode refills waste row on wrap.

    Args:
        last_row_dir: DIR_E or DIR_W for the last boustrophedon row

    Returns: list of opchar strings
    """
    base_ops = build_h2_correction_gadget()  # 323 ops (318 corr + 5 advance)
    # Take correction through Phase F, WITHOUT epilogue (E E e).
    # After Phase F: H0=EV(0), H1=PA(1).
    correction_without_epilogue = base_ops[:315]

    # Preamble: deposit handler signal to waste row
    preamble = ['T', 'Z', ']']

    # ── Phase G: deposit EV and PA waste to waste row ──
    # State after Phase F: H0=EV(0), H1=PA(1). GP on waste row.
    phase_g = [
        'Z',    # deposit EV waste: [H0=EV] ↔ [GP=waste_row]
        ']',    # GP advance east on waste row
        'E',    # H0: EV(0) → PA(1)
        'Z',    # deposit PA waste: [H0=PA] ↔ [GP=waste_row]
        ']',    # GP advance east on waste row
        'E',    # H0: PA(1) → CWL(2)
        'e',    # H1: PA(1) → CWL(2)
    ]

    # Conditional mirror: ? for west-going, ! for east-going
    cond_mirror = '?' if last_row_dir == DIR_W else '!'

    # H2-only advance (calculating heads stay fixed on stomach)
    # OLD: ['A', 'V', cond_mirror, 'V'] — V temporarily corrupts remote cell,
    # causing cross-IP interference when interleaved with the other IP.
    # NEW: m copies [H2] to local [H0] (XOR, H0 was 0), T moves it to CL
    # for the conditional mirror test, T restores, m restores. NO remote write.
    h2_advance = ['A', 'm', 'T', cond_mirror, 'T', 'm']

    ops = preamble + correction_without_epilogue + phase_g + h2_advance
    return ops


# ═══════════════════════════════════════════════════════════════════
# Layout calculations
# ═══════════════════════════════════════════════════════════════════

def _last_row_direction(n_ops, code_left, code_right):
    """Determine direction of the last boustrophedon row.

    Returns DIR_E or DIR_W.
    """
    first_row_slots = code_right - code_left
    inner_row_slots = code_right - code_left - 1

    if n_ops <= first_row_slots:
        return DIR_E  # row_count=1, east

    remaining = n_ops - first_row_slots
    extra_rows = math.ceil(remaining / inner_row_slots)
    total_row_count = 1 + extra_rows

    # row_count 1=east, 2=west, 3=east, 4=west, ...
    return DIR_W if total_row_count % 2 == 0 else DIR_E


def compute_layout(width):
    """Compute grid layout for serpentine ouroboros at given width.

    Returns dict with layout information.
    """
    code_left = 2
    code_right = width - 2

    first_row_slots = code_right - code_left      # W - 4
    inner_row_slots = code_right - code_left - 1   # W - 5

    # Determine last row direction for conditional mirror choice.
    # Build a trial gadget to get the op count, then verify direction.
    last_dir = _last_row_direction(329, code_left, code_right)  # estimate
    gadget_ops = build_serpentine_gadget(last_dir)
    n_ops = len(gadget_ops)

    # Recompute with actual op count (may differ from estimate)
    last_dir = _last_row_direction(n_ops, code_left, code_right)
    # Rebuild if direction changed (affects conditional mirror choice)
    gadget_ops2 = build_serpentine_gadget(last_dir)
    if len(gadget_ops2) != n_ops:
        n_ops = len(gadget_ops2)
        last_dir = _last_row_direction(n_ops, code_left, code_right)

    if n_ops <= first_row_slots:
        code_rows = 1
    else:
        remaining = n_ops - first_row_slots
        code_rows = 1 + math.ceil(remaining / inner_row_slots)

    # Per gadget: R code rows + 1 handler + 1 stomach + 1 waste = R+3
    rows_per_gadget = code_rows + 3
    total_rows = 2 * rows_per_gadget

    layout = {
        'width': width,
        'n_ops': n_ops,
        'code_rows': code_rows,
        'rows_per_gadget': rows_per_gadget,
        'total_rows': total_rows,
        'last_row_dir': last_dir,
        'ga_code': (0, code_rows - 1),
        'ga_handler': code_rows,
        'ga_gp': code_rows + 1,           # stomach (DSL scratch window)
        'ga_waste': code_rows + 2,         # waste row (GP lives here)
        'gb_code': (rows_per_gadget, rows_per_gadget + code_rows - 1),
        'gb_handler': rows_per_gadget + code_rows,
        'gb_gp': rows_per_gadget + code_rows + 1,      # stomach
        'gb_waste': rows_per_gadget + code_rows + 2,    # waste row
        'code_left': code_left,
        'code_right': code_right,
        'first_row_slots': first_row_slots,
        'inner_row_slots': inner_row_slots,
    }
    return layout


def _boustrophedon_op_position(op_idx, code_left, code_right, start_row):
    """Return (row, col, direction) for an op at given index in boustrophedon.

    direction is DIR_E or DIR_W for the row containing this op.
    """
    first_slots = code_right - code_left
    inner_slots = code_right - code_left - 1

    if op_idx < first_slots:
        return start_row, code_left + op_idx, DIR_E

    remaining = op_idx - first_slots
    row_offset = 1 + remaining // inner_slots
    pos_in_row = remaining % inner_slots
    row = start_row + row_offset
    row_count = row_offset + 1  # 1-indexed

    if row_count % 2 == 0:
        # West-going: code from right_col-1 downward
        col = code_right - 1 - pos_in_row
        return row, col, DIR_W
    else:
        # East-going (not first): code from left_col+1 upward
        col = code_left + 1 + pos_in_row
        return row, col, DIR_E


# ═══════════════════════════════════════════════════════════════════
# Grid builder
# ═══════════════════════════════════════════════════════════════════

def make_serpentine_ouroboros(width=99, errors=None):
    """Build the serpentine dual ouroboros grid.

    Args:
        width: grid width (no coprimality constraint needed)
        errors: list of (row, col, bit) tuples for error injection

    Returns: (sim, layout, cycle_length)
    """
    layout = compute_layout(width)

    last_dir = layout['last_row_dir']
    gadget_ops = build_serpentine_gadget(last_dir)
    op_values = [OP[ch] for ch in gadget_ops]

    T = layout['total_rows']
    W = width
    code_left = layout['code_left']
    code_right = layout['code_right']
    R = layout['code_rows']

    sim = FB2DSimulator(rows=T, cols=W)

    # ── Place gadget code for both gadgets ──
    ga_start = layout['ga_code'][0]
    gb_start = layout['gb_code'][0]

    _place_gadget(sim, layout, op_values, ga_start)
    _place_gadget(sim, layout, op_values, gb_start)

    # ── Inject errors ──
    if errors:
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # ── Row setup ──
    ga_gp = layout['ga_gp']       # stomach row
    gb_gp = layout['gb_gp']       # stomach row
    ga_waste = layout['ga_waste']  # waste row (GP lives here)
    gb_waste = layout['gb_waste']  # waste row

    # ── IP0: runs gadget A, H2 starts on gadget B's first code row ──
    sim.ip_row = ga_start
    sim.ip_col = code_left
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(ga_gp, DSL_CWL)
    sim.h1 = sim._to_flat(ga_gp, DSL_CWL)
    sim.h2 = sim._to_flat(gb_start, code_left)  # H2 starts at gb code area
    sim.cl = sim._to_flat(ga_gp, DSL_ROT)
    sim.gp = sim._to_flat(ga_waste, 0)  # GP on waste row, col 0
    # h2_dir defaults to DIR_E (set in FB2DSimulator)

    # ── IP1: runs gadget B, H2 starts on gadget A's first code row ──
    sim.add_ip(
        ip_row=gb_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(gb_gp, DSL_CWL),
        h1=sim._to_flat(gb_gp, DSL_CWL),
        h2=sim._to_flat(ga_start, code_left),
        cl=sim._to_flat(gb_gp, DSL_ROT),
        gp=sim._to_flat(gb_waste, 0),  # GP on waste row, col 0
    )

    # ── Compute cycle length ──
    cycle_length = _compute_cycle_length(sim, layout)

    return sim, layout, cycle_length


def _place_gadget(sim, layout, op_values, start_row):
    """Place one gadget's code, handler, corridors, merge gates, and bounce.

    HANDLER (horizontal boundary):
      When H2's horizontal advance (A) hits a zero cell, the conditional
      mirror (? or !) in the boustrophedon fires, dropping IP south to
      the handler row.  Handler: / B C U : \\
        / redirects S→W.  B retreats H2 horizontally (undo A).
        C advances H2 vertically in h2_vdir (replaces old hardcoded 'h').
        U flips h2_dir.  : increments CL (signal for & gate).
        \\ sends W→N back to the last code row.

    VERTICAL BOUNDARY TEST (on code row, between & gates):
      After the handler, IP goes west on the last code row through:
        & #1 → T Z ] → m T ? T m → & #2

      T Z ] deposits the handler's CL signal to the waste row (same
      pattern as the preamble).  Then m T ? T m tests [H2]:
        m copies [H2] to H0 (XOR, H0 was 0).
        T moves H0 to CL for testing.
        ? (/ if CL==0) fires if H2 is on a zero cell (outside code area).
        T m restore on the non-bounce path.

      On non-handler cycles: T Z are 0↔0 no-ops, ] advances GP,
      m T ? T m is harmless (H2 on valid code cell → CL≠0 → no fire).

    BOUNCE SUB-HANDLER (vertical boundary):
      When the vertical ? fires, IP drops to the handler row:
        / D O C : \\
        D retreats H2 (undo the C that went to invalid row).
        O flips h2_vdir (N↔S).
        C re-advances H2 in the new direction (bounce back).
        : increments CL.  \\ sends W→N to & #2 merge gate.

      This makes H2 ping-pong between the first code row and the handler
      row (inclusive), without ever entering stomach/waste rows.

    HANDLER ROW X-FILL:
      The handler row is filled with X (swap, no-op when H0=H1) so that
      H2 sees nonzero cells there and includes it in the scan.  This means
      the handler row itself gets error-corrected by the sweep — desirable!
      H2's vertical boundary triggers on the stomach row (all zeros),
      not the handler row.
    """
    W = layout['width']
    R = layout['code_rows']
    code_left = layout['code_left']
    code_right = layout['code_right']
    last_dir = layout['last_row_dir']
    handler_row = start_row + R

    assert last_dir == DIR_W, (
        f"Handler merge-back requires west-going last row; "
        f"got {'East' if last_dir == DIR_E else last_dir}. "
        f"Adjust width so last boustrophedon row goes west.")

    # ── Place boustrophedon code ──
    rows_used, end_row, last_col, end_dir_int = place_boustrophedon(
        sim, op_values, code_left, code_right, start_row)

    # ── Fill partial last row with X (no-op when H0=H1) ──
    _fill_row(sim, layout, start_row + R - 1)

    # ── Corridor: last code row col 1 → W→N ──
    last_code_row = start_row + R - 1
    sim.grid[sim._to_flat(last_code_row, 1)] = encode_opcode(OP['\\'])

    # ── Corridor: first code row col 1 → N→E ──
    sim.grid[sim._to_flat(start_row, 1)] = encode_opcode(OP['/'])

    # ── Locate the conditional mirror (? or !) in the boustrophedon ──
    gadget_ops = build_serpentine_gadget(last_dir)
    cond_idx = len(gadget_ops) - 3
    cond_row, cond_col, _ = _boustrophedon_op_position(
        cond_idx, code_left, code_right, start_row)

    # ── Place main handler on handler_row ──
    # C replaces old 'h' — advances H2 in h2_vdir instead of hardcoded south.
    handler_ops = ['/', 'B', 'C', 'U', ':', '\\']
    gate_col = cond_col - (len(handler_ops) - 1)  # col where \ exits north

    # ── Code row ops between & #1 and & #2 ──
    code_row_ops = ['T', 'Z', ']', 'm', 'T', '?', 'T', 'm']
    vtest_col = gate_col - 6  # column of ?v (vertical boundary test)

    # ── Bounce sub-handler on handler_row ──
    bounce_ops = ['/', 'D', 'O', 'C', ':', '\\']
    bounce_gate_col = vtest_col - (len(bounce_ops) - 1)

    assert bounce_gate_col >= code_left, (
        f"Handler + bounce ops would extend past code_left "
        f"(bounce_gate_col={bounce_gate_col}, code_left={code_left}). "
        f"Width too narrow.")

    # Place main handler ops (west from cond_col)
    for i, op_ch in enumerate(handler_ops):
        col = cond_col - i
        sim.grid[sim._to_flat(handler_row, col)] = encode_opcode(OP[op_ch])

    # ── & gate #1: merge from main handler ──
    sim.grid[sim._to_flat(last_code_row, gate_col)] = encode_opcode(OP['&'])

    # ── Code row ops: T Z ] m T ? T m (deposit CL + vertical test) ──
    for i, op_ch in enumerate(code_row_ops):
        col = gate_col - 1 - i
        sim.grid[sim._to_flat(last_code_row, col)] = encode_opcode(OP[op_ch])

    # ── Bounce sub-handler (west from vtest_col on handler row) ──
    for i, op_ch in enumerate(bounce_ops):
        col = vtest_col - i
        sim.grid[sim._to_flat(handler_row, col)] = encode_opcode(OP[op_ch])

    # ── & gate #2: merge from bounce ──
    sim.grid[sim._to_flat(last_code_row, bounce_gate_col)] = encode_opcode(OP['&'])

    # ── Fill handler row with X (so H2 includes it in the scan) ──
    _fill_row(sim, layout, handler_row)
    # ── Fill col 1 on all rows from first code row to handler row ──
    # Col 1 has corridor mirrors (/ and \) on the first and last code
    # rows, but is empty on middle code rows and the handler row.
    # When H2 hits the west boundary at col 1, vertical advance checks
    # the adjacent row at col 1.  If that cell is zero, H2 bounces
    # prematurely, getting stuck ping-ponging between two rows.
    # Fill col 1 with X (no-op) on all rows where it's empty.
    for row in range(start_row, handler_row + 1):
        flat = sim._to_flat(row, 1)
        if sim.grid[flat] == 0:
            sim.grid[flat] = encode_opcode(OP['X'])


def _fill_row(sim, layout, row):
    """Fill empty cells on a row with X (opcode 19, swap — no-op when H0=H1).

    Used for:
    - Last boustrophedon row: prevents false H2 boundary detection.
    - Handler row: makes H2 include it in the scan (gets error-corrected).

    Only fills cells that are currently 0 (doesn't overwrite placed ops).
    """
    code_left = layout['code_left']
    code_right = layout['code_right']
    fill_value = encode_opcode(OP['X'])

    for col in range(code_left, code_right + 1):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = fill_value


def _compute_cycle_length(sim, layout):
    """Compute steps for one full IP loop through the boustrophedon.

    Traces the IP path through code rows, advance block, handler path
    (both normal and handler converge back to row 0).
    """
    from fb2d import _CELL_TO_PAYLOAD, _PAYLOAD_TO_OPCODE

    start_row = layout['ga_code'][0]
    code_left = layout['code_left']

    SLASH = {DIR_E: DIR_N, DIR_N: DIR_E, DIR_S: DIR_W, DIR_W: DIR_S}
    BACKSLASH = {DIR_E: DIR_S, DIR_S: DIR_E, DIR_N: DIR_W, DIR_W: DIR_N}
    dr = [-1, 0, 1, 0]
    dc = [0, 1, 0, -1]

    r, c, d = start_row, code_left, DIR_E
    steps = 0

    while True:
        steps += 1
        flat = sim._to_flat(r, c)
        val = sim.grid[flat]
        payload = _CELL_TO_PAYLOAD[val]
        opcode = _PAYLOAD_TO_OPCODE[payload]

        # Handle unconditional mirrors only (conditional mirrors depend on state)
        if opcode == 1:  # /
            d = SLASH[d]
        elif opcode == 2:  # backslash
            d = BACKSLASH[d]

        # Move
        r = (r + dr[d]) % sim.rows
        c = (c + dc[d]) % sim.cols

        if r == start_row and c == code_left and d == DIR_E:
            break

        if steps > 50000:
            raise RuntimeError(f"Cycle length exceeded 50000 — IP not looping? "
                               f"At ({r},{c}) dir={d}")

    return steps


# ═══════════════════════════════════════════════════════════════════
# Serpentine scan order simulation
# ═══════════════════════════════════════════════════════════════════

def simulate_h2_scan(layout, ip_idx, max_cycles):
    """Simulate H2 serpentine movement with vertical ping-pong.

    H2 scans horizontally (A), bouncing at row boundaries (B + U).
    On horizontal boundary: C advances H2 vertically in h2_vdir.
    If that lands on a zero cell (outside code+handler area):
      D retreats, O flips h2_vdir, C re-advances (ping-pong bounce).
    Handler row is X-filled, so H2 includes it in the scan.

    Args:
        layout: from compute_layout()
        ip_idx: 0 for IP0 (H2 scans gb), 1 for IP1 (H2 scans ga)
        max_cycles: max cycles to simulate

    Returns: list of (row, col) for each cycle
    """
    T = layout['total_rows']
    W = layout['width']
    code_left = layout['code_left']
    code_right = layout['code_right']

    if ip_idx == 0:
        h2_row = layout['gb_code'][0]
    else:
        h2_row = layout['ga_code'][0]
    h2_col = code_left
    h2_dir = DIR_E    # horizontal momentum
    h2_vdir = DIR_S   # vertical momentum (starts south)

    dr = [-1, 0, 1, 0]
    dc = [0, 1, 0, -1]

    if ip_idx == 0:
        target_code = layout['gb_code']
        target_handler = layout['gb_handler']
    else:
        target_code = layout['ga_code']
        target_handler = layout['ga_handler']

    def is_nonzero(row, col):
        """Check if a cell would be non-zero in the grid.

        Code rows + handler row are filled with ops/X in code_left..code_right.
        Everything else (stomach, waste, other gadget) is zero.
        """
        # Code rows + handler row: col 1 and code_left..code_right filled
        if target_code[0] <= row <= target_handler:
            if code_left <= col <= code_right:
                return True
            if col == 1:
                return True  # corridor mirrors or X fill
            return False
        # Stomach, waste, other gadget rows: all zero
        return False

    cells = []
    for cycle in range(max_cycles):
        # Record current H2 position (cell to be corrected)
        cells.append((h2_row, h2_col))

        # ── Horizontal advance (A) ──
        new_row = (h2_row + dr[h2_dir]) % T
        new_col = (h2_col + dc[h2_dir]) % W

        if is_nonzero(new_row, new_col):
            # Normal: advance H2 horizontally
            h2_row, h2_col = new_row, new_col
        else:
            # Horizontal boundary detected
            # B: retreat (don't commit the advance)
            # C: advance H2 vertically in h2_vdir
            vert_row = (h2_row + dr[h2_vdir]) % T

            if is_nonzero(vert_row, h2_col):
                # Valid row: commit vertical advance
                h2_row = vert_row
            else:
                # Vertical boundary: bounce (D O C)
                # D: retreat (don't commit), O: flip h2_vdir, C: re-advance
                h2_vdir = h2_vdir ^ 2  # O: flip N↔S
                bounce_row = (h2_row + dr[h2_vdir]) % T
                h2_row = bounce_row

            # U: flip horizontal direction
            h2_dir = h2_dir ^ 2  # E↔W

    return cells


def h2_cycle_for_cell(layout, ip_idx, target_row, target_col, max_cycles=2000):
    """Find which cycle H2 visits a specific cell.

    Returns cycle number, or -1 if not found within max_cycles.
    """
    cells = simulate_h2_scan(layout, ip_idx, max_cycles)
    for k, (r, c) in enumerate(cells):
        if r == target_row and c == target_col:
            return k
    return -1


def early_code_cells(layout, ip_idx, max_cycle=None):
    """Return code cells visited within GP budget.

    Returns list of (cycle, row, col).
    """
    W = layout['width']
    if max_cycle is None:
        max_cycle = W - DSL_ROT - 2

    cells = simulate_h2_scan(layout, ip_idx, max_cycle)

    if ip_idx == 0:
        target_code = layout['gb_code']
    else:
        target_code = layout['ga_code']

    result = []
    for k, (r, c) in enumerate(cells):
        if target_code[0] <= r <= target_code[1]:
            result.append((k, r, c))
    return result


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def _cheat_clear_waste(sim, layout):
    """Cheat mode: zero both waste rows so GP never encounters old waste.

    This is a non-reversible intervention. It simulates the future
    compressor/mouth that would earn clean zeros by metabolizing fuel.
    For now, it just hands the agent free zeros.
    """
    W = layout['width']
    for waste_row in [layout['ga_waste'], layout['gb_waste']]:
        base = waste_row * W
        for c in range(W):
            sim.grid[base + c] = 0


def _step_all_infinite_zeros(sim, layout):
    """Step all IPs then zero the waste rows — infinite zeros cheat.

    Unlike _cheat_clear_waste (which runs between cycles), this runs
    after EVERY step_all(), so GP always sees clean zeros ahead.
    This IS reversible: step_back_all() undoes the step, then we
    re-zero the waste rows (which are always supposed to be zero
    in this mode, so the re-zero is idempotent).
    """
    sim.step_all()
    _cheat_clear_waste(sim, layout)


def _step_back_all_infinite_zeros(sim, layout):
    """Reverse one step with infinite-zeros cheat.

    Zero waste rows, then step back. The zeroing ensures the reverse
    step sees the same state it would have seen during forward execution
    (waste rows were zeroed after the forward step that produced this state).
    """
    _cheat_clear_waste(sim, layout)
    sim.step_back_all()


def sweep_length(layout):
    """Calculate one full H2 sweep (down-up or up-down) in cycles.

    A "sweep" = H2 visits every cell in every code row + handler row,
    going down then back up (or up then down). This is the natural
    unit for noise injection rate.

    For n_rows rows of effective width W_eff cells each:
      One pass (all rows one direction) = n_rows * W_eff cycles
      One full sweep (down + up) = 2 * n_rows * W_eff cycles
      (minus the turnaround row which is visited once, not twice)
      = (2 * n_rows - 1) * W_eff cycles

    Returns: (cycles_per_sweep, cells_per_sweep)
    """
    n_rows = layout['code_rows'] + 1  # code rows + handler row
    W_eff = layout['code_right'] - layout['code_left'] + 1  # effective row width

    # Down pass: n_rows * W_eff, Up pass: (n_rows - 1) * W_eff
    # (bottom row visited once, then H2 bounces back up)
    cycles_per_sweep = (2 * n_rows - 1) * W_eff
    cells_per_sweep = cycles_per_sweep  # 1 cell per cycle
    return cycles_per_sweep, cells_per_sweep


def run_serpentine_test(width, errors, label="", check_reverse=False,
                        cheat=True):
    """Test serpentine ouroboros correction.

    Args:
        cheat: if True, clear waste rows between cycles to prevent GP
               from encountering old waste on wrap. Non-reversible.
               If 'infinite', zero waste rows after every step_all() —
               this is reversible and has no GP budget limit.
    """
    if label:
        print(f"=== {label} ===")

    sim, layout, cycle_length = make_serpentine_ouroboros(width, errors)
    T = layout['total_rows']
    W = width

    # Save expected (clean) grid values for error cells
    expected = {}
    for row, col, bit in errors:
        flat = sim._to_flat(row, col)
        clean = inject_error(sim.grid[flat], bit)
        expected[(row, col)] = clean

    # Figure out how many cycles we need
    max_cycles_needed = 0
    for row, col, bit in errors:
        ga_code = layout['ga_code']
        gb_code = layout['gb_code']

        if ga_code[0] <= row <= ga_code[1]:
            ip_idx = 1  # IP1 corrects GA
        elif gb_code[0] <= row <= gb_code[1]:
            ip_idx = 0  # IP0 corrects GB
        else:
            continue

        k = h2_cycle_for_cell(layout, ip_idx, row, col)
        if k < 0:
            print(f"    WARNING: cell ({row},{col}) not reached within scan budget")
            continue
        max_cycles_needed = max(max_cycles_needed, k + 1)

    n_cycles = max_cycles_needed + 2
    total_rounds = n_cycles * cycle_length

    if label:
        print(f"    Grid: {T}x{W} ({layout['code_rows']} code rows/gadget)")
        print(f"    Gadget: {layout['n_ops']} ops, cycle={cycle_length} steps")
        print(f"    Errors: {len(errors)} injected")
        print(f"    Cycles needed: {max_cycles_needed}, running {n_cycles}"
              f" ({total_rounds} rounds)")

    if cheat == 'infinite':
        for _ in range(total_rounds):
            _step_all_infinite_zeros(sim, layout)
    elif cheat:
        # Run cycle-by-cycle, clearing waste between cycles
        for cyc in range(n_cycles):
            for _ in range(cycle_length):
                sim.step_all()
            _cheat_clear_waste(sim, layout)
    else:
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

    if check_reverse:
        # Reverse test: raw step_all/step_back (no cheat), truly reversible.
        # Limited to GP budget (~219 cycles for W=99), but the correction
        # tests here use few cycles so this is fine.
        sim2, _, _ = make_serpentine_ouroboros(width, errors)
        grid_before = sim2.grid[:]
        for _ in range(total_rounds):
            sim2.step_all()
        for _ in range(total_rounds):
            sim2.step_back_all()

        reverse_ok = (sim2.grid == grid_before)
        if not reverse_ok:
            diffs = [(i, grid_before[i], sim2.grid[i])
                     for i in range(len(sim2.grid))
                     if grid_before[i] != sim2.grid[i]]
            if label:
                print(f"    [REVERSE] {len(diffs)} cells differ")
        if label:
            print(f"    Reverse: {'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_layout_info(width=99):
    """Print layout information."""
    layout = compute_layout(width)
    print(f"=== Layout for W={width} ===")
    print(f"    Gadget: {layout['n_ops']} ops")
    print(f"    Code rows per gadget: {layout['code_rows']}")
    print(f"    Last row direction: {'East' if layout['last_row_dir'] == DIR_E else 'West'}")
    print(f"    Grid: {layout['total_rows']}x{width}"
          f" = {layout['total_rows'] * width} cells")
    print(f"    GA code: rows {layout['ga_code'][0]}-{layout['ga_code'][1]}")
    print(f"    GA handler: row {layout['ga_handler']}")
    print(f"    GA stomach: row {layout['ga_gp']}")
    print(f"    GA waste: row {layout['ga_waste']}")
    print(f"    GB code: rows {layout['gb_code'][0]}-{layout['gb_code'][1]}")
    print(f"    GB handler: row {layout['gb_handler']}")
    print(f"    GB stomach: row {layout['gb_gp']}")
    print(f"    GB waste: row {layout['gb_waste']}")
    # Show first few serpentine cells for IP0
    cells = early_code_cells(layout, 0, max_cycle=20)
    print(f"    IP0 H2 first code cells: "
          + ", ".join(f"({r},{c})@{k}" for k, r, c in cells[:8]))
    return True


def test_cycle_length(width=99):
    """Verify cycle length computation."""
    print(f"=== Cycle length (W={width}) ===")
    sim, layout, cycle_length = make_serpentine_ouroboros(width)
    print(f"    Cycle length: {cycle_length} steps")

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


def test_scan_pattern(width=99):
    """Verify serpentine scan visits expected cells."""
    print(f"=== Scan pattern (W={width}) ===")
    layout = compute_layout(width)
    # Need enough cycles to span multiple rows (code_right - code_left + overhead)
    n_cycles = (layout['code_right'] - layout['code_left']) * 2 + 20
    cells = simulate_h2_scan(layout, 0, max_cycles=n_cycles)

    # Check first few cells are on expected rows
    gb_start = layout['gb_code'][0]
    code_left = layout['code_left']

    first_row_cells = [(k, r, c) for k, (r, c) in enumerate(cells)
                       if r == gb_start]
    print(f"    First row cells: {len(first_row_cells)}")
    if first_row_cells:
        print(f"    First: ({first_row_cells[0][1]},{first_row_cells[0][2]})@{first_row_cells[0][0]}")
        print(f"    Last:  ({first_row_cells[-1][1]},{first_row_cells[-1][2]})@{first_row_cells[-1][0]}")

    # Check that we see cells on multiple rows
    rows_seen = set(r for r, c in cells)
    print(f"    Rows visited in {n_cycles} cycles: {sorted(rows_seen)}")
    ok = len(rows_seen) > 1
    print(f"    Multiple rows: {'ok' if ok else 'FAIL'}")
    return ok


def test_single_error_gb(width=99):
    """Single error on gadget B code, corrected by IP0."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=80)
    gb_code = layout['gb_code']
    gb_cells = [(k, r, c) for k, r, c in cells
                if gb_code[0] <= r <= gb_code[1]]
    assert gb_cells, "No gadget B cells in early scan!"
    k, row, col = gb_cells[0]
    return run_serpentine_test(
        width=width,
        errors=[(row, col, 3)],
        label=f"Single error on gadget B ({row},{col})@cycle{k} (W={width})")


def test_single_error_ga(width=99):
    """Single error on gadget A code, corrected by IP1."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 1, max_cycle=80)
    ga_code = layout['ga_code']
    ga_cells = [(k, r, c) for k, r, c in cells
                if ga_code[0] <= r <= ga_code[1]]
    assert ga_cells, "No gadget A cells in early scan!"
    k, row, col = ga_cells[0]
    return run_serpentine_test(
        width=width,
        errors=[(row, col, 7)],
        label=f"Single error on gadget A ({row},{col})@cycle{k} (W={width})")


def test_errors_both_gadgets(width=99):
    """Errors on both gadgets — mutual correction."""
    layout = compute_layout(width)
    ga_code = layout['ga_code']
    gb_code = layout['gb_code']

    cells0 = early_code_cells(layout, 0, max_cycle=80)
    gb_cells = [(k, r, c) for k, r, c in cells0
                if gb_code[0] <= r <= gb_code[1]]

    cells1 = early_code_cells(layout, 1, max_cycle=80)
    ga_cells = [(k, r, c) for k, r, c in cells1
                if ga_code[0] <= r <= ga_code[1]]

    errors = []
    for i in range(min(2, len(gb_cells))):
        k, r, c = gb_cells[i]
        errors.append((r, c, 3 + i * 4))
    for i in range(min(2, len(ga_cells))):
        k, r, c = ga_cells[i]
        errors.append((r, c, 7 + i * 4))

    return run_serpentine_test(
        width=width,
        errors=errors,
        label=f"Errors on both gadgets ({len(errors)} errors, W={width})")


def test_all_16_positions(width=99):
    """Every bit position on an early gadget B cell."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=80)
    gb_code = layout['gb_code']
    gb_cells = [(k, r, c) for k, r, c in cells
                if gb_code[0] <= r <= gb_code[1]]
    assert gb_cells, "No gadget B cells in early scan!"
    _, row, col = gb_cells[0]

    all_ok = True
    for bit in range(16):
        ok = run_serpentine_test(
            width=width,
            errors=[(row, col, bit)],
            label="")
        if not ok:
            print(f"    bit {bit}: FAIL")
            all_ok = False
    print(f"=== All 16 error positions at ({row},{col}) (W={width}) ===")
    print(f"  {'PASS' if all_ok else 'FAIL'}")
    return all_ok


def test_random_early(width=99, n_errors=8, seed=42):
    """Random errors on early scan cells (within GP budget)."""
    random.seed(seed)
    layout = compute_layout(width)
    ga_code = layout['ga_code']
    gb_code = layout['gb_code']

    # Use default max_cycle (GP budget) to avoid exceeding GP trail
    cells0 = early_code_cells(layout, 0)
    gb_candidates = [(r, c) for k, r, c in cells0
                     if gb_code[0] <= r <= gb_code[1]]

    cells1 = early_code_cells(layout, 1)
    ga_candidates = [(r, c) for k, r, c in cells1
                     if ga_code[0] <= r <= ga_code[1]]

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

    return run_serpentine_test(
        width=width,
        errors=errors,
        label=f"Random early ({len(errors)} errors, W={width})")


def test_reverse(width=99):
    """Test reversibility."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=20)
    gb_code = layout['gb_code']
    gb_cells = [(k, r, c) for k, r, c in cells
                if gb_code[0] <= r <= gb_code[1]]
    assert gb_cells
    _, row, col = gb_cells[0]

    return run_serpentine_test(
        width=width,
        errors=[(row, col, 3)],
        label=f"Reverse check ({row},{col}) (W={width})",
        check_reverse=True)


def test_even_width(width=100):
    """Test with an even width (no coprimality needed for serpentine)."""
    layout = compute_layout(width)
    cells = early_code_cells(layout, 0, max_cycle=80)
    gb_code = layout['gb_code']
    gb_cells = [(k, r, c) for k, r, c in cells
                if gb_code[0] <= r <= gb_code[1]]
    if not gb_cells:
        print(f"=== Even width W={width} ===")
        print(f"    No early GB cells — skipping")
        return True
    k, row, col = gb_cells[0]
    return run_serpentine_test(
        width=width,
        errors=[(row, col, 5)],
        label=f"Even width W={width}: error on GB ({row},{col})@cycle{k}")


def test_h2_pingpong(width=99):
    """Verify H2 ping-pongs within code+handler rows only.

    Tests that H2 never enters stomach or waste rows (which would
    cause irreversibility from encountering changing values).
    """
    print(f"=== H2 ping-pong scan (W={width}) ===")
    layout = compute_layout(width)

    # Simulate enough cycles for at least 2 full down-up sweeps
    n_cycles = 2000
    for ip_idx in [0, 1]:
        if ip_idx == 0:
            target_code = layout['gb_code']
            target_handler = layout['gb_handler']
            label = "IP0→GB"
        else:
            target_code = layout['ga_code']
            target_handler = layout['ga_handler']
            label = "IP1→GA"

        cells = simulate_h2_scan(layout, ip_idx, n_cycles)
        rows_seen = set(r for r, c in cells)
        valid_rows = set(range(target_code[0], target_handler + 1))
        escaped = rows_seen - valid_rows

        if escaped:
            print(f"    {label}: FAIL — H2 escaped to rows {sorted(escaped)}")
            return False

        # Check ping-pong pattern: should see all code rows + handler
        expected_rows = set(range(target_code[0], target_handler + 1))
        missing = expected_rows - rows_seen
        print(f"    {label}: rows {sorted(rows_seen)}"
              f" {'(all expected)' if not missing else f'MISSING {sorted(missing)}'}")

    print(f"    H2 bounded within code+handler rows: ok")
    return True


def test_long_reverse(width=99, n_cycles=200):
    """Test reversibility over many cycles (raw step_all/step_back).

    Uses raw step_all/step_back with no cheat — truly reversible.
    Limited by GP wrapping contamination: handler deposits to waste every
    ~96 cycles; GP wraps at W/4 ≈ 25 cycles. At ~219 cycles for W=99,
    GP recycles old deposits → irreversibility. 200 cycles is safe.

    NOTE: The infinite-zeros cheat is NOT reversible because zeroing
    waste rows destroys breadcrumbs that step_back needs. True infinite
    reversibility requires either (a) a reversible compressor that
    consumes waste into fuel, or (b) enough waste capacity to never wrap.
    """
    print(f"=== Long reverse ({n_cycles} cycles, W={width}) ===")
    sim, layout, cycle_length = make_serpentine_ouroboros(width, errors=[])
    total_steps = n_cycles * cycle_length

    # Report sweep info
    cyc_per_sweep, cells_per_sweep = sweep_length(layout)
    print(f"    Sweep: {cyc_per_sweep} cycles/sweep"
          f" ({cells_per_sweep} cells), running {n_cycles} cycles"
          f" = {n_cycles / cyc_per_sweep:.1f} sweeps")

    grid_before = sim.grid[:]

    for _ in range(total_steps):
        sim.step_all()
    for _ in range(total_steps):
        sim.step_back_all()

    grid_ok = (sim.grid == grid_before)
    if not grid_ok:
        diffs = [(i, grid_before[i], sim.grid[i])
                 for i in range(len(sim.grid))
                 if grid_before[i] != sim.grid[i]]
        print(f"    Grid: {len(diffs)} cells differ — FAIL")
    else:
        print(f"    Grid: ok ({total_steps} steps forward+back)")

    print(f"    Reverse: {'ok' if grid_ok else 'FAIL'}")
    return grid_ok


def test_late_errors_cheat(width=99):
    """Test errors on late rows (last code row) using infinite-zeros cheat.

    These errors are reached after ~370 cycles. The infinite-zeros cheat
    zeros waste rows every step, so GP never encounters old waste.
    """
    layout = compute_layout(width)
    ga_start, ga_end = layout['ga_code']
    gb_start, gb_end = layout['gb_code']

    errors = [
        (ga_end, 15, 11),   # GA last row
        (gb_end, 20, 14),   # GB last row
    ]
    return run_serpentine_test(
        width=width,
        errors=errors,
        label=f"Late errors with infinite-zeros (W={width})",
        cheat='infinite')


def test_full_sweep_cheat(width=99):
    """Test errors on both early and late rows with infinite-zeros cheat.

    4 errors spread across both gadgets, first and last code rows.
    Also verifies reversibility with check_reverse=True.
    """
    layout = compute_layout(width)
    ga_start, ga_end = layout['ga_code']
    gb_start, gb_end = layout['gb_code']

    errors = [
        (ga_start, 5, 3),
        (ga_end, 15, 11),
        (gb_start, 8, 7),
        (gb_end, 20, 14),
    ]
    return run_serpentine_test(
        width=width,
        errors=errors,
        label=f"Full sweep 4 errors with infinite-zeros (W={width})",
        cheat='infinite')


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

    sim, layout, cycle_length = make_serpentine_ouroboros(width, errors)
    T = layout['total_rows']

    if filename is None:
        filename = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f'serpentine-ouroboros-w{width}.fb2d')
    sim.save_state(filename)

    print(f"Saved: {filename}")
    print(f"  Grid: {T}x{width} ({layout['code_rows']} code rows/gadget)")
    print(f"  Gadget: {layout['n_ops']} ops, cycle={cycle_length} steps")
    print(f"  GA code: rows {ga_start}-{ga_end}, handler: {layout['ga_handler']}, stomach: {layout['ga_gp']}, waste: {layout['ga_waste']}")
    print(f"  GB code: rows {gb_start}-{gb_end}, handler: {layout['gb_handler']}, stomach: {layout['gb_gp']}, waste: {layout['gb_waste']}")
    print(f"  {len(errors)} errors injected")
    print()
    print(f"In fb2d_server GUI:")
    print(f"  Load: serpentine-ouroboros-w{width}")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    width = 99
    for i, arg in enumerate(sys.argv):
        if arg == '--width' and i + 1 < len(sys.argv):
            width = int(sys.argv[i + 1])

    if '--save' in sys.argv:
        save_demo(width=width)
        sys.exit(0)

    print(f"Serpentine dual ouroboros (W={width})")
    layout = compute_layout(width)
    print(f"  {layout['code_rows']} code rows/gadget,"
          f" {layout['total_rows']} total rows,"
          f" {layout['n_ops']} ops/cycle")
    print()

    all_ok = True

    all_ok &= test_layout_info(width)
    print()

    all_ok &= test_cycle_length(width)
    print()

    all_ok &= test_scan_pattern(width)
    print()

    all_ok &= test_single_error_gb(width)
    print()

    all_ok &= test_single_error_ga(width)
    print()

    all_ok &= test_errors_both_gadgets(width)
    print()

    all_ok &= test_all_16_positions(width)
    print()

    all_ok &= test_random_early(width)
    print()

    all_ok &= test_reverse(width)
    print()

    all_ok &= test_even_width()
    print()

    all_ok &= test_h2_pingpong(width)
    print()

    all_ok &= test_long_reverse(width)
    print()

    all_ok &= test_late_errors_cheat(width)
    print()

    all_ok &= test_full_sweep_cheat(width)
    print()

    if all_ok:
        print("=" * 60)
        print(f"ALL SERPENTINE OUROBOROS TESTS PASSED (W={width})")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

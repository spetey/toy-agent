#!/usr/bin/env python3
"""
agent-v1.py -- Dual immunity gadget + metabolism (self-fueling agent).

Extends the v8 correction-mask immunity gadget with metabolism rows
that XOR duplicate fuel runs to produce zeros, replacing WastePool.

Layout per gadget (R+11 rows):
  Row 0:        BOUNDARY (~)
  Row 1:        COPY-OVER ROW
  Row 2:        CLEAN BYPASS ROW
  Row 3:        RETURN ROW (correction handlers)
  Row 4:        HANDLER ROW
  Rows 5..R+4:  CODE ROWS (boustrophedon, last row goes WEST)
  Row R+5:      METABOLISM RETURN ROW (loop bounces, above main)
  Row R+6:      METABOLISM MAIN ROW
  Row R+7:      METABOLISM CORRIDOR
  Row R+8:      BOUNDARY (~)
  Row R+9:      STOMACH
  Row R+10:     FUEL/WASTE ROW (EX)

Routing: IP exits last code row going west → / at col 2 (W→S) → south
through return row (NOP) → \\ at (metab_main, 2) (S→E) → metabolism.
After metabolism: \\ exit → corridor / (S→W) → west → \\ at col 1
(W→N) → north through all rows → / at first_code_row col 1 (N→E) →
( merge gate → next correction cycle.

Run:  python3 programs/agent-v1.py [--width W]
"""

import sys
import os
import math
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, DIR_S, DIR_W, encode_opcode)

_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

DSL_PA = dgd.DSL_PA
DSL_CWL = dgd.DSL_CWL
DSL_ROT = dgd.DSL_ROT

_v8_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'immunity-gadgets-v8-correction-mask.py')
_v8_spec = importlib.util.spec_from_file_location('v8', _v8_path)
v8 = importlib.util.module_from_spec(_v8_spec)
_v8_spec.loader.exec_module(v8)

OP = OPCODES
NOP_CELL = hamming_encode(1017)
BOUNDARY_CELL = 0xFFFF


HUNGER_PERIOD = 300  # eat every N bypass cycles (0 = disabled)


def compute_agent_layout(width):
    """Compute grid layout: v8 correction + metabolism (R+11 per gadget).

    Uses code_left=4 (one column wider corridor than v8 standalone) to
    provide col 3 as a dedicated vertical NOP lane for the hunger bypass.
    This requires width >= 89 so all 4 ? mirrors fit on the first code row.
    """
    if HUNGER_PERIOD > 0 and width < 89:
        raise ValueError(
            f"Hunger mechanism requires width >= 89 (got {width}). "
            f"Use HUNGER_PERIOD=0 for narrow agents.")
    code_left = 4 if HUNGER_PERIOD > 0 else 3
    v8_layout = v8.compute_probe_layout(width, code_left=code_left)
    code_rows = v8_layout['code_rows']
    rows_per_gadget = code_rows + 11
    total_rows = 2 * rows_per_gadget

    layout = dict(v8_layout)
    layout['rows_per_gadget'] = rows_per_gadget
    layout['total_rows'] = total_rows

    # Gadget A (rows 0..R+10)
    layout['ga_metab_return'] = 5 + code_rows    # R+5
    layout['ga_metab_main'] = 6 + code_rows      # R+6
    layout['ga_metab_corridor'] = 7 + code_rows   # R+7
    layout['ga_blank_bot'] = 8 + code_rows        # R+8
    layout['ga_stomach'] = 9 + code_rows          # R+9
    layout['ga_waste'] = 10 + code_rows           # R+10

    # Gadget B
    RPG = rows_per_gadget
    layout['gb_blank_top'] = RPG
    layout['gb_copyover'] = RPG + 1
    layout['gb_clean_bypass'] = RPG + 2
    layout['gb_return'] = RPG + 3
    layout['gb_handler'] = RPG + 4
    layout['gb_code'] = (RPG + 5, RPG + 4 + code_rows)
    layout['gb_metab_return'] = RPG + 5 + code_rows
    layout['gb_metab_main'] = RPG + 6 + code_rows
    layout['gb_metab_corridor'] = RPG + 7 + code_rows
    layout['gb_blank_bot'] = RPG + 8 + code_rows
    layout['gb_stomach'] = RPG + 9 + code_rows
    layout['gb_waste'] = RPG + 10 + code_rows

    return layout


def _clear_row(sim, row, width):
    """Zero out an entire row."""
    for col in range(width):
        sim.grid[sim._to_flat(row, col)] = 0


def _fill_row_nop(sim, row, width):
    """Fill zero cells in a row with NOP, leaving non-zero cells intact."""
    for col in range(width):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def _place(sim, row, col, op_ch):
    """Place an opcode on the grid."""
    sim.grid[sim._to_flat(row, col)] = encode_opcode(OP[op_ch])


def _place_metabolism(sim, layout, metab_return_row, metab_main_row,
                      metab_corridor_row, last_code_row, blank_bot_row):
    """Place metabolism on 3 rows + fix routing."""
    W = layout['width']

    # -- Clear the 3 metabolism rows (v8 may have placed boundary here) --
    for row in [metab_return_row, metab_main_row, metab_corridor_row]:
        _clear_row(sim, row, W)

    # -- Clear the old v8 boundary row (v8 placed it at code_start + R) --
    # It's now the metab_return row, already cleared above.

    # -- Place real boundary at blank_bot_row --
    for col in range(W):
        sim.grid[sim._to_flat(blank_bot_row, col)] = BOUNDARY_CELL

    # -- Entry routing from last code row --
    _place(sim, last_code_row, 2, '/')       # W→S: send IP south
    sim.grid[sim._to_flat(last_code_row, 1)] = NOP_CELL  # remove old \\

    # -- Metabolism main row --
    # Correction entry at col 2 (\\ S→E).
    # Hunger entry at col 3 () = \\ if [EX]==0, S→E).
    # Metabolism ops cols 4-39.
    main_ops = {
        2: '\\',   # S→E entry from correction path (clean EX)
        3: '(',    # S→E entry from hunger path (\\ if [EX]≠0)
        4: 'e',    # H1 east (separate from H0 for reference cell)
        5: 'P',    # re-dirty EX for advance ) at col 6
        6: ')',    # advance re-entry (\\ if [EX]==0)
        7: ']',    # EX east
        8: 'Z',    # swap [H0]↔[EX]
        9: 'T',    # bridge [CL]↔[H0]
        10: '?',   # advance bounce (/ if CL==0, E→N to return above)
        12: 'T',   # ref swap: undo bridge
        13: 'X',   # ref swap: swap H0↔H1
        15: '&',   # compress re-entry (\\ if CL≠0)
        16: ':',   # CL++ iteration counter
        17: ']',   # EX east
        18: 'Z',   # swap
        20: 'x',   # XOR
        21: 'T',   # bridge
        22: '?',   # compress bounce (/ if CL==0, E→N)
        23: 'T',   # mismatch undo bridge
        24: 'x',   # mismatch undo XOR
        25: 'Z',   # mismatch restore
        26: ')',   # walk-back re-entry (\\ if [EX]==0)
        27: '[',   # EX west
        28: 'Z',   # swap
        29: 'T',   # bridge
        30: '?',   # walk-back bounce (/ if CL==0, E→N)
        31: 'Z',   # dirty found: restore cell
        32: 'X',   # dump old ref to H0
        33: ']',   # EX east
        34: 'Z',   # deposit old ref
        35: 'T',   # bridge (CL has : accumulator)
        36: ']',   # EX east
        37: 'Z',   # deposit : accumulator
        38: ']',   # advance EX to clean cell
        39: '\\',  # exit E→S to corridor
    }
    for col, op_ch in main_ops.items():
        _place(sim, metab_main_row, col, op_ch)

    # Boundary markers
    sim.grid[sim._to_flat(metab_main_row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(metab_main_row, W - 1)] = BOUNDARY_CELL

    _fill_row_nop(sim, metab_main_row, W)

    # -- Return row (ABOVE main) --
    # Loop bounces: ? fires E→N from main → \\ (N→W) on return → T → / (W→S) back to main
    return_ops = {
        # Advance: ?@10 → \\@10 → T@9 → /@6 → )@6
        10: '\\', 9: 'T', 6: '/',
        # Compress: ?@22 → \\@22 → T@19 → /@15 → &@15
        22: '\\', 19: 'T', 15: '/',
        # Walk-back: ?@30 → \\@30 → T@29 → /@26 → )@26
        30: '\\', 29: 'T', 26: '/',
    }
    for col, op_ch in return_ops.items():
        _place(sim, metab_return_row, col, op_ch)

    # (No P at col 2 — correction enters metab_main with clean EX so
    # ( at col 3 is NOP.  Hunger enters with dirty EX so ( fires S→E.)

    sim.grid[sim._to_flat(metab_return_row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(metab_return_row, W - 1)] = BOUNDARY_CELL
    _fill_row_nop(sim, metab_return_row, W)

    # -- Corridor row (BELOW main) --
    # \\@39 main → E→S → /@39 corridor (S→W) → west → \\@1 (W→N) → north
    #
    # Counter reset: after metabolism, zero DSL_PA and reload hunger period.
    # Counter reset: reload DSL_S2 (countdown, col 5) from DSL_S1 (period
    # constant, col 4).  H0 goes CWL→S0→S1→S2 (3×E), Z deposits old S2
    # (dirty from hunger detour's + or 0 from correction) into waste, then
    # . loads period from S1.  3×W restores H0 to CWL.
    # + before Z ensures non-zero deposit (S2 may be 0 on correction path).
    corridor_ops = {
        39: '/',    # S→W
        38: 'w',    # H1 west (restore H1: S0 → CWL)
        37: 'E',    # H0 east (CWL → S0)
        36: 'E',    # H0 east (S0 → S1)
        35: 'E',    # H0 east (S1 → S2 = countdown cell)
        34: '+',    # [S2]++ (ensure non-zero deposit)
        33: 'Z',    # swap [S2≥1] ↔ [EX]: deposit non-zero
        32: ']',    # advance EX past deposit
        31: 'e',    # H1 east (CWL → S0)
        30: 'e',    # H1 east (S0 → S1 = period cell)
        29: '.',    # [S2] += [S1] → S2 = 0 + period = period
        28: 'w',    # H1 west (S1 → S0)
        27: 'w',    # H1 west (S0 → CWL, restored)
        26: 'W',    # H0 west (S2 → S1)
        25: 'W',    # H0 west (S1 → S0)
        24: 'W',    # H0 west (S0 → CWL, restored)
        1:  '\\',   # W→N → north to first_code_row
    }

    for c, op_ch in corridor_ops.items():
        _place(sim, metab_corridor_row, c, op_ch)

    sim.grid[sim._to_flat(metab_corridor_row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(metab_corridor_row, W - 1)] = BOUNDARY_CELL
    _fill_row_nop(sim, metab_corridor_row, W)


def _place_hunger_bypass(sim, layout, clean_bypass_row, return_row,
                         first_code_row):
    """Place hunger countdown on bypass row + detour on v8 return row.

    Countdown cell: DSL_S2 (stomach col 5), NOT DSL_PA (col 1).
    PA must be 0 at cycle start for Phase A parity accumulation.

    Bypass row (going west from pre_syn_col=42 toward col 2):
      col 41: T     undo pre-syndrome T (skipped by bypass)
      col 40: I     undo pre-syndrome I (skipped by bypass)
      ... NOP ...
      col 21: E     H0 east (CWL → S0)
      col 20: E     H0 east (S0 → S1)
      col 19: E     H0 east (S1 → S2 = countdown)
      col 18: T     bridge S2 → CL
      col 17: ;     decrement CL
      col 16: ?     / if CL==0 → HUNGRY (W→S)
      col 15: T     undo bridge (not hungry)
      col 14: W     S2 → S1
      col 13: W     S1 → S0
      col 12: W     S0 → CWL (restored)
      ... NOP ...
      col  3: #     / if [EX]==0 (safety gate)

    Return row detour (v8 correction return row, cols 3-7 free):
      ? fires at bypass col 16 (W→S). H0 at S2 (col 5), bridge active.
      IP goes S through return_row col 16 (NOP) and handler_row col 16
      (NOP) to first_code_row col 16 (code op — bad!).

      So we catch on return_row instead.  V8 return_row occupancy:
        col 2: P, cols 25-32: rewind ops.  Cols 3-16 available (except
        any already placed).  We use cols 7-3 (going west):
      col 16: /     catch S→W
      col 15: T     undo bridge (CL restored, S2=0)
      col 14: +     [S2]++ (S2=1, non-zero deposit)
      col 13: Z     swap [S2=1] ↔ [EX]: waste dirty ✓, [EX] gets 1
      col 12: W     S2 → S1
      col 11: W     S1 → S0
      col 10: W     S0 → CWL (restored)
      col  9: NOP   (continue west)
      ...
      col  3: /     W→S → col 3 express lane

    Handler row col 3: E (H0 east, dummy — H0 already at CWL).
    Col 3 is NOP through code rows (code_left=4).
    metab_main col 3: ( (\ if EX≠0) → S→E into metabolism.
    """
    if HUNGER_PERIOD == 0:
        return   # hunger disabled

    # -- Bypass row: pre-syndrome undo --
    _place(sim, clean_bypass_row, 41, 'T')    # undo pre-syndrome T
    _place(sim, clean_bypass_row, 40, 'I')    # undo pre-syndrome I

    # -- Bypass row: hunger countdown (DSL_S2 = col 5) --
    _place(sim, clean_bypass_row, 21, 'E')    # CWL → S0
    _place(sim, clean_bypass_row, 20, 'E')    # S0 → S1
    _place(sim, clean_bypass_row, 19, 'E')    # S1 → S2
    _place(sim, clean_bypass_row, 18, 'T')    # bridge S2 → CL
    _place(sim, clean_bypass_row, 17, ';')    # decrement
    _place(sim, clean_bypass_row, 16, '?')    # / if CL==0 → hungry
    _place(sim, clean_bypass_row, 15, 'T')    # undo bridge (not hungry)
    _place(sim, clean_bypass_row, 14, 'W')    # S2 → S1
    _place(sim, clean_bypass_row, 13, 'W')    # S1 → S0
    _place(sim, clean_bypass_row, 12, 'W')    # S0 → CWL
    # (No gate at col 3 — countdown is in S2 now, always initialized.
    #  A # here would be harmful: if it fired, / at return_row col 3
    #  would send the IP west off the gadget.)

    # -- Return row detour (v8 return row, cols 3-16 free) --
    _place(sim, return_row, 16, '/')          # catch S→W
    _place(sim, return_row, 15, 'T')          # undo bridge (S2=0)
    _place(sim, return_row, 14, '+')          # [S2]++ → 1
    _place(sim, return_row, 13, 'Z')          # swap [S2=1] ↔ [EX]
    _place(sim, return_row, 12, 'W')          # S2 → S1
    _place(sim, return_row, 11, 'W')          # S1 → S0
    _place(sim, return_row, 10, 'W')          # S0 → CWL
    _place(sim, return_row, 3, '/')           # W→S → col 3 express

    # -- Handler row col 3: NOP is fine (H0 already at CWL) --
    # (Remove old E placement — H0 was at PA before, now at CWL after 3×W)
    handler_row = first_code_row - 1
    flat = sim._to_flat(handler_row, 3)
    if sim.grid[flat] == 0:
        sim.grid[flat] = NOP_CELL

    # -- NOP fill col 3 on first code row --
    flat = sim._to_flat(first_code_row, 3)
    if sim.grid[flat] == 0:
        sim.grid[flat] = NOP_CELL


def make_agent_v1(width=88, fuel_spec=None):
    """Build the dual-gadget agent with metabolism."""
    layout = compute_agent_layout(width)
    last_dir = layout['last_row_dir']
    main_ops, probe_idx, copyover_base, pre_syn_idx, n_cl_inc = \
        v8.build_probe_bypass_gadget(last_dir)
    op_values = [-NOP_CELL if ch == 'o' else OP[ch] for ch in main_ops]

    T = layout['total_rows']
    W = width
    sim = FB2DSimulator(rows=T, cols=W)

    ga_code_start = layout['ga_code'][0]
    gb_code_start = layout['gb_code'][0]

    # Place correction gadgets (v8) — this will place boundary at wrong
    # rows (code_start + R), which we'll fix in _place_metabolism.
    v8._place_probe_gadget(sim, layout, op_values, main_ops,
                           ga_code_start, layout['ga_copyover'],
                           layout['ga_clean_bypass'],
                           layout['ga_return'], layout['ga_handler'],
                           probe_idx, copyover_base, pre_syn_idx,
                           is_upper=True)
    v8._place_probe_gadget(sim, layout, op_values, main_ops,
                           gb_code_start, layout['gb_copyover'],
                           layout['gb_clean_bypass'],
                           layout['gb_return'], layout['gb_handler'],
                           probe_idx, copyover_base, pre_syn_idx,
                           is_upper=False)

    # Place metabolism (fixes boundary rows, clears v8's misplaced ones)
    ga_last_code = layout['ga_code'][1]
    gb_last_code = layout['gb_code'][1]
    ga_first_code = layout['ga_code'][0]
    gb_first_code = layout['gb_code'][0]

    _place_metabolism(sim, layout,
                      layout['ga_metab_return'], layout['ga_metab_main'],
                      layout['ga_metab_corridor'], ga_last_code,
                      layout['ga_blank_bot'])
    _place_metabolism(sim, layout,
                      layout['gb_metab_return'], layout['gb_metab_main'],
                      layout['gb_metab_corridor'], gb_last_code,
                      layout['gb_blank_bot'])

    # Place hunger bypass (bypass row + return row detour)
    # Uses v8's return row (correction return) cols 3-7, which are free.
    # Uses v8's clean_bypass row cols 3-10, which are NOP.
    _place_hunger_bypass(sim, layout,
                         layout['ga_clean_bypass'], layout['ga_return'],
                         ga_first_code)
    _place_hunger_bypass(sim, layout,
                         layout['gb_clean_bypass'], layout['gb_return'],
                         gb_first_code)

    # Initialize hunger: period constant at DSL_S1 (col 4), countdown at
    # DSL_S2 (col 5).  Both unused in v8 (V opcode replaced barrel shifter).
    # PA (col 1) must stay 0 — Phase A uses it as parity accumulator.
    DSL_S1 = 4
    DSL_S2 = 5
    if HUNGER_PERIOD > 0:
        for stomach_row in [layout['ga_stomach'], layout['gb_stomach']]:
            sim.grid[sim._to_flat(stomach_row, DSL_S1)] = hamming_encode(HUNGER_PERIOD)
            sim.grid[sim._to_flat(stomach_row, DSL_S2)] = hamming_encode(HUNGER_PERIOD)

    # Place fuel
    if fuel_spec is None:
        fuel_spec = [(189, 17), (250, 17), (380, 17), (639, 17)]

    def place_fuel(waste_row):
        bite = 15  # match free_food_bite_size default
        fuel_payloads = [189, 250, 380, 639]  # A, B, C, D
        sim.grid[sim._to_flat(waste_row, 0)] = hamming_encode(999)  # dirty cell
        # Fill cols 1 onward: zeros for initial buffer, then bites until end
        for c in range(1, bite + 1):
            if c < W:
                sim.grid[sim._to_flat(waste_row, c)] = 0  # initial zeros
        col = bite + 1
        bite_idx = 0
        bite_count = 0
        while col < W:
            sim.grid[sim._to_flat(waste_row, col)] = hamming_encode(fuel_payloads[bite_idx])
            col += 1
            bite_count += 1
            if bite_count % bite == 0:
                bite_idx = (bite_idx + 1) % len(fuel_payloads)

    place_fuel(layout['ga_waste'])
    place_fuel(layout['gb_waste'])

    # Set up IPs
    code_left = layout['code_left']

    sim.ip_row = ga_code_start
    sim.ip_col = code_left
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.h1 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.ix = sim._to_flat(layout['gb_copyover'], 1)
    sim.cl = sim._to_flat(layout['ga_stomach'], DSL_ROT)
    sim.ex = sim._to_flat(layout['ga_waste'], 0)

    ip1_ex = sim._to_flat(layout['gb_waste'], 0)
    sim.add_ip(
        ip_row=gb_code_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        h1=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        ix=sim._to_flat(layout['ga_copyover'], 1),
        cl=sim._to_flat(layout['gb_stomach'], DSL_ROT),
        ex=ip1_ex,
    )

    return sim, layout


if __name__ == '__main__':
    width = 89
    for i, arg in enumerate(sys.argv):
        if arg == '--width' and i + 1 < len(sys.argv):
            width = int(sys.argv[i + 1])

    print(f"Building agent-v1 (W={width})...")
    sim, layout = make_agent_v1(width)
    print(f"  Grid: {sim.rows}x{sim.cols}")
    print(f"  Rows per gadget: {layout['rows_per_gadget']}")
    print(f"  Code rows: {layout['code_rows']}")
    R = layout['code_rows']
    print(f"  GA: code rows {layout['ga_code']}, metab {layout['ga_metab_main']}, "
          f"stomach {layout['ga_stomach']}, fuel {layout['ga_waste']}")
    print(f"  GB: code rows {layout['gb_code']}, metab {layout['gb_metab_main']}, "
          f"stomach {layout['gb_stomach']}, fuel {layout['gb_waste']}")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, f'agent-v1-w{width}.fb2d')
    sim.save_state(out_path, hints={'free_food': 1})
    print(f"  Saved: {out_path}")

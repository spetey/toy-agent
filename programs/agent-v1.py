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


def compute_agent_layout(width):
    """Compute grid layout: v8 correction + metabolism (R+11 per gadget)."""
    v8_layout = v8.compute_probe_layout(width)
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
    # Entry at col 2 (\\ S→E), then metabolism ops cols 3-39.
    main_ops = {
        2: '\\',   # S→E entry from code rows
        4: 'e',    # H1 east (separate from H0 for reference cell)
        5: 'P',    # re-dirty EX (Phase G left it clean)
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

    sim.grid[sim._to_flat(metab_return_row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(metab_return_row, W - 1)] = BOUNDARY_CELL
    _fill_row_nop(sim, metab_return_row, W)

    # -- Corridor row (BELOW main) --
    # \\@39 main → E→S → /@39 corridor (S→W) → west → \\@1 (W→N) → north
    _place(sim, metab_corridor_row, 39, '/')    # S→W
    _place(sim, metab_corridor_row, 38, 'w')    # H1 west (restore H1 = H0 for correction)
    _place(sim, metab_corridor_row, 1, '\\')    # W→N → north to first_code_row

    sim.grid[sim._to_flat(metab_corridor_row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(metab_corridor_row, W - 1)] = BOUNDARY_CELL
    _fill_row_nop(sim, metab_corridor_row, W)


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
    width = 88
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

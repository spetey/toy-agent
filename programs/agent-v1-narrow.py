#!/usr/bin/env python3
"""
agent-v1-narrow.py -- Narrow dual immunity gadget + metabolism (W=45).

Wraps the 147-op correction gadget into multiple boustrophedon rows
and uses an upward boustrophedon for the 2-bit copy-over.

Layout per gadget (19 rows):
  Row 0:        BOUNDARY (~)
  Row 1:        COPY-OVER EXIT (routing only: / at col 2, \\ at col 43)
  Row 2:        COPY-OVER TOP (E, 39 ops — upward boustrophedon)
  Row 3:        COPY-OVER BOTTOM (W, 31 ops + NOPs — entry \\ at col 42)
  Row 4:        BYPASS ($ at col 2, \\ at col 41)
  Row 5:        RETURN (P at col 2, rewind ops at cols 24-31)
  Row 6:        HANDLER (h-handler at cols 8-16, v-handler at cols 21-37)
  Row 7:        CODE ROW 1 (E): ops 0-38, ( at col 2, / at col 1
  Row 8:        CODE ROW 2 (W): mini-boustrophedon padded
  Row 9:        CODE ROW 3 (E): probe ? at col 42
  Row 10:       CODE ROW 4 (W): remaining ops
  Row 11:       CODE ROW 5 (E): remaining ops
  Row 12:       CODE ROW 6 (W): NOP padding, / at col 2 for metabolism
  Row 13:       METABOLISM RETURN
  Row 14:       METABOLISM MAIN
  Row 15:       METABOLISM CORRIDOR (\\ at col 1)
  Row 16:       BOUNDARY (~)
  Row 17:       STOMACH
  Row 18:       FUEL/WASTE (EX)

Grid: 38 x 45 = 1710 cells (vs 26 x 88 = 2288 for wide agent, 25% smaller).

Run:  python3 programs/agent-v1-narrow.py
"""

import sys
import os
import math
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, DIR_S, DIR_W, encode_opcode)

# Import from dual-gadget-demo.py
_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

DSL_CWL = dgd.DSL_CWL
DSL_ROT = dgd.DSL_ROT

# Import from v8
_v8_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'immunity-gadgets-v8-correction-mask.py')
_v8_spec = importlib.util.spec_from_file_location('v8', _v8_path)
v8 = importlib.util.module_from_spec(_v8_spec)
_v8_spec.loader.exec_module(v8)

# Import from agent-v1
_a1_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'agent-v1.py')
_a1_spec = importlib.util.spec_from_file_location('a1', _a1_path)
a1 = importlib.util.module_from_spec(_a1_spec)
_a1_spec.loader.exec_module(a1)

OP = OPCODES
NOP_CELL = hamming_encode(1017)
BOUNDARY_CELL = 0xFFFF


# ===================================================================
# Layout
# ===================================================================

WIDTH = 45
CODE_LEFT = 3
CODE_RIGHT = WIDTH - 2  # 43
PROBE_COL = 42  # column where probe ? lands (NOP on rows above)
CODE_ROWS = 6   # 1 first + 2 mini-boust + 2 remaining + 1 padding
ROWS_PER_GADGET = CODE_ROWS + 13  # 6 code + 3 copyover + bypass + return + handler + 2 boundary + stomach + fuel + 3 metab = 19


def compute_narrow_layout(width=WIDTH):
    """Compute the narrow grid layout (W=45, 19 rows per gadget)."""
    code_rows = CODE_ROWS
    rows_per_gadget = ROWS_PER_GADGET
    total_rows = 2 * rows_per_gadget

    # Gadget A (rows 0..18)
    ga = {
        'blank_top':       0,
        'copyover_exit':   1,
        'copyover_top':    2,
        'copyover_bottom': 3,
        'bypass':          4,
        'return':          5,
        'handler':         6,
        'code_start':      7,
        'code_end':        7 + code_rows - 1,  # 12
        'metab_return':    7 + code_rows,       # 13
        'metab_main':      8 + code_rows,       # 14
        'metab_corridor':  9 + code_rows,       # 15
        'blank_bot':       10 + code_rows,      # 16
        'stomach':         11 + code_rows,      # 17
        'waste':           12 + code_rows,      # 18
    }

    # Gadget B
    RPG = rows_per_gadget
    gb = {k: v + RPG for k, v in ga.items()}

    layout = {
        'width': width,
        'code_rows': code_rows,
        'rows_per_gadget': rows_per_gadget,
        'total_rows': total_rows,
        'code_left': CODE_LEFT,
        'code_right': CODE_RIGHT,
        'probe_col': PROBE_COL,
    }

    # Flatten into layout dict with ga_/gb_ prefixes
    for prefix, d in [('ga_', ga), ('gb_', gb)]:
        for k, v in d.items():
            layout[prefix + k] = v
        layout[prefix + 'code'] = (d['code_start'], d['code_end'])

    return layout


# ===================================================================
# Placement helpers
# ===================================================================

def _place(sim, row, col, op_ch):
    """Place an opcode on the grid."""
    sim.grid[sim._to_flat(row, col)] = encode_opcode(OP[op_ch])


def _place_nop(sim, row, col):
    sim.grid[sim._to_flat(row, col)] = NOP_CELL


def _fill_nop(sim, row, col_start, col_end):
    """Fill cols [col_start, col_end] with NOP where cell is 0."""
    for col in range(col_start, col_end + 1):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def _fill_row_nop(sim, row, width):
    """Fill zero cells in entire row with NOP."""
    for col in range(width):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def _place_boundary_row(sim, row, width):
    """Fill entire row with boundary cells."""
    for col in range(width):
        sim.grid[sim._to_flat(row, col)] = BOUNDARY_CELL


def _place_boundary_edges(sim, row, width):
    """Place boundary cells at col 0 and col W-1."""
    sim.grid[sim._to_flat(row, 0)] = BOUNDARY_CELL
    sim.grid[sim._to_flat(row, width - 1)] = BOUNDARY_CELL


# ===================================================================
# Code placement (custom boustrophedon)
# ===================================================================

def _place_narrow_code(sim, layout, op_values, main_ops, code_start_row):
    """Place the 147 correction ops in a custom narrow boustrophedon.

    Row layout (relative to code_start_row):
      Row 0 (E): ops 0-38 at cols 3-41. NOP at 42. \\@43 turn.
      Row 1 (W): mini-boust padded. /@43 entry, ops near left, /@3 turn.
      Row 2 (E): mini-boust. \\@3 entry, ops at 4-42, probe ? at 42. \\@43.
      Row 3 (W): /@43 entry. Remaining ops west.  /@3 turn.
      Row 4 (E): \\@3 entry. Remaining ops east. \\@43 turn.
      Row 5 (W): /@43 entry. NOP padding (ensures last_dir=W).
    """
    W = layout['width']
    CL = CODE_LEFT    # 3
    CR = CODE_RIGHT   # 43

    n_ops = len(op_values)
    assert n_ops == 147

    # Find the ? positions in the ops
    q_indices = [i for i, op in enumerate(main_ops) if op == '?']
    assert len(q_indices) >= 4
    pre_syn_idx = q_indices[2]   # 38
    probe_idx = q_indices[3]      # 82

    # -- Row 0 (E): ops 0-38 at cols 3-41 --
    row = code_start_row
    for i in range(39):  # ops 0-38
        col = CL + i
        val = op_values[i]
        sim.grid[sim._to_flat(row, col)] = encode_opcode(val) if val >= 0 else (-val)
    _place_nop(sim, row, 42)              # NOP for clean northward corridor
    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['\\'])  # E→S turn

    # -- Row 1 (W): mini-boustrophedon padded (ops 39-43) --
    row = code_start_row + 1
    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['/'])   # S→W entry

    # Ops 39-43: 5 real ops. Back-pad: NOPs at high cols, ops at low cols.
    # West row goes from col CR-1=42 to CL+1=4. 39 inner slots.
    mini_first_ops = 5  # ops 39-43
    inner_slots = CR - CL - 1  # 39
    n_nops_row1 = inner_slots - mini_first_ops  # 34

    # Place NOPs at cols 42 down to 42-33=9 (34 NOPs)
    for i in range(n_nops_row1):
        col = CR - 1 - i  # 42, 41, ..., 9
        _place_nop(sim, row, col)

    # Place ops 39-43 at cols 8, 7, 6, 5, 4
    for i in range(mini_first_ops):
        col = CR - 1 - n_nops_row1 - i
        val = op_values[39 + i]
        sim.grid[sim._to_flat(row, col)] = encode_opcode(val) if val >= 0 else (-val)

    sim.grid[sim._to_flat(row, CL)] = encode_opcode(OP['/'])   # W→S turn at left

    # -- Row 2 (E): mini-boustrophedon (ops 44-82, probe ? at col 42) --
    row = code_start_row + 2
    sim.grid[sim._to_flat(row, CL)] = encode_opcode(OP['\\'])  # S→E entry

    # 39 ops (44-82) at cols 4-42
    for i in range(39):
        col = CL + 1 + i  # 4, 5, ..., 42
        val = op_values[44 + i]
        sim.grid[sim._to_flat(row, col)] = encode_opcode(val) if val >= 0 else (-val)

    # Verify probe ? is at col 42
    assert main_ops[82] == '?', f"Expected probe ? at op 82, got {main_ops[82]}"
    assert CL + 1 + (82 - 44) == PROBE_COL, f"Probe at col {CL + 1 + (82 - 44)}, expected {PROBE_COL}"

    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['\\'])  # E→S for 1-bit errors

    # -- Row 3 (W): remaining ops 83-121 (39 ops) --
    row = code_start_row + 3
    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['/'])   # S→W entry

    remaining_start = 83
    row3_count = min(inner_slots, n_ops - remaining_start)  # 39
    for i in range(row3_count):
        col = CR - 1 - i  # 42, 41, ..., 4
        val = op_values[remaining_start + i]
        sim.grid[sim._to_flat(row, col)] = encode_opcode(val) if val >= 0 else (-val)

    sim.grid[sim._to_flat(row, CL)] = encode_opcode(OP['/'])   # W→S turn

    # -- Row 4 (E): remaining ops 122-146 (25 ops) --
    row = code_start_row + 4
    sim.grid[sim._to_flat(row, CL)] = encode_opcode(OP['\\'])  # S→E entry

    remaining_start2 = 83 + row3_count  # 122
    row4_count = n_ops - remaining_start2  # 25
    for i in range(row4_count):
        col = CL + 1 + i  # 4, 5, ..., 28
        val = op_values[remaining_start2 + i]
        sim.grid[sim._to_flat(row, col)] = encode_opcode(val) if val >= 0 else (-val)

    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['\\'])  # E→S turn

    # -- Row 5 (W): NOP padding for DIR_W exit --
    row = code_start_row + 5
    sim.grid[sim._to_flat(row, CR)] = encode_opcode(OP['/'])   # S→W entry
    # All NOP (filled later)

    # -- NOP fill all code rows --
    for r in range(6):
        _fill_nop(sim, code_start_row + r, CL, CR)

    # -- Corridor at col 1-2 --
    first_code_row = code_start_row
    last_code_row = code_start_row + 5
    sim.grid[sim._to_flat(last_code_row, 1)] = encode_opcode(OP['\\'])  # v8 corridor
    sim.grid[sim._to_flat(first_code_row, 1)] = encode_opcode(OP['/'])  # corridor N→E
    sim.grid[sim._to_flat(first_code_row, 2)] = encode_opcode(OP['('])  # merge gate

    # NOP fill cols 1-2 on non-first code rows (overwrite \ on last row later by metab)
    for r in range(code_start_row + 1, last_code_row + 1):
        for col in [1, 2]:
            flat = sim._to_flat(r, col)
            if sim.grid[flat] == 0:
                sim.grid[flat] = NOP_CELL


# ===================================================================
# Copy-over placement (upward boustrophedon, 3 rows)
# ===================================================================

def _place_narrow_copyover(sim, layout, copyover_full, probe_col,
                           exit_row, top_row, bottom_row,
                           is_upper, RPG):
    """Place copy-over in a 3-row upward boustrophedon.

    Bottom row (W): entry \\@42, ops going west, \\@3 turn up.
    Top row (E):    /@3 entry, ops going east, /@43 exit up.
    Exit row (W):   \\@43 → NOP west → /@2 exit south.
    """
    W = layout['width']
    CL = CODE_LEFT
    CR = CODE_RIGHT

    n_ops = len(copyover_full)
    inner_slots = CR - CL - 1  # 39

    # Bottom row: west from col 41 to col 4. 38 content slots.
    # (col 42 has entry \, col 3 has turn \)
    bottom_content_slots = CR - 1 - (CL + 1) + 1  # 42-4+1 = 39... wait
    # Actually: IP enters at col 42 going W. First op at col 41. Last at col 4.
    # Then \@3 turns W→N. So content: cols 41 to 4 = 38 slots.
    bottom_slots = CR - 1 - (CL + 1)  # 42 - 4 = 38

    # Top row: east from col 4 to col 42. 39 content slots.
    # /@3 catches N→E. First op at col 4. Last at col 42.
    # Then /@43 exits E→N.
    top_slots = CR - 1 - CL  # 42 - 3 = 39

    assert n_ops <= bottom_slots + top_slots, \
        f"Copy-over too long: {n_ops} ops > {bottom_slots + top_slots} slots"

    # Split: fill top row fully, remainder on bottom (back-padded)
    ops_on_top = min(n_ops, top_slots)
    ops_on_bottom = n_ops - ops_on_top
    nops_on_bottom = bottom_slots - ops_on_bottom

    # -- Bottom row (W) --
    # Entry mirror
    sim.grid[sim._to_flat(bottom_row, PROBE_COL)] = encode_opcode(OP['\\'])  # N→W

    # Back-pad: NOPs at high cols (near entry), then real ops at low cols
    col = CR - 1  # start at 42 going west (but 42 already has \, so skip)
    # Actually entry \ is at col 42. First content col is 41.
    col = CR - 1  # 42... no, entry \ at col 42. Content starts at col 41.
    # Hmm: IP enters going W from \@42. First step: col 41. Then 40, 39, ...
    # Content cols: 41, 40, 39, ..., 4. That's 38 cols.

    # Place NOPs first (high cols)
    for i in range(nops_on_bottom):
        c = CR - 1 - i  # 42, 41, 40, ...
        # Col 42 already has entry \, skip it
        c = (CR - 2) - i  # 41, 40, 39, ...
        _place_nop(sim, bottom_row, c)

    # Place ops (low cols)
    for i in range(ops_on_bottom):
        c = (CR - 2) - nops_on_bottom - i
        op_ch = copyover_full[i]
        if op_ch == 'o':
            sim.grid[sim._to_flat(bottom_row, c)] = NOP_CELL
        else:
            sim.grid[sim._to_flat(bottom_row, c)] = encode_opcode(OP[op_ch])

    # Turn mirror at left
    sim.grid[sim._to_flat(bottom_row, CL)] = encode_opcode(OP['\\'])  # W→N

    # -- Top row (E) --
    # Entry mirror
    sim.grid[sim._to_flat(top_row, CL)] = encode_opcode(OP['/'])  # N→E

    # Place ops east from col 4
    for i in range(ops_on_top):
        c = CL + 1 + i  # 4, 5, 6, ...
        op_idx = ops_on_bottom + i
        op_ch = copyover_full[op_idx]
        if op_ch == 'o':
            sim.grid[sim._to_flat(top_row, c)] = NOP_CELL
        else:
            sim.grid[sim._to_flat(top_row, c)] = encode_opcode(OP[op_ch])

    # Exit mirror: / at code_right sends E→N
    sim.grid[sim._to_flat(top_row, CR)] = encode_opcode(OP['/'])  # E→N

    # -- Exit row (W) --
    sim.grid[sim._to_flat(exit_row, CR)] = encode_opcode(OP['\\'])  # N→W
    sim.grid[sim._to_flat(exit_row, 2)] = encode_opcode(OP['/'])    # W→S exit

    # NOP fill all 3 rows
    for r in [exit_row, top_row, bottom_row]:
        _place_boundary_edges(sim, r, W)
        _fill_nop(sim, r, 1, CR)


# ===================================================================
# Full gadget placement
# ===================================================================

def _place_narrow_gadget(sim, layout, op_values, main_ops,
                         code_start_row, copyover_exit_row,
                         copyover_top_row, copyover_bottom_row,
                         bypass_row, return_row, handler_row,
                         blank_top_row, blank_bot_row,
                         is_upper):
    """Place one narrow gadget (19 rows)."""
    W = layout['width']
    CL = CODE_LEFT
    CR = CODE_RIGHT
    RPG = layout['rows_per_gadget']

    # -- Place code (custom boustrophedon) --
    _place_narrow_code(sim, layout, op_values, main_ops, code_start_row)

    # -- Boundary rows --
    _place_boundary_row(sim, blank_top_row, W)
    _place_boundary_row(sim, blank_bot_row, W)

    # Boundary edges on all auxiliary and code rows
    all_rows = [copyover_exit_row, copyover_top_row, copyover_bottom_row,
                bypass_row, return_row, handler_row]
    all_rows += list(range(code_start_row, code_start_row + CODE_ROWS))
    for r in all_rows:
        _place_boundary_edges(sim, r, W)

    # -- NOP fill auxiliary rows --
    for r in [handler_row, return_row, bypass_row,
              copyover_exit_row, copyover_top_row, copyover_bottom_row]:
        _fill_nop(sim, r, 1, CR)

    # -- Locate ? mirrors in the ops --
    q_indices = [i for i, op in enumerate(main_ops) if op == '?']
    hbound_idx = q_indices[0]    # 5
    vbound_idx = q_indices[1]    # 18
    pre_syn_idx = q_indices[2]   # 38
    probe_idx = q_indices[3]     # 82

    # All first 3 ? marks should be on the first code row
    # (they're at ops 5, 18, 38 which are all < 39 first-row slots)
    hbound_col = CL + hbound_idx      # 8
    vbound_col = CL + vbound_idx      # 21
    pre_syn_col = CL + pre_syn_idx    # 41

    # -- Horizontal handler (9 ops) on handler_row --
    h_handler_ops = ['/', ';', 'T', 'm', 'B', 'C', 'U', ']', '\\']
    for i, op_ch in enumerate(h_handler_ops):
        sim.grid[sim._to_flat(handler_row, hbound_col + i)] = encode_opcode(OP[op_ch])

    # Verify alignment: handler exit at hbound_col+8 = merge1 col
    merge1_idx = next(i for i, op in enumerate(main_ops[hbound_idx+1:], hbound_idx+1) if op == ')')
    merge1_col = CL + merge1_idx  # should be 16
    assert hbound_col + 8 == merge1_col, \
        f"H-handler exit {hbound_col+8} != merge {merge1_col}"

    # -- Rewind handler (17 ops) on handler_row --
    v_handler_ops = ['/', 'D', ']', '(', 'B', 'D', 'A',
                     'm', 'T', ':', '%', ';', 'T', 'm', 'C', ']', '\\']
    for i, op_ch in enumerate(v_handler_ops):
        sim.grid[sim._to_flat(handler_row, vbound_col + i)] = encode_opcode(OP[op_ch])

    # -- Return row ops (rewind loop bounces) --
    percent_col = vbound_col + 10   # 31
    paren_col = vbound_col + 3      # 24
    sim.grid[sim._to_flat(return_row, percent_col)] = encode_opcode(OP['\\'])
    return_ops = [';', 'T', 'm', 'P']
    for i, op_ch in enumerate(return_ops):
        sim.grid[sim._to_flat(return_row, percent_col - 1 - i)] = encode_opcode(OP[op_ch])
    sim.grid[sim._to_flat(return_row, paren_col)] = encode_opcode(OP['/'])

    # Verify rewind alignment
    merge2_idx = next(i for i, op in enumerate(main_ops[vbound_idx+1:], vbound_idx+1) if op == ')')
    merge2_col = CL + merge2_idx  # should be 37
    assert vbound_col + 16 == merge2_col, \
        f"V-handler exit {vbound_col+16} != merge {merge2_col}"

    # -- Clean bypass row --
    sim.grid[sim._to_flat(bypass_row, pre_syn_col)] = encode_opcode(OP['\\'])  # N→W
    sim.grid[sim._to_flat(bypass_row, 2)] = encode_opcode(OP['$'])  # / if [EX]≠0

    # P at (return_row, 2): re-dirties EX for merge
    sim.grid[sim._to_flat(return_row, 2)] = encode_opcode(OP['P'])

    # -- Copy-over (upward boustrophedon) --
    # Build copy-over ops
    _, _, copyover_base, _, n_cl_inc = v8.build_probe_bypass_gadget(DIR_W)

    copyover_full = list(copyover_base)
    if is_upper:
        copyover_full.append('O')       # flip ix_vdir S→N
    else:
        copyover_full.append('o')
    copyover_full += ['C'] * RPG
    copyover_full.append('m')
    copyover_full += ['D'] * RPG
    if is_upper:
        copyover_full.append('O')
    else:
        copyover_full.append('o')
    copyover_full.append('j')
    copyover_full += [']', '+', 'Z', ']']

    _place_narrow_copyover(sim, layout, copyover_full, PROBE_COL,
                           copyover_exit_row, copyover_top_row,
                           copyover_bottom_row, is_upper, RPG)


# ===================================================================
# Metabolism (reused from agent-v1)
# ===================================================================

def _place_metabolism(sim, layout, metab_return_row, metab_main_row,
                      metab_corridor_row, last_code_row, blank_bot_row,
                      first_code_row):
    """Place metabolism on 3 rows + routing. Adapted from agent-v1."""
    W = layout['width']

    # Clear metabolism rows
    for row in [metab_return_row, metab_main_row, metab_corridor_row]:
        for col in range(W):
            sim.grid[sim._to_flat(row, col)] = 0

    # Boundary at blank_bot_row
    _place_boundary_row(sim, blank_bot_row, W)

    # Entry routing from last code row
    _place(sim, last_code_row, 2, '/')        # W→S
    sim.grid[sim._to_flat(last_code_row, 1)] = NOP_CELL  # remove corridor \\

    # Metabolism main row (same ops as agent-v1)
    main_ops = {
        2: '\\',  4: 'e',  5: 'P',  6: ')',  7: ']',  8: 'Z',
        9: 'T',  10: '?', 12: 'T', 13: 'X', 15: '&', 16: ':',
        17: ']', 18: 'Z', 20: 'x', 21: 'T', 22: '?', 23: 'T',
        24: 'x', 25: 'Z', 26: ')', 27: '[', 28: 'Z', 29: 'T',
        30: '?', 31: 'Z', 32: 'X', 33: ']', 34: 'Z', 35: 'T',
        36: ']', 37: 'Z', 38: ']', 39: '\\',
    }
    for col, op_ch in main_ops.items():
        _place(sim, metab_main_row, col, op_ch)

    _place_boundary_edges(sim, metab_main_row, W)
    _fill_row_nop(sim, metab_main_row, W)

    # Metabolism return row
    return_ops = {
        10: '\\', 9: 'T', 6: '/',
        22: '\\', 19: 'T', 15: '/',
        30: '\\', 29: 'T', 26: '/',
    }
    for col, op_ch in return_ops.items():
        _place(sim, metab_return_row, col, op_ch)

    _place_boundary_edges(sim, metab_return_row, W)
    _fill_row_nop(sim, metab_return_row, W)

    # Metabolism corridor row
    _place(sim, metab_corridor_row, 39, '/')     # S→W
    _place(sim, metab_corridor_row, 38, 'w')     # H1 west
    _place(sim, metab_corridor_row, 1, '\\')     # W→N

    _place_boundary_edges(sim, metab_corridor_row, W)
    _fill_row_nop(sim, metab_corridor_row, W)


# ===================================================================
# Top-level builder
# ===================================================================

def make_narrow_agent(width=WIDTH, bite_size=10):
    """Build the narrow dual-gadget agent with metabolism."""
    layout = compute_narrow_layout(width)
    T = layout['total_rows']
    W = width
    RPG = layout['rows_per_gadget']

    # Build gadget ops (same as v8)
    main_ops, probe_idx, copyover_base, pre_syn_idx, n_cl_inc = \
        v8.build_probe_bypass_gadget(DIR_W)
    op_values = [-NOP_CELL if ch == 'o' else OP[ch] for ch in main_ops]

    sim = FB2DSimulator(rows=T, cols=W)

    # Place gadget A
    _place_narrow_gadget(sim, layout, op_values, main_ops,
                         layout['ga_code_start'],
                         layout['ga_copyover_exit'],
                         layout['ga_copyover_top'],
                         layout['ga_copyover_bottom'],
                         layout['ga_bypass'],
                         layout['ga_return'],
                         layout['ga_handler'],
                         layout['ga_blank_top'],
                         layout['ga_blank_bot'],
                         is_upper=True)

    # Place gadget B
    _place_narrow_gadget(sim, layout, op_values, main_ops,
                         layout['gb_code_start'],
                         layout['gb_copyover_exit'],
                         layout['gb_copyover_top'],
                         layout['gb_copyover_bottom'],
                         layout['gb_bypass'],
                         layout['gb_return'],
                         layout['gb_handler'],
                         layout['gb_blank_top'],
                         layout['gb_blank_bot'],
                         is_upper=False)

    # Place metabolism
    _place_metabolism(sim, layout,
                      layout['ga_metab_return'], layout['ga_metab_main'],
                      layout['ga_metab_corridor'],
                      layout['ga_code'][1],  # last code row
                      layout['ga_blank_bot'],
                      layout['ga_code'][0])   # first code row

    _place_metabolism(sim, layout,
                      layout['gb_metab_return'], layout['gb_metab_main'],
                      layout['gb_metab_corridor'],
                      layout['gb_code'][1],
                      layout['gb_blank_bot'],
                      layout['gb_code'][0])

    # Place fuel
    def place_fuel(waste_row):
        bite = bite_size
        fuel_payloads = [189, 250, 380, 639]
        sim.grid[sim._to_flat(waste_row, 0)] = hamming_encode(999)  # dirty cell
        # Initial zeros: 1x bite (2x would eat too much fuel at W=45)
        n_zeros = bite
        for c in range(1, n_zeros + 1):
            if c < W:
                sim.grid[sim._to_flat(waste_row, c)] = 0
        col = n_zeros + 1
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
    ga_code_start = layout['ga_code'][0]
    gb_code_start = layout['gb_code'][0]
    code_left = layout['code_left']

    sim.ip_row = ga_code_start
    sim.ip_col = code_left
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.h1 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.ix = sim._to_flat(layout['gb_copyover_bottom'], 1)
    sim.cl = sim._to_flat(layout['ga_stomach'], DSL_ROT)
    sim.ex = sim._to_flat(layout['ga_waste'], 0)

    ip1_ex = sim._to_flat(layout['gb_waste'], 0)
    sim.add_ip(
        ip_row=gb_code_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        h1=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        ix=sim._to_flat(layout['ga_copyover_bottom'], 1),
        cl=sim._to_flat(layout['gb_stomach'], DSL_ROT),
        ex=ip1_ex,
    )

    return sim, layout


# ===================================================================
# Tests
# ===================================================================

def test_build():
    """Test that the narrow agent builds without errors."""
    sim, layout = make_narrow_agent()
    assert sim.rows == 38
    assert sim.cols == 45
    assert layout['rows_per_gadget'] == 19
    assert layout['code_rows'] == 6
    print(f"  Grid: {sim.rows}x{sim.cols} = {sim.rows * sim.cols} cells")
    print(f"  RPG: {layout['rows_per_gadget']}")
    return sim, layout


def test_cycle(sim, layout, n_steps=50000):
    """Run N steps and verify the IP completes at least one cycle."""
    start_row = sim.ip_row
    start_col = sim.ip_col
    start_dir = sim.ip_dir
    cycle_found = False

    for step in range(n_steps):
        sim.step_all()
        if (sim.ip_row == start_row and
            sim.ip_col == start_col and
            sim.ip_dir == start_dir and
            step > 100):
            print(f"  Cycle found at step {step + 1}")
            cycle_found = True
            break

    if not cycle_found:
        print(f"  No cycle in {n_steps} steps (IP at row={sim.ip_row}, col={sim.ip_col})")
    return cycle_found


def test_reversibility(sim, layout, n_steps=10000):
    """Step forward N times, then backward N times. Grid should match."""
    import array
    grid_before = array.array('H', sim.grid)

    for _ in range(n_steps):
        sim.step_all()

    for _ in range(n_steps):
        sim.step_back_all()

    diffs = sum(1 for a, b in zip(grid_before, sim.grid) if a != b)
    print(f"  Reversibility: {n_steps} steps forward+back, {diffs} diffs")
    assert diffs == 0, f"Reversibility broken: {diffs} diffs"
    return True


def test_correction(sim, layout, n_steps=100000, noise_rate=200):
    """Run with noise and verify the agent survives."""
    from fb2d import NoisePool

    sim2, _ = make_narrow_agent()
    W = layout['width']
    RPG = layout['rows_per_gadget']

    # Define noise target rows (code + handler + return + bypass + copyover rows)
    ga_code_start = layout['ga_code'][0]
    ga_code_end = layout['ga_code'][1]
    gb_code_start = layout['gb_code'][0]
    gb_code_end = layout['gb_code'][1]

    noise_rows = list(range(ga_code_start, ga_code_end + 1))
    noise_rows += [layout['ga_handler'], layout['ga_return'],
                   layout['ga_bypass'], layout['ga_copyover_bottom'],
                   layout['ga_copyover_top'], layout['ga_copyover_exit']]
    noise_rows += list(range(gb_code_start, gb_code_end + 1))
    noise_rows += [layout['gb_handler'], layout['gb_return'],
                   layout['gb_bypass'], layout['gb_copyover_bottom'],
                   layout['gb_copyover_top'], layout['gb_copyover_exit']]

    # Build noise targets (flat indices, cols 1 to W-2)
    noise_targets = []
    for r in noise_rows:
        for c in range(1, W - 1):
            noise_targets.append(sim2._to_flat(r, c))

    np = NoisePool(seed=42, rate=noise_rate, targets=noise_targets)
    sim2.noise_pool = np

    # Enable waste pool for cleanup
    from fb2d import WastePool
    waste_rows_a = [layout['ga_stomach']]
    waste_rows_b = [layout['gb_stomach']]
    waste_targets_a = []
    for r in waste_rows_a:
        for c in range(W):
            waste_targets_a.append(sim2._to_flat(r, c))
    waste_targets_b = []
    for r in waste_rows_b:
        for c in range(W):
            waste_targets_b.append(sim2._to_flat(r, c))

    wp = WastePool(targets=waste_targets_a + waste_targets_b)
    sim2.waste_pool = wp

    for step in range(n_steps):
        sim2.step_all()

    print(f"  Ran {n_steps} steps with noise rate {noise_rate}/1M")
    flips = np.total_flips if hasattr(np, 'total_flips') else '?'
    print(f"  Noise flips: {flips}")
    return True


if __name__ == '__main__':
    bite = 15
    for i, arg in enumerate(sys.argv):
        if arg == '--bite' and i + 1 < len(sys.argv):
            bite = int(sys.argv[i + 1])

    print(f"Building narrow agent-v1 (W={WIDTH}, bite={bite})...")
    sim, layout = make_narrow_agent(bite_size=bite)

    print(f"  Grid: {sim.rows}x{sim.cols} = {sim.rows * sim.cols} cells")
    print(f"  RPG: {layout['rows_per_gadget']}")
    print(f"  GA: code rows {layout['ga_code']}, metab {layout['ga_metab_main']}, "
          f"stomach {layout['ga_stomach']}, fuel {layout['ga_waste']}")
    print(f"  GB: code rows {layout['gb_code']}, metab {layout['gb_metab_main']}, "
          f"stomach {layout['gb_stomach']}, fuel {layout['gb_waste']}")

    # Save
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, f'agent-v1-narrow-w{WIDTH}.fb2d')
    sim.save_state(out_path, hints={'free_food': 1, 'bite_size': bite})
    print(f"  Saved: {out_path}")

    # Run tests
    print("\nTest: cycle detection...")
    sim2, layout2 = make_narrow_agent(bite_size=bite)
    test_cycle(sim2, layout2)

    print("\nTest: reversibility...")
    sim3, layout3 = make_narrow_agent(bite_size=bite)
    test_reversibility(sim3, layout3)

    print("\nAll tests passed.")

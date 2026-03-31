#!/usr/bin/env python3
"""
immunity-gadgets-v7-syndrome-inspect.py -- Copy-over for 2-bit errors.

Extends v5's low-waste architecture with:

1. PRE-SYNDROME FILTER (I opcode):
   Before copy-in (m), the I opcode tests syndrome([IX]) without copying
   the full codeword.  Clean cells (syndrome=0) bypass EVERYTHING -- no
   copy-in, Phase A/B, or correction needed.  This is much shorter than
   v5's bypass which still required copy-in + Phase A+B + probe.

2. COPY-OVER FOR 2-BIT ERRORS:
   When the probe fires (p_all=0, syndrome!=0), the cell has a 2-bit
   error that SECDED cannot correct.  The copy-over row does Phase A/B
   undo + IX round-trip to the gadget's OWN corresponding cell, copies
   it via m, computes error mask, and writes it back via j.

ARCHITECTURE (per gadget, R+8 rows — one extra vs v5):
  Row 0:        BOUNDARY ROW (0xFFFF; top boundary for IX)
  Row 1:        COPY-OVER ROW (replaces v5's bypass row for 2-bit errors)
  Row 2:        CLEAN BYPASS ROW (pre-syndrome clean cell fast path)
  Row 3:        RETURN ROW (NOP-filled, IX scans; rewind loop path)
  Row 4:        HANDLER ROW (boundary handlers going East, NOP-filled)
  Rows 5..R+4:  CODE ROWS (boustrophedon)
  Row R+5:      BOUNDARY ROW (0xFFFF; bottom boundary for IX)
  Row R+6:      STOMACH ROW (working area: H0, H1, CL fixed here)
  Row R+7:      WASTE ROW (EX roams, eats zeros, excretes waste)

COL 2 ROUTING (3 flows merge via EX discrimination):
  Copy-over row (1): / at col 2 (unconditional W→S exit).
    Copy-over ends with ]+Z] so EX is CLEAN at exit.
  Clean bypass row (2): $ at col 2 (/ if [EX]≠0).
    Pre-syndrome bypass: arrives West, EX dirty → $ fires W→S → South. ✓
    Copy-over: arrives South, EX clean → $ NOP → continues South. ✓
  Return row (3): P at col 2 (re-dirties EX for copy-over path).
  Code row (5): ( at col 2 (merge gate, fires S→E on dirty EX).

NOP_CELL: payload 1017 (0x7E8E), same as v5.  The I opcode replaces
the unused M opcode (54, payload 54), so payload 1017 stays as the
64th codeword of the [11,6,4] code with full d_min=4 NOP filler
protection (0/11 1-bit bad, 0/55 2-bit bad).

Run tests:  python3 programs/immunity-gadgets-v7-syndrome-inspect.py [--width W]
"""

import sys
import os
import math
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, DIR_S, DIR_W, encode_opcode, OPCODE_PAYLOADS,
                  SYNDROME_XOR_MASK)

# Import from dual-gadget-demo.py
_dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dual-gadget-demo.py')
_spec = importlib.util.spec_from_file_location('dgd', _dgd_path)
dgd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dgd)

GadgetBuilder = dgd.GadgetBuilder
build_h2_correction_gadget = dgd.build_h2_correction_gadget
place_boustrophedon = dgd.place_boustrophedon
DSL_EV = dgd.DSL_EV
DSL_PA = dgd.DSL_PA
DSL_CWL = dgd.DSL_CWL
DSL_S0 = dgd.DSL_S0
DSL_ROT = dgd.DSL_ROT
DSL_SLOT_WIDTH = dgd.DSL_SLOT_WIDTH
SYNDROME_POSITIONS = dgd.SYNDROME_POSITIONS

from hamming import encode, inject_error

OP = OPCODES

NOP_CELL = hamming_encode(1017)  # 0x7E8E, data-bit dist 8 from zero (same as v5)
BOUNDARY_CELL = 0xFFFF


# ===================================================================
# Probe-bypass gadget builder (v7)
# ===================================================================

def build_probe_bypass_gadget(last_row_dir):
    """Build the probe-bypass correction gadget (v7).

    Order:
      1. Preamble (P)
      2. IX advance + horizontal boundary test (A m T : ? ; T m o o o o)
      3. Handler #1 merge ()) + P + vertical test + handler #2 merge ()) + P
      4. Pre-syndrome filter (I T ? T I) -- NEW
      5. Copy-in (m) + Probe (Phase A + B + T + ?)
      6. Correction (Phase A' + C + D + C' + uncompute + writeback + F + G)

    Returns: (main_ops, probe_branch_idx, copyover_base_ops,
              pre_syndrome_idx, n_cl_increments)
    """
    gb = GadgetBuilder()

    # -- 1. Preamble --
    gb.emit('P')

    # -- 2. IX advance + horizontal boundary test --
    gb.emit('A')
    gb.emit('m')
    gb.emit('T')
    gb.emit(':')
    gb.emit('?')     # horizontal boundary: / if CL==0
    gb.emit(';')
    gb.emit('T')
    gb.emit('m')
    for _ in range(4):
        gb.emit('o')

    # -- 3. Handler #1 merge + vertical test --
    gb.emit(')')
    gb.emit('P')

    gb.emit('m')
    gb.emit('T')
    gb.emit(':')
    gb.emit('?')     # vertical boundary: / if CL==0
    gb.emit(';')
    gb.emit('T')
    gb.emit('m')
    for _ in range(12):
        gb.emit('o')
    gb.emit(')')
    gb.emit('P')

    # -- 4. Pre-syndrome filter (v7) --
    pre_syndrome_pos = gb.pos()
    gb.emit('I')     # [H0=CWL] ^= syndrome_4bit([IX])
    gb.emit('T')     # swap [CL=ROT] <-> [H0=CWL]
    gb.emit('?')     # PRE-SYNDROME: / if payload(CL)==0 -> clean bypass
    gb.emit('T')     # undo swap (syndrome!=0 only)
    gb.emit('I')     # undo XOR (syndrome!=0 only)

    # -- 5. Copy-in + Probe --
    copy_in_pos = gb.pos()
    gb.emit('m')

    # Phase A: overall parity
    gb.move_h0_col(DSL_PA)
    gb.xor_accumulate_bits(list(range(16)))

    # Phase B: z-extract
    gb.move_h0_col(DSL_EV)
    gb.move_h1_col(DSL_PA)
    gb.emit('z')
    gb.move_h1_col(DSL_CWL)

    gb.emit_n('l', 3)
    gb.emit('T')
    gb.emit('?')     # PROBE: / if payload(CL)==0 -> copy-over (2-bit) or bypass (clean)
    probe_branch_idx = gb.pos() - 1

    # -- If dirty (1-bit): continue correction --
    gb.emit(']')
    gb.emit('T')
    gb.emit_n('r', 3)

    # Phase A'
    gb.move_h0_col(DSL_PA)
    gb.xor_accumulate_bits(list(range(15, -1, -1)))

    # Phase C
    gb.move_h0_col(DSL_S0)
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])
    gb.move_h0_col(dgd.DSL_S1)
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])
    gb.move_h0_col(dgd.DSL_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])
    gb.move_h0_col(dgd.DSL_S3)
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    # Phase D
    gb.move_h0_col(DSL_EV)
    gb.move_h1_col(dgd.DSL_SCR)
    gb.move_cl_col(DSL_S0)
    DSL_SI = dgd.DSL_SI
    for i in range(4):
        if i > 0:
            gb.move_cl_col(DSL_SI[i])
        shift = 1 << i
        gb.emit_n('l', shift)
        gb.emit('f')
        gb.emit_n('r', shift)
        gb.emit('f')

    # Phase C'
    gb.move_h0_col(dgd.DSL_S3)
    gb.move_h1_col(DSL_CWL)
    gb.move_cl_col(DSL_ROT)
    gb.cl_payload = 8
    gb.xor_accumulate_bits([8, 9, 10, 11, 12, 13, 14, 15])
    gb.move_h0_col(dgd.DSL_S2)
    gb.xor_accumulate_bits([15, 14, 13, 12, 7, 6, 5, 4])
    gb.move_h0_col(dgd.DSL_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7, 10, 11, 14, 15])
    gb.move_h0_col(DSL_S0)
    gb.xor_accumulate_bits([15, 13, 11, 9, 7, 5, 3, 1])
    gb.set_cl_payload(0)

    # Uncompute + writeback
    gb.move_h0_col(DSL_CWL)
    gb.emit('m')
    gb.move_h0_col(DSL_EV)
    gb.emit('j')

    # Phase F
    gb.move_h1_col(DSL_PA)
    gb.emit('z')
    gb.emit('x')

    # Epilogue
    gb.move_h0_col(DSL_CWL)
    gb.move_h1_col(DSL_CWL)

    # Phase G
    gb.move_h0_col(DSL_EV)
    gb.emit('+')
    gb.emit('Z')
    gb.emit(']')
    gb.move_h0_col(DSL_PA)
    gb.emit('+')
    gb.emit('Z')
    gb.emit(']')
    gb.move_h0_col(DSL_CWL)
    gb.move_h1_col(DSL_CWL)

    main_ops = gb.ops

    # -- Copy-over base ops --
    n_cl_increments = sum(1 for op in gb.ops[copy_in_pos:probe_branch_idx] if op == ':')

    copyover_base = ['T', 'r', 'r', 'r']
    copyover_base += [';'] * n_cl_increments
    copyover_base += ['w', 'z', 'e', 'E', 'E']

    return main_ops, probe_branch_idx, copyover_base, pre_syndrome_pos, n_cl_increments


# ===================================================================
# Layout
# ===================================================================

def compute_probe_layout(width):
    """Compute grid layout (v7, R+8 rows per gadget)."""
    code_left = 3
    code_right = width - 2

    first_row_slots = code_right - code_left
    inner_row_slots = code_right - code_left - 1

    last_dir = _last_row_direction_probe(999, code_left, code_right)
    main_ops, probe_idx, copyover_base, pre_syn_idx, n_cl_inc = \
        build_probe_bypass_gadget(last_dir)
    n_ops = len(main_ops)

    last_dir = _last_row_direction_probe(n_ops, code_left, code_right)
    main_ops2, probe_idx2, copyover_base2, pre_syn_idx2, n_cl_inc2 = \
        build_probe_bypass_gadget(last_dir)
    if len(main_ops2) != n_ops:
        n_ops = len(main_ops2)
        main_ops = main_ops2
        probe_idx = probe_idx2
        copyover_base = copyover_base2
        pre_syn_idx = pre_syn_idx2
        n_cl_inc = n_cl_inc2
        last_dir = _last_row_direction_probe(n_ops, code_left, code_right)

    if n_ops <= first_row_slots:
        code_rows = 1
    else:
        remaining = n_ops - first_row_slots
        code_rows = 1 + math.ceil(remaining / inner_row_slots)

    # R+8: boundary + copyover + clean_bypass + return + handler + R code + boundary + stomach + waste
    # One extra row vs v5 for clean bypass routing (avoids rewind return row conflicts).
    rows_per_gadget = code_rows + 8
    total_rows = 2 * rows_per_gadget

    # Gadget A
    ga_blank_top = 0
    ga_copyover = 1
    ga_clean_bypass = 2
    ga_return = 3
    ga_handler = 4
    ga_code_start = 5
    ga_code_end = 4 + code_rows
    ga_blank_bot = 5 + code_rows
    ga_stomach = 6 + code_rows
    ga_waste = 7 + code_rows

    # Gadget B
    gb_blank_top = rows_per_gadget
    gb_copyover = rows_per_gadget + 1
    gb_clean_bypass = rows_per_gadget + 2
    gb_return = rows_per_gadget + 3
    gb_handler = rows_per_gadget + 4
    gb_code_start = rows_per_gadget + 5
    gb_code_end = rows_per_gadget + 4 + code_rows
    gb_blank_bot = rows_per_gadget + 5 + code_rows
    gb_stomach = rows_per_gadget + 6 + code_rows
    gb_waste = rows_per_gadget + 7 + code_rows

    layout = {
        'width': width,
        'n_ops': n_ops,
        'probe_branch_idx': probe_idx,
        'copyover_base': copyover_base,
        'pre_syndrome_idx': pre_syn_idx,
        'n_cl_increments': n_cl_inc,
        'code_rows': code_rows,
        'rows_per_gadget': rows_per_gadget,
        'total_rows': total_rows,
        'last_row_dir': last_dir,
        'ga_blank_top': ga_blank_top, 'ga_copyover': ga_copyover,
        'ga_clean_bypass': ga_clean_bypass,
        'ga_return': ga_return, 'ga_handler': ga_handler,
        'ga_code': (ga_code_start, ga_code_end),
        'ga_blank_bot': ga_blank_bot,
        'ga_stomach': ga_stomach, 'ga_waste': ga_waste,
        'gb_blank_top': gb_blank_top, 'gb_copyover': gb_copyover,
        'gb_clean_bypass': gb_clean_bypass,
        'gb_return': gb_return, 'gb_handler': gb_handler,
        'gb_code': (gb_code_start, gb_code_end),
        'gb_blank_bot': gb_blank_bot,
        'gb_stomach': gb_stomach, 'gb_waste': gb_waste,
        'code_left': code_left, 'code_right': code_right,
        'first_row_slots': first_row_slots,
        'inner_row_slots': inner_row_slots,
    }
    return layout


def _last_row_direction_probe(n_ops, code_left, code_right):
    first_row_slots = code_right - code_left
    inner_row_slots = code_right - code_left - 1
    if n_ops <= first_row_slots:
        return DIR_E
    remaining = n_ops - first_row_slots
    extra_rows = math.ceil(remaining / inner_row_slots)
    total_row_count = 1 + extra_rows
    return DIR_W if total_row_count % 2 == 0 else DIR_E


def _boustrophedon_op_position(op_idx, code_left, code_right, start_row):
    first_slots = code_right - code_left
    inner_slots = code_right - code_left - 1
    if op_idx < first_slots:
        return start_row, code_left + op_idx, DIR_E
    remaining = op_idx - first_slots
    row_offset = 1 + remaining // inner_slots
    pos_in_row = remaining % inner_slots
    row = start_row + row_offset
    row_count = row_offset + 1
    if row_count % 2 == 0:
        col = code_right - 1 - pos_in_row
        return row, col, DIR_W
    else:
        col = code_left + 1 + pos_in_row
        return row, col, DIR_E


def make_probe_bypass_ouroboros(width=195, errors=None):
    """Build the dual ouroboros grid (v7)."""
    layout = compute_probe_layout(width)
    last_dir = layout['last_row_dir']
    main_ops, probe_idx, copyover_base, pre_syn_idx, n_cl_inc = \
        build_probe_bypass_gadget(last_dir)
    op_values = [-NOP_CELL if ch == 'o' else OP[ch] for ch in main_ops]

    T = layout['total_rows']
    W = width
    sim = FB2DSimulator(rows=T, cols=W)

    ga_code_start = layout['ga_code'][0]
    gb_code_start = layout['gb_code'][0]

    _place_probe_gadget(sim, layout, op_values, main_ops,
                        ga_code_start, layout['ga_copyover'],
                        layout['ga_clean_bypass'],
                        layout['ga_return'], layout['ga_handler'],
                        probe_idx, copyover_base, pre_syn_idx,
                        is_upper=True)
    _place_probe_gadget(sim, layout, op_values, main_ops,
                        gb_code_start, layout['gb_copyover'],
                        layout['gb_clean_bypass'],
                        layout['gb_return'], layout['gb_handler'],
                        probe_idx, copyover_base, pre_syn_idx,
                        is_upper=False)

    if errors:
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    code_left = layout['code_left']

    # IP0: runs gadget A, IX scans gadget B
    sim.ip_row = ga_code_start
    sim.ip_col = code_left
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.h1 = sim._to_flat(layout['ga_stomach'], DSL_CWL)
    sim.ix = sim._to_flat(layout['gb_copyover'], 1)
    sim.cl = sim._to_flat(layout['ga_stomach'], DSL_ROT)
    sim.ex = sim._to_flat(layout['ga_waste'], 0)
    sim.grid[sim.ex] = hamming_encode(1)

    # IP1: runs gadget B, IX scans gadget A
    ip1_ex = sim._to_flat(layout['gb_waste'], 0)
    sim.grid[ip1_ex] = hamming_encode(1)
    sim.add_ip(
        ip_row=gb_code_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        h1=sim._to_flat(layout['gb_stomach'], DSL_CWL),
        ix=sim._to_flat(layout['ga_copyover'], 1),
        cl=sim._to_flat(layout['gb_stomach'], DSL_ROT),
        ex=ip1_ex,
    )

    cycle_length = _compute_cycle_length(sim, layout)
    return sim, layout, cycle_length


def _place_probe_gadget(sim, layout, op_values, main_ops,
                        code_start_row, copyover_row, clean_bypass_row,
                        return_row, handler_row,
                        probe_branch_idx, copyover_base,
                        pre_syndrome_idx, is_upper=True):
    """Place one gadget (v7, R+8 layout)."""
    W = layout['width']
    R = layout['code_rows']
    RPG = layout['rows_per_gadget']
    code_left = layout['code_left']
    code_right = layout['code_right']
    last_dir = layout['last_row_dir']

    assert last_dir == DIR_W, f"Need west-going last row, got {last_dir}"

    first_code_row = code_start_row
    last_code_row = code_start_row + R - 1

    # -- Boustrophedon code --
    place_boustrophedon(sim, op_values, code_left, code_right,
                        start_row=code_start_row)
    _fill_row_with_nop(sim, layout, last_code_row)

    # -- Corridor --
    sim.grid[sim._to_flat(last_code_row, 1)] = encode_opcode(OP['\\'])
    sim.grid[sim._to_flat(first_code_row, 1)] = encode_opcode(OP['/'])

    # -- Merge gate --
    sim.grid[sim._to_flat(first_code_row, 2)] = encode_opcode(OP['('])

    # NOP fill cols 1-2 on non-first code rows
    for row in range(first_code_row + 1, last_code_row + 1):
        for col in [1, 2]:
            flat = sim._to_flat(row, col)
            if sim.grid[flat] == 0:
                sim.grid[flat] = NOP_CELL

    # -- NOP fill auxiliary rows (cols 1..code_right) --
    for row in [handler_row, return_row, clean_bypass_row, copyover_row]:
        for col in range(1, code_right + 1):
            sim.grid[sim._to_flat(row, col)] = NOP_CELL

    # -- Boundary markers --
    blank_top = copyover_row - 1
    blank_bot = code_start_row + R
    for col in range(W):
        sim.grid[sim._to_flat(blank_top, col)] = BOUNDARY_CELL
        sim.grid[sim._to_flat(blank_bot, col)] = BOUNDARY_CELL

    for row in [copyover_row, clean_bypass_row, return_row, handler_row] + \
               list(range(first_code_row, last_code_row + 1)):
        sim.grid[sim._to_flat(row, 0)] = BOUNDARY_CELL
        sim.grid[sim._to_flat(row, W - 1)] = BOUNDARY_CELL

    # -- Locate ? mirrors --
    question_indices = [i for i, op in enumerate(main_ops) if op == '?']
    assert len(question_indices) >= 4, f"Expected 4 ?, found {len(question_indices)}"

    hbound_idx = question_indices[0]
    vbound_idx = question_indices[1]
    pre_syn_idx = question_indices[2]
    probe_idx = question_indices[3]

    hbound_row, hbound_col, _ = _boustrophedon_op_position(
        hbound_idx, code_left, code_right, code_start_row)
    vbound_row, vbound_col, _ = _boustrophedon_op_position(
        vbound_idx, code_left, code_right, code_start_row)
    pre_syn_row, pre_syn_col, _ = _boustrophedon_op_position(
        pre_syn_idx, code_left, code_right, code_start_row)
    probe_row, probe_col, _ = _boustrophedon_op_position(
        probe_idx, code_left, code_right, code_start_row)

    for name, r in [('H-bound', hbound_row), ('V-bound', vbound_row),
                    ('Pre-syn', pre_syn_row), ('Probe', probe_row)]:
        assert r == first_code_row, f"{name} ? at row {r}, expected {first_code_row}"

    # -- Horizontal handler (9 ops) --
    h_handler_ops = ['/', ';', 'T', 'm', 'B', 'C', 'U', ']', '\\']
    for i, op_ch in enumerate(h_handler_ops):
        sim.grid[sim._to_flat(handler_row, hbound_col + i)] = encode_opcode(OP[op_ch])

    # Verify alignment
    h_exit_col = hbound_col + 8
    merge1_idx = next(i for i, op in enumerate(main_ops[hbound_idx+1:], hbound_idx+1) if op == ')')
    _, merge1_col, _ = _boustrophedon_op_position(merge1_idx, code_left, code_right, code_start_row)
    assert h_exit_col == merge1_col

    # -- Rewind handler (17 ops) --
    v_handler_ops = ['/', 'D', ']', '(', 'B', 'D', 'A',
                     'm', 'T', ':', '%', ';', 'T', 'm', 'C', ']', '\\']
    for i, op_ch in enumerate(v_handler_ops):
        sim.grid[sim._to_flat(handler_row, vbound_col + i)] = encode_opcode(OP[op_ch])

    # -- Return row ops --
    percent_col = vbound_col + 10
    paren_col = vbound_col + 3
    sim.grid[sim._to_flat(return_row, percent_col)] = encode_opcode(OP['\\'])
    return_ops = [';', 'T', 'm', 'P']
    for i, op_ch in enumerate(return_ops):
        sim.grid[sim._to_flat(return_row, percent_col - 1 - i)] = encode_opcode(OP[op_ch])
    sim.grid[sim._to_flat(return_row, paren_col)] = encode_opcode(OP['/'])

    # Verify rewind alignment
    v_exit_col = vbound_col + 16
    merge2_idx = next(i for i, op in enumerate(main_ops[vbound_idx+1:], vbound_idx+1) if op == ')')
    _, merge2_col, _ = _boustrophedon_op_position(merge2_idx, code_left, code_right, code_start_row)
    assert v_exit_col == merge2_col

    # -- Clean bypass row (pre-syndrome fast path) --
    # Pre-syndrome ? fires E→N. IP goes N through handler (NOP), return (NOP),
    # to clean_bypass_row where \ catches N→W. West through NOPs to col 2.
    # $ (/ if [EX]≠0) at col 2:
    #   Clean bypass arrives W with dirty EX → $ fires W→S → South. ✓
    #   Copy-over arrives S with clean EX → $ NOP → continues South. ✓
    sim.grid[sim._to_flat(clean_bypass_row, pre_syn_col)] = encode_opcode(OP['\\'])
    sim.grid[sim._to_flat(clean_bypass_row, 2)] = encode_opcode(OP['$'])

    # P at (return_row, 2): re-dirties EX for copy-over path before ( merge.
    # Clean bypass (dirty EX) passes through harmlessly (P increments further).
    # Return row col 2 is safe — rewind ops are at cols 24-31, far from col 2.
    sim.grid[sim._to_flat(return_row, 2)] = encode_opcode(OP['P'])

    # -- Copy-over row --
    # Entry: \ at probe_col (N→W from probe ? fire).
    sim.grid[sim._to_flat(copyover_row, probe_col)] = encode_opcode(OP['\\'])

    # Build full copy-over ops: Phase A/B undo + IX trip + writeback + waste
    copyover_full = list(copyover_base)
    if is_upper:
        copyover_full.append('O')       # flip ix_vdir S→N
    else:
        copyover_full.append('o')       # NOP padding (ix_vdir already correct)
    copyover_full += ['C'] * RPG        # advance IX to own cell
    copyover_full.append('m')           # [CWL] ^= [IX_own] = error_mask
    copyover_full += ['D'] * RPG        # retreat IX back
    if is_upper:
        copyover_full.append('O')       # flip back N→S
    else:
        copyover_full.append('o')
    copyover_full.append('j')           # [IX_partner] ^= error_mask → corrected!
    # Deposit CWL waste and leave EX CLEAN for $ routing:
    # ] advances EX to clean cell, + ensures nonzero, Z swaps CWL↔[EX],
    # ] advances EX to next clean cell.
    copyover_full += [']', '+', 'Z', ']']

    # Place copy-over ops going West from probe_col-1
    for i, op_ch in enumerate(copyover_full):
        col = probe_col - 1 - i
        if op_ch == 'o':
            sim.grid[sim._to_flat(copyover_row, col)] = NOP_CELL
        else:
            sim.grid[sim._to_flat(copyover_row, col)] = encode_opcode(OP[op_ch])

    # Exit: / at col 2 (W→S). Copy-over IP with clean EX passes through
    # $ (return row) as NOP, hits P (handler row) to re-dirty, then ( merge.
    sim.grid[sim._to_flat(copyover_row, 2)] = encode_opcode(OP['/'])

    last_copyover_col = probe_col - 1 - (len(copyover_full) - 1)
    assert last_copyover_col >= 3, \
        f"Copy-over extends past col 2 (last op at col {last_copyover_col})"


def _fill_row_with_nop(sim, layout, row):
    code_left = layout['code_left']
    code_right = layout['code_right']
    for col in range(code_left, code_right + 1):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def _compute_cycle_length(sim, layout):
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
        if opcode == 1:
            d = SLASH[d]
        elif opcode == 2:
            d = BACKSLASH[d]
        r = (r + dr[d]) % sim.rows
        c = (c + dc[d]) % sim.cols
        if r == start_row and c == code_left and d == DIR_E:
            break
        if steps > 50000:
            raise RuntimeError(f"Cycle exceeded 50000 at ({r},{c}) dir={d}")
    return steps


# ===================================================================
# Tests
# ===================================================================

def _cheat_clear_waste(sim, layout):
    W = layout['width']
    half = W // 2
    threshold_high = int(W * 0.9)
    threshold_low = int(W * 0.1)
    waste_rows = [
        (layout['ga_waste'], sim.ips[0]['ex']),
        (layout['gb_waste'], sim.ips[1]['ex']),
    ]
    for waste_row, ex_flat in waste_rows:
        base = waste_row * W
        ex_col = ex_flat - base
        if ex_col < 0 or ex_col >= W:
            continue
        if ex_col >= threshold_high:
            for c in range(half):
                flat = base + c
                if flat != ex_flat:
                    sim.grid[flat] = 0
        elif ex_col <= threshold_low:
            for c in range(half, W):
                flat = base + c
                if flat != ex_flat:
                    sim.grid[flat] = 0


def test_layout_info(width=195):
    layout = compute_probe_layout(width)
    main_ops, probe_idx, copyover_base, pre_syn_idx, n_cl_inc = \
        build_probe_bypass_gadget(layout['last_row_dir'])
    print(f"=== v7 Layout (W={width}) ===")
    print(f"    Main gadget: {layout['n_ops']} ops")
    print(f"    Copy-over base: {len(copyover_base)} ops")
    print(f"    Code rows: {layout['code_rows']}, RPG: {layout['rows_per_gadget']}")
    print(f"    Last row: {'West' if layout['last_row_dir'] == DIR_W else 'East'}")
    print(f"    Grid: {layout['total_rows']}x{width}")

    code_left = layout['code_left']
    code_right = layout['code_right']
    question_indices = [i for i, op in enumerate(main_ops) if op == '?']
    pre_syn_col_val = code_left + question_indices[2]
    probe_col_val = code_left + question_indices[3]
    print(f"    Pre-syndrome ? at col {pre_syn_col_val}")
    print(f"    Probe ? at col {probe_col_val}")

    RPG = layout['rows_per_gadget']
    copyover_full_len = len(copyover_base) + 1 + RPG + 1 + RPG + 1 + 1 + 1 + 1
    last_col = probe_col_val - 1 - (copyover_full_len - 1)
    print(f"    Copy-over: {copyover_full_len} ops, cols {probe_col_val-1}..{last_col}")
    ok = last_col >= 3
    if not ok:
        print(f"    FAIL: copy-over extends past col 2")
    return ok


def test_build_gadget(width=195):
    print(f"=== Build gadget v7 (W={width}) ===")
    try:
        sim, layout, cycle_length = make_probe_bypass_ouroboros(width)
        print(f"    Grid: {sim.rows}x{sim.cols}, cycle: {cycle_length}")
        print(f"    PASS")
        return True
    except Exception as e:
        print(f"    FAIL: {e}")
        import traceback; traceback.print_exc()
        return False


def test_cycle_length(width=195):
    print(f"=== Cycle length v7 (W={width}) ===")
    sim, layout, cycle_length_dirty = make_probe_bypass_ouroboros(width)
    print(f"    Dirty-path cycle: {cycle_length_dirty}")
    start_row, start_col, start_dir = sim.ip_row, sim.ip_col, sim.ip_dir
    max_steps = cycle_length_dirty + 500
    actual_steps = 0
    for _ in range(max_steps):
        sim.step_all()
        _cheat_clear_waste(sim, layout)
        actual_steps += 1
        if (sim.ip_row == start_row and sim.ip_col == start_col
                and sim.ip_dir == start_dir):
            break
    ok = (sim.ip_row == start_row and sim.ip_col == start_col
          and sim.ip_dir == start_dir)
    print(f"    Clean-path actual: {actual_steps} steps {'ok' if ok else 'FAIL'}")
    if ok and cycle_length_dirty:
        pct = (cycle_length_dirty - actual_steps) / cycle_length_dirty * 100
        print(f"    Savings: {cycle_length_dirty - actual_steps} steps ({pct:.0f}%)")
    return ok


def test_no_error(width=195):
    print(f"=== No error v7 (W={width}) ===")
    sim, layout, cycle_length = make_probe_bypass_ouroboros(width, errors=[])
    grid_before = sim.grid[:]
    n_cycles = 10
    for _ in range(n_cycles * cycle_length):
        sim.step_all()
        _cheat_clear_waste(sim, layout)

    all_ok = True
    check_rows = []
    for gadget in [('GA', layout['ga_code'], layout['ga_handler'],
                    layout['ga_return'], layout['ga_clean_bypass'],
                    layout['ga_copyover']),
                   ('GB', layout['gb_code'], layout['gb_handler'],
                    layout['gb_return'], layout['gb_clean_bypass'],
                    layout['gb_copyover'])]:
        name, (cs, ce), hr, rr, cbr, cor = gadget
        check_rows += [cor, cbr, rr, hr] + list(range(cs, ce + 1))

    for row in check_rows:
        base = row * layout['width']
        for col in range(layout['width']):
            flat = base + col
            if sim.grid[flat] != grid_before[flat]:
                print(f"    MODIFIED ({row},{col}): 0x{grid_before[flat]:04x} -> 0x{sim.grid[flat]:04x}")
                all_ok = False
    print(f"    {'PASS' if all_ok else 'FAIL'}")
    return all_ok


def test_single_error(width=195):
    print(f"=== Single error v7 (W={width}) ===")
    layout = compute_probe_layout(width)
    gb_start = layout['gb_code'][0]
    code_left = layout['code_left']
    row, col, bit = gb_start, code_left, 3
    sim, layout, cycle_length = make_probe_bypass_ouroboros(width, [(row, col, bit)])
    flat = sim._to_flat(row, col)
    expected = inject_error(sim.grid[flat], bit)
    R = layout['code_rows']
    scan_cols = layout['code_right'] - layout['code_left'] + 1
    n_steps = (R + 3) * scan_cols * cycle_length
    print(f"    Running {n_steps} steps...")
    for _ in range(n_steps):
        sim.step_all()
        _cheat_clear_waste(sim, layout)
    ok = sim.grid[flat] == expected
    print(f"    0x{sim.grid[flat]:04x} expected 0x{expected:04x} {'ok' if ok else 'FAIL'}")
    return ok


def test_copyover(width=195):
    print(f"=== Copy-over (2-bit) v7 (W={width}) ===")
    layout = compute_probe_layout(width)
    gb_start = layout['gb_code'][0]
    code_left = layout['code_left']
    row, col = gb_start, code_left
    sim, layout, cycle_length = make_probe_bypass_ouroboros(
        width, [(row, col, 3), (row, col, 5)])
    flat = sim._to_flat(row, col)
    expected = inject_error(inject_error(sim.grid[flat], 5), 3)
    R = layout['code_rows']
    scan_cols = layout['code_right'] - layout['code_left'] + 1
    n_steps = (R + 3) * scan_cols * cycle_length
    print(f"    Running {n_steps} steps...")
    for _ in range(n_steps):
        sim.step_all()
        _cheat_clear_waste(sim, layout)
    ok = sim.grid[flat] == expected
    print(f"    0x{sim.grid[flat]:04x} expected 0x{expected:04x} {'ok' if ok else 'FAIL'}")
    return ok


if __name__ == '__main__':
    width = 195  # v7: 379 ops, W=195 gives 2 code rows (West), RPG=10
    for i, arg in enumerate(sys.argv):
        if arg == '--width' and i + 1 < len(sys.argv):
            width = int(sys.argv[i + 1])

    all_ok = True
    all_ok &= test_layout_info(width)
    print()
    all_ok &= test_build_gadget(width)
    print()
    all_ok &= test_cycle_length(width)
    print()
    all_ok &= test_no_error(width)
    print()
    all_ok &= test_single_error(width)
    print()
    all_ok &= test_copyover(width)
    print()

    if all_ok:
        sim, layout, cycle_length = make_probe_bypass_ouroboros(width)
        out_dir = os.path.dirname(os.path.abspath(__file__))
        out_path = os.path.join(out_dir, f'immunity-gadgets-v7-syndrome-inspect-w{width}.fb2d')
        sim.save_state(out_path, hints={'waste_cleanup': 1})
        print(f"Saved: {out_path}")
        print("=" * 60)
        print(f"ALL v7 TESTS PASSED (W={width})")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

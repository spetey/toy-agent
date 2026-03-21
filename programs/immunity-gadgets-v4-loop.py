#!/usr/bin/env python3
"""
immunity-gadgets-v4-loop.py — Rewind-loop correction architecture.

Extends v3's probe-bypass architecture by replacing the ping-pong
vertical bounce handler with a rewind loop that sends IX back to the
top after hitting the bottom boundary.  This gives uniform sweep
frequency — every row is visited every N steps, instead of top/bottom
rows having 2x exposure from ping-pong.

ARCHITECTURE (per gadget, R+7 rows):
  Row 0:        BOUNDARY ROW (0xFFFF; top boundary for IX)
  Row 1:        BYPASS ROW (NOP-filled, IX scans)
  Row 2:        RETURN ROW (NOP-filled, IX scans; rewind loop path)
  Row 3:        HANDLER ROW (boundary handlers going East, NOP-filled)
  Rows 4..R+3:  CODE ROWS (boustrophedon)
  Row R+4:      BOUNDARY ROW (0xFFFF; bottom boundary for IX)
  Row R+5:      STOMACH ROW (working area: H0, H1, CL fixed here)
  Row R+6:      WASTE ROW (EX roams, eats zeros, excretes waste)

IX SCAN AREA: rows 1..R+3 (bypass + return + handler + code = R+3).
  Vertical boundary: 0xFFFF rows above and below.
  Horizontal boundary: col 0 and col W-1 are 0xFFFF on scan rows.
  Boundary detection: m T : ? ; T m (: wraps payload 2047→0, ? fires).

KEY DESIGN: three ? mirrors, three rows above code.
  - Boundary ? sends IP North from code row 4 to handler row 3.
    Handler entry / (N→E) catches the IP.
    Horizontal handler: / B C U ; \\.  (6 ops)
    Rewind handler: / D & B D A m T : % ; T m C ; \\.  (16 ops)
    Exit \\ (E→S) drops IP back to code row at the & merge gate.
  - Probe ? sends IP North from code row 4 through handler+return NOPs
    to bypass row 1.  Entry \\ (N→W).  Bypass ops go West.
    Exit / (W→S) at col 2.  IP drops through (2,2) NOP → (3,2) NOP
    → (4,2) & merge gate.

REWIND HANDLER: / D & B D A m T : % ; T m C ; \\
  Replaces the old ping-pong bounce handler (/ D O ; X \\).
  On vertical boundary detection, loops IX back to the top boundary
  row-by-row for uniform sweep frequency.
  / entry (N→E).  D retreats IX from boundary.  & is the loop
  re-entry point (\\-if-CL≠0): first entry has CL=0 so & is NOP;
  on re-entry the return row sets CL≠0 via ; so & fires (S→E).
  B D A moves IX up one row.  m T : % tests for boundary.
  Non-boundary: % fires → return row (west-going: \\ ; T m ; /)
  → back to & for next iteration.  Boundary: % doesn't fire →
  ; T m undoes test, C advances IX south from boundary to bypass
  row (ix_vdir=S), ; sets CL signal, \\ exits to code row.

SIGNAL: ; (decrement) not : (increment).
  : increments from 0 to 1 (= bit 0 only).  Bit 0 is a Hamming parity
  position, invisible to DATA_MASK.  & tests grid[CL] & DATA_MASK,
  so payload(1) = 0 and & never fires.  ; decrements from 0 to 0xFFFF
  which has all bits set → & fires correctly.

HANDLER/RETURN/BYPASS ROW FILL:
  NOP cells (non-zero valid Hamming codewords that decode to NOP).
  - Non-zero so IX includes all three rows in its scan (gets corrected!).
  - NOP so the probe IP passes through without executing anything.
  - NOP value: hamming_encode(1017) = 0x7e8e (2-bit-error safe).

HANDLER ALIGNMENT:
  Horizontal handler: 6 ops (/ B C U ; \\).  Exit \\ at ?_col + 5.
  & merge gate at ?_col + 5 on code row.
  Rewind handler: 16 ops.  Exit \\ at ?_col + 15.
  & merge gate at ?_col + 15 on code row (with T Z ] deposit after).

CORRIDOR: col 1
  Last code row (R+3) col 1: \\ (W→N)
  First code row (4) col 1: / (N→E)
  Intermediate code rows col 1: NOP filler (o)
  Merge gate & at (4, 2) — corridor, handler, and bypass all converge.
  Col 0 = 0xFFFF boundary (western IX boundary).

VULNERABILITY ANALYSIS:
  Clean path: ~90 steps (probe + bypass).  Only PA written then zeroed.
  Dirty path: ~370 steps (probe + correction).  Full 9-cell exposure.
  At 95% clean cells: avg exposure drops from ~370 to ~104 steps.

Run tests:  python3 programs/immunity-gadgets-v4-loop.py [--width W]
"""

import sys
import os
import math
import random
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, DIR_N, DIR_S, DIR_W, encode_opcode, OPCODE_PAYLOADS)

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

# NOP cell value: non-zero valid Hamming codeword, decodes to NOP (opcode 0).
# Used to fill handler/bypass rows so IX sees non-zero cells but IP passes through.
# Must be in the [11,6,4] opcode code's correction ball for opcode 0 — i.e.,
# every single data-bit flip still decodes to NOP.
#
# Payload 1017 is the 64th (last unused) codeword of the [11,6,4] opcode code.
# As a codeword, it has d_min=4 from all other codewords, giving:
#   - 1-bit safe: all 11 data-bit flips → NOP (0/11 become real opcodes)
#   - 2-bit safe: all 55 data-bit pairs → NOP (0/55 become real opcodes)
# Compare payload 1019 (non-codeword): 1-bit safe but 30/55 2-bit → real opcodes.
# Data-bit distance 8 from zero, distance 3 from boundary marker (payload 2047).
NOP_CELL = hamming_encode(1017)  # 0x7e8e, data-bit dist 8 from zero

# Boundary marker: 0xFFFF (payload 2047).  Not a valid opcode (decodes to NOP).
# Detected by : ? ; pattern (: increments payload 2047→0, ? fires on zero).
# Displayed as '~' in both REPL and GUI.
BOUNDARY_CELL = 0xFFFF


# ═══════════════════════════════════════════════════════════════════
# Probe-bypass gadget builder
# ═══════════════════════════════════════════════════════════════════

def build_probe_bypass_gadget(last_row_dir):
    """Build the probe-bypass correction gadget.

    Order:
      1. Preamble (T Z ]) — deposit handler/bypass CL signal to waste
      2. IX advance + horizontal boundary test (A m T : ? ; T m o)
      3. Handler #1 merge (&) + CL deposit (T Z ]) + vertical test
         (m T : ? ; T m o...) + handler #2 merge (&)
      4. Copy-in (m) + Probe (Phase A + B + T + ?)
      5. [BRANCH: clean → bypass row 0, dirty → continue]
      6. Correction (Phase A' + C + D + C' + uncompute + writeback + F + G)

    The o o padding (NOP filler) after each T m undo allows 6-op handlers going East
    on the handler row (/ B C U ; \\) to align their exit with the & gate.

    Returns: (main_ops, probe_branch_idx, bypass_ops)
    """
    gb = GadgetBuilder()

    # ── 1. Preamble: deposit handler/bypass CL signal to waste ──
    gb.emit('T')     # swap [CL=ROT] ↔ [H0=CWL]
    gb.emit('Z')     # swap [H0=CWL] ↔ [EX=waste]
    gb.emit(']')     # EX advance east on waste row

    # ── 2. IX advance + horizontal boundary test ──
    # Boundary cells are 0xFFFF (payload 2047).  : increments payload
    # 2047→0, then ? fires on zero.  ; undoes the increment.
    gb.emit('A')     # advance IX in ix_dir
    gb.emit('m')     # [H0=CWL] ^= [IX] → CWL = remote value (was 0)
    gb.emit('T')     # swap [CL=ROT] ↔ [H0=CWL] → CL has remote value
    gb.emit(':')     # CL++ (boundary 2047→0, normal cells stay non-zero)
    gb.emit('?')     # horizontal boundary test: / if CL==0 → E→N
    gb.emit(';')     # undo: CL-- (restore original payload)
    gb.emit('T')     # undo: CL ↔ CWL
    gb.emit('m')     # undo: CWL ^= [IX] → CWL = 0
    gb.emit('o')     # NOP filler — aligns handler exit with &

    # ── 3. Handler #1 merge + CL deposit + vertical test ──
    gb.emit('&')     # handler #1 merge gate (\ if CL!=0)
    gb.emit('T')     # deposit: swap CL ↔ CWL
    gb.emit('Z')     # deposit: swap CWL ↔ EX → waste
    gb.emit(']')     # EX advance

    gb.emit('m')     # vertical test: [H0=CWL] ^= [IX]
    gb.emit('T')     # CL ↔ CWL
    gb.emit(':')     # CL++ (boundary 2047→0)
    gb.emit('?')     # vertical boundary test: / if CL==0 → E→N
    gb.emit(';')     # undo: CL--
    gb.emit('T')     # undo
    gb.emit('m')     # undo
    # NOP filler padding for 16-op rewind handler alignment (exit \ at vbound+15)
    for _ in range(11):
        gb.emit('o')
    gb.emit('&')     # rewind handler merge gate (handler \ at ?+15)
    gb.emit('T')     # deposit rewind handler CL signal
    gb.emit('Z')     # to waste
    gb.emit(']')     # EX advance

    # ── 4. Copy-in + Probe ──
    copy_in_pos = gb.pos()   # mark for bypass CL-undo count
    gb.emit('m')     # [H0=CWL] ^= [IX] → CWL = remote codeword

    # Phase A: overall parity via Y
    gb.move_h0_col(DSL_PA)          # CWL(2) → PA(1): W
    gb.xor_accumulate_bits(list(range(16)))   # Y at rotations 0..15

    # Phase B: z-extract PA.bit0 → EV
    gb.move_h0_col(DSL_EV)          # PA(1) → EV(0): W
    gb.move_h1_col(DSL_PA)          # CWL(2) → PA(1): w
    gb.emit('z')                     # EV.bit0 ← PA.bit0; PA.bit0 ← 0
    gb.move_h1_col(DSL_CWL)         # PA(1) → CWL(2): e

    # Rotate p_all from bit0 (parity position) to bit3 (data position).
    # Conditional mirrors test PAYLOAD, not raw cell value.  Bit 0 is a
    # Hamming parity bit (invisible to payload).  Bit 3 is the first data
    # bit — payload will be non-zero if p_all=1.
    gb.emit_n('l', 3)   # rotate EV left 3: bit0 → bit3

    # Test: swap rotated p_all into CL for conditional mirror
    gb.emit('T')     # swap [CL=ROT] ↔ [H0=EV] → CL gets rotated p_all

    # Probe conditional mirror
    gb.emit('?')     # PROBE TEST: / if payload(CL)==0 → E→N → bypass
    probe_branch_idx = gb.pos() - 1

    # ── If dirty (? didn't fire): continue correction ──
    gb.emit('T')     # undo: CL ↔ EV restore
    gb.emit_n('r', 3)   # undo rotation: bit3 → bit0

    # Phase A': Y-uncompute PA
    gb.move_h0_col(DSL_PA)          # EV(0) → PA(1): E
    gb.xor_accumulate_bits(list(range(15, -1, -1)))   # CL: 15→0

    # Phase C: Syndrome computation via Y
    gb.move_h0_col(DSL_S0)          # PA(1) → S0(3): E×2
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])

    gb.move_h0_col(dgd.DSL_S1)
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])

    gb.move_h0_col(dgd.DSL_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])

    gb.move_h0_col(dgd.DSL_S3)
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    # Phase D: Barrel shifter
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

    # Phase C': Y-uncompute S0-S3
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

    # Uncompute local copy
    gb.move_h0_col(DSL_CWL)
    gb.emit('m')     # CWL ^= [IX] → CWL = 0

    # Write correction to remote
    gb.move_h0_col(DSL_EV)
    gb.emit('j')     # [IX] ^= [H0=EV]

    # Phase F: cleanup
    gb.move_h1_col(DSL_PA)
    gb.emit('z')     # swap bit0 EV ↔ PA
    gb.emit('x')     # EV ^= PA

    # Epilogue: return H0, H1 to CWL
    gb.move_h0_col(DSL_CWL)
    gb.move_h1_col(DSL_CWL)

    # Phase G: waste deposit
    gb.move_h0_col(DSL_EV)
    gb.emit('Z')     # deposit EV waste
    gb.emit(']')     # EX advance
    gb.move_h0_col(DSL_PA)
    gb.emit('Z')     # deposit PA waste
    gb.emit(']')     # EX advance
    gb.move_h0_col(DSL_CWL)
    gb.move_h1_col(DSL_CWL)

    main_ops = gb.ops

    # ── Build bypass ops (executed on bypass row going West) ──
    # After probe fires (CL==0, clean cell), state is:
    #   H0 at EV(0), H1 at CWL(2), CL at ROT(8)
    #   [EV] = 0 (T swapped p_all=0 to EV, which was rotated overall parity)
    #   [PA] = Phase A junk (bit0 cleared by z)
    #   [CWL] = remote codeword (from copy-in m)
    #   [ROT] = 0 (was p_all=0, now swapped to ROT via T)
    #   BUT: CL was incremented 15 times by Phase A+B's : ops!
    #         After T, those 15 increments are in [EV], not CL.
    #         (T swapped CL↔EV at the probe point.)
    #
    # Bypass must:
    #   1. Undo T (self-inverse): puts Phase A+B accumulator back in CL
    #   2. Undo l l l rotation (no-op for clean, EV=0)
    #   3. Undo 15 : increments with 15 ; decrements
    #   4. Undo z + head moves (both bit0s are 0, z is no-op)
    #   5. Zero PA via Z swap to waste (faster than Phase A' uncompute)
    #   6. Move H0 to CWL, undo copy-in (m)
    #   7. CL signal (;) for merge gate — ; not :, because value 1
    #      (from :) has only bit 0 set, which is a parity position
    #      invisible to DATA_MASK.  ; decrements 0→0xFFFF, all bits set.
    # Note: NO IX advance on bypass — IX already advanced by A at
    # the beginning of the main code path. Next cycle's A advances IX.
    #
    # The 15+1=16 ; ops fit easily: bypass row has ~50 empty NOP cells
    # between the last bypass op and the exit / at col 2.

    # Count : ops between copy-in and probe to compute undo count
    n_cl_increments = sum(1 for op in gb.ops[copy_in_pos:probe_branch_idx] if op == ':')

    bypass = [
        'T',               # undo T: CL ↔ EV (puts Phase A+B accum back in CL)
        'r', 'r', 'r',    # undo l l l rotation (bit3 → bit0; clean=0, no-op)
    ]
    bypass += [';'] * n_cl_increments  # undo the 15 : increments from Phase A+B
    bypass += [
        'w',       # H1: CWL(2) → PA(1)
        'z',       # undo z: swap bit0 EV ↔ PA (both 0, no-op)
        'e',       # H1: PA(1) → CWL(2)
        'E',       # H0: EV(0) → PA(1)
        'Z',       # swap [H0=PA] ↔ [EX=waste]: PA junk → waste
        ']',       # EX advance past dirty cell
        'E',       # H0: PA(1) → CWL(2)
        'm',       # undo copy-in: CWL ^= [IX] → CWL = 0
        ';',       # CL-- (0→0xFFFF, visible to & merge gate)
    ]

    return main_ops, probe_branch_idx, bypass


# ═══════════════════════════════════════════════════════════════════
# Layout and grid builder
# ═══════════════════════════════════════════════════════════════════

def compute_probe_layout(width):
    """Compute grid layout for probe-bypass ouroboros."""
    code_left = 3           # col 0 = blank boundary, col 1 = corridor, col 2 = merge
    code_right = width - 2  # col W-1 = blank boundary

    first_row_slots = code_right - code_left
    inner_row_slots = code_right - code_left - 1

    # Build trial gadget to get op count
    last_dir = _last_row_direction_probe(999, code_left, code_right)
    main_ops, probe_idx, bypass_ops = build_probe_bypass_gadget(last_dir)
    n_ops = len(main_ops)

    # Recompute with actual op count
    last_dir = _last_row_direction_probe(n_ops, code_left, code_right)
    main_ops2, probe_idx2, bypass_ops2 = build_probe_bypass_gadget(last_dir)
    if len(main_ops2) != n_ops:
        n_ops = len(main_ops2)
        main_ops = main_ops2
        probe_idx = probe_idx2
        bypass_ops = bypass_ops2
        last_dir = _last_row_direction_probe(n_ops, code_left, code_right)

    if n_ops <= first_row_slots:
        code_rows = 1
    else:
        remaining = n_ops - first_row_slots
        code_rows = 1 + math.ceil(remaining / inner_row_slots)

    # Per gadget: blank + bypass + return + handler + R code + blank + stomach + waste = R+7
    rows_per_gadget = code_rows + 7
    total_rows = 2 * rows_per_gadget

    # Row assignments (gadget A)
    ga_blank_top = 0
    ga_bypass = 1
    ga_return = 2
    ga_handler = 3
    ga_code_start = 4
    ga_code_end = 3 + code_rows  # inclusive
    ga_blank_bot = 4 + code_rows
    ga_stomach = 5 + code_rows
    ga_waste = 6 + code_rows

    # Gadget B
    gb_blank_top = rows_per_gadget
    gb_bypass = rows_per_gadget + 1
    gb_return = rows_per_gadget + 2
    gb_handler = rows_per_gadget + 3
    gb_code_start = rows_per_gadget + 4
    gb_code_end = rows_per_gadget + 3 + code_rows
    gb_blank_bot = rows_per_gadget + 4 + code_rows
    gb_stomach = rows_per_gadget + 5 + code_rows
    gb_waste = rows_per_gadget + 6 + code_rows

    layout = {
        'width': width,
        'n_ops': n_ops,
        'probe_branch_idx': probe_idx,
        'bypass_ops': bypass_ops,
        'code_rows': code_rows,
        'rows_per_gadget': rows_per_gadget,
        'total_rows': total_rows,
        'last_row_dir': last_dir,
        'ga_blank_top': ga_blank_top,
        'ga_bypass': ga_bypass,
        'ga_return': ga_return,
        'ga_handler': ga_handler,
        'ga_code': (ga_code_start, ga_code_end),
        'ga_blank_bot': ga_blank_bot,
        'ga_stomach': ga_stomach,
        'ga_waste': ga_waste,
        'gb_blank_top': gb_blank_top,
        'gb_bypass': gb_bypass,
        'gb_return': gb_return,
        'gb_handler': gb_handler,
        'gb_code': (gb_code_start, gb_code_end),
        'gb_blank_bot': gb_blank_bot,
        'gb_stomach': gb_stomach,
        'gb_waste': gb_waste,
        'code_left': code_left,
        'code_right': code_right,
        'first_row_slots': first_row_slots,
        'inner_row_slots': inner_row_slots,
    }
    return layout


def _last_row_direction_probe(n_ops, code_left, code_right):
    """Determine direction of the last boustrophedon row."""
    first_row_slots = code_right - code_left
    inner_row_slots = code_right - code_left - 1

    if n_ops <= first_row_slots:
        return DIR_E

    remaining = n_ops - first_row_slots
    extra_rows = math.ceil(remaining / inner_row_slots)
    total_row_count = 1 + extra_rows

    return DIR_W if total_row_count % 2 == 0 else DIR_E


def _boustrophedon_op_position(op_idx, code_left, code_right, start_row):
    """Return (row, col, direction) for op at given index."""
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


def make_probe_bypass_ouroboros(width=99, errors=None):
    """Build the probe-bypass dual ouroboros grid."""
    layout = compute_probe_layout(width)

    last_dir = layout['last_row_dir']
    main_ops, probe_idx, bypass_ops = build_probe_bypass_gadget(last_dir)
    # 'o' = NOP filler (payload 1017); encode as negative raw cell value
    # so place_boustrophedon uses it directly (not via encode_opcode).
    op_values = [-NOP_CELL if ch == 'o' else OP[ch] for ch in main_ops]

    T = layout['total_rows']
    W = width

    sim = FB2DSimulator(rows=T, cols=W)

    # Place both gadgets
    ga_code_start = layout['ga_code'][0]
    gb_code_start = layout['gb_code'][0]

    _place_probe_gadget(sim, layout, op_values, main_ops,
                        ga_code_start, layout['ga_bypass'],
                        layout['ga_return'], layout['ga_handler'],
                        probe_idx, bypass_ops)
    _place_probe_gadget(sim, layout, op_values, main_ops,
                        gb_code_start, layout['gb_bypass'],
                        layout['gb_return'], layout['gb_handler'],
                        probe_idx, bypass_ops)

    # Inject errors
    if errors:
        for row, col, bit in errors:
            flat = sim._to_flat(row, col)
            sim.grid[flat] = inject_error(sim.grid[flat], bit)

    # Head positions
    ga_stomach = layout['ga_stomach']
    gb_stomach = layout['gb_stomach']
    ga_waste = layout['ga_waste']
    gb_waste = layout['gb_waste']
    code_left = layout['code_left']

    # IP0: runs gadget A, IX scans gadget B (starting on bypass row)
    gb_bypass = layout['gb_bypass']
    ga_bypass = layout['ga_bypass']
    sim.ip_row = ga_code_start
    sim.ip_col = code_left
    sim.ip_dir = DIR_E
    sim.h0 = sim._to_flat(ga_stomach, DSL_CWL)
    sim.h1 = sim._to_flat(ga_stomach, DSL_CWL)
    # IX starts one col back on bypass row: first A advances to code_left
    sim.ix = sim._to_flat(gb_bypass, code_left - 1)
    sim.cl = sim._to_flat(ga_stomach, DSL_ROT)
    sim.ex = sim._to_flat(ga_waste, 0)

    # IP1: runs gadget B, IX scans gadget A (starting on bypass row)
    sim.add_ip(
        ip_row=gb_code_start, ip_col=code_left, ip_dir=DIR_E,
        h0=sim._to_flat(gb_stomach, DSL_CWL),
        h1=sim._to_flat(gb_stomach, DSL_CWL),
        ix=sim._to_flat(ga_bypass, code_left - 1),
        cl=sim._to_flat(gb_stomach, DSL_ROT),
        ex=sim._to_flat(gb_waste, 0),
    )

    # Compute cycle length (dirty path, for reference)
    cycle_length = _compute_cycle_length(sim, layout)

    return sim, layout, cycle_length


def _place_probe_gadget(sim, layout, op_values, main_ops,
                        code_start_row, bypass_row, return_row,
                        handler_row, probe_branch_idx, bypass_ops):
    """Place one probe-bypass gadget with rewind loop.

    BOUNDARY ROWS (rows 0 and R+4 relative to gadget):
      Filled with 0xFFFF boundary markers.  IX vertical boundaries.
      Detected by : ? ; pattern (payload 2047+1 wraps to 0).

    BYPASS ROW (row 1 relative to gadget):
      Filled with NOP cells so IX includes it in the scan (gets corrected!).
      Entry \\ at probe_col (N→W).  Bypass ops going West.
      Exit / at col 2 (W→S).  IP drops through return+handler NOPs to & merge.

    RETURN ROW (row 2 relative to gadget):
      NOP-filled (IX scans it).  West-going path for rewind loop.
      \\ at %_col catches N→W from handler's % exit.
      Ops going West: ; T m : (undo boundary test + restore CL=0).
      / at !_col catches W→S back to handler's ! re-entry.

    HANDLER ROW (row 3 relative to gadget):
      Filled with NOP cells (non-zero, valid Hamming, opcode 0).
      Horizontal handler: / B C U ; \\  (6 ops, going East).
      Rewind handler:  / D & B D A m T : % ; T m C ; \\  (16 ops, going East).
      Handler exit \\ (E→S) drops to code row where & merge gate is.

    BOUNDARY COLUMNS: col 0 and col W-1 on all scan rows = 0xFFFF.

    CORRIDOR: col 1 on code rows.
      Last code row col 1: \\ (W→N).  First code row col 1: / (N→E).
      & merge gate at (first_code_row, col 2).
    """
    W = layout['width']
    R = layout['code_rows']
    code_left = layout['code_left']
    code_right = layout['code_right']
    last_dir = layout['last_row_dir']

    assert last_dir == DIR_W, (
        f"Probe-bypass requires west-going last row; "
        f"got {'East' if last_dir == DIR_E else last_dir}")

    first_code_row = code_start_row
    last_code_row = code_start_row + R - 1

    # ── Place boustrophedon code ──
    rows_used, end_row, last_col, end_dir_int = place_boustrophedon(
        sim, op_values, code_left, code_right, start_row=code_start_row)

    # Fill partial last row with NOP filler
    _fill_row_with_nop(sim, layout, last_code_row)

    # ── Corridor at col 1 (col 0 = blank boundary for IX) ──
    sim.grid[sim._to_flat(last_code_row, 1)] = encode_opcode(OP['\\'])
    sim.grid[sim._to_flat(first_code_row, 1)] = encode_opcode(OP['/'])

    # ── Merge gate at (first_code_row, col 2) ──
    sim.grid[sim._to_flat(first_code_row, 2)] = encode_opcode(OP['&'])

    # Fill cols 1-2 on all non-first code rows with NOP filler.
    # (First code row has / at col 1 and & at col 2; last code row has
    # \ at col 1 but col 2 still needs NOP filler so IX can scan through it.)
    for row in range(first_code_row + 1, last_code_row + 1):
        for col in [1, 2]:
            flat = sim._to_flat(row, col)
            if sim.grid[flat] == 0:
                sim.grid[flat] = NOP_CELL

    # ── Fill handler, return, and bypass rows with NOP cells (cols 1..code_right) ──
    # NOP cells so IX includes these rows in its scan (gets corrected!).
    for row in [handler_row, return_row, bypass_row]:
        for col in range(1, code_right + 1):
            sim.grid[sim._to_flat(row, col)] = NOP_CELL

    # ── Fill boundary markers (0xFFFF) ──
    # Blank rows (top and bottom): entire row is boundary.
    blank_top = bypass_row - 1
    blank_bot = code_start_row + R
    for col in range(W):
        sim.grid[sim._to_flat(blank_top, col)] = BOUNDARY_CELL
        sim.grid[sim._to_flat(blank_bot, col)] = BOUNDARY_CELL

    # Col 0 and col W-1 on scan rows (bypass, return, handler, code): boundary.
    for row in [bypass_row, return_row, handler_row] + list(range(first_code_row, last_code_row + 1)):
        sim.grid[sim._to_flat(row, 0)] = BOUNDARY_CELL
        sim.grid[sim._to_flat(row, W - 1)] = BOUNDARY_CELL

    # ── Locate the three ? mirrors in the boustrophedon ──
    question_indices = [i for i, op in enumerate(main_ops) if op == '?']
    assert len(question_indices) >= 3, \
        f"Expected 3 ? mirrors, found {len(question_indices)}"

    hbound_idx = question_indices[0]  # horizontal boundary
    vbound_idx = question_indices[1]  # vertical boundary
    probe_idx = question_indices[2]   # probe

    hbound_row, hbound_col, _ = _boustrophedon_op_position(
        hbound_idx, code_left, code_right, code_start_row)
    vbound_row, vbound_col, _ = _boustrophedon_op_position(
        vbound_idx, code_left, code_right, code_start_row)
    probe_row, probe_col, _ = _boustrophedon_op_position(
        probe_idx, code_left, code_right, code_start_row)

    # All three ? should be on the first code row (going East)
    assert hbound_row == first_code_row, \
        f"H-boundary ? at row {hbound_row}, expected {first_code_row}"
    assert vbound_row == first_code_row, \
        f"V-boundary ? at row {vbound_row}, expected {first_code_row}"
    assert probe_row == first_code_row, \
        f"Probe ? at row {probe_row}, expected {first_code_row}"

    # ── Place horizontal boundary handler on handler row (going East) ──
    # Entry / (N→E), ops B C U ;, exit \ (E→S)
    # ; not : — see SIGNAL note in module docstring.
    h_handler_ops = ['/', 'B', 'C', 'U', ';', '\\']
    for i, op_ch in enumerate(h_handler_ops):
        col = hbound_col + i
        sim.grid[sim._to_flat(handler_row, col)] = encode_opcode(OP[op_ch])

    # Verify handler exit aligns with first & merge gate
    h_exit_col = hbound_col + 5
    merge1_idx = None
    for i, op in enumerate(main_ops[hbound_idx+1:], hbound_idx+1):
        if op == '&':
            merge1_idx = i
            break
    assert merge1_idx is not None, "No & after horizontal boundary"
    _, merge1_col, _ = _boustrophedon_op_position(
        merge1_idx, code_left, code_right, code_start_row)
    assert h_exit_col == merge1_col, \
        f"Handler exit col {h_exit_col} != & col {merge1_col}"

    # ── Place rewind handler on handler row (going East) ──
    # 16-op handler: / D & B D A m T : % ; T m C ; \
    # Replaces old 6-op bounce handler (/ D O ; X \).
    # On vertical boundary detection, loops IX back to top boundary
    # row-by-row for uniform sweep frequency.
    #   / entry (N→E), D retreat IX from boundary,
    #   & loop re-entry (\ if CL≠0; first entry CL=0 → NOP),
    #   B D A move IX up one row (retreat h, retreat v, advance h),
    #   m T : % boundary test (% fires if non-boundary → return row),
    #   ; T m undo test, C advance IX south from boundary to bypass row,
    #   ; signal for & merge, \ exit (E→S).
    # Return row sets CL≠0 via ; before re-entering at &.
    v_handler_ops = ['/', 'D', '&', 'B', 'D', 'A',
                     'm', 'T', ':', '%', ';', 'T', 'm', 'C', ';', '\\']
    assert len(v_handler_ops) == 16, f"Rewind handler has {len(v_handler_ops)} ops, expected 16"
    for i, op_ch in enumerate(v_handler_ops):
        col = vbound_col + i
        sim.grid[sim._to_flat(handler_row, col)] = encode_opcode(OP[op_ch])

    # ── Place return row ops (west-going rewind loop path) ──
    # % at handler position 9 fires N→ return row.
    # \ at return_row catches N→W. Ops going West: ; T m ;.
    # / at return_row catches W→S back to handler & at position 2.
    percent_col = vbound_col + 9    # handler position of %
    ampersand_col = vbound_col + 2  # handler position of &

    sim.grid[sim._to_flat(return_row, percent_col)] = encode_opcode(OP['\\'])
    return_ops = [';', 'T', 'm', ';']   # going West from percent_col-1
    for i, op_ch in enumerate(return_ops):
        col = percent_col - 1 - i
        sim.grid[sim._to_flat(return_row, col)] = encode_opcode(OP[op_ch])
    sim.grid[sim._to_flat(return_row, ampersand_col)] = encode_opcode(OP['/'])

    # Verify return row / aligns with handler &
    assert percent_col - 1 - (len(return_ops) - 1) > ampersand_col, \
        f"Return row ops overlap with / at ampersand_col={ampersand_col}"

    # Verify rewind handler exit aligns with second & merge gate
    v_exit_col = vbound_col + 15   # 16-op handler, \ at position 15
    merge2_idx = None
    for i, op in enumerate(main_ops[vbound_idx+1:], vbound_idx+1):
        if op == '&':
            merge2_idx = i
            break
    assert merge2_idx is not None, "No & after vertical boundary"
    _, merge2_col, _ = _boustrophedon_op_position(
        merge2_idx, code_left, code_right, code_start_row)
    assert v_exit_col == merge2_col, \
        f"Rewind exit col {v_exit_col} != & col {merge2_col}"

    # ── Place bypass path on bypass row (over NOP fill) ──
    # Entry: \ at (bypass_row, probe_col). N→W.
    sim.grid[sim._to_flat(bypass_row, probe_col)] = encode_opcode(OP['\\'])

    # Bypass ops go West from probe_col-1
    bypass_op_values = [OP[ch] for ch in bypass_ops]
    for i, opval in enumerate(bypass_op_values):
        col = probe_col - 1 - i
        sim.grid[sim._to_flat(bypass_row, col)] = encode_opcode(opval)

    # Exit: / at (bypass_row, col 2). W→S.
    # IP traverses NOPs from last bypass op to col 2, then / sends it
    # South through (handler_row, 2) NOP → (first_code_row, 2) & merge.
    sim.grid[sim._to_flat(bypass_row, 2)] = encode_opcode(OP['/'])


def _fill_row_with_nop(sim, layout, row):
    """Fill empty cells on a row with NOP filler (payload 1017, 'o')."""
    code_left = layout['code_left']
    code_right = layout['code_right']
    for col in range(code_left, code_right + 1):
        flat = sim._to_flat(row, col)
        if sim.grid[flat] == 0:
            sim.grid[flat] = NOP_CELL


def _compute_cycle_length(sim, layout):
    """Compute steps for one full IP loop (dirty path, unconditional mirrors only)."""
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

        if opcode == 1:  # /
            d = SLASH[d]
        elif opcode == 2:  # backslash
            d = BACKSLASH[d]

        r = (r + dr[d]) % sim.rows
        c = (c + dc[d]) % sim.cols

        if r == start_row and c == code_left and d == DIR_E:
            break

        if steps > 50000:
            raise RuntimeError(f"Cycle exceeded 50000 at ({r},{c}) dir={d}")

    return steps


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_layout_info(width=99):
    """Print layout information."""
    layout = compute_probe_layout(width)
    print(f"=== Probe-Bypass Layout (W={width}) ===")
    print(f"    Main gadget: {layout['n_ops']} ops")
    print(f"    Bypass: {len(layout['bypass_ops'])} ops")
    print(f"    Code rows: {layout['code_rows']}")
    print(f"    Last row: {'West' if layout['last_row_dir'] == DIR_W else 'East'}")
    print(f"    Grid: {layout['total_rows']}x{width}")
    scan_rows = layout['code_rows'] + 3
    print(f"    Scan rows: {scan_rows} "
          f"(bypass+return+handler+{layout['code_rows']}code)")
    print(f"    GA: blank={layout['ga_blank_top']}, bypass={layout['ga_bypass']}, "
          f"return={layout['ga_return']}, handler={layout['ga_handler']}, "
          f"code={layout['ga_code'][0]}-{layout['ga_code'][1]}, "
          f"blank={layout['ga_blank_bot']}, "
          f"stomach={layout['ga_stomach']}, waste={layout['ga_waste']}")
    print(f"    GB: blank={layout['gb_blank_top']}, bypass={layout['gb_bypass']}, "
          f"return={layout['gb_return']}, handler={layout['gb_handler']}, "
          f"code={layout['gb_code'][0]}-{layout['gb_code'][1]}, "
          f"blank={layout['gb_blank_bot']}, "
          f"stomach={layout['gb_stomach']}, waste={layout['gb_waste']}")

    code_left = layout['code_left']
    code_right = layout['code_right']
    probe_row, probe_col, _ = _boustrophedon_op_position(
        layout['probe_branch_idx'], code_left, code_right, layout['ga_code'][0])
    print(f"    Probe ? at ({probe_row},{probe_col})")

    # Verify bypass fits
    bypass_len = len(layout['bypass_ops'])
    last_bypass_col = probe_col - 1 - (bypass_len - 1)
    print(f"    Bypass: cols {probe_col-1}..{last_bypass_col} "
          f"({bypass_len} ops), exit / at col 2")

    ok = last_bypass_col >= 3  # must leave col 2 for exit /
    if not ok:
        print(f"    FAIL: bypass extends past col 2 (last op at col {last_bypass_col})")
    return ok


def test_build_gadget(width=99):
    """Test that the gadget builds successfully."""
    print(f"=== Build gadget (W={width}) ===")
    try:
        sim, layout, cycle_length = make_probe_bypass_ouroboros(width)
        print(f"    Grid: {sim.rows}x{sim.cols}")
        print(f"    Cycle length (dirty path): {cycle_length} steps")
        print(f"    PASS")
        return True
    except Exception as e:
        print(f"    FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cycle_length(width=99):
    """Measure actual cycle length for clean cells (bypass path).

    With no errors, all cells are clean → short cycle.
    """
    print(f"=== Cycle length (W={width}) ===")
    sim, layout, cycle_length_dirty = make_probe_bypass_ouroboros(width)
    print(f"    Dirty-path cycle length (computed): {cycle_length_dirty}")

    start_row = sim.ip_row
    start_col = sim.ip_col
    start_dir = sim.ip_dir

    # Step until IP0 returns to start
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
    print(f"    Clean-path actual: {actual_steps} steps")
    print(f"    IP returns: {'ok' if ok else 'FAIL'}"
          f" ({sim.ip_row},{sim.ip_col} dir={sim.ip_dir})")
    if ok:
        savings = cycle_length_dirty - actual_steps
        pct = savings / cycle_length_dirty * 100 if cycle_length_dirty else 0
        print(f"    Savings: {savings} steps ({pct:.0f}%)")
    return ok


def _cheat_clear_waste(sim, layout):
    """Zero waste rows for infinite-zeros mode."""
    W = layout['width']
    for waste_row in [layout['ga_waste'], layout['gb_waste']]:
        base = waste_row * W
        for c in range(W):
            sim.grid[base + c] = 0


def test_no_error(width=99):
    """Test that clean cells pass through without corruption."""
    print(f"=== No error (W={width}) ===")
    sim, layout, cycle_length = make_probe_bypass_ouroboros(width, errors=[])

    # Save grid
    grid_before = sim.grid[:]

    # Run several cycles (use dirty cycle length as upper bound)
    n_cycles = 10
    total = n_cycles * cycle_length
    for _ in range(total):
        sim.step_all()
        _cheat_clear_waste(sim, layout)

    # Check code + handler rows unchanged
    all_ok = True
    check_rows = []
    for gadget in [('GA', layout['ga_code'], layout['ga_handler'],
                    layout['ga_return'], layout['ga_bypass']),
                   ('GB', layout['gb_code'], layout['gb_handler'],
                    layout['gb_return'], layout['gb_bypass'])]:
        name, (cs, ce), hr, rr, br = gadget
        check_rows.append(br)   # bypass
        check_rows.append(rr)   # return
        check_rows.append(hr)   # handler
        check_rows.extend(range(cs, ce + 1))  # code

    for row in check_rows:
        base = row * layout['width']
        for col in range(layout['width']):
            flat = base + col
            if sim.grid[flat] != grid_before[flat]:
                print(f"    MODIFIED ({row},{col}): "
                      f"0x{grid_before[flat]:04x} -> 0x{sim.grid[flat]:04x}")
                all_ok = False

    print(f"    {'PASS' if all_ok else 'FAIL'}: scan area rows "
          f"{'unchanged' if all_ok else 'CHANGED'} after {n_cycles} cycles")
    return all_ok


def test_single_error(width=99):
    """Test correction of a single error on gadget B."""
    print(f"=== Single error (W={width}) ===")
    layout = compute_probe_layout(width)
    gb_start = layout['gb_code'][0]
    code_left = layout['code_left']

    # Error on first cell of gadget B's first code row
    row, col, bit = gb_start, code_left, 3
    errors = [(row, col, bit)]

    sim, layout, cycle_length = make_probe_bypass_ouroboros(width, errors)

    # Expected clean value
    flat = sim._to_flat(row, col)
    expected = inject_error(sim.grid[flat], bit)

    print(f"    Grid: {sim.rows}x{sim.cols}")
    print(f"    Error at ({row},{col}) bit {bit}")
    print(f"    Before: 0x{sim.grid[flat]:04x}, expected after: 0x{expected:04x}")

    # IX must scan through bypass+handler+code+border rows to reach any cell.
    # Each cell takes ~1 gadget cycle.  Most cells are clean (shorter cycle).
    # One full down-sweep covers all cells; use dirty cycle as upper bound.
    R = layout['code_rows']
    scan_rows = R + 2  # bypass + handler + code
    scan_cols = layout['code_right'] - layout['code_left'] + 1
    cells_per_sweep = scan_rows * scan_cols
    n_steps = cells_per_sweep * cycle_length
    print(f"    Running {n_steps} steps ({cells_per_sweep} cells × {cycle_length} steps/cell)")

    for _ in range(n_steps):
        sim.step_all()
        _cheat_clear_waste(sim, layout)

    result = sim.grid[flat]
    ok = (result == expected)
    print(f"    After {n_steps} steps: 0x{result:04x} expected 0x{expected:04x}"
          f" {'ok' if ok else 'FAIL'}")
    return ok


if __name__ == '__main__':
    width = 99
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

    if all_ok:
        # Generate loadable state file
        sim, layout, cycle_length = make_probe_bypass_ouroboros(width)
        out_dir = os.path.dirname(os.path.abspath(__file__))
        out_path = os.path.join(out_dir, f'immunity-gadgets-v4-loop-w{width}.fb2d')
        sim.save_state(out_path, hints={'waste_cleanup': 1})
        print(f"Saved: {out_path}")
        print("=" * 60)
        print(f"ALL PROBE-BYPASS TESTS PASSED (W={width})")
        print("=" * 60)
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

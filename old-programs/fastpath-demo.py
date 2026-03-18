#!/usr/bin/env python3
"""
fastpath-demo.py — Fast-path parity skip for Hamming(16,11) correction.

When scanning cells, ~95% are typically clean (no errors). The standard
correction gadget runs all 323 ops per cell regardless. The fast-path
optimization checks overall parity first (~73 ops) and skips the expensive
correction phases (~252 ops) when the cell is clean.

ARCHITECTURE:
  The gadget is split into:
    - PARITY PREFIX (73 ops): m + Phase A + B + A' + rotate EV to data bit
    - FAST CLEANUP (3 ops): E E m — return H0 to CWL, uncompute
    - CORRECTION (252 ops): r r r + Phase C + D + C' + uncompute + writeback
                            + cleanup + epilogue
    - ADVANCE (5 ops): E e ] > a — advance all heads east

  Branch: after prefix, EX points at EV which has p_all in bit 3 (rotated
  from bit 0 so it's visible to EX-conditional mirrors via DATA_MASK).
    - [EX] payload=0 (clean): # mirror triggers → fast path
    - [EX] payload≠0 (error): # mirror passes through → correction

LAYOUT (two independent rows, no merge):
  Row 0: REMOTE — codewords (IX scans here)
  Row 1: BYPASS — fast path with own advance (entered via # branch)
  Row 2: MAIN CODE — prefix + # + correction + advance
  Row 3: EX — scratch cells

  Fast path: IP goes N from #, enters Row 1, does fast cleanup + advance,
  continues through NOPs.
  Slow path: IP passes through #, does correction + advance, continues
  through NOPs.

  Each path has its own advance ops. No merge/rejoin needed.

Run tests:  python3 programs/fastpath-demo.py
"""

import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  encode_opcode, OPCODE_PAYLOADS)

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, decode

# ── Sliding slot offsets (same as dual-gadget-demo.py) ──
DSL_EV   = 0
DSL_PA   = 1
DSL_CWL  = 2
DSL_S0   = 3
DSL_S1   = 4
DSL_S2   = 5
DSL_S3   = 6
DSL_SCR  = 7
DSL_ROT  = 8
DSL_SI   = [DSL_S0, DSL_S1, DSL_S2, DSL_S3]

SYNDROME_POSITIONS = [
    [1, 3, 5, 7, 9, 11, 13, 15],
    [2, 3, 6, 7, 10, 11, 14, 15],
    [4, 5, 6, 7, 12, 13, 14, 15],
    [8, 9, 10, 11, 12, 13, 14, 15],
]

REMOTE_ROW = 0
BYPASS_ROW = 1
CODE_ROW   = 2
EX_ROW     = 3
N_ROWS     = 4


class GadgetBuilder:
    """Build opcode sequences tracking head positions on EX row."""

    def __init__(self, h0_col=DSL_CWL, h1_col=DSL_CWL,
                 cl_col=DSL_ROT, cl_payload=0, gp_col=DSL_EV):
        self.ops = []
        self.cursor = 0
        self.h0_col = h0_col
        self.h1_col = h1_col
        self.cl_col = cl_col
        self.cl_payload = cl_payload
        self.gp_col = gp_col

    def emit(self, opchar):
        self.ops.append(opchar)
        self.cursor += 1

    def emit_n(self, opchar, n):
        for _ in range(n):
            self.emit(opchar)

    def pos(self):
        return self.cursor

    def move_h0_col(self, target_col):
        diff = target_col - self.h0_col
        for _ in range(abs(diff)):
            self.emit('E' if diff > 0 else 'W')
        self.h0_col = target_col

    def move_h1_col(self, target_col):
        diff = target_col - self.h1_col
        for _ in range(abs(diff)):
            self.emit('e' if diff > 0 else 'w')
        self.h1_col = target_col

    def move_cl_col(self, target_col):
        diff = target_col - self.cl_col
        for _ in range(abs(diff)):
            self.emit('>' if diff > 0 else '<')
        self.cl_col = target_col
        self.cl_payload = None

    def set_cl_payload(self, target):
        assert self.cl_payload is not None
        diff = target - self.cl_payload
        if diff > 0:
            self.emit_n(':', diff)
        elif diff < 0:
            self.emit_n(';', -diff)
        self.cl_payload = target

    def xor_accumulate_bits(self, bit_positions):
        for bit_pos in bit_positions:
            self.set_cl_payload(bit_pos)
            self.emit('Y')


# ═══════════════════════════════════════════════════════════════════
# Gadget builders
# ═══════════════════════════════════════════════════════════════════

def build_prefix():
    """Build the parity-check prefix (common to fast and slow paths).

    After prefix:
      H0 = EV(0), H1 = CWL(2), CL = ROT(8) payload=0, EX = EV(0)
      [EV] has p_all in bit 3 (rotated from bit 0 so DATA_MASK sees it)

    Returns: list of opchar strings
    """
    gb = GadgetBuilder()
    gb.emit('m')  # copy-in

    # Phase A: Overall parity via Y (CL 0→15)
    gb.move_h0_col(DSL_PA)
    gb.xor_accumulate_bits(list(range(16)))

    # Phase B: z-extract PA.bit0 → EV.bit0
    gb.move_h0_col(DSL_EV)
    gb.move_h1_col(DSL_PA)
    gb.emit('z')
    gb.move_h1_col(DSL_CWL)

    # Phase A': Y-uncompute PA (CL 15→0)
    gb.move_h0_col(DSL_PA)
    gb.xor_accumulate_bits(list(range(15, -1, -1)))

    # Rotate EV left 3: bit0 → bit3 (data position visible to EX mirrors)
    gb.move_h0_col(DSL_EV)
    gb.emit_n('l', 3)

    return gb.ops


def build_correction():
    """Build the correction suffix (slow path only).

    Entry state: H0=EV(0), H1=CWL(2), CL=ROT(8) payload=0, EX=EV(0)
                 [EV] has p_all in bit3 (rotated by prefix)
    Exit state:  H0=CWL(2), H1=CWL(2), CL=ROT(8) payload=0, EX=EV(0)

    Returns: list of opchar strings
    """
    gb = GadgetBuilder(h0_col=DSL_EV, h1_col=DSL_CWL,
                       cl_col=DSL_ROT, cl_payload=0, gp_col=DSL_EV)

    # Undo rotation: r r r restores p_all from bit3 to bit0
    gb.emit_n('r', 3)

    # Phase C: Syndrome via Y
    gb.move_h0_col(DSL_S0)
    gb.xor_accumulate_bits(SYNDROME_POSITIONS[0])
    gb.move_h0_col(DSL_S1)
    gb.xor_accumulate_bits([15, 14, 11, 10, 7, 6, 3, 2])
    gb.move_h0_col(DSL_S2)
    gb.xor_accumulate_bits([4, 5, 6, 7, 12, 13, 14, 15])
    gb.move_h0_col(DSL_S3)
    gb.xor_accumulate_bits([15, 14, 13, 12, 11, 10, 9, 8])

    # Phase D: Barrel shifter
    gb.move_h0_col(DSL_EV)
    gb.move_h1_col(DSL_SCR)
    gb.move_cl_col(DSL_S0)
    for i in range(4):
        if i > 0:
            gb.move_cl_col(DSL_SI[i])
        shift = 1 << i
        gb.emit_n('l', shift)
        gb.emit('f')
        gb.emit_n('r', shift)
        gb.emit('f')

    # Phase C': Y-uncompute S0-S3
    gb.move_h0_col(DSL_S3)
    gb.move_h1_col(DSL_CWL)
    gb.move_cl_col(DSL_ROT)
    gb.cl_payload = 8
    gb.xor_accumulate_bits([8, 9, 10, 11, 12, 13, 14, 15])
    gb.move_h0_col(DSL_S2)
    gb.xor_accumulate_bits([15, 14, 13, 12, 7, 6, 5, 4])
    gb.move_h0_col(DSL_S1)
    gb.xor_accumulate_bits([2, 3, 6, 7, 10, 11, 14, 15])
    gb.move_h0_col(DSL_S0)
    gb.xor_accumulate_bits([15, 13, 11, 9, 7, 5, 3, 1])
    gb.set_cl_payload(0)

    # Uncompute local copy
    gb.move_h0_col(DSL_CWL)
    gb.emit('m')

    # Write correction to remote
    gb.move_h0_col(DSL_EV)
    gb.emit('j')

    # Cleanup z+x
    gb.move_h1_col(DSL_PA)
    gb.emit('z')
    gb.emit('x')

    # Epilogue: return H0, H1 to CWL
    gb.move_h0_col(DSL_CWL)
    gb.move_h1_col(DSL_CWL)

    return gb.ops


def build_fast_cleanup():
    """Build the fast-path cleanup (no correction needed).

    Entry: H0=EV(0), H1=CWL(2), CL=ROT(8) payload=0, EX=EV(0), [EV]=0
    Exit:  H0=CWL(2), H1=CWL(2), CL=ROT(8) payload=0, EX=EV(0)

    Returns: list of opchar strings
    """
    return ['E', 'E', 'm']  # H0: EV(0)→PA(1)→CWL(2), then uncompute CWL


ADVANCE_OPS = ['E', 'e', ']', '>', 'a']


# ═══════════════════════════════════════════════════════════════════
# Grid layout
# ═══════════════════════════════════════════════════════════════════

def make_fastpath_torus(cases, first_cw_col=4):
    """Build a 4-row test grid with fast-path bypass.

    Layout:
      Row 0: REMOTE — codewords
      Row 1: BYPASS — / entry + fast cleanup + advance + NOPs
      Row 2: MAIN CODE — prefix + # branch + correction + advance + NOPs
      Row 3: EX — scratch cells

    No rejoin needed: each path has its own advance.

    Returns: (sim, expected, layout_info)
    """
    prefix_ops = build_prefix()
    correction_ops = build_correction()
    fast_ops = build_fast_cleanup()

    n_prefix = len(prefix_ops)
    n_correction = len(correction_ops)
    n_fast = len(fast_ops)
    n_advance = len(ADVANCE_OPS)

    # CODE_ROW layout:
    #   [0..n_prefix-1]: prefix
    #   [n_prefix]: # branch mirror
    #   [n_prefix+1 .. +n_correction]: correction ops
    #   [n_prefix+1+n_correction .. +n_advance-1]: advance
    branch_col = n_prefix
    slow_advance_start = branch_col + 1 + n_correction
    slow_advance_end = slow_advance_start + n_advance - 1

    # BYPASS_ROW layout:
    #   [branch_col]: / entry mirror (N→E)
    #   [branch_col+1 .. +n_fast]: fast cleanup
    #   [branch_col+1+n_fast .. +n_advance-1]: advance
    fast_advance_start = branch_col + 1 + n_fast
    fast_advance_end = fast_advance_start + n_advance - 1

    n = len(cases)
    ex_start_col = first_cw_col - DSL_CWL
    max_gp_col = ex_start_col + DSL_ROT + n
    cols = max(slow_advance_end + 2, first_cw_col + n + 2, max_gp_col + 2)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # ── Place prefix on CODE_ROW ──
    for i, ch in enumerate(prefix_ops):
        sim.grid[sim._to_flat(CODE_ROW, i)] = encode_opcode(OP[ch])

    # ── Branch mirror: # (/ if [EX]==0) ──
    sim.grid[sim._to_flat(CODE_ROW, branch_col)] = encode_opcode(OP['#'])

    # ── Correction on CODE_ROW ──
    for i, ch in enumerate(correction_ops):
        sim.grid[sim._to_flat(CODE_ROW, branch_col + 1 + i)] = encode_opcode(OP[ch])

    # ── Slow-path advance on CODE_ROW ──
    for i, ch in enumerate(ADVANCE_OPS):
        sim.grid[sim._to_flat(CODE_ROW, slow_advance_start + i)] = encode_opcode(OP[ch])

    # ── BYPASS ROW ──
    # Entry: / at (1, branch_col) — turns N→E
    sim.grid[sim._to_flat(BYPASS_ROW, branch_col)] = encode_opcode(OP['/'])
    # Fast cleanup
    for i, ch in enumerate(fast_ops):
        sim.grid[sim._to_flat(BYPASS_ROW, branch_col + 1 + i)] = encode_opcode(OP[ch])
    # Fast-path advance
    for i, ch in enumerate(ADVANCE_OPS):
        sim.grid[sim._to_flat(BYPASS_ROW, fast_advance_start + i)] = encode_opcode(OP[ch])

    # ── Codewords on REMOTE_ROW ──
    expected = []
    for i, (payload, error_bit) in enumerate(cases):
        cw = encode(payload)
        bad = inject_error(cw, error_bit) if error_bit is not None else cw
        expected.append(cw)
        sim.grid[sim._to_flat(REMOTE_ROW, first_cw_col + i)] = bad

    # ── Initial head positions ──
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East

    sim.h0 = sim._to_flat(EX_ROW, ex_start_col + DSL_CWL)
    sim.h1 = sim._to_flat(EX_ROW, ex_start_col + DSL_CWL)
    sim.ix = sim._to_flat(REMOTE_ROW, first_cw_col)
    sim.cl = sim._to_flat(EX_ROW, ex_start_col + DSL_ROT)
    sim.ex = sim._to_flat(EX_ROW, ex_start_col + DSL_EV)

    layout_info = {
        'n_prefix': n_prefix,
        'n_correction': n_correction,
        'n_fast': n_fast,
        'n_advance': n_advance,
        'branch_col': branch_col,
        'slow_advance_end': slow_advance_end,
        'fast_advance_end': fast_advance_end,
        'cols': cols,
        'ex_start_col': ex_start_col,
        'first_cw_col': first_cw_col,
        'fast_cycle_steps': branch_col + 1 + n_fast + n_advance,
        'slow_cycle_steps': branch_col + 1 + n_correction + n_advance,
    }

    return sim, expected, layout_info


# ═══════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════

def run_fastpath_test(cases, verbose=True, check_reverse=True):
    """Test fast-path correction on a sequence of codewords.

    Returns: bool (all tests passed)
    """
    n = len(cases)
    first_cw_col = 4
    sim, expected, info = make_fastpath_torus(cases, first_cw_col=first_cw_col)

    if verbose:
        print(f"    Grid: {sim.rows}×{sim.cols}")
        print(f"    Prefix: {info['n_prefix']} ops, "
              f"Correction: {info['n_correction']} ops, "
              f"Fast: {info['n_fast']} ops")
        print(f"    Fast cycle: {info['fast_cycle_steps']} ops, "
              f"Slow cycle: {info['slow_cycle_steps']} ops")

    all_ok = True
    total_fast = 0
    total_slow = 0
    all_steps = []

    for cw_idx in range(n):
        payload, error_bit = cases[cw_idx]
        visited_bypass = False
        step_count = 0

        # Reset IP to start of prefix for each cell.
        # Heads are already advanced by previous cycle's advance ops
        # (or at initial positions for the first cell).
        if cw_idx > 0:
            sim.ip_row = CODE_ROW
            sim.ip_col = 0
            sim.ip_dir = 1  # East

        # Run until the IP passes the advance section on either row.
        while True:
            sim.step()
            step_count += 1

            if sim.ip_row == BYPASS_ROW:
                visited_bypass = True

            # Detect: IP on CODE_ROW past slow advance, or
            #         IP on BYPASS_ROW past fast advance
            if (sim.ip_row == CODE_ROW and
                sim.ip_col > info['slow_advance_end'] and
                sim.ip_dir == 1):
                break
            if (sim.ip_row == BYPASS_ROW and
                sim.ip_col > info['fast_advance_end'] and
                sim.ip_dir == 1):
                break

            if step_count > 600:
                if verbose:
                    print(f"    CW[{cw_idx}]: TIMEOUT at ({sim.ip_row},{sim.ip_col})")
                all_ok = False
                break

        err_desc = f"bit {error_bit}" if error_bit is not None else "none"
        expected_fast = (error_bit is None)
        path_ok = (visited_bypass == expected_fast)

        if visited_bypass:
            total_fast += 1
        else:
            total_slow += 1

        data_col = first_cw_col + cw_idx
        result = sim.grid[sim._to_flat(REMOTE_ROW, data_col)]
        correct = (result == expected[cw_idx])
        all_steps.append(step_count)

        if verbose or not correct or not path_ok:
            path_name = "FAST" if visited_bypass else "SLOW"
            status = 'ok' if (correct and path_ok) else 'FAIL'
            print(f"    CW[{cw_idx}] payload={payload} err={err_desc}: "
                  f"{path_name} ({step_count} steps) "
                  f"0x{result:04x}→0x{expected[cw_idx]:04x} {status}")
            if not path_ok:
                print(f"      Expected {'FAST' if expected_fast else 'SLOW'}")

        all_ok &= correct & path_ok

    if verbose:
        print(f"    Summary: {total_fast} fast, {total_slow} slow")

    # Reverse check (single-cell only — multi-cell uses IP teleport which
    # isn't reversible, so we skip reverse for n>1)
    if check_reverse and n == 1:
        sim2, _, _ = make_fastpath_torus(cases, first_cw_col=first_cw_col)
        total_steps = 0
        steps = 0
        while True:
            sim2.step()
            steps += 1
            total_steps += 1
            if (sim2.ip_row == CODE_ROW and
                sim2.ip_col > info['slow_advance_end'] and
                sim2.ip_dir == 1):
                break
            if (sim2.ip_row == BYPASS_ROW and
                sim2.ip_col > info['fast_advance_end'] and
                sim2.ip_dir == 1):
                break
            if steps > 600:
                break

        for _ in range(total_steps):
            sim2.step_back()

        reverse_ok = True
        for i in range(n):
            data_col = first_cw_col + i
            payload, error_bit = cases[i]
            cw = encode(payload)
            orig = inject_error(cw, error_bit) if error_bit is not None else cw
            result = sim2.grid[sim2._to_flat(REMOTE_ROW, data_col)]
            if result != orig:
                reverse_ok = False
                if verbose:
                    print(f"    [REVERSE] CW[{i}]: 0x{result:04x}"
                          f" != expected 0x{orig:04x}")

        for col in range(sim2.cols):
            v = sim2.grid[sim2._to_flat(EX_ROW, col)]
            if v != 0:
                reverse_ok = False
                if verbose:
                    print(f"    [REVERSE] EX col {col}: 0x{v:04x} != 0")
                break

        if verbose:
            print(f"    Reverse ({total_steps} steps): "
                  f"{'ok' if reverse_ok else 'FAIL'}")
        all_ok &= reverse_ok

    return all_ok, all_steps


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

def test_clean_cell():
    """Clean cell → fast path."""
    print("=== Fast-path: clean cell ===")
    ok, _ = run_fastpath_test([(42, None)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_single_error():
    """Single-bit error → slow path."""
    print("=== Fast-path: single error (bit 3) ===")
    ok, _ = run_fastpath_test([(42, 3)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_bit0_error():
    """Bit-0 error → slow path."""
    print("=== Fast-path: bit-0 error ===")
    ok, _ = run_fastpath_test([(42, 0)])
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_mixed():
    """Mix of clean and corrupted cells."""
    print("=== Fast-path: mixed clean/corrupted ===")
    cases = [
        (42, None),    # clean → fast
        (100, 7),      # error → slow
        (0, None),     # clean → fast
        (2047, 15),    # error → slow
        (1, None),     # clean → fast
        (500, 0),      # bit-0 error → slow
    ]
    ok, _ = run_fastpath_test(cases)
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_all_clean():
    """All clean cells — all fast."""
    print("=== Fast-path: all clean (10 cells) ===")
    cases = [(i * 100, None) for i in range(10)]
    ok, _ = run_fastpath_test(cases, verbose=False)
    print(f"  All 10 clean: {'PASS' if ok else 'FAIL'}")
    return ok


def test_all_errors():
    """All 16 error positions — all slow."""
    print("=== Fast-path: all 16 error positions ===")
    cases = [(42, bit) for bit in range(16)]
    ok, steps = run_fastpath_test(cases, verbose=False)
    if not ok:
        run_fastpath_test(cases, verbose=True)
    print(f"  All 16 positions: {'PASS' if ok else 'FAIL'}")
    return ok


def test_random():
    """Random mix."""
    print("=== Fast-path: random (20 cells) ===")
    random.seed(42)
    cases = []
    for _ in range(20):
        payload = random.randint(0, 2047)
        if random.random() < 0.5:
            cases.append((payload, None))
        else:
            cases.append((payload, random.randint(0, 15)))
    ok, _ = run_fastpath_test(cases, verbose=False)
    n_clean = sum(1 for _, e in cases if e is None)
    n_error = len(cases) - n_clean
    print(f"  {n_clean} clean + {n_error} errors: {'PASS' if ok else 'FAIL'}")
    return ok


def test_performance():
    """Compare step counts: fast-path vs standard gadget."""
    print("=== Performance comparison ===")

    import importlib.util
    dgd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'dual-gadget-demo.py')
    spec = importlib.util.spec_from_file_location('dgd', dgd_path)
    dgd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dgd)

    # 90% clean, 10% errors
    random.seed(123)
    n = 20
    cases = []
    for _ in range(n):
        payload = random.randint(0, 2047)
        if random.random() < 0.1:
            cases.append((payload, random.randint(0, 15)))
        else:
            cases.append((payload, None))

    n_clean = sum(1 for _, e in cases if e is None)
    n_error = n - n_clean

    # Fast-path
    ok, steps = run_fastpath_test(cases, verbose=False, check_reverse=False)
    fast_total = sum(steps)

    # Standard
    std_cycle = len(dgd.build_h2_correction_gadget())
    # Standard gadget: each cycle traverses the full torus row width
    std_sim, _, std_cycle_len, _ = dgd.make_h2_test_torus(
        cases, first_cw_col=4)
    std_total = n * std_cycle_len

    fast_clean_steps = [s for i, s in enumerate(steps) if cases[i][1] is None]
    fast_error_steps = [s for i, s in enumerate(steps) if cases[i][1] is not None]

    print(f"    Cells: {n} ({n_clean} clean, {n_error} errors)")
    print(f"    Standard: {std_cycle_len} steps/cell × {n} = {std_total} total")
    print(f"    Fast-path: {fast_total} total (avg {fast_total/n:.1f}/cell)")
    if fast_clean_steps:
        avg_fast = sum(fast_clean_steps) / len(fast_clean_steps)
        print(f"    Clean avg: {avg_fast:.1f} steps "
              f"({avg_fast/std_cycle_len*100:.0f}% of standard)")
    if fast_error_steps:
        avg_slow = sum(fast_error_steps) / len(fast_error_steps)
        print(f"    Error avg: {avg_slow:.1f} steps")
    if fast_total > 0:
        speedup = std_total / fast_total
        print(f"    Speedup: {speedup:.2f}×")

    return True


# ═══════════════════════════════════════════════════════════════════
# .fb2d state file generation
# ═══════════════════════════════════════════════════════════════════

def save_fb2d(sim, filename):
    """Save simulator state as a .fb2d file for the interactive REPL."""
    programs_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(programs_dir, filename)
    sim.save_state(path)
    print(f"  Saved: {filename}")


def generate_fb2d_files():
    """Generate .fb2d state files for interactive exploration."""
    print("=== Generating .fb2d state files ===")

    # 1. Clean cell → fast path (payload=42, no error)
    sim_clean, _, info = make_fastpath_torus([(42, None)])
    save_fb2d(sim_clean, 'fastpath-clean.fb2d')

    # 2. Single-bit error → slow path (payload=42, bit 5 error)
    sim_err, _, _ = make_fastpath_torus([(42, 5)])
    save_fb2d(sim_err, 'fastpath-err5.fb2d')

    # 3. Mixed: 3 clean + 3 corrupted for watching branching behavior
    mixed_cases = [
        (42, None),    # clean → fast
        (100, 7),      # error → slow
        (0, None),     # clean → fast
        (2047, 15),    # error → slow
        (1, None),     # clean → fast
        (500, 0),      # bit-0 error → slow
    ]
    sim_mixed, _, _ = make_fastpath_torus(mixed_cases)
    save_fb2d(sim_mixed, 'fastpath-mixed.fb2d')

    print(f"  Layout: Row 0=REMOTE (codewords), Row 1=BYPASS (fast path),")
    print(f"          Row 2=CODE (prefix + # + correction), Row 3=EX (scratch)")
    print(f"  Prefix={info['n_prefix']} ops, branch at col {info['branch_col']}")
    print(f"  Fast cycle: {info['fast_cycle_steps']} steps, "
          f"Slow cycle: {info['slow_cycle_steps']} steps")
    print(f"  Try: python3 fb2d.py → load fastpath-clean")
    print(f"       Step ~83 times to see clean cell take fast path (row 1)")
    print(f"       load fastpath-err5")
    print(f"       Step ~332 times to see error cell take slow path (row 2)")


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    prefix = build_prefix()
    correction = build_correction()
    fast = build_fast_cleanup()
    print(f"Gadget split: prefix={len(prefix)}, "
          f"correction={len(correction)}, fast={len(fast)}")
    print(f"Standard total: {len(prefix) + len(correction) + len(ADVANCE_OPS)} ops")
    print(f"Fast-path total: {len(prefix) + len(fast) + len(ADVANCE_OPS)} ops "
          f"({len(prefix)+len(fast)+len(ADVANCE_OPS)}/{len(prefix)+len(correction)+len(ADVANCE_OPS)} "
          f"= {(len(prefix)+len(fast)+len(ADVANCE_OPS))/(len(prefix)+len(correction)+len(ADVANCE_OPS))*100:.0f}%)")
    print()

    all_ok = True
    all_ok &= test_clean_cell()
    print()
    all_ok &= test_single_error()
    print()
    all_ok &= test_bit0_error()
    print()
    all_ok &= test_mixed()
    print()
    all_ok &= test_all_clean()
    print()
    all_ok &= test_all_errors()
    print()
    all_ok &= test_random()
    print()
    all_ok &= test_performance()
    print()

    if all_ok:
        print("=" * 60)
        print("ALL FAST-PATH TESTS PASSED")
        print("=" * 60)
        print()
        generate_fb2d_files()
    else:
        print("=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)

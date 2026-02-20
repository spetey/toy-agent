#!/usr/bin/env python3
"""
hamming-gadget-demo.py — SECDED Hamming(8,4) as a spatial fb2d gadget.

Full spatial program on a toroidal grid with while-loop pattern and
corridor-based IP routing. Computes syndrome AND corrects in one
forward pass, with full step_back() reversibility.

ARCHITECTURE (5-row torus):

  Row 0 (DATA):      CW  S0  S1  S2  PA  FIX SYND CNTF CNTB MASK
                       0   1   2   3   4   5    6    7    8    9
  Row 1 (CORR_FWD):  forward rotation loop body (/ [body→W] \\)
  Row 2 (CORR_BWD):  backward rotation loop body (/ [body→W] \\)
  Row 3 (CODE):      [opcodes left-to-right ...]
  Row 4 (GP):        0 0 0 ...

WHILE-LOOP PATTERN (zero-iteration safe):

  Each loop uses the pattern:
       / [body ops, right-to-left] \\    ← corridor row
       ( P  [NOP padding]           %    ← code row

  Flow:
    First entry: IP going E hits (. [GP]=0 → passes through.
      P increments GP. IP walks E through NOPs to %.
      If [CL]=0: passes through E. Loop exits. Body never ran.
      If [CL]!=0: % (/-reflect) sends E→N. IP rises to \\ on corridor.
        \\ reflects N→W. IP walks W through body (executes body ops).
        / reflects W→S. IP drops to (. [GP]!=0 → ( (\\-reflect) S→E.
        P increments GP again. IP walks E to %. Repeat check.

  This gives true while(CL!=0) behavior — zero iterations when CL=0.
  Cost: one GP byte per loop (the initial P before first % check).

ALGORITHM PHASES (all on CODE row, IP walks East):

  Phase 1 — SYNDROME (~300 ops, straight-line):
    s2: XOR bits {4,5,6,7} → S2    (rotate-XOR-unrotate × 4)
    s1: XOR bits {2,3,6,7} → S1
    s0: XOR bits {1,3,5,7} → S0

  Phase 2 — SYNDROME ASSEMBLY (~30 ops):
    z-extract bit 0 of S2, S1, S0 into SYND cell.
    Result: SYND = (s2<<2)|(s1<<1)|s0, clean value 0–7.

  Phase 3 — OVERALL PARITY (~100 ops):
    XOR all 8 bits of CW into PA via rotate-XOR-unrotate.

  Phase 4 — CONDITIONAL COUNTER SETUP (~10 ops):
    f-gate (CL on PA): swap CNTF↔SYND if p_all=1.
    Copy: CNTB += CNTF.
    Result: CNTF=CNTB=syndrome if p_all=1, else both 0.

  Phase 5 — FORWARD ROTATION LOOP:
    While CNTF>0: rotate CW right 1, decrement CNTF.
    Body is on corridor row (CORR_FWD), traversed going West.

  Phase 6 — CONDITIONAL BIT FLIP:
    f MASK↔FIX (gated on PA), x CW^=MASK, f restore.
    Flips bit 0 of CW when p_all=1. No-op when p_all=0.
    For syndrome>0: CW is rotated, so bit 0 = the target bit.
    For syndrome=0 + p_all=1 (bit 0 error): flips bit 0 directly.
    For no error or double error: MASK=0, no change.

  Phase 7 — BACKWARD ROTATION LOOP:
    While CNTB>0: rotate CW left 1, decrement CNTB.
    Body is on corridor row (CORR_BWD), traversed going West.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

from hamming import encode, inject_error, inject_double_error, decode

# ── Data cell columns (row 0) ──
CW   = 0   # codeword
S0   = 1   # syndrome bit 0 accumulator
S1   = 2   # syndrome bit 1 accumulator
S2   = 3   # syndrome bit 2 accumulator
PA   = 4   # overall parity accumulator
FIX  = 5   # constant 1
SYND = 6   # assembled syndrome (0-7)
CNTF = 7   # forward rotation counter
CNTB = 8   # backward rotation counter
MASK = 9   # conditional XOR mask

DATA_ROW  = 0
CORR_FWD  = 1   # forward loop body corridor
CORR_BWD  = 2   # backward loop body corridor
CODE_ROW  = 3
GP_ROW    = 4
N_ROWS    = 5
N_DATA    = 10


class GadgetBuilder:
    """Build an opcode sequence and track head positions.

    Supports emitting ops, emitting NOP spacers (for loop body gaps on
    the code row), and building corridor body ops separately.
    """

    def __init__(self):
        self.ops = []           # (col, opchar) pairs for CODE_ROW
        self.cursor = 0         # current column on CODE_ROW
        self.h0_col = CW
        self.h1_col = CW
        self.cl_col = CW
        self.corridors = {}     # name → list of (col, opchar) pairs

    def emit(self, opchar):
        """Emit an opcode at the current cursor position."""
        self.ops.append((self.cursor, opchar))
        self.cursor += 1

    def skip(self, n):
        """Skip n columns (NOP padding on code row)."""
        self.cursor += n

    def pos(self):
        """Current cursor column."""
        return self.cursor

    def move_h0(self, target):
        diff = target - self.h0_col
        for _ in range(abs(diff)):
            self.emit('E' if diff > 0 else 'W')
        self.h0_col = target

    def move_h1(self, target):
        diff = target - self.h1_col
        for _ in range(abs(diff)):
            self.emit('e' if diff > 0 else 'w')
        self.h1_col = target

    def move_cl(self, target):
        diff = target - self.cl_col
        for _ in range(abs(diff)):
            self.emit('>' if diff > 0 else '<')
        self.cl_col = target

    def rotate_right(self, n):
        for _ in range(n):
            self.emit('r')

    def rotate_left(self, n):
        for _ in range(n):
            self.emit('l')

    def xor_accumulate(self, target_col, source_col, bit_positions):
        """XOR specific bit positions of source into target via
        rotate-XOR-unrotate. H0 starts and ends at source_col."""
        for bit_pos in bit_positions:
            assert self.h0_col == source_col
            self.rotate_right(bit_pos)
            self.move_h0(target_col)
            self.move_h1(source_col)
            self.emit('x')
            self.move_h0(source_col)
            self.move_h1(target_col)
            self.rotate_left(bit_pos)

    def build_loop_body(self, name, entry_col, exit_col, body_ops):
        """Register corridor body ops for a while-loop.

        body_ops: list of opchar in EXECUTION order.
        These will be placed right-to-left on the corridor row between
        entry_col and exit_col, so that the IP traversing West executes
        them in the given order.

        The corridor gets / at entry_col and \\ at exit_col.
        Body ops fill columns (exit_col-1) down to (entry_col+1), going W.
        """
        available = exit_col - entry_col - 1
        assert len(body_ops) <= available, \
            f"Loop body {name}: {len(body_ops)} ops but only {available} slots"

        corridor_ops = []
        corridor_ops.append((entry_col, '/'))
        corridor_ops.append((exit_col, '\\'))
        # Body ops: first to execute is at exit_col-1, last at entry_col+1
        for i, op in enumerate(body_ops):
            col = exit_col - 1 - i
            corridor_ops.append((col, op))

        self.corridors[name] = corridor_ops


def build_gadget():
    """Build the complete Hamming SECDED gadget.

    Returns: (code_ops, corridors, mirrors, total_cols)
      code_ops: list of (col, opchar) for CODE_ROW
      corridors: dict of name → list of (col, opchar) for corridor rows
      mirrors: dict of named positions
      total_cols: total columns needed
    """
    gb = GadgetBuilder()
    mirrors = {}

    # ── Phase 1: Syndrome computation ──
    gb.move_h1(S2)
    gb.xor_accumulate(S2, CW, [4, 5, 6, 7])

    gb.move_h1(S1)
    gb.xor_accumulate(S1, CW, [2, 3, 6, 7])

    gb.move_h1(S0)
    gb.xor_accumulate(S0, CW, [1, 3, 5, 7])

    # ── Phase 2: Syndrome assembly via z-extraction ──
    # Move GP from (GP_ROW, 0) to (DATA_ROW, SYND) for z ops
    for _ in range(4):      # GP north ×4: row 4→0
        gb.emit('{')
    for _ in range(SYND):   # GP east to col 6
        gb.emit(']')

    gb.move_h0(S2)
    gb.emit('z')            # SYND bit0 = s2

    gb.move_h0(SYND)
    gb.emit('l')            # rotate SYND left: s2 → bit 1
    gb.move_h0(S1)
    gb.emit('z')            # SYND bit0 = s1

    gb.move_h0(SYND)
    gb.emit('l')            # s2 → bit 2, s1 → bit 1
    gb.move_h0(S0)
    gb.emit('z')            # SYND bit0 = s0

    # SYND = (s2<<2)|(s1<<1)|s0, clean 0-7

    # Move GP back to (GP_ROW, 0)
    for _ in range(SYND):
        gb.emit('[')
    for _ in range(4):
        gb.emit('}')

    # ── Phase 3: Overall parity ──
    gb.move_h0(CW)
    gb.move_h1(PA)
    gb.xor_accumulate(PA, CW, list(range(8)))

    # ── Phase 4: Conditional counter setup ──
    # f-gate: CNTF↔SYND when p_all=1. Copy to CNTB.
    # Result: CNTF=CNTB=syndrome if p_all=1, else both 0.
    gb.move_cl(PA)
    gb.move_h0(CNTF)
    gb.move_h1(SYND)
    gb.emit('f')            # p_all=1: CNTF↔SYND → CNTF=syndrome, SYND=0
                             # p_all=0: no swap   → CNTF=0, SYND=syndrome

    gb.move_h0(CNTB)
    gb.move_h1(CNTF)
    gb.emit('.')            # CNTB += CNTF

    # ── Phase 5: Forward rotation while-loop ──
    # While CNTF>0: rotate CW right 1, decrement CNTF.
    # Body on corridor CORR_FWD. Code row: ] ( P [nops] %

    # Forward loop body (execution order):
    #   r (rotate CW right), E×7 (H0 to CNTF), - (decrement), W×7 (H0 back to CW)
    fwd_body = ['r'] + ['E'] * (CNTF - CW) + ['-'] + ['W'] * (CNTF - CW)
    fwd_body_len = len(fwd_body)  # 1 + 7 + 1 + 7 = 16

    gb.move_cl(CNTF)
    gb.move_h0(CW)
    gb.emit(']')            # GP east to fresh zero
    fwd_entry_col = gb.pos()
    gb.emit('(')
    gb.emit('P')
    # NOP padding for body width
    gb.skip(fwd_body_len)
    fwd_exit_col = gb.pos()
    gb.emit('%')            # [CL]=[CNTF]: exit when 0

    gb.build_loop_body('fwd', fwd_entry_col, fwd_exit_col, fwd_body)
    mirrors['fwd_entry'] = fwd_entry_col
    mirrors['fwd_exit'] = fwd_exit_col

    # ── Phase 6: Conditional bit flip ──
    # XOR CW with 1 if p_all=1 (via MASK). No-op if p_all=0.
    gb.move_cl(PA)
    gb.move_h0(MASK)
    gb.move_h1(FIX)
    gb.emit('f')            # p_all=1: MASK↔FIX → MASK=1, FIX=0
    gb.move_h0(CW)
    gb.move_h1(MASK)
    gb.emit('x')            # CW ^= MASK (flips bit 0 if MASK=1)
    gb.move_h0(MASK)
    gb.move_h1(FIX)
    gb.emit('f')            # restore: MASK=0, FIX=1

    # ── Phase 7: Backward rotation while-loop ──
    # While CNTB>0: rotate CW left 1, decrement CNTB.

    bwd_body = ['l'] + ['E'] * (CNTB - CW) + ['-'] + ['W'] * (CNTB - CW)
    bwd_body_len = len(bwd_body)  # 1 + 8 + 1 + 8 = 18

    gb.move_cl(CNTB)
    gb.move_h0(CW)
    gb.emit(']')
    bwd_entry_col = gb.pos()
    gb.emit('(')
    gb.emit('P')
    gb.skip(bwd_body_len)
    bwd_exit_col = gb.pos()
    gb.emit('%')

    gb.build_loop_body('bwd', bwd_entry_col, bwd_exit_col, bwd_body)
    mirrors['bwd_entry'] = bwd_entry_col
    mirrors['bwd_exit'] = bwd_exit_col

    # End marker for exit detection
    mirrors['end'] = gb.pos()

    total_cols = gb.pos() + 2  # small margin
    return gb.ops, gb.corridors, mirrors, total_cols


def make_hamming_gadget(codeword):
    """Build a 5-row torus with the Hamming SECDED gadget."""
    code_ops, corridors, mirrors, min_cols = build_gadget()
    cols = max(N_DATA, min_cols)

    sim = FB2DSimulator(rows=N_ROWS, cols=cols)

    # Place data
    sim.grid[sim._to_flat(DATA_ROW, CW)] = codeword
    sim.grid[sim._to_flat(DATA_ROW, FIX)] = 1

    # Place code row opcodes
    for col, opchar in code_ops:
        sim.grid[sim._to_flat(CODE_ROW, col)] = OP[opchar]

    # Place forward loop corridor on CORR_FWD
    for col, opchar in corridors['fwd']:
        sim.grid[sim._to_flat(CORR_FWD, col)] = OP[opchar]

    # Place backward loop corridor on CORR_BWD
    for col, opchar in corridors['bwd']:
        sim.grid[sim._to_flat(CORR_BWD, col)] = OP[opchar]

    # Initial state
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(DATA_ROW, CW)
    sim.h1 = sim._to_flat(DATA_ROW, CW)
    sim.cl = sim._to_flat(DATA_ROW, CW)
    sim.gp = sim._to_flat(GP_ROW, 0)

    return sim, mirrors


def run_gadget(codeword, verbose=False):
    """Run the Hamming gadget on a codeword.

    Returns: (result_cw, ref_syndrome, ref_p_all, forward_steps, reverse_ok)
    """
    sim, mirrors = make_hamming_gadget(codeword)
    end_col = mirrors['end']

    # Run until IP passes the end marker going East on CODE_ROW
    max_steps = 20000
    for _ in range(max_steps):
        if (sim.ip_row == CODE_ROW and sim.ip_col >= end_col
                and sim.ip_dir == 1):
            break
        sim.step()
    else:
        if verbose:
            print(f"  TIMEOUT at step {sim.step_count},"
                  f" IP=({sim.ip_row},{sim.ip_col}) dir={sim.ip_dir}")
        return codeword, -1, -1, max_steps, False

    forward_steps = sim.step_count
    result_cw = sim.grid[sim._to_flat(DATA_ROW, CW)]

    # Reference syndrome for reporting
    _, ref_syn, ref_p_all, _ = decode(codeword)

    # Reverse all steps
    for _ in range(forward_steps):
        sim.step_back()

    # Verify full restoration
    reverse_ok = (
        sim.grid[sim._to_flat(DATA_ROW, CW)]   == codeword and
        sim.grid[sim._to_flat(DATA_ROW, FIX)]  == 1 and
        sim.grid[sim._to_flat(DATA_ROW, S0)]   == 0 and
        sim.grid[sim._to_flat(DATA_ROW, S1)]   == 0 and
        sim.grid[sim._to_flat(DATA_ROW, S2)]   == 0 and
        sim.grid[sim._to_flat(DATA_ROW, PA)]   == 0 and
        sim.grid[sim._to_flat(DATA_ROW, SYND)] == 0 and
        sim.grid[sim._to_flat(DATA_ROW, CNTF)] == 0 and
        sim.grid[sim._to_flat(DATA_ROW, CNTB)] == 0 and
        sim.grid[sim._to_flat(DATA_ROW, MASK)] == 0
    )

    if verbose and not reverse_ok:
        print(f"    [WARN] Reverse failed:")
        names = ['CW','S0','S1','S2','PA','FIX','SYND','CNTF','CNTB','MASK']
        for i, name in enumerate(names):
            val = sim.grid[sim._to_flat(DATA_ROW, i)]
            print(f"      {name}={val}")

    return result_cw, ref_syn, ref_p_all, forward_steps, reverse_ok


def run_test(data4, error_bit=None, error_bit2=None, verbose=False):
    """Test Hamming correction on a single codeword."""
    cw = encode(data4)

    if error_bit2 is not None:
        bad = inject_double_error(cw, error_bit, error_bit2)
        error_desc = f"double flip bits {error_bit},{error_bit2}"
    elif error_bit is not None:
        bad = inject_error(cw, error_bit)
        error_desc = f"flip bit {error_bit}"
    else:
        bad = cw
        error_desc = "no error"

    result, syndrome, p_all_err, steps, reverse_ok = run_gadget(bad, verbose)

    if error_bit2 is not None:
        expected = bad          # double error: don't correct
        ok = (result == expected)
    elif error_bit is not None:
        expected = cw           # single error: correct
        ok = (result == expected)
    else:
        expected = cw           # no error: untouched
        ok = (result == expected)

    if verbose or not ok or not reverse_ok:
        print(f"  data={data4:04b} cw={cw:08b} {error_desc}")
        print(f"    input={bad:08b} syn={syndrome:03b} p_all={p_all_err}"
              f" → result={result:08b} expected={expected:08b}"
              f" {'ok' if ok else 'FAIL'}")
        print(f"    {steps} steps, reverse={'ok' if reverse_ok else 'FAIL'}")

    return ok and reverse_ok


if __name__ == '__main__':
    all_ok = True
    print("=== Hamming SECDED Spatial Gadget in fb2d ===\n")

    code_ops, corridors, mirrors, total_cols = build_gadget()
    print(f"Gadget: {len(code_ops)} code ops, {total_cols} columns")
    print(f"  Fwd loop:  cols {mirrors['fwd_entry']}–{mirrors['fwd_exit']}"
          f"  ({len(corridors['fwd'])-2} body ops on corridor)")
    print(f"  Bwd loop:  cols {mirrors['bwd_entry']}–{mirrors['bwd_exit']}"
          f"  ({len(corridors['bwd'])-2} body ops on corridor)")

    print("\n--- No errors ---")
    no_err_ok = True
    for data in range(16):
        no_err_ok &= run_test(data)
    print(f"  16/16 no-error cases: {'PASS' if no_err_ok else 'FAIL'}")
    all_ok &= no_err_ok

    print("\n--- Single-bit error correction ---")
    single_ok = True
    count = 0
    for data in range(16):
        for bit in range(8):
            single_ok &= run_test(data, error_bit=bit)
            count += 1
    print(f"  {count}/{count} single-bit errors: {'PASS' if single_ok else 'FAIL'}")
    all_ok &= single_ok

    print("\n--- Double-bit error detection ---")
    double_ok = True
    count = 0
    for data in range(16):
        for b1 in range(8):
            for b2 in range(b1 + 1, 8):
                double_ok &= run_test(data, error_bit=b1, error_bit2=b2)
                count += 1
    print(f"  {count}/{count} double-bit errors: {'PASS' if double_ok else 'FAIL'}")
    all_ok &= double_ok

    print("\n--- Verbose examples ---")
    run_test(0b1010, verbose=True)
    run_test(0b1010, error_bit=3, verbose=True)
    run_test(0b1010, error_bit=7, verbose=True)
    run_test(0b1010, error_bit=0, verbose=True)
    run_test(0b1010, error_bit=0, error_bit2=3, verbose=True)

    print(f"\n{'='*55}")
    print(f"{'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*55}")

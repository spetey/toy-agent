#!/usr/bin/env python3
"""
fuel-demo-v2.py — Agent that eats fuel, replenishes EX, runs inner bounded loops.

Demonstrates the full "digestive system": fuel → zeros → computation.

ARCHITECTURE (5-row torus):

  Row 0 (FUEL):       a a b b c c ... 0   ← compressible pairs, zero-terminated
  Row 1 (INNER_CORR): /  ·  \\              ← corridor for inner ( P ... % loop
  Row 2 (OUTER_CORR): /  · · · · · · · · \\  ← corridor for outer spatial loop
  Row 3 (CODE):       \\ e x P Z ] > E ] ( P - % E e > %   ← agent gadget
  Row 4 (EX):         0 0 0 0 0 0 0 0 0 0  ← EX trail

GADGET (16 opcodes, col 1-16 on code row):
  e x     — H1 east, XOR compress fuel pair → fuel[2i] = 0
  P Z     — breadcrumb + shuttle: P writes to EX, Z swaps zero from fuel
  ] > E   — advance EX, CL, H0 to fuel[2i+1] (the fuel value V)
  ] ( P   — advance EX to fresh cell, enter inner loop, breadcrumb
  -       — decrement V (inner loop body, the "useful work")
  %       — inner loop exit check (CL at fuel[2i+1])
  E e >   — advance H0, H1, CL to next fuel pair
  %       — outer loop exit check (CL at fuel[2i+2])

INNER LOOP ( P - % on cols 9-12):
  Uses standard ( P ... % pattern. CL and H0 point at fuel[2i+1] (value V).
  Each iteration: P increments breadcrumb (accumulates in one EX cell),
  - decrements V. After V iterations, V=0, % passes through.
  Corridor: row 1 has / at col 9, \\ at col 12. No head resets needed
  (H0 doesn't move in the loop body).

OUTER SPATIAL LOOP (% at col 16 + row 2 corridor):
  Not a ( P % loop — uses physical torus routing instead.
  % reflects E→N when fuel remains. IP travels: row 2 \\ at col 16 → W,
  row 2 / at col 0 → S, row 3 \\ at col 0 → E. Re-enters gadget.
  Heads advance 2 fuel cells per iteration (no reset needed).

EX ACCOUNTING:
  Per fuel pair (value V): 2 EX cells consumed by ] advances, V consumed
  by inner loop P. The outer P+Z pair is self-cleaning (nets zero).
  Total: V+2 pre-existing zeros consumed per pair, 1 new zero produced.
  The EX trail starts all-zero; fuel extends the reserve.

WASTE PATTERN:
  fuel_even[0] = 1 (outer P breadcrumb from first Z swap)
  fuel_even[i] = V_{i-1} + 1 (previous inner breadcrumb + outer P, cleaned by Z)
  fuel_odd[i] = 0 (inner loop counted V down to zero)
  EX trail: all zeros except last inner cell = V_last (not cleaned).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, encode_opcode, hamming_encode,
                  cell_to_payload, _PAYLOAD_TO_OPCODE)

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}


def cell_char(v):
    """Decode a 16-bit grid cell to its opcode character."""
    if v == 0:
        return '·'
    op = _PAYLOAD_TO_OPCODE[cell_to_payload(v)]
    return OPCHAR.get(op, '·')


def make_fuel_agent_v2(fuel_pairs):
    """Build a torus agent with fuel compression + inner bounded loop.

    fuel_pairs: list of nonzero values. Each appears twice in fuel row.
    Returns (sim, n_pairs).
    """
    n_pairs = len(fuel_pairs)
    fuel = []
    for v in fuel_pairs:
        fuel.extend([v, v])
    fuel.append(0)  # zero terminator

    FUEL_ROW = 0
    INNER_CORR = 1
    OUTER_CORR = 2
    CODE_ROW = 3
    EX_ROW = 4
    rows = 5

    # Gadget: e x P Z ] > E ] ( P - % E e > %
    # Col:    1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6
    #                            1 1 1 1 1 1 1
    gadget = ['e', 'x', 'P', 'Z', ']', '>', 'E', ']', '(', 'P', '-', '%',
              'E', 'e', '>', '%']
    gadget_start = 1        # col 0 is \ for re-entry
    gadget_len = len(gadget) # 16

    # Key columns (1-indexed from gadget_start)
    inner_open_col = gadget_start + 8    # ( at col 9
    inner_close_col = gadget_start + 11  # % at col 12
    outer_close_col = gadget_start + 15  # % at col 16

    # Grid width: must fit fuel, code, and EX trail
    total_gp_needed = sum(fuel_pairs) + 2 * n_pairs + 4  # generous padding
    cols = max(outer_close_col + 4, len(fuel) + 2, total_gp_needed + 2, 24)

    sim = FB2DSimulator(rows=rows, cols=cols)

    # Place fuel on row 0 (Hamming-encoded data values)
    for i, v in enumerate(fuel):
        sim.grid[sim._to_flat(FUEL_ROW, i)] = hamming_encode(v)

    # Place gadget on CODE_ROW (Hamming-encoded opcodes)
    for i, op_name in enumerate(gadget):
        sim.grid[sim._to_flat(CODE_ROW, gadget_start + i)] = encode_opcode(OP[op_name])

    # Re-entry mirror: \ at (CODE_ROW, 0)
    sim.grid[sim._to_flat(CODE_ROW, 0)] = encode_opcode(OP['\\'])

    # Outer corridor: / at col 0, \ at outer_close_col on OUTER_CORR
    sim.grid[sim._to_flat(OUTER_CORR, 0)] = encode_opcode(OP['/'])
    sim.grid[sim._to_flat(OUTER_CORR, outer_close_col)] = encode_opcode(OP['\\'])

    # Inner corridor: / at inner_open_col, \ at inner_close_col on INNER_CORR
    sim.grid[sim._to_flat(INNER_CORR, inner_open_col)] = encode_opcode(OP['/'])
    sim.grid[sim._to_flat(INNER_CORR, inner_close_col)] = encode_opcode(OP['\\'])

    # Initial state
    sim.ip_row = CODE_ROW
    sim.ip_col = gadget_start
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(FUEL_ROW, 0)
    sim.h1 = sim._to_flat(FUEL_ROW, 0)
    sim.cl = sim._to_flat(FUEL_ROW, 0)
    sim.ex = sim._to_flat(EX_ROW, 0)
    sim.step_count = 0

    return sim, n_pairs, cols


def run_test(fuel_pairs, label="", verbose=False):
    """Run a v2 fuel agent test with inner bounded loop."""
    sim, n_pairs, grid_cols = make_fuel_agent_v2(fuel_pairs)
    cols = grid_cols
    CODE_ROW = 3
    EX_ROW = 4
    cols = sim.cols

    print(f"\n{'='*60}")
    print(f"Fuel agent v2: {fuel_pairs}  {label}")
    print(f"{'='*60}")

    fuel_len = 2 * n_pairs + 1
    fuel_before = [cell_to_payload(sim.grid[sim._to_flat(0, c)])
                   for c in range(fuel_len)]
    gp_cells = sum(fuel_pairs) + 2 * n_pairs + 4
    gp_before = [cell_to_payload(sim.grid[sim._to_flat(4, c)])
                 for c in range(min(gp_cells, cols))]

    print(f"  Before:")
    print(f"    Fuel (row 0): {fuel_before}")
    print(f"    EX   (row 4): {gp_before[:20]}{'...' if len(gp_before) > 20 else ''}")

    # Show grid
    code_str = ""
    for c in range(min(20, cols)):
        v = sim.grid[sim._to_flat(3, c)]
        ch = cell_char(v)
        code_str += f" {ch}"
    print(f"    Code (row 3):{code_str}")

    corr_str = ""
    for c in range(min(20, cols)):
        v = sim.grid[sim._to_flat(2, c)]
        ch = cell_char(v)
        corr_str += f" {ch}"
    print(f"    OutCorr(row 2):{corr_str}")

    icorr_str = ""
    for c in range(min(20, cols)):
        v = sim.grid[sim._to_flat(1, c)]
        ch = cell_char(v)
        icorr_str += f" {ch}"
    print(f"    InCorr (row 1):{icorr_str}")

    # Run until exit
    max_steps = 100000
    for step in range(max_steps):
        # Exit: IP on CODE_ROW going E past the outer %
        if sim.ip_row == CODE_ROW and sim.ip_dir == 1 and sim.ip_col > (1 + 15):
            break
        sim.step()

        if verbose and sim.step_count <= 80:
            r, c = sim.ip_row, sim.ip_col
            opval = sim.grid[sim._to_flat(r, c)]
            opch = cell_char(opval)
            h0_r, h0_c = divmod(sim.h0, cols)
            h1_r, h1_c = divmod(sim.h1, cols)
            cl_r, cl_c = divmod(sim.cl, cols)
            ex_r, ex_c = divmod(sim.ex, cols)
            print(f"    step {sim.step_count:3d}: IP=({r},{c})={opch:2s}"
                  f"  H0=({h0_r},{h0_c}) H1=({h1_r},{h1_c})"
                  f"  CL=({cl_r},{cl_c}) EX=({ex_r},{ex_c})"
                  f"  [H0]={cell_to_payload(sim.grid[sim.h0])}"
                  f" [CL]={cell_to_payload(sim.grid[sim.cl])}"
                  f"  [EX]={cell_to_payload(sim.grid[sim.ex])}")

    if sim.step_count >= max_steps:
        print(f"  TIMEOUT after {max_steps} steps!")
        return False

    forward_steps = sim.step_count

    # Results (compare payloads)
    fuel_after_p = [cell_to_payload(sim.grid[sim._to_flat(0, c)])
                    for c in range(fuel_len)]
    gp_after_p = [cell_to_payload(sim.grid[sim._to_flat(4, c)])
                  for c in range(min(gp_cells, cols))]

    print(f"  After ({forward_steps} steps):")
    print(f"    Fuel (row 0): {fuel_after_p}")
    print(f"    EX   (row 4): {gp_after_p[:20]}{'...' if len(gp_after_p) > 20 else ''}")

    # Analysis (payload-level)
    # Fuel odd positions should be 0 (inner loop counted them down to zero).
    # Fuel even positions absorb waste from P+Z:
    #   fuel_even[0] = 1 (first outer P writes 1, Z swaps it into fuel[0])
    #   fuel_even[i] = V_{i-1} + 1 for i > 0 (prev inner breadcrumb + outer P)
    fuel_even = [fuel_after_p[i] for i in range(0, 2 * n_pairs, 2)]
    fuel_odd = [fuel_after_p[i] for i in range(1, 2 * n_pairs, 2)]

    expected_even = [1]
    for i in range(1, n_pairs):
        expected_even.append(fuel_pairs[i - 1] + 1)

    even_ok = (fuel_even == expected_even)
    odd_ok = all(v == 0 for v in fuel_odd)
    fuel_ok = even_ok and odd_ok

    print(f"    Fuel even (waste absorbed): {fuel_even}  "
          f"expected {expected_even}  {'ok' if even_ok else 'FAIL'}")
    print(f"    Fuel odd (consumed to 0):  {fuel_odd}  {'ok' if odd_ok else 'FAIL'}")

    # EX trail analysis:
    # Per outer iteration i, EX cells are used as follows:
    #   Cell 2i:   outer P writes, Z immediately swaps zero in → cleaned to 0
    #   Cell 2i+1: skipped by ] (stays 0)
    #   Cell 2i+2: inner loop P accumulates V_i breadcrumbs
    # Next iteration's outer P+Z cleans cell 2i+2 (waste goes to fuel).
    # Last iteration's inner cell 2(k-1)+2 is NOT cleaned (leftover waste).
    last_inner_cell = 2 * (n_pairs - 1) + 2
    last_inner_value = fuel_pairs[-1]

    gp_ok = True
    for i in range(min(gp_cells, cols)):
        expected = last_inner_value if i == last_inner_cell else 0
        if gp_after_p[i] != expected:
            gp_ok = False
            break

    print(f"    EX trail: clean except cell {last_inner_cell}={last_inner_value}"
          f"  {'ok' if gp_ok else 'FAIL'}")
    print(f"    EX (first {min(20, gp_cells)} cells): {gp_after_p[:20]}")

    # Reversibility (compare raw cell values)
    for _ in range(forward_steps):
        sim.step_back()

    fuel_reversed = [sim.grid[sim._to_flat(0, c)] for c in range(fuel_len)]
    gp_reversed = [sim.grid[sim._to_flat(4, c)] for c in range(min(gp_cells, cols))]

    orig_fuel_encoded = []
    for v in fuel_pairs:
        orig_fuel_encoded.extend([hamming_encode(v), hamming_encode(v)])
    orig_fuel_encoded.append(0)  # hamming_encode(0) = 0

    reverse_ok = (fuel_reversed == orig_fuel_encoded and
                  all(v == 0 for v in gp_reversed))

    print(f"    Reversible: {'PASS' if reverse_ok else 'FAIL'}")
    if not reverse_ok:
        fuel_rev_p = [cell_to_payload(v) for v in fuel_reversed]
        orig_p = [cell_to_payload(v) for v in orig_fuel_encoded]
        print(f"      Fuel reversed: {fuel_rev_p}")
        print(f"      Expected fuel: {orig_p}")
        gp_rev_p = [cell_to_payload(v) for v in gp_reversed]
        print(f"      EX reversed:   {gp_rev_p[:20]}")

    # Total inner work done
    total_inner_iters = sum(fuel_pairs)
    print(f"    Total inner loop iterations: {total_inner_iters}")
    print(f"    Steps per inner iteration: ~{forward_steps / max(total_inner_iters, 1):.1f}")

    overall = fuel_ok and gp_ok and reverse_ok
    print(f"    Result: {'PASS' if overall else 'FAIL'}")
    return overall


if __name__ == '__main__':
    all_ok = True

    # Start with verbose single pair to see the mechanics
    all_ok &= run_test([3], "(single pair, V=3)", verbose=True)
    all_ok &= run_test([1], "(single pair, V=1)")
    all_ok &= run_test([2, 3], "(two pairs)")
    all_ok &= run_test([1, 2, 3], "(three pairs)")
    all_ok &= run_test([5, 5, 5], "(uniform V=5)")
    all_ok &= run_test([1, 1, 1, 1, 1, 1], "(six pairs, V=1)")
    all_ok &= run_test([10, 20, 30], "(larger values)")

    print(f"\n{'='*60}")
    print(f"{'All tests passed!' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*60}")

    if all_ok:
        # Save loadable demo
        sim, _, _ = make_fuel_agent_v2([3, 2, 1])
        fn = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'fuel-demo-v2.fb2d')
        sim.save_state(fn)
        print(f"\nSaved: {fn}")
        print(f"Run:   python3 fb2d.py  →  load fuel-demo-v2")
        print(f"\nOuter gadget: e x P Z ] > E ] ( P - % E e > %")
        print(f"  e x     — compress fuel pair (XOR → zero)")
        print(f"  P Z     — shuttle: breadcrumb then swap zero to EX")
        print(f"  ] > E   — advance EX, CL, H0 to fuel value cell")
        print(f"  ] ( P   — advance EX, enter inner loop, breadcrumb")
        print(f"  -       — decrement counter (inner loop body)")
        print(f"  %       — inner loop check: loop if counter nonzero")
        print(f"  E e >   — advance H0, H1, CL to next fuel pair")
        print(f"  %       — outer loop check: loop if fuel remains")

#!/usr/bin/env python3
"""
fuel-demo-v3.py — Agent eats fuel, rotates data bytes as error-checking work.

Demonstrates: fuel compression → GP zeros → error-checking computation.
First step toward a self-maintaining agent that eats fuel to power
error-correction on its own important data.

ARCHITECTURE (6-row torus):

  Row 0 (FUEL):       V V V V V V ... 0    compressible pairs, zero-terminated
  Row 1 (DATA):       D 0 D 0 D 0 ...      data bytes at even cols, counter at odd
  Row 2 (INNER_CORR): . . . . / . . \\ ...  inner loop corridor
  Row 3 (OUTER_CORR): . / . . . . . . . \\  outer loop corridor
  Row 4 (CODE):       ] ( P e x Z ] > E S . v s w ] ( P W l E - % N E n e e ^ > %
  Row 5 (GP):         0 0 0 0 0 0 0 0 ...  GP trail

GADGET (30 opcodes, cols 0-29):
  ] ( P           — setup + outer loop entry + breadcrumb
  e x             — XOR compress fuel pair → fuel[2i] = 0
  Z ]             — shuttle zero to GP, advance GP
  > E S . v       — copy fuel value V to DATA counter cell
  s w             — position H1 at data byte
  ] ( P           — inner loop entry + breadcrumb
  W l E -         — rotate data byte left, decrement counter
  %               — inner loop exit (CL checks counter)
  N E n e e ^ >   — restore all heads to fuel row for next pair
  %               — outer loop exit (CL checks next fuel cell)

INNER LOOP: Each iteration rotates data[2i] left by 1 bit (the "error check")
and decrements the counter. After V iterations, data byte is rotl^V(original).

CORRIDORS:
  Inner (row 2): / at col 15, \\ at col 21
  Outer (row 3): / at col 1,  \\ at col 29
  No conflicts: outer corridor is NOP at cols 15, 21.

WASTE PATTERN:
  fuel_even[0] = 1 (first outer breadcrumb)
  fuel_even[i] = V_{i-1} + 1 (previous inner breadcrumb + outer P)
  fuel_odd[i] = V_i (original fuel values, untouched — no zeros!)
  data_even[i] = rotl^V_i(D_i) (rotated data bytes)
  data_odd[i] = 0 (counter scratch, counted down to zero)
  GP: all zeros except last inner cell = V_last
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}


def rotl8(val, n):
    """Rotate 8-bit value left by n positions."""
    n = n % 8
    return ((val << n) | (val >> (8 - n))) & 0xFF


def make_fuel_agent_v3(fuel_pairs, data_bytes):
    """Build a 6-row torus agent with fuel compression + data rotation.

    fuel_pairs: list of nonzero values. Each appears twice in fuel row.
    data_bytes: list of byte values (same length as fuel_pairs).
    Returns (sim, n_pairs).
    """
    assert len(fuel_pairs) == len(data_bytes), \
        "fuel_pairs and data_bytes must have same length"
    n_pairs = len(fuel_pairs)

    # Build fuel row: V V V V ... 0
    fuel = []
    for v in fuel_pairs:
        fuel.extend([v, v])
    fuel.append(0)  # zero terminator

    FUEL_ROW = 0
    DATA_ROW = 1
    INNER_CORR = 2
    OUTER_CORR = 3
    CODE_ROW = 4
    GP_ROW = 5
    rows = 6

    # Gadget: ] ( P e x Z ] > E S . v s w ] ( P W l E - % N E n e e ^ > %
    gadget = [']', '(', 'P', 'e', 'x', 'Z', ']', '>', 'E', 'S', '.', 'v',
              's', 'w', ']', '(', 'P', 'W', 'l', 'E', '-', '%',
              'N', 'E', 'n', 'e', 'e', '^', '>', '%']
    gadget_len = len(gadget)  # 30

    # Key columns
    outer_open_col = 1     # ( for outer loop
    inner_open_col = 15    # ( for inner loop
    inner_close_col = 21   # % for inner loop
    outer_close_col = 29   # % for outer loop

    # Grid width
    data_width = 2 * n_pairs + 1
    gp_needed = 2 * n_pairs + 4  # generous
    cols = max(gadget_len + 2, data_width + 2, gp_needed + 2, 32)

    sim = FB2DSimulator(rows=rows, cols=cols)

    # Place fuel on row 0
    for i, v in enumerate(fuel):
        sim.grid[sim._to_flat(FUEL_ROW, i)] = v

    # Place data on row 1 (even cols = data bytes, odd cols = 0 counter scratch)
    for i, d in enumerate(data_bytes):
        sim.grid[sim._to_flat(DATA_ROW, 2 * i)] = d
        # Odd cols already 0 (counter scratch)

    # Place gadget on CODE_ROW
    for i, op_name in enumerate(gadget):
        sim.grid[sim._to_flat(CODE_ROW, i)] = OP[op_name]

    # Inner corridor: / at col 15 (inner (), \ at col 21 (inner %)
    sim.grid[sim._to_flat(INNER_CORR, inner_open_col)] = OP['/']
    sim.grid[sim._to_flat(INNER_CORR, inner_close_col)] = OP['\\']

    # Outer corridor: / at col 1 (outer (), \ at col 29 (outer %)
    sim.grid[sim._to_flat(OUTER_CORR, outer_open_col)] = OP['/']
    sim.grid[sim._to_flat(OUTER_CORR, outer_close_col)] = OP['\\']

    # Initial state
    sim.ip_row = CODE_ROW
    sim.ip_col = 0
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(FUEL_ROW, 0)
    sim.h1 = sim._to_flat(FUEL_ROW, 0)
    sim.cl = sim._to_flat(FUEL_ROW, 0)
    sim.gp = sim._to_flat(GP_ROW, 0)
    sim.step_count = 0

    return sim, n_pairs


def run_test(fuel_pairs, data_bytes, label="", verbose=False):
    """Run a v3 fuel agent test with data rotation."""
    sim, n_pairs = make_fuel_agent_v3(fuel_pairs, data_bytes)
    cols = sim.cols
    CODE_ROW = 4

    print(f"\n{'='*60}")
    print(f"Fuel agent v3: fuel={fuel_pairs} data={data_bytes}  {label}")
    print(f"{'='*60}")

    fuel_len = 2 * n_pairs + 1
    data_len = 2 * n_pairs
    gp_cells = 2 * n_pairs + 4

    fuel_before = [sim.grid[sim._to_flat(0, c)] for c in range(fuel_len)]
    data_before = [sim.grid[sim._to_flat(1, c)] for c in range(data_len)]
    gp_before = [sim.grid[sim._to_flat(5, c)] for c in range(min(gp_cells, cols))]

    print(f"  Before:")
    print(f"    Fuel (row 0): {fuel_before}")
    print(f"    Data (row 1): {data_before}")

    # Show grid layout
    for row_idx, row_name in [(4, "Code"), (3, "OutCorr"), (2, "InCorr")]:
        s = ""
        for c in range(min(32, cols)):
            v = sim.grid[sim._to_flat(row_idx, c)]
            ch = OPCHAR.get(v, '.')
            s += f" {ch}"
        print(f"    {row_name:8s} (row {row_idx}):{s}")

    # Run until exit
    max_steps = 200000
    for step in range(max_steps):
        # Exit: IP on CODE_ROW going E past the outer %
        if sim.ip_row == CODE_ROW and sim.ip_dir == 1 and sim.ip_col > 29:
            break
        sim.step()

        if verbose and sim.step_count <= 120:
            r, c = sim.ip_row, sim.ip_col
            opval = sim.grid[sim._to_flat(r, c)]
            opch = OPCHAR.get(opval, '.')
            h0_r, h0_c = divmod(sim.h0, cols)
            h1_r, h1_c = divmod(sim.h1, cols)
            cl_r, cl_c = divmod(sim.cl, cols)
            gp_r, gp_c = divmod(sim.gp, cols)
            print(f"    step {sim.step_count:3d}: IP=({r},{c})={opch:2s}"
                  f"  H0=({h0_r},{h0_c}) H1=({h1_r},{h1_c})"
                  f"  CL=({cl_r},{cl_c}) GP=({gp_r},{gp_c})"
                  f"  [H0]={sim.grid[sim.h0]} [CL]={sim.grid[sim.cl]}"
                  f"  [GP]={sim.grid[sim.gp]}")

    if sim.step_count >= max_steps:
        print(f"  TIMEOUT after {max_steps} steps!")
        return False

    forward_steps = sim.step_count

    # Results
    fuel_after = [sim.grid[sim._to_flat(0, c)] for c in range(fuel_len)]
    data_after = [sim.grid[sim._to_flat(1, c)] for c in range(data_len)]
    gp_after = [sim.grid[sim._to_flat(5, c)] for c in range(min(gp_cells, cols))]

    print(f"  After ({forward_steps} steps):")
    print(f"    Fuel (row 0): {fuel_after}")
    print(f"    Data (row 1): {data_after}")

    # === Check fuel ===
    fuel_even = [fuel_after[i] for i in range(0, 2 * n_pairs, 2)]
    fuel_odd = [fuel_after[i] for i in range(1, 2 * n_pairs, 2)]

    # Expected even: [1, V0+1, V1+1, ...]
    expected_fuel_even = [1]
    for i in range(1, n_pairs):
        expected_fuel_even.append(fuel_pairs[i - 1] + 1)

    fuel_even_ok = (fuel_even == expected_fuel_even)
    fuel_odd_ok = (fuel_odd == list(fuel_pairs))  # untouched
    fuel_no_zeros = all(v != 0 for v in fuel_after[:2 * n_pairs])

    print(f"    Fuel even (breadcrumbs): {fuel_even} expected {expected_fuel_even}"
          f"  {'ok' if fuel_even_ok else 'FAIL'}")
    print(f"    Fuel odd (original V):   {fuel_odd} expected {list(fuel_pairs)}"
          f"  {'ok' if fuel_odd_ok else 'FAIL'}")
    print(f"    Fuel no-zeros (excl term): {'ok' if fuel_no_zeros else 'FAIL'}")

    # === Check data ===
    data_even = [data_after[i] for i in range(0, data_len, 2)]
    data_odd = [data_after[i] for i in range(1, data_len, 2)]

    expected_data = [rotl8(d, v) for d, v in zip(data_bytes, fuel_pairs)]

    data_even_ok = (data_even == expected_data)
    data_odd_ok = all(v == 0 for v in data_odd)

    print(f"    Data even (rotated):  {data_even} expected {expected_data}"
          f"  {'ok' if data_even_ok else 'FAIL'}")
    print(f"    Data odd (counters):  {data_odd}"
          f"  {'ok' if data_odd_ok else 'FAIL'}")

    # === Check GP trail ===
    last_inner_cell_idx = 2 * (n_pairs - 1) + 2  # relative to GP start
    # Actually: let me count GP advances.
    # Col 0: ] advances GP from cell 0 to cell 1 (setup)
    # Col 1: ( — first outer entry, [GP]=0 at cell 1
    # Col 2: P — cell 1 becomes 1
    # Col 5: Z — cell 1 gets swapped (becomes 0, breadcrumb to fuel)
    #   Wait, Z swaps [H0] with [GP]. H0 is at fuel[2i]=0 (after XOR).
    #   GP is at cell 1 (value=1 from P). After Z: fuel[0]=1, GP cell=0.
    # Col 6: ] — GP to cell 2
    # Col 14: ] — GP to cell 3 (fresh for inner)
    # Col 15: ( — inner entry, [GP]=0 at cell 3
    # Col 16: P — inner loop accumulates V in cell 3
    # After inner exit: GP at cell 3, value = V
    # No ] before outer restore/exit
    # Next outer iteration:
    # Col 2: P — cell 3 goes from V to V+1
    # Col 5: Z — cell 3 gets swapped to 0
    # Col 6: ] — GP to cell 4
    # Col 14: ] — GP to cell 5
    # Inner accumulates V2 in cell 5
    #
    # Pattern: cells 1,2,4,6,... get used. Let me just compute.
    # Setup ] moves GP from 0 to 1.
    # Iter 0: P/Z on cell 1 (cleaned), ] to 2, ] to 3, inner P on cell 3 = V0
    # Iter 1: P/Z on cell 3 (cleaned), ] to 4, ] to 5, inner P on cell 5 = V1
    # Iter k: P/Z on cell 2k+1 (cleaned), ] to 2k+2, ] to 2k+3, inner P = Vk
    #
    # Last cell with inner breadcrumb: 2*(n-1)+3
    last_gp_cell = 2 * (n_pairs - 1) + 3
    last_gp_value = fuel_pairs[-1]

    gp_ok = True
    for i in range(min(gp_cells, cols)):
        expected = last_gp_value if i == last_gp_cell else 0
        if gp_after[i] != expected:
            gp_ok = False
            print(f"    GP mismatch at cell {i}: got {gp_after[i]}, expected {expected}")
            break

    print(f"    GP trail: clean except cell {last_gp_cell}={last_gp_value}"
          f"  {'ok' if gp_ok else 'FAIL'}")
    print(f"    GP (first {min(20, gp_cells)} cells): {gp_after[:20]}")

    # === Reversibility ===
    for _ in range(forward_steps):
        sim.step_back()

    fuel_rev = [sim.grid[sim._to_flat(0, c)] for c in range(fuel_len)]
    data_rev = [sim.grid[sim._to_flat(1, c)] for c in range(data_len)]
    gp_rev = [sim.grid[sim._to_flat(5, c)] for c in range(min(gp_cells, cols))]

    orig_fuel = []
    for v in fuel_pairs:
        orig_fuel.extend([v, v])
    orig_fuel.append(0)

    orig_data = []
    for i, d in enumerate(data_bytes):
        orig_data.extend([d, 0])

    reverse_ok = (fuel_rev == orig_fuel and
                  data_rev == orig_data and
                  all(v == 0 for v in gp_rev))

    print(f"    Reversible: {'PASS' if reverse_ok else 'FAIL'}")
    if not reverse_ok:
        if fuel_rev != orig_fuel:
            print(f"      Fuel: got {fuel_rev}, expected {orig_fuel}")
        if data_rev != orig_data:
            print(f"      Data: got {data_rev}, expected {orig_data}")
        if not all(v == 0 for v in gp_rev):
            print(f"      GP:   {gp_rev[:20]}")

    # Summary
    total_rotations = sum(fuel_pairs)
    overall = (fuel_even_ok and fuel_odd_ok and fuel_no_zeros and
               data_even_ok and data_odd_ok and gp_ok and reverse_ok)
    print(f"    Total rotations: {total_rotations}  Steps: {forward_steps}")
    print(f"    Result: {'PASS' if overall else 'FAIL'}")
    return overall


if __name__ == '__main__':
    all_ok = True

    # Single pair, V=3
    all_ok &= run_test([3], [42],
                        "(single pair, V=3)", verbose=True)

    # Single pair, V=1
    all_ok &= run_test([1], [255],
                        "(V=1, single rotation)")

    # V=8: full rotation cycle (data returns to original)
    all_ok &= run_test([8], [42],
                        "(V=8, full cycle — data unchanged)")

    # Two pairs
    all_ok &= run_test([2, 3], [42, 99],
                        "(two pairs)")

    # Three pairs — main demo
    all_ok &= run_test([3, 2, 1], [42, 99, 137],
                        "(three pairs — main demo)")

    # Uniform V
    all_ok &= run_test([5, 5, 5], [100, 200, 50],
                        "(uniform V=5)")

    # Larger values
    all_ok &= run_test([10, 20, 30], [100, 200, 50],
                        "(larger values)")

    print(f"\n{'='*60}")
    print(f"{'All tests passed!' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*60}")

    if all_ok:
        sim, _ = make_fuel_agent_v3([3, 2, 1], [42, 99, 137])
        fn = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'fuel-demo-v3.fb2d')
        sim.save_state(fn)
        print(f"\nSaved: {fn}")
        print(f"Run:   python3 fb2d.py  ->  load fuel-demo-v3")
        print(f"\nGadget: ] ( P e x Z ] > E S . v s w ] ( P W l E - % N E n e e ^ > %")
        print(f"  ] ( P     — setup + outer loop + breadcrumb")
        print(f"  e x       — XOR compress fuel pair")
        print(f"  Z ]       — shuttle zero to GP")
        print(f"  > E S . v — copy V to DATA counter")
        print(f"  s w       — H1 to data byte")
        print(f"  ] ( P     — inner loop entry")
        print(f"  W l E -   — rotate data byte left, decrement counter")
        print(f"  %         — inner loop exit")
        print(f"  N E n e e ^ > — restore heads to fuel row")
        print(f"  %         — outer loop exit")

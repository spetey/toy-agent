#!/usr/bin/env python3
"""
fuel-demo.py — Agent that eats fuel, shuttles zeros to GP, does work.

ARCHITECTURE (4-row torus):

  Row 0 (FUEL):  a a b b c c ... 0   ← compressible pairs, zero-terminated
  Row 1 (CORR):  /  · · · · · ·  \\   ← corridor: sends IP back west
  Row 2 (CODE):  \\ [gadget......] %   ← agent code, IP goes east
  Row 3 (GP):    0 0 0 0 0 0 0 0 0   ← GP trail

EACH LAP:
  Phase 1 — Compress fuel pair:
    e       H1 east → fuel[2i+1]
    x       [H0] ^= [H1] → fuel[2i] = 0  (XOR identical pair)

  Phase 2 — Shuttle zero to GP row:
    w       H1 west → back to fuel[2i] (the zero)
    S S S   H0 south x3 → H0 on GP row, col 2i
    E ...   H0 east to target GP cell (cell i)
    X       swap [H0] ↔ [H1] → GP cell gets 0, fuel[2i] gets old GP value

  Phase 3 — Restore H0 to fuel row for next pair:
    N N N   H0 north x3 → back to fuel row

  Phase 4 — Inner work using GP:
    ] ( P )  — advance GP, enter "loop" (1 iteration), leave breadcrumb
    (This is trivial work — just proves GP consumption works)
    Actually ] ( P ) is not right. Let's just do ] P — advance GP, write
    breadcrumb. This consumes one zero from the GP trail.

  Phase 5 — Advance for next pair:
    E E     H0 east x2 → fuel[2i+2] (start of next pair)
    e e     H1 east x2 → fuel[2i+2]
    > >     CL east x2 → fuel[2i+2] (for exit check)

  Phase 6 — Loop check:
    %       if [CL]!=0, reflect E→N → corridor → loop back
            if [CL]=0, exit east

SHUTTLE DETAIL:
  After XOR, fuel[2i]=0, fuel[2i+1]=V.
  We want to put that zero into gp_trail[i].
  H0 needs to get to (3, i). From (0, 2i): S S S moves to (3, 2i).
  Then we need to move west by (2i - i) = i columns. That's variable!

  Problem: the gadget is fixed code that loops. We can't have a variable
  number of W ops per iteration.

  Solution: H0 walks the GP row independently. Instead of going back to
  the fuel row between iterations, H0 STAYS on the GP row and advances
  east by 1 per iteration. H1 stays on the fuel row and advances east
  by 2 per iteration.

  But then for the XOR, we need H0 on the fuel row (x does [H0] ^= [H1]).

  Alternative: use a different approach. Instead of XOR+swap, use:
    . (add) and , (sub) to compute the difference.

  Or: rethink head assignments.

  NEW PLAN — fixed head roles:
    H0 = GP row walker (stays on row 3, advances east by 1/iteration)
    H1 = fuel row walker (stays on row 0, advances east by 2/iteration)
    CL = fuel row walker (for exit check, advances east by 2/iteration)

  Per iteration:
    Phase 1 — Read fuel pair, XOR to get zero:
      We need [H0]^=[H1] but H0 is on GP row, not fuel row.
      x does [H0] ^= [H1], putting result in H0's cell (GP row). No good.

      Alternative: use . and , instead:
        . : [H0] += [H1]  — add fuel[2i] to gp_trail[i]
        e : H1 east → fuel[2i+1]
        , : [H0] -= [H1]  — subtract fuel[2i+1] from gp_trail[i]
        For identical pairs: gp_trail[i] += V then -= V = 0. No change!

      That doesn't help either — it just leaves gp_trail[i] unchanged.

  Hmm. The core problem: the compression (XOR or subtract) produces a
  zero in a CELL, but we need that zero to end up on the GP row, and
  the production and destination are on different rows.

  SIMPLEST WORKING APPROACH:
  Put H0 on fuel row for the XOR. After XOR, fuel[2i]=0.
  Now use Z (swap [H0] with [GP]) to swap this zero with the GP cell.
  Z doesn't need H0 on the GP row — it swaps [H0] with [GP] regardless
  of where H0 is!

  Wait — Z is opcode 38: swap(grid[CL], grid[H0])... no.
  Let me check.

  From CLAUDE.md:
    Z (38): swap([CL], [H0]) — bridge
    No wait: Z (38): swap([H0], [GP]) — byte-level GP swap

  Let me re-check the ISA table...
    T (22): swap([CL], [H0]) — bridge
    Z (38): swap([H0], [GP]) — byte-level GP swap

  YES! Z swaps [H0] with [GP]. So:
  - H0 at fuel[2i] (which is 0 after XOR)
  - GP at gp_trail[i]
  - Z: gp_trail[i] gets 0, fuel[2i] gets old gp_trail[i] value

  This is exactly what we need! And we don't need to move H0 to the GP
  row at all. H0 stays on the fuel row.

  After Z: fuel[2i] has the old GP value (breadcrumb or 0).
  gp_trail[i] has 0 (ready for next P).

  Then ] advances GP east (to cell i+1 for next iteration).

  REVISED GADGET:
    e     H1 east → fuel[2i+1]
    x     [H0] ^= [H1] → fuel[2i] = 0
    Z     swap [H0] with [GP] → gp_trail gets 0, fuel gets old gp value
    ]     GP east (advance for next iteration)
    P     [GP]++ (breadcrumb — the "useful work" consuming a zero)
    E E   H0 east x2 → fuel[2i+2]
    e     H1 east → fuel[2i+2]
    > >   CL east x2 → CL at fuel[2i+2]
    %     exit check

  That's: e x Z ] P E E e > > %  — 11 opcodes!

  Let's verify the Z/]/P sequence:
  - After x: fuel[2i]=0, H0 at (0,2i), GP at (3,i)
  - Z: swap(fuel[2i], gp_trail[i]) → fuel[2i]=old_gp[i], gp_trail[i]=0
  - ]: GP moves east → GP at (3,i+1)
  - P: gp_trail[i+1]++ → writes breadcrumb (0→1) in cell i+1
    Wait — we just zeroed cell i, but P is writing to cell i+1.
    That means cell i stays 0 (good for future), cell i+1 gets breadcrumb.
    Next iteration's Z will zero cell i+1 (with the breadcrumb), and ]
    advances GP to i+2, and P writes to i+2.
    So each iteration: Z zeros cell i, ] moves to i+1, P writes i+1.
    Net: breadcrumbs accumulate but get cleaned up one iteration later!

  Actually wait. First iteration: GP starts at cell 0.
  - Z: swap fuel[0] with gp_trail[0]. gp_trail[0] was 0, so fuel[0]
    gets 0, gp_trail[0] gets 0. No-op (both zero). Hmm.
  - ]: GP moves to cell 1.
  - P: gp_trail[1]++ → cell 1 = 1 (breadcrumb).

  Second iteration: GP at cell 1.
  - Z: swap fuel[2] with gp_trail[1]. fuel[2]=0 (just XORed),
    gp_trail[1]=1 (breadcrumb from prev P). After: fuel[2]=1, gp_trail[1]=0.
  - ]: GP moves to cell 2.
  - P: gp_trail[2]++ → cell 2 = 1.

  Third iteration: GP at cell 2.
  - Z: swap fuel[4] with gp_trail[2]. fuel[4]=0, gp_trail[2]=1.
    After: fuel[4]=1, gp_trail[2]=0.
  - ]: GP moves to cell 3.
  - P: gp_trail[3]++ → cell 3 = 1.

  Pattern: Z always cleans up the PREVIOUS breadcrumb, then P writes a new one.
  The GP trail has at most one breadcrumb at any time. The fuel row
  accumulates the old breadcrumbs (values of 1) in the even positions.

  This works! The Z+]+P sequence is a rolling cleanup.

  On a torus wrap: when GP wraps around, cell 0 has the old breadcrumb
  from N iterations ago (value=1). Z cleans it, ] advances, P writes.
  As long as there's fuel to compress, the cleanup keeps pace.

  But wait — on the first iteration, Z swaps fuel[0] (which is 0, just
  XORed) with gp_trail[0] (which is 0). That's a no-op. The zero was
  "wasted" — we XORed the fuel pair but didn't actually deliver a useful
  zero to GP because GP's cell was already 0.

  Fix: start GP at cell 0, but do P FIRST (before Z). Then Z cleans up
  the breadcrumb that P just wrote.

  Revised: e x P Z ] E E e > > %
  - e x: compress, fuel[2i]=0
  - P: gp_trail[i]++ (writes breadcrumb, consuming the existing zero)
  - Z: swap fuel[2i](=0) with gp_trail[i](=breadcrumb) → gp[i]=0, fuel[2i]=breadcrumb
  - ]: GP east to cell i+1
  Hmm, but now P writes before Z cleans. On first entry, P increments
  cell 0 (was 0, now 1). Then Z swaps fuel[0](=0) with gp_trail[0](=1).
  After Z: fuel[0]=1, gp_trail[0]=0. Good — cleaned up immediately.
  Then ] moves GP to cell 1.

  Second iteration: P increments cell 1 (was 0, now 1). Z swaps
  fuel[2](=0) with cell 1 (=1). After: fuel[2]=1, cell 1=0. ] to cell 2.

  This works! P writes, Z immediately cleans, ] advances. Each fuel cell
  absorbs one breadcrumb. The GP trail stays clean (all zeros after Z).

  But there's a problem: on the FIRST iteration, ( would check [GP] before
  P runs. If we use ( at all. We're not using ( — the spatial loop uses %.

  Actually we don't need ( at all for this demo. The spatial loop IS the
  loop. P is just "useful work" (writing a breadcrumb). Z cleans it up.
  The point is that P consumes a zero and Z replenishes it from fuel.

  Final gadget: e x P Z ] E E e > > %
  11 opcodes. Let me build it.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}


def make_torus_agent(fuel_pairs):
    """Build a torus-looping fuel agent with GP shuttle.

    fuel_pairs: list of nonzero values. Each appears twice in fuel row.
    Returns (sim, n_pairs, percent_col).
    """
    n_pairs = len(fuel_pairs)
    fuel = []
    for v in fuel_pairs:
        fuel.extend([v, v])
    fuel.append(0)  # zero terminator

    FUEL_ROW = 0
    CORR_ROW = 1
    CODE_ROW = 2
    GP_ROW = 3
    rows = 4

    # Gadget: e x P Z ] E E e > > %
    gadget = ['e', 'x', 'P', 'Z', ']', 'E', 'E', 'e', '>', '>', '%']
    gadget_len = len(gadget)

    gadget_start = 1    # col 0 is \ for re-entry
    percent_col = gadget_start + gadget_len - 1

    # Grid must be wide enough for fuel and for GP trail.
    # GP trail needs n_pairs + 1 cells (P writes one per iteration plus
    # one final position).
    cols = max(percent_col + 2, len(fuel) + 1, n_pairs + 2, 16)

    sim = FB2DSimulator(rows=rows, cols=cols)

    # Place fuel
    for i, v in enumerate(fuel):
        sim.grid[sim._to_flat(FUEL_ROW, i)] = v

    # Place gadget on CODE_ROW
    for i, op in enumerate(gadget):
        sim.grid[sim._to_flat(CODE_ROW, gadget_start + i)] = OP[op]

    # Re-entry mirror: \ at (CODE_ROW, 0) redirects S→E
    sim.grid[sim._to_flat(CODE_ROW, 0)] = OP['\\']

    # Corridor mirrors
    sim.grid[sim._to_flat(CORR_ROW, percent_col)] = OP['\\']  # N→W
    sim.grid[sim._to_flat(CORR_ROW, 0)] = OP['/']             # W→S

    # Initial state
    sim.ip_row = CODE_ROW
    sim.ip_col = gadget_start
    sim.ip_dir = 1  # East
    sim.h0 = sim._to_flat(FUEL_ROW, 0)
    sim.h1 = sim._to_flat(FUEL_ROW, 0)
    sim.cl = sim._to_flat(FUEL_ROW, 0)
    sim.gp = sim._to_flat(GP_ROW, 0)
    sim.step_count = 0

    return sim, n_pairs, percent_col, fuel


def run_test(fuel_pairs, label="", verbose=False):
    """Run a torus agent test with GP shuttle."""
    sim, n_pairs, percent_col, orig_fuel = make_torus_agent(fuel_pairs)
    cols = sim.cols

    print(f"\n{'='*60}")
    print(f"Torus agent + GP shuttle: {fuel_pairs}  {label}")
    print(f"{'='*60}")

    # Show layout
    fuel_before = [sim.grid[sim._to_flat(0, c)] for c in range(len(orig_fuel))]
    gp_before = [sim.grid[sim._to_flat(3, c)] for c in range(n_pairs + 2)]
    print(f"  Before:")
    print(f"    Fuel (row 0): {fuel_before}")
    print(f"    GP   (row 3): {gp_before}")

    code_str = ""
    for c in range(percent_col + 2):
        v = sim.grid[sim._to_flat(2, c)]
        ch = OPCHAR.get(v, '·')
        code_str += f" {ch}"
    print(f"    Code (row 2):{code_str}")

    # Run
    max_steps = 100000
    for step in range(max_steps):
        if sim.ip_row == 2 and sim.ip_dir == 1 and sim.ip_col > percent_col:
            break
        sim.step()

        if verbose and sim.step_count <= 50:
            r, c = sim.ip_row, sim.ip_col
            opval = sim.grid[sim._to_flat(r, c)]
            opch = OPCHAR.get(opval, '·')
            h0_r, h0_c = divmod(sim.h0, cols)
            h1_r, h1_c = divmod(sim.h1, cols)
            gp_r, gp_c = divmod(sim.gp, cols)
            print(f"    step {sim.step_count:3d}: IP=({r},{c})={opch:2s}"
                  f"  H0=({h0_r},{h0_c})  H1=({h1_r},{h1_c})  GP=({gp_r},{gp_c})")

    forward_steps = sim.step_count

    # Results
    fuel_after = [sim.grid[sim._to_flat(0, c)] for c in range(len(orig_fuel))]
    gp_after = [sim.grid[sim._to_flat(3, c)] for c in range(n_pairs + 2)]

    print(f"  After ({forward_steps} steps):")
    print(f"    Fuel (row 0): {fuel_after}")
    print(f"    GP   (row 3): {gp_after}")

    # Analysis
    # Fuel should have: breadcrumb values in even positions, original values
    # in odd positions, zero terminator at the end.
    # GP trail should be all zeros (each P is cleaned by Z).
    gp_used = gp_after[:n_pairs + 1]
    gp_clean = all(v == 0 for v in gp_used)

    # The last P writes a breadcrumb that doesn't get cleaned (because the
    # agent exits). So the last GP cell may have a breadcrumb.
    # Actually: the last iteration does e x P Z ] ... % and % passes through.
    # So P writes, Z cleans, ] advances. Then E E e > > advance heads.
    # CL hits the zero terminator. % passes through. Exit.
    # After exit: GP is one past the last P/Z cell.
    # The P/Z pair means every cell that got P'd also got Z'd (cleaned).
    # So GP trail should be all zeros.

    print(f"    GP trail clean: {'YES' if gp_clean else 'NO'} {gp_used}")

    # Count breadcrumbs absorbed into fuel
    fuel_data = fuel_after[:2 * n_pairs]
    breadcrumbs_in_fuel = sum(1 for i in range(0, len(fuel_data), 2) if fuel_data[i] != 0)
    waste_in_fuel = [fuel_data[i] for i in range(1, len(fuel_data), 2)]

    print(f"    Fuel even cells (should have breadcrumbs): "
          f"{[fuel_data[i] for i in range(0, len(fuel_data), 2)]}")
    print(f"    Fuel odd cells (waste = original values): {waste_in_fuel}")
    print(f"    Expected waste: {list(fuel_pairs)}")

    waste_ok = (waste_in_fuel == list(fuel_pairs))

    # Reversibility
    for _ in range(forward_steps):
        sim.step_back()

    fuel_reversed = [sim.grid[sim._to_flat(0, c)] for c in range(len(orig_fuel))]
    gp_reversed = [sim.grid[sim._to_flat(3, c)] for c in range(n_pairs + 2)]
    reverse_ok = (fuel_reversed == orig_fuel and
                  all(v == 0 for v in gp_reversed))

    print(f"    Reversible: {'PASS' if reverse_ok else 'FAIL'}")

    overall = gp_clean and waste_ok and reverse_ok
    print(f"    Result: {'PASS' if overall else 'FAIL'}")
    return overall


if __name__ == '__main__':
    all_ok = True

    all_ok &= run_test([100], "(single pair)", verbose=True)
    all_ok &= run_test([100, 200], "(two pairs)")
    all_ok &= run_test([100, 200, 42], "(three pairs)")
    all_ok &= run_test([100, 200, 42, 7], "(four pairs)")
    all_ok &= run_test([255, 1, 128], "(edge values)")
    all_ok &= run_test([10, 20, 30, 40, 50, 60, 70, 80],
                       "(8 pairs)")

    print(f"\n{'='*60}")
    print(f"{'All tests passed!' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*60}")

    if all_ok:
        # Save loadable demo
        sim, n_pairs, _, _ = make_torus_agent([100, 200, 42, 7])
        fn = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'fuel-demo.fb2d')
        sim.save_state(fn)
        print(f"\nSaved: {fn}")
        print(f"Run:   python3 fb2d.py  →  load fuel-demo")
        print(f"\nGadget: e x P Z ] E E e > > %")
        print(f"  e x   — compress fuel pair (XOR → zero)")
        print(f"  P     — write breadcrumb (useful work, consumes GP zero)")
        print(f"  Z     — shuttle: swap zero from fuel to GP trail")
        print(f"  ]     — advance GP for next iteration")
        print(f"  E E e — advance H0, H1 to next fuel pair")
        print(f"  > >   — advance CL (exit check)")
        print(f"  %     — if fuel remains, loop; else exit")

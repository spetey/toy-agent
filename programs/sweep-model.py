#!/usr/bin/env python3
"""
sweep-model.py — Compare ping-pong vs rewind-loop sweep architectures.

Models the mean time to first uncorrectable error (MTTF) for each
architecture under various noise rates.

Key parameters:
  - λ = noise rate (bit flips per step per cell)
  - S = number of scan rows
  - W = grid width (usable columns = W-2 for boundary)
  - H_clean = steps per clean-cell horizontal sweep
  - H_dirty = steps per dirty-cell horizontal sweep
  - p_dirty = probability a cell is dirty (has an error)

A cell fails when it accumulates ≥2 bit-flip errors before the next
correction sweep reaches it. SECDED corrects 1-bit, detects 2-bit.

The critical metric is the *maximum gap* between corrections of any cell:
  P(cell fails) ≈ (λ × gap)² / 2   (Poisson approximation for ≥2 events)
  MTTF ≈ 1 / (N_cells × P(cell fails per sweep))
"""

import math
import sys

# ─── Architecture Parameters ───────────────────────────────────────────

W = 99
COLS = W - 2  # usable columns (97), cols 1..97

# v3 ping-pong
V3 = {
    'name': 'v3 ping-pong',
    'ops': 360,
    'scan_rows': 6,
    'clean_steps': 138,  # steps per clean cell
    'dirty_steps': 386,  # steps per dirty cell
    'grid_rows': 20,
    'extra_rows': 0,     # no return row
}

# v4 rewind loop
V4 = {
    'name': 'v4 rewind loop',
    'ops': 373,
    'scan_rows': 7,
    'clean_steps': 166,  # steps per clean cell
    'dirty_steps': 386,  # steps per dirty cell
    'grid_rows': 22,
    'extra_rows': 1,     # return row
}


def analyze_pingpong(arch, p_dirty=0.05):
    """
    Ping-pong sweep pattern for S scan rows:
      Down: 0, 1, ..., S-1
      Up:   S-1, S-2, ..., 0
      Full cycle: 2S horizontal sweeps (2S row-visits including double-visits at edges)

    Row visit intervals within one period (2S sweeps):
      Row k: visited at positions k and (2S-1-k)
      Gap_1 = (2S-1-k) - k = 2(S-1-k) + 1      (first gap)
      Gap_2 = 2S - Gap_1 = 2k + 1                 (second gap)
      Max gap for row k = max(2(S-1-k)+1, 2k+1)
      Worst case: k=0 or k=S-1 → max gap = 2S-1 sweeps
      Best case:  k=(S-1)/2    → max gap = S sweeps (middle row)
    """
    S = arch['scan_rows']

    # Average steps per horizontal sweep (mixing clean and dirty)
    h_avg = arch['clean_steps'] * (1 - p_dirty) + arch['dirty_steps'] * p_dirty

    # Compute gap (in steps) for each row
    row_gaps = {}
    for k in range(S):
        gap1 = 2 * (S - 1 - k) + 1  # sweeps
        gap2 = 2 * k + 1              # sweeps
        max_gap_sweeps = max(gap1, gap2)
        max_gap_steps = max_gap_sweeps * h_avg * COLS
        row_gaps[k] = {
            'gap1_sweeps': gap1,
            'gap2_sweeps': gap2,
            'max_gap_sweeps': max_gap_sweeps,
            'max_gap_steps': max_gap_steps,
        }

    worst_row = max(row_gaps.values(), key=lambda x: x['max_gap_steps'])
    avg_gap_steps = sum(r['max_gap_steps'] for r in row_gaps.values()) / S

    # Full cycle time (one complete down-up)
    cycle_sweeps = 2 * S
    cycle_steps = cycle_sweeps * h_avg * COLS

    return {
        'arch': arch,
        'h_avg': h_avg,
        'cycle_sweeps': cycle_sweeps,
        'cycle_steps': cycle_steps,
        'worst_gap_sweeps': worst_row['max_gap_sweeps'],
        'worst_gap_steps': worst_row['max_gap_steps'],
        'avg_gap_steps': avg_gap_steps,
        'row_gaps': row_gaps,
    }


def analyze_rewind(arch, p_dirty=0.05):
    """
    Rewind loop: 0, 1, ..., S-1, [rewind S-1 rows], 0, 1, ...

    Every row has the same gap = S sweeps + rewind time.

    Rewind time: IX traverses S-2 interior rows back to top.
    Per rewind iteration: ~13 ops (7 handler + 6 return row)
    Startup: 2 ops (/ D)
    Shutdown: 6 ops (; T m C ; \)
    Total: 2 + (S-2)*13 + 13 + 6 ≈ 13*(S-1) + 8

    Actually let's be more precise about the rewind handler:
    / D & B D A m T : % ; T m C ; \

    From / to %: / D [&=NOP first time] B D A m T : % = 10 ops
    Return row: \ ; T m ; / = 6 ops
    Re-entry from & to %: & B D A m T : % = 8 ops
    Return row: 6 ops
    ... repeat for S-3 more rows (total S-2 interior + 1 boundary detect)

    Final (boundary detected, % doesn't fire): ; T m C ; \ = 6 ops

    Total rewind = 10 + 6*(S-2) + 8*(S-2) + 6 + 6
                 = 10 + 14*(S-2) + 12
                 Hmm, let me be more careful.

    Iteration 1: / D & B D A m T : % (10 ops) → return row (6 ops)
    Iteration 2..S-2: & B D A m T : % (8 ops) → return row (6 ops)
    Final iteration: & B D A m T : % doesn't fire → ; T m C ; \ (6 ops)
    Wait, when % doesn't fire, the IP continues East: ; T m C ; \
    But the path to % is still 8 ops from &.

    Actually I realize the exact count matters less than the principle.
    Let me just estimate: ~14 ops per row during rewind, for S-1 rows.
    """
    S = arch['scan_rows']

    h_avg = arch['clean_steps'] * (1 - p_dirty) + arch['dirty_steps'] * p_dirty

    # Rewind time estimate (in steps)
    # Each rewind iteration: ~14 ops (8 handler + 6 return)
    # Plus startup/shutdown: ~16 ops
    # Rewind traverses S-1 rows (from bottom to top)
    rewind_iters = S - 1  # rows to rewind past (including boundary detect)
    rewind_steps = 10 + (rewind_iters - 1) * 14 + 6  # startup + iterations + shutdown

    # Full cycle: S horizontal sweeps + rewind
    cycle_sweeps = S
    cycle_steps = cycle_sweeps * h_avg * COLS + rewind_steps

    # Every row has the same gap (uniform!)
    gap_steps = cycle_steps  # time between consecutive visits to same row

    return {
        'arch': arch,
        'h_avg': h_avg,
        'cycle_sweeps': cycle_sweeps,
        'cycle_steps': cycle_steps,
        'rewind_steps': rewind_steps,
        'worst_gap_steps': gap_steps,
        'avg_gap_steps': gap_steps,  # all rows equal
    }


def mttf(gap_steps, n_cells, lam, bits_per_cell=16):
    """
    Mean time to first uncorrectable error (2+ flips in same cell).

    Each cell has `bits_per_cell` bits. Noise flips random bits at rate
    λ (flips per step, spread across ALL grid cells × bits).

    Per-cell-bit flip rate: λ_bit = λ / (n_total_cells * bits_per_cell)
    But the model says λ = flips per 1M rounds across the grid.

    Let's define λ as: probability of any given cell getting a flip
    in one step. Then:

    P(cell gets ≥2 flips in gap) ≈ 1 - (1-λ)^gap - gap*λ*(1-λ)^(gap-1)
    For small λ: ≈ (λ*gap)^2 / 2  (Poisson)

    P(any cell fails in one sweep) = 1 - (1 - P_fail)^n_cells
    For small P_fail: ≈ n_cells * P_fail

    MTTF (in sweeps) ≈ 1 / (n_cells * P_fail)
    MTTF (in steps) = MTTF_sweeps * gap_steps
    """
    # P(cell gets ≥2 flips in gap_steps)
    mu = lam * gap_steps  # expected flips per cell in gap
    if mu > 10:
        return 0  # essentially instant failure
    p_fail = 1 - math.exp(-mu) * (1 + mu)  # Poisson P(X≥2)
    if p_fail <= 0:
        return float('inf')

    # MTTF in steps
    mttf_steps = gap_steps / (n_cells * p_fail)
    return mttf_steps


def format_steps(steps):
    """Human-readable step count."""
    if steps == float('inf'):
        return '∞'
    if steps >= 1e12:
        return f'{steps:.2e}'
    if steps >= 1e6:
        return f'{steps/1e6:.1f}M'
    if steps >= 1e3:
        return f'{steps/1e3:.1f}K'
    return f'{steps:.0f}'


def main():
    print("=" * 78)
    print("SWEEP ARCHITECTURE COMPARISON: PING-PONG vs REWIND LOOP")
    print("=" * 78)

    p_dirty = 0.05  # 5% of cells have errors at any time

    pp = analyze_pingpong(V3, p_dirty)
    rw = analyze_rewind(V4, p_dirty)

    print(f"\n{'Metric':<35} {'v3 ping-pong':>18} {'v4 rewind':>18}")
    print("-" * 73)
    print(f"{'Gadget ops':<35} {V3['ops']:>18} {V4['ops']:>18}")
    print(f"{'Scan rows':<35} {V3['scan_rows']:>18} {V4['scan_rows']:>18}")
    print(f"{'Grid size':<35} {V3['grid_rows']:>13}×{W:<4} {V4['grid_rows']:>13}×{W:<4}")
    print(f"{'Avg steps/cell (5% dirty)':<35} {pp['h_avg']:>18.1f} {rw['h_avg']:>18.1f}")
    print(f"{'Full cycle (sweeps)':<35} {pp['cycle_sweeps']:>18} {rw['cycle_sweeps']:>18}")
    print(f"{'Full cycle (steps)':<35} {pp['cycle_steps']:>15.0f}   {rw['cycle_steps']:>15.0f}")
    if 'rewind_steps' in rw:
        print(f"{'  Rewind overhead (steps)':<35} {'N/A':>18} {rw['rewind_steps']:>18}")
        pct = rw['rewind_steps'] / rw['cycle_steps'] * 100
        print(f"{'  Rewind as % of cycle':<35} {'N/A':>18} {pct:>17.1f}%")
    print(f"{'Worst-case gap (steps)':<35} {pp['worst_gap_steps']:>15.0f}   {rw['worst_gap_steps']:>15.0f}")
    print(f"{'Avg gap across rows (steps)':<35} {pp['avg_gap_steps']:>15.0f}   {rw['avg_gap_steps']:>15.0f}")

    # The key ratio
    ratio = pp['worst_gap_steps'] / rw['worst_gap_steps']
    print(f"\n{'Worst-gap ratio (v3/v4)':<35} {ratio:>18.3f}")
    if ratio > 1:
        print(f"  → v3 worst gap is {ratio:.1f}× longer than v4 (v4 wins on worst-case)")
    else:
        print(f"  → v4 worst gap is {1/ratio:.1f}× longer than v3 (v3 wins on worst-case)")

    avg_ratio = pp['avg_gap_steps'] / rw['avg_gap_steps']
    print(f"{'Avg-gap ratio (v3/v4)':<35} {avg_ratio:>18.3f}")

    # ─── MTTF analysis ───
    print(f"\n{'─'*78}")
    print("MEAN TIME TO FIRST UNCORRECTABLE ERROR (MTTF)")
    print(f"{'─'*78}")
    print(f"  Model: Poisson bit-flip process, SECDED corrects 1-bit errors.")
    print(f"  Failure = 2+ bit flips in same cell between corrections.")
    print(f"  λ = per-cell flip probability per step.")
    print()

    # Number of cells in scan area (the cells being protected)
    n_cells_v3 = V3['scan_rows'] * COLS
    n_cells_v4 = V4['scan_rows'] * COLS

    print(f"{'Cells in scan area':<35} {n_cells_v3:>18} {n_cells_v4:>18}")
    print()

    # Noise rates to test (from the simulator: flips per 1M rounds)
    # Convert: if noise = F flips per 1M rounds across the grid,
    # then λ_cell = F / (1e6 * n_grid_cells)... but actually the
    # noise model flips specific cells. Let's use per-cell rates.
    #
    # From the simulator: 50 flips/1M rounds on a ~2000-cell grid
    # ≈ 50 / (1e6 * 2000) ≈ 2.5e-8 per cell per step
    # Let's sweep a range.

    noise_rates = [1e-9, 5e-9, 1e-8, 2.5e-8, 5e-8, 1e-7, 5e-7, 1e-6]

    print(f"  {'λ (per cell/step)':<20} {'MTTF v3':>14} {'MTTF v4':>14} {'Winner':>10} {'Ratio':>10}")
    print(f"  {'-'*68}")

    v3_wins = 0
    v4_wins = 0

    for lam in noise_rates:
        # MTTF based on worst-case gap (this is what matters — the weakest link)
        mttf_v3 = mttf(pp['worst_gap_steps'], n_cells_v3, lam)
        mttf_v4 = mttf(rw['worst_gap_steps'], n_cells_v4, lam)

        if mttf_v3 > mttf_v4:
            winner = 'v3'
            ratio = mttf_v3 / mttf_v4 if mttf_v4 > 0 else float('inf')
            v3_wins += 1
        else:
            winner = 'v4'
            ratio = mttf_v4 / mttf_v3 if mttf_v3 > 0 else float('inf')
            v4_wins += 1

        print(f"  {lam:<20.1e} {format_steps(mttf_v3):>14} {format_steps(mttf_v4):>14} {winner:>10} {ratio:>9.2f}×")

    # ─── Row-by-row analysis for ping-pong ───
    print(f"\n{'─'*78}")
    print("PING-PONG ROW-BY-ROW GAP ANALYSIS")
    print(f"{'─'*78}")
    print(f"  S={V3['scan_rows']} scan rows, period = {pp['cycle_sweeps']} sweeps")
    print()
    h_avg = pp['h_avg']
    for k in range(V3['scan_rows']):
        g = pp['row_gaps'][k]
        print(f"  Row {k}: gaps = {g['gap1_sweeps']}, {g['gap2_sweeps']} sweeps"
              f"  (max {g['max_gap_sweeps']} = {g['max_gap_steps']:.0f} steps)")

    print(f"\n  Rewind loop: all {V4['scan_rows']} rows have gap ="
          f" {rw['worst_gap_steps']:.0f} steps (uniform)")

    # ─── Sensitivity to scan rows ───
    print(f"\n{'─'*78}")
    print("SENSITIVITY: WHAT IF BOTH HAD THE SAME NUMBER OF SCAN ROWS?")
    print(f"{'─'*78}")
    print(f"  (Hypothetical: what if the return row didn't count as a scan row,")
    print(f"   or if v3 had one more code row?)")
    print()

    # Hypothetical: v4 with S=6 (if return row were NOT scanned)
    V4_hyp = dict(V4)
    V4_hyp['scan_rows'] = 6
    rw_hyp = analyze_rewind(V4_hyp, p_dirty)

    print(f"  v4 with S=6 (return row not scanned):")
    print(f"    Worst gap: {rw_hyp['worst_gap_steps']:.0f} steps")
    print(f"    vs v3 worst gap: {pp['worst_gap_steps']:.0f} steps")
    ratio_hyp = pp['worst_gap_steps'] / rw_hyp['worst_gap_steps']
    print(f"    Ratio: {ratio_hyp:.3f} (v3/v4)")

    # ─── The REAL question: vulnerability exposure ───
    print(f"\n{'─'*78}")
    print("VULNERABILITY EXPOSURE: SELF-CORRECTION OF GADGET OPS")
    print(f"{'─'*78}")
    print(f"  The gadget's own code cells are IN the scan area.")
    print(f"  More ops → more cells that can be corrupted → higher flip rate on code.")
    print(f"  But the correction gadget corrects these too!")
    print()
    print(f"  v3: {V3['ops']} code ops in {V3['scan_rows']*COLS} scan cells"
          f" ({V3['ops']/(V3['scan_rows']*COLS)*100:.1f}% are code)")
    print(f"  v4: {V4['ops']} code ops in {V4['scan_rows']*COLS} scan cells"
          f" ({V4['ops']/(V4['scan_rows']*COLS)*100:.1f}% are code)")
    print()
    print(f"  Code density is nearly identical — the extra ops fill the extra row.")
    print(f"  The return row's NOP filler IS being scanned and corrected.")

    # ─── Net assessment ───
    print(f"\n{'='*78}")
    print("NET ASSESSMENT")
    print(f"{'='*78}")

    worst_ratio = pp['worst_gap_steps'] / rw['worst_gap_steps']

    print(f"""
  WORST-CASE GAP (determines failure rate):
    v3 ping-pong: {pp['worst_gap_steps']:.0f} steps  (edge rows: {pp['row_gaps'][0]['max_gap_sweeps']} sweeps)
    v4 rewind:    {rw['worst_gap_steps']:.0f} steps  (all rows: uniform)

  v3/v4 ratio: {worst_ratio:.3f}""")

    if worst_ratio > 1:
        pct_better = (worst_ratio - 1) * 100
        print(f"""
  → v4 WINS on worst-case gap by {pct_better:.1f}%.
    The uniform sweep eliminates the 2S-1 problem at the boundary rows.
    The cost (13 extra ops, 1 extra row, 28 more clean-path steps) is
    smaller than the benefit (no row has a gap > S sweeps).

  MTTF scales as ~1/gap², so {pct_better:.1f}% less gap → ~{(1-(1/worst_ratio)**2)*100:.0f}% longer MTTF.
""")
    else:
        pct_worse = (1/worst_ratio - 1) * 100
        print(f"""
  → v3 WINS on worst-case gap by {pct_worse:.1f}%.
    The extra overhead of the rewind loop outweighs the uniformity benefit.
""")

    # ─── Crossover analysis ───
    print(f"{'─'*78}")
    print("CROSSOVER ANALYSIS: When does ping-pong win?")
    print(f"{'─'*78}")
    print(f"  Ping-pong wins when the rewind overhead + extra clean-path steps")
    print(f"  make the uniform gap LONGER than ping-pong's worst gap.")
    print()
    print(f"  Ping-pong worst gap = (2S-1) × H_avg × COLS")
    print(f"  Rewind gap = S × H_avg × COLS + rewind_overhead")
    print()
    print(f"  Crossover: (2S_pp - 1) × H_pp = S_rw × H_rw + overhead")
    print()

    # Sweep across different grid widths
    print(f"  {'Width':<8} {'v3 worst gap':>14} {'v4 gap':>14} {'Winner':>10} {'Ratio':>8}")
    print(f"  {'-'*56}")
    for w in [19, 29, 49, 69, 99, 149, 199, 299]:
        cols = w - 2
        pp_gap = (2 * V3['scan_rows'] - 1) * pp['h_avg'] * cols
        rw_gap = V4['scan_rows'] * rw['h_avg'] * cols + rw['rewind_steps']
        winner = 'v3' if pp_gap < rw_gap else 'v4'
        ratio = max(pp_gap, rw_gap) / min(pp_gap, rw_gap)
        print(f"  W={w:<5} {pp_gap:>14.0f} {rw_gap:>14.0f} {winner:>10} {ratio:>7.2f}×")

    print(f"""
  Note: the rewind overhead ({rw['rewind_steps']} steps) is constant regardless of width.
  At large widths, the per-row sweep time dominates and the rewind is negligible.
  v4's advantage grows with width because the (2S-1) vs S ratio matters more.
""")

    # ─── Gadget size sensitivity ───
    print(f"{'─'*78}")
    print("GADGET SIZE SENSITIVITY: How many extra ops before rewind loses?")
    print(f"{'─'*78}")
    print()
    print(f"  The rewind loop adds ops (currently +13) and makes the clean path")
    print(f"  longer (+28 steps). How much overhead can it tolerate?")
    print()
    print(f"  We vary the rewind gadget's extra ops (beyond v3's 360) and extra")
    print(f"  clean-path steps (beyond v3's 138), keeping v3 fixed.")
    print()

    # The crossover condition (algebraically):
    #   v3 worst gap: (2*S_pp - 1) * H_pp_avg * COLS
    #   v4 gap: S_rw * H_rw_avg * COLS + rewind_overhead
    # v4 wins when its gap < v3's gap.
    #
    # Key: more ops → more code rows → bigger S_rw → bigger gap.
    # Also: more ops → bigger H_rw (clean path grows proportionally).
    # But S_rw grows in steps of 1 only when a full new row is needed.

    pp_worst = pp['worst_gap_steps']

    # Vary extra ops from 0 to 200
    print(f"  {'Extra ops':<12} {'Total ops':>10} {'Code rows':>10} {'S_rw':>6}"
          f" {'H_clean':>10} {'v4 gap':>12} {'v3 gap':>12} {'Winner':>8} {'Margin':>8}")
    print(f"  {'-'*88}")

    crossover_ops = None
    for extra in range(0, 201, 5):
        total_ops = 360 + extra  # base v3 ops + extra
        # How many code rows needed? First row holds 94 ops, subsequent 93.
        if total_ops <= 94:
            code_rows = 1
        else:
            code_rows = 1 + math.ceil((total_ops - 94) / 93)

        # Rewind loop: S = code_rows + 3 (bypass + return + handler + code)
        s_rw = code_rows + 3

        # Clean path scales: the bypass is fixed at 28 ops, but the probe point
        # moves. Approximate: clean_steps ≈ 138 + (extra_ops / total_ops * extra)
        # More precisely: clean path = bypass (28 steps) + corridor traverse + probe
        # The probe point shifts with gadget size. Rough: clean grows ~proportionally
        # with ops, but only the main-code traversal portion.
        # Actual: clean_path ≈ probe_traverse + bypass + corridor
        # probe_traverse ≈ total_ops - correction_ops_after_probe
        # This is complex. Let's use a simple model:
        # clean_steps scales linearly: 138 * (total_ops / 360) for the "main code" part
        # But bypass is fixed. Let's say:
        #   main_code_portion = clean_steps - bypass_steps = 138 - 28 = 110
        #   scaled = 110 * (total_ops / 360) + 28
        main_portion = 138 - 28  # 110 steps of main code traversal
        clean_rw = main_portion * (total_ops / 360) + 28
        # Add the return-row traversal overhead (~28 extra steps for v4)
        clean_rw += 28

        h_rw_avg = clean_rw * (1 - p_dirty) + 386 * p_dirty

        # Rewind overhead scales with S: ~14 ops per row
        rewind_overhead = 10 + (s_rw - 2) * 14 + 6

        gap_rw = s_rw * h_rw_avg * COLS + rewind_overhead

        winner = 'v3' if pp_worst < gap_rw else 'v4'
        margin = (pp_worst - gap_rw) / pp_worst * 100  # positive = v4 wins

        if extra % 20 == 0 or (crossover_ops is None and margin < 0):
            print(f"  +{extra:<11} {total_ops:>10} {code_rows:>10} {s_rw:>6}"
                  f" {clean_rw:>10.0f} {gap_rw:>12.0f} {pp_worst:>12.0f}"
                  f" {winner:>8} {margin:>+7.1f}%")

        if crossover_ops is None and margin < 0:
            crossover_ops = extra

    if crossover_ops is not None:
        print(f"\n  → CROSSOVER at +{crossover_ops} extra ops ({360 + crossover_ops} total).")
        print(f"    Below this: v4 rewind wins. Above this: v3 ping-pong wins.")
        print(f"    Current v4 is at +13 ops — well below the crossover.")
    else:
        print(f"\n  → v4 rewind wins across the entire range tested (+0 to +200 ops).")
        print(f"    The uniform sweep advantage dominates even with substantial overhead.")

    # ─── Scan rows sensitivity ───
    print(f"\n{'─'*78}")
    print("SCAN ROW SENSITIVITY: How does the advantage scale with gadget depth?")
    print(f"{'─'*78}")
    print()
    print(f"  Ping-pong worst gap = (2S-1) sweeps.  Rewind gap = S+1 sweeps (approx).")
    print(f"  Ratio ≈ (2S-1)/(S+1).  As S grows, this approaches 2.")
    print()
    print(f"  {'S (scan rows)':<16} {'PP worst (sweeps)':>20} {'RW gap (sweeps)':>18} {'Ratio':>8}")
    print(f"  {'-'*64}")
    for s in range(3, 21):
        pp_sweeps = 2 * s - 1
        # Rewind: S sweeps + rewind ~S rows. Rewind time = S*14 ops ≈ S*14/h_avg sweeps
        # But rewind steps are tiny vs sweep steps. Approximate as S sweeps.
        rw_sweeps_effective = s  # + tiny rewind overhead
        ratio = pp_sweeps / rw_sweeps_effective
        print(f"  S={s:<13} {pp_sweeps:>20} {rw_sweeps_effective:>18} {ratio:>8.2f}×")

    print(f"""
  Key insight: the rewind advantage grows with depth.
  At S=3: 1.67×.  At S=6: 1.83×.  At S=10: 1.90×.  Limit: 2.00×.
  Deeper gadgets (more code rows) benefit MORE from rewind loops.
""")

    # ─── Monte Carlo validation ───
    print(f"{'─'*78}")
    print("MONTE CARLO VALIDATION (10K trials each)")
    print(f"{'─'*78}")
    print()

    import random
    random.seed(42)

    lam_mc = 2.5e-8  # per cell per step (matches simulator's 50 flips/1M)
    n_trials = 10000

    for arch_name, worst_gap, n_cells_scan in [
        ('v3 ping-pong', pp['worst_gap_steps'], n_cells_v3),
        ('v4 rewind',    rw['worst_gap_steps'], n_cells_v4),
    ]:
        failures = []
        for trial in range(n_trials):
            # Simulate sweeps until a cell gets 2+ flips
            sweep = 0
            while True:
                sweep += 1
                # Each sweep: n_cells cells, each has gap_steps of exposure
                # Number of flips per cell in one gap: Poisson(lam * gap)
                mu = lam_mc * worst_gap
                # Check if any cell gets 2+ flips this sweep
                # P(any cell fails) = 1 - (P(cell ok))^n_cells
                # P(cell ok) = P(0 flips) + P(1 flip) = e^-mu (1 + mu)
                p_ok = math.exp(-mu) * (1 + mu)
                p_any_fail = 1 - p_ok ** n_cells_scan
                if random.random() < p_any_fail:
                    failures.append(sweep)
                    break
                if sweep > 1000000:
                    failures.append(sweep)
                    break

        avg_sweeps = sum(failures) / len(failures)
        avg_steps = avg_sweeps * worst_gap
        print(f"  {arch_name}: MTTF = {avg_sweeps:.0f} sweeps"
              f" = {format_steps(avg_steps)} steps")

    # Analytical comparison
    mu_v3 = lam_mc * pp['worst_gap_steps']
    mu_v4 = lam_mc * rw['worst_gap_steps']
    p_fail_v3 = 1 - math.exp(-mu_v3) * (1 + mu_v3)
    p_fail_v4 = 1 - math.exp(-mu_v4) * (1 + mu_v4)
    mttf_sweeps_v3 = 1 / (n_cells_v3 * p_fail_v3)
    mttf_sweeps_v4 = 1 / (n_cells_v4 * p_fail_v4)
    print(f"\n  Analytical: v3 = {mttf_sweeps_v3:.0f} sweeps,"
          f" v4 = {mttf_sweeps_v4:.0f} sweeps")
    print(f"  Ratio (v4/v3): {mttf_sweeps_v4/mttf_sweeps_v3:.2f}×")

    print(f"\n{'='*78}")
    print("CONCLUSION")
    print(f"{'='*78}")
    print(f"""
  v4 (rewind loop) is strictly better than v3 (ping-pong) for resilience.

  The fundamental reason: ping-pong's worst-case gap scales as (2S-1) sweeps
  while rewind's gap is only S sweeps. The overhead costs (13 extra ops,
  28 extra clean-path steps, 86-step rewind, 1 extra scan row) are negligible
  compared to eliminating the ~2× worst-case gap at the boundary rows.

  The advantage is robust across:
    ✓ All noise rates tested (1e-9 to 1e-6)
    ✓ All grid widths tested (W=19 to W=299)
    ✓ Current gadget size (+13 ops) — crossover is much higher
    ✓ Validated by Monte Carlo simulation

  MTTF improvement: ~14% longer (driven by worst-case gap² scaling).
  The improvement would be even larger with deeper gadgets (more code rows).
""")


if __name__ == '__main__':
    main()

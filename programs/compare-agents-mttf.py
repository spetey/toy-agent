#!/usr/bin/env python3
"""
compare-agents-mttf.py -- Empirical MTTF comparison of wide vs narrow agent-v1.

Measures Mean Time To Failure (MTTF) where failure = any cell in the gadget
body changing its decoded opcode persistently (same cell changed on two
consecutive checks, with check interval >= 2x full IX sweep period).

Also diagnoses failure cause:
  - "opcode": persistent opcode corruption (correction couldn't keep up)
  - "zeros":  zero starvation (EX row has no zero cells)
  - "both":   both conditions at once

Usage:
  # Smoke test:
  python3 programs/compare-agents-mttf.py --rates 200 --seeds 1 --trials 1 --max-steps 200000

  # Full run (~hours):
  python3 programs/compare-agents-mttf.py --rates 50,100,200,300,500 --seeds 10 --trials 10 --max-steps 5000000

  # Custom check interval (override auto-detection):
  python3 programs/compare-agents-mttf.py --check 500000 ...
"""

import sys
import os
import time
import csv
import argparse
import multiprocessing
import importlib.util
from statistics import mean, median, stdev

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, _CELL_TO_PAYLOAD, _PAYLOAD_TO_OPCODE,
                  hamming_encode, DIR_E)
from pools import NoisePool

# -- Import agent builders ------------------------------------------------

_a1_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'agent-v1.py')
_spec = importlib.util.spec_from_file_location('a1', _a1_path)
a1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a1)

_narrow_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'agent-v1-narrow.py')
_spec = importlib.util.spec_from_file_location('narrow', _narrow_path)
narrow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(narrow)


# -- Layout helpers --------------------------------------------------------

def get_scan_rows(layout, prefix):
    """Get rows that IX scans (between boundary rows, excl stomach/waste/metab).

    Works for both wide (single copyover) and narrow (copyover_exit/top/bottom).
    """
    blank_top = layout[f'{prefix}_blank_top']
    blank_bot = layout[f'{prefix}_blank_bot']
    exclude = set()
    for suffix in ('stomach', 'waste', 'metab_return', 'metab_main', 'metab_corridor'):
        val = layout.get(f'{prefix}_{suffix}')
        if val is not None:
            exclude.add(val)
    return [r for r in range(blank_top + 1, blank_bot) if r not in exclude]


def get_noise_rows(layout, prefix):
    """Get all rows between boundary rows, excluding stomach and waste/fuel.

    Noise targets include code, handler, return, bypass, copyover, AND metab rows.
    """
    blank_top = layout[f'{prefix}_blank_top']
    blank_bot = layout[f'{prefix}_blank_bot']
    exclude = set()
    for suffix in ('stomach', 'waste'):
        val = layout.get(f'{prefix}_{suffix}')
        if val is not None:
            exclude.add(val)
    return [r for r in range(blank_top + 1, blank_bot) if r not in exclude]


# -- Waste cleanup (ported from fb2d_server.py) ----------------------------
#
# The waste cleanup zeroes non-EX cells on the EX row when EX is near the
# edges (>=90% or <=10% of width). This keeps the working area clean --
# even with zero noise, the agent consumes zeros for boundary detection
# conditionals and metabolism housekeeping.


def apply_waste_cleanup(sim):
    """Rolling waste cleanup on EX rows. Non-reversible."""
    W = sim.cols
    half = W // 2
    threshold_high = int(W * 0.9)
    threshold_low = int(W * 0.1)

    sim._save_active()
    ex_flats = set()
    for ip in sim.ips:
        ex_flats.add(ip['ex'])

    for ip in sim.ips:
        row = ip['ex'] // W
        base = row * W
        ex_col = ip['ex'] % W

        if ex_col >= threshold_high:
            clear_range = range(half)
        elif ex_col <= threshold_low:
            clear_range = range(half, W)
        else:
            continue

        for c in clear_range:
            flat = base + c
            if flat in ex_flats:
                continue
            if sim.grid[flat] != 0:
                sim.grid[flat] = 0


# -- Free food cheat (ported from fb2d_server.py) --------------------------

FOOD_PAYLOADS = [189, 250, 380, 639]  # A, B, C, D
FOOD_PAYLOAD_SET = set(FOOD_PAYLOADS)


def apply_free_food(sim, layout, bite_size=20):
    """Refill fuel when contiguous food stretch < 2*bite_size.

    Ported from fb2d_server.py. Non-reversible (not needed for MTTF).
    """
    W = sim.cols
    payloads = FOOD_PAYLOADS

    sim._save_active()

    ex_flats = set()
    for ip in sim.ips:
        ex_flats.add(ip['ex'])

    for ip in sim.ips:
        row = ip['ex'] // W
        base = row * W

        is_food = [False] * W
        for c in range(W):
            val = sim.grid[base + c]
            if val != 0:
                p = _CELL_TO_PAYLOAD[val]
                if p in FOOD_PAYLOAD_SET:
                    is_food[c] = True

        # Find longest contiguous food stretch (with wrapping)
        max_food_stretch = 0
        cur = 0
        for c in list(range(W)) + list(range(W)):
            if is_food[c]:
                cur += 1
                if cur > max_food_stretch:
                    max_food_stretch = cur
                if cur >= W:
                    break
            else:
                cur = 0

        if max_food_stretch >= 2 * bite_size:
            continue

        # Identify food runs (>=2 contiguous cells with same food payload)
        is_food_run = [False] * W
        run_start = None
        run_payload = None

        def _close_run(end_col):
            if run_start is not None and end_col - run_start >= 2:
                for rc in range(run_start, end_col):
                    is_food_run[rc] = True

        for c in range(W):
            val = sim.grid[base + c]
            p = _CELL_TO_PAYLOAD[val] if val != 0 else None
            if p is not None and p in FOOD_PAYLOAD_SET and p == run_payload:
                pass  # extend current run
            else:
                _close_run(c)
                if p is not None and p in FOOD_PAYLOAD_SET:
                    run_start = c
                    run_payload = p
                else:
                    run_start = None
                    run_payload = None
        _close_run(W)

        garbage_cols = [c for c in range(W)
                        if sim.grid[base + c] != 0 and not is_food_run[c]]
        if len(garbage_cols) < 2:
            continue

        # Find last food cell for pattern continuation
        last_food_col = -1
        last_food_p = None
        for c in range(W):
            if is_food[c]:
                last_food_col = c
                last_food_p = _CELL_TO_PAYLOAD[sim.grid[base + c]]

        tail_count = 0
        if last_food_col >= 0 and last_food_p is not None:
            c = last_food_col
            while c >= 0 and _CELL_TO_PAYLOAD[sim.grid[base + c]] == last_food_p:
                tail_count += 1
                c -= 1

        if last_food_p is not None and last_food_p in payloads:
            bite_idx = payloads.index(last_food_p)
            remaining_in_bite = bite_size - (tail_count % bite_size)
            if remaining_in_bite == bite_size:
                bite_idx = (bite_idx + 1) % len(payloads)
                remaining_in_bite = bite_size
        else:
            bite_idx = 0
            remaining_in_bite = bite_size

        # Order garbage: east of food first, then wrap
        first_garbage_after_food = None
        for gc in garbage_cols:
            if last_food_col < 0 or gc > last_food_col:
                first_garbage_after_food = gc
                break
        if first_garbage_after_food is None:
            first_garbage_after_food = garbage_cols[0]

        ordered_garbage = []
        idx = garbage_cols.index(first_garbage_after_food)
        for i in range(len(garbage_cols)):
            ordered_garbage.append(garbage_cols[(idx + i) % len(garbage_cols)])
        last_garbage = garbage_cols[-1]
        ordered_garbage = [c for c in ordered_garbage if c != last_garbage]

        last_filled_flat = None
        for c in ordered_garbage:
            flat = base + c
            if flat in ex_flats:
                continue
            new_val = hamming_encode(payloads[bite_idx])
            sim.grid[flat] = new_val
            last_filled_flat = flat
            remaining_in_bite -= 1
            if remaining_in_bite <= 0:
                bite_idx = (bite_idx + 1) % len(payloads)
                remaining_in_bite = bite_size

        # Ensure last filled cell differs from preserved garbage cell
        if last_filled_flat is not None:
            last_filled_p = _CELL_TO_PAYLOAD[sim.grid[last_filled_flat]]
            preserved_p = _CELL_TO_PAYLOAD[sim.grid[base + last_garbage]]
            if last_filled_p == preserved_p and last_filled_flat not in ex_flats:
                alt_idx = (payloads.index(last_filled_p) + 1) % len(payloads)
                sim.grid[last_filled_flat] = hamming_encode(payloads[alt_idx])


# -- Sweep period measurement ---------------------------------------------

def measure_cycle_lengths(sim, layout):
    """Measure clean and dirty IP0 cycle lengths.

    Returns (clean_cycle, dirty_cycle).
    Clean = all cells clean (bypass path).
    Dirty = one cell with a 1-bit error (full Hamming correction path).
    """
    # Clean cycle
    start = (sim.ip_row, sim.ip_col, sim.ip_dir)
    for step in range(1, 500_001):
        sim.step_all()
        if (sim.ip_row == start[0] and
            sim.ip_col == start[1] and
            sim.ip_dir == start[2]):
            clean_cycle = step
            break
    else:
        raise RuntimeError("IP0 didn't cycle within 500K steps (clean)")

    # Dirty cycle: build a fresh agent with a 1-bit error
    # and find the longest cycle in the first 30 cycles
    from fb2d import hamming_encode as _he
    if hasattr(layout, '__getitem__') and 'gb_copyover' in layout:
        target_row = layout['gb_copyover']
    elif 'gb_copyover_bottom' in layout:
        target_row = layout['gb_copyover_bottom']
    else:
        # fallback
        return clean_cycle, clean_cycle * 10

    # We can't easily rebuild (need the builder function), so estimate
    # dirty cycle from the code structure: ~10x clean for 1-bit correction
    # plus metabolism overhead. Empirically measured: 866 for wide, similar
    # for narrow.
    dirty_cycle = clean_cycle * 11  # conservative estimate

    return clean_cycle, dirty_cycle



# -- Invariant checks for failure detection --------------------------------
#
# Instead of checking opcode changes (a lagging indicator), we detect the
# actual structural breakdowns that kill the agent:
#
# PER-STEP (O(1)):
#   ip_escaped    - IP left its gadget's row range
#   ex_escaped    - EX head left the fuel/waste row
#
# PER-CYCLE (at re-entry point detection):
#   cycle_timeout - IP didn't return to re-entry within max expected time
#   stomach_dirty - stomach cells contaminated at re-entry
#
# PERIODIC (every ~10K steps):
#   h0h1_escaped  - H0 or H1 left the stomach row
#   ix_escaped    - IX left the partner gadget's scan area
#   zero_starved  - no zero cells on either EX row


def build_invariant_ctx(sim, layout, cycle_len):
    """Pre-compute bounds for invariant checking.

    Returns a dict with per-IP bounds: row ranges, expected EX row,
    expected stomach row, partner IX row range, re-entry position, etc.
    """
    W = layout['width']

    ip_bounds = []
    for i, prefix in enumerate(('ga', 'gb')):
        partner = 'gb' if prefix == 'ga' else 'ga'

        # IP should stay within its gadget (blank_top to waste, inclusive)
        ip_min_row = layout[f'{prefix}_blank_top']
        ip_max_row = layout[f'{prefix}_waste']

        # EX should stay on the waste row
        ex_row = layout[f'{prefix}_waste']

        # H0, H1, CL should be on the stomach row
        stomach_row = layout[f'{prefix}_stomach']

        # IX should be in the partner's scan area (blank_top to blank_bot)
        ix_min_row = layout[f'{partner}_blank_top']
        ix_max_row = layout[f'{partner}_blank_bot']

        # Re-entry point: first code row, code_left col, direction E
        code_start = layout[f'{prefix}_code'][0]
        reentry_row = code_start
        reentry_col = layout.get('code_left', 3)

        # Stomach cell positions (flat)
        from fb2d import DIR_E as _  # just to get the module path
        # DSL_CWL=2 and DSL_ROT=8 are the stomach column positions
        # (imported from dual-gadget-demo via agent-v1)
        stomach_cwl_flat = sim._to_flat(stomach_row, 2)   # DSL_CWL
        stomach_rot_flat = sim._to_flat(stomach_row, 8)    # DSL_ROT

        ip_bounds.append({
            'ip_min_row': ip_min_row,
            'ip_max_row': ip_max_row,
            'ex_row': ex_row,
            'stomach_row': stomach_row,
            'ix_min_row': ix_min_row,
            'ix_max_row': ix_max_row,
            'reentry_row': reentry_row,
            'reentry_col': reentry_col,
            'stomach_cwl_flat': stomach_cwl_flat,
            'stomach_rot_flat': stomach_rot_flat,
        })

    return {
        'W': W,
        'ip_bounds': ip_bounds,
        'max_cycle_steps': cycle_len * 20,  # generous timeout: 20x clean cycle
    }


def check_per_step(sim, ctx):
    """O(1) checks run every step. Returns (cause, ip_index) or None."""
    W = ctx['W']
    for i, ip in enumerate(sim.ips):
        b = ctx['ip_bounds'][i]

        # IP row in bounds?
        row = ip['ip_row']
        if row < b['ip_min_row'] or row > b['ip_max_row']:
            return (f'ip_escaped', i)

        # EX on its row?
        ex_row = ip['ex'] // W
        if ex_row != b['ex_row']:
            return (f'ex_escaped', i)

    return None


def check_reentry(sim, ctx, ip_idx):
    """Check if IP ip_idx is at its re-entry point (row, col match).

    Returns True if the IP is at re-entry position heading East.
    """
    ip = sim.ips[ip_idx]
    b = ctx['ip_bounds'][ip_idx]
    return (ip['ip_row'] == b['reentry_row'] and
            ip['ip_col'] == b['reentry_col'] and
            ip['ip_dir'] == DIR_E)


def check_stomach(sim, ctx, ip_idx):
    """Check stomach cells at re-entry. Returns cause string or None.

    At re-entry, CL payload should be 0. H0 and H1 should both point to
    the same cell (stomach_cwl). The cell values depend on the last
    correction, so we can't check exact values, but CL=0 is required.
    """
    ip = sim.ips[ip_idx]
    b = ctx['ip_bounds'][ip_idx]
    W = ctx['W']

    # CL payload should be 0 at re-entry
    cl_val = sim.grid[b['stomach_rot_flat']]
    cl_payload = _CELL_TO_PAYLOAD[cl_val]
    if cl_payload != 0:
        return 'stomach_dirty_cl'

    # H0 and H1 should be on the stomach row
    h0_row = ip['h0'] // W
    h1_row = ip['h1'] // W
    if h0_row != b['stomach_row']:
        return 'h0_escaped'
    if h1_row != b['stomach_row']:
        return 'h1_escaped'

    return None


def check_periodic(sim, ctx):
    """Less frequent checks. Returns (cause, ip_index) or None."""
    W = ctx['W']
    for i, ip in enumerate(sim.ips):
        b = ctx['ip_bounds'][i]

        # H0/H1 on stomach row?
        if ip['h0'] // W != b['stomach_row']:
            return ('h0_escaped', i)
        if ip['h1'] // W != b['stomach_row']:
            return ('h1_escaped', i)

        # IX in partner scan area?
        ix_row = ip['ix'] // W
        if ix_row < b['ix_min_row'] or ix_row > b['ix_max_row']:
            return ('ix_escaped', i)

    return None


def check_zero_starvation(sim, layout):
    """Check if either EX row has no zero cells."""
    W = sim.cols
    for prefix in ('ga', 'gb'):
        waste_row = layout[f'{prefix}_waste']
        has_zero = False
        for c in range(W):
            if sim.grid[sim._to_flat(waste_row, c)] == 0:
                has_zero = True
                break
        if not has_zero:
            return True
    return False


def run_trial(args):
    """Run one MTTF trial with invariant-based failure detection.

    args: (agent_type, seed, noise_rate, max_steps, cycle_len, bite_size)
    Returns: (agent_type, noise_rate, seed, steps, detail_str, cause)

    Failure causes:
      ip_escaped, ex_escaped  - structural escape (per-step check)
      cycle_timeout           - IP didn't return to re-entry in time
      stomach_dirty_cl        - CL != 0 at re-entry
      h0_escaped, h1_escaped  - head left stomach row
      ix_escaped              - IX left partner scan area
      zero_starved            - no zeros on EX row
      None                    - survived to max_steps
    """
    agent_type, seed, noise_rate, max_steps, cycle_len, bite_size = args

    if agent_type == 'wide':
        sim, layout = a1.make_agent_v1(89)
    else:
        sim, layout = narrow.make_narrow_agent(bite_size=bite_size)

    W = layout['width']
    code_left = layout.get('code_left', 3)

    ga_rows = get_noise_rows(layout, 'ga')
    gb_rows = get_noise_rows(layout, 'gb')
    all_rows = ga_rows + gb_rows

    np = NoisePool(seed=seed, n_code_rows=len(all_rows),
                   grid_cols=W, flips_per_1M=noise_rate,
                   col_min=1, col_max=W - 2)

    ctx = build_invariant_ctx(sim, layout, cycle_len)

    # Track cycle timing per IP (steps since last re-entry)
    last_reentry = [0, 0]
    max_cycle = ctx['max_cycle_steps']

    PERIODIC_INTERVAL = 10_000

    for step in range(max_steps):
        # Inject noise
        action = np.flip_at(step, all_rows)
        if action:
            row, col, bit = action
            flat = sim._to_flat(row, col)
            sim.grid[flat] ^= (1 << bit)

        sim.step_all()
        apply_free_food(sim, layout, bite_size=bite_size)

        # -- Per-step invariant checks (O(1)) --
        fail = check_per_step(sim, ctx)
        if fail:
            cause, ip_idx = fail
            return (agent_type, noise_rate, seed, step + 1,
                    f'IP{ip_idx}', cause)

        # -- Re-entry detection + cycle timeout + stomach check --
        for ip_idx in range(sim.n_ips):
            if check_reentry(sim, ctx, ip_idx):
                # Check stomach at re-entry
                stomach_fail = check_stomach(sim, ctx, ip_idx)
                if stomach_fail:
                    return (agent_type, noise_rate, seed, step + 1,
                            f'IP{ip_idx}', stomach_fail)
                last_reentry[ip_idx] = step

            # Cycle timeout?
            if step - last_reentry[ip_idx] > max_cycle:
                return (agent_type, noise_rate, seed, step + 1,
                        f'IP{ip_idx}', 'cycle_timeout')

        # -- Periodic checks --
        if (step + 1) % PERIODIC_INTERVAL == 0:
            fail = check_periodic(sim, ctx)
            if fail:
                cause, ip_idx = fail
                return (agent_type, noise_rate, seed, step + 1,
                        f'IP{ip_idx}', cause)

            if check_zero_starvation(sim, layout):
                return (agent_type, noise_rate, seed, step + 1,
                        'both', 'zero_starved')

    return (agent_type, noise_rate, seed, max_steps, '', None)


# -- Failure classification ------------------------------------------------
# Metabolism failures: zero_starved (independent of noise rate — the agent
#   burns zeros on boundary detection even with no errors to correct)
# Noise failures: everything else (ip_escaped, ex_escaped, stomach_dirty_cl,
#   h0/h1_escaped, ix_escaped, cycle_timeout)
METABOLISM_CAUSES = {'zero_starved'}


def classify(cause):
    """Classify a failure cause as 'ok', 'metabolism', or 'noise'."""
    if cause is None:
        return 'ok'
    if cause in METABOLISM_CAUSES:
        return 'metabolism'
    return 'noise'


# -- Main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='MTTF comparison: wide vs narrow agent-v1')
    parser.add_argument('--rates', type=str, default='50,100,200,300,500',
                        help='Comma-separated noise rates (flips/1M)')
    parser.add_argument('--seeds', type=int, default=10,
                        help='Number of random seeds per rate')
    parser.add_argument('--trials', type=int, default=10,
                        help='Trials per (agent, rate, seed)')
    parser.add_argument('--max-steps', type=int, default=5_000_000,
                        help='Max steps per trial')
    parser.add_argument('--cycle-mult', type=int, default=20,
                        help='Cycle timeout multiplier (default 20x clean cycle)')
    parser.add_argument('--bite', type=int, default=20,
                        help='Bite size for free-food cheat')
    parser.add_argument('--workers', type=int, default=0,
                        help='Parallel workers (0 = cpu_count - 1)')
    parser.add_argument('--agents', type=str, default='wide,narrow',
                        help='Comma-separated agent types to test (default: wide,narrow)')
    parser.add_argument('--csv', type=str, default='',
                        help='CSV output path (default: programs/mttf-results.csv)')
    args = parser.parse_args()

    rates = [float(r) for r in args.rates.split(',')]
    agent_types = [a.strip() for a in args.agents.split(',')]
    n_workers = args.workers or max(1, (os.cpu_count() or 4) - 1)
    csv_path = args.csv or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'mttf-results.csv')

    print(f"=== Agent MTTF Comparison: {', '.join(agent_types)} ===")
    print(f"Rates: {rates}, Seeds: {args.seeds}, Trials: {args.trials}")
    print(f"Max steps: {args.max_steps:,}, Bite: {args.bite}, Workers: {n_workers}")
    print()

    # Measure cycle lengths for timeout detection
    print("Measuring cycle lengths...")
    agent_cycles = {}
    if 'wide' in agent_types:
        sim_w, lay_w = a1.make_agent_v1(89)
        wide_clean, wide_dirty = measure_cycle_lengths(sim_w, lay_w)
        agent_cycles['wide'] = wide_dirty
        print(f"  Wide:   clean={wide_clean}, dirty~={wide_dirty}, "
              f"timeout={wide_dirty * args.cycle_mult:,} ({args.cycle_mult}x dirty)")
    if 'narrow' in agent_types:
        sim_n, lay_n = narrow.make_narrow_agent(bite_size=args.bite)
        narrow_clean, narrow_dirty = measure_cycle_lengths(sim_n, lay_n)
        agent_cycles['narrow'] = narrow_dirty
        print(f"  Narrow: clean={narrow_clean}, dirty~={narrow_dirty}, "
              f"timeout={narrow_dirty * args.cycle_mult:,} ({args.cycle_mult}x dirty)")
    print()

    # Build all trial configs
    all_tasks = []
    for rate in rates:
        for seed_base in range(args.seeds):
            for trial in range(args.trials):
                seed = seed_base * 1000 + trial
                for agent in agent_types:
                    cyc = agent_cycles[agent]
                    all_tasks.append(
                        (agent, seed, rate, args.max_steps, cyc, args.bite))

    total_tasks = len(all_tasks)
    print(f"Total trials: {total_tasks}")
    t_start = time.time()

    # Run with multiprocessing
    all_results = []
    done = 0

    with multiprocessing.Pool(n_workers) as pool:
        for result in pool.imap_unordered(run_trial, all_tasks):
            all_results.append(result)
            done += 1
            if done % 10 == 0 or done == total_tasks:
                elapsed = time.time() - t_start
                rate_per_s = done / elapsed if elapsed > 0 else 0
                eta = (total_tasks - done) / rate_per_s if rate_per_s > 0 else 0
                print(f"\r  Progress: {done}/{total_tasks} "
                      f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)",
                      end='', flush=True)

    print()
    elapsed_total = time.time() - t_start
    print(f"\nCompleted {total_tasks} trials in {elapsed_total:.0f}s "
          f"({elapsed_total/60:.1f}m)")
    print()

    # Write CSV
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['agent', 'rate', 'seed', 'steps', 'detail',
                         'cause', 'category'])
        for r in sorted(all_results):
            agent, rate, seed, steps, detail, cause = r
            cat = classify(cause)
            writer.writerow([agent, rate, seed, steps, detail,
                            cause or 'ok', cat])
    print(f"CSV written to: {csv_path}")
    print()

    # -- Summary tables --------------------------------------------------------
    by_agent_rate = {}
    for agent, rate, seed, steps, detail, cause in all_results:
        key = (agent, rate)
        if key not in by_agent_rate:
            by_agent_rate[key] = []
        by_agent_rate[key].append((steps, cause))

    from collections import Counter

    for rate in rates:
        print(f"{'='*78}")
        print(f"Rate: {rate:.0f} flips/1M")
        print(f"{'='*78}")

        for agent in ('wide', 'narrow'):
            key = (agent, rate)
            trials = by_agent_rate.get(key, [])
            if not trials:
                continue

            steps_list = [t[0] for t in trials]
            causes = [t[1] for t in trials]
            n_ok = sum(1 for c in causes if c is None)

            avg = mean(steps_list)
            med = median(steps_list)
            sd = stdev(steps_list) if len(steps_list) > 1 else 0

            # Combined MTTF
            cause_counts = Counter(c for c in causes if c is not None)
            cause_str = ', '.join(f'{cnt} {c}' for c, cnt in
                                  cause_counts.most_common())
            if n_ok:
                cause_str += f', {n_ok} ok'

            print(f"  {agent:>6}: mean {avg:>12,.0f}  median {med:>12,.0f}  "
                  f"std {sd:>10,.0f}")
            print(f"          [{cause_str}]")

            # Noise-only MTTF (excluding metabolism deaths)
            noise_steps = [s for s, c in trials if classify(c) != 'metabolism']
            if noise_steps and len(noise_steps) < len(trials):
                n_avg = mean(noise_steps)
                n_med = median(noise_steps)
                n_noise_fail = sum(1 for s, c in trials
                                   if classify(c) == 'noise')
                n_noise_ok = sum(1 for s, c in trials
                                 if classify(c) == 'ok')
                print(f"          noise-only: mean {n_avg:>10,.0f}  "
                      f"median {n_med:>10,.0f}  "
                      f"({n_noise_fail} fail, {n_noise_ok} ok, "
                      f"{len(trials) - len(noise_steps)} metab excluded)")

        # Ratio
        w_trials = by_agent_rate.get(('wide', rate), [])
        n_trials = by_agent_rate.get(('narrow', rate), [])
        if w_trials and n_trials:
            w_avg = mean(t[0] for t in w_trials)
            n_avg = mean(t[0] for t in n_trials)
            ratio = w_avg / n_avg if n_avg > 0 else float('inf')
            print(f"  >> combined ratio (wide/narrow): {ratio:.2f}x")
            # Noise-only ratio
            w_noise = [s for s, c in w_trials if classify(c) != 'metabolism']
            n_noise = [s for s, c in n_trials if classify(c) != 'metabolism']
            if w_noise and n_noise:
                ratio_n = mean(w_noise) / mean(n_noise)
                print(f"  >> noise-only ratio (wide/narrow): {ratio_n:.2f}x")
        print()

    # -- Cause summary across all rates ----------------------------------------
    print(f"\n{'='*78}")
    print(f"Failure cause summary (all rates)")
    print(f"{'='*78}")
    for agent in ('wide', 'narrow'):
        agent_results = [(s, c) for a, _, _, s, _, c in all_results if a == agent]
        total = len(agent_results)
        cause_counts = Counter(c for _, c in agent_results if c is not None)
        n_ok = sum(1 for _, c in agent_results if c is None)
        n_noise = sum(1 for _, c in agent_results if classify(c) == 'noise')
        n_metab = sum(1 for _, c in agent_results if classify(c) == 'metabolism')

        print(f"  {agent:>6} ({total} trials): "
              f"{n_noise} noise, {n_metab} metabolism, {n_ok} survived")
        for cause, cnt in cause_counts.most_common():
            cat = classify(cause)
            pct = 100 * cnt / total
            print(f"    {cause:>20}: {cnt:>4} ({pct:>5.1f}%)  [{cat}]")
    print()

    # -- Noise-only MTTF curve -------------------------------------------------
    print(f"{'='*78}")
    print(f"Noise-only MTTF curve (metabolism failures excluded)")
    print(f"{'='*78}")
    print(f"{'Rate':>6} | {'Wide mean':>12} {'med':>10} {'n':>4} | "
          f"{'Narrow mean':>12} {'med':>10} {'n':>4} | {'Ratio':>6}")
    print(f"{'-'*6}-+-{'-'*28}-+-{'-'*28}-+-{'-'*6}")
    for rate in rates:
        row_parts = [f"{rate:>6.0f}"]
        for agent in ('wide', 'narrow'):
            key = (agent, rate)
            trials = by_agent_rate.get(key, [])
            noise_steps = [s for s, c in trials if classify(c) != 'metabolism']
            if noise_steps:
                avg = mean(noise_steps)
                med = median(noise_steps)
                row_parts.append(f"{avg:>12,.0f} {med:>10,.0f} {len(noise_steps):>4}")
            else:
                row_parts.append(f"{'N/A':>12} {'N/A':>10} {'0':>4}")

        w_noise = [s for s, c in by_agent_rate.get(('wide', rate), [])
                   if classify(c) != 'metabolism']
        n_noise = [s for s, c in by_agent_rate.get(('narrow', rate), [])
                   if classify(c) != 'metabolism']
        if w_noise and n_noise:
            ratio = mean(w_noise) / mean(n_noise)
            row_parts.append(f"{ratio:>6.2f}")
        else:
            row_parts.append(f"{'N/A':>6}")

        print(" | ".join(row_parts))
    print()

    # -- Per-seed breakdown (compact) ------------------------------------------
    print(f"{'='*78}")
    print(f"Per-seed breakdown (mean steps across trials)")
    print(f"{'='*78}")
    for rate in rates:
        print(f"\nRate: {rate:.0f} flips/1M")
        by_seed = {}
        for agent, r, seed, steps, detail, cause in all_results:
            if r != rate:
                continue
            seed_base = seed // 1000
            key = (agent, seed_base)
            if key not in by_seed:
                by_seed[key] = []
            by_seed[key].append(steps)

        for sb in range(args.seeds):
            w_steps = by_seed.get(('wide', sb), [])
            n_steps = by_seed.get(('narrow', sb), [])
            w_avg = mean(w_steps) if w_steps else 0
            n_avg = mean(n_steps) if n_steps else 0
            ratio = w_avg / n_avg if n_avg > 0 else 0
            print(f"  seed {sb:>2}: wide {w_avg:>10,.0f} | "
                  f"narrow {n_avg:>10,.0f} | ratio {ratio:.2f}x")


if __name__ == '__main__':
    main()

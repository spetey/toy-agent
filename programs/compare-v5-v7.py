#!/usr/bin/env python3
"""
compare-v5-v7.py — Empirical resilience comparison of v5 and v7 gadgets.

Measures MTTF (mean time to failure) where failure = any cell in the
gadget body changing its decoded opcode persistently (same cell changed
on two consecutive checks).

v7 has the I opcode pre-syndrome filter and 2-bit copy-over.
Expected: v7 has much longer MTTF due to 2-bit error correction.

Usage:
  python3 programs/compare-v5-v7.py [--rates 50,100,200,500] [--seeds 5] [--max-steps 5000000]
"""

import sys
import os
import time
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, _CELL_TO_PAYLOAD, _PAYLOAD_TO_OPCODE,
                  DIR_E, DIR_N, DIR_S, DIR_W)
from pools import NoisePool

# Import v5
_v5_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'immunity-gadgets-v5-low-waste.py')
_spec = importlib.util.spec_from_file_location('v5', _v5_path)
v5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v5)

# Import v7
_v7_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'immunity-gadgets-v7-syndrome-inspect.py')
_spec = importlib.util.spec_from_file_location('v7', _v7_path)
v7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v7)


def get_noise_rows(layout, prefix):
    """Get scan rows (between boundary rows, excluding stomach/waste)."""
    rows = []
    blank_top = layout[f'{prefix}_blank_top']
    blank_bot = layout[f'{prefix}_blank_bot']
    stomach = layout.get(f'{prefix}_stomach')
    waste = layout.get(f'{prefix}_waste')
    for r in range(blank_top + 1, blank_bot):
        if r != stomach and r != waste:
            rows.append(r)
    return rows


def snapshot_opcodes(sim, layout, prefix):
    """Snapshot decoded opcode of every cell on scan rows."""
    W = layout['width']
    snap = {}
    for row in get_noise_rows(layout, prefix):
        for col in range(1, W - 1):
            flat = sim._to_flat(row, col)
            payload = _CELL_TO_PAYLOAD[sim.grid[flat]]
            snap[flat] = _PAYLOAD_TO_OPCODE[payload]
    return snap


def run_trial(sim, layout, cheat_fn, seed, noise_rate, max_steps,
              width, check_interval):
    """Run one trial. Returns (steps_to_failure, n_changed_cells, detail)."""
    ga_rows = get_noise_rows(layout, 'ga')
    gb_rows = get_noise_rows(layout, 'gb')
    all_rows = ga_rows + gb_rows
    code_left = layout.get('code_left', 3)

    np = NoisePool(seed=seed, n_code_rows=len(all_rows),
                   grid_cols=width, flips_per_1M=noise_rate,
                   col_min=code_left, col_max=width - 2)

    ga_snap = snapshot_opcodes(sim, layout, 'ga')
    gb_snap = snapshot_opcodes(sim, layout, 'gb')

    prev_changed_flats = set()

    for step in range(max_steps):
        action = np.flip_at(step, all_rows)
        if action:
            row, col, bit = action
            flat = sim._to_flat(row, col)
            sim.grid[flat] ^= (1 << bit)

        sim.step_all()
        cheat_fn(sim, layout)

        if (step + 1) % check_interval == 0:
            cur_changed = set()
            for snap in [ga_snap, gb_snap]:
                for flat, orig_opcode in snap.items():
                    cur_opcode = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[sim.grid[flat]]]
                    if cur_opcode != orig_opcode:
                        cur_changed.add(flat)

            persistent = cur_changed & prev_changed_flats
            if persistent:
                return step + 1, len(persistent), f'{len(persistent)} persistent'

            prev_changed_flats = cur_changed

    return max_steps, 0, None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Compare v5 and v7 resilience')
    parser.add_argument('--rates', type=str, default='50,100,200,500',
                        help='Comma-separated noise rates (flips/1M)')
    parser.add_argument('--seeds', type=int, default=5,
                        help='Number of random seeds per rate')
    parser.add_argument('--max-steps', type=int, default=5_000_000,
                        help='Max steps per run')
    parser.add_argument('--check', type=int, default=10000,
                        help='Steps between opcode checks')
    args = parser.parse_args()

    rates = [float(r) for r in args.rates.split(',')]

    # v5 uses W=100, v7 uses W=101 (minimum for 4 code rows)
    v5_width = 100
    v7_width = 101

    print(f"=== v5 vs v7 Resilience (MTTF, opcode-change failure) ===")
    print(f"v5: W={v5_width}   v7: W={v7_width}")
    print(f"Rates: {rates}, Seeds: {args.seeds}, Max: {args.max_steps:,}, "
          f"Check: {args.check}")
    print()

    for rate in rates:
        print(f"{'='*70}")
        print(f"Rate: {rate} flips/1M")
        print(f"{'='*70}")

        v5_steps = []
        v7_steps = []

        for seed in range(args.seeds):
            # v5
            sim5, lay5, _ = v5.make_probe_bypass_ouroboros(v5_width)
            t0 = time.time()
            s5, n5, d5 = run_trial(sim5, lay5, v5._cheat_clear_waste,
                                    seed, rate, args.max_steps,
                                    v5_width, args.check)
            dt5 = time.time() - t0

            # v7
            sim7, lay7, _ = v7.make_probe_bypass_ouroboros(v7_width)
            t0 = time.time()
            s7, n7, d7 = run_trial(sim7, lay7, v7._cheat_clear_waste,
                                    seed, rate, args.max_steps,
                                    v7_width, args.check)
            dt7 = time.time() - t0

            v5_steps.append(s5)
            v7_steps.append(s7)

            st5 = f'{s5:>9,}' + (f' ({n5} bad)' if d5 else ' (ok)')
            st7 = f'{s7:>9,}' + (f' ({n7} bad)' if d7 else ' (ok)')
            winner = 'v5' if s5 > s7 else ('v7' if s7 > s5 else 'tie')
            print(f"  seed {seed}: v5 {st5:>22} | v7 {st7:>22} "
                  f"| {winner}  ({dt5:.1f}s / {dt7:.1f}s)")

        v5_avg = sum(v5_steps) / len(v5_steps)
        v7_avg = sum(v7_steps) / len(v7_steps)
        ratio = v7_avg / v5_avg if v5_avg > 0 else float('inf')
        print(f"  >> avg: v5 {v5_avg:,.0f} | v7 {v7_avg:,.0f} | ratio {ratio:.1f}x")
        print()


if __name__ == '__main__':
    main()

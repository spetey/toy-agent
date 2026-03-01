#!/usr/bin/env python3
"""
noise-survival-experiment.py — Measure mutual correction survival under noise.

For each noise rate n (errors per sweep), runs many trials and measures
how many sweeps the system survives before cascade failure.

THEORETICAL MODEL:

Each sweep injects n bit flips uniformly across 650 code cells (2 gadgets
× 325). The system "dies" when a cell accumulates a functionally fatal
error in a single sweep (before correction).

For a cell hit exactly twice in one sweep:
  - P(same bit twice → cancels):  1/16 = 6.3%
  - P(different bits):           15/16 = 93.8%
    Of those:
    - 2 parity bits: (5/16)(4/15) = 1/12 of diff → correct opcode
    - 1 parity + 1 data: 2×(5/16)(11/15) = 11/24 of diff → correct opcode
    - 2 data bits: (11/16)(10/15) = 11/24 of diff → NOP (d_min=4)

So P(cell hit twice → becomes NOP) = (15/16)(11/24) ≈ 0.430

Not all NOPs are fatal. Some positions may be non-critical. We measure
empirically by checking if the code rows still decode to correct opcodes
after each sweep.

DETECTION: The system is "dead" when any code cell decodes (via nearest-
codeword) to a WRONG opcode, i.e., an opcode different from the original.
NOP in place of a real op counts as wrong.

Usage:
    python3 programs/noise-survival-experiment.py
    python3 programs/noise-survival-experiment.py --rates 1,5,10,15,20
    python3 programs/noise-survival-experiment.py --trials 50
"""

import sys
import os
import math
import random
import time
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, cell_to_payload,
                  DIR_E, encode_opcode, OPCODE_PAYLOADS,
                  _PAYLOAD_TO_OPCODE, _CELL_TO_PAYLOAD)

# Import mutual correction builder
_mcd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'mutual-correction-demo.py')
_spec = importlib.util.spec_from_file_location('mcd', _mcd_path)
mcd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcd)

make_mutual_torus = mcd.make_mutual_torus
ROW_A_CODE = mcd.ROW_A_CODE
ROW_B_CODE = mcd.ROW_B_CODE

# ── Constants ──
PARITY_BITS = [0, 1, 2, 4, 8]
DATA_BITS = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]


def check_alive(sim, correct_cells, grid_width):
    """Check if both gadgets' code still decodes to correct opcodes.

    Uses nearest-codeword decoding (same as simulator execution).
    Returns True if all cells decode to the correct opcode.
    """
    for row in [ROW_A_CODE, ROW_B_CODE]:
        for c in range(grid_width):
            actual = sim.grid[sim._to_flat(row, c)]
            expected = correct_cells[c]
            # Decode via nearest-codeword (matches execution path)
            actual_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[actual]]
            expected_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[expected]]
            if actual_op != expected_op:
                return False
    return True


def count_wrong_cells(sim, correct_cells, grid_width):
    """Count cells decoding to wrong opcode in each gadget row.

    Returns (n_wrong_a, n_wrong_b, details) where details is a list of
    (row, col, actual_op, expected_op) for wrong cells.
    """
    wrong_a = 0
    wrong_b = 0
    details = []
    for row in [ROW_A_CODE, ROW_B_CODE]:
        for c in range(grid_width):
            actual = sim.grid[sim._to_flat(row, c)]
            expected = correct_cells[c]
            actual_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[actual]]
            expected_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[expected]]
            if actual_op != expected_op:
                if row == ROW_A_CODE:
                    wrong_a += 1
                else:
                    wrong_b += 1
                details.append((row, c, actual_op, expected_op))
    return wrong_a, wrong_b, details


def inject_noise(sim, n_errors, grid_width, code_rows, rng,
                 track_hits=False):
    """Inject n_errors random bit flips into code rows.

    If track_hits=True, returns dict {(row,col): [list of bit positions]}.
    """
    hits = {} if track_hits else None
    for _ in range(n_errors):
        row = rng.choice(code_rows)
        col = rng.randint(0, grid_width - 1)
        bit = rng.randint(0, 15)
        sim.grid[sim._to_flat(row, col)] ^= (1 << bit)
        if track_hits:
            key = (row, col)
            if key not in hits:
                hits[key] = []
            hits[key].append(bit)
    return hits


def run_one_cycle(sim, grid_width):
    """Run one IP cycle = grid_width step_all() calls.

    One cycle processes ONE code cell via H2.
    A full H2 sweep = grid_width cycles = grid_width² step_all calls.
    """
    for _ in range(grid_width):
        sim.step_all()


def run_one_sweep(sim, grid_width):
    """Run one full H2 sweep = grid_width cycles = grid_width² step_all() calls.

    During one sweep, ALL code cells get corrected (one per cycle).
    """
    for _ in range(grid_width * grid_width):
        sim.step_all()


ROW_A_GP = 1
ROW_B_GP = 3

def zero_gp_rows(sim, grid_width):
    """Zero GP rows (cheat to avoid dirty-trail interference)."""
    for row in [ROW_A_GP, ROW_B_GP]:
        for c in range(grid_width):
            sim.grid[sim._to_flat(row, c)] = 0


def run_trial(noise_rate, max_sweeps=500, seed=None, verbose=False,
              gp_cleanup=False):
    """Run one trial: return number of sweeps survived.

    Returns max_sweeps if system still alive (censored).
    gp_cleanup: if True, zero GP rows after each sweep (cheat mode).
    """
    rng = random.Random(seed)
    sim, correct_cells, gadget_ops, grid_width = make_mutual_torus()
    code_rows = [ROW_A_CODE, ROW_B_CODE]

    for sweep in range(1, max_sweeps + 1):
        # Check residual state at start of sweep
        if verbose:
            wa_res, wb_res, res_details = count_wrong_cells(
                sim, correct_cells, grid_width)
            dirty = []
            for row in [ROW_A_CODE, ROW_B_CODE]:
                for c in range(grid_width):
                    actual = sim.grid[sim._to_flat(row, c)]
                    expected = correct_cells[c]
                    if actual != expected:
                        actual_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[actual]]
                        expected_op = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[expected]]
                        if actual_op == expected_op:
                            label = "A" if row == ROW_A_CODE else "B"
                            dirty.append((label, c, actual, expected))
            if wa_res or wb_res or dirty:
                print(f"  sweep {sweep} START: {wa_res+wb_res} wrong-op, "
                      f"{len(dirty)} dirty (correct op, wrong raw)")

        # Run one full H2 sweep with gradual noise injection (per-cycle)
        total_injected = 0
        lam_per_cycle = noise_rate / grid_width
        for cycle in range(grid_width):
            # Inject noise for this cycle (matches GUI behavior)
            n_errors = _poisson_sample(lam_per_cycle, rng)
            if n_errors > 0:
                inject_noise(sim, n_errors, grid_width, code_rows, rng)
                total_injected += n_errors
            # Run one cycle
            run_one_cycle(sim, grid_width)

        # GP cleanup cheat
        if gp_cleanup:
            zero_gp_rows(sim, grid_width)

        # Check if still alive
        if not check_alive(sim, correct_cells, grid_width):
            if verbose:
                wa, wb, details = count_wrong_cells(sim, correct_cells, grid_width)
                print(f"  DEAD at sweep {sweep} (+{total_injected} flips): "
                      f"{wa} wrong in A, {wb} wrong in B")
                for row, col, aop, eop in details[:10]:
                    label = "A" if row == ROW_A_CODE else "B"
                    actual_raw = sim.grid[sim._to_flat(row, col)]
                    print(f"    {label} col {col}: op {aop} != expected {eop}"
                          f"  raw=0x{actual_raw:04x}")
                if len(details) > 10:
                    print(f"    ... and {len(details)-10} more")
            return sweep
        elif verbose:
            print(f"  sweep {sweep}: +{total_injected} flips → survived")

    return max_sweeps  # survived all sweeps


def _poisson_sample(lam, rng):
    """Poisson variate via Knuth's algorithm."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p < L:
            return k - 1


# ── Theoretical prediction ──

def predict_mean_sweeps(n, n_cells=650, grid_width=325):
    """Predict mean sweeps to first fatal error.

    One H2 sweep = grid_width cycles. Noise is injected gradually
    (Poisson(n/grid_width) per cycle). Correction processes 1 cell per
    cycle. A cell accumulates a fatal 2-bit error when it gets 2+ hits
    across the full sweep before being corrected.

    Simplification: treat all n errors as uniformly distributed across
    n_cells over the full sweep (birthday model). This is approximate
    since correction happens concurrently.

    A fatal error = cell gets ≥2 hits AND the bits cause NOP:
      P(fatal | 2 hits) = P(different bits) × P(2 data bits | different)
                        = (15/16) × (11/24) ≈ 0.430
    """
    lam = n / n_cells  # per-cell Poisson rate per sweep

    p0 = math.exp(-lam)
    p1 = lam * math.exp(-lam)
    p2 = (lam ** 2 / 2) * math.exp(-lam)
    p3plus = 1 - p0 - p1 - p2

    p_fatal_given_2 = (15 / 16) * (11 / 24)
    p_fatal_given_3plus = 1.0

    e_fatal = n_cells * (p2 * p_fatal_given_2 + p3plus * p_fatal_given_3plus)

    if e_fatal > 0:
        return 1.0 / e_fatal
    else:
        return float('inf')


# ── Main experiment ──

def run_experiment(rates, n_trials=30, max_sweeps=500, gp_cleanup=False):
    """Run experiment for multiple noise rates."""
    print("=" * 70)
    mode = "GP CLEANUP ON" if gp_cleanup else "NO GP CLEANUP"
    print(f"NOISE SURVIVAL EXPERIMENT — Mutual Correction ({mode})")
    print("=" * 70)

    # Get grid info
    sim, correct_cells, gadget_ops, grid_width = make_mutual_torus()
    n_cells = 2 * grid_width  # total code cells across both gadgets
    print(f"Gadget size: {len(gadget_ops)} ops")
    print(f"Grid width: {grid_width}")
    print(f"Total code cells: {n_cells}")
    print(f"Trials per rate: {n_trials}")
    print(f"Max sweeps: {max_sweeps}")
    print()

    # Header
    print(f"{'Rate':>6}  {'Predicted':>10}  {'Measured':>10}  {'Median':>8}"
          f"  {'Min':>6}  {'Max':>6}  {'Censored':>8}  {'Time':>6}")
    print("-" * 70)

    for rate in rates:
        predicted = predict_mean_sweeps(rate, n_cells)

        t0 = time.time()
        results = []
        for trial in range(n_trials):
            seed = trial * 1000 + int(rate * 100)
            s = run_trial(rate, max_sweeps=max_sweeps, seed=seed,
                          gp_cleanup=gp_cleanup)
            results.append(s)

        elapsed = time.time() - t0
        censored = sum(1 for r in results if r >= max_sweeps)
        results_uncensored = [r for r in results if r < max_sweeps]

        if results_uncensored:
            mean = sum(results_uncensored) / len(results_uncensored)
            median = sorted(results_uncensored)[len(results_uncensored) // 2]
            mn = min(results_uncensored)
            mx = max(results_uncensored)
        else:
            mean = float('inf')
            median = max_sweeps
            mn = max_sweeps
            mx = max_sweeps

        pred_str = f"{predicted:.1f}" if predicted < 1e6 else "inf"
        mean_str = f"{mean:.1f}" if mean < 1e6 else f">{max_sweeps}"
        med_str = f"{median}" if median < max_sweeps else f">{max_sweeps}"

        print(f"{rate:>6.1f}  {pred_str:>10}  {mean_str:>10}  {med_str:>8}"
              f"  {mn:>6}  {mx:>6}  {censored:>4}/{n_trials:<3}  {elapsed:>5.1f}s")

    print()
    print("Notes:")
    print("  Predicted = 1/E[fatal cells per sweep] (birthday collision model)")
    print("  Measured = mean sweeps to death (excluding censored)")
    print(f"  Censored = trials surviving all {max_sweeps} sweeps")
    print("  Fatal = 2+ data-bit errors in same cell in same sweep → NOP")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--rates', type=str, default='1,3,5,8,10,12,15,20,30,50',
                        help='Comma-separated noise rates')
    parser.add_argument('--trials', type=int, default=30,
                        help='Number of trials per rate')
    parser.add_argument('--max-sweeps', type=int, default=500,
                        help='Maximum sweeps per trial')
    parser.add_argument('--verbose', type=float, default=0,
                        help='Run one verbose trial at this rate to diagnose failure')
    parser.add_argument('--gp-cleanup', action='store_true',
                        help='Zero GP rows after each sweep (cheat mode)')
    args = parser.parse_args()

    if args.verbose > 0:
        mode = "GP CLEANUP ON" if args.gp_cleanup else "NO GP CLEANUP"
        print(f"=== Verbose single trial at rate={args.verbose} ({mode}) ===")
        result = run_trial(args.verbose, max_sweeps=args.max_sweeps,
                           seed=42, verbose=True, gp_cleanup=args.gp_cleanup)
        print(f"\nSurvived {result} sweeps")
    else:
        rates = [float(r) for r in args.rates.split(',')]
        run_experiment(rates, n_trials=args.trials, max_sweeps=args.max_sweeps,
                       gp_cleanup=args.gp_cleanup)

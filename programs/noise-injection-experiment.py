#!/usr/bin/env python3
"""
noise-injection-experiment.py — Monte Carlo noise injection experiments
for mutual correction.

Questions answered:
1. What is the maximum sustainable noise rate for parity-bit errors?
2. What happens with random (any-bit) noise?
3. At what rate does the system transition from stable to diverging?

Model:
  Every cycle (325 step_all calls), inject Poisson(λ) random bit flips
  into code rows A and B. Track syndrome errors and payload corruption
  over time across multiple trials.

Usage:
  python3 programs/noise-injection-experiment.py            # run all experiments
  python3 programs/noise-injection-experiment.py --quick     # fast sanity check
  python3 programs/noise-injection-experiment.py --save      # save demo with errors
"""

import sys
import os
import random
import math
import time
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (FB2DSimulator, OPCODES, hamming_encode, hamming_syndrome,
                  cell_to_payload, DIR_E, encode_opcode, _PAYLOAD_TO_OPCODE,
                  OPCODE_PAYLOADS)

# Import mutual correction torus builder
_mc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'mutual-correction-demo.py')
_spec = importlib.util.spec_from_file_location('mc', _mc_path)
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

from hamming import inject_error

# ── Constants ──
PARITY_BITS = [0, 1, 2, 4, 8]
DATA_BITS = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]
N_CODE_CELLS = 323  # gadget ops per code row
CODE_ROWS = [mc.ROW_A_CODE, mc.ROW_B_CODE]
GP_ROWS = [mc.ROW_A_GP, mc.ROW_B_GP]
MAX_OPCODE = 56  # opcode numbers 0-56; with d_min=4, use _PAYLOAD_TO_OPCODE lookup


# ═══════════════════════════════════════════════════════════════════
# Poisson sampler (stdlib random doesn't have one)
# ═══════════════════════════════════════════════════════════════════

def poisson_sample(lam, rng):
    """Poisson variate via Knuth's algorithm. Fine for λ < 30."""
    if lam == 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p < L:
            return k - 1


# ═══════════════════════════════════════════════════════════════════
# Error measurement
# ═══════════════════════════════════════════════════════════════════

def count_errors(sim, code_row, correct_cells, cols):
    """Count errors in a code row.

    Returns: (syndrome_only, payload_corrupted, nop_payloads)
      syndrome_only: cells with parity errors but correct payload
      payload_corrupted: cells with wrong payload (wrong opcode)
      nop_payloads: subset of payload_corrupted where new payload → NOP (opcode 0)
    """
    syndrome_only = 0
    payload_corrupted = 0
    nop_payloads = 0
    for c in range(cols):
        val = sim.grid[sim._to_flat(code_row, c)]
        expected = correct_cells[c]
        if val != expected:
            actual_pl = cell_to_payload(val)
            expected_pl = cell_to_payload(expected)
            if actual_pl != expected_pl:
                payload_corrupted += 1
                if _PAYLOAD_TO_OPCODE[actual_pl] == 0:
                    nop_payloads += 1
            else:
                syndrome_only += 1
    return syndrome_only, payload_corrupted, nop_payloads


def count_all_errors(sim, correct_cells, cols):
    """Count errors in both code rows. Returns dict."""
    syn_a, pl_a, nop_a = count_errors(sim, mc.ROW_A_CODE, correct_cells, cols)
    syn_b, pl_b, nop_b = count_errors(sim, mc.ROW_B_CODE, correct_cells, cols)
    return {
        'syn_a': syn_a, 'pl_a': pl_a, 'nop_a': nop_a,
        'syn_b': syn_b, 'pl_b': pl_b, 'nop_b': nop_b,
        'total_errors': syn_a + pl_a + syn_b + pl_b,
        'total_payload': pl_a + pl_b,
    }


# ═══════════════════════════════════════════════════════════════════
# Noise injection
# ═══════════════════════════════════════════════════════════════════

def inject_noise(sim, grid_width, n_errors, error_type, rng):
    """Inject random bit flips into code rows.

    Args:
        sim: simulator
        grid_width: number of columns
        n_errors: number of bit flips to inject
        error_type: 'any', 'parity', 'data'
        rng: random.Random instance

    Returns: n_injected
    """
    for _ in range(n_errors):
        row = rng.choice(CODE_ROWS)
        col = rng.randint(0, grid_width - 1)

        if error_type == 'parity':
            bit = rng.choice(PARITY_BITS)
        elif error_type == 'data':
            bit = rng.choice(DATA_BITS)
        else:  # 'any'
            bit = rng.randint(0, 15)

        flat = sim._to_flat(row, col)
        sim.grid[flat] ^= (1 << bit)

    return n_errors


# ═══════════════════════════════════════════════════════════════════
# Single experiment trial
# ═══════════════════════════════════════════════════════════════════

def run_trial(error_rate, n_cycles, error_type='any', seed=42,
              verbose=False, crash_threshold=0.5):
    """Run a single noise injection trial.

    Args:
        error_rate: expected bit flips per cycle (Poisson λ)
        n_cycles: number of correction cycles
        error_type: 'any', 'parity', 'data'
        seed: random seed
        verbose: print per-cycle stats
        crash_threshold: fraction of payload-corrupted cells to declare crash

    Returns: dict with results
    """
    rng = random.Random(seed)

    # Start clean
    sim, correct, gadget_ops, width = mc.make_mutual_torus()

    history = []  # per-cycle error counts
    total_injected = 0
    crashed = False
    crash_cycle = None

    for cycle in range(n_cycles):
        # Inject noise at cycle boundary
        n_new = poisson_sample(error_rate, rng)
        inject_noise(sim, width, n_new, error_type, rng)
        total_injected += n_new

        # Run one correction cycle (325 step_all calls)
        try:
            for _ in range(width):
                sim.step_all()
        except Exception as e:
            if verbose:
                print(f"  Cycle {cycle}: EXCEPTION {e}")
            crashed = True
            crash_cycle = cycle
            break

        # Measure errors
        errs = count_all_errors(sim, correct, width)
        history.append(errs)

        if verbose and (cycle % 50 == 0 or cycle == n_cycles - 1):
            print(f"  Cycle {cycle:4d}: syn={errs['syn_a']+errs['syn_b']:3d}"
                  f"  pl={errs['total_payload']:3d}"
                  f"  injected={total_injected}")

        # Crash detection: if too many payload errors, system is lost
        crash_count = errs['total_payload']
        if crash_count > crash_threshold * 2 * N_CODE_CELLS:
            crashed = True
            crash_cycle = cycle
            if verbose:
                print(f"  Cycle {cycle}: CRASHED — {crash_count} payload errors")
            break

    # Compute steady-state stats from last 50% of cycles (or all if crashed)
    if not crashed and len(history) > 10:
        tail = history[len(history)//2:]
    else:
        tail = history[-10:] if history else [count_all_errors(sim, correct, width)]

    avg_syn = sum(h['syn_a'] + h['syn_b'] for h in tail) / len(tail)
    avg_pl = sum(h['total_payload'] for h in tail) / len(tail)
    max_pl = max(h['total_payload'] for h in tail)
    max_syn = max(h['syn_a'] + h['syn_b'] for h in tail)
    final = history[-1] if history else count_all_errors(sim, correct, width)

    return {
        'error_rate': error_rate,
        'error_type': error_type,
        'n_cycles': n_cycles,
        'seed': seed,
        'total_injected': total_injected,
        'crashed': crashed,
        'crash_cycle': crash_cycle,
        'avg_syndrome': avg_syn,
        'avg_payload': avg_pl,
        'max_payload': max_pl,
        'max_syndrome': max_syn,
        'final_syndrome': final['syn_a'] + final['syn_b'],
        'final_payload': final['total_payload'],
        'history': history,
    }


# ═══════════════════════════════════════════════════════════════════
# Experiment runners
# ═══════════════════════════════════════════════════════════════════

def experiment_parity_noise(n_cycles=200, n_trials=3):
    """Experiment 1: parity-bit-only noise at various rates.

    Parity errors don't change the opcode, so both IPs execute correctly.
    The correction should always succeed — question is the steady-state
    error count.
    """
    print("\n" + "=" * 70)
    print("EXPERIMENT 1: Parity-bit noise")
    print("=" * 70)
    print("Parity-bit flips change the Hamming codeword but NOT the opcode.")
    print("Both IPs execute correctly regardless. Correction should always work.\n")

    rates = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]

    print(f"{'Rate':>6s}  {'AvgSyn':>7s}  {'MaxSyn':>7s}  {'AvgPL':>6s}"
          f"  {'Injected':>9s}  {'Status':>8s}")
    print("-" * 55)

    for rate in rates:
        results = []
        for trial in range(n_trials):
            r = run_trial(rate, n_cycles, error_type='parity',
                         seed=42 + trial)
            results.append(r)

        avg_syn = sum(r['avg_syndrome'] for r in results) / len(results)
        max_syn = max(r['max_syndrome'] for r in results)
        avg_pl = sum(r['avg_payload'] for r in results) / len(results)
        avg_inj = sum(r['total_injected'] for r in results) / len(results)
        any_crash = any(r['crashed'] for r in results)

        status = "CRASHED" if any_crash else "STABLE"
        print(f"{rate:6.1f}  {avg_syn:7.1f}  {max_syn:7d}  {avg_pl:6.1f}"
              f"  {avg_inj:9.0f}  {status:>8s}")

    return True


def experiment_random_noise(n_cycles=200, n_trials=3):
    """Experiment 2: random bit noise (any of 16 bits).

    ~31% hit parity bits (safe), ~69% hit data bits.
    Of data-bit flips, ~31% produce NOP payloads (>56), ~69% change opcode.
    So ~31% + 0.69*0.31 ≈ 52% safe, ~48% produce different valid opcodes.

    Actually: 5/16 parity = 31.25%. Of the 11/16 data bits, about 97%
    of random data-bit payloads > 56 (since there are 2048 possible
    payloads and only 57 are valid opcodes = 2.8%). So:
    ~31% hit parity (safe) + ~69% * 97% = ~67% produce NOP = ~98% safe.
    Wait, that's too optimistic — the payload only changes by one data bit,
    not to a random value. Let me compute properly.

    For a given opcode (0-56), flipping one data bit can produce payloads
    from 0-2047. Many of these are > 56 (NOP), but the probability depends
    on which bit is flipped and the original opcode.
    """
    print("\n" + "=" * 70)
    print("EXPERIMENT 2: Random bit noise (any bit position)")
    print("=" * 70)
    print("Random single-bit flips. 5/16 hit parity (safe).")
    print("Data-bit flips may change the opcode, causing execution errors.\n")

    rates = [0.01, 0.05, 0.1, 0.5, 1.0, 2.0]

    print(f"{'Rate':>6s}  {'AvgSyn':>7s}  {'AvgPL':>6s}  {'MaxPL':>6s}"
          f"  {'Injected':>9s}  {'Status':>8s}  {'CrashCyc':>9s}")
    print("-" * 65)

    for rate in rates:
        results = []
        for trial in range(n_trials):
            r = run_trial(rate, n_cycles, error_type='any',
                         seed=42 + trial, crash_threshold=0.5)
            results.append(r)

        avg_syn = sum(r['avg_syndrome'] for r in results) / len(results)
        avg_pl = sum(r['avg_payload'] for r in results) / len(results)
        max_pl = max(r['max_payload'] for r in results)
        avg_inj = sum(r['total_injected'] for r in results) / len(results)
        any_crash = any(r['crashed'] for r in results)
        crash_cycles = [r['crash_cycle'] for r in results if r['crashed']]

        status = "CRASHED" if any_crash else "STABLE"
        crash_str = str(min(crash_cycles)) if crash_cycles else "-"
        print(f"{rate:6.2f}  {avg_syn:7.1f}  {avg_pl:6.1f}  {max_pl:6d}"
              f"  {avg_inj:9.0f}  {status:>8s}  {crash_str:>9s}")

    return True


def experiment_data_bit_analysis():
    """Experiment 3: analyze what fraction of data-bit flips are safe.

    For each opcode in the gadget, for each data bit position, compute
    whether flipping that bit produces a NOP (payload > 56) or a different
    valid opcode. This gives the exact safe fraction.
    """
    print("\n" + "=" * 70)
    print("EXPERIMENT 3: Data-bit safety analysis")
    print("=" * 70)
    print("For each gadget opcode × data bit position: is the flip safe?\n")

    gadget_ops = mc.build_h2_correction_gadget()
    op_values = [OPCODES[ch] for ch in gadget_ops]

    total_flips = 0
    safe_nop = 0        # produces NOP (payload > 56)
    safe_same = 0       # flipped bit doesn't change payload (shouldn't happen for data bits)
    unsafe_different = 0  # produces different valid opcode
    unsafe_mirror = 0     # produces a mirror opcode (changes IP direction!)

    mirror_ops = {1, 2, 3, 4, 5, 6}  # /, \, %, ?, &, !

    # Per-opcode breakdown
    opcode_danger = {}  # opcode -> count of unsafe flips

    for opval in op_values:
        cw = encode_opcode(opval)
        orig_payload = OPCODE_PAYLOADS[opval]
        if opval not in opcode_danger:
            opcode_danger[opval] = 0

        for bit in DATA_BITS:
            total_flips += 1
            corrupted = cw ^ (1 << bit)
            new_payload = cell_to_payload(corrupted)
            new_opcode = _PAYLOAD_TO_OPCODE[new_payload]

            if new_payload == orig_payload:
                safe_same += 1  # shouldn't happen for data bits
            elif new_opcode == 0:
                safe_nop += 1  # payload → NOP (no valid opcode)
            else:
                unsafe_different += 1
                opcode_danger[opval] += 1
                if new_opcode in mirror_ops:
                    unsafe_mirror += 1

    total = total_flips
    print(f"  Total gadget ops: {len(gadget_ops)}")
    print(f"  Total data-bit flips analyzed: {total}")
    print(f"  Safe (→ NOP):              {safe_nop:5d} ({100*safe_nop/total:.1f}%)")
    print(f"  Safe (→ same payload):     {safe_same:5d} ({100*safe_same/total:.1f}%)")
    print(f"  Unsafe (→ valid opcode):   {unsafe_different:5d} ({100*unsafe_different/total:.1f}%)")
    print(f"    of which → mirror:       {unsafe_mirror:5d} ({100*unsafe_mirror/total:.1f}%)")
    print()

    # Combined safety for ANY random bit flip (parity + data)
    n_gadget = len(gadget_ops)
    total_any = n_gadget * 16
    safe_parity = n_gadget * 5  # parity bits always safe
    safe_data_nop = safe_nop
    total_safe = safe_parity + safe_data_nop + safe_same
    total_unsafe = total_any - total_safe

    print(f"  Overall (any random bit flip on gadget code):")
    print(f"    Total possible flips:    {total_any}")
    print(f"    Safe parity flips:       {safe_parity:5d} ({100*safe_parity/total_any:.1f}%)")
    print(f"    Safe data→NOP:           {safe_data_nop:5d} ({100*safe_data_nop/total_any:.1f}%)")
    print(f"    Total safe:              {total_safe:5d} ({100*total_safe/total_any:.1f}%)")
    print(f"    UNSAFE (→ valid opcode): {total_unsafe:5d} ({100*total_unsafe/total_any:.1f}%)")

    # Most dangerous opcodes (highest fraction of unsafe data-bit flips)
    print(f"\n  Most vulnerable opcodes (highest unsafe data-bit flip count):")
    sorted_ops = sorted(opcode_danger.items(), key=lambda x: -x[1])
    op_to_char = {v: k for k, v in OPCODES.items()}
    for opval, count in sorted_ops[:10]:
        ch = op_to_char.get(opval, '?')
        freq = sum(1 for o in op_values if o == opval)
        per_instance = count // freq if freq > 0 else count
        print(f"    op={opval:2d} '{ch}': {per_instance}/11 unsafe per cell"
              f" ({100*per_instance/11:.0f}%), appears {freq}× in gadget"
              f" ({count} total)")

    return True


def experiment_convergence_trace(error_rate=0.5, n_cycles=500,
                                 error_type='parity', seed=42):
    """Experiment 4: detailed time series of error counts.

    Prints a cycle-by-cycle trace useful for understanding dynamics.
    """
    print("\n" + "=" * 70)
    print(f"EXPERIMENT 4: Convergence trace (rate={error_rate}, type={error_type})")
    print("=" * 70)

    result = run_trial(error_rate, n_cycles, error_type=error_type,
                       seed=seed, verbose=True)

    if result['history']:
        final = result['history'][-1]
        print(f"\n  Final state: syn={final['syn_a']+final['syn_b']}"
              f"  pl={final['total_payload']}")
        print(f"  Total injected: {result['total_injected']}")
        print(f"  Steady-state avg syndrome: {result['avg_syndrome']:.1f}")
        print(f"  Status: {'CRASHED' if result['crashed'] else 'STABLE'}")

        # Error count distribution over time
        syns = [h['syn_a'] + h['syn_b'] for h in result['history']]
        pls = [h['total_payload'] for h in result['history']]
        print(f"\n  Syndrome error range: {min(syns)} — {max(syns)}")
        print(f"  Payload error range:  {min(pls)} — {max(pls)}")

        # Print ASCII sparkline of syndrome errors
        if max(syns) > 0:
            print(f"\n  Syndrome errors over time (max={max(syns)}):")
            spark = _sparkline(syns, width=70)
            print(f"  {spark}")

    return result


def _sparkline(values, width=70):
    """Create a simple ASCII sparkline."""
    if not values:
        return ""
    # Downsample if needed
    if len(values) > width:
        step = len(values) / width
        sampled = []
        for i in range(width):
            idx = int(i * step)
            sampled.append(values[idx])
        values = sampled

    max_val = max(values) if max(values) > 0 else 1
    blocks = " ▁▂▃▄▅▆▇█"
    result = ""
    for v in values:
        idx = int(v / max_val * (len(blocks) - 1))
        result += blocks[idx]
    return result


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    quick = '--quick' in sys.argv

    if quick:
        n_cycles = 50
        n_trials = 1
        print("=== QUICK MODE (reduced cycles/trials) ===\n")
    else:
        n_cycles = 200
        n_trials = 3

    t0 = time.time()

    # Experiment 3 first (fast, no simulation)
    experiment_data_bit_analysis()

    # Experiment 1: parity noise
    experiment_parity_noise(n_cycles=n_cycles, n_trials=n_trials)

    # Experiment 2: random noise
    experiment_random_noise(n_cycles=n_cycles, n_trials=n_trials)

    # Experiment 4: convergence trace (parity)
    experiment_convergence_trace(error_rate=1.0, n_cycles=n_cycles,
                                 error_type='parity', seed=42)

    # Experiment 4b: convergence trace (any bit, low rate)
    experiment_convergence_trace(error_rate=0.1, n_cycles=n_cycles,
                                 error_type='any', seed=42)

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"All experiments completed in {elapsed:.1f}s")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
cl-ordering-optimize.py — Find optimal CL adjustment ordering for Hamming(16,11) gadget.

Minimizes the total number of ':' and ';' ops (CL increments/decrements)
needed across Phases A, A', C, C', and final cleanup.

The Y opcode does [H0] ^= ror([H1], [CL]&15). CL must be set to the correct
bit position before each Y. CL adjustments use ':' (increment) and ';'
(decrement), each costing 1 op. The total CL movement cost is the sum of
|CL_next - CL_current| across all Y ops.

Constraints:
  - CL starts at 0 before Phase A.
  - Phase A visits all 16 positions {0..15}, order is free.
  - Phase A' reverses Phase A's order.
  - Phase C visits 4 syndrome groups in some order; within each group,
    bit positions can be visited in any order.
  - Phase C' reverses Phase C (reversed group order AND reversed within-group order).
  - After Phase C', CL must return to 0.
  - Between Phase A' ending and Phase C starting, CL doesn't change.
"""

import itertools
import time


# Syndrome bit positions for standard-form Hamming(16,11)
SYNDROME_POSITIONS = [
    [1, 3, 5, 7, 9, 11, 13, 15],       # s0
    [2, 3, 6, 7, 10, 11, 14, 15],       # s1
    [4, 5, 6, 7, 12, 13, 14, 15],       # s2
    [8, 9, 10, 11, 12, 13, 14, 15],     # s3
]


def sequence_cost_from(start_cl, sequence):
    """Total CL cost starting from start_cl, visiting sequence in order."""
    if not sequence:
        return 0
    cost = abs(sequence[0] - start_cl)
    for i in range(len(sequence) - 1):
        cost += abs(sequence[i + 1] - sequence[i])
    return cost


def compute_aa_cost(k):
    """Compute minimum A+A' cost for Phase A starting at position k.

    For positions {0..15} on the integer line with L1 metric, starting from CL=0,
    the optimal Phase A path with first element k is the V-shape:
      Option 1: k, k-1, ..., 0, k+1, ..., 15  (left-first)
      Option 2: k, k+1, ..., 15, k-1, ..., 0   (right-first)

    A' is the reverse. A' starts where A ends (no additional jump).
    """
    # Left-first: k, k-1, ..., 0, k+1, ..., 15
    path_left = list(range(k, -1, -1)) + list(range(k + 1, 16))
    cost_a_left = sequence_cost_from(0, path_left)
    cost_aprime_left = sequence_cost_from(path_left[-1], list(reversed(path_left)))
    aa_left = cost_a_left + cost_aprime_left

    # Right-first: k, k+1, ..., 15, k-1, ..., 0
    path_right = list(range(k, 16)) + list(range(k - 1, -1, -1))
    cost_a_right = sequence_cost_from(0, path_right)
    cost_aprime_right = sequence_cost_from(path_right[-1], list(reversed(path_right)))
    aa_right = cost_a_right + cost_aprime_right

    if aa_left <= aa_right:
        return aa_left, path_left
    else:
        return aa_right, path_right


def analyze_current():
    """Analyze the current ordering from dual-gadget-demo.py."""
    print("=" * 70)
    print("CURRENT ORDERING ANALYSIS")
    print("=" * 70)

    # Phase A: ascending 0..15
    phase_a = list(range(16))
    phase_a_prime = list(range(15, -1, -1))

    # Phase C current ordering from dual-gadget-demo.py:
    s0_fwd = [1, 3, 5, 7, 9, 11, 13, 15]
    s1_fwd = [15, 14, 11, 10, 7, 6, 3, 2]
    s2_fwd = [4, 5, 6, 7, 12, 13, 14, 15]
    s3_fwd = [15, 14, 13, 12, 11, 10, 9, 8]

    # --- Phase A ---
    cost_a = sequence_cost_from(0, phase_a)
    cl_after_a = phase_a[-1]
    print(f"\nPhase A: {phase_a}")
    print(f"  CL: 0 -> {cl_after_a}, cost = {cost_a}")

    # --- Phase A' ---
    cost_a_prime = sequence_cost_from(cl_after_a, phase_a_prime)
    cl_after_a_prime = phase_a_prime[-1]
    print(f"Phase A': {phase_a_prime}")
    print(f"  CL: {cl_after_a} -> {cl_after_a_prime}, cost = {cost_a_prime}")

    # --- Phase C ---
    cl = cl_after_a_prime  # = 0
    print(f"\nPhase C (CL starts at {cl}):")
    total_c = 0
    phase_c_full = []
    group_order = [0, 1, 2, 3]
    within = {0: s0_fwd, 1: s1_fwd, 2: s2_fwd, 3: s3_fwd}
    for gi in group_order:
        group = within[gi]
        c = sequence_cost_from(cl, group)
        print(f"  s{gi}: {group}  CL: {cl} -> {group[-1]}, cost = {c}")
        total_c += c
        cl = group[-1]
        phase_c_full.extend(group)
    cl_after_c = cl
    print(f"  Total Phase C: {total_c}")

    # --- Phase C' ---
    phase_c_prime = list(reversed(phase_c_full))
    cost_c_prime = sequence_cost_from(cl_after_c, phase_c_prime)
    cl_after_c_prime = phase_c_prime[-1]
    print(f"\nPhase C' (CL starts at {cl_after_c}):")
    idx = 0
    cl_tmp = cl_after_c
    for gi in reversed(group_order):
        chunk = phase_c_prime[idx:idx + 8]
        c = sequence_cost_from(cl_tmp, chunk)
        print(f"  s{gi}: {chunk}  CL: {cl_tmp} -> {chunk[-1]}, cost = {c}")
        cl_tmp = chunk[-1]
        idx += 8
    print(f"  Total Phase C': {cost_c_prime}")

    # --- Cleanup ---
    cost_cleanup = abs(cl_after_c_prime)
    print(f"\nCleanup: CL {cl_after_c_prime} -> 0, cost = {cost_cleanup}")

    total = cost_a + cost_a_prime + total_c + cost_c_prime + cost_cleanup
    print(f"\n{'=' * 50}")
    print(f"TOTAL CURRENT COST: {total}")
    print(f"  Phase A:   {cost_a}")
    print(f"  Phase A':  {cost_a_prime}")
    print(f"  Phase C:   {total_c}")
    print(f"  Phase C':  {cost_c_prime}")
    print(f"  Cleanup:   {cost_cleanup}")

    return total


def precompute_group_opts():
    """Precompute optimal within-group orderings.

    For each group g (0-3), for each starting CL (0-15), enumerate all 8!
    permutations and record the best cost for each ending position.

    Returns: group_opts[g][start_cl] = dict: end_pos -> (cost, order)
    """
    group_opts = {}
    for g in range(4):
        group_opts[g] = {}
        for scl in range(16):
            best_by_end = {}
            for perm in itertools.permutations(SYNDROME_POSITIONS[g]):
                cost = sequence_cost_from(scl, perm)
                end = perm[-1]
                if end not in best_by_end or cost < best_by_end[end][0]:
                    best_by_end[end] = (cost, list(perm))
            group_opts[g][scl] = best_by_end
    return group_opts


def optimize_phase_c(start_cl, group_opts):
    """Optimize Phase C + C' + cleanup given CL starts at start_cl.

    Try all 4! = 24 permutations of syndrome groups.
    Use DP over groups to handle within-group ordering efficiently.

    Key cost identity:
      Phase C' is the reverse of Phase C.
      Let S = full Phase C sequence.
      cost(C) from start_cl = |S[0] - start_cl| + sum_internal_diffs(S)
      cost(C') from S[-1] = 0 + sum_internal_diffs(S)  [starts at S[-1], same diffs reversed]
      So: cost(C') = cost(C) - |S[0] - start_cl|
      CL after C' = S[0]
      Cleanup = |S[0]|

      Total C+C'+cleanup = 2*cost(C) - |S[0] - start_cl| + |S[0]|
    """
    best_total = float('inf')
    best_config = None

    for group_perm in itertools.permutations([0, 1, 2, 3]):
        # DP: track CL position -> (cost_c, within_orders)
        dp = {start_cl: (0, [])}

        for gi in group_perm:
            new_dp = {}
            for cl_pos, (cost_so_far, orders_so_far) in dp.items():
                for end_pos, (gcost, gorder) in group_opts[gi][cl_pos].items():
                    total = cost_so_far + gcost
                    if end_pos not in new_dp or total < new_dp[end_pos][0]:
                        new_dp[end_pos] = (total, orders_so_far + [gorder])
            dp = new_dp

        for cl_after_c, (cost_c, orders) in dp.items():
            # First element of C
            first_elem = orders[0][0]

            # Use the identity: C+C'+cleanup = 2*cost_c - |first - start| + |first|
            cost_c_prime = cost_c - abs(first_elem - start_cl)
            cost_cleanup = abs(first_elem)
            total = cost_c + cost_c_prime + cost_cleanup

            if total < best_total:
                best_total = total
                best_config = (list(group_perm), [list(o) for o in orders],
                               cost_c, cost_c_prime, cost_cleanup)

    return best_total, best_config


def verify_cost(phase_a, group_perm, within_orders):
    """Verify total cost by simulating every CL movement."""
    cl = 0
    total = 0

    # Phase A
    for pos in phase_a:
        total += abs(pos - cl)
        cl = pos

    # Phase A'
    for pos in reversed(phase_a):
        total += abs(pos - cl)
        cl = pos

    # Phase C
    for order in within_orders:
        for pos in order:
            total += abs(pos - cl)
            cl = pos

    # Phase C'
    full_c = []
    for order in within_orders:
        full_c.extend(order)
    for pos in reversed(full_c):
        total += abs(pos - cl)
        cl = pos

    # Cleanup
    total += abs(cl)

    return total


def optimize_full():
    """Full optimization of Phase A + A' + C + C' + cleanup."""
    print("\n" + "=" * 70)
    print("FULL OPTIMIZATION")
    print("=" * 70)

    print("\nPhase A analysis:")
    print("  For each A[0]=k, the best V-shape Phase A has A+A' cost:")
    for k in range(16):
        aa_cost, _ = compute_aa_cost(k)
        print(f"    k={k:2d}: A+A' = {aa_cost}")

    # Precompute group orderings once
    print("\nPrecomputing within-group orderings (4 groups x 16 starts x 8! perms)...")
    t0 = time.time()
    group_opts = precompute_group_opts()
    print(f"  Done in {time.time() - t0:.1f}s")

    print("\nSearching over A[0] = 0..15, each with 4! group perms + DP...")
    best_overall = float('inf')
    best_overall_config = None

    for k in range(16):
        aa_cost, path_a = compute_aa_cost(k)
        # CL after A' = A[0] = k
        c_total, c_config = optimize_phase_c(k, group_opts)
        total = aa_cost + c_total

        if total < best_overall:
            best_overall = total
            best_overall_config = (k, aa_cost, path_a, c_total, c_config)

    # Also check if there are non-V-shape Phase A paths that could be better.
    # We proved on {0..7} that V-shape is optimal for A+A'. For {0..15},
    # since the positions are on an integer line with L1 metric, the same
    # argument holds: any non-monotone segment adds to the internal cost
    # without reducing the initial jump. So V-shape is optimal.

    # Unpack best
    fp, cost_aa, path_a, cost_c_total, c_config = best_overall_config
    group_perm, within_orders, cost_c, cost_c_prime, cost_cleanup = c_config

    # Verify
    verified = verify_cost(path_a, group_perm, within_orders)
    assert verified == best_overall, f"Verification failed: {verified} vs {best_overall}"

    # Display results
    print(f"\n{'=' * 70}")
    print(f"OPTIMAL RESULT")
    print(f"{'=' * 70}")
    print(f"Total CL ops: {best_overall}")

    print(f"\nPhase A (A[0]={fp}):")
    print(f"  Path: {path_a}")
    cost_a_only = sequence_cost_from(0, path_a)
    cost_aprime_only = sequence_cost_from(path_a[-1], list(reversed(path_a)))
    print(f"  Cost(A): {cost_a_only}, Cost(A'): {cost_aprime_only}")
    print(f"  A+A': {cost_aa}")

    print(f"\nPhase A' (reverse):")
    print(f"  Path: {list(reversed(path_a))}")

    print(f"\nPhase C (starting CL={fp}):")
    print(f"  Group order: s{group_perm[0]}, s{group_perm[1]}, s{group_perm[2]}, s{group_perm[3]}")
    cl = fp
    for i, gi in enumerate(group_perm):
        order = within_orders[i]
        cost_g = sequence_cost_from(cl, order)
        print(f"  s{gi}: {order}")
        print(f"       CL: {cl} -> {order[-1]}, cost = {cost_g}")
        cl = order[-1]
    print(f"  Total Phase C: {cost_c}")

    full_c = []
    for order in within_orders:
        full_c.extend(order)
    full_c_prime = list(reversed(full_c))

    print(f"\nPhase C' (reverse, CL starts at {cl}):")
    idx = 0
    cl_tmp = cl
    for gi in reversed(group_perm):
        chunk = full_c_prime[idx:idx + 8]
        c = sequence_cost_from(cl_tmp, chunk)
        print(f"  s{gi}: {chunk}")
        print(f"       CL: {cl_tmp} -> {chunk[-1]}, cost = {c}")
        cl_tmp = chunk[-1]
        idx += 8
    print(f"  Total Phase C': {cost_c_prime}")

    cl_final = full_c_prime[-1]
    print(f"\nCleanup: CL {cl_final} -> 0, cost = {cost_cleanup}")

    print(f"\nBreakdown:")
    print(f"  Phase A:   {cost_a_only}")
    print(f"  Phase A':  {cost_aprime_only}")
    print(f"  Phase C:   {cost_c}")
    print(f"  Phase C':  {cost_c_prime}")
    print(f"  Cleanup:   {cost_cleanup}")
    print(f"  TOTAL:     {best_overall}")
    print(f"\nVerification: PASSED")

    return best_overall


def show_all_ties(current_cost):
    """Show all configurations that tie with the optimal."""
    print("\n" + "=" * 70)
    print("ALL OPTIMAL CONFIGURATIONS (tied at minimum cost)")
    print("=" * 70)

    group_opts = precompute_group_opts()

    optimal_configs = []
    for k in range(16):
        aa_cost, path_a = compute_aa_cost(k)
        for group_perm in itertools.permutations([0, 1, 2, 3]):
            dp = {k: (0, [])}
            for gi in group_perm:
                new_dp = {}
                for cl_pos, (cost_so_far, orders_so_far) in dp.items():
                    for end_pos, (gcost, gorder) in group_opts[gi][cl_pos].items():
                        total_c = cost_so_far + gcost
                        if end_pos not in new_dp or total_c < new_dp[end_pos][0]:
                            new_dp[end_pos] = (total_c, orders_so_far + [gorder])
                dp = new_dp

            for cl_after_c, (cost_c, orders) in dp.items():
                first_elem = orders[0][0]
                cost_c_prime = cost_c - abs(first_elem - k)
                cost_cleanup = abs(first_elem)
                c_total = cost_c + cost_c_prime + cost_cleanup
                grand = aa_cost + c_total

                if grand == current_cost:
                    optimal_configs.append((k, aa_cost, list(group_perm),
                                          [list(o) for o in orders],
                                          cost_c, cost_c_prime, cost_cleanup))

    print(f"\nFound {len(optimal_configs)} optimal configurations:")
    for i, (k, aa, gp, wo, cc, ccp, ccl) in enumerate(optimal_configs):
        full_c = []
        for o in wo:
            full_c.extend(o)
        print(f"\n  Config {i+1}: A[0]={k}, A+A'={aa}, groups=s{'s'.join(str(g) for g in gp)}")
        for j, gi in enumerate(gp):
            print(f"    s{gi}: {wo[j]}")
        print(f"    C={cc}, C'={ccp}, cleanup={ccl}")


if __name__ == '__main__':
    current = analyze_current()
    print(f"\nCurrent cost confirmed: {current}")

    optimal = optimize_full()

    print(f"\n{'=' * 70}")
    print(f"SUMMARY")
    print(f"{'=' * 70}")
    print(f"Current total CL ops:  {current}")
    print(f"Optimal total CL ops:  {optimal}")
    print(f"Savings:               {current - optimal}")
    if current > 0:
        print(f"Reduction:             {100 * (current - optimal) / current:.1f}%")

    if current == optimal:
        print("\nThe current ordering is already optimal!")
        print("Searching for all tied-optimal configurations...")
        show_all_ties(current)

#!/usr/bin/env python3
"""
Hamming Distance Analysis of fb2d Opcode Encoding

Analyzes the vulnerability of the current sequential opcode assignment
to single-bit flips, and explores whether reassignment can improve
the minimum Hamming distance between dangerous opcodes (j, m) and
common gadget opcodes.

Key encoding facts:
  - 16-bit cells with Hamming(16,11) SECDED
  - 11 data bit positions: {3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15}
  - IP reads the 11-bit payload as the opcode
  - Flipping one data bit in the 16-bit cell flips exactly one payload bit
  - 57 used opcodes: 0 (NOP), 1-56 (operations)
  - Payloads 57-2047 are also NOP
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fb2d import hamming_encode, cell_to_payload, DATA_POSITIONS

# ─── Opcode assignments (current, sequential) ──────────────────────

OPCODE_NAMES = {
    0: 'NOP', 1: '/', 2: '\\', 3: '%', 4: '?', 5: '&', 6: '!',
    7: 'N', 8: 'S', 9: 'E', 10: 'W', 11: 'n', 12: 's', 13: 'e', 14: 'w',
    15: '+', 16: '-', 17: '.', 18: ',', 19: 'X', 20: 'F', 21: 'G', 22: 'T',
    23: '>', 24: '<', 25: '^', 26: 'v', 27: 'P', 28: 'Q',
    29: ']', 30: '[', 31: '}', 32: '{', 33: 'K', 34: '(', 35: ')',
    36: '#', 37: '$', 38: 'Z', 39: 'x', 40: 'r', 41: 'l',
    42: 'f', 43: 'z', 44: 'R', 45: 'L', 46: 'Y', 47: ':', 48: ';',
    49: 'H(h2N)', 50: 'h(h2S)', 51: 'a(h2E)', 52: 'd(h2W)',
    53: 'm', 54: 'M', 55: 'j', 56: 'V',
}

# Critical opcodes for analysis
CRITICAL = {
    'm': 53, 'M': 54, 'j': 55, 'V': 56,
    'Y': 46, ':': 47, ';': 48,
    'r': 40, 'l': 41,
    'E': 9, 'W': 10, 'e': 13, 'w': 14,
    '+': 15, '-': 16,
}

# "Dangerous" H2 write opcodes that can cause cascade failures
DANGEROUS = {'j': 55, 'm': 53}

# Common gadget opcodes (most frequently used in correction gadgets)
GADGET_OPS = {'Y': 46, ':': 47, ';': 48, 'E': 9, 'W': 10, 'r': 40, 'l': 41}

ALL_USED = set(range(57))  # payloads 0-56

# ─── Helper functions ──────────────────────────────────────────────

def hamming_distance(a, b):
    """Hamming distance between two 11-bit payload values."""
    return bin(a ^ b).count('1')

def single_bit_neighbors(payload, nbits=11):
    """Return all payloads reachable by flipping exactly one bit (in 11-bit space)."""
    neighbors = []
    for i in range(nbits):
        neighbors.append(payload ^ (1 << i))
    return neighbors

def format_binary(val, bits=11):
    """Format as binary with specified width."""
    return format(val, f'0{bits}b')

# ═══════════════════════════════════════════════════════════════════
# PART 1: Hamming distances between all critical opcode pairs
# ═══════════════════════════════════════════════════════════════════

def part1():
    print("=" * 72)
    print("PART 1: Hamming distances between critical opcode pairs")
    print("        (11-bit payload space)")
    print("=" * 72)

    names = list(CRITICAL.keys())
    vals = [CRITICAL[n] for n in names]

    # Header
    hdr = f"{'':>5s}"
    for n in names:
        hdr += f" {n:>4s}"
    print(hdr)

    for i, n1 in enumerate(names):
        row = f"{n1:>5s}"
        for j, n2 in enumerate(names):
            d = hamming_distance(vals[i], vals[j])
            row += f" {d:>4d}"
        print(row)

    print()

    # Show the actual binary representations
    print("11-bit payload binary representations:")
    for name in names:
        val = CRITICAL[name]
        print(f"  {name:>3s} = {val:>3d} = {format_binary(val)}")

    print()

    # Highlight the dangerous vs gadget distances
    print("Key distances (dangerous <-> gadget):")
    for dname, dval in DANGEROUS.items():
        for gname, gval in GADGET_OPS.items():
            d = hamming_distance(dval, gval)
            xor_val = dval ^ gval
            print(f"  {dname}({dval}) <-> {gname}({gval}): distance {d}  "
                  f"(XOR = {format_binary(xor_val)} = {xor_val})")

    return

# ═══════════════════════════════════════════════════════════════════
# PART 2: Which single-data-bit flips produce dangerous opcodes?
# ═══════════════════════════════════════════════════════════════════

def part2():
    print("\n" + "=" * 72)
    print("PART 2: Single-data-bit flips on gadget opcodes that produce")
    print("        dangerous opcodes (j=55, m=53)")
    print("=" * 72)
    print()
    print("For each gadget opcode, we check: flipping each of the 11 payload")
    print("bits, does the result equal any dangerous opcode?")
    print()

    # Also check: what does each flip produce?
    for gname, gval in sorted(GADGET_OPS.items(), key=lambda x: x[1]):
        print(f"  {gname} = {gval} = {format_binary(gval)}:")
        neighbors = single_bit_neighbors(gval)
        for bit_idx, neighbor in enumerate(neighbors):
            neighbor_name = OPCODE_NAMES.get(neighbor, f"NOP({neighbor})" if neighbor > 56 else "???")
            is_dangerous = neighbor in DANGEROUS.values()
            marker = " *** DANGEROUS ***" if is_dangerous else ""
            is_used = neighbor in ALL_USED
            used_tag = "" if is_used else " [unused/NOP]"
            if is_dangerous or neighbor in ALL_USED:
                print(f"    flip bit d{bit_idx}: {gval} -> {neighbor} "
                      f"({format_binary(gval)} -> {format_binary(neighbor)}) "
                      f"= {neighbor_name}{used_tag}{marker}")

    # Now comprehensive: for ALL 57 used opcodes, which single-bit flips -> j or m?
    print()
    print("Complete list of single-bit flips producing j(55) or m(53):")
    print("-" * 60)
    for dname, dval in sorted(DANGEROUS.items(), key=lambda x: x[1]):
        print(f"\n  Flips producing {dname}={dval} ({format_binary(dval)}):")
        found = False
        for src in sorted(ALL_USED):
            d = hamming_distance(src, dval)
            if d == 1:
                src_name = OPCODE_NAMES.get(src, f"op{src}")
                xor = src ^ dval
                bit_idx = xor.bit_length() - 1
                # Which payload bit?
                for i in range(11):
                    if xor == (1 << i):
                        bit_idx = i
                        break
                print(f"    {src_name}({src}) -> {dname}({dval}): "
                      f"flip payload bit d{bit_idx}  "
                      f"({format_binary(src)} -> {format_binary(dval)})")
                found = True
        if not found:
            print(f"    (none)")

    return

# ═══════════════════════════════════════════════════════════════════
# PART 3: Fraction of cascade-causing flips
# ═══════════════════════════════════════════════════════════════════

def part3():
    print("\n" + "=" * 72)
    print("PART 3: Fraction of single-data-bit flips on gadget code cells")
    print("        that produce j or m (cascade-causing flips)")
    print("=" * 72)
    print()

    total_flips = 0
    dangerous_flips = 0
    results_per_op = {}

    for gname, gval in sorted(GADGET_OPS.items(), key=lambda x: x[1]):
        op_dangerous = 0
        neighbors = single_bit_neighbors(gval)
        for neighbor in neighbors:
            total_flips += 1
            if neighbor in DANGEROUS.values():
                dangerous_flips += 1
                op_dangerous += 1
        results_per_op[gname] = (op_dangerous, 11)

    print(f"  Gadget opcodes analyzed: {list(GADGET_OPS.keys())}")
    print(f"  Total single-data-bit flips: {total_flips}")
    print(f"  Flips producing j or m: {dangerous_flips}")
    print(f"  Fraction: {dangerous_flips}/{total_flips} = {dangerous_flips/total_flips:.4f}")
    print()
    print("  Per-opcode breakdown:")
    for gname, (d, t) in sorted(results_per_op.items(), key=lambda x: GADGET_OPS[x[0]]):
        print(f"    {gname}({GADGET_OPS[gname]}): {d}/{t} flips -> dangerous")

    # Also compute for ALL 57 opcodes
    print()
    print("  Extended: across ALL 57 used opcodes (0-56):")
    total_all = 0
    dangerous_all = 0
    per_op_details = []
    for src in sorted(ALL_USED):
        op_d = 0
        for neighbor in single_bit_neighbors(src):
            total_all += 1
            if neighbor in DANGEROUS.values():
                dangerous_all += 1
                op_d += 1
        if op_d > 0:
            per_op_details.append((src, op_d))

    print(f"    Total flips: {total_all}")
    print(f"    Cascade-causing flips: {dangerous_all}")
    print(f"    Fraction: {dangerous_all}/{total_all} = {dangerous_all/total_all:.6f}")
    print()
    print(f"    Opcodes with >=1 cascade-causing neighbor:")
    for src, cnt in per_op_details:
        src_name = OPCODE_NAMES.get(src, f"op{src}")
        print(f"      {src_name}({src}): {cnt} flip(s) -> dangerous")

    return

# ═══════════════════════════════════════════════════════════════════
# PART 4: Can reassignment increase minimum Hamming distance?
# ═══════════════════════════════════════════════════════════════════

def part4():
    print("\n" + "=" * 72)
    print("PART 4: Optimal reassignment analysis")
    print("        Can we place j and m at payload values with Hamming")
    print("        distance >= 3 from all other used opcodes?")
    print("=" * 72)
    print()

    # Strategy: we want to find 11-bit values for j and m such that
    # their Hamming distance from every OTHER used payload is >= d_min.
    #
    # We have 57 opcodes. We can reassign ALL of them freely.
    # The question is really about the ENTIRE assignment, not just j and m.
    #
    # But let's start simple: fix the other 55 opcodes at 0-54 (or wherever),
    # and find the best place for j and m.
    #
    # Actually, we have full freedom. The real question is:
    # can we find 57 values from {0,...,2047} such that the minimum pairwise
    # Hamming distance is maximized? This is a coding theory problem.

    # First: theoretical bounds
    print("Theoretical bounds:")
    print(f"  11-bit payloads: 2048 values")
    print(f"  Used opcodes: 57")
    print(f"  Hamming(11, d) bound: A(n, d) = max codewords of length n, distance >= d")
    print()

    # Compute A(11, d) for small d using known bounds
    # A(11, 1) = 2048 (trivial)
    # A(11, 2) = 2048 (trivial - any code)
    # A(11, 3) = ? Hamming bound: 2048 / V(11, 1) = 2048 / 12 = 170.67 -> ≤ 170
    #            Actually Hamming(11,4) perfect code: 2^11 / 2^4 = 128 codewords, but
    #            that's for d=3 (Hamming bound for single-error-correcting).
    #            The perfect Hamming code [15,11,3] is different... Let me think.
    #
    # For binary codes of length n=11:
    # A(11, 3): Singleton bound gives 2^(11-2) = 512.
    #           Hamming bound: 2^11 / sum_{i=0}^{1} C(11,i) = 2048/12 = 170.67 -> ≤ 170
    #           Plotkin bound for d=3: not applicable (d < n/2 + 1)
    #           Known: A(11, 3) = 256 (exists: punctured [12,256,4] code or similar)
    #           Actually more precisely: A(11,3) = 256 via shortened Hamming codes.
    #
    # But we only need 57 codewords, way below any of these limits.
    # So placing 57 codewords with d_min >= 3 is certainly feasible.

    # Let's compute: for each possible 11-bit value, how many of the
    # 57 sequential values {0,...,56} are at Hamming distance 1?

    print("For the CURRENT sequential assignment (0-56):")
    for target_d in [1, 2, 3, 4, 5]:
        # How many 11-bit values are at distance >= target_d from ALL of {0,...,56}?
        count = 0
        for v in range(2048):
            if v in ALL_USED:
                continue
            ok = True
            for u in ALL_USED:
                if hamming_distance(v, u) < target_d:
                    ok = False
                    break
            if ok:
                count += 1
        print(f"  Values at distance >= {target_d} from all of {{0,...,56}}: {count}")

    print()

    # Now: the REAL question. We can reassign ALL 57 opcodes.
    # Can we find 57 values from {0,...,2047} with minimum pairwise distance >= 3?
    # And beyond that, >= 4?

    # Approach: try to find such an assignment greedily, or use known
    # coding theory results.

    # For d_min = 3: We need a (11, 57, 3) code.
    # Hamming bound: 57 * 12 = 684 ≤ 2048. YES, feasible.
    # Singleton bound: 57 ≤ 2^(11 - 2) = 512. YES, feasible.
    # Plotkin bound: n=11, d=3: not binding since d < n/2.

    # For d_min = 4: Hamming bound: 57 * sum C(11,0..1) = 57 * 12 = 684 ≤ 2048. YES.
    #   Wait, for d=4, the sphere-packing bound uses t=1 (floor((4-1)/2)=1):
    #   57 * V(11,1) = 57 * 12 = 684 ≤ 2048. Feasible.
    # Singleton bound: 57 ≤ 2^(11-3) = 256. YES.

    # For d_min = 5: sphere-packing uses t=2: 57 * V(11,2) = 57 * (1+11+55) = 57*67 = 3819 > 2048.
    #   So d_min = 5 might be too much for 57 codewords.
    #   Singleton: 57 ≤ 2^(11-4) = 128. Yes, but Plotkin?
    #   Plotkin: for d=5 and n=11: M ≤ 2*floor(d/(2d-n)) = hmm...
    #   n=11, d=5: 2d = 10 < n+1 = 12, so Plotkin gives M ≤ 2*floor(5/(10-11))... doesn't apply.
    #   Actually for even d: Plotkin applies when 2d > n. 2*5=10 < 11, so not applicable.
    #   The Hamming bound says ≤ 2048/67 ≈ 30.6 → at most 30 codewords for d≥5.
    #   So 57 codewords with d_min ≥ 5 is IMPOSSIBLE.

    print("Coding-theoretic feasibility:")
    print(f"  d_min = 3: Hamming bound allows ≤ {2048 // 12} codewords. Need 57. FEASIBLE.")
    print(f"  d_min = 4: Hamming bound allows ≤ {2048 // 12} codewords (t=1). FEASIBLE.")
    hamming_bound_5 = 2048 // (1 + 11 + 55)
    print(f"  d_min = 5: Hamming bound allows ≤ {hamming_bound_5} codewords (t=2). IMPOSSIBLE for 57.")
    print()

    # Let's actually CONSTRUCT a (11, 57, >=3) code greedily
    print("Greedy construction of (11, 57, d_min) codes:")
    for target_d in [3, 4]:
        code = greedy_code(11, 57, target_d)
        if code is not None:
            # Verify
            actual_min = 11
            for i in range(len(code)):
                for j2 in range(i + 1, len(code)):
                    d = hamming_distance(code[i], code[j2])
                    if d < actual_min:
                        actual_min = d
            print(f"  d_min = {target_d}: {'FOUND' if code else 'FAILED'} "
                  f"({len(code)} codewords, actual d_min = {actual_min})")
            if len(code) >= 57:
                print(f"    First 10 codewords: {code[:10]}")
        else:
            print(f"  d_min = {target_d}: FAILED (could not find 57 codewords)")

    return


def greedy_code(n, target_size, d_min):
    """Greedily construct a binary code of length n with minimum distance d_min."""
    code = [0]  # Start with 0
    candidates = list(range(1, 2**n))
    import random
    random.seed(42)  # Reproducible
    random.shuffle(candidates)

    for c in candidates:
        ok = True
        for existing in code:
            if hamming_distance(c, existing) < d_min:
                ok = False
                break
        if ok:
            code.append(c)
            if len(code) >= target_size:
                return code
    return code if len(code) >= target_size else None


# ═══════════════════════════════════════════════════════════════════
# PART 5: Theoretical best case for j and m isolation
# ═══════════════════════════════════════════════════════════════════

def part5():
    print("\n" + "=" * 72)
    print("PART 5: Can we make j and m UNREACHABLE by single-bit flips")
    print("        from any other opcode?")
    print("=" * 72)
    print()

    # If j and m are at Hamming distance >= 2 from all other 55 opcodes,
    # then NO single data-bit flip can produce j or m from any other opcode.
    #
    # But we need j and m to also be distance >= 2 from EACH OTHER.
    # (Unless we don't care about j<->m flips, since both are dangerous.)
    #
    # With distance >= 2 from all other opcodes: flipping 1 bit on any
    # non-dangerous opcode CANNOT produce j or m. That's the goal.
    #
    # But we need STRONGER: distance >= 2 means a single-bit flip can't
    # land on j or m. But the SECDED code corrects single-bit flips in
    # the 16-bit cell. The question is about DATA bit flips specifically.
    # Flipping a parity bit changes the cell but NOT the payload.
    # Flipping a data bit changes exactly one payload bit.
    # So: a single CELL bit flip on a data bit position changes the payload
    # Hamming distance by exactly 1.
    #
    # Therefore: if j and m payloads are at Hamming distance >= 2 from
    # all other used payloads, then NO single data-bit cell flip can
    # produce j or m from a non-dangerous opcode. QED.
    #
    # Can we find such an assignment?

    print("Strategy: assign 57 payload values such that the 2 dangerous")
    print("opcodes (j, m) have Hamming distance >= 2 from all 55 others.")
    print()

    # This is easy if d_min >= 3 for the full code, but we can also
    # do it with weaker constraints (only j and m need separation).

    # First, let's see how many 11-bit values have distance >= 2 from
    # all of {0, ..., 54} (the other 55 opcodes in sequential assignment).
    count_d2 = 0
    good_values = []
    for v in range(2048):
        if v <= 54:
            continue
        ok = True
        for u in range(55):
            if hamming_distance(v, u) < 2:
                ok = False
                break
        if ok:
            good_values.append(v)
            count_d2 += 1

    print(f"  Keeping other 55 opcodes at {{0,...,54}}:")
    print(f"  Values at distance >= 2 from all of {{0,...,54}}: {count_d2}")
    if count_d2 >= 2:
        # Check if we can find a pair that's also >= 2 from each other
        pairs = 0
        best_pair = None
        best_dist = 0
        for i in range(len(good_values)):
            for j2 in range(i + 1, len(good_values)):
                d = hamming_distance(good_values[i], good_values[j2])
                if d >= 2:
                    pairs += 1
                    if d > best_dist:
                        best_dist = d
                        best_pair = (good_values[i], good_values[j2])
        print(f"  Pairs with mutual distance >= 2: {pairs}")
        if best_pair:
            print(f"  Best pair: {best_pair[0]} ({format_binary(best_pair[0])}) and "
                  f"{best_pair[1]} ({format_binary(best_pair[1])}) at distance {best_dist}")
    print()

    # Now with FULL freedom (reassign all 57)
    print("With FULL reassignment freedom:")
    print("Using the d_min >= 3 code from Part 4...")

    code = greedy_code(11, 57, 3)
    if code and len(code) >= 57:
        # Pick any two as j and m
        # Verify: every pair has distance >= 3, so in particular j/m are
        # at distance >= 3 from all others.
        actual_min = 11
        for i in range(len(code)):
            for j2 in range(i + 1, len(code)):
                d = hamming_distance(code[i], code[j2])
                if d < actual_min:
                    actual_min = d

        print(f"  Found code with {len(code)} codewords, d_min = {actual_min}")
        print(f"  If d_min >= 3, then flipping 1 data bit on ANY opcode")
        print(f"  CANNOT produce any other opcode. This means:")
        print(f"    - 0 single-bit flips produce j or m from other opcodes")
        print(f"    - 0 single-bit flips produce ANY opcode from any other")
        print(f"    - Every single-data-bit flip produces a NOP (unused payload)")
        print()

        # Count: how many single-bit flips on ALL 57 code words -> another codeword?
        cross_flips = 0
        total_f = 0
        for cw in code:
            for bit in range(11):
                total_f += 1
                flipped = cw ^ (1 << bit)
                if flipped in set(code):
                    cross_flips += 1
        print(f"  Verification: {cross_flips}/{total_f} single-bit flips land on another codeword")
        print()

        # What about the dangerous-to-dangerous distance?
        # With d_min >= 3, j and m are at distance >= 3 from each other.
        # So even j can't become m via a single-bit flip.
        print(f"  j and m distance: >= {actual_min} (from code construction)")
        print(f"  Therefore j cannot become m (or vice versa) via single-bit flip.")
    else:
        print(f"  Greedy construction failed (got {len(code) if code else 0} codewords)")

    print()

    # Let's also try d_min = 4
    print("Can we do even better with d_min = 4?")
    code4 = greedy_code(11, 57, 4)
    if code4 and len(code4) >= 57:
        actual_min4 = 11
        for i in range(len(code4)):
            for j2 in range(i + 1, len(code4)):
                d = hamming_distance(code4[i], code4[j2])
                if d < actual_min4:
                    actual_min4 = d
        print(f"  d_min = 4 code: FOUND ({len(code4)} codewords, actual d_min = {actual_min4})")
        print(f"  With d_min >= 4:")
        print(f"    - Any SINGLE data-bit flip -> NOP (distance 1, can't reach another opcode)")
        print(f"    - Any TWO data-bit flips -> still can't reach another opcode (distance 2 < 4)")
        print(f"    - Only 3+ simultaneous data-bit flips could produce a wrong opcode")
        print(f"    - SECDED detects all 1-bit and 2-bit errors in the full 16-bit cell")
        print(f"    - So: uncorrectable data corruption requires >= 3 data bit flips")
        print()
        print(f"  This is EXTREMELY strong protection:")
        print(f"    A 3-bit data error that happens to land on another valid opcode")
        print(f"    would also likely have non-zero syndrome in the Hamming code,")
        print(f"    making it detectable (though not correctable) by SECDED.")

        # How many 3-data-bit errors on a codeword actually land on another?
        from itertools import combinations
        triple_hits = 0
        triple_total = 0
        for cw in code4:
            for bits in combinations(range(11), 3):
                triple_total += 1
                flipped = cw
                for b in bits:
                    flipped ^= (1 << b)
                if flipped in set(code4):
                    triple_hits += 1
        print(f"  3-data-bit flips landing on another codeword: {triple_hits}/{triple_total}")
    else:
        n_found = len(code4) if code4 else 0
        print(f"  d_min = 4 code: FAILED (greedy found only {n_found} codewords)")
        print(f"  (Greedy algorithm may not be optimal; a smarter search might work)")

    print()

    # ─── Summary comparison ────────────────────────────────────────
    print("=" * 72)
    print("SUMMARY: Current vs Optimal Encoding")
    print("=" * 72)
    print()

    # Current: count dangerous flips across all 57 opcodes
    current_dangerous = 0
    current_total = 0
    for src in range(57):
        for bit in range(11):
            current_total += 1
            flipped = src ^ (1 << bit)
            if flipped in DANGEROUS.values():
                current_dangerous += 1

    current_any_opcode = 0
    for src in range(57):
        for bit in range(11):
            flipped = src ^ (1 << bit)
            if flipped in ALL_USED and flipped != src:
                current_any_opcode += 1

    print("CURRENT ENCODING (sequential 0-56):")
    print(f"  Single-bit flips producing j or m: {current_dangerous}/{current_total} "
          f"({current_dangerous/current_total*100:.2f}%)")
    print(f"  Single-bit flips producing ANY other valid opcode: {current_any_opcode}/{current_total} "
          f"({current_any_opcode/current_total*100:.2f}%)")

    # Find the minimum distance in current encoding
    current_min = 11
    current_min_pair = None
    for i in range(57):
        for j2 in range(i + 1, 57):
            d = hamming_distance(i, j2)
            if d < current_min:
                current_min = d
                current_min_pair = (i, j2)

    print(f"  Minimum pairwise Hamming distance: {current_min} "
          f"(between opcodes {current_min_pair[0]} and {current_min_pair[1]})")
    # Count how many pairs have distance 1
    dist1_pairs = 0
    for i in range(57):
        for j2 in range(i + 1, 57):
            if hamming_distance(i, j2) == 1:
                dist1_pairs += 1
    print(f"  Pairs at distance 1: {dist1_pairs}")

    print()
    if code and len(code) >= 57:
        opt_min = 11
        for i in range(len(code)):
            for j2 in range(i + 1, len(code)):
                d = hamming_distance(code[i], code[j2])
                if d < opt_min:
                    opt_min = d

        print(f"OPTIMAL ENCODING (d_min = {opt_min} code):")
        print(f"  Single-bit flips producing j or m: 0/{11 * 57} (0.00%)")
        print(f"  Single-bit flips producing ANY other valid opcode: 0/{11 * 57} (0.00%)")
        print(f"  Minimum pairwise Hamming distance: {opt_min}")
        print(f"  Improvement factor: {current_dangerous} -> 0 cascade-causing flips (COMPLETE ELIMINATION)")

    print()
    print("CONCLUSION:")
    print("  With 57 opcodes in an 11-bit space (2048 values), there is")
    print("  enormous room for error-resistant encoding. A minimum-distance-3")
    print("  code COMPLETELY ELIMINATES the possibility of single-data-bit")
    print("  flips converting one opcode into another. This means:")
    print("  1. No cascade failures from j/m being produced by noise")
    print("  2. Every corrupted opcode becomes NOP (safe degradation)")
    print("  3. SECDED still detects the corruption for correction")

    return


# ═══════════════════════════════════════════════════════════════════
# PART 6: Additional detail - exact d_min=3 code construction
# ═══════════════════════════════════════════════════════════════════

def part6():
    print("\n" + "=" * 72)
    print("PART 6: Explicit d_min >= 3 code for all 57 opcodes")
    print("=" * 72)
    print()

    # Use a more systematic approach: take a BCH or Reed-Muller code
    # and select 57 codewords from it.
    # Or: just use the greedy code and display it.

    code = greedy_code(11, 57, 3)
    if code and len(code) >= 57:
        code = code[:57]
        print("Proposed opcode -> payload mapping (d_min = 3):")
        print(f"{'Opcode':>8s} {'Name':>6s} {'Old':>5s} {'New':>5s} {'Binary':>13s}")
        print("-" * 45)
        for i in range(57):
            name = OPCODE_NAMES.get(i, f"op{i}")
            old_val = i
            new_val = code[i]
            print(f"{i:>8d} {name:>6s} {old_val:>5d} {new_val:>5d} {format_binary(new_val)}")

        # Verify minimum distance
        actual_min = 11
        for i in range(57):
            for j2 in range(i + 1, 57):
                d = hamming_distance(code[i], code[j2])
                if d < actual_min:
                    actual_min = d
        print(f"\nVerified minimum pairwise distance: {actual_min}")

        # Show distance distribution
        from collections import Counter
        dist_hist = Counter()
        for i in range(57):
            for j2 in range(i + 1, 57):
                d = hamming_distance(code[i], code[j2])
                dist_hist[d] += 1
        print(f"\nDistance distribution:")
        for d in sorted(dist_hist.keys()):
            print(f"  d={d}: {dist_hist[d]} pairs")

    return


# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    part1()
    part2()
    part3()
    part4()
    part5()
    part6()

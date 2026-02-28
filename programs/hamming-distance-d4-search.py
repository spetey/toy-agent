#!/usr/bin/env python3
"""
Search for a d_min >= 4 code with 57 codewords in 11-bit space.

The greedy approach with random ordering failed. Let's try:
1. Systematic greedy (lexicographic order)
2. Multiple random seeds
3. Start from known good codes (shortened Reed-Muller, etc.)
4. Verify Plotkin/Singleton/Hamming bounds more carefully
"""

import random
from itertools import combinations

def hamming_distance(a, b):
    return bin(a ^ b).count('1')

def hamming_weight(a):
    return bin(a).count('1')

# ─── Bound analysis ──────────────────────────────────────────────

def analyze_bounds():
    n = 11
    target = 57
    print("=" * 60)
    print("Bound analysis for (11, M, d) codes")
    print("=" * 60)

    for d in range(1, 8):
        # Singleton bound: M ≤ 2^(n-d+1)
        singleton = 2 ** (n - d + 1)

        # Hamming (sphere-packing) bound: M ≤ 2^n / V(n, t) where t = floor((d-1)/2)
        t = (d - 1) // 2
        vol = sum(1 for _ in range(0))  # placeholder
        vol = 0
        for i in range(t + 1):
            # C(n, i)
            c = 1
            for j in range(i):
                c = c * (n - j) // (j + 1)
            vol += c
        hamming_bound = 2**n // vol if vol > 0 else 2**n

        # Plotkin bound (when 2d > n)
        plotkin = None
        if 2 * d > n:
            if d % 2 == 0:
                plotkin = 2 * (d // (2*d - n))
            else:
                plotkin = 2 * ((d+1) // (2*d - n + 1)) if 2*d - n + 1 > 0 else None

        # Griesmer bound: n ≥ sum_{i=0}^{k-1} ceil(d / 2^i) where M = 2^k
        # For M = 57, k ≈ 6 (2^6 = 64 > 57)

        feasible = "YES" if singleton >= target and hamming_bound >= target else "NO"
        if plotkin is not None and plotkin < target:
            feasible = "NO (Plotkin)"

        print(f"  d={d}: Singleton ≤ {singleton}, Hamming ≤ {hamming_bound}, "
              f"Plotkin ≤ {plotkin if plotkin else 'n/a'}  -> {feasible} for M={target}")

    print()

# ─── Smarter greedy search ──────────────────────────────────────

def greedy_code_systematic(n, target_size, d_min, seed=None):
    """Greedy code construction with specified seed."""
    candidates = list(range(2**n))
    if seed is not None:
        random.seed(seed)
        random.shuffle(candidates)

    code = []
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
    return code

def try_many_seeds(n, target_size, d_min, num_seeds=200):
    """Try many random seeds for greedy construction."""
    best = []
    best_seed = None
    for seed in range(num_seeds):
        code = greedy_code_systematic(n, target_size, d_min, seed=seed)
        if len(code) > len(best):
            best = code
            best_seed = seed
            if len(code) >= target_size:
                return code, seed
        if seed % 50 == 0 and seed > 0:
            print(f"    Tried {seed} seeds, best so far: {len(best)} (seed {best_seed})")
    return best, best_seed

# ─── Coset-based construction ──────────────────────────────────

def build_d4_code_from_even_weight():
    """
    For d_min = 4: use the even-weight subcode.

    The even-weight code of length n has d_min >= 2. Not enough.

    Better: use a [11, k, 4] linear code.
    The first-order Reed-Muller code R(1, m) has parameters [2^m, m+1, 2^(m-1)].
    R(1, 3) = [8, 4, 4], not long enough.

    For n=11, we can use shortened BCH codes or computer search.

    A [11, k, 4] code: Griesmer bound gives
    n >= ceil(4/1) + ceil(4/2) + ... + ceil(4/2^(k-1))
    k=1: n >= 4 -> yes
    k=2: n >= 4 + 2 = 6 -> yes
    k=3: n >= 4 + 2 + 1 = 7 -> yes
    k=4: n >= 4 + 2 + 1 + 1 = 8 -> yes
    k=5: n >= 4 + 2 + 1 + 1 + 1 = 9 -> yes
    k=6: n >= 4 + 2 + 1 + 1 + 1 + 1 = 10 -> yes
    k=7: n >= 4 + 2 + 1 + 1 + 1 + 1 + 1 = 11 -> yes
    k=8: n >= 4 + 2 + 1 + 1 + 1 + 1 + 1 + 1 = 12 -> NO

    So a [11, 7, 4] code MAY exist (2^7 = 128 codewords).
    We need only 57 ≤ 128. So this is feasible if the code exists!
    """
    # Try to construct a [11, 7, 4] code via generator matrix search
    # Start with identity matrix and add parity columns
    #
    # A [11, 7, 4] code has generator matrix G = [I_7 | P] where P is 7x4
    # and every row of G has weight >= 4, and every sum of rows has weight >= 4.
    # This means every nonzero codeword has weight >= 4.

    # The parity check matrix H = [P^T | I_4] has dimensions 4x11.
    # d_min >= 4 iff every 3 columns of H are linearly independent.
    # (Equivalently: no 3 columns sum to zero in GF(2).)

    # Let's search for a 4x7 matrix P^T such that:
    # 1. All 7 columns are nonzero (d_min >= 2)
    # 2. No two columns are equal (d_min >= 3)
    # 3. No three columns sum to zero (d_min >= 4)

    # There are 2^4 - 1 = 15 nonzero 4-bit vectors.
    # We need to pick 7 such that no 3 sum to 0 (in GF(2)).
    # Three columns c_i, c_j, c_k sum to 0 iff c_k = c_i XOR c_j.

    print("Constructing [11, 7, 4] linear code:")

    # All nonzero 4-bit vectors
    vectors = list(range(1, 16))  # 1 to 15

    # Try all combinations of 7 from 15
    best_cols = None
    for cols in combinations(vectors, 7):
        # Check: no three sum to zero
        ok = True
        for i in range(7):
            for j in range(i+1, 7):
                xor_val = cols[i] ^ cols[j]
                # xor_val must not be among the other columns
                for k in range(7):
                    if k != i and k != j and cols[k] == xor_val:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            best_cols = cols
            break

    if best_cols is None:
        print("  No [11, 7, 4] code found via simple search")
        return None

    print(f"  Found parity columns P^T: {best_cols}")
    print(f"  (As 4-bit vectors: {[format(c, '04b') for c in best_cols]})")

    # Build generator matrix: G = [I_7 | P]
    # Codeword = message * G, where message is a 7-bit vector
    # In systematic form: first 7 bits are the message, last 4 are parity
    codewords = []
    for msg in range(128):  # 2^7 = 128 messages
        cw = 0
        # Systematic part: bits 0-6 = message
        cw = msg
        # Parity part: bits 7-10
        parity = 0
        for i in range(7):
            if (msg >> i) & 1:
                parity ^= best_cols[i]
        cw |= (parity << 7)
        codewords.append(cw)

    # Verify d_min
    min_d = 11
    min_pair = None
    for i in range(len(codewords)):
        for j in range(i+1, len(codewords)):
            d = hamming_distance(codewords[i], codewords[j])
            if d < min_d:
                min_d = d
                min_pair = (i, j)

    print(f"  Generated {len(codewords)} codewords")
    print(f"  Actual d_min = {min_d}")
    if min_d >= 4:
        print(f"  SUCCESS: [11, 7, 4] code with 128 codewords, d_min = {min_d}")
        print(f"  We need only 57 codewords -> can select any 57 from these 128!")
    else:
        print(f"  FAILED: d_min = {min_d}, need >= 4")
        # Try next combination

    return codewords if min_d >= 4 else None


# ─── Exhaustive search for best d_min=4 parity columns ────────

def find_all_d4_parity_sets():
    """Find ALL sets of 7 columns from {1,...,15} with no 3 summing to 0."""
    vectors = list(range(1, 16))
    valid_sets = []

    for cols in combinations(vectors, 7):
        ok = True
        for i in range(7):
            for j in range(i+1, 7):
                xor_val = cols[i] ^ cols[j]
                if xor_val in cols and xor_val != cols[i] and xor_val != cols[j]:
                    # Check if xor_val is at a different index
                    for k in range(7):
                        if k != i and k != j and cols[k] == xor_val:
                            ok = False
                            break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            valid_sets.append(cols)

    return valid_sets


def main():
    analyze_bounds()

    # Try d_min = 4 with many seeds
    print("Trying greedy d_min=4 with many random seeds:")
    code4, seed4 = try_many_seeds(11, 57, 4, num_seeds=500)
    print(f"  Best result: {len(code4)} codewords (seed {seed4})")
    if len(code4) >= 57:
        min_d = min(hamming_distance(code4[i], code4[j])
                    for i in range(57) for j in range(i+1, 57))
        print(f"  d_min = {min_d}")
    print()

    # Try algebraic construction
    codewords = build_d4_code_from_even_weight()

    if codewords:
        print()
        print("Selecting 57 codewords from the [11, 7, 4] code:")
        selected = codewords[:57]

        # Verify
        min_d = 11
        for i in range(57):
            for j in range(i+1, 57):
                d = hamming_distance(selected[i], selected[j])
                if d < min_d:
                    min_d = d
        print(f"  Selected 57 codewords, d_min = {min_d}")

        # Show the mapping
        OPCODE_NAMES = {
            0: 'NOP', 1: '/', 2: '\\', 3: '%', 4: '?', 5: '&', 6: '!',
            7: 'N', 8: 'S', 9: 'E', 10: 'W', 11: 'n', 12: 's', 13: 'e', 14: 'w',
            15: '+', 16: '-', 17: '.', 18: ',', 19: 'X', 20: 'F', 21: 'G', 22: 'T',
            23: '>', 24: '<', 25: '^', 26: 'v', 27: 'P', 28: 'Q',
            29: ']', 30: '[', 31: '}', 32: '{', 33: 'K', 34: '(', 35: ')',
            36: '#', 37: '$', 38: 'Z', 39: 'x', 40: 'r', 41: 'l',
            42: 'f', 43: 'z', 44: 'R', 45: 'L', 46: 'Y', 47: ':', 48: ';',
            49: 'H2N', 50: 'H2S', 51: 'H2E', 52: 'H2W',
            53: 'm', 54: 'M', 55: 'j', 56: 'V',
        }

        print(f"\n{'Opcode':>8s} {'Name':>5s} {'Payload':>8s} {'Binary':>13s}")
        print("-" * 40)
        for i in range(57):
            name = OPCODE_NAMES.get(i, f"op{i}")
            print(f"{i:>8d} {name:>5s} {selected[i]:>8d} {format(selected[i], '011b')}")

        # Cross-flip analysis
        selected_set = set(selected)
        cross_flips = 0
        total_flips = 57 * 11
        for cw in selected:
            for bit in range(11):
                flipped = cw ^ (1 << bit)
                if flipped in selected_set:
                    cross_flips += 1
        print(f"\nSingle-bit flips landing on another codeword: {cross_flips}/{total_flips}")

        # Distance distribution
        from collections import Counter
        dist_hist = Counter()
        for i in range(57):
            for j in range(i+1, 57):
                d = hamming_distance(selected[i], selected[j])
                dist_hist[d] += 1
        print(f"\nDistance distribution:")
        for d in sorted(dist_hist.keys()):
            print(f"  d={d}: {dist_hist[d]} pairs")

        # What about 2-bit flips?
        two_bit_cross = 0
        total_2bit = 0
        for cw in selected:
            for b1 in range(11):
                for b2 in range(b1+1, 11):
                    total_2bit += 1
                    flipped = cw ^ (1 << b1) ^ (1 << b2)
                    if flipped in selected_set:
                        two_bit_cross += 1
        print(f"\n2-bit data flips landing on another codeword: {two_bit_cross}/{total_2bit}")

        # 3-bit flips?
        three_bit_cross = 0
        total_3bit = 0
        for cw in selected:
            for bits in combinations(range(11), 3):
                total_3bit += 1
                flipped = cw
                for b in bits:
                    flipped ^= (1 << b)
                if flipped in selected_set:
                    three_bit_cross += 1
        print(f"3-bit data flips landing on another codeword: {three_bit_cross}/{total_3bit}")

    # How many valid parity column sets exist?
    print("\n" + "=" * 60)
    print("Searching for ALL valid [11,7,4] parity column sets...")
    valid = find_all_d4_parity_sets()
    print(f"Found {len(valid)} valid sets of 7 parity columns from {{1,...,15}}")
    if valid:
        print(f"First 5: {valid[:5]}")


if __name__ == '__main__':
    main()

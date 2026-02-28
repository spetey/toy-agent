#!/usr/bin/env python3
"""
Correct construction of [11, k, 4] linear binary codes.

The d_min of a linear code [n, k, d] equals the minimum weight of any
nonzero codeword. For a systematic code with G = [I_k | P], the parity
check matrix is H = [-P^T | I_{n-k}] = [P^T | I_{n-k}] (over GF(2)).

d_min >= 4 iff every set of 3 columns of H is linearly independent (over GF(2)).

H has n = 11 columns, each of height r = n-k.
The first k columns are the rows of P (transposed), the last r are I_r.
"""

from itertools import combinations

def hamming_distance(a, b):
    return bin(a ^ b).count('1')

def check_d4_parity_check(H_cols, n, r):
    """
    Check if the parity check matrix H (given as a list of n column vectors,
    each an r-bit integer) has d_min >= 4.

    d >= 4 iff no 1, 2, or 3 columns of H are linearly dependent over GF(2).
    - No column is zero (d >= 2)
    - No two columns are equal (d >= 3)
    - No three columns XOR to zero (d >= 4)
    """
    assert len(H_cols) == n

    # Check no zero column
    for c in H_cols:
        if c == 0:
            return False

    # Check no two equal
    if len(set(H_cols)) < len(H_cols):
        return False

    # Check no three XOR to zero
    for i in range(n):
        for j in range(i+1, n):
            xor_ij = H_cols[i] ^ H_cols[j]
            for k in range(j+1, n):
                if H_cols[k] == xor_ij:
                    return False
    return True


def build_systematic_code(k, parity_cols, n):
    """
    Build a [n, k, d] systematic linear code.
    G = [I_k | P] where P is k x (n-k), given as parity_cols[i] = column i of P.
    Each parity_col is an integer representing the k-bit column.

    Wait, actually parity_cols should be the ROWS of P (one per message bit).
    Let me be precise:

    G is k x n. Row i of G is: e_i (k-bit) | P_row_i ((n-k)-bit).
    Codeword for message m: c = m * G (over GF(2)).
    c[0..k-1] = m (systematic part)
    c[k..n-1] = sum of P_row_i for each bit i set in m.

    H is (n-k) x n. H = [P^T | I_{n-k}].
    Column j of H for j < k: column j of P^T = row j of P = P_row_j.
    Column j of H for j >= k: column (j-k) of I_{n-k}.

    For d_min checking, H columns are:
    - Columns 0..k-1: P_row[0], ..., P_row[k-1] (each (n-k)-bit)
    - Columns k..n-1: e_0, ..., e_{n-k-1} (standard basis, (n-k)-bit)
    """
    r = n - k
    # H columns
    H_cols = list(parity_cols)  # First k columns: P rows as (n-k)-bit ints
    for i in range(r):
        H_cols.append(1 << i)  # Identity columns

    if not check_d4_parity_check(H_cols, n, r):
        return None

    # Generate all codewords
    codewords = []
    for msg in range(2**k):
        # Systematic part
        cw = msg
        # Parity part
        parity = 0
        for i in range(k):
            if (msg >> i) & 1:
                parity ^= parity_cols[i]
        cw |= (parity << k)
        codewords.append(cw)

    return codewords


def search_d4_code(n, k):
    """Search for a [n, k, >=4] linear code by trying all parity row sets."""
    r = n - k
    max_parity_val = (1 << r) - 1  # Maximum value for each parity row

    # Each parity row (P_row[i]) is an r-bit value.
    # H columns = [P_row[0], ..., P_row[k-1], e_0, ..., e_{r-1}]
    # Constraint: all H columns distinct, nonzero, no 3 XOR to 0.

    # The identity columns are e_0 = 1, e_1 = 2, ..., e_{r-1} = 2^{r-1}.
    # So parity rows must:
    # 1. All be nonzero
    # 2. All be distinct from each other
    # 3. All be distinct from any e_i (powers of 2, 0..r-1)
    # 4. No three H columns XOR to 0

    identity_cols = set(1 << i for i in range(r))

    # Valid parity values: nonzero, not a power of 2 (those are identity cols)
    # Wait - they CAN be powers of 2 if weight >= 2? No:
    # Actually, they must be distinct from identity cols.
    # P_row values that equal a power of 2 would make two H columns equal.
    # So exclude powers of 2 and 0.
    valid_parity = [v for v in range(1, max_parity_val + 1)
                    if v not in identity_cols]

    # Also need weight >= 2 for each parity row? No, that's not required.
    # Weight 1 values not in identity_cols would be fine... but wait,
    # all weight-1 values ARE powers of 2, which are in identity_cols.
    # So valid_parity already excludes all weight-1 values.
    # This means all valid parity values have weight >= 2. Good.

    print(f"Searching for [{n}, {k}, >=4] code:")
    print(f"  r = {r} parity bits")
    print(f"  Valid parity row values: {len(valid_parity)} (out of {max_parity_val})")
    print(f"  Need to choose {k} parity rows from {len(valid_parity)} values")
    print(f"  Combinations to check: C({len(valid_parity)}, {k})")

    count = 0
    found = None
    for parity_rows in combinations(valid_parity, k):
        count += 1
        H_cols = list(parity_rows) + [1 << i for i in range(r)]
        if check_d4_parity_check(H_cols, n, r):
            codewords = build_systematic_code(k, parity_rows, n)
            if codewords:
                # Verify d_min
                min_d = n
                for i in range(len(codewords)):
                    w = bin(codewords[i]).count('1')
                    if w > 0 and w < min_d:
                        min_d = w
                print(f"  Found after {count} attempts!")
                print(f"  Parity rows: {parity_rows}")
                print(f"  Codewords: {len(codewords)}, d_min = {min_d}")
                found = (parity_rows, codewords, min_d)
                return found

        if count % 100000 == 0:
            print(f"    Checked {count} combinations...")

    print(f"  Exhausted {count} combinations, no code found.")
    return None


def main():
    n = 11

    # Try different k values
    # k=7: 128 codewords (more than enough for 57)
    # k=6: 64 codewords (enough for 57)
    # k=5: 32 codewords (not enough)

    for k in [6, 7]:
        result = search_d4_code(n, k)
        if result:
            parity_rows, codewords, min_d = result

            # Verify pairwise distances
            actual_min = n
            for i in range(len(codewords)):
                for j in range(i+1, len(codewords)):
                    d = hamming_distance(codewords[i], codewords[j])
                    if d < actual_min:
                        actual_min = d
            print(f"  Verified pairwise d_min = {actual_min}")

            if len(codewords) >= 57:
                selected = codewords[:57]
                selected_set = set(selected)

                # Single-bit flip analysis
                cross_1 = sum(1 for cw in selected for b in range(n)
                              if (cw ^ (1 << b)) in selected_set and (cw ^ (1 << b)) != cw)
                print(f"\n  Single-bit flips (full 11 bits) landing on another codeword: {cross_1}/{57*11}")

                # But wait - we care about DATA bit flips in the 16-bit cell.
                # In the ORIGINAL encoding, payload bits map to specific cell positions.
                # If we REASSIGN payload values, the 11 data bits in the 16-bit cell
                # still flip independently. A single data-bit flip in the cell
                # = a single payload-bit flip. So flipping any 1 of the 11 payload bits
                # = flipping exactly 1 data bit. The analysis is the same.
                print(f"  (Data-bit flips in 16-bit cell = payload-bit flips, same analysis)")

                # Two-bit flips
                cross_2 = 0
                for cw in selected:
                    for b1 in range(n):
                        for b2 in range(b1+1, n):
                            f = cw ^ (1 << b1) ^ (1 << b2)
                            if f in selected_set and f != cw:
                                cross_2 += 1
                print(f"  Two-bit flips landing on another codeword: {cross_2}/{57*55}")

                # Three-bit flips
                cross_3 = 0
                for cw in selected:
                    for bits in combinations(range(n), 3):
                        f = cw
                        for b in bits:
                            f ^= (1 << b)
                        if f in selected_set and f != cw:
                            cross_3 += 1
                total_3 = 57 * (11*10*9 // 6)
                print(f"  Three-bit flips landing on another codeword: {cross_3}/{total_3}")

                # Show the proposed mapping
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

                print(f"\nProposed d_min={actual_min} opcode encoding:")
                print(f"{'Opcode':>8s} {'Name':>5s} {'Payload':>8s} {'Binary':>13s}")
                print("-" * 40)
                for i in range(57):
                    name = OPCODE_NAMES.get(i, f"op{i}")
                    print(f"{i:>8d} {name:>5s} {selected[i]:>8d} {format(selected[i], '011b')}")

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

            break  # Found one, stop searching
        print()


if __name__ == '__main__':
    main()

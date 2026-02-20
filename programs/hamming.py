#!/usr/bin/env python3
"""
hamming.py — Hamming(8,4) SECDED for fb2d error correction.

Encoding: 4 data bits → 8-bit codeword with 3 parity + 1 overall parity.

BIT LAYOUT (standard Hamming positions):
  Bit position:  7  6  5  4  3  2  1  0
  Role:          p0 d3 d2 d1 p2 d0 p1 p
  Hamming index: -  7  6  5  4  3  2  1   (p0 is overall parity, index 0)

  p  (bit 0) = overall parity of bits 1-7
  p1 (bit 1) = parity of positions with bit 0 set in index: 1,3,5,7 = p1,d0,d1,d3
  p2 (bit 2) = parity of positions with bit 1 set in index: 2,3,6,7 = p1 wait...

Actually, let me use the standard Hamming(7,4) layout more carefully.

STANDARD HAMMING(7,4) — positions 1-7:
  Position:  7    6    5    4    3    2    1
  Role:      d3   d2   d1   p2   d0   p1   p0

  p0 (pos 1) covers positions with bit 0 set: 1,3,5,7 → p0,d0,d1,d3
  p1 (pos 2) covers positions with bit 1 set: 2,3,6,7 → p1,d0,d2,d3
  p2 (pos 4) covers positions with bit 2 set: 4,5,6,7 → p2,d1,d2,d3

Syndrome s2 s1 s0 gives the position of the bad bit (0 = no error).

SECDED extension: add bit 0 as overall parity of all 7 bits.

  Bit:    7    6    5    4    3    2    1    0
  Role:   d3   d2   d1   p2   d0   p1   p0   p_all

  p_all = XOR of bits 1-7

Detection:
  syndrome = 3-bit value from parity checks
  p_all_check = XOR of all 8 bits (should be 0)

  syndrome=0, p_all_check=0 → no error
  syndrome≠0, p_all_check≠0 → single-bit error at position=syndrome (correctable)
                                (if syndrome=0 and p_all_check≠0, error is in p_all itself)
  syndrome≠0, p_all_check=0 → double-bit error (detected, uncorrectable)

Wait, I need to be more careful about p_all.

If the error is in bit 0 (p_all itself):
  syndrome = 0 (all Hamming parities still ok)
  p_all_check = 1 (overall parity wrong)
  → single error in p_all, correct by flipping bit 0

If error is in bits 1-7:
  syndrome ≠ 0 (points to the bad position)
  p_all_check = 1 (overall parity wrong)
  → single error, correct at position given by syndrome

If two errors in bits 1-7:
  syndrome ≠ 0
  p_all_check = 0 (two flips cancel in overall parity)
  → double error detected

This is standard SECDED.

FOR FB2D: We need to compute syndrome using the available bit ops.
The key operation: XOR subsets of bits together to get each syndrome bit.
We can access individual bits by rotating to position 0 and using z (bit-0 swap).

APPROACH FOR FB2D:
  The codeword is in one cell (8 bits). The syndrome is computed in a
  scratch cell. To check parity of a subset of bits:
  1. Copy codeword to scratch (via X swap or . add)
  2. Mask/extract relevant bits via rotate + z
  3. XOR them together

  Actually much simpler: XOR the codeword with itself rotated.

  Even simpler for fb2d: use XOR between two cells.

  Let me think about what's practical with the available ops:
  - x: [H0] ^= [H1]  (byte XOR)
  - r/l: rotate right/left 1 bit
  - z: swap bit 0 of [H0] with bit 0 of [GP]
  - f: if [CL]&1: swap([H0], [H1])

  The syndrome computation needs to extract and XOR specific bit subsets.

  PRACTICAL APPROACH: Compute syndrome in a scratch byte by accumulating
  XORs of rotated copies of the codeword. Each parity check covers
  specific bit positions. By rotating the codeword to align target bits
  with bit 0, XOR-ing into a syndrome accumulator, we build up the syndrome.

Let me first write the pure Python reference, then figure out the fb2d mapping.
"""


def encode(data4):
    """Encode 4-bit data into 8-bit SECDED Hamming codeword.

    data4: integer 0-15 (4 data bits)
    Returns: integer 0-255 (8-bit codeword)

    Bit layout of result:
      bit 7: d3    bit 6: d2    bit 5: d1    bit 4: p2
      bit 3: d0    bit 2: p1    bit 1: p0    bit 0: p_all
    """
    assert 0 <= data4 <= 15
    d0 = (data4 >> 0) & 1
    d1 = (data4 >> 1) & 1
    d2 = (data4 >> 2) & 1
    d3 = (data4 >> 3) & 1

    # Parity bits (Hamming positions 1, 2, 4)
    p0 = d0 ^ d1 ^ d3       # covers positions 1,3,5,7 → p0,d0,d1,d3
    p1 = d0 ^ d2 ^ d3       # covers positions 2,3,6,7 → p1,d0,d2,d3
    p2 = d1 ^ d2 ^ d3       # covers positions 4,5,6,7 → p2,d1,d2,d3

    # Assemble bits 7-1 (Hamming codeword)
    #   pos 7=d3, pos 6=d2, pos 5=d1, pos 4=p2, pos 3=d0, pos 2=p1, pos 1=p0
    codeword_7 = (d3 << 6) | (d2 << 5) | (d1 << 4) | (p2 << 3) | (d0 << 2) | (p1 << 1) | p0

    # Overall parity (bit 0): XOR of bits 1-7
    p_all = 0
    for i in range(7):
        p_all ^= (codeword_7 >> i) & 1

    # Full 8-bit codeword: shift Hamming bits up by 1, add p_all at bit 0
    codeword = (codeword_7 << 1) | p_all

    return codeword


def decode(codeword):
    """Decode 8-bit SECDED Hamming codeword.

    Returns: (data4, syndrome, p_all_err, corrected_codeword)
      data4: extracted 4-bit data (after correction if single error)
      syndrome: 3-bit syndrome (0 = no Hamming error)
      p_all_err: overall parity error flag (1 = odd number of bit errors)
      corrected_codeword: codeword after correction (if applicable)
    """
    # Extract bits (using our layout: bit 0 = p_all, bits 1-7 = Hamming)
    p_all = (codeword >> 0) & 1
    p0    = (codeword >> 1) & 1  # Hamming pos 1
    p1    = (codeword >> 2) & 1  # Hamming pos 2
    d0    = (codeword >> 3) & 1  # Hamming pos 3
    p2    = (codeword >> 4) & 1  # Hamming pos 4
    d1    = (codeword >> 5) & 1  # Hamming pos 5
    d2    = (codeword >> 6) & 1  # Hamming pos 6
    d3    = (codeword >> 7) & 1  # Hamming pos 7

    # Syndrome bits
    s0 = p0 ^ d0 ^ d1 ^ d3      # check positions 1,3,5,7
    s1 = p1 ^ d0 ^ d2 ^ d3      # check positions 2,3,6,7
    s2 = p2 ^ d1 ^ d2 ^ d3      # check positions 4,5,6,7

    syndrome = (s2 << 2) | (s1 << 1) | s0

    # Overall parity check
    all_bits_xor = 0
    for i in range(8):
        all_bits_xor ^= (codeword >> i) & 1
    p_all_err = all_bits_xor  # should be 0 if no error

    # Correction
    corrected = codeword
    if syndrome != 0 and p_all_err:
        # Single-bit error: syndrome gives Hamming position (1-7)
        # Map Hamming position to bit position in our codeword
        # Hamming pos N is at bit N in our codeword (since bit 0 = p_all,
        # bit 1 = Hamming pos 1, bit 2 = Hamming pos 2, etc.)
        bit_to_flip = syndrome  # Hamming position = bit position in codeword
        corrected = codeword ^ (1 << bit_to_flip)
    elif syndrome == 0 and p_all_err:
        # Error in p_all (bit 0)
        corrected = codeword ^ 1

    # Extract data from corrected codeword
    data4 = (((corrected >> 7) & 1) << 3 |  # d3
             ((corrected >> 6) & 1) << 2 |  # d2
             ((corrected >> 5) & 1) << 1 |  # d1
             ((corrected >> 3) & 1) << 0)   # d0

    return data4, syndrome, p_all_err, corrected


def inject_error(codeword, bit_pos):
    """Flip a single bit at position bit_pos (0-7)."""
    return codeword ^ (1 << bit_pos)


def inject_double_error(codeword, pos1, pos2):
    """Flip two bits."""
    return codeword ^ (1 << pos1) ^ (1 << pos2)


# ─── Mapping for fb2d: which bit positions to XOR for each syndrome bit ───
#
# Our codeword layout (MSB to LSB):
#   bit 7: d3    bit 6: d2    bit 5: d1    bit 4: p2
#   bit 3: d0    bit 2: p1    bit 1: p0    bit 0: p_all
#
# Syndrome s0 checks Hamming positions {1,3,5,7} = bits {1,3,5,7} in codeword
# Syndrome s1 checks Hamming positions {2,3,6,7} = bits {2,3,6,7} in codeword
# Syndrome s2 checks Hamming positions {4,5,6,7} = bits {4,5,6,7} in codeword
#
# To compute s0 in fb2d:
#   Need XOR of bits 1,3,5,7 of the codeword.
#   In a scratch byte, accumulate these bits one at a time:
#     rotate codeword right by 1 → bit 1 is now at bit 0. z-swap into scratch.
#     rotate codeword right by 2 more (total 3) → bit 3 at bit 0. XOR into scratch via z.
#     ... etc.
#
#   Actually easier: XOR the codeword with a mask. But we don't have AND/mask ops.
#
#   Alternative: Use the fact that XOR of specific bits can be computed by
#   XOR-ing rotated copies of the codeword. If we XOR the codeword with
#   itself rotated by various amounts, specific bits cancel or combine.
#
# This is getting complex. Let me think about what's actually feasible
# with the fb2d instruction set...
#
# SIMPLEST FB2D APPROACH:
#   Use 3 scratch cells for syndrome bits s0, s1, s2.
#   For each syndrome bit, iterate through the relevant bit positions:
#     1. Rotate codeword so target bit is at position 0
#     2. Use z to swap bit 0 of codeword with bit 0 of scratch
#        (accumulates XOR... wait, z is SWAP not XOR)
#
#   Hmm. z swaps bit 0, it doesn't XOR it. We need XOR accumulation.
#
#   What about: XOR the whole codeword into a scratch cell (using x),
#   then rotate and XOR again? The problem is x operates on full bytes.
#
# ACTUAL PRACTICAL APPROACH:
#   Compute syndrome by XOR-ing the whole codeword with specific rotations
#   of itself, then extracting the parity from bit 0.
#
#   syndrome s0 = bit1 ^ bit3 ^ bit5 ^ bit7
#
#   If we have the codeword C and compute C ^ (C >> 2):
#     bit 0 of result = bit0 ^ bit2 (not useful)
#     bit 1 of result = bit1 ^ bit3 ← part of s0!
#     bit 5 of result = bit5 ^ bit7 ← other part of s0!
#
#   Then if we take (C ^ (C>>2)) and compute that ^ (that >> 4):
#     bit 1 of result = (bit1 ^ bit3) ^ (bit5 ^ bit7) = s0!
#
#   So: s0 = bit 1 of (C ^ ror(C,2) ^ ror(C,4) ^ ror(C,6))
#   Wait that's getting complicated. Let me just use the bit-extraction approach.
#
# BIT EXTRACTION VIA ROTATE + MASK WITH z:
#   To XOR bit N of the codeword into bit 0 of a syndrome cell:
#   1. Copy codeword to a temp cell
#   2. Rotate temp right by N (so bit N is at position 0)
#   3. XOR temp's bit 0 into syndrome's bit 0
#
#   But we don't have "XOR bit 0 of X into bit 0 of Y". We have:
#   - z: swap bit 0 of [H0] with bit 0 of [GP]
#   - x: [H0] ^= [H1] (full byte)
#
#   For bit-level XOR accumulation into a single syndrome bit:
#   Option A: Use z to extract bit 0 into GP, then z again to deposit
#             into syndrome cell. But z is SWAP, not XOR.
#
#   Option B: Use full-byte XOR (x) on specially constructed bytes.
#             If the codeword is in cell A and we want to XOR just bit N
#             into syndrome cell S:
#             - Rotate A right by N so bit N is at bit 0
#             - XOR A into S (x): S ^= A. This XORs ALL bits, not just bit 0.
#             - Rotate A left by N to restore it.
#             Problem: S accumulates garbage in bits 1-7.
#             But if we only care about bit 0 of S at the end, we can
#             do all the XORs and then just look at bit 0 of S.
#
#   Wait — that might actually work! If we're computing s0 = bit1^bit3^bit5^bit7:
#     Start: S = 0
#     Rotate codeword right 1, XOR into S, rotate back.
#       S bit 0 = bit1. (Other bits get garbage, don't care.)
#     Rotate codeword right 3, XOR into S, rotate back.
#       S bit 0 = bit1 ^ bit3. (Still don't care about other bits.)
#     Rotate codeword right 5, XOR into S, rotate back.
#       S bit 0 = bit1 ^ bit3 ^ bit5.
#     Rotate codeword right 7, XOR into S, rotate back.
#       S bit 0 = bit1 ^ bit3 ^ bit5 ^ bit7 = s0!
#
#   YES! The garbage in bits 1-7 of S doesn't matter because we only
#   use bit 0 of S for the syndrome.
#
#   Cost per syndrome bit: 4 rotations + 4 XORs + 4 un-rotations = 12 ops.
#   For 3 syndrome bits: 36 ops. Plus overall parity.
#
#   But can we be smarter? Since rotating right by 7 is the same as
#   rotating left by 1, we can minimize rotation counts.
#
#   For the correction step: once we know the syndrome (which bit is bad),
#   we need to flip that bit. We can:
#   1. Rotate codeword so bad bit is at position 0
#   2. XOR bit 0 with 1 (how? increment by 1 if bit 0 is 0, decrement if 1?)
#      Actually: +1 flips bit 0 if it's 0, but +1 when bit 0 is 1 gives
#      carry into bit 1 (changes bit 1 too). Not good.
#
#   Better: XOR with a cell containing 1. x (XOR) with a cell that has
#   value 1 flips bit 0 and leaves all other bits unchanged.
#   So: put 1 in a scratch cell, XOR codeword with it.
#
#   But we need to flip the bit only IF there's an error (syndrome != 0
#   and p_all_err). Conditional execution in fb2d uses mirrors:
#   f (bit-0 Fredkin) or F (byte Fredkin) for conditional swap.
#
#   CORRECTION APPROACH:
#   1. Compute syndrome → 3 bits in a scratch cell
#   2. If syndrome != 0 and p_all_err:
#      a. Rotate codeword right by (syndrome) positions
#      b. XOR bit 0 with 1
#      c. Rotate back
#
#   The conditional part is the hard part. In fb2d, we don't have
#   if/else. But we have Fredkin gates: conditional swap.
#
#   ALTERNATIVE: Unconditional correction that's a no-op when no error.
#   If syndrome = 0, we rotate by 0 and XOR with 0 → no change.
#   If syndrome = N, we rotate by N and XOR with 1.
#   But "rotate by N" for variable N is a loop, which costs GP zeros.
#
#   FOR THE PROTOTYPE: Let's first verify the math, then worry about
#   the fb2d implementation.


def test_encode_decode():
    """Test all 16 possible data values, with and without errors."""
    print("=== Hamming(8,4) SECDED Tests ===\n")

    # Test all valid codewords
    print("--- Encode/Decode (no errors) ---")
    all_ok = True
    for data in range(16):
        cw = encode(data)
        decoded, syn, p_err, corrected = decode(cw)
        ok = (decoded == data and syn == 0 and p_err == 0)
        if not ok:
            print(f"  data={data:04b} cw={cw:08b}={cw:3d} → decoded={decoded:04b}"
                  f" syn={syn} p_err={p_err} FAIL")
            all_ok = False
    if all_ok:
        print(f"  All 16 data values: encode → decode correctly. PASS")

    # Test single-bit error correction
    print("\n--- Single-bit Error Correction ---")
    errors_corrected = 0
    errors_total = 0
    for data in range(16):
        cw = encode(data)
        for bit in range(8):
            errors_total += 1
            bad = inject_error(cw, bit)
            decoded, syn, p_err, corrected = decode(bad)
            if decoded != data or corrected != cw:
                print(f"  FAIL: data={data:04b} cw={cw:08b} flip bit {bit}"
                      f" → bad={bad:08b} decoded={decoded:04b}"
                      f" syn={syn} p_err={p_err}")
                all_ok = False
            else:
                errors_corrected += 1
    print(f"  {errors_corrected}/{errors_total} single-bit errors corrected. "
          f"{'PASS' if errors_corrected == errors_total else 'FAIL'}")

    # Test double-bit error detection
    print("\n--- Double-bit Error Detection ---")
    doubles_detected = 0
    doubles_total = 0
    for data in range(16):
        cw = encode(data)
        for b1 in range(8):
            for b2 in range(b1 + 1, 8):
                doubles_total += 1
                bad = inject_double_error(cw, b1, b2)
                decoded, syn, p_err, corrected = decode(bad)
                # Double error should have syndrome≠0 and p_err=0
                # OR syndrome=0 and p_err=0 (if the two errors cancel in syndrome)
                # Key: p_err should be 0 (even number of errors)
                if p_err == 0 and corrected != cw:
                    # Correctly detected (didn't miscorrect)
                    doubles_detected += 1
                elif p_err == 0 and corrected == cw:
                    # This shouldn't happen for 2 errors
                    print(f"  WEIRD: data={data:04b} flip {b1},{b2} → looks correct?")
                else:
                    # p_err=1 means it thinks it's a single error and will miscorrect
                    print(f"  MISCORRECT: data={data:04b} flip {b1},{b2}"
                          f" syn={syn} p_err={p_err}")
                    all_ok = False
    print(f"  {doubles_detected}/{doubles_total} double-bit errors detected. "
          f"{'PASS' if doubles_detected == doubles_total else 'FAIL'}")

    # Show some example codewords
    print("\n--- Example Codewords ---")
    print(f"  {'Data':>4s}  {'Codeword':>8s}  {'Dec':>3s}  {'Hex':>4s}")
    for data in range(16):
        cw = encode(data)
        print(f"  {data:04b}  {cw:08b}  {cw:3d}  0x{cw:02x}")

    # Show single-error correction examples
    print("\n--- Correction Examples ---")
    for data in [0b1010, 0b0110, 0b1111]:
        cw = encode(data)
        for bit in [0, 3, 7]:
            bad = inject_error(cw, bit)
            decoded, syn, p_err, corrected = decode(bad)
            status = "CORRECTED" if corrected == cw else "FAILED"
            print(f"  data={data:04b} cw={cw:08b} flip_bit={bit}"
                  f" → bad={bad:08b} syn={syn:03b} p={p_err}"
                  f" → corrected={corrected:08b} {status}")

    print(f"\n{'='*40}")
    print(f"Overall: {'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    return all_ok


def syndrome_via_xor_rotate(codeword):
    """Compute syndrome using the XOR-of-rotated-copies approach.

    This simulates what the fb2d gadget would do:
    for each syndrome bit, XOR the relevant bit positions by
    rotating the codeword to bring each target bit to position 0,
    then XOR-accumulating.

    Returns (s0, s1, s2, p_all_err)
    """
    # s0 checks Hamming positions {1,3,5,7} = codeword bits {1,3,5,7}
    s0 = 0
    for bit_pos in [1, 3, 5, 7]:
        s0 ^= (codeword >> bit_pos) & 1

    # s1 checks Hamming positions {2,3,6,7} = codeword bits {2,3,6,7}
    s1 = 0
    for bit_pos in [2, 3, 6, 7]:
        s1 ^= (codeword >> bit_pos) & 1

    # s2 checks Hamming positions {4,5,6,7} = codeword bits {4,5,6,7}
    s2 = 0
    for bit_pos in [4, 5, 6, 7]:
        s2 ^= (codeword >> bit_pos) & 1

    # Overall parity
    p_all_err = 0
    for i in range(8):
        p_all_err ^= (codeword >> i) & 1

    return s0, s1, s2, p_all_err


def test_syndrome_method():
    """Verify the XOR-rotate syndrome computation matches the direct method."""
    print("\n=== XOR-Rotate Syndrome Verification ===")
    all_ok = True
    for data in range(16):
        cw = encode(data)
        for bit in range(8):
            bad = inject_error(cw, bit)
            # Direct method
            _, syn_direct, p_err_direct, _ = decode(bad)
            # XOR-rotate method
            s0, s1, s2, p_err_rotate = syndrome_via_xor_rotate(bad)
            syn_rotate = (s2 << 2) | (s1 << 1) | s0

            if syn_direct != syn_rotate or p_err_direct != p_err_rotate:
                print(f"  MISMATCH: data={data:04b} flip={bit}"
                      f" direct syn={syn_direct} p={p_err_direct}"
                      f" rotate syn={syn_rotate} p={p_err_rotate}")
                all_ok = False

    # Also test no-error case
    for data in range(16):
        cw = encode(data)
        s0, s1, s2, p_err = syndrome_via_xor_rotate(cw)
        syn = (s2 << 2) | (s1 << 1) | s0
        if syn != 0 or p_err != 0:
            print(f"  MISMATCH (no error): data={data:04b} syn={syn} p={p_err}")
            all_ok = False

    print(f"  {'PASS' if all_ok else 'FAIL'}: XOR-rotate matches direct syndrome")
    return all_ok


def fb2d_syndrome_simulation(codeword):
    """Simulate the fb2d opcode sequence for syndrome computation.

    This models what the actual fb2d gadget does using only:
    - r (rotate right 1 bit)
    - l (rotate left 1 bit)
    - x ([H0] ^= [H1])
    - Operations on individual cells

    The approach: for each syndrome bit, XOR the codeword into a scratch
    cell after rotating to align each target bit with bit 0. We only care
    about bit 0 of the scratch cell at the end.

    Returns (syndrome, p_all_err) where syndrome is the 3-bit value.
    """
    # We work with these "cells" (simulating fb2d grid cells):
    cw = codeword           # the codeword cell (preserved via rotate-XOR-unrotate)
    scratch = 0             # scratch cell for accumulating syndrome bits
    syndrome = 0            # will hold 3-bit syndrome

    # --- Compute s0: XOR of bits {1, 3, 5, 7} ---
    scratch = 0
    for target_bit in [1, 3, 5, 7]:
        # Rotate codeword right by target_bit positions
        temp = cw
        for _ in range(target_bit):
            temp = ((temp >> 1) | ((temp & 1) << 7)) & 0xFF
        # XOR into scratch
        scratch ^= temp
        # (In fb2d: we'd rotate cw in-place, XOR, then rotate back.
        #  But since we're simulating, we use temp.)
    s0 = scratch & 1  # only bit 0 matters

    # --- Compute s1: XOR of bits {2, 3, 6, 7} ---
    scratch = 0
    for target_bit in [2, 3, 6, 7]:
        temp = cw
        for _ in range(target_bit):
            temp = ((temp >> 1) | ((temp & 1) << 7)) & 0xFF
        scratch ^= temp
    s1 = scratch & 1

    # --- Compute s2: XOR of bits {4, 5, 6, 7} ---
    scratch = 0
    for target_bit in [4, 5, 6, 7]:
        temp = cw
        for _ in range(target_bit):
            temp = ((temp >> 1) | ((temp & 1) << 7)) & 0xFF
        scratch ^= temp
    s2 = scratch & 1

    syndrome = (s2 << 2) | (s1 << 1) | s0

    # --- Overall parity: XOR all 8 bits ---
    # XOR the codeword with itself rotated by 4, then the result with
    # itself rotated by 2, then the result with itself rotated by 1.
    # This folds all 8 bits into bit 0 via a reduction tree.
    p = cw
    p ^= ((p >> 4) | ((p & 0xF) << 4)) & 0xFF  # fold: bit i ^= bit (i+4)
    p ^= ((p >> 2) | ((p & 0x3) << 6)) & 0xFF  # fold: bit i ^= bit (i+2)
    p ^= ((p >> 1) | ((p & 0x1) << 7)) & 0xFF  # fold: bit i ^= bit (i+1)
    p_all_err = p & 1

    return syndrome, p_all_err


def test_fb2d_syndrome():
    """Verify the fb2d-style syndrome computation."""
    print("\n=== FB2D-style Syndrome Simulation ===")
    all_ok = True

    for data in range(16):
        cw = encode(data)
        # Test no error
        syn, p_err = fb2d_syndrome_simulation(cw)
        if syn != 0 or p_err != 0:
            print(f"  FAIL (no error): data={data:04b} syn={syn} p={p_err}")
            all_ok = False

        # Test all single-bit errors
        for bit in range(8):
            bad = inject_error(cw, bit)
            syn, p_err = fb2d_syndrome_simulation(bad)
            _, syn_ref, p_ref, _ = decode(bad)
            if syn != syn_ref or p_err != p_ref:
                print(f"  FAIL: data={data:04b} flip={bit}"
                      f" got syn={syn} p={p_err}"
                      f" expected syn={syn_ref} p={p_ref}")
                all_ok = False

    print(f"  {'PASS' if all_ok else 'FAIL'}: fb2d-style syndrome matches reference")
    return all_ok


def fb2d_correction_simulation(codeword):
    """Simulate full SECDED correction using fb2d-compatible operations.

    Steps:
    1. Compute syndrome (3-bit) and overall parity error
    2. If single error detected: rotate codeword to bring bad bit to bit 0,
       XOR with 1 to flip it, rotate back.
    3. If no error or double error: no correction.

    The "rotate by syndrome" step requires a loop of (syndrome) iterations.
    In fb2d, this would be a ( P ... % bounded loop.

    Returns: corrected codeword
    """
    syndrome, p_all_err = fb2d_syndrome_simulation(codeword)

    if p_all_err == 0:
        # No error OR double error (uncorrectable) — don't touch it
        return codeword

    # Single-bit error. Syndrome tells us which bit (0-7).
    # But syndrome gives Hamming position 0-7 where 0 means "p_all at bit 0"
    # and 1-7 mean Hamming positions at bits 1-7.
    if syndrome == 0:
        # Error is in p_all (bit 0)
        bit_to_flip = 0
    else:
        bit_to_flip = syndrome

    # Flip the bit: rotate right by bit_to_flip, XOR with 1, rotate left
    corrected = codeword
    # Rotate right
    for _ in range(bit_to_flip):
        corrected = ((corrected >> 1) | ((corrected & 1) << 7)) & 0xFF
    # XOR bit 0 with 1 (flip it)
    corrected ^= 1
    # Rotate left (undo)
    for _ in range(bit_to_flip):
        corrected = (((corrected << 1) & 0xFF) | (corrected >> 7)) & 0xFF

    return corrected


def test_fb2d_correction():
    """Test the full fb2d-compatible correction pipeline."""
    print("\n=== FB2D-style Full Correction ===")
    all_ok = True
    corrected_count = 0
    total = 0

    for data in range(16):
        cw = encode(data)

        # No error
        result = fb2d_correction_simulation(cw)
        if result != cw:
            print(f"  FAIL (no error modified): data={data:04b}")
            all_ok = False

        # All single-bit errors
        for bit in range(8):
            total += 1
            bad = inject_error(cw, bit)
            result = fb2d_correction_simulation(bad)
            if result == cw:
                corrected_count += 1
            else:
                print(f"  FAIL: data={data:04b} flip={bit}"
                      f" bad={bad:08b} got={result:08b} expected={cw:08b}")
                all_ok = False

    print(f"  {corrected_count}/{total} single-bit errors corrected. "
          f"{'PASS' if corrected_count == total else 'FAIL'}")

    # Double error: should NOT be "corrected" (p_all_err = 0 → no correction)
    double_safe = 0
    double_total = 0
    for data in range(16):
        cw = encode(data)
        for b1 in range(8):
            for b2 in range(b1 + 1, 8):
                double_total += 1
                bad = inject_double_error(cw, b1, b2)
                result = fb2d_correction_simulation(bad)
                if result == bad:
                    # Correctly left alone (not miscorrected)
                    double_safe += 1
                else:
                    print(f"  MISCORRECT double: data={data:04b} flip={b1},{b2}")
                    all_ok = False

    print(f"  {double_safe}/{double_total} double-bit errors left alone. "
          f"{'PASS' if double_safe == double_total else 'FAIL'}")

    print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == '__main__':
    ok = True
    ok &= test_encode_decode()
    ok &= test_syndrome_method()
    ok &= test_fb2d_syndrome()
    ok &= test_fb2d_correction()

    print(f"\n{'='*50}")
    print(f"{'ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'}")
    print(f"{'='*50}")

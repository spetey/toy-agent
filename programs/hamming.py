#!/usr/bin/env python3
"""
hamming.py — Hamming(16,11) SECDED for fb2d error correction.

Encoding: 11 data bits → 16-bit codeword with 4 Hamming parity + 1 overall parity.

STANDARD-FORM BIT LAYOUT:
  Bit:  15  14  13  12  11  10   9   8   7   6   5   4   3   2   1   0
  Role: d10 d9  d8  d7  d6  d5  d4  p3  d3  d2  d1  p2  d0  p1  p0  p∀

  Parity bits at powers of 2: positions {0, 1, 2, 4, 8}
    - Bit 0: overall parity (p∀)
    - Bit 1: Hamming parity p0
    - Bit 2: Hamming parity p1
    - Bit 4: Hamming parity p2
    - Bit 8: Hamming parity p3
  Data bits: positions {3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15}

KEY PROPERTY: syndrome value == bit position of error!
  This enables the R/L correction trick in the fb2d gadget.

SYNDROME MASKS (classic Hamming check):
  s0 = popcount(cw & 0xAAAA) % 2   (all positions with bit 0 set in index)
  s1 = popcount(cw & 0xCCCC) % 2   (all positions with bit 1 set in index)
  s2 = popcount(cw & 0xF0F0) % 2   (all positions with bit 2 set in index)
  s3 = popcount(cw & 0xFF00) % 2   (all positions with bit 3 set in index)
  syndrome = s0 | (s1<<1) | (s2<<2) | (s3<<3)
  p_all = popcount(cw) % 2

DETECTION:
  syndrome=0, p_all_check=0 → no error
  syndrome≠0, p_all_check≠0 → single-bit error at bit [syndrome] (correctable)
  syndrome≠0, p_all_check=0 → double-bit error (detected, NOT correctable)
  syndrome=0, p_all_check≠0 → error in p_all bit (bit 0) only (correctable)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fb2d import (hamming_encode, hamming_syndrome, SYNDROME_SIG,
                  CELL_MASK, cell_to_payload, _popcount)

# ─── Syndrome-to-bit correction map ──────────────────────────────────

# Build the map: syndrome value → which bit position to flip.
# In standard form, syndrome == bit position (identity map!).
# Bit 0 (p_all) has syndrome 0, handled separately in decode().
SYNDROME_TO_BIT = {}
for _i in range(16):  # bits 0-15
    SYNDROME_TO_BIT[SYNDROME_SIG[_i]] = _i
# In standard form this is trivially {0:0, 1:1, ..., 15:15}
del _i


def encode(payload):
    """Encode an 11-bit payload (0-2047) into a 16-bit SECDED codeword."""
    return hamming_encode(payload)


def decode(codeword):
    """Decode 16-bit SECDED Hamming codeword.

    Returns: (payload, syndrome, p_all_err, corrected_codeword)
      payload: 11-bit data payload (after correction if single error)
      syndrome: 4-bit syndrome (0 = no Hamming error)
      p_all_err: overall parity error flag (1 = odd # of bit errors)
      corrected_codeword: codeword after correction (if applicable)
    """
    syndrome, p_all_err = hamming_syndrome(codeword)

    corrected = codeword
    if syndrome != 0 and p_all_err:
        # Single-bit error: flip the bit identified by syndrome
        bit_pos = SYNDROME_TO_BIT.get(syndrome)
        if bit_pos is not None:
            corrected = codeword ^ (1 << bit_pos)
    elif syndrome == 0 and p_all_err:
        # Error only in p_all (bit 0 in standard form)
        corrected = codeword ^ 1
    # syndrome≠0, p_all_err=0 → double-bit error (detected, not corrected)
    # syndrome=0, p_all_err=0 → no error

    payload = cell_to_payload(corrected)
    return payload, syndrome, p_all_err, corrected


def correct(codeword):
    """Correct a codeword (if single-bit error). Returns corrected codeword."""
    _, _, _, corrected = decode(codeword)
    return corrected


def syndrome(codeword):
    """Return 4-bit Hamming syndrome."""
    s, _ = hamming_syndrome(codeword)
    return s


def inject_error(codeword, bit_pos):
    """Flip a single bit in the codeword."""
    assert 0 <= bit_pos < 16
    return codeword ^ (1 << bit_pos)


def inject_double_error(codeword, bit1, bit2):
    """Flip two bits in the codeword (double-bit error)."""
    assert 0 <= bit1 < 16 and 0 <= bit2 < 16 and bit1 != bit2
    return codeword ^ (1 << bit1) ^ (1 << bit2)


# ─── Test suite ──────────────────────────────────────────────────────

def run_tests():
    """Comprehensive test suite for Hamming(16,11) SECDED."""
    print("=" * 60)
    print("Hamming(16,11) SECDED Test Suite")
    print("=" * 60)
    errors = 0

    # Test 1: Encode all 2048 payloads and verify decode
    print("\n--- Test 1: Encode/Decode all 2048 payloads ---")
    for p in range(2048):
        cw = encode(p)
        payload, syn, perr, corrected = decode(cw)
        if payload != p or syn != 0 or perr != 0 or corrected != cw:
            print(f"  FAIL at payload={p}: syn={syn}, perr={perr}")
            errors += 1
            if errors > 10:
                break
    if errors == 0:
        print(f"  PASS: All 2048 payloads encode/decode correctly")

    # Test 2: Single-bit error correction (all 16 positions, sample payloads)
    print("\n--- Test 2: Single-bit error correction ---")
    test_payloads = [0, 1, 2, 15, 42, 48, 100, 255, 1000, 1500, 2047]
    sec_errors = 0
    total_tests = 0
    for p in test_payloads:
        cw = encode(p)
        for bit in range(16):
            corrupted = inject_error(cw, bit)
            payload, syn, perr, corrected = decode(corrupted)
            total_tests += 1
            if payload != p:
                print(f"  FAIL: payload={p}, bit={bit}: got payload={payload}")
                sec_errors += 1
            if corrected != cw:
                print(f"  FAIL: payload={p}, bit={bit}: correction wrong")
                sec_errors += 1
    if sec_errors == 0:
        print(f"  PASS: {total_tests} single-bit corrections all correct")

    # Test 3: Exhaustive single-bit error correction (all payloads, all bits)
    print("\n--- Test 3: Exhaustive single-bit correction (all 2048*16) ---")
    exhaustive_errors = 0
    for p in range(2048):
        cw = encode(p)
        for bit in range(16):
            corrupted = inject_error(cw, bit)
            payload, _, _, _ = decode(corrupted)
            if payload != p:
                exhaustive_errors += 1
                if exhaustive_errors <= 5:
                    print(f"  FAIL: payload={p}, bit={bit}")
    if exhaustive_errors == 0:
        print(f"  PASS: All {2048*16} single-bit errors corrected")
    else:
        print(f"  FAIL: {exhaustive_errors} errors")
        errors += exhaustive_errors

    # Test 4: Double-bit error detection (should NOT miscorrect)
    print("\n--- Test 4: Double-bit error detection (sample) ---")
    ded_errors = 0
    test_payloads_ded = [0, 1, 42, 100, 1000, 2047]
    for p in test_payloads_ded:
        cw = encode(p)
        for b1 in range(16):
            for b2 in range(b1 + 1, 16):
                corrupted = cw ^ (1 << b1) ^ (1 << b2)
                syn, perr = hamming_syndrome(corrupted)
                if syn == 0 and perr == 0:
                    # Undetected double error!
                    print(f"  FAIL: payload={p}, bits={b1},{b2}: undetected!")
                    ded_errors += 1
                elif syn != 0 and perr != 0:
                    # Would miscorrect (thinks it's single-bit)
                    # This shouldn't happen in SECDED
                    pass  # SECDED: syn≠0, perr=0 means detected double
                # Good detection cases:
                # syn≠0, perr=0 → detected double (not correctable) ✓
                # syn=0, perr≠0 → impossible for double error in 16-bit
    if ded_errors == 0:
        print(f"  PASS: All sampled double-bit errors detected")
    else:
        errors += ded_errors

    # Test 5: Syndrome signature consistency
    print("\n--- Test 5: Syndrome signature map ---")
    sig_errors = 0
    for bit in range(15):
        cw = encode(0)  # valid codeword for payload 0 = all zeros
        corrupted = inject_error(cw, bit)
        syn, perr = hamming_syndrome(corrupted)
        expected_syn = SYNDROME_SIG[bit]
        if syn != expected_syn:
            print(f"  FAIL: bit {bit}: syn={syn}, expected={expected_syn}")
            sig_errors += 1
        if not perr:
            print(f"  FAIL: bit {bit}: p_all_err should be 1")
            sig_errors += 1
    if sig_errors == 0:
        print(f"  PASS: All 15 syndrome signatures correct")
    else:
        errors += sig_errors

    # Test 6: Some specific known values
    print("\n--- Test 6: Specific values ---")
    assert encode(0) == 0, f"encode(0) = {encode(0)}"
    cw1 = encode(1)
    assert cell_to_payload(cw1) == 1
    s, p = hamming_syndrome(cw1)
    assert s == 0 and p == 0
    print(f"  encode(0) = 0 ✓")
    print(f"  encode(1) = 0x{cw1:04x} (payload=1, syndrome=0) ✓")

    cw48 = encode(48)  # max opcode
    assert cell_to_payload(cw48) == 48
    s, p = hamming_syndrome(cw48)
    assert s == 0 and p == 0
    print(f"  encode(48) = 0x{cw48:04x} (opcode ';', syndrome=0) ✓")

    print(f"\n{'=' * 60}")
    if errors == 0:
        print("ALL TESTS PASSED!")
    else:
        print(f"FAILED: {errors} error(s)")
    print(f"{'=' * 60}")
    return errors == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)

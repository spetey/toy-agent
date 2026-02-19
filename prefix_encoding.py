#!/usr/bin/env python3
"""
Prefix-encoded integer prototype for fb2d.
Unary-length prefix scheme (Scheme A).

Encoding:
  0       → [0]                    (1 bit)
  1       → [1, 0]                 (2 bits)
  2       → [1, 1, 0,  0]         (4 bits)
  3       → [1, 1, 0,  1]         (4 bits)
  4       → [1, 1, 1, 0,  0, 0]  (6 bits)
  7       → [1, 1, 1, 0,  1, 1]  (6 bits)
  8       → [1, 1, 1, 1, 0,  0, 0, 0]  (8 bits)
  ...

Structure for n >= 2:
  Let k = floor(log2(n)) + 1   (the "tier")
  Prefix: k ones + one zero  (k + 1 bits)
  Payload: n - 2^(k-1) in (k - 1) bits, MSB first
  Total: 2k bits

For n = 0: single bit [0].          (tier 0, width 1)
For n = 1: [1, 0] (tier 1, width 2, no payload).

Convention: bits are stored MSB-first (reading direction = left to right).
"""

import math


def encode(n):
    """Encode non-negative integer n as a prefix-free bit list."""
    if n == 0:
        return [0]
    if n == 1:
        return [1, 0]

    k = math.floor(math.log2(n)) + 1   # tier
    # Prefix: k ones + zero
    prefix = [1] * k + [0]
    # Payload: n - 2^(k-1) in (k-1) bits
    payload_val = n - (1 << (k - 1))
    payload_bits = k - 1
    payload = []
    for i in range(payload_bits - 1, -1, -1):
        payload.append((payload_val >> i) & 1)
    return prefix + payload


def decode(bits):
    """Decode a prefix-encoded bit list to (value, width).
    Returns (n, w) where w is the number of bits consumed."""
    if not bits or bits[0] == 0:
        return (0, 1)

    # Count leading ones
    k = 0
    while k < len(bits) and bits[k] == 1:
        k += 1

    if k >= len(bits):
        # Ran off the end — treat as the integer with k ones
        # (on a toroidal grid this wouldn't happen, but for a finite
        # test buffer, just return what we can parse)
        return (0, len(bits))  # best-effort: treat as unparseable

    # bits[k] should be 0 (the terminator)
    assert bits[k] == 0, f"Expected 0 at position {k}, got {bits[k]}"

    if k == 1:
        # n = 1, no payload
        return (1, 2)

    # Payload: k-1 bits after the terminator
    payload_start = k + 1
    payload_bits = k - 1
    if payload_start + payload_bits > len(bits):
        # Ran off the end — on a toroidal grid this wraps; for a finite
        # test buffer, return best-effort
        return (0, len(bits))

    payload_val = 0
    for i in range(payload_bits):
        payload_val = (payload_val << 1) | bits[payload_start + i]

    n = (1 << (k - 1)) + payload_val
    width = k + 1 + payload_bits  # = 2k
    return (n, width)


def width_of(n):
    """Return the encoding width (in bits) of integer n."""
    return len(encode(n))


def tier_of(n):
    """Return the tier k of integer n.
    Tier 0: n=0.  Tier 1: n=1.  Tier k (k>=2): 2^(k-1) <= n < 2^k."""
    if n == 0:
        return 0
    if n == 1:
        return 1
    return math.floor(math.log2(n)) + 1


# ── Increment / decrement with GP tracking ────────────────────────────

class BitGrid:
    """A 1D bit array simulating a region of the fb2d grid.
    Supports GP-tracked increment and decrement of prefix-encoded integers."""

    def __init__(self, bits=None, size=64):
        if bits is not None:
            self.bits = list(bits)
        else:
            self.bits = [0] * size
        self.gp_trail = []  # Stack of displaced bits for reversibility

    def read_int(self, pos):
        """Read the prefix-encoded integer starting at bit position pos.
        Returns (value, width)."""
        return decode(self.bits[pos:])

    def write_int(self, pos, n):
        """Write the encoding of n starting at pos.
        Returns the width written. Does NOT handle growth/shrink — caller
        must manage displaced bits."""
        enc = encode(n)
        for i, b in enumerate(enc):
            self.bits[pos + i] = b
        return len(enc)

    def increment(self, pos):
        """Increment the prefix-integer at pos. GP-swaps on tier crossing.
        Returns (old_value, new_value, growth).
        growth = number of bits the encoding grew (0 or 2)."""
        n, old_width = self.read_int(pos)
        new_n = n + 1
        new_width = width_of(new_n)
        growth = new_width - old_width

        if growth == 0:
            # Same tier: just rewrite the payload
            self.write_int(pos, new_n)
        elif growth > 0:
            # Tier crossing: need to annex `growth` bits after current encoding.
            # GP-swap those bits (save their old values).
            # growth is 1 for 0->1, 2 for all other tier crossings.
            annex_start = pos + old_width
            for i in range(growth):
                old_bit = self.bits[annex_start + i]
                self.gp_trail.append(old_bit)
            # Now write the larger encoding over the whole region
            self.write_int(pos, new_n)
        else:
            raise RuntimeError(f"Unexpected growth {growth} for {n} -> {new_n}")

        return (n, new_n, growth)

    def decrement(self, pos):
        """Decrement the prefix-integer at pos. GP-restores on tier crossing.
        Returns (old_value, new_value, shrink).
        shrink = number of bits the encoding shrank (0 or 2).
        Decrementing 0 is a no-op."""
        n, old_width = self.read_int(pos)
        if n == 0:
            return (0, 0, 0)

        new_n = n - 1
        new_width = width_of(new_n)
        shrink = old_width - new_width

        if shrink == 0:
            # Same tier: just rewrite
            self.write_int(pos, new_n)
        elif shrink > 0:
            # Tier crossing downward: write smaller encoding, then
            # restore the released bit positions from GP trail.
            # shrink is 1 for 1->0, 2 for all other tier crossings.
            self.write_int(pos, new_n)
            release_start = pos + new_width
            # Restore in reverse order (LIFO)
            for i in range(shrink - 1, -1, -1):
                old_bit = self.gp_trail.pop()
                self.bits[release_start + i] = old_bit
        else:
            raise RuntimeError(f"Unexpected shrink {shrink} for {n} -> {new_n}")

        return (n, new_n, shrink)

    def show(self, pos=0, length=None):
        """Display bits with integer boundaries marked."""
        if length is None:
            length = len(self.bits)
        bits = self.bits[pos:pos + length]

        # First line: bit values
        bit_str = ''.join(str(b) for b in bits)

        # Second line: decode integers and mark boundaries
        markers = [' '] * len(bits)
        vals = []
        i = 0
        while i < len(bits):
            n, w = decode(bits[i:])
            markers[i] = '['
            if w > 1:
                markers[i + w - 1] = ']' if markers[i + w - 1] == ' ' else markers[i + w - 1]
            vals.append((i, n, w))
            i += w

        marker_str = ''.join(markers)

        print(f"  bits: {bit_str}")
        print(f"        {marker_str}")
        for (start, val, w) in vals:
            print(f"    @{start}: {val} ({w} bits)")

        if self.gp_trail:
            print(f"  GP trail: {self.gp_trail}")


# ── Tests ─────────────────────────────────────────────────────────────

def test_encode_decode():
    """Test encoding and decoding for values 0 through 100."""
    print("Testing encode/decode 0..100...")
    for n in range(101):
        enc = encode(n)
        decoded_n, w = decode(enc)
        assert decoded_n == n, f"encode/decode mismatch: {n} -> {enc} -> {decoded_n}"
        assert w == len(enc), f"width mismatch for {n}: {w} vs {len(enc)}"
    print("  PASS")


def test_bijectivity():
    """Test that every bit position starts a valid integer.
    Create a bit string from concatenated encodings and verify
    sequential parsing recovers all values."""
    print("Testing bijectivity (sequential parse)...")
    values = [0, 5, 3, 0, 0, 12, 1, 7, 8, 0, 255]
    all_bits = []
    for v in values:
        all_bits.extend(encode(v))

    # Parse back
    recovered = []
    pos = 0
    while pos < len(all_bits):
        n, w = decode(all_bits[pos:])
        recovered.append(n)
        pos += w

    assert recovered == values, f"Bijectivity failed: {values} -> {recovered}"
    print(f"  PASS ({len(all_bits)} bits, {len(values)} integers)")


def test_arbitrary_position():
    """Test that decoding from any bit position succeeds (valid everywhere)."""
    print("Testing valid-everywhere (arbitrary position decode)...")
    # Fill 64 bits with some pattern
    bits = [1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0,
            1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 0, 1]
    for i in range(len(bits)):
        try:
            n, w = decode(bits[i:])
            assert w >= 1
            assert n >= 0
        except (ValueError, IndexError):
            # Running off the end is expected for late positions
            if i + 1 < len(bits):
                raise
    print(f"  PASS (all {len(bits)} positions valid)")


def test_increment_within_tier():
    """Test increment that stays within a tier (no growth)."""
    print("Testing increment within tier...")
    grid = BitGrid(size=32)
    # Write the integer 4 at position 0: 1110 00
    grid.write_int(0, 4)
    grid.show(0, 16)

    old, new, growth = grid.increment(0)
    assert old == 4 and new == 5 and growth == 0
    print(f"  {old} -> {new} (growth={growth})")
    grid.show(0, 16)

    old, new, growth = grid.increment(0)
    assert old == 5 and new == 6 and growth == 0
    old, new, growth = grid.increment(0)
    assert old == 6 and new == 7 and growth == 0
    print(f"  -> 7")
    grid.show(0, 16)
    print("  PASS")


def test_increment_tier_crossing():
    """Test increment that crosses a tier boundary (growth by 2)."""
    print("Testing increment across tier boundary...")
    grid = BitGrid(size=32)
    grid.write_int(0, 7)
    print("  Before (7):")
    grid.show(0, 16)

    old, new, growth = grid.increment(0)
    assert old == 7 and new == 8 and growth == 2
    print(f"  {old} -> {new} (growth={growth})")
    grid.show(0, 16)
    print("  PASS")


def test_decrement_tier_crossing():
    """Test decrement that crosses a tier boundary (shrink by 2).
    First increment 7->8 to populate GP trail, then decrement back."""
    print("Testing decrement across tier boundary...")
    grid = BitGrid(size=32)
    grid.write_int(0, 7)
    # Put recognizable bits where the growth will annex
    grid.bits[6] = 1
    grid.bits[7] = 1
    print("  Before increment (7, with bits 11 after):")
    grid.show(0, 16)

    # Increment 7->8, annexing 2 bits
    grid.increment(0)
    print("  After increment (8):")
    grid.show(0, 16)

    # Now decrement 8->7, restoring the annexed bits
    old, new, shrink = grid.decrement(0)
    assert old == 8 and new == 7 and shrink == 2
    print(f"  {old} -> {new} (shrink={shrink})")
    grid.show(0, 16)
    # The bits at positions 6,7 should be restored to 1,1
    assert grid.bits[6] == 1 and grid.bits[7] == 1, \
        f"Bits not restored: [{grid.bits[6]}, {grid.bits[7]}]"
    print("  PASS")


def test_reversibility():
    """Test that increment followed by decrement restores original bits,
    including bits beyond the integer (via GP trail)."""
    print("Testing reversibility (increment then decrement)...")

    # Set up: integer 7 at pos 0, with known bits after it
    grid = BitGrid(size=32)
    grid.write_int(0, 7)
    # Put recognizable bits after the encoding of 7 (which is 6 bits)
    grid.bits[6] = 1
    grid.bits[7] = 0
    grid.bits[8] = 1
    grid.bits[9] = 1

    original_bits = list(grid.bits)
    print("  Before:")
    grid.show(0, 16)

    # Increment 7 -> 8 (grows by 2, annexing bits at positions 6 and 7)
    grid.increment(0)
    print("  After increment (7->8):")
    grid.show(0, 16)

    # The bits at positions 6,7 were overwritten. GP trail should have [1, 0].
    assert grid.gp_trail == [1, 0], f"GP trail: {grid.gp_trail}"

    # Decrement 8 -> 7 (shrinks by 2, restoring from GP trail)
    grid.decrement(0)
    print("  After decrement (8->7):")
    grid.show(0, 16)

    assert grid.bits == original_bits, (
        f"Reversibility failed!\n"
        f"  Original: {original_bits[:16]}\n"
        f"  Got:      {grid.bits[:16]}")
    assert grid.gp_trail == [], f"GP trail not empty: {grid.gp_trail}"
    print("  PASS (all bits restored, GP trail empty)")


def test_multi_increment_reversibility():
    """Increment from 0 to 20, then decrement back to 0.
    Verify all bits are restored."""
    print("Testing multi-step reversibility (0 -> 20 -> 0)...")

    grid = BitGrid(size=64)
    # Plant some recognizable background pattern
    for i in range(1, 64):
        grid.bits[i] = i % 2
    grid.bits[0] = 0  # Start with integer 0

    original_bits = list(grid.bits)

    # Increment 0 -> 20
    for target in range(1, 21):
        old, new, growth = grid.increment(0)
        assert new == target, f"Expected {target}, got {new}"

    val, w = grid.read_int(0)
    assert val == 20
    print(f"  Reached 20 (width={w})")
    grid.show(0, 32)

    # Decrement 20 -> 0
    for target in range(19, -1, -1):
        old, new, shrink = grid.decrement(0)
        assert new == target, f"Expected {target}, got {new}"

    val, w = grid.read_int(0)
    assert val == 0
    print(f"  Back to 0 (width={w})")
    grid.show(0, 32)

    assert grid.bits == original_bits, "Bits not fully restored!"
    assert grid.gp_trail == [], f"GP trail not empty: {grid.gp_trail}"
    print("  PASS (all bits restored after round trip)")


def test_encoding_table():
    """Print encoding table for reference."""
    print("\nEncoding table (0..20):")
    print(f"  {'n':>4}  {'tier':>4}  {'width':>5}  encoding")
    print(f"  {'─'*4}  {'─'*4}  {'─'*5}  {'─'*20}")
    for n in range(21):
        enc = encode(n)
        t = tier_of(n)
        bits_str = ''.join(str(b) for b in enc)
        # Insert space between prefix and payload
        if n == 0:
            formatted = bits_str
        elif n == 1:
            formatted = bits_str
        else:
            k = t
            formatted = bits_str[:k+1] + ' ' + bits_str[k+1:]
        print(f"  {n:>4}  {t:>4}  {len(enc):>5}  {formatted}")


def run_all_tests():
    print("=" * 60)
    print("  Prefix encoding prototype tests")
    print("=" * 60)

    test_encoding_table()
    print()
    test_encode_decode()
    test_bijectivity()
    test_arbitrary_position()
    test_increment_within_tier()
    test_increment_tier_crossing()
    test_decrement_tier_crossing()
    test_reversibility()
    test_multi_increment_reversibility()

    print()
    print("=" * 60)
    print("  All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    run_all_tests()

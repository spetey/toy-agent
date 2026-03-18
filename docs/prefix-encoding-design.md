# Prefix-Encoded Integers for Unbounded Arithmetic in fb2d

Claude Opus 4.6 + spetey, 2026-02-18

## Motivation

fb2d's 8-bit cells limit all arithmetic to mod 256, making the system a
linear bounded automaton rather than truly Turing-complete. Carry
arithmetic (chaining cells spatially) is awkward and fragile. The idea:
go down to the bit level. Represent all *data* as prefix-free binary
integers that can grow without bound. The IP still reads 8 bits at a
time for opcodes; the data heads operate on variable-length integers.

## Encoding: Unary-Length Prefix (Scheme A)

Every integer is encoded as a bit string, read in the head's current
direction:

```
Value   Encoding        Width
─────   ────────        ─────
0       0               1 bit
1       10              2 bits
2       110 0           4 bits
3       110 1           4 bits
4       1110 00         6 bits
5       1110 01         6 bits
6       1110 10         6 bits
7       1110 11         6 bits
8       11110 000       8 bits
...
2^k-1   1^k 0 (k-1 1s) 2k bits
2^k     1^(k+1) 0 0..0 2(k+1) bits
```

Structure: for n ≥ 1, let k = ⌊log2(n+1)⌋. The encoding is:
- **Prefix**: k ones followed by a zero (k+1 bits)
- **Payload**: the lower k-1 bits of n (i.e., n - 2^(k-1)) in binary

For n = 0: just the single bit `0`.

Total width: 2k + 1 bits for values in tier k (where tier k holds
values 2^(k-1) through 2^k - 1, except tier 0 which holds just 0,
and tier 1 which holds just 1).

### Key properties

1. **Prefix-free**: no encoding is a prefix of another. A reader can
   always determine where an integer ends by scanning the prefix.

2. **Bijective from any position**: every bit position on the grid is
   the start of a valid integer. A `0` bit starts the integer 0.
   A `1` bit starts some integer ≥ 1 (scan forward for the prefix
   terminator `0` to find the tier, then read the payload).

3. **Zero detection is O(1)**: check the first bit. If it's `0`, the
   integer is zero. This makes conditional mirrors cheap.

### Tier boundaries and growth

Within a tier, incrementing is just binary addition on the payload:
- `1110 00` (4) → `1110 01` (5) → `1110 10` (6) → `1110 11` (7)

Crossing a tier boundary requires growth by 2 bits:
- `1110 11` (7, width 6) → `11110 000` (8, width 8)

The extra 2 bits come from: one new `1` added to the prefix, and one
new `0` bit in the (now wider) payload.

Decrement across a boundary shrinks by 2 bits:
- `11110 000` (8, width 8) → `1110 11` (7, width 6)

Two bits are released: one from the prefix, one from the payload.

## Grid Model

- **Grid**: 2D toroidal, cells store 8 bits each (same as current fb2d)
- **Bit addressing**: each bit on the grid is identified by
  `(cell_index, bit_offset)` where bit_offset is 0-7.
  Bit 0 = MSB (leftmost), bit 7 = LSB (rightmost) within a cell.
  *(Convention TBD — MSB-first may be more natural for left-to-right
  reading, but LSB-first may be more natural for arithmetic.)*
- **Bit adjacency**: bits are adjacent within a cell (offset 0↔1↔...↔7),
  and bit 7 of a cell is adjacent to bit 0 of the next cell in the
  head's reading direction (East/South/etc).

## Heads

All heads (H0, H1, CL, EX) point to a *bit position*: `(cell, bit)`.

- **Integer reading**: from a head's position, read the prefix-encoded
  integer starting at that bit, extending in the head's associated
  reading direction (probably East by default, but could be per-head
  or per-op).
- **Bit-level movement**: advance one bit in a direction.
- **Integer-level movement**: skip past the current integer to the
  start of the next one.

## IP (Instruction Pointer)

The IP is unchanged from current fb2d:
- Points to a *cell* (not a bit)
- Reads the 8-bit value at that cell as an opcode
- Mirrors, direction changes, advancement all work as before

This preserves the "valid everywhere" property at the opcode level and
keeps the existing control flow machinery intact.

## Operations

### Core arithmetic (new)
- **INC**: increment the prefix-integer at H0.
  - Within a tier: binary increment of payload bits. No size change.
  - Across a tier boundary: integer grows by 2 bits. The 2 bits
    being annexed are EX-swapped: their old values are written to
    the EX trail, and EX advances 2 bits. This makes the operation
    reversible.
  - Inverse: DEC

- **DEC**: decrement the prefix-integer at H0.
  - Within a tier: binary decrement of payload bits. No size change.
  - Across a tier boundary (n = 2^k → 2^k - 1): integer shrinks by
    2 bits. The 2 released bit positions get their values restored
    from the EX trail (EX retreats 2 bits and swaps back).
  - Inverse: INC
  - Decrementing 0: TBD — could be an error, a no-op, or wrap.
    Probably should be a no-op or trap, since negative numbers aren't
    naturally represented.

### Zero test (for conditional mirrors)
- **IS_ZERO(CL)**: check bit at CL position. If it's `0`, the integer
  at CL is zero. This is a 1-bit check — very cheap.
- Same for EX-conditional mirrors: check bit at EX position.

### Head movement
- **Bit-step N/S/E/W**: move head one bit in a direction (within cell
  or crossing to adjacent cell).
- **Int-skip**: advance head past the current prefix-integer to the
  first bit of the next integer. Width is determined by scanning the
  prefix.

### Existing ops that still work
- Mirrors (`/`, `\`): unchanged
- Conditional mirrors on CL/EX: now test the *bit* at CL/EX (which is
  the first bit of a prefix-integer, so 0 ↔ integer is zero)
- IP movement: unchanged
- Head movement N/S/E/W: now means bit-level movement

### Uncertain / TBD
- **Swap, add, subtract between H0 and H1**: how do you swap two
  variable-length integers? If they're different lengths, this is a
  non-trivial bit-shifting operation. Might need EX to track the
  size difference.
- **Fredkin gate**: conditional swap of two prefix-integers. Same
  length concern.
- **2D direction of integers**: does an integer always extend East?
  Or in the head's movement direction? For 2D mirror-based control
  flow, it might be useful for integers to run vertically too.

## Reversibility

Every operation that changes the number of bits an integer occupies
must EX-swap the displaced/released bits:

**Forward (growth):**
1. Determine the 2 bit positions being annexed
2. For each: swap that grid bit with the current EX bit
3. Advance EX by 2 bits (in EX's movement direction)
4. Write the new (larger) encoding

**Backward (shrink, i.e., undo growth):**
1. Retreat EX by 2 bits
2. For each released position: swap the grid bit with the EX bit
   (restoring the original value that was there before growth)
3. Write the new (smaller) encoding

Within a tier, no bits are displaced, so no EX interaction needed —
it's just flipping payload bits, which is self-evidently reversible
(the inverse is decrement/increment).

## Open Questions

1. **Bit ordering within cells**: MSB-first or LSB-first? MSB-first
   is more natural for reading left-to-right, but LSB-first makes
   carry propagation go in the natural direction (low to high).

2. **Reading direction**: is it fixed (always East) or does each head
   have a reading direction? Per-head direction adds complexity but
   enables vertical integers for 2D patterns.

3. **Decrement of zero**: no-op? trap? wrap to max? Probably no-op
   to keep things simple and avoid needing negative numbers.

4. **Swap of different-width integers**: this is the hardest unsolved
   piece. May need to restrict swaps to same-tier integers, or use
   a EX-tracked shift operation.

5. **Opcode encoding**: the current 43 opcodes fit in 8 bits with
   room to spare. New bit-level ops will need opcode slots. We have
   212 unused values (44-255) so plenty of room.

6. **Display**: need a "bit view" mode that shows individual bits,
   with integer boundaries highlighted. The cell view should probably
   show the decoded integer value at each head position.

## Next Steps

1. Build a standalone prototype of the encoding: `encode(n) → bits`,
   `decode(bits) → n`, `increment(bits) → bits`, with EX tracking.
2. Test tier-boundary crossings thoroughly.
3. Once the encoding logic is solid, integrate into fb2d as new opcodes.

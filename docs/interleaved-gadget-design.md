# Interleaved Dual-Gadget Design (Future)

## Idea

Two error-correcting gadgets interleaved in boustrophedon layout,
each able to read the other's ops when it detects a double-bit-flip
(SECDED detects but can't correct 2-bit errors).

## Architecture

In a boustrophedon layout, gadget A and gadget B alternate rows:

```
Row 0: A code (east)
Row 1: B code (west)
Row 2: A code (east)
Row 3: B code (west)
...
```

Each gadget's IX interoceptor sweeps the other gadget's code rows.
When gadget A encounters a 2-bit error in its own code (syndrome != 0,
p_all_check = 0), it can consult the corresponding cell in gadget B
as a reference copy. If B's copy is clean (syndrome = 0), A can
reconstruct the correct value from B's copy.

## Key Benefits

- **Double-bit recovery**: SECDED alone can only detect 2-bit errors.
  With a clean reference copy from the other gadget, the correct value
  can be recovered (XOR the reference codeword into the damaged cell).
- **Spatial locality**: interleaving means each gadget's code is
  physically adjacent to the other's, minimizing IX travel distance.
- **Natural extension of mutual correction**: the existing
  mutual-correction-demo.py already has two gadgets correcting each
  other. Interleaving them spatially is the next step.

## Open Questions

- How to handle the case where both copies have errors in the same cell?
  (Requires 3+ gadgets for majority vote.)
- EX row placement: each gadget needs its own EX scratch space. Interleave
  EX rows too, or place them at the boundary?
- Direction tracking: with interleaved rows, the east/west direction
  alternates per gadget's own row count, not the absolute row index.
  The outer-loop parity approach (counter mod 2 = direction) should
  still work per-gadget.
- Boundary detection between the two gadgets' code regions.

## Prerequisites

- Contained boustrophedon gadget (single gadget, multi-row) — current work.
- Dynamic boundary detection via V-probe.
- Direction alternation using outer loop counter parity.

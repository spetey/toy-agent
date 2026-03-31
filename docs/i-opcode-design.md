# `I` Opcode Design — Syndrome Inspect (v1.15)

## Summary

`I` is a new opcode that tests the Hamming(16,11) integrity of a remote
cell via IX, without requiring copy-in or the expensive Phase C syndrome
computation (123 ops). It enables a **pre-syndrome filter** that skips
95% of cells (clean ones) in ~8 ops instead of ~70, while also enabling
**2-bit error copy-over correction** — achieving v6's resilience (~250×
MTTF improvement) at better-than-v5 performance.

## Operation

```
I:  [H0] ^= syndrome_5bit_mapped([IX])
```

Computes the 4-bit Hamming(16,11) syndrome AND overall parity of the
cell at IX, maps all 5 bits to DATA_MASK positions, and XORs them into
[H0].

**Bit mapping** (all DATA_MASK positions so payload test works):
- s0 (parity check 0) → bit 3  (data position d0)
- s1 (parity check 1) → bit 5  (data position d1)
- s2 (parity check 2) → bit 6  (data position d2)
- s3 (parity check 3) → bit 7  (data position d3)
- p_all (overall parity) → bit 9  (data position d4)

**Result**: payload([H0]) ≠ 0 iff [IX] has ANY error (including bit-0).

## Properties

- **Self-inverse**: XOR-based. Applying I twice restores [H0].
- **Reversible**: step_back re-applies the same XOR (syndrome of
  unchanged [IX]).
- **NOP guards**: NOP when H0 == IX (self-XOR loses info) or when
  H0 == IP cell (write guard).
- **Replaces opcode M** (54): payload(H0) -= payload(IX), which was
  completely superseded by m (raw XOR) in the copy-down architecture.
  Payload 54 → I. Payload 1017 stays as NOP filler.

## Why 5 bits (not 4)

The original design used only the 4-bit syndrome. But this misses
**bit-0-only errors** (syndrome=0, p_all=1): the overall parity bit
is not covered by the 4-bit syndrome. Including p_all means ALL error
types are caught:

| Syndrome | p_all | Meaning           | I result |
|----------|-------|-------------------|----------|
| 0        | 0     | Clean cell        | 0 (bypass) |
| ≠0       | 1     | 1-bit error       | ≠0 (correct) |
| ≠0       | 0     | 2-bit error       | ≠0 (copy-over) |
| 0        | 1     | Bit-0 error       | ≠0 (correct) ← caught! |

## Architecture: Pre-Syndrome Filter (v7)

Inserted after boundary tests, before copy-in:

```
... ) P          # last boundary merge
I                # [H0=CWL] ^= syndrome+pall([IX])
T                # swap [CL=ROT] ↔ [H0=CWL]
?                # / if CL==0 → bypass (clean cell!)
T                # undo swap (syndrome≠0 only)
I                # undo XOR (syndrome≠0 only)
m                # copy-in (existing v5 code)
...              # Phase A, B, probe, correction (unchanged)
```

### Three paths

1. **Clean bypass** (syndrome=0, p_all=0, ~95%): I T ? → bypass row
   → merge gate. ~84 steps total. No copy-in, no Phase A/B. Zero EX
   consumption.

2. **1-bit correction** (syndrome≠0 OR p_all≠0, ~5%): I T ? T I →
   full v5 correction path. ~399 steps. +5 ops over v5.

3. **2-bit copy-over** (syndrome≠0, p_all=0, rare): goes through
   correction to probe → probe fires (p_all=0) → copy-over row →
   Phase A/B undo + IX round-trip to own cell + j writeback + waste
   deposit. The pre-filter guarantees that ALL cells reaching the
   probe with p_all=0 have syndrome≠0, so copy-over acts
   unconditionally.

### Col 2 routing (3 flows merge via EX discrimination)

```
Copy-over row:  / at col 2 (unconditional W→S exit)
                Copy-over ends with ]+Z] so EX is CLEAN at exit.
Clean bypass:   $ at col 2 (/ if [EX]≠0)
                Clean bypass arrives W with dirty EX → $ fires W→S. ✓
                Copy-over arrives S with clean EX → $ NOP → S.     ✓
Return row:     P at col 2 (re-dirties EX for copy-over path)
Handler row:    NOP at col 2
Code row:       ( at col 2 (merge gate, fires S→E on dirty EX)
```

## Layout (R+8 rows per gadget)

```
Row 0:        BOUNDARY (0xFFFF)
Row 1:        COPY-OVER ROW (2-bit error correction)
Row 2:        CLEAN BYPASS ROW (pre-syndrome fast path)
Row 3:        RETURN ROW (rewind loop + pre-syndrome exit via $)
Row 4:        HANDLER ROW
Rows 5..R+4:  CODE ROWS (boustrophedon)
Row R+5:      BOUNDARY (0xFFFF)
Row R+6:      STOMACH
Row R+7:      WASTE
```

One extra row vs v5 (R+7). The clean bypass row is needed to avoid
routing conflicts with the return row's rewind handler ops.

## Performance

| Metric              | v5    | v6 (old)  | v7 (with I) |
|---------------------|-------|-----------|-------------|
| Clean bypass steps  | ~162  | ~269      | **~84**     |
| Rows per gadget     | R+7   | R+14      | R+8         |
| 2-bit copy-over     | No    | Yes       | **Yes**     |
| Total ops (gadget)  | 374   | 374+269   | 379         |
| Min width           | 100   | 100       | 101         |

## NOP Filler: Unchanged

By replacing M (opcode 54, payload 54) instead of adding opcode 63,
payload 1017 stays unassigned in the opcode table. NOP filler cells
with payload 1017 continue to decode to NOP (opcode 0) with full
d_min=4 protection: all 1-bit AND 2-bit data errors still decode to
NOP (0/55 bad).

## Files Modified

- `fb2d.py`: Replace M with I in OPCODES, OPCODE_PAYLOADS, step(),
  step_back(). Add SYNDROME_XOR_MASK precomputed table.
- `fb2d_gui.html`: Add I to OPCODE_CHARS, OPCODE_NAMES, OPCODE_PAYLOADS,
  palette. Remove M.
- `test_reversibility.py`: Replace M with I in OPCODE_SPECS.
- `programs/immunity-gadgets-v7-syndrome-inspect.py`: New v7 gadget.
- `programs/dual-gadget-demo.py`: Remove M references (if any).

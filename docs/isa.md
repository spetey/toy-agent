# fb2d Instruction Set Architecture (v1.8)

48 opcodes + NOP. Every 16-bit cell value is valid: the IP reads
`payload(cell)` (the 11 data bits of the Hamming(16,11) codeword) as
the opcode. Payloads 1-48 are opcodes; everything else is NOP.

## Cell Format

Each cell is a 16-bit Hamming(16,11) SECDED codeword:

```
Bit: 15 14 13 12 11 10  9  8  7  6  5  4  3  2  1  0
     d10 d9 d8 d7 d6 d5 d4 p3 d3 d2 d1 p2 d0 p1 p0 p_all
```

- **Payload**: 11 data bits → opcode or value (0-2047)
- **Parity**: 5 check bits at positions 0, 1, 2, 4, 8
- Arithmetic ops (+, -, ., ,, :, ;, P, Q) maintain the Hamming invariant
- Bit-level ops (r, l, R, L, Y, x, z, f) operate on all 16 raw bits

## Heads

| Head | Purpose |
|------|---------|
| H0   | Primary data head |
| H1   | Secondary data head |
| CL   | Condition latch — tested by conditional mirrors; also provides rotation amounts |
| GP   | Garbage pointer — breadcrumb trail for reversibility |

All heads point to cells on the same toroidal grid as the IP.

## Notation

- `[H0]` = full 16-bit cell value at head H0's position
- `payload(X)` = the 11 data bits extracted from cell X
- Movement is on a torus: edges wrap

## Mirrors (6 opcodes)

| Op | Payload | Description |
|----|---------|-------------|
| `/` | 1 | Unconditional / reflect |
| `\` | 2 | Unconditional \ reflect |
| `%` | 3 | / reflect if payload([CL]) != 0 |
| `?` | 4 | / reflect if payload([CL]) == 0 |
| `&` | 5 | \ reflect if payload([CL]) != 0 |
| `!` | 6 | \ reflect if payload([CL]) == 0 |

Mirror geometry:
- `/` maps E↔N, S↔W
- `\` maps E↔S, N↔W

Conditional mirrors test whether any data bit in [CL] is set
(`[CL] & DATA_MASK`). Parity bits are ignored.

## Head Movement (16 opcodes)

| Op | Payload | Description |
|----|---------|-------------|
| `N` | 7  | Move H0 north |
| `S` | 8  | Move H0 south |
| `E` | 9  | Move H0 east |
| `W` | 10 | Move H0 west |
| `n` | 11 | Move H1 north |
| `s` | 12 | Move H1 south |
| `e` | 13 | Move H1 east |
| `w` | 14 | Move H1 west |
| `>` | 23 | Move CL east |
| `<` | 24 | Move CL west |
| `^` | 25 | Move CL north |
| `v` | 26 | Move CL south |
| `]` | 29 | Move GP east |
| `[` | 30 | Move GP west |
| `}` | 31 | Move GP south |
| `{` | 32 | Move GP north |

All movement wraps on the torus.

## Byte-Level Data (8 opcodes)

These operate on Hamming-encoded cells, maintaining the parity invariant.

| Op | Payload | Description | Inverse |
|----|---------|-------------|---------|
| `+` | 15 | payload([H0])++ (mod 2048, with parity fixup) | `-` |
| `-` | 16 | payload([H0])-- (mod 2048, with parity fixup) | `+` |
| `.` | 17 | payload([H0]) += payload([H1]) (mod 2048, with parity fixup) | `,` |
| `,` | 18 | payload([H0]) -= payload([H1]) (mod 2048, with parity fixup) | `.` |
| `X` | 19 | swap([H0], [H1]) — full 16-bit swap | self |
| `F` | 20 | if payload([CL]) != 0: swap([H0], [H1]) — Fredkin gate | self |
| `G` | 21 | swap(H1 register, [H0]) — indirect H1 | self |
| `T` | 22 | swap([CL], [H0]) — bridge (full 16-bit) | self |

Arithmetic ops (`+`, `-`, `.`, `,`) XOR a precomputed delta into the cell
so that both the payload and parity bits update together. The operation is
bijective on all 65536 cell values, not just valid codewords.

`F` (Fredkin gate) tests `payload([CL]) != 0` — any nonzero data bit.

## Bit-Level Data (10 opcodes)

These operate on the full 16-bit raw cell value (including parity bits).
They do **not** maintain the Hamming invariant — by design, the
correction gadget uses them to manipulate raw bits.

| Op | Payload | Description | Inverse |
|----|---------|-------------|---------|
| `x` | 39 | [H0] ^= [H1] — XOR | self |
| `r` | 40 | [H0] rotate right 1 bit | `l` |
| `l` | 41 | [H0] rotate left 1 bit | `r` |
| `f` | 42 | if [CL] & 1: swap([H0], [H1]) — bit-0 Fredkin | self |
| `z` | 43 | swap(bit 0 of [H0], bit 0 of [GP]) | self |
| `R` | 44 | [H0] rotate right by (payload([CL]) & 15) bits | `L` |
| `L` | 45 | [H0] rotate left by (payload([CL]) & 15) bits | `R` |
| `Y` | 46 | [H0] ^= ror([H1], payload([CL]) & 15) — fused rotate-XOR | self |
| `:` | 47 | payload([CL])++ (mod 2048, with parity fixup) | `;` |
| `;` | 48 | payload([CL])-- (mod 2048, with parity fixup) | `:` |

Key distinctions:
- **`f`** (lowercase) gates on raw **bit 0** of [CL] — used by the
  barrel-shifter correction gadget
- **`F`** (uppercase) gates on **payload([CL]) != 0** — used for general
  conditional logic
- **`R`/`L`/`Y`** read the rotation amount from `payload([CL]) & 15`
  (0-15 positions, suitable for 16-bit cells)
- **`z`** swaps raw bit 0 of [H0] with raw bit 0 of [GP] — used to
  extract single syndrome/parity bits

## GP (Garbage Pointer) Ops (8 opcodes)

| Op | Payload | Description | Inverse |
|----|---------|-------------|---------|
| `P` | 27 | payload([GP])++ — leave breadcrumb | `Q` |
| `Q` | 28 | payload([GP])-- — erase breadcrumb | `P` |
| `K` | 33 | swap(CL register, GP register) — exchange head positions | self |
| `(` | 34 | \ reflect if payload([GP]) != 0 | — |
| `)` | 35 | \ reflect if payload([GP]) == 0 | — |
| `#` | 36 | / reflect if payload([GP]) == 0 | — |
| `$` | 37 | / reflect if payload([GP]) != 0 | — |
| `Z` | 38 | swap([H0], [GP]) — full 16-bit GP swap | self |

GP mirrors test `payload([GP])` (data bits only, ignoring parity).

The standard loop pattern is `( P ... %`:
1. `(` tests [GP]: if payload != 0, reflect (skip loop body). On first
   entry [GP] is 0, so IP continues into the body.
2. `P` increments [GP], leaving a breadcrumb (makes [GP] nonzero).
3. Loop body executes.
4. `%` tests [CL]: if payload([CL]) != 0, reflect back toward `(`.

On re-entry, `(` sees nonzero [GP] and reflects (exits). The reverse
path uses `Q` to erase the breadcrumb, restoring [GP] to 0.

## Reversibility Pairs

Every opcode has a unique inverse (itself or another opcode):

| Forward | Inverse | Notes |
|---------|---------|-------|
| `+` | `-` | Increment / decrement |
| `.` | `,` | Add / subtract |
| `r` | `l` | Rotate right / left by 1 |
| `R` | `L` | Rotate right / left by CL |
| `:` | `;` | CL increment / decrement |
| `P` | `Q` | GP breadcrumb / erase |
| `N`/`S` | `S`/`N` | Head movement pairs |
| `E`/`W` | `W`/`E` | (same for all four heads) |

Self-inverse ops: `X`, `F`, `G`, `T`, `K`, `Z`, `x`, `f`, `z`, `Y`

Mirrors (`/`, `\`, `%`, `?`, `&`, `!`, `(`, `)`, `#`, `$`) are
self-inverse in the sense that the IP direction mapping is its own
inverse — running the IP backward through a mirror reflects it the
same way.

## NOP

Any payload not in {1-48} is a NOP: the IP advances without side
effects. On the 16-bit cell, this means payloads 0, 49-2047 are all
NOPs. The canonical "empty cell" is raw value 0 (payload 0).

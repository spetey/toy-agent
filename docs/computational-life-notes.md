# Notes on "Computational Life" (Aguera y Arcas et al., 2024)

**Paper**: arXiv:2406.19108
**Authors**: Blaise Aguera y Arcas, Jyrki Alakuijala, James Evans, Ben Laurie,
Alexander Mordvintsev, Eyvind Niklasson, Ettore Randazzo, Luca Versari
**Code**: https://github.com/paradigms-of-intelligence/cubff

## BFF Language (Brainfuck Family)

10 instructions out of 256 byte values (rest are NOP):

| Symbol | Semantics |
|--------|-----------|
| `<` `>` | head0 move left/right |
| `{` `}` | head1 move left/right |
| `-` `+` | tape[head0] decrement/increment |
| `.` | tape[head1] = tape[head0] (copy h0→h1) |
| `,` | tape[head0] = tape[head1] (copy h1→h0) |
| `[` | if tape[head0]==0: jump to matching `]` |
| `]` | if tape[head0]!=0: jump to matching `[` |

**Key differences from standard BF:**
- Unified code/data tape (von Neumann architecture)
- Second head (head1) with `{`/`}`
- Copy ops replace I/O (`.`/`,` = inter-head copy, NOT console)
- Self-contained: no external I/O
- ~1/25.6 random bytes are valid instructions

## Primordial Soup Setup

- 2^17 = 131,072 programs, each 64 bytes, randomly initialized
- Fixed population (no birth/death)
- Each epoch: random pairs concatenated (128 bytes), executed (≤8192 steps),
  split back. Self-modification rewrites both programs.
- Default mutation rate: 0.024% per byte per epoch

## Key Results

1. **~40% of runs** produce self-replicators within 16k epochs
2. **Self-modification is the driver, NOT mutation** — 0% mutation still
   yields ~40% transition rate; deterministic interactions yield ~50%
3. **Zero-poisoning phase**: first replicator can't overwrite zeros →
   soup fills with zeros → second more robust replicator takes over
4. **Palindromic replicators**: copy themselves in reverse, but palindrome
   structure means the reverse copy is still functional
5. **SUBLEQ counterexample**: despite being TC, smallest replicator is
   60 bytes — too long for spontaneous emergence. Minimum replicator
   length is a critical factor for abiogenesis.

## Other Languages Tested

- **Forth**: nearly all runs transition within 1k epochs (trivial 1-byte replicator)
- **Z80**: multiple waves of increasingly capable replicators (PUSH → LDIR/LDDR)
- **Intel 8080**: two-byte non-looping replicators
- **SUBLEQ**: FAILED to produce spontaneous replicators (min length too large)

## BFF is NOT Reversible

- Copy ops (`.`/`,`) destructively overwrite the destination
- Interactions `A+B → A'+B'` are irreversible
- Paper does not discuss reversibility at all

## Relevance to fb2d

| BFF | fb2d |
|-----|------|
| Destructive copy (`.`/`,`) | Reversible: swap (`X`), XOR (`x`), Fredkin (`F`) |
| 1D tape pairs | 2D toroidal grid, shared |
| Not reversible | Every state has unique predecessor |
| Random program pairing | Multiple IPs on shared grid |
| "Valid everywhere" (NOP default) | Same: every byte is opcode or NOP |
| Abiogenesis (structure from noise) | Self-correction (structure despite noise) |

## Key Insight for fb2d Evolution

The SUBLEQ result suggests **minimum replicator/gadget length** is the
critical bottleneck for spontaneous emergence. fb2d's Hamming gadget at
~253 ops is much longer than BFF's ~8-byte replicators. This means:

- Spontaneous abiogenesis of correction gadgets in fb2d is unlikely
- But *seeded* evolution (start with a working gadget, let it evolve
  under noise pressure) is plausible — the paper shows seeded replicators
  take over 22% of the time in just 128 epochs
- 1D might help: shorter instruction encoding = shorter gadgets =
  closer to the spontaneous emergence threshold

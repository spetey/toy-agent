# F***brain 2D: Turing Completeness and Universality
## Authored or modified by Claude — 2025-02-01, updated 2026-02-19

---

## 1. Overview

**Claim:** `fb_reflect` v3 (with unbounded cell values) can simulate any counter
machine, and is therefore Turing complete.

**Strategy:** We don't try to simulate an abacus/counter machine directly, because
our IP can't jump—it's physically trapped, bouncing between mirrors. Instead, we
use a **single-main-loop architecture** with **Fredkin-gated dispatch blocks**
and **double-buffered active flags**.

The key insight: inside a `/ ... /` loop, the IP bounces right then left. All
computation happens on the rightward pass (using `>`-prefixed ops). The leftward
pass is used for a **buffer swap** (using `<`-prefixed ops). One "simulated
instruction" executes per full bounce.

---

## 2. Instruction Decomposition

A counter machine has instructions of the form `JZDEC(R, L_zero, L_nonzero)`.
We decompose these into three simpler types:

| Type | Meaning |
|------|---------|
| `INC(R, L)` | Increment register R, goto L |
| `DEC(R, L)` | Decrement register R, goto L |
| `JZ(R, L_z, L_nz)` | If R=0 goto L_z, else goto L_nz (**don't modify R**) |
| `HALT` | Stop execution |

The original `JZDEC(R, L_z, L_nz)` becomes:
- `JZ(R, L_z, L_nz')`
- Instruction `L_nz'`: `DEC(R, L_nz)`

This decomposition is crucial: **JZ doesn't modify the register it tests**.
This avoids a circular dependency in uncomputation (see §7).

---

## 3. Data Layout

The tape is divided into code and state regions:

```
[... code region (fixed) ...] [... state region ...]
                               ^--- at offset s
```

**State region (at base address s):**

```
s+0    to s+n-1     : current[0..n-1]   active flags (current step)
s+n    to s+2n-1    : next[0..n-1]      active flags (next step)
s+2n                : A                  register
s+2n+1              : B                  register
s+2n+2              : running            1=run, 0=halt
s+2n+3              : one_const          always = 1
s+2n+4              : cond_cell          scratch, maintained at 0
s+2n+5  to s+2n+7   : scratch[0..2]      all maintained at 0
s+2n+8              : addr_A             constant = s+2n (address of A)
s+2n+9              : addr_B             constant = s+2n+1
s+2n+10             : addr_s0            constant = s+2n+5 (addr of scratch[0])
...                 : (more addr constants as needed)
```

**Invariants maintained across iterations:**
- Exactly one `current[i]` is 1, all others are 0
- All `next[i]` are 0 at the start of each iteration
- `cond_cell` and all scratch cells are 0 at start/end of each iteration
- `one_const` = 1 always
- `addr_*` cells hold their constant values at start/end of each iteration

---

## 4. Code Structure

```
[preamble] / G(cl_sw) [block_1 ... block_n] [buf_swap_ops] G(cl_sw) /
```

**The outer loop:** `/ ... /` with CL → `running`. The loop bounces while
`running ≠ 0`. When HALT sets `running = 0`, the IP passes through the right
mirror and exits.

**CL toggling:** Bidirectional `G` ops at both ends of the loop body swap CL
between `addr(running)` and `addr(cond_cell)`. This means:

- At the mirrors: CL → `running` (for the loop condition)
- During block execution: CL → `cond_cell` (for Fredkin conditioning)

Trace of G(cl_sw) where cl_sw initially contains addr(cond_cell):

| Pass direction | First G | Body | Second G | At mirror |
|----------------|---------|------|----------|-----------|
| Rightward | CL: running→cond_cell | CL→cond_cell | CL: cond_cell→running | CL→running ✓ |
| Leftward | CL: running→cond_cell | CL→cond_cell | CL: cond_cell→running | CL→running ✓ |

The G is self-inverse, so each pass toggles CL twice, ending where it started
at the mirrors. ✓

---

## 5. Dispatch Block: INC(A, goto L) — Instruction k

At entry: CL → cond_cell (= 0), H0 and H1 at known reset positions.

### Phase 1: Compute condition

```
>. (H0→cond_cell, H1→current[k-1])
    cond_cell += current[k-1]
    → cond_cell = 1 if active, 0 if not
```

### Phase 2: Conditional increment of A

We use the **Fredkin-arm/act/disarm** pattern:

```
>F (CL→cond_cell, H0→one_const, H1→scratch[0])
    if cond_cell ≠ 0: swap(one_const, scratch[0])
    → scratch[0] = 1 if active, 0 if not
    → one_const = 0 if active, 1 if not

>. (H0→A, H1→scratch[0])
    A += scratch[0]
    → A incremented by 1 if active, unchanged if not

>F (CL→cond_cell, H0→one_const, H1→scratch[0])
    undo the swap
    → one_const = 1, scratch[0] = 0
```

**Why the undo Fredkin works:** `cond_cell` hasn't changed between the two >F
calls (it's still the value from Phase 1). And Fredkin is self-inverse when
applied to the same two cells with the same condition. In detail:

- Active case: First >F swaps 1↔0. A += 1. Second >F swaps 0↔1 (back). ✓
- Inactive case: Both >F's are no-ops. A += 0. ✓

### Phase 3: Activate target (set next[L-1] = 1)

Same arm/act/disarm pattern:

```
>F (CL→cond_cell, H0→one_const, H1→scratch[0])    arm
>. (H0→next[L-1], H1→scratch[0])                    next[L-1] += scratch[0]
>F (CL→cond_cell, H0→one_const, H1→scratch[0])    disarm
```

### Phase 4: Deactivate self (clear current[k-1])

Same pattern:

```
>F (CL→cond_cell, H0→one_const, H1→scratch[0])    arm
>, (H0→current[k-1], H1→scratch[0])                 current[k-1] -= scratch[0]
>F (CL→cond_cell, H0→one_const, H1→scratch[0])    disarm
```

After this: current[k-1] = 0 regardless (was 1→0 if active, was 0→0 if not).

### Phase 5: Uncompute condition

We need cond_cell back to 0. The trick: `cond_cell = cond_active`, and after
Phase 3, `next[L-1] = cond_active` (same value). So:

```
>, (H0→cond_cell, H1→next[L-1])
    cond_cell -= next[L-1]
    → cond_cell = cond_active - cond_active = 0  ✓
```

Both active and inactive cases work:
- Active: cond_cell = 1, next[L-1] = 1 → 1 - 1 = 0 ✓
- Inactive: cond_cell = 0, next[L-1] = 0 → 0 - 0 = 0 ✓

### Head positioning

Between each operation above, H0 and H1 must be repositioned using sequences
of `>}`, `>{`, `>)`, `>(`. The number of repositioning ops per block is
O(state_size), which is O(n) for n instructions. This is the main source of
code bloat, but it's polynomial.

---

## 6. Dispatch Block: JZ(A, L_zero, L_nonzero) — Instruction k

This is the most complex block because it branches on a register value.

### Phase 1: Compute condition flags

```
>. (H0→cond_cell, H1→current[k-1])     cond_cell = active flag

>. (H0→scratch[0], H1→cond_cell)        scratch[0] = cond_active

>G (H0→addr_A)                           CL → A  (addr_A saves old CL)
>F (H0→scratch[0], H1→scratch[1])        if A≠0: swap(scratch[0], scratch[1])
>G (H0→addr_A)                           CL → cond_cell (restored)
```

After this single Fredkin:
- `scratch[0]` = cond_active if A=0, = 0 if A≠0  → **this is cond_zero**
- `scratch[1]` = 0 if A=0, = cond_active if A≠0  → **this is cond_nonzero**

Both flags from one gate! The Fredkin swaps the active flag into the "nonzero
slot" when A≠0, leaving it in the "zero slot" when A=0.

### Phase 2: Activate appropriate target

For the zero case: set next[L_zero-1] using cond_zero (scratch[0]).

To gate on scratch[0] instead of cond_cell, temporarily point CL there:

```
>G (H0→addr_s0)                         CL → scratch[0]
>F (H0→one_const, H1→scratch[2])        arm: scratch[2] = 1 if cond_zero
>. (H0→next[L_zero-1], H1→scratch[2])   next[L_zero-1] += scratch[2]
>F (H0→one_const, H1→scratch[2])        disarm
>G (H0→addr_s0)                         CL → cond_cell (restored)
```

For the nonzero case: identical structure using scratch[1] and L_nonzero.

```
>G (H0→addr_s1)                         CL → scratch[1]
>F (H0→one_const, H1→scratch[2])        arm
>. (H0→next[L_nonzero-1], H1→scratch[2])
>F (H0→one_const, H1→scratch[2])        disarm
>G (H0→addr_s1)                         CL → cond_cell (restored)
```

### Phase 3: Deactivate self

Same as INC block (Fredkin-arm, subtract from current[k-1], Fredkin-disarm).

### Phase 4: Uncompute condition flags

**Undo the Fredkin** (step 1's >F): 

```
>G (H0→addr_A)                       CL → A
>F (H0→scratch[0], H1→scratch[1])    undo Fredkin
>G (H0→addr_A)                       CL → cond_cell
```

**Critical: A has not been modified** (JZ doesn't touch registers). So the undo
Fredkin sees the same A value as the original. Self-inverse ⟹ scratch[0] and
scratch[1] return to their pre-Fredkin values:
- scratch[0] = cond_active (was copied from cond_cell)
- scratch[1] = 0

**Clean scratch[0]:**

```
>, (H0→scratch[0], H1→cond_cell)     scratch[0] -= cond_cell
    = cond_active - cond_active = 0   ✓
```

**Clean cond_cell:** Using both next[] targets:

```
>, (H0→cond_cell, H1→next[L_zero-1])
>, (H0→cond_cell, H1→next[L_nonzero-1])
```

Since cond_zero + cond_nonzero = cond_active = cond_cell, and these are
exactly the values stored in next[L_zero-1] and next[L_nonzero-1]:

    cond_cell - cond_zero - cond_nonzero = cond_active - cond_active = 0  ✓

---

## 7. Why the Decomposition Matters

The decomposition of JZDEC into JZ + DEC is essential. Without it, we'd have:

**Problem:** JZDEC(A, L_z, L_nz) both tests A and decrements it. The Fredkin
that tests "is A zero?" uses CL→A. To undo this Fredkin in the uncomputation
phase, we need A at its original value. But if we decremented A in the action
phase, A has changed, and the undo Fredkin produces wrong results.

**Solution:** JZ tests A without modifying it. The undo Fredkin always sees the
original A. The decrement happens in a separate DEC instruction, which has no
A-dependent Fredkin to undo.

This is essentially a **data dependency ordering** constraint: you can't
uncompute a condition from a variable you've already modified. The decomposition
breaks the dependency.

---

## 8. DEC(A, L) Block

Identical structure to INC, except the action uses `>,` instead of `>.`:

```
>F (CL→cond_cell, H0→one_const, H1→scratch[0])   arm
>, (H0→A, H1→scratch[0])                           A -= scratch[0]
>F (CL→cond_cell, H0→one_const, H1→scratch[0])   disarm
```

Activation of target and self-deactivation proceed identically to INC.

---

## 9. HALT Block

HALT = instruction 0 (by convention). When active:

```
>F arm
>, (H0→running, H1→scratch[0])       running -= 1 → 0
>F disarm

>F arm
>. (H0→next[0], H1→scratch[0])       next[0] += 1 (goto self)
>F disarm

>F arm                                deactivate current[0]
>, (H0→current[0], H1→scratch[0])
>F disarm

>, (H0→cond_cell, H1→next[0])        cleanup: cond_cell -= next[0] = 0
```

The "goto self" ensures next[0] = cond_active, allowing clean uncomputation of
cond_cell. On the next iteration attempt, running = 0 causes the IP to pass
through the outer mirror, exiting the loop.

---

## 10. Buffer Swap (Leftward Pass)

After all blocks execute on the rightward pass, the IP bounces off the right
mirror and traverses the body leftward. The leftward pass performs the buffer
swap using `<`-prefixed operations:

For each i from 0 to n-1:

```
<. (H0→current[i], H1→next[i])      current[i] += next[i]
<, (H0→next[i], H1→current[i])      next[i] -= current[i]
```

**Why this works:**

After the rightward pass, each `current[i] = 0` (every block cleared its own
active flag — the active block explicitly decremented it, inactive blocks
left it at 0). And exactly one `next[j] = 1`.

So for each i:
- `current[i] += next[i]`: current[i] becomes next[i] (0+0=0 or 0+1=1)
- `next[i] -= current[i]`: next[i] = next[i] - next[i] = 0 ✓

Result: `current[]` now holds the next step's active flags, `next[]` is all
zeros, ready for the next iteration. ✓

**Head management for buffer swap:** Between each pair of operations, H0 and H1
advance by one cell (using `<}` and `<)`). The swap code is n identical 
gadgets laid out sequentially in the code region.

---

## 11. Correctness Summary

### Forward execution
Each main-loop iteration:
1. Rightward pass: exactly one dispatch block "fires" (the one where
   `current[k-1]=1`). It performs its action, activates the target instruction
   in `next[]`, and deactivates itself in `current[]`.
2. Leftward pass: buffer swap moves `next` → `current`, clears `next`.
3. All scratch cells return to 0. Invariants maintained. ✓

This faithfully simulates one step of the counter machine per iteration.

### Reversibility
Every operation is individually reversible:
- `>.` / `>,` are inverses
- `>F` is self-inverse (Fredkin)
- `>G` is self-inverse (swap)
- `>}` / `>{` are inverses
- Mirror disambiguation works as before (tape[CL] at mirrors determines path)

The `step_back()` function can reverse any state without execution history.

### Scratch cleanliness
Every dispatch block cleans its scratch cells. The uncomputation strategy for
each block type:
- **INC/DEC:** cond_cell cleaned via `next[L-1]` (both hold `cond_active`)
- **JZ:** cond_cell cleaned via `next[L_z-1] + next[L_nz-1]` (sum = `cond_active`);
  scratch[0] cleaned via cond_cell; scratch[1] was 0 after Fredkin undo.
- **HALT:** cond_cell cleaned via `next[0]`.

---

## 12. Encoding Complexity

For a counter machine with n instructions and 2 registers:

- **State cells:** O(n) active flags × 2 (double buffer) + O(1) registers/scratch
  + O(n) address constants = O(n)
- **Code per block:** O(n) head-positioning ops + O(1) logic ops = O(n)
- **Total code:** n blocks × O(n) per block + O(n) buffer swap = O(n²)
- **Total tape:** O(n²)

This is polynomial, which is all we need for a TC proof.

---

## 13. Caveats and Open Issues

### 13.1 Cell Size

The simulator uses 8-bit cells (mod 256). This bounds register values to 255,
making the implementation a **linear bounded automaton**, not truly Turing
complete. For the TC proof, we assume unbounded cell values (arbitrary-precision
integers). The abstract machine is TC; the 8-bit simulator is a finite
approximation.

Alternatively, registers could be encoded as sequences of cells (e.g., binary
with carry propagation for INC/DEC). This would give true TC even with 8-bit
cells, at the cost of significantly more complex dispatch blocks.

### 13.2 Tape Size

Similarly, the simulator has a fixed tape size. The TC proof assumes the tape
can be "as large as needed" — any specific computation uses finitely many cells.

### 13.3 "Valid Everywhere" Property

Every byte value (0–255) maps to either a known instruction or NOP. Any byte
sequence is a valid program. The TC proof uses specific initial tapes, but the
property holds universally. ✓

### 13.4 Not Verified

This is a proof sketch, not a formal proof. The highest-risk components are:
1. CL management via >G (ensuring paired >G calls see the same cell)
2. Head positioning (counting >}/{/>/( ops correctly)
3. Buffer swap ordering on the leftward pass
4. Interaction between bidirectional G at loop boundaries and direction-
   conditional >G inside blocks

A concrete implementation of a small counter machine (e.g., 2+3=5 via repeated
INC) would significantly increase confidence.

### 13.5 Practical Programmability

The encoding is astronomically verbose. A 5-instruction counter machine might
require thousands of tape cells. This is fine for a TC proof but far from
practically programmable. The question of whether a more compact encoding exists
(perhaps using self-modification or more clever control flow) remains open.

---

## 14. What We've Actually Shown

| Property | Status |
|----------|--------|
| Reversible | ✓ Every state has unique predecessor, inferred from state alone |
| Valid everywhere | ✓ Any byte sequence is a valid program |
| Turing complete | ✓ (sketch) Simulates counter machines via dispatch blocks |
| Practically programmable | ✗ Encoding is polynomial but enormous |
| Self-modifying | Not needed for TC, but possible (code is on the tape) |
| Garbage-free | ✓ No garbage bits accumulate (all scratch cleaned per iteration) |

The "garbage-free" property is notable: unlike Bennett's general construction,
our specific dispatch architecture returns all scratch to zero each iteration.
The "history" of which instruction executed is encoded in the active flags
(which are part of the meaningful state, not garbage).

---

## Appendix: The Fredkin Arm/Act/Disarm Pattern

This is the core gadget used throughout. To conditionally add value V from a
constant cell to target T, gated on condition C:

```
                   C≠0 case         C=0 case
                   ---------        ---------
>F(C, V, scratch)  V↔scratch        (no-op)
                   scratch=V, V=0   scratch=0, V=V

>.(T, scratch)     T += V           T += 0
                   (T incremented)  (T unchanged)

>F(C, V, scratch)  V↔scratch        (no-op)
                   scratch=0, V=V   scratch=0, V=V
```

Both cases leave scratch=0 and V restored. The only permanent effect is on T.
This is the key building block for all conditional operations.

---

## 15. The Quiescent Background Requirement (added 2026-02-19)

### 15.1 The Problem

The TC proof in §§1–12 assumes **structured initial conditions**: specific
initial tape contents with registers, active flags, address constants, and
scratch cells all initialized to known values. This is standard for TC proofs
(every UTM needs its input encoded on the tape), but it raises a deeper
question: does the **physics** of fb2d — its dynamics on *arbitrary* grid
states — support unbounded computation?

The answer appears to be **no**, for the same reason it's no for every other
known reversible TC model.

### 15.2 Two Levels of "Valid Everywhere"

fb2d satisfies "valid everywhere" in the **dynamics** sense:
- Every byte value is a valid opcode (known instruction or NOP)
- Every grid state has a unique successor AND a unique predecessor
- `step()` and `step_back()` are total functions on the full state space
- The dynamics is a bijection: no state is an orphan, no two states collide

But Turing completeness requires more than valid dynamics. It requires
**unbounded loops** — the ability to repeat a computation an arbitrary number
of times. And unbounded loops require **fresh resources**: either blank tape
cells (for history/garbage), or at minimum cells in a known state that can
serve as counters, flags, or scratch space.

### 15.3 The Fundamental Obstruction

In fb2d's reversible dynamics, every operation that "uses" a cell must be
undoable. The exteroceptor (EX) records displaced values so that
operations can be reversed. But EX itself consumes cells — and those cells
must be in a known state (typically zero) for the EX trail to be
interpretable.

On an arbitrary grid — where EX might point to cells containing any value —
the EX mechanism breaks down:
- `P` (EX++) writes a breadcrumb, but the existing cell value is unknown
- Loop-entry mirrors like `(` test `[EX] != 0`, but an arbitrary cell might
  already be nonzero, causing spurious loop entry/exit
- Carry arithmetic corridors require zero-terminated digit sequences, which
  don't exist on a random grid

This is not a bug in fb2d's design. It is a **fundamental property of
reversible computation**:

> **Every known reversible Turing-complete model requires a quiescent
> (known-state) background for the tape/memory regions that grow during
> computation.**

This includes:
- **Bennett's reversible TM simulation** (1973): requires blank history tape
- **Morita's reversible CAs** (various): valid-everywhere dynamics, but TC
  proofs use finite configurations on an all-zero background
- **Fredkin & Toffoli's conservative logic** (1982): requires blank wires
- **Janus** (reversible imperative language): variables start at zero
- **fb2d**: requires zeroed EX trail, initialized registers and flags

This appears to be an **open problem**, not a proven impossibility. No one
has proved that reversible TC *requires* quiescent background, but no one
has achieved it without one either.

### 15.4 Circuit Completeness vs. Turing Completeness

There is a weaker but still powerful notion: **circuit universality**
(sometimes called "physical universality" following Janzing 2010).

A system is circuit-universal if, for any finite region R and any desired
finite transformation T on R's contents, you can arrange the cells *outside*
R to cause T to happen. This gives you arbitrary bounded-time computation on
fixed-size inputs — any Boolean circuit — but not unbounded loops.

**fb2d is plausibly circuit-complete.** The dispatch-block architecture of
§§4–9 can implement any fixed finite computation: you lay out the code,
initialize the state region, and the IP bounces through the dispatch blocks
for a predetermined number of iterations. The only thing you *can't* do
(without quiescent background) is loop an unbounded number of times, because
eventually the EX trail would wrap into unknown territory.

For many practical purposes — including error correction, compression, and
local self-repair — circuit completeness may suffice. The agent doesn't
need to compute Ackermann's function; it needs to apply bounded corrective
transformations to its local neighborhood.

### 15.5 Relationship to Existing Work

The distinction between circuit and Turing universality in reversible
systems is well-studied:

- **Schaeffer's physically universal CA** (2014): a 2-state 2D Margolus
  block cellular automaton that is reversible, valid everywhere, and
  physically universal (circuit-complete). It is NOT Turing-complete in the
  strong sense because structures diffuse away — you cannot maintain
  persistent computational infrastructure on an arbitrary background.

- **Salo & Törma's physically universal turmite** (2020/2023): a 5-state
  binary 2D Turing machine that is reversible, valid everywhere, physically
  universal, and also Turing-universal (via the separate "nontrivial
  turmites are Turing-universal" theorem of Maldonado et al. 2017). However,
  Turing universality still requires structured (periodic) initial
  conditions — the periodic background provides the computational
  infrastructure (wires, gates, gadgets) that the turmite traverses.

- **Morita's reversible CAs**: valid-everywhere dynamics, TC for finite
  configurations on quiescent background. The dynamics is a bijection on
  ALL configurations, but meaningful computation requires structure.

fb2d sits in the same position as these systems: its dynamics is a bijection
on the full state space (valid everywhere), it is TC given structured initial
conditions, and it is plausibly circuit-complete without them.

### 15.6 Implications for the Agent Project

The original goal was an agent that resists degradation by noise. The key
question is: does the agent need Turing completeness, or is circuit
completeness sufficient?

**Circuit completeness may be enough.** An error-correcting agent needs to:
1. Detect corruption in a local neighborhood (finite check)
2. Compute a correction (finite transformation)
3. Apply the correction (finite action)

None of these require unbounded loops. They require the ability to implement
*any* finite transformation on a bounded region — which is exactly what
circuit/physical universality provides.

The strongest known result in this direction is Salo & Törma's turmite:
it can implement any finite transformation on any finite region by
controlling only the exterior. This means that even if some region is
corrupted by noise, the surrounding machinery can "compute through" the
corruption and restore it. This is precisely the property we want.

### 15.7 Open Questions

1. **Is reversible TC without quiescent background possible?** No known
   construction achieves it. It may be provably impossible (perhaps via an
   information-theoretic argument about the entropy of the tape), but no
   such proof exists.

2. **Is fb2d physically universal?** We have not proved this. Physical
   universality requires showing that for ANY finite region R and ANY
   transformation T, there exists an exterior configuration that causes T.
   The dispatch-block architecture suggests this is plausible, but a proof
   would need to handle arbitrary region contents.

3. **Should fb2d be replaced by Salo & Törma's turmite?** Their machine
   has proven physical universality with just 5 states and 2 symbols. The
   tradeoff: fb2d has a rich ISA designed for (relatively) human-writable
   programs; the turmite has a 2-symbol alphabet requiring significant
   infrastructure to be programmable. Building on existing proven results
   is appealing, but the turmite would need an fb2d-like language layer
   on top.

---

## 16. Updated Summary (2026-02-19)

| Property | Status |
|----------|--------|
| Reversible | ✓ Every state has unique predecessor, inferred from state alone |
| Valid everywhere (dynamics) | ✓ Any byte sequence is a valid program; bijection on all states |
| Turing complete (with structured init) | ✓ (sketch) Simulates counter machines via dispatch blocks |
| Turing complete (arbitrary init) | ✗ Requires quiescent background (same as all known reversible TC models) |
| Circuit complete (plausible) | ~ Dispatch blocks can implement any finite transformation (not yet proved) |
| Physically universal | ? Unknown; plausible but unproved |
| Practically programmable | ✗ Encoding is polynomial but enormous |
| Self-modifying | Not needed for TC, but possible (code is on the tape) |
| Garbage-free | ✓ No garbage bits accumulate (all scratch cleaned per iteration) |

---

## References (added 2026-02-19)

- Bennett, C.H. (1973). "Logical reversibility of computation." IBM Journal
  of Research and Development, 17(6), 525-532.
- Fredkin, E. & Toffoli, T. (1982). "Conservative logic." International
  Journal of Theoretical Physics, 21(3-4), 219-253.
- Janzing, D. (2010). "Is there a physically universal cellular automaton or
  Hamiltonian?" arXiv:1009.1720.
- Maldonado, D., Gajardo, A., Hellouin de Menibus, B., & Moreira, A. (2017).
  "Nontrivial turmites are Turing-universal." arXiv:1702.05547.
- Morita, K. (various). Reversible cellular automata — see survey in
  "Theory of Reversible Computing" (Springer, 2017).
- Salo, V. & Törma, I. (2023). "A physically universal Turing machine."
  Journal of Computer and System Sciences, 132, 16-44. arXiv:2003.10328.
- Schaeffer, L. (2014). "A physically universal cellular automaton."
  Electronic Colloquium on Computational Complexity, TR14-084.

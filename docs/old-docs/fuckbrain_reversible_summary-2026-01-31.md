# F***brain Reversible Language Project - Conversation Summary

## Goal

Design a reversible, Turing-complete, "valid everywhere" language inspired by BFF (a Brainfuck variant), where:
- **Data and program live on the same tape** (self-modifying, any byte sequence is valid)
- **Truly reversible**: given any state, you can step backward unambiguously
- **Turing-complete**: can compute anything (demonstrated via loops)
- **Valid everywhere**: no compiler needed, any byte sequence is a legal program (unlike Morita's reversible machines which require structured pairing)

## Core Challenge: Reversibility at Control Flow Joins

The fundamental problem: when multiple execution paths converge (e.g., after an if-then-else, or at a loop head), you must know which path you took to reverse. Three options exist:

1. **Garbage bits** - write something during forward execution that records the path
2. **Structured pairing** - compiler ensures predecessors are unambiguous (Morita's approach, but NOT "valid everywhere")
3. **No joins** - don't allow control flow convergence (not Turing complete)

Since we want "valid everywhere," we're committed to **garbage bits**.

## The Original FB (Non-reversible)

The original FB simulator had:
- Registers: IP, CL, H0, H1
- Key instruction: `J` does `swap(IP, tape[CL])` - swaps instruction pointer with a memory cell
- This allowed loops via the "garbage pointer idiom": use `G` to indirect-address CL through a garbage area, with each cell pre-loaded with the loop target address. After J swaps, that cell holds the return address. Next iteration uses the NEXT garbage cell.

**Problem**: Not reversible! From state (IP=X, tape), you can't tell if you jumped to X or stepped to X.

## Reversibility Approaches Explored

### Approach 1: IR (Instruction Register) Mechanism

Sam Eisenstat's idea: Each step does `swap(tape[IP], IR)` before executing. The IR "bubble" travels with execution, leaving a trail of swapped values.

**Implementation**: `fb_ir_v2.py`
- Each step: (1) swap(tape[IP], IR), (2) execute IR, (3) IP changes
- The trail of deposited values records execution history

**ISA** (opcode in parens):
```
< (1)  CL--                  > (2)  CL++
{ (3)  H0--                  } (4)  H0++
( (5)  H1--                  ) (6)  H1++
+ (7)  tape[H0]++            - (8)  tape[H0]--
x (9)  swap tape[H0],tape[H1]
. (10) tape[H0] += tape[H1]  , (11) tape[H0] -= tape[H1]
J (12) if tape[CL] != 0: swap(IP, tape[H1]); then IP++
S (13) swap tape[CL],tape[H0]
G (14) swap CL,tape[H0]
```

**J semantics**: Condition is `tape[CL]`, target is `tape[H1]`. If condition nonzero, does `swap(IP, tape[H1])` then always increments IP.

**If-then-else**: Works! Both paths leave different IR trails, so the tape states are distinct at convergence, preserving reversibility.

**Decrementing loop**: Works, BUT requires **pre-laying-out N copies of the loop body** for N iterations. The IR swap scrambles code as it executes, so you can't revisit the same code cells. This means O(N × loop_size) tape for N iterations.

### Approach 2: Tape-swap on Jump

Another idea: J does `swap(tape[IP], tape[target])` to move the J instruction to the target location, marking "I jumped here."

**Problem**: This also moves code around, breaking loops that need to revisit positions.

### The Fundamental Tension with IR/Tape-swap

Both approaches modify `tape[IP]` (the code), which breaks loops:
- **IR**: swaps tape[IP] with IR every step, leaving a trail of changed bytes
- **Tape-swap**: J physically moves itself to the target

When you try to loop back to revisit code, the original instructions aren't there anymore.

**Contrast with original FB**: J did `swap(IP, tape[CL])`, modifying a **data cell** (tape[CL]), not the code. The code stayed in place; only garbage cells got consumed.

## Working Example: Decrementing Loop with IR

File: `dec_loop_ir.fb`

Layout:
```
Code (positions 0-15):
  0-2:   - ) J    (iteration 1 loop body)
  4-6:   - ) J    (iteration 2 loop body)
  8-10:  - ) J    (iteration 3 loop body)
  12-14: - ) J    (iteration 4 loop body)

Data (positions 32+):
  32:    3         (counter)
  33-36: 3,7,11,15 (jump targets)

Registers: IP=0, IR=0, CL=32, H0=32, H1=33
```

Each iteration:
1. `-` decrements counter
2. `)` advances H1 to next target cell
3. `J` jumps to next loop body if counter != 0

When counter hits 0, J falls through. **It works, but limited to 4 iterations max** because we only laid out 4 copies.

## Promising New Direction: Run-Length Encoded Jump History

At the end of the conversation, we discussed a scheme that could allow **code-in-place** (like original FB) while being **reversible**:

**The scheme:**
- `GP` = garbage pointer
- Non-jump: `tape[GP]++` (count consecutive non-jumps)
- Jump: `GP++`, `tape[GP] = 1` (move to fresh cell, start new count)
- Initialize: `tape[GP] = 1`

**Reversal rule:**
- If `tape[GP] > 1`: last was non-jump → `tape[GP]--`
- If `tape[GP] == 1`: last was jump → `tape[GP] = 0`, `GP--`

**Example trace**: N N J N J J N (N=non-jump, J=jump)

| Step | Action | GP | tape |
|------|--------|-----|------|
| init | - | 0 | [1,0,0,0] |
| N | tape[GP]++ | 0 | [2,0,0,0] |
| N | tape[GP]++ | 0 | [3,0,0,0] |
| J | GP++, tape[GP]=1 | 1 | [3,1,0,0] |
| N | tape[GP]++ | 1 | [3,2,0,0] |
| J | GP++, tape[GP]=1 | 2 | [3,2,1,0] |
| J | GP++, tape[GP]=1 | 3 | [3,2,1,1] |
| N | tape[GP]++ | 3 | [3,2,1,2] |

This is **run-length encoding** of the jump/non-jump history. Reversal reconstructs the sequence perfectly.

**Key insight**: Garbage grows with **number of jumps**, not total steps. A 1000-iteration loop with 1 jump per iteration uses ~1000 garbage cells, same as original FB.

Combined with `swap(IP, tape[H1])` for return addresses, this could give:
- Code stays in place (no IR scrambling)
- True reversibility (GP + run-length encoding)
- O(jumps) garbage, not O(steps)

**Status**: Not yet implemented. This was the promising direction when we stopped.

## Key Files

- `fb_ir_v2.py` - IR-based simulator with conditional jump (working)
- `dec_loop_ir.fb` - Decrementing loop example for IR simulator
- `fb_tapeswap_v3.py` - Tape-swap variant (has issues with loops)
- `fb_simulator.py` - Original (non-reversible) simulator

## Open Questions

1. Can the run-length encoding scheme be implemented cleanly?
2. How does J interact with the run-length scheme? (J needs to store return address somewhere - probably still `swap(IP, tape[H1])`)
3. Can we prove this combination is truly reversible?
4. What's the minimal ISA that achieves all goals?

## Thermodynamic Connection

This work connects to algorithmic thermodynamics: irreversibility requires entropy/garbage generation. The run-length scheme is essentially the minimal garbage needed to record control flow history - it's a kind of "reversible compression" of the execution trace.

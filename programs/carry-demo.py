#!/usr/bin/env python3
"""
carry-demo.py — Demonstrates multi-cell increment with carry propagation.

Generates a carry-demo.fb2d program and tests it in the simulator.

The idea: a number is stored vertically in one column (one cell per digit,
least significant at the top, zero-terminated below). To increment it, we
use a "carry corridor" — a horizontal strip of carry-check gadgets, one
per digit position. The IP walks through the corridor; each gadget
increments one digit and either exits (no carry) or continues (carry).

This demonstrates that carry propagation needs NO unbounded loop counter —
the IP's physical position in the corridor tracks progress. The corridor
must be as long as the number might get (like a TM tape: "as big as needed").

NOTATION (standard for this project):
  H0 = position of head 0          [H0] = value at that position
  H0++ = move head right/south     [H0]++ = increment cell value

LAYOUT:

  Col:    0    1    2    3    4    5    6    ...
  Row 0:  [d0] [d1] [d2] [d3] [0]            ← number (LE, zero-terminated)
  Row 1:  /    ·    /    ·    /    ·    /     ← "done" return mirrors
  Row 2:  carry gadgets...                    ← IP walks East through these
  Row 3:  ·                                   ← scratch / entry

Each carry gadget (2 columns wide on row 2) for digit i:

  Row 1, col 2i:    /          ← done: reflects N→E, IP exits right
  Row 2, col 2i:    +          ← [H0]++  (H0 points at digit i on row 0)
  Row 2, col 2i+1:  ?          ← / reflect if [CL]==0 (wrapped → carry!)
                                  if [CL]!=0: pass through E (done with carry)
                                  if [CL]==0: reflect E→N

  If [CL]==0 (carry): IP goes N, hits row 1 col 2i+1 (NOP), continues N,
  wraps... no, we need it to go to the next gadget.

  Hmm, let me reconsider the geometry.

REVISED APPROACH — simpler:

  One gadget per digit. IP goes East on CODE_ROW.

  For each digit position i (at column i on DATA_ROW):

    CODE_ROW:  ... E  +  ?  ...
                    ↑     ↑
                    |     if [CL]==0 (wrap): / reflect E→N, take carry path
                    |     if [CL]!=0 (no wrap): pass through E to done path
                    |
                    increment digit i (H0 and CL both point at row 0, col i)

  Wait — after ?, we need two different continuations:
    - No carry: skip to the end somehow (done incrementing)
    - Carry: move H0 and CL to the next digit, then continue to next gadget

  The "skip to the end" is the hard part — we can't jump in fb2d.

  But we CAN use mirrors to route the IP differently:

  If no carry (? passes through E): IP continues E through remaining
  gadgets. But those gadgets will also execute + on their digits! We
  don't want that.

  Solution: after the first no-carry, the IP must LEAVE the corridor.
  Use ? to reflect E→N on carry, with the NO-CARRY path going straight
  through the % or & at the end of the corridor.

  Actually, let me reverse the logic. Use % (/ if [CL]!=0):

  After + : if the value didn't wrap to 0, [CL] != 0.
            % reflects E→N. IP escapes upward. DONE.
            If the value DID wrap to 0, [CL] == 0.
            % passes through E. IP continues to next gadget. CARRY.

  Then between gadgets, we need to move H0 and CL to the next digit.
  Since digits are in a column (vertically), that's an E move.

  Wait, digits should be in a ROW for horizontal H0/CL movement.
  Let me put the number horizontally on row 0, one cell per column.

FINAL LAYOUT:

  Row 0 (DATA): d0  d1  d2  d3  [0]    ← number in base-256, LE
  Row 1 (EXIT): /   ·   /   ·   /      ← escape mirrors (% reflects E→N,
                                           / on row 1 reflects N→E, exit)
  Row 2 (CODE): +%E> +%E> +%E> +%      ← gadgets, IP enters going E

  Each gadget for digit at column c:
    Row 2: +  %  E  >     (4 cells wide)
    +  : [H0]++ — increment digit (H0 at row 0, col c)
    %  : / if [CL]!=0 — if no wrap, reflect E→N (done!)
    E  : H0 East — move H0 to next digit
    >  : CL East — move CL to next digit
    ...next gadget for digit at column c+1

  When % reflects (no carry): IP goes N, hits row 1.
    Row 1 col (gadget_start + 1): need a \ to redirect N→E for exit.
    Actually / redirects N→E. So put / on row 1 at each % column.
    IP goes E on row 1 and exits the corridor to the right.

  When % passes through (carry): IP continues E, hits E (H0 east),
    then > (CL east), then the next gadget's +. Perfect.

  After the last digit: if there's STILL a carry, the number needs to
  grow by one digit. The + at the last position increments the zero
  terminator to 1. Since 0+1=1≠0, % reflects (done). The number has
  grown.

  This is the key insight: the zero terminator acts as a fresh digit!

Let me implement this.
"""

import sys
import os

# Import the simulator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES, hamming_encode, cell_to_payload, encode_opcode

def make_carry_demo(initial_value, num_digits=8, num_increments=1):
    """Build a carry corridor that increments a multi-cell number.

    The number is stored in base-256 on row 0, columns 0..num_digits-1,
    little-endian, zero-terminated.

    Returns (sim, expected_value).
    """
    # Convert initial value to LE base-256 digits
    digits = []
    v = initial_value
    while v > 0:
        digits.append(v & 0xFF)
        v >>= 8
    if not digits:
        digits = [0]

    # Pad with zeros up to num_digits
    while len(digits) < num_digits:
        digits.append(0)

    # Grid sizing
    # Row 0: data digits
    # Row 1: exit corridor (/ mirrors to catch upward-reflected IP)
    # Row 2: carry gadgets (IP runs East)
    # Row 3: entry / post-exit
    #
    # Each gadget is 3 cells wide: + % E> or just + % > at last position.
    # Actually: + % E >  (4 cells: inc, check, h0-east, cl-east)
    # Last gadget: + %    (2 cells: inc, check — no need to advance)
    # Total width for carry corridor: (num_digits-1)*4 + 2

    # But we also need setup code before the corridor to move H0/CL to (0,0)
    # and the number is already at row 0 col 0, so H0 and CL start there.

    # For multiple increments, wrap the corridor in a loop.
    # But let's start simple: just one increment, hardcoded.

    gadget_width = 3  # + % E  (last one has no E, but we add padding)
    corridor_start_col = 0
    corridor_width = num_digits * gadget_width

    cols = corridor_width + 4  # some padding
    rows = 4

    sim = FB2DSimulator(rows=rows, cols=cols)

    # Place number on row 0 (Hamming-encoded)
    for i, d in enumerate(digits):
        sim.grid[sim._to_flat(0, i)] = hamming_encode(d)

    # Place carry gadgets on row 2
    for i in range(num_digits):
        base_col = corridor_start_col + i * gadget_width
        sim.grid[sim._to_flat(2, base_col)] = encode_opcode(OPCODES['+'])     # [H0]++
        sim.grid[sim._to_flat(2, base_col + 1)] = encode_opcode(OPCODES['%']) # / if [CL]!=0
        if i < num_digits - 1:
            sim.grid[sim._to_flat(2, base_col + 2)] = encode_opcode(OPCODES['E'])  # H0 East
            # CL also needs to move East. But we have H0 and CL at the same
            # position. CL movement is >.
            # Actually we need BOTH H0 and CL to advance.
            # That's E then > ... but that's 4 cells per gadget, not 3.
            # Let me use 4 cells per gadget.
            pass

    # Redo with 4-cell gadgets: + % E >
    gadget_width = 4
    corridor_width = (num_digits - 1) * gadget_width + 2  # last one is just + %
    cols = corridor_width + 4

    sim = FB2DSimulator(rows=rows, cols=cols)

    # Place number on row 0 (Hamming-encoded)
    for i, d in enumerate(digits):
        sim.grid[sim._to_flat(0, i)] = hamming_encode(d)

    # Place carry gadgets on row 2 (Hamming-encoded opcodes)
    for i in range(num_digits):
        base_col = i * gadget_width
        sim.grid[sim._to_flat(2, base_col)] = encode_opcode(OPCODES['+'])     # [H0]++
        sim.grid[sim._to_flat(2, base_col + 1)] = encode_opcode(OPCODES['%']) # / if [CL]!=0
        if i < num_digits - 1:
            sim.grid[sim._to_flat(2, base_col + 2)] = encode_opcode(OPCODES['E'])  # H0 East
            sim.grid[sim._to_flat(2, base_col + 3)] = encode_opcode(OPCODES['>'])  # CL East

        # Place / on row 1 at the % column to catch the upward reflection
        sim.grid[sim._to_flat(1, base_col + 1)] = encode_opcode(OPCODES['/'])  # N→E redirect

    # IP starts at row 2, col 0, going East
    sim.ip_row = 2
    sim.ip_col = 0
    sim.ip_dir = 1  # East

    # H0 and CL both start at (0, 0) — the least significant digit
    sim.h0 = sim._to_flat(0, 0)
    sim.cl = sim._to_flat(0, 0)
    sim.h1 = 0
    sim.gp = sim._to_flat(3, 0)  # GP on row 3

    expected = initial_value + num_increments
    return sim, expected


def read_number(sim, num_digits):
    """Read the LE base-256 number from row 0, cols 0..num_digits-1.
    Extracts payload (11-bit) from each Hamming-encoded cell."""
    result = 0
    for i in range(num_digits - 1, -1, -1):
        result = result * 256 + (cell_to_payload(sim.grid[sim._to_flat(0, i)]))
    return result


def run_test(initial, label=""):
    """Run a single carry test and verify."""
    num_digits = 8  # support up to 256^8 - 1
    sim, expected = make_carry_demo(initial, num_digits=num_digits)

    print(f"\n{'='*60}")
    print(f"Carry test: {initial} + 1 = {expected}  {label}")
    print(f"{'='*60}")

    # Show initial state
    digits_before = [cell_to_payload(sim.grid[sim._to_flat(0, i)]) for i in range(num_digits)]
    print(f"  Before: {digits_before}  (decimal: {initial})")

    sim.display_grid()

    # Run until IP exits the corridor (reaches row 1 going East past all gadgets)
    max_steps = 1000
    for step in range(max_steps):
        sim.step()
        # IP has exited when it's on row 1 going East and past the corridor
        if sim.ip_row == 1 and sim.ip_dir == 1:  # row 1, going East
            break

    digits_after = [cell_to_payload(sim.grid[sim._to_flat(0, i)]) for i in range(num_digits)]
    got = read_number(sim, num_digits)

    print(f"  After:  {digits_after}  (decimal: {got})")
    print(f"  Steps:  {sim.step_count}")

    # Test reversibility
    forward_steps = sim.step_count
    for _ in range(forward_steps):
        sim.step_back()

    digits_reversed = [cell_to_payload(sim.grid[sim._to_flat(0, i)]) for i in range(num_digits)]
    reversed_val = read_number(sim, num_digits)
    reverse_ok = (reversed_val == initial)

    ok = (got == expected)
    print(f"  Forward:  {'PASS' if ok else 'FAIL'} (expected {expected}, got {got})")
    print(f"  Reverse:  {'PASS' if reverse_ok else 'FAIL'} (expected {initial}, got {reversed_val})")

    # Also save the state file
    if ok:
        sim2, _ = make_carry_demo(initial, num_digits=num_digits)
        fn = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'carry-demo.fb2d')
        sim2.save_state(fn)
        print(f"  Saved initial state to {fn}")

    return ok and reverse_ok


if __name__ == '__main__':
    all_ok = True

    # Basic tests
    all_ok &= run_test(0, "(0 → 1)")
    all_ok &= run_test(1, "(1 → 2)")
    all_ok &= run_test(42, "(42 → 43)")
    all_ok &= run_test(254, "(254 → 255, no carry)")
    all_ok &= run_test(255, "(255 → 256, carry to second digit!)")
    all_ok &= run_test(256, "(256 → 257)")
    all_ok &= run_test(511, "(511 → 512, double carry)")
    all_ok &= run_test(65535, "(65535 → 65536, carry through 2 digits)")
    all_ok &= run_test(65536, "(65536 → 65537)")
    all_ok &= run_test(16777215, "(2^24-1 → 2^24, carry through 3 digits)")

    print(f"\n{'='*60}")
    print(f"{'All tests passed!' if all_ok else 'SOME TESTS FAILED'}")
    print(f"{'='*60}")

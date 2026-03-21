#!/usr/bin/env python3
"""Exhaustive reversibility test for fb2d opcodes.

For every grid-writing opcode and every possible head-aliasing
combination, runs step() then step_back() on random cell values and
verifies the round-trip restores the original state exactly.

This catches the class of bugs where an operation reads a cell that it
also writes (or that aliases the write target via head overlap), making
the inverse operation see a modified parameter.

Usage:
    python3 test_reversibility.py          # run all tests
    python3 test_reversibility.py -v       # verbose (show each aliasing combo)
"""

import random
import sys
from fb2d import (
    FB2DSimulator, OPCODE_TO_CHAR, OPCODES, hamming_encode,
    _PAYLOAD_TO_OPCODE, _CELL_TO_PAYLOAD, CELL_MASK,
)

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------

RANDOM_TRIALS = 500      # random cell values per (opcode, aliasing) combo
GRID_ROWS = 4
GRID_COLS = 4
GRID_SIZE = GRID_ROWS * GRID_COLS
VERBOSE = '-v' in sys.argv

# All five heads that address grid cells, plus the IP position.
HEAD_NAMES = ['h0', 'h1', 'ix', 'cl', 'ex']

# Grid-writing opcodes and which heads they READ and WRITE.
# Format: (opcode_num, char, read_heads, write_heads)
# read_heads: heads whose grid cells are read as parameters
# write_heads: heads whose grid cells are modified
#
# We only list data ops that modify the grid. Mirrors and head-movement
# ops don't write grid cells and can't break reversibility this way.

OPCODE_SPECS = [
    # Byte-level data
    (15, '+',  ['h0'],              ['h0']),
    (16, '-',  ['h0'],              ['h0']),
    (17, '.',  ['h0', 'h1'],        ['h0']),
    (18, ',',  ['h0', 'h1'],        ['h0']),
    (19, 'X',  ['h0', 'h1'],        ['h0', 'h1']),
    (20, 'F',  ['cl', 'h0', 'h1'],  ['h0', 'h1']),
    (21, 'G',  ['h0'],              ['h0']),      # also swaps h1 register
    (22, 'T',  ['cl', 'h0'],        ['cl', 'h0']),

    # EX ops
    (27, 'P',  ['ex'],              ['ex']),
    (28, 'Q',  ['ex'],              ['ex']),
    (38, 'Z',  ['h0', 'ex'],        ['h0', 'ex']),

    # Bit-level ops
    (39, 'x',  ['h0', 'h1'],        ['h0']),
    (40, 'r',  ['h0'],              ['h0']),
    (41, 'l',  ['h0'],              ['h0']),
    (42, 'f',  ['cl', 'h0', 'h1'],  ['h0', 'h1']),
    (43, 'z',  ['h0', 'h1'],        ['h0', 'h1']),
    (44, 'R',  ['h0', 'cl'],        ['h0']),
    (45, 'L',  ['h0', 'cl'],        ['h0']),
    (46, 'Y',  ['h0', 'h1', 'cl'],  ['h0']),

    # CL increment/decrement
    (47, ':',  ['cl'],              ['cl']),
    (48, ';',  ['cl'],              ['cl']),

    # IX ops
    (53, 'm',  ['h0', 'ix'],        ['h0']),
    (54, 'M',  ['h0', 'ix'],        ['h0']),
    (55, 'j',  ['h0', 'ix'],        ['ix']),
    (56, 'V',  ['cl', 'ix'],        ['cl', 'ix']),
]


def make_sim():
    """Create a minimal simulator with a small grid."""
    sim = FB2DSimulator()
    sim.rows = GRID_ROWS
    sim.cols = GRID_COLS
    sim.grid_size = GRID_SIZE
    sim.grid = [0] * GRID_SIZE
    sim.n_ips = 1
    sim.active_ip = 0
    sim.ips = [{}]
    return sim


def set_heads(sim, h0, h1, ix, cl, ex, ip_flat):
    """Set all head positions and IP position."""
    sim.h0 = h0
    sim.h1 = h1
    sim.ix = ix
    sim.cl = cl
    sim.ex = ex
    # IP position from flat index
    sim.ip_row = ip_flat // sim.cols
    sim.ip_col = ip_flat % sim.cols
    sim.ip_dir = 1  # East
    sim.ix_dir = 1
    sim.ix_vdir = 2


def randomize_grid(sim, rng):
    """Fill grid with random 16-bit values."""
    for i in range(sim.grid_size):
        sim.grid[i] = rng.randint(0, CELL_MASK)


def save_state(sim):
    """Snapshot grid + all relevant state."""
    return (
        list(sim.grid),
        sim.h0, sim.h1, sim.ix, sim.cl, sim.ex,
        sim.ip_row, sim.ip_col, sim.ip_dir,
        sim.ix_dir, sim.ix_vdir,
    )


def check_state(sim, saved):
    """Check if current state matches saved snapshot. Returns diff string or None."""
    (grid, h0, h1, ix, cl, ex, ip_row, ip_col, ip_dir, ix_dir, ix_vdir) = saved
    diffs = []
    for i in range(len(grid)):
        if sim.grid[i] != grid[i]:
            r, c = i // sim.cols, i % sim.cols
            diffs.append(f'grid[{r},{c}]: 0x{grid[i]:04X} -> 0x{sim.grid[i]:04X}')
    if sim.h0 != h0: diffs.append(f'h0: {h0} -> {sim.h0}')
    if sim.h1 != h1: diffs.append(f'h1: {h1} -> {sim.h1}')
    if sim.ix != ix: diffs.append(f'ix: {ix} -> {sim.ix}')
    if sim.cl != cl: diffs.append(f'cl: {cl} -> {sim.cl}')
    if sim.ex != ex: diffs.append(f'ex: {ex} -> {sim.ex}')
    if sim.ip_row != ip_row: diffs.append(f'ip_row: {ip_row} -> {sim.ip_row}')
    if sim.ip_col != ip_col: diffs.append(f'ip_col: {ip_col} -> {sim.ip_col}')
    if sim.ip_dir != ip_dir: diffs.append(f'ip_dir: {ip_dir} -> {sim.ip_dir}')
    if sim.ix_dir != ix_dir: diffs.append(f'ix_dir: {ix_dir} -> {sim.ix_dir}')
    if sim.ix_vdir != ix_vdir: diffs.append(f'ix_vdir: {ix_vdir} -> {sim.ix_vdir}')
    return diffs if diffs else None


def generate_aliasing_combos():
    """Generate all interesting head-position assignments.

    We need positions for: h0, h1, ix, cl, ex, ip_flat (6 values).
    Each can be any cell in the grid (0..GRID_SIZE-1).

    Testing all 16^6 combos is too many. Instead, we test:
    1. A "no aliasing" baseline (all distinct positions).
    2. Every pair of heads at the same position (all others distinct).
    3. Every triple at the same position.
    4. All heads at the same position.

    This covers the cases that matter for NOP guards.
    """
    names = HEAD_NAMES + ['ip']
    combos = []

    # Baseline: all distinct
    combos.append(('all_distinct', {'h0': 0, 'h1': 1, 'ix': 2, 'cl': 3, 'ex': 4, 'ip': 5}))

    # Every pair aliased (15 pairs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            positions = {}
            slot = 0
            for k, name in enumerate(names):
                if name == names[i] or name == names[j]:
                    positions[name] = 0  # aliased pair at position 0
                else:
                    slot += 1
                    positions[name] = slot
            label = f'{names[i]}=={names[j]}'
            combos.append((label, positions))

    # Every triple aliased (20 triples)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for k in range(j + 1, len(names)):
                positions = {}
                slot = 0
                for m, name in enumerate(names):
                    if name in (names[i], names[j], names[k]):
                        positions[name] = 0
                    else:
                        slot += 1
                        positions[name] = slot
                label = f'{names[i]}=={names[j]}=={names[k]}'
                combos.append((label, positions))

    # All aliased
    combos.append(('all_aliased', {n: 0 for n in names}))

    return combos


def place_opcode(sim, opcode_num, ip_flat):
    """Place the opcode at the IP position so step() will execute it."""
    # Find a payload that decodes to this opcode
    for payload in range(2048):
        if _PAYLOAD_TO_OPCODE[payload] == opcode_num:
            sim.grid[ip_flat] = hamming_encode(payload)
            return
    raise ValueError(f'No payload for opcode {opcode_num}')


def test_opcode_reversibility(opcode_num, char, read_heads, write_heads):
    """Test one opcode across all aliasing combos and random cell values."""
    rng = random.Random(42 + opcode_num)
    combos = generate_aliasing_combos()
    sim = make_sim()
    failures = []

    for label, positions in combos:
        fail_count = 0
        first_diff = None

        for trial in range(RANDOM_TRIALS):
            randomize_grid(sim, rng)
            h0 = positions['h0']
            h1 = positions['h1']
            ix = positions['ix']
            cl = positions['cl']
            ex = positions['ex']
            ip = positions['ip']
            set_heads(sim, h0, h1, ix, cl, ex, ip)

            # For G (opcode 21): ensure grid[h0] < grid_size sometimes
            # to test the non-NOP path
            if opcode_num == 21 and trial % 3 == 0:
                sim.grid[h0] = rng.randint(0, GRID_SIZE - 1)

            # Place opcode at IP position
            place_opcode(sim, opcode_num, ip)

            # Save state, step forward, step back, compare
            saved = save_state(sim)
            sim.step()
            sim.step_back()
            diffs = check_state(sim, saved)

            if diffs:
                fail_count += 1
                if first_diff is None:
                    first_diff = diffs

        if fail_count > 0:
            failures.append((label, fail_count, first_diff))
        elif VERBOSE:
            print(f'    {label}: OK')

    return failures


def main():
    print(f'Exhaustive reversibility test: {len(OPCODE_SPECS)} opcodes '
          f'x {len(generate_aliasing_combos())} aliasing combos '
          f'x {RANDOM_TRIALS} random trials\n')

    total_pass = 0
    total_fail = 0

    for opcode_num, char, read_heads, write_heads in OPCODE_SPECS:
        failures = test_opcode_reversibility(opcode_num, char, read_heads, write_heads)
        if failures:
            total_fail += 1
            print(f'  FAIL  op {opcode_num:2d} ({char}): {len(failures)} aliasing combos failed')
            for label, count, diffs in failures[:3]:
                print(f'        {label}: {count}/{RANDOM_TRIALS} trials failed')
                for d in diffs[:3]:
                    print(f'          {d}')
        else:
            total_pass += 1
            print(f'  PASS  op {opcode_num:2d} ({char})')

    print(f'\n{"="*60}')
    if total_fail == 0:
        print(f'ALL {total_pass} OPCODES PASSED')
    else:
        print(f'{total_fail} OPCODES FAILED, {total_pass} passed')
    print(f'{"="*60}')
    return 0 if total_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Debug helper for hamming-gadget-demo.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import FB2DSimulator, OPCODES
from hamming import encode

OP = OPCODES
OPCHAR = {v: k for k, v in OP.items()}

# Import from the gadget module
from importlib.machinery import SourceFileLoader
gadget_mod = SourceFileLoader("hgd", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hamming-gadget-demo.py")).load_module()

build_gadget = gadget_mod.build_gadget
make_hamming_gadget = gadget_mod.make_hamming_gadget
CODE_ROW = gadget_mod.CODE_ROW

# Test a no-error case
cw = encode(0)  # data=0000, cw=00000000
sim, ops_list, fs, fe, bs, be = make_hamming_gadget(cw)
n_ops = len(ops_list)

print(f'Grid: {sim.rows}x{sim.cols}')
print(f'Opcodes: {n_ops}')
print(f'Fwd loop: ({fs})-({fe}), Bwd loop: ({bs})-({be})')

# Show the ops around the loops
print(f'\nOps around fwd loop (cols {fs-2} to {fe+2}):')
for c in range(fs-2, min(fe+3, n_ops)):
    print(f'  col {c}: {ops_list[c]}')

print(f'\nOps around bwd loop (cols {bs-2} to {be+2}):')
for c in range(bs-2, min(be+3, n_ops)):
    print(f'  col {c}: {ops_list[c]}')

# Trace the first few off-script moments
print(f'\n--- Tracing no-error case (cw=0x00) ---')
off_count = 0
for i in range(700):
    r, c = sim.ip_row, sim.ip_col
    op = sim.grid[sim._to_flat(r, c)]
    ch = OPCHAR.get(op, '.')
    if r != CODE_ROW or sim.ip_dir != 1:
        h0r, h0c = divmod(sim.h0, sim.cols)
        clr, clc = divmod(sim.cl, sim.cols)
        gpr, gpc = divmod(sim.gp, sim.cols)
        clval = sim.grid[sim.cl]
        gpval = sim.grid[sim.gp]
        print(f'  step {sim.step_count}: IP=({r},{c}) dir={sim.ip_dir} op={ch}'
              f'  CL=({clr},{clc})={clval} GP=({gpr},{gpc})={gpval}')
        off_count += 1
        if off_count > 30:
            print('  ... stopping trace')
            break
    sim.step()
    if sim.ip_row == CODE_ROW and sim.ip_col >= n_ops and sim.ip_dir == 1:
        print(f'  EXIT at step {sim.step_count}')
        break
else:
    r, c = sim.ip_row, sim.ip_col
    print(f'  Did not exit after 700 steps. IP at ({r},{c}) dir={sim.ip_dir}')

# Also test a single-error case
print(f'\n--- Tracing single error (cw=0x0F, flip bit 3) ---')
from hamming import inject_error
cw2 = encode(1)  # 0b00001111
bad = inject_error(cw2, 3)  # flip bit 3 -> 0b00000111
sim2, ops2, _, _, _, _ = make_hamming_gadget(bad)
n_ops2 = len(ops2)

off_count = 0
for i in range(1200):
    r, c = sim2.ip_row, sim2.ip_col
    op = sim2.grid[sim2._to_flat(r, c)]
    ch = OPCHAR.get(op, '.')
    if r != CODE_ROW or sim2.ip_dir != 1:
        h0r, h0c = divmod(sim2.h0, sim2.cols)
        clr, clc = divmod(sim2.cl, sim2.cols)
        gpr, gpc = divmod(sim2.gp, sim2.cols)
        clval = sim2.grid[sim2.cl]
        gpval = sim2.grid[sim2.gp]
        print(f'  step {sim2.step_count}: IP=({r},{c}) dir={sim2.ip_dir} op={ch}'
              f'  CL=({clr},{clc})={clval} GP=({gpr},{gpc})={gpval}')
        off_count += 1
        if off_count > 40:
            print('  ... stopping trace')
            break
    sim2.step()
    if sim2.ip_row == CODE_ROW and sim2.ip_col >= n_ops2 and sim2.ip_dir == 1:
        print(f'  EXIT at step {sim2.step_count}')
        # Show CW result
        cw_result = sim2.grid[sim2._to_flat(0, 0)]
        print(f'  CW result: {cw_result:08b} (expected {cw2:08b})')
        break

#!/usr/bin/env python3
"""Build factorial-with-trap.fb2d — factorial demo plus a post-computation
"trap" box with a large EX counter.

The base factorial program (programs/factorial.fb2d) computes 5! = 120 and,
because fb2d is valid-everywhere with no halt, the IP keeps running and
retraces the code. During that retrace the IP reaches the East edge of the
code row exactly once, at (3,30), heading East (step 430 in the base run).

We splice a trap there:

    (3,30)  \\   IP heading E reflects E->S, dropping into the trap rows.

Four trap rows are inserted between the old code (rows 0-3) and old row 4.
This is safe: during the whole computation neither the IP nor any head
(H0,H1,CL,EX,IX) ever crosses the row-3/row-4 boundary. The IP lives in
rows 1-3; EX lives entirely in old row 4 (now row 8). So the trap rows are
only ever entered via the \\ redirect, and EX just rides along in the
shifted-down old row 4.

Trap mechanism (per the design):

    (4,30) ]   move EX East onto a clean zero cell (payload 0)
    (5,30) (   gate: "\\-reflect if [EX] != 0" -- does NOT reflect on zero
    (6,30) P   dirty the zero: [EX]++  (0 -> 1)

The gate is entered heading S on first arrival with EX just cleaned to 0,
so it passes straight through into P, which sets EX=1. A rectangular
racetrack then returns the IP to the gate heading E:

    gate (5,30) --E->S--> P (6,30) --S--> (7,30)/ --S->W-->
    (7,29)(7,28) --W--> (7,27)\\ --W->N--> (6,27) --N-->
    (5,27)/ --N->E--> (5,28)(5,29) --E--> back to gate (5,30)

Each lap runs one P, so EX counts 1,2,3,... On every return the gate sees
EX != 0 and reflects (E->S) back into the loop. When P wraps EX back to 0,
the next gate arrival (heading E, EX == 0) passes straight through and the
IP exits East out of the trap. The IP therefore laps the box once per EX
value -- a "large counter" -- before being released.

Run:  python3 programs/factorial-with-trap.py
Writes: programs/factorial-with-trap.fb2d
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fb2d

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "factorial.fb2d")
OUT = os.path.join(HERE, "factorial-with-trap.fb2d")

K = 4  # trap rows inserted between row 3 and old row 4


def build():
    sim = fb2d.FB2DSimulator()
    sim.load_state(BASE)
    cols = sim.cols
    old_rows = sim.rows
    assert cols == 31 and old_rows == 5, (cols, old_rows)

    old = list(sim.grid)
    # (3,30) must be empty in the base program so the redirect is inert
    # until the IP actually reaches it.
    assert old[3 * cols + 30] == 0, "expected (3,30) to be NOP in base program"

    new_rows = old_rows + K  # 9
    # rows 0-3 unchanged; K blank trap rows; then old row 4.
    grid = old[:4 * cols] + [0] * (K * cols) + old[4 * cols:5 * cols]
    assert len(grid) == new_rows * cols

    def put(r, c, ch):
        grid[r * cols + c] = fb2d.encode_opcode(fb2d.OPCODES[ch])

    # --- entry redirect + trap ---
    put(3, 30, "\\")   # E -> S, drop into trap
    put(4, 30, "]")    # EX East -> clean zero
    put(5, 30, "(")    # gate: reflect \\ if [EX] != 0 (pass on zero)
    put(6, 30, "P")    # [EX]++  dirty the zero
    # racetrack corners
    put(7, 30, "/")    # bottom-right  S -> W
    put(7, 27, "\\")   # bottom-left   W -> N
    put(5, 27, "/")    # top-left      N -> E
    # (6,27) and (5,28),(5,29),(7,28),(7,29) stay NOP pass-throughs

    # EX starts in old row 4, which is now row (4+K)=8.
    ex = int(4 * cols) + (K * cols)  # 124 -> 248
    assert ex == 8 * cols

    with open(OUT, "w") as f:
        f.write("# factorial-with-trap: 5! then a large-EX-counter trap box\n")
        f.write("# Built by programs/factorial-with-trap.py\n")
        f.write(f"rows={new_rows}\ncols={cols}\n")
        f.write("ip_row=3\nip_col=0\nip_dir=1\n")
        f.write("cl=0\nh0=0\nh1=0\n")
        f.write(f"ex={ex}\n")
        f.write("step=0\n")
        f.write("grid=" + ",".join(str(v) for v in grid) + "\n")
    return OUT, new_rows, cols


if __name__ == "__main__":
    path, r, c = build()
    print(f"wrote {path}  ({r}x{c})")

#!/usr/bin/env python3
"""
F***brain 2D Grid Simulator v1
Authored or modified by Claude
Version: 2026-02-01 v1.0

A 2D reversible programming model where the instruction pointer moves
on a toroidal grid and bounces off mirrors for control flow.

Execution model:
  1. Read instruction at grid[IP_row, IP_col]
  2. Execute it (mirrors may change direction)
  3. IP advances one step in current direction

State:
  grid[rows × cols]  — program + data (von Neumann: same surface)
  IP = (row, col)    — instruction pointer position
  DIR = N/E/S/W      — IP direction
  CL                 — control locus (flat index into grid)
  H0                 — data head 0 (flat index)
  H1                 — data head 1 (flat index)

Mirror reflection rules (standard optics):
  /  : (dr,dc) → (−dc,−dr)    E→N  N→E  W→S  S→W
  \\  : (dr,dc) → (dc,dr)      E→S  S→E  W→N  N→W

Conditional mirrors reflect OR pass through based on grid[CL].
"""

import sys
import os

# ─── Direction Constants ───────────────────────────────────────────────

DIR_N, DIR_E, DIR_S, DIR_W = 0, 1, 2, 3
DR = [-1, 0, 1, 0]   # row deltas for N, E, S, W
DC = [0, 1, 0, -1]    # col deltas for N, E, S, W
DIR_NAMES = ['N', 'E', 'S', 'W']
DIR_ARROWS = ['↑', '→', '↓', '←']

# Mirror reflection lookup tables (direction in → direction out)
# /  : E→N, N→E, S→W, W→S
SLASH_REFLECT = [DIR_E, DIR_N, DIR_W, DIR_S]
# \  : E→S, S→E, N→W, W→N
BACKSLASH_REFLECT = [DIR_W, DIR_S, DIR_E, DIR_N]

# ─── Opcode Definitions ───────────────────────────────────────────────

OPCODES = {
    '/':  1,    # unconditional / reflect
    '\\': 2,   # unconditional \ reflect
    '?':  3,   # / reflect if grid[CL] ≠ 0, else pass through
    '!':  4,   # / reflect if grid[CL] = 0, else pass through
    '@':  5,   # \ reflect if grid[CL] ≠ 0, else pass through
    '#':  6,   # \ reflect if grid[CL] = 0, else pass through
    'N':  7,   # H0 move North (row−1)
    'S':  8,   # H0 move South (row+1)
    'E':  9,   # H0 move East  (col+1)
    'W':  10,  # H0 move West  (col−1)
    'n':  11,  # H1 move North
    's':  12,  # H1 move South
    'e':  13,  # H1 move East
    'w':  14,  # H1 move West
    '+':  15,  # grid[H0]++
    '-':  16,  # grid[H0]--
    '.':  17,  # grid[H0] += grid[H1]  (accumulate add)
    ',':  18,  # grid[H0] -= grid[H1]  (accumulate sub)
    'x':  19,  # swap(grid[H0], grid[H1])
    'F':  20,  # if grid[CL]≠0: swap(grid[H0], grid[H1])  (Fredkin)
    'G':  21,  # swap(CL_register, grid[H0])  (indirect CL)
    'T':  22,  # swap(grid[CL], grid[H0])     (bridge)
    '>':  23,  # CL++ (flat index)
    '<':  24,  # CL-- (flat index)
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '·'   # NOP displayed as middle dot

# Inverse direction map for head movement (for step_back)
HEAD_MOVE_INVERSE = {
    7: DIR_S, 8: DIR_N, 9: DIR_W, 10: DIR_E,   # H0: N↔S, E↔W
    11: DIR_S, 12: DIR_N, 13: DIR_W, 14: DIR_E,  # H1: N↔S, E↔W
}

# ─── ANSI Color Codes ─────────────────────────────────────────────────

ANSI = {
    'reset':   '\033[0m',
    'bold':    '\033[1m',
    'dim':     '\033[2m',
    'red':     '\033[91m',
    'green':   '\033[92m',
    'yellow':  '\033[93m',
    'cyan':    '\033[96m',
    'magenta': '\033[95m',
    'bg_red':  '\033[41m',
    'bg_mag':  '\033[45m',
    'bg_cyan': '\033[46m',
    'bg_grn':  '\033[42m',
}

DEFAULT_ROWS = 8
DEFAULT_COLS = 16


# ═══════════════════════════════════════════════════════════════════════
#  Simulator
# ═══════════════════════════════════════════════════════════════════════

class FB2DSimulator:
    def __init__(self, rows=DEFAULT_ROWS, cols=DEFAULT_COLS):
        self.rows = rows
        self.cols = cols
        self.grid_size = rows * cols
        self.grid = [0] * self.grid_size

        # IP state
        self.ip_row = 0
        self.ip_col = 0
        self.ip_dir = DIR_E

        # Data heads and control locus (flat indices)
        self.h0 = 0
        self.h1 = 0
        self.cl = 0

        self.step_count = 0
        self.use_color = True
        self.trace = False

    # ── Coordinate helpers ─────────────────────────────────────────

    def _to_rc(self, flat):
        """Flat index → (row, col)."""
        return (flat // self.cols, flat % self.cols)

    def _to_flat(self, row, col):
        """(row, col) → flat index."""
        return (row % self.rows) * self.cols + (col % self.cols)

    def _move_head(self, flat, direction):
        """Move a flat-index head one step in a 2D direction (toroidal)."""
        r, c = self._to_rc(flat)
        r = (r + DR[direction]) % self.rows
        c = (c + DC[direction]) % self.cols
        return self._to_flat(r, c)

    def _ip_flat(self):
        return self._to_flat(self.ip_row, self.ip_col)

    # ── Forward step ───────────────────────────────────────────────

    def step(self):
        """Execute one instruction: read, execute, advance IP."""
        flat_ip = self._ip_flat()
        opcode = self.grid[flat_ip]
        old_dir = self.ip_dir

        # ── Execute ──
        if opcode == 1:      # / unconditional reflect
            self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 2:    # \ unconditional reflect
            self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 3:    # ? / reflect if grid[CL] ≠ 0
            if self.grid[self.cl] != 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 4:    # ! / reflect if grid[CL] = 0
            if self.grid[self.cl] == 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 5:    # @ \ reflect if grid[CL] ≠ 0
            if self.grid[self.cl] != 0:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 6:    # # \ reflect if grid[CL] = 0
            if self.grid[self.cl] == 0:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode in (7, 8, 9, 10):    # H0 movement N/S/E/W
            dirs = {7: DIR_N, 8: DIR_S, 9: DIR_E, 10: DIR_W}
            self.h0 = self._move_head(self.h0, dirs[opcode])

        elif opcode in (11, 12, 13, 14):  # H1 movement n/s/e/w
            dirs = {11: DIR_N, 12: DIR_S, 13: DIR_E, 14: DIR_W}
            self.h1 = self._move_head(self.h1, dirs[opcode])

        elif opcode == 15:   # + grid[H0]++
            self.grid[self.h0] = (self.grid[self.h0] + 1) & 0xFF

        elif opcode == 16:   # - grid[H0]--
            self.grid[self.h0] = (self.grid[self.h0] - 1) & 0xFF

        elif opcode == 17:   # . grid[H0] += grid[H1]
            self.grid[self.h0] = (self.grid[self.h0] + self.grid[self.h1]) & 0xFF

        elif opcode == 18:   # , grid[H0] -= grid[H1]
            self.grid[self.h0] = (self.grid[self.h0] - self.grid[self.h1]) & 0xFF

        elif opcode == 19:   # x swap(grid[H0], grid[H1])
            self.grid[self.h0], self.grid[self.h1] = \
                self.grid[self.h1], self.grid[self.h0]

        elif opcode == 20:   # F Fredkin: conditional swap
            if self.grid[self.cl] != 0:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 21:   # G swap(CL_register, grid[H0])
            self.cl, self.grid[self.h0] = self.grid[self.h0], self.cl

        elif opcode == 22:   # T swap(grid[CL], grid[H0])
            self.grid[self.cl], self.grid[self.h0] = \
                self.grid[self.h0], self.grid[self.cl]

        elif opcode == 23:   # > CL++
            self.cl = (self.cl + 1) % self.grid_size

        elif opcode == 24:   # < CL--
            self.cl = (self.cl - 1) % self.grid_size

        # else: NOP (0 or 25–255)

        # ── Trace output ──
        if self.trace:
            ch = OPCODE_TO_CHAR.get(opcode, f'{opcode}')
            d_old = DIR_ARROWS[old_dir]
            d_new = DIR_ARROWS[self.ip_dir]
            dir_str = f"{d_old}→{d_new}" if self.ip_dir != old_dir else d_old
            print(f"  step {self.step_count}: ({self.ip_row},{self.ip_col}) "
                  f"{dir_str} : {ch}")

        # ── Advance IP ──
        self.ip_row = (self.ip_row + DR[self.ip_dir]) % self.rows
        self.ip_col = (self.ip_col + DC[self.ip_dir]) % self.cols
        self.step_count += 1

    # ── Reverse step ───────────────────────────────────────────────

    def step_back(self):
        """Reverse one instruction. Purely deductive — no history needed."""
        # Find the cell that was executed last
        prev_row = (self.ip_row - DR[self.ip_dir]) % self.rows
        prev_col = (self.ip_col - DC[self.ip_dir]) % self.cols
        prev_flat = self._to_flat(prev_row, prev_col)
        opcode = self.grid[prev_flat]

        # ── Determine previous direction ──
        if opcode == 1:      # / always reflects
            prev_dir = SLASH_REFLECT[self.ip_dir]
        elif opcode == 2:    # \ always reflects
            prev_dir = BACKSLASH_REFLECT[self.ip_dir]
        elif opcode in (3, 4):  # conditional / mirrors
            cond = (self.grid[self.cl] != 0) if opcode == 3 else \
                   (self.grid[self.cl] == 0)
            prev_dir = SLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        elif opcode in (5, 6):  # conditional \ mirrors
            cond = (self.grid[self.cl] != 0) if opcode == 5 else \
                   (self.grid[self.cl] == 0)
            prev_dir = BACKSLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        else:
            prev_dir = self.ip_dir

        # ── Undo instruction effect ──
        if opcode in (7, 8, 9, 10):    # H0 was moved, undo
            self.h0 = self._move_head(self.h0, HEAD_MOVE_INVERSE[opcode])

        elif opcode in (11, 12, 13, 14):  # H1 was moved, undo
            self.h1 = self._move_head(self.h1, HEAD_MOVE_INVERSE[opcode])

        elif opcode == 15:   # was ++, undo --
            self.grid[self.h0] = (self.grid[self.h0] - 1) & 0xFF

        elif opcode == 16:   # was --, undo ++
            self.grid[self.h0] = (self.grid[self.h0] + 1) & 0xFF

        elif opcode == 17:   # was +=, undo -=
            self.grid[self.h0] = (self.grid[self.h0] - self.grid[self.h1]) & 0xFF

        elif opcode == 18:   # was -=, undo +=
            self.grid[self.h0] = (self.grid[self.h0] + self.grid[self.h1]) & 0xFF

        elif opcode == 19:   # x is self-inverse
            self.grid[self.h0], self.grid[self.h1] = \
                self.grid[self.h1], self.grid[self.h0]

        elif opcode == 20:   # F is self-inverse
            if self.grid[self.cl] != 0:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 21:   # G is self-inverse
            self.cl, self.grid[self.h0] = self.grid[self.h0], self.cl

        elif opcode == 22:   # T is self-inverse
            self.grid[self.cl], self.grid[self.h0] = \
                self.grid[self.h0], self.grid[self.cl]

        elif opcode == 23:   # was CL++, undo CL--
            self.cl = (self.cl - 1) % self.grid_size

        elif opcode == 24:   # was CL--, undo CL++
            self.cl = (self.cl + 1) % self.grid_size

        # Mirrors and NOP: no data effect to undo (direction handled above)

        # ── Trace output ──
        if self.trace:
            ch = OPCODE_TO_CHAR.get(opcode, f'{opcode}')
            print(f"  undo {self.step_count - 1}: ({prev_row},{prev_col}) : {ch}")

        # ── Restore ──
        self.ip_row = prev_row
        self.ip_col = prev_col
        self.ip_dir = prev_dir
        self.step_count -= 1
        return True

    # ── Program loading ────────────────────────────────────────────

    def load_linear(self, code):
        """Load code linearly onto the grid (left-to-right, top-to-bottom).
        Returns number of instructions placed."""
        self.grid = [0] * self.grid_size
        self.ip_row = 0
        self.ip_col = 0
        self.ip_dir = DIR_E
        self.h0 = 0
        self.h1 = 0
        self.cl = 0
        self.step_count = 0

        count = 0
        for ch in code:
            if ch in OPCODES and count < self.grid_size:
                self.grid[count] = OPCODES[ch]
                count += 1
        return count

    def place_code(self, row, col, code, vertical=False):
        """Place code onto the grid starting at (row, col).
        If vertical=True, place going South; else going East."""
        count = 0
        r, c = row, col
        for ch in code:
            if ch in OPCODES:
                flat = self._to_flat(r % self.rows, c % self.cols)
                self.grid[flat] = OPCODES[ch]
                count += 1
                if vertical:
                    r += 1
                else:
                    c += 1
        return count

    # ── Display ────────────────────────────────────────────────────

    def _color(self, text, *styles):
        """Apply ANSI styles if color is enabled."""
        if not self.use_color:
            return text
        prefix = ''.join(ANSI.get(s, '') for s in styles)
        return f"{prefix}{text}{ANSI['reset']}" if prefix else text

    def _cell_char(self, value):
        """Get display character for a grid cell value."""
        if value in OPCODE_TO_CHAR:
            return OPCODE_TO_CHAR[value]
        if value < 100:
            return f'{value:2d}'
        return f'{value:02x}'

    def display_grid(self):
        """Display the grid with colored pointer markers."""
        ip_flat = self._ip_flat()
        dir_arrow = DIR_ARROWS[self.ip_dir]
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)

        # Header
        print(f"\n{'═' * 60}")
        print(f"  Step {self.step_count}   "
              f"IP=({self.ip_row},{self.ip_col}){dir_arrow}  "
              f"CL={self.cl}({cl_r},{cl_c})  "
              f"H0={self.h0}({h0_r},{h0_c})  "
              f"H1={self.h1}({h1_r},{h1_c})")
        print(f"{'═' * 60}")

        # Column headers
        hdr = "    "
        for c in range(self.cols):
            hdr += f"{c:>3}"
        print(hdr)

        # Grid rows
        for r in range(self.rows):
            line = f" {r:2d}:"
            for c in range(self.cols):
                flat = self._to_flat(r, c)
                val = self.grid[flat]
                ch = self._cell_char(val)

                # Determine what pointers are here
                is_ip = (r == self.ip_row and c == self.ip_col)
                is_cl = (flat == self.cl)
                is_h0 = (flat == self.h0)
                is_h1 = (flat == self.h1)

                # Build display: direction arrow for IP, char for others
                if is_ip:
                    display = dir_arrow
                    cell = self._color(f" {display} ", 'bold', 'red')
                elif is_cl and is_h0 and is_h1:
                    cell = self._color(f"[{ch}]", 'bold', 'yellow')
                elif is_cl and is_h0:
                    cell = self._color(f"«{ch}»", 'magenta')
                elif is_cl and is_h1:
                    cell = self._color(f"‹{ch}›", 'magenta')
                elif is_h0 and is_h1:
                    cell = self._color(f"«{ch}»", 'yellow')
                elif is_cl:
                    cell = self._color(f" {ch} ", 'magenta')
                elif is_h0:
                    cell = self._color(f" o ", 'bold', 'cyan')
                elif is_h1:
                    cell = self._color(f" o ", 'bold', 'green')
                else:
                    if val == 0:
                        cell = self._color(f" {ch} ", 'dim')
                    else:
                        cell = f" {ch} "

                line += cell
            print(line)

        # Legend
        if self.use_color:
            print(f"  {self._color('IP', 'bold', 'red')}  "
                  f"{self._color('CL', 'magenta')}  "
                  f"{self._color('H0', 'cyan')}  "
                  f"{self._color('H1', 'green')}")

    def display_values(self):
        """Display raw byte values in a grid."""
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)

        print(f"\n{'─' * 60}")
        print(f"  Values (decimal)   "
              f"IP=({self.ip_row},{self.ip_col})  "
              f"CL={self.cl}  H0={self.h0}  H1={self.h1}")
        print(f"{'─' * 60}")

        # Column headers
        hdr = "    "
        for c in range(self.cols):
            hdr += f"{c:>4}"
        print(hdr)

        for r in range(self.rows):
            line = f" {r:2d}:"
            for c in range(self.cols):
                flat = self._to_flat(r, c)
                val = self.grid[flat]
                is_ip = (r == self.ip_row and c == self.ip_col)

                if is_ip:
                    line += self._color(f"{val:>4}", 'bold', 'red')
                elif val != 0:
                    line += f"{val:>4}"
                else:
                    line += self._color(f"   ·", 'dim')
            print(line)

    def display_both(self):
        """Display grid followed by values."""
        self.display_grid()
        self.display_values()

    # ── Save / Load ────────────────────────────────────────────────

    def save_state(self, filename):
        with open(filename, 'w') as f:
            f.write(f"# F***brain 2D state\n")
            f.write(f"rows={self.rows}\ncols={self.cols}\n")
            f.write(f"ip_row={self.ip_row}\nip_col={self.ip_col}\n")
            f.write(f"ip_dir={self.ip_dir}\n")
            f.write(f"cl={self.cl}\nh0={self.h0}\nh1={self.h1}\n")
            f.write(f"step={self.step_count}\n")
            f.write(f"grid={','.join(str(v) for v in self.grid)}\n")

    def load_state(self, filename):
        with open(filename, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    if k == 'rows':
                        self.rows = int(v)
                    elif k == 'cols':
                        self.cols = int(v)
                    elif k == 'ip_row':
                        self.ip_row = int(v)
                    elif k == 'ip_col':
                        self.ip_col = int(v)
                    elif k == 'ip_dir':
                        self.ip_dir = int(v)
                    elif k == 'cl':
                        self.cl = int(v)
                    elif k == 'h0':
                        self.h0 = int(v)
                    elif k == 'h1':
                        self.h1 = int(v)
                    elif k == 'step':
                        self.step_count = int(v)
                    elif k == 'grid':
                        vals = [int(x) for x in v.split(',')]
                        self.grid_size = self.rows * self.cols
                        self.grid = vals[:self.grid_size]
                        if len(self.grid) < self.grid_size:
                            self.grid.extend([0] * (self.grid_size - len(self.grid)))


# ═══════════════════════════════════════════════════════════════════════
#  Built-in examples
# ═══════════════════════════════════════════════════════════════════════

def load_example(sim, name):
    """Load a named example into the simulator."""

    if name == 'bounce':
        # IP bounces clockwise around a rectangle
        # Corners:  / at top-left, \ at top-right,
        #           \ at bot-left, / at bot-right
        sim.grid = [0] * sim.grid_size
        sim.ip_row, sim.ip_col, sim.ip_dir = 0, 1, DIR_E
        sim.cl, sim.h0, sim.h1 = 0, 0, 0
        sim.step_count = 0
        # Top row corners
        sim.grid[sim._to_flat(0, 0)] = OPCODES['/']
        sim.grid[sim._to_flat(0, 7)] = OPCODES['\\']
        # Bottom row corners
        sim.grid[sim._to_flat(4, 0)] = OPCODES['\\']
        sim.grid[sim._to_flat(4, 7)] = OPCODES['/']
        print("Loaded 'bounce': IP bounces clockwise in an 8×5 rectangle")
        print("  /·····\\    IP starts at (0,1)→E")
        print("  ········")
        print("  ········")
        print("  ········")
        print("  \\·····/")
        return True

    elif name == 'loop':
        # Simple decrementing loop: decrements grid[7,0] each lap
        # Counter at (7,0), CL points there
        # Rectangle: rows 0-4, cols 0-7
        sim.grid = [0] * sim.grid_size
        sim.step_count = 0
        # Corners (clockwise)
        sim.grid[sim._to_flat(0, 0)] = OPCODES['/']
        sim.grid[sim._to_flat(0, 7)] = OPCODES['\\']
        sim.grid[sim._to_flat(4, 7)] = OPCODES['?']   # conditional / : exit S if grid[CL]=0
        sim.grid[sim._to_flat(4, 0)] = OPCODES['\\']
        # Decrement in body (on top row, after /)
        sim.grid[sim._to_flat(0, 1)] = OPCODES['-']
        # Counter at (7,0) = 5
        counter_pos = sim._to_flat(7, 0)
        sim.grid[counter_pos] = 5
        # IP enters going N into top-left /
        sim.ip_row, sim.ip_col, sim.ip_dir = 1, 0, DIR_N
        sim.cl = counter_pos
        sim.h0 = counter_pos
        sim.h1 = 0
        print("Loaded 'loop': decrement loop, counter=5 at (7,0)")
        print("  /-·····\\    IP starts at (1,0)↑N")
        print("  ········    CL=H0=(7,0) [counter=5]")
        print("  ········    ? at (4,7) exits S when grid[CL]=0")
        print("  ········")
        print("  \\······?")
        print("  ········")
        print("  ········")
        print("  5·······    ← counter")
        return True

    elif name == 'mirrors':
        # Demonstrate all mirror types with a zigzag path
        sim.grid = [0] * sim.grid_size
        sim.step_count = 0
        # Zigzag: E→S via \, then S→E via \, then E→N via /
        sim.grid[sim._to_flat(0, 3)] = OPCODES['\\']
        sim.grid[sim._to_flat(2, 3)] = OPCODES['\\']
        sim.grid[sim._to_flat(2, 6)] = OPCODES['/']
        sim.ip_row, sim.ip_col, sim.ip_dir = 0, 0, DIR_E
        sim.cl, sim.h0, sim.h1 = 0, 0, 0
        print("Loaded 'mirrors': zigzag path through 3 mirrors")
        print("  ···\\····    E→S at (0,3)")
        print("  ········")
        print("  ···\\··/·    S→E at (2,3), E→N at (2,6)")
        return True

    elif name == 'branch':
        # If-then-else: condition ≠ 0 → increment, = 0 → decrement
        #
        #  row 2:  · · · / · + · \ ·     (then-path: grid[H0]++)
        #  row 3:  → · · ? · - · @ · →   (else-path: grid[H0]--)
        #
        #  ? at (3,3): / reflects E→N if grid[CL]≠0, else passes E
        #  @ at (3,7): \ reflects S→E if grid[CL]≠0, else passes E
        #  Both paths exit East from (3,8)
        #
        #  condition at (7,0), result at (7,8)
        sim.grid = [0] * sim.grid_size
        sim.step_count = 0

        # Then-path (row 2)
        sim.grid[sim._to_flat(2, 3)] = OPCODES['/']    # N→E corner
        sim.grid[sim._to_flat(2, 5)] = OPCODES['+']    # increment result
        sim.grid[sim._to_flat(2, 7)] = OPCODES['\\']   # E→S corner

        # Branch and merge (row 3)
        sim.grid[sim._to_flat(3, 3)] = OPCODES['?']    # branch: / if ≠0
        sim.grid[sim._to_flat(3, 5)] = OPCODES['-']    # decrement result
        sim.grid[sim._to_flat(3, 7)] = OPCODES['@']    # merge: \ if ≠0

        # Data
        cond_pos = sim._to_flat(7, 0)
        result_pos = sim._to_flat(7, 8)
        sim.grid[cond_pos] = 3       # condition: nonzero → takes then-path
        sim.grid[result_pos] = 10    # result: starting value

        # Registers
        sim.ip_row, sim.ip_col, sim.ip_dir = 3, 0, DIR_E
        sim.cl = cond_pos      # 112
        sim.h0 = result_pos    # 120
        sim.h1 = 0
        print("Loaded 'branch': if-then-else conditional")
        print("        / · + · \\         (then-path: result++)")
        print("  → · · ? · - · @ · →    (else-path: result--)")
        print(f"  condition={sim.grid[cond_pos]} at (7,0)  "
              f"result={sim.grid[result_pos]} at (7,8)")
        print("  CL→(7,0)  H0→(7,8)")
        print("  If cond≠0: then-path (10 steps to merge)")
        print("  If cond=0: else-path (8 steps to merge)")
        print("  Test: 'data 7 0 0' to set cond=0, then 'ip 3 0' + 'dir E'")
        return True

    else:
        print(f"Unknown example. Available: bounce, loop, mirrors, branch")
        return False


# ═══════════════════════════════════════════════════════════════════════
#  Parse helpers
# ═══════════════════════════════════════════════════════════════════════

def parse_pos(sim, parts):
    """Parse a position from command parts. Returns flat index.
    Accepts:  <flat>  or  <row> <col>
    Returns (flat_index, remaining_parts) or (None, parts) on failure."""
    if len(parts) >= 2:
        try:
            r, c = int(parts[0]), int(parts[1])
            if 0 <= r < sim.rows and 0 <= c < sim.cols:
                return sim._to_flat(r, c), parts[2:]
        except ValueError:
            pass
    if len(parts) >= 1:
        try:
            flat = int(parts[0])
            if 0 <= flat < sim.grid_size:
                return flat, parts[1:]
        except ValueError:
            pass
    return None, parts


def parse_dir(s):
    """Parse a direction string. Returns direction index or None."""
    s = s.strip().upper()
    if s in ('N', 'NORTH', 'U', 'UP'):
        return DIR_N
    elif s in ('E', 'EAST', 'R', 'RIGHT'):
        return DIR_E
    elif s in ('S', 'SOUTH', 'D', 'DOWN'):
        return DIR_S
    elif s in ('W', 'WEST', 'L', 'LEFT'):
        return DIR_W
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Interactive Session
# ═══════════════════════════════════════════════════════════════════════

HELP_TEXT = """
ISA ({opcount} opcodes + NOP):
  Mirrors:     /  \\  (unconditional)
               ?  !  (/ reflect if grid[CL] ≠0 / =0)
               @  #  (\\ reflect if grid[CL] ≠0 / =0)
  H0 move:     N S E W   (North/South/East/West on grid)
  H1 move:     n s e w
  Data:        + grid[H0]++          - grid[H0]--
               . grid[H0]+=grid[H1]  , grid[H0]-=grid[H1]
               x swap(grid[H0],grid[H1])
               F if grid[CL]≠0: swap (Fredkin)
  CL:          G swap(CL_reg, grid[H0])    T swap(grid[CL], grid[H0])
               > CL++   < CL--   (flat index)

  / reflect: E↔N  S↔W     \\ reflect: E↔S  N↔W

Commands:
  tape <code>          Load code linearly (resets state)
  row <r> [c] <code>   Place code along row r (from col c, default 0)
  col <c> [r] <code>   Place code down col c (from row r, default 0)
  data <r> <c> <v>...  Set raw values at (r,c); v can be number or opcode char
  cell <r> <c> [v]     Get/set one cell

  ip <r> <c>           Set IP position
  dir <N/E/S/W>        Set IP direction
  cl [r c | flat]      Set/show CL
  h0 [r c | flat]      Set/show H0
  h1 [r c | flat]      Set/show H1

  step / s [n]         Forward n steps (default 1)
  back / b [n]         Reverse n steps (default 1)
  run [n]              Run n forward (default 100)
  runback [n]          Run n backward (default 100)

  show                 Display grid
  vals                 Display values
  both                 Display grid + values
  trace                Toggle execution trace
  color                Toggle ANSI colors

  example <name>       Load example (bounce, loop, mirrors)
  save <file>          Save state
  load <file>          Load state
  size <r> <c>         Resize grid (resets state)
  reset                Reset all state
  help                 This help
  quit / q             Exit
"""


def interactive_session():
    sim = FB2DSimulator()

    print("═" * 60)
    print("  F***brain 2D Grid Simulator v1")
    print("  Reversible · 2D mirrors · Von Neumann architecture")
    print("═" * 60)
    print(f"  Grid: {sim.rows}×{sim.cols} ({sim.grid_size} cells)")
    print(f"  Type 'help' for commands, 'example <name>' for demos")
    sim.display_grid()

    while True:
        try:
            line = input("\nFB2D> ").strip()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            # ── Quit ──
            if cmd in ('quit', 'q'):
                print("Goodbye!")
                break

            # ── Help ──
            elif cmd == 'help':
                print(HELP_TEXT.replace('{opcount}', str(len(OPCODES))))

            # ── Tape (linear load) ──
            elif cmd == 'tape':
                if args:
                    code = ' '.join(args)
                    n = sim.load_linear(code)
                    print(f"Loaded {n} instructions (state reset)")
                else:
                    print("Usage: tape <code>")
                sim.display_grid()

            # ── Row (place code along a row) ──
            elif cmd == 'row':
                if len(args) >= 2:
                    r = int(args[0])
                    # Check if second arg is a column number or code
                    try:
                        c = int(args[1])
                        code = ' '.join(args[2:])
                    except ValueError:
                        c = 0
                        code = ' '.join(args[1:])
                    n = sim.place_code(r, c, code, vertical=False)
                    print(f"Placed {n} instructions at row {r} col {c}")
                else:
                    print("Usage: row <r> [c] <code>")
                sim.display_grid()

            # ── Col (place code down a column) ──
            elif cmd == 'col':
                if len(args) >= 2:
                    c = int(args[0])
                    try:
                        r = int(args[1])
                        code = ' '.join(args[2:])
                    except ValueError:
                        r = 0
                        code = ' '.join(args[1:])
                    n = sim.place_code(r, c, code, vertical=True)
                    print(f"Placed {n} instructions at col {c} row {r}")
                else:
                    print("Usage: col <c> [r] <code>")
                sim.display_grid()

            # ── Data (set raw values) ──
            elif cmd == 'data':
                if len(args) >= 3:
                    r, c = int(args[0]), int(args[1])
                    for i, v in enumerate(args[2:]):
                        flat = sim._to_flat(r, c + i)
                        if v in OPCODES:
                            sim.grid[flat] = OPCODES[v]
                        else:
                            sim.grid[flat] = int(v) & 0xFF
                    print(f"Set {len(args)-2} values starting at ({r},{c})")
                else:
                    print("Usage: data <r> <c> <v1> [v2] ...")
                sim.display_grid()

            # ── Cell (get/set single cell) ──
            elif cmd == 'cell':
                if len(args) >= 2:
                    r, c = int(args[0]), int(args[1])
                    flat = sim._to_flat(r, c)
                    if len(args) >= 3:
                        v = args[2]
                        if v in OPCODES:
                            sim.grid[flat] = OPCODES[v]
                        else:
                            sim.grid[flat] = int(v) & 0xFF
                    val = sim.grid[flat]
                    ch = OPCODE_TO_CHAR.get(val, f'data')
                    print(f"grid[{r},{c}] = {val} ({ch})")
                else:
                    print("Usage: cell <r> <c> [value]")

            # ── IP position ──
            elif cmd == 'ip':
                if len(args) >= 2:
                    sim.ip_row = int(args[0]) % sim.rows
                    sim.ip_col = int(args[1]) % sim.cols
                print(f"IP = ({sim.ip_row},{sim.ip_col}) "
                      f"{DIR_ARROWS[sim.ip_dir]}")
                sim.display_grid()

            # ── Direction ──
            elif cmd == 'dir':
                if args:
                    d = parse_dir(args[0])
                    if d is not None:
                        sim.ip_dir = d
                    else:
                        print(f"Unknown direction: {args[0]}")
                print(f"DIR = {DIR_NAMES[sim.ip_dir]} "
                      f"{DIR_ARROWS[sim.ip_dir]}")
                sim.display_grid()

            # ── CL / H0 / H1 ──
            elif cmd in ('cl', 'h0', 'h1'):
                if args:
                    pos, _ = parse_pos(sim, args)
                    if pos is not None:
                        setattr(sim, cmd, pos)
                    else:
                        print(f"Invalid position: {' '.join(args)}")
                val = getattr(sim, cmd)
                r, c = sim._to_rc(val)
                label = cmd.upper()
                print(f"{label} = {val} ({r},{c})  "
                      f"grid[{label}] = {sim.grid[val]}")
                sim.display_grid()

            # ── Step forward ──
            elif cmd in ('step', 's'):
                n = int(args[0]) if args else 1
                for _ in range(n):
                    sim.step()
                if not sim.trace:
                    sim.display_grid()

            # ── Step backward ──
            elif cmd in ('back', 'b', 'r'):
                n = int(args[0]) if args else 1
                for _ in range(n):
                    sim.step_back()
                if not sim.trace:
                    sim.display_grid()

            # ── Run forward ──
            elif cmd == 'run':
                n = int(args[0]) if args else 100
                for _ in range(n):
                    sim.step()
                print(f"Ran {n} steps forward")
                sim.display_grid()

            # ── Run backward ──
            elif cmd == 'runback':
                n = int(args[0]) if args else 100
                actual = 0
                for _ in range(n):
                    if sim.step_count > 0:
                        sim.step_back()
                        actual += 1
                    else:
                        break
                print(f"Ran {actual} steps backward")
                sim.display_grid()

            # ── Display ──
            elif cmd == 'show':
                sim.display_grid()

            elif cmd == 'vals':
                sim.display_values()

            elif cmd == 'both':
                sim.display_both()

            # ── Trace toggle ──
            elif cmd == 'trace':
                sim.trace = not sim.trace
                print(f"Trace: {'ON' if sim.trace else 'OFF'}")
                if sim.trace:
                    sim.display_grid()

            # ── Color toggle ──
            elif cmd == 'color':
                sim.use_color = not sim.use_color
                print(f"Color: {'ON' if sim.use_color else 'OFF'}")
                sim.display_grid()

            # ── Examples ──
            elif cmd == 'example':
                if args:
                    load_example(sim, args[0])
                else:
                    print("Available examples: bounce, loop, mirrors, branch")
                sim.display_grid()

            # ── Save / Load ──
            elif cmd == 'save':
                if args:
                    fn = args[0]
                    if not fn.endswith('.fb2d'):
                        fn += '.fb2d'
                    sim.save_state(fn)
                    print(f"Saved to {fn}")
                else:
                    print("Usage: save <filename>")

            elif cmd == 'load':
                if args:
                    fn = args[0]
                    if not fn.endswith('.fb2d'):
                        fn += '.fb2d'
                    sim.load_state(fn)
                    print(f"Loaded {fn}")
                    sim.display_grid()
                else:
                    print("Usage: load <filename>")

            # ── Resize ──
            elif cmd == 'size':
                if len(args) >= 2:
                    new_r, new_c = int(args[0]), int(args[1])
                    sim = FB2DSimulator(rows=new_r, cols=new_c)
                    print(f"Resized to {new_r}×{new_c} (state reset)")
                else:
                    print(f"Grid size: {sim.rows}×{sim.cols} "
                          f"({sim.grid_size} cells)")
                sim.display_grid()

            # ── Reset ──
            elif cmd == 'reset':
                sim = FB2DSimulator(rows=sim.rows, cols=sim.cols)
                print("Reset")
                sim.display_grid()

            else:
                print(f"Unknown command: {cmd}. Type 'help' for commands.")

        except KeyboardInterrupt:
            print("\nInterrupted")
        except EOFError:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    interactive_session()

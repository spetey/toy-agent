#!/usr/bin/env python3
"""
F***brain 2D Grid Simulator v1
Authored or modified by Claude
Version: 2026-02-17 v1.6 — bit-level ops (x=XOR, r/l rotate, f bit-Fredkin, z bit-GP-swap)
                            x→X rename for byte swap; load/save defaults to ./programs

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
  GP                 — garbage pointer (flat index) for reversible breadcrumb trails

Mirror reflection rules (standard optics):
  /  : (dr,dc) → (−dc,−dr)    E→N  N→E  W→S  S→W
  \\  : (dr,dc) → (dc,dr)      E→S  S→E  W→N  N→W

Conditional mirrors reflect OR pass through based on grid[CL].
GP-conditional mirrors reflect OR pass through based on grid[GP].
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
    '%':  3,   # / reflect if grid[CL] ≠ 0, else pass through
    '?':  4,   # / reflect if grid[CL] = 0, else pass through
    '&':  5,   # \ reflect if grid[CL] ≠ 0, else pass through
    '!':  6,   # \ reflect if grid[CL] = 0, else pass through
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
    'X':  19,  # swap([H0], [H1])  (byte swap — was 'x' before v1.6)
    'F':  20,  # if [CL]≠0: swap([H0], [H1])  (Fredkin gate)
    'G':  21,  # swap(H1_register, [H0])  (indirect H1)
    'T':  22,  # swap([CL], [H0])     (bridge)
    '>':  23,  # CL East  (col+1)
    '<':  24,  # CL West  (col-1)
    '^':  25,  # CL North (row-1)
    'v':  26,  # CL South (row+1)
    # Garbage pointer (GP) ops for reversible breadcrumb trails
    'P':  27,  # grid[GP]++  (leave breadcrumb)
    'Q':  28,  # grid[GP]--  (erase breadcrumb)
    ']':  29,  # GP East  (col+1)
    '[':  30,  # GP West  (col-1)
    '}':  31,  # GP South (row+1)
    '{':  32,  # GP North (row-1)
    # GP-conditional mirrors and CL/GP swap
    'K':  33,  # swap(CL_register, GP_register)
    '(':  34,  # \ reflect if grid[GP] ≠ 0, else pass through
    ')':  35,  # \ reflect if grid[GP] = 0, else pass through
    '#':  36,  # / reflect if grid[GP] = 0, else pass through
    '$':  37,  # / reflect if grid[GP] ≠ 0, else pass through
    # Data/GP swap for efficient variable zeroing
    'Z':  38,  # swap([H0], [GP])  (byte-level GP swap)
    # ── Bit-level operations (v1.6) ──
    'x':  39,  # [H0] ^= [H1]  (XOR-accumulate, self-inverse)
    'r':  40,  # [H0] rotate right 1 bit  (bit0→bit7, inverse: l)
    'l':  41,  # [H0] rotate left 1 bit   (bit7→bit0, inverse: r)
    'f':  42,  # if [CL]&1: swap([H0], [H1])  (bit-0 Fredkin, self-inverse)
    'z':  43,  # swap(bit0 of [H0], bit0 of [GP])  (bit-level GP swap, self-inverse)
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '·'   # NOP displayed as middle dot

# Inverse direction map for head movement (for step_back)
HEAD_MOVE_INVERSE = {
    7: DIR_S, 8: DIR_N, 9: DIR_W, 10: DIR_E,   # H0: N↔S, E↔W
    11: DIR_S, 12: DIR_N, 13: DIR_W, 14: DIR_E,  # H1: N↔S, E↔W
    29: DIR_W, 30: DIR_E, 31: DIR_N, 32: DIR_S,  # GP: ]↔[, }↔{
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
    'bg_blue': '\033[44m',
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
        self.gp = 0  # Garbage pointer for breadcrumb trails

        self.step_count = 0
        self.use_color = True
        self.trace = False

        # Clipboard for block editing
        self.selection = None   # (r1, c1, r2, c2) inclusive corners
        self.clipboard = None   # (width, height, [values]) — row-major

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

        elif opcode == 3:    # % / reflect if grid[CL] ≠ 0
            if self.grid[self.cl] != 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 4:    # ? / reflect if grid[CL] = 0
            if self.grid[self.cl] == 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 5:    # & \ reflect if grid[CL] ≠ 0
            if self.grid[self.cl] != 0:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 6:    # ! \ reflect if grid[CL] = 0
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

        elif opcode == 19:   # X swap([H0], [H1])
            self.grid[self.h0], self.grid[self.h1] = \
                self.grid[self.h1], self.grid[self.h0]

        elif opcode == 20:   # F Fredkin: if [CL]!=0: swap([H0], [H1])
            if self.grid[self.cl] != 0:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 21:   # G swap(H1_register, grid[H0])
            self.h1, self.grid[self.h0] = self.grid[self.h0], self.h1

        elif opcode == 22:   # T swap(grid[CL], grid[H0])
            self.grid[self.cl], self.grid[self.h0] = \
                self.grid[self.h0], self.grid[self.cl]

        elif opcode == 23:   # > CL East
            self.cl = self._move_head(self.cl, DIR_E)

        elif opcode == 24:   # < CL West
            self.cl = self._move_head(self.cl, DIR_W)

        elif opcode == 25:   # ^ CL North
            self.cl = self._move_head(self.cl, DIR_N)

        elif opcode == 26:   # v CL South
            self.cl = self._move_head(self.cl, DIR_S)

        # ── GP (garbage pointer) operations ──
        elif opcode == 27:   # P grid[GP]++
            self.grid[self.gp] = (self.grid[self.gp] + 1) & 0xFF

        elif opcode == 28:   # Q grid[GP]--
            self.grid[self.gp] = (self.grid[self.gp] - 1) & 0xFF

        elif opcode == 29:   # ] GP East
            self.gp = self._move_head(self.gp, DIR_E)

        elif opcode == 30:   # [ GP West
            self.gp = self._move_head(self.gp, DIR_W)

        elif opcode == 31:   # } GP South
            self.gp = self._move_head(self.gp, DIR_S)

        elif opcode == 32:   # { GP North
            self.gp = self._move_head(self.gp, DIR_N)

        # ── GP-conditional mirrors and CL/GP swap ──
        elif opcode == 33:   # K swap(CL_register, GP_register)
            self.cl, self.gp = self.gp, self.cl

        elif opcode == 34:   # ( \ reflect if grid[GP] ≠ 0
            if self.grid[self.gp] != 0:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 35:   # ) \ reflect if grid[GP] = 0
            if self.grid[self.gp] == 0:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 36:   # # / reflect if grid[GP] = 0
            if self.grid[self.gp] == 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 37:   # $ / reflect if grid[GP] ≠ 0
            if self.grid[self.gp] != 0:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 38:   # Z swap([H0], [GP])
            self.grid[self.h0], self.grid[self.gp] = \
                self.grid[self.gp], self.grid[self.h0]

        # ── Bit-level operations (v1.6) ──
        elif opcode == 39:   # x  [H0] ^= [H1]  (XOR, self-inverse)
            self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.h1]

        elif opcode == 40:   # r  [H0] rotate right 1 bit
            v = self.grid[self.h0]
            self.grid[self.h0] = ((v >> 1) | ((v & 1) << 7)) & 0xFF

        elif opcode == 41:   # l  [H0] rotate left 1 bit
            v = self.grid[self.h0]
            self.grid[self.h0] = (((v << 1) & 0xFF) | (v >> 7)) & 0xFF

        elif opcode == 42:   # f  if [CL]&1: swap([H0], [H1])  (bit-0 Fredkin)
            if self.grid[self.cl] & 1:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 43:   # z  swap bit0 of [H0] with bit0 of [GP]
            a = self.grid[self.h0]
            b = self.grid[self.gp]
            a_bit = a & 1
            b_bit = b & 1
            self.grid[self.h0] = (a & 0xFE) | b_bit
            self.grid[self.gp] = (b & 0xFE) | a_bit

        # else: NOP (0 or 44–255)

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
        elif opcode in (34, 35):  # conditional \ mirrors on grid[GP]
            cond = (self.grid[self.gp] != 0) if opcode == 34 else \
                   (self.grid[self.gp] == 0)
            prev_dir = BACKSLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        elif opcode in (36, 37):  # conditional / mirrors on grid[GP]
            cond = (self.grid[self.gp] == 0) if opcode == 36 else \
                   (self.grid[self.gp] != 0)
            prev_dir = SLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
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
            self.h1, self.grid[self.h0] = self.grid[self.h0], self.h1

        elif opcode == 22:   # T is self-inverse
            self.grid[self.cl], self.grid[self.h0] = \
                self.grid[self.h0], self.grid[self.cl]

        elif opcode == 23:   # was CL East, undo West
            self.cl = self._move_head(self.cl, DIR_W)

        elif opcode == 24:   # was CL West, undo East
            self.cl = self._move_head(self.cl, DIR_E)

        elif opcode == 25:   # was CL North, undo South
            self.cl = self._move_head(self.cl, DIR_S)

        elif opcode == 26:   # was CL South, undo North
            self.cl = self._move_head(self.cl, DIR_N)

        # ── GP (garbage pointer) undo operations ──
        elif opcode == 27:   # P was ++, undo --
            self.grid[self.gp] = (self.grid[self.gp] - 1) & 0xFF

        elif opcode == 28:   # Q was --, undo ++
            self.grid[self.gp] = (self.grid[self.gp] + 1) & 0xFF

        elif opcode in (29, 30, 31, 32):  # GP movement, undo
            self.gp = self._move_head(self.gp, HEAD_MOVE_INVERSE[opcode])

        elif opcode == 33:   # K is self-inverse
            self.cl, self.gp = self.gp, self.cl

        elif opcode == 38:   # Z is self-inverse
            self.grid[self.h0], self.grid[self.gp] = \
                self.grid[self.gp], self.grid[self.h0]

        # ── Bit-level undo (v1.6) ──
        elif opcode == 39:   # x XOR is self-inverse
            self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.h1]

        elif opcode == 40:   # r was rotate-right, undo with rotate-left
            v = self.grid[self.h0]
            self.grid[self.h0] = (((v << 1) & 0xFF) | (v >> 7)) & 0xFF

        elif opcode == 41:   # l was rotate-left, undo with rotate-right
            v = self.grid[self.h0]
            self.grid[self.h0] = ((v >> 1) | ((v & 1) << 7)) & 0xFF

        elif opcode == 42:   # f bit-0 Fredkin is self-inverse
            if self.grid[self.cl] & 1:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 43:   # z bit-0 GP swap is self-inverse
            a = self.grid[self.h0]
            b = self.grid[self.gp]
            a_bit = a & 1
            b_bit = b & 1
            self.grid[self.h0] = (a & 0xFE) | b_bit
            self.grid[self.gp] = (b & 0xFE) | a_bit

        # Mirrors (incl 34–37) and NOP: no data effect to undo (direction handled above)

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
        self.gp = 0
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
        """Get display character for a grid cell value (always 1 char)."""
        if value in OPCODE_TO_CHAR:
            return OPCODE_TO_CHAR[value]
        if value < 100:
            return f'{value:2d}'
        return f'{value:02x}'

    def _cell_display(self, ch):
        """Pad cell char to exactly 3 characters for grid alignment."""
        if len(ch) == 1:
            return f" {ch} "
        elif len(ch) == 2:
            return f" {ch}"
        return ch[:3]

    def display_grid(self):
        """Display the grid with colored pointer markers."""
        ip_flat = self._ip_flat()
        dir_arrow = DIR_ARROWS[self.ip_dir]
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)
        gp_r, gp_c = self._to_rc(self.gp)

        # Header
        print(f"\n{'═' * 70}")
        print(f"  Step {self.step_count}   "
              f"IP=({self.ip_row},{self.ip_col}){dir_arrow}  "
              f"CL={self.cl}({cl_r},{cl_c})  "
              f"H0={self.h0}({h0_r},{h0_c})  "
              f"H1={self.h1}({h1_r},{h1_c})  "
              f"GP={self.gp}({gp_r},{gp_c})")
        print(f"{'═' * 70}")

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
                is_gp = (flat == self.gp)
                is_sel = (self.selection is not None and
                          self.selection[0] <= r <= self.selection[2] and
                          self.selection[1] <= c <= self.selection[3])

                # Build display: direction arrow for IP, char for others
                # _cell_display ensures exactly 3 chars for alignment
                pad = self._cell_display(ch)
                if is_ip:
                    cell = self._color(f" {dir_arrow} ", 'bold', 'red')
                elif is_gp and is_cl and is_h0:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'bold', 'yellow')
                elif is_gp and is_cl:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'magenta')
                elif is_gp and is_h0:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'cyan')
                elif is_gp:
                    if val == 0:
                        cell = self._color(f" ○ ", 'yellow')
                    else:
                        cell = self._color(pad, 'yellow')
                elif is_cl and is_h0 and is_h1:
                    cell = self._color(f"[{ch}]" if len(ch) == 1 else f"[{ch}", 'bold', 'yellow')
                elif is_cl and is_h0:
                    cell = self._color(f"«{ch}»" if len(ch) == 1 else f"«{ch}", 'magenta')
                elif is_cl and is_h1:
                    cell = self._color(f"‹{ch}›" if len(ch) == 1 else f"‹{ch}", 'magenta')
                elif is_h0 and is_h1:
                    cell = self._color(f"«{ch}»" if len(ch) == 1 else f"«{ch}", 'yellow')
                elif is_cl:
                    cell = self._color(pad, 'magenta')
                elif is_h0:
                    if val == 0:
                        cell = self._color(f" o ", 'bold', 'cyan')
                    else:
                        cell = self._color(pad, 'bold', 'cyan')
                elif is_h1:
                    if val == 0:
                        cell = self._color(f" o ", 'bold', 'green')
                    else:
                        cell = self._color(pad, 'bold', 'green')
                else:
                    if val == 0:
                        if is_sel:
                            cell = self._color(pad, 'bg_blue', 'dim')
                        else:
                            cell = self._color(pad, 'dim')
                    else:
                        if is_sel:
                            cell = self._color(pad, 'bg_blue')
                        else:
                            cell = pad

                line += cell
            print(line)

        # Legend
        if self.use_color:
            print(f"  {self._color('IP', 'bold', 'red')}  "
                  f"{self._color('CL', 'magenta')}  "
                  f"{self._color('H0', 'cyan')}  "
                  f"{self._color('H1', 'green')}  "
                  f"{self._color('GP', 'yellow')}")

    def display_values(self):
        """Display raw byte values in a grid."""
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)
        gp_r, gp_c = self._to_rc(self.gp)

        print(f"\n{'─' * 70}")
        print(f"  Values (decimal)   "
              f"IP=({self.ip_row},{self.ip_col})  "
              f"CL={self.cl}  H0={self.h0}  H1={self.h1}  GP={self.gp}")
        print(f"{'─' * 70}")

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
            f.write(f"cl={self.cl}\nh0={self.h0}\nh1={self.h1}\ngp={self.gp}\n")
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
                    elif k == 'gp':
                        self.gp = int(v)
                    elif k == 'step':
                        self.step_count = int(v)
                    elif k == 'grid':
                        vals = [int(x) for x in v.split(',')]
                        self.grid_size = self.rows * self.cols
                        self.grid = vals[:self.grid_size]
                        if len(self.grid) < self.grid_size:
                            self.grid.extend([0] * (self.grid_size - len(self.grid)))

    # ── Block editing (select / copy / cut / paste) ──────────────────

    def select_rect(self, r1, c1, r2, c2):
        """Select a rectangle. Normalizes so r1<=r2, c1<=c2."""
        r1, r2 = min(r1, r2), max(r1, r2)
        c1, c2 = min(c1, c2), max(c1, c2)
        r1 = max(0, min(r1, self.rows - 1))
        r2 = max(0, min(r2, self.rows - 1))
        c1 = max(0, min(c1, self.cols - 1))
        c2 = max(0, min(c2, self.cols - 1))
        self.selection = (r1, c1, r2, c2)
        w = c2 - c1 + 1
        h = r2 - r1 + 1
        return (h, w)

    def copy_rect(self):
        """Copy selection to clipboard. Returns (height, width) or None."""
        if self.selection is None:
            return None
        r1, c1, r2, c2 = self.selection
        h = r2 - r1 + 1
        w = c2 - c1 + 1
        vals = []
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                vals.append(self.grid[self._to_flat(r, c)])
        self.clipboard = (w, h, vals)
        return (h, w)

    def cut_rect(self):
        """Copy selection to clipboard and zero the region."""
        result = self.copy_rect()
        if result is None:
            return None
        r1, c1, r2, c2 = self.selection
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                self.grid[self._to_flat(r, c)] = 0
        return result

    def paste_rect(self, dest_r, dest_c):
        """Paste clipboard at (dest_r, dest_c) as upper-left corner.
        Wraps toroidally. Returns (height, width) or None."""
        if self.clipboard is None:
            return None
        w, h, vals = self.clipboard
        idx = 0
        for dr in range(h):
            for dc in range(w):
                r = (dest_r + dr) % self.rows
                c = (dest_c + dc) % self.cols
                self.grid[self._to_flat(r, c)] = vals[idx]
                idx += 1
        return (h, w)


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
        sim.cl, sim.h0, sim.h1, sim.gp = 0, 0, 0, 0
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
        sim.grid[sim._to_flat(4, 7)] = OPCODES['%']   # conditional / : exit S if grid[CL]=0
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
        sim.gp = 0
        print("Loaded 'loop': decrement loop, counter=5 at (7,0)")
        print("  /-·····\\    IP starts at (1,0)↑N")
        print("  ········    CL=H0=(7,0) [counter=5]")
        print("  ········    % at (4,7) exits S when grid[CL]=0")
        print("  ········")
        print("  \\······%")
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
        sim.cl, sim.h0, sim.h1, sim.gp = 0, 0, 0, 0
        print("Loaded 'mirrors': zigzag path through 3 mirrors")
        print("  ···\\····    E→S at (0,3)")
        print("  ········")
        print("  ···\\··/·    S→E at (2,3), E→N at (2,6)")
        return True

    elif name == 'branch':
        # If-then-else: condition ≠ 0 → increment, = 0 → decrement
        #
        #  row 2:  · · · / · + · \ ·     (then-path: grid[H0]++)
        #  row 3:  → · · % · - · & · →   (else-path: grid[H0]--)
        #
        #  % at (3,3): / reflects E→N if grid[CL]≠0, else passes E
        #  & at (3,7): \ reflects S→E if grid[CL]≠0, else passes E
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
        sim.grid[sim._to_flat(3, 3)] = OPCODES['%']    # branch: / if ≠0
        sim.grid[sim._to_flat(3, 5)] = OPCODES['-']    # decrement result
        sim.grid[sim._to_flat(3, 7)] = OPCODES['&']    # merge: \ if ≠0

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
        sim.gp = 0
        print("Loaded 'branch': if-then-else conditional")
        print("        / · + · \\         (then-path: result++)")
        print("  → · · % · - · & · →    (else-path: result--)")
        print(f"  condition={sim.grid[cond_pos]} at (7,0)  "
              f"result={sim.grid[result_pos]} at (7,8)")
        print("  CL→(7,0)  H0→(7,8)")
        print("  If cond≠0: then-path (10 steps to merge)")
        print("  If cond=0: else-path (8 steps to merge)")
        print("  Test: 'data 7 0 0' to set cond=0, then 'ip 3 0' + 'dir E'")
        return True

    elif name == 'multiply':
        # Multiply a × b using repeated addition
        #
        # Loop rectangle (rows 0-4, cols 0-7), clockwise:
        #   / . S - N · · \       ← body: result+=a, H0→b, b--, H0→result
        #   ·             ·
        #   \       · · · %       ← % exits S when b=0
        #                 ↓
        # Data column (col 15):
        #   row 5: result=0 (H0)
        #   row 6: b=counter (CL)
        #   row 7: a=multiplicand (H1)
        sim.grid = [0] * sim.grid_size
        sim.step_count = 0

        # Loop corners
        sim.grid[sim._to_flat(0, 0)] = OPCODES['/']    # TL
        sim.grid[sim._to_flat(0, 7)] = OPCODES['\\']   # TR
        sim.grid[sim._to_flat(4, 0)] = OPCODES['\\']   # BL
        sim.grid[sim._to_flat(4, 7)] = OPCODES['%']    # BR conditional

        # Body on top row (IP goes East)
        sim.grid[sim._to_flat(0, 1)] = OPCODES['.']    # result += a
        sim.grid[sim._to_flat(0, 2)] = OPCODES['S']    # H0 south (to b)
        sim.grid[sim._to_flat(0, 3)] = OPCODES['-']    # b--
        sim.grid[sim._to_flat(0, 4)] = OPCODES['N']    # H0 north (to result)

        # Data in column 15
        result_pos = sim._to_flat(5, 15)
        b_pos = sim._to_flat(6, 15)
        a_pos = sim._to_flat(7, 15)

        a_val, b_val = 7, 5
        sim.grid[result_pos] = 0       # result starts at 0
        sim.grid[b_pos] = b_val        # loop counter
        sim.grid[a_pos] = a_val        # multiplicand

        # Registers
        sim.ip_row, sim.ip_col, sim.ip_dir = 1, 0, DIR_N
        sim.cl = b_pos       # (6,15)
        sim.h0 = result_pos  # (5,15)
        sim.h1 = a_pos       # (7,15)
        sim.gp = 0

        print(f"Loaded 'multiply': {a_val} × {b_val} via repeated addition")
        print("  / . S - N · · \\       (body: result+=a, H0→b, b--, H0→result)")
        print("  ·             ·")
        print("  \\       · · · %       (exit S when b=0)")
        print(f"  a={a_val} at (7,15)  b={b_val} at (6,15)  result=0 at (5,15)")
        print(f"  Expected result: {a_val * b_val}")
        print(f"  Laps: {b_val} × 22 steps + exit = ~{b_val * 22} steps")
        return True
        print(f"Unknown example. Available: bounce, loop, mirrors, branch, multiply")
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
  Mirrors:
    /  (1)  unconditional / reflect    \\  (2)  unconditional \\ reflect
    %  (3)  / if grid[CL]!=0           ?  (4)  / if grid[CL]=0
    &  (5)  \\ if grid[CL]!=0           !  (6)  \\ if grid[CL]=0
  H0 movement:
    N  (7)  North    S  (8)  South    E  (9)  East    W (10)  West
  H1 movement:
    n (11)  North    s (12)  South    e (13)  East    w (14)  West
  CL movement:
    ^ (25)  North    v (26)  South    > (23)  East    < (24)  West
  GP movement (garbage pointer):
    { (32)  North    } (31)  South    ] (29)  East    [ (30)  West
  Byte-level data:
    + (15)  [H0]++                     - (16)  [H0]--
    . (17)  [H0] += [H1]              , (18)  [H0] -= [H1]
    X (19)  swap([H0], [H1])           F (20)  if [CL]!=0: swap([H0],[H1])
  Bit-level data (v1.6):
    x (39)  [H0] ^= [H1]  (XOR, self-inverse)
    r (40)  [H0] rotate right 1 bit   l (41)  [H0] rotate left 1 bit
    f (42)  if [CL]&1: swap([H0],[H1]) (bit-0 Fredkin)
    z (43)  swap(bit0 [H0], bit0 [GP]) (bit-level GP swap)
  CL data:
    G (21)  swap(H1_reg, [H0])         T (22)  swap([CL], [H0])
  GP data (breadcrumbs):
    P (27)  [GP]++  (leave breadcrumb)
    Q (28)  [GP]--  (erase breadcrumb)
  GP-conditional mirrors:
    ( (34)  \\ if [GP]!=0               ) (35)  \\ if [GP]=0
    $ (37)  /  if [GP]!=0               # (36)  /  if [GP]=0
  CL/GP swap:
    K (33)  swap(CL_register, GP_register)
  Data/GP swap:
    Z (38)  swap([H0], [GP])  (byte-level, zero a variable)
  Notation: H0 = head position, [H0] = value at that position

  / reflect: E<->N  S<->W     \\ reflect: E<->S  N<->W

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
  gp [r c | flat]      Set/show GP (garbage pointer)

  step / s [n]         Forward n steps (default 1)
  back / b [n]         Reverse n steps (default 1)
  run [n]              Run n forward (default 100)
  runback [n]          Run n backward (default 100)
  zero / z             Reverse all steps back to step 0

  show                 Display grid
  vals                 Display values
  both                 Display grid + values
  trace                Toggle execution trace
  color                Toggle ANSI colors

  example <n>       Load example (bounce, loop, mirrors, branch, multiply)
  save <file>          Save state
  load <file>          Load state
  size <r> <c>         Resize grid (resets state)
  reset                Reset all state

  sel <r1> <c1> <r2> <c2>  Select rectangle (highlighted on grid)
  sel                      Show current selection
  desel                    Clear selection
  copy                     Copy selection to clipboard
  cut                      Copy to clipboard + zero region
  paste <r> <c>            Paste clipboard at (r,c) as upper-left

  help                 This help
  quit / exit / q      Exit
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
            if cmd in ('quit', 'q', 'exit'):
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

            # ── CL / H0 / H1 / GP ──
            elif cmd in ('cl', 'h0', 'h1', 'gp'):
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

            # ── Reset to step zero ──
            elif cmd in ('zero', 'z'):
                n = sim.step_count
                for _ in range(n):
                    sim.step_back()
                print(f"Reversed {n} steps back to step 0")
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

            # ── Save / Load (default to ./programs/) ──
            elif cmd == 'save':
                if args:
                    fn = args[0]
                    if not fn.endswith('.fb2d'):
                        fn += '.fb2d'
                    # Default to ./programs/ if no directory specified
                    if os.sep not in fn and not fn.startswith('.'):
                        prog_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')
                        if os.path.isdir(prog_dir):
                            fn = os.path.join(prog_dir, fn)
                    sim.save_state(fn)
                    print(f"Saved to {fn}")
                else:
                    print("Usage: save <filename>")

            elif cmd == 'load':
                if args:
                    fn = args[0]
                    if not fn.endswith('.fb2d'):
                        fn += '.fb2d'
                    # Default to ./programs/ if no directory specified and file not found locally
                    if not os.path.exists(fn) and os.sep not in fn and not fn.startswith('.'):
                        prog_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')
                        candidate = os.path.join(prog_dir, fn)
                        if os.path.exists(candidate):
                            fn = candidate
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

            # ── Select rectangle ──
            elif cmd == 'sel':
                if len(args) >= 4:
                    r1, c1 = int(args[0]), int(args[1])
                    r2, c2 = int(args[2]), int(args[3])
                    h, w = sim.select_rect(r1, c1, r2, c2)
                    r1, c1, r2, c2 = sim.selection
                    print(f"Selected {h}×{w} block: "
                          f"({r1},{c1})-({r2},{c2})")
                elif len(args) == 0:
                    if sim.selection:
                        r1, c1, r2, c2 = sim.selection
                        h = r2 - r1 + 1
                        w = c2 - c1 + 1
                        print(f"Selection: {h}×{w} at "
                              f"({r1},{c1})-({r2},{c2})")
                    else:
                        print("No selection")
                else:
                    print("Usage: sel <r1> <c1> <r2> <c2>")
                sim.display_grid()

            elif cmd == 'desel':
                sim.selection = None
                print("Selection cleared")
                sim.display_grid()

            # ── Copy ──
            elif cmd == 'copy':
                if sim.selection is None:
                    print("Nothing selected. Use: "
                          "sel <r1> <c1> <r2> <c2>")
                else:
                    result = sim.copy_rect()
                    if result:
                        h, w = result
                        print(f"Copied {h}×{w} block to clipboard")
                    sim.selection = None
                    sim.display_grid()

            # ── Cut ──
            elif cmd == 'cut':
                if sim.selection is None:
                    print("Nothing selected. Use: "
                          "sel <r1> <c1> <r2> <c2>")
                else:
                    result = sim.cut_rect()
                    if result:
                        h, w = result
                        print(f"Cut {h}×{w} block to clipboard "
                              f"(region zeroed)")
                    sim.selection = None
                    sim.display_grid()

            # ── Paste ──
            elif cmd == 'paste':
                if sim.clipboard is None:
                    print("Clipboard empty. Use: sel + copy/cut")
                elif len(args) >= 2:
                    dest_r, dest_c = int(args[0]), int(args[1])
                    h, w = sim.paste_rect(dest_r, dest_c)
                    print(f"Pasted {h}×{w} block at ({dest_r},{dest_c})")
                    sim.selection = None
                    sim.display_grid()
                else:
                    w, h, _ = sim.clipboard
                    print(f"Clipboard: {h}×{w} block")
                    print("Usage: paste <r> <c>")

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

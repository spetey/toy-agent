#!/usr/bin/env python3
"""
F***brain 2D Grid Simulator v1
Authored or modified by Claude
Version: 2026-03-16 v1.12 — IX vertical momentum ops (C/D/O) for ping-pong scan
                            v1.11: head-overlap NOP guards for true reversibility
                            v1.10: IX momentum ops (A/B/U)
                            v1.9: IX interoceptor for cross-gadget correction
                            v1.8: [CL]++ and [CL]-- opcodes (: and ;)
                            v1.7: R/L rotate-by-[CL], Y fused rotate-XOR
                            v1.6: bit-level ops (x=XOR, r/l rotate, f bit-Fredkin, z bit-EX-swap)

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
  IX                 — interoceptor (flat index) for cross-gadget correction
  EX                 — exteroceptor (flat index) for reversible breadcrumb trails

Mirror reflection rules (standard optics):
  /  : (dr,dc) → (−dc,−dr)    E→N  N→E  W→S  S→W
  \\  : (dr,dc) → (dc,dr)      E→S  S→E  W→N  N→W

Conditional mirrors reflect OR pass through based on grid[CL].
EX-conditional mirrors reflect OR pass through based on grid[EX].
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
    # Exteroceptor (EX) ops for reversible breadcrumb trails
    'P':  27,  # grid[EX]++  (leave breadcrumb)
    'Q':  28,  # grid[EX]--  (erase breadcrumb)
    ']':  29,  # EX East  (col+1)
    '[':  30,  # EX West  (col-1)
    '}':  31,  # EX South (row+1)
    '{':  32,  # EX North (row-1)
    # EX-conditional mirrors and CL/EX swap
    'K':  33,  # swap(CL_register, GP_register)
    '(':  34,  # \ reflect if grid[EX] ≠ 0, else pass through
    ')':  35,  # \ reflect if grid[EX] = 0, else pass through
    '#':  36,  # / reflect if grid[EX] = 0, else pass through
    '$':  37,  # / reflect if grid[EX] ≠ 0, else pass through
    # Data/EX swap for efficient variable zeroing
    'Z':  38,  # swap([H0], [EX])  (byte-level EX swap)
    # ── Bit-level operations (v1.6) ──
    'x':  39,  # [H0] ^= [H1]  (XOR-accumulate, self-inverse)
    'r':  40,  # [H0] rotate right 1 bit  (bit0→bit7, inverse: l)
    'l':  41,  # [H0] rotate left 1 bit   (bit7→bit0, inverse: r)
    'f':  42,  # if [CL]&1: swap([H0], [H1])  (bit-0 Fredkin, self-inverse)
    'z':  43,  # swap(bit0 of [H0], bit0 of [H1])  (bit-level swap, self-inverse)
    'R':  44,  # [H0] rotate right by ([CL] & 15) bits  (inverse: L)
    'L':  45,  # [H0] rotate left  by ([CL] & 15) bits  (inverse: R)
    'Y':  46,  # [H0] ^= ror([H1], [CL] & 15)  (fused rotate-XOR, self-inverse)
    ':':  47,  # [CL]++  (inverse: ;)
    ';':  48,  # [CL]--  (inverse: :)
    # ── IX interoceptor operations (v1.9) ──
    'H':  49,  # IX move North (inverse: h)
    'h':  50,  # IX move South (inverse: H)
    'a':  51,  # IX move East  (inverse: d)
    'd':  52,  # IX move West  (inverse: a)
    'm':  53,  # [H0] ^= [IX]  (raw 16-bit XOR, self-inverse, copy-in/uncompute)
    'M':  54,  # payload(H0) -= payload(IX) with Δp  (inverse: see notes)
    'j':  55,  # [IX] ^= [H0]  (raw 16-bit write-back, self-inverse)
    'V':  56,  # swap([CL], [IX])  (test bridge, self-inverse)
    # ── IX momentum operations (v1.10) ──
    'A':  57,  # advance IX in ix_dir  (inverse: B)
    'B':  58,  # retreat IX opposite ix_dir  (inverse: A)
    'U':  59,  # flip ix_dir (E↔W, N↔S)  (self-inverse)
    # ── IX vertical momentum operations (v1.12) ──
    'C':  60,  # advance IX in ix_vdir  (inverse: D)
    'D':  61,  # retreat IX opposite ix_vdir  (inverse: C)
    'O':  62,  # flip ix_vdir (N↔S, E↔W)  (self-inverse)
}

OPCODE_TO_CHAR = {v: k for k, v in OPCODES.items()}
OPCODE_TO_CHAR[0] = '·'   # NOP displayed as middle dot

# ─── d_min=4 Opcode Encoding ────────────────────────────────────────
#
# Maps internal opcode number (0–56) → 11-bit payload value.
# Constructed from an [11,6,4] linear code (parity rows 7,11,13,14,19,21).
# Minimum pairwise Hamming distance between any two payloads = 4.
#
# Key property: NO combination of 1, 2, or 3 data-bit flips can turn one
# valid opcode payload into another.  Every corrupted opcode becomes NOP.
# This eliminates the cascading failure mode where noise creates rogue
# opcodes (especially j/m) that corrupt remote cells via IX.

OPCODE_PAYLOADS = {
     0:    0,  1:  449,  2:  706,  3:  771,  4:  836,  5:  645,
     6:  390,  7:   71,  8:  904,  9:  585, 10:  330, 11:  139,
    12:  204, 13:  269, 14:  526, 15:  975, 16: 1232, 17: 1297,
    18: 1554, 19: 2003, 20: 1940, 21: 1621, 22: 1366, 23: 1175,
    24: 1880, 25: 1689, 26: 1434, 27: 1115, 28: 1052, 29: 1501,
    30: 1758, 31: 1823, 32: 1376, 33: 1185, 34: 1954, 35: 1635,
    36: 1572, 37: 2021, 38: 1254, 39: 1319, 40: 1768, 41: 1833,
    42: 1066, 43: 1515, 44: 1452, 45: 1133, 46: 1902, 47: 1711,
    48:  432, 49:  113, 50:  882, 51:  691, 52:  756, 53:  821,
    54:   54, 55:  503, 56:  568,
    57:  189, 58:  250, 59:  315,
    60:  380, 61:  639, 62:  958,
}

# Reverse lookup: 11-bit payload → opcode number (0 = NOP for unrecognized)
# Uses nearest-codeword decoding: payloads within Hamming distance 1 of a
# valid opcode codeword decode to that opcode (not NOP). This is safe because
# d_min=4 guarantees zero ambiguity at distance 1. A single data-bit error
# in an opcode cell now executes the CORRECT opcode instead of NOP.
_PAYLOAD_TO_OPCODE = [0] * 2048
for _op, _pl in OPCODE_PAYLOADS.items():
    _PAYLOAD_TO_OPCODE[_pl] = _op
    # Set all distance-1 neighbors to the same opcode
    for _bit in range(11):
        _neighbor = _pl ^ (1 << _bit)
        _PAYLOAD_TO_OPCODE[_neighbor] = _op
del _op, _pl, _bit, _neighbor

def payload_to_opcode(payload):
    """Map an 11-bit payload to an internal opcode number (0 = NOP)."""
    return _PAYLOAD_TO_OPCODE[payload & 0x7FF]

def encode_opcode(opcode_num):
    """Encode an opcode number into a 16-bit Hamming(16,11) SECDED cell
    using the d_min=4 payload encoding."""
    return hamming_encode(OPCODE_PAYLOADS[opcode_num])

# Inverse direction map for head movement (for step_back)
HEAD_MOVE_INVERSE = {
    7: DIR_S, 8: DIR_N, 9: DIR_W, 10: DIR_E,   # H0: N↔S, E↔W
    11: DIR_S, 12: DIR_N, 13: DIR_W, 14: DIR_E,  # H1: N↔S, E↔W
    29: DIR_W, 30: DIR_E, 31: DIR_N, 32: DIR_S,  # EX: ]↔[, }↔{
    49: DIR_S, 50: DIR_N, 51: DIR_W, 52: DIR_E,  # IX: H↔h, a↔d
}

# ─── 16-bit Hamming(16,11) SECDED Constants ──────────────────────────
#
# Standard-form Hamming(16,11) SECDED.
# Bit layout:
#   Bit:  15  14  13  12  11  10   9   8   7   6   5   4   3   2   1   0
#         d10 d9  d8  d7  d6  d5  d4  p3  d3  d2  d1  p2  d0  p1  p0  p∀
#   Syn:  15  14  13  12  11  10   9   8   7   6   5   4   3   2   1   0
#
# Key property: syndrome value = bit position of error.  This makes
# the R/L correction trick work: R(cw, syn), XOR bit0, L(cw, syn).
#
# Two op categories:
#   Raw 16-bit ops (r,l,R,L,Y,z,x,X,F,Z,f): act on full 16 bits
#   Δp arithmetic ops (+,-,.,,,P,Q,:,;): modify payload, adjust parity

CELL_BITS = 16
CELL_MASK = 0xFFFF          # 16-bit cell values
PAYLOAD_MASK = 0x7FF        # 11-bit mask for payload VALUE arithmetic
PAYLOAD_BITS = 11
ROTATION_MASK = 0x0F        # & 15 for 16-bit rotations

# Standard-form data bit positions (non-powers-of-2, excluding 0)
DATA_POSITIONS = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]  # d0-d10
DATA_MASK = 0xFEE8          # OR of all data bit positions

# Standard-form: syndrome column i IS bit position i.
SYNDROME_SIG = list(range(16))  # [0, 1, 2, ..., 15]

# Syndrome check masks (classic Hamming: all positions with bit i set).
_SYNDROME_MASKS = [0xAAAA, 0xCCCC, 0xF0F0, 0xFF00]

# Parity generator masks: data positions in each Hamming parity's check set.
# (Same as _SYNDROME_MASKS but excluding the parity bit itself.)
_PARITY_MASKS = [
    0xAAA8,  # p0 (bit 1): positions {3,5,7,9,11,13,15}
    0xCCC8,  # p1 (bit 2): positions {3,6,7,10,11,14,15}
    0xF0E0,  # p2 (bit 4): positions {5,6,7,12,13,14,15}
    0xFE00,  # p3 (bit 8): positions {9,10,11,12,13,14,15}
]

def _popcount(x):
    """Count set bits."""
    return bin(x).count('1')

# Build scatter/gather tables for standard-form payload extraction.
# _PAYLOAD_TO_CELL: 11-bit payload → 16-bit cell (data bits only, no parity)
# _CELL_TO_PAYLOAD: 16-bit cell → 11-bit payload (ignores parity bits)
_PAYLOAD_TO_CELL = [0] * 2048
for _p in range(2048):
    _c = 0
    for _i, _bp in enumerate(DATA_POSITIONS):
        if (_p >> _i) & 1:
            _c |= (1 << _bp)
    _PAYLOAD_TO_CELL[_p] = _c

_CELL_TO_PAYLOAD = [0] * 65536
for _v in range(65536):
    _p = 0
    for _i, _bp in enumerate(DATA_POSITIONS):
        if (_v >> _bp) & 1:
            _p |= (1 << _i)
    _CELL_TO_PAYLOAD[_v] = _p

del _p, _c, _i, _bp, _v

def cell_to_payload(cell):
    """Extract the 11-bit payload from a standard-form Hamming codeword."""
    return _CELL_TO_PAYLOAD[cell & CELL_MASK]

def hamming_encode(payload):
    """Encode an 11-bit payload into a 16-bit standard-form Hamming(16,11)
    SECDED codeword."""
    payload &= PAYLOAD_MASK
    cell = _PAYLOAD_TO_CELL[payload]
    # Compute Hamming parity bits (at positions 1, 2, 4, 8)
    for i in range(4):
        if _popcount(cell & _PARITY_MASKS[i]) & 1:
            cell |= (1 << (1 << i))  # set parity bit at position 2^i
    # Overall parity at bit 0
    if _popcount(cell) & 1:
        cell |= 1
    return cell

def hamming_syndrome(codeword):
    """Compute 4-bit syndrome + overall parity error bit.
    Returns (syndrome, p_all_err).
    In standard form, syndrome value = bit position of error."""
    s = 0
    for i in range(4):
        if _popcount(codeword & _SYNDROME_MASKS[i]) & 1:
            s |= (1 << i)
    p_all_err = _popcount(codeword) & 1  # 0 for valid codeword
    return s, p_all_err

# Precompute PAYLOAD_FLIP_TO_CELL_FLIP: for each 11-bit payload flip mask,
# the complete 16-bit cell XOR mask (scattered data bits + parity fixup +
# overall parity).  Usage: cell ^= PAYLOAD_FLIP_TO_CELL_FLIP[payload_flip]
PAYLOAD_FLIP_TO_CELL_FLIP = [0] * 2048
for _pf in range(2048):
    _cf = _PAYLOAD_TO_CELL[_pf]   # scatter payload flips to data bit positions
    # Compute parity bit flips
    for _i in range(4):
        if _popcount(_cf & _PARITY_MASKS[_i]) & 1:
            _cf ^= (1 << (1 << _i))
    # Overall parity flip
    if _popcount(_cf) & 1:
        _cf ^= 1
    PAYLOAD_FLIP_TO_CELL_FLIP[_pf] = _cf

# Precompute INC_XOR / DEC_XOR for fast single-step inc/dec.
# hamming_inc(cell) = cell ^ INC_XOR[cell_to_payload(cell)]
INC_XOR = [0] * 2048
DEC_XOR = [0] * 2048
for _p in range(2048):
    _new = (_p + 1) & PAYLOAD_MASK
    INC_XOR[_p] = PAYLOAD_FLIP_TO_CELL_FLIP[_p ^ _new]
    _new = (_p - 1) & PAYLOAD_MASK
    DEC_XOR[_p] = PAYLOAD_FLIP_TO_CELL_FLIP[_p ^ _new]

del _pf, _cf, _i, _p, _new

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
    'blue':    '\033[94m',
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
        self.ix = 0  # Interoceptor for cross-gadget correction
        self.ix_dir = DIR_E  # IX horizontal momentum (for A/B advance/retreat)
        self.ix_vdir = DIR_S  # IX vertical momentum (for C/D advance/retreat)
        self.cl = 0
        self.ex = 0  # Exteroceptor for breadcrumb trails

        self.step_count = 0
        self.use_color = True
        self.trace = False

        # Clipboard for block editing
        self.selection = None   # (r1, c1, r2, c2) inclusive corners
        self.clipboard = None   # (width, height, [values]) — row-major

        # Multi-IP support
        self.n_ips = 1
        self.active_ip = 0
        self.ips = [self._capture_ip_state()]

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

    # ── Multi-IP support ─────────────────────────────────────────

    _IP_FIELDS = ('ip_row', 'ip_col', 'ip_dir', 'h0', 'h1', 'ix', 'ix_dir', 'ix_vdir', 'cl', 'ex')

    def _capture_ip_state(self):
        """Snapshot the active IP's state into a dict."""
        return {f: getattr(self, f) for f in self._IP_FIELDS}

    def _restore_ip_state(self, state_dict):
        """Restore a dict of IP state fields to self.* ."""
        for f in self._IP_FIELDS:
            setattr(self, f, state_dict[f])

    def _save_active(self):
        """Save the current self.* fields back into self.ips[self.active_ip]."""
        self.ips[self.active_ip] = self._capture_ip_state()

    def _load_active(self, index):
        """Load self.ips[index] into self.* fields."""
        self._restore_ip_state(self.ips[index])
        self.active_ip = index

    def _activate_ip(self, index):
        """Switch active IP: save current, load new."""
        if index == self.active_ip:
            return
        self._save_active()
        self._load_active(index)

    def add_ip(self, ip_row=0, ip_col=0, ip_dir=None,
               h0=0, h1=0, ix=0, ix_dir=None, ix_vdir=None, cl=0, ex=0):
        """Add a new IP with the given state. Returns the IP index."""
        if ip_dir is None:
            ip_dir = DIR_E
        if ix_dir is None:
            ix_dir = DIR_E
        if ix_vdir is None:
            ix_vdir = DIR_S
        self._save_active()
        state = {
            'ip_row': ip_row, 'ip_col': ip_col, 'ip_dir': ip_dir,
            'h0': h0, 'h1': h1, 'ix': ix, 'ix_dir': ix_dir,
            'ix_vdir': ix_vdir, 'cl': cl, 'ex': ex,
        }
        self.ips.append(state)
        self.n_ips = len(self.ips)
        return self.n_ips - 1

    # ── Forward step ───────────────────────────────────────────────

    def step(self):
        """Execute one instruction: read, execute, advance IP."""
        flat_ip = self._ip_flat()
        opcode = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[self.grid[flat_ip]]]
        old_dir = self.ip_dir

        # ── Execute ──
        if opcode == 1:      # / unconditional reflect
            self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 2:    # \ unconditional reflect
            self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 3:    # % / reflect if payload(CL) ≠ 0
            if self.grid[self.cl] & DATA_MASK:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 4:    # ? / reflect if payload(CL) = 0
            if not (self.grid[self.cl] & DATA_MASK):
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 5:    # & \ reflect if payload(CL) ≠ 0
            if self.grid[self.cl] & DATA_MASK:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 6:    # ! \ reflect if payload(CL) = 0
            if not (self.grid[self.cl] & DATA_MASK):
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode in (7, 8, 9, 10):    # H0 movement N/S/E/W
            dirs = {7: DIR_N, 8: DIR_S, 9: DIR_E, 10: DIR_W}
            self.h0 = self._move_head(self.h0, dirs[opcode])

        elif opcode in (11, 12, 13, 14):  # H1 movement n/s/e/w
            dirs = {11: DIR_N, 12: DIR_S, 13: DIR_E, 14: DIR_W}
            self.h1 = self._move_head(self.h1, dirs[opcode])

        # ── Write guard: data ops that write to grid[ip_cell] are NOP ──
        # (Same principle as existing head-overlap NOP guards: writing to
        # the IP's instruction cell destroys info needed by step_back.)

        elif opcode == 15:   # + payload(H0)++ with Δp parity fixup
            if self.h0 != flat_ip:
                self.grid[self.h0] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.h0]]]

        elif opcode == 16:   # - payload(H0)-- with Δp parity fixup
            if self.h0 != flat_ip:
                self.grid[self.h0] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.h0]]]

        elif opcode == 17:   # . payload(H0) += payload(H1) with Δp
            if self.h0 != self.h1 and self.h0 != flat_ip:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op + (_CELL_TO_PAYLOAD[self.grid[self.h1]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 18:   # , payload(H0) -= payload(H1) with Δp
            if self.h0 != self.h1 and self.h0 != flat_ip:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op - (_CELL_TO_PAYLOAD[self.grid[self.h1]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 19:   # X swap([H0], [H1])
            if self.h0 != flat_ip and self.h1 != flat_ip:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 20:   # F Fredkin: if payload(CL)!=0: swap([H0], [H1])
            if self.cl != self.h0 and self.cl != self.h1 \
                    and self.h0 != flat_ip and self.h1 != flat_ip:
                if self.grid[self.cl] & DATA_MASK:
                    self.grid[self.h0], self.grid[self.h1] = \
                        self.grid[self.h1], self.grid[self.h0]

        elif opcode == 21:   # G swap(H1_register, grid[H0])
            if self.h0 != flat_ip and self.grid[self.h0] < self.grid_size:
                self.h1, self.grid[self.h0] = self.grid[self.h0], self.h1

        elif opcode == 22:   # T swap(grid[CL], grid[H0])
            if self.cl != flat_ip and self.h0 != flat_ip:
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

        # ── EX (exteroceptor) operations ──
        elif opcode == 27:   # P payload(EX)++ with Δp parity fixup
            if self.ex != flat_ip:
                self.grid[self.ex] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.ex]]]

        elif opcode == 28:   # Q payload(EX)-- with Δp parity fixup
            if self.ex != flat_ip:
                self.grid[self.ex] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.ex]]]

        elif opcode == 29:   # ] EX East
            self.ex = self._move_head(self.ex, DIR_E)

        elif opcode == 30:   # [ EX West
            self.ex = self._move_head(self.ex, DIR_W)

        elif opcode == 31:   # } EX South
            self.ex = self._move_head(self.ex, DIR_S)

        elif opcode == 32:   # { EX North
            self.ex = self._move_head(self.ex, DIR_N)

        # ── EX-conditional mirrors and CL/EX swap ──
        elif opcode == 33:   # K swap(CL_register, GP_register)
            self.cl, self.ex = self.ex, self.cl

        elif opcode == 34:   # ( \ reflect if payload(EX) ≠ 0
            if self.grid[self.ex] & DATA_MASK:
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 35:   # ) \ reflect if payload(EX) = 0
            if not (self.grid[self.ex] & DATA_MASK):
                self.ip_dir = BACKSLASH_REFLECT[self.ip_dir]

        elif opcode == 36:   # # / reflect if payload(EX) = 0
            if not (self.grid[self.ex] & DATA_MASK):
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 37:   # $ / reflect if payload(EX) ≠ 0
            if self.grid[self.ex] & DATA_MASK:
                self.ip_dir = SLASH_REFLECT[self.ip_dir]

        elif opcode == 38:   # Z swap([H0], [EX])
            if self.h0 != flat_ip and self.ex != flat_ip:
                self.grid[self.h0], self.grid[self.ex] = \
                    self.grid[self.ex], self.grid[self.h0]

        # ── Bit-level operations (v1.6) ──
        elif opcode == 39:   # x  [H0] ^= [H1]  (XOR, self-inverse)
            if self.h0 != self.h1 and self.h0 != flat_ip:
                self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.h1]

        elif opcode == 40:   # r  [H0] rotate right 1 bit (raw 16-bit)
            if self.h0 != flat_ip:
                v = self.grid[self.h0]
                self.grid[self.h0] = ((v >> 1) | ((v & 1) << 15)) & CELL_MASK

        elif opcode == 41:   # l  [H0] rotate left 1 bit (raw 16-bit)
            if self.h0 != flat_ip:
                v = self.grid[self.h0]
                self.grid[self.h0] = (((v << 1) & CELL_MASK) | (v >> 15)) & CELL_MASK

        elif opcode == 42:   # f  if [CL]&1: swap([H0], [H1])  (bit-0 Fredkin)
            if self.cl != self.h0 and self.cl != self.h1 \
                    and self.h0 != flat_ip and self.h1 != flat_ip:
                if self.grid[self.cl] & 1:
                    self.grid[self.h0], self.grid[self.h1] = \
                        self.grid[self.h1], self.grid[self.h0]

        elif opcode == 43:   # z  swap bit0 of [H0] with bit0 of [H1] (raw 16-bit)
            if self.h0 != flat_ip and self.h1 != flat_ip:
                a = self.grid[self.h0]
                b = self.grid[self.h1]
                a_bit = a & 1
                b_bit = b & 1
                self.grid[self.h0] = (a & 0xFFFE) | b_bit
                self.grid[self.h1] = (b & 0xFFFE) | a_bit

        elif opcode == 44:   # R  [H0] ror by (payload([CL]) & 15) bits (raw 16-bit)
            if self.h0 != flat_ip:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                if n:
                    v = self.grid[self.h0]
                    self.grid[self.h0] = ((v >> n) | (v << (CELL_BITS - n))) & CELL_MASK

        elif opcode == 45:   # L  [H0] rol by (payload([CL]) & 15) bits (raw 16-bit)
            if self.h0 != flat_ip:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                if n:
                    v = self.grid[self.h0]
                    self.grid[self.h0] = ((v << n) & CELL_MASK | (v >> (CELL_BITS - n))) & CELL_MASK

        elif opcode == 46:   # Y  [H0] ^= ror([H1], payload([CL]) & 15)  (raw 16-bit, self-inverse)
            if self.h0 != self.h1 and self.h0 != flat_ip:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                v = self.grid[self.h1]
                rotated = ((v >> n) | (v << (CELL_BITS - n))) & CELL_MASK if n else v
                self.grid[self.h0] = self.grid[self.h0] ^ rotated

        elif opcode == 47:   # :  payload(CL)++ with Δp parity fixup
            if self.cl != flat_ip:
                self.grid[self.cl] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.cl]]]

        elif opcode == 48:   # ;  payload(CL)-- with Δp parity fixup
            if self.cl != flat_ip:
                self.grid[self.cl] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.cl]]]

        # ── IX (interoceptor) operations (v1.9) ──
        elif opcode in (49, 50, 51, 52):    # IX movement H/h/a/d
            dirs = {49: DIR_N, 50: DIR_S, 51: DIR_E, 52: DIR_W}
            self.ix = self._move_head(self.ix, dirs[opcode])

        elif opcode == 53:   # m [H0] ^= [IX]  (raw 16-bit XOR, self-inverse)
            if self.h0 != self.ix and self.h0 != flat_ip:
                self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.ix]

        elif opcode == 54:   # M payload(H0) -= payload(IX) with Δp (uncompute)
            if self.h0 != self.ix and self.h0 != flat_ip:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op - (_CELL_TO_PAYLOAD[self.grid[self.ix]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 55:   # j [IX] ^= [H0]  (raw 16-bit write-back, self-inverse)
            if self.ix != self.h0 and self.ix != flat_ip:
                self.grid[self.ix] = self.grid[self.ix] ^ self.grid[self.h0]

        elif opcode == 56:   # V swap([CL], [IX])  (test bridge, self-inverse)
            if self.cl != flat_ip and self.ix != flat_ip:
                self.grid[self.cl], self.grid[self.ix] = \
                    self.grid[self.ix], self.grid[self.cl]

        # ── IX momentum operations (v1.10) ──
        elif opcode == 57:   # A advance IX in ix_dir
            self.ix = self._move_head(self.ix, self.ix_dir)

        elif opcode == 58:   # B retreat IX opposite ix_dir
            self.ix = self._move_head(self.ix, self.ix_dir ^ 2)  # N↔S, E↔W

        elif opcode == 59:   # U flip ix_dir (E↔W, N↔S, self-inverse)
            self.ix_dir = self.ix_dir ^ 2

        # ── IX vertical momentum operations (v1.12) ──
        elif opcode == 60:   # C advance IX in ix_vdir
            self.ix = self._move_head(self.ix, self.ix_vdir)

        elif opcode == 61:   # D retreat IX opposite ix_vdir
            self.ix = self._move_head(self.ix, self.ix_vdir ^ 2)

        elif opcode == 62:   # O flip ix_vdir (N↔S, E↔W, self-inverse)
            self.ix_vdir = self.ix_vdir ^ 2

        # else: NOP (0 or 63–2047 payload)

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
        opcode = _PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD[self.grid[prev_flat]]]

        # ── Determine previous direction ──
        if opcode == 1:      # / always reflects
            prev_dir = SLASH_REFLECT[self.ip_dir]
        elif opcode == 2:    # \ always reflects
            prev_dir = BACKSLASH_REFLECT[self.ip_dir]
        elif opcode in (3, 4):  # conditional / mirrors (payload test)
            cond = bool(self.grid[self.cl] & DATA_MASK) if opcode == 3 else \
                   (not (self.grid[self.cl] & DATA_MASK))
            prev_dir = SLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        elif opcode in (5, 6):  # conditional \ mirrors (payload test)
            cond = bool(self.grid[self.cl] & DATA_MASK) if opcode == 5 else \
                   (not (self.grid[self.cl] & DATA_MASK))
            prev_dir = BACKSLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        elif opcode in (34, 35):  # conditional \ mirrors on payload(EX)
            cond = bool(self.grid[self.ex] & DATA_MASK) if opcode == 34 else \
                   (not (self.grid[self.ex] & DATA_MASK))
            prev_dir = BACKSLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        elif opcode in (36, 37):  # conditional / mirrors on payload(EX)
            cond = (not (self.grid[self.ex] & DATA_MASK)) if opcode == 36 else \
                   bool(self.grid[self.ex] & DATA_MASK)
            prev_dir = SLASH_REFLECT[self.ip_dir] if cond else self.ip_dir
        else:
            prev_dir = self.ip_dir

        # ── Undo instruction effect ──
        # (Write guard: same as step() — NOP if write target == prev_flat)
        if opcode in (7, 8, 9, 10):    # H0 was moved, undo
            self.h0 = self._move_head(self.h0, HEAD_MOVE_INVERSE[opcode])

        elif opcode in (11, 12, 13, 14):  # H1 was moved, undo
            self.h1 = self._move_head(self.h1, HEAD_MOVE_INVERSE[opcode])

        elif opcode == 15:   # was ++, undo -- (Δp dec)
            if self.h0 != prev_flat:
                self.grid[self.h0] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.h0]]]

        elif opcode == 16:   # was --, undo ++ (Δp inc)
            if self.h0 != prev_flat:
                self.grid[self.h0] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.h0]]]

        elif opcode == 17:   # was +=, undo -= (Δp sub)
            if self.h0 != self.h1 and self.h0 != prev_flat:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op - (_CELL_TO_PAYLOAD[self.grid[self.h1]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 18:   # was -=, undo += (Δp add)
            if self.h0 != self.h1 and self.h0 != prev_flat:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op + (_CELL_TO_PAYLOAD[self.grid[self.h1]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 19:   # X is self-inverse
            if self.h0 != prev_flat and self.h1 != prev_flat:
                self.grid[self.h0], self.grid[self.h1] = \
                    self.grid[self.h1], self.grid[self.h0]

        elif opcode == 20:   # F is self-inverse (payload test)
            if self.cl != self.h0 and self.cl != self.h1 \
                    and self.h0 != prev_flat and self.h1 != prev_flat:
                if self.grid[self.cl] & DATA_MASK:
                    self.grid[self.h0], self.grid[self.h1] = \
                        self.grid[self.h1], self.grid[self.h0]

        elif opcode == 21:   # G is self-inverse
            if self.h0 != prev_flat and self.grid[self.h0] < self.grid_size:
                self.h1, self.grid[self.h0] = self.grid[self.h0], self.h1

        elif opcode == 22:   # T is self-inverse
            if self.cl != prev_flat and self.h0 != prev_flat:
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

        # ── EX (exteroceptor) undo operations ──
        elif opcode == 27:   # P was ++, undo -- (Δp dec)
            if self.ex != prev_flat:
                self.grid[self.ex] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.ex]]]

        elif opcode == 28:   # Q was --, undo ++ (Δp inc)
            if self.ex != prev_flat:
                self.grid[self.ex] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.ex]]]

        elif opcode in (29, 30, 31, 32):  # EX movement, undo
            self.ex = self._move_head(self.ex, HEAD_MOVE_INVERSE[opcode])

        elif opcode == 33:   # K is self-inverse
            self.cl, self.ex = self.ex, self.cl

        elif opcode == 38:   # Z is self-inverse
            if self.h0 != prev_flat and self.ex != prev_flat:
                self.grid[self.h0], self.grid[self.ex] = \
                    self.grid[self.ex], self.grid[self.h0]

        # ── Bit-level undo (v1.6) ──
        elif opcode == 39:   # x XOR is self-inverse
            if self.h0 != self.h1 and self.h0 != prev_flat:
                self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.h1]

        elif opcode == 40:   # r was ror-1, undo with rol-1 (raw 16-bit)
            if self.h0 != prev_flat:
                v = self.grid[self.h0]
                self.grid[self.h0] = (((v << 1) & CELL_MASK) | (v >> 15)) & CELL_MASK

        elif opcode == 41:   # l was rol-1, undo with ror-1 (raw 16-bit)
            if self.h0 != prev_flat:
                v = self.grid[self.h0]
                self.grid[self.h0] = ((v >> 1) | ((v & 1) << 15)) & CELL_MASK

        elif opcode == 42:   # f bit-0 Fredkin is self-inverse
            if self.cl != self.h0 and self.cl != self.h1 \
                    and self.h0 != prev_flat and self.h1 != prev_flat:
                if self.grid[self.cl] & 1:
                    self.grid[self.h0], self.grid[self.h1] = \
                        self.grid[self.h1], self.grid[self.h0]

        elif opcode == 43:   # z bit-0 swap is self-inverse (raw 16-bit)
            if self.h0 != prev_flat and self.h1 != prev_flat:
                a = self.grid[self.h0]
                b = self.grid[self.h1]
                a_bit = a & 1
                b_bit = b & 1
                self.grid[self.h0] = (a & 0xFFFE) | b_bit
                self.grid[self.h1] = (b & 0xFFFE) | a_bit

        elif opcode == 44:   # R was ror-by-CL, undo with rol-by-CL (payload 16-bit)
            if self.h0 != prev_flat:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                if n:
                    v = self.grid[self.h0]
                    self.grid[self.h0] = ((v << n) & CELL_MASK | (v >> (CELL_BITS - n))) & CELL_MASK

        elif opcode == 45:   # L was rol-by-CL, undo with ror-by-CL (payload 16-bit)
            if self.h0 != prev_flat:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                if n:
                    v = self.grid[self.h0]
                    self.grid[self.h0] = ((v >> n) | (v << (CELL_BITS - n))) & CELL_MASK

        elif opcode == 46:   # Y is self-inverse (payload 16-bit)
            if self.h0 != self.h1 and self.h0 != prev_flat:
                n = _CELL_TO_PAYLOAD[self.grid[self.cl]] & ROTATION_MASK
                v = self.grid[self.h1]
                rotated = ((v >> n) | (v << (CELL_BITS - n))) & CELL_MASK if n else v
                self.grid[self.h0] = self.grid[self.h0] ^ rotated

        elif opcode == 47:   # : was [CL]++, undo -- (Δp dec)
            if self.cl != prev_flat:
                self.grid[self.cl] ^= DEC_XOR[_CELL_TO_PAYLOAD[self.grid[self.cl]]]

        elif opcode == 48:   # ; was [CL]--, undo ++ (Δp inc)
            if self.cl != prev_flat:
                self.grid[self.cl] ^= INC_XOR[_CELL_TO_PAYLOAD[self.grid[self.cl]]]

        # ── IX (interoceptor) undo (v1.9) ──
        elif opcode in (49, 50, 51, 52):    # IX was moved, undo
            self.ix = self._move_head(self.ix, HEAD_MOVE_INVERSE[opcode])

        elif opcode == 53:   # m XOR is self-inverse
            if self.h0 != self.ix and self.h0 != prev_flat:
                self.grid[self.h0] = self.grid[self.h0] ^ self.grid[self.ix]

        elif opcode == 54:   # M was -=, undo += (Δp add)
            if self.h0 != self.ix and self.h0 != prev_flat:
                _op = _CELL_TO_PAYLOAD[self.grid[self.h0]]
                _np = (_op + (_CELL_TO_PAYLOAD[self.grid[self.ix]])) & PAYLOAD_MASK
                _fl = _op ^ _np
                self.grid[self.h0] ^= PAYLOAD_FLIP_TO_CELL_FLIP[_fl]

        elif opcode == 55:   # j XOR is self-inverse
            if self.ix != self.h0 and self.ix != prev_flat:
                self.grid[self.ix] = self.grid[self.ix] ^ self.grid[self.h0]

        elif opcode == 56:   # V swap is self-inverse
            if self.cl != prev_flat and self.ix != prev_flat:
                self.grid[self.cl], self.grid[self.ix] = \
                    self.grid[self.ix], self.grid[self.cl]

        # ── IX momentum undo (v1.10) ──
        elif opcode == 57:   # A was advance, undo = retreat
            self.ix = self._move_head(self.ix, self.ix_dir ^ 2)

        elif opcode == 58:   # B was retreat, undo = advance
            self.ix = self._move_head(self.ix, self.ix_dir)

        elif opcode == 59:   # U flip is self-inverse
            self.ix_dir = self.ix_dir ^ 2

        # ── IX vertical momentum undo (v1.12) ──
        elif opcode == 60:   # C was advance, undo = retreat
            self.ix = self._move_head(self.ix, self.ix_vdir ^ 2)

        elif opcode == 61:   # D was retreat, undo = advance
            self.ix = self._move_head(self.ix, self.ix_vdir)

        elif opcode == 62:   # O flip is self-inverse
            self.ix_vdir = self.ix_vdir ^ 2

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

    # ── Multi-IP stepping ─────────────────────────────────────────

    def step_all(self):
        """One interleaved round: step IP0, IP1, ..., IP(n-1)."""
        if self.n_ips == 1:
            self.step()
            return
        self._save_active()
        for i in range(self.n_ips):
            self._load_active(i)
            self.step()
            self.ips[i] = self._capture_ip_state()
        # Leave IP0 as active for display
        self._load_active(0)

    def step_back_all(self):
        """Reverse one interleaved round: undo IP(n-1), ..., IP0."""
        if self.n_ips == 1:
            self.step_back()
            return
        self._save_active()
        for i in range(self.n_ips - 1, -1, -1):
            self._load_active(i)
            self.step_back()
            self.ips[i] = self._capture_ip_state()
        self._load_active(0)

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
        self.ix = 0
        self.cl = 0
        self.ex = 0
        self.step_count = 0
        self.n_ips = 1
        self.active_ip = 0
        self.ips = [self._capture_ip_state()]

        count = 0
        for ch in code:
            if ch in OPCODES and count < self.grid_size:
                self.grid[count] = encode_opcode(OPCODES[ch])
                count += 1
        return count

    def place_code(self, row, col, code, vertical=False):
        """Place code onto the grid starting at (row, col).
        If vertical=True, place going South; else going East.
        Opcodes are stored as Hamming(16,11) SECDED codewords."""
        count = 0
        r, c = row, col
        for ch in code:
            if ch in OPCODES:
                flat = self._to_flat(r % self.rows, c % self.cols)
                self.grid[flat] = encode_opcode(OPCODES[ch])
                count += 1
                if vertical:
                    r += 1
                else:
                    c += 1
        return count

    def wrap_code(self, opcodes, width, start_row=0, start_col=0):
        """Wrap a linear opcode sequence into a boustrophedon (serpentine) layout.
        Opcodes are stored as Hamming(16,11) SECDED codewords.

        The IP enters at (start_row, start_col) going East and snakes through
        rows using mirrors at the edges:

          Row 0:  [opcodes →→→]  \\     (W-1 opcode slots, then \\ at col W-1)
          Row 1:  /  [←←← opcodes]     (/ at col W-1 reflects S→W, opcodes W→,
                                         / at col 0 reflects W→S)
          Row 2:  \\  [opcodes →→→]  \\ (\\ at col 0 reflects S→E, ... repeat)
          ...

        First row: cols start_col..W-2 = W-1-start_col opcode slots.
        Even rows (after first): cols 1..W-2 = W-2 opcode slots.
        Odd rows (going West):   cols W-2..1 = W-2 opcode slots.

        Args:
            opcodes: list of raw opcode values (ints, e.g. 15 for +)
            width:   total grid width (must be >= 4)
            start_row: first row for code (default 0)
            start_col: first col for code on first row (default 0)

        Returns: (rows_used, end_row, end_col, end_dir)
            rows_used: number of rows consumed by the wrapped code
            end_row/col/dir: where the IP exits after the last opcode
        """
        assert width >= 4, "Need at least 4 columns for wrapping"
        assert start_col < width - 1, "start_col must leave room for mirrors"

        ops = list(opcodes)
        total = len(ops)
        if total == 0:
            return 0, start_row, start_col, DIR_E

        placed = 0
        row = start_row

        # First row: going East, cols start_col to width-2
        first_row_slots = width - 1 - start_col
        n = min(first_row_slots, total - placed)
        for i in range(n):
            self.grid[self._to_flat(row, start_col + i)] = encode_opcode(ops[placed])
            placed += 1

        if placed >= total:
            # All fit on first row, no mirrors needed
            return 1, row, start_col + n - 1, DIR_E

        # Need to wrap: place \ at col width-1 to reflect E→S
        self.grid[self._to_flat(row, width - 1)] = encode_opcode(OPCODES['\\'])

        row_count = 1

        while placed < total:
            row += 1
            row_count += 1

            if row_count % 2 == 0:
                # Odd-indexed row (0-indexed): going West
                # / at col width-1 to reflect S→W
                self.grid[self._to_flat(row, width - 1)] = encode_opcode(OPCODES['/'])

                # Opcodes from col width-2 down to col 1
                slots = width - 2
                n = min(slots, total - placed)
                for i in range(n):
                    self.grid[self._to_flat(row, width - 2 - i)] = encode_opcode(ops[placed])
                    placed += 1

                if placed >= total:
                    return row_count, row, width - 2 - (n - 1), DIR_W

                # / at col 0 to reflect W→S
                self.grid[self._to_flat(row, 0)] = encode_opcode(OPCODES['/'])

            else:
                # Even-indexed row: going East
                # \ at col 0 to reflect S→E
                self.grid[self._to_flat(row, 0)] = encode_opcode(OPCODES['\\'])

                # Opcodes from col 1 to col width-2
                slots = width - 2
                n = min(slots, total - placed)
                for i in range(n):
                    self.grid[self._to_flat(row, 1 + i)] = encode_opcode(ops[placed])
                    placed += 1

                if placed >= total:
                    return row_count, row, 1 + (n - 1), DIR_E

                # \ at col width-1 to reflect E→S
                self.grid[self._to_flat(row, width - 1)] = encode_opcode(OPCODES['\\'])

        return row_count, row, 0, DIR_E  # shouldn't reach here

    # ── Display ────────────────────────────────────────────────────

    def _color(self, text, *styles):
        """Apply ANSI styles if color is enabled."""
        if not self.use_color:
            return text
        prefix = ''.join(ANSI.get(s, '') for s in styles)
        return f"{prefix}{text}{ANSI['reset']}" if prefix else text

    def _cell_char(self, value):
        """Get display character for a grid cell value (always 1-3 chars).
        Uses d_min=4 payload→opcode lookup."""
        payload = _CELL_TO_PAYLOAD[value]
        opcode = _PAYLOAD_TO_OPCODE[payload]
        if opcode in OPCODE_TO_CHAR:
            return OPCODE_TO_CHAR[opcode]
        if value < 100:
            return f'{value:2d}'
        return f'{value:04x}'

    def _cell_display(self, ch):
        """Pad cell char to exactly 3 characters for grid alignment."""
        if len(ch) == 1:
            return f" {ch} "
        elif len(ch) == 2:
            return f" {ch}"
        return ch[:3]

    def display_grid(self):
        """Display the grid with colored pointer markers."""
        self._save_active()

        # Collect all IP positions
        ip_positions = {}  # flat -> (index, dir)
        for idx, ips in enumerate(self.ips):
            flat = self._to_flat(ips['ip_row'], ips['ip_col'])
            ip_positions[flat] = (idx, ips['ip_dir'])

        # Active IP's heads (for head display)
        dir_arrow = DIR_ARROWS[self.ip_dir]
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)
        ix_r, ix_c = self._to_rc(self.ix)
        ex_r, ex_c = self._to_rc(self.ex)

        # Header
        print(f"\n{'═' * 70}")
        if self.n_ips == 1:
            print(f"  Step {self.step_count}   "
                  f"IP=({self.ip_row},{self.ip_col}){dir_arrow}  "
                  f"CL={self.cl}({cl_r},{cl_c})  "
                  f"H0={self.h0}({h0_r},{h0_c})  "
                  f"H1={self.h1}({h1_r},{h1_c})  "
                  f"IX={self.ix}({ix_r},{ix_c})  "
                  f"EX={self.ex}({ex_r},{ex_c})")
        else:
            print(f"  Step {self.step_count}   ({self.n_ips} IPs)")
            for idx, ips in enumerate(self.ips):
                ir, ic = ips['ip_row'], ips['ip_col']
                da = DIR_ARROWS[ips['ip_dir']]
                marker = '*' if idx == self.active_ip else ' '
                print(f"  {marker}IP{idx}=({ir},{ic}){da}  "
                      f"CL={ips['cl']}  H0={ips['h0']}  "
                      f"H1={ips['h1']}  IX={ips['ix']}  "
                      f"EX={ips['ex']}")
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
                ip_here = ip_positions.get(flat)  # (idx, dir) or None
                is_cl = (flat == self.cl)
                is_h0 = (flat == self.h0)
                is_h1 = (flat == self.h1)
                is_ix = (flat == self.ix)
                is_ex = (flat == self.ex)
                is_sel = (self.selection is not None and
                          self.selection[0] <= r <= self.selection[2] and
                          self.selection[1] <= c <= self.selection[3])

                # Build display: direction arrow for IP, char for others
                # _cell_display ensures exactly 3 chars for alignment
                pad = self._cell_display(ch)
                if ip_here is not None:
                    ip_idx, ip_d = ip_here
                    ip_arrow = DIR_ARROWS[ip_d]
                    if ip_idx == 0:
                        cell = self._color(f" {ip_arrow} ", 'bold', 'red')
                    else:
                        cell = self._color(f" {ip_arrow} ", 'bold', 'bg_red')
                elif is_ex and is_cl and is_h0:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'bold', 'yellow')
                elif is_ex and is_cl:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'magenta')
                elif is_ex and is_h0:
                    cell = self._color(f"⟨{ch}⟩" if len(ch) == 1 else f"⟨{ch}", 'cyan')
                elif is_ex:
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
                elif is_ix:
                    if val == 0:
                        cell = self._color(f" o ", 'bold', 'blue')
                    else:
                        cell = self._color(pad, 'bold', 'blue')
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
            legend = f"  {self._color('IP', 'bold', 'red')}  "
            if self.n_ips > 1:
                legend += f"{self._color('IP1+', 'bold', 'bg_red')}  "
            legend += (f"{self._color('CL', 'magenta')}  "
                       f"{self._color('H0', 'cyan')}  "
                       f"{self._color('H1', 'green')}  "
                       f"{self._color('IX', 'blue')}  "
                       f"{self._color('EX', 'yellow')}")
            print(legend)

    def display_values(self):
        """Display raw cell values in a grid (16-bit)."""
        cl_r, cl_c = self._to_rc(self.cl)
        h0_r, h0_c = self._to_rc(self.h0)
        h1_r, h1_c = self._to_rc(self.h1)
        ix_r, ix_c = self._to_rc(self.ix)
        ex_r, ex_c = self._to_rc(self.ex)

        print(f"\n{'─' * 70}")
        print(f"  Values (decimal)   "
              f"IP=({self.ip_row},{self.ip_col})  "
              f"CL={self.cl}  H0={self.h0}  H1={self.h1}  IX={self.ix}  EX={self.ex}")
        print(f"{'─' * 70}")

        # Column headers
        hdr = "    "
        for c in range(self.cols):
            hdr += f"{c:>6}"
        print(hdr)

        for r in range(self.rows):
            line = f" {r:2d}:"
            for c in range(self.cols):
                flat = self._to_flat(r, c)
                val = self.grid[flat]
                is_ip = (r == self.ip_row and c == self.ip_col)

                if is_ip:
                    line += self._color(f"{val:>6}", 'bold', 'red')
                elif val != 0:
                    line += f"{val:>6}"
                else:
                    line += self._color(f"     ·", 'dim')
            print(line)

    def display_both(self):
        """Display grid followed by values."""
        self.display_grid()
        self.display_values()

    # ── Save / Load ────────────────────────────────────────────────

    def save_state(self, filename, hints=None):
        """Save state to file.  Optional hints dict adds key=value lines
        (e.g. {'waste_cleanup': 1}) that the server can read on load."""
        self._save_active()
        with open(filename, 'w') as f:
            f.write(f"# F***brain 2D state\n")
            f.write(f"rows={self.rows}\ncols={self.cols}\n")
            if hints:
                for k, v in hints.items():
                    f.write(f"{k}={v}\n")
            if self.n_ips > 1:
                f.write(f"n_ips={self.n_ips}\n")
                for i, ip in enumerate(self.ips):
                    p = f"ip{i}_"
                    f.write(f"{p}ip_row={ip['ip_row']}\n{p}ip_col={ip['ip_col']}\n")
                    f.write(f"{p}ip_dir={ip['ip_dir']}\n")
                    f.write(f"{p}cl={ip['cl']}\n{p}h0={ip['h0']}\n")
                    f.write(f"{p}h1={ip['h1']}\n{p}ix={ip['ix']}\n")
                    f.write(f"{p}ix_dir={ip.get('ix_dir', DIR_E)}\n{p}ix_vdir={ip.get('ix_vdir', DIR_S)}\n{p}ex={ip['ex']}\n")
            else:
                # Single-IP: unprefixed keys (backward compatible)
                ip = self.ips[0]
                f.write(f"ip_row={ip['ip_row']}\nip_col={ip['ip_col']}\n")
                f.write(f"ip_dir={ip['ip_dir']}\n")
                f.write(f"cl={ip['cl']}\nh0={ip['h0']}\n")
                f.write(f"h1={ip['h1']}\nix={ip['ix']}\n")
                f.write(f"ix_dir={ip.get('ix_dir', DIR_E)}\nix_vdir={ip.get('ix_vdir', DIR_S)}\nex={ip['ex']}\n")
            f.write(f"step={self.step_count}\n")
            f.write(f"grid={','.join(str(v) for v in self.grid)}\n")

    def load_state(self, filename):
        data = {}
        with open(filename, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    data[k] = v

        self.rows = int(data.get('rows', DEFAULT_ROWS))
        self.cols = int(data.get('cols', DEFAULT_COLS))
        self.grid_size = self.rows * self.cols
        self.step_count = int(data.get('step', 0))

        if 'grid' in data:
            vals = [int(x) for x in data['grid'].split(',')]
            self.grid = vals[:self.grid_size]
            if len(self.grid) < self.grid_size:
                self.grid.extend([0] * (self.grid_size - len(self.grid)))
        else:
            self.grid = [0] * self.grid_size

        n_ips = int(data.get('n_ips', 1))
        self.ips = []

        if n_ips == 1 and 'ip_row' in data:
            # Legacy single-IP format (unprefixed keys)
            # Backward compat: try new keys first, fall back to old (h2/gp/h2_dir/h2_vdir)
            self.ips.append({
                'ip_row': int(data.get('ip_row', 0)),
                'ip_col': int(data.get('ip_col', 0)),
                'ip_dir': int(data.get('ip_dir', DIR_E)),
                'h0': int(data.get('h0', 0)),
                'h1': int(data.get('h1', 0)),
                'ix': int(data.get('ix', data.get('h2', 0))),
                'ix_dir': int(data.get('ix_dir', data.get('h2_dir', DIR_E))),
                'ix_vdir': int(data.get('ix_vdir', data.get('h2_vdir', DIR_S))),
                'cl': int(data.get('cl', 0)),
                'ex': int(data.get('ex', data.get('gp', 0))),
            })
        else:
            for i in range(n_ips):
                p = f"ip{i}_"
                self.ips.append({
                    'ip_row': int(data.get(f'{p}ip_row', 0)),
                    'ip_col': int(data.get(f'{p}ip_col', 0)),
                    'ip_dir': int(data.get(f'{p}ip_dir', DIR_E)),
                    'h0': int(data.get(f'{p}h0', 0)),
                    'h1': int(data.get(f'{p}h1', 0)),
                    'ix': int(data.get(f'{p}ix', data.get(f'{p}h2', 0))),
                    'ix_dir': int(data.get(f'{p}ix_dir', data.get(f'{p}h2_dir', DIR_E))),
                    'ix_vdir': int(data.get(f'{p}ix_vdir', data.get(f'{p}h2_vdir', DIR_S))),
                    'cl': int(data.get(f'{p}cl', 0)),
                    'ex': int(data.get(f'{p}ex', data.get(f'{p}gp', 0))),
                })

        self.n_ips = len(self.ips)
        self.active_ip = 0
        self._load_active(0)

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
        sim.cl, sim.h0, sim.h1, sim.ix, sim.ex = 0, 0, 0, 0, 0
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
        sim.ix = 0
        sim.ex = 0
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
        sim.cl, sim.h0, sim.h1, sim.ix, sim.ex = 0, 0, 0, 0, 0
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
        sim.ix = 0
        sim.ex = 0
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
        sim.ix = 0
        sim.ex = 0

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
  EX movement (exteroceptor):
    { (32)  North    } (31)  South    ] (29)  East    [ (30)  West
  IX movement (interoceptor):
    H (49)  North    h (50)  South    a (51)  East    d (52)  West
  Byte-level data:
    + (15)  [H0]++                     - (16)  [H0]--
    . (17)  [H0] += [H1]              , (18)  [H0] -= [H1]
    X (19)  swap([H0], [H1])           F (20)  if [CL]!=0: swap([H0],[H1])
  Bit-level data (v1.6):
    x (39)  [H0] ^= [H1]  (XOR, self-inverse)
    r (40)  [H0] rotate right 1 bit   l (41)  [H0] rotate left 1 bit
    f (42)  if [CL]&1: swap([H0],[H1]) (bit-0 Fredkin)
    z (43)  swap(bit0 [H0], bit0 [EX]) (bit-level EX swap)
  CL data:
    G (21)  swap(H1_reg, [H0])         T (22)  swap([CL], [H0])
  EX data (breadcrumbs):
    P (27)  [EX]++  (leave breadcrumb)
    Q (28)  [EX]--  (erase breadcrumb)
  EX-conditional mirrors:
    ( (34)  \\ if [EX]!=0               ) (35)  \\ if [EX]=0
    $ (37)  /  if [EX]!=0               # (36)  /  if [EX]=0
  CL/EX swap:
    K (33)  swap(CL_register, GP_register)
  Data/EX swap:
    Z (38)  swap([H0], [EX])  (byte-level, zero a variable)
  IX data (interoceptor, v1.9):
    m (53)  [H0] ^= [IX]  (raw XOR, self-inv) M (54)  [H0] -= [IX]  (Δp payload sub)
    j (55)  [IX] ^= [H0]  (write-back, raw 16-bit, self-inverse)
    V (56)  swap([CL], [IX])  (test bridge, self-inverse)
  Notation: H0 = head position, [H0] = value at that position

  / reflect: E<->N  S<->W     \\ reflect: E<->S  N<->W

Commands:
  tape <code>          Load code linearly (resets state)
  row <r> [c] <code>   Place code along row r (from col c, default 0)
  col <c> [r] <code>   Place code down col c (from row r, default 0)
  data <r> <c> <v>...  Set raw values at (r,c); v can be number or opcode char
  cell <r> <c> [v]     Get/set one cell

  ip                   Show all IPs with state
  ip <index>           Select active IP (for head commands)
  ip <r> <c>           Set active IP position
  addip [r c dir]      Add new IP (default: 0 0 E)
  rmip <index>         Remove an IP
  dir <N/E/S/W>        Set active IP direction
  cl [r c | flat]      Set/show CL (on active IP)
  h0 [r c | flat]      Set/show H0 (on active IP)
  h1 [r c | flat]      Set/show H1 (on active IP)
  ix [r c | flat]      Set/show IX (on active IP)
  ex [r c | flat]      Set/show EX (on active IP)

  step / s [n]         Forward n steps — all IPs interleaved (default 1)
  back / b [n]         Reverse n steps — all IPs (default 1)
  run [n]              Run n forward — all IPs (default 100)
  runback [n]          Run n backward — all IPs (default 100)
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
                            sim.grid[flat] = int(v) & CELL_MASK
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
                            sim.grid[flat] = encode_opcode(OPCODES[v])
                        else:
                            sim.grid[flat] = int(v) & CELL_MASK
                    val = sim.grid[flat]
                    pl = _CELL_TO_PAYLOAD[val]
                    op = _PAYLOAD_TO_OPCODE[pl]
                    ch = OPCODE_TO_CHAR.get(op, f'data(pl={pl})')
                    print(f"grid[{r},{c}] = 0x{val:04x} (payload={pl}, op={op} '{ch}')")
                else:
                    print("Usage: cell <r> <c> [value]")

            # ── IP position / multi-IP management ──
            elif cmd == 'ip':
                if not args:
                    # Show all IPs
                    sim._save_active()
                    for i, ipstate in enumerate(sim.ips):
                        r, c = ipstate['ip_row'], ipstate['ip_col']
                        d = DIR_ARROWS[ipstate['ip_dir']]
                        active = " *" if i == sim.active_ip else ""
                        h2d = DIR_ARROWS[ipstate.get('ix_dir', DIR_E)]
                        h2v = DIR_ARROWS[ipstate.get('ix_vdir', DIR_S)]
                        print(f"  IP{i}: ({r},{c}) {d}  "
                              f"CL={ipstate['cl']} H0={ipstate['h0']} "
                              f"H1={ipstate['h1']} IX={ipstate['ix']}({h2d},{h2v}) "
                              f"EX={ipstate['ex']}{active}")
                elif len(args) == 1 and args[0].isdigit():
                    # Select active IP: ip 0, ip 1, etc.
                    idx = int(args[0])
                    if 0 <= idx < sim.n_ips:
                        sim._activate_ip(idx)
                        print(f"Switched to IP{idx}")
                    else:
                        print(f"Invalid IP index: {idx} (have {sim.n_ips} IPs)")
                elif len(args) >= 2:
                    # Set position: ip <r> <c>
                    sim.ip_row = int(args[0]) % sim.rows
                    sim.ip_col = int(args[1]) % sim.cols
                    sim._save_active()
                print(f"IP{sim.active_ip} = ({sim.ip_row},{sim.ip_col}) "
                      f"{DIR_ARROWS[sim.ip_dir]}")
                sim.display_grid()

            # ── Add IP ──
            elif cmd == 'addip':
                ip_r = int(args[0]) % sim.rows if len(args) >= 1 else 0
                ip_c = int(args[1]) % sim.cols if len(args) >= 2 else 0
                ip_d = parse_dir(args[2]) if len(args) >= 3 else DIR_E
                idx = sim.add_ip(ip_row=ip_r, ip_col=ip_c, ip_dir=ip_d)
                print(f"Added IP{idx} at ({ip_r},{ip_c}) "
                      f"{DIR_ARROWS[ip_d]}")
                sim.display_grid()

            # ── Remove IP ──
            elif cmd == 'rmip':
                if args and args[0].isdigit():
                    idx = int(args[0])
                    if sim.n_ips <= 1:
                        print("Cannot remove the last IP")
                    elif 0 <= idx < sim.n_ips:
                        sim._save_active()
                        sim.ips.pop(idx)
                        sim.n_ips = len(sim.ips)
                        # Adjust active_ip if needed
                        if sim.active_ip >= sim.n_ips:
                            sim.active_ip = sim.n_ips - 1
                        elif sim.active_ip == idx:
                            sim.active_ip = min(idx, sim.n_ips - 1)
                        sim._load_active(sim.active_ip)
                        print(f"Removed IP{idx} ({sim.n_ips} IPs remain)")
                    else:
                        print(f"Invalid IP index: {idx}")
                else:
                    print("Usage: rmip <index>")
                sim.display_grid()

            # ── Direction ──
            elif cmd == 'dir':
                if args:
                    d = parse_dir(args[0])
                    if d is not None:
                        sim.ip_dir = d
                        sim._save_active()
                    else:
                        print(f"Unknown direction: {args[0]}")
                print(f"DIR = {DIR_NAMES[sim.ip_dir]} "
                      f"{DIR_ARROWS[sim.ip_dir]}")
                sim.display_grid()

            # ── CL / H0 / H1 / IX / EX ──
            elif cmd in ('cl', 'h0', 'h1', 'ix', 'ex'):
                if args:
                    pos, _ = parse_pos(sim, args)
                    if pos is not None:
                        setattr(sim, cmd, pos)
                        sim._save_active()
                    else:
                        print(f"Invalid position: {' '.join(args)}")
                val = getattr(sim, cmd)
                r, c = sim._to_rc(val)
                label = cmd.upper()
                print(f"{label} = {val} ({r},{c})  "
                      f"grid[{label}] = {sim.grid[val]}"
                      f"  (IP{sim.active_ip})")
                sim.display_grid()

            # ── Step forward ──
            elif cmd in ('step', 's'):
                n = int(args[0]) if args else 1
                for _ in range(n):
                    sim.step_all()
                if not sim.trace:
                    sim.display_grid()

            # ── Step backward ──
            elif cmd in ('back', 'b', 'r'):
                n = int(args[0]) if args else 1
                for _ in range(n):
                    sim.step_back_all()
                if not sim.trace:
                    sim.display_grid()

            # ── Run forward ──
            elif cmd == 'run':
                n = int(args[0]) if args else 100
                for _ in range(n):
                    sim.step_all()
                print(f"Ran {n} steps forward")
                sim.display_grid()

            # ── Run backward ──
            elif cmd == 'runback':
                n = int(args[0]) if args else 100
                actual = 0
                for _ in range(n):
                    if sim.step_count > 0:
                        sim.step_back_all()
                        actual += 1
                    else:
                        break
                print(f"Ran {actual} steps backward")
                sim.display_grid()

            # ── Reset to step zero ──
            elif cmd in ('zero', 'z'):
                n = sim.step_count
                for _ in range(n):
                    sim.step_back_all()
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
                    if load_example(sim, args[0]):
                        sim.n_ips = 1
                        sim.active_ip = 0
                        sim.ips = [sim._capture_ip_state()]
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

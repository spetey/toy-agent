#!/usr/bin/env python3
"""
fb1d: 16-bit reversible bracket language on a 1D tape
Authored or modified by Claude
Version: 2026-09-30 v0.3 (M1 + M2 `( )` and `P Q` extensions, docs/fb1d-port-plan.md)
         2026-10-12 v0.4: readable op names and `#` comments in program text
         2026-10-12 v0.5: multi-block noise, incremental match cache (M3)
         2026-10-13 v0.6: `a1[ ]a1` and `b1[ ]b1` brackets that enter on
                          nonzero and repeat while zero (M4)

fb1d is fb1d8 (formerly RBFF; see fb1d8.py, docs/fb1d8_notes.md) with
fb2d's cells and interoceptor transplanted in, so that the Wikivore can
be ported to 1D.

Cells: 16-bit systematic Hamming(16,11) SECDED, exactly fb2d's encoding
(tables imported from fb2d.py).  Instruction fetch follows fb2d v1.17:
the raw 11 data bits go straight to the [11,6,4] nearest-codeword opcode
decoder, so parity bits never affect which op runs, a 1-bit data flip
still runs the same op and a 2-bit data flip gives NOP.  Each fb1d op
uses the fb2d opcode number of the same character (so `[` is fb2d 30,
`A` is fb2d 57, ...).  Every other payload, including the codewords of
fb2d-only ops, is NOP.

State per IP: a, b (data heads), ix (interoceptor), p (instruction
pointer).  All wrap modulo N.  The tape is shared.  Each step executes
the op at tape[p], then p += 1 (a jump lands one past the partner).

  <  a -= 1                 >  a += 1
  {  b -= 1                 }  b += 1
  B  ix -= 1                A  ix += 1
  -  tape[a] payload -= 1   +  tape[a] payload += 1   (fb2d delta-p ops)
  .  tape[b] ^= tape[a]     ,  tape[a] ^= tape[b]     (raw 16-bit XOR)
  m  tape[a] ^= tape[ix]                              (copy-in / uncompute)
  I  tape[a] ^= syndrome_5bit(tape[ix])               (fb2d I)
  V  tape[a] ^= 1 << syndrome_4bit(tape[ix])          (fb2d V)
  j  tape[ix] ^= tape[a]                              (write-back)
  [  if payload(tape[a]) != 0: p = matching ]   (enter body only when zero)
  ]  if payload(tape[a]) != 0: p = matching [   (repeat while nonzero)
  (  if payload(tape[b]) != 0: p = matching )   (same, testing b's cell)
  )  if payload(tape[b]) != 0: p = matching (
  P  tape[b] payload += 1   Q  tape[b] payload -= 1   (fb2d's P/Q)
  ?  if payload(tape[a]) == 0: p = matching !   (enter body only when nonzero)
  !  if payload(tape[a]) == 0: p = matching ?   (repeat while zero)
  %  if payload(tape[b]) == 0: p = matching &   (same, testing b's cell)
  &  if payload(tape[b]) == 0: p = matching %
  anything else: no-op

"payload != 0" means tape[a] & DATA_MASK != 0, as in fb2d's mirrors.
`( )` were added in M2 (v0.2): a block that tests one head may move the
other head freely, which the immunity gadget's merge needs (see
programs/fb1d-immunity-m2.py and docs/fb1d-port-plan.md).  The two
bracket families are matched independently (own stacks); barriers
empty both.  `P` / `Q` (v0.3) let b be a roaming garbage pointer with
a fixed stomach under a, as fb2d's EX; the gadget's clean-path trace
is then `P` on b's cell instead of moving the whole working frame.
`? ! % &` (v0.6, M4) are the opposite-polarity families `a1[ ]a1` and
`b1[ ]b1`: a block that is entered when the tested cell is nonzero and
repeated while it is zero.  Reversible for the same reason as `[ ]`
(the skip condition at the opener equals the repeat condition at the
closer); matched on their own stacks.  They let a walk start from a
nonzero cell and stop on the first nonzero cell, which the metabolism
needs (docs/fb1d-m4-metabolism-design.md).  fb1d has 24 ops.

Readable names (v0.4).  Program text may spell each op by its symbol
above or by a readable name, separated from its neighbours by
whitespace; `#` starts a comment to the end of the line.  The names
only change how programs are written and shown: the cells, and so the
opcode numbers shared with fb2d, are the same either way.

  a< a>  b< b>  i< i>     move a / b / ix        (< >  { }  B A)
  a- a+  b- b+            payload -1 / +1        (- +  Q P)
  b^=a   a^=b             XOR, left side changes (. ,)
  a^=i   i^=a             copy in / write back   (m j)
  syn    mask             a ^= syndrome5 / correction mask of ix's cell (I V)
  a0[ ]a0  b0[ ]b0        brackets testing a / b ([ ] ( )); the 0 is the
                          value that lets the IP in at a0[ and out at ]a0
                          (any nonzero value sends it round again)
  a1[ ]a1  b1[ ]b1        the zero-jumping families (? ! % &): the IP gets
                          in at a1[ and out at ]a1 on any nonzero value,
                          and zero sends it round again

Every name contains a character that is not a symbol, so a token is a
name if it is in the table and otherwise a run of symbols (`[>+<]`
still works); anything else is an error.

Guards (each makes the op a NOP, in both step and step_back):
  * a write to tape[p] of the executing IP (executing-cell guard)
  * . ,   when a == b
  * m I V j  when a == ix
Writes to another IP's p are allowed; step_back_all undoes IPs in
reverse order, which restores that IP's cell before it is read.

Bracket matching: stack matching over the linear tape 0..N-1 (no wrap).
Unmatched brackets are NOPs.  Boundary barrier: a cell whose raw
payload is 2047 (0xFFFF when clean, shown `~`) empties the stack, so
brackets never pair across a boundary.  Matching only depends on the
tape and brackets never write, so reversibility does not depend on the
barrier rule.  Machine(barriers=False) turns it off for comparisons.

Multi-IP: step_all() steps IP0, IP1, ... then applies noise for that
round; step_back_all() undoes noise then the IPs in reverse order.

Noise: fb2d's NoisePool (pools.py) over a flat address range, or over
several equal-length blocks (M3: the two code blocks), rate in flips per
1M step_alls over the whole region, deterministic by seed and fully
reversible.

Bracket matching is cached; the cache is kept incrementally (a sorted
list of the bracket and barrier addresses, v0.5) so that a write which
changes a cell's bracket class costs O(brackets) at the next bracket,
not O(N).  Edit the tape through set_cell / flip / poke, or call
invalidate() after editing it directly.

Usage:
  python3 fb1d.py              REPL (type help)
  python3 fb1d.py --test       verification suite (~1 min)
  python3 fb1d.py --test --long   adds the 2M-round noisy round trip
"""

import os
import sys
import bisect
import random
import itertools

try:
    import readline  # noqa: F401  (line editing in the REPL)
except ImportError:
    pass

from fb2d import (OPCODES, OPCODE_PAYLOADS, _PAYLOAD_TO_OPCODE,
                  _CELL_TO_PAYLOAD_RAW, DATA_MASK, INC_XOR, DEC_XOR,
                  SYNDROME_XOR_MASK, CORRECTION_XOR_MASK,
                  hamming_encode, hamming_syndrome, encode_opcode)
from pools import NoisePool
import fb1d8

# ─── Opcodes ────────────────────────────────────────────────────────

OP_CHARS = '<>{}BA-+.,mIVj[]()PQ?!%&'
(OP_AL, OP_AR, OP_BL, OP_BR, OP_XL, OP_XR, OP_DEC, OP_INC, OP_DOT,
 OP_COMMA, OP_M, OP_I, OP_V, OP_J, OP_LB, OP_RB, OP_LP, OP_RP,
 OP_P, OP_Q, OP_LB1, OP_RB1, OP_LP1, OP_RP1) = range(1, 25)
# bracket op -> (tests b?, jumps on zero?)
BRACKETS = {OP_LB: (False, False), OP_RB: (False, False),
            OP_LP: (True, False), OP_RP: (True, False),
            OP_LB1: (False, True), OP_RB1: (False, True),
            OP_LP1: (True, True), OP_RP1: (True, True)}
OP_DOC = [
    ('<', 'a -= 1'), ('>', 'a += 1'),
    ('{', 'b -= 1'), ('}', 'b += 1'),
    ('B', 'ix -= 1'), ('A', 'ix += 1'),
    ('-', 'payload(tape[a]) -= 1'), ('+', 'payload(tape[a]) += 1'),
    ('.', 'tape[b] ^= tape[a]      (NOP if a == b)'),
    (',', 'tape[a] ^= tape[b]      (NOP if a == b)'),
    ('m', 'tape[a] ^= tape[ix]     (NOP if a == ix)'),
    ('I', 'tape[a] ^= syndrome_5bit(tape[ix])'),
    ('V', 'tape[a] ^= 1 << syndrome_4bit(tape[ix])'),
    ('j', 'tape[ix] ^= tape[a]     (NOP if a == ix)'),
    ('[', 'if payload(tape[a]) != 0: jump to matching ]'),
    (']', 'if payload(tape[a]) != 0: jump to matching ['),
    ('(', 'if payload(tape[b]) != 0: jump to matching )   (M2: b-tested bracket)'),
    (')', 'if payload(tape[b]) != 0: jump to matching (   (M2: b-tested bracket)'),
    ('P', 'payload(tape[b]) += 1   (M2: fb2d P, for a roaming garbage pointer)'),
    ('Q', 'payload(tape[b]) -= 1   (inverse of P)'),
    ('?', 'if payload(tape[a]) == 0: jump to matching !   (M4: a1[, enter on nonzero)'),
    ('!', 'if payload(tape[a]) == 0: jump to matching ?   (M4: ]a1, repeat while zero)'),
    ('%', 'if payload(tape[b]) == 0: jump to matching &   (M4: b1[, enter on nonzero)'),
    ('&', 'if payload(tape[b]) == 0: jump to matching %   (M4: ]b1, repeat while zero)'),
    ('other', 'no-op (NOP filler `_` = payload 1017, boundary `~` = 0xFFFF)'),
]

# fb1d op index -> fb2d opcode number (same character)
FB2D_NUM = {i + 1: OPCODES[ch] for i, ch in enumerate(OP_CHARS)}
OP_CELL = {ch: encode_opcode(FB2D_NUM[i + 1]) for i, ch in enumerate(OP_CHARS)}

NOP_FILLER = hamming_encode(1017)
BOUNDARY = 0xFFFF
assert hamming_encode(2047) == BOUNDARY

# cell value -> fb1d op index (0 = NOP), via raw payload (fb2d v1.17 fetch)
_FB2D_TO_OP = [0] * 64
for _op, _num in FB2D_NUM.items():
    _FB2D_TO_OP[_num] = _op
CELL_OP = [_FB2D_TO_OP[_PAYLOAD_TO_OPCODE[_CELL_TO_PAYLOAD_RAW[v]]]
           for v in range(65536)]
# bracket-matching class: 1 = '[', 2 = ']', 4 = '(', 5 = ')', 6 = '?',
# 7 = '!', 8 = '%', 9 = '&', 3 = barrier, 0 = other.  The four bracket
# families are matched independently (each closer's class is its
# opener's plus one).
_OPENERS = {OP_LB: 1, OP_LP: 4, OP_LB1: 6, OP_LP1: 8}
_CLOSERS = {OP_RB: 2, OP_RP: 5, OP_RB1: 7, OP_RP1: 9}
CELL_CLASS = [_OPENERS.get(CELL_OP[v]) or _CLOSERS.get(CELL_OP[v]) or
              (3 if _CELL_TO_PAYLOAD_RAW[v] == 2047 else 0)
              for v in range(65536)]
del _op, _num


def payload(v):
    return _CELL_TO_PAYLOAD_RAW[v]


def cell_char(v):
    op = CELL_OP[v]
    if op:
        return OP_CHARS[op - 1]
    if v == 0:
        return '0'
    if v == NOP_FILLER:
        return '_'
    if _CELL_TO_PAYLOAD_RAW[v] == 2047:
        return '~'
    return '·'


def is_dirty(v):
    s, pa = hamming_syndrome(v)
    return bool(s or pa)


# Readable names for the ops (see the module docstring).  Each contains a
# character that is not a symbol, so names and runs of symbols never clash.
OP_NAMES = {'<': 'a<', '>': 'a>', '{': 'b<', '}': 'b>', 'B': 'i<', 'A': 'i>',
            '-': 'a-', '+': 'a+', 'Q': 'b-', 'P': 'b+',
            '.': 'b^=a', ',': 'a^=b', 'm': 'a^=i', 'j': 'i^=a',
            'I': 'syn', 'V': 'mask',
            '[': 'a0[', ']': ']a0', '(': 'b0[', ')': ']b0',
            '?': 'a1[', '!': ']a1', '%': 'b1[', '&': ']b1'}
NAME_TO_OP = {name: ch for ch, name in OP_NAMES.items()}
assert set(OP_NAMES) == set(OP_CHARS)
SYMBOL_CELL = dict(OP_CELL, **{'~': BOUNDARY, '_': NOP_FILLER, '0': 0})
assert not any(set(n) <= set(SYMBOL_CELL) for n in NAME_TO_OP)


def assemble(text):
    """Program text -> list of cells.  Tokens are separated by whitespace;
    `#` starts a comment to the end of the line.  A token is either a
    readable op name (OP_NAMES) or a run of symbols: op chars, `~`
    boundary, `_` NOP filler, `0` zero.  Anything else is an error."""
    out = []
    for line in text.splitlines():
        for tok in line.split('#', 1)[0].split():
            if tok in NAME_TO_OP:
                out.append(OP_CELL[NAME_TO_OP[tok]])
                continue
            for ch in tok:
                if ch not in SYMBOL_CELL:
                    raise ValueError(f"not an fb1d op or name: {tok!r} "
                                     "(separate names with spaces)")
                out.append(SYMBOL_CELL[ch])
    return out


def cell_name(v):
    """Readable name of the op in cell v, else its symbol (`~`, `_`, `0`, `·`)."""
    op = CELL_OP[v]
    return OP_NAMES[OP_CHARS[op - 1]] if op else cell_char(v)


def disassemble(cells, names=True):
    """Cells -> program text, one token per cell, space separated.  Cells
    that are not ops or `~ _ 0` come out as `·`, which assemble rejects."""
    return ' '.join(cell_name(v) if names else cell_char(v) for v in cells)


def match_table(tape, barriers=True, special=None):
    """Stack matching over linear order, one stack per bracket family
    (`[ ]` and `? !` on a, `( )` and `% &` on b).  Returns dict pos ->
    partner.  A barrier empties every stack.  `special`, if given, is
    the sorted list of the addresses whose CELL_CLASS is nonzero
    (brackets and barriers); the Machine keeps it up to date so a
    rebuild costs O(brackets), not O(N)."""
    stacks = {1: [], 4: [], 6: [], 8: []}
    mt = {}
    if special is None:
        special = range(len(tape))
    for i in special:
        c = CELL_CLASS[tape[i]]
        if not c:
            continue
        if c == 3:
            if barriers:
                for st in stacks.values():
                    st.clear()
        elif c in stacks:
            stacks[c].append(i)
        else:
            st = stacks[c - 1]
            if st:
                j = st.pop()
                mt[i] = j
                mt[j] = i
    return mt


# ─── Machine ────────────────────────────────────────────────────────

class IP:
    __slots__ = ('a', 'b', 'ix', 'p')

    def __init__(self, a=0, b=0, ix=0, p=0):
        self.a, self.b, self.ix, self.p = a, b, ix, p

    def tup(self):
        return (self.a, self.b, self.ix, self.p)


class Machine:
    def __init__(self, size=256, barriers=True):
        self.tape = [0] * size
        self.ips = [IP()]
        self.barriers = barriers
        self.rounds = 0          # step_all count (also the noise index)
        self._mt = None          # bracket match table, rebuilt lazily
        self._special = None     # sorted addresses of brackets and barriers
        self.noise = None        # NoisePool, or None
        self.noise_on = False
        self.noise_bases = [0]   # noise block start addresses (see set_noise)
        self.code_end = 0
        self.segments = []
        self.example = None
        self.loop_stop = 1       # REPL `loop`: run until IP0's p is here

    @property
    def N(self):
        return len(self.tape)

    def matches(self):
        if self._mt is None:
            if self._special is None:
                self._special = [i for i, v in enumerate(self.tape)
                                 if CELL_CLASS[v]]
            self._mt = match_table(self.tape, self.barriers, self._special)
        return self._mt

    def invalidate(self):
        """Call after editing self.tape directly (O(N) on the next match)."""
        self._mt = None
        self._special = None

    def _reclass(self, addr, old, new):
        """Keep the match cache consistent with tape[addr] going old -> new."""
        c0, c1 = CELL_CLASS[old], CELL_CLASS[new]
        if c0 == c1:
            return
        self._mt = None
        sp = self._special
        if sp is not None:
            if c0 and not c1:
                del sp[bisect.bisect_left(sp, addr)]
            elif c1 and not c0:
                bisect.insort(sp, addr)

    def set_cell(self, addr, val):
        """Store a raw 16-bit value, keeping the match cache incremental."""
        self._reclass(addr, self.tape[addr], val)
        self.tape[addr] = val

    def flip(self, addr, bit):
        self.set_cell(addr, self.tape[addr] ^ (1 << bit))

    def state(self):
        return (tuple(self.tape), tuple(ip.tup() for ip in self.ips),
                self.rounds)

    # ------------------------------------------------------------ core
    def _write(self, ip, addr, val):
        if addr == ip.p:                     # executing-cell guard
            return
        t = self.tape
        self._reclass(addr, t[addr], val)
        t[addr] = val

    def _exec(self, ip, inverse=False):
        """Execute tape[ip.p] in place.  Only brackets change ip.p."""
        t, N = self.tape, len(self.tape)
        op = CELL_OP[t[ip.p]]
        if not op:
            return
        a = ip.a
        if op <= OP_XR:                      # head moves
            d = -1 if op in (OP_AL, OP_BL, OP_XL) else 1
            if inverse:
                d = -d
            if op <= OP_AR:
                ip.a = (a + d) % N
            elif op <= OP_BR:
                ip.b = (ip.b + d) % N
            else:
                ip.ix = (ip.ix + d) % N
        elif op == OP_INC or op == OP_DEC:
            up = (op == OP_INC) != inverse
            v = t[a]
            self._write(ip, a, v ^ (INC_XOR if up else DEC_XOR)[_CELL_TO_PAYLOAD_RAW[v]])
        elif op == OP_P or op == OP_Q:       # same, on b's cell
            up = (op == OP_P) != inverse
            v = t[ip.b]
            self._write(ip, ip.b, v ^ (INC_XOR if up else DEC_XOR)[_CELL_TO_PAYLOAD_RAW[v]])
        elif op == OP_DOT:
            if a != ip.b:
                self._write(ip, ip.b, t[ip.b] ^ t[a])
        elif op == OP_COMMA:
            if a != ip.b:
                self._write(ip, a, t[a] ^ t[ip.b])
        elif op <= OP_J:                     # m I V j
            x = ip.ix
            if a == x:
                return
            if op == OP_M:
                self._write(ip, a, t[a] ^ t[x])
            elif op == OP_I:
                self._write(ip, a, t[a] ^ SYNDROME_XOR_MASK[t[x]])
            elif op == OP_V:
                self._write(ip, a, t[a] ^ CORRECTION_XOR_MASK[t[x]])
            else:
                self._write(ip, x, t[x] ^ t[a])
        else:                                # brackets
            on_b, on_zero = BRACKETS[op]
            nz = t[ip.b if on_b else a] & DATA_MASK
            if (not nz) if on_zero else nz:
                mt = self.matches()
                if ip.p in mt:
                    ip.p = mt[ip.p]

    def step_ip(self, i):
        ip = self.ips[i]
        self._exec(ip)
        ip.p = (ip.p + 1) % len(self.tape)

    def step_back_ip(self, i):
        ip = self.ips[i]
        ip.p = (ip.p - 1) % len(self.tape)
        self._exec(ip, inverse=True)

    def _noise_flat(self, block, col):
        return self.noise_bases[block] + col

    def _noise_done(self, action):
        if action:
            addr = self._noise_flat(action[0], action[1])
            new = self.tape[addr]
            self._reclass(addr, new ^ (1 << action[2]), new)

    def step_all(self):
        for i in range(len(self.ips)):
            self.step_ip(i)
        if self.noise_on:
            self._noise_done(self.noise.apply_forward(
                self.rounds, self.tape, self._noise_flat, self.noise_rows))
        self.rounds += 1

    def step_back_all(self):
        self.rounds -= 1
        if self.noise_on:
            self._noise_done(self.noise.undo_at(
                self.rounds, self.tape, self._noise_flat, self.noise_rows))
        for i in range(len(self.ips) - 1, -1, -1):
            self.step_back_ip(i)

    step = step_all
    step_back = step_back_all

    def run(self, max_rounds, stop_at=None, ip=0):
        """Run up to max_rounds; stop early if ips[ip].p reaches stop_at."""
        n = 0
        while n < max_rounds:
            self.step_all()
            n += 1
            if stop_at is not None and self.ips[ip].p == stop_at:
                break
        return n

    # ----------------------------------------------------------- noise
    def set_noise(self, flips_per_1M, lo=0, hi=None, seed=42, kind='any',
                  blocks=None):
        """Configure noise over tape[lo..hi] (inclusive), or over several
        equal-length blocks [(lo, hi), ...] (M3: both code blocks, with
        the stomach and fuel between them spared).  The rate is flips per
        1M rounds over the whole region, as in fb2d.  Resets the pool, so
        only call this when rounds == 0 or you won't step back past
        this point."""
        if blocks is None:
            blocks = [(lo, self.N - 1 if hi is None else hi)]
        n = blocks[0][1] - blocks[0][0] + 1
        if any(b[1] - b[0] + 1 != n for b in blocks):
            raise ValueError("noise blocks must have equal lengths")
        self.noise_bases = [b[0] for b in blocks]
        self.noise_rows = list(range(len(blocks)))
        self.noise = NoisePool(seed=seed, n_code_rows=len(blocks),
                               grid_cols=self.N, noise_type=kind,
                               flips_per_1M=flips_per_1M,
                               col_min=0, col_max=n - 1)
        self.noise_on = flips_per_1M > 0

    # --------------------------------------------------------- loading
    def load_program(self, segments, size=None, code_at=0):
        self.tape = [0] * (size or self.N)
        code = assemble('\n'.join(text for _, text in segments))
        self.tape[code_at:code_at + len(code)] = code
        self.segments = segments
        self.code_end = code_at + len(code)
        self.ips = [IP()]
        self.rounds = 0
        self.invalidate()

    def poke(self, addr, value):
        """Store an 11-bit payload as a clean codeword."""
        self.set_cell(addr, hamming_encode(value))

    def peek(self, addr):
        return _CELL_TO_PAYLOAD_RAW[self.tape[addr]]

    def segment_at(self, p):
        off = 0
        for label, text in self.segments:
            n = len(assemble(text))
            if off <= p < off + n:
                return label, p - off
            off += n
        return None, None

    # --------------------------------------------------------- display
    def display(self, lo=None, hi=None, width=16, color=True):
        red = '\033[31m' if color else ''
        off = '\033[0m' if color else ''
        heads = ' '.join(f"IP{i}: p={ip.p} a={ip.a} b={ip.b} ix={ip.ix}"
                         for i, ip in enumerate(self.ips))
        print(f"round {self.rounds}   {heads}")
        if self.noise is not None:
            print(f"  noise: {self.noise.flips_per_1M}/1M "
                  f"{'on' if self.noise_on else 'off'}, "
                  f"injected {self.noise.total_injected}")
        for start in range(0, self.code_end, 64):
            end = min(self.code_end, start + 64)
            line = ''
            for addr in range(start, end):
                ch = cell_char(self.tape[addr])
                line += f"{red}{ch}{off}" if is_dirty(self.tape[addr]) else ch
            print(f"  code[{start:4d}]: {line}")
            carets = [' '] * (end - start)
            for i, ip in enumerate(self.ips):
                if start <= ip.p < end:
                    carets[ip.p - start] = str(i) if len(self.ips) > 1 else '^'
            if ''.join(carets).strip():
                print(' ' * 14 + ''.join(carets))
        ip0 = self.ips[0]
        label, o = self.segment_at(ip0.p)
        if label:
            print(f"  IP0 in segment: {label} (+{o})  "
                  f"next op: {cell_char(self.tape[ip0.p])!r}")
        if lo is None:
            lo = self.code_end
        if hi is None:
            hi = lo + 48
        hi = min(self.N, hi)
        for start in range(lo, hi, width):
            addrs = range(start, min(hi, start + width))
            cells = ''
            for addr in addrs:
                s = f"{self.peek(addr):5d}"
                cells += f"{red}{s}{off}" if is_dirty(self.tape[addr]) else s
            print(f"  {start:4d}:" + cells)
            marks = ''
            for addr in addrs:
                m = ''
                for i, ip in enumerate(self.ips):
                    tag = '' if len(self.ips) == 1 else str(i)
                    m += ''.join(h + tag for h, v in
                                 (('a', ip.a), ('b', ip.b), ('x', ip.ix),
                                  ('p', ip.p)) if v == addr)
                marks += f"{m:>5s}"
            if marks.strip():
                print("       " + marks)


# ─── Examples (fb1d8 programs, unchanged source, payloads mod 2048) ──

EXAMPLES = {}


def example(name, doc):
    def deco(fn):
        EXAMPLES[name] = (doc, fn)
        return fn
    return deco


@example('copy', "[.>}] copies a zero-terminated string by XOR into zeros")
def ex_copy(m):
    m.load_program([('copy loop', '[.>}]')], size=64)
    src, dst = 8, 20
    for i, ch in enumerate(b'fb1d'):
        m.poke(src + 1 + i, ch)
    m.ips[0].a, m.ips[0].b = src, dst
    return dict(lo=src, hi=dst + 8,
                note="a on zero before source (8), b on zero before dest (20).")


@example('counter', "unbounded binary counter [>[,>][,<,]<-+]")
def ex_counter(m):
    m.load_program([('outer [', '['), ('to sentinel', '>'),
                    ('clear trailing ones', '[,>]'),
                    ('set bit, walk back', '[,<,]'),
                    ('home, -+, loop', '<-+]')], size=64)
    W = 16
    m.ips[0].a = m.ips[0].b = W
    m.ips[0].p = 13
    return dict(lo=W, hi=W + 16,
                note="tape[16]=constant 1, tape[17]=sentinel, bits LSB first\n"
                     "from 18.  `loop` runs one increment.")


@example('add', "acc += x using the garbage-free FOR idiom")
def ex_add(m):
    m.load_program([('heads to t', '>}')] + fb1d8.ADD(xoff=3, dst_off_from_c=-2),
                   size=128)
    D = m.code_end + 2
    m.poke(D + 4, 5)
    m.ips[0].a = m.ips[0].b = D
    return dict(lo=D, hi=D + 8, note=f"layout: acc t c d x at {D}..{D+4}, x=5.")


@example('fib', "Fibonacci, one number per 4-cell slot (payloads mod 2048)")
def ex_fib(m):
    m.load_program(fb1d8.fib_segments(), size=1400)
    D = m.code_end + 2
    m.poke(D + 1 + 4, 1)
    m.poke(D + 1 + 8, 1)
    m.ips[0].a, m.ips[0].b = D, D + 1
    return dict(lo=D, hi=D + 1 + 4 * 12,
                note=f"K at {D}, Z at {D+1}, slots [S t c d] from {D+5}.")


@example('immunity', "M2 immunity gadget correcting a static partner block (programs/fb1d-immunity-m2.py)")
def ex_immunity(m):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'programs', 'fb1d-immunity-m2.py')
    spec = importlib.util.spec_from_file_location('fb1d_immunity_m2', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    built, lay = mod.build(fuel=120)
    m.tape, m.ips, m.segments = built.tape, built.ips, built.segments
    m.code_end, m.rounds, m.noise, m.noise_on = built.code_end, 0, None, False
    m.loop_stop = lay.p_start
    m.invalidate()
    lo, hi = lay.partner_range()
    return dict(lo=lay.s - 1, hi=lay.s + 31,
                note=f"stomach s={lay.s} g={lay.g} (a on s), b on the last garbage "
                     f"cell (starts at {lay.L0}), fuel to {lay.p_lead - 1}, partner "
                     f"block {lo}..{hi} (ix on {lo}).\n"
                     f"`loop` = run until p == {lay.p_start} (one full pass, ~4100 "
                     f"rounds).  Try: flip {lo + 20} 5, loop, d {lo} {hi}, noise 300 "
                     f"{lo} {hi}.")


@example('dual', "M3: two immunity gadgets on two IPs correcting each other (programs/fb1d-immunity-m3.py)")
def ex_dual(m):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'programs', 'fb1d-immunity-m3.py')
    spec = importlib.util.spec_from_file_location('fb1d_immunity_m3', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    built, lay = mod.build(fuel=120)
    m.tape, m.ips, m.segments = built.tape, built.ips, built.segments
    m.code_end, m.rounds, m.noise, m.noise_on = built.code_end, 0, None, False
    m.loop_stop = lay.gadgets[0].p_start
    m.invalidate()
    ga, gb = lay.gadgets
    return dict(lo=ga.s - 1, hi=ga.s + 15,
                note=f"gadget A: code {ga.code}..{ga.trail - 1}, stomach s={ga.s} "
                     f"g={ga.g}, b from {ga.L0}; gadget B: code {gb.code}..{gb.trail - 1}, "
                     f"stomach s={gb.s} g={gb.g}, b from {gb.L0}.  IP0 runs A and its ix "
                     f"scans B's block {gb.lead}..{gb.trail}; IP1 the reverse.\n"
                     f"`loop` = run until IP0's p == {ga.p_start} (one pass, 4094 rounds).  "
                     f"Try: flip {ga.code + 20} 5, loop, d 0 {ga.trail}; "
                     f"noise 300 (blocks: set via Machine.set_noise(..., blocks=))")


@example('agent', "M4: the M3 pair plus metabolism and hunger (programs/fb1d-agent-m4.py)")
def ex_agent(m):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'programs', 'fb1d-agent-m4.py')
    spec = importlib.util.spec_from_file_location('fb1d_agent_m4', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    built, lay = mod.build()
    m.tape, m.ips, m.segments = built.tape, built.ips, built.segments
    m.code_end, m.rounds, m.noise, m.noise_on = built.code_end, 0, None, False
    m.loop_stop = lay.gadgets[0].p_start
    m.invalidate()
    ga, gb = lay.gadgets
    return dict(lo=ga.s - 1, hi=ga.s + 47,
                note=f"as `dual`, with a third stomach cell r and food after the zeros: "
                     f"gadget A stomach s={ga.s} g={ga.g} r={ga.r}, b from {ga.L0}, "
                     f"{lay.zeros} zeros then runs of {lay.K} food cells to {ga.fuel_hi}; "
                     f"gadget B likewise from {gb.s}.\n"
                     f"Each pass end tests the cell {lay.d} ahead of b and eats one run "
                     f"when it is not zero.  `loop` = one pass (~{7135} rounds).  The free-food "
                     f"cheat is in the harness and the browser workbench, not here.")


@example('fact', "factorials via nested FOR (payloads mod 2048)")
def ex_fact(m):
    m.load_program(fb1d8.fact_segments(), size=1400)
    D = m.code_end + 2
    m.poke(D + 1 + 8, 1)
    m.poke(D + 1 + 8 + 7, 1)
    m.ips[0].a, m.ips[0].b = D, D + 1
    return dict(lo=D, hi=D + 1 + 8 * 9,
                note=f"K at {D}, Z at {D+1}, slots of 8 from {D+9}.")


# ─── Tests ──────────────────────────────────────────────────────────

PARITY_POS = (0, 1, 2, 4, 8)
DATA_POS = (3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15)


def _check(ok, msg):
    print(f"  [{'ok' if ok else 'FAIL'}] {msg}")
    return ok


def test_fetch():
    """Parity never changes the op; 1 data flip keeps it; 2 give NOP."""
    ok = True
    for ch, cell in OP_CELL.items():
        op = CELL_OP[cell]
        for k in range(32):
            mask = sum(1 << PARITY_POS[i] for i in range(5) if k >> i & 1)
            ok &= CELL_OP[cell ^ mask] == op
        for d in DATA_POS:
            ok &= CELL_OP[cell ^ (1 << d)] == op
        for d1, d2 in itertools.combinations(DATA_POS, 2):
            ok &= CELL_OP[cell ^ (1 << d1) ^ (1 << d2)] == 0
    ok &= CELL_OP[0] == 0 and CELL_OP[NOP_FILLER] == 0 and CELL_OP[BOUNDARY] == 0
    live = sum(1 for v in range(65536) if CELL_OP[v])
    return _check(ok, f"fetch: parity-blind, 1-flip stable, 2-flip NOP "
                      f"({live}/65536 words are live ops)")


def _exhaustive(N, alphabet, label):
    seen, bad, n = set(), 0, 0
    m = Machine(N)
    ip = m.ips[0]
    for tape in itertools.product(alphabet, repeat=N):
        for a, b, x, p in itertools.product(range(N), repeat=4):
            m.tape = list(tape); m.invalidate()
            ip.a, ip.b, ip.ix, ip.p = a, b, x, p
            s0 = (tape, a, b, x, p)
            m.step_ip(0)
            s1 = (tuple(m.tape), ip.a, ip.b, ip.ix, ip.p)
            m.step_back_ip(0)
            if (tuple(m.tape), ip.a, ip.b, ip.ix, ip.p) != s0:
                bad += 1
            seen.add(s1); n += 1
    return _check(bad == 0 and len(seen) == n,
                  f"exhaustive {label} N={N}: {n} states, {len(seen)} "
                  f"distinct images, round-trip failures {bad}")


def _rand_cell(rng):
    r = rng.random()
    if r < 0.55:
        return OP_CELL[rng.choice(OP_CHARS)]
    if r < 0.65:
        return OP_CELL[rng.choice(OP_CHARS)] ^ (1 << rng.randrange(16))
    if r < 0.75:
        return 0
    if r < 0.8:
        return BOUNDARY
    if r < 0.9:
        return hamming_encode(rng.randrange(2048))
    return rng.randrange(65536)


def test_bijectivity():
    ok = True
    # every op, every head/IP aliasing on N=3, with a mixed cell alphabet
    alpha = [OP_CELL[c] for c in OP_CHARS] + [0, BOUNDARY]
    ok &= _exhaustive(3, alpha, "ops+0+~")
    # arbitrary (corrupted) data under the data-touching ops
    rng = random.Random(7)
    junk = [rng.randrange(65536) for _ in range(3)]
    for group in ('+-.,', 'mIVj', '[]<>', '(){}', 'PQ{}', '?!<>', '%&{}', '[?!]'):
        alpha = [OP_CELL[c] for c in group] + junk
        ok &= _exhaustive(3, alpha, f"{group}+junk")
    # random single-IP self-modifying runs on larger tapes
    bad = stale = 0
    for trial in range(300):
        N = rng.randint(4, 40)
        m = Machine(N, barriers=trial % 2 == 0)
        m.tape = [_rand_cell(rng) for _ in range(N)]
        ip = m.ips[0]
        ip.a, ip.b, ip.ix, ip.p = (rng.randrange(N) for _ in range(4))
        s0 = m.state()
        m.matches()                          # cache built before the writes
        for _ in range(500):
            m.step_all()
        stale += m.matches() != match_table(m.tape, m.barriers)
        for _ in range(500):
            m.step_back_all()
        bad += m.state() != s0
    ok &= _check(bad == 0 and stale == 0,
                 f"300 random single-IP runs x 500 steps: reversal failures "
                 f"{bad}, incremental match cache stale {stale} times")
    return ok


def noisy_multi_ip_roundtrip(rounds, n_ips=2, N=400, rate=5000, seed=3,
                             blocks=None):
    rng = random.Random(seed)
    m = Machine(N)
    m.tape = [_rand_cell(rng) for _ in range(N)]
    m.ips = [IP(*(rng.randrange(N) for _ in range(4))) for _ in range(n_ips)]
    m.set_noise(rate, 0, N - 1, seed=seed, blocks=blocks)
    s0 = m.state()
    stale = 0
    for r in range(rounds):
        m.step_all()
        if r % 997 == 0:
            stale += m.matches() != match_table(m.tape, m.barriers)
    injected = m.noise.total_injected
    changed = sum(x != y for x, y in zip(s0[0], m.tape))
    if blocks:
        outside = sum(x != y for i, (x, y) in enumerate(zip(s0[0], m.tape))
                      if not any(lo <= i <= hi for lo, hi in blocks))
    for _ in range(rounds):
        m.step_back_all()
    diffs = sum(x != y for x, y in zip(s0[0], m.tape))
    good = m.state() == s0 and stale == 0
    return _check(good and injected > 0,
                  f"{n_ips} IPs, {rounds} rounds, {injected} noise flips"
                  f"{f' over {len(blocks)} blocks' if blocks else ''}, "
                  f"{changed} cells changed, round trip diffs {diffs}"
                  f"{'' if good else ' (heads differ or match cache stale)'}")


def test_barriers():
    ok = True
    t = assemble('[~]')
    ok &= match_table(t, True) == {} and match_table(t, False) == {0: 2, 2: 0}
    # a parity flip on the boundary keeps it a barrier; a data flip removes it
    ok &= match_table([t[0], BOUNDARY ^ 1, t[2]]) == {}
    ok &= match_table([t[0], BOUNDARY ^ 8, t[2]]) == {0: 2, 2: 0}
    # a corrupted gadget cannot capture its neighbour's brackets
    t = assemble('[[]~[]]')
    ok &= match_table(t, True) == {1: 2, 2: 1, 4: 5, 5: 4}
    # the two families match independently: [ ( ] ) pairs 0-2 and 1-3
    t = assemble('[(])')
    ok &= match_table(t) == {0: 2, 2: 0, 1: 3, 3: 1}
    t = assemble('([~)]')
    ok &= match_table(t) == {}
    # four families, four stacks
    t = assemble('[(?%]&)!')
    ok &= match_table(t) == {0: 4, 4: 0, 1: 6, 6: 1, 2: 7, 7: 2, 3: 5, 5: 3}
    t = assemble('?~!%~&')
    ok &= match_table(t) == {} and match_table(t, False) == {0: 2, 2: 0, 3: 5, 5: 3}
    return _check(ok, "boundary barrier: brackets never pair across `~`; "
                      "`[ ]`, `( )`, `? !` and `% &` match independently")


def test_b_brackets():
    """`( X )` runs X iff payload(tape[b]) == 0 and repeats while it is
    nonzero, exactly `[ ]` with b in place of a; a is free inside."""
    ok = True
    # b on a zero: enter, bump a's cell, exit (b's cell still zero)
    m = Machine(16)
    m.tape[0:3] = assemble('(+)')
    m.ips[0].a, m.ips[0].b = 8, 9
    m.run(3)
    ok &= m.peek(8) == 1 and m.ips[0].p == 3
    # b on a nonzero: skip
    m = Machine(16)
    m.tape[0:3] = assemble('(+)')
    m.poke(9, 5)
    m.ips[0].a, m.ips[0].b = 8, 9
    m.run(1)                     # the jump is one step: `(` -> past `)`
    ok &= m.peek(8) == 0 and m.ips[0].p == 3
    # walk: ( } ) from a zero over two nonzero cells lands on the next zero
    m = Machine(16)
    m.tape[0:3] = assemble('(})')
    m.poke(10, 1); m.poke(11, 1)
    m.ips[0].b = 9
    m.run(20, stop_at=3)
    ok &= m.ips[0].b == 12
    # a-tested block moving b: [ }} ] with a on zero moves b, untested
    m = Machine(16)
    m.tape[0:4] = assemble('[}}]')
    m.poke(10, 1); m.poke(11, 1)
    m.ips[0].a, m.ips[0].b = 8, 9
    m.run(4)
    ok &= m.ips[0].b == 11 and m.ips[0].p == 4
    # P / Q: payload arithmetic on b's cell, bijective on all 65536 values
    m = Machine(16)
    m.tape[0:4] = assemble('PPQP')
    m.tape[9] = 0xFFFF ^ 0x0040                # boundary with a data-bit error
    raw0, p0 = m.tape[9], m.peek(9)
    m.ips[0].b = 9
    m.run(2)
    ok &= m.peek(9) == (p0 + 2) % 2048 and m.tape[9] != raw0
    m.run(2)
    ok &= m.peek(9) == (p0 + 2) % 2048 and m.ips[0].p == 4
    for _ in range(4):
        m.step_back_all()
    ok &= m.tape[9] == raw0
    return _check(ok, "( ) brackets: enter on b==0, repeat while b!=0, "
                      "a free inside; [ ] leaves b untested; P/Q count on b's "
                      "cell and reverse exactly")


def test_one_brackets():
    """`? X !` runs X iff payload(tape[a]) != 0 and repeats while it is
    zero; `% X &` the same on b.  Walks: `% } &` from a nonzero cell
    stops on the next nonzero cell; `? < !` likewise with a."""
    ok = True
    # a nonzero: enter, bump b's cell, exit (a's cell still nonzero)
    m = Machine(16)
    m.tape[0:3] = assemble('?P!')
    m.poke(8, 5)
    m.ips[0].a, m.ips[0].b = 8, 9
    m.run(3)
    ok &= m.peek(9) == 1 and m.ips[0].p == 3
    # a zero: skip in one step
    m = Machine(16)
    m.tape[0:3] = assemble('?P!')
    m.ips[0].a, m.ips[0].b = 8, 9
    m.run(1)
    ok &= m.peek(9) == 0 and m.ips[0].p == 3
    # a zero at the closer repeats: ? - ! from 1 runs the body twice (1 -> 0
    # -> 2047) and exits on the nonzero
    m = Machine(16)
    m.tape[0:3] = assemble('?-!')
    m.poke(8, 1)
    m.ips[0].a = 8
    n = m.run(10**5, stop_at=3)
    ok &= m.peek(8) == 2047 and n == 5
    # walk on b: % } & from a nonzero over three zeros lands on the next nonzero
    m = Machine(16)
    m.tape[0:3] = assemble('%}&')
    m.poke(9, 7); m.poke(13, 2)
    m.ips[0].b = 9
    m.run(20, stop_at=3)
    ok &= m.ips[0].b == 13
    # walk back: % { & from 13 lands on 9
    m.tape[0:3] = assemble('%{&'); m.invalidate()
    m.ips[0].p = 0
    m.run(20, stop_at=3)
    ok &= m.ips[0].b == 9
    # same on a
    m = Machine(16)
    m.tape[0:3] = assemble('?>!')
    m.poke(9, 7); m.poke(13, 2)
    m.ips[0].a = 9
    m.run(20, stop_at=3)
    ok &= m.ips[0].a == 13
    # the compress loop of the metabolism: % } . & with a on a copy of v
    m = Machine(32)
    m.tape[0:4] = assemble('%}.&')
    for i in range(5):
        m.poke(10 + i, 300)
    m.poke(15, 301)
    m.poke(8, 300)
    m.ips[0].a, m.ips[0].b = 8, 10
    s0 = m.state()
    n = m.run(100, stop_at=4)
    ok &= [m.peek(10 + i) for i in range(6)] == [300, 0, 0, 0, 0, 300 ^ 301]
    ok &= m.ips[0].b == 15 and _rev(m, n, s0)
    # a1 IF-NONZERO on a zero-family loop's flag, mixed nesting: [ ? ] !
    t = assemble('[?]!')
    ok &= match_table(t) == {0: 2, 2: 0, 1: 3, 3: 1}
    return _check(ok, "? ! and % &: enter on nonzero, repeat while zero; walks "
                      "stop on the next nonzero cell; compress loop; reverse")


def test_ix_ops():
    ok = True
    # boundary test idiom: m the cell into a zero scratch, +, IF-ZERO
    m = Machine(16)
    m.tape[0:3] = assemble('m+')
    m.tape[10] = BOUNDARY
    m.ips[0].a, m.ips[0].ix = 8, 10
    m.run(2)
    ok &= m.tape[8] == 0
    # I is zero on clean cells, nonzero on every 1- and 2-bit error
    for v in range(0, 2048, 37):
        cw = hamming_encode(v)
        ok &= SYNDROME_XOR_MASK[cw] & DATA_MASK == 0
        for i in range(16):
            ok &= SYNDROME_XOR_MASK[cw ^ (1 << i)] & DATA_MASK != 0
            for k in range(i + 1, 16):
                ok &= SYNDROME_XOR_MASK[cw ^ (1 << i) ^ (1 << k)] & DATA_MASK != 0
    # V then j repairs any single-bit error in place
    for v in (0, 1, 1017, 2047, 975):
        for i in range(16):
            m = Machine(16)
            m.tape[0:2] = assemble('Vj')
            m.tape[12] = hamming_encode(v) ^ (1 << i)
            m.ips[0].a, m.ips[0].ix = 8, 12
            m.run(2)
            ok &= m.tape[12] == hamming_encode(v) and m.tape[8] == 1 << i
    return _check(ok, "ix ops: boundary idiom, I detects 1-2 bit errors, "
                      "V+j repairs 1-bit errors")


def _rev(m, n, s0):
    for _ in range(n):
        m.step_back_all()
    return m.state() == s0


def test_examples():
    ok = True
    m = Machine()
    ex_copy(m)
    s0 = m.state(); n = m.run(100, stop_at=m.code_end)
    got = bytes(m.peek(21 + i) for i in range(4))
    ok &= _check(got == b'fb1d' and _rev(m, n, s0), f"copy: {got!r} in {n} steps")

    m = Machine()
    ex_counter(m)
    s0 = m.state(); m.run(2); total, good = 2, True
    for k in range(1, 1001):
        total += m.run(10**6, stop_at=1)
        val = sum(m.peek(18 + i) << i for i in range(20))
        good &= val == k and m.peek(16) == 1 and m.peek(17) == 0
    ok &= _check(good and _rev(m, total, s0),
                 f"counter: 1000 increments in {total} steps, reversed")

    m = Machine()
    ex_add(m)
    s0 = m.state(); n = m.run(10**5, stop_at=m.code_end)
    D = m.code_end + 2
    cells = [m.peek(D + i) for i in range(5)]
    ok &= _check(cells == [5, 0, 0, 0, 5] and _rev(m, n, s0),
                 f"add: {cells} in {n} steps")

    m = Machine()
    ex_fib(m)
    s0 = m.state(); D = m.code_end + 2; total = 0
    for _ in range(11):
        total += m.run(10**6, stop_at=1)
    got = [m.peek(D + 1 + 4 * j) for j in range(1, 13)]
    ok &= _check(got == [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]
                 and _rev(m, total, s0), f"fib: {got} in {total} steps")

    m = Machine()
    ex_fact(m)
    s0 = m.state(); D = m.code_end + 2; total = 0
    for _ in range(8):
        total += m.run(10**7, stop_at=1)
    got = [m.peek(D + 1 + 8 * j) for j in range(1, 9)]
    want = [1, 2, 6, 24, 120, 720, 5040 % 2048, 40320 % 2048]
    ok &= _check(got == want and _rev(m, total, s0),
                 f"fact: {got} in {total} steps (mod 2048)")
    return ok


def test_names():
    """Readable names assemble to the same cells as the symbols; every
    example program and the M2 gadget round-trip through disassemble."""
    ok = True
    for ch in OP_CHARS:
        ok &= assemble(OP_NAMES[ch]) == assemble(ch)
    ok &= assemble('a0[ a> a+ a< ]a0') == assemble('[>+<]')
    ok &= assemble('I[>+<]>[<IVj>]') == assemble('syn a0[ a> a+ a< ]a0 a> a0[ a< syn mask i^=a a> ]a0')
    ok &= assemble('b0[ a+ ]b0') == assemble('(+)')
    ok &= assemble('a+ # comment ~ [ \n  a+') == assemble('++')
    ok &= assemble('~_0') == [BOUNDARY, NOP_FILLER, 0]
    ok &= assemble('a1[ b> ]a1 b1[ a< ]b1') == assemble('?}!%<&')
    for bad in ('a', 'x<', '[a+', 'a^b', 'abc', '[a', ']a', 'a2[', '0[a'):
        try:
            assemble(bad)
            ok = False
        except ValueError:
            pass
    texts = {}
    for name, (_, fn) in EXAMPLES.items():
        m = Machine()
        fn(m)
        texts[name] = '\n'.join(t for _, t in m.segments)
    for name, text in texts.items():
        cells = assemble(text)
        ok &= assemble(disassemble(cells)) == cells
        ok &= assemble(disassemble(cells, names=False)) == cells
    return _check(ok, f"names: all {len(OP_CHARS)} ops, comments, bad tokens "
                      f"rejected, {len(texts)} programs round-trip "
                      f"({', '.join(texts)})")


def run_tests(long=False):
    ok = True
    print("== fetch ==")
    ok &= test_fetch()
    print("== bijectivity ==")
    ok &= test_bijectivity()
    print("== multi-IP + noise ==")
    ok &= noisy_multi_ip_roundtrip(20000, n_ips=2)
    ok &= noisy_multi_ip_roundtrip(20000, n_ips=3, seed=4)
    ok &= noisy_multi_ip_roundtrip(20000, n_ips=2, seed=6,
                                   blocks=[(10, 109), (200, 299)])
    if long:
        ok &= noisy_multi_ip_roundtrip(2_000_000, n_ips=2, N=1000,
                                       rate=300, seed=5)
    print("== brackets / ix ==")
    ok &= test_barriers()
    ok &= test_b_brackets()
    ok &= test_one_brackets()
    ok &= test_ix_ops()
    print("== fb1d8 examples on 16-bit cells ==")
    ok &= test_examples()
    print("== readable names ==")
    ok &= test_names()
    print("ALL OK" if ok else "FAILURES")
    return ok


# ─── REPL ───────────────────────────────────────────────────────────

HELP = """\
commands:
  examples            list examples          load NAME       load one
  d [lo hi]           display                src             show segments
  s [n] / b [n]       step / back n rounds   loop            run one outer-loop pass
  run [n]             run until IP0 leaves code or n rounds (default 100000)
  until P             run until IP0 p == P
  set ADDR PAYLOAD    store clean codeword   raw ADDR HEX    store raw 16-bit
  flip ADDR BIT       XOR one bit            asm ADDR TEXT   assemble ops at ADDR
                      (TEXT: symbols and/or names, e.g.  asm 0 [a a> a+ a< ]a)
  head I h ADDR       move head h (a b ix p) of IP I
  addip A B IX P      add an IP              rmip I          remove IP I
  noise RATE [LO HI [SEED]]   configure noise (flips per 1M rounds; 0 = off)
  ops                 list opcodes           names           op symbols and names
  test                run test suite
  q                   quit
"""


def repl():
    m = Machine()
    view = {}

    def show():
        m.display(view.get('lo'), view.get('hi'))

    def load(name):
        nonlocal view
        if name not in EXAMPLES:
            print("unknown example; try `examples`")
            return
        m.loop_stop = 1
        info = EXAMPLES[name][1](m)
        m.example = name
        view = {'lo': info.get('lo'), 'hi': info.get('hi')}
        print(f"loaded {name}: {EXAMPLES[name][0]}\n{info.get('note', '')}")
        show()

    print("fb1d REPL -- type help.  Loading `counter`.")
    load('counter')
    while True:
        try:
            line = input("fb1d> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        cmd, *args = line.split()
        try:
            if cmd in ('q', 'quit', 'exit'):
                break
            elif cmd == 'help':
                print(HELP)
            elif cmd == 'names':
                for ch in OP_CHARS:
                    print(f"  {ch}  {OP_NAMES[ch]:5s}")
            elif cmd == 'ops':
                for op, doc in OP_DOC:
                    print(f"  {op:6s} {doc}")
            elif cmd == 'examples':
                for name, (doc, _) in EXAMPLES.items():
                    print(f"  {name:10s} {doc}")
            elif cmd == 'load':
                load(args[0])
            elif cmd == 'd':
                if len(args) == 2:
                    view['lo'], view['hi'] = int(args[0]), int(args[1])
                show()
            elif cmd == 'src':
                off = 0
                for label, text in m.segments:
                    cells = assemble(text)
                    n = len(cells)
                    mark = '->' if off <= m.ips[0].p < off + n else '  '
                    print(f"{mark} {off:4d} {label:28s} {disassemble(cells)}")
                    off += n
            elif cmd in ('s', 'b'):
                for _ in range(int(args[0]) if args else 1):
                    m.step_all() if cmd == 's' else m.step_back_all()
                show()
            elif cmd in ('run', 'loop', 'until'):
                if cmd == 'run':
                    n = m.run(int(args[0]) if args else 100000,
                              stop_at=m.code_end)
                else:
                    n = m.run(10**7, stop_at=m.loop_stop if cmd == 'loop' else int(args[0]))
                print(f"ran {n} rounds")
                show()
            elif cmd == 'set':
                m.poke(int(args[0]), int(args[1]))
                show()
            elif cmd == 'raw':
                m.set_cell(int(args[0]), int(args[1], 16) & 0xFFFF)
                show()
            elif cmd == 'flip':
                m.flip(int(args[0]), int(args[1]))
                show()
            elif cmd == 'asm':
                addr, cells = int(args[0]), assemble(' '.join(args[1:]))
                for i, c in enumerate(cells):
                    m.set_cell((addr + i) % m.N, c)
                show()
            elif cmd == 'head':
                setattr(m.ips[int(args[0])], args[1], int(args[2]) % m.N)
                show()
            elif cmd == 'addip':
                m.ips.append(IP(*(int(x) % m.N for x in args[:4])))
                show()
            elif cmd == 'rmip':
                del m.ips[int(args[0])]
                show()
            elif cmd == 'noise':
                rate = float(args[0])
                lo = int(args[1]) if len(args) > 1 else 0
                hi = int(args[2]) if len(args) > 2 else m.N - 1
                seed = int(args[3]) if len(args) > 3 else 42
                m.set_noise(rate, lo, hi, seed)
                print(f"noise {rate}/1M over [{lo}, {hi}], seed {seed} "
                      "(pool reset: don't step back past here)")
            elif cmd == 'test':
                run_tests()
            else:
                print("unknown command; type help")
        except (IndexError, ValueError) as e:
            print(f"bad arguments ({e}); type help")


if __name__ == '__main__':
    if '--test' in sys.argv:
        sys.exit(0 if run_tests(long='--long' in sys.argv) else 1)
    repl()

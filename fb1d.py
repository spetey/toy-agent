#!/usr/bin/env python3
"""
fb1d: 16-bit reversible bracket language on a 1D tape
Authored or modified by Claude
Version: 2026-09-26 v0.2 (M1 + M2 `( )` extension, docs/fb1d-port-plan.md)

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
  anything else: no-op

"payload != 0" means tape[a] & DATA_MASK != 0, as in fb2d's mirrors.
`( )` were added in M2 (v0.2): a block that tests one head may move the
other head freely, which the immunity gadget's merge needs (see
programs/fb1d-immunity-m2.py and docs/fb1d-port-plan.md).  The two
bracket families are matched independently (own stacks); barriers
empty both.

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

Noise: fb2d's NoisePool (pools.py) over a flat address range, rate in
flips per 1M step_alls, deterministic by seed and fully reversible.

Usage:
  python3 fb1d.py              REPL (type help)
  python3 fb1d.py --test       verification suite (~1 min)
  python3 fb1d.py --test --long   adds the 2M-round noisy round trip
"""

import os
import sys
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

OP_CHARS = '<>{}BA-+.,mIVj[]()'
(OP_AL, OP_AR, OP_BL, OP_BR, OP_XL, OP_XR, OP_DEC, OP_INC, OP_DOT,
 OP_COMMA, OP_M, OP_I, OP_V, OP_J, OP_LB, OP_RB, OP_LP, OP_RP) = range(1, 19)
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
# bracket-matching class: 1 = '[', 2 = ']', 4 = '(', 5 = ')', 3 = barrier,
# 0 = other.  The two bracket families are matched independently.
CELL_CLASS = [1 if CELL_OP[v] == OP_LB else 2 if CELL_OP[v] == OP_RB else
              4 if CELL_OP[v] == OP_LP else 5 if CELL_OP[v] == OP_RP else
              3 if _CELL_TO_PAYLOAD_RAW[v] == 2047 else 0
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


def assemble(text):
    """Program text -> list of cells.  Op chars, `~` boundary, `_` NOP
    filler, `0` zero; whitespace ignored; anything else is an error."""
    out = []
    for ch in text:
        if ch.isspace():
            continue
        if ch in OP_CELL:
            out.append(OP_CELL[ch])
        elif ch == '~':
            out.append(BOUNDARY)
        elif ch == '_':
            out.append(NOP_FILLER)
        elif ch == '0':
            out.append(0)
        else:
            raise ValueError(f"not an fb1d op: {ch!r}")
    return out


def match_table(tape, barriers=True):
    """Stack matching over linear order, one stack per bracket family
    (`[ ]` on a, `( )` on b).  Returns dict pos -> partner.  A barrier
    empties both stacks."""
    st_a, st_b, mt = [], [], {}
    for i, v in enumerate(tape):
        c = CELL_CLASS[v]
        if not c:
            continue
        if c == 1:
            st_a.append(i)
        elif c == 4:
            st_b.append(i)
        elif c == 2 or c == 5:
            st = st_a if c == 2 else st_b
            if st:
                j = st.pop()
                mt[i] = j
                mt[j] = i
        elif barriers:
            st_a.clear()
            st_b.clear()
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
        self._mt = None
        self.noise = None        # NoisePool, or None
        self.noise_on = False
        self.code_end = 0
        self.segments = []
        self.example = None
        self.loop_stop = 1       # REPL `loop`: run until IP0's p is here

    @property
    def N(self):
        return len(self.tape)

    def matches(self):
        if self._mt is None:
            self._mt = match_table(self.tape, self.barriers)
        return self._mt

    def invalidate(self):
        self._mt = None

    def state(self):
        return (tuple(self.tape), tuple(ip.tup() for ip in self.ips),
                self.rounds)

    # ------------------------------------------------------------ core
    def _write(self, ip, addr, val):
        if addr == ip.p:                     # executing-cell guard
            return
        t = self.tape
        if CELL_CLASS[t[addr]] or CELL_CLASS[val]:
            self._mt = None
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
        elif op <= OP_RB:                    # [ ]  test tape[a]
            if t[a] & DATA_MASK:
                mt = self.matches()
                if ip.p in mt:
                    ip.p = mt[ip.p]
        else:                                # ( )  test tape[b]
            if t[ip.b] & DATA_MASK:
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

    def step_all(self):
        for i in range(len(self.ips)):
            self.step_ip(i)
        if self.noise_on:
            if self.noise.apply_forward(self.rounds, self.tape,
                                        lambda r, c: c, [0]):
                self._mt = None
        self.rounds += 1

    def step_back_all(self):
        self.rounds -= 1
        if self.noise_on:
            if self.noise.undo_at(self.rounds, self.tape,
                                  lambda r, c: c, [0]):
                self._mt = None
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
    def set_noise(self, flips_per_1M, lo=0, hi=None, seed=42, kind='any'):
        """Configure noise over tape[lo..hi] (inclusive).  Resets the
        pool, so only call this when rounds == 0 or you won't step back
        past this point."""
        hi = self.N - 1 if hi is None else hi
        self.noise = NoisePool(seed=seed, n_code_rows=1, grid_cols=self.N,
                               noise_type=kind, flips_per_1M=flips_per_1M,
                               col_min=lo, col_max=hi)
        self.noise_on = flips_per_1M > 0

    # --------------------------------------------------------- loading
    def load_program(self, segments, size=None, code_at=0):
        self.tape = [0] * (size or self.N)
        code = assemble(''.join(text for _, text in segments))
        self.tape[code_at:code_at + len(code)] = code
        self.segments = segments
        self.code_end = code_at + len(code)
        self.ips = [IP()]
        self.rounds = 0
        self._mt = None

    def poke(self, addr, value):
        """Store an 11-bit payload as a clean codeword."""
        self.tape[addr] = hamming_encode(value)
        self._mt = None

    def peek(self, addr):
        return _CELL_TO_PAYLOAD_RAW[self.tape[addr]]

    def segment_at(self, p):
        off = 0
        for label, text in self.segments:
            n = len(''.join(text.split()))
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
    return dict(lo=lay.frame0 - 1, hi=lay.frame0 + 31,
                note=f"frame [M s g] at {lay.frame0} (b on M, a on s), fuel to "
                     f"{lay.p_lead - 1}, partner block {lo}..{hi} (ix on {lo}).\n"
                     f"`loop` = run until p == {lay.p_start} (one full pass, ~{6370} "
                     f"rounds).  Try: flip {lo + 20} 5, loop, d {lo} {hi}, noise 300 "
                     f"{lo} {hi}.")


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
            m.tape = list(tape); m._mt = None
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
    for group in ('+-.,', 'mIVj', '[]<>', '(){}'):
        alpha = [OP_CELL[c] for c in group] + junk
        ok &= _exhaustive(3, alpha, f"{group}+junk")
    # random single-IP self-modifying runs on larger tapes
    bad = 0
    for trial in range(300):
        N = rng.randint(4, 40)
        m = Machine(N, barriers=trial % 2 == 0)
        m.tape = [_rand_cell(rng) for _ in range(N)]
        ip = m.ips[0]
        ip.a, ip.b, ip.ix, ip.p = (rng.randrange(N) for _ in range(4))
        s0 = m.state()
        for _ in range(500):
            m.step_all()
        for _ in range(500):
            m.step_back_all()
        bad += m.state() != s0
    ok &= _check(bad == 0, f"300 random single-IP runs x 500 steps: "
                           f"reversal failures {bad}")
    return ok


def noisy_multi_ip_roundtrip(rounds, n_ips=2, N=400, rate=5000, seed=3):
    rng = random.Random(seed)
    m = Machine(N)
    m.tape = [_rand_cell(rng) for _ in range(N)]
    m.ips = [IP(*(rng.randrange(N) for _ in range(4))) for _ in range(n_ips)]
    m.set_noise(rate, 0, N - 1, seed=seed)
    s0 = m.state()
    for _ in range(rounds):
        m.step_all()
    injected = m.noise.total_injected
    changed = sum(x != y for x, y in zip(s0[0], m.tape))
    for _ in range(rounds):
        m.step_back_all()
    diffs = sum(x != y for x, y in zip(s0[0], m.tape))
    good = m.state() == s0
    return _check(good and injected > 0,
                  f"{n_ips} IPs, {rounds} rounds, {injected} noise flips, "
                  f"{changed} cells changed, round trip diffs {diffs}"
                  f"{'' if good else ' (heads differ)'}")


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
    return _check(ok, "boundary barrier: brackets never pair across `~`; "
                      "`[ ]` and `( )` match independently")


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
    return _check(ok, "( ) brackets: enter on b==0, repeat while b!=0, "
                      "a free inside; [ ] leaves b untested")


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


def run_tests(long=False):
    ok = True
    print("== fetch ==")
    ok &= test_fetch()
    print("== bijectivity ==")
    ok &= test_bijectivity()
    print("== multi-IP + noise ==")
    ok &= noisy_multi_ip_roundtrip(20000, n_ips=2)
    ok &= noisy_multi_ip_roundtrip(20000, n_ips=3, seed=4)
    if long:
        ok &= noisy_multi_ip_roundtrip(2_000_000, n_ips=2, N=1000,
                                       rate=300, seed=5)
    print("== brackets / ix ==")
    ok &= test_barriers()
    ok &= test_b_brackets()
    ok &= test_ix_ops()
    print("== fb1d8 examples on 16-bit cells ==")
    ok &= test_examples()
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
  head I h ADDR       move head h (a b ix p) of IP I
  addip A B IX P      add an IP              rmip I          remove IP I
  noise RATE [LO HI [SEED]]   configure noise (flips per 1M rounds; 0 = off)
  ops                 list opcodes           test            run test suite
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
                    n = len(''.join(text.split()))
                    mark = '->' if off <= m.ips[0].p < off + n else '  '
                    print(f"{mark} {off:4d} {label:28s} {text}")
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
                m.tape[int(args[0])] = int(args[1], 16) & 0xFFFF
                m.invalidate()
                show()
            elif cmd == 'flip':
                m.tape[int(args[0])] ^= 1 << int(args[1])
                m.invalidate()
                show()
            elif cmd == 'asm':
                addr, cells = int(args[0]), assemble(''.join(args[1:]))
                for i, c in enumerate(cells):
                    m.tape[(addr + i) % m.N] = c
                m.invalidate()
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

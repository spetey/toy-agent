#!/usr/bin/env python3
"""
RBFF: Reversible BFF on a 1D tape
Authored or modified by Claude
Version: 2026-09-07 v0.1

A 1D, reversible, valid-everywhere, Turing-complete variant of BFF.
The design comes from a ChatGPT conversation forwarded by a friend of
Steve's; this file is an independent re-implementation from the prose
spec, plus a REPL and example programs.  See docs/rbff_notes.md.

State: (tape, a, b, p).  tape is N bytes; a, b are data heads; p is the
instruction pointer.  All three wrap modulo N.  Each step executes the
instruction at tape[p], then p += 1 (always, including after a jump, so
a jump lands one past the partner bracket).

  <  a -= 1          >  a += 1
  {  b -= 1          }  b += 1
  -  tape[a] -= 1    +  tape[a] += 1
  .  tape[b] ^= tape[a]
  ,  tape[a] ^= tape[b]
  [  if tape[a] != 0: p = matching ]      (enter body only when zero)
  ]  if tape[a] != 0: p = matching [      (repeat body while nonzero)
  anything else: no-op

Three guards make every step a bijection on the whole state space:
  * a write whose target is tape[p] (the executing byte) is a no-op
  * an XOR with a == b is a no-op (x ^ x = 0 would lose information)
  * an unmatched bracket is a no-op; matching uses ordinary nesting on
    the linear tape 0..N-1 (no wrap)

step_back() is purely deductive: p -= 1, read tape[p], apply the inverse.
No history register, no trail, no clean-fuel precondition.

Loop semantics: `[body]` is Janus's `from x==0 loop body until x==0`.
Enter only if tape[a]==0; after the body, repeat if tape[a]!=0.  Both
brackets jump on the SAME condition (nonzero), which is what makes the
join point unambiguous: landing after a bracket with tape[a]!=0 means
you jumped; with tape[a]==0 means you fell through.

Usage:
  python3 rbff.py            REPL (type help)
  python3 rbff.py --test     verification suite
"""

import sys
import itertools
import random

try:
    import readline  # noqa: F401  (line editing in the REPL)
except ImportError:
    pass

OPS = b'<>{}-+.,[]'
OP_DOC = [
    ('<', 'a -= 1'), ('>', 'a += 1'),
    ('{', 'b -= 1'), ('}', 'b += 1'),
    ('-', 'tape[a] -= 1'), ('+', 'tape[a] += 1'),
    ('.', 'tape[b] ^= tape[a]'), (',', 'tape[a] ^= tape[b]'),
    ('[', 'if tape[a] != 0: jump to matching ]  (enter body when zero)'),
    (']', 'if tape[a] != 0: jump to matching [  (repeat while nonzero)'),
    ('other', 'no-op'),
]


def match_table(tape):
    """Stack matching over linear order.  Returns dict pos -> partner."""
    st, mt = [], {}
    for i, c in enumerate(tape):
        if c == 91:          # [
            st.append(i)
        elif c == 93 and st:  # ]
            j = st.pop()
            mt[i] = j
            mt[j] = i
    return mt


class RBFF:
    def __init__(self, size=256):
        self.tape = [0] * size
        self.a = self.b = self.p = 0
        self.steps = 0
        self._mt = None          # cached bracket matching
        self.code_end = 0        # for display / halt detection only
        self.segments = []       # [(label, text)] for annotated display
        self.example = None

    # ---------------------------------------------------------------- core
    @property
    def N(self):
        return len(self.tape)

    def matches(self):
        if self._mt is None:
            self._mt = match_table(self.tape)
        return self._mt

    def _write(self, addr, val):
        if addr == self.p:      # executing-byte guard
            return
        self.tape[addr] = val & 0xFF
        self._mt = None

    def _exec(self, inverse=False):
        """Execute tape[p] in place without touching p (except brackets)."""
        t, a, b, p, N = self.tape, self.a, self.b, self.p, self.N
        c = t[p]
        sgn = -1 if inverse else 1
        if c == 60:   self.a = (a - sgn) % N          # <
        elif c == 62: self.a = (a + sgn) % N          # >
        elif c == 123: self.b = (b - sgn) % N         # {
        elif c == 125: self.b = (b + sgn) % N         # }
        elif c == 45: self._write(a, t[a] - sgn)      # -
        elif c == 43: self._write(a, t[a] + sgn)      # +
        elif c == 46:                                  # .
            if a != b:
                self._write(b, t[b] ^ t[a])
        elif c == 44:                                  # ,
            if a != b:
                self._write(a, t[a] ^ t[b])
        elif c in (91, 93):                            # [ ]
            mt = self.matches()
            if p in mt and t[a] != 0:
                self.p = mt[p]

    def step(self):
        self._exec()
        self.p = (self.p + 1) % self.N
        self.steps += 1

    def step_back(self):
        self.p = (self.p - 1) % self.N
        self._exec(inverse=True)
        self.steps -= 1

    def state(self):
        return (tuple(self.tape), self.a, self.b, self.p)

    def run(self, max_steps, stop_at=None):
        """Run up to max_steps; stop early if p reaches stop_at (after at
        least one step).  Returns number of steps taken."""
        n = 0
        while n < max_steps:
            self.step()
            n += 1
            if stop_at is not None and self.p == stop_at:
                break
        return n

    # ------------------------------------------------------------- loading
    def load_program(self, segments, size=None, code_at=0):
        if size:
            self.tape = [0] * size
        else:
            self.tape = [0] * self.N
        code = ''.join(text for _, text in segments)
        for i, ch in enumerate(code):
            self.tape[code_at + i] = ord(ch)
        self.segments = segments
        self.code_end = code_at + len(code)
        self.a = self.b = self.p = 0
        self.steps = 0
        self._mt = None

    def segment_at(self, p):
        """Label of the segment containing code offset p, plus offset."""
        off = 0
        for label, text in self.segments:
            if off <= p < off + len(text):
                return label, p - off
            off += len(text)
        return None, None

    # ------------------------------------------------------------- display
    def display(self, lo=None, hi=None, width=16):
        code = ''.join(chr(c) if c in OPS else '.'
                       for c in self.tape[:self.code_end])
        print(f"step {self.steps}   p={self.p}  a={self.a}  b={self.b}")
        # code with IP caret, in chunks
        for start in range(0, len(code), 64):
            chunk = code[start:start + 64]
            print(f"  code[{start:3d}]: {chunk}")
            if start <= self.p < start + len(chunk):
                print(' ' * (13 + self.p - start) + '^')
        if self.p < self.code_end:
            label, off = self.segment_at(self.p)
            if label:
                print(f"  IP in segment: {label}  (+{off})  "
                      f"next op: {chr(self.tape[self.p])!r}")
        else:
            print(f"  IP outside code (p={self.p}) -- program has halted")
        # data window
        if lo is None:
            lo = self.code_end
        if hi is None:
            hi = min(self.N, lo + 48)
        for start in range(lo, hi, width):
            cells = self.tape[start:min(hi, start + width)]
            print(f"  {start:4d}: " + ''.join(f"{v:4d}" for v in cells))
            marks = ''
            for i in range(len(cells)):
                addr = start + i
                m = ('a' if addr == self.a else '') + \
                    ('b' if addr == self.b else '') + \
                    ('p' if addr == self.p else '')
                marks += f"{m:>4s}"
            if marks.strip():
                print("        " + marks)


# ==================================================================== macros
#
# Position-relative macros for building programs.  Heads are named a, b.
#
# IF-ZERO:   with a on cell x, `[ X ]` runs X iff x == 0, provided X
#            leaves x unchanged and returns a to x.  (Enter when zero,
#            then ] sees zero and falls through.)
#
# FOR(xoff, work): garbage-free counted loop, "for c in 1..x: work".
#   Scratch cells t, c, d at consecutive addresses; x at t+xoff.
#   Heads a and b both sit on t before and after.  Invariants:
#     d == c ^ x  (so d == 0 iff c == x)
#     t == c ^ (x if c == x else 0)  -> t == 0 exactly at c==0 and c==x
#   t is the loop-test cell: zero on entry, zero at exit, nonzero in
#   between.  Each iteration uncomputes t and d, runs work, c += 1,
#   recomputes d and t.  After the loop c == x; CLEAN xors it back to 0.
#   work runs with a on c and b on t and must restore both.
#   Requires 1 <= x <= 255 (x == 0 runs 256 times) and xoff not in {0,1,2}.

def mv(head, k):
    if head == 'a':
        return ('>' if k > 0 else '<') * abs(k)
    return ('}' if k > 0 else '{') * abs(k)


def FOR(xoff, work, label='for'):
    b_to_x, b_back = mv('b', xoff), mv('b', -xoff)
    init = '>>' + b_to_x + ',' + b_back + '<<'          # d ^= x
    cond = '<<' + b_to_x + ',' + b_back + '>>'          # t ^= x  (a on d)
    clean = '>' + b_to_x + ',' + b_back + '<'           # c ^= x
    return [
        (f'{label}: init d=c^x', init),
        (f'{label}: [', '['),
        (f'{label}: uncompute t', '>>[' + cond + ']<.'),
        (f'{label}: uncompute d', '}}.{{'),
        (f'{label}: work', work),
        (f'{label}: c+=1', '+'),
        (f'{label}: recompute d', '}}.{{'),
        (f'{label}: recompute t', '.>[' + cond + ']<<'),
        (f'{label}: ]', ']'),
        (f'{label}: clean c', clean),
    ]


def ADD(xoff, dst_off_from_c, label='add'):
    """dst += x.  dst at c+dst_off_from_c, x at t+xoff."""
    work = mv('a', dst_off_from_c) + '+' + mv('a', -dst_off_from_c)
    return FOR(xoff, work, label)


# ================================================================== examples

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
    for i, ch in enumerate(b'RBFF'):
        m.tape[src + 1 + i] = ch
    m.a, m.b = src, dst          # both on the zero cells before the regions
    m._mt = None
    return dict(lo=src, hi=dst + 8,
                note="a on zero before source (8), b on zero before dest (20).\n"
                     "[ enters because tape[a]==0; ] repeats while the\n"
                     "source byte is nonzero.  Watch dest fill in.")


@example('counter', "unbounded binary counter [>[,>][,<,]<-+] (from the ChatGPT design)")
def ex_counter(m):
    m.load_program([('outer [', '['), ('to sentinel', '>'),
                    ('clear trailing ones', '[,>]'),
                    ('set bit, walk back', '[,<,]'),
                    ('home, -+, loop', '<-+]')], size=64)
    W = 16
    m.a = m.b = W
    m.p = 13                     # start at the final '+'
    return dict(lo=W, hi=W + 16,
                note="tape[16]=constant 1 (made at startup), tape[17]=sentinel,\n"
                     "tape[18..]=bits, LSB first.  Starts at the '+' (p=13):\n"
                     "creates the constant, ] jumps back to after [.\n"
                     "Use `loop` to run one increment (until p==1).")


@example('add', "acc += x using the garbage-free FOR idiom (see docs/rbff_notes.md)")
def ex_add(m):
    m.load_program([('heads to t', '>}')] + ADD(xoff=3, dst_off_from_c=-2),
                   size=128)
    D = m.code_end + 2           # acc, t, c, d, x
    m.tape[D + 4] = 5
    m.a = m.b = D
    m._mt = None
    return dict(lo=D, hi=D + 8,
                note=f"layout: acc t c d x  at {D}..{D+4}, x=5.\n"
                     "t is the loop-test cell (zero on entry and exit).\n"
                     "d = c^x, c counts 0..x.  Ends with acc=5, scratch all zero.")


def fib_segments():
    S = 4  # slot stride: S t c d
    walk = mv('a', S) + mv('b', S)
    back = mv('a', -S) + mv('b', -S)
    segs = [('outer [', '['), ('K += 1', '+'), ('a to Z', '>'),
            ('walk to first zero slot', '[' + walk + ']'),
            ('heads to t', '>}')]
    segs += ADD(xoff=-5, dst_off_from_c=-2, label='add S[-1]')
    segs += ADD(xoff=-9, dst_off_from_c=-2, label='add S[-2]')
    segs += [('heads to S', '<{'), ('advance', walk),
             ('walk back to Z', '[' + back + ']'),
             ('a to K', '<'), ('outer ]', ']')]
    return segs


@example('fib', "Fibonacci sequence, one number per 4-cell slot")
def ex_fib(m):
    segs = fib_segments()
    m.load_program(segs, size=1400)
    D = m.code_end + 2           # K at D, Z at D+1, slot j's S at D+1+4j
    m.tape[D + 1 + 4] = 1        # S_0
    m.tape[D + 1 + 8] = 1        # S_1
    m.a, m.b = D, D + 1
    m._mt = None
    return dict(lo=D, hi=D + 1 + 4 * 12,
                note=f"K (iteration count) at {D}, Z at {D+1}, slots of 4 cells\n"
                     f"[S t c d] from {D+5}.  Each pass walks to the first empty\n"
                     "slot and adds the two previous S values into it with two\n"
                     "FOR loops.  `loop` runs one pass.  Values are mod 256.")


def fact_segments():
    S = 8  # slot stride: S t1 c1 d1 t2 c2 d2 k
    walk = mv('a', S) + mv('b', S)
    back = mv('a', -S) + mv('b', -S)
    inner = [('  heads to t2', '>>}}}')] + \
        ADD(xoff=-12, dst_off_from_c=-5, label='  inner add S[-1]') + \
        [('  heads back', '<<{{{')]
    inner_text = ''.join(t for _, t in inner)
    segs = [('outer [', '['), ('K += 1', '+'), ('a to Z', '>'),
            ('walk to first zero slot', '[' + walk + ']'),
            ('k = k[-1] + 1', '>>>>>>>{,+}<<<<<<<'),
            ('heads to t1', '>}')]
    segs += FOR(xoff=6, work=inner_text, label='mul: for k')
    segs += [('heads to S', '<{'), ('advance', walk),
             ('walk back to Z', '[' + back + ']'),
             ('a to K', '<'), ('outer ]', ']')]
    return segs


@example('fact', "factorials, one per 8-cell slot: S = S[-1] * k via nested FOR")
def ex_fact(m):
    segs = fact_segments()
    m.load_program(segs, size=1400)
    D = m.code_end + 2
    m.tape[D + 1 + 8] = 1        # S_1 = 1
    m.tape[D + 1 + 8 + 7] = 1    # k = 1
    m.a, m.b = D, D + 1
    m._mt = None
    return dict(lo=D, hi=D + 1 + 8 * 9,
                note=f"K at {D}, Z at {D+1}, slots of 8 [S t1 c1 d1 t2 c2 d2 k]\n"
                     f"from {D+9}.  Each pass: new k = previous k + 1, then\n"
                     "S = S[-1] * k as a FOR over k whose body is a FOR-add.\n"
                     "`loop` runs one pass.  Mod 256: 6!=208, 7!=176, 8!=128.")


# ===================================================================== tests

def _exhaustive(N, alphabet):
    n, seen, bad = 0, set(), 0
    for tape in itertools.product(alphabet, repeat=N):
        for a in range(N):
            for b in range(N):
                for p in range(N):
                    m = RBFF(N)
                    m.tape = list(tape); m.a, m.b, m.p = a, b, p
                    s0 = m.state()
                    m.step(); s1 = m.state()
                    m.step_back()
                    if m.state() != s0:
                        bad += 1
                    seen.add(s1); n += 1
    print(f"  exhaustive N={N} |alpha|={len(alphabet)}: {n} states, "
          f"{len(seen)} images, round-trip failures {bad}")
    return n == len(seen) and bad == 0


def _reverse_check(m, nsteps, expect_state):
    for _ in range(nsteps):
        m.step_back()
    return m.state() == expect_state


def run_tests():
    ok = True
    print("== bijectivity ==")
    alpha = list(OPS) + [0, 1]
    ok &= _exhaustive(3, alpha)
    ok &= _exhaustive(4, list(b'+-<[].') + [0, 1])
    rng = random.Random(1); bad = 0
    for _ in range(200):
        N = rng.randint(4, 24)
        m = RBFF(N)
        m.tape = [rng.choice(alpha) if rng.random() < 0.9 else rng.randrange(256)
                  for _ in range(N)]
        m.a, m.b, m.p = rng.randrange(N), rng.randrange(N), rng.randrange(N)
        s0 = m.state()
        for _ in range(500):
            m.step()
        if not _reverse_check(m, 500, s0):
            bad += 1
    print(f"  200 random self-modifying runs x 500 steps: reversal failures {bad}")
    ok &= bad == 0

    print("== examples ==")
    m = RBFF()
    EXAMPLES['copy'][1](m)
    s0 = m.state(); n = m.run(100, stop_at=m.code_end)
    got = bytes(m.tape[21:25])
    print(f"  copy: {got!r} in {n} steps, reversed={_reverse_check(m, n, s0)}")
    ok &= got == b'RBFF'

    m = RBFF()
    EXAMPLES['counter'][1](m)
    s0 = m.state(); m.step(); m.step(); total = 2
    good = True
    for n in range(1, 3001):
        total += m.run(10**6, stop_at=1)
        val = sum(bit << i for i, bit in enumerate(m.tape[18:40]))
        if val != n or m.tape[16] != 1 or m.tape[17] != 0:
            good = False; break
    print(f"  counter: 3000 increments ok={good}, {total} steps "
          f"(formula {2 + 21*3000 - 7*bin(3000).count('1')}), "
          f"reversed={_reverse_check(m, total, s0)}")
    ok &= good

    m = RBFF()
    EXAMPLES['add'][1](m)
    s0 = m.state(); n = m.run(10**5, stop_at=m.code_end)
    D = m.code_end + 2
    cells = m.tape[D:D + 5]
    print(f"  add: cells={cells} in {n} steps, reversed={_reverse_check(m, n, s0)}")
    ok &= cells == [5, 0, 0, 0, 5]

    m = RBFF()
    info = EXAMPLES['fib'][1](m)
    s0 = m.state(); D = m.code_end + 2; total = 0
    for _ in range(11):
        total += m.run(10**6, stop_at=1)
    got = [m.tape[D + 1 + 4 * j] for j in range(1, 13)]
    want = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]
    scratch_clean = all(m.tape[D + 1 + 4 * j + k] == 0
                        for j in range(1, 13) for k in (1, 2, 3))
    print(f"  fib: {got} in {total} steps, scratch clean={scratch_clean}, "
          f"reversed={_reverse_check(m, total, s0)}")
    ok &= got == want and scratch_clean

    m = RBFF()
    EXAMPLES['fact'][1](m)
    s0 = m.state(); D = m.code_end + 2; total = 0
    for _ in range(8):           # first run is the single step [ -> p=1
        total += m.run(10**7, stop_at=1)
    got = [m.tape[D + 1 + 8 * j] for j in range(1, 9)]
    ks = [m.tape[D + 1 + 8 * j + 7] for j in range(1, 9)]
    want = [1, 2, 6, 24, 120, 720 % 256, 5040 % 256, 40320 % 256]
    print(f"  fact: S={got} k={ks} in {total} steps, "
          f"reversed={_reverse_check(m, total, s0)}")
    ok &= got == want and ks == list(range(1, 9))

    print("ALL OK" if ok else "FAILURES")
    return ok


# ====================================================================== REPL

HELP = """\
commands:
  examples         list example programs        load NAME    load one
  d [lo hi]        display code + data window   src          show segments
  s [n]            step forward n (default 1)   b [n]        step back n
  run [n]          run until halt or n steps (default 100000)
  loop             run until IP returns to p=1 (one outer-loop pass)
  until P          run until p == P
  trace n          step n times, printing each op
  set ADDR VAL     poke a tape cell             head a|b|p ADDR   move a head
  state            show heads and step count    ops          list opcodes
  q                quit
"""


def repl():
    m = RBFF()
    view = {}

    def show():
        m.display(view.get('lo'), view.get('hi'))

    def load(name):
        nonlocal view
        if name not in EXAMPLES:
            print("unknown example; try `examples`"); return
        info = EXAMPLES[name][1](m)
        m.example = name
        view = {'lo': info.get('lo'), 'hi': info.get('hi')}
        print(f"loaded {name}: {EXAMPLES[name][0]}")
        print(info.get('note', ''))
        show()

    print("RBFF REPL -- type help.  Loading `counter`.")
    load('counter')
    while True:
        try:
            line = input("rbff> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
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
                    mark = '->' if off <= m.p < off + len(text) else '  '
                    print(f"{mark} {off:4d} {label:28s} {text}")
                    off += len(text)
            elif cmd == 's':
                for _ in range(int(args[0]) if args else 1):
                    m.step()
                show()
            elif cmd == 'b':
                for _ in range(int(args[0]) if args else 1):
                    m.step_back()
                show()
            elif cmd == 'run':
                n = m.run(int(args[0]) if args else 100000, stop_at=m.code_end)
                print(f"ran {n} steps"); show()
            elif cmd == 'loop':
                n = m.run(10**7, stop_at=1)
                print(f"ran {n} steps"); show()
            elif cmd == 'until':
                n = m.run(10**7, stop_at=int(args[0]))
                print(f"ran {n} steps"); show()
            elif cmd == 'trace':
                for _ in range(int(args[0])):
                    op = chr(m.tape[m.p]) if m.tape[m.p] in OPS else 'nop'
                    label, _ = m.segment_at(m.p)
                    m.step()
                    print(f"  {m.steps:6d}: {op:3s} -> p={m.p:4d} a={m.a:4d} "
                          f"b={m.b:4d} [a]={m.tape[m.a]:3d} [b]={m.tape[m.b]:3d}"
                          f"   {label or ''}")
                show()
            elif cmd == 'set':
                m.tape[int(args[0])] = int(args[1]) & 0xFF; m._mt = None; show()
            elif cmd == 'head':
                setattr(m, args[0], int(args[1]) % m.N); show()
            elif cmd == 'state':
                print(f"step {m.steps} p={m.p} a={m.a} b={m.b} N={m.N} "
                      f"code_end={m.code_end} example={m.example}")
            else:
                print("unknown command; type help")
        except (IndexError, ValueError) as e:
            print(f"bad arguments ({e}); type help")


if __name__ == '__main__':
    if '--test' in sys.argv:
        sys.exit(0 if run_tests() else 1)
    repl()

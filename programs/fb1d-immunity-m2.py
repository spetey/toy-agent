#!/usr/bin/env python3
"""
fb1d immunity gadget, M2: one gadget correcting a static partner block.
Authored or modified by Claude
Version: 2026-09-30 v0.2 (fixed stomach + roaming b, using fb1d's P/Q;
v0.1 of 2026-09-26 used a moving 3-cell frame, see the port plan)

A single IP runs a bracket program that walks its interoceptor `ix`
across a partner code block (a static copy of its own code, no partner
IP), probes every cell with `I`, and repairs any single-bit error with
`V` + `j`.  Garbage goes into a fuel trail behind a roaming head `b`,
exactly as fb2d's EX row.

Tape layout (all position-relative; nothing in the code is absolute):

    ~ [gadget code] ~ [s g] [L] [fuel zeros ....] ~ [partner code] ~
                       ^a    ^b                      ^ix

Stomach (fixed): s, the probe / mask cell under head a, and g, the
flag cell; both zero between cells.  Head b sits on L, the last garbage
cell of the trail, which is always nonzero; every cell after it is zero
fuel.  Initially L is a seeded cell with payload 1.

Per partner cell (WORK, 26 ops; a on s, b on L):
    P              L += 1                      (trace, see below)
    I              s ^= syndrome5(tape[ix])    s != 0 iff the cell has an error
    [>+<]          g = 1 iff s == 0            (clean flag)
    >[ ... ]       dirty-only block, entered iff g == 0:
      <IVj+          s ^= syndrome5 (-> 0), s = 1 << syndrome4 (the mask),
                     tape[ix] ^= s (repair), s payload += 1 (nonzero garbage)
      }.,}           b to the fresh cell L+1, fuel ^= s (dump), s ^= fuel (clear),
                     b to the fresh cell L+2
      >              a back to g (still zero)
    (+)            dirty-only (b's cell is zero): g += 1
    -              g -> 0 in both cases
    <              a to s
    P              clean: L += 1 (total +2); dirty: L+2 becomes 1, the new L

After a correction b has moved by 2 and left [mask', 1] behind: two
nonzero fuel cells per correction (fb2d's EV + PA cost).  The clean
path writes only the trace L += 2.

Why the trace.  Any merge of the clean and dirty paths must end with
both cases in the same configuration.  With a periodic garbage trail,
and brackets that can only exit with the tested head on a zero cell, a
brute force over all `[ ]`/`( )` programs up to 9 ops found no way to
do that without the clean path writing something.  fb2d pays the same
price with `P` on the EX cell on the bypass path, and so does this
gadget, now literally: `P` at both ends of WORK.  The trace is bounded
by the pass-end "moult" `}P`: b steps onto the next fresh cell, which
becomes L = 1.  One fuel cell per pass; L stays in [1, 4L_p+5] for a
partner block of L_p cells and never wraps.

Pass: WORK on the resting boundary, then a scan loop east, a rewind
loop west (both with WORK on every cell, and always WORK before a
boundary test so a flipped `~` is repaired before it is tested), then
the moult.  Boundary test idiom: `m+` puts payload(cell)+1 in s, which
is zero iff the cell is `~` (payload 2047); `-m` uncomputes it.

Run:  python3 programs/fb1d-immunity-m2.py          (tests)
      python3 programs/fb1d-immunity-m2.py --quick  (skip the full sweep)
REPL: python3 fb1d.py, then `load immunity`.  Browser: fb1d.html.
"""

import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import fb1d
from fb1d import Machine, IP, assemble, BOUNDARY, hamming_encode, is_dirty

# ─── Program ─────────────────────────────────────────────────────────

WORK = [
    ('  P: L += 1', 'P'),
    ('  probe s ^= I', 'I'),
    ('  g = 1 iff clean', '[>+<]'),
    ('  a to g', '>'),
    ('  dirty [', '['),
    ('    unprobe, mask, repair, bump', '<IVj+'),
    ('    dump mask, b to fresh', '}.,}'),
    ('    a to g', '>'),
    ('  dirty ]', ']'),
    ('  merge (+)', '(+)'),
    ('  clear flag', '-'),
    ('  a to s', '<'),
    ('  P: L += 1 / new L = 1', 'P'),
]


def sweep(direction, label):
    return ([(f'{label}: test ~', 'm+'), (f'{label} [', '['),
             (f'{label}: untest, step ix', '-m' + direction)]
            + [(label + l, t) for l, t in WORK]
            + [(f'{label}: test ~', 'm+'), (f'{label} ]', ']'),
               (f'{label}: untest', '-m')])


def gadget_segments():
    return ([('outer [', '['), ('outer -', '-')]
            + [('rest' + l, t) for l, t in WORK]
            + sweep('A', 'scan') + sweep('B', 'rewind')
            + [('moult: b on, L = 1', '}P'),
               ('outer +', '+'), ('outer ]', ']')])


def gadget_text():
    return ''.join(t for _, t in gadget_segments())


# ─── Layout ──────────────────────────────────────────────────────────

class Layout:
    """Addresses of the parts of a built tape."""

    def __init__(self, code_len, fuel):
        self.code = 1                      # gadget code starts after `~`
        self.code_len = code_len
        self.s = code_len + 2              # stomach: s, g
        self.g = self.s + 1
        self.L0 = self.g + 1               # first garbage cell (seeded 1), b here
        self.fuel = fuel                   # zeros after L0
        self.p_lead = self.L0 + 1 + fuel   # partner's leading `~`
        self.p_code = self.p_lead + 1
        self.p_trail = self.p_code + code_len       # partner's trailing `~`
        self.size = self.p_trail + 1
        self.p_start = 3                   # first op of the rest-WORK

    def partner_range(self):
        """Inclusive address range of the partner block, boundaries included."""
        return self.p_lead, self.p_trail


def build(fuel=400, partner=None):
    """Return (Machine, Layout).  partner: list of cells for the partner
    block (default: a copy of the gadget code)."""
    code = assemble(gadget_text())
    if partner is None:
        partner = list(code)
    assert len(partner) == len(code)
    lay = Layout(len(code), fuel)
    m = Machine(lay.size)
    m.segments = [('~', '~')] + gadget_segments() + [('~', '~')]
    m.tape[0] = BOUNDARY
    m.tape[lay.code:lay.code + len(code)] = code
    m.tape[lay.code + len(code)] = BOUNDARY
    m.poke(lay.L0, 1)
    m.tape[lay.p_lead] = BOUNDARY
    m.tape[lay.p_code:lay.p_code + len(code)] = partner
    m.tape[lay.p_trail] = BOUNDARY
    m.code_end = lay.code + len(code) + 1
    ip = m.ips[0]
    ip.a, ip.b, ip.ix, ip.p = lay.s, lay.L0, lay.p_lead, lay.p_start
    m.invalidate()
    return m, lay


def run_pass(m, lay, limit=10**7):
    """Run one full pass (until the IP is back at the rest-WORK)."""
    n = m.run(limit, stop_at=lay.p_start)
    assert m.ips[0].p == lay.p_start, "pass did not complete"
    return n


def stomach_state(m, lay):
    """(b address, payload at b, s, g) if a is on s, else None."""
    ip = m.ips[0]
    if ip.a != lay.s:
        return None
    return ip.b, m.peek(ip.b), m.peek(lay.s), m.peek(lay.g)


def healthy(fs):
    return fs is not None and fs[1] != 0 and fs[2] == 0 and fs[3] == 0


def partner_ok(m, lay, ref):
    lo, hi = lay.partner_range()
    return m.tape[lo:hi + 1] == ref


# ─── Tests ───────────────────────────────────────────────────────────

def _check(ok, msg):
    print(f"  [{'ok' if ok else 'FAIL'}] {msg}")
    return ok


def test_clean(passes=5):
    m, lay = build()
    lo, hi = lay.partner_range()
    ref = m.tape[lo:hi + 1]
    s0 = m.state()
    steps, ok = [], True
    for k in range(passes):
        steps.append(run_pass(m, lay))
        fs = stomach_state(m, lay)
        ok &= partner_ok(m, lay, ref) and healthy(fs)
        ok &= fs[0] == lay.L0 + k + 1 and fs[1] == 1
    total = sum(steps)
    for _ in range(total):
        m.step_back_all()
    ok &= m.state() == s0
    L = lay.code_len
    return _check(ok, f"clean: {passes} passes x {steps[0]} steps (L={L}, "
                      f"{steps[0] / (2 * L + 3):.1f} steps/cell-visit), b +1 per pass, "
                      f"L back to 1, stomach fixed and zero, partner untouched, reversed")


def test_single_flips(quick=False):
    """Every cell of the partner block (boundaries included) x every bit."""
    m0, lay = build()
    lo, hi = lay.partner_range()
    ref = m0.tape[lo:hi + 1]
    cells = range(lo, hi + 1)
    bits = range(16) if not quick else (0, 3, 8, 15)
    n = bad = 0
    garbage, steps = set(), []
    rev_checked = 0
    for addr in cells:
        for bit in bits:
            m, _ = build()
            m.tape[addr] ^= 1 << bit
            m.invalidate()
            s0 = m.state()
            st = run_pass(m, lay)
            fs = stomach_state(m, lay)
            good = partner_ok(m, lay, ref) and healthy(fs) and fs[1] == 1
            if good:
                garbage.add(fs[0] - lay.L0)
                # the trail must be contiguous nonzero-payload cells
                trail = [m.peek(x) for x in range(lay.L0, fs[0] + 1)]
                good &= all(v != 0 for v in trail)
            if good and rev_checked < 40 and (addr * 16 + bit) % 37 == 0:
                for _ in range(st):
                    m.step_back_all()
                good &= m.state() == s0
                rev_checked += 1
            steps.append(st)
            n += 1
            bad += not good
    ok = bad == 0 and garbage == {3}   # 1 (moult) + 2 (correction)
    return _check(ok, f"single-bit errors: {n} (cell, bit) cases, {bad} failures; "
                      f"fuel used per pass with one correction: {sorted(garbage)} "
                      f"(1 moult + 2 garbage); {rev_checked} reversed exactly")


def test_two_flips_same_pass():
    """Two single-bit errors in different cells, both fixed in one pass."""
    m0, lay = build()
    lo, hi = lay.partner_range()
    ref = m0.tape[lo:hi + 1]
    rng = random.Random(5)
    ok, n = True, 0
    for _ in range(60):
        m, _ = build()
        a1, a2 = rng.sample(range(lo, hi + 1), 2)
        m.tape[a1] ^= 1 << rng.randrange(16)
        m.tape[a2] ^= 1 << rng.randrange(16)
        m.invalidate()
        run_pass(m, lay)
        fs = stomach_state(m, lay)
        ok &= partner_ok(m, lay, ref) and healthy(fs) and fs[0] - lay.L0 == 5
        n += 1
    return _check(ok, f"two errors per pass: {n} cases, b +5 (1 moult + 2x2)")


def test_noise(rate=300, passes=60, seed=11):
    """NoisePool over the partner block.  Every cell that carries a
    single-bit error at the end of a pass must be clean after the next
    pass; cells hit twice between passes (2-bit errors) are counted and
    excluded (M5 adds copy-over for those)."""
    m, lay = build(fuel=800)
    lo, hi = lay.partner_range()
    ref = m.tape[lo:hi + 1]
    m.set_noise(rate, lo, hi, seed=seed)
    s0 = m.state()
    total = corrected = missed = double = 0
    for _ in range(passes):
        before = m.tape[lo:hi + 1]
        singles = [i for i, (x, y) in enumerate(zip(before, ref))
                   if x != y and bin(x ^ y).count('1') == 1]
        double += sum(1 for x, y in zip(before, ref)
                      if x != y and bin(x ^ y).count('1') > 1)
        total += run_pass(m, lay)
        after = m.tape[lo:hi + 1]
        for i in singles:
            if after[i] == ref[i]:
                corrected += 1
            else:
                missed += 1
    flips = m.noise.total_injected
    fs = stomach_state(m, lay)
    good = healthy(fs)
    for _ in range(total):
        m.step_back_all()
    rev = m.state() == s0
    ok = missed == 0 and good and rev and corrected > 0
    return _check(ok, f"noise {rate}/1M over the partner for {passes} passes "
                      f"({total} rounds, {flips} flips): {corrected} single-bit errors "
                      f"corrected, {missed} missed, {double} multi-bit seen (not "
                      f"handled until M5); stomach healthy; {total}-round reversal exact")


def run_tests(quick=False):
    ok = True
    print(f"== fb1d immunity gadget M2: {len(assemble(gadget_text()))} code cells ==")
    ok &= test_clean()
    ok &= test_single_flips(quick)
    ok &= test_two_flips_same_pass()
    ok &= test_noise()
    print("ALL OK" if ok else "FAILURES")
    return ok


if __name__ == '__main__':
    sys.exit(0 if run_tests(quick='--quick' in sys.argv) else 1)

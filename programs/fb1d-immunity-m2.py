#!/usr/bin/env python3
"""
fb1d immunity gadget, M2: one gadget correcting a static partner block.
Authored or modified by Claude
Version: 2026-09-26 v0.1 (M2 of docs/fb1d-port-plan.md)

A single IP runs a bracket program that walks its interoceptor `ix`
across a partner code block (a static copy of its own code, no partner
IP), probes every cell with `I`, and repairs any single-bit error with
`V` + `j`.  Garbage goes into a fuel trail via a moving "frame".

Tape layout (all position-relative; nothing in the code is absolute):

    ~ [gadget code] ~ [M s g] [fuel zeros ....] ~ [partner code] ~
                       ^b ^a                      ^ix

Frame (3 cells, moves right through the fuel):
    M  marker under head b; nonzero at every cell boundary
    s  probe / mask cell under head a; zero between cells
    g  flag cell; zero between cells

Per partner cell (WORK, 34 ops; a on s, b on M):
    <+>            M += 1                      (drift, see below)
    I              s ^= syndrome5(tape[ix])    s != 0 iff the cell has an error
    [>+<]          g = 1 iff s == 0            (clean flag)
    >[ ... ]       dirty-only block, entered iff g == 0:
      <IVj+>         s ^= syndrome5 (-> 0), s = 1 << syndrome4 (the mask),
                     tape[ix] ^= s (repair), s payload += 1 (nonzero garbage)
      }}}>>          b to the fresh cell s' = M+3, a to the fresh cell g' = M+4
    (+)            dirty-only (b's cell is zero): a's cell += 1
    -              clean: g -> 0; dirty: that fresh cell -> 0
    <              clean: a -> s; dirty: a -> s'
    ({)            dirty-only: b -> M' = M+2 (the old g, zero)
    <+>            clean: M += 1; dirty: M' = 1

After a correction the frame has moved by 2 and left [M_old, mask'] as
garbage: two nonzero fuel cells per correction (fb2d's EV + PA cost).
The clean path leaves no fuel trace, only M += 2.

Why the drift.  Any merge of the clean and dirty paths must end with
both cases in the same configuration relative to the frame.  With a
garbage trail that is a fixed pattern repeated per correction, and
brackets that can only exit on a zero cell, no bracket program can do
that without some cell changing on the clean path as well: a brute
force over all `[ ]`/`( )` programs up to 9 ops (scratch searches for
this milestone) finds none.  fb2d has the same requirement and pays it
with `P` on the EX cell on the bypass path.  Here the price is
M += 2 per clean cell.  M is reset by the pass-end "moult" `}+>`: b and
a step right by one, the old s becomes M' = 1, the old g becomes s'.
That costs one fuel cell per pass and keeps M in [1, 4L+5], so it never
wraps to zero for partner blocks up to L = 510 cells.

Pass: WORK on the resting boundary, then a scan loop east, a rewind
loop west (both with WORK on every cell, and always WORK before a
boundary test so a flipped `~` is repaired before it is tested), then
the moult.  Boundary test idiom: `m+` puts payload(cell)+1 in s, which
is zero iff the cell is `~` (payload 2047); `-m` uncomputes it.

Run:  python3 programs/fb1d-immunity-m2.py          (tests)
      python3 programs/fb1d-immunity-m2.py --quick  (skip the full sweep)
REPL: python3 fb1d.py, then `load immunity`.
"""

import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import fb1d
from fb1d import Machine, IP, assemble, BOUNDARY, hamming_encode, is_dirty

# ─── Program ─────────────────────────────────────────────────────────

WORK = [
    ('  M += 1', '<+>'),
    ('  probe s ^= I', 'I'),
    ('  g = 1 iff clean', '[>+<]'),
    ('  a to g', '>'),
    ('  dirty [', '['),
    ('    unprobe, mask, repair, bump', '<IVj+>'),
    ("    b to s', a to g'", '}}}>>'),
    ('  dirty ]', ']'),
    ('  merge (+)', '(+)'),
    ('  clear flag', '-'),
    ('  a to s', '<'),
    ("  b to M' ({)", '({)'),
    ("  M += 1 / M' = 1", '<+>'),
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
            + [("moult: b to s, M'=1, a to g", '}+>'),
               ('outer +', '+'), ('outer ]', ']')])


def gadget_text():
    return ''.join(t for _, t in gadget_segments())


# ─── Layout ──────────────────────────────────────────────────────────

class Layout:
    """Addresses of the parts of a built tape."""

    def __init__(self, code_len, fuel):
        self.code = 1                      # gadget code starts after `~`
        self.code_len = code_len
        self.frame0 = code_len + 3         # first frame base (M cell)
        self.fuel = fuel                   # zeros after the 3 frame cells
        self.p_lead = self.frame0 + 3 + fuel        # partner's leading `~`
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
    m.poke(lay.frame0, 1)                  # M = 1
    m.tape[lay.p_lead] = BOUNDARY
    m.tape[lay.p_code:lay.p_code + len(code)] = partner
    m.tape[lay.p_trail] = BOUNDARY
    m.code_end = lay.code + len(code) + 1
    ip = m.ips[0]
    ip.b, ip.a, ip.ix, ip.p = lay.frame0, lay.frame0 + 1, lay.p_lead, lay.p_start
    m.invalidate()
    return m, lay


def run_pass(m, lay, limit=10**7):
    """Run one full pass (until the IP is back at the rest-WORK)."""
    n = m.run(limit, stop_at=lay.p_start)
    assert m.ips[0].p == lay.p_start, "pass did not complete"
    return n


def frame_state(m):
    """(M address, M payload, s, g) with a on s and b on M, else None."""
    ip = m.ips[0]
    if ip.a != ip.b + 1:
        return None
    return ip.b, m.peek(ip.b), m.peek(ip.a), m.peek(ip.a + 1)


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
    steps, ok, base0 = [], True, m.ips[0].b
    for k in range(passes):
        steps.append(run_pass(m, lay))
        fs = frame_state(m)
        ok &= partner_ok(m, lay, ref) and fs is not None
        ok &= fs[0] == base0 + k + 1 and fs[1] == 1 and fs[2] == 0 and fs[3] == 0
    total = sum(steps)
    for _ in range(total):
        m.step_back_all()
    ok &= m.state() == s0
    L = lay.code_len
    return _check(ok, f"clean: {passes} passes x {steps[0]} steps (L={L}, "
                      f"{steps[0] / (2 * L + 2):.1f} steps/cell-visit), frame +1 per pass, "
                      f"M back to 1, partner untouched, reversed")


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
            base0 = m.ips[0].b
            st = run_pass(m, lay)
            fs = frame_state(m)
            good = (partner_ok(m, lay, ref) and fs is not None
                    and fs[1] == 1 and fs[2] == 0 and fs[3] == 0)
            if good:
                shift = fs[0] - base0
                garbage.add(shift)
                # the trail must be contiguous nonzero-payload cells
                trail = [m.peek(x) for x in range(base0, fs[0])]
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
        base0 = m.ips[0].b
        run_pass(m, lay)
        fs = frame_state(m)
        ok &= partner_ok(m, lay, ref) and fs is not None and fs[0] - base0 == 5
        n += 1
    return _check(ok, f"two errors per pass: {n} cases, frame +5 (1 moult + 2x2)")


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
    fs = frame_state(m)
    healthy = fs is not None and fs[1] == 1 and fs[2] == 0 and fs[3] == 0
    for _ in range(total):
        m.step_back_all()
    rev = m.state() == s0
    ok = missed == 0 and healthy and rev and corrected > 0
    return _check(ok, f"noise {rate}/1M over the partner for {passes} passes "
                      f"({total} rounds, {flips} flips): {corrected} single-bit errors "
                      f"corrected, {missed} missed, {double} multi-bit seen (not "
                      f"handled until M5); frame healthy; {total}-round reversal exact")


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

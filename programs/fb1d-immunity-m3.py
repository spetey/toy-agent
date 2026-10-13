#!/usr/bin/env python3
"""
fb1d immunity gadgets, M3: two gadgets correcting each other.
Authored or modified by Claude
Version: 2026-10-12 v0.1

Two copies of the M2 gadget (programs/fb1d-immunity-m2.py, unchanged)
on two IPs.  Each IP runs its own block; its interoceptor `ix` walks the
*other* block, so every cell of either program, boundaries included, is
probed with `I` twice per pass and any single-bit error is repaired with
`V` + `j` by the partner.  Nothing in the gadget code changed between
M2 and M3: the program is position-relative and the partner block was
already scanned between two `~` cells.

Tape layout (two gadgets, each as in M2):

    ~ [A code] ~ [sA gA] [LA] [A fuel ...] ~ [B code] ~ [sB gB] [LB] [B fuel ...]
    ^ixB         ^aA     ^bA               ^ixA         ^aB     ^bB

What is new is the interaction.  A's `j` may write a cell that B is
about to execute, or is sitting on: a single-bit error never changes
which op runs (fetch is parity-blind and 1-flip stable), so repairs are
invisible to the running partner, and reversibility holds because
step_back_all undoes IPs in reverse order.  Noise is applied over both
code blocks (stomach and fuel spared), as fb2d's harness applies it to
code and handler rows but not the stomach or waste rows.

Costs per gadget are those of M2: 2 fuel cells per correction, 1 per
pass (the moult), 4094 rounds per pass clean, ~12 more per correction.
2-bit errors are still miscorrected (M5 adds copy-over).

Run:  python3 programs/fb1d-immunity-m3.py          (tests, ~1 min)
      python3 programs/fb1d-immunity-m3.py --quick  (4 bits per cell)
REPL: python3 fb1d.py, then `load dual`.
MTTF: python3 programs/fb1d-mttf.py --help
"""

import os
import sys
import random
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fb1d import Machine, IP, assemble, BOUNDARY  # noqa: E402


def _load(filename, modname):
    spec = importlib.util.spec_from_file_location(
        modname, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m2 = _load('fb1d-immunity-m2.py', 'fb1d_immunity_m2')
gadget_text, gadget_segments = m2.gadget_text, m2.gadget_segments

CLEAN_PASS = 4094          # rounds per pass with nothing to correct (L=106)


# ─── Layout ──────────────────────────────────────────────────────────

class Gadget:
    """Addresses of one gadget: ~ code ~ s g L0 fuel..."""

    def __init__(self, base, code_len, fuel):
        self.lead = base                        # leading `~`
        self.code = base + 1
        self.code_len = code_len
        self.trail = self.code + code_len       # trailing `~`
        self.s = self.trail + 1                 # stomach: probe / mask cell
        self.g = self.s + 1                     # stomach: flag cell
        self.L0 = self.g + 1                    # first garbage cell (seeded 1)
        self.fuel_lo = self.L0 + 1
        self.fuel_hi = self.L0 + fuel           # last fuel zero
        self.end = self.fuel_hi + 1
        self.p_start = self.code + 2            # first op of the rest-WORK

    def block(self):
        """Inclusive range the partner's ix scans: ~ code ~."""
        return self.lead, self.trail


class Layout:
    def __init__(self, code_len, fuel):
        self.code_len = code_len
        self.fuel = fuel
        a = Gadget(0, code_len, fuel)
        b = Gadget(a.end, code_len, fuel)
        self.gadgets = [a, b]
        self.size = b.end

    def partner(self, i):
        return self.gadgets[1 - i]

    def blocks(self):
        return [g.block() for g in self.gadgets]


def build(fuel=400, barriers=True):
    """Return (Machine, Layout): two gadgets, two IPs, heads at home."""
    code = assemble(gadget_text())
    lay = Layout(len(code), fuel)
    m = Machine(lay.size, barriers=barriers)
    segs = []
    for name, g in zip('AB', lay.gadgets):
        m.tape[g.lead] = BOUNDARY
        m.tape[g.code:g.code + len(code)] = code
        m.tape[g.trail] = BOUNDARY
        segs +=([('~', '~')] + [(f'{name} {l}', t) for l, t in gadget_segments()]
                 + [('~', '~'), (f'{name} stomach s g', '00'),
                    (f'{name} L0 + fuel', '0' * (fuel + 1))])
    m.invalidate()
    for g in lay.gadgets:
        m.poke(g.L0, 1)
    m.segments = segs
    m.code_end = lay.gadgets[1].trail + 1
    m.ips = [IP(a=g.s, b=g.L0, ix=lay.partner(i).lead, p=g.p_start)
             for i, g in enumerate(lay.gadgets)]
    m.loop_stop = lay.gadgets[0].p_start
    return m, lay


def set_noise(m, lay, rate, seed=42):
    """Noise over both code blocks (boundaries included)."""
    m.set_noise(rate, seed=seed, blocks=lay.blocks())


# ─── Observation ─────────────────────────────────────────────────────

def stomach_state(m, lay, i):
    """(b, payload at b, s, g, ix) for gadget i if a is on s, else None."""
    ip, g = m.ips[i], lay.gadgets[i]
    if ip.a != g.s:
        return None
    return ip.b, m.peek(ip.b), m.peek(g.s), m.peek(g.g), ip.ix


def at_rest(fs, lay, i):
    """Healthy state at a pass start: L = 1 under b, stomach zero, ix on
    the partner's leading boundary."""
    return (fs is not None and fs[1] == 1 and fs[2] == 0 and fs[3] == 0
            and fs[4] == lay.partner(i).lead)


def block_ref(m, lay, i):
    lo, hi = lay.gadgets[i].block()
    return m.tape[lo:hi + 1]


def blocks_ok(m, lay, refs):
    return all(block_ref(m, lay, i) == refs[i] for i in range(2))


def run_passes(m, lay, n=1, limit=10**7):
    """Run until each IP has started a new pass n times.  Returns
    (rounds, states) with states[i] the stomach state of gadget i at each
    of its pass starts."""
    starts = [g.p_start for g in lay.gadgets]
    counts, states, r = [0, 0], [[], []], 0
    while r < limit and min(counts) < n:
        m.step_all()
        r += 1
        for i, ip in enumerate(m.ips):
            if ip.p == starts[i] and counts[i] < n:
                counts[i] += 1
                states[i].append(stomach_state(m, lay, i))
    assert min(counts) >= n, "a pass did not complete"
    return r, states


# ─── Tests ───────────────────────────────────────────────────────────

def _check(ok, msg):
    print(f"  [{'ok' if ok else 'FAIL'}] {msg}")
    return ok


def test_clean(passes=5):
    m, lay = build()
    refs = [block_ref(m, lay, i) for i in range(2)]
    s0 = m.state()
    ok, rounds = True, []
    for k in range(passes):
        r, states = run_passes(m, lay)
        rounds.append(r)
        ok &= blocks_ok(m, lay, refs)
        for i in range(2):
            fs = states[i][0]
            ok &= at_rest(fs, lay, i) and fs[0] == lay.gadgets[i].L0 + k + 1
    ok &= rounds[0] == CLEAN_PASS and len(set(rounds)) == 1
    total = sum(rounds)
    for _ in range(total):
        m.step_back_all()
    ok &= m.state() == s0
    return _check(ok, f"clean: {passes} passes x {rounds[0]} rounds, both IPs in "
                      f"lockstep, b +1 per pass each, stomachs zero, ix at rest, "
                      f"blocks untouched, {total}-round reversal exact")


def test_single_flips(quick=False):
    """Every cell of both blocks (boundaries included) x every bit, one
    error at a time, injected at a pass start.  The partner must repair
    it within one pass at a cost of 2 fuel cells; the victim pays only
    its moult."""
    m0, lay = build()
    refs = [block_ref(m0, lay, i) for i in range(2)]
    bits = range(16) if not quick else (0, 3, 8, 15)
    n = bad = rev_checked = 0
    cost = set()
    for i, g in enumerate(lay.gadgets):
        lo, hi = g.block()
        for addr in range(lo, hi + 1):
            for bit in bits:
                m, _ = build()
                m.flip(addr, bit)
                s0 = m.state()
                r, states = run_passes(m, lay)
                good = blocks_ok(m, lay, refs)
                fuel = []
                for k in range(2):
                    fs = states[k][0]
                    good &= at_rest(fs, lay, k)
                    if fs:
                        fuel.append(fs[0] - lay.gadgets[k].L0)
                        trail = [m.peek(x) for x in range(lay.gadgets[k].L0, fs[0] + 1)]
                        good &= all(v != 0 for v in trail)
                if good:
                    cost.add((fuel[1 - i], fuel[i]))   # (corrector, victim)
                if good and rev_checked < 40 and (addr * 16 + bit) % 53 == 0:
                    for _ in range(r):
                        m.step_back_all()
                    good &= m.state() == s0
                    rev_checked += 1
                n += 1
                bad += not good
    ok = bad == 0 and cost == {(3, 1)}
    return _check(ok, f"single-bit errors: {n} (block, cell, bit) cases, {bad} "
                      f"failures; fuel per pass (corrector, victim): {sorted(cost)} "
                      f"(1 moult + 2 garbage, 1 moult); {rev_checked} reversed exactly")


def test_errors_in_both_blocks():
    """Several single-bit errors in both blocks at once, both IPs
    correcting at the same time."""
    m0, lay = build()
    refs = [block_ref(m0, lay, i) for i in range(2)]
    rng = random.Random(5)
    ok, n = True, 0
    for _ in range(60):
        m, _ = build()
        k = [rng.randint(1, 3), rng.randint(1, 3)]
        for i, g in enumerate(lay.gadgets):
            lo, hi = g.block()
            for addr in rng.sample(range(lo, hi + 1), k[i]):
                m.flip(addr, rng.randrange(16))
        r, states = run_passes(m, lay, n=2)
        good = blocks_ok(m, lay, refs)
        for i in range(2):
            fs = states[i][-1]
            good &= at_rest(fs, lay, i)
            good &= fs[0] - lay.gadgets[i].L0 == 2 + 2 * k[1 - i]
        ok &= good
        n += 1
    return _check(ok, f"errors in both blocks: {n} cases of 1-3 errors per block, "
                      f"all repaired in one pass, fuel = 2 moults + 2 per repair")


def test_live_partner_cell():
    """An error on the cell the partner IP is executing, and on each
    bracket of its outer loop, is repaired while it keeps running."""
    m0, lay = build()
    refs = [block_ref(m0, lay, i) for i in range(2)]
    ok, n = True, 0
    gb = lay.gadgets[1]
    targets = [gb.p_start, gb.code, gb.code + gb.code_len - 1,
               gb.lead, gb.trail]
    for addr in targets:
        for bit in (0, 5, 15):
            m, _ = build()
            # let B run into the middle of its pass before injecting
            m.run(1500)
            m.flip(addr, bit)
            r, states = run_passes(m, lay, n=2)
            good = blocks_ok(m, lay, refs)
            good &= all(at_rest(states[i][-1], lay, i) for i in range(2))
            ok &= good
            n += 1
    return _check(ok, f"live partner: {n} errors injected mid-pass on B's "
                      f"executing cell, first/last op, both walls: repaired by A, "
                      f"B keeps its cycle")


def test_noise(rate=300, passes=60, seed=11):
    """NoisePool over both blocks.  Every single-bit error present at a
    pass start must be gone at the next; multi-bit errors are counted."""
    m, lay = build(fuel=800)
    refs = [block_ref(m, lay, i) for i in range(2)]
    set_noise(m, lay, rate, seed=seed)
    s0 = m.state()
    total = corrected = missed = double = 0
    for _ in range(passes):
        before = [block_ref(m, lay, i) for i in range(2)]
        singles = [(i, k) for i in range(2)
                   for k, (x, y) in enumerate(zip(before[i], refs[i]))
                   if x != y and bin(x ^ y).count('1') == 1]
        double += sum(1 for i in range(2) for x, y in zip(before[i], refs[i])
                      if x != y and bin(x ^ y).count('1') > 1)
        r, states = run_passes(m, lay)
        total += r
        after = [block_ref(m, lay, i) for i in range(2)]
        for i, k in singles:
            if after[i][k] == refs[i][k]:
                corrected += 1
            else:
                missed += 1
    flips = m.noise.total_injected
    good = all(at_rest(states[i][0], lay, i) for i in range(2))
    for _ in range(total):
        m.step_back_all()
    rev = m.state() == s0
    ok = missed == 0 and good and rev and corrected > 0
    return _check(ok, f"noise {rate}/1M over both blocks for {passes} passes "
                      f"({total} rounds, {flips} flips): {corrected} single-bit "
                      f"errors corrected, {missed} missed, {double} multi-bit seen "
                      f"(M5); both stomachs healthy; {total}-round reversal exact")


def run_tests(quick=False):
    ok = True
    m, lay = build()
    print(f"== fb1d immunity M3: 2 x {lay.code_len} code cells, "
          f"{lay.size} cells with {lay.fuel} fuel each ==")
    ok &= test_clean()
    ok &= test_single_flips(quick)
    ok &= test_errors_in_both_blocks()
    ok &= test_live_partner_cell()
    ok &= test_noise()
    print("ALL OK" if ok else "FAILURES")
    return ok


if __name__ == '__main__':
    sys.exit(0 if run_tests(quick='--quick' in sys.argv) else 1)

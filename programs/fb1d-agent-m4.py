#!/usr/bin/env python3
"""
fb1d agent, M4: the M3 pair of immunity gadgets plus metabolism and hunger.
Authored or modified by Claude
Version: 2026-10-13 v0.1

Two gadgets on two IPs correct each other's code as in M3
(programs/fb1d-immunity-m3.py); each now also eats.  The fuel region
after a gadget's stomach holds a block of zeros and then food: runs of
K identical cells (the bite) with payloads cycling 189, 250, 380, 639,
fb2d's A/B/C/D.  Corrections and the per-pass moult consume the zeros;
when fewer than d zeros lie ahead of head b, the pass end eats one run,
turning its K cells into zeros.  Design, op by op, with the reasons for
each choice: docs/fb1d-m4-metabolism-design.md.

Tape layout (two gadgets):

    ~ [A code] ~ [sA gA rA] [LA] [A fuel ...] ~ [B code] ~ [sB gB rB] [LB] [B fuel ...]
    ^ixB         ^aA        ^bA               ^ixA         ^aB        ^bB

Stomach: s and g as in M2, plus r, the reference cell that holds the
run's value during an eat.  b sits on L, the last garbage cell, with
the zeros and then the food ahead of it.

Pass end (replaces M2's moult `}P`; a on s, b on L):

    >  }*d  %+&  {*d         hunger: g = 1 iff the cell d ahead is nonzero
    ? > %}& , %}.& . %{& . }*K %{& < !
                             eat iff g: walk b to the first nonzero cell F,
                             r = v, zero F+1..F+K-1 (stop on the next run's
                             first cell M, restore it), back to F, zero F,
                             K moves to M, walk back to L
    > ?<->!                  g -= 1 iff r != 0 (r is nonzero iff we ate)
    }.,P <<                  moult: b to the fresh cell, dump r into it,
                             r = 0, payload += 1 (new L = v+1 or 1), a to s

Free food (cheat, not reversible, as fb2d's): when fewer than 2K food
cells lie ahead of b, shift tape[b..hi] down so b's cell is the first
fuel cell again, and fill the freed tail with food continuing the
rotation (the last, possibly partial, run is completed first).

Run:  python3 programs/fb1d-agent-m4.py          (tests)
      python3 programs/fb1d-agent-m4.py --quick  (4 bits per cell)
REPL: python3 fb1d.py, then `load agent`.  Browser: fb1d.html, "agent".
MTTF: python3 programs/fb1d-mttf.py --agent m4 --help
"""

import os
import sys
import random
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fb1d import Machine, IP, assemble, BOUNDARY, hamming_encode  # noqa: E402


def _load(filename, modname):
    spec = importlib.util.spec_from_file_location(
        modname, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m2 = _load('fb1d-immunity-m2.py', 'fb1d_immunity_m2')
WORK, sweep = m2.WORK, m2.sweep

BITE = 15            # K: cells per food run
LOOKAHEAD = 15       # d: eat when fewer than d zeros lie ahead of b
FOOD = [189, 250, 380, 639]           # A B C D payloads (fb2d's)
FOOD_CELLS = {hamming_encode(p): p for p in FOOD}
CLEAN_PASS = 7135    # rounds per pass without an eat or a correction (code 185 cells)


# ─── Program ─────────────────────────────────────────────────────────

def pass_end_segments(K=BITE, d=LOOKAHEAD):
    return [
        ('hunger: a to g', '>'),
        (f'hunger: b to L+{d}', '}' * d),
        ('hunger: g = 1 iff nonzero', '%+&'),
        ('hunger: b to L', '{' * d),
        ('eat ?', '?'),
        ('  a to r', '>'),
        ('  walk b to F', '%}&'),
        ('  r = v', ','),
        ('  compress run', '%}.&'),
        ('  restore M', '.'),
        ('  back to F', '%{&'),
        ('  F = 0', '.'),
        (f'  {K} moves to M', '}' * K),
        ('  back to L', '%{&'),
        ('  a to g', '<'),
        ('eat !', '!'),
        ('unflag: a to r', '>'),
        ('unflag: g -= 1 iff r != 0', '?<->!'),
        ('moult: b on, dump r, L += 1', '}.,P'),
        ('a to s', '<<'),
    ]


def gadget_segments(K=BITE, d=LOOKAHEAD):
    return ([('outer [', '['), ('outer -', '-')]
            + [('rest' + l, t) for l, t in WORK]
            + sweep('A', 'scan') + sweep('B', 'rewind')
            + pass_end_segments(K, d)
            + [('outer +', '+'), ('outer ]', ']')])


def gadget_text(K=BITE, d=LOOKAHEAD):
    return ''.join(t for _, t in gadget_segments(K, d))


# ─── Layout ──────────────────────────────────────────────────────────

class Gadget:
    """Addresses of one gadget: ~ code ~ s g r L0 fuel..."""

    def __init__(self, base, code_len, fuel):
        self.lead = base                        # leading `~`
        self.code = base + 1
        self.code_len = code_len
        self.trail = self.code + code_len       # trailing `~`
        self.s = self.trail + 1                 # stomach: probe / mask cell
        self.g = self.s + 1                     # stomach: flag cell
        self.r = self.s + 2                     # stomach: eat reference
        self.L0 = self.r + 1                    # first garbage cell (seeded 1)
        self.fuel_lo = self.L0 + 1
        self.fuel_hi = self.L0 + fuel           # last fuel cell
        self.end = self.fuel_hi + 1
        self.p_start = self.code + 2            # first op of the rest-WORK

    def block(self):
        """Inclusive range the partner's ix scans: ~ code ~."""
        return self.lead, self.trail

    def stomach(self):
        return self.s, self.g, self.r


class Layout:
    def __init__(self, code_len, fuel, K, d, zeros):
        self.code_len, self.fuel, self.K, self.d, self.zeros = code_len, fuel, K, d, zeros
        a = Gadget(0, code_len, fuel)
        b = Gadget(a.end, code_len, fuel)
        self.gadgets = [a, b]
        self.size = b.end

    def partner(self, i):
        return self.gadgets[1 - i]

    def blocks(self):
        return [g.block() for g in self.gadgets]


def place_food(m, lo, hi, K, start_idx=0, start_count=0):
    """Fill tape[lo..hi] with food runs of K cells in rotation, starting
    `start_count` cells into run `start_idx`.  Returns (idx, count) of
    the cell after hi."""
    idx, count = start_idx, start_count
    for addr in range(lo, hi + 1):
        m.tape[addr] = hamming_encode(FOOD[idx])
        count += 1
        if count == K:
            idx, count = (idx + 1) % len(FOOD), 0
    return idx, count


def build(fuel=None, zeros=None, K=BITE, d=LOOKAHEAD, barriers=True, food=True):
    """Return (Machine, Layout): two gadgets, two IPs, heads at home.
    Each fuel region holds `zeros` zeros (default 2K) and then food runs
    up to `fuel` cells (default zeros + 8K); food=False leaves zeros."""
    code = assemble(gadget_text(K, d))
    if zeros is None:
        zeros = 2 * K
    if fuel is None:
        fuel = zeros + 8 * K
    lay = Layout(len(code), fuel, K, d, zeros)
    m = Machine(lay.size, barriers=barriers)
    segs = []
    for name, g in zip('AB', lay.gadgets):
        m.tape[g.lead] = BOUNDARY
        m.tape[g.code:g.code + len(code)] = code
        m.tape[g.trail] = BOUNDARY
        if food:
            place_food(m, g.fuel_lo + zeros, g.fuel_hi, K)
        segs += ([('~', '~')] + [(f'{name} {l}', t) for l, t in gadget_segments(K, d)]
                 + [('~', '~'), (f'{name} stomach s g r', '000'),
                    (f'{name} L0, zeros, food', '0' * (fuel + 1))])
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


# ─── Free food (the conveyor cheat) ──────────────────────────────────

def food_ahead(m, g, b):
    return sum(1 for x in range(b + 1, g.fuel_hi + 1) if m.tape[x] in FOOD_CELLS)


def free_food(m, lay, i, K=None):
    """fb2d's free-food cheat for a line: if fewer than 2K food cells lie
    ahead of gadget i's b, shift tape[b..hi] down to L0 (dropping the
    garbage trail), move b to L0 and fill the freed tail with food that
    continues the rotation.  Not reversible.  Returns the cells added."""
    K = K or lay.K
    g, ip = lay.gadgets[i], m.ips[i]
    b = ip.b
    if food_ahead(m, g, b) >= 2 * K:
        return 0
    n = b - g.L0
    if n <= 0:
        return 0
    hi = g.fuel_hi
    m.tape[g.L0:g.L0 + hi - b + 1] = m.tape[b:hi + 1]
    ip.b = g.L0
    # continue the rotation from the last food cell now in the region
    idx, count = 0, 0
    last = hi - n
    for x in range(last, g.L0, -1):
        if m.tape[x] in FOOD_CELLS:
            p = FOOD_CELLS[m.tape[x]]
            idx = FOOD.index(p)
            while x - count > g.L0 and m.tape[x - count] == m.tape[x]:
                count += 1
            if count >= K:
                idx, count = (idx + 1) % len(FOOD), 0
            break
    place_food(m, last + 1, hi, K, idx, count)
    m.invalidate()
    return n


# ─── Observation ─────────────────────────────────────────────────────

def stomach_state(m, lay, i):
    """(b, payload at b, s, g, ix, r) for gadget i if a is on s, else None."""
    ip, g = m.ips[i], lay.gadgets[i]
    if ip.a != g.s:
        return None
    return ip.b, m.peek(ip.b), m.peek(g.s), m.peek(g.g), ip.ix, m.peek(g.r)


REST_L = {1} | {(p + 1) % 2048 for p in FOOD}      # L after a moult: 1 or v+1


def at_rest(fs, lay, i):
    """Healthy state at a pass start: L is 1 or food+1 under b, stomach
    zero, ix on the partner's leading boundary."""
    return (fs is not None and fs[1] in REST_L and fs[2] == 0 and fs[3] == 0
            and fs[5] == 0 and fs[4] == lay.partner(i).lead)


def zeros_ahead(m, lay, i):
    g, b = lay.gadgets[i], m.ips[i].b
    n = 0
    while b + n + 1 <= g.fuel_hi and m.tape[b + n + 1] == 0:
        n += 1
    return n


def block_ref(m, lay, i):
    lo, hi = lay.gadgets[i].block()
    return m.tape[lo:hi + 1]


def blocks_ok(m, lay, refs):
    return all(block_ref(m, lay, i) == refs[i] for i in range(2))


def run_passes(m, lay, n=1, limit=10**7, food=False):
    """Run until each IP has started a new pass n times.  Returns
    (rounds, states) with states[i] the stomach state of gadget i at each
    of its pass starts.  food=True applies the conveyor at each pass
    start (before recording the state)."""
    starts = [g.p_start for g in lay.gadgets]
    counts, states, r = [0, 0], [[], []], 0
    while r < limit and min(counts) < n:
        m.step_all()
        r += 1
        for i, ip in enumerate(m.ips):
            if ip.p == starts[i] and counts[i] < n:
                counts[i] += 1
                if food:
                    free_food(m, lay, i)
                states[i].append(stomach_state(m, lay, i))
    assert min(counts) >= n, "a pass did not complete"
    return r, states


# ─── Tests ───────────────────────────────────────────────────────────

def _check(ok, msg):
    print(f"  [{'ok' if ok else 'FAIL'}] {msg}")
    return ok


def test_clean(passes=None):
    m, lay = build()
    K, d = lay.K, lay.d
    passes = passes or 2 * K + 3
    refs = [block_ref(m, lay, i) for i in range(2)]
    s0 = m.state()
    ok, rounds, eats, min_z = True, [], 0, 10**9
    for k in range(passes):
        r, states = run_passes(m, lay)
        rounds.append(r)
        ok &= blocks_ok(m, lay, refs)
        for i in range(2):
            fs = states[i][0]
            ok &= at_rest(fs, lay, i) and fs[0] == lay.gadgets[i].L0 + k + 1
            z = zeros_ahead(m, lay, i)
            min_z = min(min_z, z)
            ok &= z >= min(d, K) - 1       # the moult follows the hunger test
            eats += fs[1] != 1
    total = sum(rounds)
    ok &= eats == 2 * (passes // K) and len(set(rounds)) == 2 and min(rounds) == CLEAN_PASS
    for _ in range(total):
        m.step_back_all()
    ok &= m.state() == s0
    return _check(ok, f"clean: {passes} passes, {min(rounds)} rounds without an eat, "
                      f"{max(rounds)} with; {eats // 2} eats per gadget (every {K} "
                      f"passes), never fewer than {min_z} zeros ahead, b +1 per pass, "
                      f"stomachs zero, blocks untouched, {total}-round reversal exact")


def test_eat():
    """Start with no zeros: the first pass end eats the first run."""
    m, lay = build(zeros=0)
    K = lay.K
    ga = lay.gadgets[0]
    run0 = [m.tape[ga.fuel_lo + i] for i in range(2 * K + 1)]
    s0 = m.state()
    r, states = run_passes(m, lay)
    fs = states[0][0]
    after = [m.tape[ga.fuel_lo + i] for i in range(2 * K + 1)]
    v = FOOD[0]
    ok = (fs is not None and at_rest(fs, lay, 0) and fs[0] == ga.L0 + 1
          and fs[1] == v + 1
          and after[0] == hamming_encode(v + 1)
          and all(x == 0 for x in after[1:K])
          and after[K:] == run0[K:])
    ok &= zeros_ahead(m, lay, 0) == K - 1
    for _ in range(r):
        m.step_back_all()
    ok &= m.state() == s0
    return _check(ok, f"eat: with 0 zeros ahead the first pass eats run A: the moult "
                      f"cell holds {v}+1 = L, the other {K - 1} cells are zero, run B "
                      f"intact, b +1, {r} rounds, reversed exactly")


def test_single_flips(quick=False):
    """Every cell of both blocks x every bit, one error at a time at a
    pass start; the partner repairs it within one pass at 2 fuel cells
    (no eat in that pass: the initial zeros exceed d)."""
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
                if good and rev_checked < 30 and (addr * 16 + bit) % 97 == 0:
                    for _ in range(r):
                        m.step_back_all()
                    good &= m.state() == s0
                    rev_checked += 1
                n += 1
                bad += not good
    ok = bad == 0 and cost == {(3, 1)}
    return _check(ok, f"single-bit errors: {n} (block, cell, bit) cases, {bad} "
                      f"failures; fuel per pass (corrector, victim): {sorted(cost)}; "
                      f"{rev_checked} reversed exactly")


def test_many_corrections():
    """c errors in B's block at once with exactly d zeros ahead of A's b:
    up to (d-1)/2 corrections fit in the pass; one more starves it."""
    d = LOOKAHEAD
    m0, lay = build(zeros=d)
    ref = block_ref(m0, lay, 1)
    cmax = (d - 1) // 2
    rng = random.Random(3)
    lo, hi = lay.gadgets[1].block()
    results = {}
    for c in (cmax, cmax + 1):
        m, _ = build(zeros=d)
        for addr in rng.sample(range(lo + 1, hi), c):
            m.flip(addr, rng.randrange(16))
        r, states = run_passes(m, lay, n=2)
        fs = states[0][-1]
        ate = states[0][0] is not None and states[0][0][1] != 1
        results[c] = (at_rest(fs, lay, 0) and block_ref(m, lay, 1) == ref and ate, fs)
    ok = results[cmax][0] and not results[cmax + 1][0]
    bad = results[cmax + 1][1]
    return _check(ok, f"{cmax} corrections in one pass with {d} zeros ahead: survived "
                      f"and ate; {cmax + 1}: starved mid-pass as expected "
                      f"({f'stomach s,g = {bad[2:4]}' if bad else 'head a left the stomach'})")


def test_live_partner_cell():
    """An error on the cell the partner IP is executing, and on each wall,
    is repaired while it keeps running (as M3)."""
    m0, lay = build()
    refs = [block_ref(m0, lay, i) for i in range(2)]
    ok, n = True, 0
    gb = lay.gadgets[1]
    targets = [gb.p_start, gb.code, gb.code + gb.code_len - 1, gb.lead, gb.trail]
    for addr in targets:
        for bit in (0, 5, 15):
            m, _ = build()
            m.run(1500)
            m.flip(addr, bit)
            r, states = run_passes(m, lay, n=2)
            good = blocks_ok(m, lay, refs)
            good &= all(at_rest(states[i][-1], lay, i) for i in range(2))
            ok &= good
            n += 1
    return _check(ok, f"live partner: {n} errors injected mid-pass on B's executing "
                      f"cell, first/last op, both walls: repaired by A, B keeps its cycle")


def test_noise(rate=300, passes=200, seed=11, food=True):
    """NoisePool over both blocks, free food on: every single-bit error
    present at a pass start is repaired by the next (its bit is back,
    whatever new flips landed meanwhile), the stomachs stay clean, and b
    never nears the end of the region."""
    m, lay = build()
    refs = [block_ref(m, lay, i) for i in range(2)]
    set_noise(m, lay, rate, seed=seed)
    total = corrected = missed = double = eats = refills = 0
    bmax = 0
    for _ in range(passes):
        before = [block_ref(m, lay, i) for i in range(2)]
        singles = [(i, k, x ^ y) for i in range(2)
                   for k, (x, y) in enumerate(zip(before[i], refs[i]))
                   if x != y and bin(x ^ y).count('1') == 1]
        double += sum(1 for i in range(2) for x, y in zip(before[i], refs[i])
                      if x != y and bin(x ^ y).count('1') > 1)
        b_before = [m.ips[i].b for i in range(2)]
        r, states = run_passes(m, lay, food=food)
        total += r
        for i in range(2):
            fs = states[i][0]
            if fs and fs[1] != 1:
                eats += 1
            bmax = max(bmax, m.ips[i].b - lay.gadgets[i].L0)
            refills += m.ips[i].b < b_before[i]
        after = [block_ref(m, lay, i) for i in range(2)]
        for i, k, bit in singles:
            if (after[i][k] ^ refs[i][k]) & bit:
                missed += 1
            else:
                corrected += 1
    flips = m.noise.total_injected
    good = all(at_rest(states[i][0], lay, i) for i in range(2))
    ok = missed == 0 and good and corrected > 0 and eats > 0
    return _check(ok, f"noise {rate}/1M, free food, {passes} passes ({total} rounds, "
                      f"{flips} flips): {corrected} single-bit errors corrected, "
                      f"{missed} missed, {double} multi-bit seen (M5); {eats} eats, "
                      f"{refills} refills, b at most {bmax} cells into a {lay.fuel}-cell "
                      f"region; both stomachs healthy")


def test_noise_reversal(rate=300, passes=40, seed=5):
    """Without the cheat the run is exactly reversible, eats included."""
    m, lay = build(fuel=400)
    set_noise(m, lay, rate, seed=seed)
    s0 = m.state()
    total = eats = 0
    for _ in range(passes):
        r, states = run_passes(m, lay)
        total += r
        eats += sum(1 for i in range(2) if states[i][0] and states[i][0][1] != 1)
    good = all(at_rest(states[i][0], lay, i) for i in range(2))
    for _ in range(total):
        m.step_back_all()
    ok = good and m.state() == s0 and eats > 0
    return _check(ok, f"noise {rate}/1M without the cheat, {passes} passes, {eats} eats: "
                      f"{total}-round reversal exact")


def test_starve():
    """Without free food the agent eats every run and then dies."""
    K = BITE
    m, lay = build(zeros=2 * K, fuel=6 * K)
    passes, healthy_until = 0, None
    for k in range(200):
        r, states = run_passes(m, lay)
        passes += 1
        if not all(at_rest(states[i][0], lay, i) for i in range(2)):
            healthy_until = k
            break
    expected = lay.fuel - K + 1     # every cell of the region but the last run's
    ok = healthy_until is not None and abs(healthy_until - expected) <= 2
    return _check(ok, f"starvation without the cheat: healthy for {healthy_until} passes "
                      f"({lay.zeros} zeros + 4 runs of {K}, {expected} expected), then "
                      f"the stomach breaks")


def run_tests(quick=False):
    ok = True
    m, lay = build()
    print(f"== fb1d agent M4: 2 x {lay.code_len} code cells (M2's 106 + {lay.code_len - 106} "
          f"for the pass end), K={lay.K}, d={lay.d}, {lay.size} cells ==")
    ok &= test_clean()
    ok &= test_eat()
    ok &= test_single_flips(quick)
    ok &= test_many_corrections()
    ok &= test_live_partner_cell()
    ok &= test_noise(300)
    ok &= test_noise(200, seed=12)
    ok &= test_noise_reversal()
    ok &= test_starve()
    print("ALL OK" if ok else "FAILURES")
    return ok


if __name__ == '__main__':
    sys.exit(0 if run_tests(quick='--quick' in sys.argv) else 1)

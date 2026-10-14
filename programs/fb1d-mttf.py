#!/usr/bin/env python3
"""
fb1d-mttf.py -- Mean time to failure of the fb1d agent under noise,
optionally side by side with the fb2d narrow agent.
Authored or modified by Claude
Version: 2026-10-12 v0.1; 2026-10-13 v0.2: --agent m4 (metabolism, free food)

A trial runs the M4 agent (programs/fb1d-agent-m4.py: two gadgets that
correct each other and eat) or the M3 pair (programs/fb1d-immunity-m3.py,
--agent m3) with bit-flip noise over both code blocks until an
invariant breaks or the round budget runs out.  For m4 the free-food
conveyor is applied at every pass start unless --no-free-food is given,
in which case the agent starves once the food in its region is eaten
(as fb2d's agent does without the cheat).  Failure causes (first one
seen wins), ported from programs/compare-agents-mttf.py:

  ip_escaped      an IP left its own code block
  a_escaped       head a left the stomach (s, g; m4: s, g, r)
  b_escaped       head b left the garbage trail / fuel
  ix_escaped      ix left the partner's block
  cycle_timeout   no pass start for 4 clean-pass lengths
  stomach_dirty   s or g (m4: or r) nonzero at a pass start
  L_wrong         the last garbage cell is not 1 (m4: nor food+1) at a pass start
  ix_not_home     ix not on the partner's leading `~` at a pass start
  zero_starved    b reached the end of the fuel region

Each trial also records the first *persistent code damage*: a cell of
either block whose fetched op (or wall-ness) differs from the original
at two consecutive checks two passes apart.  Single-bit errors never
change the fetched op, so this is the trace of a 2-bit error that the
M3 gadget miscorrects (M5 adds copy-over).  Damage is reported
separately from failure, so that the numbers compare with fb2d's
harness, which measures structural failure only.

Noise.  The rate is flips per 1M rounds over the whole noise region, as
in fb2d.  Flips are drawn with geometric gaps (not fb2d's per-round
coin), so seeds here do not reproduce the REPL's NoisePool seeds; the
statistics are the same.  Region sizes differ between the two agents
(2 x 108 cells here vs code+handler rows there), so the per-cell rate
is printed too.

Results go to programs/fb1d-mttf-last.csv (untracked) unless --csv says
otherwise; programs/fb1d-mttf-results.csv is the recorded 2026-10-12
comparison run (10 seeds, 10M rounds, fb1d and fb2d narrow).

Usage:
  python3 programs/fb1d-mttf.py --rates 300 --seeds 3 --max-rounds 300000  # smoke
  python3 programs/fb1d-mttf.py --rates 100,200,300 --seeds 10              # M4 curve, free food
  python3 programs/fb1d-mttf.py --rates 200 --seeds 10 --no-free-food       # M4 starves
  python3 programs/fb1d-mttf.py --agent m3 --rates 100,200,300 --seeds 10   # M3 curve
  python3 programs/fb1d-mttf.py --rates 100,200,300 --seeds 10 --fb2d-narrow  # + fb2d
  python3 programs/fb1d-mttf.py --rates 300 --seeds 20 --no-barriers         # wall rule off
"""

import os
import sys
import csv
import math
import time
import random
import argparse
import importlib.util
import multiprocessing
from collections import Counter
from statistics import mean, median, stdev

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fb1d import CELL_OP, payload, DATA_POS  # noqa: E402

DATA_BITS = set(DATA_POS)


def _load(filename, modname):
    spec = importlib.util.spec_from_file_location(
        modname, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AGENTS = {'m3': 'fb1d-immunity-m3.py', 'm4': 'fb1d-agent-m4.py'}
_mods = {}


def agent_module(name):
    if name not in _mods:
        _mods[name] = _load(AGENTS[name], 'fb1d_agent_' + name)
    return _mods[name]


def fetch_class(v):
    """What the physics sees in a cell: its op, or wall-ness."""
    return CELL_OP[v] or (-1 if payload(v) == 2047 else 0)


def run_fb1d_trial(args):
    seed, rate, max_rounds, fuel, barriers, agent, free_food = args
    mod = agent_module(agent)
    CLEAN_PASS = mod.CLEAN_PASS
    CHECK_EVERY = 2 * CLEAN_PASS          # periodic checks (and damage persistence)
    TIMEOUT = 4 * CLEAN_PASS              # cycle timeout
    rest_L = getattr(mod, 'REST_L', {1})
    m, lay = mod.build(fuel=fuel, barriers=barriers)
    gad = lay.gadgets
    ips = m.ips
    starts = [g.p_start for g in gad]
    cells = [addr for lo, hi in lay.blocks() for addr in range(lo, hi + 1)]
    ref = {addr: fetch_class(m.tape[addr]) for addr in cells}
    rng = random.Random(seed)
    p = rate / 1e6
    log1p = math.log1p(-p) if p > 0 else None

    def gap():
        return 1 + int(math.log(1.0 - rng.random()) / log1p)

    next_flip = gap() if p > 0 else max_rounds + 1
    next_check = CHECK_EVERY
    last_re = [0, 0]
    passes = [0, 0]
    corrections = [0, 0]
    last_b = [ip.b for ip in ips]
    refills = eats = 0
    flips = 0
    damaged_prev = set()
    damage_round, damage_detail = None, ''
    r = 0

    recent = []                       # (round, addr, bit) of the last flips

    def cell_name(addr):
        blk = 0 if addr <= gad[0].trail else 1
        return f"{'AB'[blk]}+{addr - gad[blk].lead}"

    def coincidence():
        """The most recent cell hit twice within two passes before the
        failure, with the bit kinds (d = data, p = parity)."""
        for k in range(len(recent) - 1, 0, -1):
            rk, ak, bk = recent[k]
            for j in range(k - 1, -1, -1):
                rj, aj, bj = recent[j]
                if rk - rj > 2 * CLEAN_PASS:
                    break
                if aj == ak:
                    kind = ''.join('d' if b in DATA_BITS else 'p' for b in (bj, bk))
                    return f"{cell_name(ak)} bits {bj},{bk} ({kind}) {rk - rj} apart, {r - rk} before"
        return ''

    def result(cause, detail):
        return dict(agent='fb1d-' + agent, rate=rate, seed=seed, rounds=r,
                    cause=cause, detail=detail, flips=flips,
                    passes=sum(passes), corrections=sum(corrections),
                    eats=eats, refills=refills,
                    damage_round=damage_round, damage_detail=damage_detail,
                    coincidence=coincidence() if cause else '')

    while r < max_rounds:
        stop = min(next_flip, next_check, max_rounds)
        ip0, ip1 = ips
        p0, p1 = starts
        while r < stop:
            m.step_all()
            r += 1
            if ip0.p == p0 or ip1.p == p1:
                for i in (0, 1):
                    if ips[i].p == starts[i]:
                        passes[i] += 1
                        last_re[i] = r
                        corrections[i] += max(0, (ips[i].b - last_b[i] - 1) // 2)
                        fs = mod.stomach_state(m, lay, i)
                        if fs is None:
                            return result('a_escaped', f'IP{i}')
                        if fs[2] != 0 or fs[3] != 0 or (len(fs) > 5 and fs[5] != 0):
                            return result('stomach_dirty', f'IP{i} s={fs[2]} g={fs[3]}'
                                          + (f' r={fs[5]}' if len(fs) > 5 else ''))
                        if fs[1] not in rest_L:
                            return result('L_wrong', f'IP{i} L={fs[1]}')
                        if fs[4] != lay.partner(i).lead:
                            return result('ix_not_home', f'IP{i} ix={fs[4]}')
                        eats += fs[1] != 1
                        if free_food and mod.free_food(m, lay, i):
                            refills += 1
                        last_b[i] = ips[i].b
        if r == next_flip:
            addr, bit = cells[rng.randrange(len(cells))], rng.randrange(16)
            m.flip(addr, bit)
            flips += 1
            recent.append((r, addr, bit))
            if len(recent) > 64:
                del recent[:32]
            next_flip = r + gap()
        if r >= next_check:
            next_check = r + CHECK_EVERY
            for i, g in enumerate(gad):
                ip = ips[i]
                if not g.code <= ip.p < g.trail:
                    return result('ip_escaped', f'IP{i} p={ip.p}')
                if ip.a not in (g.stomach() if hasattr(g, 'stomach') else (g.s, g.g)):
                    return result('a_escaped', f'IP{i} a={ip.a}')
                if not g.L0 <= ip.b <= g.fuel_hi:
                    return result('b_escaped', f'IP{i} b={ip.b}')
                if ip.b >= g.fuel_hi - 2:
                    return result('zero_starved', f'IP{i}')
                plo, phi = lay.partner(i).block()
                if not plo <= ip.ix <= phi:
                    return result('ix_escaped', f'IP{i} ix={ip.ix}')
                if r - last_re[i] > TIMEOUT:
                    return result('cycle_timeout', f'IP{i}')
            if damage_round is None:
                damaged = {addr for addr in cells
                           if fetch_class(m.tape[addr]) != ref[addr]}
                persistent = damaged & damaged_prev
                if persistent:
                    damage_round = r - CHECK_EVERY
                    addr = min(persistent)
                    blk = 'A' if addr <= gad[0].trail else 'B'
                    damage_detail = (f'{blk}+{addr - (gad[0].lead if blk == "A" else gad[1].lead)}'
                                     f' {ref[addr]}->{fetch_class(m.tape[addr])}')
                damaged_prev = damaged
    return result(None, '')


# ─── fb2d narrow agent (optional, via compare-agents-mttf.py) ────────

_cam = None


def _fb2d():
    global _cam
    if _cam is None:
        _cam = _load('compare-agents-mttf.py', 'compare_agents_mttf')
    return _cam


def fb2d_setup(bite):
    cam = _fb2d()
    sim, lay = cam.narrow.make_narrow_agent(bite_size=bite)
    clean, dirty = cam.measure_cycle_lengths(sim, lay)
    rows = cam.get_noise_rows(lay, 'ga') + cam.get_noise_rows(lay, 'gb')
    region = len(rows) * (lay['width'] - 2)
    return dict(clean=clean, dirty=dirty, region=region, bite=bite)


def run_fb2d_trial(args):
    seed, rate, max_rounds, dirty, bite = args
    cam = _fb2d()
    agent, rate, seed, steps, detail, cause = cam.run_trial(
        ('narrow', seed, rate, max_rounds, dirty, bite))
    return dict(agent='fb2d-narrow', rate=rate, seed=seed, rounds=steps,
                cause=cause, detail=detail, flips=None, passes=None,
                corrections=None, eats=None, refills=None, damage_round=None,
                damage_detail='', coincidence='')


def run_any(task):
    kind, args = task
    return run_fb1d_trial(args) if kind == 'fb1d' else run_fb2d_trial(args)


# ─── Reporting ───────────────────────────────────────────────────────

def summarize(results, rates, agents, fb1d_name='fb1d'):
    print(f"\n{'=' * 78}\nMTTF (rounds to structural failure; survivors count "
          f"at the round budget)\n{'=' * 78}")
    print(f"{'rate':>6} | {'agent':>12} | {'mean':>12} {'median':>12} "
          f"{'std':>11} | {'n':>3} | causes")
    for rate in rates:
        for agent in agents:
            rows = [x for x in results if x['rate'] == rate and x['agent'] == agent]
            if not rows:
                continue
            t = [x['rounds'] for x in rows]
            cs = Counter(x['cause'] or 'survived' for x in rows)
            causes = ', '.join(f'{n} {c}' for c, n in cs.most_common())
            print(f"{rate:>6.0f} | {agent:>12} | {mean(t):>12,.0f} {median(t):>12,.0f} "
                  f"{(stdev(t) if len(t) > 1 else 0):>11,.0f} | {len(t):>3} | {causes}")
        if len(agents) == 2:
            t0 = [x['rounds'] for x in results if x['rate'] == rate and x['agent'] == agents[0]]
            t1 = [x['rounds'] for x in results if x['rate'] == rate and x['agent'] == agents[1]]
            if t0 and t1 and mean(t1):
                print(f"{'':>6} | {'ratio':>12} | {mean(t0) / mean(t1):>12.2f}x")
    fb = [x for x in results if x['agent'] == fb1d_name]
    if fb:
        print(f"\n{'=' * 78}\nfb1d: first persistent code damage (miscorrected "
              f"2-bit error), per rate\n{'=' * 78}")
        print(f"{'rate':>6} | {'damaged':>7} | {'mean round':>12} {'median':>12} | "
              f"{'flips':>7} {'corrections':>12} {'passes':>8} {'eats':>6} {'refills':>8}  (means per trial)")
        for rate in rates:
            rows = [x for x in fb if x['rate'] == rate]
            if not rows:
                continue
            d = [x['damage_round'] for x in rows if x['damage_round'] is not None]
            print(f"{rate:>6.0f} | {len(d):>3}/{len(rows):<3} | "
                  f"{(mean(d) if d else 0):>12,.0f} {(median(d) if d else 0):>12,.0f} | "
                  f"{mean(x['flips'] for x in rows):>7,.0f} "
                  f"{mean(x['corrections'] for x in rows):>12,.0f} "
                  f"{mean(x['passes'] for x in rows):>8,.0f} "
                  f"{mean(x['eats'] for x in rows):>6,.0f} {mean(x['refills'] for x in rows):>8,.0f}")
        fails = [x for x in fb if x['cause']]
        with_c = [x for x in fails if x['coincidence']]
        dd = [x for x in with_c if '(dd)' in x['coincidence']]
        print(f"\nfb1d failures: {len(fails)}; preceded within two passes by a "
              f"second flip on an already-flipped cell: {len(with_c)} "
              f"(both data bits: {len(dd)}); preceded by persistent damage: "
              f"{sum(1 for x in fails if x['damage_round'])}")
        for x in fails:
            print(f"  rate {x['rate']:.0f} seed {x['seed']:>2}: {x['cause']:>14} "
                  f"{x['detail']:<18} at {x['rounds']:>10,}  "
                  f"{x['coincidence'] or 'no coincidence'}"
                  + (f"; damage {x['damage_detail']} at {x['damage_round']:,}"
                     if x['damage_round'] else ''))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rates', default='100,200,300', help='flips per 1M rounds')
    ap.add_argument('--seeds', type=int, default=10, help='seeds 0..N-1')
    ap.add_argument('--max-rounds', type=int, default=10_000_000)
    ap.add_argument('--agent', choices=sorted(AGENTS), default='m4',
                    help='m4: metabolism + free food (default); m3: immunity only')
    ap.add_argument('--no-free-food', action='store_true',
                    help='m4 without the conveyor cheat: a finite fuel region, then starvation')
    ap.add_argument('--fuel', type=int, default=0,
                    help='fuel cells per gadget (default: m4 150 with free food, otherwise '
                         'enough for the budget)')
    ap.add_argument('--no-barriers', action='store_true',
                    help='Machine(barriers=False): walls do not stop bracket matching')
    ap.add_argument('--fb2d-narrow', action='store_true',
                    help='also run the fb2d narrow agent (compare-agents-mttf.py)')
    ap.add_argument('--bite', type=int, default=15, help='fb2d free-food bite size')
    ap.add_argument('--workers', type=int, default=0, help='0 = cpu_count - 1')
    ap.add_argument('--csv', default=os.path.join(HERE, 'fb1d-mttf-last.csv'),
                    help='output (default programs/fb1d-mttf-last.csv, untracked; '
                         'the recorded comparison is fb1d-mttf-results.csv)')
    args = ap.parse_args()

    rates = [float(x) for x in args.rates.split(',')]
    workers = args.workers or max(1, (os.cpu_count() or 4) - 1)
    mod = agent_module(args.agent)
    CLEAN_PASS = mod.CLEAN_PASS
    free_food = args.agent == 'm4' and not args.no_free_food
    # fuel: 1 cell per pass + 2 per correction; corrections <= flips
    if args.fuel:
        fuel = args.fuel
    elif free_food:
        fuel = None                                  # the module's default region
    else:
        fuel = (args.max_rounds // CLEAN_PASS
                + 2 * int(args.max_rounds * max(rates) / 1e6) + 50)
    m, lay = mod.build(fuel=fuel)
    fuel = lay.fuel
    region = sum(hi - lo + 1 for lo, hi in lay.blocks())
    name = 'fb1d-' + args.agent
    print(f"{name} ({AGENTS[args.agent]}): {lay.code_len} ops per gadget, noise region "
          f"{region} cells, {fuel} fuel cells per gadget, clean pass {CLEAN_PASS} rounds, "
          f"barriers {'off' if args.no_barriers else 'on'}"
          + (f", free food {'on' if free_food else 'off'} (bite {lay.K}, lookahead {lay.d})"
             if args.agent == 'm4' else ''))
    tasks = [('fb1d', (seed, rate, args.max_rounds, fuel, not args.no_barriers,
                       args.agent, free_food))
             for rate in rates for seed in range(args.seeds)]
    agents = [name]
    if args.fb2d_narrow:
        info = fb2d_setup(args.bite)
        print(f"fb2d narrow agent: noise region {info['region']} cells, clean cycle "
              f"{info['clean']} steps, dirty ~{info['dirty']}, timeout "
              f"{info['dirty'] * 20:,}")
        tasks += [('fb2d', (seed, rate, args.max_rounds, info['dirty'], args.bite))
                  for rate in rates for seed in range(args.seeds)]
        agents.append('fb2d-narrow')
        print("per-cell rates (flips per 1M rounds per cell): "
              + ', '.join(f"{r:.0f} -> fb1d {r / region:.2f}, fb2d {r / info['region']:.3f}"
                          for r in rates))
    print(f"{len(tasks)} trials, {args.max_rounds:,} rounds each, {workers} workers")

    t0 = time.time()
    results = []
    with multiprocessing.Pool(workers) as pool:
        for i, res in enumerate(pool.imap_unordered(run_any, tasks), 1):
            results.append(res)
            el = time.time() - t0
            print(f"\r  {i}/{len(tasks)} done, {el:.0f}s elapsed, "
                  f"~{el / i * (len(tasks) - i):.0f}s left   ", end='', flush=True)
    print(f"\n{len(tasks)} trials in {time.time() - t0:.0f}s")

    fields = ['agent', 'rate', 'seed', 'rounds', 'cause', 'detail', 'flips',
              'passes', 'corrections', 'eats', 'refills', 'damage_round',
              'damage_detail', 'coincidence']
    results.sort(key=lambda x: (x['agent'], x['rate'], x['seed']))
    with open(args.csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for x in results:
            w.writerow({k: ('' if x.get(k) is None else x[k]) for k in fields})
    print(f"CSV: {args.csv}")
    summarize(results, rates, agents, name)


if __name__ == '__main__':
    main()

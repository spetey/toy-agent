#!/usr/bin/env python3
"""Tests for reversible WastePool and NoisePool."""

from pools import WastePool, NoisePool


def test_waste_pool_basic():
    """Consume and unconsume returns original values."""
    wp = WastePool()
    assert wp.consume(42) == 0
    assert wp.consume(99) == 0
    assert wp.consume(255) == 0
    assert wp.pointer == 3

    # Unconsume in reverse order
    assert wp.unconsume() == 255
    assert wp.unconsume() == 99
    assert wp.unconsume() == 42
    assert wp.pointer == 0
    print('  PASS: waste_pool_basic')


def test_waste_pool_reset():
    wp = WastePool()
    wp.consume(1)
    wp.consume(2)
    wp.reset()
    assert wp.pointer == 0
    assert wp.total_swaps == 0
    print('  PASS: waste_pool_reset')


def test_noise_pool_deterministic():
    """Same seed + step always returns the same action."""
    # 1M flips per 1M steps = 100% chance per step
    np = NoisePool(seed=123, n_code_rows=2, grid_cols=50, flips_per_1M=1_000_000)
    code_rows = [0, 3]

    a1 = np.flip_at(0, code_rows)
    a2 = np.flip_at(0, code_rows)
    assert a1 == a2, f'{a1} != {a2}'

    a3 = np.flip_at(100, code_rows)
    a4 = np.flip_at(100, code_rows)
    assert a3 == a4, f'{a3} != {a4}'
    print('  PASS: noise_pool_deterministic')


def test_noise_pool_rate_zero():
    """Rate 0 never flips."""
    np = NoisePool(seed=42, n_code_rows=2, grid_cols=50, flips_per_1M=0.0)
    code_rows = [0, 3]
    for step in range(100):
        assert np.flip_at(step, code_rows) is None
    print('  PASS: noise_pool_rate_zero')


def test_noise_pool_rate_every_step():
    """1M flips/1M → every step flips."""
    np = NoisePool(seed=42, n_code_rows=2, grid_cols=50, flips_per_1M=1_000_000)
    code_rows = [0, 3]
    flips = 0
    for step in range(100):
        if np.flip_at(step, code_rows) is not None:
            flips += 1
    assert flips == 100, f'Expected 100 flips, got {flips}'
    print('  PASS: noise_pool_rate_every_step')


def test_noise_pool_1M_rate():
    """10000 flips per 1M steps → ~1% chance per step."""
    np = NoisePool(seed=42, n_code_rows=1, grid_cols=100, flips_per_1M=10_000)
    assert abs(np.rate - 0.01) < 1e-9, f'rate={np.rate}, expected 0.01'
    code_rows = [0]
    flips = 0
    n_steps = 10000
    for step in range(n_steps):
        if np.flip_at(step, code_rows) is not None:
            flips += 1
    # Expect ~100 flips (1% of 10000)
    assert 50 < flips < 200, f'Expected ~100 flips, got {flips}'
    print(f'  PASS: noise_pool_1M_rate ({flips} flips in {n_steps} steps, '
          f'flips_per_1M=10000)')


def test_noise_pool_xor_reversible():
    """Apply forward then undo → grid unchanged."""
    np = NoisePool(seed=42, n_code_rows=1, grid_cols=10, flips_per_1M=1_000_000)
    code_rows = [0]
    grid = [0] * 10

    def flat_fn(r, c):
        return r * 10 + c

    original = list(grid)

    for step in range(20):
        np.apply_forward(step, grid, flat_fn, code_rows)

    assert grid != original, 'Grid unchanged after noise?'

    for step in range(19, -1, -1):
        np.undo_at(step, grid, flat_fn, code_rows)

    assert grid == original, f'Grid not restored!\n  got:  {grid}\n  want: {original}'
    print('  PASS: noise_pool_xor_reversible')


def test_noise_pool_low_rate():
    """Low rate produces some flips, all reversible."""
    np = NoisePool(seed=42, n_code_rows=1, grid_cols=10, flips_per_1M=10_000)
    code_rows = [0]
    grid = [0] * 10
    original = list(grid)

    def flat_fn(r, c):
        return r * 10 + c

    for step in range(1000):
        np.apply_forward(step, grid, flat_fn, code_rows)

    injected = np.total_injected
    assert 0 < injected < 50, f'Expected ~10 flips, got {injected}'

    for step in range(999, -1, -1):
        np.undo_at(step, grid, flat_fn, code_rows)

    assert grid == original, 'Grid not restored after low-rate noise!'
    assert np.total_injected == 0
    print(f'  PASS: noise_pool_low_rate ({injected} flips in 1000 steps)')


def test_noise_pool_configure():
    """Configure flips_per_1M after creation."""
    np = NoisePool(seed=42, n_code_rows=1, grid_cols=99, flips_per_1M=0.0)
    assert np.rate == 0.0
    np.configure(flips_per_1M=20_000.0)
    assert abs(np.rate - 0.02) < 1e-9
    assert np.flips_per_1M == 20_000.0
    print('  PASS: noise_pool_configure')


def test_full_forward_backward():
    """Simulate the server's forward/backward pattern with both pools."""

    rows, cols = 4, 10
    grid = [0] * (rows * cols)
    code_rows = [0, 1]
    waste_row = 3

    def flat_fn(r, c):
        return r * cols + c

    # 500k flips per 1M steps = 50% chance per step
    np = NoisePool(seed=7, n_code_rows=2, grid_cols=cols, flips_per_1M=500_000)
    wp = WastePool()

    import random
    rng = random.Random(99)

    step_all_count = 0
    cleanup_addrs = {}

    def step_forward():
        nonlocal step_all_count
        # Simulate step_all dirtying the waste row
        for c in range(cols):
            grid[flat_fn(waste_row, c)] = rng.randint(0, 255)

        # Noise
        np.apply_forward(step_all_count, grid, flat_fn, code_rows)

        # Waste cleanup (every step)
        addrs = []
        for c in range(cols):
            flat = flat_fn(waste_row, c)
            val = grid[flat]
            if val != 0:
                grid[flat] = wp.consume(val)
                addrs.append(flat)
        if addrs:
            cleanup_addrs[step_all_count] = addrs

        step_all_count += 1

    def step_backward():
        nonlocal step_all_count
        step_all_count -= 1

        # Undo waste cleanup
        addrs = cleanup_addrs.get(step_all_count, [])
        for flat in reversed(addrs):
            grid[flat] = wp.unconsume()
        cleanup_addrs.pop(step_all_count, None)

        # Undo noise
        np.undo_at(step_all_count, grid, flat_fn, code_rows)

    code_grid_before = [grid[flat_fn(r, c)] for r in code_rows for c in range(cols)]

    for _ in range(20):
        step_forward()

    for _ in range(20):
        step_backward()

    code_grid_restored = [grid[flat_fn(r, c)] for r in code_rows for c in range(cols)]
    assert code_grid_restored == code_grid_before, (
        f'Code rows not restored!\n  before: {code_grid_before}\n'
        f'  after:  {code_grid_restored}'
    )
    assert np.total_injected == 0
    assert wp.pointer == 0
    print('  PASS: full_forward_backward')


def test_callback_hook_reversibility():
    """Integration test: use the real simulator with full-row waste cleanup
    + noise. Forward N steps, backward N steps, grid and all state should
    be identical to the start."""
    import os
    from fb2d import FB2DSimulator

    sim = FB2DSimulator()
    prog = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'programs', 'boustrophedon-ouroboros-w99.fb2d')
    sim.load_state(prog)

    wp = WastePool()
    np = NoisePool(seed=77, flips_per_1M=20_000.0)
    np.configure(n_code_rows=len(set(ip['ip_row'] for ip in sim.ips)),
                 grid_cols=sim.cols)

    # Snapshot initial state
    grid_start = list(sim.grid)
    ips_start = [dict(ip) for ip in sim.ips]

    step_all_count = 0
    cleanup_log = {}
    W = sim.cols

    def code_rows():
        sim._save_active()
        return list(set(ip['ip_row'] for ip in sim.ips))

    def working_rows():
        sim._save_active()
        return sorted(set(ip['gp'] // W for ip in sim.ips))

    def step_forward():
        nonlocal step_all_count
        sim.step_all()
        # Noise
        cr = code_rows()
        np.apply_forward(step_all_count, sim.grid, sim._to_flat, cr)
        # Waste cleanup: zero all non-zero cells on working rows
        cleaned = []
        for row in working_rows():
            base = row * W
            for c in range(W):
                flat = base + c
                val = sim.grid[flat]
                if val != 0:
                    cleaned.append((flat, val))
                    sim.grid[flat] = wp.consume(val)
        if cleaned:
            cleanup_log[step_all_count] = cleaned
        step_all_count += 1

    def step_backward():
        nonlocal step_all_count
        step_all_count -= 1
        # Undo waste cleanup (LIFO)
        cleaned = cleanup_log.pop(step_all_count, [])
        for flat, _val in reversed(cleaned):
            sim.grid[flat] = wp.unconsume()
        # Undo noise
        cr = code_rows()
        np.undo_at(step_all_count, sim.grid, sim._to_flat, cr)
        # Undo steps
        sim.step_back_all()

    N = 3500
    for _ in range(N):
        step_forward()

    # Verify some waste was actually cleaned
    assert wp.pointer > 0, f'No waste cleaned in {N} steps?'
    cleaned = wp.pointer

    # Now step back
    for _ in range(N):
        step_backward()

    assert step_all_count == 0
    assert wp.pointer == 0, f'Waste pool not empty: ptr={wp.pointer}'
    assert np.total_injected == 0, f'Noise not fully undone: {np.total_injected}'
    assert sim.grid == grid_start, 'Grid not restored after backward!'

    # Check IP state restored
    sim._save_active()
    for i, (got, want) in enumerate(zip(sim.ips, ips_start)):
        for key in want:
            assert got[key] == want[key], (
                f'IP{i}.{key}: got {got[key]}, want {want[key]}')

    print(f'  PASS: callback_hook_reversibility '
          f'({N} steps, {cleaned} cells cleaned, '
          f'{np._cache.__len__()} noise entries)')


if __name__ == '__main__':
    print('Running pool tests...')
    test_waste_pool_basic()
    test_waste_pool_reset()
    test_noise_pool_deterministic()
    test_noise_pool_rate_zero()
    test_noise_pool_rate_every_step()
    test_noise_pool_1M_rate()
    test_noise_pool_xor_reversible()
    test_noise_pool_low_rate()
    test_noise_pool_configure()
    test_full_forward_backward()
    test_callback_hook_reversibility()
    print('\nAll pool tests passed!')

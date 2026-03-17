#!/usr/bin/env python3
"""Reversible virtual pools for waste cleanup and noise injection.

Two off-grid pools that advance a pointer forward on step_all() and
retreat it on step_back_all(). Both are fully reversible — no history
log needed, just the pointer position and the pool contents.

WastePool — starts as infinite zeros. Consumed via swap with dirty
    working-area cells: dirty value goes into the pool, zero goes to
    the cell. Reverse: swap back.

NoisePool — a deterministic sequence of (row, col, bit) flip actions
    generated from a seed. Each entry also has a "flip?" coin based on
    the configured rate. Forward: XOR the target cell. Backward: XOR
    again (self-inverse). The seed + pointer fully determines state.
"""

import random


class WastePool:
    """Virtual pool of clean zeros, consumed by swapping with dirty cells.

    The pool is a list that grows as needed. Each slot starts at 0.
    On consume(cell_value), we store cell_value at the current slot and
    return 0 (the clean zero). On unconsume(), we return the stored
    value and reset the slot to 0.

    The pointer advances forward during forward execution and retreats
    during backward execution.
    """

    def __init__(self):
        self._data = []      # grows as needed; slots beyond len are 0
        self._ptr = 0        # next slot to consume
        self.total_swaps = 0

    @property
    def pointer(self):
        return self._ptr

    def reset(self):
        self._data.clear()
        self._ptr = 0
        self.total_swaps = 0

    def consume(self, dirty_value):
        """Swap a dirty cell value for a clean zero.

        Returns the zero. Stores dirty_value in the pool at current ptr.
        Advances the pointer.
        """
        # Grow pool if needed
        while self._ptr >= len(self._data):
            self._data.append(0)
        # The slot should be 0 (clean) — if not, something is wrong
        assert self._data[self._ptr] == 0, (
            f"WastePool slot {self._ptr} is {self._data[self._ptr]}, expected 0"
        )
        self._data[self._ptr] = dirty_value
        self._ptr += 1
        self.total_swaps += 1
        return 0  # the clean zero

    def unconsume(self):
        """Reverse one consume: retreat pointer, return the dirty value,
        reset the slot to 0.
        """
        assert self._ptr > 0, "WastePool: cannot unconsume at pointer 0"
        self._ptr -= 1
        dirty_value = self._data[self._ptr]
        self._data[self._ptr] = 0  # restore to clean
        self.total_swaps -= 1
        return dirty_value

    def status(self):
        return {
            'pointer': self._ptr,
            'total_swaps': self.total_swaps,
            'pool_size': len(self._data),
        }


class NoisePool:
    """Deterministic, reversible noise injection.

    A seeded RNG generates an infinite stream of potential noise events.
    Each event is a (row, col, bit) triple plus a coin flip (uniform
    random float). At query time, the caller provides the current noise
    rate; the event fires only if the coin < rate.

    Forward: call flip_at(step) → returns (row, col, bit) or None.
             If non-None, caller XORs grid[row,col] ^= (1 << bit).
    Backward: call flip_at(step) again → returns the same action.
              Caller XORs again (self-inverse).

    The pool is indexed by step number, so forward/backward both just
    recompute the same entry. No pointer needed — the step_count IS
    the pointer.

    The noise_rate can be changed at any time. This only affects which
    future events actually fire (the underlying coin values are fixed
    by the seed). Changing the rate mid-run and stepping back will
    correctly undo only the events that fired at the rate in effect
    when they were applied — BUT only if the rate was the same. If you
    change the rate and step back, the undo will use the new rate to
    decide whether to XOR, which may not match. To handle this, we
    store the rate that was in effect for each step in a small log.
    """

    def __init__(self, seed=42, n_code_rows=2, grid_cols=99,
                 noise_type='any', flips_per_1M=0.0,
                 col_min=1, col_max=None):
        self._seed = seed
        self._rng = random.Random(seed)
        # Cache of generated events: step -> (row_idx, col, bit, coin)
        # row_idx is an index into code_rows (not absolute row number)
        self._cache = {}
        self._max_generated = -1  # highest step we've generated up to

        # Grid geometry for address generation
        self.n_code_rows = n_code_rows
        self.grid_cols = grid_cols
        self.noise_type = noise_type  # 'any', 'parity', 'data'
        # Column range for noise (inclusive). Defaults to 1..cols-2
        # to skip boundary columns (col 0 and col cols-1).
        self.col_min = col_min
        self.col_max = col_max if col_max is not None else max(1, grid_cols - 2)

        # Rate: user-facing unit is "flips per 1M (million) step_alls".
        # Internally we convert to probability per step_all.
        self.flips_per_1M = flips_per_1M
        self.rate = self._compute_rate()  # internal probability per step

        self._rate_log = {}  # step -> rate that was in effect when applied

        # Stats
        self.total_injected = 0

    def _compute_rate(self):
        """Convert flips_per_1M to probability per step_all."""
        if self.flips_per_1M <= 0:
            return 0.0
        return self.flips_per_1M / 1_000_000.0

    PARITY_BITS = [0, 1, 2, 4, 8]
    DATA_BITS = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]

    def reset(self, seed=None):
        """Reset the pool. Optionally change seed."""
        if seed is not None:
            self._seed = seed
        self._rng = random.Random(self._seed)
        self._cache.clear()
        self._max_generated = -1
        self._rate_log.clear()
        self.total_injected = 0

    def _generate_up_to(self, step):
        """Ensure we have cached events up through the given step."""
        if step <= self._max_generated:
            return
        # We need to generate sequentially from where we left off
        # to maintain RNG determinism
        start = self._max_generated + 1
        # Reset RNG to start position by re-seeding and advancing
        # This is O(step) on first call but cached afterward
        if start == 0:
            self._rng = random.Random(self._seed)
        # Generate from start to step (inclusive)
        for s in range(start, step + 1):
            row_idx = self._rng.randint(0, max(0, self.n_code_rows - 1))
            col = self._rng.randint(self.col_min, self.col_max)
            if self.noise_type == 'parity':
                bit = self._rng.choice(self.PARITY_BITS)
            elif self.noise_type == 'data':
                bit = self._rng.choice(self.DATA_BITS)
            else:
                bit = self._rng.randint(0, 15)
            coin = self._rng.random()
            self._cache[s] = (row_idx, col, bit, coin)
        self._max_generated = step

    def flip_at(self, step, code_rows, rate=None):
        """Get the noise action for a given step.

        Args:
            step: the global step count
            code_rows: list of absolute row numbers that are code rows
            rate: override rate (if None, uses self.rate)

        Returns:
            (row, col, bit) if this step has a flip, else None.
        """
        if rate is None:
            rate = self.rate
        if rate <= 0:
            return None

        self._generate_up_to(step)
        row_idx, col, bit, coin = self._cache[step]

        if coin >= rate:
            return None  # no flip this step

        # Map row_idx to actual row
        if not code_rows:
            return None
        row = code_rows[row_idx % len(code_rows)]
        return (row, col, bit)

    def apply_forward(self, step, grid, flat_fn, code_rows):
        """Apply noise for this step (forward direction).

        Args:
            step: global step count
            grid: the simulator grid (list of ints)
            flat_fn: function(row, col) -> flat index
            code_rows: list of absolute row numbers

        Returns:
            (row, col, bit) if a flip was applied, else None.
        """
        action = self.flip_at(step, code_rows)
        if action is None:
            return None
        row, col, bit = action
        grid[flat_fn(row, col)] ^= (1 << bit)
        self._rate_log[step] = self.rate
        self.total_injected += 1
        return action

    def undo_at(self, step, grid, flat_fn, code_rows):
        """Undo noise for this step (backward direction).

        Uses the rate that was logged when this step was applied forward.
        If no rate was logged (step was never applied), uses current rate.
        """
        logged_rate = self._rate_log.pop(step, self.rate)
        action = self.flip_at(step, code_rows, rate=logged_rate)
        if action is None:
            return None
        row, col, bit = action
        grid[flat_fn(row, col)] ^= (1 << bit)  # XOR is self-inverse
        self.total_injected -= 1
        return action

    def configure(self, flips_per_1M=None, noise_type=None,
                  n_code_rows=None, grid_cols=None,
                  col_min=None, col_max=None):
        """Update configuration. Does NOT reset the cache (seed stays)."""
        if flips_per_1M is not None:
            self.flips_per_1M = flips_per_1M
            self.rate = self._compute_rate()
        if noise_type is not None:
            self.noise_type = noise_type
        # If geometry changes, we need to regenerate
        regen = False
        if n_code_rows is not None and n_code_rows != self.n_code_rows:
            self.n_code_rows = n_code_rows
            regen = True
        if grid_cols is not None and grid_cols != self.grid_cols:
            self.grid_cols = grid_cols
            # Update col_max default if it wasn't explicitly set
            if col_max is None and self.col_max == max(1, self.grid_cols - 2):
                pass  # will be updated below
            regen = True
        if col_min is not None and col_min != self.col_min:
            self.col_min = col_min
            regen = True
        if col_max is not None and col_max != self.col_max:
            self.col_max = col_max
            regen = True
        # Keep col_max in sync with grid_cols if using default
        if grid_cols is not None:
            self.col_max = max(self.col_min, grid_cols - 2)
        if regen:
            self._cache.clear()
            self._max_generated = -1

    def status(self):
        return {
            'seed': self._seed,
            'flips_per_1M': self.flips_per_1M,
            'rate_per_step': self.rate,
            'noise_type': self.noise_type,
            'total_injected': self.total_injected,
            'cache_size': len(self._cache),
            'n_code_rows': self.n_code_rows,
            'grid_cols': self.grid_cols,
        }

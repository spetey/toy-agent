#!/usr/bin/env python3
"""Flask backend for fb2d GUI — wraps the existing FB2DSimulator."""

import os
import json
from flask import Flask, jsonify, request, send_file, abort
from fb2d import (FB2DSimulator, OPCODE_TO_CHAR, OPCODES, hamming_encode,
                  cell_to_payload, encode_opcode, OPCODE_PAYLOADS,
                  _PAYLOAD_TO_OPCODE)
from pools import WastePool, NoisePool

PROGRAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')

app = Flask(__name__)
sim = FB2DSimulator()
current_file = ''  # Track which file is currently loaded

# ── Reversible pools ──────────────────────────────────────────
#
# WastePool: virtual infinite zeros. Swaps dirty working-area cells
#   for clean zeros. Reversible: swap back on step_back.
#
# NoisePool: deterministic bit-flip sequence from a seed.
#   Forward: XOR target cell. Backward: XOR again (self-inverse).
#   Rate-tunable: only entries whose coin < rate actually fire.

waste_pool = WastePool()
noise_pool = NoisePool(seed=42, flips_per_1M=0.0)

noise_enabled = False
waste_cleanup_enabled = False
free_food_enabled = False
free_food_bite_size = 15
free_food_payloads = [189, 250, 380, 639]  # A, B, C, D

# Track which step_all count we're on (for pool indexing).
# This is separate from sim.step_count which counts per-IP steps.
_step_all_count = 0


def _read_state_hint(path, key):
    """Read a boolean hint (key=1) from a .fb2d state file."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f'{key}='):
                    return line.split('=', 1)[1].strip() == '1'
    except Exception:
        pass
    return False


def _get_code_rows():
    """Auto-detect code rows from IP positions."""
    sim._save_active()
    return list(set(ip['ip_row'] for ip in sim.ips))


def _apply_noise_forward(step):
    """Apply noise for this step_all (forward). Returns action or None."""
    if not noise_enabled or noise_pool.rate <= 0:
        return None
    code_rows = _get_code_rows()
    return noise_pool.apply_forward(step, sim.grid, sim._to_flat, code_rows)


def _undo_noise(step):
    """Undo noise for this step_all (backward)."""
    if not noise_enabled:
        return None
    code_rows = _get_code_rows()
    return noise_pool.undo_at(step, sim.grid, sim._to_flat, code_rows)


# Per-step records for waste cleanup reversal.
# Maps step_all_count -> list of (flat_addr, dirty_value) for cells
# cleaned after that round's step_all.
_waste_cleanup_log = {}


def _get_working_rows():
    """Auto-detect working-area rows from EX head positions."""
    sim._save_active()
    W = sim.cols
    rows = set()
    for ip in sim.ips:
        rows.add(ip['ex'] // W)
    return sorted(rows)


def _apply_waste_cleanup_forward(step):
    """Rolling waste cleanup on EX rows.  Runs after step_all.

    Instead of zeroing every cell every step (which breaks the v5
    EX-dirty invariant), uses position-based rolling cleanup:
      - When EX column >= 90% of width: clear first half (cols 0..W/2-1).
        EX is about to wrap; ensures clean cells waiting at col 0+.
      - When EX column <= 10% of width: clear second half (cols W/2..W-1).
        EX has just wrapped; clears the old trail behind it.
    Most steps neither threshold is hit, so the dirty trail is preserved.
    Never clears the cell EX is currently sitting on (preserves the
    v5 )P dirty invariant; harmless for v4).

    Fully reversible: _undo_waste_cleanup restores dirty values.
    """
    if not waste_cleanup_enabled:
        return 0

    W = sim.cols
    half = W // 2
    threshold_high = int(W * 0.9)
    threshold_low = int(W * 0.1)

    # Collect EX positions to protect
    sim._save_active()
    ex_flats = set()
    for ip in sim.ips:
        ex_flats.add(ip['ex'])

    cleaned = []
    for row in _get_working_rows():
        base = row * W
        # Find which IP's EX is on this row
        ex_col = None
        for ip in sim.ips:
            if ip['ex'] // W == row:
                ex_col = ip['ex'] % W
                break
        if ex_col is None:
            continue

        # Determine which half to clear based on EX position
        if ex_col >= threshold_high:
            clear_range = range(half)           # first half
        elif ex_col <= threshold_low:
            clear_range = range(half, W)        # second half
        else:
            continue                            # no cleanup this step

        for c in clear_range:
            flat = base + c
            if flat in ex_flats:
                continue                        # never clear EX's cell
            val = sim.grid[flat]
            if val != 0:
                cleaned.append((flat, val))
                sim.grid[flat] = waste_pool.consume(val)

    if cleaned:
        _waste_cleanup_log[step] = cleaned
    return len(cleaned)


def _undo_waste_cleanup(step):
    """Restore dirty cells cleaned at this step (LIFO order)."""
    cleaned = _waste_cleanup_log.pop(step, None)
    if not cleaned:
        return 0
    for flat, _val in reversed(cleaned):
        sim.grid[flat] = waste_pool.unconsume()
    return len(cleaned)


# ── Free food (non-reversible cheat for metabolism testing) ────────

_free_food_log = {}

def _apply_free_food(step):
    """Refill fuel when the east-end food is running low.

    Detection (no EX dependency — just looks at the row):
    1. Count contiguous non-zero cells at the EAST end of the row.
       If this count <= bite_size, trigger.
    2. Find contiguous non-zero cells at the WEST end of the row.
       This is the garbage trail.

    Action: clear all west-end garbage EXCEPT the last cell (which
    EX may be sitting on), and fill those cells with continuing food.

    Example (bite=6):
      Before: G G G G G G G . . . . . D D D D D .
      After:  A A A A A A G . . . . . D D D D D .
    """
    if not free_food_enabled:
        return 0

    from fb2d import hamming_encode, _CELL_TO_PAYLOAD

    W = sim.cols
    bite = free_food_bite_size
    payloads = free_food_payloads
    food_payload_set = set(payloads)

    sim._save_active()
    changes = []

    # Collect EX positions to protect
    ex_flats = set()
    for ip in sim.ips:
        ex_flats.add(ip['ex'])

    for ip in sim.ips:
        row = ip['ex'] // W
        base = row * W

        # Count contiguous FOOD cells (any food payload — the whole
        # AAABBBCCCDDD block counts as one stretch). Find the longest
        # such stretch, including wrapping around the row ends.
        # Also track the last food payload for rotation continuation.
        is_food = [False] * W
        last_food_payload = None
        for c in range(W):
            val = sim.grid[base + c]
            if val != 0:
                p = _CELL_TO_PAYLOAD[val]
                if p in food_payload_set:
                    is_food[c] = True
                    last_food_payload = p

        # Find longest contiguous food stretch (with wrapping)
        max_food_stretch = 0
        cur = 0
        # Scan twice to handle wrapping
        for c in list(range(W)) + list(range(W)):
            if is_food[c]:
                cur += 1
                if cur > max_food_stretch:
                    max_food_stretch = cur
                if cur >= W:
                    break  # entire row is food
            else:
                cur = 0

        if max_food_stretch >= 2 * bite:
            continue  # plenty of food

        # Trigger: longest contiguous food run < 2*bite.
        # Replace only GARBAGE cells (non-zero cells that aren't part of
        # a food run) with continuing food. Leave food runs and zeros alone.

        # Identify which cells are part of food runs vs garbage.
        # A food run = contiguous cells with the same food payload (>=2 cells).
        is_food_run = [False] * W
        run_start = None
        run_payload = None

        def _close_run(end_col):
            """Mark cells [run_start..end_col) as food if run >= 2."""
            if run_start is not None and end_col - run_start >= 2:
                for rc in range(run_start, end_col):
                    is_food_run[rc] = True

        for c in range(W):
            val = sim.grid[base + c]
            p = _CELL_TO_PAYLOAD[val] if val != 0 else None
            if p is not None and p in food_payload_set and p == run_payload:
                pass  # extend current run
            else:
                _close_run(c)  # close previous run
                if p is not None and p in food_payload_set:
                    run_start = c
                    run_payload = p
                else:
                    run_start = None
                    run_payload = None
        _close_run(W)  # close final run

        # Find garbage cells: non-zero AND not part of a food run
        garbage_cols = [c for c in range(W)
                        if sim.grid[base + c] != 0 and not is_food_run[c]]
        if len(garbage_cols) < 2:
            continue  # not enough garbage to replace

        # Find the last food cell (easternmost in the contiguous food block)
        # and determine where to continue the pattern from.
        # Walk east from food cells to find where food ends and garbage begins.
        last_food_col = -1
        last_food_p = None
        # Scan the food stretch to find its eastern end
        for c in range(W):
            if is_food[c]:
                last_food_col = c
                last_food_p = _CELL_TO_PAYLOAD[sim.grid[base + c]]

        # Figure out how many cells of the current bite remain.
        # Count backwards from last_food_col to find how many cells of
        # last_food_p there are at the tail of the food stretch.
        tail_count = 0
        if last_food_col >= 0 and last_food_p is not None:
            c = last_food_col
            while c >= 0 and _CELL_TO_PAYLOAD[sim.grid[base + c]] == last_food_p:
                tail_count += 1
                c -= 1

        # Determine starting payload and remaining count in current bite
        if last_food_p is not None and last_food_p in payloads:
            bite_idx = payloads.index(last_food_p)
            remaining_in_bite = bite - (tail_count % bite)
            if remaining_in_bite == bite:
                # Tail was exactly a multiple of bite, move to next
                bite_idx = (bite_idx + 1) % len(payloads)
                remaining_in_bite = bite
        else:
            bite_idx = 0
            remaining_in_bite = bite

        # Fill garbage cells east of the food, wrapping around, continuing
        # the food pattern. Skip the last garbage cell (EX may be on it).
        # Order: start from the first garbage cell after the food stretch.
        first_garbage_after_food = None
        for gc in garbage_cols:
            if last_food_col < 0 or gc > last_food_col:
                first_garbage_after_food = gc
                break
        # If no garbage after food, wrap to the first garbage cell
        if first_garbage_after_food is None:
            first_garbage_after_food = garbage_cols[0]

        # Build ordered list: garbage east of food first, then wrap
        ordered_garbage = []
        idx = garbage_cols.index(first_garbage_after_food)
        for i in range(len(garbage_cols)):
            ordered_garbage.append(garbage_cols[(idx + i) % len(garbage_cols)])
        # Remove the last garbage cell (keep it for EX)
        last_garbage = garbage_cols[-1]
        ordered_garbage = [c for c in ordered_garbage if c != last_garbage]

        row_changes = []
        last_filled_flat = None
        for c in ordered_garbage:
            flat = base + c
            if flat in ex_flats:
                continue
            old_val = sim.grid[flat]
            new_val = hamming_encode(payloads[bite_idx])
            if old_val != new_val:
                row_changes.append((flat, old_val))
                sim.grid[flat] = new_val
            last_filled_flat = flat
            remaining_in_bite -= 1
            if remaining_in_bite <= 0:
                bite_idx = (bite_idx + 1) % len(payloads)
                remaining_in_bite = bite

        # Ensure the last filled cell differs from the preserved garbage
        # cell. Otherwise the compressor sees them as one run and hits
        # the zero buffer, causing the zero-in-fuel problem.
        if last_filled_flat is not None:
            last_filled_p = _CELL_TO_PAYLOAD[sim.grid[last_filled_flat]]
            preserved_p = _CELL_TO_PAYLOAD[sim.grid[base + last_garbage]]
            if last_filled_p == preserved_p and last_filled_flat not in ex_flats:
                # Change to next payload in rotation
                alt_idx = (payloads.index(last_filled_p) + 1) % len(payloads)
                alt_val = hamming_encode(payloads[alt_idx])
                row_changes.append((last_filled_flat, sim.grid[last_filled_flat]))
                sim.grid[last_filled_flat] = alt_val

        changes.extend(row_changes)

    if changes:
        _free_food_log[step] = changes
    return len(changes)


def _undo_free_food(step):
    """Undo free food changes at this step."""
    changes = _free_food_log.pop(step, None)
    if not changes:
        return 0
    for flat, old_val in reversed(changes):
        sim.grid[flat] = old_val
    return len(changes)


def _step_all_forward():
    """One forward round with reversible noise + waste cleanup + free food.

    Order: step all IPs, then apply noise, then clean working rows,
    then refill food if needed.
    """
    global _step_all_count
    sim.step_all()
    _apply_noise_forward(_step_all_count)
    _apply_waste_cleanup_forward(_step_all_count)
    _apply_free_food(_step_all_count)
    _step_all_count += 1


def _step_all_backward():
    """Reverse one round: undo food, undo waste, undo noise, undo steps."""
    global _step_all_count
    _step_all_count -= 1
    _undo_free_food(_step_all_count)
    _undo_waste_cleanup(_step_all_count)
    _undo_noise(_step_all_count)
    sim.step_back_all()


def serialize_state():
    """Return current simulator state as a dict."""
    # Ensure ips array is up to date
    sim._save_active()
    result = {
        'rows': sim.rows,
        'cols': sim.cols,
        'grid': sim.grid,
        # Legacy flat fields (for backward compat with old GUI code)
        'ip_row': sim.ip_row,
        'ip_col': sim.ip_col,
        'ip_dir': sim.ip_dir,
        'cl': sim.cl,
        'h0': sim.h0,
        'h1': sim.h1,
        'ix': sim.ix,
        'ex': sim.ex,
        'step_count': sim.step_count,
        'step_all_count': _step_all_count,
        'current_file': current_file,
        # Multi-IP fields
        'n_ips': sim.n_ips,
        'active_ip': sim.active_ip,
        'ips': sim.ips,
        # Noise pool state
        'noise_enabled': noise_enabled,
        'noise_rate': noise_pool.flips_per_1M,       # user-facing: flips/1M steps
        'noise_rate_per_step': noise_pool.rate,       # internal: prob/step
        'noise_type': noise_pool.noise_type,
        'noise_total_injected': noise_pool.total_injected,
        'noise_seed': noise_pool._seed,
        # Waste pool state
        'waste_cleanup_enabled': waste_cleanup_enabled,
        'waste_pool_pointer': waste_pool.pointer,
        'waste_pool_swaps': waste_pool.total_swaps,
        # Free food state
        'free_food_enabled': free_food_enabled,
        'free_food_bite_size': free_food_bite_size,
    }
    return result


@app.route('/')
def index():
    return send_file('fb2d_gui.html')


@app.route('/api/state')
def get_state():
    return jsonify(serialize_state())


@app.route('/api/new', methods=['POST'])
def new_program():
    """Create a blank grid with the specified dimensions."""
    global current_file, _step_all_count, waste_cleanup_enabled, free_food_enabled
    data = request.get_json(force=True)
    rows = int(data.get('rows', 10))
    cols = int(data.get('cols', 10))
    if rows < 1 or cols < 1 or rows > 1000 or cols > 2000:
        abort(400, 'Invalid dimensions (max 1000x2000)')
    sim.rows = rows
    sim.cols = cols
    sim.grid_size = rows * cols
    sim.grid = [0] * sim.grid_size
    sim.step_count = 0
    # Reset to single IP at (0,0) going East
    sim.ips = [{
        'ip_row': 0, 'ip_col': 0, 'ip_dir': 1,  # DIR_E = 1
        'h0': 0, 'h1': 0, 'ix': 0, 'ix_dir': 1, 'ix_vdir': 2,  # DIR_S = 2
        'cl': 0, 'ex': 0,
    }]
    sim.n_ips = 1
    sim.active_ip = 0
    sim._load_active(0)
    current_file = ''
    _step_all_count = 0
    noise_pool.reset()
    waste_pool.reset()
    _waste_cleanup_log.clear()
    _free_food_log.clear()
    waste_cleanup_enabled = False
    free_food_enabled = False
    return jsonify(serialize_state())


@app.route('/api/load', methods=['POST'])
def load_file():
    global current_file, _step_all_count, waste_cleanup_enabled, free_food_enabled
    data = request.get_json(force=True)
    filename = data.get('filename', '')
    # Sanitize: only allow filenames, no path traversal
    if '/' in filename or '\\' in filename or '..' in filename:
        abort(400, 'Invalid filename')
    path = os.path.join(PROGRAMS_DIR, filename)
    if not os.path.isfile(path):
        abort(404, f'File not found: {filename}')
    sim.load_state(path)
    current_file = filename
    _step_all_count = 0
    # Reset pools (keep enabled/rate/type settings)
    noise_pool.reset()
    noise_pool.configure(n_code_rows=sim.rows, grid_cols=sim.cols)
    waste_pool.reset()
    _waste_cleanup_log.clear()
    _free_food_log.clear()
    # Read hints from state file (default off)
    waste_cleanup_enabled = _read_state_hint(path, 'waste_cleanup')
    free_food_enabled = _read_state_hint(path, 'free_food')
    return jsonify(serialize_state())


@app.route('/api/reset', methods=['POST'])
def reset_state():
    """Reload the current file to reset back to step 0."""
    global _step_all_count, waste_cleanup_enabled
    data = request.get_json(force=True) if request.data else {}
    filename = data.get('filename', '') or current_file
    if not filename:
        abort(400, 'No filename to reset')
    if '/' in filename or '\\' in filename or '..' in filename:
        abort(400, 'Invalid filename')
    path = os.path.join(PROGRAMS_DIR, filename)
    if not os.path.isfile(path):
        abort(404, f'File not found: {filename}')
    sim.load_state(path)
    _step_all_count = 0
    # Reset pools (keep enabled/rate/type settings)
    noise_pool.reset()
    noise_pool.configure(n_code_rows=sim.rows, grid_cols=sim.cols)
    waste_pool.reset()
    _waste_cleanup_log.clear()
    _free_food_log.clear()
    waste_cleanup_enabled = _read_state_hint(path, 'waste_cleanup')
    free_food_enabled = _read_state_hint(path, 'free_food')
    return jsonify(serialize_state())


@app.route('/api/step', methods=['POST'])
def step_forward():
    n = min(int(request.args.get('n', 1)), 10000)
    try:
        for _ in range(n):
            _step_all_forward()
    except Exception as e:
        print(f'[step] error after {n} steps: {e}')
        # Return current state even on error so GUI can display it
    return jsonify(serialize_state())


@app.route('/api/back', methods=['POST'])
def step_backward():
    n = min(int(request.args.get('n', 1)), 10000)
    try:
        for _ in range(n):
            if _step_all_count <= 0:
                break
            _step_all_backward()
    except Exception as e:
        print(f'[back] error after {n} steps: {e}')
    return jsonify(serialize_state())


@app.route('/api/files')
def list_files():
    files = sorted(f for f in os.listdir(PROGRAMS_DIR) if f.endswith('.fb2d'))
    return jsonify(files)


@app.route('/api/opcodes')
def get_opcodes():
    return jsonify({str(k): v for k, v in OPCODE_TO_CHAR.items()})


@app.route('/api/annotations', methods=['GET'])
def get_annotations():
    filename = request.args.get('file', '')
    if not filename:
        abort(400, 'Missing file parameter')
    ann_path = os.path.join(PROGRAMS_DIR, filename + '.annotations.json')
    if os.path.isfile(ann_path):
        with open(ann_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({'cells': {}, 'regions': []})


@app.route('/api/annotations', methods=['POST'])
def save_annotations():
    filename = request.args.get('file', '')
    if not filename or '/' in filename or '\\' in filename or '..' in filename:
        abort(400, 'Invalid file parameter')
    data = request.get_json(force=True)
    ann_path = os.path.join(PROGRAMS_DIR, filename + '.annotations.json')
    with open(ann_path, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ── Edit routes ─────────────────────────────────────────────────


@app.route('/api/setcell', methods=['POST'])
def set_cell():
    data = request.get_json(force=True)
    r, c, val = int(data['r']), int(data['c']), int(data['value'])
    if 0 <= r < sim.rows and 0 <= c < sim.cols and 0 <= val <= 65535:
        sim.grid[sim._to_flat(r, c)] = val
    return jsonify(serialize_state())


@app.route('/api/setcells', methods=['POST'])
def set_cells():
    data = request.get_json(force=True)
    for cell in data.get('cells', []):
        r, c, val = int(cell['r']), int(cell['c']), int(cell['value'])
        if 0 <= r < sim.rows and 0 <= c < sim.cols and 0 <= val <= 65535:
            sim.grid[sim._to_flat(r, c)] = val
    return jsonify(serialize_state())


@app.route('/api/select', methods=['POST'])
def select_rect():
    data = request.get_json(force=True)
    sim.select_rect(int(data['r1']), int(data['c1']),
                    int(data['r2']), int(data['c2']))
    return jsonify(serialize_state())


@app.route('/api/copy', methods=['POST'])
def copy_rect():
    sim.copy_rect()
    has = sim.clipboard is not None
    w, h = (sim.clipboard[0], sim.clipboard[1]) if has else (0, 0)
    result = serialize_state()
    result['clipboard'] = {'width': w, 'height': h, 'loaded': has}
    return jsonify(result)


@app.route('/api/cut', methods=['POST'])
def cut_rect():
    sim.cut_rect()
    has = sim.clipboard is not None
    w, h = (sim.clipboard[0], sim.clipboard[1]) if has else (0, 0)
    result = serialize_state()
    result['clipboard'] = {'width': w, 'height': h, 'loaded': has}
    return jsonify(result)


@app.route('/api/paste', methods=['POST'])
def paste_rect():
    data = request.get_json(force=True)
    sim.paste_rect(int(data['r']), int(data['c']))
    return jsonify(serialize_state())


@app.route('/api/delete_selection', methods=['POST'])
def delete_selection():
    """Zero all cells in the current selection."""
    if sim.selection is not None:
        r1, c1, r2, c2 = sim.selection
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                sim.grid[sim._to_flat(r, c)] = 0
    return jsonify(serialize_state())


@app.route('/api/save', methods=['POST'])
def save_state():
    data = request.get_json(force=True)
    filename = data.get('filename', currentFile if 'currentFile' in dir() else '')
    if not filename:
        abort(400, 'No filename specified')
    if '/' in filename or '\\' in filename or '..' in filename:
        abort(400, 'Invalid filename')
    if not filename.endswith('.fb2d'):
        filename += '.fb2d'
    path = os.path.join(PROGRAMS_DIR, filename)
    sim.save_state(path)
    return jsonify({'ok': True, 'filename': filename})


@app.route('/api/resize', methods=['POST'])
def resize_grid():
    data = request.get_json(force=True)
    new_rows, new_cols = int(data['rows']), int(data['cols'])
    if new_rows < 1 or new_cols < 1 or new_rows > 1000 or new_cols > 2000:
        abort(400, 'Invalid dimensions')
    # Preserve existing data where possible
    old_grid = list(sim.grid)
    old_rows, old_cols = sim.rows, sim.cols
    sim.rows = new_rows
    sim.cols = new_cols
    sim.grid_size = new_rows * new_cols
    sim.grid = [0] * sim.grid_size
    for r in range(min(old_rows, new_rows)):
        for c in range(min(old_cols, new_cols)):
            sim.grid[r * new_cols + c] = old_grid[r * old_cols + c]
    # Clamp head positions for all IPs
    sim._save_active()
    for i, ipstate in enumerate(sim.ips):
        ipstate['ip_row'] = min(ipstate['ip_row'], new_rows - 1)
        ipstate['ip_col'] = min(ipstate['ip_col'], new_cols - 1)
        for head in ('cl', 'h0', 'h1', 'ix', 'ex'):
            ipstate[head] = min(ipstate[head], sim.grid_size - 1)
    sim._load_active(sim.active_ip)
    sim.selection = None
    sim.clipboard = None
    return jsonify(serialize_state())


# ── Multi-IP routes ────────────────────────────────────────────


@app.route('/api/addip', methods=['POST'])
def add_ip():
    data = request.get_json(force=True) if request.data else {}
    ip_row = int(data.get('ip_row', 0))
    ip_col = int(data.get('ip_col', 0))
    ip_dir = int(data.get('ip_dir', 1))  # DIR_E = 1
    h0 = int(data.get('h0', 0))
    h1 = int(data.get('h1', 0))
    ix = int(data.get('ix', 0))
    ix_dir = int(data.get('ix_dir', 1))  # DIR_E = 1
    cl = int(data.get('cl', 0))
    ex = int(data.get('ex', 0))
    idx = sim.add_ip(ip_row=ip_row, ip_col=ip_col, ip_dir=ip_dir,
                     h0=h0, h1=h1, ix=ix, ix_dir=ix_dir, cl=cl, ex=ex)
    result = serialize_state()
    result['added_ip'] = idx
    return jsonify(result)


@app.route('/api/sethead', methods=['POST'])
def set_head():
    """Set a head position or IP state for the given IP index."""
    data = request.get_json(force=True)
    ip_idx = int(data.get('ip', 0))
    if not (0 <= ip_idx < sim.n_ips):
        abort(400, f'Invalid IP index: {ip_idx}')
    sim._save_active()
    ipstate = sim.ips[ip_idx]
    # Set head by name to flat address (row * cols + col)
    head = data.get('head', '')
    if head in ('h0', 'h1', 'ix', 'cl', 'ex'):
        row = int(data['row'])
        col = int(data['col'])
        if 0 <= row < sim.rows and 0 <= col < sim.cols:
            ipstate[head] = row * sim.cols + col
    elif head == 'ip':
        row = int(data['row'])
        col = int(data['col'])
        if 0 <= row < sim.rows and 0 <= col < sim.cols:
            ipstate['ip_row'] = row
            ipstate['ip_col'] = col
    # Optional: set direction fields
    if 'ip_dir' in data:
        ipstate['ip_dir'] = int(data['ip_dir']) % 4
    if 'ix_dir' in data:
        ipstate['ix_dir'] = int(data['ix_dir']) % 4
    if 'ix_vdir' in data:
        ipstate['ix_vdir'] = int(data['ix_vdir']) % 4
    sim._load_active(sim.active_ip)
    return jsonify(serialize_state())


@app.route('/api/rmip', methods=['POST'])
def remove_ip():
    data = request.get_json(force=True)
    idx = int(data.get('index', -1))
    if sim.n_ips <= 1:
        abort(400, 'Cannot remove the last IP')
    if not (0 <= idx < sim.n_ips):
        abort(400, f'Invalid IP index: {idx}')
    sim._save_active()
    sim.ips.pop(idx)
    sim.n_ips = len(sim.ips)
    if sim.active_ip >= sim.n_ips:
        sim.active_ip = sim.n_ips - 1
    elif sim.active_ip == idx:
        sim.active_ip = min(idx, sim.n_ips - 1)
    sim._load_active(sim.active_ip)
    return jsonify(serialize_state())


@app.route('/api/switchip', methods=['POST'])
def switch_ip():
    data = request.get_json(force=True)
    idx = int(data.get('index', 0))
    if 0 <= idx < sim.n_ips:
        sim._activate_ip(idx)
    else:
        abort(400, f'Invalid IP index: {idx}')
    return jsonify(serialize_state())


# ── Reversible pool routes ─────────────────────────────────────


@app.route('/api/noise', methods=['GET'])
def get_noise():
    return jsonify({
        'enabled': noise_enabled,
        'flips_per_1M': noise_pool.flips_per_1M,
        'rate_per_step': noise_pool.rate,
        'type': noise_pool.noise_type,
        'total_injected': noise_pool.total_injected,
        'seed': noise_pool._seed,
        # GUI sends/reads 'rate' as flips per 1M
        'rate': noise_pool.flips_per_1M,
    })


@app.route('/api/noise', methods=['POST'])
def set_noise():
    global noise_enabled
    data = request.get_json(force=True)
    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if new_enabled and not noise_enabled:
            seed = data.get('seed', None)
            noise_pool.reset(seed=seed)
            noise_pool.configure(n_code_rows=len(_get_code_rows()),
                                 grid_cols=sim.cols)
        noise_enabled = new_enabled
    if 'rate' in data:
        # 'rate' = flips per 1M step_alls
        noise_pool.configure(flips_per_1M=max(0.0, float(data['rate'])))
    if 'type' in data:
        t = data['type']
        if t in ('any', 'parity', 'data'):
            noise_pool.configure(noise_type=t)
    if 'seed' in data and noise_enabled:
        new_seed = int(data['seed'])
        if new_seed != noise_pool._seed:
            if noise_pool.total_injected > 0:
                print(f'[noise-pool] WARNING: seed change rejected — '
                      f'{noise_pool.total_injected} flips outstanding. '
                      f'Step back to 0 or reset first.')
            else:
                noise_pool.reset(seed=new_seed)
    print(f'[noise-pool] config: enabled={noise_enabled} '
          f'rate={noise_pool.flips_per_1M}/1M rounds '
          f'({noise_pool.rate:.9f}/step) '
          f'type={noise_pool.noise_type} seed={noise_pool._seed}')
    return jsonify({
        'enabled': noise_enabled,
        'flips_per_1M': noise_pool.flips_per_1M,
        'rate_per_step': noise_pool.rate,
        'type': noise_pool.noise_type,
        'total_injected': noise_pool.total_injected,
        'seed': noise_pool._seed,
        'rate': noise_pool.flips_per_1M,
    })


@app.route('/api/waste_cleanup', methods=['POST'])
def set_waste_cleanup():
    global waste_cleanup_enabled
    data = request.get_json(force=True)
    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if new_enabled and not waste_cleanup_enabled:
            waste_pool.reset()
            _waste_cleanup_log.clear()
            _free_food_log.clear()
        waste_cleanup_enabled = new_enabled
    print(f'[waste-pool] config: enabled={waste_cleanup_enabled}'
          f' pool_ptr={waste_pool.pointer}')
    return jsonify({
        'enabled': waste_cleanup_enabled,
        'pointer': waste_pool.pointer,
        'total_swaps': waste_pool.total_swaps,
    })


@app.route('/api/ex_cleanup', methods=['POST'])
def set_gp_cleanup():
    """Legacy endpoint — redirects to waste_cleanup."""
    return set_waste_cleanup()


@app.route('/api/free_food', methods=['POST'])
def set_free_food():
    global free_food_enabled, free_food_bite_size
    data = request.get_json(force=True)
    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if new_enabled and not free_food_enabled:
            _free_food_log.clear()
        free_food_enabled = new_enabled
    if 'bite_size' in data:
        free_food_bite_size = max(1, int(data['bite_size']))
    print(f'[free-food] config: enabled={free_food_enabled}'
          f' bite_size={free_food_bite_size}')
    return jsonify({
        'enabled': free_food_enabled,
        'bite_size': free_food_bite_size,
    })


# ── Snapshot routes ───────────────────────────────────────────────
#
# A snapshot captures the zero-state (source .fb2d file contents, pool
# config) plus the step count. Loading a snapshot replays deterministically
# from step 0, rebuilding full pool history so reversibility works.


@app.route('/api/snapshot', methods=['GET'])
def download_snapshot():
    """Download a JSON snapshot of the current run.

    Contains everything needed for deterministic replay: the initial
    .fb2d file contents (grid + IPs at step 0), pool configuration,
    and the step count to replay to. On load, the server replays
    forward from step 0 to reconstruct full reversibility state.
    """
    # Read the source .fb2d file contents
    source_contents = None
    if current_file:
        path = os.path.join(PROGRAMS_DIR, current_file)
        if os.path.isfile(path):
            with open(path, 'r') as f:
                source_contents = f.read()

    snapshot = {
        'version': 1,
        'source_file': current_file,
        'source_contents': source_contents,
        'step_all_count': _step_all_count,
        'noise': {
            'enabled': noise_enabled,
            'seed': noise_pool._seed,
            'flips_per_1M': noise_pool.flips_per_1M,
            'type': noise_pool.noise_type,
            # Geometry params that affect RNG event generation
            'n_code_rows': noise_pool.n_code_rows,
            'grid_cols': noise_pool.grid_cols,
            'col_min': noise_pool.col_min,
            'col_max': noise_pool.col_max,
        },
        'waste_cleanup_enabled': waste_cleanup_enabled,
        # Current state for quick inspection (not used for replay)
        'current_state': serialize_state(),
    }
    return jsonify(snapshot)


@app.route('/api/snapshot', methods=['POST'])
def load_snapshot():
    """Load a snapshot by replaying from zero-state.

    Accepts a JSON snapshot, writes the source .fb2d to a temp file
    if needed, loads it, configures pools, then replays forward to
    the target step count. Returns the final state.
    """
    global current_file, _step_all_count, noise_enabled, waste_cleanup_enabled, free_food_enabled

    data = request.get_json(force=True)
    version = data.get('version', 1)
    target_steps = int(data.get('step_all_count', 0))
    source_file = data.get('source_file', '')
    source_contents = data.get('source_contents', None)
    noise_cfg = data.get('noise', {})
    waste_enabled = data.get('waste_cleanup_enabled', False)

    # Step 1: Load the zero-state grid
    import tempfile
    if source_contents:
        # Write source to a temp file and load it
        with tempfile.NamedTemporaryFile(mode='w', suffix='.fb2d',
                                         delete=False) as tmp:
            tmp.write(source_contents)
            tmp_path = tmp.name
        try:
            sim.load_state(tmp_path)
        finally:
            os.unlink(tmp_path)
    elif source_file:
        path = os.path.join(PROGRAMS_DIR, source_file)
        if not os.path.isfile(path):
            abort(404, f'Source file not found: {source_file}')
        sim.load_state(path)
    else:
        abort(400, 'Snapshot must contain source_contents or source_file')

    current_file = source_file
    _step_all_count = 0

    # Step 2: Configure pools with exact geometry from snapshot
    # n_code_rows and col range must match the original run exactly,
    # because they determine the RNG output in _generate_up_to().
    noise_pool.reset(seed=int(noise_cfg.get('seed', 42)))
    noise_pool.configure(
        flips_per_1M=float(noise_cfg.get('flips_per_1M', 0)),
        noise_type=noise_cfg.get('type', 'any'),
        n_code_rows=int(noise_cfg.get('n_code_rows', len(_get_code_rows()))),
        grid_cols=int(noise_cfg.get('grid_cols', sim.cols)),
        col_min=int(noise_cfg.get('col_min', 1)),
        col_max=int(noise_cfg.get('col_max', max(1, sim.cols - 2))),
    )
    noise_enabled = bool(noise_cfg.get('enabled', False))

    waste_pool.reset()
    _waste_cleanup_log.clear()
    _free_food_log.clear()
    waste_cleanup_enabled = bool(waste_enabled)

    # Step 3: Replay forward to target step
    for _ in range(target_steps):
        _step_all_forward()

    print(f'[snapshot] loaded: {source_file}, replayed {target_steps} steps '
          f'(noise={noise_enabled}, waste={waste_cleanup_enabled})')

    return jsonify(serialize_state())


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    print(f'fb2d GUI server — programs dir: {PROGRAMS_DIR}')
    print(f'Open http://localhost:{port}')
    app.run(debug=True, use_reloader=False, port=port)

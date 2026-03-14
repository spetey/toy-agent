#!/usr/bin/env python3
"""Flask backend for fb2d GUI — wraps the existing FB2DSimulator."""

import os
import json
import math
import random
from flask import Flask, jsonify, request, send_file, abort
from fb2d import (FB2DSimulator, OPCODE_TO_CHAR, OPCODES, hamming_encode,
                  cell_to_payload, encode_opcode, OPCODE_PAYLOADS,
                  _PAYLOAD_TO_OPCODE)

PROGRAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')

app = Flask(__name__)
sim = FB2DSimulator()
current_file = ''  # Track which file is currently loaded

# ── Noise injection state ──────────────────────────────────────
PARITY_BITS = [0, 1, 2, 4, 8]
DATA_BITS = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]

noise_enabled = False
noise_rate = 1.0         # errors per H2 sweep (Poisson lambda)
noise_type = 'any'      # 'any', 'parity', 'data'
noise_step_counter = 0  # steps since last injection
noise_rng = random.Random(42)
noise_total_injected = 0

# ── GP cleanup cheat ─────────────────────────────────────────
gp_cleanup_enabled = False  # auto-zero GP rows when GP wraps
gp_cleanup_count = 0        # how many times cleanup has fired


def _poisson_sample(lam):
    """Poisson variate via Knuth's algorithm. Fine for small lambda."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= noise_rng.random()
        if p < L:
            return k - 1


noise_cycle_count = 0  # total cycles completed with noise enabled


def _inject_noise_now():
    """Inject noise: Poisson(noise_rate/cols) bit flips per IP cycle.

    noise_rate is errors per H2 sweep (= sim.cols IP cycles).
    Each call is one IP cycle, so lambda_per_cycle = noise_rate / sim.cols.
    """
    global noise_total_injected, noise_cycle_count
    noise_cycle_count += 1
    lam_per_cycle = noise_rate / sim.cols
    n_errors = _poisson_sample(lam_per_cycle)
    sweeps = noise_cycle_count / sim.cols
    if n_errors == 0:
        if noise_cycle_count % (sim.cols // 2) == 0:
            print(f'[noise] sweep {sweeps:.1f}: 0 errors '
                  f'(total: {noise_total_injected}, rate={noise_rate}/sweep)')
        return
    # Auto-detect code rows from IP positions
    sim._save_active()
    code_rows = list(set(ip['ip_row'] for ip in sim.ips))
    for _ in range(n_errors):
        row = noise_rng.choice(code_rows)
        col = noise_rng.randint(0, sim.cols - 1)
        if noise_type == 'parity':
            bit = noise_rng.choice(PARITY_BITS)
        elif noise_type == 'data':
            bit = noise_rng.choice(DATA_BITS)
        else:  # 'any'
            bit = noise_rng.randint(0, 15)
        sim.grid[sim._to_flat(row, col)] ^= (1 << bit)
    noise_total_injected += n_errors
    print(f'[noise] sweep {sweeps:.1f}: injected {n_errors} '
          f'(total: {noise_total_injected})')


def _noise_after_step():
    """Called after each step_all(). Injects noise at cycle boundaries."""
    global noise_step_counter
    if not noise_enabled:
        return
    noise_step_counter += 1
    if noise_step_counter >= sim.cols:
        noise_step_counter = 0
        _inject_noise_now()


# ── GP cleanup cheat ─────────────────────────────────────────
gp_cleanup_interval = 0      # 0 = disabled; >0 = clean every N step_count units


def _gp_cleanup_after_step():
    """Called after each step_all().  Zeros all GP rows every N steps.

    The interval is in step_count units (= n_ips * step_all_calls).
    For serpentine-ouroboros-w99 with 2 IPs:
      90 cycles × 388 steps/cycle × 2 IPs = 69840.

    The interval is device-specific and set via the /api/gp_cleanup
    endpoint or the GUI prompt.
    """
    global gp_cleanup_count
    if not gp_cleanup_enabled or gp_cleanup_interval <= 0:
        return
    # Use the simulator's own step_count for timing
    if sim.step_count == 0 or sim.step_count % gp_cleanup_interval != 0:
        return

    # Zero ALL GP rows
    sim._save_active()
    W = sim.cols
    gp_rows_cleaned = set()
    for ip in sim.ips:
        gp_row = ip['gp'] // W
        if gp_row not in gp_rows_cleaned:
            base = gp_row * W
            for c in range(W):
                sim.grid[base + c] = 0
            gp_rows_cleaned.add(gp_row)
    gp_cleanup_count += 1
    print(f'[gp-cleanup] step {sim.step_count}: zeroed rows '
          f'{sorted(gp_rows_cleaned)} (cleanup #{gp_cleanup_count})')


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
        'h2': sim.h2,
        'gp': sim.gp,
        'step_count': sim.step_count,
        'current_file': current_file,
        # Multi-IP fields
        'n_ips': sim.n_ips,
        'active_ip': sim.active_ip,
        'ips': sim.ips,
        # Noise injection state
        'noise_enabled': noise_enabled,
        'noise_rate': noise_rate,
        'noise_type': noise_type,
        'noise_total_injected': noise_total_injected,
        # GP cleanup state
        'gp_cleanup_enabled': gp_cleanup_enabled,
        'gp_cleanup_interval': gp_cleanup_interval,
        'gp_cleanup_count': gp_cleanup_count,
    }
    return result


@app.route('/')
def index():
    return send_file('fb2d_gui.html')


@app.route('/api/state')
def get_state():
    return jsonify(serialize_state())


@app.route('/api/load', methods=['POST'])
def load_file():
    global current_file, noise_step_counter, noise_total_injected, noise_cycle_count
    global gp_cleanup_count
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
    # Reset noise counters (keep enabled/rate/type settings)
    noise_step_counter = 0
    noise_total_injected = 0
    noise_cycle_count = 0
    # Reset GP cleanup counters
    _gp_step_counter = 0
    gp_cleanup_count = 0
    return jsonify(serialize_state())


@app.route('/api/reset', methods=['POST'])
def reset_state():
    """Reload the current file to reset back to step 0."""
    global noise_step_counter, noise_total_injected, noise_cycle_count
    global gp_cleanup_count
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
    # Reset noise counters (keep enabled/rate/type settings)
    noise_step_counter = 0
    noise_total_injected = 0
    noise_cycle_count = 0
    # Reset GP cleanup counters
    _gp_step_counter = 0
    gp_cleanup_count = 0
    return jsonify(serialize_state())


@app.route('/api/step', methods=['POST'])
def step_forward():
    n = min(int(request.args.get('n', 1)), 10000)
    try:
        for _ in range(n):
            sim.step_all()
            _noise_after_step()
            _gp_cleanup_after_step()
    except Exception as e:
        print(f'[step] error after {n} steps: {e}')
        # Return current state even on error so GUI can display it
    return jsonify(serialize_state())


@app.route('/api/back', methods=['POST'])
def step_backward():
    n = min(int(request.args.get('n', 1)), 10000)
    try:
        for _ in range(n):
            sim.step_back_all()
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
        for head in ('cl', 'h0', 'h1', 'h2', 'gp'):
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
    h2 = int(data.get('h2', 0))
    h2_dir = int(data.get('h2_dir', 1))  # DIR_E = 1
    cl = int(data.get('cl', 0))
    gp = int(data.get('gp', 0))
    idx = sim.add_ip(ip_row=ip_row, ip_col=ip_col, ip_dir=ip_dir,
                     h0=h0, h1=h1, h2=h2, h2_dir=h2_dir, cl=cl, gp=gp)
    result = serialize_state()
    result['added_ip'] = idx
    return jsonify(result)


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


# ── Noise injection routes ─────────────────────────────────────


@app.route('/api/noise', methods=['GET'])
def get_noise():
    return jsonify({
        'enabled': noise_enabled,
        'rate': noise_rate,
        'type': noise_type,
        'total_injected': noise_total_injected,
    })


@app.route('/api/noise', methods=['POST'])
def set_noise():
    global noise_enabled, noise_rate, noise_type
    global noise_step_counter, noise_total_injected, noise_cycle_count
    data = request.get_json(force=True)
    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if new_enabled and not noise_enabled:
            # Turning on: reset counters and re-seed RNG
            noise_step_counter = 0
            noise_total_injected = 0
            noise_cycle_count = 0
            noise_rng.seed()  # re-seed from system entropy
        noise_enabled = new_enabled
    if 'rate' in data:
        noise_rate = max(0.0, float(data['rate']))
    if 'type' in data:
        t = data['type']
        if t in ('any', 'parity', 'data'):
            noise_type = t
    print(f'[noise] config: enabled={noise_enabled} rate={noise_rate}/sweep '
          f'type={noise_type} sweep={sim.cols} cycles')
    return jsonify({
        'enabled': noise_enabled,
        'rate': noise_rate,
        'type': noise_type,
        'total_injected': noise_total_injected,
    })


@app.route('/api/gp_cleanup', methods=['POST'])
def set_gp_cleanup():
    global gp_cleanup_enabled, gp_cleanup_count, gp_cleanup_interval
    data = request.get_json(force=True)
    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if new_enabled and not gp_cleanup_enabled:
            gp_cleanup_count = 0
        gp_cleanup_enabled = new_enabled
    if 'interval' in data:
        gp_cleanup_interval = int(data['interval'])
    print(f'[gp-cleanup] config: enabled={gp_cleanup_enabled}'
          f' interval={gp_cleanup_interval} cleanups={gp_cleanup_count}')
    return jsonify({
        'enabled': gp_cleanup_enabled,
        'interval': gp_cleanup_interval,
        'count': gp_cleanup_count,
    })


if __name__ == '__main__':
    print(f'fb2d GUI server — programs dir: {PROGRAMS_DIR}')
    print(f'Open http://localhost:5001')
    app.run(debug=True, use_reloader=False, port=5001)

#!/usr/bin/env python3
"""Flask backend for fb2d GUI — wraps the existing FB2DSimulator."""

import os
import json
from flask import Flask, jsonify, request, send_file, abort
from fb2d import FB2DSimulator, OPCODE_TO_CHAR, OPCODES

PROGRAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')

app = Flask(__name__)
sim = FB2DSimulator()
current_file = ''  # Track which file is currently loaded


def serialize_state():
    """Return current simulator state as a dict."""
    return {
        'rows': sim.rows,
        'cols': sim.cols,
        'grid': sim.grid,
        'ip_row': sim.ip_row,
        'ip_col': sim.ip_col,
        'ip_dir': sim.ip_dir,
        'cl': sim.cl,
        'h0': sim.h0,
        'h1': sim.h1,
        'gp': sim.gp,
        'step_count': sim.step_count,
        'current_file': current_file,
    }


@app.route('/')
def index():
    return send_file('fb2d_gui.html')


@app.route('/api/state')
def get_state():
    return jsonify(serialize_state())


@app.route('/api/load', methods=['POST'])
def load_file():
    global current_file
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
    return jsonify(serialize_state())


@app.route('/api/reset', methods=['POST'])
def reset_state():
    """Reload the current file to reset back to step 0."""
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
    return jsonify(serialize_state())


@app.route('/api/step', methods=['POST'])
def step_forward():
    n = min(int(request.args.get('n', 1)), 10000)
    for _ in range(n):
        sim.step()
    return jsonify(serialize_state())


@app.route('/api/back', methods=['POST'])
def step_backward():
    n = min(int(request.args.get('n', 1)), 10000)
    for _ in range(n):
        sim.step_back()
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
    if 0 <= r < sim.rows and 0 <= c < sim.cols and 0 <= val <= 255:
        sim.grid[sim._to_flat(r, c)] = val
    return jsonify(serialize_state())


@app.route('/api/setcells', methods=['POST'])
def set_cells():
    data = request.get_json(force=True)
    for cell in data.get('cells', []):
        r, c, val = int(cell['r']), int(cell['c']), int(cell['value'])
        if 0 <= r < sim.rows and 0 <= c < sim.cols and 0 <= val <= 255:
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
    # Clamp head positions
    sim.ip_row = min(sim.ip_row, new_rows - 1)
    sim.ip_col = min(sim.ip_col, new_cols - 1)
    sim.cl = min(sim.cl, sim.grid_size - 1)
    sim.h0 = min(sim.h0, sim.grid_size - 1)
    sim.h1 = min(sim.h1, sim.grid_size - 1)
    sim.gp = min(sim.gp, sim.grid_size - 1)
    sim.selection = None
    sim.clipboard = None
    return jsonify(serialize_state())


if __name__ == '__main__':
    print(f'fb2d GUI server — programs dir: {PROGRAMS_DIR}')
    print(f'Open http://localhost:5000')
    app.run(debug=True, port=5000)

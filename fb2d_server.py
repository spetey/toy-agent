#!/usr/bin/env python3
"""Flask backend for fb2d GUI — wraps the existing FB2DSimulator."""

import os
import json
from flask import Flask, jsonify, request, send_file, abort
from fb2d import FB2DSimulator, OPCODE_TO_CHAR, OPCODES

PROGRAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programs')

app = Flask(__name__)
sim = FB2DSimulator()


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
    }


@app.route('/')
def index():
    return send_file('fb2d_gui.html')


@app.route('/api/state')
def get_state():
    return jsonify(serialize_state())


@app.route('/api/load', methods=['POST'])
def load_file():
    data = request.get_json(force=True)
    filename = data.get('filename', '')
    # Sanitize: only allow filenames, no path traversal
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


if __name__ == '__main__':
    print(f'fb2d GUI server — programs dir: {PROGRAMS_DIR}')
    print(f'Open http://localhost:5000')
    app.run(debug=True, port=5000)

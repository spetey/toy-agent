#!/usr/bin/env python3
"""
migrate-to-16bit.py — Convert old 8-bit .fb2d files to 16-bit Hamming encoding.

Old files store raw opcode numbers (0-56) and data values (0-255) directly.
New files use 16-bit Hamming(16,11) SECDED encoding:
  - Opcodes: encode_opcode(v) → hamming_encode(OPCODE_PAYLOADS[v])
  - Data:    hamming_encode(v) → payload = v with correct parity

Strategy for distinguishing code from data:
  - Data rows: rows containing h0 or h1 initial positions (excluding IP row)
  - GP row: row containing gp initial position
  - Code rows: everything else (including IP row)
  - Values > 56 on any row: always data (no opcode > 56)
  - Values = 0: always 0 (NOP encodes to 0 in both systems)

Also handles the old text format (size/cell/data directives) used by
enter-loop-exit.fb2d.

Usage:
    python3 programs/migrate-to-16bit.py                    # dry run all
    python3 programs/migrate-to-16bit.py --apply            # apply changes
    python3 programs/migrate-to-16bit.py --file foo.fb2d    # single file
"""

import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from fb2d import (hamming_encode, encode_opcode, OPCODES,
                  OPCODE_PAYLOADS, cell_to_payload)

PROGRAMS_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_OPCODE = max(OPCODES.values())  # 56


def is_old_format(filepath):
    """Check if a .fb2d file uses old 8-bit format (max value ≤ 255)."""
    with open(filepath, 'r') as f:
        content = f.read()

    # Text format (size/cell/data)
    if content.strip().startswith('size '):
        return True

    # Standard format — check grid values
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('grid='):
            vals = [int(x) for x in line[5:].split(',') if x.strip()]
            max_val = max(vals) if vals else 0
            return max_val <= 255

    return False


def parse_text_format(filepath):
    """Parse old text format (size/cell/data) into standard format."""
    rows = cols = 0
    ip_row = ip_col = 0
    ip_dir = 1  # E
    h0 = h1 = cl = gp = 0
    h2 = 0
    code_cells = {}   # (r,c) → opcode_char
    data_cells = {}   # (r,c) → int value

    dir_map = {'N': 0, 'E': 1, 'S': 2, 'W': 3}

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            cmd = parts[0]

            if cmd == 'size':
                rows, cols = int(parts[1]), int(parts[2])
            elif cmd == 'cell':
                r, c = int(parts[1]), int(parts[2])
                if len(parts) > 3:
                    code_cells[(r, c)] = parts[3]
            elif cmd == 'data':
                r, c = int(parts[1]), int(parts[2])
                if len(parts) > 3:
                    data_cells[(r, c)] = int(parts[3])
            elif cmd == 'ip':
                ip_row, ip_col = int(parts[1]), int(parts[2])
            elif cmd == 'dir':
                ip_dir = dir_map.get(parts[1], 1)
            elif cmd == 'h0':
                h0 = int(parts[1]) * cols + int(parts[2])
            elif cmd == 'h1':
                h1 = int(parts[1]) * cols + int(parts[2])
            elif cmd == 'cl':
                cl = int(parts[1]) * cols + int(parts[2])
            elif cmd == 'gp':
                gp = int(parts[1]) * cols + int(parts[2])

    # Build grid
    grid = [0] * (rows * cols)
    for (r, c), opchar in code_cells.items():
        if opchar in OPCODES:
            grid[r * cols + c] = encode_opcode(OPCODES[opchar])
    for (r, c), val in data_cells.items():
        grid[r * cols + c] = hamming_encode(val)

    return {
        'rows': rows, 'cols': cols,
        'ip_row': ip_row, 'ip_col': ip_col, 'ip_dir': ip_dir,
        'h0': h0, 'h1': h1, 'h2': h2, 'cl': cl, 'gp': gp,
        'grid': grid, 'step': 0,
    }


def parse_standard_format(filepath):
    """Parse standard key=value .fb2d format."""
    data = {}
    comments = []
    with open(filepath, 'r') as f:
        for line in f:
            raw = line.rstrip('\n')
            stripped = raw.strip()
            if stripped.startswith('#') or not stripped:
                comments.append(raw)
                continue
            if '=' in stripped:
                k, v = stripped.split('=', 1)
                data[k.strip()] = v.strip()

    rows = int(data['rows'])
    cols = int(data['cols'])
    grid_vals = [int(x) for x in data['grid'].split(',')]

    return {
        'rows': rows, 'cols': cols,
        'ip_row': int(data.get('ip_row', 0)),
        'ip_col': int(data.get('ip_col', 0)),
        'ip_dir': int(data.get('ip_dir', 1)),
        'h0': int(data.get('h0', 0)),
        'h1': int(data.get('h1', 0)),
        'h2': int(data.get('h2', 0)),
        'cl': int(data.get('cl', 0)),
        'gp': int(data.get('gp', 0)),
        'grid': grid_vals,
        'step': int(data.get('step', 0)),
        'comments': comments,
        'n_ips': int(data.get('n_ips', 1)),
        'raw_data': data,
    }


def migrate_grid(state):
    """Convert old 8-bit grid values to 16-bit Hamming encoding.

    Returns (new_grid, report) where report describes changes.
    """
    rows = state['rows']
    cols = state['cols']
    grid = list(state['grid'])
    h0 = state['h0']
    h1 = state['h1']
    cl = state['cl']
    gp = state['gp']
    ip_row = state['ip_row']

    # Identify data rows vs code rows
    h0_row = h0 // cols
    h1_row = h1 // cols
    gp_row = gp // cols
    cl_row = cl // cols

    # Data rows: rows with h0 or h1 (where arithmetic data lives)
    # Exclude IP row even if a head is there
    data_rows = set()
    for hr in [h0_row, h1_row]:
        if hr != ip_row:
            data_rows.add(hr)
    # GP row is scratch data
    if gp_row != ip_row:
        data_rows.add(gp_row)

    report = {'data_cells': 0, 'code_cells': 0, 'zero_cells': 0,
              'data_rows': sorted(data_rows), 'warnings': []}

    new_grid = [0] * len(grid)
    for i, v in enumerate(grid):
        if v == 0:
            new_grid[i] = 0
            report['zero_cells'] += 1
            continue

        r = i // cols

        if r in data_rows:
            # Data row: encode as data payload
            new_grid[i] = hamming_encode(v)
            report['data_cells'] += 1
        elif v > MAX_OPCODE:
            # Value > 56 can't be an opcode — must be data
            new_grid[i] = hamming_encode(v)
            report['data_cells'] += 1
            report['warnings'].append(
                f"  row {r} col {i % cols}: value {v} > {MAX_OPCODE} "
                f"on code row — encoded as data")
        else:
            # Code row, value ≤ 56: encode as opcode
            new_grid[i] = encode_opcode(v)
            report['code_cells'] += 1

    return new_grid, report


def write_fb2d(filepath, state, new_grid):
    """Write migrated state to .fb2d file in standard format."""
    lines = []

    # Preserve original comments
    for c in state.get('comments', []):
        lines.append(c)

    lines.append(f"rows={state['rows']}")
    lines.append(f"cols={state['cols']}")

    # Multi-IP support
    n_ips = state.get('n_ips', 1)
    raw = state.get('raw_data', {})

    if n_ips > 1:
        lines.append(f"n_ips={n_ips}")
        for i in range(n_ips):
            prefix = f"ip{i}_"
            for field in ['ip_row', 'ip_col', 'ip_dir', 'cl', 'h0', 'h1', 'h2', 'gp']:
                key = f"{prefix}{field}"
                if key in raw:
                    lines.append(f"{key}={raw[key]}")
    else:
        lines.append(f"ip_row={state['ip_row']}")
        lines.append(f"ip_col={state['ip_col']}")
        lines.append(f"ip_dir={state['ip_dir']}")
        lines.append(f"cl={state['cl']}")
        lines.append(f"h0={state['h0']}")
        lines.append(f"h1={state['h1']}")
        if state.get('h2', 0) != 0:
            lines.append(f"h2={state['h2']}")
        lines.append(f"gp={state['gp']}")

    lines.append(f"step={state['step']}")
    lines.append(f"grid={','.join(str(v) for v in new_grid)}")
    lines.append("")

    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))


def process_file(filepath, apply=False):
    """Process one .fb2d file. Returns True if migration was needed."""
    basename = os.path.basename(filepath)

    if not is_old_format(filepath):
        return False

    with open(filepath, 'r') as f:
        content = f.read()

    # Parse based on format
    if content.strip().startswith('size '):
        state = parse_text_format(filepath)
        new_grid = state['grid']  # Already encoded during parsing
        report = {'data_cells': 0, 'code_cells': 0, 'zero_cells': 0,
                  'data_rows': [], 'warnings': ['text format — converted directly']}
    else:
        state = parse_standard_format(filepath)
        new_grid, report = migrate_grid(state)

    action = "MIGRATED" if apply else "WOULD MIGRATE"
    print(f"  {action}: {basename}")
    print(f"    {report['code_cells']} code cells, "
          f"{report['data_cells']} data cells, "
          f"{report['zero_cells']} zeros")
    if report['data_rows']:
        print(f"    data rows: {report['data_rows']}")
    for w in report['warnings']:
        print(f"    WARNING: {w}")

    if apply:
        write_fb2d(filepath, state, new_grid)

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Migrate old 8-bit .fb2d files to 16-bit Hamming encoding')
    parser.add_argument('--apply', action='store_true',
                        help='Actually write changes (default: dry run)')
    parser.add_argument('--file', type=str, default=None,
                        help='Migrate a single file')
    args = parser.parse_args()

    if args.file:
        path = os.path.join(PROGRAMS_DIR, args.file) if '/' not in args.file else args.file
        if not os.path.isfile(path):
            print(f"File not found: {path}")
            sys.exit(1)
        process_file(path, apply=args.apply)
        return

    # Process all .fb2d files
    files = sorted(f for f in os.listdir(PROGRAMS_DIR) if f.endswith('.fb2d'))
    migrated = 0
    skipped = 0

    mode = "APPLYING" if args.apply else "DRY RUN"
    print(f"=== fb2d 8-bit → 16-bit migration ({mode}) ===\n")

    for f in files:
        path = os.path.join(PROGRAMS_DIR, f)
        if process_file(path, apply=args.apply):
            migrated += 1
        else:
            skipped += 1

    print(f"\n{migrated} files {'migrated' if args.apply else 'need migration'}, "
          f"{skipped} already 16-bit")


if __name__ == '__main__':
    main()

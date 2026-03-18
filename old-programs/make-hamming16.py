#!/usr/bin/env python3
"""Generate Hamming(16,11) SECDED correction gadget as .fb2d state files.

Usage:
    python3 programs/make-hamming16.py [payload] [--error BIT] [--wrap WIDTH]

Examples:
    python3 programs/make-hamming16.py              # payload=42, error on bit 5
    python3 programs/make-hamming16.py 100           # payload=100, error on bit 5
    python3 programs/make-hamming16.py 42 --error 3  # error on bit 3
    python3 programs/make-hamming16.py 42 --no-error # no error (gadget is no-op)
    python3 programs/make-hamming16.py 42 --wrap 40  # wrapped to 40 columns
"""

import sys
import os
import importlib.util
import argparse

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'programs'))

# Import hamming-gadget-demo (hyphenated filename needs importlib)
spec = importlib.util.spec_from_file_location(
    "hamming_gadget_demo",
    os.path.join(project_root, "programs", "hamming-gadget-demo.py"))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

from fb2d import hamming_encode, cell_to_payload
from hamming import encode, inject_error


def main():
    parser = argparse.ArgumentParser(
        description="Generate Hamming(16,11) SECDED correction gadget .fb2d files")
    parser.add_argument('payload', nargs='?', type=int, default=42,
                        help='11-bit payload value (0-2047, default: 42)')
    parser.add_argument('--error', type=int, default=5,
                        help='Bit position to flip (0-15, default: 5)')
    parser.add_argument('--no-error', action='store_true',
                        help='Generate without error injection')
    parser.add_argument('--wrap', type=int, default=None,
                        help='Wrap code into WIDTH columns (default: linear)')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Output filename (default: auto-generated)')
    args = parser.parse_args()

    if args.payload < 0 or args.payload > 2047:
        print(f"Error: payload must be 0-2047, got {args.payload}")
        sys.exit(1)

    cw = hamming_encode(args.payload)
    print(f"Payload:   {args.payload} (0x{args.payload:03x})")
    print(f"Codeword:  0x{cw:04x} (correct)")

    if args.no_error:
        input_cw = cw
        error_desc = "none"
        suffix = "clean"
    else:
        bit = args.error
        if bit < 0 or bit > 15:
            print(f"Error: bit must be 0-15, got {bit}")
            sys.exit(1)
        input_cw = inject_error(cw, bit)
        error_desc = f"bit {bit} flipped"
        suffix = f"err{bit}"
        print(f"Injected:  0x{input_cw:04x} ({error_desc})")

    sim = demo.make_hamming_gadget(input_cw, wrap_width=args.wrap)

    print(f"Grid:      {sim.rows} rows × {sim.cols} cols")
    code_ops, _, _ = demo.build_gadget(
        gp_distance=sim.rows - 1, n_rows=sim.rows)
    print(f"Gadget:    {len(code_ops)} ops")

    # Generate output filename
    if args.output:
        outfile = args.output
    else:
        wrap_tag = f"-w{args.wrap}" if args.wrap else ""
        outfile = os.path.join(
            project_root, "programs",
            f"hamming16-p{args.payload}-{suffix}{wrap_tag}.fb2d")

    sim.save_state(outfile)
    print(f"Saved:     {outfile}")
    print()
    print("To run in the simulator:")
    print(f"  python3 fb2d.py")
    basename = os.path.splitext(os.path.basename(outfile))[0]
    print(f"  load {basename}")
    print(f"  step 280     (or 'run' to run until IP returns)")
    print()

    # Show expected outcome
    if args.no_error:
        print(f"Expected:  CW unchanged at 0x{cw:04x} (no error → no correction)")
    else:
        print(f"Expected:  CW corrected from 0x{input_cw:04x} → 0x{cw:04x}")
        print(f"           (payload {args.payload} restored)")


if __name__ == '__main__':
    main()

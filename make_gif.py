#!/usr/bin/env python3
"""
Create GIF from PNG snapshots.

Usage:
    python make_gif.py                          # Uses vortex_batch/*.png
    python make_gif.py benard_batch             # Uses benard_batch/*.png
    python make_gif.py vortex_batch -o out.gif  # Custom output name
    python make_gif.py -fps 10 -scale 50        # 10fps, 50% scale
"""

import argparse
import subprocess
import sys
from pathlib import Path


def make_gif_ffmpeg(input_dir, output, fps=5, scale=None):
    """Use ffmpeg to create GIF (best quality/size ratio)."""
    pattern = str(Path(input_dir) / "snapshot_*.png")

    # Build filter chain
    filters = []
    if scale:
        filters.append(f"scale=iw*{scale}/100:-1")
    filters.append(f"fps={fps}")
    filters.append("split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer")

    filter_str = ",".join(filters[:2]) + "," + filters[2]  # palettegen needs special handling

    # Simpler approach - just scale and set fps
    if scale:
        vf = f"fps={fps},scale=iw*{scale}/100:-1:flags=lanczos"
    else:
        vf = f"fps={fps}"

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-pattern_type", "glob",
        "-i", pattern,
        "-vf", vf,
        "-loop", "0",
        output
    ]

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"Created: {output}")


def make_gif_convert(input_dir, output, fps=5, scale=None):
    """Use ImageMagick convert as fallback."""
    pattern = str(Path(input_dir) / "snapshot_*.png")
    delay = int(100 / fps)  # Convert fps to centiseconds delay

    cmd = ["convert", "-delay", str(delay), "-loop", "0"]

    if scale:
        cmd.extend(["-resize", f"{scale}%"])

    cmd.extend([pattern, output])

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"Created: {output}")


def main():
    parser = argparse.ArgumentParser(description="Create GIF from PNG snapshots")
    parser.add_argument("input_dir", nargs="?", default="vortex_batch",
                        help="Directory containing snapshot_*.png files (default: vortex_batch)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output filename (default: <input_dir>.gif)")
    parser.add_argument("-fps", "--fps", type=int, default=5,
                        help="Frames per second (default: 5)")
    parser.add_argument("-scale", "--scale", type=int, default=None,
                        help="Scale percentage, e.g. 50 for 50%% (default: no scaling)")
    parser.add_argument("--use-convert", action="store_true",
                        help="Use ImageMagick instead of ffmpeg")

    args = parser.parse_args()

    # Check input directory exists
    input_path = Path(args.input_dir)
    if not input_path.exists():
        print(f"Error: Directory '{args.input_dir}' not found")
        sys.exit(1)

    # Count PNGs
    pngs = list(input_path.glob("snapshot_*.png"))
    if not pngs:
        print(f"Error: No snapshot_*.png files found in '{args.input_dir}'")
        sys.exit(1)
    print(f"Found {len(pngs)} snapshots in {args.input_dir}/")

    # Default output name
    output = args.output or f"{args.input_dir}.gif"

    # Try ffmpeg first, fall back to convert
    if args.use_convert:
        make_gif_convert(args.input_dir, output, args.fps, args.scale)
    else:
        try:
            make_gif_ffmpeg(args.input_dir, output, args.fps, args.scale)
        except FileNotFoundError:
            print("ffmpeg not found, trying ImageMagick convert...")
            try:
                make_gif_convert(args.input_dir, output, args.fps, args.scale)
            except FileNotFoundError:
                print("Error: Neither ffmpeg nor ImageMagick found")
                print("Install one of them:")
                print("  sudo apt install ffmpeg")
                print("  sudo apt install imagemagick")
                sys.exit(1)


if __name__ == "__main__":
    main()

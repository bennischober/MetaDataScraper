"""Spike harness (issue #51): measure a keyframe-only cropdetect pass.

Throwaway measurement tool, not production code. It runs `cropdetect` over the
whole runtime decoding only keyframes, dumps a per-reading timeline, and reports
the cost and the keyframe interval distribution.

Usage:
    python tools/spike_crop_timeline.py <file> [--limit 120] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

CROP_RE = re.compile(r"x1:(-?\d+) x2:(-?\d+) y1:(-?\d+) y2:(-?\d+) w:(\d+) h:(\d+)")


def probe_stream(path: Path) -> dict[str, str]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,bit_depth,pix_fmt,color_transfer,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def keyframe_timeline(
    path: Path, limit: float | None, crop_limit: int
) -> tuple[list[dict], float, str]:
    """Runs a keyframe-only cropdetect pass and returns (readings, seconds, stderr)."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-skip_frame",
        "nokey",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        f"cropdetect=limit={crop_limit}:round=2:reset=1",
        "-an",
        "-sn",
        "-f",
        "null",
        "-",
    ]
    if limit is not None:
        command = command[:1] + ["-t", str(limit)] + command[1:]
    started = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=7200)
    elapsed = time.perf_counter() - started
    readings: list[dict] = []
    timestamp = 0.0
    for line in proc.stderr.splitlines():
        if "Parsed_cropdetect" in line:
            match = CROP_RE.search(line)
            if match:
                x1, x2, y1, y2, w, h = (int(g) for g in match.groups())
                stamp = re.search(r"t:([0-9.]+)", line)
                if stamp:
                    timestamp = float(stamp.group(1))
                readings.append(
                    {
                        "timestamp": timestamp,
                        "x": x1,
                        "y": y1,
                        "width": w,
                        "height": h,
                        "bottom_bar": y2,
                        "top_bar": y1,
                    }
                )
            continue
    return readings, elapsed, proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--limit", type=float, default=120.0, help="seconds to analyse (0 = whole file)"
    )
    parser.add_argument("--crop-limit", type=int, default=24, help="cropdetect limit")
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    info = probe_stream(args.file)
    width = int(info.get("width", 0))
    height = int(info.get("height", 0))
    duration = float(info.get("duration", 0) or 0)
    limit = None if args.limit <= 0 else args.limit

    print(f"file      {args.file.name}")
    print(f"codec     {info.get('codec_name')} {width}x{height} {info.get('pix_fmt')}")
    print(f"transfer  {info.get('color_transfer')}")
    print(f"duration  {duration:.1f}s  (analysing {limit if limit else 'whole file'}s)")

    readings, elapsed, stderr = keyframe_timeline(args.file, limit, args.crop_limit)
    analysed = min(limit, duration) if limit else duration
    print(
        f"pass      {elapsed:.1f}s for {analysed:.0f}s of runtime "
        f"=> {elapsed / max(analysed, 1) * 3600:.1f}s per hour of runtime"
    )
    print(f"readings  {len(readings)}")
    if readings:
        gaps = [
            round(b["timestamp"] - a["timestamp"], 2)
            for a, b in zip(readings, readings[1:])
            if b["timestamp"] > a["timestamp"]
        ]
        if gaps:
            gaps_sorted = sorted(gaps)
            median = gaps_sorted[len(gaps_sorted) // 2]
            print(
                f"keyframe  min {min(gaps):.2f}s  median {median:.2f}s  "
                f"max {max(gaps):.2f}s  (n={len(gaps)})"
            )
        rects: dict[tuple[int, int, int, int], int] = {}
        for r in readings:
            key = (r["x"], r["y"], r["width"], r["height"])
            rects[key] = rects.get(key, 0) + 1
        print("rects     (the distinct crops seen, most common first)")
        for key, count in sorted(rects.items(), key=lambda kv: -kv[1])[:8]:
            share = count / len(readings) * 100
            print(f"          x={key[0]} y={key[1]} {key[2]}x{key[3]}  {share:.0f}%")
    if args.csv is not None:
        args.csv.write_text(
            "timestamp,x,y,width,height\n"
            + "\n".join(
                f"{r['timestamp']},{r['x']},{r['y']},{r['width']},{r['height']}" for r in readings
            )
        )
        print(f"csv       {args.csv}")
    if "Error" in stderr:
        print("stderr contained errors; see the raw output")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

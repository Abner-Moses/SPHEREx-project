#!/usr/bin/env python3
"""Encode the existing 30-second original underscore as the presentation bed."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".audit/demo-audio/original-underscore.wav"
OUTPUT = ROOT / "frontend/public/demo/audio/presentation.mp3"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def run(args):
    subprocess.run(args, check=True)


def duration(path):
    return float(subprocess.check_output([
        FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)
    ], text=True).strip())


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Missing original music source: {SOURCE}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Source is the locally synthesized, narration-free underscore. Loop the input
    # to make the encode robust to source length changes, then cut exactly 30 s.
    run([
        FFMPEG, "-y", "-stream_loop", "-1", "-i", str(SOURCE), "-t", "30.000",
        "-map", "0:a:0", "-af", "volume=5,atrim=duration=30,asetpts=N/SR/TB",
        "-ar", "48000", "-ac", "2", "-codec:a", "libmp3lame", "-b:a", "192k",
        "-id3v2_version", "0", str(OUTPUT),
    ])
    actual = duration(OUTPUT)
    if abs(actual - 30.0) > 0.001:
        raise SystemExit(f"Expected 30.000 s output, got {actual:.3f} s")
    print(f"Wrote {OUTPUT} ({actual:.3f} s, music only)")


if __name__ == "__main__":
    main()
